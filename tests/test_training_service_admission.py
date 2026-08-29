"""
tests/test_training_service_admission.py

The training admission path: what reaches ``training_jobs``, and what must not.

Spec: strategy-builder task 6.3 (`design.md` -> Training workflow, § API surface).
Requirements 12.6, 14.7, 14.8, 14.10, 15.1, 15.12, 15.13, 15.14.

WHAT THIS FILE HOLDS IN PLACE
-----------------------------
Six properties, each of which would be a real defect if it stopped holding:

1. **No job row on a gate failure.** A graph whose usable feature columns fall short
   is refused, the response carries required AND available for both dimensions, and
   ``training_jobs`` is empty afterwards (Requirements 14.3, 14.4, 14.7, 14.8).
2. **No job row on a cap rejection.** An over-cap epoch count is refused naming the
   cap, the requested value and the permitted value, and no row is written
   (Requirement 16.3).
3. **Idempotent per (version, node).** Asserted through the DATABASE constraint, not
   through the service's convenience pre-check: the double below enforces
   ``uq_tj_active_per_node``'s partial predicate, so a second insert for a live
   (version, node) is rejected exactly as PostgreSQL would reject it and the service
   has to translate that rejection rather than surface it (Requirement 15.13).
4. **Cancel sets ``cancel_requested``** and does NOT set ``CANCELLED``: the worker
   owns that transition (Requirement 15.7). ``updated_at`` moves, because migration
   004d deliberately attaches no trigger to this table.
5. **No ML node -> version ``READY``, training not required** (Requirement 14.10).
   Not "skipped", not an empty job.
6. **No exchange identifier in the persisted config** (Requirement 12.6, SB-06). The
   configuration holds the resolved data source - the DATA node's symbol and
   timeframe - and no venue and no credential, at any depth.

WHAT IS REAL HERE AND WHAT IS A DOUBLE
--------------------------------------
Real: the assembled registry; canonical graphs built from published descriptors; the
compiler; the validator including stage 11; ``market_data_validation``;
``dag_engine`` executing the feature pipeline through the same ``execute_dag`` call
the runtime makes; ``feature_validator``; ``ml_dataset``; the whole of
``ml_training_policy``; and every status code, which comes from production code.

Doubles, and only these two:

* **The database.** ``FakeSupabase`` is an in-memory store that enforces the two
  constraints these tests are about - a missing relation, and
  ``uq_tj_active_per_node``'s partial unique predicate. It is a double because there
  is no local PostgreSQL and migration ``004d_training_and_models.sql`` is unapplied;
  that limitation is stated rather than papered over, and live enforcement of the
  index remains the migration's own VERIFICATION queries.
* **The candle feed.** ``fetch_training_bars`` is the one I/O boundary of the
  admission path and is replaced with a deterministic window, so these tests are
  about the gate, the caps and the writes rather than about an exchange being
  reachable.

Nothing else is stubbed. In particular no gate, cap, quality rule or feature formula
is replaced by a test-only version, because a test that replaces the thing under test
proves nothing.

A NOTE ON THE SYNTHETIC WINDOW
------------------------------
``bounded_bars`` is a smooth bounded oscillation rather than a random walk. That is
not to make the data flattering: ``market_data_validation.OutlierDetector`` rejects
any close beyond 3 sigma, and a random walk of a few thousand bars is near-certain to
contain one. A window that component rejects is a legitimate ``DATA_QUALITY`` block
(and is asserted as one below), but it is not the case the other five properties are
about, so the shared window is one the validator accepts.
"""

import json
import math
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import strategy_service as S
from backend_app.backend.strategy_builder import (
    LIFECYCLE_READY,
    LIFECYCLE_TRAINING,
    LIFECYCLE_VALIDATED,
    compile_version,
)
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag import validator as V
from backend_app.backend.strategy_dag.schema import (
    FORBIDDEN_PARAM_FIELDS,
    EdgeSpec,
    NodeSpec,
    StrategyGraph,
)

USER_ID = "usr_training_owner"
OTHER_USER_ID = "usr_training_other"
STRATEGY_ID = "stg_training_0001"

#: A plan that actually carries the ML training feature and a non-zero epoch cap.
#: FREE and STARTER carry neither, so a test running on them would be asserting the
#: entitlement layer rather than the caps.
PAID_TIER = "pro"


# ---------------------------------------------------------------------------
# The registry and the graphs, both real
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reg():
    """The real assembled registry. Assembly costs ~150 ms, so it is shared."""
    return registry_module.build_registry()


@pytest.fixture(autouse=True)
def default_hooks():
    """Stages 10b and 11 installed, whatever ran before.

    Other files in the suite exercise the seam and call ``clear_stage_hooks()``, which
    removes both. Restoring them here keeps these tests independent of collection
    order rather than quietly passing or failing depending on it.
    """
    V.install_default_stage_hooks()
    yield
    V.install_default_stage_hooks()


@pytest.fixture(autouse=True)
def forget_column_probe():
    """Forget the cached canonical-column verdict between tests.

    ``canonical_columns_supported`` caches per process, so a test whose double omits
    the columns would otherwise poison the next one.
    """
    S.reset_canonical_column_support()
    yield
    S.reset_canonical_column_support()


def node(reg, block_id, **params):
    return NodeSpec.create(block_id, reg[block_id].category, params=params)


