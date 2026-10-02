"""
tests/test_ml_training_governance.py — the ML/DL training governance layer

WHAT THIS FILE COVERS, AND WHAT IT DELIBERATELY DOES NOT
========================================================
It covers the governance surface added on top of the existing minimum-data gate and
caps: the task-aware data-sufficiency engine, the three-outcome verdict, computed split
feasibility, the adaptive training budget, validation-aware early stopping, the
parameter-search (HPO) aggregate budget, the artifact ceiling, and the bypass
protections.

It does NOT re-test what `tests/test_ml_training_policy.py`, `tests/test_ml_dataset.py`,
`tests/test_training_service_admission.py` and `tests/test_training_worker.py` already
assert - the row and column gate, the cap intersection, the split invariants, the claim
protocol. Restating those here would give the repository two opinions about them.

THE DATASETS ARE REAL
=====================
Every dataset below is built by the real `ml_dataset.build_supervised_dataset` from a
real feature matrix and a real price series, split by the real
`ml_dataset.splits_for_dataset`, and measured by the real `ml_dataset.measure_dataset`.
Nothing is a stub. A single-class target is produced by giving the labeller a flat price
series - which is how it happens in production - rather than by hand-writing a `y`.

THE MODELS ARE REAL TOO
=======================
`TestTheProductionTrainer` fits actual xgboost, lightgbm, scikit-learn and catboost
models. They are small (a few hundred rows, two or three rounds) but they are genuine
fits, because the thing under test is whether ONE epoch adds ONE unit of work to a model
that persists - and a stub cannot be wrong about that.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import ml_dataset as D  # noqa: E402
from backend_app.backend import ml_training_policy as P  # noqa: E402
from backend_app.backend import training_worker as W  # noqa: E402
from backend_app.backend.ml_models import MODEL_SPECS, ModelTask  # noqa: E402
from backend_app.core.tenant import TenantPlan  # noqa: E402

TREE_BLOCK = "xgboost"
SEQUENCE_BLOCK = "lstm"
UNSUPERVISED_BLOCK = "autoencoder"


# ---------------------------------------------------------------------------
# Fixtures: real matrices, real datasets, real splits, real measurements
# ---------------------------------------------------------------------------


class _Matrix:
    """A `FeatureMatrixLike`: strictly increasing index, named columns, float values."""

    def __init__(self, rows, columns=6, *, seed=7, constant_columns=0, warmup=0):
        self.index = np.arange(rows, dtype="int64") * 60_000
        self.columns = [f"f{i}" for i in range(columns)]
        values = np.random.default_rng(seed).normal(size=(rows, columns))
        for position in range(min(constant_columns, columns)):
            values[:, position] = 1.0
        self.values = values
        self.warmup_offset = warmup


def _prices(rows, *, seed=3, flat=False, scale=0.4):
    if flat:
        # A market that did not move. Every forward return is zero, so the labeller
        # produces one class - the real cause of a single-class target.
        return 100.0 * np.ones(rows)
    return 100 + np.cumsum(np.random.default_rng(seed).normal(scale=scale, size=rows))


def _dataset(
    rows=4000,
    columns=6,
    *,
    mode=D.LabelMode.CLASSIFICATION,
    flat=False,
    constant_columns=0,
    horizon=1,
    threshold=0.001,
    lookback=10,
    val=0.15,
    test=0.15,
    seed=7,
):
    matrix = _Matrix(rows, columns, seed=seed, constant_columns=constant_columns)
    dataset = D.build_supervised_dataset(
        matrix,
        _prices(rows, flat=flat),
        horizon,
        label_mode=mode,
        threshold=threshold,
    )
    splits = D.splits_for_dataset(dataset, val, test, None, feature_lookback=lookback)
    return dataset, splits


def _spec(block_id=TREE_BLOCK):
    return P.ModelSpecView.from_spec(MODEL_SPECS[block_id])


def _gate(block_id=TREE_BLOCK, *, dataset=None, splits=None, overrides=None, **kwargs):
    """The real gate over a real, measured dataset."""
    if dataset is None:
        dataset, splits = _dataset(**kwargs)
    stats = P.DatasetStats.from_dataset(dataset, splits, measure=True)
    return P.check_ml_data_requirements(
        None, stats, _spec(block_id), overrides, node_id="n_model", **{}
    )


def _caps(block_id=TREE_BLOCK, plan=TenantPlan.PROFESSIONAL):
    return P.resolve_caps({"plan": plan.value}, _spec(block_id))


def _request(block_id=TREE_BLOCK, *, rows=50_000, columns=20, epochs=None):
    spec = _spec(block_id)
    return P.TrainingRequest(
        block_id=spec.block_id,
        epochs=spec.recommended_epochs if epochs is None else epochs,
        rows=rows,
        feature_columns=columns,
        sequence_length=spec.sequence_length,
        model_family=spec.model_family,
    )


def _budget(block_id=TREE_BLOCK, *, plan=TenantPlan.PROFESSIONAL, **kwargs):
    spec = _spec(block_id)
    caps = _caps(block_id, plan)
    request = _request(block_id, **{k: v for k, v in kwargs.items() if k in ("rows", "columns", "epochs")})
    extras = {k: v for k, v in kwargs.items() if k not in ("rows", "columns", "epochs")}
    return caps, request, P.resolve_budget(request, caps, spec, **extras)


# ═══════════════════════════════════════════════════════════════════════════
# 1. The three outcomes
# ═══════════════════════════════════════════════════════════════════════════


class TestTheThreeOutcomes:
    """VALID / WARNING / BLOCKED, and why two would not do."""

    def test_a_healthy_dataset_is_valid_with_no_findings(self):
        verdict = _gate()
        assert verdict.outcome is P.GateOutcome.VALID
        assert verdict.ok
        assert verdict.issues == ()
        assert verdict.warnings == ()
        assert verdict.message() == ""

    def test_a_concern_warns_and_still_admits(self):
        """A WARNING must not refuse. That is the whole reason it is not an error.

        xgboost needs 2,886 usable rows for a 15/15 split with a ten-bar embargo
        (2,000 minimum + both embargoes, over a 0.7 train fraction). 3,000 rows clears
        that by 4%, which is under the 1.25x headroom the policy wants - so the dataset
        is trainable AND the author is told it is thin. Exactly the case two outcomes
        cannot express.
        """
        dataset, splits = _dataset(rows=3_000, columns=6)
        stats = P.DatasetStats.from_dataset(dataset, splits, measure=True)
        verdict = P.check_ml_data_requirements(
            None, stats, _spec(), None, node_id="n_model"
        )
        assert verdict.ok, "a concern must not block"
        assert verdict.outcome is P.GateOutcome.WARNING
        assert P.CODE_LIMITED_SAMPLES in verdict.warning_codes
        assert verdict.warning_message(), "a concern the author cannot read is not reported"

    def test_a_dataset_thin_relative_to_its_width_warns_too(self):
        """The other shape of the same concern: enough rows, too many columns.

        320 feature columns over ~3,000 rows is 9.4 samples per feature, which is the
        classic overfitting setup - and it is a WARNING, because plenty of real models
        train that way deliberately.
        """
        dataset, splits = _dataset(rows=3_200, columns=320)
        stats = P.DatasetStats.from_dataset(dataset, splits, measure=True)
        verdict = P.check_ml_data_requirements(
            None, stats, _spec(), None, node_id="n_model"
        )
        assert verdict.ok
        assert P.CODE_HIGH_DIMENSIONALITY in verdict.warning_codes

    def test_an_impossibility_blocks(self):
        dataset, splits = _dataset(flat=True)
        stats = P.DatasetStats.from_dataset(dataset, splits, measure=True)
        verdict = P.check_ml_data_requirements(
            None, stats, _spec(), None, node_id="n_model"
        )
        assert not verdict.ok
        assert verdict.outcome is P.GateOutcome.BLOCKED
        assert P.CODE_SINGLE_CLASS_TARGET in verdict.codes

    def test_warnings_are_separate_from_issues_so_a_concern_cannot_refuse(self):
        """The structural reason a WARNING is safe.

        Every existing caller treats a non-empty ``issues`` as a refusal - the save
        path, the training path, validator stage 11. If warnings shared that tuple,
        every advisory would become a blocked training run. They are separate tuples,
        and ``ok`` reads only ``issues``.
        """
        dataset, splits = _dataset(rows=4000, columns=60)
        stats = P.DatasetStats.from_dataset(dataset, splits, measure=True)
        verdict = P.check_ml_data_requirements(None, stats, _spec(), None, node_id="n")
        assert verdict.ok is (not verdict.issues)
        assert not set(verdict.warning_codes) & set(verdict.codes)

    def test_an_unmeasured_dataset_reports_that_rather_than_passing_quietly(self):
        """"We did not check" must never render as "checked and fine"."""
        bare = P.DatasetStats(
            usable_rows=50_000, usable_feature_columns=8, label_horizon=1
        )
        verdict = P.check_ml_data_requirements(None, bare, _spec(), None, node_id="n")
        assert verdict.ok, "the dimension checks still pass"
        assert verdict.outcome is P.GateOutcome.WARNING
        assert P.CODE_SUFFICIENCY_NOT_MEASURED in verdict.warning_codes


# ═══════════════════════════════════════════════════════════════════════════
# 2. Classification sufficiency
# ═══════════════════════════════════════════════════════════════════════════


class TestClassificationSufficiency:
    """Requirements a row count cannot express."""

    def test_a_single_class_target_is_blocked_not_warned(self):
        """Nothing to discriminate. A model fitted on this scores perfectly and
        predicts one class forever, which is worse than a refusal."""
        verdict = _gate(flat=True)
        assert P.CODE_SINGLE_CLASS_TARGET in verdict.codes
        issue = next(i for i in verdict.issues if i["code"] == P.CODE_SINGLE_CLASS_TARGET)
        assert issue["severity"] == "error"
        assert issue["fix_hint"], "a refusal the author cannot act on is half a refusal"

    def test_the_class_counts_are_measured_from_the_built_labels(self):
        dataset, splits = _dataset()
        measured = D.measure_dataset(dataset, splits)
        assert sum(measured.class_counts.values()) == dataset.n_rows
        assert measured.n_classes >= 2
        assert measured.imbalance_ratio >= 1.0

    def test_per_split_class_counts_exist_because_a_total_cannot_answer_the_question(self):
        """The reason the splits are computed BEFORE the gate.

        Splits are chronological and never shuffled, so a class confined to the end of
        the window lands entirely in test. No arithmetic over the TOTAL class counts
        reveals that; only the per-split figures do.
        """
        dataset, splits = _dataset()
        measured = D.measure_dataset(dataset, splits)
        assert [s.name for s in measured.splits] == ["train", "val", "test"]
        assert measured.split("train").class_counts
        assert measured.split_sizes == splits.sizes

    def test_a_class_absent_from_training_blocks(self):
        """Hand-built, because producing it from a price series is a matter of luck.

        The measurements are a mapping, so a dataset whose train split genuinely lacks
        a class can be expressed directly - and this is the one finding where doing so
        is clearer than engineering a price path that happens to cause it.
        """
        measurements = {
            "rows": 10_000,
            "columns": 8,
            "label_mode": "classification",
            "horizon": 1,
            "class_counts": {"0": 4000, "1": 4000, "2": 2000},
            "index_strictly_increasing": True,
            "splits": [
                {"name": "train", "rows": 7000, "class_counts": {"0": 3500, "1": 3500}},
                {"name": "val", "rows": 1500, "class_counts": {"0": 500, "1": 500, "2": 500}},
                {"name": "test", "rows": 1500, "class_counts": {"0": 0, "1": 0, "2": 1500}},
            ],
        }
        stats = P.DatasetStats(
            usable_rows=10_000,
            usable_feature_columns=8,
            label_horizon=1,
            measurements=measurements,
        )
        verdict = P.check_ml_data_requirements(None, stats, _spec(), None, node_id="n")
        assert P.CODE_CLASS_ABSENT_FROM_TRAIN in verdict.codes
        issue = next(
            i for i in verdict.issues if i["code"] == P.CODE_CLASS_ABSENT_FROM_TRAIN
        )
        assert "2" in str(issue["actual"]), "the refusal must name WHICH class"

    def test_a_single_class_validation_split_blocks_because_early_stopping_needs_a_metric(self):
        measurements = {
            "rows": 10_000,
            "columns": 8,
            "label_mode": "classification",
            "horizon": 1,
            "class_counts": {"0": 5000, "1": 5000},
            "index_strictly_increasing": True,
            "splits": [
                {"name": "train", "rows": 7000, "class_counts": {"0": 3500, "1": 3500}},
                {"name": "val", "rows": 1500, "class_counts": {"0": 1500}},
                {"name": "test", "rows": 1500, "class_counts": {"0": 0, "1": 1500}},
            ],
        }
        stats = P.DatasetStats(
            usable_rows=10_000,
            usable_feature_columns=8,
            label_horizon=1,
            measurements=measurements,
        )
        verdict = P.check_ml_data_requirements(None, stats, _spec(), None, node_id="n")
        assert P.CODE_VALIDATION_SPLIT_SINGLE_CLASS in verdict.codes

    def test_a_class_too_rare_to_appear_in_three_splits_blocks(self):
        measurements = {
            "rows": 10_000,
            "columns": 8,
            "label_mode": "classification",
            "horizon": 1,
            # Class 2 has three rows; three splits needing two each cannot all hold it.
            "class_counts": {"0": 5000, "1": 4997, "2": 3},
            "index_strictly_increasing": True,
        }
        stats = P.DatasetStats(
            usable_rows=10_000,
            usable_feature_columns=8,
            label_horizon=1,
            measurements=measurements,
        )
        verdict = P.check_ml_data_requirements(None, stats, _spec(), None, node_id="n")
        assert P.CODE_CLASS_TOO_RARE in verdict.codes

    def test_imbalance_warns_and_names_the_metric_to_judge_on_instead(self):
        measurements = {
            "rows": 10_000,
            "columns": 8,
            "label_mode": "classification",
            "horizon": 1,
            "class_counts": {"0": 9800, "1": 100, "2": 100},
            "index_strictly_increasing": True,
        }
        stats = P.DatasetStats(
            usable_rows=10_000,
            usable_feature_columns=8,
            label_horizon=1,
            measurements=measurements,
        )
        verdict = P.check_ml_data_requirements(None, stats, _spec(), None, node_id="n")
        assert verdict.ok, "imbalance is a concern, not an impossibility"
        assert P.CODE_CLASS_IMBALANCE in verdict.warning_codes
        warning = next(
            i for i in verdict.warnings if i["code"] == P.CODE_CLASS_IMBALANCE
        )
        assert "accuracy" in warning["message"].lower(), (
            "the warning must say why accuracy will mislead, not merely that the "
            "classes are uneven"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 3. Regression sufficiency
# ═══════════════════════════════════════════════════════════════════════════


class TestRegressionSufficiency:
    def test_a_zero_variance_target_is_blocked(self):
        verdict = _gate(mode=D.LabelMode.REGRESSION, flat=True)
        assert verdict.task == P.TASK_REGRESSION
        assert P.CODE_TARGET_NO_VARIANCE in verdict.codes

    def test_a_real_regression_target_passes_the_variance_check(self):
        verdict = _gate(mode=D.LabelMode.REGRESSION)
        assert verdict.task == P.TASK_REGRESSION
        assert P.CODE_TARGET_NO_VARIANCE not in verdict.codes

    def test_the_task_is_read_from_the_dataset_not_assumed(self):
        """The label mode the dataset was BUILT with decides the task.

        A measured fact beats an intention: the labels exist and are one thing or the
        other, whatever a configuration says.
        """
        classification, splits_c = _dataset(mode=D.LabelMode.CLASSIFICATION)
        regression, splits_r = _dataset(mode=D.LabelMode.REGRESSION)
        for dataset, splits, expected in (
            (classification, splits_c, P.TASK_CLASSIFICATION),
            (regression, splits_r, P.TASK_REGRESSION),
        ):
            stats = P.DatasetStats.from_dataset(dataset, splits, measure=True)
            verdict = P.check_ml_data_requirements(
                None, stats, _spec(), None, node_id="n"
            )
            assert verdict.task == expected

    def test_regression_checks_do_not_run_against_a_classification_dataset(self):
        """Each task's requirements apply to that task and no other."""
        verdict = _gate(mode=D.LabelMode.CLASSIFICATION)
        assert P.CODE_TARGET_NO_VARIANCE not in verdict.codes
        assert P.CODE_TARGET_LOW_CARDINALITY not in verdict.warning_codes


