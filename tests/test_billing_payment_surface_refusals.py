# -*- coding: utf-8 -*-
"""The Payment methods surface answers honestly instead of 500-ing or fabricating.

WHAT WAS WRONG
--------------
The billing page's "Payment methods" panel was Stripe-shaped on a platform that bills through
**Razorpay**, and all three of its couplings were dead:

1. ``POST /api/billing/portal`` opened with ``_validate_keys("stripe")``, which raises
   ``HTTPException(500, "Stripe is not configured: STRIPE_SECRET_KEY is not set.")`` when there
   is no live ``sk_live_`` secret. The panel's one action called it, so the button answered 500
   on every press. Razorpay has no hosted customer portal, so there was never anything for it
   to open.
2. ``POST /api/billing/payment-methods`` required ``brand``/``last4``/``expiry_month``/
   ``expiry_year`` "from the Stripe PaymentMethod response". There is no Stripe.js anywhere in
   ``algo22-terminal``, so no caller had one - and when those values *were* supplied they were
   stored verbatim with nothing verifying them against any gateway.
3. ``GET /api/billing/payment-methods`` reads the table only (2) could fill, so it is always
   empty. That read is left exactly as it was: ``[]`` for "nothing stored" is honest.

WHAT IS ASSERTED HERE
---------------------
That each mutating route now refuses with a **stable machine-readable code** and a sentence
that names its own reason, never a 500 and never an invented URL or card - and that the two
refusals which have different causes are told apart, because "this provider has no portal"
(permanent, unfixable) and "this deployment configured no provider" (an environment variable)
need opposite responses from whoever reads them.

That the Stripe branch is **still reached and still works** when Stripe is genuinely the
configured provider: the change is additive, not a removal, and that is asserted by driving
the branch with the Stripe SDK's own entry points patched, so it is proved without a network
call and without a real credential.

THE CREDENTIALS BELOW ARE NOT CREDENTIALS
-----------------------------------------
``rzp_live_notarealkeyid`` / ``sk_live_notarealsecret`` are literals shaped to satisfy
``_validate_keys``' live-prefix rule and nothing else. They authenticate against nothing; no
network call is made with either (the one test that reaches the Stripe SDK patches the three
calls it would make). They are set with ``monkeypatch``, so no environment outlives a test.

WHY THE RATE LIMITER IS RESET PER TEST
--------------------------------------
``POST /portal`` is ``@limiter.limit("5/minute")`` and this run's limiter is in-memory keyed on
the caller's address, which for every ``TestClient`` in the suite is the same literal
``"testclient"`` (this Starlette version's ``TestClient`` takes no ``client=`` override, so the
key cannot be varied instead). Another test file's calls would otherwise spend this file's
budget and turn a refusal assertion into a 429. The reset isolates the budget; it does not
remove the limit, and the limit itself is asserted elsewhere
(``test_billing_e2e.py::test_portal_is_rate_limited``).

Validates: the money-path honesty rule - never fabricate, and never offer an action that
cannot succeed.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend_app.core.dependencies import get_current_user
from backend_app.core.rate_limit import limiter
from backend_app.main import app

#: Shaped to pass ``_validate_keys``' live-prefix check and to authenticate against nothing.
_FAKE_RAZORPAY_KEY_ID = "rzp_live_notarealkeyid"
_FAKE_RAZORPAY_SECRET = "notarealrazorpaysecret"
_FAKE_STRIPE_SECRET = "sk_live_notarealsecret"

_USER: Dict[str, Any] = {
    "id": "b7c1e4d2-3a8f-4e61-9d0b-5f2c8a1e7d43",
    "email": "payment-surface@example.com",
    "username": "payment-surface",
    "role": "authenticated",
    "access_token": "token-payment-surface",
}

#: A body that satisfies ``AddPaymentMethodRequest`` completely, so the refusal under test is
#: reached rather than FastAPI's own 422. The card attributes are the point: this is exactly the
#: shape the deleted write would have stored verbatim, unverified by any gateway.
_WELL_FORMED_ADD_BODY: Dict[str, Any] = {
    "payment_method_id": "pm_notarealpaymentmethod",
    "set_as_default": True,
    "brand": "visa",
    "last4": "4242",
    "expiry_month": 12,
    "expiry_year": 2031,
}


@pytest.fixture
def client() -> Iterator[TestClient]:
    """The REAL mounted app, with only the authenticated identity overridden.

    ``raise_server_exceptions=False`` so a crash is observable as the 500 response the
    platform's own handler renders - without it, "never 500" would be unfalsifiable, because
    every crash would surface as a test error instead.

    ``get_request_supabase`` is deliberately NOT overridden: none of the three handlers under
    test depends on it. ``get_db`` is not overridden either, so the DELETE case below is decided
    by a real ``payment_methods`` lookup rather than by a double that was told what to answer.
    """
    limiter.reset()
    app.dependency_overrides[get_current_user] = lambda: _USER
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        limiter.reset()


@pytest.fixture
def razorpay_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The live configuration: Razorpay credentials present, Stripe absent."""
    monkeypatch.setenv("RAZORPAY_KEY_ID", _FAKE_RAZORPAY_KEY_ID)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", _FAKE_RAZORPAY_SECRET)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)


