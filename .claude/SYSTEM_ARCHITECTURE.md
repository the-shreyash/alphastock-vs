# StockAssist AI
## System Architecture
Version: 1.1
Part 1 - Foundation Architecture

---

# Introduction

This document defines the complete technical architecture of StockAssist AI.

Every developer and every AI assistant working on this project must follow this architecture.

The goal is to create software that can eventually serve:

• Millions of API requests

• Thousands of concurrent users

• Multiple AI providers

• Multiple broker integrations

• Real-time market streaming

• Continuous AI monitoring

This architecture prioritizes:

Scalability

Maintainability

Security

Performance

Developer Experience

Production Readiness

---

# High Level Architecture

StockAssist AI follows a modular layered architecture.

                    Internet
                        │
                Cloudflare CDN
                        │
             Reverse Proxy (Nginx)
                        │
        ┌────────────────────────────┐
        │                            │
Frontend (React)          Admin Portal
        │                            │
        └──────────────┬─────────────┘
                       │
                  API Gateway
                       │
────────────────────────────────────────
Authentication Layer
────────────────────────────────────────
                       │
Business Services Layer
────────────────────────────────────────
                       │
AI Orchestrator
                       │
────────────────────────────────────────
Market Engine
Portfolio Engine
Broker Engine
Notification Engine
Subscription Engine
Admin Engine
Learning Engine
────────────────────────────────────────
                       │
Database Layer
                       │
MongoDB
Redis
Vector DB (Future)
                       │
────────────────────────────────────────
Market Data Layer
(see MARKET_DATA_ARCHITECTURE.md)
────────────────────────────────────────
                       │
Market Gateway
Source Manager
Provider Adapters
                       │
Market Data Providers
                       │
Yahoo Finance
Broker WebSockets (Zerodha, Upstox, Angel One, Fyers, Dhan)
Licensed Exchange Feeds (Future)
                       │
Other External APIs
                       │
TradingView
Claude
Gemini
News API
Economic Calendar
Mail Service
Notification Service

---

# Technology Stack

Frontend

React 19

TypeScript

Vite

TailwindCSS

shadcn/ui

Framer Motion

GSAP

React Query

React Hook Form

Zod

Lucide Icons

TradingView Widgets

Recharts

Socket.IO Client

Backend

Node.js

Express.js

TypeScript

Socket.IO

JWT

Passport

Multer

BullMQ

Redis

MongoDB

Mongoose

Cron Jobs

Docker

Database

MongoDB Atlas

Redis

Future

Vector Database

Pinecone / Weaviate

Infrastructure

Railway

Vercel

Cloudflare

GitHub Actions

Docker

Monitoring

Sentry

PostHog

Prometheus (Future)

Grafana (Future)

---

# System Layers

The project follows layered architecture.

Presentation Layer

↓

Application Layer

↓

Business Layer

↓

AI Layer

↓

Infrastructure Layer

↓

Database Layer

Each layer has one responsibility.

Never mix responsibilities.

---

# Folder Structure

StockAssist-AI/

```
client/
server/
shared/
.claude/
docs/
scripts/
docker/
.github/
```

---

# Client Architecture

client/

```
src/
│
├── app/
├── assets/
├── components/
│
├── layouts/
├── pages/
├── routes/
├── hooks/
├── services/
├── contexts/
├── stores/
├── providers/
├── lib/
├── utils/
├── constants/
├── types/
├── styles/
├── animations/
├── mock/
├── icons/
├── theme/
└── config/
```

---

# Component Architecture

Components are divided into:

UI Components

↓

Feature Components

↓

Business Components

↓

Layouts

↓

Pages

Never create large components.

Prefer composition.

---

Example

```
Button

↓

Card

↓

StockCard

↓

MarketOverview

↓

Dashboard
```

---

# Page Structure

Every page contains

```
Page

↓

Layout

↓

Sections

↓

Components

↓

Hooks

↓

Services
```

Business logic never belongs inside pages.

---

# Backend Architecture

server/

```
src/

controllers/

routes/

middleware/

services/

repositories/

models/

jobs/

events/

workers/

socket/

ai/

market/

broker/

notifications/

scheduler/

utils/

config/

database/

validators/

types/
```

---

# Request Flow

Client

↓

API Gateway

↓

Authentication

↓

Validation

↓

Controller

↓

Service

↓

Repository

↓

Database

↓

Response

Business logic belongs only inside Services.

---

# Controllers

Responsibilities

Receive Request

Validate Input

Call Service

Return Response

Nothing else.

Controllers must remain small.

---

# Services

The heart of backend logic.

Responsibilities

Business Rules

AI Calls

Broker Calls

Portfolio Logic

Trade Logic

Risk Calculations

News Analysis

Market Analysis

Never access Express objects directly.

---

# Repositories

Responsibilities

Database Queries

Aggregation

Pagination

Caching

No business logic.

---

# Models

Each Mongo model should contain:

Schema

Indexes

Virtual Fields

Validation

No business logic.

---

# Shared Types

shared/

Contains

Enums

Interfaces

Constants

DTOs

API Contracts

Shared Validation

Frontend and backend should use the same types whenever possible.

---

# Frontend State

Use React Query for:

API Data

Caching

Background Refresh

Mutation

Invalidation

Use Context only for:

Theme

Authentication

Preferences

Avoid unnecessary global state.

---

# Service Layer

Every API call belongs inside:

services/

Example

```
MarketService

PortfolioService

NewsService

BrokerService

AuthService

AIService

NotificationService
```

Never call fetch directly inside components.

---

# Routing

Public

Landing

Pricing

FAQ

Login

Register

Private

Dashboard

