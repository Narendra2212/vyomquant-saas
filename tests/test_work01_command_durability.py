"""WORK-01 / WORK-02 regression: a command the worker could not run is not thrown away.

THE DEFECT
----------
``backend_app/workers/command_worker.py`` acknowledged every message from a ``finally:``
block::

    try:
        ... dispatch start_bot / stop_bot / kill_all ...
    except Exception as e:
        logger.exception("Worker failed: %s", e)
    finally:
        await redis_manager.xack(stream_name, "command_workers", msg_id)

XACK removes an entry from the consumer group's Pending Entries List, and the PEL is the
only record that an entry was delivered but not completed. So a command that *raised*
was indistinguishable from one that succeeded, and it was gone: a ``stop_bot`` that hit a
transient error left the bot trading with nothing anywhere recording that the stop had
been requested. ``backend/reconciliation_worker.py`` does not reconcile the fleet against
``strategy_deployments``, so no second path recovered it.

WORK-02 was the same wound from the other side: nothing ever called XAUTOCLAIM, so an
entry whose consumer died *before* acknowledging stayed pending forever; and the consumer
name defaulted to one shared string, so replicas shared a PEL and could acknowledge each
other's in-flight work.

HOW THIS FILE TESTS IT
----------------------
With a fake Redis that actually implements the stream semantics that matter -- delivery
moves an entry into a per-consumer PEL, XACK removes it, XAUTOCLAIM transfers entries
idle beyond a threshold. The assertions are then about *where the entry is*, which is the
real property, rather than about the text of the module.

Section 6 is a negative control: it reconstructs the old ``finally:`` shape against the
same fake and shows the PEL ends up empty. Without it, every "the entry is still
pending" assertion here could be passing for the wrong reason.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.workers import command_worker as cw  # noqa: E402
from backend_app.workers.command_worker import (  # noqa: E402
    CONSUMER_GROUP,
    COMMAND_STREAM,
    DEAD_LETTER_STREAM,
    MAX_DELIVERIES,
    MIN_RECLAIM_IDLE_SECONDS,
    CommandWorker,
    Outcome,
    _default_consumer_name,
)


# ── a Redis fake with real PEL semantics ─────────────────────────────────────


class FakeStreamRedis:
    """Enough of Redis Streams to make "was it acknowledged?" a meaningful question.

    Models the one invariant the fix turns on: an entry delivered by XREADGROUP sits in a
    per-consumer Pending Entries List until XACKed, and only XAUTOCLAIM can move it to
    another consumer. A fake that acknowledged implicitly, or had no PEL at all, would
    pass every test here no matter what the worker did.
    """

    def __init__(self) -> None:
        self.streams: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
        self.next_seq = 0
        # group -> {msg_id: {"consumer": str, "idle_ms": int, "fields": dict}}
        self.pel: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.delivered: Dict[str, set] = {}
        self.counters: Dict[str, int] = {}
        self.expirations: Dict[str, int] = {}
        self.ack_log: List[str] = []
        self.xadd_failures: set = set()
        self.connected = False

    # -- plumbing the worker touches -------------------------------------
    @property
    def pool(self):
        return self

    async def connect(self):
        self.connected = True

    async def xgroup_create(self, stream, group, id="$", mkstream=False):
        self.pel.setdefault(group, {})
        self.delivered.setdefault(group, set())
        self.streams.setdefault(stream, [])
        return True

    # -- stream writes ----------------------------------------------------
    async def xadd(self, stream: str, payload: dict, **kwargs):
        if stream in self.xadd_failures:
            raise RuntimeError(f"simulated XADD failure on {stream}")
        self.next_seq += 1
        entry_id = f"1-{self.next_seq}"
        self.streams.setdefault(stream, []).append((entry_id, dict(payload)))
        return entry_id

    # -- consumer group reads --------------------------------------------
    async def xreadgroup(self, group: str, consumer: str, streams: dict, count: int = 10,
                         block: int = 1000):
        out = []
        for stream, cursor in streams.items():
            if cursor != ">":
                continue
            seen = self.delivered.setdefault(group, set())
            fresh = [(i, f) for i, f in self.streams.get(stream, []) if i not in seen]
            fresh = fresh[:count]
            if not fresh:
                continue
            for entry_id, fields in fresh:
                seen.add(entry_id)
                self.pel.setdefault(group, {})[entry_id] = {
                    "consumer": consumer, "idle_ms": 0, "fields": fields,
                }
            out.append((stream, fresh))
        return out

    async def xack(self, stream: str, group: str, *ids: str):
        removed = 0
        for entry_id in ids:
            if self.pel.get(group, {}).pop(entry_id, None) is not None:
                removed += 1
                self.ack_log.append(entry_id)
        return removed

    async def xautoclaim(self, stream, group, consumer, min_idle_time, start_id="0-0",
                         count=10):
        claimed = []
        for entry_id, meta in sorted(self.pel.get(group, {}).items()):
            if meta["idle_ms"] >= min_idle_time and meta["consumer"] != consumer:
                meta["consumer"] = consumer
                meta["idle_ms"] = 0
                claimed.append((entry_id, meta["fields"]))
            if len(claimed) >= count:
                break
        return ("0-0", claimed, [])

    async def xpending(self, stream, group):
        return {"pending": len(self.pel.get(group, {}))}

    # -- counters ---------------------------------------------------------
    async def incr(self, key: str, amount: int = 1) -> int:
        self.counters[key] = self.counters.get(key, 0) + amount
        return self.counters[key]

    async def expire(self, key: str, time: int, *args, **kwargs) -> bool:
        self.expirations[key] = time
        return True

    async def delete(self, *keys: str):
        for key in keys:
            self.counters.pop(key, None)
        return len(keys)

    # -- test helpers -----------------------------------------------------
    def age_pending(self, by_ms: int) -> None:
        """Advance every pending entry's idle time, simulating a dead consumer."""
        for meta in self.pel.get(CONSUMER_GROUP, {}).values():
            meta["idle_ms"] += by_ms

    def pending_ids(self) -> List[str]:
        return sorted(self.pel.get(CONSUMER_GROUP, {}))

    def dead_letters(self) -> List[Tuple[str, Dict[str, Any]]]:
        return list(self.streams.get(DEAD_LETTER_STREAM, []))


