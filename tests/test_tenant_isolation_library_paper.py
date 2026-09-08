"""
tests/test_tenant_isolation_library_paper.py - the example-based tenant-isolation cases for
``/api/library/*``, ``/api/paper/sessions/*`` and the ``paper.{session_id}`` channel.

Spec: marketplace-subscriptions-paper-trading task 34.4. Requirements 21.8, 25.8, 29.9.

WHY THIS FILE EXISTS RATHER THAN NEW METHODS IN THE TWO EXISTING ISOLATION MODULES
---------------------------------------------------------------------------------
``tests/test_tenant_isolation_fixes.py`` and ``tests/test_tenant_isolation_strategy_clone.py``
both drive ``/api/strategies/*`` and ``/api/signal-trace/*`` over ``MagicMock`` service doubles
whose ``.table().select().eq().eq().execute()`` chain is hand-wired per test. Neither harness can
reach ``/api/library/*`` or ``/api/paper/*``: those routers issue statements this repository's ONE
Persistence_Layer double answers (``delete``, ``upsert``, ``in_``, ``not_.is_``, ``.count``, the
eleven ``paper_*`` tables with their unique indexes and UPDATE triggers), and a ``MagicMock``
chain would answer every one of them with a truthy ``MagicMock`` - which reads as "the row was
found" for both tenants. Extending either file in place would therefore have meant building a
third harness inside it. This module is a NEW ``tests/test_tenant_isolation_*`` file, so it is
picked up by the same ``tests/test_tenant_isolation_*`` selector Requirement 25.8 names, and it
reuses the existing double rather than adding one. Not one line of either existing file is
touched.

THE ONE PERSISTENCE_LAYER DOUBLE, REUSED AND NOT DUPLICATED
-----------------------------------------------------------
``tests/paper_seed.py`` records the rule: this repository has exactly ONE Persistence_Layer
double, because a second would be a second set of assumptions about the database. The hierarchy is
``FakeSupabase`` (``tests/test_paper_repository.py``) <- ``MatrixStore``
(``tests/property/test_tenant_isolation_matrix.py``) <- ``CountingStore`` / ``_JourneyStore``.
Everything below drives it through the machinery the property matrix already exposes -
:func:`fresh_store`, :func:`_matrix_app`, :func:`_attempt`, :data:`MATRIX`,
:data:`OWNER_SCOPED_LIST_SURFACES`, :data:`REQUIREMENT_21_4_GAPS` and the three assertion helpers -
so nothing here re-states what tenant isolation means.

THE DIVISION OF LABOUR WITH THE PROPERTY MATRIX
-----------------------------------------------
``tests/property/test_tenant_isolation_matrix.py`` owns the GENERATED half: P-41 to P-46 draw the
tenant pair and every record identifier, and its exhaustive sweep walks the whole derived matrix
once. This file is the EXAMPLE-BASED counterpart task 34.4 asks for: concrete, named cases over
the two literal tenants, one pytest case per surface, so a failure names the endpoint rather than
a cell of a 700-cell grid, and so the ``tests/test_tenant_isolation_*`` selector covers the new
surfaces even when ``tests/property/`` is not part of the run. No Hypothesis is used here.

The cell pools are DERIVED from :data:`MATRIX` rather than hand-listed, for the reason that module
derives its rows from ``app.router.routes``: a route added under either prefix joins the pools
without anybody editing this file, and a pool that went empty fails its own guard below.

WHAT EACH GROUP ASSERTS
-----------------------
``test_a_library_read_of_another_tenants_record_is_refused_and_names_no_field_of_it``
``test_a_paper_session_read_of_another_tenants_session_is_refused_and_names_no_field_of_it``
    Requirement 21.8's first and third assertions on every GET that NAMES a record: the answer is
    the answer an identifier that exists in no tenant gets, obtained from the same route in the
    same store, and no field value of the other tenant appears at any nesting depth.

``test_a_library_mutation_of_another_tenants_record_leaves_every_row_byte_identical``
``test_a_paper_mutation_of_another_tenants_session_leaves_every_row_byte_identical``
    Requirement 21.8's second assertion, measured on the ROWS: a full before/after image of every
    table this specification introduces or modifies, compared as text. A status code alone would
    pass for a handler that applied the write and then reported a refusal.

``test_an_owner_scoped_list_returns_no_row_of_the_other_tenant``
``test_the_owner_scoped_lists_are_not_vacuous_for_the_caller``
    Requirement 21.5 over :data:`OWNER_SCOPED_LIST_SURFACES` - which already enumerates which of
    the matrix's list surfaces are owner-scoped and which are the catalogue reads Requirement 6
    makes browsable by design - plus the control that makes "returned nothing" unable to pass.

``test_the_paper_channel_refuses_a_subscription_to_another_tenants_session``
``test_a_broadcast_on_another_tenants_session_channel_reaches_no_intruder_connection``
    Requirements 19.4, 19.6, 21.4, 21.7 through the REAL
    ``core.websocket_auth.authorize_channel_subscription`` and ``backend.ws_channels.PAPER_FAMILY``
    authorisation path - no stub. The second one also asserts the intruder's socket is absent from
    every one of ``ws_manager._stores()``'s eleven stores, which is where the leak that was already
    fixed lived.

``test_every_pinned_requirement_21_4_gap_is_named_by_a_case_in_this_file``
    The seven cells of :data:`REQUIREMENT_21_4_GAPS` are recorded defects of today's
    Marketplace_API, not exemptions. Where a case above lands on one, it asserts the PINNED
    signature pair exactly instead of the equality - so closing a gap turns this file red and
    points at that table. Nothing here "fixes" the router; that is a separate task.

WHAT THIS FILE DOES NOT ESTABLISH
---------------------------------
1. **Row-level security is not exercised.** There is no PostgreSQL in this environment. The double
   answers like a service-role client, which is the WEAKER of the two paths and therefore the
   right one to drive in process: an endpoint whose only tenant scope is a policy this host cannot
   run leaks here and fails, rather than passing on a filter the double honoured for it. The RLS
   text is asserted by the schema-contract suites and the runtime behaviour by the production
   sequence.
2. **The Audit_Log entry Requirement 21.4 also requires is not asserted here.** ``_matrix_app``
   silences the recorder, because it pushes to a Redis this host does not have; the audit half
   belongs to the audit-log task.
3. **No timing claim.** Requirement 19.4's one-second refusal deadline over a dictionary lookup
   would be measuring the double.

Coroutines are driven on the process's ONE event loop through
``tests/test_paper_order_lifecycle_writes._run_coroutine``. ``asyncio.run`` appears nowhere: a
fresh loop per call exhausts the Windows loopback port range through
``socket._fallback_socketpair`` and has hung this suite past 700 seconds.
"""

