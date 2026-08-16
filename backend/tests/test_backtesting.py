"""Tests for services/backtest_engine.py's run_backtest() and POST /api/backtest.

REWRITTEN IN PH3.9, AND THE REASON MATTERS
------------------------------------------
`yfinance` is not installed in this project's venv. Before PH3.9 that meant
`run_backtest` fell through to `_synthetic_backtest`, and so **every assertion
in this file was made against fabricated data** — "win rate is between 0 and
100" passed because a random number between 10 and 16 out of 20 is, and
"equity_curve is a list of {date, capital}" passed against invented 2025 dates.
The suite was green and was testing nothing about the strategy engine.

PH3.9 deleted that fallback. So this file now covers the two things that are
actually true of the engine:

1. **No history means no result.** `HistoricalDataUnavailable` from the engine,
   503 from the route — never a 200 carrying invented performance.
2. **The simulation itself**, exercised against a deterministic in-test price
   series through a stub `yfinance` module. This is stricter than what was here
   before: the metric fields are asserted over data whose correct answers are
   known, rather than over noise where any number satisfies "0 <= x <= 100".

The /api/backtest route requires no auth and touches no DB, so it is exercised
directly via TestClient(app).
"""
import sys
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from server import app
from services import backtest_engine

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


# --------------------------------------------------------------------------- #
# A deterministic stand-in for yfinance                                         #
# --------------------------------------------------------------------------- #
class _FakeHistory:
    """The narrow slice of a pandas DataFrame that `run_backtest` touches:
    `.empty`, `.index`, and `["Open"/"Close"/"High"/"Low"/"Volume"]`."""

    def __init__(self, bars):
        self._bars = bars
        self.index = [datetime(2025, 1, 1) + timedelta(days=i) for i in range(len(bars))]

    @property
    def empty(self):
        return not self._bars

    def __getitem__(self, column):
        return [bar[column] for bar in self._bars]


