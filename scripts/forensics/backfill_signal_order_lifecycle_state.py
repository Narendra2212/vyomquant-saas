"""
scripts/forensics/backfill_signal_order_lifecycle_state.py

Task 3.3 of the trading-lifecycle-integration spec: the backfill of
``public.signals.order_lifecycle_state`` from the legacy ``public.signals.status``
column. Requirements 16.2, 16.3.

WHY THIS IS A SCRIPT AND NOT A SECTION OF SQL
---------------------------------------------
``design.md`` is explicit: "Application code performs the backfill (one UPDATE per
legacy value, not a server-side function), so the mapping lives in exactly one place
(order_lifecycle_state.py) rather than being restated in SQL." So the legacy-status
vocabulary appears in this repository exactly once - as
``order_lifecycle_state.SIGNALS_STATUS_MAP`` - and this script *derives* its UPDATE
statements from that dict rather than transcribing them. Adding a legacy spelling to
that map adds an UPDATE here with no edit to this file; nothing here has to be kept in
sync by hand.

``backend_app/migrations/005b_signal_lifecycle_and_idempotency.sql`` section 3 is
therefore a POINTER to this script plus the verification queries that prove the work is
finished. It contains no ``UPDATE``.

WHAT IT DOES, STATEMENT BY STATEMENT
------------------------------------
One ``UPDATE`` per key of ``SIGNALS_STATUS_MAP``, in sorted order, each of the shape::

    UPDATE public.signals
       SET order_lifecycle_state = <that key's canonical state>
     WHERE order_lifecycle_state IS NULL
       AND <normalised status> = <that key>

plus ONE final statement for rows whose legacy ``status`` is NULL or blank, which take
the state :func:`resolve_order_lifecycle_state` itself returns when no source reported
anything. That value is read from the module, not written here, for the same
one-place reason.

Two clauses in that shape are load-bearing:

* ``order_lifecycle_state IS NULL`` makes the whole run re-runnable AND makes it
  incapable of overwriting a value the application has already written. A signal that
  the live path has moved to a later state is not dragged back to whatever its legacy
  ``status`` column still says.
* the *normalised* comparison mirrors
  ``order_lifecycle_state.normalise_source_value`` in SQL - ``btrim``, then lower-case,
  then ``-`` and space folded onto ``_`` - because ``signals.status`` is a
  DB-unenforced text column and a row spelled ``"Pending"`` or ``" pending"`` is the
  same legacy value as ``"pending"``. The Python normaliser is the definition; this is
  its transcription, and ``tests/test_signal_lifecycle_backfill_script.py`` pins the
  two together.

WHAT IT REFUSES TO DO
---------------------
* It refuses to ``--apply`` while any row carries a legacy status that
  ``SIGNALS_STATUS_MAP`` does not know, and names every such spelling with its row
  count. Guessing a state for it - or letting it stay NULL and calling the backfill
  complete - would report a state about real money that no source actually claimed.
  The fix is to add the spelling to ``SIGNALS_STATUS_MAP`` (Requirement 16.3 makes the
  mapping total over the source vocabulary) and re-run, never to widen this script.
* It refuses to run at all if ``signals.order_lifecycle_state`` does not exist yet,
  naming the migration that adds it, rather than failing with a bare 42703.
* It writes nothing outside ``public.signals.order_lifecycle_state``: no DDL, no
  ``DELETE``, no ``TRUNCATE``, no touch of the legacy ``status`` column, which stays as
  the source it reads and as the column existing readers keep reading.
* It commits only after re-counting: if any row is still NULL after the UPDATEs, the
  transaction is rolled back and the run fails, because "leave no row NULL" is the
  task's own completion condition and a partially reconciled table is worse than an
  untouched one.

SECURITY
--------
The connection string is never printed or logged; only redacted metadata is emitted,
the same way ``apply_migration_007.py`` handles it. No signal payload is printed -
only per-legacy-value counts, which carry no credential, no symbol and no user.

USAGE
-----
    python scripts/forensics/backfill_signal_order_lifecycle_state.py --check
    python scripts/forensics/backfill_signal_order_lifecycle_state.py --apply

``--check`` is read-only: it rolls back and prints what ``--apply`` would do.
``DATABASE_URL`` is used when set; otherwise the DSN is resolved from the same AWS
secret ``apply_migration_007.py`` reads.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend_app.backend.order_lifecycle_state import (  # noqa: E402  (after sys.path)
    SIGNALS_STATUS_MAP,
    normalise_source_value,
    resolve_order_lifecycle_state,
)

#: The migration that must have been applied before this script can run. Section 1 of
#: it adds the column this script fills; section 3 of it points back here.
MIGRATION_REL = "backend_app/migrations/005b_signal_lifecycle_and_idempotency.sql"

#: The table and the two columns involved. The legacy column is READ ONLY here.
TABLE = "public.signals"
TARGET_COLUMN = "order_lifecycle_state"
LEGACY_COLUMN = "status"

#: Same secret and region ``apply_migration_007.py`` uses, so an operator has one
#: place to look. ``DATABASE_URL`` takes precedence when it is set.
SECRET_ID = "vyomquant/production/database_url"
REGION = "ap-southeast-1"


# ══════════════════════════════════════════════════════════════════════════
# THE PLAN - pure, importable without a database and without boto3
# ══════════════════════════════════════════════════════════════════════════


def normalised_status_sql(column: str = LEGACY_COLUMN) -> str:
    """``column`` as a reconciliation-table key, in SQL.

    The transcription of ``order_lifecycle_state.normalise_source_value`` into
    PostgreSQL, and in that function's own order: trim first, then lower-case, then
    fold ``-`` and space onto ``_``. The order matters - folding spaces before trimming
    would turn ``" pending "`` into ``"_pending_"``, which no key matches.
    """
    return f"lower(replace(replace(btrim({column}), '-', '_'), ' ', '_'))"


@dataclass(frozen=True)
class BackfillStatement:
    """One ``UPDATE``, for exactly one legacy value.

    ``legacy_status`` is ``None`` for the single statement that covers rows reporting
    no legacy status at all (NULL or blank), whose target state is whatever
    :func:`resolve_order_lifecycle_state` returns when no source reported.
    """

    legacy_status: Optional[str]
    order_lifecycle_state: str
    sql: str
    params: Mapping[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.legacy_status if self.legacy_status is not None else "(absent)"


def _update(where: str) -> str:
    """The one statement shape every entry of the plan shares.

    ``order_lifecycle_state IS NULL`` is not an optimisation: it is what makes the run
    re-runnable and what makes it incapable of dragging a signal the live path has
    already moved back to whatever its legacy column still says.
    """
    return (
        f"UPDATE {TABLE}\n"
        f"   SET {TARGET_COLUMN} = %({TARGET_COLUMN})s\n"
        f" WHERE {TARGET_COLUMN} IS NULL\n"
        f"   AND {where}"
    )


def backfill_plan() -> Tuple[BackfillStatement, ...]:
    """One ``UPDATE`` per key of ``SIGNALS_STATUS_MAP``, plus one for an absent status.

    Derived from the map, never transcribed from it: a spelling added to
    ``SIGNALS_STATUS_MAP`` appears here on the next import, and a spelling removed from
    it disappears. Sorted, so two runs issue the statements in the same order and a
    diff of two ``--check`` outputs is readable.
    """
    normalised = normalised_status_sql()
    statements: List[BackfillStatement] = []

    for legacy in sorted(SIGNALS_STATUS_MAP):
        state = SIGNALS_STATUS_MAP[legacy].value
        statements.append(
            BackfillStatement(
                legacy_status=legacy,
                order_lifecycle_state=state,
                sql=_update(f"{normalised} = %(legacy_status)s"),
                params={TARGET_COLUMN: state, "legacy_status": legacy},
            )
        )

    # Rows that report no legacy status at all. Not a special case invented here: it is
    # exactly the "no source reported anything" input the resolver already answers, so
    # the answer is read from the resolver rather than written down a second time.
    absent_state = resolve_order_lifecycle_state().value
    statements.append(
        BackfillStatement(
            legacy_status=None,
            order_lifecycle_state=absent_state,
            sql=_update(f"({LEGACY_COLUMN} IS NULL OR {normalised} = '')"),
            params={TARGET_COLUMN: absent_state},
        )
    )

    return tuple(statements)


def observed_status_sql() -> str:
    """Every legacy spelling still awaiting reconciliation, with its row count."""
    return (
        f"SELECT {normalised_status_sql()} AS normalised_status, count(*) AS rows\n"
        f"  FROM {TABLE}\n"
        f" WHERE {TARGET_COLUMN} IS NULL\n"
        " GROUP BY 1\n"
        " ORDER BY 1"
    )


def remaining_null_sql() -> str:
    """The completion condition of this task, as a query. Expected to return 0."""
    return f"SELECT count(*) FROM {TABLE} WHERE {TARGET_COLUMN} IS NULL"


def target_column_present_sql() -> str:
    return (
        "SELECT count(*) FROM information_schema.columns\n"
        " WHERE table_schema = 'public' AND table_name = 'signals'\n"
        f"   AND column_name = '{TARGET_COLUMN}'"
    )


def unrecognised_legacy_values(observed: Iterable[Any]) -> Tuple[str, ...]:
    """The observed spellings ``SIGNALS_STATUS_MAP`` cannot map, normalised and sorted.

    ``None`` and blank are NOT unrecognised - they mean "this row reports no legacy
    status", which the plan's final statement covers. An unrecognised value is a real
    spelling nobody reconciled, and it is a refusal rather than a fallback for the
    reason ``UnknownSourceStatus`` gives: guessing would report a state about real
    money that no source claimed.
    """
    unknown = set()
    for value in observed:
        key = normalise_source_value(value)
        if key is None:
            continue
        if key not in SIGNALS_STATUS_MAP:
            unknown.add(key)
    return tuple(sorted(unknown))


# ══════════════════════════════════════════════════════════════════════════
# THE DATABASE SIDE - every psycopg2/boto3 import is local, on purpose
# ══════════════════════════════════════════════════════════════════════════


def get_dsn() -> str:
    """``DATABASE_URL`` when set, else the AWS secret. Never printed."""
    from_env = (os.getenv("DATABASE_URL") or "").strip()
    if from_env:
        return from_env

    import boto3  # local import: the pure plan above must import without boto3

    secrets = boto3.client("secretsmanager", region_name=REGION)
    value = secrets.get_secret_value(SecretId=SECRET_ID)["SecretString"].strip()
    if value.startswith("{"):
        data = json.loads(value)
        for key in ("DATABASE_URL", "database_url", "url", "dsn"):
            if key in data:
                return data[key]
        raise SystemExit("secret JSON has no recognised URL key")
    return value


def describe(dsn: str) -> None:
    """Redacted connection metadata, matching ``apply_migration_007.py``'s output."""
    parsed = urlparse(dsn)
    host = parsed.hostname or ""
    parts = host.split(".")
    safe_host = "***." + ".".join(parts[-3:]) if len(parts) > 3 else "***"
    print("  host      : " + safe_host)
    print("  port      : " + str(parsed.port))
    print("  database  : " + ((parsed.path or "").lstrip("/") or "(default)"))
    print("  sslmode   : " + ("require" if "sslmode=require" in dsn else "(unset)"))
    print("  user      : " + ("[REDACTED]" if parsed.username else "(none)"))
    print("  password  : " + ("[REDACTED]" if parsed.password else "(none)"))