Markets

Portfolio

Trading

Journal

AI Workspace

Settings

Admin

Admin Dashboard

Analytics

Users

Billing

API Monitoring

---

# Authentication

JWT

Refresh Tokens

Secure Cookies

Role Based Access

Session Validation

Device Tracking

Future

OAuth

Passkeys

---

# Authorization

Roles

Guest

User

Premium

Elite

Admin

Super Admin

Permissions are checked on every request.

---

# Environment Variables

Never hardcode secrets.

Use:

```
MONGO_URI

JWT_SECRET

REDIS_URL

CLAUDE_API_KEY

GEMINI_API_KEY

YAHOO_API_KEY

ZERODHA_CLIENT_ID

ZERODHA_SECRET

NEWS_API_KEY

MAIL_API_KEY
```

Production values never belong inside Git.

---

# Error Handling

Every endpoint returns

Success

Loading

Validation Error

Authentication Error

Authorization Error

Not Found

Rate Limited

Internal Server Error

Every response follows one structure.

---

# Logging

Log:

Requests

Errors

Authentication

Payments

Trades

Broker Calls

AI Calls

API Failures

Never log secrets.

---

# Configuration

All configuration belongs inside

config/

No magic values inside code.

Everything configurable.

---

# Performance Principles

Lazy Load Pages

Code Splitting

Image Optimization

Caching

Compression

Background Refresh

Pagination

Debouncing

Memoization

Virtualization

Streaming Responses (Future)

---

# Coding Principles

Single Responsibility

Open Closed Principle

Dependency Injection where appropriate

Composition over Inheritance

Reusable Components

Reusable Services

Strong Typing

Meaningful Naming

No Duplicate Code

---

# Development Workflow

Every feature follows:

Research

↓

Architecture

↓

Database

↓

Backend

↓

Frontend

↓

Integration

↓

Testing

↓

Performance Review

↓

Security Review

↓

Documentation

↓

Deployment

Never skip architecture.

---

# Long-Term Goal

The architecture should support:

10,000+ users

Multiple brokers

Multiple AI providers

Multiple subscription plans

Real-time trading

Continuous AI monitoring

Future mobile apps

Enterprise customers

Future international markets

Without requiring a complete rewrite.

Every architectural decision should move the project toward becoming a production-grade AI Trading Operating System.

# part -2

---

# Part 2 - AI Orchestration Architecture

Version: 1.0

---

# Overview

The AI layer is the heart of StockAssist AI.

Unlike traditional AI applications that send every request directly to a Large Language Model (LLM), StockAssist AI follows an Agentic AI architecture.

Instead of one AI doing everything, multiple specialized AI agents collaborate to solve a user's request.

The user always interacts with a single assistant called **StockAssist Intelligence (SAI)**.

Internally, SAI coordinates a team of specialized agents.

---

# High Level AI Architecture

```

User

↓

Master AI (SAI)

↓

AI Orchestrator

↓

Planner

↓

Agent Router

↓

┌───────────────┬──────────────┬──────────────┐
│ │ │ │
Market News Technical Fundamental
Agent Agent Agent Agent
│ │ │ │
└───────────────┴──────────────┴──────────────┘

↓

Risk Agent

↓

Portfolio Agent

↓

Broker Agent

↓

Notification Agent

↓

Final Response Builder

↓

User

```

---

# AI Layers

The AI system is divided into layers.

Layer 1

User Interaction

Layer 2

Master AI

Layer 3

Planning

Layer 4

Specialized Agents

Layer 5

Reasoning

Layer 6

Response Generation

Layer 7

Memory

Layer 8

Monitoring

Every layer has one responsibility.

---

# Master AI (SAI)

SAI is the single intelligence users interact with.

Responsibilities

Receive user requests

Understand intent

Maintain conversation

Maintain memory

Call orchestrator

Merge responses

Generate explanations

Return final answer

The user should never know multiple agents exist unless we choose to visualize them.

---

# AI Orchestrator

Purpose

Coordinate every AI workflow.

Responsibilities

Receive task

Break task into subtasks

Select required agents

Execute agents

Collect outputs

Resolve conflicts

Generate unified response

Track execution

Log reasoning

Estimate cost

Monitor latency

Retry failures

The orchestrator never performs analysis itself.

It coordinates.

---

# Planner

Purpose

Understand user intent.

Example

User:

Should I buy Reliance today?

Planner creates:

Task 1

Market Analysis

Task 2

Technical Analysis

Task 3

Fundamental Analysis

Task 4

News Analysis

Task 5

Risk Analysis

Task 6

Portfolio Analysis

Return combined recommendation.

---

# Agent Router

Purpose

Determine which agents should execute.

Simple question

↓

Only Learning Agent

Complex trade

↓

Market

↓

Technical

↓

Fundamental

↓

News

↓

Risk

↓

Portfolio

↓

Broker

↓

Merge

This prevents unnecessary AI costs.

---

# Agent Execution

Agents execute independently.

Whenever possible:

Run agents in parallel.

Never wait sequentially if not required.

Example

Market Agent

News Agent

Technical Agent

↓

Execute Together

↓

Merge

↓

Generate Answer

This dramatically improves response speed.

---

# Response Builder

Purpose

Merge outputs from multiple agents.

Example

Market

Bullish

News

Positive

Technical

Strong Breakout

Risk

Medium

Portfolio

Suitable

↓

Final Answer

Confidence

89%

Suggested Action

BUY

Reasons

Volume breakout

Positive news

Sector leadership

Healthy fundamentals

Risk

Medium

Alternative

Wait for confirmation above ₹xxxx

---

# AI Memory

