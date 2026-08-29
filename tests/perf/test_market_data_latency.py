"""tests/perf/test_market_data_latency.py — the market data latency experiment.

Spec: strategy-builder task 7.6. Requirements 19.11, 19.12, 19.13; ``design.md`` ->
"Latency measurement plan".

THIS IS A SCRIPTED EXPERIMENT, NOT A CI TEST
--------------------------------------------
It is kept out of the default lane twice over, because a 24 h measurement that anyone can
trip by running ``pytest -q`` is a broken build waiting to happen:

1. ``pytest.ini`` names ``tests/perf`` in ``norecursedirs``, so the default lane does not
   collect this directory at all. Passing the file path explicitly still collects it —
   that is how the experiment is run.
2. Every measuring test additionally requires ``AERORA_MARKET_DATA_LATENCY_EXPERIMENT`` to
   be set, so even ``pytest tests/perf`` skips rather than starting a long run by accident.

Run it with::

    set AERORA_MARKET_DATA_LATENCY_EXPERIMENT=1
    .venv\\Scripts\\python.exe -m pytest tests/perf/test_market_data_latency.py -q

Knobs, all environment variables, all with the design's values as defaults:
``AERORA_MARKET_DATA_LATENCY_SECONDS`` (observation window; the design prescribes 86400),
``AERORA_MARKET_DATA_LATENCY_SYMBOLS``, ``AERORA_MARKET_DATA_LATENCY_TIMEFRAMES``,
``AERORA_MARKET_DATA_LATENCY_REPORT`` (output path).

WHAT IT MEASURES, AND FOR WHOM
------------------------------
``design.md``'s two candidates:

* **Candidate A — direct CCXT stream.** ``mds/main.py`` watches ``watch_ohlcv`` and
  publishes ``candles[-1]`` to a Redis channel on every socket update;
  ``data_seeking_engine.DataEngine.stream_live_ohlcv`` relays it to a consumer. Reaching it
  needs Redis **and** a running MDS process **and** an exchange socket.
* **Candidate B — validated OHLCV pipeline.** ``market_data_contract.validated_window``
  over ``market_data_validation.MarketDataValidator``. Reaching a *live* measurement of it
  needs an exchange.

Twelve metric rows, one function each, all optional: whatever a run cannot measure stays
``None`` and is reported as "not measured".

HOW IT DEGRADES WHEN THERE IS NO FEED — WHICH IS THIS ENVIRONMENT
-----------------------------------------------------------------
No exchange host resolves here and no Redis is running. So:

* :func:`probe_candidate_a` fails on the absent Redis transport with a recorded reason.
* :func:`probe_candidate_b`'s TCP probe *passes* — ``api.binance.com:443`` completes a
  handshake here — but the REST API does not answer, and ``ConnectionEngine.connect``
  responds to that by injecting ``DEV_MODE``'s mock interface and returning a working
  exchange object whose ``fetch_ohlcv`` generates candles from ``time.time()``.
  :func:`refuse_if_mocked` catches that and refuses to measure it. This is the single most
  important safeguard in the file: without it the harness happily publishes fiction, and an
  earlier revision of it did.
* Both candidates therefore come back from :func:`market_data_latency.unmeasured` with a
  recorded reason, every metric ``None``.
* ``choose_market_data_source`` therefore rejects both at the correctness floor
  (``NOT_MEASURED``) and returns ``BLOCKED``.
* The report is still written, and says the experiment did not run and why.

That is the point. A decision document reporting invented latencies is worse than one
reporting that nothing was measured, so nothing here synthesises a latency, a completeness
figure or a duplicate count in the absence of a feed.

WHAT *IS* HONESTLY MEASURABLE HERE, AND WHY IT IS FENCED OFF
------------------------------------------------------------
Two things, both recorded in the report under ``notes`` and neither used as a decision
input:

* **The ingest path's processing cost.** ``ClosedBarIngest.offer`` per event, and
  ``validated_window`` end to end, over generated CCXT-shaped rows. That is a real
  measurement *of the code* and it bounds the pipeline latency Candidate B adds — it is not
  a measurement of a feed, and it cannot populate ``pipeline`` or the floor metrics.
* **A structural fact about Candidate A's path**, established by reading the shipped source
  rather than by guessing: the ``mds/main.py`` -> ``stream_live_ohlcv`` relay imports
  nothing from ``market_data_contract`` and applies no closed-bar, duplicate or ordering
  rule, and it republishes ``candles[-1]`` on every socket update. That is why Candidate A's
  correctness is *unproven* rather than assumed good, and it is a claim this file checks
  instead of asserting in prose.

WHAT IT DOES NOT DO
-------------------
No credential is read, no order is placed, no database row is written, no endpoint is
served and no listener is bound. The only outbound traffic is a short TCP reachability
probe to the feed the experiment is trying to measure. Nothing here relaxes
``OutlierDetector``'s 3σ filter or ``GapHandler``'s gap refusal: if the platform validator
refuses a window during the processing-cost observation, the refusal is recorded as a
finding, not worked around.
"""
from __future__ import annotations

import asyncio
import inspect
import math
import os
import socket
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import pytest

from backend_app.backend.feed_state import expected_interval_seconds
from backend_app.backend.market_data_latency import (
    DECISION_REPORT_PATH,
    P99_MARGIN_MS,
    SOURCE_A,
    SOURCE_B,
    LatencySummary,
    SourceMeasurement,
    choose_market_data_source,
    evaluate_correctness_floor,
    unmeasured,
    write_decision_report,
)

# ══════════════════════════════════════════════════════════════════════════
# OPT-IN
# ══════════════════════════════════════════════════════════════════════════

EXPERIMENT_ENV = "AERORA_MARKET_DATA_LATENCY_EXPERIMENT"

#: ``design.md``: 24 h continuous plus a 1 h forced-churn window. Left as the default so
#: the prescribed experiment is what you get when you ask for no window, and a shorter run
#: has to be requested explicitly and is labelled a smoke run in the report.
PRESCRIBED_WINDOW_SECONDS = 24 * 60 * 60
PRESCRIBED_SYMBOLS: Tuple[str, ...] = ("BTC/USDT", "ETH/USDT", "ALGO/USDT")
PRESCRIBED_TIMEFRAMES: Tuple[str, ...] = ("1m", "15m", "1h")

