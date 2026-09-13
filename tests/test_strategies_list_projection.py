"""
tests/test_strategies_list_projection.py - vyomquant-ui-redesign BC-3 and BC-4.

Tasks 12.3 and 12.4. Requirements 4.1, 19.1, 19.2. ``design.md`` §7.2, §16.

WHAT THIS FILE EXISTS FOR
-------------------------
§7.2 audited the Strategies table column by column against ``GET /api/strategies`` and found
two of its ten columns had no backend source at all:

* **Last signal.** ``last_signal_at`` was read by the *dashboard* strategy projection
  (``dashboard_aggregation_service.get_strategies``, published there as ``last_signal_time``)
  and by nothing on the strategies list. BC-3 adds it to the list, from the same key on the
  same table, so there is one definition of "last signal" rather than two.
* **Last execution.** No ``last_execution*`` field existed on any strategy projection.
  ``execution_records`` already computes ``MAX(created_at) as last_execution_at`` for its
  tenant-wide summary (``ExecutionRecordRepository.get_stats``); BC-4 adds ``GROUP BY
  strategy_id`` to that same expression and publishes the per-strategy figure.

The frontend rendered both as not-available, which was honest but useless: a trader could
not tell a strategy that fired an hour ago from one that has never fired.

THE THREE CLAIMS THIS FILE HOLDS
--------------------------------
1. ``null``, NEVER ABSENT AND NEVER FABRICATED (Requirement 19.2). Both keys are on every
   entry of every list, and both are ``null`` when there is nothing to report. A key that
   appeared only when there was a value would make "never signalled" indistinguishable from
   "this build does not report signal times", and a substituted clock read would tell a
   trader a dormant strategy just executed.

2. THE FIGURE IS THE STORED MAXIMUM (Requirement 4.1). ``last_execution_at`` for a strategy
   with executions is the greatest ``created_at`` among *its* executions - not the tenant's
   latest, not the first, and not another tenant's. Asserted against a real database with
   real rows, through the real aggregate, so it is the SQL that is under test.

3. ADDITIVE (Requirement 19.1). Every field the endpoint published before still has the same
   name, type and value; the row set and its order are untouched; and the whole page costs
   exactly one execution-store read regardless of how many strategies it carries.

WHY THERE IS A REAL DATABASE HERE AND NOT A REPOSITORY DOUBLE
    BC-4's entire content is one SQL statement. A double standing in for the session would
    assert that this module calls a method, which is not the claim - the claim is that the
    statement groups by strategy, takes the maximum, and is scoped to one tenant. So the ORM
    model's own metadata builds ``execution_records`` in an in-memory SQLite database, real
    rows go in, and the repository method runs against them unmodified.

    The strategy rows come from Supabase rather than SQLAlchemy, so they use
    ``tests/test_task_5_2_include_archived``'s PostgREST double - the one this endpoint's
    existing suite already established, imported rather than re-implemented.

WHAT THIS FILE CANNOT PROVE
    There is no PostgreSQL here, so nothing here proves the ``(tenant_id, ...)`` indexes
    serve the grouped aggregate, nor that RLS scopes the strategy read at the database as
    well as the ``user_id`` predicate does.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend_app.core.models.execution_record import (
    ExecutionRecordModel,
    ExecutionRecordRepository,
    ExecutionStatus,
)
from backend_app.routers import strategies as router_module

# The endpoint's existing suite owns the PostgREST double and the strategy row fixture.
from tests.test_task_5_2_include_archived import _list, _strategy_row, _Supabase

#: Two strategies owned by the test user. Fixed ids so the SQLite rows and the Supabase rows
#: address the same strategies.
BUSY_ID = "44444444-4444-4444-8444-444444444444"
QUIET_ID = "55555555-5555-4555-8555-555555555555"

#: A third strategy, owned by a different tenant, whose executions must not be reachable.
FOREIGN_ID = "66666666-6666-4666-8666-666666666666"

#: The tenant ``execution_records`` rows are keyed on. ``user["id"]`` on this endpoint IS the
#: tenant id, so it has to be UUID-shaped for BC-4 to address anything.
TENANT = "77777777-7777-4777-8777-777777777777"
FOREIGN_TENANT = "88888888-8888-4888-8888-888888888888"

#: The instants the executions are recorded at. Fixed and ordered, never a clock read, so
#: "the maximum" is a stated fact about the fixture rather than a coincidence of timing.
EARLIER = datetime(2024, 5, 1, 9, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2024, 5, 1, 17, 30, 0, tzinfo=timezone.utc)
LATEST_FOREIGN = datetime(2024, 6, 9, 23, 59, 0, tzinfo=timezone.utc)

#: The stored signal instant BC-3 must report verbatim.
SIGNALLED_AT = "2024-05-02T08:15:00+00:00"


# ══════════════════════════════════════════════════════════════════════════
# THE EXECUTION STORE - real table, real rows, real aggregate
# ══════════════════════════════════════════════════════════════════════════


@contextmanager
def _execution_store() -> Iterator[Any]:
    """An in-memory ``execution_records`` table, built from the ORM model's own metadata.

    Only this one table is created, so nothing else in ``Base.metadata`` has to be
    satisfiable for BC-4's statement to be exercised.

    ``StaticPool`` + ``check_same_thread=False`` because the router reads the execution store
    on a worker thread (the session is synchronous, so blocking the event loop with it would
    stall every other request). Default in-memory SQLite gives each thread its *own* empty
    database, which would have this suite asserting against a table that is not there.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ExecutionRecordModel.__table__.create(bind=engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def execution_db() -> Iterator[Any]:
    with _execution_store() as session:
        yield session


def _record(
    session: Any,
    *,
    tenant_id: str,
    strategy_id: str,
    created_at: datetime,
    symbol: str = "BTC/USDT",
) -> None:
    """One ``execution_records`` row, written through the ORM model."""
    session.add(
        ExecutionRecordModel(
            execution_id=f"exec_{uuid4().hex[:16]}",
            tenant_id=UUID(tenant_id),
            strategy_id=strategy_id,
            symbol=symbol,
            side="buy",
            size="0.01",
            price="60000.00",
            status=ExecutionStatus.COMPLETED,
            created_at=created_at,
            updated_at=created_at,
        )
    )
    session.commit()


def _seed_executions(session: Any) -> None:
    """``BUSY_ID`` executed twice, ``QUIET_ID`` never, and another tenant executed latest.

    The foreign row is deliberately the most recent instant in the whole table: if the
    aggregate were not scoped by ``tenant_id``, ``BUSY_ID`` would report it.
    """
    _record(session, tenant_id=TENANT, strategy_id=BUSY_ID, created_at=EARLIER)
    _record(session, tenant_id=TENANT, strategy_id=BUSY_ID, created_at=LATER)
    _record(
        session, tenant_id=FOREIGN_TENANT, strategy_id=FOREIGN_ID, created_at=LATEST_FOREIGN
    )


# ══════════════════════════════════════════════════════════════════════════
# THE LIST SURFACE
# ══════════════════════════════════════════════════════════════════════════


def _store(*, last_signal_at: Optional[str] = None, extra: int = 0) -> Any:
    """The two strategies, plus ``extra`` filler rows, in the PostgREST double.

    ``last_signal_at`` is set on ``BUSY_ID`` only. Passing ``None`` leaves the column off
    both rows entirely, which is the state of every database in this repository: no
    migration declares ``strategies.last_signal_at``, so a real row simply has no such key.
    """
    busy = _strategy_row(BUSY_ID, user_id=TENANT, name="Busy")
    quiet = _strategy_row(QUIET_ID, user_id=TENANT, name="Quiet")
    if last_signal_at is not None:
        busy["last_signal_at"] = last_signal_at
    rows = [busy, quiet]
    for index in range(extra):
        rows.append(
            _strategy_row(
                f"99999999-9999-4999-8999-{index:012d}", user_id=TENANT, name=f"Filler {index}"
            )
        )
    return _Supabase({"strategies": rows})


def _listing(store: Any, session: Any, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """``GET /api/strategies`` as ``TENANT``, with the execution store bound to ``session``.

    ``session`` is redirected at the *connection source only*: the router still resolves the
    real ``get_db_context`` name, builds the real repository and runs the real statement
    against real rows. Nothing about BC-4's own logic is stood in for.
    """
    with patch("backend_app.core.database.get_db_context", _binder(session)):
        return _list(store, params, user_id=TENANT)


def _entry(body: Dict[str, Any], strategy_id: str) -> Dict[str, Any]:
    for item in body["strategies"]:
        if item["id"] == strategy_id:
            return item
    raise AssertionError(f"{strategy_id} is not in {[i['id'] for i in body['strategies']]}")


# ══════════════════════════════════════════════════════════════════════════
# 1. BC-3 - ``last_signal_at`` IS PUBLISHED, AND null WHEN UNREPORTED
#    (Requirements 4.1, 19.2)
# ══════════════════════════════════════════════════════════════════════════


def test_a_strategy_that_never_signalled_reports_last_signal_at_as_null_not_absent(
    execution_db,
):
    """THE CLAIM TASK 12.3 IS ACCOUNTABLE FOR.

    The key is on the entry and its value is ``None`` -> JSON ``null``. Absence would be
    read by the client's ``??`` chains as "no signal reporting in this build", which is a
    different statement, and any timestamp at all would be invented.
    """
    _seed_executions(execution_db)

    body = _listing(_store(), execution_db)
    entry = _entry(body, QUIET_ID)

    assert "last_signal_at" in entry, (
        "last_signal_at is absent from the entry; Requirement 19.2 asks for an explicit "
        "null, not a missing key a reader has to interpret"
    )
    assert entry["last_signal_at"] is None


def test_every_entry_on_every_list_carries_last_signal_at(execution_db):
    """Both entries, and on the archived view as well as the default one."""
    _seed_executions(execution_db)

    for params in ({}, {"include_archived": "true"}):
        body = _listing(_store(), execution_db, params)
        assert body["strategies"], params
        for entry in body["strategies"]:
            assert "last_signal_at" in entry, (entry["id"], params)


def test_the_stored_signal_instant_is_reported_verbatim(execution_db):
    """When the column does hold a value, that value is what is published.

    Not reformatted into a different calendar, and not replaced by a clock read - the
    projection reports the instant the row recorded.
    """
    _seed_executions(execution_db)

    body = _listing(_store(last_signal_at=SIGNALLED_AT), execution_db)

    assert _entry(body, BUSY_ID)["last_signal_at"] == SIGNALLED_AT
    # The strategy the fixture did NOT set it on stays null, so the value came from the row
    # rather than from something applied to the whole page.
    assert _entry(body, QUIET_ID)["last_signal_at"] is None


def test_bc3_reads_the_same_key_the_dashboard_projection_reads():
    """One definition of "last signal", not two (design.md §7.2).

    ``dashboard_aggregation_service.get_strategies`` publishes ``s.get("last_signal_at")``
    as ``last_signal_time``. BC-3 had to reuse that source rather than introduce a second
    one, so both are asserted to name the same key.
    """
    from backend_app.backend.dashboard_aggregation_service import (
        DashboardAggregationService,
    )

    dashboard = inspect.getsource(DashboardAggregationService.get_strategies)
    assert 'get("last_signal_at")' in dashboard, (
        "the dashboard projection no longer reads last_signal_at; BC-3's source moved and "
        "the two projections can now disagree"
    )
    assert router_module.LAST_SIGNAL_AT_COLUMN == "last_signal_at"


def test_bc3_needed_no_query_change_at_all():
    """The listing already reads ``select("*")``, so the column travels with the row.

    Stated as an assertion because the alternative - naming the column in the projection -
    would raise PostgreSQL's ``42703`` on every database in this repository, none of which
    declares ``strategies.last_signal_at``. It would also change which rows come back if it
    were pushed into a filter, which Requirement 19.1 forbids.
    """
    source = inspect.getsource(router_module.list_strategies)
    code = source[source.index('"""', source.index('"""') + 3) + 3 :]

    assert '.select("*")' in code
    assert 'select("last_signal_at' not in code
    assert '.eq("last_signal_at' not in code
    assert '.is_("last_signal_at' not in code
    assert "last_signal_at" not in router_module._LIST_COLUMNS, (
        "_LIST_COLUMNS is a row-narrowing filter (``if column in row``), so a name added to "
        "it is DROPPED when the column does not exist - the absent-instead-of-null outcome"
    )


# ══════════════════════════════════════════════════════════════════════════
# 2. BC-4 - ``last_execution_at`` IS THE PER-STRATEGY MAXIMUM
#    (Requirements 4.1, 19.2)
# ══════════════════════════════════════════════════════════════════════════


def test_the_aggregate_returns_the_maximum_created_at_per_strategy(execution_db):
    """The statement itself: ``MAX(created_at) … GROUP BY strategy_id``, one tenant.

    ``BUSY_ID`` executed at ``EARLIER`` and at ``LATER``; the answer is ``LATER``.
    """
    _seed_executions(execution_db)
    repo = ExecutionRecordRepository(execution_db)

    latest = repo.get_last_execution_at_by_strategy(UUID(TENANT))

    assert _naive(latest[BUSY_ID]) == _naive(LATER)


def test_a_strategy_with_no_executions_is_absent_from_the_aggregate(execution_db):
    """``GROUP BY`` emits no group for no rows, so the caller reads a missing key as null.

    Deliberately absent rather than present-and-``None``: "never executed" and "executed at
    an unknown time" are different claims and only the first is true.
    """
    _seed_executions(execution_db)
    repo = ExecutionRecordRepository(execution_db)

    latest = repo.get_last_execution_at_by_strategy(UUID(TENANT))

    assert QUIET_ID not in latest
    assert latest.get(QUIET_ID) is None


def test_the_aggregate_is_scoped_to_one_tenant(execution_db):
    """Another tenant's execution - the most recent in the table - is not reachable."""
    _seed_executions(execution_db)
    repo = ExecutionRecordRepository(execution_db)

    latest = repo.get_last_execution_at_by_strategy(UUID(TENANT))

    assert FOREIGN_ID not in latest
    assert all(_naive(value) <= _naive(LATER) for value in latest.values()), (
        f"a foreign tenant's execution at {LATEST_FOREIGN} leaked into {latest}"
    )


def test_the_per_strategy_maximum_agrees_with_the_tenant_wide_summary(execution_db):
    """BC-4 is ``get_stats``' expression regrouped, so the two cannot disagree.

    The tenant-wide ``last_execution_at`` must be exactly the greatest of the per-strategy
    values. If it were not, this router would have introduced a second definition of "last
    execution", which is the thing task 12.4 explicitly rules out.
    """
    _seed_executions(execution_db)
    repo = ExecutionRecordRepository(execution_db)

    per_strategy = repo.get_last_execution_at_by_strategy(UUID(TENANT))
    tenant_wide = repo.get_stats(UUID(TENANT)).last_execution_at

    assert per_strategy
    assert _naive(tenant_wide) == max(_naive(value) for value in per_strategy.values())


def test_the_list_reports_the_max_execution_timestamp_and_null_without_one(execution_db):
    """THE CLAIM TASK 12.4 IS ACCOUNTABLE FOR, end to end through the endpoint."""
    _seed_executions(execution_db)

    body = _listing(_store(), execution_db)

    busy = _entry(body, BUSY_ID)
    quiet = _entry(body, QUIET_ID)

    assert _instant(busy["last_execution_at"]) == _naive(LATER), (
        f"expected the maximum of {EARLIER.isoformat()} and {LATER.isoformat()}, "
        f"got {busy['last_execution_at']!r}"
    )
    assert _instant(busy["last_execution_at"]) != _naive(EARLIER)
    assert "last_execution_at" in quiet, "the key must be present, not merely falsy"
    assert quiet["last_execution_at"] is None


def test_the_list_reports_null_rather_than_a_guess_when_the_store_is_unreadable():
    """An unreadable execution store: every row reports ``null``, and the listing answers 200.

    The Strategies page must not go down over an optional column, and it must not fill the
    column in either. The failure injected here is the one that actually happens - the
    session cannot be opened at all.
    """

    def _refuse():
        raise RuntimeError("could not connect to the execution store")

    with patch("backend_app.core.database.get_db_context", _refuse):
        body = _list(_store(), user_id=TENANT)

    assert body["total"] == 2
    for entry in body["strategies"]:
        assert entry["last_execution_at"] is None, entry["id"]
        assert "last_execution_at" in entry


def test_a_non_uuid_identity_reports_null_rather_than_raising(execution_db):
    """``execution_records.tenant_id`` is a UUID column, so a non-UUID id addresses nothing.

    ``_safe_uid``'s pattern admits non-UUID identities, and several suites use them. That
    must degrade to "nothing to report", never to an error and never to another tenant.
    """
    _seed_executions(execution_db)

    store = _Supabase({"strategies": [_strategy_row(BUSY_ID, user_id="user_not_a_uuid")]})
    with patch("backend_app.core.database.get_db_context", _binder(execution_db)):
        body = _list(store, user_id="user_not_a_uuid")

    assert body["total"] == 1
    assert body["strategies"][0]["last_execution_at"] is None


# ══════════════════════════════════════════════════════════════════════════
# 3. BOTH ADDITIONS ARE ADDITIVE  (Requirement 19.1)
# ══════════════════════════════════════════════════════════════════════════


#: Every field the endpoint published before BC-3 and BC-4, with the value the fixture row
#: gives it. Compared by equality, so a changed value or a changed type fails here.
def _pre_change_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    return {column: row[column] for column in router_module._LIST_COLUMNS if column in row}


def test_no_field_the_endpoint_already_published_changed(execution_db):
    """Requirement 19.1: the two keys are added and nothing else moves.

    Asserted against the source row rather than against a hardcoded expectation, so this
    catches a repointed field as well as a renamed one. ``buy_logic`` is excluded because
    ``_lift_dag_fields`` has always emptied its ``_nodes`` / ``_edges`` out of it.
    """
    _seed_executions(execution_db)
    store = _store(last_signal_at=SIGNALLED_AT)
    source_row = store.row("strategies", BUSY_ID)

    entry = _entry(_listing(store, execution_db), BUSY_ID)

    for column, expected in _pre_change_fields(source_row).items():
        if column == "buy_logic":
            continue
        assert entry[column] == expected, f"{column}: was {expected!r}, now {entry[column]!r}"

    # The archival keys task 5.2 added are likewise untouched.
    assert entry["is_archived"] is False
    assert entry["archived_at"] is None

    # And the columns this endpoint has never published still do not travel.
    for withheld in ("sell_logic", "risk", "indicators", "ml_model_path", "user_id"):
        assert withheld not in entry


def test_the_row_set_and_its_order_are_unchanged(execution_db):
    """The execution read is a separate statement, so it cannot filter or reorder the list.

    The strategy query is byte-for-byte the one task 5.2 left: same table, same ``user_id``
    predicate, same ``created_at DESC`` order, no join. BC-4 reads a different store
    entirely and the result is only ever *indexed into*, so a strategy with no execution row
    is still listed - which is what ``QUIET_ID`` appearing here proves.
    """
    _seed_executions(execution_db)
    store = _store(extra=2)

    with_bc4 = [entry["id"] for entry in _listing(store, execution_db)["strategies"]]
    with _execution_store() as empty:  # the same statement over an empty relation
        without_any = [entry["id"] for entry in _listing(store, empty)["strategies"]]

    assert with_bc4 == without_any, (
        "the listing returned a different row set depending on what the execution store "
        "held; BC-4 must not be able to filter the page"
    )
    assert BUSY_ID in with_bc4 and QUIET_ID in with_bc4
    assert len(with_bc4) == 4


def test_the_whole_page_costs_exactly_one_execution_store_read(execution_db):
    """No N+1: one grouped aggregate for the page, not one statement per strategy.

    Four strategies here. A per-row implementation would read four times, and on a real
    account with fifty strategies it would read fifty times on every page load.
    """
    _seed_executions(execution_db)
    reads: List[str] = []

    real = router_module._read_last_execution_at_by_strategy

    def counting(user_id: str) -> Dict[str, Any]:
        reads.append(user_id)
        return real(user_id)

    with patch.object(router_module, "_read_last_execution_at_by_strategy", counting):
        with patch("backend_app.core.database.get_db_context", _binder(execution_db)):
            body = _list(_store(extra=2), user_id=TENANT)

    assert body["total"] == 4
    assert reads == [TENANT], (
        f"the execution store was read {len(reads)} times for a 4-strategy page; BC-4 must "
        "issue one grouped aggregate, not one query per strategy"
    )


def test_the_aggregate_groups_by_strategy_and_takes_a_maximum():
    """The statement is the regrouped ``get_stats`` expression, read off the source.

    A future edit that replaced the aggregate with a per-row lookup, or with a different
    timestamp column, would still satisfy the value assertions above on this fixture. This
    holds the *shape* of the read that makes them cheap and makes them mean "last".
    """
    source = inspect.getsource(
        ExecutionRecordRepository.get_last_execution_at_by_strategy
    )

    assert "MAX(created_at)" in source
    assert "GROUP BY strategy_id" in source
    assert "WHERE tenant_id = :tenant_id" in source
    assert "MAX(created_at)" in inspect.getsource(ExecutionRecordRepository.get_stats), (
        "get_stats no longer uses MAX(created_at); the two definitions of 'last execution' "
        "have drifted apart"
    )


def test_the_projection_never_substitutes_a_clock_read():
    """``_iso`` has no default: ``None`` in, ``None`` out.

    The single place a fabricated timestamp could enter this projection is a fallback in the
    normaliser, so it is asserted not to have one.
    """
    assert router_module._iso(None) is None
    assert router_module._iso(LATER) == LATER.isoformat()
    assert router_module._iso(SIGNALLED_AT) == SIGNALLED_AT

    source = inspect.getsource(router_module._iso)
    for forbidden in ("now(", "utcnow", "time.time"):
        assert forbidden not in source, f"_iso reaches for {forbidden}"


# ══════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════


def _binder(session: Any):
    """A ``get_db_context`` stand-in that yields ``session``."""
    from contextlib import contextmanager

    @contextmanager
    def _bound() -> Iterator[Any]:
        yield session

    return _bound


def _naive(value: Optional[datetime]) -> Optional[datetime]:
    """``value`` without its tzinfo, for comparison across drivers.

    SQLite returns ``DateTime(timezone=True)`` columns naive while PostgreSQL returns them
    aware. Both describe the same instant here - every fixture instant is written in UTC -
    and the claim under test is "which row won the maximum", not how a driver spells a zone.
    """
    if value is None:
        return None
    return value.replace(tzinfo=None)


def _instant(value: Optional[str]) -> Optional[datetime]:
    """A published ISO-8601 string back as an instant, for the same reason as :func:`_naive`."""
    if value is None:
        return None
    return _naive(datetime.fromisoformat(value))


# ══════════════════════════════════════════════════════════════════════════
# 4. BC-3's PRODUCER — ``last_signal_at`` IS NOW A REAL FIGURE
#    (task 12.3's follow-up; Requirements 4.1, 19.1, 19.2)
#
# Everything above this line tests the READER, and the reader was never the problem: it
# published ``last_signal_at`` correctly and reported ``null`` honestly. The problem was
# that ``null`` was the answer FOREVER — no migration in this repository declared
# ``strategies.last_signal_at`` and nothing wrote it, so the Strategies page's "Last signal"
# column had a permanent not-available state, which is the one outcome Task 12's preamble
# rules out.
#
# The producer is ``015_strategy_last_signal_at.sql`` (the column) plus
# ``backend_app/backend/strategy_last_signal.py`` (the write). This section drives the REAL
# signal-recording path — ``signal_service.generate_signal`` — against the same PostgREST
# double the listing is tested through, so one store holds both the ``signals`` row that was
# written and the ``strategies`` row that was updated, and the assertion is end to end:
# a strategy that signalled reports the instant it signalled at, through
# ``GET /api/strategies``.
#
# WHY THE WRITE IS SCHEDULED AND NOT AWAITED, AND WHY THAT IS TESTED HERE
#     The signal write path must not become able to fail. ``schedule_last_signal_at`` calls
#     ``asyncio.create_task`` and returns, so nothing on the signal path awaits the update:
#     it cannot raise into it and cannot slow it down. Both halves of that are asserted
#     below rather than asserted in a comment — a raising update and a HANGING update both
#     have to leave ``generate_signal`` returning a persisted signal.
# ══════════════════════════════════════════════════════════════════════════

#: The instant the signal under test is generated at. Fixed and passed to
#: ``generate_signal(now=...)``, so "the strategy reports the instant it signalled" is a
#: comparison against a stated value rather than against whatever the clock said.
SIGNAL_GENERATED_AT = datetime(2024, 5, 3, 11, 22, 33, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fresh_last_signal_probe():
    """The "015 is unapplied" verdict is cached per process; no test may inherit another's."""
    from backend_app.backend import signal_service as signal_module
    from backend_app.backend import strategy_last_signal as producer

    producer.reset_last_signal_column_support()
    signal_module.reset_signal_lifecycle_column_support()
    yield
    producer.reset_last_signal_column_support()
    signal_module.reset_signal_lifecycle_column_support()


def _signal_store(*, absent_columns=(), failing_tables=(), extra: int = 0) -> Any:
    """The two strategies and an empty ``signals`` table, in one PostgREST double.

    Neither strategy row carries ``last_signal_at``, which is the state of a database the
    moment 015 is applied: the column exists and every row is ``NULL``.
    """
    store = _store(extra=extra)
    store.rows["signals"] = []
    store.absent_columns = set(absent_columns)
    store.failing_tables = set(failing_tables)
    return store


def _generate_signal_for(
    store: Any,
    *,
    strategy_id: str = BUSY_ID,
    user_id: str = TENANT,
    now: Optional[datetime] = SIGNAL_GENERATED_AT,
) -> Any:
    """One signal through the real ``generate_signal``, with its scheduled update drained.

    ``deployment_row`` / ``action_output`` are task 10.1's own fixtures, imported rather than
    re-implemented, so this exercises the attribution the live path actually mints from.

    The drain is what makes the fire-and-forget write observable: the production path
    deliberately does not await it, so a test that did not drain would be asserting against
    a race. Draining is a *test* affordance and not a production one — nothing on the signal
    path calls it.
    """
    from backend_app.backend.signal_service import generate_signal
    from backend_app.backend.strategy_last_signal import drain_last_signal_at_writes
    from tests.test_task_10_1_generate_signal import action_output, deployment_row

    async def _go() -> Any:
        signal = await generate_signal(
            deployment_row(user_id=user_id, strategy_id=strategy_id),
            action_output(),
            sb=store,
            now=now,
        )
        await drain_last_signal_at_writes()
        return signal

    return asyncio.run(_go())


def _signals_in(store: Any) -> List[Dict[str, Any]]:
    return list(store.rows.get("signals", []))


def test_a_strategy_that_has_signalled_reports_the_real_instant_end_to_end(execution_db):
    """THE CLAIM THIS FOLLOW-UP IS ACCOUNTABLE FOR.

    Generate one signal for ``BUSY_ID`` through the real recording path, then read
    ``GET /api/strategies``. The busy strategy reports the instant it signalled at, and the
    quiet one — which produced no signal — still reports ``null``. Before this change the
    first of those was ``null`` too, on every database, permanently.
    """
    _seed_executions(execution_db)
    store = _signal_store()

    signal = _generate_signal_for(store)

    body = _listing(store, execution_db)
    busy = _entry(body, BUSY_ID)

    assert busy["last_signal_at"] is not None, (
        "the strategy signalled and the list still reports null; the producer did not write "
        "the column, so BC-3's field is still permanently not-available"
    )
    assert _instant(busy["last_signal_at"]) == _naive(SIGNAL_GENERATED_AT)
    # The value is the SIGNAL ROW's own generated_at, not a second clock read taken while
    # updating the strategy. One instant, recorded once, published twice.
    assert busy["last_signal_at"] == signal.generated_at
    assert _signals_in(store)[0]["generated_at"] == signal.generated_at

    # And nothing was applied to the whole page: the strategy that did not signal is null.
    assert _entry(body, QUIET_ID)["last_signal_at"] is None


def test_a_strategy_that_has_not_signalled_keeps_no_last_signal_at_at_all(execution_db):
    """Requirement 19.2. The quiet row is not written to — not even with ``NULL``.

    A producer that touched every row would make "never signalled" and "signalled, value
    unknown" the same state in the database. The update is addressed by strategy id, so the
    quiet row is left exactly as it was.
    """
    _seed_executions(execution_db)
    store = _signal_store()

    _generate_signal_for(store)

    quiet_row = store.row("strategies", QUIET_ID)
    assert "last_signal_at" not in quiet_row, (
        f"the producer touched a strategy that did not signal: {quiet_row.get('last_signal_at')!r}"
    )
    assert _entry(_listing(store, execution_db), QUIET_ID)["last_signal_at"] is None


def test_the_signal_is_persisted_even_when_the_timestamp_update_cannot_be_written(
    execution_db,
):
    """015 UNAPPLIED. The signal survives; only the denormalised timestamp is skipped.

    This is the state of every database until 015 is applied by hand, reproduced the way
    PostgREST really reports it: an UPDATE naming a column the schema cache does not have
    fails with ``PGRST204``. The signal row must still be written and returned, and the
    listing must still answer 200 with ``last_signal_at`` present and ``null`` — never a
    guessed instant, and never a 500.
    """
    _seed_executions(execution_db)
    store = _signal_store(absent_columns={"last_signal_at"})

    signal = _generate_signal_for(store)

    assert signal is not None
    assert len(_signals_in(store)) == 1, "the signal row was lost to a failed side write"
    assert _signals_in(store)[0]["id"] == signal.id

    body = _listing(store, execution_db)
    for entry in body["strategies"]:
        assert "last_signal_at" in entry, entry["id"]
        assert entry["last_signal_at"] is None, entry["id"]


def test_the_signal_is_persisted_even_when_the_strategies_table_is_unreachable(
    execution_db,
):
    """A real outage on the strategy row, not a missing column. Same outcome.

    ``failing_tables`` makes every statement against ``strategies`` raise. The signal write
    goes to ``signals`` and is unaffected, which is the whole point of scheduling the update
    off the path rather than awaiting it inside a ``try``.
    """
    _seed_executions(execution_db)
    store = _signal_store(failing_tables={"strategies"})

    signal = _generate_signal_for(store)

    assert signal is not None
    assert len(_signals_in(store)) == 1
    assert _signals_in(store)[0]["id"] == signal.id


def test_the_signal_is_persisted_even_when_the_update_raises_outright(execution_db):
    """Belt and braces: the producer itself blows up, and the signal is still recorded.

    ``record_last_signal_at`` is written not to raise, so this replaces it with one that
    does. The signal path must not notice, because it never awaits the task.
    """
    from backend_app.backend import strategy_last_signal as producer

    _seed_executions(execution_db)
    store = _signal_store()

    async def _explode(*_args: Any, **_kwargs: Any) -> bool:
        raise RuntimeError("the producer is broken")

    with patch.object(producer, "record_last_signal_at", _explode):
        signal = _generate_signal_for(store)

    assert signal is not None
    assert len(_signals_in(store)) == 1
    assert store.row("strategies", BUSY_ID).get("last_signal_at") is None


def test_a_hanging_timestamp_update_does_not_hold_the_signal_up(execution_db):
    """THE CONSTRAINT THAT SHAPED THE DESIGN: the update cannot DELAY the signal either.

    "Cannot fail" is not enough on this path. An awaited update — even one wrapped in a
    total ``try/except`` — would still let a hanging PostgREST request sit between a
    generated signal and risk validation. So the update is *scheduled*, and this test proves
    it by making it never finish: ``generate_signal`` still returns a persisted signal.
    """
    from backend_app.backend import strategy_last_signal as producer
    from backend_app.backend.signal_service import generate_signal
    from tests.test_task_10_1_generate_signal import action_output, deployment_row

    _seed_executions(execution_db)
    store = _signal_store()
    started = asyncio.Event()

    async def _never_returns(*_args: Any, **_kwargs: Any) -> bool:
        started.set()
        await asyncio.sleep(3600)
        return False

    async def _go() -> Any:
        with patch.object(producer, "record_last_signal_at", _never_returns):
            signal = await asyncio.wait_for(
                generate_signal(
                    deployment_row(user_id=TENANT, strategy_id=BUSY_ID),
                    action_output(),
                    sb=store,
                    now=SIGNAL_GENERATED_AT,
                ),
                timeout=5,
            )
            # Let the scheduled task actually start, so this is a hang and not merely a
            # task that never ran, then abandon it the way a shutdown would.
            await asyncio.wait_for(started.wait(), timeout=5)
            for task in tuple(producer._pending):
                task.cancel()
            return signal

    signal = asyncio.run(_go())

    assert signal is not None, "generate_signal waited on the timestamp update"
    assert len(_signals_in(store)) == 1


def test_the_producer_cannot_move_another_tenants_strategy(execution_db):
    """The UPDATE is addressed by strategy id AND owner, beside RLS.

    A signal whose ``user_id`` is not the strategy row's owner updates zero rows rather than
    writing a timestamp onto someone else's strategy.
    """
    from backend_app.backend.strategy_last_signal import record_last_signal_at

    _seed_executions(execution_db)
    store = _signal_store()

    wrote = asyncio.run(
        record_last_signal_at(
            store,
            strategy_id=BUSY_ID,
            user_id=FOREIGN_TENANT,
            generated_at=SIGNAL_GENERATED_AT.isoformat(),
        )
    )

    assert wrote is False, "the producer reported a write it was not allowed to make"
    assert "last_signal_at" not in store.row("strategies", BUSY_ID)
    assert _entry(_listing(store, execution_db), BUSY_ID)["last_signal_at"] is None


def test_the_producer_never_substitutes_a_clock_read():
    """Requirement 19.2 at the one place a fabricated instant could enter the column.

    ``_instant`` has no default, so an absent ``generated_at`` writes nothing rather than
    "now". Asserted as behaviour *and* off the source, because a fallback added later would
    satisfy neither reading.
    """
    from backend_app.backend import strategy_last_signal as producer

    assert producer._instant(None) is None
    assert producer._instant("   ") is None
    assert producer._instant(SIGNAL_GENERATED_AT) == SIGNAL_GENERATED_AT.isoformat()

    store = _signal_store()
    for missing in (None, "", "   "):
        assert (
            asyncio.run(
                producer.record_last_signal_at(
                    store, strategy_id=BUSY_ID, user_id=TENANT, generated_at=missing
                )
            )
            is False
        )
    assert "last_signal_at" not in store.row("strategies", BUSY_ID)
    assert not [write for write in store.writes if write[1] == "strategies"]

    source = inspect.getsource(producer._instant)
    for forbidden in ("now(", "utcnow", "time.time", "today("):
        assert forbidden not in source, f"_instant reaches for {forbidden}"


def test_the_producer_writes_the_same_column_the_projection_reads():
    """One spelling of "last signal" across the migration, the writer and the reader."""
    from backend_app.backend import strategy_last_signal as producer

    assert producer.LAST_SIGNAL_AT_COLUMN == router_module.LAST_SIGNAL_AT_COLUMN
    assert producer.STRATEGIES_TABLE == "strategies"
    # The degradation warning has to name the file an operator must apply, or a null field
    # is indistinguishable from a broken one in the logs.
    assert producer.STRATEGY_LAST_SIGNAL_MIGRATION.endswith(
        "015_strategy_last_signal_at.sql"
    )


def test_the_signal_path_schedules_the_update_and_never_awaits_it():
    """The isolation is structural, so it is asserted structurally as well as behaviourally.

    ``_persist_signal`` must call ``schedule_last_signal_at`` — which returns a task — and
    must not ``await`` the producer. An edit that changed the call to ``await
    record_last_signal_at(...)`` would keep every value assertion above passing while
    reintroducing the failure mode this whole design exists to avoid.
    """
    from backend_app.backend import signal_service as signal_module

    source = inspect.getsource(signal_module._persist_signal)

    assert "schedule_last_signal_at(" in source
    assert "await schedule_last_signal_at" not in source
    assert "record_last_signal_at" not in source, (
        "_persist_signal reaches the producer directly instead of scheduling it; the "
        "signal path must not be able to wait on the timestamp update"
    )


def test_the_page_costs_no_read_per_strategy_for_last_signal_at(execution_db):
    """No N+1, and no second query at all: the column travels on the row already read.

    ``list_strategies`` reads ``select("*")``, so ``last_signal_at`` arrives with the
    strategy row. There is nothing to aggregate and nothing to join — unlike BC-4, which
    needs one grouped aggregate against a different store. Four strategies here, and the
    ``signals`` table is not read even once.
    """
    _seed_executions(execution_db)
    store = _signal_store(extra=2)

    _generate_signal_for(store)
    reads_before = len(store.selects)
    writes_before = len(store.writes)

    body = _listing(store, execution_db)

    assert body["total"] == 4
    listing_reads = store.selects[reads_before:]
    assert [table for table, _ in listing_reads] == ["strategies"], (
        f"a 4-strategy page issued {listing_reads} against PostgREST; last_signal_at must "
        "cost no read of its own"
    )
    assert store.writes[writes_before:] == [], "the listing wrote to the database"


def test_the_producer_does_not_swallow_its_own_cancellation():
    """"Never raises" means never raises a FAILURE — not "refuses to be cancelled".

    A fire-and-forget task that ate its own ``CancelledError`` would hang an orderly
    shutdown while every other task wound down. Cancellation cannot reach the signal path
    from here, because the signal path never awaits this task.
    """
    from backend_app.backend import strategy_last_signal as producer

    class _Cancelling:
        def table(self, _name: str) -> "_Cancelling":
            return self

        def update(self, _payload: Any) -> "_Cancelling":
            return self

        def eq(self, *_args: Any) -> "_Cancelling":
            return self

        async def execute(self) -> Any:
            raise asyncio.CancelledError()

    async def _go() -> None:
        await producer.record_last_signal_at(
            _Cancelling(),
            strategy_id=BUSY_ID,
            user_id=TENANT,
            generated_at=SIGNAL_GENERATED_AT.isoformat(),
        )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_go())

    # And a cancellation is not misfiled as "015 is unapplied", which would stop the
    # producer re-attempting for the whole re-check window.
    assert producer.last_signal_column_is_absent() is False
