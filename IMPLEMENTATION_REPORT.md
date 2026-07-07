# StockAssist AI — Implementation Report

Sprint: 1 — Project Audit
Date: 2026-07-07
Status: AWAITING APPROVAL — no code changes made
Scope: Read-only audit of the entire repository against `.claude/` documentation
(CLAUDE.md, PROJECT.md, PRODUCT_REQUIREMENT.md, SYSTEM_ARCHITECTURE.md, ROADMAP.md, TASK.md, DECISIONS.md)

---

# 1. Executive Summary

The project is substantially more complete than TASK.md suggests (Milestones 2–5 marked NOT_STARTED are largely built), but there is a **critical, unrecorded architectural divergence**: the entire documented tech stack does not match the implemented one.

| | Documented (.claude) | Actual (repository) |
|---|---|---|
| Backend | Node.js + Express + TypeScript | **Python 3.11 + FastAPI** (`backend/server.py`) |
| Frontend build | Vite + TypeScript | **CRA + Craco, plain JavaScript** (`frontend/craco.config.js`) |
| Server state | React Query | **Manual `useState` + `setInterval` polling** |
| Cache / queue | Redis + BullMQ | **In-memory dict + APScheduler + n8n webhooks** |
| API versioning | `/api/v1` | **`/api` (unversioned)** |
| Brokers | Zerodha + Upstox | **Zerodha only** (Kite Connect, full) |
| Payments | Razorpay + Stripe | **None** (Stripe SDK installed, unused) |
| Branding | StockAssist AI | **AlphaPartner** (emails, admin seed, tests) |

**Verdict:** The actual implementation is a coherent, working platform (105+ endpoints, 21 frontend pages, dual-AI debate engine, Zerodha live trading, paper trading, backtesting, journal, n8n automation, ~2,100 lines of tests). However, it is **not production-ready** due to: (a) critical security gaps, (b) silent mock-data fallbacks in live endpoints (violates ADR-021), (c) a 2,961-line monolithic `server.py` (violates SYSTEM_ARCHITECTURE.md layering), and (d) documentation that describes a different system than the one that exists.

**Decision required before Sprint 2** (see §10): either update documentation to ratify the FastAPI/JS stack via a new ADR, or plan a migration. All other work depends on this decision.

---

# 2. Current Architecture (As Built)

## 2.1 Backend — `backend/`

- **FastAPI 0.110.1 + Uvicorn**, async throughout; MongoDB 7 via `motor`; APScheduler 3.11 (IST cron) for morning/EOD/weekly jobs; Socket-style realtime via native FastAPI WebSocket (`/api/ws`, server.py:1573).
- **Structure:** `server.py` (2,961 lines — routers, auth, AI helpers, advisor engine, WebSocket manager, startup/shutdown all in one file), `models.py` (191 lines, Pydantic), `market_data.py` (288 lines, simulated data), `services/` (23 modules: `ai_debate_engine`, `claude_provider`, `gemini_provider`, `gemini_direct`, `real_market`, `zerodha_service`, `paper_trade`, `backtest_engine`, `news_service`, `email_service`, `whatsapp_service`, `scheduler`, `trade_journal`, …).
- **API surface:** ~105 routes across 24 prefixes: `/api/auth`, `/api/market`, `/api/stocks`, `/api/analysis`, `/api/trades`, `/api/portfolio`, `/api/notifications`, `/api/chat`, `/api/sip`, `/api/advisor`, `/api/settings`, `/api/zerodha`, `/api/monitor`, `/api/whatsapp`, `/api/email`, `/api/news`, `/api/journal`, `/api/webhooks`, `/api/watchlist`, `/api/paper`, `/api/backtest`, `/api/gemini`, Google auth, WebSocket.
- **AI:** Dual-provider "debate" engine (`services/ai_debate_engine.py:29`) — Claude + Gemini in parallel, cross-review, synthesized verdict; per-feature preferred-provider fallback (`simple_chat`, line 200). LiteLLM is in `requirements.txt` but unused. `gemini_direct.py` hardcodes `gemini-2.0-flash` / `gemini-2.5-flash`. This is a reasonable simplification of the documented 14-agent SAI orchestrator, but is not the documented architecture.
- **Market data:** Yahoo Finance HTTP (`services/real_market.py`), 30–60s in-memory TTL cache, RSI/MACD/VWAP computation, pattern detection; Alpha Vantage optional. **Every live path falls back to random simulated data from `market_data.py`** (see §4).
- **Broker:** Zerodha Kite Connect fully implemented (OAuth session, holdings, positions, orders, funds, postback webhook, emergency-stop) — `services/zerodha_service.py`, routes at server.py:1859–2315. Upstox: absent.
- **Automation:** 4 n8n workflows (`n8n/*.json`) → authenticated webhooks (`X-Webhook-Key`, timing-safe compare, server.py:2385) for morning scan, evening summary, weekly review, news digest; invocations logged to `webhook_logs`.
- **DB:** 12 collections; startup indexes on `users.email` (unique), `login_attempts`, `trades.user_id`, `notifications.user_id`, `chat_messages`, `broker_accounts` (server.py:2860–2865).

