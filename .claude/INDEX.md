# StockAssist AI
## Documentation Index

Version: 1.2

Status: Feature Freeze — Production Hardening (PH1–PH3)

---

# Purpose

This document is the entry point for every AI assistant and developer working on StockAssist AI.

Before making any changes to the codebase, read this file first.

This file explains:

- Project vision
- Documentation structure
- Which documents to read
- Development workflow
- Implementation order
- Documentation priority

Never begin implementation without consulting this index.

---

# Project Overview

Project Name

StockAssist AI

Type

AI-Powered Stock Market Analysis & Trading Platform

Architecture

Full Stack MERN

AI First

Real-Time

Production Ready

Primary AI Models

Claude

Gemini

Primary Markets

NSE

BSE

Future

US Markets

Crypto

Forex

---

# Documentation Structure

The project documentation is divided into logical categories.

1. Core Documents

These explain what StockAssist AI is.

Read these first.

CLAUDE.md

PROJECT.md

PRODUCT_REQUIREMENTS.md

ROADMAP.md

---

2. Architecture Documents

SYSTEM_ARCHITECTURE.md

REALTIME_SYSTEM.md

MARKET_DATA_ARCHITECTURE.md

DATABASE.md

API_REFERENCE.md

BROKER_INTEGRATION.md

MARKET_ENGINE.md

SECURITY.md

DEPLOYMENT.md

3. AI Documents

These define AI behavior.

AI_AGENT_SYSTEM.md

PROMPTS.md

DECISIONS.md

---

4. Product Documents

These explain user-facing features.

FEATURES.md

USER_FLOWS.md

SUBSCRIPTIONS.md

PAYMENT_SYSTEM.md

ADMIN_PORTAL.md

---

5. UI Documents

These define the design system.

UI_GUIDELINES.md

DESIGN_SYSTEM.md

---

6. Engineering Documents

These define implementation standards.

CODING_STANDARDS.md

TESTING.md

TASKS.md

---

7. Production Hardening Documents

These govern the current phase of work. Mandatory reading until v1.0 launch.

PRODUCTION_HARDENING.md

PRODUCTION_ROADMAP.md

CHANGELOG.md

---

# Reading Order

When starting a new conversation:

Read

1. CLAUDE.md

2. PROJECT.md

3. This INDEX.md

Then read only the documents required for the current task.

Avoid loading unnecessary documents.

---

# Documentation Map

CLAUDE.md

Purpose

Master instructions for Claude.

Read When

Always.

---

PROJECT.md

Purpose

Complete product overview.

Read When

Always.

---

PRODUCT_REQUIREMENTS.md

Purpose

Business requirements.

Read When

Building features.

---

SYSTEM_ARCHITECTURE.md

Purpose

System design.

Read When

Backend

Architecture

Infrastructure

---

DATABASE.md

Purpose

MongoDB schema.

Read When

Working with data.

---

API_REFERENCE.md

Purpose

REST APIs.

Read When

Frontend

Backend

API Integration

---

BROKER_INTEGRATION.md

Purpose

Broker connections.

Read When

Trading

Portfolio

Orders

---

MARKET_ENGINE.md

Purpose

Live market engine.

Read When

Scanner

Charts

Market

News

---

MARKET_DATA_ARCHITECTURE.md

Purpose

Authoritative source for all market data provider behavior.

Defines:

• Market Gateway

• Source Manager

• Provider Adapters (Yahoo, broker WebSockets, future licensed feeds)

• Provider priority and automatic switching

• Normalized market event model

• Failover and recovery

• Market data tiers (Free / Broker-Connected / Premium)

Read When

Market data

Providers

Broker feeds

Scanner

Market Engine

Real-Time Features

This document is mandatory for any work that touches how market data enters the platform.

---

REALTIME_SYSTEM.md

Purpose

Defines how StockAssist AI behaves as a real-time event-driven platform.

It explains:

• Event Driven Architecture

• Redis Pub/Sub

• Socket.IO

• Live Dashboard

• Live Scanner

• Live Portfolio

• Live Trade Monitor

• AI Activity Timeline

• Real-Time Notifications

• GSAP Animations

• Connection Management

Read When

Dashboard

Market Engine

Scanner

Portfolio

Trading

Broker Integration

AI Workspace

Notifications

Frontend Performance

Real-Time Features

This document is mandatory for every feature that updates continuously.

AI_AGENT_SYSTEM.md

Purpose

Multi-agent architecture.

Read When

Building AI features.

---

PROMPTS.md

Purpose

AI prompt library.

Read When

Changing AI behavior.

---

FEATURES.md

Purpose

Product features.

Read When

Creating pages.

---

USER_FLOWS.md

Purpose

User journeys.

Read When

