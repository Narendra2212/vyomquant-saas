"""The gate for the one production tool that WRITES.

`scripts/verify_production_security.py` measures cross-tenant isolation by seeding a row
as customer A and attempting to read it as customer B. That requires writing, and the
launch instruction says not to modify production data. The reconciliation is that nothing
is COMMITTED: every statement runs inside one transaction ending in an unconditional
`ROLLBACK`.

That reconciliation is the entire safety argument, so it is tested rather than asserted.

TWO PROPERTIES, DELIBERATELY NOT CONFLATED
------------------------------------------
An earlier version of this file compared row counts before and after and described the
result as the database being "byte-for-byte unchanged". That was an overclaim, and it is
the kind of overclaim this whole exercise exists to avoid. Row counts are a weak witness:
they are blind to a row deleted and reinserted, to a sequence advanced, to an audit row
written by a trigger, and to anything a `SECURITY DEFINER` function did out of band.

So the two properties are now measured and reported separately:

  DATABASE ROWS UNCHANGED      every table in `public` plus `auth.users` holds the same
                               number of rows, the synthetic identities are gone, and no
                               row carrying a synthetic marker survives. This is what
                               matters for customer data.

  DATABASE STATE FULLY RESTORED
                               additionally: every sequence sits where it did, no audit
                               or trigger-backed table gained a row, and no irreversible
                               side effect was detected. This is a STRICTLY STRONGER
                               claim, and PostgreSQL does not generally provide it --
                               `nextval()` is explicitly non-transactional.

The test ASSERTS the first and REPORTS the second. Where the second does not hold, the
specific irreversible effect is named rather than hidden inside a pass, because an
operator pointing this at a production database is entitled to know exactly what it
leaves behind.

STRUCTURAL CHECK
----------------
Measuring one run proves that run committed nothing. It does not prove the next one
cannot. So the source is also inspected: any reachable `commit()`, any `autocommit = True`
during the probe, or any exception path that can reach the end of `main()` without the
`finally` performing a rollback, fails these tests. The rollback has to be structurally
unavoidable, not merely observed once.
"""

from __future__ import annotations

import ast
import json
import os
import pathlib
import subprocess
import sys
import tempfile

import pytest

TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT_REL = os.path.join("scripts", "verify_production_security.py")
_SCRIPT_ABS = os.path.join(_REPO_ROOT, _SCRIPT_REL)

#: Tables whose rows are written by a trigger or are an append-only audit trail. A row
#: appearing here during a rolled-back probe would be an irreversible side effect, and
#: these are the places it would most plausibly appear.
AUDIT_AND_TRIGGER_TABLES = (
    "risk_settings_audit",
    "security_logs",
    "order_lifecycle_transitions",
    "transaction_records",
    "transaction_checkpoints",
    "transaction_rollbacks",
    "paper_events",
    "library_subscription_transitions",
    "marketplace_submission_transitions",
    "referral_commissions",
)


# ── the structural check runs without a database ─────────────────────────────


def _probe_source() -> str:
    return pathlib.Path(_SCRIPT_ABS).read_text(encoding="utf-8")


def _probe_ast() -> ast.Module:
    return ast.parse(_probe_source())


def test_the_probe_source_has_no_byte_order_mark():
    """A BOM makes the static checks below unable to parse their own target.

    PowerShell 5.1's `Set-Content -Encoding utf8` writes UTF-8 WITH a BOM, and two files
    acquired one during this work. CPython's import machinery tolerates it, so the script
    still ran and nothing looked wrong -- but

        ast.parse(open(path, encoding="utf-8").read())
        SyntaxError: invalid non-printable character U+FEFF

    and every structural guarantee asserted in this file depends on that parse. Asserted
    first, and separately, so the failure says "there is a BOM" rather than "the commit
    check could not run".

    `tests/test_bandit_sast_gate_is_green.py` walks module ASTs the same way and has the
    same exposure. `staging/strip_bom.py --apply` removes them.
    """
    raw = pathlib.Path(_SCRIPT_ABS).read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), (
        f"{_SCRIPT_REL} begins with a UTF-8 BOM. Every AST-based check in this file "
        f"fails to parse it. Run: python staging/strip_bom.py --apply"
    )