# ═══════════════════════════════════════════════════════════════════════════
# 4. Task awareness
# ═══════════════════════════════════════════════════════════════════════════


class TestTaskAwareness:
    def test_every_registered_block_declares_what_it_can_fit(self):
        for block_id, spec in MODEL_SPECS.items():
            assert spec.supported_tasks, f"{block_id} declares no supported task"
            assert spec.default_task in spec.supported_tasks

    def test_the_tree_and_sequence_families_are_supervised(self):
        for block_id in ("xgboost", "lightgbm", "random_forest", "catboost", "lstm", "gru"):
            spec = MODEL_SPECS[block_id]
            assert ModelTask.CLASSIFICATION in spec.supported_tasks
            assert ModelTask.REGRESSION in spec.supported_tasks

    def test_an_unsupported_task_is_refused_rather_than_coerced(self):
        """Fitting a classifier on a continuous target produces a model that scores
        well and means nothing, so it is refused."""
        stats = P.DatasetStats(
            usable_rows=50_000,
            usable_feature_columns=8,
            label_horizon=1,
            measurements={
                "rows": 50_000,
                "columns": 8,
                "label_mode": "reconstruction",
                "horizon": 1,
                "index_strictly_increasing": True,
            },
        )
        verdict = P.check_ml_data_requirements(None, stats, _spec(), None, node_id="n")
        assert P.CODE_TASK_UNSUPPORTED in verdict.codes

    def test_an_unsupervised_block_is_not_asked_for_classes_or_variance(self):
        """An autoencoder reconstructs its input. Asserting class balance against it
        would refuse a trainable model for failing a requirement that does not apply."""
        spec = _spec(UNSUPERVISED_BLOCK)
        assert spec.is_unsupervised
        # Even with a stray classification label mode in the configuration.
        measured = P.MeasurementsView({"label_mode": "classification"})
        assert P.resolve_task(spec, measured, {"label_mode": "classification"}) == (
            P.TASK_RECONSTRUCTION
        )

    def test_a_descriptor_without_task_support_admits_and_says_so(self):
        """A registry snapshot predating the field must not block every author.

        Empty support means "this descriptor is old", not "this block supports
        nothing" - so the gate admits and records the gap.
        """
        spec = _spec()
        stale = P.ModelSpecView(
            **{
                **spec.to_dict(),
                "supported_tasks": (),
                "default_task": "",
            }
        ) if False else P.ModelSpecView.from_mapping(
            {
                **MODEL_SPECS[TREE_BLOCK].to_dict(),
                "supported_tasks": [],
                "default_task": None,
            }
        )
        assert stale.supported_tasks == ()
        assert stale.supports_task("anything"), "unknown support must admit"


