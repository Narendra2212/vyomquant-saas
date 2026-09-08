"""
tests/test_paper_market_feed_selection.py - tasks 24.1 and 24.2, and nothing else.

Spec: marketplace-subscriptions-paper-trading tasks 24.1 (source selection and its two
refusals) and 24.2 (the mds handshake, the transport observation and the delivery latency).
Requirements 14.1, 14.2, 14.3, 14.4, 14.6, 14.8, 14.10, 26.6, 28.3.

WHAT EACH CASE IS FOR, AND WHY IT CANNOT PASS FOR THE WRONG REASON
------------------------------------------------------------------
Every case below was run twice: once against the module as it stands, and once against the
module with the single guard it is about removed or inverted. The second run must fail. The
notes on each class record what was removed and what the failure was, because a guard test
that passes with the guard absent tests nothing.

Three of those "cannot pass for the wrong reason" decisions are structural rather than
incidental, and they are the reason this file is shaped the way it is:

* **The selection rule is driven for real.** ``choose_market_data_source`` is never stubbed.
  A stub would let the floor be skipped silently, which is precisely the failure the floor
  exists to prevent: an unmeasured candidate has to be refused *inside*
  ``evaluate_correctness_floor``, before a p99 is ever compared, and that is only observable
  if the real function runs. :class:`TestAnUnmeasuredSourceCannotBeSelected` therefore also
  asserts the identity of the function the feed holds.

* **The DEV_MODE mock interface is installed by the real installer.**
  ``ConnectionEngine._apply_mock_interface`` is invoked and *its* output is handed to
  ``open_feed`` - the approach ``tests/test_asset_discovery.py`` established for the same
  guard. A hand-rolled ``SimpleNamespace(load_markets=...)`` would keep passing after that
  method were renamed, and the guard would be disarmed with every test still green.

* **The refusals are asserted by absence, in order.** "The session stays ``CREATED`` and no
  subscription is opened" is a statement about what did *not* happen, so the Redis double
  records every interaction in one ordered log and the assertion is that the log is empty
  when the refusal is raised. A refusal that published first and raised second satisfies
  "an exception was raised" and fails this file.

WHY ``_run_coroutine`` AND NOT ``asyncio.run``
----------------------------------------------
Lifted from ``tests/test_settlement_service.py`` for the reason recorded there:
``asyncio.run`` closes the loop and leaves the thread with no event loop at all, which breaks
any later test in the same process that reaches for one. This runner restores what it found.

THE DOUBLES
-----------
The Persistence_Layer double is ``tests/test_paper_repository.FakeSupabase`` - the one paper
double this repository has, per ``tests/paper_seed.py``'s note - which gained the three
session-scoped tables 009 declares (``paper_sessions``, ``paper_events``,
``paper_market_events``) and their three unique constraints for this task rather than being
duplicated here.

The Redis double is local, and it is local because there is no existing one to reuse: the
publish/pubsub surface ``open_feed`` speaks to appears in no other double in ``tests/``
(``tests/crash_recovery/harness.RecordingRedis`` and the ``_RecordingRedis`` of tasks 9.1 and
10.2 are key/value stores with no ``publish`` and no ``pubsub``).
"""

from __future__ import annotations

import asyncio
import ast
import inspect
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

from backend_app.backend import asset_universe as au
from backend_app.backend import market_data_latency as mdl
from backend_app.backend.marketplace.errors import HTTP_STATUS_FOR_CODE
from backend_app.backend.paper import paper_market_feed as feed
from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper.errors import PAPER_MARKET_DATA_UNAVAILABLE
from tests.test_paper_repository import FakeSupabase

USER = "11111111-1111-1111-1111-111111111111"
OTHER_USER = "99999999-9999-9999-9999-999999999999"
SESSION = "22222222-2222-2222-2222-222222222222"
EXCHANGE = "binance"
SYMBOL = "BTC/USDT"
TIMEFRAME = "1m"

#: The channel ``mds/main.py::broadcast_ohlcv`` publishes this pair's candles to, spelled as a
#: literal rather than built from the module under test - the point of asserting a wire
#: contract is that both ends were written down independently and agree.
DATA_CHANNEL = "mds:data:binance:BTC/USDT"

#: A fixed processing instant, so the latency of Requirement 14.10 is an exact figure rather
#: than a range. See :class:`TestTheLatencyIsProcessingTimeMinusTheEventTimestamp`.
PROCESSED_AT = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

#: The same instant as integer epoch milliseconds - the unit ``mds/main.py`` publishes, since
#: it forwards CCXT's ``latest[0]``. Every candle timestamp below is derived from this by
#: subtracting a whole number of milliseconds, because a candle instant that carried a
#: fractional millisecond could not be represented on the wire and the expected latency would
#: then depend on how the truncation fell rather than on the arithmetic under test.
PROCESSED_AT_MS = int(PROCESSED_AT.timestamp()) * 1000


def _ms_before(milliseconds: int) -> int:
    """A candle open time this many milliseconds before :data:`PROCESSED_AT`, in epoch ms."""
    return PROCESSED_AT_MS - int(milliseconds)


# ══════════════════════════════════════════════════════════════════════════
# The loop runner (see the module docstring)
# ══════════════════════════════════════════════════════════════════════════


def _run_coroutine(coro: Any) -> Any:
    """Run ``coro`` to completion **without leaving the thread without an event loop**."""
    previous: Optional[asyncio.AbstractEventLoop]
    try:
        previous = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        previous = None

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        if previous is not None and not previous.is_closed():
            asyncio.set_event_loop(previous)
        else:
            asyncio.set_event_loop(asyncio.new_event_loop())


# ══════════════════════════════════════════════════════════════════════════
# The Redis double: publish, pubsub, and an ordered log of every interaction
# ══════════════════════════════════════════════════════════════════════════


class _PubSub:
    """One subscription. ``listen()`` hands out the frames the test queued, in order.

    The frames are queued rather than generated, because what a candle looks like on this
    channel is ``mds/main.py::broadcast_ohlcv``'s payload and a test that invented its own
    shape would be asserting against itself.

    When the queue empties the iterator **ends**, which ``FeedHandle._receive`` reads as the
    dropped subscription of Requirement 14.5. No case here consumes past its queue; a case
    that did would take the disconnection branch, which belongs to task 24.4.
    """

    def __init__(self, redis: "RecordingRedis") -> None:
        self.redis = redis
        self.channels: List[str] = []

    async def subscribe(self, channel: str) -> None:
        self.channels.append(channel)
        self.redis.subscribed.append(channel)
        self.redis.log.append(("subscribe", channel))

    async def unsubscribe(self, *channels: str) -> None:
        self.redis.log.append(("unsubscribe", ",".join(channels)))

    async def close(self) -> None:
        self.redis.log.append(("close", ""))

    def listen(self) -> Any:
        async def _frames() -> Any:
            # The confirmation frame a real pubsub sends first. It is here so the module's
            # "skip the protocol frames" branch is exercised rather than assumed: a candle
            # counter that counted this one would be counting two unrelated facts.
            yield {"type": "subscribe", "channel": self.channels[0], "data": 1}
            while self.redis.frames:
                yield self.redis.frames.pop(0)

        return _frames()


class RecordingRedis:
    """``publish`` and ``pubsub`` - the whole surface ``open_feed`` uses - plus one log.

    :attr:`log` is the point. It records every interaction in the order it happened, so
    "nothing was published and nothing was subscribed" is assertable as an empty list rather
    than as two separate absences, and an implementation that published before it refused
    fails on the log even though it also raised.
    """

    def __init__(self, frames: Optional[List[Any]] = None) -> None:
        self.published: List[Tuple[str, Any]] = []
        self.subscribed: List[str] = []
        self.pubsub_calls = 0
        self.log: List[Tuple[str, Any]] = []
        #: The frames ``listen()`` hands out, in order.
        self.frames: List[Any] = list(frames or [])

    async def publish(self, channel: str, message: Any) -> int:
        self.published.append((channel, message))
        self.log.append(("publish", channel))
        return 1

    def pubsub(self) -> _PubSub:
        self.pubsub_calls += 1
        self.log.append(("pubsub", ""))
        return _PubSub(self)

    # -- assertion helpers -------------------------------------------------
    def commands(self) -> List[Dict[str, Any]]:
        """Every ``mds:commands`` message, decoded."""
        return [
            json.loads(message)
            for channel, message in self.published
            if channel == feed.MDS_COMMAND_CHANNEL
        ]


