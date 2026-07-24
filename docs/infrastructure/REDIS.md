# Redis Infrastructure

**Sprint:** PH2.7 — Production Redis Infrastructure
**Status:** Production-ready, single node
**Owns:** shared cache, cross-process realtime fan-out

---

## 1. What Redis is for here — and what it is not for

Redis in StockAssist AI does exactly two jobs:

| Job | Consumer | Data | Durable? |
|---|---|---|---|
| **Shared cache** | `services/cache.py` → market data, news, stock details, economic calendar | JSON blobs with a TTL | No — reconstructible from provider APIs |
| **Cross-process Pub/Sub** | `services/realtime/event_bridge.py` → WebSocket fan-out between replicas | Transient event envelopes | No — never stored |

**Nothing here is a system of record.** That is a deliberate architectural line, and
it is what makes every other decision in this document defensible:

- **Sessions** live in MongoDB (`backend/security/sessions.py`). Refresh-token
  rotation with reuse detection needs an authoritative, durable record; losing one
  to an eviction would silently disable theft detection.
- **Rate-limit counters** live in MongoDB (`backend/security/rate_limit.py`).
- **Audit logs** live in MongoDB.

Because everything in Redis is reconstructible, the correct failure behaviour is
*degrade and keep serving* — never *fail the request*. The whole client design
follows from that one sentence.

> **The rule to preserve:** if you are about to put something in Redis that cannot
> be recomputed, put it in MongoDB instead. A Redis holding both evictable and
> non-evictable data is one where `maxmemory-policy` is wrong for half its
> contents, and you find out which half during an incident.

---

## 2. Architecture

```
                         ┌──────────────────────────────────────┐
   HTTP request ────────▶│  services/                           │
                         │    real_market · news_service ·      │
                         │    stock_details · market_engine     │
                         └────────────────┬─────────────────────┘
                                          │  cache_get / cache_set
                                          │  cache_get_many / cache_set_many
                                          ▼
   event_bus  ──────────▶┌──────────────────────────────────────┐
   (in-process)          │  services/cache.py        POLICY     │
                         │  JSON encoding · TTLs · batching ·   │
                         │  bounded in-memory fallback store    │
                         └────────────────┬─────────────────────┘
                                          │  execute(op, fn)
                                          ▼
      ┌───────────────────────────────────────────────────────────────┐
      │  infrastructure/redis_client.py            CONNECTION         │
      │                                                               │
      │   connection pool  ·  retry  ·  circuit breaker  ·  metrics   │
      │   ─────────────────────────────────────────────────────────   │
      │   the ONLY place in the backend that opens a Redis connection │
      └───────────┬───────────────────────────────────┬───────────────┘
                  │                                   │
                  │ pooled (24)                       │ dedicated, 1 per channel
                  │                                   │
                  │                    ┌──────────────▼────────────────┐
                  │                    │ infrastructure/redis_pubsub.py│
                  │                    │  reconnect · backoff+jitter · │
                  │                    │  one-subscriber-per-channel   │
                  │                    └──────────────┬────────────────┘
                  │                                   │
                  ▼                                   ▼
      ╔══════════════════════════════════════════════════════════════╗
      ║                          REDIS  7.2                          ║
      ║   docker/redis/redis.conf  +  --requirepass  --maxmemory     ║
      ║                                                              ║
      ║   AOF everysec ──▶ redis_data volume      (warm restart)     ║
      ║   allkeys-lru  ──▶ 256 MB ceiling         (bounded memory)   ║
      ║   internal network, no published port     (no exposure)      ║
      ╚═══════════════════════════╤══════════════════════════════════╝
                                  │ INFO (every 30s, background)
                                  ▼
      ┌──────────────────────────────────────────────────────────────┐
      │  observability/  →  /api/health/ready · /api/metrics          │
      │                     /api/diagnostics/redis                    │
      └──────────────────────────────────────────────────────────────┘
```

### Package boundary

