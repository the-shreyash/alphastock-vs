"""Tests for the Sprint 5 stock-details service (services/stock_details.py).

Covers the pure math helpers (pivot points, ATR, beta, swing-level
clustering, max drawdown, annualized volatility) plus the section routes'
unknown-symbol → 404 and live-source-down → {"available": false} contracts.

Fully hermetic: math helpers are pure (no I/O); route tests patch
services.stock_details.fetch_yahoo_quote so no real Yahoo Finance network
call is made. The service only caches successful (available) payloads, so
patched-failure tests cannot pollute the shared in-memory cache.
"""
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from server import app
from services.stock_details import (
    align_close_series,
    annualized_volatility,
    calculate_atr,
    calculate_beta,
    find_swing_levels,
    max_drawdown,
    pivot_points,
)

client = TestClient(app)


# ─── pivot_points ─────────────────────────────────────────────

def test_pivot_points_classic_formula():
    # ARRANGE — H=110, L=90, C=100 → P = 100
    result = pivot_points(110, 90, 100)

    # ASSERT — classic floor-trader levels
    assert result["p"] == 100.0
    assert result["r1"] == 110.0   # 2P - L
    assert result["s1"] == 90.0    # 2P - H
    assert result["r2"] == 120.0   # P + (H - L)
    assert result["s2"] == 80.0    # P - (H - L)
    assert result["r3"] == 130.0   # H + 2(P - L)
    assert result["s3"] == 70.0    # L - 2(H - P)


def test_pivot_points_ordering():
    result = pivot_points(105.5, 98.25, 101.0)
    assert result["s3"] < result["s2"] < result["s1"] < result["p"] < result["r1"] < result["r2"] < result["r3"]


# ─── calculate_atr ────────────────────────────────────────────

def test_atr_constant_range_equals_range():
    # ARRANGE — 20 identical candles: high 12, low 10, close 11.
    # Every true range is max(2, 1, 1) = 2, so ATR must be exactly 2.
    n = 20
    highs = [12.0] * n
    lows = [10.0] * n
    closes = [11.0] * n

    # ACT / ASSERT
    assert calculate_atr(highs, lows, closes, period=14) == 2.0


def test_atr_insufficient_history_returns_none():
    assert calculate_atr([12.0] * 5, [10.0] * 5, [11.0] * 5, period=14) is None
    assert calculate_atr([], [], []) is None


# ─── calculate_beta ───────────────────────────────────────────

def test_beta_of_2x_leveraged_series():
    # ARRANGE — stock daily returns are exactly 2x the index returns
    # (alternating ±1% on the index → ±2% on the stock) → beta = 2.
    index = [100.0]
    stock = [100.0]
    for i in range(60):
        r = 0.01 if i % 2 == 0 else -0.01
        index.append(index[-1] * (1 + r))
        stock.append(stock[-1] * (1 + 2 * r))

    # ACT
    beta = calculate_beta(stock, index)

    # ASSERT — small compounding noise allowed
    assert beta is not None
    assert abs(beta - 2.0) < 0.05


def test_beta_insufficient_overlap_returns_none():
    assert calculate_beta([100.0] * 10, [100.0] * 10) is None


# ─── align_close_series ───────────────────────────────────────

def test_align_close_series_drops_sessions_missing_from_one_side():
    # ARRANGE — index is missing day 2 (Yahoo returned a None close there),
    # so its series is one bar shorter. Date alignment must keep only the
    # common sessions instead of tail-pairing different dates.
    day = 86400
    stock = {
        "historical_closes": [100.0, 101.0, 102.0, 103.0],
        "historical_close_timestamps": [0, day, 2 * day, 3 * day],
    }
    index = {
        "historical_closes": [200.0, 202.0, 203.0],
        "historical_close_timestamps": [0, 2 * day, 3 * day],
    }

    # ACT
    s, m = align_close_series(stock, index)

    # ASSERT — day 1 (stock-only) dropped; remaining pairs share dates
    assert s == [100.0, 102.0, 103.0]
    assert m == [200.0, 202.0, 203.0]


