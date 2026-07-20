# Dead Telemetry Audit

## Objective
Identify unused, orphaned, or disconnected telemetry components across the backend architecture to support cleanup and maintain system integrity.

## Findings

### 1. Dead Channels (No Publishers)
- **`deployment_events`**: The channel `ChannelType.DEPLOYMENT_EVENTS` is declared, and the function `publish_deployment` exists in `ws_event_stream.py`, but it is **never** invoked anywhere in the codebase.
- **`infrastructure`**: The channel `ChannelType.INFRASTRUCTURE` is declared in `ws_channels.py`, but there is no corresponding publish function, no event types defined, and it is never emitted.

### 2. Dead Memory Stores
The `BotTelemetryService` class (in `backend_app/backend/bot_telemetry.py`) instantiates local memory stores for signals and risks, but they are completely bypassed by the live engine:
- **`SignalEventStore.record_signal_event()`**: Never called. Signals are passed directly to `ws_publish_signal_trace()`.
- **`RiskEventStore.record_risk_event()`**: Never called. Risk events are built on the fly and dispatched directly via `publish_risk_event()`.

*Note: `ExecutionEventStore` and `BotHealthTracker` ARE actively used.*

### 3. Orphaned Dashboards (Frontend Impact)
Because `deployment_events` and `infrastructure` channels are never pushed, any frontend components relying strictly on live telemetry to populate deployment statuses or infrastructure health are orphaned and will remain blank or stagnant unless they poll REST APIs.

## Conclusion
The live telemetry stream primarily supports Bot Health, Executions, Risk alerts, and Signal Traces. The memory stores for Signals and Risks are dead code, and Deployment/Infrastructure channels are unimplemented stubs.
