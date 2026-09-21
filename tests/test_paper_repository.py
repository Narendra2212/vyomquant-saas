"""
tests/test_paper_repository.py - unit tests for the Paper_Repository (task 23.1).

Spec: marketplace-subscriptions-paper-trading task 23.1. Requirements 17.1, 17.2, 17.11, 17.12,
18.1, 18.5, 21.2, 21.5, 24.10, 28.3.

WHAT IS ASSERTED HERE, AND WHY EACH CLAIM NEEDED A TEST
-------------------------------------------------------
1. **The default account is single.** ``uq_paper_account_default`` is a PARTIAL unique index on
   ``(user_id, currency) WHERE session_id IS NULL``, and the whole default-account design rests
   on it. A double that did not enforce it would make every assertion about "the" default account
   vacuous - two rows would simply both exist and the read would return whichever came first. So
   :class:`FakeSupabase` enforces both account indexes (and ``uq_paper_order_idem``,
   ``uq_paper_fill_event``, ``uq_paper_position_open``), and
   :func:`test_the_double_enforces_uq_paper_account_default` asserts *the double itself* refuses
   the second default row before anything else relies on it.

2. **Refusal, not degradation.** With ``paper_accounts`` absent every entry point raises a
   ``PaperError`` carrying ``PAPER_PERSISTENCE_UNAVAILABLE``, HTTP 503, naming
   ``009_paper_trading.sql`` - and returns no figure of any kind. A memory-derived balance is the
   fabricated figure Requirement 28.3 forbids.

3. **A read that did not complete is not a read that found nothing.** Both shapes a failure takes
   - a raised driver exception and a completed response carrying an ``error`` envelope - raise
   ``PaperPersistenceError`` rather than answering ``None`` or ``[]``.

4. **The tenant is a predicate.** Every read's filter list is inspected for ``user_id``, so the
   claim is about the statement that was issued and not merely about what was returned
   (Requirement 21.5).

5. **No float reaches a balance, a price or a quantity** (Requirement 18.1).

WHY THERE IS NO ``_run_coroutine`` HERE
--------------------------------------
``paper_repository`` is synchronous throughout - by design, because ``routers/risk.py`` calls the
service's read methods synchronously from inside ``async def`` handlers, so the storage layer
under them cannot be ``await``-only. No coroutine is driven by this file, and ``asyncio.run``
appears nowhere in it, so the loop-leak that ``_run_coroutine`` exists to prevent in
``tests/test_settlement_service.py`` cannot arise. The double follows that file's style otherwise:
a recording fluent query builder over mutable in-process tables, with injectable failures.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

import pytest

from backend_app.backend.paper import COLUMN_CONTRACT as PAPER_CONTRACT
from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper.errors import PAPER_PERSISTENCE_UNAVAILABLE, PaperError
from backend_app.backend.paper.paper_order_state import PaperOrderState

USER = "11111111-1111-1111-1111-111111111111"
OTHER_USER = "22222222-2222-2222-2222-222222222222"
SESSION = "33333333-3333-3333-3333-333333333333"
OTHER_SESSION = "44444444-4444-4444-4444-444444444444"
SYMBOL = "BTC-USDT"

NOW = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
CAPITAL = Decimal("100000")


# ══════════════════════════════════════════════════════════════════════════
# THE PERSISTENCE_LAYER DOUBLE
# ══════════════════════════════════════════════════════════════════════════


class FakeUniqueViolation(Exception):
    """What a driver raises for ``23505``.

    Both spellings a driver may surface are present - the constraint name and the SQLSTATE -
    because ``paper_repository._is_unique_violation`` matches on either and the point of the
    double is to exercise that reading rather than to assume it.
    """

    def __init__(self, constraint: str, table: str) -> None:
        self.pgcode = "23505"
        super().__init__(
            f'duplicate key value violates unique constraint "{constraint}" on {table} (23505)'
        )


class FakeMissingTable(Exception):
    """What a driver raises for ``42P01`` - and what PostgREST says for ``PGRST205``."""

    def __init__(self, table: str) -> None:
        self.pgcode = "42P01"
        super().__init__(
            f'relation "public.{table}" does not exist (42P01); '
            "could not find the table in the schema cache (PGRST205)"
        )


class FakeNotNullSessionId(Exception):
    """What a driver raises for ``23502`` on a ``paper_*.session_id`` column.

    The one signature of a database carrying ``009_paper_trading.sql`` but not
    ``013_paper_default_account_children.sql``: the seven accounting child tables still declare
    ``session_id NOT NULL``, so every session-scoped write succeeds and every DEFAULT-ACCOUNT
    write - which writes ``session_id: None`` - is refused. Modelled here so the classification in
    ``paper_repository._execute`` is exercised rather than assumed.
    """

    def __init__(self, table: str) -> None:
        self.pgcode = "23502"
        super().__init__(
            f'null value in column "session_id" of relation "public.{table}" '
            "violates not-null constraint (23502 not_null_violation)"
        )


class FakeCheckViolation(Exception):
    """What a driver raises for ``23514`` - a CHECK constraint or a trigger that ``RAISE``d it.

    Added by task 25.2 for ``trg_paper_session_config_immutable``
    (``009_paper_trading.sql`` section 14d), which is a ``BEFORE UPDATE ... FOR EACH ROW`` trigger
    on ``paper_sessions`` raising with ``USING ERRCODE = '23514'`` when
    ``NEW.config IS DISTINCT FROM OLD.config``.

    Modelled here for the same reason the eight unique indexes are: a double that accepted an
    UPDATE the real database refuses would let "the session configuration is frozen"
    (Requirement 16.12) pass as an assertion about code that in fact writes it. The message
    reproduces the trigger's own wording so a caller matching on text rather than on ``pgcode``
    is exercised too.
    """

    def __init__(self, constraint: str, table: str, message: str = "") -> None:
        self.pgcode = "23514"
        self.constraint = constraint
        super().__init__(
            message
            or (
                f'new row for relation "public.{table}" violates check constraint '
                f'"{constraint}" (23514)'
            )
        )


#: ``paper_session_allowed_transitions``' seed, transcribed from ``009_paper_trading.sql``
#: section 2 - six pairs, no seventh, no self-edge. Used by
#: :meth:`FakeSupabase._enforce_update_triggers` to model ``trg_paper_session_guard``.
#:
#: Transcribed from the MIGRATION and deliberately not imported from
#: ``paper_session_service.PAPER_SESSION_TRANSITIONS``: this double stands in for the database, and
#: a guard that read its rule out of the module under test would agree with that module by
#: construction rather than by being right. The two are held against each other in
#: ``tests/test_submission_state_agreement.py``, which parses 009's INSERT.
PAPER_SESSION_SEED_PAIRS: Set[Tuple[str, str]] = {
    ("CREATED", "RUNNING"),
    ("RUNNING", "PAUSED"),
    ("PAUSED", "RUNNING"),
    ("RUNNING", "STOPPED"),
    ("PAUSED", "STOPPED"),
    ("STOPPED", "CREATED"),
}


class _Resp:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Query:
    """A recording query builder mimicking the supabase-py fluent chain."""

    def __init__(self, table: str, client: "FakeSupabase") -> None:
        self.table_name = table
        self.client = client
        self.op = "select"
        self.cols: Optional[str] = None
        self.payload: Optional[Dict[str, Any]] = None
        #: ``(operator, column, value)`` in the order the module applied them.
        self.filters: List[Tuple[str, str, Any]] = []
        self.orders: List[Tuple[str, bool]] = []
        self.limit_value: Optional[int] = None

    # -- the fluent surface ------------------------------------------------
    def select(self, cols: str) -> "_Query":
        self.op = "select"
        self.cols = cols
        return self

    def insert(self, payload: Dict[str, Any]) -> "_Query":
        self.op = "insert"
        self.payload = dict(payload)
        return self

    def update(self, payload: Dict[str, Any]) -> "_Query":
        self.op = "update"
        self.payload = dict(payload)
        return self

    def eq(self, col: str, val: Any) -> "_Query":
        self.filters.append(("eq", col, val))
        return self

    def is_(self, col: str, val: Any) -> "_Query":
        self.filters.append(("is", col, val))
        return self

    def gt(self, col: str, val: Any) -> "_Query":
        """PostgREST's ``?col=gt.val``. Added by task 26.4 for the replay read.

        The Paper_Channel replay is ``sequence > last_received``, which is the first STRICT
        inequality any statement in this repository needs. Compared numerically in
        :meth:`FakeSupabase._matching`, not as text - ``"9" > "10"`` is true as strings and false as
        sequences, and a double that got that wrong would let a replay assertion pass on a log
        shorter than ten events and fail on production.
        """
        self.filters.append(("gt", col, val))
        return self

    def order(self, col: str, desc: bool = False) -> "_Query":
        self.orders.append((col, bool(desc)))
        return self

    def limit(self, count: int) -> "_Query":
        self.limit_value = int(count)
        return self

    def execute(self) -> Any:
        return self.client._execute(self)

    # -- assertion helpers -------------------------------------------------
    def filtered_columns(self) -> Set[str]:
        return {col for _, col, _ in self.filters}

    def filter_value(self, col: str) -> Any:
        for _, name, value in self.filters:
            if name == col:
                return value
        raise AssertionError(f"{col} is not a predicate on this statement")


class FakeSupabase:
    """Eleven mutable tables, the eight unique indexes 009 declares, and injectable failures.

    The indexes are the point. ``uq_paper_account_default`` is PARTIAL - it applies only where
    ``session_id IS NULL`` - so a user may hold many session accounts in one currency and exactly
    one default account, and a double that collapsed that distinction would let every
    default-account assertion in this file pass for the wrong reason.

    The three SESSION-scoped tables - ``paper_sessions``, ``paper_events`` and
    ``paper_market_events`` - were added here by task 24.1/24.2 rather than in a second double,
    because ``tests/paper_seed.py`` records the rule this repository holds to: there is exactly one
    Persistence_Layer double, and a second would be a second set of assumptions about the database.
    Their three unique constraints (``uq_paper_market_event``, ``uq_paper_event_seq``,
    ``uq_paper_event_id``) are enforced below for the same reason the account indexes are: a double
    that accepted every insert would let a dedupe assertion pass without a dedupe.

    Task 25.2 adds one **trigger** on the same principle:
    ``trg_paper_session_config_immutable`` refuses any UPDATE that changes
    ``paper_sessions.config`` (Requirement 16.12). See :meth:`_enforce_update_triggers`.
    """

    #: The two tables that are NOT ``paper_*`` and are NOT written by ``paper_repository``, added
    #: by task 27.1 because the Requirement 17.9 pipeline READS them before it creates anything:
    #: ``library_strategies`` is what ``marketplace.entitlement_resolver`` resolves the single
    #: admission decision from (with ``marketplace_submissions`` and ``library_subscriptions``
    #: carried on the row as PostgREST embeds them), and ``strategy_versions`` is where
    #: ``paper_session_service`` reads the version's ``lifecycle_state`` and ``compiled_plan``.
    #:
    #: They are here rather than in a second double for the reason ``tests/paper_seed.py`` records:
    #: there is exactly ONE Persistence_Layer double, and a second would be a second set of
    #: assumptions about the database. Nothing in this file writes either table - they are seeded
    #: premises, and ``wrote_anything()`` therefore still means "the code under test created
    #: something".
    ADJACENT_TABLES = {
        "library_strategies": "library_strategies",
        "strategy_versions": "strategy_versions",
    }

    #: Every table name keyed to the attribute holding its rows.
    TABLES = {
        repo.ACCOUNTS_TABLE: "accounts",
        repo.ORDERS_TABLE: "orders",
        repo.FILLS_TABLE: "fills",
        repo.POSITIONS_TABLE: "positions",
        repo.BALANCE_EVENTS_TABLE: "balance_events",
        repo.TRADES_TABLE: "trades",
        repo.EQUITY_SNAPSHOTS_TABLE: "equity_snapshots",
        repo.METRICS_TABLE: "metrics",
        repo.SESSIONS_TABLE: "sessions",
        repo.EVENTS_TABLE: "events",
        repo.MARKET_EVENTS_TABLE: "market_events",
        **ADJACENT_TABLES,
    }

    def __init__(
        self,
        *,
        accounts: Optional[List[Dict[str, Any]]] = None,
        orders: Optional[List[Dict[str, Any]]] = None,
        positions: Optional[List[Dict[str, Any]]] = None,
        trades: Optional[List[Dict[str, Any]]] = None,
        equity_snapshots: Optional[List[Dict[str, Any]]] = None,
        metrics: Optional[List[Dict[str, Any]]] = None,
        sessions: Optional[List[Dict[str, Any]]] = None,
        library_strategies: Optional[List[Dict[str, Any]]] = None,
        strategy_versions: Optional[List[Dict[str, Any]]] = None,
        missing_tables: bool = False,
        session_id_not_null: bool = False,
        raise_on: Optional[Set[Tuple[str, str]]] = None,
        error_on: Optional[Set[Tuple[str, str]]] = None,
        before_insert: Optional[Any] = None,
        before_update: Optional[Any] = None,
        after_select: Optional[Any] = None,
    ) -> None:
        self.accounts: List[Dict[str, Any]] = [dict(r) for r in (accounts or [])]
        self.orders: List[Dict[str, Any]] = [dict(r) for r in (orders or [])]
        self.fills: List[Dict[str, Any]] = []
        self.positions: List[Dict[str, Any]] = [dict(r) for r in (positions or [])]
        self.balance_events: List[Dict[str, Any]] = []
        self.trades: List[Dict[str, Any]] = [dict(r) for r in (trades or [])]
        self.equity_snapshots: List[Dict[str, Any]] = [
            dict(r) for r in (equity_snapshots or [])
        ]
        self.metrics: List[Dict[str, Any]] = [dict(r) for r in (metrics or [])]
        #: The RLS-owner root and its two append-only logs. ``sessions`` is seedable because a
        #: session row is a premise for the feed path rather than something it creates; the two
        #: logs are not, because every row in them has to be written by the code under test.
        self.sessions: List[Dict[str, Any]] = [dict(r) for r in (sessions or [])]
        self.events: List[Dict[str, Any]] = []
        self.market_events: List[Dict[str, Any]] = []
        #: The two seeded premises of :data:`ADJACENT_TABLES`. Read-only in practice: no function
        #: under ``backend_app/backend/paper/`` writes either.
        self.library_strategies: List[Dict[str, Any]] = [
            dict(r) for r in (library_strategies or [])
        ]
        self.strategy_versions: List[Dict[str, Any]] = [
            dict(r) for r in (strategy_versions or [])
        ]

        #: 009 unapplied: every statement answers ``42P01``.
        self.missing_tables = missing_tables
        #: 009 applied but 013 not: a child insert carrying ``session_id: None`` answers ``23502``.
        #: Session-scoped inserts still succeed, which is what makes this a different condition
        #: from :attr:`missing_tables` rather than a broader version of it.
        self.session_id_not_null = session_id_not_null
        self.raise_on = set(raise_on or set())
        self.error_on = set(error_on or set())
        #: Called with ``(client, query)`` immediately before an insert lands - the seam a
        #: concurrent writer is injected through on the create path.
        self.before_insert = before_insert
        #: Called with ``(client, query)`` immediately before an update's rows are matched - the
        #: seam a concurrent writer is injected through on the insert-then-update path, where
        #: there is no intervening SELECT for :attr:`after_select` to fire on. It is what lets a
        #: test move a row *between* the statement that created it and the statement that was
        #: about to transition it, which is precisely the race the ``expected_state`` predicate in
        #: ``paper_repository.update_order`` exists to detect (Requirement 16.4).
        self.before_update = before_update
        #: Called with ``(client, query)`` immediately after a select's rows are copied out - the
        #: seam a concurrent writer is injected through on the read-then-update path.
        self.after_select = after_select

        self.statements: List[_Query] = []
        self.ops: List[Tuple[str, str]] = []
        self._sequence = 0

    # -- the supabase-py surface the module uses ---------------------------
    def table(self, name: str) -> _Query:
        return _Query(name, self)

    # -- execution ---------------------------------------------------------
    def _execute(self, q: _Query) -> Any:
        key = (q.op, q.table_name)
        self.statements.append(q)
        self.ops.append(key)

        if self.missing_tables and q.table_name.startswith("paper_"):
            raise FakeMissingTable(q.table_name)
        if key in self.raise_on:
            raise RuntimeError(f"{q.op} on {q.table_name} did not complete")
        if key in self.error_on:
            return {"data": None, "error": {"message": f"boom on {q.table_name}"}}

        rows = getattr(self, self.TABLES[q.table_name])

        if q.op == "select":
            matched = self._matching(rows, q)
            for column, desc in reversed(q.orders):
                matched.sort(
                    key=lambda r, col=column: self._sortable(r.get(col)), reverse=desc
                )
            if q.limit_value is not None:
                matched = matched[: q.limit_value]
            result = [copy.deepcopy(r) for r in matched]
            # The seam a concurrent writer lands through: the read has completed and its rows are
            # already copied out, so a hook that mutates the stored rows here reproduces exactly
            # the race the version predicate exists to detect - the caller holds a correct image
            # of a row that has since moved.
            if self.after_select is not None:
                self.after_select(self, q)
            return _Resp(result)

        if q.op == "insert":
            if self.before_insert is not None:
                self.before_insert(self, q)
            if (
                self.session_id_not_null
                and q.table_name != repo.ACCOUNTS_TABLE
                and (q.payload or {}).get("session_id") is None
            ):
                raise FakeNotNullSessionId(q.table_name)
            row = self._defaults(q.table_name, dict(q.payload or {}))
            self._enforce_unique(q.table_name, row)
            rows.append(row)
            return _Resp([copy.deepcopy(row)])

        if q.op == "update":
            # The seam a concurrent writer lands through on the insert-then-update path: the rows
            # have NOT been matched yet, so a hook that moves the target row here makes the
            # guarded UPDATE match zero rows - the stale-guard race, reproduced exactly.
            if self.before_update is not None:
                self.before_update(self, q)
            touched = self._matching(rows, q)
            for row in touched:
                self._enforce_update_triggers(q.table_name, row, q.payload or {})
            for row in touched:
                row.update(q.payload or {})
            return _Resp([copy.deepcopy(r) for r in touched])

        raise AssertionError(f"unsupported operation {q.op!r}")

    # -- the parts that make the double a database -------------------------
    def _defaults(self, table: str, row: Dict[str, Any]) -> Dict[str, Any]:
        """The column defaults 009 declares, so a row reads back the way it would from Postgres."""
        self._sequence += 1
        row.setdefault("id", f"{table}-{self._sequence}")
        row.setdefault("created_at", NOW.isoformat())
        row.setdefault("updated_at", NOW.isoformat())
        if table in (repo.ACCOUNTS_TABLE, repo.POSITIONS_TABLE):
            row.setdefault("version", 1)
        # Every one of the eight account-scoped tables has a NULLABLE session_id: paper_accounts
        # always did (NULL is the default account), and 013 relaxed the seven children so a row
        # belonging to that account can carry NULL exactly as its account does. paper_sessions is
        # excluded because it HAS no session_id column - it IS the session - and inventing one here
        # would let a statement filter on a column the real table does not have.
        if table != repo.SESSIONS_TABLE and table not in self.ADJACENT_TABLES:
            row.setdefault("session_id", None)
        if table == repo.ACCOUNTS_TABLE:
            row.setdefault("realized_pnl", "0")
            row.setdefault("stale", False)
            row.setdefault("last_price_at", None)
        if table == repo.SESSIONS_TABLE:
            # 009 section 3's own column defaults, so a seeded session reads back the way a row
            # created by the session-start path would - and in particular so `session_state`
            # defaults to CREATED and `feed_state` to PENDING rather than to nothing.
            row.setdefault("environment", "PAPER")
            row.setdefault("session_state", "CREATED")
            row.setdefault("feed_state", "PENDING")
            row.setdefault("feed_transport", None)
            row.setdefault("event_sequence", 0)
        if table == repo.MARKET_EVENTS_TABLE:
            row.setdefault("received_at", NOW.isoformat())
            row.setdefault("latency_ms", None)
        return row

    @staticmethod
    def _enforce_update_triggers(
        table: str, stored: Dict[str, Any], payload: Dict[str, Any]
    ) -> None:
        """The BEFORE UPDATE triggers 009 declares, for the columns this double can see.

        Currently one: ``trg_paper_session_config_immutable`` (009 section 14d). It is a
        ``BEFORE UPDATE ... FOR EACH ROW`` trigger on ``paper_sessions`` that raises ``23514``
        when ``NEW.config IS DISTINCT FROM OLD.config``, which is what makes Requirement 16.12's
        "captured at session start and unchanged for the session's lifetime" a property of the
        database rather than of this repository's discipline.

        ``IS DISTINCT FROM`` is reproduced, not ``<>``: an UPDATE that sets ``config`` to the value
        it already holds is **permitted** by the real trigger, and a double that refused it would
        assert something stricter than the database does. Compared on the parsed JSONB - two
        payloads with the same members in a different order are the same ``jsonb`` value in
        PostgreSQL, so ``config`` is compared as a mapping and not as text.

        Task 27.3 adds the SECOND: ``trg_paper_session_guard`` (009 section 14c), the
        ``BEFORE UPDATE ... FOR EACH ROW`` trigger that raises ``23514`` naming both states when a
        ``session_state`` transition is absent from ``paper_session_allowed_transitions``. It was
        deliberately absent here until now because nothing in ``paper_repository`` wrote
        ``session_state`` and a guard modelled before its writer exists would be a guess at that
        writer's shape. ``transition_session_state`` is that writer, so the guard is modelled now -
        and modelled for the same reason the eight unique indexes are: a double that accepted an
        illegal transition would let "an illegal operation is refused even by a direct SQL UPDATE"
        (Requirement 17.14) pass as an assertion about code that in fact performs one.

        The six pairs are transcribed from **009**, not imported from
        ``paper_session_service.PAPER_SESSION_TRANSITIONS``: this double models the DATABASE, and
        reading the machine out of the module under test would make the guard agree with that module
        by construction. ``tests/test_submission_state_agreement.py`` is what holds the migration
        and the Python constant against each other.

        A SAME-VALUE write is let through, exactly as ``paper_session_guard()`` lets it through:
        its own body checks ``NEW.session_state IS DISTINCT FROM OLD.session_state`` first, so an
        UPDATE that re-writes the state a row already holds is not a transition.
        """
        if table != repo.SESSIONS_TABLE:
            return

        if "config" in payload and payload["config"] != stored.get("config"):
            raise FakeCheckViolation(
                "trg_paper_session_config_immutable",
                table,
                f"paper_sessions.config is frozen at start and cannot be changed "
                f"(session {stored.get('id')}; Requirement 16.12) (23514)",
            )

        if "session_state" in payload:
            old = stored.get("session_state")
            new = payload["session_state"]
            if new != old and (str(old), str(new)) not in PAPER_SESSION_SEED_PAIRS:
                raise FakeCheckViolation(
                    "paper_session_allowed_transitions",
                    table,
                    f"paper_sessions {stored.get('id')}: {old} -> {new} is not a permitted "
                    f"Paper_Session state transition (Requirement 17.14) (23514)",
                )

    def _enforce_unique(self, table: str, row: Dict[str, Any]) -> None:
        existing = getattr(self, self.TABLES[table])

        if table == repo.ACCOUNTS_TABLE:
            if row.get("session_id") is None:
                # uq_paper_account_default: PARTIAL, WHERE session_id IS NULL.
                for other in existing:
                    if other.get("session_id") is None and (
                        str(other.get("user_id")),
                        str(other.get("currency")),
                    ) == (str(row.get("user_id")), str(row.get("currency"))):
                        raise FakeUniqueViolation("uq_paper_account_default", table)
            else:
                # uq_paper_account_session: UNIQUE (session_id, currency).
                for other in existing:
                    if (str(other.get("session_id")), str(other.get("currency"))) == (
                        str(row.get("session_id")),
                        str(row.get("currency")),
                    ):
                        raise FakeUniqueViolation("uq_paper_account_session", table)

        elif table == repo.ORDERS_TABLE and row.get("idempotency_key") is not None:
            if row.get("session_id") is None:
                # uq_paper_order_idem_default (013): PARTIAL unique on
                # (account_id, idempotency_key) WHERE session_id IS NULL AND idempotency_key IS
                # NOT NULL. This is the index that makes the default account's idempotency claim
                # non-vacuous. Without it modelled here, every default-account idempotency
                # assertion in this file would pass for the wrong reason - the second order would
                # simply be inserted.
                for other in existing:
                    if other.get("session_id") is None and (
                        str(other.get("account_id")),
                        other.get("idempotency_key"),
                    ) == (str(row.get("account_id")), row.get("idempotency_key")):
                        raise FakeUniqueViolation("uq_paper_order_idem_default", table)
            else:
                # uq_paper_order_idem: UNIQUE (session_id, idempotency_key). Rows with a NULL
                # session_id are deliberately EXCLUDED from this branch, because SQL treats two
                # NULLs as DISTINCT and this index therefore cannot arbitrate them - which is the
                # whole reason 013 adds the companion above. A double that compared
                # str(None) == str(None) here would report a uniqueness PostgreSQL does not have,
                # and the default-account tests would prove nothing about the real database.
                for other in existing:
                    if other.get("session_id") is not None and (
                        str(other.get("session_id")),
                        other.get("idempotency_key"),
                    ) == (str(row.get("session_id")), row.get("idempotency_key")):
                        raise FakeUniqueViolation("uq_paper_order_idem", table)

        elif table == repo.FILLS_TABLE:
            # uq_paper_fill_event: UNIQUE (order_id, fill_event_id).
            for other in existing:
                if (str(other.get("order_id")), other.get("fill_event_id")) == (
                    str(row.get("order_id")),
                    row.get("fill_event_id"),
                ):
                    raise FakeUniqueViolation("uq_paper_fill_event", table)

        elif table == repo.MARKET_EVENTS_TABLE:
            # uq_paper_market_event: UNIQUE (session_id, source_event_id). Requirement 14.7's
            # DURABLE dedupe - the arbiter the in-process LRU is only a cache for - so it is
            # enforced here rather than recorded: a double that accepted the second copy would let
            # `next_validated_event`'s no-op branch pass without ever being entered.
            for other in existing:
                if (str(other.get("session_id")), other.get("source_event_id")) == (
                    str(row.get("session_id")),
                    row.get("source_event_id"),
                ):
                    raise FakeUniqueViolation("uq_paper_market_event", table)

        elif table == repo.EVENTS_TABLE:
            # uq_paper_event_seq UNIQUE (session_id, sequence) and uq_paper_event_id UNIQUE
            # (session_id, event_id) - both, because they catch different mistakes: two writers
            # that read the same sequence, and one outage recorded twice.
            for other in existing:
                if str(other.get("session_id")) == str(row.get("session_id")):
                    if str(other.get("sequence")) == str(row.get("sequence")):
                        raise FakeUniqueViolation("uq_paper_event_seq", table)
                    if other.get("event_id") == row.get("event_id"):
                        raise FakeUniqueViolation("uq_paper_event_id", table)

        elif table == repo.POSITIONS_TABLE and row.get("closed_at") is None:
            # uq_paper_position_open: UNIQUE (account_id, symbol) WHERE closed_at IS NULL.
            for other in existing:
                if other.get("closed_at") is None and (
                    str(other.get("account_id")),
                    other.get("symbol"),
                ) == (str(row.get("account_id")), row.get("symbol")):
                    raise FakeUniqueViolation("uq_paper_position_open", table)

    @staticmethod
    def _sortable(value: Any) -> Any:
        """One column value, as something two rows can be compared on.

        PostgreSQL orders a ``BIGINT`` column numerically and a ``TEXT`` column lexicographically,
        and ``paper_events.sequence`` is the first column this double is asked to order by where the
        difference is observable: ten events sorted as text put 10 before 9. So an integer (and an
        integer-valued string, which is how a JSONB or NUMERIC round trip can present one) sorts
        numerically and everything else sorts as text, with numbers ahead of text so the two groups
        never interleave unpredictably.
        """
        if isinstance(value, bool) or value is None:
            return (1, str(value or ""))
        if isinstance(value, int):
            return (0, value)
        text = str(value)
        try:
            return (0, int(text))
        except ValueError:
            return (1, text)

    @staticmethod
    def _matching(rows: List[Dict[str, Any]], q: _Query) -> List[Dict[str, Any]]:
        matched = list(rows)
        for operator, col, val in q.filters:
            if operator == "is":
                assert str(val).lower() == "null", (
                    "the only IS predicate PostgREST needs here is IS NULL; "
                    f"got {val!r} on {col}"
                )
                matched = [r for r in matched if r.get(col) is None]
            elif operator == "gt":
                # Numeric where both sides are numeric, which is every use this double has: the
                # replay read's `sequence > n`. See :meth:`_sortable` for why text comparison would
                # be wrong rather than merely different.
                matched = [
                    r
                    for r in matched
                    if FakeSupabase._sortable(r.get(col)) > FakeSupabase._sortable(val)
                ]
            else:
                matched = [r for r in matched if str(r.get(col)) == str(val)]
        return matched

    # -- assertion helpers -------------------------------------------------
    def statements_on(self, table: str, *ops: str) -> List[_Query]:
        wanted = set(ops) or None
        return [
            s
            for s in self.statements
            if s.table_name == table and (wanted is None or s.op in wanted)
        ]

    def wrote_anything(self) -> bool:
        return any(s.op in ("insert", "update") for s in self.statements)

    def default_account(self, user_id: str = USER) -> Dict[str, Any]:
        for row in self.accounts:
            if str(row.get("user_id")) == user_id and row.get("session_id") is None:
                return row
        raise AssertionError(f"no default account for {user_id!r}")


# ══════════════════════════════════════════════════════════════════════════
# FIXTURES
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _fresh_probe() -> Any:
    """Forget the cached migration verdict around every test.

    The probe caches a positive verdict for the life of the process, which is the behaviour under
    test in :func:`test_the_probe_runs_once_per_process`. Without this, the first test to take a
    verdict would decide it for every test after it.
    """
    repo.reset_persistence_probe()
    yield
    repo.reset_persistence_probe()


def _client(**kwargs: Any) -> FakeSupabase:
    return FakeSupabase(**kwargs)


def _seeded() -> Tuple[FakeSupabase, Dict[str, Any]]:
    """A client holding one default account for :data:`USER`, and that account row."""
    client = _client()
    account = repo.get_or_create_account(client, USER, initial_capital=CAPITAL)
    return client, account


# ══════════════════════════════════════════════════════════════════════════
# THE DOUBLE IS A DATABASE (without this, the single-default claim is vacuous)
# ══════════════════════════════════════════════════════════════════════════


def test_the_double_enforces_uq_paper_account_default() -> None:
    """A second row with ``session_id IS NULL`` for the same ``(user, currency)`` is refused."""
    client = _client()
    client.table(repo.ACCOUNTS_TABLE).insert(
        {"user_id": USER, "session_id": None, "currency": "USD", "initial_capital": "1"}
    ).execute()

    with pytest.raises(FakeUniqueViolation) as caught:
        client.table(repo.ACCOUNTS_TABLE).insert(
            {"user_id": USER, "session_id": None, "currency": "USD", "initial_capital": "2"}
        ).execute()

    assert "uq_paper_account_default" in str(caught.value)
    assert len(client.accounts) == 1


def test_the_double_enforces_uq_paper_account_session() -> None:
    """A second account for the same ``(session, currency)`` is refused."""
    client = _client()
    client.table(repo.ACCOUNTS_TABLE).insert(
        {"user_id": USER, "session_id": SESSION, "currency": "USD", "initial_capital": "1"}
    ).execute()

    with pytest.raises(FakeUniqueViolation) as caught:
        client.table(repo.ACCOUNTS_TABLE).insert(
            {"user_id": USER, "session_id": SESSION, "currency": "USD", "initial_capital": "2"}
        ).execute()

    assert "uq_paper_account_session" in str(caught.value)


def test_the_default_index_is_partial_so_session_accounts_coexist() -> None:
    """One default plus many session accounts in the same currency - the PARTIAL index's point."""
    client = _client()
    for session_id in (None, SESSION, OTHER_SESSION):
        client.table(repo.ACCOUNTS_TABLE).insert(
            {
                "user_id": USER,
                "session_id": session_id,
                "currency": "USD",
                "initial_capital": "1",
            }
        ).execute()
    assert len(client.accounts) == 3


