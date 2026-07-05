# AlphaPartner — Feature Builder Agent Skill

> This agent skill is loaded when building any of the 8 missing features.
> Always read CLAUDE.md first, then follow the workflow below exactly.

---

## Feature Builder Workflow (Follow Every Time)

```
STEP 1 → READ existing files relevant to the feature
STEP 2 → DECIDE: already built or genuinely missing?
STEP 3 → BUILD only the missing pieces
STEP 4 → WRITE tests for new code
STEP 5 → RUN full test suite (103 must pass)
STEP 6 → SUMMARIZE what was built
```

Never skip Step 1 and Step 2. Rebuilding something that already exists wastes
time and risks breaking existing functionality.

---

## Feature 1 — AI Activity Feed

### Check these files first
```bash
cat backend/server.py | grep -A5 "ai-activity\|activity_feed\|activity-feed"
ls backend/services/ | grep activity
cat frontend/src/pages/Dashboard.jsx | grep -i "activity\|ActivityFeed"
```

### Skip if
All three exist: `/api/ai-activity` route + `activity_logger.py` service + ActivityFeed UI in Dashboard.

### Build spec

**backend/services/activity_logger.py** (new file):
- `from collections import deque` — `_activity_log = deque(maxlen=50)`
- Each entry: `{ "time": "HH:MM:SS", "action": str, "category": "scan|news|alert|rank|monitor", "status": "running|done|warning" }`
- `log_activity(action, category="scan", status="done")` — appends entry + broadcasts via WebSocket to channel `"activity_feed"`
- `get_recent_activity()` — returns last 20 entries as list
- Pre-populate on module load with 5 realistic startup log entries

**backend/server.py** additions:
- `GET /api/ai-activity` → returns `get_recent_activity()`
- WebSocket: on new `log_activity()` call, push entry to all connected clients on `"activity_feed"` channel
- Inject `log_activity()` calls in:
  - `scheduler.py` morning scan: `"Scanning NSE top gainers"` / `category="scan"`
  - `real_market.py` fetch: `"Fetching live market data"` / `category="scan"`
  - `ai_debate_engine.py`: `f"Running AI debate for {symbol}"` / `category="rank"`
  - `portfolio_monitor.py`: `f"Monitoring trade: {symbol}"` / `category="monitor"`
  - `telegram_service.py` send: `"Sending Telegram alert"` / `category="alert"`

**frontend/src/pages/Dashboard.jsx** additions:
- New `ActivityFeed` component (can be in same file or separate `components/ActivityFeed.jsx`)
- Positioned: right sidebar panel or bottom section of Dashboard
- Title: "AI Activity" with green pulsing dot
- Shows last 20 entries, newest first
- Category badge colors: scan=blue, news=purple, rank=amber, monitor=teal, alert=red
- WebSocket subscribe to `"activity_feed"` — append new entries in real-time
- Fallback: poll `GET /api/ai-activity` every 10 seconds if WebSocket unavailable
- Never empty — show simulated entries if no real data

### Tests to write (test_activity_feed.py)
```python
test_get_activity_feed_returns_list()
test_activity_feed_entries_have_required_fields()
test_log_activity_appends_to_deque()
test_activity_feed_max_50_entries()
```

---

## Feature 2 — Morning Report UI

### Check these files first
```bash
cat backend/server.py | grep -A5 "morning\|reports"
ls frontend/src/pages/ | grep -i morning
cat backend/scheduler.py | grep -i morning
```

### Skip if
`/api/reports/morning` route exists AND `MorningReport.jsx` page exists.

### Build spec

**backend/server.py** additions:
- `GET /api/reports/morning` (JWT protected)
- Check MongoDB `reports` collection for today's report (keyed by date)
- If found → return cached
- If not → generate on-demand:
  - Fetch live market data from `real_market.py`
  - Get today's top 3 picks (call existing stock picks logic)
  - Ask `ai_debate_engine` for 200-word morning briefing
  - Store in MongoDB `reports` collection
  - Return report
