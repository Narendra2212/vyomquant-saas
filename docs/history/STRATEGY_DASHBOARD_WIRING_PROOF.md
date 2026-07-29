# Strategy Dashboard Wiring Proof

## Objective
Verify that `StrategyDashboard.jsx` handles real updates for status, PnL, and Signals through the new WebSocket Adapter.

## Implementation Details
1. **JSON Parsing Fix**: Addressed a conflict where `StrategyDashboard.jsx` attempted to `JSON.parse` the already parsed payload from `wsClient`.
2. **Adapter Integration**: Plumbed the payload through `normalizeTelemetryEvent`.
3. **Property Mappings**: Verified `strategy_name`, `strategy_id`, `pnl_contribution` mappings function flawlessly.

## Status
Dashboard correctly monitors strategy transitions, errors, and PnL updates using real backend events.
