"""SEC-15 -- MFA on billing mutations, decided by which direction the money moves.

THE FINDING
    ``require_aal2``'s own security contract (``core/dependencies.py``) lists four
    surfaces the dependency "MUST be applied to". Three were wired (exchange API key
    mutation by AUTH-01, admin endpoints, password change). The fourth,
    "Billing/payment mutations", was wired to NOTHING.

THE PRODUCT OWNER'S EARLIER HOLD, AND WHAT IT DID AND DID NOT COVER
    The hold was reconfirmed twice, the second time with a specific rationale: Razorpay's
    own checkout-time security. The ledger records, correctly, that this "genuinely covers
    create_checkout_session/verify_razorpay_payment but does not extend to
    cancel_subscription/resume_subscription/delete_payment_method, which remain ungated
    regardless of payment aggregator."

WHY ALL FIVE WERE NOT SIMPLY GATED
    Because ``require_aal2`` is a HARD 403 at ``aal1``, and MFA enrolment is not required
    to hold an account. AUTH-01 already states that consequence deliberately for
    exchange-key mutation. Applied to ``cancel_subscription`` it would mean a paying
    customer who never enrolled a factor CANNOT STOP THEIR OWN BILLING through the
    product. That is a consumer-protection harm, and a dark pattern, worse in kind than
    the risk it mitigates -- an attacker with a stolen password cancelling a subscription,
    which takes effect only at period end and costs the victim nothing directly.

    So the rule is the direction of the money, not the word "billing":

        starts or resumes billing  ->  gated, or covered by the Razorpay rationale
        stops billing              ->  never gated

        create_checkout_session   starts    accepted on the owner's Razorpay rationale
        verify_razorpay_payment   confirms  accepted on the owner's Razorpay rationale
        resume_subscription       RESUMES   GATED  <- an attacker here costs real money
        delete_payment_method     destroys  GATED  <- zero customer impact; no writer
        cancel_subscription       STOPS     UNGATED, deliberately and asserted below

WHAT THIS FILE PROVES
    That the two routes are gated, that cancellation CANNOT be gated by a later
    well-intentioned change without this test failing and making someone read the
    reasoning, and that the gate is the real dependency rather than a look-alike.
    No network, no Supabase, no payment provider: the routes are inspected through
    FastAPI's own dependency graph.
"""
from __future__ import annotations

import inspect

import pytest
from fastapi import Depends
from fastapi.routing import APIRoute

from backend_app.core.dependencies import get_current_user, require_aal2

#: (handler name, must it require aal2?). One table, so the policy is readable in one place.
BILLING_MUTATIONS = [
    ("resume_subscription", True),
    ("delete_payment_method", True),
    ("cancel_subscription", False),
    ("create_checkout_session", False),
    ("verify_razorpay_payment", False),
]

GATED = [name for name, gated in BILLING_MUTATIONS if gated]
UNGATED = [name for name, gated in BILLING_MUTATIONS if not gated]


def _handler(name: str):
    from backend_app.routers import billing

    fn = getattr(billing, name, None)
    assert fn is not None, f"billing.{name} no longer exists"
    return fn


def _dependency_callables(fn) -> set:
    """Every dependency callable in the handler's own signature.

    Reads ``inspect.signature`` defaults rather than FastAPI's resolved graph, because the
    question is what THIS handler declares -- a parent-router dependency would protect the
    route without the handler saying so, and SEC-15 is about the handler's own contract.
    """
    found = set()
    for param in inspect.signature(fn).parameters.values():
        default = param.default
        if isinstance(default, type(Depends(lambda: None))):
            if default.dependency is not None:
                found.add(default.dependency)
    return found


# ══════════════════════════════════════════════════════════════════════════════
#  1. The two routes that must be gated
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("name", GATED)
def test_the_route_requires_aal2(name):
    assert require_aal2 in _dependency_callables(_handler(name)), (
        f"billing.{name} no longer requires AAL2. require_aal2's contract names "
        f"'Billing/payment mutations', and this route moves money toward the platform, "
        f"so a session holding only a stolen password must not reach it."
    )