| Package | Responsibility | May not know about |
|---|---|---|
| `infrastructure/` | connections: pooling, retry, breaker, reconnect | quotes, trades, portfolios |
| `services/cache.py` | policy: serialization, TTLs, fallback, batching | socket timeouts, pool internals |
| `observability/` | reporting: health, metrics, diagnostics | how to open a connection |

`RedisManager._build_client()` is the single construction site. A future migration
to Sentinel or Cluster is a change to that one function.

---

## 3. Server configuration

All tuning lives in **`docker/redis/redis.conf`**, mounted read-only, shared by the
base stack and the secrets overlay. Two things are deliberately kept out of it and
passed on the command line (later arguments override the config file):

| Passed on CLI | Why not in the file |
|---|---|
| `--requirepass` | A credential. `redis.conf` is tracked in git. |
| `--maxmemory`, `--maxmemory-policy` | Per-environment sizing from `.env`. |

### The decisions that matter

| Setting | Value | Reasoning |
|---|---|---|
| **Persistence** | AOF on, RDB off (`save ""`) | Not for durability — for **restart behaviour**. Without persistence every Redis restart empties the cache and every replica simultaneously re-fetches the entire quote universe from rate-limited third-party APIs. A 2-second restart becomes minutes of degraded market data. AOF loses ≤1s; RDB loses everything since the last snapshot. Running both means two forking processes writing one volume for durability that reconstructible data does not need. |
| `appendfsync` | `everysec` | `always` costs an fsync on every cache write for durability nobody is asking for. `no` can lose 30s+. |
| `aof-use-rdb-preamble` | `yes` | Restart replays a binary snapshot plus a short tail, not a full command log. This is what keeps restart in the low seconds — the entire point of enabling persistence. |
| `auto-aof-rewrite-percentage` | `100` / min `64mb` | A cache that constantly SETs and expires grows the AOF without bound while the dataset stays flat. Compaction keeps restart fast. |
| `maxmemory` | `256mb` (env) | A cache without a ceiling is an OOM waiting to happen: Redis consumes everything, then the kernel kills it — taking the AOF's clean shutdown with it. |
| `maxmemory-policy` | `allkeys-lru` | Everything stored is reconstructible, so evicting a cold key always beats refusing a write. The default (`noeviction`) would make a full Redis return OOM on every SET. |
| `timeout` | `0` (never) | **A Pub/Sub trap.** A healthy subscriber is *by definition* idle. Any non-zero idle timeout disconnects exactly the quiet, working subscribers, and on a slow market day the realtime fan-out flaps continuously. |
| `tcp-keepalive` | `300` | Detects peers that vanished without a FIN (SIGKILLed container, dropped NAT mapping). Server-side half of dead-connection detection. |
| `maxclients` | `512` | Redis's default 10000 is not a limit on a memory-capped container — client buffers are not counted against `maxmemory`, so 10000 clients can OOM before eviction ever engages. 512 is ~16 replicas' worth of headroom; hitting it means a leak, and a clear `-ERR max number of clients reached` beats an OOM kill. |
| `lazyfree-lazy-*` | `yes` (all four) | Redis executes commands single-threaded, so freeing a large value blocks *every* client. The universe-snapshot entries are multi-megabyte; one eviction is a multi-millisecond stall across every replica at once. |
| `client-output-buffer-limit pubsub` | `32mb 8mb 60` | See §5 — this line is why the reconnect loop exists. |
| `enable-protected-configs`<br>`enable-debug-command`<br>`enable-module-command` | `no` (all) | These are the primitives that turn Redis read access into arbitrary file write (`CONFIG SET dir` + `SAVE` → cron entry / `authorized_keys`). Not reachable via `CONFIG SET`, so an attacker holding the password cannot re-enable them. |
| `slowlog` | 10 ms / 256 entries | Redis commands are microsecond-scale; 10 ms means something pathological. 256 entries is the difference between "the slowlog covers the incident" and "it rolled over while you read the alert". |
| `latency-monitor-threshold` | `100` | Makes `LATENCY DOCTOR` name the cause of a p99 spike (fork / expire cycle / eviction) instead of leaving you to guess. |
| `io-threads` | `1` | Only parallelizes socket I/O, never command execution. Helps one workload — many clients moving large values — which this is not. Raising it on a 1–2 CPU container actively hurts. |

