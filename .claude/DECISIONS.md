# StockAssist AI
## Architecture & Product Decisions

Version: 1.2

Status: Active Development

---

# Purpose

This document records all major engineering, architecture, infrastructure, AI, product, and business decisions made during the development of StockAssist AI.

Every significant decision must be documented before implementation.

The objective is to ensure future developers understand:

• Why a decision was made

• Alternatives considered

• Trade-offs

• Long-term impact

This document acts as the project's Architectural Decision Record (ADR).

---

# Decision Template

Every decision should follow this structure.

Decision ID

Date

Status

Context

Decision

Alternatives Considered

Pros

Cons

Consequences

Review Date

---

# ADR-001

Title

Frontend Framework

Status

Accepted

Context

The platform requires a scalable frontend capable of handling dashboards, charts, AI workspaces, responsive layouts, animations, and real-time updates.

Decision

Use

React

TypeScript

Vite

Tailwind CSS

shadcn/ui

Reason

Fast development

Excellent ecosystem

Type safety

Reusable components

Large community

Future scalability

Alternatives

Vue

Angular

Next.js

Rejected Because

Current architecture is SPA-focused and does not require SSR initially.

---

# ADR-002

Title

Backend Framework

Status

Accepted

Decision

Node.js

Express

TypeScript

Reason

Unified JavaScript ecosystem

Large package ecosystem

Real-time support

Easy broker integration

---

# ADR-003

Title

Database

Status

Accepted

Decision

MongoDB Atlas

Reason

Flexible schema

Excellent for evolving products

Good TypeScript support

Easy scaling

Alternatives

PostgreSQL

MySQL

Firebase

---

# ADR-004

Title

Caching

Decision

Redis

Reason

Fast

Reliable

Supports BullMQ

Excellent caching

Session storage

Rate limiting

---

# ADR-005

Title

Background Jobs

Decision

BullMQ

Redis

Reason

Reliable

Retry support

Scheduling

Independent workers

---

# ADR-006

Title

Authentication

Decision

JWT

Refresh Tokens

Reason

Stateless

Scalable

API Friendly

---

# ADR-007

Title

Broker Architecture

Decision

Broker Adapter Pattern

Reason

Support multiple brokers

Minimal code duplication

Easy future expansion

Supported Brokers

Zerodha

Upstox

Future

Angel One

Groww

Interactive Brokers

---

# ADR-008

Title

Market Data

Decision

Provider Abstraction Layer

Reason

Never tie business logic directly to one provider.

Benefits

Easy replacement

Fallback providers

Testing

Caching

Status

Extended by ADR-026 (Provider-Independent Market Data Architecture).

---

# ADR-009

Title

AI Architecture

Decision

Multi-Agent System

Reason

Single AI becomes difficult to scale.

Multiple specialized agents provide:

Better reasoning

Parallel execution

Cleaner architecture

---

# ADR-010

Title

Primary AI Models

Decision

Claude

Gemini

Reason

Claude

Reasoning

Architecture

Code

Planning

Gemini

Fast responses

Large context

Multimodal

Future

Model abstraction layer.

Never depend on one provider.

---

# ADR-011

Title

Real-Time Updates

Decision

WebSockets

Fallback

Polling

Reason

Fast updates

Lower latency

Better UX

---

# ADR-012

Title

Deployment

Decision

Frontend

Vercel

Backend

Railway

Future

AWS

Reason

Fast deployment

Low maintenance

Simple scaling

---

# ADR-013

Title

Event Driven Architecture

Decision

Publish business events.

Examples

trade.created

portfolio.updated

subscription.upgraded

Reason

Loose coupling

Scalable

Future microservices

---

# ADR-014

Title

UI Framework

Decision

Glassmorphism

Minimalism

Motion Design

Reason

Premium appearance

High readability

Modern financial UI

---

# ADR-015

Title

Design Language

Inspired By

Apple

Linear

Stripe

TradingView

Bloomberg

Reason

Trust

Professionalism

Simplicity

---

# ADR-016

Title

State Management

Decision

React Query

Context

Reason

Separate server state from UI state.

Avoid unnecessary global state.

---

# ADR-017

Title

Validation

Decision

Zod

Reason

Type-safe validation

Frontend and backend consistency

---

# ADR-018

Title

API Design

Decision

REST

Versioned APIs

/api/v1

Future

GraphQL

---

# ADR-019

Title

Documentation First

Decision

Create documentation before implementation.

Reason

AI-assisted development

Clear requirements

Better consistency

Lower technical debt

---

# ADR-020

Title

Development Workflow

Decision

Documentation

↓

Design

↓

Implementation

↓

Testing

↓

Deployment

Reason

Predictable development

Higher quality

---

# ADR-021

Title

No Mock Data in Production

Decision

Every production feature must use live APIs.

Reason

Platform trust

Accurate AI

Reliable analytics

---

# ADR-022

Title

Security First

Decision

Every feature undergoes security review.

Reason

Financial platform

Sensitive data

Broker integrations

Payments

---

# ADR-023

Title

AI Transparency

Decision

AI should explain its reasoning.

Users should understand

Why a recommendation exists

Confidence level

Supporting evidence

Risks

AI must not behave as an unexplained black box.

---

