"""
backend/dag_engine_parallel.py — Parallel DAG Execution Engine.

Upgraded DAGEngine with parallel execution support:
  - Detects independent nodes (same topological level)
  - Executes nodes in parallel using ThreadPoolExecutor
  - Preserves all dependencies (waits for predecessors)
  - Optimizes execution time for complex DAGs
  - Provides detailed performance metrics

Parallel Architecture:
  ┌─────────────────────────────────────────────────────────────┐
  │                    PARALLEL DAG ENGINE                       │
  │                                                                │
  │  Level 0: [Input1] [Input2] [Input3]        ──▶ Parallel    │
  │     ↓          ↓         ↓                                    │
  │  Level 1: [Indicator1] [Indicator2]         ──▶ Parallel    │
  │     ↓          ↓                                            │
  │  Level 2: [Logic1]                          ──▶ Sequential  │
  │     ↓                                                       │
  │  Level 3: [Action1] [Action2]               ──▶ Parallel  │
  │                                                                │
  └─────────────────────────────────────────────────────────────┘
"""
import logging
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd
from fastapi import APIRouter, Query
from pydantic import BaseModel

from backend_app.backend.dag_engine import (ActionExecutor, IndicatorExecutor,
                                            LogicExecutor, MLExecutor)

# Import existing executors from dag_engine

logger = logging.getLogger("DAGEngineParallel")


@dataclass
class ParallelExecutionMetrics:
    """Performance metrics for parallel DAG execution."""
    total_nodes: int = 0
    total_levels: int = 0
    nodes_per_level: Dict[int, int] = field(default_factory=dict)
    
    # Timing
    total_execution_time_ms: float = 0.0
    level_execution_times: Dict[int, float] = field(default_factory=dict)
    node_execution_times: Dict[str, float] = field(default_factory=dict)
    
    # Parallel efficiency
    parallelizable_nodes: int = 0
    sequential_nodes: int = 0
    estimated_speedup: float = 1.0
    
    # Thread pool stats
    max_workers: int = 0
    actual_workers_used: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'total_nodes': self.total_nodes,
            'total_levels': self.total_levels,
            'nodes_per_level': self.nodes_per_level,
            'total_execution_time_ms': round(self.total_execution_time_ms, 2),
            'level_execution_times': {k: round(v, 2) for k, v in self.level_execution_times.items()},
            'avg_node_time_ms': round(
                sum(self.node_execution_times.values()) / len(self.node_execution_times), 2
            ) if self.node_execution_times else 0,
            'parallelizable_ratio': round(
                self.parallelizable_nodes / self.total_nodes, 2
            ) if self.total_nodes > 0 else 0,
            'estimated_speedup': round(self.estimated_speedup, 2),
            'efficiency': round(
                self.estimated_speedup / min(self.max_workers, self.parallelizable_nodes), 2
            ) if self.parallelizable_nodes > 0 else 0,
        }


