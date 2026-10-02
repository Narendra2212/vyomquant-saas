"""tests/test_mounted_endpoint_projections.py — what a mounted endpoint ANSWERS when
its projection names a column ``public.<table>`` does not have.

WHAT THIS FILE ADDS THAT THE DRIFT SUITE CANNOT
-----------------------------------------------
``tests/test_schema_table_reference_drift.py`` proves a PARSE-LEVEL fact: no code path
under ``backend_app/`` names a column the migration set and the recorded production roster
do not declare, except the ones its register still carries. That is the right shape of
guard for drift, and it says nothing about what the endpoint carrying such a reference
*answers*. Both defects task 13.19 fixes were verdicts:

  * ``GET /api/admin/users`` selected ``profiles.volume_usd`` with NO ``try/except`` around
    the chain, so the mounted, admin-gated endpoint **500ed unconditionally** — it had
    never once been able to answer.
  * ``GET /api/signals/`` selected twelve columns ``public.execution_records`` does not
    have, inside a blanket ``except Exception`` that turns any failure into a 503, so it
    answered ``503 SIGNAL_FETCH_FAILED`` for every caller regardless of stored data.

So this file observes the verdict itself, before and after.

THE DOUBLE IS SCHEMA-FAITHFUL, WHICH IS THE WHOLE POINT
-------------------------------------------------------
A permissive test double — one whose ``.select()`` ignores its projection — CANNOT
reproduce either defect. It hands back the row happily with the pre-fix projection and the
handler passes. That is precisely why the existing suites stayed green for the whole life
of both bugs: ``tests/test_exception_swallow_regression.py`` drives ``GET /api/signals/``
with a client whose ``table()`` raises an *injected* ``Exception``, which proves the 503
path is wired but cannot distinguish "the database is down" from "this projection can
never succeed".

:class:`_SchemaFaithfulTable` therefore raises a ``42703``-shaped error for any column the
relation does not have, as Postgres does, and narrows returned rows to the projection, as
Postgres does. This is task 13.18's
``tests/test_marketplace_eligibility_tenant_verdict.py`` pattern, reused.

Its notion of "the columns the table has" is NOT hand-written here: it is the drift suite's
own ``_allowed_columns`` oracle — migration-parsed for tables a ``CREATE TABLE`` declares,
plus ``PRODUCTION_COLUMNS_FOR_UNCREATED_TABLES`` for the ones only ``ALTER``ed — the same
oracle that polices the real reads, and the one
``test_the_recorded_production_column_sets_are_still_what_they_claim`` keeps honest. 13.18
recorded that the marketplace manifest "would have caught this defect the day it was
written had the manifest been derived from the schema instead of hand-listed beside it";
this file takes that lesson rather than restating a column list.

The oracle was re-confirmed against production (PostgreSQL 17.6, read-only
``SELECT "<col>" FROM public."<table>" LIMIT 0``) while this file was written:
``profiles.volume_usd`` → ``42703`` with ``profiles.subscription_tier`` → OK on the same
table; all twelve ``execution_records`` names → ``42703`` with ``user_id`` and ``symbol``
→ OK; every column replay reads off ``signals`` → OK, and ``signals.created_at`` and
``signals.side`` → ``42703``.

**Validates: Requirements 2.13**
"""

import asyncio
import os
import sys
from typing import Any, Dict, FrozenSet, List, Optional, Tuple
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.core.dependencies import (  # noqa: E402
    get_admin_user,
    get_current_user,
    get_request_supabase,
)
from backend_app.main import app  # noqa: E402
from tests.test_schema_table_reference_drift import (  # noqa: E402
    RETIRED_PRODUCTION_COLUMNS_EXECUTION_RECORDS,
    _allowed_columns,
)

ADMIN_ID = "11111111-1111-4111-8111-111111111111"
OWNER_ID = "22222222-2222-4222-8222-222222222222"
SIGNAL_ID = "33333333-3333-4333-8333-333333333333"

#: The pre-fix projection, verbatim from ``routers/admin.py`` before task 13.19. Replayed
#: by :func:`test_the_pre_fix_admin_projection_still_cannot_answer` so the before/after is
#: a measurement and not a claim.
PRE_FIX_ADMIN_PROJECTION = (
    "id, username, email, subscription_tier, volume_usd, is_frozen, created_at"
)

#: The pre-fix ``list_signal_traces`` projection, verbatim. Twelve of these twenty names
#: are columns ``public.execution_records`` does not have.
PRE_FIX_SIGNAL_LIST_PROJECTION = (
    "id, strategy_id, exchange_id, symbol, timeframe, indicators, ml_inputs, "
    "ml_outputs, confidence, side, risk_verdict, status, filled_quantity, quantity, "
    "price, latency_ms, pnl, failure_reason, created_at, updated_at"
)


