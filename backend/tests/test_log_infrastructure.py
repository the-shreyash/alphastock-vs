"""PH2.6 — log infrastructure tests: rotation, retention, stream routing, redaction.

Hermetic and deterministic. No sleeps, no polling, no real `/var/log`: every
test writes into a `tmp_path` directory, and every assertion that depends on the
writer thread having caught up goes through `log_streams.flush()`, which is an
exact barrier (`Queue.join()`) rather than a timing guess. A flaky logging test
is worse than no logging test — it trains people to re-run CI.

What is deliberately covered here, because each one is a silent failure in
production:

* rotation actually bounds the file (the whole point of the sprint)
* retention deletes by AGE BEFORE COUNT (a compliance property, not a disk one)
* the pruner never deletes a file it did not create
* `security.audit.events` lands in audit.log and NOT in security.log — the
  ordering trap called out in log_streams' module docstring
* ERROR records are copied to error.log while REMAINING in their own stream
* tracebacks survive the queue (the stdlib `prepare()` would strip them)
* credentials are redacted in the FILE sink, which is where they would persist
* the request ID survives into the file sink
"""
import gzip
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from observability import context, log_rotation, log_streams
from observability import logging as obs_logging


# --------------------------------------------------------------------------- #
# Fixtures                                                                      #
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def isolated_logging_state(monkeypatch):
    """Restore the root logger and every module global around each test.

    The logging subsystem is process-global by design, and these tests
    deliberately reconfigure it. Without a full restore, a test that enables
    file sinks would leave a queue handler attached to the root logger for the
    REST OF THE SESSION — every later test in the suite would then write into a
    `tmp_path` that pytest has already deleted. That failure presents as an
    unrelated test breaking, which is a miserable thing to track down.
    """
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level

    # Every PH2.6 env var, cleared. Otherwise a developer with LOG_TO_FILES=1
    # exported in their shell gets different results than CI — the exact class
    # of "works on my machine" this suite exists to rule out.
    for var in (
        log_streams.LOG_TO_FILES_ENV,
        log_streams.LOG_DIR_ENV,
        log_streams.LOG_FILE_STREAMS_ENV,
        log_streams.LOG_QUEUE_SIZE_ENV,
        log_rotation.LOG_MAX_BYTES_ENV,
        log_rotation.LOG_BACKUP_COUNT_ENV,
        log_rotation.LOG_RETENTION_DAYS_ENV,
        log_rotation.LOG_COMPRESS_ENV,
    ):
        monkeypatch.delenv(var, raising=False)

    log_streams.reset_drop_counter()
    yield
    log_streams.stop_file_sinks()
    log_streams.reset_drop_counter()
    obs_logging.reset_for_tests()

    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in saved_handlers:
        root.addHandler(handler)
    root.setLevel(saved_level)


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    """An enabled, isolated file-sink directory."""
    directory = tmp_path / "logs"
    monkeypatch.setenv(log_streams.LOG_TO_FILES_ENV, "1")
    monkeypatch.setenv(log_streams.LOG_DIR_ENV, str(directory))
    return directory