class ParallelDAGEngine:
    """
    Parallel DAG Execution Engine with optimized scheduling.
    
    Key Features:
    1. Level-based topological grouping for parallel execution
    2. ThreadPoolExecutor for CPU-bound indicator calculations
    3. Async support for I/O-bound operations
    4. Dependency-aware synchronization
    5. Detailed performance metrics
    """
    
    def __init__(self, max_workers: Optional[int] = None):
        """
        Initialize parallel DAG engine.
        
        Args:
            max_workers: Max thread pool size. Defaults to CPU count * 2.
        """
        self.max_workers = max_workers or (threading.cpu_count() * 2)
        
        # Executors for different node types
        self.executors = {
            "indicator": IndicatorExecutor(),
            "ml": MLExecutor(),
            "logic": LogicExecutor(),
            "action": ActionExecutor(),
        }
        
        # Thread pool for parallel execution
        self.thread_pool = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="dag_worker"
        )
        
        # Execution state
        self.node_results: Dict[str, pd.Series] = {}
        self.execution_log: List[Dict] = []
        self.metrics = ParallelExecutionMetrics(max_workers=self.max_workers)
        
        # Synchronization
        self._result_lock = threading.Lock()
        self._log_lock = threading.Lock()
        
        logger.info(f"ParallelDAGEngine initialized with {self.max_workers} workers")
    
    def shutdown(self):
        """Clean shutdown of thread pool."""
        self.thread_pool.shutdown(wait=True)
        logger.info("ParallelDAGEngine shutdown complete")
    
    def build_graph(self, nodes: List[Dict], edges: List[Dict]) -> Tuple[Dict[str, Set[str]], Dict[str, int], Dict[str, int]]:
        """
        Build graph structure and compute levels.
        
        Returns:
            (graph, in_degree, levels) where:
            - graph: node_id -> set of successors
            - in_degree: node_id -> count of incoming edges
            - levels: node_id -> topological level (0 = root)
        """
        graph = defaultdict(set)
        reverse_graph = defaultdict(set)  # For finding predecessors
        in_degree = defaultdict(int)
        
        node_ids = {node["id"] for node in nodes}
        
        # Initialize nodes
        for node_id in node_ids:
            if node_id not in graph:
                graph[node_id] = set()
            if node_id not in in_degree:
                in_degree[node_id] = 0
        
        # Build graph from edges
        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            if source and target and source in node_ids and target in node_ids:
                graph[source].add(target)
                reverse_graph[target].add(source)
                in_degree[target] += 1
        
        # Compute levels using BFS
        levels = {}
        queue = deque()
        
        # Level 0: nodes with no dependencies
        for node_id in node_ids:
            if in_degree[node_id] == 0:
                levels[node_id] = 0
                queue.append(node_id)
        
        # BFS to assign levels
        while queue:
            node_id = queue.popleft()
            current_level = levels[node_id]
            
            for successor in graph.get(node_id, set()):
                # Update successor level if this path is longer
                if successor not in levels:
                    levels[successor] = current_level + 1
                else:
                    levels[successor] = max(levels[successor], current_level + 1)
                
                in_degree[successor] -= 1
                if in_degree[successor] == 0:
                    queue.append(successor)
        
        # Restore in_degree for later use
        in_degree = defaultdict(int)
        for edge in edges:
            if edge.get("target") in node_ids:
                in_degree[edge.get("target")] += 1
        
        return dict(graph), dict(in_degree), levels
    
    def group_nodes_by_level(self, nodes: List[Dict], levels: Dict[str, int]) -> Dict[int, List[Dict]]:
        """
        Group nodes by their topological level.
        
        Returns:
            Dict mapping level -> list of nodes at that level
        """
        level_groups = defaultdict(list)
        node_map = {node["id"]: node for node in nodes}
        
        for node_id, level in levels.items():
            if node_id in node_map:
                level_groups[level].append(node_map[node_id])
        
        return dict(sorted(level_groups.items()))
    
    def get_node_inputs(self, node_id: str, edges: List[Dict]) -> List[str]:
        """Get all predecessor node IDs."""
        inputs = []
        for edge in edges:
            if edge.get("target") == node_id:
                inputs.append(edge.get("source"))
        return inputs
    
    def _execute_node_sync(
        self, 
        node: Dict, 
        edges: List[Dict], 
        market_data: pd.DataFrame
    ) -> Tuple[str, pd.Series, float]:
        """
        Execute a single node synchronously.
        
        Returns:
            (node_id, result, execution_time_ms)
        """
        node_id = node["id"]
        node_type = node.get("type", "indicator")
        start_time = time.perf_counter()
        
        try:
            # Get inputs (predecessor results)
            input_ids = self.get_node_inputs(node_id, edges)
            inputs = {}
            
            # Read from shared results with lock
            with self._result_lock:
                for input_id in input_ids:
                    if input_id in self.node_results:
                        inputs[input_id] = self.node_results[input_id]
            
            # Execute
            executor = self.executors.get(node_type)
            if executor:
                result = executor.execute(node, inputs, market_data)
            else:
                logger.warning(f"No executor for type: {node_type}")
                result = pd.Series(0, index=market_data.index)
            
            # Store result with lock
            with self._result_lock:
                self.node_results[node_id] = result
            
            # Log execution
            execution_time = (time.perf_counter() - start_time) * 1000
            
            with self._log_lock:
                self.execution_log.append({
                    "node_id": node_id,
                    "type": node_type,
                    "level": node.get("_level", -1),
                    "inputs": list(inputs.keys()),
                    "execution_time_ms": round(execution_time, 2),
                    "output_samples": result.dropna().head(3).tolist(),
                })
            
            return node_id, result, execution_time
            
        except Exception as e:
            execution_time = (time.perf_counter() - start_time) * 1000
            logger.error(f"Error executing node {node_id}: {e}")
            
            # Return neutral signal on error
            result = pd.Series(0, index=market_data.index)
            
            with self._result_lock:
                self.node_results[node_id] = result
            
            return node_id, result, execution_time
    
    def execute_level_parallel(
        self,
        level: int,
        nodes: List[Dict],
        edges: List[Dict],
        market_data: pd.DataFrame
    ) -> float:
        """
        Execute all nodes at a given level in parallel.
        
        Args:
            level: Topological level number
            nodes: Nodes at this level
            edges: All DAG edges
            market_data: Market data
            
        Returns:
            Total execution time for this level
        """
        if not nodes:
            return 0.0
        
        start_time = time.perf_counter()
        
        # Mark nodes with their level for logging
        for node in nodes:
            node["_level"] = level
        
        # Submit all nodes in this level to thread pool
        futures = {}
        for node in nodes:
            future = self.thread_pool.submit(
                self._execute_node_sync,
                node,
                edges,
                market_data
            )
            futures[future] = node["id"]
        
        # Wait for all to complete
        completed = 0
        for future in as_completed(futures):
            node_id = futures[future]
            try:
                node_id, result, exec_time = future.result()
                completed += 1
                
                # Update metrics
                self.metrics.node_execution_times[node_id] = exec_time
                
            except Exception as e:
                logger.error(f"Future error for node {node_id}: {e}")
        
        level_time = (time.perf_counter() - start_time) * 1000
        
        logger.info(
            f"Level {level}: Executed {completed}/{len(nodes)} nodes "
            f"in {level_time:.2f}ms"
        )
        
        return level_time
    
    def execute_dag_parallel(
        self,
        nodes: List[Dict],
        edges: List[Dict],
        market_data: pd.DataFrame,
        collect_metrics: bool = True
    ) -> Dict[str, Any]:
        """
        Execute full DAG with parallel scheduling.
        
        Execution Strategy:
        1. Build dependency graph and compute levels
        2. Group nodes by level
        3. Execute each level in parallel
        4. Wait for level completion before next level
        5. Collect and combine results
        
        Returns:
            {
                "signals": pd.Series,
                "node_results": Dict[str, pd.Series],
                "execution_order": List[str],
                "execution_levels": Dict[int, List[str]],
                "execution_log": List[Dict],
                "action_nodes": List[str],
                "metrics": ParallelExecutionMetrics,
                "performance": Dict
            }
        """
        total_start = time.perf_counter()
        
        # Reset state
        self.node_results = {}
        self.execution_log = []
        self.metrics = ParallelExecutionMetrics(max_workers=self.max_workers)
        
        # Build graph and compute levels
        graph, in_degree, levels = self.build_graph(nodes, edges)
        
        # Group nodes by level
        level_groups = self.group_nodes_by_level(nodes, levels)
        
        # Record metrics
        self.metrics.total_nodes = len(nodes)
        self.metrics.total_levels = len(level_groups)
        self.metrics.nodes_per_level = {
            level: len(node_list) 
            for level, node_list in level_groups.items()
        }
        
        # Count parallelizable nodes
        for level, node_list in level_groups.items():
            if len(node_list) > 1:
                self.metrics.parallelizable_nodes += len(node_list)
            else:
                self.metrics.sequential_nodes += len(node_list)
        
        # Calculate estimated speedup
        if self.metrics.parallelizable_nodes > 0:
            # Amdahl's Law approximation
            parallel_fraction = self.metrics.parallelizable_nodes / self.metrics.total_nodes
            max_speedup = 1 / (
                (1 - parallel_fraction) + 
                (parallel_fraction / min(self.max_workers, self.metrics.parallelizable_nodes))
            )
            self.metrics.estimated_speedup = max_speedup
        
        logger.info(
            f"Starting parallel execution: {len(nodes)} nodes, "
            f"{len(level_groups)} levels, {self.max_workers} workers"
        )
        
        # Execute level by level
        action_nodes = []
        
        for level in sorted(level_groups.keys()):
            level_nodes = level_groups[level]
            
            logger.debug(f"Executing level {level} with {len(level_nodes)} nodes")
            
            # Execute this level in parallel
            level_time = self.execute_level_parallel(
                level, level_nodes, edges, market_data
            )
            
            self.metrics.level_execution_times[level] = level_time
            
            # Track action nodes
            for node in level_nodes:
                if node.get("type") == "action":
                    action_nodes.append(node["id"])
        
        # Calculate total time
        total_time = (time.perf_counter() - total_start) * 1000
        self.metrics.total_execution_time_ms = total_time
        
        # Combine action signals
        final_signals = pd.Series(0, index=market_data.index)
        for action_id in action_nodes:
            if action_id in self.node_results:
                signals = self.node_results[action_id]
                final_signals += signals
        
        # Normalize
        final_signals = final_signals.apply(
            lambda x: 1 if x > 0 else (-1 if x < 0 else 0)
        )
        
        # Build execution order (flattened levels)
        execution_order = []
        for level in sorted(level_groups.keys()):
            execution_order.extend([n["id"] for n in level_groups[level]])
        
        logger.info(
            f"Parallel execution complete: {total_time:.2f}ms "
            f"({len(action_nodes)} action nodes)"
        )
        
        return {
            "signals": final_signals,
            "node_results": self.node_results.copy(),
            "execution_order": execution_order,
            "execution_levels": {
                level: [n["id"] for n in nodes]
                for level, nodes in level_groups.items()
            },
            "execution_log": self.execution_log,
            "action_nodes": action_nodes,
            "metrics": self.metrics.to_dict(),
            "performance": {
                "total_time_ms": round(total_time, 2),
                "nodes_per_second": round(
                    len(nodes) / (total_time / 1000), 1
                ) if total_time > 0 else 0,
                "parallel_efficiency": round(
                    self.metrics.estimated_speedup / self.max_workers, 2
                ) if self.max_workers > 0 else 0,
            }
        }
    
    def compare_execution_modes(
        self,
        nodes: List[Dict],
        edges: List[Dict],
        market_data: pd.DataFrame
    ) -> Dict[str, Any]:
        """
        Compare parallel vs sequential execution performance.
        
        Returns performance comparison for analysis.
        """
        # Parallel execution
        parallel_start = time.perf_counter()
        parallel_result = self.execute_dag_parallel(nodes, edges, market_data)
        parallel_time = (time.perf_counter() - parallel_start) * 1000
        
        # Sequential execution (single thread)
        seq_engine = SequentialDAGEngine()
        sequential_start = time.perf_counter()
        seq_engine.execute_dag(nodes, edges, market_data)
        sequential_time = (time.perf_counter() - sequential_start) * 1000
        
        speedup = sequential_time / parallel_time if parallel_time > 0 else 1.0
        
        return {
            "parallel": {
                "time_ms": round(parallel_time, 2),
                "metrics": parallel_result["metrics"],
            },
            "sequential": {
                "time_ms": round(sequential_time, 2),
            },
            "comparison": {
                "speedup": round(speedup, 2),
                "time_saved_ms": round(sequential_time - parallel_time, 2),
                "efficiency_percent": round(
                    (speedup / min(self.max_workers, len(nodes))) * 100, 1
                ),
            }
        }


