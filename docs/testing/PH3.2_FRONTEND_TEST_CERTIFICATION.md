# PH3.2 — Frontend Test Certification

**Sprint:** PH3.2 — Frontend Testing & UI Regression Foundation
**Phase:** PH3 — Production Hardening & Quality Assurance
**Date:** 2026-08-10
**Decision:** ✅ **CERTIFIED**

> **Numbering.** The sprint brief labelled this work **PH3.2**. This
> repository's `PRODUCTION_ROADMAP.md` and `TASK.md` number PH3.2 as *Mock Data
> Eradication* — untouched by this sprint — and number this work **PH3.3**. This
> document keeps the brief's label; both trackers carry the cross-reference.
> Nothing was renumbered unilaterally.

---

## 1. Executive Summary

The frontend had no automated tests. `npm test` ran nothing, and every UI
regression in this repository has been caught — or missed — by a human looking
at a screen.

It now has **313 tests across 17 suites, green in ~8 seconds**, covering
authentication, routing and route guards, admin access control, the dashboard
shell, paper-trading order entry, the AI workspace, the watchlist, notifications
and the realtime store. Every critical screen is asserted in all four states:
loading, success, empty, and error.

Two framework decisions went against the previously documented plan, both on
evidence rather than preference, and both recorded in §2.

**Five frontend defects were found by these tests and fixed.** The most
consequential: paper trading rendered a failed load as an *empty account* — zero
balance, "No open paper trades" — which in a trading UI reads as *my positions
are gone*, not *the server is down*.

**One pre-existing build defect was found and deliberately not fixed**, with its
attribution verified experimentally rather than assumed (§16, §19).

| Measure | Result |
|---------|--------|
| Tests | **313 passed**, 0 failed, 17 suites |
| Runtime | ~8s (full), ~14s with coverage |
| Overall statement coverage | **33.6%** |
| Critical-path statement coverage | **77.0%** |
| Defects found | 7 (5 fixed, 1 deferred with rationale, 1 pre-existing) |
| Backend regression (PH1 + PH3.1) | **1,035 passed**, 95 deselected — baseline held |
| Production build | Blocked by a **pre-existing** ESLint dependency conflict; app compiles cleanly |

---

## 2. Testing Framework

**Jest 27 + React Testing Library 16**, executed through `craco test`.

### Why not Vitest

`TESTING.md` had named Vitest as the target. It was not adopted.

This application builds with webpack through CRA 5 / craco. Vitest would run the
suite through esbuild — a *different transform than the one that ships*. A suite
that validates a bundle no user ever receives is a weaker signal than its test
count suggests. Jest already ships inside `react-scripts`, applies the same
`babel-preset-react-app` transform as the production build, and required no new
runner.

Revisit only if the build itself migrates to Vite. Recorded in `TESTING.md`.

### Why not MSW

MSW is the usual recommendation for request interception and was evaluated
first. CRA 5 pins Jest 27, whose resolver predates `package.json#exports`; MSW v2
is exports-only ESM and needs `TextEncoder`, `TransformStream`,
`BroadcastChannel` and `ReadableStream` polyfilled under jsdom before it will
load. That is a large, fragile surface to maintain for one capability.

Interception happens at the **axios adapter** instead (§14).

### Installed

| Package | Version | Role |
|---------|---------|------|
| `@testing-library/react` | 16.3.2 | render + accessible queries |
| `@testing-library/dom` | 10.4.1 | query engine (RTL 16 peer) |
| `@testing-library/jest-dom` | 6.10.0 | DOM/a11y matchers |
| `@testing-library/user-event` | 14.6.3 | realistic interaction |
| `axios-mock-adapter` | 2.1.0 | transport-boundary interception |

Dev dependencies only. No production dependency was added or changed.

### Configuration required to make CRA 5 / Jest 27 work with this stack

Three real obstacles, all resolved in `frontend/package.json` and
`src/setupTests.js`:

1. **`react-router-dom@7` does not resolve.** Its `main` field points at
   `./dist/main.js`, which does not exist in the published package; Jest 27
   cannot read the `exports` map that would have redirected it. Fixed with three
   `moduleNameMapper` entries pinning `react-router-dom`, `react-router` and
   `react-router/dom` to their CJS builds.
