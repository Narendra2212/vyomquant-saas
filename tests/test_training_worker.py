"""
tests/test_training_worker.py

The training worker: what it claims, what it writes, and how a run ends.

Spec: strategy-builder task 6.4 (`design.md` -> Training workflow ->
`PROCEDURE run_training_job`). Requirements 15.7, 15.8, 15.9, 15.10, 16.4, 16.6.

WHAT THIS FILE HOLDS IN PLACE
-----------------------------
Seven properties, each of which would be a real defect if it stopped holding:

1. **The claim is atomic and exclusive.** ``QUEUED -> RUNNING`` happens once; a second
   worker that saw the same Redis hint gets nothing and does not steal the job. The row
   is the queue - the hint is not consulted for exclusion at all.
2. **Caps are re-evaluated before the first epoch** (Requirement 16.4). A row whose
   ``epochs_total`` was written past the cap - which is exactly what a client bypassing
   the API produces - is refused on the worker with the requested and permitted values,
   and global saturation *defers* rather than rejects (Requirement 16.5).
3. **Cancellation stops at an epoch boundary and binds no model** (Requirement 15.7).
   The status becomes ``CANCELLED``, carries no failure reason, and the model binder is
   never reached - structurally, not by convention.
4. **The wall clock is enforced** (Requirement 15.8): ``FAILED`` with
   ``MAX_DURATION_EXCEEDED``, and again no binder.
5. **A stale heartbeat is worker loss** (Requirement 15.9): ``FAILED`` with
   ``WORKER_LOST``, the version back to ``SAVED``, no model bound - and a job that
   heartbeated between the sweep's read and its write is left alone, because the write
   is a compare-and-set on the heartbeat it judged.
6. **Every failure reason is classified** (Requirement 15.10). The reason column is
   always a member of the closed vocabulary and is never the exception's text, including
   for the outlier-rejected window that `market_data_validation` refuses.
7. **Every transition sets ``updated_at`` itself**, because migration 004d deliberately
   attaches no ``BEFORE UPDATE`` trigger to ``training_jobs``.

Plus the two Requirement 16.6 wirings - deterministic seeding proved by reproducibility,
and ``MemoryMonitor`` bounds proved by a refusal - and the ``metrics_history``
data-minimisation rule 004d says its writer has to enforce.

WHAT IS REAL HERE AND WHAT IS A DOUBLE
--------------------------------------
Real: the assembled registry; a canonical graph built from published descriptors; the
compiler; the validator; ``market_data_validation``; ``dag_engine`` executing the feature
pipeline; ``feature_validator``; ``ml_dataset``; the whole of ``ml_training_policy``
including ``resolve_caps``/``enforce_caps``; ``core.ml_safety``'s
``DeterministicEnforcer``, ``MemoryMonitor`` and ``TrainingIsolator``; the whole of
``strategy_service``'s admission path, which is what *creates* the job rows these tests
run; and every line of ``training_worker``.

Doubles, and only these three:

* **The database.** ``FakeSupabase`` from the task 6.3 suite, extended with
  ``strategy_versions`` updates and a record of the filters each write carried so the
  compare-and-set can be asserted rather than assumed. It is a double because there is
  no local PostgreSQL and ``004d_training_and_models.sql`` is unapplied.
* **The candle feed.** ``strategy_service.fetch_training_bars`` is the one I/O boundary,
  replaced with a deterministic window - the same seam task 6.3's tests use.
* **The realtime hand-off**, recorded rather than broadcast, since no WebSocket manager
  is running.

**The trainer is NOT a double.** ``GradientDescentTrainer`` below is a real
multinomial-logistic-regression fit by gradient descent over the real ``(X, y)`` the real
``ml_dataset`` assembled from the real feature pipeline, standardised on train-split
statistics only, reporting real cross-entropy losses. It is installed through
``TrainingBackend``, which is production API - the seam task 6.5 will install its own
trainer into. What is under test is the worker's control of a training run; the trainer
is the run.

WHAT IS NOT VERIFIED HERE
-------------------------
No real ``training_jobs`` table, so ``uq_tj_active_per_node``, the RLS policies and the
CHECK constraints (``chk_tj_status``, ``chk_tj_progress``,
``chk_tj_failed_has_reason``) are exercised against a Python double shaped like them;
live enforcement remains 004d's own VERIFICATION queries. No artifact and no
``model_versions`` row is produced by anything here, because that is task 6.5 - the
binder used below records what it was asked to bind and writes nothing.
"""

import math
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import strategy_service as S
from backend_app.backend import training_worker as W
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag import validator as V
from backend_app.backend.strategy_dag.schema import EdgeSpec, NodeSpec, StrategyGraph
from backend_app.core import ml_safety

USER_ID = "usr_training_owner"
STRATEGY_ID = "stg_training_0001"

#: The one plan that carries the ML-training feature AND a non-zero epoch cap. FREE and
#: BASIC are zero on purpose, so a run on them would be asserting the entitlement layer
#: rather than the worker.
PAID_TIER = "pro"


# ═══════════════════════════════════════════════════════════════════════════
# The registry, the graph and the window - all as task 6.3's suite builds them
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def reg():
    """The real assembled registry. Assembly costs ~150 ms, so it is shared."""
    return registry_module.build_registry()


@pytest.fixture(autouse=True)
def default_hooks():
    """Stages 10b and 11 installed, whatever ran before this file in the suite."""
    V.install_default_stage_hooks()
    yield
    V.install_default_stage_hooks()


@pytest.fixture(autouse=True)
def forget_column_probe():
    S.reset_canonical_column_support()
    yield
    S.reset_canonical_column_support()


@pytest.fixture(autouse=True)
def clean_worker_state():
    """No backend installed, no latched isolation state, default safety config.

    Every one of these is process-global, so leaving one set would make a later test in
    this file - or in another file - pass or fail depending on collection order.
    """
    W.reset_training_backend()
    W.reset_isolation_latch()
    memory_before = ml_safety.MemoryMonitor._config
    training_before = ml_safety.TrainingIsolator._config
    # Process isolation OFF for the loop tests: ``TrainingIsolator`` uses a ``spawn``
    # pool, a closure over a live model is not picklable, and paying a process launch per
    # epoch to prove that would make this file minutes long. The isolation path itself is
    # asserted directly in ``TestIsolationMemoryAndDeterminism`` instead.
    ml_safety.TrainingIsolator.configure(
        ml_safety.TrainingConfig(enable_process_isolation=False)
    )
    yield
    ml_safety.MemoryMonitor._config = memory_before
    ml_safety.TrainingIsolator._config = training_before
    W.reset_training_backend()
    W.reset_isolation_latch()


def node(reg, block_id, **params):
    return NodeSpec.create(block_id, reg[block_id].category, params=params)


def model_graph(reg, *, block_id="xgboost", lags=(1, 2, 3, 4, 5, 6)):
    """data -> ema -> feat_lag -> model -> gt(vs constant) -> buy.

    A pipeline the platform would actually run, so the features the worker trains on are
    produced by the executors that would compute them when the strategy trades.
    """
    data = node(reg, "ohlcv_feed", symbol="BTC/USDT", timeframe="15m",
                market_type="spot", mode="streaming")
    ema = node(reg, "ema", window=20, source="close")
    lag = node(reg, "feat_lag", lags=list(lags))
    model = node(reg, block_id)
    const = node(reg, "constant", value=0.5)
    gate = node(reg, "gt")
    action = node(reg, "action_buy_market", quantity_type="percent_of_equity",
                  quantity=0.25)
    graph = StrategyGraph(
        nodes=[data, ema, lag, model, const, gate, action],
        edges=[
            EdgeSpec.create(data.id, "close", ema.id, "series"),
            EdgeSpec.create(ema.id, "value", lag.id, "series"),
            EdgeSpec.create(lag.id, "matrix", model.id, "features"),
            EdgeSpec.create(model.id, "prediction", gate.id, "left"),
            EdgeSpec.create(const.id, "value", gate.id, "right"),
            EdgeSpec.create(gate.id, "out", action.id, "signal"),
        ],
    )
    return graph, model.id


def bounded_bars(count, *, step_ms=900_000, start=1_600_000_000_000):
    """A deterministic, evenly spaced, OHLC-consistent window.

    Shaped to be *acceptable to* ``market_data_validation``, which is REUSED AS-IS and
    whose thresholds are not relaxed from the training path: evenly spaced so
    ``GapHandler`` finds no gap, smooth and bounded so ``OutlierDetector`` finds no
    3-sigma close, strictly positive so ``CandleIntegrityValidator`` finds no violation.
    """
    rows = []
    for i in range(int(count)):
        open_price = 100.0 + 10.0 * math.sin(i / 40.0)
        close_price = 100.0 + 10.0 * math.sin((i + 1) / 40.0)
        rows.append(
            [
                start + i * step_ms,
                open_price,
                max(open_price, close_price) + 0.05,
                min(open_price, close_price) - 0.05,
                close_price,
                1000.0,
            ]
        )
    return rows


def spiky_bars(count, **kwargs):
    """``bounded_bars`` with one violent close, which the 3-sigma rule rejects."""
    rows = bounded_bars(count, **kwargs)
    middle = len(rows) // 2
    rows[middle][2] = 100_000.0
    rows[middle][4] = 99_000.0
    return rows


