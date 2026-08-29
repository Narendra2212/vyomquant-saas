# -*- coding: utf-8 -*-
"""
tests/test_ml_training_policy.py

Unit tests for `backend_app/backend/ml_training_policy.py` and for validation stage 11,
the ML readiness gate wired into `strategy_dag/validator.py`.

Spec: strategy-builder task 6.2 (`design.md` -> ML/DL model registry -> "Minimum-data
gate" and "Resource caps (backend-enforced)").
Requirements 14.2, 14.3, 14.4, 14.5, 14.6, 16.1, 16.2, 16.3, 16.7.

What these tests hold in place
------------------------------
* **The gate arithmetic at the boundary.** Both families, at *exactly* the required row
  count and at required - 1. A gate that only rejects tiny datasets and only accepts huge
  ones tells you nothing about where its edge is, and the edge is the whole contract:
  Requirement 14.4 is a comparison, and an off-by-one in it either blocks a dataset that
  would have trained or admits one that will crash on an empty split.
* **`required` and `available` for both dimensions, always.** Requirement 14.9 renders
  both quantities. A verdict that reports only the dimension that failed forces the UI
  back to "insufficient data", which is the message this whole requirement exists to
  delete.
* **The cap intersection direction.** `min(tier_cap, model.max_safe_epochs)`, checked
  across every plan x model pair, including the pairs where the tier binds and the pairs
  where the model binds. `max()` here would let a subscription upgrade buy a training run
  the model registry declares unsafe, and a test that only looks at one pair cannot see
  which way round the code got it.
* **Each cap independently.** Every cap is exercised with the other dimensions widened, so
  a passing test names the cap that actually fired rather than whichever one happens to
  trip first.
* **Measured, never estimated.** `DatasetStats` is built from a real `SupervisedDataset`
  and the usable row count is compared against the hand-computed
  `rows - warmup - horizon`. A mapping that carries only a raw total is *refused*, because
  doing that subtraction inside the gate is the estimate Requirement 14.2 forbids.
* **Stage 11 distinguishes three states.** `NOT_IMPLEMENTED` (nothing installed),
  `SKIPPED` (installed, no dataset statistics supplied) and `PASSED`. A graph validated
  without dataset statistics must not read as ML-ready.
* **Caps are additive.** `require_ml_training` and `check_ml_quota` still exist and still
  express what they always did; the resolved caps *record* the entitlement flag and the
  monthly allowance instead of restating or replacing either.

Nothing is mocked. The registry is the real assembled one, the model figures come from
`ml_models.MODEL_SPECS`, and the dataset is built by `ml_dataset.build_supervised_dataset`
over real numpy arrays.
"""

import dataclasses
import math
import random

import numpy as np
import pytest

from backend_app.backend import ml_training_policy as P
from backend_app.backend.ml_dataset import build_supervised_dataset
from backend_app.backend.ml_models import MODEL_SPECS, PLATFORM_MIN_FEATURE_COLUMNS
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag import validator as V
from backend_app.backend.strategy_dag.feature_matrix import build_feature_matrix
from backend_app.backend.strategy_dag.registry import BlockRegistry
from backend_app.backend.strategy_dag.schema import (
    BlockCategory,
    EdgeSpec,
    NodeSpec,
    StrategyGraph,
)
from backend_app.core.tenant import TenantPlan

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

#: One representative of each family the gate treats differently.
TREE_BLOCK = "xgboost"
SEQUENCE_BLOCK = "lstm"


# ---------------------------------------------------------------------------
# Fixtures and builders
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reg() -> BlockRegistry:
    """The real assembled registry. Assembly is ~150 ms, so it is shared."""
    return registry_module.build_registry()


@pytest.fixture(autouse=True)
def default_hooks():
    """Both landed stages installed, whatever ran before.

    Other modules in the suite exercise the seam and call `clear_stage_hooks()`, which
    removes stage 10b *and* stage 11. Restoring them here keeps these tests independent of
    collection order rather than quietly passing or failing depending on it.
    """
    V.install_default_stage_hooks()
    yield
    V.install_default_stage_hooks()


def spec_view(block_id: str) -> P.ModelSpecView:
    return P.ModelSpecView.from_spec(MODEL_SPECS[block_id])


def config_for(block_id: str, *, label_horizon: int = 1) -> P.ValidationConfig:
    """The model's own split geometry, with no feature lookback raising the embargo.

    Passing no `feature_lookback` keeps the arithmetic in these tests a function of the
    model spec alone, so a boundary assertion states the gate's edge rather than a
    particular graph's warmup.
    """
    return P.ValidationConfig.for_model(spec_view(block_id), label_horizon=label_horizon)


def stats(rows: int, columns: int, *, horizon: int = 1) -> P.DatasetStats:
    return P.DatasetStats(
        usable_rows=rows, usable_feature_columns=columns, label_horizon=horizon
    )


def gate(block_id: str, rows: int, columns: int, **kwargs) -> P.GateVerdict:
    return P.check_ml_data_requirements(
        None,
        stats(rows, columns),
        MODEL_SPECS[block_id],
        config_for(block_id),
        node_id="model-1",
        **kwargs,
    )


def node(reg: BlockRegistry, block_id: str, **params) -> NodeSpec:
    return NodeSpec.create(block_id, reg[block_id].category, params=params)


