"""tests/perf/test_strategy_builder_budgets.py - the compile and validation budgets, measured.

Spec: strategy-builder task 9.6. Requirements 25.1, 25.2; ``design.md`` -> Performance
("Compile budget | p95 < 50 ms at 200 nodes", "Validation budget | p95 < 120 ms server-side")
and ``design.md`` -> Testing strategy -> Performance.

RUN IT WITH
-----------
::

    .venv\\Scripts\\python.exe -m pytest tests/perf/test_strategy_builder_budgets.py -q -s

``-s`` is worth it: the measured table is printed, and a budget nobody can see the distance to
is a budget nobody will notice moving. The whole file is about a minute and a half - it needs
no feed, no Redis, no exchange, no database and no network.

WHY IT IS IN ``tests/perf`` AND NOT IN THE DEFAULT LANE
-------------------------------------------------------
``pytest.ini`` names ``tests/perf`` in ``norecursedirs``, so ``pytest -q`` does not collect
this directory. That exclusion was added by task 7.6 for a 24 h live-feed experiment, and the
reason it is reused here is different and worth stating: **a wall-clock assertion on a shared
runner fails for reasons that have nothing to do with the code under test.** The backend suite
carries a pinned failure ceiling that every task from 8.6 onward reconciles against; a timing
test that flakes under a noisy neighbour would turn that ceiling into noise, and "the build is
red because the runner was busy" is exactly how a real regression gets waved through.

Unlike task 7.6's harness this file is **not** gated a second time on an environment variable.
7.6 needed that because ``pytest tests/perf`` would otherwise start a 24 h measurement by
accident. This one finishes in a minute and a half, so ``pytest tests/perf`` should run it -
that is how task 9.7 confirms the budgets at 200 nodes.

The deterministic half of task 9.6 - that the graphs are the sizes they claim, that they
validate, that the 200-node one sits on Requirement 25.4's ceiling, that the p95 is a real
nearest-rank percentile, and Requirement 25.3's cache-hit ratio - is in the default lane, in
``tests/test_task_9_6_performance_budgets.py``. The graph builders come from there, so both
lanes mean the same thing by "a 200-node graph".

WHAT IS MEASURED, AND WHERE THE FIGURES COME FROM
-------------------------------------------------
Every percentile is read from the **shipped instrumentation**, not from a stopwatch wrapped
around the call: ``metrics.builder_compile_duration_ms`` and
``metrics.builder_validation_duration_ms``. Task 9.1 made those two ``Summary`` rather than
``Histogram`` specifically so this task could read 25.1 and 25.2 off an exact nearest-rank
percentile instead of a bucket edge, and it bucketed the compile metric by
``metrics.NODE_COUNT_BUCKETS`` - 10 / 50 / 100 / 200 - which are the measurement points
``design.md`` names. Both of those decisions are load-bearing here. The percentile method
delegates to ``market_data_latency.LatencySummary.from_samples``, so there is one nearest-rank
implementation in the codebase.

Four subjects - five rows, because the debounce path is read twice - at each of 10 / 50 / 100 /
200 nodes and again at the 200-node / 400-edge legal maximum:

1. **compile, request path.** ``strategy_builder.compile_version`` - what
   ``POST /api/strategies/compile``, the save path, the clone path and the deploy path all go
   through. It validates once and hands the report to ``compile_plan(..., report=report)``, so
   the recorded duration is the compile: order, levels, plan assembly, postconditions and
   hash. This is the figure Requirement 25.1's 50 ms is about.
2. **compile, no report.** ``strategy_compiler.compile_graph`` without a precomputed report -
   the shape three ``strategy_operations`` call sites use. ``compile_plan`` measures from
   after the parse, so on this path the recorded "compile" duration **contains the
   validation**. Reported as its own row rather than blended into row 1, because a figure that
   silently means two different things is worse than two figures.
3. **validation.** ``validator.validate`` directly.
4. **validation under the debounce path.** The real ``strategies.validate_strategy`` handler,
   called with the body the client's debounced POST actually carries: the canonical envelope
   at the top level, not wrapped in ``{"dag": ...}``. Both halves are read - the server-side
   validation the metric records, and the handler's whole wall time including the parse and
   the response assembly - plus a smaller sample of real HTTP round trips through the mounted
   app. The 400 ms interval itself, and the coalescing that makes ten keystrokes cost one
   request, are the client's and are asserted in
   ``algo22-terminal/tests/unit/graphValidation.test.js`` and
   ``strategyBuilder.validation.test.jsx`` (task 3.10); what is measured here is what the
   request that survives the debounce costs the server.

THE ONE TIGHT FIGURE, STATED UP FRONT
-------------------------------------
Three of the four series clear their budget by 4x to 6x at the maximum legal graph. The fourth
does not: **compile without a precomputed report is ~40 ms of a 50 ms budget at 200 nodes and
400 edges - about 1.2x.** It passes, it is the honest number, and it is the row that would
breach first on slower hardware. The cause is located rather than guessed at: the excess over
the request path is the validation that path also runs, which
:meth:`TestTheCompileBudget.test_the_tightest_series_is_the_one_that_folds_validation_into_the_metric`
asserts. The remedy, if it ever breaches, is the one ``strategy_builder.compile_version``
already applies - validate once and hand the report to ``compile_plan`` - and the three
``strategy_operations`` call sites that pass no report are where it would be applied. Nothing
was tuned to make that row pass, and it is not given a looser budget than the row beside it.

HONESTY RULES THIS FILE FOLLOWS
-------------------------------
* ``WARMUP`` iterations are run and discarded before every series, and ``gc.collect()`` is
  called before each one. That is not tuning: the first call through a path pays lazy imports,
  and a generational collection triggered by the *previous* series' garbage would otherwise be
  charged to this one. Garbage collection stays **enabled** during measurement, so a real
  pause is still counted, and ``max_ms`` is reported alongside every p95 so an outlier is
  visible rather than hidden behind the percentile.
* Nothing is retried and no sample is discarded after the fact.
* The failure message on every budget assertion prints the measured figure, the budget and the
  margin, so a red run says what the number was.
* No control is touched. ``tests/conftest.py``'s ``VYOMQUANT_MODE=safe`` freeze stays on -
  nothing here executes a plan or places an intent - the authenticated identity is the only
  dependency override, and the graphs are validated rather than forced past the validator.
"""
from __future__ import annotations

