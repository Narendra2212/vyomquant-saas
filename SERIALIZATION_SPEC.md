# SERIALIZATION SPECIFICATION

This document outlines how the ReactFlow state is mapped directly to the backend Pydantic models.

## The Serializer Function
Before hitting any backend route (`/validate`, `/backtest`, or `/save`), the frontend must call a serialization function: `serializeReactFlowToDAG(nodes, edges)`.

### 1. Edge Serialization
ReactFlow edges are cleanly mapped to the backend `DAGEdge` schema.

```javascript
// ReactFlow Edge
{
  id: "e1-2",
  source: "node-1",
  target: "node-2"
}

// Backend DAGEdge Mapping
{
  id: edge.id,
  source: edge.source,
  target: edge.target,
  label: edge.label || null,
  condition: edge.data?.condition || null
}
```

### 2. Node Serialization
The serializer maps the generic ReactFlow node structure into the specific backend `DAGNode` schema, extracting values from the `data` object.

```javascript
// Backend DAGNode Mapping
function serializeNode(node) {
  const base = {
    id: node.id,
    type: node.type, // "input", "indicator", "logic", "ml", "action"
    label: node.data.label || node.type
  };

  switch(node.type) {
    case 'input':
      return {
        ...base,
        symbol: node.data.symbol || "BTCUSDT",
        timeframe: node.data.timeframe || "1h"
      };
    case 'indicator':
      return {
        ...base,
        indicator: node.data.indicator, // e.g., "rsi"
        params: node.data.params || {}  // e.g., { period: 14 }
      };
    case 'logic':
      return {
        ...base,
        operator: node.data.operator // e.g., "GT", "AND"
      };
    case 'ml':
      return {
        ...base,
        model_id: node.data.model_id,
        confidence_threshold: node.data.confidence_threshold || 0.7
      };
    case 'action':
      return {
        ...base,
        action: node.data.action, // "buy" or "sell"
        order_type: node.data.order_type || "market",
        amount: node.data.amount || 1.0
      };
    default:
      throw new Error(`Unsupported node type: ${node.type}`);
  }
}
```

### 3. Assembling the DAGConfig
The serialized nodes and edges are packaged into the `DAGConfig` object required by `BacktestRequest` or `StrategyBlueprint`.

```javascript
const dagConfig = {
  nodes: reactFlowNodes.map(serializeNode),
  edges: reactFlowEdges.map(serializeEdge),
  symbols: extractSymbols(reactFlowNodes), // Utility to find all input node symbols
  timeframe: extractTimeframe(reactFlowNodes) // Utility to find the primary timeframe
};
```
