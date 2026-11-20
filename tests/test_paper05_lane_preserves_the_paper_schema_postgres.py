"""PAPER-05 -- the paper schema must survive every file that runs before PAPER-05 in the lane.

WHAT THIS FILE EXISTS TO PREVENT, AND WHY A SKIP WAS HIDING IT
---------------------------------------------------------------
``tests/test_paper05_apply_fill_atomic_postgres.py`` proves PAPER-05's actual property --
that ``paper_apply_fill_atomic`` admits exactly one of N concurrent deliveries of the same
``fill_event_id`` and that a loser leaves nothing behind. It is gated on a real server, and
in the ``provisioning`` lane all 10 of its tests SKIPPED, reporting that the ``paper_*``
tables were absent from a database the same job had provisioned them into. The ledger
recorded that contradiction as not root-caused.

The cause was test ordering against a shared database. The lane runs every
PostgreSQL-gated file in ONE pytest invocation, and
``tests/test_paper03_write_service_role_only_postgres.py`` is listed immediately before
PAPER-05's file. PAPER-03 needs mirrors named ``public.paper_*`` exactly (it applies
migration 043 verbatim, and 043 names those tables literally), and it used to take those
names by ``DROP TABLE ... CASCADE``-ing whatever was already there, on the way in and again
on the way out, without ever restoring it. So PAPER-03 deleted the real paper schema, and
PAPER-05's own ``_require_migration_048`` fixture then correctly reported it missing and
skipped. A skip is not a failure, so a green lane hid it.

PAPER-03 now moves the real tables into a stash schema and moves them back in its
teardown. This file is the regression test for that, and it is deliberately BEHAVIOURAL
rather than a source-text check: it runs the real PAPER-03 file as a subprocess, the same
way ``tests/test_ops02_rollback_keeps_load_balancer_attached.py`` runs the real
``infra/rollback.sh``, and then asks the database whether the real tables are still there.
A source-level assertion that PAPER-03 "calls ``_stash_real_tables``" would pass even if
the stash logic were broken.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

_PG_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")
_HAS_PG = _PG_URL.startswith("postgres")

pg_only = pytest.mark.skipif(
    not _HAS_PG,
    reason=(
        "requires PostgreSQL: set AERORA_TEST_DATABASE_URL to a postgres DSN. A SKIP here "
        "is NOT a pass -- it is the same shape of skip that hid the defect this file is about."
    ),
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PAPER03 = _REPO_ROOT / "tests" / "test_paper03_write_service_role_only_postgres.py"

# The eleven PAPER-03 borrows. The two `*_allowed_transitions` reference tables are
# deliberately not in this list: PAPER-03 does not touch them.
_BORROWED = [
    "paper_sessions", "paper_accounts", "paper_orders", "paper_positions", "paper_metrics",
    "paper_fills", "paper_balance_events", "paper_trades",
    "paper_equity_snapshots", "paper_events", "paper_market_events",
]

# The three PAPER-05's own gate probes. If these survive, PAPER-05 runs instead of skipping.
_PAPER05_GATE = ["paper_accounts", "paper_orders", "paper_fills"]


def _present(tables: list[str]) -> set[str]:
    conn = psycopg2.connect(_PG_URL)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                " WHERE table_schema = 'public' AND table_name = ANY(%s)",
                (tables,),
            )
            return {r[0] for r in cur.fetchall()}
    finally:
        conn.close()


@pytest.fixture(scope="module")
def paper03_has_run():
    """Run the real PAPER-03 file to completion as a subprocess, once.

    Skips rather than fails if the real paper schema is not present to begin with: this
    file's claim is that PAPER-03 PRESERVES the schema, and there is nothing to preserve on
    a database that never had it. Saying so is the point -- it is exactly the distinction
    between "verified" and "did not run" that the original skip blurred.
    """
    before = _present(_BORROWED)
    if not before:
        pytest.skip(
            "the real paper_* tables are not present on this database, so there is nothing "
            "for PAPER-03 to preserve; provision the schema first "
            "(scripts/provision_database.py --apply --create-substrate)"
        )

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(_PAPER03), "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    return before, proc


@pg_only
def test_paper03_actually_ran_rather_than_skipping(paper03_has_run):
    """Guard on the guard: if PAPER-03 itself skipped, it never borrowed anything, and
    every assertion below would pass for the wrong reason."""
    _before, proc = paper03_has_run
    tail = (proc.stdout or "")[-3000:] + (proc.stderr or "")[-2000:]
    assert " passed" in tail, (
        "PAPER-03 did not report any passing test, so this file cannot tell whether the "
        f"stash/restore cycle works. pytest said:\n{tail}"
    )


@pg_only
def test_the_real_paper_tables_survive_paper03(paper03_has_run):
    """The defect itself: every table PAPER-03 borrows must still exist afterwards."""
    before, proc = paper03_has_run
    after = _present(_BORROWED)
    destroyed = sorted(before - after)
    assert not destroyed, (
        "PAPER-03 destroyed real paper tables and did not restore them: "
        f"{destroyed}. Any paper-schema test running later in the same pytest invocation "
        "will now skip or fail for a reason that has nothing to do with its own subject -- "
        "which is exactly how PAPER-05's 10 atomicity tests silently skipped in a green "
        f"provisioning lane.\npytest output tail:\n{(proc.stdout or '')[-1500:]}"
    )


@pg_only
def test_paper05s_own_gate_would_now_open(paper03_has_run):
    """Stated in PAPER-05's own terms: the three tables its ``_require_migration_048``
    fixture probes with ``to_regclass`` must be present after PAPER-03 has run, because
    that probe returning false is the precise mechanism of the recorded skip."""
    _before, _proc = paper03_has_run
    missing = sorted(set(_PAPER05_GATE) - _present(_PAPER05_GATE))
    assert not missing, (
        f"PAPER-05's gate would still refuse: {missing} absent after PAPER-03 ran. "
        "Its 10 atomicity tests would skip, and PAPER-05's atomicity would stay unproven."
    )


@pg_only
def test_paper03_leaves_no_stash_schema_behind(paper03_has_run):
    """The restore must be complete, not partial: a leftover stash schema means tables were
    moved aside and never moved back."""
    _before, _proc = paper03_has_run
    conn = psycopg2.connect(_PG_URL)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                " WHERE table_schema = 'paper03_stash' ORDER BY table_name"
            )
            left = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()
    assert not left, f"paper03_stash still holds tables that were never moved back: {left}"