### Not configured, on purpose

`replicaof`, `cluster-enabled`, ACL users, `activedefrag` — see the closing block
of `docker/redis/redis.conf` for each rationale and §9 for the migration path.

### ⚠ Container sizing

An AOF rewrite **forks**, and copy-on-write can push peak RSS toward **2×
`maxmemory`** on a write-heavy workload. Size the container's memory limit at
**≥ 2 × `REDIS_MAXMEMORY`**. A container limited to exactly `maxmemory` gets
OOM-killed mid-rewrite — strictly worse than no persistence, because it also
truncates the AOF being rewritten.

---

## 4. Connection lifecycle

### The three mechanisms

They solve different problems and are routinely confused.

| Mechanism | Absorbs | Scope | Budget |
|---|---|---|---|
| **Pool** | connection setup cost; unbounded concurrency | process | 24 connections |
| **Retry** | the failure that *will* succeed if tried again now | one operation | ~500 ms |
| **Circuit breaker** | the failure that *will not* | the dependency | 10 s cooldown |

Without a breaker, a dead Redis makes the **application** slow: every operation
pays a full connect timeout before failing, and every one is a coroutine holding
an event-loop slot — the classic cascade where the outage is caused by the retry
traffic rather than the original fault.

### The breaker

```
   CLOSED ──(5 consecutive connection failures)──▶ OPEN
     ▲                                              │
     │                                    (10s cooldown elapses)
     │                                              ▼
     └────────(trial succeeds)──────────────── HALF_OPEN
                                                    │
                                       (trial fails)│
                                                    ▼
                                                  OPEN
```

- **HALF_OPEN admits exactly one trial.** Admitting all of them means that at the
  moment Redis recovers, every replica's entire backlog arrives at once — a
  reconnect storm against a server that has just finished loading its AOF.
- **Opening the circuit drops the pool.** Every pooled connection is presumed
  dead; keeping them means the trial likely picks a stale one and fails for a
  reason unrelated to recovery, so the breaker would never close.
- **Command errors do not count.** A `WRONGTYPE` is a healthy server answering a
  buggy call site. Counting it would let one code bug disable the cache globally.
  Only `ConnectionError`, `TimeoutError`, `BusyLoadingError`, `AuthenticationError`
  and `OSError` are connection-level.
- **The readiness probe drives recovery detection.** When the breaker is open the
  probe returns "unhealthy" in microseconds without a round-trip; once the cooldown
  elapses, the probe's own call *is* the half-open trial. Recovery is detected on a
  polling cadence that already exists.

### What replaced what

Before PH2.7, `services/cache.py` latched `_redis_failed = True` on the first
failure and **never cleared it**. One transient blip — a Redis restart during a
deploy, a two-second partition, an AOF-rewrite pause — permanently demoted the
process to its in-memory fallback for its entire lifetime. Nothing raised, no
request failed, no alert fired. The process just silently stopped sharing cache
state with its peers until someone restarted it for an unrelated reason.

The health probe worked around this by building a brand-new client on every poll
— a TCP connect + AUTH + teardown several times a minute, forever, per replica.
Both are gone.

### Degradation is a real mode

When Redis is unavailable every read and write silently uses the bounded
in-process store. The process keeps serving; what it loses is **sharing**. Two
replicas hold divergent caches and serve slightly different data for the same
symbol until TTLs expire.

That is why Redis is registered as a **non-critical** health check: an unhealthy
Redis is reported but does not pull the instance out of the load balancer, because
the instance can still serve.

---

## 5. Pub/Sub

### Why reconnect is a feature and not a nicety

`client-output-buffer-limit pubsub 32mb 8mb 60` is the most important line in
`redis.conf` for realtime correctness.

