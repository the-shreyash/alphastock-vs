# StockAssist AI
## Security Documentation

Version: 1.2

Status: Active Development — PH1.4b complete (2026-07-20)

---

# Purpose

This document is the high-level, operational security guide for StockAssist AI: the rules, policies, and checklists every developer follows day to day.

For the engineering-depth blueprint — exact module design, request/OAuth/refresh/logout sequence diagrams, threat model, trust boundaries, and the authoritative record of what is implemented versus planned — see **SECURITY_ARCHITECTURE.md**, the single source of truth for all security architecture decisions. Where this document and SECURITY_ARCHITECTURE.md appear to differ on implementation detail, SECURITY_ARCHITECTURE.md is authoritative; it is derived directly from the codebase.

Security is not a feature.

Security is part of every feature.

Every developer must follow this document before implementing any functionality.

---

# Security Goals

Protect User Data

Protect Financial Data

Protect Broker Accounts

Protect AI APIs

Protect Payments

Protect Infrastructure

Protect Business Logic

Prevent Abuse

Detect Attacks

Recover Quickly

---

# Security Principles

Least Privilege

Zero Trust

Defense in Depth

Fail Secure

Secure by Default

Audit Everything

Encrypt Sensitive Data

Never Trust Client Input

Never Expose Secrets

Validate Everything

---

# Security Layers

User

↓

HTTPS

↓

Cloudflare

↓

Rate Limiter

↓

API Gateway

↓

Authentication

↓

Authorization

↓

Validation

↓

Business Logic

↓

Database

↓

Encrypted Storage

Every request passes through all layers.

---

# Authentication

Supported

Email & Password

Google OAuth (hardened — see Google OAuth Security below)

Future

GitHub OAuth

Passkeys

Biometric Authentication

Magic Links

---

# Google OAuth Security

The Google OAuth flow is fail-closed and follows OAuth 2.0 / OpenID Connect
best practices (hardened in PH1.2).

Summary: server-side authorization-code exchange only, CSRF `state`
double-submit plus single-use server-side replay protection, cryptographic
`id_token` verification, a mandatory `email_verified` gate, an allowlisted
redirect URI bound to the flow's state, `sub`-first identity resolution with
safe account linking, and immutable audit logging of every outcome. Missing
Google credentials fail closed (401, no session).

Full design, rationale, and the OAuth login sequence diagram:
**SECURITY_ARCHITECTURE.md §13 (Google OAuth Architecture)** and **§29 (OAuth
Login Sequence)**.

---

# Password Policy

Status: ENFORCED since PH1.5 (2026-07-19) for all new passwords, centralized
in `backend/security/passwords.py` and applied at the model layer (422 before
hashing). Existing accounts are unaffected — login never re-validates policy.

Minimum Length

12 Characters

Maximum Length

64 Characters (and 72 UTF-8 bytes — the bcrypt truncation boundary)

Require

Uppercase

Lowercase

Number

Special Character

Reject

Common passwords (bundled blocklist, padding-resistant matching)

Password equal to or containing the user's email or name

Repeated-character passwords (fewer than 5 unique characters)

Sequential passwords (alphabet, digit, or keyboard-row runs, either direction)

Leading/trailing whitespace is stripped before validation and hashing.

Never store passwords in plain text.

Hash passwords using Argon2 or bcrypt with a strong cost factor.
(Implemented: bcrypt with explicit cost factor 12.)

Login failures are generic and timing-equalized — the response never reveals
whether the email exists, and validation errors never echo the submitted
password.

Full design and rationale: **SECURITY_ARCHITECTURE.md §15 (Password Security
Architecture)**.

---

# Multi-Factor Authentication

Future

Authenticator App

Email OTP

Hardware Security Keys

Recovery Codes

Admin accounts should require MFA.

---

# JWT Strategy

Access Token

Short Lifetime

15 Minutes

Refresh Token

Long Lifetime

30 Days

Store Refresh Tokens securely.

Rotate refresh tokens after use.

Immediately revoke compromised tokens.