from __future__ import annotations

import json
from typing import Any, List, Mapping, Tuple

import pytest

from backend_app.backend import ws_channels as channels
from backend_app.backend.paper import paper_channel as pc
from backend_app.core import websocket_auth as WA

# ── The one event loop this process has. Never ``asyncio.run``. ───────────────────────────
from tests.test_paper_order_lifecycle_writes import _run_coroutine as _run

# ── The Paper_Channel harness of task 26.4, imported rather than rebuilt. ─────────────────
# ``_fresh_state`` is autouse, so importing it here is what clears the module-scope owner cache
# and the persistence probe around every test in this file too.
from tests.test_task_26_4_paper_channel_delivery import (  # noqa: F401 - _fresh_state is autouse
    Connection,
    _client,
    _fresh_state,
    _frame,
    _registry,
    _seed_events,
    _session_row,
    _subscribe,
)

# ── The tenant-isolation matrix's machinery. Reused, never re-stated. ─────────────────────
from tests.property.test_tenant_isolation_matrix import (
    ABSENT_IDS,
    ATTEMPT_REFERENCE,
    LISTING_PRIVATE,
    LIST_EXPOSURE_CELLS,
    LIST_ROWS_BY_KEY,
    MATRIX,
    MUTATING_METHODS,
    ORACLE_GAP,
    ORACLE_PUBLIC_CATALOGUE,
    ORACLE_STRICT,
    OWNER_SCOPED_LIST_SURFACES,
    PAPER_ORDER,
    PAPER_SESSION,
    REQUIREMENT_21_4_GAPS,
    U1,
    U1_IDS,
    U2,
    U2_IDS,
    _ALL_U2_VALUES,
    _Answer,
    _assert_no_row_of_u2_changed,
    _assert_no_row_of_u2_moved,
    _assert_no_value_of_u2_appears,
    _attempt,
    _gap_signature,
    _matrix_app,
    assert_indistinguishable_from_a_nonexistent_record,
    fresh_store,
)

# ══════════════════════════════════════════════════════════════════════════
# THE CELL POOLS - DERIVED FROM THE MATRIX, NEVER LISTED
# ══════════════════════════════════════════════════════════════════════════

#: ``/api/library/admin/*`` is excluded from the pools below, and the exclusion is a statement
#: rather than a convenience: those routes are gated on ``get_admin_user``, which ``_matrix_app``
#: deliberately does not grant, so both halves of every comparison are the role gate's 403 and the
#: attempt never reaches the tenant boundary. They are covered by the property matrix's exhaustive
#: sweep, which drives every row including those; adding them here would add cases that assert an
#: equality between two identical role refusals.
_ADMIN_PREFIX = "/api/library/admin"


def _reference_cells(prefix: str, methods: Tuple[str, ...]) -> Tuple[Any, ...]:
    """Every cell of :data:`MATRIX` that NAMES a record, under ``prefix``, on one of ``methods``.

    A reference cell is one whose row carries the other tenant's identifier in its path or in a
    declared body field - which is exactly the shape Requirement 21.8's "an access using a second
    account's identifiers" has. Exposure cells (a row that merely RETURNS records of a kind) are
    the list group's business below, and identity cells are Requirement 21.1's, which the property
    matrix's P-45 owns.
    """
    return tuple(
        cell
        for cell in MATRIX
        if cell.attempt == ATTEMPT_REFERENCE
        and not cell.row.is_channel
        and (cell.row.path or "").startswith(prefix)
        and not (cell.row.path or "").startswith(_ADMIN_PREFIX)
        and (cell.row.method or "GET") in methods
        # A public-catalogue cell answers a published Listing differently BY DESIGN
        # (Requirement 6), so its oracle is the property matrix's relaxed one and an equality
        # assertion here would be asserting the catalogue does not work.
        and cell.oracle != ORACLE_PUBLIC_CATALOGUE
    )