def _pipeline(root_level=logging.INFO):
    """Attach the real file pipeline to a private logger tree."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(root_level)
    summary = log_streams.attach_file_sinks(root, obs_logging.StructuredFormatter())
    return root, summary


def _read_lines(path):
    """Parsed JSON records from a log file, or [] if it was never created."""
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# --------------------------------------------------------------------------- #
# Rotation policy resolution                                                    #
# --------------------------------------------------------------------------- #
class TestRotationPolicy:
    def test_defaults_are_the_documented_values(self):
        policy = log_rotation.resolve_policy()
        assert policy.max_bytes == 50 * 1024 * 1024
        assert policy.backup_count == 10
        assert policy.retention_days == 14
        assert policy.compress is True

    def test_worst_case_counts_the_live_file_too(self):
        policy = log_rotation.RotationPolicy(max_bytes=1000, backup_count=3)
        # 3 backups + the file currently being written = 4, not 3. Off-by-one
        # here would under-state the footprint in the capacity guidance.
        assert policy.worst_case_bytes == 4000

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("1048576", 1048576),
            ("0", log_rotation.MIN_MAX_BYTES),          # clamped up, not accepted
            ("999999999999", log_rotation.MAX_MAX_BYTES),  # clamped down
            ("not-a-number", log_rotation.DEFAULT_MAX_BYTES),
            ("", log_rotation.DEFAULT_MAX_BYTES),
        ],
    )
    def test_max_bytes_clamps_instead_of_failing(self, monkeypatch, raw, expected):
        """A logging misconfiguration must never stop a deployment."""
        monkeypatch.setenv(log_rotation.LOG_MAX_BYTES_ENV, raw)
        assert log_rotation.resolve_policy().max_bytes == expected

    @pytest.mark.parametrize(
        "raw,expected", [("1", True), ("true", True), ("0", False), ("off", False), ("maybe", True)]
    )
    def test_compress_flag_parsing(self, monkeypatch, raw, expected):
        monkeypatch.setenv(log_rotation.LOG_COMPRESS_ENV, raw)
        assert log_rotation.resolve_policy().compress is expected


# --------------------------------------------------------------------------- #
# Segment naming                                                                #
# --------------------------------------------------------------------------- #
class TestSegmentNaming:
    def test_recognises_plain_and_compressed_segments(self):
        base = "/logs/application.log"
        assert log_rotation.parse_segment_time(base, base + ".20260722T134501") is not None
        assert log_rotation.parse_segment_time(base, base + ".20260722T134501.gz") is not None
        assert log_rotation.parse_segment_time(base, base + ".20260722T134501-2.gz") is not None

    def test_ignores_anything_it_did_not_create(self):
        """The safety property behind prune(): it can only delete its own files."""
        base = "/logs/application.log"
        for foreign in (base, base + ".keepme", base + ".2026-07-22", base + ".txt",
                        "/logs/other.log.20260722T134501"):
            assert log_rotation.parse_segment_time(base, foreign) is None

    def test_sequence_restores_true_age_order_within_one_second(self):
        """`-2` sorts before `.gz` in ASCII, so name order is NOT age order."""
        base = "/logs/application.log"
        first = log_rotation.parse_segment(base, base + ".20260722T134501")
        second = log_rotation.parse_segment(base, base + ".20260722T134501-2")
        assert first[0] == second[0]      # same second
        assert first[1] < second[1]       # bare segment is older


# --------------------------------------------------------------------------- #
# Rotation and compression                                                      #
# --------------------------------------------------------------------------- #
class TestRotation:
    def test_file_is_bounded_and_segments_are_created(self, tmp_path):
        path = str(tmp_path / "application.log")
        policy = log_rotation.RotationPolicy(max_bytes=2048, backup_count=5, compress=False)
        handler = log_rotation.RotatingLogFileHandler(path, policy)
        handler.setFormatter(logging.Formatter("%(message)s"))

        for i in range(400):
            handler.emit(
                logging.LogRecord("t", logging.INFO, __file__, i, "x" * 100, None, None)
            )
        handler.close()

        segments = log_rotation.list_segments(path)
        assert segments, "nothing rotated — the file grew without bound"
        # The live file is the only one allowed to be at/below the limit; every
        # rotated segment was closed at roughly max_bytes. A generous ceiling
        # (one record can always overshoot) still proves boundedness.
        assert os.path.getsize(path) <= policy.max_bytes + 200
        for segment_path, _ in segments:
            assert os.path.getsize(segment_path) <= policy.max_bytes + 200

    def test_compression_produces_a_readable_archive(self, tmp_path):
        path = str(tmp_path / "application.log")
        policy = log_rotation.RotationPolicy(max_bytes=1024, backup_count=5, compress=True)
        handler = log_rotation.RotatingLogFileHandler(path, policy)
        handler.setFormatter(logging.Formatter("%(message)s"))
        for i in range(60):
            handler.emit(
                logging.LogRecord("t", logging.INFO, __file__, i, "compress-me-" + "y" * 80, None, None)
            )
        handler.close()

        segments = log_rotation.list_segments(path)
        assert segments
        archive = segments[0][0]
        assert archive.endswith(".gz")
        with gzip.open(archive, "rt", encoding="utf-8") as handle:
            assert "compress-me-" in handle.read()
        # No temp file survives, and the uncompressed original is gone.
        assert not os.path.exists(archive[: -len(".gz")])
        assert not any(p.endswith(".tmp") for p in os.listdir(tmp_path))

    def test_backup_count_zero_keeps_no_history(self, tmp_path):
        """"Bound the file, keep nothing" has to actually delete, not rename."""
        path = str(tmp_path / "application.log")
        policy = log_rotation.RotationPolicy(max_bytes=512, backup_count=0, compress=False)
        handler = log_rotation.RotatingLogFileHandler(path, policy)
        handler.setFormatter(logging.Formatter("%(message)s"))
        for i in range(50):
            handler.emit(logging.LogRecord("t", logging.INFO, __file__, i, "z" * 60, None, None))
        handler.close()
        assert log_rotation.list_segments(path) == []
        assert os.path.getsize(path) <= 512 + 200

    def test_rotation_failure_never_raises_into_the_caller(self, tmp_path, monkeypatch):
        """A full disk must degrade to an unrotated file, not a 500 on a trade."""
        path = str(tmp_path / "application.log")
        handler = log_rotation.RotatingLogFileHandler(
            path, log_rotation.RotationPolicy(max_bytes=256, backup_count=2, compress=False)
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        monkeypatch.setattr(
            log_rotation.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
        )
        for i in range(30):
            handler.emit(logging.LogRecord("t", logging.INFO, __file__, i, "q" * 60, None, None))
        handler.close()  # no exception is the assertion


# --------------------------------------------------------------------------- #
# Retention                                                                     #
# --------------------------------------------------------------------------- #
class TestRetention:
    def _segment(self, base, age_days, suffix=".gz"):
        stamp = (datetime.now(timezone.utc) - timedelta(days=age_days)).strftime(
            log_rotation.SEGMENT_TIMESTAMP_FORMAT
        )
        path = f"{base}.{stamp}{suffix}"
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("segment")
        return path

    def _handler(self, base, **policy_kwargs):
        return log_rotation.RotatingLogFileHandler(
            base, log_rotation.RotationPolicy(**policy_kwargs)
        )

    def test_prunes_by_age(self, tmp_path):
        base = str(tmp_path / "audit.log")
        old = self._segment(base, age_days=40)
        recent = self._segment(base, age_days=2)
        handler = self._handler(base, retention_days=14, backup_count=100)

        removed = handler.prune()
        handler.close()

        assert old in removed and not os.path.exists(old)
        assert recent not in removed and os.path.exists(recent)

    def test_prunes_by_count_oldest_first(self, tmp_path):
        base = str(tmp_path / "access.log")
        segments = [self._segment(base, age_days=days) for days in (5, 4, 3, 2, 1)]
        handler = self._handler(base, backup_count=2, retention_days=0)

        handler.prune()
        handler.close()

        # Keep the 2 newest (1 and 2 days old); drop the 3 oldest.
        assert not os.path.exists(segments[0]) and not os.path.exists(segments[1])
        assert os.path.exists(segments[3]) and os.path.exists(segments[4])

    def test_age_is_applied_before_count(self, tmp_path):
        """The compliance property: a 400-day-old segment goes, even when the
        backup count alone would happily have kept it."""
        base = str(tmp_path / "audit.log")
        ancient = self._segment(base, age_days=400)
        handler = self._handler(base, retention_days=30, backup_count=100)

        handler.prune()
        handler.close()

        assert not os.path.exists(ancient)

    def test_never_deletes_a_foreign_file(self, tmp_path):
        base = str(tmp_path / "application.log")
        self._segment(base, age_days=99)
        bystanders = [
            str(tmp_path / "application.log.keepme"),
            str(tmp_path / "application.log"),
            str(tmp_path / "notes.txt"),
        ]
        for path in bystanders:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("do not delete")

        handler = self._handler(base, retention_days=1, backup_count=0)
        handler.prune()
        handler.close()

        for path in bystanders:
            assert os.path.exists(path), f"pruner deleted a file it did not create: {path}"

    def test_retention_days_zero_disables_age_pruning(self, tmp_path):
        base = str(tmp_path / "application.log")
        ancient = self._segment(base, age_days=1000)
        handler = self._handler(base, retention_days=0, backup_count=50)
        handler.prune()
        handler.close()
        assert os.path.exists(ancient)


# --------------------------------------------------------------------------- #
# Stream routing                                                                #
# --------------------------------------------------------------------------- #
class TestStreamRouting:
    @pytest.mark.parametrize(
        "logger_name,expected",
        [
            ("security.audit.events", "audit"),
            ("security.csrf", "security"),
            ("security", "security"),
            ("stockassist.access", "access"),
            ("server", "application"),
            ("services.market_gateway", "application"),
            # Prefix matching is on a dot boundary, so a logger that merely
            # STARTS WITH "security" is not a security log.
            ("securityfoo", "application"),
        ],
    )
    def test_exclusive_streams_partition_by_logger_name(self, logger_name, expected):
        record = logging.LogRecord(logger_name, logging.INFO, __file__, 1, "m", None, None)
        matched = [
            stream.name
            for stream in log_streams.EXCLUSIVE_STREAMS
            if log_streams.StreamFilter(stream, log_streams.preceding_prefixes(stream)).filter(record)
        ]
        # Exactly one — a partition, not a fan-out.
        assert matched == [expected]

    def test_audit_is_not_also_written_to_the_security_stream(self):
        """The single easiest thing to get wrong: `security.audit.events` is a
        child of `security`, and double-writing would give security.log the
        audit stream's much longer retention requirement by accident."""
        record = logging.LogRecord(
            log_streams.AUDIT_LOGGER, logging.INFO, __file__, 1, "m", None, None
        )
        security_filter = log_streams.StreamFilter(
            log_streams.SECURITY_STREAM, log_streams.preceding_prefixes(log_streams.SECURITY_STREAM)
        )
        assert security_filter.filter(record) is False

    def test_error_stream_is_a_view_not_a_partition(self):
        """An ERROR from the access log belongs in BOTH access.log and
        error.log — making it exclusive would strip the access log of exactly
        its 5xx lines."""
        error_filter = log_streams.StreamFilter(
            log_streams.ERROR_STREAM, log_streams.preceding_prefixes(log_streams.ERROR_STREAM)
        )
        access_filter = log_streams.StreamFilter(
            log_streams.ACCESS_STREAM, log_streams.preceding_prefixes(log_streams.ACCESS_STREAM)
        )
        err = logging.LogRecord(
            log_streams.ACCESS_LOGGER, logging.ERROR, __file__, 1, "500", None, None
        )
        info = logging.LogRecord(
            log_streams.ACCESS_LOGGER, logging.INFO, __file__, 1, "200", None, None
        )
        assert error_filter.filter(err) and access_filter.filter(err)
        assert not error_filter.filter(info) and access_filter.filter(info)

    def test_stream_selection_from_env(self, monkeypatch):
        monkeypatch.setenv(log_streams.LOG_FILE_STREAMS_ENV, "audit,security")
        assert [s.name for s in log_streams.selected_streams()] == ["audit", "security"]

    def test_unknown_stream_selection_falls_back_to_all(self, monkeypatch):
        monkeypatch.setenv(log_streams.LOG_FILE_STREAMS_ENV, "nonsense")
        assert log_streams.selected_streams() == log_streams.ALL_STREAMS


