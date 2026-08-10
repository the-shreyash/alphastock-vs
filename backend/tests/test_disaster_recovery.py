"""PH2.10 — disaster recovery tests: verification layering and rollback safety.

WHY THESE TWO SCRIPTS HAVE A TEST SUITE
---------------------------------------
Same reason the backup scripts do (see test_backup_restore.py), with one extra
turn of the screw: these scripts run *during an incident*. A backup script's bug
is discovered months later at the worst possible moment; a recovery script's bug
is discovered AT that moment, by someone who is already having the worst day of
their quarter and has no capacity to debug the tool they are debugging with.

So the properties asserted here are the ones whose absence would make the tools
actively harmful rather than merely useless:

* a failed prerequisite produces SKIP for everything beneath it, never a
  cascade of misleading failures pointing at the wrong layer
* an EMPTY restored database is a FAILURE, not a pass — the check that catches
  "we restored the stack but not the data"
* the manifest count comparison actually compares (a mismatch fails)
* the running build is checked against the expected one, so "I rolled back" and
  "the old code is running" are not confused
* a rollback whose target image is absent changes NOTHING — no env edit, no
  container recreation, no stopped service
* a rollback that does not become healthy is REVERTED automatically, and the
  revert is recorded
* `--previous` means the previous *different* version, not the previous line
* the env-file write is atomic and preserves every other key

HERMETIC BY CONSTRUCTION
------------------------
No Docker, no network, no MongoDB. `docker` and `curl` are replaced by stubs on
PATH whose behaviour is driven by environment variables, so what is asserted is
these SCRIPTS' logic — the part this repository owns. The real end-to-end drill
against a live stack is an operational procedure
(docs/operations/DISASTER_RECOVERY.md §Drills), not something CI can run.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DR_DIR = REPO_ROOT / "scripts" / "dr"
VERIFY = DR_DIR / "dr_verify.sh"
ROLLBACK = DR_DIR / "deploy_rollback.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="DR scripts require bash"
)


# --------------------------------------------------------------------------- #
# Stubs                                                                        #
# --------------------------------------------------------------------------- #
# One dispatching stub per binary rather than one per test: a change to the way
# a script invokes docker then breaks visibly in one place, instead of silently
# falling through to a default that returns the wrong shape everywhere.
DOCKER_STUB = r"""#!/usr/bin/env bash
set -u
echo "docker $*" >> "${STUB_CALL_LOG}"

case "$1" in
  info)
      exit "${STUB_DOCKER_INFO_RC:-0}" ;;
  image)
      # docker image inspect <ref>
      exit "${STUB_IMAGE_PRESENT_RC:-1}" ;;
  pull)
      exit "${STUB_PULL_RC:-1}" ;;
  inspect)
      fmt=""; ref=""
      shift
      while [ $# -gt 0 ]; do
          case "$1" in
              -f) fmt="$2"; shift 2 ;;
              *)  ref="$1"; shift ;;
          esac
      done
      case "${fmt}" in
          *State.Status*)  printf '%s\n' "${STUB_STATE:-running}" ;;
          *Health*)        printf '%s\n' "${STUB_HEALTH:-healthy}" ;;
          *RestartCount*)  printf '%s\n' "${STUB_RESTARTS:-0}" ;;
          # The running image reflects the last successful `up -d`, not a fixed
          # constant. A stub that reports the SAME image before and after a
          # rollback cannot tell a real rollback apart from a silent no-op —
          # which is precisely the defect PH2.12 found against a live daemon.
          *Config.Image*)
              if [ -n "${STUB_STATE_FILE:-}" ] && [ -s "${STUB_STATE_FILE:-}" ]; then
                  cat "${STUB_STATE_FILE}"
              else
                  printf '%s\n' "${STUB_RUNNING_IMAGE:-}"
              fi ;;
      esac
      exit 0 ;;
  compose)
      # Strip `compose -f <file>`
      shift
      [ "${1:-}" = "-f" ] && shift 2
      case "${1:-}" in
        config) exit "${STUB_COMPOSE_CONFIG_RC:-0}" ;;
        ps)
            svc="${3:-}"
            case " ${STUB_MISSING_SERVICES:-} " in
              *" ${svc} "*) exit 0 ;;
            esac
            printf 'cid-%s\n' "${svc}" ;;
        up)
            # A successful `up -d` publishes the tag it was invoked with, so a
            # later `inspect` reports the new build. STUB_UP_IS_NOOP=1 models
            # the failure this stub used to hide: compose exits 0, prints
            # nothing alarming, and recreates NOTHING (an inherited
            # BACKEND_IMAGE_TAG outranking the .env file, for instance).
            if [ "${STUB_UP_RC:-0}" = "0" ] && [ "${STUB_UP_IS_NOOP:-0}" != "1" ] \
               && [ -n "${STUB_STATE_FILE:-}" ]; then
                printf '%s:%s\n' "${BACKEND_IMAGE:-stockassist-backend}" \
                    "${BACKEND_IMAGE_TAG:-}" > "${STUB_STATE_FILE}"
            fi
            exit "${STUB_UP_RC:-0}" ;;
        exec)
            # exec -T <service> <cmd> ...
            shift            # exec
            [ "${1:-}" = "-T" ] && shift
            shift            # service
            case "${1:-}" in
              redis-cli) printf '%s\n' "${STUB_REDIS_REPLY:-PONG}" ;;
              # Hand off to the mongosh stub on PATH rather than reimplementing
              # it here, so `docker` mode and `direct` mode are answered by the
              # same fake database — the two must not be able to disagree.
              mongosh)   exec "$@" ;;
            esac ;;
      esac
      exit 0 ;;
