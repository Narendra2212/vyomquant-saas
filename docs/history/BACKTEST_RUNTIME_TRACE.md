# BACKTEST RUNTIME TRACE

Here is the exact object lifecycle and type degradation trace during a strategy backtest.

## Trace Path

1.  **`POST /api/strategies/backtest` (FastAPI Router)**
    *   **File:** `routers/strategies.py`, Line 1166
    *   **Input Type:** Raw JSON payload.
    *   **Validation:** FastAPI maps the payload to the `BacktestRequest` Pydantic model.
    *   **Current State:** `request` is a strict `BacktestRequest` Pydantic instance. All contained nodes are strict `DAGNode` Pydantic instances.

2.  **Model Serialization**
    *   **File:** `routers/strategies.py`, Line 1184
    *   **Code:** `request_dict = request.model_dump()`
    *   **Current State:** The entire request is recursively stripped of Pydantic typing and becomes a standard Python `dict`. All `DAGNode` instances are now dictionaries. Defaults like `params={}` are populated.

3.  **Synchronous Thread Handoff**
    *   **File:** `routers/strategies.py`, Line 1185
    *   **Code:** `return await asyncio.to_thread(_run_backtest_sync, request_dict)`
    *   **Current State:** Background execution begins receiving the generic `dict`.

4.  **DAG Engine Initialization**
    *   **File:** `routers/strategies.py`, Line 1230
    *   **Code:** `dag_nodes = dag_config.get("nodes", [])`
    *   **Current State:** Extracting the list of node dictionaries.

5.  **Execution Engine Entry**
    *   **File:** `routers/strategies.py`, Line 1361
    *   **Code:** `dag_result = dag_engine.execute_dag(dag_nodes, dag_edges, df)`
    *   **Current State:** Nodes enter `backend/dag_engine.py` as `nodes: List[Dict]`.

6.  **Node Execution Loop**
    *   **File:** `backend/dag_engine.py`, Line 1230-1232
    *   **Code:** 
        ```python
        for node_id in execution_order:
            node = node_map[node_id]
            result = self.execute_node(node, edges, market_data)
        ```
    *   **Current State:** A single node is extracted. It remains a `Dict`.

7.  **Logic Executor Routing**
    *   **File:** `backend/dag_engine.py`, Line 1156
    *   **Code:** `result = executor.execute(node, inputs, market_data)`
    *   **Current State:** `LogicExecutor.execute` receives the node as a `Dict`.

## Conclusion
The data arrives as a typed Pydantic object, is dumped to a standard `dict`, and traverses the entire backend compiler and execution engine as a dictionary. Reading configuration via `.get()` is the only valid way to parse parameters at runtime.
