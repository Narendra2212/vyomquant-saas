"""
tests/test_task_5_3_rename_strategy.py

Trading-lifecycle-integration task 5.3 — ``PUT /api/strategies/{id}/rename``.
Requirements 2.6, 3.3, 20.2.

WHAT THIS FILE HOLDS TO
-----------------------
* **The route is actually reachable, and is not the ``PUT /{strategy_id}`` handler in
  disguise.** ``strategies.py`` already declares ``PUT /{strategy_id}``, and a body of
  ``{"name": ...}`` sent to *that* handler would also answer 200 and also write
  ``{"name": ...}`` — so "the write looks right" proves nothing about which handler ran.
  Two independent assertions close that gap: the app's own router resolution for
  ``PUT /api/strategies/{id}/rename`` is asserted to land on ``rename_strategy``, and the
  behavioural discriminators are asserted too (a 101-character name is a 422 here and
  would be a *successful* edit there; a body with no ``name`` at all is a 422 here and
  would be the edit path's 403 there). ``PUT /api/strategies/{id}`` is asserted to still
  work, so the ordering did not shadow it in the other direction.
* **Requirement 2.6 is measured at its exact boundaries, on the TRIMMED name.** 0, 1, 100
  and 101 characters, plus whitespace-only and padded input. The trimmed name is asserted
  to be what is *stored*, so a name that only fits because of its padding cannot be
  persisted with the padding still on it.
* **"Touches only ``strategies.name``" is asserted about the WRITE.** The fake client
  records every write it is handed and applies it, so the claim is a fact about the
  statement the handler issued — one ``update`` on ``strategies`` whose payload has
  exactly one key — and the strategy's other columns and its versions, backtests,
  deployments and signals are read back afterwards to confirm they are untouched.
* **Requirement 3.3 is the real archive gate.** The refusal comes from task 5.1's
  :func:`~backend_app.backend.strategy_archive.assert_strategy_not_archived` with the
  ``OPERATION_RENAME`` constant that task published for this handler, so "archived" cannot
  mean one thing to a rename and another to an edit.
* **The doubles are the real thing wherever one exists.** The archive gate, the FastAPI
  route and the router module are real. The only double is the PostgREST client, which
  this environment has no instance of.

WHAT THIS FILE CANNOT PROVE
---------------------------
There is no PostgreSQL here, so nothing here proves RLS refuses a cross-tenant rename at
the database as well as the ``user_id`` filter does. The cross-tenant answer is asserted
only as "a non-owner gets the same 404 a missing strategy gets" — the shape Requirement
20.2 asks for.
"""

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import strategy_archive as archive
from backend_app.routers import strategies as router_module

USER_ID = "user_task_5_3"
OTHER_USER_ID = "user_task_5_3_other"
STRATEGY_ID = "11111111-1111-4111-8111-111111111111"
OTHER_STRATEGY_ID = "22222222-2222-4222-8222-222222222222"

ARCHIVED_AT = archive.ARCHIVED_AT_COLUMN
ARCHIVED_STAMP = "2024-05-05T05:05:05+00:00"
ORIGINAL_NAME = "Task 5.3 Strategy"

MIN_LENGTH = router_module.STRATEGY_NAME_MIN_LENGTH
MAX_LENGTH = router_module.STRATEGY_NAME_MAX_LENGTH


# ---------------------------------------------------------------------------
# Doubles (the pattern tasks 5.1 and 5.2 established)
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, data):
        self.data = data
        self.error = None


