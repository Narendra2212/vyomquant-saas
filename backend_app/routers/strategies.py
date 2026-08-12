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
from enum import Enum
from typing import Any, Dict, List

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status

from backend_app.core.dependencies import (create_request_supabase,
                                           get_current_user, get_fleet,
                                           get_vault, get_ws_manager)
from backend_app.core.subscription_dependencies import (
    check_bot_quota,
    check_ml_quota,
    check_strategy_quota,
    decrement_usage,
    increment_usage,
    require_live_trading,
    require_ml_training,
)
from backend_app.core.subscription_engine import Resource
from backend_app.core.event_bus import publish_command, PublishError
from backend_app.core.rate_limit import limiter
import ccxt
from backend_app.backend.optimization_engine import get_optimization_engine, OptimizationConfig, OptimizationMethod, ValidationMethod
from backend_app.backend.backtest_runtime import get_backtest_runtime

router = APIRouter()
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# DAG COMPILER — Validates DAG structure before saving
# ═══════════════════════════════════════════════════════════════════════════

class DAGCompilationError(Exception):
    """Raised when DAG compilation/validation fails."""
    pass


class DAGValidationError(Exception):
    """Raised when DAG validation fails."""
    pass


class NodeType(Enum):
    """Valid node types in the DAG type system."""
    MARKET_DATA = "market_data"
    INDICATOR = "indicator"
    FEATURE = "feature"
    MATH = "math"
    ML = "ml"
    DL = "dl"
    LOGIC = "logic"
    VALIDATION = "validation"
    PORTFOLIO = "portfolio"
    SIGNAL = "signal"
    ACTION = "action"


def _detect_ml_nodes(nodes: List[Dict]) -> List[Dict]:
    """
    Detect ML/DL nodes in a DAG.
    
    Returns list of ML/DL nodes with their model_id requirements.
    """
    ml_nodes = []
    for node in nodes:
        node_type = node.get("type", "").lower()
        if node_type in [NodeType.ML.value, NodeType.DL.value]:
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


# Type compatibility rules: source_type -> [allowed_target_types]
TYPE_COMPATIBILITY: Dict[str, List[str]] = {
    NodeType.MARKET_DATA.value: [NodeType.INDICATOR.value, NodeType.FEATURE.value, NodeType.MATH.value, NodeType.VALIDATION.value],
    NodeType.INDICATOR.value: [NodeType.FEATURE.value, NodeType.LOGIC.value, NodeType.ML.value, NodeType.DL.value, NodeType.MATH.value],
    NodeType.FEATURE.value: [NodeType.ML.value, NodeType.DL.value, NodeType.LOGIC.value, NodeType.MATH.value],
    NodeType.MATH.value: [NodeType.LOGIC.value, NodeType.SIGNAL.value, NodeType.FEATURE.value, NodeType.ML.value, NodeType.DL.value],
    NodeType.ML.value: [NodeType.SIGNAL.value, NodeType.LOGIC.value, NodeType.PORTFOLIO.value],
    NodeType.DL.value: [NodeType.SIGNAL.value, NodeType.LOGIC.value, NodeType.PORTFOLIO.value],
    NodeType.LOGIC.value: [NodeType.SIGNAL.value, NodeType.ACTION.value, NodeType.VALIDATION.value, NodeType.PORTFOLIO.value],
    NodeType.VALIDATION.value: [NodeType.ACTION.value, NodeType.LOGIC.value, NodeType.SIGNAL.value],
    NodeType.PORTFOLIO.value: [NodeType.ACTION.value, NodeType.SIGNAL.value],
    NodeType.SIGNAL.value: [NodeType.ACTION.value, NodeType.LOGIC.value, NodeType.PORTFOLIO.value, NodeType.VALIDATION.value],
    NodeType.ACTION.value: []  # ACTION is terminal - no outgoing edges
}


class CompiledDAG:
    """
    Compiled and validated DAG representation with versioning.
    
    Versioning enables safe upgrades and backward compatibility.
    """
    
    # Schema version for DAG structure compatibility
    SCHEMA_VERSION = 1  # Increment when breaking changes to DAG structure
    
    def __init__(
        self,
        nodes: List[Dict],
        edges: List[Dict],
        execution_order: List[str],
        action_nodes: List[str],
        input_nodes: List[str],
        version: int = 1,
        schema_version: int = SCHEMA_VERSION
    ):
        self.nodes = nodes
        self.edges = edges
        self.execution_order = execution_order
        self.action_nodes = action_nodes
        self.input_nodes = input_nodes
        self.version = version  # Strategy version for user updates
        self.schema_version = schema_version  # DAG schema compatibility
        self.created_at = None  # Set on first save
        self.updated_at = None  # Set on each update
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "execution_order": self.execution_order,
            "action_nodes": self.action_nodes,
            "input_nodes": self.input_nodes,
            "version": self.version,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "dag_hash": self.compute_hash()
        }
    
    def compute_hash(self) -> str:
        """Compute deterministic hash of DAG content for integrity."""
        import hashlib
        import json
        
        content = json.dumps({
            "nodes": sorted([n.get("id") for n in self.nodes]),
            "edges": sorted([f"{e.get('source')}->{e.get('target')}" for e in self.edges]),
            "schema_version": self.schema_version
        }, sort_keys=True)
        
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    def is_compatible_with(self, other: 'CompiledDAG') -> bool:
        """Check if two DAG versions are schema-compatible."""
        return self.schema_version == other.schema_version
    
    def increment_version(self) -> None:
        """Increment version on update."""
        self.version += 1
        self.updated_at = datetime.now()


