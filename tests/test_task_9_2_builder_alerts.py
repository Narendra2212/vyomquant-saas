"""strategy-builder task 9.2 - the four Strategy Builder alerts.

Requirements 24.4 and 24.5, plus ``design.md`` -> Observability's two unnumbered alerts
(training queue depth beyond threshold, asset-universe age beyond 12 h).

WHAT THIS SUITE IS ASSERTING, IN ONE SENTENCE PER GROUP
------------------------------------------------------
1. **The four conditions fire exactly where they should and nowhere else.** Each one has a
   near-miss that must stay silent, and each near-miss is a case that would otherwise train
   an operator to ignore the pager: a zero-size order for the non-finite alert, a paper
   deployment and an unmeasurable feed for the STALE alert, a queue one job short of the
   threshold, a universe exactly at 12 h.
2. **An alert never fails or delays the act it observes.** Asserted by breaking the alert
   path and checking that the intent is still refused, the feed is still classified and the
   response is still served - and by checking that delivery is scheduled onto the loop
   rather than awaited.
3. **Nothing identifying or secret reaches a payload.** Enforced by
   ``builder_alerts.safe_details`` and asserted over every alert all four conditions can
   produce, including under Hypothesis.
4. **Every threshold is the one an existing authority already owns.** The feed boundary is
   the classifier's, the queue threshold is the training pool's width, the universe
   threshold is task 7.1's TTL. Asserted by moving the authority and watching the threshold
   move with it.
"""

import asyncio
import logging

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

import backend_app.backend.builder_alerts as BA
import backend_app.backend.dag_engine as DE
import backend_app.backend.feed_state as FS
import backend_app.backend.metrics as M
from backend_app.core.alerting_system import AlertSeverity, AlertType

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


#: Requirement 19.6's five states, so a new one cannot be added without this suite noticing
#: that its alert behaviour was never decided.
ALL_FEED_STATES = ("LIVE", "DELAYED", "STALE", "DISCONNECTED", "INSUFFICIENT_DATA")

#: The bar labels the pipeline publishes an interval for, used where a real interval matters.
MEASURABLE_TIMEFRAMES = ("1m", "5m", "15m", "1h")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def collector(monkeypatch):
    """A fresh task 9.1 collector installed as the module singleton, per test.

    Same fixture as task 9.1's suite and for the same reason: every seam reaches the
    collector through a lazy import inside the function, so replacing the module attribute is
    enough. Fresh per test because the non-finite condition compares a counter against a
    watermark, and a shared counter would make the assertions order-dependent.
    """
    fresh = M.MetricsCollector()
    monkeypatch.setattr(M, "metrics_collector", fresh)
    return fresh


@pytest.fixture(autouse=True)
def clean_watermark():
    """The non-finite watermark is process-global. Reset around every test."""
    BA.reset_state()
    yield
    BA.reset_state()


@pytest.fixture
def no_delivery(monkeypatch):
    """Capture what ``raise_alerts`` was handed instead of delivering it.

    The conditions are the unit under test in most of this suite; delivery has its own group
    below. Capturing keeps the two apart, and keeps a webhook out of a condition test.
    """
    seen = []

    def capture(alerts):
        pending = [a for a in (alerts or []) if isinstance(a, BA.BuilderAlert)]
        seen.extend(pending)
        return len(pending)

    monkeypatch.setattr(BA, "raise_alerts", capture)
    return seen


def _feed(state="STALE", **kwargs):
    """A ``FeedStateReport`` produced by the real classifier, never hand-built.

    Hand-building the report would let this suite assert against a state the classifier
    cannot actually produce, which is the failure mode the alert is supposed to be immune to.
    So the inputs are chosen and the classifier decides.
    """
    presets = {
        "LIVE": dict(timeframe="5m", connected=True, age_seconds=10.0),
        "DELAYED": dict(timeframe="5m", connected=True, age_seconds=600.0),
        "STALE": dict(timeframe="5m", connected=True, age_seconds=5_000.0),
        "DISCONNECTED": dict(timeframe="5m", connected=False, age_seconds=5_000.0),
        "INSUFFICIENT_DATA": dict(
            timeframe="5m", connected=True, age_seconds=10.0, available_bars=1, warmup_bars=50
        ),
    }
    base = dict(presets[state])
    base.update(kwargs)
    return FS.evaluate_feed_state(**base)


# ═══════════════════════════════════════════════════════════════════════════
#  1. THE ALERTS JOIN THE PLATFORM'S OWN VOCABULARY
# ═══════════════════════════════════════════════════════════════════════════


class TestTheAlertTypesAreThePlatformsOwn:
    """No second alerting system. The four types live on ``core/alerting_system.py``."""

    @pytest.mark.parametrize("name", BA.BUILDER_ALERT_TYPE_NAMES)
    def test_each_builder_alert_type_is_registered_with_the_dispatcher(self, name):
        """A condition whose type the dispatcher does not know cannot be delivered."""
        assert hasattr(AlertType, name), (
            f"{name} is raised by builder_alerts but is not an AlertType, so "
            "`deliver` would drop it."
        )

    def test_there_are_exactly_four_builder_alert_types(self):
        """The task names four. A fifth would be a condition nobody agreed to page on."""
        registered = [m.name for m in AlertType if m.name.startswith("BUILDER_")]

        assert sorted(registered) == sorted(BA.BUILDER_ALERT_TYPE_NAMES)

    @pytest.mark.parametrize("severity", ["CRITICAL", "HIGH", "MEDIUM"])
    def test_each_severity_used_is_a_real_severity(self, severity):
        assert hasattr(AlertSeverity, severity)

    def test_the_two_correctness_alerts_are_critical_and_the_two_capacity_ones_are_not(
        self, collector, no_delivery
    ):
        """Severity is a routing decision: CRITICAL and HIGH reach email, MEDIUM does not.

        A refused order and a live deployment on a dead feed are correctness faults. A deep
        queue is capacity. Filing all four as CRITICAL would make the word meaningless.
        """
        collector.record_dag_intent_blocked_non_finite()
        non_finite = BA.intent_blocked_non_finite_condition()
        stale = BA.feed_state_condition(_feed("STALE"), "live")
        queue = BA.training_queue_depth_condition(10_000)
        universe = BA.asset_universe_age_condition(60 * 60 * 24 * 7)

        assert non_finite.severity_name == "CRITICAL"
        assert stale.severity_name == "CRITICAL"
        assert queue.severity_name == "MEDIUM"
        assert universe.severity_name == "HIGH"