# --------------------------------------------------------------------------- #
# Log directory                                                                 #
# --------------------------------------------------------------------------- #
class TestLogDirectory:
    def test_creates_the_directory(self, tmp_path):
        target = tmp_path / "nested" / "logs"
        path, error = log_streams.ensure_log_dir(str(target))
        assert error is None and path == str(target)
        assert os.path.isdir(str(target))

    def test_leaves_no_probe_file_behind(self, tmp_path):
        log_streams.ensure_log_dir(str(tmp_path))
        assert os.listdir(tmp_path) == []

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the permission bits")
    def test_unwritable_directory_reports_an_error_instead_of_raising(self, tmp_path):
        """The container must still boot; it just logs to stdout only."""
        target = tmp_path / "readonly"
        target.mkdir()
        os.chmod(target, 0o500)
        try:
            path, error = log_streams.ensure_log_dir(str(target))
            assert path is None and "not writable" in error
        finally:
            os.chmod(target, 0o700)


# --------------------------------------------------------------------------- #
# Queue behaviour                                                               #
# --------------------------------------------------------------------------- #
class TestBoundedQueue:
    def test_full_queue_drops_and_counts_instead_of_blocking(self):
        import queue as queue_module

        handler = log_streams.BoundedQueueHandler(queue_module.Queue(maxsize=2))
        for i in range(5):
            handler.emit(logging.LogRecord("t", logging.INFO, __file__, i, "m", None, None))
        # 2 queued, 3 dropped — and, crucially, the call returned.
        assert log_streams.dropped_records() == 3

    def test_prepare_preserves_exc_info(self):
        """The stdlib's prepare() strips exc_info for pickling. Ours must not:
        that would silently drop every stack trace from every file log while
        stdout kept them."""
        import queue as queue_module

        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            exc_info = sys.exc_info()
        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "failed", None, exc_info)
        prepared = log_streams.BoundedQueueHandler(queue_module.Queue()).prepare(record)
        assert prepared.exc_info is not None

    def test_prepare_does_not_mutate_the_original_record(self):
        """The same record object also goes to the stdout handler."""
        import queue as queue_module

        record = logging.LogRecord("t", logging.INFO, __file__, 1, "value=%s", ("x",), None)
        log_streams.BoundedQueueHandler(queue_module.Queue()).prepare(record)
        assert record.args == ("x",)


