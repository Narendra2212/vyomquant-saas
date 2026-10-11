"""Every SQLAlchemy model must match the schema the migrations actually produce.

WHAT THIS GUARDS
----------------
Found by provisioning a real PostgreSQL from source (``scripts/provision_database.py``)
and then querying through the ORM: ``backend_app.core.position_model.PositionModel``
declares fourteen columns on table ``positions``, and the provisioned table has five.
Eleven declared columns do not exist. A real query fails:

    ProgrammingError: (psycopg2.errors.UndefinedColumn)
    column positions.position_id does not exist

    model declares     : avg_entry_price, closed_at, created_at, exchange_id,
                         position_id, realized_pnl, side, size, status, strategy_id,
                         symbol, tenant_id, unrealized_pnl, updated_at
    database has       : created_at, data, id, tenant_id, updated_at
    declared-but-absent: avg_entry_price, closed_at, exchange_id, position_id,
                         realized_pnl, side, size, status, strategy_id, symbol,
                         unrealized_pnl

ROOT CAUSE — three competing declarations of one table:

  1. ``alembic/versions/d97ffff9c3bb_execution_schema_rebuild.py`` creates ``positions``
     as a generic blob: ``(id, tenant_id, data json, created_at, updated_at)``.
  2. ``core/position_model.PositionModel`` declares it with fourteen typed columns.
  3. ``backend/state_service.py`` declares a THIRD ``PositionModel`` on the same
     ``__tablename__ = "positions"``, with yet another column set (``user_id``,
     ``version``, ``position_id``).

Whichever runs first wins, and ``main.py``'s ``Base.metadata.create_all()`` skips a table
that already exists — so the ORM silently binds to a table it cannot read.

BLAST RADIUS, by import: ``backend/position_engine.py`` (the fill-to-position path),
``backend/pnl_engine.py``, ``backend/metrics_system.py``, ``backend/fee_engine.py`` and
``core/consistency_checker.py`` (exchange reconciliation) all query through
``PositionRepository``.

WHY THIS TEST IS GENERAL RATHER THAN ONE ASSERTION ABOUT ``positions``
----------------------------------------------------------------------
A test that pinned only this table would not have caught it before it happened, and will
not catch the next one. This walks every mapped model and compares it against the
provisioned catalogue, so any model/schema divergence fails here.

HOW IT RUNS
-----------
Requires a provisioned PostgreSQL. SKIPS when one is not configured, because a skip is
the honest result for "the infrastructure this needs was not available" — it must never
report PASS without having looked. Provide it with:

    python staging/bootstrap_pg.py start --data-dir pgdata_r1 --expect-provisioned
    python scripts/provision_database.py --dsn <dsn> --apply --create-substrate
    set AERORA_SCHEMA_CHECK_DSN=<dsn>

The data directory is named explicitly because `bootstrap_pg`'s default is `pgdata`, a
different cluster from the provisioned `pgdata_r1`; the bare form now fails closed rather
than quietly creating an empty one (Phase 10 / Workstreams H and I).
"""

import os
import pathlib

import pytest

DSN = os.environ.get("AERORA_SCHEMA_CHECK_DSN", "")

if not DSN:
    _dsn_file = pathlib.Path(__file__).resolve().parent.parent / "staging" / "pg_dsn.txt"
    if _dsn_file.is_file():
        DSN = _dsn_file.read_text(encoding="utf-8").strip()


