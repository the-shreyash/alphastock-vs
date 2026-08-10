"""PH2.9 — backup & restore tests: publication, encryption, retention, guards.

WHY SHELL SCRIPTS HAVE A PYTEST SUITE
-------------------------------------
The backup scripts are the only code in this repository whose failure mode is
*silent and delayed*: a bug introduced today produces plausible-looking files
every night and is discovered months later, during the one hour when it matters
most. Everything else in the codebase fails in front of a user. This does not.

So the properties below are asserted mechanically rather than trusted to review.
Each one corresponds to a documented failure that has cost real companies real
data:

* a truncated / empty artifact is never published (a `.partial` that got
  renamed is the classic "our backups were all 0 bytes" incident)
* the manifest's SHA-256 matches the file that will actually be restored
* an encrypted artifact round-trips through the exact decrypt path the restore
  uses — the encrypt and decrypt sides cannot drift apart unnoticed
* production REFUSES to write an unencrypted database dump
* corruption and a wrong passphrase are both DETECTED rather than reported OK
* retention keeps N per tier, never crosses tiers, never touches a file it did
  not create, and never runs before the new backup exists
* a failing dump leaves every previous backup intact
* the restore refuses to run without a manifest, refuses to overwrite a
  populated database unattended, and verifies the archive BEFORE writing
* the `.env` reader parses; it does not `source` (i.e. it is not an RCE)

HERMETIC BY CONSTRUCTION
------------------------
No MongoDB, no Docker, no network. `mongodump`, `mongorestore` and `mongosh` are
replaced by stubs on PATH, so these tests assert the SCRIPTS' behaviour — the
part that is this repository's to get right — and not the mongo tools', which
are upstream's. The real end-to-end drill against a live mongod is a documented
operational procedure (docs/operations/BACKUP_AND_RESTORE.md §Verification), not
something CI can run.
"""
import gzip
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKUP_DIR = REPO_ROOT / "scripts" / "backup"

PASSPHRASE = "test-passphrase-not-a-real-secret"

# The four bytes at the head of every mongodump archive (little-endian
# 0x8199e26d). verify_backup.sh checks for exactly these, so the stub must
# produce them or the "is this really a mongodump archive?" check would be
# vacuous.
MONGO_ARCHIVE_MAGIC = b"\x6d\xe2\x99\x81"

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None or shutil.which("gzip") is None,
    reason="backup scripts require openssl and gzip",
)


# --------------------------------------------------------------------------- #
# Stubs                                                                         #
# --------------------------------------------------------------------------- #
MONGODUMP_STUB = r"""#!/usr/bin/env bash
# Stub mongodump. Writes a gzip stream whose payload begins with the real
# mongodump archive magic, so the structural verifier is genuinely exercised.
set -eu
echo "mongodump $*" >> "${STUB_CALL_LOG}"
if [ "${STUB_MONGODUMP_MODE:-ok}" = "fail" ]; then
    echo "stub: simulated dump failure" >&2
    exit 1
fi
if [ "${STUB_MONGODUMP_MODE:-ok}" = "empty" ]; then
    exit 0
fi
printf '\155\342\231\201' | cat - "${STUB_PAYLOAD}" | gzip -c
"""

MONGORESTORE_STUB = r"""#!/usr/bin/env bash
set -eu
echo "mongorestore $*" >> "${STUB_CALL_LOG}"
cat > /dev/null
exit "${STUB_MONGORESTORE_RC:-0}"
"""

