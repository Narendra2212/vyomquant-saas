"""
tests/test_leakage_validation_stage.py

Unit tests for validation stage 10b, structural leakage detection:
`backend_app/backend/strategy_dag/validator.validate_no_lookahead`.

Spec: strategy-builder task 5.5 (`design.md` -> Data leakage protection, mechanism 1).
Requirements 18.1, 18.2, 18.3, 18.4.

What these tests hold in place:

* **Each of the three codes rejects and accepts.** `LOOKAHEAD_SHIFT`,
  `LEAKY_FEATURE_INTO_MODEL` and `GLOBAL_STATISTIC_LEAK` each have a graph that raises them
  and a near-identical graph that does not. A rule that only ever fires is a rule nobody
  can satisfy.
* **Order-independence.** ML reachability is computed once, before any node is examined, so
  permuting the node and edge lists cannot change the verdict. Computing it inside the loop
  would make the same graph validate differently across two processes - which is the whole
  reason the task calls this out.
* **Port precision.** Ichimoku publishes five outputs and only `chikou` reads forward.
  Flagging the block would refuse `tenkan` and `kijun`, which are past-only and legitimate.
* **Reaching a model is what matters** for rule (b): the same leaky output feeding a chart
  comparison is not a training leak.
* **The stage is wired in, not merely importable.** It runs from `validate()` with no caller
  opt-in, because a leakage check some call site has to enable is one some call site ships
  without.

Nothing is faked: the registry is the real assembled one and the leakage classifications
come from `indicators_backend.INDICATOR_SPECS` and `feature_engineering.FEATURE_SPECS`.
"""

import random

import pytest

from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag import validator as V
from backend_app.backend.strategy_dag.registry import BlockRegistry
from backend_app.backend.strategy_dag.schema import (
    SEVERITY_ERROR,
    BlockCategory,
    EdgeSpec,
    NodeSpec,
    StrategyGraph,
)

#: The nine keys of the structured error contract (design.md -> Structured error contract).
CONTRACT_KEYS = {
    "code",
    "severity",
    "node_id",
    "edge_id",
    "field",
    "message",
    "expected",
    "actual",
    "fix_hint",
}

LEAKAGE_CODES = {
    V.CODE_LOOKAHEAD_SHIFT,
    V.CODE_LEAKY_FEATURE_INTO_MODEL,
    V.CODE_GLOBAL_STATISTIC_LEAK,
}


# ---------------------------------------------------------------------------
# Fixtures and builders
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reg() -> BlockRegistry:
    """The real assembled registry. Assembly is ~150 ms, so it is shared."""
    return registry_module.build_registry()


@pytest.fixture(autouse=True)
def default_hooks():
    """Guarantee the landed stage is installed regardless of what ran before.

    Another module in the suite exercises the seam and calls `clear_stage_hooks()` in its
    teardown, which also removes the leakage implementation installed at import. Restoring
    it here keeps these tests independent of collection order rather than quietly passing
    or failing depending on it.
    """
    V.install_default_stage_hooks()
    yield
    V.install_default_stage_hooks()


def node(reg: BlockRegistry, block_id: str, **params) -> NodeSpec:
    return NodeSpec.create(block_id, reg[block_id].category, params=params)


def data_node(reg: BlockRegistry) -> NodeSpec:
    return node(
        reg,
        "ohlcv_feed",
        symbol="BTC/USDT",
        timeframe="15m",
        market_type="spot",
        mode="streaming",
    )


def action_node(reg: BlockRegistry) -> NodeSpec:
    return node(
        reg, "action_buy_market", quantity_type="percent_of_equity", quantity=0.25
    )


def leakage_issues(graph: StrategyGraph, reg: BlockRegistry):
    """Stage 10b on its own, so a test is not reading another stage's verdict."""
    return V.validate_no_lookahead(graph, reg)


def leakage_codes(issues):
    return {issue["code"] for issue in issues}