experiment_only = pytest.mark.skipif(
    not os.environ.get(EXPERIMENT_ENV, "").strip(),
    reason=(
        f"scripted market data latency experiment; set {EXPERIMENT_ENV}=1 to run it. "
        f"It is excluded from the default lane by pytest.ini's norecursedirs and gated "
        f"again here so that `pytest tests/perf` cannot start a 24 h measurement by "
        f"accident."
    ),
)


def _env_seconds() -> float:
    raw = os.environ.get("AERORA_MARKET_DATA_LATENCY_SECONDS", "").strip()
    if not raw:
        return float(PRESCRIBED_WINDOW_SECONDS)
    try:
        return max(1.0, float(raw))
    except ValueError:
        return float(PRESCRIBED_WINDOW_SECONDS)


def _env_tuple(name: str, default: Tuple[str, ...]) -> Tuple[str, ...]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    parts = tuple(p.strip() for p in raw.split(",") if p.strip())
    return parts or default


def _report_path() -> str:
    return os.environ.get("AERORA_MARKET_DATA_LATENCY_REPORT", "").strip() or DECISION_REPORT_PATH


# ══════════════════════════════════════════════════════════════════════════
# REACHABILITY PROBES — the honest gate on every measurement below
# ══════════════════════════════════════════════════════════════════════════

PROBE_TIMEOUT_SECONDS = 5.0


class MockedFeed(RuntimeError):
    """``ConnectionEngine`` fell back to ``DEV_MODE``'s mock interface. Refuse to measure it.

    This is the most dangerous failure mode the harness has, and the reason it is a hard
    refusal rather than a warning: ``ConnectionEngine.connect`` catches a venue failure and,
    when ``DEV_MODE`` is set, calls ``_apply_mock_interface`` and returns a *working* exchange
    object whose ``fetch_ohlcv`` is a generator. Candles arrive, counters populate,
    percentiles compute, and every figure is fiction.

    An earlier revision of this file did exactly that and published a completeness of 5.0
    from 2,495 "bars" spread over a 499-bar grid — the mock re-bases its timestamps on
    ``time.time()`` per call, so each poll invented a fresh grid. A TCP handshake on port 443
    is not proof that the REST API answers; this check is.
    """


#: The prefix ``connection_engine._apply_mock_interface`` gives every method it injects.
_MOCK_PREFIX = "mock_"

#: The methods this harness reads. If any of them is a mock, nothing measured is a feed fact.
_FEED_METHODS = ("fetch_ohlcv", "load_markets", "watch_ohlcv")


def mocked_methods(exchange: Any) -> List[str]:
    """Which of the feed methods have been replaced by ``DEV_MODE`` mocks."""
    found: List[str] = []
    for name in _FEED_METHODS:
        candidate = getattr(exchange, name, None)
        if str(getattr(candidate, "__name__", "")).startswith(_MOCK_PREFIX):
            found.append(name)
    return found


def refuse_if_mocked(exchange: Any) -> None:
    """Raise :class:`MockedFeed` when the connection is a ``DEV_MODE`` stand-in."""
    mocked = mocked_methods(exchange)
    if mocked:
        raise MockedFeed(
            "ConnectionEngine returned a DEV_MODE mock interface rather than a live venue "
            "(mocked: " + ", ".join(mocked) + "). connection_engine._apply_mock_interface "
            "generates candles from time.time(), so every latency, completeness, duplicate "
            "and ordering figure derived from it would be fiction. Refusing to measure it."
        )


def _tcp_reachable(host: str, port: int) -> Optional[str]:
    """``None`` when a TCP connection opens, else the reason it did not.

    Deliberately dumb: resolve, connect, close. No credential, no handshake beyond TCP, no
    payload. It answers one question — is the thing this experiment wants to measure
    reachable at all — and it answers it in under ``PROBE_TIMEOUT_SECONDS``.
    """
    try:
        socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        return f"DNS resolution for {host} failed ({exc})"
    except OSError as exc:  # pragma: no cover - platform dependent
        return f"address lookup for {host} failed ({exc})"
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT_SECONDS):
            return None
    except OSError as exc:
        return f"TCP connection to {host}:{port} failed ({exc})"


def probe_candidate_a() -> Optional[str]:
    """``None`` when Candidate A's transport is reachable, else why it is not.

    Candidate A needs three things at once: a Redis instance (the MDS publishes to a Redis
    channel), the MDS process itself, and the exchange socket the MDS watches. Redis is
    checked because it is the nearest hop and its absence is decisive — with no Redis,
    ``DataEngine.stream_live_ohlcv`` cannot subscribe and no candle can arrive whatever the
    exchange is doing.
    """
    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        return (
            "REDIS_URL is empty, so Candidate A has no transport: "
            "DataEngine.stream_live_ohlcv subscribes to the Redis channel "
            "'mds:data:{exchange}:{symbol}' that mds/main.py publishes to, and with no "
            "Redis no candle can arrive on this path at all."
        )
    parsed = urlparse(url if "://" in url else f"redis://{url}")
    host, port = parsed.hostname or "localhost", parsed.port or 6379
    unreachable = _tcp_reachable(host, port)
    if unreachable:
        return f"Candidate A's Redis transport is unreachable: {unreachable}"
    return None


def probe_candidate_b(exchange_host: str = "api.binance.com") -> Optional[str]:
    """``None`` when an exchange endpoint is reachable, else why it is not.

    Candidate B's live measurement reads real candles through
    ``DataEngine.fetch_historical_ohlcv`` / the validated pipeline, so it needs the venue.
    """
    unreachable = _tcp_reachable(exchange_host, 443)
    if unreachable:
        return (
            f"Candidate B cannot be measured against a live feed: {unreachable}. "
            f"The validated pipeline needs real candles with real exchange timestamps to "
            f"produce an end-to-end latency figure."
        )
    return None


# ══════════════════════════════════════════════════════════════════════════
# THE TWELVE METRIC ROWS — one collector each
# ══════════════════════════════════════════════════════════════════════════