def model_strategy(reg: BlockRegistry, block_id: str = TREE_BLOCK):
    """data -> ema -> feat_lag -> model -> gt(vs constant) -> buy.

    A pipeline the platform would actually run, so stage 11 is exercised against a real
    graph rather than a hand-made ML node with no upstream.
    """
    data = node(
        reg,
        "ohlcv_feed",
        symbol="BTC/USDT",
        timeframe="15m",
        market_type="spot",
        mode="streaming",
    )
    ema = node(reg, "ema", window=20, source="close")
    lag = node(reg, "feat_lag", lags=[1, 2, 3])
    model = node(reg, block_id)
    const = node(reg, "constant", value=0.5)
    gt = node(reg, "gt")
    act = node(
        reg, "action_buy_market", quantity_type="percent_of_equity", quantity=0.25
    )
    graph = StrategyGraph(
        nodes=[data, ema, lag, model, const, gt, act],
        edges=[
            EdgeSpec.create(data.id, "close", ema.id, "series"),
            EdgeSpec.create(ema.id, "value", lag.id, "series"),
            EdgeSpec.create(lag.id, "matrix", model.id, "features"),
            EdgeSpec.create(model.id, "prediction", gt.id, "left"),
            EdgeSpec.create(const.id, "value", gt.id, "right"),
            EdgeSpec.create(gt.id, "out", act.id, "signal"),
        ],
    )
    return graph, {"model": model, "data": data, "action": act}


def linear_strategy(reg: BlockRegistry):
    """data -> ema -> gt(vs constant) -> buy. No ML node at all."""
    data = node(
        reg,
        "ohlcv_feed",
        symbol="BTC/USDT",
        timeframe="15m",
        market_type="spot",
        mode="streaming",
    )
    ema = node(reg, "ema", window=20, source="close")
    const = node(reg, "constant", value=0.5)
    gt = node(reg, "gt")
    act = node(
        reg, "action_buy_market", quantity_type="percent_of_equity", quantity=0.25
    )
    return StrategyGraph(
        nodes=[data, ema, const, gt, act],
        edges=[
            EdgeSpec.create(data.id, "close", ema.id, "series"),
            EdgeSpec.create(ema.id, "value", gt.id, "left"),
            EdgeSpec.create(const.id, "value", gt.id, "right"),
            EdgeSpec.create(gt.id, "out", act.id, "signal"),
        ],
    )


def stage_entry(report, number: int = 11):
    entries = [e for e in report.stages if e["stage"] == number]
    assert len(entries) == 1, f"expected one stage-{number} entry, got {entries}"
    return entries[0]


# ---------------------------------------------------------------------------
# 1. Measured, never estimated (Requirement 14.2)
# ---------------------------------------------------------------------------


def build_dataset(n_rows: int, n_columns: int, warmup: int, horizon: int):
    """A real `SupervisedDataset` over real arrays, so the counts below are measured."""
    rng = np.random.default_rng(20250612)
    index = np.arange(n_rows, dtype="int64")
    names = [f"f{i}" for i in range(n_columns)]
    series = [rng.normal(size=n_rows) for _ in names]
    for column in series:
        column[:warmup] = np.nan
    matrix = build_feature_matrix(
        index, names, series, column_warmup={name: warmup for name in names}
    )
    prices = np.linspace(100.0, 200.0, n_rows)
    return build_supervised_dataset(matrix, prices, horizon)


def test_usable_rows_are_measured_from_the_built_dataset_not_a_formula():
    """The dataset already dropped warmup and the trailing horizon; the stats read it.

    Validates: Requirements 14.2
    """
    dataset = build_dataset(n_rows=500, n_columns=6, warmup=40, horizon=3)
    measured = P.DatasetStats.from_dataset(dataset)

    # The independent arithmetic, stated here only to prove the measurement agrees with
    # it on a clean synthetic case - not used by the gate.
    assert measured.usable_rows == 500 - 40 - 3
    assert measured.usable_rows == dataset.n_rows
    assert measured.usable_feature_columns == 6 == dataset.n_columns
    assert measured.label_horizon == 3
    assert measured.dropped_warmup_rows == 40
    assert measured.dropped_trailing_rows == 3
    assert measured.total_rows == 500
    assert measured.feature_names == tuple(f"f{i}" for i in range(6))
    assert measured.source == "supervised_dataset"


def test_dataset_stats_reads_the_datasets_own_stats_mapping_unchanged():
    """`SupervisedDataset.stats()` can be handed over as-is; no key translation needed.

    Validates: Requirements 14.2
    """
    dataset = build_dataset(n_rows=400, n_columns=5, warmup=20, horizon=2)
    from_mapping = P.DatasetStats.from_mapping(dataset.stats())

    assert from_mapping.usable_rows == dataset.n_rows
    assert from_mapping.usable_feature_columns == dataset.n_columns
    assert from_mapping.label_horizon == dataset.horizon


def test_a_total_bar_count_and_a_warmup_figure_are_refused():
    """The estimate path is closed, not silently taken.

    A gate that computes `total - warmup - horizon` itself is wrong whenever the exchange
    served a gap or a feature column came out all-NaN and was dropped - and those are
    exactly the cases the author needs told about.

    Validates: Requirements 14.2
    """
    with pytest.raises(P.DatasetStatsError) as excinfo:
        P.DatasetStats.from_mapping(
            {"total_rows": 10_000, "warmup_bars": 200, "horizon": 1}
        )

    assert "measured" in str(excinfo.value).lower()


def test_a_dataset_with_no_label_horizon_is_refused():
    """A horizon of zero labels a row with its own bar, which is not a prediction."""
    with pytest.raises(P.DatasetStatsError):
        P.DatasetStats(usable_rows=10_000, usable_feature_columns=8, label_horizon=0)


# ---------------------------------------------------------------------------
# 2. The gate arithmetic at the boundary - TREE family
# ---------------------------------------------------------------------------


def test_tree_family_admits_exactly_the_required_row_count():
    """At exactly `required_rows` the gate passes. The edge is inclusive.

    Validates: Requirements 14.4
    """
    spec = spec_view(TREE_BLOCK)
    config = config_for(TREE_BLOCK)
    required = P.required_row_count(spec, config)

    verdict = gate(TREE_BLOCK, required, spec.min_feature_columns)

    assert verdict.ok, verdict.issues
    assert verdict.required["rows"] == required
    assert verdict.available["rows"] == required