import asyncio
import gc
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest

from backend_app.backend import metrics as M
from backend_app.backend import strategy_builder as SB
from backend_app.backend import strategy_compiler as SC
from backend_app.backend.market_data_latency import LatencySummary
from backend_app.backend.strategy_dag import validator as V
from backend_app.core.dependencies import get_current_user, get_request_supabase
from tests.test_task_9_6_performance_budgets import (
    CLIENT_DEBOUNCE_MS,
    COMPILE_BUDGET_MS,
    MEASUREMENT_POINTS,
    VALIDATION_BUDGET_MS,
    assembled_registry,
    ceiling_graph,
    debounce_body,
    fan_graph,
)

# ══════════════════════════════════════════════════════════════════════════
#  SAMPLING
# ══════════════════════════════════════════════════════════════════════════

#: Observations per series. The nearest-rank p95 of 120 samples is the 114th value, so the
#: figure is decided by six observations rather than by one - which matters, because a p95 read
#: from twenty samples is really "the second worst of twenty". 120 x the ~30 ms a 200-node
#: compile-plus-validate costs is about four seconds per series; twenty-five series plus the
#: HTTP row put the whole file at about a minute and a half. Well inside
#: ``Summary.DEFAULT_CAPACITY`` (4096), which the default lane asserts.
SAMPLES = 120

#: Discarded before every series. The first call through a path pays one-off costs - lazy
#: imports, first-touch of a descriptor dict - that are real but are not what a p95 over an
#: editing session looks like.
WARMUP = 5

