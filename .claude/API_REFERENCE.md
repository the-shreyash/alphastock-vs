# StockAssist AI
## API Reference

Version: 1.0

Status: Active Development

---

# Purpose

This document defines every API exposed by StockAssist AI.

Every API endpoint must be documented before implementation.

This document acts as the contract between frontend and backend.

Goals

• Consistency

• Scalability

• Security

• Versioning

• Maintainability

• Developer Experience

---

# API Standards

Architecture

REST API

Future

GraphQL

Internal Services

REST + Event Bus

Authentication

JWT

HTTPS Required

JSON Only

UTF-8

Timezone

UTC

API Prefix

/api/v1

Example

/api/v1/auth/login

Never expose unversioned endpoints.

---

# Request Standards

Headers

Authorization

Bearer Token

Content-Type

application/json

Accept

application/json

Request ID

x-request-id

Optional

Correlation ID

x-correlation-id

---

# Response Format

Every API returns the same structure.

Success

{
  "success": true,
  "message": "Request completed successfully.",
  "data": {},
  "meta": {},
  "timestamp": ""
}

Error

{
  "success": false,
  "message": "Validation failed.",
  "error": {
      "code": "VALIDATION_ERROR",
      "details": []
  },
  "timestamp": ""
}

---

# Authentication APIs

POST

/api/v1/auth/register

Purpose

Create account.

Request

Name

Email

Password

Response

User

Token

Refresh Token

---

POST

/api/v1/auth/login

Purpose

Authenticate user.

Returns

Access Token

Refresh Token

User Profile

Permissions

---

POST

/api/v1/auth/logout

Invalidate session.

---

POST

/api/v1/auth/refresh

Generate new access token.

---

POST

/api/v1/auth/forgot-password

---

POST

/api/v1/auth/reset-password

---

GET

/api/v1/auth/me

Current logged-in user.

---

# User APIs

GET

/users/profile

GET

/users/preferences

PATCH

/users/preferences

PATCH

/users/profile

DELETE

/users/account

---

# Dashboard APIs

GET

/dashboard

Returns

Market Summary

Morning Report

Portfolio Summary

Watchlist

News

AI Activity

Notifications

---

# Market APIs

GET

/market/overview

GET

/market/indices

GET

/market/sectors

GET

/market/gainers

GET

/market/losers

GET

/market/heatmap

GET

/market/calendar

GET

/market/news

GET

/market/ipo

GET

/market/status

All endpoints return live market data.

---

# Stock APIs

GET

/stocks

Search stocks.

GET

/stocks/{symbol}

Stock Overview.

GET

/stocks/{symbol}/chart

GET

/stocks/{symbol}/analysis

GET

/stocks/{symbol}/financials

GET

/stocks/{symbol}/news

GET

/stocks/{symbol}/technical

GET

/stocks/{symbol}/fundamental

GET

/stocks/{symbol}/history

GET

/stocks/{symbol}/recommendation

---

# Scanner APIs

GET

/scanner

Parameters

Timeframe

Sector

Market Cap

Volume

Indicators

Strategy

Returns

Ranked opportunities.

---

# AI APIs

POST

/ai/chat

Purpose

Chat with SAI.

---

POST

/ai/analyze-stock

Returns

Technical Analysis

Fundamental Analysis

Risk

AI Recommendation

Confidence

---

POST

/ai/analyze-portfolio

---

POST

/ai/morning-report

---

POST

/ai/trade-review

---

POST

/ai/learning

---

GET

/ai/activity

Returns

Live AI activity timeline.

---

GET

/ai/models

Returns

Claude

Gemini

Status

Latency

Usage

---

# Portfolio APIs

GET

/portfolio

Unified, live-enriched holdings (broker-primary merge of db.holdings + manual
non-paper open trades). Each row carries `source` (broker|manual), sector,
current price/value, P&L and `day_change_pct`. (Sprint 8)

GET

/portfolio/summary

Totals: invested, current value, unrealized + realized P&L, holdings count,
`sources`. (Sprint 8: broker-inclusive)

GET

/portfolio/intelligence

Full Portfolio Intelligence bundle — `{holdings, summary, sources, allocation
(by_holding + by_sector), diversification (HHI/label/effective_holdings), pnl,
risk (score 0-100 + explainable factors), movers, suggestions, dividends,
health}`. Single payload the Portfolio page consumes. (Sprint 8)

GET

/portfolio/performance?range=1M|3M|6M|1Y|ALL

Equity curve + returns (abs/pct) + best/worst day from stored daily snapshots;
`available:false` until ≥2 end-of-day snapshots exist (built forward, never
back-filled). (Sprint 8)

GET

/portfolio/export

Current holdings as `text/csv` (feeds the Portfolio Download action). (Sprint 8)

---

# Watchlist APIs

GET

/watchlists

POST

/watchlists

PATCH

/watchlists/{id}

DELETE

/watchlists/{id}

POST

/watchlists/{id}/stocks

DELETE

/watchlists/{id}/stocks/{symbol}

---

# Trading APIs (Sprint 9 — Trading Engine)

POST

/trades

Risk-gated entry. Body accepts target1..3, trailing_stop
{enabled, type: percent|points, value}, and optional live broker
execution (broker, order_type MARKET|LIMIT, product, auto_exit).
Returns 422 with {violations, warnings, metrics} when the Risk
Manager blocks the trade; 502 when the broker rejects the order.