class _RecordingAuditLogger:
    """``StrategyAuditLogger.log``'s signature, recording instead of writing."""

    def __init__(self) -> None:
        self.records: List[Dict[str, Any]] = []

    async def log(self, action: Any, **fields: Any) -> Dict[str, Any]:
        record = {"action": action, **fields}
        self.records.append(record)
        return record


class _RecordingMetrics:
    """The seven ``record_paper_feed_*`` calls this path makes, recorded.

    Only the methods ``paper_market_feed`` actually calls: a double carrying the whole
    collector's surface would hide a call this path started making. The four the 24.1/24.2
    path reaches were here first; ``record_paper_feed_duplicate``,
    ``record_paper_feed_out_of_order`` and ``record_paper_feed_reconnect`` were added when
    tasks 24.3 and 24.4 started making them, and they are added *here* rather than in a
    second recorder because ``tests/test_paper_market_feed_events.py`` imports this one -
    two metric doubles would be two accounts of what the module emits.

    ``record_db_statement`` joined them for the same reason: the fixture below replaces the
    whole ``metrics_collector`` attribute, and the feed persists its selection through
    ``paper_repository.update_session_feed``, whose ``_execute`` files one ``paper.db.*`` sample
    per statement (task 33.5, Requirement 26.6). That call reaches THIS double, so the double
    has to answer it - a recorder that did not would make an instrumented write fail with
    ``AttributeError`` inside the path under test.

    Every method's signature is the collector's, keyword names included, so a call this
    module makes positionally where the collector expects a keyword fails here rather than
    in production.
    """

    def __init__(self) -> None:
        self.states: List[Tuple[str, str]] = []
        self.events: List[Tuple[str, str]] = []
        self.latencies: List[Tuple[Any, str, str]] = []
        self.invalid: List[Tuple[str, str]] = []
        #: ``(symbol, arbiter)`` - which of the two dedupe arbiters caught each duplicate.
        self.duplicates: List[Tuple[str, str]] = []
        self.out_of_order: List[str] = []
        self.reconnects: List[str] = []
        #: ``(domain, operation, duration_ms, failed)`` per persisted statement.
        self.db_statements: List[Tuple[str, str, Any, bool]] = []

    def record_paper_feed_state(self, state: Any, reason: str = "") -> None:
        self.states.append((str(state), str(reason)))

    def record_paper_feed_event(self, symbol: str = "", transport: str = "") -> None:
        self.events.append((symbol, transport))

    def record_paper_feed_latency(
        self, latency_ms: Any, symbol: str = "", timeframe: str = ""
    ) -> None:
        self.latencies.append((latency_ms, symbol, timeframe))

    def record_paper_feed_invalid(self, symbol: str = "", reason: str = "") -> None:
        self.invalid.append((symbol, reason))

    def record_paper_feed_duplicate(self, symbol: str = "", arbiter: str = "") -> None:
        self.duplicates.append((symbol, arbiter))

    def record_paper_feed_out_of_order(self, symbol: str = "") -> None:
        self.out_of_order.append(symbol)

    def record_paper_feed_reconnect(self, outcome: str = "") -> None:
        self.reconnects.append(outcome)

    def record_db_statement(
        self,
        domain: str,
        operation: str,
        *,
        duration_ms: Any = None,
        failed: bool = False,
    ) -> None:
        self.db_statements.append((domain, operation, duration_ms, failed))


# ══════════════════════════════════════════════════════════════════════════
# MEASUREMENTS - the input this module refuses to invent
# ══════════════════════════════════════════════════════════════════════════


def _floor_clean(source: str, *, p99_ms: float) -> mdl.SourceMeasurement:
    """A measurement that passes Requirement 19.11's floor, with a stated p99.

    Every one of the five floor criteria is **counted** and every count is the value the
    requirement demands, over ``expected_bars`` at
    ``CorrectnessFloor.min_completeness_sample_bars`` - the smallest sample in which 99.99 %
    completeness is resolvable. Read from the floor rather than hardcoded, so a change to the
    threshold moves this fixture with it instead of silently making it insufficient.
    """
    bars = mdl.DEFAULT_FLOOR.min_completeness_sample_bars
    return mdl.SourceMeasurement(
        source=source,
        has_validation_layer=feed.HAS_VALIDATION_LAYER[source],
        measured=True,
        symbols=(SYMBOL,),
        timeframes=(TIMEFRAME,),
        expected_bars=bars,
        received_bars=bars,
        monotonic_violations=0,
        duplicates_delivered=0,
        invalid_ohlc_delivered=0,
        synthetic_candles_delivered=0,
        end_to_end=mdl.LatencySummary.from_samples([p99_ms]),
    )


def _admitting() -> Dict[str, mdl.SourceMeasurement]:
    """Both candidates measured and floor-clean, so the decision is ``SELECTED``.

    B is given the lower p99 as well as the validation layer, so the selected source is B
    under the margin rule and under the tie-break alike - the cases below are about the feed,
    not about which candidate wins.
    """
    return {
        mdl.SOURCE_A: _floor_clean(mdl.SOURCE_A, p99_ms=90.0),
        mdl.SOURCE_B: _floor_clean(mdl.SOURCE_B, p99_ms=10.0),
    }


# ══════════════════════════════════════════════════════════════════════════
# THE SESSION ROW AND THE CLIENT
# ══════════════════════════════════════════════════════════════════════════


def _session_row(**overrides: Any) -> Dict[str, Any]:
    """One ``paper_sessions`` row at ``CREATED``, as the session-start path would create it.

    ``market_data_source`` is ``NOT NULL`` in 009, so a session row always carries one. It is
    seeded as ``PENDING_SELECTION`` - a value no selection rule can produce - so "the feed
    recorded the source it selected" is a change this file can see, rather than a value that
    was already there.
    """
    row: Dict[str, Any] = {
        "id": SESSION,
        "user_id": USER,
        "environment": "PAPER",
        "session_state": "CREATED",
        "exchange_id": EXCHANGE,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "market_data_source": "PENDING_SELECTION",
        "feed_state": feed.FEED_STATE_PENDING,
        "feed_transport": None,
    }
    row.update(overrides)
    return row


def _client(**kwargs: Any) -> FakeSupabase:
    return FakeSupabase(sessions=[_session_row()], **kwargs)


def _config(**overrides: Any) -> feed.PaperFeedConfig:
    fields: Dict[str, Any] = {
        "session_id": SESSION,
        "user_id": USER,
        "exchange_id": EXCHANGE,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
    }
    fields.update(overrides)
    return feed.PaperFeedConfig(**fields)


@pytest.fixture(autouse=True)
def _fresh_probe() -> Any:
    """Forget the cached migration verdict around every case, as the repository suite does."""
    repo.reset_persistence_probe()
    yield
    repo.reset_persistence_probe()


@pytest.fixture
def metrics(monkeypatch: pytest.MonkeyPatch) -> _RecordingMetrics:
    """Requirement 26.6's collector, replaced with a recorder.

    The feed reaches the collector through ``metrics.guarded_collector()``, which reads the module
    attribute on every call, so replacing the attribute is enough and nothing has to be
    re-imported.
    """
    from backend_app.backend import metrics as metrics_module

    recorder = _RecordingMetrics()
    monkeypatch.setattr(metrics_module, "metrics_collector", recorder)
    return recorder