def test_tree_family_blocks_one_row_below_the_required_count():
    """required - 1 blocks, and the verdict states both figures.

    Validates: Requirements 14.4
    """
    spec = spec_view(TREE_BLOCK)
    config = config_for(TREE_BLOCK)
    required = P.required_row_count(spec, config)

    verdict = gate(TREE_BLOCK, required - 1, spec.min_feature_columns)

    assert not verdict.ok
    assert verdict.codes == (P.CODE_INSUFFICIENT_ROWS,)
    assert verdict.required["rows"] == required
    assert verdict.available["rows"] == required - 1
    issue = verdict.issues[0]
    assert issue["expected"] == required
    assert issue["actual"] == required - 1


def test_the_required_row_count_reserves_the_fractions_and_both_embargoes():
    """The formula is the design's, and it is a function of the spec, not of this test.

    Every figure below is read from the model spec; the only thing asserted is the shape
    of the expression that combines them.

    Validates: Requirements 14.4
    """
    spec = spec_view(TREE_BLOCK)
    config = config_for(TREE_BLOCK)

    expected = math.ceil(
        (spec.min_training_rows + 2 * config.embargo_bars)
        / (1.0 - spec.val_fraction - spec.test_fraction)
    )

    assert P.required_row_count(spec, config) == expected
    # And it is strictly more than the bare minimum: the reserved splits and the two
    # embargo gaps are rows that exist and train nothing.
    assert expected > spec.min_training_rows


# ---------------------------------------------------------------------------
# 3. The gate arithmetic at the boundary - SEQUENCE family
# ---------------------------------------------------------------------------


def test_sequence_family_admits_exactly_the_required_row_count():
    """At exactly `required_rows` a sequence model passes both row checks.

    Passing the row check while failing the window check would mean the gate admits a run
    whose train split cannot hold one complete sequence, so both are asserted here.

    Validates: Requirements 14.4, 14.5
    """
    spec = spec_view(SEQUENCE_BLOCK)
    config = config_for(SEQUENCE_BLOCK)
    required = P.required_row_count(spec, config)

    verdict = gate(SEQUENCE_BLOCK, required, spec.min_feature_columns)

    assert verdict.ok, verdict.issues
    assert verdict.available["train_rows"] >= P.required_train_rows(spec)


def test_sequence_family_blocks_one_row_below_the_required_count():
    """Validates: Requirements 14.4, 14.5"""
    spec = spec_view(SEQUENCE_BLOCK)
    config = config_for(SEQUENCE_BLOCK)
    required = P.required_row_count(spec, config)

    verdict = gate(SEQUENCE_BLOCK, required - 1, spec.min_feature_columns)

    assert not verdict.ok
    assert P.CODE_INSUFFICIENT_ROWS in verdict.codes
    assert verdict.available["rows"] == required - 1


def test_a_sequence_model_needs_the_window_on_top_of_the_minimum_rows():
    """The sequence requirement is strictly larger than the tree one for the same minimum.

    Validates: Requirements 14.5
    """
    spec = spec_view(SEQUENCE_BLOCK)

    assert P.required_train_rows(spec) == spec.min_training_rows + spec.sequence_length
    # Every training sample consumes a whole window, so the required total exceeds what a
    # non-sequence model with the same `min_training_rows` would need.
    as_tree = dataclasses.replace(spec, model_family=P.FAMILY_TREE, sequence_length=None)
    config = config_for(SEQUENCE_BLOCK)
    assert P.required_row_count(spec, config) > P.required_row_count(as_tree, config)


def test_a_short_sequence_train_split_is_reported_with_the_window_and_the_available_rows():
    """Requirement 14.6 asks for the sequence length *and* the available training rows.

    Validates: Requirements 14.6
    """
    spec = spec_view(SEQUENCE_BLOCK)

    verdict = gate(SEQUENCE_BLOCK, 200, spec.min_feature_columns)

    assert not verdict.ok
    assert P.CODE_INSUFFICIENT_SEQUENCE_ROWS in verdict.codes
    issue = next(
        i for i in verdict.issues if i["code"] == P.CODE_INSUFFICIENT_SEQUENCE_ROWS
    )
    assert str(spec.sequence_length) in issue["message"]
    assert verdict.required["sequence_length"] == spec.sequence_length
    assert verdict.available["sequence_length"] == spec.sequence_length
    assert verdict.required["train_rows"] == P.required_train_rows(spec)
    assert verdict.available["train_rows"] == int(200 * (1.0 - spec.reserved_fraction))


def test_the_tree_family_carries_no_sequence_figures():
    """A tree model has no window, and the verdict does not invent one."""
    spec = spec_view(TREE_BLOCK)
    verdict = gate(TREE_BLOCK, 10, spec.min_feature_columns)

    assert "sequence_length" not in verdict.required
    assert "train_rows" not in verdict.available
    assert P.CODE_INSUFFICIENT_SEQUENCE_ROWS not in verdict.codes


# ---------------------------------------------------------------------------
# 4. Feature columns: the platform floor and the model floor
# ---------------------------------------------------------------------------


def test_the_column_floor_is_the_greater_of_the_platform_and_model_minimums():
    """Validates: Requirements 14.3"""
    spec = spec_view(TREE_BLOCK)
    expected = max(PLATFORM_MIN_FEATURE_COLUMNS, spec.min_feature_columns)

    at_floor = gate(TREE_BLOCK, 1_000_000, expected)
    below = gate(TREE_BLOCK, 1_000_000, expected - 1)

    assert at_floor.ok, at_floor.issues
    assert not below.ok
    assert below.codes == (P.CODE_INSUFFICIENT_FEATURE_COLUMNS,)
    assert below.required["columns"] == expected
    assert below.available["columns"] == expected - 1
    assert P.platform_min_feature_columns() == PLATFORM_MIN_FEATURE_COLUMNS