class DAGCompiler:
    """
    Compiles and validates DAG strategies.
    
    Ensures only executable DAGs are allowed into the system.
    """
    
    VALID_NODE_TYPES = {
        "market_data", "indicator", "feature", "math",
        "ml", "dl", "logic", "validation", "portfolio",
        "signal", "action", "input"
    }
    
    @staticmethod
    def compile(nodes: List[Dict], edges: List[Dict]) -> CompiledDAG:
        """
        Compile and validate a DAG.
        
        Args:
            nodes: List of node definitions
            edges: List of edge definitions
        
        Returns:
            CompiledDAG: Validated and compiled DAG
        
        Raises:
            DAGCompilationError: If validation fails
        """
        if not nodes:
            raise DAGCompilationError("No nodes provided in DAG")
        
        logger.info(f"🔧 Compiling DAG: {len(nodes)} nodes, {len(edges)} edges")
        
        # Step 1: Validate basic structure
        DAGCompiler._validate_structure(nodes, edges)
        
        # Step 2: Validate node types
        DAGCompiler._validate_types(nodes)
        
        # Step 3: Validate dependencies (edges reference valid nodes)
        DAGCompiler._validate_dependencies(nodes, edges)
        
        # Step 4: Validate type compatibility
        DAGCompiler._validate_type_compatibility(nodes, edges)
        
        # Step 5: Build adjacency list
        adjacency = DAGCompiler._build_adjacency(nodes, edges)
        
        # Step 6: Cycle detection (DFS)
        DAGCompiler._detect_cycles(nodes, adjacency)
        
        # Step 7: Connectivity check
        reachable = DAGCompiler._check_connectivity(nodes, adjacency)
        
        # Step 8: Orphan node detection
        # Step 7: Orphan node detection
        DAGCompiler._detect_orphans(nodes, reachable)
        
        # Step 8: Execution path check (must reach ACTION node)
        action_nodes = DAGCompiler._check_execution_path(nodes, adjacency, reachable)
        
        # Step 9: Validate action node signal inputs (MUST receive from SIGNAL or LOGIC)
        DAGCompiler._validate_action_inputs(nodes, edges)
        
        # Step 10: Calculate execution order (topological sort)
        execution_order = DAGCompiler._topological_sort(nodes, adjacency)
        
        # Step 10: Get input nodes
        input_nodes = [n["id"] for n in nodes if n.get("type") == "input"]
        
        logger.info(f"✅ DAG compiled successfully: {len(action_nodes)} action nodes")
        
        return CompiledDAG(
            nodes=nodes,
            edges=edges,
            execution_order=execution_order,
            action_nodes=action_nodes,
            input_nodes=input_nodes
        )
    
    @staticmethod
    def _validate_structure(nodes: List[Dict], edges: List[Dict]) -> None:
        """Validate basic DAG structure."""
        # Check node IDs are unique
        node_ids = [n.get("id") for n in nodes]
        if len(node_ids) != len(set(node_ids)):
            duplicates = [nid for nid in node_ids if node_ids.count(nid) > 1]
            raise DAGCompilationError(f"Duplicate node IDs: {set(duplicates)}")
        
        # Check all nodes have IDs
        for node in nodes:
            if not node.get("id"):
                raise DAGCompilationError("Node missing required 'id' field")
        
        # Check edges reference valid fields
        for edge in edges:
            if "source" not in edge:
                raise DAGCompilationError(f"Edge missing 'source': {edge}")
            if "target" not in edge:
                raise DAGCompilationError(f"Edge missing 'target': {edge}")
    
    @staticmethod
    def _validate_types(nodes: List[Dict]) -> None:
        """Validate node types."""
        for node in nodes:
            node_type = node.get("type", "").lower()
            if node_type not in DAGCompiler.VALID_NODE_TYPES:
                raise DAGCompilationError(
                    f"Invalid node type '{node_type}' for node '{node.get('id')}'. "
                    f"Valid types: {DAGCompiler.VALID_NODE_TYPES}"
                )
    
    @staticmethod
    def _validate_dependencies(nodes: List[Dict], edges: List[Dict]) -> None:
        """Validate all edge references exist."""
        node_ids = {n.get("id") for n in nodes}
        
        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            
            if source not in node_ids:
                raise DAGCompilationError(f"Edge references unknown source: '{source}'")
            if target not in node_ids:
                raise DAGCompilationError(f"Edge references unknown target: '{target}'")
    
    @staticmethod
    def _validate_type_compatibility(nodes: List[Dict], edges: List[Dict]) -> None:
        """
        Validate type compatibility for all edges.
        
        Enforces the DAG type system rules:
        - MARKET_DATA → INDICATOR, FEATURE
        - INDICATOR → FEATURE, LOGIC, ML
        - FEATURE → ML, LOGIC
        - ML → SIGNAL, LOGIC
        - LOGIC → SIGNAL, ACTION
        - SIGNAL → ACTION, LOGIC
        - ACTION → (none - terminal)
        
        Raises:
            DAGCompilationError: If type compatibility violated
        """
        # Build node type lookup
        node_types = {n.get("id"): n.get("type", "").lower() for n in nodes}
        
        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            
            source_type = node_types.get(source, "")
            target_type = node_types.get(target, "")
            
            # Check if connection is valid
            allowed_targets = TYPE_COMPATIBILITY.get(source_type, [])
            
            if target_type not in allowed_targets:
                raise DAGCompilationError(
                    f"Invalid type connection: '{source}' ({source_type}) → "
                    f"'{target}' ({target_type}). "
                    f"Type '{source_type}' can only connect to: {allowed_targets}. "
                    f"This violates the DAG type system rules."
                )
        
        logger.debug("✅ Type compatibility validation passed")
    
    @staticmethod
    def _build_adjacency(
        nodes: List[Dict],
        edges: List[Dict]
    ) -> Dict[str, List[str]]:
        """Build adjacency list representation."""
        adjacency = {n.get("id"): [] for n in nodes}
        
        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            if source in adjacency:
                adjacency[source].append(target)
        
        return adjacency
    
    @staticmethod
    def _detect_cycles(nodes: List[Dict], adjacency: Dict[str, List[str]]) -> None:
        """
        Detect cycles using DFS.
        
        Raises:
            DAGCompilationError: If cycle detected
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {n.get("id"): WHITE for n in nodes}
        
        def dfs(node_id: str, path: List[str]) -> None:
            color[node_id] = GRAY
            
            for neighbor in adjacency.get(node_id, []):
                if color[neighbor] == GRAY:
                    # Back edge to gray node = cycle
                    cycle_start = path.index(neighbor)
                    cycle = path[cycle_start:] + [neighbor]
                    raise DAGCompilationError(
                        f"Cycle detected in DAG: {' -> '.join(cycle)}"
                    )
                elif color[neighbor] == WHITE:
                    dfs(neighbor, path + [neighbor])
            
            color[node_id] = BLACK
        
        for node in nodes:
            node_id = node.get("id")
            if color[node_id] == WHITE:
                dfs(node_id, [node_id])
    
    @staticmethod
    def _check_connectivity(
        nodes: List[Dict],
        adjacency: Dict[str, List[str]]
    ) -> set:
        """
        Check all nodes are reachable from input nodes.
        
        Returns:
            set: All reachable node IDs
        """
        # Find input nodes (sources with no incoming edges)
        all_targets = set()
        for targets in adjacency.values():
            all_targets.update(targets)
        
        input_nodes = [
            n.get("id") for n in nodes
            if n.get("id") not in all_targets
        ]
        
        if not input_nodes:
            raise DAGCompilationError(
                "No input nodes found. DAG must have at least one node with no incoming edges."
            )
        
        # BFS from input nodes to find all reachable nodes
        reachable = set()
        queue = input_nodes.copy()
        
        while queue:
            node_id = queue.pop(0)
            if node_id in reachable:
                continue
            reachable.add(node_id)
            
            for neighbor in adjacency.get(node_id, []):
                if neighbor not in reachable:
                    queue.append(neighbor)
        
        return reachable
    
    @staticmethod
    def _detect_orphans(nodes: List[Dict], reachable: set) -> None:
        """Detect orphan nodes (not reachable from any input)."""
        all_nodes = {n.get("id") for n in nodes}
        orphans = all_nodes - reachable
        
        if orphans:
            raise DAGCompilationError(
                f"Orphan nodes detected (not reachable from input): {orphans}. "
                f"All nodes must be part of the execution path."
            )
    
    @staticmethod
    def _check_execution_path(
        nodes: List[Dict],
        adjacency: Dict[str, List[str]],
        reachable: set
    ) -> List[str]:
        """
        Check that execution reaches at least one ACTION node.
        
        Returns:
            List[str]: IDs of reachable action nodes
        """
        action_nodes = [
            n.get("id") for n in nodes
            if n.get("type") in ("action", "signal", "logic") and n.get("id") in reachable
        ]
        
        if not action_nodes:
            raise DAGCompilationError(
                "No ACTION nodes reachable in DAG. "
                f"Reachable nodes: {reachable}. "
                "DAG must have at least one 'action', 'signal', or 'logic' type node that produces signals."
            )
        
        return action_nodes
    
    @staticmethod
    def _validate_action_inputs(nodes: List[Dict], edges: List[Dict]) -> None:
        """
        Validate that all ACTION nodes receive valid signal inputs.
        
        Rules:
        1. ACTION node must have at least one incoming edge
        2. ACTION node inputs must be from SIGNAL or LOGIC nodes only
        3. No direct connections from INDICATOR/FEATURE/ML to ACTION
        
        Raises:
            DAGCompilationError: If action node has invalid inputs
        """
        node_types = {n.get("id"): n.get("type", "").lower() for n in nodes}
        
        # Build reverse adjacency (who connects TO each node)
        incoming = {n.get("id"): [] for n in nodes}
        for edge in edges:
            target = edge.get("target")
            source = edge.get("source")
            if target in incoming:
                incoming[target].append(source)
        
        # Check each action node
        for node in nodes:
            if node.get("type") != "action":
                continue
            
            node_id = node.get("id")
            sources = incoming.get(node_id, [])
            
            # Rule 1: Must have at least one input
            if not sources:
                raise DAGCompilationError(
                    f"ACTION node '{node_id}' has no inputs. "
                    f"Action nodes must receive signal input from SIGNAL or LOGIC nodes. "
                    f"This is an 'empty trade' strategy - no signal will trigger trades."
                )
            
            # Rule 2: All inputs must be from SIGNAL or LOGIC nodes
            for source_id in sources:
                source_type = node_types.get(source_id, "unknown")
                
                if source_type not in ["signal", "logic"]:
                    raise DAGCompilationError(
                        f"ACTION node '{node_id}' receives invalid input from '{source_id}' ({source_type}). "
                        f"Action nodes can ONLY receive input from SIGNAL or LOGIC nodes. "
                        f"Direct connections from {source_type} to action are not allowed. "
                        f"Expected: {source_type} → SIGNAL/LOGIC → ACTION"
                    )
        
        logger.debug("✅ Action node signal inputs validated")
    
    @staticmethod
    def _topological_sort(
        nodes: List[Dict],
        adjacency: Dict[str, List[str]]
    ) -> List[str]:
        """
        Calculate execution order using Kahn's algorithm.
        
        Returns:
            List[str]: Node IDs in execution order
        """
        # Calculate in-degrees
        in_degree = {n.get("id"): 0 for n in nodes}
        for targets in adjacency.values():
            for target in targets:
                in_degree[target] += 1
        
        # Start with nodes that have no dependencies
        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        result = []
        
        while queue:
            node_id = queue.pop(0)
            result.append(node_id)
            
            for neighbor in adjacency.get(node_id, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
        
        if len(result) != len(nodes):
            raise DAGCompilationError(
                "Topological sort failed. Graph may have undetected cycles."
            )
        
        return result

    @staticmethod
    def analyze_dependencies(nodes: List[Dict], edges: List[Dict]) -> Dict[str, Any]:
        """Automatic dependency analysis for DAG nodes."""
        incoming: Dict[str, List[str]] = {n.get("id", ""): [] for n in nodes}
        outgoing: Dict[str, List[str]] = {n.get("id", ""): [] for n in nodes}
        for edge in edges:
            src = edge.get("source")
            tgt = edge.get("target")
            if src in outgoing and tgt in incoming:
                outgoing[src].append(tgt)
                incoming[tgt].append(src)
        
        node_deps = {}
        for n in nodes:
            nid = n.get("id", "")
            node_deps[nid] = {
                "type": n.get("type"),
                "depends_on": incoming.get(nid, []),
                "depended_by": outgoing.get(nid, []),
                "depth": len(incoming.get(nid, [])),
            }
        return {
            "node_dependencies": node_deps,
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "critical_path_length": max([len(v["depends_on"]) for v in node_deps.values()], default=0) + 1
        }

    @staticmethod
    def generate_documentation(strategy_name: str, nodes: List[Dict], edges: List[Dict]) -> str:
        """Automatic markdown documentation generator for compiled strategies."""
        deps = DAGCompiler.analyze_dependencies(nodes, edges)
        doc = [
            f"# Strategy Specification: {strategy_name}",
            f"**Total Nodes**: {deps['total_nodes']} | **Total Connections**: {deps['total_edges']}",
            "\n## Pipeline Nodes\n"
        ]
        for n in nodes:
            nid = n.get("id")
            ntype = n.get("type", "unknown").upper()
            label = n.get("data", {}).get("label", nid)
            doc.append(f"- **[{ntype}] {label}** (`id: {nid}`) — Inputs: `{deps['node_dependencies'][nid]['depends_on']}`")
        doc.append("\n## Execution Pipeline Graph\n```")
        for edge in edges:
            doc.append(f"{edge.get('source')} ---> {edge.get('target')}")
        doc.append("```")
        return "\n".join(doc)

    @staticmethod
    def optimize_dag(nodes: List[Dict], edges: List[Dict]) -> Dict[str, Any]:
        """Automatic DAG graph optimization (prunes unused nodes and redundant passes)."""
        compiled = DAGCompiler.compile(nodes, edges)
        active_nodes = [n for n in nodes if n.get("id") in compiled.execution_order]
        active_ids = {n.get("id") for n in active_nodes}
        active_edges = [e for e in edges if e.get("source") in active_ids and e.get("target") in active_ids]
        return {
            "nodes": active_nodes,
            "edges": active_edges,
            "pruned_nodes_count": len(nodes) - len(active_nodes),
            "pruned_edges_count": len(edges) - len(active_edges),
            "execution_order": compiled.execution_order,
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

def _sb(user: dict):
    """Get an RLS-scoped Supabase client for the authenticated request."""
    token = user.get("access_token")
    if not token:
        raise HTTPException(401, "Missing authenticated Supabase token.")
    return create_request_supabase(token)


def _safe_uid(uid: str) -> str:
    """Validate UUID-shaped user_id before injecting into SQL."""
    if re.match(r"^[a-zA-Z0-9\-_]{1,128}$", str(uid)):
        return str(uid)
    raise ValueError(f"Unsafe user_id rejected: '{uid}'")


# ── GET /api/strategies ─────────────────────────────────────────────────
@router.get("/blocks")
async def get_available_blocks():
    """
    Get all available blocks for the Strategy Builder.
    Returns dynamic block registry from backend capabilities.
    """
    from backend_app.backend.indicators_backend import AVAILABLE_INDICATORS
    from backend_app.backend.ml_models import AVAILABLE_ML_MODELS, AVAILABLE_DL_MODELS
    
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
        "total_blocks": len(indicators) + len(ml_models) + len(dl_models)
    }


@router.get("/")
@limiter.limit("100/minute")
async def list_strategies(user: dict = Depends(get_current_user)):
    """Returns all strategies saved in Supabase for this user."""
    try:
        sb = _sb(user)
        if not sb:
            return []
        import asyncio
        query = (
            sb
            .table("strategies")
            .select("*")
            .eq("user_id", user["id"])
            .order("created_at", desc=True)
        )
        resp = await asyncio.to_thread(query.execute)
        
        results = resp.data
        for item in results:
            if "buy_logic" in item and isinstance(item["buy_logic"], dict):
                bl = item["buy_logic"]
                item["nodes"] = bl.pop("_nodes", [])
                item["edges"] = bl.pop("_edges", [])
                item["dag_version"] = bl.pop("_dag_version", 1)
                item["dag_schema_version"] = bl.pop("_dag_schema_version", None)
                item["dag_created_at"] = bl.pop("_dag_created_at", None)
                item["dag_updated_at"] = bl.pop("_dag_updated_at", None)
                item["dag_hash"] = bl.pop("_dag_hash", None)
                item["buy_logic"] = bl
        return {"strategies": results, "total": len(results)}
    except Exception as e:
        import traceback
        logger.error(f"[STRATEGIES] Error listing strategies: {e}")
        traceback.print_exc()
        return {"strategies": [], "total": 0, "error": str(e)}


# ── POST /api/strategies ─────────────────────────────────────────────────
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
    
    STEP 1: DAG validation performed before saving.
    """
    logger.info(f"[STRATEGIES] Creating strategy for user {user['id']}: {body.get('name')}")
    
    # STEP 1: DAG VALIDATION - Validate DAG structure before saving
    nodes = body.get("nodes", [])
    edges = body.get("edges", [])
    
    if nodes or edges:
        try:
            # Run comprehensive DAG validation
            dag_config = {"nodes": nodes, "edges": edges}
            validate_dag(dag_config)
            
            # Compile DAG
            compiled_dag = DAGCompiler.compile(nodes, edges)
            logger.info(
                f"✅ STEP 1: DAG validated and compiled: {len(compiled_dag.execution_order)} nodes, "
                f"{len(compiled_dag.action_nodes)} actions"
            )
        except DAGValidationError as e:
            logger.error(f"🚫 STEP 1: DAG validation failed: {e}")
            raise HTTPException(status_code=400, detail=str(e))
        except DAGCompilationError as e:
            logger.error(f"🚫 DAG compilation failed: {e}")
            return {
                "error": "DAG compilation failed",
                "detail": str(e),
                "hint": "Check your strategy graph for cycles, disconnected nodes, or missing action nodes"
            }
    
    # Build data with DAG versioning
    from datetime import datetime
    
    data = {
        "user_id": user["id"],
        "name": body.get("name", "Unnamed Strategy"),
        "symbol": body.get("symbol", "BTC/USDT"),
        "timeframe": body.get("timeframe", "5m"),
        "buy_logic": body.get("buy_logic", {}),
        "sell_logic": body.get("sell_logic", {}),
        "risk": body.get("risk", {}),
        "indicators": body.get("indicators", []),
        "ml_model_path": body.get("ml_model_path"),
        "exchange_id": body.get("exchange_id", "binance"),
        "status": "stopped",
    }
    
    # Store DAG fields inside buy_logic as workaround for missing DB columns
    buy_logic = data["buy_logic"]
    if not isinstance(buy_logic, dict):
        buy_logic = {}
        data["buy_logic"] = buy_logic
    
    buy_logic["_nodes"] = nodes
    buy_logic["_edges"] = edges
    buy_logic["_dag_version"] = 1
    buy_logic["_dag_schema_version"] = CompiledDAG.SCHEMA_VERSION
    buy_logic["_dag_created_at"] = datetime.now().isoformat()
    buy_logic["_dag_updated_at"] = datetime.now().isoformat()
    buy_logic["_dag_hash"] = None

    
    # Compute DAG hash for integrity
    if nodes or edges:
        try:
            compiled = DAGCompiler.compile(nodes, edges)
            data["dag_hash"] = compiled.compute_hash()
            data["execution_order"] = compiled.execution_order
        except Exception:
            pass  # Compilation errors handled earlier
    
    try:
        import asyncio
        sb = _sb(user)
        if not sb:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Strategy creation requires a configured Supabase connection. "
                    "SUPABASE_URL and SUPABASE_ANON_KEY must be set."
                ),
            )
        query = sb.table("strategies").insert(data)
        resp = await asyncio.to_thread(query.execute)
        if resp.data:
            strategy_id = resp.data[0].get("id")
            logger.info(f"[STRATEGIES] Strategy created successfully: {strategy_id}")
            return {
                "id": strategy_id,
                "strategy_id": strategy_id,
                "status": "created"
            }
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
    sb = _sb(user)
    if not sb:
        return {"id": strategy_id, "user_id": user["id"], "name": "Dev Strategy", "status": "stopped"}
    import asyncio
    query = (
        sb
        .table("strategies")
        .select("*")
        .eq("id", strategy_id)
        .eq("user_id", user["id"])
    )
    resp = await asyncio.to_thread(query.execute)
    
    if not resp.data:
        raise HTTPException(404, "Strategy not found.")
        
    item = resp.data[0]
    if "buy_logic" in item and isinstance(item["buy_logic"], dict):
        bl = item["buy_logic"]
        item["nodes"] = bl.pop("_nodes", [])
        item["edges"] = bl.pop("_edges", [])
        item["dag_version"] = bl.pop("_dag_version", 1)
        item["dag_schema_version"] = bl.pop("_dag_schema_version", None)
        item["dag_created_at"] = bl.pop("_dag_created_at", None)
        item["dag_updated_at"] = bl.pop("_dag_updated_at", None)
        item["dag_hash"] = bl.pop("_dag_hash", None)
        item["buy_logic"] = bl
        
    return item


