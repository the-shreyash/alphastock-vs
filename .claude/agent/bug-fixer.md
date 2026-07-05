# AlphaPartner — Bug Fixer Agent Skill

> Load this skill when debugging errors, fixing failing tests, or
> investigating unexpected behavior in AlphaPartner.

---

## Bug Fix Workflow

```
STEP 1 → REPRODUCE — understand exactly what fails and when
STEP 2 → LOCATE — find the exact file, function, and line causing the issue
STEP 3 → DIAGNOSE — understand root cause before touching any code
STEP 4 → FIX — minimal targeted change only
STEP 5 → VERIFY — run tests to confirm fix works and nothing else broke
```

Never jump to Step 4 without completing Steps 1-3 first.

---

## Step 1: Reproduce the Bug

Before reading any code, collect all available information:

```
- What is the exact error message or unexpected behavior?
- Which endpoint, component, or function is failing?
- Does it fail consistently or intermittently?
- Does it fail in backend, frontend, or both?
- What was the last code change before this started?
```

If it's a backend error:
```bash
# Check backend logs
cd backend && ./venv/bin/python -m uvicorn server:app --port 8000
# Then trigger the failing action and read the traceback

# If it's a test failure:
cd backend && ./venv/bin/python -m pytest -k "failing_test_name" --tb=long -v
```

---

## Step 2: Locate the Bug

### For backend errors

**FastAPI 422 Unprocessable Entity**
→ Pydantic validation failed. Check request body matches the Pydantic model in `models.py`

**FastAPI 500 Internal Server Error**
→ Unhandled exception in route handler. Check the traceback for exact line.

**`RuntimeWarning: coroutine was never awaited`**
→ Async function called without `await`. Search for the function name and add `await`.

**`AttributeError: 'coroutine' object has no attribute...`**
→ Same as above — missing `await` on async function.

**Motor / MongoDB errors**
```bash
# Check if MongoDB is running
mongosh --eval "db.adminCommand('ping')"

# Check Motor connection in server.py startup
grep -n "motor\|MongoClient\|motor_asyncio" backend/server.py
```

**Import errors**
```bash
cd backend && ./venv/bin/python -c "from server import app" 2>&1
```

### For frontend errors

**White screen / nothing renders**
→ Check browser console for JS errors. Usually a component throwing during render.

**API call failing**
```javascript
// Check network tab in browser devtools
// Look for the failing request URL, status code, and response body
```

**`Cannot read property X of undefined`**
→ Data not loaded yet when component tries to access it. Add optional chaining or loading check.

**WebSocket not connecting**
→ Check if backend WebSocket endpoint is registered in server.py

---

## Step 3: Diagnose Root Cause

### Common AlphaPartner-specific bug patterns

**Bug: Async function used synchronously**
```python
# WRONG
result = some_async_function()  # returns coroutine, not result
data = result["key"]  # AttributeError: 'coroutine' has no attribute

# RIGHT
result = await some_async_function()
data = result["key"]
```

**Bug: PyMongo sync used instead of Motor async**
```python
# WRONG — blocks event loop
from pymongo import MongoClient
db = MongoClient()["alpha_stock_db"]
user = db.users.find_one({"_id": user_id})

# RIGHT — non-blocking
from motor.motor_asyncio import AsyncIOMotorClient
# (already set up in server.py — use the db object from there)
user = await db.users.find_one({"_id": user_id})
```

**Bug: ObjectId not serialized to JSON**
```python
# WRONG — MongoDB ObjectId can't be JSON serialized
return {"user": user_doc}  # crashes with ObjectId error

# RIGHT
user_doc["_id"] = str(user_doc["_id"])
return {"user": user_doc}
```

**Bug: Motor cursor not properly iterated**
```python
# WRONG
trades = db.trades.find({"user_id": user_id})  # returns cursor
for trade in trades:  # sync iteration of async cursor

# RIGHT
trades = []
async for trade in db.trades.find({"user_id": user_id}):
    trades.append(trade)
```

**Bug: React useEffect infinite loop**
```javascript
// WRONG — object/array in dependency array creates new reference each render
useEffect(() => {
  fetchData();
}, [someObject]);  // infinite loop if someObject is recreated each render

// RIGHT — use primitive values or useMemo
useEffect(() => {
  fetchData();
}, [someObject.id]);  // use a stable primitive
```

**Bug: React state mutation**
```javascript
// WRONG
const newTrades = trades;
newTrades.push(newTrade);  // mutates original state
setTrades(newTrades);  // React won't re-render

// RIGHT
setTrades([...trades, newTrade]);
```

**Bug: Frontend calling wrong API path**
```javascript
// WRONG — hardcoded path might differ from actual route
const res = await api.get('/trades/paper');

// RIGHT — check server.py for exact route path
const res = await api.get('/api/paper/trades');
```

---

## Step 4: Fix Guidelines

- Make the **smallest possible change** that fixes the bug.
- Do NOT refactor unrelated code while fixing a bug.
- Do NOT add new features while fixing a bug.
- Add a comment explaining WHY the fix was needed if it's non-obvious.
- If the fix changes a function signature, check all callers.

---

## Step 5: Verify the Fix

```bash
# Always run full test suite after ANY backend fix
cd backend && ./venv/bin/python -m pytest --tb=short

# Expected: all 103 pass
# If new failures appear → the fix broke something else → re-examine

# For frontend fixes — manually test the affected page in browser
# Check browser console for new errors
```

---

## Quick Reference: Where Things Are

| Thing to find | Where to look |
|---|---|
| All API routes | `backend/server.py` |
| Database models | `backend/models.py` |
| AI debate logic | `backend/services/ai_debate_engine.py` |
| Market data fetch | `backend/services/real_market.py` |
| Zerodha integration | `backend/services/zerodha_service.py` |
| Background jobs | `backend/services/scheduler.py` |
| Auth / JWT | `backend/server.py` (auth router section) |
| Frontend routing | `frontend/src/App.js` |
| API client config | `frontend/src/context/services/api.js` |
| Design tokens | `frontend/src/index.css` |
| Auth state | `frontend/src/context/AuthContext.jsx` |
| Dashboard page | `frontend/src/pages/Dashboard.jsx` |
| Stock picks page | `frontend/src/pages/StockPicks.jsx` |
