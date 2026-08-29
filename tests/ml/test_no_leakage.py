"""
tests/ml/test_no_leakage.py

The leakage suite: strategy-builder task 5.11, the acceptance gate named in
`tasks.md` that must pass before Backtester or Marketplace work resumes.

`design.md` -> Feature engineering -> "Data leakage protection" mandates four cases, and
this file covers them:

  (a) a model trained on data shuffled in time scores materially worse on the chronological
      test split than one trained correctly - the empirical check, the one no structural
      rule can perform;
  (b) a future-shifted column raises `LOOKAHEAD_SHIFT`;
  (c) a `REVIEW_REQUIRED` block feeding a model is rejected;
  (d) global-statistic normalization is rejected.

**Property 18: No feature on a path into a model reads a bar later than its own row.**

**Validates: Requirements 18.2, 18.3, 18.4, 18.7, 18.8**

--------------------------------------------------------------------------------------
What this file adds over the suites already in place
--------------------------------------------------------------------------------------

`tests/test_leakage_validation_stage.py` (task 5.5) already covers the three refusal codes
at the validator level, one code at a time, with a rejecting and an accepting graph each.
`tests/test_ml_dataset.py` (task 5.6) already covers embargo arithmetic, split disjointness
and `rows(X) == len(y)`. Neither is repeated here. What is new:

* **The empirical case.** Cases (b)-(d) are structural: a rule reads a graph and refuses
  it. Case (a) cannot be checked that way - it asks whether the platform's own feature
  pipeline actually derives its signal from temporal order, so that destroying the order
  destroys the score. That is a measurement, and it is the only one of the four that would
  notice a leak nobody wrote a rule for.
* **The refusals expressed as a training-admission consequence.** Cases (b)-(d) are
  asserted here through the whole `validate()` pipeline and on `report.dag_hash`: a refused
  graph earns no hash, and a training job has nothing to bind a model version to. The
  stage-level suite asserts the issue list; this one asserts that the issue actually stops
  the strategy reaching a trainer.
* **Property 18 as a property, not as examples.** Three generated forms: over graphs (the
  validator names exactly the forward-reading nodes and no others, for arbitrary
  combinations of leaky and safe twins), over the shipped feature kernels (replacing every
  bar after row `k` leaves rows `0..k` bit-identical), and over the supervised dataset
  (row `i`'s features depend on no bar after `i`, while row `i`'s label depends on bar
  `i + horizon` - Requirements 18.7 and 18.8, the two halves that must not swap).

--------------------------------------------------------------------------------------
How case (a) is made deterministic, and why the margin is what it is
--------------------------------------------------------------------------------------

A naive "shuffled scores worse" test is flaky three ways: the model has its own randomness,
the data has a seed, and a single shuffle draw can get lucky. All three are removed rather
than tolerated:

1. **The learner has no randomness at all.** `_fit` is a ridge least-squares solve in
   closed form (`np.linalg.solve`) on train-split-standardised features. Given the same
   rows it returns the same weights, on every machine and every run - no `random_state` to
   set, no tree-building tie-breaks, no library version to drift. `DeterministicEnforcer`
   wraps every fit anyway, so the global NumPy and Python RNG state is pinned during the
   measurement and restored afterwards: a learner swapped in later cannot make this file
   non-reproducible, and this file cannot perturb another test's RNG stream.
2. **The data uses `RandomState`, not `default_rng`.** NumPy guarantees stream
   compatibility for the legacy `RandomState` generators indefinitely; `Generator` streams
   carry no such guarantee. The synthetic series is therefore identical across NumPy
   versions.
3. **The score is balanced accuracy, and the shuffled side is averaged over several
   draws.** Plain accuracy has a moving baseline - the majority class share of the test
   split drifts to 0.57 on some seeds, so a shuffled model that predicts one class
   constantly can "score" 0.57 and eat most of the margin. Balanced accuracy (the mean of
   the two class recalls) puts a constant predictor at exactly 0.500 by construction, so
   the comparison is against chance rather than against class imbalance.

**The data-generating process makes temporal order the whole signal.** Bar returns follow
an AR(1) with `phi = 0.7`: `r[t] = 0.7 * r[t-1] + noise`. The next bar's sign is therefore
predictable from the current bar's sign at a rate of `1 - arccos(0.7) / pi ~ 0.747`, and
that predictability lives entirely in the ordering of the bars. Permute the bars and the
autocorrelation is gone - not diluted, gone - so a model fit on the permuted series has
nothing to learn and its weights collapse towards zero.

**The margin.** Measured over the five seeds and four shuffle draws below: the honest model
scores 0.713 to 0.765 balanced accuracy, and every shuffled model scores exactly 0.500
(collapsed weights make it a constant predictor). The gap is never below 0.213. The
threshold asserted is 0.10, chosen as ~4 standard errors of a balanced-accuracy estimate on
this test split (`n_test = 478`, two classes, `se ~ 0.023`), which is the point below which
a gap would no longer be distinguishable from sampling noise. It is not a number tuned
until the test went green: it sits at less than half the smallest gap actually observed, so
the test has room to survive an honest model that gets somewhat worse or a shuffled model
that gets somewhat luckier, and still fails loudly if the temporal signal stops mattering.

Nothing here is mocked. The feature columns come from `FeatureEngine`'s shipped kernels, the
splits from `ml_dataset.splits_for_dataset`, the labels from
`ml_dataset.build_supervised_dataset`, and the refusals from the real assembled registry
driving the real `validate()` pipeline.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Dict, List, Sequence, Set, Tuple

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend_app.backend.feature_engineering import FeatureEngine
from backend_app.backend.ml_dataset import (
    CLASS_UP,
    LabelMode,
    SupervisedDataset,
    build_supervised_dataset,
    splits_for_dataset,
)
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag import validator as V
from backend_app.backend.strategy_dag.feature_matrix import build_feature_matrix
from backend_app.backend.strategy_dag.registry import BlockRegistry
from backend_app.backend.strategy_dag.schema import EdgeSpec, NodeSpec, StrategyGraph
from backend_app.core.ml_safety import DeterministicEnforcer

# The graph builders are imported rather than restated: two copies of "what a model
# pipeline looks like" would drift, and the stage-level suite is where they live.
from tests.test_leakage_validation_stage import action_node, data_node, node

# ══════════════════════════════════════════════════════════════════════════
#  THE MEASUREMENT'S CONSTANTS
# ══════════════════════════════════════════════════════════════════════════

#: AR(1) coefficient. The entire predictable component of the series.
PHI = 0.7
#: Per-bar innovation scale (1% moves), so prices stay in a realistic range.
SIGMA = 0.01
#: Bars per synthetic series. 2400 leaves ~478 rows in the test split, which fixes the
#: standard error of a balanced-accuracy estimate at ~0.023.
BARS = 2400
#: Label horizon: the next bar. Requirement 18.8 - strictly forward, and only forward.
HORIZON = 1
#: Longest feature lookback below, and therefore half of the embargo floor (18.6).
LOOKBACK = 5
VAL_FRACTION = 0.2
TEST_FRACTION = 0.2

#: Five independent series. A property that holds on one seed and not the others is not a
#: property, and naming the seeds keeps every failure reproducible.
SEEDS: Tuple[int, ...] = (11, 12, 13, 14, 15)
#: Bar permutations per seed. The shuffled score is a constant-predictor 0.500 every time,
#: so a handful of draws is enough to show it is not one lucky permutation.
BAR_SHUFFLES_PER_SEED = 4
#: Label permutations per seed, averaged. More draws are needed here: see
#: `test_permuting_the_labels_within_the_train_split_destroys_the_score`.
LABEL_SHUFFLES_PER_SEED = 15

#: Below this the honest pipeline is not learning, and the comparison below would be two
#: coin flips agreeing. ~4 standard errors above chance.
MIN_HONEST_SCORE = 0.60
#: Above this a "shuffled" model is not actually shuffled.
MAX_SHUFFLED_SCORE = 0.55
#: "Materially worse", in balanced-accuracy points. ~4 standard errors; observed gaps are
#: never below 0.213.
MIN_MATERIAL_GAP = 0.10
#: What a leak buys. A forward-shifted column contains the label, so the score goes to the
#: ceiling - which is why the structural rules exist.
LEAKED_SCORE_FLOOR = 0.95

PROPERTY_SETTINGS = settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


# ══════════════════════════════════════════════════════════════════════════
#  SYNTHETIC MARKET, REAL FEATURE KERNELS
# ══════════════════════════════════════════════════════════════════════════


def _ar1_prices(seed: int, bars: int = BARS) -> Tuple[np.ndarray, np.ndarray]:
    """A price series whose only predictable component is its bar ordering.

    `RandomState` rather than `default_rng`: NumPy guarantees the legacy generators'
    streams do not change, so this series is the same series next year.
    """
    rs = np.random.RandomState(seed)
    innovations = rs.normal(0.0, SIGMA, bars)
    returns = np.zeros(bars, dtype=float)
    for t in range(1, bars):
        returns[t] = PHI * returns[t - 1] + innovations[t]
    prices = 100.0 * np.exp(np.cumsum(returns))
    index = np.arange(bars, dtype="int64") * 60_000  # one-minute bars, milliseconds
    return index, prices


def _honest_features(index: np.ndarray, prices: np.ndarray):
    """Five backward-only columns, each from a shipped `FeatureEngine` kernel.

    Every one reads bar `t` and earlier. `build_feature_matrix` derives the warmup offset
    from the NaNs actually present, so the offset reflects the data rather than a claim
    about it.
    """
    ret1 = FeatureEngine.compute_returns(prices, (1,))[:, 0]
    ret3 = FeatureEngine.compute_returns(prices, (3,))[:, 0]
    lags = FeatureEngine.compute_lag_features(ret1, [1, 2])
    rolling = FeatureEngine.compute_rolling_mean(ret1, LOOKBACK)
    return build_feature_matrix(
        index,
        ["ret_1", "ret_3", "ret_1_lag_1", "ret_1_lag_2", "ret_1_mean_5"],
        [ret1, ret3, lags[:, 0], lags[:, 1], rolling],
        node_id="n_features",
    )


def _dataset(index: np.ndarray, prices: np.ndarray) -> SupervisedDataset:
    """Features and labels through the platform's own builder, never by hand."""
    return build_supervised_dataset(
        _honest_features(index, prices),
        prices,
        HORIZON,
        label_mode=LabelMode.CLASSIFICATION,
        threshold=0.0,
    )


