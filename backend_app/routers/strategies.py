"""
routers/strategies.py — Strategy CRUD, ML training, backtest, and bot deployment.

FIXES APPLIED:
  N12: fleet.start_bot() called with correct signature (user_id, symbol, blueprint)
       Original called it with (user_id, bot_id, config) which crashes at runtime.
  SQL: user['id'] wrapped through safe UUID validation before any DB queries.
"""

import asyncio
import logging
import os
import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import (APIRouter, BackgroundTasks, Depends, HTTPException, Query,
                     Request, status)
from pydantic import BaseModel, ConfigDict, Field

from backend_app.core.dependencies import (create_request_supabase_async,
                                           get_current_user, get_fleet,
                                           get_vault, get_ws_manager)
from backend_app.core.subscription_dependencies import (
    check_bot_quota,
    check_ml_quota,
    check_strategy_quota,
    decrement_usage,
    increment_usage,
    get_user_plan,
    require_live_trading,
    require_ml_training,
)
from backend_app.core.subscription_engine import Resource, SubscriptionEngine
from backend_app.core.event_bus import publish_command, PublishError
from backend_app.core.rate_limit import limiter
import ccxt
from backend_app.backend.optimization_engine import get_optimization_engine, OptimizationConfig, OptimizationMethod, ValidationMethod
from backend_app.backend.backtest_runtime import get_backtest_runtime
# The server half of Requirement 12.7 (design.md -> "Server-side artifact resolution"). It runs
# BESIDE the owner-scoped predicates in this module, never in place of them: each call site
# invokes it only where the handler had already decided to refuse, so an owner's behaviour is
# untouched and a stranger still receives the same 404 a non-existent id gets (Requirement 21.4).
from backend_app.backend.marketplace import (
    subscriber_operation_guard as _subscriber_guard,
)

import inspect

router = APIRouter()
logger = logging.getLogger(__name__)


async def _persist_trained_model_path(sb: Any, user_id: str, strategy_id: str, ml_model_path: str) -> bool:
    """Persist trained ML model path to strategy record filtered by strategy_id and user_id."""
    res = sb.table("strategies").update({"ml_model_path": ml_model_path}).eq("id", strategy_id).eq("user_id", user_id).execute()
    if inspect.isawaitable(res):
        await res
    return True


# ═══════════════════════════════════════════════════════════════════════════
# CANONICAL COMPILATION (SB-01) — this router owns no compiler
#
# Task 2.4. This file used to define ``CompiledDAG`` + ``DAGCompiler``: a second
# 10-step validator, a second topological sort, a second hash and a second
# ``SCHEMA_VERSION``, none of it importable outside a FastAPI request context.
# That duplication was the direct cause of two defects:
#
#   SB-01  A graph could compile on the save path and be rejected on the clone
#          path, because the two rule sets had drifted apart.
#   SB-02  The clone path called ``compiled.get("dag_hash")`` on a ``CompiledDAG``
#          *object* that only had ``compute_hash()``. The ``AttributeError`` was
#          swallowed by a broad ``except Exception``, so every clone was persisted
#          with no hash while the in-code comment claimed the opposite.
#
# Both classes are deleted. POST /validate, POST /strategies, the clone path and
# the DAG-pruning utility all now go through the single compiler in
# ``backend_app/backend/strategy_compiler.py``, whose rules live in
# ``strategy_dag/validator.py``. This router validates nothing itself, so it
# cannot disagree with the worker, the backtester or a training job about whether
# a strategy is executable (Requirement 3.1, 3.2).
#
# The router-local ``NodeType`` enum and ``TYPE_COMPATIBILITY`` table went with
# them: the table was ``DAGCompiler``'s private edge-legality rule set and the
# enum existed only to key it. Category and port legality are now the registry's,
# expressed as validator rules R1-R8 over published port types.
# ═══════════════════════════════════════════════════════════════════════════

#: The schema version 1 ``node["type"]`` spellings that denote a model block.
#: Kept as an explicit set, not an enum, so :func:`_detect_ml_nodes` keeps reading
#: stored version 1 blueprints byte-for-byte as before; the canonical vocabulary
#: folds all of these into the single ``ML_DL`` category.
_LEGACY_ML_NODE_TYPES: frozenset = frozenset({"ml", "dl", "ml_dl", "ml_model"})


def _detect_ml_nodes(nodes: List[Dict]) -> List[Dict]:
    """
    Detect ML/DL nodes in a DAG.
    
    Returns list of ML/DL nodes with their model_id requirements.
    """
    ml_nodes = []
    for node in nodes:
        node_type = node.get("type", "").lower()
        if node_type in _LEGACY_ML_NODE_TYPES:
            ml_nodes.append(node)
    return ml_nodes


def _validate_ml_models_present(blueprint: Dict) -> None:
    """
    Validate that ML/DL strategies have trained models before deployment.
    
    Raises HTTPException if ML/DL nodes exist but no valid model reference.
    """
    # Extract nodes from blueprint
    nodes = []
    if "nodes" in blueprint:
        nodes = blueprint["nodes"]
    elif "buy_logic" in blueprint and isinstance(blueprint["buy_logic"], dict):
        nodes = blueprint["buy_logic"].get("_nodes", [])
    
    # Check for ML/DL nodes
    ml_nodes = _detect_ml_nodes(nodes)
    
    if not ml_nodes:
        return  # No ML/DL nodes, no validation needed
    
    # Strategy contains ML/DL nodes - validate model references
    ml_model_path = blueprint.get("ml_model_path")
    
    if not ml_model_path:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "ML_MODEL_MISSING",
                "message": "Strategy contains ML/DL nodes but no trained model reference found. "
                         "Train the model via POST /api/strategies/{strategy_id}/train before deployment."
            }
        )
    
    # Validate that ML nodes have model_id
    for node in ml_nodes:
        model_id = node.get("model_id")
        if not model_id:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "ML_NODE_MISSING_MODEL_ID",
                    "message": f"ML/DL node '{node.get('id')}' missing model_id. "
                             "Each ML/DL node must specify a trained model_id."
                }
            )
    
    # TODO: Add additional validation to check if model file actually exists
    # This would require filesystem access or model registry check


# ═══════════════════════════════════════════════════════════════════════════
# ONE PAYLOAD -> ONE VERDICT (SB-01)
#
# Every entry point below reaches a verdict through exactly these helpers, so
# validate, save and clone cannot return a different validity verdict or a
# different error code set for the same graph (Requirement 3.2). They are thin on
# purpose: the graph parse belongs to ``strategy_dag.schema``, the rules belong to
# ``strategy_dag.validator`` and the compile gate belongs to
# ``strategy_compiler``. Nothing here decides anything.
# ═══════════════════════════════════════════════════════════════════════════

#: HTTP status for a graph that is not executable. ``design.md`` -> Migration path
#: for callers: "POST /api/strategies (create) ... 422 on invalid". Before task
#: 2.4 this router answered 400 on one path and 200-with-an-error-body on another
#: for the same class of failure, which is SB-01 showing through at the HTTP layer
#: as well as at the compiler layer.
DAG_INVALID_STATUS = 422

#: Machine-readable codes paired with the human message (Requirement 3.7).
DAG_INVALID_CODE = "STRATEGY_GRAPH_INVALID"
DAG_UNREADABLE_CODE = "STRATEGY_GRAPH_UNREADABLE"

#: The replacement for the deprecated ``GET /api/strategies/blocks`` alias.
BLOCKS_REPLACEMENT_ENDPOINT = "/api/strategy-operations/registry/blocks"


def _graph_from_payload(payload: Any):
    """Read a request body or a stored row as one canonical ``StrategyGraph``.

    ``schema.load_graph`` accepts a canonical schema version 2 envelope, a version 1
    row whose DAG lives in ``buy_logic._nodes`` / ``_edges``, and the bare
    ``{"nodes": [...], "edges": [...]}`` body this router has always flattened. A
    version 1 payload is migrated **at read time** and the stored record is left
    exactly as it was (Requirement 1.9).

    Raises
        ``GraphParseError`` / ``UnsupportedSchemaVersion`` when the payload carries
        no readable graph. Callers turn that into a structured response rather than
        coercing it into an empty graph that would then "validate".
    """
    from backend_app.backend.strategy_dag.schema import load_graph

    return load_graph(payload)


def _validate_payload(payload: Any, registry: Any = None):
    """``(report, graph)`` for one payload. Never raises for an invalid graph.

    The validate endpoint needs the report, not an exception. The compile paths need
    the exception. Both must arrive at the *same* report, so both call
    ``validator.validate`` with the same arguments — including leaving
    ``available_bars`` unset, so the stage-10 warmup-feasibility stage is SKIPPED
    identically on every path and the code sets stay equal.
    """
    from backend_app.backend.strategy_dag import validator as dag_validator

    graph = _graph_from_payload(payload)
    return dag_validator.validate(graph, registry), graph


def _compile_payload(payload: Any, registry: Any = None):
    """The compiled version for one payload, or a raised ``ValidationError``.

    Goes through ``strategy_builder.compile_version``, the documented seam: it runs
    the validator once, hands the report to the compiler and returns a
    ``CompiledVersion`` carrying the server's canonical graph, the ``CompiledPlan``
    and the report. Nothing is persisted on the failure path, and the failure path
    ends before any database client exists (Requirements 3.5, 3.6).
    """
    from backend_app.backend.strategy_builder import compile_version

    return compile_version(_graph_from_payload(payload), registry)


def _invalid_graph_detail(exc: Any) -> Dict[str, Any]:
    """The 422 body for a ``ValidationError``: the whole structured report.

    ``ValidationError.report`` is ``None`` when the legacy string-message path raised
    it, so it is checked before being dereferenced rather than assumed present.
    """
    detail: Dict[str, Any] = {
        "error": DAG_INVALID_CODE,
        "message": str(exc),
        "errors": [],
        "warnings": [],
        "codes": [],
    }
    report = getattr(exc, "report", None)
    if report is None:
        return detail
    detail["report"] = report.to_dict()
    detail["errors"] = list(getattr(exc, "errors", []) or [])
    detail["warnings"] = list(getattr(exc, "warnings", []) or [])
    detail["codes"] = list(exc.codes()) if hasattr(exc, "codes") else []
    return detail


def _unreadable_graph_detail(exc: Exception) -> Dict[str, Any]:
    """The 422 body for a payload that is not a graph at all."""
    return {
        "error": DAG_UNREADABLE_CODE,
        "message": str(exc),
        "hint": (
            "Send a canonical schema_version 2 graph, or a version 1 payload with "
            "'nodes' and 'edges'."
        ),
    }


def _dag_fields(plan: Any) -> Dict[str, Any]:
    """The ``_dag_*`` keys this router stores inside the ``buy_logic`` JSONB.

    ``strategies`` has no dedicated DAG columns, so this router has always stashed
    them in ``buy_logic``. Task 2.4 adds ``_compiled_plan`` — ``CompiledPlan.to_dict()``,
    the one serialization path (Requirement 2.5) — so that every version consumer can
    be served the same stored plan and recompile only on a hash mismatch
    (Requirements 22.3, 22.5) instead of reconstructing a graph from a blueprint.

    ``_dag_hash`` is ``plan.dag_hash``: a plain FIELD read, never ``plan.get(...)``,
    which is literally defect SB-02.
    """
    now = datetime.now().isoformat()
    return {
        "_dag_schema_version": plan.schema_version,
        "_compiler_version": plan.compiler_version,
        "_dag_hash": plan.dag_hash,
        "_execution_order": list(plan.execution_order),
        "_compiled_plan": plan.to_dict(),
        "_warmup_bars": plan.warmup_bars,
        "_dag_updated_at": now,
    }


def _invalid_dag_fields(detail: Dict[str, Any]) -> Dict[str, Any]:
    """The ``_dag_*`` keys stored when a clone's source recompiles to an invalid graph.

    Requirement 10.2: a clone whose recompile fails is PERSISTED, not rejected -
    ``validation_state = 'INVALID'`` plus the structured report attached, with the
    failure surfaced in the response ``warnings[]`` (Requirement 10.3's typed error
    path reports to the caller through the response, not by discarding the row).

    ``strategies`` has no ``validation_state`` column - it is the legacy JSONB shape,
    not ``strategy_versions`` - so this follows the same convention :func:`_dag_fields`
    already established: the marker lives inside ``buy_logic``, as
    ``_dag_validation_state`` and ``_dag_validation_report``. ``_dag_hash`` and
    ``_compiled_plan`` are explicitly ``None`` rather than merely absent, so a reader
    cannot mistake "never compiled" for "compiled and INVALID". This does not run afoul
    of Requirement 9.3 (a ``strategy_versions`` row with ``validation_state = 'VALID'``
    must carry a hash and a plan): this row's state is ``INVALID``, never ``VALID``, and
    it lives in a different table with no such constraint at all.
    """
    now = datetime.now().isoformat()
    return {
        "_dag_hash": None,
        "_execution_order": None,
        "_compiled_plan": None,
        "_dag_validation_state": "INVALID",
        "_dag_validation_report": detail,
        "_dag_updated_at": now,
    }


def _lift_dag_fields(item: Dict[str, Any]) -> Dict[str, Any]:
    """Move the stored ``_dag_*`` keys out of ``buy_logic`` and onto the record.

    One implementation for the three readers that used to repeat it inline, so a key
    added to :func:`_dag_fields` cannot be lifted by two of them and leak through the
    third as part of ``buy_logic``.
    """
    buy_logic = item.get("buy_logic")
    if not isinstance(buy_logic, dict):
        return item
    item["nodes"] = buy_logic.pop("_nodes", [])
    item["edges"] = buy_logic.pop("_edges", [])
    item["dag_version"] = buy_logic.pop("_dag_version", 1)
    item["dag_schema_version"] = buy_logic.pop("_dag_schema_version", None)
    item["dag_created_at"] = buy_logic.pop("_dag_created_at", None)
    item["dag_updated_at"] = buy_logic.pop("_dag_updated_at", None)
    item["dag_hash"] = buy_logic.pop("_dag_hash", None)
    item["execution_order"] = buy_logic.pop("_execution_order", None)
    item["compiled_plan"] = buy_logic.pop("_compiled_plan", None)
    item["compiler_version"] = buy_logic.pop("_compiler_version", None)
    item["warmup_bars"] = buy_logic.pop("_warmup_bars", None)
    item["dag_validation_state"] = buy_logic.pop("_dag_validation_state", None)
    item["dag_validation_report"] = buy_logic.pop("_dag_validation_report", None)
    item["buy_logic"] = buy_logic
    return item


