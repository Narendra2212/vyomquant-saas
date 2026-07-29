# Bot Monitoring Wiring Proof

## Objective
Verify that `BotMonitoringConsole.jsx` is successfully wired to the real backend telemetry websocket and no longer relies on hardcoded mock arrays for telemetry data.

## Implementation Details
1. **Mock Data Removal**: Removed the static mock arrays in `useEffect` that populated `bots`, `signals`, and `riskMetrics`.
2. **Adapter Integration**: Imported and applied `normalizeTelemetryEvent` from `websocketClient.js` inside the `handleSignal` and `handleInfrastructure` callbacks.
3. **Data Mapping**:
   - Mapped `data.signal_type` to `type`
   - Mapped `data.uptime_seconds` to `uptime`
   - Preserved fallback values to ensure robust rendering when backend fields are missing.

## Status
Component is fully dynamic. It relies solely on `WS_CHANNELS.SIGNAL_TRACE` and `WS_CHANNELS.BOT_STATUS` subscriptions.