## 2.2 Frontend — `frontend/`

- **React 19 + CRA/Craco + JavaScript**, React Router 7, Tailwind 3.4 + shadcn/ui (60+ primitives in `src/components/ui/`), Framer Motion + GSAP, Recharts + lightweight-charts, axios.
- **API layer is correctly centralized** in `src/services/api.js` — single axios instance, Bearer token from localStorage, 401 → refresh-token retry interceptor with loop guards. No component calls fetch/axios directly. Base URL from `REACT_APP_BACKEND_URL`.
- **Auth:** `context/AuthContext.jsx` — email/password + Google OAuth (via session flow), auto-login (dev), `ProtectedRoute`/`PublicRoute` guards in `App.js:28–49`.
- **Pages (21 routes, App.js):** Landing, Login, Register, BrokerCallback, Dashboard, Markets, Watchlist, StockPicks, StockDetail, TradeMonitor, Portfolio, AIAssistant, SIPAdvisor, InvestmentAdvisor (new), News, TradeJournal, Settings, PaperTrading, Backtesting, MorningReport.
- **Realtime:** `hooks/useWebSocket.js` (market ticks, trade/portfolio updates, alerts, activity feed; 5s reconnect) plus 15–60s polling intervals on Dashboard/Markets/TradeMonitor/Navbar.
- **Theme:** Full light/dark via CSS variables (`index.css`) + `ThemeContext`, consistent glassmorphism design language — matches UI_GUIDELINES intent.
- **New untracked files:** `components/AIQuickAction.jsx` (351 lines, floating context-aware AI chat FAB, wired in `Layout.jsx`) and `pages/InvestmentAdvisor.jsx` (425 lines, multi-horizon AI recommendations, routed at `/advisor`) — both well-built and integrated.

## 2.3 Infrastructure

- `docker-compose.yml`: MongoDB 7 + FastAPI backend + n8n (basic auth). No Redis. No CI/CD pipeline found. `.env` files exist locally and are correctly git-ignored (`.gitignore:85`).

---

# 3. Feature Status vs Documentation

