# AlphaPartner — Code Review Rules

> Load this file when performing any code review task on AlphaPartner.
> Apply every checklist item to every file reviewed. Never skip a section.

---

## How to Run a Code Review

When asked to review code, follow this exact sequence:

1. Read the file(s) to be reviewed in full before commenting on anything.
2. Run through every checklist section below.
3. Group findings by severity: 🔴 Critical → 🟡 Warning → 🔵 Suggestion.
4. For every 🔴 Critical finding, provide the corrected code inline.
5. For 🟡 and 🔵, explain what to fix and why — code optional.
6. End with a summary: total issues found per severity + overall verdict.

---

## Backend (Python / FastAPI) Review Checklist

### Async & Database
- [ ] Every function that touches MongoDB uses `async def` + `await`
- [ ] No `pymongo` sync calls anywhere — only `motor.motor_asyncio`
- [ ] No `time.sleep()` inside async functions — use `asyncio.sleep()`
- [ ] Background tasks use `FastAPI BackgroundTasks` — not `threading.Thread`
- [ ] Database operations use `await collection.find_one()`, `await collection.insert_one()`, etc.
- [ ] Cursor iteration uses `async for doc in cursor` — not `list(cursor)`

### API & Routes
- [ ] All routes that need authentication use the JWT dependency
- [ ] Route paths follow `/api/resource-name` format (kebab-case)
- [ ] Request bodies use Pydantic models — no raw `dict` parsing
- [ ] Response models defined or at least `response_model` hinted
- [ ] HTTP status codes are correct (201 for creates, 404 for not found, 422 for validation)
- [ ] No sensitive data (passwords, raw tokens) returned in responses

### Security
- [ ] No hardcoded API keys, secrets, or passwords anywhere
- [ ] All secrets loaded from `os.environ.get()` with safe fallbacks
- [ ] JWT verification applied to all protected endpoints
- [ ] No SQL/NoSQL injection risk (Motor + Pydantic validation prevents most, but check raw filter dicts)
- [ ] Webhook endpoints use API key header check, not JWT

### Error Handling
- [ ] All external API calls (Yahoo Finance, Zerodha, Telegram, etc.) are wrapped in try/except
- [ ] Fallback/simulated data returned when external service unavailable — never crash
- [ ] `HTTPException` raised with meaningful detail messages
- [ ] No bare `except:` — always catch specific exceptions or `except Exception as e`

### Code Quality
- [ ] No duplicate logic — check if a utility already exists in `services/` before writing new
- [ ] Pydantic model fields have correct types and Optional where nullable
- [ ] New MongoDB fields are Optional with defaults — never required on existing collections
- [ ] No print() statements in production code — use proper logging
- [ ] Function names are descriptive snake_case
- [ ] No Emergent platform imports or references

---

## Frontend (React / JavaScript) Review Checklist

### Component Structure
- [ ] All components are functional — no class components
- [ ] Hooks are used correctly (useState, useEffect, useContext)
- [ ] No hook calls inside conditionals or loops
- [ ] useEffect has correct dependency arrays — no missing deps, no infinite loops
- [ ] Components are not doing too much — single responsibility principle

### API & Data
- [ ] All API calls go through `context/services/api.js` Axios client — no raw fetch()
- [ ] Loading states handled — skeleton cards shown while data fetches
- [ ] Error states handled — user sees a message if API fails, not a blank screen
- [ ] No API keys or secrets in frontend code
- [ ] WebSocket connections cleaned up in useEffect return/cleanup function

### Styling
- [ ] Tailwind utility classes used — no inline styles for layout/color
- [ ] No hardcoded hex/rgb color values — use CSS variables from `index.css`
- [ ] New components match glassmorphic card design from existing pages
- [ ] Lucide React for icons — no other icon libraries imported
- [ ] Responsive: components don't break on narrower screens

### React Patterns
- [ ] Lists always have unique `key` props — never use array index as key if list can reorder
- [ ] Heavy computations wrapped in `useMemo`
- [ ] Event handlers wrapped in `useCallback` where passed as props
- [ ] No direct state mutations — always use setter functions
- [ ] Conditional rendering is clean — no `undefined` or `null` leaking into JSX

### Code Quality
- [ ] No `console.log()` left in production code
- [ ] No commented-out blocks of dead code
- [ ] Component and file names match: `Dashboard.jsx` exports `function Dashboard()`
- [ ] Imports are clean — no unused imports
- [ ] No duplicate components — check if a similar component already exists in pages/

---

## Architecture Review Checklist (for larger changes)

- [ ] New backend service files go in `backend/services/` — not in `backend/` root
- [ ] New frontend pages go in `frontend/src/pages/` — registered in `App.js`
- [ ] New routes added to `server.py` — not in separate router files (unless very large)
- [ ] New Pydantic models added to `models.py`
- [ ] No circular imports between services
- [ ] WebSocket channels have clear names — no generic "message" events
- [ ] n8n workflows only call FastAPI webhook endpoints — no direct DB access from n8n
- [ ] Paper trades never touch `zerodha_service.py`

---

## Test Coverage Check

After any backend change, verify:
```bash
cd backend && ./venv/bin/python -m pytest --tb=short
```

Expected: **103 passed, 0 failed**

If any test fails after a change:
1. Read the test failure message carefully.
2. Identify if the change broke an existing behavior or the test expectation is outdated.
3. Fix the source code to restore passing tests — do NOT modify tests unless the test itself has a genuine bug.
4. Re-run tests to confirm all 103 pass again.

---

## Severity Definitions

| Level | Meaning | Must Fix? |
|---|---|---|
| 🔴 Critical | Will cause runtime crash, security vulnerability, data loss, or breaks existing tests | Yes — fix immediately |
| 🟡 Warning | Will cause subtle bugs, performance issues, or bad UX but won't crash | Yes — fix before merging |
| 🔵 Suggestion | Code quality, readability, or best practice improvement | Recommended |

---

## Review Output Format

```
## Code Review: [filename(s)]

### 🔴 Critical Issues (X found)
**Issue 1:** [description]
- Line: [line number or range]
- Problem: [what is wrong]
- Fix:
  [corrected code]

### 🟡 Warnings (X found)
**Warning 1:** [description]
- Line: [line number]
- Problem: [what is wrong and why it matters]
- Suggestion: [what to do instead]

### 🔵 Suggestions (X found)
**Suggestion 1:** [description]

---
### Summary
- Critical: X | Warnings: X | Suggestions: X
- Verdict: [PASS / NEEDS FIXES / CRITICAL CHANGES REQUIRED]
```