class FakeFleet:
    """Records what the worker asked the fleet to do, and can be told to fail."""

    def __init__(self, fail_with: Optional[BaseException] = None) -> None:
        self.started: List[dict] = []
        self.stopped: List[tuple] = []
        self.shutdowns = 0
        self.fail_with = fail_with

    async def start_bot(self, user_id, symbol, blueprint, *, paper_trading):
        if self.fail_with:
            raise self.fail_with
        self.started.append(
            {"user_id": user_id, "symbol": symbol, "paper_trading": paper_trading}
        )
        return True, symbol

    async def stop_bot(self, user_id, symbol):
        if self.fail_with:
            raise self.fail_with
        self.stopped.append((user_id, symbol))
        return True, "stopped"

    async def shutdown_all(self):
        if self.fail_with:
            raise self.fail_with
        self.shutdowns += 1


@pytest.fixture
def harness(monkeypatch):
    """A worker wired to the fake Redis and a fake fleet, with an active subscription."""
    fake = FakeStreamRedis()
    fleet = FakeFleet()

    monkeypatch.setattr(cw, "redis_manager", fake)

    class _AppState:
        pass

    state = _AppState()
    state.fleet = fleet
    monkeypatch.setattr(cw, "app_state", state)

    import backend_app.core.subscription_middleware as sm

    async def _active(_user_id):
        return {"status": "active"}

    monkeypatch.setattr(sm, "get_user_subscription", _active)

    worker = CommandWorker(worker_id="test-consumer-a")
    asyncio.get_event_loop_policy()
    return worker, fake, fleet


