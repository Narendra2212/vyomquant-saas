"""
tests/test_marketplace_checkout_regression.py

Task 18.3 of ``marketplace-subscriptions-paper-trading``: the ROUTER-level regression guard on
``POST /api/library/{library_id}/checkout``.

WHY THIS IS NOT tests/test_checkout_service.py
----------------------------------------------
Task 18.1's suite unit-tests ``checkout_service.create_checkout`` directly, by calling it with a
fake Persistence_Layer and an injected provider factory. That proves the *service* is correct.
It says nothing about whether the endpoint reaches it, whether the handler still indexes a
``.single()`` response as a list before the service is ever consulted, or whether the amount the
service computed survives the trip into the response body. Every one of the four defects task
18.2 removed lived in the **handler**, above the service, so this module drives the whole path
through FastAPI: ``TestClient`` → route → dependencies → handler → service → fake driver, and
asserts on the HTTP status, the HTTP body and the rows the driver actually received.

WHAT IS ASSERTED (Requirements 9.2, 9.3, 9.4, 9.11, 9.13, 10.1)
---------------------------------------------------------------
Five statements, each of which FAILS against the pre-fix handler (see the revert-proof section):

1. **The endpoint completes.** ``.single()`` sets ``resp.data`` to a **dict**, so the pre-fix
   ``strat = resp.data[0]`` raised ``KeyError: 0`` — and that statement sat *outside* the
   ``try``, so FastAPI answered a bare 500. The endpoint had therefore **never completed for any
   Listing**. Asserted as a 200 carrying ``subscription_id`` and ``provider_reference``.
2. **The amount is exactly 1999 Minor_Units** for a ``$19.99`` Listing — in the response body,
   in the persisted row, and in the value handed to the provider. Not the 1998 that
   ``int(float("19.99") * 100)`` produces (Requirements 9.2, 10.1).
3. **The ``PENDING`` insert records ``owner_share_minor`` 1799 and ``platform_fee_minor`` 200 and
   carries no ``expires_at``** — absent, not present-and-``NULL``, because
   ``check_deployment_permission`` reads a null expiry as a perpetual subscription
   (Requirements 9.3, 10.1).
4. **A provider failure leaves the row present in ``payment_failed`` with a ``failure_cause``**,
   and **zero** ``delete`` statements are issued against ``library_subscriptions``
   (Requirement 9.4).
5. **The 30-second deadline abandons the attempt** and applies the same ``PAYMENT_FAILED`` path
   (Requirement 9.13). The deadline that reaches the provider call on the *router* path is
   asserted to be the production 30, and the abandonment behaviour is then exercised under a
   short injected deadline — a suite that waited thirty real seconds to prove a deadline exists
   is a suite nobody runs.

═══════════════════════════════════════════════════════════════════════════════════════════════
REVERT-PROOF — the exact pre-fix construct this module detects (Task 35.2 reads this section)
═══════════════════════════════════════════════════════════════════════════════════════════════
A regression test that passes against the unfixed code is evidence of nothing. The construct
this module is built to catch is the body ``create_marketplace_checkout`` had before task 18.2
(``git show HEAD:backend_app/routers/library.py``, lines 1820–1975):

    resp = (svc.table("library_strategies")
              .select("*")                                   # ← DEFECT 1 (default-OPEN read)
              .eq("id", lib_id).eq("is_active", True)
              .in_("moderation_status", ["approved", "featured"])
              .single().execute())
    if not resp.data:
        raise HTTPException(404, …)
    strat = resp.data[0]                                     # ← DEFECT 2  KeyError: 0
    …
    svc.table("library_subscriptions").insert({
        …, "price_paid": strat.get("price"),
        "expires_at": None,                                  # ← DEFECT 3  "perpetual"
    }).execute()                                             #   and NO split components
    try:
        import stripe                                        # ← DEFECT 5  inline SDK import
        stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "sk_test_dummy")
        amount_cents = int(float(strat.get("price", 0)) * 100)  # ← DEFECT 4  1998, not 1999
        session = stripe.checkout.Session.create(…)          #   and NO deadline at all
        …
    except Exception as exc:
        try:
            svc.table("library_subscriptions").delete().eq("id", sub_id).execute()  # DEFECT 6
        except:                                              # ← bare except: pass
            pass
        raise HTTPException(500, "Failed to create checkout session")

:data:`PRE_FIX_CONSTRUCT` names the four defects the task enumerates — the ``.single()``
mis-index, the ``float`` amount, the null ``expires_at`` with no split, and the delete-on-failure
— together with the two the source guard additionally forbids (``.select("*")`` and the inline
SDK import with its ``"sk_test_dummy"`` default), what replaced each, and how to reproduce the
revert.

HOW TO REPRODUCE THE REVERT, AND WHAT FAILS
-------------------------------------------
Two ways, and both are exercised here.

*Mechanically, in-process, with no file edit:* :func:`PRE_FIX_CHECKOUT` is the pre-fix body as a
callable, defect for defect, over the **same** fake driver and the **same** seeded Listing this
module's router tests use. ``test_the_five_assertions_all_fail_under_the_in_process_revert``
runs it and asserts that each of the five assertion helpers — the very functions the five
router tests call — raises ``AssertionError``, naming the defect. That is the check task 35.2
can run without touching the working tree.

*By hand, against the real handler:* while task 18.3 was implemented, ``library.py``'s handler
body was replaced with the pre-fix body quoted above (a scratch revert), then
``pytest tests/test_marketplace_checkout_regression.py`` was run. **7 failed, 10 passed** — the
five behavioural assertions plus both source guards. Verbatim:

    test_checkout_completes_rather_than_raising
      AssertionError: [current handler] POST /api/library/{id}/checkout did not complete:
      HTTP 500, body {'error': 'INTERNAL_SERVER_ERROR', …}. The .single() response is a dict;
      `resp.data[0]` on it raises KeyError: 0 (Requirement 9.11)
      — with the captured server traceback ending:
          File "backend_app/routers/library.py", line 2373, in create_marketplace_checkout
            strat = resp.data[0]
        KeyError: 0

    test_charged_amount_is_1999_minor_units
      AssertionError: [current handler] the response body carries no 'amount_minor', so the
      charged amount is not reported in Minor_Units at all: {'error': 'INTERNAL_SERVER_ERROR',
      …} (Requirements 9.2, 10.1)

    test_pending_insert_records_the_split_and_omits_expires_at
      AssertionError: [current handler] expected exactly one PENDING insert, saw 0: []
      (Requirement 9.3 — the row is written in one committed statement)

    test_provider_failure_leaves_a_payment_failed_row_and_never_deletes
      AssertionError: [current handler] a provider failure answered HTTP 500, not the 502
      MARKETPLACE_CHECKOUT_UNAVAILABLE the catalogue defines: {'error':
      'INTERNAL_SERVER_ERROR', …}

    test_thirty_second_deadline_abandons_the_attempt_and_marks_payment_failed
      AssertionError: [current handler] no deadline reached the provider call at all — the
      attempt would wait on the provider indefinitely (Requirement 9.13)

    test_the_pre_fix_constructs_are_absent_from_the_handler
      AssertionError: .select("*") is back in the handler at line(s) [40, 18]; the read is
      checkout_service.CHECKOUT_LISTING_SELECT (Requirement 6.1)
      — the numbers are handler-relative, so they move with the reverted body's formatting

    test_the_handler_delegates_to_the_checkout_service
      AssertionError: the handler must call _checkout_service.create_checkout exactly once;
      found 0

Note what the whole-handler revert shows: **all five** behavioural assertions fail at defect 1,
because ``strat = resp.data[0]`` raises before the amount, the insert, the failure path or the
deadline is ever reached. That IS the pre-fix reality — the endpoint completed for no Listing, so
the other three defects were unreachable in production. Isolating defects 2-4 therefore needs
the in-process revert, where ``_ListShapedDb`` hands the pre-fix body the list shape it assumed
and ``test_the_in_process_revert_reproduces_each_named_defect`` observes 1998, the null
``expires_at`` with no split, and the delete.

The handler was then restored and its SHA-256 confirmed identical to the pre-experiment file
(``70B76581C1C1AD57654A3EC9CF1C1079F762F9A845006F3572E009A12BEF8D71``); all 17 tests pass. The
scratch revert is NOT left in the tree — the in-process revert above is its reproducible form.

NON-VACUITY
-----------
The seeded ``library_strategies`` row carries **both** money columns: the authoritative
``price_minor`` 1999 **and** the legacy ``price`` mirror ``"19.99"`` the pre-fix handler read.
So "the amount is 1999" is a statement about a row from which 1998 is genuinely derivable, and
the revert produces 1998 rather than failing for want of a column. The row is also
``is_active``/``moderation_status='approved'`` so the pre-fix filter chain matches it, and
carries an embedded ``PUBLISHED`` Submission so the current gate admits it.

HARNESS
-------
FastAPI ``TestClient`` (``raise_server_exceptions=False``, so a handler that raises is observed
as the 500 a client would receive) plus a filter-aware fake Supabase double that returns the
Listing read as a **single mapping** — the exact shape ``.single()`` produces — and keeps a
mutable ``library_subscriptions`` table recording every statement, including deletes. Returning
the mapping rather than a list is load-bearing: a double that returned a list would hand the
pre-fix ``resp.data[0]`` a working row and the defect this module exists to catch would vanish
along with the bug. The provider factory is injected over
``checkout_service.default_provider_session_factory``, so no SDK, credential or network call is
involved.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import inspect
import logging
import os
import sys
import textwrap
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend_app.backend.marketplace import checkout_service as cs
from backend_app.backend.marketplace.errors import (
    MARKETPLACE_CHECKOUT_UNAVAILABLE,
)
from backend_app.core.dependencies import get_current_user
from backend_app.core.subscription_dependencies import require_marketplace_access
from backend_app.main import app
from backend_app.routers import library as library_router

logger = logging.getLogger("MarketplaceCheckoutRegression")

client = TestClient(app, raise_server_exceptions=False)

LIBRARY_PATH = (
    Path(__file__).resolve().parents[1] / "backend_app" / "routers" / "library.py"
)


# ══════════════════════════════════════════════════════════════════════════
# Identities, the seeded Listing, and the exact money figures
# ══════════════════════════════════════════════════════════════════════════

#: Valid UUIDs, because ``_safe_uuid`` rejects anything else with a 422 before the handler runs.
LISTING_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
OWNER_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
CALLER_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"

CALLER_USER = {
    "id": CALLER_ID,
    "email": "buyer@test.example",
    "role": "authenticated",
    "app_metadata": {},
    "user_metadata": {},
}

#: A $19.99 Listing. ``price_minor`` is the authority; ``price`` is the legacy NUMERIC(10,2)
#: mirror the pre-fix handler read. Both are seeded — see NON-VACUITY above.
PRICE_MINOR_1999 = 1999
PRICE_MAJOR_TEXT = "19.99"

#: ``int(float("19.99") * 100)`` — what the pre-fix arithmetic charged. 19.99 is not nineteen
#: and ninety-nine hundredths in binary, and ``int()`` truncates the 1998.9999999999998.
PRE_FIX_AMOUNT_1998 = 1998

OWNER_SHARE_1799 = 1799  # (1999 * 90) // 100
PLATFORM_FEE_200 = 200   # 1999 - 1799

#: The deadline injected for the behavioural half of assertion 5. The PRODUCTION value that
#: must reach the provider call is asserted separately, and is 30.
TEST_DEADLINE_SECONDS = 0.02


def _seeded_listing_row() -> Dict[str, Any]:
    """A PUBLISHED, purchasable Listing carrying every column both handlers read.

    ``marketplace_submissions`` is the embed ``CHECKOUT_LISTING_SELECT`` requests;
    ``is_active`` / ``moderation_status`` are the columns the pre-fix filter chain matched on.
    Both are present so neither the current nor the reverted handler fails for want of a column
    — a revert must fail on the DEFECT, not on the fixture.
    """
    return {
        "id": LISTING_ID,
        "name": "Momentum Breakout",
        "author_id": OWNER_ID,
        # The authority (task 4.1's Minor_Units integer)…
        "price_minor": PRICE_MINOR_1999,
        # …and the legacy mirror the pre-fix handler read through float().
        "price": PRICE_MAJOR_TEXT,
        "currency": "USD",
        "is_active": True,
        "moderation_status": "approved",
        "subscription_tier": "standard",
        "marketplace_submissions": [{"submission_state": "PUBLISHED"}],
    }


# ══════════════════════════════════════════════════════════════════════════
# THE PRE-FIX CONSTRUCT, RECORDED MECHANICALLY (task 18.3 step 2)
# ══════════════════════════════════════════════════════════════════════════

#: The pre-fix construct, defect by defect, so a future reader — or Task 35.2's "each of the six
#: root-cause regression tests fails on a revert of its fix" check — can reproduce the revert
#: without rediscovering it.
#:
#: ``defects`` holds the four the task enumerates. ``also_removed`` holds the two the source
#: guard additionally forbids: they are not among the five behavioural assertions (a
#: ``select("*")`` read and a test-mode credential default are not observable in a response
#: body) but they were part of the same construct and a revert would bring them back, so the
#: guard names them structurally instead.
PRE_FIX_CONSTRUCT: Dict[str, Any] = {
    "handler": "backend_app.routers.library.create_marketplace_checkout",
    "spec_task": "marketplace-subscriptions-paper-trading / 18.1, 18.2",
    "requirements": ("9.2", "9.3", "9.4", "9.11", "9.13", "10.1"),
    "source_of_truth": "git show HEAD:backend_app/routers/library.py  (lines 1820-1975)",
    "defects": {
        "single_indexed_as_list": {
            "was": "strat = resp.data[0]   # on a .single() response, i.e. a dict",
            "symptom": "KeyError: 0 raised OUTSIDE the try -> bare HTTP 500; the endpoint "
                       "never completed for any Listing",
            "now": "checkout_service._rows(response) normalises the .single() dict, the "
                   "{'data': ...} envelope and the bare list into one list shape",
            "asserted_by": "assert_completes_rather_than_raising",
            "requirement": "9.11",
        },
        "float_amount": {
            "was": 'amount_cents = int(float(strat.get("price", 0)) * 100)  '
                   "# and the amount_paise twin",
            "symptom": "a $19.99 Listing was charged 1998, not 1999",
            "now": "money.amount_for_listing(listing) -> the stored price_minor int, unchanged",
            "asserted_by": "assert_charged_amount_is_1999",
            "requirement": "9.2 / 10.1",
        },
        "null_expires_at_and_no_split": {
            "was": '"expires_at": None on the PENDING insert, with no owner_share_minor and '
                   "no platform_fee_minor",
            "symptom": "check_deployment_permission reads a null expiry as a PERPETUAL "
                       "subscription, and the 90/10 split was never recorded",
            "now": "checkout_service.OMITTED_ON_PENDING omits the column entirely, and "
                   "money.split_ninety_ten writes both components on the one insert",
            "asserted_by": "assert_pending_insert_records_split_and_omits_expires_at",
            "requirement": "9.3 / 10.1",
        },
        "delete_on_provider_failure": {
            "was": 'svc.table("library_subscriptions").delete().eq("id", sub_id).execute() '
                   "inside a bare `except: pass`",
            "symptom": "a failed provider call DESTROYED the only record that the payment "
                       "attempt had been made",
            "now": "checkout_service._record_failure UPDATEs the row to payment_failed with a "
                   "failure_cause and a failed_at; the row is never deleted",
            "asserted_by": "assert_provider_failure_leaves_payment_failed_row",
            "requirement": "9.4",
        },
    },
    "also_removed": {
        "star_select": {
            "was": 'svc.table("library_strategies").select("*")',
            "now": "checkout_service.CHECKOUT_LISTING_SELECT (explicit column list + the "
                   "Submission_State embed)",
            "asserted_by": "test_the_pre_fix_constructs_are_absent_from_the_handler",
            "requirement": "6.1",
        },
        "inline_sdk_import_and_test_credential": {
            "was": 'import stripe / import razorpay in the handler, with '
                   'os.environ.get("STRIPE_SECRET_KEY", "sk_test_dummy")',
            "now": "checkout_service.default_provider_session_factory, credentials through "
                   "billing._validate_keys — which refuses a placeholder on a production path",
            "asserted_by": "test_the_pre_fix_constructs_are_absent_from_the_handler",
            "requirement": "9.1",
        },
        "no_provider_deadline": {
            "was": "the provider SDK call was awaited with no timeout of any kind",
            "now": "asyncio.wait_for(..., timeout=PROVIDER_DEADLINE_SECONDS) == 30",
            "asserted_by": "assert_deadline_abandons_and_marks_payment_failed",
            "requirement": "9.13",
        },
    },
    "revert_in_process": (
        "tests/test_marketplace_checkout_regression.py::PRE_FIX_CHECKOUT — the pre-fix body as "
        "a callable over the same fake driver; "
        "test_the_five_assertions_all_fail_under_the_in_process_revert runs it and asserts each "
        "of the five assertion helpers raises AssertionError"
    ),
    "revert_by_hand": (
        "replace the body of create_marketplace_checkout with the block quoted in this "
        "module's docstring, run pytest tests/test_marketplace_checkout_regression.py, then "
        "restore the file and confirm its SHA-256"
    ),
}


async def PRE_FIX_CHECKOUT(
    *,
    lib_id: str,
    user_id: str,
    currency: str,
    svc: Any,
    provider_call: Any,
) -> Dict[str, Any]:
    """The pre-fix handler body, defect for defect, as an in-process callable.

    Transcribed from ``git show HEAD:backend_app/routers/library.py``. Two deliberate
    departures, neither of which touches a defect:

    * the FastAPI dependency wiring (``Depends(get_current_user)``, ``_safe_uuid``) is replaced
      by plain arguments — those lines were not defective and are not what is being reverted;
    * ``stripe.checkout.Session.create(...)`` is replaced by the injected ``provider_call``,
      which receives the amount the pre-fix arithmetic computed. The SDK call itself was never
      the defect; the **amount handed to it** was, and that is what ``provider_call`` records.
      This also keeps the revert runnable with no SDK, credential or network.

    Everything else is verbatim, including the statement order, the ``select("*")``, the
    ``resp.data[0]``, the ``"expires_at": None``, the missing split components, the absent
    deadline and the ``delete`` inside the bare ``except: pass``.
    """
    # Fetch strategy details
    try:
        resp = (
            svc.table("library_strategies")
            .select("*")  # DEFECT: default-OPEN column list
            .eq("id", lib_id)
            .eq("is_active", True)
            .in_("moderation_status", ["approved", "featured"])
            .single()
            .execute()
        )
    except Exception as exc:
        logger.error(f"Checkout strategy lookup error: {exc}")
        raise HTTPException(500, "Failed to fetch strategy details")

    if not resp.data:
        raise HTTPException(404, "Strategy not found or not available for subscription")

    # DEFECT: .single() sets resp.data to a DICT. dict[0] raises KeyError: 0, and this
    # statement is OUTSIDE the try above, so FastAPI answers a bare 500.
    strat = resp.data[0]

    if not strat.get("price"):
        raise HTTPException(400, "This strategy is free. Use the clone endpoint instead.")

    # Check if already subscribed
    try:
        existing = (
            svc.table("library_subscriptions")
            .select("*")
            .eq("library_id", lib_id)
            .eq("user_id", user_id)
            .eq("status", "active")
            .execute()
        )
        if existing.data:
            raise HTTPException(400, "You are already subscribed to this strategy")
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"Subscription check error: {exc}")

    now_ts = datetime.now(timezone.utc).isoformat()
    sub_id = str(uuid.uuid4())

    try:
        (
            svc.table("library_subscriptions")
            .insert(
                {
                    "id": sub_id,
                    "library_id": lib_id,
                    "user_id": user_id,
                    "subscription_tier": strat.get("subscription_tier", "standard"),
                    "price_paid": strat.get("price"),
                    "currency": strat.get("currency", "USD"),
                    "status": "pending",
                    "started_at": now_ts,
                    # DEFECT: present-and-NULL, read as "perpetual subscription" downstream.
                    # And NO owner_share_minor / platform_fee_minor anywhere.
                    "expires_at": None,
                }
            )
            .execute()
        )
    except Exception as exc:
        logger.error(f"Pending subscription creation error: {exc}")
        raise HTTPException(500, "Failed to create pending subscription")

    try:
        # DEFECT: `import stripe` + STRIPE_SECRET_KEY with a "sk_test_dummy" default lived here.
        # DEFECT: int(float(price) * 100) -> 1998 for a $19.99 Listing.
        amount_cents = int(float(strat.get("price", 0)) * 100)
        # DEFECT: no deadline of any kind around the provider call.
        session = provider_call(amount_cents, sub_id)
        return {
            "checkout_url": session["url"],
            "subscription_id": sub_id,
            "provider": "stripe",
        }
    except Exception as exc:
        logger.error(f"Checkout session creation error: {exc}")
        # DEFECT: the audit trail of the attempt is DELETED.
        try:
            svc.table("library_subscriptions").delete().eq("id", sub_id).execute()
        except Exception:  # the original was a bare `except:`; behaviourally identical here
            pass
        raise HTTPException(500, "Failed to create checkout session")


# ══════════════════════════════════════════════════════════════════════════
# The fake Supabase double
# ══════════════════════════════════════════════════════════════════════════


class _Resp:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Query:
    """A recording fluent builder. Honours ``eq``/``in_``; ignores the ``select`` column list.

    Ignoring the projection is deliberate, and is the same choice
    ``tests/test_library_detail_projection_regression.py`` made: a double that honoured
    ``.select(...)`` would hand a reverted handler a pre-narrowed row and the leak — here, the
    1998 derivable from the legacy ``price`` column — would disappear with the bug.
    """

    def __init__(self, table: str, db: "FakeSupabase") -> None:
        self.table_name = table
        self.db = db
        self.op = "select"
        self.payload: Optional[Dict[str, Any]] = None
        self.cols: Optional[str] = None
        self.filters: List[Tuple[str, Any]] = []
        self.single_requested = False

    # ── chain ────────────────────────────────────────────────────────────
    def select(self, cols: str = "*", *a, **k) -> "_Query":
        self.op = "select"
        self.cols = cols
        return self

    def insert(self, payload: Dict[str, Any], *a, **k) -> "_Query":
        self.op = "insert"
        self.payload = copy.deepcopy(payload)
        return self

    def update(self, payload: Dict[str, Any], *a, **k) -> "_Query":
        self.op = "update"
        self.payload = copy.deepcopy(payload)
        return self

    def delete(self, *a, **k) -> "_Query":
        self.op = "delete"
        return self

    def eq(self, column: str, value: Any) -> "_Query":
        self.filters.append((column, value))
        return self

    def in_(self, column: str, values: Any) -> "_Query":
        self.filters.append((column, ("__in__", tuple(values))))
        return self

    def single(self, *a, **k) -> "_Query":
        self.single_requested = True
        return self

    def order(self, *a, **k) -> "_Query":
        return self

    def limit(self, *a, **k) -> "_Query":
        return self

    def execute(self) -> Any:
        return self.db._execute(self)


class FakeSupabase:
    """Serves the seeded Listing and a mutable ``library_subscriptions`` table.

    The Listing read answers with a **single mapping** — the shape ``.single()`` produces —
    whether or not ``.single()`` was called. That is what makes defect 1 reachable: the current
    service normalises the mapping through ``_rows``, and the pre-fix ``resp.data[0]`` raises
    ``KeyError: 0`` on it.

    Every executed statement is recorded, and the provider factory appends its own marker to the
    same list, so "the row was committed before the provider was asked" is a measurable fact.
    """

    def __init__(
        self,
        *,
        listing_row: Optional[Dict[str, Any]] = None,
        subscription_rows: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.listing_row = copy.deepcopy(
            listing_row if listing_row is not None else _seeded_listing_row()
        )
        self.subscription_rows: List[Dict[str, Any]] = [
            dict(r) for r in (subscription_rows or [])
        ]
        self.ops: List[Tuple[str, str]] = []
        self.statements: List[_Query] = []

    # ---- the supabase-py surface both handlers use -----------------------
    def table(self, name: str) -> _Query:
        return _Query(name, self)

    def note_provider_call(self, provider: str) -> None:
        self.ops.append(("provider", provider))

    # ---- evaluation -----------------------------------------------------
    def _execute(self, q: _Query) -> Any:
        self.ops.append((q.op, q.table_name))
        self.statements.append(q)

        if q.table_name == "library_strategies":
            if not self._matches_listing(q):
                return _Resp(None)
            # THE SHAPE THAT MATTERS: a single mapping, exactly as .single() returns.
            return _Resp(copy.deepcopy(self.listing_row))

        if q.table_name == "library_subscriptions":
            if q.op == "select":
                return _Resp([dict(r) for r in self._matching(q)])
            if q.op == "insert":
                row = dict(q.payload or {})
                self.subscription_rows.append(row)
                return _Resp([dict(row)])
            if q.op == "update":
                touched = self._matching(q)
                for row in touched:
                    row.update(q.payload or {})
                return _Resp([dict(r) for r in touched])
            if q.op == "delete":
                touched = self._matching(q)
                for row in touched:
                    self.subscription_rows.remove(row)
                return _Resp([dict(r) for r in touched])

        return _Resp([])

    def _matches_listing(self, q: _Query) -> bool:
        for column, value in q.filters:
            actual = self.listing_row.get(column)
            if isinstance(value, tuple) and value and value[0] == "__in__":
                if actual not in value[1]:
                    return False
            elif str(actual) != str(value):
                return False
        return True

    def _matching(self, q: _Query) -> List[Dict[str, Any]]:
        rows = self.subscription_rows
        for column, value in q.filters:
            if isinstance(value, tuple) and value and value[0] == "__in__":
                rows = [r for r in rows if r.get(column) in value[1]]
            else:
                rows = [r for r in rows if str(r.get(column)) == str(value)]
        return rows

    # ---- assertion helpers ----------------------------------------------
    def inserted_payloads(self, table: str) -> List[Dict[str, Any]]:
        return [
            dict(s.payload or {})
            for s in self.statements
            if s.op == "insert" and s.table_name == table
        ]

    def delete_count(self, table: str) -> int:
        return sum(1 for s in self.statements if s.op == "delete" and s.table_name == table)


# ══════════════════════════════════════════════════════════════════════════
# Provider factory doubles, and the recorded outcome
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class Outcome:
    """One checkout attempt, as observed from outside: the HTTP answer plus the driver's state.

    The same record is produced by the router runner and by the in-process revert runner, so the
    five assertion helpers below apply unchanged to both. That is what makes "each assertion
    fails against the pre-fix code" a check rather than a claim.
    """

    #: The HTTP status a client would see. The revert runner maps a raised
    #: ``HTTPException``/``KeyError`` onto the status FastAPI would answer.
    status_code: int
    body: Dict[str, Any]
    db: FakeSupabase
    #: The amounts, in Minor_Units, the provider was actually asked to charge.
    provider_amounts: List[int] = field(default_factory=list)
    #: The deadline values that reached ``_request_provider_session`` on this path. Empty means
    #: no deadline was applied at all — which is what the pre-fix handler did.
    deadlines_seen: List[float] = field(default_factory=list)
    #: The exception that escaped the handler, if any.
    raised: Optional[BaseException] = None
    #: Which revert this outcome came from, for assertion messages.
    origin: str = "current handler"


def _ok_factory(db: FakeSupabase, amounts: List[int], *, reference: str = "cs_test_1999"):
    """A provider factory that succeeds and records the amount it was asked to charge."""

    def factory(context: cs.CheckoutContext) -> cs.ProviderSession:
        db.note_provider_call(context.provider)
        amounts.append(context.amount_minor)
        return cs.ProviderSession(
            provider=context.provider,
            reference=reference,
            checkout_url="https://provider.example/checkout/1",
        )

    return factory


def _raising_factory(db: FakeSupabase, amounts: List[int], message: str):
    def factory(context: cs.CheckoutContext) -> cs.ProviderSession:
        db.note_provider_call(context.provider)
        amounts.append(context.amount_minor)
        raise RuntimeError(message)

    return factory


def _slow_factory(db: FakeSupabase, amounts: List[int], seconds: float):
    """A blocking factory — which is what both real SDKs are."""

    def factory(context: cs.CheckoutContext) -> cs.ProviderSession:
        db.note_provider_call(context.provider)
        amounts.append(context.amount_minor)
        time.sleep(seconds)
        return cs.ProviderSession(provider=context.provider, reference="too_late")

    return factory


class _DeadlineProbe:
    """Records the deadline the router path applies, then shortens it for the test.

    The recorded value is the PRODUCTION one — whatever ``create_checkout`` was called with by
    the handler — so assertion 5 can insist it is 30 while the abandonment behaviour runs in
    twenty milliseconds. The real ``_request_provider_session`` (and therefore the real
    ``asyncio.wait_for``, ``TimeoutError`` translation and ``PAYMENT_FAILED`` bookkeeping) is
    what actually executes.
    """

    def __init__(self, test_deadline: float = TEST_DEADLINE_SECONDS) -> None:
        self.original = cs._request_provider_session
        self.test_deadline = test_deadline
        self.seen: List[float] = []

    async def __call__(self, factory: Any, context: Any, deadline_seconds: float) -> Any:
        self.seen.append(deadline_seconds)
        return await self.original(factory, context, self.test_deadline)


def _run_coroutine(coro: Any) -> Any:
    """Run ``coro`` to completion **without leaving the thread without an event loop**.

    Not ``asyncio.run``: that closes its loop and leaves the main thread with *no* current loop,
    and other suites in this repository still use the deprecated
    ``asyncio.get_event_loop().run_until_complete(...)`` — which then raises
    ``RuntimeError: There is no current event loop in thread 'MainThread'``. A test module that
    breaks a module collected after it is not a regression guard, it is a new regression, so the
    loop that was current is put back (or a fresh usable one installed) before returning.
    """
    previous: Optional[asyncio.AbstractEventLoop]
    try:
        previous = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        previous = None

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        if previous is not None and not previous.is_closed():
            asyncio.set_event_loop(previous)
        else:
            asyncio.set_event_loop(asyncio.new_event_loop())


class _nested:
    """Enter a list of context managers as one."""

    def __init__(self, managers: List[Any]) -> None:
        self.managers = managers

    def __enter__(self) -> "_nested":
        self.entered: List[Any] = []
        try:
            for manager in self.managers:
                manager.__enter__()
                self.entered.append(manager)
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc: Any) -> bool:
        for manager in reversed(self.entered):
            manager.__exit__(*exc)
        return False


# ══════════════════════════════════════════════════════════════════════════
# The two runners: through the route, and through the in-process revert
# ══════════════════════════════════════════════════════════════════════════


def run_checkout(
    *,
    provider: str = "ok",
    currency: str = "USD",
    db: Optional[FakeSupabase] = None,
) -> Outcome:
    """POST the checkout through the real route and record what came back.

    ``provider`` selects the injected factory: ``"ok"``, ``"raises"`` or ``"slow"``. The slow
    case also installs :class:`_DeadlineProbe`, so the deadline the handler's call actually
    carried is recorded.
    """
    db = db or FakeSupabase()
    amounts: List[int] = []
    probe: Optional[_DeadlineProbe] = None

    if provider == "ok":
        factory = _ok_factory(db, amounts)
    elif provider == "raises":
        factory = _raising_factory(db, amounts, "gateway refused the session")
    elif provider == "slow":
        factory = _slow_factory(db, amounts, seconds=0.4)
        probe = _DeadlineProbe()
    else:  # pragma: no cover - programming error in the test itself
        raise ValueError(f"unknown provider double {provider!r}")

    app.dependency_overrides[get_current_user] = lambda: CALLER_USER
    app.dependency_overrides[require_marketplace_access] = lambda: True
    try:
        managers = [
            patch.object(library_router, "_build_service_client", return_value=db),
            patch.object(cs, "default_provider_session_factory", factory),
        ]
        if probe is not None:
            managers.append(patch.object(cs, "_request_provider_session", probe))
        with _nested(managers):
            response = client.post(
                f"/api/library/{LISTING_ID}/checkout", json={"currency": currency}
            )
    finally:
        app.dependency_overrides.clear()

    try:
        body = response.json()
    except ValueError:  # pragma: no cover - a non-JSON body would itself be a defect
        body = {}

    return Outcome(
        status_code=response.status_code,
        body=body if isinstance(body, dict) else {},
        db=db,
        provider_amounts=amounts,
        deadlines_seen=list(probe.seen) if probe is not None else [],
        origin="current handler",
    )


def run_pre_fix_checkout(
    *,
    provider: str = "ok",
    db: Optional[FakeSupabase] = None,
) -> Outcome:
    """Run :func:`PRE_FIX_CHECKOUT` over the same fake driver and record the same Outcome.

    This is the mechanically executable revert. No file is edited and no working tree is left
    modified: the pre-fix body is a callable in this module, driven by the same seed and the same
    double the router tests use, so the five assertion helpers can be pointed straight at it.
    """
    db = db or FakeSupabase()
    amounts: List[int] = []

    def provider_call(amount_minor: int, sub_id: str) -> Dict[str, Any]:
        db.note_provider_call("stripe")
        amounts.append(amount_minor)
        if provider == "raises":
            raise RuntimeError("gateway refused the session")
        if provider == "slow":
            # No deadline exists in the pre-fix path, so this simply blocks and then succeeds.
            time.sleep(0.05)
        return {"url": "https://provider.example/checkout/1", "id": "cs_pre_fix"}

    status_code = 200
    body: Dict[str, Any] = {}
    raised: Optional[BaseException] = None
    try:
        body = _run_coroutine(
            PRE_FIX_CHECKOUT(
                lib_id=LISTING_ID,
                user_id=CALLER_ID,
                currency="USD",
                svc=db,
                provider_call=provider_call,
            )
        )
    except HTTPException as exc:
        raised = exc
        status_code = exc.status_code
        body = {"detail": exc.detail}
    except Exception as exc:  # KeyError: 0 — the unhandled shape FastAPI turns into a bare 500
        raised = exc
        status_code = 500
        body = {}

    return Outcome(
        status_code=status_code,
        body=body if isinstance(body, dict) else {},
        db=db,
        provider_amounts=amounts,
        deadlines_seen=[],  # the pre-fix path applies no deadline at all
        raised=raised,
        origin="in-process revert (PRE_FIX_CHECKOUT)",
    )


# ══════════════════════════════════════════════════════════════════════════
# THE FIVE ASSERTIONS, as reusable helpers
# ══════════════════════════════════════════════════════════════════════════


def assert_completes_rather_than_raising(outcome: Outcome) -> None:
    """ASSERTION 1 — the endpoint completes for a purchasable Listing (Requirement 9.11)."""
    assert outcome.raised is None or outcome.status_code == 200, (
        f"[{outcome.origin}] the checkout raised {type(outcome.raised).__name__}: "
        f"{outcome.raised!r}. Indexing a .single() response (a dict) as a list — "
        f"`strat = resp.data[0]` — raises KeyError: 0 outside the try, so FastAPI answers a "
        f"bare 500 and the endpoint completes for NO Listing (Requirement 9.11)"
    )
    assert outcome.status_code == 200, (
        f"[{outcome.origin}] POST /api/library/{{id}}/checkout did not complete: HTTP "
        f"{outcome.status_code}, body {outcome.body!r}. The .single() response is a dict; "
        f"`resp.data[0]` on it raises KeyError: 0 (Requirement 9.11)"
    )
    for key in ("subscription_id", "provider", "provider_reference", "amount_minor"):
        assert key in outcome.body, (
            f"[{outcome.origin}] the completed response is missing {key!r}: {outcome.body!r}"
        )


def assert_charged_amount_is_1999(outcome: Outcome) -> None:
    """ASSERTION 2 — a $19.99 Listing is charged exactly 1999, never 1998 (Req 9.2, 10.1)."""
    assert "amount_minor" in outcome.body, (
        f"[{outcome.origin}] the response body carries no 'amount_minor', so the charged "
        f"amount is not reported in Minor_Units at all: {outcome.body!r} "
        f"(Requirements 9.2, 10.1)"
    )
    amount = outcome.body["amount_minor"]
    assert amount == PRICE_MINOR_1999, (
        f"[{outcome.origin}] the response reports {amount} Minor_Units for a "
        f"${PRICE_MAJOR_TEXT} Listing; it must be exactly {PRICE_MINOR_1999}. "
        f"int(float('{PRICE_MAJOR_TEXT}') * 100) is {PRE_FIX_AMOUNT_1998} (Requirement 9.2)"
    )
    assert amount != PRE_FIX_AMOUNT_1998
    assert isinstance(amount, int) and not isinstance(amount, bool), (
        f"[{outcome.origin}] amount_minor is {type(amount).__name__}, not an int of "
        f"Minor_Units (Requirement 10.3)"
    )

    assert outcome.provider_amounts == [PRICE_MINOR_1999], (
        f"[{outcome.origin}] the provider was asked to charge {outcome.provider_amounts}, not "
        f"[{PRICE_MINOR_1999}] — the amount the response reports and the amount the provider "
        f"is asked for must be the same integer (Requirement 9.2)"
    )

    payloads = outcome.db.inserted_payloads("library_subscriptions")
    assert payloads, f"[{outcome.origin}] no library_subscriptions insert reached the driver"
    assert payloads[0].get("price_minor") == PRICE_MINOR_1999, (
        f"[{outcome.origin}] the persisted row records "
        f"price_minor={payloads[0].get('price_minor')!r}, not {PRICE_MINOR_1999} "
        f"(Requirement 10.1)"
    )


def assert_pending_insert_records_split_and_omits_expires_at(outcome: Outcome) -> None:
    """ASSERTION 3 — both split components present, ``expires_at`` absent (Req 9.3, 10.1)."""
    payloads = outcome.db.inserted_payloads("library_subscriptions")
    assert len(payloads) == 1, (
        f"[{outcome.origin}] expected exactly one PENDING insert, saw {len(payloads)}: "
        f"{payloads!r} (Requirement 9.3 — the row is written in one committed statement)"
    )
    payload = payloads[0]

    assert payload.get("status") == "pending", (
        f"[{outcome.origin}] the inserted row's status is {payload.get('status')!r}, "
        f"not 'pending'"
    )
    assert payload.get("owner_share_minor") == OWNER_SHARE_1799, (
        f"[{outcome.origin}] the PENDING insert records "
        f"owner_share_minor={payload.get('owner_share_minor')!r}; it must be "
        f"{OWNER_SHARE_1799} = (1999 * 90) // 100 (Requirement 10.1)"
    )
    assert payload.get("platform_fee_minor") == PLATFORM_FEE_200, (
        f"[{outcome.origin}] the PENDING insert records "
        f"platform_fee_minor={payload.get('platform_fee_minor')!r}; it must be "
        f"{PLATFORM_FEE_200} = 1999 - 1799 (Requirement 10.1)"
    )
    # chk_ls_split_conserved, evaluated against the one row image.
    assert (
        payload["owner_share_minor"] + payload["platform_fee_minor"] == payload["price_minor"]
    ), f"[{outcome.origin}] the split does not conserve the amount: {payload!r}"

    assert "expires_at" not in payload, (
        f"[{outcome.origin}] the PENDING insert carries 'expires_at' "
        f"({payload.get('expires_at')!r}). It must be OMITTED, not present-and-NULL: "
        f"check_deployment_permission reads a null expiry as a PERPETUAL subscription, and "
        f"chk_ls_active_has_period only makes an ACTIVE row without a period unrepresentable "
        f"if the column is left alone (Requirement 9.3)"
    )
    for column in ("period_start", "period_expiry"):
        assert column not in payload, (
            f"[{outcome.origin}] the PENDING insert carries {column!r}; a PENDING row has no "
            f"period (Requirement 9.3)"
        )
    leaked = cs.OMITTED_ON_PENDING & set(payload)
    assert not leaked, (
        f"[{outcome.origin}] the PENDING insert carries columns OMITTED_ON_PENDING forbids: "
        f"{sorted(leaked)}"
    )

    stored = outcome.db.subscription_rows
    assert len(stored) == 1, f"[{outcome.origin}] expected one stored row, saw {stored!r}"
    assert "expires_at" not in stored[0], (
        f"[{outcome.origin}] a later statement added 'expires_at' to the PENDING row: "
        f"{stored[0]!r}"
    )


def assert_provider_failure_leaves_payment_failed_row(outcome: Outcome) -> None:
    """ASSERTION 4 — the row survives a provider failure, in ``payment_failed`` (Req 9.4)."""
    assert outcome.status_code == 502, (
        f"[{outcome.origin}] a provider failure answered HTTP {outcome.status_code}, not the "
        f"502 MARKETPLACE_CHECKOUT_UNAVAILABLE the catalogue defines: {outcome.body!r}"
    )
    error = outcome.body.get("error") or {}
    assert error.get("code") == MARKETPLACE_CHECKOUT_UNAVAILABLE, (
        f"[{outcome.origin}] the failure body carries code {error.get('code')!r}, not "
        f"{MARKETPLACE_CHECKOUT_UNAVAILABLE!r}: {outcome.body!r}"
    )

    assert outcome.db.delete_count("library_subscriptions") == 0, (
        f"[{outcome.origin}] {outcome.db.delete_count('library_subscriptions')} delete "
        f"statement(s) were issued against library_subscriptions. A failed provider call must "
        f"never delete the row — that destroys the only record the attempt was made "
        f"(Requirement 9.4)"
    )

    rows = outcome.db.subscription_rows
    assert len(rows) == 1, (
        f"[{outcome.origin}] the PENDING row must survive a provider failure; the table now "
        f"holds {rows!r} (Requirement 9.4)"
    )
    row = rows[0]
    assert row.get("status") == "payment_failed", (
        f"[{outcome.origin}] the surviving row is in status {row.get('status')!r}, not "
        f"'payment_failed' (Requirement 9.4)"
    )
    assert row.get("failure_cause"), (
        f"[{outcome.origin}] the surviving row records no failure_cause: {row!r} "
        f"(Requirement 9.4)"
    )
    assert row.get("failed_at"), (
        f"[{outcome.origin}] the surviving row records no failed_at: {row!r}"
    )
    # The attempt stays auditable: the amount and the split are still on the row.
    assert row.get("price_minor") == PRICE_MINOR_1999
    assert row.get("owner_share_minor") == OWNER_SHARE_1799
    assert row.get("platform_fee_minor") == PLATFORM_FEE_200


def assert_deadline_abandons_and_marks_payment_failed(outcome: Outcome) -> None:
    """ASSERTION 5 — the 30-second deadline abandons the attempt, same failure path (Req 9.13)."""
    assert outcome.deadlines_seen, (
        f"[{outcome.origin}] no deadline reached the provider call at all — the attempt would "
        f"wait on the provider indefinitely (Requirement 9.13)"
    )
    assert outcome.deadlines_seen == [cs.PROVIDER_DEADLINE_SECONDS] == [30], (
        f"[{outcome.origin}] the deadline applied on the router path was "
        f"{outcome.deadlines_seen!r}; Requirement 9.13 fixes it at 30 seconds"
    )

    assert outcome.status_code == 502, (
        f"[{outcome.origin}] an abandoned attempt answered HTTP {outcome.status_code}, not "
        f"502: {outcome.body!r}"
    )
    error = outcome.body.get("error") or {}
    assert error.get("code") == MARKETPLACE_CHECKOUT_UNAVAILABLE, (
        f"[{outcome.origin}] the deadline body carries code {error.get('code')!r}, not "
        f"{MARKETPLACE_CHECKOUT_UNAVAILABLE!r}"
    )
    assert (error.get("details") or {}).get("timed_out") is True, (
        f"[{outcome.origin}] the deadline answer does not report timed_out: {outcome.body!r}"
    )

    # And then the SAME PAYMENT_FAILED path as any other provider failure.
    assert outcome.db.delete_count("library_subscriptions") == 0, (
        f"[{outcome.origin}] a timed-out attempt deleted the row (Requirement 9.4)"
    )
    rows = outcome.db.subscription_rows
    assert len(rows) == 1, f"[{outcome.origin}] the row must survive the timeout: {rows!r}"
    row = rows[0]
    assert row.get("status") == "payment_failed", (
        f"[{outcome.origin}] a timed-out attempt left the row in {row.get('status')!r}, not "
        f"'payment_failed' (Requirements 9.13, 9.4)"
    )
    assert row.get("failure_cause") and "deadline" in row["failure_cause"], (
        f"[{outcome.origin}] the timeout is not recorded as the failure_cause: {row!r}"
    )
    assert row.get("failed_at")
    assert row.get("provider_reference") is None, (
        f"[{outcome.origin}] an abandoned attempt recorded a provider_reference: {row!r}"
    )


#: The five, in the order the task lists them. Read by the in-process revert test, so "each of
#: the five fails against the pre-fix code" iterates the same functions the five tests call.
THE_FIVE_ASSERTIONS = (
    ("1 completes rather than raising", assert_completes_rather_than_raising, "ok"),
    ("2 charged amount is 1999", assert_charged_amount_is_1999, "ok"),
    (
        "3 split recorded, expires_at omitted",
        assert_pending_insert_records_split_and_omits_expires_at,
        "ok",
    ),
    (
        "4 provider failure leaves payment_failed",
        assert_provider_failure_leaves_payment_failed_row,
        "raises",
    ),
    (
        "5 deadline abandons the attempt",
        assert_deadline_abandons_and_marks_payment_failed,
        "slow",
    ),
)


# ══════════════════════════════════════════════════════════════════════════
# 1-5. THE FIVE ROUTER-LEVEL ASSERTIONS AGAINST THE CURRENT HANDLER
# ══════════════════════════════════════════════════════════════════════════


def test_checkout_completes_rather_than_raising():
    """The endpoint completes for a purchasable Listing.

    _Requirements: 9.11_
    """
    assert_completes_rather_than_raising(run_checkout(provider="ok"))


def test_charged_amount_is_1999_minor_units():
    """A ``$19.99`` Listing is charged exactly 1999 Minor_Units — in the body, in the row and at
    the provider.

    _Requirements: 9.2, 10.1_
    """
    assert_charged_amount_is_1999(run_checkout(provider="ok"))


def test_pending_insert_records_the_split_and_omits_expires_at():
    """The ``PENDING`` insert carries ``owner_share_minor`` 1799 and ``platform_fee_minor`` 200,
    and no ``expires_at``.

    _Requirements: 9.3, 10.1_
    """
    assert_pending_insert_records_split_and_omits_expires_at(run_checkout(provider="ok"))


def test_provider_failure_leaves_a_payment_failed_row_and_never_deletes():
    """A provider failure leaves the row present in ``payment_failed`` with a ``failure_cause``;
    zero ``delete`` statements are issued against ``library_subscriptions``.

    _Requirements: 9.4_
    """
    assert_provider_failure_leaves_payment_failed_row(run_checkout(provider="raises"))


def test_thirty_second_deadline_abandons_the_attempt_and_marks_payment_failed():
    """The 30-second deadline abandons the attempt and applies the same ``PAYMENT_FAILED`` path.

    _Requirements: 9.13, 9.4_
    """
    assert_deadline_abandons_and_marks_payment_failed(run_checkout(provider="slow"))


# ══════════════════════════════════════════════════════════════════════════
# THE IN-PROCESS REVERT — each of the five fails against the pre-fix body
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "label,assertion,provider",
    THE_FIVE_ASSERTIONS,
    ids=[row[0].split()[0] for row in THE_FIVE_ASSERTIONS],
)
def test_the_five_assertions_all_fail_under_the_in_process_revert(label, assertion, provider):
    """Every one of the five assertions fails against :func:`PRE_FIX_CHECKOUT`.

    This is what makes the module a regression guard rather than a description of the current
    behaviour: the assertions are pointed at the pre-fix body — same seed, same driver, same
    helper functions — and each must reject it.
    """
    outcome = run_pre_fix_checkout(provider=provider)
    with pytest.raises(AssertionError) as raised:
        assertion(outcome)
    assert "in-process revert" in str(raised.value), (
        "the failure message must name the revert it came from, so a future reader can tell "
        "which side failed"
    )


def test_the_in_process_revert_reproduces_each_named_defect():
    """The revert exhibits defects 1-4 by name, so the parametrised test above is not passing
    for some unrelated reason.

    _Requirements: 9.2, 9.3, 9.4, 9.11_
    """
    # DEFECT 1 — the .single() dict indexed as a list.
    broken = run_pre_fix_checkout(provider="ok")
    assert isinstance(broken.raised, KeyError) and broken.raised.args == (0,), (
        f"the revert must raise KeyError: 0 from `strat = resp.data[0]`; it raised "
        f"{broken.raised!r}"
    )
    assert broken.status_code == 500
    # Nothing else happened: the handler died before it could insert or charge anything.
    assert broken.db.inserted_payloads("library_subscriptions") == []
    assert broken.provider_amounts == []

    # DEFECTS 2-4 are downstream of the KeyError, so reach them by handing the pre-fix body the
    # row shape it expected — a LIST — which is the only difference. Every other defect is then
    # observable.
    class _ListShapedDb(FakeSupabase):
        """The same double, answering the Listing read as a one-row LIST.

        This isolates defect 1 from defects 2-4: with the shape the pre-fix code assumed,
        ``resp.data[0]`` succeeds and the remaining three defects become observable.
        """

        def _execute(self, q: _Query) -> Any:
            if q.table_name == "library_strategies":
                self.ops.append((q.op, q.table_name))
                self.statements.append(q)
                if not self._matches_listing(q):
                    return _Resp([])
                return _Resp([copy.deepcopy(self.listing_row)])
            return super()._execute(q)

    ok = run_pre_fix_checkout(provider="ok", db=_ListShapedDb())
    assert ok.status_code == 200, ok.body

    # DEFECT 2 — 1998, not 1999.
    assert ok.provider_amounts == [PRE_FIX_AMOUNT_1998], (
        f"the pre-fix arithmetic must charge {PRE_FIX_AMOUNT_1998}; it charged "
        f"{ok.provider_amounts}"
    )
    assert "amount_minor" not in ok.body

    # DEFECT 3 — expires_at present and NULL, and neither split component recorded.
    payload = ok.db.inserted_payloads("library_subscriptions")[0]
    assert "expires_at" in payload and payload["expires_at"] is None, (
        f'the pre-fix insert must carry "expires_at": None; it carried {payload!r}'
    )
    assert "owner_share_minor" not in payload
    assert "platform_fee_minor" not in payload
    assert "price_minor" not in payload

    # DEFECT 4 — a provider failure DELETES the row.
    failed = run_pre_fix_checkout(provider="raises", db=_ListShapedDb())
    assert failed.status_code == 500
    assert failed.db.delete_count("library_subscriptions") == 1, (
        "the pre-fix failure path must issue exactly one delete against "
        "library_subscriptions — that is the audit trail it destroyed"
    )
    assert failed.db.subscription_rows == [], (
        "the pre-fix failure path must leave no row behind at all"
    )

    # And no deadline: the pre-fix path never applies one.
    slow = run_pre_fix_checkout(provider="slow", db=_ListShapedDb())
    assert slow.deadlines_seen == []
    assert slow.status_code == 200, "the pre-fix path has no deadline to abandon the attempt"


# ══════════════════════════════════════════════════════════════════════════
# THE SOURCE GUARD — the pre-fix constructs are absent from the handler
# ══════════════════════════════════════════════════════════════════════════


def _handler_ast() -> ast.AST:
    """The current ``create_marketplace_checkout`` as an AST, docstring excluded.

    An AST walk rather than a text search, and deliberately so: the handler's own docstring
    NAMES every construct below (``select("*")``, ``float(``, ``resp.data[0]``, ``delete()``,
    ``STRIPE_SECRET_KEY``, ``sk_test_dummy``) in order to explain what it replaced. A text scan
    would fail on the explanation rather than on the code.
    """
    source = textwrap.dedent(inspect.getsource(library_router.create_marketplace_checkout))
    tree = ast.parse(source)
    func = tree.body[0]
    assert isinstance(func, (ast.AsyncFunctionDef, ast.FunctionDef))
    body = list(func.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]  # drop the docstring
    module = ast.Module(body=body, type_ignores=[])
    return module


def _module_ast() -> ast.Module:
    return ast.parse(LIBRARY_PATH.read_text(encoding="utf-8"), str(LIBRARY_PATH))


def _string_constants_excluding_docstrings(tree: ast.AST) -> set:
    """Every string constant in ``tree`` except docstrings.

    Docstrings are excluded for the same reason ``_handler_ast`` drops the handler's: this file's
    prose explains what was removed, and an explanation is not an occurrence.
    """
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    }


def _table_of_chain(node: ast.AST) -> Optional[str]:
    """The ``.table("x")`` argument reached by descending a fluent chain from ``node``."""
    current: Any = node
    for _ in range(40):
        if isinstance(current, ast.Call):
            func = current.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "table"
                and current.args
                and isinstance(current.args[0], ast.Constant)
            ):
                return str(current.args[0].value)
            current = func
        elif isinstance(current, ast.Attribute):
            current = current.value
        elif isinstance(current, ast.Subscript):
            current = current.value
        else:
            return None
    return None


def test_the_pre_fix_constructs_are_absent_from_the_handler():
    """None of the pre-fix constructs survives in ``create_marketplace_checkout``.

    Five structural statements, one per construct the revert would restore:
    no ``.select("*")``, no ``float(`` in the amount path, no indexing of a ``.single()``
    result, no ``.delete()`` on ``library_subscriptions``, and no inline ``import stripe`` /
    ``import razorpay`` / ``STRIPE_SECRET_KEY`` default.

    _Requirements: 9.2, 9.4, 9.11, 10.1_
    """
    handler = _handler_ast()

    # ── no .select("*") ──────────────────────────────────────────────────
    star_selects = [
        node.lineno
        for node in ast.walk(handler)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "select"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "*"
    ]
    assert not star_selects, (
        f'.select("*") is back in the handler at line(s) {star_selects}; the read is '
        f"checkout_service.CHECKOUT_LISTING_SELECT (Requirement 6.1)"
    )

    # ── no float() anywhere on the amount path ───────────────────────────
    float_calls = [
        node.lineno
        for node in ast.walk(handler)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "float"
    ]
    assert not float_calls, (
        f"float() is back in the handler at line(s) {float_calls}; "
        f'int(float("19.99") * 100) is {PRE_FIX_AMOUNT_1998}, not {PRICE_MINOR_1999} '
        f"(Requirements 9.2, 10.3)"
    )
    float_literals = [
        node.lineno
        for node in ast.walk(handler)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]
    assert not float_literals, f"a float literal is back at line(s) {float_literals}"

    # ── no `resp.data[0]`-style indexing of a .single() result ───────────
    data_subscripts = [
        node.lineno
        for node in ast.walk(handler)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "data"
    ]
    assert not data_subscripts, (
        f"a driver `.data` result is indexed at line(s) {data_subscripts}. `.single()` returns "
        f"a dict, so `resp.data[0]` raises KeyError: 0 and the endpoint completes for no "
        f"Listing (Requirement 9.11)"
    )
    # Nor `.execute().data` returned or bound at all — the handler holds no driver response.
    execute_calls = [
        node.lineno
        for node in ast.walk(handler)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
    ]
    assert not execute_calls, (
        f"the handler executes a driver statement itself at line(s) {execute_calls}; the "
        f"service owns the transaction (task 18.2)"
    )

    # ── no .delete() on library_subscriptions ────────────────────────────
    handler_deletes = [
        node.lineno
        for node in ast.walk(handler)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "delete"
    ]
    assert not handler_deletes, (
        f".delete() is back in the handler at line(s) {handler_deletes}; a failed provider "
        f"call must UPDATE the row to payment_failed, never delete it (Requirement 9.4)"
    )
    module_deletes = [
        node.lineno
        for node in ast.walk(_module_ast())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "delete"
        and _table_of_chain(node.func.value) == "library_subscriptions"
    ]
    assert not module_deletes, (
        f'library.py issues .delete() on library_subscriptions at line(s) {module_deletes}; '
        f"a Subscription row is the record of a payment attempt and is never deleted "
        f"(Requirement 9.4)"
    )

    # ── no bare `except:` (the clause the delete hid inside) ─────────────
    bare_excepts = [
        node.lineno
        for node in ast.walk(handler)
        if isinstance(node, ast.ExceptHandler) and node.type is None
    ]
    assert not bare_excepts, f"a bare `except:` is back at line(s) {bare_excepts}"

    # ── no inline SDK import and no credential default ───────────────────
    imports = [
        alias.name
        for node in ast.walk(handler)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in getattr(node, "names", [])
    ]
    for sdk in ("stripe", "razorpay"):
        assert not any(str(name).split(".")[0] == sdk for name in imports), (
            f"`import {sdk}` is back in the handler; both SDKs live in "
            f"checkout_service.default_provider_session_factory (Requirement 9.1)"
        )

    #: String constants in the handler, docstring already excluded — so the explanation of what
    #: was removed does not register as the thing itself.
    handler_strings = {
        node.value
        for node in ast.walk(handler)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    for forbidden in ("STRIPE_SECRET_KEY", "sk_test_dummy", "rzp_test_dummy"):
        assert forbidden not in handler_strings, (
            f"{forbidden!r} is back in the handler; credentials are validated once, in "
            f"billing._validate_keys, which refuses a placeholder on a production path"
        )

    # ── and the same two, file-wide: a reverted inline import would land in library.py ──
    module = _module_ast()
    module_imports = [
        (alias.name, node.lineno)
        for node in ast.walk(module)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in getattr(node, "names", [])
    ]
    for sdk in ("stripe", "razorpay"):
        offenders = [line for name, line in module_imports if str(name).split(".")[0] == sdk]
        assert not offenders, (
            f"library.py imports {sdk} at line(s) {offenders}; the router imports no payment "
            f"SDK at all (Requirement 9.1)"
        )
    module_strings = _string_constants_excluding_docstrings(module)
    for forbidden in ("STRIPE_SECRET_KEY", "sk_test_dummy", "rzp_test_dummy"):
        assert forbidden not in module_strings, (
            f"{forbidden!r} appears in library.py outside a docstring; no router reads a "
            f"provider credential, least of all with a test-mode default"
        )


def test_the_handler_delegates_to_the_checkout_service():
    """What replaced the pre-fix body: one ``await _checkout_service.create_checkout(...)``.

    Recorded as an assertion so a revert cannot pass the guard above by deleting the handler's
    logic without restoring the delegation.

    _Requirements: 9.2, 9.3_
    """
    handler = _handler_ast()
    delegations = [
        node
        for node in ast.walk(handler)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_checkout"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "_checkout_service"
    ]
    assert len(delegations) == 1, (
        "the handler must call _checkout_service.create_checkout exactly once; found "
        f"{len(delegations)}"
    )
    passed = {kw.arg for kw in delegations[0].keywords}
    assert {"caller", "listing_id", "currency", "supabase"} <= passed, (
        f"the delegation is missing argument(s): {passed!r}"
    )
    assert "deadline_seconds" not in passed, (
        "the handler must not override the provider deadline; Requirement 9.13 fixes it at the "
        "service's 30-second default"
    )


def test_the_declared_deadline_the_router_path_uses_is_thirty_seconds():
    """The value the handler's call carries into the provider step is 30 (Requirement 9.13)."""
    assert cs.PROVIDER_DEADLINE_SECONDS == 30
    default = inspect.signature(cs.create_checkout).parameters["deadline_seconds"].default
    assert default == 30


# ══════════════════════════════════════════════════════════════════════════
# NON-VACUITY
# ══════════════════════════════════════════════════════════════════════════


def test_the_seed_makes_1998_genuinely_derivable():
    """The Listing carries the legacy ``price`` mirror the pre-fix arithmetic read.

    Without it, "the charged amount is 1999" would pass against the pre-fix code for want of an
    input rather than because the arithmetic is right.
    """
    row = _seeded_listing_row()
    assert row["price"] == PRICE_MAJOR_TEXT
    assert int(float(row["price"]) * 100) == PRE_FIX_AMOUNT_1998
    assert row["price_minor"] == PRICE_MINOR_1999
    # And the row satisfies BOTH handlers' purchasability gates, so a revert fails on the
    # defect rather than on the fixture.
    assert row["is_active"] is True
    assert row["moderation_status"] in ("approved", "featured")
    assert row["marketplace_submissions"][0]["submission_state"] == "PUBLISHED"


def test_the_listing_read_answers_with_the_single_shape():
    """The double returns the Listing as a mapping — the shape ``.single()`` produces.

    Load-bearing: it is what makes ``resp.data[0]`` raise ``KeyError: 0``. A double that
    answered with a list would hide defect 1 entirely.
    """
    db = FakeSupabase()
    response = (
        db.table("library_strategies")
        .select("*")
        .eq("id", LISTING_ID)
        .eq("is_active", True)
        .in_("moderation_status", ["approved", "featured"])
        .single()
        .execute()
    )
    assert isinstance(response.data, Mapping)
    with pytest.raises(KeyError):
        _ = response.data[0]
    # And the service's own normaliser reads the same shape as one row.
    assert cs._rows(response)[0]["price_minor"] == PRICE_MINOR_1999


def test_the_pre_fix_construct_record_is_complete():
    """:data:`PRE_FIX_CONSTRUCT` names all four defects and points at a runnable revert.

    Task 35.2 reads this structure, so its shape is asserted rather than assumed.
    """
    assert PRE_FIX_CONSTRUCT["handler"].endswith("create_marketplace_checkout")
    assert set(PRE_FIX_CONSTRUCT["defects"]) == {
        "single_indexed_as_list",
        "float_amount",
        "null_expires_at_and_no_split",
        "delete_on_provider_failure",
    }
    named = {
        entry["asserted_by"]
        for entry in PRE_FIX_CONSTRUCT["defects"].values()
    }
    defined = set(globals())
    assert named <= defined, f"PRE_FIX_CONSTRUCT names missing assertions: {named - defined}"
    for entry in PRE_FIX_CONSTRUCT["defects"].values():
        assert entry["was"] and entry["now"] and entry["symptom"] and entry["requirement"]
    assert "PRE_FIX_CHECKOUT" in PRE_FIX_CONSTRUCT["revert_in_process"]
    assert callable(PRE_FIX_CHECKOUT)