@pytest.fixture
def window(monkeypatch):
    """Replace the one I/O boundary with a deterministic window."""
    state = {"calls": [], "generator": bounded_bars}

    async def _fetch(symbol, timeframe, bars):
        state["calls"].append({"symbol": symbol, "timeframe": timeframe, "bars": int(bars)})
        return state["generator"](int(bars))

    monkeypatch.setattr(S, "fetch_training_bars", _fetch)
    return state


@pytest.fixture(autouse=True)
def handoffs(monkeypatch):
    """Record the best-effort hand-offs instead of needing Redis or a socket."""
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


# ═══════════════════════════════════════════════════════════════════════════
# The database double
# ═══════════════════════════════════════════════════════════════════════════


class MissingRelation(Exception):
    """What PostgreSQL says when a table does not exist (SQLSTATE 42P01)."""

    def __init__(self, table):
        super().__init__(
            f'relation "public.{table}" does not exist (SQLSTATE 42P01 undefined_table)'
        )


class UniqueViolation(Exception):
    """What PostgreSQL says when a unique index rejects a row (SQLSTATE 23505)."""

    def __init__(self, index):
        super().__init__(
            f'duplicate key value violates unique constraint "{index}" '
            f"(SQLSTATE 23505 unique_violation)"
        )


class FakeResult:
    def __init__(self, data=None):
        self.data = [] if data is None else data
        self.error = None


class FakeQuery:
    """A PostgREST-shaped builder. ``execute()`` is a coroutine, as the async client's is."""

    def __init__(self, db, table, op, payload=None):
        self.db = db
        self.table = table
        self.op = op
        self.payload = payload
        self.filters = []
        self._limit = None

    def eq(self, column, value):
        self.filters.append(("eq", column, value))
        return self

    def neq(self, column, value):
        self.filters.append(("neq", column, value))
        return self

    def in_(self, column, values):
        self.filters.append(("in", column, list(values)))
        return self

    def lt(self, column, value):
        self.filters.append(("lt", column, value))
        return self

    def limit(self, count):
        self._limit = int(count)
        return self

    def order(self, *args, **kwargs):
        return self

    async def execute(self):
        return self.db.run(self)


class FakeTable:
    def __init__(self, db, name):
        self.db = db
        self.name = name

    def select(self, *args, **kwargs):
        return FakeQuery(self.db, self.name, "select")

    def insert(self, payload):
        return FakeQuery(self.db, self.name, "insert", payload)

    def update(self, payload):
        return FakeQuery(self.db, self.name, "update", payload)


class FakeSupabase:
    """An in-memory store with the constraints these tests are about, and nothing else.

    Enforced, because the tests turn on them:

    * a table absent from ``tables`` raises :class:`MissingRelation`, which is how the
      unapplied-migration path is exercised;
    * ``uq_tj_active_per_node``'s **partial** predicate on ``training_jobs`` inserts;
    * ``chk_tj_failed_has_reason`` and ``chk_tj_progress`` on writes, because "a FAILED
      job always carries a reason" and "progress stays in [0,1]" are properties this file
      asserts and the real table would refuse a violation rather than store it.

    Not enforced, and not pretended to be: RLS, foreign keys, column types. Those belong
    to 004d's own VERIFICATION queries and to task 8.7's isolation matrix.

    ``writes`` records every UPDATE with the filters it carried, so a compare-and-set can
    be asserted directly instead of inferred from its effect.
    """

    def __init__(self, tables=None):
        self.tables = {
            name: [dict(row) for row in rows] for name, rows in (tables or {}).items()
        }
        self.touched = []
        self.writes = []

    @classmethod
    def seeded(cls, *, with_training_jobs=True, tier=PAID_TIER):
        tables = {
            "strategies": [
                {"id": STRATEGY_ID, "user_id": USER_ID, "current_version": "v1.0"}
            ],
            "strategy_versions": [],
            "profiles": [{"id": USER_ID, "subscription_tier": tier}],
        }
        if with_training_jobs:
            tables["training_jobs"] = []
        return cls(tables)

    def table(self, name):
        self.touched.append(name)
        return FakeTable(self, name)

    def rows(self, name):
        return self.tables.get(name, [])

    @property
    def jobs(self):
        return self.tables.get("training_jobs", [])

    @property
    def versions(self):
        return self.tables.get("strategy_versions", [])

    def job(self, job_id=None):
        """One job row, by id or the only one there is."""
        rows = self.jobs
        if job_id is None:
            assert len(rows) == 1, f"expected exactly one job row, found {len(rows)}"
            return rows[0]
        for row in rows:
            if row.get("id") == job_id:
                return row
        raise AssertionError(f"no job row {job_id!r}")

    def updates_to(self, table):
        return [entry for entry in self.writes if entry["table"] == table]

    def _match(self, query):
        if query.table not in self.tables:
            raise MissingRelation(query.table)
        matched = []
        for row in self.tables[query.table]:
            keep = True
            for kind, column, value in query.filters:
                actual = row.get(column)
                if kind == "eq" and actual != value:
                    keep = False
                elif kind == "neq" and actual == value:
                    keep = False
                elif kind == "in" and actual not in value:
                    keep = False
                elif kind == "lt" and not (actual is not None and actual < value):
                    keep = False
                if not keep:
                    break
            if keep:
                matched.append(row)
        return matched

    def _assert_uq_tj_active_per_node(self, row):
        if str(row.get("status", "QUEUED")) not in S.TRAINING_JOB_LIVE_STATES:
            return
        for existing in self.tables["training_jobs"]:
            if (
                existing.get("version_id") == row.get("version_id")
                and existing.get("node_id") == row.get("node_id")
                and str(existing.get("status")) in S.TRAINING_JOB_LIVE_STATES
            ):
                raise UniqueViolation("uq_tj_active_per_node")

    @staticmethod
    def _assert_training_job_checks(row):
        """``chk_tj_failed_has_reason`` and ``chk_tj_progress``, verbatim."""
        if str(row.get("status")) == "FAILED" and row.get("failure_reason") is None:
            raise Exception(
                'new row violates check constraint "chk_tj_failed_has_reason"'
            )
        progress = row.get("progress")
        if progress is not None and not (0 <= float(progress) <= 1):
            raise Exception('new row violates check constraint "chk_tj_progress"')

    def run(self, query):
        if query.op == "select":
            matched = self._match(query)
            if query._limit is not None:
                matched = matched[: query._limit]
            return FakeResult([dict(row) for row in matched])

        if query.op == "insert":
            if query.table not in self.tables:
                raise MissingRelation(query.table)
            row = dict(query.payload)
            if query.table == "training_jobs":
                row.setdefault("status", "QUEUED")
                row.setdefault("cancel_requested", False)
                self._assert_uq_tj_active_per_node(row)
                self._assert_training_job_checks(row)
            self.tables[query.table].append(row)
            return FakeResult([dict(row)])

        if query.op == "update":
            matched = self._match(query)
            self.writes.append(
                {
                    "table": query.table,
                    "payload": dict(query.payload),
                    "filters": list(query.filters),
                    "matched": len(matched),
                }
            )
            for row in matched:
                candidate = {**row, **query.payload}
                if query.table == "training_jobs":
                    self._assert_training_job_checks(candidate)
                row.update(query.payload)
            return FakeResult([dict(row) for row in matched])

        raise AssertionError(f"unsupported operation {query.op!r}")  # pragma: no cover


@pytest.fixture
def db():
    return FakeSupabase.seeded()


def owner(plan=PAID_TIER):
    return {
        "id": USER_ID,
        "email": "owner@example.com",
        "role": "authenticated",
        "access_token": "token_owner",
        "plan": plan,
    }


@pytest.fixture
def service(db):
    """The real :class:`StrategyService`, wired to the store."""
    from backend_app.backend.strategy_service import StrategyService

    with patch.object(S, "create_request_supabase_async", AsyncMock(return_value=db)):
        yield StrategyService()


async def queue_job(service, reg, graph, **training):
    """Create a real version and a real ``QUEUED`` job row through task 6.3's own path.

    The job the worker runs is therefore the job the platform writes, config and all -
    not a hand-made row shaped like one.
    """
    result = await service.create_version_and_maybe_train(
        owner(), STRATEGY_ID, graph.to_dict(), registry=reg,
        training_cfg=training or None,
    )
    assert result["training"]["state"] == S.TRAINING_QUEUED, result["training"]
    return result


# ═══════════════════════════════════════════════════════════════════════════
# A real trainer, installed through the production seam
# ═══════════════════════════════════════════════════════════════════════════


