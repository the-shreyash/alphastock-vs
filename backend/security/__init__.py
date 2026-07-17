"""StockAssist AI security package.

Home for cross-cutting, security-relevant primitives that must behave
identically everywhere they are used. Current tenants:

* `security.cookies` (PH1.3) — centralized authentication-cookie policy.
* `security.cors` (PH1.4) — centralized, environment-driven CORS policy.

Subsequent hardening sprints add their own modules here (CSRF, headers, rate
limiting, token lifecycle) per PRODUCTION_ROADMAP.md.
"""
