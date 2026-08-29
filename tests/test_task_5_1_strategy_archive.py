"""
tests/test_task_5_1_strategy_archive.py

Trading-lifecycle-integration task 5.1 - ``archive_strategy`` and the rewired
``DELETE /api/strategies/{id}``.
Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 20.2.

WHAT THIS FILE HOLDS TO
-----------------------
* **The delete is an UPDATE, and that is asserted about the WRITE.** The fake client
  records every write it is handed and applies updates to its rows, so "the strategy was
  archived and nothing was deleted" is a fact about what the handler issued - an
  ``update`` on ``strategies`` carrying ``archived_at``, and *no* ``delete`` against any
  table - rather than a fact about the response body. Requirement 3.5's "no dependent row
  is physically deleted" is checked by reading the dependents back afterwards.
* **The 409 names every blocking deployment.** The gate is exercised across the whole
  binding vocabulary: each of ``DEPLOYING``, ``RUNNING``, ``PAUSED`` blocks, each of
  ``STOPPED``, ``FAILED`` does not, and the refusal body carries one entry per blocking
  deployment with its id and its canonical state.
* **The unapplied-migration contract is tested as a REFUSAL, not as a 500 and not as a
  success.** ``005a_strategy_archive.sql``'s header states that a path which writes
  ``archived_at`` must degrade with a warning naming that file and must never report a
  strategy as archived when the write did not happen. Both halves are asserted: the
  warning text contains the file name, and the response is a 503 that carries neither
  ``"archived"`` nor any write at all - and, specifically, no fallback row deletion.
* **The doubles are the real thing wherever one exists.** The archive gate, the binding
  vocabulary, the ``STOPPABLE_BINDING_STATES`` set, the audit logger and the FastAPI route
  are all real. The only doubles are the PostgREST client, which this environment has no
  instance of, and the quota counter, which needs Redis.

WHAT THIS FILE CANNOT PROVE
---------------------------
There is no PostgreSQL here and 005a is unapplied, so nothing here proves the column is
nullable, that the partial index exists, or that RLS refuses a cross-tenant archival
write. Those are 005a's own verification queries and task 2.2's migration test. The
cross-tenant HTTP answer is asserted here only as "a non-owner gets the same 404 a
missing strategy gets" - the shape Requirement 20.2 asks for.
"""

import logging
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import strategy_archive as archive
from backend_app.backend.strategy_lifecycle import (
    BINDING_STATES,
    STOPPABLE_BINDING_STATES,
    is_active_binding_status,
)
from backend_app.core.audit_trail import (
    StrategyAuditAction,
    StrategyAuditLogger,
    get_strategy_audit_logger,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = REPO_ROOT / "backend_app" / "migrations" / "005a_strategy_archive.sql"
ROUTER_PATH = REPO_ROOT / "backend_app" / "routers" / "strategies.py"

USER_ID = "user_task_5_1"
OTHER_USER_ID = "user_task_5_1_other"
STRATEGY_ID = "11111111-1111-4111-8111-111111111111"
OTHER_STRATEGY_ID = "22222222-2222-4222-8222-222222222222"
DEPLOYMENT_ID = "33333333-3333-4333-8333-333333333333"
SECOND_DEPLOYMENT_ID = "44444444-4444-4444-8444-444444444444"

ARCHIVED_AT = archive.ARCHIVED_AT_COLUMN


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, data):
        self.data = data
        self.error = None


