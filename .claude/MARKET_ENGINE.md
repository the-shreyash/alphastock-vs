# StockAssist AI
## Market Engine Documentation

Version: 1.1

Status: Active Development

---

# Purpose

The Market Engine is the central market intelligence system of StockAssist AI.

Its responsibility is to collect, normalize, validate, process, rank, cache, and distribute real-time market data throughout the platform.

The Market Engine is NOT responsible for making investment decisions.

It provides reliable data to the AI system.

The AI reasons over this data.

---

# Core Responsibilities

The Market Engine continuously:

Collects Market Data

Collects Global Markets

Collects News

Collects Economic Events

Monitors Indices

Monitors Sectors

Monitors Options Chain

Monitors Market Breadth

Monitors Institutional Activity

Detects Opportunities

Publishes Events

Caches Market Data

Provides APIs

Maintains WebSocket Streams

---

# High Level Architecture

```

External Providers

↓

Market Gateway

↓

Data Collectors

↓

Normalizer

↓

Validator

↓

Market Cache

↓

Processing Engine

↓

Ranking Engine

↓

Scanner Engine

↓

Event Bus

↓

AI System

↓

Frontend

```

---

# Supported Markets

Phase 1

NSE

BSE

---

Phase 2

US Markets

NASDAQ

NYSE

---

Phase 3

Crypto

Forex

ETF

Mutual Funds

Commodities

Global Indices

---

# Data Providers

Provider selection, priority, switching, and failover are defined authoritatively in MARKET_DATA_ARCHITECTURE.md. The Market Engine never communicates with providers directly — it consumes normalized market events from the Market Gateway.

Provider priority (resolved per user by the Source Manager):

1. Connected Broker WebSocket (Zerodha, Upstox, Angel One, Fyers, Dhan)

2. Licensed Exchange Feed (future)

3. Yahoo Finance (always-available polling baseline)

Secondary / future

Alpha Vantage

Polygon

Finnhub

TwelveData

Crypto, Forex, US market providers

Each provider is one adapter behind the Market Gateway. Adding a provider never changes the Market Engine.

---

# Market Gateway

Purpose

Provide one unified interface.

Never allow the application to directly call external APIs.

Everything passes through:

Market Gateway

The Market Gateway owns provider connections, authentication, normalization, validation, health monitoring, and reconnection. The Source Manager decides which provider is active per user and orchestrates automatic switching and failover.

Full design: MARKET_DATA_ARCHITECTURE.md.

Implemented (Sprint D1, 2026-08-19)

`services/market_engine/gateway.py` resolves a provider per request by capability, never by name:

    market_gateway.get_quote(symbol)
        → source_manager.resolve(Capability.QUOTES)
        → provider.fetch_quote(symbol)          # raw provider payload
        → normalize_stock_quote(raw, provider.normalizer_key)
        → validate_stock_quote(...)
        → stamp source_tier + ingested_at
        → event_bus.publish("price.updated", ...)

Adding a provider is one adapter, one normalizer family, and one
`provider_registry.register()` call — nothing in the Market Engine changes.

Failover needs no switching code: the gateway records every call outcome
against provider health, a provider that fails consistently stops being
resolved, and the next priority tier takes over. Recovery is symmetric.
`provider.status` events carry the freshness tier only — never a provider name.

Extended (Sprint D2, 2026-08-20)

The Source Manager now returns an ordered failover *chain*, not a single
provider, and the gateway walks it inside one request:

    market_gateway.get_quote(symbol, user_id=...)
        → source_manager.resolve_feed(
              Capability.QUOTES,
              ResolutionContext(user_id=..., symbol=symbol))
        → Resolution(provider, chain, reason)
        → try chain[0]; on exception try chain[1]; …
        → normalize with the provider that actually answered

D1 called the head of the chain alone, so the baseline only took over after a
provider had failed eight consecutive *requests*. Selection now also honours
per-user entitlement (`MarketDataProvider.is_eligible_for`), reports a fourth
health state `unknown` for a provider never yet exercised, and returns an
explicit `UnavailableReason` instead of a bare `None`. See ADR-029.

