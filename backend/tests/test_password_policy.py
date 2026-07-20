"""PH1.5 — Password Policy & Account Protection tests.

Asserts the centralized password policy (security/passwords.py): validation
rules for new passwords, explicit bcrypt cost, safe verification (never
raises, timing-padded), registration enforcement at the model layer (422),
and the sprint's compatibility guarantees — legacy users keep logging in,
OAuth-native accounts fail password login with a generic 401 (not a 500),
and the pre-existing brute-force lockout is preserved unchanged.

Hermetic: runs in-process against the FakeDB, no live server or Mongo.
"""
import logging

import bcrypt
import pytest

import server
from security import passwords as pw
from security.passwords import (
    hash_password,
    normalize_password,
    validate_new_password,
    verify_password,
)

STRONG = "Horizon!Mint7Quartz"  # meets every rule, used across tests


# --------------------------------------------------------------------------- #
# Policy rules (pure unit tests)                                               #
# --------------------------------------------------------------------------- #
class TestPolicyRules:
    def test_strong_password_accepted(self):
        assert validate_new_password(STRONG) == []

    def test_min_length_boundary(self):
        assert pw.MSG_TOO_SHORT in validate_new_password("Sh0rt!Pw901")   # 11
        assert validate_new_password("Sh0rt!Pw9012") == []                # 12

    def test_max_length_boundary(self):
        base = "Aa1!"
        ok = base + "x" * 60          # 64 chars
        too_long = base + "x" * 61    # 65 chars
        assert validate_new_password(ok) == []
        assert pw.MSG_TOO_LONG in validate_new_password(too_long)

    def test_multibyte_over_72_bytes_rejected(self):
        # 30 chars but 4 bytes each: 96 bytes < 64 chars — byte cap must fire.
        candidate = "Aa1!" + "𠜎" * 26
        assert len(candidate) <= 64
        assert len(candidate.encode("utf-8")) > 72
        assert pw.MSG_TOO_LONG in validate_new_password(candidate)

    def test_missing_uppercase(self):
        assert pw.MSG_NO_UPPER in validate_new_password("horizon!mint7qz")

    def test_missing_lowercase(self):
        assert pw.MSG_NO_LOWER in validate_new_password("HORIZON!MINT7QZ")

    def test_missing_digit(self):
        assert pw.MSG_NO_DIGIT in validate_new_password("Horizon!MintQz")

    def test_missing_special(self):
        assert pw.MSG_NO_SPECIAL in validate_new_password("HorizonMint7Qz")

    def test_multiple_violations_reported_together(self):
        violations = validate_new_password("short")
        assert len(violations) >= 3  # length + upper + digit + special

    def test_common_password_rejected(self):
        # Meets every character-class rule, still a known-common credential.
        assert pw.MSG_COMMON in validate_new_password("Password@1234")

    def test_common_password_with_trailing_padding_rejected(self):
        # "monkey" is a list entry; digits/punctuation padding must not save it.
        assert pw.MSG_COMMON in validate_new_password("Monkey987654!!")

    def test_common_list_loads_lowercase_and_is_cached(self):
        first = pw._common_passwords()
        assert "password" in first
        assert all(e == e.lower() for e in first)
        assert pw._common_passwords() is first  # lru_cache identity

    def test_email_as_password_rejected(self):
        email = "trader.one@example.com"
        assert pw.MSG_CONTAINS_EMAIL in validate_new_password(
            "Trader.one@example.com1!", email=email)          # full email inside
        assert pw.MSG_CONTAINS_EMAIL in validate_new_password(
            "Trader.one99!Zz", email=email)                   # local part inside
        assert validate_new_password(STRONG, email=email) == []

    def test_name_as_password_rejected(self):
        assert pw.MSG_CONTAINS_NAME in validate_new_password(
            "Shreyash77!Qz", name="Shreyash Yadav")
        # Short (<5 char) name tokens must not false-positive on containment.
        assert validate_new_password("Grand!Lee7Stone", name="Lee Ray") == []

    def test_repeated_characters_rejected(self):
        assert pw.MSG_REPEATED in validate_new_password("aaaaaaaaaA1!")
        assert pw.MSG_REPEATED in validate_new_password("aBaBaBaBaB1!")
        assert pw.MSG_REPEATED not in validate_new_password("Mississippi1!")

    def test_sequential_characters_rejected(self):
        assert pw.MSG_SEQUENTIAL in validate_new_password("Abcdefgh!77Zk")   # alphabet
        assert pw.MSG_SEQUENTIAL in validate_new_password("Xk!m1234Vbqz9")   # digits
        assert pw.MSG_SEQUENTIAL in validate_new_password("Xk!m4321Vbqz9")   # reversed
        assert pw.MSG_SEQUENTIAL in validate_new_password("Qwertyz!77Km1")   # keyboard row

    def test_sequential_rule_no_false_positives(self):
        # Real suite passwords and 3-char runs must pass.
        assert pw.MSG_SEQUENTIAL not in validate_new_password("TestPass123!x")
        assert pw.MSG_SEQUENTIAL not in validate_new_password("S3cure!Passw0rd")

    def test_whitespace_normalization(self):
        assert normalize_password("  Horizon!Mint7Quartz\n") == STRONG
        assert validate_new_password("  " + STRONG + "  ") == []
        # Interior whitespace is preserved and counts as a special character.
        assert validate_new_password("Mint horizon 7Q sky") == []
        assert pw.MSG_TOO_SHORT in validate_new_password("   \t\n  ")