The system maintains multiple memory layers.

Short-Term Memory

Current conversation

Current stock

Current portfolio

Current task

Medium-Term Memory

Recent conversations

Watchlist

Open trades

Recent reports

Long-Term Memory

User profile

Risk profile

Investment goals

Learning history

Preferred sectors

Favorite stocks

Portfolio evolution

Future

Semantic Vector Memory

For retrieval augmented generation.

---

# AI Context Engine

Before every response:

Gather

User Profile

Portfolio

Watchlist

Market

News

Previous Conversations

Recent Trades

Morning Report

Current Time

Broker Status

Open Orders

Then build context.

The AI should always understand the user's situation.

---

# Reflection Engine

Before responding:

Ask:

Is my answer complete?

Did I miss any risks?

Did I explain enough?

Should another agent review this?

Reflection improves reliability.

---

# Debate Engine

Some decisions require multiple opinions.

Example

Claude

↓

Bullish

Gemini

↓

Neutral

↓

Compare

↓

Evidence

↓

Confidence

↓

Final Explanation

Users can optionally open:

"View AI Debate"

to understand differing viewpoints.

---

# Self-Correction Engine

After generating a response:

Check

Facts

Numbers

Missing sections

Confidence

Reasoning

Formatting

If problems exist:

Regenerate affected sections.

---

# Confidence Engine

Every AI response includes:

Overall Confidence

Technical Confidence

Fundamental Confidence

News Confidence

Market Confidence

Risk Confidence

Example

Overall

91%

Technical

95%

Fundamental

86%

News

80%

Risk

92%

---

# Cost Optimizer

AI requests cost money.

Optimize automatically.

Simple question

↓

Gemini

Complex reasoning

↓

Claude

Market summary

↓

Gemini

Portfolio Review

↓

Claude

Large Reports

↓

Claude

Fast Search

↓

Gemini

The orchestrator chooses automatically.

---

# AI Scheduling

Background Jobs

05:00

Read US Markets

05:10

Read Global News

05:20

Read Futures

05:30

Analyze GIFT Nifty

05:40

Read FII/DII

06:00

Scan NSE

06:20

Generate Morning Report

07:00

Send Morning Report

09:15

Start Live Monitoring

Every schedule configurable.

---

# Background Workers

Workers run independently.

Market Worker

News Worker

Portfolio Worker

Trade Worker

Notification Worker

Risk Worker

Scheduler Worker

Workers never block each other.

---

# Event Bus

Every important action becomes an event.

Example

Price Updated

↓

Portfolio Updated

↓

Trade Updated

↓

Risk Updated

↓

Notification Triggered

Everything communicates through events.

Future

Kafka

Redis Streams

RabbitMQ

---

# AI Health Monitor

Monitor

Claude

Gemini

Response Time

Failure Rate

Latency

Daily Cost

Tokens Used

Availability

Fallback Status

If Claude fails

↓

Automatically switch to Gemini where appropriate.

If Gemini fails

↓

Retry

↓

Queue

↓

Notify Admin

---

# Prompt Manager

Every AI prompt belongs inside

server/ai/prompts/

Never hardcode prompts.

Version every prompt.

Example

MorningReport.md

PortfolioReview.md

TradeAnalysis.md

LearningMode.md

RiskReview.md

This allows prompts to evolve independently.

---

# Tool Calling

Agents should use tools rather than relying solely on language models.

Examples

Market API

News API

Broker API

Portfolio API

Economic Calendar

Technical Indicators

Database

Notification Service

The LLM reasons over tool outputs.

---

# AI Safety

The AI must never:

Guarantee profits

Promise returns

Hide uncertainty

Invent market data

Ignore missing information

Encourage reckless trading

Instead:

Explain risks.

Explain uncertainty.

Recommend verification where appropriate.

---

# Future Evolution

The architecture should support:

More AI providers

OpenAI

Anthropic

Gemini

Local Models

Financial LLMs

More agents

Voice Assistant

Image Analysis

PDF Analysis

Research Assistant

Autonomous workflows

Without changing the overall architecture.

---

# End of Part 2

---

# Part 3 — Core Business Engines

Version: 1.0

---

# Overview

The Business Layer contains the core engines responsible for running StockAssist AI.

Unlike traditional applications where business logic is scattered across controllers and services, StockAssist AI organizes business logic into independent engines.

Each engine owns a single business domain.

Every engine communicates through events instead of tightly coupling services together.

The Business Layer should remain independent of:

Frontend

AI Providers

Broker Providers

Database Vendors

Notification Providers

This allows the platform to scale and evolve without major rewrites.

---

# High Level Engine Architecture

```

                    API Gateway
                          │
────────────────────────────────────────────
                    Business Layer
────────────────────────────────────────────

Market Engine

Portfolio Engine

Trading Engine

Broker Engine

Notification Engine

News Engine

Learning Engine

Subscription Engine

Analytics Engine

Admin Engine

Search Engine

Monitoring Engine

────────────────────────────────────────────

                          │

                    Database Layer

```

---

# Market Engine

## Purpose

The Market Engine is responsible for processing, caching, and distributing all market data.

Every market-related feature depends on this engine.

The engine is provider-independent by design.

It never communicates with market data providers directly. All market data enters the platform through the Market Gateway, is normalized into the universal market event model, and only then reaches the Market Engine.

Provider selection, switching, and failover are handled by the Source Manager.

Switching providers requires zero changes to the Market Engine.

Authoritative reference: MARKET_DATA_ARCHITECTURE.md.

```
Market Data Providers
        ↓
Market Gateway
        ↓
Source Manager
        ↓
Normalization Layer
        ↓
Market Engine
        ↓
Redis Event Bus
        ↓
WebSocket
        ↓
Frontend
```