class ResourceSampler:
    """CPU and RSS over the observation window. ``design.md`` rows 4 and 5.

    ``psutil`` if it is installed, nothing if it is not — an absent sampler leaves
    ``cpu_percent_*`` and ``rss_*`` ``None`` rather than reporting a zero.
    """

    def __init__(self) -> None:
        self._process: Any = None
        self._cpu: List[float] = []
        self.rss_start: Optional[int] = None
        self.rss_end: Optional[int] = None
        try:
            import psutil

            self._process = psutil.Process(os.getpid())
            self._process.cpu_percent(interval=None)  # prime the delta
            self.rss_start = int(self._process.memory_info().rss)
        except Exception:  # pragma: no cover - psutil optional
            self._process = None

    @property
    def available(self) -> bool:
        return self._process is not None

    def sample(self) -> None:
        if self._process is None:
            return
        try:
            self._cpu.append(float(self._process.cpu_percent(interval=None)))
            self.rss_end = int(self._process.memory_info().rss)
        except Exception:  # pragma: no cover
            pass

    def summary(self) -> Dict[str, Optional[float]]:
        if not self._cpu:
            return {"cpu_percent_mean": None, "cpu_percent_p95": None}
        summary = LatencySummary.from_samples(self._cpu)
        return {
            "cpu_percent_mean": None if summary is None else summary.mean_ms,
            "cpu_percent_p95": None if summary is None else summary.p95_ms,
        }


class ArrivalRecorder:
    """Latency, completeness, ordering, duplication and reliability off one arrival stream.

    Every number here is derived from timestamps the events themselves carry; nothing is
    inferred and nothing is defaulted. Feed one event at a time through :meth:`record`.
    """

    def __init__(self, timeframe: str, *, stall_intervals: int = 3) -> None:
        self.timeframe = timeframe
        self.interval_seconds = expected_interval_seconds(timeframe)
        self.stall_intervals = stall_intervals

        self.end_to_end_ms: List[float] = []
        self.ingest_ms: List[float] = []
        self.pipeline_ms: List[float] = []

        self.events = 0
        self.errors = 0
        self.reconnects = 0
        self.recovery_seconds: List[float] = []
        self.events_lost: List[int] = []

        self.distinct_open_times: set = set()
        self.duplicates = 0
        self.out_of_order = 0
        self.monotonic_violations = 0
        self.silent_stalls = 0

        self._high_water_ms: Optional[int] = None
        self._last_arrival: Optional[float] = None
        self._first_arrival: Optional[float] = None
        self._skew_ms: List[float] = []

    def record(
        self,
        *,
        exchange_timestamp_ms: int,
        received_monotonic: float,
        dag_input_monotonic: float,
        received_wall_ms: float,
        dag_input_wall_ms: float,
    ) -> None:
        """One delivered event.

        ``*_monotonic`` are ``perf_counter`` readings, used for the pipeline segment where a
        wall clock would be exposed to NTP steps. ``*_wall_ms`` are epoch milliseconds,
        needed for the two segments that straddle the exchange's clock.
        """
        self.events += 1
        now = time.perf_counter()
        if self._first_arrival is None:
            self._first_arrival = now
        if self._last_arrival is not None and self.interval_seconds:
            silence = now - self._last_arrival
            if silence > self.stall_intervals * self.interval_seconds:
                self.silent_stalls += 1
        self._last_arrival = now

        self.end_to_end_ms.append(float(dag_input_wall_ms) - float(exchange_timestamp_ms))
        self.ingest_ms.append(float(received_wall_ms) - float(exchange_timestamp_ms))
        self.pipeline_ms.append((float(dag_input_monotonic) - float(received_monotonic)) * 1000.0)
        self._skew_ms.append(float(received_wall_ms) - float(exchange_timestamp_ms))

        stamp = int(exchange_timestamp_ms)
        if stamp in self.distinct_open_times:
            self.duplicates += 1
        else:
            self.distinct_open_times.add(stamp)
        if self._high_water_ms is not None and stamp < self._high_water_ms:
            self.out_of_order += 1
            self.monotonic_violations += 1
        if self._high_water_ms is None or stamp > self._high_water_ms:
            self._high_water_ms = stamp

    def note_reconnect(self, recovery_seconds: float, events_lost: int) -> None:
        self.reconnects += 1
        self.recovery_seconds.append(float(recovery_seconds))
        self.events_lost.append(int(events_lost))

    def note_error(self) -> None:
        self.errors += 1

    # -- derived ------------------------------------------------------------

    @property
    def window_seconds(self) -> Optional[float]:
        if self._first_arrival is None or self._last_arrival is None:
            return None
        return self._last_arrival - self._first_arrival

    def expected_bars(self, window_seconds: Optional[float]) -> Optional[int]:
        """Bars due in ``window_seconds`` at this timeframe's published interval.

        The interval comes from ``feed_state.expected_interval_seconds``, which reads
        ``market_data_validation.TIMEFRAME_MINUTES`` — the pipeline's own vocabulary. An
        unmeasurable timeframe yields ``None``, never a guessed hour, because guessing the
        interval here would misstate completeness by the ratio of the guess to the truth.
        """
        if self.interval_seconds is None or not window_seconds or window_seconds <= 0:
            return None
        return int(math.floor(window_seconds / float(self.interval_seconds)))

    def to_measurement(
        self,
        *,
        source: str,
        has_validation_layer: bool,
        symbols: Sequence[str],
        timeframes: Sequence[str],
        window_seconds: Optional[float],
        invalid_ohlc_delivered: Optional[int],
        synthetic_candles_delivered: Optional[int],
        resources: Optional[ResourceSampler] = None,
        notes: Optional[Sequence[str]] = None,
    ) -> SourceMeasurement:
        window = window_seconds if window_seconds is not None else self.window_seconds
        expected = self.expected_bars(window)
        cpu = resources.summary() if resources is not None and resources.available else {}
        skew = LatencySummary.from_samples(self._skew_ms)
        measurement = SourceMeasurement(
            source=source,
            has_validation_layer=has_validation_layer,
            measured=True,
            symbols=tuple(symbols),
            timeframes=tuple(timeframes),
            window_seconds=window,
            events_observed=self.events,
            end_to_end=LatencySummary.from_samples(self.end_to_end_ms),
            ingest=LatencySummary.from_samples(self.ingest_ms),
            pipeline=LatencySummary.from_samples(self.pipeline_ms),
            cpu_percent_mean=cpu.get("cpu_percent_mean"),
            cpu_percent_p95=cpu.get("cpu_percent_p95"),
            rss_start_bytes=None if resources is None else resources.rss_start,
            rss_end_bytes=None if resources is None else resources.rss_end,
            throughput_events_per_second=(
                None if not window or window <= 0 else self.events / float(window)
            ),
            reconnects=self.reconnects,
            reconnect_recovery_mean_seconds=(
                None
                if not self.recovery_seconds
                else sum(self.recovery_seconds) / len(self.recovery_seconds)
            ),
            reconnect_recovery_max_seconds=(
                None if not self.recovery_seconds else max(self.recovery_seconds)
            ),
            events_lost_per_reconnect=(
                None if not self.events_lost else sum(self.events_lost) / len(self.events_lost)
            ),
            expected_bars=expected,
            received_bars=len(self.distinct_open_times) or None,
            monotonic_violations=self.monotonic_violations,
            clock_skew_ms=None if skew is None else skew.p50_ms,
            duplicates_delivered=self.duplicates,
            out_of_order_delivered=self.out_of_order,
            invalid_ohlc_delivered=invalid_ohlc_delivered,
            synthetic_candles_delivered=synthetic_candles_delivered,
            uptime_fraction=(
                None
                if not window or window <= 0
                else max(0.0, 1.0 - (sum(self.recovery_seconds) / float(window)))
            ),
            error_count=self.errors,
            silent_stall_episodes=self.silent_stalls,
            notes=list(notes or ()),
        )
        return measurement