class GradientDescentTrainer:
    """Multinomial logistic regression, one gradient step per epoch. A real fit.

    Not a stub: it standardises the real feature matrix using **train-split statistics
    only** (so nothing leaks backwards from validation), fits real weights by gradient
    descent on the train split, and reports real cross-entropy on train and validation.
    Its initial weights come from ``np.random``, which is what lets
    ``test_two_runs_with_the_same_seed_are_identical`` prove that
    ``DeterministicEnforcer.deterministic_context`` is actually wrapping the run.

    ``on_epoch`` is the hook the cancellation and worker-loss tests use to change the
    world mid-run - the only way to exercise "a flag set by another process while this one
    is training" without a second process.
    """

    def __init__(self, ctx, *, learning_rate=0.5, on_epoch=None, fail_at=None):
        self.ctx = ctx
        self.learning_rate = float(learning_rate)
        self.on_epoch = on_epoch
        self.fail_at = fail_at

        dataset = ctx.dataset
        splits = ctx.splits
        raw = np.nan_to_num(np.asarray(dataset.X, dtype=float), nan=0.0,
                            posinf=0.0, neginf=0.0)
        train = slice(splits.train.start, splits.train.stop)
        mean = raw[train].mean(axis=0)
        std = raw[train].std(axis=0)
        std[std == 0] = 1.0
        self.X = np.hstack([(raw - mean) / std, np.ones((raw.shape[0], 1))])

        labels = np.asarray(dataset.y)
        # Classes from the TRAIN split only; a class first seen in validation is masked
        # out of scoring rather than silently teaching the model that it exists.
        self.classes = np.unique(labels[train])
        self.y = labels
        self.slices = {
            "train": train,
            "val": slice(splits.val.start, splits.val.stop),
            "test": slice(splits.test.start, splits.test.stop),
        }
        self.W = np.random.normal(0.0, 0.01, (self.X.shape[1], len(self.classes)))
        self.epochs_seen = []

    # -- the fit ---------------------------------------------------------
    def _onehot(self, labels):
        out = np.zeros((len(labels), len(self.classes)))
        for column, value in enumerate(self.classes):
            out[:, column] = (labels == value).astype(float)
        return out

    def _probabilities(self, features):
        scores = features @ self.W
        scores -= scores.max(axis=1, keepdims=True)
        exp = np.exp(scores)
        return exp / np.clip(exp.sum(axis=1, keepdims=True), 1e-12, None)

    def _cross_entropy(self, split):
        window = self.slices[split]
        features = self.X[window]
        labels = self.y[window]
        keep = np.isin(labels, self.classes)
        if not keep.any():
            return float("nan")
        probabilities = self._probabilities(features[keep])
        truth = self._onehot(labels[keep])
        return float(-np.mean(np.log(np.clip((probabilities * truth).sum(axis=1), 1e-12, None))))

    def train_epoch(self, epoch):
        if self.fail_at is not None and epoch == self.fail_at:
            raise ZeroDivisionError("the trainer blew up on purpose")
        window = self.slices["train"]
        features = self.X[window]
        truth = self._onehot(self.y[window])
        gradient = features.T @ (self._probabilities(features) - truth) / len(features)
        self.W -= self.learning_rate * gradient
        self.epochs_seen.append(int(epoch))
        if self.on_epoch is not None:
            self.on_epoch(epoch)
        return {"loss": self._cross_entropy("train"), "val_loss": self._cross_entropy("val")}

    def evaluate(self, split):
        window = self.slices[split]
        labels = self.y[window]
        keep = np.isin(labels, self.classes)
        predicted = self.classes[self._probabilities(self.X[window][keep]).argmax(axis=1)]
        return {
            "loss": self._cross_entropy(split),
            "accuracy": float((predicted == labels[keep]).mean()) if keep.any() else 0.0,
        }

    @property
    def model(self):
        return {"weights": self.W, "classes": self.classes.tolist()}


class RecordingBinder:
    """Stands in for task 6.5's model-versioning step and writes nothing.

    It records that it was asked to bind, which is what lets the cancellation and
    duration tests assert **no model was bound** rather than assert around it. Returning
    a mapping is enough for the worker to write ``COMPLETED``; producing an artifact, a
    checksum and a ``model_versions`` row is task 6.5's, and deliberately not simulated
    here.
    """

    def __init__(self, *, refuse=False):
        self.calls = []
        self.refuse = refuse

    def __call__(self, ctx, model):
        self.calls.append({"job_id": ctx.job_id, "node_id": ctx.node_id, "model": model})
        if self.refuse:
            return {}
        return {
            "bound": True,
            "node_id": ctx.node_id,
            "version_id": ctx.version_id,
            "seam": "task 6.5",
        }


def install(trainer_factory=None, *, binder=None, name="test-backend", **trainer_kwargs):
    """Install a backend through the production seam and return the binder."""
    recorded = binder if binder is not None else RecordingBinder()
    factory = trainer_factory or (
        lambda ctx: GradientDescentTrainer(ctx, **trainer_kwargs)
    )
    W.register_training_backend(
        W.TrainingBackend(trainer=factory, binder=recorded, name=name)
    )
    return recorded


def iso(moment):
    return moment.isoformat()


def ago(seconds):
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Requirement 15.10 - a CLASSIFIED failure reason, never a generic string
# ═══════════════════════════════════════════════════════════════════════════


class TestFailureReasonsAreClassified:
    """The reason column draws from a closed vocabulary and never from an exception.

    This is the property that makes a builder able to *render* a failure. A column
    holding ``"list index out of range"`` is not a classified reason, and the whole point
    of Requirement 15.10 is that a client never has to pattern-match one.
    """

    def test_the_vocabulary_is_closed_and_names_both_required_reasons(self):
        assert W.FAILURE_MAX_DURATION_EXCEEDED == "MAX_DURATION_EXCEEDED"
        assert W.FAILURE_WORKER_LOST == "WORKER_LOST"
        assert W.FAILURE_MAX_DURATION_EXCEEDED in W.FAILURE_REASONS
        assert W.FAILURE_WORKER_LOST in W.FAILURE_REASONS
        assert isinstance(W.FAILURE_REASONS, frozenset)

    def test_the_admission_vocabulary_is_reused_not_restated(self):
        """One vocabulary for the builder, not two that drift.

        A window that degrades between admission and execution fails on the worker for
        the same measured reason the API would have blocked it for, so the constants are
        task 6.3's own.
        """
        assert W.FAILURE_DATA_QUALITY is S.REASON_DATA_QUALITY
        assert W.FAILURE_CAP_EXCEEDED is S.REASON_CAP_EXCEEDED
        assert W.FAILURE_ML_REQUIREMENTS is S.REASON_ML_REQUIREMENTS
        assert W.FAILURE_FEATURES is S.REASON_FEATURES

    def test_a_free_text_message_cannot_be_written_as_a_reason(self):
        with pytest.raises(W.UnclassifiedFailureReason):
            W.assert_classified_reason("training failed: list index out of range")
        with pytest.raises(W.UnclassifiedFailureReason):
            W.assert_classified_reason("")
        assert W.assert_classified_reason(W.FAILURE_WORKER_LOST) == "WORKER_LOST"

    def test_a_rejected_window_classifies_as_data_quality_with_the_validators_words(self):
        """The known ``OutlierDetector`` strictness, mapped rather than relaxed.

        ``market_data_validation.OutlierDetector._z_score_filter`` rejects a window with
        any close beyond 3 sigma and is REUSED AS-IS. The rejection arrives as a
        ``TrainingBlocked(DATA_QUALITY)``; classification keeps that reason and carries
        the validator's own message into the detail rather than into the reason column.
        """
        blocked = S.TrainingBlocked(
            S.REASON_DATA_QUALITY,
            "rejected by the platform's data validator: outlier at index 900",
            {"quality_level": "REJECTED", "validator_error": "3-sigma close"},
        )
        reason, detail = W.classify_failure(blocked)

        assert reason == W.FAILURE_DATA_QUALITY
        assert detail["quality_level"] == "REJECTED"
        assert "3-sigma" in detail["validator_error"]

    def test_a_cap_rejection_carries_the_requested_and_permitted_values(self):
        from backend_app.backend.ml_training_policy import CAP_MAX_EPOCHS, CapExceeded

        reason, detail = W.classify_failure(
            CapExceeded(CAP_MAX_EPOCHS, 5000, 300, unit="rounds", block_id="xgboost")
        )

        assert reason == W.FAILURE_CAP_EXCEEDED
        assert detail["cap"] == CAP_MAX_EPOCHS
        assert detail["requested"] == 5000
        assert detail["allowed"] == 300

    def test_a_duration_overrun_carries_elapsed_and_allowed(self):
        reason, detail = W.classify_failure(W.MaxDurationExceeded(2400.5, 1800))

        assert reason == W.FAILURE_MAX_DURATION_EXCEEDED
        assert detail["elapsed_seconds"] == pytest.approx(2400.5)
        assert detail["allowed_seconds"] == 1800

    def test_a_memory_refusal_classifies_as_memory_exceeded(self):
        assert W.classify_failure(MemoryError("too big"))[0] == W.FAILURE_MEMORY_EXCEEDED
        assert (
            W.classify_failure(W.MemoryBoundExceeded("too big"))[0]
            == W.FAILURE_MEMORY_EXCEEDED
        )

    def test_the_two_seam_failures_name_task_65(self):
        for error in (W.TrainerUnavailable("no trainer"), W.ModelPersistenceUnavailable("no binder")):
            reason, detail = W.classify_failure(error)
            assert reason in W.FAILURE_REASONS
            assert "6.5" in detail["next_task"]

    def test_an_unexpected_exception_is_still_classified_and_keeps_its_text_aside(self):
        """The catch-all is a classification, not a message.

        The exception's own words survive in ``detail`` - an operator needs them - but
        they never become the ``failure_reason`` a client reads.
        """
        reason, detail = W.classify_failure(IndexError("list index out of range"))

        assert reason == W.FAILURE_TRAINING_RUNTIME_ERROR
        assert reason in W.FAILURE_REASONS
        assert detail["failure"] == "IndexError"
        assert detail["message"] == "list index out of range"
        assert reason != "list index out of range"