2. **`react-router@7` references `TextEncoder` at module scope**, which
   jsdom 16 (bundled with Jest 27) does not provide. Polyfilled from Node's
   `util` in `setupTests.js` — a genuine polyfill, not a substitute.
3. **`gsap/ScrollTrigger` is ESM-only** and is imported by the Landing page.
   `transformIgnorePatterns` was narrowed to transform `gsap`.

---

## 3. Test Architecture

```
frontend/src/
├── setupTests.js                     ← jsdom polyfills + network isolation
├── test-utils/
│   ├── index.js                      ← renderWithProviders, renderAppAt,
│   │                                    mockAuthenticatedUser/mockAdminUser,
│   │                                    stubLocation, resetRealtimeStore
│   ├── apiMock.js                    ← installApiMock, HTTP, pending,
│   │                                    stubRemainingWith
│   └── fixtures.js                   ← deterministic test data
├── __tests__/                        ← Layer 2: cross-screen integration
│   ├── routing.test.jsx
│   └── authFlow.integration.test.jsx
├── utils/__tests__/                  ← Layer 1
├── services/__tests__/
├── context/__tests__/
├── store/__tests__/
├── components/__tests__/
└── pages/__tests__/
```

**Layer 1 — unit / component.** Formatters, the API client's interceptors, the
realtime store's reducers, and each critical screen rendered in isolation inside
the real provider tree.

**Layer 2 — integration.** `routing.test.jsx` and `authFlow.integration.test.jsx`
drive the application's **real route table** — `AppRouter`, exported from
`App.js` for exactly this purpose — so a guard removed from a route declaration
fails the suite rather than passing against a test-local copy.

**Layer 3 — E2E.** Deliberately not started (§18). No E2E framework existed, and
introducing one would have meant a browser runtime, a booted backend and a
seeded database — infrastructure the brief explicitly said not to build here.

### Design rule: no faked auth state

`renderWithProviders` assembles the **real** `ThemeProvider → AuthProvider →
RealtimeProvider` tree. Authentication is established the way production
establishes it — by answering `GET /auth/me` at the network mock — never by
injecting a stub context value. A test proving "an admin sees the admin portal"
therefore also proves the real `AuthProvider` parses the real response shape.

---

## 4. Component Test Strategy

Behaviour, not implementation. Assertions are written against what a user can
observe: rendered text, accessible roles, enabled/disabled state, and the
requests the app actually sends. No test inspects component state, spies on
`setState`, or asserts a render count.

Where the codebase already provided `data-testid` hooks they are used for
*locating* elements; the assertion that follows is always behavioural. Where a
control is user-facing, `getByRole(...{ name })` is preferred, which is why
several missing accessible names surfaced as defects (§16).

Prioritised, per the brief: authentication components, routing/guards, the
dashboard shell, trade entry, AI analysis, admin access, notifications and the
critical forms. Decorative components were not tested.

---

## 5. Integration Test Strategy

Two suites, both mounted on the real route table with only the network faked.

`routing.test.jsx` (22 tests) — public/protected/admin/unknown routes for
signed-out, signed-in and admin users, plus the in-flight case.

`authFlow.integration.test.jsx` (7 tests) — the journeys that cross screens:
sign in → application shell; sign out → login screen with the credential
cleared; hard refresh → session restored without a login prompt; expired session
→ returned to login.

---

## 6. Authentication Coverage

