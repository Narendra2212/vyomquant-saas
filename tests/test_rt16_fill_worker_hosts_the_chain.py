"""RT-16 — the worker that starts the private fill stream, exactly once, in the right order.

WHAT THIS HOSTS
---------------
Three links that each existed and none of which had a caller::

    1. ExchangeWebSocketListener._listen_for_orders   (watch_orders -> _handle_order_update)
    2. EventListener._on_exchange_event              (enqueue -> _event_processing_loop)
    3. EventRouter.route_event                       (-> the execution record)

RT-16's entry is emphatic that starting any one alone **delivers nothing**:
``_listen_for_orders`` would build events and hand them to a listener whose
``_event_router`` is ``None``, and ``EventListener`` refuses those events by design. So the
order is load-bearing and is asserted here as an order, not as three independent facts.

AND IT MUST BE EXACTLY ONE STREAMER. ``gunicorn`` runs ``--workers ${WORKERS:-2}`` and ECS
can run more than one task, so without a lease every fill gets several writers racing on
one execution row. The lease is Redis-backed and fails closed, so "leadership unverifiable"
and "not the leader" take the same path -- standing down.

NO NETWORK, NO VENUE, NO REDIS, NO DATABASE. Every collaborator here is a recording double.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib

import pytest

from backend_app.workers import exchange_fill_worker as mod
from backend_app.workers.exchange_fill_worker import (ExchangeFillWorker,
                                                      _PerEventRouter)


class _Listener:
    """Stands in for ExchangeWebSocketListener."""

    def __init__(self):
        self.started = 0
        self.stopped = 0
        self.listening: set = set()
        self.calls: list = []
        self.fail_on: set = set()

    async def start(self):
        self.started += 1
        self.calls.append("feeder.start")

    async def stop(self):
        self.stopped += 1
        self.calls.append("feeder.stop")

    async def start_listening(self, tenant_id, exchange_id, symbols=None):
        if (tenant_id, exchange_id) in self.fail_on:
            raise RuntimeError("venue refused")
        self.listening.add((tenant_id, exchange_id))
        self.calls.append(f"listen+{tenant_id}:{exchange_id}")

    async def stop_listening(self, tenant_id, exchange_id):
        self.listening.discard((tenant_id, exchange_id))
        self.calls.append(f"listen-{tenant_id}:{exchange_id}")


class _EventBus:
    """Stands in for EventListener. Records the router it was started with."""

    def __init__(self, shared_calls=None):
        self.router = None
        self.started = 0
        self.stopped = 0
        self.calls = shared_calls if shared_calls is not None else []

    async def start(self, event_router):
        self.router = event_router
        self.started += 1
        self.calls.append("bus.start")

    async def stop(self):
        self.stopped += 1
        self.calls.append("bus.stop")


class _Store:
    def __init__(self, rows=None, raises=False):
        self.rows = rows if rows is not None else []
        self.raises = raises
        self.reads = 0

    async def rows_in(self, statuses):
        self.reads += 1
        if self.raises:
            raise RuntimeError("supabase unreachable")
        return list(self.rows)


def _worker(monkeypatch, *, leader=True, rows=None, raises=False, shared_calls=None):
    """A worker with every collaborator replaced and leadership forced."""
    calls = shared_calls if shared_calls is not None else []
    listener = _Listener()
    listener.calls = calls
    bus = _EventBus(shared_calls=calls)
    store = _Store(rows=rows, raises=raises)

    async def fake_lease(**kw):
        calls.append("lease")
        return leader

    monkeypatch.setattr(mod, "try_acquire_or_renew", fake_lease)

    w = ExchangeFillWorker(
        state_store=store, listener=listener, event_bus=bus, router=object(),
    )
    return w, listener, bus, store, calls


LIVE = {"user_id": "t1", "exchange_id": "binance", "mode": "live"}


# ══════════════════════════════════════════════════════════════════════════════════════
#  1 — THE ORDER IS LOAD-BEARING
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_the_chain_starts_router_then_drain_then_feeder(monkeypatch):
    """Reversed, every fill arriving before the router is refused and logged as an error.

    Safe, because the guard exists -- but it would lose the first fills of every
    deployment for no reason.
    """
    w, listener, bus, _store, calls = _worker(monkeypatch, rows=[LIVE])

    await w.process_iteration()

    assert "bus.start" in calls and "feeder.start" in calls
    assert calls.index("bus.start") < calls.index("feeder.start"), (
        "the feeder was started before the event drain; events published in that window "
        "are refused because EventListener has no router yet"
    )
    # And the drain was handed a router rather than started bare.
    assert bus.router is not None, "EventListener was started with no router"


@pytest.mark.asyncio
async def test_the_chain_is_started_once_across_many_cycles(monkeypatch):
    w, listener, bus, _store, _calls = _worker(monkeypatch, rows=[LIVE])

    for _ in range(4):
        await w.process_iteration()

    assert bus.started == 1, "the event drain was restarted on a later cycle"
    assert listener.started == 1, "the feeder was restarted on a later cycle"


# ══════════════════════════════════════════════════════════════════════════════════════
#  2 — EXACTLY ONE STREAMER
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_a_non_leader_starts_nothing_at_all(monkeypatch):
    w, listener, bus, store, _calls = _worker(monkeypatch, leader=False, rows=[LIVE])

    await w.process_iteration()

    assert bus.started == 0
    assert listener.started == 0
    assert listener.listening == set()
    assert store.reads == 0, (
        "a non-leader read the deployment table; the work must not happen at all"
    )
    assert w.describe()["is_leader"] is False


@pytest.mark.asyncio
async def test_losing_the_lease_closes_every_socket(monkeypatch):
    """The dangerous direction.

    A process that keeps streaming after another has taken over gives every fill two
    writers -- exactly the fan-out the lease exists to prevent.
    """
    state = {"leader": True}
    calls: list = []
    listener = _Listener()
    listener.calls = calls
    bus = _EventBus(shared_calls=calls)

    async def fake_lease(**kw):
        return state["leader"]

    monkeypatch.setattr(mod, "try_acquire_or_renew", fake_lease)
    w = ExchangeFillWorker(
        state_store=_Store(rows=[LIVE]), listener=listener, event_bus=bus,
        router=object(),
    )

    await w.process_iteration()
    assert listener.listening == {("t1", "binance")}

    state["leader"] = False
    await w.process_iteration()

    assert listener.listening == set(), "a demoted process kept streaming"
    assert listener.stopped == 1
    assert bus.stopped == 1
    assert w.describe()["chain_started"] is False


@pytest.mark.asyncio
async def test_unverifiable_leadership_is_treated_as_not_leader(monkeypatch):
    """`try_acquire_or_renew` answers False when Redis is unreachable, by design.

    Standing down is the safe reading of "unknown": the alternative is every process
    assuming it has no competition, which is the fan-out the primitive prevents.
    """
    w, listener, bus, _store, _calls = _worker(monkeypatch, leader=False, rows=[LIVE])
    await w.process_iteration()
    assert listener.started == 0 and bus.started == 0


def test_the_lease_is_delegated_and_not_reimplemented():
    """RECON-01's rule, asserted for this caller too.

    Its guard test requires callers to delegate to the shared primitive and NOT carry
    their own `nx=True` -- two implementations of a distributed-locking rule must not
    drift.
    """
    source = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    assert "try_acquire_or_renew" in source
    assert "nx=True" not in source, (
        "this worker reimplements the Redis lock instead of delegating to leader_lease"
    )


def test_the_lease_outlives_a_poll_cycle():
    """`leader_lease` states this as an argument contract.

    A TTL below the loop interval means the holder keeps losing a lease it is still
    actively using, so leadership churns and the streams are torn down repeatedly.
    """
    assert mod.LEASE_TTL_SECONDS > mod.POLL_INTERVAL_SECONDS


def test_the_lease_key_is_specific_to_this_subsystem():
    """Sharing a key with another loop makes each one's leader suppress the other."""
    from backend_app.backend import exchange_reconciliation as recon

    other = getattr(recon, "RECONCILIATION_LEADER_LOCK_KEY", None)
    assert mod.LEADER_LOCK_KEY != other
    assert "fill" in mod.LEADER_LOCK_KEY


