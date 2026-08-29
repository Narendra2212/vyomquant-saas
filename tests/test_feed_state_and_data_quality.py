"""tests/test_feed_state_and_data_quality.py

Stale data must never be labelled ``LIVE``. That is the whole property.

Spec: strategy-builder task 7.4. ``design.md`` -> Data preview and honesty ("Feed state is
reported literally"), and its API table's
``GET /api/strategy-operations/strategies/{id}/data-quality``. Requirements 19.6, 19.7, 19.8,
19.9, 19.10, 19.14, plus the controls the endpoint must not weaken: 21.4 (auth and tenant
isolation), 12.1 / SB-06 (no venue in a request or a response).

THE DEFECT CLASS THIS FILE EXISTS FOR
-------------------------------------
A green dot rendered because a socket object exists, over a chart whose last candle arrived
forty minutes ago. Every failure mode of that shape is an *optimistic default*: a state that
decays to fresh, an unknown age read as zero, an interval guessed when none is published, a
cached liveness reading served as a current one. So the assertions here are not "the endpoint
returns a plausible state". They are:

* **Property 26, by construction.** Over a deterministic sweep of injected ages against every
  bar interval the pipeline publishes, anything reported ``LIVE`` has an age strictly below
  1.5 x its expected interval - and the sweep includes the exact boundary, which is the value
  a ``<=`` would get wrong. (Task 7.7 owns the generated ``hypothesis`` version of this in
  ``tests/test_feed_state_property.py``; this is the deterministic table.)
* **Nothing unmeasured reads as fresh.** An unknown age, an unpublished interval, a
  transport whose state cannot be read, an observation source that raised - each is asserted
  to produce a non-``LIVE`` state with ``age_seconds: null``, never ``0``.
* **The five states stay five, and stay distinct.** ``DISCONNECTED`` and
  ``INSUFFICIENT_DATA`` are asserted not to collapse into one another or into ``STALE``.
* **The numbers are on the wire.** Requirement 19.8 asks for the age *together with* the
  expected interval; both are asserted present, in seconds and rendered, on every state.
* **The report is read, not recomputed.** The quality body is asserted to be
  ``market_data_validation``'s own ``DataQualityReport.to_dict()``, produced by that module's
  own singleton validator, with the strict outlier posture intact - a rejected window is
  asserted to arrive classified as ``DATA_QUALITY``, not softened and not a 500.

WHAT IS SUPPLIED AND WHAT IS REAL
---------------------------------
Supplied, and all of it outside the property under test: the OHLCV window (the endpoint's one
I/O boundary - there is no venue reachable from this environment), the strategy row (a fake
service applying the real ``id`` **and** ``user_id`` predicate, so the cross-tenant 404 is a
real one), and the connection monitor's contents (a real ``WebSocketMonitor`` with connections
registered and their last-message timestamps set, because no feed runs here).

Real: the registry, the graphs, ``load_graph``, ``StrategyCompiler``, ``plan.warmup_bars``,
``feed_state``'s classifier, ``market_data_validation``'s validator and its thresholds, and
the router with its auth dependency and its limiter.
"""

import ast
import inspect
import os
import sys
import time

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import feed_state as FS
from backend_app.backend import market_data_validation as MDV
from backend_app.backend import websocket_monitor as WM
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag import validator as V
from backend_app.backend.strategy_dag.schema import (EdgeSpec, NodeSpec,
                                                     StrategyGraph)
from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.routers import strategy_operations as SO

#: The five states Requirement 19.6 permits. Written out here on purpose: this is the one
#: place a literal list is the point, because the requirement fixes the vocabulary.
REQUIRED_STATES = {"LIVE", "DELAYED", "STALE", "DISCONNECTED", "INSUFFICIENT_DATA"}

OWNER = {
    "id": "usr_feed_owner",
    "email": "owner@example.com",
    "role": "authenticated",
    "access_token": "token_owner",
}
INTRUDER = {
    "id": "usr_feed_intruder",
    "email": "intruder@example.com",
    "role": "authenticated",
    "access_token": "token_intruder",
}

STRATEGY_ID = "stg_feed_0001"
SYMBOL = "ETH/USDT"
TIMEFRAME = "5m"
INTERVAL_SECONDS = 300  # 5m, from market_data_validation.TIMEFRAME_MINUTES

#: Bars the fake feed can serve. Larger than the endpoint's own floor so the window it asks
#: for is what decides, not what the pool happens to hold.
POOL_BARS = 900


def quality_path(strategy_id: str = STRATEGY_ID) -> str:
    return f"/api/strategy-operations/strategies/{strategy_id}/data-quality"


# ---------------------------------------------------------------------------
# The real registry and real graphs
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reg():
    return registry_module.get_registry()


def _node(reg, block_id, **params):
    return NodeSpec.create(block_id, reg[block_id].category, params=params)


def _required(descriptor):
    values = {}
    for spec in descriptor.params:
        value = spec.default if spec.default is not None else spec.example
        if spec.required and value is not None:
            values[spec.key] = value
    return values