def test_both_dimensions_are_reported_even_when_only_one_fell_short():
    """A UI that has to ask a second time for the other half renders "insufficient data".

    Validates: Requirements 14.3, 14.4
    """
    spec = spec_view(TREE_BLOCK)
    required_rows = P.required_row_count(spec, config_for(TREE_BLOCK))

    only_columns_short = gate(TREE_BLOCK, required_rows, 2)
    only_rows_short = gate(TREE_BLOCK, 10, spec.min_feature_columns)

    for verdict in (only_columns_short, only_rows_short):
        assert set(verdict.required) >= {"columns", "rows"}
        assert set(verdict.available) >= {"columns", "rows"}
        assert all(value is not None for value in verdict.required.values())
        assert all(value is not None for value in verdict.available.values())


def test_the_gate_collects_every_problem_in_one_pass():
    """Collect-all, not fail-fast: the author fixes both shortfalls in one round.

    Validates: Requirements 14.3, 14.4
    """
    verdict = gate(TREE_BLOCK, 10, 2)

    assert set(verdict.codes) == {
        P.CODE_INSUFFICIENT_FEATURE_COLUMNS,
        P.CODE_INSUFFICIENT_ROWS,
    }


def test_the_blocking_message_states_required_and_available_for_both_dimensions():
    """The mandated message shape falls out of required / available.

    Validates: Requirements 14.3, 14.4
    """
    verdict = gate(TREE_BLOCK, 1_240, 3)
    message = verdict.message()

    assert message.startswith("Training cannot start. Required:")
    assert "3 feature columns" in message
    assert "1,240 rows" in message
    assert f"{verdict.required['rows']:,} usable rows" in message
    # A passing verdict has nothing to say.
    assert gate(TREE_BLOCK, 1_000_000, 50).message() == ""


def test_every_gate_issue_is_a_full_structured_error_contract_entry():
    """So a verdict folds into a ValidationReport untranslated."""
    verdict = gate(SEQUENCE_BLOCK, 200, 2)

    assert verdict.issues
    for issue in verdict.issues:
        assert set(issue) == CONTRACT_KEYS
        assert issue["severity"] == "error"
        assert issue["node_id"] == "model-1"
        assert issue["message"].strip()
        assert issue["fix_hint"].strip()


def test_an_untrainable_or_unavailable_model_is_reported():
    """Requirement 14.x step 4: the model must be runnable, not merely declared."""
    spec = spec_view(TREE_BLOCK)
    rows = P.required_row_count(spec, config_for(TREE_BLOCK))

    not_trainable = P.check_ml_data_requirements(
        None,
        stats(rows, spec.min_feature_columns),
        dataclasses.replace(spec, can_train=False),
        config_for(TREE_BLOCK),
        node_id="model-1",
    )
    no_backend = P.check_ml_data_requirements(
        None,
        stats(rows, spec.min_feature_columns),
        dataclasses.replace(spec, backend_available=False),
        config_for(TREE_BLOCK),
        node_id="model-1",
    )

    assert not_trainable.codes == (P.CODE_MODEL_NOT_TRAINABLE,)
    assert no_backend.codes == (P.CODE_MODEL_NOT_TRAINABLE,)


def test_an_unresolvable_model_blocks_rather_than_passing_silently():
    """"We could not check" must not read as "it is fine"."""
    verdict = P.check_ml_data_requirements(
        None, stats(1_000_000, 50), "no_such_model", node_id="model-1"
    )

    assert not verdict.ok
    assert verdict.codes == (P.CODE_MODEL_SPEC_UNAVAILABLE,)
    # The available side is still measured, so the UI can show what it does have.
    assert verdict.available["rows"] == 1_000_000
    assert verdict.available["columns"] == 50


def test_a_reserved_fraction_of_one_or_more_is_refused():
    """The design's own precondition. Reserved >= 1 leaves no train split at all."""
    with pytest.raises(P.MLTrainingPolicyError):
        P.ValidationConfig(
            val_fraction=0.6, test_fraction=0.4, embargo_bars=0, label_horizon=1
        )


def test_the_embargo_floor_is_raised_by_the_feature_lookback_never_lowered():
    """A larger embargo means more rows required, which is the strict direction."""
    spec = spec_view(TREE_BLOCK)
    bare = P.ValidationConfig.for_model(spec, label_horizon=1)
    with_lookback = P.ValidationConfig.for_model(
        spec, label_horizon=1, feature_lookback=500
    )

    assert with_lookback.embargo_bars >= bare.embargo_bars
    assert with_lookback.embargo_bars == max(spec.embargo_bars, 500 + 1)
    # An explicit embargo can raise it further but cannot go under either floor.
    assert (
        P.ValidationConfig.for_model(
            spec, label_horizon=1, feature_lookback=500, embargo_bars=1
        ).embargo_bars
        == with_lookback.embargo_bars
    )
    assert P.required_row_count(spec, with_lookback) > P.required_row_count(spec, bare)


# ---------------------------------------------------------------------------
# 5. Cap resolution: the intersection direction (Requirements 16.1, 16.2)
# ---------------------------------------------------------------------------

PAID_PLANS = (TenantPlan.PROFESSIONAL, TenantPlan.ENTERPRISE)


