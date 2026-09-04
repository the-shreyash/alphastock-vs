"""AI Heartbeat Engine — the platform's "always-on" brain.

This engine makes the AI genuinely *alive*: a background loop cycles through a
registry of REAL tasks (live index/US-market fetches, news scans, breakout &
volume scans, open-trade monitoring, portfolio health, top-pick generation,
sentiment, sector rotation, macro/earnings news scans). Each task logs
`log_activity(action, category, "running")` when it starts and a truthful
result summary with status `"done"` (or `"warning"` on failure) when it
finishes — so the AI Activity feed is an honest trace of work actually done,
never canned strings.

Every task here is **market-wide**, owned by nobody, and therefore imports
`activity_logger.log_platform_activity` under the local alias `log_activity`
(D6.1 / S4). The unaliased `log_activity` now requires a `user_id` and is for
per-account work only. When a task in this module starts producing per-account
content, it must switch to the private logger rather than reach for the alias —
the surface that leaked one user's orders to every socket was exactly a
per-account entry sitting in the platform stream.

A second loop streams live prices over the WebSocket, and the trade/portfolio
tasks push `trade_update`, `portfolio_update` and `alert` messages that match
the contracts the frontend (`hooks/useWebSocket.js`) already understands.

Guarded off during pytest via `DISABLE_BACKGROUND_ENGINE=1`. Nothing here runs
on import — `start_engine(db, ws_manager)` must be called from app startup.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta

from services.market_engine import scanner_worker

logger = logging.getLogger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────────
TICK_INTERVAL = 12          # seconds between task ticks (one task per tick)
PRICE_STREAM_INTERVAL = 15  # seconds between live-price broadcasts
ALERT_DEDUP_MINUTES = 30    # suppress duplicate target/SL alerts within window
VOLUME_BATCH_SIZE = 8       # stocks scanned per "Checking Volume" tick

# ── Engine state (set in start_engine) ────────────────────────────────────
_db = None
_ws = None
_started = False
_batch_ptr = 0


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def _send_user(user_id, message):
    """Deliver a message to a single user's WebSocket connections."""
    if not _ws:
        return
    try:
        await _ws.send_to_user(str(user_id), message)
    except Exception as e:
        logger.error(f"Heartbeat send_to_user error: {e}")


# D6.3 — a `_broadcast(message)` helper lived here, wrapping
# `ws_manager.broadcast`. Nothing had ever called it. It is removed rather than
# left dormant: this module's job is per-account work (`_send_to_user`, directly
# above, is the one delivery primitive it needs), and an unused fan-out sitting
# next to it is the exact shape D6.1 / S6 catalogued — a private payload is one
# call away from every connected socket, with nothing failing and nothing logged.
# Public, market-wide fan-out belongs to the two loops in `server.py` that own it.


async def _publish(event_type, data):
    """Publish a real domain event onto the market event bus (Sprint R3).

    The R2 event bridge forwards every bus event to the matching socket channel,
    so these publishes are what let the Scanner / News / Sectors / Markets
    surfaces update live without polling. Data here is always the REAL result
    the calling task already computed — never fabricated. Best-effort: a publish
    failure must never break the task's core work."""
    try:
        from services.market_engine.event_bus import event_bus
        await event_bus.publish(event_type, data or {})
    except Exception as e:
        logger.warning(f"Heartbeat publish '{event_type}' failed: {e}")


async def _price_map(symbols):
    """Fetch {symbol: quote} for a list of symbols via cached Yahoo 2d quotes."""
    from services.real_market import fetch_yahoo_quote
    symbols = list(symbols)
    if not symbols:
        return {}
    results = await asyncio.gather(
        *[fetch_yahoo_quote(s, "2d") for s in symbols], return_exceptions=True
    )
    out = {}
    for sym, res in zip(symbols, results):
        if isinstance(res, dict) and res:
            out[sym] = res
    return out


def _next_volume_batch():
    """Rotate through STOCK_UNIVERSE, returning the next slice of symbols."""
    global _batch_ptr
    from market_data import STOCK_UNIVERSE
    symbols = [s["symbol"] for s in STOCK_UNIVERSE]
    if not symbols:
        return []
    start = _batch_ptr % len(symbols)
    batch = symbols[start:start + VOLUME_BATCH_SIZE]
    if len(batch) < VOLUME_BATCH_SIZE:
        batch += symbols[: VOLUME_BATCH_SIZE - len(batch)]
    _batch_ptr = (start + VOLUME_BATCH_SIZE) % len(symbols)
    return batch


# ═══════════════════════════════════════════════════════════════════════════
# REAL TASKS — each logs running -> done/warning around genuine work
# ═══════════════════════════════════════════════════════════════════════════

async def task_global_markets():
    from services.activity_logger import log_platform_activity as log_activity
    from services.real_market import fetch_real_global_markets
    log_activity("Reading Global Markets", "scan", "running")
    try:
        markets = await fetch_real_global_markets()
        valid = [m for m in (markets or []) if m.get("value")]
        if not valid:
            log_activity("Global markets data unavailable", "scan", "warning")
            return
        leader = max(valid, key=lambda m: m.get("change_pct", 0))
        log_activity(
            f"Read {len(valid)} global indices — {leader['name']} {leader['change_pct']:+.2f}%",
            "scan", "done",
        )
        # Stream live global indices to the Markets page.
        await _publish("market.global.updated", {"markets": valid})
    except Exception as e:
        logger.error(f"task_global_markets error: {e}")
        log_activity("Reading Global Markets failed", "scan", "warning")