#: HTTP round trips at 200 nodes. Fewer than ``SAMPLES``, because each one spins an event loop
#: and the figure is a cross-check on the handler measurement rather than the primary read. At
#: 60 the nearest-rank p95 is the 57th value, so this row is the coarsest in the file and is
#: read as an order-of-magnitude confirmation rather than as a precise percentile.
HTTP_SAMPLES = 60

#: Server-side identity for the handler and HTTP measurements. Requirement 21.1 keeps
#: ``Depends(get_current_user)`` on the endpoint; supplying an identity is not removing it.
USER = {
    "id": "usr_task96_perf",
    "email": "perf@example.com",
    "role": "authenticated",
    "access_token": "token_task96_perf",
}


@dataclass(frozen=True)
class Series:
    """One measured population, and what it was a population of."""

    subject: str
    node_count: int
    edge_count: int
    summary: LatencySummary
    budget_ms: float

    @property
    def p95_ms(self) -> float:
        return self.summary.p95_ms

    @property
    def margin(self) -> float:
        """How many times over the measured p95 fits the budget."""
        return self.budget_ms / self.p95_ms if self.p95_ms else float("inf")

    def row(self) -> str:
        return (
            f"  {self.subject:<34} {self.node_count:>4} nodes {self.edge_count:>4} edges  "
            f"p50 {self.summary.p50_ms:7.2f}  p95 {self.summary.p95_ms:7.2f}  "
            f"p99 {self.summary.p99_ms:7.2f}  max {self.summary.max_ms:7.2f}  "
            f"n={self.summary.count:>4}  budget {self.budget_ms:.0f} ms "
            f"({self.margin:.1f}x headroom)"
        )


def _clear(metric: M.Summary) -> None:
    """Drop the retained ring, so a series' percentiles are that series' percentiles.

    ``Summary`` keeps a bounded ring per label combination and the collector is a process
    singleton, so a series measured after another one would otherwise read a p95 over both.
    The lifetime sum and count go with it: they are what the Prometheus exporter divides to
    report a mean, and a sum left behind after its samples were dropped would describe a
    population that no longer exists. The ``measured`` fixture clears both metrics again when
    it is done, so this file leaves the collector as it found it.
    """
    metric.samples.clear()
    metric.sum_values.clear()
    metric.count_values.clear()


def _observe(
    call: Callable[[], Any],
    metric: M.Summary,
    labels: Optional[Dict[str, Any]] = None,
) -> LatencySummary:
    """Run ``call`` ``SAMPLES`` times and read the percentiles off ``metric``."""
    labels = labels or {}
    for _ in range(WARMUP):
        call()
    gc.collect()
    _clear(metric)
    for _ in range(SAMPLES):
        call()
    summary = metric.summary(**labels)
    assert summary is not None, "the instrumentation recorded nothing to measure"
    assert summary.count == SAMPLES, (
        f"expected {SAMPLES} recorded observations, the metric holds {summary.count} - "
        f"the population being measured is not the population being run"
    )
    return summary


def _wall(call: Callable[[], Any]) -> LatencySummary:
    """``SAMPLES`` wall-clock observations of ``call``, for work no metric records."""
    for _ in range(WARMUP):
        call()
    gc.collect()
    observed: List[float] = []
    for _ in range(SAMPLES):
        started = time.perf_counter()
        call()
        observed.append((time.perf_counter() - started) * 1000.0)
    summary = LatencySummary.from_samples(observed)
    assert summary is not None
    return summary


# ══════════════════════════════════════════════════════════════════════════
#  THE MEASUREMENT, RUN ONCE
# ══════════════════════════════════════════════════════════════════════════

#: ``fan_graph`` at each measurement point, plus the maximum legal graph. The ceiling case is
#: filed under the same ``node_count`` label ("200") as the fan case, which is why the two are
#: measured in separate series rather than in one loop.
CASES: Tuple[Tuple[str, int], ...] = tuple(
    (f"{count} nodes", count) for count in MEASUREMENT_POINTS
) + (("200 nodes / 400 edges", V.LIMITS.max_nodes),)