@pytest.mark.parametrize("plan", list(TenantPlan))
@pytest.mark.parametrize("block_id", sorted(MODEL_SPECS))
def test_the_epoch_cap_is_the_lesser_of_the_tier_and_the_model_ceiling(plan, block_id):
    """`min(tier_cap, model.max_safe_epochs)`, for every plan x model pair.

    Checked across the whole matrix because a single pair cannot distinguish `min` from
    `max`: whichever way round the code got it, some pairs agree by coincidence.

    Validates: Requirements 16.2
    """
    caps = P.resolve_caps(plan=plan, model_spec=block_id)
    tier = P.ML_TIER_CAPS[plan].max_epochs
    model = MODEL_SPECS[block_id].max_safe_epochs

    assert caps.max_epochs == min(tier, model)
    assert caps.tier_max_epochs == tier
    assert caps.model_max_safe_epochs == model
    # Never the other direction, unless the two happen to be equal.
    if tier != model:
        assert caps.max_epochs != max(tier, model)


def test_a_paid_tier_raises_the_epoch_cap_but_never_past_what_the_spec_calls_safe():
    """Both sides of the intersection, on real pairs where each one binds.

    Validates: Requirements 16.2
    """
    # The tier binds: PROFESSIONAL allows fewer epochs than xgboost declares safe.
    pro_tree = P.resolve_caps(plan=TenantPlan.PROFESSIONAL, model_spec=TREE_BLOCK)
    assert pro_tree.tier_max_epochs < pro_tree.model_max_safe_epochs
    assert pro_tree.max_epochs == pro_tree.tier_max_epochs

    # Upgrading raises it - within the model's ceiling.
    ent_tree = P.resolve_caps(plan=TenantPlan.ENTERPRISE, model_spec=TREE_BLOCK)
    assert ent_tree.max_epochs > pro_tree.max_epochs
    assert ent_tree.max_epochs <= MODEL_SPECS[TREE_BLOCK].max_safe_epochs

    # The model binds: ENTERPRISE's allowance far exceeds what an LSTM declares safe, and
    # the upgrade buys nothing beyond it.
    ent_seq = P.resolve_caps(plan=TenantPlan.ENTERPRISE, model_spec=SEQUENCE_BLOCK)
    assert ent_seq.tier_max_epochs > ent_seq.model_max_safe_epochs
    assert ent_seq.max_epochs == MODEL_SPECS[SEQUENCE_BLOCK].max_safe_epochs


def test_every_resolved_cap_dimension_is_present_and_non_negative():
    """Requirement 16.1 enumerates eight dimensions; all eight are resolved.

    Validates: Requirements 16.1
    """
    caps = P.resolve_caps({"plan": "pro"}, TREE_BLOCK)

    for name in (
        "max_epochs",
        "max_rows",
        "max_feature_columns",
        "max_concurrent_jobs_per_user",
        "max_concurrent_jobs_global",
        "max_duration_seconds",
        "max_memory_mb",
        "max_model_size_mb",
    ):
        value = getattr(caps, name)
        assert isinstance(value, int) and value >= 0, name
    assert caps.block_id == TREE_BLOCK
    assert caps.plan == TenantPlan.PROFESSIONAL.value
    assert caps.epoch_unit == MODEL_SPECS[TREE_BLOCK].epoch_unit.value


def test_the_feature_column_cap_never_exceeds_the_graph_capacity_limit():
    """The platform ceiling is read from the validator's own limits, not restated."""
    caps = P.resolve_caps(plan=TenantPlan.ENTERPRISE, model_spec=TREE_BLOCK)

    assert caps.max_feature_columns <= V.LIMITS.max_feature_columns


def test_runtime_ceilings_come_from_ml_safety():
    """Duration, memory and artifact size are bounded by the isolation layer's config."""
    from backend_app.core.ml_safety import MemoryMonitor, TrainingIsolator

    caps = P.resolve_caps(plan=TenantPlan.ENTERPRISE, model_spec=TREE_BLOCK)

    assert caps.max_duration_seconds <= TrainingIsolator._config.max_training_time_seconds
    assert caps.max_memory_mb <= TrainingIsolator._config.max_memory_mb
    assert caps.max_model_size_mb <= MemoryMonitor._config.max_model_cache_size_mb


@pytest.mark.parametrize("plan", [TenantPlan.FREE, TenantPlan.BASIC])
def test_an_unentitled_plan_gets_caps_that_agree_with_the_entitlement_layer(plan):
    """The caps do not permit what `require_ml_training` would refuse.

    Validates: Requirements 16.7
    """
    caps = P.resolve_caps(plan=plan, model_spec=TREE_BLOCK)

    assert caps.ml_training_entitled is False
    assert caps.monthly_training_quota == 0
    assert caps.max_epochs == 0
    assert caps.max_concurrent_jobs_per_user == 0


@pytest.mark.parametrize("plan", PAID_PLANS)
def test_an_entitled_plan_records_the_flag_and_the_monthly_allowance(plan):
    """Both existing controls are consulted and reported, not replaced.

    Validates: Requirements 16.7
    """
    caps = P.resolve_caps(plan=plan, model_spec=TREE_BLOCK)

    assert caps.ml_training_entitled is True
    assert caps.monthly_training_quota is not None
    assert caps.monthly_training_quota != 0
    assert caps.max_epochs > 0
    assert caps.max_concurrent_jobs_per_user >= 1


def test_the_existing_entitlement_dependencies_are_still_in_force():
    """`require_ml_training` and `check_ml_quota` are untouched; caps are additive.

    This module adds per-request bounds those two do not express. It does not re-implement
    them, and nothing here relaxes them.

    Validates: Requirements 16.7
    """
    import inspect

    from backend_app.core import subscription_dependencies as deps

    for name in ("require_ml_training", "check_ml_quota"):
        fn = getattr(deps, name)
        assert inspect.iscoroutinefunction(fn), name

    source = inspect.getsource(P)
    assert "def require_ml_training" not in source
    assert "def check_ml_quota" not in source


