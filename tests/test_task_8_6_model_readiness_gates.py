"""
tests/test_task_8_6_model_readiness_gates.py

The model checksum gate and the feature schema drift gate, held in place.

Spec: strategy-builder task 8.6. ``design.md`` -> "DAG runtime contract" (the
``AWAITING_MODEL`` row of the imperfect-reality table) and the "Model artifact checksum
mismatch" / "Model/feature schema drift" rows of the failure-mode table.
Requirements 17.6 and 17.7.

WHAT THESE TESTS HOLD IN PLACE
------------------------------
* **17.6 - a computed checksum that differs is refused, twice.** The requirement names
  two enforcers: the DAG_Runtime marks the node awaiting a model, and the
  Deployment_Service holds the deployment out of the running state. Both are asserted from
  the one verdict - ``ArtifactVerdict.runtime_state`` is ``AWAITING_MODEL`` and the deploy
  gate raises ``MODEL_AWAITING_ARTIFACT`` (409) - so the two halves cannot drift apart.

* **The checksum is the platform's own.** ``SafeModelLoader.compute_checksum`` computes it,
  reached through task 6.5's ``ArtifactStore.checksum``. A test computes the digest both
  ways over the same real file on disk and requires them to agree, so this gate cannot
  acquire a second idea of what an artifact's checksum is.

* **17.7 - the "actual" columns are the real ones.** ``declared_feature_columns`` walks the
  plan and names the columns statically; ``strategy_service.run_feature_pipeline`` computes
  them for real from real bars. The two are required to produce the **same ordered list**,
  for a single feature block, for a ``feat_concat`` of two, and through ``feat_select``.
  That agreement is the whole basis of the gate: a static walk that disagreed with the
  pipeline would refuse good deployments and admit drifted ones.

* **17.7 reports expected AND actual.** Every drift refusal is required to carry both
  ordered column lists plus the missing and extra sets. "The schema drifted" without the
  columns is not something an author can act on.

* **The comparison is ``FeatureValidator.check_model_compatibility``.** Reused as-is, with
  the recorded contract presented under sklearn's own attribute names. A test drives the
  real validator over the same pair and requires the verdict to agree with it, and a
  structural test requires the module to call it.

* **Fail closed, everywhere.** No artifact reference, no recorded checksum, an unreadable
  store, a recorded schema with no column names, a plan whose columns cannot be named, a
  model node the graph no longer has, a block that changed underneath the model: each is a
  refusal, never a pass. "Could not tell" is not "it verified".

* **Migrations 004d/004e unapplied is a classified refusal naming the file, never a 500.**
  With ``model_versions`` absent the deploy gate raises ``MODEL_VERSIONS_UNAVAILABLE``
  (503) naming ``004d_training_and_models.sql``, and the degradation is logged.

* **The gate is upstream of the ownership reads.** A version whose artifact does not match
  its checksum is refused before any exchange account or risk config is resolved, so a
  deploy that was never going to happen puts no account id in a log line.

Nothing under test is mocked. The registry, the compiler, the compiled plan, the feature
executors, the artifact store (a real one, on a real temporary directory), the SHA-256
routine and the feature validator are all the real ones. The fake is the PostgREST client,
which is the one thing this environment has no instance of.
"""

import io
import json
import os
import sys
import tokenize
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import deployment_binding as db
from backend_app.backend import model_readiness as mr
from backend_app.backend import model_versioning as MV
from backend_app.backend import strategy_service as S
from backend_app.backend.strategy_compiler import compile_graph
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag.plan import CompiledPlan
from backend_app.backend.strategy_dag.schema import EdgeSpec, NodeSpec, StrategyGraph
from backend_app.core.ml_safety import SafeModelLoader

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "backend_app" / "backend" / "model_readiness.py"

USER_ID = "user_task_8_6"
OTHER_USER_ID = "user_someone_else_8_6"
STRATEGY_ID = "strategy_task_8_6"
VERSION_ID = "55555555-5555-4555-8555-555555555555"
MODEL_VERSION_ID = "66666666-6666-4666-8666-666666666666"
ARTIFACT_BYTES = b"a real artifact's bytes, checksummed as they are stored"


# ---------------------------------------------------------------------------
# Real graphs, compiled by the real compiler
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def registry():
    return registry_module.build_registry()


def _node(registry, block_id, **params):
    return NodeSpec.create(block_id, registry[block_id].category, params=params)