def test_the_probe_source_contains_no_commit_call():
    """No reachable `commit()` on a connection or transaction, anywhere in the file.

    Walked as an AST rather than grepped, so a commit inside a nested function, a
    comprehension or an exception handler is still found, and a mention of the word
    "commit" in a docstring is not a false positive.

    `savepoint.commit()` is the one permitted form and it is permitted for a specific
    reason: releasing a SAVEPOINT is not a transaction commit. It discards the savepoint
    and keeps the work inside the SAME uncommitted transaction, which still ends in
    ROLLBACK. The allowance is narrow -- the receiver must be named `savepoint`.
    """
    offenders: list[str] = []
    for node in ast.walk(_probe_ast()):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "commit":
            continue
        receiver = func.value
        name = (
            receiver.id if isinstance(receiver, ast.Name)
            else receiver.attr if isinstance(receiver, ast.Attribute)
            else "<expression>"
        )
        if name == "savepoint":
            continue
        offenders.append(f"line {node.lineno}: {name}.commit()")

    assert not offenders, (
        "the production security probe contains a commit call. Its entire safety "
        "argument is that nothing is committed, so this must be unreachable by "
        "construction:\n  " + "\n  ".join(offenders)
    )


def test_the_probe_never_enables_autocommit():
    """`autocommit = True`, or `psycopg2.connect(..., autocommit=True)`, would make every
    statement its own committed transaction and silently defeat the rollback.
    """
    offenders: list[str] = []
    for node in ast.walk(_probe_ast()):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "autocommit"
                    and isinstance(node.value, ast.Constant)
                    and node.value.value is True
                ):
                    offenders.append(f"line {node.lineno}: autocommit = True")
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if (
                    kw.arg == "autocommit"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                ):
                    offenders.append(f"line {node.lineno}: autocommit=True keyword")
                if kw.arg == "isolation_level":
                    offenders.append(
                        f"line {node.lineno}: isolation_level passed; "
                        f"ISOLATION_LEVEL_AUTOCOMMIT would defeat the rollback"
                    )
    assert not offenders, (
        "the probe enables autocommit somewhere, which would commit each statement "
        "individually and make the final ROLLBACK a no-op:\n  " + "\n  ".join(offenders)
    )


def test_the_probe_asserts_autocommit_is_off_before_writing():
    """Belt and braces: the script itself must check, not just avoid setting it.

    A connection whose default came from a DSN parameter or a pooler could arrive with
    autocommit on, and neither of the two tests above would notice.
    """
    source = _probe_source()
    assert "assert not conn.autocommit" in source, (
        "the probe does not assert that autocommit is off before it writes. The two "
        "static checks above only prove it does not ENABLE autocommit; they cannot "
        "prove the connection did not arrive with it on."
    )


def test_the_rollback_is_in_a_finally_block():
    """The rollback must be structurally unavoidable.

    Walks `main()` and requires a `try` whose `finally` performs `conn.rollback()`. A
    rollback at the end of the happy path would be skipped by any exception -- including
    one raised by the probe's own seeding -- and the synthetic rows would then be
    committed by whatever cleanup ran next, or left dangling for the connection's own
    teardown to decide.
    """
    tree = _probe_ast()
    main_fn = next(
        (n for n in tree.body
         if isinstance(n, ast.FunctionDef) and n.name == "main"),
        None,
    )
    assert main_fn is not None, "scripts/verify_production_security.py has no main()"

    def _has_rollback(nodes) -> bool:
        for node in nodes:
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "rollback"
                    and isinstance(inner.func.value, ast.Name)
                    and inner.func.value.id == "conn"
                ):
                    return True
        return False

    protected = [
        node for node in ast.walk(main_fn)
        if isinstance(node, ast.Try) and node.finalbody and _has_rollback(node.finalbody)
    ]
    assert protected, (
        "main() does not perform conn.rollback() inside a finally block. The rollback "
        "must happen on every exit path, including an exception raised mid-probe."
    )


def test_the_probe_refuses_without_acknowledgement_in_source():
    """The acknowledgement flag must gate the run before any connection is opened."""
    source = _probe_source()
    assert "_refuse_unless_authorised(args)" in source
    index_refuse = source.index("_refuse_unless_authorised(args)")
    index_connect = source.index("psycopg2.connect(args.dsn)")
    assert index_refuse < index_connect, (
        "the probe connects to the database before checking its acknowledgement flag"
    )