LIBRARY_READ_CELLS: Tuple[Any, ...] = _reference_cells("/api/library", ("GET",))
LIBRARY_MUTATION_CELLS: Tuple[Any, ...] = _reference_cells("/api/library", MUTATING_METHODS)
PAPER_READ_CELLS: Tuple[Any, ...] = _reference_cells("/api/paper", ("GET",))
PAPER_MUTATION_CELLS: Tuple[Any, ...] = _reference_cells("/api/paper", MUTATING_METHODS)

#: Every pool is asserted non-empty at import: a pool that silently went empty would turn its
#: whole group into zero collected cases, which reads as coverage to a report that counts passes.
for _name, _pool in (
    ("LIBRARY_READ_CELLS", LIBRARY_READ_CELLS),
    ("LIBRARY_MUTATION_CELLS", LIBRARY_MUTATION_CELLS),
    ("PAPER_READ_CELLS", PAPER_READ_CELLS),
    ("PAPER_MUTATION_CELLS", PAPER_MUTATION_CELLS),
):
    assert _pool, (
        f"{_name} is empty, so its whole group would collect no cases at all. Either the routes it "
        f"derives from are gone, or MATRIX no longer classifies them as reference cells."
    )


def _cell_id(cell: Any) -> str:
    """The pytest parameter id: the endpoint and the record kind, so a failure names the case."""
    return cell.name


# ══════════════════════════════════════════════════════════════════════════
# THE THREE ASSERTIONS, APPLIED TO ONE CELL
# ══════════════════════════════════════════════════════════════════════════


def _two_answers(cell: Any) -> Tuple[_Answer, _Answer]:
    """The two halves of Requirement 21.4's comparison for one cell.

    ``foreign`` names ``u2``'s record; ``absent`` names an identifier that exists in no tenant.
    Both are made as ``u1`` against a FRESH store, through :func:`_attempt` - the same function the
    property matrix drives - so the oracle is what the route actually answers for an unknown
    identifier and never a literal this file chose.
    """
    foreign = _attempt(cell, U2_IDS, caller=U1, identity=U1)
    absent = _attempt(cell, ABSENT_IDS, caller=U1, identity=U1)
    return foreign, absent


def _assert_answered_as_a_nonexistent_record(cell: Any, foreign: _Answer, absent: _Answer) -> None:
    """Requirement 21.8's first assertion, or the PINNED gap where the route has one.

    A cell whose ``oracle`` is :data:`ORACLE_GAP` is one of the seven
    :data:`REQUIREMENT_21_4_GAPS` - a place the Marketplace_API as it stands today tells the caller
    WHICH refusal it is. Those are asserted as the exact pair recorded in that table, referenced by
    name, so the recorded defect cannot drift and cannot be mistaken for coverage. Closing one
    turns this assertion red and points at ``REQUIREMENT_21_4_GAPS``; fixing the router is a
    separate task and is deliberately not done here.
    """
    if cell.oracle == ORACLE_GAP:
        gap = REQUIREMENT_21_4_GAPS[(cell.row.key, cell.column)]
        observed = (_gap_signature(foreign), _gap_signature(absent))
        assert observed == (gap.foreign, gap.absent), (
            f"{cell.name}: this cell is one of the seven PINNED Requirement 21.4 gaps in "
            f"tests/property/test_tenant_isolation_matrix.REQUIREMENT_21_4_GAPS, and its answers "
            f"have moved. Recorded {(gap.foreign, gap.absent)!r}, observed {observed!r}. If the "
            f"two answers are now identical the gap is CLOSED - delete the entry from that table, "
            f"which moves this cell into the strict set here as well. Why it matters: {gap.note}"
        )
        return

    assert cell.oracle == ORACLE_STRICT, (
        f"{cell.name}: unexpected oracle {cell.oracle!r}; this file handles the strict oracle and "
        f"the pinned gaps, and excludes the public-catalogue cells by construction."
    )
    assert foreign.comparable() == absent.comparable(), (
        f"{cell.name} (Requirement 21.4): the answer for u2's {cell.column} differs from the "
        f"answer for an identifier that exists in no tenant, so the response is an existence "
        f"oracle for another tenant's identifiers.\n"
        f"  another tenant's id -> {foreign.comparable()!r}\n"
        f"  nonexistent id      -> {absent.comparable()!r}"
    )


