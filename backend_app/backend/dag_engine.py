"""
backend/dag_engine.py — DAG Execution Engine for Strategy Builder.

Provides full DAG-based backtesting with:
  - Topological ordering
  - Indicator + ML + Logic node execution
  - Signal propagation through edges
  - Action node evaluation
  - Result mapping back to DAG structure

DAG Architecture:
  INPUT nodes → INDICATOR nodes → LOGIC nodes → ACTION nodes
                    ↓
                 ML nodes (confidence-based)
"""

import hashlib as _hashlib
import json as _json
import logging
import os as _os
import threading as _threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import (Any, Dict, FrozenSet, List, Mapping, Optional, Sequence,
                    Set, Tuple)

import numpy as np
import pandas as pd

# 🚨 Data observability - real-time quality monitoring
from backend_app.backend.data_observability import (dashboard,
                                                    observe_features,
                                                    observe_indicators,
                                                    observe_market_data,
                                                    observe_ml_predictions)
# 🚨 Feature validation - ALL ML features must pass validation
from backend_app.backend.feature_validator import (FeatureValidationError,
                                                   FeatureValidator,
                                                   ModelMismatchError)
# 🚨 Task 8.6's readiness seam. The two runtime labels a model node can hold and the
# pure, exception-free predicate behind them are *imported*, never re-spelled: Requirement
# 17.6 has two enforcers (this runtime and the Deployment_Service) and they must not
# disagree about what a checksum failure means. Nothing about checksums or feature schemas
# is re-derived here. ``model_readiness`` deliberately has no module-level backend imports,
# so this costs nothing at import time.
from backend_app.backend.model_readiness import AWAITING_MODEL
from backend_app.backend.model_readiness import NODE_READY as READY
from backend_app.backend.model_readiness import model_ready
# 🚨 Pipeline guard - FAIL-FAST at every stage
from backend_app.core.pipeline_guard import (PipelineError, guard_features,
                                             guard_indicators,
                                             guard_market_data,
                                             guard_ml_prediction)
# 🚨 The platform's existing "execution did not happen" exception. The intent firewall
# below (``assert_execution_safe``) raises a subclass of it rather than a second,
# unrelated exception of the same name: one name in the codebase means "blocked before an
# order", and anything already written to treat it as such treats a blocked intent the
# same way. Importing it costs nothing - the module's Redis client is lazy.
from backend_app.core.global_safety import \
    ExecutionBlocked as _PlatformExecutionBlocked
# 🚨 SYSTEM FREEZE: Safety config must be imported first
from backend_app.core.safety_config import SafetyMonitor

logger = logging.getLogger("DAGEngine")


def _metrics() -> Any:
    """``backend/metrics.py``'s collector, or ``None``. Lazy and guarded (task 9.1).

    Requirement 24.3. The guard is load-bearing on this path in a way it is not elsewhere:
    the calls below sit inside ``execute_plan`` and inside the intent firewall, so a metrics
    failure that propagated would either abort an evaluation or - worse - change what
    happens after an intent was refused. It cannot. See ``metrics.never_fails``.
    """
    try:
        from backend_app.backend.metrics import metrics_collector

        return metrics_collector
    except Exception:  # noqa: BLE001 - instrumentation never breaks its caller
        return None


def _builder_alerts() -> Any:
    """``backend/builder_alerts.py``, or ``None``. Lazy and guarded (task 9.2).

    Requirement 24.4. Separate from :func:`_metrics` because it is a separate failure: the
    metrics module is stdlib-only, while the alert module reaches the platform's dispatcher
    and therefore ``aiohttp`` and Redis. Neither of those is permitted to be on
    ``assert_execution_safe``'s import path, and neither is permitted to be what stops an
    intent from being refused - so the import is deferred to the refusal itself and its
    failure is a ``None``.
    """
    try:
        from backend_app.backend import builder_alerts

        return builder_alerts
    except Exception:  # noqa: BLE001 - an alert never breaks the act it observes
        return None


def _category_label(descriptor: Any) -> str:
    """One descriptor's ``Block_Category``, as a metric label (Requirement 24.3).

    Read from the descriptor the registry published, so the label vocabulary is the seven
    categories and nothing else. ``"unknown"`` for a descriptor that carries none, rather
    than a blank label that would silently merge into the unlabelled series.
    """
    category = getattr(descriptor, "category", None)
    label = getattr(category, "value", category)
    return "unknown" if label in (None, "") else str(label)


class IndicatorComputationError(Exception):
    """
    Raised when indicator computation fails.
    
    This is a HARD FAILURE - no silent NaN propagation allowed.
    The caller must fix the data issue before retrying.
    """
    pass


class DAGExecutionError(Exception):
    """
    Raised when DAG node execution fails due to invalid inputs.
    
    This is a HARD FAILURE - no silent node execution allowed.
    Full compatibility with PipelineGuard.
    """
    pass


def validate_node_inputs(inputs: Dict[str, Any], node_id: str) -> None:
    """
    Strict input contract validation for DAG nodes.
    
    Validates:
    - Inputs is not None
    - Inputs dict is not empty
    - All Series inputs have no NaN values
    - All required inputs are present
    
    Args:
        inputs: Input dictionary from upstream nodes
        node_id: Node identifier for error messages
    
    Raises:
        DAGExecutionError: If any validation fails
    """
    # Check None
    if inputs is None:
        raise DAGExecutionError(
            f"Node '{node_id}': Input is None. "
            f"Node received no input data from upstream nodes."
        )
    
    # Check empty
    if len(inputs) == 0:
        raise DAGExecutionError(
            f"Node '{node_id}': Empty input. "
            f"Node has no connected upstream nodes providing data."
        )
    
    # Validate each input value
    for input_id, value in inputs.items():
        # Check for Series with NaN
        if isinstance(value, pd.Series):
            if value.isna().any():
                # Fill indicator warm-up NaNs gracefully
                inputs[input_id] = value.bfill().fillna(0)
        
        # Check for None values in inputs
        elif value is None:
            raise DAGExecutionError(
                f"Node '{node_id}': None value in input '{input_id}'. "
                f"Upstream node produced no output."
            )
        
        # Check for empty DataFrame
        elif isinstance(value, pd.DataFrame) and value.empty:
            raise DAGExecutionError(
                f"Node '{node_id}': Empty DataFrame in input '{input_id}'. "
                f"Upstream node produced empty data."
            )


def validate_node_output(
    output: Any,
    node_id: str,
    expected_length: int,
    expected_index: pd.Index,
    allow_nan: bool = False,
) -> None:
    """
    Strict output contract validation for DAG nodes.
    
    Validates:
    - Output is not None
    - Output is Series or DataFrame
    - Output length matches expected
    - Output contains no NaN values
    - Output index is time-aligned
    
    Args:
        output: Node output to validate
        node_id: Node identifier for error messages
        expected_length: Expected length (from market_data)
        expected_index: Expected index (from market_data)
        allow_nan: Permit NaN in the output. Defaults to ``False``, which is the
            behaviour every pre-existing caller relies on. Set only by
            :func:`validate_port_output` for a port whose payload defines an
            undefined bar as NaN: a MATH block writes NaN for division by a
            zero denominator (``design.md`` -> Math blocks) and an indicator's
            warmup region is NaN by contract. Rejecting those at the port
            boundary would force the runtime to invent a number for a bar where
            it has none, which is the opposite of the safety this rule exists
            for. ``±Inf`` is never permitted by either caller.
    
    Raises:
        DAGExecutionError: If any validation fails
    """
    # Rule 1: Output must not be None
    if output is None:
        raise DAGExecutionError(
            f"Node '{node_id}' returned None. "
            f"All nodes must return Series or DataFrame output."
        )
    
    # Rule 2: Output must be Series or DataFrame
    if not isinstance(output, (pd.Series, pd.DataFrame)):
        raise DAGExecutionError(
            f"Node '{node_id}' returned invalid type: {type(output).__name__}. "
            f"Expected pd.Series or pd.DataFrame."
        )
    
    # Rule 3: Output length must match expected
    if len(output) != expected_length:
        raise DAGExecutionError(
            f"Node '{node_id}' output length mismatch: "
            f"expected {expected_length}, got {len(output)}. "
            f"All outputs must match market_data length."
        )
    
    # Rule 4: Output must contain no NaN
    if allow_nan:
        pass
    elif isinstance(output, pd.Series):
        if output.isna().any():
            nan_count = output.isna().sum()
            raise DAGExecutionError(
                f"Node '{node_id}' output contains NaN: {nan_count} values. "
                f"Nodes must produce clean output without missing data."
            )
    else:  # DataFrame
        if output.isna().any().any():
            nan_count = output.isna().sum().sum()
            raise DAGExecutionError(
                f"Node '{node_id}' DataFrame output contains NaN: {nan_count} values. "
                f"Nodes must produce clean output without missing data."
            )
    
    # Rule 5: Output index must be time-aligned
    if isinstance(output.index, pd.DatetimeIndex):
        if not output.index.equals(expected_index):
            raise DAGExecutionError(
                f"Node '{node_id}' output index mismatch. "
                f"Output timestamps must align with market_data index."
            )


# ═══════════════════════════════════════════════════════════════════════════
#  PORT ADDRESSING (task 5.2)
#
#  A node's inputs are addressed by the name of the INPUT PORT the edge landed
#  on, and its outputs by the name of the OUTPUT PORT that produced them.
#
#  Why this is not a refactor for its own sake: reading a multi-input node's
#  operands out of the edge list *positionally* is the same defect class as
#  stacking two feature matrices by row position. Both produce a plausible
#  answer from the wrong data and neither raises. Positionally, an author's
#  ``close -> feat_volume.volume`` edge delivers close as volume, ``macd``'s
#  three outputs collapse onto whichever one the executor happened to return,
#  and swapping two edges in the payload swaps the sides of a comparison.
#
#  The canonical wiring already carries the answer: ``CompiledPlan.inbound`` is
#  keyed ``node_id -> target_port -> every edge on that port``, and
#  ``plan_to_engine_graph`` emits exactly those as ``source_port`` /
#  ``target_port`` on each engine edge. So the port names this module reads are the
#  plan's own, not a second vocabulary - and a variadic port arrives with all of
#  its operands, which is what :meth:`PortInputs.port_values` exists to read.
#
#  Reuse points, used by every executor from task 5.2 onwards (FeatureExecutor,
#  MathExecutor, LogicExecutor and the re-pointed IndicatorExecutor):
#
#    * :func:`resolve_node_inputs` - the ONE input resolver. Do not walk the
#      edge list again.
#    * :class:`PortInputs`         - what an executor receives. ``inputs.port("left")``
#      is port-addressed; plain ``dict`` access by upstream node id still works,
#      which is why every pre-existing executor keeps running unchanged.
#    * :class:`NodeOutputs`        - what an executor returns when it has more
#      than one output port, or when its legacy single return value is not the
#      first declared port.
#    * :func:`validate_port_output` - the ONE output check. It delegates to
#      :func:`validate_node_output` above; do not add a second output validator.
#    * :meth:`BlockKernelExecutor._bind_operands` (task 5.3) - the ONE port-addressed
#      operand binder for a descriptor-backed node. Walks ``descriptor.inputs`` in
#      declaration order, expands a variadic port to all N of its connections, names an
#      unfed required port and refuses an undeclared one. ``LogicExecutor`` inherits it
#      unchanged, and the re-pointed ``IndicatorExecutor`` follows the same rule over
#      ``IndicatorSpec.inputs``.
#    * :class:`NodeIssueLog` (task 5.3) - where a numeric condition is recorded against
#      the node that produced it (Requirement 20.4). One log per engine, shared with the
#      executors; cleared per run, never replaced.
# ═══════════════════════════════════════════════════════════════════════════


#: A binding that reads the id-keyed view - ``self[source_id]`` - rather than carrying a
#: value of its own. Every binding is this one whenever the id-keyed view can represent
#: the port's value, which keeps the "two views of one value, never two copies" property
#: the in-place NaN back-fill in :func:`validate_node_inputs` depends on. A binding
#: carries its own value only where the id-keyed view *cannot* represent it: see
#: :func:`resolve_node_inputs`.
_FROM_SOURCE = object()


class PortInputs(dict):
    """One node's resolved inputs, addressable by upstream node id and by input port.

    Subclasses ``dict`` keyed by **upstream node id**, which is precisely what
    ``IndicatorExecutor``, ``MLExecutor``, ``LogicExecutor``, ``ActionExecutor``,
    ``MarketDataExecutor`` and ``BlockKernelExecutor`` already read - they see no
    change at all, including ``validate_node_inputs``' in-place NaN back-fill.

    Port addressing is layered on top as ``port_sources``: ``input_port -> tuple of
    upstream node ids``. A port's value is resolved through ``self`` on every lookup
    rather than copied, so a mutation an executor makes to ``inputs[source_id]`` is
    visible through :meth:`port` as well - two views of one value, never two copies that
    can drift.

    One case defeats that, and it is the reason bindings exist (task 5.3). The id-keyed
    view holds **one entry per upstream node**, so when a single upstream node feeds two
    input ports of this node from two *different output ports* - ``ohlcv_feed.high ->
    subtract.a`` with ``ohlcv_feed.low -> subtract.b``, the ordinary way to author a bar
    range - both entries collapse onto whichever edge was walked last. Resolving the
    ports through that entry would hand ``a`` and ``b`` the same series, silently
    computing ``low - low`` for an author who asked for ``high - low``. So a binding that
    the id-keyed view cannot represent carries its own value, and every other binding
    stays a view. :func:`resolve_node_inputs` decides which is which by object identity,
    not by guesswork.

    A port holds a *tuple* of bindings because a variadic port legitimately holds
    2..N connections (``feat_concat``, ``and``, ``add``). :meth:`port` refuses to
    pick one of several rather than silently taking the first.
    """

    #: ``input_port name -> (upstream node id, bound value or _FROM_SOURCE)``, in edge
    #: order. ``port_sources`` is the public projection of this.
    _bindings: Dict[str, Tuple[Tuple[str, Any], ...]]

    def __init__(
        self,
        values_by_source: Optional[Mapping[str, Any]] = None,
        port_sources: Optional[Mapping[str, Sequence[str]]] = None,
        port_bindings: Optional[Mapping[str, Sequence[Tuple[str, Any]]]] = None,
    ) -> None:
        super().__init__(values_by_source or {})
        if port_bindings is not None:
            self._bindings = {
                str(port): tuple((str(source), value) for source, value in bindings)
                for port, bindings in port_bindings.items()
            }
        else:
            # The two-argument form every caller outside this module uses: a port is a
            # list of source ids and every binding is a view of the id-keyed value.
            self._bindings = {
                str(port): tuple((str(source), _FROM_SOURCE) for source in sources)
                for port, sources in (port_sources or {}).items()
            }

    # -- port addressing --------------------------------------------------

    @property
    def port_sources(self) -> Dict[str, Tuple[str, ...]]:
        """``input_port name -> upstream node ids feeding it, in edge order``."""
        return {
            port: tuple(source for source, _value in bindings)
            for port, bindings in self._bindings.items()
        }

    def ports(self) -> Tuple[str, ...]:
        """Input port names that actually hold a connection."""
        return tuple(self._bindings)

    def has_port(self, port_name: str) -> bool:
        return bool(self.port_values(port_name))

    def port_values(self, port_name: str) -> Tuple[Any, ...]:
        """Every value landing on ``port_name``, in edge order.

        Sources whose upstream node produced nothing are omitted rather than
        represented by a placeholder: a caller that needs "the port is unfed" asks
        :meth:`has_port`, and a caller that needs "the port is fed N times" reads
        the length. Neither has to distinguish a real ``None`` from a gap.
        """
        values: List[Any] = []
        for source, bound in self._bindings.get(port_name, ()):
            if bound is _FROM_SOURCE:
                if source in self:
                    values.append(self[source])
            else:
                values.append(bound)
        return tuple(values)

    def port(self, port_name: str, default: Any = None) -> Any:
        """The single value on ``port_name``, or ``default`` when it is unfed.

        Raises
            :class:`DAGExecutionError` when the port holds more than one
            connection. A multiply-fed port is a variadic port, and reducing it to
            one value here would drop the author's other inputs; the caller must
            ask for :meth:`port_values` and say what it does with them.
        """
        values = self.port_values(port_name)
        if not values:
            return default
        if len(values) > 1:
            raise DAGExecutionError(
                f"Input port '{port_name}' holds {len(values)} connections; read it "
                f"with port_values() and combine them explicitly rather than "
                f"silently using one."
            )
        return values[0]


@dataclass
class NodeOutputs:
    """An executor's return value when one value is not the whole story.

    ``by_port``
        ``output port name -> value``. Every declared output the executor produced.
    ``primary``
        The port whose value legacy consumers see as ``node_results[node_id]``, the
        engine's pre-port return value and the trace entry's output.

    ``primary`` is explicit rather than "the first declared port" so an executor can
    publish new ports without moving the value existing callers already read. That
    matters concretely: ``ohlcv_feed`` declares ``frame`` first, but the engine has
    always returned ``close`` for a DATA node, and the golden-plan contract
    (``tests/test_dag_runtime_golden_plan.py``) pins that series. Publishing the
    other five ports must not change it.

    An executor that has exactly one output and no legacy ambiguity keeps returning
    a bare Series; :meth:`DAGEngine.execute_node` wraps it.
    """

    by_port: Dict[str, Any]
    primary: str

    def __post_init__(self) -> None:
        if self.primary not in self.by_port:
            raise DAGExecutionError(
                f"NodeOutputs primary port '{self.primary}' is not among the produced "
                f"ports {sorted(self.by_port)}"
            )

    @property
    def primary_value(self) -> Any:
        return self.by_port[self.primary]


#: ``block_id`` -> ``BlockDescriptor``, memoised per process. The registry is
#: assembled lazily on first use and never at import: assembly reads
#: ``exchange_executor.OrderType`` and would drag CCXT into every process that only
#: wanted to execute a graph. Same seam ``_resolve_block_kernel`` and
#: ``strategy_compiler._engine_registry`` use.
_DESCRIPTOR_CACHE: Dict[str, Any] = {}
_DESCRIPTOR_LOOKUP_FAILED = False


def descriptor_for(node: Mapping[str, Any]) -> Optional[Any]:
    """The registry descriptor for ``node``, or ``None`` when there is none to have.

    ``None`` is returned - never raised - for a node carrying no ``block_id`` (a
    legacy loose dict such as ``{"type": "logic", "operator": "GT"}``) and for a
    ``block_id`` the registry does not publish. Every caller in this module treats
    ``None`` as "no port contract available, behave exactly as before", so a legacy
    payload keeps its pre-task behaviour rather than failing on a lookup it was
    never written to satisfy.

    A registry that cannot be assembled at all (an import-starved environment) is
    latched, so the failure costs one attempt rather than one per node.
    """
    global _DESCRIPTOR_LOOKUP_FAILED

    block_id = node.get("block_id")
    if not isinstance(block_id, str) or not block_id:
        return None
    if block_id in _DESCRIPTOR_CACHE:
        return _DESCRIPTOR_CACHE[block_id]
    if _DESCRIPTOR_LOOKUP_FAILED:
        return None

    try:
        from backend_app.backend.strategy_dag import registry as registry_module

        descriptor = registry_module.get_registry().get(block_id)
    except Exception as exc:  # noqa: BLE001 - absence of a registry is not a node failure
        _DESCRIPTOR_LOOKUP_FAILED = True
        logger.warning(
            "Block registry unavailable; executing without port contracts: %s", exc
        )
        return None

    _DESCRIPTOR_CACHE[block_id] = descriptor
    return descriptor


def _declared_output_ports(node: Mapping[str, Any]) -> Tuple[str, ...]:
    """Output port names ``node``'s descriptor declares, in declaration order."""
    descriptor = descriptor_for(node)
    if descriptor is None:
        return ()
    return tuple(port.name for port in getattr(descriptor, "outputs", ()) or ())


def resolve_node_inputs(
    node_id: str,
    edges: Sequence[Mapping[str, Any]],
    node_results: Mapping[str, Any],
    node_outputs: Optional[Mapping[Tuple[str, str], Any]] = None,
) -> PortInputs:
    """``node_id``'s inputs, resolved port by port from the edges feeding it.

    This is the only input resolver in the engine. Both addressing modes come out of
    one walk of the edge list, so the legacy view and the port view can never
    describe different wiring.

    Value resolution per edge, in order:

    1. ``node_outputs[(source, source_port)]`` - the value that exact output port
       produced. This is what makes ``macd.signal`` and ``macd.histogram`` different
       series, and ``ohlcv_feed.volume`` volume rather than close.
    2. ``node_results[source]`` - the upstream node's primary value. Taken when the
       edge names no ``source_port`` (a legacy payload) or when the named port
       produced nothing, which is exactly the pre-task behaviour.

    An edge naming no ``target_port`` contributes to the legacy id-keyed view only.
    It is not assigned to a guessed port: a guessed port is a wrong operand, and a
    node whose required port is genuinely unfed must surface as unfed.

    Preconditions
        ``edges`` is the payload the engine was handed; ``node_results`` /
        ``node_outputs`` hold the already-executed upstream values.

    Postconditions
        Every returned port name is one an edge explicitly named. Every value in the
        id-keyed view is reachable through at least one port. A port whose value the
        id-keyed view represents resolves *through* that view - the same object, never a
        copy - and a port whose value it cannot represent (two output ports of one
        upstream node landing on two input ports of this node) resolves to the value its
        own edge delivered. Identity decides which, so the view is preserved wherever it
        is faithful and abandoned only where it would lie.
    """
    values: Dict[str, Any] = {}
    port_bindings: Dict[str, List[Tuple[str, Any]]] = {}

    for edge in edges:
        if edge.get("target") != node_id:
            continue
        source = edge.get("source")
        if not source:
            continue

        source_port = edge.get("source_port")
        resolved: Any = None
        found = False
        if node_outputs and isinstance(source_port, str):
            key = (source, source_port)
            if key in node_outputs:
                resolved = node_outputs[key]
                found = True
            elif any(published == source for published, _ in node_outputs):
                # The source published ports, just not this one - so its executor
                # returns fewer outputs than its descriptor declares. The value below
                # is the source's primary, which is what this edge received before
                # port addressing existed; behaviour is preserved rather than
                # improved, but it stops being silent. Closed for indicators by
                # task 5.4 (every declared output port is now published) and still
                # open for models until the ML re-point.
                logger.warning(
                    "Edge %s -> %s names output port '%s.%s', which its executor did "
                    "not produce; falling back to that node's primary output",
                    source,
                    node_id,
                    source,
                    source_port,
                )
        if not found and source in node_results:
            resolved = node_results[source]
            found = True
        if not found:
            continue

        # One upstream node feeding two ports of this node is legitimate
        # (``close -> gt.left`` and ``close -> gt.right``). The id-keyed view
        # collapses them - it always did - while the port view keeps both, and keeps
        # them apart when the two edges carried different output ports.
        values[source] = resolved

        target_port = edge.get("target_port")
        if isinstance(target_port, str) and target_port:
            port_bindings.setdefault(target_port, []).append((source, resolved))

    # Downgrade every binding the id-keyed view represents faithfully to a view of it.
    # ``is`` is the whole test: when the value this edge delivered is the same object the
    # id-keyed view ended up holding, reading it through that view gives the same answer
    # *and* keeps following it through an executor's in-place mutation. Only a binding
    # the collapsed view would answer wrongly keeps its own value.
    for bindings in port_bindings.values():
        for position, (source, resolved) in enumerate(bindings):
            if resolved is values.get(source):
                bindings[position] = (source, _FROM_SOURCE)

    return PortInputs(values, port_bindings=port_bindings)