@pytest.fixture(scope="module")
def reg():
    return assembled_registry()


@pytest.fixture(scope="module")
def graphs(reg) -> Dict[str, Any]:
    """One graph per case, each validated before it is timed.

    A refused graph would make the compile series a measurement of the refusal path, which is
    a different and much cheaper piece of work. The default lane asserts the same thing; it is
    re-asserted here so this file cannot be run alone against a broken builder and still
    report a comfortable margin.
    """
    built = {label: fan_graph(reg, count) for label, count in CASES[:-1]}
    built[CASES[-1][0]] = ceiling_graph(reg)
    for label, graph in built.items():
        report = V.validate(graph, reg)
        assert report.valid, f"{label} does not validate: {report.codes()}"
    return built


@pytest.fixture(scope="module")
def measured(reg, graphs) -> Dict[str, Series]:
    """Every series, measured once. Keyed ``"<subject>@<case>"``."""
    collector = M.metrics_collector
    compile_metric = collector.builder_compile_duration_ms
    validation_metric = collector.builder_validation_duration_ms
    handler = _handler()

    out: Dict[str, Series] = {}
    for label, count in CASES:
        graph = graphs[label]
        edges = len(graph.edges)
        bucket = M.node_count_bucket(count)
        body = debounce_body(graph)

        out[f"compile.request_path@{label}"] = Series(
            "compile (request path)",
            count,
            edges,
            _observe(
                lambda g=graph: SB.compile_version(g, reg),
                compile_metric,
                {"node_count": bucket},
            ),
            COMPILE_BUDGET_MS,
        )
        out[f"compile.no_report@{label}"] = Series(
            "compile (validation included)",
            count,
            edges,
            _observe(
                lambda g=graph: SC.compile_graph(g, reg),
                compile_metric,
                {"node_count": bucket},
            ),
            COMPILE_BUDGET_MS,
        )
        out[f"validation@{label}"] = Series(
            "validation",
            count,
            edges,
            _observe(lambda g=graph: V.validate(g, reg), validation_metric),
            VALIDATION_BUDGET_MS,
        )
        out[f"debounce.server@{label}"] = Series(
            "validation (debounce path)",
            count,
            edges,
            _observe(lambda b=body: asyncio.run(handler(b)), validation_metric),
            VALIDATION_BUDGET_MS,
        )
        out[f"debounce.handler@{label}"] = Series(
            "debounce handler, end to end",
            count,
            edges,
            _wall(lambda b=body: asyncio.run(handler(b))),
            VALIDATION_BUDGET_MS,
        )
    _clear(compile_metric)
    _clear(validation_metric)
    return out


def _handler() -> Callable[[Dict[str, Any]], Any]:
    """The real validate handler, with the identity supplied and nothing else changed.

    The coroutine is called directly rather than over HTTP for the percentile population:
    Requirement 25.2's budget is on the **server-side** duration, and httpx's own request
    encoding and response decoding are the client's cost, not the server's. ``TestClient`` is
    used as well, below, so the figure is not only a function-call measurement.
    """
    from backend_app.routers.strategies import validate_strategy

    async def call(body: Dict[str, Any]):
        return await validate_strategy(body=body, user=USER)

    return call


@pytest.fixture(scope="module")
def http_round_trip(graphs) -> Tuple[LatencySummary, Dict[str, Any]]:
    """``HTTP_SAMPLES`` real ``POST /api/strategies/validate`` calls at 200 nodes."""
    from fastapi.testclient import TestClient

    from backend_app.main import app

    body = debounce_body(graphs["200 nodes"])
    app.dependency_overrides[get_current_user] = lambda: USER
    app.dependency_overrides[get_request_supabase] = lambda: None
    try:
        with TestClient(app) as client:
            first = client.post("/api/strategies/validate", json=body)
            assert first.status_code == 200, first.text
            payload = first.json()
            observed: List[float] = []
            for _ in range(HTTP_SAMPLES):
                started = time.perf_counter()
                response = client.post("/api/strategies/validate", json=body)
                observed.append((time.perf_counter() - started) * 1000.0)
                assert response.status_code == 200
    finally:
        app.dependency_overrides.clear()
    summary = LatencySummary.from_samples(observed)
    assert summary is not None
    return summary, payload


