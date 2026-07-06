# StockAssist AI
## Documentation Index

Version: 1.0

Status: Active Development

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

These explain how the platform is built.

SYSTEM_ARCHITECTURE.md

DATABASE.md

API_REFERENCE.md

BROKER_INTEGRATION.md

MARKET_ENGINE.md

SECURITY.md

DEPLOYMENT.md

---

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

# Phase-Based Reading Guide

## Project Audit

Read

CLAUDE.md

PROJECT.md

PRODUCT_REQUIREMENTS.md

SYSTEM_ARCHITECTURE.md

ROADMAP.md

TASKS.md

DECISIONS.md

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

PRODUCT_REQUIREMENTS.md

API_REFERENCE.md

TASKS.md

---

## Broker Integration

Read

BROKER_INTEGRATION.md

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

Read Documentation

↓

Understand Requirements

↓

Analyze Existing Code

↓

Create Implementation Plan

↓

Implement Feature

↓

Write Tests

↓

Validate Performance

↓

Update Documentation

↓

Update TASKS.md

↓

Commit Changes

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

---

# Long-Term Vision

This documentation system should allow any engineer or AI assistant to understand, maintain, and extend StockAssist AI without relying on previous chat history.

The documentation should remain the single source of truth throughout the lifetime of the project.

---

# End of Documentation Index