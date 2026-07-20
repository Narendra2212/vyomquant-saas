# STRATEGY PERSISTENCE AUDIT

## Objective
Verify the ability to save, load, edit, and resave a strategy using the updated builder endpoints.

## Lifecycle Findings

### 1. Load Strategy
**Status**: YES
The UI Sidebar successfully requests existing strategies. Clicking on a strategy triggers `setLoadedStrategy()` and hydrates the ReactFlow canvas `nodes` and `edges` state parameters.

### 2. Edit Strategy
**Status**: YES
The user can successfully add nodes, change parameters, and attach logic nodes. `serializeReactFlowToDAG` successfully strips these visual configurations down into raw backend-compatible `DAGConfig` updates.

### 3. Resave Strategy
**Status**: BROKEN (Data Duplication)
When clicking "Save Strategy" on a loaded strategy, the frontend calls `handleSaveStrategy`:
```javascript
const payload = serializeReactFlowToDAG(nodes, edges, strategyName);
await post('/api/strategies', payload);
```

**Issue**: The frontend always executes a `POST /api/strategies` request, even if a strategy is currently loaded.
- The `PUT /api/strategies/{id}` endpoint is ignored.
- Consequently, hitting "Save" multiple times or editing an existing strategy creates redundant duplicate copies of the strategy in the backend database rather than updating the original row.

## Verdict
The structural components are there, but the frontend state machine fails to implement basic CRUD updating (`PUT`), substituting it entirely with blind creation (`POST`).
