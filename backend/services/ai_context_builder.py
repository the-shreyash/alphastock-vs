"""
AI Context Builder for StockAssist AI (SAI) — Sprint R7.5.

WHY this exists
---------------
Before this service, the chat assistant (`server.ai_chat`) received only the
user's long-term memory and the last few conversation turns — never any live
platform data. Because the Master System Prompt forbids fabrication ("if a
number is not provided, say so"), the model correctly, but uselessly, replied
"I don't have access to live market data." That behaviour is exactly what
`.claude/REALTIME_SYSTEM.md` and the R7.5 sprint brief forbid: the AI must
always reason from the Market Engine as its source of truth.

This module closes that gap. It assembles a *fresh, live* context snapshot from
the data the platform already produces and hands it to the Prompt Builder as one
compact markdown block. The assistant then answers from real numbers instead of
model memory.

DESIGN CONTRACT
---------------
* **Compose, never re-implement.** Every section reuses an existing service
  function (`real_market.*`, `portfolio_engine.*`, `news_service.*`,
  `ai_memory.*`, `activity_logger.*`). This module adds *no* new data source.
* **Best-effort.** Every section is isolated: a failing fetch degrades to an
  omitted section, never a raised exception. A broken news feed must never break
  the chat reply.
* **Concurrent + budgeted.** All async fetches run under one `asyncio.gather`
  with a wall-clock budget so a slow provider cannot stall the assistant.
* **Token-aware.** The rendered block is deliberately compact (short labels,
  rounded numbers, capped list lengths) so it stays cheap on the Haiku-class
  chat model.
* **Decoupled from the live-market layer.** `quotes_map_func` is injected by the
  caller (`server.real_quotes_map`), mirroring `portfolio_engine.build_holdings`
  and `portfolio_stream` — this keeps the module import-safe (no circular import
  with `server`) and trivially testable.
* **Honest availability.** `live_market_available` is True only when the market
  overview actually resolved. The Prompt Builder uses it to decide whether the
  assistant should serve the "feed temporarily unavailable" fallback.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# Overall wall-clock budget for gathering every live section. Individual calls
# hit Redis/in-memory caches (30-60s TTL) so this is comfortable; it exists to
# bound the tail latency of a cold/slow upstream provider.
CONTEXT_BUDGET_SECONDS = 4.0

# Per-user micro-cache: rapid successive messages (a user typing follow-ups)
# reuse one snapshot instead of re-fetching. Kept short so data still feels live.
_CACHE_TTL_SECONDS = 8.0
_cache: dict[str, tuple[float, "ChatContext"]] = {}

QuotesMapFunc = Callable[[list], Awaitable[dict]]


@dataclass
class ChatContext:
    """The assembled live context handed to the Prompt Builder.

    ``text`` is the rendered markdown block injected into the ``ai_chat`` prompt.
    ``live_market_available`` drives the assistant's unavailable-feed fallback.
    ``sections`` keeps the structured pieces for tests / future consumers.
    """

    text: str = ""
    live_market_available: bool = False
    sections: dict[str, Any] = field(default_factory=dict)


def reset_cache() -> None:
    """Clear the per-user micro-cache (tests / forced refresh)."""
    _cache.clear()


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _fmt(v, suffix: str = "", prefix: str = "") -> str:
    """Compact human number; explicit 'n/a' rather than a fabricated 0."""
    if v is None:
        return "n/a"
    if isinstance(v, (int, float)):
        return f"{prefix}{v:,.2f}{suffix}".rstrip("0").rstrip(".") if isinstance(v, float) else f"{prefix}{v}{suffix}"
    return f"{prefix}{v}{suffix}"


def _pct(v) -> str:
    if v is None:
        return "n/a"
    n = _num(v)
    return f"{n:+.2f}%"


async def _safe(coro, label: str, default=None):
    """Await a coroutine, swallowing + logging any failure to keep the build
    best-effort. Returns ``default`` on error so the section simply drops out."""
    try:
        return await coro
    except Exception as e:  # noqa: BLE001 — telemetry must never break chat
        logger.warning("AI context section '%s' failed: %s", label, e)
        return default


# --------------------------------------------------------------------------- #
# Section renderers (pure — operate on already-fetched data)
# --------------------------------------------------------------------------- #
def _render_market(overview: Optional[dict]) -> Optional[str]:
    if not overview:
        return None
    nifty = overview.get("nifty") or {}
    bank = overview.get("bank_nifty") or {}
    sensex = overview.get("sensex") or {}
    lines = [
        "## Live Market Snapshot (source of truth)",
        f"- Market status: {overview.get('market_status', 'n/a')}",
        f"- NIFTY 50: {_fmt(nifty.get('value'))} ({_pct(nifty.get('change_pct'))})",
        f"- Bank NIFTY: {_fmt(bank.get('value'))} ({_pct(bank.get('change_pct'))})",
        f"- SENSEX: {_fmt(sensex.get('value'))} ({_pct(sensex.get('change_pct'))})",
        f"- India VIX: {_fmt(overview.get('india_vix'))}",
        f"- Market sentiment: {_fmt(overview.get('market_sentiment'), '/100')}",
    ]
    breadth = overview.get("advance_decline") or {}
    if breadth:
        lines.append(
            f"- Breadth: {breadth.get('advancing', 'n/a')} advancing / "
            f"{breadth.get('declining', 'n/a')} declining"
        )
    return "\n".join(lines)


def _render_movers(gainers: Optional[list], losers: Optional[list]) -> Optional[str]:
    def _row(q):
        return f"{q.get('symbol')} ({_pct(q.get('change_pct'))})"

    parts = []
    if gainers:
        parts.append("- Top gainers: " + ", ".join(_row(q) for q in gainers[:5]))
    if losers:
        parts.append("- Top losers: " + ", ".join(_row(q) for q in losers[:5]))
    if not parts:
        return None
    return "## Movers\n" + "\n".join(parts)


def _render_sectors(sectors: Optional[list]) -> Optional[str]:
    if not sectors:
        return None
    top = sectors[:3]
    bottom = sectors[-2:] if len(sectors) > 3 else []
    lead = ", ".join(f"{s['sector']} ({_pct(s.get('change_pct'))})" for s in top)
    line = f"- Leading: {lead}"
    out = ["## Sector Performance", line]
    if bottom:
        lag = ", ".join(f"{s['sector']} ({_pct(s.get('change_pct'))})" for s in bottom)
        out.append(f"- Lagging: {lag}")
    return "\n".join(out)


def _render_global(markets: Optional[list]) -> Optional[str]:
    if not markets:
        return None
    avail = [m for m in markets if m.get("available")]
    if not avail:
        return None
    row = ", ".join(f"{m['name']} ({_pct(m.get('change_pct'))})" for m in avail[:5])
    return "## Global Markets\n- " + row


def _render_portfolio(holdings: Optional[list], pnl: Optional[dict],
                      risk: Optional[dict]) -> Optional[str]:
    if not holdings:
        return "## Portfolio\n- No holdings on record for this user."
    lines = ["## Portfolio & Positions", f"- Holdings: {len(holdings)}"]
    if pnl:
        lines.append(
            f"- Invested: {_fmt(pnl.get('invested'), prefix='₹')} · "
            f"Current: {_fmt(pnl.get('current_value'), prefix='₹')} · "
            f"Unrealised P&L: {_fmt(pnl.get('unrealized'), prefix='₹')} "
            f"({_pct(pnl.get('unrealized_pct'))})"
        )
        if pnl.get("best"):
            lines.append(
                f"- Best: {pnl['best']['symbol']} ({_pct(pnl['best']['pnl_pct'])}) · "
                f"Worst: {pnl['worst']['symbol']} ({_pct(pnl['worst']['pnl_pct'])})"
                if pnl.get("worst") else
                f"- Best: {pnl['best']['symbol']} ({_pct(pnl['best']['pnl_pct'])})"
            )
    if risk and risk.get("level") not in (None, "—"):
        lines.append(f"- Risk score: {risk.get('score')}/100 ({risk.get('level')})")
    # A few representative positions (cap to keep the block small).
    top = sorted(holdings, key=lambda h: abs(_num(h.get("pnl_pct"))), reverse=True)[:6]
    for h in top:
        lines.append(
            f"  • {h.get('symbol')}: qty {_fmt(h.get('quantity'))}, "
            f"value {_fmt(h.get('current_value'), prefix='₹')}, "
            f"P&L {_pct(h.get('pnl_pct'))}"
        )
    return "\n".join(lines)


def _render_open_trades(trades: Optional[list], quotes: Optional[dict]) -> Optional[str]:
    if not trades:
        return None
    quotes = quotes or {}
    lines = ["## Open Trades"]
    for t in trades[:8]:
        sym = (t.get("symbol") or "").upper()
        q = quotes.get(sym) or {}
        price = q.get("price") if isinstance(q, dict) else None
        entry = t.get("entry_price")
        segs = [
            f"{sym} {(t.get('type') or 'BUY').upper()}",
            f"entry {_fmt(entry, prefix='₹')}",
        ]
        if price is not None:
            segs.append(f"live {_fmt(price, prefix='₹')}")
        if t.get("stop_loss") is not None:
            segs.append(f"SL {_fmt(t.get('stop_loss'), prefix='₹')}")
        if t.get("status"):
            segs.append(str(t.get("status")))
        lines.append("- " + ", ".join(segs))
    return "\n".join(lines)


def _render_watchlist(items: Optional[list], quotes: Optional[dict]) -> Optional[str]:
    if not items:
        return None
    quotes = quotes or {}
    parts = []
    for it in items[:15]:
        sym = (it.get("symbol") or "").upper()
        q = quotes.get(sym) or {}
        chg = q.get("change_pct") if isinstance(q, dict) else None
        parts.append(f"{sym} ({_pct(chg)})" if chg is not None else sym)
    return "## Watchlist\n- " + ", ".join(parts)


def _render_news(articles: Optional[list], sentiment: Optional[dict]) -> Optional[str]:
    if not articles and not sentiment:
        return None
    lines = ["## Latest News"]
    if sentiment and sentiment.get("available"):
        lines.append(f"- News sentiment: {sentiment.get('label')} ({_fmt(sentiment.get('score'))})")
    for a in (articles or [])[:5]:
        title = (a.get("title") or "").strip()
        if not title:
            continue
        tag = a.get("sentiment")
        lines.append(f"- {title}" + (f" [{tag}]" if tag else ""))
    return "\n".join(lines) if len(lines) > 1 else None


def _render_broker(session: Optional[dict]) -> Optional[str]:
    if not session:
        return "## Broker\n- No broker connected (analysis/paper mode)."
    name = session.get("broker") or "broker"
    connected = session.get("connected", False)
    return f"## Broker\n- {name}: {'connected (live session)' if connected else 'disconnected'}"


def _render_activity(entries: Optional[list]) -> Optional[str]:
    if not entries:
        return None
    parts = [f"{e.get('time')} {e.get('action')}" for e in entries[:5] if e.get("action")]
    if not parts:
        return None
    return "## Recent Platform AI Activity\n- " + "\n- ".join(parts)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
async def build_chat_context(
    db,
    user: dict,
    quotes_map_func: QuotesMapFunc,
    *,
    message: Optional[str] = None,
) -> ChatContext:
    """Assemble the live context block for a chat request.

    Returns a :class:`ChatContext`. Never raises: on total failure it returns an
    empty context with ``live_market_available=False`` so the caller can still
    reply (the prompt's fallback rule then applies).
    """
    user_id = str((user or {}).get("_id") or "anonymous")

    # Micro-cache: reuse a very recent snapshot for the same user.
    hit = _cache.get(user_id)
    if hit and (time.monotonic() - hit[0]) < _CACHE_TTL_SECONDS:
        return hit[1]

    try:
        ctx = await asyncio.wait_for(
            _assemble(db, user, quotes_map_func),
            timeout=CONTEXT_BUDGET_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("AI context build timed out (>%ss) for user %s", CONTEXT_BUDGET_SECONDS, user_id)
        ctx = ChatContext(text="", live_market_available=False)
    except Exception as e:  # noqa: BLE001 — must never break the chat
        logger.warning("AI context build failed for user %s: %s", user_id, e)
        ctx = ChatContext(text="", live_market_available=False)

    _cache[user_id] = (time.monotonic(), ctx)
    return ctx


async def _assemble(db, user: dict, quotes_map_func: QuotesMapFunc) -> ChatContext:
    """Concurrently fetch every section, then render. Import inside the function
    keeps module import cheap and avoids import cycles at server startup."""
    from services import real_market, portfolio_engine, news_service, ai_memory
    from services.activity_logger import get_recent_activity

    user_id = (user or {}).get("_id")

    # ---- Fetch open trades & watchlist symbols first (needed for quotes) ---- #
    open_trades = await _safe(
        db.trades.find({"user_id": user_id, "status": {"$in": ["OPEN", "open", "ACTIVE", "active"]}}).to_list(20),
        "open_trades", default=[],
    ) or []
    watchlist = await _safe(
        db.watchlist.find({"user_id": user_id}).sort("added_at", -1).to_list(20),
        "watchlist", default=[],
    ) or []

    extra_symbols = list({
        (t.get("symbol") or "").upper() for t in open_trades if t.get("symbol")
    } | {
        (w.get("symbol") or "").upper() for w in watchlist if w.get("symbol")
    })

    # ---- Concurrent live fetches (all best-effort, one budget) ---- #
    (
        overview, gainers, losers, sectors, global_markets,
        holdings, articles, sentiment, memory, extra_quotes, broker,
    ) = await asyncio.gather(
        _safe(real_market.fetch_real_market_overview(), "overview"),
        _safe(real_market.fetch_real_gainers(5), "gainers", default=[]),
        _safe(real_market.fetch_real_losers(5), "losers", default=[]),
        _safe(real_market.fetch_real_sectors(), "sectors", default=[]),
        _safe(real_market.fetch_real_global_markets(), "global", default=[]),
        _safe(portfolio_engine.build_holdings(db, user, quotes_map_func), "holdings", default=[]),
        _safe(news_service.fetch_news(), "news", default=[]),
        _safe(news_service.get_market_sentiment(), "sentiment"),
        _safe(ai_memory.get_user_memory(db, user_id), "memory", default={}),
        _safe(quotes_map_func(extra_symbols), "extra_quotes", default={}) if extra_symbols else _noop({}),
        _safe(db.broker_accounts.find_one({"user_id": user_id, "connected": {"$ne": False}}), "broker"),
    )

    # ---- Derived analytics (pure, cheap, never raise on empty) ---- #
    pnl = portfolio_engine.compute_pnl(holdings) if holdings else None
    risk = portfolio_engine.compute_risk_score(holdings) if holdings else None

    # Recent platform AI activity (in-memory, synchronous).
    try:
        activity = get_recent_activity()
    except Exception:  # noqa: BLE001
        activity = []

    memory_ctx = ""
    try:
        memory_ctx = ai_memory.build_memory_context(user, memory or {})
    except Exception as e:  # noqa: BLE001
        logger.warning("AI context memory render failed: %s", e)

    # ---- Render every section; drop the ones with no data ---- #
    blocks = [
        _render_market(overview),
        _render_movers(gainers, losers),
        _render_sectors(sectors),
        _render_global(global_markets),
        _render_portfolio(holdings, pnl, risk),
        _render_open_trades(open_trades, extra_quotes),
        _render_watchlist(watchlist, extra_quotes),
        _render_news(articles, sentiment),
        _render_broker(broker),
        _render_activity(activity),
    ]
    body = "\n\n".join(b for b in blocks if b)

    header = (
        "LIVE PLATFORM CONTEXT — current, real data from the StockAssist Market "
        "Engine. Treat every number below as ground truth for this conversation.\n"
    )
    text_parts = [header, body]
    if memory_ctx:
        # build_memory_context() already returns its own labelled block.
        text_parts.append("## User Memory\n" + memory_ctx)
    text = "\n\n".join(p for p in text_parts if p).strip()

    return ChatContext(
        text=text,
        live_market_available=bool(overview),
        sections={
            "overview": overview,
            "holdings_count": len(holdings) if holdings else 0,
            "open_trades": len(open_trades),
            "watchlist": len(watchlist),
            "news": len(articles) if articles else 0,
            "broker_connected": bool(broker),
        },
    )


async def _noop(value):
    """Awaitable that immediately returns ``value`` — lets us keep one flat
    ``asyncio.gather`` even when there are no extra symbols to price."""
    return value