@pytest.mark.parametrize("name", GATED)
def test_the_gate_replaces_get_current_user_rather_than_sitting_beside_it(name):
    """``require_aal2`` itself depends on ``get_current_user``.

    Declaring both would authenticate twice and, worse, invites a later edit that keeps
    the plain one and drops the gate while the signature still looks protected -- which is
    how AUTH-01 describes this dependency having been applied to ZERO endpoints while
    existing.
    """
    deps = _dependency_callables(_handler(name))
    assert get_current_user not in deps, (
        f"billing.{name} declares both require_aal2 and get_current_user; the gate "
        f"already depends on the latter"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  2. Cancellation must stay reachable -- the test that protects customers
# ══════════════════════════════════════════════════════════════════════════════


def test_cancel_subscription_is_not_gated_so_a_customer_can_always_stop_billing():
    """THE LOAD-BEARING ONE, and it asserts the ABSENCE of a gate on purpose.

    If this ever fails, do not "fix" it by deleting the test. Read
    ``cancel_subscription``'s docstring first: gating it locks a paying customer who never
    enrolled a factor out of stopping their own charges, which is a worse outcome than the
    griefing it would prevent. The one condition that retires this reasoning is MFA
    enrolment becoming mandatory before first payment -- at which point no paying account
    can be at aal1 and the lockout cannot happen.
    """
    deps = _dependency_callables(_handler("cancel_subscription"))
    assert require_aal2 not in deps, (
        "cancel_subscription is now gated on AAL2. A customer without an enrolled factor "
        "can no longer stop their own billing through the product and must contact "
        "support to stop being charged. See the route's docstring: this is a "
        "consumer-protection harm, not a hardening. If MFA enrolment is now mandatory "
        "before first payment, that reasoning has expired -- say so there and here."
    )
    assert get_current_user in deps, (
        "cancel_subscription must still be AUTHENTICATED. Ungated means 'no second "
        "factor required', never 'no identity required'."
    )


def test_cancellation_is_still_rate_limited():
    """The compensating control for leaving it ungated.

    Griefing by repeated cancel/resume is bounded by the limiter rather than by MFA.
    """
    # The limiter is applied as a decorator ABOVE the def, so read the decorator block
    # rather than the function source.
    fn = _handler("cancel_subscription")
    module_src = inspect.getsource(inspect.getmodule(fn))
    decorator_block = module_src.split("async def cancel_subscription")[0][-400:]

    assert "limiter.limit" in decorator_block, (
        "cancel_subscription lost its rate limit. With no AAL2 gate, the limiter is the "
        "only thing bounding cancel/resume griefing."
    )


# ══════════════════════════════════════════════════════════════════════════════
#  3. The checkout pair, accepted on the owner's recorded rationale
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("name", ["create_checkout_session", "verify_razorpay_payment"])
def test_the_checkout_pair_is_recorded_as_accepted_not_forgotten(name):
    """These two are ungated on an explicit product decision, not by oversight.

    Pinned so the distinction survives: the ledger records the owner's rationale as
    Razorpay's own checkout-time security, which reaches exactly these two routes because
    they are the only ones that hand the customer to a payment page.
    """
    deps = _dependency_callables(_handler(name))
    assert get_current_user in deps or require_aal2 in deps, (
        f"billing.{name} authenticates nobody; whatever the MFA decision, identity is "
        f"not optional"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  4. The contract is still the thing being satisfied
# ══════════════════════════════════════════════════════════════════════════════


def test_the_security_contract_still_names_billing_mutations():
    """If the contract's fourth bullet is ever removed, this file's premise is gone and
    the whole policy above should be revisited rather than silently kept."""
    contract = inspect.getdoc(require_aal2) or ""
    assert "Billing/payment mutations" in contract, (
        "require_aal2's contract no longer names billing mutations. SEC-15 exists because "
        "it did; if that changed, re-derive the policy instead of inheriting this one."
    )


def test_require_aal2_still_fails_closed_on_a_missing_claim():
    """The gate is only worth applying if it refuses by default.

    Pinned here too because SEC-15's value rests on it entirely: SC-3 records a period
    when `aal` was dropped before reaching this function, which made it answer 403 for
    everyone -- and the mirror-image bug would make it answer 200 for everyone.
    """
    source = inspect.getsource(require_aal2)
    assert '"aal1"' in source, (
        "the default for a missing aal claim is gone; it must default to aal1 (deny), "
        "never to aal2"
    )
    assert "aal2" in source and "403" in source or "HTTP_403_FORBIDDEN" in source


def test_every_billing_mutation_in_the_table_actually_exists():
    """Guards against the table drifting away from the router it describes."""
    for name, _ in BILLING_MUTATIONS:
        _handler(name)


def test_the_routes_are_registered_and_reachable():
    """A dependency on an unregistered handler protects nothing."""
    from backend_app.routers import billing

    paths = {
        r.endpoint.__name__
        for r in billing.router.routes
        if isinstance(r, APIRoute)
    }
    for name, _ in BILLING_MUTATIONS:
        assert name in paths, f"billing.{name} is not registered on the router"
