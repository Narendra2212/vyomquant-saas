# Connection Layer Extraction Report

Generated: 2026-06-17 23:43

## Summary
- Items identified: 22
- Items copied: 22

## Extracted Components

| Component | Description |
|-----------|-------------|
| `connection/` | Core exchange connector infrastructure |
| `api_ws/` | WebSocket API endpoints |
| `backend_conn/` | Connection-layer backend modules |

## Components Identified (22 total)
- `connection/ (core exchange connector infrastructure)`
- `api_ws/ (WebSocket API endpoints)`
- `backend\event_publisher.py`
- `backend\exchange_executor.py`
- `backend\exchange_reconciliation.py`
- `backend\exchange_simulator.py`
- `backend\exchange_telemetry.py`
- `backend\exchange_websocket_listener.py`
- `backend\market_data_validation.py`
- `backend\websocket_cluster.py`
- `backend\websocket_manager.py`
- `backend\websocket_monitor.py`
- `backend\ws_channels.py`
- `backend\ws_event_stream.py`
- `backend\ws_server.py`
- `backend\distributed_execution\exchange_reconciliation_engine.py`
- `backend\distributed_execution\idempotent_exchange_submission.py`
- `backend\exchange_validation\exchange_behavior_validator.py`
- `backend\exchange_validation\stale_feed_validator.py`
- `backend\validation\websocket_sequence_validator.py`
- `backend\validation_runtime\websocket_gap_alarm.py`
- `backend\observability\load_testing\websocket_stress_test.py`

## Keyword Filter Applied
exchange, connector, binance, bybit, okx, bitget, kucoin, coinbase, kraken, websocket, ws_, _ws, market_data, redis_pub, pubsub, event_publisher, streaming, feed, ticker, orderbook

## Notes
- No imports rewritten
- Original source UNTOUCHED
