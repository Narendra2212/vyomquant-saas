# Save Contract Repair

Goal: Make strategy creation pass StrategyBlueprint validation.

Verified that `algo22-terminal/src/App.jsx` `handleSaveStrategy` already performs the required steps:
1. Calls `serializeReactFlowToDAG(nodes, edges)` to replace the raw ReactFlow payload with the backend-compatible schema.
2. Injects the required fields: `buy_logic`, `sell_logic`, `risk`, `symbol`, and `timeframe`.
3. Captures `response.strategy_id` and stores it into `strategyIdState`.

Added `handleValidateStrategy()` call before proceeding with the save.