- Report shape:
  ```json
  {
    "date": "YYYY-MM-DD",
    "market_mood": "Bullish|Bearish|Neutral|Cautious",
    "mood_score": -1.0,
    "nifty": {"value": 0.0, "change_pct": 0.0},
    "banknifty": {"value": 0.0, "change_pct": 0.0},
    "sensex": {"value": 0.0, "change_pct": 0.0},
    "ai_briefing": "string",
    "top_picks": [],
    "key_risks": ["risk1", "risk2", "risk3"],
    "global_cues": "string",
    "fii_dii": {"fii_net": 0.0, "dii_net": 0.0},
    "generated_at": "ISO datetime"
  }
  ```

**frontend/src/pages/MorningReport.jsx** (new file):
- Header: date + "Morning Briefing"
- Market Mood banner (full width, color-coded)
- 3 index cards: Nifty / BankNifty / Sensex
- AI Briefing blockquote card
- Top 3 Picks (read-only — no trade button)
- Key Risks (red-tinted warning card, 3 bullets)
- Global Cues (blue-tinted card)
- FII/DII mini table
- "Refresh" button

**frontend/src/App.js**: add `/morning-report` route

### Tests to write (test_morning_report.py)
```python
test_morning_report_returns_report_object()
test_morning_report_has_required_fields()
test_morning_report_mood_is_valid_value()
test_morning_report_cached_on_second_call()
```

---

## Feature 3 — Paper Trading Mode

### Check these files first
```bash
cat backend/server.py | grep -A5 "paper"
cat backend/models.py | grep -i "paper\|is_paper"
ls frontend/src/pages/ | grep -i paper
```

### Skip if
`/api/paper/trade` route + `is_paper` field in models + `PaperTrading.jsx` page all exist.

### Build spec

**backend/models.py** additions:
- Add to Trade model: `is_paper: bool = False`
- Add to Trade model: `setup_type: Optional[str] = None`

**backend/services/paper_trade.py** (new file):
- `get_paper_balance(user_id)` → reads `paper_capital` from users collection (default 100000)
- `update_paper_balance(user_id, amount)` → Motor `update_one` upsert
- `get_paper_trades(user_id)` → all trades where `is_paper=True`
- `get_paper_pnl(user_id)` → total realized + unrealized P&L
- `execute_paper_trade(user_id, symbol, quantity, price, type, stop_loss, target1, setup_type)` → insert trade with `is_paper=True`, deduct/add to `paper_capital`
- **NEVER import or call anything from `zerodha_service.py`**

**backend/server.py** additions:
```
GET  /api/paper/balance
GET  /api/paper/trades
GET  /api/paper/pnl
POST /api/paper/trade
POST /api/paper/close/{trade_id}
POST /api/paper/reset
```

**frontend/src/pages/PaperTrading.jsx** (new file):
- "SIMULATED" yellow badge in header
- Paper balance card + P&L card
- "New Paper Trade" button → modal with all trade fields
- Open paper trades table with Close button
- Closed paper trades table
- Reset Capital button with confirmation

**frontend/src/App.js**: add `/paper-trading` route

### Tests to write (test_paper_trading.py)
```python
test_get_paper_balance_returns_100000_default()
test_execute_paper_trade_creates_trade_with_is_paper_true()
test_execute_paper_trade_deducts_from_balance()
test_paper_trade_never_calls_zerodha()
test_reset_paper_capital_restores_100000()
test_close_paper_trade_calculates_pnl()
```

---

## Feature 4 — Chart Pattern Detection

### Check these files first
```bash
cat backend/services/real_market.py | grep -i "pattern\|engulf\|doji\|hammer"
cat backend/server.py | grep -A3 "patterns"
cat frontend/src/pages/StockDetail.jsx | grep -i "pattern"
```

### Skip if
`detect_chart_patterns()` in real_market.py + `/api/stocks/{symbol}/patterns` route + pattern badges in StockDetail.jsx all exist.

### Build spec

**backend/services/real_market.py** additions:
New function `detect_chart_patterns(ohlcv_data: list) -> list`:
- Detect: Bullish Engulfing, Bearish Engulfing, Doji, Hammer, Shooting Star, Double Top, Double Bottom
- Each result: `{ "pattern": str, "candle_index": int, "timestamp": str, "signal": "bullish|bearish|neutral", "confidence": float, "description": str }`
- Works on empty list (returns `[]`) — never crash