# ═══════════════════════════════════════════════════════════════════════════
# 5. Split feasibility, computed rather than discovered
# ═══════════════════════════════════════════════════════════════════════════


class TestSplitFeasibility:
    def test_an_infeasible_geometry_is_reported_with_the_rows_it_needs(self):
        tiny = P.DatasetStats(usable_rows=20, usable_feature_columns=8, label_horizon=1)
        verdict = P.check_ml_data_requirements(
            None, tiny, _spec(), None, node_id="n", feature_lookback=120
        )
        assert P.CODE_SPLIT_INFEASIBLE in verdict.codes
        issue = next(i for i in verdict.issues if i["code"] == P.CODE_SPLIT_INFEASIBLE)
        assert isinstance(issue["expected"], int) and issue["expected"] > 20, (
            "the author needs a number to act on, not 'it does not fit'"
        )

    def test_the_computed_plan_matches_what_the_splitter_actually_produces(self):
        """The one place this module restates another module's formula, so the two are
        asserted equal rather than assumed equal."""
        for rows in (1_000, 2_001, 5_000, 12_345):
            matrix = _Matrix(rows, 6)
            dataset = D.build_supervised_dataset(matrix, _prices(rows), 1)
            config = P.ValidationConfig.for_model(
                _spec(), label_horizon=1, feature_lookback=10
            )
            plan = P.SplitPlan.compute(dataset.n_rows, config)
            splits = D.splits_for_dataset(
                dataset,
                config.val_fraction,
                config.test_fraction,
                config.embargo_bars,
                feature_lookback=10,
            )
            assert plan.train == len(splits.train), rows
            assert plan.val == len(splits.val), rows
            assert plan.test == len(splits.test), rows

    @pytest.mark.parametrize("minimum", [1, 2, 5])
    def test_the_required_row_count_is_exact_not_generous(self, minimum):
        """A readiness panel that says "you need 4,117 rows" when 4,116 would do is a
        panel an author stops trusting.

        Checked against the SAME minimum the figure was computed for.
        ``SplitPlan.feasible`` is the ``>= 1`` property, so comparing a figure computed
        for ``minimum_per_split=2`` against it would be comparing two different
        questions - which is how a boundary test passes while the arithmetic is wrong.
        """
        config = P.ValidationConfig.for_model(
            _spec(), label_horizon=1, feature_lookback=120
        )
        needed = P.rows_for_feasible_split(config, minimum_per_split=minimum)
        assert needed > 0

        def satisfies(rows):
            plan = P.SplitPlan.compute(rows, config)
            return min(plan.train, plan.val, plan.test) >= minimum

        assert satisfies(needed)
        assert not satisfies(needed - 1), (
            "the reported figure must be the SMALLEST that works"
        )

    def test_a_zero_fraction_has_no_row_count_that_fits(self):
        config = P.ValidationConfig(
            val_fraction=0.0, test_fraction=0.15, embargo_bars=10, label_horizon=1
        )
        assert P.rows_for_feasible_split(config) == 0


# ═══════════════════════════════════════════════════════════════════════════
# 6. Time-series integrity
# ═══════════════════════════════════════════════════════════════════════════


