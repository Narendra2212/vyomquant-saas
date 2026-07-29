# DAG BUILDER EXECUTION GAP

## The Gap
There is a massive misalignment between what the `StrategyBuilder.jsx` ReactFlow UI generates and what the Production DAG Backend (Sprint 1A engine) requires to execute a simulation or live deployment.

Currently, the frontend simply serializes ReactFlow's generic `nodes` and `edges` arrays and attempts to arbitrarily append them to legacy API payloads. 

### Gap 1: Saving a DAG
The backend `StrategyBlueprint` model was originally built for form-based strategy creation, requiring strict `buy_logic`, `sell_logic`, and `risk` blocks.
The frontend tries to save a DAG by sending `{ name, nodes, edges }`. This causes an immediate `422 ValidationError`. Because the Pydantic model is configured with `extra="allow"`, we can bridge this gap temporarily without backend changes by sending dummy validation logic alongside the `nodes` and `edges`.

### Gap 2: Executing a DAG Backtest
The Sprint 1A backend explicitly introduced a top-level `dag: DAGConfig` field inside the `BacktestRequest` schema to handle graph-based strategy execution.
However, the frontend `App.jsx` `runBacktest` function incorrectly stuffs the DAG into `params: { dag_nodes, dag_edges }`. The backend completely ignores this, attempts to look for the missing `dag` object or legacy `strategies` list, and fails.

### Gap 3: Deploying a DAG
The frontend builder has a "Deploy Live" button that calls a phantom `api/strategies/deploy` route via `strategiesApi.deployDag()`. This route does not exist. The correct workflow requires the DAG to be saved first to obtain a `strategy_id`, and then deployed using `POST /api/strategies/{id}/deploy` with a body containing `exchange_id: "binance"` (or similar).

## Conclusion
The frontend UI is completely disconnected from the backend contracts. Bridging this gap requires strict payload transformation functions inside the frontend API service layer to conform to the backend's Pydantic schemas.