| Behaviour | Suite |
|-----------|-------|
| Login screen renders; no premature error | `Login.test.jsx` |
| Credentials posted exactly as typed | `Login`, `AuthContext` |
| Invalid credentials show the server's reason | `Login`, `authFlow` |
| 422 validation body renders as text, not `[object Object]` | `Login`, `Register`, `apiError` |
| 429 / 403 / 500 / network failure each explained | `Login`, `Register` |
| Pending state disables submit; double-submit blocked | `Login`, `Register` |
| Form re-enabled after failure; stale error cleared on retry | `Login` |
| Successful auth updates auth state → app shell | `AuthContext`, `authFlow` |
| Cookie-only session (no `token` in body) works | `AuthContext` |
| Logout revokes server-side, clears token, returns to login | `AuthContext`, `authFlow` |
| Logout succeeds locally even when the API call fails | `AuthContext`, `authFlow` |
| Session restored on reload; bearer token attached | `AuthContext`, `authFlow` |
| Expired session → login screen | `AuthContext`, `authFlow` |
| `/auth/me` probed exactly once per mount | `AuthContext` |
| Session probe deferred on the OAuth callback route | `AuthContext` |
| `useAuth` outside its provider throws | `AuthContext` |
| Registration enforces the 12-char PH1.5 minimum client-side | `Register` |
| Duplicate email (409) explained | `Register` |
| **Google OAuth** — authorization URL requested from the backend, bound to this origin | `Login` |
| Google — browser handed to the returned URL | `Login` |
| Google — unavailable / no URL returned → explained, no navigation | `Login` |
| Google callback — `code`+`state`+`redirect_uri` posted with credentials | `AuthCallback` |
| Google callback — session established *before* navigating | `AuthCallback` |
| Google callback — single-use code exchanged exactly once | `AuthCallback` |
| Google callback — missing code/state/`error=access_denied` → login, nothing sent | `AuthCallback` |
| Google callback — rejected CSRF state / expired code / 500 / network → login | `AuthCallback` |

**API client** (`services/api.js`, 100% statements) is tested separately because
it is the highest-leverage code in the frontend: 401 → refresh → replay; retry
capped at once so a still-401 endpoint cannot loop; the dead-session latch that
stops a dashboard full of widgets from firing a refresh each; the latch re-arming
on a fresh sign-in; `/auth/*` endpoints excluded from refresh; and 400/403/404/
409/429/500/network/timeout all passing through untouched.

---

## 7. Dashboard Coverage

The dashboard fans out to a dozen independent endpoints, each widget owning its
loading and empty state. That design is deliberate — one dead endpoint must
degrade one card, never the page — so the suite pins the property that makes it
worth having.

- Shell renders; core market data requested on mount
- Loading placeholders shown, and **no widget stuck on its skeleton** after its
  request settles
- Empty watchlist / notifications / AI picks each *explained*, not blank
- Populated watchlist, notifications, portfolio summary (Indian digit grouping)
  and market indices render API data
- **Partial failure stays partial**: with a widget's endpoint returning 401,
  403, 429 or 500, that widget degrades while its neighbours keep rendering
- Page survives the core market fetch failing outright
- Page survives a malformed payload where an array was expected
- Quick actions all carry accessible names

---

## 8. Trading Coverage

`PaperTrading.test.jsx` — 29 tests. The four things a trader must be able to
trust:

1. **What is on screen is what the API returned** — balance, P&L, open and
   closed positions.
2. **A submitted order carries exactly the numbers typed** — asserted as
   *numbers*, since strings are silently coerced or rejected by the engine; the
   selected side (BUY/SELL) is asserted too.
3. **A rejected order says why and does not look like a success** — the risk
   manager's reason is shown, the ticket stays open for correction, submit
   re-enables, and no success confirmation appears.
4. **A failure to load says so** — see FE-003 (§16).

Also covered: the ticket closing on success so an order cannot be sent twice;
submit disabled in flight; entry price prefilled from the live quote on blur;
position close reporting realised P&L; a failed close leaving the position
listed; capital reset requiring confirmation and reporting failure.

`tradeService.test.js` — 19 tests over the Trading Engine gateway: every read
endpoint's URL and body unwrapping, create/validate/modify/exit verbs and paths
(a wrong verb here is a silent 404 that looks to the trader like nothing
happened), the order-history query string, and `tradeErrorDetails` normalising a
risk rejection into `{ message, violations, warnings }`.

---

## 9. AI Coverage

`AIAssistant.test.jsx` — 28 tests, enforcing the product rule that **an AI
failure must be stated; silence is the one response the workspace may never
give**.

