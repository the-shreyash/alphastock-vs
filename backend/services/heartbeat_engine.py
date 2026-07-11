"""AI Heartbeat Engine — the platform's "always-on" brain.

This engine makes the AI genuinely *alive*: a background loop cycles through a
registry of REAL tasks (live index/US-market fetches, news scans, breakout &
volume scans, open-trade monitoring, portfolio health, top-pick generation,
sentiment, sector rotation, macro/earnings news scans). Each task logs
`log_activity(action, category, "running")` when it starts and a truthful
result summary with status `"done"` (or `"warning"` on failure) when it
finishes — so the AI Activity feed is an honest trace of work actually done,
never canned strings.

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


async def _broadcast(message):
    """Deliver a message to all connected WebSocket clients."""
    if not _ws or not _ws.active:
        return
    try:
        await _ws.broadcast(message)
    except Exception as e:
        logger.error(f"Heartbeat broadcast error: {e}")


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
    from services.activity_logger import log_activity
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
    from services.activity_logger import log_activity
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
    from services.activity_logger import log_activity
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
    from services.activity_logger import log_activity
    from services.news_service import fetch_news
    log_activity("Scanning News", "news", "running")
    try:
        news = await fetch_news()
        if not news:
            log_activity("No fresh market headlines available", "news", "warning")
            return
        log_activity(f"Scanned {len(news)} market headlines", "news", "done")
        # Stream the latest headlines live to the News/Dashboard surfaces.
        await _publish("news.received", {"articles": news[:10], "count": len(news)})
    except Exception as e:
        logger.error(f"task_scan_news error: {e}")
        log_activity("Scanning News failed", "news", "warning")


async def task_find_breakouts():
    from services.activity_logger import log_activity
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
    from services.activity_logger import log_activity
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
    from services.activity_logger import log_activity
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
    from services.activity_logger import log_activity
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
    from services.activity_logger import log_activity
    log_activity("Monitoring Open Trades", "monitor", "running")
    try:
        open_trades = await _db.trades.find({"status": "OPEN"}).to_list(200)
        if not open_trades:
            log_activity("No open trades to monitor", "monitor", "done")
            return

        symbols = list({t["symbol"] for t in open_trades})
        quotes = await _price_map(symbols)
        alerts_sent = 0

        for trade in open_trades:
            quote = quotes.get(trade["symbol"])
            if not quote:
                continue
            price = quote["price"]
            entry = trade["entry_price"]
            qty = trade["quantity"]
            pnl = round((price - entry) * qty, 2)
            pnl_pct = round(((price - entry) / entry) * 100, 2) if entry else 0
            user_id = trade["user_id"]

            await _send_user(user_id, {
                "type": "trade_update",
                "user_id": user_id,
                "data": {
                    "trade_id": str(trade["_id"]),
                    "symbol": trade["symbol"],
                    "current_price": price,
                    "entry_price": entry,
                    "unrealized_pnl": pnl,
                    "unrealized_pnl_pct": pnl_pct,
                },
                "timestamp": _now_iso(),
            })

            # Target / stop-loss crossing → notification + alert push (deduped)
            crossed = None
            sl = trade.get("stop_loss")
            t1 = trade.get("target1")
            if sl and price <= sl:
                crossed = ("STOP_LOSS_HIT", "critical",
                           f"{trade['symbol']} hit stop-loss ₹{sl}. Current ₹{price}, P&L ₹{pnl}.")
            elif t1 and price >= t1:
                crossed = ("TARGET_HIT", "positive",
                           f"{trade['symbol']} hit target ₹{t1}! Current ₹{price}, profit ₹{pnl}.")

            if crossed and not await _recent_alert_exists(user_id, crossed[0], trade["symbol"]):
                ntype, severity, message = crossed
                await _db.notifications.insert_one({
                    "user_id": user_id,
                    "type": ntype,
                    "symbol": trade["symbol"],
                    "title": f"AI Alert: {trade['symbol']}",
                    "message": message,
                    "severity": severity,
                    "read": False,
                    "created_at": _now_iso(),
                })
                await _send_user(user_id, {
                    "type": "alert",
                    "data": {
                        "type": ntype,
                        "severity": severity,
                        "symbol": trade["symbol"],
                        "message": message,
                        "trade_id": str(trade["_id"]),
                        "price": price,
                        "pnl": pnl,
                    },
                    "timestamp": _now_iso(),
                })
                alerts_sent += 1

        summary = f"Monitored {len(open_trades)} open position(s)"
        if alerts_sent:
            summary += f" — {alerts_sent} alert(s) fired"
        log_activity(summary, "monitor", "warning" if alerts_sent else "done")
    except Exception as e:
        logger.error(f"task_monitor_trades error: {e}")
        log_activity("Monitoring Open Trades failed", "monitor", "warning")


async def task_monitor_portfolio():
    from services.activity_logger import log_activity
    log_activity("Monitoring Portfolio", "monitor", "running")
    try:
        open_trades = await _db.trades.find({"status": "OPEN"}).to_list(200)
        if not open_trades:
            log_activity("No portfolios with open positions", "monitor", "done")
            return

        symbols = list({t["symbol"] for t in open_trades})
        quotes = await _price_map(symbols)

        by_user = {}
        for trade in open_trades:
            quote = quotes.get(trade["symbol"])
            if not quote:
                continue
            pnl = round((quote["price"] - trade["entry_price"]) * trade["quantity"], 2)
            agg = by_user.setdefault(trade["user_id"], {"pnl": 0.0, "positions": 0})
            agg["pnl"] += pnl
            agg["positions"] += 1

        for user_id, agg in by_user.items():
            await _send_user(user_id, {
                "type": "portfolio_update",
                "user_id": user_id,
                "data": {
                    "total_unrealized_pnl": round(agg["pnl"], 2),
                    "total_pnl": round(agg["pnl"], 2),
                    "open_positions": agg["positions"],
                },
                "timestamp": _now_iso(),
            })

        log_activity(
            f"Portfolio health checked for {len(by_user)} trader(s)", "monitor", "done"
        )
    except Exception as e:
        logger.error(f"task_monitor_portfolio error: {e}")
        log_activity("Monitoring Portfolio failed", "monitor", "warning")


async def task_top_picks():
    from services.activity_logger import log_activity
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
    from services.activity_logger import log_activity
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
    from services.activity_logger import log_activity
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
    from services.activity_logger import log_activity
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
    from services.activity_logger import log_activity
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


async def task_morning_report():
    from services.activity_logger import log_activity
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


async def _collect_prices():
    """Build {SYMBOL: {price, change_pct}} for indices + watchlist + open trades."""
    from services.real_market import fetch_real_market_overview, fetch_all_universe_quotes, fetch_yahoo_quote
    data = {}

    # Main indices
    overview = await fetch_real_market_overview()
    if overview:
        for key, sym in (("nifty", "NIFTY"), ("bank_nifty", "BANKNIFTY"), ("sensex", "SENSEX")):
            val = overview.get(key, {})
            if val.get("value"):
                data[sym] = {"price": val["value"], "change_pct": val.get("change_pct", 0)}

    # Symbols in any watchlist or any open trade
    symbols = set()
    try:
        symbols.update(await _db.watchlist.distinct("symbol"))
        symbols.update(await _db.trades.distinct("symbol", {"status": "OPEN"}))
    except Exception as e:
        logger.error(f"_collect_prices symbol gather error: {e}")

    if symbols:
        quotes = await fetch_all_universe_quotes()
        qmap = {q["symbol"]: q for q in (quotes or []) if q.get("symbol")}
        missing = []
        for sym in symbols:
            q = qmap.get(sym)
            if q and q.get("price") is not None:
                data[sym] = {"price": q["price"], "change_pct": q.get("change_pct", 0)}
            else:
                missing.append(sym)
        # Symbols not in the universe cache (rare custom trade symbols)
        if missing:
            extra = await _price_map(missing)
            for sym, q in extra.items():
                data[sym] = {"price": q["price"], "change_pct": q.get("change_pct", 0)}

    return data


async def _price_stream_loop():
    """Broadcast live prices to all clients every PRICE_STREAM_INTERVAL seconds."""
    logger.info("AI price stream loop running")
    while True:
        try:
            if _ws and _ws.active:
                prices = await _collect_prices()
                if prices:
                    await _broadcast({
                        "type": "prices",
                        "data": prices,
                        "timestamp": _now_iso(),
                    })
        except Exception as e:
            logger.error(f"Price stream loop error: {e}")
        await asyncio.sleep(PRICE_STREAM_INTERVAL)


def start_engine(db, ws_manager):
    """Start the heartbeat + price-stream background loops.

    Idempotent. No-op when DISABLE_BACKGROUND_ENGINE=1 (used by the test suite,
    mirroring how the scheduler is kept out of the way during pytest).
    """
    global _db, _ws, _started
    if _started:
        return
    if os.environ.get("DISABLE_BACKGROUND_ENGINE") == "1":
        logger.info("AI heartbeat engine disabled via DISABLE_BACKGROUND_ENGINE=1")
        return
    _db = db
    _ws = ws_manager
    _started = True
    asyncio.create_task(_heartbeat_loop())
    asyncio.create_task(_price_stream_loop())
    logger.info("AI heartbeat engine started (%d tasks + live price stream)", len(TASKS))