def fingerprint(issues):
    """The order-insensitive identity of an issue list, for permutation comparison."""
    return sorted(
        (i["code"], i["node_id"], i["edge_id"], i["field"], i["actual"]) for i in issues
    )


def model_strategy(reg: BlockRegistry, *, source_port: str = "value"):
    """data -> ema -> feat_lag -> xgboost -> gt(vs constant) -> buy.

    A real ML pipeline, so "on a path into a model" is exercised against a graph the
    platform would actually run. Returns ``(graph, nodes_by_role)``.
    """
    data = data_node(reg)
    ema = node(reg, "ema", window=20, source="close")
    lag = node(reg, "feat_lag", lags=[1, 2, 3])
    model = node(reg, "xgboost")
    const = node(reg, "constant", value=0.5)
    gt = node(reg, "gt")
    act = action_node(reg)

    graph = StrategyGraph(
        nodes=[data, ema, lag, model, const, gt, act],
        edges=[
            EdgeSpec.create(data.id, "close", ema.id, "series"),
            EdgeSpec.create(ema.id, source_port, lag.id, "series"),
            EdgeSpec.create(lag.id, "matrix", model.id, "features"),
            EdgeSpec.create(model.id, "prediction", gt.id, "left"),
            EdgeSpec.create(const.id, "value", gt.id, "right"),
            EdgeSpec.create(gt.id, "out", act.id, "signal"),
        ],
    )
    roles = {
        "data": data,
        "ema": ema,
        "lag": lag,
        "model": model,
        "const": const,
        "gt": gt,
        "action": act,
    }
    return graph, roles


# ---------------------------------------------------------------------------
# The stage is wired into the pipeline
# ---------------------------------------------------------------------------


def test_leakage_stage_is_installed_and_runs_without_an_opt_in(reg):
    """Stage 10b is no longer pending: it runs on every path through validate()."""
    assert V.STAGE_LEAKAGE not in V.pending_stages()

    graph, _ = model_strategy(reg)
    report = V.validate(graph, reg, available_bars=100_000)

    assert V.STAGE_LEAKAGE not in report.pending_stages
    ran = [e for e in report.stages if e["name"] == V.STAGE_LEAKAGE]
    assert len(ran) == 1
    assert ran[0]["status"] == V.STATUS_PASSED
    assert ran[0]["stage"] == 10


def test_a_clean_model_pipeline_raises_no_leakage_code(reg):
    """The accepting case for the whole stage, so the rejections below mean something."""
    graph, _ = model_strategy(reg)

    assert leakage_issues(graph, reg) == []
    report = V.validate(graph, reg, available_bars=100_000)
    assert LEAKAGE_CODES.isdisjoint(set(report.codes())), report.errors


# ---------------------------------------------------------------------------
# (a) LOOKAHEAD_SHIFT - Requirement 18.2
# ---------------------------------------------------------------------------


def test_negative_shift_raises_lookahead_shift(reg):
    graph, roles = model_strategy(reg)
    shift = node(reg, "shift", bars=-3)
    graph.nodes.append(shift)
    graph.edges.append(EdgeSpec.create(roles["ema"].id, "value", shift.id, "a"))
    graph.edges.append(EdgeSpec.create(shift.id, "out", roles["lag"].id, "series"))

    issues = leakage_issues(graph, reg)
    hits = [i for i in issues if i["code"] == V.CODE_LOOKAHEAD_SHIFT]

    assert len(hits) == 1
    issue = hits[0]
    assert issue["node_id"] == shift.id
    assert issue["field"] == "bars"
    assert issue["severity"] == SEVERITY_ERROR
    assert issue["actual"] == -3
    # The message must say what is wrong and the hint must say what to do instead.
    assert "future" in issue["message"].lower()
    assert "3" in issue["fix_hint"]


def test_positive_shift_is_accepted(reg):
    """The accepting half: a backward shift is the block's whole purpose."""
    graph, roles = model_strategy(reg)
    shift = node(reg, "shift", bars=5)
    graph.nodes.append(shift)
    graph.edges.append(EdgeSpec.create(roles["ema"].id, "value", shift.id, "a"))
    graph.edges.append(EdgeSpec.create(shift.id, "out", roles["lag"].id, "series"))

    assert leakage_issues(graph, reg) == []