async def task_us_markets():
    from services.activity_logger import log_platform_activity as log_activity
    from services.real_market import fetch_yahoo_quote
    log_activity("Reading US Markets", "scan", "running")
    try:
        sp, nasdaq, dow = await asyncio.gather(
            fetch_yahoo_quote("^GSPC", "2d"),
            fetch_yahoo_quote("^IXIC", "2d"),
            fetch_yahoo_quote("^DJI", "2d"),
            return_exceptions=True,
        )
        parts = []
        for name, q in (("S&P 500", sp), ("Nasdaq", nasdaq), ("Dow", dow)):
            if isinstance(q, dict) and q:
                parts.append(f"{name} {q.get('change_pct', 0):+.2f}%")
        if parts:
            log_activity("US Markets — " + ", ".join(parts), "scan", "done")
        else:
            log_activity("US Markets data unavailable", "scan", "warning")
    except Exception as e:
        logger.error(f"task_us_markets error: {e}")
        log_activity("Reading US Markets failed", "scan", "warning")


async def task_fii_dii():
    from services.activity_logger import log_platform_activity as log_activity
    from services.real_market import fetch_real_fii_dii
    log_activity("Checking FII/DII Data", "scan", "running")
    try:
        data = await fetch_real_fii_dii()
        if not data or data.get("source") == "unavailable":
            log_activity("FII/DII data not yet published for today", "scan", "warning")
            return
        fii = data.get("fii", {}).get("net", 0)
        dii = data.get("dii", {}).get("net", 0)
        log_activity(
            f"FII net {fii:+,.0f} Cr, DII net {dii:+,.0f} Cr", "scan", "done"
        )
    except Exception as e:
        logger.error(f"task_fii_dii error: {e}")
        log_activity("Checking FII/DII Data failed", "scan", "warning")


async def task_scan_news():
    from services.activity_logger import log_platform_activity as log_activity
    from services.news_service import fetch_news, filter_breaking_novel
    log_activity("Scanning News", "news", "running")
    try:
        news = await fetch_news()
        if not news:
            log_activity("No fresh market headlines available", "news", "warning")
            return
        log_activity(f"Scanned {len(news)} market headlines", "news", "done")
        # Stream the latest headlines live to the News/Dashboard surfaces.
        await _publish("news.received", {"articles": news[:10], "count": len(news)})
        # Breaking headlines (Sprint R8): novelty-gated so each event carries
        # only headlines never streamed before — the frontend toasts these.
        breaking = filter_breaking_novel(news)
        if breaking:
            log_activity(
                f"{len(breaking)} breaking headline(s): {breaking[0]['title'][:60]}",
                "news", "warning",
            )
            await _publish("news.breaking", {
                "articles": breaking[:5],
                "count": len(breaking),
            })
    except Exception as e:
        logger.error(f"task_scan_news error: {e}")
        log_activity("Scanning News failed", "news", "warning")


async def task_find_breakouts():
    from services.activity_logger import log_platform_activity as log_activity
    from services.real_market import fetch_all_universe_quotes
    log_activity("Finding Breakouts", "scan", "running")
    try:
        quotes = await fetch_all_universe_quotes()
        candidates = [
            q for q in (quotes or [])
            if q.get("high") and q.get("price")
            and q["price"] >= q["high"] * 0.995
            and q.get("change_pct", 0) > 1
        ]
        if candidates:
            candidates.sort(key=lambda q: q.get("change_pct", 0), reverse=True)
            names = ", ".join(q["symbol"] for q in candidates[:3])
            log_activity(
                f"Found {len(candidates)} breakout candidate(s): {names}", "scan", "done"
            )
            # Stream only NEW hits to the live Scanner feed (Sprint R4): the
            # scan re-detects the same breakout every cycle, so novelty gating
            # keeps the feed from flooding with repeats.
            novel = scanner_worker.filter_novel("breakout", candidates)
            if novel:
                await _publish("scanner.breakout", {
                    "kind": "breakout",
                    "candidates": novel[:10],
                    "count": len(novel),
                })
        else:
            log_activity("No breakouts right now — market consolidating", "scan", "done")
    except Exception as e:
        logger.error(f"task_find_breakouts error: {e}")
        log_activity("Finding Breakouts failed", "scan", "warning")


async def task_check_volume():
    from services.activity_logger import log_platform_activity as log_activity
    from services.real_market import fetch_real_stock_quote
    log_activity("Checking Volume", "scan", "running")
    try:
        batch = _next_volume_batch()
        results = await asyncio.gather(
            *[fetch_real_stock_quote(s) for s in batch], return_exceptions=True
        )
        surges = [
            r for r in results
            if isinstance(r, dict) and r and r.get("volume_ratio", 0) > 1.5
        ]
        if surges:
            names = ", ".join(r["symbol"] for r in surges[:3])
            log_activity(
                f"{len(surges)}/{len(batch)} stocks with unusual volume: {names}",
                "scan", "done",
            )
            surges.sort(key=lambda r: r.get("volume_ratio") or 0, reverse=True)
            # Stream only NEW volume spikes (Sprint R4; event name per
            # REALTIME_SYSTEM.md — was `scanner.volume` before R4).
            novel = scanner_worker.filter_novel("volume_spike", surges)
            if novel:
                await _publish("scanner.volume_spike", {
                    "kind": "volume_spike",
                    "candidates": novel[:10],
                    "count": len(novel),
                })
        else:
            log_activity(f"Volume normal across {len(batch)} stocks scanned", "scan", "done")
    except Exception as e:
        logger.error(f"task_check_volume error: {e}")
        log_activity("Checking Volume failed", "scan", "warning")