@pytest.fixture
def report(request, capsys):
    """Emit measured figures under ``-s``. The numbers are the deliverable."""
    enabled = request.config.getoption("capture") == "no"

    def emit(line: str) -> None:
        if not enabled:
            return
        with capsys.disabled():
            print(line)

    return emit


def _assert_within(series: Series) -> None:
    """One assertion shape for every budget, so none can be the lenient one."""
    assert series.p95_ms <= series.budget_ms, (
        f"{series.subject} at {series.node_count} nodes / {series.edge_count} edges: "
        f"p95 {series.p95_ms:.2f} ms exceeds the {series.budget_ms:.0f} ms budget by "
        f"{series.p95_ms - series.budget_ms:.2f} ms "
        f"(p50 {series.summary.p50_ms:.2f}, p99 {series.summary.p99_ms:.2f}, "
        f"max {series.summary.max_ms:.2f}, n={series.summary.count})"
    )


# ══════════════════════════════════════════════════════════════════════════
#  1. THE TABLE
# ══════════════════════════════════════════════════════════════════════════


class TestTheMeasuredTable:
    def test_every_series_was_measured_and_is_printed(self, measured, report):
        """Not a budget check - the record of what the run saw, in one place."""
        report(f"\n  SAMPLES={SAMPLES} WARMUP={WARMUP} (nearest-rank percentiles, ms)\n")
        for label, _count in CASES:
            for key in (
                "compile.request_path",
                "compile.no_report",
                "validation",
                "debounce.server",
                "debounce.handler",
            ):
                report(measured[f"{key}@{label}"].row())
            report("")
        assert len(measured) == 5 * len(CASES)

    def test_the_http_round_trip_is_reported_beside_the_handler_figure(
        self, http_round_trip, report
    ):
        summary, payload = http_round_trip
        report(
            f"  {'POST /validate, over HTTP':<34} {200:>4} nodes {200:>4} edges  "
            f"p50 {summary.p50_ms:7.2f}  p95 {summary.p95_ms:7.2f}  "
            f"p99 {summary.p99_ms:7.2f}  max {summary.max_ms:7.2f}  n={summary.count:>4}"
        )
        assert payload["valid"] is True
        assert payload["dag_hash"]


# ══════════════════════════════════════════════════════════════════════════
#  2. REQUIREMENT 25.1 - COMPILE, p95 <= 50 ms
# ══════════════════════════════════════════════════════════════════════════