@pytest.fixture
def no_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither provider configured - a deployment fault, not a product fact."""
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)


def _refusal(response: Any) -> Dict[str, Any]:
    """The decoded body of a refusal, with the two things every refusal owes checked.

    A stable ``error`` code (so a client can branch without parsing prose) and a non-empty
    ``message`` (so a person is told the reason). The router raises
    ``detail={"error": …, "message": …}``; ``main.py``'s handler flattens that to the top level
    and keeps ``detail`` as well, and BOTH spellings are checked, because the frontend reads
    ``err.data.detail.message`` and a future client may read the flattened pair.
    """
    body = response.json()
    assert isinstance(body, dict), f"a refusal body decoded to {type(body).__name__}: {body!r}"
    code = body.get("error")
    assert isinstance(code, str) and code.strip(), (
        f"no stable machine-readable error code in {body!r}"
    )
    message = body.get("message")
    assert isinstance(message, str) and message.strip(), f"no message in {body!r}"
    nested = body.get("detail")
    assert isinstance(nested, dict), (
        f"`detail` is not the coded object the frontend reads (`err.data.detail.message`): "
        f"{nested!r}"
    )
    assert nested.get("error") == code and nested.get("message") == message, (
        f"the flattened pair and `detail` disagree: {body!r}"
    )
    return body


def _assert_invents_nothing(response: Any) -> None:
    """No URL and no card attribute anywhere in a refusal body.

    The whole failure mode being fixed is a surface that would rather make something up than
    admit it cannot act, so the refusals are checked for the two things they must never carry:
    a link to send somebody to, and an instrument to show them.
    """
    text = response.text
    assert "http://" not in text and "https://" not in text, (
        f"a refusal carries a URL, which is the fabrication this fixes: {text[:500]!r}"
    )
    body = response.json()
    for forbidden in ("url", "last4", "brand", "expiry_month", "expiry_year", "payment_method"):
        assert forbidden not in body, (
            f"a refusal carries {forbidden!r}, which nothing verified: {body!r}"
        )


# ══════════════════════════════════════════════════════════════════════════
#  POST /api/billing/portal
# ══════════════════════════════════════════════════════════════════════════


def test_portal_refuses_with_a_named_reason_when_the_provider_has_no_portal(
    client: TestClient, razorpay_only: None
) -> None:
    """Razorpay configured: a coded 501 naming the provider, not the old 500 about Stripe."""
    response = client.post("/api/billing/portal", json={})

    assert response.status_code == 501, (
        f"expected a 501 refusal for a provider with no hosted portal, got "
        f"{response.status_code}: {response.text[:500]!r}"
    )
    body = _refusal(response)
    assert body["error"] == "PROVIDER_HAS_NO_BILLING_PORTAL"
    message = body["message"]
    # The reason has to be IN the message, not merely implied by the status code: this is the
    # sentence `Billing.jsx` renders into `ds/Alert` verbatim.
    assert "Razorpay" in message, f"the refusal does not name the provider: {message!r}"
    assert "portal" in message.lower()
    assert "checkout" in message.lower(), (
        f"the refusal does not say where the instrument is actually supplied: {message!r}"
    )
    # The old failure named a provider this platform does not bill through.
    assert "Stripe" not in message, f"the refusal still talks about Stripe: {message!r}"
    _assert_invents_nothing(response)


def test_portal_separates_an_unconfigured_deployment_from_a_provider_without_one(
    client: TestClient, no_provider: None
) -> None:
    """No provider at all is a different fact with a different fix, so it gets its own code."""
    response = client.post("/api/billing/portal", json={})

    assert response.status_code != 500, (
        f"a missing provider configuration must not crash the route: {response.text[:500]!r}"
    )
    assert response.status_code == 503, (
        f"expected 503 for a deployment with no live provider credentials, got "
        f"{response.status_code}: {response.text[:500]!r}"
    )
    body = _refusal(response)
    assert body["error"] == "PAYMENT_PROVIDER_NOT_CONFIGURED"
    assert body["error"] != "PROVIDER_HAS_NO_BILLING_PORTAL", (
        "an operator fault and a permanent product limitation must not share one code"
    )
    _assert_invents_nothing(response)


def test_portal_still_opens_a_stripe_session_when_stripe_is_the_configured_provider(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Stripe branch is preserved: this is additive, not a removal.

    Driven through the Stripe SDK's own three entry points, patched - so the branch is proved
    to run without a network call and without a real credential. The URL asserted is the one
    the patched session returns; nothing here would pass if the handler invented its own.
    """
    monkeypatch.setenv("STRIPE_SECRET_KEY", _FAKE_STRIPE_SECRET)
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)

    customer = type("Customer", (), {"id": "cus_stub", "metadata": {"user_id": _USER["id"]}})()
    customers = type("CustomerList", (), {"data": [customer]})()
    session = type("PortalSession", (), {"url": "https://billing.stripe.test/session/stub"})()

    import stripe

    with (
        patch.object(stripe.Customer, "list", return_value=customers) as listed,
        patch.object(stripe.billing_portal.Session, "create", return_value=session) as created,
    ):
        response = client.post("/api/billing/portal", json={})

    assert response.status_code == 200, (
        f"the Stripe branch must still work when Stripe is genuinely configured, got "
        f"{response.status_code}: {response.text[:500]!r}"
    )
    assert response.json() == {"url": "https://billing.stripe.test/session/stub"}
    assert listed.called, "the Stripe customer lookup was skipped"
    assert created.called, "no Stripe billing-portal session was created"


