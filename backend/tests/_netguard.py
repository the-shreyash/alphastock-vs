"""Outbound-network guard for the hermetic backend test suite (PH3.1).

WHY THIS FILE EXISTS
--------------------
"Hermetic" was, before PH3.1, an aspiration enforced by nothing. A test could
call a route that quietly reached the real internet and still pass — the call
sites are wrapped in broad `try/except` blocks (correctly: a market-data
provider being down must not take the API down), so a live HTTP request and a
mocked one produce the same green tick.

A measurement on 2026-08-09 found three tests in the default suite opening live
TLS connections on every run:

  * ``test_ai_live_activity.py::test_scheduler_morning_job_broadcasts_run``
    → api.anthropic.com, Google Generative Language API
  * ``test_morning_report.py::test_morning_report_returns_report_object``
    → Yahoo Finance (6 endpoints)
  * ``test_trading_engine.py::test_partial_exit_endpoint``
    → api.anthropic.com, Google Generative Language API

None of them *asserted* on the result. They were paying real latency (the
default suite ran in 202s; 168s with network blocked), real API credits, and
importing real-world flakiness into a suite whose entire job is to be a
trustworthy signal.

WHAT IT DOES
------------
`install()` replaces `socket.socket.connect`/`connect_ex` with a wrapper that
raises `NetworkAccessBlocked` for any address that is not loopback. It is
patched at the `socket` layer rather than per-library because the escapes came
through three different clients (`aiohttp`, `httpx`, `requests` via yfinance) —
guarding each one is a list that goes stale the moment someone adds a fourth.

Loopback stays open: `TestClient` does not use a socket, but `socketpair`,
asyncio's self-pipe, and the DNS resolver's local paths do, and blocking those
breaks the event loop rather than the test.

WHAT IT IS NOT
--------------
It is not a sandbox. A test that genuinely wants the network can be marked
`integration` or `live`, and `conftest.py` will not install the guard for it.
The guard exists to make an accidental escape *loud*, not to make one
impossible.
"""
import socket

#: Hosts a hermetic test may still connect to. Loopback only.
_ALLOWED_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", ""})


class NetworkAccessBlocked(OSError):
    """Raised when a hermetic test attempts a non-loopback socket connection.

    Subclasses `OSError` deliberately. Application code that reaches the
    network already handles `OSError` (connection refused, DNS failure), so a
    blocked call takes the same offline fallback path the test is meant to be
    exercising — the test fails on a *wrong assertion*, which is diagnosable,
    rather than on an exotic exception escaping from inside a library.
    """


def _host_of(address):
    """Best-effort host extraction from any of socket's address forms."""
    if isinstance(address, (tuple, list)) and address:
        return address[0]
    return None


def _blocked(address):
    host = _host_of(address)
    if host is None:
        # AF_UNIX and friends: a filesystem path, never the internet.
        return False
    return str(host) not in _ALLOWED_HOSTS


def install(monkeypatch):
    """Block non-loopback socket connections for the duration of one test.

    Takes a `monkeypatch` fixture rather than patching globally so the guard is
    torn down deterministically after each test, including on failure.
    """
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def connect(self, address, *args, **kwargs):
        if _blocked(address):
            raise NetworkAccessBlocked(
                f"Hermetic test attempted an outbound connection to {address!r}. "
                "Mock the client, or mark the test `integration`/`live`. "
                "See docs/testing/TEST_ARCHITECTURE.md §External API isolation."
            )
        return real_connect(self, address, *args, **kwargs)

    def connect_ex(self, address, *args, **kwargs):
        if _blocked(address):
            raise NetworkAccessBlocked(
                f"Hermetic test attempted an outbound connection to {address!r}. "
                "Mock the client, or mark the test `integration`/`live`."
            )
        return real_connect_ex(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
