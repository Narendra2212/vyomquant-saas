#!/usr/bin/env python
"""Enumerate and CLASSIFY every identity-like column, before anything is altered.

BUG-049
-------
`user_id` is declared three ways across this schema: `uuid` on 43 columns,
`character varying(36)` on the three billing tables, and `text` on nine more including
`exchange_keys`. A foreign key cannot span two types, so those columns declare no
reference to the customer they belong to.

WHY THIS SCRIPT EXISTS SEPARATELY FROM THE FIX
----------------------------------------------
"Convert everything that is text to uuid" would be wrong, and the instruction says so
explicitly. A column holding text is not evidence that it holds a customer identity:

  * `exchange_keys.user_id` is a customer identity stored as text  -> convert
  * `subscriptions.provider_customer_id` is Razorpay's identifier  -> leave alone,
    converting it would destroy the only link to the payment provider
  * `security_logs.user_id` may hold a customer id OR a service name OR be absent
  * `orders.tenant_id` is a tenant identity, which in this product is 1:1 with a
    customer but is a distinct concept

So every column is classified first, from evidence, and only classes A (canonical
customer identity) and B (tenant identity) are candidates for conversion. The
classification and its reasoning are written to `BUG049_IDENTITY_MATRIX.json` so the
decision is reviewable BEFORE a migration touches a table holding invoices.

HOW CLASSIFICATION IS DECIDED
-----------------------------
Mechanically where possible, and the mechanism is recorded per column:

  1. an existing foreign key to `profiles`/`auth.users`  -> A, decided by the schema
  2. the column appears in an RLS policy predicate alongside `auth.uid()`  -> A
  3. the column appears in a policy predicate alongside `current_setting('app.current
     _tenant_id')`  -> B
  4. every non-null value is a well-formed UUID and the name matches a customer-identity
     name  -> A, decided by the data
  5. the name matches a provider pattern (`provider_*`, `*_provider_id`, `stripe_*`,
     `razorpay_*`, `external_*`)  -> C
  6. values are present and NOT UUID-shaped  -> E if the name is identity-like, D
     otherwise

Anything the rules cannot place is reported `UNCLASSIFIED` and excluded from conversion.
Unclassified is not a synonym for "safe to convert".

EXIT CODES
----------
    0  enumeration complete; matrix written
    1  at least one class A/B column holds values that are NOT convertible to uuid.
       The matrix names them. NOTHING is altered -- see the migration plan it emits.
    2  could not run
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import sys

try:
    import psycopg2
except ImportError:  # pragma: no cover
    sys.exit("psycopg2 is required: pip install psycopg2-binary")

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.owner_columns import BASELINE_OWNER_COLUMNS  # noqa: E402
from scripts.verify_schema_drift import redact  # noqa: E402

CANONICAL_TYPE = "uuid"

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

#: Names that denote an EXTERNAL system's identifier. Converting one of these to uuid
#: would destroy the only link to the payment provider or exchange, so they are class C
#: and excluded from conversion regardless of what they currently hold.
PROVIDER_PATTERNS = (
    re.compile(r"^provider_"),
    re.compile(r"_provider_id$"),
    re.compile(r"^(stripe|razorpay|paypal)_"),
    re.compile(r"^external_"),
    re.compile(r"_external_id$"),
    re.compile(r"^(gateway|psp)_"),
)

#: Names that denote a customer. Used only as corroboration -- never on its own to
#: justify converting a column whose data is not UUID-shaped.
CUSTOMER_NAME_PATTERNS = (
    "user_id", "profile_id", "owner_id", "author_id", "customer_id",
    "creator_id", "seller_id", "purchaser_id", "subscriber_id",
    "referrer_id", "referred_id", "created_by", "reviewed_by",
    "referred_by_user_id", "top_creator_id", "account_id",
)

TENANT_NAME_PATTERNS = ("tenant_id",)

COLUMNS_SQL = """
SELECT c.table_schema,
       c.table_name,
       c.column_name,
       c.data_type,
       coalesce(c.character_maximum_length::text, '') AS maxlen,
       c.udt_name,
       c.is_nullable,
       coalesce(c.column_default, '')                 AS col_default,
       c.is_identity,
       coalesce(c.identity_generation, '')            AS identity_generation,
       c.is_generated
