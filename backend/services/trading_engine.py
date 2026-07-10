"""Trading Engine — Sprint 9 (BROKER_INTEGRATION.md, .claude/TASK.md).

Completes the trade lifecycle on top of the Broker Engine:

    Risk Manager  →  Entry  →  Monitor (trailing stop, multi-target)  →  Exit

Responsibilities:
  • Pre-trade risk validation against the user's own risk settings
    (max_trades_per_day, max_daily_loss, capital, risk_level).
  • Trailing-stop management: tracks the best price seen and ratchets the
    stop toward it — the stop only ever tightens, never loosens.
  • Multi-target evaluation (target1..3) with partial profit booking and
    an immutable per-trade event timeline.
  • Exit execution: manual, partial, or — with explicit per-trade consent
    (`auto_exit`) — a live market order through the Broker Engine.

Compliance: live exit orders are ONLY placed when the user opted in on that
specific trade at creation time (auto_exit=True) AND the trade is broker
linked. Everything else is alert-only (see BROKER_INTEGRATION.md
"Compliance": explicit user consent before placing live orders).

Pure business logic lives here (no FastAPI imports) so it stays hermetically
testable with the FakeDB double. NOTE: event/target arrays are persisted via
$set of the whole list (read-modify-write) — the monitor cycle is the single
writer, and FakeDB only supports $set.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Risk-per-trade guideline as % of capital, keyed by the user's risk level.
RISK_PCT_BY_LEVEL = {"conservative": 1.0, "moderate": 2.0, "aggressive": 3.0}

TRAIL_TYPES = ("percent", "points")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _is_short(trade: dict) -> bool:
    return (trade.get("type") or "BUY").upper() == "SELL"


def _qty_open(trade: dict) -> int:
    q = trade.get("quantity_open")
    return int(q if q is not None else trade.get("quantity") or 0)


def _per_share_pnl(trade: dict, price: float) -> float:
    entry = trade.get("entry_price") or 0
    return (entry - price) if _is_short(trade) else (price - entry)


def make_event(event_type: str, message: str, price: float = None) -> dict:
    event = {"type": event_type, "message": message, "at": _now_iso()}
    if price is not None:
        event["price"] = price
    return event


# ─── Risk Manager ────────────────────────────────────────────────────────────

def compute_trade_metrics(user: dict, trade: dict) -> dict:
    """Risk/reward math for one prospective trade against the user's capital."""
    entry = float(trade.get("entry_price") or 0)
    sl = float(trade.get("stop_loss") or 0)
    target = float(trade.get("target1") or 0)
    qty = int(trade.get("quantity") or 0)
    short = _is_short(trade)

    per_share_risk = (sl - entry) if short else (entry - sl)
    per_share_reward = (entry - target) if short else (target - entry)
    capital = float(user.get("capital") or 0)
    risk_pct_limit = RISK_PCT_BY_LEVEL.get(
        (user.get("risk_level") or "moderate").lower(), 2.0)
    risk_budget = capital * risk_pct_limit / 100

    risk_amount = round(per_share_risk * qty, 2)
    reward_amount = round(per_share_reward * qty, 2)
    return {
        "risk_amount": risk_amount,
        "reward_amount": reward_amount,
        "risk_reward": round(per_share_reward / per_share_risk, 2) if per_share_risk > 0 else 0,
        "risk_pct_of_capital": round(risk_amount / capital * 100, 2) if capital else 0,
        "position_value": round(entry * qty, 2),
        "risk_pct_limit": risk_pct_limit,
        "suggested_quantity": int(risk_budget // per_share_risk) if per_share_risk > 0 else 0,
    }


def validate_trade(user: dict, trade: dict, trades_today: int,
                   today_realized_pnl: float) -> dict:
    """Pre-trade Risk Manager. Violations block the order; warnings educate.

    `trades_today` counts trades ENTERED today; `today_realized_pnl` is the
    realized P&L of trades closed today (both computed by the caller from db).
    """
    violations, warnings = [], []
    entry = float(trade.get("entry_price") or 0)
    sl = float(trade.get("stop_loss") or 0)
    short = _is_short(trade)
    side = "SELL" if short else "BUY"

    # Stop-loss must protect: below entry for a long, above for a short.
    if entry <= 0:
        violations.append("Entry price must be greater than zero.")
    elif (not short and sl >= entry) or (short and sl <= entry):
        violations.append(
            f"Stop loss must be {'below' if not short else 'above'} the entry "
            f"price for a {side} trade (entry ₹{entry}, SL ₹{sl}).")

    # Targets must be on the profitable side, in order.
    targets = [trade.get("target1"), trade.get("target2"), trade.get("target3")]
    prev = entry
    for level, target in enumerate(targets, start=1):
        if target is None:
            continue
        target = float(target)
        wrong_side = target <= entry if not short else target >= entry
        out_of_order = target <= prev if not short else target >= prev
        if wrong_side:
            violations.append(
                f"Target {level} (₹{target}) must be {'above' if not short else 'below'} "
                f"the entry price for a {side} trade.")
        elif out_of_order:
            violations.append(
                f"Target {level} (₹{target}) must be beyond target {level - 1} (₹{prev}).")
        prev = target

    # Daily discipline limits from the user's own settings.
    max_trades = int(user.get("max_trades_per_day") or 0)
    if max_trades and trades_today >= max_trades:
        violations.append(
            f"Daily trade limit reached ({trades_today}/{max_trades}). "
            "Overtrading is the #1 account killer — review today's trades instead.")

    max_daily_loss = float(user.get("max_daily_loss") or 0)
    if max_daily_loss and today_realized_pnl <= -max_daily_loss:
        violations.append(
            f"Daily loss limit hit (₹{abs(round(today_realized_pnl, 2))} lost of "
            f"₹{max_daily_loss} allowed). Trading is paused for today — protect your capital.")

    metrics = compute_trade_metrics(user, trade)

    remaining_loss_budget = (max_daily_loss + min(today_realized_pnl, 0)
                             if max_daily_loss else None)
    if (remaining_loss_budget is not None and metrics["risk_amount"] > 0
            and metrics["risk_amount"] > remaining_loss_budget and not violations):
        violations.append(
            f"This trade risks ₹{metrics['risk_amount']} but only "
            f"₹{round(remaining_loss_budget, 2)} of today's loss budget remains. "
            "Reduce quantity or tighten the stop.")

    # Educational warnings (do not block; surfaced in the UI risk panel).
    if metrics["risk_pct_of_capital"] > metrics["risk_pct_limit"]:
        warnings.append(
            f"You are risking {metrics['risk_pct_of_capital']}% of capital — above the "
            f"{metrics['risk_pct_limit']}% guideline for a {user.get('risk_level', 'moderate')} "
            f"profile. Suggested quantity: {metrics['suggested_quantity']}.")
    if 0 < metrics["risk_reward"] < 1:
        warnings.append(
            f"Risk:reward is 1:{metrics['risk_reward']} — you risk more than you stand "
            "to gain at target 1. Look for at least 1:1.5.")
    capital = float(user.get("capital") or 0)
    if capital and metrics["position_value"] > capital:
        warnings.append(
            f"Position value ₹{metrics['position_value']} exceeds your capital "
            f"₹{capital} — this requires leverage/margin.")

    return {"approved": not violations, "violations": violations,
            "warnings": warnings, "metrics": metrics}


# ─── Trailing stop ───────────────────────────────────────────────────────────

def update_trailing_stop(trade: dict, price: float) -> dict:
    """Ratchet the trailing stop toward the best price seen.

    Returns {} when nothing changed, else the fields to persist
    ({best_price} and/or {stop_loss}). The stop only ever tightens.
    """
    config = trade.get("trailing_stop") or {}
    short = _is_short(trade)
    changes = {}

    best = trade.get("best_price")
    if best is None or (price > best if not short else price < best):
        best = price
        changes["best_price"] = price

    if not config.get("enabled") or not config.get("value"):
        return changes
    value = float(config["value"])
    if config.get("type") not in TRAIL_TYPES:
        return changes

    if config.get("type") == "percent":
        distance = best * value / 100
    else:
        distance = value
    candidate = round(best - distance if not short else best + distance, 2)

    current_sl = trade.get("stop_loss") or 0
    tightened = candidate > current_sl if not short else candidate < current_sl
    if tightened:
        changes["stop_loss"] = candidate
    return changes


# ─── Target / stop evaluation ────────────────────────────────────────────────

def target_exit_quantity(trade: dict, level: int) -> int:
    """Quantity to book when target `level` is hit.

    Split evenly across the configured targets against the ORIGINAL quantity;
    the last configured target (or the only one) closes whatever remains.
    """
    levels = [n for n in (1, 2, 3) if trade.get(f"target{n}") is not None]
    qty_open = _qty_open(trade)
    if not levels or level == levels[-1]:
        return qty_open
    per_target = int((trade.get("quantity") or 0) // len(levels))
    return max(1, min(per_target, qty_open)) if qty_open else 0


def evaluate_trade(trade: dict, price: float) -> list:
    """Return the lifecycle actions the current price triggers for this trade.

    Actions: {"action": "SL_HIT", "quantity"} |
             {"action": "TARGET_HIT", "level", "target_price", "quantity"}
    Each target fires once (tracked via trade["targets_hit"]). SL wins when
    both would trigger (a gapping candle) — capital protection first.
    """
    if trade.get("status") != "OPEN" or _qty_open(trade) <= 0:
        return []
    short = _is_short(trade)
    sl = trade.get("stop_loss") or 0

    if sl and (price <= sl if not short else price >= sl):
        return [{"action": "SL_HIT", "quantity": _qty_open(trade)}]

    actions = []
    hit_levels = {h.get("level") for h in (trade.get("targets_hit") or [])}
    for level in (1, 2, 3):
        target = trade.get(f"target{level}")
        if target is None or level in hit_levels:
            continue
        if price >= target if not short else price <= target:
            qty = target_exit_quantity(trade, level)
            if qty > 0:
                actions.append({"action": "TARGET_HIT", "level": level,
                                "target_price": float(target), "quantity": qty})
                # Later levels in the same tick book against what's left.
                trade = {**trade, "quantity_open": _qty_open(trade) - qty,
                         "targets_hit": (trade.get("targets_hit") or []) + [{"level": level}]}
    return actions


def apply_partial_exit(trade: dict, price: float, quantity: int, reason: str) -> dict:
    """Book `quantity` at `price`; returns the $set update dict.

    Closes the trade (status from `reason`) when nothing remains open, with
    pnl = realized partials + this exit; else keeps it OPEN with updated
    quantity_open / realized_pnl.
    """
    qty_open = _qty_open(trade)
    quantity = max(0, min(int(quantity), qty_open))
    if quantity == 0:
        return {}
    booked = round(_per_share_pnl(trade, price) * quantity, 2)
    realized = round((trade.get("realized_pnl") or 0) + booked, 2)
    remaining = qty_open - quantity

    update = {"quantity_open": remaining, "realized_pnl": realized}
    if remaining == 0:
        invested = (trade.get("entry_price") or 0) * (trade.get("quantity") or 0)
        update.update({
            "status": reason,
            "exit_price": price,
            "exit_time": _now_iso(),
            "pnl": realized,
            "pnl_percent": round(realized / invested * 100, 2) if invested else 0,
        })
    return update


# ─── Monitor cycle ───────────────────────────────────────────────────────────

async def _notify(db, trade: dict, title: str, message: str, severity: str, ntype: str):
    try:
        await db.notifications.insert_one({
            "user_id": trade["user_id"], "type": ntype, "symbol": trade.get("symbol"),
            "title": title, "message": message, "severity": severity,
            "read": False, "created_at": _now_iso(),
        })
    except Exception as e:
        logger.error(f"Trading engine notification failed: {e}")


async def _broker_exit(broker_engine, trade: dict, quantity: int, reason: str):
    """Place the live market exit order for an auto_exit trade. Returns the
    broker order result or None on failure (failure never blocks bookkeeping —
    the user is alerted either way)."""
    side = "BUY" if _is_short(trade) else "SELL"
    try:
        return await broker_engine.place_order(trade["user_id"], trade["broker"], {
            "symbol": trade["symbol"], "exchange": trade.get("exchange", "NSE"),
            "transaction_type": side, "quantity": quantity,
            "order_type": "MARKET", "product": trade.get("product") or "CNC",
            "tag": f"engine-{reason.lower()[:12]}",
        })
    except Exception as e:
        logger.error(f"Auto-exit order failed for {trade.get('symbol')}: {e}")
        return None


async def run_cycle(db, quotes: dict, broker_engine=None, ws_push=None) -> dict:
    """One monitoring pass over every OPEN (non-paper) trade.

    `quotes` maps SYMBOL -> {"price": ...} (prefetched by the scheduler job so
    this cycle never triggers its own market-data fan-out).
    """
    trades = await db.trades.find({"status": "OPEN", "is_paper": {"$ne": True}}).to_list(200)
    stats = {"checked": 0, "trailed": 0, "targets_hit": 0, "sl_exits": 0, "auto_orders": 0}

    for trade in trades:
        quote = quotes.get((trade.get("symbol") or "").upper())
        if not quote or not quote.get("price"):
            continue
        price = float(quote["price"])
        stats["checked"] += 1
        events = list(trade.get("events") or [])
        update = {}

        # 1. Trailing stop ratchet (always safe — never places an order).
        trail = update_trailing_stop(trade, price)
        if "stop_loss" in trail:
            stats["trailed"] += 1
            events.append(make_event(
                "TRAILING_SL",
                f"Trailing stop moved ₹{trade.get('stop_loss')} → ₹{trail['stop_loss']} "
                f"(best price ₹{trail.get('best_price', trade.get('best_price'))}).",
                price))
        if trail:
            update.update(trail)
            trade = {**trade, **trail}

        # 2. Target / SL lifecycle.
        auto = bool(trade.get("auto_exit")) and bool(trade.get("broker"))
        for action in evaluate_trade(trade, price):
            if action["action"] == "TARGET_HIT":
                level, qty = action["level"], action["quantity"]
                stats["targets_hit"] += 1
                hits = list(trade.get("targets_hit") or [])
                hit = {"level": level, "price": price,
                       "target_price": action["target_price"], "at": _now_iso()}
                message = (f"{trade['symbol']} hit target {level} (₹{action['target_price']}) "
                           f"at ₹{price}.")
                if auto:
                    order = await _broker_exit(broker_engine, trade, qty, f"T{level}")
                    if order:
                        stats["auto_orders"] += 1
                        hit["quantity_booked"] = qty
                        hit["order_id"] = order.get("order_id")
                        message += f" Auto-exit: booked {qty} at market (order {order.get('order_id')})."
                        update.update(apply_partial_exit(trade, price, qty, "TARGET_HIT"))
                        trade = {**trade, **update}
                    else:
                        message += " Auto-exit order FAILED — please exit manually."
                else:
                    message += f" Consider booking {qty} shares."
                hits.append(hit)
                update["targets_hit"] = hits
                trade = {**trade, "targets_hit": hits}
                events.append(make_event("TARGET_HIT", message, price))
                await _notify(db, trade, f"Target {level} hit: {trade['symbol']}",
                              message, "positive", "TARGET_HIT")

            elif action["action"] == "SL_HIT":
                stats["sl_exits"] += 1
                message = (f"{trade['symbol']} breached stop loss ₹{trade.get('stop_loss')} "
                           f"at ₹{price}.")
                if auto:
                    order = await _broker_exit(broker_engine, trade, action["quantity"], "SL")
                    if order:
                        stats["auto_orders"] += 1
                        message += (f" Auto-exit: closed {action['quantity']} at market "
                                    f"(order {order.get('order_id')}).")
                        update.update(apply_partial_exit(trade, price, action["quantity"], "SL_HIT"))
                        trade = {**trade, **update}
                    else:
                        message += " Auto-exit order FAILED — exit manually NOW."
                else:
                    message += " Exit to protect capital."
                events.append(make_event("SL_HIT", message, price))
                await _notify(db, trade, f"Stop loss hit: {trade['symbol']}",
                              message, "critical", "ENGINE_SL_HIT")

        if len(events) != len(trade.get("events") or []) or update:
            update["events"] = events
            await db.trades.update_one({"_id": trade["_id"]}, {"$set": update})
            if ws_push:
                try:
                    await ws_push(trade["user_id"], {"type": "trade_engine_event", "data": {
                        "trade_id": str(trade["_id"]), "symbol": trade["symbol"],
                        "status": update.get("status", "OPEN"),
                        "stop_loss": trade.get("stop_loss"),
                        "events": events[-3:],
                    }})
                except Exception:
                    pass
    return stats


# ─── Risk summary (dashboard) ────────────────────────────────────────────────

async def build_risk_summary(db, user: dict) -> dict:
    """Live view of today's risk usage vs the user's own limits."""
    user_id = str(user["_id"])
    today = _today()
    all_trades = await db.trades.find(
        {"user_id": user_id, "is_paper": {"$ne": True}}).to_list(500)

    open_trades = [t for t in all_trades if t.get("status") == "OPEN"]
    trades_today = len([t for t in all_trades
                        if (t.get("entry_time") or "").startswith(today)])
    realized_today = round(sum(
        t.get("pnl") or 0 for t in all_trades
        if t.get("pnl") is not None and (t.get("exit_time") or "").startswith(today)), 2)

    open_risk = 0.0
    open_exposure = 0.0
    for t in open_trades:
        qty = _qty_open(t)
        per_share = ((t["stop_loss"] - t["entry_price"]) if _is_short(t)
                     else (t["entry_price"] - t["stop_loss"]))
        open_risk += max(0.0, per_share) * qty
        open_exposure += (t.get("entry_price") or 0) * qty

    capital = float(user.get("capital") or 0)
    max_daily_loss = float(user.get("max_daily_loss") or 0)
    max_trades = int(user.get("max_trades_per_day") or 0)
    loss_used = abs(min(realized_today, 0))
    return {
        "trades_today": trades_today,
        "max_trades_per_day": max_trades,
        "realized_pnl_today": realized_today,
        "max_daily_loss": max_daily_loss,
        "loss_budget_used_pct": round(loss_used / max_daily_loss * 100, 1) if max_daily_loss else 0,
        "loss_budget_remaining": round(max_daily_loss - loss_used, 2) if max_daily_loss else None,
        "trading_halted": bool(max_daily_loss and realized_today <= -max_daily_loss),
        "open_positions": len(open_trades),
        "open_risk": round(open_risk, 2),
        "open_risk_pct_of_capital": round(open_risk / capital * 100, 2) if capital else 0,
        "open_exposure": round(open_exposure, 2),
        "capital": capital,
    }