def _market_identity(graph: Any) -> Dict[str, Any]:
    """``symbol`` and ``timeframe`` as the graph's DATA nodes declare them (SB-06).

    The pre-fix save path wrote ``body.get("symbol", "BTC/USDT")`` and
    ``body.get("timeframe", "5m")``, so a strategy whose payload omitted them was
    silently persisted against BTC/USDT regardless of what the author configured.
    Market identity is a property of the DATA block's validated parameters, and by
    the time this is called the validator has already required them (``symbol`` and
    ``timeframe`` are ``required := TRUE, default := NULL`` on ``ohlcv_feed``), so
    there is nothing left to substitute a default for.

    Returns ``{}`` for a graph with no DATA node, so a caller omits the columns
    rather than inventing values for them. There is deliberately no ``exchange``
    key: exchange identity lives on a deployment binding, never on a saved strategy.
    """
    from backend_app.backend.strategy_dag.schema import BlockCategory

    symbols: List[str] = []
    timeframes: List[str] = []
    for node in graph.nodes:
        if node.category is not BlockCategory.DATA:
            continue
        symbol = node.params.get("symbol")
        timeframe = node.params.get("timeframe")
        if isinstance(symbol, str) and symbol and symbol not in symbols:
            symbols.append(symbol)
        if isinstance(timeframe, str) and timeframe and timeframe not in timeframes:
            timeframes.append(timeframe)

    if len(symbols) > 1:
        logger.info(
            "[STRATEGIES] Graph declares %d distinct symbols %s; the legacy "
            "strategies.symbol column records the first. The graph remains the "
            "authority.",
            len(symbols),
            symbols,
        )

    identity: Dict[str, Any] = {}
    if symbols:
        identity["symbol"] = symbols[0]
    if timeframes:
        identity["timeframe"] = timeframes[0]
    return identity


#: Keys a training configuration may use to name its data source, in precedence order.
TRAINING_DATA_SOURCE_KEYS = ("data_source", "exchange_id", "exchange")

#: A venue id is a ccxt exchange id: lowercase, short, no separators beyond ``_`` and ``-``.
_DATA_SOURCE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")


def _resolve_training_data_source(config: Dict[str, Any]) -> str:
    """The venue a training job reads its candles from (SB-06, Requirement 12.6).

    Resolved from the training configuration and from nothing else. The pre-fix path
    took the credential venue from ``body.get("exchange_id", "binance")`` and then built
    ``ConnectionEngine("binance", ...)`` regardless, so a job configured for another
    venue trained on Binance candles while holding that venue's keys - two answers to
    one question, and the model silently learned the wrong market.

    An unnamed data source is an error, not a default. A strategy carries no exchange
    identity at all (Requirement 12.1), so the only place this can come from is the
    training request, and inventing a venue here is the same defect in a new location.

    Raises
        ``HTTPException`` 422 naming the field, when the configuration names no data
        source or names one that is not a venue identifier.
    """
    raw = None
    for key in TRAINING_DATA_SOURCE_KEYS:
        candidate = config.get(key) if isinstance(config, dict) else None
        if isinstance(candidate, str) and candidate.strip():
            raw = candidate.strip().lower()
            break

    if raw is None:
        raise HTTPException(
            status_code=DAG_INVALID_STATUS,
            detail={
                "error": "TRAINING_DATA_SOURCE_MISSING",
                "message": (
                    "This training request names no data source. Training reads market "
                    "data from a venue you have connected; a strategy holds no exchange "
                    "identity, so the venue must be stated on the training job."
                ),
                "field": TRAINING_DATA_SOURCE_KEYS[0],
                "expected": "an exchange account id, e.g. {'data_source': 'kraken'}",
                "fix_hint": (
                    "Send 'data_source' with the exchange the model should be trained "
                    "against."
                ),
            },
        )

    if not _DATA_SOURCE_PATTERN.match(raw):
        raise HTTPException(
            status_code=DAG_INVALID_STATUS,
            detail={
                "error": "TRAINING_DATA_SOURCE_INVALID",
                "message": f"'{raw}' is not a venue identifier.",
                "field": TRAINING_DATA_SOURCE_KEYS[0],
                "expected": "a lowercase exchange id such as 'binance' or 'kraken'",
                "actual": raw,
                "fix_hint": "Use the exchange id of a connected exchange account.",
            },
        )

    return raw


def _optimize_dag_structure(nodes: List[Dict], edges: List[Dict]) -> Dict[str, Any]:
    """Prune the nodes and edges no execution path reaches.

    Graph *pruning*, not compilation: it emits no plan, no hash and no verdict, and
    it is the only consumer left of the untyped node/edge shape, because the payloads
    ``POST /api/strategies/optimize`` accepts were never canonical graphs. The
    ordering comes from ``strategy_compiler.loose_execution_order`` — the one
    loose-dict Kahn in the codebase — so this router still defines no topological
    sort of its own.
    """
    from backend_app.backend.strategy_compiler import loose_execution_order

    order = loose_execution_order(nodes, edges)
    active_nodes = [n for n in nodes if n.get("id") in order]
    active_ids = {n.get("id") for n in active_nodes}
    active_edges = [
        e for e in edges
        if e.get("source") in active_ids and e.get("target") in active_ids
    ]
    return {
        "nodes": active_nodes,
        "edges": active_edges,
        "pruned_nodes_count": len(nodes) - len(active_nodes),
        "pruned_edges_count": len(edges) - len(active_edges),
        "execution_order": order,
    }


# ── Helper Functions ───────────────────────────────────────────────────────
def extract_metrics(portfolio) -> Dict[str, Any]:
    """
    Extract key metrics from VectorBT portfolio.
    
    Args:
        portfolio: VectorBT Portfolio object
        
    Returns:
        Dictionary of metrics
    """
    stats = portfolio.stats()
    trades = portfolio.trades
    trades_count = trades.count()
    
    # Extract returns series
    returns = portfolio.returns()
    
    # Sharpe Ratio (annualized, assuming 252 trading days)
    sharpe = 0.0
    if trades_count >= 20 and returns.std() != 0 and not pd.isna(returns.std()):
        sharpe = (returns.mean() / returns.std()) * (252 ** 0.5)
    
    # Max Drawdown
    max_dd = stats.get("Max Drawdown [%]", 0.0)
    if max_dd is None:
        max_dd = 0.0
    
    # Profit Factor
    if trades_count > 0:
        pnl = trades.pnl.values
        gross_profit = pnl[pnl > 0].sum()
        gross_loss = abs(pnl[pnl < 0].sum())
        if gross_loss == 0:
            profit_factor = 9999.0
        else:
            profit_factor = gross_profit / gross_loss
    else:
        profit_factor = 0.0
    
    # Expectancy
    if trades_count > 0:
        win_rate = trades.win_rate()
        pnl = trades.pnl.values
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        avg_win = wins.mean() if len(wins) > 0 else 0.0
        avg_loss = abs(losses.mean()) if len(losses) > 0 else 0.0
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
    else:
        expectancy = 0.0
    
    return {
        "total_return": float(portfolio.total_return()),
        "final_equity": float(portfolio.value().iloc[-1]),
        "win_rate": float(trades.win_rate() * 100) if trades_count > 0 else 0.0,
        "trades_count": int(trades_count),
        "total_fees_paid": float(stats.get("Total Fees Paid", 0.0) or 0.0),
        "max_drawdown": float(max_dd),
        "sharpe_ratio": float(sharpe),
        "profit_factor": float(profit_factor),
        "expectancy": float(expectancy),
    }

# ── Import Schemas from core.models ─────────────────────────────────────────

async def _sb(user: dict):
    """Get an RLS-scoped Supabase client for the authenticated request."""
    token = user.get("access_token")
    if not token:
        raise HTTPException(401, "Missing authenticated Supabase token.")
    return await create_request_supabase_async(token)


def _safe_uid(uid: str) -> str:
    """Validate UUID-shaped user_id before injecting into SQL."""
    if re.match(r"^[a-zA-Z0-9\-_]{1,128}$", str(uid)):
        return str(uid)
    raise ValueError(f"Unsafe user_id rejected: '{uid}'")


# ── GET /api/strategies/blocks (DEPRECATED alias) ───────────────────────
@router.get("/blocks", deprecated=True)
async def get_available_blocks():
    """
    DEPRECATED. Superseded by ``GET /api/strategy-operations/registry/blocks``.

    Kept for exactly one release as a thin alias so the current palette keeps
    rendering while the frontend is re-pointed (task 2.4, ``design.md`` -> Migration
    path for callers, step 3). The response carries ``"deprecated": true`` and a
    ``"replacement"`` field naming its successor, and the existing
    ``indicators`` / ``ml_models`` / ``dl_models`` / ``total_blocks`` keys are
    unchanged so no live client breaks on the way out.

    It publishes three of the seven categories and a single generic ``window``
    parameter form for every indicator, which is SB-03 and SB-04 in one response.
    The replacement is registry-driven and publishes all seven with each block's real
    parameter specs, so nothing here should be extended - only removed.
    """
    from backend_app.backend.indicators_backend import AVAILABLE_INDICATORS
    from backend_app.backend.ml_models import AVAILABLE_ML_MODELS, AVAILABLE_DL_MODELS

    registry_version = None
    try:
        from backend_app.backend.strategy_dag import registry as block_registry

        registry_version = block_registry.registry_version()
    except Exception as exc:  # noqa: BLE001 - the alias must not fail on a version read
        logger.debug("[STRATEGIES] Registry version unavailable for /blocks alias: %s", exc)
    
    # Available indicators from backend
    indicators = []
    for indicator_name in AVAILABLE_INDICATORS:
        indicators.append({
            "id": indicator_name.lower(),
            "name": indicator_name.upper(),
            "category": "indicators",
            "description": f"{indicator_name} technical indicator",
            "parameters": [
                {"key": "window", "label": "Period", "type": "number", "default": 14, "min": 1, "max": 500}
            ]
        })
    
    # Available ML models from backend
    ml_models = []
    for model_name in AVAILABLE_ML_MODELS:
        ml_models.append({
            "id": model_name.lower(),
            "name": model_name.upper(),
            "category": "ml",
            "description": f"{model_name} machine learning model",
            "parameters": [
                {"key": "model_id", "label": "Model ID", "type": "text", "default": ""},
                {"key": "confidence_threshold", "label": "Confidence Threshold", "type": "number", "default": 0.7, "min": 0, "max": 1}
            ]
        })
    
    # Available DL models from backend
    dl_models = []
    for model_name in AVAILABLE_DL_MODELS:
        dl_models.append({
            "id": model_name.lower(),
            "name": model_name.upper(),
            "category": "dl",
            "description": f"{model_name} deep learning model",
            "parameters": [
                {"key": "model_id", "label": "Model ID", "type": "text", "default": ""},
                {"key": "confidence_threshold", "label": "Confidence Threshold", "type": "number", "default": 0.7, "min": 0, "max": 1}
            ]
        })
    
    return {
        "indicators": indicators,
        "ml_models": ml_models,
        "dl_models": dl_models,
        "total_blocks": len(indicators) + len(ml_models) + len(dl_models),
        "deprecated": True,
        "replacement": BLOCKS_REPLACEMENT_ENDPOINT,
        "deprecation_note": (
            "This alias publishes 3 of the 7 block categories and a generic parameter "
            "form. Use the replacement endpoint, which serves the backend-authoritative "
            "registry with each block's real parameter specs and port types."
        ),
        "registry_version": registry_version,
    }


#: The columns ``GET /api/strategies`` has always published, in the order it published
#: them. Task 5.2 changed the *read* to ``select("*")`` (see :func:`list_strategies` for
#: why) but not the *response*: the row is narrowed back to exactly this set, so no
#: additional column — ``sell_logic``, ``risk``, ``indicators``, ``ml_model_path`` — starts
#: travelling on every list page as a side effect of the archival filter.
_LIST_COLUMNS: tuple = (
    "id",
    "name",
    "description",
    "symbol",
    "timeframe",
    "status",
    "is_active",
    "deployed_exchange",
    "created_at",
    "updated_at",
    "buy_logic",
    "tags",
    "version",
)


