"""
API Flow Test Script
Systematically tests all API endpoints to verify:
- Request reaches backend
- Response returns correctly
- Logs failures
"""

import requests
import json
from typing import Dict, Any
from datetime import datetime

BASE_URL = "http://localhost:8000"


class APITester:
    def __init__(self):
        self.results = []
        self.session = requests.Session()
        self.token = None

    def log_result(
        self,
        endpoint: str,
        method: str,
        success: bool,
        error: str = None,
        response_data: Any = None,
    ):
        result = {
            "timestamp": datetime.now().isoformat(),
            "endpoint": endpoint,
            "method": method,
            "success": success,
            "error": error,
            "response_data": str(response_data)[:200] if response_data else None,
        }
        self.results.append(result)
        status = "✓ PASS" if success else "✗ FAIL"
        print(f"{status} - {method} {endpoint}")
        if error:
            print(f"  Error: {error}")

    def test_get(self, endpoint: str) -> bool:
        try:
            response = self.session.get(f"{BASE_URL}{endpoint}")
            success = response.status_code < 400
            self.log_result(
                endpoint,
                "GET",
                success,
                error=f"Status {response.status_code}" if not success else None,
                response_data=response.json() if response.content else None,
            )
            return success
        except Exception as e:
            self.log_result(endpoint, "GET", False, error=str(e))
            return False

    def test_post(self, endpoint: str, data: Dict[str, Any]) -> bool:
        try:
            response = self.session.post(f"{BASE_URL}{endpoint}", json=data)
            success = response.status_code < 400
            self.log_result(
                endpoint,
                "POST",
                success,
                error=f"Status {response.status_code}" if not success else None,
                response_data=response.json() if response.content else None,
            )
            return success
        except Exception as e:
            self.log_result(endpoint, "POST", False, error=str(e))
            return False

    def test_put(self, endpoint: str, data: Dict[str, Any]) -> bool:
        try:
            response = self.session.put(f"{BASE_URL}{endpoint}", json=data)
            success = response.status_code < 400
            self.log_result(
                endpoint,
                "PUT",
                success,
                error=f"Status {response.status_code}" if not success else None,
                response_data=response.json() if response.content else None,
            )
            return success
        except Exception as e:
            self.log_result(endpoint, "PUT", False, error=str(e))
            return False

    def test_delete(self, endpoint: str) -> bool:
        try:
            response = self.session.delete(f"{BASE_URL}{endpoint}")
            success = response.status_code < 400
            self.log_result(
                endpoint,
                "DELETE",
                success,
                error=f"Status {response.status_code}" if not success else None,
                response_data=response.json() if response.content else None,
            )
            return success
        except Exception as e:
            self.log_result(endpoint, "DELETE", False, error=str(e))
            return False

    def run_all_tests(self):
        print("=" * 60)
        print("API FLOW TEST - Starting Systematic Endpoint Testing")
        print("=" * 60)

        # Exchange API
        print("\n--- EXCHANGE API ---")
        self.test_get("/api/exchanges/supported")
        self.test_get("/api/exchanges/")
        self.test_post(
            "/api/exchanges/test",
            {
                "exchange_id": "binance",
                "api_key": "test_key",
                "secret_key": "test_secret",
                "label": "test",
            },
        )

        # Market API
        print("\n--- MARKET API ---")
        self.test_get("/api/market/symbols")
        self.test_get("/api/market/ticker/BTC%2FUSDT")
        self.test_get("/api/market/orderbook/BTC%2FUSDT?limit=10")
        self.test_get("/api/market/candles/BTC%2FUSDT/1m?limit=150")

        # Strategy API
        print("\n--- STRATEGY API ---")
        self.test_get("/api/strategies")
        self.test_post(
            "/api/strategies/backtest",
            {
                "strategy_name": "test_strategy",
                "nodes": [],
                "edges": [],
                "timeframe": "15m",
                "initial_capital": 10000,
                "trade_size_pct": 10,
                "ml_threshold": 0.7,
                "stop_loss_pct": 2,
                "take_profit_pct": 4,
            },
        )

        # Order API
        print("\n--- ORDER API ---")
        self.test_get("/api/orders/trades/history")
        self.test_post(
            "/api/orders/execute",
            {
                "symbol": "BTC/USDT",
                "side": "buy",
                "order_type": "market",
                "amount": 0.01,
                "order_id": "00000000-0000-0000-0000-000000000000",
            },
        )

        # Risk API
        print("\n--- RISK API ---")
        self.test_get("/api/risk/config")
        self.test_get("/api/risk/strategy-limits")
        self.test_get("/api/risk/margin-health")
        self.test_put(
            "/api/risk/config",
            {
                "max_daily_loss": 500,
                "max_open_positions": 10,
                "max_leverage": 3,
                "kill_switches": {},
            },
        )

        # Billing API
        print("\n--- BILLING API ---")
        self.test_get("/api/billing/plans")
        self.test_get("/api/billing/invoices")
        self.test_get("/api/billing/payment-methods")
        self.test_post("/api/billing/checkout", {"planId": "pro", "currency": "USD"})

        # Notification API
        print("\n--- NOTIFICATION API ---")
        self.test_get("/api/notifications/settings")
        self.test_put(
            "/api/notifications/settings",
            {"channels": {}, "triggers": {}, "max_alerts_per_minute": 10},
        )

        # Support API
        print("\n--- SUPPORT API ---")
        self.test_get("/api/support/tickets")
        self.test_post(
            "/api/support/tickets",
            {"subject": "Test ticket", "message": "Test message", "priority": "medium"},
        )

        # User API
        print("\n--- USER API ---")
        self.test_get("/api/user/profile")
        self.test_get("/api/user/stats")
        self.test_get("/api/user/referral/stats")

        # Leaderboard API
        print("\n--- LEADERBOARD API ---")
        self.test_get("/api/leaderboard/?period=7d")

        # Print Summary
        print("\n" + "=" * 60)
        print("TEST SUMMARY")
        print("=" * 60)
        passed = sum(1 for r in self.results if r["success"])
        total = len(self.results)
        print(f"Total Tests: {total}")
        print(f"Passed: {passed}")
        print(f"Failed: {total - passed}")

        if total - passed > 0:
            print("\nFAILED TESTS:")
            for r in self.results:
                if not r["success"]:
                    print(f"  {r['method']} {r['endpoint']}: {r['error']}")

        # Save results to file
        with open("api_flow_test_results.json", "w") as f:
            json.dump(self.results, f, indent=2)
        print("\nResults saved to api_flow_test_results.json")


if __name__ == "__main__":
    tester = APITester()
    tester.run_all_tests()
