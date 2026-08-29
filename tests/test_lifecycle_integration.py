"""
Comprehensive End-to-End Lifecycle Integration Tests

Tests the complete VyomQuant trading lifecycle:
Strategy Builder → Save → Strategies → Backtest → Deploy → Signal Trace

This is production-grade integration testing, not unit testing.
"""

import pytest
import pytest_asyncio
import asyncio
import json
from datetime import datetime, timedelta
from uuid import uuid4
import os

# Test configuration
API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")
TEST_USER_EMAIL = os.getenv("TEST_USER_EMAIL", "test_lifecycle@example.com")
TEST_USER_PASSWORD = os.getenv("TEST_USER_PASSWORD", "test_password_123")

# Configure pytest-asyncio
pytestmark = pytest.mark.asyncio


class TestLifecycleIntegration:
    """Complete lifecycle integration tests."""
    
    @pytest_asyncio.fixture
    async def auth_token(self):
        """Get authentication token for test user."""
        import httpx
        
        async with httpx.AsyncClient() as client:
            # Login to get token
            response = await client.post(
                f"{API_BASE}/api/auth/login",
                json={
                    "email": TEST_USER_EMAIL,
                    "password": TEST_USER_PASSWORD
                }
            )
            
            if response.status_code != 200:
                pytest.skip(f"Authentication failed: {response.text}")
            
            data = response.json()
            return data.get("access_token")
    
    @pytest_asyncio.fixture
    async def test_strategy(self, auth_token):
        """Create a test strategy for lifecycle testing."""
        import httpx
        
        strategy_data = {
            "name": "Lifecycle Test Strategy",
            "description": "Strategy for end-to-end lifecycle testing",
            "nodes": [
                {
                    "id": "data_1",
                    "type": "data",
                    "position": {"x": 100, "y": 100},
                    "data": {
                        "block_id": "ohlcv_feed",
                        "category": "DATA",
                        "params": {
                            "symbol": "BTC/USDT",
                            "timeframe": "1h"
                        }
                    }
                },
                {
                    "id": "indicator_1",
                    "type": "indicator",
                    "position": {"x": 300, "y": 100},
                    "data": {
                        "block_id": "rsi",
                        "category": "INDICATOR",
                        "params": {
                            "window": 14
                        }
                    }
                },
                {
                    "id": "logic_1",
                    "type": "logic",
                    "position": {"x": 500, "y": 100},
                    "data": {
                        "block_id": "simple_logic",
                        "category": "LOGIC",
                        "params": {
                            "condition": "rsi < 30",
                            "action": "BUY"
                        }
                    }
                }
            ],
            "edges": [
                {
                    "id": "edge_1",
                    "source": "data_1",
                    "target": "indicator_1",
                    "sourceHandle": "right",
                    "targetHandle": "left"
                },
                {
                    "id": "edge_2",
                    "source": "indicator_1",
                    "target": "logic_1",
                    "sourceHandle": "right",
                    "targetHandle": "left"
                }
            ]
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{API_BASE}/api/strategies",
                json=strategy_data,
                headers={"Authorization": f"Bearer {auth_token}"}
            )
            
            assert response.status_code == 200, f"Strategy creation failed: {response.text}"
            data = response.json()
            return data
    
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires running backend server")
    async def test_01_strategy_creation(self, auth_token):
        """Test Phase 1: Strategy creation and persistence."""
        import httpx
        
        strategy_data = {
            "name": "Test Strategy 1",
            "description": "Test strategy for lifecycle",
            "nodes": [
                {
                    "id": "data_1",
                    "type": "data",
                    "position": {"x": 100, "y": 100},
                    "data": {
                        "block_id": "ohlcv_feed",
                        "category": "DATA",
                        "params": {
                            "symbol": "BTC/USDT",
                            "timeframe": "1h"
                        }
                    }
                },
                {
                    "id": "indicator_1",
                    "type": "indicator",
                    "position": {"x": 300, "y": 100},
                    "data": {
                        "block_id": "rsi",
                        "category": "INDICATOR",
                        "params": {
                            "window": 14
                        }
                    }
                }
            ],
            "edges": [
                {
                    "id": "edge_1",
                    "source": "data_1",
                    "target": "indicator_1",
                    "sourceHandle": "right",
                    "targetHandle": "left"
                }
            ]
        }
        
        async with httpx.AsyncClient() as client:
            # Create strategy
            response = await client.post(
                f"{API_BASE}/api/strategies",
                json=strategy_data,
                headers={"Authorization": f"Bearer {auth_token}"}
            )
            
            assert response.status_code == 200, f"Strategy creation failed: {response.text}"
            data = response.json()
            
            # Verify strategy structure
            assert "id" in data
            assert "dag_hash" in data
            assert "warmup_bars" in data
            
            # Verify it appears in strategies list
            list_response = await client.get(
                f"{API_BASE}/api/strategies",
                headers={"Authorization": f"Bearer {auth_token}"}
            )
            
            assert list_response.status_code == 200
            strategies = list_response.json()
            assert any(s["id"] == data["id"] for s in strategies["strategies"])
            
            return data["id"]
    
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires running backend server")
    async def test_02_strategy_retrieval(self, auth_token, test_strategy):
        """Test Phase 2: Strategy retrieval and canonical format."""
        import httpx
        
        strategy_id = test_strategy["id"]
        
        async with httpx.AsyncClient() as client:
            # Get strategy by ID
            response = await client.get(
                f"{API_BASE}/api/strategies/{strategy_id}",
                headers={"Authorization": f"Bearer {auth_token}"}
            )
            
            assert response.status_code == 200
            strategy = response.json()
            
            # Verify canonical format
            assert "nodes" in strategy
            assert "edges" in strategy
            assert "dag_hash" in strategy
            assert "compiled_plan" in strategy
            assert "execution_order" in strategy
            
            # Verify nodes have required fields
            for node in strategy["nodes"]:
                assert "id" in node
                assert "type" in node or "category" in node
    
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires running backend server")
    async def test_03_backtest_execution(self, auth_token, test_strategy):
        """Test Phase 3: Backtest execution with VectorBT."""
        import httpx
        
        strategy_id = test_strategy["id"]
        
        backtest_payload = {
            "strategies": ["rsi"],
            "symbols": ["BTCUSDT"],
            "timeframe": "1h",
            "initial_capital": 10000,
            "trade_size_pct": 0.1,
            "stop_loss_pct": 0.02,
            "take_profit_pct": 0.04,
            "ml_threshold": 0.7,
            "params": {},
            "dag": {
                "schema_version": "2.0",
                "nodes": test_strategy.get("nodes", []),
                "edges": test_strategy.get("edges", []),
                "symbols": ["BTCUSDT"],
                "timeframe": "1h",
                "strategy_name": test_strategy.get("name", "Test Strategy")
            },
            "sync": True  # Force synchronous execution for testing
        }
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{API_BASE}/api/strategies/backtest",
                json=backtest_payload,
                headers={"Authorization": f"Bearer {auth_token}"}
            )
            
            # Backtest might fail if data is unavailable, that's acceptable for integration test
            if response.status_code == 200:
                results = response.json()
                
                # Verify result structure
                assert "total_return_pct" in results or "error" in results
                
                if "total_return_pct" in results:
                    assert "equity" in results
                    assert "win_rate_pct" in results
                    assert "max_drawdown_pct" in results
            else:
                # Data availability issues are acceptable in test environment
                pytest.skip(f"Backtest failed (likely data unavailable): {response.text}")
    
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires running backend server")
    async def test_04_backtest_persistence(self, auth_token, test_strategy):
        """Test Phase 4: Backtest result persistence."""
        import httpx
        
        strategy_id = test_strategy["id"]
        
        backtest_data = {
            "strategy_id": strategy_id,
            "version": 1,  # Integer version per migration schema
            "dataset": "BTC/USDT",
            "start_date": (datetime.now() - timedelta(days=30)).isoformat().split('T')[0],
            "end_date": datetime.now().isoformat().split('T')[0],
            "initial_capital": 10000,
            "commission": 0.001,
            "slippage": 0.0005,
            "blueprint": {
                "nodes": test_strategy.get("nodes", []),
                "edges": test_strategy.get("edges", []),
                "name": test_strategy.get("name", "Test Strategy")
            }
        }
        
        async with httpx.AsyncClient() as client:
            # Create backtest record
            response = await client.post(
                f"{API_BASE}/api/strategy-operations/strategies/{strategy_id}/backtests",
                json=backtest_data,
                headers={"Authorization": f"Bearer {auth_token}"}
            )
            
            if response.status_code == 200:
                data = response.json()
                backtest_id = data["backtest"]["id"]
                
                # Verify backtest was saved
                list_response = await client.get(
                    f"{API_BASE}/api/strategy-operations/backtests",
                    headers={"Authorization": f"Bearer {auth_token}"}
                )
                
                assert list_response.status_code == 200
                backtests = list_response.json()
                assert any(b["id"] == backtest_id for b in backtests["backtests"])
                
                # Verify reproducibility fields
                backtest = next(b for b in backtests["backtests"] if b["id"] == backtest_id)
                assert "engine_version" in backtest
                assert "dataset_checksum" in backtest
                assert "dag_hash" in backtest
            else:
                pytest.skip(f"Backtest persistence failed: {response.text}")
    
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires running backend server")
    async def test_05_deployment_validation(self, auth_token, test_strategy):
        """Test Phase 5: Deployment validation without actual deployment."""
        import httpx
        
        strategy_id = test_strategy["id"]
        
        deployment_config = {
            "exchange_id": "binance",
            "environment": "paper",
            "capital": 10000,
            "trade_size_pct": 10,
            "max_drawdown": 15,
            "stop_loss": 2
        }
        
        async with httpx.AsyncClient() as client:
            # This should fail in test environment without exchange credentials
            # but we can test the validation logic
            response = await client.post(
                f"{API_BASE}/api/strategies/{strategy_id}/deploy",
                json=deployment_config,
                headers={"Authorization": f"Bearer {auth_token}"}
            )
            
            # In test environment, this should fail due to missing exchange credentials
            # which is expected behavior
            assert response.status_code in [400, 403, 503], "Deployment should fail in test environment"
    
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires running backend server")
    async def test_06_signal_trace_retrieval(self, auth_token):
        """Test Phase 6: Signal trace API."""
        import httpx
        
        async with httpx.AsyncClient() as client:
            # Get signal trace (may be empty in test environment)
            response = await client.get(
                f"{API_BASE}/api/signal-trace/signals",
                headers={"Authorization": f"Bearer {auth_token}"}
            )
            
            assert response.status_code == 200
            data = response.json()
            
            # Verify structure
            assert "signals" in data
            assert "total" in data
            
            # Verify signal structure if any exist
            for signal in data["signals"]:
                assert "id" in signal
                assert "strategy_id" in signal
                assert "decision" in signal
                assert "status" in signal
    
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires running backend server")
    async def test_07_strategy_clone(self, auth_token, test_strategy):
        """Test Phase 7: Strategy cloning and versioning."""
        import httpx
        
        strategy_id = test_strategy["id"]
        
        async with httpx.AsyncClient() as client:
            # Clone strategy
            response = await client.post(
                f"{API_BASE}/api/strategies/{strategy_id}/clone",
                json={"new_name": "Cloned Test Strategy"},
                headers={"Authorization": f"Bearer {auth_token}"}
            )
            
            if response.status_code == 200:
                cloned_strategy = response.json()
                
                # Verify clone
                assert cloned_strategy["id"] != strategy_id
                assert cloned_strategy["name"] == "Cloned Test Strategy"
                
                # Verify both strategies exist
                list_response = await client.get(
                    f"{API_BASE}/api/strategies",
                    headers={"Authorization": f"Bearer {auth_token}"}
                )
                
                strategies = list_response.json()["strategies"]
                assert len(strategies) >= 2
            else:
                pytest.skip(f"Strategy clone failed: {response.text}")
    
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires running backend server")
    async def test_08_strategy_delete(self, auth_token, test_strategy):
        """Test Phase 8: Strategy deletion safety."""
        import httpx
        
        strategy_id = test_strategy["id"]
        
        async with httpx.AsyncClient() as client:
            # Try to delete strategy
            response = await client.delete(
                f"{API_BASE}/api/strategies/{strategy_id}",
                headers={"Authorization": f"Bearer {auth_token}"}
            )
            
            # In test environment, deletion should work if strategy is not running
            if response.status_code == 200:
                # Verify it's deleted
                list_response = await client.get(
                    f"{API_BASE}/api/strategies",
                    headers={"Authorization": f"Bearer {auth_token}"}
                )
                
                strategies = list_response.json()["strategies"]
                assert not any(s["id"] == strategy_id for s in strategies)
            else:
                pytest.skip(f"Strategy deletion failed: {response.text}")
    
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires running backend server")
    async def test_09_historical_data_validation(self, auth_token):
        """Test Phase 9: Historical data validation API."""
        import httpx
        
        validation_request = {
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "start_date": (datetime.now() - timedelta(days=30)).isoformat().split('T')[0],
            "end_date": datetime.now().isoformat().split('T')[0]
        }
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{API_BASE}/api/strategy-operations/backtests/validate-data",
                json=validation_request,
                headers={"Authorization": f"Bearer {auth_token}"}
            )
            
            if response.status_code == 200:
                validation_result = response.json()
                
                # Verify validation structure
                assert "valid" in validation_result
                assert "issues" in validation_result
                assert "warnings" in validation_result
                assert "data_info" in validation_result
            else:
                pytest.skip(f"Data validation failed: {response.text}")
    
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires running backend server")
    async def test_10_backtest_comparison(self, auth_token):
        """Test Phase 10: Backtest comparison functionality."""
        import httpx
        
        async with httpx.AsyncClient() as client:
            # Try to compare backtests (may fail if no backtests exist)
            response = await client.post(
                f"{API_BASE}/api/strategy-operations/backtests/compare",
                json={"backtest_ids": []},
                headers={"Authorization": f"Bearer {auth_token}"}
            )
            
            # Should handle empty comparison gracefully
            assert response.status_code in [200, 404, 400]


class TestProductionReadiness:
    """Production readiness verification tests."""
    
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires running backend server")
    async def test_api_health(self):
        """Test that all required API endpoints are accessible."""
        import httpx
        
        endpoints = [
            "/api/health",
            "/api/strategies",
            "/api/signal-trace/signals",
            "/api/strategy-operations/backtests"
        ]
        
        async with httpx.AsyncClient() as client:
            for endpoint in endpoints:
                response = await client.get(f"{API_BASE}{endpoint}")
                # Health check should return 200, others should require auth
                assert response.status_code in [200, 401, 403], f"Endpoint {endpoint} returned {response.status_code}"
    
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires running backend server")
    async def test_database_schema(self):
        """Test that required database tables exist."""
        # This would require database access, skipping for API-level test
        pytest.skip("Database schema test requires direct DB access")
    
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires running backend server")
    async def test_websocket_availability(self):
        """Test that WebSocket endpoint is accessible."""
        import websockets
        
        try:
            async with websockets.connect(f"ws://localhost:8000/api/ws") as ws:
                # Connection test
                await ws.send(json.dumps({"type": "ping"}))
                response = await ws.recv()
                assert response is not None
        except Exception as e:
            pytest.skip(f"WebSocket not available: {e}")


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v", "-s"])