# ═══════════════════════════════════════════════════════════════════════════
#  2. CONDITION 1 - ANY `dag.intents.blocked_non_finite` INCREMENT
# ═══════════════════════════════════════════════════════════════════════════


class TestTheNonFiniteIntentAlert:
    """**Validates: Requirements 24.4**"""

    def test_an_untouched_counter_raises_nothing(self, collector):
        """The counter is expected to stay at zero, and zero is not an increment."""
        assert BA.intent_blocked_non_finite_condition() is None

    def test_the_first_increment_raises_the_alert(self, collector):
        collector.record_dag_intent_blocked_non_finite()

        alert = BA.intent_blocked_non_finite_condition()

        assert alert is not None
        assert alert.alert_type_name == "BUILDER_INTENT_BLOCKED_NON_FINITE"
        assert alert.details["blocked_total"] == 1
        assert alert.details["risen_since_last_alert"] == 1

    def test_the_same_increment_is_not_reported_twice(self, collector):
        """The watermark. Two drivers read this condition; neither may page twice."""
        collector.record_dag_intent_blocked_non_finite()
        first = BA.intent_blocked_non_finite_condition()

        assert first is not None
        assert BA.intent_blocked_non_finite_condition() is None
        assert BA.intent_blocked_non_finite_condition() is None

    def test_a_further_increment_after_an_alert_raises_a_further_alert(self, collector):
        collector.record_dag_intent_blocked_non_finite()
        BA.intent_blocked_non_finite_condition()

        collector.record_dag_intent_blocked_non_finite()
        second = BA.intent_blocked_non_finite_condition()

        assert second is not None
        assert second.details["blocked_total"] == 2
        assert second.details["risen_since_last_alert"] == 1

    def test_a_burst_is_one_alert_naming_the_whole_rise(self, collector):
        """Seven refusals are one page that says seven, not seven pages."""
        for _ in range(7):
            collector.record_dag_intent_blocked_non_finite()

        alert = BA.intent_blocked_non_finite_condition()

        assert alert.details["risen_since_last_alert"] == 7
        assert "7 trade intent(s)" in alert.message

    def test_a_counter_that_went_backwards_is_not_an_increment(self, collector, monkeypatch):
        """A restarted process, or a replaced collector. Neither is a refused order."""
        collector.record_dag_intent_blocked_non_finite()
        collector.record_dag_intent_blocked_non_finite()
        assert BA.intent_blocked_non_finite_condition() is not None

        monkeypatch.setattr(M, "metrics_collector", M.MetricsCollector())

        assert BA.intent_blocked_non_finite_condition() is None

    def test_an_unreadable_counter_raises_nothing_rather_than_guessing(
        self, collector, monkeypatch
    ):
        monkeypatch.setattr(M, "metrics_collector", None)

        assert BA.blocked_non_finite_total() is None
        assert BA.intent_blocked_non_finite_condition() is None

    def test_the_message_says_no_order_was_sent(self, collector):
        """The operator's first question. The gate refused; nothing reached a venue."""
        collector.record_dag_intent_blocked_non_finite()

        alert = BA.intent_blocked_non_finite_condition()

        assert "No order was sent" in alert.message


class TestTheNonFiniteAlertAtTheIntentGate:
    """The wiring, driven through the real ``assert_execution_safe``.

    **Validates: Requirements 24.4**
    """

    def test_a_non_finite_quantity_refuses_the_intent_and_raises_the_alert(
        self, collector, no_delivery
    ):
        with pytest.raises(DE.ExecutionBlocked):
            DE.assert_execution_safe({"quantity": float("nan"), "price": 100.0}, "n_buy")

        assert collector.dag_intents_blocked_non_finite.total() == 1
        assert len(no_delivery) == 1
        assert no_delivery[0].alert_type_name == "BUILDER_INTENT_BLOCKED_NON_FINITE"

    def test_a_zero_size_order_is_refused_and_raises_no_alert(self, collector, no_delivery):
        """The load-bearing near-miss.

        ``assert_execution_safe`` also refuses ``NON_POSITIVE_QUANTITY``. Requirement 24.4's
        alert is on the non-finite arm, and this alert fires on *any* increment of an
        unlabelled counter - so folding the second arm in would page an operator every time
        an author submitted a zero-size order, which is an authoring mistake and not an
        incident.
        """
        with pytest.raises(DE.ExecutionBlocked) as blocked:
            DE.assert_execution_safe({"quantity": 0.0, "price": 100.0}, "n_buy")

        assert blocked.value.code == "NON_POSITIVE_QUANTITY"
        assert collector.dag_intents_blocked_non_finite.total() == 0
        assert no_delivery == []

    def test_a_safe_intent_raises_nothing(self, collector, no_delivery):
        DE.assert_execution_safe({"quantity": 1.5, "price": 100.0}, "n_buy")

        assert no_delivery == []

    def test_a_broken_alert_module_does_not_stop_an_intent_from_being_refused(
        self, collector, monkeypatch
    ):
        """The one that matters most: a refusal that stopped refusing would send the order."""

        def boom(*_a, **_k):
            raise RuntimeError("the alerting path exploded")

        monkeypatch.setattr(BA, "notice_intent_blocked_non_finite", boom)

        with pytest.raises(DE.ExecutionBlocked) as blocked:
            DE.assert_execution_safe({"quantity": float("inf")}, "n_buy")

        assert blocked.value.code == "NON_FINITE_ORDER_FIELD"

    def test_an_unimportable_alert_module_does_not_stop_the_refusal(
        self, collector, monkeypatch
    ):
        monkeypatch.setattr(DE, "_builder_alerts", lambda: None)

        with pytest.raises(DE.ExecutionBlocked):
            DE.assert_execution_safe({"quantity": float("nan")}, "n_buy")

        # The metric is still recorded: the two seams are independent.
        assert collector.dag_intents_blocked_non_finite.total() == 1