class _Query:
    """A chainable PostgREST stand-in that honours ``.eq``, APPLIES updates, and refuses
    a projection naming an absent column exactly as PostgREST does (``42703``)."""

    def __init__(self, parent, table):
        self._parent = parent
        self._table = table
        self._filters = {}
        self._mode = "select"
        self._payload = None

    def select(self, columns="*", *a, **kw):
        self._mode = "select"
        named = [c.strip() for c in columns.split(",")] if isinstance(columns, str) else []
        missing = [c for c in named if c and c != "*" and c in self._parent.absent_columns]
        if missing:
            raise RuntimeError(
                f"column {self._table}.{missing[0]} does not exist (42703 undefined_column)"
            )
        return self

    def insert(self, payload, *a, **kw):
        self._mode = "insert"
        self._payload = payload
        return self

    def update(self, payload, *a, **kw):
        self._mode = "update"
        self._payload = payload
        return self

    def delete(self, *a, **kw):
        self._mode = "delete"
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def is_(self, column, value):
        self._filters[f"is_{column}"] = value
        return self

    def order(self, *a, **kw):
        return self

    def limit(self, *a, **kw):
        return self

    def _matches(self, row):
        return all(str(row.get(key)) == str(want) for key, want in self._filters.items())

    async def execute(self):
        if self._table in self._parent.failing_tables:
            raise RuntimeError(f"connection to {self._table} failed (timeout)")

        rows = self._parent.rows.setdefault(self._table, [])
        offending = sorted(set(self._payload or {}) & self._parent.absent_columns)
        if self._mode in ("insert", "update") and offending:
            raise RuntimeError(
                f"Could not find the '{offending[0]}' column of '{self._table}' "
                f"in the schema cache (PGRST204)"
            )

        if self._mode == "select":
            self._parent.selects.append((self._table, dict(self._filters)))

        if self._mode == "insert":
            row = dict(self._payload)
            rows.append(row)
            self._parent.writes.append(("insert", self._table, dict(row), {}))
            return _Result([dict(row)])

        if self._mode == "update":
            self._parent.writes.append(
                ("update", self._table, dict(self._payload or {}), dict(self._filters))
            )
            touched = []
            for row in rows:
                if self._matches(row):
                    row.update(dict(self._payload or {}))
                    touched.append(dict(row))
            return _Result(touched)

        if self._mode == "delete":
            self._parent.writes.append(("delete", self._table, {}, dict(self._filters)))
            removed = [dict(row) for row in rows if self._matches(row)]
            self._parent.rows[self._table] = [r for r in rows if not self._matches(r)]
            return _Result(removed)

        return _Result([dict(row) for row in rows if self._matches(row)])


class _NoRepresentation(_Query):
    """A client that returns no representation for an ``update``, as PostgREST does unless
    asked for one. The row is still updated; only the reply is empty."""

    async def execute(self):
        result = await super().execute()
        if self._mode == "update":
            return _Result([])
        return result


class _Supabase:
    query_class = _Query

    def __init__(self, rows=None, absent_columns=(), failing_tables=()):
        self.rows = {k: [dict(r) for r in v] for k, v in (rows or {}).items()}
        self.absent_columns = set(absent_columns)
        self.failing_tables = set(failing_tables)
        self.writes = []
        self.selects = []

    def table(self, name):
        return self.query_class(self, name)

    def row(self, table, row_id):
        for row in self.rows.get(table, []):
            if str(row.get("id")) == str(row_id):
                return row
        return None

    def writes_of(self, mode, table=None):
        return [w for w in self.writes if w[0] == mode and (table is None or w[1] == table)]


class _SupabaseNoRepresentation(_Supabase):
    query_class = _NoRepresentation


def _user(user_id=USER_ID):
    return {"id": user_id, "email": f"{user_id}@example.com", "access_token": "tok"}


def _strategy_row(strategy_id=STRATEGY_ID, user_id=USER_ID, **overrides):
    row = {
        "id": strategy_id,
        "user_id": user_id,
        "name": ORIGINAL_NAME,
        "description": "unchanged",
        "symbol": "BTC/USDT",
        "timeframe": "5m",
        "status": "stopped",
        "buy_logic": {"_nodes": [{"id": "n1"}], "_edges": []},
        "sell_logic": {"rule": "unchanged"},
        "risk": {"max_dd": 0.1},
        "version": 1,
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-02T00:00:00+00:00",
        ARCHIVED_AT: None,
    }
    row.update(overrides)
    return row