esac
exit 0
"""

CURL_STUB = r"""#!/usr/bin/env bash
# Reproduces just enough curl: `-w '%{http_code}'` prints a status, otherwise a
# body. The URL is the last argument.
set -u
url=""; want_code=0
for a in "$@"; do
    case "$a" in
        -w) want_code=1 ;;
        http*) url="$a" ;;
    esac
done
echo "curl ${url}" >> "${STUB_CALL_LOG}"

code=200
case "${url}" in
    */api/health/live)    code="${STUB_LIVE_CODE:-200}" ;;
    */api/health/ready)   code="${STUB_READY_CODE:-200}" ;;
    */api/health/startup) code="${STUB_STARTUP_CODE:-200}" ;;
    */api/diagnostics)    code="${STUB_DIAG_CODE:-200}" ;;
esac

if [ "${want_code}" = "1" ]; then
    printf '%s' "${code}"
    [ "${code}" = "000" ] && exit 7
    exit 0
fi

case "${url}" in
    */api/diagnostics)
        [ "${STUB_DIAG_CODE:-200}" = "200" ] || exit 0
        # MUST mirror the real /api/diagnostics payload: the build identity is
        # nested under "build", and "process.python_version" sits alongside it as
        # a decoy the parser must not match. This stub previously emitted a flat
        # {"app_version","vcs_ref"} pair that the endpoint has never returned —
        # so the suite validated dr_verify.sh against its own bug and passed
        # while the real check could never succeed (PH2.12).
        printf '{"service": "stockassist-backend", "build": {"version": "%s", "revision": "%s", "build_date": "unknown"}, "process": {"python_version": "3.11.15"}}\n' \
            "${STUB_APP_VERSION:-1.4.0}" "${STUB_VCS_REF:-abc1234}" ;;
esac
exit 0
"""

MONGOSH_STUB = r"""#!/usr/bin/env bash
set -u
echo "mongosh $*" >> "${STUB_CALL_LOG}"
# Assigned, not inlined into the `${VAR:-default}` below: brace-escaping a JSON
# literal inside a parameter default leaks a backslash into the value, and the
# resulting "{...,\"trades\":7\}" is invalid JSON that fails in the comparator
# rather than in the stub.
default_collections='{"users":3,"trades":7}'
js=""
while [ $# -gt 0 ]; do
    case "$1" in
        --eval) js="$2"; shift 2 ;;
        *) shift ;;
    esac
done
case "${js}" in
    *ping*)           printf '%s\n' "${STUB_PING:-1}" ;;
    *JSON.stringify*) printf '%s\n' "${STUB_COLLECTIONS:-$default_collections}" ;;
