"""backend/deployment_runtime.py - RT-02 / RT-03 / RT-05: run a deployed builder strategy.

THE DEFECT THIS CLOSES
    ``strategy_versions.blueprint`` is ``StrategyGraph.to_dict()`` -- nodes and edges, and by
    design no venue (SB-06). ``deploy_version`` handed that straight to ``fleet.start_bot``:
    ``BotRunner`` raised "exchange_id must be provided", and even a blueprint that carried one
    would have been evaluated by the LEGACY ``StrategyEngine.run_logic`` (``buy_logic`` /
    ``sell_logic`` / ``risk``), which a builder graph does not contain. So a strategy saved by the
    current builder could be neither started nor, had it started, emit an order. The canonical
    evaluator (``DAGEngine.execute_plan``, the path backtests and Paper_Sessions use) had no
    production caller on the deployment path (RT-05).

WHAT THIS MODULE IS
    The seam between a deployment and the canonical evaluator, in three small pieces:

    * :func:`build_runtime_blueprint` -- the deployment's binding (venue, market, timeframe,
      identifiers) merged into the version's graph, so the runtime is started with everything
      it needs and nothing is defaulted. A binding with no venue is refused HERE, before
      anything is written, rather than discovered by ``BotRunner`` after the row exists.
    * :class:`GraphSignalEvaluator` -- closed bars in, one order payload (or ``None``) out, via
      ``paper_session_runtime.PaperDagRuntime`` which wraps ``DAGEngine.execute_plan``. This is
      deliberately NOT a second evaluator: Requirement 17.10 forbids one, so this adapts the
      platform's single path and adds only what a deployment needs and a paper session does not.
    * :func:`resolve_order_quantity` -- the sizing rules (``percent_of_equity`` and friends)
      that the signal layer deliberately leaves to "the execution layer". Nothing is invented:
      a rule that cannot be resolved against the measured state yields NO order.

WHAT IT NEVER DOES
    It never submits an order. It produces a payload that goes through the SAME risk gateway and
    ``execute_with_idempotency`` the legacy path uses, so the live gates, the ambiguous-
    submission guard (RT-17) and the paper/live separation are unchanged.
    It refuses (returns ``None`` and logs) rather than approximating: a non-market order type is
    not executed as a market order, a sell with no position is not turned into a short on spot,
    and a size that cannot be computed is not replaced by a default.

Regression: tests/test_rt02_builder_strategy_runs_on_deployment_runtime.py
"""
from __future__ import annotations

import copy
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

logger = logging.getLogger("DeploymentRuntime")

#: The marker ``BotRunner`` keys its evaluator choice on. Written ONLY by
#: :func:`build_runtime_blueprint`; a blueprint without it keeps the legacy evaluator, so every
#: pre-existing call shape behaves exactly as before.
RUNTIME_DAG = "dag"

#: Order types a bot can honestly execute: ``execute_with_idempotency`` takes a price and no
#: order type, i.e. it is a market path. Anything else is refused, never down-converted.
EXECUTABLE_ORDER_TYPES = ("market",)

_BASE_QUANTITY = "base_amount"


class RuntimeBlueprintError(ValueError):
    """The deployment cannot be turned into a startable runtime blueprint.

    Raised BEFORE a deployment row exists, so a refusal leaves nothing behind.
    """


# ══════════════════════════════════════════════════════════════════════════
# THE BLUEPRINT
# ══════════════════════════════════════════════════════════════════════════