@pytest.fixture
def audit(monkeypatch: pytest.MonkeyPatch) -> _RecordingAuditLogger:
    """``core.audit_trail.get_strategy_audit_logger``, replaced with a recorder.

    Patched on the module rather than on the feed, because the feed resolves it at call time
    (``from backend_app.core.audit_trail import ...`` inside the function) - so patching the
    source is what a production call would see.
    """
    from backend_app.core import audit_trail

    logger = _RecordingAuditLogger()
    monkeypatch.setattr(audit_trail, "get_strategy_audit_logger", lambda: logger)
    return logger


def _open(
    *,
    redis: RecordingRedis,
    supabase: Any = None,
    exchange: Any = None,
    measurements: Optional[Dict[str, mdl.SourceMeasurement]] = None,
    config: Optional[feed.PaperFeedConfig] = None,
) -> feed.FeedHandle:
    return _run_coroutine(
        feed.open_feed(
            config or _config(),
            exchange=exchange,
            redis=redis,
            supabase=supabase,
            measurements=measurements,
        )
    )


# ══════════════════════════════════════════════════════════════════════════
# 1. A BLOCKED DECISION IS A 409 CARRYING THE RULE AND THE REASON
#    (Requirements 14.4, 28.3)
# ══════════════════════════════════════════════════════════════════════════


class TestABlockedDecisionRefusesWithTheRuleAndReason:
    """Task 24.1: ``outcome == OUTCOME_BLOCKED`` raises ``PaperMarketDataUnavailable``.

    Red run: the ``if decision.outcome == OUTCOME_BLOCKED: ... raise`` block in ``open_feed``
    was commented out. Every case in this class failed - the three assertion cases with
    ``DID NOT RAISE PaperMarketDataUnavailable``.
    """

    def test_the_refusal_is_raised_at_all(self, metrics: _RecordingMetrics) -> None:
        with pytest.raises(feed.PaperMarketDataUnavailable):
            _open(redis=RecordingRedis(), measurements=None)

    def test_the_code_is_paper_market_data_unavailable_at_409(self) -> None:
        with pytest.raises(feed.PaperMarketDataUnavailable) as caught:
            _open(redis=RecordingRedis(), measurements=None)

        assert caught.value.code == PAPER_MARKET_DATA_UNAVAILABLE
        assert caught.value.http_status == 409
        # And 409 is the catalogue's, not this exception's own opinion - the status is pinned
        # there so no call site can turn "there is no validated market data" into a 200.
        assert HTTP_STATUS_FOR_CODE[PAPER_MARKET_DATA_UNAVAILABLE] == 409

    def test_it_carries_the_decisions_own_rule_and_reason_verbatim(self) -> None:
        """Requirement 14.4's "report a market-data-unavailable condition", with the cause.

        The expected strings come from ``select_market_data_source`` itself rather than from a
        literal here: the refusal has to quote the verdict ``market_data_latency`` reached,
        and a hardcoded sentence would still pass if the feed substituted its own.
        """
        decision = feed.select_market_data_source(None)
        assert decision.outcome == mdl.OUTCOME_BLOCKED

        with pytest.raises(feed.PaperMarketDataUnavailable) as caught:
            _open(redis=RecordingRedis(), measurements=None)

        error = caught.value
        assert error.rule == decision.rule
        assert error.reason == decision.reason
        assert error.details["rule"] == decision.rule
        assert error.details["reason"] == decision.reason
        # The rule is the floor's own name for this outcome, not a paraphrase.
        assert decision.rule == mdl.RULE_FLOOR_BLOCKED_ALL

    def test_the_reason_survives_redaction_so_the_cause_reaches_the_caller(self) -> None:
        """``details`` goes through ``redact_details``; both strings must come out intact.

        Otherwise the 409 would be true but useless: an operator reading it would know the
        feed was refused and not that nothing had been measured.
        """
        from backend_app.backend.marketplace.errors import redact_details

        with pytest.raises(feed.PaperMarketDataUnavailable) as caught:
            _open(redis=RecordingRedis(), measurements=None)

        scrubbed = redact_details(caught.value.details)
        assert scrubbed["rule"] == caught.value.rule
        assert scrubbed["reason"] == caught.value.reason


# ══════════════════════════════════════════════════════════════════════════
# 2. THE REFUSAL TOUCHES NOTHING - AND IT TOUCHES NOTHING *FIRST*
#    (Requirements 14.2, 14.4, 28.3)
# ══════════════════════════════════════════════════════════════════════════


class TestARefusalLeavesTheSessionCreatedAndPublishesNothing:
    """Task 24.1: "the session stays ``CREATED`` and no subscription is opened".

    Red run: one ``await _resolve_redis(redis).publish(MDS_COMMAND_CHANNEL, ...)`` was added
    to ``open_feed``'s BLOCKED branch immediately **before** the ``raise`` - the exact
    "subscribed first, raised second" shape. The refusal still happened, so
    :class:`TestABlockedDecisionRefusesWithTheRuleAndReason` stayed green, and
    ``test_the_interaction_log_is_empty`` plus
    ``test_no_mds_commands_message_is_published`` failed on the ordered log.
    """

    def test_the_interaction_log_is_empty(self) -> None:
        """The ordering assertion: nothing happened, not "nothing was left behind".

        A refusal that published and then raised would leave ``[('publish', 'mds:commands')]``
        here. Asserted as an ordered log rather than as a pair of counters because the claim
        being tested is about *sequence*: steps 4 and 5 of ``open_feed`` must be unreachable
        from step 2, not merely undone by it.
        """
        redis = RecordingRedis()
        with pytest.raises(feed.PaperMarketDataUnavailable):
            _open(redis=redis, measurements=None)

        assert redis.log == []

    def test_no_mds_commands_message_is_published(self) -> None:
        redis = RecordingRedis()
        with pytest.raises(feed.PaperMarketDataUnavailable):
            _open(redis=redis, measurements=None)

        assert redis.commands() == []
        assert redis.published == []

    def test_no_data_channel_is_subscribed(self) -> None:
        redis = RecordingRedis()
        with pytest.raises(feed.PaperMarketDataUnavailable):
            _open(redis=redis, measurements=None)

        assert redis.subscribed == []
        assert redis.pubsub_calls == 0

    def test_the_session_row_is_untouched_and_still_created(self) -> None:
        """Requirement 14.3's column is not written, and 17.7's state has not moved."""
        client = _client()
        with pytest.raises(feed.PaperMarketDataUnavailable):
            _open(redis=RecordingRedis(), supabase=client, measurements=None)

        row = client.sessions[0]
        assert row["session_state"] == "CREATED"
        assert row["market_data_source"] == "PENDING_SELECTION"
        assert row["feed_state"] == feed.FEED_STATE_PENDING
        assert row["feed_transport"] is None

    def test_no_statement_was_issued_against_any_paper_table(self) -> None:
        """Stronger than reading the row back: the refusal issues no statement at all.

        A refusal that wrote and then restored would pass the row assertion above. This one
        also rules out a read - the decision is reached before the Persistence_Layer is
        touched, which is what makes the refusal safe to make before a session even exists.
        """
        client = _client()
        with pytest.raises(feed.PaperMarketDataUnavailable):
            _open(redis=RecordingRedis(), supabase=client, measurements=None)

        assert client.statements == []
        assert client.wrote_anything() is False

    def test_the_two_logs_hold_no_row(self) -> None:
        client = _client()
        with pytest.raises(feed.PaperMarketDataUnavailable):
            _open(redis=RecordingRedis(), supabase=client, measurements=None)

        assert client.market_events == []
        assert client.events == []


# ══════════════════════════════════════════════════════════════════════════
# 3. AN UNMEASURED SOURCE CANNOT BE SELECTED - THROUGH THE REAL RULE
#    (Requirements 14.1, 14.4)
# ══════════════════════════════════════════════════════════════════════════