def build_plan(
    registry,
    *,
    lags=(1, 2, 3),
    time_parts=None,
    select=None,
    model_block="xgboost",
):
    """A real ``CompiledPlan`` whose ML node is fed by a real feature chain.

    Returns ``(plan, ml_node_id)``. The shape varies only in the feature chain, because
    that is the part Requirement 17.7 compares: one feature block, a ``feat_concat`` of
    two, or a ``feat_select`` projection over either.
    """
    data = _node(
        registry,
        "ohlcv_feed",
        symbol="BTC/USDT",
        timeframe="15m",
        market_type="spot",
        mode="streaming",
    )
    ema = _node(registry, "ema", window=20, source="close")
    lag = _node(registry, "feat_lag", lags=list(lags))
    model = _node(registry, model_block)
    const = _node(registry, "constant", value=0.5)
    gate = _node(registry, "gt")
    action = _node(
        registry,
        "action_buy_market",
        quantity_type="percent_of_equity",
        quantity=0.25,
    )

    nodes = [data, ema, lag, model, const, gate, action]
    edges = [
        EdgeSpec.create(data.id, "close", ema.id, "series"),
        EdgeSpec.create(ema.id, "value", lag.id, "series"),
        EdgeSpec.create(model.id, "prediction", gate.id, "left"),
        EdgeSpec.create(const.id, "value", gate.id, "right"),
        EdgeSpec.create(gate.id, "out", action.id, "signal"),
    ]

    tail = lag
    if time_parts:
        times = _node(registry, "feat_time", parts=list(time_parts))
        concat = _node(registry, "feat_concat")
        nodes += [times, concat]
        edges += [
            EdgeSpec.create(data.id, "frame", times.id, "frame"),
            EdgeSpec.create(lag.id, "matrix", concat.id, "matrix"),
            EdgeSpec.create(times.id, "matrix", concat.id, "matrix"),
        ]
        tail = concat
    if select is not None:
        projection = _node(registry, "feat_select", columns=list(select))
        nodes.append(projection)
        edges.append(EdgeSpec.create(tail.id, "matrix", projection.id, "matrix"))
        tail = projection

    edges.append(EdgeSpec.create(tail.id, "matrix", model.id, "features"))
    plan = compile_graph(StrategyGraph(nodes=nodes, edges=edges), registry)
    return plan, model.id


def bars(count, *, step_ms=900_000, start=1_600_000_000_000):
    """A deterministic, evenly spaced, OHLC-consistent window of CCXT rows."""
    rows = []
    price = 100.0
    for index in range(count):
        price += 0.25 if index % 3 else -0.15
        high = price + 0.6
        low = price - 0.6
        rows.append(
            [start + index * step_ms, price, high, low, price + 0.1, 1_000.0 + index]
        )
    return rows


# ---------------------------------------------------------------------------
# A real artifact store on a real directory
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_module_state():
    MV.reset_artifact_store()
    db.reset_binding_column_support()
    yield
    MV.reset_artifact_store()
    db.reset_binding_column_support()


@pytest.fixture
def store(tmp_path):
    local = MV.LocalArtifactStore(tmp_path / "artifacts")
    MV.register_artifact_store(local)
    return local


@pytest.fixture
def stored_artifact(store):
    """``(uri, checksum)`` for real bytes written through the real store."""
    uri = store.put("u/s/v/node/model.joblib", ARTIFACT_BYTES)
    return uri, store.checksum(uri)


def model_row(uri, checksum, *, node_id, block_id="xgboost", feature_names=("lag_1",),
              feature_count=None, user_id=USER_ID, **overrides):
    """One ``model_versions`` row, shaped as task 6.5's writer records it."""
    row = {
        "id": MODEL_VERSION_ID,
        "user_id": user_id,
        "version_id": VERSION_ID,
        "node_id": node_id,
        "block_id": block_id,
        "model_version": 1,
        "is_active": True,
        "artifact_uri": uri,
        "artifact_checksum": checksum,
        "serialization": "joblib",
        "feature_schema": {
            "feature_names": list(feature_names),
            "feature_count": (
                feature_count if feature_count is not None else len(feature_names)
            ),
        },
    }
    row.update(overrides)
    return row


