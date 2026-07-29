# CRUD REALITY CHECK

## Create Flow
* **Status:** IMPLEMENTED
* **Actual Source Locations:**
  * `algo22-terminal/src/App.jsx` -> `handleSaveStrategy` (Line 5519)
  * `algo22-terminal/src/api/modules/strategies.js` -> `create` (Line 55)
  * Invokes `POST /api/strategies`.

## Update Flow
* **Status:** MISSING (In UI logic)
* **Actual Source Locations:**
  * `algo22-terminal/src/api/modules/strategies.js` -> `update` (Line 63) exists and maps to `PUT /api/strategies/${id}`.
  * **Missing Logic:** A full repository scan confirms that `strategiesApi.update` or `.update(` is **never called** anywhere in `algo22-terminal/src`. `handleSaveStrategy` never checks `loadedStrategy.id` to branch between save and update.

## Delete Flow
* **Status:** IMPLEMENTED
* **Actual Source Locations:**
  * `algo22-terminal/src/App.jsx` -> `handleDeleteStrategy` (Line 3415)
  * `algo22-terminal/src/api/modules/strategies.js` -> `delete` (Line 70)
  * Invokes `DELETE /api/strategies/${id}`.

## Duplicate-Save Prevention
* **Status:** MISSING
* **Actual Source Locations:** Absent. Pressing "Save" inside the `StrategyBuilder` component repeatedly generates net-new strategies in the backend because there is no mechanism to track `strategy_id` post-save.

## Verdict
**PARTIAL**