esac
exit 0
"""


@pytest.fixture
def env(tmp_path):
    """An isolated DR environment: stub PATH, env file, ledger, call log."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (
        ("docker", DOCKER_STUB),
        ("curl", CURL_STUB),
        ("mongosh", MONGOSH_STUB),
    ):
        p = bin_dir / name
        p.write_text(body)
        p.chmod(0o755)

    env_file = tmp_path / "deploy.env"
    env_file.write_text("APP_ENV=production\nBACKEND_IMAGE_TAG=1.4.0\nKEEP_ME=yes\n")

    e = dict(os.environ)
    e.update(
        {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "STUB_CALL_LOG": str(tmp_path / "calls.log"),
            # Explicit, so the repository's own .env can never leak into a test.
            "BACKUP_MODE": "docker",
            "BACKUP_ROOT": str(tmp_path / "backups"),
            "BACKUP_COMPOSE_FILE": str(tmp_path / "docker-compose.yml"),
            "MONGO_DB_NAME": "testdb",
            "MONGO_ROOT_USERNAME": "root",
            "MONGO_ROOT_PASSWORD": "rootpw",
            "DR_ENV_FILE": str(env_file),
            "DR_DEPLOY_LEDGER": str(tmp_path / "deployments.tsv"),
            "BACKEND_IMAGE": "stockassist-backend",
            "STUB_RUNNING_IMAGE": "stockassist-backend:1.4.0",
            # Where the docker stub records the image published by `up -d`, so
            # "what is running" can change over a test the way it does in life.
            "STUB_STATE_FILE": str(tmp_path / "running_image"),
        }
    )
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")

    return {
        "env": e,
        "tmp": tmp_path,
        "env_file": env_file,
        "ledger": tmp_path / "deployments.tsv",
        "calls": tmp_path / "calls.log",
    }


def run(script, args, env, **overrides):
    e = dict(env["env"])
    e.update({k: str(v) for k, v in overrides.items()})
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        env=e,
        timeout=120,
    )


def out(proc):
    return proc.stdout + proc.stderr


# --------------------------------------------------------------------------- #
# dr_verify.sh — layering                                                      #
# --------------------------------------------------------------------------- #
class TestVerifyLayering:
    def test_all_green_exits_zero(self, env):
        p = run(VERIFY, ["--level", "full", "--base-url", "http://app"], env)
        assert p.returncode == 0, out(p)
        assert "fail=0" in p.stdout
        assert "VERIFIED" in p.stderr

    def test_summary_line_is_machine_readable_on_stdout(self, env):
        p = run(VERIFY, ["--level", "quick", "--base-url", "http://app"], env)
        # stdout is exactly one parseable line; the human report is on stderr,
        # so a wrapper can consume one without parsing the other.
        assert p.stdout.strip().startswith("dr_verify level=quick")
        assert len(p.stdout.strip().splitlines()) == 1

    def test_missing_container_skips_data_layer_rather_than_failing_it(self, env):
        p = run(
            VERIFY,
            ["--level", "full", "--base-url", "http://app"],
            env,
            STUB_MISSING_SERVICES="mongo",
        )
        assert p.returncode == 1, out(p)
        text = out(p)
        assert "FAIL  container: mongo" in text
        # The point of the test: the DATA checks must not also report failure —
        # they never ran, and three failures pointing at three layers sends the
        # operator to the wrong one.
        assert "SKIP  mongodb reachable" in text
        assert "FAIL  mongodb reachable" not in text

    def test_unhealthy_container_fails(self, env):
        p = run(VERIFY, ["--level", "quick", "--base-url", "http://app"], env,
                STUB_HEALTH="unhealthy")
        assert p.returncode == 1
        assert "healthcheck reports unhealthy" in out(p)

    def test_container_still_starting_is_not_a_failure(self, env):
        p = run(VERIFY, ["--level", "quick", "--base-url", "http://app"], env,
                STUB_HEALTH="starting")
        assert p.returncode == 0, out(p)
        assert "start period" in out(p)

    def test_restart_count_is_surfaced_as_a_warning(self, env):
        p = run(VERIFY, ["--level", "quick", "--base-url", "http://app"], env,
                STUB_RESTARTS="3")
        assert p.returncode == 0
        assert "3 restart(s)" in out(p)

    def test_readiness_503_fails_but_liveness_still_passes(self, env):
        p = run(VERIFY, ["--level", "quick", "--base-url", "http://app"], env,
                STUB_READY_CODE="503")
        assert p.returncode == 1
        text = out(p)
        assert "PASS  liveness" in text
        assert "FAIL  readiness" in text

    def test_dead_process_skips_the_checks_below_it(self, env):
        p = run(VERIFY, ["--level", "quick", "--base-url", "http://app"], env,
                STUB_LIVE_CODE="000")
        assert p.returncode == 1
        text = out(p)
        assert "FAIL  liveness" in text
        assert "SKIP  readiness" in text
        assert "HTTP 000000" not in text  # the doubled-status regression

    def test_quick_level_skips_the_data_layer(self, env):
        p = run(VERIFY, ["--level", "quick", "--base-url", "http://app"], env)
        assert "SKIP  mongodb reachable" in out(p)
        assert "--level quick" in out(p)