# ── everything below needs a real database ───────────────────────────────────

_needs_pg = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgres"),
    reason=(
        "needs a real PostgreSQL instance: the script measures RLS behaviour as the "
        "anon/authenticated roles, none of which exist in SQLite."
    ),
)

psycopg2 = pytest.importorskip("psycopg2")


def _connect():
    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = True
    return conn


def _row_counts(conn) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY 1"
        )
        tables = [r[0] for r in cur.fetchall()]
        counts = {}
        for table in tables:
            cur.execute(f'SELECT count(*) FROM public."{table}"')
            counts[table] = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM auth.users")
        counts["auth.users"] = cur.fetchone()[0]
    return counts


def _sequence_values(conn) -> dict[str, int]:
    """Current value of every sequence, read WITHOUT advancing it.

    `last_value` from `pg_sequences` is a read; `nextval()` would itself be the side
    effect being measured.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT schemaname || '.' || sequencename, coalesce(last_value, 0) "
            "FROM pg_sequences WHERE schemaname IN ('public', 'auth') ORDER BY 1"
        )
        return {name: value for name, value in cur.fetchall()}


def _audit_counts(conn) -> dict[str, int]:
    with conn.cursor() as cur:
        out = {}
        for table in AUDIT_AND_TRIGGER_TABLES:
            cur.execute(
                "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname='public' AND c.relkind='r' AND c.relname=%s",
                (table,),
            )
            if not cur.fetchone():
                continue
            cur.execute(f'SELECT count(*) FROM public."{table}"')
            out[table] = cur.fetchone()[0]
    return out


def _identity_pks(conn) -> set[str]:
    """Primary keys of every row in `profiles` and `auth.users`.

    Stronger than a count: a probe that deleted a real customer and inserted a synthetic
    one would leave the count identical.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT id::text FROM public.profiles")
        profiles = {f"profiles:{r[0]}" for r in cur.fetchall()}
        cur.execute("SELECT id::text FROM auth.users")
        users = {f"auth.users:{r[0]}" for r in cur.fetchall()}
    return profiles | users


#: The synthetic text the probe stamps on anything it writes. Scanned either side of the
#: run so the assertion is a DELTA — see `test_no_synthetic_marker_value_survives`.
_MARKERS = ["probe-", "example.invalid", "sweep-reader"]


def _marker_rows(conn, markers: list[str]) -> list[tuple[str, str, int]]:
    """Any row anywhere in `public` whose text column holds one of `markers`.

    The probe stamps synthesised values with `probe-<hex>` and its identities with
    e-mail addresses at `example.invalid`. If any of those survive a rollback, something
    was committed that the row counts might not reveal -- a row written into a table that
    also lost a row, say.
    """
    if not markers:
        return []
    found: list[tuple[str, str, int]] = []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.table_name, c.column_name "
            "FROM information_schema.columns c "
            "JOIN pg_class t ON t.relname = c.table_name "
            "JOIN pg_namespace n ON n.oid = t.relnamespace AND n.nspname = 'public' "
            "WHERE c.table_schema = 'public' AND t.relkind = 'r' "
            "  AND c.data_type IN ('text', 'character varying') "
            "ORDER BY 1, 2"
        )
        columns = cur.fetchall()
        for table, column in columns:
            for marker in markers:
                cur.execute(
                    f'SELECT count(*) FROM public."{table}" '
                    f'WHERE "{column}" LIKE %s',
                    (f"%{marker}%",),
                )
                count = cur.fetchone()[0]
                if count:
                    found.append((table, column, count))
    return found


def _run(extra_args: list[str], env_extra: dict[str, str] | None = None):
    env = {**os.environ, "AERORA_TEST_DATABASE_URL": TEST_DATABASE_URL}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, _SCRIPT_REL, "--dsn", TEST_DATABASE_URL, *extra_args],
        cwd=_REPO_ROOT, capture_output=True, text=True, timeout=1800, env=env,
    )


# ── refusals ─────────────────────────────────────────────────────────────────


