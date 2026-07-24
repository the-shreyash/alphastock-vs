"""Log file rotation, compression and retention (PH2.6).

WHY THIS MODULE EXISTS
----------------------
PH2.5 gave the application *structured* logs. It deliberately stopped at stdout,
because that is the twelve-factor contract and because a containerised process
should not be in the business of managing files. That remains the default and
the recommended path.

But "the platform ships it" assumes a platform. Real deployments spend time in
states where there is no collector yet: the first VM, the on-prem install, the
compliance requirement that says "retain authentication events for 90 days on
durable storage", the incident where the collector itself is the thing that
broke. In every one of those, `docker logs` is what you have — and `docker logs`
with the default json-file driver is an **unbounded file on the host**. A
service that logs steadily fills the disk, and a full disk does not take down
the noisy container; it takes down every container on the box, plus the SSH
daemon you were going to use to fix it.

So this module exists to make writing logs to disk *safe*: bounded in size,
bounded in count, bounded in age, and cheap to store. It is the answer to the
only question that matters about a log file, which is "what stops it?"

WHY TIMESTAMPED SEGMENTS AND NOT `.1 .2 .3`
-------------------------------------------
The stdlib's `RotatingFileHandler` shifts every backup down by one on each
rotation: `.9`→`.10`, `.8`→`.9`, … That is N renames per rotation, it makes a
file's name change meaning over time (today's `app.log.3` is not tomorrow's),
and it interacts badly with compression because a segment can be either
`app.log.3` or `app.log.3.gz` depending on whether the compressor has caught up.

Rotating to `application.log.20260722T134501.gz` instead is one rename, the name
never changes again once written, the chronology is readable without stat-ing
anything, and "give me the logs from around 13:45" is a glob. This is what
`logrotate`'s `dateext` does, and it is what operators actually reach for.

WHY COMPRESSION IS SYNCHRONOUS HERE
-----------------------------------
Gzipping 50 MB takes on the order of a second. Doing that on the thread that
called `logger.info()` would mean one unlucky request in every few hundred
thousand pays a full second of latency — a p99.99 cliff with no visible cause,
which is a genuinely miserable thing to debug.

It is safe to do it inline *because of where this handler is installed*:
`observability.log_streams` always places file handlers behind a
`QueueListener`, so `doRollover` runs on a background thread and the request
path only ever does an in-memory queue put. The queue is what makes synchronous
compression correct, and it is why these two modules are designed together.

Compression writes to `<name>.gz.tmp` and then `os.replace`s it into place.
`os.replace` is atomic on POSIX and on Windows, so a process killed mid-compress
leaves either the original segment or a complete archive — never a truncated
`.gz` that no tool can read.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not do time-based rotation ("rotate at midnight"). Size is the bound
that protects the disk; wall-clock rotation protects nothing on its own and
produces a 4 GB file on a busy day and a 2 KB file on a quiet one. Age is
handled where it belongs — as a *retention* rule applied to already-rotated
segments.
"""
from __future__ import annotations

import glob
import gzip
import logging
import logging.handlers
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Configuration                                                                 #
# --------------------------------------------------------------------------- #
LOG_MAX_BYTES_ENV = "LOG_FILE_MAX_BYTES"
LOG_BACKUP_COUNT_ENV = "LOG_FILE_BACKUP_COUNT"
LOG_RETENTION_DAYS_ENV = "LOG_RETENTION_DAYS"
LOG_COMPRESS_ENV = "LOG_FILE_COMPRESS"

# 50 MB × 10 segments = 500 MB worst case per stream before compression, and
# roughly 50 MB + 10×~5 MB ≈ 100 MB once the rotated segments are gzipped (JSON
# logs compress at roughly 10:1 — they are enormously repetitive).
#
# The numbers are chosen against a concrete failure: five streams at these
# limits is a ~500 MB steady-state footprint on a 20 GB root volume. Ten times
# larger would still "work" right up until the day it did not.
DEFAULT_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 10
DEFAULT_RETENTION_DAYS = 14
DEFAULT_COMPRESS = True