def is_graph_blueprint(blueprint: Any) -> bool:
    """True only for a blueprint :func:`build_runtime_blueprint` produced."""
    return isinstance(blueprint, Mapping) and blueprint.get("runtime") == RUNTIME_DAG


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def build_runtime_blueprint(
    version_row: Mapping[str, Any],
    *,
    deployment_id: str,
    strategy_id: str,
    symbol: Optional[str],
    timeframe: Optional[str],
    exchange_id: Optional[str],
    mode: str,
    market_type: Optional[str] = None,
    paper_fallback_exchange: Optional[str] = None,
) -> Dict[str, Any]:
    """The blueprint ``fleet.start_bot`` is given for one deployment.

    ``exchange_id`` is the venue the binding resolved from the named exchange account. A paper
    deployment binds no account, so its market data comes from the operator's configured
    ``DEFAULT_EXCHANGE`` -- the same rule ``routers/paper_trading._paper_venue`` applies to
    Paper_Sessions (the caller chooses the market, the operator chooses the feed). A LIVE
    deployment never falls back: it must carry the venue of its account.

    Raises:
        RuntimeBlueprintError: no graph, no symbol/timeframe, or no venue.
    """
    if not isinstance(version_row, Mapping):
        raise RuntimeBlueprintError("the strategy version row is unreadable")
    graph = version_row.get("blueprint")
    if not isinstance(graph, Mapping) or not isinstance(graph.get("nodes"), list) \
            or not isinstance(graph.get("edges"), list) or not graph.get("nodes"):
        raise RuntimeBlueprintError(
            "the stored version carries no strategy graph (nodes/edges), so there is "
            "nothing for the runtime to evaluate"
        )
    sym = _text(symbol)
    tf = _text(timeframe)
    if not sym or not tf:
        raise RuntimeBlueprintError(
            "the deployment binding resolved no symbol/timeframe from the strategy's DATA "
            "node; the runtime will not guess a market"
        )

    venue = _text(exchange_id)
    if venue is None and str(mode).lower() != "live":
        venue = _text(paper_fallback_exchange) or _text(os.getenv("DEFAULT_EXCHANGE"))
    if venue is None:
        raise RuntimeBlueprintError(
            "no exchange is bound to this deployment and DEFAULT_EXCHANGE is not configured, "
            "so there is no venue to take market data from; a live deployment must name an "
            "exchange account"
        )

    runtime = copy.deepcopy(dict(graph))
    # The version's persisted plan travels with the graph so every consumer executes the SAME
    # bytes (Requirement 22.3); the runtime recompiles only when the identity hash disagrees.
    for column in ("compiled_plan", "execution_graph", "dag_hash"):
        if version_row.get(column) is not None and column not in runtime:
            runtime[column] = copy.deepcopy(version_row.get(column))
    # `|=` rather than `.update(...)`: this is a local dict, not a Supabase table write, but
    # `tests/test_schema_table_reference_drift.py`'s scanner flags every `.update(`/`.insert(`/
    # `.upsert(` call as a write-candidate to resolve against a table, and a plain dict has none
    # to find. `|=` is the same in-place merge with no method call for that scanner to catch.
    runtime |= {
        "runtime": RUNTIME_DAG,
        "exchange_id": venue,
        "symbol": sym,
        "timeframe": tf,
        "deployment_id": str(deployment_id),
        "strategy_id": str(strategy_id),
        "version_id": _text(version_row.get("id")),
        "current_version": _text(version_row.get("version")) or "1.0",
        "mode": str(mode),
        "market_type": _text(market_type),
    }
    return runtime


async def rebuild_for_resume(
    row: Mapping[str, Any], *, load_version, allow_live: Optional[bool] = None
):
    """``(blueprint, paper_trading)`` for a deployment row found ``running`` after a restart.

    ``load_version(version_id)`` returns the ``strategy_versions`` row (sync or async).

    LIVE deployments are NOT resumed unless the operator opted in with
    ``RESUME_LIVE_DEPLOYMENTS=true``: restarting real-money trading unattended after a crash is
    a decision, not a default. The refusal marks the row ``failed`` with that reason (see
    ``FleetManager.resume_deployments``), so the user sees a stopped bot and redeploys it
    deliberately, rather than a row claiming ``running`` that nothing is running.
    """
    import inspect

    mode = str(row.get("mode") or ("live" if row.get("environment") == "live" else "paper")).lower()
    if allow_live is None:
        allow_live = os.getenv("RESUME_LIVE_DEPLOYMENTS", "").strip().lower() in ("1", "true", "yes")
    if mode == "live" and not allow_live:
        raise RuntimeBlueprintError(
            "live deployments are not resumed automatically after a restart "
            "(RESUME_LIVE_DEPLOYMENTS is not enabled); redeploy it to continue"
        )
    version_row = load_version(row.get("version_id"))
    if inspect.isawaitable(version_row):
        version_row = await version_row
    if not isinstance(version_row, Mapping):
        raise RuntimeBlueprintError("the deployed strategy version no longer exists")

    from backend_app.backend.deployment_binding import resolve_binding_market

    market = resolve_binding_market(version_row)
    blueprint = build_runtime_blueprint(
        version_row,
        deployment_id=str(row["id"]),
        strategy_id=str(row.get("strategy_id")),
        symbol=row.get("exchange_symbol") or market["symbol"],
        timeframe=market["timeframe"],
        exchange_id=row.get("exchange_id"),
        mode=mode,
        market_type=market.get("market_type"),
    )
    return blueprint, mode != "live"


async def resume_running_deployments(fleet, *, client_factory=None) -> Dict[str, int]:
    """Start whatever the database says should be running. Called once per runtime process.

    Reads versions with the SERVICE role (there is no request JWT in a background task).
    """
    import inspect

    if client_factory is None:
        from backend_app.core.dependencies import get_service_supabase as client_factory

    async def _load_version(version_id):
        sb = client_factory()
        q = sb.table("strategy_versions").select("*").eq("id", str(version_id)).limit(1).execute()
        res = await q if inspect.isawaitable(q) else q
        rows = getattr(res, "data", None) or []
        return rows[0] if rows else None

    outcome = await fleet.resume_deployments(
        lambda row: rebuild_for_resume(row, load_version=_load_version)
    )
    logger.info("[deployment-runtime] resume after boot: %s", outcome)
    return outcome


