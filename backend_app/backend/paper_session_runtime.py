"""
backend/paper_session_runtime.py - the Paper_Session's DAG runtime wiring.

Spec: marketplace-subscriptions-paper-trading task 28.1. Requirements 17.10, 23.5, 27.3.

WHY THIS MODULE EXISTS AT ALL, AND WHY IT IS NOT IN ``backend/paper/``
---------------------------------------------------------------------
``paper/paper_session_service.py`` implements the whole session pipeline and takes the strategy
runtime as a SEAM - ``spawn_session_loop(..., evaluate=..., plan=..., record_signal=...)`` -
for two reasons its own section header states: Requirement 17.10 forbids a second strategy
evaluation path and Requirement 23.5 forbids a second signal store, so the package calls the
platform's ONE runtime rather than containing one; and ``tests/test_paper_no_random.py`` pins
that package's first-party import list, so it may not grow an import of ``dag_engine`` (whose
transitive graph is numpy, pandas, the feature validators and the ML readiness gate).

The consequence is that SOMETHING has to perform the wiring, and the layer that performs it is
the layer that owns the runtime: the route. ``routers/paper_trading.py`` is that route, and this
module is the adapter it hands to ``start_session`` so the seam is filled by the platform's
existing pieces rather than by a paper-specific reimplementation:

  * ``DAGEngine.execute_plan`` - the ONE evaluation path. The same call ``dag_event_loop``'s
    deployment branch, ``dag_risk_integration``, ``backtest_runtime`` and the node preview make.
    Nothing here computes an indicator, resolves a port, decides readiness or applies a warmup:
    ``execute_plan`` does all of it and returns a Trade_Intent only when the ACTION node and
    every node in its upstream closure hold ``READY``.
  * ``dag_event_loop.RollingWindow`` - the ONE closed-bar window the live loop feeds the engine
    from, with its first-wins-on-duplicate and monotonic-timestamp guards.
  * ``signal_service.action_node_output`` - the ONE ``TradeIntent`` -> named-key signal
    projection. It already spells an exit block's absent side as ``CLOSE``, and it already
    refuses to write a percentage into a quantity column.

WHAT IS DELIBERATELY NOT HERE, AND WHY EACH IS A REFUSAL RATHER THAN A DEFAULT
-----------------------------------------------------------------------------
* **Position sizing.** ``block_specs.QUANTITY_TYPES`` has six members and exactly one of them -
  ``base_amount`` - is already an order quantity. ``percent_of_equity``,
  ``percent_of_free_balance``, ``quote_notional``, ``fixed_notional`` and
  ``percent_of_position`` are rules for computing a size against account state, and resolving
  them is the live execution layer's. ``signal_service.action_node_output`` therefore routes
  them to ``sizing_intention`` rather than to ``quantity``, and this module carries that
  distinction through unchanged: a signal that states a sizing intention states no quantity,
  ``paper_session_service.signal_to_intent`` reports ``SIGNAL_STATES_NO_QUANTITY`` and NO order
  is submitted. Writing the percentage into the quantity would be a fabricated order size
  (Requirement 28.3).
* **A float -> Decimal conversion.** ``PaperSignal`` refuses a binary float rather than
  converting it, and its docstring says so in terms: "widening that column's Python type is
  ``signal_service``'s change to make, not something this loop may paper over by rounding". So
  the declared quantity is carried EXACTLY AS THE PLAN STORES IT and
  ``paper_session_service.paper_signal`` is the one gate on it. A plan whose ``quantity`` param
  deserialises from JSONB as a Python float therefore produces a contained, logged
  ``PaperSignalUnreadable`` per bar rather than an order for an amount nobody typed. That is a
  real limitation of the current plan storage, not of this adapter, and it is stated rather than
  hidden.
* **``random``.** The signal identifier is a UUIDv5 over stable inputs, so a replay of the same
  market events mints the same identifiers (Requirement 15.4). ``uuid4`` would make a replay
  diverge in the one column that ties a paper order back to its decision.

WHAT TASK 29.2 ADDED HERE, AND WHY IT IS HERE AND NOT IN ``backend/paper/``
--------------------------------------------------------------------------
:func:`build_session_signal_recorder` is the ``record_signal=`` seam's production filling - the
third adapter in this module, beside the evaluator and the plan, and for the identical reason:
``paper_session_service`` may not import ``signal_service`` (its pinned import list, and the
argument in its own section header), so the wiring belongs on THIS side of the seam.

  * ``signal_service.generate_paper_signal`` - the ONE recording path. Requirement 23.5 forbids
    a second signal store, so a ``PAPER`` signal is written to ``public.signals`` by the same
    ``mint`` -> probe -> INSERT that records a ``LIVE`` one, with ``environment='PAPER'``,
    ``paper_session_id`` set and ``deployment_id`` NULL. Nothing here formats a row, chooses a
    column or holds a signal.
  * The evaluator's OWN object is handed over unnarrowed. ``paper_session_service._record_signal``
    passes ``produced`` rather than its ``PaperSignal`` projection precisely so the trace records
    Requirement 23.2's full detail; task 29.4 restricts what a SUBSCRIBER may read of it at read
    time (Requirement 23.3). Narrowing here would shrink the record rather than the disclosure.
  * A failure PROPAGATES out of the recorder. ``_record_signal`` logs it and lets the paper order
    stand - the asymmetry with the live path's Requirement 15.5 refusal is argued in that
    function's docstring - so this adapter does not catch what that seam is there to contain.

EVERY IMPORT IS LAZY, AND THAT IS NOT AN OPTIMISATION
----------------------------------------------------
``dag_engine``, ``dag_event_loop``, ``signal_service`` and ``strategy_compiler`` pull numpy,
pandas and the whole feature/ML graph. ``routers/paper_trading.py`` is imported by ``main.py`` at
process start and the six retained ``/api/paper/*`` endpoints need none of it, so the imports sit
inside the functions that use them - the same convention ``routers/strategies.py`` and
``routers/strategy_operations.py`` already follow for ``DAGEngine``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger("PaperSessionRuntime")


# ══════════════════════════════════════════════════════════════════════════
# IDENTITY
# ══════════════════════════════════════════════════════════════════════════

#: The UUIDv5 namespace every paper signal identifier is minted under. A fixed, derived value -
#: not a literal somebody typed - so it is reproducible from this line alone.
PAPER_SIGNAL_NAMESPACE: uuid.UUID = uuid.uuid5(
    uuid.NAMESPACE_URL, "https://vyomquant.invalid/paper-session/signal"
)

#: The order types a Paper_Session can execute. ``paper_simulator`` supports market and limit
#: orders, and ``chk_paper_order_type`` enforces the same two. A ``stop_market`` /
#: ``stop_limit`` / ``take_profit_*`` ACTION node is carried through UNCHANGED so
#: :func:`~paper_session_service.paper_signal` refuses it by name: a stop order silently
#: executed as a market order at the bar's close is a different order from the one the author
#: declared.
PAPER_ORDER_TYPES: Tuple[str, ...] = ("market", "limit")

#: How many closed bars one session's window retains. The same figure ``DAGEventLoop`` uses for
#: its per-symbol windows, so a paper session's indicators see the same history depth a live
#: deployment's do.
DEFAULT_WINDOW_BARS = 1000


def paper_signal_id(
    *, session_id: Any, node_id: Any, bar: Any, decision: Any = None
) -> str:
    """The Signal Trace identifier one paper decision carries, as a UUID string.

    Derived, never drawn: ``uuid5`` over the session, the ACTION node, the bar the decision was
    made on and the decision itself. Two consequences, both required:

    * A replay of the same ``paper_market_events`` rows through the same plan mints the same
      identifiers, so ``paper_orders.signal_id`` is stable across a replay (Requirement 15.4).
    * Two ACTION nodes firing on one bar, or one node firing on two bars, get different
      identifiers - which is what makes the value usable as ``uq_paper_order_idem``'s input
      through :func:`~paper_session_service.signal_intent_idempotency_key`.

    It is a UUID because ``paper_orders.signal_id`` is ``UUID``. That column carries no foreign
    key to ``public.signals``, so an identifier minted before task 29.2's ``PAPER`` write exists
    is storable - it is a correlation key with no row yet, which is a stated gap and not a
    dangling reference.
    """
    key = "|".join(
        str(part)
        for part in (session_id, node_id, bar, decision if decision is not None else "")
    )
    return str(uuid.uuid5(PAPER_SIGNAL_NAMESPACE, key))


# ══════════════════════════════════════════════════════════════════════════
# THE PLAN
# ══════════════════════════════════════════════════════════════════════════


def resolve_session_plan(version_row: Any, registry: Any = None) -> Optional[Any]:
    """The ``CompiledPlan`` a Paper_Session executes, or ``None`` when it cannot be resolved.

    ``strategy_compiler.load_plan`` and nothing else: it executes the STORED plan when its
    identity hash still matches the version's graph and recompiles only when it does not
    (Requirement 22.5), which is the same rule every other version consumer applies. Resolving
    the plan any other way here would be a second answer to "what does this version execute".

    ``None`` means the version's plan could not be resolved - no row, no ``compiled_plan`` and no
    graph, or a graph that no longer compiles. It is returned rather than raised because the
    session already exists and is ``RUNNING`` by the time the loop is spawned: the honest outcome
    is a loop with no evaluator, whose every bar is logged at ERROR by
    ``paper_session_service.step_session``, rather than a start that reports failure for a session
    that was created.
    """
    if version_row is None:
        logger.error(
            "[paper-runtime] no strategy_versions row was resolved for this session, so there is "
            "no compiled plan to evaluate; the session will produce no signal"
        )
        return None
    try:
        from backend_app.backend.strategy_compiler import load_plan

        loaded = load_plan(version_row, registry)
    except Exception as exc:  # noqa: BLE001 - see the docstring: reported, not raised
        logger.error(
            "[paper-runtime] the compiled plan for strategy version %s could not be resolved "
            "(%s); the session will produce no signal",
            (version_row or {}).get("id")
            if isinstance(version_row, Mapping)
            else getattr(version_row, "id", None),
            exc,
        )
        return None

    logger.info(
        "[paper-runtime] plan %s resolved for a Paper_Session (reused=%s, reason=%s)",
        loaded.dag_hash,
        loaded.reused,
        loaded.reason,
    )
    return loaded.plan


# ══════════════════════════════════════════════════════════════════════════
# THE EVALUATOR
# ══════════════════════════════════════════════════════════════════════════


class PaperDagRuntime:
    """One Paper_Session's ``evaluate(plan, event)`` - the platform's DAG runtime, adapted.

    Holds exactly the three pieces of per-session state the runtime needs across bars, and no
    figure of its own:

    * one :class:`~dag_event_loop.RollingWindow` of closed bars, appended to from the validated
      ``paper_market_feed.MarketEvent`` the session's feed already accepted. No second ingest,
      no second validation and no gap repair - the paper feed has already de-duplicated, ordered
      and validated the candle, and this window's own first-wins guard is the last line.
    * one :class:`~dag_engine.PlanRuntimeState`, kept across evaluations so warmup and per-node
      readiness accumulate exactly as they do for a long-lived deployment.
    * one :class:`~dag_engine.DAGEngine`.

    Called through ``starlette.concurrency.run_in_threadpool`` by
    ``paper_session_service.step_session``, which is why it is SYNCHRONOUS: it is the one
    CPU-bound step of the session pipeline (Requirement 27.3).

    One instance per session. Sharing one across sessions would share a window and a runtime
    state between two tenants' evaluations, which Requirement 17.6's isolation forbids.
    """

    def __init__(
        self,
        *,
        session_id: Any,
        symbol: Any,
        timeframe: Any,
        registry: Any = None,
        window_bars: int = DEFAULT_WINDOW_BARS,
    ) -> None:
        self.session_id = str(session_id)
        self.symbol = str(symbol)
        self.timeframe = str(timeframe)
        self.registry = registry
        self.window_bars = int(window_bars)
        #: Bars appended to the window. Read by a test and by the log line below; it is a count
        #: of what happened, never a substitute for one.
        self.bars_admitted = 0
        #: Bars the window refused (a repeat, or one out of order). Counted here because the
        #: paper feed counts its own drops and these are a different set.
        self.bars_refused = 0
        self._window: Any = None
        self._engine: Any = None
        self._state: Any = None

    # ── the seam ────────────────────────────────────────────────────────

    def __call__(self, plan: Any, event: Any) -> Tuple[Mapping[str, Any], ...]:
        """``StrategyEvaluator``: one bar in, zero or more signal mappings out."""
        return self.evaluate(plan, event)

    def evaluate(self, plan: Any, event: Any) -> Tuple[Mapping[str, Any], ...]:
        """Append the bar, evaluate the plan, project the intents that fired.

        Returns:
            A tuple of named-key mappings, each of which
            ``paper_session_service.paper_signal`` reads by name. ``()`` is the common answer
            for a bar: a warming window, a node that did not trigger, or an intent this session
            cannot act on.

        Never raises. Requirement 14.7's rule on the live path - "a node evaluation error on one
        event SHALL NOT halt the runtime's processing of later events" - applies here for the
        same reason, so a ``DAGExecutionError``, an ``ExecutionBlocked`` or anything else the
        engine raises costs this bar's signals and nothing more. Each is logged with the session
        identifier and the bar, which is what an operator needs to tell "no signal" from
        "no evaluation".
        """
        if plan is None:
            return ()

        bar = getattr(event, "event_timestamp", None)
        try:
            appended = self._append(event)
        except Exception as exc:  # noqa: BLE001 - contained per Requirement 14.7
            logger.error(
                "[paper-runtime] session %s could not admit bar %s into its window (%s); no "
                "signal was produced for it",
                self.session_id,
                bar,
                exc,
            )
            return ()
        if not appended:
            # The window refused a repeat or an out-of-order bar. Not an error and not silence
            # either: the feed already recorded the event, so the fact that the runtime did not
            # re-evaluate on it is worth one line at debug.
            logger.debug(
                "[paper-runtime] session %s window refused bar %s (repeat or out of order); no "
                "evaluation for it",
                self.session_id,
                bar,
            )
            return ()

        try:
            intents = self._execute(plan)
        except Exception as exc:  # noqa: BLE001 - contained per Requirement 14.7
            logger.warning(
                "[paper-runtime] session %s: the DAG evaluation of bar %s did not complete "
                "(%s: %s); no signal was produced for this bar and the session continues",
                self.session_id,
                bar,
                type(exc).__name__,
                exc,
            )
            return ()

        produced: List[Mapping[str, Any]] = []
        for intent in intents:
            projected = self._project(plan, intent, event)
            if projected is not None:
                produced.append(projected)
        return tuple(produced)

    # ── the three collaborators ─────────────────────────────────────────

    def _append(self, event: Any) -> bool:
        """Fold one validated market event into the closed-bar window."""
        from backend_app.backend.dag_event_loop import RollingWindow, normalise_instant

        if self._window is None:
            self._window = RollingWindow(
                symbol=self.symbol,
                timeframe=self.timeframe,
                max_size=self.window_bars,
            )

        # tz-NAIVE UTC, through the platform's own normaliser: ``RollingWindow.append_bar``
        # refuses a comparison between a tz-aware timestamp and a tz-naive one, and the live
        # loop's windows are naive. ``paper_market_feed.MarketEvent.event_timestamp`` is
        # tz-aware, so the conversion happens here rather than being left to a TypeError.
        instant = normalise_instant(getattr(event, "event_timestamp", None))
        if instant is None:
            raise ValueError(
                "the market event carries no readable event_timestamp, so the bar has no place "
                "in an ordered window"
            )

        # ``float`` here is correct and is NOT a money computation. Indicators are computed in
        # numpy/pandas by the platform's one runtime, which is float-based; every MONETARY and
        # QUANTITY value the session persists is computed by ``paper_accounting`` in exact
        # decimal from the event's own ``Decimal`` prices (Requirement 18.1). Nothing this
        # window holds reaches a balance, a fill price or a fee.
        appended = self._window.append_bar(
            instant,
            float(event.open),
            float(event.high),
            float(event.low),
            float(event.close),
            float(event.volume),
        )
        if appended:
            self.bars_admitted += 1
        else:
            self.bars_refused += 1
        return bool(appended)

    def _execute(self, plan: Any) -> List[Any]:
        """``DAGEngine.execute_plan`` over the window. The platform's ONE evaluation path."""
        from backend_app.backend.dag_engine import DAGEngine, PlanRuntimeState

        if self._engine is None:
            self._engine = DAGEngine()
        if self._state is None:
            self._state = PlanRuntimeState()

        return list(
            self._engine.execute_plan(
                plan,
                self._window.to_dataframe(),
                self._state,
                registry=self.registry,
            )
        )

    def _project(
        self, plan: Any, intent: Any, event: Any
    ) -> Optional[Dict[str, Any]]:
        """One ``TradeIntent`` as the named-key mapping ``paper_signal`` reads, or ``None``.

        ``signal_service.action_node_output`` does the projection - it is the platform's one
        ``TradeIntent`` -> signal adapter, and it already spells an exit block's absent side as
        ``CLOSE`` and routes a non-base ``quantity_type`` to ``sizing_intention`` instead of to
        ``quantity``. This method adds exactly four things that projection does not carry and
        this session needs, and NOTHING else:

        1. ``signal_id`` - :func:`paper_signal_id`, derived so a replay reproduces it.
        2. ``order_type`` / ``limit_price`` - lifted from the intent to the top level, because
           ``action_node_output`` files them under ``market_context`` and ``paper_signal`` reads
           them by name. Carried UNCHANGED, so a ``stop_limit`` action is refused by name rather
           than executed as something else.
        3. ``generated_at`` - the bar's own market instant. Never a clock read: a replay must
           reproduce the timestamp (Requirement 15.4).
        4. ``quantity`` - re-read from the INTENT rather than taken from the projection, because
           ``action_node_output`` passes it through ``float()`` for ``public.signals.quantity``
           and ``PaperSignal`` refuses a binary float rather than converting one. The declared
           value travels exactly as the plan stores it and ``paper_signal`` is the only gate.

        ``None`` means this intent states nothing this session can act on and was logged.
        """
        from backend_app.backend.signal_service import action_node_output

        if not bool(getattr(intent, "triggered", False)):
            # ``execute_plan`` returns intents in ``plan.action_nodes`` order and an untriggered
            # one is an ACTION node that did not fire. Not a signal.
            return None

        try:
            output = action_node_output(
                intent, plan=plan, runtime_state=self._state
            )
        except Exception as exc:  # noqa: BLE001 - contained per Requirement 14.7
            logger.warning(
                "[paper-runtime] session %s could not project the intent from node %s (%s); no "
                "signal for it",
                self.session_id,
                getattr(intent, "node_id", None),
                exc,
            )
            return None

        decision = output.get("decision")
        if decision is None:
            logger.info(
                "[paper-runtime] session %s: the intent from node %s states no decision this "
                "session can read, so no signal was produced from it",
                self.session_id,
                getattr(intent, "node_id", None),
            )
            return None

        node_id = getattr(intent, "node_id", None)
        bar = getattr(event, "event_timestamp", None)

        signal: Dict[str, Any] = {
            "signal_id": paper_signal_id(
                session_id=self.session_id,
                node_id=node_id,
                bar=getattr(event, "source_event_id", None) or bar,
                decision=decision,
            ),
            "decision": decision,
            # The SESSION's symbol, not the intent's, when the intent names none: the session is
            # single-market by construction and ``step_session`` compares the two before it
            # submits anything, so an intent that names a different market is refused there.
            "symbol": output.get("symbol") or self.symbol,
            "generated_at": bar,
        }

        order_type = getattr(intent, "order_type", None)
        if order_type is not None:
            signal["order_type"] = order_type

        for field, attribute in (
            ("quantity", "quantity"),
            ("limit_price", "limit_price"),
        ):
            value = getattr(intent, attribute, None)
            if value is not None:
                signal[field] = value

        if "quantity" not in signal:
            sizing = output.get("sizing_intention")
            if sizing:
                # The author sized against equity, free balance, a quote notional or the open
                # position. That is a rule, not a quantity, and resolving it belongs to the live
                # execution layer. Reported at INFO with its type named so an operator can see
                # WHY the session produced a signal and no order, rather than inferring it from
                # an absent row.
                logger.info(
                    "[paper-runtime] session %s: node %s sized as %s, which is a sizing rule "
                    "rather than a base quantity; the signal is produced and no order intent is "
                    "built from it (position sizing is the execution layer's)",
                    self.session_id,
                    node_id,
                    sizing.get("quantity_type"),
                )

        return signal