# Floors and ceilings, applied to operator input. A `LOG_FILE_MAX_BYTES=1024`
# typo would rotate every few hundred lines and turn the log directory into
# thousands of tiny files; a zero would rotate on every record. Clamping is
# preferred to rejecting for the same reason `LOG_LEVEL` falls back instead of
# raising: a logging misconfiguration must never be able to stop a deployment.
MIN_MAX_BYTES = 64 * 1024
MAX_MAX_BYTES = 2 * 1024 * 1024 * 1024
MAX_BACKUP_COUNT = 1000
MAX_RETENTION_DAYS = 3650

# UTC, and `T`-separated so the name sorts lexicographically in the same order it
# sorts chronologically — `ls` is then already sorted, and so is a glob.
SEGMENT_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%S"

# `application.log.20260722T134501.gz`, or `…T134501-2.gz` when two rotations
# land inside the same second. The optional `-N` disambiguator is not
# theoretical: a burst that fills a small `max_bytes` twice in one second is
# exactly what happens when a dependency starts flapping and every request logs
# a stack trace.
_SEGMENT_RE = re.compile(r"^\.(?P<stamp>\d{8}T\d{6})(?:-(?P<seq>\d+))?(?P<gz>\.gz)?$")

_COMPRESS_LEVEL = 6  # 9 costs ~3× the CPU for ~2% on JSON. Not a good trade.
_COPY_CHUNK = 1024 * 1024


def _warn(message: str) -> None:
    """Report a logging-subsystem problem without using the logging subsystem.

    Routing this through `logging` would be circular at exactly the moment it
    matters — the handler that failed may be the one that would carry the
    warning. stderr is always there.
    """
    print(f"[observability.log_rotation] {message}", file=sys.stderr)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        _warn(f"{name}={raw!r} is not an integer; using {default}")
        return default
    if value < minimum:
        _warn(f"{name}={value} is below the {minimum} floor; using {minimum}")
        return minimum
    if value > maximum:
        _warn(f"{name}={value} is above the {maximum} ceiling; using {maximum}")
        return maximum
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    _warn(f"{name}={raw!r} is not a boolean; using {default}")
    return default


@dataclass(frozen=True)
class RotationPolicy:
    """The complete answer to "what stops this file from growing?"

    Three independent bounds, because each one fails on its own:

    * `max_bytes` alone bounds a single file but not the directory.
    * `backup_count` alone bounds the directory but lets a low-traffic service
      keep segments from 2019 forever.
    * `retention_days` alone bounds age but not size — one bad day still fills
      the disk inside the retention window.

    Together they bound the footprint under *both* a traffic spike and a long
    quiet period, which are the two shapes real services actually have.
    """

    max_bytes: int = DEFAULT_MAX_BYTES
    backup_count: int = DEFAULT_BACKUP_COUNT
    retention_days: int = DEFAULT_RETENTION_DAYS
    compress: bool = DEFAULT_COMPRESS

    @property
    def worst_case_bytes(self) -> int:
        """Uncompressed upper bound for one stream: the live file plus backups.

        Deliberately ignores compression. Capacity planning has to hold for the
        moment *before* the compressor runs, and for a pathological input that
        does not compress. An estimate that is only true on average is not a
        bound.
        """
        return self.max_bytes * (self.backup_count + 1)

    def describe(self) -> dict:
        return {
            "max_bytes": self.max_bytes,
            "backup_count": self.backup_count,
            "retention_days": self.retention_days,
            "compress": self.compress,
            "worst_case_bytes_per_stream": self.worst_case_bytes,
        }


def resolve_policy() -> RotationPolicy:
    """Read the rotation policy from the environment. Never raises."""
    return RotationPolicy(
        max_bytes=_env_int(LOG_MAX_BYTES_ENV, DEFAULT_MAX_BYTES, MIN_MAX_BYTES, MAX_MAX_BYTES),
        backup_count=_env_int(LOG_BACKUP_COUNT_ENV, DEFAULT_BACKUP_COUNT, 0, MAX_BACKUP_COUNT),
        retention_days=_env_int(
            LOG_RETENTION_DAYS_ENV, DEFAULT_RETENTION_DAYS, 0, MAX_RETENTION_DAYS
        ),
        compress=_env_bool(LOG_COMPRESS_ENV, DEFAULT_COMPRESS),
    )