# ══════════════════════════════════════════════════════════════════════════
#  A LEARNER WITH NO RANDOMNESS
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class _Ridge:
    """Weights plus the train-split scaler they were fit with.

    The scaler travels with the model on purpose: standardising the test split with its own
    statistics would itself be a leak (the `feat_standardize` / `GLOBAL_STATISTIC_LEAK`
    family), so the only statistics available at prediction time are the training ones.
    """

    weights: np.ndarray
    mean: np.ndarray
    scale: np.ndarray


def _fit(X: np.ndarray, y: np.ndarray, *, seed: int = 0, l2: float = 1e-2) -> _Ridge:
    """Closed-form ridge on +-1 labels. Same rows in, same weights out, always."""
    with DeterministicEnforcer.deterministic_context(seed=seed):
        mean = X.mean(axis=0)
        scale = X.std(axis=0)
        scale = np.where(scale > 0, scale, 1.0)
        design = np.hstack([(X - mean) / scale, np.ones((X.shape[0], 1))])
        gram = design.T @ design + l2 * np.eye(design.shape[1])
        weights = np.linalg.solve(gram, design.T @ y)
    return _Ridge(weights=weights, mean=mean, scale=scale)


def _predict(model: _Ridge, X: np.ndarray) -> np.ndarray:
    design = np.hstack([(X - model.mean) / model.scale, np.ones((X.shape[0], 1))])
    return np.where(design @ model.weights >= 0, 1, -1)


def _pm1(labels: np.ndarray) -> np.ndarray:
    """Up as +1, everything else as -1. `threshold=0.0` makes FLAT a measure-zero case."""
    return np.where(labels == CLASS_UP, 1, -1)


def _balanced_accuracy(predicted: np.ndarray, truth: np.ndarray) -> float:
    """Mean of the per-class recalls: a constant predictor scores exactly 0.500.

    Plain accuracy would reward a collapsed model for guessing whichever class happens to
    be more common in the test split, which is precisely the confound that makes a naive
    shuffle test flaky.
    """
    recalls = [
        float((predicted[truth == cls] == cls).mean())
        for cls in (1, -1)
        if bool((truth == cls).any())
    ]
    return float(np.mean(recalls)) if recalls else 0.0


@dataclass(frozen=True)
class _Chronological:
    """One seed's honest, chronologically-split, embargoed training problem."""

    seed: int
    index: np.ndarray
    prices: np.ndarray
    X_train: np.ndarray
    y_train: np.ndarray
    X_test: np.ndarray
    y_test: np.ndarray
    embargo_bars: int

    def score(self, model: _Ridge) -> float:
        """Any model's balanced accuracy on *this* seed's chronological test split."""
        return _balanced_accuracy(_predict(model, self.X_test), self.y_test)