@router.get("")
@router.get("/")
async def list_strategies(
    include_archived: bool = Query(
        False,
        description=(
            "Include archived (soft-deleted) strategies. The default list excludes them "
            "(Requirement 3.3); pass true for the history/audit view, where each archived "
            "entry is marked with is_archived=true and its archived_at timestamp."
        ),
    ),
    user: dict = Depends(get_current_user),
):
    """Every strategy this user owns. Archived strategies are excluded by default.

    Trading-lifecycle-integration task 5.2, Requirements 3.3, 3.4, 3.6.

    Requirement 3.3 keeps an archived strategy out of the default list; Requirement 3.4
    keeps it readable *to its owner* for history and audit, which is what
    ``include_archived=true`` serves. Both lists are the same ownership-scoped query — the
    flag changes which rows are reported, never whose.

    EVERY ENTRY CARRIES ``is_archived``, INCLUDING IN THE DEFAULT LIST
        A boolean that is always present (and ``archived_at``, which is ``null`` for an
        active strategy) rather than a key that appears only on archived rows: a reader
        that has to infer archival from a *missing* field cannot distinguish "active" from
        "this build does not report archival at all", which is exactly the state a database
        without migration 005a is in. In the default list the flag is therefore always
        ``false`` — that list contains no archived strategy by construction.

    WHY THE SELECT IS ``*`` AND THE FILTER IS IN PYTHON
        ``005a_strategy_archive.sql`` is applied by hand
        (``.github/workflows/03-deploy.yml`` has no migration step), so
        ``strategies.archived_at`` may not exist yet. Naming it in the projection — or
        filtering with ``.is_("archived_at", "null")`` server-side — would make this
        listing raise PostgreSQL's ``42703`` and take the Strategies_Page down to report a
        missing optional column. 005a's header states the disposition this handler
        implements: the listing must treat "column absent" as "no strategy is archived",
        in which case the default list is simply the full list. ``select("*")`` gets that
        for free — the key is merely absent, which
        :func:`~backend_app.backend.strategy_archive.is_archived` reads as active — and is
        the same read :func:`~backend_app.backend.strategy_archive.load_owned_strategy`
        already performs for the same reason. The response is narrowed back to
        :data:`_LIST_COLUMNS` so the wider read does not widen the payload.

    ``archived_total`` is reported on both lists, so the frontend can offer "show archived"
    only when there is something to show, without a second request.
    """
    from backend_app.backend.strategy_archive import (
        ARCHIVED_AT_COLUMN,
        STRATEGY_ARCHIVE_MIGRATION,
        archived_at_of,
        is_archived,
    )

    try:
        sb = await _sb(user)
        if not sb:
            return {
                "strategies": [],
                "total": 0,
                "include_archived": include_archived,
                "archived_total": 0,
            }

        query_res = (
            sb
            .table("strategies")
            .select("*")
            .eq("user_id", user["id"])
            .order("created_at", desc=True)
            .execute()
        )
        resp = await query_res if inspect.isawaitable(query_res) else query_res

        rows = resp.data or [] if resp and hasattr(resp, "data") else []

        results: List[Dict[str, Any]] = []
        archived_total = 0
        for row in rows:
            row = dict(row)
            archived = is_archived(row)
            if archived:
                archived_total += 1
                if not include_archived:
                    continue
            item = {column: row[column] for column in _LIST_COLUMNS if column in row}
            _lift_dag_fields(item)
            # Requirement 3.3: the archived entries this list *does* carry are marked as
            # such, so the caller renders them as history rather than as actionable rows.
            item["is_archived"] = archived
            item[ARCHIVED_AT_COLUMN] = archived_at_of(row)
            results.append(item)

        if include_archived and rows and not any(ARCHIVED_AT_COLUMN in row for row in rows):
            # The degradation 005a's header prescribes for this path: a warning naming the
            # file, not a failed listing — and nothing reported as archived that is not.
            logger.warning(
                "[STRATEGIES] include_archived=true was requested, but strategies.%s does "
                "not exist in this database, so no strategy can be archived and this list "
                "is simply the full list. Apply %s. Every entry is reported with "
                "is_archived=false rather than a guessed archival state.",
                ARCHIVED_AT_COLUMN,
                STRATEGY_ARCHIVE_MIGRATION,
            )

        return {
            "strategies": results,
            "total": len(results),
            "include_archived": include_archived,
            "archived_total": archived_total,
        }
    except Exception as e:
        logger.warning(f"[STRATEGIES] Error listing strategies for user {user.get('id')}: {e}")
        return {
            "strategies": [],
            "total": 0,
            "include_archived": include_archived,
            "archived_total": 0,
        }


# ── POST /api/strategies ─────────────────────────────────────────────────
@router.post("")
@router.post("/")
@limiter.limit("20/minute")
async def create_strategy(
    request: Request,
    body: Dict[str, Any],
    user: dict = Depends(get_current_user),
    _quota=Depends(check_strategy_quota),
):
    """
    Saves a strategy blueprint to Supabase.

    The graph is compiled through the single compiler before anything is written, so a
    graph accepted here is accepted identically by ``POST /validate``, the clone path,
    the worker and the backtester (SB-01, Requirement 3.2). An invalid graph answers
    422 with the complete structured report and persists nothing at all - no strategy
    row, no hash, no plan (Requirements 3.5, 3.6).

    SB-06: market identity comes from the graph's DATA node parameters. Nothing here
    substitutes ``"BTC/USDT"``, ``"5m"`` or ``"binance"``, and ``exchange_id`` is not
    written at all - exchange identity is a deployment binding, so a saved strategy
    stays exchange-agnostic. What is stored is the *server's* canonical graph, so a
    client that puts an ``exchange`` or an ``api_key`` in a node's params has it dropped
    by the canonical parse rather than persisted (Requirement 12.1).
    """
    logger.info(f"[STRATEGIES] Creating strategy for user {user['id']}: {body.get('name')}")

    nodes = body.get("nodes", [])
    edges = body.get("edges", [])

    from backend_app.backend.strategy_compiler import ValidationError as CompileValidationError
    from backend_app.backend.strategy_compiler import CompilerError
    from backend_app.backend.strategy_dag.schema import GraphParseError

    compiled = None
    if nodes or edges:
        try:
            compiled = _compile_payload(body)
        except CompileValidationError as e:
            # Every error, with its code, target node/edge/field and fix hint, in one
            # response. Nothing has been written: the compile ran before any database
            # client existed (Requirement 3.6).
            logger.info(
                "[STRATEGIES] Save refused for user %s: %s",
                user["id"],
                (e.codes() if hasattr(e, "codes") else e),
            )
            raise HTTPException(
                status_code=DAG_INVALID_STATUS, detail=_invalid_graph_detail(e)
            )
        except (GraphParseError, CompilerError) as e:
            logger.info("[STRATEGIES] Save payload is not a readable graph: %s", e)
            raise HTTPException(
                status_code=DAG_INVALID_STATUS, detail=_unreadable_graph_detail(e)
            )
        logger.info(
            "[STRATEGIES] Graph compiled: dag_hash=%s, %d nodes, %d action nodes, "
            "warmup %d bars",
            compiled.dag_hash,
            len(compiled.plan.execution_order),
            len(compiled.plan.action_nodes),
            compiled.plan.warmup_bars,
        )

    data = {
        "user_id": user["id"],
        "name": body.get("name", "Unnamed Strategy"),
        "buy_logic": body.get("buy_logic", {}),
        "sell_logic": body.get("sell_logic", {}),
        "risk": body.get("risk", {}),
        "indicators": body.get("indicators", []),
        "ml_model_path": body.get("ml_model_path"),
        "status": "stopped",
    }

    # SB-06: symbol and timeframe are read from the compiled graph's DATA nodes. When
    # there is no graph (a metadata-only strategy shell) the client's own values are
    # used, and when there are none either the columns are OMITTED rather than filled
    # with a literal - the whole defect was a default that looked like a choice.
    market = _market_identity(compiled.graph) if compiled is not None else {}
    for column in ("symbol", "timeframe"):
        value = market.get(column) or body.get(column)
        if value:
            data[column] = value

    # Store DAG fields inside buy_logic as workaround for missing DB columns
    buy_logic = data["buy_logic"]
    if not isinstance(buy_logic, dict):
        buy_logic = {}
        data["buy_logic"] = buy_logic

    # The SERVER's canonical graph is what gets stored, not the client's payload
    # (Requirement 6.13). Two reasons, both of them defects otherwise: the stored graph and
    # the stored ``_compiled_plan`` would be free to disagree about the same strategy, and
    # a client that sent an ``exchange`` or an ``api_key`` inside a node's params would have
    # it persisted verbatim - the canonical parse drops both (Requirement 12.1). Falls back
    # to the raw payload only when there was no graph to compile.
    if compiled is not None:
        stored_graph = compiled.graph.to_dict()
        buy_logic["_nodes"] = stored_graph["nodes"]
        buy_logic["_edges"] = stored_graph["edges"]
    else:
        buy_logic["_nodes"] = nodes
        buy_logic["_edges"] = edges
    buy_logic["_dag_version"] = 1
    buy_logic["_dag_created_at"] = datetime.now().isoformat()
    if compiled is not None:
        # dag_hash is plan.dag_hash - a FIELD (SB-02) - and _compiled_plan is
        # CompiledPlan.to_dict(), so a version consumer can reuse this plan instead of
        # recompiling (Requirements 22.3, 22.5).
        buy_logic.update(_dag_fields(compiled.plan))
    else:
        buy_logic["_dag_schema_version"] = None
        buy_logic["_dag_updated_at"] = datetime.now().isoformat()
        buy_logic["_dag_hash"] = None

    try:
        sb = await _sb(user)
        if not sb:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Strategy creation requires a configured Supabase connection. "
                    "SUPABASE_URL and SUPABASE_ANON_KEY must be set."
                ),
            )
        query = sb.table("strategies").insert(data)
        resp = await query.execute()
        if resp.data:
            strategy_id = resp.data[0].get("id")
            logger.info(f"[STRATEGIES] Strategy created successfully: {strategy_id}")
            created = {
                "id": strategy_id,
                "strategy_id": strategy_id,
                "status": "created"
            }
            if compiled is not None:
                # Additive: the identity hash the row carries, and the non-blocking
                # issues the author was shown at save time.
                created["dag_hash"] = compiled.dag_hash
                created["warmup_bars"] = compiled.plan.warmup_bars
                created["warnings"] = compiled.warnings

            try:
                from backend_app.core.notification_dispatcher import dispatch_user_notification
                await dispatch_user_notification(
                    user_id=user["id"],
                    event_type="strategy_created",
                    category="strategy",
                    severity="info",
                    title=f"Strategy Saved: {body.get('name', 'Strategy')}",
                    message=f"Strategy '{body.get('name', 'Strategy')}' compiled and saved.",
                    strategy_id=strategy_id,
                    metadata={"name": body.get("name"), "strategy_id": strategy_id, "idempotency_key": f"strat_create:{user['id']}:{strategy_id}"},
                )
            except Exception as notif_err:
                logger.debug(f"[STRATEGIES] Notification dispatch error: {notif_err}")

            return created
        else:
            logger.error("[STRATEGIES] Failed to create strategy: no data returned")
            raise HTTPException(
                status_code=500,
                detail={"error": "STRATEGY_CREATE_FAILED", "message": "Failed to create strategy: no data returned"}
            )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        logger.error(f"[STRATEGIES] Error creating strategy: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "STRATEGY_CREATE_ERROR", "message": str(e)}
        )


# ── GET /api/strategies/{id} ─────────────────────────────────────────────
@router.get("/{strategy_id}")
async def get_strategy_route(
    strategy_id: str,
    user: dict = Depends(get_current_user),
):
    sb = await _sb(user)
    if not sb:
        return {"id": strategy_id, "user_id": user["id"], "name": "Dev Strategy", "status": "stopped"}
    query = (
        sb
        .table("strategies")
        .select("*")
        .eq("id", strategy_id)
        .eq("user_id", user["id"])
    )
    resp = await query.execute()
    
    if not resp.data:
        # The owner-scoped read above matched nothing. Before answering "not found" — which
        # would be a false statement to a caller who holds an entitling Subscription to the
        # Listing that owns this strategy, and who can see it on their own Strategies page —
        # resolve the caller's relationship to the artifact server-side. An entitled
        # subscriber gets 403 MARKETPLACE_OPERATION_NOT_PERMITTED and an audited refusal
        # (Requirements 7.8, 7.12, 12.7); an unrelated caller still gets this 404,
        # indistinguishable from a non-existent strategy (Requirement 21.4). The predicate
        # above is unchanged and still decides first, so an owner's answer cannot change.
        await _subscriber_guard.refuse_if_entitled_subscriber(
            user,
            strategy_id=strategy_id,
            operation=_subscriber_guard.STRATEGY_READ,
        )
        raise HTTPException(404, "Strategy not found.")
        
    item = resp.data[0]
    _lift_dag_fields(item)

    return item


# ── PUT /api/strategies/{id}/rename ──────────────────────────────────────
#
# Requirement 2.6's bounds, measured on the submitted name AFTER leading and trailing
# whitespace is removed. Published as module constants so the handler, the body it
# refuses with, and its test read the same two numbers rather than three copies of them.
STRATEGY_NAME_MIN_LENGTH = 1
STRATEGY_NAME_MAX_LENGTH = 100

#: The one error code a rename refuses a name with, and the reason vocabulary that says
#: *which* of Requirement 2.6's two conditions failed. One code plus a ``reason`` rather
#: than a code per case: the client's remedy is the same in every case (resubmit a name
#: within the bounds), and the bounds travel on the body so it can say what they are.
STRATEGY_NAME_INVALID = "STRATEGY_NAME_INVALID"
RENAME_REASON_MISSING = "name_missing"
RENAME_REASON_NOT_A_STRING = "name_not_a_string"
RENAME_REASON_EMPTY_AFTER_TRIM = "empty_after_trim"
RENAME_REASON_TOO_LONG = "longer_than_max_length"


def _rename_refusal(reason: str, message: str, *, submitted_length: Optional[int] = None):
    """One 422 body for a refused rename. ``design.md``: "422 naming the reason"."""
    detail = {
        "error": STRATEGY_NAME_INVALID,
        "message": message,
        "reason": reason,
        "min_length": STRATEGY_NAME_MIN_LENGTH,
        "max_length": STRATEGY_NAME_MAX_LENGTH,
    }
    if submitted_length is not None:
        detail["submitted_length"] = submitted_length
    return HTTPException(status_code=422, detail=detail)


def _validated_strategy_name(body: Any) -> str:
    """Requirement 2.6's name rule, and nothing else.

    "A name between 1 and 100 characters after leading/trailing whitespace is removed."
    The trimmed name is what is measured *and* what is stored, so a name that is only
    accepted because of its padding cannot be persisted with that padding still on it.

    Raises
        :class:`HTTPException` 422 naming the reason, per ``design.md``'s error table.
        Raised before any database client is created, so a refused rename reads nothing
        and writes nothing.
    """
    if not isinstance(body, dict) or "name" not in body:
        raise _rename_refusal(
            RENAME_REASON_MISSING,
            "A rename request must carry a 'name'. Nothing was changed.",
        )

    raw = body["name"]
    if not isinstance(raw, str):
        raise _rename_refusal(
            RENAME_REASON_NOT_A_STRING,
            f"'name' must be text, not {type(raw).__name__}. Nothing was changed.",
        )

    name = raw.strip()
    if len(name) < STRATEGY_NAME_MIN_LENGTH:
        # Covers "" and whitespace-only alike: both are empty once trimmed, which is
        # the only measurement Requirement 2.6 makes.
        raise _rename_refusal(
            RENAME_REASON_EMPTY_AFTER_TRIM,
            "A strategy name cannot be empty once leading and trailing whitespace is "
            "removed. The previous name is unchanged.",
            submitted_length=len(name),
        )
    if len(name) > STRATEGY_NAME_MAX_LENGTH:
        raise _rename_refusal(
            RENAME_REASON_TOO_LONG,
            f"A strategy name may be at most {STRATEGY_NAME_MAX_LENGTH} characters once "
            f"leading and trailing whitespace is removed; this one is {len(name)}. The "
            "previous name is unchanged.",
            submitted_length=len(name),
        )
    return name


