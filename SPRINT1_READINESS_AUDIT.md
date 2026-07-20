# SPRINT 1 READINESS AUDIT

## Objective
Determine whether the frontend React architecture is properly wired to drive the production backend execution and strategy engines.

## Workflow Audits

### 1. Strategy Save Workflow
- **Backend route exists?** ✅ YES (`POST /api/strategies`)
- **Frontend invokes route?** ✅ YES (`App.jsx` -> `handleSaveStrategy` -> `strategiesApi.create`)
- **Payload compatible?** ❌ NO. The frontend sends `{ name, nodes, edges }`. The backend expects a `StrategyBlueprint` model, which strictly requires `buy_logic`, `sell_logic`, and `risk` fields to be present. The backend will reject the request with a 422 Unprocessable Entity ValidationError.
- **Auth token attached?** ✅ YES
- **End-to-end executable?** ❌ **BROKEN**

### 2. Strategy Validation Workflow
- **Backend route exists?** ✅ YES (`POST /api/strategies/validate`)
- **Frontend invokes route?** ❌ NO. The frontend bypasses the API entirely and runs a hardcoded client-side check (`validateInputs` in `App.jsx`).
- **Payload compatible?** N/A
- **Auth token attached?** N/A
- **End-to-end executable?** ❌ **BROKEN**

### 3. Backtest Workflow
- **Backend route exists?** ✅ YES (`POST /api/strategies/backtest`)
- **Frontend invokes route?** ✅ YES (`App.jsx` -> `Backtester` -> `runBacktest`)
- **Payload compatible?** ❌ NO. The frontend sends `params: { dag_nodes, dag_edges }` while the backend schema (`BacktestRequest`) expects a top-level `dag: DAGConfig` object containing `{ nodes, edges, symbols, timeframe }`.
- **Auth token attached?** ✅ YES
- **End-to-end executable?** ❌ **BROKEN**

### 4. Deploy Workflow
- **Backend route exists?** ✅ YES (`POST /api/strategies/{id}/deploy`)
- **Frontend invokes route?** ✅ YES (`App.jsx` -> `handleDeployStrategy`) and ❌ NO (`handleDeployLive` calls non-existent `deployDag`).
- **Payload compatible?** ❌ NO. The frontend calls `strategiesApi.deploy(id)` with an empty body. The backend explicitly requires a `DeployRequest` schema containing `exchange_id: str`.
- **Auth token attached?** ✅ YES
- **End-to-end executable?** ❌ **BROKEN**

## Verdict
**STATUS: BROKEN**

The React frontend currently operates as an isolated mockup. Every primary API boundary (Save, Backtest, Deploy) suffers from a severe payload mismatch or routing error, preventing the UI from successfully executing operations on the backend.