def test_negative_shift_is_reported_even_off_a_model_path(reg):
    """A forward shift is broken wherever it sits - its runtime raises on it.

    Requirement 18.2 asks for the model-path case; reporting the superset satisfies that
    and additionally names a graph that would fail at execution. The issue records whether
    a model is downstream so a caller can still tell the two apart.
    """
    data = data_node(reg)
    ema = node(reg, "ema", window=20, source="close")
    shift = node(reg, "shift", bars=-1)
    const = node(reg, "constant", value=1.0)
    gt = node(reg, "gt")
    act = action_node(reg)
    graph = StrategyGraph(
        nodes=[data, ema, shift, const, gt, act],
        edges=[
            EdgeSpec.create(data.id, "close", ema.id, "series"),
            EdgeSpec.create(ema.id, "value", shift.id, "a"),
            EdgeSpec.create(shift.id, "out", gt.id, "left"),
            EdgeSpec.create(const.id, "value", gt.id, "right"),
            EdgeSpec.create(gt.id, "out", act.id, "signal"),
        ],
    )

    hits = [
        i for i in leakage_issues(graph, reg) if i["code"] == V.CODE_LOOKAHEAD_SHIFT
    ]
    assert len(hits) == 1
    assert hits[0]["node_id"] == shift.id
    # No model in this graph, so the message does not claim one.
    assert "feeds a model" not in hits[0]["message"]


def test_non_numeric_shift_is_left_to_the_parameter_stage(reg):
    """An unreadable parameter is stage 3's error, and must not crash stage 10b."""
    graph, roles = model_strategy(reg)
    shift = node(reg, "shift", bars="soon")
    graph.nodes.append(shift)
    graph.edges.append(EdgeSpec.create(roles["ema"].id, "value", shift.id, "a"))

    assert leakage_issues(graph, reg) == []
    # The graph is still blocked - by the stage that owns parameter types.
    report = V.validate(graph, reg, available_bars=100_000)
    assert not report.valid


# ---------------------------------------------------------------------------
# (b) LEAKY_FEATURE_INTO_MODEL - Requirement 18.3
# ---------------------------------------------------------------------------


def _with_ichimoku(reg: BlockRegistry, port: str, *, into_model: bool):
    """Wire one Ichimoku output either into the model path or into a bare comparison."""
    graph, roles = model_strategy(reg)
    ich = node(reg, "ichimoku_cloud", tenkan=9, kijun=26, senkou_b=52)
    graph.nodes.append(ich)
    for side in ("high", "low", "close"):
        graph.edges.append(EdgeSpec.create(roles["data"].id, side, ich.id, side))

    if into_model:
        # Replace ema -> feat_lag with ichimoku[port] -> feat_lag, so the value reaches
        # xgboost through the feature matrix.
        graph.edges = [
            e
            for e in graph.edges
            if not (e.source == roles["ema"].id and e.target == roles["lag"].id)
        ]
        graph.edges.append(EdgeSpec.create(ich.id, port, roles["lag"].id, "series"))
    else:
        # Into a comparison that has no model downstream at all.
        const2 = node(reg, "constant", value=100.0)
        gt2 = node(reg, "gt")
        act2 = action_node(reg)
        graph.nodes.extend([const2, gt2, act2])
        graph.edges.extend(
            [
                EdgeSpec.create(ich.id, port, gt2.id, "left"),
                EdgeSpec.create(const2.id, "value", gt2.id, "right"),
                EdgeSpec.create(gt2.id, "out", act2.id, "signal"),
            ]
        )
    return graph, roles, ich


