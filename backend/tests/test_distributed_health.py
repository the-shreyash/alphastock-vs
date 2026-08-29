"""Sprint D5.8 — DB-1: distributed provider/broker health and recovery state.

WHAT THIS FILE PINS
-------------------
D5.1–D5.7 each built a correct mechanism on a wrong assumption: that there is
one process. With N uvicorn workers, ``BrokerHealth``, ``ProviderHealth`` and
D5.7's failure cool-down are N independent opinions about the same remote
dependency, and the consequence that matters is not the cosmetic one. D5.7
promised **at most one recovery trial per cool-down**; per worker, that is N
trials per cool-down against a provider the deployment already knows is down.

So the success condition for this sprint is not "Redis is involved". It is:

  * two workers observe **one** health state — a failure recorded by A is seen
    by B, and B cannot reset A's streak;
  * two workers resolving in the same instant spend **one** trial;
  * a failed trial advances **one** ladder that both workers then observe;
  * users, providers, brokers and the provider/broker namespaces stay isolated;
  * and **none of the semantics D5.1–D5.7 established change** — an empty
    success still fails to clear the streak, an auth failure still stays out of
    the broker state machine, a re-admitted provider still ranks last, and a
    live socket's evidence still never leaves the process that holds it.

HOW THE MULTI-WORKER TESTS WORK, AND WHY THEY USE A REAL REDIS
---------------------------------------------------------------
A "worker" here is an independent object graph — its own ``SourceManager``, its
own ``ProviderHealthRecovery``, its own provider instances — sharing one Redis,
which is exactly what two processes are as far as this state is concerned. A
mocked Redis would prove nothing about the one property the sprint is for:
atomicity is a *server-side* guarantee, and a double that answers the way the
author expected is the failure mode the PH2 certification named.

They therefore skip when no Redis is reachable. Everything that can be pinned
without one — the key model, the failure mode, the durations, the opt-outs — is
hermetic and always runs.

NO TEST HERE OPENS A BROKER SOCKET OR REACHES A MARKET API.
LIVE VALIDATION WAS NOT PERFORMED.
"""

import asyncio
import ast
import logging
import os
import pathlib
import re
import uuid

import pytest

from infrastructure import health_state, redis_client
from infrastructure.health_state import (
    BROKER,
    HEALTH_STATE_TTL_SECONDS,
    PROVIDER,
    TRIAL_LEASE_SECONDS,
    HealthKey,
    SharedHealthStore,
    broker_key,
    provider_key,
)
from services.brokers import health as broker_health
from services.brokers.health import BrokerConnectionState, BrokerHealth
from services.market_engine.providers import (
    HEALTH_PROBE_BASE_DELAY,
    HEALTH_PROBE_MAX_DELAY,
    Capability,
    ProviderHealthRecovery,
    ProviderRegistry,
    ProviderState,
    ResolutionContext,
    StreamingTickProvider,
)
from services.market_engine.providers.base import (
    DEGRADED_AFTER_FAILURES,
    DOWN_AFTER_FAILURES,
)
from services.market_engine.source_manager import SourceManager
from tests.test_provider_health_recovery import FlakyPollingProvider, run

BACKEND = pathlib.Path(__file__).resolve().parent.parent

#: The modules D5.8 wrote or changed. Every structural sweep below reads exactly
#: this list, so a module added to the mechanism later is either added here or is
#: visibly missing.
D58_MODULES = (
    "infrastructure/health_state.py",
    "services/market_engine/providers/health_recovery.py",
    "services/market_engine/providers/base.py",
    "services/market_engine/source_manager.py",
    "services/market_engine/gateway.py",
    "services/brokers/health.py",
    "services/brokers/gateway.py",
)

#: Live-looking, and fake. Used as the Redis password so a leak of connection
#: credentials into a key, a value or a log line is findable by string search.
FAKE_REDIS_PASSWORD = "S3cr3t-Redis-Pa55w0rd-9f2b"


# ==================================================================
# Redis discovery
# ==================================================================


def _candidate_urls():
    """Where a test Redis might be, most explicit first.

    ``REDIS_TEST_URL`` is the supported way to point this suite at one. The two
    fallbacks are the port ``docker-compose.yml`` publishes and the port a
    throwaway ``docker run -p 6399:6379 redis`` lands on, so a developer who has
    either running gets the multi-worker coverage without configuring anything.

    Database 15 in every case: these tests write real keys, and writing them into
    a developer's working database would be rude at best.
    """
    explicit = os.environ.get("REDIS_TEST_URL", "").strip()
    if explicit:
        return [explicit]
    urls = []
    for host in ("127.0.0.1:6399", "127.0.0.1:6379"):
        # Password-first, so that when a password-protected Redis is available
        # the whole suite runs over a connection whose credentials *could* leak
        # and the sweep at the bottom of this file is searching a keyspace that
        # had something to leak. A bare Redis still works; the sweep is then
        # weaker and says so.
        urls.append(f"redis://:{FAKE_REDIS_PASSWORD}@{host}/15")
        urls.append(f"redis://{host}/15")
    return urls


def _reachable_url():
    try:
        import redis.asyncio as aioredis
    except Exception:  # pragma: no cover - redis-py absent
        return None

    async def probe(url):
        client = aioredis.from_url(url, socket_connect_timeout=0.5, socket_timeout=0.5)
        try:
            await client.ping()
            return True
        except Exception:
            return False
        finally:
            await client.aclose()

    for url in _candidate_urls():
        try:
            if asyncio.new_event_loop().run_until_complete(probe(url)):
                return url
        except Exception:
            continue
    return None


REDIS_URL = _reachable_url()

needs_redis = pytest.mark.skipif(
    REDIS_URL is None,
    reason=(
        "no Redis reachable — set REDIS_TEST_URL, or run "
        "`docker run -d --rm -p 6399:6379 redis:7-alpine`. The multi-worker "
        "properties of D5.8 are server-side guarantees and are deliberately not "
        "asserted against a double."
    ),
)