#: The owner's strategy, another tenant's strategy, and one row in each table
#: Requirement 2.6 says a rename must leave unchanged.
def _rows(strategy=None):
    return {
        "strategies": [
            strategy or _strategy_row(),
            _strategy_row(OTHER_STRATEGY_ID, OTHER_USER_ID, name="THEIRS"),
        ],
        "strategy_versions": [{"id": "v-1", "strategy_id": STRATEGY_ID, "version": 1}],
        "strategy_backtests": [{"id": "b-1", "strategy_id": STRATEGY_ID, "outcome": "ok"}],
        "strategy_deployments": [
            {"id": "d-1", "strategy_id": STRATEGY_ID, "user_id": USER_ID, "status": "stopped"}
        ],
        "signals": [{"id": "s-1", "strategy_id": STRATEGY_ID, "status": "executed"}],
        "signal_events": [{"id": "e-1", "signal_id": "s-1"}],
    }


UNTOUCHED_TABLES = (
    "strategy_versions",
    "strategy_backtests",
    "strategy_deployments",
    "signals",
    "signal_events",
)


@pytest.fixture(autouse=True)
def fresh_column_probe():
    """The 005a verdict is cached per process; no test may inherit another's."""
    archive.reset_archive_column_support()
    yield
    archive.reset_archive_column_support()


# ---------------------------------------------------------------------------
# The HTTP surface
# ---------------------------------------------------------------------------


def _app(user_id=USER_ID):
    from fastapi import FastAPI

    from backend_app.core.dependencies import get_current_user

    app = FastAPI()
    app.include_router(router_module.router, prefix="/api/strategies")
    app.state.limiter = router_module.limiter
    app.dependency_overrides[get_current_user] = lambda: _user(user_id)
    return app


def _client(user_id=USER_ID):
    from fastapi.testclient import TestClient

    return TestClient(_app(user_id), raise_server_exceptions=False)