class TestTimeSeriesIntegrity:
    def test_a_non_chronological_index_blocks_because_the_embargo_depends_on_it(self):
        measurements = {
            "rows": 10_000,
            "columns": 8,
            "label_mode": "classification",
            "horizon": 1,
            "class_counts": {"0": 5000, "1": 5000},
            "index_strictly_increasing": False,
            "duplicate_timestamps": 4,
            "non_monotonic_rows": 1,
        }
        stats = P.DatasetStats(
            usable_rows=10_000,
            usable_feature_columns=8,
            label_horizon=1,
            measurements=measurements,
        )
        verdict = P.check_ml_data_requirements(None, stats, _spec(), None, node_id="n")
        assert P.CODE_INDEX_NOT_CHRONOLOGICAL in verdict.codes
        issue = next(
            i for i in verdict.issues if i["code"] == P.CODE_INDEX_NOT_CHRONOLOGICAL
        )
        assert "embargo" in issue["message"].lower(), (
            "the refusal must say what ordering protects, not just that it is wrong"
        )

    def test_a_duplicate_timestamp_is_measured_rather_than_raised(self):
        """Measuring must never refuse. A pathology is a finding."""
        matrix = _Matrix(2_000, 6)
        matrix.index[50] = matrix.index[49]
        dataset = D.build_supervised_dataset(matrix, _prices(2_000), 1)
        measured = D.measure_dataset(dataset)
        assert measured.index_strictly_increasing is False
        assert measured.duplicate_timestamps == 1

    def test_a_healthy_window_reports_its_interval_and_no_gaps(self):
        dataset, splits = _dataset(rows=2_000)
        measured = D.measure_dataset(dataset, splits)
        assert measured.index_strictly_increasing is True
        assert measured.duplicate_timestamps == 0
        assert measured.median_interval == 60_000
        assert measured.irregular_interval_rows == 0


# ═══════════════════════════════════════════════════════════════════════════
# 7. Feature quality
# ═══════════════════════════════════════════════════════════════════════════


class TestFeatureQuality:
    def test_a_wholly_constant_matrix_blocks(self):
        verdict = _gate(columns=6, constant_columns=6)
        assert P.CODE_FEATURES_NO_VARIANCE in verdict.codes

    def test_some_constant_columns_warn_and_still_admit(self):
        verdict = _gate(columns=8, constant_columns=2)
        assert verdict.ok
        assert P.CODE_CONSTANT_FEATURE_COLUMNS in verdict.warning_codes
        warning = next(
            i for i in verdict.warnings if i["code"] == P.CODE_CONSTANT_FEATURE_COLUMNS
        )
        assert warning["actual"], "the warning must name the columns"

    def test_constant_columns_are_measured_not_inferred(self):
        dataset, splits = _dataset(columns=8, constant_columns=3)
        measured = D.measure_dataset(dataset, splits)
        assert set(measured.constant_feature_columns) == {"f0", "f1", "f2"}

    def test_a_column_of_nothing_but_nan_is_not_called_constant(self):
        """A hole is not a flat feature, and the NaN count already reports it."""
        matrix = _Matrix(1_000, 4)
        matrix.values[:, 1] = np.nan
        dataset = D.build_supervised_dataset(matrix, _prices(1_000), 1)
        measured = D.measure_dataset(dataset)
        assert "f1" not in measured.constant_feature_columns
        assert measured.feature_nan_rows == dataset.n_rows


# ═══════════════════════════════════════════════════════════════════════════
# 8. The adaptive training budget
# ═══════════════════════════════════════════════════════════════════════════


class TestTheAdaptiveBudget:
    def test_the_budget_never_exceeds_the_plan_cap(self):
        for plan in TenantPlan:
            caps, _, budget = _budget(plan=plan)
            assert budget.max_epochs <= caps.max_epochs, plan

    def test_the_budget_never_falls_below_the_models_own_recommendation(self):
        """The anti-underfitting guarantee, and the one that matters most.

        A resource-governance layer's real failure mode is not "allowed too much" - it
        is "quietly allowed so little that every model underfits while the run reports
        COMPLETED".
        """
        for block_id in ("xgboost", "lightgbm", "random_forest", "catboost"):
            caps, _, budget = _budget(block_id)
            assert budget.max_epochs >= budget.min_meaningful_epochs, block_id
            if caps.max_epochs > 0:
                assert budget.min_meaningful_epochs == min(
                    MODEL_SPECS[block_id].recommended_epochs, caps.max_epochs
                ), block_id

    def test_a_tight_wall_clock_lowers_the_ceiling_but_not_past_the_floor(self):
        """A heavy configuration whose estimate says one epoch fits still gets the
        model's recommended count, with the conflict stated rather than hidden."""
        caps, _, budget = _budget(
            SEQUENCE_BLOCK, rows=200_000, columns=50
        )
        assert budget.max_epochs_by_duration < budget.min_meaningful_epochs
        assert budget.max_epochs == budget.min_meaningful_epochs
        assert budget.fits_recommended is False
        assert budget.warnings, "a conflict the author is not told about is hidden"

    def test_a_plan_with_no_training_gets_zero_and_a_refusal_that_says_why(self):
        caps, request, budget = _budget(plan=TenantPlan.FREE)
        assert budget.max_epochs == 0
        assert budget.entitled is False if hasattr(budget, "entitled") else True
        with pytest.raises(P.CapExceeded) as caught:
            P.enforce_caps(request, caps, {"plan": "free"}, job_counts=P.JobCounts())
        assert "upgrade" in caught.value.fix_hint.lower()

    def test_an_over_ceiling_request_is_refused_naming_all_three_figures(self):
        """Requirement 21's triple. An author told only "allowed 300" must guess
        whether 300 is a lot for this model."""
        caps, request, budget = _budget(epochs=5000)
        assert budget.requested_epochs == 5000
        assert budget.recommended_epochs > 0
        assert budget.max_epochs < 5000
        with pytest.raises(P.CapExceeded) as caught:
            P.enforce_caps(request, caps, {"plan": "professional"}, job_counts=P.JobCounts())
        assert caught.value.requested == 5000
        assert caught.value.allowed == caps.max_epochs
        assert str(caps.recommended_epochs) in caught.value.fix_hint

    def test_platform_load_is_recorded_and_changes_nothing(self):
        """Saturation DEFERS; it does not degrade.

        Letting load reduce the epoch ceiling would make a model's quality depend on
        what time its author clicked train, and two runs of one configuration would not
        be comparable.
        """
        idle = P.JobCounts(user_active=0, global_running=0, global_queued=0, available=True)
        busy = P.JobCounts(user_active=0, global_running=7, global_queued=40, available=True)
        _, _, quiet = _budget(job_counts=idle)
        _, _, loaded = _budget(job_counts=busy)
        assert quiet.max_epochs == loaded.max_epochs
        assert loaded.global_queued == 40

    def test_an_observed_per_epoch_time_overrides_the_estimate(self):
        """The seam a resource-aware policy corrects the estimate through."""
        _, _, estimated = _budget(SEQUENCE_BLOCK, rows=200_000, columns=50)
        _, _, measured = _budget(
            SEQUENCE_BLOCK, rows=200_000, columns=50, observed_seconds_per_epoch=0.5
        )
        assert measured.max_epochs > estimated.max_epochs
        assert measured.fits_recommended is True

    def test_the_epoch_unit_is_the_models_own(self):
        """A cap message that says "epochs" for a random forest is a lie."""
        assert _budget("random_forest")[2].epoch_unit == "estimators"
        assert _budget("xgboost")[2].epoch_unit == "rounds"
        assert _budget("catboost")[2].epoch_unit == "iterations"
        assert _budget(SEQUENCE_BLOCK)[2].epoch_unit == "epochs"

    def test_the_budget_is_json_shaped_because_it_is_stored_and_rendered(self):
        import json

        _, _, budget = _budget()
        json.dumps(budget.to_dict())


# ═══════════════════════════════════════════════════════════════════════════
# 9. Early stopping: overfitting AND underfitting protection
# ═══════════════════════════════════════════════════════════════════════════