---

## Responsibilities

Fetch Live Prices

Market Depth

OHLC Data

Historical Data

Indices

Sector Performance

Market Breadth

Volume

52 Week High

52 Week Low

Corporate Actions

Dividend Information

IPO Information

Currency

Commodity

Options Chain

Future Data

---

## Internal Modules

Price Service

Index Service

Historical Service

Sector Service

Options Service

Heatmap Service

Corporate Actions Service

Cache Manager

WebSocket Publisher

---

## Update Frequency

Live Prices

1–5 seconds

Indices

5 seconds

Historical Data

On Demand

Corporate Actions

Daily

IPO

Daily

Market Breadth

10 seconds

---

# Portfolio Engine

## Purpose

Maintain complete portfolio intelligence.

The Portfolio Engine should never depend directly on AI.

AI consumes Portfolio data.

Portfolio never consumes AI.

---

## Responsibilities

Portfolio Valuation

Profit Loss

Allocation

Sector Exposure

Dividend Tracking

Asset Allocation

Broker Sync

Performance

Cash Position

Historical Performance

Portfolio Health

Risk Metrics

---

## Portfolio Pipeline

Broker Sync

↓

Normalize Holdings

↓

Calculate Values

↓

Calculate Allocation

↓

Calculate Risk

↓

Publish Portfolio Event

↓

AI Consumes Event

---

# Trading Engine

## Purpose

Handle every trading operation.

This engine coordinates:

Orders

Positions

Trade History

Paper Trading

Backtesting

Future Automation

---

## Responsibilities

Place Orders

Modify Orders

Cancel Orders

Track Orders

Trade Validation

Position Tracking

PnL Calculation

Target Tracking

Stop Loss Tracking

Trade Statistics

---

## Order Lifecycle

User

↓

Validate

↓

Risk Check

↓

Broker Engine

↓

Order Status

↓

Portfolio Update

↓

Notification

↓

AI Review

---

# Broker Engine

Purpose

Connect external brokers.

Supported

Zerodha

Upstox

Angel One

Groww (Future)

Interactive Brokers (Future)

---

Responsibilities

OAuth

Authentication

Portfolio Sync

Order Placement

Order Modification

Order Cancellation

Order Tracking

Funds

Margins

Positions

Trade History

The Broker Engine should expose one unified interface.

Other services never interact with brokers directly.

---

Broker Adapter Pattern

```

Trading Engine

↓

Broker Interface

↓

Zerodha Adapter

↓

Upstox Adapter

↓

Future Broker Adapter

```

Every broker implements the same interface.

---

# News Engine

Purpose

Centralize every news source.

Responsibilities

Financial News

Company News

Global News

Economic News

Policy Changes

Corporate Actions

IPO News

Sector News

Market Rumors (Future)

---

News Pipeline

Collect

↓

Deduplicate

↓

Rank

↓

Categorize

↓

Summarize

↓

Store

↓

Publish Event

↓

AI Analysis

---

# Notification Engine

Purpose

Deliver meaningful notifications.

Never spam.

---

Channels

Browser

Email

Push Notification

SMS (Future)

WhatsApp (Future)

---

Notification Types

Morning Report

Trade Alert

Portfolio Alert

Price Alert

News Alert

Risk Alert

Payment Alert

Security Alert

Announcement

---

Notification Workflow

Trigger

↓

Rules Engine

↓

Priority

↓

Delivery Channel

↓

Send

↓

Track Delivery

↓

Log

---

# Search Engine

Purpose

Power global search.

Search:

Stocks

Users

News

Strategies

Journal

Portfolio

Watchlist

Reports

AI Conversations

Future

Semantic Search

---

Search Features

Autocomplete

Suggestions

Trending

Recent

History

Filters

Ranking

---

# Learning Engine

Purpose

Track user learning.

Responsibilities

Progress

Completed Lessons

Quiz Scores

Trading Concepts

Mistakes

Recommendations

Learning Roadmap

Certificates (Future)

---

Learning Pipeline

Trade

↓

AI Review

↓

Detect Mistake

↓

Generate Lesson

↓

Track Progress

---

# Subscription Engine

Purpose

Manage access.

Responsibilities

Plans

Billing

Credits

Limits

AI Requests

Premium Features

Feature Unlocking

Grace Period

Renewals

---

Plan Validation

Every request checks:

User Plan

↓

Permission

↓

Usage

↓

Remaining Credits

↓

Allow

or

Reject

---

# Analytics Engine

Purpose

Generate business insights.

Collect

Users

Trades

AI Usage

Morning Reports

Revenue

Retention

Engagement

Errors

Performance

Everything should be event-driven.

---

Analytics Dashboards

User Analytics

Business Analytics

AI Analytics

Trading Analytics

Revenue Analytics

API Analytics

Admin Analytics

---

# Monitoring Engine

Purpose

Monitor entire platform.

Track

CPU

RAM

Latency

API Errors

Broker Status

AI Status

Database

Redis

WebSocket

Background Jobs

Notification Queue

Payments

Alerts

---

# Rules Engine

Purpose

Centralize business rules.

Example

Morning Report

↓

Market Open?

↓

Generate

↓

Premium User?

↓

Include Advanced Section

↓

Send

Rules should never be hardcoded inside services.

---

# Scheduler Engine

Purpose

Run recurring jobs.

Examples

05:00

Global Markets

05:15

News

05:30

Gift Nifty

06:00

Market Scan

06:30

Morning Report

07:00

Notifications

09:15

Market Open

15:30

Closing Report

20:00