def test_the_double_enforces_uq_paper_order_idem_default() -> None:
    """A second default-account order under the same key on the same account is refused.

    ``uq_paper_order_idem_default`` (migration 013) is
    ``(account_id, idempotency_key) WHERE session_id IS NULL AND idempotency_key IS NOT NULL``.
    Without the double enforcing it, every default-account idempotency assertion below would pass
    because the second order was simply inserted, not because anything prevented it.
    """
    client = _client()
    first = {
        "session_id": None,
        "account_id": "acct-1",
        "user_id": USER,
        "idempotency_key": "k",
    }
    client.table(repo.ORDERS_TABLE).insert(dict(first)).execute()

    with pytest.raises(FakeUniqueViolation) as caught:
        client.table(repo.ORDERS_TABLE).insert(dict(first)).execute()

    assert "uq_paper_order_idem_default" in str(caught.value)
    assert len(client.orders) == 1


def test_the_double_models_null_session_ids_as_distinct_in_the_base_index() -> None:
    """Two default-account orders sharing a key on DIFFERENT accounts coexist.

    This is the SQL semantics the whole reconciliation turns on: ``uq_paper_order_idem`` is
    ``(session_id, idempotency_key)`` and two NULL ``session_id`` values are DISTINCT, so that
    index cannot arbitrate default-account rows at all. The double must model that rather than
    comparing ``str(None) == str(None)``, or it would report a uniqueness the database does not
    have and the companion index would look unnecessary.
    """
    client = _client()
    for account_id in ("acct-1", "acct-2"):
        client.table(repo.ORDERS_TABLE).insert(
            {
                "session_id": None,
                "account_id": account_id,
                "user_id": USER,
                "idempotency_key": "k",
            }
        ).execute()
    assert len(client.orders) == 2

    # And a NULL-session row never collides with a session row carrying the same key.
    client.table(repo.ORDERS_TABLE).insert(
        {
            "session_id": SESSION,
            "account_id": "acct-1",
            "user_id": USER,
            "idempotency_key": "k",
        }
    ).execute()
    assert len(client.orders) == 3