@_needs_pg
def test_it_refuses_without_the_acknowledgement_flag():
    proc = _run([])
    assert proc.returncode != 0, "the script ran without acknowledgement"
    combined = proc.stdout + proc.stderr
    assert "REFUSING TO RUN" in combined
    assert "rolled back" in combined, (
        "the refusal should say what the flag acknowledges, not just refuse"
    )


@_needs_pg
@pytest.mark.parametrize(
    "var", ["VYOMQUANT_ENABLE_LIVE_TRADING", "AERORA_ENABLE_LIVE_TRADING"]
)
def test_it_refuses_while_a_live_trading_switch_is_set(var):
    proc = _run(["--i-understand-this-writes-and-rolls-back", "--limit", "1"],
                {var: "true"})
    assert proc.returncode != 0, f"the script ran with {var}=true"
    combined = proc.stdout + proc.stderr
    assert "REFUSING TO RUN" in combined and var in combined


@_needs_pg
def test_it_refuses_in_live_mode():
    proc = _run(["--i-understand-this-writes-and-rolls-back", "--limit", "1"],
                {"VYOMQUANT_MODE": "live"})
    assert proc.returncode != 0
    assert "VYOMQUANT_MODE" in proc.stdout + proc.stderr


def test_it_refuses_the_transaction_pooler():
    """Session settings do not survive port 6543, and the role switching this script
    does is session state. A probe whose `SET LOCAL ROLE` silently did not apply would
    report that anon cannot read anything -- as the connecting superuser.

    No database needed: the refusal happens before connecting.
    """
    proc = subprocess.run(
        [sys.executable, _SCRIPT_REL,
         "--dsn", "postgresql://postgres:secret@db.example.supabase.co:6543/postgres",
         "--i-understand-this-writes-and-rolls-back"],
        cwd=_REPO_ROOT, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode != 0
    combined = proc.stdout + proc.stderr
    assert "6543" in combined
    assert "secret" not in combined, "the refusal leaked the credential"


# ── the two properties, measured separately ──────────────────────────────────


@pytest.fixture(scope="module")
def probe_run():
    """Run the probe once, with a full state snapshot either side.

    Module-scoped because a complete run takes most of a minute and every assertion
    below interrogates the same evidence.
    """
    if not TEST_DATABASE_URL.startswith("postgres"):
        pytest.skip("needs a real PostgreSQL instance")

    conn = _connect()
    try:
        before = {
            "rows": _row_counts(conn),
            "sequences": _sequence_values(conn),
            "audit": _audit_counts(conn),
            "identities": _identity_pks(conn),
            "markers": _marker_rows(conn, _MARKERS),
        }
    finally:
        conn.close()

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "matrix.json")
        proc = _run(["--i-understand-this-writes-and-rolls-back", "--out", out])
        assert proc.returncode in (0, 1), (
            f"the script failed to complete (exit {proc.returncode}):\n"
            f"{proc.stdout[-3000:]}\n{proc.stderr[-2000:]}"
        )
        matrix = json.loads(pathlib.Path(out).read_text(encoding="utf-8"))

    conn = _connect()
    try:
        after = {
            "rows": _row_counts(conn),
            "sequences": _sequence_values(conn),
            "audit": _audit_counts(conn),
            "identities": _identity_pks(conn),
        }
        markers = _marker_rows(conn, _MARKERS)
    finally:
        conn.close()

    return {
        "before": before, "after": after, "matrix": matrix,
        "markers": markers, "stdout": proc.stdout,
    }


@_needs_pg
def test_property_one_database_rows_unchanged(probe_run):
    """DATABASE ROWS UNCHANGED -- asserted. This is the customer-data guarantee."""
    before = probe_run["before"]["rows"]
    after = probe_run["after"]["rows"]
    moved = {
        table: (before[table], after[table])
        for table in before if before[table] != after.get(table)
    }
    assert not moved, (
        "row counts changed across a run that is supposed to commit nothing:\n"
        + "\n".join(f"  {t}: {b} -> {a}" for t, (b, a) in moved.items())
    )