def _columns_of(table: str) -> FrozenSet[str]:
    """The columns ``public.<table>`` has, from the drift suite's oracle.

    ``execution_records`` is the one special case, and it is special BECAUSE of this
    fix. The drift suite's oracle covers tables application code reads; task 13.19
    deleted the only three reads of that table, so its roster entry had to go (an unread
    entry is a permanent unchecked exemption, and
    ``test_the_recorded_production_column_sets_are_still_what_they_claim`` says so). The
    22-column measurement was retired to a constant rather than discarded, and that
    constant is what the before-case below needs: a table whose columns are known, so
    the pre-fix projection can be shown to be refused by it.
    """
    live = _allowed_columns(table)
    if live is not None:
        return live
    if table == "execution_records":
        return RETIRED_PRODUCTION_COLUMNS_EXECUTION_RECORDS
    raise AssertionError(
        "%s has no column oracle in tests/test_schema_table_reference_drift.py, so "
        "this double cannot be schema-faithful about it. Add it to the roster rather "
        "than guessing here." % table
    )


class _UndefinedColumn(Exception):
    """A fake Postgres ``42703`` for a column the relation does not have.

    Shaped like the driver error the real reads raised: a ``.code`` of ``"42703"`` and a
    message naming the relation and the column.
    """

    def __init__(self, table: str, column: str) -> None:
        self.code = "42703"
        self.message = 'column "%s" of relation "%s" does not exist' % (column, table)
        super().__init__(self.message)


class _Resp:
    def __init__(self, data: Any) -> None:
        self.data = data


def _projected_columns(projection: str) -> List[str]:
    """The column names a PostgREST ``.select()`` literal asks for.

    Both projections under test are flat comma-separated lists. ``*`` asks for whatever
    exists and names nothing undefined.
    """
    return [
        part.strip()
        for part in str(projection).split(",")
        if part.strip() and part.strip() != "*"
    ]


class _SchemaFaithfulTable:
    """A chain-able fake table that answers ``42703`` for a column it does not have.

    ``execute`` is a coroutine function: every handler under test does
    ``await ....execute()`` against ``_PooledAsyncPostgrestClient``, so a synchronous
    double would fail on the ``await`` instead of on the projection and the assertions
    would be vacuous.
    """

    def __init__(
        self,
        name: str,
        rows: List[Dict[str, Any]],
        columns: FrozenSet[str],
        journal: List[Tuple[str, Tuple[Any, ...]]],
    ) -> None:
        self._name = name
        self._rows = rows
        self._columns = columns
        self._journal = journal
        self._projection: List[str] = []
        self._filters: List[Tuple[str, str, Any]] = []

    def select(self, projection: str = "*", *args: Any, **kwargs: Any):
        self._projection = _projected_columns(projection)
        for column in self._projection:
            if column not in self._columns:
                raise _UndefinedColumn(self._name, column)
        return self

    def update(self, *args: Any, **kwargs: Any):
        return self

    def eq(self, column: str, value: Any):
        self._filters.append(("eq", column, value))
        return self

    def ilike(self, column: str, value: Any):
        self._filters.append(("ilike", column, value))
        return self

    def order(self, *args: Any, **kwargs: Any):
        return self

    def limit(self, *args: Any, **kwargs: Any):
        return self

    def _matched(self) -> List[Dict[str, Any]]:
        result = list(self._rows)
        for kind, column, value in self._filters:
            if kind == "eq":
                result = [r for r in result if str(r.get(column, "")) == str(value)]
        return result

    async def execute(self) -> _Resp:
        self._journal.append((self._name, tuple(self._filters)))
        rows = self._matched()
        if not self._projection:
            return _Resp(rows)
        return _Resp(
            [{column: row.get(column) for column in self._projection} for row in rows]
        )