- Welcome message, starter prompts, send disabled until input exists
- Empty conversation history explained, not blank
- Previous conversations listed; most recent opened on arrival
- History load failure → welcome message; conversation-list failure → empty list
- Message posted with `session_id` **and a `run_id`** — the correlation id that
  pairs live `ai.step` frames to the request
- User message shown optimistically; reply rendered; composer cleared
- Enter-to-send; starter-prompt send; whitespace-only ignored
- **Working indicator** shown while in flight, and the live step timeline
  replaces the fallback pulse once matching `ai.run.started` / `ai.step` frames
  arrive through the store — proving the correlation wiring end to end
- Composer locked so a second request cannot overlap the first
- 500 / 429 / 401 / network → the failure is stated
- Composer released after failure; **thinking indicator never outlives the
  request** (a stuck spinner is indistinguishable from a hung backend)
- Empty AI response does not crash the thread

---

## 10. Admin Coverage

**Access control** (`routing.test.jsx`): signed-out → login; authenticated
non-admin → bounced to the app shell and never the admin layout, verified across
`/admin/dashboard`, `/admin/users`, `/admin/payments`, `/admin/logs`,
`/admin/feature-flags`; `admin` **and** `super_admin` both admitted.

**Page** (`AdminDashboard.test.jsx`): skeleton while loading; metrics rendered
from the API payload with Indian digit grouping (`1,280`, `₹2,45,000`); every
metric labelled; system health reported; and the loading state resolving on 403,
500 and network failure rather than spinning forever.

That last group is pinned at *current* behaviour and annotated in-test: the page
currently renders zeroed metrics on failure rather than saying the load failed
(FE-006, §18).

---

## 11. Routing Coverage

22 tests against the real route table.

| Case | Result asserted |
|------|-----------------|
| `/` signed out | Landing renders, no app shell |
| `/login`, `/register` signed out | screen renders |
| `/login` signed in | redirected into the app shell |
| `/dashboard` signed out | redirected to login |
| `/portfolio`, `/trades`, `/watchlist`, `/settings`, `/paper-trading` signed out | redirected to login |
| `/dashboard` signed in | app shell renders |
| any protected route, auth **in flight** | waits — does **not** flash the login screen |
| `/admin/*` signed out | redirected to login |
| `/admin/*` non-admin | app shell, never the admin portal |
| `/admin/dashboard` as `admin` / `super_admin` | admin portal renders |
| unknown path, signed out | Landing |
| unknown path, signed in | app shell |

The in-flight case guards a specific bug class: a guard treating "not yet known"
as "signed out" flashes every returning user through the login screen on every
hard refresh.

---

## 12. Error / Loading / Empty State Coverage

| Screen | Loading | Success | Empty | Error |
|--------|:-------:|:-------:|:-----:|:-----:|
| Login | ✅ | ✅ | n/a | ✅ |
| Register | ✅ | ✅ | n/a | ✅ |
| OAuth callback | ✅ | ✅ | n/a | ✅ |
| Dashboard (per widget) | ✅ | ✅ | ✅ | ✅ |
| Paper trading | ✅ | ✅ | ✅ | ✅ |
| AI workspace | ✅ | ✅ | ✅ | ✅ |
| Watchlist | ✅ | ✅ | ✅ | ⚠️ degrades to empty (FE-006) |
| Notifications | ✅ | ✅ | ✅ | ⚠️ degrades to empty (FE-006) |
| Admin dashboard | ✅ | ✅ | n/a | ⚠️ degrades to zeros (FE-006) |

⚠️ marks *tested and documented current behaviour*, not untested behaviour.

---

## 13. Accessibility Baseline

Per the brief: a baseline, not a WCAG audit.

Asserted on critical UI — buttons have accessible names; form inputs are
programmatically labelled; errors are announced via `role="alert"`; primary
interactions are keyboard-reachable (tab order through the login form,
Enter-to-activate on the AI tab bar, Enter-to-send in the composer).

Writing these assertions is what surfaced **FE-002, FE-004 and FE-005** (§16) —
three real defects that a `data-testid`-only suite would never have found.

