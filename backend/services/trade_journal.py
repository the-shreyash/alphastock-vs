"""Trade journal with AI performance review."""
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


async def get_trade_journal(db, user_id, days=30):
    """Get trade journal entries for a user."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    trades = await db.trades.find({
        "user_id": user_id,
        "status": {"$ne": "OPEN"},
        "exit_time": {"$gte": cutoff},
    }).sort("exit_time", -1).to_list(200)

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


async def get_performance_stats(db, user_id, days=7):
    """Get weekly performance statistics."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    all_trades = await db.trades.find({"user_id": user_id}).to_list(500)
    closed = [t for t in all_trades if t.get("status") != "OPEN"]
    recent = [t for t in closed if (t.get("exit_time") or "") >= cutoff]

    def calc_stats(trades_list):
        if not trades_list:
            return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0, "total_pnl": 0, "avg_pnl": 0, "best": 0, "worst": 0}
        pnls = [t.get("pnl") if t.get("pnl") is not None else 0 for t in trades_list]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        total = sum(pnls)
        return {
            "total": len(trades_list),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(trades_list) * 100, 1) if trades_list else 0,
            "total_pnl": round(total, 2),
            "avg_pnl": round(total / len(trades_list), 2) if trades_list else 0,
            "best": round(max(pnls), 2) if pnls else 0,
            "worst": round(min(pnls), 2) if pnls else 0,
        }

    return {
        "period": f"Last {days} days",
        "recent": calc_stats(recent),
        "all_time": calc_stats(closed),
        "open_positions": len([t for t in all_trades if t.get("status") == "OPEN"]),
    }


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
