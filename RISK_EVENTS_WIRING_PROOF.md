# Risk Events Wiring Proof

## Objective
Verify that `LiveRiskAlerts.jsx` successfully interprets real risk payload structures through the adapter.

## Implementation Details
1. **JSON Parsing Fix**: Addressed a conflict where `LiveRiskAlerts.jsx` was attempting to execute `JSON.parse(event.data)` on objects that were already parsed by the central `websocketClient.js` interface.
2. **Adapter Integration**: Brought `normalizeTelemetryEvent` into the payload digestion.
3. **Deduplication Maintained**: Event IDs are still correctly parsed to ensure alert toasts aren't spammed.

## Status
Component properly handles Risk Events from the WebSocket.