Redis Pub/Sub has **no backpressure** — the publisher never waits for subscribers.
If a subscriber stops reading (blocked event loop, GC pause, network stall) the
server buffers on its behalf. Without a limit, one stuck subscriber makes Redis
consume unbounded memory and eventually take down the instance for everyone. The
limit converts that into a bounded, local failure: the slow subscriber is dropped.

**The corollary most implementations get wrong: being disconnected is normal
operation, not an exceptional error.**

The pre-PH2.7 listener ended permanently on the first exception. Nothing looked
broken — HTTP worked, the cache worked, health checks passed, because *pinging
Redis* and *being subscribed to it* are different facts. The only symptom was that
WebSocket clients on that one replica silently stopped receiving cross-process
events. To the user: "the market went quiet." To the operator: nothing at all.

### Guarantees

| Guarantee | Mechanism | Failure it prevents |
|---|---|---|
| Dedicated connection | `single_connection_client=True`, separate from the pool | A `SUBSCRIBE`-mode connection cannot serve a `GET`; sharing the pool would remove one from circulation for the process's lifetime |
| Automatic reconnect | supervised loop, no exit on error | Silent permanent loss of realtime delivery |
| Exponential backoff + jitter | 0.5s → 30s cap, ×[0.5, 1.5] | Without jitter every replica retries on the same schedule and arrives at the recovering server in synchronised waves |
| One subscriber per channel | module-level registry in `redis_pubsub` | Duplicate delivery — harder to notice than *no* delivery, because the UI just updates twice |
| Graceful shutdown | `get_message(timeout=1.0)` polling, not `listen()` | `listen()` blocks forever, so ending it means cancelling mid-await — skipping the clean UNSUBSCRIBE and leaving a server-side client entry |
| Contained failures | bad payload dropped + counted; raising handler logged + counted | One bad message costing the process its subscription |
| No `socket_timeout` | keepalive only, on this connection | A quiet subscriber is a *healthy* subscriber; a read timeout would tear it down every 2 idle seconds |

### Measured backoff ladder

| Attempt | Range | Median | Cumulative |
|---|---|---|---|
| 1 | 0.25 – 0.75 s | 0.51 s | 0.5 s |
| 2 | 0.51 – 1.49 s | 0.99 s | 1.5 s |
| 3 | 1.01 – 2.98 s | 2.11 s | 3.6 s |
| 4 | 2.01 – 5.96 s | 4.33 s | 7.9 s |
| 5 | 4.05 – 12.00 s | 8.23 s | 16.2 s |
| 6 | 8.03 – 23.96 s | 15.51 s | 31.7 s |
| 7+ | 15.16 – 45.00 s | ~30 s | capped |

**A short blip (Redis restart, ~2–5 s) is recovered on attempt 1–3, i.e. within
~1–4 seconds of the server accepting connections again.** The cap matters as much
as the growth: an uncapped exponential reaches hours, and a subscriber that gives
up for an hour is indistinguishable from one that never reconnects.

### What Pub/Sub cannot do

Redis Pub/Sub is **at-most-once**. Messages published while a subscriber is
disconnected are gone — no replay, no offset, no acknowledgement. Reconnecting
restores the *stream*, not the *gap*.

Acceptable here **and only here**: the bridged events are UI refresh signals for
live market data. A missed `price.updated` is corrected by the next one seconds
later, and the frontend also polls.

> **If anything where a missed message is a lost fact ever rides this path** — an
> order fill, a payment, a job to execute — it needs **Redis Streams** (consumer
> groups, acknowledgements, replay from an offset) or a real broker. The instinct
> when the first dropped message is noticed is to raise the buffer limit. That is
> the wrong lever.

---

## 6. Performance

### What was already right (Sprint R9, preserved)

`cache_get_many` / `cache_set_many` collapse N operations into one `MGET` /
one pipelined round-trip. **Round-trip count is the number that matters**: each GET
is ~100 µs of Redis work but ~0.5 ms of network + event-loop overhead, so fifty
sequential GETs cost ~25 ms of wall clock spent almost entirely waiting. The
universe-quote warm-up uses `MGET` to collapse ~50 round-trips into one.

