# Update Contract Repair

Goal: Prevent duplicate strategy creation.

Verified in `algo22-terminal/src/App.jsx` `handleSaveStrategy`:
- It checks `let currentId = strategyIdState || loadedStrategy?.id;`
- If `currentId` exists, it hits `endpoints.strategies.update(currentId, payload)` (which maps to `PUT /api/strategies/{id}`).
- Else, it hits `endpoints.strategies.create(payload)` (which maps to `POST /api/strategies`).

This logic correctly uses `PUT` for subsequent saves, preventing duplicate records.