# ══════════════════════════════════════════════════════════════════════════
# REFUSAL, NOT DEGRADATION (Requirements 17.2, 28.3)
# ══════════════════════════════════════════════════════════════════════════


def _entry_points(client: FakeSupabase) -> Dict[str, Any]:
    """Every public entry point, as a no-argument callable, for the refusal sweep."""
    return {
        "get_or_create_account": lambda: repo.get_or_create_account(client, USER),
        "read_account": lambda: repo.read_account(client, USER),
        "get_positions": lambda: repo.get_positions(client, USER),
        "get_orders": lambda: repo.get_orders(client, USER),
        "get_trades": lambda: repo.get_trades(client, USER),
        "get_equity_snapshots": lambda: repo.get_equity_snapshots(
            client, USER, session_id=SESSION
        ),
        "get_metrics": lambda: repo.get_metrics(client, USER, session_id=SESSION),
        "lock_account_for_update": lambda: repo.lock_account_for_update(client, USER),
        "bump_version": lambda: repo.bump_version(
            client, user_id=USER, account_id="a", expected_version=1, payload={}
        ),
        "probe_idempotency_key": lambda: repo.probe_idempotency_key(
            client, user_id=USER, session_id=SESSION, idempotency_key="k"
        ),
        "insert_order": lambda: repo.insert_order(
            client,
            session_id=SESSION,
            account_id="a",
            user_id=USER,
            symbol=SYMBOL,
            side="buy",
            order_type="market",
            quantity=Decimal("1"),
            fingerprint="f",
        ),
        "insert_fill": lambda: repo.insert_fill(
            client,
            order_id="o",
            session_id=SESSION,
            user_id=USER,
            fill_event_id="e",
            quantity=Decimal("1"),
            price=Decimal("2"),
            fee_minor=0,
            slippage_minor=0,
            filled_at=NOW,
        ),
        "upsert_position": lambda: repo.upsert_position(
            client,
            session_id=SESSION,
            account_id="a",
            user_id=USER,
            symbol=SYMBOL,
            side="LONG",
            size=Decimal("1"),
            entry_price=Decimal("2"),
            opened_at=NOW,
        ),
        "insert_balance_event": lambda: repo.insert_balance_event(
            client,
            session_id=SESSION,
            account_id="a",
            user_id=USER,
            cause="FILL",
            available_delta=Decimal("0"),
            locked_delta=Decimal("0"),
            realized_delta=Decimal("0"),
            available_after=Decimal("0"),
            locked_after=Decimal("0"),
            realized_after=Decimal("0"),
            occurred_at=NOW,
        ),
        "insert_trade": lambda: repo.insert_trade(
            client,
            session_id=SESSION,
            account_id="a",
            user_id=USER,
            symbol=SYMBOL,
            side="LONG",
            quantity=Decimal("1"),
            entry_price=Decimal("2"),
            exit_price=Decimal("3"),
            realized_pnl=Decimal("1"),
            fee_minor=0,
            opened_at=NOW,
            closed_at=NOW,
        ),
        "insert_equity_snapshot": lambda: repo.insert_equity_snapshot(
            client,
            session_id=SESSION,
            user_id=USER,
            total_equity=Decimal("1"),
            available_balance=Decimal("1"),
            locked_balance=Decimal("0"),
            position_market_value=Decimal("0"),
            cause="FILL",
            taken_at=NOW,
        ),
    }


