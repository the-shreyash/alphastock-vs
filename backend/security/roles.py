"""Centralized role taxonomy & assignment authorization (PH1.12 / finding F-1).

The single source of truth for *which* roles exist and *who* may grant them.

Two guarantees for any value written to ``users.role``:

1. **Allowlist.** A role must be one of :data:`ASSIGNABLE_ROLES`. An arbitrary
   string can never be persisted as a role — this defends against typos and
   against an attacker probing the admin user-editor with privileged-looking
   values.

2. **Least privilege on elevation.** The admin-tier roles (``admin``,
   ``super_admin``) may be granted **only** by a ``super_admin``. A plain
   ``admin`` editing a user cannot promote anyone — including themselves — to an
   admin-tier role. This closes the privilege-escalation path in the admin user
   editor (``PUT /api/admin/users/{id}``), where ``role`` was previously an
   unchecked passthrough field.

Plan / entitlement roles (``free``/``pro``/``elite``/…) are grantable by any
admin — that is ordinary account administration, not privilege elevation, and it
matches the existing ``grant-plan`` endpoint's allowlist.

This module is deliberately framework-thin: it raises ``fastapi.HTTPException``
so call sites in ``server.py`` get a clean 4xx with no translation boilerplate,
mirroring ``security.identifiers`` and ``security.cookies``.
"""
from __future__ import annotations

from fastapi import HTTPException

#: The default role every account starts with.
USER_ROLE = "user"

#: Subscription / entitlement tiers. Grantable by any admin (account admin).
#: Kept in sync with the ``grant-plan`` endpoint's ``valid_plans`` allowlist.
PLAN_ROLES = frozenset({
    "free", "pro", "premium", "elite",
    "lifetime", "developer", "investor", "beta_tester",
})

#: Privileged control-plane roles. Grantable ONLY by a super_admin.
ADMIN_TIER_ROLES = frozenset({"admin", "super_admin"})

#: Every value that may legitimately be written to ``users.role``.
ASSIGNABLE_ROLES = frozenset({USER_ROLE}) | PLAN_ROLES | ADMIN_TIER_ROLES

SUPER_ADMIN_ROLE = "super_admin"

__all__ = [
    "USER_ROLE",
    "SUPER_ADMIN_ROLE",
    "PLAN_ROLES",
    "ADMIN_TIER_ROLES",
    "ASSIGNABLE_ROLES",
    "is_admin_tier",
    "validate_role_assignment",
]


def is_admin_tier(role: str) -> bool:
    """Return True if ``role`` is a privileged control-plane role."""
    return role in ADMIN_TIER_ROLES


def validate_role_assignment(new_role: str, actor_role: str) -> str:
    """Authorize assigning ``new_role`` by an actor whose role is ``actor_role``.

    Args:
        new_role: The role the actor is attempting to write to a user record.
        actor_role: The role of the authenticated actor performing the change.

    Returns:
        ``new_role`` unchanged, when the assignment is permitted (so the call
        can be used inline: ``update["role"] = validate_role_assignment(...)``).

    Raises:
        HTTPException: ``400 Bad Request`` if ``new_role`` is not on the
            allowlist; ``403 Forbidden`` if ``new_role`` is an admin-tier role
            and ``actor_role`` is not ``super_admin``.
    """
    if new_role not in ASSIGNABLE_ROLES:
        allowed = ", ".join(sorted(ASSIGNABLE_ROLES))
        raise HTTPException(status_code=400, detail=f"Invalid role. Choose from: {allowed}")
    if is_admin_tier(new_role) and actor_role != SUPER_ADMIN_ROLE:
        raise HTTPException(status_code=403, detail="Only super_admin can assign admin-tier roles")
    return new_role
