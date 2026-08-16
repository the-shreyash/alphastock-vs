"""Trade journal with AI performance review.

SCOPE (PH3.8). Everything in this module reports on REAL-MONEY trading.
Paper trades share the `trades` collection (`is_paper: True`) and were being
counted here, so a virtual-money win inflated the win rate and the total P&L a
trader sees on the Trade Journal page. They are now excluded from every live
figure and reported separately, under an explicit `paper` key, so nothing is
hidden — only labelled. Windows are IST days via `analytics.periods`; see
`docs/architecture/ANALYTICS.md` §5.
"""
import logging
from datetime import datetime, timezone

from analytics import periods, queries

logger = logging.getLogger(__name__)


async def get_trade_journal(db, user_id, days=30):
    """Get real-money trade journal entries for a user."""
    trades = await db.trades.find(
        queries.closed_in_window(periods.window_of_days(days), user_id)
    ).sort("exit_time", -1).to_list(200)

    journal = []
    for t in trades:
        journal.append({
            "id": str(t["_id"]),
            "symbol": t["symbol"],
            "stock_name": t.get("stock_name", t["symbol"]),
            "type": t.get("type", "BUY"),
            "entry_price": t["entry_price"],
            "exit_price": t.get("exit_price"),
            "quantity": t["quantity"],
            "pnl": t.get("pnl", 0),
            "pnl_percent": t.get("pnl_percent", 0),
            "status": t["status"],
            "entry_time": t.get("entry_time", ""),
            "exit_time": t.get("exit_time", ""),
            "notes": t.get("notes", ""),
            "ai_confidence": t.get("ai_confidence"),
        })
    return journal


def calc_stats(trades_list):
    """Summary statistics over a list of closed trades.

    Win / loss / breakeven are three outcomes, not two. A trade that closed at
    exactly zero is neither won nor lost, and counting it as a loss (the
    pre-PH3.8 behaviour, inherited from `pnl > 0 else loss`) understated the win
    rate — most visibly after a paper-capital reset, which force-closes open
    positions with `pnl: 0`. `win_rate` keeps its denominator as the FULL trade
    count so the three outcomes still sum to 100%.
    """
    if not trades_list:
        return {"total": 0, "wins": 0, "losses": 0, "breakeven": 0, "win_rate": 0,
                "total_pnl": 0, "avg_pnl": 0, "best": 0, "worst": 0}
    pnls = [t.get("pnl") if t.get("pnl") is not None else 0 for t in trades_list]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    breakeven = [p for p in pnls if p == 0]
    total = sum(pnls)
    return {
        "total": len(trades_list),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(breakeven),
        "win_rate": round(len(wins) / len(trades_list) * 100, 1),
        "total_pnl": round(total, 2),
        "avg_pnl": round(total / len(trades_list), 2),
        "best": round(max(pnls), 2),
        "worst": round(min(pnls), 2),
    }


async def get_performance_stats(db, user_id, days=7):
    """Real-money performance statistics, with paper trading reported apart.

    PH3.8. Three things changed and each was a wrong number, not a style point:

    * **Paper trades are excluded from `recent` and `all_time`.** They were
      included, so a ₹9,000 virtual gain and a ₹500 real loss reported as
      ₹8,500 of profit at a 50% win rate. The paper figures are still returned,
      under `paper`, because removing them would have hidden information rather
      than corrected it.
    * **The window is IST days, half-open**, instead of "the ISO string of the
      instant `days × 24h` ago", which put the boundary at whatever time of day
      the request happened to arrive and made the same query return different
      trade sets an hour apart.
    * **No 500-document cap.** `find(...).to_list(500)` applied no sort, so an
      all-time figure for an active trader summed an arbitrary 500 rows.
    """
    window = periods.window_of_days(days)

    live_closed = await db.trades.find(queries.closed(queries.live_trades(user_id))).to_list(None)
    recent = [t for t in live_closed if window.contains(t.get("exit_time"))]
    paper_closed = await db.trades.find(queries.closed(queries.paper_trades(user_id))).to_list(None)
    open_count = await db.trades.count_documents(
        queries.open_positions(queries.live_trades(user_id)))

    return {
        "period": window.label,
        "window": window.as_dict(),
        "scope": "live",
        "basis": "gross",  # no brokerage/STT/GST is recorded anywhere — ANALYTICS.md §6
        "recent": calc_stats(recent),
        "all_time": calc_stats(live_closed),
        "open_positions": open_count,
        "paper": {
            "all_time": calc_stats(paper_closed),
            "note": "Virtual capital. Excluded from every figure above.",
        },
    }