@pytest.fixture
def store(monkeypatch):
    """A real shared store, on a real Redis.

    Discovery prefers an authenticated URL (see :func:`_candidate_urls`), so
    where one is available every assertion in this file runs against a
    connection whose credentials could leak — which is what makes the sweep at
    the bottom of this file an actual search rather than a formality.
    """
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    redis_client.manager.reset_for_tests()
    yield SharedHealthStore()
    redis_client.manager.reset_for_tests()


@pytest.fixture
def unique():
    """A name no other test or run has used, so tests never share a key."""
    return lambda prefix="p": f"d58-{prefix}-{uuid.uuid4().hex[:12]}"


class Worker:
    """One process's view of the shared state.

    Its own registry, its own ``SourceManager``, its own cool-down register and
    its own provider objects — everything D5 keeps in memory — over one shared
    store. That is precisely what a second uvicorn worker is.
    """

    def __init__(self, store, *, name, owner=None, failing=True):
        self.registry = ProviderRegistry()
        self.recovery = ProviderHealthRecovery(store=store)
        self.manager = SourceManager(
            self.registry, health_recovery=self.recovery, store=store
        )
        self.provider = FlakyPollingProvider(name, failing=failing, owner_user_id=owner)
        self.registry.register(self.provider)

    def context(self, owner=None):
        return ResolutionContext(user_id=owner) if owner else None

    async def fail(self, times=1):
        for _ in range(times):
            await self.manager.record_failure_shared(self.provider, RuntimeError("503"))

    async def succeed(self, *, empty=False):
        await self.manager.record_success_shared(self.provider, empty=empty)

    async def resolve(self, owner=None):
        """One full gateway-shaped resolution: prepare, then resolve."""
        ctx = self.context(owner)
        shared = await self.manager.prepare(Capability.QUOTES, ctx)
        return self.manager.resolve_feed(Capability.QUOTES, ctx, shared=shared)

    async def read(self):
        ok, records = await self.manager._store.read_many(
            [SourceManager._store_key(self.provider)]
        )
        assert ok
        return records[SourceManager._store_key(self.provider)]


# ==================================================================
# Shared state — the core DB-1 property
# ==================================================================


@needs_redis
def test_a_failure_recorded_by_one_worker_is_seen_by_another(store, unique):
    """The whole of DB-1 in one assertion.

    Before D5.8 worker B's counter was zero no matter what worker A had seen,
    which is why a broken provider needed ``8 x N`` failures to be excluded
    rather than 8.
    """
    name = unique()

    async def scenario():
        a, b = Worker(store, name=name), Worker(store, name=name)
        await a.fail(times=3)

        assert a.provider.health().state is ProviderState.DEGRADED
        # B has recorded nothing at all and still learns the truth, because the
        # resolution prelude reads the shared record.
        assert b.provider.health().state is ProviderState.UNKNOWN
        await b.resolve()
        assert b.provider.health().state is ProviderState.DEGRADED
        assert b.provider.health().consecutive_failures == 3

    run(scenario())


@needs_redis
def test_a_second_worker_cannot_reset_the_first_workers_failure_streak(store, unique):
    """B's *reads* must not become writes.

    A worker that resolved a provider and then merged its own zeroed counters
    back would erase evidence it never had — the silent lost update this
    sprint's atomicity requirement exists to prevent.
    """
    name = unique()

    async def scenario():
        a, b = Worker(store, name=name), Worker(store, name=name)
        await a.fail(times=5)
        for _ in range(3):
            await b.resolve()

        record = await a.read()
        assert record.consecutive_failures == 5
        assert record.state == ProviderState.DEGRADED.value

    run(scenario())


@needs_redis
def test_a_success_on_one_worker_recovers_the_provider_for_every_worker(store, unique):
    name = unique()

    async def scenario():
        a, b = Worker(store, name=name), Worker(store, name=name)
        await a.fail(times=DOWN_AFTER_FAILURES)
        await b.resolve()
        assert b.provider.health().state is ProviderState.DOWN

        await a.succeed()
        await b.resolve()
        assert b.provider.health().state is ProviderState.UP
        assert b.provider.health().consecutive_failures == 0

    run(scenario())


@needs_redis
def test_a_provider_driven_down_by_one_worker_is_excluded_by_the_other(store, unique):
    """The exclusion is the point, and it is the *resolution* that must show it.

    Worker B has made no failing call. It must still refuse to offer the
    provider, because the eight failures that created DOWN are the deployment's
    evidence and not worker A's private opinion.
    """
    name = unique()

    async def scenario():
        a, b = Worker(store, name=name), Worker(store, name=name)
        await a.fail(times=DOWN_AFTER_FAILURES)

        resolution = await b.resolve()
        assert not resolution.available
        assert b.provider.health().state is ProviderState.DOWN

    run(scenario())


@needs_redis
def test_shared_health_survives_a_worker_restart(store, unique):
    """State written by a process that is gone is still the platform's state.

    A restarted worker builds brand-new provider objects with UNKNOWN health.
    Before D5.8 that erased everything the deployment knew; now the first
    resolution re-reads it.
    """
    name = unique()

    async def scenario():
        first = Worker(store, name=name)
        await first.fail(times=DOWN_AFTER_FAILURES)
        del first

        restarted = Worker(store, name=name)
        assert restarted.provider.health().state is ProviderState.UNKNOWN
        await restarted.resolve()
        assert restarted.provider.health().state is ProviderState.DOWN

    run(scenario())


# ==================================================================
# The recovery trial — one per cool-down, across the whole deployment
# ==================================================================


@needs_redis
def test_two_workers_resolving_at_once_spend_exactly_one_recovery_trial(store, unique):
    """LIM-D5.7-2, closed.

    Both workers see the same DOWN provider with the same expired cool-down and
    resolve in the same instant. Exactly one may be offered the provider; the
    other must be told the trial is taken.
    """
    name = unique()

    async def scenario():
        a, b = Worker(store, name=name), Worker(store, name=name)
        await a.fail(times=DOWN_AFTER_FAILURES)
        # Arm the shared cool-down, then make it due without waiting a minute.
        await a.resolve()
        await _make_trial_due(store, provider_key(name))

        claims = await asyncio.gather(
            a.recovery.claim_due([a.provider]),
            b.recovery.claim_due([b.provider]),
        )
        granted = [c for c in claims if c.granted]
        assert len(granted) == 1, (
            "both workers were offered the same trial — DB-1's whole point is "
            "that one logical trial is spent once"
        )
        assert all(c.distributed for c in claims)

    run(scenario())