**backend/server.py** additions:
- `GET /api/stocks/{symbol}/patterns` → fetch last 50 candles → run `detect_chart_patterns()` → return list

**frontend/src/pages/StockDetail.jsx** additions:
- "Patterns Detected" section below chart
- Colored badges per pattern (green=bullish, red=bearish, gray=neutral)
- Click badge → tooltip/modal with description
- "No patterns detected" empty state

**frontend/src/pages/StockPicks.jsx** additions:
- Add "Patterns" row to each stock card showing top 1-2 detected patterns

### Tests to write (test_chart_patterns.py)
```python
test_detect_patterns_empty_list_returns_empty()
test_bullish_engulfing_detected_correctly()
test_doji_detected_correctly()
test_get_stock_patterns_endpoint_returns_list()
test_pattern_confidence_between_0_and_1()
```

---

## Feature 5 — Historical Setup Success Rate

### Check these files first
```bash
cat backend/services/trade_journal.py | grep -i "setup\|success_rate\|win_rate"
cat backend/server.py | grep -A3 "setup-stats"
cat frontend/src/pages/StockPicks.jsx | grep -i "success\|win.rate\|historical"
```

### Skip if
`get_setup_success_rates()` in trade_journal.py + `/api/journal/setup-stats` route + bar chart in StockPicks all exist.

### Build spec

**backend/services/trade_journal.py** additions:
New function `get_setup_success_rates(user_id: str) -> dict`:
- Query all closed trades for user
- Group by `setup_type`
- Per group: `total_trades, winning_trades, win_rate, avg_pnl_percent, best_trade_pnl, worst_trade_pnl`
- Return demo data when no trades (so UI is never empty)

**backend/server.py** additions:
- `GET /api/journal/setup-stats` (JWT protected)

**frontend/src/pages/StockPicks.jsx** additions:
- "Setup Performance History" section with Recharts BarChart
- Bar colors: green >60%, amber 40-60%, red <40%
- Hover tooltip: "X trades | Y% win | avg +Z%"

### Tests to write (test_setup_stats.py)
```python
test_setup_stats_returns_dict()
test_win_rate_calculation_correct()
test_setup_stats_returns_demo_when_no_trades()
test_setup_type_accepted_in_trade_creation()
```

---

## Feature 6 — AI Trade Coaching

### Check these files first
```bash
cat backend/services/trade_journal.py | grep -i "coach\|lesson\|grade"
cat backend/server.py | grep -A3 "coaching"
cat frontend/src/pages/TradeJournal.jsx | grep -i "coach\|lesson\|grade"
```

### Skip if
`generate_trade_coaching()` + `/api/trades/{id}/coaching` route + coaching modal in TradeJournal.jsx all exist.

### Build spec

**backend/services/trade_journal.py** additions:
New async function `generate_trade_coaching(trade: dict) -> dict`:
- Build AI prompt from trade data
- Call `ai_debate_engine` with coaching prompt
- Return: `{ trade_id, coaching_text, lesson_title, grade (A/B/C/D), grade_reason, what_went_right, what_went_wrong, next_time }`
- Cache result in trades collection under `"coaching"` key
- Use `FastAPI BackgroundTasks` — never block trade close response

**backend/server.py** additions:
- `GET /api/trades/{trade_id}/coaching` → return cached or generate
- `GET /api/trades/coaching/summary` → last 5 lessons for dashboard widget
- On trade close → trigger `generate_trade_coaching()` as background task

**frontend updates**:
- TradeJournal.jsx: "View Coaching" button per closed trade → drawer/modal
- Dashboard.jsx: "Latest AI Lessons" widget (last 3 lesson titles + grades)
- TradeMonitor.jsx: "Live Coaching Tip" per open trade (40-word tip, refresh every 5 min)

### Tests to write (test_trade_coaching.py)
```python
test_coaching_endpoint_returns_coaching_object()
test_coaching_has_valid_grade()
test_coaching_cached_on_second_call()
test_coaching_rejected_for_open_trade()
test_coaching_summary_returns_list()
```

