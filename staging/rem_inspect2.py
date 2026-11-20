"""Remediation phase, part 2: compact facts for the referral, SECURITY DEFINER and
strategies-allowlist fixes. Writes ASCII, one fact per line, to keep the log small.
"""
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

        def q(label, sql):
            try:
                cur.execute(sql)
                rows = cur.fetchall()
            except psycopg2.Error as exc:
                out.append(f"{label}| QUERYFAIL {exc.pgcode} {str(exc).splitlines()[0]}")
                return []
            if not rows:
                out.append(f"{label}| (none)")
            for r in rows:
                out.append(f"{label}| " + " ~ ".join("" if v is None else str(v) for v in r))
            return rows

        q("REFCOMM_EXISTS", "SELECT to_regclass('public.referral_commissions')::text")
        q(
            "REFCOMM_COLS",
            """SELECT column_name, data_type FROM information_schema.columns
                WHERE table_schema='public' AND table_name='referral_commissions'
                ORDER BY ordinal_position""",
        )
        q(
            "REFCOMM_CONSTRAINTS",
            """SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
                WHERE conrelid = to_regclass('public.referral_commissions') ORDER BY 1""",
        )
        q(
            "REFCOMM_INDEXES",
            """SELECT indexname FROM pg_indexes
                WHERE schemaname='public' AND tablename='referral_commissions' ORDER BY 1""",
        )
        q(
            "REFWALLET_COLS",
            """SELECT column_name, data_type FROM information_schema.columns
                WHERE table_schema='public' AND table_name='referral_wallets'
                ORDER BY ordinal_position""",
        )
        q(
            "SECDEF_FUNCS",
            """SELECT p.proname, coalesce(p.proacl::text,'NULL_DEFAULT_PUBLIC')
                 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='public' AND p.prosecdef ORDER BY 1""",
        )
        q(
            "PROCESS_REF_COMM_HAS_DUP_GUARD",
            """SELECT CASE WHEN pg_get_functiondef(oid) ILIKE '%referral_commissions%payment_id%'
                           AND pg_get_functiondef(oid) ILIKE '%IF EXISTS%'
                      THEN 'HAS_EXISTS_GUARD' ELSE 'NO_EXISTS_GUARD' END
                 FROM pg_proc WHERE proname='process_referral_commission'""",
        )
        q(
            "LS_UPDATE_POLICY_ROLES",
            """SELECT p.polname,
                      (SELECT string_agg(r.rolname, ',') FROM pg_roles r
                        WHERE r.oid = ANY(p.polroles)) AS roles
                 FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
                WHERE c.relname='library_strategies' AND p.polcmd='w'""",
        )
        q(
            "STRATEGIES_COLS",
            """SELECT column_name FROM information_schema.columns
                WHERE table_schema='public' AND table_name='strategies'
                ORDER BY ordinal_position""",
        )
        q(
            "DEPLOY_LIVE_UNIQ_INDEX",
            """SELECT indexname FROM pg_indexes
                WHERE schemaname='public' AND tablename='strategy_deployments'
                  AND indexname='uq_strategy_deployments_one_live'""",
        )

    conn.close()
    with open("staging/rem_inspect2.txt", "w", encoding="ascii", errors="replace") as fh:
        fh.write("\n".join(out) + "\n")
    print(f"wrote {len(out)} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