@needs_redis
def test_the_worker_that_did_not_win_the_trial_does_not_offer_the_provider(store, unique):
    """The claim has to reach *resolution*, not just the register.

    A claim that was refused but whose provider still turned up in the failover
    chain would spend the trial anyway, one layer down.
    """
    name = unique()

    async def scenario():
        a, b = Worker(store, name=name), Worker(store, name=name)
        await a.fail(times=DOWN_AFTER_FAILURES)
        await a.resolve()
        await _make_trial_due(store, provider_key(name))

        first, second = await asyncio.gather(a.resolve(), b.resolve())
        offered = [r for r in (first, second) if r.available]
        assert len(offered) == 1

    run(scenario())


@needs_redis
def test_a_failed_trial_advances_one_ladder_that_every_worker_observes(store, unique):
    name = unique()

    async def scenario():
        a, b = Worker(store, name=name), Worker(store, name=name)
        await a.fail(times=DOWN_AFTER_FAILURES)
        await a.resolve()
        await _make_trial_due(store, provider_key(name))

        claim = await a.recovery.claim_due([a.provider])
        assert claim.granted
        # The trial ran and failed: the ladder climbs once, for everybody.
        await a.recovery.note_probe_failed_shared(a.provider)

        after = await b.recovery.claim_due([b.provider])
        assert not after.granted
        ok, claims = await store.claim_trials(
            [provider_key(name)],
            base_delay=HEALTH_PROBE_BASE_DELAY,
            max_delay=HEALTH_PROBE_MAX_DELAY,
        )
        assert ok
        claim_state = claims[provider_key(name)]
        assert claim_state.outcome == health_state.TOO_SOON
        # Second rung: the base delay doubled, and doubled once — not once per
        # worker that happened to look.
        assert claim_state.due_in_seconds > HEALTH_PROBE_BASE_DELAY

    run(scenario())


@needs_redis
def test_a_successful_trial_clears_the_cool_down_for_every_worker(store, unique):
    name = unique()

    async def scenario():
        a, b = Worker(store, name=name), Worker(store, name=name)
        await a.fail(times=DOWN_AFTER_FAILURES)
        await a.resolve()
        await _make_trial_due(store, provider_key(name))
        assert (await a.recovery.claim_due([a.provider])).granted

        await a.succeed()

        # B sees a healthy provider and no cool-down at all: the next claim
        # *arms* a fresh one rather than reporting an outstanding ladder.
        await b.resolve()
        assert b.provider.health().state is ProviderState.UP
        ok, claims = await store.claim_trials(
            [provider_key(name)],
            base_delay=HEALTH_PROBE_BASE_DELAY,
            max_delay=HEALTH_PROBE_MAX_DELAY,
        )
        assert ok and claims[provider_key(name)].outcome == health_state.ARMED

    run(scenario())


@needs_redis
def test_an_unreached_offer_costs_nothing_the_way_d57_says(store, unique):
    """The D5.7 rule the lease exists to preserve.

    "A provider that is offered and never reached — because something healthier
    answered — costs nothing at all." So a granted claim that produces no
    evidence must not climb the ladder; only ``note_probe_failed_shared`` does.
    """
    name = unique()

    async def scenario():
        a = Worker(store, name=name)
        await a.fail(times=DOWN_AFTER_FAILURES)
        await a.resolve()
        await _make_trial_due(store, provider_key(name))

        before = await _trial_attempts(store, provider_key(name))
        assert (await a.recovery.claim_due([a.provider])).granted
        assert await _trial_attempts(store, provider_key(name)) == before

    run(scenario())


# ==================================================================
# Isolation
# ==================================================================


@needs_redis
def test_two_users_on_the_same_provider_name_have_independent_health(store, unique):
    """Two accounts' feeds from one broker are two subjects.

    The key carries the owner, so this holds even though the two providers share
    a name — which is the case the *naming convention* alone would have got
    wrong.
    """
    name = unique()

    async def scenario():
        alice = Worker(store, name=name, owner="user-alice")
        bob = Worker(store, name=name, owner="user-bob")
        await alice.fail(times=DOWN_AFTER_FAILURES)

        await bob.resolve(owner="user-bob")
        assert bob.provider.health().state is ProviderState.UNKNOWN
        assert (await bob.read()).total_calls == 0

    run(scenario())


@needs_redis
def test_two_providers_for_one_user_have_independent_health(store, unique):
    async def scenario():
        one = Worker(store, name=unique("a"), owner="user-alice")
        two = Worker(store, name=unique("b"), owner="user-alice")
        await one.fail(times=DOWN_AFTER_FAILURES)

        await two.resolve(owner="user-alice")
        assert two.provider.health().state is ProviderState.UNKNOWN

    run(scenario())


@needs_redis
def test_a_broker_and_a_provider_of_the_same_name_never_collide(store, unique):
    """Two namespaces, one string.

    A broker named ``zerodha`` and a market-data provider named ``zerodha`` are
    different subjects with different thresholds; a key model that dropped
    ``kind`` would merge them.
    """
    name = unique()

    async def scenario():
        for _ in range(DOWN_AFTER_FAILURES):
            await store.record(
                broker_key(name), health_state.FAILURE, stamp="t",
                degraded_after=DEGRADED_AFTER_FAILURES, down_after=DOWN_AFTER_FAILURES,
            )
        ok, records = await store.read_many([broker_key(name), provider_key(name)])
        assert ok
        assert records[broker_key(name)].state == "down"
        assert records[provider_key(name)].state == "unknown"

    run(scenario())