# ═══════════════════════════════════════════════════════════════════════════
#  3. CONDITION 2 - SUSTAINED `STALE` ON A LIVE DEPLOYMENT
# ═══════════════════════════════════════════════════════════════════════════


class TestTheStaleFeedAlert:
    """**Validates: Requirements 24.5**"""

    def test_a_live_deployment_on_a_measured_stale_feed_raises_the_alert(self, collector):
        report = _feed("STALE")

        alert = BA.feed_state_condition(report, "live")

        assert alert is not None
        assert alert.alert_type_name == "BUILDER_FEED_STALE"
        assert alert.details["state"] == "STALE"
        assert alert.details["reason"] == FS.REASON_AGE_OVER_STALE

    @pytest.mark.parametrize("mode", [None, "", "paper", "backtest", "PAPER"])
    def test_only_a_live_deployment_pages(self, collector, mode):
        """Real orders are what make a stopped feed an emergency.

        Requirement 24.5 is qualified "a live deployment". A paper run on a dead feed is a
        simulation producing nothing, and a preview is not a deployment at all - paging on
        either would teach an operator to dismiss the page that matters.
        """
        assert BA.feed_state_condition(_feed("STALE"), mode) is None

    def test_the_mode_is_matched_case_insensitively(self, collector):
        assert BA.feed_state_condition(_feed("STALE"), "LIVE") is not None
        assert BA.feed_state_condition(_feed("STALE"), " live ") is not None

    @pytest.mark.parametrize("state", ["LIVE", "DELAYED", "DISCONNECTED", "INSUFFICIENT_DATA"])
    def test_no_other_feed_state_pages(self, collector, state):
        assert BA.feed_state_condition(_feed(state), "live") is None

    def test_every_one_of_the_five_states_has_a_decided_answer(self, collector):
        """A sixth state added later must not silently inherit "pages" or "does not"."""
        fired = {
            state: BA.feed_state_condition(_feed(state), "live") is not None
            for state in ALL_FEED_STATES
        }

        assert fired == {
            "LIVE": False,
            "DELAYED": False,
            "STALE": True,
            "DISCONNECTED": False,
            "INSUFFICIENT_DATA": False,
        }

    def test_a_feed_that_never_delivered_anything_does_not_page(self, collector):
        """``STALE`` is also the classifier's fail-closed answer, and that is not a stop.

        ``NO_EVENT_OBSERVED`` is a feed that never started. It is correctly classified
        ``STALE`` - an unmeasured feed must never read ``LIVE`` - but it is not "the feed
        stopped three intervals ago", and an operator paged for it has nothing to act on.
        """
        report = FS.evaluate_feed_state(timeframe="5m", connected=True, age_seconds=None)

        assert report.state is FS.FeedState.STALE
        assert report.reason == FS.REASON_AGE_UNKNOWN
        assert BA.feed_state_condition(report, "live") is None

    def test_a_bar_label_with_no_published_interval_does_not_page(self, collector):
        """``EXPECTED_INTERVAL_NOT_PUBLISHED`` is a pipeline limitation, not a stopped feed."""
        report = FS.evaluate_feed_state(
            timeframe="7s", connected=True, age_seconds=99_999.0
        )

        assert report.state is FS.FeedState.STALE
        assert report.reason == FS.REASON_INTERVAL_UNKNOWN
        assert BA.feed_state_condition(report, "live") is None

    def test_the_payload_carries_the_classifiers_own_numbers(self, collector):
        """Requirement 19.8's figures, not a colour, and not a second derivation.

        ``stale_after_seconds`` is the classifier's ``3 x interval`` property. Reading it off
        the report is what makes it impossible for the panel and the pager to disagree about
        where three intervals falls.
        """
        report = FS.evaluate_feed_state(
            timeframe="1m", connected=True, age_seconds=300.0
        )

        alert = BA.feed_state_condition(report, "live")

        assert alert.details["expected_interval_seconds"] == 60
        assert alert.details["stale_after_seconds"] == report.stale_after_seconds == 180.0
        assert alert.details["age_seconds"] == 300.0
        assert alert.details["intervals_behind"] == 5.0
        assert alert.details["stale_at_intervals"] == FS.STALE_AT_INTERVALS

    def test_the_alert_boundary_is_the_classifiers_boundary_and_not_a_copy_of_it(
        self, collector
    ):
        """One below the boundary is ``DELAYED`` and silent; the boundary itself pages.

        Requirement 19.9 closes the boundary on the worse side - exactly ``3 i`` is
        ``STALE`` - and the alert inherits that rather than restating it.
        """
        interval = 60
        just_under = FS.evaluate_feed_state(
            timeframe="1m", connected=True, age_seconds=interval * 3 - 0.001
        )
        exactly_at = FS.evaluate_feed_state(
            timeframe="1m", connected=True, age_seconds=float(interval * 3)
        )

        assert just_under.state is FS.FeedState.DELAYED
        assert BA.feed_state_condition(just_under, "live") is None
        assert exactly_at.state is FS.FeedState.STALE
        assert BA.feed_state_condition(exactly_at, "live") is not None

    def test_no_report_at_all_raises_nothing(self, collector):
        assert BA.feed_state_condition(None, "live") is None