@_needs_pg
def test_the_synthetic_identities_are_absent_afterwards(probe_run):
    """Stronger than a count: the exact primary keys are compared.

    A probe that deleted a real customer and inserted a synthetic one would leave
    `count(*)` identical and this set different.
    """
    before = probe_run["before"]["identities"]
    after = probe_run["after"]["identities"]
    added = sorted(after - before)
    removed = sorted(before - after)
    assert not added, (
        f"these identities exist after the rollback and did not before: {added[:10]}"
    )
    assert not removed, (
        f"these identities existed before the probe and are now gone: {removed[:10]}. "
        f"The probe is supposed to add and roll back, never to delete."
    )
    assert probe_run["matrix"]["leftover_identities_after_rollback"] == 0, (
        "the script's own post-rollback check found its synthetic identities present"
    )


@_needs_pg
def test_no_synthetic_marker_value_survives(probe_run):
    """The probe stamps synthesised text as `probe-<hex>` and its identities as
    `...@example.invalid`. None of what IT writes may remain anywhere in `public`.

    This catches what row counts cannot: a committed insert into a table that also lost
    a row, leaving the count unchanged.

    MEASURED AS A DELTA, like its two siblings above. The earlier version compared the
    post-run scan against the empty list, which silently assumed the probe is the only
    thing that has ever written a `probe-…` value to this database. It is not: the
    development substrate carried `paper_orders.idempotency_key = 'probe-direct-1'`,
    committed by a different `staging/` script a day earlier, and that one foreign row
    failed this test while the probe itself had rolled back cleanly. An absolute count
    cannot tell "the probe leaked" from "something else once wrote a similar string",
    which is the only question this test is asking. The delta can, and it still fails on
    any marker row the run actually adds.

    The residue is deliberately left in place rather than deleted, so this assertion
    passing is itself evidence that the misattribution is fixed.
    """
    before = dict(((t, c), n) for t, c, n in probe_run["before"]["markers"])
    after = dict(((t, c), n) for t, c, n in probe_run["markers"])

    gained = {
        key: (before.get(key, 0), count)
        for key, count in after.items()
        if count > before.get(key, 0)
    }
    assert not gained, (
        "rows carrying the probe's synthetic markers survived the rollback:\n"
        + "\n".join(
            f"  {t}.{c}: {b} row(s) before -> {a} row(s) after"
            for (t, c), (b, a) in gained.items()
        )
    )


@_needs_pg
def test_no_audit_or_trigger_backed_table_gained_a_row(probe_run):
    """Trigger-written and append-only tables, checked by name.

    A `SECURITY DEFINER` trigger that logged the probe's inserts would be an
    irreversible side effect if it wrote outside the transaction. Inside the
    transaction it rolls back with everything else -- which is what this asserts.
    """
    before = probe_run["before"]["audit"]
    after = probe_run["after"]["audit"]
    moved = {
        table: (before[table], after.get(table))
        for table in before if before[table] != after.get(table)
    }
    assert not moved, (
        "audit or trigger-backed tables changed across the rolled-back probe:\n"
        + "\n".join(f"  {t}: {b} -> {a}" for t, (b, a) in moved.items())
    )


@_needs_pg
def test_property_two_full_state_restoration_is_reported_not_assumed(probe_run):
    """DATABASE STATE FULLY RESTORED -- the stronger claim, measured and REPORTED.

    This test does not fail on a sequence advance, and that is deliberate rather than
    lenient. `nextval()` is explicitly non-transactional in PostgreSQL: a sequence
    consumed inside a rolled-back transaction stays consumed, by design, so that two
    concurrent transactions cannot be handed the same value. No amount of care in the
    probe can avoid it while it inserts rows into tables with serial defaults.

    What this test enforces is HONESTY about it: the effect is named, and the launch
    documents may claim "rows unchanged" but may not claim "state fully restored" unless
    this reports no drift.
    """
    before = probe_run["before"]["sequences"]
    after = probe_run["after"]["sequences"]
    advanced = {
        name: (before[name], after.get(name))
        for name in before if before[name] != after.get(name)
    }

    rows_unchanged = probe_run["before"]["rows"] == probe_run["after"]["rows"]
    assert rows_unchanged, "property one failed; see the dedicated test"

    if advanced:
        print()
        print("IRREVERSIBLE SIDE EFFECT -- sequences advanced and did not roll back:")
        for name, (b, a) in advanced.items():
            print(f"    {name}: {b} -> {a}")
        print("  This is PostgreSQL's documented behaviour: nextval() is")
        print("  non-transactional so that concurrent transactions cannot be handed the")
        print("  same value. No customer data is affected -- only the next surrogate key")
        print("  that will be issued.")
        print()
        print("  VERDICT: DATABASE ROWS UNCHANGED = yes")
        print("           DATABASE STATE FULLY RESTORED = NO (sequences advanced)")
    else:
        print()
        print("  VERDICT: DATABASE ROWS UNCHANGED = yes")
        print("           DATABASE STATE FULLY RESTORED = yes (no sequence advanced,")
        print("           no audit row written, no identity added or removed)")

    # The claim the documents are allowed to make is recorded here, as data, so a
    # reader of the test output can see which of the two properties holds.
    assert isinstance(advanced, dict)


