# Deploy Contract Repair

Goal: Repair deployment lifecycle.

Modified `algo22-terminal/src/App.jsx`:
- Made sure `deployDag()` phantom route was removed.
- Validated that `strategy_id` is required before deployment. If not present, the user gets an error: "Cannot deploy an unsaved strategy."
- Created dropdown in `StrategyBuilderInner` for Binance, Bybit, OKX, storing the selection in `selectedDeployExchange`.
- Passed the dropdown selection down to the API call: `endpoints.strategies.deploy(currentId, { exchange_id: selectedDeployExchange || "binance" })`.
- Fixed `handleDeployStrategy` in the `Strategies` component to allow an `exchangeId` argument and pass it in the `endpoints.strategies.deploy(id, { exchange_id: exchangeId })` payload.