Portfolio Review

Every job configurable.

---

# Event System

Everything important emits an event.

Examples

Price Updated

News Published

Trade Executed

Portfolio Updated

Morning Report Ready

Notification Sent

Broker Connected

Payment Received

AI Finished Analysis

Events allow engines to remain independent.

---

# Engine Communication

Example

Market Engine

↓

Price Updated Event

↓

Portfolio Engine

↓

Portfolio Updated Event

↓

AI Engine

↓

Recommendation Event

↓

Notification Engine

↓

User Alert

No engine directly calls another engine whenever possible.

Prefer events.

---

# Caching Strategy

Cache

Live Prices

News

Morning Reports

Sector Data

Historical Data

AI Responses (where safe)

User Preferences

Use Redis.

---

# Failover Strategy

Market data failover is owned by the Source Manager (see MARKET_DATA_ARCHITECTURE.md):

If the active market data provider fails

↓

Source Manager falls back to the next provider in the priority list
(Broker WebSocket → Licensed Exchange Feed → Yahoo Finance)

↓

If no provider is available, the UI shows last cached data,
clearly timestamped, with a single calm banner.

Never expose internal provider errors to users.

If Claude fails

↓

Gemini

If Broker API fails

↓

Retry

↓

Queue

↓

Notify User

The platform should degrade gracefully.

---

# Long-Term Architecture Goal

Every engine should be independently scalable.

Future:

Market Engine

↓

Separate Service

AI Engine

↓

Separate Service

Notification Engine

↓

Separate Service

Broker Engine

↓

Separate Service

Without changing application architecture.

The platform should evolve naturally from a modular monolith into service-oriented components if future growth requires it.

---

# End of Part 3

---

# Part 4 — Data Layer & Database Architecture

Version: 1.0

---

# Overview

The Data Layer is responsible for storing, organizing, protecting, and serving every piece of information inside StockAssist AI.

The database architecture must support:

- Millions of market records
- Thousands of concurrent users
- Real-time updates
- AI memory
- Broker synchronization
- Portfolio history
- Audit logs
- Analytics
- Future international markets

The system must remain scalable for the next 10+ years.

---

# Database Philosophy

Database design must prioritize:

Consistency

Scalability

Reliability

Security

Performance

Future Expansion

Every collection must have a clear responsibility.

Avoid duplicated data whenever possible.

Normalize important relationships while denormalizing high-frequency read models where performance benefits justify it.

---

# Database Stack

Primary Database

MongoDB Atlas

Cache Layer

Redis

Future

Vector Database

(Pinecone / Weaviate)

Object Storage

AWS S3 / Cloudflare R2

Search Engine (Future)

OpenSearch / Elasticsearch

---

# High-Level Data Architecture

