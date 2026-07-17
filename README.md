# StockAssist AI

[![Branding Note](https://img.shields.io/badge/Branding-StockAssist%20AI%20%2F%20AlphaPartner-blue)](#branding-and-naming-note)
[![Stack](https://img.shields.io/badge/Stack-FastAPI%20%2B%20React%20%2B%20MongoDB-green)](#tech-stack)
[![Status](https://img.shields.io/badge/Status-Feature%20Freeze%20%2F%20Hardening-red)](#current-project-status)

StockAssist AI is a state-of-the-art, AI-powered Trading Operating System built to redefine how retail investors and traders interact with the stock markets. The platform merges Artificial Intelligence, Real-Time Market Data, Financial Education, Portfolio Intelligence, Trading Tools, and Automation into a unified, Bloomberg-style terminal interface.

---

## Vision
To build the world's most intelligent AI-powered investing and trading operating system. StockAssist AI does not simply show charts; it explains them. It does not simply display news; it analyzes sentiment and impact. It serves as an active, round-the-clock intelligent partner for retail investors.

---

## Features
- **AI Market Dashboard:** Live terminal-style dashboard with streaming stock tickers and indices.
- **Top 3 AI Stock Picks:** Proactive recommendations generated daily with comprehensive scoring.
- **Dual-AI Debate Engine:** Interactive debate between Claude (Anthropic) and Gemini (Google) detailing the bull and bear cases for stock picks.
- **Active Portfolio Monitor:** Real-time health scoring, smart exit suggestions, and risk alerts.
- **Technical & Fundamental Scanner:** Multi-criteria stock filters with momentum, volume breakout, and indicator scans.
- **SIP Advisor:** Automated mutual fund and recurring investment planner with personalized AI suggestions.
- **Trade Journal & Coaching:** Automated trading diary tracking performance metrics with AI weekly reviews.
- **Smart Notifications:** Real-time email and WhatsApp alerts (via Twilio/SendGrid integration).

---

## Tech Stack

### Backend
- **Core:** Python 3.11 / FastAPI
- **Database:** MongoDB (via Motor asynchronous driver)
- **Caching & Real-Time Events:** Redis (Pub/Sub) + Socket.IO for event-driven updates
- **Task Scheduler:** APScheduler for automated morning scans, portfolio checks, and database jobs
- **AI Integration:** Direct connection to Google Gemini and Anthropic Claude APIs

### Frontend
- **Core:** React (Create React App + Craco / plain JavaScript)
- **Styling:** Tailwind CSS + Vanilla CSS (Bloomberg-style dark theme with Outfit & Manrope typography)
- **UI Components:** Radix UI / shadcn/ui
- **Charts:** TradingView Lightweight Charts (v5) + Recharts
- **Animations:** GSAP

---

## Architecture Overview
The platform uses a provider-independent real-time market data layer:
- **Market Gateway:** Normalizes raw events from various providers (broker WebSockets, Yahoo Finance, licensed exchange feeds).
- **Source Manager:** Implements automatic, silent failover protocols (Broker Feed → Licensed Feed → Yahoo Finance → Normalized Cache).
- **Broker Adapter Framework:** All connected brokers share a single interface, isolating integration code (currently supports Kite Connect).

---

## Repository Structure
```
├── .claude/                # Developer guides, system prompts, decisions, and tasks
├── backend/                # FastAPI application code and tests
│   ├── scripts/            # Database seed and maintenance scripts
│   ├── services/           # Business logic, engines, and integrations
│   └── tests/              # Test suites (340+ unit/integration tests)
├── docs/                   # Categorized documentation (Business, Engineering, Operations)
├── frontend/               # React application code
│   ├── public/             # Static assets
│   └── src/                # Components, pages, hooks, store, and utilities
├── memory/                 # Primary PRD and foundational requirement specs
├── n8n/                    # Workflow templates for n8n automated integration
└── README.md               # Repository landing and guide (this file)
```

---

## Installation & Setup

### Prerequisites
- **Python:** 3.11 or higher
- **Node.js:** 18.x or higher
- **Yarn:** 1.22.x or higher (recommended for frontend)
- **MongoDB:** Local instance or MongoDB Atlas URI
- **Redis:** Running local instance (required for WebSockets and pub/sub cache)

### Backend Installation
1. Navigate to the backend directory:
   ```bash
   cd backend
   ```
2. Create and activate a Python virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows use: venv\Scripts\activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Copy the environment configuration:
   ```bash
   cp .env.example .env
   ```
   *Edit the `.env` file with your Mongo URIs, Redis URL, and Claude/Gemini API keys.*
5. Run the FastAPI development server:
   ```bash
   uvicorn server:app --reload --port 8000
   ```

### Frontend Installation
1. Navigate to the frontend directory:
   ```bash
   cd frontend
   ```
2. Install package dependencies:
   ```bash
   yarn install  # Or: npm install
   ```
3. Configure environment:
   ```bash
   cp .env.example .env
   ```
4. Run the React development server:
   ```bash
   yarn start  # Or: npm start
   ```
   *The client will start on [http://localhost:3000](http://localhost:3000).*

---

## Development & Verification

### Running Backend Tests
To run unit and integration tests locally, execute:
```bash
cd backend
pytest
```
*Make sure your database environment variables are configured correctly for testing (the test suite uses a mocked database context where appropriate).*

### Branch & Code Strategy
Please review the contribution guidelines before opening branches or pull requests.
- All development should follow Conventional Commits.
- Branches should align with the current sprint track.

---

## Branding and Naming Note
- **StockAssist AI** is the primary public branding and naming convention.
- **AlphaPartner** is the internal code name and project handle used historically in certain database structures, legacy emails, and configuration keys. Both refer to this same unified repository.

---

## Documentation Index
Detailed developer instructions and architecture designs are stored in the `.claude/` system folder:
- **Developer Welcome:** [CLAUDE.md](file:///.claude/CLAUDE.md) — System expectations and context.
- **Architecture Log:** [DECISIONS.md](file:///.claude/DECISIONS.md) — Architectural Decision Records (ADRs v1.0–v1.2).
- **Hardening Roadmap:** [PRODUCTION_ROADMAP.md](file:///.claude/PRODUCTION_ROADMAP.md) — Sprints PH1–PH3 roadmap.
- **Security Blueprint:** [SECURITY.md](file:///.claude/SECURITY.md) — Vulnerability SLA and protocol rules.
- **API Spec:** [API_REFERENCE.md](file:///.claude/API_REFERENCE.md) — Endpoint specifications.
- **Documentation Index:** [INDEX.md](file:///.claude/INDEX.md) — Entry point for all technical files.

---

## Core Repository Guides
- **How to contribute:** [CONTRIBUTING.md](file:///Users/shreyash12/Files/alpha_stock/alpha-stock-main/CONTRIBUTING.md)
- **Version release log:** [CHANGELOG.md](file:///Users/shreyash12/Files/alpha_stock/alpha-stock-main/CHANGELOG.md)
- **Product roadmap:** [ROADMAP.md](file:///Users/shreyash12/Files/alpha_stock/alpha-stock-main/ROADMAP.md)
- **Security & Vulnerability disclosure:** [SECURITY.md](file:///Users/shreyash12/Files/alpha_stock/alpha-stock-main/SECURITY.md)
- **Technical & product support:** [SUPPORT.md](file:///Users/shreyash12/Files/alpha_stock/alpha-stock-main/SUPPORT.md)
- **License Agreement:** [LICENSE](file:///Users/shreyash12/Files/alpha_stock/alpha-stock-main/LICENSE)
# Here are your Instructions
