"""Infrastructure clients: the backend's connections to the services it runs on.

Introduced in PH2.7 for Redis, and named for what it will hold rather than for
what it holds today. The distinction from the neighbouring packages is about
*what a module is responsible for*, not about layering ceremony:

    backend/security/        who may do what, and with which credential (PH1)
    backend/observability/   what this process is doing and whether it is well (PH2.5–2.6)
    backend/infrastructure/  how this process talks to its backing services (PH2.7)
    backend/services/        what the product does

The rule that keeps the boundary honest: a module in ``services/`` may depend on
``infrastructure/``; nothing in ``infrastructure/`` may know what a portfolio,
a trade or a quote is. ``services/cache.py`` is the worked example — it owns the
*policy* (JSON serialization, TTLs, the in-memory fallback, what a cache key
means) while this package owns the *connection* (pooling, retry, circuit
breaking, reconnect).

That split is what makes "no duplicated Redis clients" enforceable rather than
aspirational. Before PH2.7 there were two independent client constructions in
the backend — one in the cache layer and one in the health probe — with
different timeouts, different failure semantics and, in one of them, a latch
that permanently disabled Redis after a single blip. There is now exactly one
place that opens a Redis connection: ``redis_client.RedisManager._build_client``.
A future migration to Sentinel or Cluster is a change to that one function.

    from infrastructure import redis_client, redis_pubsub

    ok, value = await redis_client.execute("get", lambda r: r.get(key))
    await redis_pubsub.start_subscriber("sa:events", handler)

PH3.6 added ``tasks`` to this package on the same test: a supervised asyncio
task registry is *how this process runs work*, and it knows nothing about
portfolios, trades or quotes. It is the shutdown path the perpetual loops in
``server.py`` and ``services/heartbeat_engine.py`` did not have.

    from infrastructure import tasks

    tasks.spawn("market-broadcast-loop", market_broadcast_loop())
    await tasks.cancel_all()
"""
from infrastructure import redis_client, redis_pubsub, tasks  # noqa: F401

__all__ = ["redis_client", "redis_pubsub", "tasks"]
