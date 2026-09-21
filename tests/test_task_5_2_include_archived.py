"""
tests/test_task_5_2_include_archived.py

Trading-lifecycle-integration task 5.2 — ``GET /api/strategies?include_archived=``.
Requirements 3.3, 3.4, 3.6.

WHAT THIS FILE HOLDS TO
-----------------------
* **The default list is the active list, and the flag is the only thing that changes
  that.** Requirement 3.3 keeps an archived strategy out of the default list;
  Requirement 3.4 keeps it readable to its own owner for history and audit. Both are
  asserted against the same store, with the same user, so the difference is provably the
  flag and not the query's ownership scope.
* **Each archived entry is MARKED, not merely included.** ``is_archived`` and
  ``archived_at`` are asserted per entry, in both lists, so a caller can render an
  archived strategy as history rather than as an actionable row.
* **The listing does not name ``archived_at`` in its projection.** ``005a`` is applied by
  hand, so the column may not exist. The fake client reproduces PostgREST's real
  behaviour — naming a missing column in a ``select`` raises ``42703`` — which means "the
  listing still works with 005a unapplied" is a fact about the request the handler
  issued, not a fact about a mock that forgives everything. The same case asserts the
  degradation 005a's header prescribes: a WARNING naming the file, the full list, and no
  strategy reported as archived.
* **The response shape did not widen.** The read became ``select("*")``; the published
  entry is asserted to still carry exactly the columns this endpoint always published
  (plus the lifted DAG fields and the two archival keys), so no unrelated column starts
  travelling on every list page.

WHAT THIS FILE CANNOT PROVE
---------------------------
There is no PostgreSQL here, so nothing here proves RLS scopes the listing at the
database as well as the ``user_id`` filter does, nor that the partial index on
``archived_at IS NULL`` serves the default query. Those are 005a's own verification
queries and task 2.2's migration test.
"""

import logging
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import strategy_archive as archive

REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTER_PATH = REPO_ROOT / "backend_app" / "routers" / "strategies.py"

USER_ID = "user_task_5_2"
OTHER_USER_ID = "user_task_5_2_other"
ACTIVE_ID = "11111111-1111-4111-8111-111111111111"
ARCHIVED_ID = "22222222-2222-4222-8222-222222222222"
OTHER_TENANT_ID = "33333333-3333-4333-8333-333333333333"

ARCHIVED_AT = archive.ARCHIVED_AT_COLUMN
ARCHIVED_STAMP = "2024-05-05T05:05:05+00:00"

#: Exactly what this endpoint published before task 5.2 touched it.
PUBLISHED_COLUMNS = {
    "id",
    "name",
    "description",
    "symbol",
    "timeframe",
    "status",
    "is_active",
    "deployed_exchange",
    "created_at",
    "updated_at",
    "buy_logic",
    "tags",
    "version",
}

#: What :func:`_lift_dag_fields` adds to every entry it is handed.
LIFTED_DAG_KEYS = {
    "nodes",
    "edges",
    "dag_version",
    "dag_schema_version",
    "dag_created_at",
    "dag_updated_at",
    "dag_hash",
    "execution_order",
    "compiled_plan",
    "compiler_version",
    "warmup_bars",
    "dag_validation_state",
    "dag_validation_report",
}

ARCHIVAL_KEYS = {"is_archived", ARCHIVED_AT}

#: The two activity timestamps vyomquant-ui-redesign BC-3 and BC-4 add to this projection
#: (that spec's tasks 12.3 and 12.4, design.md §7.2). Named here so the exact-equality guard
#: in :class:`TestResponseShape` stays exact: task 5.2's claim is that ``select("*")`` did not
#: widen the response *as a side effect*, and these two are a deliberate, spec-registered
#: addition rather than a leaked column. ``tests/test_strategies_list_projection.py`` is what
#: holds their own semantics. Neither carries any value task 5.2 asserts.
ACTIVITY_KEYS = {"last_signal_at", "last_execution_at"}


# ---------------------------------------------------------------------------
# Doubles (the pattern task 5.1's suite established)
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, data):
        self.data = data
        self.error = None


class _Query:
    """A chainable PostgREST stand-in that honours ``.eq`` and refuses absent columns.

    ``absent_columns`` reproduces an unapplied migration the way PostgREST really
    reports one: naming the column in a ``select`` raises PostgreSQL's ``42703``, while
    ``select("*")`` succeeds and simply does not carry the key. That asymmetry is the
    whole point of this double for task 5.2 — it is what makes "the listing survives a
    database without 005a" an assertion about the request rather than about the mock.
    """

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