def _run(coro):
    return asyncio.run(coro)


def _publish(fake: FakeStreamRedis, action: str, payload: str) -> str:
    fake.pel.setdefault(CONSUMER_GROUP, {})
    fake.delivered.setdefault(CONSUMER_GROUP, set())
    import json as _json
    entry_id = f"1-{len(fake.streams.get(COMMAND_STREAM, [])) + 1000}"
    fake.streams.setdefault(COMMAND_STREAM, []).append(
        (entry_id, {"action": action, "payload": payload})
    )
    return entry_id


_GOOD_START = '{"user_id":"u1","symbol":"BTC/USDT","blueprint":{},"paper_trading":true}'


# ── 1. the acknowledgement decision ─────────────────────────────────────────


def test_a_successful_command_is_acknowledged(harness):
    worker, fake, fleet = harness
    entry_id = _publish(fake, "start_bot", _GOOD_START)

    _run(worker.process_iteration())

    assert fleet.started, "the command never reached the fleet"
    assert entry_id in fake.ack_log
    assert fake.pending_ids() == [], (
        "a completed command must leave the PEL, or the reaper will run it again"
    )
    assert worker.commands_done == 1


def test_a_failed_command_is_NOT_acknowledged(harness):
    """THE WORK-01 ASSERTION.

    The fleet raises, so the command did not happen. The entry must stay pending -- that
    is the only durable record that the work is outstanding.
    """
    worker, fake, fleet = harness
    fleet.fail_with = RuntimeError("supabase timeout")
    entry_id = _publish(fake, "stop_bot", '{"user_id":"u1","symbol":"BTC/USDT"}')

    _run(worker.process_iteration())

    assert entry_id not in fake.ack_log, (
        "the failed command was ACKed. An ACKed entry leaves the PEL and is never "
        "redelivered, so the stop request is gone -- WORK-01 is open."
    )
    assert fake.pending_ids() == [entry_id]
    assert worker.commands_failed == 1
    assert worker.commands_done == 0


def test_a_refused_command_is_acknowledged_not_retried_forever(harness, monkeypatch):
    """An entitlement verdict is terminal, so it must not accumulate in the PEL.

    This is the distinction the original `finally:` had no way to express: refusing is a
    decision, failing is an accident, and only one of them should be retried.
    """
    worker, fake, fleet = harness
    import backend_app.core.subscription_middleware as sm

    async def _cancelled(_user_id):
        return {"status": "cancelled"}

    monkeypatch.setattr(sm, "get_user_subscription", _cancelled)
    entry_id = _publish(fake, "start_bot", _GOOD_START)

    _run(worker.process_iteration())

    assert fleet.started == [], "a cancelled subscriber's bot was started"
    assert entry_id in fake.ack_log
    assert fake.pending_ids() == []
    assert worker.commands_refused == 1


def test_an_unparseable_command_is_refused_rather_than_retried(harness):
    worker, fake, _fleet = harness
    entry_id = _publish(fake, "start_bot", "{not json")

    _run(worker.process_iteration())

    assert entry_id in fake.ack_log
    assert worker.commands_refused == 1


def test_an_unknown_action_is_refused(harness):
    worker, fake, fleet = harness
    entry_id = _publish(fake, "launch_rocket", "{}")

    _run(worker.process_iteration())

    assert entry_id in fake.ack_log
    assert fleet.started == [] and fleet.stopped == []


def test_a_start_bot_without_a_declared_mode_is_refused(harness):
    """The mode refusal must survive the durability refactor.

    Guarded independently by tests/test_live_deployment_mode_propagation.py; asserted
    here behaviourally, because that one reads the source.
    """
    worker, fake, fleet = harness
    entry_id = _publish(
        fake, "start_bot", '{"user_id":"u1","symbol":"BTC/USDT","blueprint":{}}'
    )

    _run(worker.process_iteration())

    assert fleet.started == [], "a bot was started in an unestablished mode"
    assert entry_id in fake.ack_log


