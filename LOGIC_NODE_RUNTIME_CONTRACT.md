# LOGIC NODE RUNTIME CONTRACT

## Objective
Determine the exact data type and structure received by `LogicExecutor.execute(node: Dict, ...)` to ensure the compiler patch will not trigger runtime exceptions like `AttributeError`.

## The Route Journey
1. **Endpoint**: `POST /api/strategies/backtest`
2. **Pydantic Validation**: The payload is parsed into a `BacktestRequest` Pydantic object. Inside this object, the DAG nodes are parsed into `DAGNode` Pydantic objects.
3. **Serialization**: Before passing to the background thread, the router calls `request.model_dump()`. This strips all Pydantic types and converts the entire payload into standard nested Python dictionaries.
4. **Execution Engine**: `dag_engine.py` iterators over `nodes: List[Dict]` and calls `self.execute_node(node, ...)`.

## Audit Answers

1. **Is the executor receiving a `DAGNode` Pydantic object?**
   **No.** It receives a generic Python dictionary.
2. **Is it receiving a dictionary?**
   **Yes.**
3. **Is `node.get("params")` valid?**
   **Yes.** Since `node` is a `dict`, this is the correct access pattern. Due to Pydantic's `Field(default_factory=dict)`, it will default to `{}` if omitted by the frontend.
4. **Is `node.params` valid?**
   **No.** This will throw an `AttributeError: 'dict' object has no attribute 'params'`.
5. **Is `node.config` used instead of params?**
   **No.** The schema uses `params`.

## Conclusion
The proposed patch using `params = node.get("params", {})` and `threshold = params.get("threshold")` is structurally sound and mathematically safe. It perfectly matches the runtime execution context.
