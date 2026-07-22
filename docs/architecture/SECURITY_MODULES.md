# Security Modules Architecture

**Project:** StockAssist AI  
**Document Version:** 1.0  
**Last Updated:** July 2026  
**Owner:** Security Engineering

---

# Purpose

This document describes every module inside `backend/security/`.

Each security concern must have **exactly one authoritative implementation**.

Developers must never duplicate security logic elsewhere in the application.

This document explains:

- Why each module exists
- What problem it solves
- Public APIs
- Internal dependencies
- Test coverage
- Rules for future development

---

# Security Design Principles

The security layer follows these principles:

- Single Responsibility Principle
- Single Source of Truth
- Fail Closed
- Defense in Depth
- Secure by Default
- Least Privilege
- Centralized Security Logic
- No Business Logic Inside Security Modules
- Test Every Security Boundary
- Environment-Aware Configuration

---

# Module Dependency Diagram

```
                server.py
                     │
                     │
         ┌───────────┴────────────┐
         │                        │
      Authentication         API Requests
         │                        │
         └───────────┬────────────┘
                     │
           backend/security/
                     │
 ┌─────────────────────────────────────────────┐
 │ cookies.py                                  │
 │ cors.py                                     │
 │ headers.py                                  │
 │ passwords.py                                │
 │ jwt.py                                      │
 │ sessions.py                                 │
 │ csrf.py                                     │
 │ rate_limit.py                               │
 │ recovery.py                                 │
 │ secrets.py                                  │
 │ audit.py                                    │
 │ roles.py                                    │
 │ identifiers.py                              │
 └─────────────────────────────────────────────┘
```

---

# Module Reference

---

# cookies.py

## Purpose

Centralizes all cookie creation and validation.

Responsible for ensuring secure cookie behavior across the application.

---

## Responsibilities

- Create authentication cookies
- Configure cookie expiration
- Secure attributes
- SameSite policy
- HttpOnly enforcement
- Production Secure enforcement

---

## Public API

```python
set_auth_cookies()

clear_auth_cookies()

get_cookie_settings()
```

---

## Dependencies

- secrets.py
- jwt.py

---

## Used By

- Login
- Logout
- Refresh
- OAuth
- Session Management

---

## Tests

- test_cookie_security.py

---

## Future Improvements

- Cookie prefixes (__Host-, __Secure-)
- Partitioned cookies
- Browser compatibility monitoring

---

# cors.py

## Purpose

Provides centralized CORS configuration.

---

## Responsibilities

- Allowed origins
- Allowed methods
- Credentials policy
- Fail-closed validation

---

## Public API

```python
configure_cors()
```

---

## Dependencies

- secrets.py

---

## Tests

- test_cors_hardening.py

---

## Future Improvements

- Dynamic origin caching
- Enterprise multi-tenant support

---

# headers.py

## Purpose

Applies HTTP security headers to every response.

---

## Responsibilities

- CSP
- HSTS
- X-Frame-Options
- X-Content-Type-Options
- Referrer Policy
- Permissions Policy

---

## Public API

```python
configure_security_headers()
```

---

## Dependencies

None

---

## Tests

- test_security_headers.py

---

# passwords.py

## Purpose

Central password policy implementation.

---

## Responsibilities

- Password validation
- bcrypt hashing
- Password verification
- Timing attack mitigation
- Password policy enforcement

---

## Public API

```python
validate_password()

hash_password()

verify_password()
```

---

## Dependencies

- bcrypt

---

## Tests

- test_password_policy.py

---

## Future Improvements

- Password history
- Breach password API
- Adaptive policies

---

# jwt.py

## Purpose

Central JWT implementation.

---

## Responsibilities

- Token generation
- Token validation
- Claim validation
- Expiration
- Rotation support

---

## Public API

```python
create_access_token()

create_refresh_token()

verify_token()
```

---

## Dependencies

- secrets.py
- sessions.py

---

## Tests