async def task_scan_momentum():
    from services.activity_logger import log_platform_activity as log_activity
    from services.real_market import fetch_all_universe_quotes
    log_activity("Scanning Momentum", "scan", "running")
    try:
        quotes = await fetch_all_universe_quotes()
        if not quotes:
            log_activity("Momentum scan skipped — live data unavailable", "scan", "warning")
            return
        candidates = scanner_worker.momentum_pass(quotes)
        if candidates:
            names = ", ".join(q["symbol"] for q in candidates[:3])
            log_activity(
                f"Found {len(candidates)} momentum mover(s): {names}", "scan", "done"
            )
            novel = scanner_worker.filter_novel("momentum", candidates)
            if novel:
                await _publish("scanner.momentum", {
                    "kind": "momentum",
                    "candidates": novel[:10],
                    "count": len(novel),
                })
        else:
            log_activity(f"No fresh momentum across {len(quotes)} stocks", "scan", "done")
    except Exception as e:
        logger.error(f"task_scan_momentum error: {e}")
        log_activity("Scanning Momentum failed", "scan", "warning")


_sweep_ptr = 0


async def task_scanner_sweep():
    """Continuously re-run the preset scanners (2 per tick, rotating) and emit
    ONE worker-tagged `scanner.updated` — the frontend's signal to refresh the
    scanner results table without polling (Sprint R4). Preset scans reuse the
    30s-cached universe quotes, so a sweep costs ~zero extra upstream calls."""
    global _sweep_ptr
    from services.activity_logger import log_platform_activity as log_activity
    from services.market_engine import scanner_engine
    log_activity("Sweeping Scanner Strategies", "scan", "running")
    try:
        keys = list(scanner_engine.STRATEGY_PRESETS.keys())
        picked = [keys[(_sweep_ptr + i) % len(keys)] for i in range(2)]
        _sweep_ptr = (_sweep_ptr + 2) % len(keys)

        strategies = {}
        for key in picked:
            result = await scanner_engine.scan(strategy=key, limit=5, publish=False)
            if not result.get("available", True):
                log_activity("Scanner sweep skipped — live data unavailable", "scan", "warning")
                return
            top = result["results"][0]["symbol"] if result.get("results") else None
            strategies[key] = {"matched": result.get("total_matched", 0), "top": top}

        summary = ", ".join(
            f"{k}: {v['matched']}" for k, v in strategies.items()
        )
        log_activity(f"Scanner sweep complete — {summary}", "scan", "done")
        await _publish("scanner.updated", {
            "source": "worker",
            "strategies": strategies,
            "scanned_at": _now_iso(),
        })
    except Exception as e:
        logger.error(f"task_scanner_sweep error: {e}")
        log_activity("Scanner sweep failed", "scan", "warning")


async def _recent_alert_exists(user_id, ntype, symbol):
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=ALERT_DEDUP_MINUTES)).isoformat()
    existing = await _db.notifications.find_one({
        "user_id": user_id,
        "type": ntype,
        "symbol": symbol,
        "created_at": {"$gte": cutoff},
    })
    return existing is not None


async def task_monitor_trades():
    """Stream per-user ``trade.updated`` snapshots for every open trade
    (Sprint R6 — replaces the pre-R6 per-user legacy ``trade_update`` push,
    whose P&L ignored partial exits and short direction, plus its duplicate
    SL/target alerts: the trading engine owns real-trade lifecycle alerts,
    correctly). Paper trades keep a side-aware crossing alert here — the
    engine never touches them, so this is their only watchdog."""
    from services import trade_stream
    from services.activity_logger import log_platform_activity as log_activity
    log_activity("Monitoring Open Trades", "monitor", "running")
    try:
        open_trades = await _db.trades.find({"status": "OPEN"}).to_list(200)
        if not open_trades:
            log_activity("No open trades to monitor", "monitor", "done")
            return

        symbols = list({t["symbol"] for t in open_trades})
        quotes = await _price_map(symbols)
        users = await trade_stream.publish_all(_db, quotes, reason="monitor")

        # Paper-trade SL/target crossing alerts (side-aware, deduped).
        alerts_sent = 0
        for trade in open_trades:
            if not trade.get("is_paper"):
                continue
            quote = quotes.get(trade["symbol"])
            if not quote or quote.get("price") is None:
                continue
            price = quote["price"]
            short = (trade.get("type") or "BUY").upper() == "SELL"
            sl = trade.get("stop_loss")
            t1 = trade.get("target1")
            crossed = None
            if sl and (price <= sl if not short else price >= sl):
                crossed = ("STOP_LOSS_HIT", "critical",
                           f"Paper trade {trade['symbol']} hit stop-loss ₹{sl}. Current ₹{price}.")
            elif t1 and (price >= t1 if not short else price <= t1):
                crossed = ("TARGET_HIT", "positive",
                           f"Paper trade {trade['symbol']} hit target ₹{t1}! Current ₹{price}.")

            if crossed and not await _recent_alert_exists(
                    trade["user_id"], crossed[0], trade["symbol"]):
                ntype, severity, message = crossed
                from services.notification_service import create_notification
                await create_notification(
                    _db, trade["user_id"], type=ntype,
                    title=f"AI Alert: {trade['symbol']}", message=message,
                    severity=severity, symbol=trade["symbol"],
                    data={"trade_id": str(trade["_id"]), "price": price})
                alerts_sent += 1

        summary = f"Monitored {len(open_trades)} open position(s) across {users} trader(s)"
        if alerts_sent:
            summary += f" — {alerts_sent} paper alert(s) fired"
        log_activity(summary, "monitor", "warning" if alerts_sent else "done")
    except Exception as e:
        logger.error(f"task_monitor_trades error: {e}")
        log_activity("Monitoring Open Trades failed", "monitor", "warning")


