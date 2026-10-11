"""Apply the remediation migrations and print every NOTICE/EXCEPTION they raise.

Each file carries its own postflight assertions, so a silent success here means the
assertions passed, not that the file merely parsed.
"""
from __future__ import annotations

import sys

import psycopg2

sys.path.insert(0, "staging")
from dsn import resolve_dsn  # noqa: E402

FILES = [
    "backend_app/migrations/023_remediate_privilege_escalation.sql",
    "backend_app/migrations/024_remediate_referral_double_commission.sql",
]


def main() -> int:
    failures = 0
    for path in FILES:
        print("=" * 78)
        print(f"APPLYING {path}")
        print("-" * 78)
        conn = psycopg2.connect(resolve_dsn())
        conn.autocommit = True
        try:
            with open(path, encoding="utf-8") as fh:
                sql = fh.read()
            with conn.cursor() as cur:
                cur.execute(sql)
            for notice in conn.notices:
                print("  " + notice.strip())
            print(f"  RESULT: APPLIED OK")
        except psycopg2.Error as exc:
            for notice in conn.notices:
                print("  " + notice.strip())
            print(f"  RESULT: FAILED {exc.pgcode}")
            print(f"  {str(exc).strip()}")
            failures += 1
        finally:
            conn.close()

    print("=" * 78)
    print(f"SUMMARY: {len(FILES) - failures}/{len(FILES)} applied, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