class SequentialDAGEngine:
    """Sequential version for performance comparison."""
    
    def __init__(self):
        self.executors = {
            "indicator": IndicatorExecutor(),
            "ml": MLExecutor(),
            "logic": LogicExecutor(),
            "action": ActionExecutor(),
        }
        self.node_results: Dict[str, pd.Series] = {}
    
    def execute_dag(
        self,
        nodes: List[Dict],
        edges: List[Dict],
        market_data: pd.DataFrame
    ) -> Dict[str, Any]:
        """Execute DAG sequentially (for comparison)."""
        self.node_results = {}
        
        # Build graph and do topological sort
        graph = defaultdict(set)
        in_degree = defaultdict(int)
        
        for node in nodes:
            node_id = node["id"]
            if node_id not in graph:
                graph[node_id] = set()
            if node_id not in in_degree:
                in_degree[node_id] = 0
        
        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            if source and target:
                graph[source].add(target)
                in_degree[target] += 1
        
        # Topological sort
        queue = deque([n for n, d in in_degree.items() if d == 0])
        execution_order = []
        
        while queue:
            node_id = queue.popleft()
            execution_order.append(node_id)
            for neighbor in graph.get(node_id, set()):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
        
        # Execute sequentially
        node_map = {node["id"]: node for node in nodes}
        action_nodes = []
        
        for node_id in execution_order:
            node = node_map[node_id]
            node_type = node.get("type", "indicator")
            
            # Get inputs
            input_ids = []
            for edge in edges:
                if edge.get("target") == node_id:
                    input_ids.append(edge.get("source"))
            
            inputs = {
                input_id: self.node_results[input_id]
                for input_id in input_ids
                if input_id in self.node_results
            }
            
            # Execute
            executor = self.executors.get(node_type)
            if executor:
                result = executor.execute(node, inputs, market_data)
                self.node_results[node_id] = result
            
            if node_type == "action":
                action_nodes.append(node_id)
        
        # Combine signals
        final_signals = pd.Series(0, index=market_data.index)
        for action_id in action_nodes:
            if action_id in self.node_results:
                final_signals += self.node_results[action_id]
        
        final_signals = final_signals.apply(
            lambda x: 1 if x > 0 else (-1 if x < 0 else 0)
        )
        
        return {
            "signals": final_signals,
            "execution_order": execution_order,
            "action_nodes": action_nodes,
        }