`cache_set_many` uses `transaction=False` — a plain command pipeline, not
MULTI/EXEC. These are independent cache writes with no invariant between them, so
atomicity buys nothing while making Redis block on the whole batch and, under
Cluster, refuse it for spanning slots.

### PH2.7 measurements

Code-path cost with the transport stubbed (isolates the wrapper, excludes network):

| Path | Cost |
|---|---|
| `cache_get()` facade overhead | **3.5 µs/op** |
| `cache_set()` facade overhead (incl. `json.dumps`) | **7.3 µs/op** |
| `execute()` while circuit **OPEN** | **1.1 µs/op** |
| — connect timeout it replaces | 1,500,000 µs/op (**~1.3 million× cheaper**) |

The last row is the entire value of the breaker: while Redis is down, an operation
costs a microsecond instead of a second and a half, so a dead dependency does not
make the application slow.

Instrumentation overhead is ~3.5 µs against a network round-trip of ~500 µs —
under 1%.

### Serialization

JSON with `default=str`. Deliberately not pickle (arbitrary code execution on
deserialization — a Redis compromise would become an RCE in every replica) and not
MessagePack (a dependency and an opaque wire format, for a saving that is
irrelevant next to the round-trip). Values are encoded **before** the Redis call, so
an encoding bug is not misdiagnosed as a Redis failure and does not count against
the breaker.

---

## 7. Health & monitoring

### Endpoints

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /api/health/ready` | public | One bit: is Redis reachable (non-critical) |
| `GET /api/metrics` | token in prod | Prometheus exposition |
| `GET /api/diagnostics/redis` | token in prod | Connection + Pub/Sub + server, in one payload |
| `GET /api/diagnostics/redis?refresh=1` | token in prod | Forces a fresh `INFO` round-trip |

`/api/diagnostics/redis` is the 3am endpoint. Readiness answers *is Redis up*;
this answers *what is actually wrong* — including the **pubsub** section, which has
no equivalent in a ping: a process can pass every Redis health check while its
subscription is dead.

The Redis URL is always redacted (`redis://***@host:6379/0`) in this payload and in
every log line, because redis-py's connection errors stringify to a message
containing the password.

### Metrics

**Client-side** — from in-process counters at render time. A scrape costs nothing
and can never add load to Redis:

| Metric | Type | Notes |
|---|---|---|
| `redis_up` | gauge | 0 with `REDIS_URL` set = running on the fallback |
| `redis_circuit_state` | gauge | 0 closed, 1 half-open, 2 open |
| `redis_pool_connections{state}` | gauge | `in_use` approaching `max` = commands queueing |
| `redis_commands_total{operation,outcome}` | counter | `outcome=unavailable` = never sent |
| `redis_command_duration_seconds{operation}` | histogram | buckets start at 100 µs |
| `redis_connection_errors_total` | counter | link failures only |
| `redis_pubsub_reconnects_total{channel}` | counter | |
| `redis_pubsub_messages_total{channel,disposition}` | counter | received / published / dropped / handler_error |

**Server-side** — sampled by a background task every `REDIS_STATS_INTERVAL_SECONDS`
(default 30), **never at scrape time**, so whoever can reach `/api/metrics` cannot
generate Redis load by scraping faster:

`redis_server_memory_used_bytes`, `redis_server_memory_max_bytes`,
`redis_server_connected_clients`, `redis_server_evicted_keys_total`,
`redis_server_expired_keys_total`, `redis_server_rejected_connections_total`

### Suggested alerts

