"""
tests/test_ml_executor_does_not_fabricate_predictions.py

An ML node that cannot produce a prediction publishes ``0.5`` for every bar, and a
downstream comparator reads that as a positive signal.

P1-04. Filed as "trained models are never used for inference", which is true but is the
milder half. Measured against the real ``MLExecutor``, the behaviour depends on
``VYOMQUANT_MODE`` and only one of the three outcomes is safe:

    VYOMQUANT_MODE=safe (the DEFAULT when the variable is unset -- see
    ``safety_config.get_vyomquant_mode``, whose default argument is ``"safe"``)
        ``SafetyMonitor.check_execution_allowed("ml_inference")`` returns
        ``"[BLOCKED] EXECUTION BLOCKED: VYOMQUANT_MODE=safe (operation: ml_inference)"``
        and ``MLExecutor.execute`` returns ``pd.Series(0.5, index=market_data.index)``.
        Measured: identical output with and without a model in the registry. FAILS OPEN.

    VYOMQUANT_MODE=paper or live, registry empty (which it always is)
        raises ``RuntimeError("MODEL NOT FOUND: ...")``. Fails closed. Correct.

    monitor permitting AND a model present
        raises ``AttributeError: 'MLExecutor' object has no attribute
        '_get_feature_schema'``. That method is called at one line and defined nowhere in
        the repository.

WHY ``0.5`` IS NOT NEUTRAL. It is published as the node's output series and flows into
whatever comparator the strategy wired downstream. Measured on the real executor:
``(output >= 0.5)`` is true on **every bar**. So a strategy whose ML node feeds a
``>= 0.5`` threshold gets a node that votes yes, always, forever, with no model loaded and
nothing in any log to say the prediction was manufactured. The comment on that line calls
it "Return neutral signal instead of executing"; there is no neutral value for a
comparison whose threshold the node does not know.

THE REGISTRY IS NEVER POPULATED, AND POPULATING IT WOULD NOT HELP.
``MLExecutor.__init__`` takes ``model_registry: Optional[Dict] = None`` and defaults to
``{}``; all three instantiations in the repository -- ``dag_engine.py`` and
``dag_engine_parallel.py`` twice -- pass nothing. The obvious one-line "fix" of feeding it
a model is measurably not a fix: the next statement calls the missing
``_get_feature_schema``, and past that the method contains

    if None.isna().any().any():          # AttributeError on NoneType
        ...
    return None                          # not `result`

followed by unreachable observability code and a second, unreachable ``return result``.
``MLExecutor`` cannot return a model-derived prediction by any route.

WHAT THIS FILE REQUIRES, AND WHAT IT DELIBERATELY DOES NOT.
It requires that a node which cannot predict REFUSES rather than publishing a number. It
does not require ML inference to work. Wiring the registry, supplying a feature schema and
repairing the tail would turn a path that has never produced a real prediction into one
that drives trades, which is a feature with an owner, not a defect fix. The governed plan
path already does this correctly and is untouched here: ``execute_plan`` gates on
``state.model_versions`` through ``model_readiness.model_ready`` and marks the node
``AWAITING_MODEL``, holding it dormant instead of inventing a value.

CONSEQUENCE FOR BACKTESTS, STATED PLAINLY. ``backtest_runtime`` reaches this executor via
``self.dag_engine.execute(...)``. A strategy with an ML node currently "backtests" with
that node pinned at 0.5 and produces numbers; after this change it is refused. That is the
same judgement already applied to the fabricated no-VectorBT fallback
(``BacktestEngineUnavailable``): a simulation that silently replaces a model with a
constant is not a backtest of the submitted strategy, and reporting nothing is better than
reporting that.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import dag_engine as de
from backend_app.backend.dag_engine import DAGEngine, MLExecutor

BARS = 60
INDEX = pd.date_range("2024-01-01", periods=BARS, freq="h", tz="UTC")
MARKET = pd.DataFrame(
    {
        "open": np.linspace(100, 160, BARS),
        "high": np.linspace(101, 161, BARS),
        "low": np.linspace(99, 159, BARS),
        "close": np.linspace(100, 160, BARS),
        "volume": np.full(BARS, 10.0),
    },
    index=INDEX,
)
FEATURE = pd.Series(np.linspace(0.1, 0.9, BARS), index=INDEX)
NODE = {"id": "ml1", "type": "ml", "model_id": "m1"}


class _Model:
    """A model that would predict cleanly if it were ever reached."""

    n_features_in_ = 1

    def predict(self, frame):
        return np.full(len(frame), 0.9)


@pytest.fixture
def monitor_permits(monkeypatch):
    """Make the safety monitor allow ml_inference, as paper/live mode does."""
    monkeypatch.setattr(
        de.SafetyMonitor, "check_execution_allowed", staticmethod(lambda _op: None)
    )


@pytest.fixture
def monitor_blocks(monkeypatch):
    """Make the safety monitor block ml_inference, as VYOMQUANT_MODE=safe does."""
    monkeypatch.setattr(
        de.SafetyMonitor,
        "check_execution_allowed",
        staticmethod(lambda op: f"[BLOCKED] EXECUTION BLOCKED: test (operation: {op})"),
    )


# ---------------------------------------------------------------------------
# 1. The refusal has a name
# ---------------------------------------------------------------------------


class TestTheRefusalIsNamed:
    def test_there_is_a_dedicated_exception(self):
        """So "ML cannot run here" is distinguishable from "the model blew up"."""
        assert hasattr(de, "MLInferenceUnavailable"), (
            "dag_engine declares no MLInferenceUnavailable, so a node that cannot "
            "predict has no way to say so distinctly"
        )

    def test_it_is_a_runtime_error_so_existing_handlers_still_catch_it(self):
        """``MLExecutor`` already raised ``RuntimeError``; callers catch that.

        Subclassing keeps every existing ``except RuntimeError`` working, so this
        narrows the blast radius to the thing that changed - the fail-open branch.
        """
        assert issubclass(de.MLInferenceUnavailable, RuntimeError)


# ---------------------------------------------------------------------------
# 2. The fail-open branch: a blocked monitor must not publish a number
# ---------------------------------------------------------------------------


class TestABlockedMonitorRefusesInsteadOfPublishingAConstant:
    def test_a_blocked_monitor_refuses(self, monitor_blocks):
        """This is the defect. Today it returns 0.5 for every bar."""
        with pytest.raises(de.MLInferenceUnavailable):
            MLExecutor().execute(NODE, {"f1": FEATURE}, MARKET)

    def test_a_blocked_monitor_refuses_even_with_a_model_present(self, monitor_blocks):
        with pytest.raises(de.MLInferenceUnavailable):
            MLExecutor(model_registry={"m1": _Model()}).execute(
                NODE, {"f1": FEATURE}, MARKET
            )

    def test_the_refusal_names_the_block_reason(self, monitor_blocks):
        """A refusal a reader cannot act on is only marginally better than a 0.5."""
        with pytest.raises(de.MLInferenceUnavailable) as caught:
            MLExecutor().execute(NODE, {"f1": FEATURE}, MARKET)
        assert "BLOCKED" in str(caught.value)

    def test_no_branch_returns_a_constant_half(self, monitor_blocks):
        """Whatever happens, the node must not hand a tradeable number downstream."""
        try:
            result = MLExecutor().execute(NODE, {"f1": FEATURE}, MARKET)
        except de.MLInferenceUnavailable:
            return
        pytest.fail(
            f"the node published a value instead of refusing: "
            f"{sorted(set(np.asarray(result).tolist()))}"
        )


class TestMissingFeaturesRefuseToo:
    def test_no_inputs_at_all_is_refused(self, monitor_permits):
        """Refused by the input contract, which already ran first and is correct.

        ``validate_node_inputs`` rejects an empty ``inputs`` dict with
        ``DAGExecutionError("Empty input. Node has no connected upstream nodes providing
        data.")`` before the feature collection is reached. That is a refusal, which is
        all this test requires - it is asserted here so the ordering is recorded rather
        than rediscovered, and so that a future change which moves the feature check
        earlier still has to refuse.
        """
        with pytest.raises((de.MLInferenceUnavailable, de.DAGExecutionError)):
            MLExecutor(model_registry={"m1": _Model()}).execute(NODE, {}, MARKET)

    def test_non_series_inputs_count_as_no_features(self, monitor_permits):
        """The branch that mattered: inputs ARRIVE, but none of them is a feature series.

        The executor collects only ``pd.Series`` values, so this passes the input
        contract and then finds ``features == []``. That was the second
        ``return pd.Series(0.5, ...)``.
        """
        with pytest.raises(de.MLInferenceUnavailable):
            MLExecutor(model_registry={"m1": _Model()}).execute(
                NODE, {"f1": 1.0, "f2": "x"}, MARKET
            )


# ---------------------------------------------------------------------------
# 3. The missing model still refuses, and says something true
# ---------------------------------------------------------------------------


class TestAMissingModelRefusesWithAnAccurateReason:
    def test_an_empty_registry_refuses(self, monitor_permits):
        with pytest.raises(de.MLInferenceUnavailable):
            MLExecutor().execute(NODE, {"f1": FEATURE}, MARKET)

    def test_the_registry_is_empty_on_every_engine_this_repo_builds(self):
        """The premise, asserted rather than described.

        If something ever populates it, this fails and the whole file needs revisiting.
        """
        assert DAGEngine().executors["ml"].model_registry == {}

    def test_the_refusal_says_the_registry_is_never_populated(self, monitor_permits):
        """The old message advised checking that "Model registry contains the model".

        Nothing can put a model there -- all three instantiations pass no registry -- so
        that was advice the reader could not act on. The refusal must not send someone
        looking for a configuration step that does not exist.
        """
        with pytest.raises(de.MLInferenceUnavailable) as caught:
            MLExecutor().execute(NODE, {"f1": FEATURE}, MARKET)
        message = str(caught.value).lower()
        assert "model_versions" in message or "execute_plan" in message or "plan" in message, (
            "the refusal does not point at the governed path that does work"
        )

    def test_a_model_being_present_does_not_produce_a_prediction_either(
        self, monitor_permits
    ):
        """Because the tail is broken three separate ways; see the module docstring.

        Asserted so that nobody "fixes" P1-04 by wiring the registry and concludes from
        a green test that inference works.
        """
        with pytest.raises(de.MLInferenceUnavailable):
            MLExecutor(model_registry={"m1": _Model()}).execute(
                NODE, {"f1": FEATURE}, MARKET
            )


# ---------------------------------------------------------------------------
# 4. The broken tail is gone rather than left as a trap
# ---------------------------------------------------------------------------


def _ml_executor_ast():
    """``MLExecutor``'s class node, for assertions about EXECUTABLE code only.

    The class docstring and the comments beside the refusal describe the removed code by
    name -- that is the point of them -- so a text search for ``None.isna()`` or
    ``_get_feature_schema`` matches the explanation as readily as the defect. Parsing
    discards comments entirely and makes docstrings identifiable, so what is left is only
    what runs.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(MLExecutor)))
    return tree.body[0]


