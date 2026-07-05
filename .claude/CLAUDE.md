# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**AlphaPartner** is a full-stack AI-powered stock trading platform for Indian NSE markets. It combines a FastAPI backend and React frontend with dual-AI analysis (Claude + Gemini), real broker integration (Zerodha Kite), paper trading, backtesting, and multi-channel notifications.

## Commands

### Frontend (from `frontend/`)
```bash
yarn start          # Dev server on http://localhost:3000
yarn build          # Production build
yarn test           # Run Jest tests
yarn test -- --testPathPattern=<file>  # Run a single test file
```

### Backend (from `backend/`)
```bash
uvicorn server:app --host 0.0.0.0 --port 8000 --reload   # Dev server
pytest                          # Run all tests
pytest tests/test_backend.py    # Run a single test file
python -m flake8 .              # Lint
mypy .                          # Type check
```

### Docker (from project root)
```bash
docker-compose up               # Start all services (MongoDB, backend, frontend)
docker-compose up -d            # Start in background
docker-compose logs -f backend  # Follow backend logs
```

## Architecture

### Backend (`backend/`)
- **`server.py`** — Monolithic FastAPI app (~87KB). All REST endpoints and WebSocket handlers live here. Imports from the `services/` layer.
- **`models.py`** — Pydantic models for all request/response and DB document shapes.
- **`market_data.py`** — Aggregates NSE market data (25+ stock universe), FII/DII flows, sector performance, and generates AI stock picks.
- **`services/`** — 22 independent service modules:
  - `ai_debate_engine.py` — Orchestrates Claude vs. Gemini dual-analysis; final recommendation from the "debate"
  - `claude_provider.py` / `gemini_provider.py` — AI provider wrappers with a unified interface
  - `real_market.py` — Live order execution via Zerodha Kite
  - `paper_trade.py` / `backtest_engine.py` — Simulated trading and historical backtesting
  - `portfolio_monitor.py` — Real-time P&L tracking
  - `scheduler.py` — APScheduler jobs for recurring tasks (morning reports, alerts)
  - `email_service.py` / `whatsapp_service.py` / `telegram_service.py` — Notification channels

**Database:** MongoDB via Motor (async). Models have `to_mongo()` / `from_mongo()` helpers.
**Auth:** JWT (PyJWT) + bcrypt + Google OAuth2.

### Frontend (`frontend/src/`)
- **`App.js`** — React Router with 16+ routes; wraps protected routes in auth check.
- **`pages/`** — One JSX component per route (Dashboard, StockPicks, StockDetail, Portfolio, AIAssistant, Backtesting, PaperTrading, TradeJournal, etc.).
- **`components/`** — Organized by domain: `ui/` (48+ Shadcn base components), `charts/`, `dashboard/`, `layout/`, `notifications/`.
- **`context/`** — `AuthContext` (user session) and `ThemeContext` (light/dark toggle).
- **`services/`** — Axios-based API client modules, one per backend domain.

**State:** React Context only (no Redux).
**Build:** Craco (CRA override) with Tailwind CSS + Shadcn UI.

## Key Environment Variables

Copy `.env.example` to `.env` in the root and `backend/` directories. Critical variables:

| Variable | Purpose |
|---|---|
| `MONGO_URL` | MongoDB connection string |
| `JWT_SECRET` | JWT signing key |
| `ANTHROPIC_API_KEY` | Claude AI provider |
| `GOOGLE_GEMINI_KEY` | Gemini AI provider |
| `KITE_API_KEY` / `KITE_API_SECRET` | Zerodha broker |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | WhatsApp alerts |
| `SENDGRID_API_KEY` | Email alerts |
| `ENABLE_AUTO_LOGIN` | Dev shortcut; set `ADMIN_EMAIL` / `ADMIN_PASSWORD` alongside |

Frontend reads `REACT_APP_BACKEND_URL` and `REACT_APP_GOOGLE_CLIENT_ID`.

## Design System

Detailed specs are in `design_guidelines.json`. Key rules:
- **Fonts:** Outfit (headings), Manrope (body), JetBrains Mono (tickers/numbers)
- **Colors:** Monochrome palette — gain `#10B981`, loss `#F43F5E`, AI accent `#6366F1` (light) / `#818CF8` (dark). Background is `#09090B` (dark), not pure black.
- **Components:** Shadcn UI base, overridden for Apple-like aesthetics — `rounded-xl/2xl`, `shadow-sm`, no heavy drop-shadows. Primary buttons: black/white text in light mode, inverted in dark.
- **Icons:** `lucide-react` exclusively.
- **Testing hooks:** Add `data-testid` to all interactive elements.
- **Beginner/Advanced toggle:** Conditionally renders plain-English explanations vs. dense data tables; managed via context/state.
- **Dual-AI debate UI:** Side-by-side bubbles with distinct subtle colors for Claude (coral hint) vs. Gemini (blue/purple hint).

## Testing Protocol

`test_result.md` tracks a multi-phase (7-phase) test plan in YAML format. When adding features:
1. Update `test_result.md` with new tasks under the appropriate phase.
2. Mark tasks as `working` / `implemented` / `stuck` with a `stuck_count`.
3. Backend tests live in `backend/tests/test_phase*.py`.