class TestTheStaleFeedAlertAtTheClassifier:
    """The wiring, through ``evaluate_feed_state``'s own report closure.

    **Validates: Requirements 24.5**
    """

    def test_the_classifier_raises_the_alert_for_a_live_deployment(
        self, collector, no_delivery
    ):
        FS.evaluate_feed_state(
            timeframe="5m", connected=True, age_seconds=5_000.0, deployment_mode="live"
        )

        assert len(no_delivery) == 1
        assert no_delivery[0].alert_type_name == "BUILDER_FEED_STALE"

    def test_the_default_caller_raises_nothing(self, collector, no_delivery):
        """The Builder's one existing feed-state caller previews a version. Not a deployment."""
        FS.evaluate_feed_state(timeframe="5m", connected=True, age_seconds=5_000.0)

        assert no_delivery == []

    @pytest.mark.parametrize("mode", [None, "paper", "live"])
    @pytest.mark.parametrize("state", ALL_FEED_STATES)
    def test_the_deployment_mode_changes_no_classification(self, collector, mode, state):
        """The new argument is an alert qualifier and nothing else.

        If passing a mode could move a state, this task would have changed what the feed
        panel reports - and task 7.4's whole point is that the panel reports a measurement.
        """
        without = _feed(state)
        with_mode = _feed(state, deployment_mode=mode)

        assert with_mode.state is without.state
        assert with_mode.reason == without.reason
        assert with_mode.age_seconds == without.age_seconds
        assert with_mode.expected_interval_seconds == without.expected_interval_seconds

    def test_the_deployment_mode_never_reaches_the_wire_form(self, collector):
        """It is a caller's fact, not a measurement, so it is not on the report's payload."""
        report = _feed("STALE", deployment_mode="live")

        assert "deployment_mode" not in report.to_dict()
        assert "mode" not in report.to_dict()

    def test_a_broken_alert_module_does_not_stop_a_feed_from_being_classified(
        self, collector, monkeypatch
    ):
        """``evaluate_feed_state`` documents that it never raises. No exceptions to that."""

        def boom(*_a, **_k):
            raise RuntimeError("the alerting path exploded")

        monkeypatch.setattr(BA, "notice_feed_state", boom)

        report = FS.evaluate_feed_state(
            timeframe="5m", connected=True, age_seconds=5_000.0, deployment_mode="live"
        )

        assert report.state is FS.FeedState.STALE
        assert report.reason == FS.REASON_AGE_OVER_STALE

    def test_an_unimportable_alert_module_does_not_stop_classification(
        self, collector, monkeypatch
    ):
        monkeypatch.setattr(FS, "_builder_alerts", lambda: None)

        report = FS.evaluate_feed_state(
            timeframe="5m", connected=True, age_seconds=10.0, deployment_mode="live"
        )

        assert report.state is FS.FeedState.LIVE
        # And the metric is still recorded: the two seams are independent.
        assert collector.market_data_feed_state.total() == 1


# ═══════════════════════════════════════════════════════════════════════════
#  4. CONDITION 3 - TRAINING QUEUE DEPTH BEYOND THRESHOLD
# ═══════════════════════════════════════════════════════════════════════════


class TestTheTrainingQueueDepthAlert:
    def test_the_threshold_is_drain_cycles_of_the_pool_width(self, monkeypatch):
        """Not an absolute. An operator who widens the fleet stops being paged."""
        monkeypatch.setattr(BA, "training_pool_width", lambda: 8)

        assert BA.training_queue_depth_threshold() == BA.TRAINING_QUEUE_DRAIN_CYCLES * 8

        monkeypatch.setattr(BA, "training_pool_width", lambda: 40)

        assert BA.training_queue_depth_threshold() == BA.TRAINING_QUEUE_DRAIN_CYCLES * 40

    def test_the_pool_width_is_the_training_policys_own_figure(self):
        """One number, so the alert and Requirement 16.5's saturation check agree."""
        from backend_app.backend.ml_training_policy import (
            DEFAULT_MAX_CONCURRENT_JOBS_GLOBAL,
        )

        assert BA.training_pool_width() == int(DEFAULT_MAX_CONCURRENT_JOBS_GLOBAL)

    def test_an_operator_can_name_an_absolute_depth(self, monkeypatch):
        monkeypatch.setenv("ML_TRAINING_QUEUE_DEPTH_ALERT", "500")

        assert BA.training_queue_depth_threshold() == 500

    @pytest.mark.parametrize("junk", ["", "0", "-3", "lots"])
    def test_an_unusable_override_falls_back_to_the_derived_threshold(
        self, monkeypatch, junk
    ):
        monkeypatch.setenv("ML_TRAINING_QUEUE_DEPTH_ALERT", junk)
        monkeypatch.setattr(BA, "training_pool_width", lambda: 8)

        assert BA.training_queue_depth_threshold() == BA.TRAINING_QUEUE_DRAIN_CYCLES * 8

    def test_a_queue_at_the_threshold_pages(self, monkeypatch):
        monkeypatch.setattr(BA, "training_queue_depth_threshold", lambda: 32)

        alert = BA.training_queue_depth_condition(32)

        assert alert is not None
        assert alert.alert_type_name == "BUILDER_TRAINING_QUEUE_DEPTH"
        assert alert.details["queued"] == 32
        assert alert.details["threshold"] == 32

    def test_a_queue_one_job_short_does_not_page(self, monkeypatch):
        monkeypatch.setattr(BA, "training_queue_depth_threshold", lambda: 32)

        assert BA.training_queue_depth_condition(31) is None

    def test_an_empty_queue_does_not_page(self):
        assert BA.training_queue_depth_condition(0) is None

    @pytest.mark.parametrize("junk", [None, "many", -1, object()])
    def test_an_unreadable_depth_raises_nothing_rather_than_guessing(self, junk):
        assert BA.training_queue_depth_condition(junk) is None

    def test_the_message_names_the_pool_the_queue_is_measured_against(self, monkeypatch):
        monkeypatch.setattr(BA, "training_pool_width", lambda: 8)
        monkeypatch.delenv("ML_TRAINING_QUEUE_DEPTH_ALERT", raising=False)

        alert = BA.training_queue_depth_condition(64)

        assert alert.details["pool_width"] == 8
        assert alert.details["drain_cycles_now"] == 8.0
        assert "nothing has been lost" in alert.suggested_action


