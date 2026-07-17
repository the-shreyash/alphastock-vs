# StockAssist AI
## Admin Portal Documentation

Version: 1.1

Status: Active Development

---

# Purpose

The Admin Portal is the internal control center of StockAssist AI.

It allows administrators to monitor, manage, configure, secure, and operate the entire platform from one place.

The Admin Portal is never accessible to normal users.

It is designed for:

Platform Owner

Developers

Support Team

Operations Team

Finance Team

Future Moderators

---

# Objectives

The Admin Portal should allow administrators to:

Monitor platform health

Manage users

Manage subscriptions

Monitor AI

Monitor APIs

View analytics

Control feature flags

Manage announcements

Monitor payments

Handle support

Review logs

Maintain system security

Everything should update in real time.

---

# Admin Roles

## Super Admin

Full unrestricted access.

Can:

Manage everything

Delete users

Grant plans

Configure AI

Configure APIs

Manage payments

View logs

Manage brokers

Manage announcements

Deploy feature flags

System settings

---

## Admin

Limited administrator.

Can:

Manage users

Respond to support

View analytics

View logs

Cannot modify critical system settings.

---

## Support Admin

Can:

View users

View subscriptions

Respond to tickets

Reset user sessions

Cannot access financial data.

---

## Finance Admin

Can access:

Payments

Revenue

Invoices

Subscriptions

Refunds

Cannot modify platform settings.

---

# Dashboard

Purpose

Provide a live overview of platform health.

Cards

Total Users

Users Online

Today's Active Users

Premium Users

Elite Users

Revenue Today

MRR

ARR

Today's Trades

AI Requests

Morning Reports Generated

Broker Connections

API Health

Server Health

Database Health

Redis Health

Notification Queue

Support Tickets

Everything refreshes automatically.

---

# Navigation

Dashboard

Users

Subscriptions

Payments

AI

Market

Broker

API Health

Notifications

Support

Announcements

Analytics

Logs

Feature Flags

Settings

System Health

---

# User Management

Purpose

Manage registered users.

User List

Name

Email

Plan

Status

Registration Date

Last Login

Broker Connected

AI Usage

Portfolio Value

Actions

View Profile

Block User

Suspend User

Delete User

Grant Premium

Grant Elite

Grant Lifetime

Grant Beta Access

Reset AI Usage

Reset Password

Force Logout

View Activity

Export User

---

# VIP Access

Purpose

Grant premium access manually.

Workflow

Enter Email

↓

Choose Plan

↓

Choose Duration

↓

Save

↓

User Immediately Receives Access

Supported Plans

Free

Pro

Elite

Lifetime

Internal Team

Investor

Beta Tester

---

# AI Monitoring

Monitor

Claude

Gemini

Future Models

Display

Status

Latency

Response Time

Daily Requests

Monthly Requests

Token Usage

Estimated Cost

Failures

Fallbacks

Average Response

Current Queue

---

# AI Agent Monitoring

Monitor every agent.

Master Orchestrator

Market Analyst

Technical Analyst

Fundamental Analyst

News Intelligence

Portfolio Manager

Trade Monitor

Risk Manager

Learning Mentor

Morning Report Agent

Notification Agent

Broker Agent

Operations Agent

Display

Running

Idle

Processing

Failed

Restarting

Error Count

Average Response Time

---

# Market Data Provider Monitoring

The admin portal is the only surface allowed to display provider-level detail (see MARKET_DATA_ARCHITECTURE.md — user-facing surfaces show only the tier: Live / Delayed).

Display per provider (via Market Gateway health + Source Manager status):

Connection State

Active Users on Provider

p50 / p95 Latency

Message Rate

Error / Reconnect Counts

Failover Events

---

# API Monitoring

Supported APIs

Yahoo Finance

NSE

Claude

Gemini

News API

TradingView

Zerodha

Upstox

Payment Gateway

Mail Provider

For each API display

Status

Latency

Requests Today

Requests This Month

Quota Remaining

Failure Rate

Last Error

Last Success

---

# Broker Monitoring

Connected Brokers

Daily Syncs

Failed Syncs

Order Success Rate

Average Sync Time

WebSocket Status

Authentication Errors

Token Expiry

Broker Availability

---

# Payments

Dashboard

Revenue Today

Weekly Revenue

Monthly Revenue

Annual Revenue

MRR

ARR

Pending Payments

Refunds

Failed Payments

Invoices

Subscriptions

Charts

Revenue