| Feature (PRD §) | Status | Evidence / Gap |
|---|---|---|
| Authentication (JWT, refresh, brute-force lockout) | ✅ Done | server.py:727–815; 5-attempt/15-min lockout |
| Google OAuth | ⚠️ Done with dev bypass | server.py:1735 demo-user fallback (§5.1-C4) |
| Landing page | ✅ Done | Landing.jsx (545 lines) |
| Dashboard | ✅ Done | Live overview, picks, AI activity feed |
| Markets (heatmap, movers, breadth, sectors, FII/DII, global) | ✅ Done | Markets.jsx; FII/DII often mock (§4) |
| Stock Detail workspace | ✅ Done | Chart, patterns, AI summary, alerts |
| Stock Scanner | ⚠️ Partial | Picks generated via `/api/analysis/top-picks`; no dedicated scanner page/filters |
| AI Workspace / Chat | ✅ Done | `/assistant` + AIQuickAction FAB; context-aware |
| Morning Report | ✅ Done | APScheduler + n8n + email; `/morning-report` page |
| Portfolio + AI health | ✅ Done | Portfolio.jsx, `/api/portfolio` |
| Watchlist | ✅ Done | End-to-end (recent commit 04e8ec3) |
| Trade Monitor + AI coaching | ✅ Done | TradeMonitor.jsx, coaching/live-tips routes |
| Trade Journal | ✅ Done | `/api/journal` stats, weekly review, setup stats |
| Paper Trading | ✅ Done | `/api/paper/*`; ₹100,000 virtual balance |
| Backtesting | ⚠️ Basic | Single-strategy `/api/backtest`; no equity curve/drawdown outputs per PRD §24 |
| Strategy Builder | ❌ Missing | PRD §19 — no NL-to-strategy feature |
| Broker: Zerodha | ✅ Done | Full Kite Connect incl. live orders |
| Broker: Upstox | ❌ Missing | Documented in PRD §17, DECISIONS ADR-007 |
| Notifications | ⚠️ Partial | Email ✅ (SendGrid/SMTP/simulated), WhatsApp ✅ (Twilio), WebSocket push ✅, Telegram partial, browser push ❌ |
| Search | ⚠️ Partial | SearchBox component + `/api/stocks/search`; no unified search (news/journal/holdings) per PRD §27 |
| Subscriptions / Plans / Credits | ❌ Missing | PRD §34–36 — zero implementation (Stripe SDK unused) |
| Payments (Razorpay/Stripe) | ❌ Missing | No endpoints |
| Admin Portal | ❌ Missing | PRD §32–43 — admin user seeded, but no admin routes/UI, no user management, no API monitoring, no feature flags, no analytics |
| AI usage limits / cost tracking | ❌ Missing | PRD §35, §38 — no per-user limits or token accounting |
| Multi-agent SAI orchestrator (14 agents) | ❌ Divergent | Replaced by 2-provider debate engine (simpler, working) |
| AI Memory (long-term) | ❌ Missing | Chat history persists per session only |
| Learning Mode / Mentor | ⚠️ Minimal | Lessons card on Dashboard; no learning center |
| Audit logging | ❌ Missing | No audit trail for admin/broker actions |
| Redis / BullMQ | ❌ Missing | In-memory cache; APScheduler local |
| Mobile/PWA | ❌ Future | Responsive web only |

Rough completion: **Phase 1–2 ≈ done, Phase 3 (AI) ≈ 70%, Phase 4 (pro tools) ≈ 50%, Phase 5 (SaaS/billing) ≈ 0%, Phase 6 (admin/ops) ≈ 5%.**
TASK.md is badly stale — it lists Milestones 2–5 as NOT_STARTED although most of that work exists.

---

# 4. Mock Data Inventory (ADR-021 Violations)

ADR-021: "Every production feature must use live APIs." Current state: live-first with **silent random-data fallbacks** — users cannot tell real from simulated.

**Source of all mock data: `backend/market_data.py` (entire file, 288 lines).**
- Hardcoded base prices for a 30-stock universe (lines 5–36): RELIANCE 2890, TCS 3680, HDFCBANK 1740, …
- `_jitter()` (line 51) applies `random.uniform` noise to every value.
- Index bases (lines 54–75): Nifty 24180, BankNifty 52340, Sensex 79820; VIX = random 11–18; sentiment = random 35–80; advance/decline = random.
- Commodities (lines 126–131), Global markets (115–122), **FII/DII = pure random ±₹3,500 Cr** (135–138).
- `get_stock_quote()` (line 143): random price ±3%, RSI random 30–80, MACD random ±5, volume ratio random 0.8–2.0.

**Live endpoints that serve this mock data on fallback (server.py):**
1. `GET /api/market/overview` (819) → `real_overview()` (119) → mock on Yahoo failure.
2. `GET /api/market/gainers` / `losers` (824–832) → random 5 stocks with random % change.
3. `GET /api/stocks/{symbol}` (875) → random quote incl. random technicals.
4. `GET /api/advisor/recommend` (1458) → `build_advisor_recommendations()` (510): if universe fetch fails (541–545), **AI recommendations are computed over random prices** (`data_source="fallback"` is set internally but not surfaced consistently).
5. `GET /api/market/summary` (854) → AI summary prompt may be fed mock overview/sectors/FII-DII.
6. Morning report (990) → overview from `real_overview()` mock fallback.
7. Watchlist quotes (2500–2555), paper-trading prices, backtest prices, trade coaching current-price context — all route through `real_quote()` which falls back to mock.
8. `GET /api/market/fii-dii` → real fetch attempted; in practice mock random values.