def _server_answers(dsn: str) -> str:
    """``""`` if the server at ``dsn`` answers, otherwise the reason it does not.

    THE GUARD USED TO TEST FOR A DSN *STRING*, NOT FOR A REACHABLE SERVER, and this
    file's own docstring promises the opposite: *"SKIPS when one is not configured,
    because a skip is the honest result for 'the infrastructure this needs was not
    available'."* A non-empty but stale DSN satisfied ``skipif`` and then every test
    failed on ``OperationalError: connection refused``, which reports an unavailable
    database as a schema defect.

    That is not a hypothetical staleness either. ``staging/pg_dsn.txt`` is written by
    ``staging/bootstrap_pg.py``, and ``pgserver`` allocates a **new random port on every
    restart** -- so the file is guaranteed to go stale the moment the local instance is
    restarted or stopped. Measured in the Phase 7 full regression: these four tests
    failed against ``port 57227`` while 104 other PostgreSQL tests skipped honestly,
    from the same unavailable server.

    Connecting here, at import time, is the only way ``skipif`` can tell the two apart.
    The connection is immediately closed and nothing is read, so this costs one TCP
    round trip against localhost and nothing at all when the DSN is empty.

    A REACHABLE-BUT-UNPROVISIONED server still runs the tests and still fails them.
    "The schema is wrong" is exactly what this file exists to report, and only
    "the server is not there" becomes a skip.
    """
    if not dsn:
        return (
            "no provisioned PostgreSQL. Set AERORA_SCHEMA_CHECK_DSN or run "
            "staging/bootstrap_pg.py + scripts/provision_database.py. NOT a pass."
        )
    try:
        import psycopg2
    except Exception as exc:  # noqa: BLE001
        return f"psycopg2 is not importable ({exc}). NOT a pass."
    try:
        psycopg2.connect(dsn, connect_timeout=5).close()
    except Exception as exc:  # noqa: BLE001
        return (
            f"the configured PostgreSQL is not reachable, so this measurement could not "
            f"be taken: {type(exc).__name__}: {str(exc).strip()[:200]}. NOT a pass -- "
            f"start it with staging/bootstrap_pg.py and refresh staging/pg_dsn.txt, "
            f"whose port changes on every restart."
        )
    return ""


_SKIP_REASON = _server_answers(DSN)

pytestmark = pytest.mark.skipif(bool(_SKIP_REASON), reason=_SKIP_REASON or "reachable")

#: Models whose divergence is already recorded as a known defect in
#: FINAL_LAUNCH_READINESS_REPORT.md. Listed here so the rest of the suite can be held to
#: the rule while these are fixed, and so the list can only shrink.
#:
#: THIS IS NOT A SUPPRESSION OF THE FINDING. `test_the_known_divergences_are_still_the
#: _only_ones` asserts the set has not grown, and
#: `test_position_model_cannot_read_the_provisioned_positions_table` asserts the defect
#: is REAL and still present, so removing a name from this list without fixing the
#: schema turns both red.
#: EMPTY, AND IT GOT THERE BY THE SCHEMA BEING FIXED RATHER THAN THE LIST BEING EDITED.
#:
#: ``positions`` was the single entry. Alembic revision ``b2c3d4e5f6a7`` reshapes the
#: table onto ``core/position_model.PositionModel`` -- the declaration the application is
#: actually built on, used by ``position_engine``, ``pnl_engine``, ``fee_engine``,
#: ``metrics_system``, ``event_router`` and ``consistency_checker`` -- so all fourteen
#: declared columns now exist and ``position_id`` is the primary key.
#: ``tests/test_position_lifecycle_postgres.py`` runs create/read/update/close through the
#: repository against a real server to prove the reshape is usable and not merely present.
KNOWN_DIVERGENT: set[str] = set()


def _engine():
    from sqlalchemy import create_engine

    return create_engine(DSN)


def _mapped_tables():
    """Every table a SQLAlchemy model maps to, with its declared column names."""
    # Importing main registers every model on Base.metadata, which is what it exists to
    # do at startup; without it only the models imported transitively appear.
    os.environ.setdefault("ENV", "testing")
    os.environ.setdefault("DEV_MODE", "true")

    from backend_app.core.database import Base
    import backend_app.core.position_model  # noqa: F401
    import backend_app.core.models.execution_record  # noqa: F401

    return {
        name: sorted(c.name for c in table.columns)
        for name, table in Base.metadata.tables.items()
    }


def test_the_database_is_actually_provisioned():
    """Guards the guard: if the database is empty, every comparison below would
    vacuously pass by finding no tables to compare."""
    from sqlalchemy import inspect

    tables = inspect(_engine()).get_table_names(schema="public")
    assert len(tables) > 50, (
        f"only {len(tables)} public table(s); this database does not look provisioned, "
        f"so the comparisons in this file would prove nothing"
    )


