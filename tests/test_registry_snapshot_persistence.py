# -*- coding: utf-8 -*-
"""
tests/test_registry_snapshot_persistence.py

Tests for block registry snapshot provenance.

Spec: strategy-builder task 3.2. Requirements 4.16, 9.1.

What these tests hold in place
------------------------------
* **Requirement 4.16.** A snapshot is retained for each registry version: one row per
  ``registry_version``, carrying the whole ``BlockRegistry.to_dict()`` payload.
* **Idempotency on ``registry_version``.** Two layers, tested separately. In-process, the
  second attempt issues no statement at all. Across processes - modelled by clearing the
  process cache and writing again over the same rows - the upsert names
  ``on_conflict="registry_version"`` with ``ignore_duplicates=True``, so the table still
  holds exactly one row. Restarting the API does not accumulate snapshots.
* **The deploy-ordering hazard.** Migration 004 part 2
  (``backend_app/migrations/004b_block_registry_snapshots.sql``) is applied by hand, so the
  code reaches production first. With the table absent the write degrades to a WARNING that
  NAMES THE MIGRATION, does not raise, and does not fail the save that triggered it.
* **A genuine write error is not swallowed.** A check violation, a unique violation or a
  connection failure is not mistaken for a missing table.
  :func:`write_registry_snapshot` re-raises it; :func:`record_registry_snapshot` converts it
  to ``FAILED``, logs it at ERROR with a traceback, and - the part that matters - does NOT
  remember the version as recorded, so the next save retries instead of assuming success.
* **Requirement 9.1's half of the task.** The ``registry_version`` recorded on a created
  version equals the assembled registry's, and equals the key of the snapshot written on the
  same save. The two artifacts agree, which is the whole point: the label on the version
  resolves to a row in the snapshot table.
* **The purity guard is not weakened.** No database write was added under ``strategy_dag/``;
  ``tests/test_strategy_dag_architecture.py`` is the authority on that and stays green. This
  file asserts the corollary: importing the snapshot writer does not assemble the registry
  or drag in a heavy dependency.

What is NOT covered here
------------------------
There is no local PostgreSQL, so the database is the in-memory model from
``tests/test_strategy_version_canonical_persistence.py``, reused rather than duplicated. It
enforces the snapshot table's column set, ``chk_brs_descriptors_shape``, NOT NULL on
``descriptors``, the primary key, and ON CONFLICT DO NOTHING. It cannot prove:

* that the real RLS policies and grants on ``block_registry_snapshots`` are wired as
  intended - those are asserted structurally by the migration's own verification queries,
* that PostgREST reports a missing table with the exact message shape modelled here (both
  PostgreSQL ``42P01`` and PostgREST ``PGRST205`` are recognised, as is a message naming the
  table, so all three known shapes degrade),
* JSONB coercion of the descriptor payload.

Nothing about the registry is faked: the payload written is the real assembled
``BlockRegistry.to_dict()``.
"""

import asyncio
import json
import logging
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from backend_app.backend.registry_snapshot_service import (
    RECORDED_OUTCOMES,
    SNAPSHOT_KEY_COLUMN,
    SNAPSHOT_PAYLOAD_COLUMN,
    SNAPSHOT_TABLE,
    SNAPSHOT_TABLE_MIGRATION,
    SnapshotOutcome,
    is_missing_snapshot_table_error,
    is_snapshot_permission_error,
    record_registry_snapshot,
    recorded_registry_versions,
    reset_registry_snapshot_state,
    snapshot_row,
    snapshot_table_support_state,
    write_registry_snapshot,
)
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag.registry import BlockRegistry

