"""A PostgREST-shaped Persistence_Layer handle backed by REAL PostgreSQL.

WHY THIS EXISTS RATHER THAN ``tests/test_paper_repository.FakeSupabase``
=======================================================================
``FakeSupabase`` is the established double and it is a good one -- it reimplements the
eight unique indexes 009 declares. But it reimplements them, and PAPER-01 is a
*persistence* finding: the question is whether a bot's fill reaches real tables and
survives, under the real constraints. A double cannot answer that, because the thing under
test is the database.

So this client speaks the same eleven-method fluent surface ``paper_repository`` uses
(``table`` / ``select`` / ``insert`` / ``update`` / ``eq`` / ``is_`` / ``gt`` / ``order``
/ ``limit`` / ``single`` / ``execute``) and translates each statement into SQL against a
live PostgreSQL instance. What that buys over the double:

* ``uq_paper_order_idem_default``, ``uq_paper_fill_event`` and ``uq_paper_position_open``
  are the real partial indexes, with real NULL semantics -- which is the whole subtlety
  migration 013 exists for.
* ``chk_paper_balances_non_negative``, ``chk_paper_order_fill_bound``,
  ``chk_paper_order_state`` and the rest are real CHECK constraints.
* ``trg_paper_order_transition_guard`` and the six ``*_append_only`` triggers actually
  fire.
* A ``23505`` arrives as a real ``psycopg2`` error carrying ``pgcode``, which is what
  ``paper_repository._is_unique_violation`` reads.

NOT A TEST MODULE. The filename deliberately does not match ``test_*.py``, so pytest does
not collect it -- the same convention ``tests/property/paper_market_streams.py`` uses.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
No transaction. ``paper_repository`` issues one statement at a time because PostgREST
offers it nothing else, and its write-ordering comments are *about* that constraint. A
client that wrapped the six writes of a fill in a transaction would hide the partial-write
window those comments describe, so ``autocommit`` is on and each statement stands alone --
which is faithful to the transport in production.

No RLS. The connection is the table owner, as the service-role backend connection is.
Row-level security on the ``paper_*`` tables is covered by the tenant-isolation suite; what
this client is for is accounting and persistence semantics.

TYPE FIDELITY, AND WHY ``NUMERIC`` COMES BACK AS ``str``
--------------------------------------------------------
``paper_accounting.to_decimal`` **refuses a float** outright (Requirement 18.1), so a
client that handed back ``float`` for a ``NUMERIC(28,10)`` column would make every read
raise. ``Decimal`` would work, but PostgREST hands back JSON, so ``str`` is both exact and
closer to the transport: ``Decimal('1.23')`` and ``'1.23'`` are the same number to
``to_decimal`` and only one of them survives a JSON round trip. ``uuid`` and
``timestamptz`` are stringified for the same reason.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

import psycopg2
import psycopg2.extras

__all__ = ["PostgresSupabase", "PgResponse", "paper_tables_present", "purge_paper_user"]


#: Every table this client is allowed to name, so a typo is a Python error here rather
#: than a SQL injection surface or a confusing ``relation does not exist``.
ALLOWED_TABLES = frozenset(
    {
        "paper_sessions",
        "paper_accounts",
        "paper_orders",
        "paper_fills",
        "paper_positions",
        "paper_balance_events",
        "paper_trades",
        "paper_equity_snapshots",
        "paper_metrics",
        "paper_events",
        "paper_market_events",
        "paper_order_allowed_transitions",
        "paper_session_allowed_transitions",
        # ── Added for LIB-01 (tests/test_lib01_clone_strategy_postgres.py) ──────────
        # The three tables `routers/library.py:clone_strategy` touches. Admitted here
        # rather than in a second shim, because a second PostgREST-shaped client over
        # psycopg2 would be a second set of the same bugs.
        "library_strategies",
        "strategies",
        "library_ratings",
    }
)


def _jsonable(value: Any) -> Any:
    """One stored value as PostgREST would render it. See the module docstring."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, _uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, memoryview):
        return bytes(value)
    return value


def _row(record: Any) -> Dict[str, Any]:
    return {key: _jsonable(val) for key, val in dict(record).items()}


def _bind(value: Any) -> Any:
    """One outbound value as psycopg2 should see it."""
    if isinstance(value, (dict, list)):
        return psycopg2.extras.Json(value)
    return value


class PgResponse:
    """What ``execute()`` returns. ``.data`` and ``.error``, the two attributes read."""

    def __init__(self, data: Any) -> None:
        self.data = data
        self.error = None

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"PgResponse(data={self.data!r})"