def _chronological(seed: int) -> _Chronological:
    index, prices = _ar1_prices(seed)
    dataset = _dataset(index, prices)
    splits = splits_for_dataset(
        dataset, VAL_FRACTION, TEST_FRACTION, feature_lookback=LOOKBACK
    )
    return _Chronological(
        seed=seed,
        index=index,
        prices=prices,
        X_train=splits.train.take(dataset.X),
        y_train=_pm1(splits.train.take(dataset.y)),
        X_test=splits.test.take(dataset.X),
        y_test=_pm1(splits.test.take(dataset.y)),
        embargo_bars=splits.embargo_bars,
    )


@pytest.fixture(scope="module")
def chronological() -> Dict[int, _Chronological]:
    """Each seed's honest problem, built once: feature kernels are Python loops."""
    return {seed: _chronological(seed) for seed in SEEDS}


@pytest.fixture(scope="module")
def honest_models(chronological) -> Dict[int, _Ridge]:
    return {
        seed: _fit(case.X_train, case.y_train, seed=seed)
        for seed, case in chronological.items()
    }


# ══════════════════════════════════════════════════════════════════════════
#  CASE (a): SHUFFLED IN TIME SCORES MATERIALLY WORSE
#  design.md -> "a model trained on shuffled-in-time data scores materially worse
#  on the chronological test split than one trained correctly"
# ══════════════════════════════════════════════════════════════════════════


def test_the_honest_pipeline_learns_the_temporal_signal(chronological, honest_models):
    """The control. Without it, "shuffled scores worse" could be two collapsed models.

    This also pins the embargo: the gap between train and test absorbs the longest feature
    lookback plus the label horizon, so the score below is earned on rows whose label
    windows never touched the training rows' feature windows.
    """
    for seed, case in chronological.items():
        score = case.score(honest_models[seed])
        assert score >= MIN_HONEST_SCORE, (
            f"seed {seed}: honest balanced accuracy {score:.3f} < "
            f"{MIN_HONEST_SCORE}. The AR(1) signal should be learnable; if it is not, "
            f"the comparison against a shuffled model means nothing."
        )
        assert case.embargo_bars >= LOOKBACK + HORIZON


def test_a_model_trained_on_bars_shuffled_in_time_scores_materially_worse(
    chronological, honest_models
):
    """Case (a). Permuting the bars destroys the signal, and the score says so.

    The permutation is applied to the *bars*, under an unchanged, still strictly increasing
    timestamp index - which is the only way the bug is representable. `make_temporal_splits`
    refuses a non-monotonic index outright, and `SplitRange` hands back contiguous slices
    with no member list to permute, so the shuffle cannot be introduced downstream. What
    remains is the upstream form: rows that arrived out of order, or a `sample(frac=1)`
    before featurisation. That is what is modelled here.

    Every shuffled model collapses to a constant predictor, which balanced accuracy scores
    at exactly 0.500, so the observed gap is the honest score minus one half - never below
    0.213 across these seeds and draws. The assertion demands 0.10.
    """
    for seed, case in chronological.items():
        honest_score = case.score(honest_models[seed])

        for draw in range(BAR_SHUFFLES_PER_SEED):
            rs = np.random.RandomState(10_000 + 100 * seed + draw)
            shuffled_prices = case.prices[rs.permutation(case.prices.size)]

            shuffled_dataset = _dataset(case.index, shuffled_prices)
            shuffled_splits = splits_for_dataset(
                shuffled_dataset, VAL_FRACTION, TEST_FRACTION, feature_lookback=LOOKBACK
            )
            shuffled_model = _fit(
                shuffled_splits.train.take(shuffled_dataset.X),
                _pm1(shuffled_splits.train.take(shuffled_dataset.y)),
                seed=seed,
            )

            # Scored on the *chronological* test split: the only split that resembles
            # trading, and the one the design names.
            shuffled_score = case.score(shuffled_model)
            gap = honest_score - shuffled_score

            assert shuffled_score <= MAX_SHUFFLED_SCORE, (
                f"seed {seed} draw {draw}: a model trained on bars shuffled in time "
                f"scored {shuffled_score:.3f} on the chronological test split. Either the "
                f"shuffle is not destroying the temporal signal, or the signal is not "
                f"temporal."
            )
            assert gap >= MIN_MATERIAL_GAP, (
                f"seed {seed} draw {draw}: honest {honest_score:.3f} vs shuffled "
                f"{shuffled_score:.3f} is a gap of {gap:.3f}, below the "
                f"{MIN_MATERIAL_GAP} required to call it material."
            )


def test_permuting_the_labels_within_the_train_split_destroys_the_score(
    chronological, honest_models
):
    """The other shuffle that ships by accident: X kept in order, y permuted.

    This is the recorded ML-1 family - features and labels drawn from different bars - and
    `build_supervised_dataset` prevents it structurally by slicing both from one range. The
    measurement here shows what the structure is worth: break the pairing and the score
    goes to chance.

    Compared against the *mean* over 15 permutations rather than the worst one, deliberately.
    With five collinear momentum columns, a weight vector fit on permuted labels still has
    roughly a coin flip's chance of pointing the right way, so an individual draw can land
    near 0.69 while the null's centre sits at 0.50. Asserting against the maximum would be
    asserting that a coin never comes up heads; the mean is the honest summary of the null,
    and the honest model beats it by 0.19 or more on every seed here.
    """
    for seed, case in chronological.items():
        honest_score = case.score(honest_models[seed])

        null_scores: List[float] = []
        for draw in range(LABEL_SHUFFLES_PER_SEED):
            rs = np.random.RandomState(20_000 + 100 * seed + draw)
            permuted_labels = case.y_train[rs.permutation(case.y_train.size)]
            null_scores.append(case.score(_fit(case.X_train, permuted_labels, seed=seed)))

        null_mean = float(np.mean(null_scores))
        gap = honest_score - null_mean

        assert gap >= MIN_MATERIAL_GAP, (
            f"seed {seed}: honest {honest_score:.3f} vs label-permuted null mean "
            f"{null_mean:.3f} (over {LABEL_SHUFFLES_PER_SEED} draws) is a gap of "
            f"{gap:.3f}, below {MIN_MATERIAL_GAP}. Either the feature/label pairing "
            f"carries no information, or it was never intact."
        )