@pytest.mark.parametrize("entry_point", sorted(_entry_points(FakeSupabase())))
def test_every_entry_point_refuses_503_when_009_is_unapplied(entry_point: str) -> None:
    """Requirements 17.2, 28.3: an absent ``paper_accounts`` is an outage, not a memory balance."""
    client = _client(missing_tables=True)
    with pytest.raises(PaperError) as caught:
        _entry_points(client)[entry_point]()

    error = caught.value
    assert error.code == PAPER_PERSISTENCE_UNAVAILABLE
    assert error.http_status == 503
    assert error.details.get("migration") == "009_paper_trading.sql"
    assert not client.wrote_anything(), (
        f"{entry_point} issued a write against an absent relation"
    )


def test_the_refusal_names_the_bare_migration_file_so_it_survives_redaction() -> None:
    """``details["migration"]`` must not be the repository path.

    ``marketplace.errors.FORBIDDEN_BODY_SUBSTRINGS`` lists ``"backend_app"``, so a full path would
    be replaced by the redaction placeholder and the one refusal that exists to tell an operator
    what to apply would name nothing.
    """
    client = _client(missing_tables=True)
    with pytest.raises(PaperError) as caught:
        repo.read_account(client, USER)

    migration = caught.value.details["migration"]
    assert migration == repo.PAPER_TRADING_MIGRATION_FILE
    assert "backend_app" not in migration
    assert repo.PAPER_TRADING_MIGRATION.endswith(migration)


def test_there_is_no_memory_fallback_after_a_refusal() -> None:
    """A refused read leaves nothing cached that a later read could serve as a balance."""
    absent = _client(missing_tables=True)
    with pytest.raises(PaperError):
        repo.get_or_create_account(absent, USER, initial_capital=CAPITAL)
    assert absent.accounts == []

    # A fresh client whose tables exist starts from no rows: the refused call stored nothing
    # anywhere a second call could find it.
    repo.reset_persistence_probe()
    present = _client()
    assert repo.read_account(present, USER) is None


def test_the_probe_runs_once_per_process() -> None:
    """One ``SELECT id FROM paper_accounts LIMIT 1``, cached, however many calls follow."""
    client = _client()
    repo.read_account(client, USER)
    repo.read_account(client, USER)
    repo.get_positions(client, USER)

    probes = [
        s
        for s in client.statements_on(repo.ACCOUNTS_TABLE, "select")
        if s.cols == repo.PROBE_SELECT
    ]
    assert len(probes) == 1
    assert probes[0].limit_value == 1


def test_a_negative_verdict_learned_from_a_statement_is_remembered() -> None:
    """A process that probed successfully and then met ``42P01`` refuses without re-probing."""
    client = _client()
    assert repo.paper_persistence_supported(client) is True

    client.missing_tables = True
    with pytest.raises(PaperError) as caught:
        repo.get_positions(client, USER)
    assert caught.value.code == PAPER_PERSISTENCE_UNAVAILABLE

    client.missing_tables = False
    # The verdict is now negative and cached, so the next call refuses on the cache alone.
    before = len(client.statements)
    with pytest.raises(PaperError):
        repo.get_positions(client, USER)
    assert len(client.statements) == before, "a cached negative verdict still issued a statement"


def test_an_inconclusive_probe_attempts_the_statement_rather_than_reporting_a_migration() -> None:
    """A failure that does not say "no such relation" must not be relabelled as an unapplied 009."""
    client = _client(raise_on={("select", repo.ACCOUNTS_TABLE)})
    with pytest.raises(repo.PaperPersistenceError):
        repo.read_account(client, USER)


# ══════════════════════════════════════════════════════════════════════════
# A READ THAT DID NOT COMPLETE IS NOT A READ THAT FOUND NOTHING
# ══════════════════════════════════════════════════════════════════════════


def test_a_raised_read_failure_is_not_an_empty_answer() -> None:
    client = _client(raise_on={("select", repo.POSITIONS_TABLE)})
    with pytest.raises(repo.PaperPersistenceError):
        repo.get_positions(client, USER)


def test_an_error_envelope_is_not_an_empty_answer() -> None:
    """A response that "completed" carrying an ``error`` is a failure, not zero rows."""
    client = _client(error_on={("select", repo.TRADES_TABLE)})
    with pytest.raises(repo.PaperPersistenceError):
        repo.get_trades(client, USER)


def test_a_broken_idempotency_read_never_answers_no_such_order() -> None:
    """The costliest confusion of the two: it would place a second order for one request."""
    client = _client(error_on={("select", repo.ORDERS_TABLE)})
    with pytest.raises(repo.PaperPersistenceError):
        repo.probe_idempotency_key(
            client, user_id=USER, session_id=SESSION, idempotency_key="key-1"
        )


def test_an_empty_result_set_is_an_answer() -> None:
    """The other side of that claim: a completed read matching nothing answers, not raises."""
    client = _client()
    assert repo.read_account(client, USER) is None
    assert repo.get_positions(client, USER) == []
    assert repo.get_orders(client, USER) == []
    assert repo.get_trades(client, USER) == []
    assert repo.get_equity_snapshots(client, USER, session_id=SESSION) == []
    assert repo.get_metrics(client, USER, session_id=SESSION) is None


# ══════════════════════════════════════════════════════════════════════════
# THE DEFAULT ACCOUNT (Requirement 17.1, design.md -> "The default account")
# ══════════════════════════════════════════════════════════════════════════


def test_get_or_create_account_creates_the_default_account_at_session_id_null() -> None:
    client = _client()
    account = repo.get_or_create_account(client, USER, initial_capital=CAPITAL)

    assert account["session_id"] is None
    assert account["user_id"] == USER
    assert account["currency"] == "USD"
    assert Decimal(account["initial_capital"]) == CAPITAL
    assert Decimal(account["available_balance"]) == CAPITAL
    assert Decimal(account["locked_balance"]) == Decimal("0")
    # available + locked + position_market_value, with nothing locked and no positions.
    assert Decimal(account["total_equity"]) == CAPITAL
    assert account["version"] == 1


def test_get_or_create_account_is_idempotent_and_inserts_once() -> None:
    client = _client()
    first = repo.get_or_create_account(client, USER, initial_capital=CAPITAL)
    second = repo.get_or_create_account(client, USER, initial_capital=Decimal("5"))

    assert second["id"] == first["id"]
    assert Decimal(second["initial_capital"]) == CAPITAL, (
        "the second call must return the stored account, not reopen it at a new capital"
    )
    assert len(client.statements_on(repo.ACCOUNTS_TABLE, "insert")) == 1
    assert len(client.accounts) == 1


def test_the_default_account_is_located_by_is_null_and_not_by_eq_none() -> None:
    """``.eq("session_id", None)`` renders as ``session_id=eq.None`` and matches nothing.

    Which would make every call insert a new default account, leaving
    ``uq_paper_account_default`` as the only thing between a user and two balances. The predicate
    must be ``IS NULL``.
    """
    client, _ = _seeded()
    read = client.statements_on(repo.ACCOUNTS_TABLE, "select")[-1]
    assert ("is", "session_id", "null") in read.filters
    assert ("eq", "session_id", None) not in read.filters


def test_a_concurrent_first_request_loses_to_the_index_and_reads_the_winner() -> None:
    """Read-then-insert is not atomic over HTTP. The index decides, and the loser reads the row.

    This is the assertion that makes ``uq_paper_account_default`` operational rather than
    decorative: the second writer receives the winner's account, not a ``23505``, and no second
    row exists.
    """
    client = _client()

    def _competing_writer(fake: FakeSupabase, query: _Query) -> None:
        if query.table_name != repo.ACCOUNTS_TABLE:
            return
        fake.before_insert = None  # the competitor writes once
        fake.accounts.append(
            fake._defaults(
                repo.ACCOUNTS_TABLE,
                {
                    "id": "winner",
                    "user_id": USER,
                    "session_id": None,
                    "currency": "USD",
                    "initial_capital": "777",
                    "available_balance": "777",
                    "locked_balance": "0",
                    "total_equity": "777",
                },
            )
        )

    client.before_insert = _competing_writer
    account = repo.get_or_create_account(client, USER, initial_capital=CAPITAL)

    assert account["id"] == "winner"
    assert Decimal(account["initial_capital"]) == Decimal("777")
    assert len(client.accounts) == 1


def test_a_session_account_is_a_different_account_from_the_default() -> None:
    """Requirement 17.6: a session's balances are its own, and are not what ``/account`` serves."""
    client = _client()
    default = repo.get_or_create_account(client, USER, initial_capital=CAPITAL)
    scoped = repo.get_or_create_account(
        client, USER, "USD", SESSION, initial_capital=Decimal("500")
    )

    assert scoped["id"] != default["id"]
    assert scoped["session_id"] == SESSION
    assert repo.read_account(client, USER)["id"] == default["id"]
    assert repo.read_account(client, USER, "USD", SESSION)["id"] == scoped["id"]


def test_two_sessions_hold_independent_accounts_in_the_same_currency() -> None:
    client = _client()
    first = repo.get_or_create_account(client, USER, "USD", SESSION, initial_capital=CAPITAL)
    second = repo.get_or_create_account(
        client, USER, "USD", OTHER_SESSION, initial_capital=CAPITAL
    )
    assert first["id"] != second["id"]


def test_currencies_are_normalised_to_upper_case() -> None:
    """So ``usd`` and ``USD`` are the same account rather than two under the partial index."""
    client = _client()
    created = repo.get_or_create_account(client, USER, "usd", initial_capital=CAPITAL)
    assert created["currency"] == "USD"
    assert repo.read_account(client, USER, "USD")["id"] == created["id"]
    assert len(client.accounts) == 1


def test_a_float_capital_is_refused_rather_than_persisted() -> None:
    """Requirement 18.1: a binary float has already lost exactness before it reaches storage."""
    client = _client()
    with pytest.raises(Exception) as caught:
        repo.get_or_create_account(client, USER, initial_capital=100000.10)
    assert "float" in str(caught.value).lower()
    assert client.accounts == []


def test_a_blank_user_id_is_refused_before_any_statement() -> None:
    """Without the identity the tenant predicate would match on ``''``."""
    client = _client()
    with pytest.raises(ValueError):
        repo.read_account(client, "   ")


# ══════════════════════════════════════════════════════════════════════════
# TENANT ISOLATION AS A PREDICATE (Requirements 21.2, 21.5)
# ══════════════════════════════════════════════════════════════════════════


def test_every_read_carries_user_id_as_a_predicate() -> None:
    """Requirement 21.5: scoped in the query, not filtered after retrieval."""
    client, account = _seeded()
    account_id = account["id"]

    repo.read_account(client, USER)
    repo.get_positions(client, USER, account_id=account_id)
    repo.get_orders(client, USER, account_id=account_id)
    repo.get_trades(client, USER, account_id=account_id)
    repo.get_equity_snapshots(client, USER, session_id=SESSION)
    repo.get_metrics(client, USER, session_id=SESSION)
    repo.probe_idempotency_key(client, user_id=USER, session_id=SESSION, idempotency_key="k")

    reads = [s for s in client.statements if s.op == "select" and s.cols != repo.PROBE_SELECT]
    assert reads, "no projected read was issued"
    for statement in reads:
        assert "user_id" in statement.filtered_columns(), (
            f"the {statement.table_name} read did not scope by user_id"
        )
        assert statement.filter_value("user_id") == USER


def test_another_users_default_account_is_never_fetched() -> None:
    client = _client()
    repo.get_or_create_account(client, OTHER_USER, initial_capital=CAPITAL)

    assert repo.read_account(client, USER) is None
    read = client.statements_on(repo.ACCOUNTS_TABLE, "select")[-1]
    assert read.filter_value("user_id") == USER


def test_another_users_rows_are_absent_from_every_list_read() -> None:
    client = _client()
    other = repo.get_or_create_account(client, OTHER_USER, initial_capital=CAPITAL)
    repo.upsert_position(
        client,
        session_id=OTHER_SESSION,
        account_id=other["id"],
        user_id=OTHER_USER,
        symbol=SYMBOL,
        side="LONG",
        size=Decimal("1"),
        entry_price=Decimal("100"),
        opened_at=NOW,
    )
    repo.insert_trade(
        client,
        session_id=OTHER_SESSION,
        account_id=other["id"],
        user_id=OTHER_USER,
        symbol=SYMBOL,
        side="LONG",
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        exit_price=Decimal("110"),
        realized_pnl=Decimal("10"),
        fee_minor=0,
        opened_at=NOW,
        closed_at=NOW,
    )

    assert repo.get_positions(client, USER) == []
    assert repo.get_trades(client, USER) == []