# --------------------------------------------------------------------------- #
# Hashing primitives                                                           #
# --------------------------------------------------------------------------- #
class TestHashingPrimitives:
    def test_hash_uses_explicit_cost_12(self):
        assert hash_password(STRONG).startswith("$2b$12$")

    def test_round_trip(self):
        hashed = hash_password(STRONG)
        assert verify_password(STRONG, hashed) is True
        assert verify_password("Wrong!Guess99x", hashed) is False

    @pytest.mark.parametrize("bad_hash", [None, "", "not-a-bcrypt-hash"])
    def test_verify_never_raises_on_bad_stored_hash(self, bad_hash):
        assert verify_password(STRONG, bad_hash) is False

    def test_dummy_hash_is_wellformed_cost_12(self):
        assert pw._DUMMY_HASH.startswith("$2b$12$")
        # checkpw must run without error (this is the timing pad).
        assert bcrypt.checkpw(b"anything", pw._DUMMY_HASH.encode()) is False

    def test_server_delegates_to_security_module(self):
        assert server.hash_password is hash_password
        assert server.verify_password is verify_password


# --------------------------------------------------------------------------- #
# Registration endpoint enforcement                                            #
# --------------------------------------------------------------------------- #
class TestRegisterEndpoint:
    def _register(self, client, password, email="newuser@example.com", name="New User"):
        return client.post("/api/auth/register",
                           json={"name": name, "email": email, "password": password})

    def test_weak_password_rejected_422_no_user_created(self, client, fake_db):
        r = self._register(client, "weak")
        assert r.status_code == 422
        assert len(fake_db.users.docs) == 0

    def test_422_detail_names_rules_never_echoes_password(self, client, fake_db):
        secret = "shortpw"
        r = self._register(client, secret)
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert isinstance(detail, list)
        msgs = " ".join(e["msg"] for e in detail)
        assert "12 characters" in msgs
        assert secret not in r.text

    def test_strong_password_registers_with_unchanged_contract(self, client, fake_db):
        r = self._register(client, STRONG)
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"id", "name", "email", "role", "capital", "token"}
        assert "password_hash" not in body

    def test_stored_hash_is_bcrypt_cost_12_not_plaintext(self, client, fake_db):
        self._register(client, STRONG)
        stored = fake_db.users.docs[0]["password_hash"]
        assert stored.startswith("$2b$12$")
        assert STRONG not in stored

    def test_padded_password_normalized_before_hashing(self, client, fake_db):
        r = self._register(client, "  " + STRONG + "  ")
        assert r.status_code == 200
        r = client.post("/api/auth/login",
                        json={"email": "newuser@example.com", "password": STRONG})
        assert r.status_code == 200

    def test_email_derived_password_rejected_end_to_end(self, client, fake_db):
        r = self._register(client, "Newuser55!Qzt", email="newuser@example.com")
        assert r.status_code == 422
        assert len(fake_db.users.docs) == 0

    def test_login_model_not_policed(self, client, fake_db):
        # UserLogin has no policy validator: a legacy-weak credential must reach
        # the credential check (401 here — no such user), never a 422.
        r = client.post("/api/auth/login", json={"email": "x@example.com", "password": "x"})
        assert r.status_code == 401


