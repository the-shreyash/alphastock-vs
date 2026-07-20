# Executive Baseline & Readiness Report
**Project Name:** StockAssist AI (AlphaPartner)  
**Date:** 2026-07-17  
**Sprint:** SI1.1 — Repository Audit  
**Status:** Feature Freeze / Production Hardening Baseline  
**Author:** Lead Architect / CTO  

---

## 1. Executive Summary

This report establishes the engineering baseline for **StockAssist AI** (currently branded in code as **AlphaPartner**), an AI-powered Stock Market Analysis & Trading Platform. Having completed its Minimum Viable Product (MVP) phase (Phase 1, Sprints 1–12 and Phase 2, Releases R1–R9), the project has transitioned to a strict **Feature Freeze** and entered a **Production Hardening Program** (PH1–PH3). 

A baseline audit reveals that while the core application is feature-rich, highly functional, and contains a comprehensive backend test suite, it is **not ready for production**. The repository carries multiple critical security findings (such as default admin authentication configurations and wildcard CORS policies), broken deployment packaging, and a substantial divergence between the documented system design and the actual implementation.

This report summarizes these findings, establishes the repository maturity score, outlines key strengths and weaknesses, and defines immediate priorities to prepare the application for a secure, resilient production launch.

---

## 2. Current Project Status