class _Query:
    """A chainable PostgREST stand-in that honours ``.eq``, APPLIES updates and REMOVES rows.

    Applying the write is what makes this file's central assertions possible: "the row
    carries a timestamp afterwards" and "no dependent row disappeared" are facts about the
    store, not about the call.

    ``absent_columns`` reproduces an unapplied migration the way PostgREST really reports
    one: naming the column in a ``select`` raises PostgreSQL's ``42703``, sending it in a
    write raises PostgREST's ``PGRST204``, and ``select("*")`` succeeds while simply not
    carrying the key - which is precisely why the production reads use ``*``.
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
                f'column {self._table}.{missing[0]} does not exist (42703 undefined_column)'
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

    def table(self, name):
        return _Query(self, name)

    def row(self, table, row_id):
        for row in self.rows.get(table, []):
            if str(row.get("id")) == str(row_id):
                return row
        return None

    def writes_of(self, mode, table=None):
        return [
            w for w in self.writes if w[0] == mode and (table is None or w[1] == table)
        ]


def _user(user_id=USER_ID):
    return {"id": user_id, "email": f"{user_id}@example.com", "access_token": "tok"}


def _strategy_row(strategy_id=STRATEGY_ID, user_id=USER_ID, **overrides):
    row = {
        "id": strategy_id,
        "user_id": user_id,
        "name": "Task 5.1 Strategy",
        "symbol": "BTC/USDT",
        "status": "stopped",
        ARCHIVED_AT: None,
    }
    row.update(overrides)
    return row


def _deployment_row(status="stopped", deployment_id=DEPLOYMENT_ID, **overrides):
    row = {
        "id": deployment_id,
        "user_id": USER_ID,
        "strategy_id": STRATEGY_ID,
        "version_id": "99999999-9999-4999-8999-999999999999",
        "version": "v1.0",
        "status": status,
        "mode": "paper",
    }
    row.update(overrides)
    return row


#: A full store: the owner's strategy, another tenant's strategy, and one dependent row in
#: each table archiving must not touch (Requirement 3.5).
def _rows(strategy=None, deployments=None):
    return {
        "strategies": [
            strategy or _strategy_row(),
            _strategy_row(OTHER_STRATEGY_ID, OTHER_USER_ID, name="THEIRS"),
        ],
        "strategy_deployments": list(deployments or [_deployment_row()]),
        "strategy_versions": [{"id": "v-1", "strategy_id": STRATEGY_ID}],
        "strategy_backtests": [{"id": "b-1", "strategy_id": STRATEGY_ID}],
        "signals": [{"id": "s-1", "strategy_id": STRATEGY_ID}],
        "signal_events": [{"id": "e-1", "signal_id": "s-1"}],
    }


DEPENDENT_TABLES = (
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


@pytest.fixture(autouse=True)
def audit(monkeypatch):
    """The real :class:`StrategyAuditLogger`, recording into a list instead of Redis."""
    import backend_app.core.audit_trail as audit_module

    recorded = []
    logger = StrategyAuditLogger()

    async def _append(record):
        recorded.append(record)
        return True

    logger._append_history = _append  # noqa: SLF001
    logger.recorded = recorded
    monkeypatch.setattr(audit_module, "_strategy_audit_logger", logger)
    return logger


# ---------------------------------------------------------------------------
# 1. The happy path: an UPDATE, a timestamp, and nothing deleted
# ---------------------------------------------------------------------------


class TestArchivingIsASoftDelete:
    @pytest.mark.asyncio
    async def test_it_sets_archived_at_and_returns_the_timestamp(self):
        sb = _Supabase(_rows())

        result = await archive.archive_strategy(sb, _user(), STRATEGY_ID)

        assert result["status"] == archive.STATUS_ARCHIVED
        assert result["strategy_id"] == STRATEGY_ID
        stamp = result[ARCHIVED_AT]
        assert stamp, "Requirement 3.2/task 5.1: the archived timestamp is returned"
        # The value is on the row, not merely in the reply.
        assert sb.row("strategies", STRATEGY_ID)[ARCHIVED_AT] == stamp

    @pytest.mark.asyncio
    async def test_the_write_is_an_update_carrying_archived_at_and_both_filters(self):
        sb = _Supabase(_rows())

        await archive.archive_strategy(sb, _user(), STRATEGY_ID)

        updates = sb.writes_of("update", "strategies")
        assert len(updates) == 1
        _mode, _table, payload, filters = updates[0]
        assert set(payload) == {ARCHIVED_AT}, "only the archival column is touched"
        assert filters == {"id": STRATEGY_ID, "user_id": USER_ID}

    @pytest.mark.asyncio
    async def test_no_row_in_any_table_is_deleted(self):
        """Requirement 3.2: a soft delete. Requirement 3.5: no dependent row is removed."""
        sb = _Supabase(_rows())
        before = {t: len(sb.rows[t]) for t in DEPENDENT_TABLES}

        await archive.archive_strategy(sb, _user(), STRATEGY_ID)

        assert sb.writes_of("delete") == [], "archiving must issue no DELETE at all"
        assert {t: len(sb.rows[t]) for t in DEPENDENT_TABLES} == before
        assert sb.row("strategies", STRATEGY_ID) is not None

    @pytest.mark.asyncio
    async def test_another_tenants_strategy_is_untouched(self):
        sb = _Supabase(_rows())

        await archive.archive_strategy(sb, _user(), STRATEGY_ID)

        assert sb.row("strategies", OTHER_STRATEGY_ID)[ARCHIVED_AT] is None

    @pytest.mark.asyncio
    async def test_it_records_one_audit_entry_naming_the_strategy(self, audit):
        sb = _Supabase(_rows())

        result = await archive.archive_strategy(sb, _user(), STRATEGY_ID)

        entries = [
            r
            for r in get_strategy_audit_logger().recorded
            if r.action is StrategyAuditAction.STRATEGY_ARCHIVED
        ]
        assert len(entries) == 1
        entry = entries[0]
        assert entry.resource_type == "strategy"
        assert entry.resource_id == STRATEGY_ID
        assert entry.actor_id == USER_ID
        assert entry.after == result[ARCHIVED_AT]


# ---------------------------------------------------------------------------
# 2. Requirement 3.1: an active deployment blocks, and is named
# ---------------------------------------------------------------------------


class TestActiveDeploymentsBlockArchival:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", sorted(STOPPABLE_BINDING_STATES))
    async def test_each_stoppable_state_refuses_with_409(self, state):
        sb = _Supabase(_rows(deployments=[_deployment_row(status=state.lower())]))

        with pytest.raises(archive.ArchiveRejected) as caught:
            await archive.archive_strategy(sb, _user(), STRATEGY_ID)

        rejection = caught.value
        assert rejection.code == "STRATEGY_HAS_ACTIVE_DEPLOYMENTS"
        assert rejection.http_status == 409
        blocking = rejection.details["blocking_deployments"]
        assert [b["deployment_id"] for b in blocking] == [DEPLOYMENT_ID]
        assert blocking[0]["binding_state"] == state

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state", ["STOPPED", "FAILED"])
    async def test_a_finished_deployment_does_not_block(self, state):
        sb = _Supabase(_rows(deployments=[_deployment_row(status=state.lower())]))

        result = await archive.archive_strategy(sb, _user(), STRATEGY_ID)

        assert result["status"] == archive.STATUS_ARCHIVED

    @pytest.mark.asyncio
    async def test_every_blocking_deployment_is_named_not_just_the_first(self):
        """Requirement 3.1 identifies *each* blocking deployment, not the first one found."""
        sb = _Supabase(
            _rows(
                deployments=[
                    _deployment_row(status="running"),
                    _deployment_row(status="paused", deployment_id=SECOND_DEPLOYMENT_ID),
                    _deployment_row(status="stopped", deployment_id="not-blocking"),
                ]
            )
        )

        with pytest.raises(archive.ArchiveRejected) as caught:
            await archive.archive_strategy(sb, _user(), STRATEGY_ID)

        blocking = caught.value.details["blocking_deployments"]
        assert {b["deployment_id"] for b in blocking} == {
            DEPLOYMENT_ID,
            SECOND_DEPLOYMENT_ID,
        }
        assert {b["binding_state"] for b in blocking} == {"RUNNING", "PAUSED"}

    @pytest.mark.asyncio
    async def test_a_refusal_writes_nothing(self):
        sb = _Supabase(_rows(deployments=[_deployment_row(status="running")]))

        with pytest.raises(archive.ArchiveRejected):
            await archive.archive_strategy(sb, _user(), STRATEGY_ID)

        assert sb.writes == []
        assert sb.row("strategies", STRATEGY_ID)[ARCHIVED_AT] is None

    @pytest.mark.asyncio
    async def test_a_legacy_running_bot_with_no_deployment_row_still_blocks(self):
        """``deploy_bot`` records a live bot by setting ``strategies.status``, nothing else.

        A strategy live only that way has no ``strategy_deployments`` row to find, and
        archiving it would hide a strategy whose bot is still trading.
        """
        sb = _Supabase(_rows(strategy=_strategy_row(status="running"), deployments=[]))

        with pytest.raises(archive.ArchiveRejected) as caught:
            await archive.archive_strategy(sb, _user(), STRATEGY_ID)

        blocking = caught.value.details["blocking_deployments"]
        assert len(blocking) == 1
        assert blocking[0]["binding_state"] == "RUNNING"
        assert blocking[0]["source"] == "strategies.status"

    @pytest.mark.asyncio
    async def test_an_unreadable_deployment_state_refuses_rather_than_archiving_blind(self):
        sb = _Supabase(_rows(), failing_tables=("strategy_deployments",))

        with pytest.raises(archive.ArchiveRejected) as caught:
            await archive.archive_strategy(sb, _user(), STRATEGY_ID)

        assert caught.value.code == "STRATEGY_DEPLOYMENT_STATE_UNREADABLE"
        assert caught.value.http_status == 503
        assert sb.row("strategies", STRATEGY_ID)[ARCHIVED_AT] is None


class TestTheBlockingSetIsTheKillSwitchesOwn:
    def test_it_is_stoppable_binding_states_imported_unchanged(self):
        """design.md: the archive gate and the kill switch cannot answer differently."""
        assert STOPPABLE_BINDING_STATES == ("DEPLOYING", "RUNNING", "PAUSED")
        assert set(STOPPABLE_BINDING_STATES) < set(BINDING_STATES)

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("running", True),
            ("active", True),
            ("paused", True),
            ("deploying", True),
            ("deployed", True),
            ("starting", True),
            ("stopped", False),
            ("stopping", False),
            ("failed", False),
            ("RUNNING", True),
            # strategies.status has its own vocabulary; none of these is a live bot,
            # and none of them may be guessed at.
            ("draft", False),
            ("saved", False),
            ("", False),
            (None, False),
        ],
    )
    def test_is_active_binding_status_recognises_only_live_spellings(self, raw, expected):
        assert is_active_binding_status(raw) is expected

    def test_an_unrecognised_status_is_not_reported_as_a_failed_deployment(self, caplog):
        """``binding_state`` warns and answers FAILED; this question must do neither."""
        with caplog.at_level(logging.WARNING):
            assert is_active_binding_status("draft") is False
        assert "draft" not in caplog.text


# ---------------------------------------------------------------------------
# 3. Requirement 3.6: re-archiving is idempotent
# ---------------------------------------------------------------------------


class TestReArchivingIsIdempotent:
    ORIGINAL = "2024-05-05T05:05:05+00:00"

    @pytest.mark.asyncio
    async def test_it_reports_already_archived_and_keeps_the_original_timestamp(self):
        sb = _Supabase(_rows(strategy=_strategy_row(**{ARCHIVED_AT: self.ORIGINAL})))

        result = await archive.archive_strategy(sb, _user(), STRATEGY_ID)

        assert result["status"] == archive.STATUS_ALREADY_ARCHIVED
        assert result[ARCHIVED_AT] == self.ORIGINAL
        assert sb.row("strategies", STRATEGY_ID)[ARCHIVED_AT] == self.ORIGINAL

    @pytest.mark.asyncio
    async def test_it_performs_no_further_state_change_and_no_second_audit_record(self):
        sb = _Supabase(_rows(strategy=_strategy_row(**{ARCHIVED_AT: self.ORIGINAL})))

        await archive.archive_strategy(sb, _user(), STRATEGY_ID)

        assert sb.writes == []
        assert get_strategy_audit_logger().recorded == []

    @pytest.mark.asyncio
    async def test_an_active_deployment_does_not_turn_it_into_a_409(self):
        """Already archived is answered before the deployment gate: there is nothing to do."""
        sb = _Supabase(
            _rows(
                strategy=_strategy_row(**{ARCHIVED_AT: self.ORIGINAL}),
                deployments=[_deployment_row(status="running")],
            )
        )

        result = await archive.archive_strategy(sb, _user(), STRATEGY_ID)

        assert result["status"] == archive.STATUS_ALREADY_ARCHIVED


# ---------------------------------------------------------------------------
# 4. Requirement 20.2: a non-owner is told nothing
# ---------------------------------------------------------------------------


class TestOwnership:
    @pytest.mark.asyncio
    async def test_a_non_owner_gets_the_same_404_a_missing_strategy_gets(self):
        sb = _Supabase(_rows())

        with pytest.raises(archive.ArchiveRejected) as theirs:
            await archive.archive_strategy(sb, _user(OTHER_USER_ID), STRATEGY_ID)
        with pytest.raises(archive.ArchiveRejected) as missing:
            await archive.archive_strategy(sb, _user(), "does-not-exist")

        assert theirs.value.http_status == missing.value.http_status == 404
        assert theirs.value.code == missing.value.code == "STRATEGY_NOT_FOUND"
        assert theirs.value.message == missing.value.message
        assert STRATEGY_ID not in theirs.value.message

    @pytest.mark.asyncio
    async def test_a_non_owners_attempt_writes_nothing(self):
        sb = _Supabase(_rows())

        with pytest.raises(archive.ArchiveRejected):
            await archive.archive_strategy(sb, _user(OTHER_USER_ID), STRATEGY_ID)

        assert sb.writes == []
        assert sb.row("strategies", STRATEGY_ID)[ARCHIVED_AT] is None


# ---------------------------------------------------------------------------
# 5. Migration 005a is unapplied: degrade, name the file, report nothing archived
# ---------------------------------------------------------------------------


class TestUnappliedMigration:
    """``005a_strategy_archive.sql``'s own header states this contract."""

    def test_the_migration_header_states_the_contract_this_class_tests(self):
        """If the migration's stated contract changes, this file must be revisited."""
        text = MIGRATION_PATH.read_text(encoding="utf-8", errors="replace")
        assert "005a_strategy_archive.sql" in text
        assert "NEVER a strategy reported as archived" in text

    def _absent(self, **kwargs):
        return _Supabase(_rows(**kwargs), absent_columns={ARCHIVED_AT})

    @pytest.mark.asyncio
    async def test_it_refuses_with_503_naming_the_migration(self):
        sb = self._absent()

        with pytest.raises(archive.ArchiveRejected) as caught:
            await archive.archive_strategy(sb, _user(), STRATEGY_ID)

        assert caught.value.code == "STRATEGY_ARCHIVE_UNAVAILABLE"
        assert caught.value.http_status == 503
        assert archive.STRATEGY_ARCHIVE_MIGRATION in caught.value.message
        assert caught.value.details["migration"] == archive.STRATEGY_ARCHIVE_MIGRATION

    @pytest.mark.asyncio
    async def test_it_is_a_warning_naming_the_file_not_an_unhandled_error(self, caplog):
        sb = self._absent()

        with caplog.at_level(logging.WARNING):
            with pytest.raises(archive.ArchiveRejected):
                await archive.archive_strategy(sb, _user(), STRATEGY_ID)

        assert "005a_strategy_archive.sql" in caplog.text
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    @pytest.mark.asyncio
    async def test_nothing_is_written_and_nothing_is_deleted(self):
        """The worst outcome would be falling back to the hard delete this replaced."""
        sb = self._absent()

        with pytest.raises(archive.ArchiveRejected):
            await archive.archive_strategy(sb, _user(), STRATEGY_ID)

        assert sb.writes == []
        assert sb.row("strategies", STRATEGY_ID) is not None
        assert all(sb.rows[t] for t in DEPENDENT_TABLES)

    @pytest.mark.asyncio
    async def test_the_write_path_classifies_it_too_when_the_probe_said_otherwise(self):
        """A cached positive verdict must not turn a 42703 at the UPDATE into a 500."""
        sb = self._absent()
        archive.reset_archive_column_support()
        with patch.object(archive, "archive_column_supported", AsyncMock(return_value=True)):
            with pytest.raises(archive.ArchiveRejected) as caught:
                await archive.archive_strategy(sb, _user(), STRATEGY_ID)

        assert caught.value.code == "STRATEGY_ARCHIVE_UNAVAILABLE"
        assert caught.value.http_status == 503
        # The write path recorded what it learned, so the next attempt does not re-probe.
        assert archive.archive_column_support_state() is False

    @pytest.mark.asyncio
    async def test_no_database_client_is_a_refusal_not_a_fabricated_success(self):
        with pytest.raises(archive.ArchiveRejected) as caught:
            await archive.archive_strategy(None, _user(), STRATEGY_ID)

        assert caught.value.code == "STRATEGY_ARCHIVE_UNAVAILABLE"
        assert caught.value.http_status == 503

    @pytest.mark.parametrize(
        "message,expected",
        [
            ("column strategies.archived_at does not exist (42703)", True),
            ("Could not find the 'archived_at' column ... (PGRST204)", True),
            ("archived_at not found in schema cache", True),
            ('relation "public.strategies" does not exist (42P01)', False),
            ("PGRST205 table not found", False),
            ("connection reset by peer", False),
            ("", False),
        ],
    )
    def test_only_a_definitive_missing_column_is_classified_as_one(self, message, expected):
        assert archive.is_missing_archived_at_error(RuntimeError(message)) is expected