def test_an_unknown_plan_falls_back_to_the_most_restrictive_tier_with_a_warning():
    """Fail closed: an unreadable plan must not resolve to a generous allowance."""
    caps = P.resolve_caps({"plan": "some_plan_that_does_not_exist"}, TREE_BLOCK)

    assert caps.plan == TenantPlan.FREE.value
    assert caps.max_epochs == 0


def test_a_billing_plan_alias_resolves_through_the_existing_plan_mapper():
    """No alias table is restated here; the platform's normaliser is used."""
    for alias in ("pro", "pro_999", "professional", "PROFESSIONAL", "ml_addon"):
        assert (
            P.resolve_caps({"plan": alias}, TREE_BLOCK).plan
            == TenantPlan.PROFESSIONAL.value
        )
    assert (
        P.resolve_caps({"plan": "elite_1999"}, TREE_BLOCK).plan
        == TenantPlan.ENTERPRISE.value
    )


# ---------------------------------------------------------------------------
# 6. Cap enforcement: each cap independently (Requirement 16.3)
# ---------------------------------------------------------------------------


def generous_caps(block_id: str = TREE_BLOCK, **overrides) -> P.MLTrainingCaps:
    """Enterprise caps with every dimension widened, so one cap can be tested alone.

    Without this, a test aiming at `MAX_ROWS` would trip `MAX_MEMORY` first and pass for
    the wrong reason.
    """
    caps = P.resolve_caps(plan=TenantPlan.ENTERPRISE, model_spec=block_id)
    widened = {
        "max_rows": 10**9,
        "max_feature_columns": 10**6,
        "max_memory_mb": 10**9,
        "max_concurrent_jobs_per_user": 10**6,
        "max_concurrent_jobs_global": 10**6,
    }
    widened.update(overrides)
    return dataclasses.replace(caps, **widened)


def small_request(block_id: str = TREE_BLOCK, **overrides) -> P.TrainingRequest:
    fields = {
        "block_id": block_id,
        "epochs": 1,
        "rows": 10_000,
        "feature_columns": 10,
        "model_family": MODEL_SPECS[block_id].model_family.value,
        "sequence_length": MODEL_SPECS[block_id].sequence_length,
    }
    fields.update(overrides)
    return P.TrainingRequest(**fields)


def available_counts(**overrides) -> P.JobCounts:
    fields = {"user_active": 0, "global_running": 0, "global_queued": 0}
    fields.update(overrides)
    return P.JobCounts(available=True, **fields)


def test_a_request_inside_every_cap_is_admitted():
    """The accepting case, so the rejections below mean something."""
    caps = generous_caps()
    admission = P.enforce_caps(
        small_request(), caps, job_counts=available_counts()
    )

    assert admission.admitted
    assert admission.decision is P.AdmissionDecision.ADMIT
    assert admission.estimated_memory_mb > 0
    assert admission.caps is caps


def test_max_epochs_is_enforced_alone():
    """Validates: Requirements 16.3"""
    caps = generous_caps()

    with pytest.raises(P.CapExceeded) as excinfo:
        P.enforce_caps(
            small_request(epochs=caps.max_epochs + 1),
            caps,
            job_counts=available_counts(),
        )

    error = excinfo.value
    assert error.cap == P.CAP_MAX_EPOCHS
    assert error.requested == caps.max_epochs + 1
    assert error.allowed == caps.max_epochs
    assert error.http_status == 422
    payload = error.to_dict()
    assert payload["requested"] == caps.max_epochs + 1
    assert payload["allowed"] == caps.max_epochs
    assert payload["unit"] == caps.epoch_unit
    # And exactly at the cap it is admitted, so the boundary is inclusive.
    assert P.enforce_caps(
        small_request(epochs=caps.max_epochs), caps, job_counts=available_counts()
    ).admitted


def test_max_rows_is_enforced_alone():
    """Validates: Requirements 16.3"""
    caps = generous_caps(max_rows=50_000)

    with pytest.raises(P.CapExceeded) as excinfo:
        P.enforce_caps(
            small_request(rows=50_001), caps, job_counts=available_counts()
        )

    assert excinfo.value.cap == P.CAP_MAX_ROWS
    assert excinfo.value.requested == 50_001
    assert excinfo.value.allowed == 50_000
    assert P.enforce_caps(
        small_request(rows=50_000), caps, job_counts=available_counts()
    ).admitted


def test_max_feature_columns_is_enforced_alone():
    """Validates: Requirements 16.3"""
    caps = generous_caps(max_feature_columns=64)

    with pytest.raises(P.CapExceeded) as excinfo:
        P.enforce_caps(
            small_request(feature_columns=65), caps, job_counts=available_counts()
        )

    assert excinfo.value.cap == P.CAP_MAX_FEATURES
    assert excinfo.value.requested == 65
    assert excinfo.value.allowed == 64
    assert P.enforce_caps(
        small_request(feature_columns=64), caps, job_counts=available_counts()
    ).admitted


def test_max_concurrent_jobs_per_user_is_enforced_alone():
    """Validates: Requirements 16.3"""
    caps = generous_caps(max_concurrent_jobs_per_user=2)

    with pytest.raises(P.CapExceeded) as excinfo:
        P.enforce_caps(
            small_request(), caps, job_counts=available_counts(user_active=2)
        )

    assert excinfo.value.cap == P.CAP_MAX_CONCURRENT_USER
    assert excinfo.value.requested == 3
    assert excinfo.value.allowed == 2
    assert P.enforce_caps(
        small_request(), caps, job_counts=available_counts(user_active=1)
    ).admitted