**Frontend hardcoded data (mostly acceptable):**
- `Dashboard.jsx:46–52` AI activity placeholders (loading fallback — acceptable).
- `Dashboard.jsx:282` and `Markets.jsx:115` hardcoded market-breadth fallback `{advances:1042, declines:842, unchanged:176}` — silently fake.
- `Landing.jsx:77–107` placeholder pricing tiers (Free/₹499/₹1,499) — marketing only, but must be reconciled with the (nonexistent) subscription system before launch.

**Required remediation (proposal):** every market payload must carry `data_source: "live" | "cached" | "simulated"`, the UI must badge simulated data, and production config must be able to disable fallbacks entirely (fail with a proper error state instead).

---

# 5. Bugs & Security Findings

## 5.1 Critical (fix before any deployment)

| # | Finding | Location |
|---|---|---|
| C1 | **Default admin password `admin123`** seeded if `ADMIN_PASSWORD` unset; same credentials hardcoded in tests | server.py:2872; backend/tests/test_backend.py:12 |
| C2 | **Auth cookies `secure=False`** (sent over plain HTTP) | server.py:198–199, 811, 1847 |
| C3 | **CORS default `*` with `allow_credentials=True`, all methods/headers** — CSRF exposure | server.py:2846–2851 |
| C4 | **Google OAuth bypass:** `code == "mock-code-for-testing"` (or missing client credentials) logs in as `demo-user@alphapartner.com` without token verification | server.py:1735–1798 |
| C5 | **WebSocket unauthenticated** — `user_id` taken from raw query param; any client can subscribe as any user (trade/portfolio update leakage) | server.py:1573–1600 |
| C6 | **No password policy** — `UserCreate.password` is unconstrained `str`; 1-char passwords accepted | backend/models.py:35 |

## 5.2 High

| # | Finding | Location |
|---|---|---|
| H1 | Silent mock fallback on AI-driven recommendations (users may act on random data) | server.py:537–545, §4 |
| H2 | No rate limiting on `/api/auth/register` (login has lockout; register does not) | server.py:727 |
| H3 | JWT stored in localStorage on frontend (XSS-exfiltrable) while backend also sets httponly cookies — two parallel auth transport mechanisms | frontend/src/services/api.js:14; AuthContext.jsx |
| H4 | No pagination on list endpoints (`.to_list(None)`) — trades, notifications, chat history, journal; memory/DoS risk | e.g. server.py:2149 |
| H5 | n8n basic-auth password hardcoded `alphapartner123` in docker-compose | docker-compose.yml:~101 |
| H6 | Verbose error detail leaks upstream API responses to clients | e.g. server.py:1754 |
| H7 | No audit logging for admin actions or live Zerodha order placement | — |

## 5.3 Medium / Bugs

- `uid` str-vs-ObjectId inconsistency papered over with defensive casts (server.py:42–46) — root cause unfixed; risk of silent query mismatches.
- Trade coaching prompt uses stale entry-price context instead of fetching current quote (server.py:1246+).
- Paper-trading balance mutation is not concurrency-safe (`services/paper_trade.py`).
- Inconsistent error envelope: mix of `{"error": ...}` and FastAPI `{"detail": ...}`.
- Frontend: many pages swallow errors with `console.error` only (Portfolio.jsx:88,100; StockPicks.jsx:114,138,165; TradeJournal.jsx:86,107; Dashboard.jsx:378; StockDetail.jsx:302,324) — violates CLAUDE.md error-handling rules (no user-visible error/retry).
- Frontend: leftover debug logs "Zerodha: Simulated order" (TradeMonitor.jsx:191,194).
- No circuit breaker for Yahoo/Kite outages — every request re-attempts and times out slowly.

