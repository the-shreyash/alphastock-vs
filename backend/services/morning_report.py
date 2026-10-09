"""Morning Report — the automated pre-market briefing (Sprint 10).

Generated every market day before the open, and on demand from the Morning
Report page. Answers the only question that matters at 8:30 AM: *what changed
overnight, and what does it mean for me today?*

Two layers
──────────
The report is deliberately split, because its two halves have different
identities, costs, and lifetimes:

  Market layer    Identical for every user (global markets, Gift Nifty, news,
                  economic calendar, scanner, top picks, risk warnings). Tens of
                  provider calls, so it is generated once per day, persisted to
                  ``db.reports`` and served from there.

  Personal layer  Different for every user (portfolio alerts). Computed per
                  request from that user's live holdings and never written into
                  the shared document.

Keeping them separate is a correctness requirement, not an optimization: the
shared document is cached by *date alone*, so any per-user field written into it
would be served to whichever user happened to request the report second. The two
layers are merged at read time by :func:`get_morning_report`.

Data access
───────────
Every market read goes through the Market Gateway (MARKET_DATA_ARCHITECTURE.md)
— this module never touches a provider. Sections degrade independently: a dead
news feed costs the news section and nothing else. Any section that cannot be
sourced is marked ``available: false`` with a reason and is *never* filled with a
plausible-looking substitute.

Transparency
────────────
Generation streams a truthful AIRun step timeline (REALTIME_SYSTEM.md → "AI
Thinking Process"); each step wraps the real work it names.

Provenance (D6.9)
─────────────────
Every generation — successful, failed, or blocked on unreachable inputs —
persists an `services.ai_provenance` record alongside the report. It answers,
without inference: when generation began and when it *succeeded*, whether a
model wrote the briefing and which one, which market-data tier the numbers came
from and when they were observed, and what went wrong if anything did.

Two distinctions this module refuses to collapse:

  * `ai_briefing` is not necessarily AI. It is a model's narration when
    `briefing_source == "ai"` and the grounded restatement of collected numbers
    otherwise. Only the first may be labelled AI-generated.
  * `top_picks` are never AI. They are a deterministic technical scan
    (`services.real_market.fetch_real_top_picks`), generated as part of this
    report and carrying its timestamp — which is what `top_picks_source`
    records, so no surface has to guess.

Freshness is derived at read time from `provenance.completed_at` and from
nothing else. Reports written before D6.9 carry no record and are described as
`status: "unknown"`; no timestamp, provider or model is invented for them.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from services import ai_provenance
from services.ai_activity import AIRun
from services.ai_provenance import AIOutcome, GenerationStatus
from services.market_engine import market_gateway

logger = logging.getLogger(__name__)

REPORT_TYPE = "morning"

#: Version of the report's own composition logic — which sections exist, how
#: mood is weighted, how risk warnings are derived. Bumped when a reader of an
#: older stored report would misread it. Distinct from the *prompt* version,
#: which versions only the narration, and from the provenance-shape version.
ANALYSIS_VERSION = "1.0.0"

#: The prompt this report's briefing is generated from. Named once so the
#: provenance record and the actual call can never cite different prompts.
BRIEFING_PROMPT_KEY = "morning_report"

#: Where `ai_briefing` came from. The heading "AI Market Briefing" is only
#: truthful for `BRIEFING_SOURCE_AI`; the deterministic value restates collected
#: numbers and is never a model's work.
BRIEFING_SOURCE_AI = "ai"
BRIEFING_SOURCE_DETERMINISTIC = "deterministic"

#: How `top_picks` were selected. They come from a deterministic RSI / volume /
#: MACD / pattern scan and have never involved a model, so the surfaces that
#: rendered them under an "AI" heading were mislabelling, not misconfigured.
#: Stamped on the document so no consumer has to know that by reading the
#: scanner's source.
PICKS_SOURCE_DETERMINISTIC = "deterministic_technical_scan"

# Step labels for the shared market layer, in execution order. One label per
# real phase of _build_market_layer() — never a label for work that isn't done.
MARKET_STEPS = [
    "Collecting Market Data",
    "Reading Global Markets",
    "Reading News",
    "Checking Economic Calendar",
    "Scanning NSE",
    "Analyzing Sector Flows",
    "Generating Report",
    "Saving Report",
]

# Appended only when the report is generated for a signed-in user.
PERSONAL_STEP = "Reviewing Your Portfolio"

MAX_HEADLINES = 6
MAX_CALENDAR_EVENTS = 5


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pct(v: Optional[float]) -> str:
    return f"{v:+.2f}%" if v is not None else "unavailable"


async def _safe(coro, section: str, default, step=None):
    """Run a section fetch; degrade to `default` instead of failing the report.

    One unreachable feed must never cost the user their whole briefing. When a
    `step` is given, a failure marks that step `warning` so the live timeline
    reports the degradation instead of claiming the work succeeded.
    """
    try:
        return await coro
    except Exception as exc:
        logger.warning("Morning report: %s section failed: %s", section, exc)
        if step is not None:
            step.warn()
        return default


# --------------------------------------------------------------------------- #
# Market layer — shared, generated once per day
# --------------------------------------------------------------------------- #

def _summarize_global(markets: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Global markets grouped by region with a factual overnight read.

    The previous implementation asserted a fixed sentence ("US futures and Asian
    markets influencing early Indian session") every morning regardless of what
    markets actually did. This states only what the quotes show.
    """
    live = [m for m in (markets or []) if m.get("available") and m.get("change_pct") is not None]
    if not live:
        return {
            "available": False,
            "markets": markets or [],
            "summary": "Global market quotes are temporarily unavailable — overnight cues cannot be read.",
            "advancing": 0,
            "declining": 0,
        }

    advancing = [m for m in live if m["change_pct"] > 0]
    declining = [m for m in live if m["change_pct"] < 0]

    if len(advancing) > len(declining):
        tone = "Overnight global cues are broadly positive"
    elif len(declining) > len(advancing):
        tone = "Overnight global cues are broadly negative"
    else:
        tone = "Overnight global cues are mixed"

    best = max(live, key=lambda m: m["change_pct"])
    worst = min(live, key=lambda m: m["change_pct"])
    summary = (
        f"{tone} — {len(advancing)} of {len(live)} tracked indices closed higher. "
        f"{best['name']} {_pct(best['change_pct'])} led; {worst['name']} {_pct(worst['change_pct'])} lagged."
    )

    return {
        "available": True,
        "markets": markets,
        "summary": summary,
        "advancing": len(advancing),
        "declining": len(declining),
    }


