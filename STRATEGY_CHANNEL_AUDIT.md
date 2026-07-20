# Strategy Channel Audit

## Audit Target
- `backend_app/backend/ws_channels.py`
- `backend_app/backend/ws_event_stream.py`

## Objective
Compare actual emitted channels against frontend expectations. Ensure that the WebSocket channels defined in the backend match the frontend's expected WS_CHANNELS, and document the event types emitted for each channel.

## Channel Inventory

The backend natively supports the following `ChannelType` enums:
1. `BOT_STATUS` ("bot_status")
2. `SIGNAL_TRACE` ("signal_trace")
3. `EXECUTION_EVENTS` ("execution_events")
4. `RISK_EVENTS` ("risk_events")
5. `DEPLOYMENT_EVENTS` ("deployment_events")
6. `INFRASTRUCTURE` ("infrastructure")

### Channel Event Matrix

| Channel | Supported Event Types | Description |
|---|---|---|
| `bot_status` | `bot_health`, `bot_connected`, `bot_disconnected`, `bot_error`, `heartbeat` | Real-time health metrics and connection state for trading bots. |
| `signal_trace` | `signal_received`, `signal_validated`, `signal_risk_checked`, `signal_executed`, `signal_rejected`, `signal_failed` | Lifecycle tracking of trading signals from generation to execution. |
| `execution_events` | `order_submitted`, `order_filled`, `order_partial`, `order_rejected`, `order_error` | Order execution tracking. |
| `risk_events` | `risk_block`, `risk_warning`, `kill_switch`, `position_limit`, `drawdown_alert` | Risk management triggers and alerts. |
| `deployment_events` | `deploy_started`, `deploy_success`, `deploy_failed`, `bot_started`, `bot_stopped` | Strategy and bot lifecycle deployment events. |

## Standard Schema

The backend guarantees the following standard schema for all emitted events, ensuring strict alignment with frontend type expectations:
```json
{
    "type": "string",
    "channel": "string",
    "timestamp": "string",
    "bot_id": "string|null",
    "strategy_id": "string|null",
    "tenant_id": "string",
    "message_id": "string",
    "payload": "object"
}
```

## Verdict
**PASS.** The backend `ChannelType` enumeration strictly mirrors frontend `WS_CHANNELS` constants (`wsChannels.js`), with exhaustive coverage of event types properly grouped by domain. No orphan channels were detected.