The project is currently in a **Feature Freeze**. No new user-facing features or product capabilities are to be implemented or merged until the system meets the **Definition of Production Ready** defined in [.claude/PRODUCTION_HARDENING.md](file:///.claude/PRODUCTION_HARDENING.md#L367-L373).

* **Backend Completeness:** ~95% of planned MVP features are implemented, including dual-agent AI debates, news extraction, and Zerodha Kite Connect live trading.
* **Frontend Completeness:** ~90% of planned pages are implemented, including a live Dashboard, Stock Detail workspaces, and real-time Trade Monitors.
* **Hardening Progress:** Sprint PH1.1 (Authentication Backdoor Removal) is complete. The two critical authentication backdoors (auto-login routes and OAuth bypass logic) and plain-text dev credential seeding have been permanently deleted from [backend/server.py](file:///Users/shreyash12/Files/alpha_stock/alpha-stock-main/backend/server.py). The system is now awaiting the next security hardening sprints.

---

## 3. Tech Stack

The actual system stack deviates significantly from what is documented in the architectural guides. Below is the verified as-built stack:

| Layer | Documented (.claude/ Docs) | Actual Implemented Stack (As-Built) |
| :--- | :--- | :--- |
| **Backend Framework** | Node.js + Express + TypeScript | **Python 3.11 + FastAPI** |
| **Frontend Framework** | React + TypeScript + Vite | **React 19 + JavaScript + Create React App (CRA/Craco)** |
| **Database** | MongoDB Atlas | MongoDB (accessed via `motor` async driver) |
| **Real-time / Events** | Redis + BullMQ | Redis Pub/Sub + Native WebSockets (`FastAPI WebSocket`) |
| **Automation** | Independent Workers | **n8n Workflow Engine** (integrated via Webhooks) |
| **Jobs & Scheduling** | BullMQ workers | **APScheduler** (in-process loops within the FastAPI server) |

This tech stack drift represents a major engineering baseline risk, as new developers consulting the documentation would design and write code for an entirely different stack. Suppressing the stack divergence in favor of the FastAPI + React JS stack was accepted under [ADR-027](file:///.claude/DECISIONS.md#L779-L826).

---

## 4. Repository Maturity Score

Based on a detailed evaluation against production standards, the current **Overall Production Readiness Score** is **4.2 / 10**. 

The target score required for public launch is **≥ 9.0 composite with no individual category scoring below 8.0**.

### Scorecard Breakdown

| Category | Score | Basis & Main Findings |
| :--- | :--- | :--- |
| **Application Functionality** | **8.5 / 10** | Feature-complete MVP; primary trading, AI workspace, and data flows are functional. |
| **Authentication & Authorization** | **5.0 / 10** | Purged of critical backdoors (PH1.1), but still lacks a password complexity policy and email verification. |
| **API & Transport Security** | **3.0 / 10** | CORS defaults to `*` with credentials allowed; cookies lack `Secure` flags; no rate limiting. |
| **Secrets & Configuration** | **6.0 / 10** | Git ignores `.env` files and has no hardcoded API keys, but docker-compose contains weak default fallbacks and there is no boot-time config validation. |
| **Packaging & Deployability** | **1.0 / 10** | `docker-compose.yml` refers to Dockerfiles that do not exist in the repository; local build fails. |
| **CI/CD Pipeline** | **0.0 / 10** | Nonexistent. No pipeline configurations or workflows are present. |
| **Testing Depth** | **5.0 / 10** | Strong backend unit test coverage (341 tests), but contains non-hermetic integration failures and zero frontend tests. |
| **Observability & Logging** | **3.5 / 10** | A health check route exists, but the server lacks structured JSON logging, metrics collection, or error tracking. |
| **Data Integrity** | **5.5 / 10** | Multiple live endpoints fall back to random simulated data generators on Yahoo Finance failures, violating [ADR-021](file:///.claude/DECISIONS.md#L624-L642). |
| **Documentation Accuracy** | **5.0 / 10** | Detailed, high-quality guidelines, but describes the Node.js/TypeScript stack rather than Python/FastAPI/JS. |

---

## 5. Major Strengths

* **Robust Event-Driven Real-time Foundation:** Clean integration of Redis Pub/Sub and WebSockets to push live ticks, portfolio, and trade updates directly to the client dashboard.
* **Comprehensive Backend Test Coverage:** 341 passing tests covering complex business logic like the trade journal, paper trading, and portfolio calculations.
* **Dual-AI Debate Engine:** Pragmatic and functional implementation of multi-LLM consensus (Claude + Gemini) for stock evaluation.
* **Complete Broker Integration:** Zerodha Kite Connect is fully wired with support for holdings, funds, and postback webhooks.
* **Centralized API Client:** The frontend handles networking and token refresh loops cleanly through a centralized Axios client.

---

## 6. Major Weaknesses

* **Critical Security Gaps:** wildcards on CORS, insecure local auth cookies, and an unauthenticated WebSocket endpoint allowing subscription to any user's tick stream.
* **Broken Containerization:** Missing Dockerfiles block standard local developer setup (`docker compose up --build` fails immediately).
* **Stack Mismatch & Documentation Drift:** Documentation describes a completely different programming language, package runtime, and compiler chain.
* **Silent Mock Data Fallbacks:** In-memory quote failures fall back to jittered random walk generators. A user has no way of telling simulated data from live data.
* **Monolithic Backend Code:** `server.py` is a 4,823-line file combining routing, middleware, business logic, templates, and WebSocket management.

---

## 7. Immediate Priorities

1. **OAuth Hardening & Fail-Closed Logic (PH1.2):** Make the Google OAuth flow fail-closed, verifying auth codes server-side with Google.
2. **Session & Cookie Security (PH1.3):** Set `secure=True` on cookies, implement CSRF token verification, and unify cookie handling.
3. **CORS & Security Headers (PH1.4):** Enforce strict CORS whitelists and deploy defensive browser headers (HSTS, CSP).
4. **Dockerization (PH2.1–2.3):** Create multi-stage production Dockerfiles for the backend and frontend.
5. **CI/CD Pipeline Creation (PH2.5):** Build a GitHub Actions runner to automate testing, linting, and build validation.

---

## 8. Next Sprint Recommendation

It is recommended to proceed immediately with **Sprint PH1.2 (Google OAuth Production Flow)** and **Sprint PH1.3 (Cookie & Session Security)** as detailed in the [.claude/PRODUCTION_ROADMAP.md](file:///.claude/PRODUCTION_ROADMAP.md#L58-L82). These sprints target the highest remaining security exposure risks in the authentication layer.