---

# 6. Technical Debt

1. **Monolithic `server.py` (2,961 lines)** — routers, auth utils, AI helpers, a ~390-line advisor engine, WebSocket manager, and startup logic in one file. Violates the documented controllers→services→repositories layering. Should split into `routes/`, `core/` (auth, config), `websocket/`.
2. **No repository layer** — raw `db.collection` access inline in route handlers throughout.
3. **In-memory caching** (`real_market.py:14–55`) — non-distributed, lost on restart; Redis documented but absent.
4. **Duplicate market-data logic** — `real_quote()`/`real_quotes_map()`/`real_overview()` in server.py wrap near-identical logic in `services/real_market.py`.
5. **LiteLLM and Stripe SDKs installed but unused** (requirements.txt) — dead dependencies.
6. **Tests are integration tests** hitting live Yahoo/AI endpoints with hardcoded admin credentials; no external-API mocking; require running MongoDB; flaky by design. `tests/_fakedb.py` exists but is barely used.
7. **Frontend polling instead of React Query** — redundant requests, no dedup/cache; 5 different intervals (15s/30s/60s/5m) plus WebSocket producing overlapping updates and re-render storms.
8. **No lazy loading / code splitting** — all 21 pages statically imported in App.js; single large bundle.
9. **No list virtualization or pagination** on Holdings/Trades/Watchlist tables.
10. **Missing type safety on both sides** — Python functions lack return hints in places; frontend has zero TypeScript despite Zod being installed (unused validation opportunity).
11. **TASK.md / documentation drift** — the master tracker does not reflect ~2 milestones of shipped work; INDEX.md references filenames that don't exist (`PRODUCT_REQUIREMENTS.md` vs actual `PRODUCT_REQUIREMENT.md`, `TASKS.md` vs `TASK.md`, `PROMPTS.md` vs `PROMPT.md`).

---

# 7. Dead Code & Duplicates

**Dead code:**
- `frontend/src/components/dashboard/WhatsAppPanel.jsx` — not imported anywhere.
- `frontend/src/components/dashboard/PortfolioMonitor.jsx` — not imported anywhere (contains its own 60s polling logic that would double requests if ever mounted as-is).
- Unused backend deps: `litellm`, `stripe` (and effectively Alpha Vantage path if no key ever configured).
- `.claude/architecture.md` (223 bytes) and `.claude/DEVELOPMENT_WORKFLOW.md` (204 bytes) — stub files.

**Duplicates:**
- `Reveal` scroll-animation component copy-pasted into 10+ pages (Dashboard.jsx:11–24 and identical blocks in Markets, Portfolio, TradeMonitor, …) — should be `components/Reveal.jsx`.
- Score/confidence ring implemented twice: `Portfolio.jsx:34–49` (`ScoreRing`) and `InvestmentAdvisor.jsx:54–72` (`ConfidenceRing`).
- Stat-card patterns re-implemented per page with drifting styles.
- Backend: quote-fetching + enrichment duplicated between server.py helpers and `real_market.py` (see §6.4).

---

# 8. Architecture Inconsistencies

1. **Docs vs reality (stack):** every architecture document describes Node/Express/TS + Redis + BullMQ + `/api/v1`. None of it matches. This is the single largest inconsistency in the project and blocks meaningful use of the documentation.
2. **Branding:** code, emails (`alerts@alphapartner.ai`, `email_service.py:19–20`), admin seed (`admin@alphapartner.com`), and n8n say "AlphaPartner"; docs say "StockAssist AI".
3. **Layering:** business logic (advisor scoring, AI prompt construction, portfolio math) lives inside route-handler bodies in server.py rather than services — contradicts SYSTEM_ARCHITECTURE.md "business logic belongs only inside Services".
4. **Dual auth transport:** backend sets httponly cookies AND returns bearer tokens that the frontend stores in localStorage; only one mechanism should exist (cookies preferred per SECURITY.md intent).
5. **Two realtime mechanisms** (WebSocket + polling) updating the same views without coordination.
6. **AI architecture:** documented 14-agent SAI orchestrator with planner/router/reflection/debate vs implemented 2-provider debate + direct calls. The implementation is pragmatic; the docs should be revised to describe it (or a target evolution path), not a fictional system.