# ══════════════════════════════════════════════════════════════════════════════════════
#  3 — WHICH PAIRS, AND PAPER MUST NOT OPEN A SOCKET
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row,expected",
    [
        ({"user_id": "t1", "exchange_id": "binance", "mode": "live"}, True),
        ({"user_id": "t1", "exchange_id": "BINANCE", "mode": "LIVE"}, True),
        # `environment` is the fallback deployment_runtime itself uses when 004e's
        # binding columns have not been applied.
        ({"user_id": "t1", "exchange_id": "binance", "environment": "live"}, True),
        # Paper fills against this platform's own simulator and has no venue orders to
        # hear about, so streaming for one spends an authenticated socket to receive
        # nothing -- and needs a credential the user may never have stored.
        ({"user_id": "t1", "exchange_id": "binance", "mode": "paper"}, False),
        ({"user_id": "t1", "exchange_id": "binance", "environment": "paper"}, False),
        # Mode unestablished: NOT live. This is the one case where guessing wrong opens
        # a real socket.
        ({"user_id": "t1", "exchange_id": "binance"}, False),
        # No venue to connect to; guessing one would dial the wrong exchange with a real
        # credential.
        ({"user_id": "t1", "mode": "live"}, False),
        ({"exchange_id": "binance", "mode": "live"}, False),
    ],
)
async def test_only_live_deployments_naming_a_venue_are_streamed(monkeypatch, row, expected):
    w, listener, _bus, _store, _calls = _worker(monkeypatch, rows=[row])

    await w.process_iteration()

    assert bool(listener.listening) is expected, (
        f"row {row} was {'not ' if expected else ''}streamed"
    )


