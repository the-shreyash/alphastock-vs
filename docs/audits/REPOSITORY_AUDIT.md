# Repository Structure & Organization Audit
**Project Name:** StockAssist AI (AlphaPartner)  
**Date:** 2026-07-17  
**Sprint:** SI1.1 — Repository Audit  
**Author:** Lead Architect / CTO  

---

## 1. Folder Structure Review

The repository uses a split monorepo-style structure, organizing the backend API and frontend client into separate root directories:

```
/ (Root)
├── .claude/               # AI context guides, rules, and Architectural Decision Records
├── backend/               # Python/FastAPI Application Code
│   ├── services/          # Business logic, broker integrations, and engine files
│   ├── scripts/           # Dev database seeding scripts
│   ├── tests/             # Backend test suite (pytest files)
│   └── venv/              # Local Python virtual environment
├── docs/                  # Project-wide documentation folders (business, engineering, operations)
├── frontend/              # React Application Code
│   ├── public/            # Static assets and entry HTML
│   └── src/               # React components, routing, states, and hooks
├── memory/                # Project memory/cache directory (historical data)
├── n8n/                   # Automation workflows (JSON files)
├── test_reports/          # Test execution history and reports
└── tests/                 # Placeholder root tests folder (empty)
```

### Analysis
* **Strengths:** Clear top-level separation between the frontend user client (`frontend/`) and the backend service APIs (`backend/`).
* **Weaknesses:** Subfolders are not fully standardized. For instance, the root contains a `tests/` folder which is empty, while all actual backend tests reside in `backend/tests/`. The `docs/` folder contains empty placeholder files, while the actual core documentation is stored inside `.claude/`.

---

## 2. Naming Conventions

The repository follows a mix of Python and JavaScript naming standards:

| Entity | Standard Pattern | Compliance Status | Findings & Examples |
| :--- | :--- | :--- | :--- |
| **Python Directories** | lowercase, snake_case | **Mostly Compliant** | `backend/services/market_engine` is correct. |
| **Python Files** | lowercase, snake_case | **Mostly Compliant** | `server.py`, `models.py`, `market_data.py`. |
| **React Components** | PascalCase | **Compliant** | `AIAssistant.jsx`, `Dashboard.jsx`, `Layout.jsx`. |
| **React Hooks/Context** | camelCase | **Compliant** | `useWebSocket.js`, `AuthContext.jsx`. |
| **Automation JSON** | snake_case | **Compliant** | `morning_scan_workflow.json` in `n8n/`. |

---

## 3. Configuration Files

Several workspace configuration files are distributed across the repository:

### Root Level
* **`.gitignore`:** Standard Git exclusion mapping. Correctly filters environment files, system cache, package dependencies (`node_modules`), python venvs, and logs.
* **`.env` / `.env.example`:** Defines environment keys. Mismatch identified: the `.env.example` contains Node/Express keys, but the actual stack is Python/FastAPI.
* **`docker-compose.yml`:** Defines the multi-container stack (MongoDB, FastAPI, n8n). Issues: refers to non-existent `Dockerfile` templates, contains hardcoded basic-auth passwords, and exposes MongoDB publicly without proper authentication configurations.

### Frontend Level
* **`package.json` / `package-lock.json` / `yarn.lock`:** Inconsistent use of yarn and npm package managers (both locks are checked in, which can lead to package resolution conflicts).
* **`craco.config.js`:** Customizes CRA webpack configuration to load Tailwind/PostCSS.
* **`jsconfig.json`:** Configures path mappings for module imports.

### Backend Level
* **`requirements.txt`:** Lists Python pip dependencies. Unnecessary runtime packaging of dev tools (e.g. `black`, `flake8`) found.

---

## 4. Scripts

* **`backend/scripts/seed_dev_admin.py`:** Added in Sprint PH1.1. Seeds a developer admin user when the environment is not production (`APP_ENV != production`). Correctly replaces security-risk backdoors.
* **Absence of Shell Automation:** No root shell scripts or makefiles are present to simplify starting both servers locally, running tests, or cleaning cache directories.

---

## 5. Missing Folders

* **`.github/`:** No GitHub metadata folder. This means there are no GitHub Actions workflows, PR templates, issue templates, or codeowner definitions.
* **`backend/routers/`:** There is no dedicated router folder in the backend. FastAPI endpoints are mostly packed inside the single `server.py` monolith rather than being modularized into router files.
* **`frontend/src/tests/`:** No frontend testing structure exists.
* **`docs/audits/`:** Lacked a centralized repository for technical audits prior to this sprint.

---

## 6. Duplicate Folders

* **`tests/`:** The empty root `tests/` directory is redundant, as actual tests are located in `backend/tests/`. 
* **`memory/`:** Overlaps conceptually with cache and test data storage.

---

## 7. Repository Organization

The overall separation of components is logical, but operational hygiene needs improvement:
* The root directory is cluttered with report files like `PRODUCTION_READINESS_REPORT.md` and `IMPLEMENTATION_REPORT.md`. These should reside in audit folders to preserve root cleanliness.
* The presence of both `package-lock.json` and `yarn.lock` in the `frontend` folder causes install inconsistencies.

---

## 8. Recommendations

1. **Delete Empty Root Tests:** Remove `/tests` at the root and ensure all backend testing stays in `/backend/tests`.
2. **Modularize API Routes:** Create `backend/routers/` and begin splitting the 4,823-line `server.py` monolith into router files (e.g., `auth.py`, `market.py`, `portfolio.py`).
3. **Clean Up Lockfiles:** Choose one package manager for the frontend (either `yarn` or `npm`) and delete the unused lockfile.
4. **Create a Root Scripts Directory:** Add a `/scripts` directory at the root to host startup helper scripts (e.g., `start-dev.sh`, `run-tests.sh`).
5. **Reconcile Config Files:** Update `.env.example` to remove the Express/Node.js variable names and map Python environment variables accurately.