class TestTheTrainingQueueAlertAtTheWorker:
    """The wiring, through ``count_all_training_jobs``' service-role count."""

    @staticmethod
    def _sb_with(rows):
        """The smallest object that satisfies the query chain the function builds."""

        class _Q:
            def select(self, *_a, **_k):
                return self

            def in_(self, *_a, **_k):
                return self

            def execute(self):
                return self

        class _SB:
            def table(self, *_a, **_k):
                return _Q()

        class _Result:
            data = rows

        return _SB(), _Result()

    @pytest.mark.asyncio
    async def test_the_authoritative_queued_count_drives_the_alert(
        self, monkeypatch, no_delivery
    ):
        import backend_app.backend.strategy_service as S
        import backend_app.backend.training_worker as TW

        rows = [{"id": f"j{i}", "status": "QUEUED", "user_id": "u"} for i in range(40)]
        sb, result = self._sb_with(rows)

        async def fake_execute(_query):
            return result

        monkeypatch.setattr(S, "_execute", fake_execute)
        monkeypatch.setattr(BA, "training_queue_depth_threshold", lambda: 32)

        counts = await TW.count_all_training_jobs(sb, "u")

        assert counts.available is True
        assert counts.global_queued == 40
        assert len(no_delivery) == 1
        assert no_delivery[0].details["queued"] == 40

    @pytest.mark.asyncio
    async def test_a_queue_under_the_threshold_pages_nothing(self, monkeypatch, no_delivery):
        import backend_app.backend.strategy_service as S
        import backend_app.backend.training_worker as TW

        rows = [{"id": f"j{i}", "status": "QUEUED", "user_id": "u"} for i in range(3)]
        sb, result = self._sb_with(rows)

        async def fake_execute(_query):
            return result

        monkeypatch.setattr(S, "_execute", fake_execute)
        monkeypatch.setattr(BA, "training_queue_depth_threshold", lambda: 32)

        counts = await TW.count_all_training_jobs(sb, "u")

        assert counts.global_queued == 3
        assert no_delivery == []

    @pytest.mark.asyncio
    async def test_an_unreadable_queue_pages_nothing(self, no_delivery):
        """Migrations 004-004e are unapplied locally.

        An absent ``training_jobs`` degrades to ``JobCounts.unavailable`` naming
        ``004d_training_and_models.sql``. An unreadable queue is not a deep one, so nothing
        is raised - the alert must not turn a migration gap into a capacity page.
        """
        import backend_app.backend.training_worker as TW

        counts = await TW.count_all_training_jobs(None, "u")

        assert counts.available is False
        assert no_delivery == []

    @pytest.mark.asyncio
    async def test_a_broken_alert_module_does_not_stop_the_count(self, monkeypatch):
        """The caps below this count are a control. It has to return."""
        import backend_app.backend.strategy_service as S
        import backend_app.backend.training_worker as TW

        rows = [{"id": f"j{i}", "status": "QUEUED", "user_id": "u"} for i in range(40)]
        sb, result = self._sb_with(rows)

        async def fake_execute(_query):
            return result

        def boom(*_a, **_k):
            raise RuntimeError("the alerting path exploded")

        monkeypatch.setattr(S, "_execute", fake_execute)
        monkeypatch.setattr(BA, "notice_training_queue_depth", boom)

        counts = await TW.count_all_training_jobs(sb, "u")

        assert counts.global_queued == 40
        assert counts.available is True


# ═══════════════════════════════════════════════════════════════════════════
#  5. CONDITION 4 - ASSET UNIVERSE AGE BEYOND 12 h
# ═══════════════════════════════════════════════════════════════════════════


class TestTheAssetUniverseAgeAlert:
    def test_the_threshold_is_two_of_task_7_1s_ttls(self):
        """12 h, derived rather than written down. Requirement 25.5 fixes the TTL at 6 h."""
        from backend_app.backend.asset_universe import UNIVERSE_TTL_SECONDS

        assert BA.universe_ttl_seconds() == UNIVERSE_TTL_SECONDS == 6 * 60 * 60
        assert BA.UNIVERSE_AGE_TTL_MULTIPLE == 2
        assert BA.universe_age_threshold_seconds() == 12 * 60 * 60

    def test_the_threshold_follows_the_ttl_rather_than_a_constant(self, monkeypatch):
        """An operator who moves the TTL moves the alert. 43200 would not have moved."""
        monkeypatch.setattr(BA, "universe_ttl_seconds", lambda: 3_600)

        assert BA.universe_age_threshold_seconds() == 7_200

    def test_a_universe_older_than_twelve_hours_pages(self, collector):
        alert = BA.asset_universe_age_condition(12 * 60 * 60 + 1)

        assert alert is not None
        assert alert.alert_type_name == "BUILDER_ASSET_UNIVERSE_STALE"
        assert alert.details["threshold_seconds"] == 12 * 60 * 60
        assert alert.details["ttl_seconds"] == 6 * 60 * 60

    def test_a_universe_exactly_at_twelve_hours_does_not_page(self, collector):
        """The near-miss. 12 h is the last acceptable age, not the first unacceptable one."""
        assert BA.asset_universe_age_condition(12 * 60 * 60) is None

    def test_a_universe_one_ttl_old_does_not_page(self, collector):
        """One TTL is a due refresh, not a fault. Two is four missed passes."""
        assert BA.asset_universe_age_condition(6 * 60 * 60 + 1) is None

    def test_the_gauge_is_the_default_source(self, collector):
        collector.set_asset_universe_age(60 * 60 * 20)

        alert = BA.asset_universe_age_condition()

        assert alert is not None
        assert alert.details["age_seconds"] == float(60 * 60 * 20)

    def test_an_unset_gauge_is_not_an_age_of_zero_and_does_not_page(self, collector):
        """Task 9.1 returns ``None`` for a gauge nobody set. Zero would read "just now"."""
        assert collector.builder_assets_universe_age_seconds.get() is None
        assert BA.asset_universe_age_condition() is None

    @pytest.mark.parametrize(
        "junk", [None, "old", float("nan"), float("inf"), float("-inf"), -1.0]
    )
    def test_an_unusable_age_raises_nothing_rather_than_guessing(self, collector, junk):
        assert BA.asset_universe_age_condition(junk) is None

    def test_the_message_says_what_an_author_is_actually_looking_at(self, collector):
        alert = BA.asset_universe_age_condition(60 * 60 * 30)

        assert "delisted" in alert.message
        assert alert.details["age_text"] == "1d 06h"


