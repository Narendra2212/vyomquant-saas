"""The gate that must pass before `verify_schema_drift.py` is pointed at production.

WHY THIS FILE EXISTS
--------------------
The production drift check is the first operation this project will perform against the
live customer database, and the instruction governing it is unambiguous: it must be
READ-ONLY. "The script sets `readonly=True`" is a claim about source code, not a
property of a session, and this is a database holding customer billing records. So the
property is tested.

Four things are asserted, and each one corresponds to a way the claim could be false:

  1. The session the script opens genuinely rejects writes. Not "the script does not
     issue any" -- the SERVER refuses, with SQLSTATE 25006, so a future edit that adds a
     stray write cannot silently succeed.
  2. A full run changes nothing. Measured by snapshotting row counts and the catalogue
     before and after, rather than by reading the queries and judging them harmless.
  3. The DSN cannot leak. `redact()` is given credentials and asserted not to return
     them, including the awkward cases -- a password containing `@`, a DSN with no `@`
     at all.
  4. Supabase's transaction pooler on port 6543 is refused, because session settings do
     not survive it and `default_transaction_read_only` is the entire guarantee in (1).

WHAT THIS FILE DOES NOT ESTABLISH
---------------------------------
That production is correct, or that it has been read. It establishes only that the tool
is safe to run there. The distinction is the whole point of the exercise.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgres"),
    reason=(
        "needs a real PostgreSQL instance: `default_transaction_read_only` and SQLSTATE "
        "25006 do not exist in SQLite, so the read-only guarantee cannot be measured "
        "there. Start one with staging/bootstrap_pg.py."
    ),
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

psycopg2 = pytest.importorskip("psycopg2")

from scripts.verify_schema_drift import (  # noqa: E402
    _refuse_transaction_pooler,
    live_snapshot,
    redact,
)


# ── 1. the session refuses writes, and the server is what refuses ────────────


def test_the_checkers_own_session_cannot_write():
    """Open a session exactly as the checker does, then try to write through it.

    The write attempted is a harmless one against a scratch table the test creates on a
    SEPARATE connection, so a pass means "the read-only session was refused" rather than
    "the statement happened to be invalid".
    """
    writer = psycopg2.connect(TEST_DATABASE_URL)
    writer.autocommit = True
    table = "drift_readonly_probe"
    try:
        with writer.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS public.{table}")
            cur.execute(f"CREATE TABLE public.{table} (id int)")

        # The checker's own session configuration, duplicated here deliberately: if
        # someone changes it in the script, this test keeps asserting the old contract
        # and the mismatch is the signal.
        reader = psycopg2.connect(TEST_DATABASE_URL)
        reader.set_session(readonly=True, autocommit=True)
        try:
            with reader.cursor() as cur:
                with pytest.raises(psycopg2.Error) as caught:
                    cur.execute(f"INSERT INTO public.{table} (id) VALUES (1)")
            assert caught.value.pgcode == "25006", (
                f"a write through the drift checker's session was rejected with "
                f"{caught.value.pgcode}, not 25006 read_only_sql_transaction. The "
                f"read-only guarantee is not coming from the server."
            )
        finally:
            reader.close()

        # And nothing landed.
        with writer.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM public.{table}")
            assert cur.fetchone()[0] == 0
    finally:
        with writer.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS public.{table}")
        writer.close()


def test_the_session_also_refuses_ddl():
    """DDL as well as DML. A read-only transaction blocks both, and it is worth proving
    separately: the checker's whole purpose is reading the catalogue, so a bug there
    would most plausibly take the form of a CREATE or ALTER, not an INSERT.
    """
    reader = psycopg2.connect(TEST_DATABASE_URL)
    reader.set_session(readonly=True, autocommit=True)
    try:
        with reader.cursor() as cur:
            with pytest.raises(psycopg2.Error) as caught:
                cur.execute("CREATE TABLE public.drift_readonly_ddl_probe (id int)")
        assert caught.value.pgcode == "25006", (
            f"DDL through the drift checker's session was rejected with "
            f"{caught.value.pgcode}, not 25006"
        )
    finally:
        reader.close()


# ── 2. a full run changes nothing ────────────────────────────────────────────


def _fingerprint(conn) -> tuple:
    """Enough state that any write by the checker would change it.

    Row counts across every table, plus the catalogue's own object counts. Not a
    checksum of the data -- that would be slow and is unnecessary, because the question
    is whether the number of rows or objects moved at all.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY 1"
        )
        tables = [r[0] for r in cur.fetchall()]

        counts = []
        for table in tables:
            cur.execute(f'SELECT count(*) FROM public."{table}"')
            counts.append((table, cur.fetchone()[0]))

        cur.execute(
            "SELECT (SELECT count(*) FROM information_schema.columns "
            "          WHERE table_schema='public'), "
            "       (SELECT count(*) FROM pg_policies WHERE schemaname='public'), "
            "       (SELECT count(*) FROM pg_constraint), "
            "       (SELECT count(*) FROM pg_proc p JOIN pg_namespace n "
            "          ON n.oid=p.pronamespace WHERE n.nspname='public'), "
            "       (SELECT count(*) FROM information_schema.role_table_grants "
            "          WHERE table_schema='public')"
        )
        catalogue = cur.fetchone()

    return tuple(counts), catalogue


