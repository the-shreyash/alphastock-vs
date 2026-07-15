# StockAssist AI
## Subscription & Billing Documentation

Version: 1.0

Status: Active Development

---

# Purpose

This document defines the subscription model of StockAssist AI.

It explains:

• Plans
• Billing
• Credits
• AI Usage
• Feature Access
• Payments
• Upgrades
• Downgrades
• Enterprise Plans
• Lifetime Access

This document is the single source of truth for monetization.

---

# Subscription Philosophy

Users should experience value before paying.

The Free plan should be genuinely useful.

Premium plans should unlock advanced capabilities instead of simply removing artificial limitations.

The subscription model must be transparent.

No hidden charges.

No misleading pricing.

---

# Premium Never Sells Market Data

This is a permanent monetization rule (see MARKET_DATA_ARCHITECTURE.md).

Market data is either free (Yahoo Finance for guests/free users) or already owned by the user (their broker's streaming feed, activated automatically on broker connection).

Premium sells intelligence, automation, and productivity:

• AI Portfolio Intelligence
• Morning Report
• Advanced Scanner
• AI Coach
• Strategy Builder
• Backtesting
• Trade Journal analytics
• AI Trade Review
• Risk Engine
• Multi-Agent AI Debate
• Smart Alerts
• Automation

Why: market data is a commodity with licensing risk; AI reasoning over the user's own portfolio and context is StockAssist's moat. Live data quality must never appear as a plan differentiator.

---

# Plans

StockAssist AI has three primary plans.

Free

↓

Pro

↓

Elite

Future

Enterprise

Educational

Lifetime

Investor

Internal

---

# Free Plan

Purpose

Allow beginners to learn and explore the platform.

Features

Dashboard

Market Overview

Basic Scanner



Basic AI Chat - use gemini and claude very  basic model 

Basic Portfolio

Watchlist

News

Search

Learning Center

Limited Notifications

---

Limits

AI Requests

10 / Day

Scanner Refresh

Every 10 Minutes

Watchlists

2

Portfolio Review

Basic

Morning Report for only 3 day trial period

1 / Day

Broker Accounts

1

Trade Monitoring

No

Paper Trading

yes

Backtesting

No

---

# Pro Plan

Purpose

For active traders.

Everything in Free plus

Unlimited Dashboard

Unlimited Scanner

AI Portfolio Review

Paper Trading

Backtesting

Trade Journal

Advanced Scanner

Advanced Search

Priority AI

Portfolio Intelligence

Watchlists

Unlimited

Trade Review

Advanced Morning Report

Broker Sync

AI Monitoring

Basic

---

Limits

AI Requests

100 / Day

Broker Accounts

3

Strategies

50

Backtests

Unlimited

Paper Portfolio

Unlimited

---

# Elite Plan

Purpose

Professional traders and investors.

Everything in Pro plus

Unlimited AI

Continuous AI Monitoring

Real-Time Alerts

Advanced AI Models

Priority Queue

Unlimited Strategies

Unlimited Backtesting

Unlimited Paper Trading

Advanced Portfolio Intelligence

Trade Assistant

Risk Manager

Future Premium Features

---

Limits

AI Requests

Unlimited (Fair Usage Policy)

Broker Accounts

Unlimited

Watchlists

Unlimited

Strategies

Unlimited

Backtests

Unlimited

Morning Reports

Unlimited

---

# Enterprise Plan (Future)

Purpose

Organizations

Investment Firms

Educational Institutions

Research Teams

Features

Multi-user Workspace

Role Management

Shared Portfolios

Shared Watchlists

Team Analytics

Dedicated Support

Custom AI Models

API Access

SLA

Custom Billing

---

# Internal Plans

Purpose

Allow manual access without payment.

Types

Developer

Administrator

Investor

Beta Tester

Lifetime

Support Team

These plans are assigned manually through the Admin Portal.

---

# Feature Matrix

Authentication

Free

✓

Pro

✓

Elite

✓

---

Market Dashboard

✓

✓

✓

---

Morning Report

Basic

Advanced

Premium

---

Stock Scanner

Basic

Advanced

Premium

---

Portfolio Review

Basic

AI

Advanced AI

---

Paper Trading

—

✓

✓

---

Backtesting

—

✓

✓

---

AI Chat

Limited

100/day

Unlimited*

---

Trade Monitoring

—

Basic

Continuous

---

AI Portfolio Monitoring

—

✓

✓

---

Broker Integration

1 Broker

3 Brokers

Unlimited

---

Learning Center

✓

✓

✓

---

Admin Portal

Admin Only

---

# AI Usage System

Every AI request consumes credits.

Simple requests

↓

Low Cost

Complex analysis

↓

Medium Cost

Portfolio Review

↓

Higher Cost

Morning Report

↓

Higher Cost

Large Research

↓

Highest Cost

The platform tracks AI usage per user.

---

# AI Credits

Every account maintains:

Daily Credits

Monthly Credits

Purchased Credits

Bonus Credits

Used Credits

Remaining Credits

Credits are deducted automatically.

---

# Credit Packs

Users may purchase additional credits.

Examples

100 Credits

500 Credits

1000 Credits

5000 Credits

Purchased credits never expire unless otherwise stated.

---

# Fair Usage Policy

Elite plans are unlimited.

However,

the platform may apply reasonable protections against automated abuse or excessive usage that could impact service quality for others.

Legitimate users should not experience restrictions during normal use.

---

# Billing Cycle

Supported

Monthly

Quarterly (Future)

Yearly

Lifetime

---

# Payment Providers

Primary

Razorpay

Stripe

Future

PayPal

UPI Direct

Bank Transfer

---

# Upgrade Flow

User

↓

Pricing Page

↓

Choose Plan

↓

Checkout

↓

Payment

↓

Webhook

↓

Verify

↓

Update Subscription

↓

Refresh Session

↓

Unlock Features

↓

Success

---

# Downgrade Flow

User

↓

Manage Subscription

↓

Choose New Plan

↓

Confirm

↓

Apply At Renewal

↓

Notify User

Current benefits remain until billing period ends.

---

# Cancellation

User may cancel anytime in first 24 hours only after payment confirmations.

Cancellation

↓

Subscription Ends At Renewal

↓

No Immediate Data Loss

↓

User Can Reactivate

---

# Expired Subscription

When a subscription expires

Premium features become unavailable.

User retains:

Account

Portfolio

Trade History

Watchlists

AI Conversations

Nothing is deleted automatically.

---

# Refund Policy

Managed according to business policy and payment provider capabilities.

Admin Portal supports

Approve Refund

Reject Refund

Partial Refund

Track Refund Status

Every refund is logged.

---

# Promotions

Supported

Discount Codes

Referral Codes

Student Discounts

Seasonal Offers

Launch Campaigns

Investor Coupons

Promotions should be configurable.

---

# Referral Program (Future)

Users receive rewards for successful referrals.

Possible Rewards

AI Credits

Free Pro Days

Elite Trial

Cash Rewards

Referral analytics available in Admin Portal.

---

# Feature Gating

Every premium feature checks

Authentication

↓

Subscription

↓

Credits

↓

Permissions

↓

Access Granted

No UI-only restrictions.

Backend always validates permissions.

---

# Subscription Events

subscription.created

subscription.upgraded

subscription.downgraded

subscription.expired

subscription.cancelled

credits.used

credits.added

payment.completed

refund.completed

Events enter the Event Bus.

---

# Notifications

Users receive notifications for

Upcoming Renewal

Successful Payment

Failed Payment

Credits Low

Credits Exhausted

Subscription Expiring

Plan Upgraded

Plan Downgraded

---

# Analytics

Track

Plan Distribution

Revenue

MRR

ARR

Conversion

Trial Conversion

Renewals

Cancellation

Refund Rate

Average Revenue Per User

Customer Lifetime Value

AI Cost Per Plan

---

# Admin Controls

Admins can

Grant Plans

Extend Plans

Reset Credits

Add Credits

Remove Credits

Pause Subscription

Resume Subscription

Grant Lifetime Access

View Billing History

---

# Security

Validate payment webhooks.

Never trust client-side payment confirmation.

Encrypt payment metadata.

Never store sensitive payment information.

Log every billing event.

---

# Future Enhancements

Family Plan

Team Plan

Education Plan

Marketplace Credits

Usage-Based Billing

Pay-As-You-Go AI

Custom Enterprise Contracts

API Subscription Plans

White Label Licensing

---

# Long-Term Vision

The subscription system should be flexible enough to support millions of users, multiple billing providers, international expansion, enterprise customers, and future AI pricing models.

It should balance accessibility for beginners with sustainable operating costs, ensuring that StockAssist AI remains both valuable to users and commercially viable.

---

# End of Subscription Documentation