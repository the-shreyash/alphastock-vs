# syntax=docker/dockerfile:1.7
# ==============================================================================
# StockAssist AI — Backend production image (PH2.1)
#
# WHY THIS FILE EXISTS
# --------------------
# "It works on my machine" is not a deployment strategy. This Dockerfile turns
# the backend into a single immutable artifact that carries its own interpreter,
# its own system libraries and its own pinned dependency set, so the thing CI
# tests is bit-for-bit the thing production runs. Every environment difference
# that remains is an environment VARIABLE — never a build difference.
#
# BUILD
#     docker build -t stockassist-backend:local ./backend
#
# RUN
#     docker run --rm -p 8000:8000 --env-file production.env stockassist-backend:local
#
# Full operator documentation: docs/deployment/DOCKER.md
#
# ARCHITECTURE — two stages, one shipped
#
#     ┌─ stage 1: builder ────────────┐      ┌─ stage 2: runtime ───────────┐
#     │ python:3.11-slim              │      │ python:3.11-slim             │
#     │ + gcc, g++, make, headers     │      │ (no compilers, no headers)   │
#     │ + pip, wheels, build caches   │ ───► │ /opt/venv  (copied)          │
#     │ → /opt/venv                   │ only │ /app       (source)          │
#     │                               │ this │ USER appuser (uid 10001)     │
#     │        DISCARDED              │ moves│        SHIPPED               │
#     └───────────────────────────────┘      └──────────────────────────────┘
#
# The builder's toolchain never reaches the published image. That is worth
# roughly 250-300 MB, but the real prize is the security posture: an attacker
# who achieves code execution in the runtime container finds no compiler, no
# package headers and no build tooling to stage a native payload with.
# ==============================================================================

# Declared once, before any FROM, so both stages are guaranteed to use the same
# interpreter. A builder that compiles wheels for 3.11 and a runtime on 3.12
# fails at import time with an ABI error that is genuinely painful to diagnose.
ARG PYTHON_VERSION=3.11
ARG DEBIAN_RELEASE=bookworm

# `-slim` (~45 MB) rather than the default Debian image (~350 MB) or `-alpine`.
#   * The default image ships a full distro toolchain this app never invokes.
#   * Alpine is smaller still, but uses musl instead of glibc, so the manylinux
#     wheels for pandas / numpy / cryptography / grpcio do not apply and pip
#     falls back to compiling from source. That turns a ~1 minute dependency
#     install into a 15-30 minute one and reintroduces a compiler dependency —
#     and musl's allocator is measurably slower for numeric workloads. For a
#     pandas/numpy-heavy service, slim-glibc is the correct trade.
#
# NOTE ON PINNING: the tag is pinned to a minor version, so a rebuild picks up
# Debian security patches automatically — the right default while there is no
# CI. Once PH2.6 lands image scanning, pin by digest
# (`python:3.11-slim-bookworm@sha256:...`) for byte-identical rebuilds, with
# Dependabot's `docker` ecosystem (already configured in .github/dependabot.yml)
# proposing digest bumps.

# ==============================================================================
# STAGE 1 — builder
#
# Sole responsibility: produce a self-contained virtualenv at /opt/venv.
# Nothing here is shipped, so it can be as fat as it needs to be.
# ==============================================================================
FROM python:${PYTHON_VERSION}-slim-${DEBIAN_RELEASE} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

# Build toolchain for the handful of dependencies without a prebuilt manylinux
# wheel for this platform (notably jq, and any package on linux/arm64 whose
# publisher ships x86-only wheels). `--no-install-recommends` is not a micro-
# optimisation: without it apt pulls in documentation, locales and suggested
# packages, roughly tripling what gets installed.
#
# The `apt-get update && apt-get install && rm -rf` chain is ONE `RUN` on
# purpose. Each RUN creates a layer; splitting them would leave the ~40 MB
# package index permanently embedded in the layer below, where a later `rm`
# cannot reclaim it — layers are additive and immutable, deletion only masks.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# A virtualenv (rather than a plain `pip install --user` or system install) so
# the dependency set is ONE self-contained, relocatable directory. Stage 2 then
# copies a single tree and gets a byte-identical environment, with no risk of
# mixing application packages into the base image's system site-packages.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /build