class _Supabase:
    def __init__(self, rows=None, absent_columns=(), failing_tables=()):
        self.rows = {k: [dict(r) for r in v] for k, v in (rows or {}).items()}
        self.absent_columns = set(absent_columns)
        self.failing_tables = set(failing_tables)
        self.writes = []
        self.selects = []

    def table(self, name):
        return _Query(self, name)

    def row(self, table, row_id):
        for row in self.rows.get(table, []):
            if str(row.get("id")) == str(row_id):
                return row
        return None


def _user(user_id=USER_ID):
    return {"id": user_id, "email": f"{user_id}@example.com", "access_token": "tok"}


def _strategy_row(strategy_id, user_id=USER_ID, archived_at=None, **overrides):
    row = {
        "id": strategy_id,
        "user_id": user_id,
        "name": f"Strategy {strategy_id[:4]}",
        "description": "",
        "symbol": "BTC/USDT",
        "timeframe": "5m",
        "status": "stopped",
        "is_active": False,
        "deployed_exchange": None,
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-02T00:00:00+00:00",
        "buy_logic": {"_nodes": [{"id": "n1"}], "_edges": []},
        "tags": [],
        "version": 1,
        # Columns the endpoint has never published, present on the row so "the read is
        # select(*)" cannot silently widen the response.
        "sell_logic": {"secret": "not published"},
        "risk": {"max_dd": 0.1},
        "indicators": ["rsi"],
        "ml_model_path": "/models/whatever.pkl",
        ARCHIVED_AT: archived_at,
    }
    row.update(overrides)
    return row


def _rows(*, archived_at=ARCHIVED_STAMP, drop_archival_column=False):
    """One active strategy, one archived strategy, one belonging to another tenant."""
    rows = [
        _strategy_row(ACTIVE_ID),
        _strategy_row(ARCHIVED_ID, archived_at=archived_at),
        _strategy_row(OTHER_TENANT_ID, user_id=OTHER_USER_ID, name="THEIRS"),
    ]
    if drop_archival_column:
        for row in rows:
            row.pop(ARCHIVED_AT, None)
    return {"strategies": rows}


# ---------------------------------------------------------------------------
# The HTTP surface
# ---------------------------------------------------------------------------


def _client(user_id=USER_ID):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend_app.core.dependencies import get_current_user
    from backend_app.routers import strategies as router_module

    app = FastAPI()
    app.include_router(router_module.router, prefix="/api/strategies")
    app.state.limiter = router_module.limiter
    app.dependency_overrides[get_current_user] = lambda: _user(user_id)
    return TestClient(app, raise_server_exceptions=False)