# ══════════════════════════════════════════════════════════════════════════
# THE SIGNAL_TRACE RECORDER (task 29.2)
# ══════════════════════════════════════════════════════════════════════════


def build_session_signal_recorder(
    supabase: Any,
    *,
    session: Mapping[str, Any],
    version_row: Any = None,
) -> Any:
    """The ``record_signal=`` ``spawn_session_loop`` takes: one ``PAPER`` Signal_Trace write.

    The seam is ``record(signal, *, environment, paper_session_id)`` and nothing else, so the
    two things a write needs beyond those three - the RLS-scoped client and the session's own
    attribution - are closed over here. That is the same reason
    ``paper_session_service.spawn_session_loop`` is a factory.

    Substituting this for ``paper_session_service.no_signal_trace_recorder`` is what turns that
    default's ERROR line ("signal … was NOT recorded in the Signal_Trace: no recorder is
    installed") into a row in ``public.signals``. The signature is identical, which is why the
    substitution is a substitution and not a signature change.

    Args:
        supabase: The session's RLS-scoped PostgREST client. The INSERT runs under the owner's
            identity, so Requirement 20.1's scoping is the database's, not this closure's.
        session: The ``paper_sessions`` row. Read through named keys by
            ``signal_service.paper_session_facts``; its ``id`` becomes ``paper_session_id`` and
            never ``deployment_id``.
        version_row: The ``strategy_versions`` row, for the version LABEL a ``paper_sessions``
            row does not carry. The same row :func:`build_session_evaluator` resolves the plan
            from, so both adapters describe the same version.

    Returns:
        An async callable. It returns the persisted ``signal_service.Signal``, so a caller that
        wants the recorded identifier has it, and it RAISES on a failure -
        ``paper_session_service._record_signal`` is what logs and lets the paper order stand.

    Refuses, rather than defaults, on an environment that is not ``PAPER``: this recorder is
    built for one Paper_Session, and writing a row that says ``LIVE`` because a caller asked for
    one would be the single worst outcome available to it (Requirement 23.1). The value is
    resolved by exact spelling through ``execution_environment.parse_execution_environment`` -
    the platform's one resolver, which never treats an unrecognised value as close enough.
    """
    session_id = str((session or {}).get("id") or "")

    async def record(signal: Any, *, environment: str, paper_session_id: Any) -> Any:
        from backend_app.backend.execution_environment import (
            ExecutionEnvironment,
            parse_execution_environment,
        )
        from backend_app.backend.signal_service import generate_paper_signal

        resolved = parse_execution_environment(environment)
        if resolved is not ExecutionEnvironment.PAPER:
            raise ValueError(
                f"a Paper_Session's Signal_Trace recorder was asked to record "
                f"environment={environment!r}; it records "
                f"{ExecutionEnvironment.PAPER.value} only, and it does not default an "
                f"unresolved or mismatched value to anything (Requirement 23.1)"
            )

        recorded = await generate_paper_signal(
            session,
            signal,
            paper_session_id=paper_session_id or session_id,
            version=version_row,
            sb=supabase,
        )
        logger.info(
            "[paper-runtime] recorded signal %s for session %s in the Signal_Trace "
            "(environment=%s, deployment_id=%s)",
            recorded.id,
            paper_session_id or session_id,
            resolved.value,
            recorded.deployment_id,
        )
        return recorded

    return record