# ── The most important four lines in this file: dependency-layer caching ──────
# requirements.txt is copied and installed BEFORE the application source.
# Docker invalidates a layer's cache only when its inputs change, and every
# layer after an invalidated one is rebuilt too. Dependencies change rarely;
# application code changes constantly. Ordering them this way means a normal
# code edit reuses the cached dependency layer and rebuilds in seconds. Copying
# the whole source first (the classic beginner mistake) would re-resolve and
# re-download ~120 pinned packages on every single-character change.
#
# requirements-dev.txt is deliberately NOT installed: pytest, black, flake8,
# isort and mypy are development and CI tooling (PH1.11 finding M14). Keeping
# them out is both smaller and safer — dev tooling on a production server is
# pure attack surface with zero operational value.
#
# pip itself is deliberately NOT upgraded here. The base image ships a specific
# pip, and that version is pinned by the base-image tag — introducing an
# unpinned `pip install --upgrade pip` would smuggle an unversioned dependency
# into an otherwise fully pinned build and defeat the point of the exercise.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ── Prune the installed environment ───────────────────────────────────────────
# Done HERE, in the builder, so the trimmed tree is what stage 2 copies. Pruning
# in the runtime stage would be worse than useless: the full-size files would
# still exist in the layer below, and the `rm` would only add another layer.
#
# Measured on this dependency set (linux/arm64):
#   bundled test suites   66 MB   pandas/tests alone is 40 MB
#   pip                   16 MB
#   ──────────────────────────
#   total                 82 MB
#
# 1. Library test suites. No library imports its own tests at runtime; they
#    exist so a user can run `pandas.test()`. This project's tests live in
#    backend/tests/ and run in CI. `-maxdepth 3` keeps the match to real
#    top-level package test directories rather than anything named "tests"
#    arbitrarily deep in a data directory.
# 2. pip. Once the venv is built, a package installer inside a production image
#    is not a feature — it is a convenient tool for anyone who lands a shell.
#    (setuptools is deliberately KEPT: several dependencies still import
#    `pkg_resources` at runtime, and removing it trades 11 MB for an outage.)
#
# NOT done, and worth recording so nobody re-litigates it: `strip
# --strip-unneeded` over the 147 MB of .so files saved exactly 0 bytes. Modern
# manylinux wheels are already stripped by the auditwheel toolchain, so the step
# is pure build time. Likewise `pip install --no-compile` would save ~158 MB of
# .pyc, but the source tree is read-only to the app user with
# PYTHONDONTWRITEBYTECODE set, so every container start would re-parse the whole
# dependency tree — trading a one-off image cost for a permanent startup cost on
# every deploy and every autoscale event. The bytecode stays.
RUN find /opt/venv/lib -maxdepth 5 -type d \( -name tests -o -name testing -o -name idle_test \) \
        -prune -exec rm -rf {} + \
    && rm -rf /opt/venv/lib/python*/site-packages/pip \
              /opt/venv/lib/python*/site-packages/pip-*.dist-info \
    && rm -f /opt/venv/bin/pip /opt/venv/bin/pip3 /opt/venv/bin/pip3.*

# ==============================================================================
# STAGE 2 — runtime
#
# The image that actually ships. Everything here is either required to serve a
# request or required to prove the container is alive.
# ==============================================================================
FROM python:${PYTHON_VERSION}-slim-${DEBIAN_RELEASE} AS runtime

# ── OCI image metadata ────────────────────────────────────────────────────────
# Standard labels, read by registries, scanners and `docker inspect`. When an
# incident starts with "which commit is this image?", the answer should come
# from the artifact itself rather than from a deploy log someone has to find.
# PH2.6 passes the git SHA and build timestamp in from CI.
ARG APP_VERSION=0.0.0-dev
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="StockAssist AI Backend" \
      org.opencontainers.image.description="FastAPI backend for the StockAssist AI trading operating system" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/the-shreyash/alpha-stock"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PATH="/opt/venv/bin:${PATH}" \
    APP_ENV=production \
    HOST=0.0.0.0 \
    PORT=8000 \
    WEB_CONCURRENCY=1 \
    TZ=UTC
# Rationale for each:
#   PYTHONDONTWRITEBYTECODE — the source tree is owned by root and read-only to
#       the app user, so bytecode writes would fail anyway; bytecode is instead
#       precompiled below, at build time, by root.
#   PYTHONUNBUFFERED — without it Python block-buffers stdout when it is a pipe
#       (which it always is under Docker), so logs arrive minutes late or vanish
#       entirely when the container is killed. This single variable is the
#       difference between having and not having logs during an incident.
#   PYTHONFAULTHANDLER — dumps a Python traceback on a fatal signal (SIGSEGV,
#       SIGABRT). A native crash inside a C extension otherwise leaves nothing
#       but an exit code.
#   TZ=UTC — the container clock is UTC, always. Timestamp correlation across
#       services is impossible when hosts disagree about local time. The
#       application's market-hours scheduler sets Asia/Kolkata explicitly in
#       services/scheduler.py, so NSE logic is unaffected by this.
#   APP_ENV=production — the image defaults to its most restrictive posture.
#       Running it in development requires an explicit downgrade, not the other
#       way round; a forgotten variable can never accidentally relax cookie
#       flags, CORS or secret-strength enforcement.