async def task_monitor_portfolio():
    """Recompute every trader's FULL portfolio (broker holdings + manual open
    trades, via portfolio_engine) and stream a per-user `portfolio.updated`
    event through the bus/bridge (Sprint R5 — replaces the pre-R5 legacy
    `portfolio_update` push that covered manual trades only)."""
    from services import portfolio_stream
    from services.activity_logger import log_platform_activity as log_activity
    log_activity("Monitoring Portfolio", "monitor", "running")
    try:
        open_trades = await _db.trades.find({"status": "OPEN"}).to_list(200)
        broker_holdings = await _db.holdings.find({}).to_list(500)
        manual = [t for t in open_trades if not t.get("is_paper")]
        user_ids = {t["user_id"] for t in manual} | {h["user_id"] for h in broker_holdings}
        if not user_ids:
            log_activity("No portfolios with open positions", "monitor", "done")
            return

        # One shared quote fetch for every symbol held by anyone this cycle —
        # each user's snapshot then reads from the prefetched map (no N× fetch).
        #
        # D5.16 — the share is now conditional, for the same reason the price
        # broadcast's is (`_publish_prices`): a user with their own promoted
        # feed must be marked from *their* feed, and one platform resolution
        # reused for everybody makes that unreachable by construction. The
        # Source Manager answers whether a user's baseline is genuinely the
        # shared one; only those users share, and there is one extra resolution
        # per promoted account rather than one per user.
        from services.market_engine.gateway import market_gateway
        symbols = {t["symbol"] for t in manual} | {
            h["symbol"] for h in broker_holdings if h.get("symbol")}
        prefetched = await portfolio_stream.quotes_map(list(symbols))

        async def shared_quotes(syms):
            return {(s or "").upper(): prefetched.get((s or "").upper()) for s in syms}

        streamed = 0
        for user_id in user_ids:
            shared = market_gateway.baseline_prices_are_shared(str(user_id))
            snapshot = await portfolio_stream.publish_snapshot(
                _db, user_id,
                quotes_map_func=shared_quotes if shared else None,
                reason="monitor")
            if snapshot:
                streamed += 1

        log_activity(
            f"Portfolio P&L streamed for {streamed} trader(s)", "monitor", "done"
        )
    except Exception as e:
        logger.error(f"task_monitor_portfolio error: {e}")
        log_activity("Monitoring Portfolio failed", "monitor", "warning")


async def task_top_picks():
    from services.activity_logger import log_platform_activity as log_activity
    from services.real_market import fetch_real_top_picks
    log_activity("Finding Top Picks", "rank", "running")
    try:
        result = await fetch_real_top_picks(3)
        picks = result.get("picks", []) if result else []
        if not picks:
            log_activity("No high-conviction picks right now", "rank", "done")
            return
        await _db.market_analysis.update_one(
            {"date": _today()},
            {"$set": {"top_picks": picks, "picks_generated_at": _now_iso()}},
            upsert=True,
        )
        top = picks[0]
        log_activity(
            f"Top pick: {top['symbol']} ({top.get('confidence', 0)}% confidence)",
            "rank", "done",
        )
    except Exception as e:
        logger.error(f"task_top_picks error: {e}")
        log_activity("Finding Top Picks failed", "rank", "warning")


async def task_sentiment():
    from services.activity_logger import log_platform_activity as log_activity
    from services.real_market import fetch_all_universe_quotes
    log_activity("Analyzing Sentiment", "rank", "running")
    try:
        quotes = await fetch_all_universe_quotes()
        quotes = [q for q in (quotes or []) if q.get("change_pct") is not None]
        total = len(quotes)
        if not total:
            log_activity("Sentiment data unavailable", "rank", "warning")
            return
        up = len([q for q in quotes if q.get("change_pct", 0) > 0])
        ratio = up / total
        mood = "Bullish" if ratio > 0.6 else "Bearish" if ratio < 0.4 else "Neutral"
        log_activity(
            f"Market sentiment {mood} — {up}/{total} stocks advancing", "rank", "done"
        )
        # Stream live breadth + top movers to the Markets heatmap / breadth bar.
        ranked = sorted(quotes, key=lambda q: q.get("change_pct", 0), reverse=True)
        await _publish("market.movers.updated", {
            "gainers": ranked[:5],
            "losers": list(reversed(ranked[-5:])) if len(ranked) >= 5 else [],
        })
        await _publish("breadth.updated", {
            "advances": up, "declines": total - up, "total": total,
            "sentiment": mood, "advance_ratio": round(ratio, 3),
        })
    except Exception as e:
        logger.error(f"task_sentiment error: {e}")
        log_activity("Analyzing Sentiment failed", "rank", "warning")