class TestVerifyDataLayer:
    def test_empty_database_is_a_failure(self, env):
        """The check that catches 'we restored the stack but not the data'."""
        p = run(VERIFY, ["--level", "full", "--base-url", "http://app"], env,
                STUB_COLLECTIONS="{}")
        assert p.returncode == 1
        assert "no collections" in out(p)

    def test_unreachable_mongo_fails_and_skips_the_content_check(self, env):
        p = run(VERIFY, ["--level", "full", "--base-url", "http://app"], env,
                STUB_PING="0")
        assert p.returncode == 1
        text = out(p)
        assert "FAIL  mongodb reachable" in text
        assert "SKIP  mongodb has data" in text

    def test_manifest_counts_matching_passes(self, env, tmp_path):
        manifest = tmp_path / "m.json"
        manifest.write_text('{"schema":1,"collections":{"users":3,"trades":7}}')
        p = run(VERIFY, ["--level", "full", "--base-url", "http://app",
                         "--expect-manifest", str(manifest)], env)
        assert p.returncode == 0, out(p)
        assert "PASS  counts match manifest" in out(p)

    def test_manifest_mismatch_fails_and_names_the_collection(self, env, tmp_path):
        """A comparison that cannot fail is not a comparison."""
        manifest = tmp_path / "m.json"
        manifest.write_text('{"schema":1,"collections":{"users":3,"trades":9999}}')
        p = run(VERIFY, ["--level", "full", "--base-url", "http://app",
                         "--expect-manifest", str(manifest)], env)
        assert p.returncode == 1
        assert "MISMATCH trades expected=9999 actual=7" in out(p)

    def test_manifest_missing_collection_is_reported_as_missing(self, env, tmp_path):
        manifest = tmp_path / "m.json"
        manifest.write_text('{"schema":1,"collections":{"users":3,"gone":1}}')
        p = run(VERIFY, ["--level", "full", "--base-url", "http://app",
                         "--expect-manifest", str(manifest)], env)
        assert p.returncode == 1
        assert "gone expected=1 actual=MISSING" in out(p)

    def test_redis_unreachable_fails(self, env):
        p = run(VERIFY, ["--level", "full", "--base-url", "http://app"], env,
                STUB_REDIS_REPLY="ERR")
        assert p.returncode == 1
        assert "degraded in-process cache" in out(p)


class TestVerifyRunningBuild:
    def test_version_mismatch_fails(self, env):
        """The check that makes a rollback verifiable instead of assumed."""
        p = run(VERIFY, ["--level", "quick", "--base-url", "http://app",
                         "--expect-version", "1.3.0"], env,
                STUB_APP_VERSION="1.4.0")
        assert p.returncode == 1
        assert "expected 1.3.0, serving 1.4.0" in out(p)

    def test_version_match_passes(self, env):
        p = run(VERIFY, ["--level", "quick", "--base-url", "http://app",
                         "--expect-version", "1.3.0"], env,
                STUB_APP_VERSION="1.3.0")
        assert p.returncode == 0, out(p)

    def test_gated_diagnostics_is_a_skip_unless_a_version_was_asserted(self, env):
        p = run(VERIFY, ["--level", "quick", "--base-url", "http://app"], env,
                STUB_DIAG_CODE="403")
        assert p.returncode == 0
        assert "SKIP  running build" in out(p)

    def test_gated_diagnostics_is_a_failure_when_a_version_was_asserted(self, env):
        p = run(VERIFY, ["--level", "quick", "--base-url", "http://app",
                         "--expect-version", "1.3.0"], env,
                STUB_DIAG_CODE="403")
        assert p.returncode == 1
        assert "DR_OPS_TOKEN" in out(p)


class TestVerifyUsage:
    def test_bad_level_is_a_usage_error(self, env):
        p = run(VERIFY, ["--level", "medium"], env)
        assert p.returncode == 2  # 2 = usage, so monitoring never pages for a typo

    def test_unknown_flag_is_a_usage_error(self, env):
        p = run(VERIFY, ["--wat"], env)
        assert p.returncode == 2

    def test_help_exits_zero(self, env):
        p = run(VERIFY, ["--help"], env)
        assert p.returncode == 0
        assert "post-recovery verification" in out(p)


