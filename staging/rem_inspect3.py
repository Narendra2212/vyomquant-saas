"""Remediation part 3: facts needed for the MFA step-up design and later fixes."""
from __future__ import annotations

import sys

import psycopg2

sys.path.insert(0, "staging")
from dsn import resolve_dsn  # noqa: E402


def main() -> int:
    conn = psycopg2.connect(resolve_dsn())
    conn.autocommit = True
    out = []
    with conn.cursor() as cur:

        def q(label, sql, params=None):
            try:
                cur.execute(sql, params or {})
                rows = cur.fetchall()
            except psycopg2.Error as exc:
                out.append(f"{label}| QUERYFAIL {exc.pgcode} {str(exc).splitlines()[0]}")
                return
            if not rows:
                out.append(f"{label}| (none)")
            for r in rows:
                out.append(f"{label}| " + " ~ ".join("" if v is None else str(v) for v in r))

        q("AUTH_SCHEMA_TABLES",
          "SELECT table_name FROM information_schema.tables "
          " WHERE table_schema='auth' ORDER BY 1")
        q("MFA_FACTORS_EXISTS", "SELECT to_regclass('auth.mfa_factors')::text")
        q("STRATEGY_DEPLOYMENTS_COLS",
          "SELECT column_name, data_type FROM information_schema.columns "
          " WHERE table_schema='public' AND table_name='strategy_deployments' "
          " ORDER BY ordinal_position")
        q("DEPLOY_INDEXES",
          "SELECT indexname, indexdef FROM pg_indexes "
          " WHERE schemaname='public' AND tablename='strategy_deployments' ORDER BY 1")
        q("REFERRAL_RELATIONSHIPS_COLS",
          "SELECT column_name, data_type FROM information_schema.columns "
          " WHERE table_schema='public' AND table_name='referral_relationships' "
          " ORDER BY ordinal_position")
        q("REFERRAL_RELATIONSHIPS_CONSTRAINTS",
          "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
          " WHERE conrelid = to_regclass('public.referral_relationships') ORDER BY 1")

    conn.close()
    with open("staging/rem_inspect3.txt", "w", encoding="ascii", errors="replace") as fh:
        fh.write("\n".join(out) + "\n")
    print(f"wrote {len(out)} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