class TestTheCompileBudget:
    """"WHEN a Canonical_Graph of 200 nodes is compiled, THE Strategy_Compiler SHALL
    complete compilation with a 95th-percentile duration of 50 milliseconds or less.\""""

    def test_the_request_path_meets_the_budget_at_200_nodes(self, measured):
        """The criterion itself, on the path a request takes."""
        _assert_within(measured["compile.request_path@200 nodes"])

    @pytest.mark.parametrize("label", [label for label, _ in CASES])
    def test_the_request_path_meets_the_budget_at_every_measurement_point(
        self, measured, label
    ):
        _assert_within(measured[f"compile.request_path@{label}"])

    def test_the_maximum_legal_graph_meets_the_budget(self, measured):
        """200 nodes *and* 400 edges: the largest graph Requirement 25.4 permits.

        25.1 states its budget in nodes, and compile cost is a function of edges too, so the
        node ceiling alone is not the worst legal case. This is.
        """
        _assert_within(measured["compile.request_path@200 nodes / 400 edges"])

    @pytest.mark.parametrize("label", [label for label, _ in CASES])
    def test_the_no_report_path_also_meets_the_budget(self, measured, label):
        """The three ``strategy_operations`` call sites that pass no precomputed report.

        On that path ``compile_plan`` measures from after the parse, so the duration the
        metric records **includes the validation** - the figure is a compile-plus-validate and
        is labelled that way in the table. It is asserted against the compile budget anyway,
        because that is what those call sites publish under
        ``builder.compile.duration_ms``: a dashboard reading 25.1's series cannot tell which
        call site produced a sample, so the budget has to hold for both or the series is not
        the criterion's series.
        """
        _assert_within(measured[f"compile.no_report@{label}"])

    def test_compile_alone_is_the_cheaper_half_of_the_two_paths(self, measured):
        """A sanity check on what the two rows mean, not a budget.

        The request path excludes validation and the no-report path includes it, so the first
        must be strictly cheaper. If it ever were not, one of the two rows is measuring
        something other than what it says.
        """
        with_report = measured["compile.request_path@200 nodes"].p95_ms
        without = measured["compile.no_report@200 nodes"].p95_ms
        assert with_report < without, (
            f"compile-with-report p95 {with_report:.2f} ms is not below "
            f"compile-plus-validate p95 {without:.2f} ms"
        )

    def test_the_measured_margin_is_not_marginal(self, measured):
        """Recorded as an assertion so a slide from 6x to 1.05x is a red test, not a note.

        Set at 2x, well under the ~5.6x measured, so this is a regression alarm and not a
        second budget competing with Requirement 25.1's. Only the request path carries this
        alarm: the no-report row's margin is genuinely tight today and the test below says so
        rather than pretending otherwise.
        """
        series = measured["compile.request_path@200 nodes / 400 edges"]
        assert series.margin >= 2.0, (
            f"the compile budget still holds (p95 {series.p95_ms:.2f} ms <= "
            f"{series.budget_ms:.0f} ms) but the headroom has fallen to "
            f"{series.margin:.1f}x, from the ~5.6x measured when this was written"
        )

    def test_the_tightest_series_is_the_one_that_folds_validation_into_the_metric(
        self, measured, report
    ):
        """The finding this file exists to surface, asserted rather than left in prose.

        At the maximum legal graph the no-report row's p95 is ~40 ms against 50 ms - a margin
        of ~1.2x, the tightest figure in the file and the one that would breach first on
        slower hardware. It is tight for a locatable reason and not because assembly is slow:
        the excess over the request path is the *validation* it also runs, which is checked
        here so the diagnosis cannot be guessed at. The remedy, if it ever breaches, is the
        one ``strategy_builder.compile_version`` already applies - validate once and pass the
        report - and the three ``strategy_operations`` call sites that do not are where it
        would be applied.
        """
        tightest = min(measured.values(), key=lambda series: series.margin)
        expected = measured["compile.no_report@200 nodes / 400 edges"]
        assert tightest is expected, (
            f"the tightest series is now {tightest.subject} at {tightest.node_count} nodes "
            f"({tightest.margin:.1f}x), not the compile-plus-validate row - the shape of the "
            f"cost has changed and this file's diagnosis no longer describes it"
        )
        report(
            f"\n  tightest series: {expected.subject} at {expected.node_count} nodes / "
            f"{expected.edge_count} edges, p95 {expected.p95_ms:.2f} ms of "
            f"{expected.budget_ms:.0f} ms ({expected.margin:.2f}x)"
        )

        blended = expected.p95_ms
        compile_only = measured["compile.request_path@200 nodes / 400 edges"].p95_ms
        validation = measured["validation@200 nodes / 400 edges"].p95_ms
        excess = blended - compile_only
        # Percentiles do not add, so the bound is generous on purpose: the claim is that the
        # difference *is* the validation, not that three p95s satisfy an equation.
        assert 0.5 * validation <= excess <= 1.6 * validation, (
            f"compile-plus-validate p95 {blended:.2f} ms exceeds compile-only "
            f"{compile_only:.2f} ms by {excess:.2f} ms, which is not the validation cost "
            f"({validation:.2f} ms) - the extra time is coming from somewhere else"
        )


