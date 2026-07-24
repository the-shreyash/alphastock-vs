# Production Logging Infrastructure

**Status:** PH2.6 complete (2026-07-22)
**Audience:** SRE, DevOps, on-call engineers, compliance reviewers
**Code:** `backend/observability/logging.py` · `log_streams.py` · `log_rotation.py`
**Related:** [MONITORING.md](MONITORING.md) · [DOCKER.md](../deployment/DOCKER.md) · [DOCKER_COMPOSE.md](../deployment/DOCKER_COMPOSE.md) · [runbooks.md](runbooks.md) · [incident-response.md](incident-response.md)

---

## 1. What this gives you, and what it deliberately does not

PH2.5 made the application's logs *structured*. It stopped at stdout, which is
the twelve-factor contract and the right default: a containerised process should
not manage log files.

That default assumes a platform. Real deployments spend time without one — the
first VM, the on-prem install, the compliance rule that says "retain
authentication events for 90 days on durable storage", and the incident where
the log platform itself is what broke. In each of those, `docker logs` is what
you have, and with the default `json-file` driver that is **an unbounded file on
the host**. A service that logs steadily fills the disk, and a full disk does not
take down the noisy container — it takes down every container on the box, plus
the SSH daemon you were going to use to fix it.

PH2.6 answers the only question that matters about a log file: *what stops it?*

| Capability | Answers |
|---|---|
| Stream separation | Which records are these, and how long must they be kept? |
| Size rotation | What stops one file from filling the disk? |
| Retention (age + count) | What stops the *directory* from filling the disk? |
| Compression | What does a month of logs actually cost to store? |
| Bounded queue | What stops the disk from stalling the event loop? |
| Redaction | What guarantees a credential is never written down? |
| Docker log options | What caps the container runtime's own copy? |

Explicitly **not** in scope and still open: Loki, ELK, CloudWatch, Datadog,
Splunk, OpenSearch, alerting, distributed tracing. This sprint builds the seam
those attach to — see §9.

---

## 2. Architecture

```
                    Application code
              (logger.info / .warning / .exception)
                            │
                            │  request_id from contextvar
                            ▼
              ┌──────────────────────────────┐
              │   Root logger (one config)   │  observability.logging
              └──────────────┬───────────────┘
                             │
            ┌────────────────┴─────────────────┐
            │                                  │
            ▼                                  ▼
   ┌──────────────────┐          ┌──────────────────────────────┐
   │ StreamHandler    │          │ BoundedQueueHandler          │
   │ → STDOUT         │          │ → in-memory queue (10k)      │
   │ ALWAYS ON        │          │ ONLY IF LOG_TO_FILES=1       │
   │ formats inline   │          │ put_nowait; drops if full    │
   └────────┬─────────┘          └──────────────┬───────────────┘
            │                                   │
            │                     ═══════ thread boundary ═══════
            │                                   │
            │                                   ▼
            │                     ┌──────────────────────────────┐
            │                     │ QueueListener (bg thread)    │
            │                     │  owns ALL file I/O,          │
            │                     │  rotation and compression    │
            │                     └──────────────┬───────────────┘
            │                                    │  routed by logger name
            │              ┌─────────┬───────────┼───────────┬─────────┐
            │              ▼         ▼           ▼           ▼         ▼
            │        application  access    security     audit     error
            │           .log       .log       .log        .log      .log
            │              └─────────┴───────────┴───────────┴─────────┘
            │                          /var/log/stockassist
            │                    rotated · gzipped · pruned
            │                                    │
            ▼                                    ▼
   Docker json-file driver              Named volume backend_logs
   (10 MB × 3, non-blocking)            (survives redeploy)
            │                                    │
            └──────────────┬─────────────────────┘
                           ▼
          Future: ELK · Loki · CloudWatch · Datadog  (PH2.10)
```

Two independent paths, on purpose. **Stdout is unconditional**; enabling files
adds a sink and never removes one, so `docker logs`, `kubectl logs` and any
already-attached collector behave exactly as before.

---

## 3. The five streams

Separation is derived entirely from **logger names**, which already carry the
information. No call site changed to get this.