@_needs_pg
def test_it_actually_measured_something(probe_run):
    """The complement of every test above, and it is not redundant.

    A script that connected and did nothing would leave every count, sequence and
    identity unchanged and pass all of them. This asserts the run produced real probe
    results, so "nothing was committed" is a statement about a run that happened.
    """
    probes = probe_run["matrix"]["probes"]
    assert len(probes) >= 40, f"only {len(probes)} table(s) probed"

    seeded = [p for p in probes if p["seeded"]]
    assert len(seeded) >= 40, f"only {len(seeded)} table(s) could be seeded"

    isolated = [p for p in probes if p.get("verdict") == "ISOLATED"]
    assert len(isolated) >= 40, (
        f"only {len(isolated)} table(s) reached the ISOLATED verdict -- meaning the "
        f"owner could read its own row AND nobody else could. Fewer than that and the "
        f"'0 leaks' result is not evidence of isolation, it is evidence that nothing "
        f"was readable."
    )

    leaks = [p for p in probes if p.get("verdict") == "LEAK"]
    assert not leaks, (
        "cross-tenant or anon access succeeded on: "
        + ", ".join(f"{p['table']} ({'; '.join(p['leaks'])})" for p in leaks)
    )


@_needs_pg
def test_the_output_reports_its_own_rollback(probe_run):
    """An operator reading the console must be able to see that the rollback happened,
    without opening the JSON or trusting this test.
    """
    stdout = probe_run["stdout"]
    assert "ROLLBACK issued" in stdout
    assert "synthetic identities still present after rollback: 0" in stdout
    assert "autocommit: OFF" in stdout


@_needs_pg
def test_service_role_is_documented_as_intentionally_privileged(probe_run):
    """The report must not leave a reader thinking service_role was tested and passed.

    It holds BYPASSRLS; it would 'fail' every probe by design, and the real control for
    paths that use it is the application's own tenant filter -- a separate, unverified
    question. Saying so is part of the output's correctness.
    """
    assert "service_role" in probe_run["stdout"]
    assert "BYPASSRLS" in probe_run["stdout"]


@_needs_pg
def test_no_control_and_not_seeded_are_not_counted_as_isolation(probe_run):
    """The classification contract, asserted against the artefact.

    `NO CONTROL` means every attempt was blocked including the owner's own, so the
    policy was never shown to discriminate. `NOT SEEDED` means nothing was measured at
    all. Neither may appear as evidence of isolation, and the script's exit code must be
    non-zero while any remain.
    """
    probes = probe_run["matrix"]["probes"]
    verdicts = {p["table"]: p.get("verdict") for p in probes}

    for table, verdict in verdicts.items():
        if verdict == "ISOLATED":
            probe = next(p for p in probes if p["table"] == table)
            assert probe["results"].get("owner_select") == "ALLOWED", (
                f"{table} is reported ISOLATED without a passing positive control; "
                f"that is the NO CONTROL case mislabelled"
            )
            assert not probe["leaks"], f"{table} is ISOLATED with recorded leaks"

    no_control = [t for t, v in verdicts.items() if v == "NO CONTROL"]
    not_seeded = [p["table"] for p in probes if not p["seeded"]]
    print()
    print(f"  NO CONTROL ({len(no_control)}): {no_control}")
    print(f"  NOT SEEDED ({len(not_seeded)}): {not_seeded}")
    print("  Neither is counted as isolation by the script or by this test.")