**Implemented (PH1.6):** access-token lifetime is 15 minutes and refresh tokens rotate on every use with reuse detection (a replayed refresh revokes the whole family) — see SECURITY_ARCHITECTURE.md §11/§12. Both lifetimes are env-configurable (`JWT_ACCESS_TTL_SECONDS`, `JWT_REFRESH_TTL_SECONDS`); the shipped **default refresh lifetime is 7 days** (aligned with the `refresh_token` cookie Max-Age). Deployments that want the 30-day policy target above set `JWT_REFRESH_TTL_SECONDS=2592000`. Server-side revocation is durable (MongoDB `sessions` collection); token `ver` and `password_changed_at` provide platform-wide and per-user kill-switches for compromised tokens.

---

# Session Management

Track

Session ID

Device

Browser

IP Address

Country

Created Time

Last Activity

Allow users to:

View Active Sessions

Logout Specific Session

Logout All Sessions

**Implemented (PH1.6):** each login/registration/OAuth opens a durable session (refresh-token family) that captures `session_id`, `user_agent`, `ip`, created/last-used timestamps, and absolute expiry — the data-model groundwork for the "active sessions" screen. `POST /api/auth/logout` revokes the current session; `POST /api/auth/logout-all` revokes every session for the user (`SessionStore.revoke_all_for_user`). The user-facing "View Active Sessions / Logout Specific Session" **UI** and device/country enrichment are PH1.10.

---

# Cookie Security

Authentication cookies are the browser's session credential and are hardened
centrally in `backend/security/cookies.py` (PH1.3). No auth cookie is set or
cleared anywhere else, so the policy cannot drift across call sites.

Cookies in use: `access_token` and `refresh_token` (session, Path `/`) and
`g_oauth_state` (short-lived Google OAuth CSRF state, Path `/api/auth`,
single-use). All three are always `HttpOnly`; `Secure` is forced `True` in
production regardless of environment override; `SameSite` defaults to `Lax`
and is configurable. Clearing mirrors the exact attributes used when setting,
so browsers actually delete the cookie. Session fixation is structurally
prevented: login, registration, and Google OAuth all mint fresh tokens and
overwrite cookies in place.

Full policy table, design rationale, and defaults:
**SECURITY_ARCHITECTURE.md §10 (Cookie Architecture)**.

---

# Authorization

Role-Based Access Control (RBAC)

Roles

Guest

User

Pro

Elite

Admin

Super Admin

Every API verifies permissions.

Never trust frontend role checks.

---

# Fine-Grained Permissions

Target design (not yet implemented — see SECURITY_ARCHITECTURE.md §8 for the
gap and current state). Planned permissions include

View Portfolio

Trade Stocks

Connect Broker

View Admin Dashboard

Manage Users

Manage Payments

Manage AI

Manage Feature Flags

Each permission is individually configurable.

Authorization today is role-based, not permission-based: `require_admin` gates
`/api/admin/*` on `role ∈ {admin, super_admin}`. No PH1 sprint currently owns
building the fine-grained system described above.

---

# HTTPS

Always Enabled.

No HTTP in production.

Enable HSTS.

Redirect HTTP to HTTPS.

---

# API Security

Validate Every Request

Validate Headers

Validate Body

Validate Query Parameters

Validate Path Parameters

Reject Invalid JSON

Reject Oversized Requests

Reject Unknown Fields where appropriate.

---

# Input Validation

Use schema validation.

Validate

Email

Password

Phone

Stock Symbols

Order Quantity

Price

Dates

Enums

Never trust user input.

---

# Rate Limiting

Guest

30 Requests / Minute

Free

120 Requests / Minute

Pro

300 Requests / Minute

Elite

600 Requests / Minute

Admin

Configurable

Different endpoints may have stricter limits.

Not yet implemented platform-wide (PH1.7). A narrower mechanism already exists
today: login attempts are locked out per `ip:email` after 5 failures for 15
minutes. See SECURITY_ARCHITECTURE.md §21 (Rate Limiting Strategy).

---

# Secrets Management

