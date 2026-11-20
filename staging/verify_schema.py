"""Verify the provisioned schema against the application's stated assumptions.

Run against the STAGING database created by scripts/provision_database.py. Every answer
here comes from the PostgreSQL catalogue, not from reading a migration file.

The headline question is the one the pre-launch audit could not answer:
**does execution_records have a PRIMARY KEY on execution_id?** That key is the
database-level guarantee behind order idempotency, and because nothing ran
`alembic upgrade`, whether it existed anywhere was unknown.
"""

import os
import pathlib
import sys

import psycopg2


def _dsn() -> str:
    """The database to check: the environment first, then the local staging file.

    The environment comes first so this runs in CI, where `pg_dsn.txt` does not exist --
    it is a local artefact and `.gitignore` excludes it. Reading the file
    unconditionally, as an earlier version did, made this script the one probe in
    `staging/run_all_probes.py` that could not run anywhere but a developer's machine.
    """
    env = os.environ.get("AERORA_TEST_DATABASE_URL")
    if env:
        return env.strip()

    local = pathlib.Path(__file__).resolve().parent / "pg_dsn.txt"
    if local.exists():
        return local.read_text(encoding="utf-8").strip()

    sys.exit(
        "no database to check: set AERORA_TEST_DATABASE_URL, or start a local server "
        "with staging/bootstrap_pg.py so that staging/pg_dsn.txt exists"
    )


DSN = _dsn()

#: Tables the application depends on, grouped by the concern that would break.
REQUIRED_TABLES = {
    "execution / orders": [
        "execution_records", "orders", "fills", "positions", "idempotency_keys",
    ],
    "strategy": [
        "strategies", "strategy_versions", "strategy_deployments", "strategy_backtests",
    ],
    "paper trading": [
        "paper_sessions", "paper_orders", "paper_fills", "paper_positions",
        "paper_accounts",
    ],
    "marketplace": [
        "library_strategies", "library_ratings", "library_subscriptions",
        "marketplace_submissions", "deployment_permissions",
    ],
    "billing": ["subscriptions", "invoices", "payment_methods"],
    "identity / tenancy": ["profiles", "processed_orders"],
    "ml": ["training_governance_decisions"],
    "signals": ["signals"],
    "notifications / support": ["notifications", "support_tickets", "ticket_comments"],
    "risk": ["risk_settings", "strategy_limits", "risk_settings_audit"],
}

#: (table, expected primary key columns) — the constraints the application relies on.
EXPECTED_PRIMARY_KEYS = [
    ("execution_records", ["execution_id"]),
    ("idempotency_keys", ["idempotency_key", "tenant_id"]),
    ("orders", ["id"]),
    ("positions", ["position_id"]),
    ("profiles", ["id"]),
    ("strategies", ["id"]),
]

#: Unique constraints/indexes whose absence would permit a duplicate that matters.
EXPECTED_UNIQUE = [
    ("paper_orders", "uq_paper_order_idem"),
    ("strategy_versions", "uq_strategy_versions_one_current"),
]

FAILURES: list[str] = []
PASSES: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    if ok:
        PASSES.append(label)
        print(f"  PASS  {label}")
    else:
        FAILURES.append(f"{label}{(' — ' + detail) if detail else ''}")
        print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")