# --------------------------------------------------------------------------- #
# deploy_rollback.sh                                                           #
# --------------------------------------------------------------------------- #
class TestLedger:
    def test_list_without_a_ledger_is_not_an_error(self, env):
        p = run(ROLLBACK, ["list"], env)
        assert p.returncode == 0
        assert "record the current deployment" in out(p)

    def test_record_then_list(self, env):
        run(ROLLBACK, ["record", "--tag", "1.3.0", "--note", "baseline"], env)
        p = run(ROLLBACK, ["list"], env)
        assert "1.3.0" in out(p) and "baseline" in out(p)

    def test_ledger_carries_no_secret_material(self, env):
        env["env"]["MONGO_ROOT_PASSWORD"] = "sup3r-secret-value"
        run(ROLLBACK, ["record", "--tag", "1.3.0"], env)
        body = env["ledger"].read_text()
        assert "sup3r-secret-value" not in body
        assert "rootpw" not in body

    def test_running_container_wins_over_the_env_file(self, env):
        """During an incident the two disagree more often than they agree."""
        env["env_file"].write_text("BACKEND_IMAGE_TAG=9.9.9\n")
        p = run(ROLLBACK, ["current"], env, STUB_RUNNING_IMAGE="stockassist-backend:1.4.0")
        assert p.stdout.strip() == "1.4.0"

    def test_previous_skips_repeats_of_the_current_tag(self, env):
        for tag, note in (("1.3.0", "old"), ("1.4.0", "new"), ("1.4.0", "restart")):
            run(ROLLBACK, ["record", "--tag", tag, "--note", note], env)
        p = run(ROLLBACK, ["rollback", "--previous", "--yes"], env,
                STUB_IMAGE_PRESENT_RC="1")
        # Refused for the right reason, and about the right tag: 1.3.0, not the
        # duplicate 1.4.0 that is already running.
        assert "stockassist-backend:1.3.0" in out(p)

    def test_previous_without_a_ledger_fails_cleanly(self, env):
        p = run(ROLLBACK, ["rollback", "--previous", "--yes"], env)
        assert p.returncode == 1
        assert "no previous deployment" in out(p)

    def test_unknown_command_is_a_usage_error(self, env):
        p = run(ROLLBACK, ["frobnicate"], env)
        assert p.returncode == 2


class TestRollbackPreconditions:
    def test_absent_target_image_changes_nothing(self, env):
        """The precondition that exists because there is no registry yet."""
        before = env["env_file"].read_text()
        p = run(ROLLBACK, ["rollback", "--to", "1.3.0", "--yes"], env,
                STUB_IMAGE_PRESENT_RC="1")
        assert p.returncode == 1
        assert "NOT on this host" in out(p)
        assert env["env_file"].read_text() == before
        assert "up -d" not in env["calls"].read_text()

    def test_rollback_to_the_running_tag_is_a_no_op(self, env):
        p = run(ROLLBACK, ["rollback", "--to", "1.4.0", "--yes"], env)
        assert p.returncode == 0
        assert "nothing to do" in out(p)
        assert "up -d" not in env["calls"].read_text()

    def test_dry_run_touches_nothing(self, env):
        before = env["env_file"].read_text()
        p = run(ROLLBACK, ["rollback", "--to", "1.3.0", "--yes", "--dry-run"], env,
                STUB_IMAGE_PRESENT_RC="0")
        assert p.returncode == 0
        assert env["env_file"].read_text() == before
        assert "up -d" not in env["calls"].read_text()

    def test_missing_target_argument_is_a_usage_error(self, env):
        p = run(ROLLBACK, ["rollback", "--yes"], env)
        assert p.returncode == 2

    def test_to_and_previous_are_mutually_exclusive(self, env):
        p = run(ROLLBACK, ["rollback", "--to", "1.3.0", "--previous", "--yes"], env)
        assert p.returncode == 2