Never store secrets inside source code.

Store in secure environment variables or a secrets manager.

Examples

JWT Secret

Mongo URI

Redis URL

Claude API Key

Gemini API Key

Broker Secrets

Payment Secrets

Encryption Keys

Rotate secrets periodically.

---

# Encryption

Encrypt

Passwords

Broker Tokens

Refresh Tokens

Sensitive User Preferences

Payment Metadata

API Keys

Never expose decrypted values in logs.

---

# Broker Security

Never store

Broker Password

PIN

OTP

Security Questions

Only store encrypted access tokens where supported.

Reconnect users when tokens expire.

Market data entitlement (see MARKET_DATA_ARCHITECTURE.md):

Broker tokens used for the streaming market data feed are strictly per-user.

One user's broker feed must never be shared with, multiplexed to, or cached for another user.

StockAssist consumes the broker feed only on behalf of the authenticated user who owns the entitlement — never redistributes it.

On token revocation or expiry, the Source Manager silently falls back to Yahoo Finance and the user is prompted to reconnect — never shown a raw provider error.

---

# Payment Security

Never process card information directly.

Use provider-hosted checkout.

Verify every webhook signature.

Log all payment events.

Support PCI compliance through payment providers.

---

# AI Security

Protect AI API Keys.

Rate limit AI usage.

Monitor abuse.

Prevent prompt injection where possible.

Filter sensitive data before sending prompts.

Log AI failures.

---

# Prompt Protection

Never include

Secrets

API Keys

Passwords

Tokens

Internal Configuration

Database Credentials

inside prompts.

Only send required context.

---

# Data Privacy

Collect only necessary information.

Allow users to:

Export Data

Delete Account

Delete AI History

Manage Preferences

Comply with applicable privacy regulations.

---

# Logging

Log

Authentication

Payments

Trades

Broker Connections

Admin Actions

Security Events

Do NOT log

Passwords

Tokens

API Keys

Sensitive Personal Data

---

# Audit Logging

Every critical action is recorded.

Examples

User Login

Password Change

Broker Connected

Trade Executed

Plan Changed

Refund Issued

Admin Login

Feature Enabled

Audit logs are immutable.

---

# Error Handling

Never expose

Stack Traces

Database Errors

Internal Paths

API Keys

Detailed Exceptions

Return user-friendly messages.

Log technical details internally.

---

# File Upload Security

Validate

Type

Size

Extension

Content

Scan uploaded files before processing.

Store outside the public directory.

---

# Database Security

Use

Least Privilege Accounts

Encrypted Connections

Indexes

Backups

Access Controls

Audit Logs

Never expose database directly.

---

# Redis Security

Require Authentication.

Disable Public Access.

Encrypt connections where supported.

Store only temporary data.

---

# WebSocket Security

Authenticate connection.

Validate every event.

Rate limit messages.

Disconnect inactive clients.

Reject unauthorized subscriptions.

---

# CSRF Protection

Protect state-changing endpoints.

`SameSite=Lax` on all auth cookies is the current baseline (withholds cookies
on cross-site sub-requests). A dedicated CSRF token layer for cookie-based,
state-changing routes is designed but **not yet scheduled to a PH1 sprint** —
see SECURITY_ARCHITECTURE.md §18 for the gap and the recommended next step.

---

# XSS Protection

Escape output.

Sanitize HTML.

Use Content Security Policy (CSP).

Avoid unsafe inline scripts.

---

# SQL / NoSQL Injection

Use parameterized queries.

Never concatenate user input into queries.

Validate all identifiers.

---

# Content Security Policy

Restrict

Scripts

Styles

Images

Frames

Connections

Only trusted origins.

---

# CORS Policy

Cross-Origin Resource Sharing is production-hardened and centralized in
`backend/security/cors.py` (PH1.4). It is the single place CORS is configured;
`server.py` wires it in via `apply_cors(app)`. The previous
wildcard-with-credentials default has been removed.