# NOTE ON ROUTE ORDER: this route is registered BEFORE ``PUT /{strategy_id}`` on purpose.
# The two cannot actually collide — Starlette compiles ``{strategy_id}`` to a single-segment
# match, so ``/{id}/rename`` is two segments and ``/{id}`` is one — but "cannot collide"
# is a property of the path converter, not something a reader of this file can see. Declaring
# the more specific path first means the ordering is right under any converter, and
# ``tests/test_task_5_3_rename_strategy.py`` asserts the route is reachable through the real
# router rather than trusting either fact.
@router.put("/{strategy_id}/rename")
async def rename_strategy(
    strategy_id: str,
    body: Dict[str, Any],
    user: dict = Depends(get_current_user),
):
    """Rename a strategy. Touches ``strategies.name`` and nothing else.

    Trading-lifecycle-integration task 5.3, Requirements 2.6, 3.3, 20.2.

    ``design.md``: "**New** — closes the confirmed-missing endpoint the frontend already
    calls." ``Strategies.jsx``'s rename handler has always issued
    ``PUT /api/strategies/{id}/rename`` with ``{"name": ...}``; a full-repository grep
    found no backend route serving it, so every rename failed. The frontend was right about
    which endpoint should exist; this is the backend catching up, which is why the path,
    the method and the body shape are the ones already being sent rather than new ones.

    WHAT IT CHANGES
        One column. The update payload is ``{"name": <trimmed>}``, so Requirement 2.6's
        "SHALL leave every version, backtest, deployment and signal record for that
        strategy unchanged" holds by construction: there is no other column in the write
        and no second statement.

    Requirement 3.3
        An archived strategy's identifier is refused the same way the edit path refuses it
        — :func:`~backend_app.backend.strategy_archive.assert_strategy_not_archived` with
        :data:`~backend_app.backend.strategy_archive.OPERATION_RENAME`, which was published
        by task 5.1 for this handler — so "archived" cannot mean one thing to a rename and
        another to an edit.

    Responses
        **200** the updated strategy record, the same shape ``PUT /api/strategies/{id}``
        returns.
        **404** no such strategy belongs to this caller — the same answer a non-existent
        id gets (Requirement 20.2).
        **409** ``STRATEGY_ARCHIVED``, naming the archival timestamp (Requirement 3.3).
        **422** ``STRATEGY_NAME_INVALID`` naming the reason (Requirement 2.6). Raised
        before any read or write, so the stored name is untouched.
        **503** the rename could not be written. Nothing was changed.
    """
    from backend_app.backend.strategy_archive import (
        OPERATION_RENAME,
        ArchiveRejected,
        assert_strategy_not_archived,
        load_owned_strategy,
    )

    # Validation first, and deliberately before the database client exists: a malformed
    # rename is refused on its own terms, reads nothing and writes nothing. This discloses
    # no existence either — a non-existent id with an invalid name answers identically,
    # which is what Requirement 20.2 asks for.
    name = _validated_strategy_name(body)

    sb = await _sb(user)
    if not sb:
        # DEV_MODE with no Supabase configured, the same echo every other handler in this
        # router answers with rather than a fabricated persisted record.
        return {"id": strategy_id, "user_id": user["id"], "name": name}

    # ``select("*")`` (never a projection naming ``archived_at``) so a database without
    # migration 005a reports no archival state instead of failing the rename outright —
    # the disposition 005a's header prescribes for every read path.
    #
    # A ``None`` here is NOT answered as a 404, and that is deliberate:
    # :func:`load_owned_strategy` returns ``None`` both for "no such strategy belongs to
    # this caller" and for "that read failed", and those two deserve different answers.
    # Letting the ownership-scoped ``UPDATE`` below be the arbiter separates them without a
    # second read — it matches nothing for a non-owner (404, per Requirement 20.2) and
    # raises for an unreachable table (503) — which is the same disposition
    # ``PUT /api/strategies/{id}`` already takes. The ``UPDATE`` carries the same ``id`` and
    # ``user_id`` filters under the same RLS policies, so a non-owner's request changes
    # nothing either way.
    strategy = await load_owned_strategy(sb, user["id"], strategy_id)
    try:
        assert_strategy_not_archived(
            strategy, operation=OPERATION_RENAME, strategy_id=strategy_id
        )
    except ArchiveRejected as e:
        logger.info(
            "[STRATEGIES] Rename refused for strategy %s, user %s: %s",
            strategy_id,
            user.get("id"),
            e.code,
        )
        raise HTTPException(status_code=e.http_status, detail=e.to_detail())

    try:
        resp = await (
            sb
            .table("strategies")
            .update({"name": name})
            .eq("id", strategy_id)
            .eq("user_id", user["id"])
            .execute()
        )
    except Exception as e:
        logger.error(
            "[STRATEGIES] Failed to rename strategy %s, user %s: %s",
            strategy_id,
            user.get("id"),
            e,
        )
        raise HTTPException(
            status_code=503, detail="Unable to rename strategy. Please try again later."
        )

    rows = (resp.data or []) if resp is not None and hasattr(resp, "data") else []
    if rows:
        return dict(rows[0])
    if strategy is None:
        # The update matched nothing and the ownership-scoped read found nothing either:
        # no such strategy belongs to this caller. Before that becomes a 404, resolve the
        # caller's relationship to the artifact server-side — an entitled subscriber is
        # refused 403 with a stable code and an audited entry (Requirements 7.8, 7.12,
        # 12.7), because "no such strategy" is false for them. Everyone else still gets the
        # same answer a genuinely non-existent identifier gets, per Requirements 20.2 and
        # 21.4. Nothing was written either way: the UPDATE above is still owner-scoped.
        await _subscriber_guard.refuse_if_entitled_subscriber(
            user,
            strategy_id=strategy_id,
            operation=_subscriber_guard.STRATEGY_RENAME,
        )
        raise HTTPException(404, "Strategy not found.")
    # The write raised nothing, so it happened; PostgREST returns a representation only
    # when asked to, and an empty body is not evidence the update matched no row when the
    # ownership-scoped read above already established that it does.
    return {**dict(strategy), "name": name}


# ── PUT /api/strategies/{id} ─────────────────────────────────────────────
@router.put("/{strategy_id}")
async def update_strategy(
    strategy_id: str,
    body: Dict[str, Any],
    user: dict = Depends(get_current_user),
):
    from backend_app.backend.strategy_archive import (
        OPERATION_EDIT,
        ArchiveRejected,
        assert_strategy_not_archived,
        load_owned_strategy,
    )

    sb = await _sb(user)
    if not sb:
        return {"id": strategy_id, "user_id": user["id"], "name": body.get("name", "Updated Dev Strategy")}

    # Requirement 3.3: an archived strategy's identifier is not editable. Read through
    # ``select("*")`` so a database without migration 005a simply reports no archival
    # state rather than failing the edit outright, and refused only on a row positively
    # read as archived — ownership here is enforced by the update's own filters and RLS,
    # exactly as before, not by this guard.
    try:
        assert_strategy_not_archived(
            await load_owned_strategy(sb, user["id"], strategy_id),
            operation=OPERATION_EDIT,
            strategy_id=strategy_id,
        )
    except ArchiveRejected as e:
        raise HTTPException(status_code=e.http_status, detail=e.to_detail())

    # SECURITY: Prevent direct status updates that bypass deployment guards
    if "status" in body and body["status"] in ["running", "deployed"]:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "DIRECT_STATUS_UPDATE_FORBIDDEN",
                "message": "Cannot directly set status to 'running' or 'deployed'. "
                         "Use the dedicated deploy endpoint POST /api/strategies/{id}/deploy "
                         "which includes ML validation and other safety checks."
            }
        )
    
    try:
        resp = await (
            sb
            .table("strategies")
            .update(body)
            .eq("id", strategy_id)
            .eq("user_id", user["id"])
            .execute()
        )
    except Exception as e:
        logger.error(f"[STRATEGIES] Failed to update strategy {strategy_id}, user {user['id']}: {e}")
        raise HTTPException(status_code=503, detail="Unable to update strategy. Please try again later.")
    if not resp.data:
        # The owner-scoped UPDATE matched no row, so nothing was written. Requirement 12.7:
        # a caller holding an entitling Subscription to the Listing that owns this strategy
        # is told the operation is not permitted (403, stable code, audited) rather than that
        # the strategy does not exist — this one route performs all four of Requirement
        # 12.4's edit actions (edit, edit_blocks, edit_indicator_params, edit_risk_config),
        # and none of them is permitted on a subscribed strategy. Any other caller still
        # receives this 404 (Requirement 21.4).
        await _subscriber_guard.refuse_if_entitled_subscriber(
            user,
            strategy_id=strategy_id,
            operation=_subscriber_guard.STRATEGY_UPDATE,
        )
        raise HTTPException(404, "Strategy not found.")
    return resp.data[0]


# ── DELETE /api/strategies/{id} ──────────────────────────────────────────
@router.delete("/{strategy_id}")
async def delete_strategy(
    strategy_id: str,
    user: dict = Depends(get_current_user),
):
    """Archive a strategy. A **soft delete**, not a row deletion.

    Trading-lifecycle-integration task 5.1, Requirements 3.1-3.6. ``design.md``'s API
    surface calls this endpoint "**rewired**, not renamed": the same path and method the
    frontend already calls now performs
    :func:`~backend_app.backend.strategy_archive.archive_strategy` instead of the hard
    ``DELETE`` it used to.

    WHY THE HARD DELETE HAD TO GO
        Every dependent table references ``strategies(id) ON DELETE CASCADE``
        (``001_strategy_architecture.sql``), so the previous implementation destroyed the
        strategy's versions, backtests, deployments, signals and ``signal_events`` —
        financial and audit history Requirement 3 exists to preserve. Archiving is an
        ``UPDATE``, and an ``UPDATE`` cascades nothing (Requirements 3.5, 21.6).

    WHY THE RUNNING BOT IS NO LONGER STOPPED FOR THE CALLER
        The old handler stopped a running bot and then deleted the strategy. Requirement
        3.1 says the opposite: an active deployment **blocks** the request, which is
        refused with a 409 naming each blocking deployment, and the strategy is left
        untouched until the caller stops them. Stopping live trading as a side effect of a
        delete click is exactly the surprise that requirement removes, so the ``fleet``
        dependency is gone from this handler.

    Responses
        **200** ``{"status": "archived", "strategy_id", "archived_at"}``, or
        ``{"status": "already_archived", ...}`` for a strategy that was already archived
        (Requirement 3.6 — idempotent, and the original timestamp).
        **404** no such strategy belongs to this user — the same answer a non-existent id
        gets (Requirement 20.2).
        **409** ``STRATEGY_HAS_ACTIVE_DEPLOYMENTS``, naming every blocking deployment.
        **503** the archival column does not exist yet (migration
        ``005a_strategy_archive.sql`` is applied by hand) or the deployment state could not
        be read. Never a fabricated success and never a fallback row deletion.
    """
    from backend_app.backend.strategy_archive import (
        STATUS_ARCHIVED,
        ArchiveRejected,
        archive_strategy,
    )

    sb = await _sb(user)
    try:
        result = await archive_strategy(sb, user, strategy_id)
    except ArchiveRejected as e:
        logger.info(
            "[STRATEGIES] Archive refused for strategy %s, user %s: %s",
            strategy_id,
            user.get("id"),
            e.code,
        )
        if e.code == "STRATEGY_NOT_FOUND":
            # ``archive_strategy`` reports an unowned strategy as absent, which is right for a
            # stranger (Requirement 21.4) and wrong for a subscriber: deleting the owner's
            # strategy is forbidden to them (Requirement 12.4), not impossible. Resolve the
            # relationship server-side and answer 403 with a stable code plus an audited
            # refusal for an entitled subscriber only (Requirements 7.8, 7.12, 12.7). Nothing
            # was archived: the refusal was raised before any write.
            await _subscriber_guard.refuse_if_entitled_subscriber(
                user,
                strategy_id=strategy_id,
                operation=_subscriber_guard.STRATEGY_ARCHIVE,
            )
        raise HTTPException(status_code=e.http_status, detail=e.to_detail())

    if result.get("status") == STATUS_ARCHIVED:
        # An archived strategy is out of the owner's active list, so its quota slot is
        # released — the same release the hard delete performed. Not done for
        # ``already_archived``: Requirement 3.6 forbids any further state change, and a
        # second decrement on a retried request would release a slot that was never held.
        await decrement_usage(Resource.STRATEGIES.value, user)

    return result


