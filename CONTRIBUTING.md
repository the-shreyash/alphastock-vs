# Contributing to StockAssist AI

Thank you for contributing to StockAssist AI! This document outlines our development guidelines, coding standards, branch strategies, and pull request processes. By following these rules, we ensure a high standard of code quality and maintainability.

---

## Code of Conduct
We expect all developers to write high-quality, professional-grade code, respect security protocols, and collaborate constructly.

---

## Development Setup

### Local Prerequisites
- **Python:** 3.11.x (installed and accessible in terminal path)
- **Node.js:** 18.x or 20.x
- **Yarn:** 1.22.x
- **MongoDB:** Version 6.0+ (running locally or a credentials URI)
- **Redis:** Running local instance (required for Socket.IO event bridge)

### Database Seeding
To initialize your development database with an administrative account without compromising credentials, run the dev seed script:
```bash
cd backend
python scripts/seed_dev_admin.py
```
*Note: This script will refuse to execute when `APP_ENV=production` is set to ensure production safety.*

---

## Branch Strategy
We organize development around sprints and hardening tracks.

### Branch Naming Conventions
- **Hardening / Security Sprints:** `ph1.*`, `ph2.*`, or `ph3.*` (e.g., `ph1.2-cors-headers`)
- **Features:** `feat/<component-name>` (e.g., `feat/whatsapp-alerts`)
- **Bug Fixes:** `fix/<bug-description>` (e.g., `fix/jwt-expiration`)
- **Refactoring:** `refactor/<target-area>` (e.g., `refactor/server-routers`)
- **Documentation:** `docs/<target-docs>` (e.g., `docs/api-specs`)

### Merge Pipeline
All code branches merge into the primary active sprint or branch (e.g., `sprint-r3-frontend-realtime`) before final merge to `main`. direct pushes to `main` are strictly protected and blocked.

---

## Commit Convention
We enforce the **Conventional Commits** specification. Commits should be structured as follows:

```
<type>(<scope>): <description>
```

### Supported Types
- `feat`: A new user-facing feature.
- `fix`: A bug fix.
- `docs`: Documentation-only changes.
- `style`: Changes that do not affect the meaning of the code (formatting, missing semi-colons, etc.).
- `refactor`: A code change that neither fixes a bug nor adds a feature.
- `test`: Adding missing tests or correcting existing tests.
- `chore`: Changes to the build process, packages, or auxiliary tools.

### Examples
- `feat(scanner): add relative strength index scanner criteria`
- `fix(auth): correct token rotation handling on expired cookies`
- `refactor(db): split database configuration into separate config file`
- `test(portfolio): add validation tests for stock ticker formats`

---

## Coding Standards

### Python (Backend)
- **Style Guide:** Follow **PEP 8**. We use `black` for automated code formatting and `isort` for imports.
- **Safety:** Validate all inputs using Pydantic schemas. Avoid raw database concatenation; always write parameterized queries.
- **Error Handling:** Avoid silent failures or exposing raw exceptions to client response payloads. Use defined service exceptions.
- **Type Annotations:** Use Python type hints consistently for function inputs and returns.

### JavaScript/React (Frontend)
- **Components:** Components must reside under `frontend/src/components/` and pages under `frontend/src/pages/`.
- **Styling:** Use Tailwind CSS utility classes inside components. Custom styles must be integrated using custom CSS variables inside `frontend/src/index.css`.
- **Code Splitting:** Implement route-level lazy loading for page-level elements to preserve page load times.
- **Accessibility:** Adhere to accessibility (a11y) checklists (proper labels, key triggers, color contrast).

---

## Documentation Standards
Documentation is an active part of our coding process:
1. **Sync Documentation:** If a pull request modifies an API path, schema, or deployment step, update the corresponding file under `.claude/` (e.g., [API_REFERENCE.md](file:///.claude/API_REFERENCE.md)).
2. **Commentary:** Document complex algorithms, failovers, and integrations. Preserve docstrings and class summaries.
3. **Changelog:** Always append a descriptive summary of changes in the unreleased section of `CHANGELOG.md`.

---

## Pull Request Process

1. **Verify Builds:** Run tests and build checks locally before pushing:
   - Backend: Run `pytest`
   - Frontend: Run `yarn build`
2. **Secrets Check:** Verify that no credentials, tokens, or environment overrides are committed.
3. **No TODOs:** All `TODO`, `FIXME`, or `HACK` markers must be addressed or scheduled as discrete issues before requesting review.
4. **Code Review:** Every PR requires at least one peer approval.
5. **No Features During Feature Freeze:** Merging new product features is disabled during Production Hardening cycles (PH1–PH3).

---

## Issue Reporting
If you identify a bug or would like to request a feature, please file a GitHub Issue with the following details:
- **Title:** Clean, descriptive summary.
- **Environment:** OS, browser, package versions, and environment tags (e.g., local dev, staging).
- **Steps to Reproduce:** Sequential details to duplicate the issue.
- **Expected vs. Actual Behavior:** What should have happened versus what actually occurred.
- **Logs / Screenshots:** Attach relevant stack traces or console screenshot elements.