# One stub for every mongosh call the scripts make, dispatched on a distinctive
# substring of each --eval script. Keeping the dispatch here — rather than
# giving each test its own bespoke stub — means a change to one of those JS
# snippets breaks visibly in one place instead of silently returning the wrong
# shape everywhere.
MONGOSH_STUB = r"""#!/usr/bin/env bash
set -eu
echo "mongosh $*" >> "${STUB_CALL_LOG}"
default_collections='{"alpha":2}'
js=""
while [ $# -gt 0 ]; do
    case "$1" in
        --eval) js="$2"; shift 2 ;;
        *) shift ;;
    esac
done
case "${js}" in
    *dropDatabase*)   exit 0 ;;
    *"names.length"*) printf '%s\n' "${STUB_TARGET_STATE:-0 0}" ;;
    *MISSING*)        printf '%s\n' "${STUB_COMPARE_REPORT:-MATCH alpha 2 2}" ;;
    *JSON.stringify*) printf '%s\n' "${STUB_COLLECTIONS:-$default_collections}" ;;
esac
exit 0
"""


@pytest.fixture
def env(tmp_path):
    """A complete, isolated backup environment: stub PATH, backup root, secrets."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (
        ("mongodump", MONGODUMP_STUB),
        ("mongorestore", MONGORESTORE_STUB),
        ("mongosh", MONGOSH_STUB),
    ):
        p = bin_dir / name
        p.write_text(body)
        p.chmod(0o755)

    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"FAKE-COLLECTION-DATA" * 64)

    passphrase_file = tmp_path / "passphrase"
    passphrase_file.write_text(PASSPHRASE)
    passphrase_file.chmod(0o600)

    e = dict(os.environ)
    e.update(
        PATH=f"{bin_dir}:{os.environ.get('PATH', '')}",
        BACKUP_ROOT=str(tmp_path / "backups"),
        BACKUP_MODE="direct",
        MONGO_URL="mongodb://127.0.0.1:27017/testdb",
        MONGO_DB_NAME="testdb",
        APP_ENV="development",
        BACKUP_ENCRYPTION_PASSPHRASE_FILE=str(passphrase_file),
        STUB_CALL_LOG=str(tmp_path / "calls.log"),
        STUB_PAYLOAD=str(payload),
    )
    # The scripts read the repository `.env` for convenience. In a test that
    # would silently import the developer's real configuration, so it is pointed
    # at an empty file — the tests must depend only on what they set.
    e["BACKUP_CONFIG_SOURCE_ROOT"] = str(tmp_path / "cfgroot")
    (tmp_path / "cfgroot").mkdir()

    e["_TMP"] = str(tmp_path)
    return e


def run(script, *args, env=None, expect_rc=0):
    """Run a backup script and assert its exit code, showing output on failure."""
    proc = subprocess.run(
        [str(BACKUP_DIR / script), *args],
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    if expect_rc is not None:
        assert proc.returncode == expect_rc, (
            f"{script} exited {proc.returncode}, expected {expect_rc}\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    return proc


def artifacts(root, tier="daily", kind="mongo"):
    """Restorable artifacts only — the exact set the scripts themselves select.

    Notably excludes `*.rejected`, which is what a failed post-write
    verification renames an artifact to: quarantined files must be invisible to
    `--latest`, to retention, and to these assertions alike.
    """
    d = Path(root) / kind / tier
    if not d.exists():
        return []
    suffixes = (".archive.gz", ".archive.gz.enc", ".tar.gz", ".tar.gz.enc")
    return sorted(p for p in d.iterdir() if p.name.endswith(suffixes))


def manifest_of(artifact: Path) -> dict:
    base = artifact.name
    for suffix in (".enc", ".gz", ".archive", ".tar"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return json.loads((artifact.parent / f"{base}.manifest.json").read_text())


def sha256_of(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def decrypt(path: Path, out: Path, passphrase=PASSPHRASE):
    subprocess.run(
        ["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "600000",
         "-md", "sha512", "-pass", f"pass:{passphrase}", "-in", str(path), "-out", str(out)],
        check=True,
        capture_output=True,
    )


# --------------------------------------------------------------------------- #
# Publication                                                                   #
# --------------------------------------------------------------------------- #
class TestPublication:
    def test_backup_writes_artifact_and_manifest(self, env):
        run("backup_mongo.sh", "--tier", "daily", env=env)
        found = artifacts(env["BACKUP_ROOT"])
        assert len(found) == 1
        m = manifest_of(found[0])
        assert m["kind"] == "mongo"
        assert m["database"] == "testdb"
        assert m["tier"] == "daily"
        assert m["schema"] == 1

    def test_manifest_sha256_matches_the_published_file(self, env):
        """The checksum must describe the file that will be restored, not the
        bytes that were written — `mv` across a filesystem is a copy, and a copy
        is where a full disk truncates silently."""
        run("backup_mongo.sh", "--tier", "daily", env=env)
        artifact = artifacts(env["BACKUP_ROOT"])[0]
        assert manifest_of(artifact)["sha256"] == sha256_of(artifact)
        assert manifest_of(artifact)["size_bytes"] == artifact.stat().st_size

    def test_collection_baseline_is_recorded(self, env):
        """Without the per-collection baseline a restore cannot be verified at
        all — only observed."""
        env["STUB_COLLECTIONS"] = '{"users":5,"trades":125}'
        run("backup_mongo.sh", "--tier", "daily", env=env)
        assert manifest_of(artifacts(env["BACKUP_ROOT"])[0])["collections"] == {"users": 5, "trades": 125}

    def test_empty_dump_is_never_published(self, env):
        """A zero-byte dump restores to an empty database and is exactly what a
        mid-dump authentication failure produces.

        With encryption on it is also *invisible* to a size check: openssl turns
        zero bytes of input into a ~32-byte file. The post-write structural
        verification is what catches it, and the result must not be selectable
        as a backup afterwards.
        """
        env["STUB_MONGODUMP_MODE"] = "empty"
        run("backup_mongo.sh", "--tier", "daily", env=env, expect_rc=1)
        assert artifacts(env["BACKUP_ROOT"]) == []

    def test_a_rejected_artifact_is_quarantined_not_deleted(self, env):
        """The file is the evidence for why the backup failed. It must be inert
        — outside every glob — but it must still exist."""
        env["STUB_MONGODUMP_MODE"] = "empty"
        run("backup_mongo.sh", "--tier", "daily", env=env, expect_rc=1)
        d = Path(env["BACKUP_ROOT"]) / "mongo" / "daily"
        assert list(d.glob("*.rejected")), "the failed artifact was deleted instead of quarantined"

    def test_failed_dump_leaves_previous_backups_intact(self, env):
        run("backup_mongo.sh", "--tier", "daily", env=env)
        good = artifacts(env["BACKUP_ROOT"])[0]
        good_sha = sha256_of(good)

        env["STUB_MONGODUMP_MODE"] = "fail"
        run("backup_mongo.sh", "--tier", "daily", env=env, expect_rc=1)

        assert good.exists(), "a failed run destroyed an existing good backup"
        assert sha256_of(good) == good_sha

    def test_no_partial_file_survives_a_failure(self, env):
        env["STUB_MONGODUMP_MODE"] = "fail"
        run("backup_mongo.sh", "--tier", "daily", env=env, expect_rc=1)
        leftovers = list((Path(env["BACKUP_ROOT"]) / "mongo" / "daily").glob("*.partial"))
        assert leftovers == [], f"a truncated artifact was left behind: {leftovers}"

    def test_backup_directory_is_not_world_readable(self, env):
        run("backup_mongo.sh", "--tier", "daily", env=env)
        artifact = artifacts(env["BACKUP_ROOT"])[0]
        assert artifact.stat().st_mode & 0o077 == 0, "backup artifact is readable by other users"


# --------------------------------------------------------------------------- #
# Encryption                                                                    #
# --------------------------------------------------------------------------- #
class TestEncryption:
    def test_encrypted_artifact_round_trips(self, tmp_path, env):
        """Decrypting with the documented parameters must yield a valid gzip
        stream whose payload is a mongodump archive. This is the property that
        keeps the encrypt and decrypt paths from drifting apart."""
        run("backup_mongo.sh", "--tier", "daily", env=env)
        artifact = artifacts(env["BACKUP_ROOT"])[0]
        assert artifact.name.endswith(".archive.gz.enc")

        plain = tmp_path / "plain.gz"
        decrypt(artifact, plain)
        assert gzip.decompress(plain.read_bytes()).startswith(MONGO_ARCHIVE_MAGIC)

    def test_production_refuses_to_write_an_unencrypted_dump(self, env):
        env["APP_ENV"] = "production"
        del env["BACKUP_ENCRYPTION_PASSPHRASE_FILE"]
        proc = run("backup_mongo.sh", "--tier", "daily", env=env, expect_rc=1)
        assert "refusing to write an UNENCRYPTED backup" in proc.stderr
        assert artifacts(env["BACKUP_ROOT"]) == []

    def test_development_may_write_plaintext_but_says_so_loudly(self, env):
        del env["BACKUP_ENCRYPTION_PASSPHRASE_FILE"]
        proc = run("backup_mongo.sh", "--tier", "daily", env=env)
        assert "PLAINTEXT" in proc.stderr
        assert artifacts(env["BACKUP_ROOT"])[0].name.endswith(".archive.gz")

    def test_trailing_newline_in_the_passphrase_file_is_stripped(self, tmp_path, env):
        """An editor adds a trailing newline silently; openssl would treat it as
        part of the passphrase, producing an artifact only decryptable by
        whoever reproduces the same stray byte."""
        pf = tmp_path / "pass-with-newline"
        pf.write_text(PASSPHRASE + "\n")
        env["BACKUP_ENCRYPTION_PASSPHRASE_FILE"] = str(pf)
        run("backup_mongo.sh", "--tier", "daily", env=env)

        plain = tmp_path / "plain.gz"
        decrypt(artifacts(env["BACKUP_ROOT"])[0], plain, passphrase=PASSPHRASE)
        assert gzip.decompress(plain.read_bytes()).startswith(MONGO_ARCHIVE_MAGIC)


# --------------------------------------------------------------------------- #
# Verification                                                                  #
# --------------------------------------------------------------------------- #
class TestVerification:
    def test_structural_verification_passes_on_a_fresh_backup(self, env):
        run("backup_mongo.sh", "--tier", "daily", env=env)
        proc = run("verify_backup.sh", "--level", "structural",
                   str(artifacts(env["BACKUP_ROOT"])[0]), env=env)
        assert "structural OK" in proc.stderr

    def test_corruption_is_detected(self, env):
        run("backup_mongo.sh", "--tier", "daily", env=env)
        artifact = artifacts(env["BACKUP_ROOT"])[0]

        data = bytearray(artifact.read_bytes())
        data[len(data) // 2] ^= 0xFF
        artifact.write_bytes(bytes(data))

        proc = run("verify_backup.sh", "--level", "checksum", str(artifact), env=env, expect_rc=1)
        assert "CHECKSUM MISMATCH" in proc.stderr

    def test_corruption_that_survives_the_checksum_is_caught_structurally(self, env):
        """Rewriting the manifest to match a corrupted artifact defeats the
        checksum. The gzip CRC over the decrypted payload does not care."""
        run("backup_mongo.sh", "--tier", "daily", env=env)
        artifact = artifacts(env["BACKUP_ROOT"])[0]

        data = bytearray(artifact.read_bytes())
        data[len(data) // 2] ^= 0xFF
        artifact.write_bytes(bytes(data))

        manifest_path = artifact.parent / f"{artifact.name.replace('.archive.gz.enc', '')}.manifest.json"
        m = json.loads(manifest_path.read_text())
        m["sha256"] = sha256_of(artifact)
        manifest_path.write_text(json.dumps(m, indent=2))

        proc = run("verify_backup.sh", "--level", "structural", str(artifact), env=env, expect_rc=1)
        assert "DECRYPTION FAILED" in proc.stderr or "GZIP INTEGRITY FAILED" in proc.stderr

    def test_wrong_passphrase_is_detected(self, tmp_path, env):
        run("backup_mongo.sh", "--tier", "daily", env=env)
        artifact = artifacts(env["BACKUP_ROOT"])[0]

        wrong = tmp_path / "wrong"
        wrong.write_text("not-the-passphrase")
        env["BACKUP_ENCRYPTION_PASSPHRASE_FILE"] = str(wrong)

        proc = run("verify_backup.sh", "--level", "structural", str(artifact), env=env, expect_rc=1)
        assert "DECRYPTION FAILED" in proc.stderr

    def test_missing_manifest_fails_verification(self, env):
        run("backup_mongo.sh", "--tier", "daily", env=env)
        artifact = artifacts(env["BACKUP_ROOT"])[0]
        next(artifact.parent.glob("*.manifest.json")).unlink()

        proc = run("verify_backup.sh", "--level", "checksum", str(artifact), env=env, expect_rc=1)
        assert "missing manifest" in proc.stderr

    def test_unknown_manifest_schema_is_refused_not_ignored(self, env):
        """A verifier that reports OK because it silently misread a field is
        worse than one that errors — the error gets fixed."""
        run("backup_mongo.sh", "--tier", "daily", env=env)
        artifact = artifacts(env["BACKUP_ROOT"])[0]
        manifest_path = next(artifact.parent.glob("*.manifest.json"))
        m = json.loads(manifest_path.read_text())
        m["schema"] = 99
        manifest_path.write_text(json.dumps(m, indent=2))

        proc = run("verify_backup.sh", "--level", "checksum", str(artifact), env=env, expect_rc=1)
        assert "cannot verify" in proc.stderr

    def test_a_valid_gzip_that_is_not_a_mongodump_archive_is_rejected(self, tmp_path, env):
        """The failure this catches: a shell redirection that captured an error
        message instead of a dump. Perfectly valid gzip, useless as a backup."""
        env["STUB_PAYLOAD"] = str(tmp_path / "payload.bin")
        run("backup_mongo.sh", "--tier", "daily", env=env)
        artifact = artifacts(env["BACKUP_ROOT"])[0]

        impostor = gzip.compress(b"ERROR: could not connect to server\n")
        subprocess.run(
            ["openssl", "enc", "-aes-256-cbc", "-salt", "-pbkdf2", "-iter", "600000",
             "-md", "sha512", "-pass", f"pass:{PASSPHRASE}", "-out", str(artifact)],
            input=impostor, check=True, capture_output=True,
        )
        manifest_path = next(artifact.parent.glob("*.manifest.json"))
        m = json.loads(manifest_path.read_text())
        m["sha256"] = sha256_of(artifact)
        manifest_path.write_text(json.dumps(m, indent=2))

        proc = run("verify_backup.sh", "--level", "structural", str(artifact), env=env, expect_rc=1)
        assert "NOT A MONGODUMP ARCHIVE" in proc.stderr


# --------------------------------------------------------------------------- #
# Retention                                                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.slow
class TestRetention:
    """Marked `slow` (PH3.1): every test here sleeps 1.05 s per artifact.

    The sleeps are not hiding a race — the retention pruner sorts by
    whole-second filesystem mtime, so two artifacts created inside the same
    second are genuinely indistinguishable to it and the fixture has to space
    them out. That makes this class ~43 s of the suite's ~140 s. It stays in
    the default run (it is real coverage of PH2.9's pruner); the marker exists
    so `pytest -m "not slow"` gives a fast inner loop. Making it fast for real
    means letting the pruner take an explicit clock — PH3.11's problem, not a
    reason to skip it now.
    """

    def test_keeps_only_the_configured_number_per_tier(self, env):
        env["BACKUP_RETAIN_DAILY"] = "3"
        for _ in range(5):
            run("backup_mongo.sh", "--tier", "daily", env=env)
            # The filename carries a whole-second UTC timestamp; without this the
            # five backups would collide on one name.
            subprocess.run(["sleep", "1.05"], check=True)
        assert len(artifacts(env["BACKUP_ROOT"])) == 3

    def test_pruning_removes_the_manifest_with_its_artifact(self, env):
        env["BACKUP_RETAIN_DAILY"] = "1"
        for _ in range(3):
            run("backup_mongo.sh", "--tier", "daily", env=env)
            subprocess.run(["sleep", "1.05"], check=True)
        d = Path(env["BACKUP_ROOT"]) / "mongo" / "daily"
        assert len(list(d.glob("*.manifest.json"))) == 1, "orphaned manifests left behind"

    def test_tiers_are_pruned_independently(self, env):
        """A daily run must never delete the monthly backup — the whole point of
        grandfather-father-son is that the tiers have different lifetimes."""
        env["BACKUP_RETAIN_DAILY"] = "1"
        run("backup_mongo.sh", "--tier", "monthly", env=env)
        subprocess.run(["sleep", "1.05"], check=True)
        for _ in range(3):
            run("backup_mongo.sh", "--tier", "daily", env=env)
            subprocess.run(["sleep", "1.05"], check=True)

        assert len(artifacts(env["BACKUP_ROOT"], tier="daily")) == 1
        assert len(artifacts(env["BACKUP_ROOT"], tier="monthly")) == 1

    def test_pruner_never_touches_a_file_it_did_not_create(self, env):
        """An operator's hand-made `mongo-preupgrade-KEEP...` must be invisible
        to retention. Same rule as PH2.6's log pruner."""
        env["BACKUP_RETAIN_DAILY"] = "1"
        run("backup_mongo.sh", "--tier", "daily", env=env)
        d = Path(env["BACKUP_ROOT"]) / "mongo" / "daily"
        keeper = d / "KEEP-before-the-v2-migration.archive.gz.enc"
        keeper.write_bytes(b"operator's own copy")

        for _ in range(3):
            subprocess.run(["sleep", "1.05"], check=True)
            run("backup_mongo.sh", "--tier", "daily", env=env)

        assert keeper.exists(), "retention deleted a file it did not create"

    def test_invalid_retention_count_refuses_to_prune(self, env):
        """A typo'd BACKUP_RETAIN_DAILY must never evaluate to 'keep zero'."""
        run("backup_mongo.sh", "--tier", "daily", env=env)
        subprocess.run(["sleep", "1.05"], check=True)
        env["BACKUP_RETAIN_DAILY"] = "0"
        proc = run("backup_mongo.sh", "--tier", "daily", env=env, expect_rc=1)
        assert "retention count must be >= 1" in proc.stderr
        assert len(artifacts(env["BACKUP_ROOT"])) == 2, "backups were deleted despite an invalid count"

    def test_no_prune_flag_keeps_everything(self, env):
        env["BACKUP_RETAIN_DAILY"] = "1"
        for _ in range(3):
            run("backup_mongo.sh", "--tier", "daily", "--no-prune", env=env)
            subprocess.run(["sleep", "1.05"], check=True)
        assert len(artifacts(env["BACKUP_ROOT"])) == 3


# --------------------------------------------------------------------------- #
# Restore guards                                                                #
# --------------------------------------------------------------------------- #
class TestRestoreGuards:
    def test_restore_refuses_an_artifact_with_no_manifest(self, env):
        run("backup_mongo.sh", "--tier", "daily", env=env)
        artifact = artifacts(env["BACKUP_ROOT"])[0]
        next(artifact.parent.glob("*.manifest.json")).unlink()

        proc = run("restore_mongo.sh", str(artifact), env=env, expect_rc=1)
        assert "no manifest" in proc.stderr

    def test_restore_verifies_before_writing_anything(self, env):
        """The unrecoverable ordering mistake is to drop a live collection and
        then discover the archive is corrupt."""
        run("backup_mongo.sh", "--tier", "daily", env=env)
        artifact = artifacts(env["BACKUP_ROOT"])[0]
        artifact.write_bytes(b"this is not a backup")

        proc = run("restore_mongo.sh", str(artifact), env=env, expect_rc=1)
        assert "NOTHING was restored" in proc.stderr
        assert "mongorestore" not in Path(env["STUB_CALL_LOG"]).read_text()

    def test_restore_into_a_populated_database_requires_confirmation(self, env):
        run("backup_mongo.sh", "--tier", "daily", env=env)
        artifact = artifacts(env["BACKUP_ROOT"])[0]
        env["STUB_TARGET_STATE"] = "12 4000"

        proc = run("restore_mongo.sh", str(artifact), "--drop", env=env, expect_rc=1)
        assert "requires an interactive terminal" in proc.stderr
        assert "mongorestore" not in Path(env["STUB_CALL_LOG"]).read_text()

    def test_restore_into_an_empty_database_needs_no_confirmation(self, env):
        """The disaster-recovery case is where a prompt is most obstructive and
        least useful: there is nothing there to lose."""
        run("backup_mongo.sh", "--tier", "daily", env=env)
        artifact = artifacts(env["BACKUP_ROOT"])[0]
        env["STUB_TARGET_STATE"] = "0 0"
        env["STUB_COMPARE_REPORT"] = "MATCH alpha 2 2"

        run("restore_mongo.sh", str(artifact), env=env)
        assert "mongorestore" in Path(env["STUB_CALL_LOG"]).read_text()

    def test_restore_remaps_namespaces_for_a_different_target_database(self, env):
        run("backup_mongo.sh", "--tier", "daily", env=env)
        artifact = artifacts(env["BACKUP_ROOT"])[0]
        env["STUB_COMPARE_REPORT"] = "MATCH alpha 2 2"

        run("restore_mongo.sh", str(artifact), "--target-db", "scratch", "--yes", env=env)
        calls = Path(env["STUB_CALL_LOG"]).read_text()
        assert "--nsFrom=testdb.*" in calls and "--nsTo=scratch.*" in calls

    def test_a_missing_collection_after_restore_is_a_failure(self, env):
        """mongorestore exits 0 on a restore that moved nothing."""
        run("backup_mongo.sh", "--tier", "daily", env=env)
        artifact = artifacts(env["BACKUP_ROOT"])[0]
        env["STUB_COMPARE_REPORT"] = "MISSING alpha 2 -"

        proc = run("restore_mongo.sh", str(artifact), "--yes", env=env, expect_rc=1)
        assert "verification FAILED" in proc.stderr

    def test_default_restore_is_a_merge_not_a_replace(self, env):
        run("backup_mongo.sh", "--tier", "daily", env=env)
        artifact = artifacts(env["BACKUP_ROOT"])[0]
        env["STUB_COMPARE_REPORT"] = "MATCH alpha 2 2"

        run("restore_mongo.sh", str(artifact), env=env)
        assert "--drop" not in Path(env["STUB_CALL_LOG"]).read_text()


# --------------------------------------------------------------------------- #
# Configuration & upload backups                                                #
# --------------------------------------------------------------------------- #
class TestConfigBackup:
    @pytest.fixture
    def cfgroot(self, env):
        root = Path(env["BACKUP_CONFIG_SOURCE_ROOT"])
        (root / "secrets").mkdir()
        (root / "secrets" / "jwt_secret").write_text("s3cret-value")
        (root / "secrets" / "README.md").write_text("docs, tracked in git")
        (root / "secrets" / "example.env.example").write_text("TEMPLATE=1")
        (root / ".env").write_text("MONGO_URL=mongodb://x\n")
        return root

    def test_config_backup_is_always_encrypted(self, env, cfgroot):
        del env["BACKUP_ENCRYPTION_PASSPHRASE_FILE"]
        proc = run("backup_config.sh", env=env, expect_rc=1)
        assert "refusing to write a configuration backup without encryption" in proc.stderr
        assert not (Path(env["BACKUP_ROOT"]) / "config").exists()

    def test_config_backup_round_trips_and_excludes_tracked_files(self, tmp_path, env, cfgroot):
        run("backup_config.sh", env=env)
        artifact = next((Path(env["BACKUP_ROOT"]) / "config").glob("*.tar.gz.enc"))

        plain = tmp_path / "cfg.tar.gz"
        decrypt(artifact, plain)
        listing = subprocess.run(["tar", "-tzf", str(plain)], capture_output=True, text=True, check=True).stdout

        assert "secrets/jwt_secret" in listing
        assert ".env" in listing
        assert "README.md" not in listing, "a git-tracked file was archived"
        assert ".example" not in listing, "a template file was archived"

    def test_config_manifest_lists_names_but_never_contents(self, env, cfgroot):
        run("backup_config.sh", env=env)
        artifact = next((Path(env["BACKUP_ROOT"]) / "config").glob("*.tar.gz.enc"))
        m = manifest_of(artifact)
        assert "secrets/jwt_secret" in m["files"]
        assert "s3cret-value" not in json.dumps(m), "the manifest leaked a secret value"


class TestUploadsBackup:
    def test_uploads_round_trip(self, tmp_path, env):
        src = tmp_path / "uploads"
        (src / "a").mkdir(parents=True)
        (src / "a" / "avatar.png").write_bytes(b"\x89PNG-bytes")

        run("backup_uploads.sh", "--path", str(src), env=env)
        artifact = next((Path(env["BACKUP_ROOT"]) / "uploads").glob("*.tar.gz.enc"))
        assert manifest_of(artifact)["file_count"] == 1

        dest = tmp_path / "restored"
        dest.mkdir()
        run("backup_uploads.sh", "--restore", str(artifact), "--path", str(dest), "--yes", env=env)
        assert (dest / "a" / "avatar.png").read_bytes() == b"\x89PNG-bytes"

    def test_an_empty_upload_store_is_not_an_error(self, tmp_path, env):
        """Uploads are declared but not yet mounted (docker-compose.yml). A
        nightly job that fails every night for a component that does not exist
        yet is a job whose failures stop being read."""
        empty = tmp_path / "empty-uploads"
        empty.mkdir()
        proc = run("backup_uploads.sh", "--path", str(empty), env=env)
        assert "0 files" in proc.stderr
        assert "ALERT" in proc.stderr


# --------------------------------------------------------------------------- #
# Configuration loading                                                         #
# --------------------------------------------------------------------------- #
class TestEnvFileLoading:
    """`source .env` is the obvious implementation and it is an RCE primitive.

    These two tests pin the parser's contract; without them a future
    "simplification" back to `source` would pass every other test in this file.
    """

    def test_env_file_is_parsed_not_executed(self, tmp_path, env):
        marker = tmp_path / "PWNED"
        env_file = tmp_path / "repo.env"
        env_file.write_text(f'EVIL=$(touch "{marker}")\nBACKUP_RETAIN_DAILY=4\n')

        proc = subprocess.run(
            ["bash", "-c",
             f'set -euo pipefail; . "{BACKUP_DIR}/lib.sh"; bk_load_env_file "{env_file}"; '
             'printf "%s\\n" "${BACKUP_RETAIN_DAILY:-unset}"'],
            env=env, capture_output=True, text=True, check=True,
        )
        assert not marker.exists(), "the .env reader executed a command substitution"
        assert proc.stdout.strip() == "4"

    def test_explicit_environment_beats_the_env_file(self, tmp_path, env):
        env_file = tmp_path / "repo.env"
        env_file.write_text("BACKUP_RETAIN_DAILY=99\n")
        env["BACKUP_RETAIN_DAILY"] = "3"

        proc = subprocess.run(
            ["bash", "-c",
             f'set -euo pipefail; . "{BACKUP_DIR}/lib.sh"; bk_load_env_file "{env_file}"; '
             'printf "%s\\n" "${BACKUP_RETAIN_DAILY}"'],
            env=env, capture_output=True, text=True, check=True,
        )
        assert proc.stdout.strip() == "3"
