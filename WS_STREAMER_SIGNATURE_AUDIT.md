# Phase 0: WebSocket Streamer Signature Audit

## Target

`backend_app/backend/ws_event_stream.py` -> `WebSocketEventStreamer`

## `handle_connection()` Signature

```python
async def handle_connection(
    self,
    websocket: Any,
    tenant_id: str,
    connection_id: Optional[str] = None
) -> str:
```

**Required Parameters:**
1. `websocket`: The FastAPI WebSocket instance.
2. `tenant_id`: The tenant string, extracted from the authenticated JWT.

**Optional Parameters:**
1. `connection_id`: If omitted, a UUID will be generated.

## Authentication Expectations

Authentication is **not** handled inside `WebSocketEventStreamer`. The `WebSocketEventStreamer` expects authentication to be completed *before* calling `handle_connection()`, and expects the `tenant_id` to be passed as a verified parameter.

## Tenant Requirements

The `tenant_id` string is strictly enforced. It is used to isolate all subscriptions, connections, and replay buffers inside `SubscriptionManager` and `EventReplayBuffer`.

## Subscription Message Format

The websocket route expects JSON payload from the client:
```json
{
  "type": "subscribe",
  "channel": "<channel_name>"
}
```

Valid channels (from `is_valid_channel()` check):
- `bot_status`
- `signal_trace`
- `execution_events`
- `risk_events`
- `deployment_events`

## Replay Buffer Behavior

The replay buffer can be triggered by sending:
```json
{
  "type": "replay",
  "channel": "<channel_name>",
  "since_sequence_id": 123
}
```
Or timestamp-based:
```json
{
  "type": "replay",
  "channel": "<channel_name>",
  "since_timestamp": "2024-01-01T00:00:00Z"
}
```

The streamer maintains monotonic sequence IDs per channel per tenant for deduplication and ordered delivery.
