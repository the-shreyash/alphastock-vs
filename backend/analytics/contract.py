"""The analytics response contract (PH3.8).

WHY A CONTRACT AND NOT JUST A NUMBER
------------------------------------
Every analytics endpoint in this product returned bare scalars:

    {"mrr": 12475, "retention_rate": 78.5, "revenue_today": 0}

Three numbers, three completely different epistemic statuses, and nothing in
the payload distinguishes them. ``mrr`` is a real user count multiplied by a
hardcoded price. ``retention_rate`` is a literal typed into the source. And
``revenue_today`` is 0 because the ``payments`` collection is empty — which a
reader cannot tell apart from "we made no money today". A consumer of that
payload — a frontend, an operator, a future AI summarising the business — has
no way to know which figures it may act on.

**Zero is the dangerous value.** An unavailable metric rendered as 0 is not a
missing number; it is a *wrong* number that looks authoritative and formats
beautifully. This module makes "I cannot compute this" a first-class value that
cannot be confused with "the answer is zero".

WHAT A METRIC CARRIES
---------------------
Name, value, unit, the resolved time window, the provenance class, the status,
and when it was computed. The provenance class is the load-bearing field: it is
the machine-readable answer to *should I trust this?*

    REAL         read directly from persisted production records
    DERIVED      computed deterministically from persisted production records
    MOCK         fabricated — hardcoded, formula-invented, or randomised
    UNAVAILABLE  legitimate metric, required source data does not exist

ADDITIVE BY CONSTRUCTION
------------------------
:func:`envelope` wraps a set of metrics *alongside* the flat keys an existing
endpoint already returns, rather than replacing them. PH3.8 is an audit sprint:
it must make the truth visible without breaking a single one of the 313
frontend tests or any consumer written against the current shapes. The flat
keys stay; ``analytics`` is added next to them; PH3.9 removes the mocks and, if
it chooses, retires the flat keys.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from analytics.periods import Window, now_utc, resolve

#: Provenance classes. The four-way classification PH3.8 assigns to every metric.
REAL = "real"
DERIVED = "derived"
MOCK = "mock"
UNAVAILABLE = "unavailable"

PROVENANCE = (REAL, DERIVED, MOCK, UNAVAILABLE)

#: Delivery status, orthogonal to provenance. A metric can be provenance=DERIVED
#: and status=unavailable (the calculation is sound, this user has no data yet).
#: A metric that is provenance=MOCK is ALWAYS status=mock — the two cannot
#: disagree, and `Metric.__post_init__` enforces that rather than trusting the
#: caller to keep them in step.
AVAILABLE = "available"
EMPTY = "empty"

STATUSES = (AVAILABLE, EMPTY, MOCK, UNAVAILABLE)

#: Units. A number with no unit is how a rupee figure ends up rendered as a
#: percentage. `count` is the default because most metrics are counts.
COUNT = "count"
INR = "INR"
PERCENT = "percent"
RATIO = "ratio"
SECONDS = "seconds"

UNITS = (COUNT, INR, PERCENT, RATIO, SECONDS)


class ContractError(ValueError):
    """A metric was constructed with an invalid provenance / status / unit.

    Raised at construction, not at serialisation: a malformed metric must fail
    in the test that builds it, not in production when somebody reads it.
    """


@dataclass(frozen=True)
class Metric:
    """One analytics number, with everything needed to interpret it.

    ``value`` is ``None`` whenever ``status`` is not ``available`` — enforced,
    not merely conventional. This is the whole point of the module: an
    unavailable metric has no value, and cannot be coerced into 0 by a
    ``value or 0`` on the far side of an HTTP boundary because there is nothing
    there to coerce.
    """

    name: str
    value: Any = None
    unit: str = COUNT
    provenance: str = DERIVED
    status: str = AVAILABLE
    period: Optional[Window] = None
    source: str = ""
    note: str = ""
    comparison: Optional[dict] = None
    calculated_at: str = field(default_factory=lambda: now_utc().isoformat())

    def __post_init__(self):
        if self.provenance not in PROVENANCE:
            raise ContractError(
                f"{self.name}: provenance {self.provenance!r} is not one of {PROVENANCE}.")
        if self.status not in STATUSES:
            raise ContractError(
                f"{self.name}: status {self.status!r} is not one of {STATUSES}.")
        if self.unit not in UNITS:
            raise ContractError(f"{self.name}: unit {self.unit!r} is not one of {UNITS}.")
        # A mock metric must announce itself in BOTH fields. Letting a caller
        # set provenance=MOCK with status=available is how a fabricated number
        # reaches a dashboard that only checks one of them.
        if self.provenance == MOCK and self.status != MOCK:
            raise ContractError(
                f"{self.name}: a MOCK metric must carry status={MOCK!r}, got {self.status!r}.")
        if self.provenance == UNAVAILABLE and self.status != UNAVAILABLE:
            raise ContractError(
                f"{self.name}: an UNAVAILABLE metric must carry status={UNAVAILABLE!r}, "
                f"got {self.status!r}.")
        if self.status in (UNAVAILABLE, EMPTY) and self.value is not None:
            raise ContractError(
                f"{self.name}: status={self.status!r} must carry value=None, got {self.value!r}. "
                "An uncomputable metric has no value — rendering it as 0 is the defect "
                "this contract exists to prevent.")
        if self.status in (UNAVAILABLE, MOCK) and not self.note:
            raise ContractError(
                f"{self.name}: status={self.status!r} requires a `note` explaining why. "
                "A metric a user cannot see needs a reason they can read.")

    @property
    def trustworthy(self) -> bool:
        """May a consumer act on this number as a fact about production?"""
        return self.provenance in (REAL, DERIVED) and self.status == AVAILABLE

    def as_dict(self) -> dict:
        out = {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "status": self.status,
            "provenance": self.provenance,
            "calculated_at": self.calculated_at,
        }
        if self.period is not None:
            out["period"] = self.period.as_dict()
        if self.source:
            out["source"] = self.source
        if self.note:
            out["note"] = self.note
        if self.comparison is not None:
            out["comparison"] = self.comparison
        return out


def real(name, value, **kw) -> Metric:
    """A number read directly from persisted production records."""
    return Metric(name=name, value=value, provenance=REAL, **kw)


def derived(name, value, **kw) -> Metric:
    """A number computed deterministically from persisted production records."""
    return Metric(name=name, value=value, provenance=DERIVED, **kw)


def mock(name, value, note, **kw) -> Metric:
    """A fabricated number, carried explicitly rather than removed.

    PH3.8 does not delete mock analytics — that is PH3.9's sprint, and ripping
    a number out of a dashboard without its replacement is a worse state than
    labelling it. What PH3.8 refuses to allow is a fabricated number that
    *looks* real: the value still ships (so nothing regresses), and it ships
    flagged, with the reason a reader needs.
    """
    return Metric(name=name, value=value, provenance=MOCK, status=MOCK, note=note, **kw)


def unavailable(name, note, **kw) -> Metric:
    """A legitimate metric whose source data does not exist. Never a zero."""
    kw.pop("value", None)
    return Metric(name=name, value=None, provenance=UNAVAILABLE,
                  status=UNAVAILABLE, note=note, **kw)


def empty(name, note="", **kw) -> Metric:
    """The calculation is sound and the dataset is legitimately empty.

    Distinct from UNAVAILABLE: "you have closed no trades yet" is a true fact
    about this user, whereas "we cannot compute revenue because no payment
    records exist anywhere" is a gap in the platform. Conflating them tells a
    new user the product is broken.
    """
    return Metric(name=name, value=None, provenance=DERIVED,
                  status=EMPTY, note=note, **kw)


def envelope(metrics, period=None, surface="") -> dict:
    """Assemble metrics into the standard analytics block.

    Returns a dict with a ``metrics`` map keyed by metric name plus the summary
    counts an operator needs at a glance: how many of these numbers are real,
    and how many are still fabricated. Those counts are what make a dashboard's
    honesty visible without reading every field.
    """
    if isinstance(period, str):
        period = resolve(period)
    items = list(metrics)
    return {
        "surface": surface,
        "period": period.as_dict() if period is not None else None,
        "generated_at": now_utc().isoformat(),
        "metrics": {m.name: m.as_dict() for m in items},
        "provenance_summary": {
            p: sum(1 for m in items if m.provenance == p) for p in PROVENANCE
        },
        "trustworthy": all(m.trustworthy for m in items) if items else True,
    }