def main() -> int:
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()

    print("=" * 78)
    print("STAGING SCHEMA VERIFICATION")
    print("=" * 78)
    cur.execute("SELECT current_setting('server_version')")
    print(f"server: PostgreSQL {cur.fetchone()[0]}")
    cur.execute(
        "SELECT count(*) FROM pg_tables WHERE schemaname='public'"
    )
    print(f"public base tables: {cur.fetchone()[0]}")
    print()

    # ── Tables exist ─────────────────────────────────────────────────────────
    print("1. REQUIRED TABLES")
    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
    present = {r[0] for r in cur.fetchall()}
    for concern, tables in REQUIRED_TABLES.items():
        missing = [t for t in tables if t not in present]
        check(not missing, f"{concern}: {len(tables)} table(s)",
              f"missing {missing}" if missing else "")
    print()

    # ── Primary keys ─────────────────────────────────────────────────────────
    print("2. PRIMARY KEYS (the audit's central unknown)")
    for table, expected in EXPECTED_PRIMARY_KEYS:
        if table not in present:
            check(False, f"{table} PK", "table absent")
            continue
        cur.execute(
            """
            SELECT a.attname
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            JOIN unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
            WHERE n.nspname='public' AND t.relname=%s AND c.contype='p'
            ORDER BY k.ord
            """,
            (table,),
        )
        actual = [r[0] for r in cur.fetchall()]
        check(actual == expected, f"{table} PK = {expected}",
              f"actual {actual or 'NONE'}")
    print()

    # ── Unique constraints that prevent duplicates ───────────────────────────
    print("3. UNIQUE CONSTRAINTS / INDEXES")
    for table, name in EXPECTED_UNIQUE:
        cur.execute(
            """
            SELECT 1 FROM pg_indexes
            WHERE schemaname='public' AND tablename=%s AND indexname=%s
            UNION
            SELECT 1 FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
            WHERE t.relname=%s AND c.conname=%s
            """,
            (table, name, table, name),
        )
        check(cur.fetchone() is not None, f"{table}.{name}")
    print()

    # ── Foreign keys ─────────────────────────────────────────────────────────
    print("4. FOREIGN KEYS")
    cur.execute(
        "SELECT count(*) FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
        "JOIN pg_namespace n ON n.oid=t.relnamespace "
        "WHERE n.nspname='public' AND c.contype='f'"
    )
    fk_count = cur.fetchone()[0]
    check(fk_count > 0, f"{fk_count} foreign key constraint(s) present")
    print()

    # ── Row level security ───────────────────────────────────────────────────
    print("5. ROW LEVEL SECURITY")
    cur.execute(
        "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND rowsecurity"
    )
    rls_tables = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM pg_policies WHERE schemaname='public'")
    policies = cur.fetchone()[0]
    check(rls_tables > 0, f"RLS enabled on {rls_tables} table(s)")
    check(policies > 0, f"{policies} policy/policies defined")

    # The inert tenant-GUC policies the audit found: keyed on app.current_tenant_id,
    # which nothing in the application sets.
    cur.execute(
        "SELECT count(*) FROM pg_policies WHERE schemaname='public' "
        "AND (qual LIKE '%app.current_tenant_id%' OR with_check LIKE '%app.current_tenant_id%')"
    )
    guc_policies = cur.fetchone()[0]
    print(f"  INFO  {guc_policies} policy/policies key on app.current_tenant_id "
          f"({'INERT unless the app sets that GUC' if guc_policies else 'none'})")

    cur.execute(
        "SELECT count(*) FROM pg_policies WHERE schemaname='public' "
        "AND (qual LIKE '%auth.uid()%' OR with_check LIKE '%auth.uid()%')"
    )
    print(f"  INFO  {cur.fetchone()[0]} policy/policies key on auth.uid()")
    print()

    # ── Migration tracking ───────────────────────────────────────────────────
    print("6. MIGRATION RECORD")
    cur.execute("SELECT count(*), max(applied_at) FROM public.schema_migrations")
    n, last = cur.fetchone()
    check(n > 0, f"{n} migration file(s) recorded, last at {last}")
    cur.execute("SELECT version_num FROM alembic_version")
    rows = cur.fetchall()
    check(bool(rows), f"alembic_version = {[r[0] for r in rows]}")
    print()

    print("=" * 78)
    print(f"RESULT: {len(PASSES)} passed, {len(FAILURES)} failed")
    if FAILURES:
        print("\nFAILURES:")
        for f in FAILURES:
            print(f"  - {f}")
    print("=" * 78)

    conn.close()
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