def _executable_python(path: Path) -> str:
    """``path``'s source with every comment and string literal removed.

    A structural claim has to be about code. This module's docstring discusses the
    functions it reuses by name, so a naive substring search would be satisfied by the
    prose that explains the reuse rather than by the reuse.
    """
    kept = []
    with path.open("rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            kept.append(token.string)
    return "\n".join(kept)


# ---------------------------------------------------------------------------
# 1. Requirement 17.6 - the artifact checksum gate
# ---------------------------------------------------------------------------


class TestArtifactChecksumGate:
    def test_the_stored_artifact_matches_and_the_node_is_ready(
        self, registry, stored_artifact
    ):
        uri, checksum = stored_artifact
        _plan, ml = build_plan(registry)
        verdict = mr.verify_artifact_checksum(model_row(uri, checksum, node_id=ml))

        assert verdict.ok
        assert verdict.code == mr.CODE_MODEL_READY
        assert verdict.runtime_state == mr.NODE_READY
        assert verdict.details["recorded_checksum"] == checksum

    def test_the_checksum_is_safemodelloaders_own_over_the_stored_file(
        self, store, stored_artifact
    ):
        """No second checksum routine, and no second idea of what is being hashed.

        The digest the gate recomputes is ``SafeModelLoader.compute_checksum`` over the
        bytes as they are actually on disk. Computed both ways here and required to agree,
        so the recorded value and the verified value can never be produced by two
        different functions.
        """
        uri, checksum = stored_artifact
        assert checksum == SafeModelLoader.compute_checksum(str(store.path_of(
            "u/s/v/node/model.joblib"
        )))
        assert "compute_checksum" in _executable_python(
            REPO_ROOT / "backend_app" / "backend" / "model_versioning.py"
        )

    def test_a_tampered_artifact_is_a_mismatch_and_the_node_awaits_a_model(
        self, registry, store, stored_artifact
    ):
        """17.6's condition, on a real file that really changed underneath the row."""
        uri, checksum = stored_artifact
        path = store.path_of("u/s/v/node/model.joblib")
        path.write_bytes(ARTIFACT_BYTES + b" and one byte more")

        _plan, ml = build_plan(registry)
        verdict = mr.verify_artifact_checksum(model_row(uri, checksum, node_id=ml))

        assert not verdict.ok
        assert verdict.code == mr.CODE_ARTIFACT_CHECKSUM_MISMATCH
        assert verdict.runtime_state == mr.AWAITING_MODEL
        assert verdict.details["recorded_checksum"] == checksum
        assert verdict.details["computed_checksum"] != checksum
        assert verdict.details["computed_checksum"] == store.checksum(uri)

    def test_an_artifact_that_is_not_there_is_refused_rather_than_raising(
        self, registry, store
    ):
        _plan, ml = build_plan(registry)
        verdict = mr.verify_artifact_checksum(
            model_row(store.uri_for("u/s/v/node/gone.joblib"), "a" * 64, node_id=ml)
        )

        assert not verdict.ok
        assert verdict.code == mr.CODE_ARTIFACT_UNREADABLE
        assert verdict.runtime_state == mr.AWAITING_MODEL

    def test_a_store_that_blows_up_is_refused_rather_than_raising(self, registry):
        class ExplodingStore:
            def checksum(self, uri):
                raise RuntimeError("object storage said no")

        _plan, ml = build_plan(registry)
        verdict = mr.verify_artifact_checksum(
            model_row("s3://bucket/x", "b" * 64, node_id=ml), store=ExplodingStore()
        )

        assert not verdict.ok
        assert verdict.code == mr.CODE_ARTIFACT_UNREADABLE
        assert verdict.runtime_state == mr.AWAITING_MODEL
        assert "RuntimeError" == verdict.details["failure"]

    @pytest.mark.parametrize(
        "overrides, code",
        [
            ({"artifact_uri": ""}, mr.CODE_ARTIFACT_REFERENCE_MISSING),
            ({"artifact_uri": None}, mr.CODE_ARTIFACT_REFERENCE_MISSING),
            ({"artifact_checksum": ""}, mr.CODE_ARTIFACT_CHECKSUM_MISSING),
            ({"artifact_checksum": None}, mr.CODE_ARTIFACT_CHECKSUM_MISSING),
        ],
    )
    def test_a_vacuous_comparison_is_a_refusal_not_a_pass(
        self, registry, stored_artifact, overrides, code
    ):
        """A comparison against nothing is exactly the hole 17.6 exists to close."""
        uri, checksum = stored_artifact
        _plan, ml = build_plan(registry)
        verdict = mr.verify_artifact_checksum(
            model_row(uri, checksum, node_id=ml, **overrides)
        )

        assert not verdict.ok
        assert verdict.code == code
        assert verdict.runtime_state == mr.AWAITING_MODEL

    def test_model_ready_is_the_runtimes_seam_and_no_row_is_not_ready(
        self, registry, stored_artifact
    ):
        """``design.md``'s ``model_ready(node, state)``. ``None`` is the unloaded half."""
        uri, checksum = stored_artifact
        _plan, ml = build_plan(registry)

        assert mr.model_ready(model_row(uri, checksum, node_id=ml)) is True
        assert mr.model_ready(None) is False
        assert mr.model_ready({}) is False

    def test_nothing_in_the_module_deserializes_the_artifact(self):
        """The gate is the digest alone.

        ``joblib.load`` on an authenticated deploy path is arbitrary code execution and an
        unbounded allocation, and 17.6 asks about the checksum rather than about the
        object. Loading stays in the runtime's loader, where the model is going to be
        executed anyway.
        """
        code = _executable_python(MODULE_PATH)
        for banned in ("joblib", "load_model", "pickle"):
            assert banned not in code


# ---------------------------------------------------------------------------
# 2. Requirement 17.7 - the "actual" columns are the real ones
# ---------------------------------------------------------------------------


class TestDeclaredColumnsAgreeWithTheFeaturePipeline:
    """The static walk and the real pipeline must name the same columns, in order.

    This is the load-bearing test of the drift gate. The walk exists because computing
    features at deploy time would put a market-data fetch on the deploy path; the price of
    that choice is that the walk has to be *exact*, and exactness is only demonstrable
    against the pipeline that produced the schema being compared.
    """

    @staticmethod
    async def _real_columns(plan, registry, ml_node_id):
        frame = S.training_frame(bars(600), 600)
        matrix = await S.run_feature_pipeline(plan, registry, frame, ml_node_id)
        return list(matrix.columns)

    @pytest.mark.asyncio
    async def test_one_feature_block(self, registry):
        plan, ml = build_plan(registry, lags=(1, 2, 3))
        declared, reason = mr.declared_feature_columns(plan, ml, registry)

        assert reason is None
        assert declared == await self._real_columns(plan, registry, ml)
        assert declared == ["lag_1", "lag_2", "lag_3"]

    @pytest.mark.asyncio
    async def test_a_concat_of_two_matrices_in_edge_order(self, registry):
        """Order comes from the edges, not from sorting - as ``concat_matrices`` does."""
        plan, ml = build_plan(registry, lags=(1,), time_parts=("hour", "dow"))
        declared, reason = mr.declared_feature_columns(plan, ml, registry)

        assert reason is None
        assert declared == await self._real_columns(plan, registry, ml)
        assert declared == ["lag_1", "time_hour", "time_dow"]

    @pytest.mark.asyncio
    async def test_a_select_projection(self, registry):
        plan, ml = build_plan(registry, lags=(1, 2, 3), select=("lag_1", "lag_3"))
        declared, reason = mr.declared_feature_columns(plan, ml, registry)

        assert reason is None
        assert declared == await self._real_columns(plan, registry, ml)
        assert declared == ["lag_1", "lag_3"]

    def test_the_walk_derives_no_column_name_of_its_own(self):
        """``dag_engine._feature_column_names`` is the naming authority, reused."""
        assert "_feature_column_names" in _executable_python(MODULE_PATH)

    def test_a_model_node_with_no_feature_matrix_is_undetermined(self, registry):
        plan, ml = build_plan(registry)
        payload = json.loads(json.dumps(plan.to_dict()))
        payload["inbound"][ml].pop("features", None)
        stripped = CompiledPlan.from_dict(payload)
        declared, reason = mr.declared_feature_columns(stripped, ml, registry)

        assert declared is None
        assert ml in reason

    def test_a_block_the_registry_does_not_publish_is_undetermined_not_guessed(
        self, registry
    ):
        """Fail closed: a schema that cannot be compared has not matched."""

        class _RegistryMissingFeatureBlocks:
            def get(self, block_id):
                return None if block_id.startswith("feat_") else registry.get(block_id)

        plan, ml = build_plan(registry)
        declared, reason = mr.declared_feature_columns(
            plan, ml, _RegistryMissingFeatureBlocks()
        )

        assert declared is None
        assert "descriptor" in reason

    def test_an_unknown_node_id_is_undetermined(self, registry):
        plan, _ml = build_plan(registry)
        declared, reason = mr.declared_feature_columns(plan, "n_not_here", registry)

        assert declared is None
        assert "n_not_here" in reason


# ---------------------------------------------------------------------------
# 3. Requirement 17.7 - the comparison and its report
# ---------------------------------------------------------------------------


class TestFeatureSchemaDriftGate:
    def test_a_matching_schema_reports_expected_and_actual(
        self, registry, stored_artifact
    ):
        uri, checksum = stored_artifact
        plan, ml = build_plan(registry, lags=(1, 2))
        verdict = mr.compare_feature_schema(
            model_row(uri, checksum, node_id=ml, feature_names=("lag_1", "lag_2")),
            plan,
            registry,
        )

        assert verdict.ok
        assert verdict.code == mr.CODE_FEATURE_SCHEMA_MATCHES
        assert verdict.expected == ["lag_1", "lag_2"]
        assert verdict.actual == ["lag_1", "lag_2"]
        assert verdict.warnings == ()

    def test_a_column_the_graph_no_longer_produces_is_drift_naming_both_sides(
        self, registry, stored_artifact
    ):
        """17.7's report, in full: expected, actual, missing and extra."""
        uri, checksum = stored_artifact
        plan, ml = build_plan(registry, lags=(1, 2))
        verdict = mr.compare_feature_schema(
            model_row(
                uri, checksum, node_id=ml, feature_names=("lag_1", "lag_2", "lag_5")
            ),
            plan,
            registry,
        )

        assert not verdict.ok
        assert verdict.code == mr.CODE_FEATURE_SCHEMA_DRIFT
        assert verdict.expected == ["lag_1", "lag_2", "lag_5"]
        assert verdict.actual == ["lag_1", "lag_2"]
        assert verdict.details["missing_feature_columns"] == ["lag_5"]
        assert verdict.details["extra_feature_columns"] == []
        assert verdict.details["expected_feature_count"] == 3
        assert verdict.details["actual_feature_count"] == 2

    def test_a_column_the_graph_gained_is_drift_naming_it(
        self, registry, stored_artifact
    ):
        uri, checksum = stored_artifact
        plan, ml = build_plan(registry, lags=(1, 2), time_parts=("hour",))
        verdict = mr.compare_feature_schema(
            model_row(uri, checksum, node_id=ml, feature_names=("lag_1", "lag_2")),
            plan,
            registry,
        )

        assert not verdict.ok
        assert verdict.code == mr.CODE_FEATURE_SCHEMA_DRIFT
        assert verdict.details["extra_feature_columns"] == ["time_hour"]
        assert verdict.actual == ["lag_1", "lag_2", "time_hour"]

    def test_the_verdict_agrees_with_the_real_feature_validator(
        self, registry, stored_artifact
    ):
        """The comparison is ``check_model_compatibility``, not a second one written here.

        The real validator is driven over the same pair the gate compared, and its answer
        is required to be the gate's answer. A locally written comparison could drift from
        the one the inference path actually applies, which would make a green deploy gate
        no evidence at all.
        """
        import pandas as pd

        from backend_app.backend.feature_validator import (
            FeatureValidator,
            ModelMismatchError,
        )

        uri, checksum = stored_artifact
        plan, ml = build_plan(registry, lags=(1, 2))
        recorded = ("lag_1", "lag_2", "lag_5")
        verdict = mr.compare_feature_schema(
            model_row(uri, checksum, node_id=ml, feature_names=recorded), plan, registry
        )

        class _Contract:
            n_features_in_ = len(recorded)
            feature_names_in_ = recorded

        with pytest.raises(ModelMismatchError):
            FeatureValidator.check_model_compatibility(
                _Contract(), pd.DataFrame(columns=list(verdict.actual), dtype=float)
            )
        assert not verdict.ok
        assert "check_model_compatibility" in _executable_python(MODULE_PATH)

    def test_an_order_only_difference_warns_rather_than_refuses(
        self, registry, stored_artifact
    ):
        """The inference path projects onto the recorded order, so this is normalised.

        ``FeatureValidator.validate_features`` does ``feature_df[schema.expected_features]``
        before the model sees the frame. Refusing a deploy for something the runtime fixes
        would be refusing on a difference that has no consequence - but an author who
        reordered their feature blocks should still be told.
        """
        uri, checksum = stored_artifact
        plan, ml = build_plan(registry, lags=(1,), time_parts=("hour", "dow"))
        verdict = mr.compare_feature_schema(
            model_row(
                uri,
                checksum,
                node_id=ml,
                feature_names=("time_dow", "lag_1", "time_hour"),
            ),
            plan,
            registry,
        )

        assert verdict.ok
        assert [w["code"] for w in verdict.warnings] == [
            mr.WARNING_FEATURE_ORDER_DIFFERS
        ]
        assert verdict.expected == ["time_dow", "lag_1", "time_hour"]
        assert verdict.actual == ["lag_1", "time_hour", "time_dow"]

    @pytest.mark.parametrize(
        "schema",
        [None, {}, {"feature_count": 2}, {"feature_names": []}, "not a document"],
    )
    def test_a_schema_that_records_no_columns_is_refused(
        self, registry, stored_artifact, schema
    ):
        uri, checksum = stored_artifact
        plan, ml = build_plan(registry)
        verdict = mr.compare_feature_schema(
            model_row(uri, checksum, node_id=ml, feature_schema=schema), plan, registry
        )

        assert not verdict.ok
        assert verdict.code == mr.CODE_FEATURE_SCHEMA_NOT_RECORDED

    def test_a_model_bound_to_a_node_this_graph_no_longer_has_is_refused(
        self, registry, stored_artifact
    ):
        uri, checksum = stored_artifact
        plan, _ml = build_plan(registry)
        verdict = mr.compare_feature_schema(
            model_row(uri, checksum, node_id="n_gone"), plan, registry
        )

        assert not verdict.ok
        assert verdict.code == mr.CODE_MODEL_NODE_NOT_IN_PLAN
        assert verdict.expected == ["lag_1"]

    def test_a_node_that_changed_model_block_is_refused(
        self, registry, stored_artifact
    ):
        """Comparing columns across a changed model block compares unrelated things."""
        uri, checksum = stored_artifact
        plan, ml = build_plan(registry, model_block="lightgbm")
        verdict = mr.compare_feature_schema(
            model_row(uri, checksum, node_id=ml, block_id="xgboost"), plan, registry
        )

        assert not verdict.ok
        assert verdict.code == mr.CODE_MODEL_BLOCK_CHANGED
        assert verdict.details["recorded_block_id"] == "xgboost"
        assert verdict.details["plan_block_id"] == "lightgbm"

    def test_columns_that_cannot_be_named_are_refused_not_assumed_to_match(
        self, registry, stored_artifact
    ):
        class _RegistryMissingFeatureBlocks:
            def get(self, block_id):
                return None if block_id.startswith("feat_") else registry.get(block_id)

        uri, checksum = stored_artifact
        plan, ml = build_plan(registry)
        verdict = mr.compare_feature_schema(
            model_row(uri, checksum, node_id=ml),
            plan,
            _RegistryMissingFeatureBlocks(),
        )

        assert not verdict.ok
        assert verdict.code == mr.CODE_FEATURE_COLUMNS_UNDETERMINED
        assert verdict.actual is None
        assert verdict.expected == ["lag_1"]

    def test_a_recorded_count_that_disagrees_with_the_names_is_drift(
        self, registry, stored_artifact
    ):
        """``n_features_in_`` is checked as well as the names, by the real validator."""
        uri, checksum = stored_artifact
        plan, ml = build_plan(registry, lags=(1,))
        verdict = mr.compare_feature_schema(
            model_row(
                uri, checksum, node_id=ml, feature_names=("lag_1",), feature_count=7
            ),
            plan,
            registry,
        )

        assert not verdict.ok
        assert verdict.code == mr.CODE_FEATURE_SCHEMA_DRIFT


# ---------------------------------------------------------------------------
# 4. Both gates, over a whole version
# ---------------------------------------------------------------------------


class TestEvaluateModelGates:
    def test_a_graph_with_no_model_node_passes_and_checks_nothing(self, registry):
        data = _node(
            registry,
            "ohlcv_feed",
            symbol="BTC/USDT",
            timeframe="15m",
            market_type="spot",
            mode="streaming",
        )
        ema = _node(registry, "ema", window=20, source="close")
        const = _node(registry, "constant", value=0.5)
        gate = _node(registry, "gt")
        action = _node(
            registry,
            "action_buy_market",
            quantity_type="percent_of_equity",
            quantity=0.25,
        )
        plan = compile_graph(
            StrategyGraph(
                nodes=[data, ema, const, gate, action],
                edges=[
                    EdgeSpec.create(data.id, "close", ema.id, "series"),
                    EdgeSpec.create(ema.id, "value", gate.id, "left"),
                    EdgeSpec.create(const.id, "value", gate.id, "right"),
                    EdgeSpec.create(gate.id, "out", action.id, "signal"),
                ],
            ),
            registry,
        )
        report = mr.evaluate_model_gates(VERSION_ID, plan, {}, registry=registry)

        assert report.ok
        assert report.artifacts == ()
        assert report.schemas == ()

    def test_an_ml_node_with_no_active_model_version_is_reported_as_unbound(
        self, registry
    ):
        plan, ml = build_plan(registry)
        report = mr.evaluate_model_gates(VERSION_ID, plan, {}, registry=registry)

        assert not report.ok
        assert report.unbound_nodes == (ml,)

    def test_a_checksum_failure_stops_the_schema_being_compared(
        self, registry, store, stored_artifact
    ):
        """Drift measured against an artifact we know is not the artifact is not a fact."""
        uri, checksum = stored_artifact
        store.path_of("u/s/v/node/model.joblib").write_bytes(b"different")

        plan, ml = build_plan(registry)
        report = mr.evaluate_model_gates(
            VERSION_ID,
            plan,
            {ml: model_row(uri, checksum, node_id=ml, feature_names=("nonsense",))},
            registry=registry,
        )

        assert not report.ok
        assert report.first_artifact_failure().code == mr.CODE_ARTIFACT_CHECKSUM_MISMATCH
        assert report.schemas == ()

    def test_a_stray_row_for_a_node_the_plan_lacks_is_not_consulted(
        self, registry, stored_artifact
    ):
        """The plan decides which nodes are checked, so a stale row cannot pass a version."""
        uri, checksum = stored_artifact
        plan, ml = build_plan(registry)
        report = mr.evaluate_model_gates(
            VERSION_ID,
            plan,
            {"n_stale": model_row(uri, checksum, node_id="n_stale")},
            registry=registry,
        )

        assert not report.ok
        assert report.unbound_nodes == (ml,)

    def test_a_clean_version_passes_both_gates_with_no_warnings(
        self, registry, stored_artifact
    ):
        uri, checksum = stored_artifact
        plan, ml = build_plan(registry, lags=(1, 2))
        report = mr.evaluate_model_gates(
            VERSION_ID,
            plan,
            {ml: model_row(uri, checksum, node_id=ml, feature_names=("lag_1", "lag_2"))},
            registry=registry,
        )

        assert report.ok
        assert report.warnings == []


class TestThisModuleRaisesNothing:
    def test_no_refusal_is_raised_from_the_readiness_module(self):
        """The runtime gate and the deploy refusal cannot be the same exception.

        The runtime wants a label to put on a node; the Deployment_Service wants something
        it can turn into an HTTP status. One exception type cannot serve both without the
        runtime catching exceptions on its hot path, so every function here returns a
        verdict and ``deployment_binding`` is the single place a verdict becomes a
        ``DeployRejected``.
        """
        code = _executable_python(MODULE_PATH)
        assert "DeployRejected" not in code
        assert "HTTPException" not in code
        assert "fastapi" not in code


# ---------------------------------------------------------------------------
# 5. The deploy gate - 17.6's second enforcer and 17.7's refusal
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, parent, table):
        self._parent = parent
        self._table = table
        self._filters = {}

    def select(self, columns="*", *a, **kw):
        self._parent.selects.append(self._table)
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def limit(self, *a, **kw):
        return self

    def execute(self):
        if self._table in self._parent.absent_tables:
            raise RuntimeError(f'relation "{self._table}" does not exist (42P01)')
        rows = [
            row
            for row in self._parent.rows.get(self._table, [])
            if all(str(row.get(k)) == str(v) for k, v in self._filters.items())
        ]
        return _Result(rows)