async def task_sector_rotation():
    from services.activity_logger import log_platform_activity as log_activity
    from services.real_market import fetch_real_sectors
    log_activity("Checking Sector Rotation", "scan", "running")
    try:
        sectors = await fetch_real_sectors()
        if not sectors:
            log_activity("Sector data unavailable", "scan", "warning")
            return
        lead = sectors[0]
        lag = sectors[-1]
        log_activity(
            f"Sector rotation — {lead['sector']} leading ({lead['change_pct']:+.2f}%), "
            f"{lag['sector']} lagging ({lag['change_pct']:+.2f}%)",
            "scan", "done",
        )
        # Stream live sector performance to the Markets/Dashboard heatmap.
        await _publish("sector.updated", {"sectors": sectors})
    except Exception as e:
        logger.error(f"task_sector_rotation error: {e}")
        log_activity("Checking Sector Rotation failed", "scan", "warning")


async def task_economic_calendar():
    from services.activity_logger import log_platform_activity as log_activity
    from services.news_service import fetch_news
    log_activity("Watching Economic Calendar", "news", "running")
    keywords = ("rbi", "inflation", "gdp", "repo rate", "fed", "interest rate",
                "monetary policy", "cpi", "wpi", "fiscal", "budget", "rate cut")
    try:
        news = await fetch_news()
        hits = [
            a for a in (news or [])
            if any(k in (a.get("title", "") + " " + a.get("summary", "")).lower() for k in keywords)
        ]
        if hits:
            log_activity(f"{len(hits)} macro/policy headline(s) on the radar", "news", "done")
        else:
            log_activity("No major economic events flagged", "news", "done")
    except Exception as e:
        logger.error(f"task_economic_calendar error: {e}")
        log_activity("Watching Economic Calendar failed", "news", "warning")


async def task_earnings():
    from services.activity_logger import log_platform_activity as log_activity
    from services.news_service import fetch_news
    log_activity("Checking Earnings", "news", "running")
    keywords = ("earnings", "quarterly result", "q1 result", "q2 result", "q3 result",
                "q4 result", "net profit", "revenue", "results ", "profit rises",
                "profit falls", "beats estimate", "misses estimate")
    try:
        news = await fetch_news()
        hits = [
            a for a in (news or [])
            if any(k in (a.get("title", "") + " " + a.get("summary", "")).lower() for k in keywords)
        ]
        if hits:
            log_activity(f"{len(hits)} earnings-related headline(s) detected", "news", "done")
        else:
            log_activity("No notable earnings in the headlines", "news", "done")
    except Exception as e:
        logger.error(f"task_earnings error: {e}")
        log_activity("Checking Earnings failed", "news", "warning")


#: Max symbols enriched **per account** per cycle.
#:
#: Was a platform-wide cap, which is what a platform-wide query needs. Now that
#: the cycle is per account the same number means something different and
#: better: one user with a 400-symbol watchlist can no longer consume the whole
#: budget and leave every other connected account unrefreshed.
WATCHLIST_STREAM_CAP = 40


async def _watchlist_symbols(user_id) -> list:
    """One account's watchlisted symbols — filtered by owner, always.

    D5.16 §2. This read was `db.watchlist.distinct("symbol")` with **no filter**
    and its result was published as one event with no `user_id`, which the event
    bridge therefore *broadcast*: every socket on the `watchlist` channel
    received every user's watchlisted symbols and their prices, and the browser
    folded them straight into its live price store.

    The scoping is here, at selection, rather than at delivery. Publishing
    per-user while still selecting globally would have kept exactly the same
    disclosure and merely made it harder to see — and a filter applied by the
    consumer is not a boundary at all.
    """
    if _db is None:
        return []
    try:
        symbols = await _db.watchlist.distinct("symbol", {"user_id": str(user_id)})
    except Exception as e:
        # No account identifier in the message: this line is reachable on every
        # cycle for every connected user, and the id is what identifies them.
        logger.error(f"Watchlist symbol read for one account failed: {e}")
        return []
    return sorted({s for s in symbols if s})[:WATCHLIST_STREAM_CAP]


#: The fields a `watchlist.quotes` entry carries.
#:
#: A closed list rather than the resolved quote, for two reasons that happen to
#: point the same way. Data minimisation — this payload crosses a socket and the
#: normalized quote carries a dozen fields the widget does not render. And
#: containment — `source_tier` aside, nothing about *which* provider answered may
#: reach a consumer (Developer Rule 4), and a closed projection makes that a
#: property of this boundary rather than of every future normalizer field.
_WATCHLIST_QUOTE_FIELDS = ("price", "change_pct", "rsi", "volume_ratio", "source_tier")


async def _watchlist_quotes(user_id, symbols: list) -> dict:
    """`{SYMBOL: quote}` for one account's watchlist, through the gateway.

    D5.16 §5. This called `fetch_real_stock_quote` directly — Yahoo, with no
    user and no resolution — so a user on their own broker feed was quoted from
    the delayed baseline on the very surface that shows a live price, and no
    ranking could have changed it.

    A broker feed's quote is *thin*: a `MarketTick` carries a price and not an
    RSI. Absent fields are omitted rather than written as null, so the merge in
    the browser's price store keeps the technical fields an earlier cycle or the
    REST read supplied instead of blanking them.
    """
    from services.market_engine.gateway import market_gateway

    resolved = await market_gateway.get_prices(symbols, user_id=user_id)
    quotes = {}
    for symbol, quote in resolved.items():
        entry = {k: quote[k] for k in _WATCHLIST_QUOTE_FIELDS
                 if quote.get(k) is not None}
        if entry.get("price") is not None:
            quotes[symbol] = entry
    return quotes