| Expression | Severity | Means |
|---|---|---|
| `redis_circuit_state == 2` for 2m | warning | Degraded to in-process cache; replicas diverging |
| `rate(redis_pubsub_reconnects_total[15m]) > 0` with no restart | warning | Subscribers dropped for slow consumption — see §5 |
| `redis_server_rejected_connections_total > 0` | critical | `maxclients` reached: leak or unplanned scale-up |
| `redis_server_memory_used_bytes / redis_server_memory_max_bytes > 0.9` | warning | Working set outgrowing `maxmemory` |
| `redis_pool_connections{state="in_use"} / max > 0.8` | warning | Slow commands, not too few connections |
| `redis_up == 0` and `REDIS_URL` set | warning | Leading indicator — users see nothing yet |

`redis_circuit_state` is the single most useful series: it is a **leading**
indicator, because the fallback keeps serving while it climbs.

---

## 8. Operations

### Configuration reference

| Variable | Default | Notes |
|---|---|---|
| `REDIS_URL` | *(unset)* | Unset = single-process, in-memory fallback. Must include a password. |
| `REDIS_MAX_CONNECTIONS` | 24 | Raising it is rarely the fix for slowness — a saturated pool usually means a slow *command* |
| `REDIS_CONNECT_TIMEOUT_SECONDS` | 1.5 | **Must stay below `HEALTH_PROBE_TIMEOUT_SECONDS` (2.0)** |
| `REDIS_SOCKET_TIMEOUT_SECONDS` | 2.0 | Per-command |
| `REDIS_HEALTH_CHECK_INTERVAL_SECONDS` | 30 | PING a pooled connection if idle longer. **This is what makes a Redis restart invisible** |
| `REDIS_RETRY_ATTEMPTS` | 2 | Connection errors and timeouts only |
| `REDIS_CIRCUIT_FAILURE_THRESHOLD` | 5 | |
| `REDIS_CIRCUIT_RESET_SECONDS` | 10 | |
| `REDIS_STATS_INTERVAL_SECONDS` | 30 | 0 disables the sampler |
| `REDIS_MAXMEMORY` | 256mb | Compose-level; size the container at ≥2× this |
| `REDIS_MAXMEMORY_POLICY` | allkeys-lru | Compose-level |
| `REDIS_PASSWORD` | *(required)* | `.env`, consumed by Compose. No default anywhere. |

### Troubleshooting

**"Realtime updates stopped on one replica"** — the classic. Check
`/api/diagnostics/redis` → `pubsub.subscribers.<channel>.connected`. If `false`
with a climbing `reconnects_total`, the subscriber is being dropped: check
`client-output-buffer-limit` violations in the Redis log and whether that replica's
event loop is blocked.

**"`redis_up` is 0 but Redis is fine"** — the circuit is open. `circuit_state` and
`last_error` in the diagnostics payload name the cause. It closes on its own within
`REDIS_CIRCUIT_RESET_SECONDS` of recovery; no restart needed.

**"Cache hit rate collapsed"** — check `redis_server_evicted_keys_total`. Steady
growth is normal for an LRU cache at capacity; a step change means the working set
outgrew `maxmemory`.

**"Redis restart takes a long time"** — it is replaying the AOF and answers
`-LOADING` meanwhile. `redis-cli INFO persistence` → `loading:1`. If this is slow,
the AOF is not being rewritten: check `aof_rewrite_in_progress` and
`auto-aof-rewrite-*`. The container health check allows a 30 s start period for it.

**"Redis was OOM-killed"** — almost always an AOF rewrite fork against a container
limited to exactly `maxmemory`. See §3 ⚠.

### Commands

```bash
# What is actually running (never trust the file alone)
docker compose exec redis redis-cli CONFIG GET maxmemory-policy
docker compose exec redis redis-cli INFO persistence
docker compose exec redis redis-cli INFO memory

# Latency and slow commands
docker compose exec redis redis-cli --latency
docker compose exec redis redis-cli SLOWLOG GET 10
docker compose exec redis redis-cli LATENCY DOCTOR

# Who is connected, and what is subscribed
docker compose exec redis redis-cli CLIENT LIST
docker compose exec redis redis-cli PUBSUB CHANNELS

# Application view
curl -s -H "X-Metrics-Token: $METRICS_TOKEN" localhost:8000/api/diagnostics/redis | jq
```