# ═══════════════════════════════════════════════════════════════════════════
# FASTAPI ENDPOINTS FOR PARALLEL DAG
# ═══════════════════════════════════════════════════════════════════════════


router = APIRouter(prefix="/api/strategies/dag", tags=["parallel-dag"])


class DAGExecutionRequest(BaseModel):
    """Request for parallel DAG execution."""
    dag_nodes: List[Dict[str, Any]]
    dag_edges: List[Dict[str, Any]]
    market_data: Optional[List[Dict]] = None  # Optional OHLCV data
    compare_modes: bool = False  # Compare parallel vs sequential


class DAGExecutionResponse(BaseModel):
    """Response from parallel DAG execution."""
    signals: List[Dict]
    execution_order: List[str]
    execution_levels: Dict[int, List[str]]
    action_nodes: List[str]
    metrics: Dict[str, Any]
    performance: Dict[str, Any]


class PerformanceComparisonResponse(BaseModel):
    """Performance comparison response."""
    parallel_time_ms: float
    sequential_time_ms: float
    speedup: float
    time_saved_ms: float
    efficiency_percent: float
    node_count: int
    parallelizable_nodes: int


# Global engine instance
_parallel_engine: Optional[ParallelDAGEngine] = None


def get_parallel_engine() -> ParallelDAGEngine:
    """Get or create the parallel engine singleton."""
    global _parallel_engine
    if _parallel_engine is None:
        _parallel_engine = ParallelDAGEngine()
    return _parallel_engine