@needs_redis
def test_two_brokers_have_independent_health(store, unique):
    async def scenario():
        first, second = unique("brokerA"), unique("brokerB")
        for _ in range(DOWN_AFTER_FAILURES):
            await broker_health.record_failure_shared(BrokerHealth(broker=first), "E1")
        health = BrokerHealth(broker=second)
        await broker_health.record_failure_shared(health, "E1")
        assert health.state is BrokerConnectionState.UNKNOWN
        assert health.consecutive_failures == 1

    run(scenario())


# ==================================================================
# Broker health
# ==================================================================


@needs_redis
def test_broker_health_reaches_down_on_the_deployments_failures_not_one_workers(
    store, unique
):
    """Eight failures, spread across two workers, still means DOWN.

    Before D5.8 this took eight failures *per worker*, so a broker outage during
    a rolling deploy was reported as healthy by every replica that had made
    fewer than eight calls.
    """
    async def scenario():
        name = unique("broker")
        worker_a, worker_b = BrokerHealth(broker=name), BrokerHealth(broker=name)
        for i in range(DOWN_AFTER_FAILURES):
            target = worker_a if i % 2 == 0 else worker_b
            await broker_health.record_failure_shared(target, "GATEWAY_TIMEOUT")

        # The worker that recorded the eighth failure knows immediately; the
        # other learns on its next mutation or read, which is what
        # `refresh_shared` is for and what the Admin Portal now calls.
        assert worker_b.state is BrokerConnectionState.DOWN
        assert worker_b.consecutive_failures == DOWN_AFTER_FAILURES
        assert worker_a.state is BrokerConnectionState.DEGRADED
        assert await broker_health.refresh_shared(worker_a)
        assert worker_a.state is BrokerConnectionState.DOWN
        assert worker_a.consecutive_failures == DOWN_AFTER_FAILURES

    run(scenario())


@needs_redis
def test_the_admin_read_path_reports_the_deployments_health_not_one_workers(
    store, unique
):
    """DB-1's original complaint, closed at the surface that raised it.

    TASK.md's DB-1 entry is about the Admin Portal seeing "whichever worker
    answers". `health_shared` is the read that fixes it: it adopts the shared
    record before rendering, so two refreshes cannot disagree.
    """
    from services.brokers.gateway import broker_gateway

    async def scenario():
        broker = broker_gateway.list_brokers()[0]["name"]
        adapter = broker_gateway.resolve(broker)
        adapter.health.reset()
        await store.forget(broker_key(broker))
        try:
            elsewhere = BrokerHealth(broker=broker)
            for _ in range(DOWN_AFTER_FAILURES):
                await broker_health.record_failure_shared(elsewhere, "E_CODE")

            assert broker_gateway.health(broker)["state"] == "unknown"
            rendered = await broker_gateway.health_shared(broker)
            assert rendered["state"] == BrokerConnectionState.DOWN.value
            assert rendered["consecutive_failures"] == DOWN_AFTER_FAILURES
        finally:
            adapter.health.reset()
            await store.forget(broker_key(broker))

    run(scenario())


@needs_redis
def test_an_auth_failure_still_stays_out_of_the_broker_state_machine(store, unique):
    """The load-bearing rule of ``services/brokers/health.py``, in Redis.

    Kite invalidates every token at 06:00 IST. If token expiry counted against
    health, sharing the counter would make the daily false outage *worse* — one
    deployment-wide DOWN instead of one per worker.
    """
    async def scenario():
        name = unique("broker")
        health = BrokerHealth(broker=name)
        for _ in range(DOWN_AFTER_FAILURES * 2):
            await broker_health.record_auth_failure_shared(health)

        assert health.state is BrokerConnectionState.UNKNOWN
        assert health.total_auth_failures == DOWN_AFTER_FAILURES * 2
        assert health.total_errors == 0
        assert health.consecutive_failures == 0

    run(scenario())


@needs_redis
def test_an_empty_success_still_does_not_clear_the_failure_streak(store, unique):
    """D1's rule, enforced inside the atomic section.

    A provider answering 200-with-no-data is not healthy, and if the shared
    script had cleared the streak the mistake would now be deployment-wide.
    """
    name = unique()

    async def scenario():
        a = Worker(store, name=name)
        await a.fail(times=DOWN_AFTER_FAILURES)
        await a.succeed(empty=True)

        record = await a.read()
        assert record.state == ProviderState.DOWN.value
        assert record.consecutive_failures == DOWN_AFTER_FAILURES
        assert record.total_empty == 1

    run(scenario())


# ==================================================================
# Concurrency
# ==================================================================


@needs_redis
def test_two_simultaneous_failures_produce_two_increments(store, unique):
    """The read-modify-write this design refuses to use, falsified.

    With ``GET`` / modify / ``SET`` the two coroutines below read the same value
    and write the same value, and one failure disappears. Every increment lives
    inside one Lua script instead, so it cannot.
    """
    name = unique()

    async def scenario():
        a, b = Worker(store, name=name), Worker(store, name=name)
        await asyncio.gather(a.fail(), b.fail())
        record = await a.read()
        assert record.consecutive_failures == 2
        assert record.total_calls == 2
        assert record.total_errors == 2

    run(scenario())


@needs_redis
def test_many_simultaneous_failures_reach_down_exactly_at_the_threshold(store, unique):
    name = unique()

    async def scenario():
        workers = [Worker(store, name=name) for _ in range(DOWN_AFTER_FAILURES)]
        await asyncio.gather(*(w.fail() for w in workers))
        record = await workers[0].read()
        assert record.consecutive_failures == DOWN_AFTER_FAILURES
        assert record.state == ProviderState.DOWN.value

    run(scenario())


