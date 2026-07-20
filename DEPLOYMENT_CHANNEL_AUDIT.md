# DEPLOYMENT_CHANNEL_AUDIT.md

## Sprint 1D.3 — Phase 3: Deployment Channel Audit
**Generated:** 2026-07-17T19:42:00Z
**Audit Classification:** DEAD / ORPHANED

---

## Objective
Determine whether the WebSocket channel `deployment_events` is:
- **ACTIVE**: Emitting events in production, with publishers and subscribers.
- **DEAD**: Defined, but no active emitter invokes it during runtime.
- **ORPHANED**: Frontend expects/subscribes to it, but the backend lacks emitters or vice-versa.

---

## Audit Methodology
1. **Search Targets**:
   - `publish_deployment` (the event publication wrapper function)
   - `deployment_events` (the channel label used in the streaming layer)
   - `ChannelType.DEPLOYMENT_EVENTS` (the Python Enum value)

2. **Analysis Scope**:
   - Backend `backend_app` codebase (routers, execution loops, workers)
   - Frontend `algo22-terminal` codebase (source components, websocket subscribers)
   - Test suites and validation wrappers

---

## Key Findings

### 1. Backend Code Analysis
- **Definition**: The enum `ChannelType.DEPLOYMENT_EVENTS = "deployment_events"` is defined in `backend_app/backend/ws_channels.py`.
- **Publisher Implementation**: The function `publish_deployment()` is declared on line 1186 in `backend_app/backend/ws_event_stream.py`:
  ```python
  async def publish_deployment(
      streamer: WebSocketEventStreamer,
      tenant_id: str,
      strategy_id: str,
      deployment_data: Dict[str, Any]
  ) -> WebSocketMessage:
      ...
  ```
- **Emissions**: A case-sensitive search for `publish_deployment` across the entire `backend_app` directory confirms that it is **never** imported or called by the live strategy router, deployment orchestrator, or worker threads.
- **Only Active Usage**: The function is exclusively called in the test script `test_ws_events.py` to verify that the websocket routing layer works.

### 2. Frontend Code Analysis
- **Definition**: `WS_CHANNELS.DEPLOYMENT_EVENTS: 'deployment_events'` is defined in `algo22-terminal/src/constants/wsChannels.js` on line 28.
- **Subscribers**: There are no React components or dashboard views in the frontend that subscribe to or display messages from the `deployment_events` channel. The frontend subscribes to `bot_status`, `signal_trace`, `execution_events`, and `risk_events` but does not mount a subscriber for `deployment_events`.

---

## Verdict & Recommendations
- **Classification**: **DEAD**
- **Explanation**: The deployment channel is fully implemented in the websocket routing tier but is completely dead at the engine layer (no emitters) and frontend layer (no subscribers).
- **Recommendation**:
  - Keep the channel type and helper functions for future extensions (e.g. strategy deploy state dashboard widget).
  - Remove from active telemetry status reviews since it does not carry live production traffic.
