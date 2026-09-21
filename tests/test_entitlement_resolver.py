"""
tests/test_entitlement_resolver.py

Focused unit tests for ``backend_app/backend/marketplace/entitlement_resolver.py`` (task 17.1).

These exercise :func:`resolve` against a FAKE Supabase double that returns scripted rows for the
one embedded round trip (``library_strategies`` embedding ``marketplace_submissions`` and the
caller's ``library_subscriptions``) and for the current-version read
(``strategy_versions``) - the same injected-``supabase`` pattern ``test_submission_service.py``
uses, so the whole admission decision is checkable without a database or a TestClient.

What is asserted
----------------
* Purity: the module imports no FastAPI and no ``errors.py`` at source level (AST).
* The six reasons: OWNED, SUBSCRIBED, NOT_SUBSCRIBED, EXPIRED, LISTING_UNAVAILABLE and
  SUBSCRIPTION_SUSPENDED, each with its ``entitling`` flag and wire code.
* Sweep-independence (Requirement 11.7, P-11): a subscription whose stored ``status`` is still
  ``'active'`` but whose ``period_expiry`` is null or in the past is EXPIRED, not SUBSCRIBED.
* A SUSPENDED / UNPUBLISHED *Listing* still entitles an ACTIVE, unexpired Subscription
  (Requirement 4.11), distinct from a suspended *Subscription*.
* An unresolvable ``source_strategy_id`` gives LISTING_UNAVAILABLE (-> 409), disclosing no
  identifiers beyond the listing key.
* Identity comes only from ``caller``: a subscription belonging to another user does not entitle,
  and a request-supplied subscription / owner / tenant identifier riding along changes nothing -
  the spoofed decision is asserted EQUAL to the bare-session decision (Requirements 7.7, 21.1).
* The measured round-trip count: the admission decision is ONE read of ``library_strategies``
  (the embedded join), plus - only on a would-be entitling path - design.md's
  ``current_version_of(...)`` version read. So a non-entitling decision measures 1 and an
  entitling one measures 2, and neither grows with the number of embedded rows.
* A read that does not complete raises ``EntitlementReadFailed`` (``MARKETPLACE_READ_FAILED``,
  503) rather than becoming ``NOT_SUBSCRIBED`` or ``LISTING_UNAVAILABLE`` - both for a driver
  exception and for a completed response carrying an ``error`` envelope (Requirement 30.5).
* ``entitling`` cannot disagree with ``reason``: the pairing is enforced on construction.
* The wire codes agree with the shared error catalogue.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend.marketplace import entitlement_resolver as er
from backend_app.backend.marketplace.entitlement_resolver import (
    ENTITLING_REASONS,
    Entitlement,
    EntitlementReadFailed,
    EntitlementReason,
    WIRE_CODE_FOR_REASON,
    resolve,
)

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "backend_app"
    / "backend"
    / "marketplace"
    / "entitlement_resolver.py"
)

NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
FUTURE = NOW + timedelta(days=10)
PAST = NOW - timedelta(days=10)

CALLER = {"id": "user-1"}
OWNER = {"id": "owner-9"}
LISTING_ID = "listing-abc"
STRATEGY_ID = "strat-xyz"


# ══════════════════════════════════════════════════════════════════════════
# A fake Supabase double
# ══════════════════════════════════════════════════════════════════════════


class _Resp:
    def __init__(self, data: Any):
        self.data = data


class _Query:
    """A recording query builder mimicking the supabase-py fluent chain."""

    def __init__(self, table: str, client: "FakeSupabase"):
        self.table_name = table
        self.client = client
        self.filters: List[tuple] = []
        self.cols: Optional[str] = None

    def select(self, cols):
        self.cols = cols
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def execute(self):
        return self.client._execute(self)


class FakeSupabase:
    """Returns scripted rows keyed by table name; records every executed query."""

    def __init__(self, listing_rows=None, version_rows=None, raise_on=None, error_on=None):
        self._listing_rows = listing_rows if listing_rows is not None else []
        self._version_rows = version_rows if version_rows is not None else []
        self.raise_on = raise_on or set()
        # Tables whose read "completes" but answers with a PostgREST error envelope rather than
        # rows - the shape that must NOT be read as "no rows" (Requirement 30.5).
        self.error_on = error_on or set()
        self.calls: List[_Query] = []

    def table(self, name):
        return _Query(name, self)

    def _execute(self, q: _Query):
        self.calls.append(q)
        if q.table_name in self.raise_on:
            raise RuntimeError(f"read failed on {q.table_name}")
        if q.table_name in self.error_on:
            return {"data": None, "error": {"message": f"boom on {q.table_name}"}}
        if q.table_name == "library_strategies":
            return _Resp(list(self._listing_rows))
        if q.table_name == "strategy_versions":
            return _Resp(list(self._version_rows))
        return _Resp([])

    def tables_read(self):
        return [c.table_name for c in self.calls]

    def round_trips(self) -> int:
        """Every ``.execute()`` this double served - the measured round-trip count."""
        return len(self.calls)

    def round_trips_per_table(self) -> "Counter[str]":
        return Counter(self.tables_read())


def _listing_row(
    *,
    author_id=OWNER["id"],
    source_strategy_id=STRATEGY_ID,
    submission_state="PUBLISHED",
    subscription=None,
):
    submissions = (
        [] if submission_state is None else [{"submission_state": submission_state}]
    )
    subs = [] if subscription is None else [subscription]
    return {
        "id": LISTING_ID,
        "author_id": author_id,
        "source_strategy_id": source_strategy_id,
        "source_cloning_enabled": True,
        "marketplace_submissions": submissions,
        "library_subscriptions": subs,
    }


def _subscription(*, status="active", period_expiry=FUTURE, user_id=CALLER["id"], sub_id="sub-1"):
    return {
        "id": sub_id,
        "user_id": user_id,
        "status": status,
        "period_expiry": period_expiry.isoformat() if isinstance(period_expiry, datetime) else period_expiry,
    }


def _version_rows(n=1, is_draft=False):
    return [
        {"id": f"ver-{i}", "strategy_id": STRATEGY_ID, "version": i + 1, "is_draft": is_draft}
        for i in range(n)
    ]


def _run(coro):
    return asyncio.run(coro)


# ══════════════════════════════════════════════════════════════════════════
# Purity
# ══════════════════════════════════════════════════════════════════════════


def test_module_imports_no_fastapi_or_errors_at_source_level():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    roots = set()
    module_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
            module_names.add(node.module)
    assert "fastapi" not in roots
    # errors.py imports FastAPI; importing it would break purity.
    assert not any(m.endswith("errors") for m in module_names)


# ══════════════════════════════════════════════════════════════════════════
# The six reasons
# ══════════════════════════════════════════════════════════════════════════


def test_owned_entitles_regardless_of_subscription():
    fake = FakeSupabase(
        listing_rows=[_listing_row(author_id=CALLER["id"])],
        version_rows=_version_rows(2),
    )
    ent = _run(resolve(CALLER, LISTING_ID, fake, NOW))
    assert ent.entitling is True
    assert ent.reason is EntitlementReason.OWNED
    assert ent.wire_code is None
    # The current (highest) version is resolved for the owner.
    assert ent.version_id == "ver-1"  # version=2 is highest; id ver-1 by construction order
    assert ent.source_strategy_id == STRATEGY_ID


def test_subscribed_active_and_unexpired_entitles():
    fake = FakeSupabase(
        listing_rows=[_listing_row(subscription=_subscription())],
        version_rows=_version_rows(1),
    )
    ent = _run(resolve(CALLER, LISTING_ID, fake, NOW))
    assert ent.entitling is True
    assert ent.reason is EntitlementReason.SUBSCRIBED
    assert ent.wire_code is None
    assert ent.subscription_id == "sub-1"
    assert ent.version_id == "ver-0"


def test_no_subscription_row_is_not_subscribed():
    fake = FakeSupabase(listing_rows=[_listing_row(subscription=None)])
    ent = _run(resolve(CALLER, LISTING_ID, fake, NOW))
    assert ent.entitling is False
    assert ent.reason is EntitlementReason.NOT_SUBSCRIBED
    assert ent.wire_code == "MARKETPLACE_NOT_SUBSCRIBED"
    # No version read is issued on the cheap NOT_SUBSCRIBED path.
    assert "strategy_versions" not in fake.tables_read()


def test_expired_period_in_past_even_when_status_still_active_is_expired():
    # Sweep-independence (Req 11.7, P-11): status='active' but period_expiry is in the past.
    fake = FakeSupabase(
        listing_rows=[_listing_row(subscription=_subscription(status="active", period_expiry=PAST))],
        version_rows=_version_rows(1),
    )
    ent = _run(resolve(CALLER, LISTING_ID, fake, NOW))
    assert ent.entitling is False
    assert ent.reason is EntitlementReason.EXPIRED
    assert ent.wire_code == "MARKETPLACE_SUBSCRIPTION_EXPIRED"


def test_null_period_expiry_even_when_status_active_is_expired():
    fake = FakeSupabase(
        listing_rows=[_listing_row(subscription=_subscription(status="active", period_expiry=None))],
        version_rows=_version_rows(1),
    )
    ent = _run(resolve(CALLER, LISTING_ID, fake, NOW))
    assert ent.entitling is False
    assert ent.reason is EntitlementReason.EXPIRED


def test_now_equal_to_period_expiry_is_expired():
    # now >= period_expiry is non-entitling; the boundary itself is expired.
    fake = FakeSupabase(
        listing_rows=[_listing_row(subscription=_subscription(status="active", period_expiry=NOW))],
        version_rows=_version_rows(1),
    )
    ent = _run(resolve(CALLER, LISTING_ID, fake, NOW))
    assert ent.reason is EntitlementReason.EXPIRED


def test_expired_status_label_is_expired():
    fake = FakeSupabase(
        listing_rows=[_listing_row(subscription=_subscription(status="expired", period_expiry=FUTURE))],
        version_rows=_version_rows(1),
    )
    ent = _run(resolve(CALLER, LISTING_ID, fake, NOW))
    assert ent.reason is EntitlementReason.EXPIRED


def test_suspended_subscription_is_its_own_reason():
    fake = FakeSupabase(
        listing_rows=[_listing_row(subscription=_subscription(status="suspended", period_expiry=FUTURE))],
        version_rows=_version_rows(1),
    )
    ent = _run(resolve(CALLER, LISTING_ID, fake, NOW))
    assert ent.entitling is False
    assert ent.reason is EntitlementReason.SUBSCRIPTION_SUSPENDED
    assert ent.wire_code == "MARKETPLACE_OPERATION_NOT_PERMITTED"


@pytest.mark.parametrize("listing_state", ["SUSPENDED", "UNPUBLISHED"])
def test_suspended_or_unpublished_listing_still_entitles_active_sub(listing_state):
    # Req 4.11: a Listing going SUSPENDED / UNPUBLISHED does not revoke an already-paid,
    # unexpired subscription.
    fake = FakeSupabase(
        listing_rows=[
            _listing_row(submission_state=listing_state, subscription=_subscription())
        ],
        version_rows=_version_rows(1),
    )
    ent = _run(resolve(CALLER, LISTING_ID, fake, NOW))
    assert ent.entitling is True
    assert ent.reason is EntitlementReason.SUBSCRIBED


def test_missing_listing_is_listing_unavailable():
    fake = FakeSupabase(listing_rows=[])
    ent = _run(resolve(CALLER, LISTING_ID, fake, NOW))
    assert ent.entitling is False
    assert ent.reason is EntitlementReason.LISTING_UNAVAILABLE
    assert ent.wire_code == "MARKETPLACE_STRATEGY_UNAVAILABLE"
    # Nothing beyond the listing key is disclosed.
    assert ent.source_strategy_id is None
    assert ent.version_id is None
    assert ent.subscription_id is None


def test_unresolvable_source_strategy_id_is_listing_unavailable():
    # An ACTIVE, unexpired sub against a PUBLISHED listing, but the backing strategy resolves
    # to no live version -> LISTING_UNAVAILABLE (409), disclosing no owner identity.
    fake = FakeSupabase(
        listing_rows=[_listing_row(subscription=_subscription())],
        version_rows=[],  # no strategy_versions row
    )
    ent = _run(resolve(CALLER, LISTING_ID, fake, NOW))
    assert ent.entitling is False
    assert ent.reason is EntitlementReason.LISTING_UNAVAILABLE


def test_only_draft_versions_do_not_resolve():
    fake = FakeSupabase(
        listing_rows=[_listing_row(subscription=_subscription())],
        version_rows=_version_rows(2, is_draft=True),
    )
    ent = _run(resolve(CALLER, LISTING_ID, fake, NOW))
    assert ent.reason is EntitlementReason.LISTING_UNAVAILABLE


def test_listing_with_no_submission_is_unavailable_for_subscriber():
    fake = FakeSupabase(
        listing_rows=[_listing_row(submission_state=None, subscription=_subscription())],
        version_rows=_version_rows(1),
    )
    ent = _run(resolve(CALLER, LISTING_ID, fake, NOW))
    assert ent.entitling is False
    assert ent.reason is EntitlementReason.LISTING_UNAVAILABLE


# ══════════════════════════════════════════════════════════════════════════
# Identity comes only from the caller
# ══════════════════════════════════════════════════════════════════════════


def test_foreign_subscription_does_not_entitle():
    # A subscription row belonging to another user must not entitle this caller.
    fake = FakeSupabase(
        listing_rows=[
            _listing_row(subscription=_subscription(user_id="somebody-else"))
        ],
        version_rows=_version_rows(1),
    )
    ent = _run(resolve(CALLER, LISTING_ID, fake, NOW))
    assert ent.entitling is False
    assert ent.reason is EntitlementReason.NOT_SUBSCRIBED


def test_resolve_takes_no_identifier_parameter_beyond_the_session_and_the_listing():
    # The signature itself is the guarantee: there is nowhere for a request-supplied
    # subscription_id, owner_id, tenant_id or entitlement flag to enter (Req 7.7, 21.1, P-45).
    assert list(inspect.signature(resolve).parameters) == [
        "caller",
        "listing_id",
        "supabase",
        "now",
    ]


def test_request_supplied_identifiers_do_not_change_the_decision():
    """A caller carrying attacker-chosen identifiers decides exactly as a bare session does.

    The only subscription in the database belongs to somebody else, and the caller is not the
    author. A body/query/WS-supplied ``subscription_id``, ``owner_id``, ``tenant_id``,
    ``user_id`` or ``entitled`` flag riding along on the caller object must be ignored
    (Requirements 7.7, 21.1) - the decision is a function of the authenticated ``id`` and the
    stored rows alone.
    """
    spoofed = {
        "id": CALLER["id"],
        # Everything below is client-supplied noise the resolver must not read.
        "owner_id": OWNER["id"],
        "author_id": OWNER["id"],
        "user_id": "somebody-else",
        "tenant_id": "tenant-of-the-owner",
        "subscription_id": "sub-1",
        "subscription_status": "active",
        "period_expiry": FUTURE.isoformat(),
        "entitled": True,
        "reason": "SUBSCRIBED",
    }
    rows = [_listing_row(subscription=_subscription(user_id="somebody-else"))]

    spoofed_result = _run(resolve(spoofed, LISTING_ID, FakeSupabase(rows, _version_rows(1)), NOW))
    bare_result = _run(resolve(CALLER, LISTING_ID, FakeSupabase(rows, _version_rows(1)), NOW))

    assert spoofed_result.reason is EntitlementReason.NOT_SUBSCRIBED
    assert spoofed_result.entitling is False
    # Identical decisions: the extra fields moved nothing.
    assert spoofed_result == bare_result


def test_request_supplied_owner_identifier_does_not_confer_ownership():
    # Claiming the owner's id in the request does not make the caller the author; only the
    # stored ``author_id`` against the authenticated ``id`` does.
    spoofed = {"id": CALLER["id"], "author_id": OWNER["id"], "ownership": "OWNED"}
    fake = FakeSupabase(
        listing_rows=[_listing_row(author_id=OWNER["id"], subscription=None)],
        version_rows=_version_rows(1),
    )
    ent = _run(resolve(spoofed, LISTING_ID, fake, NOW))
    assert ent.reason is EntitlementReason.NOT_SUBSCRIBED
    assert ent.version_id is None


# ══════════════════════════════════════════════════════════════════════════
# The round-trip count, measured
# ══════════════════════════════════════════════════════════════════════════


def test_one_embedded_round_trip_for_the_admission_join():
    fake = FakeSupabase(
        listing_rows=[_listing_row(subscription=_subscription())],
        version_rows=_version_rows(1),
    )
    _run(resolve(CALLER, LISTING_ID, fake, NOW))
    # Exactly one read of library_strategies (the embedded join), then one version read.
    assert fake.tables_read() == ["library_strategies", "strategy_versions"]
    # The admission decision itself - listing + submission state + the caller's subscription -
    # is ONE round trip. The second read is design.md's ``current_version_of(...)``: the
    # Requirement 7.5 server-side artifact resolution, issued only on a would-be entitling path.
    counts = fake.round_trips_per_table()
    assert counts["library_strategies"] == 1
    assert counts["strategy_versions"] == 1
    assert fake.round_trips() == 2
    # The join is one embedded select naming both embedded resources, not three selects.
    join = fake.calls[0]
    assert "marketplace_submissions(" in join.cols
    assert "library_subscriptions(" in join.cols
    assert join.filters == [("id", LISTING_ID)]


@pytest.mark.parametrize(
    "listing_rows,expected_reason",
    [
        ([], EntitlementReason.LISTING_UNAVAILABLE),
        ([_listing_row(subscription=None)], EntitlementReason.NOT_SUBSCRIBED),
        (
            [_listing_row(subscription=_subscription(period_expiry=PAST))],
            EntitlementReason.EXPIRED,
        ),
        (
            [_listing_row(subscription=_subscription(status="suspended"))],
            EntitlementReason.SUBSCRIPTION_SUSPENDED,
        ),
    ],
)
def test_non_entitling_decisions_cost_exactly_one_round_trip(listing_rows, expected_reason):
    fake = FakeSupabase(listing_rows=listing_rows, version_rows=_version_rows(1))
    ent = _run(resolve(CALLER, LISTING_ID, fake, NOW))
    assert ent.reason is expected_reason
    assert fake.round_trips() == 1
    assert fake.tables_read() == ["library_strategies"]


def test_round_trip_count_does_not_grow_with_extra_embedded_rows():
    # More submission rows and a longer version history do not add reads: the count is a
    # property of the decision, not of the data volume.
    row = _listing_row(subscription=_subscription())
    row["marketplace_submissions"] = [
        {"submission_state": "REJECTED"},
        {"submission_state": "PUBLISHED"},
    ]
    fake = FakeSupabase(listing_rows=[row], version_rows=_version_rows(12))
    ent = _run(resolve(CALLER, LISTING_ID, fake, NOW))
    assert ent.reason is EntitlementReason.SUBSCRIBED
    assert fake.round_trips() == 2


# ══════════════════════════════════════════════════════════════════════════
# A read that does not complete is not a verdict (Requirement 30.5)
# ══════════════════════════════════════════════════════════════════════════


def test_failed_admission_read_raises_rather_than_answering_not_subscribed():
    fake = FakeSupabase(
        listing_rows=[_listing_row(subscription=_subscription())],
        raise_on={"library_strategies"},
    )
    with pytest.raises(EntitlementReadFailed) as excinfo:
        _run(resolve(CALLER, LISTING_ID, fake, NOW))
    # Distinguishable from every one of the six reasons, and carrying the catalogue's read code.
    assert excinfo.value.wire_code == "MARKETPLACE_READ_FAILED"
    assert isinstance(excinfo.value.__cause__, RuntimeError)


def test_error_envelope_is_a_failed_read_not_an_absent_listing():
    # A completed request that answers ``{"data": None, "error": ...}`` must not read as
    # "no such listing" - that would be LISTING_UNAVAILABLE for a broken read.
    fake = FakeSupabase(listing_rows=[], error_on={"library_strategies"})
    with pytest.raises(EntitlementReadFailed):
        _run(resolve(CALLER, LISTING_ID, fake, NOW))


def test_failed_version_read_does_not_become_listing_unavailable():
    fake = FakeSupabase(
        listing_rows=[_listing_row(subscription=_subscription())],
        raise_on={"strategy_versions"},
    )
    with pytest.raises(EntitlementReadFailed):
        _run(resolve(CALLER, LISTING_ID, fake, NOW))


def test_read_failed_wire_code_matches_the_error_catalogue():
    from backend_app.backend.marketplace import errors

    assert EntitlementReadFailed.wire_code == errors.MARKETPLACE_READ_FAILED
    assert errors.HTTP_STATUS_FOR_CODE[errors.MARKETPLACE_READ_FAILED] == 503


# ══════════════════════════════════════════════════════════════════════════
# The entitling flag cannot disagree with the reason
# ══════════════════════════════════════════════════════════════════════════


def test_entitling_flag_and_reason_are_held_to_one_meaning():
    assert ENTITLING_REASONS == {EntitlementReason.OWNED, EntitlementReason.SUBSCRIBED}
    with pytest.raises(ValueError):
        Entitlement(entitling=True, reason=EntitlementReason.NOT_SUBSCRIBED)
    with pytest.raises(ValueError):
        Entitlement(entitling=False, reason=EntitlementReason.SUBSCRIBED)


def test_resolve_never_returns_a_reason_outside_the_six():
    cases = [
        FakeSupabase([], []),
        FakeSupabase([_listing_row(author_id=CALLER["id"])], _version_rows(1)),
        FakeSupabase([_listing_row(subscription=None)], _version_rows(1)),
        FakeSupabase([_listing_row(subscription=_subscription())], _version_rows(1)),
        FakeSupabase(
            [_listing_row(subscription=_subscription(status="suspended"))], _version_rows(1)
        ),
        FakeSupabase(
            [_listing_row(subscription=_subscription(period_expiry=PAST))], _version_rows(1)
        ),
    ]
    seen = set()
    for fake in cases:
        ent = _run(resolve(CALLER, LISTING_ID, fake, NOW))
        assert ent.reason in set(EntitlementReason)
        assert ent.entitling is (ent.reason in ENTITLING_REASONS)
        seen.add(ent.reason)
    # All six reasons are reachable from these six fixtures.
    assert seen == set(EntitlementReason)


# ══════════════════════════════════════════════════════════════════════════
# The wire-code mapping agrees with the shared catalogue
# ══════════════════════════════════════════════════════════════════════════


def test_wire_codes_match_error_catalogue():
    from backend_app.backend.marketplace import errors

    # Entitling reasons carry no wire code.
    assert WIRE_CODE_FOR_REASON[EntitlementReason.OWNED] is None
    assert WIRE_CODE_FOR_REASON[EntitlementReason.SUBSCRIBED] is None
    # Non-entitling reasons map onto real catalogue codes with the design's HTTP statuses.
    assert WIRE_CODE_FOR_REASON[EntitlementReason.NOT_SUBSCRIBED] == errors.MARKETPLACE_NOT_SUBSCRIBED
    assert WIRE_CODE_FOR_REASON[EntitlementReason.EXPIRED] == errors.MARKETPLACE_SUBSCRIPTION_EXPIRED
    assert WIRE_CODE_FOR_REASON[EntitlementReason.LISTING_UNAVAILABLE] == errors.MARKETPLACE_STRATEGY_UNAVAILABLE
    assert WIRE_CODE_FOR_REASON[EntitlementReason.SUBSCRIPTION_SUSPENDED] == errors.MARKETPLACE_OPERATION_NOT_PERMITTED
    assert errors.HTTP_STATUS_FOR_CODE[errors.MARKETPLACE_NOT_SUBSCRIBED] == 403
    assert errors.HTTP_STATUS_FOR_CODE[errors.MARKETPLACE_SUBSCRIPTION_EXPIRED] == 403
    assert errors.HTTP_STATUS_FOR_CODE[errors.MARKETPLACE_STRATEGY_UNAVAILABLE] == 409
    assert errors.HTTP_STATUS_FOR_CODE[errors.MARKETPLACE_OPERATION_NOT_PERMITTED] == 403


def test_every_reason_has_a_wire_mapping():
    for reason in EntitlementReason:
        assert reason in WIRE_CODE_FOR_REASON
