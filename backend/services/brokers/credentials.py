"""Authentication / configuration boundary for broker adapters.

WHAT WAS WRONG BEFORE D3
-------------------------
Every adapter read `os.environ` directly, and so did code that is not an
adapter: `BrokerEngine.start_stream` reached for `KITE_API_KEY` by name in order
to build a Zerodha WebSocket URL. That is a broker-specific secret name in the
engine, which means the engine cannot open a stream for a broker it was not
written to know about — the exact coupling the framework exists to remove.

WHAT THIS BOUNDARY GIVES
-------------------------
An adapter declares *which* environment variables carry its credentials
(:class:`BrokerCredentialSpec`); it never reads them. Everything that needs a
credential asks the adapter for a :class:`BrokerCredentials`, and the engine can
hand a stream its API key without knowing whose key it is.

That single indirection buys several things at once:

  * `is_configured()` stops being per-adapter boilerplate and becomes one
    correct implementation.
  * Secrets are read through exactly one function, which is where a future move
    to a secrets manager (SECRETS.md) plugs in — one change instead of one per
    broker.
  * Nothing outside this module needs to know that `KITE_API_KEY` is Zerodha's,
    which is what lets `server.py`, `BrokerEngine` and the stream manager stay
    broker-agnostic.

WHY VALUES ARE READ AT CALL TIME AND NEVER CACHED
--------------------------------------------------
Configuration is read on every access rather than snapshotted at import. Caching
would make credential rotation require a process restart, and it would make the
test suite — which sets these with `monkeypatch.setenv` — depend on import
order. Reading an environment variable is not a cost worth a cache.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class BrokerCredentialSpec:
    """Which environment variables carry a broker's credentials.

    Declared by the adapter as a class attribute. `extra_env` maps a canonical
    name onto a broker-specific variable for anything beyond the common three —
    a client id, a TOTP secret, a partner code — so a broker with an unusual
    configuration shape does not need a new mechanism, only a new entry.
    """

    api_key_env: str = ""
    api_secret_env: str = ""
    redirect_url_env: str = ""
    extra_env: Dict[str, str] = field(default_factory=dict)

    #: Which of api_key / api_secret must be present for the broker to be usable.
    #: A broker authenticating by client id + TOTP rather than key + secret sets
    #: this to match, instead of pretending to have a secret it does not use.
    required: Tuple[str, ...] = ("api_key", "api_secret")


@dataclass(frozen=True)
class BrokerCredentials:
    """Resolved credentials for one broker. Never logged, never serialized.

    There is deliberately no `as_dict()` and no `__repr__` override that would
    print values: this object exists to be passed, not displayed. SECURITY.md's
    "no credentials in logs" rule is easier to keep when the type makes leaking
    inconvenient.
    """

    broker: str
    api_key: str = ""
    api_secret: str = ""
    redirect_url: str = ""
    extra: Dict[str, str] = field(default_factory=dict)

    def is_complete(self, spec: BrokerCredentialSpec) -> bool:
        return all(bool(getattr(self, name, "")) for name in spec.required)

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return f"<BrokerCredentials broker={self.broker!r} configured={bool(self.api_key)}>"


def resolve_credentials(broker: str, spec: BrokerCredentialSpec) -> BrokerCredentials:
    """Read `spec` from the environment. The only place broker secrets are read."""
    return BrokerCredentials(
        broker=broker,
        api_key=_env(spec.api_key_env),
        api_secret=_env(spec.api_secret_env),
        redirect_url=_env(spec.redirect_url_env),
        extra={name: _env(var) for name, var in (spec.extra_env or {}).items()},
    )


def _env(name: Optional[str]) -> str:
    if not name:
        return ""
    return os.environ.get(name, "").strip()