# --------------------------------------------------------------------------- #
# Segment naming                                                                #
# --------------------------------------------------------------------------- #
def parse_segment(base_filename: str, path: str) -> Optional[Tuple[float, int]]:
    """`(epoch_seconds, sequence)` for a rotated segment, or None if not one.

    Returning None for an unrecognised name is a safety property, not laziness:
    `prune` only ever deletes files this function claims. An operator's
    `application.log.keepme` or an editor's swap file is therefore invisible to
    the pruner, and this module can never delete something it did not create.

    The sequence number is what keeps ordering correct inside a single second.
    Filename order is *not* age order there: the first segment of the second is
    bare (`…T174956`) and later ones carry `-2`, `-3`, and `-` sorts before `.`
    in ASCII — so sorting by name would rank the oldest segment last and make
    prune-by-count delete the newest files first. A bare segment is sequence 1,
    which restores the true order.
    """
    if not path.startswith(base_filename):
        return None
    match = _SEGMENT_RE.match(path[len(base_filename):])
    if match is None:
        return None
    try:
        stamp = datetime.strptime(match.group("stamp"), SEGMENT_TIMESTAMP_FORMAT)
    except ValueError:
        return None
    raw_seq = match.group("seq")
    return stamp.replace(tzinfo=timezone.utc).timestamp(), int(raw_seq) if raw_seq else 1


def parse_segment_time(base_filename: str, path: str) -> Optional[float]:
    """Epoch seconds encoded in a rotated segment's name, or None if not one."""
    parsed = parse_segment(base_filename, path)
    return None if parsed is None else parsed[0]


def list_segments(base_filename: str) -> List[Tuple[str, float]]:
    """Rotated segments for one stream, oldest first. Excludes the live file."""
    found: List[Tuple[str, float, int]] = []
    for path in glob.glob(glob.escape(base_filename) + ".*"):
        parsed = parse_segment(base_filename, path)
        if parsed is not None:
            found.append((path, parsed[0], parsed[1]))
    # (time, sequence, path): deterministic on every host, so the same policy
    # applied to the same directory always retains the same set.
    found.sort(key=lambda item: (item[1], item[2], item[0]))
    return [(path, when) for path, when, _ in found]


def directory_usage_bytes(directory: str) -> int:
    """Total bytes of regular files directly inside `directory`.

    Used by the boot summary and by the tests that assert the policy actually
    bounds storage. Non-recursive on purpose: the log directory is flat, and a
    recursive walk over a mount point someone nested underneath it would be a
    surprising amount of I/O to do at startup.
    """
    total = 0
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                try:
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                except OSError:  # pragma: no cover - file vanished mid-scan
                    continue
    except OSError:
        return 0
    return total