# ══════════════════════════════════════════════════════════════════════════
#  3. REQUIREMENT 25.2 - VALIDATION, p95 <= 120 ms SERVER-SIDE
# ══════════════════════════════════════════════════════════════════════════


class TestTheValidationBudget:
    """"WHEN a Canonical_Graph of 200 nodes is validated, THE Graph_Validator SHALL complete
    validation with a 95th-percentile server-side duration of 120 milliseconds or less.\""""

    def test_validation_meets_the_budget_at_200_nodes(self, measured):
        _assert_within(measured["validation@200 nodes"])

    @pytest.mark.parametrize("label", [label for label, _ in CASES])
    def test_validation_meets_the_budget_at_every_measurement_point(
        self, measured, label
    ):
        _assert_within(measured[f"validation@{label}"])

    def test_the_maximum_legal_graph_meets_the_budget(self, measured):
        """Eleven stages over 200 nodes and 400 edges, collect-all rather than fail-fast."""
        _assert_within(measured["validation@200 nodes / 400 edges"])

    def test_every_stage_ran_for_the_graph_that_was_timed(self, reg, graphs):
        """A budget met by a pipeline that skipped stages would not be the budget.

        All twelve declared stages are recorded and eleven of them ``PASSED``. The one
        ``SKIPPED`` is ``warmup``, and that is correct rather than a gap: nothing on the
        validate path supplies ``available_bars`` - ``_validate_payload`` leaves it unset
        deliberately, so every path skips stage 10a identically - and a stage with no
        measurement to work from must say so instead of reporting a clean pass.
        """
        report = V.validate(graphs["200 nodes / 400 edges"], reg)
        recorded: Dict[str, List[str]] = {}
        for stage in report.stages:
            recorded.setdefault(stage["name"], []).append(stage["status"])

        assert {name for _number, name in V.STAGE_NAMES} == set(recorded)
        skipped = {
            name for name, statuses in recorded.items() if V.STATUS_SKIPPED in statuses
        }
        assert skipped == {V.STAGE_WARMUP}, recorded
        assert all(
            status == V.STATUS_PASSED
            for name, statuses in recorded.items()
            if name != V.STAGE_WARMUP
            for status in statuses
        ), recorded

    def test_the_measured_margin_is_not_marginal(self, measured):
        series = measured["validation@200 nodes / 400 edges"]
        assert series.margin >= 2.0, (
            f"the validation budget still holds (p95 {series.p95_ms:.2f} ms <= "
            f"{series.budget_ms:.0f} ms) but the headroom has fallen to "
            f"{series.margin:.1f}x, from the ~3.5x measured when this was written"
        )


# ══════════════════════════════════════════════════════════════════════════
#  4. VALIDATION UNDER THE DEBOUNCE PATH
# ══════════════════════════════════════════════════════════════════════════


