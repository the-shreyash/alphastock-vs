"""Sprint R5 — Portfolio Live Migration tests (hermetic, no network).

Covers the live portfolio stream layer (services/portfolio_stream.py):
  • snapshot_payload: pnl/allocation/light-holdings shape + the legacy-compat
    flat fields (total_pnl / total_unrealized_pnl / open_positions)
  • publish_snapshot: publishes portfolio.updated with user_id; silent (None,
    no event) for users with no holdings; price_override supersedes the quote
  • apply_broker_ticks: canonical MarketTick dicts (symbol-keyed since D4.3 —
    the broker's instrument identifier is resolved at the broker boundary and
    never arrives here), fresh marks persisted to db.holdings, throttle (second
    burst suppressed, reset_state re-arms), unmatched/empty ticks emit nothing
  • heartbeat task_monitor_portfolio contract: one portfolio.updated per user
    covering BOTH broker-holdings users and manual-trade users, reason
    "monitor"

Quote fetchers are injected/monkeypatched; the event bus is spied in-process.
"""
import asyncio

import pytest

from tests._fakedb import FakeDB

from services import portfolio_stream
from services.market_engine.event_bus import event_bus


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_stream_state():
    portfolio_stream.reset_state()
    yield
    portfolio_stream.reset_state()


class _BusSpy:
    def __init__(self, *event_types):
        self.events = []
        self._types = event_types

        async def handler(evt):
            self.events.append(evt)

        self._handler = handler
        for et in event_types:
            event_bus.subscribe(et, self._handler)

    def close(self):
        for et in self._types:
            event_bus.unsubscribe(et, self._handler)

    def of(self, event_type):
        return [e for e in self.events if e["type"] == event_type]


async def _quotes(symbols):
    book = {
        "RELIANCE": {"price": 2600.0, "sector": "Energy", "change_pct": 1.2},
        "TCS": {"price": 3800.0, "sector": "IT", "change_pct": -0.5},
    }
    return {(s or "").upper(): book.get((s or "").upper()) for s in symbols}


def _broker_holding(user_id="u1", symbol="RELIANCE", qty=10, avg=2000.0,
                    token=738561, broker="zerodha"):
    return {
        "user_id": user_id, "broker": broker, "symbol": symbol,
        "quantity": qty, "average_price": avg,
        "invested_value": round(avg * qty, 2),
        "market_value": round(avg * qty, 2),
        "last_price": avg, "instrument_token": token,
    }


def _manual_trade(user_id="u2", symbol="TCS", qty=5, entry=3500.0):
    return {
        "user_id": user_id, "symbol": symbol, "status": "OPEN",
        "quantity": qty, "entry_price": entry, "is_paper": False,
    }


# ---------------------------------------------------------------------------
# snapshot_payload — pure shape
# ---------------------------------------------------------------------------

def _enriched_holdings():
    return [
        {"symbol": "RELIANCE", "invested": 20000.0, "current_price": 2600.0,
         "current_value": 26000.0, "pnl": 6000.0, "pnl_pct": 30.0,
         "day_change_pct": 1.2, "sector": "Energy", "quantity": 10},
        {"symbol": "TCS", "invested": 17500.0, "current_price": 3800.0,
         "current_value": 19000.0, "pnl": 1500.0, "pnl_pct": 8.57,
         "day_change_pct": -0.5, "sector": "IT", "quantity": 5},
    ]


def test_snapshot_payload_shape_and_compat_fields():
    payload = portfolio_stream.snapshot_payload(_enriched_holdings(), "u1", "monitor")
    assert payload["user_id"] == "u1"
    assert payload["reason"] == "monitor"
    assert payload["open_positions"] == 2
    # P&L block (no realized — that belongs to the REST intelligence bundle).
    assert payload["pnl"]["invested"] == 37500.0
    assert payload["pnl"]["current_value"] == 45000.0
    assert payload["pnl"]["unrealized"] == 7500.0
    assert "realized" not in payload["pnl"]
    # Legacy-compat flat fields (pre-R5 portfolio_update consumer contract).
    assert payload["total_pnl"] == 7500.0
    assert payload["total_unrealized_pnl"] == 7500.0
    # Allocation carries both breakdowns for the live pie/sector bars.
    symbols = [a["symbol"] for a in payload["allocation"]["by_holding"]]
    assert symbols == ["RELIANCE", "TCS"]  # sorted by value desc
    sectors = {a["sector"] for a in payload["allocation"]["by_sector"]}
    assert sectors == {"Energy", "IT"}
    # Light per-holding marks only (no avg/invested duplication).
    row = payload["holdings"][0]
    assert set(row) == {"symbol", "current_price", "current_value",
                        "pnl", "pnl_pct", "day_change_pct"}


# ---------------------------------------------------------------------------
# publish_snapshot
# ---------------------------------------------------------------------------

def test_publish_snapshot_emits_per_user_event():
    async def run():
        db = FakeDB()
        db.holdings.docs = [_broker_holding()]
        spy = _BusSpy("portfolio.updated")
        try:
            payload = await portfolio_stream.publish_snapshot(
                db, "u1", quotes_map_func=_quotes, reason="monitor")
        finally:
            spy.close()
        return payload, spy.events

    payload, events = _run(run())
    assert payload is not None
    assert len(events) == 1
    data = events[0]["data"]
    assert data["user_id"] == "u1"
    assert data["reason"] == "monitor"
    # Live quote (2600) marks the broker position bought at 2000.
    assert data["pnl"]["current_value"] == 26000.0
    assert data["total_pnl"] == 6000.0