def build_session_evaluator(
    *,
    session_id: Any,
    symbol: Any,
    timeframe: Any,
    version_row: Any,
    registry: Any = None,
) -> Tuple[Optional[PaperDagRuntime], Optional[Any]]:
    """``(evaluate, plan)`` for one session, or ``(None, None)`` when the plan is unresolvable.

    The pair ``paper_session_service.spawn_session_loop`` takes. Returned as a pair rather than
    bound into one object because the loop passes ``plan`` to ``evaluate`` on every bar, and the
    seam is ``evaluate(plan, event)``.

    ``(None, None)`` is a legitimate answer and is the one case the caller must handle: the
    session exists and is ``RUNNING``, so the loop is still spawned - the feed has to be drained,
    the market events recorded and the equity revalued - and ``step_session`` logs at ERROR on
    every bar that no runtime is installed. That is louder and more honest than a start that
    reported failure for a session it had already created.
    """
    plan = resolve_session_plan(version_row, registry)
    if plan is None:
        return None, None
    runtime = PaperDagRuntime(
        session_id=session_id,
        symbol=symbol,
        timeframe=timeframe,
        registry=registry,
    )
    return runtime, plan


__all__ = [
    "DEFAULT_WINDOW_BARS",
    "PAPER_ORDER_TYPES",
    "PAPER_SIGNAL_NAMESPACE",
    "PaperDagRuntime",
    "build_session_evaluator",
    "build_session_signal_recorder",
    "paper_signal_id",
    "resolve_session_plan",
]
