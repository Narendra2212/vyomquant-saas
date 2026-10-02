"""tests/test_billing_entitlements_endpoint.py — ``GET /api/billing/entitlements`` over HTTP.

WHY THIS FILE EXISTS, AND WHY IT IS AT THE HTTP LEVEL
=====================================================
A production billing page reported *"Could not check your plan — your entitlements are
unreadable, so none are assumed"* while every service-level test over the entitlement layer
passed. It passed because the fault was not in the layer: ``get_plan_context`` was doing exactly
what it is specified to do — failing CLOSED when the ``profiles`` read does not answer, rather
than assuming Free and silently stripping a paying account of the capacity it bought.

What was wrong was that the refusal was INDISTINGUISHABLE from a defect. It was raised as a bare
``RuntimeError``, the handler funnelled every exception into one generic
``500 BILLING_ENTITLEMENTS_FAILED``, and the response carried no correlation id — so

  * the trader could not tell "try again in a moment" from "report this";
  * whoever picked up the report could not either, without the server log;
  * and the ``req_…`` reference printed on screen appeared in no log line at all, because
    ``apiClient`` mints it client-side and the handler never recorded the inbound header.

None of those three facts is visible from inside the entitlement layer. They are properties of the
RESPONSE, so they are asserted against the response. This file is the same correction applied to
``tests/test_pricing_ladder.py::TestThePublicPlansEndpoint`` — both exist because a seam between a
correct service and a correct client went untested.

WHAT IS PINNED
--------------
The four outcomes this endpoint can have, by status code and by stable error code:

    readable profile            → 200, the plan the row holds
    no profile row              → 200, ``free`` — absence is an answer, not a failure
    profiles read RAISES        → 503 ``BILLING_PLAN_UNVERIFIABLE`` (production)
    no database client          → 503 ``BILLING_PLAN_UNVERIFIABLE`` (production)

plus the fail-OPEN half outside production, and the correlation id in both error bodies.

``ENV`` is set per test rather than once for the module: ``_is_production()`` reads it at call
time, and the whole point of two of these tests is the difference between the two settings.

THE PLAN CACHE IS WHY EVERY TEST USES ITS OWN USER ID
-----------------------------------------------------
``get_plan_context`` caches the resolved plan under ``entitlement:plan:{user_id}`` and the router
caches the whole payload under ``billing:entitlements:{user_id}``. Sharing an id across these
tests makes the second one read the first one's answer — which, while this file was being written,
produced five identical passes for five different inputs. ``conftest``'s autouse
``_entitlement_state_is_per_test`` clears those keys between tests; a fresh id makes the isolation
independent of it.
"""

from __future__ import annotations

import os
from itertools import count
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.core.subscription_dependencies import PlanVerificationUnavailable
from backend_app.main import app

ENDPOINT = "/api/billing/entitlements"

#: A readable ``profiles`` row for a paying account. The column names are the ones
#: ``get_plan_context`` and the router's lifecycle read actually select.
PAID_PROFILE = {
    "subscription_tier": "pro",
    "subscription_status": "active",
    "plan_limit_overrides": None,
    "next_billing_date": None,
    "cancel_at_period_end": False,
    "billing_interval": "month",
}

_ids = count(1)


def fresh_user_id() -> str:
    """A user id no other test in this file has used. See the module docstring."""
    return f"00000000-0000-4000-8000-{next(_ids):012d}"


def profiles_double(row: Optional[Dict[str, Any]] = None, raises: Optional[Exception] = None):
    """A Supabase client double whose ``profiles`` read returns one row, or raises.

    ``raises`` stands in for what production actually does when the request-scoped read does not
    answer: PostgREST reporting an expired JWT, a connection failure, a denied policy that errors
    rather than returning nothing. The entitlement layer treats all of those identically — it
    cannot establish the plan — so one double covers them.
    """
    client = MagicMock()
    chain = client.table.return_value
    for method in ("select", "eq", "limit", "order", "single", "neq", "gte", "lte"):
        getattr(chain, method).return_value = chain

    def execute():
        if raises is not None:
            raise raises
        return MagicMock(data=[row] if row else [])

    chain.execute.side_effect = execute
    return client


def call(supabase: Any, env: str, headers: Optional[Dict[str, str]] = None):
    """One request to the endpoint, as ``user`` with ``supabase``, with ``ENV`` set to ``env``."""
    user = {"id": fresh_user_id(), "email": "trader@vyomquant.io", "role": "authenticated"}
    previous = os.environ.get("ENV", "")
    os.environ["ENV"] = env
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_request_supabase] = lambda: supabase
    try:
        # No `with`: the application lifespan opens the Redis, database and exchange connections
        # this suite has none of. The endpoint touches no startup-managed resource.
        return TestClient(app, raise_server_exceptions=False).get(ENDPOINT, headers=headers or {})
    finally:
        app.dependency_overrides.clear()
        os.environ["ENV"] = previous


# ══════════════════════════════════════════════════════════════════════════
# 1. The outcomes that are NOT failures
# ══════════════════════════════════════════════════════════════════════════


