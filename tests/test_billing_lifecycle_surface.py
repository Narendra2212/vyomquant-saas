"""``core.billing_lifecycle`` advertises only what this product actually performs.

WHY THIS FILE EXISTS
    ``BillingLifecycle`` used to carry 12 classmethods. Ten of them had NO production
    caller: ``start_trial``, ``calculate_trial_end_date``, ``check_trial_expiry``,
    ``calculate_grace_period_end_date``, ``check_subscription_expiry``,
    ``activate_subscription``, ``upgrade_subscription``, ``downgrade_subscription``,
    ``renew_subscription`` and ``calculate_next_billing_date``. They were driven only by
    three audit test files, each against a Supabase mock, each asserting the method's own
    return dict (``act["status"] == "active"``) -- so they proved the library returned what
    it said it returned and nothing about what the product does.

    That mattered, because the class docstring advertised a trial, an upgrade path, a
    downgrade path, a renewal and a grace period, and a reader had every reason to believe
    a 14-day trial existed. It did not. Nothing ever wrote ``SubscriptionStatus.TRIAL`` or
    ``SubscriptionStatus.GRACE_PERIOD``.

    ``renew_subscription`` is the one that deserves naming individually, because it DID have
    a caller: ``billing.py:2883`` at the branch point -- inside ``stripe_webhook()``'s
    ``invoice.payment_succeeded`` branch, immediately after ``stripe.Subscription.retrieve``.
    Stripe recurred, so Stripe had renewals to handle. Razorpay orders are one-time, there
    is no ``invoice.payment_succeeded`` equivalent, and that handler is gone, so the caller
    went with the provider rather than being orphaned.

    ``calculate_next_billing_date`` was additionally a live money bug: it built the next
    period by substituting the month, so 31 January raised ``ValueError: day is out of range
    for month``. ``add_billing_period`` -- which IS called, by ``_record_plan_period`` -- is
    calendar-safe and is what survived.

WHAT THE PRODUCT ACTUALLY DOES, SO THE ABSENCES ABOVE ARE READ CORRECTLY
    * Activation: the Razorpay webhook calls ``_apply_billing_entitlement``, which writes
      ``profiles.subscription_tier``, and ``_record_plan_period``, which writes the period.
    * Renewal: by RE-PURCHASE. Buying the same plan again extends from the current period
      end rather than from today (``_record_plan_period``'s ``previous_plan == item_key``
      branch), so renewing early never costs the buyer time. There is no standing
      instruction at the gateway, so nothing charges a card automatically.
    * Expiry: ``core.subscription_dependencies._one_time_period_has_lapsed`` stops honouring
      the tier once ``next_billing_date`` passes, and ``core.plan_expiry`` persists the
      downgrade to free.
    * Cancel / resume: local state changes, which is why ``cancel_subscription`` survived.
    * Trial, grace period, proration on upgrade, scheduled downgrade at period end: NONE OF
      THESE EXIST. A plan change takes effect when the new plan is paid for.

This file is a ratchet on that surface. It is deliberately a structural test: the point is
what the module OFFERS, which is a question about its surface, not about a return value.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from backend_app.core.billing_lifecycle import BillingLifecycle, SubscriptionStatus

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend_app"

#: Everything the product calls. Both are reached from the Razorpay webhook's grant path or
#: from the cancel route; neither is a mock-only convenience.
EXPECTED_SURFACE = {"add_billing_period", "cancel_subscription"}

#: Removed because no production code called them. Re-adding any of these means wiring a
#: real caller in the same change, not restoring a library for tests to drive.
REMOVED_BECAUSE_UNCALLED = {
    "activate_subscription",
    "calculate_grace_period_end_date",
    "calculate_next_billing_date",
    "calculate_trial_end_date",
    "check_subscription_expiry",
    "check_trial_expiry",
    "downgrade_subscription",
    "renew_subscription",
    "start_trial",
    "upgrade_subscription",
}


def _public_methods() -> set[str]:
    return {
        name
        for name, _member in inspect.getmembers(
            BillingLifecycle, predicate=lambda m: inspect.isfunction(m) or inspect.ismethod(m)
        )
        if not name.startswith("_")
    }


def test_the_surface_is_exactly_what_the_product_calls():
    assert _public_methods() == EXPECTED_SURFACE, (
        "BillingLifecycle's public surface changed. If a method was ADDED, wire its "
        "production caller in the same change and add it to EXPECTED_SURFACE; a lifecycle "
        "operation that only tests invoke is how this class came to advertise a 14-day "
        "trial the product has never had."
    )


@pytest.mark.parametrize("name", sorted(REMOVED_BECAUSE_UNCALLED))
def test_an_uncalled_lifecycle_operation_has_not_come_back(name):
    assert not hasattr(BillingLifecycle, name), (
        f"BillingLifecycle.{name} is back. It previously existed with no production caller. "
        "If the product now genuinely performs this operation, the caller is the evidence "
        "for that and belongs in the same commit."
    )


def test_nothing_in_the_backend_writes_a_trial_or_grace_period_status():
    """``TRIAL`` and ``GRACE_PERIOD`` are retained as vocabulary, and must stay unwritten.

    They are kept because a missing attribute is a harder failure than an unused one -- a
    stale reader asking for ``SubscriptionStatus.TRIAL`` should get a string, not an
    ``AttributeError`` inside a payment handler. But if anything ever starts WRITING one,
    the product has acquired a state with no code to leave it: nothing expires a trial and
    nothing ends a grace period, so a profile written into either would stay there.
    """
    assert SubscriptionStatus.TRIAL == "trial"
    assert SubscriptionStatus.GRACE_PERIOD == "grace_period"

    writers: list[str] = []
    for path in sorted(BACKEND.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        rel = path.relative_to(REPO).as_posix()
        for node in ast.walk(tree):
            # `SubscriptionStatus.TRIAL` used as a VALUE: a dict value, an assignment, or a
            # call argument. Its definition inside the enum class is excluded by matching
            # only attribute access, never the bare `TRIAL = "trial"` target.
            if not isinstance(node, ast.Attribute):
                continue
            if node.attr not in ("TRIAL", "GRACE_PERIOD"):
                continue
            if getattr(node.value, "id", None) != "SubscriptionStatus":
                continue
            writers.append(f"{rel}:{node.lineno} reads SubscriptionStatus.{node.attr}")

    assert writers == [], (
        "SubscriptionStatus.TRIAL / GRACE_PERIOD is referenced in backend code:\n  "
        + "\n  ".join(writers)
        + "\nThere is no trial and no grace period in this product: nothing starts one and, "
          "more importantly, nothing ends one. A profile written into either state would "
          "stay in it."
    )


def test_the_real_activation_path_is_the_webhook_not_this_class():
    """The coverage the deleted tests appeared to give actually lives on the webhook path.

    Asserted so that "we deleted the activation tests" cannot be read as "activation is
    untested". The grant is applied by ``_apply_billing_entitlement`` and the paid period by
    ``_record_plan_period``, both reached from ``razorpay_webhook``; the behavioural tests
    for that path are ``test_billing_e2e.py``, ``test_bill01_razorpay_plans_expire.py`` and
    ``test_subscription_renewal_regression.py``.
    """
    billing = (BACKEND / "routers" / "billing.py").read_text(encoding="utf-8")
    tree = ast.parse(billing)

    handlers = [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "razorpay_webhook"
    ]
    assert len(handlers) == 1

    defined = {
        n.name for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for required in ("_apply_billing_entitlement", "_record_plan_period"):
        assert required in defined, f"{required} is gone; the grant path has changed shape"

    # `add_billing_period` is the calendar-safe survivor, and `_record_plan_period` is what
    # calls it. If that call disappears, a paid plan stops acquiring an end date and the
    # BILL-01 defect (a month's payment buying the plan forever) is open again.
    calls_add_period = [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "add_billing_period"
    ]
    assert calls_add_period, (
        "nothing calls BillingLifecycle.add_billing_period any more, so a purchased plan "
        "gets no period end and never expires (BILL-01)."
    )