Growth

Plan Distribution

---

# Subscription Management

View

Free Users

Pro Users

Elite Users

Lifetime Users

Expired Plans

Upcoming Renewals

Actions

Upgrade

Downgrade

Cancel

Extend

Reset Credits

Grant Bonus Credits

---

# AI Usage

Monitor

Requests

Tokens

Credits

Daily Usage

Monthly Usage

Average Cost

Cost Per User

Cost Per Request

Top AI Users

Usage by Plan

Alerts

High Cost Users

Abnormal Usage

Potential Abuse

---

# Feature Flags

Enable

Disable

Hide

Beta Release

Examples

Paper Trading

Backtesting

Broker Integration

Morning Report

AI Debate

Learning Mode

Voice AI

Mobile Features

Changes should not require deployment.

---

# Analytics

User Analytics

Daily Active Users

Monthly Active Users

Retention

Churn

Growth

Conversion

Feature Usage

Most Used Features

Most Used Stocks

Most Used AI Features

Morning Report Reads

Portfolio Reviews

Scanner Usage

Search Trends

Business Analytics

Revenue

AI Cost

Profit

Subscriptions

Renewals

Cancellation

Refunds

---

# Support Center

View

Tickets

Bug Reports

Feature Requests

Feedback

Priority

Assigned Admin

Response Time

Resolution Time

Internal Notes

Actions

Reply

Assign

Close

Reopen

Escalate

---

# Announcements

Create

Edit

Delete

Schedule

Target

All Users

Free Users

Pro Users

Elite Users

Admins

Examples

Maintenance

Feature Release

Security Notice

Market Alert

Promotions

---

# Notifications

Monitor

Sent

Delivered

Opened

Clicked

Failed

Retry Queue

Notification Types

Email

Browser

Push

SMS (Future)

WhatsApp (Future)

---

# Audit Logs

Record every administrative action.

Examples

User Blocked

Plan Granted

Payment Refunded

Feature Enabled

API Changed

Broker Disabled

Settings Updated

Every audit log includes

Admin

Timestamp

IP

Action

Target

Result

Audit logs are immutable.

---

# System Health

Monitor

CPU

RAM

Disk

MongoDB

Redis

Queues

Workers

WebSockets

Scheduler

Background Jobs

Alerts

Green

Healthy

Yellow

Warning

Red

Critical

---

# Event Monitoring

Display

Events Per Minute

Failed Events

Queue Size

Dead Letter Queue

Replay Status

Worker Health

Average Processing Time

---

# Security Dashboard

Failed Logins

Blocked IPs

Rate Limit Events

Token Expiry

Admin Sessions

Suspicious Activity

2FA Status

Security Alerts

---

# Settings

Configure

Platform Name

Support Email

Market Hours

Maintenance Mode

AI Providers

Broker Providers

Payment Providers

Notification Providers

Feature Flags

API Keys (managed securely)

---

# Permissions Matrix

Every admin permission should be role-based.

Examples

View Users

Edit Users

Delete Users

View Payments

Refund Payments

Manage AI

Manage APIs

Manage Brokers

Manage Feature Flags

Manage Settings

Every permission should be individually configurable.

---

# Future Modules

Customer Success Dashboard

Sales Dashboard

Investor Dashboard

Marketing Dashboard

CRM

Knowledge Base

AI Operations Center

Incident Management

Status Page

Multi-Tenant Support

Enterprise Administration

---

# Performance Goals

Dashboard Load

< 2 seconds

API Health Refresh

< 5 seconds

User Search

< 500ms

Analytics Dashboard

< 3 seconds

Live Updates

Near Real-Time

---

# Admin Portal Checklist

Before production verify:

✓ Role-Based Access Control

✓ Audit Logging

✓ API Monitoring

✓ AI Monitoring

✓ Broker Monitoring

✓ Payment Monitoring

✓ Feature Flags

✓ Analytics

✓ Security Dashboard

✓ Responsive Design

✓ Dark Theme

✓ Light Theme

✓ Performance Optimized

---

# Long-Term Vision

The Admin Portal should evolve into a complete operations center for StockAssist AI.

Administrators should be able to manage the entire platform—from AI models and broker integrations to users, payments, analytics, and infrastructure—without requiring direct database access or server intervention.

It should provide a secure, real-time, and comprehensive view of the health and performance of the business, enabling rapid issue resolution, informed decision-making, and continuous platform improvement.

---

# End of Admin Portal Documentation