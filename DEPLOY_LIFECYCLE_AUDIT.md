# DEPLOY LIFECYCLE AUDIT

## Objective
Verify the end-to-end integration of strategy saving and deployment hooks in the frontend.

## Findings

### 1. Strategy Save Returns ID
**Status**: YES (Backend Side)
The backend `POST /api/strategies` successfully inserts the DAG payload into Supabase and returns:
```json
{
  "strategy_id": "uuid-...",
  "status": "created"
}
```

### 2. UI Stores Strategy ID
**Status**: NO (Frontend Side)
Inside `App.jsx -> handleSaveStrategy`, the response from the POST request is ignored:
```javascript
const payload = serializeReactFlowToDAG(nodes, edges, strategyName);
await post('/api/strategies', payload); // RESPONSE IGNORED
setSaveState("saved");
```
Because the `strategy_id` is never captured, `loadedStrategy` remains null or unchanged after a new save.

### 3. Deploy Uses Correct Strategy ID
**Status**: PARTIAL
Inside `App.jsx -> handleDeployLive`:
```javascript
const strategyId = loadedStrategy?.id || 'new';
await post(`/api/strategies/${strategyId}/deploy`, { mode: "live", ...payload });
```
Because `handleSaveStrategy` does not store the new ID in `loadedStrategy`, pressing "Deploy Live" after pressing "Save" will send the request to `/api/strategies/new/deploy` instead of the actual `strategy_id`.

**Verdict**: The frontend hooks are implemented, but the state sync between `Save` and `Deploy` is broken, resulting in deployments bypassing the stored strategy reference.