class TestTheDeadCodeIsRemoved:
    def test_no_attribute_is_read_off_a_literal_none(self):
        """``if None.isna().any().any():`` is an ``AttributeError``, always.

        Asserted structurally rather than by matching that text: any attribute access on
        a literal ``None`` is the same bug, however it is spelled.
        """
        import ast

        offenders = [
            node
            for node in ast.walk(_ml_executor_ast())
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Constant)
            and node.value.value is None
        ]

        assert not offenders, (
            f"MLExecutor reads an attribute off a literal None at line(s) "
            f"{[n.lineno for n in offenders]}; that raises AttributeError on every "
            f"execution that reaches it"
        )

    def test_the_undefined_feature_schema_helper_is_not_called(self):
        import ast

        calls = [
            node
            for node in ast.walk(_ml_executor_ast())
            if isinstance(node, ast.Attribute) and node.attr == "_get_feature_schema"
        ]
        if calls:
            assert hasattr(MLExecutor, "_get_feature_schema"), (
                f"MLExecutor references self._get_feature_schema at line(s) "
                f"{[n.lineno for n in calls]}, and it is defined nowhere in the "
                f"repository"
            )

    def test_execute_publishes_no_constant(self):
        """No ``0.5`` literal may remain anywhere in ``execute``.

        The two fail-open branches both returned ``pd.Series(0.5, ...)``. This is the
        structural form of the same requirement the behavioural tests above make, so a
        third branch cannot quietly reintroduce it.
        """
        import ast

        function = next(
            node
            for node in _ml_executor_ast().body
            if isinstance(node, ast.FunctionDef) and node.name == "execute"
        )
        constants = [
            node.lineno
            for node in ast.walk(function)
            if isinstance(node, ast.Constant) and node.value == 0.5
        ]

        assert not constants, (
            f"MLExecutor.execute still contains a 0.5 literal at line(s) {constants}; a "
            f"node that cannot predict must not publish a number a comparator can act on"
        )

    def test_no_statement_follows_an_unconditional_return_in_execute(self):
        """The old tail had observability code and a second ``return result`` after a
        ``return None`` inside a ``try``, so neither could ever run.
        """
        import ast
        import inspect
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(MLExecutor.execute)))
        function = tree.body[0]

        def unreachable(body):
            for index, statement in enumerate(body):
                if isinstance(statement, (ast.Return, ast.Raise)) and index < len(body) - 1:
                    return body[index + 1]
                for field in ("body", "orelse", "finalbody"):
                    nested = getattr(statement, field, None)
                    if isinstance(nested, list):
                        found = unreachable(nested)
                        if found is not None:
                            return found
            return None

        dead = unreachable(function.body)
        assert dead is None, (
            f"MLExecutor.execute has a statement at line {dead.lineno} that follows an "
            f"unconditional return or raise in the same block, so it cannot run"
        )