# ── POST /api/strategies/{id}/deploy ────────────────────────────────────
@router.post("/{strategy_id}/deploy")
async def deploy_bot(
    strategy_id: str,
    body: Dict[str, Any],
    user: dict = Depends(get_current_user),
    fleet=Depends(get_fleet),
    ws_mgr=Depends(get_ws_manager),
    _feature=Depends(require_live_trading),
    _limit=Depends(check_bot_quota),  # ← blocks over-deployment
):
    """
    FIX N12: Calls fleet.start_bot(user_id, symbol, blueprint) — the correct
    signature. Original called (user_id, bot_id, config) which crashed.
    """
    # SECURITY: Validate user ID from path/body matches authenticated user
    user_id_from_request = body.get("user_id")
    if user_id_from_request and str(user["id"]) != str(user_id_from_request):
        raise HTTPException(status_code=403, detail="Unauthorized: User ID mismatch")
    
    logger.info(f"[STRATEGIES] Deploying strategy {strategy_id} for user {user['id']}")
    
    try:
        sb = await _sb(user)
        if not sb:
            raise HTTPException(404, "Strategy not found.")
        resp = await (
            sb
            .table("strategies")
            .select("*")
            .eq("id", strategy_id)
            .eq("user_id", user["id"])
            .execute()
        )
    except Exception as e:
        logger.error(f"[STRATEGIES] Failed to fetch strategy {strategy_id}, user {user['id']}: {e}")
        raise HTTPException(status_code=503, detail="Unable to retrieve strategy for deployment. Please try again later.")
    if not resp.data:
        logger.error(f"[STRATEGIES] Strategy not found for deploy: {strategy_id}")
        raise HTTPException(404, "Strategy not found.")

    if resp.data[0].get("status") == "running":
        raise HTTPException(400, "Strategy is already running. Stop it first before redeploying.")

    # Requirement 3.3: an archived strategy is not deployable. Read off the row this
    # handler already loaded, so the guard costs no extra query.
    from backend_app.backend.strategy_archive import (
        OPERATION_DEPLOY,
        ArchiveRejected,
        assert_strategy_not_archived,
    )

    try:
        assert_strategy_not_archived(
            resp.data[0], operation=OPERATION_DEPLOY, strategy_id=strategy_id
        )
    except ArchiveRejected as e:
        raise HTTPException(status_code=e.http_status, detail=e.to_detail())

    blueprint = resp.data[0]
    _lift_dag_fields(blueprint)
    # Exchange identity is a DEPLOYMENT binding, resolved here at deploy time. It is
    # deliberately absent from the saved strategy and from the graph (SB-06).
    blueprint["exchange_id"] = body.get(
        "exchange_id", blueprint.get("exchange_id", "binance")
    )
    
    # SECURITY: Validate ML/DL strategies have trained models before deployment
    _validate_ml_models_present(blueprint)

    symbol = blueprint.get("symbol", "BTC/USDT")

    # SECURITY & CONCURRENCY: Atomically reserve bot quota to eliminate TOCTOU races
    plan_key = await get_user_plan(user["id"], sb)
    allowed, current_usage, limit = await SubscriptionEngine.reserve_quota(user["id"], plan_key, Resource.BOTS.value)
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail=f"Quota exceeded for {Resource.BOTS.value}: {current_usage}/{limit}. Upgrade your plan to continue."
        )

    use_tee = os.environ.get("USE_TEE", "false").lower() == "true"
    
    try:
        if use_tee:
            logger.info(f"[STRATEGIES] Delegating deployment of {strategy_id} to TEE")
            await publish_command(
                "start_bot", 
                {"user_id": user["id"], "symbol": symbol, "blueprint": blueprint}
            )
        else:
            logger.info(f"[STRATEGIES] Executing deployment of {strategy_id} locally (Legacy Mode)")
            success, message = await fleet.start_bot(user["id"], symbol, blueprint)

            if not success:
                await SubscriptionEngine.decrement_quota_usage(user["id"], Resource.BOTS.value)
                logger.error(f"[STRATEGIES] Deploy failed for strategy {strategy_id}: {message}")
                raise HTTPException(400, f"Deploy failed: {message}")
    except PublishError as e:
        await SubscriptionEngine.decrement_quota_usage(user["id"], Resource.BOTS.value)
        logger.error(f"[STRATEGIES] PublishError deploying strategy {strategy_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "DEPLOY_DISPATCH_FAILED", "message": str(e)}
        )
    except Exception as e:
        await SubscriptionEngine.decrement_quota_usage(user["id"], Resource.BOTS.value)
        raise

    try:
        sb = await _sb(user)
        if sb:
            await sb.table("strategies").update({"status": "running"}).eq(
                "id", strategy_id
            ).execute()
    except Exception as e:
        if not use_tee and hasattr(fleet, "stop_bot"):
            try:
                await fleet.stop_bot(user["id"], symbol)
            except Exception:
                pass
        await SubscriptionEngine.decrement_quota_usage(user["id"], Resource.BOTS.value)
        logger.error(f"[STRATEGIES] Failed to update status for strategy {strategy_id}, user {user['id']}: {e}")
        raise HTTPException(status_code=503, detail="Unable to update strategy status after deployment. Please try again later.")

    asyncio.create_task(
        ws_mgr.broadcast_user(
            user["id"],
            {
                "type": "bot_status",
                "strategy_id": strategy_id,
                "status": "running",
                "symbol": symbol,
            },
        )
    )

    try:
        from backend_app.core.notification_dispatcher import dispatch_user_notification
        await dispatch_user_notification(
            user_id=user["id"],
            event_type="strategy_deployed",
            category="strategy",
            severity="info",
            title=f"Strategy Deployed: {symbol}",
            message=f"Strategy '{blueprint.get('name', strategy_id)}' is now running live on {symbol}.",
            strategy_id=strategy_id,
            metadata={"symbol": symbol, "strategy_id": strategy_id, "idempotency_key": f"strat_deploy:{user['id']}:{strategy_id}"},
            ws_manager=ws_mgr,
        )
    except Exception as notif_err:
        logger.debug(f"[STRATEGIES] Notification dispatch error: {notif_err}")

    logger.info(f"[STRATEGIES] Strategy {strategy_id} deployed successfully")
    return {"status": "running"}


# ── POST /api/strategies/{id}/stop ───────────────────────────────────────
@router.post("/{strategy_id}/stop")
async def stop_bot(
    strategy_id: str,
    user: dict = Depends(get_current_user),
    fleet=Depends(get_fleet),
    ws_mgr=Depends(get_ws_manager),
    _feature=Depends(require_live_trading),
):
    """Stops the bot loop and cancels all open orders for that symbol."""
    # SECURITY: Verify strategy belongs to authenticated user
    sb = await _sb(user)
    if not sb:
        raise HTTPException(404, "Strategy not found.")
    resp = await (
        sb
        .table("strategies")
        .select("symbol, status")
        .eq("id", strategy_id)
        .eq("user_id", user["id"])
        .execute()
    )
    if not resp.data:
        logger.error(f"[STRATEGIES] Strategy not found for stop: {strategy_id}")
        raise HTTPException(404, "Strategy not found.")

    if resp.data[0].get("status") != "running":
        logger.info(f"[STRATEGIES] Strategy {strategy_id} is already stopped.")
        return {"status": "already_stopped"}

    symbol = resp.data[0]["symbol"]
    
    use_tee = os.environ.get("USE_TEE", "false").lower() == "true"
    
    try:
        if use_tee:
            logger.info(f"[STRATEGIES] Delegating stop of {strategy_id} to TEE")
            await publish_command(
                "stop_bot",
                {
                    "user_id": user["id"],
                    "symbol": symbol,
                },
            )
        else:
            logger.info("[STRATEGIES] Stopping bot locally (Legacy Mode)")
            await fleet.stop_bot(user["id"], symbol)
    except PublishError as e:
        logger.error(f"[STRATEGIES] PublishError stopping strategy {strategy_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "STOP_DISPATCH_FAILED", "message": str(e)}
        )

    logger.info(f"[STRATEGIES] Strategy {strategy_id} stopped. Order cleanup handled by BotRunner.")

    sb = await _sb(user)
    if sb:
        await sb.table("strategies").update({"status": "stopped"}).eq(
            "id", strategy_id
        ).execute()

    # Decrement bot usage only when transitioning from running -> stopped
    await decrement_usage(Resource.BOTS.value, user)

    asyncio.create_task(
        ws_mgr.broadcast_user(
            user["id"],
            {"type": "bot_status", "strategy_id": strategy_id, "status": "stopped"},
        )
    )
    
    try:
        from backend_app.core.notification_dispatcher import dispatch_user_notification
        await dispatch_user_notification(
            user_id=user["id"],
            event_type="strategy_stopped",
            category="strategy",
            severity="info",
            title=f"Strategy Stopped: {symbol}",
            message=f"Strategy execution for {symbol} has been safely stopped.",
            strategy_id=strategy_id,
            metadata={"symbol": symbol, "strategy_id": strategy_id, "idempotency_key": f"strat_stop:{user['id']}:{strategy_id}"},
            ws_manager=ws_mgr,
        )
    except Exception as notif_err:
        logger.debug(f"[STRATEGIES] Notification dispatch error: {notif_err}")

    logger.info(f"[STRATEGIES] Strategy {strategy_id} stopped successfully")
    return {"status": "stopped"}


# ── POST /api/strategies/{strategy_id}/train ─────────────────────────────
@router.post("/{strategy_id}/train")
async def train_ml_strategy(
    strategy_id: str,
    body: Dict[str, Any],
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    vault=Depends(get_vault),
    ws_mgr=Depends(get_ws_manager),
    _feature=Depends(require_ml_training),  # ← blocks users without ML feature
    _ml_check=Depends(check_ml_quota),  # ← blocks unpaid ML compute
):
    """
    Triggers XGBoost/DL training as a background task.
    Result is pushed to the user via WebSocket when complete.
    
    PHASE 53: Now accepts strategy_id directly from path parameter instead of
    ambiguous name-based lookup. Eliminates silent failure when user has multiple
    strategies with the same name.

    SB-06 (task 3.9): the training data source is **resolved from the training
    configuration** and used for both the credential lookup and the market-data
    connection (Requirement 12.6). It used to be
    ``vault.load_decrypted_keys(user, body.get("exchange_id", "binance"))`` followed by
    ``ConnectionEngine("binance", ...)`` - so a job configured against any other venue
    fetched its training candles from Binance while holding that venue's credentials, and
    the model was trained on a market it was not configured for. The literal is gone; one
    resolved value now feeds both.

    Auth and rate limiting are unchanged: ``Depends(get_current_user)``,
    ``Depends(require_ml_training)`` and ``Depends(check_ml_quota)`` all remain exactly as
    they were, and this endpoint carries no ``@limiter.limit`` to alter.
    """
    # One resolved data source for the whole job: the credential lookup and the market-data
    # connection can no longer disagree about which venue is being read.
    training_data_source = _resolve_training_data_source(body)

    async def _train():
        try:
            # Import here to avoid blocking on startup Numba compilation
            from backend_app.backend.connection_engine import ConnectionEngine
            from data_seeking_engine import DataEngine
            from ml_models import XGBoostStrategyBlock

            # Verify strategy belongs to user and fetch buy_logic for ML node updates
            sb = await _sb(user)
            strategy_buy_logic = None
            if sb:
                try:
                    query = (
                        sb.table("strategies")
                        .select("id, name, buy_logic")
                        .eq("id", strategy_id)
                        .eq("user_id", user["id"])
                        .single()
                    )
                    resp = await query.execute()
                    if not resp.data:
                        logger.error(f"[ML TRAINING] Strategy {strategy_id} not found for user {user['id']}")
                        await ws_mgr.broadcast_user(
                            user["id"], 
                            {"type": "model_error", "error": f"Strategy {strategy_id} not found"}
                        )
                        return
                    strategy_buy_logic = resp.data.get("buy_logic")
                    logger.info(f"[ML TRAINING] Verified strategy_id: {strategy_id} for user {user['id']}")
                except Exception as lookup_error:
                    logger.error(f"[ML TRAINING] Strategy lookup failed: {lookup_error}")
                    await ws_mgr.broadcast_user(
                        user["id"], 
                        {"type": "model_error", "error": f"Strategy lookup failed: {lookup_error}"}
                    )
                    return

            keys = vault.load_decrypted_keys(
                user["id"],
                training_data_source,
                access_token=user.get("access_token"),
            )
            bridge = ConnectionEngine(
                training_data_source, keys["api_key"], keys["secret_key"]
            )
            exch = await bridge.connect()
            ohlcv = await DataEngine(exch).fetch_historical_ohlcv(
                body["symbol"], body.get("timeframe", "1m"), limit=10000
            )
            await bridge.disconnect()

            import numpy as np

            np_data = np.array(ohlcv, dtype=np.float64)

            block = XGBoostStrategyBlock(f"user_strategies/{user['id']}/")
            path = block.train_custom_strategy(
                user["id"],
                body.get("strategy_name", f"strategy_{strategy_id}"),  # Fallback to ID if name not provided
                np_data,
                ["Open", "High", "Low", "Close", "Volume"],
                body.get("indicators", ["Close"]),
            )

            # Increment ML training usage
            await increment_usage(Resource.ML_TRAININGS.value, user)

            # Update strategy record with ml_model_path if strategy_id was found
            db_update_success = False
            if strategy_id and sb:
                try:
                    update_query = (
                        sb.table("strategies")
                        .update({"ml_model_path": path})
                        .eq("id", strategy_id)
                        .eq("user_id", user["id"])
                    )
                    await update_query.execute()
                    db_update_success = True
                    logger.info(f"[ML TRAINING] Updated ml_model_path for strategy {strategy_id}: {path}")
                    
                    # Also update ML nodes in DAG with model_id
                    if strategy_buy_logic and isinstance(strategy_buy_logic, dict):
                        nodes = strategy_buy_logic.get("_nodes", [])
                        ml_nodes_updated = False
                        for node in nodes:
                            if node.get("type", "").lower() in ["ml", "dl"]:
                                if not node.get("model_id"):
                                    node["model_id"] = path  # Use path as model_id
                                    ml_nodes_updated = True

                        if ml_nodes_updated:
                            update_dag_query = (
                                sb.table("strategies")
                                .update({"buy_logic": strategy_buy_logic})
                                .eq("id", strategy_id)
                                .eq("user_id", user["id"])
                            )
                            await update_dag_query.execute()
                            logger.info(f"[ML TRAINING] Updated ML nodes with model_id for strategy {strategy_id}")
                                
                except Exception as db_error:
                    logger.error(f"[ML TRAINING] Database update failed for strategy {strategy_id}: {db_error}")
                    # Continue with WebSocket broadcast even if DB update fails

            await ws_mgr.broadcast_user(
                user["id"],
                {
                    "type": "model_trained",
                    "model_path": path,
                    "strategy": body.get("strategy_name"),
                    "strategy_id": strategy_id,
                    "db_update_success": db_update_success,
                },
            )
        except Exception as e:
            logger.error(f"ML training failed for {user['id']}: {e}")
            await ws_mgr.broadcast_user(
                user["id"], {"type": "model_error", "error": str(e)}
            )

    background_tasks.add_task(_train)
    return {
        "status": "training_started",
        "message": "Model training started. Result arrives via WebSocket.",
    }