- test_jwt_sessions.py

---

## Future Improvements

- Key rotation
- JWKS support
- Multiple signing keys

---

# sessions.py

## Purpose

Manages authenticated user sessions.

---

## Responsibilities

- Session lifecycle
- Refresh rotation
- Replay detection
- Logout
- Logout all devices

---

## Public API

```python
create_session()

revoke_session()

rotate_session()

revoke_all_sessions()
```

---

## Dependencies

- jwt.py
- audit.py

---

## Tests

- test_jwt_sessions.py

---

# csrf.py

## Purpose

Protects cookie-based requests against CSRF attacks.

---

## Responsibilities

- Token generation
- Token validation
- Session binding

---

## Public API

```python
generate_csrf_token()

verify_csrf()
```

---

## Dependencies

- sessions.py

---

## Tests

- test_csrf.py

---

# rate_limit.py

## Purpose

Protects APIs against abuse.

---

## Responsibilities

- Login limits
- Register limits
- Refresh limits
- Progressive lockout

---

## Public API

```python
check_rate_limit()
```

---

## Dependencies

- audit.py

---

## Tests

- test_rate_limit.py

---

# recovery.py

## Purpose

Implements account recovery workflows.

---

## Responsibilities

- Email verification
- Password reset
- Password change
- Recovery tokens

---

## Public API

```python
create_reset_token()

verify_reset_token()

change_password()
```

---

## Dependencies

- passwords.py
- sessions.py
- audit.py

---

## Tests

- test_recovery.py

---

# secrets.py

## Purpose

Centralized secret management.

---

## Responsibilities

- Environment validation
- Secret loading
- Startup validation

---

## Public API

```python
validate_config()

get_secret()
```

---

## Dependencies

Environment Variables

---

## Tests

- test_secrets.py

---

# audit.py

## Purpose

Centralized security audit logging.

---

## Responsibilities

- Security events
- Login events
- Token events
- Recovery events
- Rate limit events

---

## Public API

```python
log_security_event()

log_auth_event()

log_admin_event()
```

---

## Dependencies

None

---

## Tests

- test_audit.py

---

# roles.py

## Purpose

Centralized authorization rules.

---

## Responsibilities

- Role validation
- Role assignment
- Privilege escalation prevention

---

## Public API

```python
validate_role_assignment()

is_admin()

is_super_admin()
```

---

## Dependencies

- audit.py

---

## Tests

- test_roles.py

---

# identifiers.py

## Purpose

Safely parses and validates database identifiers.

---

## Responsibilities

- ObjectId parsing
- Error handling
- Input validation

---

## Public API

```python
parse_object_id()
```

---

## Dependencies

MongoDB

---

## Tests

- test_identifiers.py

---

# Development Rules

When adding a new security feature:

✅ Create a dedicated module if it represents a new security concern.

✅ Keep business logic outside `backend/security/`.

✅ Every public function must have tests.

✅ Every module must be documented in this file.

✅ Avoid circular dependencies between security modules.

✅ Do not duplicate logic already implemented in another security module.

---

# Module Ownership Matrix

| Module | Owner | Security Domain |
|----------|--------|----------------|
| cookies.py | Security | Cookie Security |
| cors.py | Security | Transport Security |
| headers.py | Security | HTTP Security |
| passwords.py | Security | Credential Security |
| jwt.py | Security | Authentication |
| sessions.py | Security | Session Management |
| csrf.py | Security | Request Protection |
| rate_limit.py | Security | Abuse Prevention |
| recovery.py | Security | Identity Recovery |
| secrets.py | Security | Secret Management |
| audit.py | Security | Audit Logging |
| roles.py | Security | Authorization |
| identifiers.py | Security | Input Validation |

---

# Summary

The `backend/security/` package is the **single source of truth** for all application security. Any change to authentication, authorization, session management, request protection, or secret handling **must** be implemented through these modules. This ensures consistency, maintainability, and production-grade security across the StockAssist AI platform.