@needs_redis
def test_a_failure_racing_a_success_loses_neither_call(store, unique):
    """Order is not asserted — accounting is.

    Which of the two lands second is genuinely undefined and either outcome is
    correct. What must never happen is a call vanishing, or a state that belongs
    to neither ordering.
    """
    name = unique()

    async def scenario():
        a, b = Worker(store, name=name), Worker(store, name=name)
        await a.fail(times=DEGRADED_AFTER_FAILURES)
        await asyncio.gather(a.fail(), b.succeed())

        record = await a.read()
        assert record.total_calls == DEGRADED_AFTER_FAILURES + 2
        assert record.state in {ProviderState.UP.value, ProviderState.DEGRADED.value}
        if record.state == ProviderState.UP.value:
            assert record.consecutive_failures == 0
        else:
            assert record.consecutive_failures == DEGRADED_AFTER_FAILURES + 1

    run(scenario())


@needs_redis
def test_four_workers_claiming_at_once_still_spend_one_trial(store, unique):
    """Two is the smallest race; four is the one that catches a check-then-set
    whose window is wide enough to admit a third."""
    name = unique()

    async def scenario():
        workers = [Worker(store, name=name) for _ in range(4)]
        await workers[0].fail(times=DOWN_AFTER_FAILURES)
        await workers[0].resolve()
        await _make_trial_due(store, provider_key(name))

        claims = await asyncio.gather(
            *(w.recovery.claim_due([w.provider]) for w in workers)
        )
        assert sum(1 for c in claims if c.granted) == 1

    run(scenario())


@needs_redis
def test_a_recovery_claim_racing_a_new_failure_charges_the_ladder_once(store, unique):
    name = unique()

    async def scenario():
        a, b = Worker(store, name=name), Worker(store, name=name)
        await a.fail(times=DOWN_AFTER_FAILURES)
        await a.resolve()
        await _make_trial_due(store, provider_key(name))

        before = await _trial_attempts(store, provider_key(name))
        await asyncio.gather(a.recovery.claim_due([a.provider]), b.fail())
        # One failure while DOWN, so exactly one rung — the claim itself never
        # charges.
        assert await _trial_attempts(store, provider_key(name)) == before + 1

    run(scenario())


@needs_redis
def test_a_claim_on_an_expired_key_arms_a_fresh_cool_down_rather_than_granting(
    store, unique
):
    """Expiration racing a claim.

    A key that ages out mid-incident must not be read as "due" — that would turn
    a TTL into a free trial. The script's first branch treats an absent record as
    first sight: armed, and refused.
    """
    name = unique()

    async def scenario():
        a = Worker(store, name=name)
        await a.fail(times=DOWN_AFTER_FAILURES)
        await a.resolve()
        await _make_trial_due(store, provider_key(name))
        client = await redis_client.get_client()
        await client.delete(provider_key(name).probe_key)

        claims = await a.recovery.claim_due([a.provider])
        assert not claims.granted

    run(scenario())


# ==================================================================
# The Python state machine is the oracle for the Lua one
# ==================================================================


BROKER_SEQUENCES = (
    ("success",),
    ("failure",) * DEGRADED_AFTER_FAILURES,
    ("failure",) * DOWN_AFTER_FAILURES,
    ("failure", "failure", "success", "failure"),
    ("auth", "auth", "failure", "auth", "success"),
    ("failure",) * (DOWN_AFTER_FAILURES + 3) + ("success",),
)


@needs_redis
@pytest.mark.parametrize("sequence", BROKER_SEQUENCES, ids=lambda s: "-".join(s)[:40])
def test_the_shared_broker_state_machine_agrees_with_the_local_one(
    store, unique, sequence
):
    """The parity test that keeps a second implementation honest.

    The transitions had to be expressed twice — once in Python for the local
    fallback, once in Lua so they can run atomically. A second expression is a
    second thing to get wrong, so it is not trusted: the same event sequence goes
    through both and the resulting snapshots must be identical. The Python
    version is the oracle, because it is the one D3 shipped and every earlier
    test already pins.
    """
    async def scenario():
        name = unique("broker")
        local = BrokerHealth(broker=name)
        shared = BrokerHealth(broker=name)
        for event in sequence:
            if event == "success":
                local.record_success()
                await broker_health.record_success_shared(shared)
            elif event == "failure":
                local.record_failure("E_CODE")
                await broker_health.record_failure_shared(shared, "E_CODE")
            else:
                local.record_auth_failure()
                await broker_health.record_auth_failure_shared(shared)

        assert _comparable(local.as_dict()) == _comparable(shared.as_dict())

    run(scenario())


PROVIDER_SEQUENCES = (
    ("failure",) * DEGRADED_AFTER_FAILURES,
    ("failure",) * DOWN_AFTER_FAILURES,
    ("failure", "empty", "failure", "success"),
    ("empty", "empty", "failure", "empty"),
    ("failure",) * DOWN_AFTER_FAILURES + ("success", "failure"),
)


@needs_redis
@pytest.mark.parametrize("sequence", PROVIDER_SEQUENCES, ids=lambda s: "-".join(s)[:40])
def test_the_shared_provider_state_machine_agrees_with_the_local_one(
    store, unique, sequence
):
    name = unique()

    async def scenario():
        local = FlakyPollingProvider(name)
        shared = Worker(store, name=name)
        for event in sequence:
            if event == "success":
                local.record_success()
                await shared.succeed()
            elif event == "empty":
                local.record_success(empty=True)
                await shared.succeed(empty=True)
            else:
                local.record_failure(RuntimeError("503"))
                await shared.fail()

        assert _comparable(local.health().as_dict()) == _comparable(
            shared.provider.health().as_dict()
        )

    run(scenario())


@needs_redis
def test_the_shared_ladder_matches_the_python_ladder_rung_for_rung(store, unique):
    """``_delay`` exists twice — in Python and in Lua — and must agree.

    Checked through the base/max the store is asked for rather than by reading
    the script, so a change to either arithmetic breaks this.
    """
    name = unique()
    recovery = ProviderHealthRecovery(store=store)

    async def scenario():
        key = provider_key(name)
        await store.forget(key)
        for attempt in range(1, 6):
            ok, attempts = await store.note_trial_failed(
                key, base_delay=HEALTH_PROBE_BASE_DELAY, max_delay=HEALTH_PROBE_MAX_DELAY
            )
            assert ok and attempts == attempt
            ok, claims = await store.claim_trials(
                [key], base_delay=HEALTH_PROBE_BASE_DELAY, max_delay=HEALTH_PROBE_MAX_DELAY
            )
            assert ok
            expected = recovery._delay(attempt)
            # Within a second: the script measures from Redis's clock and the
            # round trip is real.
            assert abs(claims[key].due_in_seconds - expected) < 1.0

    run(scenario())