One limitation D2 leaves open: a demoted provider is never called again, so it
cannot recover on its own until Phase 5 adds probation windows and periodic
re-probing. D3's broker adapter is the natural first caller, because a
reconnected WebSocket knows it recovered without anyone polling it.

Two silent defects were closed in D1: normalized quotes had been stamped with
`provider: "yahoo"` (provider identity leaking downstream), and index
normalization had never actually run (the provider's index sub-dicts carry no
`name`, and a nameless index fails validation, so the raw payload passed
through on every request).

Benefits

Centralized

Cached

Secure

Observable

Replaceable

Provider-Independent

---

# Market Collectors

Collectors run independently.

Collectors

Price Collector

Index Collector

Sector Collector

News Collector

Corporate Action Collector

Options Collector

Economic Calendar Collector

Institutional Activity Collector

Dividend Collector

IPO Collector

---

# Collection Frequency

Frequency depends on the active provider tier (see MARKET_DATA_ARCHITECTURE.md):

Live Prices

Streaming tier: tick-level (broker WebSocket / licensed feed)

Delayed tier: 15–60 seconds (Yahoo polling)

Indices

Streaming tier: tick-level

Delayed tier: 15–60 seconds

News

Real-Time

Corporate Actions

Daily

IPO

Daily

Financial Statements

Quarterly

Global Markets

Every 5 Minutes

Economic Calendar

Hourly

---

# Data Normalizer

Purpose

Different APIs return different formats.

Normalize everything.

Example

Yahoo

closePrice

↓

Internal

close

Broker

ltp

↓

Internal

price

The rest of the platform never depends on provider formats.

---

# Data Validator

Validate

Missing Values

Invalid Prices

Negative Volume

Duplicate Records

Outliers

Timestamp

Market Hours

Reject invalid data.

---

# Market Cache

Use Redis.

Cache

Live Prices

Indices

Heatmaps

Scanner Results

Morning Report

News

Sector Rankings

Most Active Stocks

Cache reduces API calls.

---

# Market Models

Market Data

Index Data

Sector Data

News

Economic Events

Corporate Actions

IPO

Options Chain

Institutional Activity

Each model should have a dedicated service.

---

# Processing Pipeline

Raw Data

↓

Validation

↓

Normalization

↓

Enrichment

↓

Caching

↓

Event Publishing

↓

Database Storage

↓

AI Consumption

---

# Market Scanner

Purpose

Continuously scan every listed stock.

Scanner should support:

Intraday

Swing

Long Term

Momentum

Breakout

Reversal

Volume

Value Investing

Growth Investing

Dividend

---

# Scanner Filters

Volume

Price

Sector

Market Cap

RSI

MACD

EMA

VWAP

Moving Average

52 Week High

52 Week Low

Delivery %

Institutional Buying

News Sentiment

Custom Filters

---

# Scanner Ranking

Every stock receives scores.

Momentum Score

Trend Score

Volume Score

Risk Score

News Score

Sector Score

Liquidity Score

AI Confidence

Overall Opportunity Score

Top opportunities appear first.

---

# Opportunity Engine

Purpose

Identify trading opportunities.

Example Signals

Volume Breakout

Golden Cross

Support Bounce

Resistance Breakout

High Relative Strength

Institutional Buying

Gap Up

Gap Down

Unusual Options Activity

Positive News

Earnings Surprise

Every opportunity includes reasoning.

---

# Market Breadth

Track

Advancing Stocks

Declining Stocks

Unchanged

Advance Decline Ratio

Sector Breadth

Market Participation

Displayed on Dashboard.

---

# Sector Engine

Tracks

Sector Leaders

Sector Weakness

Sector Rotation

Sector Momentum

Sector Breadth

Sector Strength Score

Used by AI.

---

# Index Engine

Monitor

Nifty 50

Sensex

Bank Nifty

Midcap

Smallcap

India VIX

Global Indices

Each index has its own detail page.

---

# Global Market Engine

Monitor

US Markets

European Markets

Asian Markets

Gift Nifty

Dow Futures

NASDAQ Futures

Oil

Gold

Silver

USDINR