# ══════════════════════════════════════════════════════════════════════════
# THE TWO OBSERVATIONS THIS ENVIRONMENT *CAN* MAKE — both fenced off
# ══════════════════════════════════════════════════════════════════════════


def _generated_closed_bars(count: int, timeframe: str = "1m") -> List[List[float]]:
    """CCXT-shaped rows on a clean grid, for timing the ingest code path only.

    Deliberately gentle: a small deterministic drift, no gap and no outlier, so the
    observation times the *path* rather than tripping the platform validator's 3σ filter or
    its gap refusal. Those thresholds are not touched; see
    :func:`observe_ingest_processing_cost` for what happens when one of them fires anyway.
    """
    interval_ms = int((expected_interval_seconds(timeframe) or 60) * 1000)
    start = int(
        (datetime.now(timezone.utc) - timedelta(milliseconds=interval_ms * (count + 2)))
        .timestamp()
        * 1000
    )
    rows: List[List[float]] = []
    price = 100.0
    for index in range(count):
        price += 0.01 if index % 2 == 0 else -0.005
        rows.append(
            [
                start + index * interval_ms,
                price,
                price + 0.02,
                price - 0.02,
                price + 0.005,
                10.0 + (index % 7),
            ]
        )
    return rows


def observe_ingest_processing_cost(events: int = 2000, timeframe: str = "1m") -> List[str]:
    """Time Candidate B's ingest code path over generated rows. **Not a feed measurement.**

    Returns note lines for the report. Two numbers:

    * ``ClosedBarIngest.offer`` per event — the arrival-seam cost (closed-bar gate,
      duplicate check, ordering check, parse).
    * ``validated_window`` for the whole window — the seam plus the platform validator.

    Both are processing cost, not latency: there is no exchange timestamp involved, so
    neither can populate ``pipeline`` and neither is a decision input. What they do bound is
    how much pipeline latency Candidate B's validation layer can possibly add, which is the
    quantity ``design.md``'s hypothesis ("a small constant validation overhead") is about.

    If the platform validator refuses the generated window, that refusal is reported as the
    finding it is. Nothing here loosens ``OutlierDetector``'s 3σ filter or ``GapHandler``'s
    gap refusal to get a number out.
    """
    from backend_app.backend.market_data_contract import (
        ClosedBarIngest,
        MarketDataContractError,
        validated_window,
    )

    rows = _generated_closed_bars(events, timeframe)
    notes: List[str] = []

    ingest = ClosedBarIngest(timeframe, drop_late=True)
    per_event_ms: List[float] = []
    for row in rows:
        started = time.perf_counter()
        ingest.offer(row)
        per_event_ms.append((time.perf_counter() - started) * 1000.0)
    summary = LatencySummary.from_samples(per_event_ms)
    if summary is not None:
        notes.append(
            f"Processing cost, NOT a feed latency: `ClosedBarIngest.offer` over "
            f"{summary.count} generated {timeframe} rows — p50 {summary.p50_ms:.4f} ms, "
            f"p95 {summary.p95_ms:.4f} ms, p99 {summary.p99_ms:.4f} ms, "
            f"max {summary.max_ms:.4f} ms. Counters: {ingest.counters.to_dict()}. This is "
            f"the arrival seam's own cost with no exchange timestamp involved, so it is not "
            f"an end-to-end, ingest or pipeline latency and is not a decision input."
        )

    async def _timed_window() -> Tuple[Optional[float], Optional[str]]:
        started = time.perf_counter()
        try:
            await validated_window(rows, "BTC/USDT", timeframe, mode="backtest")
        except MarketDataContractError as exc:
            return None, f"{exc.code}: {exc.message}"
        except Exception as exc:  # pragma: no cover - surfaced as a finding, not swallowed
            return None, f"{type(exc).__name__}: {exc}"
        return (time.perf_counter() - started) * 1000.0, None

    elapsed_ms, refusal = asyncio.run(_timed_window())
    if elapsed_ms is not None:
        notes.append(
            f"Processing cost, NOT a feed latency: `validated_window` over the same "
            f"{len(rows)} rows — {elapsed_ms:.3f} ms for the whole window "
            f"({elapsed_ms / max(len(rows), 1):.4f} ms per bar), including the platform "
            f"`MarketDataValidator`. Also not a decision input."
        )
    else:
        notes.append(
            f"FINDING: the platform validator refused the generated window rather than "
            f"grading it ({refusal}). The 3σ outlier filter and the gap refusal are left "
            f"exactly as they are, so the whole-window processing cost is unmeasured here. "
            f"On a real feed this is the same classified DATA_QUALITY refusal earlier "
            f"phases mapped, and it would show up against Candidate B's completeness "
            f"rather than as a latency."
        )
    return notes


