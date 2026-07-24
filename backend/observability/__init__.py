"""StockAssist AI observability package (PH2.5).

Home for the cross-cutting instrumentation that makes a running deployment
*legible* — the answer to "is it up?", "is it healthy?", "how fast is it?",
"what happened to request X?" and "which build is this?". Current tenants:

* `observability.context` (PH2.5) — request correlation. A `contextvars`-backed
  request context carrying the request ID (and method/path) for the lifetime of
  one request, so any log line emitted anywhere in the call tree — middleware,
  service, repository, background helper — can be joined back to the request
  that caused it without threading an argument through every signature. The one
  place a request ID is generated, validated, or read. Deliberately dependency
  free (it imports nothing from this codebase) so every other module here, and
  `security.audit`, can import it without a cycle.
* `observability.logging` (PH2.5) — structured logging. The JSON log record
  schema, the boot-time `configure_logging()` that owns the root logger, the
  access-log emitter, and message/field scrubbing. The one place log output is
  shaped, and the one place it is configured — PH2.6's file sinks are installed
  from inside it rather than through a second entry point. Stdout is
  unconditional (12-factor): shipping is the platform's job.
* `observability.log_streams` (PH2.6) — log stream separation and the file-sink
  pipeline. Routes records into application / access / security / audit / error
  streams by logger name (so no call site changed), and puts every file handler
  behind a bounded `QueueListener` so disk I/O and rotation never run on the
  event loop. Opt-in via `LOG_TO_FILES`; degrades to stdout-only rather than
  failing a boot.
* `observability.log_rotation` (PH2.6) — the rotation, compression and
  retention policy: size-triggered rollover to timestamped segments, gzip, and
  pruning by both age and count. The answer to "what stops this file growing?"
* `observability.metrics` (PH2.5) — an in-process, dependency-free metrics
  registry (counter / gauge / histogram) rendered in the Prometheus text
  exposition format. No Prometheus client library and no server — the format is
  the de-facto standard and costs nothing to emit, so PH2.10 can point a scraper
  at `/api/metrics` without re-instrumenting anything.
* `observability.health` (PH2.5) — the health-check registry and the process
  lifecycle state machine (starting → ready → stopping) behind the three
  distinct probes. Dependency probes run in parallel, under timeout, with a
  short result cache and production-safe failure details.
* `observability.runtime` (PH2.5) — runtime diagnostics: version, build
  provenance (git ref, build date), environment, start time, uptime, process
  facts. Reports only what a deployment already reveals about itself; it reads
  no secret values, ever.
* `observability.middleware` (PH2.5) — `ObservabilityMiddleware`, the single
  pure-ASGI seam that ties the above together: assign/propagate the request ID,
  time the request, count it, stamp `X-Request-ID`, emit one access log line.
  One middleware rather than three, because each additional layer is a real
  per-request cost on every route.
* `observability.routes` (PH2.5) — the operational HTTP surface:
  `/api/health/live`, `/api/health/ready`, `/api/health/startup`, `/api/health`,
  `/api/metrics`, `/api/diagnostics`, plus the production access gate on the
  latter two.

Design rules for anything added here:

1. **Observability must never be able to take the application down.** Every
   probe, counter and log call is wrapped so that its failure degrades the
   telemetry, never the request. A metrics bug that 500s a trading endpoint is
   strictly worse than no metrics.
2. **Cost is paid at scrape time, not request time.** The request path only
   increments integers; aggregation, rendering and process introspection happen
   when someone asks.
3. **A secret must never reach a log line or a diagnostic payload.** Redaction
   reuses `security.audit`'s markers so there is one list, not two.

See `docs/operations/MONITORING.md` for the operator-facing documentation, and
`docs/operations/LOGGING.md` for the log infrastructure specifically (streams,
rotation, retention, redaction, Docker logging drivers).
"""