# ══════════════════════════════════════════════════════════════════════════
#  POST /api/billing/payment-methods
# ══════════════════════════════════════════════════════════════════════════


def test_adding_a_payment_method_refuses_and_names_tokenisation_as_the_reason(
    client: TestClient, razorpay_only: None
) -> None:
    """A well-formed body no longer stores an unverified card; it gets a coded refusal."""
    response = client.post("/api/billing/payment-methods", json=_WELL_FORMED_ADD_BODY)

    assert response.status_code == 501, (
        f"expected a 501 refusal, got {response.status_code}: {response.text[:500]!r}"
    )
    body = _refusal(response)
    assert body["error"] == "PAYMENT_METHOD_STORAGE_UNSUPPORTED"
    message = body["message"]
    assert "tokenis" in message.lower() or "tokeniz" in message.lower(), (
        f"the refusal does not name tokenisation as what is missing: {message!r}"
    )
    assert "Stripe" not in message, (
        f"the refusal still blames the caller for lacking a Stripe response: {message!r}"
    )
    _assert_invents_nothing(response)


def test_the_refused_add_stored_nothing(client: TestClient, razorpay_only: None) -> None:
    """The read stays empty afterwards - a refusal that wrote a row would be the worst case."""
    before = client.get("/api/billing/payment-methods")
    assert before.status_code == 200, before.text[:500]

    client.post("/api/billing/payment-methods", json=_WELL_FORMED_ADD_BODY)

    after = client.get("/api/billing/payment-methods")
    assert after.status_code == 200, after.text[:500]
    assert after.json() == before.json(), (
        f"the refused write changed the stored set: {before.json()!r} -> {after.json()!r}"
    )
    assert all(
        entry.get("last4") != _WELL_FORMED_ADD_BODY["last4"] for entry in after.json()
    ), "the caller-supplied card digits reached storage"


