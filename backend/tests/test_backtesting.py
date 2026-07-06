"""Tests for services/backtest_engine.py's run_backtest() and POST /api/backtest.

`yfinance` is not installed in this project's venv, so run_backtest() always
falls through to its synthetic-data fallback today; test_backtest_returns_
fallback_without_yfinance additionally forces `import yfinance` to raise
ImportError via a `sys.modules` patch so the assertion holds regardless of
whether yfinance happens to be installed in a given environment.

The /api/backtest route requires no auth and touches no DB (confirmed by
reading server.py), so it is exercised directly via TestClient(app).
"""
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient

from server import app

client = TestClient(app)

PAYLOAD = {
    "symbol": "RELIANCE",
    "start_date": "2025-01-01",
    "end_date": "2025-06-01",
    "strategy": "RSI_STRATEGY",
    "stop_loss_pct": 2.0,
    "target_pct": 4.0,
    "initial_capital": 100000.0,
}


def test_backtest_returns_result_object():
    # ACT
    response = client.post("/api/backtest", json=PAYLOAD)

    # ASSERT
    assert response.status_code == 200, response.text
    assert isinstance(response.json(), dict)


def test_backtest_result_has_all_required_fields():
    # ACT
    response = client.post("/api/backtest", json=PAYLOAD)

    # ASSERT
    data = response.json()
    required = [
        "symbol", "strategy", "period", "initial_capital", "final_capital",
        "data_source", "total_trades", "winning_trades", "losing_trades",
        "win_rate", "total_return_pct", "max_drawdown_pct", "best_trade_pct",
        "worst_trade_pct", "avg_trade_pct", "sharpe_ratio", "trades", "equity_curve",
    ]
    for field in required:
        assert field in data, f"missing '{field}' in backtest result: {data}"


def test_win_rate_between_0_and_100():
    # ACT
    response = client.post("/api/backtest", json=PAYLOAD)

    # ASSERT
    win_rate = response.json()["win_rate"]
    assert 0 <= win_rate <= 100


def test_equity_curve_is_list_of_date_capital_objects():
    # ACT
    response = client.post("/api/backtest", json=PAYLOAD)

    # ASSERT
    equity_curve = response.json()["equity_curve"]
    assert isinstance(equity_curve, list)
    assert len(equity_curve) > 0
    for point in equity_curve:
        assert "date" in point and "capital" in point


def test_backtest_returns_fallback_without_yfinance():
    # ARRANGE — force `import yfinance` to raise ImportError regardless of
    # whether the package happens to be installed in this environment.
    with patch.dict(sys.modules, {"yfinance": None}):
        # ACT
        response = client.post("/api/backtest", json=PAYLOAD)

    # ASSERT
    assert response.status_code == 200, response.text
    assert response.json()["data_source"] == "synthetic"