def _assert_no_row_moved(cell: Any, *answers: _Answer) -> None:
    """Requirement 21.8's second assertion, over a full before/after image of the rows.

    ``fresh_store()`` is deterministic for the default tenancy, so its snapshot IS the "before"
    image of the store each attempt was made against - which is what lets "no row changed" be a
    comparison of rows as text rather than a count of statements.
    """
    clean = fresh_store().snapshot()
    clean_foreign = fresh_store().rows_touching(_ALL_U2_VALUES)
    for answer in answers:
        _assert_no_row_of_u2_moved(cell, clean_foreign, answer)
        _assert_no_row_of_u2_changed(cell, answer, clean)


# ══════════════════════════════════════════════════════════════════════════
# 1. READS - /api/library/*
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("cell", LIBRARY_READ_CELLS, ids=_cell_id)
def test_a_library_read_of_another_tenants_record_is_refused_and_names_no_field_of_it(
    cell: Any,
) -> None:
    """Requirement 21.8 for every ``GET /api/library/*`` that names a record.

    Authenticated as ``u1``, naming ``u2``'s Listing / Submission / Subscription: the answer is
    byte-identical in status and body to the answer for an identifier that exists in no tenant,
    no row of ``u2``'s changed, and no field value of ``u2``'s appears anywhere in the response at
    any nesting depth.

    **Validates: Requirements 21.4, 21.8**
    """
    foreign, absent = _two_answers(cell)
    _assert_answered_as_a_nonexistent_record(cell, foreign, absent)
    _assert_no_row_moved(cell, foreign, absent)
    _assert_no_value_of_u2_appears(cell, foreign)


# ══════════════════════════════════════════════════════════════════════════
# 2. READS - /api/paper/sessions/* and /api/paper/orders/*
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("cell", PAPER_READ_CELLS, ids=_cell_id)
def test_a_paper_session_read_of_another_tenants_session_is_refused_and_names_no_field_of_it(
    cell: Any,
) -> None:
    """Requirement 21.8 for the Paper_Session detail read and its seven sub-resource reads.

    ``routers/paper_trading._owned_session`` is the one gate all eight pass through, and
    ``paper_repository.read_session_summary`` carries ``user_id`` as a PREDICATE - so another
    tenant's row is never fetched rather than fetched and dropped. What that has to produce, and
    what is asserted here, is one answer for "no such session" and "somebody else's session":
    the same 404, the same sentence, and ``details`` carrying only the identifier the caller itself
    supplied.

    **Validates: Requirements 21.4, 21.5, 21.8**
    """
    foreign, absent = _two_answers(cell)
    _assert_answered_as_a_nonexistent_record(cell, foreign, absent)
    _assert_no_row_moved(cell, foreign, absent)
    _assert_no_value_of_u2_appears(cell, foreign)


def test_the_paper_session_reads_cover_the_detail_body_and_all_seven_sub_resources() -> None:
    """The pool above really is the whole session read surface, not whichever rows survived.

    Without this the parametrised group would silently shrink to nothing if the session routes
    stopped being classified as reference cells, and a report counting passes would not notice.
    """
    covered = {cell.row.key for cell in PAPER_READ_CELLS}
    expected = {
        "GET /api/paper/sessions/{session_id}",
        "GET /api/paper/sessions/{session_id}/orders",
        "GET /api/paper/sessions/{session_id}/fills",
        "GET /api/paper/sessions/{session_id}/positions",
        "GET /api/paper/sessions/{session_id}/trades",
        "GET /api/paper/sessions/{session_id}/equity",
        "GET /api/paper/sessions/{session_id}/metrics",
        "GET /api/paper/sessions/{session_id}/events",
    }
    missing = sorted(expected - covered)
    assert not missing, (
        f"these Paper_Session reads are no longer covered by an example-based cross-tenant case: "
        f"{missing}. Either the route is gone, or MATRIX stopped classifying it as a cell that "
        f"names a Paper_Session."
    )
    assert PAPER_SESSION in {cell.column for cell in PAPER_READ_CELLS}
    assert PAPER_ORDER in {cell.column for cell in PAPER_MUTATION_CELLS}, (
        "DELETE /api/paper/orders/{order_id} is the paper order mutation this group is about"
    )


# ══════════════════════════════════════════════════════════════════════════
# 3. MUTATIONS - THE ROWS, BEFORE AND AFTER
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("cell", LIBRARY_MUTATION_CELLS, ids=_cell_id)
def test_a_library_mutation_of_another_tenants_record_leaves_every_row_byte_identical(
    cell: Any,
) -> None:
    """Requirement 21.8's second assertion for every write under ``/api/library``.

    The status is not the subject: a handler that applied the write and then reported a refusal
    answers exactly like one that refused. What is asserted is a full before/after image of every
    row of every table this specification introduces or modifies, compared as canonical text -
    and, on top of it, that the refusal is the non-existent-record refusal and carries no field
    value of ``u2``'s.

    **Validates: Requirements 21.4, 21.8, 21.9**
    """
    foreign, absent = _two_answers(cell)
    _assert_no_row_moved(cell, foreign, absent)
    _assert_answered_as_a_nonexistent_record(cell, foreign, absent)
    _assert_no_value_of_u2_appears(cell, foreign)