# --------------------------------------------------------------------------- #
# Login compatibility + account protection (sprint guarantees)                 #
# --------------------------------------------------------------------------- #
class TestLoginCompatibility:
    LEGACY_EMAIL = "legacy@example.com"
    LEGACY_PASSWORD = "weak"  # pre-PH1.5 credential — must keep working

    @pytest.fixture
    def legacy_user(self, fake_db):
        from bson import ObjectId
        doc = {
            "_id": ObjectId(),
            "name": "Legacy User",
            "email": self.LEGACY_EMAIL,
            "password_hash": hash_password(self.LEGACY_PASSWORD),
            "role": "user",
        }
        fake_db.users.docs.append(doc)
        return doc

    @pytest.fixture
    def oauth_user(self, fake_db):
        from bson import ObjectId
        doc = {
            "_id": ObjectId(),
            "name": "OAuth Native",
            "email": "oauth@example.com",
            "password_hash": "",  # exactly how the Google flow stores them
            "auth_provider": "google",
            "google_sub": "sub-123",
            "role": "user",
        }
        fake_db.users.docs.append(doc)
        return doc

    def test_legacy_weak_password_user_still_logs_in(self, client, legacy_user):
        r = client.post("/api/auth/login",
                        json={"email": self.LEGACY_EMAIL, "password": self.LEGACY_PASSWORD})
        assert r.status_code == 200
        assert r.json()["email"] == self.LEGACY_EMAIL

    def test_oauth_native_password_login_generic_401_not_500(self, client, oauth_user):
        r = client.post("/api/auth/login",
                        json={"email": "oauth@example.com", "password": "AnyGuess!123x"})
        assert r.status_code == 401
        assert r.json()["detail"] == "Invalid email or password"

    def test_unknown_email_and_wrong_password_indistinguishable(self, client, legacy_user):
        r_unknown = client.post("/api/auth/login",
                                json={"email": "ghost@example.com", "password": "AnyGuess!123x"})
        r_wrong = client.post("/api/auth/login",
                              json={"email": self.LEGACY_EMAIL, "password": "AnyGuess!123x"})
        assert r_unknown.status_code == r_wrong.status_code == 401
        assert r_unknown.json()["detail"] == r_wrong.json()["detail"]

    def test_lockout_after_five_failures_preserved(self, client, fake_db, legacy_user):
        for _ in range(5):
            r = client.post("/api/auth/login",
                            json={"email": self.LEGACY_EMAIL, "password": "Wrong!Guess9x"})
            assert r.status_code == 401
        r = client.post("/api/auth/login",
                        json={"email": self.LEGACY_EMAIL, "password": self.LEGACY_PASSWORD})
        assert r.status_code == 429
        assert "Too many attempts" in r.json()["detail"]

    def test_failures_against_oauth_account_count_toward_lockout(self, client, fake_db, oauth_user):
        for _ in range(5):
            client.post("/api/auth/login",
                        json={"email": "oauth@example.com", "password": "AnyGuess!123x"})
        attempts = fake_db.login_attempts.docs
        assert attempts and attempts[0]["count"] == 5

    def test_successful_login_clears_attempt_record(self, client, fake_db, legacy_user):
        for _ in range(3):
            client.post("/api/auth/login",
                        json={"email": self.LEGACY_EMAIL, "password": "Wrong!Guess9x"})
        assert fake_db.login_attempts.docs
        r = client.post("/api/auth/login",
                        json={"email": self.LEGACY_EMAIL, "password": self.LEGACY_PASSWORD})
        assert r.status_code == 200
        assert not fake_db.login_attempts.docs


# --------------------------------------------------------------------------- #
# Hash exposure + logging hygiene                                              #
# --------------------------------------------------------------------------- #
class TestNoLeaks:
    def test_password_hash_never_in_auth_responses(self, client, fake_db):
        r = client.post("/api/auth/register", json={
            "name": "Leak Check", "email": "leak@example.com", "password": STRONG})
        assert "password_hash" not in r.json()
        r = client.post("/api/auth/login",
                        json={"email": "leak@example.com", "password": STRONG})
        assert "password_hash" not in r.json()
        token = r.json()["token"]
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert "password_hash" not in r.json()

    def test_password_never_logged(self, client, fake_db, caplog):
        with caplog.at_level(logging.DEBUG):
            client.post("/api/auth/register", json={
                "name": "Log Check", "email": "logcheck@example.com", "password": STRONG})
            client.post("/api/auth/login",
                        json={"email": "logcheck@example.com", "password": "Wrong!Guess9x"})
        joined = " ".join(rec.getMessage() for rec in caplog.records)
        assert STRONG not in joined
        assert "Wrong!Guess9x" not in joined