def test_a_future_shifted_column_would_buy_an_impossible_score(chronological):
    """Why cases (b)-(d) exist: one forward-shifted column and the model is perfect.

    The column added here is the next bar's return - exactly what `shift(bars=-1)` produces
    on a model path. The score goes to the ceiling, which is the shape every leaked
    backtest has. The structural refusal of the graph that expresses this is asserted in
    `test_a_future_shifted_column_is_refused_before_training`; this test is the reason that
    refusal matters.
    """
    for seed, case in chronological.items():
        ret1 = FeatureEngine.compute_returns(case.prices, (1,))[:, 0]
        # The leak: bar t carries bar t+1's return. Trailing NaN, and the dataset drops the
        # trailing HORIZON rows anyway.
        leaked = np.full(ret1.size, np.nan)
        leaked[:-1] = ret1[1:]

        matrix = build_feature_matrix(
            case.index,
            ["ret_1", "ret_1_shifted_forward"],
            [ret1, leaked],
            node_id="n_leaky",
        )
        leaky_dataset = build_supervised_dataset(
            matrix,
            case.prices,
            HORIZON,
            label_mode=LabelMode.CLASSIFICATION,
            threshold=0.0,
        )
        splits = splits_for_dataset(
            leaky_dataset, VAL_FRACTION, TEST_FRACTION, feature_lookback=LOOKBACK
        )
        model = _fit(
            splits.train.take(leaky_dataset.X),
            _pm1(splits.train.take(leaky_dataset.y)),
            seed=seed,
        )
        score = _balanced_accuracy(
            _predict(model, splits.test.take(leaky_dataset.X)),
            _pm1(splits.test.take(leaky_dataset.y)),
        )

        assert score >= LEAKED_SCORE_FLOOR, (
            f"seed {seed}: a column holding the next bar's return scored only "
            f"{score:.3f}. The leak is supposed to be trivially learnable - if it is not, "
            f"this test is no longer demonstrating what the refusals prevent."
        )


def test_the_measurement_is_reproducible_within_and_across_runs(chronological):
    """Determinism, asserted rather than assumed.

    Both halves are exact, not approximate: the learner is a closed-form solve, so refitting
    the same rows returns the same weights bit for bit, and rebuilding the same seed's data
    returns the same series. A flaky empirical test gets deleted; this is the assertion that
    says it is not one.
    """
    case = chronological[SEEDS[0]]

    first = _fit(case.X_train, case.y_train, seed=case.seed)
    second = _fit(case.X_train, case.y_train, seed=case.seed)
    np.testing.assert_array_equal(first.weights, second.weights)
    assert case.score(first) == case.score(second)

    rebuilt = _chronological(case.seed)
    np.testing.assert_array_equal(rebuilt.prices, case.prices)
    np.testing.assert_array_equal(rebuilt.X_train, case.X_train)
    np.testing.assert_array_equal(rebuilt.y_test, case.y_test)


# ══════════════════════════════════════════════════════════════════════════
#  CASES (b), (c), (d): THE REFUSALS, AS A TRAINING-ADMISSION CONSEQUENCE
#
#  The stage-level suite (task 5.5) asserts the issue lists. These assert the
#  consequence: a refused graph earns no dag_hash, so there is nothing for a
#  training job to bind a model version to.
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def reg() -> BlockRegistry:
    return registry_module.build_registry()


@pytest.fixture(autouse=True)
def default_hooks():
    """Stage 10b installed regardless of collection order.

    Another module exercises the stage seam and clears the hooks in its teardown, which
    also removes the leakage implementation installed at import. Restoring it here keeps
    this suite from passing or failing on collection order.
    """
    V.install_default_stage_hooks()
    yield
    V.install_default_stage_hooks()


def _shift_pipeline(reg: BlockRegistry, bars: int) -> Tuple[StrategyGraph, NodeSpec]:
    """data -> ema -> shift(bars) -> feat_lag -> xgboost -> gt -> buy."""
    data = data_node(reg)
    ema = node(reg, "ema", window=20, source="close")
    shift = node(reg, "shift", bars=bars)
    lag = node(reg, "feat_lag", lags=[1, 2, 3])
    model = node(reg, "xgboost")
    const = node(reg, "constant", value=0.5)
    gt = node(reg, "gt")
    act = action_node(reg)
    graph = StrategyGraph(
        nodes=[data, ema, shift, lag, model, const, gt, act],
        edges=[
            EdgeSpec.create(data.id, "close", ema.id, "series"),
            EdgeSpec.create(ema.id, "value", shift.id, "a"),
            EdgeSpec.create(shift.id, "out", lag.id, "series"),
            EdgeSpec.create(lag.id, "matrix", model.id, "features"),
            EdgeSpec.create(model.id, "prediction", gt.id, "left"),
            EdgeSpec.create(const.id, "value", gt.id, "right"),
            EdgeSpec.create(gt.id, "out", act.id, "signal"),
        ],
    )
    return graph, shift


def _ichimoku_pipeline(reg: BlockRegistry, port: str) -> Tuple[StrategyGraph, NodeSpec]:
    """data -> ichimoku[port] -> feat_lag -> xgboost -> gt -> buy."""
    data = data_node(reg)
    ich = node(reg, "ichimoku_cloud", tenkan=9, kijun=26, senkou_b=52)
    lag = node(reg, "feat_lag", lags=[1, 2, 3])
    model = node(reg, "xgboost")
    const = node(reg, "constant", value=0.5)
    gt = node(reg, "gt")
    act = action_node(reg)
    graph = StrategyGraph(
        nodes=[data, ich, lag, model, const, gt, act],
        edges=[
            EdgeSpec.create(data.id, "high", ich.id, "high"),
            EdgeSpec.create(data.id, "low", ich.id, "low"),
            EdgeSpec.create(data.id, "close", ich.id, "close"),
            EdgeSpec.create(ich.id, port, lag.id, "series"),
            EdgeSpec.create(lag.id, "matrix", model.id, "features"),
            EdgeSpec.create(model.id, "prediction", gt.id, "left"),
            EdgeSpec.create(const.id, "value", gt.id, "right"),
            EdgeSpec.create(gt.id, "out", act.id, "signal"),
        ],
    )
    return graph, ich