| Stream | File | Selected by | Volume | Retention want | Read by |
|---|---|---|---|---|---|
| `audit` | `audit.log` | `security.audit.events` | Lowest | Months–years | Compliance, forensics |
| `security` | `security.log` | `security.*` | Low | Months | Abuse investigation, limit tuning |
| `access` | `access.log` | `stockassist.access` | Highest | Days | Capacity planning, latency triage |
| `application` | `application.log` | everything else | High | Days | Debugging a specific failure |
| `error` | `error.log` | any logger at ERROR+ | Low | Weeks | "What is broken right now?" |

**Why separate at all.** Storing "this admin changed that user's role" under the
same retention rule as an access log line forces a choice between paying to keep
26 million `GET /api/health` lines for a year, and deleting the audit trail after
a week. Both are wrong, and no amount of clever querying fixes it once the data
is gone. Separating at write time is what makes per-stream retention possible.

**Order is significant.** `security.audit.events` is a child of `security`, so
the audit stream is matched *first*; otherwise the security stream would swallow
every audit record and `audit.log` would be permanently empty. Matching is
prefix-on-a-dot-boundary, so `security` claims `security.csrf` but never
`securityfoo`.

**`error.log` is a view, not a partition.** An ERROR from `stockassist.access`
appears in *both* `access.log` and `error.log`. Making it exclusive would mean
the access log silently loses exactly its 5xx lines — the ones you go to the
access log to find.

---

## 4. Rotation and retention

Three independent bounds, because each one fails alone:

* `max_bytes` alone bounds a single file but not the directory.
* `backup_count` alone bounds the directory but lets a low-traffic service keep
  segments from 2019 forever.
* `retention_days` alone bounds age but not size — one bad day still fills the
  disk inside the retention window.

Together they bound the footprint under both a traffic spike and a long quiet
period, which are the two shapes real services actually have.

**Age is applied before count.** Applying count first could retain a segment
that age has already expired, quietly breaking a "we do not keep request logs
longer than N days" commitment — the kind of rule that exists for legal reasons
rather than disk reasons.

### Segment naming

Rotated segments are timestamped, not shifted:

```
application.log                          ← live
application.log.20260722T134501.gz       ← rotated, compressed
application.log.20260722T134501-2.gz     ← second rotation within the same second
```

Not the stdlib's `.1 .2 .3` scheme, which renames every backup on every rotation
(N renames), makes a file's name change meaning over time (today's `.3` is not
tomorrow's), and interacts badly with compression. A timestamped name is written
once, never changes, sorts chronologically under a plain `ls`, and makes "the
logs from around 13:45" a glob. This is what `logrotate`'s `dateext` does.

**The pruner only deletes files it can prove it created.** A name that does not
parse as a segment — `application.log.keepme`, an editor swap file, an
operator's copy — is invisible to retention and is never removed.

### Capacity

Defaults, and what they cost (measured, §8):

| Setting | Default | Effect |
|---|---|---|
| `LOG_FILE_MAX_BYTES` | 50 MB | Rotate at 50 MB |
| `LOG_FILE_BACKUP_COUNT` | 10 | Keep 10 segments per stream |
| `LOG_RETENTION_DAYS` | 14 | Delete segments older than 14 days |
| `LOG_FILE_COMPRESS` | on | gzip level 6 (~8:1 on real logs) |

* **Worst case, uncompressed:** 550 MB per stream, **2.7 GB** across all five —
  the number to plan volume capacity against, because it must hold for the
  moment *before* the compressor runs.
* **Steady state, compressed:** ~110 MB per stream, **~560 MB** total.

Enabling all five streams on a 20 GB root volume is comfortable. Ten times these
limits would still "work", right up until the day it did not.

---

## 5. Redaction — what can never reach a log file

Redaction matters most on disk, because a file persists. Four layers, verified
by tests in `backend/tests/test_log_infrastructure.py`:

