"""Distributed health and recovery state (D5.8 / DB-1).

WHY THIS MODULE EXISTS
----------------------
Every health mechanism D5 built is process-local, and each one was correct while
it was the only one making a decision::

    BrokerHealth                     one counter per worker per broker
    ProviderHealth                   one counter per worker per provider
    ProviderHealthRecovery (D5.7)    one cool-down per worker per provider

With ``N`` uvicorn workers behind one deployment that is ``N`` independent
opinions about the same remote dependency. Two of the consequences are visible
and one is not:

  * the Admin Portal reports whichever worker answered the request;
  * a provider needs ``8 × N`` failed calls before every worker has excluded it;
  * **and the one DB-1 is actually named for** — D5.7 grants "at most one trial
    per cool-down", but per worker, so a DOWN provider that is genuinely down
    is retried ``N`` times per cool-down instead of once. That is the guarantee
    the ladder exists to make, and it is the guarantee a second process breaks.

This module is the shared store those three mechanisms consult. It is
deliberately *not* in ``services/`` — ``services.market_engine`` may not import
``services.brokers`` (pinned by
``test_the_market_engine_never_imports_a_broker_module``) and the broker layer
must not acquire market-engine vocabulary, so the one place both may reach is
``infrastructure/``, which already owns the Redis connection.

WHAT IS SHARED AND WHAT IS NOT — THE DISCRIMINATOR
---------------------------------------------------
Not every counter belongs here, and moving one that does not is worse than
leaving it local. The question is not "is this in memory?" but:

    is this state *evidence about something every worker observes*?

  * A **broker's API** is one remote system. Every worker's calls to it are
    evidence about the same thing, so ``BrokerHealth`` is shared.
  * A **polled provider** — the permanent baseline — is registered in every
    worker and called by every worker over HTTP. Shared.
  * A **streaming provider** is one live socket held by one worker. Its health,
    its readiness, its probation window and its delivery-latency samples are all
    evidence about *that link*, and D5.3 already rules that a reconnect discards
    them. Publishing them would let a dead socket's DOWN state be inherited by
    the fresh link a different worker opened — the exact opposite of what D5.5
    and D5.6 established, where a re-attached feed is a new feed that must earn
    its readiness again. **Not shared**, by the provider's own
    :attr:`~services.market_engine.providers.base.MarketDataProvider.health_is_shared`.

So the shared set is precisely the set where the double-spend DB-1 names can
happen, and nothing more.

THE STATE MACHINE IS NOT REIMPLEMENTED — IT IS TRANSLATED, AND PINNED
----------------------------------------------------------------------
The transitions have to run inside Redis (see ATOMICITY), which means a second
expression of them in Lua. A second expression is a second thing to get wrong,
so it is not trusted: ``tests/test_distributed_health.py`` replays the same
event sequences through the existing Python classes and through this store and
asserts the resulting snapshots are identical. The Python implementation is the
oracle; the Lua must agree with it or the suite goes red.

ATOMICITY
---------
``GET`` → modify → ``SET`` loses updates, and the updates it loses are exactly
the ones that matter: two workers recording the seventh and eighth consecutive
failure of the same provider in the same instant must produce a streak of 8, not
7, and two workers finding the same trial due must not both take it. Every
mutation here is therefore **one Lua script**, which Redis runs to completion
without interleaving, and each one is a single round trip.

The scripts read the clock with ``redis.call('TIME')`` rather than accepting one
from the caller. D5.1–D5.7 all use ``time.monotonic`` because a duration must not
be measurable by an NTP step; monotonic clocks are not comparable *between
processes*, so a shared ladder cannot use them, and accepting each worker's
wall clock would make the ladder as skewed as the worst-set clock in the fleet.
Redis's own clock is one clock for every worker, which is the property the shared
ladder needs. (Requires effect-based script replication — the default from Redis
7, which is what ``docker/redis`` runs.)

REDIS UNAVAILABLE: BOUNDED LOCAL FALLBACK
------------------------------------------
Chosen from what the deployment already guarantees, not from preference. Redis
is registered ``critical=False`` in the readiness probe, ``services/cache.py``
degrades to a per-process dict when it is absent, and
``infrastructure/redis_client.py`` exists to make degradation a first-class path
rather than an error path. Health must therefore do neither of the two things
that sound decisive:

  * **fail closed** (treat unreachable Redis as DOWN) turns a Redis blip into a
    total market-data outage — a dependency that is explicitly non-critical
    taking down the feed it was only ever observing;
  * **fail open** (ignore health while Redis is down) throws away the local
    evidence this worker has in its hands and hammers a provider it *knows* is
    failing.

So every method here returns ``ok=False`` and the caller applies the mutation to
its own local object with the code that has always been there. The platform
reverts to exactly its pre-D5.8 behaviour — per-worker health, per-worker
cool-downs, at worst ``N`` trials per cool-down — for as long as Redis is away,
and the next successful mutation re-establishes the shared value from Redis's own
reply rather than pushing a local guess into it.

SECURITY
--------
Keys carry a provider or broker name and, for a per-user provider, a user id.
None is a credential, and the same three facts already appear in provider names,
registry keys and the admin diagnostics surface. No token, no API key, no
session and no URL is ever written to a key or a value here, and nothing this
module stores reaches a consumer payload — Developer Rule 4 is unchanged, since
the Market Gateway still publishes ``source_tier`` and no identity.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence, Tuple

from infrastructure import redis_client

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Durations                                                                     #
# --------------------------------------------------------------------------- #

#: How long a shared health record lives without being touched, in seconds.
#:
#: A TTL is required in *both* directions and the bounds are semantic, not
#: aesthetic.
#:
#: It may not be shorter than the longest cool-down it has to outlive. D5.7's
#: ladder tops out at ``HEALTH_PROBE_MAX_DELAY`` (240s); if the health record
#: expired inside that window the provider would silently return to UNKNOWN —
#: re-admitted with no evidence, its failure streak erased by a key expiry
#: rather than by a successful call, which is precisely the "reset the failure
#: count" defect this sprint exists to prevent.
#:
#: It may not be unbounded either. A provider that is unregistered and never
#: seen again — a per-user feed for a closed account — would otherwise leave a
#: key in Redis forever, which is the unbounded-growth trap
#: ``forget_user_status`` and ``ProviderHealthRecovery.forget`` both avoid.
#:
#: One hour is an order of magnitude above the 240s floor and finite. What it
#: means operationally: a provider nothing has called for an hour reports
#: UNKNOWN, which is the honest answer — there is no evidence about it.
HEALTH_STATE_TTL_SECONDS = 3600.0

#: How long one worker's claim on a due recovery trial is exclusive, in seconds.
#:
#: This is a *lease*, and the distinction between it and the cool-down ladder is
#: the whole of the D5.7 interaction. D5.7's rule is that the ladder is charged
#: by evidence and never by the offer: "a provider that is offered and never
#: reached — because something healthier answered — costs nothing at all."
#: Advancing the ladder at claim time would break that rule, so the claim
#: instead takes a short exclusive lease on the *offer*. One worker gets the
#: trial; every other worker resolving in the same window is told it is taken.
#:
#: The floor is one provider call: the lease has to outlive the gap between the
#: offer and the evidence, and the longest per-call HTTP timeout in the platform
#: is 12s (``services/http_client.py`` call sites). The ceiling is the base
#: cool-down: a lease longer than ``HEALTH_PROBE_BASE_DELAY`` (60s) taken by a
#: worker that then died would park a due trial for a whole cool-down, which is
#: the outage DB-1 is supposed to shorten. Thirty seconds sits between the two
#: with room on both sides.
TRIAL_LEASE_SECONDS = 30.0


# --------------------------------------------------------------------------- #
# Keys                                                                          #
# --------------------------------------------------------------------------- #

#: Namespace prefix. Colon-separated and versioned by subsystem rather than
#: sharing ``services/cache.py``'s flat underscore names, because these keys are
#: hashes with a schema and a TTL policy of their own — a future ``SCAN`` for
#: "everything DB-1 owns" has to be able to name them exactly.
KEY_PREFIX = "sa:health"

#: Placeholder for the owner segment of a platform-wide record, so that a key
#: always has the same number of segments and an empty owner can never make a
#: global provider's key collide with a per-user one's.
GLOBAL_OWNER = "-"

PROVIDER = "provider"
BROKER = "broker"


@dataclass(frozen=True)
class HealthKey:
    """What one shared health record is *about*.

    Three parts, and each one is load bearing:

    ``kind``   provider or broker. Two namespaces that must never collide: a
               broker and a market-data provider can carry the same name and are
               different subjects with different thresholds.
    ``owner``  the user a per-user provider belongs to, empty for
               platform-wide. Present because two users can hold a feed from the
               same broker, and one account's outage is not the other's — the
               same reason D5.7's ``ProbeKey`` carries it rather than relying on
               a naming convention that a later sprint could change.
    ``name``   the registry name of the provider, or the broker name.
    """

    kind: str
    name: str
    owner: str = ""

    def __post_init__(self) -> None:
        if self.kind not in (PROVIDER, BROKER):
            raise ValueError(f"unknown health key kind: {self.kind!r}")
        if not self.name:
            raise ValueError("health key needs a name")

    @property
    def redis_key(self) -> str:
        return f"{KEY_PREFIX}:{self.kind}:{self.owner or GLOBAL_OWNER}:{self.name}"

    @property
    def probe_key(self) -> str:
        """The recovery cool-down record for the same subject.

        Derived from the health key rather than built independently, so the two
        can never be scoped differently — a cool-down keyed without the owner
        while health is keyed with it would let one user's trial be consumed on
        another's behalf, which is mutation 15 of this sprint's falsification
        list and is impossible by construction here.
        """
        return f"{KEY_PREFIX}:trial:{self.kind}:{self.owner or GLOBAL_OWNER}:{self.name}"


def provider_key(name: str, owner: Optional[str] = None) -> HealthKey:
    return HealthKey(kind=PROVIDER, name=name, owner=str(owner) if owner else "")


def broker_key(broker: str) -> HealthKey:
    """A broker's key, deliberately with no owner.

    A broker's API availability is one fact about one remote system; every
    user's calls observe the same outage. Per-user session state is a different
    concept and lives on ``BrokerConnection`` — see ``services/brokers/health.py``.
    """
    return HealthKey(kind=BROKER, name=broker)


# --------------------------------------------------------------------------- #
# Outcomes                                                                      #
# --------------------------------------------------------------------------- #

#: The four events the shared state machine understands. A closed set written
#: once, because the whole point of this module is that "what a failure does to
#: health" has one answer for every worker.
SUCCESS = "success"
EMPTY = "empty"
FAILURE = "failure"
AUTH = "auth"

#: What one trial claim did.
ARMED = "armed"                       #: First sight of a DOWN subject — cool-down started, trial refused.
TOO_SOON = "too_soon"                 #: The cool-down has not run.
CLAIMED_ELSEWHERE = "claimed_elsewhere"  #: Due, but another worker holds the lease.
CLAIMED = "claimed"                   #: This worker holds the trial.


@dataclass
class SharedHealth:
    """One subject's authoritative counters, as Redis last reported them.

    Field names are generic on purpose. ``ProviderHealth`` calls its last error
    field ``last_error_class`` and ``BrokerHealth`` calls its ``last_error_code``;
    both are one short diagnostic label, and giving the store a union of both
    spellings would mean every future health-bearing subsystem adds a third.
    The adapters map their own name onto ``error_label`` at the boundary.
    """

    state: str = "unknown"
    consecutive_failures: int = 0
    total_calls: int = 0
    total_errors: int = 0
    total_empty: int = 0
    total_auth_failures: int = 0
    last_success_at: Optional[str] = None
    last_error_at: Optional[str] = None
    error_label: Optional[str] = None
    #: The state before this mutation, so a caller can tell whether to publish a
    #: status change without a second read. ``None`` for a plain read.
    previous_state: Optional[str] = None

    @property
    def changed(self) -> bool:
        return self.previous_state is not None and self.previous_state != self.state


@dataclass
class TrialClaim:
    """The result of asking Redis for one recovery trial."""

    key: HealthKey
    outcome: str
    attempts: int = 0
    due_in_seconds: float = 0.0

    @property
    def granted(self) -> bool:
        return self.outcome == CLAIMED


# --------------------------------------------------------------------------- #
# Lua                                                                           #
# --------------------------------------------------------------------------- #
#
# Each script is one atomic mutation and one round trip. They are written
# against the *existing* Python state machines and pinned against them by a
# parity test — see the module docstring.

#: Shared preamble: the clock. See ATOMICITY in the module docstring for why the
#: time comes from Redis and not from the caller.
_NOW_MS = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
"""