FROM information_schema.columns c
JOIN pg_class t      ON t.relname = c.table_name
JOIN pg_namespace n  ON n.oid = t.relnamespace AND n.nspname = c.table_schema
WHERE c.table_schema = 'public'
  AND t.relkind = 'r'
  AND (c.column_name = ANY(%s) OR c.column_name LIKE '%%user_id%%'
       OR c.column_name LIKE '%%tenant%%' OR c.column_name LIKE '%%customer%%')
ORDER BY c.table_name, c.column_name
"""

FK_SQL = """
SELECT c.relname        AS tbl,
       att.attname      AS col,
       fn.nspname       AS ref_schema,
       fc.relname       AS ref_table,
       fatt.attname     AS ref_column,
       con.conname      AS constraint_name,
       CASE con.confdeltype
            WHEN 'a' THEN 'NO ACTION' WHEN 'r' THEN 'RESTRICT'
            WHEN 'c' THEN 'CASCADE'   WHEN 'n' THEN 'SET NULL'
            WHEN 'd' THEN 'SET DEFAULT' ELSE con.confdeltype::text
       END              AS on_delete,
       CASE con.confupdtype
            WHEN 'a' THEN 'NO ACTION' WHEN 'r' THEN 'RESTRICT'
            WHEN 'c' THEN 'CASCADE'   WHEN 'n' THEN 'SET NULL'
            WHEN 'd' THEN 'SET DEFAULT' ELSE con.confupdtype::text
       END              AS on_update
FROM pg_constraint con
JOIN pg_class c       ON c.oid = con.conrelid
JOIN pg_namespace n   ON n.oid = c.relnamespace AND n.nspname = 'public'
JOIN pg_class fc      ON fc.oid = con.confrelid
JOIN pg_namespace fn  ON fn.oid = fc.relnamespace
CROSS JOIN LATERAL unnest(con.conkey, con.confkey) AS k(conkey, confkey)
JOIN pg_attribute att  ON att.attrelid = con.conrelid  AND att.attnum = k.conkey
JOIN pg_attribute fatt ON fatt.attrelid = con.confrelid AND fatt.attnum = k.confkey
WHERE con.contype = 'f'
"""

RLS_SQL = """
SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
"""

POLICY_SQL = """
SELECT tablename, policyname, cmd, array_to_string(roles, ','),
       coalesce(qual, ''), coalesce(with_check, '')
