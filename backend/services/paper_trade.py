"""Paper Trading service — virtual trades with ₹1,00,000 starting capital.
Paper trades live in the same 'trades' collection but have is_paper=True.
Paper capital is tracked in users.paper_capital field.
"""
import logging
from datetime import datetime, timezone
from bson import ObjectId

logger = logging.getLogger(__name__)

DEFAULT_CAPITAL = 100_000.0
SETUP_TYPES = [
    "RSI_BREAKOUT", "VWAP_CROSS", "BULLISH_ENGULFING", "BEARISH_ENGULFING",
    "EMA_CROSSOVER", "MACD_SIGNAL", "SUPPORT_BOUNCE", "RESISTANCE_BREAK",
    "TRIANGLE_BREAKOUT", "GAP_UP_PLAY", "MOMENTUM",
]


async def get_paper_balance(user_id: str, db) -> dict:
    """Return paper_capital for user, defaulting to 1,00,000 if not set."""
    user = await db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        return {"balance": DEFAULT_CAPITAL, "starting": DEFAULT_CAPITAL}
    balance = user.get("paper_capital", DEFAULT_CAPITAL)
    return {
        "balance": round(balance, 2),
        "starting": DEFAULT_CAPITAL,
        "pnl": round(balance - DEFAULT_CAPITAL, 2),
        "pnl_pct": round(((balance - DEFAULT_CAPITAL) / DEFAULT_CAPITAL) * 100, 2),
    }


async def update_paper_balance(user_id: str, amount: float, db):
    """Add (positive) or subtract (negative) from paper_capital, atomically.

    D6.7 — WHY THIS IS `$inc` AND NOT `$set`
    ----------------------------------------
    This used to read the balance, add to it in Python, and `$set` the result.
    That is a lost update, and it is not theoretical: `tests/test_d63_real_db_
    races.py::test_concurrent_credits_should_all_be_applied` reproduced it
    against a real `mongod` — four concurrent ₹100 credits against ₹1,00,000
    left ₹1,00,100, not ₹1,00,400, because all four read the same starting
    value. It was recorded by D6.3 as an open defect under `xfail(strict=True)`
    rather than swept up, and this is the fix that clears the marker.

    `$inc` is evaluated by the server against the document's current value under
    the document lock, so N concurrent callers apply N increments. No lock, no
    retry, no transaction — the whole race lives in one field of one document,
    and the smallest correct mechanism is the operator that was designed for it.

    THE SEEDING BRANCH, AND WHY IT IS NOT A SECOND RACE
    --------------------------------------------------
    `$inc` on a field that does not exist starts from zero, not from
    `DEFAULT_CAPITAL` — so a user row that predates paper trading would have its
    balance silently reset to the increment. The pre-D6.7 code got this right by
    accident (its Python-side default did the work), and losing it would be a
    financial regression dressed as a concurrency fix.

    So the increment runs first and unconditionally: if it matched a row, that
    row had a balance and the arithmetic is done. Only when it matches nothing
    does the seeding branch run, and that branch is itself conditional on the
    balance still being absent — so of N concurrent callers that all find the
    row missing, the `_id` unique index admits exactly one creator and the rest
    fall back to the increment they should have taken.
    """
    oid = ObjectId(user_id)
    delta = round(amount, 2)
    result = await db.users.update_one({"_id": oid}, {"$inc": {"paper_capital": delta}})
    if not getattr(result, "matched_count", 0):
        try:
            await db.users.update_one(
                {"_id": oid, "paper_capital": {"$exists": False}},
                {"$set": {"paper_capital": round(DEFAULT_CAPITAL + delta, 2)}},
                upsert=True,
            )
        except Exception:
            # A concurrent creator won the `_id` index. The row exists now, so
            # the increment this call owes it is the one that was skipped above.
            await db.users.update_one({"_id": oid}, {"$inc": {"paper_capital": delta}})
    row = await db.users.find_one({"_id": oid}, {"paper_capital": 1})
    return round((row or {}).get("paper_capital", DEFAULT_CAPITAL + delta), 2)