Bond Yields

This data feeds the Morning Report.

---

# News Intelligence Pipeline

Collect

↓

Deduplicate

↓

Classify

↓

Summarize

↓

Sentiment Analysis

↓

Company Mapping

↓

Sector Mapping

↓

Importance Ranking

↓

AI Consumption

---

# Morning Report Builder

Every morning

Collect

↓

Global Markets

↓

Gift Nifty

↓

News

↓

Economic Calendar

↓

Sector Analysis

↓

Scanner Results

↓

Top Opportunities

↓

Generate Report

↓

Store

↓

Notify Users

Implemented in services/morning_report.py (Sprint 10). Every market read goes
through the Market Gateway — the builder never touches a provider.

Sections degrade independently: an unreachable feed costs its own section and
nothing else. Any section that cannot be sourced is marked `available: false`
with a reason and is never filled with a substitute value.

## Gift Nifty

Gift Nifty is a Nifty 50 futures contract on NSE International Exchange. It
trades while the NSE cash market is closed, which makes it the best pre-market
read on the open — and it is carried by no free feed (not Yahoo, Alpha
Vantage, or a broker's NSE/BSE instrument list). It requires an NSE IX data
subscription or a licensed vendor.

services/market_engine/gift_nifty.py is therefore a collector with a
priority-ordered adapter chain and no adapter registered by default: it
reports the quote as explicitly unavailable rather than deriving one. Register
an adapter and every consumer — Morning Report, AI context, frontend — picks
it up with no further change:

    gift_nifty.register_adapter("nse_ix", fetch_fn, tier="streaming")

Adapters are tried in order; the first non-None result wins; one that raises
is logged and skipped.

---

# Real-Time Streaming

Primary

WebSockets

Fallback

Polling

Streams

Prices

Orders

Portfolio

Scanner

News

Indices

AI Activity

---

# Event Publishing

Events

market.open

market.close

price.updated

sector.updated

news.received

scanner.updated

opportunity.detected

market.alert

market.crash

market.recovery

Everything enters the Event Bus.

---

# AI Integration

The Market Engine never decides.

It provides data to:

Market Analyst

Technical Analyst

Fundamental Analyst

News Agent

Morning Report Agent

Risk Manager

Portfolio Manager

The AI makes decisions using Market Engine outputs.

---

# Error Handling

Handle

API Failure

Timeout

Rate Limit

Network Failure

Invalid Response

Missing Fields

Recovery

Retry

Automatic provider failover via the Source Manager
(Broker WebSocket → Licensed Feed → Yahoo Finance;
see MARKET_DATA_ARCHITECTURE.md)

Cached Data

Admin Alert

---

# Performance Targets

Market Price Update

< 1  seconds

Scanner Refresh

< 10 seconds

Morning Report

< 60 seconds

Dashboard Load

< 2 seconds

Search Response

< 500 ms

---

# Monitoring

Monitor

API Latency

Collector Status

Queue Length

Cache Hit Rate

WebSocket Health

Event Throughput

Scanner Duration

Provider Availability

Displayed in Admin Portal.

---

# Security

Validate Provider Responses

Rate Limit External APIs

Cache Responses

Encrypt Credentials

Never expose provider secrets

Log failures

---

# Future Enhancements

Machine Learning Ranking

Market Regime Detection

Sector Rotation Prediction

Volatility Forecasting

AI Trend Prediction

Alternative Data

Satellite Data (Future)

Social Sentiment

Options Flow Analysis

Global Macro Analysis

---

# Development Checklist

Before release verify:

✓ Live Data

✓ Validation

✓ Caching

✓ WebSocket Streaming

✓ Scanner

✓ Ranking

✓ Event Publishing

✓ Monitoring

✓ Error Handling

✓ Documentation

✓ Performance

---

# Long-Term Vision

The Market Engine should become a standalone financial data platform.

Every feature in StockAssist AI depends on its accuracy, reliability, and performance.

It should be capable of scanning thousands of securities continuously, providing clean, normalized, and real-time market intelligence to the AI system while remaining provider-independent and horizontally scalable.

---

# End of Market Engine Documentation