def _graph(reg, indicator_block, indicator_params, *, symbol=SYMBOL, timeframe=TIMEFRAME):
    """``ohlcv_feed -> <indicator> -> gt -> action``, validated by the real validator."""
    data = _node(
        reg,
        "ohlcv_feed",
        symbol=symbol,
        timeframe=timeframe,
        market_type="spot",
        mode="streaming",
    )
    indicator = _node(reg, indicator_block, **indicator_params)
    gate = _node(reg, "gt", **_required(reg["gt"]))
    threshold = _node(reg, "constant", value=0.5)
    action = _node(reg, "action_buy_market", **_required(reg["action_buy_market"]))
    graph = StrategyGraph(
        nodes=[data, indicator, gate, threshold, action],
        edges=[
            EdgeSpec.create(data.id, "close", indicator.id, "series"),
            EdgeSpec.create(indicator.id, "value", gate.id, "left"),
            EdgeSpec.create(threshold.id, "value", gate.id, "right"),
            EdgeSpec.create(gate.id, "out", action.id, "signal"),
        ],
    )
    report = V.validate(graph, reg)
    assert report.valid, f"the fixture graph must validate: {report.codes()}"
    return graph


@pytest.fixture(scope="module")
def fast_graph(reg):
    """A 20-bar EMA: a warmup any window the endpoint reads will cover."""
    return _graph(reg, "ema", {"window": 20})


@pytest.fixture(scope="module")
def slow_graph(reg):
    """A 300-bar SMA, so "fewer bars than the compiled warmup" is reachable."""
    return _graph(reg, "sma", {"window": 300, "source": "close"})


# ---------------------------------------------------------------------------
# The supplied window
# ---------------------------------------------------------------------------


def _pool(bars=POOL_BARS, spike=False):
    """A deterministic OHLCV pool the real validator accepts, or deliberately rejects.

    Clean shape: a sine close whose largest |z| is well under the validator's own 3σ
    threshold, evenly spaced at the graph's own 5m interval so ``GapHandler`` finds no gap,
    and OHLC relationships that hold on every row. ``spike=True`` puts one close far outside
    3σ - the case ``OutlierDetector._z_score_filter`` rejects outright.
    """
    stamps = pd.date_range("2024-01-01", periods=bars, freq="5min", name="timestamp")
    close = pd.Series(100.0 + 20.0 * np.sin(np.linspace(0.0, 12.0, bars)), index=stamps)
    if spike:
        # Near the END of the pool, because the endpoint reads the tail: a spike outside the
        # window it actually validates would prove nothing about the validator's posture.
        close.iloc[-10] = 100_000.0
    frame = pd.DataFrame(
        {
            "open": close - 0.25,
            "high": close + 1.5,
            "low": close - 0.5,
            "close": close,
            "volume": pd.Series(np.linspace(1_000.0, 9_000.0, bars), index=stamps),
        },
        index=stamps,
    )
    return frame


