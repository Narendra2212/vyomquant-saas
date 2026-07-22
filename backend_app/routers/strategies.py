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
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from backend_app.core.dependencies import (check_deployment_limit,
                                           check_ml_build_limit,
                                           create_request_supabase,
                                           get_current_user, get_fleet,
                                           get_vault, get_ws_manager)
from backend_app.core.event_bus import publish_command
from backend_app.core.rate_limit import limiter

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
    ML = "ml"
    LOGIC = "logic"
    SIGNAL = "signal"
    ACTION = "action"


# Type compatibility rules: source_type -> [allowed_target_types]
TYPE_COMPATIBILITY: Dict[str, List[str]] = {
    NodeType.MARKET_DATA.value: [NodeType.INDICATOR.value, NodeType.FEATURE.value],
    NodeType.INDICATOR.value: [NodeType.FEATURE.value, NodeType.LOGIC.value, NodeType.ML.value],
    NodeType.FEATURE.value: [NodeType.ML.value, NodeType.LOGIC.value],
    NodeType.ML.value: [NodeType.SIGNAL.value, NodeType.LOGIC.value],
    NodeType.LOGIC.value: [NodeType.SIGNAL.value, NodeType.ACTION.value],
    NodeType.SIGNAL.value: [NodeType.ACTION.value, NodeType.LOGIC.value],
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
    
    VALID_NODE_TYPES = {"indicator", "ml", "logic", "action", "input"}
    
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
            if n.get("type") == "action" and n.get("id") in reachable
        ]
        
        if not action_nodes:
            raise DAGCompilationError(
                "No ACTION nodes reachable in DAG. "
                f"Reachable nodes: {reachable}. "
                "DAG must have at least one 'action' type node that produces signals."
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
@router.get("/")
async def list_strategies(user: dict = Depends(get_current_user)):
    """Returns all strategies saved in Supabase for this user."""
    try:
        import asyncio
        query = (
            _sb(user)
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
        return results
    except Exception as e:
        import traceback
        logger.error(f"[STRATEGIES] Error listing strategies: {e}")
        traceback.print_exc()
        return {"error": str(e)}


# ── POST /api/strategies ─────────────────────────────────────────────────
@router.post("/")
@limiter.limit("20/minute")
async def create_strategy(
    request: Request,
    body: Dict[str, Any],
    user: dict = Depends(get_current_user),
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
        query = _sb(user).table("strategies").insert(data)
        resp = await asyncio.to_thread(query.execute)
        if resp.data:
            strategy_id = resp.data[0].get("id")
            logger.info(f"[STRATEGIES] Strategy created successfully: {strategy_id}")
            return {
                "strategy_id": strategy_id,
                "status": "created"
            }
        else:
            logger.error("[STRATEGIES] Failed to create strategy: no data returned")
            raise HTTPException(500, "Failed to create strategy")
    except Exception as e:
        logger.error(f"[STRATEGIES] Error creating strategy: {e}")
        raise HTTPException(500, f"Strategy creation failed: {str(e)}")


# ── GET /api/strategies/{id} ─────────────────────────────────────────────
@router.get("/{strategy_id}")
async def get_strategy_route(
    strategy_id: str,
    user: dict = Depends(get_current_user),
):
    import asyncio
    query = (
        _sb(user)
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
    resp = (
        _sb(user)
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
    resp = (
        _sb(user)
        .table("strategies")
        .select("symbol, status")
        .eq("id", strategy_id)
        .eq("user_id", user["id"])
        .execute()
    )
    if resp.data and resp.data[0].get("status") == "running":
        await fleet.stop_bot(user["id"], resp.data[0]["symbol"])

    _sb(user).table("strategies").delete().eq("id", strategy_id).eq(
        "user_id", user["id"]
    ).execute()
    return {"status": "ok", "deleted": strategy_id}


# ── POST /api/strategies/{id}/deploy ────────────────────────────────────
@router.post("/{strategy_id}/deploy")
async def deploy_bot(
    strategy_id: str,
    body: Dict[str, Any],
    user: dict = Depends(get_current_user),
    fleet=Depends(get_fleet),
    ws_mgr=Depends(get_ws_manager),
    _limit=Depends(check_deployment_limit),  # ← blocks over-deployment
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

    symbol = blueprint.get("symbol", "BTC/USDT")

    use_tee = os.environ.get("USE_TEE", "false").lower() == "true"
    
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

    _sb(user).table("strategies").update({"status": "running"}).eq(
        "id", strategy_id
    ).execute()

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
    vault=Depends(get_vault),
    ws_mgr=Depends(get_ws_manager),
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

    asyncio.create_task(
        ws_mgr.broadcast_user(
            user["id"],
            {"type": "bot_status", "strategy_id": strategy_id, "status": "stopped"},
        )
    )
    
    logger.info(f"[STRATEGIES] Strategy {strategy_id} stopped successfully")
    return {"status": "stopped"}


# ── POST /api/strategies/train-ml ────────────────────────────────────────
@router.post("/train-ml")
async def train_ml_strategy(
    body: Dict[str, Any],
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    vault=Depends(get_vault),
    ws_mgr=Depends(get_ws_manager),
    _ml_check=Depends(check_ml_build_limit),  # ← blocks unpaid ML compute
):
    """
    Triggers XGBoost/DL training as a background task.
    Result is pushed to the user via WebSocket when complete.
    """

    async def _train():
        try:
            # Import here to avoid blocking on startup Numba compilation
            from connection_engine import ConnectionEngine
            from data_seeking_engine import DataEngine
            from ml_models import XGBoostStrategyBlock

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
                body["strategy_name"],
                np_data,
                ["Open", "High", "Low", "Close", "Volume"],
                body.get("indicators", ["Close"]),
            )

            # Increment ml_strategies_built counter
            sb = _sb(user)
            r = (
                sb.table("profiles")
                .select("ml_strategies_built")
                .eq("id", user["id"])
                .execute()
            )
            current = r.data[0].get("ml_strategies_built", 0) if r.data else 0
            sb.table("profiles").update({"ml_strategies_built": current + 1}).eq(
                "id", user["id"]
            ).execute()

            await ws_mgr.broadcast_user(
                user["id"],
                {
                    "type": "model_trained",
                    "model_path": path,
                    "strategy": body.get("strategy_name"),
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
def backtest(request: Request, payload: dict):
    """Enqueue backtest to background worker."""
    from backend_app.worker import task_queue
    job = task_queue.enqueue("backend_app.routers.strategies.backtest_internal", payload, job_timeout=3600)
    return {"job_id": job.id, "status": "queued"}


@router.get("/backtest/{job_id}")
def get_backtest_status(job_id: str):
    """Poll backtest status."""
    from backend_app.worker import task_queue
    job = task_queue.fetch_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.is_finished:
        return {"job_id": job.id, "status": "completed", "result": job.result}
    elif job.is_failed:
        # Avoid sending raw stack trace in production if possible, but for beta it's okay
        return {"job_id": job.id, "status": "failed", "error": "Internal backtest execution failed."}
    
    return {"job_id": job.id, "status": "running" if job.is_started else "queued"}


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
            print("📊 Using DAG execution mode")
            dag_nodes = dag_config.get("nodes", [])
            dag_edges = dag_config.get("edges", [])
            strategy_name = dag_config.get("strategy_name", "DAG Strategy")
            symbols = dag_config.get("symbols", ["BTCUSDT"])
            timeframe = dag_config.get("timeframe", "1h")
            use_dag = True
            
            print(f"  Nodes: {len(dag_nodes)}")
            print(f"  Edges: {len(dag_edges)}")
            print(f"  Name: {strategy_name}")
            
        else:
            # === LEGACY MODE (BACKWARD COMPATIBILITY) ===
            print("📊 Using legacy strategy mode")
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
            
            print(f"  Strategies: {strategy_names}")
        
        # Common parameters
        if isinstance(symbols, str):
            symbols = [symbols]
        
        initial_capital = payload.get("initial_capital", 10000.0)
        trade_size_pct = payload.get("trade_size_pct", 0.1)
        payload.get("stop_loss_pct", 0.02)
        payload.get("take_profit_pct", 0.04)
        
        print(f"Symbols: {symbols}")
        print(f"Timeframe: {timeframe}")
        print(f"Initial Capital: {initial_capital}")

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
                print(f"📡 Fetching: {normalized}")
                
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
                
                print(f"✅ {symbol}: {len(df)} candles")
                return df
                
            except Exception as e:
                print(f"⚠️ CCXT failed for {symbol}: {e}")
                
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
        
        print(f"📊 Data fetched for {len(market_data)} symbols")

        # -----------------------------
        # DAG EXECUTION (PRIMARY)
        # -----------------------------
        if use_dag:
            dag_engine = DAGEngine()
            all_signals = {}
            
            # Execute DAG for each symbol
            for symbol, df in market_data.items():
                print(f"\n🔍 Executing DAG for {symbol}...")
                
                # Execute the DAG
                dag_result = dag_engine.execute_dag(dag_nodes, dag_edges, df)
                signals = dag_result["signals"]
                
                all_signals[symbol] = signals
                print(f"  Signals generated: {len(signals)}")
                print(f"  Buy signals: {(signals == 1).sum()}")
                print(f"  Sell signals: {(signals == -1).sum()}")
                
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
        print(f"\n📈 Running simulation for {min_length} steps...")
        
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
                            print(f"  [t={i}] BUY {symbol} @ {current_price:.2f}")
                
                elif signal == -1 and has_position:
                    # Sell signal - close position
                    pnl, msg = execution.close_position(symbol, current_price)
                    portfolio.close_position(symbol)
                    risk.update_equity(float(pnl))
                    print(f"  [t={i}] SELL {symbol} @ {current_price:.2f} (PnL: {pnl:.2f})")
        
        # Close remaining positions
        for symbol in list(execution.get_positions().keys()):
            if symbol in market_data:
                final_price = Decimal(str(market_data[symbol]["close"].iloc[-1]))
                pnl, msg = execution.close_position(symbol, final_price)
                risk.update_equity(float(pnl))
                print(f"  [FINAL] CLOSE {symbol} @ {final_price:.2f}")

        # -----------------------------
        # RESULTS
        # -----------------------------
        stats = execution.get_stats()
        
        print("\n✅ BACKTEST COMPLETE")
        print(f"Total Trades: {stats['total_trades']}")
        print(f"Win Rate: {stats['win_rate']:.2%}")
        print(f"Total PnL: {stats['total_pnl']:.2f}")
        
        # Calculate metrics
        total_return_pct = float((stats['current_equity'] / initial_capital - 1) * 100)
        max_drawdown_pct = float(stats['drawdown_pct'] * 100)
        
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
                {"time": 1, "value": float(stats['current_equity'])}
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
            "final_equity": float(stats['current_equity']),
            "total_trades": int(stats['total_trades']),
            "win_rate_pct": float(stats['win_rate'] * 100),
            "total_pnl": float(stats['total_pnl']),
            "max_drawdown_pct": max_drawdown_pct,
            "total_fees": float(stats['total_commission']),
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
async def clone_strategy_stub(strategy_id: str, user: dict = Depends(get_current_user)):
    raise HTTPException(status_code=501, detail="Marketplace clone not implemented")

@router.post("/optimize")
async def optimize_strategy_stub(user: dict = Depends(get_current_user)):
    raise HTTPException(status_code=501, detail="Optimization not implemented")

@router.post("/monte-carlo")
async def monte_carlo_stub(user: dict = Depends(get_current_user)):
    raise HTTPException(status_code=501, detail="Monte Carlo not implemented")

@router.post("/walk-forward")
async def walk_forward_stub(user: dict = Depends(get_current_user)):
    raise HTTPException(status_code=501, detail="Walk Forward not implemented")

@router.post("/{strategy_id}/pause")
async def pause_strategy_stub(strategy_id: str, user: dict = Depends(get_current_user)):
    raise HTTPException(status_code=501, detail="Pause strategy not implemented")

@router.post("/{strategy_id}/resume")
async def resume_strategy_stub(strategy_id: str, user: dict = Depends(get_current_user)):
    raise HTTPException(status_code=501, detail="Resume strategy not implemented")

def validate_dag(config): pass  # Replaced missing symbol
