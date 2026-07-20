# Connection Layer Extraction Report

Generated: 2026-06-18

## Goal
Isolate exchange connectivity and event streaming components into a dedicated, long-running service at `aerora_quant_platform/connection_layer/`.

## Component Responsibilities
- Exchange connectivity (Binance, Bybit, OKX via CCXT)
- Market data ingestion
- WebSocket streaming to clients
- Event publishing (Redis PubSub)
- Feed reconciliation

## Extraction Scope
- `api_ws/` (WebSocket routes and managers)
- `backend/exchange_websocket_listener.py`
- `backend/exchange_simulator.py`
- `backend/ws_channels.py`
- `backend/ws_server.py`
- `backend/ws_event_stream.py`

## Execution Steps (Copy-Only)
```powershell
$src = "d:\aerora_quant_backend_updated_final1\aerora_quant_backend_updated_final1"
$dest = "d:\aerora_quant_backend_updated_final1\aerora_quant_platform\connection_layer"

Copy-Item -Path "$src\api_ws" -Destination "$dest" -Recurse -Force
New-Item -ItemType Directory -Force -Path "$dest\backend"
Copy-Item -Path "$src\backend\exchange_*.py" -Destination "$dest\backend\" -Force
Copy-Item -Path "$src\backend\ws_*.py" -Destination "$dest\backend\" -Force
```

## Rollback Instructions
```powershell
Remove-Item -Recurse -Force "d:\aerora_quant_backend_updated_final1\aerora_quant_platform\connection_layer\*"
```