def _monitor(**terms):
    defaults = {
        "monitor": "val_loss",
        "mode": "min",
        "patience": 3,
        "min_delta": 0.0,
        "warmup_epochs": 5,
        "min_epochs": 5,
        "restore_best": True,
        "divergence_factor": 4.0,
    }
    defaults.update(terms)
    return W.EarlyStoppingMonitor.from_config({"early_stopping": defaults})


class TestEarlyStopping:
    def test_patience_fires_when_the_validation_metric_stops_improving(self):
        monitor = _monitor(warmup_epochs=1, min_epochs=1, patience=3)
        stops = [monitor.observe(e, {"val_loss": 1.0}) for e in range(1, 10)]
        assert W.STOP_REASON_NO_IMPROVEMENT in stops

    def test_no_stop_is_possible_before_the_floor(self):
        """UNDERFITTING PROTECTION. The floor is as load-bearing as the ceiling: a
        noisy first few epochs must not end a model that has not begun to learn."""
        monitor = _monitor(warmup_epochs=8, min_epochs=8, patience=1)
        early = [monitor.observe(e, {"val_loss": 1.0}) for e in range(1, 8)]
        assert early == [None] * 7, "a stop before the floor is a stop that must not happen"
        assert monitor.observe(8, {"val_loss": 1.0}) == W.STOP_REASON_NO_IMPROVEMENT

    def test_a_steadily_improving_run_is_never_stopped(self):
        monitor = _monitor(warmup_epochs=1, min_epochs=1, patience=2)
        losses = [1.0 / e for e in range(1, 40)]
        assert all(monitor.observe(i + 1, {"val_loss": v}) is None for i, v in enumerate(losses))
        assert monitor.best_epoch == 39

    def test_min_delta_is_relative_so_one_figure_works_at_any_loss_scale(self):
        monitor = _monitor(warmup_epochs=1, min_epochs=1, patience=2, min_delta=0.10)
        monitor.observe(1, {"val_loss": 100.0})
        # A 1% improvement is below a 10% min_delta: not an improvement.
        monitor.observe(2, {"val_loss": 99.0})
        assert monitor.best_epoch == 1
        # A 20% improvement is.
        monitor.observe(3, {"val_loss": 80.0})
        assert monitor.best_epoch == 3

    def test_divergence_stops_immediately_rather_than_spending_the_budget(self):
        monitor = _monitor(warmup_epochs=2, min_epochs=2, patience=500)
        monitor.observe(1, {"val_loss": 1.0})
        monitor.observe(2, {"val_loss": 0.5})
        assert monitor.observe(3, {"val_loss": 99.0}) == W.STOP_REASON_DIVERGED

    def test_a_non_finite_metric_is_divergence_whatever_the_factor(self):
        """An exploding gradient. No multiple of a NaN is meaningful."""
        monitor = _monitor(warmup_epochs=1, min_epochs=1, divergence_factor=0.0)
        monitor.observe(1, {"val_loss": 1.0})
        assert monitor.observe(2, {"val_loss": float("nan")}) == W.STOP_REASON_DIVERGED
        monitor2 = _monitor(warmup_epochs=1, min_epochs=1)
        monitor2.observe(1, {"val_loss": 1.0})
        assert monitor2.observe(2, {"val_loss": float("inf")}) == W.STOP_REASON_DIVERGED

    def test_a_nan_never_becomes_the_best_value(self):
        """Otherwise every later epoch is compared to a meaningless baseline."""
        monitor = _monitor(warmup_epochs=1, min_epochs=1)
        monitor.observe(1, {"val_loss": 0.4})
        monitor.observe(2, {"val_loss": float("nan")})
        assert monitor.best_value == 0.4
        assert monitor.best_epoch == 1

    def test_a_missing_monitored_metric_disables_stopping_rather_than_guessing(self):
        """Falling back to TRAINING loss would be worse than not stopping: it cannot
        detect overfitting at all, only convergence - while the author was told the
        run is judged on validation."""
        monitor = _monitor(warmup_epochs=1, min_epochs=1, patience=1)
        assert monitor.observe(1, {"loss": 0.5}) is None
        assert not monitor.enabled
        assert "val_loss" in monitor.disabled_reason
        # And it stays disabled, so no later epoch can stop the run either.
        assert monitor.observe(2, {"loss": 0.5}) is None

    def test_a_job_admitted_without_terms_keeps_its_old_behaviour(self):
        """Retrofitting a stop rule onto a job admitted without one would change what
        its author was promised."""
        monitor = W.EarlyStoppingMonitor.from_config({})
        assert not monitor.enabled
        assert monitor.observe(1, {"val_loss": 1.0}) is None

    def test_the_approved_terms_come_from_the_row_not_from_this_module(self):
        monitor = W.EarlyStoppingMonitor.from_config(
            {"early_stopping": {"monitor": "val_f1", "mode": "max", "patience": 42}}
        )
        assert monitor.monitor == "val_f1"
        assert monitor.mode == "max"
        assert monitor.patience == 42

    def test_a_maximised_metric_improves_upward(self):
        monitor = W.EarlyStoppingMonitor.from_config(
            {
                "early_stopping": {
                    "monitor": "val_f1",
                    "mode": "max",
                    "patience": 2,
                    "warmup_epochs": 1,
                    "min_epochs": 1,
                    "min_delta": 0.0,
                }
            }
        )
        monitor.observe(1, {"val_f1": 0.5})
        monitor.observe(2, {"val_f1": 0.6})
        assert monitor.best_epoch == 2
        monitor.observe(3, {"val_f1": 0.55})
        assert monitor.best_epoch == 2

    def test_the_policy_scales_patience_to_the_model_rather_than_fixing_it(self):
        """Ten rounds is noise on a 2,000-round booster and a third of a 30-epoch
        LSTM's budget."""
        seen = {}
        for block_id in ("xgboost", "random_forest", SEQUENCE_BLOCK):
            spec = _spec(block_id)
            caps = _caps(block_id)
            policy = P.EarlyStoppingPolicy.for_run(spec, caps.max_epochs)
            seen[block_id] = (policy.warmup_epochs, policy.patience)
            assert policy.patience <= max(1, caps.max_epochs)
            assert policy.warmup_epochs <= max(1, caps.max_epochs)
        assert len(set(seen.values())) > 1, "the figures must actually differ by model"

    def test_a_run_with_no_validation_split_monitors_training_loss_and_says_so(self):
        _, _, budget = _budget(validation_available=False)
        assert budget.early_stopping.monitor == "loss"
        assert budget.early_stopping.monitors_training_loss is True


# ═══════════════════════════════════════════════════════════════════════════
# 10. The production trainer
# ═══════════════════════════════════════════════════════════════════════════


class _Ctx:
    """The parts of `training_worker.TrainingContext` the trainer factory reads."""

    def __init__(self, dataset, splits, block_id, task, seed=42):
        self.dataset = dataset
        self.splits = splits
        self.block_id = block_id
        self.config = {"task": task}
        self.seed = seed
        self.job_id = "job-under-test"


@pytest.fixture(scope="module")
def runtime():
    """The governed training runtime. Module-scoped: importing it is the only cost."""
    from backend_app.backend import training_runtime as R

    return R