_RECORD_LUA = """
local key = KEYS[1]
local outcome = ARGV[1]
local stamp = ARGV[2]
local ttl = tonumber(ARGV[3])
local degraded_after = tonumber(ARGV[4])
local down_after = tonumber(ARGV[5])
local label = ARGV[6]

local function num(field)
  return tonumber(redis.call('HGET', key, field)) or 0
end

local previous = redis.call('HGET', key, 'state')
if not previous then previous = 'unknown' end
local state = previous

redis.call('HSET', key, 'total_calls', num('total_calls') + 1)

if outcome == 'empty' then
  -- Counted, and deliberately does NOT clear the failure streak: a provider
  -- answering 200-with-no-data is not healthy, and treating it as healthy is
  -- how a silently empty feed keeps its primary slot.
  redis.call('HSET', key, 'total_empty', num('total_empty') + 1)
elseif outcome == 'auth' then
  -- A per-user session failure. Counted, never part of the state machine —
  -- otherwise every broker goes DOWN at 06:00 IST when tokens expire.
  redis.call('HSET', key, 'total_auth_failures', num('total_auth_failures') + 1)
  redis.call('HSET', key, 'last_error_at', stamp, 'error_label', label)
elseif outcome == 'success' then
  redis.call('HSET', key, 'consecutive_failures', 0, 'last_success_at', stamp)
  state = 'up'
elseif outcome == 'failure' then
  local streak = num('consecutive_failures') + 1
  redis.call('HSET', key,
             'consecutive_failures', streak,
             'total_errors', num('total_errors') + 1,
             'last_error_at', stamp,
             'error_label', label)
  if streak >= down_after then
    state = 'down'
  elseif streak >= degraded_after then
    state = 'degraded'
  end
else
  return redis.error_reply('unknown outcome')
end

if state ~= previous then
  redis.call('HSET', key, 'state', state)
end
redis.call('PEXPIRE', key, ttl)
return {previous, redis.call('HGETALL', key)}
"""

