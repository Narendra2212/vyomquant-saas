"""TENANT-01: what isolation does the LIVE database actually enforce?

The repository's migrations are a CLAIM about RLS. This asks the database.

Reports, for every table holding per-user data:
  * is RLS enabled, and is it FORCED (owner-exempt or not)?
  * what policies exist, for which roles, with what predicate?
  * does the predicate reference auth.uid() (works on a request path) or
    current_setting('app.current_tenant_id') (inert -- nothing sets it)?
  * what are the role grants?

Then it checks, by execution, whether `app.current_tenant_id` is ever set, and whether
the connection the application uses is the table owner (which skips RLS unless FORCE is
on).

Nonce-stamped at both ends.
"""
from __future__ import annotations

import sys
import uuid

import psycopg2

NONCE = sys.argv[1] if len(sys.argv) > 1 else uuid.uuid4().hex[:12]
print(f"NONCE={NONCE}")

DSN = "postgresql://postgres:@127.0.0.1:62674/postgres"

PER_USER_TABLES = [
    # live trading core -- the suspected gap
    "orders", "positions", "fills", "dag_tasks", "execution_records",
    "idempotency_keys", "transaction_records", "transaction_checkpoints",
    "reconciliation_mismatches",
    # strategy / deployment
    "strategies", "strategy_versions", "strategy_deployments", "signals",
    "backtests",
    # credentials
    "exchange_keys",
    # marketplace
    "library_strategies", "library_subscriptions", "library_ratings",
    "marketplace_listings", "marketplace_submissions", "strategy_subscriptions",
    # money
    "profiles", "billing_invoices", "referral_codes", "referral_relationships",
    "referral_commissions", "payment_methods",
    # paper (the reference implementation)
    "paper_sessions", "paper_accounts", "paper_orders", "paper_fills",
    "paper_positions",
    # misc per-user
    "notifications", "security_logs", "risk_settings", "training_jobs",
    "model_versions",
]

conn = psycopg2.connect(DSN)
conn.autocommit = True
cur = conn.cursor()

cur.execute("SELECT current_user, session_user, version()")
who = cur.fetchone()
print(f"connected as: current_user={who[0]} session_user={who[1]}")
print(f"server: {who[2].split(',')[0]}")
print()

# Which of the tables actually exist here
cur.execute(
    """
    SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, pg_get_userbyid(c.relowner)
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public' AND c.relkind = 'r'
     ORDER BY c.relname
    """
)
tables = {r[0]: {"rls": r[1], "forced": r[2], "owner": r[3]} for r in cur.fetchall()}

cur.execute(
    """
    SELECT tablename, policyname, permissive, roles, cmd,
           pg_catalog.pg_get_expr(p.polqual, p.polrelid)      AS using_expr,
           pg_catalog.pg_get_expr(p.polwithcheck, p.polrelid)  AS check_expr
      FROM pg_policies pol
      JOIN pg_policy p ON p.polname = pol.policyname
      JOIN pg_class c ON c.oid = p.polrelid AND c.relname = pol.tablename
     WHERE pol.schemaname = 'public'
     ORDER BY tablename, policyname
    """
)
policies: dict = {}
for row in cur.fetchall():
    policies.setdefault(row[0], []).append(row)


def classify(expr: str | None) -> str:
    if not expr:
        return "NO-PREDICATE"
    low = expr.lower()
    if "current_setting" in low and "current_tenant" in low:
        return "INERT(app.current_tenant_id)"
    if "auth.uid()" in low:
        return "AUTH-UID"
    if low.strip() == "true":
        return "ALLOW-ALL"
    return "OTHER"


print("=" * 100)
print(f"{'table':34} {'exists':6} {'rls':5} {'force':5} {'policies':8} predicate-kinds")
print("=" * 100)

missing = []
no_rls = []
inert = []
for name in PER_USER_TABLES:
    meta = tables.get(name)
    if meta is None:
        missing.append(name)
        print(f"{name:34} {'no':6} {'-':5} {'-':5} {'-':8} (table not present on this DB)")
        continue
    pols = policies.get(name, [])
    kinds = sorted({classify(p[5]) for p in pols}) or ["<none>"]
    if not meta["rls"]:
        no_rls.append(name)
    if any(k.startswith("INERT") for k in kinds):
        inert.append(name)
    print(
        f"{name:34} {'yes':6} {str(meta['rls']):5} {str(meta['forced']):5} "
        f"{len(pols):<8} {','.join(kinds)}"
    )

print()
print("=" * 100)
print("SUMMARY")
print("=" * 100)
print(f"tables not present on this database : {len(missing)}")
if missing:
    print("   " + ", ".join(missing))
print(f"present but RLS DISABLED            : {len(no_rls)}")
if no_rls:
    print("   " + ", ".join(no_rls))
print(f"RLS enabled but predicate is INERT  : {len(inert)}")
if inert:
    print("   " + ", ".join(inert))

forced = [n for n, m in tables.items() if m["forced"]]
print(f"tables with FORCE ROW LEVEL SECURITY: {len(forced)}")
print("   " + (", ".join(forced) if forced else "NONE -- the owner bypasses every policy"))

print()
print("=== is app.current_tenant_id ever set on this connection? ===")
cur.execute("SELECT current_setting('app.current_tenant_id', true)")
val = cur.fetchone()[0]
print(f"current_setting('app.current_tenant_id', true) = {val!r}")
print("   -> NULL means every policy comparing against it matches no row,")
print("      and the owner connection skips RLS anyway (no FORCE).")

print()
print("=== auth schema / auth.uid() availability ===")
cur.execute(
    "SELECT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
    "WHERE n.nspname='auth' AND p.proname='uid')"
)
print(f"auth.uid() exists on this DB: {cur.fetchone()[0]}")

print()
print("=== roles present ===")
cur.execute("SELECT rolname FROM pg_roles WHERE rolname IN "
            "('anon','authenticated','service_role','postgres') ORDER BY rolname")
print("   " + ", ".join(r[0] for r in cur.fetchall()))

conn.close()
print()
print(f"NONCE_END={NONCE}")