def test_a_command_missing_a_required_field_is_refused(harness):
    worker, fake, fleet = harness
    entry_id = _publish(fake, "stop_bot", '{"user_id":"u1"}')  # no symbol

    _run(worker.process_iteration())

    assert fleet.stopped == []
    assert entry_id in fake.ack_log, (
        "a structurally broken command should be terminal, not retried to exhaustion"
    )


# ── 2. the reaper brings failures back ──────────────────────────────────────


def test_an_abandoned_command_is_reclaimed_and_runs(harness):
    """WORK-02. Without XAUTOCLAIM a pending entry is never delivered again."""
    worker, fake, fleet = harness
    fleet.fail_with = RuntimeError("transient")
    entry_id = _publish(fake, "stop_bot", '{"user_id":"u1","symbol":"BTC/USDT"}')

    _run(worker.process_iteration())
    assert fake.pending_ids() == [entry_id]

    # The consumer that holds it goes away and the entry goes idle.
    fake.pel[CONSUMER_GROUP][entry_id]["consumer"] = "dead-consumer"
    fake.age_pending(int(MIN_RECLAIM_IDLE_SECONDS * 1000) + 1)
    fleet.fail_with = None

    _run(worker.process_iteration())

    assert fleet.stopped == [("u1", "BTC/USDT")], (
        "the reclaimed command never ran; a crashed consumer's work is still stranded"
    )
    assert entry_id in fake.ack_log
    assert worker.commands_reclaimed == 1


def test_a_command_still_in_flight_is_not_stolen(harness):
    """The idle threshold must not reclaim work a live consumer is mid-way through."""
    worker, fake, fleet = harness
    fleet.fail_with = RuntimeError("transient")
    entry_id = _publish(fake, "stop_bot", '{"user_id":"u1","symbol":"BTC/USDT"}')
    _run(worker.process_iteration())

    fake.pel[CONSUMER_GROUP][entry_id]["consumer"] = "another-live-consumer"
    fake.age_pending(1000)  # 1s: far below the floor
    fleet.fail_with = None

    _run(worker.process_iteration())

    assert fleet.stopped == [], (
        "an entry idle for only 1s was reclaimed; the reaper is stealing in-flight work"
    )
    assert worker.commands_reclaimed == 0


def test_the_reclaim_threshold_cannot_drop_below_the_floor():
    """A fast poll interval must not shrink the threshold into the manufacture-failure zone."""
    worker = CommandWorker(worker_id="x")
    worker.poll_interval = 0.01
    assert worker.reclaim_idle_ms >= MIN_RECLAIM_IDLE_SECONDS * 1000

    worker.poll_interval = 60.0
    assert worker.reclaim_idle_ms > MIN_RECLAIM_IDLE_SECONDS * 1000, (
        "a long poll interval should widen the threshold, not be clamped to the floor"
    )


# ── 3. retries are bounded and exhaustion is durable ────────────────────────


def test_a_poison_command_is_dead_lettered_not_retried_forever(harness):
    worker, fake, fleet = harness
    fleet.fail_with = RuntimeError("always broken")
    entry_id = _publish(fake, "stop_bot", '{"user_id":"u1","symbol":"BTC/USDT"}')

    for _ in range(MAX_DELIVERIES):
        _run(worker.process_iteration())
        if fake.pending_ids():
            fake.pel[CONSUMER_GROUP][entry_id]["consumer"] = "dead"
            fake.age_pending(int(MIN_RECLAIM_IDLE_SECONDS * 1000) + 1)

    # One more pass takes the attempt count past the budget.
    _run(worker.process_iteration())

    parked = fake.dead_letters()
    assert parked, (
        f"after {MAX_DELIVERIES}+ attempts the command was neither run nor parked; it "
        "would be retried forever"
    )
    assert entry_id in fake.ack_log, (
        "a dead-lettered command must also be ACKed, or the PEL grows without bound"
    )
    assert worker.commands_dead_lettered == 1


