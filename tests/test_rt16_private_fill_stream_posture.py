"""RT-16 -- the venue fill stream: the chain that is not wired, and two defects in it.

WHY THIS FILE EXISTS
--------------------
RT-16's remaining_action says: "NO PRIVATE ORDER/FILL STREAM IS CONSUMED ANYWHERE.
`EventRouter._handle_filled` exists and now resolves the right row, but nothing feeds it
-- so a fill is still only discovered by the watchdog's poll." That state was recorded in
prose only. Nothing asserted it, so any one of the three links could be wired -- or any of
them quietly broken further -- without a test noticing, and a reader could reasonably
conclude from `EventRouter`'s existence that fills are streamed.

THE CHAIN, AND ALL THREE LINKS ARE OPEN
---------------------------------------
    1. ExchangeWebSocketListener._listen_for_orders   watch_orders() -> _handle_order_update
       `start()` / `start_listening()` have no caller.
    2. EventListener._on_exchange_event               enqueue -> _event_processing_loop
       `start(event_router)` -- the only thing that sets `_event_router` AND drains the
       queue -- has no caller.
    3. EventRouter.route_event                        -> _handle_filled -> the DB
       `EventRouter` / `get_event_router` have no caller; main.py never imports either.

So even starting link 1 today would deliver nothing: events would be built, handed to a
listener with no router, and -- before this change -- queued into a PriorityQueue nothing
drains.

TWO REAL DEFECTS FOUND WHILE ESTABLISHING THAT, AND FIXED HERE
--------------------------------------------------------------
(A) `_listen_for_orders` called `get_or_create_exchange(user_id=..., exchange_id=...)` with
    NO credentials and then awaited `watch_orders()`, which is an authenticated per-account
    channel. It could not work, and the enclosing `except Exception: sleep(1)` meant it
    would not fail loudly either -- it would spin once a second forever, logging what looks
    like a transient network error. Worse, the connection pool is keyed on
    (user, exchange), so this listener winning the pool race would hand an UNAUTHENTICATED
    client to every credentialed caller for that pair. CONN-01 records exactly that, naming
    this file as one of the two offenders.

(B) `EventListener._on_exchange_event` added the event to `_processed_event_ids` and then
    queued it, with no check that anything drains the queue. On an unstarted listener that
    is the worst of the three possible behaviours at once: the fill is lost, the memory is
    not, a redelivery is suppressed as a duplicate, and nothing reports any of it.

WHAT THIS FILE DOES NOT CLAIM
-----------------------------
Nothing here certifies venue reconciliation. No exchange is contacted; every test uses
fakes or reads source. RT-16 stays open: certification needs a live venue, credentials
(VAULT-01 records every stored ciphertext as currently undecryptable) and a host for a
per-(tenant, exchange) consumer fleet (INF-03). Live trading stays OFF.
"""
from __future__ import annotations

import asyncio
import inspect
import pathlib
from datetime import datetime, timezone

import pytest

from backend_app.backend.event_listener import (EventListener, EventType,
                                                ExchangeEvent)
from backend_app.backend.exchange_websocket_listener import \
    ExchangeWebSocketListener

REPO = pathlib.Path(__file__).resolve().parents[1]
MAIN_SRC = REPO / "backend_app" / "main.py"
WS_LISTENER_SRC = REPO / "backend_app" / "backend" / "exchange_websocket_listener.py"


def _code_only(source: str) -> str:
    """``source`` with comment lines removed.

    Needed because the fixes below are heavily commented and those comments NAME the
    thing being asserted about -- ``get_or_create_exchange``, ``create_task`` -- so a
    naive substring or ordering check reads the explanation instead of the code. Two of
    these tests failed that way on first run, which is the same trap
    test_p10d_exchange_reconciliation_fails_safely.py records hitting: a comment
    mentioning a module made an identical assertion fail with nothing wired.
    """
    return "\n".join(
        line for line in source.splitlines()
        if not line.lstrip().startswith("#")
    )


