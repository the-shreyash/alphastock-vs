# StockAssist AI
## Security Documentation

Version: 1.1

Status: Active Development

---

# Purpose

This document defines every security requirement for StockAssist AI.

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

Flow initiation

The authorization URL and the CSRF `state` are generated server-side
(`GET /api/auth/google/login-url`). The `state` is a cryptographically random
value bound to the browser via a short-lived httponly cookie. The client never
constructs the Google URL.

CSRF and replay protection

The callback exchange (`POST /api/auth/google/session`) requires the `state` and
validates it (constant-time) against the httponly cookie (double-submit, per-browser
binding). It is also backed by a single-use server-side record (Redis when
configured, in-memory fallback otherwise) that is consumed on first use — this
defeats replay and gives an authoritative TTL expiry across processes. Missing,
mismatched, replayed, or expired state is rejected.

Identity verification

The returned OpenID Connect `id_token` is cryptographically verified (signature
against Google's public keys, audience = our client_id, expiry, issuer). Identity
is taken only from the verified token — never from an unauthenticated endpoint.

Email verification gate

A Google account whose `email_verified` claim is not true is rejected. An
unverified email never creates a new account and never links to an existing one.

Redirect URI

The redirect URI must match an allowlist derived from configured frontend
origins. There is no hardcoded fallback.

Identity and account linking

The Google `sub` claim is the primary external identity (stable across Google
profile or email changes); the verified email is the secondary key. Accounts
resolve by `sub` first, then by verified email to link an existing email/password
account — the Google identity is attached and the password credential is
preserved rather than taken over. Email is the unique key, so no duplicate
accounts are created. An email already bound to a different `sub` is rejected.

Audit logging

Every OAuth outcome is written to an immutable security-audit log
(`security_audit_logs`): successes (with new-account / linked flags) and failures
with a machine-readable reason. The log records ip, user-agent, and outcome —
never tokens, authorization codes, or state values.

Configuration errors fail closed

When `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` are unset, Google sign-in returns
401 and no session is issued.

---

# Password Policy

Minimum Length

12 Characters

Require

Uppercase

Lowercase

Number

Special Character

Never store passwords in plain text.

Hash passwords using Argon2 or bcrypt with a strong cost factor.

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

---

# Cookie Security

Authentication cookies are the browser's session credential and are hardened
centrally in `backend/security/cookies.py` (PH1.3). No auth cookie is set or
cleared anywhere else, so the policy cannot drift across call sites.

Cookies in use:

- `access_token` — access JWT. HttpOnly, Path `/`.
- `refresh_token` — refresh JWT. HttpOnly, Path `/`.
- `g_oauth_state` — short-lived Google OAuth CSRF state. HttpOnly, Path
  `/api/auth`, single-use, burned after the exchange.

Policy:

- **HttpOnly** — always. JavaScript never needs these cookies; this keeps them
  out of reach of XSS.
- **Secure** — driven by `COOKIE_SECURE` and **forced `True` when
  `APP_ENV=production`** (the override is ignored in production). No auth token
  is ever transmitted over plain HTTP in production. Development defaults to
  `False` so cookies work over `http://localhost`.
- **SameSite** — `Lax` by default (a cookie-layer CSRF baseline: cookies are
  withheld on cross-site sub-requests). Configurable via `COOKIE_SAMESITE`
  (`lax`/`strict`/`none`); `None` requires `Secure` and is auto-degraded to
  `Lax` when it would not be `Secure`. The OAuth-state cookie is never `Strict`
  so it survives the top-level redirect back from Google.
- **Path** — least required: session cookies at `/`; OAuth state at `/api/auth`.
- **Domain** — optional `COOKIE_DOMAIN` for subdomain session sharing;
  host-only when unset.
- **Clearing** — logout and OAuth burn mirror the exact key/path/domain/security
  attributes used when setting, so browsers actually delete the cookie.

Session fixation: login, registration and Google OAuth all mint fresh tokens
and overwrite the cookies in place, so a pre-authentication cookie value can
never be promoted to an authenticated session.

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

Permissions include

View Portfolio

Trade Stocks

Connect Broker

View Admin Dashboard

Manage Users

Manage Payments

Manage AI

Manage Feature Flags

Each permission is individually configurable.

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

Use SameSite cookies where applicable.

Validate CSRF tokens when using cookie-based authentication.

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
wildcard-with-credentials default (`Access-Control-Allow-Origin: *` +
`allow_credentials=True`) — which is both unsafe and forbidden by the Fetch
standard — has been removed.

Allowed origins

`CORS_ALLOWED_ORIGINS` is the single source of truth: a comma-separated,
exact-match allowlist of origins (scheme + host + port, no trailing slash).
Legacy `CORS_ORIGINS` and `FRONTEND_URL` are still honored as inputs for
backward compatibility. A literal `*` is stripped from every source, so a
wildcard can never enter the allowlist or pair with credentials. The browser's
`Origin` header is compared verbatim; an unknown origin never receives
`Access-Control-Allow-Origin` and is blocked.

Development

When no origin variable is configured (and `APP_ENV` is not `production`), the
policy falls back to the local dev origins `http://localhost:3000` and
`http://localhost:5173`, so the app runs with zero configuration.

Production

Nothing is assumed. An unconfigured allowlist in production is empty and every
cross-origin request is rejected (fail closed). Deployments must set
`CORS_ALLOWED_ORIGINS` explicitly.

Credentials, methods, headers

Credentials are allowed (`Access-Control-Allow-Credentials: true`) — the app
authenticates with HttpOnly session cookies — but only because origins are an
exact allowlist and never the wildcard; the two invariants are enforced
together. Allowed methods are restricted to those the REST API serves
(`GET, POST, PUT, PATCH, DELETE, OPTIONS`) and allowed request headers to those
the frontend sends (`Authorization, Content-Type, Accept, Origin,
X-Requested-With`) rather than reflecting `*`. No response headers are exposed
(cookie-based auth needs none). Preflight (`OPTIONS`) is handled by the
middleware and cached for 10 minutes.

Environment variables

- `CORS_ALLOWED_ORIGINS` — canonical exact-match origin allowlist (comma
  separated). Required in production.
- `CORS_ORIGINS` — legacy input, still honored. Wildcard `*` is ignored.
- `FRONTEND_URL` — legacy input, still honored (also used by the OAuth
  redirect-URI allowlist, PH1.2).
- `APP_ENV` — `production` disables the local dev-origin fallback (fail closed).

---

# Security Headers

Enable

HSTS

X-Frame-Options

X-Content-Type-Options

Referrer-Policy

Permissions-Policy

Content-Security-Policy

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