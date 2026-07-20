# SERIALIZER CONTRACT AUDIT

## Function Trace: `serializeReactFlowToDAG()`
* **File:** UNKNOWN / MISSING
* **Status:** ❌ DOES NOT EXIST

## Audit Verification
A comprehensive file-system and source-code scan of the `algo22-terminal` frontend architecture confirms that **no function named `serializeReactFlowToDAG()` exists.**

The frontend natively stores the DAG configuration as raw `ReactFlow` node objects (e.g. `{ id: '1', position: { x: 250, y: 50 }, data: { label: 'Binance Data Stream' }, type: 'input' }`). 

When passing this data to the backend via `handleSaveStrategy` or `runBacktest`, it simply dumps the raw ReactFlow objects directly into the JSON payload:
```javascript
const payload = {
  name: strategyName,
  nodes, // <-- Raw ReactFlow state
  edges, // <-- Raw ReactFlow state
};
```

## Contract Verification
Because the serializer does not exist, the required node normalization (extracting `input`, `indicator`, `logic`, `ml`, `action` into strictly typed formats matching the Sprint 1A DAGNode schemas) never occurs. 

### Compatibility Score: 0 / 100
The frontend currently sends visual coordinates (e.g., `position: { x, y }`, `style: { background: ... }`) and omits required functional routing paths, causing an absolute failure when passed to the backend `DAGEngine`.
