"""Put the OLD `library_ratings` SELECT policy back, so the new tests can be shown to
fail against the defect rather than merely passing against the fix.

A regression test that passes in both the broken and the repaired state proves nothing.
Run this, run the tests (they must fail), then re-run
`staging/apply_hardening.py` to restore the corrected policy.
"""

from __future__ import annotations

import os
import sys

import psycopg2
import psycopg2.extensions

SQL = """
DROP POLICY IF EXISTS library_ratings_select_reviews_public ON public.library_ratings;
DROP POLICY IF EXISTS library_ratings_select_authenticated ON public.library_ratings;
CREATE POLICY library_ratings_select_authenticated
    ON public.library_ratings FOR SELECT TO authenticated USING (true);
"""


def main() -> int:
    dsn = os.environ.get("AERORA_TEST_DATABASE_URL")
    if not dsn:
        with open(os.path.join("staging", "pg_dsn.txt"), encoding="utf-8") as fh:
            dsn = fh.read().strip()

    conn = psycopg2.connect(dsn)
    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    with conn.cursor() as cur:
        cur.execute(SQL)
    conn.close()
    print("restored the pre-fix policy: library_ratings SELECT USING (true)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