def test_bump_version_will_not_move_another_users_account() -> None:
    """The identity is on the UPDATE too, so a guessed ``account_id`` moves nothing."""
    client = _client()
    victim = repo.get_or_create_account(client, OTHER_USER, initial_capital=CAPITAL)

    with pytest.raises(repo.PaperConcurrencyConflict):
        repo.bump_version(
            client,
            user_id=USER,
            account_id=victim["id"],
            expected_version=victim["version"],
            payload={"available_balance": Decimal("0")},
        )

    assert Decimal(client.accounts[0]["available_balance"]) == CAPITAL
    assert client.accounts[0]["version"] == 1


def test_lock_account_for_update_will_not_return_another_users_account() -> None:
    client = _client()
    victim = repo.get_or_create_account(client, OTHER_USER, initial_capital=CAPITAL)
    with pytest.raises(repo.PaperPersistenceError):
        repo.lock_account_for_update(client, USER, account_id=victim["id"])


# ══════════════════════════════════════════════════════════════════════════
# THE OPTIMISTIC LOCK - WHAT SUBSTITUTES FOR ``SELECT ... FOR UPDATE``
# ══════════════════════════════════════════════════════════════════════════


def test_lock_then_bump_moves_the_balance_and_increments_the_version() -> None:
    client, account = _seeded()

    locked = repo.lock_account_for_update(client, USER, account_id=account["id"])
    assert locked["version"] == 1

    updated = repo.bump_version(
        client,
        user_id=USER,
        account_id=locked["id"],
        expected_version=locked["version"],
        payload={
            "available_balance": Decimal("90000"),
            "locked_balance": Decimal("10000"),
            "total_equity": CAPITAL,
        },
    )

    assert updated["version"] == 2
    assert Decimal(updated["available_balance"]) == Decimal("90000")
    assert Decimal(updated["locked_balance"]) == Decimal("10000")


def test_the_update_carries_the_read_version_as_a_predicate() -> None:
    """The whole substitution: the version is in the ``WHERE`` clause, not just the payload."""
    client, account = _seeded()
    repo.bump_version(
        client,
        user_id=USER,
        account_id=account["id"],
        expected_version=1,
        payload={"available_balance": Decimal("1")},
    )

    update = client.statements_on(repo.ACCOUNTS_TABLE, "update")[-1]
    assert ("eq", "version", 1) in update.filters
    assert update.payload["version"] == 2


def test_a_stale_version_is_a_conflict_and_writes_nothing() -> None:
    """A lost update surfaces as a conflict rather than as a silently overwritten balance."""
    client, account = _seeded()

    repo.bump_version(
        client,
        user_id=USER,
        account_id=account["id"],
        expected_version=1,
        payload={"available_balance": Decimal("50000")},
    )

    with pytest.raises(repo.PaperConcurrencyConflict) as caught:
        repo.bump_version(
            client,
            user_id=USER,
            account_id=account["id"],
            expected_version=1,  # the image this writer read, now stale
            payload={"available_balance": Decimal("0")},
        )

    assert caught.value.table == repo.ACCOUNTS_TABLE
    assert Decimal(client.default_account()["available_balance"]) == Decimal("50000")


def test_bump_version_refuses_to_write_the_identity_or_the_counter() -> None:
    """The guard is made of ``id``, ``user_id`` and ``version``; a payload cannot rewrite them."""
    client, account = _seeded()
    for column in ("id", "user_id", "session_id", "currency", "version", "created_at"):
        with pytest.raises(ValueError):
            repo.bump_version(
                client,
                user_id=USER,
                account_id=account["id"],
                expected_version=1,
                payload={column: "x"},
            )


def test_a_float_balance_is_refused_by_bump_version() -> None:
    client, account = _seeded()
    with pytest.raises(Exception) as caught:
        repo.bump_version(
            client,
            user_id=USER,
            account_id=account["id"],
            expected_version=1,
            payload={"available_balance": 90000.10},
        )
    assert "float" in str(caught.value).lower()
    assert client.default_account()["version"] == 1


def test_lock_account_for_update_locates_the_default_account_without_an_id() -> None:
    client, account = _seeded()
    locked = repo.lock_account_for_update(client, USER)
    assert locked["id"] == account["id"]


def test_lock_account_for_update_refuses_when_there_is_no_account() -> None:
    """Answering ``None`` would let the caller compute new balances against nothing."""
    client = _client()
    with pytest.raises(repo.PaperPersistenceError):
        repo.lock_account_for_update(client, USER)


# ══════════════════════════════════════════════════════════════════════════
# ORDERS (Requirements 16.8, 16.11, 17.12)
# ══════════════════════════════════════════════════════════════════════════


def _order(client: FakeSupabase, account_id: str, **kwargs: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "session_id": SESSION,
        "account_id": account_id,
        "user_id": USER,
        "symbol": SYMBOL,
        "side": "buy",
        "order_type": "limit",
        "quantity": Decimal("2"),
        "limit_price": Decimal("100"),
        "fingerprint": "fp-1",
    }
    payload.update(kwargs)
    return repo.insert_order(client, **payload)


@pytest.mark.parametrize(
    "state,legacy",
    [
        (PaperOrderState.CREATED, "NEW"),
        (PaperOrderState.ACCEPTED, "OPEN"),
        (PaperOrderState.PARTIALLY_FILLED, "OPEN"),
        (PaperOrderState.FILLED, "FILLED"),
        (PaperOrderState.CANCELLED, "CANCELLED"),
        (PaperOrderState.REJECTED, "REJECTED"),
    ],
)
def test_legacy_status_is_derived_from_the_state_and_never_passed(
    state: PaperOrderState, legacy: str
) -> None:
    """Requirement 17.12: ``?status=OPEN`` keeps its exact meaning, from one mapping.

    Both ``ACCEPTED`` and ``PARTIALLY_FILLED`` answer ``OPEN`` - which is what makes an order
    live on the book, partly filled or not, still appear where it appears today.
    """
    client, account = _seeded()
    order = _order(client, account["id"], order_state=state)
    assert order["order_state"] == state.value
    assert order["legacy_status"] == legacy


def test_insert_order_will_not_accept_a_legacy_status_argument() -> None:
    """An order that was ``FILLED`` and ``OPEN`` at once must be unrepresentable."""
    client, account = _seeded()
    with pytest.raises(TypeError):
        _order(client, account["id"], legacy_status="OPEN")


def test_get_orders_filters_legacy_status_in_the_statement() -> None:
    """Requirement 21.5 applies to the status filter too: a predicate, not a post-filter."""
    client, account = _seeded()
    _order(client, account["id"], order_state=PaperOrderState.ACCEPTED, fingerprint="a")
    _order(client, account["id"], order_state=PaperOrderState.PARTIALLY_FILLED, fingerprint="b")
    _order(client, account["id"], order_state=PaperOrderState.FILLED, fingerprint="c")

    open_orders = repo.get_orders(client, USER, legacy_status="OPEN")

    assert {o["order_state"] for o in open_orders} == {"ACCEPTED", "PARTIALLY_FILLED"}
    read = client.statements_on(repo.ORDERS_TABLE, "select")[-1]
    assert read.filter_value("legacy_status") == "OPEN"


def test_get_orders_can_filter_the_canonical_state_too() -> None:
    client, account = _seeded()
    _order(client, account["id"], order_state=PaperOrderState.ACCEPTED, fingerprint="a")
    _order(client, account["id"], order_state=PaperOrderState.FILLED, fingerprint="b")

    filled = repo.get_orders(client, USER, order_state=PaperOrderState.FILLED)
    assert [o["order_state"] for o in filled] == ["FILLED"]


def test_an_unknown_order_state_is_refused_rather_than_queried() -> None:
    client, _ = _seeded()
    with pytest.raises(ValueError):
        repo.get_orders(client, USER, order_state="PENDING")


def test_an_unsupported_side_or_order_type_is_refused() -> None:
    """``chk_paper_order_side`` and ``chk_paper_order_type``, named before the statement."""
    client, account = _seeded()
    with pytest.raises(ValueError):
        _order(client, account["id"], side="short")
    with pytest.raises(ValueError):
        _order(client, account["id"], order_type="stop")
    assert client.orders == []


def test_probe_idempotency_key_finds_the_one_order_under_that_key() -> None:
    client, account = _seeded()
    created = _order(client, account["id"], idempotency_key="key-1")

    found = repo.probe_idempotency_key(
        client, user_id=USER, session_id=SESSION, idempotency_key="key-1"
    )
    assert found is not None
    assert found["id"] == created["id"]
    assert found["fingerprint"] == created["fingerprint"]


def test_probe_idempotency_key_answers_none_for_an_unused_key() -> None:
    client, _ = _seeded()
    assert (
        repo.probe_idempotency_key(
            client, user_id=USER, session_id=SESSION, idempotency_key="never-used"
        )
        is None
    )


def test_an_idempotency_key_is_scoped_to_one_session() -> None:
    """``uq_paper_order_idem`` is ``(session_id, idempotency_key)``: sessions may reuse a key."""
    client, account = _seeded()
    _order(client, account["id"], idempotency_key="key-1")
    reused = _order(
        client, account["id"], session_id=OTHER_SESSION, idempotency_key="key-1", fingerprint="b"
    )

    assert reused["session_id"] == OTHER_SESSION
    assert (
        repo.probe_idempotency_key(
            client, user_id=USER, session_id=OTHER_SESSION, idempotency_key="key-1"
        )["id"]
        == reused["id"]
    )


def test_a_duplicate_idempotency_key_is_a_conflict_and_inserts_no_second_order() -> None:
    """Requirement 16.8: exactly one order exists for one key within one session."""
    client, account = _seeded()
    _order(client, account["id"], idempotency_key="key-1")

    with pytest.raises(repo.PaperConcurrencyConflict):
        _order(client, account["id"], idempotency_key="key-1", fingerprint="different")

    assert len(client.orders) == 1


def test_an_over_long_idempotency_key_is_refused_before_the_statement() -> None:
    """``chk_paper_order_idem_len`` is the backstop, not the error surface."""
    client, account = _seeded()
    with pytest.raises(ValueError):
        _order(client, account["id"], idempotency_key="k" * 129)
    assert client.orders == []


# ══════════════════════════════════════════════════════════════════════════
# FILLS (Requirements 16.9, 18.13)
# ══════════════════════════════════════════════════════════════════════════


def test_insert_fill_records_the_fill_with_exact_integer_minor_units() -> None:
    client, account = _seeded()
    order = _order(client, account["id"])

    fill = repo.insert_fill(
        client,
        order_id=order["id"],
        session_id=SESSION,
        user_id=USER,
        fill_event_id="evt-1",
        quantity=Decimal("1.5"),
        price=Decimal("100.25"),
        fee_minor=15,
        slippage_minor=3,
        filled_at=NOW,
    )

    assert Decimal(fill["quantity"]) == Decimal("1.5")
    assert Decimal(fill["price"]) == Decimal("100.25")
    assert fill["fee_minor"] == 15
    assert fill["slippage_minor"] == 3
    assert isinstance(fill["fee_minor"], int)


def test_a_repeated_fill_event_id_is_a_duplicate_and_not_a_second_movement() -> None:
    """Requirement 16.9 / 18.13, held by ``uq_paper_fill_event``."""
    client, account = _seeded()
    order = _order(client, account["id"])
    kwargs: Dict[str, Any] = {
        "order_id": order["id"],
        "session_id": SESSION,
        "user_id": USER,
        "fill_event_id": "evt-1",
        "quantity": Decimal("1"),
        "price": Decimal("100"),
        "fee_minor": 10,
        "slippage_minor": 0,
        "filled_at": NOW,
    }
    repo.insert_fill(client, **kwargs)

    with pytest.raises(repo.PaperDuplicateFill) as caught:
        repo.insert_fill(client, **kwargs)

    assert caught.value.fill_event_id == "evt-1"
    assert caught.value.order_id == order["id"]
    assert len(client.fills) == 1


def test_the_same_fill_event_id_on_a_different_order_is_not_a_duplicate() -> None:
    client, account = _seeded()
    first = _order(client, account["id"], fingerprint="a")
    second = _order(client, account["id"], fingerprint="b")
    for order in (first, second):
        repo.insert_fill(
            client,
            order_id=order["id"],
            session_id=SESSION,
            user_id=USER,
            fill_event_id="evt-1",
            quantity=Decimal("1"),
            price=Decimal("100"),
            fee_minor=0,
            slippage_minor=0,
            filled_at=NOW,
        )
    assert len(client.fills) == 2