# ---------------------------------------------------------------------------
# 6. Requirement 3.3: an archived identifier is refused afterwards
# ---------------------------------------------------------------------------


class TestArchivedStrategiesRefuseFurtherOperations:
    @pytest.mark.parametrize("operation", list(archive.ARCHIVED_REFUSED_OPERATIONS))
    def test_each_operation_named_by_requirement_3_3_is_refused(self, operation):
        row = _strategy_row(**{ARCHIVED_AT: "2024-01-01T00:00:00+00:00"})

        with pytest.raises(archive.ArchiveRejected) as caught:
            archive.assert_strategy_not_archived(
                row, operation=operation, strategy_id=STRATEGY_ID
            )

        rejection = caught.value
        assert rejection.code == "STRATEGY_ARCHIVED"
        assert rejection.http_status == 409
        assert rejection.details["operation"] == operation
        assert rejection.details[ARCHIVED_AT] == "2024-01-01T00:00:00+00:00"

    def test_an_active_strategy_passes(self):
        archive.assert_strategy_not_archived(
            _strategy_row(), operation=archive.OPERATION_EDIT
        )

    def test_a_row_without_the_column_passes(self):
        """005a unapplied: with no column, nothing can be archived - so nothing is refused."""
        row = _strategy_row()
        row.pop(ARCHIVED_AT)
        archive.assert_strategy_not_archived(row, operation=archive.OPERATION_EDIT)
        assert archive.is_archived(row) is False

    def test_an_unreadable_row_passes_rather_than_inventing_a_state(self):
        archive.assert_strategy_not_archived(None, operation=archive.OPERATION_DEPLOY)

    @pytest.mark.parametrize(
        "value,expected",
        [
            (None, False),
            ("", False),
            ("   ", False),
            ("2024-01-01T00:00:00+00:00", True),
        ],
    )
    def test_is_archived_is_archived_at_is_not_null_and_nothing_else(self, value, expected):
        assert archive.is_archived(_strategy_row(**{ARCHIVED_AT: value})) is expected

    def test_archival_is_not_read_off_the_status_column(self):
        """005a's header: archival is deliberately not a ``strategies.status`` spelling."""
        assert archive.is_archived(_strategy_row(status="archived")) is False

    @pytest.mark.asyncio
    async def test_the_archived_strategy_is_still_readable_by_its_owner(self):
        """Requirement 3.4: archived stays inspectable for history and audit."""
        sb = _Supabase(_rows())
        await archive.archive_strategy(sb, _user(), STRATEGY_ID)

        row = await archive.load_owned_strategy(sb, USER_ID, STRATEGY_ID)

        assert row is not None
        assert archive.is_archived(row) is True


