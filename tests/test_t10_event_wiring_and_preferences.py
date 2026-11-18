"""
T10 — event wiring and preference filtering, exercised rather than grepped.

WHAT WAS MEASURED BEFORE THIS
=============================
Two separate defects, both recorded as passing "gap" tests in
`tests/test_notif01_durable_dedupe_postgres.py` and
`tests/test_notif02_mandatory_alert_floor.py`:

1. **No preference read anywhere on the dispatch path.** `notification_settings` had two
   readers across `backend_app`, both in `routers/user.py` — the GET and the PUT of the
   preferences panel itself. So the eight switches a customer could set governed nothing:
   turning "Trade Executions" off still delivered every fill.

2. **Six event classes with no producer at all.** AST over every
   `dispatch_user_notification` call site found 17 sites and not one of them a trade fill,
   an order failure, a stop-loss firing, a risk-limit breach, a deployment failure or an
   authentication event. `new_login_detected` existed in migration 073's CHECK constraint,
   in the request model and in the panel, and was dispatched from nowhere.

WHAT THIS FILE ASSERTS, AND WHY IT IS SHAPED THIS WAY
=====================================================
Every test here CALLS the code. Source-text assertions (does the dispatcher mention the
filter, does an event type have a producer) stay in the two files that already owned those
measurements; duplicating them here would be two tests that pass together and fail together.

The notification stores are process-global (`routers/notifications._in_memory_notifications`
and `_idempotency_index`), so every test that observes a dispatch clears them first — see
`clean_notification_store`. Without that, dedupe assertions would pass for the wrong reason:
a key written by an earlier test would already be in the index.

NO POSTGRESQL GATE ON THIS FILE, DELIBERATELY. The durable half — that
`uq_notifications_user_idempotency` is the authority for dedupe, and that the severity CHECK
holds — is `test_notif01_durable_dedupe_postgres.py`'s subject and is gated there. What is
tested here is the DECISION: which events dispatch, with what key, and whether a preference
suppresses one. Those are properties of the Python, measurable without a database, and gating
them on PostgreSQL would turn them into skips in the default lane, which is where a
regression would actually be caught.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

import pytest

from backend_app.core import notification_dispatcher as dispatcher
from backend_app.core.models.pydantic_models import MANDATORY_NOTIFICATION_EVENTS
from backend_app.core.notification_preferences import (
    MANDATORY_EVENT_TYPES,
    PREFERENCE_KEY_BY_EVENT_TYPE,
    PREFERENCE_KEYS_WITHOUT_PRODUCER,
    event_is_permitted,
    is_mandatory_event,
    preference_key_for,
)
from backend_app.routers import notifications as notifications_router


# ══════════════════════════════════════════════════════════════════════════
# FIXTURES AND FAKES
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def clean_notification_store():
    """Empty the process-global notification stores around every test.

    `create_notification` writes to `_in_memory_notifications` and `_idempotency_index`
    whenever no Supabase client is configured, which is the shape of this test run. Both
    are module-level dicts, so a key left behind by one test makes the next test's "was it
    de-duplicated?" assertion meaningless — it would pass because of the previous test.
    Cleared before AND after so a failure cannot contaminate the rest of the file.
    """
    with notifications_router._notifications_lock:
        notifications_router._in_memory_notifications.clear()
        notifications_router._idempotency_index.clear()
    yield
    with notifications_router._notifications_lock:
        notifications_router._in_memory_notifications.clear()
        notifications_router._idempotency_index.clear()


@pytest.fixture(autouse=True)
def no_service_client(monkeypatch):
    """Make `_resolve_persistence_client` resolve to None unless a test says otherwise.

    The dispatcher resolves a service-role client when a caller supplies none (NOTIF-01).
    In a test run there is no such client, but `get_service_supabase_async` may still do
    real work discovering that — and a test must not depend on how long that takes or on
    whatever a developer has in their environment.

    A SUPPLIED CLIENT STILL WINS, which is the real contract (`routers/support.py` passes
    its own) and is how these tests inject a preference store. Only the "nothing supplied"
    branch is pinned, to None.
    """

    async def _resolve(supplied=None):
        return supplied

    monkeypatch.setattr(dispatcher, "_resolve_persistence_client", _resolve)


class _Result:
    def __init__(self, data: List[Dict[str, Any]]) -> None:
        self.data = data


class FakePreferenceStore:
    """A Supabase-shaped client covering the two tables the dispatch path touches.

    TABLE-AWARE ON PURPOSE. `notification_settings` is read by
    `core.notification_preferences._stored_events` and `notifications` is written by
    `routers/notifications.create_notification`, through the SAME client — the dispatcher
    resolves one and uses it for both. A fake that answered the preference row to every
    query would make the notification insert echo back a preference row, and the store
    assertions would then fail for a reason that has nothing to do with the behaviour under
    test. (That is not hypothetical: it is what the first version of this fake did.)

    The call chains are matched exactly — `.table().select().eq().limit().execute()` for
    the read and `.table().insert().execute()` for the write — so a change to either chain
    fails here rather than silently reading or writing nothing.

    `raise_on_read` reproduces an unreachable preference store, the case that must FAIL
    TOWARD DELIVERY.
    """

    def __init__(
        self,
        events: Optional[Dict[str, Any]] = None,
        *,
        rows: Optional[List[Dict[str, Any]]] = None,
        raise_on_read: bool = False,
    ) -> None:
        self._rows = rows if rows is not None else (
            [{"events": events}] if events is not None else []
        )
        self._raise = raise_on_read
        self.tables_read: List[str] = []
        self.inserted: List[Dict[str, Any]] = []
        self._table: Optional[str] = None
        self._payload: Optional[Dict[str, Any]] = None

    # -- query builder -----------------------------------------------------
    def table(self, name: str):
        self.tables_read.append(name)
        if self._raise and name == "notification_settings":
            raise RuntimeError("preference store is unreachable")
        self._table = name
        self._payload = None
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def insert(self, payload: Dict[str, Any]):
        self._payload = payload
        return self

    def execute(self):
        if self._table == "notification_settings":
            return _Result(list(self._rows))
        if self._table == "notifications" and self._payload is not None:
            # Echo the row back with a generated id, the way PostgREST returns the
            # inserted row with its `gen_random_uuid()` default applied. `create_notification`
            # reads that row back and uses it as the record, so echoing keeps `user_id`
            # on it — which is what `_stored_for` filters on.
            import uuid as _uuid

            row = {**self._payload, "id": str(_uuid.uuid4())}
            self.inserted.append(row)
            return _Result([row])
        return _Result([])


def _dispatch(**kwargs) -> Optional[str]:
    """Run one dispatch to completion and return the notification id (or None)."""
    return asyncio.run(dispatcher.dispatch_user_notification(**kwargs))


def _stored_for(user_id: str) -> List[Dict[str, Any]]:
    with notifications_router._notifications_lock:
        return [
            n
            for n in notifications_router._in_memory_notifications.values()
            if n.get("user_id") == user_id
        ]


# ══════════════════════════════════════════════════════════════════════════
# PART 1 — the mapping itself
# ══════════════════════════════════════════════════════════════════════════


def test_every_mapped_preference_key_is_one_the_model_declares() -> None:
    """A filter keyed on a name no row carries would suppress nothing.

    This is the trap migration 073 documents for its CHECK constraint: the column's
    declared default names `system`/`stop_loss`/`risk_alerts`/`trade_fills`, and not one
    production row carries any of them — every row carries the eight fields of
    `NotificationEvents`, because the panel's PUT always sends the whole object. A mapping
    pointing at the declared defaults would read a missing key, find nothing, deliver
    everything, and look enforced.
    """
    from backend_app.core.models.pydantic_models import NotificationEvents

    declared = set(NotificationEvents.model_fields)
    mapped = set(PREFERENCE_KEY_BY_EVENT_TYPE.values())
    assert mapped <= declared, (
        f"these preference keys are not fields of NotificationEvents and therefore "
        f"appear in no stored row: {sorted(mapped - declared)}"
    )

    # And the inverse, which is the part that keeps the mapping honest as the platform
    # grows: every one of the eight declared keys is either mapped to at least one event
    # type or explicitly recorded as having no producer. A key in neither set is a switch
    # the panel shows and nobody has decided about.
    assert declared == mapped | PREFERENCE_KEYS_WITHOUT_PRODUCER, (
        "these preference keys govern nothing and are not listed as producerless: "
        f"{sorted(declared - mapped - PREFERENCE_KEYS_WITHOUT_PRODUCER)}. Either map an "
        "event type onto them or add them to PREFERENCE_KEYS_WITHOUT_PRODUCER with the "
        "measurement that says why."
    )


def test_the_mandatory_floor_is_derived_from_the_model_not_restated() -> None:
    """`MANDATORY_EVENT_TYPES` must follow `MANDATORY_NOTIFICATION_EVENTS`.

    Two hand-maintained lists of the same floor is one edit away from a mandatory alert
    becoming suppressible, with nothing in either file looking wrong. So the filter derives
    its set, and this asserts the derivation rather than the contents.
    """
    for event_type in MANDATORY_EVENT_TYPES:
        key = preference_key_for(event_type)
        assert key in MANDATORY_NOTIFICATION_EVENTS, (
            f"{event_type} is treated as mandatory but its preference key {key!r} is not "
            "on the model's floor"
        )

    # The three model keys each govern at least the event type of the same name.
    for key in MANDATORY_NOTIFICATION_EVENTS:
        assert is_mandatory_event(key), (
            f"{key} is on the model's mandatory floor but the dispatcher would let a "
            "preference suppress it"
        )


# ══════════════════════════════════════════════════════════════════════════
# PART 2 — preference suppression, the floor, and a failed read
# ══════════════════════════════════════════════════════════════════════════


def test_a_preference_that_is_off_suppresses_the_notification() -> None:
    """The headline behaviour: a stored `false` stops the dispatch."""
    client = FakePreferenceStore({"trade_executed": False})
    assert asyncio.run(event_is_permitted("u-1", "trade_executed", client)) is False
    assert client.tables_read == ["notification_settings"]


def test_a_suppressed_event_is_neither_stored_nor_returned() -> None:
    """Suppression happens BEFORE `create_notification`, not after.

    Filtering after the insert would leave the row sitting in the very feed the customer
    muted, and would still have broadcast it over their WebSocket. So the observable
    consequence is: no id returned, nothing in the store, and the WebSocket manager never
    called.
    """
    broadcasts: List[Any] = []

    class _WS:
        async def broadcast_user(self, *args, **kwargs):
            broadcasts.append((args, kwargs))

    notification_id = _dispatch(
        user_id="u-suppressed",
        event_type="trade_executed",
        category="trade",
        title="Fill",
        message="BUY 1 BTC/USDT",
        severity="info",
        metadata={"idempotency_key": "order_filled:exec-1"},
        supabase=FakePreferenceStore({"trade_executed": False}),
        ws_manager=_WS(),
    )

    assert notification_id is None
    assert _stored_for("u-suppressed") == []
    assert broadcasts == [], "a suppressed notification was still broadcast live"


@pytest.mark.parametrize("event_type", sorted(MANDATORY_EVENT_TYPES))
def test_the_mandatory_floor_overrides_a_preference_that_is_off(event_type: str) -> None:
    """Every mandatory event delivers even when its switch is stored as false.

    The row used here has ALL THREE mandatory keys set to `false` — a state migration 073's
    CHECK now forbids, which is exactly why the dispatcher must not depend on the CHECK: a
    row written before the constraint, or through a route that bypassed the request model,
    can still look like this.
    """
    all_off = {key: False for key in MANDATORY_NOTIFICATION_EVENTS}
    client = FakePreferenceStore(all_off)
    assert asyncio.run(event_is_permitted("u-2", event_type, client)) is True


def test_the_floor_does_not_even_read_the_store() -> None:
    """A floor that depends on a successful database read is not a floor.

    `event_is_permitted` answers for a mandatory event before touching the client, so an
    unreachable preference store cannot affect a kill-switch alert at all. Asserted by
    handing it a client that RAISES on any read: if the floor were implemented as
    "read, then override", this would still pass — so the assertion is that the client was
    never touched.
    """
    exploding = FakePreferenceStore(raise_on_read=True)
    assert asyncio.run(event_is_permitted("u-3", "kill_switch_activated", exploding)) is True
    assert exploding.tables_read == [], (
        "the mandatory floor consulted the preference store. It must answer before any "
        "read, so that a store failure can never suppress a safety alert."
    )


def test_a_failed_preference_read_still_delivers_and_logs_at_error(caplog) -> None:
    """An unreadable preference store is not a customer asking for silence.

    The failure direction matters more than the mechanism: dropping a notification because
    the preferences could not be read is the NOTIF-01 defect (18 of 19 call sites writing
    nowhere) happening again through a new door. So the read fails open, and says so at
    ERROR — DEBUG is not enabled in production, which is how the original defect stayed
    invisible.
    """
    caplog.set_level(logging.ERROR, logger="backend_app.core.notification_preferences")

    permitted = asyncio.run(
        event_is_permitted("u-4", "trade_executed", FakePreferenceStore(raise_on_read=True))
    )
    assert permitted is True

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "a failed preference read logged nothing at ERROR"
    assert "DELIVERED" in errors[0].getMessage()


def test_a_malformed_events_column_delivers() -> None:
    """A row whose `events` is not an object answers "deliver", not "suppress"."""
    for bad in ("not-an-object", 7, [], None):
        client = FakePreferenceStore(rows=[{"events": bad}])
        assert asyncio.run(event_is_permitted("u-5", "trade_executed", client)) is True


@pytest.mark.parametrize("value", [0, None, "false", "no", "", []])
def test_only_an_unambiguous_false_suppresses(value: Any) -> None:
    """`0` and `null` are NOT consent, which is the dangerous case 073 measured.

    Migration 073's probe recorded which shapes this column accepts at a DISCRETIONARY key:
    the JSON boolean `false`, the strings `"false"`/`"no"`, the number `0`, and JSON `null`
    are all storable, because the CHECK only constrains the three mandatory keys and
    `authenticated` retains INSERT and UPDATE. `bool(0)` and `bool(None)` are both False in
    Python, so a truthiness test would read a number and a null as "switched off" — a
    suppression arriving through a shape nobody intended as one.
    """
    client = FakePreferenceStore({"trade_executed": value})
    assert asyncio.run(event_is_permitted("u-6", "trade_executed", client)) is True


def test_an_absent_key_and_an_absent_row_both_deliver() -> None:
    """A partial payload that never mentions a switch does not disagree with it.

    Same clause migration 073's predicate carries for an absent mandatory key, applied to
    the discretionary ones: an account that has never opened the preferences panel has no
    row at all and must receive everything.
    """
    assert asyncio.run(
        event_is_permitted("u-7", "trade_executed", FakePreferenceStore({"bot_state_change": False}))
    ) is True
    assert asyncio.run(event_is_permitted("u-8", "trade_executed", FakePreferenceStore())) is True


def test_an_event_no_switch_governs_is_delivered() -> None:
    """Default-open for anything the panel never offered a control for.

    `order_failed` is the pointed case. The only trading-executions switch is for FILLS
    ("Real-time order fill alerts and position entries"), so a customer who muted fill
    confirmations has not asked to stop being told that an order was REFUSED. Pinned here
    because mapping it onto `trade_executed` is the obvious-looking mistake.
    """
    everything_off = {key: False for key in ("trade_executed", "bot_state_change",
                                             "daily_pnl_summary", "api_key_expiring",
                                             "backtest_complete")}
    client = FakePreferenceStore(everything_off)
    for event_type in ("order_failed", "deployment_failed", "exchange_disconnected",
                       "payment_succeeded", "support_ticket_resolved"):
        assert preference_key_for(event_type) is None
        assert asyncio.run(event_is_permitted("u-9", event_type, client)) is True


# ══════════════════════════════════════════════════════════════════════════
# PART 3 — each newly wired event dispatches once, and dedupes on redelivery
# ══════════════════════════════════════════════════════════════════════════


def test_a_permitted_event_dispatches_exactly_once() -> None:
    notification_id = _dispatch(
        user_id="u-once",
        event_type="trade_executed",
        category="trade",
        title="Fill",
        message="BUY 1 BTC/USDT",
        metadata={"idempotency_key": "order_filled:exec-once"},
        supabase=FakePreferenceStore({"trade_executed": True}),
    )
    assert notification_id
    assert len(_stored_for("u-once")) == 1


def test_redelivery_of_the_same_event_is_deduplicated() -> None:
    """The second dispatch returns the FIRST notification's id and stores nothing new.

    In this environment the dedupe is enforced by `_idempotency_index`; in production it is
    enforced by `uq_notifications_user_idempotency` (migration 071), which is the authority
    because the index dies on restart, cannot see another task's entries and evicts at
    5,000. `test_notif01_durable_dedupe_postgres.py` owns the durable half. What matters
    here is that every newly wired producer actually SENDS a key, which is the precondition
    for either mechanism to work at all.
    """
    args = dict(
        user_id="u-dedupe",
        event_type="trade_executed",
        category="trade",
        title="Fill",
        message="BUY 1 BTC/USDT",
        metadata={"idempotency_key": "order_filled:exec-dedupe"},
        supabase=FakePreferenceStore({"trade_executed": True}),
    )
    first = _dispatch(**args)
    second = _dispatch(**args)

    assert first and second
    assert first == second, "a redelivered event produced a second, different notification"
    assert len(_stored_for("u-dedupe")) == 1


def test_two_different_events_are_not_collapsed_together() -> None:
    """Dedupe must not be so coarse that it hides a second, genuine event."""
    for key in ("order_filled:exec-A", "order_filled:exec-B"):
        _dispatch(
            user_id="u-distinct",
            event_type="trade_executed",
            category="trade",
            title="Fill",
            message="BUY 1 BTC/USDT",
            metadata={"idempotency_key": key},
            supabase=FakePreferenceStore({"trade_executed": True}),
        )
    assert len(_stored_for("u-distinct")) == 2


# ── the order-outcome producer (fills and failures) ───────────────────────


def _bot_runner_stub(*, paper: bool = False):
    """A BotRunner with only the attributes `_notify_order_outcome` reads.

    Constructed by `__new__` rather than by calling `BotRunner(...)`: the real constructor
    builds a CCXT bridge, a data processor, a risk manager and a telemetry client, none of
    which this method touches. Instantiating them to test a notification would be testing
    the constructor.
    """
    from backend_app.backend.master_executor import BotRunner

    bot = BotRunner.__new__(BotRunner)
    bot.user_id = "u-bot"
    bot.symbol = "BTC/USDT"
    bot.exchange_id = "binance"
    bot.paper_trading = paper
    bot.blueprint = {"strategy_id": "strat-1"}
    return bot


def _captured_dispatches(monkeypatch) -> List[Dict[str, Any]]:
    """Capture what `schedule_user_notification` was asked to send.

    Patched at the dispatcher module, which is where the producers import it from, so this
    records the call the producer actually made rather than re-implementing the scheduling.
    """
    calls: List[Dict[str, Any]] = []

    def _capture(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(dispatcher, "schedule_user_notification", _capture)
    return calls


@pytest.mark.parametrize("status", ["completed", "partially_filled"])
def test_a_venue_confirmed_fill_dispatches_trade_executed(monkeypatch, status) -> None:
    calls = _captured_dispatches(monkeypatch)
    bot = _bot_runner_stub()
    bot._notify_order_outcome(
        status=status,
        side="buy",
        requested_qty=Decimal("1"),
        filled_qty=Decimal("1"),
        price=50000.0,
        execution_id="exec-fill-1",
    )
    assert len(calls) == 1
    call = calls[0]
    assert call["event_type"] == "trade_executed"
    assert call["category"] == "trade"
    assert call["user_id"] == "u-bot"
    assert call["metadata"]["idempotency_key"] == "order_filled:exec-fill-1"


@pytest.mark.parametrize("status", ["failed", "blocked", None, "something_unknown"])
def test_an_order_that_did_not_execute_dispatches_order_failed(monkeypatch, status) -> None:
    calls = _captured_dispatches(monkeypatch)
    bot = _bot_runner_stub()
    bot._notify_order_outcome(
        status=status,
        side="sell",
        requested_qty=Decimal("2"),
        filled_qty=Decimal("0"),
        price=50000.0,
        execution_id="exec-fail-1",
        message="insufficient balance",
    )
    assert len(calls) == 1
    assert calls[0]["event_type"] == "order_failed"
    assert calls[0]["severity"] == "error"
    assert calls[0]["metadata"]["idempotency_key"] == "order_failed:exec-fail-1"
    assert "insufficient balance" in calls[0]["message"]


@pytest.mark.parametrize("status", ["accepted", "skipped_completed", "skipped_executing"])
def test_a_resting_or_deduplicated_order_notifies_nothing(monkeypatch, status) -> None:
    """Neither a fill nor a failure, so neither notification.

    `accepted` means the venue holds the order and has filled nothing — telling the
    customer their trade executed would be false. `skipped_*` means the idempotency layer
    refused a replay because an identical execution already ran, so THIS call did nothing
    and the original already produced its own notification. Announcing either would be the
    fabricated alert the brief forbids.
    """
    calls = _captured_dispatches(monkeypatch)
    _bot_runner_stub()._notify_order_outcome(
        status=status,
        side="buy",
        requested_qty=Decimal("1"),
        filled_qty=Decimal("0"),
        price=50000.0,
        execution_id="exec-quiet-1",
    )
    assert calls == []


def test_a_fill_and_a_failure_on_one_execution_id_do_not_collide(monkeypatch) -> None:
    """The outcome is part of the key, not just the execution id.

    An intent reported failed and then, on a retry, filled is two different things to tell
    a customer. Keying on the execution id alone would de-duplicate the second away.
    """
    calls = _captured_dispatches(monkeypatch)
    bot = _bot_runner_stub()
    for status, filled in (("failed", Decimal("0")), ("completed", Decimal("1"))):
        bot._notify_order_outcome(
            status=status, side="buy", requested_qty=Decimal("1"), filled_qty=filled,
            price=1.0, execution_id="exec-same",
        )
    keys = [c["metadata"]["idempotency_key"] for c in calls]
    assert keys == ["order_failed:exec-same", "order_filled:exec-same"]


def test_a_paper_bots_fill_says_so(monkeypatch) -> None:
    """A simulated fill is announced and labelled, never passed off as live."""
    calls = _captured_dispatches(monkeypatch)
    _bot_runner_stub(paper=True)._notify_order_outcome(
        status="completed", side="buy", requested_qty=Decimal("1"),
        filled_qty=Decimal("1"), price=1.0, execution_id="exec-paper",
    )
    assert calls[0]["metadata"]["mode"] == "paper"
    assert "Paper" in calls[0]["title"]


def test_an_order_notification_failure_cannot_break_the_tick(monkeypatch, caplog) -> None:
    """Telling the customer must never propagate into the trading loop.

    `_notify_order_outcome` is called from inside the execution block whose `except`
    rolls exposure back. An exception escaping it would roll back a real, settled fill —
    the bot would then believe it holds nothing after buying, which is the RT-02 defect
    this loop already carries a comment about.
    """
    def _explode(**_kwargs):
        raise RuntimeError("scheduler is gone")

    monkeypatch.setattr(dispatcher, "schedule_user_notification", _explode)
    caplog.set_level(logging.ERROR)

    _bot_runner_stub()._notify_order_outcome(
        status="completed", side="buy", requested_qty=Decimal("1"),
        filled_qty=Decimal("1"), price=1.0, execution_id="exec-boom",
    )
    assert any("could not schedule" in r.getMessage() for r in caplog.records)


# ── the risk producer (loss limits and limit breaches) ────────────────────


def test_a_loss_protective_rule_is_announced_as_a_stop_loss() -> None:
    """The three rules that fire because money was lost carry `stop_loss_triggered`.

    They are the platform's stop-loss: each one locks new position opens until the
    condition clears. There is no ORDER-level stop — `deployment_runtime`'s
    `EXECUTABLE_ORDER_TYPES` is `("market",)` and nothing evaluates `stop_loss_pct` outside
    the backtester — so these are the only real stop-loss events the production path can
    produce, and the event is not fabricated from a backtest.
    """
    from backend_app.routers.risk import (STOP_LOSS_RULE_CODES,
                                          _risk_breach_notification)

    for rule in STOP_LOSS_RULE_CODES:
        shape = _risk_breach_notification(rule, "Daily loss $600.00 reached limit of $500.00")
        assert shape is not None
        assert shape["event_type"] == "stop_loss_triggered"
        assert shape["severity"] == "critical"
        assert is_mandatory_event(shape["event_type"])


def test_every_other_refusal_is_announced_as_a_risk_limit_breach() -> None:
    from backend_app.routers.risk import _risk_breach_notification

    for rule in ("MAX_POSITIONS_EXCEEDED", "SYMBOL_RESTRICTED",
                 "MAX_POSITION_SIZE_EXCEEDED", "CAPITAL_UTILIZATION_LIMIT_EXCEEDED",
                 "KILL_SWITCH_ACTIVE"):
        shape = _risk_breach_notification(rule, "a reason")
        assert shape is not None, f"{rule} tells the customer nothing"
        assert shape["event_type"] == "risk_limit_breached"
        # Inherits the floor through the mapping, so no preference can hide a breach.
        assert is_mandatory_event(shape["event_type"])


def test_the_kill_switch_endpoint_is_not_announced_twice() -> None:
    """`activate_kill_switch` dispatches its own `critical` notification.

    It also calls `record_risk_violation(uid, "KILL_SWITCH_MANUAL_ACTIVATION", ...)`. Both
    announcing it would send the customer two notifications for one act — a `critical` one
    naming the halt and a `warning` one naming the audit rule — which is noise, not
    coverage.
    """
    from backend_app.routers.risk import _risk_breach_notification

    assert _risk_breach_notification("KILL_SWITCH_MANUAL_ACTIVATION", "user halted") is None


def test_a_repeated_breach_collapses_onto_one_notification(monkeypatch) -> None:
    """A breached limit refuses EVERY attempt while it holds; the customer is told once.

    The paper path evaluates these rules per bar, which is why `_risk_violations_log`
    needed a 200-per-user cap. One notification per refusal would be a storm, so the
    idempotency key carries an hour bucket and the unique index collapses the rest.
    Asserted on the KEY rather than by counting deliveries, because the collapsing is the
    database's job and this is the input that lets it do it.
    """
    from backend_app.routers import risk as risk_router

    keys: List[str] = []

    def _capture(**kwargs):
        keys.append(kwargs["metadata"]["idempotency_key"])
        return True

    monkeypatch.setattr(dispatcher, "schedule_user_notification", _capture)

    async def _run():
        for _ in range(5):
            risk_router.record_risk_violation(
                "u-breach", "MAX_DAILY_LOSS_EXCEEDED", "Daily loss limit reached"
            )

    asyncio.run(_run())

    assert len(keys) == 5, "not every refusal attempted to notify"
    assert len(set(keys)) == 1, (
        f"five refusals of the same rule produced {len(set(keys))} distinct idempotency "
        "keys, so the unique index cannot collapse them and the customer gets a storm"
    )
    assert keys[0].startswith("risk_breach:MAX_DAILY_LOSS_EXCEEDED:")


def test_a_refusal_outside_an_event_loop_still_refuses() -> None:
    """`record_risk_violation` is called from synchronous code with no loop.

    A CLI or a sync test has nothing to schedule onto. The refusal, its audit mirror and
    its log line must all still happen; only the notification is skipped. Asserted because
    the alternative — raising, or calling `asyncio.run` and blocking the per-bar path — is
    the obvious-looking implementation.
    """
    from backend_app.routers import risk as risk_router

    before = len(risk_router._risk_violations_log.get("u-noloop", []))
    risk_router.record_risk_violation("u-noloop", "MAX_POSITIONS_EXCEEDED", "too many")
    after = risk_router._risk_violations_log.get("u-noloop", [])
    assert len(after) == before + 1
    assert after[-1]["rule"] == "MAX_POSITIONS_EXCEEDED"


def test_schedule_user_notification_reports_rather_than_raising_with_no_loop(caplog) -> None:
    """The sync entry point is total, and an actionable severity says so at ERROR."""
    caplog.set_level(logging.ERROR, logger="backend_app.core.notification_dispatcher")
    scheduled = dispatcher.schedule_user_notification(
        user_id="u-noloop",
        event_type="risk_limit_breached",
        category="risk",
        title="Risk Limit Breached",
        message="an order was refused",
        severity="warning",
    )
    assert scheduled is False
    assert any("was NOT scheduled" in r.getMessage() for r in caplog.records)


def test_schedule_user_notification_hands_the_coroutine_to_the_tracked_helper() -> None:
    """It must use `fire_and_forget_task`, not a bare `create_task`.

    A bare `create_task` keeps no reference, so the task is collectable before it runs —
    one of the two defects in the `dispatch_user_notification_sync` this replaced. The
    platform's tracked helper holds a strong reference and logs any exception, and reusing
    it is the point: `routers/risk.record_risk_violation` and
    `backend/builder_alerts.raise_alerts` already schedule work this way.
    """
    delivered: List[str] = []

    async def _run():
        async def _fake_dispatch(**kwargs):
            delivered.append(kwargs["event_type"])
            return "notif_fake"

        original = dispatcher.dispatch_user_notification
        dispatcher.dispatch_user_notification = _fake_dispatch
        try:
            assert dispatcher.schedule_user_notification(
                user_id="u-sched",
                event_type="risk_limit_breached",
                category="risk",
                title="t",
                message="m",
                severity="warning",
            ) is True
            # Yield so the scheduled task runs before the loop closes.
            await asyncio.sleep(0)
        finally:
            dispatcher.dispatch_user_notification = original

    asyncio.run(_run())
    assert delivered == ["risk_limit_breached"]


# ── the remaining producers, asserted on their dispatched shape ───────────


def test_the_login_notification_is_keyed_per_session() -> None:
    """Two sign-ins are two notifications; one sign-in reported twice is one.

    Keyed on a SHA-256 prefix of the access token, which is one per session. Not the user
    id and a timestamp: two sign-ins inside the same second would collide and the second
    one — the interesting one, if the first was the attacker's — would be de-duplicated
    away. The token itself is never stored.
    """
    from backend_app.routers.auth import _login_dedupe_token

    a = _login_dedupe_token("access-token-session-A")
    b = _login_dedupe_token("access-token-session-B")
    assert a != b
    assert a == _login_dedupe_token("access-token-session-A")
    assert "access-token-session-A" not in a
    assert len(a) == 32


def test_the_login_notification_is_dispatched_and_is_unsuppressible(monkeypatch) -> None:
    from backend_app.routers import auth as auth_router

    calls: List[Dict[str, Any]] = []

    async def _capture(**kwargs):
        calls.append(kwargs)
        return "notif_login"

    monkeypatch.setattr(dispatcher, "dispatch_user_notification", _capture)
    asyncio.run(auth_router._notify_new_login("u-login", "a@example.invalid", "tok-1"))

    assert len(calls) == 1
    assert calls[0]["event_type"] == "new_login_detected"
    assert calls[0]["category"] == "security"
    assert is_mandatory_event("new_login_detected")
    assert calls[0]["metadata"]["idempotency_key"].startswith("new_login:")
    # The credential must not travel into a notification row.
    assert "tok-1" not in str(calls[0]["metadata"])


def test_a_failed_login_notification_cannot_fail_the_sign_in(monkeypatch, caplog) -> None:
    """A customer must never be unable to sign in because the alert could not be sent."""
    from backend_app.routers import auth as auth_router

    async def _explode(**_kwargs):
        raise RuntimeError("notification store is gone")

    monkeypatch.setattr(dispatcher, "dispatch_user_notification", _explode)
    caplog.set_level(logging.ERROR)

    # Returns normally rather than raising — that is the whole assertion.
    asyncio.run(auth_router._notify_new_login("u-login", "a@example.invalid", "tok-2"))
    assert any("new_login_detected" in r.getMessage() for r in caplog.records)
