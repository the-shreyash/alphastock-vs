"""Route-table introspection for the PH3.3 mechanical API sweeps.

WHY THE ROUTE LIST IS DERIVED, NOT WRITTEN DOWN
-----------------------------------------------
The authorization sweeps assert a property that must hold for *every* protected
endpoint: anonymous callers get 401, non-admins get 403 on the admin surface.
A hand-maintained list of 126 routes would be stale within a sprint — and the
route it was missing would be the newly-added one, which is precisely the route
most likely to have shipped without its dependency.

So the list is read from `server.app` itself, at collection time. Adding an
endpoint automatically adds its authorization test; forgetting the dependency
turns that test red. Nothing in `tests/` has to be edited to keep pace.

The classification is taken from the *dependency graph* rather than from the
path, because that is what actually enforces access: a route is "protected" iff
`get_current_user` appears somewhere in its resolved dependency tree, and
"admin" iff `require_admin` does. A route that merely looks administrative
because of its URL is not trusted to be one.
"""
from __future__ import annotations

from bson import ObjectId
from fastapi.routing import APIRoute

import server

#: HTTP verbs FastAPI adds implicitly; never interesting to an authz sweep.
_IMPLICIT_METHODS = frozenset({"HEAD", "OPTIONS"})

#: Deterministic, syntactically-valid stand-ins for path parameters. Every
#: identifier-shaped parameter gets a *valid* ObjectId so a 4xx in an authz test
#: can only mean "rejected by authorization", never "rejected by id parsing" —
#: malformed identifiers are the separate concern of test_api_validation.py.
_PATH_PARAM_SAMPLES = {
    "symbol": "RELIANCE",
    "broker": "zerodha",
}
_VALID_OID = str(ObjectId("64b7f0c2e13b4a5d6f8c9a01"))


def sample_path(path: str) -> str:
    """Substitute concrete values for every `{param}` placeholder in `path`."""
    out = path
    for name, value in _PATH_PARAM_SAMPLES.items():
        out = out.replace("{" + name + "}", value)
    while "{" in out:
        start = out.index("{")
        end = out.index("}", start)
        out = out[:start] + _VALID_OID + out[end + 1:]
    return out


def _dependency_names(route: APIRoute) -> set[str]:
    """Every callable in the route's fully-resolved dependency tree, by name."""
    names: set[str] = set()
    stack = [route.dependant]
    while stack:
        dependant = stack.pop()
        if dependant.call is not None:
            names.add(getattr(dependant.call, "__name__", str(dependant.call)))
        stack.extend(dependant.dependencies)
    return names


def _classify():
    protected, admin, public = [], [], []
    for route in server.app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api"):
            continue
        names = _dependency_names(route)
        for method in sorted((route.methods or set()) - _IMPLICIT_METHODS):
            entry = (method, route.path)
            if "require_admin" in names:
                admin.append(entry)
            elif "get_current_user" in names:
                protected.append(entry)
            else:
                public.append(entry)
    return sorted(protected), sorted(admin), sorted(public)


#: (method, path) for every route requiring an ordinary authenticated user.
USER_PROTECTED_ROUTES, ADMIN_ROUTES, PUBLIC_ROUTES = _classify()

#: Everything behind any credential check — the anonymous-rejection surface.
AUTHENTICATED_ROUTES = sorted(USER_PROTECTED_ROUTES + ADMIN_ROUTES)


def route_id(entry) -> str:
    """`GET /api/trades` → `GET-/api/trades`, for a readable pytest node id."""
    method, path = entry
    return f"{method}-{path}"