# ═══════════════════════════════════════════════════════════════════════════
#  6. PAYLOAD HYGIENE  (SB-06 / Requirement 12.1, and cardinality)
# ═══════════════════════════════════════════════════════════════════════════


def _every_alert(collector):
    """One of each. Every alert this module can produce, for the hygiene assertions."""
    collector.record_dag_intent_blocked_non_finite()
    return [
        BA.intent_blocked_non_finite_condition(),
        BA.feed_state_condition(_feed("STALE"), "live"),
        BA.training_queue_depth_condition(100_000),
        BA.asset_universe_age_condition(60 * 60 * 48),
    ]


class TestNothingIdentifyingOrSecretReachesAnAlert:
    def test_all_four_conditions_produced_an_alert_for_these_assertions(self, collector):
        """A guard on the guard: an assertion over an empty list proves nothing."""
        assert all(a is not None for a in _every_alert(collector))

    def test_no_payload_key_names_a_credential(self, collector):
        offenders = {
            alert.alert_type_name: sorted(
                k for k in alert.details if BA._key_words(k) & BA.CREDENTIAL_WORDS
            )
            for alert in _every_alert(collector)
            if any(BA._key_words(k) & BA.CREDENTIAL_WORDS for k in alert.details)
        }

        assert offenders == {}, f"SB-06 / Requirement 12.1: {offenders}"

    def test_no_payload_key_names_a_tenant_or_a_resource(self, collector):
        offenders = {
            alert.alert_type_name: sorted(
                k for k in alert.details if BA._key_words(k) & BA.IDENTITY_WORDS
            )
            for alert in _every_alert(collector)
            if any(BA._key_words(k) & BA.IDENTITY_WORDS for k in alert.details)
        }

        assert offenders == {}

    def test_the_filter_drops_an_identity_a_future_caller_adds(self):
        """A filter, not a review convention. The next author cannot leak by forgetting."""
        clean = BA.safe_details(
            {
                "queued": 40,
                "user_id": "u-1",
                "tenant_id": "t-1",
                "strategy_id": "s-1",
                "version_id": "v-1",
                "deployment_id": "d-1",
                "job_id": "j-1",
                "node_id": "n-1",
                "email": "a@b.c",
                "session_id": "sess",
                "request_id": "req",
                "ip": "10.0.0.1",
            }
        )

        assert clean == {"queued": 40}

    def test_the_filter_drops_a_credential_a_future_caller_adds(self):
        clean = BA.safe_details(
            {
                "timeframe": "5m",
                "exchange": "binance",
                "exchange_id": "binance",
                "venue": "binance",
                "api_key": "AK",
                "secret": "S",
                "passphrase": "P",
                "password": "P",
                "token": "T",
                "credentials": {"k": "v"},
            }
        )

        assert clean == {"timeframe": "5m"}

    @pytest.mark.parametrize(
        "keep", ["timeframe", "state", "reason", "mode", "metric", "queued", "threshold"]
    )
    def test_the_filter_keeps_the_vocabulary_words_the_conditions_use(self, keep):
        """Word-wise, not substring-wise: ``timeframe`` survives a ``time``/``frame`` split."""
        assert BA.safe_details({keep: "x"}) == {keep: "x"}

    def test_a_payload_value_is_bounded(self):
        clean = BA.safe_details({"reason": "R" * 5_000})

        assert len(clean["reason"]) == BA.DETAIL_VALUE_MAX_LENGTH

    def test_no_message_or_title_carries_a_market_or_a_venue(self, collector):
        """The feed report the classifier returns carries no symbol, so neither can this.

        ``market_data.feed_state`` deliberately has no ``symbol`` label (cardinality), and
        ``FeedStateReport`` has no symbol field at all - so the alert cannot name a market
        even by accident, and it cannot name a venue, which is a deployment fact (SB-06).
        """
        for alert in _every_alert(collector):
            blob = f"{alert.title} {alert.message} {alert.details}".lower()

            assert "binance" not in blob
            assert "usdt" not in blob
            assert "api_key" not in blob