---

# 9. What Is Genuinely Good

- Clean, consistent premium UI with a real design system (CSS variables, glass cards, light/dark parity) — aligns with UI_GUIDELINES/DESIGN_SYSTEM.
- Centralized frontend API layer with refresh-token handling done correctly.
- Real Yahoo-Finance pipeline with computed technicals and pattern detection.
- Working dual-AI debate engine with provider fallback — a solid foundation for the SAI vision.
- Full Zerodha integration including postback webhooks and emergency-stop.
- n8n automation with authenticated (timing-safe) webhooks and invocation logging.
- Broad test suite (16 files, ~2,169 lines) covering nearly every router.
- Login brute-force lockout, bcrypt hashing, timing-safe webhook comparison — some security fundamentals are present.

---

# 10. Recommendations (Proposed Sprint Plan — pending approval)

## Decision Gate (must resolve first)
**D1. Ratify or migrate the stack.** Recommended: **ratify FastAPI + React/JS as the official stack** via new ADRs (ADR-026 backend = FastAPI/Python, ADR-027 frontend = CRA→Vite migration path, ADR-028 TypeScript adoption plan, ADR-029 Redis deferral) and rewrite SYSTEM_ARCHITECTURE.md/CLAUDE.md to match reality. A Node/TS rewrite would discard a working 105-endpoint backend for zero user value.
**D2. Confirm product name** (StockAssist AI vs AlphaPartner) — affects emails, seeds, tests, n8n, docs.

## Sprint 2 — Security Hardening (Critical, small diffs)
1. Remove `admin123` default → require `ADMIN_PASSWORD` or generate random + log-once (server.py:2872).
2. `secure=True` cookies behind env flag; pick ONE auth transport (httponly cookie) and remove localStorage token.
3. CORS: explicit origin whitelist; drop wildcard default.
4. Delete Google OAuth demo-user bypass (server.py:1735); gate any dev bypass behind `ENABLE_AUTO_LOGIN`-style explicit env, off by default.
5. Authenticate WebSocket via JWT (token in query/subprotocol → verify → derive user_id).
6. Password policy (min length/complexity) in `UserCreate`; rate-limit register.
7. Remove hardcoded n8n password from docker-compose (env var).

## Sprint 3 — Data Integrity (ADR-021 compliance)
1. Add `data_source` field to every market/advisor payload; UI badge for simulated data.
2. `DISABLE_MOCK_FALLBACK=true` production mode → proper error states instead of random data.
3. Real FII/DII source or explicit "unavailable" state (never random money flows).
4. Circuit breaker + retry policy for Yahoo/Kite.

## Sprint 4 — Structural Refactor
1. Split server.py into `routes/`, `core/`, `websocket/`; extract advisor engine to `services/advisor.py`.
2. Standardize error envelope; add pagination to all list endpoints.
3. Frontend: extract `Reveal`, `ScoreRing`; delete or wire WhatsAppPanel/PortfolioMonitor; adopt React Query (replaces polling), add route-level lazy loading; surface errors to users with retry.
4. Fix uid type inconsistency; make paper trading concurrency-safe.

## Sprint 5+ — Missing Product Pillars (per ROADMAP phases)
- Subscriptions + payments + AI usage limits (PRD §34–36, §35) — currently 0%.
- Admin portal (PRD §32–43).
- Upstox adapter (or formally defer via ADR).
- Backtesting outputs to PRD spec (equity curve, drawdown, profit factor).
- Update TASK.md to reflect true milestone status (this report's §3 can seed it).

---

# 11. Audit Metadata

- Backend: 40+ Python files reviewed (server.py 2,961 ln; services/ 23 modules; tests/ 16 files).
- Frontend: 21 pages, 70+ components, services/context/hooks reviewed.
- Docs: 8 required `.claude` documents read in full; filename drift noted (§6.11).
- No code was modified. `.env` files confirmed git-ignored; no secrets found committed.

**Next step:** review §10 decision gate and sprint proposals. No implementation will begin until approved.