@pytest.mark.parametrize("cell", PAPER_MUTATION_CELLS, ids=_cell_id)
def test_a_paper_mutation_of_another_tenants_session_leaves_every_row_byte_identical(
    cell: Any,
) -> None:
    """Requirement 21.8's second assertion for the Paper_Session operations and the order cancel.

    ``paper_session_service.apply_operation`` gates on the state that was read - scoped by
    ``user_id`` - BEFORE it issues any statement, so "changed nothing" is a fact about statements
    that were never issued. This measures it on the rows anyway, because that is the claim
    Requirement 21.4 makes and a gate can be moved.

    **Validates: Requirements 17.14, 21.4, 21.8**
    """
    foreign, absent = _two_answers(cell)
    _assert_no_row_moved(cell, foreign, absent)
    _assert_answered_as_a_nonexistent_record(cell, foreign, absent)
    _assert_no_value_of_u2_appears(cell, foreign)


# ══════════════════════════════════════════════════════════════════════════
# 4. THE OWNER-SCOPED LISTS (Requirement 21.5)
# ══════════════════════════════════════════════════════════════════════════
#
# WHICH LISTS, AND WHY NOT THE CATALOGUE
# --------------------------------------
# ``OWNER_SCOPED_LIST_SURFACES`` already enumerates which of the matrix's list surfaces are scoped
# by the authenticated identity and which are the catalogue reads Requirement 6 makes browsable by
# anyone - each with the reason written on it. Reusing that classification rather than restating it
# is the point: the property matrix checks it against the matrix's own exposure cells in BOTH
# directions, so a list endpoint added later is already unable to go unclassified, and this group
# inherits that guarantee instead of maintaining a second list that could disagree.


def _list_url(surface: Any, ids: Mapping[str, str]) -> str:
    """One list surface's URL, addressed with ``ids``' identifiers."""
    row = LIST_ROWS_BY_KEY[surface.key]
    url = row.path or ""
    for param in row.params:
        slot = surface.path_slots.get(param, param)
        url = url.replace("{" + param + "}", str(ids[slot]))
    return url


def _read_list(surface: Any, caller: str, ids: Mapping[str, str]) -> _Answer:
    """One owner-scoped list, read as ``caller``, over a store holding BOTH tenants' records."""
    store = fresh_store()
    url = _list_url(surface, ids)
    with _matrix_app(store, caller) as client:
        response = client.get(url)
    try:
        parsed: Any = response.json()
    except ValueError:
        parsed = response.text
    supplied = [caller] + [
        str(ids[surface.path_slots.get(param, param)])
        for param in LIST_ROWS_BY_KEY[surface.key].params
    ]
    return _Answer(
        status=response.status_code,
        body=parsed,
        text=response.text,
        snapshot=store.snapshot(),
        foreign_rows=store.rows_touching(_ALL_U2_VALUES),
        supplied=tuple(supplied),
    )


@pytest.mark.parametrize(
    "surface", OWNER_SCOPED_LIST_SURFACES, ids=lambda surface: surface.key
)
def test_an_owner_scoped_list_returns_no_row_of_the_other_tenant(surface: Any) -> None:
    """Requirement 21.5: an owner-scoped list answers ``u1`` with ``u1``'s rows and nothing else.

    The store holds BOTH tenants' ten record kinds, so a list whose scope went missing has ``u2``'s
    rows available to return. The response is then scanned, at every nesting depth AND over the raw
    text, for every field value of ``u2``'s that the surface's own exposure cells declare it
    returns - which is the same scan the property matrix applies, over the same value set.

    ``expected_status`` is the surface's own: one of them answers 503 in this environment and says
    why on itself, and pinning the status is what stops this case passing on an error page that
    happens to mention nobody.

    **Validates: Requirement 21.5**
    """
    answer = _read_list(surface, U1, U1_IDS)

    assert answer.status == surface.expected_status, (
        f"{surface.key}: the caller's OWN list answered {answer.status} rather than "
        f"{surface.expected_status}, so this case is not measuring a scoped list. {surface.note}\n"
        f"  body: {answer.text[:800]}"
    )

    exposure_cells = LIST_EXPOSURE_CELLS[surface.key]
    assert exposure_cells, (
        f"{surface.key} is classified as an owner-scoped list but the matrix records no exposure "
        f"cell for it, so there is no record kind to scan the response for."
    )
    for cell in exposure_cells:
        _assert_no_value_of_u2_appears(cell, answer)

    _assert_no_row_of_u2_moved(exposure_cells[0], fresh_store().rows_touching(_ALL_U2_VALUES), answer)