async def task_watchlist_stream():
    """Stream enriched quotes (RSI, volume ratio) to each connected account for
    that account's own watchlist, as a per-user ``watchlist.quotes`` event
    (Sprint R8; scoped and canonically routed in D5.16).

    The fast 15s price loop already covers price/change; this slower task covers
    the technical fields the Watchlist UI shows, so the page needs no fallback
    poll while connected.

    The recipient set is the *connected* accounts, taken from the socket manager
    exactly as `_publish_prices` takes it. A user with rows in the database and
    no socket produces no work: there is nobody to send it to, and resolving a
    watchlist for an absent user would be a per-user query with a platform-wide
    cost.
    """
    from services.activity_logger import log_platform_activity as log_activity
    log_activity("Refreshing Watchlists", "monitor", "running")
    try:
        users = list(getattr(_ws, "user_connections", {}) or {})
        if not users:
            log_activity("No connected accounts to refresh", "monitor", "done")
            return
        served = 0
        total = 0
        for user_id in users:
            try:
                symbols = await _watchlist_symbols(user_id)
                if not symbols:
                    continue
                quotes = await _watchlist_quotes(user_id, symbols)
                if not quotes:
                    continue
                # `user_id` is what makes the event bridge deliver rather than
                # broadcast (`realtime/event_bridge._deliver`). It is the whole
                # security property of this publish, so it is not optional and
                # not conditional.
                await _publish("watchlist.quotes", {
                    "user_id": str(user_id),
                    "quotes": quotes,
                    "count": len(quotes),
                })
                served += 1
                total += len(quotes)
            except Exception as e:
                # One account's failure must not cost every other connected
                # account its refresh.
                logger.warning(f"Watchlist refresh for one account failed: {e}")
        if not served:
            log_activity("No watchlisted stocks to refresh", "monitor", "done")
            return
        log_activity(
            f"Watchlists refreshed — {total} live quotes across {served} account(s)",
            "monitor", "done",
        )
    except Exception as e:
        logger.error(f"task_watchlist_stream error: {e}")
        log_activity("Refreshing Watchlists failed", "monitor", "warning")


async def task_morning_report():
    from services.activity_logger import log_platform_activity as log_activity
    from services.real_market import fetch_real_market_overview
    log_activity("Preparing Morning Report", "rank", "running")
    try:
        overview = await fetch_real_market_overview()
        if not overview:
            log_activity("Morning report data unavailable", "rank", "warning")
            return
        await _db.market_analysis.update_one(
            {"date": _today()},
            {"$set": {"overview_snapshot": overview, "overview_updated_at": _now_iso()}},
            upsert=True,
        )
        nifty = overview.get("nifty", {})
        log_activity(
            f"Morning report data refreshed — Nifty {nifty.get('value', 0):,.0f} "
            f"({nifty.get('change_pct', 0):+.2f}%)",
            "rank", "done",
        )
    except Exception as e:
        logger.error(f"task_morning_report error: {e}")
        log_activity("Preparing Morning Report failed", "rank", "warning")


# Task registry: (fn, minimum interval in seconds).
# Monitoring runs most often; heavy/cached tasks run rarely.
TASKS = [
    (task_monitor_trades, 60),
    (task_monitor_portfolio, 90),
    (task_sentiment, 100),
    (task_find_breakouts, 120),
    (task_check_volume, 140),
    (task_scan_momentum, 150),
    (task_scan_news, 150),
    (task_scanner_sweep, 180),
    (task_watchlist_stream, 120),
    (task_sector_rotation, 160),
    (task_global_markets, 180),
    (task_us_markets, 200),
    (task_economic_calendar, 300),
    (task_earnings, 300),
    (task_morning_report, 600),
    (task_fii_dii, 600),
    (task_top_picks, 900),
]


async def _heartbeat_loop():
    """Every TICK_INTERVAL, run the single most-overdue due task (staggered)."""
    loop = asyncio.get_event_loop()
    start = loop.time()
    # Seed last-run times so every task is immediately due, with a tiny
    # per-task offset so shorter-interval (more important) tasks fire first.
    state = [
        {"fn": fn, "interval": interval, "last": start - interval - i * 0.1}
        for i, (fn, interval) in enumerate(TASKS)
    ]
    logger.info("AI heartbeat loop running with %d real tasks", len(state))
    while True:
        try:
            now = loop.time()
            due = [s for s in state if (now - s["last"]) >= s["interval"]]
            if due:
                chosen = max(due, key=lambda s: (now - s["last"]) / s["interval"])
                chosen["last"] = now
                await chosen["fn"]()
        except Exception as e:
            logger.error(f"Heartbeat loop error: {e}")
        await asyncio.sleep(TICK_INTERVAL)


#: Index keys carried on the overview, and the canonical symbols the price
#: store keys them by.
_INDEX_SYMBOLS = (("nifty", "NIFTY"), ("bank_nifty", "BANKNIFTY"), ("sensex", "SENSEX"))

#: The overview key India VIX is carried under, and its canonical symbol.
#:
#: It is a bare number on the overview, not a `{value, change_pct}` block like
#: the three above — the provider publishes no day-change for it — so it is
#: unpacked separately rather than being bent into the same loop. The symbol is
#: the one `real_market.INDEX_TICKERS` and `catalogue.INDEX_ALIASES` already
#: agree on, which is what lets the delayed baseline and a broker tick land in
#: the same slot of the same price store.
_VIX_KEY, _VIX_SYMBOL = "india_vix", "INDIAVIX"

