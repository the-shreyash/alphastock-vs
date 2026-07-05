# AlphaPartner — Testing Rules & Guidelines

> Load this file for any task involving running tests, writing new tests,
> debugging test failures, or understanding the test suite.

---

## Test Suite Overview

| Category | Count | Location |
|---|---|---|
| Core backend tests | 99 | `backend/` (pytest) |
| Advanced integration tests | 4 | `backend/` (pytest) |
| **Total** | **103** | All must pass at all times |

The 4 integration tests specifically cover:
- Settings update flow
- Emergency Stop execution
- Chat debate response format (Claude + Gemini + synthesis)
- SIP debate response format

---

## Running Tests

### Run all tests (standard)
```bash
cd backend
./venv/bin/python -m pytest
```

### Run with short traceback on failure
```bash
cd backend
./venv/bin/python -m pytest --tb=short
```

### Run a specific test by name
```bash
cd backend
./venv/bin/python -m pytest -k "test_name_here"
```

### Run with verbose output
```bash
cd backend
./venv/bin/python -m pytest -v
```

### Run and show only failures
```bash
cd backend
./venv/bin/python -m pytest --tb=short -q
```

---

## Test Rules (Non-Negotiable)

1. **103 tests must always pass** — after ANY backend change, run tests and verify.
2. **Never modify existing tests** to make them pass after a code change — fix the source code instead.
3. **Exception**: if a test itself has a genuine bug (tests wrong behavior), document the bug clearly before modifying the test.
4. **New features require new tests** — every new API endpoint needs at least one test.
5. **Simulated/fallback behavior must be testable** — tests should not require real API keys to pass.
6. **Frontend changes do not require backend test reruns** — only backend changes trigger test verification.

---

## Writing New Tests

### File naming
- Test files: `test_[feature_name].py` inside `backend/`
- Example: `test_paper_trading.py`, `test_activity_feed.py`

### Test function naming
```python
def test_[endpoint_or_function]_[scenario]():
    ...

# Examples:
def test_get_paper_trades_returns_empty_for_new_user():
def test_execute_paper_trade_deducts_balance():
def test_activity_feed_returns_list():
def test_morning_report_generates_on_demand():
```

### Test structure (AAA pattern)
```python
def test_feature_behavior():
    # ARRANGE — set up test data and state
    user_id = "test_user_123"
    trade_data = {"symbol": "RELIANCE", "quantity": 10, ...}

    # ACT — call the function or endpoint being tested
    response = client.post("/api/paper/trade", json=trade_data, headers=auth_headers)

    # ASSERT — verify the outcome
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["trade"]["is_paper"] == True
```

### FastAPI test client setup
```python
from fastapi.testclient import TestClient
from server import app

client = TestClient(app)

# Auth header for protected endpoints
def get_auth_headers(user_id="test_user"):
    # Use the existing test JWT generation pattern from existing test files
    token = create_test_token(user_id)
    return {"Authorization": f"Bearer {token}"}
```

### Testing async functions directly
```python
import asyncio
import pytest

@pytest.mark.asyncio
async def test_async_service_function():
    result = await some_async_service_function("param")
    assert result is not None
```

### Mocking external services
```python
from unittest.mock import patch, AsyncMock

# Mock Zerodha API
with patch("services.zerodha_service.get_holdings", new_callable=AsyncMock) as mock:
    mock.return_value = [{"symbol": "RELIANCE", "quantity": 10}]
    response = client.get("/api/zerodha/holdings", headers=auth_headers)

# Mock AI debate engine
with patch("services.ai_debate_engine.run_debate", new_callable=AsyncMock) as mock:
    mock.return_value = {"verdict": "Bullish setup", "claude": "...", "gemini": "..."}
    response = client.post("/api/chat", json={"message": "Analyze HDFC"}, headers=auth_headers)
```

---

## What to Test for Each New Feature

### Feature: AI Activity Feed
```
- GET /api/ai-activity returns a list (even if empty)
- Each item has: time, action, category, status fields
- WebSocket connection to activity_feed channel does not crash
- log_activity() appends to deque correctly
```

### Feature: Chart Pattern Detection
```
- detect_chart_patterns([]) returns empty list (not crash)
- Bullish engulfing detected correctly with test OHLCV data
- GET /api/stocks/{symbol}/patterns returns list
- Each pattern has: pattern, signal, confidence, description fields
```

### Feature: Paper Trading
```
- GET /api/paper/balance returns default 100000 for new user
- POST /api/paper/trade succeeds with valid data
- POST /api/paper/trade deducts from paper_capital
- Closed paper trades appear in GET /api/paper/trades
- POST /api/paper/reset restores capital to 100000
- Paper trades have is_paper=True in DB
- Paper trades NEVER call zerodha_service functions
```

### Feature: Historical Setup Success Rate
```
- GET /api/journal/setup-stats returns dict
- Win rate calculation correct: 3 wins / 5 trades = 60%
- Returns demo data (not crash) when no trades exist
- setup_type field accepted in POST /api/trades
```

### Feature: Backtesting Engine
```
- POST /api/backtest returns result with all expected fields
- win_rate is between 0 and 100
- equity_curve is a list of {date, capital} objects
- Returns fallback data when yfinance unavailable (not crash)
- Sharpe ratio is a float (can be negative)
```

### Feature: AI Trade Coaching
```
- GET /api/trades/{trade_id}/coaching returns coaching object
- Coaching has: grade, lesson_title, what_went_right, what_went_wrong, next_time
- Grade is one of: A, B, C, D
- Coaching is cached — second call returns same result without re-generating
- Returns 400 for open trades (coaching only for closed trades)
```

### Feature: Morning Report
```
- GET /api/reports/morning returns report object
- Report has: date, market_mood, nifty, banknifty, sensex, ai_briefing, top_picks
- market_mood is one of: Bullish, Bearish, Neutral, Cautious
- Report cached for same date — second call returns cached version
- Returns report even when stock picks not yet generated (top_picks: [])
```

### Feature: n8n Webhooks
```
- POST /api/webhooks/morning-scan returns {"status": "ok"}
- POST /api/webhooks/evening-summary returns {"status": "ok"}
- POST /api/webhooks/weekly-review returns {"status": "ok"}
- POST /api/webhooks/news-digest returns {"status": "ok"}
- Webhook without correct API key header returns 403
```

---

## Debugging Failing Tests

When a test fails after a code change, follow this sequence:

1. Read the full error message — identify which assertion failed.
2. Check if the change altered a route path, response format, or field name.
3. Check if a new required field was added to a Pydantic model that existing test data doesn't have.
4. Check if an async function is not being awaited properly.
5. Check if a MongoDB operation changed and the test mock needs updating.

### Common failure patterns

**Pattern: `KeyError` or `assert 'field' in response`**
→ Response format changed. Align new code response with what test expects.

**Pattern: `422 Unprocessable Entity` in test**
→ Pydantic validation failed. Check if request body in test matches updated model.

**Pattern: `RuntimeWarning: coroutine was never awaited`**
→ New async function called without `await`. Add `await` or use `AsyncMock` in test.

**Pattern: `AssertionError: 103 != 99` (wrong count)**
→ New tests added but some new tests failing. Fix new tests first, then verify 103+ pass.

---

## Test Environment Notes

- Tests run without real API keys — all external services use simulated fallbacks.
- MongoDB: tests use the same database unless a test fixture creates isolated collections.
- `REACT_APP_BACKEND_URL=http://localhost:8000` must be set when running tests.
- `venv/bin/python` must be used — not system Python — to ensure correct packages.