# ═══════════════════════════════════════════════════════════════════════════
# 2. 004d's data-minimisation rule, enforced by its writer
# ═══════════════════════════════════════════════════════════════════════════


class TestMetricsHistoryHoldsScalarsOnly:
    """``metrics_history`` never carries a prediction vector or a feature value.

    004d's header states the rule and then states that SQL cannot enforce it - "the
    enforcement itself belongs to the writer in tasks 6.4 and 6.6". This is that writer,
    so the rule is asserted here rather than hoped for.
    """

    def test_scalars_survive(self):
        clean = W.sanitize_epoch_metrics(
            {"loss": 0.42, "val_loss": 0.51, "learning_rate": 0.05}, epoch=3
        )

        assert clean == {"loss": 0.42, "val_loss": 0.51, "learning_rate": 0.05, "epoch": 3}

    def test_a_forbidden_key_is_dropped_even_when_its_value_is_a_scalar(self):
        """A single float named ``prediction`` is still model output."""
        clean = W.sanitize_epoch_metrics({"loss": 0.1, "prediction": 0.99, "y_true": 1})

        assert clean == {"loss": 0.1}

    def test_a_vector_is_dropped_even_under_an_innocent_key(self):
        clean = W.sanitize_epoch_metrics(
            {"loss": 0.1, "curve": [0.9, 0.8, 0.7], "matrix": np.zeros((4, 3))}
        )

        assert clean == {"loss": 0.1}

    def test_nan_and_infinity_become_null_rather_than_breaking_the_write(self):
        clean = W.sanitize_epoch_metrics(
            {"loss": float("nan"), "val_loss": float("inf"), "metric": 0.5}
        )

        assert clean == {"loss": None, "val_loss": None, "metric": 0.5}

    def test_a_numpy_scalar_is_a_scalar(self):
        clean = W.sanitize_epoch_metrics({"loss": np.float64(0.25), "epochs": np.int64(3)})

        assert clean == {"loss": 0.25, "epochs": 3}


# ═══════════════════════════════════════════════════════════════════════════
# 3. The claim: atomic, exclusive, and against the table rather than Redis
# ═══════════════════════════════════════════════════════════════════════════


class TestTheClaimIsAtomicAndExclusive:
    """``QUEUED -> RUNNING`` happens once.

    The mechanism is a conditional UPDATE carrying ``status = 'QUEUED'``, which is what
    makes two workers holding the same Redis hint safe: PostgreSQL serialises them on the
    row lock and the loser re-evaluates the predicate against the already-updated row.
    A read-then-write would let both proceed, which is why the filter is asserted here
    and not just its effect.
    """

    @pytest.mark.asyncio
    async def test_a_claim_sets_running_a_worker_and_a_heartbeat(self, service, reg, db, window):
        await queue_job(service, reg, model_graph(reg)[0], epochs=3)
        job_id = db.job()["id"]

        claimed = await W.claim_training_job(db, job_id, "worker-a")

        assert claimed is not None
        row = db.job()
        assert row["status"] == "RUNNING"
        assert row["worker_id"] == "worker-a"
        assert row["last_heartbeat"], "a claimed job must be able to prove it is alive"
        assert row["started_at"], "the duration cap measures from started_at"
        # 004d attaches no BEFORE UPDATE trigger to training_jobs, so the WRITE has to
        # carry the column. Asserted from the payload rather than by comparing the stored
        # value against the insert's: Windows' wall clock has ~16 ms granularity, so two
        # writes in the same tick legitimately produce the same ISO string.
        claim_write = db.updates_to("training_jobs")[-1]
        assert "updated_at" in claim_write["payload"]
        assert W.parse_timestamp(claim_write["payload"]["updated_at"]) is not None

    @pytest.mark.asyncio
    async def test_the_claim_is_conditional_on_the_queued_status(self, service, reg, db, window):
        await queue_job(service, reg, model_graph(reg)[0], epochs=3)
        job_id = db.job()["id"]

        await W.claim_training_job(db, job_id, "worker-a")

        claim_writes = [
            entry for entry in db.updates_to("training_jobs")
            if entry["payload"].get("status") == "RUNNING"
        ]
        assert len(claim_writes) == 1
        assert ("eq", "status", "QUEUED") in claim_writes[0]["filters"], (
            "without the status predicate the claim is a read-then-write race"
        )
        assert ("eq", "id", job_id) in claim_writes[0]["filters"]

    @pytest.mark.asyncio
    async def test_a_second_worker_gets_nothing_and_does_not_steal_the_job(
        self, service, reg, db, window
    ):
        await queue_job(service, reg, model_graph(reg)[0], epochs=3)
        job_id = db.job()["id"]

        first = await W.claim_training_job(db, job_id, "worker-a")
        second = await W.claim_training_job(db, job_id, "worker-b")

        assert first is not None
        assert second is None, "two workers must not both run one job"
        assert db.job()["worker_id"] == "worker-a"

    @pytest.mark.asyncio
    async def test_a_finished_job_cannot_be_claimed(self, service, reg, db, window):
        await queue_job(service, reg, model_graph(reg)[0], epochs=3)
        job_id = db.job()["id"]
        db.job()["status"] = "COMPLETED"

        assert await W.claim_training_job(db, job_id, "worker-a") is None

    @pytest.mark.asyncio
    async def test_a_reclaim_does_not_reset_started_at(self, service, reg, db, window):
        """Crashing must not buy a fresh duration budget.

        Requirement 15.8 measures elapsed duration from ``started_at``. If a re-claim
        reset it, a job could outrun any cap simply by being re-queued often enough.
        """
        await queue_job(service, reg, model_graph(reg)[0], epochs=3)
        job_id = db.job()["id"]

        await W.claim_training_job(db, job_id, "worker-a")
        original = db.job()["started_at"]
        await W.release_training_job(db, job_id, "worker-a", reason="test")
        await W.claim_training_job(db, job_id, "worker-b")

        assert db.job()["started_at"] == original

    @pytest.mark.asyncio
    async def test_an_unapplied_migration_degrades_with_a_warning_naming_004d(self, caplog):
        """No 500, no crash on every poll - a warning that says which file to apply."""
        empty = FakeSupabase.seeded(with_training_jobs=False)

        with caplog.at_level("WARNING"):
            claimed = await W.claim_training_job(empty, "job-1", "worker-a")

        assert claimed is None
        assert "004d_training_and_models.sql" in caplog.text

    @pytest.mark.asyncio
    async def test_the_authoritative_queue_is_the_table_not_the_hint(
        self, service, reg, db, window
    ):
        """A job whose Redis hint was lost is still picked up.

        ``enqueue_training_job`` is best effort precisely because of this: the ``QUEUED``
        row is the queue, so a Redis that was down delays a job rather than losing it.
        """
        await queue_job(service, reg, model_graph(reg)[0], epochs=3)
        job_id = db.job()["id"]

        assert await W.next_queued_job_id(db) == job_id

        await W.claim_training_job(db, job_id, "worker-a")
        assert await W.next_queued_job_id(db) is None


# ═══════════════════════════════════════════════════════════════════════════
# 4. A whole run: what a COMPLETED job looks like
# ═══════════════════════════════════════════════════════════════════════════