class TestTheDebouncePath:
    """``design.md`` -> Testing strategy -> Performance: "Validation latency under the
    debounce path".

    The debounce is the client's: 400 ms after the last edit, one request. The interval and
    the coalescing are asserted in ``graphValidation.test.js`` and
    ``strategyBuilder.validation.test.jsx`` (task 3.10), because that is where they are
    implemented. What is measured here is the request that survives the debounce - the real
    handler, the real body, at the largest graph an author can build.
    """

    @pytest.mark.parametrize("label", [label for label, _ in CASES])
    def test_the_server_side_validation_meets_the_budget(self, measured, label):
        _assert_within(measured[f"debounce.server@{label}"])

    @pytest.mark.parametrize("label", [label for label, _ in CASES])
    def test_the_whole_handler_meets_the_budget(self, measured, label):
        """Parse, validate and assemble the structured report - all of it server-side.

        The metric covers the validation; this covers the handler around it, which reads the
        canonical envelope, runs the pipeline and builds the ``errors`` / ``warnings`` /
        ``stages`` / ``execution_path`` response. Requirement 25.2's budget is stated on the
        server-side duration, and all of this is server-side.
        """
        _assert_within(measured[f"debounce.handler@{label}"])

    def test_the_handler_costs_more_than_the_validation_it_contains(self, measured):
        """Which is the check that the two rows are not the same measurement twice."""
        inner = measured["debounce.server@200 nodes"].p95_ms
        whole = measured["debounce.handler@200 nodes"].p95_ms
        assert whole > inner, (
            f"handler p95 {whole:.2f} ms is not above the validation p95 {inner:.2f} ms it "
            f"contains"
        )

    def test_the_http_round_trip_meets_the_budget_too(self, http_round_trip):
        """The same work over the mounted app, so the figure is not only a function call.

        This one carries httpx's request encoding and response decoding as well, which are the
        client's costs and not the server's, so it is the most pessimistic of the three reads
        and is asserted against the same 120 ms rather than being given an allowance.
        """
        summary, _payload = http_round_trip
        assert summary.p95_ms <= VALIDATION_BUDGET_MS, (
            f"POST /api/strategies/validate at 200 nodes: p95 {summary.p95_ms:.2f} ms "
            f"exceeds {VALIDATION_BUDGET_MS:.0f} ms (p50 {summary.p50_ms:.2f}, "
            f"max {summary.max_ms:.2f}, n={summary.count})"
        )

    def test_one_debounced_request_fits_inside_the_debounce_interval(self, measured):
        """Why 400 ms is a sane interval, measured rather than assumed.

        If a validation took longer than the debounce, a steadily typing author would queue
        requests faster than the server retired them. At 200 nodes and 400 edges the whole
        handler's p95 must sit inside the 400 ms window - and it does, by a wide margin, which
        is what makes the client's "one request per burst" a real bound on server load rather
        than a hope.
        """
        series = measured["debounce.handler@200 nodes / 400 edges"]
        assert series.p95_ms < CLIENT_DEBOUNCE_MS, (
            f"the debounced handler p95 is {series.p95_ms:.2f} ms against a "
            f"{CLIENT_DEBOUNCE_MS} ms debounce - requests would accumulate"
        )


# ══════════════════════════════════════════════════════════════════════════
#  5. NOTHING WAS WEAKENED, AND NOTHING WAS EXECUTED
# ══════════════════════════════════════════════════════════════════════════


class TestNoControlIsWeakened:
    def test_the_endpoint_still_depends_on_an_authenticated_user(self):
        """Supplying an identity is not removing the dependency (Requirement 21.1)."""
        import inspect

        from backend_app.routers.strategies import validate_strategy

        default = inspect.signature(validate_strategy).parameters["user"].default
        assert getattr(default, "dependency", None) is get_current_user

    def test_an_anonymous_caller_is_still_refused(self, graphs):
        from fastapi.testclient import TestClient

        from backend_app.main import app

        app.dependency_overrides.clear()
        response = TestClient(app).post(
            "/api/strategies/validate", json=debounce_body(graphs["10 nodes"])
        )
        assert response.status_code in (401, 403), response.status_code

    def test_the_system_freeze_is_still_in_place(self):
        """``tests/conftest.py`` sets it and this file never lifts it."""
        import os

        assert os.environ.get("VYOMQUANT_MODE") == "safe"

    def test_nothing_here_executed_a_plan_or_deployed_anything(self, measured):
        """Compilation is I/O-free (task 9.5) and this file only compiles and validates.

        Asserted through the metrics rather than in prose: a run that had reached the action
        boundary or a lifecycle transition would have left a count behind, and a run that had
        persisted anything would have needed a database client this file never provides.
        """
        collector = M.metrics_collector
        assert collector.dag_intents_blocked_non_finite.get() == 0
        assert collector.deployment_state_transitions.total() == 0

    def test_every_compile_this_file_timed_produced_a_plan(self, measured):
        """So no series is a measurement of the refusal path, which is far cheaper."""
        collector = M.metrics_collector
        assert collector.builder_compile_failures.total() == 0
        assert (
            collector.builder_compile_failures.get(
                reason=SC.COMPILE_FAILURE_INVALID_GRAPH
            )
            == 0
        )