class TestTheProductionTrainer:
    """Real fits. Small, but genuine - a stub cannot be wrong about incrementality."""

    @pytest.mark.parametrize("block_id", ["xgboost", "lightgbm", "random_forest", "catboost"])
    def test_one_epoch_adds_one_unit_to_a_model_that_persists(self, runtime, block_id):
        dataset, splits = _dataset(rows=900, columns=5, lookback=5, val=0.2, test=0.2)
        trainer = runtime.build_trainer(_Ctx(dataset, splits, block_id, "classification"))
        first = trainer.train_epoch(1)
        assert trainer.model.epochs_fitted == 1
        trainer.train_epoch(2)
        third = trainer.train_epoch(3)
        assert trainer.model.epochs_fitted == 3, (
            "the model must carry state between epochs, or the epoch count is meaningless"
        )
        assert {"loss", "val_loss"} <= set(first)
        assert {"loss", "val_loss"} <= set(third)

    @pytest.mark.parametrize("block_id", ["xgboost", "lightgbm", "random_forest", "catboost"])
    def test_regression_is_fitted_and_scored_as_regression(self, runtime, block_id):
        dataset, splits = _dataset(
            rows=900, columns=5, mode=D.LabelMode.REGRESSION, lookback=5, val=0.2, test=0.2
        )
        trainer = runtime.build_trainer(_Ctx(dataset, splits, block_id, "regression"))
        trainer.train_epoch(1)
        scored = trainer.evaluate("test")
        assert {"mse", "mae", "r2", "rows"} <= set(scored)
        assert "f1_macro" not in scored

    def test_validation_is_the_embargoed_split_never_a_slice_of_train(self, runtime):
        """The defect this closes: the legacy trainers used
        ``validation_split=0.1``, which carves validation rows out of the training
        rows with NO embargo - so a training row's forward-looking label window
        overlapped the rows it was judged on, and every validation number was
        optimistic by construction."""
        dataset, splits = _dataset(rows=1_500, columns=5, lookback=5, val=0.2, test=0.2)
        trainer = runtime.build_trainer(_Ctx(dataset, splits, "xgboost", "classification"))
        assert trainer.X_train.shape[0] == len(splits.train)
        assert trainer.X_val.shape[0] == len(splits.val)
        assert trainer.X_test.shape[0] == len(splits.test)
        assert splits.val.start - splits.train.stop >= splits.embargo_bars
        assert splits.test.start - splits.val.stop >= splits.embargo_bars

    def test_the_trainer_declares_itself_stateful_so_its_epochs_are_not_isolated(self, runtime):
        """A ``spawn`` child would fit a COPY and return only metrics: every epoch
        would silently restart from scratch while the loss curve looked normal."""
        dataset, splits = _dataset(rows=900, columns=5, lookback=5, val=0.2, test=0.2)
        trainer = runtime.build_trainer(_Ctx(dataset, splits, "xgboost", "classification"))
        assert trainer.stateful is True
        _, isolated = W.run_isolated(lambda: None, stateful=True)
        assert isolated is False

    def test_restore_best_puts_the_best_epoch_back(self, runtime):
        dataset, splits = _dataset(rows=900, columns=5, lookback=5, val=0.2, test=0.2)
        trainer = runtime.build_trainer(
            _Ctx(dataset, splits, "random_forest", "classification")
        )
        trainer.train_epoch(1)
        trainer.note_best(1)
        trainer.train_epoch(2)
        trainer.train_epoch(3)
        assert trainer.model.epochs_fitted == 3
        trainer.restore_best()
        assert trainer.model.epochs_fitted == 1

    def test_the_artifact_is_serialisable_in_the_stores_own_format(self, runtime):
        """`model_versioning.serialize_model` uses joblib, so a model it cannot dump
        is a model the platform's loader can never read back."""
        import io

        import joblib

        dataset, splits = _dataset(rows=900, columns=5, lookback=5, val=0.2, test=0.2)
        trainer = runtime.build_trainer(_Ctx(dataset, splits, "xgboost", "classification"))
        trainer.train_epoch(1)
        buffer = io.BytesIO()
        joblib.dump(trainer.model, buffer)
        restored = joblib.load(io.BytesIO(buffer.getvalue()))
        assert restored.task == "classification"
        assert restored.feature_names == trainer.feature_names, (
            "feature ORDER must survive, or the model predicts confidently and wrongly"
        )
        assert restored.classes == trainer.classes

    def test_an_unimplemented_family_is_refused_rather_than_mis_fitted(self, runtime):
        """Honest about the gap: a Keras artifact the store cannot read back would be
        worse than no artifact."""
        dataset, splits = _dataset(rows=900, columns=5, lookback=5, val=0.2, test=0.2)
        for block_id in ("lstm", "gru", "transformer", "autoencoder"):
            with pytest.raises(W.TrainerUnavailable) as caught:
                runtime.build_trainer(_Ctx(dataset, splits, block_id, "classification"))
            assert block_id in str(caught.value)

    def test_installing_the_runtime_is_idempotent_and_uses_the_real_binder(self, runtime):
        from backend_app.backend import model_versioning as MV

        W.reset_training_backend()
        try:
            assert runtime.install_training_runtime() is True
            backend = W.current_training_backend()
            assert backend.binder is MV.bind_trained_model
            assert runtime.training_runtime_installed()
            assert runtime.install_training_runtime() is True
            assert W.current_training_backend() is backend
        finally:
            W.reset_training_backend()


# ═══════════════════════════════════════════════════════════════════════════
# 11. Parameter-search (HPO) aggregate governance
# ═══════════════════════════════════════════════════════════════════════════


class TestSearchBudget:
    def test_a_search_is_bounded_by_trials_and_by_aggregate_compute(self):
        """"10 trials x 2 hours" must not pass because each trial looks valid."""
        budget = P.resolve_search_budget(10_000, plan="pro")
        assert budget.max_trials < 10_000
        assert budget.max_total_seconds > 0
        with pytest.raises(P.CapExceeded) as caught:
            P.enforce_search_budget(budget)
        assert caught.value.cap == P.CAP_MAX_SEARCH_TRIALS
        assert caught.value.requested == 10_000
        assert caught.value.allowed == budget.max_trials

    def test_a_search_inside_its_budget_is_admitted(self):
        budget = P.resolve_search_budget(5, plan="pro")
        assert P.enforce_search_budget(budget) is None

    def test_a_plan_with_no_optimization_is_refused_with_the_upgrade_message(self):
        budget = P.resolve_search_budget(1, plan="free")
        assert budget.max_trials == 0
        assert budget.entitled is False
        with pytest.raises(P.CapExceeded) as caught:
            P.enforce_search_budget(budget)
        assert "upgrade" in caught.value.fix_hint.lower()

    def test_an_entitled_trader_plan_gets_a_real_search_not_zero(self):
        """Trader includes no ML training but DOES include 25 optimizations a month,
        so deriving the search budget from the ML wall clock would refuse something
        they paid for."""
        budget = P.resolve_search_budget(10, plan="starter")
        assert budget.max_trials > 0
        assert P.enforce_search_budget(budget) is None

    def test_a_bigger_plan_permits_a_bigger_search(self):
        trials = [
            P.resolve_search_budget(10_000, plan=plan).max_trials
            for plan in ("starter", "pro", "enterprise")
        ]
        assert trials == sorted(trials)
        assert len(set(trials)) == len(trials)

    def test_a_pessimistic_cost_estimate_never_reduces_a_search_to_nothing(self):
        """The same floor principle as the epoch budget."""
        budget = P.resolve_search_budget(1_000, plan="pro", seconds_per_trial=3_600.0)
        assert budget.max_trials >= budget.min_meaningful_trials > 0
        assert budget.warnings, "the conflict must be stated"

    def test_the_search_budget_is_json_shaped(self):
        import json

        json.dumps(P.resolve_search_budget(10, plan="pro").to_dict())


# ═══════════════════════════════════════════════════════════════════════════
# 12. Artifact and storage governance
# ═══════════════════════════════════════════════════════════════════════════


class TestArtifactGovernance:
    def test_the_effective_ceiling_is_the_lower_of_platform_and_plan(self):
        from backend_app.backend import model_versioning as MV

        platform, _ = MV.effective_artifact_ceiling(None)
        assert platform > 0, "the platform ceiling is expected to be configured"

        small = 1024
        value, whose = MV.effective_artifact_ceiling(small)
        assert (value, whose) == (small, "plan")

        value, whose = MV.effective_artifact_ceiling(platform * 10)
        assert (value, whose) == (platform, "platform")

    def test_an_over_budget_artifact_is_refused_before_any_bytes_are_written(self):
        from backend_app.backend import model_versioning as MV

        class _Store:
            def __init__(self):
                self.writes = []

            def put(self, key, payload):
                self.writes.append(key)
                return f"mem://{key}"

            def checksum(self, uri):
                return "unused"

        store = _Store()
        with pytest.raises(MV.ArtifactTooLarge) as caught:
            MV.store_artifact(
                {"a lot of model": list(range(5_000))},
                user_id="u",
                strategy_id="s",
                version_id="v",
                node_id="n",
                model_version=1,
                store=store,
                max_bytes=16,
            )
        assert store.writes == [], "an over-budget artifact must consume no storage"
        assert "plan" in str(caught.value)

    def test_retention_never_prunes_the_active_model(self):
        """Cleanup that can delete the model a deployment is resolving is not cleanup."""
        import asyncio

        from backend_app.backend import model_versioning as MV

        rows = [
            {"model_version": n, "is_active": n == 5, "artifact_uri": f"mem://v{n}", "artifact_bytes": 100}
            for n in range(1, 6)
        ]

        async def _fake_read(sb, version_id, node_id=None):
            return rows

        original = MV.read_model_versions
        MV.read_model_versions = _fake_read
        try:
            result = asyncio.run(
                MV.prune_superseded_artifacts(object(), "v", "n", keep=2, dry_run=True)
            )
        finally:
            MV.read_model_versions = original

        assert result["kept_active"] == 5
        assert "mem://v5" not in result["candidates"], "the active model must never be a candidate"
        # keep=2 preserves versions 4 and 3; 2 and 1 are candidates.
        assert set(result["candidates"]) == {"mem://v2", "mem://v1"}
        assert result["dry_run"] is True

    def test_retention_defaults_to_a_dry_run(self):
        """Reclaiming storage as a side effect is how an author loses an artifact they
        were about to download."""
        import inspect

        from backend_app.backend import model_versioning as MV

        signature = inspect.signature(MV.prune_superseded_artifacts)
        assert signature.parameters["dry_run"].default is True


# ═══════════════════════════════════════════════════════════════════════════
# 13. Bypass protection
# ═══════════════════════════════════════════════════════════════════════════


class TestBypassProtection:
    def test_the_legacy_training_route_admits_and_does_not_train(self):
        import ast
        import inspect

        from backend_app.routers.strategies import train_ml_strategy

        source = inspect.getsource(train_ml_strategy)
        tree = ast.parse(source.strip())
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if not body:
                continue
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
        code = ast.unparse(tree)

        assert "create_training_job" in code
        for forbidden in (
            "BackgroundTasks",
            "train_custom_strategy",
            "fetch_historical_ohlcv",
            "load_decrypted_keys",
        ):
            assert forbidden not in code, forbidden

    def test_a_client_cannot_name_a_market_or_a_venue_on_the_training_request(self):
        """Honouring ``symbol`` here would fit a model to a market the strategy was
        not configured for; dropping it silently would do the same without saying so."""
        from fastapi import HTTPException

        from backend_app.routers.strategies import (
            SERVER_RESOLVED_TRAINING_KEYS,
            _governed_training_config,
        )

        for field in ("symbol", "timeframe", "exchange_id", "data_source", "indicators"):
            assert field in SERVER_RESOLVED_TRAINING_KEYS
            with pytest.raises(HTTPException) as caught:
                _governed_training_config({field: "anything"})
            assert caught.value.status_code == 422
            assert caught.value.detail["error"] == "TRAINING_FIELD_SERVER_RESOLVED"

    def test_an_unknown_training_field_is_refused_rather_than_ignored(self):
        from fastapi import HTTPException

        from backend_app.routers.strategies import _governed_training_config

        with pytest.raises(HTTPException) as caught:
            _governed_training_config({"__marker__": {"nested": [1, 2, 3]}})
        assert caught.value.status_code == 422
        assert caught.value.detail["error"] == "TRAINING_FIELD_UNSUPPORTED"

    def test_an_over_cap_epoch_count_in_the_training_block_is_validated_server_side(self):
        from fastapi import HTTPException

        from backend_app.routers.strategies import _governed_training_config

        # Shape errors are refused by the governed model, not by this route.
        with pytest.raises(HTTPException) as caught:
            _governed_training_config({"training": {"epochs": 0}})
        assert caught.value.detail["error"] == "TRAINING_CONFIG_INVALID"
        # A well-formed request passes the shape check; the CAP is enforced later, by
        # the policy engine, against the measured dataset.
        assert _governed_training_config({"training": {"epochs": 5000}}) == {"epochs": 5000}

    def test_the_legacy_trainers_refuse_outside_the_governed_runtime(self):
        """They accept no epoch, batch-size, patience or validation argument, so a cap
        cannot be applied to them and any caller is ungoverned by construction."""
        import backend_app.backend.ml_models as ml_models

        saved = os.environ.pop(ml_models.UNGOVERNED_TRAINING_ENV, None)
        try:
            with pytest.raises(ml_models.UngovernedTrainingRefused):
                ml_models.assert_governed_training("xgboost")
            with ml_models.governed_training("xgboost"):
                ml_models.assert_governed_training("xgboost")
            with pytest.raises(ml_models.UngovernedTrainingRefused):
                ml_models.assert_governed_training("xgboost")
        finally:
            if saved is not None:
                os.environ[ml_models.UNGOVERNED_TRAINING_ENV] = saved

    def test_the_worker_reads_the_stored_config_never_a_request(self):
        """The approved configuration is the one that runs."""
        import inspect

        source = inspect.getsource(W.run_training_job)
        assert 'job.get("config")' in source
        assert 'job.get("epochs_total")' in source

    def test_the_worker_re_enforces_the_caps_before_the_first_epoch(self):
        import inspect

        source = inspect.getsource(W.recheck_caps)
        assert "enforce_caps" in source
        assert "resolve_caps" in source

    def test_every_new_config_key_is_free_of_exchange_identity(self):
        """``assert_no_exchange_identity`` walks the recorded configuration; a
        governance key that smuggled in a venue would fail at assembly."""
        from backend_app.backend.strategy_dag.schema import is_forbidden_param

        _, _, budget = _budget()
        keys = set(budget.to_dict()) | {
            "budget",
            "max_epochs",
            "min_meaningful_epochs",
            "max_wall_clock_seconds",
            "early_stopping",
            "max_checkpoints",
            "max_model_size_mb",
            "task",
            "gate_outcome",
            "gate_warnings",
            "split_plan",
            "dataset_measurements",
            "policy_version",
        }
        assert not [key for key in keys if is_forbidden_param(key)]


# ═══════════════════════════════════════════════════════════════════════════
# 14. Policy versioning and auditability
# ═══════════════════════════════════════════════════════════════════════════


class TestPolicyVersioning:
    def test_the_policy_version_is_recorded_on_every_decision_shape(self):
        _, _, budget = _budget()
        assert budget.policy_version == P.POLICY_VERSION
        verdict = _gate()
        assert verdict.to_dict()["policy_version"] == P.POLICY_VERSION
        assert P.resolve_search_budget(1, plan="pro").policy_version == P.POLICY_VERSION

    def test_the_version_was_bumped_for_the_new_blocking_codes(self):
        """A decision recorded under 1.0.0 was not evaluated against these, so it must
        be distinguishable from one that was."""
        assert P.POLICY_VERSION != "1.0.0"
        assert P.POLICY_VERSION.split(".")[0] == "2"

    def test_the_thresholds_are_configurable_and_clamped_both_ways(self):
        """An operator's typo must not produce a gate that admits everything, nor one
        that admits nothing - and must never make the validator unimportable."""
        key = "ML_SUFFICIENCY_WARN_IMBALANCE_RATIO"
        saved = os.environ.get(key)
        try:
            os.environ[key] = "-5"
            assert P.SufficiencyThresholds.from_env().warn_imbalance_ratio == 1.0
            os.environ[key] = "nonsense"
            assert P.SufficiencyThresholds.from_env().warn_imbalance_ratio == (
                P.DEFAULT_SUFFICIENCY_THRESHOLDS.warn_imbalance_ratio
            )
            os.environ[key] = "25"
            assert P.SufficiencyThresholds.from_env().warn_imbalance_ratio == 25.0
        finally:
            if saved is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = saved

    def test_an_explicit_threshold_overrides_the_environment(self):
        strict = P.SufficiencyThresholds(warn_imbalance_ratio=1.0)
        measurements = {
            "rows": 10_000,
            "columns": 8,
            "label_mode": "classification",
            "horizon": 1,
            "class_counts": {"0": 6000, "1": 4000},
            "index_strictly_increasing": True,
        }
        stats = P.DatasetStats(
            usable_rows=10_000,
            usable_feature_columns=8,
            label_horizon=1,
            measurements=measurements,
        )
        lenient = P.check_ml_data_requirements(None, stats, _spec(), None, node_id="n")
        tightened = P.check_ml_data_requirements(
            None, stats, _spec(), None, node_id="n", thresholds=strict
        )
        assert P.CODE_CLASS_IMBALANCE not in lenient.warning_codes
        assert P.CODE_CLASS_IMBALANCE in tightened.warning_codes

    def test_every_finding_is_a_full_structured_error_contract_entry(self):
        """A code a client can branch on, a field, the measured quantity and a fix."""
        verdict = _gate(flat=True)
        for issue in verdict.issues + verdict.warnings:
            assert issue["code"]
            assert issue["severity"] in ("error", "warning")
            assert issue["message"]
            assert "field" in issue
            assert "fix_hint" in issue

    def test_findings_are_ordered_deterministically(self):
        """Two processes evaluating one dataset must produce one verdict, in one order."""
        dataset, splits = _dataset(flat=True, columns=8, constant_columns=3)
        stats = P.DatasetStats.from_dataset(dataset, splits, measure=True)
        first = P.check_ml_data_requirements(None, stats, _spec(), None, node_id="n")
        second = P.check_ml_data_requirements(None, stats, _spec(), None, node_id="n")
        assert first.codes == second.codes
        assert first.warning_codes == second.warning_codes


class TestTheGovernanceMetricsAreExportedAndSeparatelyDeclared:
    """The four governance series, pinned the way ``metrics.py`` pins its other lists.

    WHY THIS CLASS EXISTS, SPECIFICALLY
    -----------------------------------
    These four metrics were originally appended to
    ``MetricsCollector.STRATEGY_BUILDER_METRIC_ATTRIBUTES``, and that tuple is asserted
    element-for-element against Requirements 24.1-24.3 in
    ``tests/test_task_9_1_builder_metrics.py``. Two of its tests failed for a metric the
    requirement they check does not mention. The file's own comment already warned about
    this - ``PAPER_FEED_METRIC_ATTRIBUTES`` and ``MARKETPLACE_PAPER_METRIC_ATTRIBUTES``
    are separate tuples for exactly that reason - so the governance series now get a
    fourth tuple, and this class is what keeps them out of the pinned one.

    The second half matters more than the first. A metric can be defined, recorded and
    still never scraped: the exposition is built from the attribute lists, so a series
    whose list is not folded into ``get_prometheus_metrics`` is silently invisible. That
    is the failure these tests exist to catch, and it is not hypothetical either - the
    four were briefly declared and unexported while the tuple was being moved.
    """

    #: The series names a dashboard or alert would be written against. Spelled as the
    #: metric objects spell them, before Prometheus name sanitisation.
    GOVERNANCE_SERIES = (
        "training.dataset.warnings",
        "training.dataset.blocks",
        "training.jobs.early_stopped",
        "training.epochs.used_ratio",
    )

    def _collector(self):
        from backend_app.backend.metrics import MetricsCollector

        return MetricsCollector()

    def test_the_governance_series_are_not_in_the_pinned_builder_tuple(self):
        """Requirements 24.1-24.3 do not mention these, so they may not be asserted there."""
        collector = self._collector()
        overlap = set(collector.STRATEGY_BUILDER_METRIC_ATTRIBUTES) & set(
            collector.TRAINING_GOVERNANCE_METRIC_ATTRIBUTES
        )
        assert overlap == set(), (
            f"{sorted(overlap)} are declared in both tuples. The strategy-builder tuple is "
            f"pinned element-for-element against its requirement text; a governance metric "
            f"in it fails that assertion."
        )

    def test_the_tuple_and_the_metric_objects_agree(self):
        """A typo in the attribute list would drop a series from the scrape silently."""
        collector = self._collector()
        names = [
            getattr(collector, attribute).name
            for attribute in collector.TRAINING_GOVERNANCE_METRIC_ATTRIBUTES
        ]
        assert sorted(names) == sorted(self.GOVERNANCE_SERIES)
        assert len(set(collector.TRAINING_GOVERNANCE_METRIC_ATTRIBUTES)) == len(
            collector.TRAINING_GOVERNANCE_METRIC_ATTRIBUTES
        )

    def test_every_governance_series_reaches_the_scrape(self):
        """Defined is not exposed. Each one has to appear in ``/metrics``' payload.

        One sample of each is recorded first: a labelled series with no observations
        exports no lines at all, so an unrecorded metric would pass a substring check
        for the wrong reason.
        """
        from backend_app.backend import metrics as M

        collector = self._collector()
        collector.record_training_dataset_warning("LIMITED_SAMPLES")
        collector.record_training_dataset_block("SPLIT_INFEASIBLE")
        collector.record_training_early_stop("NO_IMPROVEMENT")
        collector.record_training_epochs_used("xgboost", 12, 40)

        exported = collector.get_prometheus_metrics()
        for series in self.GOVERNANCE_SERIES:
            assert M.to_prometheus_name(series) in exported, (
                f"{series} is declared but never scraped. Its attribute tuple is probably "
                f"not folded into get_prometheus_metrics()."
            )

    def test_the_epoch_ratio_records_budget_actually_spent(self):
        """12 of 40 approved epochs is 0.3, which is the figure the histogram observes."""
        from backend_app.backend import metrics as M

        collector = self._collector()
        collector.record_training_epochs_used("xgboost", 12, 40)
        exported = collector.get_prometheus_metrics()
        name = M.to_prometheus_name("training.epochs.used_ratio")
        assert f'{name}_sum{{block_id="xgboost"}} 0.3' in exported

    def test_a_zero_epoch_ceiling_records_nothing_rather_than_dividing_by_it(self):
        """A refused request has no ratio to report, and must not raise on the way out."""
        collector = self._collector()
        collector.record_training_epochs_used("xgboost", 0, 0)
        exported = collector.get_prometheus_metrics()
        assert "training_epochs_used_ratio_count" not in exported