Not attempted: colour contrast, focus-trap correctness in dialogs, screen-reader
transcript review, reduced-motion behaviour. Those belong to the accessibility
audit sprint.

---

## 14. API Mocking Strategy

Every network call in the app goes through the single axios instance in
`services/api.js`. `axios-mock-adapter` replaces that instance's **adapter** —
the last step before a request leaves the process.

Everything above the adapter runs for real: the bearer-token request
interceptor, the 401 silent-refresh interceptor, every service module and every
component. This is the whole reason for the choice. Mocking
`tradeService.create` would prove nothing about the interceptors; intercepting
the transport proves the entire client stack behaves correctly against a given
server response.

**Statuses exercised:** 200, 400, 401, 403, 404, 409, 422, 429, 500, network
failure, timeout, and never-settling (for loading-state assertions).

**Nothing can reach a real service.** `setupTests.js` points the axios base URL
at `http://backend.test`, replaces `fetch` with a stub that rejects loudly, and
installs an inert `WebSocket` class so `RealtimeProvider` never opens a socket.
Live-data behaviour is exercised by writing directly into the Zustand realtime
store. No test touches a broker, NSE, Yahoo Finance, Google, an AI provider, a
payment provider, Twilio, or any backend.

`onNoMatch: "throwException"` is the default: an unstubbed request fails by name
rather than hanging.

> **Documented trap.** `axios-mock-adapter` matches handlers **in registration
> order**. A catch-all registered before the specific routes answers them, and
> the test passes for the wrong reason. This bit during development — three
> Watchlist tests passed against the catch-all instead of their intended stubs —
> and is now called out in `apiMock.js`, in `TESTING.md`, and enforced by the
> `renderWatchlist(items, setupStubs)` helper shape.

**Fixtures** (`test-utils/fixtures.js`) are frozen constants — no `Date.now()`,
no `Math.random()` — and every shape mirrors what `backend/server.py` actually
returns, including the real asymmetry that `POST /auth/login` returns `id` +
`token` while `GET /auth/me` returns `_id` and no token. No real user data,
broker account, credential or production identifier appears anywhere.

---

## 15. Coverage Results

`yarn test:coverage`, application code only, excluding the vendored
`components/ui/` shadcn primitives.

### Overall

| Metric | Covered | Total | % |
|--------|--------:|------:|--:|
| Statements | 1560 | 4639 | **33.6%** |
| Branches | 883 | 4519 | 19.5% |
| Functions | 445 | 1555 | 28.6% |
| Lines | 1329 | 3863 | 34.4% |

### Critical paths — **77.0% aggregate statements** (1048/1362)

| Module | Stmts | Branch | Funcs | Lines |
|--------|------:|-------:|------:|------:|
| `services/api.js` | **100%** | 94.4% | 100% | 100% |
| `services/googleAuth.js` | **100%** | 100% | 100% | 100% |
| `utils/formatters.js` | **100%** | 100% | 100% | 100% |
| `pages/Login.jsx` | **100%** | 100% | 100% | 100% |
| `pages/AIAssistant.jsx` | **100%** | 92.5% | 100% | 100% |
| `context/AuthContext.jsx` | 97.6% | 80.0% | 100% | 100% |
| `pages/PaperTrading.jsx` | 96.1% | 68.6% | 91.9% | 96.6% |
| `pages/AuthCallback.jsx` | 95.8% | 75.0% | 100% | 100% |
| `services/tradeService.js` | 94.3% | 95.0% | 91.3% | 95.2% |
| `components/notifications/NotificationPanel.jsx` | 94.1% | 90.5% | 91.3% | 100% |
| `utils/apiError.js` | 92.0% | 84.0% | 100% | 89.5% |
| `pages/Watchlist.jsx` | 89.1% | 59.5% | 90.0% | 89.9% |
| `pages/admin/AdminDashboard.jsx` | 84.0% | 96.2% | 84.6% | 88.9% |
| `components/admin/AdminRoute.jsx` | 75.0% | 75.0% | 100% | 83.3% |
| `hooks/useAIWorkspace.js` | 75.6% | 50.0% | 78.6% | 77.9% |
| `pages/Dashboard.jsx` | 66.4% | 36.3% | 64.6% | 68.4% |
| `store/realtimeStore.js` | 60.6% | 46.3% | 57.7% | 64.8% |