# ==================================================================
# Redis unavailable — the documented failure mode
# ==================================================================


def test_an_unconfigured_deployment_keeps_the_process_local_behaviour(monkeypatch):
    """No Redis is a supported deployment, not a degraded one.

    ``services/cache.py`` degrades to a dict, the readiness probe registers Redis
    ``critical=False``, and a single process needs none of this. So with
    ``REDIS_URL`` unset every mechanism must behave exactly as D5.7 shipped it.
    """
    monkeypatch.delenv("REDIS_URL", raising=False)
    redis_client.manager.reset_for_tests()
    store = SharedHealthStore()
    assert not store.enabled

    async def scenario():
        worker = Worker(store, name="local-only")
        await worker.fail(times=DOWN_AFTER_FAILURES)
        assert worker.provider.health().state is ProviderState.DOWN
        assert worker.recovery.probe_for(worker.provider) is not None

    run(scenario())
    redis_client.manager.reset_for_tests()


def test_redis_being_down_does_not_mark_every_provider_down(monkeypatch):
    """Fail-closed, refused explicitly.

    Redis is a non-critical dependency. A store that answered "DOWN" when it
    could not reach Redis would turn a cache blip into a total market-data
    outage — the cascading failure ``infrastructure/redis_client.py`` was written
    to prevent, arriving through the module that was only supposed to observe it.
    """
    monkeypatch.setenv("REDIS_URL", f"redis://:{FAKE_REDIS_PASSWORD}@127.0.0.1:6390/0")
    redis_client.manager.reset_for_tests()
    store = SharedHealthStore()

    async def scenario():
        worker = Worker(store, name="unreachable-redis")
        assert worker.provider.health().state is ProviderState.UNKNOWN
        await worker.resolve()
        assert worker.provider.health().state is ProviderState.UNKNOWN
        resolution = await worker.resolve()
        assert resolution.available

    run(scenario())
    redis_client.manager.reset_for_tests()


def test_a_single_failure_with_redis_down_is_still_a_single_failure(monkeypatch):
    """Degradation must stay proportional to evidence.

    The sharp form of "do not fail closed": it is not enough that a *healthy*
    provider survives a Redis outage. A provider with one real failure must be
    one failure from healthy, not eight. A store that answered "DOWN" — or a
    fallback that escalated because the *infrastructure* failed rather than
    because the provider did — would exclude the whole feed on a Redis blip.
    """
    monkeypatch.setenv("REDIS_URL", f"redis://:{FAKE_REDIS_PASSWORD}@127.0.0.1:6390/0")
    redis_client.manager.reset_for_tests()
    store = SharedHealthStore()

    async def scenario():
        worker = Worker(store, name="unreachable-redis-3")
        await worker.fail()
        health = worker.provider.health()
        assert health.consecutive_failures == 1
        assert health.state is ProviderState.UNKNOWN
        assert (await worker.resolve()).available

        await worker.fail(times=DEGRADED_AFTER_FAILURES - 1)
        assert worker.provider.health().state is ProviderState.DEGRADED
        assert (await worker.resolve()).available

    run(scenario())
    redis_client.manager.reset_for_tests()


def test_redis_being_down_does_not_mark_every_provider_up(monkeypatch):
    """Fail-open, refused too.

    The other half of the decision: an unreachable store must not throw away the
    evidence this worker holds in its hands. Local failures still demote, still
    exclude, and still arm a local cool-down.
    """
    monkeypatch.setenv("REDIS_URL", f"redis://:{FAKE_REDIS_PASSWORD}@127.0.0.1:6390/0")
    redis_client.manager.reset_for_tests()
    store = SharedHealthStore()

    async def scenario():
        worker = Worker(store, name="unreachable-redis-2")
        await worker.fail(times=DOWN_AFTER_FAILURES)
        assert worker.provider.health().state is ProviderState.DOWN
        resolution = await worker.resolve()
        assert not resolution.available
        # And the cool-down that D5.7 arms is armed locally, so this worker still
        # paces its own re-admission.
        assert worker.recovery.probe_for(worker.provider) is not None

    run(scenario())
    redis_client.manager.reset_for_tests()


def test_a_partial_claim_failure_is_reported_as_a_whole_failure(monkeypatch):
    """Half a claim set is worse than none.

    A caller that trusted the claims that succeeded would judge the rest on local
    state alone — which is the double-spend the claim exists to prevent, arriving
    through the error path instead of the happy one.
    """
    monkeypatch.setenv("REDIS_URL", f"redis://:{FAKE_REDIS_PASSWORD}@127.0.0.1:6390/0")
    redis_client.manager.reset_for_tests()
    store = SharedHealthStore()

    async def scenario():
        ok, claims = await store.claim_trials(
            [provider_key("a"), provider_key("b")],
            base_delay=HEALTH_PROBE_BASE_DELAY,
            max_delay=HEALTH_PROBE_MAX_DELAY,
        )
        assert not ok and claims == {}

    run(scenario())
    redis_client.manager.reset_for_tests()


# ==================================================================
# What deliberately stayed local
# ==================================================================


def test_a_streaming_feeds_health_is_never_shared():
    """The sharpest "do not over-distribute" case.

    A socket's health, readiness, probation and latency are evidence about one
    live link in one process. Publishing them would let a dead link's DOWN state
    be inherited by the fresh link another worker opened — the opposite of the
    rule D5.3 established and D5.5/D5.6 depend on.
    """
    feed = StreamingTickProvider("brokerfeed:nova:user-1", owner_user_id="user-1")
    assert feed.health_is_shared is False
    assert FlakyPollingProvider("baseline").health_is_shared is True