class TestAnUnmeasuredSourceCannotBeSelected:
    """Task 24.1: the floor fails ``NOT_MEASURED`` / ``FLOOR_METRICS_MISSING`` before p99.

    The real ``market_data_latency.choose_market_data_source`` runs in every case here. That
    is the whole point: the requirement is about *where* the refusal happens - inside the
    floor, before latency is read - and a stubbed decision could not show that.

    Red run: ``select_market_data_source`` was replaced with a body returning
    ``SourceDecision(outcome=OUTCOME_SELECTED, selected=SOURCE_B, ...)`` without consulting
    the floor - the "second selection rule" task 24.1 forbids. Every case in this class
    failed.
    """

    def test_the_feed_holds_the_platforms_own_selection_function(self) -> None:
        """Requirement 14.1: the existing rule, by identity - not a copy of it.

        If this ever fails, every other case in this class is measuring something else.
        """
        assert feed.choose_market_data_source is mdl.choose_market_data_source

    def test_nothing_measured_blocks_and_selects_nothing(self) -> None:
        decision = feed.select_market_data_source(None)

        assert decision.outcome == mdl.OUTCOME_BLOCKED
        assert decision.selected is None
        for source in (mdl.SOURCE_A, mdl.SOURCE_B):
            verdict = decision.verdict_for(source)
            assert verdict is not None
            assert verdict.passed is False
            assert verdict.rejection_reason == mdl.FLOOR_NOT_MEASURED

    def test_the_floor_fails_before_the_p99_comparison_is_reachable(self) -> None:
        """``NOT_MEASURED`` is reported with **no** floor checks evaluated at all.

        ``evaluate_correctness_floor`` returns ``checks=()`` on that branch, so the rejection
        is provably upstream of every criterion and therefore upstream of the 25 ms margin.
        The unmeasured candidate also has no p99 to compare, which is the same fact from the
        other side.
        """
        decision = feed.select_market_data_source(None)

        for source in (mdl.SOURCE_A, mdl.SOURCE_B):
            verdict = decision.verdict_for(source)
            assert verdict.checks == ()
        assert decision.p99_margin_ms is None
        for measurement in decision.measurements:
            assert measurement.p99_end_to_end_ms is None

    def test_an_unmeasured_candidate_carries_the_reason_the_feed_supplied(self) -> None:
        """``measurement_for`` names what would have to happen, and never invents a figure."""
        measurement = feed.measurement_for(mdl.SOURCE_A, measurements=None)

        assert measurement.measured is False
        assert measurement.unavailable_reason == feed.UNMEASURED_REASON
        assert measurement.expected_bars is None
        assert measurement.duplicates_delivered is None
        assert measurement.end_to_end is None

    def test_a_zero_latency_candidate_with_uncounted_floor_metrics_is_still_refused(
        self,
    ) -> None:
        """The decisive case: the best possible p99 does not buy admission.

        Both candidates report a 0.001 ms p99 - unbeatable - and ``measured=True``, but their
        five floor metrics were never counted. ``FLOOR_METRICS_NOT_MEASURED`` is the verdict
        and nothing is selected, which is what "the correctness floor is honoured before the
        latency comparison" means in practice.
        """
        fast_but_uncounted = {
            source: mdl.SourceMeasurement(
                source=source,
                has_validation_layer=feed.HAS_VALIDATION_LAYER[source],
                measured=True,
                end_to_end=mdl.LatencySummary.from_samples([0.001]),
            )
            for source in (mdl.SOURCE_A, mdl.SOURCE_B)
        }

        decision = feed.select_market_data_source(fast_but_uncounted)

        assert decision.outcome == mdl.OUTCOME_BLOCKED
        assert decision.selected is None
        for source in (mdl.SOURCE_A, mdl.SOURCE_B):
            assert (
                decision.verdict_for(source).rejection_reason
                == mdl.FLOOR_METRICS_MISSING
            )

    def test_one_measured_candidate_is_selected_and_the_unmeasured_one_is_not(
        self,
    ) -> None:
        """The other half of the claim: only the *unmeasured* candidate is excluded.

        A rule that refused everything would pass every case above for the wrong reason.
        """
        decision = feed.select_market_data_source(
            {mdl.SOURCE_B: _floor_clean(mdl.SOURCE_B, p99_ms=10.0)}
        )

        assert decision.outcome == mdl.OUTCOME_SELECTED
        assert decision.selected == mdl.SOURCE_B
        assert decision.verdict_for(mdl.SOURCE_A).rejection_reason == mdl.FLOOR_NOT_MEASURED

    def test_open_feed_refuses_a_deployment_whose_floor_metrics_were_not_counted(
        self,
    ) -> None:
        """The refusal reaches ``open_feed``, not only ``select_market_data_source``.

        Both candidates ran and both report an unbeatable p99, so the only thing standing
        between this deployment and a running session is the floor.
        """
        redis = RecordingRedis()
        fast_but_uncounted = {
            source: mdl.SourceMeasurement(
                source=source,
                has_validation_layer=feed.HAS_VALIDATION_LAYER[source],
                measured=True,
                end_to_end=mdl.LatencySummary.from_samples([0.001]),
            )
            for source in (mdl.SOURCE_A, mdl.SOURCE_B)
        }

        with pytest.raises(feed.PaperMarketDataUnavailable) as caught:
            _open(redis=redis, measurements=fast_but_uncounted)

        assert caught.value.code == PAPER_MARKET_DATA_UNAVAILABLE
        assert caught.value.rule == mdl.RULE_FLOOR_BLOCKED_ALL
        assert redis.log == []

    def test_a_measurement_of_another_candidate_is_refused_not_relabelled(self) -> None:
        """Attributing one candidate's figures to another would admit a source on evidence
        that is not about it - so it raises rather than being quietly accepted."""
        mislabelled = {mdl.SOURCE_B: _floor_clean(mdl.SOURCE_A, p99_ms=1.0)}

        with pytest.raises(ValueError) as caught:
            feed.measurement_for(mdl.SOURCE_B, measurements=mislabelled)

        assert "is not evidence about another" in str(caught.value)


# ══════════════════════════════════════════════════════════════════════════
# 4. THE DEV_MODE MOCK INTERFACE IS REFUSED - BY THE INTERFACE, NOT THE FLAG
#    (Requirements 14.8, 28.3)
# ══════════════════════════════════════════════════════════════════════════


def _mock_served_exchange() -> Any:
    """An exchange object served by the **real** ``_apply_mock_interface``.

    Built the way ``tests/test_asset_discovery.py::test_a_dev_mode_mock_market_map_is_refused``
    builds it, and for the same reason: the detector matches the ``__name__`` of the closure
    that installer binds, so invoking the installer for real is what makes a rename of it
    break this test instead of silently disarming the guard.
    """
    from backend_app.backend.connection_engine import ConnectionEngine

    engine = ConnectionEngine(exchange_id=EXCHANGE)
    engine.exchange = SimpleNamespace()
    engine._apply_mock_interface()
    return engine.exchange


