# Technical Debt & Risks Ledger
**Project Name:** StockAssist AI (AlphaPartner)  
**Date:** 2026-07-17  
**Sprint:** SI1.1 — Repository Audit  
**Author:** Lead Architect / CTO  

---

## 1. Known Technical Debt

* **Monolithic Backend Design:** The entire FastAPI routing surface, dependency injections, session checks, database setup, and helper utilities are contained inside the 4,823-line [backend/server.py](file:///Users/shreyash12/Files/alpha_stock/alpha-stock-main/backend/server.py). This makes it difficult to maintain and scale.
* **No Repository Layer:** Route handlers inside `server.py` execute direct, raw MongoDB queries via `db.collection.find(...)` and `update_one(...)`. The lack of an abstraction layer between databases and API handlers makes it hard to change schemas or switch database providers in the future.
* **In-Memory Caching:** The caching logic inside `backend/services/real_market.py` is implemented using local in-memory dictionaries. This cache is lost whenever the server restarts, and it cannot scale across multiple API instances. Redis is installed but is only used for pub/sub real-time updates.
* **Duplicate Quote Fetching Logic:** Enrichment of stock quotes is duplicated across route helpers inside `server.py` and the core service module `real_market.py`.
* **Dead Dependencies:** Unused libraries like `litellm` and the `stripe` python SDK are present in `requirements.txt`, adding package weight and raising the security vulnerability footprint.
* **Frontend State Management:** The frontend client uses a mix of native state hooks (`useState`), `setInterval` polling, and raw WebSocket callbacks to update dashboard components. This lack of a structured server-state query framework (like React Query) results in redundant requests and complex state synchronization bugs.
* **TypeScript Drift:** Plain JavaScript is used in React components, violating the TypeScript requirements documented in [.claude/CODING_STANDARDS.md](file:///.claude/CODING_STANDARDS.md#L47-L54).

---

## 2. Temporary & Mock Implementations

* **Fabricated Admin Analytics (ADR-021 Violations):**
  * `GET /api/admin/analytics/revenue` generates a fake 30-day revenue list using math formulas.
  * Admin dashboard overviews calculate daily revenue using a placeholder variable (`revenue_today = total_payments * 499`).
  * Feature usage analytics return hardcoded percentage indicators.
* **Simulated Market Data Fallbacks:** The helper class `backend/market_data.py` generates random walk prices, sentiment values, VIX indicators, and commodity prices when external feeds fail. This simulated data is served silently to user watchlists, portfolio calculations, and AI prompts without warning.
* **OAuth Bypass Logic:** Legacies in the OAuth code session exchange (`/api/auth/google/session`) permit callers to bypass Google authorization checks by passing a test string. This bypass was removed in Sprint PH1.1 but remains part of the code's historical design context.

---

## 3. Future Refactors

* **Split the FastAPI Monolith (PH3.6):** Move endpoints into modular router directories under `backend/routers/` (e.g., `auth.py`, `market.py`, `portfolio.py`, `admin.py`).
* **Introduce a Repository Layer:** Extract direct MongoDB client queries into repository classes (e.g. `UserRepository`, `TradeRepository`) to isolate data access logic.
* **Consolidate Caching in Redis:** Move in-memory caches from Python dictionaries to Redis, enabling distributed caching.
* **Adopt React Query / TanStack Query:** Refactor frontend networking to use React Query hooks, replacing manual `setInterval` polling and simplifying cache synchronization.
* **Migrate Frontend to Vite + TypeScript:** Move the client compiler from Craco/CRA to Vite, and migrate JavaScript component files (`.jsx`) to TypeScript (`.tsx`).

---

## 4. Deferred Work

* **Subscriptions & Payment Integrations:** Zero endpoints exist for payment processing or subscription plans. The Stripe SDK is installed but unused.
* **Upstox Adapter:** Documented as an adapter in `DECISIONS.md`, but no Upstox adapter is implemented inside the broker services folder. Only Zerodha (Kite Connect) is supported.
* **AI Long-Term Memory:** AI chats persist only for the duration of the browser session. Long-term memory storage is not implemented.
* **Learning Mode Hub:** The application UI displays basic tutorial cards, but there is no dedicated learning hub or automated advisor mentor flows.

---

## 5. Security & Operational Risks

* **Risk of Account Takeovers:** The absence of complexity rules on user registration passwords allows weak passwords (e.g., single characters) to be set.
* **Data Leakage Risk:** Unauthenticated WebSocket rooms (`/api/ws`) allow anyone to subscribe to tick feeds by passing arbitrary user IDs in the query parameters.
* **Service Downtime Risk:** The backend lacks circuit breakers for external Yahoo Finance and Kite Connect APIs. Failure of external APIs causes API requests to hang and time out slowly.
* **Data Loss Risk:** Backups of MongoDB are not automated, and no restore drills have been run or documented.
* **Legal Compliance Risk:** The use of Yahoo Finance data at a commercial scale carries legal exposure, as it violates their redistribution terms.