def _statistic_pipeline(
    reg: BlockRegistry, block_id: str, **params
) -> Tuple[StrategyGraph, NodeSpec]:
    """data -> ema -> (zscore | normalize) -> xgboost -> gt -> buy."""
    data = data_node(reg)
    ema = node(reg, "ema", window=20, source="close")
    stat = node(reg, block_id, **params)
    model = node(reg, "xgboost")
    const = node(reg, "constant", value=0.5)
    gt = node(reg, "gt")
    act = action_node(reg)
    graph = StrategyGraph(
        nodes=[data, ema, stat, model, const, gt, act],
        edges=[
            EdgeSpec.create(data.id, "close", ema.id, "series"),
            EdgeSpec.create(ema.id, "value", stat.id, "series"),
            EdgeSpec.create(stat.id, "matrix", model.id, "features"),
            EdgeSpec.create(model.id, "prediction", gt.id, "left"),
            EdgeSpec.create(const.id, "value", gt.id, "right"),
            EdgeSpec.create(gt.id, "out", act.id, "signal"),
        ],
    )
    return graph, stat


def _assert_refused(graph: StrategyGraph, reg: BlockRegistry, code: str, node_id: str):
    """The graph is refused, the refusal names the node, and no hash is produced."""
    report = V.validate(graph, reg, available_bars=100_000)

    assert not report.valid, f"expected {code} to refuse this graph"
    named = [i for i in report.errors if i["code"] == code and i["node_id"] == node_id]
    assert named, (
        f"{code} not reported against {node_id}; got "
        f"{sorted((i['code'], i['node_id']) for i in report.errors)}"
    )
    # No hash means no version to persist and nothing for a training job to bind to: the
    # refusal is not advisory.
    assert report.dag_hash is None


def test_a_future_shifted_column_is_refused_before_training(reg):
    """Case (b). Requirement 18.2.

    `shift(bars=-1)` on a model path is refused twice over - the parameter range rejects a
    negative bar count and stage 10b names the lookahead - and neither refusal is the other's
    fallback. Only `LOOKAHEAD_SHIFT` explains *why* it is unsafe rather than merely
    out of range, which is what an author has to read.
    """
    graph, shift = _shift_pipeline(reg, bars=-1)
    _assert_refused(graph, reg, V.CODE_LOOKAHEAD_SHIFT, shift.id)


def test_a_review_required_block_feeding_a_model_is_refused_before_training(reg):
    """Case (c). Requirement 18.3.

    Ichimoku's `chikou` at bar `t` is the close of bar `t + kijun`, and the descriptor
    declares that port as the leaky one. The expectation is read from the registry rather
    than hardcoded, so a reclassification cannot leave this test asserting last year's rule.
    """
    descriptor = reg["ichimoku_cloud"]
    assert V._leakage_risk_name(descriptor) == "REVIEW_REQUIRED"
    leaky_ports = V._leaky_output_ports(descriptor)
    assert leaky_ports, "the descriptor no longer declares which outputs leak"

    graph, ich = _ichimoku_pipeline(reg, leaky_ports[0])
    _assert_refused(graph, reg, V.CODE_LEAKY_FEATURE_INTO_MODEL, ich.id)


@pytest.mark.parametrize(
    "block_id, params",
    [
        ("feat_zscore", {"window": 20, "mode": "global"}),
        ("feat_normalize", {"window": 20, "method": "minmax", "mode": "global"}),
    ],
)
def test_global_statistic_normalization_is_refused_before_training(reg, block_id, params):
    """Case (d). Requirement 18.4.

    A whole-series mean or min/max is computed from bars the model will not have at bar `t`,
    including every bar of the test split - so a graph carrying one cannot produce an honest
    score for any split, which is why the refusal does not depend on reaching a model.
    """
    graph, stat = _statistic_pipeline(reg, block_id, **params)
    _assert_refused(graph, reg, V.CODE_GLOBAL_STATISTIC_LEAK, stat.id)


@pytest.mark.parametrize(
    "builder_name, build",
    [
        ("backward shift", lambda reg: _shift_pipeline(reg, bars=1)),
        (
            "past-only ichimoku port",
            lambda reg: _ichimoku_pipeline(reg, "tenkan"),
        ),
        (
            "rolling zscore",
            lambda reg: _statistic_pipeline(reg, "feat_zscore", window=20, mode="rolling"),
        ),
        (
            "expanding zscore",
            lambda reg: _statistic_pipeline(
                reg, "feat_zscore", window=20, mode="expanding"
            ),
        ),
        (
            "rolling normalize",
            lambda reg: _statistic_pipeline(
                reg, "feat_normalize", window=20, method="robust", mode="rolling"
            ),
        ),
    ],
)
def test_the_leak_free_twin_of_each_refusal_trains_and_earns_a_hash(
    reg, builder_name, build
):
    """The accepting half of every refusal above, through the whole pipeline.

    A control that refuses everything protects nothing an author can use. Each twin here is
    one parameter away from a graph refused above and must validate completely - including
    earning a `dag_hash`, since that is what a version and a training job are keyed on.
    """
    graph, _ = build(reg)
    report = V.validate(graph, reg, available_bars=100_000)

    leakage_codes = {
        V.CODE_LOOKAHEAD_SHIFT,
        V.CODE_LEAKY_FEATURE_INTO_MODEL,
        V.CODE_GLOBAL_STATISTIC_LEAK,
    }
    assert leakage_codes.isdisjoint(set(report.codes())), (
        f"{builder_name} is leak-free but was flagged: {report.errors}"
    )
    assert report.valid, f"{builder_name} should validate: {report.errors}"
    assert report.dag_hash is not None


# ══════════════════════════════════════════════════════════════════════════
#  PROPERTY 18, FORM 1: OVER GRAPHS
#
#  "No feature on a path into a model reads a bar later than its own row",
#  stated over arbitrary combinations of forward-reading constructs and their
#  past-only twins rather than one example each.
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class _Injection:
    """One drawn construct to graft onto a model pipeline, leaky or safe."""

    kind: str
    into_model: bool
    bars: int
    window: int
    port_pick: int


_INJECTION_KINDS = (
    "negative_shift",       # leaky always: a forward shift is broken wherever it sits
    "positive_shift",       # safe twin
    "leaky_ichimoku_port",  # leaky only on a path into a model
    "safe_ichimoku_port",   # safe twin
    "global_zscore",        # leaky always: whole-series statistics see every split
    "rolling_zscore",       # safe twin
    "expanding_zscore",     # safe twin: bars up to and including t only
    "global_normalize",     # leaky always
    "rolling_normalize",    # safe twin
)


@st.composite
def _injections(draw) -> _Injection:
    return _Injection(
        kind=draw(st.sampled_from(_INJECTION_KINDS)),
        into_model=draw(st.booleans()),
        bars=draw(st.integers(min_value=1, max_value=12)),
        window=draw(st.integers(min_value=2, max_value=60)),
        port_pick=draw(st.integers(min_value=0, max_value=7)),
    )