def observe_candidate_a_path_structure() -> List[str]:
    """Facts about Candidate A's delivery path, read off the shipped source.

    Not a measurement and not a floor input — a structural claim, checked rather than
    asserted in prose, that says why Candidate A's correctness is *unproven* rather than
    assumed fine. Two things are looked for in the relay path's own source:

    * whether anything on it imports the closed-bar / duplicate / ordering contract, and
    * whether the publisher republishes the latest (forming) candle per socket update.
    """
    notes: List[str] = []
    try:
        from backend_app.backend.data_seeking_engine import DataEngine

        relay = inspect.getsource(DataEngine.stream_live_ohlcv)
    except Exception as exc:  # pragma: no cover
        return [f"Candidate A's relay source could not be read ({type(exc).__name__}: {exc})."]

    gated = "market_data_contract" in relay or "ClosedBarIngest" in relay
    notes.append(
        f"Structural (source-read, not a measurement): "
        f"`DataEngine.stream_live_ohlcv` "
        f"{'references' if gated else 'does not reference'} the closed-bar ingest contract, "
        f"and applies no duplicate, ordering or OHLC-integrity rule of its own. A consumer "
        f"of this path therefore receives whatever the publisher sent, which is why "
        f"Candidate A's Requirement 19.11 figures cannot be assumed to be zero and must be "
        f"measured."
    )
    try:
        import backend_app.mds.main as mds

        publisher = inspect.getsource(mds.broadcast_ohlcv)
        republishes_latest = "candles[-1]" in publisher
        notes.append(
            f"Structural (source-read, not a measurement): `mds/main.py`'s "
            f"`broadcast_ohlcv` "
            f"{'republishes `candles[-1]` on every socket update' if republishes_latest else 'does not republish the latest candle per update'}"
            f", and its REST fallback polls every 10 s. Republishing the newest candle per "
            f"update means the same bar open time is published repeatedly while the bar is "
            f"still forming, so duplicates-delivered and forming-bar delivery are the "
            f"figures to watch for this candidate."
        )
    except Exception as exc:
        notes.append(
            f"Candidate A's publisher source could not be read "
            f"({type(exc).__name__}: {exc}); its per-update republish behaviour is therefore "
            f"unverified here."
        )
    return notes


# ══════════════════════════════════════════════════════════════════════════
# THE EXPERIMENT
# ══════════════════════════════════════════════════════════════════════════


def run_experiment(*, report_path: Optional[str] = None) -> Dict[str, Any]:
    """Measure both candidates as far as this environment permits, decide, and publish.

    Returns the decision plus the notes that went into the report, so a caller (a test, or
    a human running the script) can assert on it without re-parsing markdown.
    """
    window_seconds = _env_seconds()
    symbols = _env_tuple("AERORA_MARKET_DATA_LATENCY_SYMBOLS", PRESCRIBED_SYMBOLS)
    timeframes = _env_tuple("AERORA_MARKET_DATA_LATENCY_TIMEFRAMES", PRESCRIBED_TIMEFRAMES)
    path = report_path or _report_path()

    reason_a = probe_candidate_a()
    reason_b = probe_candidate_b()

    experiment_notes: List[str] = [
        f"Requested observation window: {window_seconds:.0f} s "
        + (
            "— the window `design.md` prescribes."
            if window_seconds >= PRESCRIBED_WINDOW_SECONDS
            else f"— **shorter than the {PRESCRIBED_WINDOW_SECONDS} s `design.md` prescribes**, "
            f"so this is a smoke run and not the prescribed experiment."
        ),
        f"Symbols requested: {', '.join(symbols)}.",
        f"Timeframes requested: {', '.join(timeframes)}.",
        f"Transport probe before measuring — Candidate A: "
        f"{'reachable' if reason_a is None else 'unreachable'}; Candidate B: "
        f"{'reachable' if reason_b is None else 'unreachable'}. A reachable transport is "
        f"necessary and not sufficient: a candidate can probe clean and still deliver "
        f"nothing, and that is reported as unmeasured rather than as a zero.",
        f"Forced-churn window with induced disconnects: not run. Inducing a disconnect "
        f"requires a connection that stays up.",
    ]
    environment_notes: List[str] = []

    a_notes = observe_candidate_a_path_structure()

    if reason_a is None:
        result_a = _measure_candidate_a(symbols, timeframes, window_seconds, a_notes)
    else:
        result_a = unmeasured(SOURCE_A, has_validation_layer=False, reason=reason_a)

    if reason_b is None:
        result_b = _measure_candidate_b(symbols, timeframes, window_seconds, [])
    else:
        result_b = unmeasured(SOURCE_B, has_validation_layer=True, reason=reason_b)

    # The structural read and the processing-cost timing are attached to whichever
    # candidate ended up unmeasured, because that is the run in which they are the only
    # observations available. They are labelled in the report and are never decision inputs.
    if not result_a.measured:
        result_a.notes = list(a_notes) + list(result_a.notes)
        environment_notes.append(f"Candidate A was not measured. {result_a.unavailable_reason}")
    if not result_b.measured:
        result_b.notes = list(result_b.notes) + observe_ingest_processing_cost()
        environment_notes.append(
            f"Candidate B was not measured against a live feed. {result_b.unavailable_reason}"
        )

    if not result_a.measured or not result_b.measured:
        environment_notes.append(
            "For every candidate above that was not measured there is no exchange timestamp, "
            "no arrival timestamp and no delivered-candle count, so none of `design.md`'s "
            "twelve metric rows can be derived for it. Nothing was synthesised to fill the "
            "gap: every unmeasured figure is reported as 'not measured', and a candidate is "
            "credited with no zero it did not earn."
        )
        environment_notes.append(
            "Consequence for the decision: an unmeasured candidate has demonstrated none of "
            "Requirement 19.11's admission criteria, so it fails the correctness floor with "
            "reason NOT_MEASURED and is inadmissible at any latency. With neither candidate "
            "measured the outcome is BLOCKED, which is the correct answer to 'which source "
            "may feed the order router' when nobody has measured either."
        )
        environment_notes.append(
            "To obtain a real decision: run this file with "
            f"{EXPERIMENT_ENV}=1 against an environment where the venue resolves, an "
            "authenticated CCXT connection can be established, and Redis and the MDS process "
            "are up — with AERORA_MARKET_DATA_LATENCY_SECONDS unset so the prescribed 24 h "
            "window is used."
        )

    decision = choose_market_data_source(result_a, result_b)
    written = write_decision_report(
        decision,
        path=path,
        environment_notes=environment_notes,
        experiment_notes=experiment_notes,
    )
    return {
        "decision": decision,
        "report_path": written,
        "environment_notes": environment_notes,
        "experiment_notes": experiment_notes,
    }