def test_chikou_into_a_model_raises_leaky_feature_into_model(reg):
    """Ichimoku's lagging span read at bar t carries the close of bar t+kijun."""
    graph, _, ich = _with_ichimoku(reg, "chikou", into_model=True)

    hits = [
        i
        for i in leakage_issues(graph, reg)
        if i["code"] == V.CODE_LEAKY_FEATURE_INTO_MODEL
    ]
    assert len(hits) == 1
    issue = hits[0]
    assert issue["node_id"] == ich.id
    assert issue["field"] == "chikou"
    assert issue["severity"] == SEVERITY_ERROR
    # Port-precise rules name the offending connection, not just the node.
    assert issue["edge_id"] is not None
    assert "chikou" in issue["message"]


def test_a_past_only_ichimoku_output_into_a_model_is_accepted(reg):
    """Port precision: flagging the block would refuse tenkan, which is past-only."""
    graph, _, _ = _with_ichimoku(reg, "tenkan", into_model=True)

    assert leakage_codes(leakage_issues(graph, reg)) == set()


def test_chikou_off_any_model_path_is_accepted(reg):
    """Rule (b) is about what reaches a model. On a chart comparison it is legitimate."""
    graph, _, _ = _with_ichimoku(reg, "chikou", into_model=False)

    issues = leakage_issues(graph, reg)
    assert V.CODE_LEAKY_FEATURE_INTO_MODEL not in leakage_codes(issues)


def test_leaky_output_reaching_a_model_indirectly_is_still_caught(reg):
    """Reachability, not adjacency: an extra hop between the leak and the model is still a leak."""
    graph, roles, ich = _with_ichimoku(reg, "chikou", into_model=True)
    # Insert feat_standardize between the feature matrix and the model.
    std = node(reg, "feat_standardize", fit_on="train_split")
    graph.nodes.append(std)
    graph.edges = [
        e
        for e in graph.edges
        if not (e.source == roles["lag"].id and e.target == roles["model"].id)
    ]
    graph.edges.append(EdgeSpec.create(roles["lag"].id, "matrix", std.id, "matrix"))
    graph.edges.append(EdgeSpec.create(std.id, "matrix", roles["model"].id, "features"))

    hits = [
        i
        for i in leakage_issues(graph, reg)
        if i["code"] == V.CODE_LEAKY_FEATURE_INTO_MODEL
    ]
    assert [i["node_id"] for i in hits] == [ich.id]


def test_a_review_discharged_block_may_feed_a_model(reg):
    """`feat_standardize` is REVIEW_REQUIRED for a procedural reason, not a structural one.

    Its risk is that a scaler fit over the full series leaks validation and test
    statistics. That is discharged by chronological splits (mechanism 2) plus its own
    `fit_on` parameter, whose only representable value is `train_split`. Feeding a model is
    the only thing the block exists to do, so treating it like `chikou` would delete the
    design's own recommended feature pipeline.
    """
    descriptor = reg["feat_standardize"]
    assert V._leakage_risk_name(descriptor) == "REVIEW_REQUIRED"
    assert V.REVIEW_DISCHARGED_BY_FLAG & set(descriptor.capability_flags)

    graph, roles = model_strategy(reg)
    std = node(reg, "feat_standardize", fit_on="train_split")
    graph.nodes.append(std)
    graph.edges = [
        e
        for e in graph.edges
        if not (e.source == roles["lag"].id and e.target == roles["model"].id)
    ]
    graph.edges.append(EdgeSpec.create(roles["lag"].id, "matrix", std.id, "matrix"))
    graph.edges.append(EdgeSpec.create(std.id, "matrix", roles["model"].id, "features"))

    assert leakage_issues(graph, reg) == []


def test_review_discharge_requires_the_safe_parameter_value_too(reg):
    """The declaration alone is not enough: the node's own parameter must confirm it.

    Guards the case where `fit_on` gains a wider option set later - the discharge must stop
    applying on its own rather than needing this rule to be remembered and updated.
    """
    descriptor = reg["feat_standardize"]
    safe = NodeSpec.create(
        "feat_standardize", descriptor.category, params={"fit_on": "train_split"}
    )
    unsafe = NodeSpec.create(
        "feat_standardize", descriptor.category, params={"fit_on": "full_series"}
    )

    assert V._review_is_discharged(descriptor, safe) is True
    assert V._review_is_discharged(descriptor, unsafe) is False