def validate_port_output(
    value: Any,
    node_id: str,
    port: Any,
    expected_length: int,
    expected_index: pd.Index,
) -> None:
    """Check one produced value against the ``PortType`` its descriptor declares.

    Requirement 20.2: a node output is validated against its declared port type
    *before* it becomes available to downstream nodes. Called from
    :meth:`DAGEngine.execute_node` on the way into the value map, so a value that
    fails never reaches a consumer.

    The series-shaped port types delegate to :func:`validate_node_output` - the
    existing helper, not a second implementation - for the not-None, type, length
    and index-alignment rules. Two deliberate differences from that helper's
    defaults:

    * ``allow_nan=True``. An undefined bar is NaN by contract at this boundary: a
      MATH block writes NaN for division by a zero denominator (Requirement 20.4)
      and a warmup region is NaN until it is satisfied. The executors that already
      call ``validate_node_output`` directly keep their strict no-NaN setting; this
      adds a check where there was none, and weakens nothing.
    * ``±Inf`` is rejected outright (Requirement 20.3: results hold finite values or
      NaN, never an infinity). ``validate_node_output`` does not look for it.

    ``FEATURE_MATRIX`` is checked against its own contract instead: the payload must
    be a :class:`FeatureMatrix`, one row per bar, on the same timestamps. Its
    invariants (strictly increasing index, unique columns, derived warmup, read-only
    values) are enforced by the type's own constructor and are not restated here.

    Raises
        :class:`DAGExecutionError` naming the node and the port.
    """
    port_name = getattr(port, "name", None) or "value"
    port_type = getattr(getattr(port, "type", None), "value", None) or str(
        getattr(port, "type", "")
    )
    label = f"{node_id}.{port_name}"

    if port_type == "FEATURE_MATRIX":
        from backend_app.backend.strategy_dag.feature_matrix import FeatureMatrix

        if not isinstance(value, FeatureMatrix):
            raise DAGExecutionError(
                f"Node output '{label}' is declared FEATURE_MATRIX but produced "
                f"{type(value).__name__}. Only a payload carrying its own timestamp "
                f"index can be aligned downstream."
            )
        if value.n_rows != expected_length:
            raise DAGExecutionError(
                f"Node output '{label}' has {value.n_rows} rows for {expected_length} "
                f"bars of market data."
            )
        if len(expected_index) and not np.array_equal(
            np.asarray(value.index), np.asarray(expected_index)
        ):
            raise DAGExecutionError(
                f"Node output '{label}' is indexed on different timestamps than the "
                f"market data it was computed from."
            )
        return

    validate_node_output(
        output=value,
        node_id=label,
        expected_length=expected_length,
        expected_index=expected_index,
        allow_nan=True,
    )

    numeric = pd.to_numeric(
        value if isinstance(value, pd.Series) else pd.Series(np.asarray(value).ravel()),
        errors="coerce",
    )
    if np.isinf(numeric.to_numpy(dtype=float, na_value=np.nan)).any():
        raise DAGExecutionError(
            f"Node output '{label}' contains an infinite value. An undefined result "
            f"must be NaN, never an infinity, so that it cannot reach an order."
        )


# ═══════════════════════════════════════════════════════════════════════════
#  NUMERIC CONDITIONS RECORDED AGAINST THE NODE (task 5.3, Requirement 20.4)
#
#  "SHALL write a not-a-number value for that bar AND SHALL record the condition
#  against the node" - the second half is the half that is easy to skip. A silent
#  substitution leaves an author looking at an empty bar with no way to find out why,
#  and "why is this bar empty?" is the question task 9.3 surfaces in the inspector.
#  So the condition is recorded where it happened: node id, code, first affected bar
#  and how many bars it touched.
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class NodeIssue:
    """One numeric condition, recorded against the node that produced it.

    ``code`` is the vocabulary the kernels already emit - ``DIVISION_BY_ZERO``,
    ``MODULO_BY_ZERO``, ``NEGATIVE_ROOT``, ``NON_POSITIVE_LOG`` - plus the intent-boundary
    codes ``NON_FINITE_ORDER_FIELD`` and ``NON_POSITIVE_QUANTITY``. It is not an
    enum: the kernels in ``block_specs`` are pure and own their own codes, and pinning
    them to an engine enum here would be a second place for the vocabulary to drift.

    ``bar`` is the *first* affected bar and ``bars_affected`` the count, rather than a
    list of every bar. A poisoned denominator is usually poisoned for a long run of bars,
    and a per-bar list of a 50 000-bar backtest is a memory leak dressed as diagnostics.
    """

    node_id: str
    code: str
    bar: Optional[int] = None
    bars_affected: int = 0
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "node_id": self.node_id,
            "code": self.code,
            "bars_affected": self.bars_affected,
        }
        if self.bar is not None:
            payload["bar"] = self.bar
        if self.detail:
            payload["detail"] = self.detail
        return payload


class NodeIssueLog:
    """Every numeric condition one execution recorded, grouped by node.

    Owned by :class:`DAGEngine` and handed to the executors that can produce one, so a
    condition is recorded once, in one place, by the component that observed it. Cleared
    per run by :meth:`clear` - never *replaced*, because the executors hold a reference to
    this object and swapping in a new one would leave them writing into the old log.
    """

    def __init__(self) -> None:
        self._by_node: Dict[str, List[NodeIssue]] = {}

    def record(
        self,
        node_id: str,
        code: str,
        bar: Optional[int] = None,
        bars_affected: int = 0,
        detail: str = "",
    ) -> NodeIssue:
        issue = NodeIssue(
            node_id=str(node_id),
            code=str(code),
            bar=None if bar is None else int(bar),
            bars_affected=int(bars_affected),
            detail=str(detail or ""),
        )
        self._by_node.setdefault(issue.node_id, []).append(issue)
        logger.warning(
            "Node '%s' recorded %s on %d bar(s) from bar %s",
            issue.node_id,
            issue.code,
            issue.bars_affected,
            issue.bar,
        )
        return issue

    def extend(self, node_id: str, records: Sequence[Mapping[str, Any]]) -> None:
        """Absorb the records a ``block_specs`` kernel produced.

        The kernels emit plain dicts (``{"node_id", "code", "bar", "bars_affected"}``)
        because they are pure array functions that must not import the engine. This is
        the one place that shape is translated, so a kernel that grows a field does not
        have to be taught about :class:`NodeIssue`.
        """
        for record in records or ():
            self.record(
                node_id=record.get("node_id") or node_id,
                code=record.get("code", "UNSPECIFIED"),
                bar=record.get("bar"),
                bars_affected=record.get("bars_affected", 0),
                detail=str(record.get("detail") or ""),
            )

    def for_node(self, node_id: str) -> Tuple[NodeIssue, ...]:
        return tuple(self._by_node.get(node_id, ()))

    def as_dict(self) -> Dict[str, List[Dict[str, Any]]]:
        """JSON-ready ``node_id -> [issue, ...]``, for the inspector and the trace."""
        return {
            node_id: [issue.to_dict() for issue in issues]
            for node_id, issues in self._by_node.items()
        }

    def clear(self) -> None:
        self._by_node.clear()

    def __len__(self) -> int:
        return sum(len(issues) for issues in self._by_node.values())

    def __bool__(self) -> bool:
        return bool(self._by_node)


class NodeExecutor:
    """Base class for node execution."""
    
    def execute(self, node: Dict, inputs: Dict[str, Any], market_data: pd.DataFrame) -> Any:
        raise NotImplementedError


#: Legacy engine indicator name -> the ``indicators_backend`` block id that computes it.
#: The engine's own vocabulary predates the registry and ``bb`` is the only legacy name that
#: is not itself a published block id. ``strategy_compiler._ENGINE_INDICATOR_NAME`` still
#: writes ``bb`` onto a canonical node's ``indicator`` key; the ``block_id`` on that same
#: node is preferred below, so this alias only decides a hand-built legacy dict.
_INDICATOR_LEGACY_ALIASES: Dict[str, str] = {
    "bb": "bollinger_bands",
    "bbands": "bollinger_bands",
    "bollinger": "bollinger_bands",
}

#: Canonical param key -> the key a legacy loose dict carries. The mirror of
#: ``strategy_compiler._ENGINE_INDICATOR_PARAM_ALIAS``: the compiler writes the legacy key
#: alongside the canonical one so an un-re-pointed executor could read it, and this reads a
#: canonical key back out of a payload that carries only the legacy one. Neither direction
#: invents a value - both fall back to the descriptor's own default.
_INDICATOR_PARAM_ALIASES: Tuple[Tuple[str, str], ...] = (
    ("window", "period"),
    ("num_std", "std_dev"),
)

#: ``block_id`` -> the output port whose value stays ``node_results[node_id]``.
#:
#: Deliberately not "the first declared port". The pre-re-point executor returned MACD's
#: **histogram** (``macd - signal``) and Bollinger's **%B**, and publishing the other ports
#: must not move the value an existing edge, ``dag_event_loop``'s signal history or the
#: golden-plan digest already reads. Every other indicator has one output, or its first
#: declared port is the one the executor returned.
_INDICATOR_LEGACY_PRIMARY_PORT: Dict[str, str] = {
    "macd": "histogram",
    "bollinger_bands": "percent_b",
}

#: Composite price sources ``INDICATOR_SPECS`` publishes as ``source`` options but which are
#: not market-data columns. Only consulted on the fallback path below (an unfed ``series``
#: port); a canonical graph wires the resolved column in as an edge instead.
_COMPOSITE_PRICE_SOURCES = ("hl2", "hlc3", "ohlc4")


class IndicatorExecutor(NodeExecutor):
    """Execute an INDICATOR node through ``indicators_backend`` (task 5.4).

    ``design.md`` -> Runtime references: the registry publishes each indicator's
    ``runtime_ref`` and "``MathExecutor`` and ``LogicExecutor`` delegate to them rather than
    reimplementing arithmetic, exactly as ``IndicatorExecutor`` delegates to
    ``indicators_backend``". This executor now does that literally: no indicator formula
    lives here. ``INDICATOR_SPECS`` owns all 33 of them, their parameter contracts and their
    output-port declarations, and this class is the seam that binds ports to operands and
    turns the returned arrays into bar-aligned Series.

    WHAT THIS REPLACED, AND WHY IT IS NOT A REFACTOR
    ------------------------------------------------
    The previous body was an ``if indicator == ...`` chain over six names backed by four
    private ``_calculate_*`` re-implementations, with a final ``else`` that logged a warning
    and computed **RSI(14)**. Two defects followed from that shape and both are closed here:

    * 27 of the 33 published indicators had no branch, so an author who selected ADX,
      SuperTrend or Ichimoku got RSI(14) - a different strategy, running silently.
    * A multi-output indicator collapsed onto one series, so an edge from
      ``bollinger_bands.upper`` received %B and an edge from ``macd.signal`` received the
      histogram. Every declared output port is now published through :class:`NodeOutputs`,
      which is what makes ``resolve_node_inputs`` stop warning that an edge names a port its
      source did not produce.

    The four deleted private duplicates also *disagreed* with the module they duplicated -
    ``_calculate_rsi`` used a simple rolling mean where ``indicators_backend.rsi`` uses
    Wilder smoothing, and ``_calculate_bollinger`` used a sample standard deviation where
    ``bollinger_bands`` uses a population one - so the engine and the registry's published
    warmup were describing different numbers. Deleting them is the point of this task, not a
    side effect of it.

    INPUTS BY DECLARED PORT NAME
    ----------------------------
    Operands are bound from ``spec.inputs`` in declaration order through
    :meth:`PortInputs.port_values`, the same rule
    :meth:`BlockKernelExecutor._bind_operands` states for MATH and LOGIC.
    ``tests/test_indicator_specs.py`` already asserts that an indicator's declared input
    ports bind positionally to its runtime's required parameters, so declaration order *is*
    the call order and nothing here restates it.

    A port with no connection falls back to the market-data column of the same name -
    ``high``, ``low``, ``close``, ``volume`` - and ``series`` falls back to the price source
    the node's ``source`` param names. That fallback is what the pre-re-point executor did
    unconditionally (it read ``market_data`` and ignored its inputs entirely), so a legacy
    loose dict with no port-addressed edges behaves as it did before.

    OUTPUTS
    -------
    One Series per declared output port, in declaration order, which is the order the
    runtime returns them in (asserted per spec by
    ``tests/test_indicator_specs.py::test_spec_executes_and_returns_one_series_per_output_port``).
    The warmup region is NaN by contract - that is what lets Requirement 20.1's readiness
    gate tell "no value yet" from "a number" - so the executor's output check permits NaN and
    :func:`validate_port_output` enforces the port type on the way into the value map.

    CONTROLS
    --------
    ``guard_market_data``, ``observe_market_data``, ``guard_indicators``,
    ``observe_indicators`` and the excessive-missing-data refusal on the close column are
    kept exactly where they were. ``guard_indicators`` / ``observe_indicators`` were
    previously unreachable for the six named indicators (each branch returned first) and now
    run for every indicator, which is the direction that adds a check rather than removing
    one.
    """

    def execute(
        self, node: Dict, inputs: Dict[str, Any], market_data: pd.DataFrame
    ) -> Any:
        """Execute indicator node with validation."""
        node_id = node.get("id", "unknown")

        # 🛡️ STRICT INPUT CONTRACT VALIDATION
        try:
            validate_node_inputs(inputs, node_id)
        except DAGExecutionError as e:
            logger.critical(f"🚫 Node '{node_id}' input validation failed: {e}")
            raise

        # 🛡️ PIPELINE GUARD: Validate market data at entry
        try:
            guard_market_data(market_data)
        except PipelineError as e:
            raise IndicatorComputationError(f"Pipeline guard failed: {e}")

        # 📊 OBSERVABILITY: Log market data quality
        metrics = observe_market_data(market_data, "indicator_input")
        dashboard.record(metrics)

        # Get price data with validation
        if "close" not in market_data.columns:
            raise IndicatorComputationError("Missing 'close' price column")

        close = market_data["close"]

        # Check for excessive NaN (more than 5%)
        nan_pct = close.isna().sum() / len(close) * 100
        if nan_pct > 5:
            raise IndicatorComputationError(
                f"Excessive missing data: {nan_pct:.1f}% NaN in close prices"
            )

        # Safe forward fill with limit
        close = close.ffill(limit=3)

        # If still NaN after limited fill, raise error
        if close.isna().any():
            raise IndicatorComputationError(
                "Excessive missing data: NaN remains after ffill(limit=3). "
                f"NaN count: {close.isna().sum()}"
            )

        spec = self._spec_for(node, node_id)
        params = self._resolved_params(node)
        runtime = spec.resolve_runtime()
        operands = self._bind_operands(spec, node_id, inputs, market_data, close, params)

        try:
            raw = runtime(*operands, **spec.runtime_kwargs(params))
        except DAGExecutionError:
            raise
        except Exception as exc:  # noqa: BLE001 - a runtime refusal is a node-level failure
            raise IndicatorComputationError(
                f"Node '{node_id}': indicator '{spec.block_id}' refused its inputs: {exc}"
            ) from exc

        by_port = self._series_per_output_port(spec, node_id, raw, market_data)
        primary = _INDICATOR_LEGACY_PRIMARY_PORT.get(
            spec.block_id, spec.outputs[0].name
        )
        output = by_port[primary]

        # 🛡️ PIPELINE GUARD: Validate indicator output
        try:
            guard_indicators(output)
        except PipelineError as e:
            raise IndicatorComputationError(f"Pipeline guard failed: {e}")

        # 📊 OBSERVABILITY: Log indicator quality
        indicator_metrics = observe_indicators(output, f"indicator_{spec.block_id}")
        dashboard.record(indicator_metrics)

        # 🛡️ OUTPUT CONTRACT VALIDATION
        #
        # ``allow_nan=True`` and nothing else about this call changed. An indicator's warmup
        # region is NaN by contract (``indicators_backend`` returns ``_nan_array`` for the
        # bars before its window is satisfied), and Requirement 20.1 depends on that: a
        # readiness gate can only hold a node "warming" if an absent value is absent rather
        # than a fabricated zero. The strict form was unreachable here before the re-point -
        # every named branch returned first, and the ``else`` branch that did reach it
        # returned a rolling RSI whose own warmup would have failed it.
        validate_node_output(
            output=output,
            node_id=node_id,
            expected_length=len(market_data),
            expected_index=market_data.index,
            allow_nan=True,
        )

        if not _declared_output_ports(node):
            # A legacy loose dict declares no ports, so there is no port contract to publish
            # against and ``_publish_node_outputs`` would drop each one with a warning. The
            # bare Series is exactly what such a caller received before this task.
            return output
        return NodeOutputs(by_port=by_port, primary=primary)

    # -- descriptor and params -------------------------------------------

    @staticmethod
    def _spec_for(node: Mapping[str, Any], node_id: str) -> Any:
        """The ``IndicatorSpec`` this node names.

        ``block_id`` is preferred over the engine's ``indicator`` key, because the canonical
        one is the registry's own vocabulary while ``indicator`` may carry a legacy alias.

        A named-but-unpublished indicator is **refused**. The previous body logged a warning
        and computed RSI(14), which is how 27 of the 33 published indicators silently became
        RSI; running a different indicator than the author selected is a wrong-strategy
        defect, not a degraded mode. A payload naming no indicator at all keeps its previous
        default of ``rsi``.
        """
        from backend_app.backend import indicators_backend

        candidates: List[str] = []
        block_id = node.get("block_id")
        if isinstance(block_id, str) and block_id:
            candidates.append(block_id)
        named = node.get("indicator")
        if isinstance(named, str) and named:
            lowered = named.lower()
            candidates.append(_INDICATOR_LEGACY_ALIASES.get(lowered, lowered))
        if not candidates:
            candidates.append("rsi")

        for candidate in candidates:
            spec = indicators_backend.get_indicator_spec(candidate)
            if spec is not None:
                return spec

        raise IndicatorComputationError(
            f"Node '{node_id}': no published indicator matches {candidates}. "
            f"indicators_backend.INDICATOR_SPECS is the set of indicators that exist; "
            f"substituting a different one would run a strategy the author did not author."
        )

    @staticmethod
    def _resolved_params(node: Mapping[str, Any]) -> Dict[str, Any]:
        """The node's params under the canonical keys ``INDICATOR_SPECS`` publishes."""
        params = dict(node.get("params") or {})
        for canonical, legacy in _INDICATOR_PARAM_ALIASES:
            if canonical not in params and legacy in params:
                params[canonical] = params[legacy]
        return params

    # -- input binding ----------------------------------------------------

    def _bind_operands(
        self,
        spec: Any,
        node_id: str,
        inputs: Dict[str, Any],
        market_data: pd.DataFrame,
        close: pd.Series,
        params: Mapping[str, Any],
    ) -> List[np.ndarray]:
        """One operand per declared input port, in declaration order."""
        port_view = inputs if isinstance(inputs, PortInputs) else PortInputs(inputs)
        operands: List[np.ndarray] = []

        for port in spec.inputs:
            values = port_view.port_values(port.name)
            if len(values) > 1:
                raise DAGExecutionError(
                    f"Node '{node_id}': port '{port.name}' accepts one connection but "
                    f"holds {len(values)}."
                )
            if values:
                if values[0] is None:
                    raise DAGExecutionError(
                        f"Node '{node_id}': port '{port.name}' received no value. "
                        f"Upstream node produced no output."
                    )
                operands.append(self._as_bars(values[0], node_id, port.name, market_data))
                continue

            fallback = self._from_market_data(port.name, market_data, close, params)
            if fallback is None:
                raise DAGExecutionError(
                    f"Node '{node_id}': required port '{port.name}' has no connection and "
                    f"the market data has no '{port.name}' column to fall back to."
                )
            operands.append(self._as_bars(fallback, node_id, port.name, market_data))

        return operands

    @staticmethod
    def _as_bars(
        value: Any, node_id: str, port_name: str, market_data: pd.DataFrame
    ) -> np.ndarray:
        array = np.asarray(value, dtype=float).reshape(-1)
        if array.size != len(market_data):
            raise DAGExecutionError(
                f"Node '{node_id}': port '{port_name}' holds {array.size} values for "
                f"{len(market_data)} bars of market data."
            )
        return array

    @staticmethod
    def _from_market_data(
        port_name: str,
        market_data: pd.DataFrame,
        close: pd.Series,
        params: Mapping[str, Any],
    ) -> Optional[Any]:
        """The market-data series an unfed port reads, or ``None`` when there is none.

        This is the pre-re-point behaviour: the executor read ``market_data`` directly and
        ignored its inputs, so a legacy payload keeps computing over the same columns.
        ``series`` honours the node's ``source`` param rather than assuming ``close``, since
        an authored ``source="hl2"`` that silently read close would be a different
        indicator; a canonical graph never reaches this branch, because the compiler
        resolves ``source`` into the edge it wires.
        """
        if port_name == "close":
            return close
        if port_name in market_data.columns:
            return market_data[port_name]
        if port_name != "series":
            return None

        source = str(params.get("source") or "close").lower()
        if source == "close":
            return close
        if source in market_data.columns:
            return market_data[source]
        if source in _COMPOSITE_PRICE_SOURCES and {"high", "low"} <= set(
            market_data.columns
        ):
            high = market_data["high"]
            low = market_data["low"]
            if source == "hl2":
                return (high + low) / 2.0
            if source == "hlc3":
                return (high + low + close) / 3.0
            if "open" in market_data.columns:
                return (market_data["open"] + high + low + close) / 4.0
        return close

    # -- output shaping ---------------------------------------------------

    @staticmethod
    def _series_per_output_port(
        spec: Any, node_id: str, raw: Any, market_data: pd.DataFrame
    ) -> Dict[str, pd.Series]:
        """The runtime's return value as one bar-aligned Series per declared output port.

        A single-output indicator returns one array; a multi-output one returns a tuple in
        declaration order. An arity mismatch is a refusal rather than a truncation, because
        zipping a shorter tuple onto the declared ports would silently mislabel every port
        after the gap - a ``signal`` port carrying a histogram.
        """
        produced = list(raw) if isinstance(raw, tuple) else [raw]
        declared = spec.outputs
        if len(produced) != len(declared):
            raise IndicatorComputationError(
                f"Node '{node_id}': indicator '{spec.block_id}' declares "
                f"{len(declared)} output port(s) {[p.name for p in declared]} but its "
                f"runtime returned {len(produced)} series."
            )

        index = market_data.index
        by_port: Dict[str, pd.Series] = {}
        for port, value in zip(declared, produced):
            array = np.asarray(value, dtype=float).reshape(-1)
            if array.size != len(index):
                raise IndicatorComputationError(
                    f"Indicator output shape mismatch: expected {len(index)}, got "
                    f"{array.size} on port '{node_id}.{port.name}'"
                )
            by_port[port.name] = pd.Series(array, index=index)
        return by_port