def test_max_memory_is_enforced_alone():
    """Validates: Requirements 16.3"""
    caps = generous_caps(max_memory_mb=256)
    request = small_request(rows=2_000_000, feature_columns=500)

    with pytest.raises(P.CapExceeded) as excinfo:
        P.enforce_caps(request, caps, job_counts=available_counts())

    assert excinfo.value.cap == P.CAP_MAX_MEMORY
    assert excinfo.value.allowed == 256
    assert excinfo.value.requested == P.estimate_memory_mb(request)


def test_a_sequence_models_windowed_matrix_is_counted_in_the_memory_estimate():
    """Treating a 60-bar window like a single row under-counts by two orders of magnitude."""
    tree = small_request(TREE_BLOCK, rows=50_000, feature_columns=20)
    sequence = small_request(SEQUENCE_BLOCK, rows=50_000, feature_columns=20)

    assert sequence.is_sequence
    assert P.estimate_memory_mb(sequence) > P.estimate_memory_mb(tree)


def test_global_saturation_defers_rather_than_rejecting():
    """Requirement 16.5: hold the job in the queue and report a position estimate."""
    caps = generous_caps(max_concurrent_jobs_global=4)

    admission = P.enforce_caps(
        small_request(),
        caps,
        job_counts=available_counts(global_running=4, global_queued=6),
    )

    assert admission.deferred
    assert admission.queue_position == 7
    assert admission.caps is caps


def test_missing_job_counts_degrade_with_a_warning_naming_the_migration():
    """`training_jobs` does not exist here; that is a warning, not a 500."""
    caps = generous_caps()

    admission = P.enforce_caps(small_request(), caps)

    assert admission.admitted
    assert any(P.TRAINING_TABLES_MIGRATION in w for w in admission.warnings)
    # The per-request caps were still applied: an over-cap request is still refused.
    with pytest.raises(P.CapExceeded):
        P.enforce_caps(small_request(epochs=caps.max_epochs + 1), caps)


def test_caps_resolved_without_a_model_cannot_admit_a_job():
    """Without a model ceiling there is no "lesser of" to enforce (Requirement 16.2)."""
    caps = P.resolve_caps(plan=TenantPlan.ENTERPRISE)

    assert caps.has_model_ceiling is False
    with pytest.raises(P.MLTrainingPolicyError) as excinfo:
        P.enforce_caps(small_request(), caps, job_counts=available_counts())

    assert "max_safe_epochs" in str(excinfo.value)


def test_caps_for_one_model_cannot_admit_a_request_for_another():
    """Admitting against another model's ceiling would defeat the intersection."""
    caps = generous_caps(SEQUENCE_BLOCK)

    with pytest.raises(P.MLTrainingPolicyError):
        P.enforce_caps(
            small_request(TREE_BLOCK), caps, job_counts=available_counts()
        )


def test_a_request_built_from_a_spec_and_measured_stats_defaults_to_the_recommendation():
    """The client's epoch count is optional; the spec's recommendation is the default."""
    request = P.TrainingRequest.build(TREE_BLOCK, stats(12_345, 9))

    assert request.epochs == MODEL_SPECS[TREE_BLOCK].recommended_epochs
    assert request.rows == 12_345
    assert request.feature_columns == 9
    assert request.block_id == TREE_BLOCK


# ---------------------------------------------------------------------------
# 7. Validation stage 11, wired into the one rule engine
# ---------------------------------------------------------------------------


def test_stage_eleven_is_installed_and_no_stage_is_pending():
    """Both seams are installed at import; nothing has to be switched on."""
    assert V.pending_stages() == ()
    assert V.STAGE_ML_READINESS not in V.pending_stages()


def test_without_dataset_statistics_stage_eleven_skips_rather_than_passing(reg):
    """A graph nobody measured must not read as ML-ready.

    SKIPPED, PASSED and NOT_IMPLEMENTED are three different statements and this is the
    middle one: the stage exists, and it was not given the measurement it needs.
    """
    graph, _ = model_strategy(reg)

    report = V.validate(graph, reg, available_bars=100_000)
    entry = stage_entry(report)

    assert entry["status"] == V.STATUS_SKIPPED
    assert entry["status"] != V.STATUS_PASSED
    assert V.STAGE_ML_READINESS not in report.pending_stages
    assert "dataset statistics" in entry["detail"]
    assert "NOT confirmed ready" in entry["detail"]


def test_a_graph_with_no_model_node_passes_stage_eleven(reg):
    """Nothing to check is not the same as could not check (Requirement 14.10)."""
    report = V.validate(linear_strategy(reg), reg, available_bars=100_000)

    assert stage_entry(report)["status"] == V.STATUS_PASSED


def test_stage_eleven_blocks_a_graph_whose_measured_data_falls_short(reg):
    """Validates: Requirements 14.3, 14.4"""
    graph, roles = model_strategy(reg)

    report = V.validate(
        graph,
        reg,
        available_bars=100_000,
        ml_dataset_stats={"rows": 10, "columns": 3, "horizon": 1},
    )

    assert stage_entry(report)["status"] == V.STATUS_FAILED
    assert not report.valid
    assert report.dag_hash is None
    codes = set(report.codes())
    assert P.CODE_INSUFFICIENT_FEATURE_COLUMNS in codes
    assert P.CODE_INSUFFICIENT_ROWS in codes
    for issue in report.errors:
        if issue["code"].startswith("INSUFFICIENT"):
            assert issue["node_id"] == roles["model"].id
            assert issue["expected"] is not None
            assert issue["actual"] is not None


def test_stage_eleven_passes_a_graph_with_ample_measured_data(reg):
    """The accepting case for the stage."""
    graph, _ = model_strategy(reg)

    report = V.validate(
        graph,
        reg,
        available_bars=1_000_000,
        ml_dataset_stats={"rows": 500_000, "columns": 12, "horizon": 1},
    )

    assert stage_entry(report)["status"] == V.STATUS_PASSED
    assert report.valid, report.errors