Designing UX.

---

UI_GUIDELINES.md

Purpose

UI rules.

Read When

Frontend.

---

DESIGN_SYSTEM.md

Purpose

Typography

Spacing

Glass Effects

Colors

Components

Read When

Frontend.

---

ADMIN_PORTAL.md

Purpose

Admin system.

Read When

Building admin.

---

SUBSCRIPTIONS.md

Purpose

Plans

Credits

Limits

Read When

Billing.

---

PAYMENT_SYSTEM.md

Purpose

Payments.

Read When

Checkout

Billing

Credits

---

SECURITY.md

Purpose

Security.

Read When

Authentication

Payments

Broker

AI

Always recommended.

---

TESTING.md

Purpose

Quality Assurance.

Read When

Before deployment.

---

DEPLOYMENT.md

Purpose

Deployment.

Read When

Production.

---

ROADMAP.md

Purpose

Future planning.

Read When

Planning new work.

---

TASKS.md

Purpose

Master backlog.

Read When

Every implementation.

Update after completing work.

---

CODING_STANDARDS.md

Purpose

Coding conventions.

Read When

Always while coding.

---

DECISIONS.md

Purpose

Architecture decisions.

Read When

Changing architecture.

---

PRODUCTION_HARDENING.md

Purpose

Master architecture document for the Production Hardening program: audit baseline, risk matrix, readiness score, security/infrastructure/deployment/testing/monitoring/recovery strategies, certification checklists, and the Definition of Production Ready.

Read When

Any PH1/PH2/PH3 work.

Any security, deployment, or launch-related task.

Mandatory until v1.0 launch.

---

PRODUCTION_ROADMAP.md

Purpose

Sprint-level plan for the three Production Hardening phases (PH1 Security, PH2 Infrastructure & DevOps, PH3 Quality Assurance) — 36 sprints with objectives, acceptance criteria, validation, rollback, and the implementation dependency graph.

Read When

Starting any PH sprint.

Planning hardening work.

---

CHANGELOG.md

Purpose

Documentation and release change history.

Read When

Releasing.

Bumping documentation versions.

---

# Phase-Based Reading Guide

## Production Hardening (Current Phase)

PRODUCTION_HARDENING.md

PRODUCTION_ROADMAP.md

SECURITY.md

DEPLOYMENT.md

TESTING.md

TASKS.md

Objective

Take the feature-complete MVP to a certified production launch. No new product features until Production Certification (PH3.12).

---

## Project Audit

PROJECT.md

PRODUCT_REQUIREMENTS.md

REALTIME_SYSTEM.md

FEATURES.md

UI_GUIDELINES.md

DESIGN_SYSTEM.md

API_REFERENCE.md

CODING_STANDARDS.md

TASKS.md

---

## Dashboard

Read

PROJECT.md

PRODUCT_REQUIREMENTS.md

FEATURES.md

UI_GUIDELINES.md

DESIGN_SYSTEM.md

API_REFERENCE.md

CODING_STANDARDS.md

TASKS.md

---

## Market Engine

Read

MARKET_ENGINE.md

MARKET_DATA_ARCHITECTURE.md

REALTIME_SYSTEM.md

DATABASE.md

SYSTEM_ARCHITECTURE.md

API_REFERENCE.md

AI_AGENT_SYSTEM.md

TASKS.md

---

## Stock Details

Read

MARKET_ENGINE.md

API_REFERENCE.md

DATABASE.md

FEATURES.md

UI_GUIDELINES.md

---

## Portfolio

Read

DATABASE.md

BROKER_INTEGRATION.md

API_REFERENCE.md

AI_AGENT_SYSTEM.md

FEATURES.md

---

## AI Workspace

Read

AI_AGENT_SYSTEM.md

PROMPTS.md

REALTIME_SYSTEM.md

PRODUCT_REQUIREMENTS.md

API_REFERENCE.md

TASKS.md

---

## Broker Integration

Read

BROKER_INTEGRATION.md

MARKET_DATA_ARCHITECTURE.md

REALTIME_SYSTEM.md

DATABASE.md

SECURITY.md

API_REFERENCE.md

SYSTEM_ARCHITECTURE.md

---

## Admin Portal

Read

ADMIN_PORTAL.md

DATABASE.md

SUBSCRIPTIONS.md

PAYMENT_SYSTEM.md

SECURITY.md

---

## Payments

Read

PAYMENT_SYSTEM.md

SUBSCRIPTIONS.md

DATABASE.md

SECURITY.md

API_REFERENCE.md

---

## Real-Time Infrastructure

Read

REALTIME_SYSTEM.md

MARKET_DATA_ARCHITECTURE.md

SYSTEM_ARCHITECTURE.md

