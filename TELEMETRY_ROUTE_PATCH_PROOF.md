# Phase 1: Telemetry Route Mount & Tenant Policy Proof

## Objective
Verify the `/ws/telemetry` route mounts correctly, authenticates via token, respects the tenant policy, and accurately subscribes to channels without string indexing runtime crashes.

## Proof of Fix

### 1. Client Connects to /ws/telemetry?token=test_token
Client connects using the query parameter token.
```
INFO:     127.0.0.1:63363 - "WebSocket /ws/telemetry?token=test_token" [accepted]
INFO:WSRoutes:[WS/telemetry] Connection accepted for tenant test_user_id_123
INFO:ws_event_stream:Registered connection da214e63-f0fb-4362-8d24-2eef68d93263 for tenant test_user_id_123
```

### 2. Client Sends a Subscribe Message
Client script sends:
```json
{"type": "subscribe", "channel": "bot_status"}
```

### 3. Server Logs Show Subscription
The server successfully reads the payload, validates the channel, and registers the subscription on the `SubscriptionManager`.
```
INFO:ws_event_stream:Client da214e63-f0fb-4362-8d24-2eef68d93263 subscribed to bot_status
INFO:ws_event_stream:Client da214e63-f0fb-4362-8d24-2eef68d93263 subscribed to signal_trace
```

### 4. Client Receives Acknowledgment
The server responds correctly via `send_text` (resolving the FastAPI `send` exception):
```
Received: {"type": "subscribed", "channel": "bot_status"}
Received: {"type": "subscribed", "channel": "signal_trace"}
```

### 5. Replay Buffer Triggered
Client sends:
```json
{"type": "replay"}
```
Server responds (with empty replay as none were pushed):
```
INFO:ws_event_stream:Replayed 0 events to da214e63-f0fb-4362-8d24-2eef68d93263
```
Client output:
```json
Event: {"type": "replay_complete", "events_replayed": 0, "since_sequence_id": null}
```

## Resolution
- **Route Mount**: Added `@ws_router.websocket("/ws/telemetry")` invoking `ws_streamer.handle_connection`.
- **Lifespan Startup**: Added `await ws_streamer.start()` inside `backend_app/main.py` lifespan to start heartbeat and cleanup background loops.
- **FastAPI Exception Fixed**: Replaced `.send(json.dumps(...))` with `.send_text(json.dumps(...))` in `backend_app/backend/ws_event_stream.py` to correctly send text frames, eliminating `TypeError: string indices must be integers, not 'str'`.
- **Tenant Policy**: Ensured `tenant_id` propagation from token mapping down into the streamer.