def _main_code_only() -> str:
    """main.py with comment lines stripped.

    The same precaution test_p10d_exchange_reconciliation_fails_safely.py had to take:
    a comment that merely NAMES a module made an identical assertion fail there without a
    line of code wiring anything.
    """
    return "\n".join(
        line for line in MAIN_SRC.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


# ══════════════════════════════════════════════════════════════════════════════
#  1. The chain is open, link by link
# ══════════════════════════════════════════════════════════════════════════════


def test_link_1_the_websocket_listener_is_not_started_in_the_web_process():
    code = _main_code_only()
    for marker in ("ExchangeWebSocketListener", "start_listening"):
        assert marker not in code, (
            f"{marker} now appears in backend_app/main.py. Starting the venue order "
            f"stream needs credentials per (tenant, exchange) (VAULT-01), a host that "
            f"is not the web process (INF-03, PAPER-04), and leader election -- "
            f"gunicorn runs --workers ${{WORKERS:-2}}, so two workers would each open a "
            f"private stream for every pair. If that decision has been taken, update "
            f"this test and record the reasoning in RT-16."
        )


def test_link_2_the_event_listener_is_not_started_in_the_web_process():
    code = _main_code_only()
    assert "EventListener" not in code, (
        "EventListener now appears in backend_app/main.py. It is only useful with a "
        "router: start(event_router) is what sets _event_router and drains the queue."
    )


def test_link_3_the_event_router_is_not_constructed_in_the_web_process():
    code = _main_code_only()
    for marker in ("EventRouter", "get_event_router", "route_event"):
        assert marker not in code, (
            f"{marker} now appears in backend_app/main.py. EventRouter is the link that "
            f"actually writes a fill to the ledger, and "
            f"backend_app/core/models/execution_record.py records that 'OrderWatchdog is "
            f"the only owner of adjudication, and introducing a second one would be "
            f"worse than the defect' -- so wiring this is an architecture decision, not "
            f"a wiring fix."
        )


def test_the_three_links_are_pinned_separately_and_not_as_one():
    """Each link is asserted on its own, deliberately.

    Closing any ONE of them delivers nothing and must not read as progress toward a
    streamed fill: a started websocket listener with no router still loses every event,
    and a constructed router with no feeder still never executes. Three assertions mean
    three separate decisions.
    """
    names = {
        name for name, obj in globals().items()
        if name.startswith("test_link_") and callable(obj)
    }
    assert len(names) == 3, f"expected one test per link, found {sorted(names)}"


# ══════════════════════════════════════════════════════════════════════════════
#  2. Defect A: a private channel is never opened unauthenticated
# ══════════════════════════════════════════════════════════════════════════════


def test_the_private_stream_resolves_credentials_before_connecting():
    source = _code_only(
        inspect.getsource(ExchangeWebSocketListener._listen_for_orders)
    )
    assert "_load_keys" in source, (
        "_listen_for_orders no longer resolves credentials. watch_orders() is an "
        "authenticated channel; without keys it cannot subscribe, and the enclosing "
        "except/sleep(1) turns that into a silent once-per-second retry forever."
    )
    assert source.index("_load_keys") < source.index("get_or_create_exchange"), (
        "credentials must be resolved BEFORE the pooled client is requested -- "
        "requesting it first is what caches an unauthenticated client (CONN-01)"
    )


def test_no_credential_means_no_stream_and_no_pool_entry():
    """THE HEADLINE FOR DEFECT A. Refusing must happen before the pool is touched."""
    listener = ExchangeWebSocketListener()
    listener._running = True
    calls = []

    async def _must_not_be_called(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("the connection pool was reached without credentials")

    import backend_app.backend.exchange_websocket_listener as mod

    original_get = mod.get_or_create_exchange
    original_load = ExchangeWebSocketListener._load_keys
    mod.get_or_create_exchange = _must_not_be_called
    ExchangeWebSocketListener._load_keys = staticmethod(lambda t, e: None)
    try:
        asyncio.run(listener._listen_for_orders("tenant-1", "binance", None))
    finally:
        mod.get_or_create_exchange = original_get
        ExchangeWebSocketListener._load_keys = original_load

    assert calls == [], (
        "get_or_create_exchange was called with no credentials available. The pool is "
        "keyed on (user, exchange), so this caches an unauthenticated client that every "
        "credentialed caller for the pair then receives."
    )


def test_credentials_are_passed_positionally_in_the_pools_own_order():
    """`get_or_create_exchange(user_id, exchange_id, api_key, secret_key, password)`.

    Pinned because a silent mis-ordering here produces a client that authenticates with
    the secret as the key -- which the venue rejects, and which the retry loop hides.
    """
    from backend_app.backend.connection_engine import get_or_create_exchange

    params = list(inspect.signature(get_or_create_exchange).parameters)
    assert params[:5] == [
        "user_id", "exchange_id", "api_key", "secret_key", "password"
    ], params


@pytest.mark.parametrize(
    "keys",
    [None, {}, {"api_key": "k"}, {"secret_key": "s"},
     {"api_key": "", "secret_key": "s"}, {"api_key": "k", "secret_key": ""},
     "not-a-dict"],
)
def test_an_incomplete_credential_is_refused_rather_than_half_used(monkeypatch, keys):
    class _Vault:
        def load_decrypted_keys(self, user_id, exchange_id):
            if keys == "not-a-dict":
                return keys
            return keys

    import backend_app.core.dependencies as deps

    monkeypatch.setattr(deps, "get_vault", lambda: _Vault())
    assert ExchangeWebSocketListener._load_keys("t", "binance") is None


def test_a_vault_that_raises_is_none_not_an_exception(monkeypatch):
    """VAULT-01: every stored ciphertext is currently undecryptable, so this is the
    EXPECTED path today, not an edge case."""
    class _Boom:
        def load_decrypted_keys(self, user_id, exchange_id):
            raise RuntimeError("cannot decrypt: master key unavailable")

    import backend_app.core.dependencies as deps

    monkeypatch.setattr(deps, "get_vault", lambda: _Boom())
    assert ExchangeWebSocketListener._load_keys("t", "binance") is None


def test_a_complete_credential_is_returned(monkeypatch):
    class _Vault:
        def load_decrypted_keys(self, user_id, exchange_id):
            return {"api_key": "k", "secret_key": "s", "password": "p"}

    import backend_app.core.dependencies as deps

    monkeypatch.setattr(deps, "get_vault", lambda: _Vault())
    keys = ExchangeWebSocketListener._load_keys("t", "binance")
    assert keys == {"api_key": "k", "secret_key": "s", "password": "p"}


def test_conn01s_offender_list_still_names_this_file():
    """If this file stops calling the pool without credentials, CONN-01's note should
    not silently keep claiming it does -- and if it starts again, this is the reminder."""
    source = WS_LISTENER_SRC.read_text(encoding="utf-8")
    assert "CONN-01" in source, (
        "the reason credentials are mandatory here is CONN-01's pool-poisoning finding; "
        "keep the citation so the next editor sees why"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  3. Defect B: nothing is queued into a queue nothing drains
# ══════════════════════════════════════════════════════════════════════════════


def _event(event_id: str = "evt-1") -> ExchangeEvent:
    return ExchangeEvent(
        event_id=event_id,
        event_type=EventType.ORDER_FILLED,
        tenant_id="tenant-1",
        exchange_id="binance",
        data={"order_id": "V-1"},
        exchange_timestamp=datetime.now(timezone.utc),
        received_at=datetime.now(timezone.utc),
    )


def test_an_unstarted_listener_does_not_grow_its_queue():
    """THE HEADLINE FOR DEFECT B."""
    listener = EventListener()
    assert listener._event_router is None, "a fresh listener has no router"

    listener._on_exchange_event(_event())

    assert listener._event_queue.qsize() == 0, (
        "the event was queued into a PriorityQueue that nothing drains -- "
        "_event_processing_loop only runs after start(event_router), which has no caller. "
        "That loses the fill and leaks the memory."
    )


def test_a_refused_event_is_not_recorded_as_processed():
    """Otherwise a redelivery of the same fill is discarded as a duplicate.

    This is the part that makes the old behaviour unrecoverable rather than merely
    lossy: the dedup set was updated before the queue, so the venue resending the
    event -- the one mechanism that could have saved it -- was suppressed.
    """
    listener = EventListener()
    event = _event("evt-dedup")

    listener._on_exchange_event(event)

    assert event.event_id not in listener._processed_event_ids, (
        "the refused event is still marked processed, so a redelivery would be dropped "
        "as a duplicate and the fill could never arrive by any route"
    )


def test_a_started_listener_does_queue():
    """The guard must not break the wired case it exists to protect."""
    listener = EventListener()
    listener._running = True
    listener._event_router = object()

    listener._on_exchange_event(_event("evt-ok"))

    assert listener._event_queue.qsize() == 1


def test_the_enqueue_is_synchronous_and_ordered():
    """`put_nowait`, not `create_task(put(...))`.

    The queue is unbounded, so `put` never blocks and the task was pure overhead -- and
    it detached the enqueue from the call, so the relative order of two events delivered
    in the same tick depended on task scheduling rather than on the sequence key that
    exists to decide it, and any exception vanished into an un-awaited task.
    """
    source = _code_only(inspect.getsource(EventListener._on_exchange_event))
    assert "put_nowait" in source
    assert "create_task" not in source, (
        "the enqueue is detached again; ordering then depends on task scheduling rather "
        "than on the sequence key"
    )

    listener = EventListener()
    listener._running = True
    listener._event_router = object()
    first, second = _event("evt-a"), _event("evt-b")
    first.sequence_id, second.sequence_id = 1, 2

    listener._on_exchange_event(first)
    listener._on_exchange_event(second)

    assert listener._event_queue.qsize() == 2, (
        "both events must be queued by the time the call returns, with no event loop "
        "needed to make that true"
    )


def test_a_duplicate_is_still_dropped_when_wired():
    """The dedup behaviour the guard reaches around must survive it."""
    listener = EventListener()
    listener._running = True
    listener._event_router = object()
    event = _event("evt-same")

    listener._on_exchange_event(event)
    listener._on_exchange_event(event)

    assert listener._event_queue.qsize() == 1
