# StockAssist AI
## Market Engine Documentation

Version: 1.0

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