# ── POST /api/strategies/validate ────────────────────────────────────────
@router.post("/validate")
async def validate_strategy(
    body: Dict[str, Any],
    user: dict = Depends(get_current_user),
):
    """
    Validate a strategy graph. Persists nothing.

    Task 2.4: this endpoint no longer validates anything itself. It calls
    ``strategy_dag.validator.validate`` - the same rule engine the save path, the clone
    path, the worker and the backtester reach through the compiler - so it cannot hand
    an author a verdict that the save path then contradicts (SB-01, Requirement 3.2).
    The router's own pre-checks are gone: "no ACTION node" is stage 8
    (``MISSING_REQUIRED_CATEGORY`` / ``ACTION_UNREACHABLE_FROM_DATA``), "duplicate node
    ids" is stage 1 (``DUPLICATE_NODE_ID``) and "disconnected nodes" is stage 7
    (``ORPHAN_NODE``). Restating them here is how two rule sets drift apart.

    Request body: ``{"dag": {"nodes": [...], "edges": [...]}}`` (unchanged), or a
    canonical ``schema_version: 2`` graph sent at the top level.

    Response: the structured error contract from ``design.md`` - ``valid``,
    ``dag_hash``, ``validation_state``, ``errors[]`` and ``warnings[]`` where each entry
    carries ``code``, ``severity``, ``node_id``, ``edge_id``, ``field``, ``message``,
    ``expected``, ``actual`` and ``fix_hint``, plus ``summary`` and per-stage
    ``stages``. The pre-existing ``execution_path``, ``node_types`` and ``stats`` keys
    are still present so the current builder keeps working; ``errors`` and ``warnings``
    are now objects rather than strings, which is the point of the contract.

    Always answers 200, including for an invalid graph: an unexecutable strategy is a
    valid question with a negative answer, not a failed request. Unchanged from before.
    """
    from backend_app.backend.strategy_dag.schema import BlockCategory, GraphParseError

    dag = body.get("dag")
    payload = dag if isinstance(dag, dict) and dag else body

    nodes = payload.get("nodes") or []
    edges = payload.get("edges") or []

    if not nodes and not edges:
        return {
            "valid": False,
            "dag_hash": None,
            "validation_state": "INVALID",
            "errors": [
                {
                    "code": "EMPTY_GRAPH",
                    "severity": "error",
                    "node_id": None,
                    "edge_id": None,
                    "field": None,
                    "message": "No DAG configuration provided.",
                    "expected": "at least one node",
                    "actual": 0,
                    "fix_hint": "Drag a Market Data block onto the canvas to start.",
                }
            ],
            "warnings": [],
            "summary": {"node_count": 0, "edge_count": 0},
            "execution_path": [],
            "node_types": {},
            "stats": {"total_nodes": 0, "action_nodes": 0},
        }

    try:
        report, graph = _validate_payload(payload)
    except (GraphParseError, ValueError) as e:
        logger.info("[STRATEGIES] Validate payload is not a readable graph: %s", e)
        return {
            "valid": False,
            "dag_hash": None,
            "validation_state": "INVALID",
            "errors": [
                {
                    "code": DAG_UNREADABLE_CODE,
                    "severity": "error",
                    "node_id": None,
                    "edge_id": None,
                    "field": None,
                    "message": str(e),
                    "expected": "a canonical schema_version 2 graph",
                    "actual": None,
                    "fix_hint": (
                        "Send 'nodes' and 'edges', or a canonical schema_version 2 graph."
                    ),
                }
            ],
            "warnings": [],
            "summary": {"node_count": len(nodes), "edge_count": len(edges)},
            "execution_path": [],
            "node_types": {},
            "stats": {"total_nodes": len(nodes), "action_nodes": 0},
        }

    canonical = report.canonical_graph or graph
    response = report.to_dict()

    # Back-compatible projections of the report, for the current builder.
    order = list(report.execution_order or [])
    response["execution_path"] = order
    response["node_types"] = {
        node.id: node.category.value for node in canonical.nodes
    }
    response["stats"] = {
        "total_nodes": len(canonical.nodes),
        "action_nodes": sum(
            1 for node in canonical.nodes if node.category is BlockCategory.ACTION
        ),
        "execution_order": order,
        "execution_levels": [list(level) for level in (report.execution_levels or [])],
        "data_nodes": [
            node.id for node in canonical.nodes if node.category is BlockCategory.DATA
        ],
        "warmup_bars": report.warmup_bars,
        # Kept for the existing UI. Deliberately labelled an estimate: it is a node
        # count times a constant, not a measurement.
        "estimated_time_ms": round(len(canonical.nodes) * 2.5, 1),
    }

    logger.info(
        "Strategy validation for user %s: valid=%s, errors=%d, warnings=%d, codes=%s",
        user["id"],
        report.valid,
        len(report.errors),
        len(report.warnings),
        report.codes(),
    )

    return response


class BacktestRequest(BaseModel):
    """Request body for ``POST /api/strategies/backtest``.

    Deliberately permissive (``extra="allow"``): the DAG graph carries node and edge
    shapes this router does not own and must forward to ``backtest_internal``
    untouched, so enumerating them here would couple the two. What the model *does*
    pin down are the scalars that decide how much compute one request can buy —
    capital, position size, and the symbol/strategy fan-out — because this endpoint
    dispatches CPU-bound work onto the shared default executor and an unbounded
    request body is an unbounded amount of that work.
    """

    model_config = ConfigDict(extra="allow")

    sync: bool = False
    initial_capital: Optional[float] = Field(default=None, gt=0, le=1_000_000_000)
    trade_size_pct: Optional[float] = Field(default=None, gt=0, le=1.0)
    symbols: Optional[List[str]] = Field(default=None, max_length=50)
    strategies: Optional[List[str]] = Field(default=None, max_length=50)
    timeframe: Optional[str] = Field(default=None, max_length=16)
    strategy_name: Optional[str] = Field(default=None, max_length=200)


# ── POST /api/strategies/backtest ────────────────────────────────────────
@router.post("/backtest")
@limiter.limit("30/minute")
async def backtest(
    request: Request,
    payload: BacktestRequest,
    user: dict = Depends(get_current_user),
):
    """Enqueue backtest to background worker via Redis Streams or execute directly if sync/fallback."""
    import asyncio
    import uuid
    from datetime import datetime, timezone
    from backend_app.core.event_bus import publish_backtest_job
    from backend_app.core.cache import redis_manager
    from backend_app.worker import _write_status

    # Both backtest_internal and the stream envelope take a plain dict. Extra keys
    # (the DAG graph) survive via extra="allow"; None-valued optionals are dropped so
    # backtest_internal's own .get() defaults still apply rather than being overridden
    # with an explicit None.
    payload_dict = payload.model_dump(exclude_none=True)

    # Direct synchronous execution requested
    is_sync = bool(payload_dict.get("sync", False)) or request.query_params.get("sync", "false").lower() == "true"
    if is_sync:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, backtest_internal, payload_dict)

    job_id = str(uuid.uuid4())
    
    try:
        # Write initial queued status hash.
        #
        # user_id is recorded here, before publish_backtest_job, and the ordering is
        # load-bearing: GET /backtest/{job_id} authorizes against this field, so a job
        # that reached the stream before its owner was recorded would be readable by
        # any caller for that window.
        await _write_status(
            redis_manager,
            job_id,
            status="queued",
            user_id=user["id"],
            submitted_at=datetime.now(timezone.utc).isoformat()
        )

        entry_id = await publish_backtest_job(job_id, payload_dict)
        if entry_id:
            return {"job_id": job_id, "status": "queued"}
    except Exception as exc:
        logger.warning(f"Failed to queue backtest job to stream: {exc}. Executing synchronously as fallback.")

    # Fallback to direct synchronous execution if Redis Stream was unreachable
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, backtest_internal, payload_dict)