class TestTheDevModeMockInterfaceIsRefused:
    """Task 24.1: a mock-served connection is refused, with an audit record.

    Red run 1 (the rename this test exists to catch): ``asset_universe``'s
    ``MOCK_MARKET_LOADER_NAME`` was changed to ``"mock_load_markets_renamed"`` - the guard
    goes quiet while every other test stays green. Every case in this class failed.

    Red run 2 (the flag): ``markets_are_mocked`` was replaced with
    ``return bool(core.dependencies.DEV_MODE)``.
    ``test_the_mock_interface_is_refused_with_the_flag_unset`` and
    ``test_a_real_interface_is_not_refused_with_the_flag_set`` both failed - which is the pair
    that pins "detected by inspecting the resolved exchange object, not by reading DEV_MODE".
    """

    def test_the_installer_really_installs_a_hardcoded_market_map(self) -> None:
        """The premise, asserted so the refusal below is a refusal of something real."""
        exchange = _mock_served_exchange()

        assert set(exchange.markets) == {"BTC/USDT", "ETH/USDT"}
        assert exchange.load_markets.__name__ == au.MOCK_MARKET_LOADER_NAME
        assert au.markets_are_mocked(exchange) is True

    def test_open_feed_refuses_it_with_the_mock_interface_rule(
        self, audit: _RecordingAuditLogger
    ) -> None:
        redis = RecordingRedis()
        with pytest.raises(feed.PaperMarketDataUnavailable) as caught:
            _open(
                redis=redis,
                exchange=_mock_served_exchange(),
                measurements=_admitting(),
            )

        error = caught.value
        assert error.code == PAPER_MARKET_DATA_UNAVAILABLE
        assert error.http_status == 409
        assert error.rule == feed.PaperMarketDataUnavailable.RULE_MOCK_INTERFACE
        assert error.details["exchange_id"] == EXCHANGE

    def test_it_is_refused_even_though_the_measurements_admit_a_source(self) -> None:
        """The two refusals are independent: this one is not the floor's.

        Same measurements, no mock interface -> the feed opens. So the refusal above is
        attributable to the interface and to nothing else.
        """
        redis = RecordingRedis()
        handle = _open(redis=redis, exchange=None, measurements=_admitting())

        assert handle.market_data_source == mdl.SOURCE_B
        assert redis.commands()

    def test_the_refusal_is_audited_as_paper_feed_refused_mock_interface(
        self, audit: _RecordingAuditLogger
    ) -> None:
        """Requirement 14.8: "SHALL record the refusal reason"."""
        with pytest.raises(feed.PaperMarketDataUnavailable):
            _open(
                redis=RecordingRedis(),
                exchange=_mock_served_exchange(),
                measurements=_admitting(),
            )

        assert len(audit.records) == 1
        record = audit.records[0]
        assert record["action"].name == feed.AUDIT_ACTION_REFUSED_MOCK_INTERFACE
        assert record["action"].name == "PAPER_FEED_REFUSED_MOCK_INTERFACE"
        assert record["actor_id"] == USER
        assert record["resource_type"] == "paper_session"
        assert record["resource_id"] == SESSION
        assert record["metadata"]["exchange_id"] == EXCHANGE
        assert record["metadata"]["symbol"] == SYMBOL
        assert record["metadata"]["timeframe"] == TIMEFRAME

    def test_the_audit_member_exists_on_the_platforms_own_enum(self) -> None:
        """No second audit facility and no new member: it is the existing enum's."""
        from backend_app.core.audit_trail import StrategyAuditAction

        assert hasattr(StrategyAuditAction, feed.AUDIT_ACTION_REFUSED_MOCK_INTERFACE)

    def test_the_mock_refusal_publishes_nothing_and_leaves_the_session_created(
        self, audit: _RecordingAuditLogger
    ) -> None:
        redis = RecordingRedis()
        client = _client()
        with pytest.raises(feed.PaperMarketDataUnavailable):
            _open(
                redis=redis,
                supabase=client,
                exchange=_mock_served_exchange(),
                measurements=_admitting(),
            )

        assert redis.log == []
        assert client.statements == []
        assert client.sessions[0]["session_state"] == "CREATED"
        assert client.sessions[0]["market_data_source"] == "PENDING_SELECTION"

    # -- the detector does not consult the flag ----------------------------

    def test_the_mock_interface_is_refused_with_the_flag_unset(
        self, monkeypatch: pytest.MonkeyPatch, audit: _RecordingAuditLogger
    ) -> None:
        """``DEV_MODE`` off, mock interface installed -> still refused.

        The two can disagree: ``connect()`` installs the mock only on its failure branch, so a
        deployment whose flag was turned off after an interface was installed is still being
        served hardcoded prices. It is the interface that decides what the prices are.
        """
        from backend_app.core import dependencies

        monkeypatch.setattr(dependencies, "DEV_MODE", False, raising=False)
        monkeypatch.delenv("DEV_MODE", raising=False)

        with pytest.raises(feed.PaperMarketDataUnavailable) as caught:
            _open(
                redis=RecordingRedis(),
                exchange=_mock_served_exchange(),
                measurements=_admitting(),
            )

        assert caught.value.rule == feed.PaperMarketDataUnavailable.RULE_MOCK_INTERFACE

    def test_a_real_interface_is_not_refused_with_the_flag_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``DEV_MODE`` on, a real interface resolved -> the feed opens.

        The other direction of the same claim. A detector that read the flag would refuse
        here, and refusing a real feed because of a configuration value would take a working
        session down for no reason.
        """
        from backend_app.core import dependencies

        monkeypatch.setattr(dependencies, "DEV_MODE", True, raising=False)
        monkeypatch.setenv("DEV_MODE", "true")

        real_ish = SimpleNamespace(id=EXCHANGE, markets={}, load_markets=lambda: {})
        assert au.markets_are_mocked(real_ish) is False

        redis = RecordingRedis()
        handle = _open(redis=redis, exchange=real_ish, measurements=_admitting())

        assert handle.market_data_source == mdl.SOURCE_B
        assert redis.subscribed == [DATA_CHANNEL]

    def test_the_detector_reads_no_flag_and_no_environment(self) -> None:
        """Asserted on the detector's own AST, not on its prose.

        ``markets_are_mocked``'s docstring explains at length that it does not read
        ``DEV_MODE``, so a substring search over its source would match the explanation. The
        names its code actually references are what is checked: no ``DEV_MODE``, no
        ``os.environ``, no ``getenv``, no ``settings``.
        """
        tree = ast.parse(inspect.getsource(au.markets_are_mocked))
        referenced = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

        for forbidden in ("DEV_MODE", "environ", "getenv", "settings", "dependencies"):
            assert forbidden not in referenced

    def test_none_is_not_a_mock(self) -> None:
        """Requirement 15.3 lets a session start with no exchange connection at all."""
        assert au.markets_are_mocked(None) is False


# ══════════════════════════════════════════════════════════════════════════
# 5. session.market_data_source RECORDS decision.selected (Requirement 14.3)
# ══════════════════════════════════════════════════════════════════════════


class TestTheSelectedSourceIsRecordedOnTheSession:
    """Task 24.1: ``session.market_data_source <- decision.selected.source``.

    Red run: the ``repo.update_session_feed(...)`` call in ``open_feed``'s step 5 was
    commented out. ``test_the_session_row_records_the_selected_source`` and
    ``test_the_recorded_source_is_the_decisions_and_not_a_default`` failed on
    ``'PENDING_SELECTION' != 'candidate_b_validated_ohlcv_pipeline'``.
    """

    def test_the_session_row_records_the_selected_source(self) -> None:
        client = _client()
        handle = _open(
            redis=RecordingRedis(), supabase=client, measurements=_admitting()
        )

        assert client.sessions[0]["market_data_source"] == handle.decision.selected
        assert handle.market_data_source == handle.decision.selected

    def test_the_recorded_source_is_the_decisions_and_not_a_default(self) -> None:
        """The recorded value moves with the decision, so it is not a constant.

        Here A is given the better p99 *and* B's measurement is withheld, so the rule selects
        A - and the row has to say A. A feed that wrote a fixed string, or wrote whichever
        candidate it preferred, would pass the case above and fail this one.
        """
        client = _client()
        only_a = {mdl.SOURCE_A: _floor_clean(mdl.SOURCE_A, p99_ms=5.0)}

        handle = _open(redis=RecordingRedis(), supabase=client, measurements=only_a)

        assert handle.decision.selected == mdl.SOURCE_A
        assert client.sessions[0]["market_data_source"] == mdl.SOURCE_A

    def test_the_write_is_scoped_to_the_owner_and_the_session(self) -> None:
        """Requirement 21.5: the identity is in the statement, not applied afterwards."""
        client = _client()
        _open(redis=RecordingRedis(), supabase=client, measurements=_admitting())

        updates = [
            statement
            for statement in client.statements_on(repo.SESSIONS_TABLE, "update")
        ]
        assert updates
        assert updates[0].filter_value("user_id") == USER
        assert updates[0].filter_value("id") == SESSION

    def test_another_users_session_row_is_not_written(self) -> None:
        client = FakeSupabase(
            sessions=[_session_row(), _session_row(id="other", user_id=OTHER_USER)]
        )
        _open(redis=RecordingRedis(), supabase=client, measurements=_admitting())

        intruder = next(row for row in client.sessions if row["id"] == "other")
        assert intruder["market_data_source"] == "PENDING_SELECTION"

    def test_the_feed_state_is_not_declared_healthy_by_opening_the_subscription(
        self,
    ) -> None:
        """Opening a subscription is not evidence that data is flowing.

        The session's ``feed_state`` stays at 009's ``PENDING`` until an event passes
        validation, so a session that subscribed and then received nothing is not reported as
        healthy (Requirement 28.5).
        """
        client = _client()
        handle = _open(
            redis=RecordingRedis(), supabase=client, measurements=_admitting()
        )

        assert handle.feed_state == feed.FEED_STATE_PENDING
        assert client.sessions[0]["feed_state"] == feed.FEED_STATE_PENDING


# ══════════════════════════════════════════════════════════════════════════
# 6. THE HANDSHAKE IS THE EXISTING MDS ONE (Requirement 14.2)
# ══════════════════════════════════════════════════════════════════════════


class TestTheHandshakeUsesTheExistingMdsChannels:
    """Task 24.2: publish ``subscribe`` to ``mds:commands``, subscribe to ``mds:data:...``.

    Red run: ``mds_data_channel`` was changed to return ``f"mds:{exchange}:{symbol}"``.
    ``test_the_data_channel_is_the_exact_channel_broadcast_ohlcv_publishes_to``,
    ``test_the_channel_helper_is_the_one_the_handle_uses`` and
    ``test_the_command_and_the_subscription_name_the_same_pair`` failed on the exact string.
    """

    def test_the_command_channel_is_the_one_handle_commands_listens_on(self) -> None:
        assert feed.MDS_COMMAND_CHANNEL == "mds:commands"

    def test_the_published_command_is_exactly_action_exchange_symbol(self) -> None:
        redis = RecordingRedis()
        _open(redis=redis, measurements=_admitting())

        assert len(redis.commands()) == 1
        command = redis.commands()[0]
        assert command == {
            "action": "subscribe",
            "exchange": EXCHANGE,
            "symbol": SYMBOL,
        }
        # Exactly those three keys: `handle_commands` reads `action`, `exchange` and `symbol`
        # and nothing else, so a fourth would be a field nobody consumes.
        assert set(command) == {"action", "exchange", "symbol"}

    def test_the_command_goes_to_mds_commands_and_nowhere_else(self) -> None:
        redis = RecordingRedis()
        _open(redis=redis, measurements=_admitting())

        assert [channel for channel, _ in redis.published] == [feed.MDS_COMMAND_CHANNEL]

    def test_the_data_channel_is_the_exact_channel_broadcast_ohlcv_publishes_to(
        self,
    ) -> None:
        """The exact string, asserted against a literal written independently here."""
        redis = RecordingRedis()
        handle = _open(redis=redis, measurements=_admitting())

        assert redis.subscribed == [DATA_CHANNEL]
        assert handle.channel == DATA_CHANNEL
        assert redis.pubsub_calls == 1

    def test_the_channel_helper_is_the_one_the_handle_uses(self) -> None:
        assert feed.mds_data_channel(EXCHANGE, SYMBOL) == DATA_CHANNEL
        assert _config().channel == DATA_CHANNEL

    def test_the_exchange_id_is_lower_cased_the_way_handle_commands_keys_it(self) -> None:
        """``handle_commands`` does ``data.get("exchange", ...).lower()`` before keying its
        stream table, so a caller passing ``"Binance"`` must land on the channel the service
        actually publishes to rather than on one nobody writes."""
        redis = RecordingRedis()
        handle = _open(
            redis=redis,
            measurements=_admitting(),
            config=_config(exchange_id="Binance"),
        )

        assert redis.commands()[0]["exchange"] == EXCHANGE
        assert handle.channel == DATA_CHANNEL
        assert redis.subscribed == [DATA_CHANNEL]

    def test_the_command_precedes_the_subscription(self) -> None:
        """Publish first, subscribe second - the order ``data_seeking_engine`` established.

        The command is what wakes ``broadcast_ohlcv`` for a pair nobody is streaming yet; a
        subscription opened before it would be a subscription to a silent channel if the
        command then failed.
        """
        redis = RecordingRedis()
        _open(redis=redis, measurements=_admitting())

        assert redis.log == [
            ("publish", feed.MDS_COMMAND_CHANNEL),
            ("pubsub", ""),
            ("subscribe", DATA_CHANNEL),
        ]

    def test_the_command_and_the_subscription_name_the_same_pair(self) -> None:
        """A command for one pair and a subscription to another would deliver nothing."""
        redis = RecordingRedis()
        _open(redis=redis, measurements=_admitting())

        command = redis.commands()[0]
        assert redis.subscribed == [
            f"mds:data:{command['exchange']}:{command['symbol']}"
        ]


# ══════════════════════════════════════════════════════════════════════════
# 7. THE TRANSPORT IS OBSERVED, NEVER ASSUMED (Requirements 14.6, 28.5)
# ══════════════════════════════════════════════════════════════════════════

#: One candle on ``mds:data:*``, in ``broadcast_ohlcv``'s own payload shape. Serialised with
#: ``json.dumps`` and delivered as text, because that is what arrives on the wire - and
#: because it is what makes ``decode_payload``'s ``parse_float=Decimal`` do the work a test
#: handing over a ready-made mapping would have skipped.
def _frame(
    *,
    transport: Optional[str] = "WEBSOCKET",
    timestamp_ms: Optional[int] = None,
    close: Any = 60000.5,
    volume: Any = 1.5,
    overrides: Optional[Dict[str, Any]] = None,
    drop: Tuple[str, ...] = (),
) -> Dict[str, Any]:
    """One ``mds:data:*`` frame in ``broadcast_ohlcv``'s payload shape.

    ``overrides`` replaces or adds payload fields and ``drop`` removes them, both applied last.
    They exist for ``tests/test_paper_market_feed_events.py``, which needs a candle whose low
    exceeds its high, one naming another market, and one missing a required field - all of them
    the *same* payload shape with one thing wrong, which is what makes the resulting drop
    attributable to that one thing. Added here rather than as a second builder there so both
    files agree on what a well-formed frame looks like.
    """
    payload: Dict[str, Any] = {
        "exchange": EXCHANGE,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "timestamp": timestamp_ms
        if timestamp_ms is not None
        else int((PROCESSED_AT - timedelta(minutes=1)).timestamp() * 1000),
        "open": 59900.0,
        "high": 60100.0,
        "low": 59800.0,
        "close": close,
        "volume": volume,
    }
    if transport is not None:
        payload["transport"] = transport
    if overrides:
        payload.update(overrides)
    for name in drop:
        payload.pop(name, None)
    return {"type": "message", "channel": DATA_CHANNEL, "data": json.dumps(payload)}


def _one_event(
    frame: Dict[str, Any],
    *,
    client: Optional[FakeSupabase] = None,
) -> Tuple[Optional[feed.MarketEvent], feed.FeedHandle, FakeSupabase]:
    """Open a feed, deliver one frame, and return what came out."""
    supabase = client if client is not None else _client()
    redis = RecordingRedis(frames=[frame])
    handle = _open(redis=redis, supabase=supabase, measurements=_admitting())
    event = _run_coroutine(feed.next_validated_event(handle))
    return event, handle, supabase


class TestTheTransportIsObservedFromThePayload:
    """Task 24.2: ``feed_transport`` and ``feed_state`` come from the payload's field.

    Red run: ``normalise_transport``'s fall-through was changed from
    ``FEED_TRANSPORT_UNKNOWN`` to ``FEED_TRANSPORT_WEBSOCKET`` - the "default to the happy
    path" substitution Requirement 28.5 forbids. ``test_a_payload_with_no_transport_field_...``
    (both cases) failed on ``'WEBSOCKET' != 'UNKNOWN'`` and on the feed state being
    ``HEALTHY``. The ``WEBSOCKET`` and ``REST`` cases stayed green, which is why the missing
    case is asserted separately.
    """

    def test_a_websocket_payload_maps_to_websocket_and_healthy(
        self, metrics: _RecordingMetrics
    ) -> None:
        event, handle, client = _one_event(_frame(transport="WEBSOCKET"))

        assert event is not None
        assert event.transport == feed.FEED_TRANSPORT_WEBSOCKET
        assert handle.feed_transport == feed.FEED_TRANSPORT_WEBSOCKET
        assert handle.feed_state == feed.FEED_STATE_HEALTHY
        assert client.sessions[0]["feed_transport"] == "WEBSOCKET"
        assert client.sessions[0]["feed_state"] == "HEALTHY"

    def test_a_rest_payload_maps_to_rest_and_fallback_rest(
        self, metrics: _RecordingMetrics
    ) -> None:
        """Requirement 14.6: the fallback is *recorded* in the session's feed state.

        ``mds/main.py``'s REST loop publishes to the same channel with an otherwise identical
        payload, so the ``transport`` field is the only thing that distinguishes them.
        """
        event, handle, client = _one_event(_frame(transport="REST"))

        assert event is not None
        assert event.transport == feed.FEED_TRANSPORT_REST
        assert handle.feed_transport == feed.FEED_TRANSPORT_REST
        assert handle.feed_state == feed.FEED_STATE_FALLBACK_REST
        assert client.sessions[0]["feed_transport"] == "REST"
        assert client.sessions[0]["feed_state"] == "FALLBACK_REST"

    def test_a_payload_with_no_transport_field_does_not_crash(
        self, metrics: _RecordingMetrics
    ) -> None:
        """An older MDS publishes no ``transport``. The candle is still processed.

        Dropping it would be worse than recording it as unidentified: the candle itself is
        valid market data, and a deployment mid-rollout would stop trading.
        """
        event, handle, client = _one_event(_frame(transport=None))

        assert event is not None
        assert event.sequence == 1
        assert client.market_events and len(client.market_events) == 1

    def test_a_payload_with_no_transport_field_is_not_reported_as_a_healthy_websocket(
        self, metrics: _RecordingMetrics
    ) -> None:
        """The explicit state the implementation chose: ``UNKNOWN`` / ``TRANSPORT_UNKNOWN``.

        Asserted both positively - it is that state - and negatively - it is not ``HEALTHY``
        and not ``WEBSOCKET`` - because the negative is the requirement and the positive is
        what makes the requirement checkable.
        """
        event, handle, client = _one_event(_frame(transport=None))

        assert event is not None
        assert event.transport == feed.FEED_TRANSPORT_UNKNOWN == "UNKNOWN"
        assert handle.feed_transport == feed.FEED_TRANSPORT_UNKNOWN
        assert handle.feed_state == feed.FEED_STATE_TRANSPORT_UNKNOWN == "TRANSPORT_UNKNOWN"
        assert client.sessions[0]["feed_transport"] == "UNKNOWN"
        assert client.sessions[0]["feed_state"] == "TRANSPORT_UNKNOWN"

        assert event.transport != feed.FEED_TRANSPORT_WEBSOCKET
        assert handle.feed_state != feed.FEED_STATE_HEALTHY
        assert client.sessions[0]["feed_state"] != "HEALTHY"

    def test_an_unrecognised_transport_is_unknown_rather_than_stored_verbatim(
        self, metrics: _RecordingMetrics
    ) -> None:
        """``feed_transport`` is a closed vocabulary the simulator's gate reads."""
        event, handle, _client_ = _one_event(_frame(transport="CARRIER_PIGEON"))

        assert event is not None
        assert event.transport == feed.FEED_TRANSPORT_UNKNOWN
        assert handle.feed_state == feed.FEED_STATE_TRANSPORT_UNKNOWN

    def test_the_three_transports_map_to_three_distinct_states(self) -> None:
        """The table, asserted as a table: no two transports share a feed state."""
        mapping = feed.FEED_STATE_FOR_TRANSPORT

        assert set(mapping) == set(feed.FEED_TRANSPORTS)
        assert len(set(mapping.values())) == 3
        assert mapping[feed.FEED_TRANSPORT_WEBSOCKET] == feed.FEED_STATE_HEALTHY
        assert mapping[feed.FEED_TRANSPORT_REST] == feed.FEED_STATE_FALLBACK_REST
        assert mapping[feed.FEED_TRANSPORT_UNKNOWN] == feed.FEED_STATE_TRANSPORT_UNKNOWN

    def test_only_a_websocket_payload_reaches_the_healthy_state(self) -> None:
        """The negative form of the same table, which is what Requirement 18.15 reads."""
        healthy = [
            transport
            for transport, state in feed.FEED_STATE_FOR_TRANSPORT.items()
            if state == feed.FEED_STATE_HEALTHY
        ]
        assert healthy == [feed.FEED_TRANSPORT_WEBSOCKET]


