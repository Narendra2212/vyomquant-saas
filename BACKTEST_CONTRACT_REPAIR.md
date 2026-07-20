# Backtest Contract Repair

Goal: Submit DAGConfig correctly and enter DAG execution mode inside `_run_backtest_sync()`.

Verified in `algo22-terminal/src/App.jsx` `runBacktest()`:
- `params.dag_nodes` and `params.dag_edges` have been replaced with the root-level `dag` object matching the Pydantic model.
- The `dag` object contains `{ nodes, edges, symbols, timeframe }`.
- Analyzed `_run_backtest_sync()` inside `routers/strategies.py`: it correctly intercepts `dag_config = request.get("dag")`, detects `dag_config.get("nodes")`, and switches `use_dag = True`, logging "Using DAG execution mode" and executing the DAG strategy instead of legacy fallback.