def model_graph(reg, *, block_id="xgboost", lags=(1, 2, 3, 4, 5, 6),
                symbol="BTC/USDT", timeframe="15m"):
    """data -> ema -> feat_lag -> model -> gt(vs constant) -> buy.

    A pipeline the platform would actually run, so the feature pipeline is executed by
    the real executors over a real plan rather than against a hand-made ML node with no
    upstream. ``lags`` is the knob that sets the produced feature column count, which
    is what the minimum-data gate measures.
    """
    data = node(reg, "ohlcv_feed", symbol=symbol, timeframe=timeframe,
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


def linear_graph(reg):
    """data -> ema -> gt(vs constant) -> buy. No model node anywhere."""
    data = node(reg, "ohlcv_feed", symbol="SOL/USDT", timeframe="4h",
                market_type="spot", mode="streaming")
    ema = node(reg, "ema", window=20, source="close")
    const = node(reg, "constant", value=30.0)
    gate = node(reg, "gt")
    action = node(reg, "action_buy_market", quantity_type="percent_of_equity",
                  quantity=0.25)
    return StrategyGraph(
        nodes=[data, ema, const, gate, action],
        edges=[
            EdgeSpec.create(data.id, "close", ema.id, "series"),
            EdgeSpec.create(ema.id, "value", gate.id, "left"),
            EdgeSpec.create(const.id, "value", gate.id, "right"),
            EdgeSpec.create(gate.id, "out", action.id, "signal"),
        ],
    )


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------


def bounded_bars(count, *, step_ms=900_000, start=1_600_000_000_000):
    """A deterministic, evenly spaced, OHLC-consistent window.

    Evenly spaced so ``GapHandler`` finds no gap; smooth and bounded so
    ``OutlierDetector`` finds no 3-sigma close and no 5% bar-to-bar move; strictly
    positive prices and non-negative volume so ``CandleIntegrityValidator`` finds no
    violation. Every one of those is a rule of ``market_data_validation``, which is
    REUSED AS-IS - the window is shaped to be *acceptable to it*, not to bypass it.
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
    """``bounded_bars`` with one violent close, which the outlier rule rejects."""
    rows = bounded_bars(count, **kwargs)
    middle = len(rows) // 2
    rows[middle][2] = 100_000.0  # high
    rows[middle][4] = 99_000.0   # close
    return rows


@pytest.fixture
def window(monkeypatch):
    """Replace the one I/O boundary with a deterministic window.

    Returns a recorder so a test can assert what was asked for, and can swap the
    generator for a hostile one.
    """
    state = {"calls": [], "generator": bounded_bars}

    async def _fetch(symbol, timeframe, bars):
        state["calls"].append(
            {"symbol": symbol, "timeframe": timeframe, "bars": int(bars)}
        )
        return state["generator"](int(bars))

    monkeypatch.setattr(S, "fetch_training_bars", _fetch)
    return state


@pytest.fixture(autouse=True)
def handoffs(monkeypatch):
    """Neutralise the two best-effort hand-offs and record that they happened.

    ``enqueue_training_job`` and ``publish_training_event`` are wake-up hints, not the
    source of truth (the ``QUEUED`` row is). Recording them lets a test assert the
    hand-off occurred without a Redis or a WebSocket manager being present.
    """
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


# ---------------------------------------------------------------------------
# The database double: an in-memory store that enforces the two constraints
# these tests are about
# ---------------------------------------------------------------------------


class MissingRelation(Exception):
    """What PostgreSQL says when a table does not exist (SQLSTATE 42P01)."""

    def __init__(self, table):
        super().__init__(
            f'relation "public.{table}" does not exist '
            f"(SQLSTATE 42P01 undefined_table)"
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
    """An in-memory store with the constraints that matter here, and nothing else.

    Enforced, because the tests are about them:

    * a table that is not in ``tables`` raises :class:`MissingRelation`, which is how
      the unapplied-migration path is exercised;
    * ``training_jobs`` inserts are checked against ``uq_tj_active_per_node``'s
      **partial** predicate - unique on ``(version_id, node_id)`` only ``WHERE status
      IN ('QUEUED','RUNNING')`` - so a completed job leaves the node retrainable,
      exactly as the migration specifies.

    Not enforced, and not pretended to be: RLS, foreign keys, the CHECK constraints or
    column types. Those belong to the migration's own VERIFICATION queries and to task
    8.7's isolation matrix; asserting them against a Python dict would prove nothing
    about PostgreSQL.
    """

    def __init__(self, tables=None):
        self.tables = {
            name: [dict(row) for row in rows] for name, rows in (tables or {}).items()
        }
        self.touched = []

    @classmethod
    def seeded(cls, *, with_training_jobs=True, tier=PAID_TIER):
        tables = {
            "strategies": [
                {"id": STRATEGY_ID, "user_id": USER_ID, "current_version": "v1.0"}
            ],
            "strategy_versions": [],
            "profiles": [
                {"id": USER_ID, "subscription_tier": tier},
                {"id": OTHER_USER_ID, "subscription_tier": tier},
            ],
        }
        if with_training_jobs:
            tables["training_jobs"] = []
        return cls(tables)

    # -- the client surface ------------------------------------------------
    def table(self, name):
        self.touched.append(name)
        return FakeTable(self, name)

    # -- inspection --------------------------------------------------------
    def rows(self, name):
        return self.tables.get(name, [])

    @property
    def jobs(self):
        return self.tables.get("training_jobs", [])

    @property
    def versions(self):
        return self.tables.get("strategy_versions", [])

    # -- execution ---------------------------------------------------------
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
                if not keep:
                    break
            if keep:
                matched.append(row)
        return matched

    def _assert_uq_tj_active_per_node(self, row):
        """``uq_tj_active_per_node``, predicate included."""
        if str(row.get("status", "QUEUED")) not in S.TRAINING_JOB_LIVE_STATES:
            return
        for existing in self.tables["training_jobs"]:
            if (
                existing.get("version_id") == row.get("version_id")
                and existing.get("node_id") == row.get("node_id")
                and str(existing.get("status")) in S.TRAINING_JOB_LIVE_STATES
            ):
                raise UniqueViolation("uq_tj_active_per_node")

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
            self.tables[query.table].append(row)
            return FakeResult([dict(row)])

        if query.op == "update":
            matched = self._match(query)
            for row in matched:
                row.update(query.payload)
            return FakeResult([dict(row) for row in matched])

        raise AssertionError(f"unsupported operation {query.op!r}")  # pragma: no cover


@pytest.fixture
def db():
    return FakeSupabase.seeded()


@pytest.fixture
def service(db):
    """The real :class:`StrategyService`, wired to the store.

    Only ``create_request_supabase_async`` is replaced, so ``_get_supabase`` and every
    query the service issues stay production code.
    """
    from backend_app.backend.strategy_service import StrategyService

    with patch.object(S, "create_request_supabase_async", AsyncMock(return_value=db)):
        yield StrategyService()


def owner(plan=PAID_TIER):
    return {
        "id": USER_ID,
        "email": "owner@example.com",
        "role": "authenticated",
        "access_token": "token_owner",
        "plan": plan,
    }


async def save(service, reg, graph, **training):
    return await service.create_version_and_maybe_train(
        owner(),
        STRATEGY_ID,
        graph.to_dict(),
        registry=reg,
        training_cfg=training or None,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. Requirement 14.10 - no model node means READY, and training NOT REQUIRED
# ═══════════════════════════════════════════════════════════════════════════


class TestNoModelNodeIsReadyAndNotRequired:
    """The version is deployable and the response says training was not needed.

    Not "training skipped", not a job with nothing to do. Both of those would leave a
    client unable to tell "there is nothing to train" from "training has not started
    yet", which is the ambiguity Requirement 14.10 exists to remove.
    """

    @pytest.mark.asyncio
    async def test_the_saved_version_is_ready(self, service, reg, db):
        result = await save(service, reg, linear_graph(reg))

        assert result["lifecycle_state"] == LIFECYCLE_READY
        assert db.versions[0]["lifecycle_state"] == LIFECYCLE_READY

    @pytest.mark.asyncio
    async def test_the_response_states_training_is_not_required(self, service, reg):
        result = await save(service, reg, linear_graph(reg))

        training = result["training"]
        assert training["state"] == S.TRAINING_NOT_REQUIRED
        assert training["required"] is False
        assert training["job_id"] is None
        assert training["state"] != "SKIPPED", (
            "'not required' and 'skipped' are different facts; a client must be able "
            "to tell them apart"
        )

    @pytest.mark.asyncio
    async def test_no_job_row_and_no_feed_read(self, service, reg, db, window):
        result = await save(service, reg, linear_graph(reg))

        assert result["training"]["jobs"] == []
        assert db.jobs == [], "a graph with nothing to train creates no job row"
        assert window["calls"] == [], (
            "a graph with no model node must not fetch a training window either"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 2. Requirements 15.1, 15.12, 15.14 - QUEUED, with the job id, nothing trained
# ═══════════════════════════════════════════════════════════════════════════


class TestAdmittedTrainingIsQueuedAndNotRun:
    @pytest.mark.asyncio
    async def test_the_save_response_carries_queued_and_the_job_id(
        self, service, reg, db, window
    ):
        graph, _model_id = model_graph(reg)
        result = await save(service, reg, graph, epochs=100)

        training = result["training"]
        assert training["state"] == S.TRAINING_QUEUED, training
        assert training["job_id"], "Requirement 15.12: the save response carries the id"
        assert training["job_id"] == db.jobs[0]["id"]

    @pytest.mark.asyncio
    async def test_one_row_per_model_node_in_queued(self, service, reg, db, window):
        graph, model_id = model_graph(reg)
        await save(service, reg, graph, epochs=100)

        assert len(db.jobs) == 1
        job = db.jobs[0]
        assert job["status"] == "QUEUED"
        assert job["node_id"] == model_id
        assert job["block_id"] == "xgboost"
        assert job["version_id"] == db.versions[0]["id"]
        assert job["user_id"] == USER_ID
        assert job["strategy_id"] == STRATEGY_ID

    @pytest.mark.asyncio
    async def test_the_row_proves_no_epoch_ran_during_the_request(
        self, service, reg, db, window
    ):
        """Requirement 15.1: the job id comes back without waiting for training.

        The row itself is the evidence: zero epochs completed, zero progress, no start
        timestamp, no loss. A path that trained inline could not produce this row.
        """
        graph, _model_id = model_graph(reg)
        await save(service, reg, graph, epochs=100)

        job = db.jobs[0]
        assert job["epoch_current"] == 0
        assert job["progress"] == 0
        assert job.get("started_at") is None
        assert job.get("loss") is None
        assert job.get("val_loss") is None
        assert job.get("failure_reason") is None
        assert job["epochs_total"] == 100, "what the worker will run, recorded up front"

    @pytest.mark.asyncio
    async def test_the_job_is_handed_off_rather_than_executed(
        self, service, reg, db, window, handoffs
    ):
        graph, _model_id = model_graph(reg)
        await save(service, reg, graph, epochs=100)

        assert handoffs["enqueued"] == [db.jobs[0]["id"]]
        assert any(event == "training.queued" for event, _ in handoffs["events"]), (
            "the author is told training was started (Requirement 15.11)"
        )

    @pytest.mark.asyncio
    async def test_the_version_moves_to_training_not_ready(
        self, service, reg, db, window
    ):
        graph, _model_id = model_graph(reg)
        await save(service, reg, graph, epochs=100)

        assert db.versions[0]["lifecycle_state"] == LIFECYCLE_TRAINING
        assert db.versions[0]["lifecycle_state"] != LIFECYCLE_READY, (
            "a version awaiting a model version is not deployable"
        )

    @pytest.mark.asyncio
    async def test_the_configuration_and_fingerprint_are_recorded(
        self, service, reg, db, window
    ):
        """Requirement 15.14: the configuration AND a dataset fingerprint, per job."""
        graph, _model_id = model_graph(reg)
        await save(service, reg, graph, epochs=100)

        job = db.jobs[0]
        assert job["dataset_fingerprint"], "no fingerprint means no reproducible rerun"
        assert len(job["dataset_fingerprint"]) == 64, "sha256, hex"
        config = job["config"]
        for key in (
            "symbol", "timeframe", "epochs", "seed", "label_horizon", "embargo_bars",
            "val_fraction", "test_fraction", "dag_hash", "range", "splits",
        ):
            assert key in config, f"config must record {key} for a faithful rerun"
        assert config["epochs"] == 100
        assert job["feature_columns"] == 6
        assert job["feature_names"] and len(job["feature_names"]) == 6
        assert job["usable_rows"] > 0
        assert job["split_sizes"], "the split geometry is recorded with the job"

    def test_the_fingerprint_follows_the_whole_window_not_its_endpoints(self):
        """Two windows with the same endpoints and a different middle differ.

        A fingerprint of the first and last timestamp would collide here, and the
        worker's ``expect_fingerprint`` check would then train on a revised candle
        without noticing.
        """
        import pandas as pd

        first = S.training_frame(bounded_bars(500), 500)
        revised = first.copy()
        revised.iloc[250, revised.columns.get_loc("close")] += 1.0

        assert isinstance(first.index, pd.DatetimeIndex)
        assert S.dataset_fingerprint(first, "BTC/USDT", "15m") == S.dataset_fingerprint(
            first.copy(), "BTC/USDT", "15m"
        )
        assert S.dataset_fingerprint(first, "BTC/USDT", "15m") != S.dataset_fingerprint(
            revised, "BTC/USDT", "15m"
        )
        assert S.dataset_fingerprint(first, "BTC/USDT", "15m") != S.dataset_fingerprint(
            first, "ETH/USDT", "15m"
        ), "the market is part of a dataset's identity"

    @pytest.mark.asyncio
    async def test_measured_statistics_reach_the_persisted_validation_report(
        self, service, reg, window
    ):
        """Stage 11 records PASSED because it was given a measurement, not SKIPPED.

        The training path is the only caller that HAS the built dataset, so it is the
        caller that must pass the statistics. A report saying ``PASSED`` here and
        ``SKIPPED`` from the plain validate path is the honest pair.
        """
        graph, _model_id = model_graph(reg)
        result = await save(service, reg, graph, epochs=100)

        stages = {entry["stage"]: entry for entry in result["report"].stages}
        assert stages[11]["status"] == V.STATUS_PASSED, stages[11]

        plain = compile_version(graph, reg)
        plain_stage = {e["stage"]: e for e in plain.report.stages}[11]
        assert plain_stage["status"] == V.STATUS_SKIPPED
        assert "NOT confirmed ready" in plain_stage["detail"]

    @pytest.mark.asyncio
    async def test_the_window_is_sized_from_the_gate_arithmetic(
        self, service, reg, window
    ):
        """The fetch is at least warmup + horizon + the gate's own required row count.

        Sizing it by a second rule would let the fetch and the gate disagree, which
        would surface as "insufficient data" for a range the author had configured
        correctly.
        """
        from backend_app.backend.ml_training_policy import (
            ModelSpecView,
            ValidationConfig,
            required_row_count,
        )

        graph, _model_id = model_graph(reg)
        plan = compile_version(graph, reg).plan
        spec = ModelSpecView.for_block_id("xgboost")
        cfg = ValidationConfig.for_model(
            spec, label_horizon=1, feature_lookback=plan.warmup_bars
        )

        await save(service, reg, graph, epochs=100)

        asked = window["calls"][0]["bars"]
        assert asked >= plan.warmup_bars + 1 + required_row_count(spec, cfg)
        assert asked <= S.TRAINING_MAX_BARS
        assert window["calls"][0]["symbol"] == "BTC/USDT"
        assert window["calls"][0]["timeframe"] == "15m"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Requirements 14.3, 14.4, 14.7, 14.8 - no job row on any blocked path
# ═══════════════════════════════════════════════════════════════════════════


class TestNoJobRowOnAGateFailure:
    """A short dataset is refused, and the refusal leaves nothing behind.

    Three lags produce three feature columns, and every model block declares a minimum
    of five. That is the shortfall Requirement 14.3 is about, measured from the built
    dataset rather than estimated from a bar count.
    """

    @pytest.mark.asyncio
    async def test_the_gate_blocks_and_writes_no_job(self, service, reg, db, window):
        graph, _model_id = model_graph(reg, lags=(1, 2, 3))
        result = await save(service, reg, graph, epochs=100)

        training = result["training"]
        assert training["state"] == S.TRAINING_BLOCKED, training
        assert training["reason"] == S.REASON_ML_REQUIREMENTS
        assert db.jobs == [], (
            "Requirement 14.3: a blocked gate creates NO training_jobs row"
        )
        assert training["job_id"] is None
        assert training["jobs"] == []

    @pytest.mark.asyncio
    async def test_the_block_states_required_and_available_for_both_dimensions(
        self, service, reg, window
    ):
        """Requirement 14.9 renders both quantities, so both must be on the wire.

        Reporting only the failing dimension forces the UI back to "insufficient
        data", which is exactly what that requirement exists to delete.
        """
        graph, model_id = model_graph(reg, lags=(1, 2, 3))
        result = await save(service, reg, graph, epochs=100)

        detail = result["training"]["detail"]
        assert set(detail["required"]) >= {"columns", "rows"}
        assert set(detail["available"]) >= {"columns", "rows"}
        assert detail["available"]["columns"] == 3, "measured from the built dataset"
        assert detail["required"]["columns"] >= 5
        assert detail["node_id"] == model_id
        assert "Required:" in detail["message"] and "Available:" in detail["message"]

    @pytest.mark.asyncio
    async def test_the_version_is_still_saved_and_undeployable(
        self, service, reg, db, window
    ):
        """A short history does not make the graph invalid.

        Refusing the save as well would throw away a compiled version and force the
        author to resubmit an identical graph. The version is persisted ``VALIDATED``
        - un-deployable, because no model is bound - and only the training half is
        refused.
        """
        graph, _model_id = model_graph(reg, lags=(1, 2, 3))
        result = await save(service, reg, graph, epochs=100)

        assert result["validation_state"] == "VALID"
        assert len(db.versions) == 1
        assert db.versions[0]["lifecycle_state"] == LIFECYCLE_VALIDATED

    @pytest.mark.asyncio
    async def test_a_rejected_window_blocks_on_data_quality(
        self, service, reg, db, window
    ):
        """Requirement 14.7: a window the validator refuses creates no job either.

        ``market_data_validation`` rejects a 3-sigma close outright rather than
        grading it. Both shapes of refusal mean the same thing here, and neither is a
        500.
        """
        window["generator"] = spiky_bars
        graph, _model_id = model_graph(reg)
        result = await save(service, reg, graph, epochs=100)

        assert result["training"]["state"] == S.TRAINING_BLOCKED
        assert result["training"]["reason"] == S.REASON_DATA_QUALITY
        assert db.jobs == []

    def test_an_unset_market_is_refused_rather_than_substituted(self, reg):
        """No symbol and no timeframe is a refusal, not a ``BTC/USDT`` default.

        The resolver is exercised directly because it is a guard behind a guard: the
        compiler already refuses an ``ohlcv_feed`` with no symbol
        (``PARAM_REQUIRED_MISSING``, held by ``test_sb06_exchange_agnostic_save.py``),
        so this branch is not reachable through the endpoint. It is asserted anyway,
        because the day something else calls it is the day the substitution would come
        back.
        """

        class PlanWithoutMarket:
            data_nodes = ()

            def node(self, node_id):  # pragma: no cover - never reached
                return None

        with pytest.raises(S.TrainingBlocked) as caught:
            S.resolve_training_data_source(PlanWithoutMarket())

        assert caught.value.reason == S.REASON_DATA_SOURCE
        assert "BTC/USDT" not in str(caught.value), (
            "the refusal must not name a market the author did not choose (SB-06)"
        )

    def test_two_markets_are_refused_rather_than_one_being_chosen(self, reg):
        """A model is trained over one bar series, so two is a refusal.

        Also asserted directly, and for the same reason: the compiler's
        ``MULTI_SYMBOL_ACTION_PATH`` rule refuses this graph first, so the resolver is
        a second line rather than the first. Both lines are wanted - the compiler's
        rule is about an ACTION node's provenance, and this one is about a dataset.
        """

        class TwoMarketPlan:
            data_nodes = ("d1", "d2")

            def node(self, node_id):
                params = (
                    {"symbol": "BTC/USDT", "timeframe": "15m"}
                    if node_id == "d1"
                    else {"symbol": "ETH/USDT", "timeframe": "1h"}
                )
                return type("N", (), {"id": node_id, "block_id": "ohlcv_feed",
                                      "params": params})()

        with pytest.raises(S.TrainingBlocked) as caught:
            S.resolve_training_data_source(TwoMarketPlan())

        assert caught.value.reason == S.REASON_DATA_SOURCE
        assert caught.value.detail["symbols"] == ["BTC/USDT", "ETH/USDT"]


class TestNoJobRowOnACapRejection:
    """An over-cap request is refused naming the cap, and writes nothing.

    Requirement 16.3: the response carries the requested value together with the
    permitted value for the exceeded cap. Requirement 16.2 fixes the permitted value at
    ``min(model.max_safe_epochs, tier_cap)``.
    """

    @pytest.mark.asyncio
    async def test_an_over_cap_epoch_count_blocks_and_writes_no_job(
        self, service, reg, db, window
    ):
        from backend_app.backend.ml_training_policy import resolve_caps

        caps = resolve_caps(owner(), "xgboost")
        graph, _model_id = model_graph(reg)

        result = await save(service, reg, graph, epochs=caps.max_epochs + 1)

        training = result["training"]
        assert training["state"] == S.TRAINING_BLOCKED, training
        assert training["reason"] == S.REASON_CAP_EXCEEDED
        assert db.jobs == [], (
            "a cap rejection creates NO training_jobs row (optional task 6.8 asserts "
            "this too; it holds now)"
        )

    @pytest.mark.asyncio
    async def test_the_block_names_the_cap_the_request_and_the_allowance(
        self, service, reg, window
    ):
        from backend_app.backend.ml_training_policy import CAP_MAX_EPOCHS, resolve_caps

        caps = resolve_caps(owner(), "xgboost")
        graph, _model_id = model_graph(reg)

        result = await save(service, reg, graph, epochs=caps.max_epochs + 7)

        detail = result["training"]["detail"]
        assert detail["cap"] == CAP_MAX_EPOCHS
        assert detail["requested"] == caps.max_epochs + 7
        assert detail["allowed"] == caps.max_epochs
        assert detail["allowed"] == min(
            caps.tier_max_epochs, caps.model_max_safe_epochs
        )

    @pytest.mark.asyncio
    async def test_the_request_is_refused_never_silently_clamped(
        self, service, reg, db, window
    ):
        """A refusal, never a quiet reduction.

        Clamping would train a different model from the one the author asked for and
        report success, so the author would never learn their configuration was not
        honoured.
        """
        from backend_app.backend.ml_training_policy import resolve_caps

        caps = resolve_caps(owner(), "xgboost")
        graph, _model_id = model_graph(reg)

        await save(service, reg, graph, epochs=caps.max_epochs + 1)

        assert db.jobs == []

    @pytest.mark.asyncio
    async def test_a_plan_without_ml_training_blocks_rather_than_queues(self, reg, window):
        """Requirement 16.7: the existing entitlement control stays in force.

        A FREE plan carries neither the ``ml_training`` feature nor a non-zero epoch
        allowance, so the training half is refused. The save still succeeds - an
        indicator-only strategy from the same author is unaffected - and no job row is
        created.
        """
        from backend_app.backend.strategy_service import StrategyService

        db = FakeSupabase.seeded(tier="free")
        graph, _model_id = model_graph(reg)
        with patch.object(
            S, "create_request_supabase_async", AsyncMock(return_value=db)
        ):
            result = await StrategyService().create_version_and_maybe_train(
                {**owner(), "plan": "free"},
                STRATEGY_ID,
                graph.to_dict(),
                registry=reg,
                training_cfg={"epochs": 10},
            )

        assert result["training"]["state"] == S.TRAINING_BLOCKED
        assert db.jobs == []
        assert len(db.versions) == 1, "the version is still saved"


# ═══════════════════════════════════════════════════════════════════════════
# 4. Requirement 15.13 - idempotent per (version, node)
# ═══════════════════════════════════════════════════════════════════════════


class TestIdempotencyIsHeldByTheDatabaseConstraint:
    """At most one QUEUED-or-RUNNING job per (version, node), enforced by the index.

    The service's pre-check is a convenience. What holds the invariant against two
    concurrent requests is ``uq_tj_active_per_node``, and the first three tests drive
    the insert path directly so the constraint - not the pre-check - is what answers.
    """

    @pytest.mark.asyncio
    async def test_a_second_insert_for_a_live_node_returns_the_existing_job(self, db):
        """The unique violation becomes the idempotent answer, not a 500.

        This is the concurrent case: both callers passed the pre-check, and only the
        index separates them.
        """
        row = {
            "id": "job-1",
            "user_id": USER_ID,
            "strategy_id": STRATEGY_ID,
            "version_id": "ver-1",
            "node_id": "n_model",
            "block_id": "xgboost",
            "status": "QUEUED",
            "config": {},
        }
        first = await S.insert_training_job(db, row)
        assert first["idempotent"] is False
        assert first["available"] is True

        second = await S.insert_training_job(db, {**row, "id": "job-2"})
        assert second["idempotent"] is True, (
            "a duplicate live job must be translated into the existing one"
        )
        assert second["job"]["id"] == "job-1"
        assert len(db.jobs) == 1, "the index must leave exactly one live job"

    @pytest.mark.asyncio
    async def test_the_uniqueness_is_scoped_to_live_jobs_only(self, db):
        """A finished job leaves the node retrainable.

        This is the partial predicate doing its job. Uniqueness on
        ``(version_id, node_id)`` *without* ``WHERE status IN ('QUEUED','RUNNING')``
        would make a node trainable exactly once, forever - which inverts Requirement
        15.13 rather than relaxing it.
        """
        db.tables["training_jobs"].append(
            {
                "id": "job-old",
                "user_id": USER_ID,
                "version_id": "ver-1",
                "node_id": "n_model",
                "status": "COMPLETED",
            }
        )
        result = await S.insert_training_job(
            db,
            {
                "id": "job-new",
                "user_id": USER_ID,
                "strategy_id": STRATEGY_ID,
                "version_id": "ver-1",
                "node_id": "n_model",
                "block_id": "xgboost",
                "status": "QUEUED",
                "config": {},
            },
        )
        assert result["idempotent"] is False
        assert len(db.jobs) == 2

    @pytest.mark.asyncio
    async def test_a_different_node_of_the_same_version_is_not_blocked(self, db):
        base = {
            "user_id": USER_ID,
            "strategy_id": STRATEGY_ID,
            "version_id": "ver-1",
            "block_id": "xgboost",
            "status": "QUEUED",
            "config": {},
        }
        await S.insert_training_job(db, {**base, "id": "a", "node_id": "n_a"})
        result = await S.insert_training_job(db, {**base, "id": "b", "node_id": "n_b"})

        assert result["idempotent"] is False
        assert len(db.jobs) == 2

    @pytest.mark.asyncio
    async def test_requesting_training_twice_yields_one_job(
        self, service, reg, db, window
    ):
        """The endpoint-level view: a repeat answers with the job that already exists.

        Also the reason the pre-check runs first: the per-user concurrency cap counts
        jobs in QUEUED or RUNNING, so without it the retry would be refused by the cap
        it had itself satisfied a moment earlier.
        """
        graph, model_id = model_graph(reg)
        saved = await save(service, reg, graph, epochs=100)
        version_id = saved["version_id"] if "version_id" in saved else saved["version"]["id"]
        first_job_id = saved["training"]["job_id"]

        repeat = await service.create_training_job(
            owner(),
            version_id,
            node_id=model_id,
            registry=reg,
            training_cfg={"epochs": 100},
        )

        assert repeat["job_id"] == first_job_id
        assert repeat["idempotent_nodes"] == [model_id]
        assert len(db.jobs) == 1, "no second live job for the same (version, node)"

    @pytest.mark.asyncio
    async def test_an_unapplied_migration_degrades_rather_than_crashing(self, reg, window):
        """No ``training_jobs`` table: ``UNAVAILABLE``, naming 004d, and no invented id.

        Migration ``004d_training_and_models.sql`` is applied by hand and there is no
        local PostgreSQL, so this is the path an operator will actually hit. It must not
        be a 500, and above all it must not report a job id nothing will ever pick up.
        """
        from backend_app.backend.strategy_service import StrategyService

        db = FakeSupabase.seeded(with_training_jobs=False)
        graph, _model_id = model_graph(reg)
        with patch.object(
            S, "create_request_supabase_async", AsyncMock(return_value=db)
        ):
            result = await StrategyService().create_version_and_maybe_train(
                owner(), STRATEGY_ID, graph.to_dict(), registry=reg,
                training_cfg={"epochs": 100},
            )

        training = result["training"]
        assert training["state"] == S.TRAINING_UNAVAILABLE, training
        assert training["job_id"] is None
        assert "004d_training_and_models.sql" in training["reason"], (
            "the warning must name the file an operator has to apply"
        )
        assert len(db.versions) == 1, "the version is still saved"
        assert db.versions[0]["lifecycle_state"] == LIFECYCLE_VALIDATED, (
            "nothing is queued, so the version does not claim to be TRAINING"
        )

    def test_the_two_classifiers_recognise_what_postgresql_actually_says(self):
        """The translation is keyed on the codes and the index name, not on prose."""
        assert S.is_missing_training_table_error(MissingRelation("training_jobs"))
        assert S.is_duplicate_active_job_error(UniqueViolation("uq_tj_active_per_node"))
        # And they stay narrow: an unrelated failure is not swallowed as either.
        assert not S.is_missing_training_table_error(Exception("connection reset"))
        assert not S.is_duplicate_active_job_error(Exception("connection reset"))


# ═══════════════════════════════════════════════════════════════════════════
# 5. Requirement 15.7 - cancel sets cancel_requested, and nothing else
# ═══════════════════════════════════════════════════════════════════════════


def live_job(db, *, job_id="job-live", status="QUEUED", user_id=USER_ID):
    row = {
        "id": job_id,
        "user_id": user_id,
        "strategy_id": STRATEGY_ID,
        "version_id": "ver-1",
        "node_id": "n_model",
        "block_id": "xgboost",
        "status": status,
        "cancel_requested": False,
        "config": {},
        "epoch_current": 3,
        "progress": 0.3,
        "updated_at": "2020-01-01T00:00:00+00:00",
    }
    db.tables["training_jobs"].append(row)
    return row


class TestCancellationIsCooperative:
    @pytest.mark.asyncio
    async def test_cancel_sets_the_flag_and_leaves_the_status_alone(self, service, db):
        row = live_job(db, status="RUNNING")
        before = row["updated_at"]

        result = await service.request_job_cancellation(owner(), "job-live")

        assert result["cancel_requested"] is True
        assert result["status"] == "RUNNING"
        assert row["cancel_requested"] is True
        assert row["status"] == "RUNNING", (
            "the WORKER sets CANCELLED at the next epoch boundary; marking it here "
            "would make the row disagree with a process still writing epochs"
        )
        assert row["updated_at"] != before, (
            "004d attaches no BEFORE UPDATE trigger to training_jobs, so every writer "
            "sets updated_at itself"
        )

    @pytest.mark.asyncio
    async def test_cancel_binds_no_model_and_touches_no_progress(self, service, db):
        row = live_job(db, status="RUNNING")
        await service.request_job_cancellation(owner(), "job-live")

        assert row["epoch_current"] == 3, "cancelling does not rewrite what happened"
        assert row["progress"] == 0.3
        assert "model_version_id" not in row

    @pytest.mark.asyncio
    async def test_cancel_is_idempotent_and_says_so(self, service, db):
        live_job(db)
        first = await service.request_job_cancellation(owner(), "job-live")
        second = await service.request_job_cancellation(owner(), "job-live")

        assert first["already_requested"] is False
        assert second["already_requested"] is True
        assert second["cancel_requested"] is True

    @pytest.mark.asyncio
    async def test_a_finished_job_is_a_state_conflict_not_a_silent_success(
        self, service, db
    ):
        from backend_app.backend.strategy_service import DeployPrerequisiteError

        row = live_job(db, status="COMPLETED")
        with pytest.raises(DeployPrerequisiteError) as caught:
            await service.request_job_cancellation(owner(), "job-live")

        assert "COMPLETED" in str(caught.value)
        assert row["cancel_requested"] is False

    @pytest.mark.asyncio
    async def test_another_users_job_is_not_found_and_is_untouched(self, service, db):
        row = live_job(db, user_id=OTHER_USER_ID)

        with pytest.raises(ValueError) as caught:
            await service.request_job_cancellation(owner(), "job-live")

        assert "not found" in str(caught.value).lower(), (
            "another tenant's job is reported exactly as a missing one, so existence "
            "does not leak"
        )
        assert row["cancel_requested"] is False

    @pytest.mark.asyncio
    async def test_an_unapplied_migration_is_not_found_rather_than_a_crash(self):
        from backend_app.backend.strategy_service import StrategyService

        db = FakeSupabase.seeded(with_training_jobs=False)
        with patch.object(
            S, "create_request_supabase_async", AsyncMock(return_value=db)
        ):
            with pytest.raises(ValueError):
                await StrategyService().request_job_cancellation(owner(), "job-live")


# ═══════════════════════════════════════════════════════════════════════════
# 6. Requirement 12.6 / SB-06 - the config holds the data source, never a venue
# ═══════════════════════════════════════════════════════════════════════════


def forbidden_keys_in(payload, path="config"):
    """Every key at any depth of ``payload`` naming exchange identity or a secret."""
    from backend_app.backend.strategy_dag.schema import is_forbidden_param

    found = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if is_forbidden_param(key):
                found.append(f"{path}.{key}")
            found.extend(forbidden_keys_in(value, f"{path}.{key}"))
    elif isinstance(payload, (list, tuple)):
        for position, value in enumerate(payload):
            found.extend(forbidden_keys_in(value, f"{path}[{position}]"))
    return found


class TestThePersistedConfigCarriesNoExchangeIdentity:
    """Task 8.7's Property 23 scans this exact column. It holds now.

    The configuration's job is to name the RESOLVED DATA SOURCE - the market the
    graph's DATA block declares - so the worker can refetch the same window
    (Requirement 12.6). A venue is a deployment-time choice and a credential lives only
    in the vault, so neither belongs here.
    """

    @pytest.mark.asyncio
    async def test_no_forbidden_key_at_any_depth(self, service, reg, db, window):
        graph, _model_id = model_graph(reg)
        await save(service, reg, graph, epochs=100)

        config = db.jobs[0]["config"]
        assert forbidden_keys_in(config) == [], (
            f"training_jobs.config must hold no exchange identity or credential; the "
            f"forbidden vocabulary is {list(FORBIDDEN_PARAM_FIELDS)}"
        )

    @pytest.mark.asyncio
    async def test_the_config_names_the_resolved_data_source(
        self, service, reg, db, window
    ):
        graph, _model_id = model_graph(reg)
        await save(service, reg, graph, epochs=100)

        source = db.jobs[0]["config"]["data_source"]
        assert source["symbol"] == "BTC/USDT"
        assert source["timeframe"] == "15m"
        assert source["block_id"] == "ohlcv_feed"
        assert source["node_id"], "the data source names the node it was resolved from"
        assert "exchange" not in source and "venue" not in source

    @pytest.mark.asyncio
    async def test_the_serialized_config_holds_no_venue_literal(
        self, service, reg, db, window, monkeypatch
    ):
        """Even the venue the server actually read from must not reach the row.

        ``fetch_training_bars`` resolves the feed from ``DEFAULT_EXCHANGE`` at fetch
        time. That is a server setting, not part of the strategy, and a value that never
        enters the configuration cannot leak from it.
        """
        monkeypatch.setenv("DEFAULT_EXCHANGE", "kraken")
        graph, _model_id = model_graph(reg)
        await save(service, reg, graph, epochs=100)

        serialized = json.dumps(db.jobs[0]["config"]).lower()
        for venue in ("kraken", "binance", "coinbase", "bybit", "okx"):
            assert venue not in serialized, (
                f"{venue!r} must not reach training_jobs.config"
            )

    def test_the_assembly_guard_refuses_every_forbidden_key(self):
        """The structural guard fails at assembly, not in a later audit.

        A future field that smuggles a venue or a key in has to get past this, and it
        fails before the write rather than being found by Property 23 in a column that
        already holds it.
        """
        with pytest.raises(RuntimeError) as caught:
            S.assert_no_exchange_identity({"splits": {"nested": {"api_key": "AK"}}})
        assert "api_key" in str(caught.value)

        for key in FORBIDDEN_PARAM_FIELDS:
            with pytest.raises(RuntimeError):
                S.assert_no_exchange_identity({key: "value"})

        with pytest.raises(RuntimeError):
            S.assert_no_exchange_identity({"list": [{"apiKey": "ccxt spelling"}]})

        # And it lets an honest configuration through unchanged.
        S.assert_no_exchange_identity(
            {"symbol": "BTC/USDT", "timeframe": "15m", "splits": {"embargo_bars": 12}}
        )
