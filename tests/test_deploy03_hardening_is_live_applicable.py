"""tests/test_deploy03_hardening_is_live_applicable.py — DEPLOY-03

`migrations/hardening/999_privilege_hardening.sql` could not be applied to a live
database. A dry run against production -- the whole file in a transaction, rolled back --
hit `statement timeout`, then `lock timeout`. The conclusion recorded at the time was
"it needs a maintenance window", and the privilege hardening sat unapplied because of it.

The diagnosis was wrong in a fixable way. `ALTER TABLE ... ENABLE ROW LEVEL SECURITY`
takes an `AccessExclusiveLock` **even when RLS is already enabled** -- PostgreSQL does
not check whether the change is a no-op before taking the lock -- and every one of this
file's RLS targets already has RLS on in any database provisioned even once. The file was
taking ten heavy locks to change nothing, and on a live API those contend.

Guarding each site with a `pg_class.relrowsecurity` test took the file from 14 heavy
locks to 4, and the remaining 4 are intentional policy replacements on quiet tables.

THIS FILE EXISTS BECAUSE THE GUARD IS INVISIBLE WHEN IT WORKS. An unguarded ALTER is
functionally identical on an empty database -- provisioning still passes, every other
hardening test still passes, and the only symptom is that applying to production starts
timing out again. Nothing else in the suite would notice.

The static checks need no database and so run everywhere. The measurement needs one and
skips without it; a SKIP here is not a pass.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HARDENING = REPO / "migrations" / "hardening" / "999_privilege_hardening.sql"
MIGRATION_074 = REPO / "backend_app" / "migrations" / "074_mkt10_revoke_backtest_dml.sql"

#: How many lines above an ALTER the guard may sit. The generated form puts the
#: `relrowsecurity` test 1-3 lines above; 5 is slack for reformatting, not for a
#: different construct.
_GUARD_WINDOW = 5

#: Tables whose lock comes from `DROP POLICY` / `CREATE POLICY`, which is a real change
#: and deliberately NOT guarded -- skipping it because a policy of that name exists would
#: silently keep a wrong predicate.
POLICY_DDL_TABLES = {
    "payment_methods",
    "creator_analytics",
    "marketplace_analytics",
    "library_ratings",
}


def _lines() -> list[str]:
    return HARDENING.read_text(encoding="utf-8").splitlines()


def _executable_enable_sites() -> list[tuple[int, str]]:
    """Every line that actually issues an RLS enable, excluding comments about one."""
    out = []
    for i, line in enumerate(_lines()):
        s = line.strip()
        if "ENABLE ROW LEVEL SECURITY" not in s:
            continue
        if s.startswith("--"):
            continue
        out.append((i + 1, s))
    return out


# ───────────────────────── static: every site is guarded ─────────────────────────


def test_the_file_still_enables_row_level_security_somewhere() -> None:
    """Guard against the lazy fix: deleting the ALTERs instead of guarding them.

    Removing them would also take the lock count to zero, and would silently stop the
    file from securing a freshly provisioned database -- the opposite of its purpose.
    """
    sites = _executable_enable_sites()
    assert len(sites) >= 10, (
        f"only {len(sites)} executable ENABLE ROW LEVEL SECURITY site(s) remain. The "
        "lock problem is solved by GUARDING these, not by removing them: a new database "
        "needs them to actually run."
    )


def test_every_rls_enable_is_guarded_by_a_catalog_check() -> None:
    """The regression that would silently make the file unapplicable to production again."""
    lines = _lines()
    unguarded = []
    for lineno, text in _executable_enable_sites():
        i = lineno - 1
        window = " ".join(lines[max(0, i - _GUARD_WINDOW):i])
        if "relrowsecurity" not in window:
            unguarded.append(f"line {lineno}: {text}")

    assert not unguarded, (
        "these ENABLE ROW LEVEL SECURITY statements have no relrowsecurity guard above "
        "them:\n  " + "\n  ".join(unguarded) + "\n\n"
        "ALTER TABLE takes an AccessExclusiveLock even when RLS is already enabled, and "
        "every target here already has it on in a provisioned database. Unguarded, this "
        "file goes back to timing out against a live cluster -- which is why the privilege "
        "hardening sat unapplied. See SECTION 0b."
    )


def test_each_guard_treats_an_unknown_table_as_not_enabled() -> None:
    """`COALESCE(..., false)`, so a missing pg_class row does not skip the ALTER silently.

    Without it, a NULL makes `IF NOT ... THEN` fall through and the table is left
    unsecured with nothing said. Treating unknown as "not enabled" makes the ALTER run
    and fail loudly.
    """
    text = HARDENING.read_text(encoding="utf-8")
    guards = re.findall(r"IF NOT (COALESCE\()?\(?SELECT c?\.?relrowsecurity", text)
    bare = [g for g in guards if not g]
    assert not bare, (
        f"{len(bare)} relrowsecurity guard(s) are not wrapped in COALESCE(..., false). A "
        "NULL from pg_class would skip the ALTER and leave the table unsecured silently."
    )


def test_section_0b_records_why_the_guards_are_there() -> None:
    """The measurement is the whole justification; losing it invites an innocent revert."""
    text = HARDENING.read_text(encoding="utf-8")
    assert "SECTION 0b" in text
    for phrase in ("AccessExclusiveLock", "EVEN WHEN RLS IS ALREADY ENABLED"):
        assert phrase in text, f"SECTION 0b no longer states: {phrase}"


def test_the_policy_replacements_are_named_as_deliberately_unguarded() -> None:
    """So the next reader does not "finish the job" by guarding them too.

    Guarding a policy replacement would keep a wrong predicate whenever a policy of that
    name already existed, which is a correctness regression dressed as a performance fix.
    """
    text = HARDENING.read_text(encoding="utf-8")
    for table in sorted(POLICY_DDL_TABLES):
        assert table in text, f"{table} is no longer named in the hardening file"
    assert "NOT guarded" in text or "not guarded" in text.lower(), (
        "SECTION 0b no longer records that the policy replacements are intentionally "
        "left unguarded"
    )


def test_074_no_longer_claims_revoke_waits_on_table_locks() -> None:
    """A measured correction to a claim this repository shipped.

    074 asserted REVOKE "still waits behind open transactions that hold the table".
    Measured: REVOKE takes no relation lock on the target table at all. The claim is
    removed and the measurement recorded, so the next person reasoning about lock
    contention starts from the right facts.
    """
    text = MIGRATION_074.read_text(encoding="utf-8")
    assert "waits behind open transactions that hold the table" not in text, (
        "074 still carries the unmeasured claim that REVOKE waits on a table lock"
    )
    assert "no relation lock on" in text, (
        "074 should record what REVOKE's locking was measured to be"
    )


# ───────────────────────── measured: the lock count ─────────────────────────

_PG_URL = (
    os.environ.get("AERORA_TEST_DATABASE_URL")
    or os.environ.get("TEST_DATABASE_URL")
    or ""
)

pg_only = pytest.mark.skipif(
    not _PG_URL,
    reason=(
        "requires PostgreSQL: set AERORA_TEST_DATABASE_URL to a postgres DSN. "
        "A SKIP here is NOT a pass -- the lock count is unverified."
    ),
)


@pytest.fixture()
def pg():
    import psycopg2

    conn = psycopg2.connect(_PG_URL)
    conn.autocommit = False
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _heavy_locks(cur) -> list[str]:
    cur.execute(
        """
        SELECT c.relname
          FROM pg_locks l
          JOIN pg_class c     ON c.oid = l.relation
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE l.pid = pg_backend_pid() AND l.locktype = 'relation'
           AND n.nspname = 'public'     AND l.mode = 'AccessExclusiveLock'
         ORDER BY 1
        """
    )
    return [r[0] for r in cur.fetchall()]


@pg_only
def test_a_redundant_rls_enable_really_does_take_the_heavy_lock(pg) -> None:
    """The premise the guards rest on, asserted against a real server.

    If a future PostgreSQL made this a no-op, the guards would be unnecessary rather than
    wrong -- but the claim in SECTION 0b would be stale, and this is where that surfaces.
    """
    cur = pg.cursor()
    cur.execute("SELECT to_regclass('public.strategy_backtests')")
    if cur.fetchone()[0] is None:
        pytest.skip("public.strategy_backtests is not in this database")

    cur.execute(
        "SELECT relrowsecurity FROM pg_class WHERE oid='public.strategy_backtests'::regclass"
    )
    if not cur.fetchone()[0]:
        pytest.skip("RLS is not already enabled here, so there is no redundancy to test")

    cur.execute("ALTER TABLE public.strategy_backtests ENABLE ROW LEVEL SECURITY")
    assert "strategy_backtests" in _heavy_locks(cur), (
        "a redundant ENABLE ROW LEVEL SECURITY no longer takes AccessExclusiveLock. "
        "SECTION 0b's reasoning is now stale -- re-measure before trusting it."
    )
    pg.rollback()


@pg_only
def test_a_revoke_takes_no_lock_on_the_table(pg) -> None:
    """The other half of the correction, so 074's comment stays honest."""
    cur = pg.cursor()
    cur.execute("SELECT to_regclass('public.strategy_backtests')")
    if cur.fetchone()[0] is None:
        pytest.skip("public.strategy_backtests is not in this database")

    cur.execute(
        "REVOKE INSERT, UPDATE, DELETE ON public.strategy_backtests FROM authenticated"
    )
    assert "strategy_backtests" not in _heavy_locks(cur), (
        "REVOKE now takes AccessExclusiveLock on the table. 074's comment and SECTION 0b "
        "both say it does not -- re-measure and correct them."
    )
    pg.rollback()