### Reading the overall number honestly

33.6% is low, and it is low for a reason that is visible in the per-file report:
roughly thirty feature pages this sprint did not scope sit at 0% — `Portfolio`
(752 lines), `TradeMonitor` (1,404), `StockDetail`, `Markets`, `News`,
`Settings`, `TradeJournal`, `Backtesting`, `MorningReport`, `InvestmentAdvisor`,
`SIPAdvisor`, `StockPicks`, and ten admin pages.

Raising the headline figure with shallow does-it-render tests over those pages
was explicitly declined. It would have moved the percentage without adding one
piece of information about whether the product works. The second column is the
number to hold this sprint to.

No `fail_under` threshold is enforced; PH3.11 sets one from trend data.

---

## 16. Defects Found

| ID | Severity | Defect |
|----|----------|--------|
| FE-001 | **HIGH** | Every client-thrown error message silently discarded |
| FE-002 | **MEDIUM** | Auth failures announced in colour only |
| FE-003 | **HIGH** | Paper trading renders a failed load as an empty account |
| FE-004 | **MEDIUM** | Form labels not programmatically associated |
| FE-005 | **MEDIUM** | Icon-only controls have no accessible name |
| FE-006 | **MEDIUM** | Silent load failures across four more surfaces (deferred) |
| FE-007 | **HIGH** | Production build fails — pre-existing, out of scope |

### FE-001 — every client-thrown error message was silently discarded (HIGH)

`Login.jsx` and `Register.jsx` both did:

```js
setError(formatApiError(err.response?.data?.detail) || err.message);
```

This looks like a fallback chain and is not one. `formatApiError(undefined)`
returns `"Something went wrong. Please try again."` — truthy — so `err.message`
was **unreachable**. Any error without an HTTP `detail` produced the generic
message, including the deliberate, specific `"Google sign-in is unavailable
right now."` thrown by `services/googleAuth.js`.

The helper was also duplicated across the two files and had already drifted
(different fallback strings). Extracted to `utils/apiError.js`, which now also
distinguishes a transport failure — whose message (`"Network Error"`) is a
diagnostic — from an application error, whose message was written for a user.
A latent `String(detail)` → `"[object Object]"` path for unrecognised object
shapes was fixed at the same time, caught by its own test.

### FE-002 — auth failures announced in colour only (MEDIUM)

The login and registration error banners were styled `<div>`s with no
`role="alert"`. A screen-reader user submitting bad credentials received no
signal whatsoever that anything had happened.

### FE-003 — paper trading renders a failed load as an empty account (HIGH)

```js
} catch (e) {
  console.error(e);          // ← and then fall through to the normal view
} finally { setLoading(false); }
```

With `/paper/balance`, `/paper/pnl` and `/paper/trades` failing, the page
rendered a zero balance, zero P&L and *"No open paper trades. Place your first
one!"* — indistinguishable from a genuinely empty account. In a trading UI that
reads as **my positions are gone**, not *the server is down*.

Fixed with an explicit error state carrying the server's reason and a retry
control, plus an array guard on the trades payload. Covered by six tests
including recovery-on-retry.

### FE-004 — form labels not programmatically associated (MEDIUM)

Login, Register and the paper-trade order ticket rendered `<label>` elements
with no `htmlFor`/`id` pairing and no nesting, so assistive technology could not
connect a label to its input. Surfaced when `getByLabelText` failed across ten
order-ticket tests. Fixed with explicit `htmlFor`/`id` pairs.

### FE-005 — icon-only controls have no accessible name (MEDIUM)

