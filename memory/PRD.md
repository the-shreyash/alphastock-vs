# AlphaPartner - AI Trading Platform PRD

## Original Problem Statement
Build AlphaPartner — an AI-powered trading operating system for Indian stock markets (NSE/BSE). Features include AI Market Dashboard, Top 3 AI Stock Picks with dual AI debate, Trade Monitoring, Portfolio Management, AI Chat Assistant, SIP Advisor, Smart Notifications, and Risk Management.

## Architecture
- **Frontend**: React + Tailwind CSS + Shadcn UI + Recharts
- **Backend**: FastAPI (Python) + MongoDB (motor async)
- **AI**: Dual AI system - Claude Opus 4.5 + Gemini 2.5 Pro (Direct APIs)
- **Auth**: JWT + direct Google OAuth 2.0 (httpOnly cookies + Bearer token fallback)
- **Market Data**: Simulated/mock (ready for real API integration)

## User Personas
- **Primary**: Indian retail trader (intraday/swing) wanting AI-assisted decisions
- **Secondary**: Beginning investor exploring SIP/mutual fund options

## Core Requirements
1. Dark terminal UI (Bloomberg-style) - IMPLEMENTED
2. Dual AI debate system (Claude vs Gemini) - IMPLEMENTED
3. Real-time market dashboard with indices - IMPLEMENTED (simulated)
4. AI stock picks with confidence scores - IMPLEMENTED
5. Trade monitor with P&L tracking - IMPLEMENTED
6. Portfolio management - IMPLEMENTED
7. AI Chat Assistant - IMPLEMENTED
8. SIP Advisor with AI recommendations - IMPLEMENTED
9. Notification system - IMPLEMENTED
10. Settings & Risk management config - IMPLEMENTED

## What's Been Implemented (May 26, 2026)

### Phase 7 - REAL Market Data + WhatsApp LIVE + In-App News (Latest)
- REAL market data from Yahoo Finance: Nifty 23,123 (-1.04%), BankNifty 54,063.75 (-0.79%), Sensex 73,524.26 (-0.97%)
- REAL stock prices: RELIANCE INR 1,263.30 (-2.15%) from Yahoo Finance
- Market status correctly shows OPEN/CLOSED based on actual market hours
- WhatsApp LIVE via Twilio: Test message sent successfully, alerts configured to +918400881203
- News articles open in-app modal (no external redirect) with "Read Full Article" option
- HTML entities stripped from news summaries, Yahoo prices rounded properly
- 99/99 cumulative backend tests pass

### Phase 6 - Deep Zerodha Integration
- Zerodha Account Dashboard: Balance (Available Margin, Used Margin, Opening Balance), Profile, Holdings count
- One-Click Trade from AI Picks: "Trade Now" button opens confirmation modal with pre-filled entry/SL/target, editable quantity, places order on Zerodha
- Stock Autocomplete: New Trade form suggests stocks when typing (from NSE universe)
- Portfolio Zerodha Tab: "Platform Trades" and "Zerodha Holdings" tabs showing holdings from both sources
- Full account endpoints: /api/zerodha/account, /funds, /profile, /orders, /quick-trade
- 82/82 backend tests pass

### Phase 5 - News, Journal, Full AI Transparency, Auto-Login
- Auto-login (skip auth for dev mode, gated behind ENABLE_AUTO_LOGIN env flag)
- Full AI Report with scoring breakdown (RSI, Volume, Price Action, VWAP, News Sentiment) + dual debate + related news
- Removed Beginner/Advanced toggle — clean unified UI
- Market News page with real RSS feeds from MoneyControl, ET Markets, LiveMint, ET Stocks (37+ live articles)
- Trade Journal with performance stats (P&L, win rate, best/worst) + AI weekly review generator
- Zerodha API keys configured (API Key: 53we9s4kpuz62wpw) - status: READY
- 68/68 backend tests pass

### Phase 4 - AI Monitoring + Charts + WhatsApp
- Always-on AI Portfolio Monitoring with health score (0-100), proactive alerts (SL proximity, target hit, RSI overbought, volume spikes, significant losses)
- TradingView professional candlestick charts (lightweight-charts v5) with volume bars, 1D/1W/1M period switching
- Stock Detail page with full technical indicators and market data
- WhatsApp Twilio integration (simulated, ready for Twilio keys) with dashboard setup panel
- Zerodha callback/postback URLs configured and ready
- AI monitoring runs every 60 seconds during market hours via cron
- 54/54 backend tests pass

### Phase 3 - UI Redesign
- Complete UI overhaul: Premium Apple-like clean design (Outfit + Manrope + JetBrains Mono fonts)
- Light/Dark theme toggle with CSS custom properties (persisted in localStorage)
- Beginner/Advanced mode toggle (beginners get explanations, advanced get raw data)
- Marketing Landing Page with features, how-it-works, disclaimer, CTA
- Live stock ticker bar on dashboard
- Redesigned all pages with premium card styling, generous spacing, rounded corners

### Phase 2 - Integrations
- Alpha Vantage integration (KEY: IKOR5GFCSOI96GNK - LIVE)
- Zerodha Kite Connect integration (simulated, ready for keys)
- WebSocket real-time streaming (10s market updates, price ticks)
- APScheduler cron jobs (5 jobs: morning 8:30, scanner every 5min, trade monitor every 1min, exit 3:10PM, EOD 4PM)
- Direct Google OAuth 2.0 flow

### Phase 1 - MVP (40/40 backend tests pass)
- JWT auth + Google OAuth
- Market data APIs (all endpoints)
- AI Analysis with dual AI debate (Claude Opus 4.5 + Gemini 2.5 Pro)
- Trade CRUD with live P&L tracking
- Portfolio management
- AI Chat Assistant
- SIP Calculator + AI recommendations
- Notification system
- Settings with risk management

## Prioritized Backlog

### P0 (Critical - Next Phase)
- Connect real Alpha Vantage key for live NSE/BSE data
- Connect real Zerodha Kite API keys for live order execution
- TradingView chart widget integration for professional charts

### P1 (Important)
- WhatsApp/Email notification delivery (Twilio/SendGrid)
- Mobile responsive optimization
- Trade journal with AI review
- Backtesting engine

### P2 (Nice to Have)
- Community features (share picks)
- Multi-exchange support (BSE)
- Advanced chart patterns detection
- Portfolio rebalancing suggestions

## Next Tasks
1. Add real Alpha Vantage API key for live market data
2. Add Zerodha Kite API keys for live trading
3. Integrate TradingView widgets for professional charts
4. Add WhatsApp/Email notification channels
5. Mobile responsive optimization
