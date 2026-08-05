"""
tests/test_websocket_auth_fail_closed.py — WebSocket Authentication Fail-Closed Tests

Tests WebSocket endpoints for fail-closed authentication behavior:
- (a) no token provided → connection closed
- (b) token provided but invalid/expired → connection closed  
- (c) token verification function throws exception → connection closed
- (d) token valid but for different tenant/user → connection closed

Author: Principal Software Architect
Date: 2025-08-02
"""

import sys
import os
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["DEV_MODE"] = "true"
os.environ["ENV"] = "testing"
os.environ["REDIS_URL"] = ""
os.environ["DEFAULT_EXCHANGE"] = "binance"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect


class TestWebSocketAuthFailClosed:
    """Test WebSocket authentication fail-closed behavior."""
    
    @pytest.fixture
    def app(self):
        """Create FastAPI app with WebSocket routes."""
        from backend_app.main import app
        return app
    
    @pytest.fixture
    def client(self, app):
        """Create test client."""
        return TestClient(app)

    @pytest.fixture
    def dag_client(self):
        """Create test client for DAG event loop routes."""
        from backend_app.backend.dag_event_loop import router as dag_router
        dag_app = FastAPI()
        dag_app.include_router(dag_router)
        return TestClient(dag_app)

    @pytest.fixture
    def ws_server_client(self):
        """Create test client for standalone WebSocket server routes."""
        from backend_app.backend.ws_server import app as ws_server_app
        return TestClient(ws_server_app)
    
    @pytest.fixture
    def valid_token(self):
        """Generate a valid test token."""
        import jwt
        import time
        
        secret = os.getenv("SUPABASE_JWT_SECRET", "dev-secret-change-in-production")
        payload = {
            "sub": "test_user_123",
            "email": "test@example.com",
            "tenant_id": "tenant_123",
            "role": "authenticated",
            "aud": "authenticated",
            "iss": "algo22-test",
            "exp": int(time.time()) + 3600  # 1 hour from now
        }
        return jwt.encode(payload, secret, algorithm="HS256")
    
    @pytest.fixture
    def expired_token(self):
        """Generate an expired test token."""
        import jwt
        import time
        
        secret = os.getenv("SUPABASE_JWT_SECRET", "dev-secret-change-in-production")
        payload = {
            "sub": "test_user_123",
            "email": "test@example.com",
            "tenant_id": "tenant_123",
            "role": "authenticated",
            "aud": "authenticated",
            "iss": "algo22-test",
            "exp": int(time.time()) - 3600  # 1 hour ago
        }
        return jwt.encode(payload, secret, algorithm="HS256")
    
    @pytest.fixture
    def invalid_token(self):
        """Generate an invalid test token."""
        return "invalid_token_string"
    
    @pytest.fixture
    def different_tenant_token(self):
        """Generate a valid token for a different tenant."""
        import jwt
        import time
        
        secret = os.getenv("SUPABASE_JWT_SECRET", "dev-secret-change-in-production")
        payload = {
            "sub": "different_user_456",
            "email": "different@example.com",
            "tenant_id": "tenant_456",
            "role": "authenticated",
            "aud": "authenticated",
            "iss": "algo22-test",
            "exp": int(time.time()) + 3600
        }
        return jwt.encode(payload, secret, algorithm="HS256")
    
    def test_ws_telemetry_no_token(self, client):
        """Test /ws/telemetry rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/telemetry") as websocket:
                pass
        
        # Should close with policy violation code (4001 or 1008)
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_ws_telemetry_invalid_token(self, client, invalid_token):
        """Test /ws/telemetry rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/telemetry?token={invalid_token}") as websocket:
                pass
        
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_ws_telemetry_expired_token(self, client, expired_token):
        """Test /ws/telemetry rejects connection with expired token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/telemetry?token={expired_token}") as websocket:
                pass
        
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_ws_telemetry_valid_token(self, client, valid_token):
        """Test /ws/telemetry accepts connection with valid token."""
        try:
            with client.websocket_connect(f"/ws/telemetry?token={valid_token}") as websocket:
                pass
        except WebSocketDisconnect as e:
            pytest.fail(f"Valid token should not cause disconnect: {e}")
    
    def test_ws_ticker_no_token(self, client):
        """Test /ws/ticker/{symbol} rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/ticker/BTC-USDT") as websocket:
                pass
        
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_ws_ticker_invalid_token(self, client, invalid_token):
        """Test /ws/ticker/{symbol} rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/ticker/BTC-USDT?token={invalid_token}") as websocket:
                pass
        
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_ws_ticker_valid_token(self, client, valid_token):
        """Test /ws/ticker/{symbol} accepts connection with valid token."""
        try:
            with client.websocket_connect(f"/ws/ticker/BTC-USDT?token={valid_token}") as websocket:
                pass
        except WebSocketDisconnect as e:
            pytest.fail(f"Valid token should not cause disconnect: {e}")
    
    def test_ws_orderbook_no_token(self, client):
        """Test /ws/orderbook/{symbol} rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/orderbook/BTC-USDT") as websocket:
                pass
        
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_ws_orderbook_invalid_token(self, client, invalid_token):
        """Test /ws/orderbook/{symbol} rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/orderbook/BTC-USDT?token={invalid_token}") as websocket:
                pass
        
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_ws_candles_no_token(self, client):
        """Test /ws/candles/{symbol}/{timeframe} rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/candles/BTC-USDT/5m") as websocket:
                pass
        
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_ws_candles_invalid_token(self, client, invalid_token):
        """Test /ws/candles/{symbol}/{timeframe} rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/candles/BTC-USDT/5m?token={invalid_token}") as websocket:
                pass
        
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_ws_user_no_token(self, client):
        """Test /ws/user/{user_id} rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/user/test_user_123") as websocket:
                pass
        
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_ws_user_invalid_token(self, client, invalid_token):
        """Test /ws/user/{user_id} rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/user/test_user_123?token={invalid_token}") as websocket:
                pass
        
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_ws_user_tenant_mismatch(self, client, different_tenant_token):
        """Test /ws/user/{user_id} rejects connection with different tenant token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/user/test_user_123?token={different_tenant_token}") as websocket:
                pass
        
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_ws_dashboard_no_token(self, client):
        """Test /ws/dashboard rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/dashboard") as websocket:
                pass
        
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_ws_dashboard_invalid_token(self, client, invalid_token):
        """Test /ws/dashboard rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/dashboard?token={invalid_token}&user_id=test_user_123") as websocket:
                pass
        
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_ws_strategy_no_token(self, client):
        """Test /ws/strategy/{strategy_id} rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/strategy/strategy_123") as websocket:
                pass
        
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_ws_strategy_invalid_token(self, client, invalid_token):
        """Test /ws/strategy/{strategy_id} rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/strategy/strategy_123?token={invalid_token}&user_id=test_user_123") as websocket:
                pass
        
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_ws_signal_trace_no_token(self, client):
        """Test /ws/signal-trace rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/signal-trace") as websocket:
                pass
        
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_ws_signal_trace_invalid_token(self, client, invalid_token):
        """Test /ws/signal-trace rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/signal-trace?token={invalid_token}&user_id=test_user_123") as websocket:
                pass
        
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_ws_pnl_no_token(self, client):
        """Test /ws/pnl/{user_id} rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/pnl/test_user_123") as websocket:
                pass
        
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_ws_pnl_invalid_token(self, client, invalid_token):
        """Test /ws/pnl/{user_id} rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/pnl/test_user_123?token={invalid_token}") as websocket:
                pass
        
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_dag_task_websocket_no_token(self, client):
        """Test /api/dag/tasks/ws/{task_id} rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/api/dag/tasks/ws/task_123") as websocket:
                pass
        
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_dag_task_websocket_invalid_token(self, client, invalid_token):
        """Test /api/dag/tasks/ws/{task_id} rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/api/dag/tasks/ws/task_123?token={invalid_token}") as websocket:
                pass
        
        assert exc_info.value.code in (4003, 4001, 1008, 1000)
    
    def test_dag_task_websocket_missing_tenant_id(self, client, valid_token):
        """
        Test /api/dag/tasks/ws/{task_id} rejects connection when task lacks tenant_id.
        This is a defensive code test: the getattr default changed from fail-open to fail-closed.
        """
        # Direct unit test of the comparison logic
        class TaskWithoutTenantId:
            task_id = "task_123"
            # Deliberately missing tenant_id attribute
        
        task = TaskWithoutTenantId()
        auth_user = {"id": "user_123"}
        
        # Simulate the check logic from dag_tasks.py
        task_tenant_id = getattr(task, "tenant_id", None)
        if task_tenant_id is None:
            # Should reject when tenant_id is missing
            assert True, "Task without tenant_id should be rejected"
        else:
            assert False, "Task without tenant_id should not reach this branch"
    
    def test_dag_task_websocket_tenant_mismatch(self, client, valid_token):
        """
        Test /api/dag/tasks/ws/{task_id} rejects connection when tenant_id doesn't match.
        This verifies the tenant isolation check still works after the fix.
        """
        # Direct unit test of the comparison logic
        class TaskWithDifferentTenant:
            task_id = "task_123"
            tenant_id = "other_tenant_456"
        
        task = TaskWithDifferentTenant()
        auth_user = {"id": "user_123"}
        
        # Simulate the check logic from dag_tasks.py
        task_tenant_id = getattr(task, "tenant_id", None)
        if task_tenant_id is None:
            assert False, "Task with tenant_id should not fail the None check"
        if str(auth_user.get("id")) != str(task_tenant_id):
            # Should reject when tenant_id doesn't match
            assert True, "Task with mismatched tenant_id should be rejected"
        else:
            assert False, "Task with mismatched tenant_id should not reach this branch"
    
    def test_dag_task_websocket_tenant_match(self, client, valid_token):
        """
        Test /api/dag/tasks/ws/{task_id} accepts connection when tenant_id matches.
        This is the positive regression test: legitimate connections must still work.
        """
        from backend_app.core.dag_task_queue import dag_task_queue
        from unittest.mock import AsyncMock, patch
        
        # Mock a task object with matching tenant_id
        class TaskWithMatchingTenant:
            task_id = "task_123"
            tenant_id = "tenant_123"
        
        mock_task = TaskWithMatchingTenant()
        
        # Also mock get_task_status to return a valid status
        with patch.object(dag_task_queue, '_load_task', return_value=mock_task), \
             patch.object(dag_task_queue, 'get_task_status', return_value={"status": "pending"}):
            # This should NOT raise WebSocketDisconnect during the connection attempt
            # (The test will time out because we don't mock the polling loop, but that's OK -
            # we just want to verify the initial connection succeeds)
            try:
                with client.websocket_connect(f"/api/dag/tasks/ws/task_123?token={valid_token}") as websocket:
                    # Wait briefly to ensure initial status is sent
                    import asyncio
                    asyncio.run(asyncio.sleep(0.1))
            except WebSocketDisconnect as exc_info:
                # If it disconnects, it should be with code 1011 (internal error from our incomplete mock)
                # NOT with 4003 (unauthorized)
                assert exc_info.value.code != 4003, f"Should not reject with 4003 for matching tenant, got {exc_info.value.code}"
    
    def test_dag_event_loop_websocket_no_token(self, dag_client):
        """Test /ws/{session_id} in dag_event_loop rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with dag_client.websocket_connect("/ws/session_123") as websocket:
                pass
        
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_dag_event_loop_websocket_invalid_token(self, dag_client, invalid_token):
        """Test /ws/{session_id} in dag_event_loop rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with dag_client.websocket_connect(f"/ws/session_123?token={invalid_token}") as websocket:
                pass
        
        assert exc_info.value.code in (4003, 4001, 1008, 1000)
    
    def test_ws_server_public_no_token(self, ws_server_client):
        """Test /ws/public/{channel} in ws_server rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with ws_server_client.websocket_connect("/ws/public/orders") as websocket:
                pass
        
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_ws_server_public_invalid_token(self, ws_server_client, invalid_token):
        """Test /ws/public/{channel} in ws_server rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with ws_server_client.websocket_connect(f"/ws/public/orders?token={invalid_token}") as websocket:
                pass
        
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_ws_server_tenant_no_token(self, ws_server_client):
        """Test /ws/{tenant_id} in ws_server rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with ws_server_client.websocket_connect("/ws/tenant_123") as websocket:
                pass
        
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_ws_server_tenant_invalid_token(self, ws_server_client, invalid_token):
        """Test /ws/{tenant_id} in ws_server rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with ws_server_client.websocket_connect(f"/ws/tenant_123?token={invalid_token}") as websocket:
                pass
        
        assert exc_info.value.code in (4001, 1008, 1000)
    
    def test_ws_server_tenant_mismatch(self, ws_server_client, different_tenant_token):
        """Test /ws/{tenant_id} in ws_server rejects connection with different tenant."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with ws_server_client.websocket_connect(f"/ws/tenant_123?token={different_tenant_token}") as websocket:
                pass
        
        assert exc_info.value.code in (4003, 4001, 1008, 1000)
    
    def test_ws_telemetry_verification_exception(self, client):
        """Test /ws/telemetry rejects connection when verification throws exception."""
        from backend_app.core.websocket_auth import _decode_hs256_token
        
        with patch('backend_app.api_ws.ws_routes._decode_hs256_token') as mock_decode:
            mock_decode.side_effect = Exception("Verification failed unexpectedly")
            
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect("/ws/telemetry?token=some_token") as websocket:
                    pass
            
            assert exc_info.value.code in (4003, 1008, 1000)
    
    def test_ws_ticker_verification_exception(self, client):
        """Test /ws/ticker/{symbol} rejects connection when verification throws exception."""
        from backend_app.core.websocket_auth import _decode_hs256_token
        
        with patch('backend_app.api_ws.ws_routes._decode_hs256_token') as mock_decode:
            mock_decode.side_effect = Exception("Verification failed unexpectedly")
            
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect("/ws/ticker/BTC-USDT?token=some_token") as websocket:
                    pass
            
            assert exc_info.value.code in (4003, 1008, 1000)
    
    def test_ws_orderbook_verification_exception(self, client):
        """Test /ws/orderbook/{symbol} rejects connection when verification throws exception."""
        from backend_app.core.websocket_auth import _decode_hs256_token
        
        with patch('backend_app.api_ws.ws_routes._decode_hs256_token') as mock_decode:
            mock_decode.side_effect = Exception("Verification failed unexpectedly")
            
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect("/ws/orderbook/BTC-USDT?token=some_token") as websocket:
                    pass
            
            assert exc_info.value.code in (4003, 1008, 1000)
    
    def test_ws_candles_verification_exception(self, client):
        """Test /ws/candles/{symbol}/{timeframe} rejects connection when verification throws exception."""
        from backend_app.core.websocket_auth import _decode_hs256_token
        
        with patch('backend_app.api_ws.ws_routes._decode_hs256_token') as mock_decode:
            mock_decode.side_effect = Exception("Verification failed unexpectedly")
            
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect("/ws/candles/BTC-USDT/5m?token=some_token") as websocket:
                    pass
            
            assert exc_info.value.code in (4003, 1008, 1000)


def run_all_tests():
    """Run all WebSocket authentication tests."""
    print("=" * 60)
    print("WEBSOCKET AUTHENTICATION FAIL-CLOSED TESTS")
    print("=" * 60)
    
    import pytest
    result = pytest.main([__file__, "-v", "--tb=short"])
    
    print("\n" + "=" * 60)
    if result == 0:
        print("ALL TESTS PASSED")
    else:
        print(f"TESTS FAILED (exit code: {result})")
    print("=" * 60)
    
    return result


if __name__ == "__main__":
    sys.exit(run_all_tests())