| Layer | Mechanism | Catches |
|---|---|---|
| Never collected | Middleware strips query strings; bodies and headers are never logged | OAuth `code`/`state`, reset tokens, `Authorization`, `Cookie`, passwords, PII in bodies |
| Structured fields | `security.audit.redact_fields` key markers | `password`, `token`, `api_key`, `secret`, `cookie`, `authorization`, … at any nesting depth |
| Message scrubbing | `LOG_SCRUB_MESSAGES` regex on free text | `token=...`, `password: ...`, `Authorization: Bearer ...` in legacy f-string log calls |
| Exception messages | Same scrubber applied to `str(exc)` | Mongo URIs — a pymongo connection error stringifies *with the credentials in it* |

Three details that are load-bearing and easy to break:

1. **One list, not two.** The redactor is the same one `security.audit` uses, so
   a marker added for a new credential type protects the audit log and the
   application log together.
2. **`status_code` is exempt** from the key-marker redactor. It contains the
   substring `code` (a marker, present for OAuth authorization codes), and
   without a narrow allowlist of observability-owned schema fields the
   most-queried field in the access log would read `[REDACTED]`. The fix is to
   name the fields this package owns, never to weaken the security control.
3. **Failure is fail-closed.** If the redactor cannot run, the fields are
   dropped (`[REDACTION-UNAVAILABLE]`) rather than emitted raw. Losing
   structured context is recoverable; writing a credential to a file that gets
   shipped, indexed and backed up is not.

The message scrubber requires an explicit `=` or `:` separator. An earlier
revision also accepted bare whitespace and corrupted ordinary prose — the config
validator's own warning, *"MONGO_URL carries no username:password — the database
is either unauthenticated…"*, came out with the em dash read as a value. A
scrubber that quietly damages legitimate messages costs more than it saves.

---

## 6. Docker integration

### Path A — the container runtime's copy (always on)

Configured once in `docker-compose.yml` via the `x-logging` anchor:

```yaml
driver: json-file
options:
  max-size: "10m"        # bounded — the default is UNBOUNDED
  max-file: "3"
  mode: "non-blocking"   # a stalled log backend must not stall the event loop
  max-buffer-size: "4m"
```

`mode: non-blocking` is the line worth understanding. The default `blocking`
means that if the logging driver stalls, `write()` to stdout blocks — and in an
asyncio server that stalls **the event loop**, so a slow log backend becomes an
application-wide outage on requests that never logged anything. Non-blocking
buffers and, if the buffer fills, drops log lines instead of the service.

30 MB per service makes `docker logs` a **triage** tool, not an archive: at a few
hundred requests a second that is minutes of history. Durable retention comes
from shipping off-host or from the file sinks — raising these numbers to get
retention trades a bounded footprint for an unbounded one and still guarantees
nothing.

### Path B — the application's own files (opt-in)

```bash
LOG_TO_FILES=1                      # off by default
LOG_DIR=/var/log/stockassist        # FHS location, set by compose
```

Two things make this work in a container running as a non-root user:

* **The image pre-creates the directory.** `backend/Dockerfile` runs
  `install -d -o appuser -g appuser -m 0750 /var/log/stockassist`. When Docker
  mounts a named volume onto a path that does not exist in the image, it creates
  it **root-owned** — the app (uid 10001) then gets `EACCES` on first write,
  which surfaces as a stderr warning and silently missing log files. Pre-creating
  it makes Docker copy that ownership onto a fresh volume instead.
* **The volume is mounted unconditionally.** `backend_logs:/var/log/stockassist`
  is always mounted, even with `LOG_TO_FILES=0`, where it stays empty and costs
  nothing. Requiring operators to add a mount at the same moment they enable
  file logging guarantees the case where logs land on the container's writable
  layer, look perfectly fine under `docker exec`, and vanish on the next deploy.

```bash
docker compose exec backend ls -la /var/log/stockassist
docker compose exec backend tail -f /var/log/stockassist/error.log
docker compose exec backend sh -c 'gzip -dc /var/log/stockassist/audit.log.*.gz' | jq .
```

### Supported logging drivers

The `x-logging` anchor is the **entire** integration point. Point it at a
platform and no application code changes, because the application only ever
writes to stdout.

