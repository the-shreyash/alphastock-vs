"""Sprint R4 — Scanner Live Migration tests (hermetic, no network).

Covers the continuous-scanner worker layer:
  • filter_novel: per-(kind, symbol) cooldown — first hit passes, repeats are
    suppressed, expiry re-arms, kinds are independent, expired entries pruned
  • detect_momentum: threshold + acceleration semantics over cycle snapshots
  • heartbeat task contracts: scanner.momentum published once (then deduped),
    volume task emits scanner.volume_spike (never the pre-R4 scanner.volume),
    scanner sweep emits exactly ONE worker-tagged scanner.updated
  • scanner_engine.scan(): publish=False emits nothing; default emits
    source="api"

All fetchers are monkeypatched; the event bus is spied in-process.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from services.market_engine import scanner_worker
from services.market_engine.event_bus import event_bus


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_worker_state():
    scanner_worker.reset_state()
    yield
    scanner_worker.reset_state()


class _BusSpy:
    """Subscribe to event types on the singleton bus and record hits."""

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


# ---------------------------------------------------------------------------
# filter_novel — cooldown dedupe
# ---------------------------------------------------------------------------

def test_filter_novel_first_hit_passes_repeat_suppressed():
    hit = [{"symbol": "RELIANCE", "price": 2850.0}]
    assert scanner_worker.filter_novel("breakout", hit) == hit
    assert scanner_worker.filter_novel("breakout", hit) == []


def test_filter_novel_cooldown_expiry_and_pruning():
    now = datetime.now(timezone.utc)
    hit = [{"symbol": "TCS"}]
    assert scanner_worker.filter_novel("breakout", hit, now=now) == hit

    later = now + timedelta(minutes=scanner_worker.HIT_COOLDOWN_MINUTES + 1)
    # Past the cooldown the same hit is novel again, and the expired entry
    # was pruned before being re-recorded (state stays bounded).
    assert scanner_worker.filter_novel("breakout", hit, now=later) == hit
    assert len(scanner_worker._recent_hits) == 1


def test_filter_novel_kinds_are_independent():
    hit = [{"symbol": "INFY"}]
    assert scanner_worker.filter_novel("breakout", hit) == hit
    assert scanner_worker.filter_novel("volume_spike", hit) == hit
    assert scanner_worker.filter_novel("momentum", hit) == hit


def test_filter_novel_skips_candidates_without_symbol():
    assert scanner_worker.filter_novel("breakout", [{"price": 1.0}]) == []


# ---------------------------------------------------------------------------
# detect_momentum — threshold + acceleration
# ---------------------------------------------------------------------------

def test_detect_momentum_threshold_and_acceleration():
    prev = {"HOT": 2.5, "FLAT": 2.4, "WARM": 1.0}
    quotes = [
        {"symbol": "HOT", "change_pct": 3.0},    # accelerated +0.5 → included
        {"symbol": "FLAT", "change_pct": 2.5},   # +0.1 < MIN_ACCEL → excluded
        {"symbol": "WARM", "change_pct": 2.2},   # newly above threshold → included
        {"symbol": "COLD", "change_pct": 0.5},   # below threshold → excluded
        {"symbol": "NEW", "change_pct": 4.0},    # unseen symbol → included
        {"symbol": "NAN"},                        # missing change_pct → tolerated
    ]
    candidates, snapshot = scanner_worker.detect_momentum(quotes, prev)

    symbols = [c["symbol"] for c in candidates]
    assert symbols == ["NEW", "HOT", "WARM"]  # sorted by change_pct desc
    hot = next(c for c in candidates if c["symbol"] == "HOT")
    assert hot["prev_change_pct"] == 2.5
    assert hot["acceleration"] == 0.5
    # Snapshot carries every quote with a change_pct for the next cycle.
    assert snapshot == {"HOT": 3.0, "FLAT": 2.5, "WARM": 2.2, "COLD": 0.5, "NEW": 4.0}


def test_momentum_pass_flat_repeat_not_re_reported():
    quotes = [{"symbol": "TCS", "change_pct": 3.0}]
    first = scanner_worker.momentum_pass(quotes)
    assert [c["symbol"] for c in first] == ["TCS"]
    # Same reading next cycle: already hot, no acceleration → no candidate.
    assert scanner_worker.momentum_pass(quotes) == []


# ---------------------------------------------------------------------------
# Heartbeat task contracts
# ---------------------------------------------------------------------------

def test_momentum_task_publishes_contract_once(monkeypatch):
    from services import real_market
    from services import heartbeat_engine

    quotes = [
        {"symbol": "TCS", "change_pct": 3.2, "price": 4100.0},
        {"symbol": "INFY", "change_pct": 0.4, "price": 1500.0},
    ]

    async def fake_universe():
        return quotes

    monkeypatch.setattr(real_market, "fetch_all_universe_quotes", fake_universe)

    spy = _BusSpy("scanner.momentum")
    try:
        _run(heartbeat_engine.task_scan_momentum())
        _run(heartbeat_engine.task_scan_momentum())  # repeat cycle → deduped
    finally:
        spy.close()

    events = spy.of("scanner.momentum")
    assert len(events) == 1
    data = events[0]["data"]
    assert data["kind"] == "momentum"
    assert data["count"] == 1
    assert data["candidates"][0]["symbol"] == "TCS"
    assert data["candidates"][0]["prev_change_pct"] is None


def test_volume_task_emits_volume_spike_never_old_name(monkeypatch):
    from services import real_market
    from services import heartbeat_engine

    monkeypatch.setattr(heartbeat_engine, "_next_volume_batch", lambda: ["AAA", "BBB"])

    async def fake_quote(symbol):
        return {"symbol": symbol, "price": 100.0, "volume_ratio": 2.0 if symbol == "AAA" else 1.0}

    monkeypatch.setattr(real_market, "fetch_real_stock_quote", fake_quote)

    spy = _BusSpy("scanner.volume_spike", "scanner.volume")
    try:
        _run(heartbeat_engine.task_check_volume())
    finally:
        spy.close()

    assert spy.of("scanner.volume") == []
    events = spy.of("scanner.volume_spike")
    assert len(events) == 1
    data = events[0]["data"]
    assert data["kind"] == "volume_spike"
    assert [c["symbol"] for c in data["candidates"]] == ["AAA"]


def test_breakout_task_dedupes_repeat_cycle(monkeypatch):
    from services import real_market
    from services import heartbeat_engine

    quotes = [{"symbol": "SBIN", "price": 999.0, "high": 1000.0, "change_pct": 2.0}]

    async def fake_universe():
        return quotes

    monkeypatch.setattr(real_market, "fetch_all_universe_quotes", fake_universe)

    spy = _BusSpy("scanner.breakout")
    try:
        _run(heartbeat_engine.task_find_breakouts())
        _run(heartbeat_engine.task_find_breakouts())
    finally:
        spy.close()

    events = spy.of("scanner.breakout")
    assert len(events) == 1
    assert events[0]["data"]["kind"] == "breakout"
    assert events[0]["data"]["candidates"][0]["symbol"] == "SBIN"


def test_scanner_sweep_publishes_single_worker_update(monkeypatch):
    from services.market_engine.gateway import market_gateway
    from services import heartbeat_engine

    quotes = [
        {"symbol": "TCS", "price": 4100.0, "change_pct": 2.0, "rsi": 60,
         "volume_ratio": 1.6, "volume": 1_000_000, "sector": "IT"},
    ]

    async def fake_universe():
        return quotes

    monkeypatch.setattr(market_gateway, "get_universe_quotes", fake_universe)

    spy = _BusSpy("scanner.updated")
    try:
        _run(heartbeat_engine.task_scanner_sweep())
    finally:
        spy.close()

    events = spy.of("scanner.updated")
    assert len(events) == 1  # scan(publish=False) must not add its own
    data = events[0]["data"]
    assert data["source"] == "worker"
    assert len(data["strategies"]) == 2
    for entry in data["strategies"].values():
        assert set(entry) == {"matched", "top"}


# ---------------------------------------------------------------------------
# scanner_engine.scan() — publish gating + source tagging
# ---------------------------------------------------------------------------

def test_scan_publish_flag_and_api_source(monkeypatch):
    from services.market_engine import scanner_engine
    from services.market_engine.gateway import market_gateway

    async def fake_universe():
        return [{"symbol": "TCS", "price": 4100.0, "change_pct": 2.0,
                 "rsi": 60, "volume_ratio": 1.6, "volume": 1_000_000, "sector": "IT"}]

    monkeypatch.setattr(market_gateway, "get_universe_quotes", fake_universe)

    spy = _BusSpy("scanner.updated")
    try:
        _run(scanner_engine.scan(strategy="momentum", publish=False))
        assert spy.of("scanner.updated") == []

        _run(scanner_engine.scan(strategy="momentum"))
    finally:
        spy.close()

    events = spy.of("scanner.updated")
    assert len(events) == 1
    assert events[0]["data"]["source"] == "api"