def test_a_fractional_fee_minor_is_refused_rather_than_rounded() -> None:
    """Rounding here would move money by an amount no ledger row records."""
    client, account = _seeded()
    order = _order(client, account["id"])
    with pytest.raises(ValueError) as caught:
        repo.insert_fill(
            client,
            order_id=order["id"],
            session_id=SESSION,
            user_id=USER,
            fill_event_id="evt-1",
            quantity=Decimal("1"),
            price=Decimal("100"),
            fee_minor=Decimal("10.5"),
            slippage_minor=0,
            filled_at=NOW,
        )
    assert "minor unit" in str(caught.value)
    assert client.fills == []


def test_a_float_fill_price_is_refused() -> None:
    client, account = _seeded()
    order = _order(client, account["id"])
    with pytest.raises(Exception) as caught:
        repo.insert_fill(
            client,
            order_id=order["id"],
            session_id=SESSION,
            user_id=USER,
            fill_event_id="evt-1",
            quantity=Decimal("1"),
            price=100.25,
            fee_minor=0,
            slippage_minor=0,
            filled_at=NOW,
        )
    assert "float" in str(caught.value).lower()


# ══════════════════════════════════════════════════════════════════════════
# POSITIONS (Requirement 18.5)
# ══════════════════════════════════════════════════════════════════════════


def _position(client: FakeSupabase, account_id: str, **kwargs: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "session_id": SESSION,
        "account_id": account_id,
        "user_id": USER,
        "symbol": SYMBOL,
        "side": "LONG",
        "size": Decimal("2"),
        "entry_price": Decimal("100"),
        "opened_at": NOW,
    }
    payload.update(kwargs)
    return repo.upsert_position(client, **payload)


def test_upsert_position_inserts_then_updates_the_open_row() -> None:
    client, account = _seeded()
    opened = _position(client, account["id"])
    assert opened["version"] == 1

    grown = _position(client, account["id"], size=Decimal("3"), entry_price=Decimal("105"))

    assert grown["id"] == opened["id"], "a second open position was inserted for one symbol"
    assert grown["version"] == 2
    assert Decimal(grown["size"]) == Decimal("3")
    assert len(client.positions) == 1


def test_a_closed_position_persists_at_size_zero_with_closed_at_set() -> None:
    """Requirement 18.5, and the change from the existing ``del user_positions[symbol]``."""
    client, account = _seeded()
    _position(client, account["id"])
    closed = _position(client, account["id"], size=Decimal("0"), closed_at=NOW)

    assert Decimal(closed["size"]) == Decimal("0")
    assert closed["closed_at"] is not None
    assert len(client.positions) == 1, "the closed position was deleted rather than kept"


def test_get_positions_excludes_closed_rows_by_default_and_in_the_statement() -> None:
    client, account = _seeded()
    _position(client, account["id"])
    _position(client, account["id"], size=Decimal("0"), closed_at=NOW)

    assert repo.get_positions(client, USER) == []
    read = client.statements_on(repo.POSITIONS_TABLE, "select")[-1]
    assert ("is", "closed_at", "null") in read.filters

    assert len(repo.get_positions(client, USER, include_closed=True)) == 1


def test_a_symbol_can_be_reopened_once_its_position_is_closed() -> None:
    """The PARTIAL index releases on ``closed_at``, so history does not block a new open."""
    client, account = _seeded()
    first = _position(client, account["id"])
    _position(client, account["id"], size=Decimal("0"), closed_at=NOW)
    reopened = _position(client, account["id"], size=Decimal("1"), opened_at=NOW)

    assert reopened["id"] != first["id"]
    assert len(client.positions) == 2
    assert len(repo.get_positions(client, USER)) == 1


def test_a_position_side_outside_long_short_is_refused() -> None:
    """``chk_paper_position_side``: direction is a side value, never a negative size."""
    client, account = _seeded()
    with pytest.raises(ValueError):
        _position(client, account["id"], side="long")
    assert client.positions == []


def test_a_position_update_that_lost_the_version_race_is_a_conflict() -> None:
    """A writer that moved the row between the read and the update makes the update match nothing.

    Positions carry their own ``version`` column, and ``upsert_position`` is a read followed by a
    write because ``uq_paper_position_open`` is PARTIAL and so cannot be named in a PostgREST
    ``ON CONFLICT``. The version predicate is what makes the gap between the two detectable.
    """
    client, account = _seeded()
    _position(client, account["id"])

    def _a_competing_writer_moves_it(fake: FakeSupabase, query: _Query) -> None:
        if query.table_name != repo.POSITIONS_TABLE:
            return
        fake.after_select = None  # it writes once
        for row in fake.positions:
            row["version"] = row["version"] + 1
            row["size"] = "7"

    client.after_select = _a_competing_writer_moves_it

    with pytest.raises(repo.PaperConcurrencyConflict) as caught:
        _position(client, account["id"], size=Decimal("5"))

    assert caught.value.table == repo.POSITIONS_TABLE
    assert Decimal(client.positions[0]["size"]) == Decimal("7"), (
        "the losing writer overwrote the winner's size instead of being refused"
    )


# ══════════════════════════════════════════════════════════════════════════
# THE APPEND-ONLY LEDGERS (Requirements 18.7, 18.10, 18.11)
# ══════════════════════════════════════════════════════════════════════════


def test_insert_balance_event_records_the_three_deltas_and_the_three_results() -> None:
    client, account = _seeded()
    event = repo.insert_balance_event(
        client,
        session_id=SESSION,
        account_id=account["id"],
        user_id=USER,
        cause="ORDER_LOCK",
        available_delta=Decimal("-200"),
        locked_delta=Decimal("200"),
        realized_delta=Decimal("0"),
        available_after=Decimal("99800"),
        locked_after=Decimal("200"),
        realized_after=Decimal("0"),
        occurred_at=NOW,
    )

    assert event["cause"] == "ORDER_LOCK"
    assert Decimal(event["available_delta"]) == Decimal("-200")
    assert Decimal(event["locked_after"]) == Decimal("200")


@pytest.mark.parametrize("cause", list(repo.BALANCE_EVENT_CAUSES))
def test_every_balance_event_cause_the_constraint_permits_is_accepted(cause: str) -> None:
    client, account = _seeded()
    written = repo.insert_balance_event(
        client,
        session_id=SESSION,
        account_id=account["id"],
        user_id=USER,
        cause=cause,
        available_delta=Decimal("0"),
        locked_delta=Decimal("0"),
        realized_delta=Decimal("0"),
        available_after=Decimal("0"),
        locked_after=Decimal("0"),
        realized_after=Decimal("0"),
        occurred_at=NOW,
    )
    assert written["cause"] == cause


def test_a_balance_event_cause_outside_the_constraint_is_refused() -> None:
    client, account = _seeded()
    with pytest.raises(ValueError):
        repo.insert_balance_event(
            client,
            session_id=SESSION,
            account_id=account["id"],
            user_id=USER,
            cause="ADJUSTMENT",
            available_delta=Decimal("0"),
            locked_delta=Decimal("0"),
            realized_delta=Decimal("0"),
            available_after=Decimal("0"),
            locked_after=Decimal("0"),
            realized_after=Decimal("0"),
            occurred_at=NOW,
        )
    assert client.balance_events == []


def test_insert_trade_records_a_closed_round_trip() -> None:
    client, account = _seeded()
    trade = repo.insert_trade(
        client,
        session_id=SESSION,
        account_id=account["id"],
        user_id=USER,
        symbol=SYMBOL,
        side="LONG",
        quantity=Decimal("1.5"),
        entry_price=Decimal("100"),
        exit_price=Decimal("110"),
        realized_pnl=Decimal("15"),
        fee_minor=12,
        opened_at=NOW,
        closed_at=NOW + timedelta(minutes=5),
    )

    assert Decimal(trade["realized_pnl"]) == Decimal("15")
    assert trade["fee_minor"] == 12
    assert repo.get_trades(client, USER)[0]["id"] == trade["id"]


def test_get_trades_returns_the_most_recently_closed_first_and_honours_limit() -> None:
    client, account = _seeded()
    for index in range(3):
        repo.insert_trade(
            client,
            session_id=SESSION,
            account_id=account["id"],
            user_id=USER,
            symbol=SYMBOL,
            side="LONG",
            quantity=Decimal("1"),
            entry_price=Decimal("100"),
            exit_price=Decimal("101"),
            realized_pnl=Decimal("1"),
            fee_minor=0,
            opened_at=NOW,
            closed_at=NOW + timedelta(minutes=index),
        )

    newest_first = repo.get_trades(client, USER)
    closed = [row["closed_at"] for row in newest_first]
    assert closed == sorted(closed, reverse=True)
    assert len(repo.get_trades(client, USER, limit=2)) == 2


@pytest.mark.parametrize("cause", list(repo.EQUITY_SNAPSHOT_CAUSES))
def test_every_equity_snapshot_cause_requirement_18_11_names_is_accepted(cause: str) -> None:
    client, _ = _seeded()
    snapshot = repo.insert_equity_snapshot(
        client,
        session_id=SESSION,
        user_id=USER,
        total_equity=CAPITAL,
        available_balance=CAPITAL,
        locked_balance=Decimal("0"),
        position_market_value=Decimal("0"),
        cause=cause,
        taken_at=NOW,
    )
    assert snapshot["cause"] == cause
    assert snapshot["stale"] is False


def test_an_equity_snapshot_cause_outside_the_constraint_is_refused() -> None:
    client, _ = _seeded()
    with pytest.raises(ValueError):
        repo.insert_equity_snapshot(
            client,
            session_id=SESSION,
            user_id=USER,
            total_equity=CAPITAL,
            available_balance=CAPITAL,
            locked_balance=Decimal("0"),
            position_market_value=Decimal("0"),
            cause="TICK",
            taken_at=NOW,
        )
    assert client.equity_snapshots == []


def test_get_equity_snapshots_is_read_in_non_decreasing_order() -> None:
    """``paper_accounting.max_drawdown`` REFUSES an out-of-order series rather than sorting it.

    So the order has to come from the statement. Snapshots are written out of order here on
    purpose: an implementation that read them ``desc`` would pass a test that wrote them in order.
    """
    client, _ = _seeded()
    for offset in (2, 0, 1):
        repo.insert_equity_snapshot(
            client,
            session_id=SESSION,
            user_id=USER,
            total_equity=CAPITAL + Decimal(offset),
            available_balance=CAPITAL,
            locked_balance=Decimal("0"),
            position_market_value=Decimal(offset),
            cause="FILL",
            taken_at=NOW + timedelta(minutes=offset),
        )

    series = repo.get_equity_snapshots(client, USER, session_id=SESSION)
    taken = [row["taken_at"] for row in series]
    assert taken == sorted(taken)

    read = client.statements_on(repo.EQUITY_SNAPSHOTS_TABLE, "select")[-1]
    assert read.orders == [("series_index", False), ("taken_at", False)]


def test_a_reset_series_sorts_after_the_series_before_it() -> None:
    """Requirement 17.15: a reset begins a new series and the old snapshots stay readable."""
    client, _ = _seeded()
    for index, offset in ((1, 0), (0, 5)):
        repo.insert_equity_snapshot(
            client,
            session_id=SESSION,
            user_id=USER,
            total_equity=CAPITAL,
            available_balance=CAPITAL,
            locked_balance=Decimal("0"),
            position_market_value=Decimal("0"),
            cause="SESSION_START",
            taken_at=NOW + timedelta(minutes=offset),
            series_index=index,
        )

    series = repo.get_equity_snapshots(client, USER, session_id=SESSION)
    assert [row["series_index"] for row in series] == [0, 1]


def test_a_stale_equity_snapshot_records_that_it_is_stale() -> None:
    """Requirement 18.15: staleness is a fact about the snapshot, so it is stored on it."""
    client, _ = _seeded()
    snapshot = repo.insert_equity_snapshot(
        client,
        session_id=SESSION,
        user_id=USER,
        total_equity=CAPITAL,
        available_balance=CAPITAL,
        locked_balance=Decimal("0"),
        position_market_value=Decimal("0"),
        cause="REVALUATION",
        taken_at=NOW,
        stale=True,
    )
    assert snapshot["stale"] is True


# ══════════════════════════════════════════════════════════════════════════
# METRICS
# ══════════════════════════════════════════════════════════════════════════


def test_get_metrics_returns_the_most_recently_computed_row() -> None:
    """009 places no unique index on ``paper_metrics.session_id``, so the newest row is current."""
    client = _client(
        metrics=[
            {
                "id": "m-old",
                "session_id": SESSION,
                "user_id": USER,
                "win_rate": "0.40000",
                "computed_at": NOW.isoformat(),
            },
            {
                "id": "m-new",
                "session_id": SESSION,
                "user_id": USER,
                "win_rate": "0.60000",
                "computed_at": (NOW + timedelta(minutes=1)).isoformat(),
            },
        ]
    )
    current = repo.get_metrics(client, USER, session_id=SESSION)
    assert current is not None
    assert current["id"] == "m-new"