def test_align_close_series_falls_back_without_timestamps():
    # Cached quotes from before the aligned-timestamp field existed must
    # still work: fall back to the raw close series untouched.
    stock = {"historical_closes": [1.0, 2.0]}
    index = {"historical_closes": [3.0, 4.0, 5.0]}
    assert align_close_series(stock, index) == ([1.0, 2.0], [3.0, 4.0, 5.0])


# ─── find_swing_levels ────────────────────────────────────────

def test_swing_levels_cluster_within_one_percent_and_rank_by_touches():
    # ARRANGE — a gently sloping base (no tied local maxima) with three
    # separated peaks near 110 (110.0, 110.5, 109.8 — all within 1% of each
    # other → one cluster with 3 touches) and one distinct peak at 130.
    highs = [100.0 - i * 0.01 for i in range(40)]
    lows = [95.0 - i * 0.01 for i in range(40)]
    for idx, peak in ((5, 110.0), (15, 110.5), (25, 109.8)):
        highs[idx] = peak
    highs[35] = 130.0

    # ACT
    swing_highs, _ = find_swing_levels(highs, lows)

    # ASSERT — clustered peak ranked first by touches
    assert swing_highs, "expected at least one swing-high cluster"
    top = swing_highs[0]
    assert top["touches"] == 3
    assert abs(top["level"] - (110.0 + 110.5 + 109.8) / 3) < 0.01
    # the lone 130 peak is its own weaker cluster
    lone = [c for c in swing_highs if c["level"] == 130.0]
    assert lone and lone[0]["touches"] == 1


def test_swing_levels_empty_input():
    highs, lows = find_swing_levels([], [])
    assert highs == [] and lows == []


# ─── max_drawdown / annualized_volatility ─────────────────────

def test_max_drawdown_peak_to_trough():
    # ARRANGE — peak 120 → trough 60 is a 50% drawdown; later recovery to 90
    # must not shrink it.
    assert max_drawdown([100.0, 120.0, 60.0, 90.0]) == 50.0


def test_max_drawdown_monotonic_rise_is_zero():
    assert max_drawdown([100.0, 105.0, 110.0, 120.0]) == 0.0


def test_annualized_volatility_flat_series_is_zero():
    assert annualized_volatility([100.0] * 30) == 0.0


def test_annualized_volatility_insufficient_history_returns_none():
    assert annualized_volatility([100.0, 101.0]) is None


# ─── Route contracts (hermetic — Yahoo patched) ───────────────

def test_levels_unknown_symbol_returns_404():
    # FAKESYM is not in STOCK_UNIVERSE and Yahoo (patched) has no data → 404
    with patch("services.stock_details.fetch_yahoo_quote", new_callable=AsyncMock, return_value=None):
        resp = client.get("/api/stocks/FAKESYM/levels")
    assert resp.status_code == 404


def test_levels_known_symbol_source_down_returns_unavailable():
    # RELIANCE is a known universe symbol; when the live source is down the
    # endpoint must answer 200 with an explicit available:false — never 500,
    # never simulated levels.
    with patch("services.stock_details.fetch_yahoo_quote", new_callable=AsyncMock, return_value=None):
        resp = client.get("/api/stocks/RELIANCE/levels")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["symbol"] == "RELIANCE"
    assert "note" in body


def test_financials_invalid_params_return_400():
    resp = client.get("/api/stocks/RELIANCE/financials?statement=bogus&period=annual")
    assert resp.status_code == 400
    resp = client.get("/api/stocks/RELIANCE/financials?statement=income&period=hourly")
    assert resp.status_code == 400


def test_peers_non_universe_symbol_is_graceful():
    # Peers are universe-scoped: a valid-but-unknown symbol gets an explicit
    # unavailable payload (not a 404, not an error).
    resp = client.get("/api/stocks/UNLISTEDCO/peers")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert "note" in body