class TestACompletedRun:
    """Every epoch ran, the model was bound, and the row says exactly that."""

    @pytest.mark.asyncio
    async def test_the_run_completes_and_the_row_reports_the_epochs_it_did(
        self, service, reg, db, window
    ):
        await queue_job(service, reg, model_graph(reg)[0], epochs=4)
        job_id = db.job()["id"]
        binder = install()

        result = await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        assert result.completed, result.to_dict()
        assert result.epochs_completed == 4
        row = db.job()
        assert row["status"] == "COMPLETED"
        assert row["failure_reason"] is None
        assert row["epoch_current"] == 4
        assert row["progress"] == 1.0
        assert row["completed_at"]
        assert len(binder.calls) == 1

    @pytest.mark.asyncio
    async def test_progress_is_derived_from_completed_epochs_only(
        self, service, reg, db, window
    ):
        """Requirement 15.4. Progress is observed after an epoch returns, never timed.

        The per-epoch write is inspected rather than only the final value, because a
        fraction that happened to end at 1.0 could still have been fabricated on the way.
        """
        await queue_job(service, reg, model_graph(reg)[0], epochs=4)
        job_id = db.job()["id"]
        install()

        await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        progress = [
            entry["payload"]["progress"]
            for entry in db.updates_to("training_jobs")
            if "epoch_current" in entry["payload"]
        ]
        assert progress == [0.25, 0.5, 0.75, 1.0]

    @pytest.mark.asyncio
    async def test_every_epoch_records_scalar_metrics_and_a_duration(
        self, service, reg, db, window
    ):
        await queue_job(service, reg, model_graph(reg)[0], epochs=3)
        job_id = db.job()["id"]
        install()

        await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        history = db.job()["metrics_history"]
        assert [entry["epoch"] for entry in history] == [1, 2, 3]
        for entry in history:
            assert isinstance(entry["loss"], float)
            assert isinstance(entry["val_loss"], float)
            assert entry["duration_seconds"] >= 0.0
        assert db.job()["loss"] == pytest.approx(history[-1]["loss"])
        assert db.job()["val_loss"] == pytest.approx(history[-1]["val_loss"])

    @pytest.mark.asyncio
    async def test_the_model_is_actually_learning(self, service, reg, db, window):
        """A sanity check on the run, not on the worker.

        If the loss did not move, the "epochs" would be a loop counter rather than a
        fit, and every timing, progress and cancellation assertion above would be about
        nothing.
        """
        await queue_job(service, reg, model_graph(reg)[0], epochs=6)
        job_id = db.job()["id"]
        install()

        await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        losses = [entry["loss"] for entry in db.job()["metrics_history"]]
        assert losses[-1] < losses[0], losses

    @pytest.mark.asyncio
    async def test_the_binder_receives_the_context_task_65_needs(
        self, service, reg, db, window
    ):
        graph, model_node = model_graph(reg)
        await queue_job(service, reg, graph, epochs=2)
        job_id = db.job()["id"]
        binder = install()

        await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        call = binder.calls[0]
        assert call["job_id"] == job_id
        assert call["node_id"] == model_node
        assert call["model"] is not None

    @pytest.mark.asyncio
    async def test_no_model_versions_row_is_written_by_this_task(
        self, service, reg, db, window
    ):
        """Task 6.4 creates no ``model_versions`` row. That is 6.5, behind the seam."""
        await queue_job(service, reg, model_graph(reg)[0], epochs=2)
        install()

        await W.run_training_job(db.job()["id"], sb=db, worker_id="worker-a")

        assert "model_versions" not in db.tables
        assert "model_versions" not in db.touched

    @pytest.mark.asyncio
    async def test_every_transition_moved_updated_at(self, service, reg, db, window):
        """004d attaches no trigger to this table, so every writer sets the column."""
        await queue_job(service, reg, model_graph(reg)[0], epochs=3)
        install()

        await W.run_training_job(db.job()["id"], sb=db, worker_id="worker-a")

        writes = db.updates_to("training_jobs")
        assert writes, "the run wrote nothing at all"
        missing = [entry for entry in writes if "updated_at" not in entry["payload"]]
        assert not missing, f"{len(missing)} write(s) forgot updated_at: {missing}"


# ═══════════════════════════════════════════════════════════════════════════
# 5. Requirement 15.7 - cancellation stops at an epoch boundary, binds no model
# ═══════════════════════════════════════════════════════════════════════════


class TestCancellationAtAnEpochBoundary:
    """The author's stop is honoured at the next boundary, and nothing is bound.

    Task 6.3's cancel endpoint sets ``cancel_requested`` and deliberately does NOT set
    ``CANCELLED`` - the worker owns that transition, so the row cannot claim a job is
    cancelled while a process is still writing epochs against it. This is the other half
    of that contract.
    """

    def _cancel_after(self, db, job_id, epoch):
        """Set the flag from 'another process' once ``epoch`` has been fitted."""

        def _hook(current):
            if current == epoch:
                db.job(job_id)["cancel_requested"] = True

        return _hook

    @pytest.mark.asyncio
    async def test_the_job_becomes_cancelled_at_the_next_boundary(
        self, service, reg, db, window
    ):
        await queue_job(service, reg, model_graph(reg)[0], epochs=6)
        job_id = db.job()["id"]
        binder = install(
            lambda ctx: GradientDescentTrainer(
                ctx, on_epoch=self._cancel_after(db, job_id, 2)
            )
        )

        result = await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        assert result.cancelled
        row = db.job()
        assert row["status"] == "CANCELLED"
        assert row["epoch_current"] == 2, (
            "the stop is at the NEXT boundary, so the epoch in flight completes and is "
            "reported; anything else would discard work the row already recorded"
        )
        assert row["progress"] == pytest.approx(2 / 6)
        assert binder.calls == [], "a cancelled job must bind no model version"

    @pytest.mark.asyncio
    async def test_a_cancelled_job_carries_no_failure_reason(self, service, reg, db, window):
        """Cancelling is not failing. A reason here would misreport the author's own act."""
        await queue_job(service, reg, model_graph(reg)[0], epochs=5)
        job_id = db.job()["id"]
        install(
            lambda ctx: GradientDescentTrainer(
                ctx, on_epoch=self._cancel_after(db, job_id, 1)
            )
        )

        await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        assert db.job()["status"] == "CANCELLED"
        assert db.job()["failure_reason"] is None

    @pytest.mark.asyncio
    async def test_the_flag_is_read_fresh_at_every_boundary(self, service, reg, db, window):
        """A cached copy of the row could only ever say "no".

        ``cancel_requested`` is set by a different process after this job started, so the
        worker has to re-read it. Counting the reads is how that is asserted: one per
        boundary, not one per run.
        """
        await queue_job(service, reg, model_graph(reg)[0], epochs=4)
        job_id = db.job()["id"]
        install()
        before = len(db.touched)

        await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        reads = db.touched[before:].count("training_jobs")
        assert reads >= 4, (
            f"expected at least one training_jobs read per epoch boundary, saw {reads}"
        )

    @pytest.mark.asyncio
    async def test_a_job_cancelled_before_its_first_epoch_runs_none(
        self, service, reg, db, window
    ):
        await queue_job(service, reg, model_graph(reg)[0], epochs=4)
        job_id = db.job()["id"]
        db.job()["cancel_requested"] = True
        seen = []
        binder = install(
            lambda ctx: GradientDescentTrainer(ctx, on_epoch=seen.append)
        )

        result = await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        assert result.cancelled
        assert seen == [], "the boundary check runs before the first epoch, not after it"
        assert db.job()["epoch_current"] == 0
        assert db.job()["progress"] == 0
        assert binder.calls == []

    @pytest.mark.asyncio
    async def test_the_cancellation_is_announced(self, service, reg, db, window, handoffs):
        await queue_job(service, reg, model_graph(reg)[0], epochs=3)
        job_id = db.job()["id"]
        install(
            lambda ctx: GradientDescentTrainer(
                ctx, on_epoch=self._cancel_after(db, job_id, 1)
            )
        )

        await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        events = [name for name, _ in handoffs["events"]]
        assert "training.cancelled" in events
        assert "training.completed" not in events


# ═══════════════════════════════════════════════════════════════════════════
# 6. Requirement 15.8 - the wall clock
# ═══════════════════════════════════════════════════════════════════════════


class TestTheDurationCap:
    """A job that outran its permitted clock is ``FAILED`` / ``MAX_DURATION_EXCEEDED``."""

    @pytest.mark.asyncio
    async def test_an_overrun_job_fails_with_max_duration_exceeded(
        self, service, reg, db, window
    ):
        await queue_job(service, reg, model_graph(reg)[0], epochs=4)
        job_id = db.job()["id"]
        # The job has been running for two hours. The PROFESSIONAL tier permits 1800 s,
        # intersected with the isolator's own ceiling.
        db.job()["started_at"] = iso(ago(7200))
        binder = install()

        result = await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        assert result.failed
        assert result.failure_reason == W.FAILURE_MAX_DURATION_EXCEEDED
        row = db.job()
        assert row["status"] == "FAILED"
        assert row["failure_reason"] == "MAX_DURATION_EXCEEDED"
        assert binder.calls == [], "an overrun job must bind no model version"

    @pytest.mark.asyncio
    async def test_the_detail_states_elapsed_and_permitted(self, service, reg, db, window):
        await queue_job(service, reg, model_graph(reg)[0], epochs=2)
        job_id = db.job()["id"]
        db.job()["started_at"] = iso(ago(7200))
        install()

        result = await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        assert result.detail["elapsed_seconds"] > result.detail["allowed_seconds"]
        assert result.detail["allowed_seconds"] > 0

    @pytest.mark.asyncio
    async def test_the_version_is_left_undeployable(self, service, reg, db, window):
        """Requirement 15.10's second half: a failed job leaves the version un-deployable."""
        await queue_job(service, reg, model_graph(reg)[0], epochs=2)
        job_id = db.job()["id"]
        db.job()["started_at"] = iso(ago(7200))
        install()

        await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        from backend_app.backend.strategy_builder import LIFECYCLE_READY

        assert db.versions[0]["lifecycle_state"] == W.FAILED_LIFECYCLE_STATE
        assert db.versions[0]["lifecycle_state"] != LIFECYCLE_READY

    @pytest.mark.asyncio
    async def test_a_job_inside_its_budget_is_not_stopped(self, service, reg, db, window):
        await queue_job(service, reg, model_graph(reg)[0], epochs=2)
        job_id = db.job()["id"]
        db.job()["started_at"] = iso(ago(5))
        install()

        result = await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        assert result.completed

    def test_an_unreadable_ceiling_skips_the_check_rather_than_failing_everything(self):
        """Zero means "no readable ceiling", not "no time at all"."""
        job = {"started_at": iso(ago(99_999))}

        W._assert_within_duration(job, 0)
        W._assert_within_duration(job, None)

        with pytest.raises(W.MaxDurationExceeded):
            W._assert_within_duration(job, 10)

    def test_elapsed_is_measured_from_started_at(self):
        assert W.elapsed_seconds({"started_at": iso(ago(120))}) == pytest.approx(120, abs=5)
        # Falls back to created_at, and to zero when neither is readable.
        assert W.elapsed_seconds({"created_at": iso(ago(60))}) == pytest.approx(60, abs=5)
        assert W.elapsed_seconds({"started_at": "not a timestamp"}) == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# 7. Requirement 15.9 - a stale heartbeat is worker loss