# ---------------------------------------------------------------------------
# 7. The HTTP surface: DELETE /api/strategies/{id}
# ---------------------------------------------------------------------------


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend_app.core.dependencies import get_current_user
    from backend_app.routers import strategies as router_module

    app = FastAPI()
    app.include_router(router_module.router, prefix="/api/strategies")
    app.state.limiter = router_module.limiter
    app.dependency_overrides[get_current_user] = lambda: _user()
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def quota():
    """The quota counter needs Redis; its calls are observed instead."""
    with patch(
        "backend_app.core.subscription_engine.SubscriptionEngine.decrement_quota_usage",
        new=AsyncMock(return_value=0),
    ) as decrement:
        yield decrement


class TestDeleteEndpointIsRewiredToArchive:
    def _call(self, sb, strategy_id=STRATEGY_ID):
        with patch(
            "backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb)
        ):
            return _client().delete(f"/api/strategies/{strategy_id}")

    def test_it_answers_200_archived_with_the_timestamp(self, quota):
        sb = _Supabase(_rows())

        response = self._call(sb)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == archive.STATUS_ARCHIVED
        assert body["strategy_id"] == STRATEGY_ID
        assert body[ARCHIVED_AT]
        assert sb.row("strategies", STRATEGY_ID)[ARCHIVED_AT] == body[ARCHIVED_AT]

    def test_it_issues_no_delete_against_any_table(self, quota):
        sb = _Supabase(_rows())

        self._call(sb)

        assert sb.writes_of("delete") == []

    def test_it_releases_the_strategy_quota_slot_once(self, quota):
        sb = _Supabase(_rows())

        self._call(sb)

        assert quota.await_count == 1

    def test_an_already_archived_strategy_releases_nothing_further(self, quota):
        sb = _Supabase(
            _rows(strategy=_strategy_row(**{ARCHIVED_AT: "2024-01-01T00:00:00+00:00"}))
        )

        response = self._call(sb)

        assert response.status_code == 200
        assert response.json()["status"] == archive.STATUS_ALREADY_ARCHIVED
        assert quota.await_count == 0

    def test_a_blocking_deployment_is_a_409_naming_it(self, quota):
        sb = _Supabase(_rows(deployments=[_deployment_row(status="running")]))

        response = self._call(sb)

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["error"] == "STRATEGY_HAS_ACTIVE_DEPLOYMENTS"
        assert detail["blocking_deployments"][0]["deployment_id"] == DEPLOYMENT_ID
        assert detail["blocking_deployments"][0]["binding_state"] == "RUNNING"
        assert quota.await_count == 0

    def test_a_non_owner_gets_404_and_no_write(self, quota):
        from backend_app.core.dependencies import get_current_user
        from backend_app.routers import strategies as router_module

        sb = _Supabase(_rows())
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(router_module.router, prefix="/api/strategies")
        app.state.limiter = router_module.limiter
        app.dependency_overrides[get_current_user] = lambda: _user(OTHER_USER_ID)
        with patch(
            "backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb)
        ):
            response = TestClient(app, raise_server_exceptions=False).delete(
                f"/api/strategies/{STRATEGY_ID}"
            )

        assert response.status_code == 404
        assert sb.writes == []

    def test_an_unapplied_migration_is_503_and_not_a_deletion(self, quota):
        sb = _Supabase(_rows(), absent_columns={ARCHIVED_AT})

        response = self._call(sb)

        assert response.status_code == 503
        detail = response.json()["detail"]
        assert detail["error"] == "STRATEGY_ARCHIVE_UNAVAILABLE"
        assert archive.STRATEGY_ARCHIVE_MIGRATION in detail["message"]
        assert sb.writes == []
        assert sb.row("strategies", STRATEGY_ID) is not None
        assert quota.await_count == 0

    def test_the_handler_source_no_longer_deletes_the_row(self):
        """Read off the source, so a re-introduced hard delete cannot pass silently."""
        source = ROUTER_PATH.read_text(encoding="utf-8", errors="replace")
        start = source.index('@router.delete("/{strategy_id}")')
        handler = source[start : source.index('@router.post("/{strategy_id}/deploy")')]
        assert "archive_strategy" in handler
        assert ".delete()" not in handler

    def test_editing_an_archived_strategy_is_refused(self):
        sb = _Supabase(
            _rows(strategy=_strategy_row(**{ARCHIVED_AT: "2024-01-01T00:00:00+00:00"}))
        )

        with patch(
            "backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb)
        ):
            response = _client().put(
                f"/api/strategies/{STRATEGY_ID}", json={"name": "renamed"}
            )

        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "STRATEGY_ARCHIVED"
        assert sb.writes == []

    def test_editing_an_active_strategy_still_works(self):
        sb = _Supabase(_rows())

        with patch(
            "backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb)
        ):
            response = _client().put(
                f"/api/strategies/{STRATEGY_ID}", json={"name": "renamed"}
            )

        assert response.status_code == 200
        assert sb.row("strategies", STRATEGY_ID)["name"] == "renamed"
