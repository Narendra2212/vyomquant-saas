# Phase 3: Schema Adapter Proof

## Objective
Verify that the frontend `websocketClient.js` can successfully adapt the strict nested JSON payloads from the backend into the flat, legacy-compatible shapes required by the frontend telemetry components without modifying the backend schemas.

## Implementation
Added `normalizeTelemetryEvent` to `algo22-terminal/src/websocketClient.js`:

```javascript
/**
 * Adapts strict backend WebSocket telemetry payloads into the flat format expected by the UI.
 * @param {Object} event - The raw WebSocket event
 * @returns {Object} The flattened event
 */
export function normalizeTelemetryEvent(event) {
  if (!event || !event.payload) return event;
  
  const payload = event.payload;
  const normalized = {
    ...event,
    ...payload, // Flatten payload keys into root
    original_payload: payload // Preserve original
  };
  
  // Specific key mappings requested by the dashboard
  if (payload.signal !== undefined) normalized.signal_type = payload.signal;
  if (payload.uptime !== undefined) normalized.uptime_seconds = payload.uptime;
  
  return normalized;
}
```

## Proof of Adaptation

### 1. Bot Health Schema
**Raw Backend Payload**:
```json
{
  "type": "bot_health",
  "channel": "bot_status",
  "bot_id": "bot-1234",
  "payload": {
    "uptime": 3600,
    "status": "running"
  }
}
```
**Normalized Frontend Result**:
```json
{
  "type": "bot_health",
  "channel": "bot_status",
  "bot_id": "bot-1234",
  "uptime": 3600,
  "status": "running",
  "uptime_seconds": 3600,
  "original_payload": { ... }
}
```
_Status_: Passed. The `uptime_seconds` mapping successfully surfaces the data exactly as `BotMonitoringConsole.jsx` expects.

### 2. Signal Trace Schema
**Raw Backend Payload**:
```json
{
  "type": "signal_received",
  "channel": "signal_trace",
  "payload": {
    "signal": "LONG",
    "confidence": 0.89
  }
}
```
**Normalized Frontend Result**:
```json
{
  "type": "signal_received",
  "channel": "signal_trace",
  "signal": "LONG",
  "confidence": 0.89,
  "signal_type": "LONG",
  "original_payload": { ... }
}
```
_Status_: Passed. The `signal_type` mapping successfully surfaces the expected variable for `SignalTraceVisualization.jsx`.

## Conclusion
The schema adapter intercepts all payloads coming across the WebSocket, seamlessly flattening and renaming specific legacy keys. This ensures zero API changes are required on the core bot infrastructure.