_READ_LUA = """
local out = {}
for i = 1, #KEYS do
  out[i] = redis.call('HGETALL', KEYS[i])
end
return out
"""

#: The ladder, in Lua. Identical arithmetic to
#: ``ProviderHealthRecovery._delay``: the base delay doubling per failed probe,
#: capped. Duplicated rather than imported for the reason the whole script is
#: duplicated — it has to run inside the atomic section — and pinned against the
#: Python original by a parity test.
_DELAY_LUA = """
local function delay(attempts, base, maxd)
  local d = base * (2 ^ math.max(0, attempts - 1))
  if d > maxd then d = maxd end
  return d
end
"""

_CLAIM_LUA = _DELAY_LUA + _NOW_MS + """
local key = KEYS[1]
local base = tonumber(ARGV[1])
local maxd = tonumber(ARGV[2])
local lease = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

local attempts = tonumber(redis.call('HGET', key, 'attempts'))
if attempts == nil then
  -- First sight of this DOWN subject. Armed and refused, exactly as D5.7's
  -- `due_from` does locally: reading the cool-down is what starts it, so there
  -- is no path by which a subject becomes DOWN without one.
  local d = delay(1, base, maxd)
  redis.call('HSET', key, 'attempts', 1, 'next_probe_at', now + d, 'claimed_until', 0)
  redis.call('PEXPIRE', key, ttl)
  return {'armed', 1, math.floor(d)}
end

redis.call('PEXPIRE', key, ttl)

local next_at = tonumber(redis.call('HGET', key, 'next_probe_at')) or 0
if now < next_at then
  return {'too_soon', attempts, math.floor(next_at - now)}
end

local claimed_until = tonumber(redis.call('HGET', key, 'claimed_until')) or 0
if now < claimed_until then
  -- Another worker already took this cool-down's trial. It is not charged to
  -- this one and this one does not get to offer the subject.
  return {'claimed_elsewhere', attempts, math.floor(claimed_until - now)}
end

redis.call('HSET', key, 'claimed_until', now + lease)
return {'claimed', attempts, 0}
"""