# ADR-024

Title

Subscription Model

Decision

Freemium SaaS

Free

Pro

Elite

Future Enterprise

Reason

Accessible for beginners

Sustainable business model

---

# ADR-025

Title

Admin Portal

Decision

Separate internal application.

Reason

Never expose administrative functionality to regular users.

RBAC required.

---

# ADR-026

Title

Provider-Independent Market Data Architecture

Date

2026-07-16

Decision

StockAssist AI is provider-independent. All market data enters the platform through a Market Gateway abstraction with per-provider adapters, governed by a Source Manager that selects the best provider per user.

Provider priority:

1. Connected Broker WebSocket (Zerodha, Upstox, Angel One, Fyers, Dhan)

2. Licensed Exchange Feed (future)

3. Yahoo Finance (always-available baseline)

Every provider produces one normalized market event model. The Market Engine, AI, and Frontend consume only normalized events and never know the provider — downstream provenance is limited to a source tier (streaming / delayed).

Connecting a broker automatically upgrades the user's feed to the broker's streaming WebSocket at no subscription cost — the broker already owns the user's data entitlement.

Premium never sells market data; it sells AI intelligence.

Reason

Yahoo Finance (polling) was the platform's real latency bottleneck, not the internal event-driven architecture. Depending on any single provider is a business and technical risk. Broker feeds give professional streaming data with zero data cost.

Consequences

• Adding a provider = one adapter + one normalizer + registry entry; nothing else changes.
• Never bypass the Market Gateway or Source Manager.
• Frontend and AI must never contain provider-specific logic.
• Failover is automatic and silent (broker → licensed → Yahoo → cached data with banner).

Authoritative document

MARKET_DATA_ARCHITECTURE.md

---

# ADR-027

Title

Feature Freeze & Production Hardening Program (PH1–PH3)

Date

2026-07-17

Status

Accepted

Context

The MVP is feature complete (Phase 1 Sprints 1–12; Phase 2 Releases R1–R9). The Sprint 12 Production Readiness Audit (PRODUCTION_READINESS_REPORT.md) returned a verdict of NOT READY FOR PRODUCTION: two critical authentication backdoors enabled by default, wildcard CORS with credentialed requests, insecure auth cookies, broken Docker packaging, no CI/CD, no rate limiting, fabricated admin analytics data (ADR-021 violation), a non-hermetic backend test suite, and zero frontend tests. The audit also confirmed a structural documentation/code mismatch: DEPLOYMENT.md and ADR-002 describe a Node.js + Express + TypeScript backend and Vite frontend, while the actual system is Python + FastAPI (`backend/server.py`) with a CRA/craco JavaScript frontend.

Decision

1. Immediate feature freeze. No new product features merge until Production Certification.

2. A three-phase Production Hardening program is inserted between the completed MVP and product Phases 3–9:
   • PH1 — Production Security Hardening (12 sprints)
   • PH2 — Production Infrastructure & DevOps (12 sprints)
   • PH3 — Production Quality Assurance (12 sprints)

3. Two permanent documents govern the program: PRODUCTION_HARDENING.md (strategy, risk, certification, Definition of Production Ready) and PRODUCTION_ROADMAP.md (36 sprint definitions, sequencing, dependency graph).

4. Security removals (auth backdoors, OAuth fallbacks) are permanent — never rolled back; broken flows are fixed forward.

5. The FastAPI + CRA stack is acknowledged as the as-built system of record. ADR-002 is superseded in practice; DEPLOYMENT.md and related documents will be reconciled to the actual stack in sprint PH3.10 (either documenting the FastAPI stack as final or recording an explicit migration ADR — one of the two must be chosen there).

6. Launch requires the three phase certifications (PH1.12, PH2.12, PH3.12) and a re-scored production readiness of ≥ 9.0 with no category below 8.0.

Alternatives Considered

• Fix only the six critical blockers and launch — rejected: leaves no pipeline to keep them fixed, no tests to catch regressions, and unmeasured recovery capability.

• Continue feature development in parallel with hardening — rejected: every new feature widens the attack/regression surface being certified.

Consequences

• Product Phases 3–9 in ROADMAP.md are blocked until PH3.12.
• All PH work is tracked in TASKS.md under "Production Hardening Program".
• Documentation version bumped to 1.2; standalone CHANGELOG.md introduced.
• Estimated program duration: ~5–6 calendar weeks with parallel tracks.

Review Date

At PH3.12 (Production Certification go/no-go).

---

# Pending Decisions

Future decisions to document.

International Markets

Mobile App Framework

Desktop App

Vector Database

Kubernetes

AI Model Routing

Auto Trading

Enterprise Features

White Label Platform

---

# Decision Lifecycle

Proposed

↓

Review

↓

Accepted

↓

Implemented

↓

Deprecated

↓

Archived

Every major change should create a new decision record.

---

# Review Policy

Review architecture decisions:

Every major release

Every infrastructure change

Every AI provider change

Every broker expansion

Every security update

---

# Long-Term Vision

The Decisions document becomes the institutional memory of StockAssist AI.

Years from now, every developer should be able to understand why the platform evolved the way it did, reducing confusion, preventing repeated debates, and preserving engineering knowledge.

---

# End of Decisions Documentation