class PgQuery:
    """One statement under construction. Mirrors ``FakeSupabase._Query``'s surface."""

    def __init__(self, table: str, client: "PostgresSupabase") -> None:
        if table not in ALLOWED_TABLES:
            raise AssertionError(
                f"{table!r} is not one of the paper tables this client may name: "
                f"{sorted(ALLOWED_TABLES)}"
            )
        self.table_name = table
        self.client = client
        self.op = "select"
        self.payload: Optional[Dict[str, Any]] = None
        self.filters: List[Tuple[str, str, Any]] = []
        self.orders: List[Tuple[str, bool]] = []
        self.limit_value: Optional[int] = None
        self.want_single = False

    # ── the fluent surface ────────────────────────────────────────────────

    def select(self, cols: str = "*") -> "PgQuery":
        # The requested column list is deliberately ignored in favour of ``*``. Every
        # projection in ``paper_repository`` is a subset of the table, so ``*`` is a
        # superset and every read finds the keys it looks for -- while a hand-parsed
        # column list would be a second place the projection could be got wrong.
        self.op = "select"
        return self

    def insert(self, payload: Dict[str, Any]) -> "PgQuery":
        self.op = "insert"
        self.payload = dict(payload)
        return self

    def update(self, payload: Dict[str, Any]) -> "PgQuery":
        self.op = "update"
        self.payload = dict(payload)
        return self

    def upsert(self, payload: Dict[str, Any], on_conflict: str = "id") -> "PgQuery":
        """``INSERT ... ON CONFLICT (<on_conflict>) DO UPDATE``.

        Added for ``clone_strategy``'s step 9, which writes the verified-clone marker as
        ``upsert(..., on_conflict="library_id,user_id")``. ``on_conflict`` is a
        comma-separated column list exactly as PostgREST spells it, and the real unique
        index behind it (``library_ratings_user_library_idx``) is what arbitrates - which
        is the point of running against the real database rather than a double.
        """
        self.op = "upsert"
        self.payload = dict(payload)
        self.conflict_columns = [
            part.strip() for part in str(on_conflict).split(",") if part.strip()
        ]
        return self

    def eq(self, col: str, val: Any) -> "PgQuery":
        self.filters.append(("eq", col, val))
        return self

    def in_(self, col: str, values: Any) -> "PgQuery":
        self.filters.append(("in", col, list(values)))
        return self

    def is_(self, col: str, val: Any) -> "PgQuery":
        self.filters.append(("is", col, val))
        return self

    def gt(self, col: str, val: Any) -> "PgQuery":
        self.filters.append(("gt", col, val))
        return self

    def order(self, col: str, desc: bool = False) -> "PgQuery":
        self.orders.append((col, bool(desc)))
        return self

    def limit(self, count: int) -> "PgQuery":
        self.limit_value = int(count)
        return self

    def single(self) -> "PgQuery":
        self.want_single = True
        return self

    # ── compilation ───────────────────────────────────────────────────────

    def _where(self) -> Tuple[str, List[Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        for operator, col, val in self.filters:
            ident = f'"{col}"'
            if operator == "eq":
                clauses.append(f"{ident} = %s")
                params.append(_bind(val))
            elif operator == "gt":
                clauses.append(f"{ident} > %s")
                params.append(_bind(val))
            elif operator == "in":
                if not val:
                    # PostgREST's ``in.()`` matches nothing; a bare ``IN ()`` is a syntax
                    # error, so the impossible predicate is spelled out instead.
                    clauses.append("FALSE")
                else:
                    marks = ", ".join(["%s"] * len(val))
                    clauses.append(f"{ident} IN ({marks})")
                    params.extend(_bind(item) for item in val)
            elif operator == "is":
                # PostgREST spells IS NULL as ``?col=is.null``; the repository passes the
                # string "null" (and sometimes a real None). Both mean IS NULL.
                if val is None or str(val).strip().lower() in ("null", "none"):
                    clauses.append(f"{ident} IS NULL")
                elif str(val).strip().lower() in ("true", "false"):
                    clauses.append(f"{ident} IS {str(val).strip().upper()}")
                else:
                    raise AssertionError(f"unsupported is_ value: {val!r}")
            else:  # pragma: no cover - the surface is closed
                raise AssertionError(f"unsupported operator {operator!r}")
        if not clauses:
            return "", params
        return " WHERE " + " AND ".join(clauses), params

    def _suffix(self) -> str:
        sql = ""
        if self.orders:
            parts = [
                f'"{col}" {"DESC" if desc else "ASC"}' for col, desc in self.orders
            ]
            sql += " ORDER BY " + ", ".join(parts)
        if self.limit_value is not None:
            sql += f" LIMIT {int(self.limit_value)}"
        return sql

    def compile(self) -> Tuple[str, Sequence[Any]]:
        table = f'public."{self.table_name}"'
        if self.op == "select":
            where, params = self._where()
            return f"SELECT * FROM {table}{where}{self._suffix()}", params
        if self.op == "insert":
            payload = self.payload or {}
            if not payload:
                raise AssertionError("insert with an empty payload")
            cols = ", ".join(f'"{key}"' for key in payload)
            marks = ", ".join(["%s"] * len(payload))
            params = [_bind(value) for value in payload.values()]
            return (
                f"INSERT INTO {table} ({cols}) VALUES ({marks}) RETURNING *",
                params,
            )
        if self.op == "update":
            payload = self.payload or {}
            if not payload:
                raise AssertionError("update with an empty payload")
            assignments = ", ".join(f'"{key}" = %s' for key in payload)
            params = [_bind(value) for value in payload.values()]
            where, where_params = self._where()
            if not where:
                raise AssertionError(
                    "refusing an UPDATE with no predicate; every update in "
                    "paper_repository is scoped"
                )
            return (
                f"UPDATE {table} SET {assignments}{where} RETURNING *",
                params + list(where_params),
            )
        if self.op == "upsert":
            payload = self.payload or {}
            if not payload:
                raise AssertionError("upsert with an empty payload")
            conflict = getattr(self, "conflict_columns", ["id"])
            cols = ", ".join(f'"{key}"' for key in payload)
            marks = ", ".join(["%s"] * len(payload))
            target = ", ".join(f'"{col}"' for col in conflict)
            # Everything except the conflict target is refreshed, which is what PostgREST's
            # default ``resolution=merge-duplicates`` does.
            updatable = [key for key in payload if key not in conflict]
            if updatable:
                assignments = ", ".join(
                    f'"{key}" = EXCLUDED."{key}"' for key in updatable
                )
                action = f"DO UPDATE SET {assignments}"
            else:
                action = "DO NOTHING"
            params = [_bind(value) for value in payload.values()]
            return (
                f"INSERT INTO {table} ({cols}) VALUES ({marks}) "
                f"ON CONFLICT ({target}) {action} RETURNING *",
                params,
            )
        raise AssertionError(f"unsupported op {self.op!r}")  # pragma: no cover

    def execute(self) -> PgResponse:
        sql, params = self.compile()
        rows = self.client.run(sql, params)
        self.client.statements.append((self.table_name, self.op, sql))
        if self.want_single:
            if len(rows) != 1:
                raise psycopg2.errors.NoDataFound(  # type: ignore[attr-defined]
                    f"single() expected exactly one row from {self.table_name}, got "
                    f"{len(rows)}"
                )
            return PgResponse(rows[0])
        return PgResponse(rows)


class PostgresSupabase:
    """A Persistence_Layer handle over a real PostgreSQL connection."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.conn = psycopg2.connect(dsn)
        self.conn.autocommit = True
        #: Every statement issued, for a test that asserts on statement counts.
        self.statements: List[Tuple[str, str, str]] = []

    def table(self, name: str) -> PgQuery:
        return PgQuery(name, self)

    def run(self, sql: str, params: Sequence[Any]) -> List[Dict[str, Any]]:
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, list(params))
            if cur.description is None:
                return []
            return [_row(record) for record in cur.fetchall()]

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass


def paper_tables_present(dsn: str) -> bool:
    """Whether migration 009 is applied in this database."""
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                " WHERE table_schema='public' AND table_name='paper_accounts')"
            )
            return bool(cur.fetchone()[0])
    finally:
        conn.close()


#: PARENT-FIRST, and that ordering is forced by the database rather than chosen.
#:
#: ``paper_append_only_guard()`` refuses a direct DELETE on the six append-only tables
#: "while its owning account or order still exists" -- measured: deleting
#: ``paper_balance_events`` child-first raises
#: ``CheckViolation: paper_balance_events is append-only``. The guard's purpose is to stop
#: a client rewriting history while the book is live, and to let the CASCADES do the
#: removal. So the account is deleted and the cascades take the rest:
#:
#:   paper_accounts --CASCADE--> paper_orders --CASCADE--> paper_fills
#:                  --CASCADE--> paper_positions, paper_balance_events, paper_trades
#:
#: ``paper_equity_snapshots`` and ``paper_metrics`` hang off ``session_id`` only, which is
#: NULL for a default-account row, so nothing cascades to them -- they are swept after the
#: account is gone, at which point the guard admits the DELETE.
_PURGE_PARENTS = ("paper_accounts",)
_PURGE_ORPHANS = ("paper_equity_snapshots", "paper_metrics")


def purge_paper_user(dsn: str, user_id: str) -> None:
    """Remove every paper row for one synthetic user. Parent-first; see :data:`_PURGE_PARENTS`."""
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for table in _PURGE_PARENTS:
                cur.execute(
                    f'DELETE FROM public."{table}" WHERE user_id = %s', (user_id,)
                )
            for table in _PURGE_ORPHANS:
                cur.execute(
                    f'DELETE FROM public."{table}" WHERE user_id = %s', (user_id,)
                )
    finally:
        conn.close()