def _skeleton(reg: BlockRegistry):
    """A two-matrix model pipeline: data -> ema -> {lag, returns} -> concat -> xgboost.

    `feat_concat` gives every injected matrix somewhere legitimate to go, so an injection
    lands on a real path into the model rather than on an improvised edge.
    """
    data = data_node(reg)
    ema = node(reg, "ema", window=20, source="close")
    lag = node(reg, "feat_lag", lags=[1, 2, 3])
    returns = node(reg, "feat_returns", periods=[1])
    concat = node(reg, "feat_concat")
    model = node(reg, "xgboost")
    const = node(reg, "constant", value=0.5)
    gt = node(reg, "gt")
    act = action_node(reg)

    graph = StrategyGraph(
        nodes=[data, ema, lag, returns, concat, model, const, gt, act],
        edges=[
            EdgeSpec.create(data.id, "close", ema.id, "series"),
            EdgeSpec.create(ema.id, "value", lag.id, "series"),
            EdgeSpec.create(ema.id, "value", returns.id, "series"),
            EdgeSpec.create(lag.id, "matrix", concat.id, "matrix"),
            EdgeSpec.create(returns.id, "matrix", concat.id, "matrix"),
            EdgeSpec.create(concat.id, "matrix", model.id, "features"),
            EdgeSpec.create(model.id, "prediction", gt.id, "left"),
            EdgeSpec.create(const.id, "value", gt.id, "right"),
            EdgeSpec.create(gt.id, "out", act.id, "signal"),
        ],
    )
    return graph, {"data": data, "ema": ema, "concat": concat, "model": model}


def _series_sink(reg: BlockRegistry, graph: StrategyGraph, source: NodeSpec, port: str):
    """Consume a series output on a branch with no model downstream."""
    const = node(reg, "constant", value=1.0)
    compare = node(reg, "gt")
    act = action_node(reg)
    graph.nodes.extend([const, compare, act])
    graph.edges.extend(
        [
            EdgeSpec.create(source.id, port, compare.id, "left"),
            EdgeSpec.create(const.id, "value", compare.id, "right"),
            EdgeSpec.create(compare.id, "out", act.id, "signal"),
        ]
    )


def _matrix_into_model(graph: StrategyGraph, roles, source: NodeSpec, port: str):
    """Route a matrix output into the model through the concat node."""
    graph.edges.append(EdgeSpec.create(source.id, port, roles["concat"].id, "matrix"))


def _series_into_model(
    reg: BlockRegistry, graph: StrategyGraph, roles, source: NodeSpec, port: str
):
    """Route a series output into the model through its own lag block."""
    lag = node(reg, "feat_lag", lags=[1])
    graph.nodes.append(lag)
    graph.edges.append(EdgeSpec.create(source.id, port, lag.id, "series"))
    _matrix_into_model(graph, roles, lag, "matrix")


def _matrix_sink(reg: BlockRegistry, graph: StrategyGraph, source: NodeSpec, port: str):
    """Consume a matrix output off every model path."""
    keep = node(reg, "feat_select", columns=["c0"])
    graph.nodes.append(keep)
    graph.edges.append(EdgeSpec.create(source.id, port, keep.id, "matrix"))


def _materialise(
    reg: BlockRegistry, injections: Sequence[_Injection]
) -> Tuple[StrategyGraph, Set[Tuple[str, str]]]:
    """Graft the drawn injections onto the skeleton and state the expected verdict.

    Returns the graph and the exact set of `(code, node_id)` pairs stage 10b must report -
    no more and no fewer. The expectations follow the three documented rules:

      * a negative shift is reported wherever it sits (its runtime raises on it);
      * a whole-series statistic is reported wherever it sits (it sees every split);
      * a future-encoding output is reported only on a path into a model.
    """
    graph, roles = _skeleton(reg)
    expected: Set[Tuple[str, str]] = set()

    descriptor = reg["ichimoku_cloud"]
    leaky_ports = list(V._leaky_output_ports(descriptor))
    safe_ports = [p.name for p in descriptor.outputs if p.name not in leaky_ports]

    for injection in injections:
        kind = injection.kind

        if kind in ("negative_shift", "positive_shift"):
            bars = -injection.bars if kind == "negative_shift" else injection.bars
            shift = node(reg, "shift", bars=bars)
            graph.nodes.append(shift)
            graph.edges.append(
                EdgeSpec.create(roles["ema"].id, "value", shift.id, "a")
            )
            if injection.into_model:
                _series_into_model(reg, graph, roles, shift, "out")
            else:
                _series_sink(reg, graph, shift, "out")
            if kind == "negative_shift":
                expected.add((V.CODE_LOOKAHEAD_SHIFT, shift.id))
            continue

        if kind in ("leaky_ichimoku_port", "safe_ichimoku_port"):
            ports = leaky_ports if kind == "leaky_ichimoku_port" else safe_ports
            port = ports[injection.port_pick % len(ports)]
            ich = node(reg, "ichimoku_cloud", tenkan=9, kijun=26, senkou_b=52)
            graph.nodes.append(ich)
            for side in ("high", "low", "close"):
                graph.edges.append(
                    EdgeSpec.create(roles["data"].id, side, ich.id, side)
                )
            if injection.into_model:
                _series_into_model(reg, graph, roles, ich, port)
            else:
                _series_sink(reg, graph, ich, port)
            if kind == "leaky_ichimoku_port" and injection.into_model:
                expected.add((V.CODE_LEAKY_FEATURE_INTO_MODEL, ich.id))
            continue

        if kind.endswith("zscore"):
            mode = kind.split("_")[0]
            stat = node(reg, "feat_zscore", window=injection.window, mode=mode)
        else:
            mode = kind.split("_")[0]
            stat = node(
                reg,
                "feat_normalize",
                window=injection.window,
                method="minmax",
                mode=mode,
            )
        graph.nodes.append(stat)
        graph.edges.append(EdgeSpec.create(roles["ema"].id, "value", stat.id, "series"))
        if injection.into_model:
            _matrix_into_model(graph, roles, stat, "matrix")
        else:
            _matrix_sink(reg, graph, stat, "matrix")
        if mode == "global":
            expected.add((V.CODE_GLOBAL_STATISTIC_LEAK, stat.id))

    return graph, expected


