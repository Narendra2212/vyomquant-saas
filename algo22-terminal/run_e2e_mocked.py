import sys
import os
import json
from fastapi.testclient import TestClient

sys.path.append(r"d:\aerora_quant_backend_updated_final1\aerora_quant_backend_updated_final1")
from main import app
client = TestClient(app)

async def mock_get_current_user():
    return {"id": "mock_user", "email": "mock@test.com"}

from core.dependencies import get_current_user
app.dependency_overrides[get_current_user] = mock_get_current_user

ser_nodes = [
  { "id": "1", "type": "input", "label": "Data", "params": {"symbol": "BTCUSDT", "timeframe": "1h"}, "symbol": "BTCUSDT", "timeframe": "1h" },
  { "id": "2", "type": "indicator", "label": "RSI(14)", "params": {"period": 14}, "indicator": "rsi" },
  { "id": "3", "type": "logic", "label": "GT(70)", "params": {"threshold": 70}, "operator": "GT" },
  { "id": "4", "type": "action", "label": "SELL", "params": {"amount": 1.0, "order_type": "market"}, "action": "sell", "amount": 1.0, "order_type": "market" }
]

ser_edges = [
  { "source": "1", "target": "2" },
  { "source": "2", "target": "3" },
  { "source": "3", "target": "4" }
]

def print_res(res):
    print("STATUS:", res.status_code)
    try:
        print(json.dumps(res.json(), indent=2))
    except:
        print(res.text)
    print("\n")

print("=== PHASE 2: VALIDATE ===")
validate_payload = { "dag": { "nodes": ser_nodes, "edges": ser_edges } }
r1 = client.post("/api/strategies/validate", json=validate_payload)
print_res(r1)

print("=== PHASE 3: SAVE ===")
save_payload = {
    "name": "Golden Strategy",
    "nodes": ser_nodes,
    "edges": ser_edges,
    "buy_logic": { "operator": "AND", "conditions": [] },
    "sell_logic": { "operator": "AND", "conditions": [] },
    "risk": { "position_size_pct": 0.1, "stop_loss_pct": 0.05, "take_profit_pct": 0.1 },
    "symbol": "BTCUSDT",
    "timeframe": "1h"
}
r2 = client.post("/api/strategies", json=save_payload)
print_res(r2)
strategy_id = r2.json().get("strategy_id")

if strategy_id:
    print(f"Captured strategy_id: {strategy_id}\n")
    print("=== PHASE 4: UPDATE ===")
    update_payload = save_payload.copy()
    update_payload["name"] = "Golden Strategy (Updated)"
    r3 = client.put(f"/api/strategies/{strategy_id}", json=update_payload)
    print_res(r3)

print("=== PHASE 5: BACKTEST ===")
backtest_payload = {
    "strategies": ["Golden Strategy"],
    "symbols": ["BTCUSDT"],
    "timeframe": "1h",
    "initial_capital": 1000,
    "trade_size_pct": 0.1,
    "stop_loss_pct": 0.02,
    "take_profit_pct": 0.04,
    "ml_threshold": 0.75,
    "params": {},
    "dag": {
        "nodes": ser_nodes,
        "edges": ser_edges,
        "symbols": ["BTCUSDT"],
        "timeframe": "1h",
        "strategy_name": "Golden Strategy"
    }
}
r4 = client.post("/api/strategies/backtest", json=backtest_payload)
print_res(r4)

if strategy_id:
    print("=== PHASE 6: DEPLOY ===")
    deploy_payload = { "exchange_id": "binance" }
    r5 = client.post(f"/api/strategies/{strategy_id}/deploy", json=deploy_payload)
    print_res(r5)
