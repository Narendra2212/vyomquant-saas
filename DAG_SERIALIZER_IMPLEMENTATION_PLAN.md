# DAG Serializer Implementation Plan

Created `algo22-terminal/src/utils/dagSerializer.js`.

The exported `serializeReactFlowToDAG(nodes, edges)` function strips visual fields (`position`, `selected`, `style`, `markerEnd`, etc.) and remaps `nodes` into the backend equivalents required by `DAGConfig` / `DAGNode`:

```javascript
{
  nodes: [
    {
      id: "n-1",
      type: "input",
      label: "CCXT Asset Feed",
      symbol: "BTC/USDT",
      timeframe: "15m"
    },
    {
      id: "n-2",
      type: "indicator",
      label: "RSI",
      indicator: "rsi",
      params: { window: 14 }
    },
    ...
  ],
  edges: [
    {
      id: "e-1-2",
      source: "n-1",
      target: "n-2",
      label: null,
      condition: null
    }
  ]
}
```

The serializer correctly handles nodes of type `source`, `indicator`, `operator`, `logic`, `mlmodel`, and `action`.
Unsupported node types (e.g. `orderbook`, `liveticker`) are automatically filtered out.