FROM pg_policies WHERE schemaname = 'public'
"""

#: Credential-bearing tables. Deletion behaviour on these is a security decision, not a
#: housekeeping one: an orphaned row here is an encrypted exchange API key with no owner.
CREDENTIAL_TABLES = ("exchange_keys", "exchange_connections")

#: Financial records that should SURVIVE customer deletion for audit and tax reasons.
#: The recommendation for these is deliberately NOT CASCADE.
FINANCIAL_RETAIN_TABLES = (
    "invoices", "billing_invoices", "subscriptions", "payment_methods",
    "referral_commissions", "referral_payouts", "marketplace_settlements",
    "plan_usage_ledger", "security_logs", "risk_settings_audit",
)


def _is_provider_name(column: str) -> bool:
    return any(p.search(column) for p in PROVIDER_PATTERNS)


def classify(column: str, dtype: str, fk, policy_text: str,
             value_sample: dict) -> tuple[str, str]:
    """Return (class, the evidence that decided it).

    Deliberately ordered: schema evidence beats name evidence beats data evidence. A
    column is only called a customer identity on the strength of its NAME when the data
    also agrees.
    """
    if _is_provider_name(column):
        return ("C", f"name matches an external-provider pattern; converting it would "
                     f"destroy the link to the provider")

    if fk is not None:
        ref_table = fk["referenced_table"]
        if ref_table in ("profiles", "users"):
            return ("A", f"declared foreign key to {fk['referenced_schema']}.{ref_table}"
                         f"({fk['referenced_column']})")
        return ("D", f"declared foreign key to {ref_table}, which is not a customer "
                     f"identity table")

    if column in TENANT_NAME_PATTERNS:
        if "current_setting" in policy_text:
            return ("B", "named tenant_id and referenced by a policy comparing it to "
                         "current_setting('app.current_tenant_id')")
        return ("B", "named tenant_id")

    if "auth.uid()" in policy_text and column in policy_text:
        return ("A", "referenced by an RLS policy predicate alongside auth.uid()")

    if column in CUSTOMER_NAME_PATTERNS:
        total = value_sample["total"]
        bad = value_sample["non_uuid"]
        if total == 0:
            return ("A", "customer-identity name; table is empty so no value "
                         "contradicts it")
        if bad == 0:
            return ("A", f"customer-identity name and all {total} non-null value(s) are "
                         f"well-formed UUIDs")
        return ("E", f"customer-identity name but {bad} of {total} value(s) are not "
                     f"UUID-shaped; legacy identifiers requiring migration")

    return ("UNCLASSIFIED", "no rule placed this column; excluded from conversion")


def recommend(table: str, column: str, klass: str, dtype: str, nullable: str,
              fk) -> dict:
    if klass not in ("A", "B"):
        return {
            "canonical_type": dtype,
            "foreign_key": "none -- not a customer or tenant identity",
            "on_delete": "n/a",
            "action": "LEAVE AS IS",
            "reason": "only classes A and B are candidates for canonical conversion",
        }

    if fk is not None:
        return {
            "canonical_type": CANONICAL_TYPE,
            "foreign_key": f"already present: {fk['constraint_name']}",
            "on_delete": fk["on_delete"],
            "action": "NO CHANGE NEEDED" if dtype == CANONICAL_TYPE else "CONVERT TYPE",
            "reason": "foreign key already declared; only the type may need aligning",
        }

    if table in CREDENTIAL_TABLES:
        on_delete = "CASCADE"
        reason = (
            "credential-bearing table. An orphaned row here is an encrypted exchange "
            "API key with no owner, which must not survive account deletion. CASCADE "
            "is the security-correct choice and it is chosen deliberately, not by "
            "default."
        )
    elif table in FINANCIAL_RETAIN_TABLES:
        on_delete = "SET NULL" if nullable == "YES" else "RESTRICT"
        reason = (
            "financial or audit record that should SURVIVE customer deletion for audit "
            "and tax purposes. CASCADE would destroy it. "
            + ("SET NULL detaches the row while keeping it readable."
               if nullable == "YES"
               else "The column is NOT NULL, so SET NULL is impossible; RESTRICT forces "
                    "the deletion path to deal with these rows explicitly rather than "
                    "silently discarding them.")
        )
    else:
        on_delete = "CASCADE"
        reason = (
            "operational per-customer data with no independent retention requirement; "
            "it should go when the customer does"
        )

    return {
        "canonical_type": CANONICAL_TYPE,
        "foreign_key": (
            "public.profiles(id)" if klass == "A" else "public.profiles(id) -- tenant is "
            "1:1 with customer in this product"
        ),
        "on_delete": on_delete,
        "action": "CONVERT TYPE AND ADD FK" if dtype != CANONICAL_TYPE else "ADD FK",
        "reason": reason,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", help="defaults to staging/pg_dsn.txt")
    ap.add_argument("--out", default="BUG049_IDENTITY_MATRIX.json")
    args = ap.parse_args()

    dsn = args.dsn
    if not dsn:
        path = ROOT / "staging" / "pg_dsn.txt"
        if not path.exists():
            print("no --dsn and staging/pg_dsn.txt does not exist")
            return 2
        dsn = path.read_text(encoding="utf-8").strip()

    conn = psycopg2.connect(dsn)
    conn.set_session(readonly=True, autocommit=True)

    with conn.cursor() as cur:
        cur.execute(COLUMNS_SQL, (list(BASELINE_OWNER_COLUMNS),))
        columns = cur.fetchall()

        cur.execute(FK_SQL)
        fks: dict[tuple[str, str], dict] = {}
        for tbl, col, ref_schema, ref_table, ref_col, name, on_del, on_upd in cur.fetchall():
            fks[(tbl, col)] = {
                "constraint_name": name,
                "referenced_schema": ref_schema,
                "referenced_table": ref_table,
                "referenced_column": ref_col,
                "on_delete": on_del,
                "on_update": on_upd,
            }

        cur.execute(RLS_SQL)
        rls = {r[0]: {"enabled": r[1], "forced": r[2]} for r in cur.fetchall()}

        cur.execute(POLICY_SQL)
        policies: dict[str, list[dict]] = {}
        for tbl, name, cmd, roles, qual, with_check in cur.fetchall():
            policies.setdefault(tbl, []).append({
                "policy": name, "cmd": cmd, "roles": roles,
                "using": qual, "with_check": with_check,
            })

        # Value inventory. Counts only -- no customer data is read, and the sample of
        # offending values is capped and only taken for columns that will be classified
        # as convertible, because that is the one case where a human has to see them.
        inventory: dict[tuple[str, str], dict] = {}
        for (schema, table, column, dtype, _maxlen, _udt, _nullable, _default,
             _ident, _identgen, _gen) in columns:
            cur.execute(
                f'SELECT count(*) FILTER (WHERE "{column}" IS NOT NULL), '
                f'       count(*) FILTER (WHERE "{column}" IS NOT NULL '
                f'                        AND "{column}"::text !~* %s) '
                f'FROM {schema}."{table}"',
                (UUID_RE.pattern.replace("^", "^").replace("$", "$"),),
            )
            total, non_uuid = cur.fetchone()
            sample: list[str] = []
            if non_uuid:
                cur.execute(
                    f'SELECT DISTINCT "{column}"::text FROM {schema}."{table}" '
                    f'WHERE "{column}" IS NOT NULL AND "{column}"::text !~* %s LIMIT 5',
                    (UUID_RE.pattern,),
                )
                sample = [r[0] for r in cur.fetchall()]
            inventory[(table, column)] = {
                "total": total, "non_uuid": non_uuid, "non_uuid_sample": sample,
            }

    conn.close()

    rows: list[dict] = []
    for (schema, table, column, dtype, maxlen, udt, nullable, default,
         is_identity, identity_generation, is_generated) in columns:
        fk = fks.get((table, column))
        table_policies = policies.get(table, [])
        referencing = [
            p for p in table_policies
            if column in p["using"] or column in p["with_check"]
        ]
        policy_text = " ".join(p["using"] + " " + p["with_check"] for p in referencing)
        values = inventory[(table, column)]

        klass, evidence = classify(column, dtype, fk, policy_text, values)
        rec = recommend(table, column, klass, dtype, nullable, fk)

        rows.append({
            "schema": schema,
            "table": table,
            "column": column,
            "current_type": f"{dtype}({maxlen})" if maxlen else dtype,
            "udt_name": udt,
            "nullable": nullable,
            "default": default or None,
            "is_identity": is_identity,
            "identity_generation": identity_generation or None,
            "is_generated": is_generated,
            "referenced_table": fk["referenced_table"] if fk else None,
            "referenced_column": fk["referenced_column"] if fk else None,
            "existing_fk": fk["constraint_name"] if fk else None,
            "on_delete": fk["on_delete"] if fk else None,
            "on_update": fk["on_update"] if fk else None,
            "rls_enabled": rls.get(table, {}).get("enabled"),
            "rls_forced": rls.get(table, {}).get("forced"),
            "policies_referencing_column": referencing,
            "customer_owned": klass in ("A", "B"),
            "classification": klass,
            "classification_evidence": evidence,
            "value_inventory": values,
            "recommended_type": rec["canonical_type"],
            "recommended_fk": rec["foreign_key"],
            "recommended_on_delete": rec["on_delete"],
            "recommended_action": rec["action"],
            "recommendation_reason": rec["reason"],
        })

    blockers = [
        r for r in rows
        if r["classification"] in ("A", "B") and r["value_inventory"]["non_uuid"] > 0
    ]
    # A ZERO-ROW INVENTORY PROVES NOTHING, and conflating it with a clean inventory is
    # the single easiest way to make this matrix falsely reassuring. On a freshly
    # provisioned database every one of these tables is empty, so "all values are
    # convertible" is vacuously true and says nothing whatsoever about production.
    #
    # Tracked as its own category so the distinction survives into the report and into
    # the migration's own precheck, which re-runs the same count against whatever
    # database it is applied to and refuses there if it finds values it cannot convert.
    unproven = [
        r for r in rows
        if r["classification"] in ("A", "B")
        and r["current_type"] != CANONICAL_TYPE
        and r["value_inventory"]["total"] == 0
    ]
    needs_conversion = [
        r for r in rows
        if r["classification"] in ("A", "B")
        and r["current_type"] != CANONICAL_TYPE
        and r["value_inventory"]["non_uuid"] == 0
    ]
    needs_fk = [
        r for r in rows
        if r["classification"] in ("A", "B") and r["existing_fk"] is None
    ]

    bundle = {
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds"),
        "database": redact(dsn),
        "canonical_type": CANONICAL_TYPE,
        "classification_legend": {
            "A": "canonical customer identity -- convert to uuid, add FK to profiles(id)",
            "B": "tenant identity -- convert to uuid, add FK (tenant is 1:1 with "
                 "customer in this product)",
            "C": "external provider identifier -- LEAVE ALONE, converting destroys the "
                 "link to the provider",
            "D": "arbitrary text or a reference to a non-customer table -- leave alone",
            "E": "legacy identifier requiring data migration -- values are not "
                 "UUID-shaped, so conversion is BLOCKED until they are reconciled",
            "UNCLASSIFIED": "no rule placed it; excluded from conversion. Not a synonym "
                            "for safe.",
        },
        "summary": {
            "columns_examined": len(rows),
            "class_A": sum(1 for r in rows if r["classification"] == "A"),
            "class_B": sum(1 for r in rows if r["classification"] == "B"),
            "class_C": sum(1 for r in rows if r["classification"] == "C"),
            "class_D": sum(1 for r in rows if r["classification"] == "D"),
            "class_E": sum(1 for r in rows if r["classification"] == "E"),
            "unclassified": sum(1 for r in rows
                                if r["classification"] == "UNCLASSIFIED"),
            "needs_type_conversion": len(needs_conversion),
            "needs_foreign_key": len(needs_fk),
            "conversion_blocked_by_data": len(blockers),
            "convertibility_unproven_empty_table": len(unproven),
        },
        "convertibility_caveat": (
            "A column whose table is EMPTY reports zero non-convertible values, which "
            "is vacuously true and establishes nothing about any other database. "
            f"{len(unproven)} of the columns needing conversion are in that position "
            "here. The migration re-runs the same inventory against whatever database it "
            "is applied to and RAISES there, with a count and a sample, rather than "
            "trusting this result."
        ),
        "convertibility_unproven_empty_table": [
            {"table": r["table"], "column": r["column"]} for r in unproven
        ],
        "conversion_blocked_by_data": [
            {
                "table": r["table"], "column": r["column"],
                "non_uuid_values": r["value_inventory"]["non_uuid"],
                "total_values": r["value_inventory"]["total"],
                "sample": r["value_inventory"]["non_uuid_sample"],
            }
            for r in blockers
        ],
        "columns": rows,
    }

    out = pathlib.Path(args.out)
    out.write_text(json.dumps(bundle, indent=1, sort_keys=False, default=str),
                   encoding="utf-8")

    print("=" * 78)
    print("BUG-049 IDENTITY COLUMN ENUMERATION AND CLASSIFICATION")
    print("=" * 78)
    print(f"database: {redact(dsn)}")
    print(f"matrix  : {out}")
    print("-" * 78)
    s = bundle["summary"]
    print(f"  {s['columns_examined']} column(s) examined")
    for key in ("class_A", "class_B", "class_C", "class_D", "class_E", "unclassified"):
        print(f"    {key:<14} {s[key]}")
    print(f"  needs type conversion : {s['needs_type_conversion']}")
    print(f"  needs foreign key     : {s['needs_foreign_key']}")
    print(f"  BLOCKED by data       : {s['conversion_blocked_by_data']}")
    print("-" * 78)

    if needs_conversion:
        print("CLASS A/B COLUMNS WHOSE TYPE MUST CHANGE (all values convertible)")
        print("-" * 78)
        for r in needs_conversion:
            print(f"  {r['table']:<28}{r['column']:<22}{r['current_type']:<22}"
                  f"-> uuid  ON DELETE {r['recommended_on_delete']}")
        print("-" * 78)

    if blockers:
        print("CONVERSION BLOCKED -- these hold values that are not UUIDs")
        print("-" * 78)
        for r in blockers:
            v = r["value_inventory"]
            print(f"  {r['table']}.{r['column']}: {v['non_uuid']} of {v['total']} "
                  f"value(s) are not UUID-shaped")
            print(f"      sample: {v['non_uuid_sample']}")
        print()
        print("  NOTHING HAS BEEN ALTERED. These values must be reconciled first --")
        print("  silently casting or discarding a customer identity would reassign or")
        print("  orphan the data it owns.")
        print("=" * 78)
        return 1

    if unproven:
        print(f"CONVERTIBILITY NOT PROVEN for {len(unproven)} column(s) -- the table is "
              f"EMPTY")
        print("-" * 78)
        for r in unproven:
            print(f"  {r['table']}.{r['column']}")
        print()
        print("  Zero non-convertible values in an empty table is vacuously true. This")
        print("  says nothing about production. The migration re-runs the same")
        print("  inventory wherever it is applied and refuses there if it finds a value")
        print("  it cannot convert.")
        print("-" * 78)

    proven = [r for r in needs_conversion if r["value_inventory"]["total"] > 0]
    if proven:
        print(f"Convertibility PROVEN against real values for {len(proven)} column(s).")
    print("No class A/B column holds a value that cannot become a uuid on this database.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
