"""Deterministic process environment for the hermetic backend test suite (PH3.1).

WHY THIS FILE EXISTS
--------------------
`server.py` line 6 is ``load_dotenv(ROOT_DIR / '.env', override=True)``. That
line runs at *import* time, and `tests/conftest.py` imports `server`. So before
PH3.1, every "hermetic" test ran against **the developer's real `backend/.env`**
— real `ANTHROPIC_API_KEY`, real `GOOGLE_GEMINI_KEY`, real `KITE_API_SECRET`,
real `TWILIO_AUTH_TOKEN`, real `MONGO_URL`. Three consequences, all bad:

1. **Real credentials in a test process.** PH3.1 §8 forbids it outright, and it
   is the difference between a test that cannot place an order and a test that
   merely happens not to.
2. **Real spend and real side effects.** Measured on 2026-08-09, three tests in
   the default suite opened live TLS connections to `api.anthropic.com`,
   Google's Generative Language API, and Yahoo Finance — every single run.
3. **Non-determinism.** A test whose result depends on which keys the person
   running it happens to have configured is not a test, it is a survey. CI
   (no `.env` at all) and a developer laptop were running different suites
   under the same command.

WHAT IT DOES
------------
`apply()` installs a fixed, obviously-synthetic environment and sets
``PYTHON_DOTENV_DISABLED=1`` — python-dotenv's own supported kill switch
(`dotenv/main.py::_load_dotenv_disabled`), so `server.py`'s `load_dotenv` and
litellm's become no-ops rather than something we have to monkeypatch. The
result is that `pytest` sees the *same* configuration on every machine.

Values are **overwritten, not defaulted**. `setdefault` would let a real
`ANTHROPIC_API_KEY` exported in the developer's shell leak straight past the
guard, which is the exact hole this closes.

DESIGN NOTES
------------
* ``APP_ENV=testing`` — a first-class environment in `security/secrets.py`
  (§`TESTING`), deliberately distinct from `development` so the configuration
  report says "testing" instead of masquerading as someone's laptop.
* Every third-party credential is the **empty string**, which every
  `*_configured()` check in the backend reads as "not configured". Routes then
  take their deterministic offline fallback, which is what a hermetic test
  should be exercising anyway.
* Secrets that the app itself must be able to *use* (JWT, CSRF, recovery,
  broker encryption, webhook auth) get fixed synthetic values. They are long
  enough to satisfy the real validators — a 19-byte JWT secret was previously
  making PyJWT emit `InsecureKeyLengthWarning` on every run.
* ``MONGO_URL``/``DB_NAME`` point at an unmistakably-named test database. No
  hermetic test connects (the `fake_db` fixture swaps `server.db` for
  `FakeDB`), but if one ever does, it must not be able to find real data.
* ``REDIS_URL`` is **removed**, not blanked: `infrastructure/redis` treats an
  absent variable as "Redis not configured" and a present-but-empty one as a
  malformed URL.

Individual tests remain free to override any of this with `monkeypatch.setenv`
— that is how the security suites exercise production-shaped configuration.
"""
import os

# A valid 44-character urlsafe-base64 Fernet key (see services/brokers/crypto.py).
# Decodes to the ASCII bytes b"stockassist-ph31-test-fernet-key", so it is
# self-evidently synthetic to anyone who base64-decodes it out of curiosity.
_TEST_FERNET_KEY = "c3RvY2thc3Npc3QtcGgzMS10ZXN0LWZlcm5ldC1rZXk="

#: Variables forced to a fixed value for every test process.
TEST_ENV = {
    # --- application configuration ------------------------------------- #
    "APP_ENV": "testing",
    "APP_VERSION": "0.0.0-test",
    "VCS_REF": "0000000000000000000000000000000000000000",
    "BUILD_DATE": "1970-01-01T00:00:00Z",
    "FRONTEND_URL": "http://localhost:3000",
    # The heartbeat/AI background engine must never start under pytest: it
    # schedules real market fan-outs on a timer that outlives the test.
    "DISABLE_BACKGROUND_ENGINE": "1",

    # --- datastores ------------------------------------------------------ #
    # Hermetic tests never connect (see the `fake_db` fixture). The name is
    # chosen so that an accidental connection lands somewhere harmless and
    # obviously wrong rather than in the development database.
    "MONGO_URL": "mongodb://127.0.0.1:27017",
    "DB_NAME": "stockassist_pytest",

    # --- secrets the application itself uses ----------------------------- #
    # Synthetic, fixed, and long enough for the real validators.
    "JWT_SECRET": "ph31-test-jwt-secret-not-a-real-secret-000000",
    "CSRF_SECRET": "ph31-test-csrf-secret-not-a-real-secret-00000",
    "RECOVERY_SECRET": "ph31-test-recovery-secret-not-a-real-secret-0",
    "BROKER_TOKEN_KEY": _TEST_FERNET_KEY,
    "WEBHOOK_API_KEY": "ph31-test-webhook-key",

    # --- third-party credentials: deliberately unconfigured -------------- #
    # Empty string == "not configured" to every `*_configured()` check in the
    # backend, so routes take their offline fallback and no request leaves the
    # machine. Anything added to backend/.env.example belongs here too.
    "ANTHROPIC_API_KEY": "",
    "GOOGLE_GEMINI_KEY": "",
    "GOOGLE_CLIENT_ID": "",
    "GOOGLE_CLIENT_SECRET": "",
    "ALPHA_VANTAGE_KEY": "",
    "KITE_API_KEY": "",
    "KITE_API_SECRET": "",
    "KITE_REDIRECT_URL": "",
    "UPSTOX_API_KEY": "",
    "UPSTOX_API_SECRET": "",
    "UPSTOX_REDIRECT_URL": "",
    "TWILIO_ACCOUNT_SID": "",
    "TWILIO_AUTH_TOKEN": "",
    "TWILIO_WHATSAPP_FROM": "",
    "USER_WHATSAPP_TO": "",
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_CHAT_ID": "",
    "SENDGRID_API_KEY": "",
    "SMTP_HOST": "",
    "SMTP_PORT": "",
    "SMTP_USER": "",
    "SMTP_PASSWORD": "",
    "EMAIL_FROM": "no-reply@test.invalid",
    "EMAIL_FROM_NAME": "StockAssist AI (test)",
}

#: Variables *removed* from the environment. Absent and empty are not the same
#: thing to these readers, and absent is the state we want.
TEST_ENV_UNSET = (
    "REDIS_URL",
    # Cookie/CORS behaviour is derived from APP_ENV unless explicitly forced.
    # Leaving a developer's override in place would silently change which
    # branch the cookie and CORS suites exercise.
    "COOKIE_SECURE",
    "COOKIE_DOMAIN",
    "COOKIE_SAMESITE",
    "CORS_ORIGINS",
)


def apply(environ=None):
    """Install the deterministic test environment. Idempotent.

    Must be called *before* `server` is imported, because `server.py` reads
    configuration at module scope.
    """
    env = os.environ if environ is None else environ

    # python-dotenv's supported kill switch. Set first: it has to be in place
    # before any `load_dotenv()` anywhere in the import graph runs, otherwise
    # backend/.env wins by `override=True`.
    env["PYTHON_DOTENV_DISABLED"] = "1"

    for key, value in TEST_ENV.items():
        env[key] = value
    for key in TEST_ENV_UNSET:
        env.pop(key, None)
