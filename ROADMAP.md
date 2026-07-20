# StockAssist AI — Product Roadmap

This document outlines the development status, milestones, and future roadmap for StockAssist AI. It aligns our engineering efforts with our long-term vision of building the world's most intelligent AI-powered investing and trading operating system.

---

## Long-Term Vision
StockAssist AI aims to become the primary financial operating system for retail investors worldwide. The platform does not simply execute orders; it provides deep technical and fundamental understanding, interactive dual-AI analysis (bull/bear debates), autonomous portfolio auditing, and real-time risk mitigation.

---

## Current Status: Feature Freeze & Production Hardening
StockAssist AI is currently in **Feature Freeze**. Following the completion of the MVP feature set (Phase 1 and Phase 2), all development is focused strictly on the **Production Hardening program (PH1–PH3)**. 

No new product features will merge or release until the repository achieves **Production Certification** (readiness score ≥ 9.0, zero security findings).

---

## Milestones Summary

```
Phase 1: Foundation (COMPLETE)
       │
       ▼
Phase 2: Core Trading (COMPLETE)
       │
       ▼
[CURRENT] Production Hardening (PH1–PH3) ─── We are here
       │
       ▼
Phase 3: AI Intelligence (PLANNED)
       │
       ▼
Phase 4: Pro Trading Tools (PLANNED)
       │
       ▼
Phase 5: SaaS Business Platform (PLANNED)
```

---

## Completed Milestones

### Phase 1 — Foundation
*Completed: 2026-07-17*
- Established the base server infrastructure (FastAPI + MongoDB).
- Designed the core broker adapter interface and user schema.
- Integrated foundational dual-AI logic (Claude + Gemini).
- Created developer guides, setup scripts, and automated unit test environments.

### Phase 2 — Core Trading Platform
*Completed: 2026-07-17*
- Implemented the terminal-style dark dashboard, stock detail views, and TradingView charting.
- Launched the **Dual-AI Debate Engine** providing detailed trade analyses.
- Designed portfolio tracking, paper trading, and automated scanning mechanisms.
- Configured Zerodha Kite Connect live trading integration.
- Integrated automated RSS news feeds and Twilio WhatsApp notifications.

---

## Active Milestone: Production Hardening (PH1 – PH3)
*Status: IN PROGRESS*

### Track PH1 — Production Security Hardening
- **Objective:** Secure authentication, rotate JWTs, implement rate limiters, restrict CORS origins, and lock down WebSocket channels.
- **Next Sprints:** Implementing production Google OAuth flow, secure httpOnly session cookies, and API brute-force protection.

### Track PH2 — Production Infrastructure & DevOps
- **Objective:** Build robust packaging, split local/production Docker configurations, establish CI/CD validation checks, configure production Redis/MongoDB, and set up disaster backups.
- **Next Sprints:** Backend and Frontend Dockerfile optimization, Docker Compose setups, and GitHub Actions pipeline definition.

### Track PH3 — Production Quality Assurance
- **Objective:** Eliminate mock data from dashboard metrics, fix flaky backend tests, introduce React frontend testing, perform security benchmarks, and execute accessibility audits.
- **Next Sprints:** Restructuring server endpoints, replacing analytics placeholders with aggregation queries, and writing test suites.

---

## Future Roadmap (Post-Hardening Launch)

### Phase 3 — AI Intelligence
- **Portfolio Audits:** Always-on AI reviewer checking portfolio diversification and macro alignment.
- **Refinement Engine:** AI memory systems summarizing personal trading history to optimize recommendations.
- **Proactive Alerts:** Dynamic risk alerts notifying users of sector rotations or high-volatility news.

### Phase 4 — Professional Trading Tools
- **Advanced Technical Scanner:** Multi-indicator scanner tracking custom trigger combinations (e.g., MACD + Bollinger Bands).
- **Backtesting Suite:** Performance backtests displaying drawdown curves, profit factors, and historical win rates.
- **Economic Calendar:** Real-time financial calendar reporting market-affecting announcements.

### Phase 5 — SaaS Business Platform
- **Stripe & Razorpay Integration:** Secure, hosted checkout supporting tiered subscription billing.
- **Usage Quotas:** AI-credit limits and prioritization queues based on tiering (Free, Pro, Elite).
- **Admin Dashboard Billing:** Administrative portals monitoring usage metrics, server costs, and billing events.

### Phase 6 — Global Markets & Autonomous OS
- **International Adapters:** Extend Market Gateway to cover US markets, cryptocurrency, and forex assets.
- **Autonomous Research Agents:** Multi-agent collaboration performing detailed research reports on target stock lists.