# ═══════════════════════════════════════════════════════════════════════════


class TestStaleHeartbeatIsWorkerLoss:
    """``FAILED`` / ``WORKER_LOST``, the version back to ``SAVED``, no model bound.

    ``design.md``'s failure table names all three. The detector is a standalone sweep
    because Requirement 15.9 attributes it to the Training_Service rather than to the
    worker holding the job - by definition that worker is the one that cannot act.
    """

    async def _running_job(self, service, reg, db, *, heartbeat_age, epochs=3):
        await queue_job(service, reg, model_graph(reg)[0], epochs=epochs)
        job_id = db.job()["id"]
        await W.claim_training_job(db, job_id, "worker-gone")
        if heartbeat_age is None:
            db.job()["last_heartbeat"] = None
        else:
            db.job()["last_heartbeat"] = iso(ago(heartbeat_age))
        return job_id

    @pytest.mark.asyncio
    async def test_a_stale_job_is_failed_with_worker_lost(self, service, reg, db, window):
        job_id = await self._running_job(service, reg, db, heartbeat_age=600)

        reaped = await W.reap_stale_jobs(db, stale_after_seconds=90)

        assert [entry["job_id"] for entry in reaped] == [job_id]
        row = db.job()
        assert row["status"] == "FAILED"
        assert row["failure_reason"] == "WORKER_LOST"
        assert row["failure_reason"] in W.FAILURE_REASONS

    @pytest.mark.asyncio
    async def test_the_version_returns_to_saved(self, service, reg, db, window):
        await self._running_job(service, reg, db, heartbeat_age=600)

        await W.reap_stale_jobs(db, stale_after_seconds=90)

        assert W.WORKER_LOST_LIFECYCLE_STATE == "SAVED"
        assert db.versions[0]["lifecycle_state"] == "SAVED"

    @pytest.mark.asyncio
    async def test_no_model_is_bound_by_the_reaper(self, service, reg, db, window):
        await self._running_job(service, reg, db, heartbeat_age=600)
        binder = install()

        await W.reap_stale_jobs(db, stale_after_seconds=90)

        assert binder.calls == []
        assert "model_versions" not in db.tables

    @pytest.mark.asyncio
    async def test_a_healthy_job_is_left_running(self, service, reg, db, window):
        await self._running_job(service, reg, db, heartbeat_age=5)

        assert await W.reap_stale_jobs(db, stale_after_seconds=90) == []
        assert db.job()["status"] == "RUNNING"

    @pytest.mark.asyncio
    async def test_a_running_job_with_no_heartbeat_at_all_is_reaped(
        self, service, reg, db, window
    ):
        """A NULL heartbeat is the *most* lost a job can be.

        This is why the staleness test is in Python: a server-side
        ``last_heartbeat < :cutoff`` would skip these rows, because in SQL
        ``NULL < anything`` is NULL and a NULL predicate matches nothing.
        """
        await self._running_job(service, reg, db, heartbeat_age=None)

        reaped = await W.reap_stale_jobs(db, stale_after_seconds=90)

        assert len(reaped) == 1
        assert db.job()["failure_reason"] == "WORKER_LOST"

    @pytest.mark.asyncio
    async def test_the_write_is_a_compare_and_set_on_the_heartbeat_it_judged(
        self, service, reg, db, window
    ):
        """A job that proves itself alive between the read and the write survives.

        Without the CAS, a worker that heartbeated in that window would be reaped and its
        run silently orphaned while it kept training.
        """
        await self._running_job(service, reg, db, heartbeat_age=600)
        observed = db.job()["last_heartbeat"]

        await W.reap_stale_jobs(db, stale_after_seconds=90)

        reap_write = [
            entry for entry in db.updates_to("training_jobs")
            if entry["payload"].get("failure_reason") == "WORKER_LOST"
        ][0]
        assert ("eq", "status", "RUNNING") in reap_write["filters"]
        assert ("eq", "last_heartbeat", observed) in reap_write["filters"]

    @pytest.mark.asyncio
    async def test_a_reaped_worker_stands_down_without_overwriting_the_verdict(
        self, service, reg, db, window
    ):
        """The losing worker must not resurrect a job nobody is running.

        The sweep records ``WORKER_LOST`` while the worker is mid-loop. At the next
        boundary the worker notices the row is no longer ``RUNNING`` and writes nothing,
        so the verdict stands and the binder is never reached.
        """
        await queue_job(service, reg, model_graph(reg)[0], epochs=5)
        job_id = db.job()["id"]

        def _go_stale(epoch):
            if epoch == 1:
                db.job(job_id)["last_heartbeat"] = iso(ago(600))
                # The sweep runs from another process; here, synchronously.
                db.job(job_id)["status"] = "FAILED"
                db.job(job_id)["failure_reason"] = "WORKER_LOST"

        binder = install(lambda ctx: GradientDescentTrainer(ctx, on_epoch=_go_stale))

        result = await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        assert db.job()["status"] == "FAILED"
        assert db.job()["failure_reason"] == "WORKER_LOST", (
            "the worker that lost the job must not overwrite the reaper's verdict"
        )
        assert result.detail.get("stood_down") is True
        assert binder.calls == []

    @pytest.mark.asyncio
    async def test_a_heartbeat_cannot_be_written_for_a_job_this_worker_no_longer_holds(
        self, service, reg, db, window
    ):
        """The heartbeat write is scoped to ``status='RUNNING' AND worker_id=:me``.

        That scoping is what stops an already-reaped process from proving liveness for a
        job the platform has written off.
        """
        job_id = await self._running_job(service, reg, db, heartbeat_age=600)
        await W.reap_stale_jobs(db, stale_after_seconds=90)

        assert await W.write_heartbeat(db, job_id, "worker-gone") is False
        assert db.job()["status"] == "FAILED"

    @pytest.mark.asyncio
    async def test_the_stale_threshold_cannot_be_set_below_the_heartbeat_interval(
        self, monkeypatch
    ):
        """A detector that fires inside one heartbeat period manufactures worker loss."""
        monkeypatch.setenv("TRAINING_HEARTBEAT_INTERVAL_SECONDS", "20")
        monkeypatch.setenv("TRAINING_HEARTBEAT_STALE_SECONDS", "5")

        assert W.heartbeat_interval_seconds() == 20
        assert W.heartbeat_stale_seconds() == 60

    @pytest.mark.asyncio
    async def test_the_sweep_degrades_when_the_migration_is_unapplied(self, caplog):
        empty = FakeSupabase.seeded(with_training_jobs=False)

        with caplog.at_level("WARNING"):
            assert await W.reap_stale_jobs(empty) == []

        assert "004d_training_and_models.sql" in caplog.text


# ═══════════════════════════════════════════════════════════════════════════
# 8. Requirement 16.4 - the caps, re-evaluated before the first epoch
# ═══════════════════════════════════════════════════════════════════════════