The AI composer's send button, the watchlist remove buttons, the notification
panel's close button and the password-reveal toggles rendered an icon and
nothing else, announcing as a bare "button". Fixed with `aria-label`s (the
password toggle's label reflects its current state).

### FE-006 — silent load failures on four more surfaces (MEDIUM, deferred)

The same pattern as FE-003 exists in `Dashboard.jsx` (per widget),
`Watchlist.jsx`, `AdminDashboard.jsx` and `NotificationPanel.jsx`: the `catch`
swallows the error and the UI shows an empty/zero state.

**Deferred deliberately.** Fixing it properly means an error-state design across
five surfaces — new UI, new copy, a shared retry affordance — which is a feature
change, not a test fix, and the brief is explicit that PH3.2 must not expand into
unrelated refactoring or redesign UI. FE-003 was fixed because a trading account
showing phantom-empty positions is a different order of harm.

Consequence ranked: `AdminDashboard` (an operator reads ₹0 MRR as a business
event) > `Watchlist`/`NotificationPanel` > `Dashboard` (whose per-widget
degradation is at least an intentional design). Each is pinned by a test at
current behaviour, so the deferred fix has a starting point and cannot regress
further.

### FE-007 — production build fails (HIGH, pre-existing, out of scope)

```
[eslint] Failed to load config "react-app" to extend from.
Referenced from: frontend/.eslintrc.json
```

`frontend/.eslintrc.json` extends `react-app`, provided by
`eslint-config-react-app@^7` (a `react-scripts` dependency requiring
`eslint@^8`). `devDependencies` pins `eslint@^9.39.5`, which yarn hoists to the
top level, and `eslint-config-react-app` ends up unresolvable.

**Attribution was verified, not assumed.** The working tree was stashed back to
the pristine pre-sprint `package.json` and `yarn.lock`, dependencies reinstalled
with `--frozen-lockfile`, and the identical failure reproduced. It predates this
sprint.

**Not fixed here.** Repairing it means either downgrading `eslint` to 8 (fighting
the repo's evident eslint-9 flat-config direction: `@eslint/js`, `globals` and
eslint-9 plugins are all pinned) or disabling CRA's build-time ESLint plugin —
both lint-policy decisions outside a testing sprint's mandate.

**The application itself compiles cleanly.** `DISABLE_ESLINT_PLUGIN=true yarn
build` succeeds with every PH3.2 change in place (§19).

---

## 17. Defects Fixed

| ID | Files changed |
|----|---------------|
| FE-001 | `utils/apiError.js` (new), `pages/Login.jsx`, `pages/Register.jsx` |
| FE-002 | `pages/Login.jsx`, `pages/Register.jsx` |
| FE-003 | `pages/PaperTrading.jsx` |
| FE-004 | `pages/Login.jsx`, `pages/Register.jsx`, `pages/PaperTrading.jsx` |
| FE-005 | `pages/AIAssistant.jsx`, `pages/Watchlist.jsx`, `components/notifications/NotificationPanel.jsx`, `pages/Login.jsx`, `pages/Register.jsx` |

Every fix has a regression test naming its defect ID. No business logic, trading
logic, AI logic or visual design was changed. The only non-defect source change
is exporting `AppRouter` from `App.js` so routing tests drive the real route
table.

---

## 18. Known Gaps

1. **~30 feature pages untested** — Portfolio, TradeMonitor, StockDetail,
   Markets, News, Settings, TradeJournal, Backtesting, MorningReport,
   InvestmentAdvisor, SIPAdvisor, StockPicks, BrokerCallback, and ten admin
   pages. Largest remaining risk; the natural next sprint.
2. **No E2E layer.** No real browser, no booted backend, no seeded database. A
   defect that only appears in a real browser (CSS-dependent layout, actual
   OAuth redirects, service-worker behaviour) remains invisible.
3. **FE-006 deferred** (§16).
4. **CI frontend job not activated** — the PH2.6 workflow placeholder still
   needs wiring. Tests are CI-ready (§19), but nothing gates a PR yet.
5. **No coverage threshold enforced** — PH3.11 sets one from trend data.
6. **Realtime store at 60.6%** — connection lifecycle, price ingestion, AI runs,
   notifications and watchlist sync are covered; scanner, news, broker and
   portfolio/trade-live slices are not.
7. **`RealtimeProvider` socket lifecycle untested** — reconnect backoff,
   heartbeat and pong-timeout logic are stubbed out, not exercised. Needs a
   controllable fake socket.
8. **Accessibility is a baseline, not an audit** (§13).
9. **jsdom does not enforce HTML5 constraint validation**, so "empty form does
   not submit" cannot be asserted directly; the declared constraint and
   `checkValidity()` are asserted instead, with the limitation noted in-test.
10. **Framework pinned to CRA/Jest 27.** The `moduleNameMapper` entries for
    react-router are a workaround for a resolver that predates `exports` maps,
    and will need revisiting if either is upgraded.

---

## 19. CI Verification

**Frontend suite — CI-ready.** No dependency on a developer's browser, a local
backend, a production environment, real APIs, or manual authentication.

```
$ CI=true yarn test:ci
Test Suites: 17 passed, 17 total
Tests:       313 passed, 313 total
Time:        ~14s (with coverage)
```

Commands, all verified working:

| Purpose | Command |
|---------|---------|
| Run once | `yarn test` |
| Watch | `yarn test:watch` |
| Coverage | `yarn test:coverage` |
| CI mode | `yarn test:ci` |
| One file | `yarn test --testPathPattern=Login` |

**Production build — blocked by FE-007 (pre-existing).**

```
$ yarn build
[eslint] Failed to load config "react-app" to extend from.     ← pre-existing

$ DISABLE_ESLINT_PLUGIN=true yarn build
Compiled successfully.  The build folder is ready to be deployed.
```

The second result is the one that speaks to this sprint: the application,
including every PH3.2 source change, compiles and chunks cleanly.

**Backend regression — baseline held.**

```
$ python -m pytest -q          # PH1 security + PH3.1 hermetic suite
1035 passed, 95 deselected, 4 warnings in 152.49s
```

Identical to the PH3.1 certified baseline. Authentication, routing, API
integration and the production bundle are unaffected by PH3.2.

---

## 20. PH3.3 Handoff

> Under this repository's roadmap numbering, the next sprints are PH3.2 (Mock
> Data Eradication, untouched and still NOT_STARTED) and PH3.4 (Frontend Service
> & Hook Coverage). The items below are ordered by value regardless of label.