def test_publish_snapshot_silent_without_holdings():
    async def run():
        db = FakeDB()
        spy = _BusSpy("portfolio.updated")
        try:
            payload = await portfolio_stream.publish_snapshot(
                db, "ghost", quotes_map_func=_quotes)
        finally:
            spy.close()
        return payload, spy.events

    payload, events = _run(run())
    assert payload is None
    assert events == []


def test_price_override_supersedes_quote_price():
    async def run():
        db = FakeDB()
        db.holdings.docs = [_broker_holding()]
        spy = _BusSpy("portfolio.updated")
        try:
            payload = await portfolio_stream.publish_snapshot(
                db, "u1", quotes_map_func=_quotes,
                reason="broker_tick", price_override={"RELIANCE": 2700.0})
        finally:
            spy.close()
        return payload

    payload = _run(run())
    assert payload["holdings"][0]["current_price"] == 2700.0
    assert payload["pnl"]["current_value"] == 27000.0


# ---------------------------------------------------------------------------
# apply_broker_ticks — tick mapping, persistence, throttle
# ---------------------------------------------------------------------------

def test_broker_ticks_map_persist_and_publish():
    async def run():
        db = FakeDB()
        db.holdings.docs = [_broker_holding(token=738561)]
        spy = _BusSpy("portfolio.updated")
        try:
            payload = await portfolio_stream.apply_broker_ticks(
                db, "u1", "zerodha",
                [{"symbol": "RELIANCE", "price": 2650.0, "exchange": "NSE"}],
                quotes_map_func=_quotes)
        finally:
            spy.close()
        return db, payload, spy.events

    db, payload, events = _run(run())
    assert len(events) == 1
    assert payload["reason"] == "broker_tick"
    # Tick price beats the (older) Yahoo quote for the ticked symbol.
    assert payload["holdings"][0]["current_price"] == 2650.0
    # Fresh mark persisted so REST reads reflect the live broker price.
    doc = db.holdings.docs[0]
    assert doc["last_price"] == 2650.0
    assert doc["market_value"] == 26500.0


def test_broker_ticks_throttled_then_rearmed():
    async def run():
        db = FakeDB()
        db.holdings.docs = [_broker_holding(token=738561)]
        ticks = [{"symbol": "RELIANCE", "price": 2650.0, "exchange": "NSE"}]
        spy = _BusSpy("portfolio.updated")
        try:
            first = await portfolio_stream.apply_broker_ticks(
                db, "u1", "zerodha", ticks, quotes_map_func=_quotes)
            second = await portfolio_stream.apply_broker_ticks(
                db, "u1", "zerodha", ticks, quotes_map_func=_quotes)
            portfolio_stream.reset_state()
            third = await portfolio_stream.apply_broker_ticks(
                db, "u1", "zerodha", ticks, quotes_map_func=_quotes)
        finally:
            spy.close()
        return first, second, third, spy.events

    first, second, third, events = _run(run())
    assert first is not None
    assert second is None  # within TICK_EMIT_INTERVAL — suppressed
    assert third is not None  # re-armed
    assert len(events) == 2


def test_broker_ticks_ignore_symbols_the_user_does_not_hold():
    async def run():
        db = FakeDB()
        db.holdings.docs = [_broker_holding(token=738561)]
        spy = _BusSpy("portfolio.updated")
        try:
            payload = await portfolio_stream.apply_broker_ticks(
                db, "u1", "zerodha",
                [{"symbol": "INFY", "price": 100.0, "exchange": "NSE"}],
                quotes_map_func=_quotes)
        finally:
            spy.close()
        return payload, spy.events

    payload, events = _run(run())
    assert payload is None
    assert events == []


# ---------------------------------------------------------------------------
# heartbeat task contract
# ---------------------------------------------------------------------------

def test_task_monitor_portfolio_streams_every_user(monkeypatch):
    from services import heartbeat_engine

    db = FakeDB()
    db.holdings.docs = [_broker_holding(user_id="u1")]     # broker user
    db.trades.docs = [_manual_trade(user_id="u2")]         # manual-trade user
    monkeypatch.setattr(heartbeat_engine, "_db", db)
    monkeypatch.setattr(portfolio_stream, "quotes_map", _quotes)

    async def run():
        spy = _BusSpy("portfolio.updated")
        try:
            await heartbeat_engine.task_monitor_portfolio()
        finally:
            spy.close()
        return spy.events

    events = _run(run())
    by_user = {e["data"]["user_id"]: e["data"] for e in events}
    assert set(by_user) == {"u1", "u2"}
    assert all(d["reason"] == "monitor" for d in by_user.values())
    # Manual user's P&L computed from the shared prefetched quotes.
    assert by_user["u2"]["pnl"]["current_value"] == 19000.0  # 5 × 3800
    assert by_user["u1"]["pnl"]["current_value"] == 26000.0  # 10 × 2600
