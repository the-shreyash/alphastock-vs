"""Broker account fixtures for D6.4's account-addressed engine.

WHY A HELPER AND NOT A LITERAL
------------------------------
`broker_account_id` is minted, not derived (see `services/brokers/accounts.py`),
so a test cannot write one down and expect the engine to agree. What a test *can*
do is decide the id itself and seed both sides with it — the `broker_accounts`
document the engine reads, and the `BrokerAccountRef` the test passes in — which
is what these helpers do.

:func:`fixture_account_id` is deterministic per `(user_id, broker, suffix)` so a
test that seeds a database in one helper and asserts a provider name in another
gets the same id without threading a value between them. That determinism is a
property of the *fixture*, never of production: `BrokerAccountDirectory.link`
mints randomly, and `tests/test_d64_identity.py` asserts it.
"""
from __future__ import annotations

import hashlib

from services.brokers.accounts import (
    BROKER_ACCOUNT_ID_PREFIX,
    BrokerAccountRef,
    BrokerAccountStatus,
)


def fixture_account_id(user_id: str, broker: str, suffix: str = "") -> str:
    """A stable, syntactically valid account id for a test fixture."""
    digest = hashlib.sha256(f"{user_id}|{broker}|{suffix}".encode()).hexdigest()
    return f"{BROKER_ACCOUNT_ID_PREFIX}{digest[:32]}"


def account_ref(user_id: str, broker: str, *, suffix: str = "",
                external_account_id: str = None,
                status: str = BrokerAccountStatus.CONNECTED,
                broker_account_id: str = None) -> BrokerAccountRef:
    """A resolved account, as the directory would have returned one."""
    return BrokerAccountRef(
        broker_account_id=broker_account_id or fixture_account_id(user_id, broker, suffix),
        user_id=str(user_id),
        broker=broker,
        external_account_id=external_account_id,
        external_identity_verified=bool(external_account_id),
        status=status,
    )


def account_doc(user_id: str, broker: str, *, suffix: str = "",
                external_account_id: str = "AB1234",
                status: str = BrokerAccountStatus.CONNECTED,
                **extra) -> dict:
    """A `broker_accounts` document matching :func:`account_ref`'s id.

    Seeded with the same id the ref carries, so an engine that loads the document
    and a test that passes the ref are talking about one account.
    """
    return {
        "broker_account_id": fixture_account_id(user_id, broker, suffix),
        "user_id": str(user_id),
        "broker": broker,
        "external_account_id": external_account_id,
        "external_identity_verified": bool(external_account_id),
        "status": status,
        "connected": status == BrokerAccountStatus.CONNECTED,
        **extra,
    }
