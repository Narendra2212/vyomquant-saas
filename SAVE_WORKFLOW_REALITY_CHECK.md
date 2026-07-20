# SAVE WORKFLOW REALITY CHECK

## Current Code
**File:** `algo22-terminal/src/App.jsx`
**Lines:** 5519-5540
```javascript
  const handleSaveStrategy = async () => {
    if (isSavingStrategy) return;
    setIsSavingStrategy(true);
    setSaveState("");
    try {
      const payload = {
        name: strategyName,
        nodes,
        edges,
      };
      await endpoints.strategies.create(payload);
      setSaveState("saved");
      setTimeout(() => setSaveState(""), 1800);
    } catch (err) {
      console.error("Strategy save failed:", err);
      setSaveState("error");
      setTimeout(() => setSaveState(""), 2200);
    } finally {
      setIsSavingStrategy(false);
    }
  };
```

## Current Payload
```javascript
{
  name: strategyName,
  nodes, // Direct ReactFlow nodes array without serialization
  edges  // Direct ReactFlow edges array without serialization
}
```

## Current Endpoint
* `endpoints.strategies.create(payload)` maps to `POST /api/strategies` inside `strategies.js` (Line 55).

## Observed Behavior
1. **PUT Update Fallback:** Does NOT exist. The function blindly calls `create` every time the save button is clicked. It never checks for an existing `strategy_id`.
2. **Strategy ID Resolution:** The backend response from `create` contains the generated `strategy_id`. However, the frontend completely drops the response body and only sets `setSaveState("saved")`.
3. **LoadedStrategy State:** `loadedStrategy.id` is never updated. 

## Contract Compliance
The payload explicitly violates the `StrategyBlueprint` schema by omitting `buy_logic`, `sell_logic`, and `risk` blocks.

## Verdict
**FAIL**