def test_the_dead_letter_entry_carries_enough_to_replay_and_diagnose(harness):
    worker, fake, fleet = harness
    fleet.fail_with = RuntimeError("always broken")
    _publish(fake, "stop_bot", '{"user_id":"u1","symbol":"BTC/USDT"}')

    for _ in range(MAX_DELIVERIES + 1):
        _run(worker.process_iteration())
        for meta in fake.pel.get(CONSUMER_GROUP, {}).values():
            meta["consumer"] = "dead"
        fake.age_pending(int(MIN_RECLAIM_IDLE_SECONDS * 1000) + 1)

    assert fake.dead_letters(), "nothing was dead-lettered"
    _entry_id, fields = fake.dead_letters()[0]
    # Replayable: the original command survives verbatim.
    assert fields["action"] == "stop_bot"
    assert '"symbol"' in fields["payload"]
    # Diagnosable: why, how many times, from where, by whom.
    for key in (
        "dead_letter_reason",
        "dead_letter_attempts",
        "dead_letter_source_id",
        "dead_letter_consumer",
    ):
        assert key in fields, f"the dead-letter entry omits {key}"


def test_a_failed_dead_letter_write_does_not_drop_the_command(harness):
    """If the parking space is unavailable, the entry must stay pending.

    ACKing here would be the original defect wearing a different hat: the command would
    be gone and there would be no record anywhere that it had ever been asked for.
    """
    worker, fake, fleet = harness
    fleet.fail_with = RuntimeError("always broken")
    fake.xadd_failures.add(DEAD_LETTER_STREAM)
    entry_id = _publish(fake, "stop_bot", '{"user_id":"u1","symbol":"BTC/USDT"}')

    for _ in range(MAX_DELIVERIES + 2):
        _run(worker.process_iteration())
        for meta in fake.pel.get(CONSUMER_GROUP, {}).values():
            meta["consumer"] = "dead"
        fake.age_pending(int(MIN_RECLAIM_IDLE_SECONDS * 1000) + 1)

    assert fake.dead_letters() == []
    assert entry_id not in fake.ack_log, (
        "the command was ACKed even though it could not be parked; it is now lost"
    )
    assert entry_id in fake.pending_ids()


def test_the_attempt_counter_is_cleared_once_a_command_settles(harness):
    """So a replayed entry id does not inherit a spent budget."""
    worker, fake, _fleet = harness
    _publish(fake, "start_bot", _GOOD_START)

    _run(worker.process_iteration())

    assert not [k for k in fake.counters if k.startswith("command:attempts:")], (
        f"attempt counters left behind: {sorted(fake.counters)}"
    )


def test_the_attempt_counter_survives_a_different_consumer(harness):
    """The budget is per command, not per consumer -- it lives in Redis for that reason."""
    worker, fake, fleet = harness
    fleet.fail_with = RuntimeError("broken")
    entry_id = _publish(fake, "stop_bot", '{"user_id":"u1","symbol":"BTC/USDT"}')

    _run(worker.process_iteration())
    assert fake.counters[f"command:attempts:{entry_id}"] == 1

    fake.pel[CONSUMER_GROUP][entry_id]["consumer"] = "dead"
    fake.age_pending(int(MIN_RECLAIM_IDLE_SECONDS * 1000) + 1)
    _run(worker.process_iteration())

    assert fake.counters[f"command:attempts:{entry_id}"] == 2, (
        "a reclaim by another consumer reset the retry budget; a crash-looping command "
        "would never reach the dead-letter stream"
    )


# ── 4. the consumer name ────────────────────────────────────────────────────


def test_two_replicas_do_not_share_a_consumer_name():
    """WORK-02. A shared name means a shared PEL, and replicas ACKing each other's work."""
    assert "command_worker_1" != _default_consumer_name()
    name = _default_consumer_name()
    assert str(os.getpid()) in name, (
        f"{name!r} does not identify this process; two replicas would collide"
    )


