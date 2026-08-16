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

**This package computes no business metrics of its own.** It deliberately does
not become a second place where P&L is calculated — `services.portfolio_engine`
and `services.trading_engine` remain the single source of truth for trading
math. What lives here is the *epistemics*: windows, provenance, and honesty
about gaps.
"""
from analytics import contract, periods, quality, registry  # noqa: F401
from analytics.contract import (  # noqa: F401
    AVAILABLE, DERIVED, EMPTY, MOCK, REAL, UNAVAILABLE,
    Metric, derived, empty, envelope, mock, real, unavailable,
)
from analytics.periods import IST, PERIODS, UnknownPeriod, Window, resolve, session_date  # noqa: F401

__all__ = [
    "contract", "periods", "quality", "registry",
    "Metric", "Window", "resolve", "session_date", "envelope",
    "real", "derived", "mock", "unavailable", "empty",
    "REAL", "DERIVED", "MOCK", "UNAVAILABLE", "AVAILABLE", "EMPTY",
    "IST", "PERIODS", "UnknownPeriod",
]