# ══════════════════════════════════════════════════════════════════════════
# 8. paper.feed.latency_ms IS PROCESSING TIME MINUS THE EVENT'S OWN TIMESTAMP
#    (Requirements 14.10, 26.6)
# ══════════════════════════════════════════════════════════════════════════


class TestTheLatencyIsProcessingTimeMinusTheEventTimestamp:
    """Task 24.2's third bullet, as an exact figure.

    The processing instant is pinned by replacing ``paper_market_feed._utc_now`` - the one
    clock this module reads - so the expected latency is an exact ``Decimal`` rather than a
    tolerance. A tolerance would admit an implementation measuring something else entirely.

    Red run: ``_latency_ms`` was replaced with ``return Decimal(0)``. Every case in this class
    failed; ``test_the_figure_moves_with_the_events_timestamp`` failed on both figures being
    zero, which is the case that pins *which* two instants are subtracted.
    """

    @pytest.fixture(autouse=True)
    def _pinned_clock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(feed, "_utc_now", lambda: PROCESSED_AT)

    def test_the_figure_is_the_difference_between_the_two_instants(
        self, metrics: _RecordingMetrics
    ) -> None:
        event, _handle, _client_ = _one_event(_frame(timestamp_ms=_ms_before(1234)))

        assert event is not None
        assert event.received_at == PROCESSED_AT
        assert event.latency_ms == Decimal("1234.000")
        assert event.latency_ms == feed._latency_ms(PROCESSED_AT, event.event_timestamp)
        # The sub-millisecond scale is real, not an artefact of a whole-millisecond input:
        # ``NUMERIC(10,3)`` stores thousandths and the arithmetic produces them.
        assert feed._latency_ms(
            PROCESSED_AT, PROCESSED_AT - timedelta(microseconds=1500)
        ) == Decimal("1.500")

    def test_the_figure_moves_with_the_events_timestamp(
        self, metrics: _RecordingMetrics
    ) -> None:
        """Two candles, two ages, one clock: the difference is the timestamp difference.

        This is what makes the measurement "minus the event's own timestamp" rather than minus
        any other instant - an implementation reading its own arrival time twice would report
        the same figure for both.
        """
        first, _h1, _c1 = _one_event(_frame(timestamp_ms=_ms_before(90_000)))
        second, _h2, _c2 = _one_event(_frame(timestamp_ms=_ms_before(30_000)))

        assert first is not None and second is not None
        assert first.latency_ms == Decimal("90000.000")
        assert second.latency_ms == Decimal("30000.000")
        assert first.latency_ms - second.latency_ms == Decimal("60000.000")

    def test_the_persisted_figure_is_the_reported_figure(
        self, metrics: _RecordingMetrics
    ) -> None:
        """``paper_market_events.latency_ms`` is ``NUMERIC(10,3)``; quantised here, not there.

        So the recorded measurement and the reported one are one number rather than two that
        agree to three places by luck.
        """
        event, _handle, client = _one_event(_frame(timestamp_ms=_ms_before(1234)))

        assert event is not None
        row = client.market_events[0]
        assert row["latency_ms"] == str(event.latency_ms) == "1234.000"
        assert Decimal(row["latency_ms"]).as_tuple().exponent == -3

    def test_the_metric_carries_the_same_figure(
        self, metrics: _RecordingMetrics
    ) -> None:
        """Requirement 26.6: the emitted metric is the measurement, not a second computation."""
        event, _handle, _client_ = _one_event(_frame(timestamp_ms=_ms_before(250)))

        assert event is not None
        assert metrics.latencies == [(event.latency_ms, SYMBOL, TIMEFRAME)]
        assert metrics.latencies[0][0] == Decimal("250.000")
        assert metrics.events == [(SYMBOL, feed.FEED_TRANSPORT_WEBSOCKET)]

    def test_a_clock_disagreement_is_reported_and_not_clamped(self) -> None:
        """A candle whose open time is ahead of this process's clock yields a NEGATIVE figure.

        Reported as measured rather than floored at zero: the two clocks disagreeing is a real
        condition, and a zero would hide it behind a plausible number (Requirement 28.5).
        """
        ahead = PROCESSED_AT + timedelta(milliseconds=500)

        assert feed._latency_ms(PROCESSED_AT, ahead) == Decimal("-500.000")

    def test_the_module_reads_one_clock_and_no_price_comes_from_it(self) -> None:
        """``_utc_now`` is the only clock, which is what keeps a session replayable.

        Pinning it above changed the latency and the two ``*_at`` instants and nothing else -
        so if this file's fixture could change a price, every latency assertion here would be
        measuring a fabricated candle.
        """
        source = Path(inspect.getfile(feed)).read_text(encoding="utf-8")
        tree = ast.parse(source)
        clock_calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"now", "utcnow", "time", "monotonic", "today"}
        }
        assert clock_calls <= {"now"}