class TestCapsAreRecheckedBeforeTheFirstEpoch:
    """The wall a request that skipped the API still hits.

    The API check cannot close three windows: a client that never went through it, a tier
    that changed between queueing and running, and the global concurrency figure task 6.2
    could only report as a lower bound from an RLS-scoped client. All three are what this
    re-check is for.
    """

    @pytest.mark.asyncio
    async def test_an_over_cap_epoch_count_written_past_the_api_is_refused(
        self, service, reg, db, window
    ):
        """The "modified client" case, made concrete.

        The row is edited to 5000 epochs *after* admission, which is what a writer
        bypassing the endpoint produces. The worker refuses it and names the cap, the
        requested value and the permitted value (Requirement 16.3).
        """
        await queue_job(service, reg, model_graph(reg)[0], epochs=3)
        job_id = db.job()["id"]
        db.job()["epochs_total"] = 5000
        seen = []
        binder = install(lambda ctx: GradientDescentTrainer(ctx, on_epoch=seen.append))

        result = await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        assert result.failed
        assert result.failure_reason == W.FAILURE_CAP_EXCEEDED
        assert result.detail["cap"] == "MAX_EPOCHS"
        assert result.detail["requested"] == 5000
        assert 0 < result.detail["allowed"] < 5000
        assert seen == [], "the re-check runs BEFORE the first epoch, not after it"
        assert binder.calls == []

    @pytest.mark.asyncio
    async def test_the_permitted_value_is_the_lesser_of_tier_and_model(
        self, service, reg, db, window
    ):
        """Requirement 16.2's intersection, asserted from the same policy the API used."""
        from backend_app.backend.ml_training_policy import ModelSpecView, resolve_caps

        await queue_job(service, reg, model_graph(reg)[0], epochs=3)
        db.job()["epochs_total"] = 100_000

        result = await W.run_training_job(db.job()["id"], sb=db, worker_id="worker-a")

        caps = resolve_caps(owner(), ModelSpecView.for_block_id("xgboost"))
        assert result.detail["allowed"] == caps.max_epochs
        assert caps.max_epochs == min(caps.tier_max_epochs, caps.model_max_safe_epochs)

    @pytest.mark.asyncio
    async def test_the_recheck_measures_the_rebuilt_dataset_not_the_recorded_figures(
        self, service, reg, db, window
    ):
        """The rows and columns re-checked are what the refetch actually produced.

        Re-using the admission-time figures would let a window that came back smaller be
        admitted against numbers it no longer has.
        """
        from backend_app.backend.ml_training_policy import ModelSpecView

        await queue_job(service, reg, model_graph(reg)[0], epochs=2)
        job = dict(db.job())
        job["usable_rows"] = 999_999_999  # a stale recorded figure

        version = db.versions[0]
        inputs = await W.rebuild_training_inputs(job, version, registry=reg)
        admission = await W.recheck_caps(
            db, job, stats=inputs.stats, spec=ModelSpecView.for_block_id("xgboost")
        )

        assert admission.admitted
        assert inputs.stats.usable_rows < 999_999_999

    @pytest.mark.asyncio
    async def test_global_saturation_defers_and_the_job_stays_queued(
        self, service, reg, db, window, monkeypatch
    ):
        """Requirement 16.5: hold it in the queue, do not reject it.

        This is the half task 6.2's report handed to 6.4: the worker runs with the service
        role, so ``global_running`` is a real fleet-wide count rather than a lower bound,
        and saturation becomes detectable here.
        """
        from backend_app.backend import ml_training_policy as P

        await queue_job(service, reg, model_graph(reg)[0], epochs=3)
        job_id = db.job()["id"]
        # Another tenant's jobs saturate the pool. Counted globally, excluding this one.
        for index in range(P.DEFAULT_MAX_CONCURRENT_JOBS_GLOBAL):
            db.jobs.append(
                {
                    "id": f"other-{index}",
                    "user_id": f"usr_other_{index}",
                    "status": "RUNNING",
                    "version_id": f"ver-{index}",
                    "node_id": f"n_{index}",
                }
            )
        seen = []
        binder = install(lambda ctx: GradientDescentTrainer(ctx, on_epoch=seen.append))

        result = await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        assert result.status == "QUEUED", result.to_dict()
        assert result.detail["deferred"] is True
        assert result.detail["queue_position"] >= 1
        row = db.job(job_id)
        assert row["status"] == "QUEUED", "a deferred job is held, not failed"
        assert row.get("failure_reason") is None
        assert row["worker_id"] is None, "the claim is released so another poll can take it"
        assert seen == [] and binder.calls == []

    @pytest.mark.asyncio
    async def test_the_running_job_is_excluded_from_its_own_global_count(
        self, service, reg, db, window
    ):
        """Otherwise the cap would refuse the very job the worker just claimed."""
        await queue_job(service, reg, model_graph(reg)[0], epochs=2)
        job_id = db.job()["id"]
        await W.claim_training_job(db, job_id, "worker-a")

        counts = await W.count_all_training_jobs(db, USER_ID, exclude_job_id=job_id)

        assert counts.available is True
        assert counts.global_running == 0
        assert counts.user_active == 0

    @pytest.mark.asyncio
    async def test_the_global_count_is_genuinely_global(self, service, reg, db, window):
        await queue_job(service, reg, model_graph(reg)[0], epochs=2)
        db.jobs.append(
            {"id": "other-1", "user_id": "usr_someone_else", "status": "RUNNING"}
        )

        counts = await W.count_all_training_jobs(db, USER_ID)

        assert counts.global_running == 1
        assert counts.user_active == 1, "the queued job of this user"

    @pytest.mark.asyncio
    async def test_the_counts_degrade_naming_004d(self, caplog):
        empty = FakeSupabase.seeded(with_training_jobs=False)

        with caplog.at_level("WARNING"):
            counts = await W.count_all_training_jobs(empty, USER_ID)

        assert counts.available is False
        assert "004d_training_and_models.sql" in caplog.text

    @pytest.mark.asyncio
    async def test_the_plan_is_read_from_the_platforms_own_lookup(self, service, reg, db):
        """No second tier table. ``profiles.subscription_tier`` via ``get_user_plan``."""
        resolved = await W.resolve_job_user(db, USER_ID)

        assert resolved["id"] == USER_ID
        assert resolved["plan_resolved"] is True
        assert resolved["plan"]


# ═══════════════════════════════════════════════════════════════════════════
# 9. Requirement 16.6 - isolation, memory bounds and deterministic seeding
# ═══════════════════════════════════════════════════════════════════════════


class TestIsolationMemoryAndDeterminism:
    """The three safety mechanisms the requirement names, each asserted by its effect."""

    @pytest.mark.asyncio
    async def test_two_runs_with_the_same_seed_are_identical(self, service, reg, db, window):
        """Deterministic seeding, proved rather than asserted.

        The trainer draws its initial weights from ``np.random``. Two runs of the same row
        produce byte-identical loss curves only if the worker really wrapped the run in
        ``DeterministicEnforcer.deterministic_context(seed)`` - the seed being the one
        recorded in ``config``, so a rerun of this row is the same run.
        """
        await queue_job(service, reg, model_graph(reg)[0], epochs=4)
        job_id = db.job()["id"]

        install()
        await W.run_training_job(job_id, sb=db, worker_id="worker-a")
        first = [entry["loss"] for entry in db.job()["metrics_history"]]

        # Re-queue the same row and run it again.
        db.job()["status"] = "QUEUED"
        db.job()["epoch_current"] = 0
        db.job()["progress"] = 0
        db.job()["metrics_history"] = None
        db.job()["completed_at"] = None
        install()
        await W.run_training_job(job_id, sb=db, worker_id="worker-b")
        second = [entry["loss"] for entry in db.job()["metrics_history"]]

        assert first == second, "the same seed must produce the same run"

    @pytest.mark.asyncio
    async def test_the_recorded_seed_is_the_one_that_is_used(self, service, reg, db, window):
        await queue_job(service, reg, model_graph(reg)[0], epochs=2, seed=1234)
        assert db.job()["config"]["seed"] == 1234

        seeds = []
        install(
            lambda ctx: (seeds.append(ctx.seed) or GradientDescentTrainer(ctx))
        )
        await W.run_training_job(db.job()["id"], sb=db, worker_id="worker-a")

        assert seeds == [1234]

    @pytest.mark.asyncio
    async def test_a_matrix_larger_than_the_platform_bound_is_refused(
        self, service, reg, db, window
    ):
        """``MemoryMonitor`` is the measurement; ``estimate_memory_mb`` said it was an estimate."""
        await queue_job(service, reg, model_graph(reg)[0], epochs=3)
        job_id = db.job()["id"]
        ml_safety.MemoryMonitor.configure(
            ml_safety.MemoryConfig(max_tensor_size_mb=0, max_batch_size=1000)
        )
        binder = install()

        result = await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        assert result.failed
        assert result.failure_reason == W.FAILURE_MEMORY_EXCEEDED
        assert db.job()["failure_reason"] == "MEMORY_EXCEEDED"
        assert binder.calls == []

    @pytest.mark.asyncio
    async def test_an_over_bound_batch_size_is_a_memory_refusal_not_a_runtime_error(
        self, service, reg, db, window
    ):
        """``validate_batch_size`` raises a bare ``ValueError``.

        Left unwrapped, the classified catch-all would absorb it as a generic runtime
        error and the operator would lose the actual diagnosis.
        """
        await queue_job(service, reg, model_graph(reg)[0], epochs=2, batch_size=64)
        ml_safety.MemoryMonitor.configure(
            ml_safety.MemoryConfig(max_tensor_size_mb=1024, max_batch_size=1)
        )
        install()

        result = await W.run_training_job(db.job()["id"], sb=db, worker_id="worker-a")

        assert result.failure_reason == W.FAILURE_MEMORY_EXCEEDED

    def test_the_duration_ceiling_is_read_from_the_live_isolator_config(self):
        """The figure the caps intersected and the figure the worker enforces are one figure."""
        ml_safety.TrainingIsolator.configure(
            ml_safety.TrainingConfig(max_training_time_seconds=777)
        )
        assert W.isolation_deadline_seconds() == 777

    def test_isolation_off_means_the_fit_runs_in_process(self):
        ml_safety.TrainingIsolator.configure(
            ml_safety.TrainingConfig(enable_process_isolation=False)
        )

        result, isolated = W.run_isolated(lambda: "fitted")

        assert result == "fitted"
        assert isolated is False

    def test_an_isolator_failure_falls_back_once_and_then_latches(self, monkeypatch, caplog):
        """The ``ml_models`` precedent, plus a latch this loop needs and that one did not.

        ``ml_models`` retries per model; this loop would retry per epoch, paying a
        ``spawn`` process launch every time for a failure that is deterministic. So the
        first failure is logged in full and remembered.
        """
        ml_safety.TrainingIsolator.configure(
            ml_safety.TrainingConfig(enable_process_isolation=True)
        )
        attempts = []

        def _explode(fn, *args, **kwargs):
            attempts.append(1)
            raise RuntimeError("a closure over a live model is not picklable")

        monkeypatch.setattr(
            ml_safety.TrainingIsolator, "run_isolated_training", _explode
        )

        with caplog.at_level("WARNING"):
            assert W.run_isolated(lambda: "fitted") == ("fitted", False)
            assert W.run_isolated(lambda: "fitted") == ("fitted", False)

        assert len(attempts) == 1, "the deterministic failure must not be re-attempted"
        assert "not picklable" in caplog.text

        W.reset_isolation_latch()
        assert W.run_isolated(lambda: "fitted") == ("fitted", False)
        assert len(attempts) == 2


