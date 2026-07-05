# AlphaPartner — Refactor Agent Skill

> Load this skill when improving existing code quality, reducing duplication,
> or restructuring without changing behavior.

---

## Refactor Rules (Non-Negotiable)

1. **Tests must pass before AND after** — run `pytest` before starting, confirm 103 pass. Run again after. Zero regressions allowed.
2. **Never change behavior** — refactoring means same output, cleaner code.
3. **One concern at a time** — don't refactor + add features in same session.
4. **Small steps** — refactor one function or file at a time. Verify tests after each step.
5. **Keep MongoDB schemas unchanged** — field names, types, and defaults must not change.

---

## Safe Refactor Targets in AlphaPartner

### Backend

**Duplicate error handling**
Many routes likely have repeated try/except blocks.
Extract to a decorator or utility:
```python
# backend/services/utils.py
from functools import wraps
import logging

def handle_service_errors(fallback=None):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logging.error(f"{func.__name__} failed: {e}")
                return fallback
        return wrapper
    return decorator
```

**Repeated ObjectId string conversion**
```python
# Extract to utility
def serialize_doc(doc: dict) -> dict:
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc
```

**Repeated JWT user extraction**
If many routes repeat JWT parsing — consolidate into a shared dependency.

**Long server.py**
If `server.py` becomes very large, routers can be extracted:
```python
# backend/routers/paper_trading.py
from fastapi import APIRouter
router = APIRouter(prefix="/api/paper", tags=["paper"])

# Then in server.py:
from routers.paper_trading import router as paper_router
app.include_router(paper_router)
```

### Frontend

**Repeated API call + loading state pattern**
Many components likely have:
```javascript
const [data, setData] = useState(null);
const [loading, setLoading] = useState(true);
useEffect(() => {
  api.get('/api/something').then(res => {
    setData(res.data);
    setLoading(false);
  });
}, []);
```
Extract to a custom hook:
```javascript
// frontend/src/hooks/useApi.js
export function useApi(url) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  useEffect(() => {
    api.get(url)
      .then(res => setData(res.data))
      .catch(err => setError(err))
      .finally(() => setLoading(false));
  }, [url]);
  return { data, loading, error };
}
```

**Repeated card/skeleton markup**
If glassmorphic card markup is copy-pasted across pages — extract to `components/Card.jsx`.

**Repeated loading skeleton**
Extract to `components/LoadingSkeleton.jsx`.

---

## Refactor Workflow

```bash
# Step 1: Confirm tests pass before starting
cd backend && ./venv/bin/python -m pytest --tb=short
# Must see: 103 passed

# Step 2: Make one targeted refactor change

# Step 3: Run tests again
cd backend && ./venv/bin/python -m pytest --tb=short
# Must still see: 103 passed (or more if new tests added)

# Step 4: If any test fails → revert the change, diagnose, retry
```

---

## What NOT to Refactor (Too Risky)

- `ai_debate_engine.py` — core AI logic, complex timing/async patterns
- `zerodha_service.py` — external broker integration, any change risks live trade issues
- `AuthContext.jsx` — auth state change affects entire frontend
- MongoDB collection field names — downstream code and tests depend on exact names
- WebSocket channel names — frontend and backend must match exactly
- Any code directly related to the Emergency Stop feature — safety-critical