class TestAReadableSubscriptionIsAnswered:
    def test_a_paying_account_gets_its_plan(self):
        body = call(profiles_double(PAID_PROFILE), "production").json()
        assert body["plan"] == "pro"
        assert body["tier"] == "PRO_QUANT"
        assert body["display_name"] == "Pro Quant"

    def test_the_response_carries_the_ladder_and_the_refusal_copy(self):
        """A client must not need a local plan table to render a locked panel."""
        body = call(profiles_double(PAID_PROFILE), "production").json()
        for key in (
            "quotas",
            "usage",
            "usage_unavailable",
            "locked_features",
            "limit_refusals",
            "metered_resources",
            "usage_period",
            "upgrade_to",
        ):
            assert key in body, f"{key} is missing from the entitlements payload"

    def test_an_unreadable_usage_figure_is_reported_rather_than_zeroed(self):
        """``0 / 10`` is a claim; an unread figure is not one.

        The double answers every table with one profiles row, so the usage reads cannot produce
        real counts — which is the condition being asserted. ``collect_usage`` catches per
        resource, so the endpoint still answers 200 and names what it could not read.
        """
        body = call(profiles_double(PAID_PROFILE), "production").json()
        assert body["usage_unavailable"], "nothing was reported as unreadable"
        for resource, reason in body["usage_unavailable"].items():
            assert isinstance(reason, str) and reason.strip(), resource
            assert resource not in body["usage"], (
                f"{resource} is both reported as unreadable and given a figure"
            )

    def test_no_profile_row_reads_as_free_rather_than_as_a_failure(self):
        """An account with nothing stored IS a Free account. Absence is an answer here."""
        response = call(profiles_double(row=None), "production")
        assert response.status_code == 200
        assert response.json()["plan"] == "free"


# ══════════════════════════════════════════════════════════════════════════
# 2. The fail-closed refusal, and that it no longer impersonates a defect
# ══════════════════════════════════════════════════════════════════════════


class TestAnUnreadableSubscriptionIsRefusedAndSaysSo:
    """The screenshot this file was written from.

    Both cases below are the entitlement layer refusing on purpose. What is asserted is that the
    RESPONSE says so: a distinct code, a retryable status, and a sentence that cannot be misread
    as "your plan was removed".
    """

    @pytest.mark.parametrize(
        "supabase,case",
        [
            (profiles_double(raises=Exception("PGRST301 JWT expired")), "the read raises"),
            (None, "there is no database client"),
        ],
    )
    def test_it_answers_503_with_its_own_code(self, supabase, case):
        response = call(supabase, "production")

        # 503, not 500: nothing is wrong with this handler. An upstream read was unavailable, the
        # condition is transient, and the status is what makes a client's retry logic correct.
        assert response.status_code == 503, case
        detail = response.json()["detail"]
        assert detail["error"] == "BILLING_PLAN_UNVERIFIABLE", case
        # The code a genuine defect in this handler produces. These being DIFFERENT strings is the
        # entire fix: one is worth waiting out, the other is worth reporting.
        assert detail["error"] != "BILLING_ENTITLEMENTS_FAILED", case

    def test_it_states_that_nothing_was_changed(self):
        """"No entitlement assumed" on a billing page reads as "my plan is gone" unless said."""
        detail = call(profiles_double(raises=Exception("boom")), "production").json()["detail"]
        assert "could not be read" in detail["message"]
        assert "has changed" in detail["message"] or "unchanged" in detail["message"]

    def test_it_tells_the_client_to_come_back(self):
        response = call(profiles_double(raises=Exception("boom")), "production")
        assert response.headers.get("Retry-After") == "5"

    def test_the_refusal_names_no_internals(self):
        """The trader's sentence must not carry a driver code, a table name or a column name."""
        detail = call(
            profiles_double(raises=Exception("PGRST301 relation profiles JWT expired")),
            "production",
        ).json()["detail"]
        message = detail["message"]
        for leaked in ("PGRST", "profiles", "JWT", "supabase", "Supabase", "Traceback"):
            assert leaked not in message, f"{leaked!r} reached the client"

    def test_outside_production_the_same_failure_falls_open_to_free(self):
        """A developer with no database still gets a usable page; production never guesses."""
        response = call(profiles_double(raises=Exception("boom")), "testing")
        assert response.status_code == 200
        assert response.json()["plan"] == "free"


# ══════════════════════════════════════════════════════════════════════════
# 3. The correlation id — the half that makes a report actionable
# ══════════════════════════════════════════════════════════════════════════


