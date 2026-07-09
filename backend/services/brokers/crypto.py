"""Encryption for broker tokens at rest.

Broker access/refresh tokens are never stored in plaintext (SECURITY.md /
BROKER_INTEGRATION.md). Values are encrypted with Fernet (AES-128-CBC + HMAC).

Key resolution order:
1. BROKER_TOKEN_KEY env var — a Fernet key (urlsafe base64, 32 bytes). Preferred
   in production so the key can be rotated independently of JWT_SECRET.
2. Derived from JWT_SECRET via SHA-256 — deterministic fallback so development
   environments work without extra configuration.
"""
import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_fernet = None

ENC_PREFIX = "enc:v1:"


def _build_fernet() -> Fernet:
    key = os.environ.get("BROKER_TOKEN_KEY", "").strip()
    if key:
        try:
            return Fernet(key.encode())
        except Exception:
            logger.error("BROKER_TOKEN_KEY is not a valid Fernet key; falling back to derived key.")
    secret = os.environ.get("JWT_SECRET", "")
    if not secret:
        # Fail secure: refuse to silently encrypt with a well-known key.
        raise RuntimeError("Cannot encrypt broker tokens: set BROKER_TOKEN_KEY or JWT_SECRET")
    derived = hashlib.sha256(f"broker-token-key:{secret}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = _build_fernet()
    return _fernet


def encrypt_token(value: str) -> str:
    """Encrypt a token for storage. Empty/None values pass through as ""."""
    if not value:
        return ""
    return ENC_PREFIX + _get_fernet().encrypt(value.encode()).decode()


def decrypt_token(value: str) -> str:
    """Decrypt a stored token.

    Legacy plaintext values (written before Sprint 7) are returned as-is so
    existing connections keep working; the engine re-encrypts them on the next
    write (see BrokerEngine._save_account).
    """
    if not value:
        return ""
    if not value.startswith(ENC_PREFIX):
        return value  # legacy plaintext — migrated on next save
    try:
        return _get_fernet().decrypt(value[len(ENC_PREFIX):].encode()).decode()
    except (InvalidToken, Exception):
        logger.error("Failed to decrypt broker token (key changed?). Reconnect required.")
        return ""


def is_encrypted(value: str) -> bool:
    return bool(value) and value.startswith(ENC_PREFIX)