_FAILED_LUA = _DELAY_LUA + _NOW_MS + """
local key = KEYS[1]
local base = tonumber(ARGV[1])
local maxd = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])

local attempts = (tonumber(redis.call('HGET', key, 'attempts')) or 0) + 1
local d = delay(attempts, base, maxd)
-- The lease is released with the same write that charges the ladder: the trial
-- produced its evidence, so holding the offer exclusive past that point would
-- delay the *next* cool-down's trial by a lease.
redis.call('HSET', key, 'attempts', attempts, 'next_probe_at', now + d, 'claimed_until', 0)
redis.call('PEXPIRE', key, ttl)
return {attempts, math.floor(d)}
"""


def _sha(script: str) -> str:
    return hashlib.sha1(script.encode("utf-8")).hexdigest()


_RECORD_SHA = _sha(_RECORD_LUA)
_READ_SHA = _sha(_READ_LUA)
_CLAIM_SHA = _sha(_CLAIM_LUA)
_FAILED_SHA = _sha(_FAILED_LUA)


# --------------------------------------------------------------------------- #
# Store                                                                         #
# --------------------------------------------------------------------------- #


class SharedHealthStore:
    """The shared health and recovery state, or an honest "not available".

    Every method returns ``(ok, value)`` in the same shape
    ``redis_client.execute`` does, and none raises: a caller that gets
    ``ok=False`` applies its own local arithmetic and carries on. See REDIS
    UNAVAILABLE in the module docstring for why that, and not a failure verdict,
    is the right answer here.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = HEALTH_STATE_TTL_SECONDS,
        lease_seconds: float = TRIAL_LEASE_SECONDS,
    ) -> None:
        self._ttl_ms = int(float(ttl_seconds) * 1000)
        self._lease_ms = int(float(lease_seconds) * 1000)

    @property
    def enabled(self) -> bool:
        """Whether a shared store is configured at all.

        ``False`` in the supported single-process deployment, where every
        mechanism keeps the process-local behaviour D5.1–D5.7 shipped. Read
        live rather than cached because the test suite and
        ``security.secrets.load_secrets`` both set ``REDIS_URL`` after import.
        """
        return redis_client.is_configured()

    # ── Health ───────────────────────────────────────────

    async def record(
        self,
        key: HealthKey,
        outcome: str,
        *,
        stamp: str,
        degraded_after: int,
        down_after: int,
        label: Optional[str] = None,
    ) -> Tuple[bool, Optional[SharedHealth]]:
        """Apply one outcome to the shared record and return the result.

        One round trip, one atomic transition. The returned :class:`SharedHealth`
        is what every worker will now read — the caller mirrors it onto its local
        object rather than applying its own arithmetic, which is what stops the
        two from drifting.
        """
        if not self.enabled:
            return False, None
        ok, raw = await self._eval(
            "health_record", _RECORD_LUA, _RECORD_SHA, [key.redis_key],
            [outcome, stamp, self._ttl_ms, int(degraded_after), int(down_after), label or ""],
        )
        if not ok or not isinstance(raw, (list, tuple)) or len(raw) != 2:
            return False, None
        return True, _health_from_reply(_text(raw[0]), raw[1])

    async def read_many(
        self, keys: Sequence[HealthKey]
    ) -> Tuple[bool, Dict[HealthKey, SharedHealth]]:
        """Every subject's current shared health, in one round trip.

        Batched deliberately: the resolution path asks about the whole candidate
        set at once, and one round trip per provider would put a Redis latency
        multiplier on a path that runs several times a second. A subject with no
        record yet is returned as UNKNOWN rather than omitted, so the caller
        never has to distinguish "absent" from "never called" — they are the same
        fact.
        """
        if not self.enabled or not keys:
            return False, {}
        ordered = list(keys)
        ok, raw = await self._eval(
            "health_read", _READ_LUA, _READ_SHA, [k.redis_key for k in ordered], [],
        )
        if not ok or not isinstance(raw, (list, tuple)) or len(raw) != len(ordered):
            return False, {}
        return True, {
            key: _health_from_reply(None, reply) for key, reply in zip(ordered, raw)
        }

    # ── Recovery trials ──────────────────────────────────

    async def claim_trials(
        self,
        keys: Sequence[HealthKey],
        *,
        base_delay: float,
        max_delay: float,
    ) -> Tuple[bool, Dict[HealthKey, TrialClaim]]:
        """Ask for one recovery trial per subject. At most one worker wins each.

        Not batched into a single script, and that is a decision: one key per
        script keeps every claim independently atomic and keeps the script
        Cluster-safe (a multi-key script whose keys hash to different slots is a
        Cluster error, and ``infrastructure/redis_client.py`` names Cluster as
        the migration this seam exists for). The list is short by construction —
        it is the DOWN subset of one capability's eligible providers — and a
        deployment with enough simultaneously-DOWN providers for this to matter
        has a larger problem than a round trip.
        """
        if not self.enabled or not keys:
            return False, {}
        claims: Dict[HealthKey, TrialClaim] = {}
        any_ok = False
        for key in keys:
            ok, raw = await self._eval(
                "health_claim", _CLAIM_LUA, _CLAIM_SHA, [key.probe_key],
                [float(base_delay) * 1000, float(max_delay) * 1000,
                 self._lease_ms, self._ttl_ms],
            )
            if not ok or not isinstance(raw, (list, tuple)) or len(raw) != 3:
                # A partial failure is reported as a whole failure: a caller that
                # trusted half a claim set would offer the unclaimed half on
                # local state alone, which is the double-spend this exists to
                # stop.
                return False, {}
            any_ok = True
            claims[key] = TrialClaim(
                key=key,
                outcome=_text(raw[0]),
                attempts=int(raw[1]),
                due_in_seconds=float(raw[2]) / 1000.0,
            )
        return any_ok, claims

    async def note_trial_failed(
        self, key: HealthKey, *, base_delay: float, max_delay: float
    ) -> Tuple[bool, Optional[int]]:
        """Charge one failed probe against the shared ladder. Returns attempts."""
        if not self.enabled:
            return False, None
        ok, raw = await self._eval(
            "health_trial_failed", _FAILED_LUA, _FAILED_SHA, [key.probe_key],
            [float(base_delay) * 1000, float(max_delay) * 1000, self._ttl_ms],
        )
        if not ok or not isinstance(raw, (list, tuple)) or len(raw) != 2:
            return False, None
        return True, int(raw[0])

    async def clear_trial(self, key: HealthKey) -> Tuple[bool, bool]:
        """Drop a subject's cool-down entirely — it recovered, or it is gone."""
        if not self.enabled:
            return False, False
        ok, raw = await redis_client.execute(
            "health_trial_clear", lambda r: r.delete(key.probe_key)
        )
        return ok, bool(ok and raw)

    async def forget(self, key: HealthKey) -> Tuple[bool, bool]:
        """Drop both records for a subject. Unregistration and tests only."""
        if not self.enabled:
            return False, False
        ok, raw = await redis_client.execute(
            "health_forget", lambda r: r.delete(key.redis_key, key.probe_key)
        )
        return ok, bool(ok and raw)

    # ── Internals ────────────────────────────────────────

    async def _eval(
        self, operation: str, script: str, sha: str,
        keys: Sequence[str], args: Sequence[Any],
    ) -> Tuple[bool, Any]:
        """EVALSHA, falling back to EVAL the first time on a given server.

        Sending the script body on every call would put a kilobyte on the wire
        in front of every health mutation; caching an sha without the fallback
        would break the first call after a Redis restart or a ``SCRIPT FLUSH``.
        Both are one round trip in the steady state.
        """
        payload = [str(a) for a in args]

        async def run(r):
            try:
                return await r.evalsha(sha, len(keys), *keys, *payload)
            except Exception as exc:  # noqa: BLE001 - narrowed immediately below
                if not _is_missing_script(exc):
                    raise
                return await r.eval(script, len(keys), *keys, *payload)

        return await redis_client.execute(operation, run)