@pytest.mark.asyncio
async def test_two_deployments_on_one_pair_open_one_socket(monkeypatch):
    """The pair is the unit, not the deployment. Three bots on one venue share a stream."""
    rows = [
        {"user_id": "t1", "exchange_id": "binance", "mode": "live"},
        {"user_id": "t1", "exchange_id": "binance", "mode": "live"},
        {"user_id": "t1", "exchange_id": "bybit", "mode": "live"},
    ]
    w, listener, _bus, _store, _calls = _worker(monkeypatch, rows=rows)

    await w.process_iteration()

    assert listener.listening == {("t1", "binance"), ("t1", "bybit")}
    assert len([c for c in listener.calls if c.startswith("listen+")]) == 2


@pytest.mark.asyncio
async def test_a_retired_deployment_stops_its_stream(monkeypatch):
    store = _Store(rows=[LIVE, {"user_id": "t2", "exchange_id": "bybit", "mode": "live"}])
    calls: list = []
    listener = _Listener()
    listener.calls = calls

    async def fake_lease(**kw):
        return True

    monkeypatch.setattr(mod, "try_acquire_or_renew", fake_lease)
    w = ExchangeFillWorker(
        state_store=store, listener=listener,
        event_bus=_EventBus(shared_calls=calls), router=object(),
    )

    await w.process_iteration()
    assert listener.listening == {("t1", "binance"), ("t2", "bybit")}

    store.rows = [LIVE]  # t2 stopped
    await w.process_iteration()

    assert listener.listening == {("t1", "binance")}
    assert w.describe()["streaming_pairs"] == [("t1", "binance")]


@pytest.mark.asyncio
async def test_a_failed_deployment_read_leaves_the_streams_alone(monkeypatch):
    """Fails closed BY CHANGING NOTHING, which is the right direction here.

    Returning an empty set on a bad read would tear down every working stream because
    one query failed.
    """
    store = _Store(rows=[LIVE])
    calls: list = []
    listener = _Listener()
    listener.calls = calls

    async def fake_lease(**kw):
        return True

    monkeypatch.setattr(mod, "try_acquire_or_renew", fake_lease)
    w = ExchangeFillWorker(
        state_store=store, listener=listener,
        event_bus=_EventBus(shared_calls=calls), router=object(),
    )

    await w.process_iteration()
    assert listener.listening == {("t1", "binance")}

    store.raises = True
    await w.process_iteration()

    assert listener.listening == {("t1", "binance")}, (
        "one failed read tore down a working stream"
    )


@pytest.mark.asyncio
async def test_a_pair_that_fails_to_start_is_retried_next_cycle(monkeypatch):
    store = _Store(rows=[LIVE])
    calls: list = []
    listener = _Listener()
    listener.calls = calls
    listener.fail_on = {("t1", "binance")}

    async def fake_lease(**kw):
        return True

    monkeypatch.setattr(mod, "try_acquire_or_renew", fake_lease)
    w = ExchangeFillWorker(
        state_store=store, listener=listener,
        event_bus=_EventBus(shared_calls=calls), router=object(),
    )

    await w.process_iteration()
    assert listener.listening == set()
    assert w.describe()["streaming_pairs"] == [], (
        "a pair that failed to start was recorded as streaming, so it would never retry"
    )

    listener.fail_on = set()
    await w.process_iteration()
    assert listener.listening == {("t1", "binance")}


