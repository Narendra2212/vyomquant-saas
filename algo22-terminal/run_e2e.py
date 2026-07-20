import requests
import json

base_url = "http://localhost:8000/api/strategies"
headers = {"Content-Type": "application/json", "Authorization": "Bearer mock-token"}

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
r1 = requests.post(f"{base_url}/validate", json=validate_payload, headers=headers)
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
r2 = requests.post(base_url, json=save_payload, headers=headers)
print_res(r2)
strategy_id = r2.json().get("strategy_id")

if strategy_id:
    print("=== PHASE 4: UPDATE ===")
    update_payload = save_payload.copy()
    update_payload["name"] = "Golden Strategy (Updated)"
    r3 = requests.put(f"{base_url}/{strategy_id}", json=update_payload, headers=headers)
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
r4 = requests.post(f"{base_url}/backtest", json=backtest_payload, headers=headers)
print_res(r4)

if strategy_id:
    print("=== PHASE 6: DEPLOY ===")
    deploy_payload = { "exchange_id": "binance" }
    r5 = requests.post(f"{base_url}/{strategy_id}/deploy", json=deploy_payload, headers=headers)
    print_res(r5)
