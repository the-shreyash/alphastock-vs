# Browser-level verification scripts

Host-side scripts that drive the **real product in a real browser**, for the
class of defect that no hermetic test can see.

## Why this directory exists

Everything in `backend/tests/` asserts at the transport boundary with a stubbed
WebSocket, and everything in `frontend/src/__tests__/` asserts against jsdom.
Both are the right default: they are fast, deterministic, and they run in CI.

They are also structurally blind to two things, and each one has now hidden a
real defect on this project:

1. **A protocol rule only the browser enforces.** D6.3's `D63-5`: the server
   accepted a WebSocket handshake without echoing the subprotocol the SPA had
   offered. Starlette's test transport does not enforce the browser's
   subprotocol rule, so every suite was green while **realtime never connected
   for any signed-in user**. Only Chrome could see it.

2. **A leak that needs two tenants alive at the same instant.** D6.3's closure
   pass found that `/stocks/{symbol}/patterns` published its scan line to the
   shared activity stream, so the symbols one account opened were announced on
   every other signed-in user's dashboard. One browser with one tab cannot
   observe this; two accounts signed in simultaneously can.

The rule these scripts encode: **when two sides of a contract each hold half of
it and the test double enforces neither, only the real client is evidence.**

## `d63_tenant_isolation.js`

Closes the testable half of `LIM-D6.3-1`. Drives five scenarios across three
isolated browser contexts (three separate cookie jars, one real Chrome):

| | Scenario |
|---|---|
| T1 | Two accounts in two contexts, signed in **simultaneously** |
| T2 | Two **tabs** on one account, one cookie jar — the D6.2 refresh-grace race |
| T3 | `A -> B` identity transition in one tab |
| T4 | WebSocket lifetime and private-frame isolation across two live contexts |
| T5 | Private activity: A's stock-page visit must not reach B's feed |

Every negative check is paired with an **owner-positive control** in the same
run, because "B saw nothing" is also what a broken fixture and a deleted log
line produce. T5's controls are the sharpest: it asserts A *does* see their own
scan, and that a symbol nobody opened is absent from either feed.

### Running it

```bash
# 1. Bring the stack up
mongod                                        # or: brew services start mongodb-community
cd backend  && venv/bin/python -m uvicorn server:app --port 8000
cd frontend && npx craco start                # serves :3000

# 2. Playwright drives the *installed* Chrome — no browser download needed
npm i playwright

# 3. Run
node scripts/browser/d63_tenant_isolation.js
```

Exits non-zero on any failed check, so it can gate a release. Override the
endpoints with `APP_URL` / `API_URL`.

The two test accounts (`d63a@d63.test`, `d63b@d63.test`) are created on first
run and reused afterwards; user A is given a small watchlist so the isolation
checks have something real to be isolated from. **Point this at a development
database only** — it registers accounts and writes watchlist rows.

### What it deliberately does not do

**No order is placed and no broker is connected.** The broker leg of
`LIM-D6.3-1` stays open: it needs live Zerodha/Upstox credentials and an
interactive login, so every broker-isolation result on this project is still
from hermetic tests rather than from a live session.

### A note on rate limits

The suite signs in several times per run. Running it repeatedly in quick
succession will trip the login rate limiter and produce `429`s that look like
isolation failures. Leave a couple of minutes between runs, or restart the API.