class TestRollbackApply:
    def test_successful_rollback_rewrites_the_tag_and_preserves_other_keys(self, env):
        p = run(ROLLBACK, ["rollback", "--to", "1.3.0", "--yes"], env,
                STUB_IMAGE_PRESENT_RC="0", STUB_APP_VERSION="1.3.0")
        assert p.returncode == 0, out(p)
        body = env["env_file"].read_text()
        assert "BACKEND_IMAGE_TAG=1.3.0" in body
        assert "KEEP_ME=yes" in body       # an atomic rewrite, not a truncation
        assert "APP_ENV=production" in body
        assert "up -d" in env["calls"].read_text()

    def test_rollback_records_both_the_before_state_and_the_result(self, env):
        run(ROLLBACK, ["rollback", "--to", "1.3.0", "--yes"], env,
            STUB_IMAGE_PRESENT_RC="0")
        ledger = env["ledger"].read_text()
        # The roll-FORWARD point must be recorded before anything changes, or a
        # rollback is a one-way door.
        assert "state before rollback to 1.3.0" in ledger
        assert "rollback from 1.4.0" in ledger

    def test_only_the_backend_is_recreated(self, env):
        run(ROLLBACK, ["rollback", "--to", "1.3.0", "--yes"], env,
            STUB_IMAGE_PRESENT_RC="0")
        up_lines = [ln for ln in env["calls"].read_text().splitlines() if "up -d" in ln]
        assert up_lines
        for line in up_lines:
            # `--no-deps`: restarting mongo and redis during an application
            # rollback is a cold start on every tier at once.
            assert "--no-deps" in line and "backend" in line
            assert " mongo" not in line

    def test_a_rollback_that_changed_nothing_is_reported_as_a_failure(self, env):
        """A healthy stack still serving the OLD build is a FAILED rollback.

        PH2.12 found this against a live Docker daemon: scripts/backup/lib.sh
        exports every key it parses from `.env`, so BACKEND_IMAGE_TAG was
        already in the environment holding the tag being rolled away from — and
        compose ranks shell variables above the `.env` file. The rewritten file
        was ignored, compose recreated nothing, and the failing release kept
        serving while the script printed "rollback verified", because the build
        being rolled away from passes its health check by definition. Health and
        "the intended build is running" are two claims; only the second one is a
        rollback.
        """
        p = run(ROLLBACK, ["rollback", "--to", "1.3.0", "--yes"], env,
                STUB_IMAGE_PRESENT_RC="0", STUB_UP_IS_NOOP="1")
        assert p.returncode == 1, out(p)
        assert "ROLLBACK DID NOT TAKE EFFECT" in out(p)
        assert "still serving '1.4.0'" in out(p)
        # The operator must not be able to close the incident on this.
        assert "rollback verified" not in out(p)
        # …and the ledger records the failure rather than a phantom success.
        assert "FAILED rollback to 1.3.0" in env["ledger"].read_text()

    def test_the_intended_tag_is_passed_to_compose_not_only_written_to_the_env_file(self, env):
        """The .env rewrite alone is not enough — see the test above."""
        run(ROLLBACK, ["rollback", "--to", "1.3.0", "--yes"], env,
            STUB_IMAGE_PRESENT_RC="0")
        # The stub records what `up -d` actually published; if the tag reached
        # compose only via the file, an inherited value would have won.
        assert (env["tmp"] / "running_image").read_text().strip() == \
            "stockassist-backend:1.3.0"

    def test_failed_verification_triggers_an_automatic_revert(self, env):
        """A rollback to a version that is also broken must not be left running."""
        p = run(ROLLBACK, ["rollback", "--to", "1.3.0", "--yes", "--timeout", "1"], env,
                STUB_IMAGE_PRESENT_RC="0", STUB_READY_CODE="503")
        assert p.returncode == 1
        assert "reverting to 1.4.0" in out(p)
        assert "BACKEND_IMAGE_TAG=1.4.0" in env["env_file"].read_text()
        assert "auto-revert" in env["ledger"].read_text()

    def test_compose_failure_also_reverts(self, env):
        p = run(ROLLBACK, ["rollback", "--to", "1.3.0", "--yes"], env,
                STUB_IMAGE_PRESENT_RC="0", STUB_UP_RC="1")
        assert p.returncode == 1
        assert "BACKEND_IMAGE_TAG=1.4.0" in env["env_file"].read_text()

    def test_no_verify_leaves_a_loud_warning_and_marks_the_ledger(self, env):
        p = run(ROLLBACK, ["rollback", "--to", "1.3.0", "--yes", "--no-verify"], env,
                STUB_IMAGE_PRESENT_RC="0")
        assert p.returncode == 0
        assert "NOT checking" in out(p)
        assert "unverified" in env["ledger"].read_text()

    def test_env_file_is_created_when_absent(self, env):
        env["env_file"].unlink()
        p = run(ROLLBACK, ["rollback", "--to", "1.3.0", "--yes"], env,
                STUB_IMAGE_PRESENT_RC="0")
        assert p.returncode == 0, out(p)
        assert env["env_file"].read_text().strip() == "BACKEND_IMAGE_TAG=1.3.0"