**Inherited assets.** `test-utils/` (render helpers, auth helpers, fixtures, API
mock), `setupTests.js` (polyfills + network isolation), and 313 passing tests.
Adding a page test is now: import `renderWithProviders`, stub routes, assert
behaviour.

**Recommended order:**

1. **Wire the CI frontend job.** Everything else compounds from a gate. The
   PH2.6 placeholder exists; `yarn test:ci` is the command. Until this lands,
   the suite protects nobody who forgets to run it.
2. **Resolve FE-007** — a repository whose production build does not run is a
   release blocker independent of tests. Decide the lint direction (eslint 9 flat
   config, or eslint 8 + CRA's config) and make it consistent.
3. **Close FE-006** — design one error-state treatment and apply it across
   Dashboard, Watchlist, AdminDashboard and NotificationPanel. Tests already pin
   current behaviour, so the change is visible.
4. **Extend page coverage** — Portfolio and TradeMonitor first: the two largest
   untested files and both money-bearing.
5. **Finish the service layer** — `adminService.js` and `brokerService.js` are
   at ~0%; `tradeService.js` shows the pattern.
6. **Cover `RealtimeProvider`'s socket lifecycle** with a controllable fake
   socket — reconnect backoff and heartbeat are real production logic.
7. **Then consider a coverage ratchet** (PH3.11), starting at the achieved level.

**Two cautions carried forward:**

- **Stub ordering** (§14) is a live foot-gun: three tests passed against a
  catch-all instead of their intended stubs during this sprint. Register
  specific routes first.
- **The suite mocks the transport, not the backend contract.** Fixtures mirror
  `backend/server.py` shapes *as of today*. A backend response-shape change will
  not fail these tests. That gap belongs to contract testing (roadmap PH3.5) —
  which is the same argument PH3.1 made about `FakeDB`, one layer up.

---

**Certified:** 2026-08-10
**Suite:** 313 tests / 17 suites / ~8s
**Coverage:** 33.6% overall, 77.0% critical path
**Backend regression:** 1,035 passed — PH3.1 baseline held