def test_get_metrics_answers_none_rather_than_zeroes_when_nothing_is_computed() -> None:
    """Requirement 28.5: an absent measurement is reported absent, never as a zero."""
    client, _ = _seeded()
    assert repo.get_metrics(client, USER, session_id=SESSION) is None


# ══════════════════════════════════════════════════════════════════════════
# THE DEFAULT ACCOUNT OWNS ITS CHILDREN (migration 013)
# ══════════════════════════════════════════════════════════════════════════
#
# 009 declared ``session_id NOT NULL`` on paper_orders, paper_fills, paper_positions,
# paper_balance_events, paper_trades, paper_equity_snapshots and paper_metrics, while
# paper_accounts.session_id was nullable - and that nullable column IS the default account. So the
# account the six existing /api/paper/* endpoints serve had nowhere to persist an order, a fill, a
# position, a trade or an equity point: a child row needed a paper_sessions row, and paper_sessions
# requires nine NOT NULL columns a default account has no honest value for.
#
# 013_paper_default_account_children.sql relaxes those seven columns and reconciles the two
# guarantees that had rested on the column being non-null. Both reconciliations are asserted here,
# because both failures would have been SILENT:
#
#   * ``uq_paper_order_idem`` is (session_id, idempotency_key) and SQL treats two NULLs as
#     DISTINCT, so it stops de-duplicating default-account orders entirely. The companion
#     ``uq_paper_order_idem_default`` is what makes the idempotency claim true for that account,
#     and the double enforces it (see test_the_double_enforces_uq_paper_order_idem_default) so
#     these assertions are not vacuous.
#   * A database with 009 and not 013 refuses every default-account child write with 23502. That
#     must be answered by a 503 naming 013 - not 009, which IS applied.


def _default_account(client: FakeSupabase) -> Dict[str, Any]:
    """The default account row, created through the module rather than assembled by hand."""
    return repo.get_or_create_account(client, USER, initial_capital=CAPITAL)


def test_a_default_account_order_is_written_with_a_null_session_id() -> None:
    """``session_id`` defaults to ``None``, and ``None`` is what lands on the row."""
    client = _client()
    account = _default_account(client)

    order = repo.insert_order(
        client,
        account_id=account["id"],
        user_id=USER,
        symbol=SYMBOL,
        side="buy",
        order_type="market",
        quantity=Decimal("1.5"),
        fingerprint="fp",
    )

    assert order["session_id"] is None
    assert order["account_id"] == account["id"]
    assert order["user_id"] == USER
    # The statement itself carried NULL, not the string "None" and not an omitted key.
    insert = client.statements_on(repo.ORDERS_TABLE, "insert")[-1]
    assert insert.payload is not None
    assert "session_id" in insert.payload
    assert insert.payload["session_id"] is None


def test_no_session_id_is_fabricated_for_the_default_account() -> None:
    """Requirement 28.3: a synthetic session id would be fabricated data, so none is invented."""
    client = _client()
    account = _default_account(client)

    for write in (
        lambda: repo.insert_order(
            client,
            account_id=account["id"],
            user_id=USER,
            symbol=SYMBOL,
            side="buy",
            order_type="market",
            quantity=Decimal("1"),
            fingerprint="fp",
        ),
        lambda: repo.upsert_position(
            client,
            account_id=account["id"],
            user_id=USER,
            symbol=SYMBOL,
            side="LONG",
            size=Decimal("1"),
            entry_price=Decimal("2"),
            opened_at=NOW,
        ),
        lambda: repo.insert_balance_event(
            client,
            account_id=account["id"],
            user_id=USER,
            cause="FILL",
            available_delta=Decimal("0"),
            locked_delta=Decimal("0"),
            realized_delta=Decimal("0"),
            available_after=CAPITAL,
            locked_after=Decimal("0"),
            realized_after=Decimal("0"),
            occurred_at=NOW,
        ),
        lambda: repo.insert_trade(
            client,
            account_id=account["id"],
            user_id=USER,
            symbol=SYMBOL,
            side="LONG",
            quantity=Decimal("1"),
            entry_price=Decimal("2"),
            exit_price=Decimal("3"),
            realized_pnl=Decimal("1"),
            fee_minor=0,
            opened_at=NOW,
            closed_at=NOW,
        ),
        lambda: repo.insert_equity_snapshot(
            client,
            user_id=USER,
            total_equity=CAPITAL,
            available_balance=CAPITAL,
            locked_balance=Decimal("0"),
            position_market_value=Decimal("0"),
            cause="SESSION_START",
            taken_at=NOW,
        ),
    ):
        row = write()
        assert row["session_id"] is None, (
            f"a write invented a session_id for the default account: {row.get('session_id')!r}"
        )


def test_the_default_account_write_path_end_to_end() -> None:
    """Order, fill, position, balance event, closed trade and equity point - then read them back.

    The path task 23.2 drives for ``POST /api/paper/orders`` on the default account. Every row is
    written with ``session_id IS NULL`` and every read finds it, which is the whole claim of the
    remediation: before 013 the first of these six writes was impossible.
    """
    client = _client()
    account = _default_account(client)
    aid = account["id"]

    order = repo.insert_order(
        client,
        account_id=aid,
        user_id=USER,
        symbol=SYMBOL,
        side="buy",
        order_type="market",
        quantity=Decimal("2"),
        fingerprint="fp",
        idempotency_key="idem-default",
    )
    fill = repo.insert_fill(
        client,
        order_id=order["id"],
        user_id=USER,
        fill_event_id="fill-1",
        quantity=Decimal("2"),
        price=Decimal("30000.1234567890"),
        fee_minor=13,
        slippage_minor=2,
        filled_at=NOW,
    )
    position = repo.upsert_position(
        client,
        account_id=aid,
        user_id=USER,
        symbol=SYMBOL,
        side="LONG",
        size=Decimal("2"),
        entry_price=Decimal("30000.1234567890"),
        opened_at=NOW,
    )
    balance_event = repo.insert_balance_event(
        client,
        account_id=aid,
        user_id=USER,
        cause="FILL",
        available_delta=Decimal("-60000.246913578"),
        locked_delta=Decimal("0"),
        realized_delta=Decimal("0"),
        available_after=Decimal("39999.753086422"),
        locked_after=Decimal("0"),
        realized_after=Decimal("0"),
        occurred_at=NOW,
        fill_id=fill["id"],
    )
    closed = repo.upsert_position(
        client,
        account_id=aid,
        user_id=USER,
        symbol=SYMBOL,
        side="LONG",
        size=Decimal("0"),
        entry_price=Decimal("30000.1234567890"),
        opened_at=NOW,
        closed_at=NOW + timedelta(minutes=5),
    )
    trade = repo.insert_trade(
        client,
        account_id=aid,
        user_id=USER,
        symbol=SYMBOL,
        side="LONG",
        quantity=Decimal("2"),
        entry_price=Decimal("30000.1234567890"),
        exit_price=Decimal("30100.0000000000"),
        realized_pnl=Decimal("199.753086422"),
        fee_minor=13,
        opened_at=NOW,
        closed_at=NOW + timedelta(minutes=5),
    )
    snapshot = repo.insert_equity_snapshot(
        client,
        user_id=USER,
        total_equity=Decimal("100199.753086422"),
        available_balance=Decimal("100199.753086422"),
        locked_balance=Decimal("0"),
        position_market_value=Decimal("0"),
        cause="FILL",
        taken_at=NOW + timedelta(minutes=5),
    )

    # Every row belongs to the default account, and says so the same way its account does.
    for row in (order, fill, position, balance_event, closed, trade, snapshot):
        assert row["session_id"] is None
        assert row["user_id"] == USER

    # And every read finds them, scoped by the account rather than by a session.
    assert [o["id"] for o in repo.get_orders(client, USER, account_id=aid)] == [order["id"]]
    assert repo.get_positions(client, USER, account_id=aid) == []
    reopened = repo.get_positions(client, USER, account_id=aid, include_closed=True)
    assert [p["id"] for p in reopened] == [closed["id"]]
    assert [t["id"] for t in repo.get_trades(client, USER, account_id=aid)] == [trade["id"]]
    assert [s["id"] for s in repo.get_equity_snapshots(client, USER)] == [snapshot["id"]]

    # Exact decimal strings survived the round trip; nothing was reformatted or rounded.
    assert fill["price"] == "30000.1234567890"
    assert trade["realized_pnl"] == "199.753086422"
    assert fill["fee_minor"] == 13


def test_a_duplicate_default_account_idempotency_key_is_a_conflict() -> None:
    """Requirement 16.8 / P-22 for the default account, enforced by ``uq_paper_order_idem_default``.

    Without that companion index this test would insert a second order and pass, which is why the
    double enforces it and why ``test_the_double_enforces_uq_paper_order_idem_default`` asserts the
    double does.
    """
    client = _client()
    account = _default_account(client)

    def _place() -> Dict[str, Any]:
        return repo.insert_order(
            client,
            account_id=account["id"],
            user_id=USER,
            symbol=SYMBOL,
            side="buy",
            order_type="market",
            quantity=Decimal("1"),
            fingerprint="fp",
            idempotency_key="idem-1",
        )

    first = _place()
    with pytest.raises(repo.PaperConcurrencyConflict) as caught:
        _place()

    assert "account" in str(caught.value)
    assert len(client.orders) == 1
    assert client.orders[0]["id"] == first["id"]


def test_a_default_account_key_and_a_session_key_do_not_collide() -> None:
    """One key, two books. Neither index arbitrates the other's rows."""
    client = _client()
    default_account = _default_account(client)
    session_account = repo.get_or_create_account(
        client, USER, session_id=SESSION, initial_capital=CAPITAL
    )

    for account_id, session_id in (
        (default_account["id"], None),
        (session_account["id"], SESSION),
    ):
        repo.insert_order(
            client,
            account_id=account_id,
            user_id=USER,
            symbol=SYMBOL,
            side="buy",
            order_type="market",
            quantity=Decimal("1"),
            fingerprint="fp",
            idempotency_key="shared-key",
            session_id=session_id,
        )

    assert len(client.orders) == 2


def test_probe_idempotency_key_finds_the_default_account_order_by_account() -> None:
    """The probe reads by ``account_id`` and ``session_id IS NULL`` - the companion index's key."""
    client = _client()
    account = _default_account(client)
    placed = repo.insert_order(
        client,
        account_id=account["id"],
        user_id=USER,
        symbol=SYMBOL,
        side="buy",
        order_type="market",
        quantity=Decimal("1"),
        fingerprint="fp",
        idempotency_key="idem-1",
    )

    found = repo.probe_idempotency_key(
        client, user_id=USER, account_id=account["id"], idempotency_key="idem-1"
    )
    assert found is not None
    assert found["id"] == placed["id"]

    read = client.statements_on(repo.ORDERS_TABLE, "select")[-1]
    assert ("is", "session_id", "null") in read.filters, (
        "the default-account probe must locate the row by IS NULL; .eq(session_id, None) would "
        "render as session_id=eq.None and match nothing, answering 'no order' for a request that "
        "has one and placing a second order (Requirement 16.8)"
    )
    assert read.filter_value("account_id") == account["id"]
    assert read.filter_value("user_id") == USER


def test_probe_idempotency_key_answers_none_for_an_unused_default_key() -> None:
    """``None`` is an answer here too, and it means the read completed and matched nothing."""
    client = _client()
    account = _default_account(client)
    assert (
        repo.probe_idempotency_key(
            client, user_id=USER, account_id=account["id"], idempotency_key="never-used"
        )
        is None
    )


def test_probe_idempotency_key_refuses_without_a_session_or_an_account() -> None:
    """A probe scoped only by ``user_id`` would answer with another currency's order."""
    client = _client()
    with pytest.raises(ValueError) as caught:
        repo.probe_idempotency_key(client, user_id=USER, idempotency_key="k")
    assert "account_id" in str(caught.value)


def test_a_default_account_probe_does_not_see_a_session_order() -> None:
    """The two scopes are separate: Requirement 17.6's isolation, read from the other side."""
    client = _client()
    session_account = repo.get_or_create_account(
        client, USER, session_id=SESSION, initial_capital=CAPITAL
    )
    default_account = _default_account(client)
    repo.insert_order(
        client,
        account_id=session_account["id"],
        user_id=USER,
        symbol=SYMBOL,
        side="buy",
        order_type="market",
        quantity=Decimal("1"),
        fingerprint="fp",
        idempotency_key="shared-key",
        session_id=SESSION,
    )

    assert (
        repo.probe_idempotency_key(
            client,
            user_id=USER,
            account_id=default_account["id"],
            idempotency_key="shared-key",
        )
        is None
    )