def test_the_owner_scoped_lists_are_not_vacuous_for_the_caller() -> None:
    """The control: the same surface really does hand each tenant its OWN rows.

    Without this, "no row of ``u2`` appeared" would hold for a list endpoint that returned nothing
    to anybody. ``GET /api/library/me`` is the control surface for the reason the property matrix
    gives for choosing it: the seed gives EVERY tenant exactly two Listings, so the list is
    non-empty for either tenant, and each must see exactly its own.

    **Validates: Requirement 21.5**
    """
    surface = next(
        item for item in OWNER_SCOPED_LIST_SURFACES if item.key == "GET /api/library/me"
    )
    for caller, own_ids, other_ids in (
        (U1, U1_IDS, U2_IDS),
        (U2, U2_IDS, U1_IDS),
    ):
        answer = _read_list(surface, caller, own_ids)
        assert answer.status == 200, f"{surface.key} as {caller}: {answer.status} {answer.text[:400]}"
        assert str(own_ids["library_id"]) in answer.text, (
            f"{surface.key} as {caller} returned neither of that tenant's two Listings, so "
            f"'no row of the other tenant appeared' would hold for an endpoint that returns "
            f"nothing at all. Body: {answer.text[:800]}"
        )
        assert str(other_ids["library_id"]) not in answer.text, (
            f"{surface.key} as {caller} returned the OTHER tenant's private Listing "
            f"{other_ids['library_id']!r}. Body: {answer.text[:800]}"
        )


# ══════════════════════════════════════════════════════════════════════════
# 5. THE paper.{session_id} CHANNEL
# ══════════════════════════════════════════════════════════════════════════
#
# The authorisation path is the REAL one: ``core.websocket_auth.authorize_channel_subscription``
# makes the whole decision, over ``backend.ws_channels.PAPER_FAMILY``'s registered owner relation
# and ``paper_channel.session_owner``'s lookup. Nothing here stubs a decision, and nothing here
# adds an authorisation or weakens one.

#: Two tenants and three sessions, as literals. UUID text because that is what ``paper_sessions.id``
#: holds and what ``ws_channels._RESOURCE_ID_PATTERN`` admits. Fixed rather than drawn: P-44 in
#: ``tests/property/test_tenant_isolation_matrix.py`` is the generated half, and this is the named
#: case task 34.4 asks for.
CHANNEL_OWNER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CHANNEL_INTRUDER = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
OWNERS_SESSION = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
INTRUDERS_OWN_SESSION = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
SESSION_IN_NO_TENANT = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"

#: How many events the owner's session has retained, so the owner's own replay has something to
#: return and "the intruder's replay was empty" is not true of an empty log.
RETAINED_EVENTS = 3


def _channel_store() -> Any:
    """A store holding the owner's session with retained events, and the intruder's own session.

    The intruder owning a session of its own is deliberate: a component that refused EVERYTHING
    would satisfy the refusal half of every assertion below, and the admitted-owner controls are
    what distinguish isolation from a blanket denial.
    """
    store = _client(
        sessions=[
            _session_row(OWNERS_SESSION, CHANNEL_OWNER, event_sequence=RETAINED_EVENTS),
            _session_row(INTRUDERS_OWN_SESSION, CHANNEL_INTRUDER, event_sequence=0),
        ]
    )
    _seed_events(store, RETAINED_EVENTS, session_id=OWNERS_SESSION, user_id=CHANNEL_OWNER)
    return store


def _authorize(channel: str, identity: str, store: Any) -> Any:
    """The REAL authorisation decision, driven on the process's one loop.

    ``supabase`` is the function's own parameter, so nothing here monkeypatches
    ``dependencies.create_request_supabase_async``.
    """
    return _run(
        WA.authorize_channel_subscription(channel, {"id": identity}, supabase=store)
    )


def _values_belonging_to_the_owner(store: Any) -> Tuple[str, ...]:
    """Every field value of the owner's session and its events that a refusal may not carry.

    The session id itself is NOT in here: the intruder NAMED it, so echoing it back reveals
    nothing the intruder did not already hold - which is exactly why ``channel`` is the one field
    the matrix's ``comparable_refusal`` excludes from its comparison.
    """
    values: List[str] = [CHANNEL_OWNER]
    for row in store.events:
        if str(row.get("session_id")) == OWNERS_SESSION:
            values.append(str(row.get("event_id")))
    return tuple(values)