#: Every canonical index symbol this loop publishes.
INDEX_PRICE_SYMBOLS = tuple(symbol for _key, symbol in _INDEX_SYMBOLS) + (_VIX_SYMBOL,)


async def _index_prices(user_id=None) -> dict:
    """`{NIFTY|BANKNIFTY|SENSEX|INDIAVIX: {price, change_pct}}` for one account.

    D5.15 — was a direct `fetch_real_market_overview()` call. Indices are
    resolved for a user like everything else, so a provider that carries them
    for one entitlement and not another is chosen per user rather than assumed.

    D5.17 — TWO READS, AND WHY THAT IS NOT A SECOND PIPELINE
    --------------------------------------------------------
    `get_indices` serves `Capability.INDICES`, which only a polling provider
    declares: a broker feed publishes TICKS and QUOTES and has no notion of a
    market *overview*. So the overview is, and will remain, the delayed
    baseline — and once D5.17 put the indices on broker feeds, a user on a live
    feed had a tick worth 24815.25 arriving on `market.tick` and this loop
    overwriting it 15 seconds later with the baseline's 24810. Visibly: an index
    card that flickered between two numbers, one of them stale, with the feed
    indicator reading `Live`.

    The fix is not to skip the overview — it carries the day-change a
    `MarketTick` cannot — but to ask the **same** canonical question about the
    price that every equity on this page is already asked:
    `get_prices(..., user_id=...)`, per symbol, through the Source Manager. For
    a user with no feed of their own it resolves the baseline and the answer is
    identical to the overview's. For a user whose feed covers the index it
    resolves that feed, which is the whole point of D5.17.

    `price` is the only field taken from that resolution. A thin streaming quote
    carries no `change_pct`, and writing one that is not there — or a zero —
    would put a fabricated "unchanged" beside a real live price.
    """
    from services.market_engine.gateway import market_gateway
    out = {}
    try:
        overview = await market_gateway.get_indices(user_id=user_id)
    except Exception as e:
        logger.warning(f"Index prices unavailable: {e}")
        overview = None
    for key, symbol in _INDEX_SYMBOLS:
        value = (overview or {}).get(key) or {}
        if value.get("value") is not None:
            out[symbol] = {"price": value["value"], "change_pct": value.get("change_pct", 0)}
    vix = (overview or {}).get(_VIX_KEY)
    if isinstance(vix, (int, float)) and not isinstance(vix, bool):
        # No `change_pct`: the provider publishes none for VIX and inventing one
        # is the fabrication this whole path exists to avoid.
        out[_VIX_SYMBOL] = {"price": vix}
    try:
        resolved = await market_gateway.get_prices(INDEX_PRICE_SYMBOLS, user_id=user_id)
    except Exception as e:
        logger.warning(f"Index price resolution failed: {e}")
        return out
    for symbol, quote in resolved.items():
        price = quote.get("price")
        if price is None:
            continue
        # An index the overview could not supply is still published when a feed
        # can price it — a live NIFTY beside a missing baseline is strictly
        # better than nothing, and the merge below adds no field it did not get.
        out.setdefault(symbol, {})["price"] = price
    return out


async def _user_price_symbols(user_id: str) -> set:
    """The equity symbols one user's live price stream should carry.

    Their own watchlist, their own open trades, and the dashboard universe every
    account sees. Scoped to the user — D5.15. This read used to be
    `db.watchlist.distinct("symbol")` with **no filter**, so the map broadcast to
    every socket carried every user's watchlist symbols; per-user delivery
    without per-user selection would have kept that and merely hidden it.
    """
    from services.brokers.feed_universe import dashboard_symbols
    symbols = set(dashboard_symbols())
    if _db is None:
        return symbols
    try:
        symbols.update(await _db.watchlist.distinct("symbol", {"user_id": str(user_id)}))
        symbols.update(await _db.trades.distinct(
            "symbol", {"user_id": str(user_id), "status": "OPEN"}))
    except Exception as e:
        logger.error(f"Price symbol gather for one account failed: {e}")
    return {s for s in symbols if s}


async def _collect_prices(user_id=None):
    """`{SYMBOL: {price, change_pct, source_tier}}` for one account.

    D5.15 — EVERY PRICE ON THIS PATH NOW COMES THROUGH THE MARKET GATEWAY.
    It previously called Yahoo directly (`fetch_all_universe_quotes`, behind a
    300-second bundle cache) and returned one global map for every socket. That
    made a per-user broker feed unreachable by construction — the loop had no
    user, so no per-user provider could ever be a candidate — and presented
    five-minute-old data as live. Resolution, failover, freshness and the tier
    stamp are the Source Manager's again, exactly as Developer Rule 2 requires.

    `user_id=None` is the platform/baseline resolution and is what the shared
    fan-out below computes once.
    """
    from services.brokers.feed_universe import dashboard_symbols
    from services.market_engine.gateway import market_gateway
    data = await _index_prices(user_id)
    symbols = await _user_price_symbols(user_id) if user_id else set(dashboard_symbols())
    if symbols:
        try:
            data.update(await market_gateway.get_prices(sorted(symbols), user_id=user_id))
        except Exception as e:
            logger.error(f"Price resolution failed: {e}")
    return data


