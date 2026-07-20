import httpx
import json
import time
import os

BASE_URL = "http://127.0.0.1:8000"

def log_proof(stage, req, res):
    with open("END_TO_END_FRONTEND_EXECUTION_PROOF.md", "a") as f:
        f.write(f"### {stage}\n")
        f.write("**Request Payload:**\n```json\n" + json.dumps(req, indent=2) + "\n```\n")
        f.write("**Response:**\n```json\n" + json.dumps(res, indent=2) + "\n```\n\n")

def run_test():
    client = httpx.Client(base_url=BASE_URL)

    with open("END_TO_END_FRONTEND_EXECUTION_PROOF.md", "w") as f:
        f.write("# End-to-End Frontend Execution Proof\n\n")

    # Golden Strategy
    nodes = [
        {"id": "n-input", "type": "input", "symbol": "BTCUSDT", "timeframe": "1h"},
        {"id": "n-rsi", "type": "indicator", "indicator": "rsi", "params": {"window": 14}},
        {"id": "n-gt", "type": "logic", "operator": "GT", "params": {"value": 70}},
        {"id": "n-sell", "type": "action", "action": "sell", "order_type": "market", "amount": 0.15}
    ]
    edges = [
        {"id": "e1", "source": "n-input", "target": "n-rsi"},
        {"id": "e2", "source": "n-rsi", "target": "n-gt"},
        {"id": "e3", "source": "n-gt", "target": "n-sell"}
    ]
    
    # 1. Validate
    val_payload = {"dag": {"nodes": nodes, "edges": edges}}
    r_val = client.post("/api/strategies/validate", json=val_payload)
    # The endpoint might need auth. We can create a mock user or use test client.
    # Ah, the endpoints require user auth! Let's mock the auth or login.
    # I'll create a user first.
    return r_val.json()

if __name__ == "__main__":
    print(run_test())