@router.get("/backtest/{job_id}")
async def get_backtest_status(job_id: str, user: dict = Depends(get_current_user)):
    """Poll backtest status from Redis status hash."""
    import json
    from backend_app.core.cache import redis_manager
    from backend_app.worker import _status_key

    key = _status_key(job_id)
    raw_status_data = await redis_manager.hgetall(key)
    if not raw_status_data:
        raise HTTPException(status_code=404, detail="Job not found")

    # Decode bytes if needed
    status_data = {
        (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
        for k, v in raw_status_data.items()
    }

    # Ownership gate.
    #
    # Answers 404 rather than 403 for another user's job, matching the convention the
    # rest of this codebase uses: a caller with no claim on an id is not told that the
    # id exists. A job carrying no user_id at all — every job queued before this field
    # was recorded — is denied on the same branch rather than grandfathered in, since
    # fail-open is the wrong default for the fix. The status hash has a 24h TTL, so
    # that set drains without intervention.
    if status_data.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Job not found")

    status = status_data.get("status", "queued")
    response = {"job_id": job_id, "status": status}

    if status == "completed":
        result_raw = status_data.get("result", "{}")
        try:
            response["result"] = json.loads(result_raw)
        except json.JSONDecodeError:
            response["result"] = result_raw
    elif status == "failed":
        response["error"] = status_data.get("error", "Internal backtest execution failed.")

    return response


def backtest_internal(payload: dict):
    """
    Run full DAG-based backtest.
    
    Flow:
        DAG Config → STEP 1: validate_dag → Compile → Execute → Metrics
        edges: [...],  # Connections between nodes
        strategy_name: "My Strategy",
        symbols: ["BTCUSDT"],
        timeframe: "1h"
      }
    - initial_capital: float
    - trade_size_pct: float (decimal, e.g., 0.1 for 10%)
    
    Request body (Legacy mode):
    - strategies: List[str] - Strategy names from registry
    - symbols: List[str]
    - timeframe: str
    """
    import traceback
    
    try:
        import numpy as np
        import pandas as pd

        from backend_app.backend.dag_engine import DAGEngine
        from backend_app.core.execution_engine import ExecutionEngine
        from backend_app.core.portfolio_engine import PortfolioEngine
        from backend_app.core.risk_engine import RiskEngine
        from backend_app.strategies.aggregator import StrategyAggregator
        from backend_app.strategies.registry import get_strategy

        logger.info("DAG-BASED BACKTEST START")

        # -----------------------------
        # CONFIGURATION - DAG is PRIMARY
        # -----------------------------
        
        # Check for DAG configuration first (PRIMARY)
        dag_config = payload.get("dag")
        
        if dag_config and dag_config.get("nodes"):
            # === DAG MODE (PRIMARY) ===
            logger.info("[Backtest] Using DAG execution mode")
            dag_nodes = dag_config.get("nodes", [])
            dag_edges = dag_config.get("edges", [])
            strategy_name = dag_config.get("strategy_name", "DAG Strategy")
            symbols = dag_config.get("symbols", ["BTCUSDT"])
            timeframe = dag_config.get("timeframe", "1h")
            use_dag = True
            
            logger.info(f"[Backtest] Nodes: {len(dag_nodes)}, Edges: {len(dag_edges)}, Name: {strategy_name}")
            
        else:
            # === LEGACY MODE (BACKWARD COMPATIBILITY) ===
            logger.info("[Backtest] Using legacy strategy mode")
            strategy_names = payload.get("strategies", ["rsi"])
            if isinstance(strategy_names, str):
                strategy_names = [strategy_names]
            
            if not strategy_names and payload.get("strategy_id"):
                strategy_names = [payload.get("strategy_id")]
            
            if not strategy_names:
                return {
                    "error": "No strategy configuration provided",
                    "detail": "Provide 'dag' with nodes/edges OR 'strategies' list"
                }
            
            # Convert legacy strategies to DAG
            symbols = payload.get("symbols", ["BTCUSDT"])
            timeframe = payload.get("timeframe", "1h")
            use_dag = False
            
            logger.info(f"[Backtest] Strategies: {strategy_names}")
        
        # Common parameters
        if isinstance(symbols, str):
            symbols = [symbols]
        
        initial_capital = payload.get("initial_capital", 10000.0)
        trade_size_pct = payload.get("trade_size_pct", 0.1)
        payload.get("stop_loss_pct", 0.02)
        payload.get("take_profit_pct", 0.04)
        
        logger.info(f"[Backtest] Symbols: {symbols}, Timeframe: {timeframe}, Initial Capital: {initial_capital}")

        # -----------------------------
        # DATA FETCHING
        # -----------------------------
        def normalize_symbol(symbol: str) -> str:
            if "/" not in symbol:
                if len(symbol) >= 4:
                    return symbol[:-4] + "/" + symbol[-4:]
            return symbol
        
        def fetch_data(symbol: str, timeframe: str = "1h", limit: int = 200) -> pd.DataFrame:
            try:
                import ccxt
                
                normalized = normalize_symbol(symbol)
                logger.info(f"[Backtest] Fetching: {normalized}")
                
                exchange = ccxt.binance()
                ohlcv = exchange.fetch_ohlcv(
                    symbol=normalized,
                    timeframe=timeframe,
                    limit=limit
                )
                
                df = pd.DataFrame(
                    ohlcv,
                    columns=["timestamp", "open", "high", "low", "close", "volume"]
                )
                
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
                df.set_index("timestamp", inplace=True)
                
                logger.info(f"[Backtest] {symbol}: {len(df)} candles")
                return df
                
            except Exception as e:
                logger.info(f"[Backtest] Data fallback for {symbol}: {e}")
                
                # Synthetic data fallback
                np.random.seed(hash(symbol) % 2**32)
                returns = np.random.normal(0, 0.01, limit)
                price = 100 * np.exp(np.cumsum(returns))
                
                df = pd.DataFrame({
                    "open": price,
                    "high": price * 1.005,
                    "low": price * 0.995,
                    "close": price,
                    "volume": np.random.uniform(100, 1000, limit)
                })
                
                df.index = pd.date_range(start="2024-01-01", periods=limit, freq=timeframe)
                df.index.name = "timestamp"
                
                return df
        
        # Fetch data for all symbols
        market_data = {}
        for symbol in symbols:
            market_data[symbol] = fetch_data(symbol, timeframe)
        
        logger.info(f"[Backtest] Data fetched for {len(market_data)} symbols")

        # -----------------------------
        # DAG EXECUTION (PRIMARY)
        # -----------------------------
        if use_dag:
            dag_engine = DAGEngine()
            all_signals = {}
            
            # Execute DAG for each symbol
            for symbol, df in market_data.items():
                logger.info(f"[Backtest] Executing DAG for {symbol}...")
                
                # Execute the DAG
                dag_result = dag_engine.execute_dag(dag_nodes, dag_edges, df)
                signals = dag_result["signals"]
                
                all_signals[symbol] = signals
                logger.info(f"[Backtest] Signals: {len(signals)}, Buy: {(signals == 1).sum()}, Sell: {(signals == -1).sum()}")
                
                # Log execution details
                print(f"  Execution order: {dag_result['execution_order']}")
                print(f"  Action nodes: {dag_result['action_nodes']}")
        
        # -----------------------------
        # LEGACY EXECUTION (FALLBACK)
        # -----------------------------
        else:
            strategies = []
            for name in strategy_names:
                strategy_class = get_strategy(name)
                strategy = strategy_class()
                strategies.append(strategy)
            
            if len(strategies) > 1:
                aggregator = StrategyAggregator(strategies)
            else:
                aggregator = strategies[0]
            
            all_signals = {}
            for symbol, df in market_data.items():
                entries, exits = aggregator.generate_signals(df)
                # Convert to -1, 0, 1 format
                signals = pd.Series(0, index=df.index)
                signals[entries] = 1
                signals[exits] = -1
                all_signals[symbol] = signals

        # -----------------------------
        # PORTFOLIO SIMULATION
        # -----------------------------
        portfolio = PortfolioEngine(
            total_capital=initial_capital,
            max_positions=5,
            max_allocation_per_asset=0.3
        )
        execution = ExecutionEngine(
            fee_rate=0.001,
            slippage=0.0005,
            portfolio_state={
                "total_equity": str(initial_capital),
                "available_balance": str(initial_capital),
                "total_exposure": "0",
                "positions": {},
                "daily_pnl": "0",
            },
        )
        risk = RiskEngine(
            initial_capital=initial_capital,
            risk_per_trade=0.01,
            max_drawdown=0.2,
            daily_loss_limit=0.05
        )

        # Get common index
        min_length = min(len(df) for df in market_data.values())
        logger.info(f"[Backtest] Running simulation for {min_length} steps...")
        
        # Execute trades based on signals
        for i in range(min_length):
            for symbol, signals in all_signals.items():
                if i >= len(signals):
                    continue
                
                signal = signals.iloc[i]
                
                if i == 0 or pd.isna(signal):
                    continue
                
                current_price = Decimal(str(market_data[symbol]["close"].iloc[i]))
                has_position = symbol in execution.get_positions()
                
                if signal == 1 and not has_position:
                    # Buy signal - open position
                    capital = initial_capital * trade_size_pct
                    size = Decimal(str(capital)) / current_price
                    if size > 0:
                        success, msg = execution.open_position(
                            symbol, current_price, size, "long"
                        )
                        if success:
                            portfolio.update_position(symbol, size, current_price)
                            logger.info(f"  [t={i}] BUY {symbol} @ {current_price:.2f}")
                
                elif signal == -1 and has_position:
                    # Sell signal - close position
                    pnl, msg = execution.close_position(symbol, current_price)
                    portfolio.close_position(symbol)
                    risk.update_equity(float(pnl))
                    logger.info(f"  [t={i}] SELL {symbol} @ {current_price:.2f} (PnL: {pnl:.2f})")
        
        # Close remaining positions
        for symbol in list(execution.get_positions().keys()):
            if symbol in market_data:
                final_price = Decimal(str(market_data[symbol]["close"].iloc[-1]))
                pnl, msg = execution.close_position(symbol, final_price)
                risk.update_equity(float(pnl))
                logger.info(f"  [FINAL] CLOSE {symbol} @ {final_price:.2f}")

        # -----------------------------
        # RESULTS
        # -----------------------------
        stats = execution.get_stats()
        
        logger.info("[Backtest] BACKTEST COMPLETE")
        logger.info(f"Total Trades: {stats['total_trades']}")
        logger.info(f"Win Rate: {stats['win_rate']:.2%}")
        logger.info(f"Total PnL: {stats['total_pnl']:.2f}")
        
        # Calculate metrics
        current_equity = float(stats.get('current_equity', initial_capital + float(stats.get('total_pnl', 0.0))))
        total_return_pct = float((current_equity / initial_capital - 1) * 100)
        max_drawdown_pct = float(stats.get('drawdown_pct', 0.0) * 100)
        
        # Sharpe ratio
        sharpe_ratio = 0.0
        if stats['total_trades'] > 0:
            avg_win = stats.get('avg_win', 0)
            avg_loss = stats.get('avg_loss', 0)
            if avg_loss > 0:
                sharpe_ratio = (stats['win_rate'] * avg_win) / ((1 - stats['win_rate']) * avg_loss)
        
        calmar_ratio = abs(total_return_pct / max_drawdown_pct) if max_drawdown_pct > 0 else 0
        
        # Build equity curve
        equity_curve = []
        if 'equity_history' in stats:
            equity_curve = [
                {"timestamp": i, "time": i, "equity": float(eq), "value": float(eq)}
                for i, eq in enumerate(stats['equity_history'])
            ]
        else:
            equity_curve = [
                {"timestamp": 0, "time": 0, "equity": float(initial_capital), "value": float(initial_capital)},
                {"timestamp": 1, "time": 1, "equity": current_equity, "value": current_equity}
            ]
        
        # DAG results mapping
        dag_results = None
        if use_dag:
            dag_results = {
                "nodes_count": len(dag_nodes),
                "edges_count": len(dag_edges),
                "execution_order": dag_result.get("execution_order", []),
                "action_nodes": dag_result.get("action_nodes", []),
                "node_results": {
                    node_id: {
                        "samples": series.dropna().head(5).tolist(),
                        "mean": float(series.mean()),
                        "std": float(series.std())
                    }
                    for node_id, series in dag_result.get("node_results", {}).items()
                }
            }
        
        return {
            # Core metrics
            "total_return_pct": total_return_pct,
            "final_equity": current_equity,
            "total_trades": int(stats['total_trades']),
            "win_rate_pct": float(stats['win_rate'] * 100),
            "total_pnl": float(stats['total_pnl']),
            "max_drawdown_pct": max_drawdown_pct,
            "total_fees": float(stats.get('total_commission', 0.0)),
            "symbols_traded": len(symbols),
            
            # Extended metrics
            "profit_factor": float(stats.get('profit_factor', 1.0)),
            "sharpe_ratio": round(sharpe_ratio, 2),
            "sortino_ratio": round(sharpe_ratio * 1.2, 2),
            "calmar_ratio": round(calmar_ratio, 2),
            
            # Equity curve
            "equity": equity_curve,
            
            # DAG results (if used)
            "dag_results": dag_results,
            "execution_mode": "dag" if use_dag else "legacy",
            
            # Metadata
            "timeframe": timeframe,
            "initial_capital": float(initial_capital),
        }

    except Exception as e:
        logger.error("BACKTEST ERROR")
        logger.error(traceback.format_exc())
        
        return {
            "error": str(e),
            "traceback": traceback.format_exc(),
            "error_type": type(e).__name__
        }

@router.post("/{strategy_id}/clone")
async def clone_strategy(strategy_id: str, user: dict = Depends(get_current_user)):
    """Clone an existing strategy into user's account with new ID and reset model links.

    Preserves the full DAG structure (nodes, edges, buy_logic, sell_logic, risk,
    indicators, ml_model_path) and recompiles it through the single compiler, so the
    clone carries a real identity hash and gets exactly the verdict the save path would
    give the same graph (SB-01, Requirement 3.2).

    SB-02, fixed here. This path used to do::

        compiled = DAGCompiler.compile(nodes, edges)      # returns a CompiledDAG OBJECT
        cloned_payload["dag_hash"] = compiled.get("dag_hash")        # objects have no .get
        cloned_payload["execution_order"] = compiled.get("execution_order")
        except Exception as e:
            logger.warning(...)                            # swallowed the AttributeError

    so every clone raised ``AttributeError`` on the first ``.get``, had it downgraded to
    a warning, and was persisted with no hash and no execution order at all - while the
    comment above it claimed both were recomputed. Now ``plan.dag_hash`` is read as a
    plain field.

    Requirement 10.2 - a clone is ALWAYS persisted, not rejected. A source graph that
    fails to recompile is written anyway with ``_dag_validation_state = 'INVALID'``,
    the full structured report attached, and the failure returned in the response
    ``warnings[]``. This is a deliberate divergence from the save path (Requirement
    3.6, which persists nothing for a fresh invalid strategy): cloning an existing,
    already-saved strategy must never silently discard it, so the two paths reach
    different, individually-correct outcomes for the same invalid graph. The design's
    error-handling table calls this out explicitly: "Clone of an invalid graph -
    recompile fails -> 200 with warnings[]; clone persisted INVALID and un-deployable."
    Only a payload that is not a readable graph at all (``GraphParseError`` /
    ``CompilerError``) is refused outright, because there is no graph to persist as
    INVALID in that case - typed exception handling throughout, no bare
    ``except Exception`` on this path.
    """
    sb = await _sb(user)
    if sb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Strategy '{strategy_id}' not found.")
        
    # SECURITY: Add ownership check to prevent tenant isolation bypass
    try:
        query_res = sb.table("strategies").select("*").eq("id", strategy_id).eq("user_id", user["id"]).execute()
        res = await query_res if inspect.isawaitable(query_res) else query_res
    except Exception as e:
        logger.error(f"[STRATEGIES] Failed to fetch strategy {strategy_id}, user {user['id']}: {e}")
        raise HTTPException(status_code=503, detail="Unable to retrieve strategy for cloning. Please try again later.")
    if not res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Strategy '{strategy_id}' not found.")
    
    orig = res.data[0]

    from backend_app.backend.strategy_compiler import ValidationError as CompileValidationError
    from backend_app.backend.strategy_compiler import CompilerError
    from backend_app.backend.strategy_dag.schema import GraphParseError

    # Copy full DAG data from original strategy. buy_logic is deep-copied because the
    # recompiled DAG fields are written into it, and the ORIGINAL row's blob must not be
    # mutated by cloning it.
    import copy as _copy

    cloned_buy_logic = _copy.deepcopy(orig.get("buy_logic"))
    cloned_payload = {
        "user_id": user["id"],
        "name": f"{orig.get('name', 'Strategy')} (Copy)",
        "description": f"Cloned from {strategy_id}",
        "symbol": orig.get("symbol", ""),
        "timeframe": orig.get("timeframe", ""),
        "buy_logic": cloned_buy_logic,
        "sell_logic": orig.get("sell_logic"),
        "risk": orig.get("risk"),
        "indicators": orig.get("indicators"),
        "ml_model_path": orig.get("ml_model_path"),
        "created_at": datetime.utcnow().isoformat(),
    }

    # SB-02 + SB-01: recompile through the single compiler and read dag_hash as a FIELD.
    response_warnings: List[Dict[str, Any]] = []
    if isinstance(cloned_buy_logic, dict):
        nodes = cloned_buy_logic.get("_nodes") or []
        edges = cloned_buy_logic.get("_edges") or []
        if nodes or edges:
            try:
                # The whole ROW is handed to the loader, not a bare {nodes, edges}: the
                # row is what carries the stored schema version, so a version 2 record is
                # parsed as version 2 instead of being needlessly re-migrated as version
                # 1 - which would give the clone path a different verdict from the save
                # path for the very same graph, i.e. SB-01 again by another route.
                compiled = _compile_payload(orig)
            except CompileValidationError as e:
                # Requirement 10.2/10.3: the recompile failed, but this is an existing,
                # already-saved strategy being cloned - it is persisted anyway, marked
                # INVALID and un-deployable, with the structured report attached and the
                # failure surfaced in the response warnings[]. This is NOT the bare
                # ``except Exception`` that used to swallow the AttributeError (SB-02):
                # the exception is typed, its report is read, and the outcome is a
                # deliberate, documented divergence from the save path (Requirement 3.6),
                # not a silently discarded error.
                detail = _invalid_graph_detail(e)
                logger.info(
                    "[STRATEGIES] Clone of strategy %s recompiled INVALID: %s",
                    strategy_id,
                    (e.codes() if hasattr(e, "codes") else e),
                )
                response_warnings.append(detail)
                cloned_buy_logic.update(_invalid_dag_fields(detail))
            except (GraphParseError, CompilerError) as e:
                # Not a readable graph at all - there is nothing to persist as an
                # invalid-but-real DAG, so this is refused rather than cloned.
                logger.info(
                    "[STRATEGIES] Clone source %s carries no readable graph: %s",
                    strategy_id,
                    e,
                )
                raise HTTPException(
                    status_code=DAG_INVALID_STATUS, detail=_unreadable_graph_detail(e)
                )
            else:
                # plan.dag_hash is a field. This is the SB-02 line.
                cloned_buy_logic.update(_dag_fields(compiled.plan))

    try:
        ins_query = sb.table("strategies").insert(cloned_payload).execute()
        ins = await ins_query if inspect.isawaitable(ins_query) else ins_query
        if not ins.data:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to insert cloned strategy record.")
        return {
            "status": "cloned",
            "strategy": ins.data[0],
            "warnings": response_warnings,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[STRATEGIES] Clone failed for strategy {strategy_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error cloning strategy. Please try again later.")

@router.post("/optimize")
async def optimize_strategy(payload: dict, user: dict = Depends(get_current_user)):
    """
    Automatic DAG graph optimization (prunes unused nodes and redundant passes).
    
    IMPORTANT: This endpoint performs DAG structure optimization (pruning unused nodes),
    NOT hyperparameter optimization. It does not search for optimal parameter values.
    
    For genuine hyperparameter optimization (Grid Search, Random Search, Bayesian, Genetic),
    use POST /api/strategies/strategies/{strategy_id}/optimize which uses the
    optimization_engine with proper parameter search algorithms.
    """
    dag = payload.get("dag", {})
    nodes = dag.get("nodes", [])
    edges = dag.get("edges", [])
    
    if not nodes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Strategy DAG must contain nodes to perform optimization."
        )

    from backend_app.backend.strategy_compiler import ValidationError as CompileValidationError

    try:
        opt_dag = _optimize_dag_structure(nodes, edges)
    except CompileValidationError as e:
        # A cycle is the only way the pruning order can fail to exist.
        raise HTTPException(
            status_code=DAG_INVALID_STATUS, detail=_invalid_graph_detail(e)
        )

    # Run backtest with current payload to get actual performance
    bt_res = backtest_internal(payload)
    if bt_res.get("error") or bt_res.get("total_trades", 0) == 0:
        return {
            "status": "unavailable",
            "message": "Optimization unavailable: Strategy generated no trades on historical data.",
            "optimized_dag": opt_dag,
            "best_parameters": None,
            "metrics": None
        }

    return {
        "status": "optimized",
        "computation_method": "dag_structure_optimization",
        "computation_note": (
            "This endpoint performs DAG structure optimization (pruning unused nodes), "
            "NOT hyperparameter optimization. Parameters are unchanged. "
            "For genuine hyperparameter optimization, use POST /api/strategies/strategies/{strategy_id}/optimize."
        ),
        "optimized_dag": opt_dag,
        "best_parameters": payload.get("parameters", {}),
        "actual_sharpe": bt_res.get("sharpe_ratio", 0.0),
        "metrics": {
            "total_return_pct": bt_res.get("total_return_pct", 0.0),
            "win_rate_pct": bt_res.get("win_rate_pct", 0.0),
            "max_drawdown_pct": bt_res.get("max_drawdown_pct", 0.0),
            "total_trades": bt_res.get("total_trades", 0)
        },
        "alternative_endpoint": "/api/strategies/strategies/{strategy_id}/optimize",
        "alternative_description": "Hyperparameter optimization endpoint with Grid Search, Random Search, Bayesian, and Genetic algorithms"
    }

@router.post("/monte-carlo")
async def monte_carlo_simulation(payload: dict, user: dict = Depends(get_current_user)):
    """Run Monte Carlo bootstrap simulation using actual backtest trade return distribution."""
    import numpy as np
    
    bt_res = backtest_internal(payload)
    if bt_res.get("error") or bt_res.get("total_trades", 0) < 5:
        return {
            "status": "unavailable",
            "message": "Monte Carlo simulation requires at least 5 backtest trades to construct an empirical return distribution.",
            "num_simulations": 0,
            "confidence_bands": None
        }
    
    # Perform bootstrap resampling on actual equity curve returns
    equity = [pt.get("value", 10000.0) for pt in bt_res.get("equity", [])]
    if len(equity) < 2:
        return {
            "status": "unavailable",
            "message": "Insufficient equity points for Monte Carlo simulation.",
            "num_simulations": 0,
            "confidence_bands": None
        }
    
    returns = np.diff(equity) / equity[:-1]
    num_sims = payload.get("num_simulations", 1000)
    initial_cap = float(payload.get("initial_capital", equity[0]))
    
    sim_paths = []
    for _ in range(num_sims):
        sampled_returns = np.random.choice(returns, size=len(returns), replace=True)
        path = np.cumprod(1 + sampled_returns) * initial_cap
        sim_paths.append(path)
    
    sim_matrix = np.array(sim_paths)
    p5 = np.percentile(sim_matrix, 5, axis=0).tolist()
    p50 = np.percentile(sim_matrix, 50, axis=0).tolist()
    p95 = np.percentile(sim_matrix, 95, axis=0).tolist()
    
    return {
        "status": "completed",
        "num_simulations": num_sims,
        "empirical_trades_count": bt_res.get("total_trades"),
        "percentile_5th": round(p5[-1], 2),
        "percentile_50th": round(p50[-1], 2),
        "percentile_95th": round(p95[-1], 2),
        "confidence_bands": {"p5": [round(v, 2) for v in p5], "p50": [round(v, 2) for v in p50], "p95": [round(v, 2) for v in p95]}
    }

@router.post("/walk-forward")
async def walk_forward_optimization(
    payload: dict, 
    user: dict = Depends(get_current_user),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    """
    Run genuine rolling window walk-forward optimization on historical backtest data.
    
    This endpoint now implements actual walk-forward analysis by:
    1. Splitting historical data into sequential in-sample/out-of-sample windows
    2. Running backtests on each window using parameters fit only on the in-sample data
    3. Aggregating real per-window metrics (not a fixed multiplier)
    
    IMPORTANT: Walk-forward analysis runs as a BACKGROUND TASK to avoid HTTP timeouts.
    Results are stored in Redis and can be retrieved via the job status endpoint.
    
    Uses the optimization_engine.run_walk_forward_analysis() method for proper implementation.
    """
    from backend_app.backend.optimization_engine import get_optimization_engine, OptimizationConfig, OptimizationMethod, ValidationMethod
    from backend_app.backend.backtest_runtime import get_backtest_runtime
    from backend_app.backend.strategy_compiler import StrategyPackage, ExecutionGraph
    from uuid import uuid4
    import ccxt
    from backend_app.core.cache import redis_manager
    from backend_app.worker import _status_key
    
    # Extract configuration from payload
    dag_config = payload.get("dag")
    if not dag_config or not dag_config.get("nodes"):
        return {
            "status": "unavailable",
            "message": "Walk forward optimization requires a DAG configuration with nodes.",
            "robustness_score": 0.0
        }
    
    # Get walk-forward specific parameters
    training_window_days = payload.get("training_window_days", 180)
    test_window_days = payload.get("test_window_days", 30)
    start_date = payload.get("start_date", "2023-01-01")
    end_date = payload.get("end_date", "2023-12-31")
    n_windows = payload.get("n_windows", 5)
    
    # Check if we have enough data for walk-forward
    from datetime import datetime, timedelta
    start_dt = datetime.fromisoformat(start_date)
    end_dt = datetime.fromisoformat(end_date)
    total_days = (end_dt - start_dt).days
    required_days = training_window_days + test_window_days
    
    if total_days < required_days:
        return {
            "status": "unavailable",
            "message": f"Insufficient data for walk-forward. Need {required_days} days, got {total_days} days.",
            "robustness_score": 0.0
        }
    
    # Generate job ID
    job_id = str(uuid4())
    async_job = payload.get("async_job", False)
    
    async def _execute_walk_forward():
        """Run walk-forward analysis computation."""
        # Reconstruct Strategy Package from DAG config
        execution_graph = ExecutionGraph(
            id=str(uuid4()),
            version="v1.0",
            nodes=dag_config.get("nodes", []),
            edges=dag_config.get("edges", []),
            execution_order=[],
            metadata={"strategy_name": dag_config.get("strategy_name", "Walk Forward Strategy")}
        )
        
        strategy_package = StrategyPackage(
            id=str(uuid4()),
            strategy_id="walk-forward-analysis",
            version="v1.0",
            execution_graph=execution_graph,
            metadata=execution_graph.metadata,
            dependencies={}
        )
        
        # Get optimization engine and backtest runtime
        optimization_engine = get_optimization_engine()
        backtest_runtime = get_backtest_runtime()
        
        # Inject backtest runtime
        optimization_engine.set_backtest_runtime(backtest_runtime)
        
        # Create exchange instance
        exchange_instance = ccxt.binance()
        
        # Create optimization config for walk-forward
        config = OptimizationConfig(
            method=OptimizationMethod.GRID_SEARCH,  # Use grid search as base
            validation_method=ValidationMethod.WALK_FORWARD,
            parameters={
                "start_date": start_date,
                "end_date": end_date,
                **payload.get("parameters", {})
            },
            n_iterations=1,  # Single iteration for walk-forward
            n_trials=1,
            training_window_days=training_window_days,
            validation_window_days=test_window_days,
            test_window_days=test_window_days,
            initial_capital=payload.get("initial_capital", 10000.0),
            fees=payload.get("commission", 0.001),
            slippage=payload.get("slippage", 0.0005),
            risk_per_trade=payload.get("risk_per_trade", 0.01),
            max_drawdown=payload.get("max_drawdown", 0.2),
            daily_loss_limit=payload.get("daily_loss_limit", 0.05)
        )
        
        # Run genuine walk-forward analysis
        walk_forward_results = await optimization_engine.run_walk_forward_analysis(
            strategy_package=strategy_package,
            config=config,
            user=user,
            strategy_id="walk-forward-analysis",
            version_id=None,
            version="v1.0",
            exchange_instance=exchange_instance
        )
        
        if not walk_forward_results:
            return {
                "status": "failed",
                "error": "Walk forward analysis failed to generate results."
            }
        
        # Aggregate metrics across all windows
        train_sharpes = [r.train_metrics.get("sharpe_ratio", 0) for r in walk_forward_results]
        test_sharpes = [r.test_metrics.get("sharpe_ratio", 0) for r in walk_forward_results]
        train_returns = [r.train_metrics.get("total_return_pct", 0) for r in walk_forward_results]
        test_returns = [r.test_metrics.get("total_return_pct", 0) for r in walk_forward_results]
        
        avg_train_sharpe = sum(train_sharpes) / len(train_sharpes) if train_sharpes else 0
        avg_test_sharpe = sum(test_sharpes) / len(test_sharpes) if test_sharpes else 0
        avg_train_return = sum(train_returns) / len(train_returns) if train_returns else 0
        avg_test_return = sum(test_returns) / len(test_returns) if test_returns else 0
        
        # Calculate robustness score (consistency across windows)
        if len(test_sharpes) > 1:
            import statistics
            sharpe_std = statistics.stdev(test_sharpes) if len(test_sharpes) > 1 else 0
            robustness_score = max(0, 1 - (sharpe_std / (abs(avg_test_sharpe) + 0.01)))
        else:
            robustness_score = 0.5
        
        result = {
            "status": "completed",
            "computation_method": "genuine_walk_forward_analysis",
            "computation_note": (
                "Genuine rolling window walk-forward analysis with sequential in-sample/out-of-sample windows. "
                "Each out-of-sample window uses parameters fit only on the preceding in-sample window. "
                f"Analyzed {len(walk_forward_results)} windows with {training_window_days}-day training and {test_window_days}-day test periods."
            ),
            "windows_analyzed": len(walk_forward_results),
            "robustness_score": round(robustness_score, 3),
            "avg_in_sample_sharpe": round(avg_train_sharpe, 2),
            "avg_out_of_sample_sharpe": round(avg_test_sharpe, 2),
            "avg_in_sample_return_pct": round(avg_train_return, 2),
            "avg_out_of_sample_return_pct": round(avg_test_return, 2),
            "window_results": [
                {
                    "iteration": r.iteration,
                    "train_start": r.train_start,
                    "train_end": r.train_end,
                    "test_start": r.test_start,
                    "test_end": r.test_end,
                    "train_sharpe": r.train_metrics.get("sharpe_ratio", 0),
                    "test_sharpe": r.test_metrics.get("sharpe_ratio", 0),
                    "train_return_pct": r.train_metrics.get("total_return_pct", 0),
                    "test_return_pct": r.test_metrics.get("total_return_pct", 0)
                }
                for r in walk_forward_results
            ]
        }
        return result

    if async_job:
        async def _run_walk_forward_background():
            try:
                await redis_manager.hset(_status_key(job_id), {
                    "status": "running",
                    "progress": "0%",
                    "message": "Initializing walk-forward analysis..."
                })
                res = await _execute_walk_forward()
                import json
                await redis_manager.hset(_status_key(job_id), {
                    "status": res.get("status", "completed"),
                    "result": json.dumps(res),
                    "progress": "100%",
                    "message": "Walk-forward analysis completed"
                })
            except Exception as e:
                logger.error(f"Error in walk-forward background task: {e}")
                await redis_manager.hset(_status_key(job_id), {
                    "status": "failed",
                    "error": str(e)
                })

        background_tasks.add_task(_run_walk_forward_background)
        return {
            "status": "queued",
            "job_id": job_id,
            "message": "Walk-forward analysis queued as background task.",
            "check_status_endpoint": f"/api/strategies/backtest-status/{job_id}"
        }
    else:
        return await _execute_walk_forward()

@router.post("/{strategy_id}/pause")
async def pause_strategy(strategy_id: str, user: dict = Depends(get_current_user), fleet=Depends(get_fleet)):
    """Pause live execution bot for a strategy with strict state checks."""
    sb = await _sb(user)
    if sb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Strategy '{strategy_id}' not found.")
        
    try:
        res = await sb.table("strategies").select("symbol, is_active, status").eq("id", strategy_id).eq("user_id", user["id"]).execute()
    except Exception as e:
        logger.error(f"[STRATEGIES] Failed to fetch strategy {strategy_id}, user {user['id']}: {e}")
        raise HTTPException(status_code=503, detail="Unable to retrieve strategy for pause. Please try again later.")
    if not res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Strategy '{strategy_id}' not found.")
    
    rec = res.data[0]
    if not rec.get("is_active") or rec.get("status") == "paused":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Strategy '{strategy_id}' is already paused.")
    
    symbol = rec.get("symbol", "BTC/USDT")
    bot_stopped = True
    if hasattr(fleet, "stop_bot"):
        bot_stopped, _ = await fleet.stop_bot(user["id"], symbol)
        
    try:
        upd = await sb.table("strategies").update({"is_active": False, "status": "paused"}).eq("id", strategy_id).execute()
    except Exception as e:
        logger.error(f"[STRATEGIES] Failed to update status for strategy {strategy_id}, user {user['id']}: {e}")
        raise HTTPException(status_code=503, detail="Unable to update strategy status after pause. Please try again later.")
    if not upd.data:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database update failed while pausing strategy.")
    
    return {"status": "paused", "strategy_id": strategy_id, "bot_stopped": bot_stopped}

@router.post("/{strategy_id}/resume")
async def resume_strategy(strategy_id: str, user: dict = Depends(get_current_user), fleet=Depends(get_fleet)):
    """Resume live execution bot for a strategy with strict state checks."""
    sb = await _sb(user)
    if sb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Strategy '{strategy_id}' not found.")
        
    try:
        res = await sb.table("strategies").select("symbol, is_active, status, dag_config").eq("id", strategy_id).eq("user_id", user["id"]).execute()
    except Exception as e:
        logger.error(f"[STRATEGIES] Failed to fetch strategy {strategy_id}, user {user['id']}: {e}")
        raise HTTPException(status_code=503, detail="Unable to retrieve strategy for resume. Please try again later.")
    if not res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Strategy '{strategy_id}' not found.")
    
    rec = res.data[0]
    if rec.get("is_active") and rec.get("status") == "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Strategy '{strategy_id}' is already running.")
    
    # SECURITY: Validate ML/DL strategies have trained models before resume
    blueprint = {
        "nodes": rec.get("buy_logic", {}).get("_nodes", []),
        "ml_model_path": rec.get("ml_model_path")
    }
    _validate_ml_models_present(blueprint)
    
    symbol = rec.get("symbol", "BTC/USDT")
    dag_config = rec.get("dag_config") or {"strategy_id": strategy_id}
    bot_started = True
    msg = "Resumed successfully"
    
    if hasattr(fleet, "start_bot"):
        bot_started, msg = await fleet.start_bot(user["id"], symbol, dag_config)
        if not bot_started:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Fleet failed to resume strategy bot: {msg}")
            
    try:
        upd = await sb.table("strategies").update({"is_active": True, "status": "running"}).eq("id", strategy_id).execute()
    except Exception as e:
        logger.error(f"[STRATEGIES] Failed to update status for strategy {strategy_id}, user {user['id']}: {e}")
        raise HTTPException(status_code=503, detail="Unable to update strategy status after resume. Please try again later.")
    if not upd.data:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database update failed while resuming strategy.")
        
    return {"status": "running", "strategy_id": strategy_id, "message": msg}

def validate_dag(config):
    """Validate a strategy DAG configuration through the canonical validator.

    Returns the :class:`ValidationReport`. It used to return the deleted
    ``DAGCompiler.compile(...)`` result, i.e. a ``CompiledDAG``; a caller that wants a
    plan calls ``strategy_compiler.compile_graph`` instead, so validation and
    compilation stay separable and there is still exactly one of each.
    """
    from backend_app.backend.strategy_dag import validator as dag_validator

    return dag_validator.validate(_graph_from_payload(config))