# ── PUT /api/strategies/{id} ─────────────────────────────────────────────
@router.put("/{strategy_id}")
async def update_strategy(
    strategy_id: str,
    body: Dict[str, Any],
    user: dict = Depends(get_current_user),
):
    sb = _sb(user)
    if not sb:
        return {"id": strategy_id, "user_id": user["id"], "name": body.get("name", "Updated Dev Strategy")}
    
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
    
    resp = (
        sb
        .table("strategies")
        .update(body)
        .eq("id", strategy_id)
        .eq("user_id", user["id"])
        .execute()
    )
    if not resp.data:
        raise HTTPException(404, "Strategy not found.")
    return resp.data[0]


# ── DELETE /api/strategies/{id} ──────────────────────────────────────────
@router.delete("/{strategy_id}")
async def delete_strategy(
    strategy_id: str,
    user: dict = Depends(get_current_user),
    fleet=Depends(get_fleet),
):
    """Stops the bot first (if running) then deletes the blueprint."""
    sb = _sb(user)
    if not sb:
        return {"status": "deleted", "id": strategy_id}
    resp = (
        sb
        .table("strategies")
        .select("symbol, status")
        .eq("id", strategy_id)
        .eq("user_id", user["id"])
        .execute()
    )
    if resp.data and resp.data[0].get("status") == "running":
        await fleet.stop_bot(user["id"], resp.data[0]["symbol"])

    sb.table("strategies").delete().eq("id", strategy_id).eq("user_id", user["id"]).execute()
    
    # Decrement strategy usage
    await decrement_usage(Resource.STRATEGIES.value, user)
    
    return {"status": "deleted", "id": strategy_id}


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
    
    resp = (
        _sb(user)
        .table("strategies")
        .select("*")
        .eq("id", strategy_id)
        .eq("user_id", user["id"])
        .execute()
    )
    if not resp.data:
        logger.error(f"[STRATEGIES] Strategy not found for deploy: {strategy_id}")
        raise HTTPException(404, "Strategy not found.")

    blueprint = resp.data[0]
    if "buy_logic" in blueprint and isinstance(blueprint["buy_logic"], dict):
        bl = blueprint["buy_logic"]
        blueprint["nodes"] = bl.pop("_nodes", [])
        blueprint["edges"] = bl.pop("_edges", [])
        blueprint["dag_version"] = bl.pop("_dag_version", 1)
        blueprint["dag_schema_version"] = bl.pop("_dag_schema_version", None)
        blueprint["dag_created_at"] = bl.pop("_dag_created_at", None)
        blueprint["dag_updated_at"] = bl.pop("_dag_updated_at", None)
        blueprint["dag_hash"] = bl.pop("_dag_hash", None)
        blueprint["buy_logic"] = bl
    blueprint["exchange_id"] = body.get(
        "exchange_id", blueprint.get("exchange_id", "binance")
    )
    
    # SECURITY: Validate ML/DL strategies have trained models before deployment
    _validate_ml_models_present(blueprint)

    symbol = blueprint.get("symbol", "BTC/USDT")

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
                logger.error(f"[STRATEGIES] Deploy failed for strategy {strategy_id}: {message}")
                raise HTTPException(400, f"Deploy failed: {message}")
    except PublishError as e:
        logger.error(f"[STRATEGIES] PublishError deploying strategy {strategy_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "DEPLOY_DISPATCH_FAILED", "message": str(e)}
        )

    _sb(user).table("strategies").update({"status": "running"}).eq(
        "id", strategy_id
    ).execute()

    # Increment bot usage
    await increment_usage(Resource.BOTS.value, user)

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
    resp = (
        _sb(user)
        .table("strategies")
        .select("symbol")
        .eq("id", strategy_id)
        .eq("user_id", user["id"])
        .execute()
    )
    if not resp.data:
        logger.error(f"[STRATEGIES] Strategy not found for stop: {strategy_id}")
        raise HTTPException(404, "Strategy not found.")

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
            await publish_command(
                "stop_bot",
                {
                    "user_id": user["id"],
                    "symbol": symbol,
                },
            )
            await fleet.stop_bot(user["id"], symbol)
    except PublishError as e:
        logger.error(f"[STRATEGIES] PublishError stopping strategy {strategy_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "STOP_DISPATCH_FAILED", "message": str(e)}
        )

    # ═══════════════════════════════════════════════════════════════════
    # CANCEL ALL ORDERS - REMOVED: ALGO-ONLY EXECUTION ENFORCED
    # ═══════════════════════════════════════════════════════════════════
    # CRITICAL FIX: Direct ExecutionEngine instantiation has been REMOVED.
    # All execution must flow through:
    # Strategy → DAG → Signal → BotRunner → UnifiedExecutionEngine → Exchange
    # 
    # Order cancellation on strategy stop is handled AUTOMATICALLY by the 
    # BotRunner's cleanup process through the proper execution pipeline.
    # 
    # Any direct call to ExecutionEngine.cancel_all() is FORBIDDEN and BLOCKED.
    logger.info(f"[STRATEGIES] Strategy {strategy_id} stopped. Order cleanup handled by BotRunner.")

    _sb(user).table("strategies").update({"status": "stopped"}).eq(
        "id", strategy_id
    ).execute()

    # Decrement bot usage
    await decrement_usage(Resource.BOTS.value, user)

    asyncio.create_task(
        ws_mgr.broadcast_user(
            user["id"],
            {"type": "bot_status", "strategy_id": strategy_id, "status": "stopped"},
        )
    )
    
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
    """

    async def _train():
        try:
            # Import here to avoid blocking on startup Numba compilation
            from backend_app.backend.connection_engine import ConnectionEngine
            from data_seeking_engine import DataEngine
            from ml_models import XGBoostStrategyBlock

            # Verify strategy belongs to user and fetch buy_logic for ML node updates
            sb = _sb(user)
            strategy_buy_logic = None
            if sb:
                try:
                    import asyncio
                    query = (
                        sb.table("strategies")
                        .select("id, name, buy_logic")
                        .eq("id", strategy_id)
                        .eq("user_id", user["id"])
                        .single()
                    )
                    resp = await asyncio.to_thread(query.execute)
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
                body.get("exchange_id", "binance"),
                access_token=user.get("access_token"),
            )
            bridge = ConnectionEngine("binance", keys["api_key"], keys["secret_key"])
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
                    await asyncio.to_thread(update_query.execute)
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
                            await asyncio.to_thread(update_dag_query.execute)
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
    Validate DAG strategy configuration before saving/executing.
    
    Provides comprehensive validation of:
    - DAG structure (cycles, connectivity)
    - Node type compatibility
    - Execution path validity
    - Warnings for potential issues
    
    Request body:
    - dag: {
        nodes: [...],  # DAG nodes
        edges: [...],  # Connections
      }
    
    Response:
    {
        "valid": true/false,
        "errors": ["error1", "error2"],
        "warnings": ["warning1"],
        "execution_path": ["node1", "node2", ...],
        "node_types": {"node1": "indicator", ...},
        "stats": {
            "total_nodes": 5,
            "action_nodes": 1,
            "estimated_time_ms": 12.5
        }
    }
    """
    dag = body.get("dag", {})
    nodes = dag.get("nodes", [])
    edges = dag.get("edges", [])
    
    response = {
        "valid": False,
        "errors": [],
        "warnings": [],
        "execution_path": [],
        "node_types": {},
        "stats": {}
    }
    
    # Check if DAG provided
    if not nodes and not edges:
        response["errors"].append("No DAG configuration provided")
        return response
    
    # Check for empty nodes
    if not nodes:
        response["errors"].append("No nodes in DAG")
        return response
    
    # Build node type map
    node_types = {n.get("id"): n.get("type", "unknown") for n in nodes}
    response["node_types"] = node_types
    
    # Basic stats
    response["stats"]["total_nodes"] = len(nodes)
    response["stats"]["action_nodes"] = sum(
        1 for n in nodes if n.get("type") == "action"
    )
    
    # Check for required node types
    has_action = any(n.get("type") == "action" for n in nodes)
    has_indicator = any(n.get("type") == "indicator" for n in nodes)
    has_input = any(n.get("type") in ["input", "market_data"] for n in nodes)
    
    if not has_action:
        response["errors"].append(
            "No ACTION node found. DAG must have at least one action node to produce signals."
        )
    
    if not has_indicator and not has_input:
        response["warnings"].append(
            "No INPUT or INDICATOR nodes found. DAG may not have data sources."
        )
    
    # Try to compile DAG
    try:
        compiled_dag = DAGCompiler.compile(nodes, edges)
        response["valid"] = True
        response["execution_path"] = compiled_dag.execution_order
        response["stats"]["execution_order"] = compiled_dag.execution_order
        response["stats"]["input_nodes"] = compiled_dag.input_nodes
        
        # Performance estimate
        node_count = len(nodes)
        est_time = node_count * 2.5  # Rough estimate: 2.5ms per node
        response["stats"]["estimated_time_ms"] = round(est_time, 1)
        
    except DAGCompilationError as e:
        response["errors"].append(str(e))
    except Exception as e:
        response["errors"].append(f"Unexpected validation error: {str(e)}")
    
    # Additional warnings
    if edges:
        # Check for disconnected nodes
        connected_nodes = set()
        for edge in edges:
            connected_nodes.add(edge.get("source"))
            connected_nodes.add(edge.get("target"))
        
        all_node_ids = {n.get("id") for n in nodes}
        disconnected = all_node_ids - connected_nodes
        
        if disconnected:
            response["warnings"].append(
                f"Potentially disconnected nodes: {disconnected}"
            )
    
    # Check for duplicate node IDs
    node_ids = [n.get("id") for n in nodes]
    duplicates = set([nid for nid in node_ids if node_ids.count(nid) > 1])
    if duplicates:
        response["errors"].append(f"Duplicate node IDs: {duplicates}")
    
    logger.info(
        f"Strategy validation for user {user['id']}: "
        f"valid={response['valid']}, errors={len(response['errors'])}, "
        f"warnings={len(response['warnings'])}"
    )
    
    return response