def test_the_paper_channel_refuses_a_subscription_to_another_tenants_session() -> None:
    """``paper.{session_id}`` for a session ``u2`` owns is refused to ``u1``, indistinguishably.

    The oracle is the refusal for a session that exists in no tenant, obtained from the same
    function in the same store. The owner of the same session is admitted in the same run, so the
    refusal cannot be a channel that admits nobody, and the refusal frame is scanned for every
    field value of the owner's records.

    **Validates: Requirements 19.4, 21.4, 21.6, 21.8**
    """
    store = _channel_store()
    target = channels.PAPER_FAMILY.channel(OWNERS_SESSION)

    foreign = _authorize(target, CHANNEL_INTRUDER, store)
    nonexistent = _authorize(
        channels.PAPER_FAMILY.channel(SESSION_IN_NO_TENANT), CHANNEL_INTRUDER, store
    )
    assert_indistinguishable_from_a_nonexistent_record(
        foreign=foreign,
        nonexistent=nonexistent,
        record="Paper_Session",
        label="task 34.4",
        context=f"paper.{OWNERS_SESSION} probed by {CHANNEL_INTRUDER}",
    )
    assert foreign.code == WA.CHANNEL_REFUSED_FORBIDDEN, (
        f"the refusal code is {foreign.code!r}; a code that distinguished another tenant's session "
        f"from an unknown one would be the existence oracle Requirement 21.4 forbids."
    )
    assert foreign.owner_id is None, "the refusal carried the session owner's identifier"

    rendered = json.dumps(foreign.refusal_frame(), default=str)
    for value in _values_belonging_to_the_owner(store):
        assert value not in rendered, (
            f"the refusal frame carries {value!r}, which belongs to the session's owner "
            f"(Requirement 21.8). Frame: {rendered}"
        )

    admitted = _authorize(target, CHANNEL_OWNER, store)
    assert admitted.allowed is True, (
        f"the OWNER of the session was refused ({admitted.code}: {admitted.reason}), so the "
        f"refusal above is not isolation - it is a channel that admits nobody."
    )
    assert str(admitted.owner_id) == CHANNEL_OWNER

    assert store.wrote_anything() is False, (
        f"an authorisation decision wrote to the Persistence_Layer: "
        f"{[(s.op, s.table_name) for s in store.statements if s.op != 'select']!r}"
    )


def test_a_broadcast_on_another_tenants_session_channel_reaches_no_intruder_connection() -> None:
    """A frame emitted on ``u2``'s session channel is never delivered to ``u1``'s connection.

    The intruder is registered by FORCE, past the authorisation the test above shows refuses it, so
    that the pre-emit ownership re-derivation of Requirement 21.7 is shown to be a SECOND,
    independent refusal rather than a restatement of the first. At the transport this subscription
    could not exist.

    Three things are then asserted, and the third is the one a leak would hide: the intruder
    received nothing and was closed with the ownership close code; the owner received the frame in
    the same broadcast (so "nothing was delivered" is not true of a channel that delivers nothing);
    and the intruder's socket is absent from EVERY one of ``ws_manager._stores()``'s eleven stores.
    That last one is where the registry leak that has already been fixed lived - a sweep over a
    hard-coded list of six stores left a disconnected socket registered in the other four - so it
    is asserted over the manager's own enumeration rather than over a list written here.

    **Validates: Requirements 19.6, 19.10, 21.4, 21.7**
    """
    store = _channel_store()
    registry = _registry()
    manager = registry.manager

    owner_connection = Connection("owner")
    intruder_connection = Connection("intruder")

    owner_subscription = _subscribe(
        registry, owner_connection, user={"id": CHANNEL_OWNER}, session_id=OWNERS_SESSION
    )
    intruder_subscription = _subscribe(
        registry,
        intruder_connection,
        user={"id": CHANNEL_INTRUDER},
        session_id=OWNERS_SESSION,
        # The subscribe message names the owner every way a message can. Requirements 19.5 and
        # 21.1 forbid deriving an identity from any of them.
        message={
            "channel": channels.PAPER_FAMILY.channel(OWNERS_SESSION),
            "last_sequence": 0,
            "user_id": CHANNEL_OWNER,
            "owner_id": CHANNEL_OWNER,
            "identity": CHANNEL_OWNER,
            "tenant_id": CHANNEL_OWNER,
        },
    )
    assert intruder_subscription.identity == CHANNEL_INTRUDER, (
        f"the subscribe message naming the owner changed the recorded identity to "
        f"{intruder_subscription.identity!r} (Requirements 19.5, 21.1)."
    )

    try:
        outcome = _run(
            registry.broadcast(OWNERS_SESSION, _frame(1, OWNERS_SESSION), supabase=store)
        )

        assert owner_subscription in outcome.delivered, (
            f"the frame did not reach the session's OWNER, so 'no event was delivered to the "
            f"intruder' would be true of a channel that delivered nothing at all. {outcome!r}"
        )
        assert intruder_subscription not in outcome.delivered
        assert outcome.ownership_revoked == (intruder_subscription,), (
            f"the intruder's subscription was not revoked before the emit (Requirements 19.6, "
            f"21.7); outcome {outcome!r}."
        )
        assert intruder_connection.sent == [], (
            f"the intruder received {intruder_connection.sent!r} on another tenant's session "
            f"(Requirement 19.6)."
        )
        assert intruder_connection.closed_with == [pc.CLOSE_CODE_OWNERSHIP], (
            f"the intruder's subscription was not closed; closes seen: "
            f"{intruder_connection.closed_with!r}."
        )
        assert registry.subscriptions(OWNERS_SESSION) == (owner_subscription,)
        assert owner_connection.sequences() == [1], (
            f"the owner received {owner_connection.sequences()!r} rather than the one broadcast "
            f"frame."
        )

        # The registry sweep, over the manager's OWN enumeration of its stores.
        leaked = [
            f"store #{index} key {key!r}"
            for index, registered in enumerate(manager._stores())
            for key, sockets in registered.items()
            if intruder_connection in sockets
        ]
        assert not leaked, (
            f"the revoked intruder connection is still registered in {leaked} (Requirement "
            f"19.10). ws_manager._stores() enumerates every per-channel store precisely so a "
            f"sweep cannot miss one."
        )
        assert intruder_connection not in manager._all_connections
        assert CHANNEL_INTRUDER not in manager._user_connections

        # Neither delivery path hands the intruder an event: the replay is a read with ``user_id``
        # as a predicate, and it must answer exactly as an unknown session does.
        intruder_replay = registry.replay(
            store, session_id=OWNERS_SESSION, user={"id": CHANNEL_INTRUDER}, last_sequence=0
        )
        assert intruder_replay.frames == (), (
            f"the replay handed the intruder {len(intruder_replay.frames)} of another tenant's "
            f"events (Requirements 19.6, 21.4)."
        )
        owner_replay = registry.replay(
            store, session_id=OWNERS_SESSION, user={"id": CHANNEL_OWNER}, last_sequence=0
        )
        assert [int(frame["sequence"]) for frame in owner_replay.frames] == list(
            range(1, RETAINED_EVENTS + 1)
        ), (
            f"the OWNER's own replay returned "
            f"{[frame['sequence'] for frame in owner_replay.frames]!r} rather than its "
            f"{RETAINED_EVENTS} retained events, so the empty replay above says nothing about "
            f"isolation."
        )
    finally:
        _run(registry.release_session(OWNERS_SESSION, reason="the attempt is over"))