# ---------------------------------------------------------------------------
# (c) GLOBAL_STATISTIC_LEAK - Requirement 18.4
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "block_id, params",
    [
        ("feat_zscore", {"window": 20, "mode": "global"}),
        ("feat_normalize", {"window": 20, "method": "minmax", "mode": "global"}),
    ],
)
def test_global_mode_raises_global_statistic_leak(reg, block_id, params):
    graph, roles = model_strategy(reg)
    stat = node(reg, block_id, **params)
    graph.nodes.append(stat)
    graph.edges.append(EdgeSpec.create(roles["ema"].id, "value", stat.id, "series"))
    graph.edges.append(EdgeSpec.create(stat.id, "matrix", roles["model"].id, "features"))

    hits = [
        i
        for i in leakage_issues(graph, reg)
        if i["code"] == V.CODE_GLOBAL_STATISTIC_LEAK
    ]
    assert len(hits) == 1
    issue = hits[0]
    assert issue["node_id"] == stat.id
    assert issue["field"] == "mode"
    assert issue["severity"] == SEVERITY_ERROR
    assert issue["actual"] == V.GLOBAL_STATISTIC_MODE
    assert "rolling" in issue["fix_hint"]


@pytest.mark.parametrize(
    "block_id, params",
    [
        ("feat_zscore", {"window": 20, "mode": "rolling"}),
        ("feat_zscore", {"window": 20, "mode": "expanding"}),
        ("feat_normalize", {"window": 20, "method": "robust", "mode": "rolling"}),
    ],
)
def test_windowed_and_expanding_modes_are_accepted(reg, block_id, params):
    """The accepting half: rolling and expanding read past bars only."""
    graph, roles = model_strategy(reg)
    stat = node(reg, block_id, **params)
    graph.nodes.append(stat)
    graph.edges.append(EdgeSpec.create(roles["ema"].id, "value", stat.id, "series"))
    graph.edges.append(EdgeSpec.create(stat.id, "matrix", roles["model"].id, "features"))

    assert leakage_issues(graph, reg) == []


def test_global_mode_is_rejected_even_without_a_model(reg):
    """A whole-series statistic is a leak into any evaluation, model or not."""
    data = data_node(reg)
    ema = node(reg, "ema", window=20, source="close")
    stat = node(reg, "feat_zscore", window=20, mode="global")
    graph = StrategyGraph(
        nodes=[data, ema, stat],
        edges=[
            EdgeSpec.create(data.id, "close", ema.id, "series"),
            EdgeSpec.create(ema.id, "value", stat.id, "series"),
        ],
    )

    assert V.CODE_GLOBAL_STATISTIC_LEAK in leakage_codes(leakage_issues(graph, reg))


# ---------------------------------------------------------------------------
# Order-independence: the property the task calls out by name
# ---------------------------------------------------------------------------


def leaky_kitchen_sink(reg: BlockRegistry):
    """One graph carrying all three leaks at once, plus safe blocks alongside them."""
    graph, roles, ich = _with_ichimoku(reg, "chikou", into_model=True)

    bad_shift = node(reg, "shift", bars=-2)
    graph.nodes.append(bad_shift)
    graph.edges.append(EdgeSpec.create(roles["ema"].id, "value", bad_shift.id, "a"))
    graph.edges.append(
        EdgeSpec.create(bad_shift.id, "out", roles["lag"].id, "series")
    )

    good_shift = node(reg, "shift", bars=2)
    graph.nodes.append(good_shift)
    graph.edges.append(EdgeSpec.create(roles["ema"].id, "value", good_shift.id, "a"))

    zscore = node(reg, "feat_zscore", window=20, mode="global")
    graph.nodes.append(zscore)
    graph.edges.append(EdgeSpec.create(roles["ema"].id, "value", zscore.id, "series"))
    graph.edges.append(
        EdgeSpec.create(zscore.id, "matrix", roles["model"].id, "features")
    )

    rolling = node(reg, "feat_zscore", window=20, mode="rolling")
    graph.nodes.append(rolling)
    graph.edges.append(EdgeSpec.create(roles["ema"].id, "value", rolling.id, "series"))

    return graph, {"ichimoku": ich, "bad_shift": bad_shift, "zscore": zscore, **roles}


