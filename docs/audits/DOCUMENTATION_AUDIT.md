# Documentation Quality & Accuracy Audit
**Project Name:** StockAssist AI (AlphaPartner)  
**Date:** 2026-07-17  
**Sprint:** SI1.1 — Repository Audit  
**Author:** Lead Architect / CTO  

---

## 1. Existing Documentation

The project maintains two distinct documentation suites:

### A. Core Developer Context Guides (`.claude/`)
These are comprehensive, high-quality architectural, product, and design documents meant to boot-strap AI assistants and developers:
* **`INDEX.md`:** Entry point and reading map for developers and AI agents.
* **`CLAUDE.md`:** Core engineering guidelines, philosophies, and development constraints.
* **`PROJECT.md`:** Product vision, mission, user flows, and core features.
* **`PRODUCT_REQUIREMENT.md`:** In-depth product and feature specifications.
* **`SYSTEM_ARCHITECTURE.md`:** Comprehensive backend architecture specification.
* **`REALTIME_SYSTEM.md`:** Real-time push, Redis Pub/Sub, and WebSocket protocols.
* **`MARKET_DATA_ARCHITECTURE.md`:** Provider-independent gateway and failover guidelines.
* **`DATABASE.md`:** MongoDB schemas and relationships.
* **`SECURITY.md`:** Identity standards, cookie policies, rate limiting rules, and audit requirements.
* **`DEPLOYMENT.md`:** Deployment pipeline, infrastructure, and backup strategies.
* **`AI_AGENT_SYSTEM.md`:** Details on the multi-agent AI system.
* **`PROMPT.md`:** prompt library for LLM features.
* **`DECISIONS.md`:** Architectural Decision Records (ADRs).
* **`PRODUCTION_HARDENING.md`:** Production Hardening program specifications.
* **`PRODUCTION_ROADMAP.md`:** 36-sprint hardening plan.
* **`CHANGELOG.md`:** Standalone changes and release notes.

### B. Standard Repository Docs (`docs/`)
Folders intended to hold public/operational guidelines:
* **`docs/business/`:** `pricing.md`, `subscription-model.md`, `roadmap-public.md`, `launch-plan.md`.
* **`docs/engineering/`:** `coding-standards.md`, `ci-cd.md`, `branching-strategy.md`, `sprint-process.md`, `release-process.md`, `semantic-versioning.md`, `contribution-guide.md`.
* **`docs/operations/`:** `incident-response.md`, `production-checklist.md`, `release-checklist.md`, `runbooks.md`.

---

## 2. Missing Documentation

* **Root `README.md`:** Currently is an empty placeholder containing only a single heading `# Here are your Instructions` (29 bytes). It lacks local development setup, prerequisites, folder guides, or build commands.
* **`CONTRIBUTING.md` / `LICENSE` / `CODE_OF_CONDUCT.md`:** Missing from the root directory.
* **Operational Details:** No documentation detailing how to configure, boot, and link n8n workflows locally.

---

## 3. Outdated Documentation

* **Tech Stack Drift:** The most critical documentation issue.
  * `.claude/SYSTEM_ARCHITECTURE.md` and `.claude/CODING_STANDARDS.md` describe a **Node.js + Express + TypeScript** backend API and a **Vite + React + TypeScript** client.
  * The actual implementation is a **Python + FastAPI** backend and a **Create React App (CRA) + plain JavaScript** client.
  * Suppressing this drift is planned for Sprint PH3.10 to rewrite the architecture guides to match Python/FastAPI and CRA/JS as-built code.
* **File Reference Mismatches:** 
  * `.claude/INDEX.md` refers to `PRODUCT_REQUIREMENTS.md` (should be `PRODUCT_REQUIREMENT.md`), `TASKS.md` (should be `TASK.md`), and `PROMPTS.md` (should be `PROMPT.md`).

---

## 4. Duplicate Documentation

* **Real-time Updates:** `.claude/SYSTEM_ARCHITECTURE.md` and `.claude/REALTIME_SYSTEM.md` both contain independent sections outlining WebSocket routing, causing duplicate specifications that can drift.
* **Security & Auth:** `.claude/SECURITY.md` and `.claude/PRODUCTION_HARDENING.md` both outline cookie security and CORS requirements.

---

## 5. Broken References

* **Placeholder Drift:** Files under `docs/` (such as `docs/engineering/coding-standards.md`) are empty 4-line placeholders that do not link to or reference their correct corresponding files inside `.claude/`. A developer opening `docs/engineering/coding-standards.md` would assume no standards exist.

---

## 6. Documentation Quality

* **`.claude/` Guides:** **Excellent.** The documents are highly descriptive, carry clear reasoning, specify edge cases, and outline target designs. They serve as a reliable blueprint.
* **`docs/` Guides:** **Very Poor.** Every single file under `/docs/business`, `/docs/engineering`, and `/docs/operations` is a generic 3–4 line placeholder title block.

---

## 7. Recommendations

1. **Write a Root README.md:** Replace the placeholder with a complete guide listing dependencies (Python 3.11, Node 19, MongoDB, Redis), local startup steps, test commands, and architectural overviews.
2. **Reconcile Stack References (PH3.10):** Update all files in `.claude/` to describe FastAPI and React JS, removing Node/Express and TypeScript assumptions unless a migration is planned.
3. **Bridge Placeholder Files:** Replace the empty contents of the files in `/docs` with short intros that redirect to their authoritative `.claude/` counterparts (e.g. `docs/engineering/coding-standards.md` should redirect to `.claude/CODING_STANDARDS.md`).
4. **Fix Filename References:** Resolve file naming drift inside `INDEX.md` to reference `PRODUCT_REQUIREMENT.md`, `TASK.md`, and `PROMPT.md` accurately.