# ══════════════════════════════════════════════════════════════════════════
# 6. THE PINNED REQUIREMENT 21.4 GAPS ARE NAMED, NOT WAIVED
# ══════════════════════════════════════════════════════════════════════════


def test_every_pinned_requirement_21_4_gap_is_named_by_a_case_in_this_file() -> None:
    """Each of the seven pinned gaps is landed on by one of the cases above, and asserted there.

    A pinned gap this file's pools did not reach would be a recorded defect nothing in the
    ``tests/test_tenant_isolation_*`` selector checks - which is exactly the hole Requirement 25.8
    exists to close. The assertion is the pin itself
    (:func:`_assert_answered_as_a_nonexistent_record`), so a gap that is CLOSED fails here rather
    than passing quietly, and a gap that MOVED fails with both signatures printed.

    **Validates: Requirements 21.4, 21.8, 29.9**
    """
    covered = {
        (cell.row.key, cell.column)
        for pool in (
            LIBRARY_READ_CELLS,
            LIBRARY_MUTATION_CELLS,
            PAPER_READ_CELLS,
            PAPER_MUTATION_CELLS,
        )
        for cell in pool
        if cell.oracle == ORACLE_GAP
    }
    unreached = sorted(key for key in REQUIREMENT_21_4_GAPS if key not in covered)
    assert not unreached, (
        f"these pinned Requirement 21.4 gaps are not reached by any case in this file, so the "
        f"tests/test_tenant_isolation_* selector does not check them: {unreached}. Widen the pools "
        f"at the top of this module rather than deleting the pins."
    )
    assert len(REQUIREMENT_21_4_GAPS) == 7, (
        f"REQUIREMENT_21_4_GAPS holds {len(REQUIREMENT_21_4_GAPS)} entries rather than the seven "
        f"the sweep found. A new entry is a NEW existence oracle and needs a decision, not a "
        f"silently updated count; a removed one means a gap was closed, which is good news that "
        f"should be recorded here too."
    )
    assert LISTING_PRIVATE in {column for _key, column in REQUIREMENT_21_4_GAPS}, (
        "the pinned gaps are all on the Marketplace_API's listing-private and strategy reference "
        "paths; a table with neither is not the table this test was written against"
    )


def test_no_case_in_this_file_weakens_a_pinned_gap_into_an_equality() -> None:
    """A gap cell must be asserted as the PIN, never as the strict equality.

    Structural rather than behavioural, and it is the guard on this file itself: if somebody moved
    a gap cell into the strict pool to make a red run green, the pin would stop being asserted and
    the recorded defect would silently become "covered". Here the classification is read off
    ``MATRIX`` - where ``ORACLE_GAP`` is DERIVED from ``REQUIREMENT_21_4_GAPS`` and cannot be
    written by hand - so the two cannot drift apart.
    """
    for pool in (
        LIBRARY_READ_CELLS,
        LIBRARY_MUTATION_CELLS,
        PAPER_READ_CELLS,
        PAPER_MUTATION_CELLS,
    ):
        for cell in pool:
            is_pinned = (cell.row.key, cell.column) in REQUIREMENT_21_4_GAPS
            assert (cell.oracle == ORACLE_GAP) is is_pinned, (
                f"{cell.name}: the cell's oracle is {cell.oracle!r} while REQUIREMENT_21_4_GAPS "
                f"{'does' if is_pinned else 'does not'} pin it. The two must agree, or a pinned "
                f"gap would be asserted as an equality it does not satisfy - or an unpinned cell "
                f"would be excused from the equality it does."
            )
