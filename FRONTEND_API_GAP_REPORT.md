# FRONTEND API GAP REPORT

| File | Function | Endpoint | Current Payload | Expected Payload | Status |
|------|----------|----------|-----------------|------------------|--------|
| `App.jsx` | `handleSaveStrategy` | `POST /api/strategies` | `{ name, nodes, edges }` | `StrategyBlueprint` requiring `buy_logic`, `sell_logic`, `risk` | ❌ BROKEN (CRITICAL) |
| `App.jsx` | `runBacktest` | `POST /api/strategies/backtest` | `{ params: { dag_nodes, dag_edges } }` | `BacktestRequest` expecting top-level `dag: DAGConfig` | ❌ BROKEN (CRITICAL) |
| `App.jsx` | `handleDeployStrategy` | `POST /api/strategies/{id}/deploy` | `<Empty Body>` | `DeployRequest` requiring `{ exchange_id: "..." }` | ❌ BROKEN (HIGH) |
| `App.jsx` | `handleDeployLive` | `POST /api/strategies/deploy` | `{ payload }` | Endpoint does not exist. Must save first and route to `/{id}/deploy`. | ❌ BROKEN (CRITICAL) |
| `strategies.js` | `deployDag` | `POST /api/strategies/deploy` | `{ payload }` | Endpoint does not exist. | ❌ BROKEN (HIGH) |
