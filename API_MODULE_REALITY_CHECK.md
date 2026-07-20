# API MODULE REALITY CHECK

**File:** `algo22-terminal/src/api/modules/strategies.js`

| Function | Endpoint | Request Shape | Response Handling | Used By |
|----------|----------|---------------|-------------------|---------|
| `create` | `POST /api/strategies` | `{ payload }` | Returns Promise | `App.jsx` (`handleSaveStrategy`) |
| `update` | `PUT /api/strategies/${id}` | `{ id, payload }` | Returns Promise | **None** (Orphaned function) |
| `deploy` | `POST /api/strategies/${id}/deploy` | `{ body, options }` (Injected `Idempotency-Key` headers) | Returns Promise | `App.jsx` (`handleDeployStrategy`) |
| `deployDag` | `POST /api/strategies/deploy` | `{ payload, options }` | Returns Promise | `App.jsx` (`handleDeployLive`) |
| `validate` | **DOES NOT EXIST** | N/A | N/A | **None** |
| `backtest` | `POST /api/strategies/backtest` | `{ payload }` | Returns Promise | `App.jsx` (`runBacktest`) |
