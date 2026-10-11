"""Run a query against the staging database and print the rows. Staging only."""
import pathlib
import sys

import psycopg2

DSN = (pathlib.Path(__file__).resolve().parent / "pg_dsn.txt").read_text(encoding="utf-8").strip()


def run(sql: str, params=None):
    conn = psycopg2.connect(DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.description:
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
                return cols, rows
            return [], []
    finally:
        conn.close()


if __name__ == "__main__":
    sql = sys.argv[1] if len(sys.argv) > 1 else "SELECT version()"
    cols, rows = run(sql)
    if cols:
        print(" | ".join(str(c) for c in cols))
        print("-" * 70)
        for r in rows:
            print(" | ".join("" if v is None else str(v) for v in r))
        print(f"({len(rows)} row{'s' if len(rows) != 1 else ''})")
    else:
        print("OK (no rows)")