def _ccxt_rows(frame, limit):
    tail = frame.tail(int(limit))
    return [
        [
            int(stamp.value // 1_000_000),
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            float(row["volume"]),
        ]
        for stamp, row in tail.iterrows()
    ]


class RecordingFeed:
    """The endpoint's one I/O boundary, replaced by a deterministic window."""

    def __init__(self, frame, *, cap=None, error=None):
        self.frame = frame
        self.cap = cap
        self.error = error
        self.calls = []

    async def __call__(self, symbol, timeframe, bars):
        self.calls.append({"symbol": symbol, "timeframe": timeframe, "bars": int(bars)})
        if self.error is not None:
            raise self.error
        wanted = int(bars) if self.cap is None else min(int(bars), int(self.cap))
        return _ccxt_rows(self.frame, wanted)


@pytest.fixture
def feed(monkeypatch):
    recorder = RecordingFeed(_pool())
    monkeypatch.setattr(SO, "_fetch_preview_bars", recorder)
    return recorder


@pytest.fixture(autouse=True)
def pristine_validator(monkeypatch):
    """A validator on ``market_data_validation``'s own default configuration.

    The endpoint deliberately shares that module's ``get_validator()`` singleton with the
    training gate, and the singleton is reconfigurable through ``POST /config``. Clearing it
    here means these tests read the shipped ``ValidationConfig`` - the thresholds the
    platform is actually held to - rather than whatever an earlier test in the run left
    behind. It does not relax anything: the config is the default one.
    """
    monkeypatch.setattr(MDV, "_validator", None)


# ---------------------------------------------------------------------------
# The supplied strategy row and the supplied monitor
# ---------------------------------------------------------------------------


class FakeStrategyService:
    """``StrategyService.get_strategy``'s ownership predicate, and nothing else.

    The real method filters ``.eq("id", …).eq("user_id", …)`` on a request-scoped client that
    also carries RLS. Both halves are applied here, so "another tenant's strategy is a 404"
    is a property this fake can actually fail. Every other service method is absent on
    purpose.
    """

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def get_strategy(self, user, strategy_id):
        self.calls.append({"user_id": user.get("id"), "strategy_id": strategy_id})
        for row in self.rows:
            if row["id"] == strategy_id and row["user_id"] == user["id"]:
                return {
                    "strategy": row,
                    "version": row.get("version"),
                    "deployments": [],
                    "performance": {},
                }
        return None


@pytest.fixture
def service(monkeypatch, fast_graph):
    """One saved strategy owned by ``OWNER``, its current version carrying ``fast_graph``."""
    state = FakeStrategyService(
        [
            {
                "id": STRATEGY_ID,
                "user_id": OWNER["id"],
                "name": "Feed",
                "version": {
                    "id": "ver_feed_1",
                    "version": "v1.0",
                    "graph_json": fast_graph.to_dict(),
                },
            }
        ]
    )

    async def factory():
        return state

    monkeypatch.setattr(SO, "get_strategy_service", factory)
    return state


@pytest.fixture
def monitor(monkeypatch):
    """A real ``WebSocketMonitor``, empty, standing in for the platform's global one.

    Real object, real ``get_all_status()`` projection; only its *contents* are supplied,
    because no market data socket runs in this environment. ``at_age`` sets a connection's
    last-message timestamp directly on the real ``ConnectionMetrics`` dataclass, which is how
    an age of exactly 1.5 intervals - the boundary - becomes reachable in a test.
    """
    instance = WM.WebSocketMonitor(stale_threshold_seconds=10.0)
    monkeypatch.setattr(WM, "get_websocket_monitor", lambda *a, **k: instance)

    def at_age(age_seconds, *, symbol=SYMBOL, connection_id="conn_1",
               exchange_id="testvenue", state=WM.ConnectionState.CONNECTED):
        conn = instance.register_connection(connection_id, exchange_id, symbol)
        conn.state = state
        conn.connected_at = time.time() - 3600.0
        conn.last_message_at = None if age_seconds is None else time.time() - age_seconds
        conn.total_messages = 0 if age_seconds is None else 42
        return conn

    instance.at_age = at_age
    return instance


@pytest.fixture
def as_owner():
    from fastapi.testclient import TestClient

    from backend_app.main import app

    app.dependency_overrides[get_current_user] = lambda: OWNER
    app.dependency_overrides[get_request_supabase] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def as_intruder():
    from fastapi.testclient import TestClient

    from backend_app.main import app

    app.dependency_overrides[get_current_user] = lambda: INTRUDER
    app.dependency_overrides[get_request_supabase] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def anonymous():
    """No override: the real auth dependency runs."""
    from fastapi.testclient import TestClient

    from backend_app.main import app

    app.dependency_overrides.clear()
    return TestClient(app)


# ═══════════════════════════════════════════════════════════════════════════
# 1. The expected interval comes from the pipeline, not from a new table
# ═══════════════════════════════════════════════════════════════════════════


class TestExpectedInterval:
    def test_every_interval_is_the_pipelines_own_figure(self):
        """Recomputed from ``TIMEFRAME_MINUTES``, never compared to a written-out list.

        A test that asserted ``"5m" -> 300`` against its own table would be a second
        vocabulary wearing a test's clothes - the very thing task 7.2 hoisted
        ``TIMEFRAME_MINUTES`` into view to prevent.
        """
        assert MDV.TIMEFRAME_MINUTES, "the pipeline vocabulary must not be empty"
        for label, minutes in MDV.TIMEFRAME_MINUTES.items():
            assert FS.expected_interval_seconds(label) == int(minutes) * 60

    def test_a_label_the_pipeline_does_not_publish_has_no_interval(self):
        """Absence is an answer. It is not an hour, which is the gate's own default."""
        for label in ("3m", "7m", "45s", "", "nonsense", None, 5):
            if isinstance(label, str) and label.strip() in MDV.TIMEFRAME_MINUTES:
                continue
            assert FS.expected_interval_seconds(label) is None

    def test_the_lookup_is_case_sensitive_so_a_month_is_not_a_minute(self):
        """CCXT writes a month ``1M`` and a minute ``1m``.

        Lower-casing the key before the lookup would read a month as a minute - a 43,200x
        error, in the direction of reporting a month-old candle as ``LIVE``.
        """
        assert FS.expected_interval_seconds("1m") == 60
        assert FS.expected_interval_seconds("1M") is None
        assert FS.expected_interval_seconds("1H") is None
        assert FS.expected_interval_seconds("1D") is None

    def test_every_timeframe_the_platform_publishes_is_measurable(self):
        """The selector's set and the freshness table cannot drift apart.

        ``/registry/timeframes`` serves the intersection of the pipeline's vocabularies
        (task 7.2). If a label reachable from the DATA block's timeframe selector had no
        interval here, every feed on it would report ``STALE`` for lack of a measurement.
        """
        timeframes, _sources = SO._pipeline_timeframes()
        assert timeframes
        for entry in timeframes:
            interval = FS.expected_interval_seconds(entry["id"])
            assert interval is not None, entry["id"]
            assert interval == entry["seconds"]


# ═══════════════════════════════════════════════════════════════════════════
# 2. The classifier: boundaries, precedence, and nothing optimistic
# ═══════════════════════════════════════════════════════════════════════════


def _state(**kwargs):
    base = {
        "timeframe": TIMEFRAME,
        "connected": True,
        "available_bars": 500,
        "warmup_bars": 20,
    }
    base.update(kwargs)
    return FS.evaluate_feed_state(**base)


class TestTheFiveStates:
    def test_the_vocabulary_is_exactly_the_five_requirement_19_6_names(self):
        assert {member.value for member in FS.FeedState} == REQUIRED_STATES

    def test_the_thresholds_are_the_requirements_own_multiples(self):
        assert FS.DELAYED_AT_INTERVALS == 1.5
        assert FS.STALE_AT_INTERVALS == 3.0


class TestBoundaries:
    """Requirements 19.7, 19.8, 19.9. Each boundary falls on the *worse* side."""

    @pytest.mark.parametrize(
        "age, expected",
        [
            (0.0, "LIVE"),
            (1.0, "LIVE"),
            (INTERVAL_SECONDS, "LIVE"),
            (449.0, "LIVE"),
            (449.999, "LIVE"),
            (450.0, "DELAYED"),  # exactly 1.5 x interval: not LIVE
            (451.0, "DELAYED"),
            (899.999, "DELAYED"),
            (900.0, "STALE"),  # exactly 3 x interval: not DELAYED
            (5_000.0, "STALE"),
            (86_400.0, "STALE"),
        ],
    )
    def test_the_age_decides_and_the_boundary_is_closed_on_the_worse_side(
        self, age, expected
    ):
        report = _state(age_seconds=age)
        assert report.state.value == expected
        assert report.age_state.value == expected

    def test_the_exact_boundary_is_the_value_a_less_than_or_equal_would_get_wrong(self):
        """1.5 x interval reads ``DELAYED``, which is what makes Property 26 strict."""
        boundary = FS.DELAYED_AT_INTERVALS * INTERVAL_SECONDS
        assert _state(age_seconds=boundary).state is FS.FeedState.DELAYED
        assert _state(age_seconds=boundary - 0.001).state is FS.FeedState.LIVE


class TestProperty26ByConstruction:
    """Property 26: every feed reported ``LIVE`` has an age below 1.5 x its interval.

    A deterministic sweep over every published interval and a grid of multiples that
    straddles both boundaries. Task 7.7 owns the generated version; this is the table, and
    it is computed from the vocabulary rather than written out.
    """

    MULTIPLES = (0.0, 0.1, 0.5, 0.9, 1.0, 1.49, 1.4999, 1.5, 1.5001, 2.0, 2.999, 3.0, 4.0, 50.0)

    def test_anything_live_is_below_one_and_a_half_intervals(self):
        seen = set()
        for label in MDV.TIMEFRAME_MINUTES:
            interval = FS.expected_interval_seconds(label)
            for multiple in self.MULTIPLES:
                age = interval * multiple
                report = FS.evaluate_feed_state(
                    timeframe=label,
                    connected=True,
                    age_seconds=age,
                    available_bars=10_000,
                    warmup_bars=20,
                )
                seen.add(report.state.value)
                assert report.state.value in REQUIRED_STATES
                if report.state is FS.FeedState.LIVE:
                    assert age < FS.DELAYED_AT_INTERVALS * interval
                    assert report.age_seconds is not None
                    assert report.expected_interval_seconds == interval
                if report.age_state is FS.FeedState.LIVE:
                    assert age < FS.DELAYED_AT_INTERVALS * interval
        # The sweep has to actually reach all three age-derived states, or it proves nothing.
        assert {"LIVE", "DELAYED", "STALE"} <= seen

    def test_no_unmeasured_input_is_ever_live(self):
        """Every shape of "we do not know" - none of them may read as fresh."""
        unmeasured = [
            _state(age_seconds=None),                       # no event observed
            _state(timeframe="3m", age_seconds=60.0),       # interval not published
            _state(timeframe=None, age_seconds=60.0),
            _state(age_seconds=float("nan")),
            _state(age_seconds=-30.0),                      # a timestamp in the future
            _state(age_seconds="not a number"),
            _state(connected=None, age_seconds=1.0),        # transport state unreadable
            _state(connected=False, age_seconds=1.0),
        ]
        for report in unmeasured:
            assert report.state is not FS.FeedState.LIVE, report.reason
            assert report.state.value in REQUIRED_STATES


class TestUnknownIsNotFresh:
    def test_an_unknown_age_is_null_and_not_zero(self):
        report = _state(age_seconds=None)
        assert report.state is FS.FeedState.STALE
        assert report.reason == FS.REASON_AGE_UNKNOWN
        body = report.to_dict()
        assert body["age_seconds"] is None
        assert body["age_text"] is None
        assert body["measured"] is False
        assert "No candle observed" in body["display"]

    def test_an_unpublished_interval_is_reported_as_unmeasurable(self):
        report = _state(timeframe="3m", age_seconds=10.0)
        assert report.state is FS.FeedState.STALE
        assert report.reason == FS.REASON_INTERVAL_UNKNOWN
        body = report.to_dict()
        assert body["expected_interval_seconds"] is None
        assert body["delayed_after_seconds"] is None
        assert body["stale_after_seconds"] is None
        assert body["age_state"] is None
        assert "cannot be measured" in body["display"]

    def test_a_20_minute_old_3m_feed_is_not_live_even_though_an_hour_would_allow_it(self):
        """The gate's ``.get(timeframe, 60)`` default is exactly what is refused here."""
        report = _state(timeframe="3m", age_seconds=20 * 60)
        assert report.state is not FS.FeedState.LIVE


class TestTransportState:
    def test_a_down_transport_is_disconnected_however_fresh_the_last_event_was(self):
        report = _state(connected=False, age_seconds=0.0)
        assert report.state is FS.FeedState.DISCONNECTED
        assert report.reason == FS.REASON_TRANSPORT_DOWN
        # The age is still reported: the state is a verdict, not a redaction.
        assert report.to_dict()["age_seconds"] == 0.0
        assert report.to_dict()["age_state"] == "LIVE"

    def test_an_unreadable_transport_state_is_distinguished_from_a_down_one(self):
        report = _state(connected=None, age_seconds=0.0)
        assert report.state is FS.FeedState.DISCONNECTED
        assert report.reason == FS.REASON_TRANSPORT_UNKNOWN


class TestPrecedence:
    """The overlaps in 19.7-19.10 resolved to one label, with every fact preserved."""

    def test_insufficient_data_is_reported_when_bars_are_short(self):
        report = _state(age_seconds=10.0, available_bars=180, warmup_bars=226)
        assert report.state is FS.FeedState.INSUFFICIENT_DATA
        assert report.reason == FS.REASON_WARMUP_UNFILLED
        body = report.to_dict()
        assert body["available_bars"] == 180
        assert body["warmup_bars"] == 226
        assert body["bars_missing"] == 46
        # Not collapsed: the age still says the feed itself is fresh.
        assert body["age_state"] == "LIVE"
        assert "180 of 226 warmup bars" in body["display"]

    def test_insufficient_data_and_disconnected_do_not_collapse(self):
        short_and_down = _state(connected=False, age_seconds=1.0, available_bars=1, warmup_bars=226)
        short_and_up = _state(connected=True, age_seconds=1.0, available_bars=1, warmup_bars=226)
        assert short_and_down.state is FS.FeedState.DISCONNECTED
        assert short_and_up.state is FS.FeedState.INSUFFICIENT_DATA
        # Both carry both facts, so neither label hides the other's evidence.
        for report in (short_and_down, short_and_up):
            body = report.to_dict()
            assert body["bars_missing"] == 225
            assert body["connected"] is (report.state is FS.FeedState.INSUFFICIENT_DATA)

    def test_a_measured_stop_outranks_a_bar_shortfall(self):
        """A stale feed is not "still filling up": bars are not accumulating."""
        report = _state(age_seconds=4 * INTERVAL_SECONDS, available_bars=1, warmup_bars=226)
        assert report.state is FS.FeedState.STALE
        assert report.reason == FS.REASON_AGE_OVER_STALE
        assert report.to_dict()["bars_missing"] == 225

    def test_a_known_bar_shortfall_outranks_an_unmeasurable_age(self):
        """A real observation beats a fail-closed guess; both are still on the wire."""
        report = _state(age_seconds=None, available_bars=1, warmup_bars=226)
        assert report.state is FS.FeedState.INSUFFICIENT_DATA
        assert report.to_dict()["age_seconds"] is None

    def test_an_uncountable_bar_count_is_not_asserted_as_a_shortfall(self):
        report = _state(age_seconds=10.0, available_bars=None, warmup_bars=5_000)
        assert report.state is FS.FeedState.LIVE
        assert report.to_dict()["bars_missing"] is None


class TestTheNumbersAreOnTheWire:
    """Requirement 19.8: the age of the last event *together with* the expected interval."""

    @pytest.mark.parametrize("age", [0.0, 449.0, 450.0, 900.0, 10_000.0])
    def test_every_state_carries_both_figures_and_both_thresholds(self, age):
        body = _state(age_seconds=age).to_dict()
        assert body["age_seconds"] == pytest.approx(age)
        assert body["expected_interval_seconds"] == INTERVAL_SECONDS
        assert body["delayed_after_seconds"] == pytest.approx(450.0)
        assert body["stale_after_seconds"] == pytest.approx(900.0)
        assert body["age_text"]
        assert body["timeframe"] == TIMEFRAME
        assert body["display"]

    def test_the_display_sentence_carries_the_age_and_the_interval(self):
        body = _state(age_seconds=252.0).to_dict()
        assert body["age_text"] == "4m 12s"
        assert "4m 12s" in body["display"]
        assert "every 5m" in body["display"]

    @pytest.mark.parametrize(
        "seconds, text",
        [
            (0.0, "0s"),
            (9.4, "9s"),
            (59.0, "59s"),
            (60.0, "1m 00s"),
            (252.0, "4m 12s"),
            (3_600.0, "1h 00m"),
            (90_000.0, "1d 01h"),
            (None, None),
            (float("nan"), None),
        ],
    )
    def test_the_age_renders_as_a_duration_and_an_absence_stays_an_absence(
        self, seconds, text
    ):
        assert FS.format_age(seconds) == text


# ═══════════════════════════════════════════════════════════════════════════
# 3. The observation: the platform's existing monitor, read conservatively
# ═══════════════════════════════════════════════════════════════════════════


class TestTheMonitorProjectionDoesNotDeadlock:
    """A defect this task found in the component it reads, fixed rather than worked around.

    ``WebSocketMonitor.get_all_status`` built its dict *while holding* ``self._lock`` and
    called ``get_connection_status``, which takes the same non-reentrant ``threading.Lock``.
    With one connection registered it blocked the calling thread permanently - not an
    exception, not a slow answer, a hang - and ``GET /health/websocket`` calls it too. The
    ids are now snapshotted under the lock and each status read outside it.

    The assertion runs in a worker thread with a join deadline, so a regression fails this
    test in a second instead of hanging the suite.
    """

    def test_reading_every_status_returns_with_connections_registered(self, monitor):
        import threading

        monitor.at_age(5.0, connection_id="c1")
        monitor.at_age(9.0, connection_id="c2", symbol="BTC/USDT")

        result = {}

        def read():
            result["statuses"] = monitor.get_all_status()

        worker = threading.Thread(target=read, daemon=True)
        worker.start()
        worker.join(timeout=5.0)

        assert not worker.is_alive(), "get_all_status did not return: the lock is re-entered"
        assert set(result["statuses"]) == {"c1", "c2"}
        assert all(isinstance(status, dict) for status in result["statuses"].values())


class TestObservation:
    def test_no_connection_for_this_market_is_a_distinct_honest_absence(self, monitor):
        monitor.at_age(1.0, symbol="BTC/USDT", connection_id="other")
        observation = FS.observe_feed(SYMBOL)
        assert observation.connected is False
        assert observation.source == FS.OBSERVATION_NO_FEED
        assert observation.age_seconds is None
        assert observation.connections_matched == 0

    def test_a_fresh_connection_is_observed_with_its_age(self, monitor):
        monitor.at_age(12.0)
        observation = FS.observe_feed(SYMBOL)
        assert observation.connected is True
        assert observation.age_seconds == pytest.approx(12.0, abs=2.0)
        assert observation.source == FS.OBSERVATION_MONITOR

    @pytest.mark.parametrize(
        "state, connected",
        [
            (WM.ConnectionState.CONNECTED, True),
            (WM.ConnectionState.STALE, True),  # the monitor's own 10s flag, not ours
            (WM.ConnectionState.DISCONNECTED, False),
            (WM.ConnectionState.RECONNECTING, False),
        ],
    )
    def test_the_monitors_transport_state_maps_without_being_reinterpreted(
        self, monitor, state, connected
    ):
        monitor.at_age(5.0, state=state)
        assert FS.observe_feed(SYMBOL).connected is connected

    def test_a_symbol_is_matched_across_separator_and_case_but_not_across_quote(
        self, monitor
    ):
        monitor.at_age(3.0, symbol="eth-usdt")
        assert FS.observe_feed("ETH/USDT").connections_matched == 1
        assert FS.observe_feed("ETH/USD").connections_matched == 0

    def test_the_least_fresh_matching_connection_is_the_one_reported(self, monitor):
        """A second venue's healthy socket must not lend this market its freshness."""
        monitor.at_age(2.0, connection_id="fresh", exchange_id="venue_a")
        monitor.at_age(4_000.0, connection_id="dead", exchange_id="venue_b")
        observation = FS.observe_feed(SYMBOL)
        assert observation.connections_matched == 2
        assert observation.age_seconds == pytest.approx(4_000.0, abs=2.0)

    def test_a_connection_that_never_delivered_reports_an_unknown_age(self, monitor):
        monitor.at_age(None)
        observation = FS.observe_feed(SYMBOL)
        assert observation.connected is True
        assert observation.age_seconds is None
        # Which the classifier then refuses to call fresh.
        assert (
            FS.evaluate_feed_state(
                timeframe=TIMEFRAME,
                connected=observation.connected,
                age_seconds=observation.age_seconds,
            ).state
            is FS.FeedState.STALE
        )

    def test_an_unreadable_monitor_is_an_unknown_transport_not_a_working_one(
        self, monkeypatch
    ):
        def boom(*args, **kwargs):
            raise RuntimeError("monitor unavailable")

        monkeypatch.setattr(WM, "get_websocket_monitor", boom)
        observation = FS.observe_feed(SYMBOL)
        assert observation.connected is None
        assert observation.source == FS.OBSERVATION_UNAVAILABLE

    def test_the_observation_names_no_venue(self, monitor):
        """SB-06 / Requirement 12.1: the venue is a deployment fact, not a field."""
        monitor.at_age(5.0, exchange_id="secretvenue")
        observation = FS.observe_feed(SYMBOL)
        assert "secretvenue" not in repr(observation).lower()


# ═══════════════════════════════════════════════════════════════════════════
# 4. The endpoint
# ═══════════════════════════════════════════════════════════════════════════


class TestEndpoint:
    def test_a_fresh_feed_and_a_clean_window_report_live_with_the_numbers(
        self, as_owner, service, feed, monitor
    ):
        monitor.at_age(30.0)
        response = as_owner.get(quality_path())

        assert response.status_code == 200, response.text
        body = response.json()
        feed_body = body["feed"]
        assert feed_body["state"] == "LIVE"
        assert feed_body["age_seconds"] < FS.DELAYED_AT_INTERVALS * INTERVAL_SECONDS
        assert feed_body["expected_interval_seconds"] == INTERVAL_SECONDS
        assert feed_body["age_text"]
        assert "every 5m" in feed_body["display"]
        assert feed_body["measured"] is True
        assert body["market"] == {"symbol": SYMBOL, "timeframe": TIMEFRAME}
        assert body["warmup_bars"] >= 20
        # A liveness reading must not be cached as if it were reference data.
        assert response.headers["cache-control"] == "no-store"

    @pytest.mark.parametrize(
        "age, expected",
        [(30.0, "LIVE"), (500.0, "DELAYED"), (40 * 60.0, "STALE")],
    )
    def test_the_served_state_follows_the_measured_age(
        self, as_owner, service, feed, monitor, age, expected
    ):
        monitor.at_age(age)
        body = as_owner.get(quality_path()).json()["feed"]
        assert body["state"] == expected
        assert body["state"] in REQUIRED_STATES
        # Whatever the state, both figures are there (Requirement 19.8).
        assert body["age_seconds"] == pytest.approx(age, abs=3.0)
        assert body["expected_interval_seconds"] == INTERVAL_SECONDS

    def test_no_feed_at_all_is_disconnected_and_still_a_200(
        self, as_owner, service, feed
    ):
        """The truthful answer in an environment with no running feed."""
        response = as_owner.get(quality_path())

        assert response.status_code == 200, response.text
        feed_body = response.json()["feed"]
        assert feed_body["state"] == "DISCONNECTED"
        assert feed_body["age_seconds"] is None
        assert feed_body["age_text"] is None
        assert feed_body["measured"] is False
        # The interval is still published, so the panel can say what it expected.
        assert feed_body["expected_interval_seconds"] == INTERVAL_SECONDS

    def test_fewer_bars_than_the_compiled_warmup_is_insufficient_data(
        self, as_owner, monkeypatch, feed, monitor, slow_graph
    ):
        """Requirement 19.10, against ``plan.warmup_bars`` rather than a guess."""
        state = FakeStrategyService(
            [
                {
                    "id": STRATEGY_ID,
                    "user_id": OWNER["id"],
                    "version": {"id": "ver_slow", "graph_json": slow_graph.to_dict()},
                }
            ]
        )

        async def factory():
            return state

        monkeypatch.setattr(SO, "get_strategy_service", factory)
        feed.cap = 120  # the venue holds less history than the warmup needs
        monitor.at_age(10.0)

        body = as_owner.get(quality_path()).json()
        feed_body = body["feed"]
        assert body["warmup_bars"] >= 300
        assert feed_body["available_bars"] == 120
        assert feed_body["warmup_bars"] == body["warmup_bars"]
        assert feed_body["bars_missing"] == body["warmup_bars"] - 120
        assert feed_body["state"] == "INSUFFICIENT_DATA"
        # The feed itself is fresh, and that fact is not lost.
        assert feed_body["age_state"] == "LIVE"

    def test_the_window_is_sized_to_cover_the_warmup_and_is_bounded(
        self, as_owner, service, feed, monitor
    ):
        monitor.at_age(10.0)
        window = as_owner.get(quality_path()).json()["data_quality"]["window"]

        assert window["bars"] <= SO.DATA_QUALITY_MAX_BARS
        assert window["bars"] >= SO.DATA_QUALITY_MIN_BARS
        assert window["covers_warmup"] is True
        assert feed.calls[-1]["bars"] == window["bars"]
        assert feed.calls[-1]["symbol"] == SYMBOL
        assert feed.calls[-1]["timeframe"] == TIMEFRAME


class TestTheQualityReportIsReadNotComputed:
    def test_the_body_is_the_validators_own_report(
        self, as_owner, service, feed, monitor
    ):
        """Requirement 19.14, and "reads ``DataQualityReport``" literally.

        Asserted by comparing the served body key-for-key against a report this test
        produces by calling ``market_data_validation`` itself on the same window - never by
        restating what a quality score ought to be.
        """
        import asyncio

        from backend_app.backend.market_data_contract import closed_bar_frame

        monitor.at_age(10.0)
        body = as_owner.get(quality_path()).json()["data_quality"]

        assert body["available"] is True
        assert body["status"] == "REPORTED"
        assert "MarketDataValidator" in body["source"]

        served_bars = feed.calls[-1]["bars"]
        frame, _counters = closed_bar_frame(
            _ccxt_rows(feed.frame, served_bars), TIMEFRAME
        )
        _cleaned, reference = asyncio.run(
            MDV.MarketDataValidator().validate(frame.tail(served_bars), SYMBOL, TIMEFRAME)
        )
        expected = reference.to_dict()
        assert set(body["report"]) == set(expected)
        for key in ("symbol", "timeframe", "total_candles", "valid_candles",
                    "invalid_candles", "quality_score", "quality_level"):
            assert body["report"][key] == expected[key], key
        # The enum's `value` is a threshold integer; the name is what a reader can act on,
        # and both are published rather than one being guessed from the other.
        assert isinstance(body["quality_level"], int)
        assert body["quality_level_name"] == reference.quality_level.name

    def test_a_window_the_validator_rejects_is_reported_as_rejected(
        self, as_owner, service, feed, monitor
    ):
        """The strict 3σ posture, surfaced honestly instead of loosened.

        ``OutlierDetector._z_score_filter`` refuses a whole window containing any close
        beyond 3σ. Earlier phases mapped that to a classified ``DATA_QUALITY`` block rather
        than relaxing the threshold, and this endpoint keeps that posture: the refusal is
        reported, with the validator's own message, as a 200 - not retried under looser
        settings and not a 500.
        """
        feed.frame = _pool(spike=True)
        monitor.at_age(10.0)

        response = as_owner.get(quality_path())
        assert response.status_code == 200, response.text
        body = response.json()["data_quality"]
        assert body["status"] == "REJECTED"
        assert body["reason"] == "DATA_QUALITY"
        assert body["report"] is None
        assert body["quality_score"] is None
        assert "outlier" in body["validator_error"].lower()
        # And the feed state is unaffected by the quality verdict: different questions.
        assert response.json()["feed"]["state"] == "LIVE"

    def test_no_window_at_all_is_an_explicit_absence_not_a_good_report(
        self, as_owner, service, feed
    ):
        feed.error = RuntimeError("no venue is reachable from here")

        response = as_owner.get(quality_path())
        assert response.status_code == 200, response.text
        body = response.json()
        quality = body["data_quality"]
        assert quality["available"] is False
        assert quality["status"] == "UNAVAILABLE"
        assert quality["report"] is None
        assert quality["quality_score"] is None
        assert quality["available_bars"] is None
        # Feed state is still served, and it is not LIVE.
        assert body["feed"]["state"] == "DISCONNECTED"

    def test_an_observation_failure_is_never_a_500(
        self, as_owner, service, feed, monkeypatch
    ):
        def boom(symbol):
            raise RuntimeError("observation exploded")

        monkeypatch.setattr(FS, "observe_feed", boom)
        response = as_owner.get(quality_path())

        assert response.status_code == 200, response.text
        feed_body = response.json()["feed"]
        assert feed_body["state"] == "DISCONNECTED"
        assert feed_body["age_seconds"] is None

    def test_the_endpoint_defines_no_threshold_of_its_own(self):
        """Structural: the router reads the ingest contract, it does not re-tune it.

        The report comes through ``market_data_contract.validated_window`` (task 7.5's one
        entry point), so this endpoint shares the closed-bar rule, the validator call and
        the counters with the path that computes indicators instead of running a second
        fetch-and-validate sequence of its own.
        """
        source = inspect.getsource(SO._data_quality_payload)
        assert "validated_window(" in source
        for forbidden in (
            "z_score",
            "ValidationConfig(",
            "min_quality_score",
            "iqr_multiplier",
            "MarketDataValidator(",
        ):
            assert forbidden not in source, forbidden

    def test_the_feed_state_module_holds_no_second_interval_table(self):
        """The interval is a lookup into the pipeline's vocabulary, not a local copy.

        Asserted on the AST: no dict literal in ``feed_state`` maps a bar-shaped string key
        to a number. A hand-written ``{"1m": 60, ...}`` appearing here later is a test
        failure rather than a review miss.
        """
        import re

        tree = ast.parse(inspect.getsource(FS))
        bar_label = re.compile(r"^\d+[smhdwM]$")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    assert not bar_label.match(key.value), key.value
        assert "TIMEFRAME_MINUTES" in inspect.getsource(FS.expected_interval_seconds)


class TestAuthAndTenantIsolation:
    def test_an_unauthenticated_caller_is_refused(self, anonymous):
        response = anonymous.get(quality_path())
        assert response.status_code in (401, 403), response.text

    def test_another_tenants_strategy_is_reported_as_missing(
        self, as_intruder, service, feed, monitor
    ):
        """404, identically to one that does not exist: existence must not leak."""
        monitor.at_age(10.0)
        response = as_intruder.get(quality_path())

        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "STRATEGY_NOT_FOUND"
        # Nothing was fetched or validated on the refused path.
        assert feed.calls == []

    def test_an_unknown_strategy_is_a_404(self, as_owner, service, feed):
        response = as_owner.get(quality_path("stg_does_not_exist"))
        assert response.status_code == 404
        assert feed.calls == []

    def test_ownership_is_resolved_before_anything_is_fetched(
        self, as_owner, service, feed, monitor
    ):
        monitor.at_age(10.0)
        as_owner.get(quality_path())
        assert service.calls == [{"user_id": OWNER["id"], "strategy_id": STRATEGY_ID}]

    def test_the_endpoint_carries_the_auth_dependency_and_a_rate_limit(self):
        signature = inspect.signature(SO.get_strategy_data_quality)
        assert "user" in signature.parameters
        assert getattr(signature.parameters["user"].default, "dependency", None) is (
            get_current_user
        )
        assert any(
            getattr(route, "path", "").endswith("/strategies/{strategy_id}/data-quality")
            for route in SO.router.routes
        ), "the data-quality route is not registered"
        assert "@limiter.limit(" in _decorators_of("get_strategy_data_quality")

    def test_the_response_names_no_venue_or_credential(
        self, as_owner, service, feed, monitor
    ):
        monitor.at_age(10.0, exchange_id="secretvenue")
        body = as_owner.get(quality_path()).text.lower()
        for forbidden in ("secretvenue", "api_key", "apikey", "secret", "passphrase"):
            assert forbidden not in body, forbidden


def _decorators_of(name: str) -> str:
    module = ast.parse(inspect.getsource(SO))
    lines = inspect.getsource(SO).splitlines()
    for node in ast.walk(module):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            first = min(dec.lineno for dec in node.decorator_list) - 1
            return "\n".join(lines[first : node.lineno])
    raise AssertionError(f"{name} is not defined in the router")


class TestVersionsThatCannotBeReportedOn:
    def test_a_version_with_no_graph_is_a_422(self, as_owner, monkeypatch, feed):
        state = FakeStrategyService(
            [{"id": STRATEGY_ID, "user_id": OWNER["id"], "version": {}}]
        )

        async def factory():
            return state

        monkeypatch.setattr(SO, "get_strategy_service", factory)
        response = as_owner.get(quality_path())

        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "DATA_QUALITY_GRAPH_UNAVAILABLE"
        assert feed.calls == []

    def test_a_version_declaring_no_market_is_a_422_naming_the_gap(
        self, as_owner, monkeypatch, feed, reg
    ):
        """No symbol and timeframe means no data source to report on."""
        graph = _graph(reg, "ema", {"window": 20})
        payload = graph.to_dict()
        for node in payload["nodes"]:
            if node["block_id"] == "ohlcv_feed":
                node["params"].pop("symbol", None)
                node["params"].pop("timeframe", None)
        state = FakeStrategyService(
            [{"id": STRATEGY_ID, "user_id": OWNER["id"], "version": {"graph_json": payload}}]
        )

        async def factory():
            return state

        monkeypatch.setattr(SO, "get_strategy_service", factory)
        response = as_owner.get(quality_path())

        assert response.status_code == 422
        assert response.json()["detail"]["error"] in (
            "DATA_QUALITY_MARKET_UNRESOLVED",
            "STRATEGY_VALIDATION_FAILED",
        )
        assert feed.calls == []