# --------------------------------------------------------------------------- #
# End-to-end pipeline                                                           #
# --------------------------------------------------------------------------- #
class TestPipeline:
    def test_records_land_in_the_right_files_as_json(self, log_dir):
        root, summary = _pipeline()
        assert summary["enabled"] is True

        logging.getLogger("server").info("application line")
        logging.getLogger("security.csrf").warning("security line")
        logging.getLogger(log_streams.AUDIT_LOGGER).info("audit line")
        logging.getLogger(log_streams.ACCESS_LOGGER).info("access line")
        log_streams.flush()

        app_lines = _read_lines(str(log_dir / "application.log"))
        assert [r["message"] for r in app_lines] == ["application line"]
        assert app_lines[0]["level"] == "INFO" and app_lines[0]["logger"] == "server"

        assert [r["message"] for r in _read_lines(str(log_dir / "security.log"))] == ["security line"]
        assert [r["message"] for r in _read_lines(str(log_dir / "audit.log"))] == ["audit line"]
        assert [r["message"] for r in _read_lines(str(log_dir / "access.log"))] == ["access line"]

    def test_errors_are_copied_to_error_log_and_kept_in_their_own_stream(self, log_dir):
        _pipeline()
        logging.getLogger("server").error("something broke")
        log_streams.flush()

        assert [r["message"] for r in _read_lines(str(log_dir / "error.log"))] == ["something broke"]
        assert [r["message"] for r in _read_lines(str(log_dir / "application.log"))] == [
            "something broke"
        ]

    def test_tracebacks_survive_the_queue(self, log_dir):
        _pipeline()
        try:
            raise RuntimeError("kaboom")
        except RuntimeError:
            logging.getLogger("server").exception("handler failed")
        log_streams.flush()

        record = _read_lines(str(log_dir / "error.log"))[0]
        assert record["exception"]["type"] == "RuntimeError"
        assert "kaboom" in record["exception"]["message"]
        assert "Traceback" in record["exception"]["stacktrace"]

    def test_request_id_is_preserved_into_the_file_sink(self, log_dir):
        _pipeline()
        token = context.bind("abc123def456", method="GET", path="/api/portfolio")
        try:
            logging.getLogger("server").info("inside a request")
        finally:
            context.reset(token)
        log_streams.flush()

        record = _read_lines(str(log_dir / "application.log"))[0]
        assert record["request_id"] == "abc123def456"
        assert record["method"] == "GET" and record["path"] == "/api/portfolio"

    def test_files_are_json_even_when_stdout_is_text(self, log_dir, monkeypatch):
        """A file is read by a collector or by jq, never by staring at it — and
        a mixed-format archive cannot be parsed by anything."""
        monkeypatch.setenv(obs_logging.LOG_FORMAT_ENV, "text")
        obs_logging.reset_for_tests()
        obs_logging.configure_logging(force=True)

        logging.getLogger("server").info("still structured")
        log_streams.flush()

        assert _read_lines(str(log_dir / "application.log"))[0]["message"] == "still structured"

    def test_stream_subset_writes_only_the_requested_files(self, log_dir, monkeypatch):
        monkeypatch.setenv(log_streams.LOG_FILE_STREAMS_ENV, "audit")
        _pipeline()
        logging.getLogger(log_streams.AUDIT_LOGGER).info("kept")
        logging.getLogger("server").info("not written to file")
        log_streams.flush()

        assert os.path.exists(str(log_dir / "audit.log"))
        assert not os.path.exists(str(log_dir / "application.log"))

    def test_subset_selection_does_not_change_what_belongs_in_a_stream(self, log_dir, monkeypatch):
        """Selecting only `security` must NOT make security.log start absorbing
        audit records simply because audit.log was not requested."""
        monkeypatch.setenv(log_streams.LOG_FILE_STREAMS_ENV, "security")
        _pipeline()
        logging.getLogger(log_streams.AUDIT_LOGGER).info("audit record")
        logging.getLogger("security.csrf").info("security record")
        log_streams.flush()

        messages = [r["message"] for r in _read_lines(str(log_dir / "security.log"))]
        assert messages == ["security record"]

    def test_disabled_by_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv(log_streams.LOG_DIR_ENV, str(tmp_path / "logs"))
        _, summary = _pipeline()
        assert summary["enabled"] is False
        assert not os.path.exists(str(tmp_path / "logs"))

    def test_unwritable_directory_degrades_to_stdout_only(self, tmp_path, monkeypatch):
        """Never fail the boot over logging."""
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("I am a file")
        monkeypatch.setenv(log_streams.LOG_TO_FILES_ENV, "1")
        monkeypatch.setenv(log_streams.LOG_DIR_ENV, str(blocker))

        _, summary = _pipeline()
        assert summary["enabled"] is False
        assert log_streams.active() is False
        logging.getLogger("server").info("still works")  # no exception

    def test_stop_is_idempotent_and_detaches_cleanly(self, log_dir):
        root, _ = _pipeline()
        assert log_streams.active()
        log_streams.stop_file_sinks()
        log_streams.stop_file_sinks()
        assert not log_streams.active()
        assert not any(isinstance(h, log_streams.BoundedQueueHandler) for h in root.handlers)

    def test_describe_reports_the_capacity_bound(self, log_dir, monkeypatch):
        monkeypatch.setenv(log_rotation.LOG_MAX_BYTES_ENV, "1048576")
        monkeypatch.setenv(log_rotation.LOG_BACKUP_COUNT_ENV, "4")
        _, summary = _pipeline()
        # 1 MB × (4 + 1) × 5 streams — the number an operator needs BEFORE
        # enabling this, not after filling a disk.
        assert summary["worst_case_bytes_total"] == 1048576 * 5 * 5
        assert summary["rotation"]["retention_days"] == 14

    def test_rotation_happens_through_the_real_pipeline(self, log_dir, monkeypatch):
        monkeypatch.setenv(log_rotation.LOG_MAX_BYTES_ENV, str(log_rotation.MIN_MAX_BYTES))
        monkeypatch.setenv(log_rotation.LOG_BACKUP_COUNT_ENV, "3")
        _pipeline()

        logger = logging.getLogger("server")
        for i in range(400):
            logger.info("padded line %s %s", i, "w" * 200)
        log_streams.flush()

        base = str(log_dir / "application.log")
        segments = log_rotation.list_segments(base)
        assert segments, "the pipeline never rotated"
        # backup_count is enforced, so the directory is bounded, not just the file.
        assert len(segments) <= 3
        assert os.path.getsize(base) <= log_rotation.MIN_MAX_BYTES + 2000