@pytest.mark.asyncio
async def test_cleanup_tears_the_whole_chain_down(monkeypatch):
    w, listener, bus, _store, _calls = _worker(monkeypatch, rows=[LIVE])
    await w.process_iteration()

    await w.cleanup()

    assert listener.listening == set()
    assert listener.stopped == 1
    assert bus.stopped == 1


# ══════════════════════════════════════════════════════════════════════════════════════
#  4 — A SESSION PER EVENT, NOT ONE FOR THE PROCESS'S LIFETIME
# ══════════════════════════════════════════════════════════════════════════════════════

class _Session:
    def __init__(self, log):
        self.log = log
        self.closed = False
        self.rolled_back = False

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True
        self.log.append("close")


@pytest.mark.asyncio
async def test_each_event_gets_its_own_session_and_closes_it():
    """EventListener holds ONE router for its lifetime.

    Handing `EventRouter` a session would therefore pin one open for days, accumulating
    identity-map state and holding a pooled connection across every idle period.
    """
    log: list = []
    made: list = []

    def session_factory():
        s = _Session(log)
        made.append(s)
        return s

    class _Router:
        def __init__(self, session):
            self.session = session

        async def route_event(self, event):
            log.append("routed")

    per_event = _PerEventRouter(session_factory=session_factory,
                                router_factory=_Router)

    await per_event.route_event(object())
    await per_event.route_event(object())

    assert len(made) == 2, "the two events shared a session"
    assert all(s.closed for s in made), "a session was leaked"
    assert per_event.routed == 2
    assert log == ["routed", "close", "routed", "close"]


@pytest.mark.asyncio
async def test_a_failing_event_rolls_back_closes_and_does_not_escape():
    """One poisoned event must not kill the stream for every other tenant.

    It is dropped rather than retried, which is acceptable only because the watchdog
    still reconciles from the venue -- this stream is an accelerator, not the system of
    record.
    """
    log: list = []
    made: list = []

    def session_factory():
        s = _Session(log)
        made.append(s)
        return s

    class _Boom:
        def __init__(self, session):
            pass

        async def route_event(self, event):
            raise RuntimeError("handler blew up")

    per_event = _PerEventRouter(session_factory=session_factory, router_factory=_Boom)

    await per_event.route_event(object())  # must not raise

    assert made[0].rolled_back is True
    assert made[0].closed is True
    assert per_event.failed == 1
    assert per_event.routed == 0


# ══════════════════════════════════════════════════════════════════════════════════════
#  5 — INF-07: A DECLARED WORKER THAT CANNOT RUN IS WORSE THAN AN UNDECLARED ONE
# ══════════════════════════════════════════════════════════════════════════════════════

def test_the_module_can_actually_be_run_as_a_process():
    """INF-07, measured in production on `dag_worker`.

    A module with no `__main__` imports, reaches the end of the file and exits 0, which
    ECS cannot tell apart from a job that completed -- so it restarts the task forever
    while the service reports a task running.
    """
    path = pathlib.Path(mod.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))

    assert any(
        isinstance(n, ast.If) and "__main__" in ast.dump(n.test) for n in tree.body
    ), "no top-level __main__ block; `python -m` on this module would exit 0 immediately"
    assert any(
        isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == "run_worker"
        for n in tree.body
    ), "no run_worker(); the three working workers all expose one so hosting has one shape"


def test_terraform_declares_this_worker_with_the_module_that_exists():
    """The declaration and the module must name each other.

    `deploy_worker_services.py` parses `local.worker_definitions` with a REGEX requiring
    cpu, memory and command in that order on one line, so a reordered or wrapped entry is
    silently skipped.
    """
    tf = (pathlib.Path(mod.__file__).parents[2] / "terraform" / "ecs.tf").read_text(
        encoding="utf-8"
    )
    assert "exchange_fills" in tf, "the worker is not declared in terraform"
    assert "backend_app.workers.exchange_fill_worker" in tf
