# StockAssist AI
## AI Agent System
Version: 1.1

---

# Overview

StockAssist AI is not powered by a single AI.

It is powered by an intelligent Multi-Agent System called:

StockAssist Intelligence (SAI)

SAI is responsible for every intelligent action inside the platform.

The user never interacts with individual agents.

Instead, the user interacts with one unified AI assistant.

Internally, the system distributes work to specialized AI agents.

The agents communicate with each other through the Master Orchestrator.

Every recommendation presented to the user is a collaboration between multiple expert agents.

---

# Goals

The AI system should:

Continuously monitor markets

Explain every recommendation

Teach users

Protect users from unnecessary risk

Continuously improve

Remember user context

Monitor portfolios

Monitor open trades

Generate reports

Coordinate all AI workflows

---

# AI Design Principles

The AI should behave like a team of professionals.

Not like one chatbot.

Every agent has one responsibility.

Every agent is an expert.

No duplicated responsibilities.

Agents collaborate.

Agents share context.

Agents never guess.

Agents explain uncertainty.

---

# Market Data Access

AI agents never communicate with market data providers.

Not directly. Not through helper utilities. Never.

All market context reaches the AI through one path:

AI request

↓

AI Context Builder

↓

Market Engine (normalized, provider-agnostic data)

↓

Normalized Market Context

↓

Claude

↓

Response

The AI never knows which provider generated the data. Context carries only the source tier (streaming / delayed) and timestamps, so the AI can calibrate its language ("live price" vs "as of 10:42 AM").

The AI must never say "I don't have live market data." It always reasons over the last known market state with its timestamp.

When a user connects a broker, the AI's context automatically becomes fresher with zero prompt or pipeline changes.

Authoritative reference: MARKET_DATA_ARCHITECTURE.md.

---

# Master Orchestrator

The Master Orchestrator is the brain.

Responsibilities:

Receive user requests

Determine which agents are required

Delegate work

Collect outputs

Resolve conflicts

Merge reasoning

Generate final response

Maintain conversation memory

Track agent health

Optimize AI cost

Prioritize speed

Prioritize accuracy

The user only sees one AI.

---

# Agent Communication

Agents communicate internally.

Example:

User asks:

Should I buy Reliance?

↓

Master Orchestrator

↓

Market Analyst

↓

Technical Analyst

↓

Fundamental Analyst

↓

News Intelligence

↓

Risk Manager

↓

Portfolio Manager

↓

Master Orchestrator

↓

Unified Answer

The user never sees internal communication.

---

# AI Lifecycle

Every request follows:

Understand

↓

Plan

↓

Delegate

↓

Analyze

↓

Debate (if required)

↓

Merge

↓

Explain

↓

Respond

↓

Remember

---

# AI Memory

The platform maintains multiple types of memory.

## User Memory

Risk preference

Investment goals

Preferred sectors

Favorite companies

Experience level

Preferred language

Notification preferences

Learning progress

---

## Portfolio Memory

Holdings

Historical performance

Past recommendations

Past trades

Current allocation

Risk exposure

---

## Conversation Memory

Previous questions

Previous explanations

User interests

Follow-up context

Conversation summaries

---

## Platform Memory

Market trends

Historical AI decisions

System health

API health

Global events

---

# Agent Responsibilities

The platform consists of specialized AI agents.

---

# 1. Market Analyst

Purpose

Monitor financial markets continuously.

Responsibilities

Monitor NSE

Monitor BSE

Monitor indices

Monitor sectors

Detect breakouts

Detect reversals

Detect unusual volume

Monitor volatility

Monitor market breadth

Generate opportunities

Never stop scanning.

Data source: normalized events from the Market Engine only — never providers directly.

---

# 2. Technical Analyst

Purpose

Read charts.

Analyze:

RSI

MACD

EMA

SMA

VWAP

Fibonacci

Trendlines

Support

Resistance

Patterns

Candlesticks

Volume

Outputs

Trend

Confidence

Entry

Exit

Stop Loss

Targets

---

# 3. Fundamental Analyst

Analyze:

Revenue

EPS

Debt

Cash Flow

Margins

ROE

ROCE

Valuation

Institutional Holdings

Promoter Holdings

Dividend

Quarterly Results

Annual Reports

Investment Score

---

# 4. News Intelligence

Reads:

Financial News

Government Policies

Company Announcements