def test_a_repeated_default_account_fill_event_is_still_a_duplicate() -> None:
    """``uq_paper_fill_event`` keys on ``order_id``, so it is unaffected by a NULL session."""
    client = _client()
    account = _default_account(client)
    order = repo.insert_order(
        client,
        account_id=account["id"],
        user_id=USER,
        symbol=SYMBOL,
        side="buy",
        order_type="market",
        quantity=Decimal("1"),
        fingerprint="fp",
    )

    def _apply() -> Dict[str, Any]:
        return repo.insert_fill(
            client,
            order_id=order["id"],
            user_id=USER,
            fill_event_id="event-1",
            quantity=Decimal("1"),
            price=Decimal("100"),
            fee_minor=1,
            slippage_minor=0,
            filled_at=NOW,
        )

    _apply()
    with pytest.raises(repo.PaperDuplicateFill):
        _apply()
    assert len(client.fills) == 1


def test_one_open_default_account_position_per_symbol() -> None:
    """``uq_paper_position_open`` keys on ``account_id``, so it holds for the default account too."""
    client = _client()
    account = _default_account(client)
    first = repo.upsert_position(
        client,
        account_id=account["id"],
        user_id=USER,
        symbol=SYMBOL,
        side="LONG",
        size=Decimal("1"),
        entry_price=Decimal("100"),
        opened_at=NOW,
    )
    second = repo.upsert_position(
        client,
        account_id=account["id"],
        user_id=USER,
        symbol=SYMBOL,
        side="LONG",
        size=Decimal("3"),
        entry_price=Decimal("110"),
        opened_at=NOW,
    )
    assert second["id"] == first["id"], "the second write must have updated the open row"
    assert len(client.positions) == 1
    assert second["size"] == "3"


def test_the_default_account_read_of_equity_and_metrics_uses_is_null() -> None:
    """A default-account series and metrics row are located by ``session_id IS NULL``."""
    client = _client()
    _default_account(client)
    snapshot = repo.insert_equity_snapshot(
        client,
        user_id=USER,
        total_equity=CAPITAL,
        available_balance=CAPITAL,
        locked_balance=Decimal("0"),
        position_market_value=Decimal("0"),
        cause="SESSION_START",
        taken_at=NOW,
    )

    assert [s["id"] for s in repo.get_equity_snapshots(client, USER)] == [snapshot["id"]]
    read = client.statements_on(repo.EQUITY_SNAPSHOTS_TABLE, "select")[-1]
    assert ("is", "session_id", "null") in read.filters
    assert read.filter_value("user_id") == USER

    assert repo.get_metrics(client, USER) is None
    metrics_read = client.statements_on(repo.METRICS_TABLE, "select")[-1]
    assert ("is", "session_id", "null") in metrics_read.filters
    assert metrics_read.filter_value("user_id") == USER


def test_a_session_series_is_not_returned_by_the_default_account_read() -> None:
    """The predicate is on the column, so neither scope leaks into the other."""
    client = _client()
    _default_account(client)
    repo.insert_equity_snapshot(
        client,
        session_id=SESSION,
        user_id=USER,
        total_equity=CAPITAL,
        available_balance=CAPITAL,
        locked_balance=Decimal("0"),
        position_market_value=Decimal("0"),
        cause="SESSION_START",
        taken_at=NOW,
    )
    assert repo.get_equity_snapshots(client, USER) == []
    assert len(repo.get_equity_snapshots(client, USER, session_id=SESSION)) == 1


def test_another_users_default_account_children_are_never_fetched() -> None:
    """Relaxing ``session_id`` relaxed nothing about the tenant (Requirements 21.2, 21.5)."""
    client = _client()
    mine = _default_account(client)
    theirs = repo.get_or_create_account(client, OTHER_USER, initial_capital=CAPITAL)
    repo.insert_order(
        client,
        account_id=theirs["id"],
        user_id=OTHER_USER,
        symbol=SYMBOL,
        side="buy",
        order_type="market",
        quantity=Decimal("1"),
        fingerprint="fp",
        idempotency_key="theirs",
    )

    assert repo.get_orders(client, USER, account_id=mine["id"]) == []
    assert repo.get_orders(client, USER) == []
    for statement in client.statements_on(repo.ORDERS_TABLE, "select"):
        assert "user_id" in statement.filtered_columns(), (
            "a default-account read issued a statement without the tenant predicate"
        )
    # Their key is invisible to my probe even with their account id, because user_id is a predicate.
    assert (
        repo.probe_idempotency_key(
            client, user_id=USER, account_id=theirs["id"], idempotency_key="theirs"
        )
        is None
    )


def test_an_unapplied_013_refuses_503_naming_that_file_and_not_009() -> None:
    """009 applied, 013 not: the refusal must name the file that is actually missing.

    ``PAPER_PERSISTENCE_UNAVAILABLE`` means "the migration is not applied", and in this database
    that migration is 013 - 009 is applied, the relation exists and the statement reached it.
    Naming 009 would send an operator to a file that changes nothing.
    """
    client = _client(session_id_not_null=True)
    account = _default_account(client)

    with pytest.raises(PaperError) as caught:
        repo.insert_order(
            client,
            account_id=account["id"],
            user_id=USER,
            symbol=SYMBOL,
            side="buy",
            order_type="market",
            quantity=Decimal("1"),
            fingerprint="fp",
        )

    error = caught.value
    assert error.code == PAPER_PERSISTENCE_UNAVAILABLE
    assert error.http_status == 503
    assert error.details["migration"] == repo.PAPER_DEFAULT_ACCOUNT_MIGRATION_FILE
    assert error.details["migration"] != repo.PAPER_TRADING_MIGRATION_FILE
    assert error.details.get("scope") == "default_account"
    assert "backend_app" not in error.details["migration"]
    assert client.orders == [], "the refused insert must have written nothing"


def test_an_unapplied_013_does_not_refuse_the_session_scoped_path() -> None:
    """The verdict is not cached as "no persistence": session paper trading still works here."""
    client = _client(session_id_not_null=True)
    account = repo.get_or_create_account(
        client, USER, session_id=SESSION, initial_capital=CAPITAL
    )

    order = repo.insert_order(
        client,
        session_id=SESSION,
        account_id=account["id"],
        user_id=USER,
        symbol=SYMBOL,
        side="buy",
        order_type="market",
        quantity=Decimal("1"),
        fingerprint="fp",
    )
    assert order["session_id"] == SESSION

    # And a subsequent default-account write still refuses, rather than the first refusal having
    # poisoned the probe for every path.
    default_account = _default_account(client)
    with pytest.raises(PaperError):
        repo.insert_order(
            client,
            account_id=default_account["id"],
            user_id=USER,
            symbol=SYMBOL,
            side="buy",
            order_type="market",
            quantity=Decimal("1"),
            fingerprint="fp",
        )
    assert repo.paper_persistence_supported(client) is True


def test_a_not_null_violation_on_another_column_is_not_relabelled_as_a_migration() -> None:
    """The classification is narrow: it must name ``session_id`` and a ``paper_`` relation."""
    assert repo.is_unapplied_default_account_migration_error(
        FakeNotNullSessionId(repo.ORDERS_TABLE)
    )
    assert not repo.is_unapplied_default_account_migration_error(
        Exception('null value in column "fingerprint" of relation "public.paper_orders" (23502)')
    )
    assert not repo.is_unapplied_default_account_migration_error(
        Exception('null value in column "session_id" of relation "public.signals" (23502)')
    )
    assert not repo.is_unapplied_default_account_migration_error(
        FakeUniqueViolation("uq_paper_order_idem", repo.ORDERS_TABLE)
    )


# ══════════════════════════════════════════════════════════════════════════
# THE PROJECTIONS AND THE MANIFEST
# ══════════════════════════════════════════════════════════════════════════


def _projection_columns(projection: str) -> Set[str]:
    return {token.strip() for token in projection.split(",") if token.strip()}


@pytest.mark.parametrize(
    "table,projection",
    [
        (repo.ACCOUNTS_TABLE, repo.ACCOUNT_SELECT),
        (repo.POSITIONS_TABLE, repo.POSITION_SELECT),
        (repo.ORDERS_TABLE, repo.ORDER_SELECT),
        (repo.TRADES_TABLE, repo.TRADE_SELECT),
        (repo.EQUITY_SNAPSHOTS_TABLE, repo.EQUITY_SNAPSHOT_SELECT),
        (repo.METRICS_TABLE, repo.METRICS_SELECT),
        (repo.ACCOUNTS_TABLE, repo.PROBE_SELECT),
    ],
)
def test_every_projection_is_inside_the_column_contract(table: str, projection: str) -> None:
    """The local half of the schema contract, so a mistyped column fails here in a second.

    ``tests/test_marketplace_paper_schema_contract.py`` holds the same projections against the
    migration-defined schema; this asserts they are inside the manifest entry that test binds
    them to, without parsing every migration file.
    """
    declared = PAPER_CONTRACT["paper_repository"]["tables"][table]  # type: ignore[index]
    missing = _projection_columns(projection) - set(declared)
    assert not missing, f"{table} projection names undeclared columns: {sorted(missing)}"


def test_no_projection_is_a_star() -> None:
    """``select("*")`` is what makes a column contract uncheckable."""
    for projection in (
        repo.ACCOUNT_SELECT,
        repo.POSITION_SELECT,
        repo.ORDER_SELECT,
        repo.TRADE_SELECT,
        repo.EQUITY_SNAPSHOT_SELECT,
        repo.METRICS_SELECT,
        repo.PROBE_SELECT,
    ):
        assert "*" not in projection


def test_every_insert_payload_column_is_declared_in_the_manifest() -> None:
    """The write half: a column written but undeclared is the 42703 the contract exists to catch."""
    client, account = _seeded()
    order = _order(client, account["id"], idempotency_key="key-1", signal_id=None)
    repo.insert_fill(
        client,
        order_id=order["id"],
        session_id=SESSION,
        user_id=USER,
        fill_event_id="evt-1",
        quantity=Decimal("1"),
        price=Decimal("100"),
        fee_minor=1,
        slippage_minor=0,
        filled_at=NOW,
    )
    _position(client, account["id"])
    repo.insert_balance_event(
        client,
        session_id=SESSION,
        account_id=account["id"],
        user_id=USER,
        cause="FILL",
        available_delta=Decimal("0"),
        locked_delta=Decimal("0"),
        realized_delta=Decimal("0"),
        available_after=Decimal("0"),
        locked_after=Decimal("0"),
        realized_after=Decimal("0"),
        occurred_at=NOW,
    )
    repo.insert_trade(
        client,
        session_id=SESSION,
        account_id=account["id"],
        user_id=USER,
        symbol=SYMBOL,
        side="LONG",
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        exit_price=Decimal("101"),
        realized_pnl=Decimal("1"),
        fee_minor=0,
        opened_at=NOW,
        closed_at=NOW,
    )
    repo.insert_equity_snapshot(
        client,
        session_id=SESSION,
        user_id=USER,
        total_equity=CAPITAL,
        available_balance=CAPITAL,
        locked_balance=Decimal("0"),
        position_market_value=Decimal("0"),
        cause="FILL",
        taken_at=NOW,
    )
    # Task 27.4's ``paper_metrics`` write. Included here because this test is the guard against the
    # ``42703`` a column written but not declared would produce, and a new write path that skipped
    # it would be exactly the path that discovered the missing column in production. Every figure is
    # passed, so no column is covered only by its default.
    repo.insert_metrics(
        client,
        session_id=SESSION,
        user_id=USER,
        computed_at=NOW,
        total_return_pct=Decimal("1.5"),
        realized_pnl=Decimal("1"),
        unrealized_pnl=Decimal("0"),
        max_drawdown_amount=Decimal("0"),
        max_drawdown_fraction=Decimal("0"),
        win_rate=Decimal("1"),
        closed_trade_count=1,
        order_count=1,
        fill_count=1,
    )
    repo.bump_version(
        client,
        user_id=USER,
        account_id=account["id"],
        expected_version=1,
        payload={"available_balance": Decimal("1")},
    )

    tables = PAPER_CONTRACT["paper_repository"]["tables"]  # type: ignore[index]
    problems: List[str] = []
    for statement in client.statements:
        if statement.op not in ("insert", "update"):
            continue
        declared = set(tables[statement.table_name])
        for column in (statement.payload or {}):
            if column not in declared:
                problems.append(f"{statement.table_name}.{column}")
    assert not problems, f"columns written but not declared: {sorted(set(problems))}"