async def get_setup_success_rates(db, user_id):
    """Historical success rate grouped by trade setup type.

    Returns per-setup stats (total_trades, winning_trades, win_rate %,
    avg_pnl_percent, best_trade_pnl, worst_trade_pnl). Returns an empty
    list when the user has no closed trades or no setup_type tagging yet —
    the frontend should show an appropriate empty state.
    """
    # Real-money only, and uncapped (PH3.8): a per-setup win rate computed over
    # an arbitrary 500 of a trader's rows is worse than no win rate, because it
    # is acted on.
    closed = await db.trades.find(queries.closed(queries.live_trades(user_id))).to_list(None)

    # Group closed trades by setup_type (skip trades without a setup tag).
    groups = {}
    for t in closed:
        setup = t.get("setup_type")
        if not setup:
            continue
        groups.setdefault(setup, []).append(t)

    if not groups:
        return {
            "setups": [],
            "is_demo": False,
            "empty_reason": "Close some trades and tag them with a setup type to see your performance history.",
        }

    setups = []
    for setup_type, trades_list in groups.items():
        pnls = [t.get("pnl") if t.get("pnl") is not None else 0 for t in trades_list]
        pnl_pcts = [t.get("pnl_percent") if t.get("pnl_percent") is not None else 0 for t in trades_list]
        wins = [p for p in pnls if p > 0]
        total = len(trades_list)
        setups.append({
            "setup_type": setup_type,
            "total_trades": total,
            "winning_trades": len(wins),
            "win_rate": round(len(wins) / total * 100, 1) if total else 0,
            "avg_pnl_percent": round(sum(pnl_pcts) / total, 2) if total else 0,
            "best_trade_pnl": round(max(pnls), 2) if pnls else 0,
            "worst_trade_pnl": round(min(pnls), 2) if pnls else 0,
        })

    # Most-traded setups first.
    setups.sort(key=lambda s: s["total_trades"], reverse=True)
    return {"setups": setups, "is_demo": False}


async def generate_weekly_review(db, user_id, ai_func=None):
    """Generate AI weekly performance review."""
    stats = await get_performance_stats(db, user_id, days=7)
    journal = await get_trade_journal(db, user_id, days=7)

    if not ai_func:
        return {
            "stats": stats,
            "journal": journal,
            "review": f"This week: {stats['recent']['total']} trades, P&L: INR {stats['recent']['total_pnl']}, Win rate: {stats['recent']['win_rate']}%",
        }

    # Build AI prompt
    trade_summary = "\n".join([
        f"- {t['symbol']}: {t['type']} @ INR {t['entry_price']} -> INR {t.get('exit_price', '?')}, P&L: INR {t['pnl']}, Status: {t['status']}"
        for t in journal[:20]
    ])

    prompt = f"""Review this trader's weekly performance:

Stats: {stats['recent']['total']} trades, Win rate: {stats['recent']['win_rate']}%, Total P&L: INR {stats['recent']['total_pnl']}, Best: INR {stats['recent']['best']}, Worst: INR {stats['recent']['worst']}

Recent trades:
{trade_summary or 'No closed trades this week'}

All-time: {stats['all_time']['total']} trades, Win rate: {stats['all_time']['win_rate']}%

Provide:
1. Performance summary (2-3 sentences)
2. What went well
3. Areas to improve
4. Risk management assessment
5. One specific actionable tip for next week

Be encouraging but honest. Use INR currency."""

    try:
        review_text = await ai_func(prompt)
    except Exception as e:
        review_text = f"Unable to generate AI review: {str(e)}"

    return {
        "stats": stats,
        "journal": journal,
        "review": review_text,
    }


# ─────────────────────────────────────────────────────────────
# AI TRADE COACHING
# ─────────────────────────────────────────────────────────────

def _grade_trade(pnl_pct: float, hit_target: bool, hit_sl: bool) -> tuple:
    """Return (grade, reason) based on trade outcome."""
    if hit_target and pnl_pct >= 3:
        return "A", "Hit target with strong returns — textbook execution"
    if hit_target and pnl_pct > 0:
        return "B", "Hit target — solid discipline"
    if pnl_pct > 0 and not hit_sl:
        return "B", "Profitable exit — good read on the market"
    if pnl_pct > -1:
        return "C", "Small loss — stop loss worked, manage position sizing next time"
    if hit_sl:
        return "C", "Stop loss hit — risk was controlled, review the setup"
    return "D", "Large loss — review entry criteria and risk management"