async def get_paper_trades(user_id: str, db) -> list:
    """Return all paper trades for user, newest first."""
    trades = await db.trades.find(
        {"user_id": user_id, "is_paper": True}
    ).sort("entry_time", -1).to_list(200)

    # Enrich with live current price
    from services.real_market import fetch_real_stock_quote
    for t in trades:
        t["_id"] = str(t["_id"])
        if t.get("status") == "OPEN":
            try:
                q = await fetch_real_stock_quote(t["symbol"])
                if q:
                    t["current_price"] = q["price"]
                    multiplier = 1 if t.get("type") == "BUY" else -1
                    t["unrealized_pnl"] = round(
                        multiplier * (q["price"] - t["entry_price"]) * t["quantity"], 2
                    )
                    t["unrealized_pnl_pct"] = round(
                        multiplier * ((q["price"] - t["entry_price"]) / t["entry_price"]) * 100, 2
                    )
            except Exception:
                t["current_price"] = t["entry_price"]
                t["unrealized_pnl"] = 0.0
                t["unrealized_pnl_pct"] = 0.0
    return trades


async def get_paper_pnl(user_id: str, db) -> dict:
    """Compute total realized + unrealized P&L for all paper trades.

    PH3.8 changes, none of them cosmetic:

    * **Closed means `status != "OPEN"`, not `status == "CLOSED"`.** The
      lifecycle also writes TARGET_HIT and SL_HIT; a paper trade that exited at
      a target contributed nothing to realised P&L and was not counted as open
      either, so it vanished from the account entirely.
    * **The 500-document cap is gone** — it silently truncated the account.
    * **A missing quote is reported, not swallowed.** Every open position whose
      quote could not be fetched used to contribute exactly ₹0 of unrealised
      P&L, which is indistinguishable from a position that has not moved. The
      count is now returned as `marks_unavailable` so the UI can say the figure
      is partial instead of presenting a degraded number as a complete one.
    """
    trades = await db.trades.find({"user_id": user_id, "is_paper": True}).to_list(None)

    realized = sum(t.get("pnl") or 0 for t in trades if t.get("status") != "OPEN")
    open_trades = [t for t in trades if t.get("status") == "OPEN"]

    unrealized = 0.0
    marks_unavailable = 0
    from services.real_market import fetch_real_stock_quote
    for t in open_trades:
        try:
            q = await fetch_real_stock_quote(t["symbol"])
        except Exception:
            q = None
        if q and q.get("price") is not None:
            multiplier = 1 if t.get("type") == "BUY" else -1
            unrealized += multiplier * (q["price"] - t["entry_price"]) * t["quantity"]
        else:
            marks_unavailable += 1

    total_pnl = round(realized + unrealized, 2)
    return {
        "realized_pnl": round(realized, 2),
        "unrealized_pnl": round(unrealized, 2),
        "total_pnl": total_pnl,
        # Denominator is the FIXED starting capital by design — a return on the
        # account's inception, which stays stable as positions open and close.
        "total_pnl_pct": round((total_pnl / DEFAULT_CAPITAL) * 100, 2),
        "open_trades": len(open_trades),
        "closed_trades": len(trades) - len(open_trades),
        "marks_unavailable": marks_unavailable,
        "complete": marks_unavailable == 0,
        "scope": "paper",
        "basis": "gross",
    }


def _validate(**kwargs) -> None:
    """Re-run `PaperTradeCreate`'s constraints on service-layer arguments.

    Imported lazily: `models` pulls in the security/password layer, and this
    service is imported inside request handlers where that cost is paid once
    per process anyway — keeping it out of module scope keeps `paper_trade`
    importable in isolation (its unit tests construct a bare FakeDB).

    The Pydantic error is flattened into a single readable sentence because it
    surfaces to the user as an HTTP 400 `detail` string, not as a structured
    422 body (that path belongs to FastAPI's own model binding).
    """
    from pydantic import ValidationError

    from models import PaperTradeCreate

    payload = dict(kwargs)
    payload["type"] = payload.pop("trade_type")
    # `None` means "caller omitted it" for the optional text/target fields;
    # let the model's own defaults apply rather than failing on a null.
    for optional_field in ("stock_name", "setup_type", "notes", "target2"):
        if payload.get(optional_field) is None:
            payload.pop(optional_field, None)

    try:
        PaperTradeCreate(**payload)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise ValueError(f"Invalid paper trade: {problems}") from exc