# --------------------------------------------------------------------------- #
# Handler                                                                       #
# --------------------------------------------------------------------------- #
class RotatingLogFileHandler(logging.handlers.RotatingFileHandler):
    """Size-triggered rotation with timestamped, optionally-gzipped segments.

    Inherits `shouldRollover` from the stdlib — the size check is correct and
    well-tested — and replaces only `doRollover`, which is the part whose naming
    and retention behaviour this module is opinionated about.

    **Every failure path here is swallowed and reported to stderr.** A handler
    that raises during rotation would, depending on `logging.raiseExceptions`,
    either print a traceback per record or propagate into whatever unrelated
    code happened to call `logger.info()`. Losing rotation degrades to "the file
    keeps growing", which is bad; turning a full disk into a 500 on a trade
    execution endpoint is worse.
    """

    def __init__(self, filename: str, policy: RotationPolicy, encoding: str = "utf-8") -> None:
        # delay=True: the file is created on the first record, not at
        # construction. Streams that never emit (a quiet `security.log` on a
        # healthy day) therefore leave no empty file, and — more usefully — a
        # permission problem surfaces as a stderr warning from the listener
        # thread rather than as an exception during application startup.
        super().__init__(
            filename,
            mode="a",
            maxBytes=policy.max_bytes,
            backupCount=policy.backup_count,
            encoding=encoding,
            delay=True,
        )
        self.policy = policy

    # -- rotation ----------------------------------------------------------- #
    def doRollover(self) -> None:  # noqa: N802 - stdlib naming
        if self.stream:
            try:
                self.stream.close()
            finally:
                self.stream = None
        try:
            self._rotate_current()
        except Exception as exc:  # pragma: no cover - defensive
            _warn(f"rotation failed for {self.baseFilename}: {exc}")
        try:
            self.prune()
        except Exception as exc:  # pragma: no cover - defensive
            _warn(f"pruning failed for {self.baseFilename}: {exc}")
        if not self.delay:
            self.stream = self._open()

    def _rotate_current(self) -> Optional[str]:
        if not os.path.exists(self.baseFilename):
            return None
        if self.backupCount <= 0:
            # `backup_count=0` means "bound the file, keep no history". Deleting
            # is the only way to honour that; renaming would keep exactly the
            # history the operator asked not to keep.
            os.remove(self.baseFilename)
            return None

        target = self._next_segment_path()
        os.replace(self.baseFilename, target)
        if self.policy.compress:
            compressed = self._compress(target)
            if compressed:
                return compressed
        return target

    def _next_segment_path(self) -> str:
        stamp = datetime.now(timezone.utc).strftime(SEGMENT_TIMESTAMP_FORMAT)
        candidate = f"{self.baseFilename}.{stamp}"
        if not self._segment_taken(candidate):
            return candidate
        # Bounded: 1000 rotations inside one second means max_bytes is set
        # absurdly low, and at that point overwriting the oldest of the second
        # is a better outcome than spinning.
        for seq in range(2, 1000):
            candidate = f"{self.baseFilename}.{stamp}-{seq}"
            if not self._segment_taken(candidate):
                return candidate
        return f"{self.baseFilename}.{stamp}-overflow"

    @staticmethod
    def _segment_taken(path: str) -> bool:
        # Both spellings, because the segment may or may not have been
        # compressed yet.
        return os.path.exists(path) or os.path.exists(path + ".gz")

    def _compress(self, path: str) -> Optional[str]:
        """Gzip `path` in place, atomically. Returns the archive path, or None.

        Runs on the QueueListener thread (see the module docstring), so the
        second or so this costs is paid by the log pipeline, never by a request.
        """
        archive = path + ".gz"
        tmp = archive + ".tmp"
        try:
            with open(path, "rb") as src:
                with gzip.open(tmp, "wb", compresslevel=_COMPRESS_LEVEL) as dst:
                    shutil.copyfileobj(src, dst, _COPY_CHUNK)
            # Atomic on POSIX and Windows: readers see the old name or the new
            # one, never a half-written archive.
            os.replace(tmp, archive)
            os.remove(path)
            return archive
        except Exception as exc:
            _warn(f"compression failed for {path}: {exc}")
            # Leave the UNCOMPRESSED segment in place. It is still a complete,
            # readable log; the only cost is disk. Deleting it to tidy up would
            # destroy log data to save space that the pruner reclaims anyway.
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:  # pragma: no cover - defensive
                pass
            return None

    # -- retention ---------------------------------------------------------- #
    def prune(self) -> List[str]:
        """Apply the retention policy. Returns the paths removed.

        Age first, then count. The order matters: applying count first could
        retain a segment that age has already expired, which quietly breaks a
        "we do not keep request logs longer than N days" commitment — the kind
        of rule that exists for legal reasons rather than disk reasons.
        """
        removed: List[str] = []
        segments = list_segments(self.baseFilename)

        if self.policy.retention_days > 0:
            cutoff = time.time() - (self.policy.retention_days * 86400)
            survivors = []
            for path, when in segments:
                if when < cutoff:
                    if self._remove(path):
                        removed.append(path)
                else:
                    survivors.append((path, when))
            segments = survivors

        excess = len(segments) - self.backupCount
        if excess > 0:
            for path, _ in segments[:excess]:  # oldest first
                if self._remove(path):
                    removed.append(path)
        return removed

    @staticmethod
    def _remove(path: str) -> bool:
        try:
            os.remove(path)
            return True
        except OSError as exc:  # pragma: no cover - defensive
            _warn(f"could not remove {path}: {exc}")
            return False
