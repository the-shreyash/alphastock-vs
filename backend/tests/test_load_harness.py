"""Regression tests for the PH3.5 load-test harness contract.

WHAT THIS FILE PROTECTS
-----------------------
PH3.5 added exactly one piece of application code: an environment-driven origin
override for market-data requests (`services/real_market.py::yahoo_origin`).
Everything else the sprint produced lives outside the application — mocks, k6
scenarios, a seeder, a runner.

That one change carries two risks worth pinning, and they pull in opposite
directions:

1. **It must be inert by default.** If a future edit makes the override apply
   without the variable set — or changes the default host — production market
   data silently starts coming from somewhere else. That is the most damaging
   possible outcome of a load-testing sprint, so the default is asserted
   explicitly rather than assumed.

2. **It must actually work when set.** If it silently stopped taking effect, the
   next load test would quietly hammer the real Yahoo Finance: the brief (§14)
   forbids it, and nobody would notice, because a working provider produces the
   same green result as a working mock. This is the same failure shape PH3.1
   found when three "hermetic" tests were reaching the live internet and passing
   either way.

The tests use the *real* call sites rather than re-deriving the URLs, so a call
site that stops routing through the helper fails here instead of at the next
load test.
"""
from __future__ import annotations

import os

import pytest

from services import real_market
from services import stock_details


ENV_VAR = "MARKET_DATA_YAHOO_BASE"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every test starts with the variable unset.

    `tests/_testenv.py` does not clear it, and a developer who has exported it
    to point at a local mock would otherwise see the "default" assertions fail
    for a reason that has nothing to do with the code.
    """
    monkeypatch.delenv(ENV_VAR, raising=False)


# --------------------------------------------------------------------------- #
# Default behaviour — the important half                                        #
# --------------------------------------------------------------------------- #
class TestOriginDefaultsToRealProvider:
    def test_query_hosts_unchanged_when_unset(self):
        assert real_market.yahoo_origin("query1") == "https://query1.finance.yahoo.com"
        assert real_market.yahoo_origin("query2") == "https://query2.finance.yahoo.com"

    def test_default_host_is_query1(self):
        """Call sites that omit the host must still get a real Yahoo origin."""
        assert real_market.yahoo_origin() == "https://query1.finance.yahoo.com"

    def test_not_reported_as_overridden_when_unset(self):
        assert real_market.yahoo_origin_overridden() is False

    def test_empty_string_is_treated_as_unset(self, monkeypatch):
        """An empty value must not resolve to an empty origin.

        `MARKET_DATA_YAHOO_BASE=` in a compose file or a `.env` is a very
        ordinary way to write "not configured", and reading it literally would
        build the URL `/v8/finance/chart/...` — a request to the application's
        own host. That would not fail loudly; it would 404 into the same broad
        `except` every provider call site already has, and market data would
        simply go quiet.
        """
        monkeypatch.setenv(ENV_VAR, "")
        assert real_market.yahoo_origin("query1") == "https://query1.finance.yahoo.com"
        assert real_market.yahoo_origin_overridden() is False

    def test_whitespace_only_is_treated_as_unset(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "   ")
        assert real_market.yahoo_origin("query1") == "https://query1.finance.yahoo.com"


# --------------------------------------------------------------------------- #
# Override behaviour                                                            #
# --------------------------------------------------------------------------- #
class TestOriginOverride:
    def test_override_replaces_every_host(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "http://127.0.0.1:9020")
        for host in ("query1", "query2", "fc"):
            assert real_market.yahoo_origin(host) == "http://127.0.0.1:9020"

    def test_trailing_slash_is_stripped(self, monkeypatch):
        """Callers concatenate a path directly, so a trailing slash would build
        `http://host//v8/...`. Tolerated rather than rejected because writing
        the trailing slash is the single most likely way to configure this."""
        monkeypatch.setenv(ENV_VAR, "http://127.0.0.1:9020/")
        assert real_market.yahoo_origin("query1") == "http://127.0.0.1:9020"

    def test_reported_as_overridden(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "http://127.0.0.1:9020")
        assert real_market.yahoo_origin_overridden() is True

    def test_read_at_call_time_not_import_time(self, monkeypatch):
        """Configuration is materialised into `os.environ` by
        `security.secrets.load_secrets()` during boot — which happens *after*
        this module is imported. A value captured at import would therefore be
        the pre-boot one. Same reason `redis_client.redis_url()` is a function.
        """
        assert real_market.yahoo_origin("query1") == "https://query1.finance.yahoo.com"
        monkeypatch.setenv(ENV_VAR, "http://127.0.0.1:9999")
        assert real_market.yahoo_origin("query1") == "http://127.0.0.1:9999"
        monkeypatch.delenv(ENV_VAR)
        assert real_market.yahoo_origin("query1") == "https://query1.finance.yahoo.com"


# --------------------------------------------------------------------------- #
# Call-site coverage — that the helper is actually USED                         #
# --------------------------------------------------------------------------- #
class TestNoHardcodedProviderHosts:
    """A helper nothing calls protects nothing.

    Asserted against the module source rather than by exercising each call site,
    because exercising them needs the network these tests exist to keep off.
    The check is narrow on purpose: it looks for the literal Yahoo hostname in
    an f-string URL, which is exactly what the override replaced.
    """

    @staticmethod
    def _url_lines(module):
        import inspect as _inspect
        src = _inspect.getsource(module)
        return [
            ln.strip() for ln in src.splitlines()
            if "finance.yahoo.com" in ln and not ln.strip().startswith("#")
            and not ln.strip().startswith("*") and '"""' not in ln
        ]

    def test_real_market_has_one_hardcoded_host_and_it_is_the_default(self):
        lines = self._url_lines(real_market)
        # Exactly one: the fallback inside `yahoo_origin` itself.
        assert len(lines) == 1, (
            "a Yahoo URL is being built without going through yahoo_origin() — "
            "a load test would send that request to the real provider:\n  "
            + "\n  ".join(lines)
        )
        assert lines[0].startswith("return f\"https://{host}.finance.yahoo.com\"")

    def test_stock_details_builds_no_yahoo_url_directly(self):
        lines = self._url_lines(stock_details)
        assert lines == [], (
            "services/stock_details.py builds a Yahoo URL directly:\n  "
            + "\n  ".join(lines)
        )


# --------------------------------------------------------------------------- #
# The AI side needs no application code, and that is worth pinning too          #
# --------------------------------------------------------------------------- #
class TestAiProviderRedirectionNeedsNoAppChange:
    def test_claude_provider_reads_key_from_environment_at_call_time(self, monkeypatch):
        """PH3.5 redirects AI traffic with the SDK's own `ANTHROPIC_BASE_URL`,
        which works only if `is_configured()` is true — i.e. only if the key is
        read live rather than captured at import. If that ever changes, the load
        harness would silently fall through to the offline simulated provider
        and the "AI under load" results would be measuring nothing."""
        from services import claude_provider

        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        assert claude_provider.is_configured() is False

        monkeypatch.setenv("ANTHROPIC_API_KEY", "loadtest-mock-key-not-a-real-credential")
        assert claude_provider.is_configured() is True