### Verification (needs a live stack)

The automated suite (`backend/tests/test_redis_infrastructure.py`, 50 tests) covers
every state machine hermetically — a test that needs `docker kill redis` either
does not run in CI or runs flakily, and the reliability code would end up the least
tested in the repo. These four need a real server:

```bash
docker compose up -d && sleep 20

# 1. Persistence survives a restart
docker compose exec backend curl -s localhost:8000/api/stocks/RELIANCE >/dev/null
docker compose restart redis && sleep 10
docker compose exec redis redis-cli DBSIZE          # expect > 0

# 2. Connection recovery — no restart of the backend required
docker compose stop redis
curl -s localhost:8000/api/health/ready | jq '.checks[] | select(.name=="redis")'
docker compose start redis && sleep 15
curl -s localhost:8000/api/health/ready | jq '.checks[] | select(.name=="redis")'
#   → status returns to "pass"; circuit_state returns to "closed"

# 3. Pub/Sub reconnect (the PH2.7 regression)
docker compose restart redis && sleep 15
curl -s -H "X-Metrics-Token: $METRICS_TOKEN" \
  localhost:8000/api/diagnostics/redis | jq '.pubsub.subscribers'
#   → connected: true, reconnects_total: >= 1

# 4. Eviction under pressure
docker compose exec redis redis-cli CONFIG SET maxmemory 8mb
# ...drive traffic...
docker compose exec redis redis-cli INFO stats | grep evicted_keys
docker compose exec redis redis-cli CONFIG SET maxmemory 256mb
```

---

## 9. Failover and the path beyond one node

**Today: a single node with no failover.** If the Redis container is lost, every
replica degrades to its in-process cache and cross-process realtime delivery stops
until it returns. The application keeps serving. This is a deliberate, documented
limitation, not an oversight — and it is acceptable precisely because nothing here
is a system of record (§1).

The migration path, in the order it becomes worth doing:

**1. Redis Sentinel** — *when:* Redis being down for minutes is unacceptable.
Automatic failover to a replica. `_build_client()` switches to
`Sentinel(...).master_for(...)`. Requires ≥3 sentinels for quorum. **Cost:** a
failover loses the writes not yet replicated (Redis replication is asynchronous),
which for this cache means a partial cold start — acceptable, and worth knowing
before you assume failover is free.

**2. A managed Redis** (ElastiCache / Memorystore / Redis Cloud) — *when:* the team
would rather not operate the above. Usually the right answer at this stage: the
change is a `REDIS_URL` and a TLS flag. Verify the managed offering's
`maxmemory-policy` default — several ship `noeviction`, which for this workload
would silently disable the shared cache.

**3. Redis Cluster** — *when:* one node cannot hold the working set or serve the
throughput. **Not a drop-in.** It changes key routing and forbids multi-key
commands across slots, which breaks `cache_get_many`'s `MGET` and
`cache_set_many`'s pipeline unless keys are hash-tagged into the same slot. Also
changes Pub/Sub semantics (use sharded Pub/Sub). At 256 MB of cache this is far
away; do not pre-build for it.

**In all three cases the blast radius is `RedisManager._build_client()`.** That is
the reason every consumer routes through this module.

---

## 10. See also

- `docker/redis/redis.conf` — every server setting with its rationale inline
- `backend/infrastructure/redis_client.py` — connection, pool, breaker
- `backend/infrastructure/redis_pubsub.py` — subscriber, reconnect
- `backend/services/cache.py` — cache policy and the in-memory fallback
- [`docs/operations/MONITORING.md`](../operations/MONITORING.md) — the metrics pipeline (PH2.5)
- [`docs/deployment/DOCKER_COMPOSE.md`](../deployment/DOCKER_COMPOSE.md) — stack topology (PH2.2)
- [`docs/deployment/SECRETS.md`](../deployment/SECRETS.md) — `REDIS_PASSWORD` delivery (PH2.3)
