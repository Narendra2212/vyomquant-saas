# EXACT FILE MODIFICATION PLAN

This plan identifies the minimum work required to wire the frontend DAG builder to the production backend APIs without modifying backend architecture. 

Tasks are ranked by Revenue Impact, Launch Impact, and Engineering Effort.

---

### Task 1: Fix DAG Backtesting Integration
**Rank:** #1 (Revenue: High | Launch: High | Effort: Low)
**Objective:** Route the frontend DAG JSON into the correct `dag: DAGConfig` field expected by the backend engine.

**File:** `algo22-terminal/src/App.jsx`
**Function:** `runBacktest` (approx line ~6220)
**Modification:**
```javascript
// Remove the current params-based stuffing:
// params: { dag_nodes: strategy.nodes, dag_edges: strategy.edges }

// Replace with top-level DAG configuration:
const payload = {
  strategies,
  symbols,
  timeframe,
  initial_capital: Number(initialCapital),
  trade_size_pct: Number(tradeSizePct) / 100,
  stop_loss_pct: Number(stopLossPct) / 100,
  take_profit_pct: Number(takeProfitPct) / 100,
  ml_threshold: Number(mlThreshold),
  // Add correct DAG schema mapping:
  dag: {
    nodes: strategy.nodes || [],
    edges: (strategy.edges || []).map(e => ({ source: e.source, target: e.target })),
    symbols: symbols,
    timeframe: timeframe
  }
};
```
**LOC Estimate:** ~12 lines modified.

---

### Task 2: Fix DAG Save Workflow
**Rank:** #2 (Revenue: High | Launch: High | Effort: Low)
**Objective:** Pass backend validation by injecting dummy required fields into the StrategyBlueprint payload so that the `nodes` and `edges` arrays are successfully saved via the `extra="allow"` configuration.

**File:** `algo22-terminal/src/App.jsx`
**Function:** `handleSaveStrategy` (approx line ~5519)
**Modification:**
```javascript
// Current:
const payload = { name: strategyName, nodes, edges };

// New:
const payload = {
  name: strategyName,
  nodes,
  edges,
  // Inject dummy fields to satisfy StrategyBlueprint validation
  buy_logic: { operator: "AND", conditions: [] },
  sell_logic: { operator: "AND", conditions: [] },
  risk: { position_size_pct: 0.1, stop_loss_pct: 0.05, take_profit_pct: 0.1 },
  symbol: "BTC/USDT",
  timeframe: "1h"
};
```
**LOC Estimate:** ~8 lines modified.

---

### Task 3: Fix Live Deployment Workflow
**Rank:** #3 (Revenue: High | Launch: High | Effort: Low)
**Objective:** Remove phantom endpoints and ensure deployment requests contain the required `DeployRequest` schema (`exchange_id`).

**File 1:** `algo22-terminal/src/api/modules/strategies.js`
**Function:** `deploy` and `deployDag`
**Modification:**
Remove `deployDag` entirely. Update `deploy` to ensure it passes a payload.

**File 2:** `algo22-terminal/src/App.jsx`
**Function:** `handleDeployStrategy` (approx line ~3426)
**Modification:**
```javascript
// Current: const res = await endpoints.strategies.deploy(id);
// New:
const res = await endpoints.strategies.deploy(id, { exchange_id: "binance" });
```

**File 3:** `algo22-terminal/src/App.jsx`
**Function:** `handleDeployLive` (approx line ~5456)
**Modification:**
```javascript
// Current: await endpoints.strategies.deployDag(payload);
// New workflow requires saving first, then deploying via ID:
let stratId = strategyId; // from state
if (!stratId) {
  // Save strategy logic here to get ID...
}
await endpoints.strategies.deploy(stratId, { exchange_id: "binance" });
```
**LOC Estimate:** ~20 lines modified.
