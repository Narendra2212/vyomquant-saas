"""
backend/strategy_compiler.py — Strategy Builder Compiler

PHASE G: Compiler-based architecture.

Visual Blocks → Block Graph → Validation → Execution Graph (IR) → Strategy Package

Builder must NEVER execute strategies directly.
All execution happens through the compiled execution graph.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
from uuid import uuid4

from backend_app.core.models.pydantic_models import DAGConfig, DAGNode, DAGEdge, NodeType

logger = logging.getLogger("StrategyCompiler")


class CompilerError(Exception):
    """Raised when compilation fails."""
    pass


class ValidationError(Exception):
    """Raised when validation fails."""
    pass


@dataclass
class ExecutionGraph:
    """
    Intermediate Representation (IR) of the strategy.
    
    This is the compiled form that will be executed by the DAG engine.
    """
    id: str
    version: str
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    execution_order: List[str]  # Topological order
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for storage/transmission."""
        return {
            "id": self.id,
            "version": self.version,
            "nodes": self.nodes,
            "edges": self.edges,
            "execution_order": self.execution_order,
            "metadata": self.metadata
        }


@dataclass
class StrategyPackage:
    """
    Complete strategy package ready for deployment.
    
    Contains:
    - Execution graph (IR)
    - Strategy metadata
    - Dependencies
    - Version information
    """
    id: str
    strategy_id: str
    version: str
    execution_graph: ExecutionGraph
    metadata: Dict[str, Any] = field(default_factory=dict)
    dependencies: Dict[str, Any] = field(default_factory=dict)
    compiled_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for storage/transmission."""
        return {
            "id": self.id,
            "strategy_id": self.strategy_id,
            "version": self.version,
            "execution_graph": self.execution_graph.to_dict(),
            "metadata": self.metadata,
            "dependencies": self.dependencies,
            "compiled_at": self.compiled_at
        }


class StrategyCompiler:
    """
    Compiles visual strategy graphs into execution graphs.
    
    Workflow:
    1. Parse DAGConfig from builder
    2. Validate graph structure
    3. Validate nodes and edges
    4. Generate topological execution order
    5. Build execution graph (IR)
    6. Create strategy package
    """
    
    def __init__(self):
        self._node_validators = {
            NodeType.INDICATOR: self._validate_indicator_node,
            NodeType.ML: self._validate_ml_node,
            NodeType.LOGIC: self._validate_logic_node,
            NodeType.ACTION: self._validate_action_node,
            NodeType.INPUT: self._validate_input_node,
        }
    
    def compile(
        self,
        dag_config: DAGConfig,
        strategy_id: str,
        version: str,
        metadata: Optional[Dict] = None
    ) -> StrategyPackage:
        """
        Compile a visual strategy graph into a strategy package.
        
        Args:
            dag_config: DAG configuration from builder
            strategy_id: Strategy ID
            version: Strategy version
            metadata: Optional metadata
            
        Returns:
            StrategyPackage ready for deployment
            
        Raises:
            CompilerError: If compilation fails
            ValidationError: If validation fails
        """
        try:
            # Phase 1: Validate graph structure
            self._validate_graph_structure(dag_config)
            
            # Phase 2: Validate nodes
            self._validate_nodes(dag_config.nodes)
            
            # Phase 3: Validate edges
            self._validate_edges(dag_config.edges, dag_config.nodes)
            
            # Phase 4: Generate execution order
            execution_order = self._generate_execution_order(dag_config.nodes, dag_config.edges)
            
            # Phase 5: Build execution graph
            execution_graph = ExecutionGraph(
                id=str(uuid4()),
                version=version,
                nodes=dag_config.nodes,
                edges=dag_config.edges,
                execution_order=execution_order,
                metadata={
                    "symbols": dag_config.symbols,
                    "timeframe": dag_config.timeframe,
                    "node_count": len(dag_config.nodes),
                    "edge_count": len(dag_config.edges),
                    **(metadata or {})
                }
            )
            
            # Phase 6: Create strategy package
            package = StrategyPackage(
                id=str(uuid4()),
                strategy_id=strategy_id,
                version=version,
                execution_graph=execution_graph,
                metadata=metadata or {},
                dependencies=self._extract_dependencies(dag_config)
            )
            
            logger.info(f"Compiled strategy {strategy_id} v{version} with {len(dag_config.nodes)} nodes")
            
            return package
            
        except ValidationError as e:
            logger.error(f"Validation failed for strategy {strategy_id}: {e}")
            raise
        except Exception as e:
            logger.error(f"Compilation failed for strategy {strategy_id}: {e}")
            raise CompilerError(f"Compilation failed: {e}")
    
    def _validate_graph_structure(self, dag_config: DAGConfig) -> None:
        """Validate overall graph structure."""
        if not dag_config.nodes:
            raise ValidationError("No nodes in strategy graph")
        
        if not dag_config.edges:
            raise ValidationError("No edges in strategy graph")
        
        # Check for at least one input node
        input_nodes = [n for n in dag_config.nodes if n.get("type") == NodeType.INPUT]
        if not input_nodes:
            raise ValidationError("Strategy must have at least one input node")
        
        # Check for at least one action node
        action_nodes = [n for n in dag_config.nodes if n.get("type") == NodeType.ACTION]
        if not action_nodes:
            raise ValidationError("Strategy must have at least one action node")
    
    def _validate_nodes(self, nodes: List[Dict]) -> None:
        """Validate all nodes."""
        node_ids = set()
        
        for node in nodes:
            node_id = node.get("id")
            if not node_id:
                raise ValidationError("Node missing id")
            
            if node_id in node_ids:
                raise ValidationError(f"Duplicate node id: {node_id}")
            node_ids.add(node_id)
            
            node_type = node.get("type")
            if not node_type:
                raise ValidationError(f"Node {node_id} missing type")
            
            validator = self._node_validators.get(node_type)
            if validator:
                validator(node)
    
    def _validate_indicator_node(self, node: Dict) -> None:
        """Validate indicator node."""
        if not node.get("indicator"):
            raise ValidationError(f"Indicator node {node['id']} missing indicator field")
        
        params = node.get("params", {})
        if not isinstance(params, dict):
            raise ValidationError(f"Indicator node {node['id']} params must be a dict")
    
    def _validate_ml_node(self, node: Dict) -> None:
        """Validate ML node."""
        if not node.get("model_id"):
            raise ValidationError(f"ML node {node['id']} missing model_id")
        
        confidence_threshold = node.get("confidence_threshold", 0.7)
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValidationError(f"ML node {node['id']} confidence_threshold must be between 0 and 1")
    
    def _validate_logic_node(self, node: Dict) -> None:
        """Validate logic node."""
        if not node.get("operator"):
            raise ValidationError(f"Logic node {node['id']} missing operator")
    
    def _validate_action_node(self, node: Dict) -> None:
        """Validate action node."""
        if not node.get("action"):
            raise ValidationError(f"Action node {node['id']} missing action")
        
        valid_actions = ["buy", "sell", "hold", "close"]
        if node["action"] not in valid_actions:
            raise ValidationError(f"Action node {node['id']} has invalid action: {node['action']}")
    
    def _validate_input_node(self, node: Dict) -> None:
        """Validate input node."""
        if not node.get("symbol"):
            raise ValidationError(f"Input node {node['id']} missing symbol")
        
        if not node.get("timeframe"):
            raise ValidationError(f"Input node {node['id']} missing timeframe")
    
    def _validate_edges(self, edges: List[Dict], nodes: List[Dict]) -> None:
        """Validate all edges."""
        node_ids = {n.get("id") for n in nodes}
        
        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            
            if not source or not target:
                raise ValidationError("Edge missing source or target")
            
            if source not in node_ids:
                raise ValidationError(f"Edge source {source} not found in nodes")
            
            if target not in node_ids:
                raise ValidationError(f"Edge target {target} not found in nodes")
        
        # Check for cycles
        if self._has_cycle(nodes, edges):
            raise ValidationError("Strategy graph contains cycles")
    
    def _has_cycle(self, nodes: List[Dict], edges: List[Dict]) -> bool:
        """Check if graph has cycles using DFS."""
        from collections import defaultdict
        
        adjacency = defaultdict(list)
        for edge in edges:
            adjacency[edge["source"]].append(edge["target"])
        
        visited = set()
        recursion_stack = set()
        
        def dfs(node_id: str) -> bool:
            visited.add(node_id)
            recursion_stack.add(node_id)
            
            for neighbor in adjacency[node_id]:
                if neighbor not in visited:
                    if dfs(neighbor):
                        return True
                elif neighbor in recursion_stack:
                    return True
            
            recursion_stack.remove(node_id)
            return False
        
        for node in nodes:
            if node["id"] not in visited:
                if dfs(node["id"]):
                    return True
        
        return False
    
    def _generate_execution_order(self, nodes: List[Dict], edges: List[Dict]) -> List[str]:
        """
        Generate topological execution order.
        
        Ensures nodes are executed in dependency order.
        """
        from collections import defaultdict, deque
        
        # Build adjacency list and in-degree count
        adjacency = defaultdict(list)
        in_degree = {node["id"]: 0 for node in nodes}
        
        for edge in edges:
            adjacency[edge["source"]].append(edge["target"])
            in_degree[edge["target"]] += 1
        
        # Kahn's algorithm for topological sort
        queue = deque([node_id for node_id, degree in in_degree.items() if degree == 0])
        execution_order = []
        
        while queue:
            node_id = queue.popleft()
            execution_order.append(node_id)
            
            for neighbor in adjacency[node_id]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
        
        if len(execution_order) != len(nodes):
            raise ValidationError("Cannot generate execution order - graph may have cycles")
        
        return execution_order
    
    def _extract_dependencies(self, dag_config: DAGConfig) -> Dict[str, Any]:
        """Extract strategy dependencies."""
        dependencies = {
            "indicators": set(),
            "ml_models": set(),
            "data_sources": set()
        }
        
        for node in dag_config.nodes:
            node_type = node.get("type")
            
            if node_type == NodeType.INDICATOR:
                dependencies["indicators"].add(node.get("indicator"))
            elif node_type == NodeType.ML:
                dependencies["ml_models"].add(node.get("model_id"))
            elif node_type == NodeType.INPUT:
                dependencies["data_sources"].add(node.get("symbol"))
        
        # Convert sets to lists for JSON serialization
        return {
            "indicators": list(dependencies["indicators"]),
            "ml_models": list(dependencies["ml_models"]),
            "data_sources": list(dependencies["data_sources"])
        }


# Singleton instance
_compiler = None

def get_compiler() -> StrategyCompiler:
    """Get singleton StrategyCompiler instance."""
    global _compiler
    if _compiler is None:
        _compiler = StrategyCompiler()
    return _compiler