@PROPERTY_SETTINGS
@given(injections=st.lists(_injections(), min_size=0, max_size=4))
def test_property_18_over_graphs_the_validator_names_exactly_the_forward_readers(
    injections,
):
    """Property 18, over generated graphs.

    Both directions in one assertion. Completeness: every drawn forward-reading construct on
    a path into a model is named. Soundness: nothing else is - each leaky construct has a
    past-only twin one parameter away, and flagging the twin would refuse a legitimate
    strategy. Set equality is what makes the second half real; asserting only that the leak
    was caught would pass for a rule that refuses everything.

    A cached registry rather than the fixture: Hypothesis runs many examples per test, and a
    function-scoped fixture inside `@given` is both a Hypothesis health-check failure and a
    per-example rebuild of a ~150 ms assembly.
    """
    reg = _cached_registry()
    graph, expected = _materialise(reg, injections)

    issues = V.validate_no_lookahead(graph, reg)
    reported = {(issue["code"], issue["node_id"]) for issue in issues}

    assert reported == expected, (
        f"drawn: {[(i.kind, i.into_model) for i in injections]}\n"
        f"missing: {sorted(expected - reported)}\n"
        f"unexpected: {sorted(reported - expected)}"
    )
    for issue in issues:
        assert issue["message"].strip() and issue["fix_hint"].strip()


@lru_cache(maxsize=1)
def _cached_registry() -> BlockRegistry:
    """The real assembled registry, built at most once per process for the property above."""
    return registry_module.build_registry()


# ══════════════════════════════════════════════════════════════════════════
#  PROPERTY 18, FORM 2: OVER THE SHIPPED FEATURE KERNELS
#
#  A kernel reads no bar later than its own row exactly when replacing every
#  bar after row k leaves rows 0..k unchanged. That is the property, stated as
#  an experiment the kernel cannot pass by accident.
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class _Kernel:
    """One feature kernel plus a strategy for its parameters."""

    name: str
    apply: Callable[..., np.ndarray]
    params: st.SearchStrategy


def _as_2d(result) -> np.ndarray:
    """Normalise a kernel's return (array, matrix or tuple of arrays) to one 2-D block."""
    if isinstance(result, tuple):
        columns = []
        for item in result:
            array = np.asarray(item, dtype=float)
            columns.append(array.reshape(-1, 1) if array.ndim == 1 else array)
        return np.hstack(columns)
    array = np.asarray(result, dtype=float)
    return array.reshape(-1, 1) if array.ndim == 1 else array


_WINDOWS = st.integers(min_value=2, max_value=12)

LEAK_SAFE_KERNELS: Tuple[_Kernel, ...] = (
    _Kernel("log_returns", lambda p: FeatureEngine.compute_log_returns(p), st.just({})),
    _Kernel(
        "returns",
        lambda p, periods: FeatureEngine.compute_returns(p, periods),
        st.fixed_dictionaries(
            {"periods": st.lists(st.integers(1, 6), min_size=1, max_size=3, unique=True)}
        ),
    ),
    _Kernel(
        "lag_features",
        lambda p, lags: FeatureEngine.compute_lag_features(p, lags),
        st.fixed_dictionaries(
            {"lags": st.lists(st.integers(1, 6), min_size=1, max_size=3, unique=True)}
        ),
    ),
    _Kernel(
        "rolling_mean",
        lambda p, window: FeatureEngine.compute_rolling_mean(p, window),
        st.fixed_dictionaries({"window": _WINDOWS}),
    ),
    _Kernel(
        "rolling_std",
        lambda p, window, ddof: FeatureEngine.compute_rolling_std(p, window, ddof),
        st.fixed_dictionaries({"window": _WINDOWS, "ddof": st.integers(0, 1)}),
    ),
    _Kernel(
        "rolling_stats",
        lambda p, window: FeatureEngine.compute_rolling_stats(p, window),
        st.fixed_dictionaries({"window": _WINDOWS}),
    ),
    _Kernel(
        "volatility",
        lambda p, window: FeatureEngine.compute_volatility(
            FeatureEngine.compute_log_returns(p), window
        ),
        st.fixed_dictionaries({"window": _WINDOWS}),
    ),
    _Kernel(
        "price_momentum",
        lambda p, windows: FeatureEngine.compute_price_momentum(p, windows),
        st.fixed_dictionaries(
            {"windows": st.lists(st.integers(1, 10), min_size=1, max_size=3, unique=True)}
        ),
    ),
    _Kernel(
        "rsi",
        lambda p, period: FeatureEngine.compute_rsi(p, period),
        st.fixed_dictionaries({"period": st.integers(2, 14)}),
    ),
    _Kernel(
        "ema",
        lambda p, period: FeatureEngine.compute_ema(p, period),
        st.fixed_dictionaries({"period": st.integers(2, 30)}),
    ),
    _Kernel(
        "macd",
        lambda p: FeatureEngine.compute_macd(p),
        st.just({}),
    ),
    _Kernel(
        "zscore_rolling",
        lambda p, window: FeatureEngine.compute_zscore(p, window, mode="rolling"),
        st.fixed_dictionaries({"window": _WINDOWS}),
    ),
    _Kernel(
        "zscore_expanding",
        lambda p, window: FeatureEngine.compute_zscore(p, window, mode="expanding"),
        st.fixed_dictionaries({"window": _WINDOWS}),
    ),
    _Kernel(
        "normalize_rolling",
        lambda p, window, method: FeatureEngine.compute_normalize(
            p, window, method=method, mode="rolling"
        ),
        st.fixed_dictionaries(
            {"window": _WINDOWS, "method": st.sampled_from(["minmax", "robust"])}
        ),
    ),
    _Kernel(
        "volume_features",
        lambda p, window: FeatureEngine.compute_volume_features(p, window),
        st.fixed_dictionaries({"window": _WINDOWS}),
    ),
    _Kernel(
        # The aggregate the DL path actually calls. Every column of it, in one shot.
        "create_feature_matrix",
        lambda p: FeatureEngine.create_feature_matrix(
            p, volumes=None, include_volume_features=False
        )[0],
        st.just({}),
    ),
)


def _price_series(min_size: int = 55, max_size: int = 75) -> st.SearchStrategy:
    """Positive, finite prices. Positive because several kernels take logs or divide."""
    return st.lists(
        st.floats(min_value=1.0, max_value=500.0, allow_nan=False, allow_infinity=False),
        min_size=min_size,
        max_size=max_size,
    ).map(lambda values: np.asarray(values, dtype=float))