MARKET_ENGINE.md

BROKER_INTEGRATION.md

DATABASE.md

API_REFERENCE.md

AI_AGENT_SYSTEM.md

CODING_STANDARDS.md

TASKS.md

Objective

Transform StockAssist AI into a fully event-driven platform.

No polling.

Everything must update automatically using Redis Pub/Sub and Socket.IO.

All UI updates should animate and only update the affected components.

## Testing

Read

TESTING.md

CODING_STANDARDS.md

SECURITY.md

DEPLOYMENT.md

---

## Deployment

Read

DEPLOYMENT.md

TESTING.md

SECURITY.md

ROADMAP.md

---

# Development Workflow

Every implementation should follow this workflow.

Read INDEX.md

↓

Read Required Documentation

↓

Analyze Existing Code

↓

Compare Code vs Documentation

↓

Identify Missing Features

↓

Create Implementation Plan

↓

Implement

↓

Test

↓

Verify Real-Time Behaviour

↓

Update Documentation

↓

Update TASKS.md

↓

Commit

---

# Documentation Priority

If documentation conflicts:

Priority Order

1. CLAUDE.md

2. INDEX.md

3. PROJECT.md

4. PRODUCT_REQUIREMENTS.md

5. SYSTEM_ARCHITECTURE.md

6. API_REFERENCE.md

7. DATABASE.md

8. SECURITY.md

9. Remaining Documentation

Never ignore higher-priority documentation.

---

# Rules for Claude

Before coding:

Read the required documentation.

Understand existing implementation.

Avoid duplicate code.

Preserve architecture.

Never redesign completed UI unless requested.

Never introduce mock data into production features.

Never remove features without approval.

Follow coding standards.

Update TASKS.md after implementation.

Keep documentation synchronized with the codebase.

Always preserve the event-driven architecture.

Never introduce unnecessary polling.

Always prefer Socket.IO for real-time updates.

Only update affected components instead of re-rendering entire pages.

All market data should flow through the Market Gateway and Market Engine. Never talk to a market data provider directly — see MARKET_DATA_ARCHITECTURE.md, the authoritative document for all provider behavior.

Every new feature must integrate with the Real-Time System if live updates are required.

If implementing AI, Broker, Portfolio, Scanner, Dashboard, or Notifications, read REALTIME_SYSTEM.md before coding.

---

# Documentation Changelog

## Version 1.2 — 2026-07-17

Major Changes

- MVP declared feature complete (Phase 1 Sprints 1–12; Phase 2 Releases R1–R9). Feature freeze in effect.
- Introduced the Production Hardening program: PH1 (Security), PH2 (Infrastructure & DevOps), PH3 (Quality Assurance), 12 sprints each.
- Created PRODUCTION_HARDENING.md — master hardening architecture document (audit baseline, risk matrix, readiness score 4.2/10, strategies, certification checklists, Definition of Production Ready).
- Created PRODUCTION_ROADMAP.md — 36-sprint implementation roadmap with sequencing and dependency graph.
- Created CHANGELOG.md — standalone change history.
- Updated ROADMAP.md, TASKS.md, DECISIONS.md to reflect the hardening interlude before product Phases 3–9.
- Recorded ADR-027 (Feature Freeze & Production Hardening Program) in DECISIONS.md.
- Baseline input: PRODUCTION_READINESS_REPORT.md (Sprint 12 audit, verdict NOT READY).

## Version 1.1 — 2026-07-16

Major Changes

- Introduced MARKET_DATA_ARCHITECTURE.md.
- Migrated from Yahoo-centric architecture to provider-independent architecture.
- Added Market Gateway and Source Manager concepts.
- Defined provider priority and failover strategy.
- Separated Connected Broker experience from Premium AI features.
- Updated all affected documentation for consistency.

Recorded as ADR-026 in DECISIONS.md. MARKET_DATA_ARCHITECTURE.md is the authoritative source for all market data provider behavior.

## Version 1.0

- Initial documentation system.

---

# Long-Term Vision

This documentation system should allow any engineer or AI assistant to understand, maintain, and extend StockAssist AI without relying on previous chat history.

The documentation should remain the single source of truth throughout the lifetime of the project.

---

# Claude Startup Workflow

Whenever Claude starts a new task:

1. Read INDEX.md.
2. Identify the current sprint or feature.
3. Load only the relevant documentation listed in INDEX.md.
4. Analyze the existing implementation before writing code.
5. Compare implementation with documentation.
6. Explain the implementation plan.
7. Implement the feature.
8. Verify functionality.
9. Verify real-time behavior if applicable.
10. Update TASKS.md and any affected documentation.
11. Summarize completed work and remaining tasks.

This workflow is mandatory for every implementation.

# End of Documentation Index