class TestDeliveryCarriesNoTenant:
    @pytest.mark.asyncio
    async def test_the_dispatcher_is_handed_no_tenant_and_no_execution(
        self, collector, monkeypatch
    ):
        """The dedup key is ``(type, source, tenant_id)``.

        ``None`` is what makes one condition one dedup identity. A tenant here would both
        name a customer on a platform-health page and multiply the dedup key by the customer
        count, which is how a five-minute window stops deduplicating.
        """
        captured = {}

        class _System:
            async def send_alert(self, **kwargs):
                captured.update(kwargs)
                return "sent"

        monkeypatch.setattr(BA, "_alerting", lambda: (_System(), AlertType, AlertSeverity))
        collector.record_dag_intent_blocked_non_finite()

        result = await BA.deliver(BA.intent_blocked_non_finite_condition())

        assert result == "sent"
        assert captured["tenant_id"] is None
        assert captured["execution_id"] is None
        assert captured["alert_type"] is AlertType.BUILDER_INTENT_BLOCKED_NON_FINITE
        assert captured["severity"] is AlertSeverity.CRITICAL
        assert captured["source"] == BA.SOURCE_INTENT_GATE


# ═══════════════════════════════════════════════════════════════════════════
#  7. AN ALERT NEVER FAILS OR DELAYS THE ACT IT OBSERVES
# ═══════════════════════════════════════════════════════════════════════════


class TestDeliveryIsScheduledAndNeverAwaited:
    def test_with_no_running_loop_the_alert_is_logged_and_nothing_blocks(
        self, collector, caplog
    ):
        """``asyncio.run`` here would block the intent gate on a webhook. It is refused.

        The alert is not lost: it goes to the log at its own severity, which is the
        dispatcher's first channel anyway.
        """
        collector.record_dag_intent_blocked_non_finite()
        alert = BA.intent_blocked_non_finite_condition()

        with caplog.at_level(logging.CRITICAL, logger="BuilderAlerts"):
            scheduled = BA.raise_alerts([alert])

        assert scheduled == 0
        assert "Trade intent blocked" in caplog.text

    @pytest.mark.asyncio
    async def test_with_a_running_loop_delivery_is_scheduled_not_awaited(
        self, collector, monkeypatch
    ):
        delivered = []

        class _System:
            async def send_alert(self, **kwargs):
                await asyncio.sleep(0)
                delivered.append(kwargs["source"])
                return "sent"

        monkeypatch.setattr(BA, "_alerting", lambda: (_System(), AlertType, AlertSeverity))
        collector.record_dag_intent_blocked_non_finite()
        alert = BA.intent_blocked_non_finite_condition()

        scheduled = BA.raise_alerts([alert])

        # Scheduled, and demonstrably not yet delivered when the call returned.
        assert scheduled == 1
        assert delivered == []
        await asyncio.sleep(0.05)
        assert delivered == [BA.SOURCE_INTENT_GATE]

    def test_an_empty_or_junk_batch_schedules_nothing_and_raises_nothing(self):
        assert BA.raise_alerts([]) == 0
        assert BA.raise_alerts(None) == 0
        assert BA.raise_alerts(["not an alert", 7]) == 0

    @pytest.mark.asyncio
    async def test_an_unreachable_dispatcher_logs_rather_than_raising(
        self, collector, monkeypatch, caplog
    ):
        monkeypatch.setattr(BA, "_alerting", lambda: (None, None, None))
        collector.record_dag_intent_blocked_non_finite()

        with caplog.at_level(logging.CRITICAL, logger="BuilderAlerts"):
            result = await BA.deliver(BA.intent_blocked_non_finite_condition())

        assert result is None
        assert "could not be reached" in caplog.text

    @pytest.mark.asyncio
    async def test_an_unknown_alert_type_logs_rather_than_raising(self, monkeypatch, caplog):
        """A condition whose type was never registered still reaches the log."""
        unknown = BA.BuilderAlert(
            alert_type_name="BUILDER_NOT_REGISTERED",
            severity_name="CRITICAL",
            title="t",
            message="m",
            source="s",
        )

        with caplog.at_level(logging.CRITICAL, logger="BuilderAlerts"):
            result = await BA.deliver(unknown)

        assert result is None
        assert "is not a known alert" in caplog.text

    @pytest.mark.parametrize(
        "notice",
        [
            "notice_intent_blocked_non_finite",
            "notice_feed_state",
            "notice_training_queue_depth",
            "notice_asset_universe_age",
        ],
    )
    def test_every_seam_entry_point_is_total(self, collector, monkeypatch, notice):
        """A condition that raised would propagate into a control. It cannot.

        Every entry point is wrapped in ``_never_fails``, and this breaks the shared
        dispatch so that all four take the failing path.
        """

        def boom(*_a, **_k):
            raise RuntimeError("everything is on fire")

        monkeypatch.setattr(BA, "raise_alerts", boom)
        collector.record_dag_intent_blocked_non_finite()

        arguments = {
            "notice_intent_blocked_non_finite": (),
            "notice_feed_state": (_feed("STALE"), "live"),
            "notice_training_queue_depth": (100_000,),
            "notice_asset_universe_age": (60 * 60 * 48,),
        }[notice]

        assert getattr(BA, notice)(*arguments) is None


# ═══════════════════════════════════════════════════════════════════════════
#  8. THE PULL PATH
# ═══════════════════════════════════════════════════════════════════════════


class TestTheMetricReadablePullPath:
    def test_a_quiet_platform_evaluates_to_no_alerts(self, collector):
        assert BA.evaluate() == []

    def test_both_metric_readable_conditions_are_evaluated(self, collector):
        collector.record_dag_intent_blocked_non_finite()
        collector.set_asset_universe_age(60 * 60 * 48)

        names = {alert.alert_type_name for alert in BA.evaluate()}

        assert names == {
            "BUILDER_INTENT_BLOCKED_NON_FINITE",
            "BUILDER_ASSET_UNIVERSE_STALE",
        }

    def test_one_failing_condition_does_not_suppress_the_other(self, collector, monkeypatch):
        def boom():
            raise RuntimeError("condition exploded")

        monkeypatch.setattr(BA, "intent_blocked_non_finite_condition", boom)
        collector.set_asset_universe_age(60 * 60 * 48)

        alerts = BA.evaluate()

        assert [a.alert_type_name for a in alerts] == ["BUILDER_ASSET_UNIVERSE_STALE"]


