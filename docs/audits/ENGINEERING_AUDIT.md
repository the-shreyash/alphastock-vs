# Engineering Practices & Standards Audit
**Project Name:** StockAssist AI (AlphaPartner)  
**Date:** 2026-07-17  
**Sprint:** SI1.1 — Repository Audit  
**Author:** Lead Architect / CTO  

---

## 1. Branch Strategy

The current git layout exhibits:
* **Active Branches:** `main` and `sprint-r3-frontend-realtime` (which is the current active branch).
* **Divergence:** The branching strategy document (`docs/engineering/branching-strategy.md`) is a placeholder and contains no guidelines on branching, staging, or hotfix processes.
* **Findings:** In practice, developers push directly to feature branches, but there are no automated branch protection rules or merge checks configured on the origin repository.

---

## 2. Commit Strategy

* **Guidelines:** `.claude/CODING_STANDARDS.md` specifies conventional commit patterns (e.g. `feat(scope): description`, `fix(scope): description`).
* **Compliance:** Commits generally follow these formats, but there is no server-side or client-side hook (such as `husky` or `commitlint`) enforcing it. This allows non-compliant messages to slip in.

---

## 3. Versioning

* **Policy:** `.claude/INDEX.md` and `.claude/CLAUDE.md` define the current project version as **1.2**.
* **Divergence:** 
  * The frontend client package configuration `frontend/package.json` hardcodes the version as `0.1.0`.
  * The backend has no central version endpoint or version constants file.
  * No Git tags exist to track releases (`v1.0.0`, `v1.2.0`, etc.) in a standard semantic versioning format.
  * The branching/release guide `docs/engineering/semantic-versioning.md` is empty.

---

## 4. Coding Standards

### Backend (Python/FastAPI)
* **monolith:** `backend/server.py` is a 4,823-line monolith that includes routing, configuration, dependencies, WebSocket lifecycle, database seeding, helper functions, and business logic. This directly violates the documented clean architecture guidelines which advocate separating routes, controllers, services, and repository layers.
* **Typing:** Type hints are present on some FastAPI models and functions, but are missing in critical utility functions and helper services.
* **Dev Tooling:** Dev tools like `black` and `flake8` are pinned in `backend/requirements.txt` instead of being isolated in a developer requirements file.

### Frontend (React/JavaScript)
* **TypeScript Mismatch:** `.claude/CODING_STANDARDS.md` mandates that all frontend and backend code be written in TypeScript with strict typing enabled. The actual frontend is built entirely in **plain JavaScript (`.jsx` and `.js` files)**.
* **Linting:** ESLint is configured in package.json but is not run as a pre-commit or pre-push hook, nor is it enforced by any pipeline.
* **Code Duplication:** Scroll animations and UI ring elements are duplicated across multiple pages instead of being extracted into reusable components.

---

## 5. Testing Status

* **Backend (pytest):** 
  * Coverage is relatively deep with **341 passing tests** covering the trading engine, paper trading, and real-time streams.
  * **6 tests fail** in the default run: 1 is a stale assertion in `test_trading_engine.py` (`closed_trades` response check), and 5 are integration tests hitting `http://localhost:8000` which fail because they require a live backend server to run.
* **Frontend:** **Zero tests exist** (no React Testing Library or Jest test files are present).
* **a11y / Performance:** No automated accessibility or load tests exist.

---

## 6. CI/CD Status

* **Status:** **Nonexistent.**
* **Findings:** There is no `.github/workflows` folder or any pipeline settings for automated builds, lint checks, test suites, or deployments. All builds and deployments are hand-triggered, increasing the risk of config drift and regression issues.

---

## 7. Security Practices

* **Strengths:** Solid credentials hygiene (no `.env` or API keys checked in), bcrypt password hashing, and user role-based access checks (RBAC) on admin endpoints.
* **Weaknesses:**
  * CORS wildcard configuration (`allow_origins="*"`) mapped alongside credentials allowed.
  * Auth cookies set with `secure=False`, allowing them to transmit over plain HTTP.
  * WebSocket routes are completely unauthenticated (`user_id` taken from raw query parameters with no token validation).
  * No brute-force rate-limiting configured on sensitive paths.

---

## 8. Recommendations

1. **Implement Husky Pre-Commit Hooks:** Add `husky` to check ESLint, black code formatting, and conventional commits before changes are committed.
2. **Modularize the Monolith (PH3.6):** Split `server.py` into separate domain routes (`/routes/auth.py`, `/routes/market.py`, `/routes/portfolio.py`) and centralize WebSocket handlers.
3. **Build the CI/CD Pipeline (PH2.5-2.6):** Create a GitHub Actions workflow to run backend pytest runs (excluding integration tests) and compile the React production build on every pull request.
4. **Fix and Isolate Tests (PH3.1):** Skip integration tests by default using pytest markers (`pytest -m "not integration"`), and update the stale assertions.
5. **Add Frontend Tests (PH3.3):** Set up Jest and React Testing Library and write basic smoke tests for user logins, router switching, and context loading.