async def _publish_prices() -> int:
    """Send each connected account its own resolved price map. Returns the
    number of accounts served.

    THE FAN-OUT, AND WHY IT IS STILL ONE RESOLUTION FOR ALMOST EVERYBODY
    ---------------------------------------------------------------------
    Per-user resolution is required for correctness (a user on their own broker
    feed must get *their* prices) and would be wasteful if taken literally: for
    a user with no provider of their own, resolving with their id and resolving
    for the platform choose from the identical candidate set and return the
    identical answer. `market_gateway.baseline_prices_are_shared` is that
    question asked of the Source Manager rather than guessed from "has a broker
    connected", and the users it answers True for share one resolution.

    A user with a personal feed is resolved on their own — that is the whole
    point, and there is one such resolution per promoted account, not per socket.
    """
    from services.market_engine.gateway import market_gateway
    from services.market_engine.source_manager import source_manager
    users = list(getattr(_ws, "user_connections", {}) or {})
    if not users:
        return 0
    shared_users = [u for u in users if market_gateway.baseline_prices_are_shared(u)]
    shared_prices = await _collect_prices(None) if shared_users else {}
    served = 0
    for user_id in users:
        try:
            if user_id in shared_users:
                symbols = await _user_price_symbols(user_id)
                prices = {s: q for s, q in shared_prices.items()
                          if s in symbols or s in INDEX_PRICE_SYMBOLS}
            else:
                prices = await _collect_prices(user_id)
            if not prices:
                continue
            await _ws.send_to_user(user_id, {
                "type": "prices",
                "data": prices,
                "timestamp": _now_iso(),
            })
            # D5.15 — ANNOUNCE THE TIER THIS USER IS ACTUALLY ON.
            #
            # Every other `provider.status` publish is driven by a *state
            # transition*: a provider registering, unregistering, or changing
            # readiness or stability. Staleness is none of those. A feed whose
            # socket stays open and simply stops delivering is demoted lazily,
            # inside `is_eligible_for`, when the next resolution asks — so
            # prices correctly fall back to the baseline and **no event is ever
            # published**. The D5.14 indicator is event-driven, so it went on
            # reading `Live` while the data behind it was the delayed baseline.
            #
            # Observed, not reasoned about: a live feed stopped delivering at
            # 09:45:20Z and the last `provider.status` on the bus was from
            # 09:44:02Z, tier `streaming`. That is precisely the "showing Yahoo
            # data while implying the broker feed is live" case the feed-state
            # contract exists to prevent.
            #
            # This loop is the right place because it is already resolving this
            # user's feed on a cadence, so the answer costs nothing extra, and
            # `publish_status` is change-gated per user — it emits only when the
            # state, tier or reason actually moved. A steady feed produces no
            # events at all; a feed that went stale produces exactly one.
            await source_manager.publish_status(user_id=user_id)
            served += 1
        except Exception as e:
            logger.error(f"Price delivery for one account failed: {e}")
    return served


async def _price_stream_loop():
    """Send each connected account its live prices every PRICE_STREAM_INTERVAL seconds."""
    logger.info("AI price stream loop running")
    while True:
        try:
            if _ws and _ws.active:
                await _publish_prices()
        except Exception as e:
            logger.error(f"Price stream loop error: {e}")
        await asyncio.sleep(PRICE_STREAM_INTERVAL)


#: Names the two loops are registered under (PH3.6). Module constants rather
#: than string literals at the call sites, because `stop_engine` must cancel
#: exactly what `start_engine` spawned and a typo would be a silent no-op.
HEARTBEAT_TASK = "ai-heartbeat-loop"
PRICE_STREAM_TASK = "ai-price-stream-loop"


def start_engine(db, ws_manager):
    """Start the heartbeat + price-stream background loops.

    Idempotent. No-op when DISABLE_BACKGROUND_ENGINE=1 (used by the test suite,
    mirroring how the scheduler is kept out of the way during pytest).

    PH3.6: the two loops are now spawned through `infrastructure.tasks` rather
    than bare `asyncio.create_task`, which is what gives them a strong reference
    and — the part that was actually missing — a shutdown path. Both loops read
    Mongo (`_collect_prices` calls `distinct` on watchlist and trades), so before
    this they kept issuing queries against a client the shutdown handler was in
    the middle of closing.
    """
    global _db, _ws, _started
    if _started:
        return
    if os.environ.get("DISABLE_BACKGROUND_ENGINE") == "1":
        logger.info("AI heartbeat engine disabled via DISABLE_BACKGROUND_ENGINE=1")
        return
    from infrastructure import tasks as task_registry

    _db = db
    _ws = ws_manager
    _started = True
    task_registry.spawn(HEARTBEAT_TASK, _heartbeat_loop())
    task_registry.spawn(PRICE_STREAM_TASK, _price_stream_loop())
    logger.info("AI heartbeat engine started (%d tasks + live price stream)", len(TASKS))


async def stop_engine():
    """Cancel both loops and return the engine to a startable state.

    Called from the application's shutdown handler. Resetting `_started` matters
    beyond tidiness: it is what makes `start_engine` work again after a stop,
    which is the property the test suite and any future lifespan-based restart
    rely on.
    """
    global _db, _ws, _started
    from infrastructure import tasks as task_registry

    if not _started:
        return
    await task_registry.registry.cancel(HEARTBEAT_TASK)
    await task_registry.registry.cancel(PRICE_STREAM_TASK)
    _started = False
    _db = None
    _ws = None
    logger.info("AI heartbeat engine stopped")