---

## Feature 7 — Backtesting Engine

### Check these files first
```bash
ls backend/services/ | grep backtest
cat backend/server.py | grep -A3 "backtest"
ls frontend/src/pages/ | grep -i backtest
```

### Skip if
`backtest_engine.py` + `/api/backtest` route + `Backtesting.jsx` page all exist.

### Build spec

**backend/services/backtest_engine.py** (new file):
- `BacktestEngine.run_backtest(params)` → strategies: RSI_STRATEGY, EMA_CROSSOVER, VWAP_REVERSION, MACD_SIGNAL
- Fetch historical OHLCV via `yfinance` (`pip install yfinance`) — symbol format: `SYMBOL.NS`
- Simulation loop: apply strategy signal per candle, execute at next open
- Return: `{ symbol, strategy, period, total_trades, winning_trades, losing_trades, win_rate, total_return_pct, max_drawdown_pct, best_trade_pct, worst_trade_pct, avg_trade_pct, sharpe_ratio, trades: [], equity_curve: [] }`
- Fallback simulated data if yfinance unavailable
- Results are stateless — do NOT store in MongoDB

**backend/server.py** additions:
- `POST /api/backtest` → request body: symbol, start_date, end_date, strategy, stop_loss_pct, target_pct, initial_capital

**frontend/src/pages/Backtesting.jsx** (new file):
- Config panel: symbol, strategy, date range, stop loss %, target %, capital
- "Run Backtest" button with loading state
- Results: 4 metric cards + Recharts LineChart equity curve + trades table

**frontend/src/App.js**: add `/backtesting` route

### Tests to write (test_backtesting.py)
```python
test_backtest_returns_result_object()
test_backtest_result_has_all_required_fields()
test_win_rate_between_0_and_100()
test_equity_curve_is_list_of_date_capital_objects()
test_backtest_returns_fallback_without_yfinance()
```

---

## Feature 8 — n8n Automation Workflows

### Check these files first
```bash
ls n8n/ 2>/dev/null || echo "n8n folder missing"
cat backend/server.py | grep -A3 "webhook"
cat docker-compose.yml 2>/dev/null || echo "docker-compose missing"
```

### Skip if
`n8n/` folder + webhook routes + docker-compose all exist.

### Build spec

**docker-compose.yml** (create at project root or add service):
- n8n service on port 5678
- Basic auth: admin / alphapartner123
- Persistent volume

**backend/server.py** additions (webhook endpoints — no JWT, use API key header):
```
POST /api/webhooks/morning-scan
POST /api/webhooks/evening-summary
POST /api/webhooks/weekly-review
POST /api/webhooks/news-digest
```
All call existing service functions. Add `WEBHOOK_API_KEY` env var check.

**n8n/ folder** (4 JSON workflow files + README.md):
- `morning_scan_workflow.json` — Schedule: 08:55 AM IST Mon-Fri
- `evening_summary_workflow.json` — Schedule: 03:35 PM IST Mon-Fri
- `weekly_review_workflow.json` — Schedule: Sunday 10:00 AM IST
- `news_digest_workflow.json` — Schedule: 09:30 AM + 01:00 PM IST Mon-Fri
- `README.md` — setup instructions

**frontend/src/pages/Settings.jsx** additions:
- n8n Automation status row in Connected Services section
- Last run times for each workflow (from MongoDB `webhook_logs` collection)

### Tests to write (test_webhooks.py)
```python
test_morning_scan_webhook_returns_ok()
test_evening_summary_webhook_returns_ok()
test_weekly_review_webhook_returns_ok()
test_news_digest_webhook_returns_ok()
test_webhook_without_api_key_returns_403()
```

---

## After Every Feature Build

```bash
# 1. Run full test suite
cd backend && ./venv/bin/python -m pytest --tb=short

# 2. Verify count
# Expected output: X passed (must be >= 103, no failures)

# 3. If any existing test fails → fix source code, NOT the test

# 4. Report what was built:
# - New files created
# - Existing files modified
# - New API routes added
# - New frontend pages/components added
# - Tests written and passing
```