| Platform | Driver | Notes |
|---|---|---|
| Loki | `loki` | Needs the `loki-docker-driver` plugin installed on the host |
| ELK / Fluentd | `fluentd` | `fluentd-address`, `tag: stockassist.{{.Name}}` |
| CloudWatch | `awslogs` | `awslogs-region`, `awslogs-group` |
| Datadog | *(keep json-file)* | Agent sidecar reads `/var/lib/docker/containers` `:ro` |
| Splunk | *(keep json-file)* or `splunk` | Universal Forwarder reads the same path |

**One caveat before switching.** With any driver other than `json-file` or
`journald`, **`docker logs` stops working** — the daemon no longer keeps a local
copy. That is fine while the platform is healthy and extremely painful during
the incident where the platform is what broke. It is the reason the backend can
also write local files, and the reason `json-file` remains the default here.

Because every record is already a flat JSON object with a stable schema
([MONITORING.md §5](MONITORING.md#5-structured-logging)), no grok pattern, no
multiline joining rule and no parser configuration is needed by any of them.

---

## 7. Configuration reference

All optional; all default to off or to a safe value. Every one of them degrades
with a stderr warning rather than failing the boot — a logging misconfiguration
must never be able to stop a deployment.

| Variable | Default | Purpose |
|---|---|---|
| `LOG_TO_FILES` | `0` | Master switch for file sinks |
| `LOG_DIR` | `/var/log/stockassist` | Where files are written |
| `LOG_FILE_STREAMS` | all five | Subset, e.g. `audit,security` |
| `LOG_FILE_MAX_BYTES` | `52428800` | Rotate at this size (clamped to 64 KiB – 2 GiB) |
| `LOG_FILE_BACKUP_COUNT` | `10` | Segments kept per stream (0 = keep none) |
| `LOG_RETENTION_DAYS` | `14` | Max segment age (0 = disable age pruning) |
| `LOG_FILE_COMPRESS` | `1` | gzip rotated segments |
| `LOG_QUEUE_SIZE` | `10000` | Records buffered before the writer (100 – 1,000,000) |

Inherited from PH2.5: `LOG_LEVEL`, `LOG_FORMAT`, `LOG_SCRUB_MESSAGES`,
`LOG_HEALTH_REQUESTS`. Full descriptions live in `backend/.env.example`, which
is generated from `security/secrets.py` — that registry is the single source of
truth, and CI fails if the two drift.

**Common configurations**

```bash
# Single VM, no collector: durable local logs
LOG_TO_FILES=1

# Collector takes everything, but compliance needs the audit trail on disk
LOG_TO_FILES=1
LOG_FILE_STREAMS=audit,security
LOG_RETENTION_DAYS=365

# Kubernetes with a log platform: stdout only (the default — change nothing)
```

---

## 8. Measured cost

Measured on the development host (Apple Silicon, APFS), 50,000 records, JSON
formatter, single stream. Absolute numbers vary by host; the *ratios* are the
point.

| Measurement | Result |
|---|---|
| Caller-thread cost, file sinks **on** | **5.90 µs/record** (169k records/sec) |
| Caller-thread cost, stdout only (PH2.5) | 12.34 µs/record (81k records/sec) |
| Sustained end-to-end (writer keeps up, 0 dropped) | **~31,000 records/sec** |
| Rotation + gzip, 1 MB segment | median **9.2 ms**, max 10.6 ms |
| Rotation + gzip, 50 MB segment (extrapolated) | ~460 ms |
| Compression ratio, realistic JSON access logs | **8.1 : 1** (12.4% of original) |
| Average structured log line | 385 bytes |

Two results worth reading twice:

**Enabling file logging made the caller *faster*** (12.34 → 5.90 µs). That is
not a mistake: with the queue, JSON formatting moves off the calling thread onto
the listener. The request path does a shallow record copy, a context snapshot
and a `put_nowait`. This is the entire justification for the queue.

**Rotation costs ~460 ms at the default 50 MB**, paid entirely on the listener
thread. Inline, that would be a p99.99 latency cliff with no visible cause — one
unlucky request in a few hundred thousand stalling for half a second. Behind the
queue it is invisible to requests; the queue simply buffers ~1,400 records at a
realistic 3k records/sec, well inside its 10,000 depth.

**Overload behaviour.** A 50,000-record *instantaneous* burst against the
default 10k queue dropped 79.8% of records. That is the design working: bounded
memory, and every loss counted in `log_records_dropped_total`. An unbounded
queue in front of a stalled disk is a memory leak with extra steps whose failure
mode is an OOM kill — losing the logs anyway, plus the service. Dropping
telemetry to keep a trading backend alive is the right trade; doing it silently
is not.

---

## 9. Troubleshooting

**No files appear.**
`LOG_TO_FILES` is off (the default), or the directory is not writable. The boot
log's `logging_configured` line reports the effective configuration —
`destination` reads `stdout` or `stdout+files`, and `file_sinks.reason` says
why. The application never fails to boot over this.

```bash
docker compose exec backend env | grep LOG_
docker compose logs backend | grep -i 'observability.log'   # stderr warnings
```

**`log_records_dropped_total` is non-zero.**
The disk could not keep up: the files have gaps. Either the volume is too slow
or the log level is too verbose for it. Raise `LOG_QUEUE_SIZE` only to absorb
*bursts* — for a sustained overload, reduce volume (`LOG_LEVEL=WARNING`, drop
`access` from `LOG_FILE_STREAMS`) or move to a faster volume.

**Disk filling despite rotation.**
Check whether something is holding a deleted file open (`lsof +L1`), whether
`LOG_FILE_STREAMS` is writing more streams than expected, and whether the
container's `json-file` copy — a *separate* 30 MB budget under
`/var/lib/docker/containers` — is what actually grew.

**`request_id` is `"-"` in a file.**
Expected for records emitted outside a request (boot, scheduler ticks,
background loops). Inside a request it must be populated: the context is
snapshotted onto the record at enqueue time precisely because the listener
thread cannot see the request's `contextvar`. If it is missing *inside* a
request, that is a regression — `test_request_id_is_preserved_into_the_file_sink`
covers it.

**A field reads `[REDACTED]` that should not.**
The audit redactor's key markers are deliberately broad. If the field is owned
by observability code and provably non-sensitive, add it to `_SCHEMA_FIELDS` in
`observability/logging.py`. Never widen the marker list — that weakens the
control for every other caller.

---

## 10. Known limitations

1. **Per-container, not centralized.** Each replica writes its own files. With
   more than one replica, "show me this request across the fleet" still requires
   shipping — the collector is PH2.10.
2. **`docker compose down -v` destroys the log volume** along with the
   databases. For an audit trail under a retention obligation, ship those
   records off-host; one volume on one machine is not a retention strategy.
3. **No time-based rotation.** Size is the bound that protects the disk;
   wall-clock rotation produces a 4 GB file on a busy day and a 2 KB file on a
   quiet one. Age is handled where it belongs — as retention on already-rotated
   segments.
4. **Retention runs at rotation time**, not on a timer. A stream that stops
   receiving records keeps its segments past `LOG_RETENTION_DAYS` until the next
   rotation. This is bounded and safe (the count still caps the directory) but it
   is not a wall-clock guarantee for a fully idle stream.
5. **Multi-worker.** With `WEB_CONCURRENCY > 1`, every worker opens the same
   files. Safe for appends under POSIX, but two workers can rotate concurrently.
   Keep `WEB_CONCURRENCY=1` (already required until PH2.8) and scale with
   replicas.
6. **The audit stream is a copy, not the record of authority.** The durable
   audit trail remains its MongoDB sink; `audit.log` is a second, more portable
   copy for forensics and shipping.

---

## 11. What comes next

| Sprint | Work |
|---|---|
| PH2.7 | Redis infrastructure — a load-bearing dependency this logging pipeline will report on |
| PH2.10 | Centralized logging: point `x-logging` at Loki/ELK/CloudWatch, or run a shipper against `backend_logs`. The JSON schema in [MONITORING.md §5](MONITORING.md#5-structured-logging) is the collector contract |
| PH2.10 | Error tracking (Sentry), alerting on `log_records_dropped_total` and the error-rate signal |
| Later | Distributed tracing — `request_id` is already the correlation key a trace ID would extend |

---

## 12. Document history

| Date | Change |
|---|---|
| 2026-07-22 | PH2.6 — created. Log streams, rotation, retention, compression, redaction verification, Docker integration. |