@router.post("/execute", response_model=DAGExecutionResponse)
async def execute_dag_parallel(request: DAGExecutionRequest):
    """
    Execute DAG with parallel scheduling.
    
    Optimizes execution by running independent nodes concurrently.
    """
    import pandas as pd
    
    engine = get_parallel_engine()
    
    # Create sample market data if not provided
    if request.market_data:
        df = pd.DataFrame(request.market_data)
        df.set_index('timestamp', inplace=True)
    else:
        # Generate synthetic data
        np.random.seed(42)
        dates = pd.date_range(end=pd.Timestamp.now(), periods=200, freq='1min')
        prices = 100 * np.exp(np.cumsum(np.random.normal(0, 0.001, 200)))
        df = pd.DataFrame({
            'open': prices,
            'high': prices * 1.005,
            'low': prices * 0.995,
            'close': prices,
            'volume': np.random.uniform(100, 1000, 200)
        }, index=dates)
    
    # Execute
    if request.compare_modes:
        comparison = engine.compare_execution_modes(
            request.dag_nodes,
            request.dag_edges,
            df
        )
        result = comparison["parallel"]
        result["comparison"] = comparison["comparison"]
    else:
        result = engine.execute_dag_parallel(
            request.dag_nodes,
            request.dag_edges,
            df
        )
    
    # Convert signals to JSON-serializable format
    signals_list = []
    if not result["signals"].empty:
        for timestamp, value in result["signals"].items():
            signals_list.append({
                "timestamp": timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp),
                "action": "buy" if value == 1 else ("sell" if value == -1 else "hold"),
                "value": int(value)
            })
    
    return {
        "signals": signals_list[-50:],  # Last 50 signals
        "execution_order": result["execution_order"],
        "execution_levels": result["execution_levels"],
        "action_nodes": result["action_nodes"],
        "metrics": result["metrics"],
        "performance": result.get("performance", {}),
    }


