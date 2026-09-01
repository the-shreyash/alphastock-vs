"""Sprint R9 — Performance Optimization tests (hermetic, no Redis/network).

Covers the backend deliverables:
  • cache_get_many / cache_set_many batched operations (in-memory fallback
    semantics: roundtrip, expired keys omitted, missing keys omitted)
  • bounded in-memory cache (expired sweep + oldest eviction at the cap)
  • fetch_all_universe_quotes MGET warm-up (warm symbols skip the HTTP fetch)
  • ConnectionManager single-serialization fan-out (one dumps per broadcast,
    identical payload to every subscribed socket, dead sockets reaped)
"""
import asyncio
from datetime import datetime, timedelta, timezone

from services import cache as cache_mod
from services.cache import (
    _MEMORY_MAX_KEYS,
    cache_get_many,
    cache_set,
    cache_set_many,
)


def _run(coro):
    return asyncio.run(coro)


def _clear_memory():
    cache_mod._memory.clear()


# ---------------------------------------------------------------------------
# Batched cache operations
# ---------------------------------------------------------------------------

def test_get_many_set_many_roundtrip():
    _clear_memory()

    async def run():
        await cache_set_many({"r9:a": {"v": 1}, "r9:b": {"v": 2}}, ttl=60)
        return await cache_get_many(["r9:a", "r9:b", "r9:missing"])

    out = _run(run())
    assert out == {"r9:a": {"v": 1}, "r9:b": {"v": 2}}


def test_get_many_omits_expired_entries():
    _clear_memory()

    async def run():
        await cache_set("r9:fresh", "ok", ttl=60)
        await cache_set("r9:stale", "old", ttl=60)
        # Age the stale entry past its TTL.
        cache_mod._memory["r9:stale"]["ts"] = (
            datetime.now(timezone.utc) - timedelta(seconds=120)
        )
        return await cache_get_many(["r9:fresh", "r9:stale"])

    out = _run(run())
    assert out == {"r9:fresh": "ok"}
    # The expired entry was also evicted from the store on read.
    assert "r9:stale" not in cache_mod._memory


def test_get_many_empty_input_is_safe():
    assert _run(cache_get_many([])) == {}
    assert _run(cache_get_many(None)) == {}


# ---------------------------------------------------------------------------
# Bounded in-memory store
# ---------------------------------------------------------------------------

def test_memory_store_stays_bounded():
    _clear_memory()

    async def run():
        for i in range(_MEMORY_MAX_KEYS + 50):
            await cache_set(f"r9:bound:{i}", i, ttl=300)

    _run(run())
    assert len(cache_mod._memory) <= _MEMORY_MAX_KEYS
    # The newest write always survives eviction.
    assert f"r9:bound:{_MEMORY_MAX_KEYS + 49}" in cache_mod._memory
    _clear_memory()


def test_prune_sweeps_expired_before_evicting_fresh():
    _clear_memory()

    async def run():
        await cache_set("r9:keep", "fresh", ttl=300)

    _run(run())
    # Fill to the cap with already-expired entries.
    old_ts = datetime.now(timezone.utc) - timedelta(seconds=999)
    for i in range(_MEMORY_MAX_KEYS):
        cache_mod._memory[f"r9:exp:{i}"] = {"data": i, "ts": old_ts, "ttl": 1}

    _run(cache_set("r9:new", "v", 300))
    assert "r9:keep" in cache_mod._memory  # fresh entry survived the sweep
    assert "r9:new" in cache_mod._memory
    assert len(cache_mod._memory) <= _MEMORY_MAX_KEYS
    _clear_memory()


# ---------------------------------------------------------------------------
# Universe quote MGET warm-up
# ---------------------------------------------------------------------------

def test_universe_quotes_warm_hits_skip_http_fetch(monkeypatch):
    import market_data
    from services import real_market

    universe = [
        {"symbol": "AAA", "name": "A Corp", "sector": "IT"},
        {"symbol": "BBB", "name": "B Corp", "sector": "Auto"},
    ]
    monkeypatch.setattr(market_data, "STOCK_UNIVERSE", universe)

    # Bundle miss; per-symbol warm-up returns AAA only.
    async def fake_get(key):
        return None

    async def fake_get_many(keys):
        # D5.19 — keyed off the constant, not a literal `_2d`. The universe now
        # fetches `TECHNICALS_RANGE` so it has the bars to compute RSI/MACD
        # (see test_universe_technicals.py); a hardcoded range here made this
        # test assert the warm-cache optimisation against a key the code no
        # longer writes, so it failed for a reason that had nothing to do with
        # the batched read it exists to protect.
        return {
            f"yahoo_AAA.NS_{real_market.TECHNICALS_RANGE}": {
                "price": 101.0, "change_pct": 1.2,
            }
        }

    fetched = []

    async def fake_fetch(symbol, range_str=real_market.TECHNICALS_RANGE):
        fetched.append(symbol)
        return {"price": 202.0, "change_pct": -0.4}

    stored = {}

    async def fake_set(key, value, ttl):
        stored[key] = value

    monkeypatch.setattr(real_market, "cache_get", fake_get)
    monkeypatch.setattr(real_market, "cache_get_many", fake_get_many)
    monkeypatch.setattr(real_market, "fetch_yahoo_quote", fake_fetch)
    monkeypatch.setattr(real_market, "cache_set", fake_set)

    quotes = _run(real_market.fetch_all_universe_quotes())

    # Only the cold symbol hit the fetch path.
    assert fetched == ["BBB"]
    by_symbol = {q["symbol"]: q for q in quotes}
    assert by_symbol["AAA"]["price"] == 101.0
    assert by_symbol["BBB"]["price"] == 202.0
    # Universe metadata is applied to warm and cold quotes alike.
    assert by_symbol["AAA"]["sector"] == "IT"
    assert f"all_universe_quotes_{real_market.TECHNICALS_RANGE}" in stored


# ---------------------------------------------------------------------------
# Single-serialization WebSocket fan-out
# ---------------------------------------------------------------------------

class _FakeWS:
    def __init__(self, fail=False):
        self.raw_sent = []
        self.fail = fail

    async def send_text(self, payload):
        if self.fail:
            raise RuntimeError("socket dead")
        self.raw_sent.append(payload)


def test_broadcast_serializes_once_and_reaps_dead():
    from server import ConnectionManager

    mgr = ConnectionManager()
    a, b, dead = _FakeWS(), _FakeWS(), _FakeWS(fail=True)
    for ws in (a, b, dead):
        mgr.active.add(ws)
        mgr.channels[ws] = {"market"}

    _run(mgr.broadcast_to_channel("market", {"type": "event", "event": "market.index.updated"}))

    # Every live socket got the exact same pre-serialized string payload.
    assert a.raw_sent and a.raw_sent == b.raw_sent
    assert isinstance(a.raw_sent[0], str)
    # The failing socket was reaped from every tracking structure.
    assert dead not in mgr.active
    assert dead not in mgr.channels


def test_send_to_user_serializes_datetimes():
    from server import ConnectionManager

    mgr = ConnectionManager()
    ws = _FakeWS()
    mgr.active.add(ws)
    mgr.user_connections["u1"] = {ws}

    _run(mgr.send_to_user("u1", {"ts": datetime(2026, 7, 16, tzinfo=timezone.utc)}))
    assert ws.raw_sent and "2026-07-16" in ws.raw_sent[0]