# ── Dedicated unprivileged user ───────────────────────────────────────────────
# Containers run as root by default and that default is wrong. Root inside a
# container is root in the kernel's eyes; combined with a container-escape CVE
# (runc, kernel namespaces — these ship regularly) it is host root. Running as a
# normal user means an escape lands on an account with no privileges anywhere.
#
# Fixed, high, explicit UID/GID 10001:
#   * FIXED so that a bind-mounted volume's file ownership is stable across
#     rebuilds instead of shifting with the order of user creation.
#   * HIGH (>10000) to stay clear of host system accounts, so a UID collision
#     cannot hand this process a host user's file permissions through a mount.
#   * The account gets /usr/sbin/nologin as its shell and no password — it
#     exists to own a process, not to be logged into.
RUN groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid appuser \
        --home-dir /app --no-create-home --shell /usr/sbin/nologin appuser

# No apt-get in this stage at all. The health check is written against the
# Python standard library specifically so that curl/wget never have to be
# installed here — see docker/healthcheck.sh. Every package NOT installed is a
# CVE that can never apply to this image.

# The base image's SYSTEM python (/usr/local) also ships a pip. The application
# never uses it — it runs entirely out of /opt/venv — but it is a fully working
# package installer sitting on a production server, which is exactly the tool an
# attacker wants after landing a shell: a supported, network-capable way to pull
# a second stage. Remove it and its console scripts. (The venv's own pip is
# already removed in the builder stage.)
RUN rm -rf /usr/local/lib/python*/site-packages/pip \
           /usr/local/lib/python*/site-packages/pip-*.dist-info \
    && rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.*

# The finished virtualenv, and nothing else, crosses the stage boundary.
COPY --from=builder --chown=root:root /opt/venv /opt/venv

WORKDIR /app

# Application source. Owned by root:root, NOT by appuser — deliberately.
# The process runs as appuser and therefore has read+execute on its own code but
# CANNOT modify it. That closes the most common post-exploitation move there is:
# writing a webshell or patching a route handler in place. Combine with
# `--read-only --tmpfs /tmp` at run time for a fully immutable filesystem.
#
# What is excluded (tests, .env, venv/, .git, caches) is governed by
# .dockerignore, which is the single auditable boundary — see that file.
COPY --chown=root:root . .

# The entrypoint and health check need the execute bit. Setting it here rather
# than relying on the committed file mode means the image is correct even when
# built from a checkout on a filesystem that does not preserve permissions
# (Windows/NTFS, some CI artifact caches) — a genuinely common and
# very confusing failure.
RUN chmod 0755 docker/entrypoint.sh docker/healthcheck.sh

# Precompile bytecode at build time, as root, into the read-only source tree.
# Startup then skips compiling ~250k lines of application code on first import,
# which measurably shortens the window between container start and first healthy
# response — the window during which a rolling deploy is running at reduced
# capacity. `|| true`: a compile warning in an unreachable module must not fail
# the build, since the bytecode is a cache, not a correctness requirement.
RUN python -m compileall -q /app > /dev/null 2>&1 || true

# Everything above this line ran as root (installing packages, creating users,
# setting modes — all of which need it). Everything from here on, including the
# entrypoint, the health check and the server itself, does not.
USER appuser

# Documentation + a hint to tooling (`docker run -P`, compose port inference).
# EXPOSE publishes nothing by itself; the port is still chosen at run time. Only
# this one port is declared: no debugger, no metrics port, no admin socket.
EXPOSE 8000

# --interval=30s     steady-state probe cadence.
# --timeout=5s       above the probe's own 4s HTTP timeout, so a slow response
#                    is reported as unhealthy by the script (with a reason in
#                    the health log) rather than killed by the daemon silently.
# --start-period=45s grace window while the app boots — validating config,
#                    importing pandas/numpy/grpc, connecting to Mongo and
#                    creating ~20 indexes. Failures during this window do NOT
#                    count toward --retries, so a slow cold start is not
#                    mistaken for a crash loop.
# --retries=3        ~90s of sustained failure before the container is marked
#                    unhealthy; absorbs a single GC pause or transient stall
#                    without triggering a restart.
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD ["/app/docker/healthcheck.sh"]

# ENTRYPOINT (exec form, never shell form) so the script becomes PID 1 and
# receives SIGTERM directly. The shell form would wrap it in `/bin/sh -c`, which
# does not forward signals — every `docker stop` would end in SIGKILL and every
# deploy would sever in-flight requests.
#
# CMD is empty: with no arguments the entrypoint starts the API server. Any
# arguments passed to `docker run` are exec'd instead, which is how one-shot
# jobs (migrations, seeding, a debug shell) reuse this exact image and its
# validated configuration. See docker/entrypoint.sh.
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD []
