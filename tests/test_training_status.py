"""
tests/test_training_status.py

Truthful training status reporting: what a client is told, and what it is never told.

Spec: strategy-builder task 6.6 (`design.md` -> Training workflow -> "Status surface
(`GET /training/jobs/{id}`)" and the Reliable-ETA rule). Requirements 14.9, 15.2, 15.3,
15.4, 15.5, 15.6.

WHAT THIS FILE HOLDS IN PLACE
-----------------------------
Six properties, each of which would be a real defect - not a cosmetic one - if it stopped
holding:

1. **Requirement 15.3's whole list is reported**, in one document, for every status. A
   field that quietly stopped being projected would make a builder render a blank where a
   measured figure belongs, and the author would have no way to tell "zero" from "not
   reported".
2. **Progress comes from completed epochs and from nothing else** (Requirement 15.4).
   There is no elapsed-time term to creep in: a job that has run for an hour without
   finishing an epoch reports the same progress as one that started a second ago.
3. **The ETA is absent until it is trustworthy** (Requirements 15.5, 15.6): fewer than
   three completed epochs, or a duration coefficient of variation of 0.35 or greater, and
   the answer is `None` **with a machine-readable reason** rather than a number. A guessed
   ETA is the specific dishonesty this requirement exists to forbid.
4. **`cancellable` is derived from the status vocabulary**, never stored, so the flag
   cannot promise a cancellation the cancel endpoint would answer 409 to.
5. **Another tenant's job is a 404, not a 403** (Requirement 21.4). A 403 confirms the
   identifier exists, which is an existence oracle over other tenants' job ids. Ownership
   is the double filter every other Strategy Builder read uses: the caller's own
   RLS-scoped client **plus** an explicit `.eq("user_id", ...)`.
6. **No artifact reference travels, anywhere in the payload.** Asserted as a **substring**
   of the whole serialised document rather than as an absent key, because a nested copy
   under any name is the same leak.

Plus the two degradation rules this environment actually exercises: an unapplied
`004d_training_and_models.sql` answers "no such job" / "no jobs" with a warning **naming
that file** and never a 500, and everything echoed out of `metrics_history` goes back
through `training_worker.sanitize_epoch_metrics` - the same function that wrote it -
because 004d's header assigns that enforcement to tasks 6.4 **and 6.6**.

WHAT IS REAL HERE AND WHAT IS A DOUBLE
--------------------------------------
Real: every line of `training_status`; `training_worker`'s vocabulary and sanitiser;
`model_versioning.public_model_version`; `strategy_service`'s admission path, which is
what *creates* the job rows the integration tests read; the real registry, compiler,
validator, feature pipeline, `ml_dataset` and `ml_training_policy` behind it; the real
worker running a real gradient-descent fit; and the real FastAPI app for the HTTP tests.

Doubles, and only these three - all three inherited from the task 6.4 and 6.5 suites
rather than rebuilt here: the database (`FakeSupabase` / `ModelStore`, because
`004d_training_and_models.sql` is unapplied and there is no local PostgreSQL), the candle
feed, and the realtime hand-off.

WHAT IS NOT VERIFIED HERE
-------------------------
No real `training_jobs` table, so `tj_owner_select` is exercised as an explicit filter
against a Python double rather than as a live RLS policy; live enforcement remains 004d's
own VERIFICATION queries plus task 8.7's isolation matrix. Nothing here writes: the
module under test issues no INSERT and no UPDATE, and the tests assert that too.
"""

import json
import logging
import math
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import model_versioning as MV
from backend_app.backend import strategy_service as S
from backend_app.backend import training_status as TS
from backend_app.backend import training_worker as W
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag import validator as V
from backend_app.core import ml_safety

from tests.test_training_worker import (  # noqa: E402 - shared doubles, deliberately
    PAID_TIER,
    STRATEGY_ID,
    USER_ID,
    FakeSupabase,
    GradientDescentTrainer,
    bounded_bars,
    model_graph,
    owner,
    queue_job,
)
from tests.test_model_versioning import (  # noqa: E402 - the model_versions half
    SECRET,
    ModelStore,
    a_row,
)


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures - the task 6.4 / 6.5 suites', re-declared because fixtures are per-module
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def reg():
    return registry_module.build_registry()


@pytest.fixture(autouse=True)
def default_hooks():
    V.install_default_stage_hooks()
    yield
    V.install_default_stage_hooks()


@pytest.fixture(autouse=True)
def forget_column_probe():
    S.reset_canonical_column_support()
    yield
    S.reset_canonical_column_support()


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    """No installed backend, no installed store, artifacts under ``tmp_path``.

    Every one of these is process-global; leaving one set would make a later test in this
    file - or in another file - pass or fail depending on collection order.
    """
    W.reset_training_backend()
    W.reset_isolation_latch()
    MV.reset_artifact_store()
    memory_before = ml_safety.MemoryMonitor._config
    training_before = ml_safety.TrainingIsolator._config
    ml_safety.TrainingIsolator.configure(
        ml_safety.TrainingConfig(enable_process_isolation=False)
    )
    monkeypatch.setenv(MV.ARTIFACT_ROOT_ENV, str(tmp_path / "artifacts"))
    monkeypatch.delenv(MV.ARTIFACT_BUCKET_ENV, raising=False)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    yield
    ml_safety.MemoryMonitor._config = memory_before
    ml_safety.TrainingIsolator._config = training_before
    W.reset_training_backend()
    W.reset_isolation_latch()
    MV.reset_artifact_store()


@pytest.fixture
def window(monkeypatch):
    async def _fetch(symbol, timeframe, bars):
        return bounded_bars(int(bars))

    monkeypatch.setattr(S, "fetch_training_bars", _fetch)
    return {}


@pytest.fixture(autouse=True)
def handoffs(monkeypatch):
    recorded = {"enqueued": [], "events": []}

    async def _enqueue(job_id, user_id):
        recorded["enqueued"].append(str(job_id))
        return True

    async def _publish(user_id, event, payload):
        recorded["events"].append((event, dict(payload)))
        return True

    monkeypatch.setattr(S, "enqueue_training_job", _enqueue)
    monkeypatch.setattr(S, "publish_training_event", _publish)
    return recorded


@pytest.fixture
def db():
    return ModelStore.seeded()


@pytest.fixture
def service(db):
    from backend_app.backend.strategy_service import StrategyService

    with patch.object(S, "create_request_supabase_async", AsyncMock(return_value=db)):
        yield StrategyService()


@pytest.fixture
def store(tmp_path):
    local = MV.LocalArtifactStore(tmp_path / "artifacts")
    MV.register_artifact_store(local)
    return local


def install_production_binder(**trainer_kwargs):
    """Task 6.5's real binder through the production seam, so a COMPLETED job has a model."""
    W.register_training_backend(
        MV.training_backend(lambda ctx: GradientDescentTrainer(ctx, **trainer_kwargs))
    )


# ═══════════════════════════════════════════════════════════════════════════
# Row builders
# ═══════════════════════════════════════════════════════════════════════════


def epoch(index, duration, *, loss=None, val_loss=None):
    """One ``metrics_history`` entry, shaped exactly as the worker writes one."""
    entry = {"epoch": int(index), "duration_seconds": float(duration)}
    if loss is not None:
        entry["loss"] = float(loss)
    if val_loss is not None:
        entry["val_loss"] = float(val_loss)
    return entry


def a_job(**overrides):
    """A ``training_jobs``-shaped mapping carrying every column 004d declares.

    Hand-made on purpose for the projection and rule tests: a row written by an older
    worker, a migration or a hand-edit is exactly the input the read-side checks exist
    for, and it cannot be produced by driving the current writer.
    """
    row = {
        "id": "job_0001",
        "user_id": USER_ID,
        "strategy_id": STRATEGY_ID,
        "version_id": "ver_1",
        "node_id": "model_node",
        "block_id": "xgboost",
        "status": "RUNNING",
        "cancel_requested": False,
        "failure_reason": None,
        "config": {"epochs": 10, "api_key": "sk_live_never_served"},
        "dataset_fingerprint": "fp_abcdef",
        "dataset_rows": 5_000,
        "usable_rows": 4_200,
        "feature_columns": 6,
        "feature_names": ["ema_20_lag_1", "ema_20_lag_2"],
        "split_sizes": {"train": 2_940, "val": 630, "test": 630},
        "epochs_total": 10,
        "epoch_current": 4,
        "loss": 0.51,
        "val_loss": 0.63,
        "progress": 0.4,
        "metrics_history": [epoch(i, 10.0, loss=0.6 - i / 100) for i in range(1, 5)],
        "worker_id": "worker-internal-host-7",
        "last_heartbeat": "2026-01-01T00:04:00+00:00",
        "created_at": "2026-01-01T00:00:00+00:00",
        "started_at": "2026-01-01T00:00:10+00:00",
        "completed_at": None,
        "updated_at": "2026-01-01T00:04:00+00:00",
    }
    row.update(overrides)
    return row


def with_jobs(*rows, tier=PAID_TIER):
    """A store holding exactly ``rows`` in ``training_jobs``."""
    store = ModelStore.seeded(tier=tier)
    store.tables["training_jobs"] = [dict(row) for row in rows]
    return store


def intruder():
    return {**owner(), "id": "usr_someone_else", "access_token": "token_intruder"}


#: ``[mean - x, mean, mean + x]`` has a sample standard deviation of exactly ``x`` and a
#: mean of exactly ``mean``, so its coefficient of variation is exactly ``x / mean`` - in
#: floating point too, for the values used below. That is what makes the boundary at 0.35
#: assertable rather than approximable.
def durations_with_cv(cv, *, mean=10.0, count=3):
    spread = cv * mean
    if count == 3:
        return [mean - spread, mean, mean + spread]
    return [mean] * (count - 3) + [mean - spread, mean, mean + spread]


# ═══════════════════════════════════════════════════════════════════════════
# 1. The vocabulary is reused, never restated
# ═══════════════════════════════════════════════════════════════════════════


class TestTheVocabularyIsReused:
    def test_the_five_statuses_are_task_6_3s_constant(self):
        """Requirement 15.2's set, from the module that owns ``chk_tj_status``."""
        assert TS.TRAINING_JOB_STATES is S.TRAINING_JOB_STATES
        assert set(TS.TRAINING_JOB_STATES) == {
            "QUEUED",
            "RUNNING",
            "COMPLETED",
            "FAILED",
            "CANCELLED",
        }

    def test_the_live_set_is_the_one_the_unique_index_scopes_to(self):
        assert TS.TRAINING_JOB_LIVE_STATES is S.TRAINING_JOB_LIVE_STATES
        assert set(TS.TRAINING_JOB_LIVE_STATES) == {"QUEUED", "RUNNING"}

    def test_the_eta_thresholds_are_the_requirements_own_figures(self):
        """Requirement 15.5: three epochs. Requirement 15.6: 0.35."""
        assert TS.ETA_MIN_COMPLETED_EPOCHS == 3
        assert TS.ETA_MAX_DURATION_CV == 0.35

    def test_the_sanitiser_is_the_writers_own_function_and_not_a_copy(self):
        """004d hands this enforcement to tasks 6.4 AND 6.6. Identity, not equivalence.

        Two sanitisers that agree today are one edit away from disagreeing, and only one
        of them would be covered by the task 6.4 suite.
        """
        history = TS.scalar_metrics_history([{"loss": 0.5, "predictions": [1, 2, 3]}])

        assert history == [W.sanitize_epoch_metrics({"loss": 0.5, "predictions": [1, 2, 3]})]
        assert TS.DURATION_KEY in W.EPOCH_METRIC_KEYS

    def test_the_duration_key_is_the_one_the_worker_writes(self):
        assert TS.DURATION_KEY == "duration_seconds"

    def test_the_report_version_is_declared(self):
        assert isinstance(TS.TRAINING_STATUS_VERSION, str)
        assert TS.TRAINING_STATUS_VERSION

    def test_the_module_issues_no_write_at_all(self):
        """A reporting surface that wrote would race the worker that owns the row - and
        004d attaches no ``updated_at`` trigger to ``training_jobs``, so every write is
        one more place to forget that column. The safest way to hold that invariant is to
        have no write to forget it on."""
        import pathlib
        import re

        source = pathlib.Path(TS.__file__).read_text(encoding="utf-8")
        # Only the call forms matter; the docstrings say the words on purpose.
        code = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        assert not re.search(r"\.insert\s*\(", code)
        assert not re.search(r"\.update\s*\(", code)
        assert not re.search(r"\.upsert\s*\(", code)
        assert not re.search(r"\.delete\s*\(", code)


# ═══════════════════════════════════════════════════════════════════════════
# 2. Requirement 15.4 - progress from COMPLETED EPOCHS and nothing else
# ═══════════════════════════════════════════════════════════════════════════


class TestProgressComesFromCompletedEpochs:
    @pytest.mark.parametrize(
        "completed,total,expected",
        [
            (0, 10, 0.0),
            (1, 10, 0.1),
            (5, 10, 0.5),
            (10, 10, 1.0),
            (3, 3, 1.0),
        ],
    )
    def test_it_is_completed_over_total(self, completed, total, expected):
        assert TS.derive_progress(completed, total) == pytest.approx(expected)

    def test_an_unknown_total_reports_absent_rather_than_zero(self):
        """``0.0`` would claim no work had been done when some may have been."""
        assert TS.derive_progress(4, None) is None
        assert TS.derive_progress(4, 0) is None
        assert TS.derive_progress(4, -1) is None

    def test_it_is_clamped_into_chk_tj_progresss_own_range(self):
        assert TS.derive_progress(-5, 10) == 0.0
        assert TS.derive_progress(99, 10) == 1.0

    def test_no_elapsed_time_term_can_move_it(self):
        """Two jobs, one started days ago and one seconds ago, no epoch finished on
        either: identical progress. This is the fabricated-progress bar the requirement
        forbids, and it is structurally impossible because no timestamp is an input."""
        old = TS.public_training_job(
            a_job(
                epoch_current=0,
                metrics_history=[],
                progress=None,
                started_at="2020-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )
        fresh = TS.public_training_job(
            a_job(
                epoch_current=0,
                metrics_history=[],
                progress=None,
                started_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:01+00:00",
            )
        )

        assert old["progress"] == fresh["progress"] == 0.0

    def test_a_stored_progress_that_disagrees_is_not_the_one_reported(self, caplog):
        """The stored column is a cache of the same arithmetic. Where the two disagree the
        derived figure wins and the disagreement is logged for an operator."""
        with caplog.at_level(logging.WARNING, logger="TrainingStatus"):
            report = TS.public_training_job(
                a_job(epoch_current=4, epochs_total=10, progress=0.99)
            )

        assert report["progress"] == pytest.approx(0.4)
        assert "progress" in caplog.text

    def test_a_half_finished_epoch_counts_for_nothing(self):
        """``epoch_current`` is a count of COMPLETED epochs. Four entries in the history
        and a fifth epoch in flight is still 4/10."""
        report = TS.public_training_job(
            a_job(
                epoch_current=4,
                epochs_total=10,
                metrics_history=[epoch(i, 10.0) for i in range(1, 5)],
            )
        )

        assert report["epoch_current"] == 4
        assert report["progress"] == pytest.approx(0.4)


# ═══════════════════════════════════════════════════════════════════════════
# 3. Requirements 15.5 and 15.6 - the ETA is absent until it is trustworthy
# ═══════════════════════════════════════════════════════════════════════════


class TestTheEtaIsAbsentUntilItIsTrustworthy:
    def eta(self, **overrides):
        arguments = {
            "status": W.STATUS_RUNNING,
            "epoch_current": 5,
            "epochs_total": 10,
            "durations": [10.0, 10.0, 10.0, 10.0, 10.0],
        }
        arguments.update(overrides)
        return TS.reliable_eta(**arguments)

    # -- the published regime ---------------------------------------------
    def test_three_stable_epochs_publish_mean_times_remaining(self):
        answer = self.eta(epoch_current=3, epochs_total=5, durations=[10.0, 10.0, 10.0])

        assert answer["eta_state"] == TS.ETA_AVAILABLE
        # 2 epochs remaining x 10 s. No smoothing, no decay, no allowance for the
        # artifact write - the requirement declares this regime trustworthy and nothing
        # else is added to it.
        assert answer["eta_seconds"] == pytest.approx(20.0)
        assert answer["eta_detail"]["epochs_remaining"] == 2
        assert answer["eta_detail"]["mean_epoch_seconds"] == pytest.approx(10.0)

    def test_the_last_epoch_publishes_zero_rather_than_nothing(self):
        answer = self.eta(epoch_current=5, epochs_total=5, durations=[10.0] * 5)

        assert answer["eta_state"] == TS.ETA_AVAILABLE
        assert answer["eta_seconds"] == pytest.approx(0.0)

    # -- Requirement 15.5: fewer than three completed epochs --------------
    @pytest.mark.parametrize("completed", [0, 1, 2])
    def test_fewer_than_three_completed_epochs_is_absent(self, completed):
        answer = self.eta(
            epoch_current=completed, durations=[10.0] * max(completed, 0)
        )

        assert answer["eta_seconds"] is None
        assert answer["eta_state"] == TS.ETA_ABSENT_TOO_FEW_EPOCHS
        assert answer["eta_detail"]["required_epochs"] == 3
        assert answer["eta_detail"]["completed_epochs"] == completed

    def test_the_third_epoch_is_the_first_that_can_publish(self):
        """The boundary, both sides of it. Requirement 15.5 says "fewer than three"."""
        assert self.eta(epoch_current=2, durations=[10.0, 10.0])["eta_seconds"] is None
        assert (
            self.eta(epoch_current=3, durations=[10.0, 10.0, 10.0])["eta_seconds"]
            is not None
        )

    # -- Requirement 15.6: unstable durations -----------------------------
    def test_a_cv_of_exactly_the_threshold_is_absent(self):
        """Requirement 15.6 says "0.35 **or greater**", so the boundary itself withholds.

        ``[6.5, 10.0, 13.5]`` has a mean of exactly 10 and a sample standard deviation of
        exactly 3.5, so its cv is exactly 0.35 in floating point as well as in arithmetic.
        """
        durations = durations_with_cv(0.35)
        assert TS.duration_coefficient_of_variation(durations) == 0.35

        answer = self.eta(epoch_current=3, durations=durations)

        assert answer["eta_seconds"] is None
        assert answer["eta_state"] == TS.ETA_ABSENT_UNSTABLE_DURATIONS
        assert answer["eta_detail"]["max_duration_cv"] == 0.35

    def test_just_below_the_threshold_publishes_and_just_above_does_not(self):
        below = durations_with_cv(0.34)
        above = durations_with_cv(0.36)

        assert TS.duration_coefficient_of_variation(below) < 0.35
        assert TS.duration_coefficient_of_variation(above) > 0.35
        assert self.eta(epoch_current=3, durations=below)["eta_seconds"] is not None
        assert self.eta(epoch_current=3, durations=above)["eta_seconds"] is None

    def test_wildly_varying_epochs_are_absent_rather_than_averaged(self):
        answer = self.eta(epoch_current=3, durations=[1.0, 10.0, 1.0])

        assert answer["eta_seconds"] is None
        assert answer["eta_state"] == TS.ETA_ABSENT_UNSTABLE_DURATIONS
        assert answer["eta_detail"]["duration_cv"] > 0.35

    def test_the_sample_standard_deviation_is_used_so_the_borderline_withholds(self):
        """``n - 1`` rather than ``n``: the larger of the two, so a borderline sample is
        withheld rather than published. Requirement 15.6 names no denominator, and
        withholding is the direction that cannot mislead."""
        values = [9.0, 10.0, 11.0]
        mean = sum(values) / len(values)
        population = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values)) / mean
        reported = TS.duration_coefficient_of_variation(values)

        assert reported > population

    def test_a_cv_is_undefined_for_fewer_than_two_samples_and_for_a_zero_mean(self):
        assert TS.duration_coefficient_of_variation([]) is None
        assert TS.duration_coefficient_of_variation([5.0]) is None
        # Reporting 0.0 here would read as "perfectly stable" for a sample that says
        # nothing at all.
        assert TS.duration_coefficient_of_variation([0.0, 0.0, 0.0]) is None

    def test_an_undefined_cv_withholds_rather_than_publishes(self):
        answer = self.eta(epoch_current=3, durations=[0.0, 0.0, 0.0])

        assert answer["eta_seconds"] is None
        assert answer["eta_state"] == TS.ETA_ABSENT_UNSTABLE_DURATIONS

    # -- the other three absences -----------------------------------------
    @pytest.mark.parametrize(
        "status", ["QUEUED", "COMPLETED", "FAILED", "CANCELLED", "", "nonsense"]
    )
    def test_a_job_that_is_not_running_has_no_estimate(self, status):
        """A queued job's remaining time depends on a queue this function cannot see; a
        finished one has none."""
        answer = self.eta(status=status)

        assert answer["eta_seconds"] is None
        assert answer["eta_state"] == TS.ETA_ABSENT_NOT_RUNNING

    def test_no_epoch_denominator_means_no_estimate(self):
        for total in (None, 0, -3):
            answer = self.eta(epochs_total=total)
            assert answer["eta_seconds"] is None
            assert answer["eta_state"] == TS.ETA_ABSENT_UNKNOWN_TOTAL

    def test_three_completed_epochs_with_fewer_than_three_measured_is_absent(self):
        """Completed and *measured* are different counts. An epoch whose duration was
        never recorded is absent from the sample rather than imputed into it."""
        answer = self.eta(epoch_current=4, durations=[10.0, 10.0])

        assert answer["eta_seconds"] is None
        assert answer["eta_state"] == TS.ETA_ABSENT_NO_DURATIONS
        assert answer["eta_detail"]["completed_epochs"] == 4
        assert answer["eta_detail"]["measured_epochs"] == 2

    def test_every_absence_names_which_condition_held_it_back(self):
        """A machine code, so the builder can render "estimating…" and say why without
        parsing prose."""
        states = {
            self.eta(status="QUEUED")["eta_state"],
            self.eta(epochs_total=None)["eta_state"],
            self.eta(epoch_current=1, durations=[10.0])["eta_state"],
            self.eta(epoch_current=4, durations=[10.0, 10.0])["eta_state"],
            self.eta(epoch_current=3, durations=[1.0, 10.0, 1.0])["eta_state"],
        }

        assert len(states) == 5, states
        assert states <= TS.ETA_STATES
        assert TS.ETA_AVAILABLE not in states

    # -- the durations themselves -----------------------------------------
    def test_only_measured_durations_enter_the_sample(self):
        history = [
            epoch(1, 10.0),
            {"epoch": 2},  # no duration at all
            {"epoch": 3, "duration_seconds": None},
            {"epoch": 4, "duration_seconds": "not a number"},
            {"epoch": 5, "duration_seconds": float("nan")},
            {"epoch": 6, "duration_seconds": -4.0},  # a negative wall clock is not one
            epoch(7, 12.0),
        ]

        assert TS.epoch_durations(history) == (10.0, 12.0)

    def test_a_zero_duration_is_kept_because_an_epoch_can_be_that_fast(self):
        """Dropping it would make a fast run look unmeasured."""
        assert TS.epoch_durations([epoch(1, 0.0), epoch(2, 0.0)]) == (0.0, 0.0)

    def test_the_worker_and_the_endpoint_cannot_disagree_about_trustworthiness(self):
        """The realtime ``training.progress`` frame calls this very function, so the
        socket and ``GET /training/jobs/{id}`` publish or withhold together."""
        import pathlib

        source = pathlib.Path(W.__file__).read_text(encoding="utf-8")
        assert "TS.reliable_eta(" in source
        assert "TS.epoch_durations(" in source


# ═══════════════════════════════════════════════════════════════════════════
# 4. The cancellable flag comes from the status vocabulary
# ═══════════════════════════════════════════════════════════════════════════


class TestTheCancellableFlagIsTruthful:
    @pytest.mark.parametrize("status", ["QUEUED", "RUNNING", "queued", "running"])
    def test_a_live_job_is_cancellable(self, status):
        assert TS.is_cancellable(status) is True
        assert TS.public_training_job(a_job(status=status))["cancellable"] is True

    @pytest.mark.parametrize("status", ["COMPLETED", "FAILED", "CANCELLED"])
    def test_a_terminal_job_is_not(self, status):
        assert TS.is_cancellable(status) is False
        assert TS.public_training_job(a_job(status=status))["cancellable"] is False

    @pytest.mark.parametrize("status", [None, "", "PAUSED", "nonsense"])
    def test_anything_outside_the_vocabulary_is_not_cancellable(self, status):
        """Fail closed: a status this build does not recognise must not advertise an
        action the cancel endpoint would refuse."""
        assert TS.is_cancellable(status) is False

    def test_it_is_derived_and_never_read_from_the_row(self):
        """A stored flag could disagree with the status. This one cannot."""
        report = TS.public_training_job(a_job(status="COMPLETED", cancellable=True))

        assert report["cancellable"] is False

    def test_the_flag_matches_the_filter_the_cancel_path_uses(self):
        """The two read the same constant, so the flag cannot promise a cancellation the
        cancel endpoint would answer 409 to."""
        for status in TS.TRAINING_JOB_STATES:
            assert TS.is_cancellable(status) is (status in S.TRAINING_JOB_LIVE_STATES)


# ═══════════════════════════════════════════════════════════════════════════
# 5. Requirement 15.3 - the whole list, in one document
# ═══════════════════════════════════════════════════════════════════════════

#: Requirement 15.3, read literally: "the dataset row count, the usable row count, the
#: feature column count, the feature names, the split sizes, the model block identifier,
#: the total epoch count, the current epoch, the training loss, the validation loss, the
#: progress fraction, the cancellable flag and the failure reason" - plus Requirement
#: 15.2's status, which the same sentence in the task names first.
REQUIREMENT_15_3_FIELDS = (
    "status",
    "dataset_rows",
    "usable_rows",
    "feature_columns",
    "feature_names",
    "split_sizes",
    "model",
    "epochs_total",
    "epoch_current",
    "loss",
    "val_loss",
    "progress",
    "cancellable",
    "failure_reason",
)


class TestTheProjectionCarriesTheWholeList:
    def test_every_field_the_requirement_names_is_present(self):
        report = TS.public_training_job(a_job())

        for field in REQUIREMENT_15_3_FIELDS:
            assert field in report, field

    @pytest.mark.parametrize("status", sorted(S.TRAINING_JOB_STATES))
    def test_every_field_is_present_in_every_status(self, status):
        """A QUEUED job reports the dataset it will train on; a FAILED one reports the
        work it did. A field that only appeared in one status would make a builder render
        a blank where a measured figure belongs."""
        report = TS.public_training_job(
            a_job(status=status, failure_reason="WORKER_LOST" if status == "FAILED" else None)
        )

        for field in REQUIREMENT_15_3_FIELDS:
            assert field in report, (status, field)

    def test_the_measured_dataset_figures_are_echoed_as_measured(self):
        report = TS.public_training_job(a_job())

        assert report["dataset_rows"] == 5_000
        assert report["usable_rows"] == 4_200
        assert report["feature_columns"] == 6
        assert report["feature_names"] == ["ema_20_lag_1", "ema_20_lag_2"]
        assert report["split_sizes"] == {"train": 2_940, "val": 630, "test": 630}

    def test_the_model_block_identifier_is_reported_under_both_names(self):
        """``design.md``'s status surface calls it ``model``; every other Strategy Builder
        document calls a block's identifier ``block_id``. Both, so neither client is
        wrong."""
        report = TS.public_training_job(a_job(block_id="lstm"))

        assert report["model"] == "lstm"
        assert report["block_id"] == "lstm"

    def test_the_run_figures_are_reported(self):
        report = TS.public_training_job(a_job())

        assert report["epochs_total"] == 10
        assert report["epoch_current"] == 4
        assert report["loss"] == pytest.approx(0.51)
        assert report["val_loss"] == pytest.approx(0.63)

    def test_a_classified_failure_reason_is_echoed_verbatim(self):
        for reason in sorted(W.FAILURE_REASONS):
            report = TS.public_training_job(a_job(status="FAILED", failure_reason=reason))
            assert report["failure_reason"] == reason

    def test_an_unclassified_failure_reason_is_reported_not_rewritten(self, caplog):
        """Rewriting it into a reason this module recognises would be inventing the
        classification Requirement 15.10 asks to be *recorded*. The row is the record."""
        with caplog.at_level(logging.WARNING, logger="TrainingStatus"):
            report = TS.public_training_job(
                a_job(status="FAILED", failure_reason="SOMETHING_ELSE")
            )

        assert report["failure_reason"] == "SOMETHING_ELSE"
        assert "FAILURE_REASONS" in caplog.text

    def test_a_blank_failure_reason_is_absent_rather_than_an_empty_string(self):
        assert TS.public_training_job(a_job(failure_reason=""))["failure_reason"] is None
        assert TS.public_training_job(a_job(failure_reason="   "))["failure_reason"] is None

    def test_a_status_outside_chk_tj_status_is_reported_and_flagged(self, caplog):
        with caplog.at_level(logging.WARNING, logger="TrainingStatus"):
            report = TS.public_training_job(a_job(status="PAUSED"))

        assert report["status"] == "PAUSED"
        assert report["cancellable"] is False
        assert "chk_tj_status" in caplog.text

    # -- the three columns that are absent BY CONSTRUCTION ----------------
    def test_the_recorded_config_is_not_served(self):
        """It is the provenance record (Requirement 15.14) and it is not on Requirement
        15.3's list. Task 6.5 publishes a **whitelisted** projection of it as a model
        version's ``hyperparameters``, so there is one path that decides which of its keys
        a client may see and it is not this one."""
        report = TS.public_training_job(a_job())

        assert "config" not in report
        assert "sk_live_never_served" not in json.dumps(report, default=str)

    def test_the_worker_identity_is_not_served(self):
        """It names internal hosts and a client has no use for it."""
        report = TS.public_training_job(a_job())

        assert "worker_id" not in report
        assert "last_heartbeat" not in report
        assert "worker-internal-host-7" not in json.dumps(report, default=str)

    def test_the_projection_is_built_from_named_keys_not_from_a_copy(self):
        """A projection that *removes* keys is one refactor away from not removing them.
        An unknown column put on the row does not come back out."""
        report = TS.public_training_job(a_job(secret_column="leak-me"))

        assert "secret_column" not in report
        assert "leak-me" not in json.dumps(report, default=str)

    # -- metrics_history goes out through the sanitiser it came in through
    def test_metrics_history_is_re_checked_on_the_way_out(self):
        """004d: that column holds per-epoch scalars only, "SQL CANNOT ENFORCE THIS", and
        the enforcement belongs to the writer in tasks 6.4 **and 6.6**. A row written by
        an older worker, a migration or a hand-edit is not covered by the write side at
        all."""
        report = TS.public_training_job(
            a_job(
                metrics_history=[
                    {
                        "epoch": 1,
                        "loss": 0.5,
                        "duration_seconds": 9.0,
                        "predictions": [0.1, 0.9],
                        "feature_values": [[1, 2], [3, 4]],
                        "y_true": [1, 0],
                    }
                ]
            )
        )

        serialised = json.dumps(report, default=str)
        assert report["metrics_history"] == [
            {"epoch": 1, "loss": 0.5, "duration_seconds": 9.0}
        ]
        for leak in ("predictions", "feature_values", "y_true", "0.9"):
            assert leak not in serialised, leak

    def test_a_vector_under_an_innocent_key_is_dropped_too(self):
        report = TS.public_training_job(
            a_job(metrics_history=[{"epoch": 1, "metric": [1, 2, 3], "loss": 0.4}])
        )

        assert report["metrics_history"] == [{"epoch": 1, "loss": 0.4}]

    def test_a_metrics_history_that_is_not_a_list_reports_empty(self, caplog):
        with caplog.at_level(logging.WARNING, logger="TrainingStatus"):
            report = TS.public_training_job(a_job(metrics_history={"loss": 0.5}))

        assert report["metrics_history"] == []

    def test_a_non_mapping_entry_is_dropped_rather_than_guessed_at(self):
        report = TS.public_training_job(
            a_job(metrics_history=[epoch(1, 5.0), "not a mapping", None, epoch(2, 5.0)])
        )

        assert len(report["metrics_history"]) == 2

    def test_the_loss_columns_go_through_the_same_rule(self):
        """``loss`` and ``val_loss`` are separate DOUBLE PRECISION columns, so the
        write-side scalar rule never saw them as metrics. A vector or a NaN in either
        would leak model output or break the JSON encoding of the whole response."""
        report = TS.public_training_job(
            a_job(loss=[0.1, 0.2, 0.3], val_loss=float("nan"))
        )

        assert report["loss"] is None
        assert report["val_loss"] is None
        # And the document still encodes, which is the point.
        json.dumps(report, default=str)

    def test_an_infinite_loss_does_not_travel(self):
        report = TS.public_training_job(a_job(loss=float("inf")))

        assert report["loss"] is None
        assert "Infinity" not in json.dumps(report, default=str)

    # -- the bound model --------------------------------------------------
    def test_an_unbound_job_says_so_explicitly(self):
        """``None`` alone would make "completed with no readable model row" look identical
        to "the caller did not ask for the model"."""
        report = TS.public_training_job(a_job(status="COMPLETED"))

        assert report["model_bound"] is False
        assert report["model_version"] is None

    def test_a_bound_model_is_task_6_5s_projection(self):
        projection = MV.public_model_version(a_row())
        report = TS.public_training_job(a_job(status="COMPLETED"), model_version=projection)

        assert report["model_bound"] is True
        assert report["model_version"] == projection


# ═══════════════════════════════════════════════════════════════════════════
# 6. No artifact reference travels - as a SUBSTRING, not as an absent key
# ═══════════════════════════════════════════════════════════════════════════


class TestNoArtifactReferenceTravels:
    URI = "local://models/u/s/v/n/v3-abcdef0123456789.joblib"

    def test_the_projection_of_a_bound_job_carries_no_reference(self):
        """Searched as a substring of the whole serialised payload rather than as one
        key, because a nested copy under any name is the same leak - and the bytes stay
        behind task 6.5's signed, ownership-checked exchange."""
        projection = MV.public_model_version(a_row(artifact_uri=self.URI))
        report = TS.public_training_job(a_job(status="COMPLETED"), model_version=projection)

        serialised = json.dumps(report, default=str)
        assert self.URI not in serialised
        assert "artifact_uri" not in serialised
        # Nor any path segment of it: the bare file name IS reported (a client needs
        # something to save the download as), the LOCATION is not.
        assert "local://" not in serialised
        assert "models/u/s/v/n" not in serialised
        # What a client legitimately needs to verify a download it has a link for:
        assert report["model_version"]["artifact_checksum"] == "a" * 64
        assert report["model_version"]["artifact_available"] is True

    def test_a_reference_smuggled_onto_the_job_row_does_not_come_out(self):
        report = TS.public_training_job(a_job(artifact_uri=self.URI))

        assert self.URI not in json.dumps(report, default=str)

    @pytest.mark.asyncio
    async def test_neither_endpoints_body_carries_a_reference(self, db, store, reg, service, window):
        graph, _ = model_graph(reg)
        await queue_job(service, reg, graph, epochs=3)
        install_production_binder()
        job_id = db.job()["id"]
        version_id = db.job()["version_id"]

        await W.run_training_job(job_id, sb=db, worker_id="worker-a")
        stored_uri = db.models[0]["artifact_uri"]
        assert stored_uri, "the run stored no artifact, so this asserts nothing"

        one = await TS.training_job_report(owner(), job_id, sb=db)
        many = await TS.list_training_job_reports(owner(), version_id, sb=db)

        for payload in (one, many):
            serialised = json.dumps(payload, default=str)
            assert stored_uri not in serialised
            assert "artifact_uri" not in serialised
        assert one["model_bound"] is True


# ═══════════════════════════════════════════════════════════════════════════
# 7. Ownership is double, and absence is the answer for "not yours"
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestOwnershipIsDoubleAndAbsenceIsTheAnswer:
    async def test_the_owner_reads_their_own_job(self):
        store = with_jobs(a_job())

        report = await TS.training_job_report(owner(), "job_0001", sb=store)

        assert report["job_id"] == "job_0001"
        assert report["status"] == "RUNNING"

    async def test_the_read_carries_an_explicit_user_filter_as_well_as_rls(self):
        """Requirement 21.2 asks for the filter in the handler as well as in the database.
        The request-scoped client applies ``tj_owner_select``; the query carries
        ``.eq("user_id", ...)`` too."""
        captured = []

        class Recording(ModelStore):
            def run(self, query):
                if query.op == "select" and query.table == "training_jobs":
                    captured.append(list(query.filters))
                return super().run(query)

        store = Recording(with_jobs(a_job()).tables)

        await TS.load_owned_training_job(owner(), "job_0001", sb=store)

        assert captured, "no training_jobs read was made"
        assert ("eq", "user_id", USER_ID) in captured[-1]
        assert ("eq", "id", "job_0001") in captured[-1]

    async def test_the_list_read_carries_the_same_two_filters(self):
        captured = []

        class Recording(ModelStore):
            def run(self, query):
                if query.op == "select" and query.table == "training_jobs":
                    captured.append(list(query.filters))
                return super().run(query)

        store = Recording(with_jobs(a_job()).tables)

        await TS.list_training_job_reports(owner(), "ver_1", sb=store)

        assert ("eq", "user_id", USER_ID) in captured[-1]
        assert ("eq", "version_id", "ver_1") in captured[-1]

    async def test_the_client_comes_from_the_request_scoped_seam(self):
        """``strategy_service.create_request_supabase_async`` - the same seam task 6.3's
        writes and task 6.5's reads go through - so the caller's own RLS context applies
        rather than a service-role singleton's."""
        store = with_jobs(a_job())
        seam = AsyncMock(return_value=store)

        with patch.object(S, "create_request_supabase_async", seam):
            await TS.training_job_report(owner(), "job_0001")

        seam.assert_awaited()
        assert seam.await_args.args[0] == owner()["access_token"]

    async def test_another_tenants_job_is_not_found_rather_than_refused(self):
        """Requirement 21.4: existence does not leak. A 403 would confirm the identifier
        names a real job."""
        store = with_jobs(a_job())

        with pytest.raises(TS.TrainingJobNotFound):
            await TS.load_owned_training_job(intruder(), "job_0001", sb=store)
        with pytest.raises(TS.TrainingJobNotFound):
            await TS.training_job_report(intruder(), "job_0001", sb=store)

    async def test_a_job_that_does_not_exist_gets_the_same_answer(self):
        store = with_jobs(a_job())

        with pytest.raises(TS.TrainingJobNotFound):
            await TS.training_job_report(owner(), "job_does_not_exist", sb=store)

    async def test_another_tenants_version_lists_no_jobs_rather_than_refusing(self):
        store = with_jobs(a_job())

        listing = await TS.list_training_job_reports(intruder(), "ver_1", sb=store)

        assert listing["jobs"] == []
        assert listing["count"] == 0

    async def test_a_row_that_slipped_past_the_filter_is_still_refused(self):
        """The last line before another tenant's run would be described. Unreachable with
        the filter above and with RLS in force - asserted anyway."""

        class Leaky(ModelStore):
            def _match(self, query):
                if query.table == "training_jobs":
                    return list(self.tables["training_jobs"])
                return super()._match(query)

        store = Leaky(with_jobs(a_job(user_id="usr_someone_else")).tables)

        with pytest.raises(TS.TrainingJobNotFound):
            await TS.load_owned_training_job(owner(), "job_0001", sb=store)

    async def test_a_listing_drops_a_row_that_is_not_this_users(self):
        class Leaky(ModelStore):
            def _match(self, query):
                if query.table == "training_jobs":
                    return list(self.tables["training_jobs"])
                return super()._match(query)

        store = Leaky(
            with_jobs(
                a_job(id="job_mine"), a_job(id="job_theirs", user_id="usr_someone_else")
            ).tables
        )

        listing = await TS.list_training_job_reports(owner(), "ver_1", sb=store)

        assert [job["job_id"] for job in listing["jobs"]] == ["job_mine"]

    async def test_an_anonymous_caller_reads_nothing(self):
        store = with_jobs(a_job())

        with pytest.raises(TS.TrainingJobNotFound):
            await TS.load_owned_training_job({}, "job_0001", sb=store)
        assert (await TS.list_training_job_reports({}, "ver_1", sb=store))["jobs"] == []

    async def test_a_listing_is_ordered_so_polling_does_not_flicker(self):
        """PostgREST returns rows in no guaranteed order without an ORDER BY."""
        store = with_jobs(
            a_job(id="job_old", node_id="n_a", created_at="2026-01-01T00:00:00+00:00"),
            a_job(id="job_new", node_id="n_b", created_at="2026-01-02T00:00:00+00:00"),
        )

        first = await TS.list_training_job_reports(owner(), "ver_1", sb=store)
        store.tables["training_jobs"].reverse()
        second = await TS.list_training_job_reports(owner(), "ver_1", sb=store)

        assert [job["job_id"] for job in first["jobs"]] == ["job_new", "job_old"]
        assert first["jobs"] == second["jobs"]

    async def test_a_deactivated_model_row_is_not_reported_as_the_jobs_model(self):
        """A superseded row keeps its own artifact and its own checksum, but the model the
        node currently resolves to is the active one."""
        store = with_jobs(a_job(id="job_1", status="COMPLETED"))
        store.tables["model_versions"] = [
            a_row(id="mv_old", training_job_id="job_1", is_active=False)
        ]

        report = await TS.training_job_report(owner(), "job_1", sb=store)

        assert report["model_bound"] is False

    async def test_another_tenants_model_row_is_not_reported_either(self):
        store = with_jobs(a_job(id="job_1", status="COMPLETED"))
        store.tables["model_versions"] = [
            a_row(id="mv_1", training_job_id="job_1", user_id="usr_someone_else")
        ]

        report = await TS.training_job_report(owner(), "job_1", sb=store)

        assert report["model_bound"] is False


# ═══════════════════════════════════════════════════════════════════════════
# 8. The unapplied migration degrades - it never 500s
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestTheUnappliedMigrationDegrades:
    async def test_one_job_is_not_found_and_the_warning_names_004d(self, caplog):
        absent = ModelStore.seeded(with_training_jobs=False)

        with caplog.at_level(logging.WARNING):
            with pytest.raises(TS.TrainingJobNotFound):
                await TS.training_job_report(owner(), "job_0001", sb=absent)

        assert "004d_training_and_models.sql" in caplog.text

    async def test_the_listing_is_empty_and_the_warning_names_004d(self, caplog):
        absent = ModelStore.seeded(with_training_jobs=False)

        with caplog.at_level(logging.WARNING):
            listing = await TS.list_training_job_reports(owner(), "ver_1", sb=absent)

        assert listing == {
            "version_id": "ver_1",
            "count": 0,
            "jobs": [],
            "report_version": TS.TRAINING_STATUS_VERSION,
        }
        assert "004d_training_and_models.sql" in caplog.text

    async def test_an_absent_model_versions_table_reports_the_job_as_unbound(self, caplog):
        """A completed job whose model table is missing is reported honestly - the run
        happened, the model cannot be described. An unreported model is an honest absence;
        an invented one is not."""
        store = FakeSupabase.seeded()
        store.tables["training_jobs"] = [a_job(id="job_1", status="COMPLETED")]

        with caplog.at_level(logging.WARNING):
            report = await TS.training_job_report(owner(), "job_1", sb=store)

        assert report["status"] == "COMPLETED"
        assert report["model_bound"] is False
        assert "004d_training_and_models.sql" in caplog.text

    async def test_a_failure_that_is_not_a_missing_table_still_propagates(self):
        """Deliberately narrow: a read that fails loudly beats one that reports an empty
        status surface for a job that is really running."""

        class Broken(ModelStore):
            def run(self, query):
                if query.op == "select" and query.table == "training_jobs":
                    raise RuntimeError("connection reset by peer")
                return super().run(query)

        store = Broken(with_jobs(a_job()).tables)

        with pytest.raises(RuntimeError, match="connection reset"):
            await TS.load_owned_training_job(owner(), "job_0001", sb=store)
        with pytest.raises(RuntimeError, match="connection reset"):
            await TS.list_training_job_reports(owner(), "ver_1", sb=store)

    async def test_a_postgrest_error_body_naming_the_missing_relation_degrades(self, caplog):
        """PostgREST answers 200 with an ``error`` body as often as it raises."""

        class ErrorBody(ModelStore):
            def run(self, query):
                if query.op == "select" and query.table == "training_jobs":
                    from tests.test_training_worker import FakeResult

                    result = FakeResult([])
                    result.error = (
                        'relation "public.training_jobs" does not exist '
                        "(SQLSTATE 42P01)"
                    )
                    return result
                return super().run(query)

        store = ErrorBody(with_jobs(a_job()).tables)

        with caplog.at_level(logging.WARNING):
            with pytest.raises(TS.TrainingJobNotFound):
                await TS.load_owned_training_job(owner(), "job_0001", sb=store)

        assert "004d_training_and_models.sql" in caplog.text