```

                MongoDB Atlas

                       │

────────────────────────────────────────────

Users

Portfolios

Trades

Orders

Watchlists

Strategies

AI Memory

Notifications

Subscriptions

Payments

Reports

Logs

Analytics

Admin

Settings

────────────────────────────────────────────

                       │

                    Redis

                       │

Market Cache

AI Cache

Session Cache

Rate Limits

Temporary Jobs

Live Prices

---

# Collection Naming Convention

Every collection uses lowercase plural names.

Examples

users

portfolios

holdings

orders

trades

watchlists

notifications

subscriptions

payments

marketdata

news

reports

auditlogs

aiconversations

settings

---

# User Collection

Purpose

Store registered users.

Fields

_id

name

email

phone

avatar

passwordHash

role

plan

status

emailVerified

brokerConnected

createdAt

updatedAt

lastLogin

preferences

securitySettings

Indexes

email

role

plan

status

---

# User Preferences

Contains

Theme

Language

Timezone

Currency

Notification Settings

Default Broker

Risk Profile

Investment Style

Favorite Sectors

Favorite Stocks

Dashboard Layout

AI Preferences

---

# Portfolio Collection

Purpose

Maintain user portfolio metadata.

Fields

userId

totalValue

cashBalance

profitLoss

riskScore

diversificationScore

broker

lastSynced

createdAt

updatedAt

---

# Holdings Collection

Purpose

Store every stock held by the user.

Fields

portfolioId

symbol

exchange

quantity

averagePrice

currentPrice

marketValue

profitLoss

sector

industry

dividendYield

allocation

updatedAt

---

# Orders Collection

Stores every broker order.

Fields

userId

broker

symbol

orderType

quantity

price

status

exchangeOrderId

placedAt

executedAt

updatedAt

---

# Trades Collection

Stores completed trades.

Fields

userId

symbol

entry

exit

target

stopLoss

quantity

broker

strategy

notes

emotion

result

createdAt

updatedAt

---

# Watchlists Collection

Purpose

Store user watchlists.

Fields

userId

name

stocks

createdAt

updatedAt

Support multiple watchlists.

---

# Strategies Collection

Stores AI-generated and user-created strategies.

Fields

userId

strategyName

description

rules

timeframe

risk

backtestId

createdAt

updatedAt

Future

Strategy Marketplace

---

# AI Conversations

Purpose

Maintain AI chat history.

Fields

conversationId

userId

messages

summary

context

model

tokens

cost

createdAt

updatedAt

Conversation history should support long-term context.

---

# AI Memory

Purpose

Store persistent AI memory.

Categories

User Profile

Investment Goals

Risk Appetite

Learning Progress

Favorite Stocks

Trading Style

Behavior Patterns

Portfolio Evolution

Lessons Learned

This memory should evolve over time.

---

# Morning Reports

Store generated reports.

Fields

date

marketSummary

newsSummary

globalMarkets

topPicks

sectorAnalysis

riskWarnings

generatedBy

createdAt

Reports should be reusable.

---

# Notifications Collection

Stores every notification.

Fields

userId

title

message

type

priority

read

sentAt

clickedAt

status

Supports Browser, Email, Push.

---

# News Collection

Purpose

Cache processed news.

Fields

headline

source

url

summary

sentiment

affectedCompanies

sector

publishedAt

importance

AI consumes processed news instead of raw feeds whenever possible.

---

# Market Data Collection

Historical market data.

Fields

symbol

exchange

interval

open

high

low

close

volume

timestamp

High-frequency data should eventually move to a specialized time-series database if required.

---

# Subscription Collection

Stores subscription status.

Fields

userId

plan

status

renewalDate

paymentProvider

billingCycle

usageLimits

credits

---

# Payments Collection

Purpose

Store payment history.

Fields

userId

provider

amount

currency

status

invoiceId

transactionId

createdAt

Never store sensitive payment information directly.

---

# Broker Accounts

Purpose

Store connected broker metadata.

Fields

userId

broker

accountId

accessTokenReference

refreshTokenReference

connected

lastSync

Never store broker credentials in plaintext.

Encrypt sensitive values.

---

# Settings Collection

Application configuration.

Contains

Feature Flags

Market Hours

AI Configuration

Notification Defaults

Supported Brokers

Supported Markets

Admin Controls

---

# Admin Collection

Stores administrator preferences.

Permissions

Audit Preferences

Dashboard Layout

Saved Reports

Announcements

---

# Audit Logs

Every critical action is recorded.

Examples

Login

Logout

Password Change

Broker Connected

Trade Placed

Payment

Admin Action

Plan Upgrade

API Failure

AI Failure

Audit logs must be immutable.

---

# System Logs

Separate from Audit Logs.

Contains

Application Errors

Warnings

Performance

Latency

Database Issues

External API Failures

Crash Reports

---

# Analytics Collections

Daily Analytics

Weekly Analytics

Monthly Analytics

Revenue

User Growth

Retention

Feature Usage

AI Usage

Broker Usage

Market Activity

Reports Generated

Notification Delivery

---

# Redis Layer

Redis should never be treated as permanent storage.

Purpose

Session Storage

Live Prices

Rate Limiting

WebSocket State

AI Cache

Temporary Reports

Background Jobs

Locks

Distributed Cache

---

# Redis Keys

Examples

market:RELIANCE

portfolio:userId

watchlist:userId

session:userId

news:today

morningreport:2026-07-06

notifications:userId

---

# Indexing Strategy

Index:

Email

User ID

Symbol

Broker

Created Date

Trade Date

Notification Status

Plan

Conversation ID

Avoid unnecessary indexes.

Review slow queries regularly.

---

# Data Retention

Market Cache

24 Hours

AI Cache

12 Hours

Notifications

2 Years

Trades

Permanent

Audit Logs

Permanent

AI Conversations

Configurable

Analytics

Permanent

---

# Backup Strategy

MongoDB Snapshots

Daily

Weekly

Monthly

Redis

Optional

Critical configuration should be version controlled.

---

# Encryption

Encrypt:

Passwords

API Keys

Broker Tokens

Refresh Tokens

Sensitive Settings

Use strong industry-standard encryption.

---

# Future Data Layer

Support

International Markets

Crypto

Forex

Options

Futures

Mutual Funds

ETFs

Bonds

Alternative Assets

Without redesigning the database.

---

# Data Quality Rules

Every document should include:

createdAt

updatedAt

createdBy (where applicable)

version (optional for migrations)

deletedAt (soft delete where appropriate)

Validation must occur before database writes.

---

# Long-Term Vision

The database should evolve into a highly scalable financial data platform capable of serving:

Millions of market records

Billions of historical candles

Thousands of concurrent traders

Multiple AI models

Multiple broker integrations

Global markets

Without requiring a redesign.

The Data Layer should become one of the strongest foundations of StockAssist AI.

---

# End of Part 4

---

# Part 5 — Event-Driven Architecture

Version: 1.0

---

# Overview

StockAssist AI is designed as an Event-Driven Platform.

Instead of every service calling another service directly, important actions generate events.

These events are published to the Event Bus.

Other services subscribe to those events and react independently.

This creates a loosely coupled, scalable, and resilient architecture.

---

# Why Event-Driven?

Traditional applications:

User Action

↓

Controller

↓

Service A

↓

Service B

↓

Service C

↓

Service D

Every service depends on another.

Problems:

• Tight coupling
• Difficult testing
• Hard debugging
• Slow scaling
• Difficult AI automation

---

StockAssist AI

User Action

↓

Event

↓

Event Bus

↓

Interested Services

↓

Independent Processing

Benefits:

Scalable

Reliable

Independent

Easy Monitoring

AI Friendly

Real-Time

---

# Event Flow

Example:

User buys Reliance

↓

OrderPlaced Event

↓

Broker Engine

Portfolio Engine

Analytics Engine

Notification Engine

Trade Monitor

AI Engine

Audit Logs

Everything updates independently.

---

# Event Bus

The Event Bus is the communication backbone.

Responsibilities:

Publish Events

Subscribe Events

Retry Failed Events

Store Events

Track Processing

Monitor Performance

Future Technologies:

Redis Streams

RabbitMQ

Kafka

NATS

Initial Version:

Node.js EventEmitter + BullMQ

---

# Event Naming Convention

Every event uses the format:

```

domain.action

```

Examples:

```

user.created

user.updated

user.deleted

broker.connected

broker.disconnected

trade.created

trade.updated

trade.closed

portfolio.updated

market.price.updated

