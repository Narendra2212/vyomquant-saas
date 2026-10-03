"""
tests/test_strategy_lifecycle_concurrency_postgres.py

production-launch-hardening task 13.23 — Requirement 1.16 [P0] / 2.16, the
DATABASE-LEVEL half that tasks 12.6, 13.11, 13.21 and 13.22 carried BLOCKED.

    1.16 [P0][UNVERIFIED] WHEN start, stop, delete and deploy are issued
    concurrently against one strategy, THEN no test establishes the outcome, so a
    strategy that is deleted while running, or deployed twice, cannot be ruled out.

    2.16 [P0] WHEN start, stop, delete and deploy race on one strategy, THEN the
    system SHALL serialise them so that no strategy ends running-but-deleted or
    deployed twice.

WHAT THIS FILE ADDS THAT ``tests/test_strategy_lifecycle_concurrency.py`` CANNOT
----------------------------------------------------------------------------------
That file drives a single-process ``dict``-backed fake and proves the
APPLICATION-level absence: 21/40 interleavings land running-but-deleted, 12/12
land a double deploy. Its own module docstring states, correctly, that the
DATABASE-level half needs a real server and is BLOCKED without one.

This file is that server. Two REAL PostgreSQL sessions, released together off a
``threading.Barrier``, each running one operation's own read-decide-write — the
technique task 13.11 used for order cancellation. No fake, no monkeypatch, no
single-process illusion of concurrency.

SELF-PROVISIONING, AND WHY THAT IS THE RIGHT CHOICE RATHER THAN A SHORTCUT
----------------------------------------------------------------------------------
Every race below runs inside a uniquely named scratch schema this file creates
and drops. It does NOT read ``public``. Three consequences, all deliberate:

* It runs on a bare ``postgres:17-alpine`` service container with **no schema
  provisioning step at all**, which is why the CI wiring for it is one line.
* It cannot be affected by, and cannot affect, production or any shared schema.
* The two tables it builds are declared **from source**, not from memory:
  :func:`_declared_columns` parses the real declarations —
  ``020_declare_pre_existing_tables.sql`` for ``strategies`` and
  ``001_strategy_architecture.sql`` for ``strategy_deployments`` — and
  :func:`test_both_tables_are_declared_in_the_repository` fails if either
  declaration disappears. Before task 13.22, ``strategies`` was declared by
  **nothing**, so this file could not have been written this way: there was no
  source to build the scratch world from. That is precisely the unlock 13.22
  provided, used here rather than only claimed.

WHAT WAS ACTUALLY MEASURED (PostgreSQL 17.6, session mode, port 5432)
----------------------------------------------------------------------------------
Six races. The results are the findings, and three of them are negative:

  R1  two deploys, READ COMMITTED        -> both COMMIT, 2 live   VIOLATION
  R2  archive vs deploy, READ COMMITTED  -> both COMMIT, archived + 1 live  VIOLATION
  R3  two deploys, SERIALIZABLE          -> both COMMIT, 2 live   VIOLATION
  R4  archive vs deploy, SERIALIZABLE    -> archiver gets 40001   legal
  R5  two deploys + partial unique index -> loser gets 23505      legal
  R6  archive vs deploy, atomic predicate-> both COMMIT           VIOLATION

R3 is the load-bearing negative: **SERIALIZABLE does not prevent the double
deploy.** Two INSERTs of two different rows give SSI no read/write dependency
cycle to cancel, because neither write falsifies a predicate the other read. So
the isolation control task 13.21 built — ``core/db_isolation.isolated_session``,
now operative and verified — is **not** the mechanism for this half, and R3 is
the measurement that says so rather than an assumption.

R6 is the second load-bearing negative: **the atomic ``UPDATE … WHERE`` pattern
task 13.11 proved for order cancellation does not transfer.** That pattern works
because cancellation's predicate and its write are the same row: ``UPDATE orders
SET status='cancelled' WHERE id=? AND status='pending'`` either matches or
reports ``rowcount == 0`` (`backend/transactional_execution_manager.py:863`).
Here the predicate is on a *different table* from the write, so under READ
COMMITTED the archiver's ``NOT EXISTS (SELECT … FROM strategy_deployments …)``
takes its snapshot before the concurrent INSERT commits, sees nothing, and the
UPDATE succeeds. Classic write skew. ``rowcount`` is 1, not 0, so there is
nothing for the caller to raise on.

R4 and R5 are the two that hold, and they do not hold symmetrically:

* R5 is a **fix, landed**: ``021_strategy_deployment_live_uniqueness.sql``
  creates the partial unique index, and the losing INSERT fails with ``23505``.
* R4 is a **mechanism that works at a price not paid here**: SERIALIZABLE does
  catch the cross-table write skew, with ``40001 could not serialize access due
  to read/write dependencies among transactions`` / "Canceled on identification
  as a pivot, during commit attempt". But task 13.21 established that only
  ``backend/paper/paper_repository.py`` retries on ``40001``, so routing the
  archive path through SERIALIZABLE without a retry converts a silent race into
  a user-visible 500. Recorded, not adopted. Task 13.23 reports it as a
  decision.

SO WHERE CLAUSE 1.16 STANDS, STATED PLAINLY
----------------------------------------------------------------------------------
**PROVEN AS A DEFECT**, at both levels, and now half fixed:

* "deployed twice" — proven (R1, R3) and **FIXED** at the root cause by 021's
  partial unique index, with R5 as the regression proof.
* "deleted while running" — proven (R2) and **NOT FIXED**. It is a two-table
  invariant; no index can span tables, the atomic predicate is measured
  insufficient (R6), and SERIALIZABLE needs a retry layer that does not exist.
  Carried as a proven P0 with this file as its reproduction.

HOW TO RUN IT
----------------------------------------------------------------------------------
Skips unless ``DATABASE_URL`` names a PostgreSQL server. In CI that is the
``database-tests`` job of ``.github/workflows/01-pr-check.yml``; locally, export
a DSN. It never touches ``public`` and drops its scratch schema in a ``finally``.
"""