@router.post("/compare", response_model=PerformanceComparisonResponse)
async def compare_execution_modes(request: DAGExecutionRequest):
    """
    Compare parallel vs sequential execution performance.
    
    Returns detailed performance metrics to evaluate optimization gains.
    """
    import pandas as pd
    
    engine = get_parallel_engine()
    
    # Create sample market data
    np.random.seed(42)
    dates = pd.date_range(end=pd.Timestamp.now(), periods=200, freq='1min')
    prices = 100 * np.exp(np.cumsum(np.random.normal(0, 0.001, 200)))
    df = pd.DataFrame({
        'open': prices,
        'high': prices * 1.005,
        'low': prices * 0.995,
        'close': prices,
        'volume': np.random.uniform(100, 1000, 200)
    }, index=dates)
    
    # Compare modes
    comparison = engine.compare_execution_modes(
        request.dag_nodes,
        request.dag_edges,
        df
    )
    
    return {
        "parallel_time_ms": comparison["parallel"]["time_ms"],
        "sequential_time_ms": comparison["sequential"]["time_ms"],
        "speedup": comparison["comparison"]["speedup"],
        "time_saved_ms": comparison["comparison"]["time_saved_ms"],
        "efficiency_percent": comparison["comparison"]["efficiency_percent"],
        "node_count": comparison["parallel"]["metrics"]["total_nodes"],
        "parallelizable_nodes": comparison["parallel"]["metrics"]["parallelizable_nodes"],
    }


@router.get("/analysis")
async def analyze_dag_structure(
    nodes: str = Query(..., description="JSON string of DAG nodes"),
    edges: str = Query(..., description="JSON string of DAG edges")
):
    """
    Analyze DAG structure and parallelization potential.
    
    Returns structural analysis without execution.
    """
    import json
    
    nodes_list = json.loads(nodes)
    edges_list = json.loads(edges)
    
    engine = ParallelDAGEngine()
    graph, in_degree, levels = engine.build_graph(nodes_list, edges_list)
    level_groups = engine.group_nodes_by_level(nodes_list, levels)
    
    # Calculate parallelization potential
    total_nodes = len(nodes_list)
    parallelizable = sum(1 for level, nodes in level_groups.items() if len(nodes) > 1)
    
    return {
        "total_nodes": total_nodes,
        "total_levels": len(level_groups),
        "nodes_per_level": {
            level: len(node_list) 
            for level, node_list in level_groups.items()
        },
        "parallelizable_levels": parallelizable,
        "max_parallel_width": max(
            (len(nodes) for nodes in level_groups.values()),
            default=0
        ),
        "estimated_speedup": round(
            total_nodes / len(level_groups), 2
        ) if level_groups else 1.0,
        "critical_path_length": len(level_groups),
        "parallelism_ratio": round(
            parallelizable / len(level_groups), 2
        ) if level_groups else 0,
    }
