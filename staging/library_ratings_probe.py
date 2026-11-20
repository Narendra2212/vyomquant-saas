"""Reproduce the `library_ratings` write-path defects against the real schema.

Three endpoints in backend_app/routers/library.py write this table:

  1. POST   /api/library/{id}/favorite   -> {"rating": None, ...}
  2. POST   /api/library/{id}/clone      -> payload with NO "rating" key at all
  3. POST   /api/library/{id}/rate       -> {"rating": <1..5>, ...}

Only (3) supplies a rating. If `library_ratings.rating` is NOT NULL with no default,
(1) returns HTTP 500 and (2) silently loses the verified-clone marker, because its
insert is wrapped in `except _PostgrestAPIError: logger.error(...)`.

The marketplace tests assert the clone marker is written, but they run against an
in-memory fake store (`tests/test_marketplace_pipeline.py` -> `patched_db.stores`),
which does not enforce NOT NULL. That is why this never surfaced.

Exit 0 -> all three write paths are accepted by the schema.
Exit 1 -> at least one documented write path is rejected.
"""

from __future__ import annotations

import sys
import uuid

import psycopg2
import psycopg2.extensions

from dsn import resolve_dsn


def _rollback_probe(conn, label: str, payload: dict) -> tuple[bool, str]:
    """Attempt one insert inside a savepoint, then always undo it.

    A savepoint is correct here: the point of the probe IS that the statement may
    fail, and the failure is the measurement. Nothing is left behind either way.
    """
    cols = ", ".join(payload)
    binds = ", ".join(["%s"] * len(payload))
    with conn.cursor() as cur:
        cur.execute("SAVEPOINT probe")
        try:
            cur.execute(
                f"INSERT INTO public.library_ratings ({cols}) VALUES ({binds})",
                list(payload.values()),
            )
        except psycopg2.Error as exc:
            cur.execute("ROLLBACK TO SAVEPOINT probe")
            return False, f"{exc.pgcode} {str(exc).splitlines()[0]}"
        cur.execute("ROLLBACK TO SAVEPOINT probe")
        return True, "accepted"


def main() -> int:
    conn = psycopg2.connect(resolve_dsn())
    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_READ_COMMITTED)

    run = uuid.uuid4().hex[:8]
    author = str(uuid.uuid4())
    listing = str(uuid.uuid4())
    rater = str(uuid.uuid4())

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO auth.users (id, email) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (author, f"{run}-author@example.test"),
        )
        cur.execute(
            "INSERT INTO auth.users (id, email) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (rater, f"{run}-rater@example.test"),
        )
        cur.execute(
            "INSERT INTO public.profiles (id) VALUES (%s) ON CONFLICT DO NOTHING", (author,)
        )
        cur.execute(
            "INSERT INTO public.profiles (id) VALUES (%s) ON CONFLICT DO NOTHING", (rater,)
        )
        # `library_strategies` owns rows via `author_id`, not `user_id`, and carries no
        # foreign keys at all, so the seed values need only satisfy NOT NULL.
        cur.execute(
            "INSERT INTO public.library_strategies "
            "(id, author_id, source_strategy_id, name, category, symbol, timeframe, "
            " exchange_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                listing,
                author,
                str(uuid.uuid4()),
                f"probe-listing-{run}",
                "trend",
                "BTC/USDT",
                "1h",
                "binance",
            ),
        )
    conn.commit()

    cases = [
        (
            "POST /{id}/favorite  (rating = NULL)",
            {"library_id": listing, "user_id": rater, "rating": None,
             "review_text": None, "is_verified_clone": False},
        ),
        (
            "POST /{id}/clone     (rating key absent)",
            {"library_id": listing, "user_id": rater, "is_verified_clone": True},
        ),
        (
            "POST /{id}/rate      (rating = 5)",
            {"library_id": listing, "user_id": rater, "rating": 5,
             "review_text": "probe", "is_verified_clone": False},
        ),
    ]

    print("=" * 78)
    print("library_ratings WRITE PATHS vs THE PROVISIONED SCHEMA")
    print("=" * 78)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT is_nullable, coalesce(column_default,'(none)') FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='library_ratings' AND column_name='rating'"
        )
        nullable, default = cur.fetchone()
    print(f"rating column: is_nullable={nullable}  default={default}")
    print("-" * 78)

    failures = []
    for label, payload in cases:
        ok, detail = _rollback_probe(conn, label, payload)
        verdict = "ACCEPTED" if ok else "REJECTED"
        print(f"  {verdict:<9} {label}")
        if not ok:
            print(f"            {detail}")
            failures.append(label)

    with conn.cursor() as cur:
        cur.execute("DELETE FROM public.library_strategies WHERE id = %s", (listing,))
        cur.execute("DELETE FROM public.profiles WHERE id IN (%s, %s)", (author, rater))
        cur.execute("DELETE FROM auth.users WHERE id IN (%s, %s)", (author, rater))
    conn.commit()
    conn.close()

    print("-" * 78)
    if failures:
        print(f"SUMMARY: {len(failures)} of {len(cases)} documented write paths are REJECTED")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"SUMMARY: all {len(cases)} documented write paths are accepted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
