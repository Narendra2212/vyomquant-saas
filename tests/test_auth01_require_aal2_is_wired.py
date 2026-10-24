"""AUTH-01 regression: `require_aal2` is actually wired to credential mutation.

THE DEFECT
----------
`backend_app/core/dependencies.py::require_aal2` was implemented, documented and tested
-- and applied to **zero endpoints**. A repository-wide search for `Depends(require_aal2)`
returned nothing, and the dependency's own code comment said so outright:

    "`require_aal2` is still applied to zero endpoints, so nothing reads this value yet."

Its docstring names the operations it "MUST be applied to", the first being "Exchange API
key mutation (create/delete)". `POST /api/exchanges/keys` -- the endpoint that stores the
credentials which reach a venue holding the customer's money -- depended only on
`get_current_user`. An attacker with a stolen password-only session could attach their own
exchange credentials, or rotate the victim's to keys they control, with no second factor
in the path.

A security function that is never called is not a security control. That is the whole
finding, and this repository had five of them (see `staging/FINAL_FULL_SYSTEM_AUDIT.md`).

THE FIX
-------
`backend_app/routers/exchange.py`: `store_keys` and `delete_connection` now depend on
`require_aal2` instead of `get_current_user`. Deliberately NOT applied to the read or
connection-test endpoints, per the dependency's own "DO NOT apply globally" instruction.

WHY THESE TESTS BUILD A REAL APP
--------------------------------
The brief requires the real dependency to be exercised rather than `require_aal2()` being
unit-tested in isolation, and that distinction is the entire point of this finding: the
function always behaved correctly in isolation, which is exactly why its being unwired
went unnoticed. So the tests below mount the actual route functions on a real FastAPI app
with the real dependency in the chain, override only `get_current_user` (the token
decoder), and drive it over HTTP with a TestClient. The AAL1/AAL2 decision is made by the
production `require_aal2`, not by a stub.

`auth.mfa_factors` does not exist on the local test database (only `auth.users` does -- it
is a Supabase-hosted table), so enrollment state cannot be queried or asserted here. That
is why the rule enforced is the flat one the docstring specifies -- "this operation
requires aal2" -- rather than a conditional step-up that could not be tested.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

fastapi = pytest.importorskip("fastapi")
from fastapi import Depends, FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend_app.core.dependencies import (  # noqa: E402
    get_current_user,
    require_aal2,
)

# ── identities ───────────────────────────────────────────────────────────────

AAL2_USER = {
    "id": "11111111-1111-4111-8111-111111111111",
    "email": "aal2@example.invalid",
    "app_metadata": {"aal": "aal2"},
}
AAL1_USER = {
    "id": "22222222-2222-4222-8222-222222222222",
    "email": "aal1@example.invalid",
    "app_metadata": {"aal": "aal1"},
}
#: No `aal` claim at all. `require_aal2` defaults to "aal1", i.e. fails closed.
NO_AAL_USER = {
    "id": "33333333-3333-4333-8333-333333333333",
    "email": "noaal@example.invalid",
    "app_metadata": {},
}
#: `aal` present as a TOP-LEVEL claim rather than inside app_metadata. The dependency
#: reads `app_metadata.aal` first and falls back to `user["aal"]` -- this pins that
#: fallback, which was itself a real bug once (the claim used to be dropped entirely,
#: which would have made require_aal2 reject everyone who HAD completed MFA).
TOP_LEVEL_AAL2_USER = {
    "id": "44444444-4444-4444-8444-444444444444",
    "email": "toplevel@example.invalid",
    "app_metadata": {},
    "aal": "aal2",
}


def _app_with(user_factory):
    """A real app exposing one AAL2-gated route and one ungated route.

    `require_aal2` sits in the dependency chain exactly as it does in production; only
    `get_current_user` -- the token decoder -- is overridden, because there is no real
    Supabase JWT to present in a unit test.
    """
    app = FastAPI()

    @app.post("/gated")
    async def gated(u: dict = Depends(require_aal2)):
        return {"ok": True, "user": u["id"]}

    @app.get("/ungated")
    async def ungated(u: dict = Depends(get_current_user)):
        return {"ok": True, "user": u["id"]}

    app.dependency_overrides[get_current_user] = user_factory
    return app


# ── 1. the dependency's decision, through a real request ─────────────────────


def test_aal2_session_is_allowed():
    client = TestClient(_app_with(lambda: AAL2_USER))
    r = client.post("/gated")
    assert r.status_code == 200, r.text
    assert r.json()["user"] == AAL2_USER["id"]


def test_aal1_session_is_denied_with_403():
    client = TestClient(_app_with(lambda: AAL1_USER))
    r = client.post("/gated")
    assert r.status_code == 403, r.text
    assert "two-factor" in r.json()["detail"].lower()


def test_missing_aal_claim_fails_closed():
    """No claim must be treated as aal1, not as "unknown therefore allow"."""
    client = TestClient(_app_with(lambda: NO_AAL_USER))
    assert client.post("/gated").status_code == 403


def test_top_level_aal_claim_is_honoured():
    """The fallback path. If this breaks, users who DID complete MFA get locked out."""
    client = TestClient(_app_with(lambda: TOP_LEVEL_AAL2_USER))
    assert client.post("/gated").status_code == 200


def test_no_session_is_denied():
    """`require_aal2` must not be reachable without authentication."""
    app = FastAPI()

    @app.post("/gated")
    async def gated(u: dict = Depends(require_aal2)):
        return {"ok": True}

    def _no_session():
        raise HTTPException(status_code=401, detail="Invalid or expired token.")

    app.dependency_overrides[get_current_user] = _no_session
    assert TestClient(app).post("/gated").status_code == 401


def test_expired_session_is_denied():
    app = FastAPI()

    @app.post("/gated")
    async def gated(u: dict = Depends(require_aal2)):
        return {"ok": True}

    def _expired():
        raise HTTPException(status_code=401, detail="Token expired")

    app.dependency_overrides[get_current_user] = _expired
    assert TestClient(app).post("/gated").status_code == 401


def test_an_ungated_route_still_works_at_aal1():
    """The control must be scoped. Read endpoints must not start requiring MFA."""
    client = TestClient(_app_with(lambda: AAL1_USER))
    assert client.get("/ungated").status_code == 200


def test_aal2_does_not_substitute_for_identity():
    """Completing MFA must not change WHICH user the request acts as."""
    client = TestClient(_app_with(lambda: AAL2_USER))
    assert client.post("/gated").json()["user"] == AAL2_USER["id"]


# ── 2. the control is wired to the endpoints its docstring names ─────────────
#
# This is the half that would have caught AUTH-01. The tests above all passed before the
# fix, because `require_aal2` was always correct in isolation.

#: (module, handler name) -> must the handler require AAL2?
#: Derived from `require_aal2`'s docstring: "Exchange API key mutation (create/delete)".
EXPECTED_AAL2_HANDLERS = [
    ("backend_app.routers.exchange", "store_keys"),
    ("backend_app.routers.exchange", "delete_connection"),
    # SEC-15: the contract's fourth bullet, "Billing/payment mutations". Only the two
    # that move money TOWARD the platform are gated; see
    # tests/test_sec15_billing_mutations_require_aal2.py for why cancellation is not,
    # and why that asymmetry is the design rather than an omission.
    ("backend_app.routers.billing", "resume_subscription"),
    ("backend_app.routers.billing", "delete_payment_method"),
]

#: Handlers that must NOT require AAL2 -- the docstring's "DO NOT apply globally" rule.
#: `/test` submits credentials but persists nothing; the rest are reads.
EXPECTED_UNGATED_HANDLERS = [
    ("backend_app.routers.exchange", "get_supported_exchanges"),
    ("backend_app.routers.exchange", "list_exchanges"),
    ("backend_app.routers.exchange", "test_connection"),
    ("backend_app.routers.exchange", "test_stored_connection"),
    ("backend_app.routers.exchange", "get_exchange_capabilities"),
    # SEC-15, and this one is a MUTATION listed here deliberately: gating cancellation
    # would stop a customer without an enrolled factor from ending their own billing.
    # That is a consumer-protection harm rather than a hardening. The reasoning, and the
    # single condition that retires it, are in the route's own docstring.
    ("backend_app.routers.billing", "cancel_subscription"),
]


def _dependency_callables(func):
    """Every dependency callable in a handler's signature defaults."""
    import inspect

    found = []
    for param in inspect.signature(func).parameters.values():
        default = param.default
        if default is inspect.Parameter.empty:
            continue
        dep = getattr(default, "dependency", None)
        if dep is not None:
            found.append(dep)
    return found