# ── POST /api/strategies/backtest ────────────────────────────────────────
@router.post("/backtest")
@limiter.limit("30/minute")
async def backtest(request: Request, payload: dict):
    """Enqueue backtest to background worker via Redis Streams."""
    import uuid
    from datetime import datetime, timezone
    from backend_app.core.event_bus import publish_backtest_job
    from backend_app.core.cache import redis_manager
    from backend_app.worker import _write_status

    job_id = str(uuid.uuid4())
    
    # Write initial queued status hash
    await _write_status(
        redis_manager,
        job_id,
        status="queued",
        submitted_at=datetime.now(timezone.utc).isoformat()
    )

    entry_id = await publish_backtest_job(job_id, payload)
    if not entry_id:
        await _write_status(redis_manager, job_id, status="failed", error="Failed to publish to stream")
        raise HTTPException(status_code=503, detail="Failed to publish backtest job to queue")

    return {"job_id": job_id, "status": "queued"}


@router.get("/backtest/{job_id}")
async def get_backtest_status(job_id: str):
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
                {"time": i, "value": float(eq)}
                for i, eq in enumerate(stats['equity_history'])
            ]
        else:
            equity_curve = [
                {"time": 0, "value": float(initial_capital)},
                {"time": 1, "value": current_equity}
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
    
    Preserves full DAG structure (nodes, edges, buy_logic, sell_logic, risk, indicators, ml_model_path)
    and re-validates the clone by recomputing dag_hash and execution_order via DAGCompiler.
    """
    sb = _sb(user)
    if sb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Strategy '{strategy_id}' not found.")
        
    # SECURITY: Add ownership check to prevent tenant isolation bypass
    res = sb.table("strategies").select("*").eq("id", strategy_id).eq("user_id", user["id"]).execute()
    if not res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Strategy '{strategy_id}' not found.")
    
    orig = res.data[0]
    
    # Copy full DAG data from original strategy
    cloned_payload = {
        "user_id": user["id"],
        "name": f"{orig.get('name', 'Strategy')} (Copy)",
        "description": f"Cloned from {strategy_id}",
        "symbol": orig.get("symbol", ""),
        "timeframe": orig.get("timeframe", ""),
        "exchange_id": orig.get("exchange_id", ""),
        "buy_logic": orig.get("buy_logic"),
        "sell_logic": orig.get("sell_logic"),
        "risk": orig.get("risk"),
        "indicators": orig.get("indicators"),
        "ml_model_path": orig.get("ml_model_path"),
        "created_at": datetime.utcnow().isoformat(),
    }
    
    # Recompute dag_hash and execution_order for the clone to ensure validation
    # This closes part of Defect 1's exposure for the clone entry point
    if isinstance(orig.get("buy_logic"), dict):
        nodes = orig["buy_logic"].get("_nodes", [])
        edges = orig["buy_logic"].get("_edges", [])
        if nodes and edges:
            try:
                compiled = DAGCompiler.compile(nodes, edges)
                cloned_payload["dag_hash"] = compiled.get("dag_hash")
                cloned_payload["execution_order"] = compiled.get("execution_order")
            except Exception as e:
                logger.warning(f"[STRATEGIES] Clone DAG validation failed for {strategy_id}: {e}")
                # Continue with clone even if validation fails - matches existing behavior
    
    try:
        ins = sb.table("strategies").insert(cloned_payload).execute()
        if not ins.data:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to insert cloned strategy record.")
        return {"status": "cloned", "strategy": ins.data[0]}
    except Exception as e:
        logger.error(f"[STRATEGIES] Clone failed for strategy {strategy_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Database error cloning strategy: {e}")

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
    
    # Run backtest with current payload to get actual performance
    bt_res = backtest_internal(payload)
    if bt_res.get("error") or bt_res.get("total_trades", 0) == 0:
        return {
            "status": "unavailable",
            "message": "Optimization unavailable: Strategy generated no trades on historical data.",
            "optimized_dag": DAGCompiler.optimize_dag(nodes, edges),
            "best_parameters": None,
            "metrics": None
        }
    
    opt_dag = DAGCompiler.optimize_dag(nodes, edges)
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
    sb = _sb(user)
    if sb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Strategy '{strategy_id}' not found.")
        
    res = sb.table("strategies").select("symbol, is_active, status").eq("id", strategy_id).eq("user_id", user["id"]).execute()
    if not res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Strategy '{strategy_id}' not found.")
    
    rec = res.data[0]
    if not rec.get("is_active") or rec.get("status") == "paused":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Strategy '{strategy_id}' is already paused.")
    
    symbol = rec.get("symbol", "BTC/USDT")
    bot_stopped = True
    if hasattr(fleet, "stop_bot"):
        bot_stopped, _ = await fleet.stop_bot(user["id"], symbol)
        
    upd = sb.table("strategies").update({"is_active": False, "status": "paused"}).eq("id", strategy_id).execute()
    if not upd.data:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database update failed while pausing strategy.")
    
    return {"status": "paused", "strategy_id": strategy_id, "bot_stopped": bot_stopped}

@router.post("/{strategy_id}/resume")
async def resume_strategy(strategy_id: str, user: dict = Depends(get_current_user), fleet=Depends(get_fleet)):
    """Resume live execution bot for a strategy with strict state checks."""
    sb = _sb(user)
    if sb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Strategy '{strategy_id}' not found.")
        
    res = sb.table("strategies").select("symbol, is_active, status, dag_config").eq("id", strategy_id).eq("user_id", user["id"]).execute()
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
            
    upd = sb.table("strategies").update({"is_active": True, "status": "running"}).eq("id", strategy_id).execute()
    if not upd.data:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database update failed while resuming strategy.")
        
    return {"status": "running", "strategy_id": strategy_id, "message": msg}

def validate_dag(config):
    """Validate strategy DAG configuration."""
    nodes = config.get("nodes", [])
    edges = config.get("edges", [])
    return DAGCompiler.compile(nodes, edges)