Earnings

Global Events

Macroeconomic Events

Outputs

Summary

Impact

Affected Companies

Risk

Confidence

---

# 5. Portfolio Manager

Monitors

Portfolio

Allocation

Diversification

Sector Exposure

Risk

Performance

Rebalancing

Dividend Opportunities

Tax Optimization (future)

---

# 6. Trade Monitor

Monitors

Every open trade

Target

Stop Loss

News

Risk

Volume

Price Action

Alerts

Trade Health

---

# 7. Learning Mentor

Responsibilities

Teach concepts

Review mistakes

Explain indicators

Recommend learning material

Measure progress

Encourage discipline

---

# 8. Strategy Builder

Converts natural language into strategies.

Supports:

Paper Trading

Backtesting

Optimization

Future Automation

---

# 9. Morning Report Agent

Automatically generates:

Morning Report

Market Summary

News Summary

Watchlist

Trade Opportunities

Sector Analysis

Risk Warnings

Before market open every day.

Transparency (Sprint R7): report generation streams a live AIRun step
timeline over the `ai` channel — per-user for on-demand requests, broadcast
for the 8:30 scheduled run. See REALTIME_SYSTEM.md → "AI Thinking Process".

Step plan (Sprint 10):

Collecting Market Data → Reading Global Markets → Reading News → Checking
Economic Calendar → Scanning NSE → Analyzing Sector Flows → Generating Report
→ Saving Report

plus Reviewing Your Portfolio when the report is generated for a signed-in
user. A cached market layer skips the market steps entirely — only the
personal step runs, because only it does real work.

A section that fails and degrades marks its own step `warning` and completes
the run `warning`. The timeline never reports `done` for work that did not
succeed.

Structure (Sprint 10): the report is two layers. The market layer is shared
by every user and generated once per day; the personal layer (portfolio
alerts) is computed per request and never persisted into the shared document
— it is keyed by date alone, so a per-user field stored there would reach the
wrong user. Implementation: services/morning_report.py.

---

# 10. Risk Manager

Calculates

Portfolio Risk

Trade Risk

Position Size

Maximum Drawdown

Correlation

Market Risk

Volatility

Protects users.

---

# 11. Broker Agent

Supports:

Zerodha

Upstox

Future Brokers

Handles

Authentication

Orders

Portfolio Sync

Trade History

Execution Status

---

# 12. Notification Agent

Responsible for:

Morning Report

Trade Alerts

Portfolio Alerts

Breaking News

Price Alerts

Target Hit

Stop Loss Hit

Market Crash

Never spam.

Prioritize relevance.

---

# 13. Subscription Manager

Controls:

Plans

Credits

AI Limits

Premium Features

Billing Permissions

Usage Tracking

---

# 14. Operations Agent

Monitors

Server

Database

API Health

AI Health

Costs

Logs

Analytics

Admin Dashboard

---

# Claude & Gemini Collaboration

The platform uses multiple AI models.

Claude

Strengths

Deep reasoning

Architecture

Code generation

Portfolio review

Risk analysis

Complex reports

Gemini

Strengths

Fast responses

Large context

News summarization

Image understanding

Research assistance

The Master Orchestrator decides which model to use.

Some tasks may use both.

---

# AI Debate System

Certain requests require multiple opinions.

Example:

Should I buy HDFC Bank?

Claude:

Bullish

Gemini:

Neutral

The Master Orchestrator compares:

Reasoning

Evidence

Confidence

Data

Agreement

Disagreement

Then generates one final recommendation.

The user may optionally view the debate.

---

# Confidence System

Every AI response includes:

Confidence Score

Risk Score

Evidence

Data Sources

Limitations

Alternative Scenarios

Never present uncertainty as fact.

---

# AI Transparency

Always distinguish:

Facts

Inference

Opinion

Prediction

Assumption

The user should always know how the conclusion was reached.

---

# Continuous Background Jobs

Even when users are offline:

Monitor markets

Monitor news

Monitor portfolios

Generate reports

Monitor trades

Prepare alerts

Update watchlists

Analyze sectors

Prepare recommendations

The AI never sleeps.

---

# Long-Term Goal

Build the world's most intelligent AI-powered financial operating system.

Every AI agent should work together like a team of professional analysts, giving users transparent explanations, timely insights, and continuous guidance while leaving important financial decisions under the user's control.