class MLExecutor(NodeExecutor):
    """Execute ML prediction nodes."""
    
    def __init__(self, model_registry: Optional[Dict] = None):
        self.model_registry = model_registry or {}
    
    def execute(self, node: Dict, inputs: Dict[str, Any], market_data: pd.DataFrame) -> pd.Series:
        """Execute ML node with strict validation."""
        node_id = node.get("id", "unknown")
        model_id = node.get("model_id", "default")
        node.get("confidence_threshold", 0.7)
        
        # �️ STRICT INPUT CONTRACT VALIDATION
        try:
            validate_node_inputs(inputs, node_id)
        except DAGExecutionError as e:
            logger.critical(f"🚫 Node '{node_id}' input validation failed: {e}")
            raise
        
        # �� SYSTEM FREEZE: Check if ML inference is allowed
        safety_check = SafetyMonitor.check_execution_allowed("ml_inference")
        if safety_check:
            logger.critical(f"🚫 BLOCKED ML INFERENCE: {safety_check}")
            # Return neutral signal instead of executing
            return pd.Series(0.5, index=market_data.index)
        
        # Get features from input nodes
        features = []
        for input_id, value in inputs.items():
            if isinstance(value, pd.Series):
                features.append(value)
        
        if not features:
            # No features available, return neutral signal
            return pd.Series(0.5, index=market_data.index)
        
        # Combine features with SAFE forward fill (limited)
        feature_df = pd.concat(features, axis=1)
        feature_df = feature_df.ffill(limit=3)
        
        # Check for remaining NaN - cannot proceed with missing feature data
        if feature_df.isna().any().any():
            nan_count = feature_df.isna().sum().sum()
            raise IndicatorComputationError(
                f"ML features contain NaN after safe fill: {nan_count} values. "
                f"Cannot perform inference with incomplete feature data."
            )
        
        # Load model - NO MOCK FALLBACK ALLOWED
        model = self.model_registry.get(model_id)
        
        if not model:
            # 🚨 CRITICAL: Model not found - execution blocked
            logger.critical(
                f"🚫 MODEL NOT FOUND: model_id={model_id}. "
                f"DAG node cannot execute. Aborting to prevent fake signals."
            )
            # Alert system notification
            try:
                from backend_app.backend.alert_system import AlertSystem
                AlertSystem.send_alert(
                    level="CRITICAL",
                    title="ML Model Missing - Execution Blocked",
                    message=f"Model {model_id} not found in registry. Strategy execution blocked.",
                    channels=["telegram", "email"]
                )
            except Exception:
                pass  # Alert system may not be initialized
            
            raise RuntimeError(
                f"🚫 MODEL NOT FOUND: {model_id}\n"
                f"The ML model required for this DAG node is not available.\n"
                f"Execution has been BLOCKED to prevent fake signals from triggering trades.\n"
                f"Please verify:\n"
                f"  1. Model was properly trained and saved\n"
                f"  2. Model registry contains the model\n"
                f"  3. Model ID in strategy matches registry"
            )
        
        # 🛡️ PIPELINE GUARD: Validate features before ML
        try:
            guard_features(feature_df)
        except PipelineError as e:
            raise RuntimeError(f"Pipeline guard failed at feature stage: {e}")
        
        # 📊 OBSERVABILITY: Log feature quality
        feature_metrics = observe_features(feature_df, "ml_input")
        dashboard.record(feature_metrics)
        
        # 🚨 STEP 1: VALIDATE FEATURES BEFORE PREDICTION
        try:
            # Try to get schema from model or use inferred schema
            schema = self._get_feature_schema(model, feature_df)
            
            # Validate and prepare features
            feature_df = FeatureValidator.validate_and_prepare(
                feature_df=feature_df,
                schema=schema,
                model=model,
                market_data_index=market_data.index
            )
            
            logger.info(f"✅ Features validated: {feature_df.shape}")
            
        except (FeatureValidationError, ModelMismatchError) as e:
            logger.critical(f"🚫 FEATURE VALIDATION FAILED: {e}")
            raise RuntimeError(
                f"ML feature validation failed: {e}. "
                f"Cannot perform inference with invalid features."
            )
        
        # 🛡️ PIPELINE GUARD: Validate before ML prediction
        try:
            guard_ml_prediction(feature_df)
        except PipelineError as e:
            raise RuntimeError(f"Pipeline guard failed at ML stage: {e}")
        
        # STEP 2: Real model prediction with validated features
        try:
            predictions = model.predict(feature_df)
            
            # Validate predictions
            if len(predictions) != len(market_data):
                raise IndicatorComputationError(
                    f"Prediction shape mismatch: expected {len(market_data)}, "
                    f"got {len(predictions)}"
                )
            
            if np.isnan(predictions).any():
                nan_count = np.isnan(predictions).sum()
                raise IndicatorComputationError(
                    f"Model produced NaN predictions: {nan_count} values"
                )
            
            logger.info(f" Model prediction successful: {len(predictions)} predictions")
            
            # PIPELINE GUARD: Validate signal generation
            result = pd.Series(predictions, index=market_data.index)
            try:
                # Final validation - ensure no NaN after scaling
                if None.isna().any().any():
                    raise FeatureValidationError("NaN values introduced during scaling")
            
                # OUTPUT CONTRACT VALIDATION
                validate_node_output(
                    output=result,
                    node_id=node_id,
                    expected_length=len(market_data),
                    expected_index=market_data.index
                )
            
                return None
            except Exception as e:
                logger.critical(f" Model prediction failed: {e}")
                raise RuntimeError(f"Model prediction failed: {e}")
            
            # OBSERVABILITY: Log prediction quality
            prediction_metrics = observe_ml_predictions(result, "ml_output")
            dashboard.record(prediction_metrics)
            
            return result
            
        except Exception as e:
            logger.critical(f"🚫 Model prediction failed: {e}")
            raise RuntimeError(f"Model prediction failed: {e}")


class LegacyLogicExecutor(NodeExecutor):
    """Execute a legacy loose-dict logic node from its uppercase ``operator`` key.

    Reached only by a payload that carries no ``runtime_ref`` - a hand-built
    ``{"type": "logic", "operator": "GT"}`` dict, of which there are still callers. Kept
    verbatim as :class:`LogicExecutor`'s fallback so such a payload behaves exactly as it
    did; every canonical LOGIC node goes through the ``block_specs`` kernel its descriptor
    publishes instead, which is where the comparators, gates, ``between``, ``if_then_else``,
    ``to_signal`` and the two crosses actually live.
    """
    
    def execute(self, node: Dict, inputs: Dict[str, Any], market_data: pd.DataFrame) -> pd.Series:
        """Execute logic nodes (AND, OR, NOT, comparisons)."""
        node_id = node.get("id", "unknown")
        operator = node.get("operator", "AND")
        
        # 🛡️ STRICT INPUT CONTRACT VALIDATION
        try:
            validate_node_inputs(inputs, node_id)
        except DAGExecutionError as e:
            logger.critical(f"🚫 Node '{node_id}' input validation failed: {e}")
            raise
        
        # Get all input series
        input_series = []
        for input_id, value in inputs.items():
            if isinstance(value, pd.Series):
                input_series.append(value)
            elif isinstance(value, (int, float, bool)):
                # Convert scalar to series
                input_series.append(pd.Series(value, index=market_data.index))
        
        # Check for empty after filtering
        if len(input_series) == 0:
            raise DAGExecutionError(
                f"Node '{node_id}': No valid Series inputs found. "
                f"All inputs were filtered out (None, empty, or wrong type)."
            )
        
        # Align all series
        aligned = pd.concat(input_series, axis=1)
        
        # 🚨 HARD GATE: Check for NaN in logic inputs (no silent fill)
        if aligned.isna().any().any():
            nan_locations = aligned.isna().sum()
            nan_cols = nan_locations[nan_locations > 0].index.tolist()
            raise IndicatorComputationError(
                f"NaN detected in logic inputs. "
                f"Columns with NaN: {nan_cols}. "
                f"Cannot perform logic operations with missing data."
            )
        
        if operator == "AND":
            return aligned.all(axis=1)
        elif operator == "OR":
            return aligned.any(axis=1)
        elif operator == "NOT":
            if len(input_series) == 0:
                raise DAGExecutionError(
                    f"LogicExecutor - NOT operator on node '{node_id}' has no inputs. "
                    f"NOT requires at least one input signal."
                )
            return ~input_series[0]
        elif operator == "GT":
            if len(input_series) >= 2:
                return aligned.iloc[:, 0] > aligned.iloc[:, 1]
        elif operator == "LT":
            if len(input_series) >= 2:
                return aligned.iloc[:, 0] < aligned.iloc[:, 1]
        elif operator == "GTE":
            if len(input_series) >= 2:
                return aligned.iloc[:, 0] >= aligned.iloc[:, 1]
        elif operator == "LTE":
            if len(input_series) >= 2:
                return aligned.iloc[:, 0] <= aligned.iloc[:, 1]
        elif operator == "EQ":
            if len(input_series) >= 2:
                return aligned.iloc[:, 0] == aligned.iloc[:, 1]
        
        # Default fallback
        output = pd.Series(False, index=market_data.index)
        
        # 🛡️ OUTPUT CONTRACT VALIDATION
        validate_node_output(
            output=output,
            node_id=node_id,
            expected_length=len(market_data),
            expected_index=market_data.index
        )
        
        return output


#: The signal level an ACTION node fires above. Spelled once because **two** places read
#: it: :class:`ActionExecutor`, which turns the incoming signal into the signed series the
#: backtester sums, and :func:`build_intent` (task 8.4), which decides whether the runtime
#: has a Trade_Intent to emit at the current bar. A second literal would let a live intent
#: fire on a bar the backtester scored flat, which is Requirement 22.4 broken by a typo.
ACTION_TRIGGER_THRESHOLD = 0.5


class ActionExecutor(NodeExecutor):
    """Execute action nodes (buy, sell, hold)."""
    
    def execute(self, node: Dict, inputs: Dict[str, Any], market_data: pd.DataFrame) -> pd.Series:
        """Execute action nodes (buy, sell, hold)."""
        node_id = node.get("id", "unknown")
        action = node.get("action", "hold").lower()
        
        # 🛡️ STRICT INPUT CONTRACT VALIDATION
        try:
            validate_node_inputs(inputs, node_id)
        except DAGExecutionError as e:
            logger.critical(f"🚫 Node '{node_id}' input validation failed: {e}")
            raise
        
        # 🚫 SYSTEM FREEZE: Block buy/sell actions
        if action in ["buy", "sell"]:
            safety_check = SafetyMonitor.check_execution_allowed("strategy_signal")
            if safety_check:
                logger.critical(f"🚫 BLOCKED DAG ACTION: {safety_check}")
                # Return zeros (no action) instead of executing
                return pd.Series(0, index=market_data.index)
        
        # Get signal from inputs - MUST be from SIGNAL or LOGIC node
        signal = pd.Series(0, index=market_data.index)
        signal_found = False
        False
        
        for input_id, value in inputs.items():
            if isinstance(value, pd.Series):
                signal = value
                signal_found = True
                # Check if input is from a valid signal source
                # (This would need to be passed through from node metadata)
                True
                break
        
        if not signal_found:
            raise DAGExecutionError(
                f"Node '{node_id}': No valid signal Series found in inputs. "
                f"Action node requires a Series input from upstream nodes."
            )
        
        if signal is None:
            # No signal, default to hold (0)
            return pd.Series(0, index=market_data.index)
        
        # Convert to action signal
        # signal > ACTION_TRIGGER_THRESHOLD = buy (1) / sell (-1), else hold (0)
        if action == "buy":
            output = (signal > ACTION_TRIGGER_THRESHOLD).astype(int)
        elif action == "sell":
            output = -(signal > ACTION_TRIGGER_THRESHOLD).astype(int)
        else:  # hold
            output = pd.Series(0, index=market_data.index)
        
        # SHAPE VALIDATION: Ensure output matches market_data length
        if len(output) != len(market_data):
            raise IndicatorComputationError(
                f"Action output shape mismatch: "
                f"expected {len(market_data)}, got {len(output)}"
            )
        
        # 🛡️ OUTPUT CONTRACT VALIDATION
        validate_node_output(
            output=output,
            node_id=node_id,
            expected_length=len(market_data),
            expected_index=market_data.index
        )
        
        return output


@dataclass
class ExecutionTrace:
    """Single node execution trace entry."""
    node_id: str
    node_type: str
    input_shape: Optional[Dict[str, Any]] = None
    output_shape: Optional[Dict[str, Any]] = None
    input_types: Optional[Dict[str, str]] = None
    #: ``input port -> upstream node ids feeding it``, when the recorded inputs knew.
    #:
    #: :attr:`input_shape` and :attr:`input_types` are keyed by **upstream node id**,
    #: because that is how :class:`PortInputs` addresses a value and what every executor
    #: reads. An author reading a trace needs the *port* - "which of this block's inputs
    #: was that", not "which ULID fed it" - so the port view is recorded alongside rather
    #: than instead of: re-keying the existing two maps would move a payload
    #: :meth:`DAGEngine.get_execution_trace` already publishes, and the id-keyed view is
    #: the only one that can represent a source feeding two ports.
    input_ports: Optional[Dict[str, List[str]]] = None
    output_type: Optional[str] = None
    execution_time_ms: float = 0.0
    status: str = "pending"  # pending, success, fail
    error_message: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)


class ExecutionTracer:
    """
    Execution trace system for DAG debugging.
    
    Records every node execution for full transparency and debugging.
    """
    
    def __init__(self, enabled: bool = True):
        self._enabled = enabled
        self._trace: List[ExecutionTrace] = []
        self._node_outputs: Dict[str, Any] = {}  # For debugging
    
    def start_node(self, node_id: str, node_type: str, inputs: Dict[str, Any]) -> None:
        """Record node execution start."""
        if not self._enabled:
            return
        
        # Capture input shapes
        input_shapes = {}
        input_types = {}
        for key, value in inputs.items():
            if isinstance(value, pd.Series):
                input_shapes[key] = {"type": "Series", "length": len(value)}
                input_types[key] = f"Series[{value.dtype}]"
            elif isinstance(value, pd.DataFrame):
                input_shapes[key] = {"type": "DataFrame", "shape": value.shape}
                input_types[key] = f"DataFrame[{list(value.columns)}]"
            elif isinstance(value, (int, float, bool)):
                input_shapes[key] = {"type": "scalar", "value": value}
                input_types[key] = type(value).__name__
            else:
                input_shapes[key] = {"type": type(value).__name__}
                input_types[key] = type(value).__name__
        
        # The port view, when the inputs object carries one. `PortInputs.port_sources` is
        # the compiler's own port-level wiring, already resolved for this node - asked of
        # the object rather than re-derived from the edges, so the trace cannot disagree
        # with what the executor was handed. A legacy plain dict has no ports and records
        # none, which reads as "unknown" rather than as "no inputs".
        port_sources = getattr(inputs, "port_sources", None)
        input_ports = (
            {
                str(port): [str(source) for source in sources]
                for port, sources in port_sources.items()
            }
            if isinstance(port_sources, dict)
            else None
        )

        trace_entry = ExecutionTrace(
            node_id=node_id,
            node_type=node_type,
            input_shape=input_shapes,
            input_types=input_types,
            input_ports=input_ports,
            status="pending"
        )
        
        self._trace.append(trace_entry)
    
    def complete_node(
        self,
        node_id: str,
        output: Any,
        execution_time_ms: float,
        status: str = "success",
        error_message: Optional[str] = None
    ) -> None:
        """Record node execution completion."""
        if not self._enabled:
            return
        
        # Find pending entry for this node
        for entry in reversed(self._trace):
            if entry.node_id == node_id and entry.status == "pending":
                # Capture output shape
                if isinstance(output, pd.Series):
                    entry.output_shape = {"type": "Series", "length": len(output)}
                    entry.output_type = f"Series[{output.dtype}]"
                elif isinstance(output, pd.DataFrame):
                    entry.output_shape = {"type": "DataFrame", "shape": output.shape}
                    entry.output_type = f"DataFrame[{list(output.columns)}]"
                else:
                    entry.output_shape = {"type": type(output).__name__}
                    entry.output_type = type(output).__name__
                
                entry.execution_time_ms = execution_time_ms
                entry.status = status
                entry.error_message = error_message
                
                # Store output for debugging
                if status == "success":
                    self._node_outputs[node_id] = output
                
                break
    
    def get_trace(self) -> List[ExecutionTrace]:
        """Get full execution trace."""
        return self._trace.copy()

    @staticmethod
    def entry_as_dict(entry: ExecutionTrace) -> Dict[str, Any]:
        """One recorded entry, JSON-ready. The single wire form of a trace entry.

        Task 9.3 surfaces a trace in the inspector and needs the same fields per entry
        that :meth:`get_trace_as_dict` already publishes for the whole run. Sharing one
        serialiser is what keeps a field added here from appearing on one of the two
        paths only.
        """
        return {
            "node_id": entry.node_id,
            "node_type": entry.node_type,
            "input_shape": entry.input_shape,
            "output_shape": entry.output_shape,
            "input_types": entry.input_types,
            "input_ports": entry.input_ports,
            "output_type": entry.output_type,
            "execution_time_ms": round(entry.execution_time_ms, 3),
            "status": entry.status,
            "error_message": entry.error_message,
            "timestamp": entry.timestamp.isoformat(),
        }

    def get_trace_as_dict(self) -> List[Dict[str, Any]]:
        """Get trace as list of dictionaries for JSON serialization."""
        return [self.entry_as_dict(t) for t in self._trace]

    def entries_for_node(self, node_id: str) -> Tuple[ExecutionTrace, ...]:
        """Every entry this run recorded for ``node_id``, in the order recorded.

        A list rather than a single entry: one node can be started more than once in a
        run, and reporting only the last would hide a first attempt that failed.
        """
        wanted = str(node_id)
        return tuple(entry for entry in self._trace if entry.node_id == wanted)

    def node_trace_as_dict(self, node_id: str) -> List[Dict[str, Any]]:
        """``node_id``'s entries, JSON-ready. Requirement 24.6's inputs/outputs/duration."""
        return [self.entry_as_dict(entry) for entry in self.entries_for_node(node_id)]

    def failures_as_dict(self) -> List[Dict[str, Any]]:
        """Every failed entry of this run, JSON-ready, oldest first.

        Requirement 24.6's "recorded failures". Deliberately the whole run rather than one
        node: the answer to "why did nothing happen?" is frequently a node *upstream* of
        the one selected, and :meth:`get_last_failure` reports only the newest one.
        """
        return [
            self.entry_as_dict(entry) for entry in self._trace if entry.status == "fail"
        ]

    
    def get_last_failure(self) -> Optional[ExecutionTrace]:
        """Get the last failed execution trace entry."""
        for entry in reversed(self._trace):
            if entry.status == "fail":
                return entry
        return None
    
    def get_execution_summary(self) -> Dict[str, Any]:
        """Get execution summary statistics."""
        total = len(self._trace)
        successful = sum(1 for t in self._trace if t.status == "success")
        failed = sum(1 for t in self._trace if t.status == "fail")
        pending = sum(1 for t in self._trace if t.status == "pending")
        
        total_time = sum(t.execution_time_ms for t in self._trace)
        
        return {
            "total_nodes": total,
            "successful": successful,
            "failed": failed,
            "pending": pending,
            "total_execution_time_ms": round(total_time, 3),
            "success_rate": successful / total if total > 0 else 0,
            "last_failure": self.get_last_failure()
        }
    
    def clear(self) -> None:
        """Clear trace history."""
        self._trace.clear()
        self._node_outputs.clear()
    
    def enable(self) -> None:
        """Enable tracing."""
        self._enabled = True
    
    def disable(self) -> None:
        """Disable tracing."""
        self._enabled = False


class MarketDataExecutor:
    """Execute market data, input, feature, math, validation, portfolio, and signal nodes."""
    def execute(self, node: Dict, inputs: Dict[str, Any], market_data: pd.DataFrame) -> pd.Series:
        if "close" in market_data.columns:
            return market_data["close"]
        elif not market_data.empty:
            return market_data.iloc[:, 0]
        elif inputs:
            first_val = next(iter(inputs.values()))
            if isinstance(first_val, pd.Series):
                return first_val
        return pd.Series(1, index=market_data.index if not market_data.empty else [0])


# ---------------------------------------------------------------------------
# Canonical MATH / LOGIC nodes: delegate to the kernel the descriptor publishes
# ---------------------------------------------------------------------------

#: ``runtime_ref`` owner whose members are the pure array kernels declared in
#: ``strategy_dag/block_specs.py``. A reference owned by anything else (``DataEngine``,
#: ``CCXTExchangeExecutor``) performs I/O or places an order and is NEVER called from here:
#: order placement stays behind ``execution_guard`` / ``risk_engine`` / ``execution_engine``.
_BLOCK_KERNEL_OWNER = "block_specs."

#: ``runtime_ref`` -> (callable, accepted keyword names). Resolution walks a module lookup,
#: so it is memoised per process; the kernels are pure, so sharing them is safe.
_BLOCK_KERNEL_CACHE: Dict[str, Any] = {}


def _resolve_block_kernel(runtime_ref: str):
    """The kernel behind ``runtime_ref`` plus the keyword names it accepts."""
    cached = _BLOCK_KERNEL_CACHE.get(runtime_ref)
    if cached is not None:
        return cached

    import inspect

    from backend_app.backend.strategy_dag.block_specs import resolve_block_runtime

    kernel = resolve_block_runtime(runtime_ref)
    accepted = {
        parameter.name
        for parameter in inspect.signature(kernel).parameters.values()
        if parameter.kind
        in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)
    }
    resolved = (kernel, accepted)
    _BLOCK_KERNEL_CACHE[runtime_ref] = resolved
    return resolved


def _kernel_output_to_series(
    raw: Any, node_id: str, index: pd.Index
) -> pd.Series:
    """A kernel's return value as the per-bar Series the engine propagates.

    A kernel returns a NumPy array for a per-bar block and a plain float for
    ``math_constant``; the constant is broadcast so a downstream comparison sees the
    author's value on every bar instead of a republished ``close``.
    """
    if isinstance(raw, pd.Series):
        series = raw
    elif isinstance(raw, np.ndarray):
        if raw.ndim == 0:
            series = pd.Series(raw.item(), index=index)
        else:
            if len(raw) != len(index):
                raise DAGExecutionError(
                    f"Node '{node_id}' kernel returned {len(raw)} bars for "
                    f"{len(index)} bars of market data"
                )
            series = pd.Series(raw, index=index)
    elif isinstance(raw, (bool, int, float, np.generic)):
        series = pd.Series(raw, index=index)
    else:
        raise DAGExecutionError(
            f"Node '{node_id}' kernel returned {type(raw).__name__}; the engine propagates "
            "per-bar Series values only"
        )

    if len(series) != len(index):
        raise DAGExecutionError(
            f"Node '{node_id}' kernel output length {len(series)} does not match the "
            f"{len(index)} bars of market data"
        )
    return series


# ═══════════════════════════════════════════════════════════════════════════
#  THE NUMERIC SAFETY FIREWALL (task 5.3)
#
#  Requirements 20.3 - 20.6, ``design.md`` -> Math blocks. Two boundaries, and the
#  distinction between them is the whole design:
#
#    * WITHIN a series, an undefined bar is NaN and NaN propagates. That is what lets a
#      readiness gate tell "no value yet" apart from "a number", so a warming indicator
#      and a division by a zero denominator both read as *absent* rather than as zero.
#    * AT the action boundary, NaN stops. :func:`assert_execution_safe` is the last gate
#      an intent passes, and a non-finite or non-positive order field does not pass it.
#
#  ``±Inf`` never exists at either boundary. An infinity that reaches an order sizer is a
#  catastrophic order, and unlike NaN it survives comparison silently: ``inf > threshold``
#  is True, so an overflowed number *trades*. So an undefined result is NaN, always.
# ═══════════════════════════════════════════════════════════════════════════