@pytest.mark.parametrize(
    "kernel", LEAK_SAFE_KERNELS, ids=[k.name for k in LEAK_SAFE_KERNELS]
)
@settings(max_examples=18, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(data=st.data())
def test_property_18_no_leak_safe_kernel_reads_a_bar_later_than_its_own_row(kernel, data):
    """Property 18 at the kernel level: future bars cannot reach an earlier row.

    Replace every bar after row `k` with different values and recompute. Rows `0..k` must
    be bit-identical - not close, identical, because a kernel that reads only bars `<= t`
    performs literally the same arithmetic on literally the same numbers. NaNs count as
    equal, since a warmup NaN in the same position is the same answer.

    This is the leak class no rule catches: a kernel that quietly indexes `t+1` publishes a
    `leakage_risk` of `NONE`, passes stage 10b, and poisons every model downstream.
    """
    prices = data.draw(_price_series())
    n = prices.size
    cut = data.draw(st.integers(min_value=0, max_value=n - 1))
    params = data.draw(kernel.params)

    altered = prices.copy()
    replacement = data.draw(
        st.lists(
            st.floats(min_value=1.0, max_value=500.0, allow_nan=False, allow_infinity=False),
            min_size=n - cut - 1,
            max_size=n - cut - 1,
        )
    )
    altered[cut + 1 :] = replacement

    before = _as_2d(kernel.apply(prices, **params))[: cut + 1]
    after = _as_2d(kernel.apply(altered, **params))[: cut + 1]

    np.testing.assert_array_equal(
        before,
        after,
        err_msg=(
            f"{kernel.name}{params} changed rows 0..{cut} when bars after {cut} were "
            f"replaced: it reads at least one bar later than its own row."
        ),
    )


@pytest.mark.parametrize(
    "kernel_name, apply",
    [
        ("zscore(mode='global')", lambda p: FeatureEngine.compute_zscore(p, 5, mode="global")),
        (
            "normalize(mode='global')",
            lambda p: FeatureEngine.compute_normalize(p, 5, method="minmax", mode="global"),
        ),
    ],
)
def test_the_global_statistic_modes_do_read_later_bars(kernel_name, apply):
    """The counterexample that justifies `GLOBAL_STATISTIC_LEAK`, not a bug report.

    Both modes are representable on purpose, so a graph can be *rejected* for them
    (Requirement 18.4). This test pins the reason: under the same experiment as the property
    above, an early row changes when a later bar changes. If either mode ever started
    passing that experiment the refusal would have lost its justification, and this test
    would be the thing that noticed.
    """
    prices = np.linspace(100.0, 140.0, 40)
    altered = prices.copy()
    altered[30:] += 500.0  # a later bar, changed

    before = apply(prices)[:31]
    after = apply(altered)[:31]

    assert not np.allclose(before, after, equal_nan=True), (
        f"{kernel_name} no longer depends on later bars - the GLOBAL_STATISTIC_LEAK "
        f"refusal would need re-justifying."
    )


# ══════════════════════════════════════════════════════════════════════════
#  PROPERTY 18, FORM 3: OVER THE SUPERVISED DATASET
#
#  Requirement 18.7: row i's features read bars at or before row i.
#  Requirement 18.8: row i's label reads bars strictly after row i.
#  The two halves must not swap, so both directions are asserted.
# ══════════════════════════════════════════════════════════════════════════


def _synthetic_matrix(prices: np.ndarray, warmup: int, index: np.ndarray):
    """A matrix whose row `t` holds only bar `t`'s own price, and its warmup NaNs.

    Deliberately trivial: this form is about `build_supervised_dataset`'s alignment, so the
    columns must not add a lookback of their own that could mask a misalignment.
    """
    own = prices.copy()
    doubled = prices * 2.0
    if warmup:
        own[:warmup] = np.nan
        doubled[:warmup] = np.nan
    return build_feature_matrix(
        index, ["own", "own_doubled"], [own, doubled], node_id="n_synthetic"
    )


@PROPERTY_SETTINGS
@given(
    bars=st.integers(min_value=12, max_value=60),
    warmup=st.integers(min_value=0, max_value=5),
    horizon=st.integers(min_value=1, max_value=5),
    row_pick=st.integers(min_value=0, max_value=999),
    shift_pick=st.integers(min_value=0, max_value=999),
)
def test_property_18_a_dataset_row_reads_its_own_bar_for_x_and_only_the_future_for_y(
    bars, warmup, horizon, row_pick, shift_pick
):
    """Property 18 at the dataset level, both directions.

    For a drawn row `i` and a drawn single bar `p`:

      * `X[i]` is the feature row at `i`'s own timestamp, and no perturbation of any bar
        changes it - features come from the matrix, at the row's own index (18.7);
      * `y[i]` changes when bar `i` or bar `i + horizon` changes, and does not change when
        any other bar changes. A label that read `i + horizon + 1`, or that read nothing
        forward at all, would fail one half or the other (18.8).

    Regression labels rather than classification: a class boundary would hide a real change
    inside the same class and turn the sensitivity half into a weaker claim.
    """
    if warmup + horizon + 1 >= bars:
        return  # no labelled row exists; that refusal is tests/test_ml_dataset.py's subject

    index = np.arange(bars, dtype="int64") * 60_000
    prices = 100.0 + np.arange(bars, dtype=float) * 0.5
    matrix = _synthetic_matrix(prices, warmup, index)

    dataset = build_supervised_dataset(
        matrix, prices, horizon, label_mode=LabelMode.REGRESSION
    )
    row = row_pick % dataset.n_rows
    own_position = dataset.row_range.start + row

    # 18.7: X row `row` is the matrix row at the same timestamp. Nothing forward.
    np.testing.assert_array_equal(dataset.X[row], matrix.values[own_position])
    assert dataset.index[row] == matrix.index[own_position]

    # Perturb exactly one bar and see which of y's rows notice.
    perturbed_at = shift_pick % bars
    perturbed_prices = prices.copy()
    perturbed_prices[perturbed_at] *= 1.5
    perturbed = build_supervised_dataset(
        matrix, perturbed_prices, horizon, label_mode=LabelMode.REGRESSION
    )

    # X never moves: it does not come from the price series at all.
    np.testing.assert_array_equal(perturbed.X, dataset.X)

    label_reads = {own_position, own_position + horizon}
    if perturbed_at in label_reads:
        assert perturbed.y[row] != dataset.y[row], (
            f"label for row {row} ignored bar {perturbed_at}, which is one of the two "
            f"bars it is defined on ({sorted(label_reads)})"
        )
    else:
        assert perturbed.y[row] == dataset.y[row], (
            f"label for row {row} changed when bar {perturbed_at} changed. It may read "
            f"only bar {own_position} and bar {own_position + horizon}."
        )
