"""Centralized role taxonomy & assignment authorization (PH1.12 / finding F-1).

The single source of truth for *which* roles exist and *who* may grant them.

Three guarantees for any write to ``users.role``:

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

3. **Least privilege on the target (D6.8 / F-2).** Guarantee 2 checks the role
   being *written*, never the account it is written *to*. A plain ``admin``
   could therefore demote a ``super_admin`` to ``pro``, overwrite their role
   through ``grant-plan``, or block them — removing the only accounts that can
   delete users or mint admins, from below. Every admin action that modifies
   another account now passes :func:`authorize_admin_target`: an admin-tier
   account may be modified only by a ``super_admin``.

Plan / entitlement roles (``free``/``pro``/``elite``/…) are grantable by any
admin — that is ordinary account administration, not privilege elevation, and
``grant-plan`` validates against :data:`PLAN_ROLES` itself rather than a copy.

A plan role is **data, not an entitlement check**. As of D6.8 no server-side
code path grants or refuses a feature on a plan role, and ``plan_expires_at``
is never read. See ``.claude/TASK.md`` (D6.8 §1) before relying on one.

This module is deliberately framework-thin: it raises ``fastapi.HTTPException``
so call sites in ``server.py`` get a clean 4xx with no translation boilerplate,
mirroring ``security.identifiers`` and ``security.cookies``.
"""
from __future__ import annotations

from fastapi import HTTPException

#: The default role every account starts with.
USER_ROLE = "user"

#: Subscription / entitlement tiers. Grantable by any admin (account admin).
#: ``grant-plan`` validates against this set directly (D6.8) — it used to keep
#: its own copy, which had already drifted (it omitted ``premium``).
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
    "authorize_admin_target",
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


def authorize_admin_target(target_role: str, actor_role: str) -> None:
    """Authorize an admin action that modifies an account whose role is ``target_role``.

    Args:
        target_role: The role *currently stored* on the account being modified —
            read from the database, never from the request.
        actor_role: The role of the authenticated actor.

    Raises:
        HTTPException: ``403 Forbidden`` if the target holds an admin-tier role
            and the actor is not ``super_admin``.

    Compares against the stored role rather than the requested one on purpose:
    :func:`validate_role_assignment` already governs what may be written, and
    what this closes is *who may be written to*. The two checks answer different
    questions, so a route that modifies another account's role needs both.
    """
    if is_admin_tier(target_role) and actor_role != SUPER_ADMIN_ROLE:
        raise HTTPException(status_code=403,
                            detail="Only super_admin can modify an admin-tier account")