class _Supabase:
    """A PostgREST stand-in that honours ``.eq`` filters and can lack a table."""

    def __init__(self, rows=None, absent_tables=()):
        self.rows = {k: [dict(r) for r in v] for k, v in (rows or {}).items()}
        self.absent_tables = set(absent_tables)
        self.selects = []

    def table(self, name):
        return _Query(self, name)


def _version_row(plan, **overrides):
    row = {
        "id": VERSION_ID,
        "strategy_id": STRATEGY_ID,
        "version": "v3.0",
        "lifecycle_state": "READY",
        "dag_hash": plan.dag_hash,
        "compiled_plan": json.loads(json.dumps(plan.to_dict())),
        "is_current": True,
    }
    row.update(overrides)
    return row


def _user(user_id=USER_ID):
    return {"id": user_id, "email": f"{user_id}@example.com", "access_token": "tok"}


@pytest.mark.asyncio
class TestDeployGate:
    async def test_a_clean_version_is_admitted_with_no_warnings(
        self, registry, stored_artifact
    ):
        uri, checksum = stored_artifact
        plan, ml = build_plan(registry, lags=(1, 2))
        sb = _Supabase(
            {
                "model_versions": [
                    model_row(
                        uri, checksum, node_id=ml, feature_names=("lag_1", "lag_2")
                    )
                ]
            }
        )

        warnings = await db.assert_models_verified(
            sb, _user(), _version_row(plan), plan, registry=registry
        )
        assert warnings == []

    async def test_a_checksum_mismatch_holds_the_deployment_out_of_running(
        self, registry, store, stored_artifact
    ):
        """17.6's second enforcer. A refusal here never reaches ``DEPLOYING``."""
        uri, checksum = stored_artifact
        store.path_of("u/s/v/node/model.joblib").write_bytes(b"someone else's bytes")

        plan, ml = build_plan(registry)
        sb = _Supabase({"model_versions": [model_row(uri, checksum, node_id=ml)]})

        with pytest.raises(db.DeployRejected) as exc:
            await db.assert_models_verified(
                sb, _user(), _version_row(plan), plan, registry=registry
            )

        rejected = exc.value
        assert rejected.code == "MODEL_AWAITING_ARTIFACT"
        assert rejected.http_status == 409
        assert rejected.details["runtime_state"] == mr.AWAITING_MODEL
        assert rejected.details["verdict"] == mr.CODE_ARTIFACT_CHECKSUM_MISMATCH
        assert rejected.details["node_id"] == ml

    async def test_drift_is_refused_and_names_expected_versus_actual_columns(
        self, registry, stored_artifact
    ):
        """Requirement 17.7's report is the point of the refusal."""
        uri, checksum = stored_artifact
        plan, ml = build_plan(registry, lags=(1, 2))
        sb = _Supabase(
            {
                "model_versions": [
                    model_row(
                        uri, checksum, node_id=ml, feature_names=("lag_1", "lag_9")
                    )
                ]
            }
        )

        with pytest.raises(db.DeployRejected) as exc:
            await db.assert_models_verified(
                sb, _user(), _version_row(plan), plan, registry=registry
            )

        rejected = exc.value
        assert rejected.code == "FEATURE_SCHEMA_DRIFT"
        assert rejected.http_status == 409
        assert rejected.details["expected_feature_columns"] == ["lag_1", "lag_9"]
        assert rejected.details["actual_feature_columns"] == ["lag_1", "lag_2"]
        assert rejected.details["missing_feature_columns"] == ["lag_9"]
        assert rejected.details["extra_feature_columns"] == ["lag_2"]
        assert rejected.to_detail()["error"] == "FEATURE_SCHEMA_DRIFT"

    async def test_an_order_only_difference_is_admitted_and_reported(
        self, registry, stored_artifact
    ):
        uri, checksum = stored_artifact
        plan, ml = build_plan(registry, lags=(1,), time_parts=("hour",))
        sb = _Supabase(
            {
                "model_versions": [
                    model_row(
                        uri, checksum, node_id=ml, feature_names=("time_hour", "lag_1")
                    )
                ]
            }
        )

        warnings = await db.assert_models_verified(
            sb, _user(), _version_row(plan), plan, registry=registry
        )

        assert [w["code"] for w in warnings] == [mr.WARNING_FEATURE_ORDER_DIFFERS]
        assert warnings[0]["expected_feature_columns"] == ["time_hour", "lag_1"]
        assert warnings[0]["actual_feature_columns"] == ["lag_1", "time_hour"]

    async def test_an_unbound_model_node_is_a_409_naming_the_node(self, registry):
        plan, ml = build_plan(registry)
        sb = _Supabase({"model_versions": []})

        with pytest.raises(db.DeployRejected) as exc:
            await db.assert_models_verified(
                sb, _user(), _version_row(plan), plan, registry=registry
            )

        assert exc.value.code == "MODEL_NODE_NOT_BOUND"
        assert exc.value.http_status == 409
        assert exc.value.details["unbound_nodes"] == [ml]

    async def test_an_inactive_model_version_does_not_count_as_bound(
        self, registry, stored_artifact
    ):
        uri, checksum = stored_artifact
        plan, ml = build_plan(registry)
        sb = _Supabase(
            {"model_versions": [model_row(uri, checksum, node_id=ml, is_active=False)]}
        )

        with pytest.raises(db.DeployRejected) as exc:
            await db.assert_models_verified(
                sb, _user(), _version_row(plan), plan, registry=registry
            )

        assert exc.value.code == "MODEL_NODE_NOT_BOUND"

    async def test_another_tenants_model_version_is_never_consulted(
        self, registry, stored_artifact
    ):
        """Ownership is re-checked on the row, not left to RLS alone.

        The refusal is "not bound" rather than anything that would confirm someone else's
        model version exists.
        """
        uri, checksum = stored_artifact
        plan, ml = build_plan(registry)
        sb = _Supabase(
            {
                "model_versions": [
                    model_row(uri, checksum, node_id=ml, user_id=OTHER_USER_ID)
                ]
            }
        )

        with pytest.raises(db.DeployRejected) as exc:
            await db.assert_models_verified(
                sb, _user(), _version_row(plan), plan, registry=registry
            )

        assert exc.value.code == "MODEL_NODE_NOT_BOUND"

    async def test_a_graph_with_no_model_node_reads_no_model_table(self, registry):
        """A pure-indicator deploy must not acquire a dependency on 004d."""
        data = _node(
            registry,
            "ohlcv_feed",
            symbol="BTC/USDT",
            timeframe="15m",
            market_type="spot",
            mode="streaming",
        )
        ema = _node(registry, "ema", window=20, source="close")
        const = _node(registry, "constant", value=0.5)
        gate = _node(registry, "gt")
        action = _node(
            registry,
            "action_buy_market",
            quantity_type="percent_of_equity",
            quantity=0.25,
        )
        plan = compile_graph(
            StrategyGraph(
                nodes=[data, ema, const, gate, action],
                edges=[
                    EdgeSpec.create(data.id, "close", ema.id, "series"),
                    EdgeSpec.create(ema.id, "value", gate.id, "left"),
                    EdgeSpec.create(const.id, "value", gate.id, "right"),
                    EdgeSpec.create(gate.id, "out", action.id, "signal"),
                ],
            ),
            registry,
        )
        sb = _Supabase({}, absent_tables=("model_versions",))

        assert (
            await db.assert_models_verified(
                sb, _user(), _version_row(plan), plan, registry=registry
            )
            == []
        )
        assert sb.selects == []

    async def test_004d_unapplied_is_a_503_naming_the_file_and_never_a_500(
        self, registry, caplog
    ):
        """Degraded, classified and named - not an exception escaping as a 500.

        "Could not look" is not "it verified", so the deployment is refused; but the
        refusal names the migration an operator has to apply, and the degradation is
        logged with that same file name.
        """
        plan, _ml = build_plan(registry)
        sb = _Supabase({}, absent_tables=("model_versions",))

        with caplog.at_level("WARNING"):
            with pytest.raises(db.DeployRejected) as exc:
                await db.assert_models_verified(
                    sb, _user(), _version_row(plan), plan, registry=registry
                )

        rejected = exc.value
        assert rejected.code == "MODEL_VERSIONS_UNAVAILABLE"
        assert rejected.http_status == 503
        assert rejected.details["migration"].endswith("004d_training_and_models.sql")
        assert "004d_training_and_models.sql" in rejected.message
        assert "004d_training_and_models.sql" in caplog.text
        assert (REPO_ROOT / rejected.details["migration"]).is_file()

    async def test_an_unreadable_plan_is_refused_before_any_model_is_read(
        self, registry
    ):
        """The market gate and the model gates read the one plan, parsed once."""
        plan, _ml = build_plan(registry)
        row = _version_row(plan, compiled_plan={"execution_order": ["nope"]})

        with pytest.raises(db.DeployRejected) as exc:
            db.load_binding_plan(row)
        assert exc.value.code == "PLAN_UNREADABLE"
        assert exc.value.http_status == 409