# ══════════════════════════════════════════════════════════════════════════
# 9. NO SECOND MARKET-DATA PATH (Requirements 14.2, 28.6)
# ══════════════════════════════════════════════════════════════════════════

#: Identifiers that would mean this module had opened its own feed. Matched **exactly** - not
#: as substrings - because ``FEED_TRANSPORT_WEBSOCKET`` is a legitimate name in this module
#: and a substring search for "websocket" would flag it.
FORBIDDEN_CALLS = frozenset(
    {
        "fetch_ohlcv",
        "fetchOHLCV",
        "watch_ohlcv",
        "watchOHLCV",
        "fetch_ticker",
        "fetch_order_book",
        "load_markets",
        "ws_connect",
        "websocket_connect",
        "create_connection",
        "WebSocketApp",
        "connect_ws",
    }
)

#: Top-level modules that are a transport or an exchange client. Importing one here would be a
#: second market-data path whatever it was used for.
FORBIDDEN_IMPORTS = frozenset(
    {
        "ccxt",
        "ccxtpro",
        "ccxt.pro",
        "websocket",
        "websockets",
        "aiohttp",
        "requests",
        "httpx",
        "urllib",
        "urllib3",
        "socket",
    }
)


def _feed_tree() -> ast.Module:
    return ast.parse(Path(inspect.getfile(feed)).read_text(encoding="utf-8"))