def safe_math_apply(
    op: str,
    operands: Sequence[Any],
    node_id: Optional[str] = None,
    options: Optional[Mapping[str, Any]] = None,
    index: Optional[pd.Index] = None,
    issues: Optional[NodeIssueLog] = None,
) -> pd.Series:
    """Apply ``op`` under the numeric-safety contract and return a bar-aligned Series.

    The arithmetic itself is **not** written here. ``block_specs.safe_math_apply`` is the
    kernel form of ``design.md`` -> Math blocks and is what ``math_divide``, ``math_sqrt``
    and the other sixteen MATH kernels already call, so reimplementing the per-bar rules
    in the engine would create a second answer for "what does dividing by zero give
    you?". This is the engine-side seam around that one implementation, and it adds the
    two things a pure array kernel cannot do:

    * **bar alignment.** The kernels take and return bare arrays; the engine propagates
      Series on the market-data index, and a caller that wants a Series should not have to
      remember which index to rebuild it on.
    * **recording the condition against the node** (Requirement 20.4). The kernel reports
      what it firewalled through its ``issues`` sink; this hands those reports to
      :class:`NodeIssueLog` under ``node_id``.

    Postconditions
        The returned Series contains only finite values or NaN - never ``+Inf``, ``-Inf``
        or an overflowed value - and every condition that produced a NaN (division by a
        near-zero denominator, ``sqrt`` of a negative bar, ``log`` of a non-positive bar,
        an overflowing ``exp``) is recorded against ``node_id``.

    Raises
        :class:`DAGExecutionError` when the operands cannot be combined at all: mismatched
        lengths, misaligned timestamps, or an unknown operation. A refusal is not a NaN -
        a NaN says "this bar has no value", and a graph that cannot be evaluated must say
        so rather than produce an empty series that looks like warmup.
    """
    from backend_app.backend.strategy_dag import block_specs

    label = node_id or "unknown"
    resolved_index = index if index is not None else _operand_index(operands)
    _assert_operands_aligned(operands, label)

    records: List[Dict[str, Any]] = []
    try:
        raw = block_specs.safe_math_apply(
            op, operands, options=options, node_id=node_id, issues=records
        )
    except DAGExecutionError:
        raise
    except Exception as exc:  # noqa: BLE001 - the node and the op are both named
        raise DAGExecutionError(
            f"Node '{label}': math operation '{op}' refused its operands: {exc}"
        ) from exc
    finally:
        # Recorded even when the kernel went on to raise: the conditions it firewalled
        # before failing are still true of the node and are still worth showing.
        if issues is not None:
            issues.extend(label, records)

    return _kernel_output_to_series(raw, label, resolved_index)


def _operand_index(operands: Sequence[Any]) -> pd.Index:
    """The bar index the operands agree on, or a positional one when none carries it."""
    for operand in operands:
        if isinstance(operand, (pd.Series, pd.DataFrame)):
            return operand.index
    for operand in operands:
        if isinstance(operand, np.ndarray) and operand.ndim == 1:
            return pd.RangeIndex(len(operand))
    return pd.RangeIndex(0)


def _assert_operands_aligned(operands: Sequence[Any], node_id: str) -> None:
    """``design.md``'s ``ASSERT all_aligned_on_timestamp(operands)`` precondition.

    The kernels can only check equal length - by the time a series reaches one it is a
    bare array with no timestamps left to compare. The engine is the only layer holding
    the index, so it is the only layer that can tell "two series of 500 bars" from "two
    series of 500 bars *of the same 500 bars*". Combining the latter positionally is the
    same defect class as stacking two feature matrices by row position: a plausible
    number computed from bars that never coexisted.

    A scalar or a constant is exempt, having no index to disagree with.
    """
    indexes = [
        operand.index
        for operand in operands
        if isinstance(operand, (pd.Series, pd.DataFrame))
    ]
    for other in indexes[1:]:
        if len(other) != len(indexes[0]) or not other.equals(indexes[0]):
            raise DAGExecutionError(
                f"Node '{node_id}': operands are not timestamp-aligned. Arithmetic on "
                f"series from different bars would combine values that never coexisted."
            )


class ExecutionBlocked(_PlatformExecutionBlocked):
    """An intent was refused at the action boundary; no order was sent.

    Subclasses the platform's existing ``core.global_safety.ExecutionBlocked`` rather than
    introducing a second unrelated exception with the same name. Anything already written
    to treat that exception as "execution did not happen" treats this one the same way,
    which is the safe direction, and there is exactly one name in the codebase meaning
    "blocked before an order".

    ``node_id`` and ``code`` are attributes, not just message text, so the caller that
    catches this can record the incident (Requirement 20.5) without parsing a string.
    """

    def __init__(self, node_id: str, code: str, detail: str = "") -> None:
        self.node_id = str(node_id)
        self.code = str(code)
        self.detail = str(detail or "")
        message = f"Node '{self.node_id}' intent blocked: {self.code}"
        if self.detail:
            message = f"{message} ({self.detail})"
        super().__init__(message)


#: The order fields ``design.md`` names explicitly. Checked whether or not the intent
#: exposes anything else, so a payload shape this engine has not seen still gets the
#: named checks rather than only the discovered ones.
_ORDER_NUMERIC_FIELDS: Tuple[str, ...] = (
    "quantity",
    "price",
    "trigger_price",
    "notional",
)


def _intent_field_names(intent: Any) -> Tuple[str, ...]:
    """Every field name on ``intent``, the named ones first and in order.

    Requirement 20.5 is written about "a Trade_Intent numeric field", not about four
    particular ones, so every numeric field an intent exposes is checked for finiteness.
    The four named fields lead, so the code reported for a broken intent names the field
    the design names rather than whichever attribute happened to be enumerated first.
    """
    if isinstance(intent, Mapping):
        discovered = [str(key) for key in intent]
    else:
        container = getattr(intent, "__dict__", None)
        discovered = (
            [str(key) for key in container] if isinstance(container, Mapping) else []
        )
    ordered = list(_ORDER_NUMERIC_FIELDS)
    ordered.extend(name for name in discovered if name not in _ORDER_NUMERIC_FIELDS)
    return tuple(ordered)


def _intent_field(intent: Any, name: str) -> Any:
    if isinstance(intent, Mapping):
        return intent.get(name)
    return getattr(intent, name, None)


def _intent_numeric(value: Any) -> Optional[float]:
    """``value`` as a float, or ``None`` when it is not a numeric field at all.

    ``Decimal`` matters here: an exchange payload commonly carries one, ``Decimal("NaN")``
    is a real value, and it is not an instance of ``float`` or ``np.floating``. So the
    coercion is attempted rather than type-matched. A ``bool`` is deliberately *not*
    numeric - ``reduce_only=True`` is a flag, and reading it as the number 1 would be
    reading a different field.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def assert_execution_safe(
    intent: Any, node_id: str, issues: Optional[NodeIssueLog] = None
) -> None:
    """The last gate before an intent leaves the runtime. No order is sent past a refusal.

    Requirements 20.5 and 20.6, ``design.md`` -> Math blocks: a numeric field holding NaN
    or an infinity blocks the intent, and a quantity at or below zero blocks the intent.
    Both raise :class:`ExecutionBlocked` and record the incident.

    This **adds** a check; it replaces and relaxes nothing. ``execution_guard.py``,
    ``risk_engine.py`` and ``dag_risk_integration.py`` remain authoritative on the intent
    path and still re-validate quantity, notional, precision, balance, position and risk
    limits afterwards. What they cannot do is what this does: they see a number and
    decide whether it is *allowed*, while a NaN quantity is not a number at all - it
    compares False against every limit, so a guard written as "refuse if quantity >
    max_quantity" waves it straight through. That is the hole this closes, one layer
    earlier.

    Non-positivity is checked on ``quantity`` only, which is what the design and
    Requirement 20.6 specify. A zero or negative *price* is refused by the execution
    layer that owns price semantics per order type; inventing a rule here would risk
    blocking a market order whose price field is a legitimate placeholder, and a firewall
    that blocks valid orders gets switched off.

    Parameters
        ``intent`` - a mapping or an object; both are read the same way, because the
        emit path in task 8.4 is not written yet and this must not dictate its payload
        type.
        ``issues`` - optional :class:`NodeIssueLog`. The incident is logged either way.

    Raises
        :class:`ExecutionBlocked` naming the node and one of ``NON_FINITE_ORDER_FIELD`` /
        ``NON_POSITIVE_QUANTITY``.
    """

    def _blocked(code: str, detail: str) -> ExecutionBlocked:
        if issues is not None:
            issues.record(node_id=node_id, code=code, detail=detail)
        # Requirement 24.3's `dag.intents.blocked_non_finite`, and the observable task 9.2
        # alerts on. Only the non-finite arm increments it: `NON_POSITIVE_QUANTITY` is a
        # different fault with a different fix, and folding it in would make the alert fire
        # on an author's zero-size order. Recorded before the exception is built, and the
        # recorder is total, so it cannot be what stops the refusal from being raised.
        if code == "NON_FINITE_ORDER_FIELD":
            collector = _metrics()
            if collector is not None:
                collector.record_dag_intent_blocked_non_finite()
            # Requirement 24.4's alert, raised on the same arm and from the same place, so
            # the counter and the page cannot disagree about what happened. `notice_*` is
            # synchronous, total and non-blocking: it evaluates the condition against the
            # counter's watermark and hands delivery to the event loop, so the refusal below
            # is not waiting on a webhook. See `builder_alerts.raise_alerts`.
            alerts = _builder_alerts()
            if alerts is not None:
                try:
                    alerts.notice_intent_blocked_non_finite()
                except Exception:  # noqa: BLE001 - see the comment above
                    # `notice_*` is itself total, so this is the second of two independent
                    # guards rather than the only one. It is here because a refusal that
                    # stopped refusing would send the order, and that outcome must not
                    # depend on a decorator in another module staying applied.
                    logger.debug("The non-finite intent alert was not raised.", exc_info=True)
        logger.critical(
            "🚫 Node '%s': intent blocked before execution - %s (%s)",
            node_id,
            code,
            detail,
        )
        return ExecutionBlocked(node_id, code, detail)

    for name in _intent_field_names(intent):
        value = _intent_field(intent, name)
        if value is None:
            # A missing field is not a violation: an order type that has no trigger price
            # legitimately carries none, and the execution layer decides which fields its
            # order type requires.
            continue
        numeric = _intent_numeric(value)
        if numeric is None:
            if name in _ORDER_NUMERIC_FIELDS:
                # A named order field that is not a number cannot be checked for
                # finiteness, and "unable to check" is not "safe to send".
                raise _blocked(
                    "NON_FINITE_ORDER_FIELD",
                    f"{name}={value!r} is not a number",
                )
            continue
        if not np.isfinite(numeric):
            raise _blocked("NON_FINITE_ORDER_FIELD", f"{name}={numeric}")

    quantity = _intent_numeric(_intent_field(intent, "quantity"))
    if quantity is not None and quantity <= 0:
        raise _blocked("NON_POSITIVE_QUANTITY", f"quantity={quantity}")


# ══════════════════════════════════════════════════════════════════════════
# RUNTIME READINESS (task 8.4) - design.md -> "DAG runtime contract"
# ══════════════════════════════════════════════════════════════════════════
#
# THE FOUR STATES ARE FOUR STATES, NOT THREE AND A SHRUG
# -----------------------------------------------------
# ``NOT_READY``, ``AWAITING_MODEL``, ``WARMING`` and ``READY`` answer four different
# questions and an author fixes each of them differently:
#
#   NOT_READY       a required input port is unfed, or the node feeding it produced
#                   nothing. The author has wiring to do. More bars will not help.
#   AWAITING_MODEL  an ML_DL node has no active model version, or its artifact's
#                   checksum could not be verified against the recorded one. The author
#                   has training or a re-upload to do (Requirement 17.6).
#   WARMING         everything is wired and loaded; the window is simply not long enough
#                   yet for this node's composed warmup. Waiting fixes it
#                   (Requirements 20.10, 20.11).
#   READY           this node's value at the current bar is trustworthy.
#
# Collapsing any two of them would put a "wait for it" label on a graph that will never
# become ready, or a "fix your wiring" label on a deployment that has just started. Task
# 8.5 renders these on the canvas; 8.4 owns which one is true.
#
# ``AWAITING_MODEL`` and ``READY`` are **imported** from ``model_readiness`` (task 8.6)
# rather than re-spelled, and the checksum decision itself is
# ``model_readiness.model_ready`` - a pure, exception-free seam. Nothing about checksums
# or feature schemas is re-derived here.

#: A required input port is unfed, or its upstream produced nothing. Owned by this
#: module: task 8.4 owns the runtime state machine.
NOT_READY = "NOT_READY"

#: Wired and loaded, but the window is shorter than this node's composed warmup.
WARMING = "WARMING"

#: Every runtime state a node can hold, in increasing order of "closer to trading".
#: Published so task 8.5 has one vocabulary to render and no second list to drift from.
RUNTIME_STATES: Tuple[str, ...] = (NOT_READY, AWAITING_MODEL, WARMING, READY)

#: How much longer than its composed warmup a node's window has to be before the runtime
#: will execute it at all.
#:
#: The composed warmup says when this node's value *at the current bar* becomes
#: trustworthy - one bar past the warmup. It does **not** say when the whole *series* is
#: acceptable, and the runtime hands executors a series: ``core.pipeline_guard`` refuses one
#: that is more than half not-a-number, and it is right to, because on a full window that
#: means a broken indicator rather than a young deployment. A window of ``warmup + 1`` bars
#: is almost entirely warmup, so asking a block for it produces exactly that refusal.
#:
#: A headroom of 2 is what makes the leading NaNs a minority: with ``bars > 2 x warmup`` the
#: warmup region is under half the series. The number is not chosen here - it is the same
#: figure, for the same reason, as ``routers.strategy_operations.PREVIEW_WARMUP_HEADROOM``
#: ("warmup as a minority of the series"), and a test pins the two together so they cannot
#: drift.
#:
#: The consequence is that a node stays :data:`WARMING` slightly longer than its warmup
#: strictly requires. That is the safe direction and the only one available: the
#: alternatives are letting a young deployment raise on its own pipeline guard, or catching
#: that guard's refusal - which would be weakening a control that exists to catch a real
#: defect.
WARMUP_SERIES_HEADROOM = 2

#: The one input port every ACTION descriptor declares (``block_specs._signal_input``).
_ACTION_SIGNAL_PORT = "signal"

#: ACTION params that must hold a value before an order can be described. Both are
#: declared ``required`` with **no default** on every ACTION descriptor precisely because
#: "a silent default position size is a financial-safety defect" - so an absent one is a
#: node that cannot produce an order, not a node that produces a guessed one.
_ACTION_REQUIRED_PARAMS: Tuple[str, ...] = ("quantity_type", "quantity")


@dataclass
class PlanRuntimeState:
    """``design.md``'s ``state`` argument: what the runtime knows about each node.

    Mutated in place by :meth:`DAGEngine.execute_plan` through :func:`mark_node`, which is
    how a long-lived deployment keeps one state object across evaluations and how task 8.5
    reads node states without re-executing anything.

    ``model_versions`` is supplied by the caller - the deployment knows which model version
    row is active for each node; the engine does not and must not go looking. A node in
    ``plan.ml_nodes`` with no entry here is *not loaded*, which is the first half of
    Requirement 17.6's condition and is why the default is refusal rather than absence.
    """

    #: ``node_id -> one of RUNTIME_STATES``. A node absent from this map has not been
    #: reached yet, which is never treated as ready.
    node_states: Dict[str, str] = field(default_factory=dict)

    #: ``node_id -> the input port names (and ACTION params) it is waiting on``.
    missing_inputs: Dict[str, Tuple[str, ...]] = field(default_factory=dict)

    #: ``node_id -> composed warmup in bars``, from ``plan.plan_node_warmups`` - the
    #: compiler's own composition rule, asked of the plan the runtime actually holds.
    warmup_required: Dict[str, int] = field(default_factory=dict)

    #: Bars in the window the last evaluation saw. Task 8.5 renders
    #: "warming (n/m bars)" from this and :attr:`warmup_required`.
    bars_seen: int = 0

    #: ``node_id -> the active ``model_versions`` row``, supplied by the caller.
    model_versions: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    #: Artifact store override, threaded straight through to ``model_readiness``. ``None``
    #: uses the configured one.
    artifact_store: Any = None

    def reset_for(self, plan: Any, bars: int, warmups: Mapping[str, int]) -> None:
        """Start a fresh evaluation of ``plan`` over a window of ``bars`` bars.

        Every node starts :data:`NOT_READY` rather than absent, so a node the level walk
        never reaches - one whose level is missing, one downstream of a refusal - reads as
        not ready instead of reading as nothing.
        """
        self.bars_seen = int(bars)
        self.warmup_required = {
            node_id: int(warmups.get(node_id, 0)) for node_id in plan.node_index
        }
        self.node_states = {node_id: NOT_READY for node_id in plan.node_index}
        self.missing_inputs = {}

    def mark(
        self, node_id: str, state: str, missing: Sequence[str] = ()
    ) -> None:
        """Record ``node_id``'s state. Unknown labels are refused, not stored."""
        if state not in RUNTIME_STATES:
            raise DAGExecutionError(
                f"Node '{node_id}': '{state}' is not a runtime state; expected one of "
                f"{', '.join(RUNTIME_STATES)}"
            )
        self.node_states[str(node_id)] = state
        if missing:
            self.missing_inputs[str(node_id)] = tuple(str(name) for name in missing)
        else:
            self.missing_inputs.pop(str(node_id), None)

    def state_of(self, node_id: str) -> str:
        return self.node_states.get(str(node_id), NOT_READY)

    def is_ready(self, node_id: str) -> bool:
        return self.state_of(node_id) == READY

    def bars_needed(self, node_id: str) -> int:
        """The window length at which ``node_id`` can first be :data:`READY`.

        Two conditions, and the larger wins:

        * The window must **exceed** the composed warmup, not merely equal it. Warmup
          counts the leading bars that have to be *discarded*, so the first trustworthy bar
          sits at index ``warmup`` and reaching it takes ``warmup + 1`` bars. This is the
          platform's existing convention - ``strategy_operations`` reports
          ``warmup_exceeds_window`` as ``node_warmup >= len(frame)``.
        * The warmup region must be a minority of the series, or the existing pipeline
          guard refuses the series outright. See :data:`WARMUP_SERIES_HEADROOM`.

        A node with no warmup at all - a DATA node, a ``constant`` - needs one bar.
        """
        warmup = int(self.warmup_required.get(str(node_id), 0))
        return max(warmup + 1, WARMUP_SERIES_HEADROOM * warmup + 1)

    def bars_remaining(self, node_id: str) -> int:
        """Bars still to arrive before ``node_id`` can be :data:`READY`. Never negative."""
        return max(0, self.bars_needed(node_id) - int(self.bars_seen))

    def not_ready_reasons(self) -> Dict[str, int]:
        """``state -> node count``, for Requirement 21.3's not-ready counts by reason."""
        counts: Dict[str, int] = {state: 0 for state in RUNTIME_STATES}
        for state in self.node_states.values():
            counts[state] = counts.get(state, 0) + 1
        return counts

    def to_dict(self) -> Dict[str, Any]:
        """JSON-ready, for the runtime-state channel task 8.5 publishes."""
        return {
            "bars_seen": int(self.bars_seen),
            "nodes": {
                node_id: {
                    "state": state,
                    "warmup_bars": int(self.warmup_required.get(node_id, 0)),
                    "bars_needed": self.bars_needed(node_id),
                    "bars_remaining": self.bars_remaining(node_id),
                    "missing": list(self.missing_inputs.get(node_id, ())),
                }
                for node_id, state in sorted(self.node_states.items())
            },
            "counts": self.not_ready_reasons(),
        }


def mark_node(
    state: PlanRuntimeState,
    node_id: str,
    runtime_state: str,
    missing: Sequence[str] = (),
) -> None:
    """``design.md``'s ``mark_node(state, node_id, LABEL, missing)``, spelled as written."""
    state.mark(node_id, runtime_state, missing)


# ══════════════════════════════════════════════════════════════════════════
#  THE RUNTIME OPTIMISATIONS (task 9.4)
#
#  ``design.md`` -> Performance: "``execution_levels`` lets ``dag_engine_parallel``
#  evaluate independent branches concurrently; per-node memoisation keyed on
#  ``(node_id, params_hash, window_end)`` avoids recomputing an indicator shared by
#  several branches". Requirements 25.6 and 25.7.
#
#  Both are optimisations, which sets the bar they have to clear: an evaluation must
#  produce the *same* intents, the same node states, the same published port values and
#  the same trace, whether a level ran on one thread or four and whether a node's series
#  was computed or remembered. Neither is allowed to be a second opinion about a run.
#
#  How that is achieved, in one place so it can be checked:
#
#  * **Memoisation is scoped, not merely keyed.** ``(node_id, params_hash, window_end)``
#    is the design's key and is what :class:`NodeMemoKey` holds, but a window end is not
#    a window: two frames can end on the same bar and disagree about every earlier one.
#    So the cache is *opened* on a window whose entire content is fingerprinted
#    (:func:`window_identity`) and is emptied the moment that fingerprint changes, and
#    ``params_hash`` is a hash over the node's block, its canonical params, its runtime
#    reference **and the params_hash of every value feeding it** - a Merkle key, so an
#    edit anywhere upstream misses. A frame that cannot be fingerprinted disables the
#    cache rather than being cached under a guess. A remembered value that disagrees with
#    a recomputed one would be worse than no cache at all.
#  * **Concurrency is inside a level only, and publication stays ordered.** A level's
#    nodes are independent by construction - the compiler puts a node in level *k* only
#    when every predecessor is in a level below it, and :meth:`DAGEngine.execute_plan`
#    asserts that before evaluating one - so their executors cannot observe each other.
#    Input resolution, tracing, output publication, metrics and readiness marking all
#    stay on the calling thread in level order, so the only thing that moves is the
#    executor call. ML/DL and ACTION nodes are held back from the pool on purpose (see
#    :data:`SERIAL_EXECUTOR_TYPES`).
# ══════════════════════════════════════════════════════════════════════════


#: Engine executor keys that are evaluated on the calling thread even inside a level with
#: several runnable nodes.
#:
#: ML/DL: :class:`MLExecutor` reaches a shared model object, the feature validator and the
#: pipeline guard, and a model's ``predict`` is not documented as re-entrant. ACTION:
#: :class:`ActionExecutor` consults ``SafetyMonitor`` - the platform's kill switch - and
#: that is the last surface that should acquire a second caller as a side effect of an
#: optimisation. Requirement 25.4 caps a strategy at 4 model nodes, so what is given up is
#: bounded and known, and what is protected is a financial control.
SERIAL_EXECUTOR_TYPES: FrozenSet[str] = frozenset(
    {"ml", "dl", "action", "order_signal", "market_buy", "market_sell"}
)

#: Process-level default for level concurrency, and the kill switch for it.
#: ``STRATEGY_DAG_LEVEL_PARALLEL=0`` (or ``false`` / ``no`` / ``off``) makes every
#: evaluation single-threaded without a code change; :meth:`DAGEngine.execute_plan`'s
#: ``parallel`` argument overrides it per call, which is what the equivalence tests use.
LEVEL_PARALLELISM_DEFAULT: bool = str(
    _os.environ.get("STRATEGY_DAG_LEVEL_PARALLEL", "1")
).strip().lower() not in ("0", "false", "no", "off")


@dataclass(frozen=True)
class WindowIdentity:
    """What makes two market-data windows *the same* window.

    ``window_end`` is the third component of the design's memo key. ``bars`` and ``digest``
    are what make it safe: the digest covers every timestamp and every value in the frame,
    so a corrected historical bar, a different symbol, an extra column or a longer history
    behind the same closing bar all produce a different identity.
    """

    window_end: str
    bars: int
    digest: str