# ═══════════════════════════════════════════════════════════════════════════
# 9. The HTTP surface - 404 for another tenant, and the controls still on
# ═══════════════════════════════════════════════════════════════════════════

JOBS_PATH = "/api/strategy-operations/training/jobs"


def _decorators_of(function_name):
    """The decorator source lines on ``function_name``, read from the router file.

    From source rather than from ``__wrapped__`` chains, because source is what a reviewer
    changing this file would delete and that is the change this test exists to catch.
    """
    import ast
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "backend_app"
        / "routers"
        / "strategy_operations.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for item in ast.walk(tree):
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == function_name:
            return [ast.unparse(d) for d in item.decorator_list]
    raise AssertionError(f"{function_name} is not defined in {path}")


@pytest.fixture
def client(db):
    """A client for the real app, with the limiter suspended for the duration.

    The limits themselves are NOT weakened - they stay exactly as declared, they are
    restored after every test, and :class:`TestTheControlsStayInForce` asserts each
    decorator is still on its endpoint. Suspending them here only stops a 429 earned by
    another file's requests from being read as a statement about this one.
    """
    from fastapi.testclient import TestClient

    from backend_app.core.dependencies import get_current_user, get_request_supabase
    from backend_app.main import app

    limiter = getattr(app.state, "limiter", None)
    was_enabled = getattr(limiter, "enabled", None)
    if limiter is not None:
        limiter.enabled = False

    app.dependency_overrides[get_current_user] = lambda: owner()
    app.dependency_overrides[get_request_supabase] = lambda: db
    try:
        with patch.object(S, "create_request_supabase_async", AsyncMock(return_value=db)):
            with patch.object(W, "_worker_client", AsyncMock(return_value=db)):
                yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        if limiter is not None and was_enabled is not None:
            limiter.enabled = was_enabled


class TestTheDesignedPathsExist:
    def test_both_reads_are_mounted_as_gets(self):
        from backend_app.main import app

        found = {
            route.path: route.methods
            for route in app.routes
            if getattr(route, "path", None) in {JOBS_PATH, f"{JOBS_PATH}/{{job_id}}"}
        }

        assert set(found) == {JOBS_PATH, f"{JOBS_PATH}/{{job_id}}"}, found
        for path, methods in found.items():
            assert "GET" in methods, path

    def test_the_listing_is_declared_before_the_parameterised_route(self):
        """Otherwise ``version_id`` could be read as a path segment."""
        from backend_app.main import app

        order = [
            route.path
            for route in app.routes
            if getattr(route, "path", "").startswith(JOBS_PATH)
        ]

        assert order.index(JOBS_PATH) < order.index(f"{JOBS_PATH}/{{job_id}}")


class TestTheControlsStayInForce:
    @pytest.mark.parametrize(
        "endpoint", ["list_training_jobs", "get_training_job"]
    )
    def test_both_reads_require_an_authenticated_caller(self, endpoint):
        import inspect

        from backend_app.routers import strategy_operations as R

        signature = inspect.signature(getattr(R, endpoint))
        assert "user" in signature.parameters, endpoint
        assert "get_current_user" in str(signature.parameters["user"].default)

    @pytest.mark.parametrize(
        "endpoint", ["list_training_jobs", "get_training_job"]
    )
    def test_both_reads_keep_a_rate_limit(self, endpoint):
        assert any("limiter.limit" in line for line in _decorators_of(endpoint)), endpoint


class TestTheHttpExchange:
    def test_the_owner_gets_200_and_requirement_15_3s_fields(self, client, db):
        db.tables["training_jobs"] = [a_job()]

        response = client.get(f"{JOBS_PATH}/job_0001")

        assert response.status_code == 200, response.text
        body = response.json()
        for field in REQUIREMENT_15_3_FIELDS:
            assert field in body, field
        assert body["progress"] == pytest.approx(0.4)
        assert body["cancellable"] is True

    def test_another_tenants_job_is_404_and_not_403(self, client, db):
        """The distinction matters: a 403 confirms the identifier names a real job, which
        is an existence oracle over other tenants' job ids (Requirement 21.4)."""
        db.tables["training_jobs"] = [a_job(user_id="usr_someone_else")]

        response = client.get(f"{JOBS_PATH}/job_0001")

        assert response.status_code == 404
        assert response.status_code != 403
        assert response.json()["detail"]["error"] == "TRAINING_JOB_NOT_FOUND"

    def test_a_job_that_does_not_exist_answers_identically(self, client, db):
        db.tables["training_jobs"] = [a_job(user_id="usr_someone_else")]

        foreign = client.get(f"{JOBS_PATH}/job_0001")
        absent = client.get(f"{JOBS_PATH}/job_never_existed")

        assert foreign.status_code == absent.status_code == 404
        # Byte-for-byte indistinguishable apart from the echoed identifier, so nothing in
        # the body tells the two apart.
        assert foreign.json()["detail"]["error"] == absent.json()["detail"]["error"]

    def test_an_unapplied_migration_is_404_and_not_500(self, client, db):
        db.tables.pop("training_jobs", None)

        response = client.get(f"{JOBS_PATH}/job_0001")

        assert response.status_code == 404
        assert response.status_code != 500

    def test_the_listing_answers_only_this_users_jobs_for_the_version(self, client, db):
        db.tables["training_jobs"] = [
            a_job(id="job_mine", node_id="n_a"),
            a_job(id="job_theirs", node_id="n_b", user_id="usr_someone_else"),
            a_job(id="job_other_version", node_id="n_c", version_id="ver_2"),
        ]

        response = client.get(JOBS_PATH, params={"version_id": "ver_1"})

        assert response.status_code == 200, response.text
        body = response.json()
        assert [job["job_id"] for job in body["jobs"]] == ["job_mine"]
        assert body["count"] == 1

    def test_the_listing_requires_a_version(self, client):
        assert client.get(JOBS_PATH).status_code == 422

    def test_an_unapplied_migration_lists_nothing_rather_than_500ing(self, client, db):
        db.tables.pop("training_jobs", None)

        response = client.get(JOBS_PATH, params={"version_id": "ver_1"})

        assert response.status_code == 200
        assert response.json()["jobs"] == []

    def test_neither_response_wraps_its_body_in_a_synthetic_status(self, client, db):
        """``status`` on a training job means one of the five states. One key with two
        meanings on sibling endpoints is worse than no envelope at all."""
        db.tables["training_jobs"] = [a_job()]

        one = client.get(f"{JOBS_PATH}/job_0001").json()
        many = client.get(JOBS_PATH, params={"version_id": "ver_1"}).json()

        assert one["status"] in S.TRAINING_JOB_STATES
        assert "status" not in many

    def test_no_response_carries_the_worker_identity_or_the_config(self, client, db):
        db.tables["training_jobs"] = [a_job()]

        for response in (
            client.get(f"{JOBS_PATH}/job_0001"),
            client.get(JOBS_PATH, params={"version_id": "ver_1"}),
        ):
            body = response.text
            assert "worker-internal-host-7" not in body
            assert "sk_live_never_served" not in body
            assert "artifact_uri" not in body


