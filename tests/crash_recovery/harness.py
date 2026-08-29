"""
The shared crash-recovery harness. Requirement 19.4, tasks 12.1 - 12.5.

Requirement 19.4 mandates ONE regression suite with FIVE named failure modes (worker
crash mid-submission, API process restart, WebSocket disconnect, exchange connection
disconnect, database/queue restart), each asserting the SAME three things:

    (a) at most one order is created at the exchange or its test double per Signal,
    (b) the Signal's persisted Order_Lifecycle_State after recovery matches its true
        state at the exchange,
    (c) no duplicate or orphaned signal/order record exists for that Signal's
        Idempotency_Key.

Five copies of those three assertions would be five chances for one of them to drift, so
they live here once, as :func:`assert_crash_recovery_invariants`, and each failure mode's
module supplies only its own interruption.

WHAT IS REAL IN HERE AND WHAT IS A DOUBLE
-----------------------------------------
Real, exercised as shipped: ``signal_service.generate_signal``, ``submit_signal``,
``recover_signal``, ``apply_order_lifecycle_state``, the ``order_lifecycle_state``
transition table and gate, ``idempotency_key_for``, and
``DistributedIdempotencyLayer.execute_with_idempotency`` including its atomic Lua
check-and-set and its owner-token release.

Doubles, because this environment has neither PostgreSQL nor Redis: the PostgREST client
(:class:`FakeDatabase` / :class:`FakePostgrestClient`, which also enforces 005b's
``uq_signals_idempotency_key``), the Redis store (:class:`RecordingRedis`, the same
recording double ``tests/test_task_9_1_signal_idempotency_key.py`` established, extended
with TTL expiry against a controllable clock), and the venue
(:class:`ExchangeTestDouble`).

THE SEAMS EACH FAILURE MODE USES
--------------------------------
Everything durable lives on :class:`CrashRecoveryWorld` - the database, the Redis store
and the exchange - and every PROCESS is a :class:`Worker` obtained from
``world.start_worker(...)``. That split is the whole simulation: a crash or a restart
throws away the worker and keeps the world, so the next worker starts with exactly what a
real restarted process starts with (rows and Redis keys, no memory).

  * **A process dying** (task 12.1's worker crash, task 12.2's API restart).
    ``world.start_worker(crash_at=..., crash_with=...)``. ``crash_at`` is
    :data:`BEFORE_EXCHANGE_CALL` or :data:`AFTER_EXCHANGE_ACCEPTED`; ``crash_with``
    defaults to :class:`WorkerCrash`, which derives from ``BaseException`` ON PURPOSE -
    a process being killed is not an exception the submission path may catch and turn
    into a trading outcome, and ``BaseException`` is what makes every ``except
    Exception`` on that path (including the idempotency layer's own lock release) behave
    the way it does when the process really goes away.
  * **The exchange connection dropping** (task 12.4). ``world.exchange.reachable =
    False`` for a hard outage, ``world.exchange.fail_lookups = n`` for a transient one,
    ``world.exchange.supports_client_order_id_lookup = False`` for Requirement 19.2's
    "does not support such a lookup". Note the deliberate second option for 12.4: an
    interruption raised as an ``Exception`` (e.g. ``crash_with=ExchangeConnectionLost``)
    is a component REPORTING a failure, which the submission path legitimately records
    as ``FAILED``; the same drop raised as a ``BaseException`` is the process losing its
    connection and dying. Those are different facts about the same network event and
    12.4 has to pick which one it is exercising.
  * **The database or queue restarting** (task 12.5). ``world.db.available = False``,
    or ``world.db.fail_on[("signals", "update")] = "57P03 ..."`` to break one specific
    write at one specific moment.
  * **A WebSocket disconnect** (task 12.3). ``world.frames`` is a
    :class:`FrameSink`: set ``connected = False`` to drop published frames, then
    ``snapshot(signal)`` to read what a reconnecting client would be told - which is the
    persisted row, so a disconnect can be shown not to change the record at all.
  * **Time passing.** ``world.clock`` is the one clock. ``world.clock.advance(60)``
    expires the crashed worker's Redis processing lock exactly the way
    ``PROCESSING_TTL`` does, and it is also the clock ``recover_signal``'s
    3-attempts-in-30-seconds bound is measured on, so that boundary can be pinned
    without a real wait.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from backend_app.backend import signal_service as svc
from backend_app.backend.order_lifecycle_state import (
    OrderLifecycleState,
    map_order_state,
    normalise_lifecycle_state,
)
from backend_app.backend.signal_service import RiskVerdict, Signal
from backend_app.core.distributed_idempotency import (
    DistributedIdempotencyLayer,
    idempotency_key_for,
)

# ══════════════════════════════════════════════════════════════════════════
# THE INTERRUPTIONS
# ══════════════════════════════════════════════════════════════════════════

#: The interruption point before the order is offered to the venue at all. Nothing can
#: exist at the exchange afterwards, which is the control case.
BEFORE_EXCHANGE_CALL = "before_exchange_call"

#: The interruption point AFTER the venue accepted the order and BEFORE the caller could
#: record that it did. This is the window task 10.2's header defends by writing
#: ``SUBMITTED`` only after the execution call returns, and the window task 12.1 exists
#: to exercise.
AFTER_EXCHANGE_ACCEPTED = "after_exchange_accepted"


class WorkerCrash(BaseException):
    """The process died mid-submission.

    ``BaseException``, not ``Exception``, and that is the entire fidelity of this double.
    A killed process does not run ``except Exception`` handlers, so modelling a crash as
    an ``Exception`` would let the submission path catch it, classify it as an execution
    failure and write ``FAILED`` - a state the signal never truly reached - and would
    let the idempotency layer release a lock a dead worker would still be holding. The
    test would then be exercising error handling, not crash recovery.
    """


class ApiProcessRestart(WorkerCrash):
    """The API process was restarted mid-request. Same shape, different label (12.2)."""


class ExchangeUnreachable(Exception):
    """The venue could not be reached: an outcome the caller is entitled to observe."""


class ExchangeConnectionLost(Exception):
    """The exchange connection dropped mid-call, reported rather than fatal (12.4)."""


class DuplicateClientOrderId(Exception):
    """The venue refused a second order carrying a client order id it already holds.

    Real venues that honour a client order id do exactly this, and it is the outermost
    of the three defences behind assertion (a): the Redis lock, then
    ``uq_signals_idempotency_key``, then the venue itself.
    """


class DatabaseUnavailable(Exception):
    """The database (or the queue in front of it) is restarting (12.5)."""


# ══════════════════════════════════════════════════════════════════════════
# THE CLOCK
# ══════════════════════════════════════════════════════════════════════════


class FakeClock:
    """One controllable clock for Redis TTLs, recovery budgets and waits.

    ``monotonic`` is what ``recover_signal`` measures its 30-second budget on and what
    :class:`RecordingRedis` expires keys against, so "60 seconds passed while the worker
    was down" and "the third lookup attempt overran the budget" are both a call to
    :meth:`advance` rather than a real sleep.
    """

    def __init__(self, start: float = 1_000.0):
        self.now = float(start)
        self.sleeps: List[float] = []

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += float(seconds)
        return self.now

    async def sleep(self, seconds: float) -> None:
        """The injected wait: recorded, and it moves the clock instead of blocking."""
        self.sleeps.append(float(seconds))
        self.advance(seconds)


# ══════════════════════════════════════════════════════════════════════════
# THE REDIS DOUBLE - survives a worker, like the real one
# ══════════════════════════════════════════════════════════════════════════


class RecordingRedis:
    """The recording Redis double from task 9.1's suite, plus clock-driven expiry.

    Expiry matters here in a way it did not there: after a crash the dead worker's
    ``processing:<token>`` lock is still in the store, and whether the next attempt sees
    a held lock or an expired one is the difference between two completely different code
    paths. So ``ex`` is honoured against :class:`FakeClock` and
    :meth:`advance_past_processing_lock` names the 60-second case explicitly.
    """

    def __init__(self, clock: FakeClock):
        self.clock = clock
        self.store: Dict[str, str] = {}
        self.expires_at: Dict[str, Optional[float]] = {}
        self.set_calls: List[Dict[str, Any]] = []
        #: Flip to False to model the store being unreachable (Requirement 19.5's path,
        #: and one option for task 12.5's queue restart).
        self.available = True

    # ── the store ──
    def _expired(self, key: str) -> bool:
        deadline = self.expires_at.get(key)
        return deadline is not None and self.clock.monotonic() >= deadline

    def _require_available(self) -> None:
        if not self.available:
            raise ConnectionError("Redis is unreachable (crash-recovery harness)")

    async def get(self, key: str):
        self._require_available()
        if self._expired(key):
            self.store.pop(key, None)
            self.expires_at.pop(key, None)
        return self.store.get(key)

    async def set(self, key: str, value: Any, ex: Optional[int] = None, nx: bool = False, **_kw):
        self._require_available()
        self.set_calls.append({"key": key, "ex": ex, "nx": nx})
        if self._expired(key):
            self.store.pop(key, None)
            self.expires_at.pop(key, None)
        if nx and key in self.store:
            return None
        self.store[key] = value
        self.expires_at[key] = None if ex is None else self.clock.monotonic() + float(ex)
        return True

    async def delete(self, *keys: str):
        self._require_available()
        for key in keys:
            self.store.pop(key, None)
            self.expires_at.pop(key, None)
        return True

    async def eval_lua(self, _script: str, keys: List[str], args: List[Any]):
        """The layer's own Lua contract: ``[status, value]``. Reused verbatim from 9.1."""
        self._require_available()
        key = keys[0]
        lock_payload, processing_ttl = args[0], args[1]
        current = await self.get(key)
        if current is not None and not str(current).startswith("processing"):
            return [1, current]
        if current is None:
            await self.set(key, lock_payload, ex=int(processing_ttl), nx=True)
            return [0, lock_payload]
        return [0, False]

    # ── observation and control ──
    def held_locks(self) -> List[str]:
        return [
            key
            for key, value in self.store.items()
            if str(value).startswith("processing") and not self._expired(key)
        ]

    def cached_results(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key, value in self.store.items():
            if self._expired(key) or str(value).startswith("processing"):
                continue
            try:
                out[key] = json.loads(value)
            except (TypeError, ValueError):
                out[key] = value
        return out

    def advance_past_processing_lock(self) -> None:
        """Let the dead worker's in-flight lock expire, as ``PROCESSING_TTL`` does.

        A crashed process never releases its lock; Redis does, 60 seconds later. A test
        that wants to exercise what happens AFTER that (rather than during it) calls this
        rather than waiting, and rather than reaching into the store to delete a key -
        which would model something Redis does not do.
        """
        self.clock.advance(DistributedIdempotencyLayer.PROCESSING_TTL + 1)

    def flush(self) -> None:
        """Everything Redis knew is gone. The durable backstop is on its own now."""
        self.store.clear()
        self.expires_at.clear()


# ══════════════════════════════════════════════════════════════════════════
# THE DATABASE DOUBLE - the durable half of the world
# ══════════════════════════════════════════════════════════════════════════


class _Result:
    def __init__(self, data: Any = None, error: Any = None):
        self.data = data
        self.error = error


class FakeQuery:
    """One chained PostgREST query, recorded in full so a test can assert on it."""

    def __init__(self, db: "FakeDatabase", table: str, verb: str, payload: Any = None):
        self.db = db
        self.table = table
        self.verb = verb
        self.payload = payload
        self.filters: List[Tuple[str, str, Any]] = []
        self.row_limit: Optional[int] = None

    def eq(self, column: str, value: Any) -> "FakeQuery":
        self.filters.append(("eq", column, value))
        return self

    def neq(self, column: str, value: Any) -> "FakeQuery":
        self.filters.append(("neq", column, value))
        return self

    def is_(self, column: str, value: Any) -> "FakeQuery":
        self.filters.append(("is", column, value))
        return self

    def in_(self, column: str, values: Any) -> "FakeQuery":
        self.filters.append(("in", column, list(values)))
        return self

    def limit(self, count: int) -> "FakeQuery":
        self.row_limit = count
        return self

    def order(self, *_args: Any, **_kwargs: Any) -> "FakeQuery":
        return self

    def range(self, *_args: Any, **_kwargs: Any) -> "FakeQuery":
        return self

    def execute(self):
        return self.db.run(self)

    def matches(self, row: Dict[str, Any]) -> bool:
        for kind, column, value in self.filters:
            actual = row.get(column)
            if kind == "eq" and actual != value:
                return False
            if kind == "neq" and actual == value:
                return False
            if kind == "is" and actual is not value and actual != value:
                return False
            if kind == "in" and actual not in value:
                return False
        return True

    def selected(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """The rows this SELECT returns: filtered, then truncated to ``limit``."""
        matched = [dict(row) for row in rows if self.matches(row)]
        return matched if self.row_limit is None else matched[: self.row_limit]


class FakePostgrestClient:
    """One process's connection to :class:`FakeDatabase`.

    A restarted worker gets a NEW one of these and the SAME :class:`FakeDatabase`, which
    is what makes "the rows survived, the process did not" the default rather than
    something each test has to arrange.
    """

    def __init__(self, db: "FakeDatabase", name: str = "client"):
        self.db = db
        self.name = name
        self._table: Optional[str] = None

    def table(self, name: str) -> "FakePostgrestClient":
        self._table = name
        return self

    def select(self, columns: str = "*") -> FakeQuery:
        return FakeQuery(self.db, self._table or "", "select", columns)

    def insert(self, payload: Any) -> FakeQuery:
        return FakeQuery(self.db, self._table or "", "insert", payload)

    def update(self, payload: Any) -> FakeQuery:
        return FakeQuery(self.db, self._table or "", "update", payload)

    def delete(self) -> FakeQuery:
        return FakeQuery(self.db, self._table or "", "delete", None)


class FakeDatabase:
    """The rows, and the two database-level guarantees this suite depends on.

    Enforced here because assertion (c) is meaningless without them:

      * ``uq_signals_idempotency_key`` (migration 005b) - a second ``signals`` row
        carrying a key an existing row already carries fails with ``23505`` naming that
        index, which is the durable backstop behind the Redis lock and the thing
        ``signal_service`` translates into ``DuplicateOrderError``.
      * ``signals.status NOT NULL DEFAULT 'pending'`` - so a row read back when 005b's
        canonical column is absent reconciles through Requirement 16.2's legacy mapping
        exactly as it would in production.

    Every verb against every table is recorded, which is how the append-only claim about
    ``order_lifecycle_transitions`` is checked: the only way to test "no UPDATE and no
    DELETE ever reaches it" is to notice one.
    """

    def __init__(self, *, lifecycle_columns: bool = True, transitions_table: bool = True,
                 signal_events_table: bool = True):
        self.tables: Dict[str, List[Dict[str, Any]]] = {}
        self.calls: List[Dict[str, Any]] = []
        #: 005b applied?
        self.lifecycle_columns = lifecycle_columns
        #: 005b section 2 applied?
        self.transitions_table = transitions_table
        #: 002's signal_events present?
        self.signal_events_table = signal_events_table
        #: Flip to False for a database/queue restart (task 12.5).
        self.available = True
        #: {(table, verb): "error text"} - raised as an exception.
        self.fail_on: Dict[Tuple[str, str], str] = {}
        #: {(table, verb): "error text"} - returned as ``result.error``.
        self.error_on: Dict[Tuple[str, str], str] = {}

    # ── connections ──
    def client(self, name: str = "client") -> FakePostgrestClient:
        return FakePostgrestClient(self, name)

    # ── reading ──
    def rows(self, table: str) -> List[Dict[str, Any]]:
        return [dict(row) for row in self.tables.get(table, [])]

    def verbs_on(self, table: str) -> set:
        return {call["verb"] for call in self.calls if call["table"] == table}

    def payloads(self, table: str, verb: str) -> List[Any]:
        return [
            call["payload"]
            for call in self.calls
            if call["table"] == table and call["verb"] == verb
        ]

    # ── the engine ──
    def run(self, query: FakeQuery):
        self.calls.append(
            {
                "table": query.table,
                "verb": query.verb,
                "payload": query.payload,
                "filters": list(query.filters),
            }
        )
        if not self.available:
            raise DatabaseUnavailable(
                "57P03 the database system is starting up (crash-recovery harness)"
            )
        signature = (query.table, query.verb)
        if signature in self.fail_on:
            raise DatabaseUnavailable(self.fail_on[signature])
        if signature in self.error_on:
            return _Result(error=self.error_on[signature])

        if query.table == "signals":
            return self._signals(query)
        if query.table == svc.ORDER_LIFECYCLE_TRANSITIONS_TABLE:
            if not self.transitions_table:
                raise Exception(
                    "PGRST205 Could not find the table "
                    "'public.order_lifecycle_transitions' in the schema cache"
                )
            return self._generic(query)
        if query.table == svc.SIGNAL_EVENTS_TABLE:
            if not self.signal_events_table:
                raise Exception(
                    "PGRST205 Could not find the table 'public.signal_events' in the "
                    "schema cache"
                )
            return self._generic(query)
        return self._generic(query)

    def _signals(self, query: FakeQuery):
        rows = self.tables.setdefault("signals", [])

        if query.verb == "select":
            columns = str(query.payload)
            if not self.lifecycle_columns and (
                "order_lifecycle_state" in columns or "idempotency_key" in columns
            ):
                raise Exception(
                    "42703 column signals.order_lifecycle_state does not exist"
                )
            return _Result(data=query.selected(rows))

        if query.verb == "insert":
            payload = dict(query.payload)
            payload.setdefault("status", "pending")  # NOT NULL DEFAULT 'pending'
            key = payload.get("idempotency_key")
            if key is not None and any(r.get("idempotency_key") == key for r in rows):
                raise Exception(
                    '23505 duplicate key value violates unique constraint '
                    '"uq_signals_idempotency_key"'
                )
            rows.append(payload)
            return _Result(data=[dict(payload)])

        if query.verb == "update":
            touched = []
            for row in rows:
                if query.matches(row):
                    row.update(query.payload)
                    touched.append(dict(row))
            return _Result(data=touched)

        return self._generic(query)

    def _generic(self, query: FakeQuery):
        rows = self.tables.setdefault(query.table, [])
        if query.verb == "select":
            return _Result(data=query.selected(rows))
        if query.verb == "insert":
            payload = dict(query.payload)
            payload.setdefault("id", f"{query.table}-{len(rows) + 1}")
            rows.append(payload)
            return _Result(data=[dict(payload)])
        if query.verb == "update":
            touched = []
            for row in rows:
                if query.matches(row):
                    row.update(query.payload)
                    touched.append(dict(row))
            return _Result(data=touched)
        if query.verb == "delete":
            kept = [r for r in rows if not query.matches(r)]
            removed = len(rows) - len(kept)
            self.tables[query.table] = kept
            return _Result(data=[{"deleted": removed}])
        return _Result(data=[])


# ══════════════════════════════════════════════════════════════════════════
# THE EXCHANGE TEST DOUBLE - Requirement 19.4's "exchange or its test double"
# ══════════════════════════════════════════════════════════════════════════


class ExchangeTestDouble:
    """A venue that keys orders by client order id, and can be asked about them.

    Two capabilities, both named by Requirement 19.2:

      * it REFUSES a second order carrying a client order id it already holds, which is
        the last line of defence behind assertion (a);
      * it supports LOOKUP BY CLIENT ORDER IDENTIFIER, which is what the recovery sweep
        queries - and can be told not to (``supports_client_order_id_lookup = False``),
        because the requirement explicitly handles a venue that cannot answer.

    :meth:`true_state` is what assertion (b) compares the persisted record against: the
    double's own order status, mapped through ``order_lifecycle_state``'s
    ``ORDER_STATE_MAP``. The mapping is the shipped one, so "matches its true state at
    the exchange" is measured with the platform's own reconciliation rather than with a
    second table written for the test.
    """

    def __init__(
        self,
        *,
        reachable: bool = True,
        supports_client_order_id_lookup: bool = True,
        fail_lookups: int = 0,
    ):
        #: {client_order_id: order}
        self.orders: Dict[str, Dict[str, Any]] = {}
        #: Every placement ATTEMPT, accepted or refused, in order.
        self.placements: List[Dict[str, Any]] = []
        #: Every lookup, in order.
        self.lookups: List[str] = []
        self.reachable = reachable
        self.supports_client_order_id_lookup = supports_client_order_id_lookup
        #: Fail this many leading lookups, then answer. A transient outage.
        self.fail_lookups = fail_lookups
        self._order_seq = 0

    # ── placing ──
    async def place_order(
        self,
        *,
        client_order_id: str,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        quantity: Optional[float] = None,
        status: str = "open",
        filled: float = 0.0,
    ) -> Dict[str, Any]:
        self.placements.append(
            {
                "client_order_id": client_order_id,
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "accepted": False,
            }
        )
        if not self.reachable:
            raise ExchangeUnreachable(
                f"the exchange is unreachable; {client_order_id} was not placed"
            )
        if client_order_id in self.orders:
            raise DuplicateClientOrderId(
                f"an order with client order id {client_order_id} already exists at the "
                f"exchange"
            )
        self._order_seq += 1
        order = {
            "order_id": f"X-{self._order_seq}",
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "filled": filled,
            "status": status,
        }
        self.orders[client_order_id] = order
        self.placements[-1]["accepted"] = True
        self.placements[-1]["order_id"] = order["order_id"]
        return dict(order)

    # ── looking up (Requirement 19.2) ──
    async def fetch_order_by_client_order_id(
        self, client_order_id: str
    ) -> Optional[Dict[str, Any]]:
        self.lookups.append(client_order_id)
        if self.fail_lookups > 0:
            self.fail_lookups -= 1
            raise ExchangeUnreachable(
                f"the exchange could not be reached to look up {client_order_id}"
            )
        if not self.reachable:
            raise ExchangeUnreachable(
                f"the exchange is unreachable; {client_order_id} could not be looked up"
            )
        order = self.orders.get(client_order_id)
        return dict(order) if order is not None else None

    # ── what the venue then does with the order ──
    def fill(self, client_order_id: str, filled: Optional[float] = None) -> Dict[str, Any]:
        order = self.orders[client_order_id]
        order["filled"] = order["quantity"] if filled is None else filled
        quantity = order.get("quantity")
        if quantity is not None and order["filled"] is not None and order["filled"] < quantity:
            order["status"] = "partial"
        else:
            order["status"] = "filled"
        return dict(order)

    def cancel(self, client_order_id: str) -> Dict[str, Any]:
        order = self.orders[client_order_id]
        order["status"] = "cancelled"
        return dict(order)

    # ── observation ──
    def orders_for(self, client_order_id: str) -> List[Dict[str, Any]]:
        order = self.orders.get(client_order_id)
        return [dict(order)] if order is not None else []

    def accepted_placements_for(self, client_order_id: str) -> List[Dict[str, Any]]:
        return [
            dict(p)
            for p in self.placements
            if p["client_order_id"] == client_order_id and p["accepted"]
        ]

    def true_state(self, client_order_id: str) -> Optional[OrderLifecycleState]:
        """The venue's truth about this key, in the canonical vocabulary."""
        order = self.orders.get(client_order_id)
        if order is None:
            return None
        return map_order_state(order["status"])


# ══════════════════════════════════════════════════════════════════════════
# THE WEBSOCKET SEAM (task 12.3's, provided here so it is not invented twice)
# ══════════════════════════════════════════════════════════════════════════


class FrameSink:
    """Where a realtime frame would go, and what a reconnecting client re-reads.

    Task 14.2 owns real publishing; nothing publishes yet. This exists so task 12.3 can
    express a WebSocket disconnect as what it actually is - frames not delivered - and
    then assert the thing Requirement 19.3 cares about: the persisted record is
    unchanged by the disconnect, and :meth:`snapshot` (what a reconnect requests) reads
    the row, never a replayed frame.
    """

    def __init__(self, db: FakeDatabase):
        self.db = db
        self.connected = True
        self.delivered: List[Dict[str, Any]] = []
        self.dropped: List[Dict[str, Any]] = []

    def publish(self, frame: Dict[str, Any]) -> bool:
        if not self.connected:
            self.dropped.append(dict(frame))
            return False
        self.delivered.append(dict(frame))
        return True

    def snapshot(self, signal: Signal) -> Optional[Dict[str, Any]]:
        for row in self.db.rows("signals"):
            if row.get("id") == signal.id:
                return row
        return None


# ══════════════════════════════════════════════════════════════════════════
# THE COMPONENTS ONE WORKER HOLDS
# ══════════════════════════════════════════════════════════════════════════


class RecordingRiskEngine:
    """Approves by default, through the explicit per-signal seam.

    Requirement 11.2's routing is task 10.2's subject and is already covered by
    ``tests/test_task_10_2_submit_signal.py``; here the risk component only has to be
    real enough that the submission reaches the exchange, and observable enough that a
    test can prove it was consulted once per attempt rather than skipped.
    """

    def __init__(self, verdict: Optional[RiskVerdict] = None):
        self.verdict = verdict or RiskVerdict(approved=True, reason="within limits")
        self.calls: List[str] = []

    async def validate_signal(self, signal: Signal) -> RiskVerdict:
        self.calls.append(signal.id)
        return self.verdict


class ScriptedExecutionEngine:
    """Places one order at the exchange double, and can be interrupted at two points.

    The client order id it sends is ``signal.idempotency_key`` - the derived key, not a
    per-attempt value - because that identity is the entire mechanism Requirement 19.1
    and 19.2 rest on. Every failure mode's simulation therefore works on the same handle
    the recovery sweep later queries by.
    """

    def __init__(
        self,
        exchange: ExchangeTestDouble,
        *,
        crash_at: Optional[str] = None,
        crash_with: type = WorkerCrash,
        order_status: str = "open",
        filled: float = 0.0,
        on_call: Optional[Any] = None,
    ):
        self.exchange = exchange
        self.crash_at = crash_at
        self.crash_with = crash_with
        self.order_status = order_status
        self.filled = filled
        #: An optional hook run just before the exchange call, for a failure mode that
        #: has to break something else at that exact moment (task 12.5's database).
        self.on_call = on_call
        self.calls: List[str] = []

    async def submit_order(self, signal: Signal) -> Dict[str, Any]:
        self.calls.append(signal.id)
        if self.on_call is not None:
            self.on_call(signal)
        if self.crash_at == BEFORE_EXCHANGE_CALL:
            raise self.crash_with(
                f"the process died before offering signal {signal.id} to the exchange"
            )
        order = await self.exchange.place_order(
            client_order_id=signal.idempotency_key,
            symbol=signal.symbol,
            side=signal.side or signal.decision,
            quantity=signal.quantity,
            status=self.order_status,
            filled=self.filled,
        )
        if self.crash_at == AFTER_EXCHANGE_ACCEPTED:
            raise self.crash_with(
                f"the process died after the exchange accepted order "
                f"{order['order_id']} for signal {signal.id}, before its state could be "
                f"recorded"
            )
        return {
            "success": True,
            "status": "submitted",
            "order_id": order["order_id"],
            "order_state": order["status"],
            "filled": order["filled"],
            "quantity": order["quantity"],
            "message": "accepted by the exchange test double",
        }


@dataclass
class Worker:
    """One process incarnation. Discarded by a crash; the world it acted on is not."""

    name: str
    world: "CrashRecoveryWorld"
    sb: FakePostgrestClient
    layer: DistributedIdempotencyLayer
    risk: RecordingRiskEngine
    execution: ScriptedExecutionEngine

    async def generate(self, **overrides: Any) -> Signal:
        """Persist a ``GENERATED`` signal through the real ``generate_signal``."""
        return await svc.generate_signal(
            self.world.deployment_row(),
            self.world.action_output(**overrides),
            sb=self.sb,
        )

    async def submit(self, signal: Signal) -> OrderLifecycleState:
        """Submit through the real ``submit_signal``, under the real idempotency layer."""
        return await svc.submit_signal(
            signal,
            risk_engine=self.risk,
            execution_engine=self.execution,
            sb=self.sb,
            idempotency_layer=self.layer,
        )

    async def recover(self, signal: Signal, **kwargs: Any) -> svc.RecoveryOutcome:
        """Run the Requirement 19.2 sweep, on the harness clock."""
        kwargs.setdefault("sleep", self.world.clock.sleep)
        kwargs.setdefault("monotonic", self.world.clock.monotonic)
        return await svc.recover_signal(
            signal, exchange=self.world.exchange, sb=self.sb, **kwargs
        )

    async def reload(self, signal: Signal) -> Signal:
        """The signal as THIS process must see it after a restart: at its persisted state.

        Requirement 19.3's "THE Signal SHALL resume from its last persisted
        Order_Lifecycle_State without re-deriving it from an earlier, superseded state",
        as a call. A restarted worker has no memory, so handing a resumed worker the
        ``Signal`` object the crashed one held - still claiming ``GENERATED`` - would be
        the test simulating a bug rather than a restart, and would append a second
        ``GENERATED -> PENDING`` transition to a history that already has one.
        """
        persisted = await svc.load_current_order_lifecycle_state(self.sb, signal)
        return signal.with_order_lifecycle_state(persisted)


# ══════════════════════════════════════════════════════════════════════════
# THE WORLD
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class CrashRecoveryWorld:
    """Everything that survives a process: the database, Redis, the exchange, the clock.

    Built by the ``crash_world`` fixture (``tests/crash_recovery/conftest.py``), which
    also points the idempotency layer's module-level Redis handle at :attr:`redis` and
    clears ``signal_service``'s per-process migration verdicts.
    """

    clock: FakeClock
    db: FakeDatabase
    redis: RecordingRedis
    exchange: ExchangeTestDouble
    frames: FrameSink
    user_id: str = "user-aaaa"
    deployment_id: str = "dep-1111"
    strategy_id: str = "strat-bbbb"
    workers: List[Worker] = field(default_factory=list)

    # ── the fixture inputs generate_signal reads ──
    def deployment_row(self, **overrides: Any) -> Dict[str, Any]:
        row = {
            "id": self.deployment_id,
            "user_id": self.user_id,
            "strategy_id": self.strategy_id,
            "version": "v3",
            "version_id": "ver-cccc",
            "exchange_account_id": "acct-dddd",
            "exchange_id": "kraken",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "mode": "paper",
            "worker_id": "worker-7",
        }
        row.update(overrides)
        return row

    def action_output(self, **overrides: Any) -> Dict[str, Any]:
        output = {
            "decision": "BUY",
            "quantity": 0.25,
            "price": 61234.5,
            "source_node_ids": ["action-1"],
            "closure_ready": True,
            "node_closure": {"rsi-1": 28.4},
            "risk_validation": {"passed": True, "reason": "within limits"},
        }
        output.update(overrides)
        return output

    # ── processes ──
    def start_worker(
        self,
        *,
        name: Optional[str] = None,
        crash_at: Optional[str] = None,
        crash_with: type = WorkerCrash,
        order_status: str = "open",
        filled: float = 0.0,
        risk: Optional[RecordingRiskEngine] = None,
        on_call: Optional[Any] = None,
    ) -> Worker:
        """A fresh process: new client, new layer, new engines, same world.

        The idempotency layer is the REAL ``DistributedIdempotencyLayer``, not a double.
        A new instance per worker is exactly what a restarted process has, and because
        the layer holds no state of its own (everything is in Redis) the new instance
        sees precisely what the crashed one left behind.
        """
        worker = Worker(
            name=name or f"worker-{len(self.workers) + 1}",
            world=self,
            sb=self.db.client(name or f"worker-{len(self.workers) + 1}"),
            layer=DistributedIdempotencyLayer(),
            risk=risk or RecordingRiskEngine(),
            execution=ScriptedExecutionEngine(
                self.exchange,
                crash_at=crash_at,
                crash_with=crash_with,
                order_status=order_status,
                filled=filled,
                on_call=on_call,
            ),
        )
        self.workers.append(worker)
        return worker

    # ── reading the world back ──
    def signal_rows_for_key(self, key: str) -> List[Dict[str, Any]]:
        return [row for row in self.db.rows("signals") if row.get("idempotency_key") == key]

    def signal_row(self, signal: Signal) -> Optional[Dict[str, Any]]:
        for row in self.db.rows("signals"):
            if row.get("id") == signal.id:
                return row
        return None

    def persisted_state(self, signal: Signal) -> Optional[OrderLifecycleState]:
        """The canonical state as the DATABASE holds it. ``None`` when unreconciled."""
        row = self.signal_row(signal)
        if row is None:
            return None
        return normalise_lifecycle_state(row.get("order_lifecycle_state"))

    def transitions(self, signal: Signal) -> List[Dict[str, Any]]:
        return [
            row
            for row in self.db.rows(svc.ORDER_LIFECYCLE_TRANSITIONS_TABLE)
            if row.get("signal_id") == signal.id
        ]

    def reconciliation_markers(self, signal: Signal) -> List[Dict[str, Any]]:
        return [
            row
            for row in self.db.rows(svc.SIGNAL_EVENTS_TABLE)
            if row.get("signal_id") == signal.id
            and row.get("event_type") == svc.MANUAL_RECONCILIATION_EVENT
        ]


# ══════════════════════════════════════════════════════════════════════════
# THE THREE ASSERTIONS (Requirement 19.4)
# ══════════════════════════════════════════════════════════════════════════

#: The states that CLAIM an order exists at the venue. Used by assertion (b): a signal in
#: one of these when the exchange holds nothing for its key is the unrecoverable lie task
#: 10.2's header describes ("a false SUBMITTED loses a trade and lies in the audit log").
STATES_CLAIMING_AN_ORDER = frozenset(
    {
        OrderLifecycleState.SUBMITTED,
        OrderLifecycleState.PARTIALLY_EXECUTED,
        OrderLifecycleState.EXECUTED,
        OrderLifecycleState.CLOSED,
    }
)


def assert_at_most_one_order_per_signal(
    world: CrashRecoveryWorld, signal: Signal, *, expect_order: Optional[bool] = None
) -> List[Dict[str, Any]]:
    """(a) At most one order is created at the exchange test double per Signal.

    Measured on ACCEPTED placements, not on attempts: a refused duplicate is the
    guarantee working, and counting it as an order would make the assertion unfalsifiable
    in the one direction that matters.

    ``expect_order`` tightens "at most one" to "exactly one" (or "none") where the failure
    mode knows which it should be - which is most of them, and leaving it at "at most"
    everywhere would let a test pass because nothing was ever submitted.
    """
    key = idempotency_key_for(signal)
    accepted = world.exchange.accepted_placements_for(key)
    assert len(accepted) <= 1, (
        f"(a) VIOLATED: {len(accepted)} orders were created at the exchange for signal "
        f"{signal.id} under idempotency key {key}: {accepted}"
    )
    orders = world.exchange.orders_for(key)
    assert len(orders) == len(accepted), (
        f"(a) the exchange holds {len(orders)} order(s) for {key} but accepted "
        f"{len(accepted)} placement(s); the double is inconsistent with itself"
    )
    if expect_order is True:
        assert len(accepted) == 1, (
            f"(a) expected exactly one order at the exchange for {key}, found none. "
            f"Placement attempts: {world.exchange.placements}"
        )
    if expect_order is False:
        assert not accepted, (
            f"(a) expected NO order at the exchange for {key}, found {accepted}"
        )
    return orders


def assert_persisted_state_matches_exchange(
    world: CrashRecoveryWorld,
    signal: Signal,
    *,
    expected_state: Optional[OrderLifecycleState] = None,
    unresolvable_if_marked: bool = False,
) -> Optional[OrderLifecycleState]:
    """(b) The Signal's post-recovery Order_Lifecycle_State matches its true state.

    Two cases, and the second one is not a weaker version of the first:

      * The exchange HOLDS an order for this key. The persisted state must equal
        ``exchange.true_state(key)`` - the venue's own status, mapped through the
        platform's own ``ORDER_STATE_MAP``.
      * The exchange holds NOTHING. Then "matches its true state" means the record must
        not CLAIM an order exists: any of ``GENERATED``, ``PENDING``, ``REJECTED``,
        ``FAILED``, ``CANCELLED`` is consistent with a venue that has nothing, and any of
        ``SUBMITTED``, ``PARTIALLY_EXECUTED``, ``EXECUTED``, ``CLOSED`` is not.

    ``expected_state`` pins the exact value where the failure mode knows it, so a test
    cannot pass on a state that merely happens to be consistent.

    ``unresolvable_if_marked`` - THE ONE RELAXATION, AND WHY IT IS NOT A LOOPHOLE
        When the venue cannot be reached at all, NO implementation can make the record
        equal the venue's state: the venue's state is unknown. Requirement 19.2 answers
        that case itself - "IF the exchange-state check exhausts its attempts without a
        definitive answer, THEN mark that Signal's Order_Lifecycle_State as requiring
        manual reconciliation rather than resubmitting the order" - so for that case
        Requirement 19.4's assertion (b) can only mean: the platform must not CLAIM a
        state it could not confirm.

        Passing ``unresolvable_if_marked=True`` therefore demands three things instead of
        equality, and all three are stronger than "skip the check":
          1. a manual-reconciliation marker actually exists for this signal (so the
             divergence is declared, not silent),
          2. the persisted state is BEHIND the venue's or equal to it - never ahead,
             measured with the shipped transition table, so the record can lag reality
             but can never overclaim,
          3. and if the venue holds nothing, the record still may not claim an order.

        It must be opted into per call, so a test can never pass merely because a marker
        happened to be written.
    """
    key = idempotency_key_for(signal)
    persisted = world.persisted_state(signal)
    assert persisted is not None, (
        f"(b) signal {signal.id} has no persisted Order_Lifecycle_State at all; the row "
        f"is {world.signal_row(signal)}"
    )
    true_state = world.exchange.true_state(key)
    markers = world.reconciliation_markers(signal)

    if unresolvable_if_marked and markers:
        assert persisted not in STATES_CLAIMING_AN_ORDER or true_state is not None, (
            f"(b) VIOLATED: signal {signal.id} claims {persisted.value} but the exchange "
            f"holds no order for {key}, and a manual-reconciliation marker does not "
            f"license claiming an order that does not exist"
        )
        if true_state is not None and persisted is not true_state:
            lags = bool(svc.lifecycle_path(persisted, true_state))
            assert lags, (
                f"(b) VIOLATED: signal {signal.id} is persisted at {persisted.value}, "
                f"which is AHEAD of the exchange's {true_state.value} for {key}. A "
                f"signal marked for manual reconciliation may lag the venue - it may "
                f"never overclaim."
            )
    elif true_state is not None:
        assert persisted is true_state, (
            f"(b) VIOLATED: signal {signal.id} is persisted at {persisted.value} but the "
            f"exchange's own record of {key} says {true_state.value}"
        )
    else:
        assert persisted not in STATES_CLAIMING_AN_ORDER, (
            f"(b) VIOLATED: signal {signal.id} is persisted at {persisted.value}, which "
            f"claims an order exists at the exchange, but the exchange holds no order "
            f"for {key}"
        )

    if expected_state is not None:
        assert persisted is expected_state, (
            f"(b) expected signal {signal.id} to be persisted at "
            f"{expected_state.value}, found {persisted.value}"
        )
    return persisted


def assert_no_duplicate_or_orphaned_record(
    world: CrashRecoveryWorld, signal: Signal
) -> None:
    """(c) No duplicate or orphaned signal/order record exists for the Idempotency_Key.

    Four distinct ways that can be false, all checked, because Requirement 19.3 names
    three of them explicitly ("split into two or more signal/order records sharing the
    same Idempotency_Key", "left referencing an order identifier that does not match the
    exchange's own record of that order") and the fourth is what an append-only log is
    for:

      1. Exactly one ``signals`` row carries the key.
      2. The row's ``order_id`` names an order the exchange actually holds under that
         key - or names nothing at all. A reference to an order the venue never issued is
         an orphan.
      3. No order at the exchange has a client order id that no signal row claims.
      4. The transition history is ONE chain: exactly one genesis row (NULL ->
         GENERATED), every later row's ``from_state`` equal to its predecessor's
         ``to_state``, and nothing but SELECT and INSERT has ever reached the log.
    """
    key = idempotency_key_for(signal)

    # 1. one row per key
    rows = world.signal_rows_for_key(key)
    assert len(rows) == 1, (
        f"(c) VIOLATED: {len(rows)} signal rows carry idempotency key {key}: {rows}"
    )
    row = rows[0]
    assert row.get("id") == signal.id, (
        f"(c) idempotency key {key} is carried by signal {row.get('id')}, not by "
        f"{signal.id}"
    )

    # 2. the order reference resolves to a real order at the venue
    orders = world.exchange.orders_for(key)
    order_ids = {o["order_id"] for o in orders}
    persisted_order_id = row.get("order_id")
    if persisted_order_id is not None:
        assert persisted_order_id in order_ids, (
            f"(c) VIOLATED: signal {signal.id} references order "
            f"{persisted_order_id}, which the exchange has no record of under {key} "
            f"(it holds {sorted(order_ids) or 'nothing'})"
        )

    # 3. nothing at the venue is unclaimed
    claimed = {r.get("idempotency_key") for r in world.db.rows("signals")}
    orphans = [
        client_order_id
        for client_order_id in world.exchange.orders
        if client_order_id not in claimed
    ]
    assert not orphans, (
        f"(c) VIOLATED: the exchange holds order(s) under client order id(s) {orphans} "
        f"that no signal row claims"
    )

    # 4. one chain, append-only
    verbs = world.db.verbs_on(svc.ORDER_LIFECYCLE_TRANSITIONS_TABLE)
    assert verbs <= {"select", "insert"}, (
        f"(c) VIOLATED: {sorted(verbs - {'select', 'insert'})} reached the append-only "
        f"transition log"
    )
    transitions = world.transitions(signal)
    genesis = [t for t in transitions if t.get("from_state") is None]
    assert len(genesis) <= 1, (
        f"(c) VIOLATED: signal {signal.id} has {len(genesis)} genesis transition rows, "
        f"so its history is split: {transitions}"
    )
    if genesis:
        assert genesis[0].get("to_state") == OrderLifecycleState.GENERATED.value
        assert transitions[0] is genesis[0] or transitions.index(genesis[0]) == 0, (
            f"(c) the genesis row is not first in signal {signal.id}'s history: "
            f"{transitions}"
        )
    previous: Optional[str] = None
    for index, transition in enumerate(transitions):
        if index == 0 and transition.get("from_state") is None:
            previous = transition.get("to_state")
            continue
        assert transition.get("from_state") == previous, (
            f"(c) VIOLATED: signal {signal.id}'s transition history is not a chain - row "
            f"{index} moves from {transition.get('from_state')} but the previous row "
            f"left it at {previous}. Full history: {transitions}"
        )
        previous = transition.get("to_state")


def assert_crash_recovery_invariants(
    world: CrashRecoveryWorld,
    signal: Signal,
    *,
    expect_order: Optional[bool] = None,
    expected_state: Optional[OrderLifecycleState] = None,
    unresolvable_if_marked: bool = False,
) -> Optional[OrderLifecycleState]:
    """All three of Requirement 19.4's assertions, for one signal, in one call.

    Every failure-mode module ends its recovery scenario here. Requirement 19.4 says the
    regression test "SHALL fail if any of these three assertions does not hold for any
    simulated failure mode", so they are checked together and each failure names which of
    (a), (b), (c) broke.
    """
    assert_at_most_one_order_per_signal(world, signal, expect_order=expect_order)
    persisted = assert_persisted_state_matches_exchange(
        world,
        signal,
        expected_state=expected_state,
        unresolvable_if_marked=unresolvable_if_marked,
    )
    assert_no_duplicate_or_orphaned_record(world, signal)
    return persisted