def test_stage_eleven_accepts_a_real_supervised_dataset(reg):
    """End to end over a measured dataset, not a hand-written stats mapping."""
    graph, _ = model_strategy(reg)
    dataset = build_dataset(n_rows=200, n_columns=8, warmup=10, horizon=2)

    report = V.validate(
        graph, reg, available_bars=100_000, ml_dataset_stats=dataset
    )

    # 188 usable rows is far short of what xgboost needs, so this blocks - and the
    # available figure is the dataset's own measured row count.
    entry = stage_entry(report)
    assert entry["status"] == V.STATUS_FAILED
    rows_issue = next(
        i for i in report.errors if i["code"] == P.CODE_INSUFFICIENT_ROWS
    )
    assert rows_issue["actual"] == dataset.n_rows == 188


def test_unreadable_dataset_statistics_skip_rather_than_pass(reg):
    """An estimate offered in place of a measurement is a skip, not a clean report."""
    graph, _ = model_strategy(reg)

    report = V.validate(
        graph,
        reg,
        available_bars=100_000,
        ml_dataset_stats={"total_rows": 100_000, "warmup_bars": 50},
    )

    entry = stage_entry(report)
    assert entry["status"] == V.STATUS_SKIPPED
    assert "could not run" in entry["detail"]


def test_statistics_naming_a_node_the_graph_does_not_carry_skip(reg):
    """Statistics measured against a different graph must not be applied to this one."""
    graph, roles = model_strategy(reg)

    report = V.validate(
        graph,
        reg,
        available_bars=100_000,
        ml_dataset_stats={
            roles["model"].id: {"rows": 500_000, "columns": 12, "horizon": 1},
            "some-node-that-is-not-here": {
                "rows": 500_000,
                "columns": 12,
                "horizon": 1,
            },
        },
    )

    assert stage_entry(report)["status"] == V.STATUS_SKIPPED


def test_per_node_statistics_are_applied_per_node(reg):
    """A graph whose model nodes read different matrices is measured per node."""
    graph, roles = model_strategy(reg)

    report = V.validate(
        graph,
        reg,
        available_bars=100_000,
        ml_dataset_stats={
            roles["model"].id: {"rows": 500_000, "columns": 12, "horizon": 1}
        },
    )

    assert stage_entry(report)["status"] == V.STATUS_PASSED
    assert report.valid, report.errors


def test_stage_eleven_is_order_independent(reg):
    """Permuting the node and edge lists cannot change the verdict or its order.

    The ML node list is computed once and sorted before any node is examined, so the same
    graph cannot validate differently across two processes.
    """
    graph, _ = model_strategy(reg)
    measured = {"rows": 10, "columns": 3, "horizon": 1}

    baseline = V.validate(
        graph, reg, available_bars=100_000, ml_dataset_stats=measured
    )
    fingerprint = [
        (i["code"], i["node_id"], i["expected"], i["actual"])
        for i in baseline.errors
        if i["code"].startswith("INSUFFICIENT")
    ]
    assert fingerprint

    rng = random.Random(6202)
    for _ in range(5):
        nodes = list(graph.nodes)
        edges = list(graph.edges)
        rng.shuffle(nodes)
        rng.shuffle(edges)
        permuted = V.validate(
            StrategyGraph(nodes=nodes, edges=edges),
            reg,
            available_bars=100_000,
            ml_dataset_stats=measured,
        )
        assert [
            (i["code"], i["node_id"], i["expected"], i["actual"])
            for i in permuted.errors
            if i["code"].startswith("INSUFFICIENT")
        ] == fingerprint


def test_clearing_the_hooks_returns_stage_eleven_to_not_implemented(reg):
    """The three states stay distinct, and the seam is still a seam."""
    graph, _ = model_strategy(reg)
    try:
        V.clear_stage_hooks()
        report = V.validate(graph, reg, available_bars=100_000)
        entry = stage_entry(report)
        assert entry["status"] == V.STATUS_NOT_IMPLEMENTED
        assert V.STAGE_ML_READINESS in report.pending_stages
        assert V.pending_stages() == (V.STAGE_LEAKAGE, V.STAGE_ML_READINESS)
    finally:
        V.install_default_stage_hooks()

    assert V.pending_stages() == ()


def test_the_stage_reads_its_model_figures_from_the_registry_descriptor(reg):
    """`metadata["model"]` is the route, so `strategy_dag` needs no `ml_models` import.

    Same mechanism stage 10b uses for its leakage facts.
    """
    descriptor = reg[TREE_BLOCK]
    view = P.ModelSpecView.coerce(descriptor)

    assert view is not None
    assert view.block_id == TREE_BLOCK
    assert view.min_training_rows == MODEL_SPECS[TREE_BLOCK].min_training_rows
    assert view.max_safe_epochs == MODEL_SPECS[TREE_BLOCK].max_safe_epochs
    assert view.sequence_length == MODEL_SPECS[TREE_BLOCK].sequence_length


def test_the_gate_and_the_stage_agree_on_the_same_graph(reg):
    """One rule engine: the stage is the gate, not a second implementation of it."""
    graph, roles = model_strategy(reg)
    measured = {"rows": 1_500, "columns": 4, "horizon": 1}

    report = V.validate(
        graph, reg, available_bars=100_000, ml_dataset_stats=measured
    )
    stage_codes = sorted(
        i["code"] for i in report.errors if i["code"].startswith("INSUFFICIENT")
    )

    warmup = report.warmup_by_node[roles["model"].id]
    verdict = P.check_ml_data_requirements(
        None,
        measured,
        reg[TREE_BLOCK],
        node_id=roles["model"].id,
        feature_lookback=warmup,
    )

    assert sorted(verdict.codes) == stage_codes