def test_a_malformed_add_body_is_still_answered_by_validation_not_by_the_refusal(
    client: TestClient, razorpay_only: None
) -> None:
    """A malformed body and an unavailable capability are two facts; each keeps its own answer.

    ``AddPaymentMethodRequest`` is still declared on the handler, so FastAPI rejects a body
    missing a required field before the handler runs. Collapsing both into one 501 would tell a
    caller with a genuine schema error the wrong thing.
    """
    response = client.post(
        "/api/billing/payment-methods", json={"set_as_default": "not-a-boolean"}
    )

    assert response.status_code == 422, (
        f"a malformed body must still be a validation 422, got {response.status_code}: "
        f"{response.text[:500]!r}"
    )
    code = response.json().get("error")
    assert isinstance(code, str) and code.strip(), (
        f"the 422 carries no stable error code: {response.json()!r}"
    )
    assert code != "PAYMENT_METHOD_STORAGE_UNSUPPORTED", (
        "the capability refusal was returned for a schema error"
    )


# ══════════════════════════════════════════════════════════════════════════
#  DELETE /api/billing/payment-methods/{method_id}
# ══════════════════════════════════════════════════════════════════════════


def test_deleting_a_payment_method_that_cannot_exist_says_why_the_table_is_empty(
    client: TestClient, razorpay_only: None
) -> None:
    """Still a 404 against a real lookup, now with a code and the reason nothing is stored."""
    response = client.delete(
        "/api/billing/payment-methods/2f9b6c1e-4d7a-4c3b-8e5f-1a0d9b2c7e64"
    )

    assert response.status_code == 404, (
        f"expected 404 for an id that belongs to no row, got {response.status_code}: "
        f"{response.text[:500]!r}"
    )
    body = _refusal(response)
    assert body["error"] == "PAYMENT_METHOD_NOT_FOUND"
    assert "Razorpay" in body["message"] or "Checkout" in body["message"], (
        f"the 404 does not explain why nothing is stored: {body['message']!r}"
    )
    _assert_invents_nothing(response)


# ══════════════════════════════════════════════════════════════════════════
#  GET /api/billing/payment-methods - UNCHANGED, and asserted to be
# ══════════════════════════════════════════════════════════════════════════


def test_reading_payment_methods_is_an_honest_empty_list(
    client: TestClient, razorpay_only: None
) -> None:
    """``[]`` for "nothing stored" is honest, so the read is left alone - including its 200."""
    response = client.get("/api/billing/payment-methods")

    assert response.status_code == 200, response.text[:500]
    methods = response.json()
    assert isinstance(methods, list), f"the read no longer returns a list: {methods!r}"
    # Not asserted to be empty: the read is row-driven and a row from some other run would be
    # a legitimate answer. What IS asserted is that nothing is synthesised into it.
    for entry in methods:
        assert entry.get("id"), f"a payment-method row with no id: {entry!r}"