def test_WORKER_NAME_is_still_honoured_but_not_shared(monkeypatch):
    """An operator can still pin the health-key prefix without recreating the collision."""
    monkeypatch.setenv("WORKER_NAME", "command_worker_1")
    name = _default_consumer_name()
    assert name.startswith("command_worker_1")
    assert name != "command_worker_1", (
        "WORKER_NAME was adopted verbatim; every replica sharing the task definition "
        "would share one PEL again"
    )
    assert str(os.getpid()) in name


def test_an_explicit_worker_id_still_wins():
    assert CommandWorker(worker_id="explicit").worker_id == "explicit"


# ── 5. observability of the new durability state ────────────────────────────


def test_health_details_exposes_the_dead_letter_backlog(harness):
    worker, _fake, _fleet = harness
    details = _run(worker.health_details())

    assert details["dead_letter_stream"] == DEAD_LETTER_STREAM
    assert details["max_deliveries"] == MAX_DELIVERIES
    for counter in (
        "commands_done",
        "commands_refused",
        "commands_failed",
        "commands_dead_lettered",
        "commands_reclaimed",
    ):
        assert counter in details
    assert details["pending_summary"] is not None, (
        "a dead-letter stream nobody can see the depth of is not actionable"
    )


# ── 6. negative control: the old shape would have failed these ──────────────


def test_the_old_ack_in_finally_shape_would_have_dropped_the_failure():
    """Proof that section 1's assertions can actually fail.

    Every "the entry is still pending" assertion above is only meaningful if the fake
    would show an *empty* PEL for the pre-fix code. This reconstructs that code against
    the same fake. If this test ever starts failing, the fake has stopped modelling
    acknowledgement and the rest of this file is vacuous.
    """
    fake = FakeStreamRedis()
    _run(fake.xgroup_create(COMMAND_STREAM, CONSUMER_GROUP))
    entry_id = _publish(fake, "stop_bot", '{"user_id":"u1","symbol":"BTC/USDT"}')

    async def pre_fix_process_iteration():
        batches = await fake.xreadgroup(
            group=CONSUMER_GROUP, consumer="old", streams={COMMAND_STREAM: ">"}
        )
        for stream_name, messages in batches:
            for msg_id, _fields in messages:
                try:
                    raise RuntimeError("supabase timeout")
                except Exception:
                    pass
                finally:
                    await fake.xack(stream_name, CONSUMER_GROUP, msg_id)

    _run(pre_fix_process_iteration())

    assert entry_id in fake.ack_log
    assert fake.pending_ids() == [], (
        "the fake does not model XACK removing an entry from the PEL, so the "
        "durability assertions in this file prove nothing"
    )


def test_the_fake_only_delivers_an_entry_once_without_a_reclaim():
    """The other half of the control: redelivery must require XAUTOCLAIM.

    If the fake re-delivered pending entries on a plain `>` read, the reaper tests would
    pass with no reaper at all.
    """
    fake = FakeStreamRedis()
    _run(fake.xgroup_create(COMMAND_STREAM, CONSUMER_GROUP))
    _publish(fake, "kill_all", "{}")

    first = _run(fake.xreadgroup(group=CONSUMER_GROUP, consumer="c1",
                                 streams={COMMAND_STREAM: ">"}))
    second = _run(fake.xreadgroup(group=CONSUMER_GROUP, consumer="c1",
                                  streams={COMMAND_STREAM: ">"}))

    assert first and first[0][1], "the first read delivered nothing"
    assert second == [], "a pending entry was redelivered by a plain '>' read"


def test_outcome_distinguishes_refusal_from_failure():
    """The type-level statement of the distinction the fix rests on."""
    assert Outcome.DONE is not Outcome.REFUSED
    assert Outcome.REFUSED is not Outcome.FAILED
    assert len({Outcome.DONE, Outcome.REFUSED, Outcome.FAILED}) == 3