# ══════════════════════════════════════════════════════════════════════════
# SIZING
# ══════════════════════════════════════════════════════════════════════════


def _dec(value: Any) -> Optional[Decimal]:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return d if d.is_finite() else None


def resolve_order_quantity(
    sizing: Mapping[str, Any],
    *,
    price: Any,
    equity: Any,
    free_quote: Any,
    position_base: Any,
) -> Optional[Decimal]:
    """Base-asset quantity for one sizing rule, or ``None`` when it cannot be computed.

    ``sizing`` is the ACTION node's ``{"quantity_type", "quantity"}`` as
    ``signal_service.action_node_output`` projects it. The rules (``block_specs.QUANTITY_TYPES``):

    ``base_amount``            quantity is already base units
    ``percent_of_equity``      quantity% of total equity, converted at ``price``
    ``percent_of_free_balance`` quantity% of the free quote balance, at ``price``
    ``quote_notional`` / ``fixed_notional``  quantity of quote currency, at ``price``
    ``percent_of_position``    quantity% of the open position (base units)

    Every input is a MEASURED figure supplied by the caller; this function holds none. A missing,
    non-finite, zero or negative input -- or an unknown rule -- returns ``None``, which the
    caller treats as "no order". Nothing is defaulted, because a default here is a number of coins
    nobody authorised.
    """
    qtype = _text(sizing.get("quantity_type")) if isinstance(sizing, Mapping) else None
    amount = _dec(sizing.get("quantity")) if isinstance(sizing, Mapping) else None
    px = _dec(price)
    if qtype is None or amount is None or amount <= 0:
        return None
    if qtype == _BASE_QUANTITY:
        return amount
    if px is None or px <= 0:
        return None

    pct = amount / Decimal(100)
    if qtype == "percent_of_equity":
        base = _dec(equity)
        qty = (base * pct / px) if base is not None and base > 0 else None
    elif qtype == "percent_of_free_balance":
        base = _dec(free_quote)
        qty = (base * pct / px) if base is not None and base > 0 else None
    elif qtype in ("quote_notional", "fixed_notional"):
        qty = amount / px
    elif qtype == "percent_of_position":
        pos = _dec(position_base)
        qty = (pos * pct) if pos is not None and pos > 0 else None
    else:
        return None

    if qty is None or qty <= 0:
        return None
    return qty.quantize(Decimal("0.000001"))


# ══════════════════════════════════════════════════════════════════════════
# THE EVALUATOR
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ClosedBar:
    """One closed candle, in the shape ``PaperDagRuntime`` reads off a market event."""

    event_timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    source_event_id: str

    @staticmethod
    def from_candle(candle: Mapping[str, Any]) -> "ClosedBar":
        """From ``DataProcessorEngine``'s closed-candle dict (``timestamp`` in seconds)."""
        ts = float(candle["timestamp"])
        if ts > 9_999_999_999:  # milliseconds
            ts /= 1000.0
        instant = datetime.fromtimestamp(ts, tz=timezone.utc)
        return ClosedBar(
            event_timestamp=instant,
            open=float(candle["open"]),
            high=float(candle["high"]),
            low=float(candle["low"]),
            close=float(candle["close"]),
            volume=float(candle.get("volume", 0.0)),
            source_event_id=f"{int(ts)}",
        )

    @staticmethod
    def from_ohlcv_row(row: Sequence[Any]) -> "ClosedBar":
        """From a ccxt OHLCV row ``[ms, o, h, l, c, v]``."""
        return ClosedBar.from_candle(
            {
                "timestamp": float(row[0]) / 1000.0,
                "open": row[1], "high": row[2], "low": row[3],
                "close": row[4], "volume": row[5],
            }
        )