@pytest.mark.asyncio
class TestTheModelGateRunsBeforeTheOwnershipReads:
    """A version that cannot run on any account is refused before one is resolved.

    Ordering is the assertion, and it needs both directions: the model gate must refuse
    first when the artifact is wrong, and the account gate must still refuse when it is
    right. One without the other would pass with the model gate placed anywhere.
    """

    @staticmethod
    def _request():
        return db.BindingRequest.from_payload(
            {
                "exchange_account_id": "77777777-7777-4777-8777-777777777777",
                "mode": "paper",
            }
        )

    async def test_a_bad_checksum_is_refused_before_the_account_is_looked_up(
        self, registry, store, stored_artifact
    ):
        uri, checksum = stored_artifact
        store.path_of("u/s/v/node/model.joblib").write_bytes(b"tampered")
        plan, ml = build_plan(registry)
        sb = _Supabase({"model_versions": [model_row(uri, checksum, node_id=ml)]})

        with pytest.raises(db.DeployRejected) as exc:
            await db.evaluate_binding(
                sb,
                _user(),
                _version_row(plan),
                STRATEGY_ID,
                "v3.0",
                self._request(),
            )

        assert exc.value.code == "MODEL_AWAITING_ARTIFACT"
        assert "exchange_keys" not in sb.selects
        assert "exchange_connections" not in sb.selects

    async def test_a_good_model_lets_the_account_gate_have_its_say(
        self, registry, stored_artifact
    ):
        uri, checksum = stored_artifact
        plan, ml = build_plan(registry, lags=(1,))
        sb = _Supabase(
            {
                "model_versions": [
                    model_row(uri, checksum, node_id=ml, feature_names=("lag_1",))
                ],
                "exchange_keys": [],
                "exchange_connections": [],
            }
        )

        with pytest.raises(db.DeployRejected) as exc:
            await db.evaluate_binding(
                sb,
                _user(),
                _version_row(plan),
                STRATEGY_ID,
                "v3.0",
                self._request(),
            )

        assert exc.value.code == "EXCHANGE_ACCOUNT_NOT_FOUND"
        assert "model_versions" in sb.selects