def _select_headlines(articles: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Top pre-market headlines — high-importance first, then most recent."""
    if not articles:
        return {
            "available": False,
            "headlines": [],
            "note": "News feeds are temporarily unreachable — no headlines available.",
        }

    # High-importance headlines first, newest first within each band.
    ranked = sorted(
        articles,
        key=lambda a: (a.get("importance") != "high", _neg_time(a.get("published"))),
    )
    headlines = [
        {
            "title": a.get("title"),
            "source": a.get("source"),
            "link": a.get("link"),
            "sentiment": a.get("sentiment"),
            "importance": a.get("importance"),
            "published": a.get("published"),
        }
        for a in ranked[:MAX_HEADLINES]
    ]
    return {"available": True, "headlines": headlines, "note": None}


def _neg_time(published: Optional[str]) -> float:
    """Sort key placing newer articles first; undated articles last."""
    if not published:
        return 0.0
    try:
        return -datetime.fromisoformat(published).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _select_calendar(calendar: Dict[str, Any]) -> Dict[str, Any]:
    """Today's events plus the nearest high-importance ones ahead."""
    if not calendar or not calendar.get("available"):
        return {"available": False, "today": [], "upcoming": [],
                "note": "Economic calendar is temporarily unavailable."}

    def slim(e: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "title": e.get("title"),
            "date": e.get("date"),
            "category": e.get("category"),
            "importance": e.get("importance"),
            "description": e.get("description"),
            "impact": e.get("impact"),
            "status": e.get("status"),
            "days_until": e.get("days_until"),
        }

    return {
        "available": True,
        "today": [slim(e) for e in calendar.get("today_events", [])],
        "upcoming": [slim(e) for e in calendar.get("upcoming_high", [])[:MAX_CALENDAR_EVENTS]],
        "note": None,
    }


def _build_risk_warnings(
    *,
    vix: Optional[float],
    fii_net: Optional[float],
    banknifty_chg: float,
    news_sentiment: Optional[Dict[str, Any]],
    calendar: Dict[str, Any],
    gift_nifty: Dict[str, Any],
) -> List[str]:
    """Risk warnings grounded in the data actually collected.

    Each line names its evidence so the user can verify it — per the product
    rule that the AI educates rather than asserts.
    """
    warnings: List[str] = []

    if vix is not None:
        warnings.append(
            f"India VIX at {vix} — {'elevated volatility, size positions down' if vix > 15 else 'moderate volatility'}"
        )
    else:
        warnings.append("India VIX unavailable — volatility reading pending")

    if fii_net is not None:
        warnings.append(
            f"FII net flow ₹{fii_net:,.0f} Cr — "
            f"{'sustained selling pressure' if fii_net < 0 else 'supportive institutional buying'}"
        )
    else:
        warnings.append("FII/DII flow unavailable — NSE publishes after market close")

    warnings.append(
        f"{'Weak' if banknifty_chg < -0.5 else 'Mixed'} Bank Nifty ({_pct(banknifty_chg)}) — "
        "watch financials for index direction"
    )

    if news_sentiment and news_sentiment.get("available"):
        warnings.append(
            f"News sentiment {news_sentiment['label']} ({news_sentiment['score']}/100 "
            f"across {news_sentiment['articles_analyzed']} headlines)"
        )
    else:
        warnings.append("News sentiment unavailable — feeds temporarily unreachable")

    # A high-importance event today is a risk in itself (gap/whipsaw potential).
    for event in calendar.get("today", [])[:2]:
        if event.get("importance") == "high":
            warnings.append(
                f"{event['title']} today — expect volatility in {event.get('impact', 'affected sectors')}"
            )

    if gift_nifty.get("available") and gift_nifty.get("change_pct") is not None:
        gap = gift_nifty["change_pct"]
        if abs(gap) >= 0.5:
            warnings.append(
                f"Gift Nifty {_pct(gap)} — a {'gap-up' if gap > 0 else 'gap-down'} open is indicated; "
                "avoid chasing the first candle"
            )

    return warnings


def _compute_mood(nifty_chg: float, banknifty_chg: float, sentiment: Optional[float]) -> Dict[str, Any]:
    """Weighted market mood. Sentiment defaults to neutral when unavailable so a
    dead news feed can't drag the mood bearish."""
    sentiment_component = (sentiment / 100 * 0.2) if sentiment is not None else 0.1
    score = round(nifty_chg * 0.5 + banknifty_chg * 0.3 + sentiment_component, 3)
    if score > 0.5:
        mood = "Bullish"
    elif score > 0:
        mood = "Cautious"
    elif score > -0.5:
        mood = "Neutral"
    else:
        mood = "Bearish"
    return {"market_mood": mood, "mood_score": score}


def _market_is_open() -> bool:
    """Whether NSE is in its regular session, per the platform clock.

    Resolved at call time through the same `validator.is_market_hours` the
    MARKET OPEN badge and `/api/market/engine/status` read, for D5.18's D-1
    reason: whether the exchange is open is a fact about the exchange and the
    clock, never a vendor's `marketState` field. An unanswerable clock reports
    closed — the report then describes its numbers conservatively rather than
    claiming a live session it cannot vouch for.
    """
    try:
        from services.market_engine import validator
        return bool(validator.is_market_hours())
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Morning report: market clock unavailable (%s)", exc)
        return False


def build_session_context() -> Dict[str, Any]:
    """What the market was doing when this report's numbers were observed.

    D5.19 — WHY A REPORT HAS TO CARRY ITS OWN TIMESTAMP AND SESSION STATE.

    The market layer is generated once per day and reused, which is correct: a
    morning report is a morning snapshot, and regenerating it on every read
    would make it a different product. What was missing is the moment it
    describes. Today's document was written at 10:26 IST — 71 minutes after
    NSE opened — and read at 12:35 with nothing on it to say so, so a
    two-hour-old Nifty level was indistinguishable from a live one.

    Both fields are labels on data the report already had. Nothing here
    fabricates, extrapolates or refreshes a number.
    """
    now = datetime.now(timezone.utc)
    return {
        "market_open": _market_is_open(),
        "observed_at": now.isoformat(),
        # Rendered in the exchange's own timezone, because "10:26 IST" is the
        # only form in which a reader can place it against the trading session.
        "observed_at_str": (now + _IST_OFFSET).strftime("%H:%M IST"),
    }


#: IST is UTC+5:30 and has no DST, so a fixed offset is exact rather than an
#: approximation — no timezone database is needed to render an NSE wall clock.
_IST_OFFSET = timedelta(hours=5, minutes=30)


def _session_instruction(market_is_open: bool, observed_at_str: str) -> str:
    """The briefing instruction, in the tense the session actually justifies.

    Split by session because the wrong half of this is precisely the defect:
    "write a pre-market briefing" is a correct instruction before 09:15 and a
    licence to invent a close at 10:26.
    """
    when = f" observed at {observed_at_str}" if observed_at_str else ""
    if market_is_open:
        return (
            f"NSE is OPEN right now. The index levels above are LIVE intraday "
            f"levels{when}, not closing prices. Write a 3-4 sentence intraday "
            "briefing for Indian traders in the present tense. Do NOT describe "
            "any level above as a close, and do NOT attribute today's sector "
            "moves to a previous session."
        )
    return (
        f"NSE is CLOSED. The levels above are the last available levels{when}. "
        "Write a 3-4 sentence pre-market briefing for Indian traders."
    )


class _BriefingResult:
    """The briefing text plus the provenance of whatever produced it.

    D6.9 — WHY A STRING WAS NOT ENOUGH. This function has always had two very
    different success paths: a model writes the narration, or the grounded
    fallback restates the collected numbers. Both are honest; only one is
    AI-generated. Returning a bare `str` erased the difference at the only
    point in the process where it was still known, and every consumer
    downstream — the persisted document, the API, the "AI Market Briefing"
    heading — then had no choice but to guess, and guessed "AI".
    """

    __slots__ = ("text", "source", "outcome", "provider", "model", "prompt_version")

    def __init__(self, *, text: str, source: str, outcome: AIOutcome,
                 provider: Optional[str] = None, model: Optional[str] = None,
                 prompt_version: Optional[str] = None) -> None:
        self.text = text
        self.source = source
        self.outcome = outcome
        self.provider = provider
        self.model = model
        self.prompt_version = prompt_version


def _deterministic(text: str, outcome: AIOutcome,
                   prompt_version: Optional[str] = None) -> _BriefingResult:
    """The grounded fallback, carrying why no model wrote it.

    `provider` and `model` stay None by construction. Naming the provider that
    was *attempted* would read to every consumer as the provider that answered
    — which is the fabrication this module exists to prevent.
    """
    return _BriefingResult(
        text=text,
        source=BRIEFING_SOURCE_DETERMINISTIC,
        outcome=outcome,
        prompt_version=prompt_version,
    )


async def _generate_briefing(facts: Dict[str, Any]) -> _BriefingResult:
    """AI briefing from the centralized prompt library, with a grounded fallback.

    The fallback is not a degraded experience — it restates real collected
    numbers. The AI adds narrative, never data. Which of the two produced this
    particular briefing is carried in the returned :class:`_BriefingResult` and
    is never inferred downstream.

    D5.19 — THE SESSION IS PART OF THE FACTS.
    This used to ask for a "pre-market briefing" and hand the model a list of
    bare numbers with no timestamp and no session state. Asked for a pre-market
    note about untimed numbers, a model writes the only tense available to it,
    and on 2026-09-01 the platform published:

        "Indian markets closed with Nifty at 24,058 ... Yesterday's
         top-performing sectors included Telecom (+2.69%)"

    at 10:26 IST, 71 minutes into an open session, about a level that had not
    closed and a sector move that was happening as it was written. A market
    event that did not occur is a fabrication whether a model or a template
    produced it, so the session state and the observation time are now facts
    the briefing is given, exactly like the Nifty level is.
    """
    market_is_open = bool(facts.get("market_is_open"))
    observed_at_str = facts.get("observed_at_str") or ""
    when = f" as of {observed_at_str}" if observed_at_str else ""

    if market_is_open:
        # "trading at", never "closed at": the number is a live level.
        fallback = (
            f"Good morning. NSE is open — these are live levels{when}. "
            f"Nifty trading at {facts['nifty_str']} ({_pct(facts['nifty_chg'])}), "
            f"Bank Nifty {_pct(facts['banknifty_chg'])}. Market mood: {facts['market_mood']}. "
            f"{facts['picks_count']} quality setups identified. "
            "Stay disciplined and respect your stops."
        )
    else:
        fallback = (
            f"Good morning. Nifty at {facts['nifty_str']} ({_pct(facts['nifty_chg'])}), "
            f"Bank Nifty {_pct(facts['banknifty_chg'])}. Market mood: {facts['market_mood']}. "
            f"{facts['picks_count']} quality setups identified. "
            "Stay disciplined and respect your stops."
        )

    try:
        from server import claude_configured, gemini_configured, get_debate_engine
        from services.prompt_library import PROMPTS, get_prefer, get_prompt
    except Exception as exc:
        logger.debug("Morning report: AI briefing unavailable (%s)", exc)
        return _deterministic(fallback, AIOutcome.NOT_CONFIGURED)

    prompt_version = PROMPTS[BRIEFING_PROMPT_KEY].version

    if not (claude_configured() or gemini_configured()):
        return _deterministic(fallback, AIOutcome.NOT_CONFIGURED, prompt_version)

    try:
        system_prompt = get_prompt(BRIEFING_PROMPT_KEY)
        context = (
            f"Nifty: {facts['nifty_str']} ({_pct(facts['nifty_chg'])})\n"
            f"Bank Nifty: {_pct(facts['banknifty_chg'])}\n"
            f"Sensex: {facts['sensex_str']}\n"
            f"Market mood: {facts['market_mood']}\n"
            f"Gift Nifty: {facts['gift_nifty_str']}\n"
            f"Global markets: {facts['global_summary']}\n"
            f"FII net: {facts['fii_str']}\n"
            f"News sentiment: {facts['news_str']}\n"
            f"Top headlines: {facts['headlines_str']}\n"
            f"Economic events today: {facts['events_str']}\n"
            f"Leading sectors: {facts['sectors_str']}\n"
            f"Top picks: {facts['picks_str']}\n\n"
            + _session_instruction(market_is_open, observed_at_str)
            + " Use only the numbers above. Anything marked unavailable must be "
            "omitted, never guessed."
        )
        engine = get_debate_engine()
        # `simple_chat_result`, not `simple_chat`: the string form returns the
        # SimulatedProvider's outage text on total provider failure, and a
        # caller holding only a string cannot tell that from a model's answer.
        # That is how "AI services are currently offline or unavailable. Please
        # check that ANTHROPIC_API_KEY …" came to be persisted as the day's
        # `ai_briefing` and published under the heading "AI Market Briefing".
        resp = await engine.simple_chat_result(
            system_prompt, context, prefer=get_prefer(BRIEFING_PROMPT_KEY), max_tokens=260
        )
    except Exception as exc:
        logger.warning("Morning report: AI briefing failed, using grounded fallback: %s", exc)
        return _deterministic(fallback, AIOutcome.PROVIDER_ERROR, prompt_version)

    text = (resp.content or "").strip()
    if not resp.success or not text:
        # A real provider was configured and did not deliver. The user still
        # gets a briefing — the grounded one, built from numbers this module
        # actually collected — but nothing downstream may call it AI-generated.
        logger.warning(
            "Morning report: no model produced a briefing (provider=%s); using grounded fallback",
            resp.provider,
        )
        return _deterministic(fallback, AIOutcome.PROVIDER_ERROR, prompt_version)

    return _BriefingResult(
        text=text,
        source=BRIEFING_SOURCE_AI,
        outcome=AIOutcome.SUCCEEDED,
        provider=resp.provider,
        model=resp.model,
        prompt_version=prompt_version,
    )


async def _build_market_layer(db, run: AIRun) -> Dict[str, Any]:
    """Generate the shared market report. Returns the persisted document shape.

    D6.9 — every exit from this function persists a provenance record, including
    the failure exits. "Today's report did not generate" and "nobody has asked
    for today's report yet" used to be the same observation (an absent document)
    and they are different facts: only the first one is a problem, and only the
    first one should stop the UI implying a report exists.
    """
    date = _today()
    prov = ai_provenance.begin(
        ai_provenance.ARTIFACT_MORNING_REPORT,
        f"{REPORT_TYPE}:{date}",
        analysis_version=ANALYSIS_VERSION,
    )

    # Step 1 — Collecting Market Data
    async with run.step() as step:
        overview = await _safe(market_gateway.get_indices(), "indices", {}, step)
        if not overview:
            step.warn()

    if not overview:
        note = ("Live market data is temporarily unavailable — the morning report "
                "cannot be generated right now.")
        # UNAVAILABLE, not FAILED: nothing broke, the inputs were unreachable,
        # and the two call for different operator responses.
        ai_provenance.failed(
            prov, code="market_data_unavailable", message=note,
            status=GenerationStatus.UNAVAILABLE,
        )
        unavailable = {
            "date": date,
            "type": REPORT_TYPE,
            "available": False,
            "note": note,
            "generated_at": prov["generated_at"],
            "provenance": prov,
        }
        await _persist(db, unavailable)
        return unavailable

    # Step 2 — Reading Global Markets (+ Gift Nifty: both are the overnight read)
    async with run.step() as step:
        global_markets, gift_nifty = await asyncio.gather(
            _safe(market_gateway.get_global_markets(), "global markets", [], step),
            _safe(market_gateway.get_gift_nifty(), "gift nifty", {"available": False}, step),
        )
        global_section = _summarize_global(global_markets)
        if not global_section["available"]:
            step.warn()

    # Step 3 — Reading News
    async with run.step() as step:
        from services.news_service import get_market_sentiment

        articles, news_sentiment = await asyncio.gather(
            _safe(market_gateway.get_news(), "news", [], step),
            _safe(get_market_sentiment(), "news sentiment", None, step),
        )
        news_section = _select_headlines(articles)
        if not news_section["available"]:
            step.warn()

    # Step 4 — Checking Economic Calendar
    async with run.step() as step:
        calendar_raw = await _safe(
            market_gateway.get_calendar(days_ahead=14, days_behind=0), "calendar", {}, step
        )
        calendar_section = _select_calendar(calendar_raw)

    # Step 5 — Scanning NSE
    async with run.step() as step:
        from services.real_market import fetch_real_top_picks

        picks_res = await _safe(fetch_real_top_picks(3), "scanner", {}, step)
        picks = (picks_res or {}).get("picks", [])
        if not picks:
            step.warn()

    # Step 6 — Analyzing Sector Flows
    async with run.step() as step:
        from services.real_market import fetch_real_fii_dii

        fii_dii, sectors = await asyncio.gather(
            _safe(fetch_real_fii_dii(), "fii/dii", {}, step),
            _safe(market_gateway.get_sectors(), "sectors", [], step),
        )

        nifty = overview.get("nifty") or {}
        bank_nifty = overview.get("bank_nifty") or {}
        sensex = overview.get("sensex") or {}
        nifty_chg = nifty.get("change_pct") or 0
        banknifty_chg = bank_nifty.get("change_pct") or 0
        vix = overview.get("india_vix")
        fii_net = (fii_dii.get("fii") or {}).get("net")

        mood = _compute_mood(nifty_chg, banknifty_chg, overview.get("market_sentiment"))
        risk_warnings = _build_risk_warnings(
            vix=vix,
            fii_net=fii_net,
            banknifty_chg=banknifty_chg,
            news_sentiment=news_sentiment,
            calendar=calendar_section,
            gift_nifty=gift_nifty,
        )

    # Step 7 — Generating Report
    async with run.step():
        nifty_val, bnk_val, sensex_val = nifty.get("value"), bank_nifty.get("value"), sensex.get("value")
        gift_str = (
            f"{gift_nifty['value']:,.0f} ({_pct(gift_nifty.get('change_pct'))})"
            if gift_nifty.get("available") else "unavailable"
        )
        session = build_session_context()
        briefing_result = await _generate_briefing({
            # D5.19 — the session is a fact the briefing is given, so the
            # narration matches what the market was actually doing. See
            # `_session_instruction`.
            "market_is_open": session["market_open"],
            "observed_at_str": session["observed_at_str"],
            "nifty_str": f"{nifty_val:,.0f}" if nifty_val is not None else "unavailable",
            "sensex_str": f"{sensex_val:,.0f}" if sensex_val is not None else "unavailable",
            "nifty_chg": nifty_chg,
            "banknifty_chg": banknifty_chg,
            "market_mood": mood["market_mood"],
            "gift_nifty_str": gift_str,
            "global_summary": global_section["summary"],
            "fii_str": f"₹{fii_net:,.0f} Cr" if fii_net is not None else "unavailable",
            "news_str": (news_sentiment or {}).get("label") or "unavailable",
            "headlines_str": "; ".join(h["title"] for h in news_section["headlines"][:3]) or "unavailable",
            "events_str": ", ".join(e["title"] for e in calendar_section["today"]) or "none scheduled",
            "sectors_str": ", ".join(
                f"{s.get('name') or s.get('sector')} ({_pct(s.get('change_pct'))})" for s in sectors[:3]
            ) or "unavailable",
            "picks_str": ", ".join(p["name"] for p in picks[:3]) or "unavailable",
            "picks_count": len(picks),
        })

        # The AI layer's provenance, recorded from what actually happened.
        # `ai_succeeded` is the only writer of a provider/model name anywhere in
        # this module, so an attribution cannot appear without a model call.
        if briefing_result.outcome == AIOutcome.SUCCEEDED:
            ai_provenance.ai_succeeded(
                prov,
                provider=briefing_result.provider,
                model=briefing_result.model,
                prompt_key=BRIEFING_PROMPT_KEY,
                prompt_version=briefing_result.prompt_version,
            )
        else:
            ai_provenance.ai_did_not_run(
                prov,
                outcome=briefing_result.outcome,
                prompt_key=BRIEFING_PROMPT_KEY,
                prompt_version=briefing_result.prompt_version,
            )

        # Which market snapshot these numbers came from. `source_tier` and never
        # a provider name — MARKET_DATA_ARCHITECTURE.md Developer Rule 4 applies
        # to an artifact exactly as it applies to a quote. Resolved without a
        # user_id because the market layer is shared platform-wide: a per-user
        # resolution cached into a shared document would serve one account's
        # broker tier to everybody.
        ai_provenance.market_data(
            prov,
            source_tier=_source_tier(),
            observed_at=session["observed_at"],
        )
        ai_provenance.completed(prov)

        report = {
            "date": date,
            "type": REPORT_TYPE,
            "available": True,
            # D5.19 — when these numbers were observed and what the market was
            # doing then. The market layer is cached for the day by design, so
            # this is what lets a reader at 12:35 tell a 10:26 level from a
            # live one instead of assuming the report is current.
            "session": session,
            **mood,
            "nifty": {"value": nifty_val, "change_pct": nifty_chg},
            "banknifty": {"value": bnk_val, "change_pct": banknifty_chg},
            "sensex": {"value": sensex_val, "change_pct": sensex.get("change_pct")},
            "gift_nifty": gift_nifty,
            "global_markets": global_section,
            # Retained for the Dashboard summary card and existing API consumers.
            # Same text as global_markets.summary — now derived from real quotes
            # rather than the fixed sentence this field used to carry.
            "global_cues": global_section["summary"],
            "news": news_section,
            "news_sentiment": news_sentiment,
            "economic_calendar": calendar_section,
            "sectors": sectors[:6],
            "top_picks": picks,
            # Deterministic, and generated as part of this report — so a pick's
            # age is this report's `completed_at`, not "now".
            "top_picks_source": PICKS_SOURCE_DETERMINISTIC,
            "key_risks": risk_warnings,
            "ai_briefing": briefing_result.text,
            # "ai" or "deterministic". The heading "AI Market Briefing" is only
            # truthful for the first; consumers read this rather than assuming.
            "briefing_source": briefing_result.source,
            "fii_dii": {"fii_net": fii_net, "dii_net": (fii_dii.get("dii") or {}).get("net")},
            # Preserved for existing consumers. `provenance.generated_at` is the
            # instant generation *began* and `provenance.completed_at` the
            # instant it *succeeded* — the latter is the only clock freshness is
            # ever measured from.
            "generated_at": prov["generated_at"],
            "provenance": prov,
        }

    # Step 8 — Saving Report
    async with run.step():
        await _persist(db, report)

    return report


def _source_tier() -> Optional[str]:
    """Freshness tier currently serving the report's index quotes, or None.

    Read through the gateway so the label tracks whichever provider is actually
    serving, rather than a literal that is wrong the day a broker feed takes
    over (MARKET_DATA_ARCHITECTURE.md, DD-1). An unanswerable gateway yields
    None — "not known", which `describe()` renders as absent provenance rather
    than as a claim.
    """
    try:
        from services.market_engine.gateway import market_gateway as gw
        from services.market_engine.providers.base import Capability

        return gw.source_tier(Capability.QUOTES)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Morning report: source tier unavailable (%s)", exc)
        return None


async def _persist(db, report: Dict[str, Any]) -> None:
    """Write the shared market document for its date.

    One writer for every outcome — success, unavailable and failure — so a
    failure can never be persisted through a path that forgot to carry
    provenance.

    REPLACE, NOT `$set`. This was `update_one({"$set": {...}})`, which MERGES.
    When a *successful* report for today was already stored and a forced
    regeneration then failed, the merge left every section of the old run —
    and its `available: true` — standing beside the new `status: "failed"`
    provenance. The document read as a complete report whose provenance said it
    had not been produced, and `_is_servable` would happily serve it. A
    regeneration replaces the day's document outright, so the stored artifact is
    always exactly one run's output.
    """
    await db.reports.replace_one(
        {"date": report["date"], "type": REPORT_TYPE},
        {**report},
        upsert=True,
    )


# --------------------------------------------------------------------------- #
# Personal layer — per user, never cached in the shared document
# --------------------------------------------------------------------------- #

async def _quotes_map(symbols: List[str]) -> Dict[str, Any]:
    """Batch live quotes through the gateway for portfolio valuation."""
    uniq = list({(s or "").upper() for s in symbols if s})
    if not uniq:
        return {}
    results = await asyncio.gather(
        *[market_gateway.get_quote(s) for s in uniq], return_exceptions=True
    )
    return {sym: (None if isinstance(r, Exception) else r) for sym, r in zip(uniq, results)}


def _portfolio_alerts(intelligence: Dict[str, Any], market: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Cross-reference the user's holdings against this morning's market state.

    This is the report's payoff: generic market news becomes "this affects *your*
    Reliance position". Every alert carries its reasoning, per the product rule
    that the AI explains rather than asserts.
    """
    alerts: List[Dict[str, Any]] = []
    holdings = intelligence.get("holdings") or []
    if not holdings:
        return alerts

    held = {(h.get("symbol") or "").upper(): h for h in holdings}

    # 1. Risk factors the portfolio engine already surfaced (single source of truth).
    risk = intelligence.get("risk") or {}
    for factor in (risk.get("factors") or [])[:2]:
        alerts.append({
            "severity": "warning" if factor["points"] >= 15 else "info",
            "symbol": None,
            "title": factor["name"],
            "message": factor["detail"],
            "why": f"Contributes {factor['points']} points to your {risk.get('level', '—')} risk score of {risk.get('score')}/100.",
        })

    # 2. Holdings sitting in this morning's weakest sectors.
    weak_sectors = {
        (s.get("name") or s.get("sector")): s.get("change_pct")
        for s in (market.get("sectors") or [])
        if (s.get("change_pct") or 0) < -0.5
    }
    for sector, change in list(weak_sectors.items())[:2]:
        exposed = [h["symbol"] for h in holdings if (h.get("sector") or "") == sector]
        if exposed:
            alerts.append({
                "severity": "warning",
                "symbol": exposed[0] if len(exposed) == 1 else None,
                "title": f"{sector} weakness affects your holdings",
                "message": f"{sector} is down {change}% — you hold {', '.join(exposed[:3])}.",
                "why": "Stocks in a weak sector tend to move with it, so these positions carry correlated downside today.",
            })

    # 3. Today's headlines naming a stock the user actually owns.
    for article in (market.get("news") or {}).get("headlines", []):
        title_upper = (article.get("title") or "").upper()
        for symbol, holding in held.items():
            name = (holding.get("name") or "").upper()
            if symbol in title_upper or (len(name) > 4 and name in title_upper):
                alerts.append({
                    "severity": "critical" if article.get("importance") == "high" else "info",
                    "symbol": symbol,
                    "title": f"News on your {symbol} position",
                    "message": article.get("title"),
                    "why": f"You hold {symbol}. Sentiment on this headline reads {article.get('sentiment', 'neutral')}.",
                })
                break

    # 4. Positions already flagged by the monitoring engine as actionable.
    for suggestion in (intelligence.get("suggestions") or []):
        if suggestion.get("tone") == "critical":
            alerts.append({
                "severity": "critical",
                "symbol": None,
                "title": "Action needed on a position",
                "message": suggestion.get("text"),
                "why": "Flagged critical by continuous portfolio monitoring — review before the open.",
            })

    order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: order.get(a["severity"], 3))
    return alerts[:6]


async def _build_personal_layer(db, user: Dict[str, Any], market: Dict[str, Any]) -> Dict[str, Any]:
    """Portfolio alerts for one user. Failure degrades this section only."""
    try:
        from services import portfolio_engine

        intelligence = await portfolio_engine.build_intelligence(db, user, _quotes_map)
    except Exception as exc:
        logger.warning("Morning report: portfolio layer failed for user: %s", exc)
        return {
            "available": False,
            "alerts": [],
            "note": "Your portfolio could not be reviewed right now.",
        }

    if not intelligence.get("holdings_count"):
        return {
            "available": True,
            "alerts": [],
            "holdings_count": 0,
            "note": "No holdings yet — connect a broker or log a trade to get personalized morning alerts.",
        }

    return {
        "available": True,
        "alerts": _portfolio_alerts(intelligence, market),
        "holdings_count": intelligence["holdings_count"],
        "risk": intelligence.get("risk"),
        "pnl": intelligence.get("pnl"),
        "note": None,
    }


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

async def get_morning_report(
    db,
    user: Optional[Dict[str, Any]] = None,
    run_id: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Today's morning report — shared market layer + this user's personal layer.

    The market layer is generated once per day and reused; the personal layer is
    always fresh. `force` regenerates the market layer (used by the scheduler,
    which must not serve yesterday's cached document).
    """
    today = _today()
    stored = None if force else await db.reports.find_one({"date": today, "type": REPORT_TYPE})
    cached = stored if _is_servable(stored) else None

    steps: List[str] = []
    if not cached:
        steps.extend(MARKET_STEPS)
    if user:
        steps.append(PERSONAL_STEP)

    run = AIRun(user["_id"] if user else None, None, steps, run_id=run_id)
    await run.start()

    try:
        if cached:
            cached.pop("_id", None)
            market = cached
        else:
            market = await _build_market_layer(db, run)

        if not market.get("available"):
            await run.complete("warning")
            return _with_provenance(market)

        if user:
            async with run.step():
                market = {**market, "portfolio": await _build_personal_layer(db, user, market)}

        await run.complete()
        return _with_provenance(market)
    except Exception as exc:
        await run.complete("warning")
        # A crash mid-generation must not leave today looking like a day nobody
        # asked about. Persist the failure so "AI online but today's report did
        # not generate" is answerable, then re-raise — the caller's error
        # handling is unchanged.
        await _persist_generation_failure(db, today, exc)
        raise


def _is_servable(stored: Optional[Dict[str, Any]]) -> bool:
    """Whether a stored document may be served instead of regenerating.

    Only a report that carries sections. A persisted *failure* or *unavailable*
    record is evidence about the last attempt, not a report — serving one would
    pin a transient 08:30 market-data outage for the rest of the day, and the
    on-demand path would never retry. Retrying is also exactly what happened
    before D6.9, when a failed generation was not persisted at all, so this
    keeps request behaviour identical while adding the record.

    A legacy document with no `provenance` key IS servable: it is a real report
    whose provenance simply was never recorded, and regenerating it on that
    basis would be a cost and behaviour change justified by nothing.
    """
    return bool(stored and stored.get("available"))


def _with_provenance(report: Dict[str, Any]) -> Dict[str, Any]:
    """Attach the derived, client-facing provenance view.

    Derived at read time, never stored: freshness is a function of *now*, so a
    persisted freshness value would be wrong the moment after it was written.

    A report with no stored record — every document written before D6.9 — is
    described as `status: "unknown"`, `known: false`. No timestamp, provider or
    model is inferred for it. In particular `generated_at` is NOT promoted into
    provenance: nothing proves that field marked a successful AI completion
    rather than the moment a document happened to be written, and a provenance
    module that guesses is worse than one that says it does not know.
    """
    return {**report, "provenance": ai_provenance.describe(report.get("provenance"))}


async def _persist_generation_failure(db, date: str, exc: BaseException) -> None:
    """Record that today's generation was attempted and raised.

    The stored message is written for a user. The exception's own text is
    logged, never persisted and never served: provider and driver errors carry
    request ids, connection strings and echoed prompts, and this record leaves
    the process through the API.
    """
    logger.error("Morning report generation failed for %s: %s", date, exc)
    prov = ai_provenance.begin(
        ai_provenance.ARTIFACT_MORNING_REPORT,
        f"{REPORT_TYPE}:{date}",
        analysis_version=ANALYSIS_VERSION,
    )
    ai_provenance.failed(
        prov,
        code="generation_error",
        message="Report generation failed. No analysis was produced for this date.",
    )
    try:
        await _persist(db, {
            "date": date,
            "type": REPORT_TYPE,
            "available": False,
            "note": prov["error"]["message"],
            "generated_at": prov["generated_at"],
            "provenance": prov,
        })
    except Exception as persist_exc:  # pragma: no cover - defensive
        logger.error("Morning report: could not persist failure record: %s", persist_exc)


async def generate_and_notify(db) -> Dict[str, Any]:
    """Scheduled 8:30 AM entry point: regenerate the report and notify users.

    Returns the generated market report. A generation that fails still returns
    a document and still publishes — an unannounced failure is the state in
    which an open dashboard keeps showing yesterday's briefing with nothing
    saying the morning's run did not happen.
    """
    try:
        report = await get_morning_report(db, user=None, force=True)
    except Exception as exc:
        # `get_morning_report` has already persisted the failure record; read it
        # back rather than re-deriving, so the notified state and the stored
        # state are the same object.
        logger.error("Morning report: scheduled generation failed: %s", exc)
        stored = await db.reports.find_one({"date": _today(), "type": REPORT_TYPE}) or {}
        stored.pop("_id", None)
        report = _with_provenance(stored) if stored else _with_provenance({
            "date": _today(), "type": REPORT_TYPE, "available": False,
            "note": "Report generation failed. No analysis was produced for this date.",
        })

    prov = report.get("provenance") or {}
    try:
        from services.market_engine.event_bus import event_bus

        await event_bus.publish("morningreport.generated", {
            "date": report.get("date"),
            "picks": len(report.get("top_picks") or []),
            "available": report.get("available", False),
            # D6.9 — the ready-signal carries the generation's outcome, so a
            # listening dashboard refetches into the right state instead of
            # assuming the arrival of the event means a report was produced.
            # Metadata only: the report body still comes from the API, which is
            # where per-user layering and authorization live.
            "status": prov.get("status"),
            "completed_at": prov.get("completed_at"),
            "is_ai_generated": bool(prov.get("is_ai_generated")),
        })
    except Exception as exc:
        logger.warning("morningreport.generated publish failed: %s", exc)

    await notify_users(db, report)
    return report


async def notify_users(db, report: Dict[str, Any]) -> int:
    """Notify every user who opted into the morning report.

    Honors the `morning_report` notification preference that models.py defines —
    the previous implementation checked `trade_alerts` and only reached users who
    had already traded, so a new user never received the report they subscribed
    to. Returns the number of users notified.
    """
    from services.notification_service import create_notification

    if report.get("available"):
        picks = report.get("top_picks") or []
        if picks:
            message = (
                f"{report.get('market_mood', 'Market')} open indicated. "
                f"Top pick: {picks[0]['name']} ({picks[0]['confidence']}% confidence). "
                f"{len(picks)} setups ready."
            )
        else:
            message = (
                f"{report.get('market_mood', 'Market')} open indicated. "
                "Your pre-market briefing is ready."
            )
    else:
        message = "Live market data was unavailable this morning — the briefing could not be generated."

    notified = 0
    cursor = db.users.find({}, {"_id": 1, "email": 1, "notification_prefs": 1})
    async for user in cursor:
        prefs = user.get("notification_prefs") or {}
        if not prefs.get("morning_report", True):
            continue

        created = await create_notification(
            db, str(user["_id"]),
            type="MORNING_REPORT",
            title="Morning Briefing Ready",
            message=message,
            dedupe_minutes=180,  # one briefing per morning, even if the job retries
        )
        if not created:
            continue
        notified += 1

        if prefs.get("email_alerts", False) and user.get("email"):
            try:
                from services.email_service import build_morning_report_email, send_email

                subject, html = build_morning_report_email(report)
                await send_email(user["email"], subject, html)
            except Exception as exc:
                logger.error("Morning report email failed for %s: %s", user.get("email"), exc)

    logger.info("Morning report: notified %d user(s)", notified)
    return notified