@pytest.mark.parametrize("module_path,handler_name", EXPECTED_AAL2_HANDLERS)
def test_handler_requires_aal2(module_path, handler_name):
    """THE assertion for AUTH-01: the control is reachable from the endpoint."""
    import importlib

    module = importlib.import_module(module_path)
    handler = getattr(module, handler_name)
    deps = _dependency_callables(handler)

    assert require_aal2 in deps, (
        f"{module_path}.{handler_name} does not depend on require_aal2. Its own "
        f"docstring names this operation as one that MUST require AAL2. A security "
        f"function that is never called is not a security control. This is AUTH-01."
    )
    assert get_current_user not in deps, (
        f"{module_path}.{handler_name} depends on BOTH require_aal2 and "
        f"get_current_user. require_aal2 already depends on get_current_user; having "
        f"both means one parameter is the un-stepped-up user and a future edit could "
        f"read the wrong one."
    )


@pytest.mark.parametrize("module_path,handler_name", EXPECTED_UNGATED_HANDLERS)
def test_read_and_test_handlers_do_not_require_aal2(module_path, handler_name):
    """`DO NOT apply globally` -- asserted, so the fix cannot creep outward."""
    import importlib

    module = importlib.import_module(module_path)
    handler = getattr(module, handler_name)
    assert require_aal2 not in _dependency_callables(handler), (
        f"{module_path}.{handler_name} now requires AAL2. The dependency's docstring "
        f"says non-sensitive read endpoints should NOT require MFA; this would force "
        f"MFA to browse exchanges."
    )


def test_require_aal2_is_applied_somewhere_at_all():
    """The single assertion that fails if the control is ever unwired again.

    Scans the router package source rather than one known handler, so deleting the
    dependency from `store_keys` specifically is caught by the test above, and deleting
    it everywhere is caught here.
    """
    import pathlib

    routers = pathlib.Path("backend_app/routers")
    assert routers.is_dir(), f"expected {routers} to exist"

    hits = [
        p.name
        for p in routers.glob("*.py")
        if "require_aal2" in p.read_text(encoding="utf-8", errors="replace")
    ]
    assert hits, (
        "No file in backend_app/routers references require_aal2. The MFA control is "
        "implemented but applied to nothing -- the exact state AUTH-01 described."
    )