POST

/trades/validate

Dry-run Risk Manager check (same body as POST /trades) — powers the
live risk panel in the New Trade form.

POST

/trades/quick

One-click trade (AI picks) via the user's CHOSEN trading platform
(users.preferred_broker, set in Settings → Trading Platform; saved
via PUT /settings {preferred_broker}). There is NO default broker:
400 when no platform is selected or the selected one is not
connected. Runs the same Risk Manager gate as POST /trades.

GET

/trades

GET

/trades/active

GET

/trades/history

GET

/trades/pnl

GET

/trades/risk/summary

Today's risk usage vs the user's limits: trades_today,
loss_budget_remaining, open_risk, open_exposure, trading_halted.

PUT

/trades/{id}

Modify an OPEN trade (stop_loss, target1..3, trailing_stop, notes —
side-aware validation, event-logged) or close it with exit_price.

POST

/trades/{id}/exit

Exit fully or partially ({exit_price?, quantity?, at_market?}).
at_market places a LIVE market order via the linked broker first.

---

# Order History API

GET

/orders

Unified order history across every broker (db.orders, fed by
placements + realtime streams). ?refresh=true re-syncs the live
order book from all connected brokers; ?broker= filters.

---

# Broker APIs

GET

/brokers

GET

/brokers/status

POST

/brokers/connect

POST

/brokers/disconnect

POST

/brokers/sync

GET

/brokers/orders

GET

/brokers/positions

POST

/brokers/orders

PATCH

/brokers/orders/{id}

DELETE

/brokers/orders/{id}

---

# Paper Trading APIs

GET

/paper

POST

/paper/trade

GET

/paper/history

DELETE

/paper/reset

---

# Backtesting APIs

POST

/backtesting/run

GET

/backtesting/history

GET

/backtesting/{id}

DELETE

/backtesting/{id}

---

# Journal APIs

GET

/journal

POST

/journal

PATCH

/journal/{id}

DELETE

/journal/{id}

---

# News APIs

GET

/news

GET

/news/trending

GET

/news/{id}

---

# Notification APIs

GET

/notifications

PATCH

/notifications/read

DELETE

/notifications/{id}

---

# Subscription APIs

GET

/subscription

POST

/subscription/upgrade

POST

/subscription/cancel

POST

/subscription/credits

GET

/subscription/history

---

# Payment APIs

POST

/payments/create

POST

/payments/webhook

GET

/payments/history

GET

/payments/invoices

---

# Search APIs

GET

/search

Supports

Stocks

News

Strategies

Portfolio

Watchlists

AI Conversations

Autocomplete

---

# Settings APIs

GET

/settings

PATCH

/settings

PATCH

/settings/theme

PATCH

/settings/notifications

PATCH

/settings/security

---

# Admin APIs

GET

/admin/dashboard

GET

/admin/users

GET

/admin/analytics

GET

/admin/api-health

GET

/admin/ai-health

GET

/admin/payments

GET

/admin/logs

GET

/admin/events

POST

/admin/users/block

POST

/admin/users/unblock

POST

/admin/users/grant-plan

POST

/admin/announcement

PATCH

/admin/feature-flag

DELETE

/admin/users/{id}

---

# WebSocket API

Connection

/ws

Channels

Market

Portfolio

Trades

Notifications

AI Activity

News

Orders

Events

---

Events

market.updated

portfolio.updated

trade.updated

trade.closed

notification.created

news.published

ai.analysis.completed

morningreport.ready

---

# HTTP Status Codes

200

Success

201

Created

204

No Content

400

Bad Request

401

Unauthorized

403

Forbidden

404

Not Found

409

Conflict

422

Validation Error

429

Rate Limit

500

Internal Server Error

503

Service Unavailable

---

# Error Codes

AUTH_REQUIRED

INVALID_TOKEN

VALIDATION_ERROR

BROKER_ERROR

MARKET_CLOSED

ORDER_FAILED

AI_UNAVAILABLE

PAYMENT_FAILED

LIMIT_REACHED

SERVER_ERROR

---

# Pagination

Standard Parameters

?page=1

?limit=20

?sort=createdAt

?order=desc

---

# Filtering

Example

?sector=Banking

?exchange=NSE

?risk=Low

?plan=Elite

---

# Rate Limits

Guest

30 requests/minute

Free

120 requests/minute

Pro

300 requests/minute

Elite

600 requests/minute

Admin

Custom

---

# Versioning

Current

v1

Future

v2

Never introduce breaking changes without a new version.

---

# Security

HTTPS Only

JWT Authentication

Role-Based Authorization

Rate Limiting

Request Validation

Input Sanitization

Audit Logging

CSRF Protection (where applicable)

Secure Cookies

Encrypted Secrets

---

# API Documentation

Every endpoint must include

Description

Parameters

Request Example

Response Example

Authentication

Permissions

Errors

Examples

Deprecation Notes

Future Compatibility

---

# API Development Checklist

Before release verify:

✓ Authentication

✓ Authorization

✓ Validation

✓ Error Handling

✓ Logging

✓ Rate Limiting

✓ Documentation

✓ Tests

✓ Performance

✓ Security Review

---

# End of API Reference
