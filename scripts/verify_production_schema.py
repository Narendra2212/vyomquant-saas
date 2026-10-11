#!/usr/bin/env python
"""READ-ONLY production schema and security verification. The paper-only launch gate.

WHAT THIS IS
------------
The remediation work verified a PostgreSQL database **provisioned from this repository**.
It established nothing about production, and that gap is the single remaining
launch-critical unknown: Supabase allows a schema to be edited through its dashboard, and
such an edit leaves no migration in this tree, so the repository and the live database can
disagree while every check in CI stays green.

This script is the first operation this project performs against the live customer
database. It is therefore **strictly read-only**, and that property is enforced by the
server rather than by convention:

  * `default_transaction_read_only` is set AND read back; the script aborts if the server
    does not confirm it. A write would be rejected with SQLSTATE 25006.
  * Supabase's transaction pooler (port 6543) is refused, because session settings do not
    survive it and the guarantee above would become unverifiable.
  * The DSN is parsed and redacted before anything is printed. No credential reaches
    stdout, a log, or a report.
  * No row data is read. Every query targets a system catalogue or
    `information_schema`. The one exception is `COUNT(*)`, used to report table
    population so that an empty-table result is not mistaken for a secure one -- counts
    carry no customer data.

`tests/test_schema_drift_checker_is_read_only_postgres.py` proves these properties by
measurement -- it attempts a write through the same session and asserts SQLSTATE 25006,
and snapshots row counts and catalogue counts before and after a full run.

WHAT IT CHECKS
--------------
  1. SCHEMA DRIFT -- all 16 dimensions, delegated to `verify_schema_drift.py` so there is
     one implementation: tables (incl. owner), columns (incl. identity/generated),
     primary keys, foreign keys (incl. ON DELETE/ON UPDATE, via pg_get_constraintdef),
     unique and check constraints, indexes, RLS flags (incl. relforcerowsecurity),
     policies (incl. roles, cmd, USING, WITH CHECK), privileges, extensions, functions,
     triggers, sequences, enum types, alembic_version, recorded migrations.
  2. BILLING EXPOSURE -- the static half: for `invoices`, `payment_methods`,
     `subscriptions`, is RLS on, are the policies owner-scoped, and do the browser roles
     hold grants that RLS would have to catch?
  3. THE TWO POLICY SHAPES THAT PASS AN RLS INVENTORY -- a table with RLS enabled and a
     constant-TRUE predicate, and a table carrying two overlapping permissive policies
     where the broader one decides the outcome. Both were real defects here
     (`library_ratings`, `referral_codes`).
  4. PRIVILEGES RLS CANNOT FILTER -- TRUNCATE, REFERENCES, TRIGGER held by a browser role.
  5. EXECUTION INTEGRITY -- `execution_records` primary key on `execution_id`,
     `idempotency_keys` uniqueness, `orders` constraints. The database-level half of the
     order-idempotency guarantee.
  6. TENANT ID TYPE CONSISTENCY -- uuid versus varchar across every tenant/user foreign
     key, which is what blocked 5 of 7 foreign keys from being created locally.
  7. HIGH-RISK TABLE INVENTORY -- the eleven named tables plus every other table carrying
     a per-customer ownership column, each with its RLS and grant posture.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
  * It does not write. Not even synthetic test rows. The behavioural cross-tenant probes
    are a separate script (`scripts/verify_production_security.py`) because they DO write
    -- inside a transaction that is always rolled back -- and that is a different risk
    decision that deserves a separate, explicit act.
  * It does not fix anything. Drift is classified and reported; remediation requires
    approval.
  * It does not run migrations. It never calls alembic.

USAGE
-----
    python scripts/verify_production_schema.py --dsn "postgresql://...:5432/postgres"

    # write the full evidence bundle for the launch report
    python scripts/verify_production_schema.py --dsn "..." --out staging/production_schema.json

EXIT CODES
----------
    0  GREEN  -- no drift, and no launch-critical security finding
    1  AMBER  -- drift exists, none of it launch-critical
    2  RED    -- at least one launch-critical finding
    3  could not run (no DSN, refused pooler, connection failed, no baseline)
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys

try:
    import psycopg2
except ImportError:  # pragma: no cover
    sys.exit("psycopg2 is required: pip install psycopg2-binary")

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.owner_columns import (  # noqa: E402
    ALWAYS_PER_CUSTOMER_TABLES,
    derive_owner_columns,
)
from scripts.verify_provisioning import SNAPSHOT_QUERIES, _norm  # noqa: E402
from scripts.verify_schema_drift import (  # noqa: E402
    ADVISORY_DIMENSIONS,
    _as_tuples,
    _refuse_transaction_pooler,
    assert_read_only,
    redact,
)

DEFAULT_BASELINE = ROOT / "staging" / "provisioning_snapshot.json"

#: The tables the launch gate names explicitly. Checked whether or not they carry a
#: recognised ownership column, because their absence is itself a finding.
HIGH_RISK_TABLES = (
    "invoices",
    "payment_methods",
    "subscriptions",
    "referral_codes",
    "referral_wallets",
    "library_ratings",
    "orders",
    "positions",
    "fills",
    "execution_records",
    "idempotency_keys",
)

#: Billing tables. Separated from the list above because the launch criteria treat
#: cross-tenant access to these as unconditionally RED.
BILLING_TABLES = ("invoices", "payment_methods", "subscriptions")

BROWSER_ROLES = ("anon", "authenticated")

#: Privileges row level security cannot filter. TRUNCATE empties a table regardless of
#: any USING clause; TRIGGER allows attaching code to another user's writes.
UNFILTERABLE = ("TRUNCATE", "REFERENCES", "TRIGGER")

#: Policies that are intentionally broader than an ownership test, each with the reason
#: it was read and accepted. An allowlist with a written justification, NOT a blanket
#: suppression: a policy not named here still fails check 3e, so a NEW broad policy --
#: including one that exists in production and not in this repository -- is caught.
#:
#: Keyed by (table, policy name) so a policy that is later REWRITTEN under the same name
#: is still covered; that is a deliberate limitation and the behavioural probe
#: (`scripts/verify_production_security.py`) is the authority on whether a leak exists.
ACCEPTED_BROAD_POLICIES: dict[tuple[str, str], str] = {
    ("marketplace_listings", "All users can view published listings"): (
        "USING (is_published = true) granted to `public`, which includes `anon`. This "
        "is the marketplace storefront and the breadth IS the feature -- an "
        "unauthenticated visitor is meant to browse published listings. The companion "
        "policy `Users can view their own listings` (user_id = auth.uid()) adds the "
        "owner's UNPUBLISHED rows, which is all it can add. Checked: the table carries "
        "no financial column; settlement figures live in `marketplace_settlements`, "
        "which is owner/purchaser scoped."
    ),
}


# â”€â”€ finding model â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class Findings:
    """Collected findings, each classified RED or AMBER at the point of discovery.

    Classification happens where the evidence is, not in a later pass over a list of
    strings. That way the reason a thing is launch-critical is written next to the check
    that found it.
    """

    def __init__(self) -> None:
        self.red: list[tuple[str, str]] = []
        self.amber: list[tuple[str, str]] = []
        self.green: list[str] = []
        self.unverified: list[tuple[str, str]] = []

    def critical(self, area: str, detail: str) -> None:
        self.red.append((area, detail))

    def minor(self, area: str, detail: str) -> None:
        self.amber.append((area, detail))

    def ok(self, detail: str) -> None:
        self.green.append(detail)

    def cannot_check(self, area: str, why: str) -> None:
        """A check that could not be performed. NOT a pass, and never counted as one."""
        self.unverified.append((area, why))

    @property
    def verdict(self) -> str:
        if self.red:
            return "RED"
        if self.amber:
            return "AMBER"
        return "GREEN"


# â”€â”€ read-only connection â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def connect_read_only(dsn: str):
    _refuse_transaction_pooler(dsn)
    conn = psycopg2.connect(dsn)
    conn.set_session(readonly=True, autocommit=True)
    assert_read_only(conn, dsn)
    return conn


def q(conn, sql: str, params=None) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


# â”€â”€ 1. schema drift â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def check_drift(conn, dsn: str, baseline: dict, f: Findings) -> dict:
    live: dict[str, list] = {}
    with conn.cursor() as cur:
        for name, sql in SNAPSHOT_QUERIES.items():
            try:
                cur.execute(sql)
                live[name] = [tuple(_norm(v) for v in row) for row in cur.fetchall()]
            except psycopg2.Error as exc:
                live[name] = [("QUERY FAILED", str(exc).splitlines()[0])]
                f.cannot_check(
                    f"drift/{name}",
                    f"production denied this catalogue read: {str(exc).splitlines()[0]}",
                )

    print("-" * 78)
    print("1. SCHEMA DRIFT vs the provisioning baseline")
    print("-" * 78)

    for name in SNAPSHOT_QUERIES:
        expected = _as_tuples(baseline.get(name, []))
        actual = _as_tuples(live.get(name, []))
        if expected == actual:
            print(f"  same  {name:<30} {len(expected)}")
            continue

        missing = [r for r in expected if r not in actual]
        extra = [r for r in actual if r not in expected]
        print(f"  DRIFT {name:<30} baseline={len(expected)} live={len(actual)} "
              f"({len(missing)} missing, {len(extra)} unexpected)")
        for row in missing[:8]:
            print(f"          MISSING from production : {row}")
        if len(missing) > 8:
            print(f"          ... and {len(missing) - 8} more missing")
        for row in extra[:8]:
            print(f"          NOT IN this repository  : {row}")
        if len(extra) > 8:
            print(f"          ... and {len(extra) - 8} more unexpected")

        detail = (f"{len(missing)} missing, {len(extra)} unexpected "
                  f"(baseline {len(expected)}, production {len(actual)})")

        if name in ADVISORY_DIMENSIONS:
            # Deployment bookkeeping. A database ahead or behind on migration records is
            # a deployment-state question, not a schema-shape one, and conflating the two
            # makes the signal useless.
            f.minor(f"drift/{name}", detail)
        elif name in ("tables", "columns", "primary_keys", "foreign_keys",
                      "row_level_security", "policies", "privileges",
                      "unique_and_check_constraints", "functions", "triggers"):
            f.critical(f"drift/{name}", detail)
        else:
            # Indexes, sequences, extensions, enum types. A missing index is a
            # performance or uniqueness question depending on the index, and the
            # uniqueness half is already covered by `unique_and_check_constraints`.
            f.minor(f"drift/{name}", detail)

    return live


# â”€â”€ 2. billing exposure, static â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def check_billing(conn, f: Findings) -> list[dict]:
    print()
    print("-" * 78)
    print("2. BILLING TABLES -- RLS, policies and browser-role grants")
    print("-" * 78)

    report: list[dict] = []
    for table in BILLING_TABLES:
        exists = q(
            conn,
            "SELECT c.relrowsecurity, c.relforcerowsecurity, pg_get_userbyid(c.relowner) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind='r' AND c.relname=%s",
            (table,),
        )
        if not exists:
            print(f"  {table:<20} ABSENT")
            f.critical(
                f"billing/{table}",
                "table does not exist in production, so the billing data model "
                "differs from this repository's",
            )
            report.append({"table": table, "exists": False})
            continue

        rls, forced, owner = exists[0]
        policies = q(
            conn,
            "SELECT policyname, cmd, array_to_string(roles, ','), permissive, "
            "       coalesce(qual, ''), coalesce(with_check, '') "
            "FROM pg_policies WHERE schemaname='public' AND tablename=%s "
            "ORDER BY policyname",
            (table,),
        )
        grants = q(
            conn,
            "SELECT grantee, privilege_type FROM information_schema.role_table_grants "
            "WHERE table_schema='public' AND table_name=%s AND grantee = ANY(%s) "
            "ORDER BY grantee, privilege_type",
            (table, list(BROWSER_ROLES)),
        )
        rows = q(conn, f'SELECT count(*) FROM public."{table}"')[0][0]

        print(f"  {table:<20} rls={rls} force={forced} owner={owner} "
              f"policies={len(policies)} rows={rows}")

        browser_grants = [f"{g}:{p}" for g, p in grants]
        if browser_grants:
            print(f"  {'':<20} browser grants: {', '.join(browser_grants)}")

        if not rls:
            f.critical(
                f"billing/{table}",
                f"ROW LEVEL SECURITY IS DISABLED and browser roles hold "
                f"{browser_grants or 'no grants'}. This is the exact defect found "
                f"locally: migration 006 PART 11 creates correct policies and never "
                f"issues ENABLE ROW LEVEL SECURITY, so the policies are inert.",
            )
        elif not policies:
            # RLS on with no policy is fail-closed for non-owners. Report it, but it is
            # not an exposure.
            f.minor(
                f"billing/{table}",
                "RLS enabled with zero policies: fail-closed for non-owners, but no "
                "customer can read their own row either",
            )
        else:
            unconstrained = [
                p[0] for p in policies
                if p[3] == "PERMISSIVE"
                and (p[4] or p[5]) in ("true", "")
                and any(r in p[2].split(",") for r in (*BROWSER_ROLES, "public"))
            ]
            if unconstrained:
                f.critical(
                    f"billing/{table}",
                    f"RLS is enabled but these permissive policies apply no row filter "
                    f"(predicate is the constant TRUE) for a browser role: "
                    f"{unconstrained}. The table reads as if RLS were off while "
                    f"relrowsecurity reports it as secured.",
                )
            else:
                f.ok(f"{table}: RLS enabled, {len(policies)} policies, none unconstrained")

        for name, cmd, roles, perm, qual, with_check in policies:
            print(f"  {'':<20}   {name} [{cmd}/{perm}] to {roles}")
            if qual:
                print(f"  {'':<20}     USING      {qual[:90]}")
            if with_check:
                print(f"  {'':<20}     WITH CHECK {with_check[:90]}")

        report.append({
            "table": table, "exists": True, "rls": rls, "forced": forced,
            "owner": owner, "rows": rows,
            "policies": [list(p) for p in policies],
            "browser_grants": browser_grants,
        })

    return report


# â”€â”€ 3. the two policy shapes an RLS inventory misses â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def check_policy_shapes(conn, f: Findings, owner_columns) -> dict:
    print()
    print("-" * 78)
    print("3. POLICY SHAPES THAT PASS AN RLS INVENTORY AND STILL LEAK")
    print("-" * 78)

    # 3a. constant-TRUE predicate on a table with an ownership column.
    unconstrained = q(
        conn,
        """
        SELECT p.tablename, p.policyname, p.cmd, array_to_string(p.roles, ',')
        FROM pg_policies p
        WHERE p.schemaname = 'public'
          AND p.permissive = 'PERMISSIVE'
          AND coalesce(p.qual, p.with_check) = 'true'
          AND ('anon' = ANY(p.roles) OR 'authenticated' = ANY(p.roles)
               OR 'public' = ANY(p.roles))
          AND EXISTS (SELECT 1 FROM information_schema.columns col
                      WHERE col.table_schema = 'public'
                        AND col.table_name = p.tablename
                        AND col.column_name = ANY(%s))
        ORDER BY 1, 2
        """,
        (list(owner_columns),),
    )
    print(f"  3a. constant-TRUE predicate over a per-customer table: {len(unconstrained)}")
    for table, policy, cmd, roles in unconstrained:
        print(f"      {table}.{policy} [{cmd}] to {roles}")
    if unconstrained:
        f.critical(
            "policy_shape/constant_true",
            f"{len(unconstrained)} permissive policy/policies apply NO row filter over a "
            f"table carrying a per-customer column, while relrowsecurity still reports "
            f"true: {[f'{t}.{p}' for t, p, _c, _r in unconstrained]}. This is the "
            f"`library_ratings` defect.",
        )
    else:
        f.ok("no per-customer table has a constant-TRUE policy")

    # 3b. overlapping permissive SELECT policies. Permissive policies are OR-ed, so the
    # broadest one decides the outcome and a narrow one added beside it changes nothing.
    overlapping = q(
        conn,
        """
        SELECT p.tablename, p.cmd, count(*), string_agg(p.policyname, ', ' ORDER BY p.policyname)
        FROM pg_policies p
        WHERE p.schemaname = 'public'
          AND p.permissive = 'PERMISSIVE'
          AND ('anon' = ANY(p.roles) OR 'authenticated' = ANY(p.roles)
               OR 'public' = ANY(p.roles))
          AND EXISTS (SELECT 1 FROM information_schema.columns col
                      WHERE col.table_schema = 'public'
                        AND col.table_name = p.tablename
                        AND col.column_name = ANY(%s))
        GROUP BY p.tablename, p.cmd
        HAVING count(*) > 1
        ORDER BY 1, 2
        """,
        (list(owner_columns),),
    )
    print(f"  3b. overlapping permissive policies for one command: {len(overlapping)}")
    for table, cmd, count, names in overlapping:
        print(f"      {table} [{cmd}] x{count}: {names}")
    if overlapping:
        # AMBER rather than RED: overlapping policies are legitimate when each covers a
        # distinct legitimate case. It becomes RED only when one of them is broad, and
        # 3a catches the broadest form. Flagged for human reading, which is what caught
        # `referral_codes` locally.
        f.minor(
            "policy_shape/overlapping",
            f"{len(overlapping)} table/command pair(s) carry more than one permissive "
            f"policy. Permissive policies are OR-ed, so the BROADEST decides the "
            f"outcome: {[f'{t}[{c}]' for t, c, _n, _s in overlapping]}. Each predicate "
            f"needs reading -- this is the shape of the `referral_codes` defect, where a "
            f"redesign added a correct own-rows policy and never dropped 006's broader "
            f"one.",
        )
    else:
        f.ok("no per-customer table has overlapping permissive policies")

    # 3c. RLS switched off entirely, with anon holding SELECT.
    rls_off = q(
        conn,
        """
        SELECT c.relname
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity
          AND EXISTS (SELECT 1 FROM information_schema.columns col
                      WHERE col.table_schema='public' AND col.table_name=c.relname
                        AND col.column_name = ANY(%s))
          AND EXISTS (SELECT 1 FROM information_schema.role_table_grants g
                      WHERE g.table_schema='public' AND g.table_name=c.relname
                        AND g.grantee='anon' AND g.privilege_type='SELECT')
        ORDER BY 1
        """,
        (list(owner_columns),),
    )
    print(f"  3c. per-customer table, RLS OFF, anon holds SELECT: {len(rls_off)}")
    for (table,) in rls_off:
        print(f"      {table}")
    if rls_off:
        f.critical(
            "policy_shape/rls_off",
            f"{len(rls_off)} table(s) carry a per-customer column, have RLS DISABLED, "
            f"and grant SELECT to anon -- readable in full by any holder of the browser "
            f"anon key: {[t for (t,) in rls_off]}",
        )
    else:
        f.ok("no per-customer table is readable by anon with RLS off")

    # 3d. a policy predicate that depends on a GUC nothing sets.
    #
    # `current_setting('app.current_tenant_id', true)` returns NULL when unset, so the
    # comparison is NULL and the policy matches zero rows -- fail-CLOSED, which is why
    # this is AMBER and not RED. But it means the policy provides no isolation while
    # appearing to, and a future change that starts setting the GUC on one code path
    # would silently switch those tables from "nobody" to "whoever the GUC says",
    # untested. Nine policies in this schema are in that state.
    guc_policies = q(
        conn,
        """
        SELECT p.tablename, p.policyname, p.cmd,
               substring(coalesce(p.qual, p.with_check) for 80)
        FROM pg_policies p
        WHERE p.schemaname = 'public'
          AND coalesce(p.qual, '') || coalesce(p.with_check, '') LIKE '%current_setting%'
        ORDER BY 1, 2
        """,
    )
    print(f"  3d. policies depending on a session GUC: {len(guc_policies)}")
    for table, policy, cmd, predicate in guc_policies[:12]:
        print(f"      {table}.{policy} [{cmd}]")
        print(f"          {predicate}")
    if guc_policies:
        f.minor(
            "policy_shape/unset_guc",
            f"{len(guc_policies)} policy/policies compare a column to "
            f"current_setting(...). If nothing on the request path sets it the predicate "
            f"is NULL and matches zero rows -- fail-closed, so not an exposure today, "
            f"but those tables get no isolation from these policies and a change that "
            f"starts setting the GUC would alter access silently. Tables: "
            f"{sorted({t for t, _p, _c, _q in guc_policies})}",
        )
    else:
        f.ok("no policy depends on a session GUC")

    # 3e. a policy that scopes by a column the table does not actually own by.
    #
    # A policy on `fills` that filters `tenant_id` is correct; one that filters a column
    # which is NOT an ownership column is scoping by something else entirely, and that
    # is the shape of "wrong tenant column". Detected by requiring that every policy on
    # a per-customer table mention at least ONE ownership column -- a policy on such a
    # table that mentions none is either unconstrained (caught by 3a) or scoping by
    # something that does not identify the customer.
    # `profiles` is owned by its own `id`, and `id` is deliberately excluded from the
    # global ownership-column list -- including a bare `id` there would make every table
    # in the schema match. `ALWAYS_PER_CUSTOMER_TABLES` exists for exactly this case, and
    # 3e has to honour it or it reports the three correct `auth.uid() = id` policies on
    # the single most sensitive table in the schema as defects.
    effective_columns = list(owner_columns) + ["id"]
    wrong_column = q(
        conn,
        """
        SELECT p.tablename, p.policyname, p.cmd, array_to_string(p.roles, ','),
               substring(coalesce(p.qual, p.with_check) for 70)
        FROM pg_policies p
        WHERE p.schemaname = 'public'
          AND p.permissive = 'PERMISSIVE'
          AND ('anon' = ANY(p.roles) OR 'authenticated' = ANY(p.roles)
               OR 'public' = ANY(p.roles))
          AND coalesce(p.qual, p.with_check) IS NOT NULL
          AND coalesce(p.qual, p.with_check) <> 'true'
          AND EXISTS (SELECT 1 FROM information_schema.columns col
                      WHERE col.table_schema = 'public'
                        AND col.table_name = p.tablename
                        AND col.column_name = ANY(%s))
          AND NOT EXISTS (
              SELECT 1 FROM information_schema.columns col
              WHERE col.table_schema = 'public'
                AND col.table_name = p.tablename
                AND col.column_name = ANY(%s)
                AND coalesce(p.qual, '') || ' ' || coalesce(p.with_check, '')
                    LIKE '%%' || col.column_name || '%%'
          )
        ORDER BY 1, 2
        """,
        (list(owner_columns), effective_columns),
    )

    accepted = [r for r in wrong_column
                if (r[0], r[1]) in ACCEPTED_BROAD_POLICIES]
    offending = [r for r in wrong_column
                 if (r[0], r[1]) not in ACCEPTED_BROAD_POLICIES]

    print(f"  3e. policies on a per-customer table that name NO ownership column: "
          f"{len(wrong_column)} ({len(accepted)} accepted by design)")
    for table, policy, cmd, roles, predicate in offending:
        print(f"      {table}.{policy} [{cmd}] to {roles}")
        print(f"          {predicate}")
    for table, policy, cmd, roles, predicate in accepted:
        print(f"      ACCEPTED {table}.{policy} [{cmd}] to {roles}")
        for line in _wrap(ACCEPTED_BROAD_POLICIES[(table, policy)], 62):
            print(f"          | {line}")

    if offending:
        f.critical(
            "policy_shape/wrong_column",
            f"{len(offending)} policy/policies on a table carrying a per-customer "
            f"column scope by something that is NOT an ownership column: "
            f"{[f'{t}.{p}' for t, p, _c, _r, _q in offending]}. The predicate is not "
            f"constant TRUE, so 3a did not catch it, but it does not restrict rows to "
            f"their owner either.",
        )
    else:
        f.ok(f"every constrained policy on a per-customer table names an ownership "
             f"column ({len(accepted)} broad policy/policies accepted by design)")

    # 3f. a policy granted to a role it should not reach.
    #
    # `anon` named EXPLICITLY on a per-customer table's policy is the clearest form:
    # `anon` has no `auth.uid()`, so any policy written for it either filters nothing or
    # is dead. Either way it should not exist.
    anon_policies = q(
        conn,
        """
        SELECT p.tablename, p.policyname, p.cmd,
               substring(coalesce(p.qual, p.with_check) for 70)
        FROM pg_policies p
        WHERE p.schemaname = 'public'
          AND 'anon' = ANY(p.roles)
          AND EXISTS (SELECT 1 FROM information_schema.columns col
                      WHERE col.table_schema = 'public'
                        AND col.table_name = p.tablename
                        AND col.column_name = ANY(%s))
        ORDER BY 1, 2
        """,
        (list(owner_columns),),
    )
    print(f"  3f. policies naming `anon` on a per-customer table: {len(anon_policies)}")
    for table, policy, cmd, predicate in anon_policies:
        print(f"      {table}.{policy} [{cmd}]  {predicate}")
    if anon_policies:
        f.critical(
            "policy_shape/anon_role",
            f"{len(anon_policies)} policy/policies grant `anon` access to a table "
            f"carrying a per-customer column: "
            f"{[f'{t}.{p}' for t, p, _c, _q in anon_policies]}. `anon` has no "
            f"auth.uid(), so such a policy either filters nothing or is dead code "
            f"pretending to be a control.",
        )
    else:
        f.ok("no policy names `anon` on a per-customer table")

    return {
        "constant_true": [list(r) for r in unconstrained],
        "overlapping": [list(r) for r in overlapping],
        "rls_off": [r[0] for r in rls_off],
        "unset_guc": [list(r) for r in guc_policies],
        "wrong_column": [list(r) for r in wrong_column],
        "anon_role": [list(r) for r in anon_policies],
    }


# â”€â”€ 4. privileges RLS cannot filter â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def check_unfilterable_privileges(conn, f: Findings) -> list[list]:
    print()
    print("-" * 78)
    print("4. PRIVILEGES ROW LEVEL SECURITY CANNOT FILTER")
    print("-" * 78)

    held = q(
        conn,
        "SELECT table_name, grantee, privilege_type "
        "FROM information_schema.role_table_grants "
        "WHERE table_schema='public' AND grantee = ANY(%s) AND privilege_type = ANY(%s) "
        "ORDER BY 1, 2, 3",
        (list(BROWSER_ROLES), list(UNFILTERABLE)),
    )
    print(f"  TRUNCATE / REFERENCES / TRIGGER held by a browser role: {len(held)}")
    for table, grantee, priv in held[:25]:
        print(f"      {table:<40} {grantee:<14} {priv}")
    if len(held) > 25:
        print(f"      ... and {len(held) - 25} more")

    if held:
        truncate = [(t, g) for t, g, p in held if p == "TRUNCATE"]
        f.critical(
            "privileges/unfilterable",
            f"{len(held)} grant(s) of TRUNCATE/REFERENCES/TRIGGER to a browser role. "
            f"TRUNCATE empties a table regardless of any policy -- "
            f"{len(truncate)} of these are TRUNCATE. Locally, `TRUNCATE public.profiles "
            f"CASCADE` as anon SUCCEEDED before this was revoked.",
        )
    else:
        f.ok("no browser role holds TRUNCATE, REFERENCES or TRIGGER")

    return [list(r) for r in held]


# â”€â”€ 5. execution integrity â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def check_execution_integrity(conn, f: Findings) -> dict:
    print()
    print("-" * 78)
    print("5. EXECUTION INTEGRITY -- the database half of order idempotency")
    print("-" * 78)

    result: dict = {}

    def constraints(table: str) -> list[tuple]:
        return q(
            conn,
            "SELECT c.conname, c.contype, pg_get_constraintdef(c.oid) "
            "FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname='public' AND t.relname=%s AND c.contype IN ('p','u') "
            "ORDER BY c.contype, c.conname",
            (table,),
        )

    def table_exists(table: str) -> bool:
        return bool(q(
            conn,
            "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind='r' AND c.relname=%s",
            (table,),
        ))

    # execution_records: PRIMARY KEY on execution_id is the guarantee behind idempotency.
    if not table_exists("execution_records"):
        print("  execution_records   ABSENT")
        f.critical("execution/execution_records",
                   "table does not exist, so order idempotency has no database-level "
                   "guarantee at all")
        result["execution_records"] = {"exists": False}
    else:
        cons = constraints("execution_records")
        pk = [c for c in cons if c[1] == "p"]
        print(f"  execution_records   constraints={len(cons)}")
        for name, kind, definition in cons:
            print(f"      [{kind}] {name}: {definition}")
        on_execution_id = [
            c for c in pk if "execution_id" in c[2]
        ]
        if not pk:
            f.critical("execution/execution_records",
                       "no PRIMARY KEY. Two workers racing past the application-level "
                       "idempotency check would both insert.")
        elif not on_execution_id:
            f.critical(
                "execution/execution_records",
                f"PRIMARY KEY exists but is not on execution_id: {pk[0][2]}. The "
                f"idempotency guarantee is keyed on the wrong column.",
            )
        else:
            f.ok(f"execution_records: PRIMARY KEY on execution_id ({pk[0][0]})")
        result["execution_records"] = {
            "exists": True, "constraints": [list(c) for c in cons],
        }

    # idempotency_keys: uniqueness is the point of the table.
    if not table_exists("idempotency_keys"):
        print("  idempotency_keys    ABSENT")
        f.critical("execution/idempotency_keys", "table does not exist")
        result["idempotency_keys"] = {"exists": False}
    else:
        cons = constraints("idempotency_keys")
        print(f"  idempotency_keys    constraints={len(cons)}")
        for name, kind, definition in cons:
            print(f"      [{kind}] {name}: {definition}")
        if not cons:
            f.critical("execution/idempotency_keys",
                       "no PRIMARY KEY and no UNIQUE constraint. A table named "
                       "idempotency_keys that does not enforce uniqueness enforces "
                       "nothing.")
        else:
            f.ok(f"idempotency_keys: {len(cons)} uniqueness constraint(s)")
        result["idempotency_keys"] = {
            "exists": True, "constraints": [list(c) for c in cons],
        }

    for table in ("orders", "fills", "positions"):
        if not table_exists(table):
            print(f"  {table:<20}ABSENT")
            f.critical(f"execution/{table}", "table does not exist in production")
            result[table] = {"exists": False}
            continue
        cons = constraints(table)
        print(f"  {table:<20}constraints={len(cons)}")
        for name, kind, definition in cons:
            print(f"      [{kind}] {name}: {definition}")
        result[table] = {"exists": True, "constraints": [list(c) for c in cons]}

    return result


# â”€â”€ 6. tenant_id type consistency â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def check_tenant_id_types(conn, f: Findings) -> list[dict]:
    print()
    print("-" * 78)
    print("6. TENANT / USER IDENTITY COLUMN TYPES")
    print("-" * 78)

    rows = q(
        conn,
        """
        SELECT c.table_name, c.column_name, c.data_type,
               coalesce(c.character_maximum_length::text, ''),
               c.is_nullable,
               (SELECT string_agg(con.conname, ',')
                  FROM pg_constraint con
                  JOIN pg_class t ON t.oid = con.conrelid
                  JOIN pg_namespace n ON n.oid = t.relnamespace
                 WHERE n.nspname = 'public' AND t.relname = c.table_name
                   AND con.contype = 'f'
                   AND pg_get_constraintdef(con.oid) LIKE '%' || c.column_name || '%')
        FROM information_schema.columns c
        WHERE c.table_schema = 'public'
          AND c.column_name IN ('tenant_id', 'user_id')
        ORDER BY c.table_name, c.column_name
        """,
    )

    by_type: dict[str, list[str]] = {}
    report: list[dict] = []
    for table, column, dtype, maxlen, nullable, fks in rows:
        shown = f"{dtype}({maxlen})" if maxlen else dtype
        by_type.setdefault(f"{column}:{shown}", []).append(table)
        report.append({
            "table": table, "column": column, "type": shown,
            "nullable": nullable, "foreign_keys": fks or "",
        })

    print(f"  {len(rows)} tenant_id/user_id column(s) across the schema")
    for key in sorted(by_type):
        column, shown = key.split(":", 1)
        tables = by_type[key]
        print(f"      {column:<10} {shown:<20} {len(tables)} table(s)")

    # The defect that blocked 5 of 7 foreign keys locally: the same logical identity
    # declared as uuid on one table and varchar on another, so no FK can be created
    # between them.
    for column in ("tenant_id", "user_id"):
        shapes = {k.split(":", 1)[1] for k in by_type if k.startswith(f"{column}:")}
        if len(shapes) > 1:
            offenders = {
                k.split(":", 1)[1]: by_type[k]
                for k in by_type if k.startswith(f"{column}:")
            }
            print(f"  MIXED TYPES for {column}: {sorted(shapes)}")
            for shape, tables in sorted(offenders.items()):
                print(f"      {shape:<20} {sorted(tables)[:6]}"
                      f"{' ...' if len(tables) > 6 else ''}")
            f.critical(
                f"tenant_type/{column}",
                f"`{column}` is declared with {len(shapes)} different types across the "
                f"schema: {sorted(shapes)}. A foreign key cannot be created between two "
                f"different types, which is what blocked 5 of 7 foreign keys locally "
                f"(BUG-033). Offending shapes: "
                f"{ {s: sorted(t)[:4] for s, t in offenders.items()} }",
            )
        elif shapes:
            f.ok(f"{column} is consistently {shapes.pop()} across the schema")

    return report


# â”€â”€ 7. high-risk table inventory â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def check_high_risk_tables(conn, f: Findings, owner_columns) -> list[dict]:
    print()
    print("-" * 78)
    print("7. HIGH-RISK AND PER-CUSTOMER TABLE INVENTORY")
    print("-" * 78)

    discovered = q(
        conn,
        """
        SELECT DISTINCT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
          AND EXISTS (SELECT 1 FROM information_schema.columns col
                      WHERE col.table_schema='public' AND col.table_name=c.relname
                        AND col.column_name = ANY(%s))
        ORDER BY 1
        """,
        (list(owner_columns),),
    )
    tables = sorted({t for (t,) in discovered} | set(HIGH_RISK_TABLES))

    report: list[dict] = []
    missing: list[str] = []
    print(f"  {len(tables)} table(s): the 11 named by the gate plus every table carrying "
          f"an ownership column")
    print(f"  {'table':<34}{'rls':<6}{'force':<7}{'pol':<5}{'anon':<22}{'rows'}")

    for table in tables:
        meta = q(
            conn,
            "SELECT c.relrowsecurity, c.relforcerowsecurity "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind='r' AND c.relname=%s",
            (table,),
        )
        if not meta:
            print(f"  {table:<34}ABSENT")
            missing.append(table)
            report.append({"table": table, "exists": False})
            continue
        rls, forced = meta[0]
        npol = q(conn,
                 "SELECT count(*) FROM pg_policies WHERE schemaname='public' AND tablename=%s",
                 (table,))[0][0]
        anon = q(
            conn,
            "SELECT string_agg(privilege_type, ',' ORDER BY privilege_type) "
            "FROM information_schema.role_table_grants "
            "WHERE table_schema='public' AND table_name=%s AND grantee='anon'",
            (table,),
        )[0][0] or "-"
        rows = q(conn, f'SELECT count(*) FROM public."{table}"')[0][0]
        print(f"  {table:<34}{str(rls):<6}{str(forced):<7}{npol:<5}{anon[:20]:<22}{rows}")
        report.append({
            "table": table, "exists": True, "rls": rls, "forced": forced,
            "policies": npol, "anon_privileges": anon, "rows": rows,
        })

    if missing:
        named_missing = [t for t in missing if t in HIGH_RISK_TABLES]
        if named_missing:
            f.critical(
                "high_risk/missing",
                f"table(s) the launch gate names explicitly do not exist in production: "
                f"{named_missing}",
            )

    return report


# â”€â”€ reporting â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def report(f: Findings, timestamp: str, dsn: str, server: str) -> int:
    print()
    print("=" * 78)
    print(f"RESULT: {f.verdict}")
    print("=" * 78)
    print(f"  database  : {redact(dsn)}")
    print(f"  server    : {server}")
    print(f"  timestamp : {timestamp}")
    print(f"  access    : READ-ONLY (default_transaction_read_only verified 'on')")
    print()

    if f.red:
        print(f"  LAUNCH-CRITICAL ({len(f.red)}):")
        for area, detail in f.red:
            print(f"    [{area}]")
            for line in _wrap(detail, 70):
                print(f"      {line}")
        print()
    if f.amber:
        print(f"  NON-CRITICAL DRIFT ({len(f.amber)}):")
        for area, detail in f.amber:
            print(f"    [{area}]")
            for line in _wrap(detail, 70):
                print(f"      {line}")
        print()
    if f.unverified:
        print(f"  COULD NOT BE CHECKED ({len(f.unverified)}) -- NOT a pass:")
        for area, why in f.unverified:
            print(f"    [{area}] {why}")
        print()
    if f.green:
        print(f"  PASSED ({len(f.green)}):")
        for detail in f.green:
            print(f"    {detail}")
        print()

    print("  WHAT THIS DOES NOT ESTABLISH")
    print("    Behavioural cross-tenant isolation. This script only READS the")
    print("    catalogue. A correct-looking policy set is not proof that one customer")
    print("    cannot read another's row -- that requires executing the attempt as the")
    print("    role, which is scripts/verify_production_security.py.")
    print("=" * 78)

    if f.verdict == "RED":
        print()
        print("  STOP. Do not remediate automatically. See section 11 of the launch")
        print("  gate: each finding needs its impact, the SQL change, a rollback")
        print("  strategy, a backup, a locking assessment, a verification query and a")
        print("  post-migration behavioural test -- then explicit approval.")
        return 2
    if f.verdict == "AMBER":
        return 1
    return 0


def _wrap(text: str, width: int) -> list[str]:
    import textwrap
    return textwrap.wrap(" ".join(text.split()), width=width) or [""]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="READ-ONLY production schema and security verification."
    )
    ap.add_argument("--dsn", required=True,
                    help="production DSN. Direct connection (5432), not the pooler (6543).")
    ap.add_argument("--baseline", default=str(DEFAULT_BASELINE),
                    help="snapshot JSON from scripts/verify_provisioning.py")
    ap.add_argument("--out", help="write the full evidence bundle here as JSON")
    args = ap.parse_args()

    baseline_path = pathlib.Path(args.baseline)
    if not baseline_path.exists():
        print(f"no baseline at {baseline_path}")
        print("produce one first:  python scripts/verify_provisioning.py")
        return 3

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    try:
        conn = connect_read_only(args.dsn)
    except psycopg2.Error as exc:
        print(f"could not connect: {str(exc).splitlines()[0]}")
        print(f"  database = {redact(args.dsn)}")
        return 3

    try:
        server = q(conn, "SELECT current_setting('server_version')")[0][0]
        print("=" * 78)
        print("PRODUCTION SCHEMA AND SECURITY VERIFICATION -- READ ONLY")
        print("=" * 78)
        print(f"database  : {redact(args.dsn)}")
        print(f"server    : PostgreSQL {server}")
        print(f"baseline  : {baseline_path}")
        print(f"timestamp : {timestamp}")
        print(f"access    : READ-ONLY, server-enforced; no row data is read")
        print()

        f = Findings()

        # Which columns name a customer is DERIVED from production's own foreign keys,
        # unioned with a written floor -- not read from a hand-maintained list. That list
        # has been wrong twice (`creator_id`, then `purchaser_id`), and each time a table
        # went unexamined while the report looked clean.
        owner_columns, derived_only = derive_owner_columns(conn)
        print(f"ownership columns : {len(owner_columns)} "
              f"(derived from foreign keys to profiles/auth.users, union a written floor)")
        if derived_only:
            print(f"  production declares these that the floor does not list: "
                  f"{list(derived_only)}")
            f.minor(
                "owner_columns/floor_out_of_date",
                f"production has foreign keys to a customer identity on column(s) the "
                f"written floor in scripts/owner_columns.py does not list: "
                f"{list(derived_only)}. They WERE checked -- the derivation picked them "
                f"up -- but add them so a database without the foreign key is covered too.",
            )
        print(f"always per-customer: {list(ALWAYS_PER_CUSTOMER_TABLES)}")
        print()

        bundle = {
            "timestamp": timestamp,
            "database": redact(args.dsn),
            "server_version": server,
            "baseline": str(baseline_path),
            "owner_columns": list(owner_columns),
            "owner_columns_derived_only": list(derived_only),
        }

        bundle["live_snapshot"] = check_drift(conn, args.dsn, baseline, f)
        bundle["billing"] = check_billing(conn, f)
        bundle["policy_shapes"] = check_policy_shapes(conn, f, owner_columns)
        bundle["unfilterable_privileges"] = check_unfilterable_privileges(conn, f)
        bundle["execution_integrity"] = check_execution_integrity(conn, f)
        bundle["tenant_id_types"] = check_tenant_id_types(conn, f)
        bundle["high_risk_tables"] = check_high_risk_tables(conn, f, owner_columns)
    finally:
        conn.close()

    code = report(f, timestamp, args.dsn, f"PostgreSQL {server}")

    bundle["verdict"] = f.verdict
    bundle["red"] = f.red
    bundle["amber"] = f.amber
    bundle["unverified"] = f.unverified
    bundle["green"] = f.green

    if args.out:
        out = pathlib.Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(bundle, indent=1, sort_keys=True, default=str),
                       encoding="utf-8")
        print(f"evidence bundle written to {out}")

    return code


if __name__ == "__main__":
    sys.exit(main())