async def execute_paper_trade(
    user_id: str,
    symbol: str,
    stock_name: str,
    quantity: int,
    entry_price: float,
    trade_type: str,
    stop_loss: float,
    target1: float,
    target2: float,
    setup_type: str,
    notes: str,
    db,
) -> dict:
    """Execute a paper trade. Checks balance for BUY. Returns the inserted trade doc.

    Validates its own arguments FIRST (PH3.12R / B-1). The HTTP route binds
    `PaperTradeCreate`, so a request that reaches here has already been
    validated — but "the caller validated it" is exactly the assumption that
    produced B-1, and this function is importable by any future scheduler,
    backfill or AI action that will not go through FastAPI. Re-validating
    against the *same* model rather than a hand-written copy of its rules keeps
    the two layers from disagreeing the way `TradeCreate` and the old inline
    `PaperTradeCreate` disagreed.

    Raises `ValueError` (which the route maps to 400) before touching the
    balance, the trades collection or anything else.
    """
    _validate(
        symbol=symbol, stock_name=stock_name, quantity=quantity,
        entry_price=entry_price, trade_type=trade_type, stop_loss=stop_loss,
        target1=target1, target2=target2, setup_type=setup_type, notes=notes,
    )

    symbol = symbol.upper()
    total_cost = round(entry_price * quantity, 2)

    if trade_type == "BUY":
        # D6.7 — CHECK AND DEBIT ARE ONE WRITE, NOT TWO STEPS.
        #
        # This used to read the balance, compare it in Python, and debit. Two
        # concurrent BUYs for ₹60,000 against a ₹1,00,000 account both read
        # ₹1,00,000, both passed the check, and both debited — leaving the
        # account at ₹-20,000 with two positions it could never have afforded.
        # Found by D6.7's stress matrix; not one of the two races D6.3 recorded.
        #
        # The sufficiency test now lives in the filter, so the server evaluates
        # it against the current balance under the document lock. A caller whose
        # filter does not match did not have the money at the instant it tried
        # to spend it, which is the only instant that matters.
        debited = await db.users.update_one(
            {"_id": ObjectId(user_id), "paper_capital": {"$gte": total_cost}},
            {"$inc": {"paper_capital": -total_cost}},
        )
        if not getattr(debited, "modified_count", 0):
            # Either genuinely insufficient, or a row with no balance yet. Read
            # it back to tell the user which, and to keep the starting-capital
            # default in one place (`get_paper_balance`).
            balance_info = await get_paper_balance(user_id, db)
            if balance_info["balance"] >= total_cost:
                # The row exists but carries no `paper_capital` field — seed it
                # and take the debit in one conditional write, which is the same
                # compare-and-swap applied to the seeded value.
                seeded = await db.users.update_one(
                    {"_id": ObjectId(user_id), "paper_capital": {"$exists": False}},
                    {"$set": {"paper_capital": round(DEFAULT_CAPITAL - total_cost, 2)}},
                )
                if getattr(seeded, "modified_count", 0):
                    debited = seeded
            if not getattr(debited, "modified_count", 0):
                raise ValueError(
                    f"Insufficient paper capital. Need ₹{total_cost:,.2f}, "
                    f"have ₹{balance_info['balance']:,.2f}"
                )

    trade_doc = {
        "user_id": user_id,
        "symbol": symbol,
        "stock_name": stock_name or symbol,
        "type": trade_type,
        "entry_price": entry_price,
        "quantity": quantity,
        "stop_loss": stop_loss,
        "target1": target1,
        "target2": target2 or target1,
        "status": "OPEN",
        "pnl": None,
        "pnl_percent": None,
        "entry_time": datetime.now(timezone.utc).isoformat(),
        "exit_time": None,
        "exit_price": None,
        "notes": notes or "",
        "setup_type": setup_type or "MOMENTUM",
        "is_paper": True,
        "total_cost": total_cost,
    }
    result = await db.trades.insert_one(trade_doc)
    trade_doc["_id"] = str(result.inserted_id)

    from services.activity_logger import log_activity
    # Private: a paper trade is still this user's trade flow — side, size and
    # symbol (D6.1 / S4).
    log_activity(
        f"Paper trade: {trade_type} {quantity} {symbol} @ ₹{entry_price}", "monitor", "done",
        user_id=str(user_id),
    )
    return trade_doc


