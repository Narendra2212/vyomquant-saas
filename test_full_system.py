import requests
import json
import time

BASE_URL = "http://127.0.0.1:8000"

def print_section(title):
    print("\n" + "="*50)
    print(f"🚀 {title}")
    print("="*50)

def test_health():
    print_section("HEALTH CHECK")
    try:
        r = requests.get(f"{BASE_URL}/docs")
        print("Docs status:", r.status_code)
        return r.status_code == 200
    except Exception as e:
        print("Health failed:", e)
        return False

def test_auth():
    print_section("AUTH TEST")
    try:
        r = requests.post(
            f"{BASE_URL}/api/auth/signin",
            json={"email": "test@test.com", "password": "test123"}
        )
        print("Auth status:", r.status_code)
        if r.status_code == 200:
            token = r.json().get("access_token")
            print("Token received ✅")
            return token
        else:
            print("Auth failed:", r.text)
            return None
    except Exception as e:
        print("Auth error:", e)
        return None

def test_backtest(token):
    print_section("BACKTEST TEST")
    try:
        headers = {"Authorization": f"Bearer {token}"}

        payload = {
            "strategies": ["rsi"],
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "initial_capital": 10000,
            "trade_size_pct": 0.1,
            "stop_loss_pct": 0.02,
            "take_profit_pct": 0.04
        }

        r = requests.post(
            f"{BASE_URL}/api/strategies/backtest",
            headers=headers,
            json=payload
        )

        print("Backtest status:", r.status_code)

        if r.status_code == 200:
            print("Backtest response:")
            print(json.dumps(r.json(), indent=2)[:500])
            return True
        else:
            print("Backtest failed:", r.text)
            return False

    except Exception as e:
        print("Backtest error:", e)
        return False

def test_infra_logs():
    print_section("INFRA CHECK")
    print("Check logs manually for:")
    print("⚠️ Redis fallback")
    print("⚠️ QuestDB fallback")
    print("⚠️ Supabase mock")
    print("👉 These should NOT crash system")

def run_all_tests():
    results = {}

    results["health"] = test_health()

    token = test_auth()
    results["auth"] = token is not None

    if token:
        results["backtest"] = test_backtest(token)
    else:
        results["backtest"] = False

    test_infra_logs()

    print_section("FINAL REPORT")

    for k, v in results.items():
        print(f"{k.upper()}: {'✅ PASS' if v else '❌ FAIL'}")

    score = sum(results.values()) / len(results) * 100
    print(f"\n🎯 SYSTEM SCORE: {score:.1f}%")

if __name__ == "__main__":
    run_all_tests()