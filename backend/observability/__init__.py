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
* `observability.errors` (PH3.7) — the closed vocabulary of thirteen failure
  classes, and the one function that maps any exception onto it. The shared
  noun set for metric labels, structured-log fields and alert rules: bounded, so
  a class is safe as a label; stable, so an alert written today still means the
  same thing after someone rewords an exception. Like `context`, it imports
  nothing from this codebase — classification is by MRO name-matching rather
  than `isinstance`, precisely so the module every subsystem depends on does not
  drag in `pymongo`, `redis`, `httpx` and `anthropic`.
* `observability.instruments` (PH3.7) — the API call sites actually use. Holds
  the closed `subsystem` / `provider` vocabularies and the helpers that update a
  whole metric family together, so a call site cannot increment two of three
  counters and leave a dashboard subtly inconsistent. Everything funnels to
  `subsystem_errors_total`, which makes "which subsystem is failing?" answerable
  from one series.
* `observability.mongo_monitor` (PH3.7) — MongoDB command and connection-pool
  metrics, registered as pymongo listeners on the client rather than wrapped
  around several hundred call sites. Reads the command *name* and the duration
  and nothing else: the command document carries emails, password hashes and
  broker tokens, and a failure document carries the credentialed URI.
* `observability.routes` (PH2.5) — the operational HTTP surface:
  `/api/health/live`, `/api/health/ready`, `/api/health/startup`, `/api/health`,
  `/api/metrics`, `/api/diagnostics`, plus the production access gate on the
  latter two, and (PH3.7) `POST /api/observability/client-errors`, the only path
  by which a browser-side failure becomes visible to an operator.

Design rules for anything added here:

1. **Observability must never be able to take the application down.** Every
   probe, counter and log call is wrapped so that its failure degrades the
   telemetry, never the request. A metrics bug that 500s a trading endpoint is
   strictly worse than no metrics.
2. **Cost is paid at scrape time, not request time.** The request path only
   increments integers; aggregation, rendering and process introspection happen
   when someone asks.
3. **A secret must never reach a log line, a metric label or a diagnostic
   payload.** Redaction reuses `security.audit`'s markers so there is one list,
   not two. A value derived from free text (an exception message, a provider
   error string, a server failure document, a browser report) may be used to
   *choose* a class from a closed vocabulary; it may never become the label.
4. **A label value that can come from outside the process is a bug.** Route
   templates, not paths; frozen vocabularies, not caller strings; and
   `METRICS_MAX_SERIES` as the backstop for whatever the first two missed. An
   unbounded label is how a metrics change takes down the system it observes.

See `docs/architecture/OBSERVABILITY.md` for the architecture, the rules that
bind anything added here, and the alert catalogue — **read it before adding an
instrument**. `docs/operations/MONITORING.md` is the operator-facing manual, and
`docs/operations/LOGGING.md` covers the log infrastructure specifically
(streams, rotation, retention, redaction, Docker logging drivers).
"""