#: Process-wide store, matching the `provider_registry` / `stream_manager`
#: convention. One per process because it holds no state — only the durations —
#: and the connection it uses is itself a singleton.
shared_health_store = SharedHealthStore()


# --------------------------------------------------------------------------- #
# Reply decoding                                                                #
# --------------------------------------------------------------------------- #


def _is_missing_script(exc: BaseException) -> bool:
    """Whether `exc` is redis-py's "this server has never seen that sha".

    Matched on the exception's class name and its message rather than by
    importing ``redis.exceptions``, for the same reason
    ``infrastructure/redis_client.is_connection_error`` does it: this module must
    import cleanly in an environment without redis-py, and the class name is the
    stable part of that library's surface. The message is checked too because
    the wording ("No matching script") carries no error code of its own.
    """
    if type(exc).__name__ == "NoScriptError":
        return True
    text = str(exc).upper()
    return "NOSCRIPT" in text or "NO MATCHING SCRIPT" in text


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return "" if value is None else str(value)


def _health_from_reply(previous: Optional[str], reply: Any) -> SharedHealth:
    """Turn one ``HGETALL`` reply into a :class:`SharedHealth`.

    Accepts both the flat list a Lua ``HGETALL`` returns and the dict redis-py
    produces for a direct call, because the same decoder serves both the scripts
    and any future non-scripted read.
    """
    fields: Dict[str, str] = {}
    if isinstance(reply, dict):
        fields = {_text(k): _text(v) for k, v in reply.items()}
    elif isinstance(reply, (list, tuple)):
        flat = [_text(item) for item in reply]
        fields = dict(zip(flat[0::2], flat[1::2]))

    def _int(name: str) -> int:
        try:
            return int(fields.get(name) or 0)
        except (TypeError, ValueError):
            return 0

    return SharedHealth(
        state=fields.get("state") or "unknown",
        consecutive_failures=_int("consecutive_failures"),
        total_calls=_int("total_calls"),
        total_errors=_int("total_errors"),
        total_empty=_int("total_empty"),
        total_auth_failures=_int("total_auth_failures"),
        last_success_at=fields.get("last_success_at") or None,
        last_error_at=fields.get("last_error_at") or None,
        error_label=fields.get("error_label") or None,
        previous_state=previous,
    )
