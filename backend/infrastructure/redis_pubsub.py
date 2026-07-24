"""Resilient Redis Pub/Sub subscribers (PH2.7).

THE BUG THIS MODULE EXISTS TO FIX
---------------------------------
``services/cache.py`` previously started a listener like this::

    pubsub = r.pubsub()
    await pubsub.subscribe(channel)
    async for message in pubsub.listen():
        ...

and wrapped the loop in ``except Exception: logger.warning("listener stopped")``.
Read that failure path carefully, because it is the most consequential kind of
bug — the kind that produces no error anywhere the user or the operator looks:

**The listener never came back.** Any exception ended the loop permanently. And
the exceptions are not hypothetical. Redis disconnects a subscriber whose output
buffer exceeds ``client-output-buffer-limit pubsub`` (32 MB, or 8 MB sustained
for 60s) — one blocked event loop is enough. A Redis restart during a deploy
kills every subscription. So does a NAT timeout, a rolling network policy change,
or an AOF-rewrite stall.

**And nothing broke.** HTTP kept working. The cache kept working. Health checks
kept passing, because pinging Redis and *being subscribed to it* are different
facts. The only symptom was that WebSocket clients connected to that one replica
silently stopped receiving cross-process events — a partial, per-replica realtime
outage that looks, to the user, like "the market went quiet", and to the
operator, like nothing at all. It would end when someone redeployed for an
unrelated reason, which is also why it would never be diagnosed.

Pub/Sub is the *only* Redis usage in this backend with a persistent, stateful
connection, and it is therefore the only one where "reconnect" is a feature that
has to be written rather than a property inherited from a connection pool.

WHAT THIS MODULE GUARANTEES
---------------------------
**A dedicated connection.** A connection in ``SUBSCRIBE`` mode accepts only
subscribe/unsubscribe/ping commands until it unsubscribes — it cannot serve a
``GET``. Taking it from the shared pool would remove a connection from
circulation for the process's lifetime and, worse, tie the realtime path's
liveness to pool pressure caused by cache traffic. The subscriber owns its own
connection, built from the same settings.

**Automatic reconnect with exponential backoff and jitter.** Backoff because a
Redis that is down does not benefit from being connected to 50 times a second.
Jitter because without it every replica retries on the same schedule and they
arrive at the recovering server in a synchronised thundering herd — the classic
way a fleet turns a brief outage into a long one.

**Exactly one subscriber per channel.** :func:`start_subscriber` is keyed by
channel name and returns the existing subscriber if one is already running.
Without this, a retried startup (or a reload, or a test) creates a second
subscription and every event is delivered *twice* — which for a trade
notification is a duplicated user-visible alert, not a cosmetic bug.

**Graceful shutdown.** The loop polls with a timeout instead of blocking in
``listen()``, so it observes the stop flag within one poll interval, unsubscribes
cleanly and closes its connection. A subscriber killed by task cancellation
leaves the server holding a client entry until TCP keepalive reaps it.

WHAT IT CANNOT GUARANTEE — AND WHY THAT IS ACCEPTABLE HERE
----------------------------------------------------------
Redis Pub/Sub is at-most-once, fire-and-forget. Messages published while a
subscriber is disconnected are **gone**; there is no replay, no offset, no
acknowledgement. Reconnecting restores the *stream*, not the *gap*.

That is tolerable for this payload and only for this payload: the bridged events
are UI refresh signals for live market data. A missed ``price.updated`` is
corrected by the next one seconds later, and the frontend also polls. It would
NOT be tolerable for anything where a missed message is a lost fact — an order
fill, a payment, a job to execute. If that ever rides this path, it needs Redis
Streams (consumer groups, acknowledgements, replay from an offset) or a real
broker, not a bigger buffer. This is written down because the natural instinct
when the first dropped message is noticed is to tune the buffer limit, and that
is the wrong lever.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any, Awaitable, Callable, Dict, Optional

from infrastructure import redis_client
from observability import metrics

logger = logging.getLogger(__name__)

# How long the receive loop blocks before checking the stop flag. The upper bound
# on shutdown latency for a subscriber, and the interval at which an idle
# subscriber wakes up — so it is a trade between a responsive `compose down` and
# doing nothing 24/7. 1s is comfortably below the 30s stop_grace_period.
POLL_TIMEOUT_SECONDS = 1.0

# Reconnect backoff: 0.5s, 1s, 2s … capped at 30s, each multiplied by a random
# factor in [0.5, 1.5]. The cap matters as much as the growth: an uncapped
# exponential reaches hours, and a subscriber that gives up for an hour after a
# long outage is indistinguishable from one that never reconnects.
BACKOFF_BASE_SECONDS = 0.5
BACKOFF_CAP_SECONDS = 30.0


MessageHandler = Callable[[Any], Awaitable[None]]


class PubSubSubscriber:
    """One channel, one dedicated connection, one supervised receive loop.

    The lifecycle is deliberately explicit — ``start()`` / ``stop()`` — rather
    than fire-and-forget, because a subscriber that nobody can stop is a
    subscriber that outlives the shutdown sequence and logs connection errors
    while the process is trying to exit.
    """

    def __init__(self, channel: str, handler: MessageHandler, *, name: Optional[str] = None) -> None:
        self.channel = channel
        self._handler = handler
        self.name = name or f"redis-pubsub:{channel}"

        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self._connected = False

        # Counters, surfaced through /api/diagnostics/redis.
        self._messages = 0
        self._dropped = 0
        self._handler_errors = 0
        self._reconnects = 0
        self._connected_at: Optional[float] = None
        self._last_message_at: Optional[float] = None
        self._last_error: Optional[str] = None

    # -- state --------------------------------------------------------------- #
    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def task(self) -> Optional[asyncio.Task]:
        """The supervising task. Exposed so ``services.cache.start_pubsub_listener``
        can keep its pre-PH2.7 return type. Do not cancel it directly — that
        skips the clean unsubscribe; call :meth:`stop`."""
        return self._task

    @property
    def connected(self) -> bool:
        return self._connected

    def stats(self) -> Dict[str, Any]:
        return {
            "channel": self.channel,
            "running": self.running,
            "connected": self._connected,
            "messages_total": self._messages,
            "dropped_total": self._dropped,
            "handler_errors_total": self._handler_errors,
            "reconnects_total": self._reconnects,
            "connected_at": self._connected_at,
            "last_message_at": self._last_message_at,
            "last_error": self._last_error,
        }

    # -- lifecycle ----------------------------------------------------------- #
    async def start(self) -> bool:
        """Start the supervised loop. Idempotent; returns False when Redis is
        not configured.

        Note what this does NOT do: it does not wait for the subscription to be
        established. Startup must not block on a dependency that is allowed to be
        down — that would make a Redis outage prevent the application from
        booting at all, converting a degraded realtime path into a total outage.
        The loop connects in the background and reports through
        ``connected`` / the metrics.
        """
        if self.running:
            return True
        if not redis_client.is_configured():
            return False
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name=self.name)
        logger.info(
            "Redis pub/sub subscriber starting on '%s'", self.channel,
            extra={"event": "redis_pubsub_start", "channel": self.channel},
        )
        return True

    async def stop(self) -> None:
        """Signal the loop to finish, then wait for it (bounded).

        The bound matters: shutdown must not hang because a subscriber is stuck
        in a socket read against an unreachable host. After the grace period the
        task is cancelled outright — a less tidy exit, but a bounded one.
        """
        self._stopping = True
        task, self._task = self._task, None
        if task is None or task.done():
            self._connected = False
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=POLL_TIMEOUT_SECONDS * 3)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        except Exception:  # pragma: no cover - defensive
            pass
        self._connected = False
        logger.info(
            "Redis pub/sub subscriber stopped on '%s'", self.channel,
            extra={"event": "redis_pubsub_stop", "channel": self.channel},
        )

    # -- the loop ------------------------------------------------------------ #
    async def _run(self) -> None:
        """Connect → receive → on failure, back off and reconnect. Forever."""
        attempt = 0
        delay = 0.0
        while not self._stopping:
            client = None
            pubsub = None
            try:
                client = await self._connect_client()
                if client is None:
                    # Redis unreachable or the circuit is open. Treated exactly
                    # like a dropped connection — back off and try again — rather
                    # than as a reason to exit, which is the bug this class
                    # exists to fix.
                    raise ConnectionError("redis client unavailable")

                pubsub = client.pubsub(ignore_subscribe_messages=True)
                await pubsub.subscribe(self.channel)

                if attempt > 0:
                    self._reconnects += 1
                    metrics.redis_pubsub_reconnects_total.inc(labels=(self.channel,))
                    logger.info(
                        "Redis pub/sub reconnected to '%s' after %d attempt(s)",
                        self.channel, attempt,
                        extra={"event": "redis_pubsub_reconnected", "channel": self.channel},
                    )
                else:
                    logger.info(
                        "Redis pub/sub subscribed to '%s'", self.channel,
                        extra={"event": "redis_pubsub_subscribed", "channel": self.channel},
                    )

                # A successful subscribe resets the backoff ladder — otherwise a
                # connection that flaps once an hour would still be waiting the
                # full 30s cap after a week of uptime.
                attempt = 0
                delay = 0.0
                self._connected = True
                self._connected_at = time.time()
                await self._receive(pubsub)

            except asyncio.CancelledError:
                # Cooperative cancellation during shutdown. Re-raise so the task
                # actually ends; cleanup happens in `finally`.
                raise
            except Exception as exc:
                self._connected = False
                self._last_error = f"{exc.__class__.__name__}: {exc}"[:300]
                if not self._stopping:
                    attempt += 1
                    # Computed ONCE and reused by the sleep below. Computing it
                    # twice would log a delay the loop does not actually wait —
                    # a small lie, but one that makes the reconnect timeline
                    # unreadable during exactly the incident it was written for.
                    delay = self._backoff(attempt)
                    logger.warning(
                        "Redis pub/sub '%s' disconnected (%s); reconnecting in %.1fs",
                        self.channel, exc, delay,
                        extra={
                            "event": "redis_pubsub_disconnected",
                            "channel": self.channel,
                            "attempt": attempt,
                        },
                    )
            finally:
                self._connected = False
                await self._teardown(pubsub, client)

            if self._stopping:
                break
            if delay > 0:
                await asyncio.sleep(delay)

    async def _connect_client(self) -> Optional[Any]:
        """Build the subscriber's OWN connection.

        ``single_connection_client=True`` gives this client one connection
        instead of a pool — correct for a subscription, which is inherently a
        single long-lived connection, and it avoids allocating a pool object per
        subscriber. Note it is built through ``redis_client._build_client``'s
        settings, not by hand, so timeouts and TLS options stay in one place.
        """
        settings = redis_client.manager.settings
        if not redis_client.is_configured():
            return None
        try:
            import redis.asyncio as aioredis

            return aioredis.from_url(
                redis_client.redis_url(),
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=settings.connect_timeout,
                # NO socket_timeout. This is the one place a read timeout is
                # wrong: a subscriber is *supposed* to sit silent between events,
                # and a 2s socket timeout would tear the subscription down every
                # 2 quiet seconds. Liveness comes from TCP keepalive and from the
                # server's `tcp-keepalive 300` instead.
                socket_keepalive=True,
                health_check_interval=settings.health_check_interval,
                single_connection_client=True,
            )
        except Exception as exc:
            self._last_error = f"{exc.__class__.__name__}: {exc}"[:300]
            return None

    async def _receive(self, pubsub: Any) -> None:
        """Pump messages until the stop flag is set or the connection breaks.

        Uses ``get_message(timeout=...)`` rather than ``async for … in listen()``
        on purpose. ``listen()`` blocks indefinitely, so the only way to end it is
        to cancel the task mid-await — which skips the clean unsubscribe and
        leaves a client entry on the server. Polling with a timeout means the
        loop reaches a natural checkpoint every second and shutdown is orderly.
        """
        while not self._stopping:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=POLL_TIMEOUT_SECONDS
            )
            if message is None:
                continue
            if message.get("type") not in ("message", "pmessage"):
                continue
            await self._dispatch(message.get("data"))

    async def _dispatch(self, raw: Any) -> None:
        """Decode one payload and hand it to the handler.

        Every failure here is contained. A malformed payload is dropped and
        counted; a raising handler is logged and counted. Neither may end the
        loop — one bad message must not cost the process its subscription, which
        is the same containment rule the event bus and the metrics collectors
        already follow.
        """
        self._messages += 1
        self._last_message_at = time.time()
        metrics.redis_pubsub_messages_total.inc(labels=(self.channel, "received"))

        try:
            payload = json.loads(raw)
        except Exception:
            self._dropped += 1
            metrics.redis_pubsub_messages_total.inc(labels=(self.channel, "dropped"))
            logger.warning(
                "Redis pub/sub '%s': dropped non-JSON message", self.channel,
                extra={"event": "redis_pubsub_bad_payload", "channel": self.channel},
            )
            return

        try:
            await self._handler(payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._handler_errors += 1
            metrics.redis_pubsub_messages_total.inc(labels=(self.channel, "handler_error"))
            logger.error(
                "Redis pub/sub '%s' handler error: %s", self.channel, exc,
                exc_info=True,
                extra={"event": "redis_pubsub_handler_error", "channel": self.channel},
            )

    async def _teardown(self, pubsub: Any, client: Any) -> None:
        """Unsubscribe and close, tolerating every failure.

        Called on the way out of *every* iteration, including the failure ones
        where the connection is already dead and each of these calls raises.
        That is why each is individually guarded: a teardown that raises would
        propagate out of the `finally` block and kill the reconnect loop —
        turning the recovery path into a second way to lose the subscription.
        """
        if pubsub is not None:
            try:
                await pubsub.unsubscribe(self.channel)
            except Exception:
                pass
            for closer in ("aclose", "close"):
                fn = getattr(pubsub, closer, None)
                if fn is None:
                    continue
                try:
                    await fn()
                    break
                except Exception:
                    continue
        if client is not None:
            for closer in ("aclose", "close"):
                fn = getattr(client, closer, None)
                if fn is None:
                    continue
                try:
                    await fn()
                    break
                except Exception:
                    continue

    @staticmethod
    def _backoff(attempt: int) -> float:
        """Exponential with full jitter, capped.

        The jitter is not decoration. Without it, every replica that lost its
        connection at the same instant retries at the same instants forever, and
        the recovering server sees its entire fleet arrive in synchronised
        waves. Randomising spreads the load across the window.
        """
        ceiling = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
        return max(0.05, ceiling * random.uniform(0.5, 1.5))


# --------------------------------------------------------------------------- #
# Registry — one subscriber per channel, process-wide                           #
# --------------------------------------------------------------------------- #
_subscribers: Dict[str, PubSubSubscriber] = {}


async def start_subscriber(channel: str, handler: MessageHandler) -> Optional[PubSubSubscriber]:
    """Subscribe to `channel`, or return the existing subscriber for it.

    Returns None when Redis is not configured — the supported single-process
    deployment, where the in-process event bus already delivers everything and a
    subscriber would have nothing to do.

    The registry is what makes this safe to call from a startup path that might
    run twice (a retried boot, a test that re-invokes wiring, a future
    hot-reload). The alternative — creating a subscriber per call — delivers
    every event N times, and duplicate delivery is far harder to notice than no
    delivery: the UI just updates twice.
    """
    existing = _subscribers.get(channel)
    if existing is not None and existing.running:
        logger.debug("Redis pub/sub already subscribed to '%s'; reusing", channel)
        return existing

    subscriber = PubSubSubscriber(channel, handler)
    if not await subscriber.start():
        return None
    _subscribers[channel] = subscriber
    return subscriber


async def stop_all() -> None:
    """Stop every subscriber. Called from the application shutdown hook."""
    subscribers = list(_subscribers.values())
    _subscribers.clear()
    for subscriber in subscribers:
        try:
            await subscriber.stop()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Subscriber stop raised (ignored): %s", exc)


def subscriber_stats() -> Dict[str, Any]:
    """Per-channel snapshot for the diagnostics endpoint."""
    return {channel: sub.stats() for channel, sub in _subscribers.items()}


def active_channels() -> list:
    return sorted(_subscribers)


def reset_for_tests() -> None:
    """Drop the registry without awaiting. Tests that started real subscribers
    must call :func:`stop_all` instead."""
    _subscribers.clear()