class BackfillRefused(Exception):
    """The backfill will not proceed, and says exactly why."""


def preflight(cur: Any) -> Dict[str, Any]:
    """Read-only. The column exists, and here is what is left to reconcile.

    Raises :class:`BackfillRefused` when the target column is absent (the migration has
    not been applied) or when a legacy spelling outside ``SIGNALS_STATUS_MAP`` is
    present.
    """
    cur.execute(target_column_present_sql())
    if int(cur.fetchone()[0]) != 1:
        raise BackfillRefused(
            f"{TABLE}.{TARGET_COLUMN} does not exist, so there is nothing to backfill. "
            f"Apply {MIGRATION_REL} (section 1) first."
        )

    cur.execute(remaining_null_sql())
    pending_rows = int(cur.fetchone()[0])

    cur.execute(observed_status_sql())
    observed = [(row[0], int(row[1])) for row in cur.fetchall()]

    unknown = unrecognised_legacy_values(value for value, _ in observed)
    if unknown:
        counts = {value: rows for value, rows in observed}
        detail = ", ".join(f"{value} ({counts.get(value, 0)} row(s))" for value in unknown)
        raise BackfillRefused(
            "legacy status spelling(s) with no entry in SIGNALS_STATUS_MAP: "
            f"{detail}. Requirement 16.3 makes that mapping total over the source "
            "vocabulary, so the fix is to add the spelling to "
            "backend_app/backend/order_lifecycle_state.py and re-run. This script will "
            "not guess a lifecycle state, and will not leave the row NULL and call the "
            "backfill complete."
        )

    return {
        "rows_awaiting_reconciliation": pending_rows,
        "observed": observed,
    }