# --------------------------------------------------------------------------- #
# Redaction — the properties that must hold on DISK, where logs persist         #
# --------------------------------------------------------------------------- #
class TestRedactionInFiles:
    @pytest.mark.parametrize(
        "message,secret",
        [
            ("login failed password=hunter2 for user", "hunter2"),
            ("calling provider api_key=sk-live-abcdef123456", "sk-live-abcdef123456"),
            ("upstream said authorization: Bearer eyJhbGciOiJIUzI1NiJ9.body.sig",
             "eyJhbGciOiJIUzI1NiJ9.body.sig"),
            ("token=abc123secret expired", "abc123secret"),
        ],
    )
    def test_credentials_in_free_text_are_scrubbed_on_disk(self, log_dir, message, secret):
        _pipeline()
        logging.getLogger("server").warning(message)
        log_streams.flush()

        written = _read_lines(str(log_dir / "application.log"))[0]["message"]
        assert secret not in written
        assert "[REDACTED]" in written

    def test_sensitive_structured_fields_are_redacted_on_disk(self, log_dir):
        _pipeline()
        logging.getLogger("server").info(
            "auth attempt",
            extra={
                "password": "hunter2",
                "access_token": "eyJhbGciOi.secret.sig",
                "api_key": "sk-live-999",
                "cookie": "session=abc",
                "authorization": "Bearer xyz",
                "user_id": "507f1f77bcf86cd799439011",
            },
        )
        log_streams.flush()

        record = _read_lines(str(log_dir / "application.log"))[0]
        raw = json.dumps(record)
        for secret in ("hunter2", "eyJhbGciOi.secret.sig", "sk-live-999", "session=abc", "Bearer xyz"):
            assert secret not in raw, f"{secret!r} was written to disk"
        # A non-sensitive field is still there — redaction that eats everything
        # would be indistinguishable from broken logging.
        assert record["user_id"] == "507f1f77bcf86cd799439011"

    def test_access_log_status_code_is_not_redacted(self, log_dir):
        """`status_code` contains the substring `code`, an audit-redactor
        marker. Without the schema-field exemption the most-queried field in the
        access log would read [REDACTED]."""
        _pipeline()
        obs_logging.log_request(
            method="GET", route="/api/portfolio", path="/api/portfolio",
            status=200, duration_ms=12.5, client_ip="10.0.0.1", user_agent="pytest",
        )
        log_streams.flush()

        record = _read_lines(str(log_dir / "access.log"))[0]
        assert record["status_code"] == 200
        assert record["duration_ms"] == 12.5
        assert record["route"] == "/api/portfolio"

    def test_exception_messages_are_scrubbed_on_disk(self, log_dir):
        """A pymongo connection error stringifies with the URI credentials in it."""
        _pipeline()
        try:
            raise ConnectionError("auth failed for mongodb://user:password=s3cr3t@host/db")
        except ConnectionError:
            logging.getLogger("server").exception("db unreachable")
        log_streams.flush()

        record = _read_lines(str(log_dir / "error.log"))[0]
        assert "s3cr3t" not in record["exception"]["message"]


# --------------------------------------------------------------------------- #
# Throughput — a floor, not a benchmark                                         #
# --------------------------------------------------------------------------- #
class TestThroughput:
    def test_logging_stays_off_the_calling_thread(self, log_dir, monkeypatch):
        """The queue's whole purpose: the caller pays an in-memory put, not a
        write. Asserted as a generous floor (>5k records/sec of CALLER time)
        because a CI runner's absolute numbers are meaningless — but a
        regression that puts blocking disk I/O back on the request path would
        miss this by orders of magnitude, which is exactly what it guards."""
        monkeypatch.setenv(log_rotation.LOG_MAX_BYTES_ENV, str(log_rotation.MIN_MAX_BYTES))
        _pipeline()
        logger = logging.getLogger("server")

        count = 2000
        started = time.perf_counter()
        for i in range(count):
            logger.info("throughput probe %s", i)
        caller_seconds = time.perf_counter() - started
        log_streams.flush()

        assert count / caller_seconds > 5000
        assert log_streams.dropped_records() == 0
