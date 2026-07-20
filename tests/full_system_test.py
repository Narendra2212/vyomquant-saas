import requests
import time

BASE_URL = "http://127.0.0.1:8000"
HEADERS = {"Authorization": "Bearer dev_bypass"}

def log(msg):
    print(f"[TEST] {msg}")

def test_health():
    log("Checking health...")
    res = requests.get(f"{BASE_URL}/health")
    assert res.status_code == 200
    log("Health OK")

def test_symbols():
    log("Checking market symbols...")
    res = requests.get(f"{BASE_URL}/api/market/symbols")
    assert res.status_code == 200
    data = res.json()
    assert len(data) > 0
    log(f"Symbols OK: {data[:3]}")

def run_backtest():
    log("Running backtest...")

    payload = {
        "strategies": ["rsi"],
        "symbol": "BTCUSDT",
        "timeframe": "1h"
    }

    res = requests.post(
        f"{BASE_URL}/api/strategies/backtest",
        headers=HEADERS,
        json=payload
    )

    assert res.status_code == 200

    data = res.json()

    log(f"Backtest Result: {data}")

    # Validate structure
    assert "total_return_pct" in data or "total_return" in data
    assert "win_rate_pct" in data or "win_rate" in data
    assert "total_trades" in data or "trades_count" in data

    return data

def test_determinism():
    log("Checking determinism...")

    r1 = run_backtest()
    time.sleep(1)
    r2 = run_backtest()

    # Verify response schemas are identical
    assert r1.keys() == r2.keys(), "Response schemas do not match"
    
    # Check that total return is very close (within 1.0% tolerance) since the latest candle updates in real-time
    r1_ret = r1.get("total_return_pct", 0.0)
    r2_ret = r2.get("total_return_pct", 0.0)
    diff = abs(r1_ret - r2_ret)
    assert diff < 1.0, f"Results diverged too much! {r1_ret} vs {r2_ret}"

    log("Determinism OK")

def test_sanity():
    log("Checking sanity...")

    data = run_backtest()

    trades_key = "total_trades" if "total_trades" in data else "trades_count"
    win_rate_key = "win_rate_pct" if "win_rate_pct" in data else "win_rate"

    assert data[trades_key] >= 0
    assert 0 <= data[win_rate_key] <= 100

    # Warn (not fail)
    if data[win_rate_key] == 100:
        print("[WARNING] Win rate is 100% -> unrealistic")

    log("Sanity OK")

if __name__ == "__main__":
    print("\n=== FULL SYSTEM TEST STARTED ===\n")

    test_health()
    test_symbols()
    test_sanity()
    test_determinism()

    print("\n=== ALL TESTS PASSED ===\n")