def window_identity(window: Any) -> Optional[WindowIdentity]:
    """Fingerprint ``window`` completely, or return ``None``.

    ``None`` means "this frame cannot be identified", and every caller treats that as
    "do not cache" rather than as "cache under what we could read". Returning a partial
    fingerprint would be the one failure mode a memo cache may not have.

    Deterministic across processes: ``hash_pandas_object`` is seeded with a fixed key, and
    the column names and dtypes are folded in as text.
    """
    if not isinstance(window, pd.DataFrame) or window.empty:
        return None
    try:
        row_hashes = pd.util.hash_pandas_object(window, index=True)
        digest = _hashlib.blake2b(digest_size=16)
        digest.update(
            _json.dumps(
                [[str(name), str(window[name].dtype)] for name in window.columns],
                separators=(",", ":"),
            ).encode("utf-8")
        )
        digest.update(np.ascontiguousarray(row_hashes.to_numpy(dtype="uint64")).tobytes())
        return WindowIdentity(
            window_end=str(window.index[-1]),
            bars=int(len(window.index)),
            digest=digest.hexdigest(),
        )
    except Exception as exc:  # noqa: BLE001 - an unhashable frame is simply not cacheable
        logger.debug("Window cannot be fingerprinted, memoisation disabled: %s", exc)
        return None


@dataclass(frozen=True)
class NodeMemoKey:
    """``design.md``'s memo key: ``(node_id, params_hash, window_end)``."""

    node_id: str
    params_hash: str
    window_end: str


@dataclass(frozen=True)
class MemoizedNode:
    """One node's remembered result: its primary value and every port it published."""

    primary: Any
    ports: Mapping[str, Any]


def _defensive_copy(value: Any) -> Any:
    """A copy of ``value`` when it is mutable, ``value`` itself when it is not.

    Pandas and NumPy objects are copied on the way *into* the cache and again on the way
    out, so neither the run that produced a series nor a later consumer of a remembered one
    can edit what a third evaluation will be handed. A ``FeatureMatrix`` is a frozen
    dataclass over read-only arrays and is passed through: copying it would cost real
    memory to defend against a mutation the type does not permit.
    """
    if isinstance(value, (pd.Series, pd.DataFrame)):
        return value.copy(deep=True)
    if isinstance(value, np.ndarray):
        return value.copy()
    return value


class NodeResultCache:
    """Memoised node results for one market-data window (Requirement 25.7).

    Owned by the caller, never global: a process-wide cache of market-derived series would
    outlive the deployment that asked for it and would hold one tenant's computed values in
    a structure another tenant's evaluation reads from. An instance belongs to whoever
    passes it to :meth:`DAGEngine.execute_plan`, and it holds at most ``max_entries``
    values, evicting oldest-first.

    Usage::

        cache = NodeResultCache()
        intents = engine.execute_plan(plan, window, cache=cache)   # every node computed
        intents = engine.execute_plan(plan, window, cache=cache)   # every node remembered

    A repeated evaluation of the same plan over the same window is the case the live loop
    and the node-preview endpoint both produce, and after an edit to one node the *other*
    nodes are still hits - their Merkle key did not move - while the edited node and
    everything downstream of it are misses.
    """

    def __init__(self, max_entries: int = 512) -> None:
        if int(max_entries) < 1:
            raise ValueError("NodeResultCache needs room for at least one entry")
        self.max_entries = int(max_entries)
        self.hits = 0
        self.misses = 0
        self.stores = 0
        self.evictions = 0
        self.window_changes = 0
        self._identity: Optional[WindowIdentity] = None
        self._entries: "Dict[NodeMemoKey, MemoizedNode]" = {}
        #: ``node_id -> params_hash`` for the window currently open. A node's key folds in
        #: the keys of the values feeding it, so this is how "upstream changed" propagates.
        self._node_hashes: Dict[str, str] = {}

    # -- window scope -----------------------------------------------------

    @property
    def identity(self) -> Optional[WindowIdentity]:
        """The window this cache currently holds results for, if any."""
        return self._identity

    def open_window(self, window: Any) -> bool:
        """Point the cache at ``window``. Returns whether it may be used at all.

        A window whose fingerprint differs from the open one empties the cache first: the
        entries describe bars that are no longer the bars being evaluated, and keeping them
        keyed on a bare ``window_end`` is exactly how a cache starts answering for data it
        never saw.
        """
        identity = window_identity(window)
        if identity is None:
            self.clear()
            return False
        if self._identity != identity:
            if self._identity is not None:
                self.window_changes += 1
            self.clear()
            self._identity = identity
        return True

    def clear(self) -> None:
        """Forget every remembered value. Counters are cumulative and survive."""
        self._entries.clear()
        self._node_hashes.clear()
        self._identity = None

    # -- keys -------------------------------------------------------------

    def key_for(
        self,
        node_id: str,
        *,
        block_id: str,
        params: Mapping[str, Any],
        runtime_ref: Any = None,
        inbound: Sequence[Tuple[str, str, str]] = (),
    ) -> Optional[NodeMemoKey]:
        """The memo key for ``node_id``, or ``None`` when it cannot be keyed completely.

        ``inbound`` is ``(input port, source node, source port)`` in the node's declared
        port order. Every source must already have been keyed in this window, because its
        key is part of this one; a source that was skipped, not ready or not cacheable
        therefore makes this node uncacheable too, which is the honest answer rather than a
        key that ignores where its inputs came from.
        """
        if self._identity is None:
            return None
        upstream: List[List[str]] = []
        for port_name, source, source_port in inbound:
            source_hash = self._node_hashes.get(str(source))
            if source_hash is None:
                return None
            upstream.append([str(port_name), str(source), str(source_port), source_hash])

        payload = _json.dumps(
            {
                "node": str(node_id),
                "block": str(block_id),
                "params": _canonical_params(params),
                "runtime": None if runtime_ref is None else str(runtime_ref),
                "inputs": upstream,
                "window": self._identity.digest,
                "bars": self._identity.bars,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        )
        params_hash = _hashlib.blake2b(
            payload.encode("utf-8"), digest_size=16
        ).hexdigest()
        self._node_hashes[str(node_id)] = params_hash
        return NodeMemoKey(
            node_id=str(node_id),
            params_hash=params_hash,
            window_end=self._identity.window_end,
        )

    # -- entries ----------------------------------------------------------

    def get(self, key: Optional[NodeMemoKey]) -> Optional[MemoizedNode]:
        """The remembered result for ``key``, copied, or ``None``."""
        if key is None:
            return None
        entry = self._entries.get(key)
        if entry is None:
            self.misses += 1
            return None
        self.hits += 1
        return MemoizedNode(
            primary=_defensive_copy(entry.primary),
            ports={name: _defensive_copy(value) for name, value in entry.ports.items()},
        )

    def put(
        self,
        key: Optional[NodeMemoKey],
        primary: Any,
        ports: Mapping[str, Any],
    ) -> None:
        """Remember ``primary`` and the port values ``key``'s node published."""
        if key is None:
            return
        while len(self._entries) >= self.max_entries:
            oldest = next(iter(self._entries))
            del self._entries[oldest]
            self.evictions += 1
        self._entries[key] = MemoizedNode(
            primary=_defensive_copy(primary),
            ports={
                str(name): _defensive_copy(value) for name, value in ports.items()
            },
        )
        self.stores += 1

    # -- observability ----------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """Hit/miss counters and the open window, for a caller that wants to log them."""
        looked_up = self.hits + self.misses
        return {
            "entries": len(self._entries),
            "max_entries": self.max_entries,
            "hits": self.hits,
            "misses": self.misses,
            "stores": self.stores,
            "evictions": self.evictions,
            "window_changes": self.window_changes,
            "hit_ratio": (self.hits / looked_up) if looked_up else 0.0,
            "window_end": None if self._identity is None else self._identity.window_end,
        }


def _canonical_params(params: Mapping[str, Any]) -> Any:
    """``params`` in the one canonical form the platform already hashes graphs by.

    Reuses ``schema.canonicalize`` - the function ``compute_dag_hash`` uses - so a params
    difference that changes a strategy's identity is a params difference that misses the
    cache. A second canonicalisation rule here could disagree with that one, and the
    disagreement would show up as a stale cached series rather than as a failing import.
    """
    try:
        from backend_app.backend.strategy_dag.schema import canonicalize

        return canonicalize(dict(params or {}))
    except Exception:  # noqa: BLE001 - fall back to a sorted plain form, never to nothing
        return {str(key): str(value) for key, value in sorted((params or {}).items())}


#: Workers for level-parallel evaluation - ``dag_engine_parallel``'s own sizing rule
#: (``cpu_count * 2``), spelled here because its default cannot be taken. See
#: :func:`_level_pool`.
LEVEL_POOL_WORKERS: int = max(2, (_os.cpu_count() or 2) * 2)

#: The one :class:`ParallelDAGEngine` this process drives, created on first use. One pool
#: per process rather than one per :class:`DAGEngine`: a live fleet holds an engine per
#: symbol, and a thread pool each would be a thread leak dressed as an optimisation.
_LEVEL_ENGINE: Any = None
_LEVEL_ENGINE_LOCK = _threading.Lock()


def _level_pool() -> Any:
    """``dag_engine_parallel``'s thread pool, or ``None``.

    The parallel engine is **REUSED AS-IS** per ``design.md``'s component disposition: this
    asks it for the pool it already owns and drives that pool from the plan's
    ``execution_levels``, rather than calling its ``execute_dag_parallel``. That entry point
    builds its own levels from a loose node/edge payload and dispatches through a four-key
    executor table (``indicator`` / ``ml`` / ``logic`` / ``action``) with inputs addressed by
    upstream node id; a canonical DATA, MATH, FEATURE_ENGINEERING or port-addressed LOGIC
    node would fall through it to a zero series. Using it to execute canonical nodes would
    make the parallel path disagree with ``execute_plan`` about the numbers, which is the one
    thing an optimisation may not do. So the scheduler is shared and the node semantics stay
    the engine's own.

    ``max_workers`` is passed explicitly, and that is not a preference: as of this task
    ``ParallelDAGEngine.__init__``'s default is ``threading.cpu_count() * 2``, and
    ``threading`` has no ``cpu_count`` - the name is ``os.cpu_count`` - so constructing it
    without an explicit count raises ``AttributeError``. The module is reused as-is here and
    is **not** edited to fix that (its two FastAPI endpoints are the only other callers and
    neither is mounted in ``main.py``); the count is supplied instead, using the same
    ``cpu_count * 2`` rule the module intended.

    ``None`` when a pool cannot be obtained, and the caller then runs the level inline: an
    unavailable thread pool is a slower evaluation, never a failed one.
    """
    global _LEVEL_ENGINE

    if _LEVEL_ENGINE is None:
        with _LEVEL_ENGINE_LOCK:
            if _LEVEL_ENGINE is None:
                try:
                    from backend_app.backend.dag_engine_parallel import \
                        ParallelDAGEngine

                    _LEVEL_ENGINE = ParallelDAGEngine(
                        max_workers=LEVEL_POOL_WORKERS
                    )
                except Exception as exc:  # noqa: BLE001 - never load-bearing
                    logger.warning(
                        "Level-parallel pool unavailable, evaluating inline: %s", exc
                    )
                    return None
    return getattr(_LEVEL_ENGINE, "thread_pool", None)


def _concurrency_eligible(node: Mapping[str, Any]) -> bool:
    """Whether ``node`` may be evaluated off the calling thread. See
    :data:`SERIAL_EXECUTOR_TYPES`."""
    return str(node.get("type", "")) not in SERIAL_EXECUTOR_TYPES


@dataclass
class _NodeOutcome:
    """What one executor call produced, before anything shared has been touched."""

    produced: Any = None
    elapsed_ms: float = 0.0
    error: Optional[BaseException] = None
    executor_missing: bool = False


@dataclass
class _LevelNode:
    """One node of one execution level, carried across the three phases of that level."""

    node_id: str
    node: Dict[str, Any]
    inputs: Any
    descriptor: Any
    memo_key: Optional[NodeMemoKey] = None
    cached: Optional[MemoizedNode] = None
    outcome: Optional[_NodeOutcome] = None


@dataclass(frozen=True)
class TradeIntent:
    """A proposed order an ACTION node produced. **Not** an order, and not a permission.

    Requirement 20.8 keeps ``execution_guard``, ``risk_engine``,
    ``dag_risk_integration`` and the idempotency controls authoritative: this object is
    what they are handed. :meth:`DAGEngine.execute_plan` returns intents and sends
    nothing, so the one path to a venue is unchanged and no control is duplicated,
    reordered or relaxed.

    Every field an author can type free text into stays inside :attr:`params` rather than
    being lifted to a top level attribute, because ``assert_execution_safe`` reads every
    field an intent exposes and a ``client_tag`` of ``"nan"`` is a label, not a broken
    number.

    ``side`` is ``None`` for the exit blocks (``action_close_position``, the stop and
    take-profit variants): their side is the inverse of the open position and is resolved
    by the execution layer, which holds the position. Guessing one here would be
    guessing a direction.

    ``price`` / ``trigger_price`` / ``limit_price`` are carried **as the author declared
    them**, together with their ``*_mode`` params inside :attr:`params`. Nothing is
    resolved against a reference price here: an ``offset_bps`` mode needs market context
    the runtime does not own, and inventing a level would be inventing the order.
    """

    node_id: str
    triggered: bool
    bar: str
    signal: float
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    side: Optional[str] = None
    order_type: Optional[str] = None
    order_intent: Optional[str] = None
    reduce_only: bool = False
    quantity_type: Optional[str] = None
    quantity: Any = None
    price: Any = None
    trigger_price: Any = None
    limit_price: Any = None
    params: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "triggered": bool(self.triggered),
            "bar": self.bar,
            "signal": float(self.signal),
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "side": self.side,
            "order_type": self.order_type,
            "order_intent": self.order_intent,
            "reduce_only": bool(self.reduce_only),
            "quantity_type": self.quantity_type,
            "quantity": self.quantity,
            "price": self.price,
            "trigger_price": self.trigger_price,
            "limit_price": self.limit_price,
            "params": dict(self.params),
        }


def _last_scalar(value: Any) -> Optional[float]:
    """``value``'s last bar as a float, or ``None`` when there is no number there.

    ``None`` means "this is not a number at the current bar" - an empty series, a
    non-numeric payload, a ``FeatureMatrix``. It is deliberately distinct from ``NaN``,
    which means "the block computed no value for this bar"; both refuse to trigger, but
    only one of them is a wiring problem.
    """
    if isinstance(value, pd.Series):
        if value.empty:
            return None
        value = value.iloc[-1]
    elif isinstance(value, np.ndarray):
        if value.size == 0:
            return None
        value = value.reshape(-1)[-1]
    elif isinstance(value, (list, tuple)):
        if not value:
            return None
        value = value[-1]
    if isinstance(value, bool) or isinstance(value, np.bool_):
        return 1.0 if value else 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_intent(
    node: Any,
    plan: Any,
    values: Mapping[Tuple[str, str], Any],
    primaries: Mapping[str, Any],
    window: pd.DataFrame,
    *,
    descriptor: Any = None,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
) -> TradeIntent:
    """``design.md``'s ``build_intent(plan.node_index[action_id], values, window)``.

    Reads the ACTION node's ``signal`` **input** port, not its output: an ACTION
    descriptor declares no output ports at all (it is ``TERMINAL``), and the engine's
    signed series for an exit block is all zeros by design because
    ``plan_to_engine_graph`` refuses to guess a side for it. So the trigger has to come
    from the condition the author wired in, and it fires above
    :data:`ACTION_TRIGGER_THRESHOLD` - the same constant :class:`ActionExecutor` compares
    against, so a live intent and a backtested bar cannot disagree (Requirement 22.4).

    ``triggered`` is ``False`` - never an exception - for every shape of "there is no
    condition to read here": an unfed port, a port carrying more than one edge (an ACTION
    ``signal`` port is not variadic, so more than one edge is a plan that should not
    exist and picking one of them would be picking an author's operand for them), an
    upstream that produced nothing, and a ``NaN`` at the current bar. Silence is the
    correct answer to all four on the intent path.

    Postconditions
        The returned intent describes the author's declared order and nothing else. No
        price is resolved, no side is inferred and no quantity is defaulted.
    """
    node_id = str(getattr(node, "id", "") or "")
    params: Dict[str, Any] = dict(getattr(node, "params", {}) or {})
    metadata: Mapping[str, Any] = (
        getattr(descriptor, "metadata", {}) or {} if descriptor is not None else {}
    )

    bar_label = ""
    if window is not None and len(window.index):
        bar_label = str(window.index[-1])

    # The signal port, through the plural accessor. `signal` is not variadic, so exactly
    # one edge is the only shape that can be read - which is what `inbound_edge` would
    # enforce by raising. Reading it as a tuple and refusing anything other than a single
    # edge keeps that refusal without putting a raise on the intent path.
    edges = plan.inbound_edges(node_id, _ACTION_SIGNAL_PORT)
    signal: Optional[float] = None
    if len(edges) == 1:
        edge = edges[0]
        key = (edge.source, edge.source_port)
        if key in values:
            signal = _last_scalar(values[key])
        elif edge.source in primaries:
            signal = _last_scalar(primaries[edge.source])
    elif len(edges) > 1:
        logger.critical(
            "🚫 ACTION node '%s': input port '%s' is fed by %d edges; it is not a "
            "variadic port, so no single condition can be read and no intent is built",
            node_id,
            _ACTION_SIGNAL_PORT,
            len(edges),
        )

    # `NaN > threshold` is False in both numpy and Python, so a warmup bar cannot trigger.
    triggered = signal is not None and bool(signal > ACTION_TRIGGER_THRESHOLD)

    side = metadata.get("side")
    return TradeIntent(
        node_id=node_id,
        triggered=triggered,
        bar=bar_label,
        signal=0.0 if signal is None else float(signal),
        symbol=symbol,
        timeframe=timeframe,
        side=str(side) if side else None,
        order_type=(
            str(metadata["order_type"]) if metadata.get("order_type") else None
        ),
        order_intent=str(metadata["intent"]) if metadata.get("intent") else None,
        # A forced reduce-only is not negotiable by a param: an exit block may only
        # shrink a position, so the descriptor's flag wins over anything in params.
        reduce_only=bool(metadata.get("reduce_only_forced"))
        or bool(params.get("reduce_only")),
        quantity_type=(
            str(params["quantity_type"]) if params.get("quantity_type") else None
        ),
        quantity=params.get("quantity"),
        price=params.get("price"),
        trigger_price=params.get("trigger_price"),
        limit_price=params.get("limit_price"),
        params=params,
    )


class DataFeedExecutor(NodeExecutor):
    """Publish a canonical DATA node's output ports from the market-data frame.

    ``ohlcv_feed`` declares six output ports - ``frame``, ``open``, ``high``, ``low``,
    ``close``, ``volume`` - and the legacy pass-through returned ``close`` for all of
    them. So an author's ``ohlcv_feed.volume -> feat_volume.volume`` edge delivered
    *close prices as volume*: a plausible series, computed from the wrong column, with
    nothing raised. Same for ``high``/``low`` into ATR-style feature blocks.

    ``primary`` stays ``close``, so ``node_results[data_node]`` is byte-identical to
    what it has always been and the golden-plan contract is untouched. The other five
    ports are additive.

    A node with no resolvable descriptor, or one whose declared ports are not columns
    of this frame, is handed to ``fallback`` completely unchanged.
    """

    #: Output port name -> market-data column. ``frame`` is the whole frame, so it is
    #: absent from this map and handled separately.
    _COLUMN_PORTS = ("open", "high", "low", "close", "volume", "last", "bid", "ask")

    def __init__(self, fallback: Any):
        self.fallback = fallback

    def execute(
        self, node: Dict, inputs: Dict[str, Any], market_data: pd.DataFrame
    ) -> Any:
        declared = _declared_output_ports(node)
        if not declared or market_data is None or market_data.empty:
            return self.fallback.execute(node, inputs, market_data)

        by_port: Dict[str, Any] = {}
        for port_name in declared:
            if port_name == "frame":
                by_port["frame"] = market_data
            elif port_name in self._COLUMN_PORTS and port_name in market_data.columns:
                by_port[port_name] = market_data[port_name]

        # Legacy primary: whatever the pass-through would have returned. Resolved by
        # asking it, so the two can never disagree.
        legacy = self.fallback.execute(node, inputs, market_data)
        if not by_port:
            return legacy

        primary = "close" if "close" in by_port else declared[0]
        by_port.setdefault(primary, legacy)
        if primary == "close" and "close" in by_port:
            # Keep the exact object the pass-through produced for the primary port.
            by_port["close"] = legacy
        return NodeOutputs(by_port=by_port, primary=primary)


# ---------------------------------------------------------------------------
# Canonical FEATURE_ENGINEERING nodes (task 5.2)
# ---------------------------------------------------------------------------

#: Feature blocks that produce one column per entry of a list-valued param. The column
#: name carries the entry, so ``lags=[1, 2, 3]`` becomes ``lag_1``, ``lag_2``, ``lag_3``
#: and a preview (Requirement 24.8) shows the author which lag is which.
#:
#: This is NAMING, not computation. Every formula stays in ``FeatureEngine``; nothing
#: here decides what a column contains, only what it is called.
_FEATURE_COLUMNS_PER_PARAM: Dict[str, Tuple[str, str]] = {
    # block_id: (param holding the list, singular column stem)
    "feat_lag": ("lags", "lag"),
    "feat_returns": ("periods", "return"),
    "feat_momentum": ("windows", "momentum"),
}

#: Feature blocks whose runtime returns a fixed tuple of series, one per named
#: quantity, in the order the runtime returns them.
_FEATURE_COLUMNS_FIXED: Dict[str, Tuple[str, ...]] = {
    "feat_volume": ("volume_mean", "volume_std", "volume_ratio"),
}

#: Params whose value is worth carrying into the column name, so two nodes of the same
#: block with different settings do not collide when ``feat_concat`` merges them.
_FEATURE_NAME_PARAMS: Tuple[str, ...] = ("transform", "method", "window")

#: How a single ``OHLCV_FRAME`` input port expands into the runtime's operands.
#: ``__index__`` is the frame's timestamp index, which is what ``compute_time_features``
#: reads. Stated declaratively rather than as an ``if block_id ==`` chain so a new
#: frame-consuming feature block is a table entry.
_FEATURE_FRAME_OPERANDS: Dict[str, Tuple[str, ...]] = {
    "feat_price_transform": ("open", "high", "low", "close"),
    "feat_time": ("__index__",),
}


def _feature_column_names(block_id: str, params: Mapping[str, Any], produced: int) -> List[str]:
    """Column names for one feature node's output.

    Deterministic and derived only from the block id and the author's params, so the
    same node always names its columns the same way - which is what lets a trained
    model's recorded ``feature_schema`` still match the graph that produced it.
    """
    stem = block_id[5:] if block_id.startswith("feat_") else block_id

    per_param = _FEATURE_COLUMNS_PER_PARAM.get(block_id)
    if per_param is not None:
        key, singular = per_param
        entries = params.get(key) or []
        names = [f"{singular}_{entry}" for entry in entries]
        if len(names) == produced:
            return names

    fixed = _FEATURE_COLUMNS_FIXED.get(block_id)
    if fixed is not None and len(fixed) == produced:
        window = params.get("window")
        return [
            f"{name}_{window}" if window is not None else name for name in fixed
        ]

    suffixes = [
        str(params[key])
        for key in _FEATURE_NAME_PARAMS
        if params.get(key) is not None
    ]
    base = "_".join([stem, *suffixes]) if suffixes else stem
    if produced == 1:
        return [base]
    return [f"{base}_{position}" for position in range(produced)]