def test_a_streaming_feed_never_reaches_the_shared_store(monkeypatch):
    """Stated as behaviour, not as a flag.

    The opt-out has to be honoured on every path that writes, or the flag is
    decoration.
    """
    calls = []

    class Recording(SharedHealthStore):
        async def record(self, *a, **kw):
            calls.append(a)
            return False, None

        async def read_many(self, keys):
            calls.append(("read", keys))
            return False, {}

        async def claim_trials(self, keys, **kw):
            calls.append(("claim", keys))
            return False, {}

    store = Recording()
    feed = StreamingTickProvider("brokerfeed:nova:user-1", owner_user_id="user-1")
    registry = ProviderRegistry()
    registry.register(feed)
    manager = SourceManager(registry, store=store)

    async def scenario():
        await manager.record_failure_shared(feed, RuntimeError("boom"))
        await manager.record_success_shared(feed)

    run(scenario())
    assert calls == [], f"a live socket's health reached the shared store: {calls}"


def test_the_d56_reprobe_register_is_deliberately_not_distributed():
    """Documented, because leaving it local is a decision and not an oversight.

    ``RecoveryRegister`` records the withdrawals *this worker's* sockets suffered,
    and a re-probe is one ordinary attach. Sharing it would let worker B attach a
    channel whose stream belongs to worker A — two sockets for one
    ``(user, broker, channel)``, which is a worse failure than the one DB-1 fixes.
    """
    source = (BACKEND / "services" / "brokers" / "recovery.py").read_text()
    assert "health_state" not in source
    text = (BACKEND / "services" / "brokers" / "recovery.py").read_text()
    assert "RecoveryRegister" in text


# ==================================================================
# Key model
# ==================================================================


def test_the_key_carries_kind_owner_and_name():
    key = provider_key("brokerfeed:nova:user-1", "user-1")
    assert key.redis_key == "sa:health:provider:user-1:brokerfeed:nova:user-1"
    assert provider_key("yahoo").redis_key == "sa:health:provider:-:yahoo"
    assert broker_key("zerodha").redis_key == "sa:health:broker:-:zerodha"


def test_the_trial_key_is_derived_from_the_health_key():
    """One scope, two records.

    A cool-down keyed without the owner while health is keyed with it would let
    one user's trial be consumed on another's behalf. Deriving the second key
    from the first makes that impossible rather than merely unlikely.
    """
    key = provider_key("feed", "user-alice")
    assert key.probe_key.endswith(":user-alice:feed")
    assert provider_key("feed", "user-bob").probe_key != key.probe_key
    assert broker_key("feed").probe_key != HealthKey(PROVIDER, "feed").probe_key


def test_a_key_of_an_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        HealthKey(kind="stream", name="x")
    with pytest.raises(ValueError):
        HealthKey(kind=BROKER, name="")


# ==================================================================
# Durations
# ==================================================================


def test_the_state_ttl_outlives_the_longest_cool_down():
    """Expiring health inside a cool-down would erase a failure streak by TTL.

    The provider would return to UNKNOWN — re-admitted with no evidence — which
    is the "reset the failure count" defect with a clock instead of a bug.
    """
    assert HEALTH_STATE_TTL_SECONDS > HEALTH_PROBE_MAX_DELAY


def test_the_state_ttl_is_finite():
    """A record that never expires is a leak.

    A per-user feed for a closed account would otherwise hold a key forever, in
    every worker's view — the unbounded growth ``forget_user_status`` and
    ``ProviderHealthRecovery.forget`` both exist to avoid.
    """
    assert 0 < HEALTH_STATE_TTL_SECONDS < 24 * 3600


def test_the_trial_lease_sits_between_one_call_and_one_cool_down():
    """Both bounds are semantic.

    Shorter than the longest provider HTTP timeout (12s) and a second worker
    could take the trial while the first is still making the call. Longer than
    the base cool-down and a worker that died holding the lease would park a due
    trial for a whole cool-down.
    """
    assert TRIAL_LEASE_SECONDS > 12.0
    assert TRIAL_LEASE_SECONDS < HEALTH_PROBE_BASE_DELAY


# ==================================================================
# Security
# ==================================================================


@needs_redis
def test_no_redis_credential_reaches_a_key_a_value_or_a_log(store, unique, caplog):
    """DEBUG through the real logging stack, with a live-looking password.

    Redis connection credentials, broker tokens and session material must not
    appear in the keyspace this module owns or in anything it logs. The password
    is real-shaped and is in the URL the fixture connected with, so there was
    something to leak.
    """
    name = unique()

    async def scenario():
        with caplog.at_level(logging.DEBUG):
            a = Worker(store, name=name)
            await a.fail(times=DOWN_AFTER_FAILURES)
            await a.resolve()
            await a.succeed()

        client = await redis_client.get_client()
        keys = [k.decode() if isinstance(k, bytes) else k
                for k in await client.keys("sa:health:*")]
        assert keys
        blob = " ".join(keys)
        for key in keys:
            values = await client.hgetall(key)
            blob += " " + " ".join(
                (v.decode() if isinstance(v, bytes) else str(v)) for v in values.values()
            )
        blob += " " + caplog.text
        for secret in (FAKE_REDIS_PASSWORD, "password", "token", "access_token"):
            assert secret not in blob, f"{secret!r} reached the shared health state"

    run(scenario())


@needs_redis
def test_one_users_health_is_invisible_to_another_users_resolution(store, unique):
    """Cross-user visibility, asked of resolution rather than of the key.

    The key model is checked above; this checks that the *behaviour* follows,
    because entitlement is what a leak would actually show up as.
    """
    name = unique()

    async def scenario():
        alice = Worker(store, name=name, owner="user-alice")
        await alice.fail(times=DOWN_AFTER_FAILURES)

        bob = Worker(store, name=name, owner="user-bob")
        resolution = await bob.resolve(owner="user-bob")
        assert resolution.available
        assert resolution.provider is bob.provider

    run(scenario())