def apply_backfill(cur: Any) -> Dict[str, int]:
    """Run the plan. One ``UPDATE`` per legacy value; returns rows touched by each."""
    touched: Dict[str, int] = {}
    for statement in backfill_plan():
        cur.execute(statement.sql, dict(statement.params))
        touched[statement.label] = int(cur.rowcount or 0)
    return touched


def verify(cur: Any) -> int:
    """Rows still NULL. The task's completion condition is that this is 0."""
    cur.execute(remaining_null_sql())
    return int(cur.fetchone()[0])


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill public.signals.order_lifecycle_state from the legacy status "
            "column, one UPDATE per SIGNALS_STATUS_MAP entry (task 3.3)."
        )
    )
    parser.add_argument("--apply", action="store_true", help="write and commit")
    parser.add_argument("--check", action="store_true", help="read-only; rolls back")
    args = parser.parse_args(argv)
    if not (args.apply or args.check):
        print("specify --check or --apply")
        return 2

    plan = backfill_plan()
    print(f"[1] plan: {len(plan)} UPDATE(s), derived from SIGNALS_STATUS_MAP")
    for statement in plan:
        print(f"    {statement.label:<24} -> {statement.order_lifecycle_state}")

    print("[2] resolving connection (value never printed)")
    dsn = get_dsn()
    describe(dsn)

    import psycopg2  # local import, same reason as boto3's

    conn = psycopg2.connect(dsn, connect_timeout=20)
    conn.autocommit = False
    try:
        cur = conn.cursor()
        cur.execute("SELECT current_database()")
        print("  connected to : " + str(cur.fetchone()[0]))

        print("[3] preflight")
        try:
            state = preflight(cur)
        except BackfillRefused as refusal:
            conn.rollback()
            print("  REFUSED: " + str(refusal))
            return 1
        print(f"  rows awaiting reconciliation: {state['rows_awaiting_reconciliation']}")
        for value, rows in state["observed"]:
            print(f"    {str(value or '(absent)'):<24} {rows} row(s)")

        if args.check:
            conn.rollback()
            print("[check] read-only, nothing written")
            return 0

        print("[4] applying the backfill")
        touched = apply_backfill(cur)
        for label, rows in touched.items():
            print(f"    {label:<24} {rows} row(s) updated")
        print(f"  total rows updated: {sum(touched.values())}")

        print("[5] verifying no row is left NULL")
        remaining = verify(cur)
        if remaining:
            conn.rollback()
            print(
                f"  FAIL: {remaining} row(s) still have a NULL {TARGET_COLUMN}; "
                "rolled back rather than leaving the table partially reconciled"
            )
            return 1

        conn.commit()
        print("  committed")
        print("[OK] every signal row now carries a canonical order_lifecycle_state")
        return 0
    except Exception as exc:  # pragma: no cover - operator-facing path
        conn.rollback()
        print("  ERROR: " + type(exc).__name__ + ": " + str(exc))
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