def test_all_three_codes_are_collected_in_one_pass(reg):
    """Collect-all: an author fixes every leak in one round, not one save per leak."""
    graph, _ = leaky_kitchen_sink(reg)

    assert leakage_codes(leakage_issues(graph, reg)) == LEAKAGE_CODES


def test_the_verdict_does_not_depend_on_node_or_edge_ordering(reg):
    """ML reachability is computed once, before the loop.

    Were it computed per node, a node examined before the model was discovered would be
    classified against an incomplete reachability set - so the same graph would validate
    differently depending on list order, and therefore differently across two processes.
    """
    graph, _ = leaky_kitchen_sink(reg)
    baseline = leakage_issues(graph, reg)
    assert leakage_codes(baseline) == LEAKAGE_CODES  # there is something to disagree about

    rng = random.Random(20260819)
    for _ in range(25):
        shuffled = StrategyGraph(
            nodes=rng.sample(graph.nodes, len(graph.nodes)),
            edges=rng.sample(graph.edges, len(graph.edges)),
            schema_version=graph.schema_version,
        )
        assert fingerprint(leakage_issues(shuffled, reg)) == fingerprint(baseline)


def test_reversed_ordering_yields_a_byte_identical_issue_list(reg):
    """Stronger than set equality: the emitted list itself is sorted, so reports diff cleanly."""
    graph, _ = leaky_kitchen_sink(reg)
    reversed_graph = StrategyGraph(
        nodes=list(reversed(graph.nodes)),
        edges=list(reversed(graph.edges)),
        schema_version=graph.schema_version,
    )

    assert leakage_issues(reversed_graph, reg) == leakage_issues(graph, reg)


def test_the_pipeline_agrees_with_the_stage_run_standalone(reg):
    """The hook passes the pipeline's own indices through; both routes must match."""
    graph, _ = leaky_kitchen_sink(reg)

    standalone = leakage_codes(leakage_issues(graph, reg))
    report = V.validate(graph, reg, available_bars=100_000)

    assert standalone <= set(report.codes())
    assert not report.valid
    assert report.dag_hash is None


# ---------------------------------------------------------------------------
# Contract and robustness
# ---------------------------------------------------------------------------


def test_every_issue_carries_the_full_structured_error_contract(reg):
    graph, _ = leaky_kitchen_sink(reg)

    issues = leakage_issues(graph, reg)
    assert issues
    for issue in issues:
        assert set(issue) == CONTRACT_KEYS, issue["code"]
        assert issue["severity"] == SEVERITY_ERROR
        assert issue["node_id"]
        assert issue["message"].strip()
        assert issue["fix_hint"].strip()


def test_an_unresolved_block_is_skipped_rather_than_guessed_at(reg):
    """Stage 2 owns unknown blocks; this stage cannot assert their leakage either way."""
    graph, roles = model_strategy(reg)
    ghost = NodeSpec.create("no_such_block", BlockCategory.FEATURE_ENGINEERING, params={})
    graph.nodes.append(ghost)
    graph.edges.append(EdgeSpec.create(ghost.id, "matrix", roles["model"].id, "features"))

    assert leakage_issues(graph, reg) == []


def test_a_model_node_is_not_its_own_leak(reg):
    """ml_reachable includes the model itself; a model must not flag on that."""
    graph, roles = model_strategy(reg)

    issues = leakage_issues(graph, reg)
    assert roles["model"].id not in {i["node_id"] for i in issues}


def test_an_empty_graph_produces_no_issues(reg):
    assert V.validate_no_lookahead(StrategyGraph(nodes=[], edges=[]), reg) == []
