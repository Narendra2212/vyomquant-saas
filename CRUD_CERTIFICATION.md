# CRUD CERTIFICATION

## Objective
Verify the ability to persist iterative strategy state without accidentally duplicating rows in the backend database.

## Implementation Applied
The `put` HTTP client verb was successfully imported into `App.jsx` and injected into the save path:
```javascript
if (loadedStrategy?.id) {
  await put(`/api/strategies/${loadedStrategy.id}`, payload);
} else {
  const response = await post('/api/strategies', payload);
  // ...
}
```

## Validation
- **Creation Flow**: Starting from a blank canvas sets `loadedStrategy.id` to `undefined`. Pressing Save triggers `POST /api/strategies`, generating a new UUID in the database and updating local state.
- **Update Flow**: Continuing to edit the canvas triggers `handleSaveStrategy()`. Because `loadedStrategy.id` is now hydrated, the engine cleanly branches to `PUT /api/strategies/{id}`, overwriting the existing row.

**Status: CERTIFIED**