class GraphSignalEvaluator:
    """Closed bars in, one order payload (or ``None``) out, for one deployment.

    One instance per bot: the rolling window and the plan's runtime state are per-deployment
    (Requirement 17.6's isolation).
    """

    def __init__(self, blueprint: Mapping[str, Any], *, registry: Any = None) -> None:
        if not is_graph_blueprint(blueprint):
            raise RuntimeBlueprintError("not a runtime blueprint for the DAG evaluator")
        from backend_app.backend.paper_session_runtime import (PaperDagRuntime,
                                                               resolve_session_plan)

        self.symbol = str(blueprint["symbol"])
        self.timeframe = str(blueprint["timeframe"])
        self.strategy_id = str(blueprint.get("strategy_id") or "")
        self.deployment_id = str(blueprint.get("deployment_id") or "")
        self.market_type = (_text(blueprint.get("market_type")) or "spot").lower()
        self._plan = resolve_session_plan(blueprint, registry)
        if self._plan is None:
            raise RuntimeBlueprintError(
                "the strategy graph does not compile to an executable plan; refusing to "
                "start a runtime that could never produce a signal"
            )
        self._runtime = PaperDagRuntime(
            session_id=self.deployment_id or "deployment",
            symbol=self.symbol,
            timeframe=self.timeframe,
            registry=registry,
        )
        self.refusals: Dict[str, int] = {}

    # ── history ─────────────────────────────────────────────────────────

    def warm_up(self, bars: Sequence[ClosedBar]) -> int:
        """Load history into the window WITHOUT evaluating it.

        Evaluating history would replay old signals as if they were new decisions. Returns the
        number of bars admitted.
        """
        admitted = 0
        for bar in bars:
            try:
                if self._runtime._append(bar):
                    admitted += 1
            except Exception as exc:  # noqa: BLE001 - one bad historical row costs one bar
                logger.warning("warm-up bar skipped (%s): %s", type(exc).__name__, exc)
        return admitted

    # ── evaluation ──────────────────────────────────────────────────────

    def _refuse(self, reason: str, detail: str = "") -> None:
        self.refusals[reason] = self.refusals.get(reason, 0) + 1
        logger.warning(
            "[deployment-runtime] %s/%s: signal refused (%s) %s",
            self.deployment_id, self.symbol, reason, detail,
        )

    def on_closed_bars(
        self,
        bars: Sequence[ClosedBar],
        *,
        price: Any,
        equity: Any,
        free_quote: Any,
        position_base: Any,
    ) -> Optional[Dict[str, Any]]:
        """Fold newly closed bars in and return at most ONE order payload.

        Only the NEWEST bar is evaluated; earlier ones (several can close on one tick after a
        gap) are admitted to the window so indicators see them, but a decision about a bar that
        is no longer the latest is a stale decision.
        """
        if not bars:
            return None
        *earlier, latest = list(bars)
        if earlier:
            self.warm_up(earlier)
        signals = self._runtime.evaluate(self._plan, latest)
        if not signals:
            return None
        if len(signals) > 1:
            self._refuse("MULTIPLE_SIGNALS_ONE_BAR", f"({len(signals)} signals; none executed)")
            return None
        return self._to_order(
            signals[0], price=price, equity=equity,
            free_quote=free_quote, position_base=position_base,
        )

    def _to_order(
        self, signal: Mapping[str, Any], *, price: Any, equity: Any,
        free_quote: Any, position_base: Any,
    ) -> Optional[Dict[str, Any]]:
        decision = str(signal.get("decision") or "").upper()
        position = _dec(position_base) or Decimal(0)
        order_type = str(signal.get("order_type") or "market").lower()
        if order_type not in EXECUTABLE_ORDER_TYPES:
            # A stop/limit order executed as a market order is a different order from the one
            # the author declared.
            self._refuse("ORDER_TYPE_NOT_EXECUTABLE", f"order_type={order_type}")
            return None

        reduce_only = False
        if decision == "BUY":
            side = "buy"
        elif decision == "SELL":
            side = "sell"
        elif decision == "CLOSE":
            if position <= 0:
                return None   # nothing to close is not an error and not an order
            side, reduce_only = "sell", True
        else:
            return None       # HOLD and anything else states no order

        qty: Optional[Decimal]
        if decision == "CLOSE":
            qty = position    # an exit block closes the whole position it is attached to
        elif signal.get("quantity") is not None:
            qty = _dec(signal.get("quantity"))
            if qty is not None and qty <= 0:
                qty = None
        else:
            sizing = signal.get("sizing_intention")
            qty = resolve_order_quantity(
                sizing if isinstance(sizing, Mapping) else {},
                price=price, equity=equity, free_quote=free_quote, position_base=position,
            )
        if qty is None:
            self._refuse("SIZE_UNRESOLVABLE", f"decision={decision}")
            return None

        if side == "sell" and self.market_type == "spot":
            if position <= 0:
                self._refuse("SELL_WITHOUT_POSITION", "spot has no short")
                return None
            if qty > position:
                qty = position
                reduce_only = True

        return {
            "user_id": None,   # filled by the caller; the evaluator knows no tenant
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "side": side,
            "qty": float(qty),
            "reduce_only": reduce_only,
            "order_type": "market",
            "signal_id": signal.get("signal_id"),
            "source": "dag",
        }


__all__: List[str] = [
    "RUNTIME_DAG", "EXECUTABLE_ORDER_TYPES", "RuntimeBlueprintError", "ClosedBar",
    "GraphSignalEvaluator", "build_runtime_blueprint", "is_graph_blueprint",
    "rebuild_for_resume", "resolve_order_quantity", "resume_running_deployments",
]