# ═══════════════════════════════════════════════════════════════════════════
#  9. PROPERTIES
# ═══════════════════════════════════════════════════════════════════════════

_SETTINGS = settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


class TestProperties:
    """The four boundaries, over the input space rather than at chosen points."""

    @_SETTINGS
    @given(
        timeframe=st.sampled_from(MEASURABLE_TIMEFRAMES),
        multiple=st.floats(min_value=0.0, max_value=12.0, allow_nan=False),
    )
    def test_the_stale_alert_fires_exactly_when_the_classifier_says_stale(
        self, collector, timeframe, multiple
    ):
        """**Validates: Requirements 24.5**

        The alert has no boundary of its own. For any age at all, it fires if and only if
        the classifier reported ``STALE`` by measurement - so the pager cannot disagree with
        the panel about whether a feed has stopped, whatever the interval.
        """
        interval = FS.expected_interval_seconds(timeframe)
        report = FS.evaluate_feed_state(
            timeframe=timeframe, connected=True, age_seconds=interval * multiple
        )

        fired = BA.feed_state_condition(report, "live") is not None
        classifier_said_measured_stale = (
            report.state is FS.FeedState.STALE
            and report.reason == FS.REASON_AGE_OVER_STALE
        )

        assert fired is classifier_said_measured_stale
        assert fired == (multiple >= FS.STALE_AT_INTERVALS)

    @_SETTINGS
    @given(
        timeframe=st.sampled_from(MEASURABLE_TIMEFRAMES),
        multiple=st.floats(min_value=0.0, max_value=12.0, allow_nan=False),
        mode=st.sampled_from([None, "", "paper", "backtest", "cloud"]),
    )
    def test_no_age_at_all_pages_when_the_deployment_is_not_live(
        self, collector, timeframe, multiple, mode
    ):
        """**Validates: Requirements 24.5**

        Requirement 24.5's "live deployment" qualifier holds for every age. There is no
        staleness so extreme that a paper run or a preview becomes an emergency.
        """
        interval = FS.expected_interval_seconds(timeframe)
        report = FS.evaluate_feed_state(
            timeframe=timeframe, connected=True, age_seconds=interval * multiple
        )

        assert BA.feed_state_condition(report, mode) is None

    @_SETTINGS
    @given(depth=st.integers(min_value=0, max_value=100_000))
    def test_the_queue_alert_fires_exactly_at_or_above_the_threshold(self, depth):
        threshold = BA.training_queue_depth_threshold()

        fired = BA.training_queue_depth_condition(depth) is not None

        assert fired is (depth >= threshold)

    @_SETTINGS
    @given(
        age=st.floats(
            min_value=0.0, max_value=60 * 60 * 24 * 30, allow_nan=False, allow_infinity=False
        )
    )
    def test_the_universe_alert_fires_exactly_above_twelve_hours(self, collector, age):
        threshold = BA.universe_age_threshold_seconds()

        fired = BA.asset_universe_age_condition(age) is not None

        assert fired is (age > threshold)

    @_SETTINGS
    @given(increments=st.lists(st.integers(min_value=1, max_value=50), min_size=1, max_size=12))
    def test_every_increment_is_eventually_reported_exactly_once(self, collector, increments):
        """**Validates: Requirements 24.4**

        Requirement 24.4 is "when a non-finite Trade_Intent is blocked, raise an alert". Over
        any sequence of bursts: every refusal is accounted for by exactly one alert's
        reported rise, and no refusal is reported twice. That is what makes the watermark
        correct rather than merely quiet.
        """
        # A fresh counter and a fresh watermark per example. The ``collector`` fixture is
        # function-scoped and Hypothesis runs many examples inside one function call, so a
        # shared counter would make this property depend on how many examples ran before it.
        # The fixture's ``monkeypatch`` still restores the real singleton at teardown.
        fresh = M.MetricsCollector()
        M.metrics_collector = fresh
        collector = fresh
        BA.reset_state()
        reported = 0.0
        for burst in increments:
            for _ in range(burst):
                collector.record_dag_intent_blocked_non_finite()
            alert = BA.intent_blocked_non_finite_condition()
            assert alert is not None, "a burst that raised the counter must raise an alert"
            reported += alert.details["risen_since_last_alert"]
            # And a second read with no new refusal reports nothing.
            assert BA.intent_blocked_non_finite_condition() is None

        assert reported == sum(increments)
        assert reported == collector.dag_intents_blocked_non_finite.total()

    @_SETTINGS
    @given(
        timeframe=st.sampled_from(MEASURABLE_TIMEFRAMES),
        multiple=st.floats(min_value=3.0, max_value=500.0, allow_nan=False),
        depth=st.integers(min_value=0, max_value=100_000),
        age=st.floats(min_value=0.0, max_value=60 * 60 * 24 * 60, allow_nan=False),
    )
    def test_no_alert_any_condition_can_produce_carries_an_identity_or_a_credential(
        self, collector, timeframe, multiple, depth, age
    ):
        """SB-06 / Requirement 12.1 and cardinality, over the whole input space.

        A chosen-example assertion would only cover the payloads the examples happened to
        produce. This covers every payload the four conditions can build.
        """
        collector.record_dag_intent_blocked_non_finite()
        interval = FS.expected_interval_seconds(timeframe)
        candidates = [
            BA.intent_blocked_non_finite_condition(),
            BA.feed_state_condition(
                FS.evaluate_feed_state(
                    timeframe=timeframe, connected=True, age_seconds=interval * multiple
                ),
                "live",
            ),
            BA.training_queue_depth_condition(depth),
            BA.asset_universe_age_condition(age),
        ]
        alerts = [a for a in candidates if a is not None]
        assume(alerts)

        for alert in alerts:
            for key in alert.details:
                assert not BA.forbidden_detail_key(key), f"{alert.alert_type_name}.{key}"
