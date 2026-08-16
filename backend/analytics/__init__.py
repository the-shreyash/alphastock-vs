"""StockAssist AI analytics package (PH3.8).

Home for the rules that decide **what a number in this product means** — which
window it covers, which timezone that window is anchored in, where it came
from, and whether a reader may act on it.

Tenants:

* `analytics.periods` — the one documented timezone strategy (storage UTC,
  boundaries IST) and the resolver that turns a period key into a half-open
  ``[start, end)`` window. Replaces the ``strftime("%Y-%m-%d")`` +
  ``startswith`` / ``$regex`` idiom that expressed every window as a UTC day on
  an NSE trading platform.
* `analytics.contract` — the metric envelope. Makes "I cannot compute this" a
  representable value that cannot be mistaken for zero, and makes a fabricated
  number announce itself in the payload rather than in a source comment.
* `analytics.registry` — the inventory of every analytics number in the
  product, classified REAL / DERIVED / MOCK / UNAVAILABLE, as code so the
  classification is testable and cannot drift. Also the PH3.9 mock-removal
  specification.
* `analytics.quality` — source-data validation. Reports; never repairs, never
  silently excludes.
* `analytics.sources` (PH3.9) — the authoritative production sources behind the
  metrics whose mocks were removed, and the two gates that decide when a metric
  is answerable at all: whether a payment integration exists, and how far back
  ``db.sessions`` actually retains activity.
* `analytics.platform_health` (PH3.9) — platform health read from real readiness
  probes and real metric counters, replacing three surfaces that reported it
  from literals.

**This package computes no business metrics of its own.** It deliberately does
not become a second place where P&L is calculated — `services.portfolio_engine`
and `services.trading_engine` remain the single source of truth for trading
math. What lives here is the *epistemics*: windows, provenance, and honesty
about gaps.
"""
from analytics import contract, periods, quality, registry, sources  # noqa: F401
from analytics.contract import (  # noqa: F401
    AVAILABLE, DERIVED, EMPTY, MOCK, REAL, UNAVAILABLE,
    Metric, derived, empty, envelope, mock, real, unavailable,
)
from analytics.periods import (  # noqa: F401
    IST, PERIODS, UnknownPeriod, Window, preceding, resolve, session_date,
)

# `platform_health` is deliberately NOT imported here. It pulls in
# `observability.metrics` and, on one path, `services.scheduler` — which builds
# an APScheduler instance at import. A package whose import graph starts a
# scheduler is a package that cannot be imported by a test, so the two admin
# routes that need it import it directly.

__all__ = [
    "contract", "periods", "quality", "registry", "sources",
    "Metric", "Window", "resolve", "preceding", "session_date", "envelope",
    "real", "derived", "mock", "unavailable", "empty",
    "REAL", "DERIVED", "MOCK", "UNAVAILABLE", "AVAILABLE", "EMPTY",
    "IST", "PERIODS", "UnknownPeriod",
]