@pg_only
def test_the_whole_file_takes_only_the_policy_locks(pg) -> None:
    """The end-to-end claim: this file is applicable to a live database.

    Applied in a transaction and rolled back, so it asserts the lock footprint without
    changing anything.
    """
    cur = pg.cursor()
    cur.execute("SELECT to_regclass('public.strategy_backtests')")
    if cur.fetchone()[0] is None:
        pytest.skip("this database is not provisioned from repository source")

    cur.execute("SET LOCAL lock_timeout = '5s'")
    cur.execute("SET LOCAL statement_timeout = '300s'")
    cur.execute(HARDENING.read_text(encoding="utf-8"))

    locked = set(_heavy_locks(cur))
    unexpected = locked - POLICY_DDL_TABLES
    assert not unexpected, (
        f"the hardening file takes AccessExclusiveLock on {sorted(unexpected)}, which are "
        "not policy replacements. Each is a lock it does not need, and enough of them is "
        "what made the file unapplicable to production. See SECTION 0b."
    )
    pg.rollback()


@pg_only
@pytest.mark.parametrize("table", ["notifications", "invoices", "strategy_backtests"])
def test_a_guard_does_not_skip_a_table_that_genuinely_needs_rls(pg, table: str) -> None:
    """The correctness half, and the one that matters more than the lock count.

    A guard that skips too eagerly would leave a table unsecured while provisioning
    reported success -- strictly worse than the lock contention it was added to avoid.
    RLS is forced OFF inside the transaction, the file applied, and RLS required back ON.
    Rolled back either way, so the cluster is unchanged.
    """
    cur = pg.cursor()
    cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
    if cur.fetchone()[0] is None:
        pytest.skip(f"public.{table} is not in this database")

    cur.execute(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY")
    cur.execute(
        "SELECT relrowsecurity FROM pg_class WHERE oid=%s::regclass", (f"public.{table}",)
    )
    assert cur.fetchone()[0] is False, "could not force RLS off, so this proves nothing"

    cur.execute("SET LOCAL lock_timeout = '5s'")
    cur.execute("SET LOCAL statement_timeout = '300s'")
    cur.execute(HARDENING.read_text(encoding="utf-8"))

    cur.execute(
        "SELECT relrowsecurity FROM pg_class WHERE oid=%s::regclass", (f"public.{table}",)
    )
    assert cur.fetchone()[0] is True, (
        f"the guard skipped {table} while its RLS was OFF, so the hardening file would "
        "leave it unsecured and still report success. The guard must skip only when RLS "
        "is ALREADY enabled."
    )
    pg.rollback()


@pg_only
def test_the_guards_are_what_removed_the_locks(pg) -> None:
    """Compares against the committed pre-guard file, so the gain is attributed, not assumed.

    Without this, the 14-to-4 improvement is a claim in a commit message. With it, the
    before-number is read out of git and measured on the same cluster in the same run.
    """
    before_sql = subprocess.run(
        ["git", "show", "origin/main:migrations/hardening/999_privilege_hardening.sql"],
        capture_output=True,
        cwd=REPO,
    )
    if before_sql.returncode != 0:
        pytest.skip("origin/main is not available in this checkout")
    # bytes -> utf-8 explicitly: the file carries box-drawing characters that the Windows
    # default codec cannot decode.
    before_text = before_sql.stdout.decode("utf-8")
    if "relrowsecurity" in before_text.split("SECTION 1:")[0]:
        pytest.skip("origin/main already carries the guards; nothing to compare against")

    cur = pg.cursor()
    cur.execute("SELECT to_regclass('public.strategy_backtests')")
    if cur.fetchone()[0] is None:
        pytest.skip("this database is not provisioned from repository source")

    cur.execute("SET LOCAL lock_timeout = '5s'")
    cur.execute("SET LOCAL statement_timeout = '300s'")
    cur.execute(before_text)
    before = set(_heavy_locks(cur))
    pg.rollback()

    cur = pg.cursor()
    cur.execute("SET LOCAL lock_timeout = '5s'")
    cur.execute("SET LOCAL statement_timeout = '300s'")
    cur.execute(HARDENING.read_text(encoding="utf-8"))
    after = set(_heavy_locks(cur))
    pg.rollback()

    assert len(after) < len(before), (
        f"the guards removed no locks: {len(before)} -> {len(after)}"
    )
    assert before - after, "no table stopped being locked"
    assert after <= POLICY_DDL_TABLES, (
        f"locks remain outside the policy replacements: {sorted(after - POLICY_DDL_TABLES)}"
    )