# ═══════════════════════════════════════════════════════════════════════════
# 10. The task 6.5 seam: what an un-landed backend looks like
# ═══════════════════════════════════════════════════════════════════════════


class TestTheModelPersistenceSeam:
    """A run that produced no bound model is never reported as complete.

    ``design.md``'s ordering is ``persist_artifact -> insert_model_version -> bind ->
    set_status(job, COMPLETED)``. So with no binder installed the honest terminal state is
    ``FAILED`` with a classified reason - a ``COMPLETED`` job carrying no model is exactly
    the silent-success shape Requirement 15.10 exists to prevent, and 6.6 would report it
    to a user as a finished training run.
    """

    @pytest.mark.asyncio
    async def test_no_backend_installed_fails_with_trainer_unavailable(
        self, service, reg, db, window
    ):
        await queue_job(service, reg, model_graph(reg)[0], epochs=3)
        assert W.current_training_backend() is None

        result = await W.run_training_job(db.job()["id"], sb=db, worker_id="worker-a")

        assert result.failed
        assert result.failure_reason == W.FAILURE_TRAINER_UNAVAILABLE
        assert db.job()["status"] == "FAILED"
        assert db.versions[0]["lifecycle_state"] == W.FAILED_LIFECYCLE_STATE

    @pytest.mark.asyncio
    async def test_a_backend_with_no_binder_fails_after_running_every_epoch(
        self, service, reg, db, window
    ):
        await queue_job(service, reg, model_graph(reg)[0], epochs=3)
        job_id = db.job()["id"]
        W.register_training_backend(
            W.TrainingBackend(
                trainer=lambda ctx: GradientDescentTrainer(ctx), binder=None, name="fit-only"
            )
        )

        result = await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        assert result.failure_reason == W.FAILURE_MODEL_PERSISTENCE_UNAVAILABLE
        assert db.job()["status"] == "FAILED"
        assert db.job()["epoch_current"] == 3, (
            "every epoch really ran; the failure is the missing persistence step"
        )

    @pytest.mark.asyncio
    async def test_a_binder_that_binds_nothing_is_not_a_completed_job(
        self, service, reg, db, window
    ):
        await queue_job(service, reg, model_graph(reg)[0], epochs=2)
        install(binder=RecordingBinder(refuse=True))

        result = await W.run_training_job(db.job()["id"], sb=db, worker_id="worker-a")

        assert result.failure_reason == W.FAILURE_MODEL_PERSISTENCE_UNAVAILABLE
        assert db.job()["status"] != "COMPLETED"

    def test_the_seam_refuses_a_non_callable_trainer(self):
        with pytest.raises(TypeError):
            W.TrainingBackend(trainer="not callable")
        with pytest.raises(TypeError):
            W.register_training_backend(object())


# ═══════════════════════════════════════════════════════════════════════════
# 11. Everything else that fails, still classified
# ═══════════════════════════════════════════════════════════════════════════


class TestOtherFailuresAreStillClassified:
    """No path out of a claimed job leaves an unclassified reason or a RUNNING row."""

    @pytest.mark.asyncio
    async def test_a_trainer_that_raises_is_a_classified_runtime_error(
        self, service, reg, db, window
    ):
        await queue_job(service, reg, model_graph(reg)[0], epochs=4)
        job_id = db.job()["id"]
        binder = install(lambda ctx: GradientDescentTrainer(ctx, fail_at=2))

        result = await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        assert result.failure_reason == W.FAILURE_TRAINING_RUNTIME_ERROR
        row = db.job()
        assert row["status"] == "FAILED"
        assert row["failure_reason"] in W.FAILURE_REASONS
        assert "ZeroDivisionError" not in str(row["failure_reason"])
        assert row["epoch_current"] == 1, "the epoch that did complete is still reported"
        assert binder.calls == []

    @pytest.mark.asyncio
    async def test_an_outlier_rejected_window_is_a_data_quality_failure(
        self, service, reg, db, window
    ):
        """The known ``OutlierDetector`` strictness, end to end on the worker.

        The window is admitted, then the *refetched* window contains a 3-sigma close -
        which is what a real feed does. ``market_data_validation`` refuses it, and the
        worker records ``DATA_QUALITY`` rather than a 500 or a generic error, and does not
        retry against looser thresholds.
        """
        await queue_job(service, reg, model_graph(reg)[0], epochs=3)
        job_id = db.job()["id"]
        window["generator"] = spiky_bars
        binder = install()

        result = await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        assert result.failure_reason == W.FAILURE_DATA_QUALITY
        assert db.job()["failure_reason"] == "DATA_QUALITY"
        assert binder.calls == []

    @pytest.mark.asyncio
    async def test_a_missing_version_is_classified_and_not_a_crash(
        self, service, reg, db, window
    ):
        await queue_job(service, reg, model_graph(reg)[0], epochs=3)
        job_id = db.job()["id"]
        db.tables["strategy_versions"] = []
        install()

        result = await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        assert result.failure_reason == W.FAILURE_VERSION_UNAVAILABLE
        assert db.job()["status"] == "FAILED"

    @pytest.mark.asyncio
    async def test_a_node_that_is_not_a_model_node_is_refused(self, service, reg, db, window):
        await queue_job(service, reg, model_graph(reg)[0], epochs=3)
        job_id = db.job()["id"]
        db.job()["node_id"] = "n_not_in_this_graph"
        install()

        result = await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        assert result.failure_reason == W.FAILURE_VERSION_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_an_unclaimable_job_writes_nothing_at_all(self, service, reg, db, window):
        await queue_job(service, reg, model_graph(reg)[0], epochs=3)
        job_id = db.job()["id"]
        db.job()["status"] = "CANCELLED"
        before = dict(db.job())
        install()

        result = await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        assert result.claimed is False
        assert result.status == ""
        assert db.job() == before

    @pytest.mark.asyncio
    async def test_the_fingerprint_is_verified_and_reported_not_enforced(
        self, service, reg, db, window, caplog
    ):
        """The recorded fingerprint stays provenance; a moved window is a warning.

        ``fetch_training_bars`` returns the *N most recent* bars, so any delay between
        queueing and running changes the fingerprint by construction. Enforcing equality
        would fail every real job; a by-timestamp fetch is what would make it enforceable,
        and that is a change to the feed contract rather than to this worker.
        """
        await queue_job(service, reg, model_graph(reg)[0], epochs=2)
        job_id = db.job()["id"]
        recorded = db.job()["dataset_fingerprint"]
        db.job()["dataset_fingerprint"] = "0" * 64
        install()

        with caplog.at_level("WARNING"):
            result = await W.run_training_job(job_id, sb=db, worker_id="worker-a")

        assert result.completed, "a moved window must not fail an otherwise sound job"
        assert "fingerprint" in caplog.text.lower()
        assert recorded, "the admission path did record one"


# ═══════════════════════════════════════════════════════════════════════════
# 12. The process around the run
# ═══════════════════════════════════════════════════════════════════════════


class TestTheWorkerLoop:
    """One poll: reap what is stale, then run at most one job."""

    @pytest.mark.asyncio
    async def test_a_poll_runs_the_queued_job_it_finds_in_the_table(
        self, service, reg, db, window
    ):
        await queue_job(service, reg, model_graph(reg)[0], epochs=2)
        install()
        worker = W.TrainingWorker(worker_id="worker-a", sb=db)

        assert await worker.process_iteration() is True
        assert db.job()["status"] == "COMPLETED"
        assert worker.jobs_run == 1

    @pytest.mark.asyncio
    async def test_an_empty_queue_is_no_work_rather_than_an_error(self, db):
        worker = W.TrainingWorker(worker_id="worker-a", sb=db)

        assert await worker.process_iteration() is False

    @pytest.mark.asyncio
    async def test_a_poll_reaps_stale_jobs_before_taking_new_work(
        self, service, reg, db, window
    ):
        await queue_job(service, reg, model_graph(reg)[0], epochs=2)
        job_id = db.job()["id"]
        await W.claim_training_job(db, job_id, "worker-gone")
        db.job()["last_heartbeat"] = iso(ago(10_000))
        worker = W.TrainingWorker(worker_id="worker-a", sb=db)

        await worker.process_iteration()

        assert worker.jobs_reaped == 1
        assert db.job()["failure_reason"] == "WORKER_LOST"

    @pytest.mark.asyncio
    async def test_no_database_client_is_no_work_rather_than_a_crash(self):
        worker = W.TrainingWorker(worker_id="worker-a", sb=None)
        with patch.object(W, "_worker_client", AsyncMock(return_value=None)):
            assert await worker.process_iteration() is False

    def test_a_worker_id_names_the_host_and_the_process(self):
        identity = W.new_worker_id()

        assert identity.startswith("tw-")
        assert str(os.getpid()) in identity
        assert W.new_worker_id() != identity
