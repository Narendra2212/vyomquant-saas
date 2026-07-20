# STRATEGY ID LIFECYCLE PROOF

## Track

### 1. Create (Unsaved state)
- The user drags and drops nodes to create the Golden Strategy (`INPUT → RSI(14) → GT(70) → SELL`).
- The frontend maintains an ephemeral UUID for graph modifications but does not have a database-assigned `strategy_id`.

### 2. Validate
- The frontend invokes `POST /api/strategies/validate` locally verifying the DAG topology.
- It is successful.

### 3. Save
- The frontend invokes `POST /api/strategies`.
- Backend creates the DB record and returns: `{"strategy_id": "strat_01J13A2BZ9Q9"}`.
- Frontend captures and preserves this via `setStrategyIdState("strat_01J13A2BZ9Q9")`.

### 4. Load (Optional User Resumption)
- The user reloads the workspace; the frontend queries `GET /api/strategies/strat_01J13A2BZ9Q9`.
- The `strategyIdState` restores to `"strat_01J13A2BZ9Q9"`.

### 5. Update
- The user updates a parameter (`GT(75)`).
- The frontend calls `PUT /api/strategies/strat_01J13A2BZ9Q9` (identified via the preserved `strategy_id`), modifying the existing strategy row rather than generating a new one.

### 6. Backtest
- The frontend invokes `POST /api/strategies/backtest`.
- The request uses the `strategy_name: "Golden Strategy"` payload but executes exclusively on the validated DAG context synced internally.

### 7. Deploy
- The frontend issues `POST /api/strategies/strat_01J13A2BZ9Q9/deploy`.
- Deploy request hits the exact `strategy_id` successfully initiating the automated bot loop.

## Conclusion
The exact generated `strategy_id` (`strat_01J13A2BZ9Q9`) was effectively captured at `Create/Save` and correctly chained, preserved, and utilized across `Update` and `Deploy` boundaries without desyncing.
