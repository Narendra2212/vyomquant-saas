# INFRASTRUCTURE_CHANNEL_AUDIT.md

## Sprint 1D.3 — Phase 4: Infrastructure Channel Audit
**Generated:** 2026-07-17T19:44:00Z
**Audit Classification:** DEAD / ORPHANED

---

## Objective
Determine whether the WebSocket channel `infrastructure` is:
- **ACTIVE**: Emitting events in production, with publishers and subscribers.
- **DEAD**: Defined, but no active emitter invokes it during runtime.
- **ORPHANED**: Frontend expects/subscribes to it, but the backend lacks emitters or vice-versa.

---

## Audit Methodology
1. **Search Targets**:
   - `infrastructure` (channel name)
   - `ChannelType.INFRASTRUCTURE` (the Python Enum value)
   - `WS_CHANNELS.INFRASTRUCTURE` (the Frontend constants)

2. **Analysis Scope**:
   - Backend `backend_app` codebase (routers, managers, event streams)
   - Frontend `algo22-terminal` codebase (source components, websocket subscribers)

---

## Key Findings

### 1. Backend Code Analysis
- **Definition**: The enum `ChannelType.INFRASTRUCTURE = "infrastructure"` is declared in `backend_app/backend/ws_channels.py` on line 23.
- **Event Types**: There are no event type definitions for `infrastructure` in `ws_channels.py`. The `CHANNEL_EVENTS` mapping completely omits `ChannelType.INFRASTRUCTURE`.
- **Emitters**: There is no helper function (e.g. `publish_infrastructure`) in `backend_app/backend/ws_event_stream.py`. A search for `ChannelType.INFRASTRUCTURE` confirms it is **never** used to publish events anywhere in the backend application.

### 2. Frontend Code Analysis
- **Definition**: `WS_CHANNELS.INFRASTRUCTURE: 'infrastructure'` is defined in `algo22-terminal/src/constants/wsChannels.js`.
- **Subscribers**: The component `InfrastructureOperations.jsx` attempts to subscribe to `WS_CHANNELS.INFRASTRUCTURE` to receive metric updates (like exchange latency, queue depth, active workers, etc.):
  ```javascript
  unsubscribeMetrics = wsClient.subscribe(WS_CHANNELS.INFRASTRUCTURE, handleMetricsUpdate);
  ```
- **Result**: Because the backend never publishes to the `infrastructure` channel, the frontend `InfrastructureOperations` page is **orphaned** of live telemetry. The values displayed on this SRE view are either initial local react mock states or static values since no WebSocket payload updates ever arrive.

---

## Verdict & Recommendations
- **Classification**: **DEAD / ORPHANED** (Backend is DEAD; Frontend is ORPHANED).
- **Explanation**: The backend declares the channel but implements no event types or publishers. The frontend actively subscribes to it, expecting telemetry that never comes.
- **Recommendation**:
  - The SRE dashboard should poll the REST `/api/health` and `/api/metrics` APIs to populate its tables instead of relying on a dead WebSocket stream.
  - Alternatively, a backend task should be scheduled to gather system and queue metrics and publish them periodically to the `infrastructure` channel.