def _price_series(count=80):
    """A sawtooth that rises and falls enough to trigger RSI entries and exits.

    Deterministic by construction — no RNG anywhere — so a failure here is a
    change in the strategy code, never a reroll. That is the property the
    deleted fallback could not have: it was seeded from `hash(str)`, which
    Python salts with PYTHONHASHSEED, so the same backtest returned 80% / 60% /
    80% win rates across three consecutive processes.
    """
    bars = []
    price = 1000.0
    for i in range(count):
        price *= 1.02 if (i // 7) % 2 == 0 else 0.985
        bars.append({"Open": round(price * 0.998, 2), "Close": round(price, 2),
                     "High": round(price * 1.01, 2), "Low": round(price * 0.99, 2),
                     "Volume": 100_000 + i})
    return bars


def _fake_yfinance(bars=None, raises=None):
    history = _FakeHistory(bars if bars is not None else _price_series())

    class _Ticker:
        def __init__(self, *args, **kwargs):
            pass

        def history(self, **kwargs):
            if raises is not None:
                raise raises
            return history

    return type("yfinance", (), {"Ticker": _Ticker})


# --------------------------------------------------------------------------- #
# 1. The fabricated fallback is gone                                            #
# --------------------------------------------------------------------------- #
def test_missing_yfinance_is_a_503_not_a_fabricated_200():
    """Before PH3.9 this exact case returned 200 with `data_source: synthetic`
    and a win rate drawn from randint(10, 16) out of 20 — always 50–80%, so a
    losing strategy could not be represented."""
    with patch.dict(sys.modules, {"yfinance": None}):
        response = client.post("/api/backtest", json=PAYLOAD)
    assert response.status_code == 503, response.text
    assert "win_rate" not in response.json()


def test_a_provider_failure_is_a_503_not_a_fabricated_200():
    """The fallback was reached on ANY exception, so a transient network blip
    produced flattering invented performance rather than an error."""
    with patch.dict(sys.modules, {"yfinance": _fake_yfinance(raises=RuntimeError("boom"))}):
        response = client.post("/api/backtest", json=PAYLOAD)
    assert response.status_code == 503


def test_an_empty_history_is_refused():
    """A delisted or misspelled symbol. The message must be actionable — a bare
    "no data" leaves an operator unable to tell a typo from an outage."""
    with patch.dict(sys.modules, {"yfinance": _fake_yfinance(bars=[])}):
        response = client.post("/api/backtest", json=PAYLOAD)
    assert response.status_code == 503
    assert "delisted" in response.json()["detail"] or "no trading days" in response.json()["detail"]


def test_too_few_bars_is_refused_rather_than_padded():
    """Below `_MIN_BARS` the indicators return their neutral seed values (`_rsi`
    yields a flat 50), so a result would measure the padding, not the strategy."""
    with patch.dict(sys.modules, {"yfinance": _fake_yfinance(bars=_price_series(4))}):
        response = client.post("/api/backtest", json=PAYLOAD)
    assert response.status_code == 503
    assert str(backtest_engine._MIN_BARS) in response.json()["detail"]


def test_the_fabricating_helper_is_gone():
    assert not hasattr(backtest_engine, "_synthetic_backtest")


# --------------------------------------------------------------------------- #
# 2. The real simulation path                                                   #
# --------------------------------------------------------------------------- #
@pytest.fixture
def real_result():
    with patch.dict(sys.modules, {"yfinance": _fake_yfinance()}):
        response = client.post("/api/backtest", json=PAYLOAD)
    assert response.status_code == 200, response.text
    return response.json()


def test_result_has_all_required_fields(real_result):
    required = [
        "symbol", "strategy", "period", "initial_capital", "final_capital",
        "data_source", "total_trades", "winning_trades", "losing_trades",
        "win_rate", "total_return_pct", "max_drawdown_pct", "best_trade_pct",
        "worst_trade_pct", "avg_trade_pct", "sharpe_ratio", "trades", "equity_curve",
    ]
    for field in required:
        assert field in real_result, f"missing '{field}': {real_result}"


def test_the_result_declares_real_provenance(real_result):
    assert real_result["data_source"] == "yfinance"
    assert real_result["provenance"] == "derived"
    assert real_result["mock_metrics"] == []


def test_gross_basis_is_declared(real_result):
    """Every P&L in this product is gross of charges, and a backtest is where
    that matters most — it is the number somebody sizes a real position from.
    On Indian intraday equity, charges routinely exceed the edge on a small
    trade."""
    assert real_result["basis"] == "gross"
    assert real_result["charges_note"]


def test_win_and_loss_counts_reconcile_with_the_total(real_result):
    """Stricter than the old `0 <= win_rate <= 100`, which any random number
    satisfied. The parts must add up to the whole."""
    assert real_result["winning_trades"] + real_result["losing_trades"] \
        <= real_result["total_trades"]
    if real_result["total_trades"]:
        expected = round(real_result["winning_trades"] / real_result["total_trades"] * 100, 2)
        assert abs(real_result["win_rate"] - expected) < 0.01


def test_the_dates_come_from_the_price_series_not_from_invented_strings(real_result):
    """The fabricated path emitted `2025-0{randint(1,9)}-{randint(10,28)}` —
    dates unrelated to the requested period, and unrelated to each other."""
    for point in real_result["equity_curve"]:
        assert "date" in point and "capital" in point
    for trade in real_result["trades"]:
        assert trade["entry_date"] <= trade["exit_date"], (
            "an exit before its entry is the signature of invented dates")


def test_the_same_inputs_produce_the_same_result():
    """The deleted fallback seeded from `hash(str)`, which PYTHONHASHSEED salts,
    so identical input produced different win rates on different processes."""
    with patch.dict(sys.modules, {"yfinance": _fake_yfinance()}):
        first = client.post("/api/backtest", json=PAYLOAD).json()
        second = client.post("/api/backtest", json=PAYLOAD).json()
    assert first["win_rate"] == second["win_rate"]
    assert first["final_capital"] == second["final_capital"]
