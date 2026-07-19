"""Centralized password policy and hashing primitives (PH1.5).

Single source of truth for how StockAssist AI validates, hashes, and verifies
passwords. No password may be hashed, verified, or strength-checked outside
this module — that is what guarantees one consistent policy across
registration, login, dev seeding, and every future credential flow (password
change, reset, admin creation), instead of per-endpoint rules that drift
apart.

Design decisions (see PRODUCTION_HARDENING.md finding H10 / risk R-05 and
PRODUCTION_ROADMAP.md PH1.5):

* **Policy applies to NEW passwords only.** ``validate_new_password`` guards
  registration (and any future password-change flow). Login never re-validates
  policy — existing users with pre-PH1.5 passwords keep logging in unchanged.
* **Explicit bcrypt cost.** ``BCRYPT_ROUNDS = 12`` is pinned in code rather
  than inherited silently from the library default, so a bcrypt upgrade can
  never weaken hashing without a reviewed diff.
* **Verification never raises.** ``verify_password`` returns ``False`` for
  empty/malformed stored hashes (OAuth-native accounts store
  ``password_hash: ""``) instead of letting ``bcrypt.checkpw`` raise a
  ``ValueError`` that surfaces as a 500 — a password login against an
  OAuth-only account is an ordinary generic 401.
* **Timing-equalized failures.** When there is no usable stored hash (unknown
  email, OAuth-only account), ``verify_password`` still performs one bcrypt
  comparison against a fixed dummy hash so response timing cannot distinguish
  "email not registered" from "wrong password" (user-enumeration defense).
* **Whitespace normalization.** Leading/trailing whitespace is stripped before
  validation *and* hashing (an invisible copy-paste artifact, not entropy).
  Interior whitespace is preserved — passphrases are welcome. No unicode
  normalization is applied: the user must type the same codepoints at login,
  and silently transforming secrets is more surprising than helpful.
* **Bounded length.** Bcrypt silently truncates input at 72 bytes; accepting
  longer passwords would make distinct inputs verify as equal. We cap at
  ``MAX_LENGTH`` characters and ``MAX_BYTES`` UTF-8 bytes (multibyte input can
  exceed 72 bytes well under 64 characters). Also bounds hashing cost.
* **No new dependencies.** Common-password rejection uses a bundled, curated
  blocklist (``data/common_passwords.txt``) instead of pulling in a strength
  library; heuristic rules (sequences, repeats, identity-derived passwords)
  are implemented directly and unit-tested.
* **Passwords are never logged.** Nothing in this module logs, stores, or
  echoes a password or its fragments; validation messages name the violated
  rule only.
"""
from __future__ import annotations

import string
from functools import lru_cache
from pathlib import Path

import bcrypt

# --------------------------------------------------------------------------- #
# Policy constants (one place, no magic numbers at call sites).                #
# --------------------------------------------------------------------------- #
MIN_LENGTH = 12                 # SECURITY.md: minimum 12 characters
MAX_LENGTH = 64                 # NIST-friendly upper bound (chars)
MAX_BYTES = 72                  # bcrypt truncation boundary (UTF-8 bytes)
BCRYPT_ROUNDS = 12              # explicit cost factor — never library-implicit
MIN_UNIQUE_CHARS = 5            # repeated-character rejection threshold
SEQUENCE_RUN = 4                # sequential-run rejection window ("abcd", "1234")
IDENTITY_TOKEN_MIN = 5          # min length of a name/email token to match inside

# Keyboard rows, alphabet, and digits — checked forward and reversed.
_SEQUENCES = (
    "abcdefghijklmnopqrstuvwxyz",
    "0123456789",
    "qwertyuiop",
    "asdfghjkl",
    "zxcvbnm",
)

# Fixed, well-formed bcrypt hash (cost = BCRYPT_ROUNDS) of a random secret that
# was never retained. Used purely as a timing pad when no real stored hash is
# available, so a failed lookup costs the same as a failed comparison.
_DUMMY_HASH = "$2b$12$4370suRdNJX.pY9g.oBaruGRi2mldnKMjhIErzDMIgkJHuXoP6TDm"

_COMMON_PASSWORDS_FILE = Path(__file__).parent / "data" / "common_passwords.txt"

# Human-readable rule messages. They name the violated rule and never echo the
# submitted password (generic-by-design; see SECURITY_ARCHITECTURE.md §15).
MSG_TOO_SHORT = f"Password must be at least {MIN_LENGTH} characters."
MSG_TOO_LONG = f"Password must be at most {MAX_LENGTH} characters."
MSG_NO_UPPER = "Password must contain an uppercase letter."
MSG_NO_LOWER = "Password must contain a lowercase letter."
MSG_NO_DIGIT = "Password must contain a number."
MSG_NO_SPECIAL = "Password must contain a special character."
MSG_COMMON = "Password is too common. Choose a less predictable password."
MSG_CONTAINS_EMAIL = "Password must not contain your email address."
MSG_CONTAINS_NAME = "Password must not contain your name."
MSG_REPEATED = "Password must use a wider variety of characters."
MSG_SEQUENTIAL = "Password must not contain sequential characters like 'abcd' or '1234'."


# --------------------------------------------------------------------------- #
# Normalization                                                                #
# --------------------------------------------------------------------------- #
def normalize_password(raw: str) -> str:
    """Strip leading/trailing whitespace (including tabs/newlines).

    Applied before both validation and hashing at registration so the two can
    never disagree. Interior whitespace is preserved. Login is untouched —
    existing hashes were made from raw input and continue to verify raw input.
    """
    return raw.strip()