class TestEveryFailureCarriesTheReferenceOnScreen:
    """``apiClient`` mints ``req_<uuid4>`` and sends it as ``X-Request-ID``;
    ``design/errorCopy.readSupportRef`` prints it as the support reference. If the server does not
    record and echo that value, the one identifier a trader can quote matches nothing in the log
    and a report has to be correlated by guessing over timestamps.
    """

    SENT = "req_11111111-2222-4333-8444-555555555555"

    def test_the_refusal_echoes_the_clients_own_reference(self):
        response = call(
            profiles_double(raises=Exception("boom")),
            "production",
            headers={"X-Request-ID": self.SENT},
        )
        assert response.json()["detail"]["request_id"] == self.SENT
        # Exactly once. `CorrelationIdMiddleware` owns the response header; the handler setting it
        # as well produced `id, id`, which is not a reference anyone can quote.
        assert response.headers.get("X-Request-ID") == self.SENT

    def test_a_generic_failure_echoes_it_too(self):
        """The catch-all arm, reached by making the entitlement read itself blow up."""
        from unittest.mock import patch

        with patch(
            "backend_app.core.subscription_dependencies.get_user_entitlements",
            side_effect=TypeError("something this handler got wrong"),
        ):
            response = call(
                profiles_double(PAID_PROFILE), "production", headers={"X-Request-ID": self.SENT}
            )

        assert response.status_code == 500
        detail = response.json()["detail"]
        assert detail["error"] == "BILLING_ENTITLEMENTS_FAILED"
        assert detail["request_id"] == self.SENT

    def test_a_reference_is_issued_even_when_the_client_sends_none(self):
        detail = call(profiles_double(raises=Exception("boom")), "production").json()["detail"]
        assert isinstance(detail["request_id"], str) and detail["request_id"].strip()


# ══════════════════════════════════════════════════════════════════════════
# 4. The refusal type itself
# ══════════════════════════════════════════════════════════════════════════


class TestPlanVerificationUnavailableStaysCompatible:
    def test_it_is_a_runtime_error(self):
        """Existing callers wrap this read in ``except RuntimeError`` and must keep working.

        ``routers/strategy_operations.py`` and ``backend/model_versioning.py`` both do, and
        ``tests/test_pricing_ladder.py`` asserts ``pytest.raises(RuntimeError)`` on the fail-closed
        path. Narrowing the type to something outside that hierarchy would have turned a handled
        refusal into an unhandled one in three places at once.
        """
        assert issubclass(PlanVerificationUnavailable, RuntimeError)

    def test_the_fail_closed_path_raises_it_by_name(self):
        from unittest.mock import patch

        from backend_app.core.subscription_dependencies import get_plan_context

        with patch.dict(os.environ, {"ENV": "production"}):
            with pytest.raises(PlanVerificationUnavailable):
                import asyncio

                asyncio.new_event_loop().run_until_complete(
                    get_plan_context(fresh_user_id(), None)
                )


# ══════════════════════════════════════════════════════════════════════════
# 5. The correlation middleware — why the reference was useless everywhere
# ══════════════════════════════════════════════════════════════════════════


class TestTheCorrelationMiddlewareAcceptsThisClientsIds:
    """The defect behind "the support reference matches nothing in the log".

    It was never that the billing handler failed to log an id. ``CorrelationIdMiddleware`` was
    registered with its DEFAULT validator, ``is_valid_uuid4``, which rejects the ``req_<uuid4>``
    this application's own client sends — so the middleware discarded it, generated a replacement,
    and every log record and every structured error body for that request carried the SERVER's id
    while the trader's screen showed the browser's. That applied to every endpoint, which is why
    the fix is at the middleware and the guard is here rather than in a billing test.

    The validator is asserted directly. Driving it through a request would prove the accepting
    cases and could not prove the rejecting ones — a rejected id is replaced by a generated one,
    so "was it rejected?" and "did the generator run?" are the same observation.
    """

    @staticmethod
    def _validator():
        """The validator as `main.py` registered it, read off the installed middleware stack."""
        from asgi_correlation_id import CorrelationIdMiddleware

        from backend_app.main import app

        for middleware in app.user_middleware:
            if middleware.cls is CorrelationIdMiddleware:
                return middleware.kwargs["validator"]
        pytest.fail("CorrelationIdMiddleware is not installed")

    @pytest.mark.parametrize(
        "value",
        [
            "req_11111111-2222-4333-8444-555555555555",  # what `apiClient` actually sends
            "11111111-2222-4333-8444-555555555555",      # a bare uuid4, e.g. curl or a proxy
            "5ecac698c05d435fbd9aae1478f86d48",           # the middleware's own hex form
        ],
    )
    def test_it_adopts_an_id_this_system_produces(self, value):
        assert self._validator()(value) is True, value

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "   ",
            "not-an-id",
            "req_",
            "req_11111111-2222-4333-8444-55555555555",   # one digit short
            "req_11111111-2222-4333-8444-555555555555x",
            "zzzzzzzz-2222-4333-8444-555555555555",       # non-hex
            # A log-injection attempt. The validator staying strict is why an inbound header
            # cannot write forged text or a second line into the record identifying its own
            # request — this is the reason the default validator exists and the reason the
            # replacement is a pattern rather than "accept anything".
            "req_11111111-2222-4333-8444-555555555555\nERROR: forged",
            "req_11111111-2222-4333-8444-555555555555 OR 1=1",
        ],
    )
    def test_it_refuses_anything_else_and_generates_instead(self, value):
        assert self._validator()(value) is False, value