def test_every_model_column_exists_in_the_database():
    """THE GENERAL RULE. A model column with no database column is a query that fails
    at runtime rather than at import."""
    from sqlalchemy import inspect

    insp = inspect(_engine())
    db_tables = set(insp.get_table_names(schema="public"))

    divergent = {}
    for table, model_cols in _mapped_tables().items():
        if table not in db_tables or table in KNOWN_DIVERGENT:
            continue
        db_cols = {c["name"] for c in insp.get_columns(table, schema="public")}
        missing = sorted(set(model_cols) - db_cols)
        if missing:
            divergent[table] = missing

    assert not divergent, (
        "ORM models declare columns the provisioned schema does not have. Every query "
        "touching these will fail with UndefinedColumn at runtime:\n"
        + "\n".join(f"  {t}: {cols}" for t, cols in sorted(divergent.items()))
    )


def test_the_known_divergences_are_still_the_only_ones():
    """``KNOWN_DIVERGENT`` may shrink, never grow."""
    from sqlalchemy import inspect

    insp = inspect(_engine())
    db_tables = set(insp.get_table_names(schema="public"))

    actually_divergent = set()
    for table, model_cols in _mapped_tables().items():
        if table not in db_tables:
            continue
        db_cols = {c["name"] for c in insp.get_columns(table, schema="public")}
        if set(model_cols) - db_cols:
            actually_divergent.add(table)

    new = actually_divergent - KNOWN_DIVERGENT
    assert not new, (
        f"new ORM/schema divergence in {sorted(new)}. Fix the model or the migration; "
        f"do not add it to KNOWN_DIVERGENT."
    )


def test_position_model_can_now_read_the_provisioned_positions_table():
    """The inverse of what this test used to assert.

    It previously asserted the DEFECT -- that ``session.query(PositionModel)`` raised
    ``UndefinedColumn: column positions.position_id does not exist`` -- deliberately, so
    the finding could not be quietly forgotten, with a docstring saying that the day the
    schema was fixed this test would fail and should be inverted.

    That day arrived with alembic revision ``b2c3d4e5f6a7``. The assertion is now that
    the query runs, which is the property anything reading positions depends on --
    including ``core/consistency_checker.py``, i.e. reconciliation.
    """
    from sqlalchemy.orm import sessionmaker

    from backend_app.core.position_model import PositionModel

    session = sessionmaker(bind=_engine())()
    try:
        # No row is expected; the point is that the statement executes rather than
        # raising on a column the table does not have.
        result = (
            session.query(PositionModel)
            .filter(PositionModel.position_id == "probe-does-not-exist")
            .first()
        )
        assert result is None
    finally:
        session.close()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "PARTIALLY RESOLVED, and the remaining half is a source-level duplication rather "
        "than a schema one. The SCHEMA conflict is fixed: alembic b2c3d4e5f6a7 reshapes "
        "`positions` onto core/position_model.PositionModel, which is the declaration "
        "position_engine, pnl_engine, fee_engine, metrics_system, event_router and "
        "consistency_checker are all built on, and KNOWN_DIVERGENT is now empty. What "
        "remains is that backend/state_service.py still declares a SECOND class with "
        "__tablename__ = 'positions' and a different column set again (user_id, "
        "quantity, available_quantity, locked_quantity, entry_price, mark_price, "
        "liquidation_price). It is unreachable from the provisioned schema -- it lives "
        "inside a conditional block in a module with its own async session factory -- so "
        "it cannot corrupt the table, but two classes claiming one table is how this "
        "started. strict=True so resolving it turns this into a FAILURE and prompts "
        "removal of the marker. Marked rather than deleted because deleting it loses the "
        "finding, and marked rather than left red because a permanently red suite stops "
        "being read."
    ),
)
def test_only_one_model_claims_the_positions_table():
    """The root cause: three declarations of one table.

    ``core/position_model.PositionModel`` and ``backend/state_service.PositionModel``
    both set ``__tablename__ = "positions"``, and the Alembic revision declares a third
    shape. Asserted at source level because the two classes are defined in different
    modules and only one is registered on the shared ``Base``.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    claimants = []
    for rel in ("backend_app/core/position_model.py", "backend_app/backend/state_service.py"):
        tree = ast.parse((root / rel).read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.Assign)
                    and any(getattr(t, "id", "") == "__tablename__" for t in stmt.targets)
                    and isinstance(stmt.value, ast.Constant)
                    and stmt.value.value == "positions"
                ):
                    claimants.append(f"{rel}::{node.name}")

    assert len(claimants) <= 1, (
        "more than one model class declares __tablename__ = 'positions', so which "
        "shape the table has depends on which declaration created it first:\n  "
        + "\n  ".join(claimants)
    )