# --------------------------------------------------------------------------- #
# Blocklist loading                                                            #
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _common_passwords() -> frozenset[str]:
    """The bundled common-password blocklist, loaded once, lowercased."""
    entries: set[str] = set()
    try:
        text = _COMMON_PASSWORDS_FILE.read_text(encoding="utf-8")
    except OSError:  # missing data file must fail open on the list only —
        return frozenset()  # every other rule still applies
    for line in text.splitlines():
        line = line.strip().lower()
        if line and not line.startswith("#"):
            entries.add(line)
    return frozenset(entries)


# --------------------------------------------------------------------------- #
# Individual rules (each returns a message or None)                            #
# --------------------------------------------------------------------------- #
def _check_length(password: str) -> str | None:
    if len(password) < MIN_LENGTH:
        return MSG_TOO_SHORT
    if len(password) > MAX_LENGTH or len(password.encode("utf-8")) > MAX_BYTES:
        return MSG_TOO_LONG
    return None


def _check_character_classes(password: str) -> list[str]:
    violations = []
    if not any(c.isupper() for c in password):
        violations.append(MSG_NO_UPPER)
    if not any(c.islower() for c in password):
        violations.append(MSG_NO_LOWER)
    if not any(c.isdigit() for c in password):
        violations.append(MSG_NO_DIGIT)
    if not any(not c.isalnum() for c in password):
        violations.append(MSG_NO_SPECIAL)
    return violations


def _check_common(password: str) -> str | None:
    lowered = password.lower()
    blocklist = _common_passwords()
    if lowered in blocklist:
        return MSG_COMMON
    # "Password1234!"-style padding: strip trailing digits/punctuation and
    # re-check, so list entries stay short and still catch padded variants.
    base = lowered.rstrip(string.digits + string.punctuation)
    if base and base != lowered and base in blocklist:
        return MSG_COMMON
    return None


def _check_identity(password: str, email: str, name: str) -> list[str]:
    lowered = password.lower()
    violations = []

    email_l = (email or "").strip().lower()
    local_part = email_l.split("@", 1)[0] if email_l else ""
    if email_l and (
        lowered == email_l
        or lowered == local_part
        or (len(local_part) >= IDENTITY_TOKEN_MIN and local_part in lowered)
    ):
        violations.append(MSG_CONTAINS_EMAIL)

    name_l = " ".join((name or "").lower().split())
    name_tokens = [t for t in name_l.split() if len(t) >= IDENTITY_TOKEN_MIN]
    if name_l and (
        lowered == name_l.replace(" ", "")
        or lowered == name_l
        or any(t in lowered for t in name_tokens)
    ):
        violations.append(MSG_CONTAINS_NAME)

    return violations


def _check_repeated(password: str) -> str | None:
    # Unique-character floor catches both "aaaaaaaaaA1!" and alternating
    # padding like "abababababA1!" that a max-run rule would miss. A genuine
    # password meeting all four character classes already has >= 4 distinct
    # characters, so this only rejects degenerate padding.
    if len(set(password)) < MIN_UNIQUE_CHARS:
        return MSG_REPEATED
    return None


def _check_sequential(password: str) -> str | None:
    lowered = password.lower()
    sequences = _SEQUENCES + tuple(s[::-1] for s in _SEQUENCES)
    for i in range(len(lowered) - SEQUENCE_RUN + 1):
        window = lowered[i:i + SEQUENCE_RUN]
        if any(window in seq for seq in sequences):
            return MSG_SEQUENTIAL
    return None


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #
def validate_new_password(password: str, *, email: str = "", name: str = "") -> list[str]:
    """Validate a NEW password against the production policy.

    Returns a list of human-readable violation messages (empty = valid), so
    callers can report every problem at once instead of one per attempt.
    ``email``/``name`` enable the identity-derived checks; pass them whenever
    they are known. The password is normalized (whitespace-stripped) before
    checking — callers that hash must hash the same normalized value.
    """
    candidate = normalize_password(password)
    violations: list[str] = []

    length_violation = _check_length(candidate)
    if length_violation:
        violations.append(length_violation)
    violations.extend(_check_character_classes(candidate))
    common_violation = _check_common(candidate)
    if common_violation:
        violations.append(common_violation)
    violations.extend(_check_identity(candidate, email, name))
    repeated_violation = _check_repeated(candidate)
    if repeated_violation:
        violations.append(repeated_violation)
    sequential_violation = _check_sequential(candidate)
    if sequential_violation:
        violations.append(sequential_violation)

    return violations


def hash_password(password: str) -> str:
    """Bcrypt-hash a password at the pinned cost factor.

    Hashing does not validate policy — callers gate NEW passwords through
    ``validate_new_password`` first. (Dev seeding and legacy flows may hash
    pre-policy secrets.)
    """
    return bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    ).decode("utf-8")


def verify_password(plain: str, hashed: str | None) -> bool:
    """Constant-time password verification that never raises.

    Empty or malformed stored hashes (OAuth-native accounts, corrupted data)
    return ``False`` after a dummy bcrypt comparison, so failures cost the
    same wall-clock time whether the account exists, has no password, or the
    password is simply wrong.
    """
    if not hashed or not isinstance(hashed, str):
        bcrypt.checkpw(plain.encode("utf-8"), _DUMMY_HASH.encode("utf-8"))
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False