def _rename(sb, body, strategy_id=STRATEGY_ID, user_id=USER_ID):
    with patch("backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb)):
        return _client(user_id).put(f"/api/strategies/{strategy_id}/rename", json=body)


def _name_of(sb, strategy_id=STRATEGY_ID):
    return sb.row("strategies", strategy_id)["name"]


# ---------------------------------------------------------------------------
# 1. The route exists, is reachable, and is not the edit handler in disguise
# ---------------------------------------------------------------------------


class TestTheRouteIsReachableAndNotShadowed:
    def test_the_app_resolves_the_path_to_the_rename_handler(self):
        """The real routing hazard, asserted against the real router's own resolution.

        ``PUT /{strategy_id}`` is declared in the same router. If it matched this path
        first, every rename would be served by the edit handler.
        """
        from starlette.routing import Match

        scope = {
            "type": "http",
            "method": "PUT",
            "path": f"/api/strategies/{STRATEGY_ID}/rename",
            "path_params": {},
            "headers": [],
            "root_path": "",
        }
        matched = [
            route
            for route in _app().routes
            if route.matches(scope)[0] == Match.FULL
        ]

        assert matched, "PUT /api/strategies/{id}/rename resolves to no route at all"
        assert matched[0].endpoint is router_module.rename_strategy

    def test_a_rename_answers_200_with_the_updated_record(self):
        sb = _Supabase(_rows())

        response = _rename(sb, {"name": "Renamed"})

        assert response.status_code == 200, response.text
        assert response.json()["name"] == "Renamed"
        assert response.json()["id"] == STRATEGY_ID

    def test_an_over_long_name_is_refused_here_and_would_be_accepted_by_the_edit_route(self):
        """A behavioural discriminator: only ``rename_strategy`` enforces the bounds."""
        sb = _Supabase(_rows())
        too_long = "x" * (MAX_LENGTH + 1)

        refused = _rename(sb, {"name": too_long})
        assert refused.status_code == 422
        assert _name_of(sb) == ORIGINAL_NAME

        # The same body through ``PUT /{strategy_id}`` is an ordinary edit, so had that
        # handler been the one serving ``/rename`` the assertion above could not hold.
        with patch("backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb)):
            edited = _client().put(f"/api/strategies/{STRATEGY_ID}", json={"name": too_long})
        assert edited.status_code == 200
        assert _name_of(sb) == too_long

    def test_a_body_without_a_name_is_the_renames_422_not_the_edit_paths_403(self):
        sb = _Supabase(_rows())

        refused = _rename(sb, {"status": "running"})

        assert refused.status_code == 422
        assert refused.json()["detail"]["reason"] == router_module.RENAME_REASON_MISSING

    def test_the_edit_route_still_works(self):
        """The new, more specific route did not shadow the general one either."""
        sb = _Supabase(_rows())

        with patch("backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb)):
            response = _client().put(
                f"/api/strategies/{STRATEGY_ID}", json={"description": "edited"}
            )

        assert response.status_code == 200
        assert sb.row("strategies", STRATEGY_ID)["description"] == "edited"

    @pytest.mark.parametrize("method", ["get", "post", "delete", "patch"])
    def test_only_put_serves_the_rename_path(self, method):
        sb = _Supabase(_rows())

        with patch("backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb)):
            call = getattr(_client(), method)
            response = call(f"/api/strategies/{STRATEGY_ID}/rename")

        assert response.status_code in (404, 405)
        assert _name_of(sb) == ORIGINAL_NAME


# ---------------------------------------------------------------------------
# 2. Requirement 2.6: 1-100 characters, measured after trimming
# ---------------------------------------------------------------------------


class TestNameValidation:
    @pytest.mark.parametrize(
        "length", [MIN_LENGTH, 2, MAX_LENGTH - 1, MAX_LENGTH], ids=lambda n: f"{n}-chars"
    )
    def test_a_name_within_the_bounds_is_applied(self, length):
        sb = _Supabase(_rows())
        name = "n" * length

        response = _rename(sb, {"name": name})

        assert response.status_code == 200, response.text
        assert response.json()["name"] == name
        assert _name_of(sb) == name

    @pytest.mark.parametrize(
        "name, reason",
        [
            ("", router_module.RENAME_REASON_EMPTY_AFTER_TRIM),
            ("   ", router_module.RENAME_REASON_EMPTY_AFTER_TRIM),
            ("\t\n ", router_module.RENAME_REASON_EMPTY_AFTER_TRIM),
            ("x" * (MAX_LENGTH + 1), router_module.RENAME_REASON_TOO_LONG),
            ("y" * (MAX_LENGTH + 400), router_module.RENAME_REASON_TOO_LONG),
        ],
        ids=["empty", "spaces-only", "mixed-whitespace", "101-chars", "500-chars"],
    )
    def test_a_name_outside_the_bounds_is_a_422_naming_the_reason(self, name, reason):
        sb = _Supabase(_rows())

        response = _rename(sb, {"name": name})

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["error"] == router_module.STRATEGY_NAME_INVALID
        assert detail["reason"] == reason
        assert detail["min_length"] == MIN_LENGTH
        assert detail["max_length"] == MAX_LENGTH

    def test_a_refused_name_retains_the_previous_one_and_writes_nothing(self):
        """Requirement 2.6: "SHALL retain the previous display name"."""
        sb = _Supabase(_rows())

        _rename(sb, {"name": "  "})

        assert _name_of(sb) == ORIGINAL_NAME
        assert sb.writes == []

    def test_a_refused_name_does_not_even_read_the_strategy(self):
        """Validation runs before the database client exists, so a malformed rename is
        refused on its own terms and cannot disclose whether the strategy exists."""
        sb = _Supabase(_rows())

        _rename(sb, {"name": "x" * 500})

        assert sb.selects == []

    def test_the_stored_name_is_the_trimmed_one(self):
        sb = _Supabase(_rows())

        response = _rename(sb, {"name": "   Padded Name \n"})

        assert response.status_code == 200
        assert _name_of(sb) == "Padded Name"
        assert response.json()["name"] == "Padded Name"

    def test_a_name_that_only_fits_after_trimming_is_accepted(self):
        sb = _Supabase(_rows())
        name = "z" * MAX_LENGTH

        response = _rename(sb, {"name": f"   {name}   "})

        assert response.status_code == 200
        assert _name_of(sb) == name

    def test_a_name_that_is_too_long_only_after_trimming_is_refused(self):
        sb = _Supabase(_rows())

        response = _rename(sb, {"name": "  " + "z" * (MAX_LENGTH + 1) + "  "})

        assert response.status_code == 422
        assert response.json()["detail"]["submitted_length"] == MAX_LENGTH + 1

    @pytest.mark.parametrize("value", [None, 42, 3.5, True, ["a"], {"a": 1}])
    def test_a_non_string_name_is_refused(self, value):
        sb = _Supabase(_rows())

        response = _rename(sb, {"name": value})

        assert response.status_code == 422
        assert (
            response.json()["detail"]["reason"] == router_module.RENAME_REASON_NOT_A_STRING
        )
        assert sb.writes == []

    def test_the_bounds_are_requirement_2_6s(self):
        assert (MIN_LENGTH, MAX_LENGTH) == (1, 100)


# ---------------------------------------------------------------------------
# 3. Requirement 2.6: only ``strategies.name`` is touched
# ---------------------------------------------------------------------------


class TestItTouchesOnlyTheName:
    def test_the_write_is_one_ownership_scoped_update_carrying_only_name(self):
        sb = _Supabase(_rows())

        _rename(sb, {"name": "Renamed"})

        assert sb.writes == [
            (
                "update",
                "strategies",
                {"name": "Renamed"},
                {"id": STRATEGY_ID, "user_id": USER_ID},
            )
        ]

    def test_no_other_column_on_the_strategy_changes(self):
        sb = _Supabase(_rows())
        before = dict(sb.row("strategies", STRATEGY_ID))

        _rename(sb, {"name": "Renamed"})

        after = dict(sb.row("strategies", STRATEGY_ID))
        assert after.pop("name") == "Renamed"
        before.pop("name")
        assert after == before

    @pytest.mark.parametrize("table", UNTOUCHED_TABLES)
    def test_every_version_backtest_deployment_and_signal_record_is_unchanged(self, table):
        sb = _Supabase(_rows())
        before = [dict(row) for row in sb.rows[table]]

        _rename(sb, {"name": "Renamed"})

        assert sb.rows[table] == before
        assert sb.writes_of("update", table) == []
        assert sb.writes_of("delete") == []

    def test_another_tenants_strategy_is_not_renamed(self):
        sb = _Supabase(_rows())

        _rename(sb, {"name": "Renamed"})

        assert sb.row("strategies", OTHER_STRATEGY_ID)["name"] == "THEIRS"


# ---------------------------------------------------------------------------
# 4. Requirement 3.3: an archived strategy is refused
# ---------------------------------------------------------------------------


class TestArchivedStrategyIsRefused:
    def _archived(self):
        return _Supabase(_rows(strategy=_strategy_row(**{ARCHIVED_AT: ARCHIVED_STAMP})))

    def test_it_answers_409_strategy_archived(self):
        sb = self._archived()

        response = _rename(sb, {"name": "Renamed"})

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["error"] == "STRATEGY_ARCHIVED"
        assert detail[ARCHIVED_AT] == ARCHIVED_STAMP

    def test_the_refusal_names_the_rename_operation(self):
        """The refusal comes from task 5.1's gate, through the constant it published for
        this handler, so archival cannot mean one thing here and another to an edit."""
        sb = self._archived()

        detail = _rename(sb, {"name": "Renamed"}).json()["detail"]

        assert detail["operation"] == archive.OPERATION_RENAME
        assert archive.OPERATION_RENAME in detail["refused_operations"]

    def test_nothing_is_written_and_the_name_is_unchanged(self):
        sb = self._archived()

        _rename(sb, {"name": "Renamed"})

        assert sb.writes == []
        assert _name_of(sb) == ORIGINAL_NAME

    def test_an_active_strategy_is_not_refused(self):
        sb = _Supabase(_rows())

        assert _rename(sb, {"name": "Renamed"}).status_code == 200

    @pytest.mark.asyncio
    async def test_archiving_then_renaming_is_refused_end_to_end(self):
        """The two halves of Requirement 3.3 against one store, through the real gate."""
        sb = _Supabase(_rows())

        await archive.archive_strategy(sb, _user(), STRATEGY_ID)
        response = _rename(sb, {"name": "Renamed"})

        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "STRATEGY_ARCHIVED"
        assert _name_of(sb) == ORIGINAL_NAME


# ---------------------------------------------------------------------------
# 5. Requirement 20.2: a non-owner gets the answer a missing strategy gets
# ---------------------------------------------------------------------------


class TestOwnership:
    def test_a_non_owner_gets_404_and_changes_nothing(self):
        """The refusal is the *response*, and no row moves.

        A write statement is issued — the ownership-scoped ``UPDATE`` is what distinguishes
        "not this caller's" from "that read failed", so both get their own answer — but it
        carries the caller's own ``user_id`` and therefore matches no row at all.
        """
        sb = _Supabase(_rows())

        response = _rename(sb, {"name": "Renamed"}, user_id=OTHER_USER_ID)

        assert response.status_code == 404
        assert _name_of(sb) == ORIGINAL_NAME
        assert _name_of(sb, OTHER_STRATEGY_ID) == "THEIRS"
        assert all(
            filters.get("user_id") == OTHER_USER_ID
            for _, _, _, filters in sb.writes_of("update", "strategies")
        )

    def test_a_missing_strategy_gets_the_same_404(self):
        sb = _Supabase(_rows())
        missing = "99999999-9999-4999-8999-999999999999"

        absent = _rename(sb, {"name": "Renamed"}, strategy_id=missing)
        foreign = _rename(sb, {"name": "Renamed"}, user_id=OTHER_USER_ID)

        assert absent.status_code == foreign.status_code == 404
        assert absent.json() == foreign.json()

    def test_the_read_is_ownership_scoped(self):
        sb = _Supabase(_rows())

        _rename(sb, {"name": "Renamed"})

        strategy_reads = [f for table, f in sb.selects if table == "strategies"]
        assert strategy_reads
        assert all(f.get("user_id") == USER_ID for f in strategy_reads)


# ---------------------------------------------------------------------------
# 6. Degradations: 005a unapplied, no representation, an unwritable table
# ---------------------------------------------------------------------------


class TestDegradations:
    def test_it_still_renames_with_migration_005a_unapplied(self):
        """005a is applied by hand, so ``archived_at`` may not exist. A rename must not
        depend on it: the read is ``select("*")`` and the write carries only ``name``."""
        rows = _rows()
        for row in rows["strategies"]:
            row.pop(ARCHIVED_AT, None)
        sb = _Supabase(rows, absent_columns={ARCHIVED_AT})

        response = _rename(sb, {"name": "Renamed"})

        assert response.status_code == 200, response.text
        assert _name_of(sb) == "Renamed"

    def test_an_update_that_returns_no_representation_still_reports_the_new_name(self):
        sb = _SupabaseNoRepresentation(_rows())

        response = _rename(sb, {"name": "Renamed"})

        assert response.status_code == 200
        assert response.json()["name"] == "Renamed"
        assert _name_of(sb) == "Renamed"

    def test_an_unwritable_table_is_503_and_not_a_fabricated_success(self):
        sb = _Supabase(_rows(), failing_tables=("strategies",))

        response = _rename(sb, {"name": "Renamed"})

        assert response.status_code == 503
        assert "rename" in str(response.json()["detail"]).lower()

    def test_no_supabase_client_echoes_rather_than_claiming_persistence(self):
        with patch("backend_app.routers.strategies._sb", new=AsyncMock(return_value=None)):
            response = _client().put(
                f"/api/strategies/{STRATEGY_ID}/rename", json={"name": "  Renamed  "}
            )

        assert response.status_code == 200
        assert response.json() == {
            "id": STRATEGY_ID,
            "user_id": USER_ID,
            "name": "Renamed",
        }