def test_the_diagnostics_surface_carries_no_credential_and_no_connection_detail():
    """Administrative diagnostics are inspected too, not just request logs."""
    recovery = ProviderHealthRecovery()
    provider = FlakyPollingProvider("baseline")
    recovery.due_from([provider])
    rendered = repr(recovery.describe())
    for forbidden in ("redis://", "password", FAKE_REDIS_PASSWORD, "token"):
        assert forbidden not in rendered


# ==================================================================
# Structure
# ==================================================================


def test_no_module_names_a_broker_inside_the_shared_health_mechanism():
    """The store is broker-neutral, comments included.

    A branch, a key, or even a comment keyed on one broker's name is how a
    generic mechanism becomes five special cases.
    """
    brokers = ("zerodha", "upstox", "angelone", "fyers", "dhan", "kite", "smartapi")
    offenders = {}
    for relative in D58_MODULES:
        if relative.startswith("services/brokers/"):
            continue
        source = (BACKEND / relative).read_text()
        # Comments and docstrings are swept too for the module D5.8 *created*,
        # because prose is where a generic mechanism first learns a broker's name
        # and the D5.5 sweep found exactly that. For the modules it only changed,
        # the sweep reads code: those files carry pre-D5.8 prose that names
        # brokers legitimately (an example of a per-user feed), and widening the
        # rule to them would either fail on history or force the history to be
        # rewritten to satisfy a new test.
        text = source if relative == "infrastructure/health_state.py" \
            else _strip_docstrings_and_comments(source)
        hits = [b for b in brokers if b in text.lower()]
        if hits:
            offenders[relative] = hits
    assert not offenders, f"broker names inside generic modules: {offenders}"


def test_the_shared_store_holds_no_second_redis_client():
    """One connection stack, as PH2.7 established.

    ``infrastructure/redis_client.py`` is the only place a Redis connection is
    built; a second one would have its own pool, its own breaker and its own
    silent-degradation bug.
    """
    source = (BACKEND / "infrastructure" / "health_state.py").read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not any(name.split(".")[0] == "redis" for name in imported), (
        "health_state must reach Redis only through infrastructure.redis_client"
    )
    assert "redis_client" in imported or "infrastructure" in imported


def test_every_mutation_is_one_atomic_script():
    """No read-modify-write anywhere in the store.

    Asserted structurally as well as behaviourally: the concurrency tests above
    show the property, and this shows that the property is not being held up by
    luck in a narrow window.
    """
    source = (BACKEND / "infrastructure" / "health_state.py").read_text()
    body = _strip_docstrings_and_comments(source)
    for forbidden in (".hset(", ".hincrby(", ".hget(", ".set("):
        assert forbidden not in body, (
            f"{forbidden} outside a script — every mutation must be atomic"
        )


def test_the_resolution_path_stayed_synchronous():
    """The contract D5.8 refused to change.

    ``resolve_feed`` is called from routes, diagnostics, the scanner and the
    gateway. Making it awaitable to fit one Redis read would have rewritten five
    modules' signatures for a question asked about a handful of DOWN providers,
    so the awaitable work lives in ``prepare`` and travels as a value.
    """
    source = (BACKEND / "services" / "market_engine" / "source_manager.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            assert node.name != "resolve_feed"
        if isinstance(node, ast.FunctionDef) and node.name == "resolve_feed":
            break
    else:  # pragma: no cover - the method exists
        pytest.fail("resolve_feed not found")


# ==================================================================
# Performance
# ==================================================================


@needs_redis
def test_a_resolution_with_nothing_down_costs_one_redis_operation(store, unique):
    """The cost has to be flat, or the prelude becomes the slow path.

    One batched read for the whole candidate set, and nothing else while every
    provider is healthy. A per-provider round trip would put a Redis latency
    multiplier in front of a path that runs several times a second.
    """
    name = unique()

    async def scenario():
        worker = Worker(store, name=name, failing=False)
        await worker.succeed()
        before = redis_client.stats()["commands_total"]
        await worker.resolve()
        assert redis_client.stats()["commands_total"] - before == 1

    run(scenario())


@needs_redis
def test_a_resolution_with_one_provider_down_costs_two(store, unique):
    """One read plus one claim. Linear in the number of DOWN providers, and
    nothing is retried in a loop."""
    name = unique()

    async def scenario():
        worker = Worker(store, name=name)
        await worker.fail(times=DOWN_AFTER_FAILURES)
        await worker.resolve()
        before = redis_client.stats()["commands_total"]
        await worker.resolve()
        assert redis_client.stats()["commands_total"] - before == 2

    run(scenario())


@needs_redis
def test_one_health_mutation_costs_one_redis_operation(store, unique):
    name = unique()

    async def scenario():
        worker = Worker(store, name=name)
        await worker.fail()  # warms the script cache
        before = redis_client.stats()["commands_total"]
        await worker.fail()
        assert redis_client.stats()["commands_total"] - before == 1

    run(scenario())


# ==================================================================
# Helpers
# ==================================================================


async def _make_trial_due(store, key):
    """Bring a cool-down forward instead of waiting sixty seconds for it.

    Writes the next-probe instant directly, which is the one thing a test may do
    to the shared record: it moves the *clock*, not the policy. Every rung, every
    claim and every ladder advance below is still computed by the script.
    """
    client = await redis_client.get_client()
    await client.hset(key.probe_key, "next_probe_at", 0)
    await client.hset(key.probe_key, "claimed_until", 0)


async def _trial_attempts(store, key):
    client = await redis_client.get_client()
    value = await client.hget(key.probe_key, "attempts")
    return int(value or 0)


def _comparable(snapshot):
    """A health snapshot without its wall-clock stamps.

    The two implementations stamp at different instants by construction; the
    counters and the state are what must agree.
    """
    return {
        k: v for k, v in snapshot.items()
        if k not in {"last_success_at", "last_error_at"}
    }


def _strip_docstrings_and_comments(source):
    """Source with its prose removed, so a sweep reads code and not commentary."""
    without_comments = re.sub(r"#.*", "", source)
    tree = ast.parse(source)
    strings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.add(node.value)
    for text in sorted(strings, key=len, reverse=True):
        without_comments = without_comments.replace(text, "")
    return without_comments