async def generate_trade_coaching(trade: dict, ai_func=None) -> dict:
    """
    Generate a personalized AI coaching lesson for a closed trade.
    Returns a coaching dict; caller should cache it in the DB.
    """
    symbol = trade.get("symbol", "?")
    trade_type = trade.get("type", "BUY")
    entry = trade.get("entry_price", 0)
    exit_p = trade.get("exit_price", entry)
    stop_loss = trade.get("stop_loss", 0)
    target = trade.get("target1", 0)
    pnl = trade.get("pnl", 0) or 0
    pnl_pct = trade.get("pnl_percent", 0) or 0
    status = trade.get("status", "CLOSED")
    setup = trade.get("setup_type", "Not specified")
    is_paper = trade.get("is_paper", False)

    hit_target = status in ("TARGET_HIT", "PROFIT") or (pnl_pct or 0) > 0
    hit_sl = status in ("SL_HIT", "STOP_LOSS_HIT") or (pnl_pct or 0) < -1.5

    grade, grade_reason = _grade_trade(pnl_pct, hit_target, hit_sl)
    paper_note = " [Paper Trade]" if is_paper else ""

    prompt = f"""Analyze this completed{paper_note} NSE trade and provide concise coaching feedback:

Trade Details:
- Symbol: {symbol} | Type: {trade_type}
- Entry: ₹{entry} | Exit: ₹{exit_p}
- Stop Loss: ₹{stop_loss} | Target: ₹{target}
- P&L: {pnl_pct:+.2f}% (₹{pnl:+.0f})
- Status: {status} | Setup: {setup}

Provide structured coaching in exactly this format (keep each section to 1-2 sentences):

LESSON_TITLE: [a short 5-7 word lesson title]
WHAT_WENT_RIGHT: [what the trader did well]
WHAT_WENT_WRONG: [what could have been better, or 'Nothing major — well executed' if grade A/B]
NEXT_TIME: [one specific actionable improvement]
COACHING: [2-3 sentence overall coaching summary — educational, encouraging, honest]

Be positive even for losing trades. Focus on process, not just outcome."""

    coaching_text = ""
    lesson_title = f"{'Profitable' if pnl_pct >= 0 else 'Learning'} trade in {symbol}"
    what_went_right = "Risk was managed with a stop loss in place."
    what_went_wrong = "Review entry timing and setup confirmation signals."
    next_time = "Confirm setup with at least 2 technical indicators before entry."

    if ai_func:
        try:
            raw = await ai_func(prompt)
            # Parse structured sections
            lines = raw.split("\n")
            for line in lines:
                if line.startswith("LESSON_TITLE:"):
                    lesson_title = line.replace("LESSON_TITLE:", "").strip()
                elif line.startswith("WHAT_WENT_RIGHT:"):
                    what_went_right = line.replace("WHAT_WENT_RIGHT:", "").strip()
                elif line.startswith("WHAT_WENT_WRONG:"):
                    what_went_wrong = line.replace("WHAT_WENT_WRONG:", "").strip()
                elif line.startswith("NEXT_TIME:"):
                    next_time = line.replace("NEXT_TIME:", "").strip()
                elif line.startswith("COACHING:"):
                    coaching_text = line.replace("COACHING:", "").strip()
            if not coaching_text:
                coaching_text = raw  # fallback: use full text
        except Exception as e:
            logger.error(f"AI coaching generation failed: {e}")
            coaching_text = (
                f"Trade analysis: {symbol} {trade_type} at ₹{entry}, exited at ₹{exit_p} "
                f"with {pnl_pct:+.2f}% P&L. "
                f"{'The trade hit its target — great discipline.' if hit_target else 'The stop loss contained the loss — that is risk management working correctly.'} "
                f"Continue refining your entry signals."
            )
    else:
        coaching_text = (
            f"{'Well done! ' if pnl_pct >= 0 else ''}{symbol} {trade_type} trade: "
            f"entered at ₹{entry}, exited at ₹{exit_p} ({pnl_pct:+.2f}%). "
            f"Grade {grade}: {grade_reason}."
        )

    return {
        "trade_id": str(trade.get("_id", "")),
        "symbol": symbol,
        "coaching_text": coaching_text,
        "lesson_title": lesson_title,
        "grade": grade,
        "grade_reason": grade_reason,
        "what_went_right": what_went_right,
        "what_went_wrong": what_went_wrong,
        "next_time": next_time,
        "pnl_pct": round(pnl_pct, 2),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