Summary: `CORS_ALLOWED_ORIGINS` is the canonical, exact-match origin allowlist
(legacy `CORS_ORIGINS`/`FRONTEND_URL` still honored). A literal `*` is stripped
from every source, so a wildcard can never enter the allowlist or pair with
credentials. Development falls back to `http://localhost:3000` /
`http://localhost:5173` only when nothing is configured and `APP_ENV` is not
`production`; production assumes nothing and fails closed (empty allowlist →
every cross-origin request rejected). Methods and headers are enumerated, not
wildcarded; no response headers are exposed.

Full origin-resolution precedence, environment variables, and rationale:
**SECURITY_ARCHITECTURE.md §19 (CORS Strategy)**.

---

# Security Headers

**Implemented and centralized in `backend/security/headers.py` (PH1.4b).** A
single pure-ASGI `SecurityHeadersMiddleware` (`apply_security_headers(app)`,
wired after CORS) stamps the security headers on **every** response — the only
place security response headers are set. Every value is environment-overridable;
the CSP is nonce-capable (`{nonce}` placeholder → per-request nonce, also on
`request.state.csp_nonce`) for future HTML rendering.

Emitted on every response:

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy` — powerful features (camera, mic, geolocation, USB, …) disabled
- `Cross-Origin-Opener-Policy: same-origin`
- `Cross-Origin-Resource-Policy: same-origin` (safe with the credentialed CORS frontend — CORP only blocks *no-cors* loads)
- `X-XSS-Protection: 0` — deprecated legacy auditor neutralized (superseded by CSP)
- `Content-Security-Policy: default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'` — strict API lockdown, **no `unsafe-*`**

Conditional:

- `Strict-Transport-Security: max-age=63072000; includeSubDomains` — only over HTTPS / production (honors `X-Forwarded-Proto` behind a proxy; `preload` opt-in)
- `Cross-Origin-Embedder-Policy: require-corp` — implemented, opt-in via `CROSS_ORIGIN_EMBEDDER_POLICY`

Full header matrix, environment variables, and rationale:
**SECURITY_ARCHITECTURE.md §20 (Security Headers Strategy)**.

---

# Infrastructure Security

Cloudflare Protection

Firewall Rules

DDoS Protection

Secure DNS

Automatic HTTPS

Server Hardening

Least Privilege

Regular Updates

---

# Monitoring

Monitor

Failed Logins

API Abuse

Rate Limit Violations

Bot Activity

AI Abuse

Broker Failures

Payment Failures

Security Alerts

Suspicious Admin Activity

---

# Incident Response

Detect

↓

Alert

↓

Investigate

↓

Contain

↓

Recover

↓

Review

↓

Document

Every incident should have a postmortem.

---

# Backup Strategy

Daily

Weekly

Monthly

Test restores regularly.

Encrypt backups.

Store backups securely.

---

# Dependency Security

Use Dependabot (or similar).

Regularly update dependencies.

Run vulnerability scans.

Remove unused packages.

---

# Secure Development

Every Pull Request should verify

Authentication

Authorization

Validation

Logging

Error Handling

Tests

Performance

Security

Documentation

---

# Penetration Testing

Before production

Authentication Testing

Authorization Testing

API Testing

Rate Limiting

XSS

CSRF

Injection

File Upload

Session Management

Broker Integration

Payment Flow

---

# Compliance

Design with support for

OWASP Top 10

Privacy Regulations

Financial Data Protection

Payment Provider Requirements

Broker API Policies

---

# Security Checklist

Before production verify

✓ HTTPS

✓ JWT Rotation

✓ MFA for Admins

✓ Rate Limiting

✓ Encryption

✓ Secure Secrets

✓ Input Validation

✓ Output Encoding

✓ Audit Logging

✓ Monitoring

✓ Backups

✓ Vulnerability Scan

✓ Penetration Testing

✓ Documentation

---

# Long-Term Vision

Security should be embedded into every layer of StockAssist AI.

The platform should continuously monitor threats, protect user data, safeguard financial operations, and evolve with modern security best practices.

Every new feature must undergo a security review before release.

---

# End of Security Documentation