class _SchemaFaithfulSupabase:
    """A Persistence_Layer double whose tables know their own columns."""

    def __init__(self, stores: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> None:
        self.stores: Dict[str, List[Dict[str, Any]]] = dict(stores or {})
        self.journal: List[Tuple[str, Tuple[Any, ...]]] = []

    def table(self, name: str) -> _SchemaFaithfulTable:
        return _SchemaFaithfulTable(
            name, self.stores.setdefault(name, []), _columns_of(name), self.journal
        )

    @property
    def tables_read(self) -> Tuple[str, ...]:
        return tuple(table for table, _filters in self.journal)

    def filters_for(self, table: str) -> Tuple[Tuple[str, str, Any], ...]:
        for name, filters in self.journal:
            if name == table:
                return filters
        return ()


def _admin():
    return {
        "id": ADMIN_ID,
        "email": "admin@vyomquant.in",
        "role": "authenticated",
        "access_token": "fake-token",
    }


def _caller():
    return {"id": OWNER_ID, "email": "trader@vyomquant.in", "access_token": "fake-token"}


def _profiles_db() -> _SchemaFaithfulSupabase:
    """Two real profile rows. Every key here is a column ``public.profiles`` has."""
    return _SchemaFaithfulSupabase(
        {
            "profiles": [
                {
                    "id": ADMIN_ID,
                    "username": "admin",
                    "email": "admin@vyomquant.in",
                    "subscription_tier": "pro",
                    "is_frozen": False,
                    "created_at": "2025-01-02T00:00:00Z",
                },
                {
                    "id": OWNER_ID,
                    "username": "trader",
                    "email": "trader@vyomquant.in",
                    "subscription_tier": "free",
                    "is_frozen": True,
                    "created_at": "2025-01-01T00:00:00Z",
                },
            ]
        }
    )


def _signal_row() -> Dict[str, Any]:
    """One ``public.signals`` row. Deliberately carries NO ``created_at`` and no ``side``:
    production answers ``42703`` for both, so a payload that depended on either would be
    reading a column that does not exist.
    """
    return {
        "id": SIGNAL_ID,
        "user_id": OWNER_ID,
        "decision": "buy",
        "symbol": "BTC/USDT",
        "strategy_id": "strat-1",
        "generated_at": "2025-02-03T10:00:00Z",
        "indicators": {"rsi": 45.0},
        "market_info": {"price": 50100.0},
        "ml_info": {"prediction": "LONG"},
    }


@pytest.fixture
def clean_overrides():
    yield
    app.dependency_overrides.clear()


def _route_paths() -> List[str]:
    return [getattr(r, "path", "") for r in app.routes]


# ==========================================================================
# FIX A — GET /api/admin/users
# ==========================================================================


class TestAdminUserListCanAnswer:
    def test_the_admin_user_list_answers_rather_than_500ing(self, clean_overrides):
        """The verdict. Against a ``profiles`` that has exactly the columns production
        has, the endpoint returns the rows — it does not raise out of the handler.
        """
        db = _profiles_db()
        app.dependency_overrides[get_admin_user] = _admin
        app.dependency_overrides[get_request_supabase] = lambda: db

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/admin/users")

        assert response.status_code == 200, (
            "GET /api/admin/users answered %s against a schema-faithful profiles table. "
            "Body: %s" % (response.status_code, response.text)
        )
        body = response.json()
        assert len(body) == 2, body
        assert db.tables_read == ("profiles",), db.tables_read

    def test_no_row_carries_a_volume_usd_key(self, clean_overrides):
        """``volume_usd`` is gone rather than present-and-always-null. A key that is
        always absent is the shape ``bugfix.md`` forbids: a figure nothing computes.
        """
        app.dependency_overrides[get_admin_user] = _admin
        app.dependency_overrides[get_request_supabase] = lambda: _profiles_db()

        client = TestClient(app, raise_server_exceptions=False)
        body = client.get("/api/admin/users").json()

        for row in body:
            assert "volume_usd" not in row, row

    def test_the_two_branches_of_the_handler_agree_on_shape(self, clean_overrides):
        """The no-supabase branch returns ``id``, ``email``, ``subscription_tier`` and
        ``is_frozen`` and never carried ``volume_usd``. The supabase branch must be a
        superset of those keys, so the two no longer diverge on the field.
        """
        app.dependency_overrides[get_admin_user] = _admin
        app.dependency_overrides[get_request_supabase] = lambda: None
        client = TestClient(app, raise_server_exceptions=False)
        fallback = client.get("/api/admin/users").json()
        assert len(fallback) == 1, fallback
        fallback_keys = set(fallback[0])

        app.dependency_overrides[get_request_supabase] = lambda: _profiles_db()
        live = client.get("/api/admin/users").json()
        live_keys = set(live[0])

        assert "volume_usd" not in fallback_keys
        assert "volume_usd" not in live_keys
        assert fallback_keys <= live_keys, (
            "the no-supabase branch returns keys the supabase branch does not: %s"
            % (fallback_keys - live_keys)
        )

    def test_the_pre_fix_admin_projection_still_cannot_answer(self, clean_overrides):
        """THE BEFORE CASE, replayed. The same double, the same rows, the pre-fix
        projection literal — and ``profiles`` refuses it with a ``42703``.

        This is what pins the double as capable of reproducing the bug. Without it the
        test above could pass because the double is permissive rather than because the
        projection is now correct.
        """
        db = _profiles_db()
        with pytest.raises(_UndefinedColumn) as raised:
            db.table("profiles").select(PRE_FIX_ADMIN_PROJECTION)
        assert raised.value.code == "42703"
        assert "volume_usd" in raised.value.message

    def test_the_fixed_projection_is_what_the_handler_now_sends(self, clean_overrides):
        """And the post-fix projection is accepted by the same table, so the fix is the
        projection and not the double.
        """
        db = _profiles_db()
        survivors = PRE_FIX_ADMIN_PROJECTION.replace(" volume_usd,", "")
        rows = asyncio.run(db.table("profiles").select(survivors).execute()).data
        assert len(rows) == 2
        assert set(rows[0]) == {
            "id",
            "username",
            "email",
            "subscription_tier",
            "is_frozen",
            "created_at",
        }


# ==========================================================================
# FIX B — routers/signals.py
# ==========================================================================


class TestSupersededSignalEndpointsAreGone:
    """Three of ``routers/signals.py``'s four endpoints were superseded by
    ``routers/signal_trace.py``, which is the router the frontend actually calls.
    They are removed rather than left present-and-broken.
    """

    @pytest.mark.parametrize(
        "path",
        ["/api/signals/", "/api/signals/{signal_id}", "/api/signals/export"],
    )
    def test_the_route_is_absent_from_the_route_table(self, path):
        assert path not in _route_paths(), (
            "%s is still registered. A superseded endpoint that 503s is worse than one "
            "that is not there: it looks like a capability." % path
        )

    @pytest.mark.parametrize(
        "path", ["/api/signals/", "/api/signals/%s" % SIGNAL_ID, "/api/signals/export"]
    )
    def test_the_path_now_404s(self, path, clean_overrides):
        app.dependency_overrides[get_current_user] = _caller
        client = TestClient(app, raise_server_exceptions=False)
        assert client.get(path).status_code == 404, path

    def test_the_replay_route_survived(self):
        """The one capability ``signal_trace.py`` has no equivalent for stays, at its
        original path.
        """
        assert "/api/signals/{signal_id}/replay" in _route_paths()

    def test_the_pre_fix_list_projection_could_never_have_answered(self):
        """THE BEFORE CASE for the list endpoint, replayed against the same double that
        the surviving replay test uses.

        ``execution_records`` holds a complete, valid order-execution row here. The
        pre-fix twenty-column projection is still refused, because twelve of its names
        are not columns of that table — so ``GET /api/signals/`` answered
        ``503 SIGNAL_FETCH_FAILED`` for every caller, with data present, forever.
        """
        db = _SchemaFaithfulSupabase(
            {
                "execution_records": [
                    {
                        "execution_id": "exec-1",
                        "user_id": OWNER_ID,
                        "symbol": "BTC/USDT",
                        "side": "buy",
                        "size": 1.0,
                        "price": 50000.0,
                        "status": "filled",
                        "created_at": "2025-02-03T10:00:00Z",
                    }
                ]
            }
        )
        with pytest.raises(_UndefinedColumn) as raised:
            db.table("execution_records").select(PRE_FIX_SIGNAL_LIST_PROJECTION)
        assert raised.value.code == "42703"

        absent = {
            "id",
            "indicators",
            "ml_inputs",
            "ml_outputs",
            "confidence",
            "risk_verdict",
            "filled_quantity",
            "quantity",
            "latency_ms",
            "pnl",
            "failure_reason",
            "timeframe",
        }
        measured = _columns_of("execution_records")
        assert absent & measured == set(), (
            "execution_records is recorded as having %s, which this fix assumed it did "
            "not." % (absent & measured)
        )


class TestSignalReplayReadsOnlySignals:
    def _patched_sb(self, db: _SchemaFaithfulSupabase):
        async def _fake_sb(user):
            return db

        return patch("backend_app.routers.signals._sb", new=_fake_sb)

    def test_replay_reads_signals_and_never_touches_execution_records(self):
        """The fix, stated as a read trace. ``execution_records`` is seeded with a row
        carrying the same id, so a surviving fallback would be observable.
        """
        from backend_app.routers.signals import replay_signal_trace

        db = _SchemaFaithfulSupabase(
            {
                "signals": [_signal_row()],
                "execution_records": [
                    {
                        "execution_id": SIGNAL_ID,
                        "user_id": OWNER_ID,
                        "side": "sell",
                        "symbol": "ETH/USDT",
                        "created_at": "2025-02-03T10:00:00Z",
                    }
                ],
            }
        )

        with self._patched_sb(db):
            result = asyncio.run(replay_signal_trace(SIGNAL_ID, _caller()))

        assert db.tables_read == ("signals",), (
            "replay read %s; it must read signals and nothing else." % (db.tables_read,)
        )
        assert ("eq", "user_id", OWNER_ID) in db.filters_for("signals")
        assert result["stored_decision"] == "BUY"
        assert result["replay_implemented"] is False
        assert result["execution_metadata"]["timestamp"] == "2025-02-03T10:00:00Z", (
            "the timestamp must come from signals.generated_at; signals has no "
            "created_at column at all (production: 42703)."
        )
        assert result["execution_metadata"]["indicators"] == {"rsi": 45.0}

    def test_replay_404s_instead_of_falling_back_to_execution_records(self):
        """``signals`` holds nothing for this id and ``execution_records`` does. Before
        the fix that produced a payload built from the wrong table's row. Now it is an
        explicit absence, and the second table is never read.
        """
        from fastapi import HTTPException

        from backend_app.routers.signals import replay_signal_trace

        db = _SchemaFaithfulSupabase(
            {
                "signals": [],
                "execution_records": [
                    {
                        "execution_id": SIGNAL_ID,
                        "user_id": OWNER_ID,
                        "side": "sell",
                        "symbol": "ETH/USDT",
                        "created_at": "2025-02-03T10:00:00Z",
                    }
                ],
            }
        )

        with self._patched_sb(db):
            with pytest.raises(HTTPException) as raised:
                asyncio.run(replay_signal_trace(SIGNAL_ID, _caller()))

        assert raised.value.status_code == 404
        assert db.tables_read == ("signals",), (
            "replay fell back to %s after signals answered empty." % (db.tables_read,)
        )

    def test_replay_does_not_synthesise_a_decision(self):
        """A row whose ``decision`` is NULL yields ``None``, not ``"BUY"``.

        The pre-fix expression ended ``or "BUY"``, so an absent decision was rendered to
        an auditor as a buy — a fabricated fact in a compliance-retrieval payload, which
        is exactly ``bugfix.md``'s bug condition.
        """
        from backend_app.routers.signals import replay_signal_trace

        row = _signal_row()
        row["decision"] = None
        db = _SchemaFaithfulSupabase({"signals": [row]})

        with self._patched_sb(db):
            result = asyncio.run(replay_signal_trace(SIGNAL_ID, _caller()))

        assert result["stored_decision"] is None, result["stored_decision"]

    def test_another_tenants_signal_is_not_retrievable(self):
        """The tenant predicate survived the rewrite."""
        from fastapi import HTTPException

        from backend_app.routers.signals import replay_signal_trace

        db = _SchemaFaithfulSupabase({"signals": [_signal_row()]})
        stranger = {"id": "99999999-9999-4999-8999-999999999999", "access_token": "t"}

        with self._patched_sb(db):
            with pytest.raises(HTTPException) as raised:
                asyncio.run(replay_signal_trace(SIGNAL_ID, stranger))

        assert raised.value.status_code == 404


class TestExportIsNotShadowed:
    """``GET /api/signals/export`` was declared AFTER ``GET /api/signals/{signal_id}``,
    so Starlette matched ``export`` as a ``signal_id`` and the export handler was
    unreachable. Asserted through the mounted app's own matcher, because a
    registration-order bug cannot be reproduced on a router assembled in a test.
    """

    def _matched_path(self, path: str) -> Optional[str]:
        from starlette.routing import Match

        scope = {"type": "http", "method": "GET", "path": path, "headers": []}
        for route in app.routes:
            match, _child = route.matches(scope)
            if match is Match.FULL:
                return getattr(route, "path", "")
        return None

    def test_signal_trace_export_wins_over_the_signal_id_path(self):
        """THE CONTRAST. ``signal_trace.py`` declares ``/signals/export`` at line ~316
        and ``/signals/{signal_id}`` at ~444 — export first, so it is correct. Verified
        rather than assumed, since this is the one hazard that produced the defect next
        door.
        """
        assert (
            self._matched_path("/api/signal-trace/signals/export")
            == "/api/signal-trace/signals/export"
        )
        assert (
            self._matched_path("/api/signal-trace/signals/%s" % SIGNAL_ID)
            == "/api/signal-trace/signals/{signal_id}"
        )

    def test_no_surviving_signals_route_can_shadow_another(self):
        """``/api/signals`` now has one route, so the hazard is structurally gone rather
        than merely reordered.
        """
        signals_paths = [p for p in _route_paths() if p.startswith("/api/signals/")]
        assert signals_paths == ["/api/signals/{signal_id}/replay"], signals_paths