def _measure_candidate_a(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    window_seconds: float,
    notes: List[str],
) -> SourceMeasurement:
    """Candidate A over the live MDS relay. Runs only when :func:`probe_candidate_a` passes.

    Consumes ``DataEngine.stream_live_ohlcv`` — the path a real consumer uses — and records
    each delivered event. Nothing is de-duplicated, re-ordered or gated on the way in: the
    point is to measure what this candidate *delivers*, and applying Candidate B's rules to
    Candidate A's output would measure Candidate B twice.
    """
    from backend_app.backend.connection_engine import ConnectionEngine
    from backend_app.backend.data_seeking_engine import DataEngine

    timeframe = timeframes[0]
    recorder = ArrivalRecorder(timeframe)
    resources = ResourceSampler()
    invalid_ohlc = 0
    forming_delivered = 0

    async def _consume() -> None:
        nonlocal invalid_ohlc, forming_delivered
        # No credential is passed and none is read from the vault: OHLCV and ticks are
        # public market data, so the experiment needs no key. Constructing the engine
        # unauthenticated keeps the harness off `credential_vault` entirely.
        engine = ConnectionEngine(exchange_id=os.environ.get("DEFAULT_EXCHANGE", "binance"))
        exchange = await engine.connect()
        data = DataEngine(exchange)
        interval_ms = int((expected_interval_seconds(timeframe) or 60) * 1000)
        deadline = time.perf_counter() + window_seconds
        try:
            # Inside the try so the session is closed even on the refusal.
            refuse_if_mocked(exchange)
            async for batch in data.stream_live_ohlcv(symbols[0], timeframe):
                received_monotonic = time.perf_counter()
                received_wall_ms = time.time() * 1000.0
                for candle in batch or ():
                    stamp = int(candle[0])
                    open_, high, low, close = (float(candle[i]) for i in (1, 2, 3, 4))
                    if not (high >= max(open_, close) and low <= min(open_, close) and high >= low):
                        invalid_ohlc += 1
                    if received_wall_ms < stamp + interval_ms:
                        forming_delivered += 1
                    recorder.record(
                        exchange_timestamp_ms=stamp,
                        received_monotonic=received_monotonic,
                        dag_input_monotonic=time.perf_counter(),
                        received_wall_ms=received_wall_ms,
                        dag_input_wall_ms=time.time() * 1000.0,
                    )
                resources.sample()
                if time.perf_counter() >= deadline:
                    break
        finally:
            await engine.disconnect()

    failure: Optional[str] = None
    try:
        asyncio.run(_consume())
    except MockedFeed as exc:
        mocked = unmeasured(SOURCE_A, has_validation_layer=False, reason=str(exc))
        mocked.notes = list(notes)
        return mocked
    except Exception as exc:
        recorder.note_error()
        failure = f"{type(exc).__name__}: {exc}"
        notes.append(
            f"Candidate A's consumption ended with {failure}. Whatever was recorded before "
            f"that point is reported; nothing was extrapolated past it."
        )

    if not recorder.events:
        result = unmeasured(
            SOURCE_A,
            has_validation_layer=False,
            reason=(
                "Candidate A's Redis transport was reachable but the relay delivered no "
                "candle within the observation window, so there is nothing to measure"
                + (f". The run ended with {failure}" if failure else "")
                + "."
            ),
        )
        result.notes = list(notes)
        return result

    notes.append(
        f"Forming bars delivered (an open time whose interval had not yet elapsed at "
        f"arrival): {forming_delivered}. Requirement 19.2 forbids a forming bar reaching "
        f"indicator computation; this figure is reported alongside the floor metrics rather "
        f"than folded into one of them."
    )
    return recorder.to_measurement(
        source=SOURCE_A,
        has_validation_layer=False,
        symbols=symbols,
        timeframes=(timeframe,),
        window_seconds=None,
        invalid_ohlc_delivered=invalid_ohlc,
        # Candidate A performs no gap filling, so it delivers no synthetic candle: this is
        # a zero it earns rather than one it is credited with.
        synthetic_candles_delivered=0,
        resources=resources,
        notes=notes,
    )