from __future__ import annotations

import os
import re
import threading
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO_ROOT / "backend_app" / "migrations"

#: The live binding states, and every ``status`` spelling that maps onto one.
#: Imported from the module that owns the vocabulary rather than restated, so the
#: index predicate in 021 is pinned against the code's own mapping.
from backend_app.backend.strategy_lifecycle import (  # noqa: E402
    STOPPABLE_BINDING_STATES,
    _STATUS_TO_BINDING_STATE,
)

LIVE_STATUS_SPELLINGS: Tuple[str, ...] = tuple(
    sorted(
        spelling
        for spelling, state in _STATUS_TO_BINDING_STATE.items()
        if state in STOPPABLE_BINDING_STATES
    )
)

#: The index 021 declares. Named here once so every assertion agrees.
LIVE_UNIQUE_INDEX = "uq_strategy_deployments_one_live"


# ══════════════════════════════════════════════════════════════════════════
# SOURCE-LEVEL: the two declarations this file's scratch world is built from.
# These run with no database and are the part that pins task 13.22's unlock.
# ══════════════════════════════════════════════════════════════════════════


def _declared_columns(sql_file: Path, table: str) -> List[str]:
    """Column names from ``CREATE TABLE [IF NOT EXISTS] [public.]<table> (...)``.

    Deliberately crude — it only has to establish that a declaration EXISTS and
    which columns it names. ``tests/test_schema_table_reference_drift.py`` owns
    full-fidelity parsing of these files.
    """
    text = sql_file.read_text(encoding="utf-8")
    match = re.search(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?" + table + r"\s*\(",
        text,
        re.IGNORECASE,
    )
    if not match:
        return []
    depth, start = 1, match.end()
    index = start
    while index < len(text) and depth:
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
        index += 1
    body = text[start : index - 1]
    columns: List[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        token = stripped.split()[0].strip(",").strip('"')
        if token.upper() in {
            "PRIMARY",
            "UNIQUE",
            "FOREIGN",
            "CHECK",
            "CONSTRAINT",
            "EXCLUDE",
        }:
            continue
        if token and token not in columns:
            columns.append(token)
    return columns


def test_both_tables_are_declared_in_the_repository() -> None:
    """``strategies`` from 020, ``strategy_deployments`` from 001.

    **Validates: Requirements 1.16**

    This is task 13.22's unlock expressed as an assertion. Before 020,
    ``strategies`` had no ``CREATE TABLE`` anywhere in the tree — 13.21 counted
    zero — so the two tables this file races against could not be built from
    source at all. If either declaration is removed, this file's scratch world
    stops being derived from the repository and the failure says so here rather
    than silently downgrading into invented DDL.
    """
    strategies = _declared_columns(
        MIGRATIONS / "020_declare_pre_existing_tables.sql", "strategies"
    )
    deployments = _declared_columns(
        MIGRATIONS / "001_strategy_architecture.sql", "strategy_deployments"
    )

    assert strategies, (
        "public.strategies is no longer declared by "
        "020_declare_pre_existing_tables.sql. Task 13.22 added that declaration and "
        "task 13.23's database-level proof of clause 1.16 is built on it."
    )
    assert deployments, (
        "public.strategy_deployments is no longer declared by "
        "001_strategy_architecture.sql."
    )
    for column in ("id", "user_id", "archived_at"):
        assert column in strategies, (
            f"strategies.{column} is not in 020's declaration; the race needs it. "
            f"Declared: {strategies}"
        )
    for column in ("id", "strategy_id", "user_id", "status"):
        assert column in deployments, (
            f"strategy_deployments.{column} is not in 001's declaration; the race "
            f"needs it. Declared: {deployments}"
        )


def test_the_index_predicate_covers_every_live_status_spelling() -> None:
    """021's ``WHERE status IN (...)`` must name all nine live spellings.

    **Validates: Requirements 2.16**

    ``_STATUS_TO_BINDING_STATE`` maps nine distinct spellings onto DEPLOYING,
    RUNNING or PAUSED. A predicate naming only the three canonical ones would
    leave six legacy spellings outside the unique index, so a writer using any of
    them could still land a second live deployment and the guard would look
    present while being bypassable. This test is the reason the predicate is
    written out in full.
    """
    sql = (MIGRATIONS / "021_strategy_deployment_live_uniqueness.sql").read_text(
        encoding="utf-8"
    )
    match = re.search(
        r"CREATE\s+UNIQUE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+"
        + LIVE_UNIQUE_INDEX
        + r".*?WHERE\s+status\s+IN\s*\((.*?)\)\s*;",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    assert match, f"021 does not declare {LIVE_UNIQUE_INDEX} with a status predicate"
    predicate = set(re.findall(r"'([a-z_]+)'", match.group(1)))

    assert LIVE_STATUS_SPELLINGS, (
        "strategy_lifecycle._STATUS_TO_BINDING_STATE yielded no live spellings — this "
        "assertion would be vacuous"
    )
    missing = set(LIVE_STATUS_SPELLINGS) - predicate
    assert not missing, (
        f"021's index predicate omits live status spelling(s) {sorted(missing)}. "
        f"strategy_lifecycle maps them to {sorted(STOPPABLE_BINDING_STATES)}, so a "
        f"deployment written with one of them is live and must be covered. "
        f"Predicate has {sorted(predicate)}."
    )
    extra = predicate - set(LIVE_STATUS_SPELLINGS)
    assert not extra, (
        f"021's index predicate names {sorted(extra)}, which strategy_lifecycle does "
        f"NOT map to a live state. Constraining a terminal status would stop a "
        f"strategy accumulating finished deployments, which is normal."
    )


# ══════════════════════════════════════════════════════════════════════════
# THE REAL-DATABASE HARNESS
# ══════════════════════════════════════════════════════════════════════════

_DSN = os.environ.get("DATABASE_URL", "")
_IS_POSTGRES = _DSN.startswith("postgresql://") or _DSN.startswith("postgres://")

try:  # pragma: no cover - import shape, not logic
    import psycopg2
    import psycopg2.errors
except ImportError:  # pragma: no cover
    psycopg2 = None  # type: ignore[assignment]

#: Applied to the six races INDIVIDUALLY and deliberately **not** as a module-level
#: ``pytestmark``. The two source-level tests above need no database and must run in
#: the main ``unit-tests`` lane, where ``DATABASE_URL`` is blank by design (task
#: 13.21). A module-level skip would take them with it and the 021/`_STATUS_TO_
#: BINDING_STATE` drift guard would then only ever run in the one job that has a
#: server — which is how a guard quietly stops guarding.
requires_postgres = pytest.mark.skipif(
    not _IS_POSTGRES or psycopg2 is None,
    reason=(
        "DATABASE_URL does not name a PostgreSQL server (or psycopg2 is absent). The "
        "database-level half of clause 1.16 cannot be proved against SQLite: it is "
        "about what two concurrent sessions commit. Runs in the database-tests job "
        "of 01-pr-check.yml."
    ),
)

LIVE_SQL_LIST = ", ".join(f"'{s}'" for s in LIVE_STATUS_SPELLINGS)


class _ScratchWorld:
    """One scratch schema with ``strategies`` + ``strategy_deployments``.

    ``public`` is never read or written. The schema name is unique per instance
    and dropped in :meth:`drop`, which every fixture calls in a ``finally``.
    """

    def __init__(self) -> None:
        self.schema = "vq_1323_" + uuid.uuid4().hex[:10]
        self.strategy_id = str(uuid.uuid4())
        self.user_id = str(uuid.uuid4())

    def connect(self):
        return psycopg2.connect(_DSN, connect_timeout=30)

    def create(self) -> None:
        conn = self.connect()
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
                cur.execute(f"CREATE SCHEMA {self.schema}")
                # Shapes taken from the declarations the source-level tests above
                # pin, narrowed to the columns the race needs.
                cur.execute(
                    f"""
                    CREATE TABLE {self.schema}.strategies (
                        id uuid PRIMARY KEY,
                        user_id uuid NOT NULL,
                        status text DEFAULT 'stopped'::text,
                        archived_at timestamp with time zone
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE {self.schema}.strategy_deployments (
                        id uuid PRIMARY KEY,
                        strategy_id uuid NOT NULL
                            REFERENCES {self.schema}.strategies(id) ON DELETE CASCADE,
                        user_id uuid NOT NULL,
                        status character varying NOT NULL DEFAULT 'deploying'
                    )
                    """
                )
        finally:
            conn.close()

    def seed(self, *, with_live_unique_index: bool) -> None:
        conn = self.connect()
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {self.schema}.strategy_deployments")
                cur.execute(f"DELETE FROM {self.schema}.strategies")
                cur.execute(f"DROP INDEX IF EXISTS {self.schema}.{LIVE_UNIQUE_INDEX}")
                if with_live_unique_index:
                    # 021's index, verbatim in shape, against the scratch table.
                    cur.execute(
                        f"CREATE UNIQUE INDEX {LIVE_UNIQUE_INDEX} "
                        f"ON {self.schema}.strategy_deployments (strategy_id) "
                        f"WHERE status IN ({LIVE_SQL_LIST})"
                    )
                cur.execute(
                    f"INSERT INTO {self.schema}.strategies (id, user_id, status) "
                    f"VALUES (%s, %s, 'stopped')",
                    (self.strategy_id, self.user_id),
                )
        finally:
            conn.close()

    def observe(self) -> Tuple[Optional[object], List[Tuple[str, str]]]:
        conn = self.connect()
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT archived_at FROM {self.schema}.strategies WHERE id = %s",
                    (self.strategy_id,),
                )
                row = cur.fetchone()
                archived = row[0] if row else None
                cur.execute(
                    f"SELECT id, status FROM {self.schema}.strategy_deployments "
                    f"WHERE strategy_id = %s ORDER BY id",
                    (self.strategy_id,),
                )
                rows = [(str(r[0]), r[1]) for r in cur.fetchall()]
            return archived, rows
        finally:
            conn.close()

    def drop(self) -> None:
        conn = self.connect()
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
        finally:
            conn.close()

    def race(
        self,
        body_a: Callable,
        body_b: Callable,
        *,
        isolation: str,
        with_live_unique_index: bool = False,
    ) -> Dict[str, object]:
        """Run two bodies in two real sessions, released together by a Barrier.

        A second Barrier (``mid``) is handed to each body so it can park between
        its own read and its own write — the window the missing re-check lives
        in. Both are genuine OS threads on genuine separate server backends, so
        this is not the single-process interleaving the fake-database file is
        limited to.
        """
        self.seed(with_live_unique_index=with_live_unique_index)
        start = threading.Barrier(2)
        mid = threading.Barrier(2)
        outcomes: Dict[str, str] = {}

        def run(name: str, body: Callable) -> None:
            conn = self.connect()
            try:
                conn.set_session(isolation_level=isolation, autocommit=False)
                with conn.cursor() as cur:
                    cur.execute(f"SET LOCAL search_path TO {self.schema}")
                    start.wait(timeout=60)
                    body(cur, mid, self)
                conn.commit()
                outcomes[name] = "COMMITTED"
            except Exception as exc:  # noqa: BLE001 - the outcome IS the measurement
                outcomes[name] = f"{type(exc).__name__}:{getattr(exc, 'pgcode', None)}"
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
            finally:
                conn.close()

        threads = [
            threading.Thread(target=run, args=("A", body_a)),
            threading.Thread(target=run, args=("B", body_b)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
            assert not thread.is_alive(), "a racing session did not finish in 120s"

        archived, deployments = self.observe()
        live = [d for d in deployments if d[1] in LIVE_STATUS_SPELLINGS]
        return {
            "outcomes": outcomes,
            "archived": archived,
            "deployments": deployments,
            "live": live,
        }


@pytest.fixture
def world():
    scratch = _ScratchWorld()
    scratch.create()
    try:
        yield scratch
    finally:
        scratch.drop()


# --- the operation bodies, each mirroring what the application really does ---


def _deploy_body(cur, mid, w: _ScratchWorld) -> None:
    """``StrategyService.deploy_version``: read ``archived_at`` once, early
    (``_load_deployable_version`` -> ``assert_strategy_not_archived``), then
    several awaits later INSERT, with nothing re-read in between."""
    cur.execute("SELECT archived_at FROM strategies WHERE id = %s", (w.strategy_id,))
    if cur.fetchone()[0] is not None:
        raise RuntimeError("deploy refused: strategy archived")
    mid.wait(timeout=60)
    cur.execute(
        "INSERT INTO strategy_deployments (id, strategy_id, user_id, status) "
        "VALUES (%s, %s, %s, 'running')",
        (str(uuid.uuid4()), w.strategy_id, w.user_id),
    )


def _archive_body(cur, mid, w: _ScratchWorld) -> None:
    """``strategy_archive.archive_strategy``: read the deployments to decide
    whether any is blocking, then ``UPDATE … WHERE id = ? AND user_id = ?`` with
    no predicate re-asserting that decision."""
    cur.execute(
        "SELECT id, status FROM strategy_deployments WHERE strategy_id = %s",
        (w.strategy_id,),
    )
    blocking = [r for r in cur.fetchall() if r[1] in LIVE_STATUS_SPELLINGS]
    mid.wait(timeout=60)
    if blocking:
        raise RuntimeError(f"archive refused by the application gate: {blocking}")
    cur.execute(
        "UPDATE strategies SET archived_at = now() WHERE id = %s AND user_id = %s",
        (w.strategy_id, w.user_id),
    )


def _archive_body_atomic(cur, mid, w: _ScratchWorld) -> None:
    """The atomic ``UPDATE … WHERE`` pattern task 13.11 proved for order
    cancellation, transplanted here: the blocking condition moved into the
    write's own predicate, raising on ``rowcount == 0``."""
    cur.execute(
        "SELECT id, status FROM strategy_deployments WHERE strategy_id = %s",
        (w.strategy_id,),
    )
    cur.fetchall()
    mid.wait(timeout=60)
    cur.execute(
        "UPDATE strategies SET archived_at = now() "
        "WHERE id = %s AND user_id = %s AND archived_at IS NULL "
        f"  AND NOT EXISTS (SELECT 1 FROM strategy_deployments d "
        f"                  WHERE d.strategy_id = strategies.id "
        f"                    AND d.status IN ({LIVE_SQL_LIST}))",
        (w.strategy_id, w.user_id),
    )
    if cur.rowcount == 0:
        raise RuntimeError("archive refused at write time: rowcount == 0")


# ══════════════════════════════════════════════════════════════════════════
# R1 / R2 — the defect, at the database level. Both EXPECTED TO SHOW THE
# VIOLATION: these assert that the race reproduces, which is what makes them
# the proof of clause 1.16 rather than a hope that it holds.
# ══════════════════════════════════════════════════════════════════════════


@requires_postgres
def test_two_real_sessions_both_land_a_live_deployment(world) -> None:
    """Two concurrent deploys, READ COMMITTED, no index: both commit.

    **Validates: Requirements 1.16**

    This is the database-level counterpart of
    ``test_deploy_can_race_itself_into_a_double_deploy``'s 12/12. Asserted as a
    reproduction, deliberately: if this ever stops reproducing, the finding has
    changed and the record must change with it, so it must not be written as a
    property that merely happens to pass.
    """
    result = world.race(_deploy_body, _deploy_body, isolation="READ COMMITTED")
    assert result["outcomes"] == {"A": "COMMITTED", "B": "COMMITTED"}, result["outcomes"]
    assert len(result["live"]) == 2, (
        "clause 1.16's 'deployed twice' no longer reproduces at READ COMMITTED "
        f"without the index: {result}"
    )


@requires_postgres
def test_two_real_sessions_land_archived_and_still_running(world) -> None:
    """Archive races deploy, READ COMMITTED: both commit, strategy ends
    archived WITH a live deployment — running-but-deleted.

    **Validates: Requirements 1.16**

    The archiver reads the deployments, sees none (the INSERT has not committed),
    and writes ``archived_at``; the deployer read ``archived_at IS NULL`` before
    that write landed and INSERTs a ``running`` row. Neither sees the other.
    """
    result = world.race(_archive_body, _deploy_body, isolation="READ COMMITTED")
    assert result["outcomes"] == {"A": "COMMITTED", "B": "COMMITTED"}, result["outcomes"]
    assert result["archived"] is not None and len(result["live"]) == 1, (
        f"clause 1.16's 'deleted while running' no longer reproduces: {result}"
    )


# ══════════════════════════════════════════════════════════════════════════
# R3 / R6 — the two NEGATIVE findings: mechanisms that do NOT fix this.
# These are the reason 021 is an index and not an isolation level, and the
# reason the second half of 2.16 is reported as a decision rather than fixed.
# ══════════════════════════════════════════════════════════════════════════


@requires_postgres
def test_serializable_does_not_prevent_the_double_deploy(world) -> None:
    """SERIALIZABLE, two deploys: **still both commit**.

    **Validates: Requirements 2.16**

    The load-bearing negative. ``core/db_isolation.isolated_session`` (task
    13.21) is a working SERIALIZABLE control, and it would not have fixed this:
    two INSERTs of two different rows create no read/write dependency cycle for
    SSI to find. Measured, so that the mechanism choice in 021 is a conclusion
    rather than a preference.
    """
    result = world.race(_deploy_body, _deploy_body, isolation="SERIALIZABLE")
    assert result["outcomes"] == {"A": "COMMITTED", "B": "COMMITTED"}, result["outcomes"]
    assert len(result["live"]) == 2, (
        "SERIALIZABLE now prevents the double deploy, which contradicts task 13.23's "
        f"recorded measurement — re-examine before trusting either: {result}"
    )


@requires_postgres
def test_the_atomic_update_predicate_does_not_prevent_running_but_deleted(world) -> None:
    """The order-cancellation pattern, transplanted: **insufficient here**.

    **Validates: Requirements 2.16**

    ``UPDATE … WHERE id = ? AND status = ?`` works for cancellation because the
    predicate and the write are the same row
    (`backend/transactional_execution_manager.py:863`, task 13.11). Here the
    predicate reads a *different table*, so under READ COMMITTED the ``NOT
    EXISTS`` is evaluated against a snapshot taken before the concurrent INSERT
    commits. ``rowcount`` is 1, not 0 — there is nothing to raise on. This is why
    task 13.23 does not ship a fix for this half.
    """
    result = world.race(
        _archive_body_atomic,
        _deploy_body,
        isolation="READ COMMITTED",
        with_live_unique_index=True,
    )
    assert result["outcomes"] == {"A": "COMMITTED", "B": "COMMITTED"}, result["outcomes"]
    assert result["archived"] is not None and len(result["live"]) == 1, (
        "the atomic predicate now prevents running-but-deleted at READ COMMITTED, "
        f"which contradicts task 13.23's measurement: {result}"
    )


# ══════════════════════════════════════════════════════════════════════════
# R4 / R5 — what holds. R5 is the fix 021 lands; R4 is a mechanism that works
# at a price task 13.21 measured and this task does not pay.
# ══════════════════════════════════════════════════════════════════════════


@requires_postgres
def test_serializable_does_catch_running_but_deleted_but_raises_40001(world) -> None:
    """SERIALIZABLE, archive vs deploy: the archiver is cancelled with ``40001``.

    **Validates: Requirements 2.16**

    SSI does find this cycle — the cross-table write skew is exactly what it is
    for. The terminal state is legal. But the refusal arrives as
    ``serialization_failure``, and task 13.21 established that only
    ``backend/paper/paper_repository.py`` retries on ``40001``. Adopting this
    without a retry layer would turn a silent race into a user-visible 500, so it
    is recorded here as available and not wired in.
    """
    result = world.race(_archive_body, _deploy_body, isolation="SERIALIZABLE")
    outcomes = result["outcomes"]
    serialization_failures = [
        name for name, text in outcomes.items() if "40001" in str(text)
    ]
    assert serialization_failures, (
        "expected one session to be cancelled with 40001 serialization_failure under "
        f"SERIALIZABLE: {outcomes}"
    )
    assert not (result["archived"] is not None and result["live"]), (
        f"SERIALIZABLE still permitted running-but-deleted: {result}"
    )


@requires_postgres
def test_the_partial_unique_index_refuses_the_second_live_deployment(world) -> None:
    """021's index: the losing INSERT fails with ``23505 unique_violation``.

    **Validates: Requirements 2.16**

    The regression proof for the half this task fixes. One session commits, the
    other is refused by the database, and exactly one live deployment survives —
    at READ COMMITTED, with no isolation-level change and no application lock.
    """
    result = world.race(
        _deploy_body,
        _deploy_body,
        isolation="READ COMMITTED",
        with_live_unique_index=True,
    )
    outcomes = result["outcomes"]
    assert sorted(outcomes.values()) == sorted(
        ["COMMITTED", "UniqueViolation:23505"]
    ), (
        "expected exactly one COMMITTED and one 23505 unique_violation: " f"{outcomes}"
    )
    assert len(result["live"]) == 1, (
        f"the index must leave exactly one live deployment: {result}"
    )


@requires_postgres
def test_the_index_also_refuses_a_second_live_deployment_sequentially(world) -> None:
    """Not only under a race: a plain second INSERT is refused too.

    **Validates: Requirements 2.16**

    Guards against a false positive in the test above — if the index only ever
    fired because of the Barrier, it would not be a constraint. And it confirms
    the predicate lets a *terminal* deployment coexist, so the normal lifecycle
    (stop, then deploy again) still works.
    """
    world.seed(with_live_unique_index=True)
    conn = world.connect()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {world.schema}")
            cur.execute(
                "INSERT INTO strategy_deployments (id, strategy_id, user_id, status) "
                "VALUES (%s, %s, %s, 'running')",
                (str(uuid.uuid4()), world.strategy_id, world.user_id),
            )
            with pytest.raises(psycopg2.errors.UniqueViolation):
                cur.execute(
                    "INSERT INTO strategy_deployments "
                    "(id, strategy_id, user_id, status) VALUES (%s, %s, %s, 'deploying')",
                    (str(uuid.uuid4()), world.strategy_id, world.user_id),
                )
    finally:
        conn.close()

    # A stopped deployment does not occupy the slot: stop, then redeploy.
    conn = world.connect()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {world.schema}")
            cur.execute(
                "UPDATE strategy_deployments SET status = 'stopped' "
                "WHERE strategy_id = %s",
                (world.strategy_id,),
            )
            cur.execute(
                "INSERT INTO strategy_deployments (id, strategy_id, user_id, status) "
                "VALUES (%s, %s, %s, 'running')",
                (str(uuid.uuid4()), world.strategy_id, world.user_id),
            )
    finally:
        conn.close()

    _, deployments = world.observe()
    live = [d for d in deployments if d[1] in LIVE_STATUS_SPELLINGS]
    assert len(deployments) == 2 and len(live) == 1, (
        "a strategy must be able to carry finished deployments alongside one live "
        f"one: {deployments}"
    )