news.published

notification.sent

subscription.upgraded

payment.completed

morningreport.generated

ai.analysis.completed

system.error

```

Use lowercase.

Use dots.

Never use spaces.

---

# Event Structure

Every event follows one schema.

```

{
"id": "evt_001",
"type": "trade.created",
"timestamp": "...",
"source": "Trading Engine",
"userId": "...",
"payload": {},
"metadata": {}
}

```

Every event is immutable.

Never modify historical events.

---

# Event Store

Purpose

Persist every important business event.

Collections

events

Event Fields

eventId

eventType

aggregateId

aggregateType

payload

metadata

createdAt

processedAt

status

source

version

Events should never be deleted.

---

# Event Categories

User Events

user.created

user.updated

user.login

user.logout

user.deleted

---

Authentication Events

login.success

login.failed

password.changed

token.refreshed

session.expired

---

Broker Events

broker.connected

broker.sync.started

broker.sync.completed

broker.sync.failed

order.placed

order.executed

order.cancelled

---

Portfolio Events

portfolio.updated

holding.added

holding.removed

allocation.changed

risk.updated

---

Market Events

market.open

market.close

price.updated

sector.updated

volume.spike

breakout.detected

support.broken

resistance.broken

---

News Events

news.received

news.analyzed

news.highimpact

news.marketmoving

---

AI Events

ai.started

ai.finished

ai.failed

ai.memory.updated

ai.report.generated

ai.recommendation.created

ai.reflection.completed

ai.debate.completed

---

Notification Events

notification.created

notification.sent

notification.failed

notification.clicked

notification.read

---

Subscription Events

subscription.started

subscription.upgraded

subscription.expired

subscription.cancelled

credits.used

credits.recharged

---

Payment Events

payment.created

payment.completed

payment.failed

refund.completed

invoice.generated

---

Admin Events

admin.login

feature.enabled

feature.disabled

maintenance.started

maintenance.completed

---

# Event Processing

Each event follows:

Publish

↓

Queue

↓

Worker

↓

Business Logic

↓

Store Result

↓

Log

↓

Notify Subscribers

No blocking.

Everything asynchronous whenever possible.

---

# Worker Architecture

Workers:

Market Worker

Portfolio Worker

Trade Worker

Notification Worker

News Worker

AI Worker

Payment Worker

Subscription Worker

Analytics Worker

Audit Worker

Every worker should run independently.

---

# Retry Policy

If processing fails:

Retry 1

↓

Retry 2

↓

Retry 3

↓

Dead Letter Queue

↓

Notify Admin

Never lose events silently.

---

# Dead Letter Queue

Purpose

Store failed events.

Admin can:

View

Retry

Delete

Inspect

Every failed event remains available.

---

# Event Monitoring

Dashboard

Events Per Minute

Failed Events

Average Processing Time

Queue Length

Worker Health

Retry Count

Dead Letter Events

Everything visualized.

---

# Event Replay

Purpose

Replay historical events.

Example:

Replay

portfolio.updated

for

User X

Rebuild Portfolio

Useful for:

Bug Fixes

Recovery

Analytics

Testing

AI Training

---

# Event Versioning

Events may evolve.

Every event includes:

version

Example

trade.created.v1

trade.created.v2

Never break existing consumers.

---

# Audit vs Event Store

Audit Logs

Human readable.

Security.

Compliance.

Event Store

Machine readable.

Business history.

Replay.

Analytics.

Keep both.

---

# CQRS (Future)

Future Architecture

Commands

↓

Write Database

↓

Events

↓

Read Models

Read Database

Separate read and write workloads.

Not required initially but architecture should allow future adoption.

---

# AI Integration

Every important event is also visible to SAI.

Examples

Trade Created

↓

Trade Monitor Agent

Portfolio Updated

↓

Portfolio Agent

News Published

↓

News Agent

Morning Report Generated

↓

Notification Agent

The AI becomes event-driven instead of polling.

---

# Real-Time Updates

Events automatically update:

Dashboard

Portfolio

Trade Monitor

Watchlist

News

Scanner

AI Activity

Morning Report

Without page refresh.

---

# Security

Never publish sensitive information.

Encrypt payloads when required.

Validate event schema.

Authenticate publishers.

Authorize subscribers.

Log every event.

---

# Long-Term Vision

The Event System becomes the nervous system of StockAssist AI.

Every user action, market update, AI decision, broker update, payment, and notification flows through this architecture.

This enables:

Real-Time AI

Live Portfolio Monitoring

Automatic Reports

Advanced Analytics

High Scalability

Future Microservices

Complete Auditability

The platform evolves from a traditional web application into a real-time financial operating system.

---

# End of Part 5


---

# Database Collections

The platform uses MongoDB as the primary database.

Every collection has a single responsibility.

Collections should remain independent whenever possible.

---

# part 6


## Users

Purpose

Store user account information.

Fields

- _id
- name
- email
- phone
- avatar
- passwordHash
- role
- subscriptionPlan
- status
- preferences
- createdAt
- updatedAt
- lastLogin

Indexes

- email
- subscriptionPlan
- status

---

## User Preferences

Purpose

Store personalized user settings.

Fields

- userId
- theme
- language
- timezone
- currency
- notificationSettings
- dashboardLayout
- AIPreferences

---

## Portfolios

Purpose

Maintain synchronized user portfolios.

Fields

- userId
- brokerId
- holdings
- cashBalance
- totalValue
- investedAmount
- unrealizedPnL
- realizedPnL
- allocation
- riskScore
- updatedAt

Indexes

- userId
- brokerId

---

## Holdings

Purpose

Store individual stock holdings.

Fields

- portfolioId
- symbol
- exchange
- quantity
- averagePrice
- currentPrice
- marketValue
- profitLoss
- allocation
- sector
- updatedAt

Indexes

- portfolioId
- symbol

---

## Orders

Purpose

Track broker orders.

Fields

- userId
- brokerId
- orderId
- symbol
- transactionType
- quantity
- price
- orderType
- product
- status
- timestamps

Indexes

- userId
- orderId
- status

---

## Trades

Purpose

Maintain completed trade history.

Fields

- userId
- orderId
- symbol
- entryPrice
- exitPrice
- quantity
- pnl
- holdingPeriod
- notes
- AIReview
- createdAt

Indexes

- userId
- symbol

---

## Watchlists

Purpose

Maintain personalized watchlists.

Fields

- userId
- name
- stocks
- color
- description
- alerts
- updatedAt

---

## Strategies

Purpose

Store AI-generated trading strategies.

Fields

- userId
- strategyName
- description
- rules
- indicators
- backtestResults
- paperTradingStatus
- createdAt

---

## AI Conversations

Purpose

Store conversation history.

Fields

- conversationId
- userId
- messages
- model
- tokenUsage
- cost
- summary
- createdAt

Future

Long conversations may be summarized automatically.

---

## AI Memory

Purpose

Persistent AI memory.

Stores

- User Preferences
- Learning Progress
- Trading Style
- Risk Appetite
- Favorite Stocks
- Favorite Sectors
- Historical Decisions

This collection should evolve independently from chat history.

---

## Morning Reports

Purpose

Store generated reports.

Fields

- reportDate
- userId
- reportData
- marketSummary
- opportunities
- generatedBy
- deliveredAt

---

## Notifications

Purpose

Track every notification.

Fields

- userId
- type
- priority
- title
- message
- deliveryChannel
- status
- read
- timestamps

---

## News

Purpose

Store normalized news.

Fields

- headline
- source
- summary
- sentiment
- affectedStocks
- affectedSectors
- publishedAt

---

## AI Recommendations

Purpose

Maintain AI recommendations.

Fields

- userId
- symbol
- recommendation
- confidence
- reasoning
- supportingData
- createdAt
- expiresAt

---

## Learning Progress

Purpose

Track educational journey.

Fields

- userId
- completedLessons
- quizzes
- achievements
- tradingMistakes
- AIRecommendations

---

## Subscription

Purpose

Manage subscriptions.

Fields

- userId
- currentPlan
- billingCycle
- renewalDate
- paymentProvider
- invoices
- usage

---

## Payments

Purpose

Track payment history.

Fields

- userId
- invoiceId
- provider
- amount
- currency
- status
- timestamps

---

## Broker Accounts

Purpose

Maintain connected broker information.

Fields

- userId
- brokerName
- encryptedTokens
- refreshToken
- status
- connectedAt

Never store secrets unencrypted.

---

## API Usage

Purpose

Track external API usage.

Fields

- provider
- requests
- failures
- latency
- estimatedCost
- dailyUsage
- monthlyUsage

---

## Audit Logs

Purpose

Record sensitive operations.

Events

- Login
- Logout
- Password Change
- Broker Connection
- Subscription Change
- Payment
- Admin Action
- Permission Update

Audit logs are immutable.

---

## System Logs

Purpose

Store application logs.

Levels

- INFO
- WARNING
- ERROR
- CRITICAL

Support full-text search.

---

# Database Relationships

User

↓

Portfolio

↓

Holdings

↓

Orders

↓

Trades

↓

Trade Journal

↓

AI Review

Every relationship should use ObjectId references while avoiding unnecessary joins.

---

# MongoDB Indexing Strategy

Every frequently queried field should be indexed.

Examples

Users

- email
- role

Trades

- userId
- symbol

Portfolio

- userId

Orders

- status

Notifications

- userId
- read

News

- publishedAt
- affectedStocks

Indexes should be monitored continuously.

---

# Redis Architecture

Redis is responsible for temporary high-speed storage.

Use Redis for:

- Live Prices
- Sessions
- JWT Blacklist
- API Cache
- Morning Reports
- Notification Queue
- AI Response Cache
- Broker Cache
- Rate Limiting
- Scheduler State

Redis should never become the permanent source of truth.

---

# Cache Strategy

Short TTL

- Live Prices
- Quotes
- News Headlines

Medium TTL

- AI Reports
- Dashboard Data
- Portfolio Summary

Long TTL

- User Preferences
- Learning Progress

Invalidate cache immediately after data changes.

---

# Data Retention Policy

Keep

Audit Logs

7 Years

Trades

Forever

Orders

Forever

AI Conversations

2 Years

Notifications

1 Year

Logs

180 Days

Analytics

5 Years

Retention policies should be configurable.

---

# Backup Strategy

Daily Incremental Backup

Weekly Full Backup

Monthly Archive

Quarterly Disaster Recovery Test

Backups should be encrypted and stored in multiple regions.

---

# Disaster Recovery

Recovery Objectives

RPO

< 15 Minutes

RTO

< 1 Hour

Critical services should recover automatically whenever possible.

---

# Data Security

All sensitive fields must be encrypted.

Examples

- Broker Tokens
- API Credentials
- Payment References
- Personal Information

Passwords must always use Argon2 or bcrypt hashing.

---

# Long-Term Data Vision

The data architecture should support:

- Multiple Countries
- Multiple Exchanges
- Multiple Currencies
- AI Memory Expansion
- Enterprise Workspaces
- Institutional Accounts
- Future Data Warehouse
- Vector Search

Without requiring major schema redesign.

---

# End of Part 6