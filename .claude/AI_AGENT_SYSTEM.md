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

# AI Artifact Provenance (D6.9)

This section is authoritative for every AI-generated artifact on the platform.
The code counterpart is `backend/services/ai_provenance.py`.

## The rule

An artifact may be presented as AI-generated only when a model actually
produced it, and that fact must be recorded at generation time — never inferred
at read time.

## Four facts that must never be collapsed

    GENERATION TIME  ≠  MARKET DATA TIME  ≠  CURRENT AI HEALTH  ≠  FRESHNESS

Collapsing them is what produced the defect this model exists to prevent: on a
deployment whose API key had no billing credit, the workspace header read
**AI ready**, the Morning Report read **AI Market Briefing**, and the text
underneath was the provider's own "AI services are currently offline" message —
persisted and served to every user for the rest of the day.

Specifically:

* An AI provider being online now is **not** evidence that today's artifact was
  generated.
* An AI provider being offline now is **not** evidence that an existing
  artifact is fake. A report generated at 08:30 stays a real 08:30 report when
  the provider dies at 14:00.
* A scheduler having run is **not** evidence that generation succeeded.
* An API key being configured is **not** evidence that a model answered.

## Generation status

    generating    a run is in flight; any body present is partial
    completed     the run finished and produced a usable artifact
    failed        the run was attempted and did not produce one
    unavailable   the run could not be attempted (inputs unreachable)

Only `completed` licenses treating the result as an artifact. Only
`completed` **and** `ai.outcome == "succeeded"` licenses calling it
AI-generated — each rules out a case the other misses.

## Provenance fields

    provenance_version   shape version of this record
    artifact_type        e.g. "morning_report"
    artifact_id          e.g. "morning:2026-09-11"
    status               the four states above
    generated_at         when the attempt BEGAN
    completed_at         when it SUCCEEDED — the only freshness clock
    analysis_version     version of the artifact's composition logic
    ai.outcome           succeeded | not_configured | provider_error | not_applicable
    ai.provider          written ONLY on success; never the provider attempted
    ai.model             written ONLY on success
    ai.prompt_key        the Prompt Library key used
    ai.prompt_version    that prompt's version
    market_data.source_tier   the freshness TIER, never a provider name
    market_data.observed_at   when the numbers were observed
    error                { code, message } — user-facing text only

`ai.provider` and `ai.model` have exactly one writer, `ai_provenance.ai_succeeded`,
so an attribution cannot appear without a model call.

## Freshness

Derived at read time from `completed_at` and from nothing else — never from
`updated_at`, the request clock, the page-load time, or current AI health.
Storing a freshness value is forbidden: it is a function of *now* and would be
wrong the instant after it was written.

    FRESH        ≤ 3h    the pre-open read is still the operative one
    STALE        ≤ 12h   same trading day; the market has moved — warn
    VERY_STALE   > 12h   predates today's pre-open window
    UNKNOWN              completed, but the instant was never recorded
    UNAVAILABLE          no successful generation to measure

The 3h and 12h thresholds are derived from the NSE session, not chosen for
roundness: generation is 08:30 IST, so 08:30+3h = 11:30 (two hours into
trading) and 08:30+12h = 20:30 (after the 15:30 close). They live in
`ai_provenance.FRESHNESS_POLICY` and are shipped to clients so the browser can
let the age advance without inventing a second definition of "stale".

**Freshness changes the label, never the availability.** A stale artifact stays
visible with a warning; it is never replaced by an error screen.

## Failure behaviour — the no-fake-AI rule

When AI is unavailable the platform must NOT invent a report, picks, confidence
scores, AI activity or AI reasoning, and must not mark AI active. Two things
are permitted and must be labelled:

* A **deterministic fallback** that restates data actually collected. The
  Morning Report's grounded briefing is one; it is stamped
  `briefing_source: "deterministic"` and never carries an AI heading.
* A **previously generated** artifact, shown with its real provenance and
  freshness warning.

Provider error strings never leave the process: they carry request ids, account
identifiers and echoed prompts. Failures are recorded as a classified label
from the closed `observability.errors` vocabulary plus a message written for a
user.

## Legacy records

Artifacts stored before provenance existed carry no record and are described as
`status: "unknown"`, `known: false`. No timestamp, provider or model is ever
backfilled for them. In particular `generated_at` is **not** promoted into
`completed_at`: it was written at the top of a path that could still fail, so
promoting it would manufacture a success that may never have happened.

"Unknown provenance" and "failed generation" are different answers and must
stay distinguishable — an absent record cannot testify that generation failed.

## Current AI health

`services/ai_health.py` records the observed outcome of every real model call,
and `/api/ai/status` reports `online` only when a configured provider is not in
an observed-failure state. A configured provider that has never been called is
reported as not-yet-verified, not as broken: knowing nothing is not evidence of
an outage.

## Adding a new AI artifact

Declare the type in `ai_provenance.ARTIFACT_TYPES`, then call `begin()` →
`ai_succeeded()` / `ai_did_not_run()` → `market_data()` → `completed()` /
`failed()`. Serve `describe()`. Do not extend the module per artifact, and do
not re-implement freshness anywhere.

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

## Lifecycle and provenance (D6.9)

    scheduler 08:30 (or an on-demand request)
      → ai_provenance.begin()            status: generating
      → market layer built via the Market Gateway
      → briefing: a model, or the grounded deterministic fallback
      → ai_succeeded() | ai_did_not_run()
      → market_data(source_tier, observed_at)
      → completed() | failed()
      → persisted to db.reports (EVERY outcome, including failures)
      → morningreport.generated published, carrying status + completed_at
      → read: describe() derives freshness from completed_at

Every exit persists a record, failures included. "Today's report did not
generate" and "nobody has asked for today's report yet" used to be the same
observation — an absent document — and they are different facts.

A persisted failure is evidence about the last attempt, not a report: the next
read retries rather than serving it, so a transient 08:30 outage is never
pinned for the day.

Two things on this report are NOT AI and are labelled accordingly:

* `ai_briefing` is a model's narration only when `briefing_source == "ai"`.
  Otherwise it is the grounded restatement of the numbers the report collected,
  and it is rendered under "Market Briefing", not "AI Market Briefing".
* `top_picks` are a deterministic RSI / volume / MACD / pattern scan
  (`top_picks_source: "deterministic_technical_scan"`) and have never involved
  a model. They carry this report's generation timestamp, so a stale report's
  picks read as stale rather than as live.

See "AI Artifact Provenance" above for the full model.

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