# SPRINT 1B INVENTORY

## 1. Current ReactFlow Node Structure
- The frontend uses several custom node components: `SourceNode`, `IndicatorNode`, `MlModelNode`, `OperatorNode`, `LogicNode`, `ActionNode`, `OrderbookImbalanceNode`, `LiveTickerNode`.
- The node `type` property in ReactFlow does not directly map to the backend's canonical `NodeType` enum (`input`, `indicator`, `logic`, `ml`, `action`). For instance, it uses `source` instead of `input`, and `operator` / `logic` instead of just `logic`.

## 2. Current Node Palette Structure
- Defined by `STRATEGY_TOOLBOX` in `App.jsx`.
- Categories:
  - Data Sources (`source`, `orderbook`, `liveticker`)
  - Indicators (`indicator`)
  - ML Models (`mlmodel`)
  - Logic (`logic`)
  - Operators (`operator`)
  - Execution (`action`)

## 3. Current State Management
- Managed via React hooks: `nodes` and `edges`.
- Undo/redo capability implemented via `pastStates` and `futureStates`.
- Node parameters persist in the `node.data.params` object.
- Integration hooks like `useStrategyEngine` and `useLogicEngine` manage the mock execution states.

## 4. Current Save Workflow
- Currently relying on local state `isSavingStrategy`, `saveState`, and calling `onBacktest()` passing the payload upward.
- Does not directly invoke `POST /api/strategies`.

## 5. Current Validation Workflow
- Client-side only validation via `validateStrategy` which calls `parseGraphToExecutionPlan` and returns `valid`, `errors`, `warnings`.
- Does not invoke `POST /api/strategies/validate`.

## 6. Current Backtest Workflow
- Handled by `useStrategyEngine` calling `POST /strategy/execute` with a `plan` object (not a `DAGConfig`).
- Does not invoke `POST /api/strategies/backtest`.

## 7. Current Deploy Workflow
- Does not exist directly in `StrategyBuilderInner`.
- The pipeline mode toggle changes state, but does not hit `POST /api/strategies/{id}/deploy`.

## Summary
The current frontend is highly disconnected from the backend API, operating on mock pipelines and sending arbitrary payload structures (`executionPlan`) to deprecated or non-existent endpoints (`/strategy/execute`). This sprint requires wiring the UI to output exact `DAGConfig` structures and point to the unified `/api/strategies/*` endpoints.