def _list(sb, params=None, user_id=USER_ID):
    with patch("backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb)):
        response = _client(user_id).get("/api/strategies", params=params or {})
    assert response.status_code == 200, response.text
    return response.json()


def _ids(body):
    return [entry["id"] for entry in body["strategies"]]


def _entry(body, strategy_id):
    for entry in body["strategies"]:
        if entry["id"] == strategy_id:
            return entry
    raise AssertionError(f"{strategy_id} is not in {_ids(body)}")


@pytest.fixture(autouse=True)
def fresh_column_probe():
    """The 005a verdict is cached per process; no test may inherit another's."""
    archive.reset_archive_column_support()
    yield
    archive.reset_archive_column_support()


@pytest.fixture(autouse=True)
def audit(monkeypatch):
    """The real :class:`StrategyAuditLogger`, recording into a list instead of Redis."""
    import backend_app.core.audit_trail as audit_module
    from backend_app.core.audit_trail import StrategyAuditLogger

    recorded = []
    logger_double = StrategyAuditLogger()

    async def _append(record):
        recorded.append(record)
        return True

    logger_double._append_history = _append  # noqa: SLF001
    logger_double.recorded = recorded
    monkeypatch.setattr(audit_module, "_strategy_audit_logger", logger_double)
    return logger_double


# ---------------------------------------------------------------------------
# 1. Requirement 3.3: the default list excludes archived strategies
# ---------------------------------------------------------------------------


class TestDefaultListExcludesArchived:
    def test_an_archived_strategy_is_absent_from_the_default_list(self):
        body = _list(_Supabase(_rows()))

        assert _ids(body) == [ACTIVE_ID]
        assert body["total"] == 1
        assert body["include_archived"] is False

    def test_it_reports_how_many_are_archived_so_the_caller_can_offer_the_flag(self):
        body = _list(_Supabase(_rows()))

        assert body["archived_total"] == 1

    def test_every_entry_in_the_default_list_is_marked_active(self):
        """``is_archived`` is always present, so "active" is never inferred from absence."""
        body = _list(_Supabase(_rows()))

        entry = _entry(body, ACTIVE_ID)
        assert entry["is_archived"] is False
        assert entry[ARCHIVED_AT] is None

    def test_a_list_with_nothing_archived_is_unchanged_by_this_task(self):
        body = _list(_Supabase(_rows(archived_at=None)))

        assert set(_ids(body)) == {ACTIVE_ID, ARCHIVED_ID}
        assert body["archived_total"] == 0

    @pytest.mark.parametrize("value", ["false", "False", "0", "no"])
    def test_an_explicit_false_is_the_default_list(self, value):
        body = _list(_Supabase(_rows()), {"include_archived": value})

        assert _ids(body) == [ACTIVE_ID]
        assert body["include_archived"] is False


# ---------------------------------------------------------------------------
# 2. Requirement 3.4: the flag includes them, and marks each one
# ---------------------------------------------------------------------------


class TestIncludeArchivedIsTheHistoryView:
    @pytest.mark.parametrize("value", ["true", "True", "1", "yes"])
    def test_the_flag_includes_the_archived_strategy(self, value):
        body = _list(_Supabase(_rows()), {"include_archived": value})

        assert set(_ids(body)) == {ACTIVE_ID, ARCHIVED_ID}
        assert body["total"] == 2
        assert body["include_archived"] is True

    def test_each_archived_entry_is_marked_with_its_timestamp(self):
        body = _list(_Supabase(_rows()), {"include_archived": "true"})

        archived = _entry(body, ARCHIVED_ID)
        assert archived["is_archived"] is True
        assert archived[ARCHIVED_AT] == ARCHIVED_STAMP

    def test_the_active_entries_alongside_it_are_not_marked(self):
        body = _list(_Supabase(_rows()), {"include_archived": "true"})

        active = _entry(body, ACTIVE_ID)
        assert active["is_archived"] is False
        assert active[ARCHIVED_AT] is None

    def test_the_flag_widens_the_rows_reported_not_the_ownership_scope(self):
        """Requirement 3.4 is a history view of the *owner's own* strategies."""
        sb = _Supabase(_rows())

        body = _list(sb, {"include_archived": "true"})

        assert OTHER_TENANT_ID not in _ids(body)
        assert sb.selects, "the listing read the strategies table"
        assert all(
            filters.get("user_id") == USER_ID
            for table, filters in sb.selects
            if table == "strategies"
        )

    def test_another_tenant_sees_only_their_own_row_under_the_same_flag(self):
        body = _list(_Supabase(_rows()), {"include_archived": "true"}, OTHER_USER_ID)

        assert _ids(body) == [OTHER_TENANT_ID]

    def test_the_listing_writes_nothing(self):
        sb = _Supabase(_rows())

        _list(sb, {"include_archived": "true"})
        _list(sb)

        assert sb.writes == []
        assert sb.row("strategies", ARCHIVED_ID)[ARCHIVED_AT] == ARCHIVED_STAMP


# ---------------------------------------------------------------------------
# 3. Migration 005a unapplied: the full list, a warning naming the file, no 500
# ---------------------------------------------------------------------------


class TestUnappliedMigration:
    """005a's header: "Task 5.2's ``include_archived`` filter must likewise treat
    'column absent' as 'no strategy is archived' (its default list is then simply the
    full list) rather than failing the listing outright."
    """

    def _absent(self):
        return _Supabase(
            _rows(drop_archival_column=True), absent_columns={ARCHIVED_AT}
        )

    def test_the_default_list_is_simply_the_full_list(self):
        body = _list(self._absent())

        assert set(_ids(body)) == {ACTIVE_ID, ARCHIVED_ID}
        assert body["total"] == 2
        assert body["archived_total"] == 0

    def test_no_strategy_is_reported_as_archived(self):
        body = _list(self._absent(), {"include_archived": "true"})

        assert set(_ids(body)) == {ACTIVE_ID, ARCHIVED_ID}
        assert all(entry["is_archived"] is False for entry in body["strategies"])
        assert all(entry[ARCHIVED_AT] is None for entry in body["strategies"])

    def test_it_warns_naming_the_migration_rather_than_failing(self, caplog):
        with caplog.at_level(logging.WARNING):
            body = _list(self._absent(), {"include_archived": "true"})

        assert body["total"] == 2, "the listing did not degrade into an empty list"
        assert archive.STRATEGY_ARCHIVE_MIGRATION in caplog.text
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    def test_the_projection_does_not_name_the_column_that_may_not_exist(self):
        """The fake raises 42703 on a named absent column, exactly as PostgREST does.

        So a handler that listed ``archived_at`` in its projection would land in the
        ``except`` and answer an empty list here. Two strategies come back instead.
        """
        body = _list(self._absent())

        assert body["strategies"], "select() must not name archived_at"

    def test_an_unreadable_table_still_degrades_the_way_it_always_did(self):
        body = _list(_Supabase(_rows(), failing_tables=("strategies",)))

        assert body == {
            "strategies": [],
            "total": 0,
            "include_archived": False,
            "archived_total": 0,
        }


# ---------------------------------------------------------------------------
# 4. The response shape did not widen
# ---------------------------------------------------------------------------


class TestResponseShape:
    def test_an_entry_carries_the_published_columns_plus_the_archival_keys(self):
        body = _list(_Supabase(_rows()), {"include_archived": "true"})

        keys = set(_entry(body, ACTIVE_ID))
        assert keys == PUBLISHED_COLUMNS | LIFTED_DAG_KEYS | ARCHIVAL_KEYS | ACTIVITY_KEYS

    @pytest.mark.parametrize(
        "column", ["sell_logic", "risk", "indicators", "ml_model_path", "user_id"]
    )
    def test_select_star_did_not_start_publishing_unrelated_columns(self, column):
        body = _list(_Supabase(_rows()), {"include_archived": "true"})

        assert column not in _entry(body, ACTIVE_ID)

    def test_the_dag_fields_are_still_lifted_out_of_buy_logic(self):
        body = _list(_Supabase(_rows()))

        entry = _entry(body, ACTIVE_ID)
        assert entry["nodes"] == [{"id": "n1"}]
        assert entry["edges"] == []
        assert "_nodes" not in entry["buy_logic"]

    def test_no_supabase_client_answers_an_empty_list_not_an_error(self):
        with patch(
            "backend_app.routers.strategies._sb", new=AsyncMock(return_value=None)
        ):
            response = _client().get("/api/strategies", params={"include_archived": "true"})

        assert response.status_code == 200
        assert response.json() == {
            "strategies": [],
            "total": 0,
            "include_archived": True,
            "archived_total": 0,
        }

    def test_the_handler_source_reads_star_and_filters_in_python(self):
        """Read off the source, so a re-introduced server-side archival filter is caught."""
        source = ROUTER_PATH.read_text(encoding="utf-8", errors="replace")
        start = source.index("async def list_strategies(")
        handler = source[start : source.index("# ── POST /api/strategies ")]
        # The docstring names both anti-patterns in prose to explain why they are wrong,
        # so the scan is of the CODE below it.
        opened = handler.index('"""')
        code = handler[handler.index('"""', opened + 3) + 3 :]

        assert '.select("*")' in code
        assert f'select("{ARCHIVED_AT}' not in code
        assert f'.is_("{ARCHIVED_AT}"' not in code
        assert "is_archived(row)" in code


# ---------------------------------------------------------------------------
# 5. End to end with the real archive gate (task 5.1)
# ---------------------------------------------------------------------------


class TestArchivingThenListing:
    """The two halves of Requirement 3 against one store: archive, then list."""

    def _store(self):
        rows = _rows(archived_at=None)
        rows["strategy_deployments"] = []
        return _Supabase(rows)

    @pytest.mark.asyncio
    async def test_an_archived_strategy_leaves_the_default_list_and_returns_under_the_flag(
        self,
    ):
        sb = self._store()

        result = await archive.archive_strategy(sb, _user(), ARCHIVED_ID)
        stamp = result[ARCHIVED_AT]

        default = _list(sb)
        assert _ids(default) == [ACTIVE_ID]
        assert default["archived_total"] == 1

        history = _list(sb, {"include_archived": "true"})
        entry = _entry(history, ARCHIVED_ID)
        assert entry["is_archived"] is True
        assert entry[ARCHIVED_AT] == stamp

    @pytest.mark.asyncio
    async def test_re_archiving_does_not_change_what_the_listing_reports(self):
        """Requirement 3.6: idempotent — including the timestamp the history view shows."""
        sb = self._store()
        first = await archive.archive_strategy(sb, _user(), ARCHIVED_ID)
        await archive.archive_strategy(sb, _user(), ARCHIVED_ID)

        history = _list(sb, {"include_archived": "true"})

        assert _entry(history, ARCHIVED_ID)[ARCHIVED_AT] == first[ARCHIVED_AT]
        assert history["archived_total"] == 1