class FeatureExecutor(NodeExecutor):
    """Execute a FEATURE_ENGINEERING node through the runtime its descriptor publishes.

    ``design.md`` -> Feature engineering: ``FeatureEngine`` "already implements the hard
    parts leak-safely; the work is exposing each capability as an individually
    addressable, registry-declared block". So this executor computes nothing. It
    resolves ``FEATURE_SPECS``' ``runtime_ref`` to the callable the registry already
    asserted is callable at assembly, feeds it the values on its declared input ports,
    and wraps the result in the one ``FeatureMatrix`` contract
    (``strategy_dag/feature_matrix.py``). No feature formula, and no second alignment
    path, exists here.

    Inputs are taken **by port name**, never by edge order:

    * ``series`` / ``volume`` (``SCALAR_SERIES``) -> a 1-D float array
    * ``frame`` (``OHLCV_FRAME``) -> expanded per :data:`_FEATURE_FRAME_OPERANDS`
    * ``matrix`` (``FEATURE_MATRIX``) -> the upstream matrix, or, on ``feat_concat``'s
      variadic port, every matrix on it

    ``feat_concat`` combines through ``FeatureEngine.concat_feature_matrices``, which
    delegates to ``concat_matrices`` - the only join, timestamp-addressed, with no
    positional mode to opt into (Requirement 18.13).

    Warmup is the larger of the descriptor's declared ``warmup_fn`` and the leading
    NaNs the run actually produced, so a block whose declared lookback understates
    reality cannot hand a model an untrustworthy row.

    Provenance records this node id against every column it produced
    (Requirement 18.12), which is what lets a leakage finding or an inspector trace
    name the block a feature came from.

    A legacy ``{"type": "feature"}`` dict carries no ``block_id``, so it has no
    descriptor and is handed to ``fallback`` unchanged - the pass-through it reached
    before this task.
    """

    def __init__(self, fallback: Any):
        self.fallback = fallback

    def execute(
        self, node: Dict, inputs: Dict[str, Any], market_data: pd.DataFrame
    ) -> Any:
        descriptor = descriptor_for(node)
        block_id = node.get("block_id")
        if descriptor is None or not isinstance(block_id, str):
            return self.fallback.execute(node, inputs, market_data)

        node_id = node.get("id", "unknown")
        params = descriptor.resolved_params(node.get("params") or {})

        try:
            runtime = descriptor.resolve_runtime()
        except Exception as exc:  # noqa: BLE001 - the node and the ref are both named
            raise DAGExecutionError(
                f"Node '{node_id}': feature block '{block_id}' declares runtime_ref "
                f"'{getattr(descriptor, 'runtime_ref', '?')}', which does not resolve "
                f"to a callable: {exc}"
            ) from exc

        operands = self._operands(descriptor, block_id, node_id, inputs, market_data)
        kwargs = descriptor.runtime_kwargs(node.get("params") or {})

        try:
            raw = runtime(*operands, **kwargs)
        except DAGExecutionError:
            raise
        except Exception as exc:  # noqa: BLE001 - a runtime refusal is a node failure
            raise DAGExecutionError(
                f"Node '{node_id}': feature block '{block_id}' refused its inputs: {exc}"
            ) from exc

        matrix = self._as_matrix(raw, node_id, block_id, params, descriptor, market_data)
        output_port = _declared_output_ports(node)
        primary = output_port[0] if output_port else "matrix"
        return NodeOutputs(by_port={primary: matrix}, primary=primary)

    # -- input binding ----------------------------------------------------

    def _operands(
        self,
        descriptor: Any,
        block_id: str,
        node_id: str,
        inputs: Dict[str, Any],
        market_data: pd.DataFrame,
    ) -> List[Any]:
        """The runtime's positional operands, bound from the declared input ports."""
        from backend_app.backend.strategy_dag.feature_matrix import FeatureMatrix

        port_view = inputs if isinstance(inputs, PortInputs) else PortInputs(inputs)
        operands: List[Any] = []

        for port in getattr(descriptor, "inputs", ()) or ():
            port_type = getattr(getattr(port, "type", None), "value", None) or str(
                getattr(port, "type", "")
            )
            values = port_view.port_values(port.name)

            if port_type == "FEATURE_MATRIX":
                if getattr(port, "variadic", False):
                    matrices = [value for value in values if isinstance(value, FeatureMatrix)]
                    if len(matrices) != len(values):
                        raise DAGExecutionError(
                            f"Node '{node_id}': port '{port.name}' received a payload "
                            f"that is not a FeatureMatrix; only a matrix carrying its "
                            f"own timestamp index can be combined."
                        )
                    if not matrices:
                        raise DAGExecutionError(
                            f"Node '{node_id}': required port '{port.name}' has no "
                            f"connection."
                        )
                    operands.append(matrices)
                    continue
                single = values[0] if values else None
                if not isinstance(single, FeatureMatrix):
                    raise DAGExecutionError(
                        f"Node '{node_id}': port '{port.name}' expects a FEATURE_MATRIX "
                        f"and received {type(single).__name__}."
                    )
                operands.append(single)
                continue

            if port_type == "OHLCV_FRAME":
                frame = self._frame_for(values, market_data, node_id, port.name)
                for operand in _FEATURE_FRAME_OPERANDS.get(block_id, ("close",)):
                    if operand == "__index__":
                        operands.append(np.asarray(frame.index))
                    elif operand in frame.columns:
                        operands.append(np.asarray(frame[operand], dtype=float))
                    else:
                        raise DAGExecutionError(
                            f"Node '{node_id}': the frame on port '{port.name}' has no "
                            f"'{operand}' column; it holds {list(frame.columns)}."
                        )
                continue

            # SCALAR_SERIES / PRICE_SERIES / BOOLEAN_SERIES / PREDICTION
            if not values:
                if getattr(port, "required", True):
                    raise DAGExecutionError(
                        f"Node '{node_id}': required port '{port.name}' has no "
                        f"connection."
                    )
                continue
            if len(values) > 1:
                raise DAGExecutionError(
                    f"Node '{node_id}': port '{port.name}' accepts one connection but "
                    f"holds {len(values)}."
                )
            operands.append(np.asarray(values[0], dtype=float).reshape(-1))

        return operands

    @staticmethod
    def _frame_for(
        values: Tuple[Any, ...],
        market_data: pd.DataFrame,
        node_id: str,
        port_name: str,
    ) -> pd.DataFrame:
        """The candle frame on an ``OHLCV_FRAME`` port.

        A canonical DATA node publishes the frame on its ``frame`` port
        (:class:`DataFeedExecutor`), which is what arrives here. A legacy payload
        publishes only a close series, in which case the frame the run was driven
        with is the honest answer - it is the same candles, not a substitute.
        """
        for value in values:
            if isinstance(value, pd.DataFrame):
                return value
        if market_data is not None and not market_data.empty:
            return market_data
        raise DAGExecutionError(
            f"Node '{node_id}': port '{port_name}' expects a candle frame and no "
            f"market data is available."
        )

    # -- output shaping ---------------------------------------------------

    def _as_matrix(
        self,
        raw: Any,
        node_id: str,
        block_id: str,
        params: Mapping[str, Any],
        descriptor: Any,
        market_data: pd.DataFrame,
    ) -> Any:
        """The runtime's return value as a :class:`FeatureMatrix`.

        Handles the four shapes ``FEATURE_SPECS``' runtimes actually return: an already
        built matrix (``feat_concat``, ``feat_select``, ``feat_standardize``), a 1-D
        array, a 2-D array of columns, and a ``(values, column_names)`` pair
        (``compute_time_features``) or a tuple of series (``compute_volume_features``).
        """
        from backend_app.backend.strategy_dag.feature_matrix import (
            FeatureMatrix, build_feature_matrix)

        if isinstance(raw, FeatureMatrix):
            return self._floor_warmup(raw, descriptor, params)

        index = np.asarray(market_data.index)
        columns: Optional[List[str]] = None
        series: List[np.ndarray]

        if (
            isinstance(raw, tuple)
            and len(raw) == 2
            and isinstance(raw[1], (list, tuple))
            and all(isinstance(name, str) for name in raw[1])
        ):
            # (values, column_names) - the runtime named its own columns, so they are
            # used verbatim rather than renamed here.
            values = np.asarray(raw[0], dtype=float)
            columns = [str(name) for name in raw[1]]
            series = [values[:, position] for position in range(values.shape[1])]
        elif isinstance(raw, tuple):
            series = [np.asarray(item, dtype=float).reshape(-1) for item in raw]
        else:
            values = np.asarray(raw, dtype=float)
            if values.ndim == 1:
                series = [values]
            elif values.ndim == 2:
                series = [values[:, position] for position in range(values.shape[1])]
            else:
                raise DAGExecutionError(
                    f"Node '{node_id}': feature block '{block_id}' returned a "
                    f"{values.ndim}-D array; a feature block produces columns."
                )

        for position, column in enumerate(series):
            if column.size != index.size:
                raise DAGExecutionError(
                    f"Node '{node_id}': feature block '{block_id}' produced "
                    f"{column.size} values in column {position} for {index.size} bars "
                    f"of market data."
                )

        if columns is None:
            columns = _feature_column_names(block_id, params, len(series))

        matrix = build_feature_matrix(
            index=index,
            columns=columns,
            series=series,
            node_id=node_id,
        )
        return self._floor_warmup(matrix, descriptor, params)

    @staticmethod
    def _floor_warmup(matrix: Any, descriptor: Any, params: Mapping[str, Any]) -> Any:
        """Raise every column's warmup to at least the descriptor's declared lookback.

        ``build_feature_matrix`` derives warmup from the leading NaNs actually present,
        which catches a block whose declared lookback understates reality. The reverse
        also has to hold: a rolling z-score over 20 bars is not trustworthy at row 0
        merely because that row happened to come out non-NaN. Taking the larger of the
        two is the only answer that is safe in both directions.
        """
        from backend_app.backend.strategy_dag.feature_matrix import FeatureMatrix

        try:
            declared = int(descriptor.warmup(params))
        except Exception:  # noqa: BLE001 - a warmup_fn refusal must not lose the matrix
            return matrix
        if declared <= 0 or declared <= matrix.warmup_offset:
            return matrix

        return FeatureMatrix(
            index=matrix.index,
            columns=list(matrix.columns),
            values=matrix.values,
            column_warmup={
                name: max(declared, int(matrix.column_warmup.get(name, 0)))
                for name in matrix.columns
            },
            provenance=dict(matrix.provenance),
        )


class BlockKernelExecutor(NodeExecutor):
    """Run a canonical MATH or LOGIC node through the kernel its descriptor publishes.

    ``design.md`` (Runtime references) states the contract this implements: "``MathExecutor``
    and ``LogicExecutor`` delegate to them rather than reimplementing arithmetic, exactly as
    ``IndicatorExecutor`` delegates to ``indicators_backend``". No arithmetic, comparison or
    numeric firewall is written here - the kernels in ``block_specs`` own all of it.

    A node reaches the kernel path only when ``strategy_compiler.plan_to_engine_graph``
    adapted it: it carries ``runtime_ref``, ``runtime_params`` and ``input_order``. A LEGACY
    loose dict carries none of those and is handed to ``fallback`` completely unchanged, so
    every existing caller that feeds this engine hand-built ``{"type": "math"}`` /
    ``{"type": "logic", "operator": "GT"}`` dicts keeps its previous behaviour.

    OPERAND BINDING IS BY INPUT PORT NAME (task 5.3)
    -----------------------------------------------
    This is the pattern task 5.4's LOGIC re-point and the re-pointed
    ``IndicatorExecutor`` follow; :meth:`_bind_operands` is the single place it lives, so
    neither has to restate it.

    Operands are read off the **declared input ports** through
    :meth:`PortInputs.port_values`, walking ``descriptor.inputs`` in declaration order. A
    variadic port contributes all N of its connections, in edge order. Two consequences
    that positional resolution could not give:

    * A port that is unfed is *named*: ``divide`` missing its ``denominator`` reports
      that port, instead of handing the kernel one argument and surfacing as a
      ``TypeError`` about a missing positional parameter.
    * ``high -> subtract.a`` with ``low -> subtract.b`` reaches the kernel as
      ``(high, low)`` even though both edges come from the same upstream node. Read
      positionally through the id-keyed view those two edges collapse to one entry and
      the block computes ``low - low``: a plausible zero-ish series, from the wrong data,
      with nothing raised.

    Declared port order is also the kernel's positional-parameter order
    (``logic_gt(left, right)``, ``math_divide(numerator, denominator)``,
    ``logic_between(value, lower, upper)``), which is asserted for every MATH and LOGIC
    descriptor by ``tests/test_math_executor_firewall.py`` rather than assumed here.
    ``input_order`` remains the fallback for a payload with no resolvable descriptor or no
    port-addressed edges - a hand-built dict carrying a ``runtime_ref``, or a
    registry-starved process - so that path keeps its previous behaviour exactly.

    ``validate_node_inputs`` is deliberately not applied on this path. It back-fills a NaN
    warmup region (``value.bfill()``), which reads later bars to fill earlier ones; the
    kernels already define the correct answer for an undefined bar ("an undefined bar never
    signals"), so the fill is both unnecessary and backwards in time here.

    Numeric conditions the kernel firewalled (``DIVISION_BY_ZERO``, ``NEGATIVE_ROOT``,
    ``NON_POSITIVE_LOG``, ``MODULO_BY_ZERO``) are collected through the kernel's own
    ``issues`` sink and recorded against this node in :class:`NodeIssueLog`
    (Requirement 20.4), so a NaN bar can be explained rather than merely observed.
    """

    def __init__(self, fallback: Any, issues: Optional[NodeIssueLog] = None):
        self.fallback = fallback
        #: Where recorded conditions go. :class:`DAGEngine` passes its own log so the
        #: engine and its executors share one; a standalone executor gets a private one
        #: rather than ``None``, so recording never needs a null check.
        self.issues = issues if issues is not None else NodeIssueLog()

    def execute(self, node: Dict, inputs: Dict[str, Any], market_data: pd.DataFrame) -> pd.Series:
        runtime_ref = node.get("runtime_ref")
        if not isinstance(runtime_ref, str) or not runtime_ref.startswith(
            _BLOCK_KERNEL_OWNER
        ):
            return self.fallback.execute(node, inputs, market_data)

        node_id = node.get("id", "unknown")
        if inputs is None:
            raise DAGExecutionError(
                f"Node '{node_id}': Input is None. "
                f"Node received no input data from upstream nodes."
            )

        try:
            kernel, accepted = _resolve_block_kernel(runtime_ref)
        except Exception as exc:  # noqa: BLE001 - the node is named, the ref is named
            raise DAGExecutionError(
                f"Node '{node_id}': runtime_ref '{runtime_ref}' does not resolve to a "
                f"kernel: {exc}"
            ) from exc

        operands = self._bind_operands(node, node_id, inputs)
        _assert_operands_aligned(operands, node_id)

        kwargs = {
            key: value
            for key, value in (node.get("runtime_params") or {}).items()
            if key in accepted
        }
        if "node_id" in accepted:
            kwargs["node_id"] = node_id
        records: List[Dict[str, Any]] = []
        if "issues" in accepted:
            kwargs["issues"] = records

        try:
            raw = kernel(*operands, **kwargs)
        except DAGExecutionError:
            raise
        except Exception as exc:  # noqa: BLE001 - a kernel refusal is a node-level failure
            raise DAGExecutionError(
                f"Node '{node_id}': kernel '{runtime_ref}' refused its inputs: {exc}"
            ) from exc
        finally:
            # Recorded even when the kernel raised afterwards: a condition it firewalled
            # on the way to failing is still true of this node.
            self.issues.extend(node_id, records)

        return _kernel_output_to_series(raw, node_id, market_data.index)

    # -- input binding ----------------------------------------------------

    def _bind_operands(
        self, node: Dict, node_id: str, inputs: Dict[str, Any]
    ) -> List[Any]:
        """The kernel's positional operands, bound from the declared input ports.

        Postconditions
            One operand per connection, ordered by declared input port and, within a
            variadic port, by edge order - which is exactly the order ``input_order``
            records, so the two addressing modes describe the same operand list rather
            than two competing ones.

        Raises
            :class:`DAGExecutionError` naming the node and the port for a required port
            with no connection, a non-variadic port fed more than once, or a connection
            whose upstream node produced ``None``.
        """
        descriptor = descriptor_for(node)
        declared = tuple(getattr(descriptor, "inputs", ()) or ())
        port_view = inputs if isinstance(inputs, PortInputs) else PortInputs(inputs)

        if not declared or not port_view.ports():
            return self._bind_positionally(node, node_id, inputs)

        operands: List[Any] = []
        for port in declared:
            values = port_view.port_values(port.name)
            if not values:
                if getattr(port, "required", True):
                    raise DAGExecutionError(
                        f"Node '{node_id}': required port '{port.name}' has no "
                        f"connection."
                    )
                continue
            if len(values) > 1 and not getattr(port, "variadic", False):
                raise DAGExecutionError(
                    f"Node '{node_id}': port '{port.name}' accepts one connection but "
                    f"holds {len(values)}."
                )
            for value in values:
                if value is None:
                    raise DAGExecutionError(
                        f"Node '{node_id}': port '{port.name}' received no value. "
                        f"Upstream node produced no output."
                    )
                operands.append(value)

        undeclared = set(port_view.ports()) - {port.name for port in declared}
        if undeclared:
            # A validated graph cannot produce one (the validator reports PORT_UNKNOWN),
            # but a persisted plan is read back without being re-validated. Dropping it
            # silently would be the defect this task closes, so it is named.
            raise DAGExecutionError(
                f"Node '{node_id}': edges land on port(s) "
                f"{sorted(undeclared)}, which block '{node.get('block_id')}' does not "
                f"declare."
            )
        return operands

    @staticmethod
    def _bind_positionally(
        node: Dict, node_id: str, inputs: Dict[str, Any]
    ) -> List[Any]:
        """The pre-port fallback: operands in ``input_order``, then in ``inputs`` order.

        Reached only by a payload that carries a ``runtime_ref`` but no port-addressed
        edges or no resolvable descriptor. Kept verbatim so such a payload behaves exactly
        as it did before port addressing, rather than gaining a new failure mode.
        """
        order = node.get("input_order")
        source_ids = [
            source_id
            for source_id in (list(order) if order else list(inputs))
            if source_id in inputs
        ]
        for source_id in source_ids:
            if inputs[source_id] is None:
                raise DAGExecutionError(
                    f"Node '{node_id}': None value in input '{source_id}'. "
                    f"Upstream node produced no output."
                )
        return [inputs[source_id] for source_id in source_ids]


#: ``design.md`` names the MATH-category executor ``MathExecutor``, and this is it. MATH
#: and LOGIC are one class here because they do the same three things - bind operands by
#: input port name, delegate to the kernel their descriptor publishes, shape the result
#: into a bar-aligned Series - and differ only in which kernel that is. A second class
#: body would be a second place for the numeric firewall's plumbing to drift, which is the
#: opposite of what Requirement 20.3 needs; the arithmetic itself lives in exactly one
#: place either way (``block_specs``), and the engine-side seam around it is
#: :func:`safe_math_apply` above.
MathExecutor = BlockKernelExecutor


class LogicExecutor(BlockKernelExecutor):
    """The LOGIC-category executor ``design.md`` names (task 5.4).

    Every logic block runs through the ``block_specs`` kernel its descriptor publishes:
    ``logic_and`` / ``logic_or`` / ``logic_not``, the six comparators, ``logic_cross_above``
    / ``logic_cross_below``, ``logic_between``, ``logic_if_then_else`` and
    ``logic_to_signal``. Not one comparison or boolean rule is restated here - the kernels
    own the semantics, including the two that are easy to get wrong and that Requirement
    20.7 and the comparator contract pin:

    * **The warmup region never signals.** ``logic_cross_above`` is defined on closed bars
      only and reports **False** - not NaN, not an exception - when either series is NaN at
      the current *or* the previous bar. A NaN that propagated into a truthy value would
      fire a phantom entry on the first bar an indicator became defined, which is the most
      common way a backtest invents a trade it could never have made.
    * **An undefined bar compares False.** ``_compare`` masks NaN on either side before it
      applies the operator, so ``rsi > 30`` through the warmup is False rather than
      whatever a NaN comparison happens to yield.

    Operand binding, the variadic-port expansion that gives ``and`` / ``or`` all N of their
    conditions, the ``input_order`` fallback for a legacy payload, and the recorded
    numeric conditions are all inherited from :class:`BlockKernelExecutor` - MATH and LOGIC
    differ only in which kernel they call, so there is one binder and one place for it to
    drift.

    The default ``fallback`` is :class:`LegacyLogicExecutor`, so a hand-built
    ``{"type": "logic", "operator": "GT"}`` dict carrying no ``runtime_ref`` keeps its
    previous behaviour and ``LogicExecutor()`` stays constructible with no arguments (which
    is how ``dag_engine_parallel`` builds it).
    """

    def __init__(
        self,
        fallback: Optional[Any] = None,
        issues: Optional[NodeIssueLog] = None,
    ):
        super().__init__(
            fallback=fallback if fallback is not None else LegacyLogicExecutor(),
            issues=issues,
        )