# The database model, the graph builders and the service seam, reused rather than rebuilt.
from tests.test_strategy_version_canonical_persistence import (
    USER_A,
    CheckViolation,
    FakeDB,
    UndefinedTable,
    UniqueViolation,
    seeded_db,
    service_on,
    valid_graph,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reg() -> BlockRegistry:
    """The real assembled registry. Assembly is ~150 ms, so it is shared."""
    return registry_module.build_registry()


@pytest.fixture(autouse=True)
def _clear_snapshot_state():
    """The table verdict and the recorded-version set are process-cached; no test may
    inherit another's."""
    reset_registry_snapshot_state()
    yield
    reset_registry_snapshot_state()


def snapshot_db(*, snapshot_table: bool = True) -> FakeDB:
    """A client with no strategies, for the writer tested in isolation."""
    return FakeDB(USER_A["id"], snapshot_table=snapshot_table)


class ExplodingClient:
    """A client whose every statement fails with ``exc``. Models a database that is
    reachable in name only: a connection reset, a timeout, a constraint violation."""

    def __init__(self, exc: BaseException, *, fail_probe: bool = False):
        self._exc = exc
        self._fail_probe = fail_probe
        self.calls: list = []

    def table(self, name):
        self.calls.append(name)
        return self

    def select(self, *args, **kwargs):
        self._pending = "select"
        return self

    def limit(self, *args, **kwargs):
        return self

    def upsert(self, data, **kwargs):
        self._pending = "upsert"
        self.upsert_kwargs = kwargs
        return self

    def execute(self):
        if self._pending == "select" and not self._fail_probe:
            # The probe succeeds: the table exists. Only the WRITE fails, which is the
            # case that must not be mistaken for "migration not applied".
            return type("R", (), {"data": [], "error": None})()
        raise self._exc


# ---------------------------------------------------------------------------
# Requirement 4.16 - the row
# ---------------------------------------------------------------------------


def test_snapshot_row_is_the_whole_served_payload_keyed_by_the_hash(reg):
    """Requirement 4.16: the snapshot IS the descriptor set, not a summary of it."""
    row = snapshot_row(reg)

    assert row is not None
    assert set(row) == {SNAPSHOT_KEY_COLUMN, SNAPSHOT_PAYLOAD_COLUMN}
    assert row[SNAPSHOT_KEY_COLUMN] == reg.registry_version
    assert row[SNAPSHOT_PAYLOAD_COLUMN] == reg.to_dict()

    payload = row[SNAPSHOT_PAYLOAD_COLUMN]
    # Everything needed to reconstruct what the author was offered.
    assert payload["registry_version"] == reg.registry_version
    assert len(payload["blocks"]) == len(reg)
    for key in ("registry_schema_version", "port_types", "categories", "compatibility_matrix"):
        assert key in payload, f"{key} missing from the snapshot payload"

    # chk_brs_descriptors_shape holds, and the document survives a JSONB round trip.
    assert isinstance(payload, dict)
    assert isinstance(payload["blocks"], list)
    assert payload["registry_version"] == row[SNAPSHOT_KEY_COLUMN]
    assert json.loads(json.dumps(payload)) == payload

    # created_at is deliberately absent: the column DEFAULT keeps the recorded time on the
    # database clock, not the application's.
    assert "created_at" not in row


def test_a_descriptor_source_that_is_not_a_registry_is_a_non_event():
    """A caller-supplied descriptor source may publish no version and no payload. That is
    not a failure, and must not produce a write or a scary log line."""

    class BareSource:
        pass

    class VersionOnly:
        registry_version = "r_deadbeef"

    class MismatchedPayload:
        registry_version = "r_deadbeef"

        def to_dict(self):
            return {"registry_version": "r_something_else", "blocks": []}

    for source in (BareSource(), VersionOnly(), MismatchedPayload()):
        assert snapshot_row(source) is None


@pytest.mark.asyncio
async def test_an_unsupported_source_writes_nothing(reg):
    class BareSource:
        pass

    db = snapshot_db()
    outcome = await record_registry_snapshot(db, BareSource())

    assert outcome is SnapshotOutcome.UNSUPPORTED_SOURCE
    assert db.snapshots == []
    assert db.statements == []


@pytest.mark.asyncio
async def test_one_snapshot_is_written_for_the_assembled_registry(reg):
    """Requirement 4.16, the happy path."""
    db = snapshot_db()

    outcome = await record_registry_snapshot(db, reg)

    assert outcome is SnapshotOutcome.WRITTEN
    assert len(db.snapshots) == 1
    row = db.snapshots[0]
    assert row[SNAPSHOT_KEY_COLUMN] == reg.registry_version
    assert row[SNAPSHOT_PAYLOAD_COLUMN] == reg.to_dict()
    assert row["created_at"] is not None  # supplied by the column DEFAULT
    assert reg.registry_version in recorded_registry_versions()


# ---------------------------------------------------------------------------
# Idempotency - layer 1 (in-process) and layer 2 (the primary key)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_second_write_in_the_same_process_issues_no_statement(reg):
    """One write per descriptor set, not one per request. The second attempt must not even
    reach the database."""
    db = snapshot_db()

    first = await record_registry_snapshot(db, reg)
    statements_after_first = list(db.statements)

    second = await record_registry_snapshot(db, reg)

    assert first is SnapshotOutcome.WRITTEN
    assert second is SnapshotOutcome.ALREADY_RECORDED
    assert first in RECORDED_OUTCOMES and second in RECORDED_OUTCOMES
    assert len(db.snapshots) == 1
    assert db.statements == statements_after_first, "the no-op still hit the database"


@pytest.mark.asyncio
async def test_a_restart_or_second_process_does_not_add_a_second_row(reg):
    """The process cache is not the only thing keeping the count at one. Clearing it models
    a restart, a second worker, or a concurrent request that lost the in-process race: the
    upsert must be ON CONFLICT DO NOTHING on the primary key."""
    db = snapshot_db()

    await record_registry_snapshot(db, reg)
    reset_registry_snapshot_state()  # a fresh process over the same rows
    outcome = await record_registry_snapshot(db, reg)

    assert outcome is SnapshotOutcome.WRITTEN  # sent again...
    assert len(db.snapshots) == 1  # ...and the table still holds one row

    # The conflict target is the primary key, and duplicates are ignored rather than
    # erroring. Naming a different column, or omitting it, would not be idempotent.
    assert db.upserts
    for table, on_conflict, ignore_duplicates in db.upserts:
        assert table == SNAPSHOT_TABLE
        assert on_conflict == SNAPSHOT_KEY_COLUMN
        assert ignore_duplicates is True


@pytest.mark.asyncio
async def test_concurrent_writers_converge_on_one_row(reg):
    """Ten simultaneous first-time writes, all racing the in-process cache."""
    db = snapshot_db()

    outcomes = await asyncio.gather(
        *(record_registry_snapshot(db, reg) for _ in range(10))
    )

    assert all(o in RECORDED_OUTCOMES for o in outcomes)
    assert len(db.snapshots) == 1


@pytest.mark.asyncio
async def test_a_changed_registry_gets_its_own_snapshot(reg):
    """Different descriptor set, different hash, second row. A snapshot per registry
    version is the requirement, not a snapshot per platform."""
    db = snapshot_db()

    class ShiftedRegistry:
        """The same payload under a different version, as a registry change would look."""

        registry_version = "r_00000001"

        def to_dict(self):
            payload = dict(reg.to_dict())
            payload["registry_version"] = "r_00000001"
            return payload

    await record_registry_snapshot(db, reg)
    await record_registry_snapshot(db, ShiftedRegistry())

    keys = sorted(r[SNAPSHOT_KEY_COLUMN] for r in db.snapshots)
    assert keys == sorted([reg.registry_version, "r_00000001"])


# ---------------------------------------------------------------------------
# The table is absent - migration 004 part 2 unapplied
# ---------------------------------------------------------------------------


def test_missing_table_errors_are_recognised_and_nothing_else_is():
    """The classification is narrow on purpose: a missing COLUMN, a permission problem or a
    connection reset must not be reported as "apply the migration"."""
    assert is_missing_snapshot_table_error(UndefinedTable(SNAPSHOT_TABLE))
    assert is_missing_snapshot_table_error(
        Exception("{'code': '42P01', 'message': 'relation does not exist'}")
    )
    assert is_missing_snapshot_table_error(
        Exception(
            "{'code': 'PGRST205', 'message': \"Could not find the table "
            f"'public.{SNAPSHOT_TABLE}' in the schema cache\"}}"
        )
    )
    assert is_missing_snapshot_table_error(
        Exception(f"relation {SNAPSHOT_TABLE} does not exist")
    )

    assert not is_missing_snapshot_table_error(Exception(""))
    assert not is_missing_snapshot_table_error(
        Exception("{'code': '42703', 'message': 'column descriptors does not exist'}")
    )
    assert not is_missing_snapshot_table_error(UniqueViolation("block_registry_snapshots_pkey"))
    assert not is_missing_snapshot_table_error(Exception("connection reset by peer"))
    assert not is_missing_snapshot_table_error(
        Exception("{'code': '42501', 'message': 'permission denied'}")
    )


def test_permission_errors_are_recognised_separately():
    """A denied insert has a specific fix, so it gets a specific warning."""
    assert is_snapshot_permission_error(
        Exception("{'code': '42501', 'message': 'permission denied for table'}")
    )
    assert is_snapshot_permission_error(
        Exception("new row violates row-level security policy")
    )
    assert not is_snapshot_permission_error(Exception("connection reset by peer"))
    assert not is_snapshot_permission_error(Exception(""))


@pytest.mark.asyncio
async def test_a_missing_table_warns_names_the_migration_and_does_not_raise(reg, caplog):
    """The deploy-ordering hazard. Requirement 4.16 goes unmet, loudly, and nothing breaks."""
    db = snapshot_db(snapshot_table=False)

    with caplog.at_level(logging.WARNING):
        outcome = await record_registry_snapshot(db, reg)

    assert outcome is SnapshotOutcome.TABLE_ABSENT
    assert db.snapshots == []

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "a missing table degraded silently"
    text = "\n".join(r.getMessage() for r in warnings)
    assert SNAPSHOT_TABLE_MIGRATION in text, "the warning does not name the migration to apply"
    assert SNAPSHOT_TABLE in text
    assert "4.16" in text

    # Not reported as an error, and not remembered as recorded, so applying the migration
    # later is enough - no code change, no redeploy.
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert reg.registry_version not in recorded_registry_versions()


@pytest.mark.asyncio
async def test_the_absent_verdict_is_probed_once_not_once_per_call(reg):
    """The probe is a query. It must not become a per-save query."""
    db = snapshot_db(snapshot_table=False)

    for _ in range(5):
        assert await record_registry_snapshot(db, reg) is SnapshotOutcome.TABLE_ABSENT

    assert snapshot_table_support_state() is False
    probes = [s for s in db.statements if s[0] == SNAPSHOT_TABLE and s[1] == "select"]
    assert len(probes) == 1, f"probed {len(probes)} times; the verdict is not cached"


@pytest.mark.asyncio
async def test_a_missing_table_does_not_stop_a_save(reg):
    """The load-bearing one: provenance is best effort, the save is not."""
    db, strategy_id = seeded_db(USER_A, snapshot_table=False)
    service = service_on(db)

    result = await service.create_version(USER_A, strategy_id, valid_graph(reg), registry=reg)

    assert len(db.versions) == 1
    assert db.versions[0][SNAPSHOT_KEY_COLUMN] == reg.registry_version
    assert result["registry_snapshot"] == SnapshotOutcome.TABLE_ABSENT.value
    assert db.snapshots == []


@pytest.mark.asyncio
async def test_no_client_is_reported_rather_than_assumed(reg):
    outcome = await record_registry_snapshot(None, reg)
    assert outcome is SnapshotOutcome.NO_CLIENT
    assert reg.registry_version not in recorded_registry_versions()


# ---------------------------------------------------------------------------
# A genuine write error is not swallowed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_genuine_write_error_propagates_from_the_strict_writer(reg):
    """``write_registry_snapshot`` re-raises what it cannot classify. A failure that is
    neither a missing table nor a denied insert is a real failure."""
    boom = UniqueViolation("block_registry_snapshots_pkey")
    client = ExplodingClient(boom)

    with pytest.raises(type(boom)):
        await write_registry_snapshot(client, reg)


@pytest.mark.asyncio
async def test_a_genuine_write_error_is_logged_as_an_error_and_retried(reg, caplog):
    """``record_registry_snapshot`` may not raise - but it may not pretend either. The
    failure is an ERROR with a traceback, and the version is NOT remembered, so the next
    save tries again instead of assuming provenance was recorded."""
    client = ExplodingClient(RuntimeError("connection reset by peer"))

    with caplog.at_level(logging.ERROR):
        outcome = await record_registry_snapshot(client, reg)

    assert outcome is SnapshotOutcome.FAILED

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "a genuine write failure was swallowed"
    assert any("connection reset by peer" in r.getMessage() for r in errors)
    assert any(r.exc_info for r in errors), "no traceback was captured"

    # Not remembered, so it is retried rather than silently skipped forever.
    assert reg.registry_version not in recorded_registry_versions()
    assert snapshot_table_support_state() is not False, (
        "a connection failure was cached as 'table absent'"
    )

    client2 = ExplodingClient(RuntimeError("connection reset by peer"))
    assert await record_registry_snapshot(client2, reg) is SnapshotOutcome.FAILED
    assert client2.calls, "the retry never reached the client"


@pytest.mark.asyncio
async def test_a_check_violation_is_not_mistaken_for_a_missing_table(reg):
    """23514 means the payload was rejected. Reporting that as "apply the migration" would
    send an operator on a wild goose chase."""
    client = ExplodingClient(CheckViolation("chk_brs_descriptors_shape"))

    with pytest.raises(CheckViolation):
        await write_registry_snapshot(client, reg)

    assert snapshot_table_support_state() is not False


@pytest.mark.asyncio
async def test_a_denied_insert_warns_with_the_policy_to_check(reg, caplog):
    """RLS or a missing grant. Classified, not swallowed, and not raised at the save."""
    client = ExplodingClient(
        Exception("{'code': '42501', 'message': 'new row violates row-level security policy'}")
    )

    with caplog.at_level(logging.WARNING):
        outcome = await record_registry_snapshot(client, reg)

    assert outcome is SnapshotOutcome.PERMISSION_DENIED
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "brs_authenticated_append" in text
    assert SNAPSHOT_TABLE_MIGRATION in text
    assert reg.registry_version not in recorded_registry_versions()


@pytest.mark.asyncio
async def test_a_failing_probe_does_not_pre_emptively_degrade_the_write(reg, caplog):
    """An inconclusive probe must resolve to "try the write", so a real error surfaces at
    the insert instead of being reported as an unapplied migration."""
    client = ExplodingClient(RuntimeError("timed out"), fail_probe=True)

    with pytest.raises(RuntimeError, match="timed out"):
        await write_registry_snapshot(client, reg)

    assert snapshot_table_support_state() is None, "a timeout was cached as a verdict"


@pytest.mark.asyncio
async def test_record_never_raises_whatever_happens(reg):
    """The contract in one test: for any client, any registry and any database state,
    ``record_registry_snapshot`` returns an outcome."""

    class Nasty:
        def table(self, name):
            raise KeyboardInterrupt if False else ValueError("no such table handle")

    for client in (None, Nasty(), ExplodingClient(RuntimeError("boom"))):
        outcome = await record_registry_snapshot(client, reg)
        assert isinstance(outcome, SnapshotOutcome)


# ---------------------------------------------------------------------------
# Requirement 9.1 - the version's registry_version resolves to a snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_save_records_the_version_and_the_snapshot_under_the_same_hash(reg):
    """Requirement 9.1 plus 4.16, joined. The label on the version row must resolve to a
    row in the snapshot table, or the provenance chain is broken.

    Note the version-side half of this is already satisfied by task 2.3: ``create_version``
    writes ``registry_version`` into the canonical column and
    ``tests/test_strategy_version_canonical_persistence.py`` asserts it. What is new here is
    that the hash now RESOLVES.
    """
    db, strategy_id = seeded_db(USER_A)
    service = service_on(db)

    result = await service.create_version(USER_A, strategy_id, valid_graph(reg), registry=reg)

    version_row = db.versions[0]
    assert version_row[SNAPSHOT_KEY_COLUMN] == reg.registry_version
    assert result[SNAPSHOT_KEY_COLUMN] == reg.registry_version
    assert result["registry_snapshot"] == SnapshotOutcome.WRITTEN.value

    # The join in the migration's verification query 7 returns nothing: every version's
    # registry_version has a snapshot.
    snapshot_keys = {r[SNAPSHOT_KEY_COLUMN] for r in db.snapshots}
    assert version_row[SNAPSHOT_KEY_COLUMN] in snapshot_keys

    snapshot = next(
        r for r in db.snapshots if r[SNAPSHOT_KEY_COLUMN] == version_row[SNAPSHOT_KEY_COLUMN]
    )
    assert snapshot[SNAPSHOT_PAYLOAD_COLUMN] == reg.to_dict()


@pytest.mark.asyncio
async def test_many_saves_record_one_snapshot(reg):
    """Three saves against the same registry: three versions, one snapshot."""
    db, strategy_id = seeded_db(USER_A)
    service = service_on(db)

    outcomes = []
    for _ in range(3):
        result = await service.create_version(
            USER_A, strategy_id, valid_graph(reg), registry=reg
        )
        outcomes.append(result["registry_snapshot"])

    assert len(db.versions) == 3
    assert len(db.snapshots) == 1
    assert outcomes == [
        SnapshotOutcome.WRITTEN.value,
        SnapshotOutcome.ALREADY_RECORDED.value,
        SnapshotOutcome.ALREADY_RECORDED.value,
    ]


@pytest.mark.asyncio
async def test_the_snapshot_is_written_after_the_version_row(reg):
    """Ordering. A provenance record for a save that did not happen would be a lie, and a
    snapshot write that fails must not be able to prevent the save.
    """
    db, strategy_id = seeded_db(USER_A)
    service = service_on(db)

    await service.create_version(USER_A, strategy_id, valid_graph(reg), registry=reg)

    version_insert = next(
        i for i, s in enumerate(db.statements) if s[:2] == ("strategy_versions", "insert")
    )
    snapshot_write = next(
        i for i, s in enumerate(db.statements) if s[:2] == (SNAPSHOT_TABLE, "upsert")
    )
    assert version_insert < snapshot_write


@pytest.mark.asyncio
async def test_an_invalid_graph_records_no_snapshot(reg):
    """The failure path still persists nothing - including no provenance for a version that
    does not exist."""
    from tests.test_strategy_version_canonical_persistence import graph_without_action

    from backend_app.backend.strategy_compiler import ValidationError

    db, strategy_id = seeded_db(USER_A)
    service = service_on(db)

    with pytest.raises(ValidationError):
        await service.create_version(
            USER_A, strategy_id, graph_without_action(reg), registry=reg
        )

    assert db.versions == []
    assert db.snapshots == []
    assert db.statements == []


# ---------------------------------------------------------------------------
# The purity guard is not weakened
# ---------------------------------------------------------------------------


def test_the_snapshot_writer_lives_outside_strategy_dag():
    """The write is in the service layer. ``strategy_dag`` stays pure - no database handle,
    no client, no I/O - which is what ``tests/test_strategy_dag_architecture.py`` enforces
    and what keeps the compiler importable by the worker and the backtester.

    Validates: Requirements 21.10
    """
    import backend_app.backend.registry_snapshot_service as writer

    assert "strategy_dag" not in Path(writer.__file__).parts

    dag_dir = REPO_ROOT / "backend_app" / "backend" / "strategy_dag"
    for path in dag_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        assert SNAPSHOT_TABLE not in source, (
            f"{path.name} references {SNAPSHOT_TABLE}; the pure core must not write to the "
            "database"
        )
        assert "registry_snapshot_service" not in source, (
            f"{path.name} imports the snapshot writer; that inverts the layering"
        )


def test_importing_the_snapshot_writer_assembles_nothing_and_drags_in_nothing_heavy():
    """Importing the writer must not trigger registry assembly (which reads
    ``exchange_executor.OrderType`` and would pull in CCXT) and must not load the web layer.
    The registry import inside it is lazy for exactly this reason.
    """
    code = (
        "import sys\n"
        "import backend_app.backend.registry_snapshot_service as m\n"
        "heavy = sorted(x for x in ('ccxt', 'fastapi') if x in sys.modules)\n"
        "from backend_app.backend.strategy_dag import registry as r\n"
        "print(heavy, r._REGISTRY is None)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert result.stdout.strip() == "[] True", result.stdout


# ---------------------------------------------------------------------------
# The migration file itself
# ---------------------------------------------------------------------------

MIGRATION_PATH = REPO_ROOT / SNAPSHOT_TABLE_MIGRATION


def _migration_sql() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


def test_the_named_migration_exists_and_is_the_one_the_warning_points_at():
    assert MIGRATION_PATH.is_file(), (
        f"{SNAPSHOT_TABLE_MIGRATION} is named in the degradation warning but does not exist"
    )


def test_the_migration_is_ascii_and_carries_no_bom():
    """Part 1 is ASCII-only; a BOM or a stray em-dash breaks tooling that reads these files
    without an explicit encoding."""
    raw = MIGRATION_PATH.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "the migration carries a UTF-8 BOM"
    raw.decode("ascii")  # raises if any byte is non-ASCII


def test_the_migration_creates_the_table_the_writer_writes_to():
    sql = _migration_sql()
    assert f"CREATE TABLE IF NOT EXISTS public.{SNAPSHOT_TABLE}" in sql
    # The three design.md columns, and the primary key the upsert conflicts on.
    assert f"{SNAPSHOT_KEY_COLUMN} TEXT PRIMARY KEY" in sql
    assert f"{SNAPSHOT_PAYLOAD_COLUMN}      JSONB NOT NULL" in sql
    assert "created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()" in sql


def test_the_migration_has_no_destructive_statement():
    """Additive only. Every occurrence of a destructive keyword must be inside a comment."""
    forbidden = ("DROP ", "TRUNCATE", "DELETE", "ALTER COLUMN")
    offenders = []
    for lineno, line in enumerate(_migration_sql().splitlines(), start=1):
        code = line.split("--", 1)[0]
        for keyword in forbidden:
            if keyword in code.upper():
                offenders.append((lineno, keyword, line.strip()))
    assert not offenders, f"destructive statement outside a comment: {offenders}"


def test_the_migration_is_one_balanced_transaction():
    sql = _migration_sql()
    code = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    assert code.count("BEGIN;") == 1
    assert code.count("COMMIT;") == 1
    assert code.index("BEGIN;") < code.index("COMMIT;")
    # Every DO $$ opens and closes.
    assert code.count("DO $$") == code.count("END $$;")
    assert code.count("$$") % 2 == 0


def test_the_migration_keeps_the_table_append_only_and_closed_to_anon():
    """The RLS decision, asserted rather than described. A SELECT policy and an INSERT
    policy; no UPDATE and no DELETE policy, so a recorded snapshot is immutable; and anon's
    grants revoked."""
    sql = _migration_sql()
    code = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())

    assert f"ALTER TABLE public.{SNAPSHOT_TABLE} ENABLE ROW LEVEL SECURITY;" in code
    assert "FOR SELECT" in code and "USING (true)" in code
    assert "FOR INSERT" in code and "WITH CHECK (true)" in code
    assert "FOR UPDATE" not in code, "an UPDATE policy would let a snapshot be rewritten"
    assert "FOR DELETE" not in code, "a DELETE policy would let a snapshot be removed"
    assert "FOR ALL" not in code

    assert f"REVOKE ALL ON public.{SNAPSHOT_TABLE} FROM anon;" in code
    assert f"GRANT SELECT, INSERT ON public.{SNAPSHOT_TABLE} TO authenticated;" in code

    # No tenant column is invented for a platform-global table.
    assert "auth.uid()" not in code
    assert "user_id" not in code


def test_the_migration_touches_no_existing_table():
    """Additive in the strongest sense: the only relation this file names in a DDL statement
    is the one it creates."""
    code = "\n".join(line.split("--", 1)[0] for line in _migration_sql().splitlines())
    for statement in ("ALTER TABLE", "CREATE TABLE", "CREATE POLICY", "GRANT", "REVOKE"):
        for line in code.splitlines():
            if statement in line:
                assert SNAPSHOT_TABLE in line or statement in ("CREATE POLICY",), (
                    f"{statement} names something other than {SNAPSHOT_TABLE}: {line.strip()}"
                )