# ═══════════════════════════════════════════════════════════════════════════
# 10. A real run, reported
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestARealRunIsReportedTruthfully:
    async def test_a_queued_job_reports_its_dataset_and_no_estimate(
        self, service, db, reg, window
    ):
        graph, node_id = model_graph(reg)
        await queue_job(service, reg, graph, epochs=4)
        job = db.job()

        report = await TS.training_job_report(owner(), job["id"], sb=db)

        assert report["status"] == "QUEUED"
        assert report["cancellable"] is True
        assert report["progress"] == 0.0
        assert report["eta_seconds"] is None
        assert report["eta_state"] == TS.ETA_ABSENT_NOT_RUNNING
        # The measured figures the gate produced, echoed rather than recomputed.
        assert report["usable_rows"] == job["usable_rows"]
        assert report["feature_columns"] == job["feature_columns"]
        assert report["feature_names"] == list(job["feature_names"])
        assert report["node_id"] == node_id
        assert report["epochs_total"] == 4
        assert report["failure_reason"] is None

    async def test_a_completed_run_reports_full_progress_and_its_bound_model(
        self, service, db, reg, window, store
    ):
        graph, _ = model_graph(reg)
        await queue_job(service, reg, graph, epochs=3)
        install_production_binder()
        job_id = db.job()["id"]

        await W.run_training_job(job_id, sb=db, worker_id="worker-a")
        report = await TS.training_job_report(owner(), job_id, sb=db)

        assert report["status"] == "COMPLETED"
        assert report["progress"] == pytest.approx(1.0)
        assert report["epoch_current"] == 3
        assert report["cancellable"] is False
        assert report["eta_seconds"] is None  # finished runs have no remaining time
        assert report["model_bound"] is True
        assert report["model_version"]["model_version"] == 1
        assert len(report["metrics_history"]) == 3
        for entry in report["metrics_history"]:
            for key, value in entry.items():
                assert isinstance(value, (int, float, bool)) or value is None, (key, value)

    async def test_every_reported_epoch_metric_is_a_scalar_the_worker_wrote(
        self, service, db, reg, window, store
    ):
        graph, _ = model_graph(reg)
        await queue_job(service, reg, graph, epochs=3)
        install_production_binder()
        job_id = db.job()["id"]

        await W.run_training_job(job_id, sb=db, worker_id="worker-a")
        report = await TS.training_job_report(owner(), job_id, sb=db)

        for entry in report["metrics_history"]:
            assert set(entry) <= set(W.EPOCH_METRIC_KEYS), entry

    async def test_a_cancelled_run_is_not_cancellable_and_carries_no_reason(
        self, service, db, reg, window, store
    ):
        """Requirement 15.7: cancellation is not a failure."""
        graph, _ = model_graph(reg)
        await queue_job(service, reg, graph, epochs=6)
        job_id = db.job()["id"]

        def request_cancel(epoch_index):
            if epoch_index == 1:
                db.job()["cancel_requested"] = True

        W.register_training_backend(
            MV.training_backend(
                lambda ctx: GradientDescentTrainer(ctx, on_epoch=request_cancel)
            )
        )
        await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        report = await TS.training_job_report(owner(), job_id, sb=db)

        assert report["status"] == "CANCELLED"
        assert report["cancellable"] is False
        assert report["failure_reason"] is None
        assert report["model_bound"] is False
        # The progress it really reached, not 0 and not 1.
        assert 0.0 < report["progress"] < 1.0

    async def test_a_failed_run_reports_a_classified_reason_and_the_work_it_did(
        self, service, db, reg, window, store
    ):
        graph, _ = model_graph(reg)
        await queue_job(service, reg, graph, epochs=5)
        job_id = db.job()["id"]
        W.register_training_backend(
            MV.training_backend(lambda ctx: GradientDescentTrainer(ctx, fail_at=3))
        )

        await W.run_training_job(job_id, sb=db, worker_id="worker-a")
        report = await TS.training_job_report(owner(), job_id, sb=db)

        assert report["status"] == "FAILED"
        assert report["failure_reason"] in W.FAILURE_REASONS
        assert report["cancellable"] is False
        assert report["model_bound"] is False
        # The two epochs that did finish are still reported.
        assert report["epoch_current"] == 2
        assert len(report["metrics_history"]) == 2

    async def test_one_job_per_model_node_is_listed_for_the_version(
        self, service, db, reg, window
    ):
        graph, node_id = model_graph(reg)
        await queue_job(service, reg, graph, epochs=3)
        version_id = db.job()["version_id"]

        listing = await TS.list_training_job_reports(owner(), version_id, sb=db)

        assert listing["count"] == 1
        assert listing["version_id"] == version_id
        assert listing["jobs"][0]["node_id"] == node_id
        assert listing["report_version"] == TS.TRAINING_STATUS_VERSION

    async def test_the_report_never_writes_to_the_row(self, service, db, reg, window):
        """This is a reporting surface. 004d attaches no ``updated_at`` trigger to
        ``training_jobs``, so the safest way to hold that invariant is to have no write."""
        graph, _ = model_graph(reg)
        await queue_job(service, reg, graph, epochs=3)
        job_id = db.job()["id"]
        before = dict(db.job())
        writes_before = len(db.writes)

        await TS.training_job_report(owner(), job_id, sb=db)
        await TS.list_training_job_reports(owner(), before["version_id"], sb=db)

        assert db.job() == before
        assert len(db.writes) == writes_before

    async def test_the_socket_frame_and_the_endpoint_agree_about_the_estimate(
        self, service, db, reg, window, store, handoffs
    ):
        """The worker's ``training.progress`` frame and the status endpoint compute the
        ETA with the same function, so one cannot show a number while the other says
        "estimating…"."""
        graph, _ = model_graph(reg)
        await queue_job(service, reg, graph, epochs=4)
        install_production_binder()
        job_id = db.job()["id"]

        await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        frames = [
            payload
            for event, payload in handoffs["events"]
            if event == "training.progress"
        ]
        assert frames, "no progress frame was published"
        for frame in frames:
            assert frame["eta_state"] in TS.ETA_STATES
            # The first two epochs cannot have an estimate, whatever the durations were.
            if frame["epoch"] < TS.ETA_MIN_COMPLETED_EPOCHS:
                assert frame["eta_seconds"] is None
                assert frame["eta_state"] == TS.ETA_ABSENT_TOO_FEW_EPOCHS
            # And the frame's progress is the endpoint's rule, not a second copy of it.
            assert frame["progress"] == pytest.approx(
                TS.derive_progress(frame["epoch"], frame["epochs_total"])
            )