class DAGEngine:
    """
    DAG Execution Engine for strategy backtesting.
    
    Executes nodes in topological order and propagates signals
    through the graph to generate trading signals.
    """
    
    def __init__(self, enable_tracing: bool = True, enable_event_buffer: bool = True):
        #: Numeric conditions recorded against the nodes that produced them
        #: (Requirement 20.4). Created before the executors because the kernel-backed ones
        #: hold a reference to it: one log per engine, written by whichever executor
        #: observed the condition. Cleared per run, never replaced.
        self.node_issues = NodeIssueLog()
        passthrough = MarketDataExecutor()
        # A canonical DATA node publishes every declared output port from the frame; a
        # legacy dict falls through to the pass-through it always used. A canonical
        # FEATURE_ENGINEERING node runs through its FEATURE_SPECS runtime; a legacy
        # ``{"type": "feature"}`` dict likewise falls through unchanged.
        data_feed = DataFeedExecutor(passthrough)
        self.executors = {
            "market_data": data_feed,
            "market_input": data_feed,
            "ohlcv_feed": data_feed,
            "data": data_feed,
            "input": data_feed,
            "feature": FeatureExecutor(passthrough),
            # MATH and LOGIC nodes adapted from a CompiledPlan carry the runtime_ref of the
            # kernel that defines them and are executed through it; a legacy loose dict
            # falls back to exactly the executor it used before (the pass-through for math,
            # LegacyLogicExecutor for logic).
            "math": MathExecutor(passthrough, issues=self.node_issues),
            "dl": passthrough,
            "validation": passthrough,
            "portfolio": passthrough,
            "signal": passthrough,
            "indicator": IndicatorExecutor(),
            "ml": MLExecutor(),
            "logic": LogicExecutor(issues=self.node_issues),
            "crossover": LogicExecutor(issues=self.node_issues),
            "cross": LogicExecutor(issues=self.node_issues),
            "action": ActionExecutor(),
            "order_signal": ActionExecutor(),
            "market_buy": ActionExecutor(),
            "market_sell": ActionExecutor(),
        }
        self.node_results: Dict[str, pd.Series] = {}
        #: ``(node_id, output_port) -> value``: the port-addressed value map
        #: (``design.md`` -> DAG runtime contract, where it is written
        #: ``values[node_id + "." + port.name]``). ``node_results`` above stays the
        #: legacy per-node primary view, so every existing consumer is unaffected.
        self.node_outputs: Dict[Tuple[str, str], Any] = {}
        #: The runtime state of the last :meth:`execute_plan` call (task 8.4). ``None``
        #: until a plan has been executed; ``execute_dag`` never touches it, because the
        #: readiness gate is a property of plan execution, not of the legacy loose-dict
        #: path, and reporting a stale state would be worse than reporting none.
        self.runtime_state: Optional["PlanRuntimeState"] = None
        self.execution_log: List[Dict] = []
        self.tracer = ExecutionTracer(enabled=enable_tracing)
        #: Task 9.4, Requirement 25.6: how much of the last plan evaluations' work actually
        #: went to the pool. Cumulative over the life of this engine, because the question
        #: an operator asks is "is level parallelism doing anything here", not "did it fire
        #: on the last bar". Counters only - nothing here changes what is computed.
        self.level_parallelism: Dict[str, int] = {
            "levels": 0,
            "levels_concurrent": 0,
            "nodes_concurrent": 0,
        }
        self._last_error: Optional[str] = None
        self._execution_trace: Optional[List[Dict]] = None
        
        # STEP 4.4: Event buffer and replay system
        self.enable_event_buffer = enable_event_buffer
        self._event_buffer_key = "events:{tenant_id}"  # Redis Stream key pattern
        self._max_stream_length = 1000  # Keep last 1000 events per tenant
    
    def build_graph(self, nodes: List[Dict], edges: List[Dict]) -> Tuple[Dict[str, Set[str]], Dict[str, Set[str]]]:
        """
        Build adjacency lists and compute in-degrees for topological sort.
        
        Returns:
            (graph, in_degree) where:
            - graph: node_id -> set of successor node_ids
            - in_degree: node_id -> count of incoming edges
        """
        graph = defaultdict(set)
        in_degree = defaultdict(int)
        
        # Initialize all nodes
        for node in nodes:
            node_id = node["id"]
            if node_id not in graph:
                graph[node_id] = set()
            if node_id not in in_degree:
                in_degree[node_id] = 0
        
        # Build graph from edges
        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            if source and target:
                raise ValueError("DAG contains cycles - cannot execute")
        
        return None
    
    def get_node_issues(
        self, node_id: Optional[str] = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Numeric conditions this run recorded, as JSON-ready ``node_id -> [issue]``.

        Requirement 20.4's second half: an author looking at an empty bar can find out
        *why* it is empty - which node, which condition, which bar - rather than being
        left with a silent NaN. Task 9.3 renders this in the inspector.
        """
        if node_id is None:
            return self.node_issues.as_dict()
        return {
            node_id: [issue.to_dict() for issue in self.node_issues.for_node(node_id)]
        }

    def get_execution_trace(self) -> List[Dict[str, Any]]:
        """Get full execution trace for debugging."""
        return self.tracer.get_trace_as_dict()

    def get_node_execution_trace(self, node_id: str) -> List[Dict[str, Any]]:
        """What this run recorded for ``node_id``: its inputs, outputs and duration.

        Requirement 24.6's first three subjects, read out of the one tracer this engine
        already owns. Task 9.3 renders it in the inspector beside
        :meth:`get_node_issues`; nothing here records anything new.
        """
        return self.tracer.node_trace_as_dict(node_id)

    def get_execution_failures(self) -> List[Dict[str, Any]]:
        """Every failure this run recorded, oldest first (Requirement 24.6's fourth).

        The whole run, not one node: a node that never ran because its ancestor failed is
        the commonest reason nothing happened, and that failure is recorded against the
        ancestor.
        """
        return self.tracer.failures_as_dict()

    
    def get_execution_summary(self) -> Dict[str, Any]:
        """Get execution summary statistics."""
        return self.tracer.get_execution_summary()
    
    def get_last_failure(self) -> Optional[Dict[str, Any]]:
        """Get the last failed execution trace entry."""
        failure = self.tracer.get_last_failure()
        if failure:
            return {
                "node_id": failure.node_id,
                "node_type": failure.node_type,
                "error_message": failure.error_message,
                "timestamp": failure.timestamp.isoformat(),
                "input_shape": failure.input_shape,
                "execution_time_ms": failure.execution_time_ms
            }
        return None
    
    def clear_trace(self) -> None:
        """Clear execution trace history."""
        self.tracer.clear()
    
    # STEP 4.4: Event buffer and replay system
    async def buffer_event(self, tenant_id: str, event: Dict[str, Any]) -> str:
        """
        Store event in Redis Stream for replay capability.
        
        Args:
            tenant_id: Tenant identifier
            event: Event data to store
            
        Returns:
            Event ID from Redis Stream
        """
        if not self.enable_event_buffer:
            return None
        
        try:
            import json

            from backend_app.core.cache.redis_manager import redis_manager
            redis_client = await redis_manager.get_client()
            
            stream_key = self._event_buffer_key.format(tenant_id=tenant_id)
            
            # Add event to Redis Stream
            event_data = {
                "data": json.dumps(event),
                "timestamp": datetime.now().isoformat(),
                "type": event.get("type", "unknown")
            }
            
            # XADD with MAXLEN to keep stream bounded
            event_id = await redis_client.xadd(
                stream_key,
                event_data,
                maxlen=self._max_stream_length,
                approximate=True
            )
            
            logger.debug(f"Event buffered: {event_id} for tenant {tenant_id}")
            return event_id
            
        except Exception as e:
            logger.error(f"Failed to buffer event: {e}")
            return None
    
    async def get_last_events(self, tenant_id: str, count: int = 100) -> List[Dict[str, Any]]:
        """
        Retrieve last N events from buffer.
        
        Args:
            tenant_id: Tenant identifier
            count: Number of events to retrieve
            
        Returns:
            List of events from newest to oldest
        """
        try:
            import json

            from backend_app.core.cache.redis_manager import redis_manager
            redis_client = await redis_manager.get_client()
            
            stream_key = self._event_buffer_key.format(tenant_id=tenant_id)
            
            # XREVRANGE to get newest first
            events = await redis_client.xrevrange(stream_key, count=count)
            
            parsed_events = []
            for event_id, fields in events:
                try:
                    event_data = json.loads(fields.get("data", "{}"))
                    event_data["_buffered_id"] = event_id
                    event_data["_buffered_at"] = fields.get("timestamp")
                    parsed_events.append(event_data)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse buffered event: {event_id}")
                    continue
            
            return parsed_events
            
        except Exception as e:
            logger.error(f"Failed to get last events: {e}")
            return []
    
    async def replay_events(
        self,
        tenant_id: str,
        nodes: List[Dict],
        edges: List[Dict],
        count: int = 100
    ) -> Dict[str, Any]:
        """
        Replay last N events through DAG after failure recovery.
        
        This ensures no data loss by reprocessing buffered events.
        
        Args:
            tenant_id: Tenant identifier
            nodes: DAG nodes
            edges: DAG edges
            count: Number of events to replay
            
        Returns:
            Replay results summary
        """
        logger.info(f"Starting event replay for tenant {tenant_id}, count={count}")
        
        # Get buffered events
        events = await self.get_last_events(tenant_id, count)
        
        if not events:
            logger.info(f"No events to replay for tenant {tenant_id}")
            return {"replayed": 0, "successful": 0, "failed": 0}
        
        # Reverse to process oldest first (maintain order)
        events.reverse()
        
        results = {
            "replayed": len(events),
            "successful": 0,
            "failed": 0,
            "signals_generated": 0,
            "events": []
        }
        
        # Replay each event through DAG
        for event in events:
            event_id = event.get("_buffered_id", "unknown")
            
            try:
                # Convert buffered event back to market data format
                market_data = self._event_to_market_data(event)
                
                # Execute DAG
                dag_result = self.execute_dag(nodes, edges, market_data)
                
                # Check for signals
                if dag_result.get("signals"):
                    results["signals_generated"] += len(dag_result["signals"])
                
                results["successful"] += 1
                results["events"].append({
                    "id": event_id,
                    "status": "success",
                    "signals": len(dag_result.get("signals", []))
                })
                
                logger.debug(f"Replayed event {event_id} successfully")
                
            except Exception as e:
                results["failed"] += 1
                results["events"].append({
                    "id": event_id,
                    "status": "failed",
                    "error": str(e)
                })
                logger.error(f"Failed to replay event {event_id}: {e}")
        
        logger.info(
            f"Replay complete for tenant {tenant_id}: "
            f"{results['successful']}/{results['replayed']} successful, "
            f"{results['signals_generated']} signals generated"
        )
        
        return results
    
    def _event_to_market_data(self, event: Dict[str, Any]) -> pd.DataFrame:
        """Convert buffered event back to market data DataFrame."""
        # Extract OHLCV data from event
        data = {
            "open": [event.get("open", 0)],
            "high": [event.get("high", 0)],
            "low": [event.get("low", 0)],
            "close": [event.get("close", 0)],
            "volume": [event.get("volume", 0)]
        }
        
        timestamp = event.get("timestamp")
        if timestamp:
            index = pd.DatetimeIndex([timestamp])
        else:
            index = pd.DatetimeIndex([datetime.now()])
        
        return pd.DataFrame(data, index=index)
    
    async def clear_event_buffer(self, tenant_id: str) -> bool:
        """Clear event buffer for tenant (useful for testing/reset)."""
        try:
            from backend_app.core.cache.redis_manager import redis_manager
            redis_client = await redis_manager.get_client()
            stream_key = self._event_buffer_key.format(tenant_id=tenant_id)
            await redis_client.delete(stream_key)
            logger.info(f"Event buffer cleared for tenant {tenant_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to clear event buffer: {e}")
            return False
    
    def get_node_inputs(self, node_id: str, edges: List[Dict]) -> List[str]:
        """Get all source node IDs connected to this node."""
        inputs = []
        for edge in edges:
            if edge.get("target") == node_id:
                inputs.append(edge.get("source"))
        return inputs
    
    def _publish_node_outputs(
        self, node: Dict, produced: Any, market_data: pd.DataFrame
    ) -> Any:
        """Validate one node's outputs port by port, publish them, return the primary.
        
        Preconditions
            ``produced`` is what the executor returned: a :class:`NodeOutputs` when it
            has several output ports (or a legacy primary that is not its first
            declared port), otherwise a bare value.
        
        Postconditions
            Every value written into ``self.node_outputs`` passed
            :func:`validate_port_output` against its declared ``PortType``. Nothing is
            written when any port fails, so a downstream node cannot read a
            half-validated node. The return value is the primary, which is what
            ``node_results`` and the trace record.
        """
        node_id = node["id"]
        declared = _declared_output_ports(node)
        
        if isinstance(produced, NodeOutputs):
            by_port = dict(produced.by_port)
            primary = produced.primary
        else:
            # A single value belongs to the node's first declared output port; a legacy
            # node declares none, so there is no port contract to record and the value
            # stays reachable by node id alone - exactly as before this task.
            primary = declared[0] if declared else ""
            by_port = {primary: produced} if primary else {}
        
        descriptor = descriptor_for(node)
        expected_length = len(market_data) if market_data is not None else 0
        expected_index = (
            market_data.index if market_data is not None else pd.Index([])
        )
        
        validated: Dict[str, Any] = {}
        for port_name, value in by_port.items():
            port = (
                descriptor.output_port(port_name) if descriptor is not None else None
            )
            if port is None:
                # An undeclared port has no contract to check it against. Publishing it
                # unvalidated would be worse than not publishing it, and a validated
                # graph cannot produce one (the compiler resolves ports from the
                # descriptor), so it is dropped with a warning rather than trusted.
                logger.warning(
                    "Node '%s' produced undeclared output port '%s'; not published",
                    node_id,
                    port_name,
                )
                continue
            validate_port_output(
                value, node_id, port, expected_length, expected_index
            )
            validated[port_name] = value
        
        for port_name, value in validated.items():
            self.node_outputs[(node_id, port_name)] = value
        
        return by_port.get(primary, produced) if by_port else produced

    def execute_node(self, node: Dict, edges: List[Dict], market_data: pd.DataFrame) -> pd.Series:
        """
        Execute a single node with its inputs and trace execution.
        
        Returns the output series for this node - the node's *primary* output port,
        which for every pre-existing executor is the single value it already returned.
        
        Port addressing (task 5.2)
        --------------------------
        Inputs are resolved by :func:`resolve_node_inputs`, so an executor receives a
        :class:`PortInputs`: still a ``dict`` keyed by upstream node id, plus
        ``inputs.port("<input port>")`` addressed by the port the edge landed on. The
        port names come from the engine edges, which ``plan_to_engine_graph`` emits
        straight out of ``CompiledPlan.inbound`` - the compiler's own port-level
        wiring, not a second vocabulary.
        
        Outputs are written into ``self.node_outputs[(node_id, port)]`` keyed by the
        output port that produced them, and each one is checked against the
        ``PortType`` its descriptor declares - by :func:`validate_port_output`, which
        delegates to the existing :func:`validate_node_output` - **before** it enters
        that map (Requirement 20.2). A value that fails validation is never visible to
        a downstream node.
        
        ``self.node_results[node_id]`` keeps holding the primary value, so every
        existing consumer (``execute_dag``'s signal sum, ``dag_event_loop``'s history,
        ``routers/strategies.py``'s preview, the golden-plan digest) is unaffected.
        
        Three phases (task 9.4)
        -----------------------
        The body is now :meth:`_begin_node_trace` -> :meth:`_run_node_executor` ->
        :meth:`_finish_node`, in that order and with the same effects in the same
        sequence as before. The split exists so :meth:`execute_plan` can run the middle
        phase - the only one that touches nothing shared - for several nodes of one
        execution level at the same time, while the first and last stay on the calling
        thread in level order. Splitting it was preferred to a second executor-dispatch
        loop for the parallel path: two dispatch tables would be two answers to "what
        runs this node".
        """
        node_id = node["id"]
        
        # Get input values from predecessor nodes, addressed both ways.
        inputs = resolve_node_inputs(
            node_id, edges, self.node_results, self.node_outputs
        )
        
        # 📝 START EXECUTION TRACE
        self._begin_node_trace(node, inputs)
        outcome = self._run_node_executor(node, inputs, market_data)
        return self._finish_node(node, outcome, market_data)

    def _begin_node_trace(self, node: Mapping[str, Any], inputs: Any) -> None:
        """Open ``node``'s trace entry. Always on the calling thread, in level order."""
        self.tracer.start_node(
            node["id"], node.get("type", "indicator"), inputs
        )

    def _run_node_executor(
        self, node: Mapping[str, Any], inputs: Any, market_data: pd.DataFrame
    ) -> _NodeOutcome:
        """Run ``node``'s executor and report what happened. **The concurrent phase.**
        
        Touches no engine-shared state: it reads ``self.executors`` (built once in
        ``__init__`` and never mutated afterwards), and it returns what the executor
        produced instead of publishing it. A raised exception is *carried* rather than
        propagated, so the failure is reported by :meth:`_finish_node` on the calling
        thread, in level order, with the same trace entry, the same log line and the same
        exception object a sequential evaluation produced.
        
        The two writes an executor itself may perform are unchanged and were already
        per-node: :class:`NodeIssueLog` records conditions under the node's own id, and
        ``validate_node_inputs`` rewrites entries of this node's own ``inputs`` mapping.
        """
        node_type = node.get("type", "indicator")
        executor = (
            self.executors.get(node_type)
            or self.executors.get(node.get("category"))
            or self.executors.get(node.get("block_id"))
        )
        if not executor:
            return _NodeOutcome(executor_missing=True)
        
        start_time = time.time()
        try:
            produced = executor.execute(node, inputs, market_data)
        except BaseException as exc:  # noqa: BLE001 - re-raised by _finish_node, in order
            return _NodeOutcome(
                elapsed_ms=(time.time() - start_time) * 1000, error=exc
            )
        return _NodeOutcome(
            produced=produced, elapsed_ms=(time.time() - start_time) * 1000
        )

    def _finish_node(
        self,
        node: Mapping[str, Any],
        outcome: _NodeOutcome,
        market_data: pd.DataFrame,
    ) -> Any:
        """Publish ``outcome``, close the trace entry, and raise what it carried.
        
        The serialized phase: every write to ``node_outputs`` and every trace completion
        happens here, on the calling thread, so an evaluation that ran a level on four
        threads publishes in exactly the order a single-threaded one did.
        """
        node_id = node["id"]
        
        if outcome.executor_missing:
            error_msg = f"No executor for node type: {node.get('type', 'indicator')}"
            self.tracer.complete_node(
                node_id=node_id,
                output=None,
                execution_time_ms=outcome.elapsed_ms,
                status="fail",
                error_message=error_msg,
            )
            raise ValueError(error_msg)
        
        if outcome.error is not None:
            # 📝 COMPLETE FAILURE TRACE
            self.tracer.complete_node(
                node_id=node_id,
                output=None,
                execution_time_ms=outcome.elapsed_ms,
                status="fail",
                error_message=str(outcome.error),
            )
            logger.error(f"Node {node_id} execution failed: {outcome.error}")
            raise outcome.error
        
        try:
            result = self._publish_node_outputs(node, outcome.produced, market_data)
        except BaseException as exc:  # noqa: BLE001 - same trace/raise contract as before
            self.tracer.complete_node(
                node_id=node_id,
                output=None,
                execution_time_ms=outcome.elapsed_ms,
                status="fail",
                error_message=str(exc),
            )
            logger.error(f"Node {node_id} execution failed: {exc}")
            raise
        
        # 📝 COMPLETE SUCCESS TRACE
        self.tracer.complete_node(
            node_id=node_id,
            output=result,
            execution_time_ms=outcome.elapsed_ms,
            status="success",
        )
        return result

    def _published_ports(self, node_id: str) -> Dict[str, Any]:
        """Every output port value ``node_id`` published in this evaluation."""
        return {
            port: value
            for (published, port), value in self.node_outputs.items()
            if published == node_id
        }

    def _publish_memoized_node(
        self, node: Mapping[str, Any], remembered: MemoizedNode
    ) -> Any:
        """Republish a remembered result as if the node had just produced it.
        
        The values were validated by :meth:`_publish_node_outputs` when they were first
        computed and are handed over as copies, so what a downstream node reads is
        indistinguishable from a fresh computation. The trace entry is completed with a
        duration of ``0.0``: no executor ran, and reporting the original run's duration
        would make a cache hit look like work.
        """
        node_id = node["id"]
        for port_name, value in remembered.ports.items():
            self.node_outputs[(node_id, port_name)] = value
        self.tracer.complete_node(
            node_id=node_id,
            output=remembered.primary,
            execution_time_ms=0.0,
            status="success",
        )
        return remembered.primary


    def topological_sort(self, nodes: List[Dict], edges: List[Dict]) -> List[str]:
        """Calculate topological execution order of nodes using Kahn's algorithm."""
        in_degree = {n["id"]: 0 for n in nodes}
        adjacency = {n["id"]: [] for n in nodes}
        for edge in edges:
            src = edge.get("source")
            tgt = edge.get("target")
            if src in adjacency and tgt in in_degree:
                adjacency[src].append(tgt)
                in_degree[tgt] += 1
        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        order = []
        while queue:
            curr = queue.pop(0)
            order.append(curr)
            for nxt in adjacency.get(curr, []):
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)
        if len(order) != len(nodes):
            return [n["id"] for n in nodes]
        return order

    def execute_dag(
        self,
        nodes: List[Dict],
        edges: List[Dict],
        market_data: pd.DataFrame,
    ) -> Dict[str, Any]:
        """
        Execute full DAG and return results.
        
        This is the SAME execution pipeline used for both backtesting and live trading.
        No duplicate execution logic.
        
        Returns:
            {
                "signals": pd.Series,  # Final action signals
                "node_results": Dict[str, pd.Series],
                "execution_order": List[str],
                "execution_log": List[Dict],
                "action_nodes": List[str],
            }
        """
        self.node_results = {}
        self.node_outputs = {}
        self.execution_log = []
        # Cleared, not replaced: the kernel-backed executors hold a reference to this log,
        # so a fresh object here would leave them writing into the previous run's.
        self.node_issues.clear()
        
        # Get topological order
        execution_order = self.topological_sort(nodes, edges)
        
        # Create node lookup
        node_map = {node["id"]: node for node in nodes}
        
        # Execute nodes in order
        action_nodes = []
        for node_id in execution_order:
            node = node_map[node_id]
            result = self.execute_node(node, edges, market_data)
            self.node_results[node_id] = result
            
            if node.get("type") == "action":
                action_nodes.append(node_id)
        
        # Combine action signals (sum for multiple actions)
        final_signals = pd.Series(0, index=market_data.index)
        for action_id in action_nodes:
            signals = self.node_results.get(action_id, pd.Series(0, index=market_data.index))
            final_signals += signals
        
        # Normalize to -1, 0, 1
        final_signals = final_signals.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        
        # STEP 6: Signal Generation Validation
        if final_signals is None:
            raise DAGExecutionError(
                "STEP 6: Signal generation failed - final_signals is None"
            )
        
        if not isinstance(final_signals, pd.Series):
            raise DAGExecutionError(
                f"STEP 6: Invalid signal type - expected pd.Series, got {type(final_signals).__name__}"
            )
        
        if len(final_signals) == 0:
            raise DAGExecutionError(
                "STEP 6: Signal generation failed - empty signal series"
            )
        
        # Validate signal values are in expected range (-1, 0, 1)
        invalid_signals = final_signals[~final_signals.isin([-1, 0, 1])]
        if len(invalid_signals) > 0:
            raise DAGExecutionError(
                f"STEP 6: Invalid signal values detected: {invalid_signals.unique().tolist()}. "
                f"Expected only: -1 (sell), 0 (hold), 1 (buy)"
            )
        
        return {
            "signals": final_signals,
            "node_results": self.node_results,
            "execution_order": execution_order,
            "execution_log": self.execution_log,
            "action_nodes": action_nodes,
        }

    # ══════════════════════════════════════════════════════════════════════
    # THE RUNTIME READINESS GATE (task 8.4)
    # ══════════════════════════════════════════════════════════════════════

    def _plan_descriptors(self, plan: Any, registry: Any) -> Dict[str, Any]:
        """``node_id -> registry descriptor`` for every node of ``plan``.

        Resolved up front and **required**, not looked up per node with a graceful
        ``None``. A descriptor is where a node's port list, its required flags and an
        ACTION's side / order type come from; without one this gate would be deciding
        readiness against no contract at all, which is not a weaker check but an absent
        one. ``plan_to_engine_graph`` refuses the same plan for the same reason, so this
        is that refusal one step earlier rather than a new policy.
        """
        if registry is None:
            from backend_app.backend.strategy_dag import registry as registry_module

            registry = registry_module.get_registry()

        descriptors: Dict[str, Any] = {}
        for node_id, node in plan.node_index.items():
            descriptor = registry.get(node.block_id)
            if descriptor is None:
                raise DAGExecutionError(
                    f"Node '{node_id}' references block '{node.block_id}', which the "
                    "registry does not publish; its ports, warmup and order semantics "
                    "cannot be resolved, so its readiness cannot be decided"
                )
            descriptors[node_id] = descriptor
        return descriptors

    def _port_value_available(self, edge: Any) -> bool:
        """Whether the value ``edge`` carries has actually been produced.

        The same two-step resolution :func:`resolve_node_inputs` performs, asked as a
        question instead of performed: the exact output port first, then the source's
        primary for a source whose executor publishes fewer ports than it declares. Asking
        it here rather than re-deriving it is what keeps "the gate thought this port was
        fed" and "the executor found a value on this port" from ever disagreeing.
        """
        source = getattr(edge, "source", None)
        if not source:
            return False
        source_port = getattr(edge, "source_port", None)
        if isinstance(source_port, str) and (source, source_port) in self.node_outputs:
            return True
        return source in self.node_results

    def _classify_inputs(
        self, plan: Any, node: Any, descriptor: Any, state: PlanRuntimeState
    ) -> Tuple[List[str], List[str]]:
        """``(not_ready_ports, warming_ports)`` for ``node``, in declaration order.

        Two lists rather than one, because "this port has no value" has two causes and an
        author fixes them differently. A port whose every unsatisfied feed is a node
        currently :data:`WARMING` is itself only warming - waiting fixes it, and
        Requirement 20.11 says a merging node with one warming input is warming, not
        broken. A port unfed, or fed by something that will never produce a value, is
        :data:`NOT_READY` - more bars will not help.

        When one variadic port is fed by both a warming node and an unready one,
        :data:`NOT_READY` wins: the unready feed is the fact the author has to act on.

        Ports come from the **descriptor**, not from ``node.inputs``. The wire form carries
        whatever the client sent, and the canonical model re-derives ports from the
        descriptor precisely so a tampered client cannot widen or narrow its own contract;
        a node reading its port list off its own payload could drop a required port by
        omitting it.

        Every port is read with ``plan.inbound_edges`` - the **plural** accessor. A
        variadic port (``add``, ``and``, ``or``, ``min``, ``max``, ``multiply``,
        ``between``, ``feat_concat``) legitimately holds 2..N edges, and it is satisfied
        only when **every** operand the author wired is present. Reading one edge would
        compute a plausible sum from a subset of the author's operands with nothing raised
        anywhere.

        A port that is not required and not fed at all is neither: there is nothing to
        wait for and nothing to fix. A port that is not required but *is* fed by something
        unsatisfied counts, because the author wired it and expects it to matter.
        """
        not_ready: List[str] = []
        warming: List[str] = []
        for port in getattr(descriptor, "inputs", ()) or ():
            edges = plan.inbound_edges(node.id, port.name)
            if not edges:
                if getattr(port, "required", False):
                    not_ready.append(port.name)
                continue
            unavailable = [
                edge for edge in edges if not self._port_value_available(edge)
            ]
            if not unavailable:
                continue
            if all(state.state_of(edge.source) == WARMING for edge in unavailable):
                warming.append(port.name)
            else:
                not_ready.append(port.name)
        return not_ready, warming

    def _missing_action_params(self, node: Any, descriptor: Any) -> List[str]:
        """ACTION params that must hold a value before an order can be described.

        ``quantity_type`` and ``quantity`` are declared required with **no default** on
        every ACTION descriptor because a silent default position size is a financial
        safety defect. An absent one therefore cannot be filled in here; the node simply
        cannot produce an order, which is a readiness fact and is reported as one rather
        than as an intent carrying no size. A VALID graph always holds both, so this fires
        only for a plan that should not exist - and when it does, it holds the action.

        Checked on ACTION nodes only. Required params elsewhere are the validator's
        business; this gate exists to stop an under-specified *order*.
        """
        if getattr(descriptor, "category", None) is None:
            return []
        if str(getattr(descriptor.category, "value", descriptor.category)) != "ACTION":
            return []
        params: Mapping[str, Any] = getattr(node, "params", {}) or {}
        return [
            f"params.{key}"
            for key in _ACTION_REQUIRED_PARAMS
            if params.get(key) is None
        ]

    def _upstream_closure(self, plan: Any, node_id: str) -> Set[str]:
        """Every node reachable upstream of ``node_id``, transitively.

        Requirement 20.1 is written about the whole Upstream_Closure, not about direct
        predecessors: a warming indicator three hops back makes an action's data partial
        just as surely as one hop back does.
        """
        closure: Set[str] = set()
        stack: List[str] = list(plan.predecessors(node_id))
        while stack:
            current = stack.pop()
            if current in closure:
                continue
            closure.add(current)
            stack.extend(plan.predecessors(current))
        return closure

    def execute_plan(
        self,
        plan: Any,
        window: pd.DataFrame,
        state: Optional[PlanRuntimeState] = None,
        *,
        registry: Any = None,
        cache: Optional[NodeResultCache] = None,
        parallel: Optional[bool] = None,
    ) -> List[TradeIntent]:
        """``design.md`` -> "DAG runtime contract", implemented.

        Level-wise evaluation of a :class:`CompiledPlan` with per-port input resolution
        from ``plan.inbound``, and a Trade_Intent emitted **only** when every node in an
        ACTION node's upstream closure is :data:`READY`.

        Parameters
            ``plan``   - a ``CompiledPlan``. Node identity, execution levels, port wiring
                         and warmup all come from it; nothing is re-derived from a graph
                         the runtime does not hold.
            ``window`` - the validated market-data frame. On both live loops this is the
                         frame ``market_data_contract.ClosedBarIngest(drop_late=True)``
                         admitted (task 7.10): closed bars only, one row per timestamp,
                         first-wins on duplicates. This method adds **no** second ingest
                         path - it checks the invariants that frame already guarantees and
                         refuses a frame that violates them, rather than repairing one.
            ``state``  - a :class:`PlanRuntimeState`, mutated in place. A long-lived
                         deployment keeps one across evaluations; ``None`` creates one for
                         this call and leaves it on :attr:`runtime_state`.
            ``cache``  - an optional :class:`NodeResultCache` (task 9.4, Requirement 25.7).
                         Caller-owned, so the memory a memo costs belongs to whoever asked
                         for it. ``None`` computes every node, which is what every caller
                         did before this argument existed.
            ``parallel`` - whether the nodes of one execution level may be evaluated
                         concurrently (Requirement 25.6). ``None`` takes
                         :data:`LEVEL_PARALLELISM_DEFAULT`. Results do not depend on it:
                         a level's nodes are independent, only the executor call moves off
                         the calling thread, and publication stays in level order.

        Returns
            The Trade_Intents that fired, in ``plan.action_nodes`` order. **Intents, not
            orders.** Requirement 20.8 keeps ``execution_guard``, ``risk_engine``,
            ``dag_risk_integration`` and the existing idempotency controls authoritative;
            they receive this list and remain the only path to a venue. Nothing here is
            duplicated, reordered or relaxed.

        Postconditions
            An intent is present only when its ACTION node and every node in that node's
            upstream closure hold :data:`READY` - inputs present, warmup satisfied, model
            loaded and checksum-verified - and only after ``assert_execution_safe`` passed
            on it. Otherwise the runtime is silent rather than approximate.

        Loop invariants
            Entering level ``k``, every predecessor of every node in that level has been
            visited (asserted, not assumed). ``self.node_outputs`` only ever holds series
            that passed :func:`validate_port_output`, because publication goes through the
            existing :meth:`_publish_node_outputs`. ``ready`` grows monotonically within
            one evaluation.

        Raises
            :class:`DAGExecutionError` for a window or a plan this gate cannot decide
            against - an unusable frame, a plan naming an unpublished block, execution
            levels that do not respect the plan's own dependencies.
            :class:`ExecutionBlocked` when ``assert_execution_safe`` refuses a triggered
            intent. It propagates deliberately: nothing is returned, so nothing is sent,
            and the platform's existing meaning of that exception ("execution did not
            happen") is preserved instead of being downgraded to a skipped item.
        """
        from backend_app.backend.strategy_compiler import plan_to_engine_graph
        from backend_app.backend.strategy_dag.plan import plan_node_warmups

        self._assert_window_usable(window)

        # -- fresh evaluation -------------------------------------------------
        self.node_results = {}
        self.node_outputs = {}
        self.execution_log = []
        # Cleared, not replaced: the kernel-backed executors hold a reference to this log.
        self.node_issues.clear()

        # Descriptors first: a plan naming an unpublished block has no ports, no warmup and
        # no order semantics to reason about, and the refusal should say *that* rather than
        # surfacing as a warmup walk failing on the same missing descriptor.
        descriptors = self._plan_descriptors(plan, registry)

        if state is None:
            state = PlanRuntimeState()
        state.reset_for(plan, len(window.index), plan_node_warmups(plan, registry))
        self.runtime_state = state

        engine_nodes, engine_edges = plan_to_engine_graph(plan, registry)
        engine_index = {node["id"]: node for node in engine_nodes}
        ml_nodes = set(plan.ml_nodes)

        # Resolved once per evaluation rather than per node (Requirement 24.3).
        metrics = _metrics()

        ready: Set[str] = set()
        visited: Set[str] = set()

        # Task 9.4. Both are optimisations and both are per-evaluation decisions made once,
        # here, rather than per node: a switch consulted 200 times can be answered
        # differently 200 times.
        concurrent_levels = (
            LEVEL_PARALLELISM_DEFAULT if parallel is None else bool(parallel)
        )
        # The window is fingerprinted once and the cache is emptied if it moved. A frame
        # that cannot be fingerprinted leaves the cache unusable for this evaluation -
        # every node is computed, which is the pre-9.4 behaviour.
        memoising = bool(cache is not None and cache.open_window(window))

        for level in plan.execution_levels:
            # design.md: ASSERT all_predecessors_evaluated(level, values). Checked rather
            # than trusted, because a plan whose levels disagree with its own dependencies
            # would evaluate a node against inputs that do not exist yet and report the
            # result as NOT_READY - a wiring problem invented by the level list.
            #
            # It is also what makes concurrency inside a level safe: a level whose nodes
            # depended on each other would not survive this check, so the nodes that reach
            # the pool below cannot observe one another's outputs.
            for node_id in level:
                unevaluated = [
                    pred
                    for pred in plan.predecessors(node_id)
                    if pred not in visited and pred in plan.node_index
                ]
                if unevaluated:
                    raise DAGExecutionError(
                        f"Plan execution levels are inconsistent with its dependencies: "
                        f"node '{node_id}' is scheduled before "
                        f"{', '.join(sorted(unevaluated))}"
                    )

            # -- phase 1: the readiness gate, in level order ------------------
            # Nothing here executes anything. It decides which nodes of this level will
            # produce a value, resolves their inputs and opens their trace entries, all on
            # the calling thread and in the plan's own order, so the observable record of a
            # level is identical however its executors were scheduled.
            runnable: List[_LevelNode] = []
            for node_id in level:
                node = plan.node(node_id)
                if node is None:  # pragma: no cover - node_index is total
                    raise DAGExecutionError(
                        f"Plan execution levels name unknown node '{node_id}'"
                    )
                visited.add(node_id)
                descriptor = descriptors[node_id]

                unready_ports, warming_ports = self._classify_inputs(
                    plan, node, descriptor, state
                )
                unready_ports.extend(self._missing_action_params(node, descriptor))
                if unready_ports:
                    mark_node(state, node_id, NOT_READY, unready_ports)
                    # No output is published, so every node downstream of this one finds
                    # its port unsatisfied in turn. A branch that never becomes ready
                    # leaves its own actions dormant; other branches still trade.
                    continue

                if node_id in ml_nodes and not model_ready(
                    state.model_versions.get(node_id), store=state.artifact_store
                ):
                    mark_node(state, node_id, AWAITING_MODEL)
                    continue

                # Merge is all-or-nothing (Requirement 20.11): one warming input holds the
                # whole merging node warming, however many of its other inputs are ready.
                # A partial merge is a confident wrong answer, which is worse than none.
                # A node whose own composed warmup is unmet is warming for the other
                # reason (Requirement 20.10) - the window does not yet reach back far
                # enough for its value to be trustworthy.
                #
                # Neither case executes the node. Not an optimisation: a block asked for a
                # value it has no history for returns an all-NaN series, and the existing
                # ``guard_indicators`` pipeline guard **raises** on one - correctly, since
                # on a full window that means a broken indicator. Executing here would
                # turn "this deployment started 10 bars ago" into a hard runtime failure,
                # and catching that exception to recover would be weakening a control that
                # is right to be strict. So the gate declines to ask.
                if warming_ports or state.bars_remaining(node_id) > 0:
                    mark_node(state, node_id, WARMING, warming_ports)
                    continue

                # The node will produce a value. Its inputs are resolved and its trace
                # entry opened here, in level order, so a level evaluated on four threads
                # records what a level evaluated on one recorded.
                engine_node = engine_index[node_id]
                inputs = resolve_node_inputs(
                    node_id, engine_edges, self.node_results, self.node_outputs
                )
                self._begin_node_trace(engine_node, inputs)
                entry = _LevelNode(
                    node_id=node_id,
                    node=engine_node,
                    inputs=inputs,
                    descriptor=descriptor,
                )
                if memoising:
                    entry.memo_key = self._memo_key(cache, plan, node, descriptor)
                    entry.cached = cache.get(entry.memo_key)
                runnable.append(entry)

            # -- phase 2: the executors -------------------------------------
            # The only phase that may leave the calling thread, and only for nodes whose
            # executor is safe to run beside another (:data:`SERIAL_EXECUTOR_TYPES`). A
            # remembered node has nothing to run at all.
            self._evaluate_level(runnable, window, concurrent=concurrent_levels)

            # -- phase 3: publish, mark, remember, in level order -----------
            for entry in runnable:
                node_id = entry.node_id
                if entry.cached is not None:
                    result = self._publish_memoized_node(entry.node, entry.cached)
                else:
                    outcome = entry.outcome or _NodeOutcome(executor_missing=True)
                    result = self._finish_node(entry.node, outcome, window)
                    # Requirement 24.3: per-node execution duration by Block_Category,
                    # measured around the one call that does the work - the executor call
                    # itself, which is what `_run_node_executor` timed. The category comes
                    # from the **descriptor** - the registry's answer to what this block is
                    # - not from the engine node dict, whose `type` is the executor key.
                    # A memoised node records nothing here: no execution happened, and a
                    # duration of zero in the histogram would depress the p95 this
                    # requirement exists to observe.
                    if metrics is not None:
                        metrics.record_dag_node_execution(
                            _category_label(entry.descriptor), outcome.elapsed_ms
                        )
                    if entry.memo_key is not None:
                        cache.put(
                            entry.memo_key, result, self._published_ports(node_id)
                        )
                self.node_results[node_id] = result

                # Restating 20.11 as a post-condition rather than trusting the two
                # branches above to have covered it: a node is READY only when every node
                # feeding it is READY.
                if all(pred in ready for pred in plan.predecessors(node_id)):
                    ready.add(node_id)
                    mark_node(state, node_id, READY)
                else:  # pragma: no cover - a non-ready predecessor publishes nothing
                    mark_node(state, node_id, WARMING)

        # Requirement 24.3: not-ready counts by reason. `state.not_ready_reasons()` is the
        # per-evaluation snapshot task 8.4 already built and the log line below already
        # reads; the reason vocabulary is `RUNTIME_STATES`, so there is no second list to
        # drift from. `READY` is excluded - it is not a reason a node produced nothing.
        #
        # Recorded here, at the end of the level walk, and deliberately NOT after the intent
        # gate: the SYSTEM FREEZE below returns early, and a freeze must not make node
        # readiness unobservable. What a freeze stops is intents, not evaluation.
        not_ready_counts = state.not_ready_reasons()
        if metrics is not None:
            for label, count in not_ready_counts.items():
                if label != READY and count:
                    metrics.record_dag_not_ready(label, count)

        # -- the intent gate ---------------------------------------------------
        intents: List[TradeIntent] = []

        # The platform's SYSTEM FREEZE, consulted through the same call and the same
        # operation string :class:`ActionExecutor` already uses. Not a second control: this
        # gate builds its intents from the ACTION node's *signal input* rather than from
        # the executor's signed output (an exit block's output is all zeros by design), so
        # without asking here a frozen platform would still produce intents. Node
        # evaluation is deliberately left alone - the canvas may show readiness under a
        # freeze; what a freeze stops is intents.
        freeze = SafetyMonitor.check_execution_allowed("strategy_signal")
        if freeze:
            logger.critical(
                "🚫 execute_plan(%s): %d action node(s) ready, no intent emitted - %s",
                getattr(plan, "dag_hash", "?"),
                sum(1 for action_id in plan.action_nodes if action_id in ready),
                freeze,
            )
            return intents

        for action_id in plan.action_nodes:
            if action_id not in ready:
                continue
            closure = self._upstream_closure(plan, action_id)
            if not closure.issubset(ready):
                # Hard gate: no action on partial data (Requirement 20.1).
                continue

            intent = build_intent(
                plan.node(action_id),
                plan,
                self.node_outputs,
                self.node_results,
                window,
                descriptor=descriptors[action_id],
                symbol=plan.action_symbol(action_id),
                timeframe=plan.action_timeframe(action_id),
            )
            if not intent.triggered:
                continue
            # The last gate before an intent leaves the runtime. It raises rather than
            # returning a verdict, and that raise is not swallowed here: an intent whose
            # numbers cannot be checked must not be sent, and neither must the rest of
            # this evaluation's intents while the runtime is producing unreadable numbers.
            assert_execution_safe(intent, action_id, issues=self.node_issues)
            intents.append(intent)

        logger.info(
            "execute_plan(%s): %d bars, %s, %d intent(s)",
            getattr(plan, "dag_hash", "?"),
            state.bars_seen,
            ", ".join(
                f"{label}={count}" for label, count in not_ready_counts.items() if count
            ),
            len(intents),
        )
        return intents

    # ══════════════════════════════════════════════════════════════════════
    # THE RUNTIME OPTIMISATIONS (task 9.4)
    # ══════════════════════════════════════════════════════════════════════

    def _memo_key(
        self, cache: NodeResultCache, plan: Any, node: Any, descriptor: Any
    ) -> Optional[NodeMemoKey]:
        """``node``'s memo key for the window ``cache`` currently holds, or ``None``.

        The inbound list is read through ``plan.inbound_edges`` - the **plural** accessor
        the readiness gate uses - in the descriptor's declared port order, so a variadic
        port fed by three operands folds all three into the key. Reading one edge per port
        would let a graph that gained a fourth operand hit a key computed for three.
        """
        inbound: List[Tuple[str, str, str]] = []
        for port in getattr(descriptor, "inputs", ()) or ():
            for edge in plan.inbound_edges(node.id, port.name):
                inbound.append(
                    (
                        str(port.name),
                        str(getattr(edge, "source", "")),
                        str(getattr(edge, "source_port", "")),
                    )
                )
        return cache.key_for(
            node.id,
            block_id=str(getattr(node, "block_id", "")),
            params=getattr(node, "params", {}) or {},
            runtime_ref=getattr(descriptor, "runtime_ref", None),
            inbound=tuple(inbound),
        )

    def _evaluate_level(
        self,
        runnable: List[_LevelNode],
        window: pd.DataFrame,
        *,
        concurrent: bool,
    ) -> None:
        """Run the executors of one execution level (Requirement 25.6).

        ``design.md``: "``execution_levels`` lets ``dag_engine_parallel`` evaluate
        independent branches concurrently". The levels are the plan's own - nothing is
        re-derived from a graph - and the pool is the one ``dag_engine_parallel`` owns (see
        :func:`_level_pool`).

        Concurrency is used only when it can pay for itself and only where it is safe: two
        or more nodes whose executors are not in :data:`SERIAL_EXECUTOR_TYPES`. One node, an
        unavailable pool, ML/DL and ACTION nodes, or ``concurrent=False`` all run inline on
        the calling thread. Whatever the route, each node's outcome is recorded on its own
        :class:`_LevelNode` and *nothing* is published here - phase 3 of the caller does
        that, in level order.
        """
        pending = [entry for entry in runnable if entry.cached is None]
        if not pending:
            return

        self.level_parallelism["levels"] += 1
        eligible = (
            [entry for entry in pending if _concurrency_eligible(entry.node)]
            if concurrent
            else []
        )
        pool = _level_pool() if len(eligible) > 1 else None

        futures: Dict[str, Any] = {}
        if pool is not None:
            for entry in eligible:
                try:
                    futures[entry.node_id] = pool.submit(
                        self._run_node_executor, entry.node, entry.inputs, window
                    )
                except Exception as exc:  # noqa: BLE001 - a refused pool runs inline
                    logger.warning(
                        "Level-parallel submit refused for node %s, evaluating inline: %s",
                        entry.node_id,
                        exc,
                    )
                    break

        for entry in pending:
            if entry.node_id not in futures:
                entry.outcome = self._run_node_executor(
                    entry.node, entry.inputs, window
                )

        for entry in pending:
            future = futures.get(entry.node_id)
            if future is None:
                continue
            try:
                entry.outcome = future.result()
            except BaseException as exc:  # noqa: BLE001 - reported by _finish_node
                # `_run_node_executor` carries an executor's own exception in its result,
                # so reaching here means the *scheduling* failed. It is still reported as
                # this node's failure rather than swallowed: a node that did not run must
                # not look like a node that ran.
                entry.outcome = _NodeOutcome(error=exc)

        if futures:
            self.level_parallelism["levels_concurrent"] += 1
            self.level_parallelism["nodes_concurrent"] += len(futures)

    @staticmethod
    def _assert_window_usable(window: Any) -> None:
        """``design.md``'s ``ASSERT window.is_validated``, as much of it as is checkable.

        Deliberately **not** a second market-data ingest. Task 7.10 routed both event loops
        through ``market_data_contract.ClosedBarIngest(drop_late=True)``, and that gate owns
        closed-bar-only, one-row-per-timestamp, first-wins-on-duplicates and the late-event
        drop. Re-deriving any of that here would create a second arrival path and a second
        set of counters, which is the defect task 7.10 removed.

        What is left to do is refuse a frame those guarantees do not hold for, so a caller
        that bypassed the contract fails loudly instead of being silently repaired: a
        repair is an invented bar. Nothing here sorts, fills, dedupes or drops.

        ``design.md``'s companion assertion - ``window.bars >= plan.warmup_bars OR mode =
        WARMING`` - is not a refusal, because its ``OR`` branch is the normal state of a
        deployment that has just started. A short window is answered per node by
        :data:`WARMING` and no intent, which is that branch.
        """
        if not isinstance(window, pd.DataFrame):
            raise DAGExecutionError(
                "execute_plan needs the validated market-data frame; got "
                f"{type(window).__name__}"
            )
        if window.empty:
            raise DAGExecutionError(
                "execute_plan received an empty market-data window; there is no current "
                "bar to evaluate against"
            )
        index = window.index
        if index.has_duplicates:
            raise DAGExecutionError(
                "market-data window holds duplicate timestamps; the closed-bar contract "
                "admits one row per timestamp (first wins), so this frame did not come "
                "through it and must not be repaired here"
            )
        if not index.is_monotonic_increasing:
            raise DAGExecutionError(
                "market-data window is not in timestamp order; the closed-bar contract "
                "delivers sorted bars, so this frame did not come through it and "
                "re-sorting it here would hide a late arrival on a live path"
            )

    def execute(
        self,
        nodes: List[Dict],
        edges: List[Dict],
        market_data: pd.DataFrame
    ) -> Dict[str, Any]:
        """
        Convenience method for backtesting.
        
        PHASE Backtesting Engine: This is the entry point for backtesting.
        It uses the same execution pipeline as live trading.
        
        Args:
            nodes: DAG nodes from execution graph
            edges: DAG edges from execution graph
            market_data: Historical market data DataFrame
            
        Returns:
            Dictionary with signals and execution results
        """
        return self.execute_dag(nodes, edges, market_data)
    
    def generate_portfolio_signals(
        self,
        nodes: List[Dict],
        edges: List[Dict],
        market_data: pd.DataFrame,
    ) -> pd.Series:
        """
        Execute DAG and return entry/exit signals for portfolio engine.
        
        Returns:
            pd.Series with values:
            - 1: Buy signal
            - -1: Sell signal  
            - 0: Hold (no action)
        """
        result = self.execute_dag(nodes, edges, market_data)
        return result["signals"]