def _measure_candidate_b(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    window_seconds: float,
    notes: List[str],
) -> SourceMeasurement:
    """Candidate B over the validated pipeline. Runs only when :func:`probe_candidate_b` passes.

    Polls ``DataEngine.fetch_historical_ohlcv`` and puts every batch through
    ``market_data_contract.validated_window``, so the window measured is the one an executor
    would actually receive. The correctness figures come from that path's **own** counters —
    ``IngestCounters`` and ``DataQualityReport`` — and are not re-tallied here (task 7.5).

    WHAT A REST POLL CAN AND CANNOT MEASURE — the reason this is not symmetrical with A
    ------------------------------------------------------------------------------------
    ``design.md`` defines Candidate B as ``data_seeking_engine`` -> ``ValidatedDataFeed`` ->
    ``dag_event_loop``. Its *live* arrival half reaches the DAG through the same MDS/Redis
    relay Candidate A uses, so with no Redis there is no arrival event for it either.

    What is left is the historical half, and it is genuinely measurable — but only for the
    metrics that do not depend on **when a bar arrived**:

    * **Measurable, and reported.** Pipeline latency (the wall time ``validated_window`` takes
      on the batch an executor will read), the delivered window's completeness over the bar
      range it covers, the ingest counters, the validator's integrity counts, CPU, RSS and
      throughput.
    * **Not measurable, and therefore left ``None``.** End-to-end and ingest latency. A
      historical batch's oldest bar is as old as the batch is deep, so
      ``received_wall − exchange_timestamp`` measures *how old the bar is*, not how long
      delivery took. An earlier revision of this harness published that subtraction as a
      540-second "end-to-end p99" — exactly the fabrication this file exists to avoid. Clock
      skew is dropped for the same reason: the quantity available is bar age, not skew.
    * **Not measurable on this path either.** Arrival ordering. Each poll re-reads an
      overlapping window, so an out-of-order arrival seen *across* polls is a property of the
      polling loop, not of the feed. What is reported instead is the pipeline's own
      ``IngestCounters`` per batch, which is a statement about what the contract did with what
      it was handed — and that is a real fact about Candidate B.
    """
    from backend_app.backend.connection_engine import ConnectionEngine
    from backend_app.backend.data_seeking_engine import DataEngine
    from backend_app.backend.market_data_contract import (
        MarketDataContractError,
        validated_window,
    )

    timeframe = timeframes[0]
    interval_seconds = expected_interval_seconds(timeframe)
    resources = ResourceSampler()

    pipeline_ms: List[float] = []
    delivered: set = set()
    duplicates = 0
    out_of_order = 0
    late = 0
    validator_duplicates = 0
    invalid_candles = 0
    ohlc_violations = 0
    gaps_found = 0
    errors = 0
    refusals: List[str] = []
    polls = 0
    started_at = time.perf_counter()

    async def _poll() -> None:
        nonlocal duplicates, out_of_order, late, invalid_candles, ohlc_violations
        nonlocal validator_duplicates, gaps_found, errors, polls
        # Unauthenticated, deliberately — see the note in `_measure_candidate_a`.
        engine = ConnectionEngine(exchange_id=os.environ.get("DEFAULT_EXCHANGE", "binance"))
        exchange = await engine.connect()
        data = DataEngine(exchange)
        poll_seconds = max(1.0, float(interval_seconds or 60) / 4.0)
        deadline = time.perf_counter() + window_seconds
        try:
            # Inside the try so the session is closed even on the refusal.
            refuse_if_mocked(exchange)
            while time.perf_counter() < deadline:
                polls += 1
                try:
                    rows = await data.fetch_historical_ohlcv(symbols[0], timeframe, limit=1000)
                except Exception:
                    errors += 1
                    await asyncio.sleep(poll_seconds)
                    continue

                # The pipeline segment, timed on the real batch: the closed-bar gate, the
                # duplicate and ordering resolution, then the platform validator. This is a
                # latency an executor actually waits for, unlike anything derived from bar age.
                segment_started = time.perf_counter()
                try:
                    result = await validated_window(rows, symbols[0], timeframe, mode="paper")
                except MarketDataContractError as exc:
                    errors += 1
                    refusals.append(f"{exc.code}: {exc.message}")
                    await asyncio.sleep(poll_seconds)
                    continue
                pipeline_ms.append((time.perf_counter() - segment_started) * 1000.0)

                # The contract's own counters, read rather than recomputed (task 7.5).
                duplicates += int(result.counters.duplicates)
                out_of_order += int(result.counters.out_of_order)
                late += int(result.counters.late_events)
                validator_duplicates += int(
                    getattr(result.report, "duplicate_timestamps", 0) or 0
                )
                invalid_candles += int(getattr(result.report, "invalid_candles", 0) or 0)
                ohlc_violations += int(getattr(result.report, "ohlc_violations", 0) or 0)
                gaps_found += int(getattr(result.report, "gaps_found", 0) or 0)

                for stamp in result.frame.index:
                    delivered.add(int(stamp.value // 1_000_000))
                resources.sample()
                await asyncio.sleep(poll_seconds)
        finally:
            await engine.disconnect()

    failure: Optional[str] = None
    try:
        asyncio.run(_poll())
    except MockedFeed as exc:
        mocked = unmeasured(SOURCE_B, has_validation_layer=True, reason=str(exc))
        mocked.notes = list(notes)
        return mocked
    except Exception as exc:
        errors += 1
        failure = f"{type(exc).__name__}: {exc}"
        notes.append(
            f"Candidate B's polling ended with {failure}. Whatever was recorded before that "
            f"point is reported; nothing was extrapolated past it."
        )

    elapsed = time.perf_counter() - started_at
    if not delivered:
        empty = unmeasured(
            SOURCE_B,
            has_validation_layer=True,
            reason=(
                "The exchange host answered a TCP probe but the validated pipeline delivered "
                "no closed bar within the observation window, so there is nothing to measure"
                + (f". The run ended with {failure}" if failure else "")
                + (f". Validator refusals seen: {'; '.join(refusals[:5])}" if refusals else "")
                + "."
            ),
        )
        empty.notes = list(notes)
        return empty

    # Completeness over the bar range actually covered, derived from the bar timestamps
    # themselves rather than from wall-clock arrivals. The grid is what "expected" means: a
    # closed 1m bar is due every 60 s between the oldest and the newest bar delivered, and a
    # missing one is a real completeness shortfall. The interval comes from
    # `feed_state.expected_interval_seconds` (`TIMEFRAME_MINUTES`), never from a guess.
    stamps = sorted(delivered)
    expected_bars: Optional[int] = None
    if interval_seconds:
        span_ms = stamps[-1] - stamps[0]
        expected_bars = int(span_ms // int(interval_seconds * 1000)) + 1

    if refusals:
        notes.append(
            f"The platform validator refused {len(refusals)} of {polls} window(s) during the "
            f"run (first: {refusals[0]}). `OutlierDetector`'s 3σ filter and `GapHandler`'s gap "
            f"refusal were left exactly as they are, so a refusal costs Candidate B "
            f"completeness rather than admitting data under looser rules. On a real 24 h run "
            f"this is the figure to watch: a strict validator that refuses often is a "
            f"completeness problem, and it is reported as one here instead of being tuned away."
        )
    notes += [
        "End-to-end and ingest latency are NOT reported for this candidate. This run reached "
        "it through `fetch_historical_ohlcv`, where the gap between a bar's exchange timestamp "
        "and its arrival is the bar's age in the batch, not delivery latency. Reporting that "
        "subtraction would publish a fabricated figure, so it is left unmeasured. The pipeline "
        "segment — the wall time `validated_window` takes on the batch an executor reads — is "
        "the one latency this path can honestly produce, and it is the quantity `design.md`'s "
        "hypothesis about the validation layer's constant overhead is about.",
        f"Delivered-stream monotonicity is 0, and it is earned rather than assumed: "
        f"`ClosedBarIngest.frame()` returns a sorted, de-duplicated index, so the series an "
        f"executor sees is monotonic by construction. The arrival-order facts are the "
        f"contract's own counters — {out_of_order} out-of-order arrivals and {late} late events "
        f"dropped at the seam (Requirement 19.3) across {polls} poll(s).",
        f"Validator-side `duplicate_timestamps` over the delivered windows: "
        f"{validator_duplicates}, against {duplicates} resolved at the ingest seam. The first "
        f"is expected to read 0 on this path because first-wins is applied before the "
        f"validator sees the frame. The two are reported side by side and never summed "
        f"(task 7.5).",
        f"Gaps reported by the validator across the run: {gaps_found}.",
    ]

    cpu = resources.summary() if resources.available else {}
    return SourceMeasurement(
        source=SOURCE_B,
        has_validation_layer=True,
        measured=True,
        symbols=(symbols[0],),
        timeframes=(timeframe,),
        window_seconds=elapsed,
        events_observed=len(delivered),
        end_to_end=None,
        ingest=None,
        pipeline=LatencySummary.from_samples(pipeline_ms),
        cpu_percent_mean=cpu.get("cpu_percent_mean"),
        cpu_percent_p95=cpu.get("cpu_percent_p95"),
        rss_start_bytes=resources.rss_start,
        rss_end_bytes=resources.rss_end,
        throughput_events_per_second=(
            None if elapsed <= 0 else len(delivered) / float(elapsed)
        ),
        reconnects=0,
        expected_bars=expected_bars,
        received_bars=len(delivered),
        monotonic_violations=0,
        clock_skew_ms=None,
        duplicates_delivered=0,
        out_of_order_delivered=0,
        invalid_ohlc_delivered=invalid_candles + ohlc_violations,
        # `resolve_gap_policy` refuses every fabricating strategy in every mode and
        # `GapHandler` fills no gap, so this path cannot deliver a synthetic candle.
        synthetic_candles_delivered=0,
        error_count=errors,
        silent_stall_episodes=0,
        uptime_fraction=None if not polls else max(0.0, 1.0 - (errors / float(polls))),
        notes=list(notes),
    )


# ══════════════════════════════════════════════════════════════════════════
# THE TESTS — the experiment's own assertions
# ══════════════════════════════════════════════════════════════════════════


@experiment_only
def test_the_experiment_publishes_a_decision_report(tmp_path):
    """Run the experiment and assert the Requirement 19.13 record says what happened."""
    outcome = run_experiment(report_path=str(tmp_path / "market_data_latency_decision.md"))
    decision = outcome["decision"]
    body = open(outcome["report_path"], encoding="utf-8").read()

    assert "# Market data source decision" in body
    assert decision.rule in body
    assert f"{P99_MARGIN_MS:.0f} ms" in body

    # Whatever the environment allowed, the document must reconcile with the decision.
    if decision.blocked:
        assert decision.selected is None
        assert "BLOCKED" in body
        for verdict in decision.floor:
            assert not verdict.passed
            assert verdict.rejection_reason
            assert verdict.rejection_detail
            assert verdict.rejection_detail in body
    else:
        verdict = decision.verdict_for(decision.selected)
        assert verdict is not None and verdict.passed, (
            "a source was selected whose correctness floor verdict does not pass"
        )
        assert decision.selected in body

    # Nothing unmeasured may be reported as a number.
    for measurement in decision.measurements:
        if not measurement.measured:
            assert measurement.unavailable_reason
            assert measurement.completeness is None
            assert measurement.p99_end_to_end_ms is None
            assert measurement.duplicates_delivered is None
            assert measurement.synthetic_candles_delivered is None


@experiment_only
def test_the_experiment_writes_the_report_the_spec_names():
    """The published artefact ``design.md`` and task 7.9 look for.

    Written to the real path, because ``reports/market_data_latency_decision.md`` being
    present and honest *is* the deliverable.
    """
    outcome = run_experiment()
    assert os.path.exists(outcome["report_path"])
    body = open(outcome["report_path"], encoding="utf-8").read()
    assert "Correctness floor (Requirement 19.11)" in body
    assert "Measured figures (Requirement 19.13)" in body
    if outcome["environment_notes"]:
        assert "What was not measured, and why" in body


@experiment_only
def test_an_unreachable_feed_is_reported_as_unmeasured_and_never_selected():
    """The honest-degradation path, asserted rather than hoped for."""
    reason_a, reason_b = probe_candidate_a(), probe_candidate_b()
    if reason_a is None and reason_b is None:
        pytest.skip("both feeds are reachable here, so there is no degradation to assert")

    outcome = run_experiment()
    decision = outcome["decision"]
    for measurement in decision.measurements:
        if measurement.measured:
            continue
        verdict = decision.verdict_for(measurement.source)
        assert verdict is not None and not verdict.passed
        assert verdict.rejection_reason == "NOT_MEASURED"
        assert decision.selected != measurement.source


@experiment_only
def test_the_ingest_path_processing_cost_is_observed_and_labelled():
    """The one thing this environment can honestly time, and it is labelled as such."""
    notes = observe_ingest_processing_cost(events=500)
    assert notes
    assert any("NOT a feed latency" in note or "FINDING" in note for note in notes)


@experiment_only
def test_candidate_a_path_structure_is_read_from_source():
    """The structural claim about Candidate A is checked, not asserted in prose."""
    notes = observe_candidate_a_path_structure()
    assert notes
    assert any("Structural (source-read" in note for note in notes)


@experiment_only
def test_a_dev_mode_mock_connection_is_refused_rather_than_measured():
    """The harness's most important safeguard: generated candles are never measured.

    ``ConnectionEngine.connect`` returns a working exchange object backed by
    ``_apply_mock_interface`` when the venue fails and ``DEV_MODE`` is set, and this
    environment hits that path. Measuring it would publish fiction, so it is a refusal.
    """

    class Mocked:
        async def mock_fetch_ohlcv(self, *args, **kwargs):  # pragma: no cover
            return []

        fetch_ohlcv = mock_fetch_ohlcv

    class Live:
        async def fetch_ohlcv(self, *args, **kwargs):  # pragma: no cover
            return []

    assert mocked_methods(Mocked()) == ["fetch_ohlcv"]
    assert mocked_methods(Live()) == []

    with pytest.raises(MockedFeed) as raised:
        refuse_if_mocked(Mocked())
    assert "DEV_MODE" in str(raised.value)

    refuse_if_mocked(Live())  # must not raise


@experiment_only
def test_a_measurement_that_names_no_floor_metric_is_inadmissible():
    """A measured-but-uninstrumented candidate is rejected, not admitted by default."""
    partial = SourceMeasurement(source=SOURCE_A, has_validation_layer=False, measured=True)
    partial.end_to_end = LatencySummary.from_samples([1.0, 2.0, 3.0])
    verdict = evaluate_correctness_floor(partial)
    assert not verdict.passed
    assert verdict.rejection_reason == "FLOOR_METRICS_NOT_MEASURED"