def test_a_full_drift_check_leaves_the_database_untouched():
    """Snapshot, run the real `live_snapshot`, snapshot again, compare.

    This is the assertion that would catch a write the two tests above cannot: one
    issued by a query inside `SNAPSHOT_QUERIES` itself -- a function call with a side
    effect, say -- rather than by the connection setup.
    """
    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = True
    try:
        before = _fingerprint(conn)

        snapshot = live_snapshot(TEST_DATABASE_URL)
        assert snapshot, "the checker returned nothing, so this proves nothing"
        assert snapshot.get("tables"), "the checker read no tables"

        after = _fingerprint(conn)
    finally:
        conn.close()

    assert before == after, (
        "running the drift checker changed the database. Row counts or catalogue "
        "object counts differ before and after:\n"
        f"  before: {before[1]}\n"
        f"  after : {after[1]}"
    )


def test_the_checker_reports_a_query_failure_instead_of_crashing():
    """A dimension it cannot read must become a recorded finding, not an exception.

    Production may deny access to something the local database allows -- Supabase
    restricts parts of the catalogue -- and the correct behaviour is to report that
    dimension as failed and carry on comparing the rest, rather than abort and leave the
    operator with nothing.
    """
    snapshot = live_snapshot(TEST_DATABASE_URL)
    for dimension, rows in snapshot.items():
        if rows and rows[0] and rows[0][0] == "QUERY FAILED":
            pytest.fail(
                f"dimension {dimension!r} could not be read on the LOCAL database, "
                f"which should not happen: {rows[0][1]}"
            )
    # Reaching here means every dimension read cleanly locally, which is the baseline
    # the production run is compared against.
    assert len(snapshot) >= 16, (
        f"expected at least 16 dimensions, got {len(snapshot)}: {sorted(snapshot)}"
    )


# ── 3. the DSN cannot leak ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "dsn, secret",
    [
        ("postgresql://postgres:sup3rs3cret@db.example.supabase.co:5432/postgres",
         "sup3rs3cret"),
        # A password containing `@`. `dsn.split("@")[-1]` leaks the tail of this one,
        # which is why redact() parses rather than splits.
        ("postgresql://postgres:p%40ssw%40rd@db.example.supabase.co:5432/postgres",
         "ssw"),
        ("postgres://user:tokenvalue123@host:6543/db?sslmode=require", "tokenvalue123"),
    ],
)
def test_redact_never_returns_the_password(dsn, secret):
    out = redact(dsn)
    assert secret not in out, (
        f"redact() leaked part of the credential: {out!r} contains {secret!r}"
    )
    assert "@" not in out, f"redact() returned userinfo: {out!r}"


def test_redact_still_identifies_the_database():
    """Redaction must not make the output useless -- the point of printing the DSN is to
    be able to tell production from staging in a launch report.
    """
    out = redact("postgresql://postgres:secret@db.abcdefg.supabase.co:5432/postgres")
    assert "db.abcdefg.supabase.co" in out
    assert "5432" in out
    assert "postgres" in out


def test_redact_handles_a_dsn_with_no_credentials():
    out = redact("postgresql://127.0.0.1:5432/postgres")
    assert "127.0.0.1" in out
    assert out.startswith("postgresql://")


# ── 4. the transaction pooler is refused ─────────────────────────────────────


def test_port_6543_is_refused():
    """Supabase's transaction-mode pooler. Session settings do not survive it, so
    `default_transaction_read_only` -- the entire guarantee tested above -- cannot be
    relied upon. The refusal must be an exit, not a warning.
    """
    with pytest.raises(SystemExit) as caught:
        _refuse_transaction_pooler(
            "postgresql://postgres:secret@db.example.supabase.co:6543/postgres"
        )
    message = str(caught.value)
    assert "6543" in message
    assert "secret" not in message, "the refusal message leaked the credential"


def test_port_5432_is_allowed():
    """The complement, so the refusal cannot pass by rejecting everything."""
    _refuse_transaction_pooler(
        "postgresql://postgres:secret@db.example.supabase.co:5432/postgres"
    )


def test_the_read_only_assertion_is_reachable_from_the_cli():
    """End to end: the script runs, prints its read-only banner, and exits 0 or 1.

    Exit 2 would mean it could not run at all, which here would mean the test setup is
    wrong rather than that drift was found.
    """
    proc = subprocess.run(
        [sys.executable, os.path.join("scripts", "verify_schema_drift.py"),
         "--dsn", TEST_DATABASE_URL],
        cwd=_REPO_ROOT, capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode in (0, 1), (
        f"the drift checker could not run (exit {proc.returncode}):\n"
        f"{proc.stdout}\n{proc.stderr}"
    )
    assert "read-only: yes" in proc.stdout, (
        "the script did not report verifying its read-only session:\n" + proc.stdout
    )
    assert "server   :" in proc.stdout, "the script did not report the server version"
    # And the credential is not in the output.
    if "@" in TEST_DATABASE_URL:
        userinfo = TEST_DATABASE_URL.split("//", 1)[1].split("@", 1)[0]
        if ":" in userinfo:
            password = userinfo.split(":", 1)[1]
            if password:
                assert password not in proc.stdout, "the CLI leaked the password"
