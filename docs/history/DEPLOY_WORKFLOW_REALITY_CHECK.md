# DEPLOY WORKFLOW REALITY CHECK

## Current Code
**File:** `algo22-terminal/src/App.jsx`
**Lines:** 3426-3435 (`handleDeployStrategy`)
```javascript
  const handleDeployStrategy = async (id) => {
    try {
      const res = await endpoints.strategies.deploy(id);
      console.log("Deployed strategy:", res);
      loadStrategies();
    } catch (err) {
      console.error("Failed to deploy strategy:", err);
    }
  };
```

**Lines:** 5456-5477 (`handleDeployLive`)
```javascript
  const handleDeployLive = async () => {
    // Validation omitted...
    try {
      const payload = {
        name: strategyName,
        nodes,
        edges,
      };
      
      await endpoints.strategies.deployDag(payload);
      setSaveState("deployed");
      setTimeout(() => setSaveState(""), 1800);
    } catch (err) { ... }
  };
```

## Current Endpoint
* `endpoints.strategies.deploy(id)` maps to `POST /api/strategies/{id}/deploy` via `strategies.js` (Line 79).
* `endpoints.strategies.deployDag(payload)` maps to `POST /api/strategies/deploy` via `strategies.js` (Line 114). Note: The FastAPI backend does NOT possess this route.

## Payload
* `deploy(id)` sends an `<Empty Body>`.
* `deployDag(payload)` sends `{ name, nodes, edges }`.

## Strategy ID Resolution
* `handleDeployStrategy` correctly receives `id` from the list view.
* `handleDeployLive` never fetches, requires, or uses a `strategy_id` before hitting the deploy endpoint.

## Contract Compliance
* **exchange_id usage:** ABSENT. Neither function injects the mandatory `exchange_id` expected by `DeployRequest`.
* **deployDag existence:** The function exists in `strategies.js` but the corresponding backend route does NOT exist.

## Verdict
**FAIL**