async def close_paper_trade(trade_id: str, user_id: str, db) -> dict:
    """Close an open paper trade at the current market price."""
    trade = await db.trades.find_one({"_id": ObjectId(trade_id), "user_id": user_id, "is_paper": True})
    if not trade:
        raise ValueError("Paper trade not found")
    if trade["status"] != "OPEN":
        raise ValueError("Trade already closed")

    # Get current market price
    from services.real_market import fetch_real_stock_quote
    q = await fetch_real_stock_quote(trade["symbol"])
    exit_price = q["price"] if q else trade["entry_price"]

    multiplier = 1 if trade["type"] == "BUY" else -1
    pnl = round(multiplier * (exit_price - trade["entry_price"]) * trade["quantity"], 2)
    pnl_pct = round(multiplier * ((exit_price - trade["entry_price"]) / trade["entry_price"]) * 100, 2)

    now = datetime.now(timezone.utc).isoformat()
    # D6.3 — the owner is part of the write, not merely of the read above. The
    # read that found this trade already filtered on `user_id`; stating the rule
    # again here is what makes it survive an edit to either statement alone.
    #
    # D6.7 — AND SO IS THE STATUS. This is the fix for the second of D6.3's two
    # recorded open races (`test_a_paper_trade_should_only_close_once`).
    #
    # The `status != "OPEN"` check above is a decision made against a value read
    # moments earlier. Restating `status: "OPEN"` in the filter is what turns
    # that decision into a compare-and-swap: of N concurrent closers, all N pass
    # the Python check, and exactly one write finds the trade still OPEN. The
    # other N-1 match nothing.
    #
    # The credit below is then conditional on having WON, which is the half that
    # actually protects the money. Without it, every caller credited the
    # proceeds — a position closed three times paid out three times. That
    # over-credit was previously *masked* by `update_paper_balance`'s lost
    # update (all three credits collapsed into one), which is why the two fixes
    # had to land together: repairing the balance alone would have converted a
    # hidden double-close into a real, visible over-credit.
    closed = await db.trades.update_one(
        {"_id": ObjectId(trade_id), "user_id": user_id, "is_paper": True,
         "status": "OPEN"},
        {"$set": {
            "status": "CLOSED",
            "exit_price": exit_price,
            "exit_time": now,
            "pnl": pnl,
            "pnl_percent": pnl_pct,
        }},
    )
    if not getattr(closed, "modified_count", 0):
        # Somebody else closed it between the read and this write. Raising the
        # same error the pre-check raises keeps the two indistinguishable to the
        # caller — the trade is closed and this request did not close it, which
        # is exactly what "Trade already closed" means.
        raise ValueError("Trade already closed")

    # Credit back proceeds on BUY close
    if trade["type"] == "BUY":
        proceeds = round(exit_price * trade["quantity"], 2)
        await update_paper_balance(user_id, proceeds, db)
    else:
        # For SELL (short), buy back at exit price
        cost = round(exit_price * trade["quantity"], 2)
        original_proceeds = round(trade["entry_price"] * trade["quantity"], 2)
        await update_paper_balance(user_id, original_proceeds - cost, db)

    return {
        "trade_id": trade_id,
        "exit_price": exit_price,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "status": "CLOSED",
    }


async def reset_paper_capital(user_id: str, db):
    """Reset paper_capital to ₹1,00,000 and close all open paper trades."""
    # `close_reason` (PH3.8): these positions are not closed at breakeven, they
    # are ABANDONED at a fabricated ₹0 because the account was reset. Without a
    # marker they are indistinguishable from real breakeven exits and pollute
    # every paper win-rate and average-P&L figure with a synthetic outcome. The
    # marker lets analytics exclude them deliberately rather than by guessing at
    # `pnl == 0`.
    await db.trades.update_many(
        {"user_id": user_id, "is_paper": True, "status": "OPEN"},
        {"$set": {"status": "CLOSED", "pnl": 0, "pnl_percent": 0,
                  "close_reason": "capital_reset",
                  "exit_time": datetime.now(timezone.utc).isoformat()}},
    )
    await db.users.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"paper_capital": DEFAULT_CAPITAL}},
        upsert=True,
    )
    from services.activity_logger import log_activity
    log_activity("Paper trading capital reset to ₹1,00,000", "monitor", "done",
                 user_id=str(user_id))
    return {"message": "Paper capital reset to ₹1,00,000", "new_balance": DEFAULT_CAPITAL}
