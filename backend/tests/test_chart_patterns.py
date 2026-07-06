"""Tests for chart pattern detection: services/real_market.py's
detect_chart_patterns() + individual candle detectors, and
GET /api/stocks/{symbol}/patterns.

Pattern items have the shape (confirmed by reading real_market.py directly):
    {"pattern": str, "candle_index": int, "signal": "bullish"|"bearish"|"neutral",
     "confidence": float in [0, 1], "description": str}
detect_chart_patterns() additionally stamps a "timestamp" field onto each item
and wraps them as {"symbol", "patterns", "summary", "bias", "bullish_count",
"bearish_count", "data_points"}.

Fully hermetic: the individual detector functions are pure (no I/O), and
detect_chart_patterns()/the API route are exercised with
services.real_market.fetch_yahoo_quote / detect_chart_patterns patched so no
real Yahoo Finance network call is made.
"""
import asyncio
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from server import app
from services.real_market import (
    detect_chart_patterns,
    detect_bullish_engulfing,
    detect_doji,
    detect_hammer,
    detect_shooting_star,
)

client = TestClient(app)


def test_detect_patterns_empty_list_returns_empty():
    # ARRANGE — no candle data at all
    assert detect_bullish_engulfing([], [], [], []) == []
    assert detect_doji([], [], [], []) == []

    # ACT / ASSERT — the top-level function also degrades gracefully when the
    # upstream data source returns nothing (insufficient/short data).
    async def run():
        with patch("services.real_market.fetch_yahoo_quote", new_callable=AsyncMock, return_value=None):
            return await detect_chart_patterns("NODATA")

    result = asyncio.run(run())
    assert result["patterns"] == []


def test_bullish_engulfing_detected_correctly():
    # ARRANGE — candle 0: small red body (open 10 -> close 9).
    # candle 1: larger green body (open 8.5 -> close 10.5) that fully engulfs candle 0's body.
    opens = [10.0, 8.5]
    closes = [9.0, 10.5]
    highs = [10.2, 10.6]
    lows = [8.8, 8.3]

    # ACT
    result = detect_bullish_engulfing(opens, highs, lows, closes)

    # ASSERT
    assert len(result) == 1
    pattern = result[0]
    assert pattern["pattern"] == "Bullish Engulfing"
    assert pattern["candle_index"] == 1
    assert pattern["signal"] == "bullish"
    assert 0 <= pattern["confidence"] <= 1


def test_doji_detected_correctly():
    # ARRANGE — open ~= close with a wide high/low range (body/range < 0.1)
    opens = [100.0]
    closes = [100.05]
    highs = [102.0]
    lows = [98.0]

    # ACT
    result = detect_doji(opens, highs, lows, closes)

    # ASSERT
    assert len(result) == 1
    pattern = result[0]
    assert pattern["pattern"] == "Doji"
    assert pattern["signal"] == "neutral"
    assert 0 <= pattern["confidence"] <= 1


def test_get_stock_patterns_endpoint_returns_list():
    # ARRANGE
    fake_result = {
        "symbol": "RELIANCE",
        "patterns": [{
            "pattern": "Doji", "candle_index": 5, "timestamp": "2026-01-01",
            "signal": "neutral", "confidence": 0.6, "description": "indecision",
        }],
        "summary": "1 pattern(s) detected", "bias": "Neutral",
        "bullish_count": 0, "bearish_count": 0, "data_points": 60,
    }

    # ACT
    with patch("services.real_market.detect_chart_patterns", new_callable=AsyncMock, return_value=fake_result):
        response = client.get("/api/stocks/RELIANCE/patterns")

    # ASSERT
    assert response.status_code == 200, response.text
    data = response.json()
    assert isinstance(data["patterns"], list)
    assert data["patterns"][0]["pattern"] == "Doji"


def test_pattern_confidence_between_0_and_1():
    # ARRANGE — bullish engulfing, doji, hammer, shooting star all populated
    engulfing = detect_bullish_engulfing([10.0, 8.5], [10.2, 10.6], [8.8, 8.3], [9.0, 10.5])
    doji = detect_doji([100.0], [102.0], [98.0], [100.05])
    # Hammer: small body near top of range, long lower wick, prior downtrend (needs index >= 3)
    hammer_closes = [100, 95, 90, 89]
    hammer_opens = [100, 95, 90, 88.5]
    hammer_highs = [100, 95, 90, 89.2]
    hammer_lows = [99, 94, 89, 80]
    hammer = detect_hammer(hammer_opens, hammer_highs, hammer_lows, hammer_closes)
    # Shooting star: mirror of hammer, prior uptrend
    star_closes = [80, 85, 90, 91]
    star_opens = [80, 85, 90, 91.5]
    star_highs = [80, 85, 90, 100]
    star_lows = [79, 84, 89, 90.8]
    star = detect_shooting_star(star_opens, star_highs, star_lows, star_closes)

    # ACT / ASSERT
    all_patterns = engulfing + doji + hammer + star
    assert len(all_patterns) >= 2, "expected at least engulfing + doji to be detected"
    for pattern in all_patterns:
        assert isinstance(pattern["confidence"], float)
        assert 0.0 <= pattern["confidence"] <= 1.0