class TestThereIsNoSecondMarketDataPath:
    """Task 24.2: "No ``fetch_ohlcv`` loop, no second websocket, no third market-data path".

    Asserted over the module's **AST**, so the module docstring's own prose about
    ``fetch_ohlcv`` and ``watch_ohlcv`` - which describes what ``mds/main.py`` owns - does not
    trip the check. A string search would either fail on the documentation or would have to be
    weakened until it caught nothing.

    Red run: ``candles = await self.redis.fetch_ohlcv(self.config.symbol)`` was added inside
    ``FeedHandle.reconnect``. ``test_the_module_calls_no_exchange_or_transport_method`` failed
    naming ``fetch_ohlcv``; the import case stayed green, which is why both are asserted.
    """

    def test_the_module_calls_no_exchange_or_transport_method(self) -> None:
        tree = _feed_tree()
        called = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        } | {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}

        offenders = sorted(called & FORBIDDEN_CALLS)
        assert offenders == [], (
            f"paper_market_feed.py names {offenders} in code; the exchange feed, its "
            f"websocket and its REST fallback are mds/main.py's and a second copy here "
            f"would be a second answer (Requirement 14.2)"
        )

    def test_the_module_imports_no_exchange_client_and_no_transport(self) -> None:
        imported: set = set()
        for node in ast.walk(_feed_tree()):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        assert sorted(imported & FORBIDDEN_IMPORTS) == []

    def test_no_method_is_ever_called_on_the_resolved_exchange(self) -> None:
        """The ``exchange`` argument is inspected, never used as a feed.

        ``open_feed`` takes the resolved connection solely so Requirement 14.8's refusal can
        be a property of the object that was resolved. An attribute access on it would mean
        this module had started reading prices from it directly.
        """
        offenders = [
            node.attr
            for node in ast.walk(_feed_tree())
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "exchange"
        ]
        assert offenders == []

    def test_the_only_channels_named_are_the_two_mds_owns(self) -> None:
        """Every ``mds`` string literal in the module, enumerated.

        Exactly two: the command channel, whole, and the ``mds:data:`` prefix of the one
        f-string :func:`mds_data_channel` builds. A third literal would be a third
        market-data path even if it were spelled with the same prefix.
        """
        literals = {
            node.value
            for node in ast.walk(_feed_tree())
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("mds:")
        }
        assert literals == {"mds:commands", "mds:data:"}
        assert feed.mds_data_channel(EXCHANGE, SYMBOL).startswith("mds:data:")

    def test_the_normalisation_and_validation_are_the_platforms_own(self) -> None:
        """Requirement 14.2's other half: no second normalisation path either.

        The two modules the requirement names are imported and nothing parallel to them is
        defined here - asserted by the imports, since a re-implementation would not need them.
        """
        modules: set = set()
        for node in ast.walk(_feed_tree()):
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)

        assert "backend_app.backend.market_data_contract" in modules
        assert "backend_app.backend.market_data_validation" in modules

    def test_the_module_defines_no_polling_loop_over_a_sleep(self) -> None:
        """A ``while`` whose body sleeps is a poller. The one ``asyncio.sleep`` is the
        reconnection backoff of Requirement 14.5, and it is not inside a loop."""
        offenders: List[int] = []
        for node in ast.walk(_feed_tree()):
            if not isinstance(node, (ast.While, ast.For, ast.AsyncFor)):
                continue
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "sleep"
                ):
                    offenders.append(node.lineno)
        assert offenders == []

    def test_no_module_in_the_paper_package_imports_random(self) -> None:
        """Requirements 8.13, 14.9, 18.1: no paper code path reaches a randomised value.

        NOT the authoritative check. ``tests/test_paper_no_random.py`` (task 25.1) is, and it is
        strictly broader: same package and same two ``import random`` forms, plus dotted and
        aliased spellings, ``numpy.random``, any import of
        ``backend_app.backend.exchange_simulator``, dynamic imports, and a pinned first-party
        dependency list. Its
        ``test_the_narrower_predecessor_is_a_proper_subset_of_this_module`` runs *this* detector
        over the same files and asserts containment, so the two cannot drift apart silently.

        This one is kept, deliberately, as the feed suite's own local canary: a feed change that
        reached for a random jitter should turn **this** file red for a developer running only
        this file, without their needing to run the whole paper suite. Both exist; neither is a
        half-overlapping duplicate of the other.
        """
        package = Path(inspect.getfile(feed)).parent
        offenders: List[str] = []
        for path in sorted(package.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) and any(
                    alias.name.split(".")[0] == "random" for alias in node.names
                ):
                    offenders.append(path.name)
                elif (
                    isinstance(node, ast.ImportFrom)
                    and node.module
                    and node.module.split(".")[0] == "random"
                ):
                    offenders.append(path.name)
        assert offenders == []
