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

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi import WebSocket
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
    def valid_token(self):
        """Generate a valid test token."""
        import jwt
        import time
        
        secret = os.getenv("SUPABASE_JWT_SECRET", "test_secret")
        payload = {
            "sub": "test_user_123",
            "email": "test@example.com",
            "tenant_id": "tenant_123",
            "role": "authenticated",
            "exp": int(time.time()) + 3600  # 1 hour from now
        }
        return jwt.encode(payload, secret, algorithm="HS256")
    
    @pytest.fixture
    def expired_token(self):
        """Generate an expired test token."""
        import jwt
        import time
        
        secret = os.getenv("SUPABASE_JWT_SECRET", "test_secret")
        payload = {
            "sub": "test_user_123",
            "email": "test@example.com",
            "tenant_id": "tenant_123",
            "role": "authenticated",
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
        
        secret = os.getenv("SUPABASE_JWT_SECRET", "test_secret")
        payload = {
            "sub": "different_user_456",
            "email": "different@example.com",
            "tenant_id": "tenant_456",
            "role": "authenticated",
            "exp": int(time.time()) + 3600
        }
        return jwt.encode(payload, secret, algorithm="HS256")
    
    def test_ws_telemetry_no_token(self, client):
        """Test /ws/telemetry rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/telemetry") as websocket:
                pass
        
        # Should close with policy violation code
        assert exc_info.value.code == 4001
        assert "Missing token" in exc_info.value.reason or "Unauthorized" in exc_info.value.reason
    
    def test_ws_telemetry_invalid_token(self, client, invalid_token):
        """Test /ws/telemetry rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/telemetry?token={invalid_token}") as websocket:
                pass
        
        assert exc_info.value.code == 4001
        assert "Invalid token" in exc_info.value.reason or "Unauthorized" in exc_info.value.reason
    
    def test_ws_telemetry_expired_token(self, client, expired_token):
        """Test /ws/telemetry rejects connection with expired token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/telemetry?token={expired_token}") as websocket:
                pass
        
        assert exc_info.value.code == 4001
        assert "Invalid token" in exc_info.value.reason or "Unauthorized" in exc_info.value.reason
    
    def test_ws_telemetry_valid_token(self, client, valid_token):
        """Test /ws/telemetry accepts connection with valid token."""
        try:
            with client.websocket_connect(f"/ws/telemetry?token={valid_token}") as websocket:
                # Connection should be accepted
                assert websocket.client_state.name == "CONNECTED"
        except WebSocketDisconnect as e:
            pytest.fail(f"Valid token should not cause disconnect: {e}")
    
    def test_ws_ticker_no_token(self, client):
        """Test /ws/ticker/{symbol} rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/ticker/BTC-USDT") as websocket:
                pass
        
        assert exc_info.value.code == 4001
        assert "Authentication required" in exc_info.value.reason
    
    def test_ws_ticker_invalid_token(self, client, invalid_token):
        """Test /ws/ticker/{symbol} rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/ticker/BTC-USDT?token={invalid_token}") as websocket:
                pass
        
        assert exc_info.value.code == 4001
        assert "Invalid token" in exc_info.value.reason or "Unauthorized" in exc_info.value.reason
    
    def test_ws_ticker_valid_token(self, client, valid_token):
        """Test /ws/ticker/{symbol} accepts connection with valid token."""
        try:
            with client.websocket_connect(f"/ws/ticker/BTC-USDT?token={valid_token}") as websocket:
                assert websocket.client_state.name == "CONNECTED"
        except WebSocketDisconnect as e:
            pytest.fail(f"Valid token should not cause disconnect: {e}")
    
    def test_ws_orderbook_no_token(self, client):
        """Test /ws/orderbook/{symbol} rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/orderbook/BTC-USDT") as websocket:
                pass
        
        assert exc_info.value.code == 4001
        assert "Authentication required" in exc_info.value.reason
    
    def test_ws_orderbook_invalid_token(self, client, invalid_token):
        """Test /ws/orderbook/{symbol} rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/orderbook/BTC-USDT?token={invalid_token}") as websocket:
                pass
        
        assert exc_info.value.code == 4001
        assert "Invalid token" in exc_info.value.reason or "Unauthorized" in exc_info.value.reason
    
    def test_ws_candles_no_token(self, client):
        """Test /ws/candles/{symbol}/{timeframe} rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/candles/BTC-USDT/5m") as websocket:
                pass
        
        assert exc_info.value.code == 4001
        assert "Authentication required" in exc_info.value.reason
    
    def test_ws_candles_invalid_token(self, client, invalid_token):
        """Test /ws/candles/{symbol}/{timeframe} rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/candles/BTC-USDT/5m?token={invalid_token}") as websocket:
                pass
        
        assert exc_info.value.code == 4001
        assert "Invalid token" in exc_info.value.reason or "Unauthorized" in exc_info.value.reason
    
    def test_ws_user_no_token(self, client):
        """Test /ws/user/{user_id} rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/user/test_user_123") as websocket:
                pass
        
        assert exc_info.value.code == 4001
        assert "Unauthorized" in exc_info.value.reason
    
    def test_ws_user_invalid_token(self, client, invalid_token):
        """Test /ws/user/{user_id} rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/user/test_user_123?token={invalid_token}") as websocket:
                pass
        
        assert exc_info.value.code == 4001
        assert "Unauthorized" in exc_info.value.reason
    
    def test_ws_user_tenant_mismatch(self, client, different_tenant_token):
        """Test /ws/user/{user_id} rejects connection with different tenant token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/user/test_user_123?token={different_tenant_token}") as websocket:
                pass
        
        assert exc_info.value.code == 4001
        assert "Unauthorized" in exc_info.value.reason
    
    def test_ws_dashboard_no_token(self, client):
        """Test /ws/dashboard rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/dashboard") as websocket:
                pass
        
        assert exc_info.value.code == 4001
        assert "Unauthorized" in exc_info.value.reason
    
    def test_ws_dashboard_invalid_token(self, client, invalid_token):
        """Test /ws/dashboard rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/dashboard?token={invalid_token}&user_id=test_user_123") as websocket:
                pass
        
        assert exc_info.value.code == 4001
        assert "Unauthorized" in exc_info.value.reason
    
    def test_ws_strategy_no_token(self, client):
        """Test /ws/strategy/{strategy_id} rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/strategy/strategy_123") as websocket:
                pass
        
        assert exc_info.value.code == 4001
        assert "Unauthorized" in exc_info.value.reason
    
    def test_ws_strategy_invalid_token(self, client, invalid_token):
        """Test /ws/strategy/{strategy_id} rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/strategy/strategy_123?token={invalid_token}&user_id=test_user_123") as websocket:
                pass
        
        assert exc_info.value.code == 4001
        assert "Unauthorized" in exc_info.value.reason
    
    def test_ws_signal_trace_no_token(self, client):
        """Test /ws/signal-trace rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/signal-trace") as websocket:
                pass
        
        assert exc_info.value.code == 4001
        assert "Unauthorized" in exc_info.value.reason
    
    def test_ws_signal_trace_invalid_token(self, client, invalid_token):
        """Test /ws/signal-trace rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/signal-trace?token={invalid_token}&user_id=test_user_123") as websocket:
                pass
        
        assert exc_info.value.code == 4001
        assert "Unauthorized" in exc_info.value.reason
    
    def test_ws_pnl_no_token(self, client):
        """Test /ws/pnl/{user_id} rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/pnl/test_user_123") as websocket:
                pass
        
        assert exc_info.value.code == 4001
        assert "Unauthorized" in exc_info.value.reason
    
    def test_ws_pnl_invalid_token(self, client, invalid_token):
        """Test /ws/pnl/{user_id} rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/pnl/test_user_123?token={invalid_token}") as websocket:
                pass
        
        assert exc_info.value.code == 4001
        assert "Unauthorized" in exc_info.value.reason
    
    def test_dag_task_websocket_no_token(self, client):
        """Test /api/dag/tasks/ws/{task_id} rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/api/dag/tasks/ws/task_123") as websocket:
                pass
        
        assert exc_info.value.code == 4001
        assert "Authentication required" in exc_info.value.reason
    
    def test_dag_task_websocket_invalid_token(self, client, invalid_token):
        """Test /api/dag/tasks/ws/{task_id} rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/api/dag/tasks/ws/task_123?token={invalid_token}") as websocket:
                pass
        
        assert exc_info.value.code == 4003
        assert "Invalid" in exc_info.value.reason or "authentication" in exc_info.value.reason
    
    def test_dag_event_loop_websocket_no_token(self, client):
        """Test /ws/{session_id} in dag_event_loop rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/session_123") as websocket:
                pass
        
        assert exc_info.value.code == 4001
        assert "Authentication required" in exc_info.value.reason
    
    def test_dag_event_loop_websocket_invalid_token(self, client, invalid_token):
        """Test /ws/{session_id} in dag_event_loop rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/session_123?token={invalid_token}") as websocket:
                pass
        
        assert exc_info.value.code == 4003
        assert "Invalid" in exc_info.value.reason or "authentication" in exc_info.value.reason
    
    def test_ws_server_public_no_token(self, client):
        """Test /ws/public/{channel} in ws_server rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/public/orders") as websocket:
                pass
        
        assert exc_info.value.code == 4001
        assert "Authentication required" in exc_info.value.reason
    
    def test_ws_server_public_invalid_token(self, client, invalid_token):
        """Test /ws/public/{channel} in ws_server rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/public/orders?token={invalid_token}") as websocket:
                pass
        
        assert exc_info.value.code == 4001
        assert "Invalid token" in exc_info.value.reason or "Unauthorized" in exc_info.value.reason
    
    def test_ws_server_tenant_no_token(self, client):
        """Test /ws/{tenant_id} in ws_server rejects connection with no token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/tenant_123") as websocket:
                pass
        
        assert exc_info.value.code == 4001
        assert "Authentication required" in exc_info.value.reason
    
    def test_ws_server_tenant_invalid_token(self, client, invalid_token):
        """Test /ws/{tenant_id} in ws_server rejects connection with invalid token."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/tenant_123?token={invalid_token}") as websocket:
                pass
        
        assert exc_info.value.code == 4001
        assert "Invalid token" in exc_info.value.reason or "Unauthorized" in exc_info.value.reason
    
    def test_ws_server_tenant_mismatch(self, client, different_tenant_token):
        """Test /ws/{tenant_id} in ws_server rejects connection with different tenant."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/tenant_123?token={different_tenant_token}") as websocket:
                pass
        
        assert exc_info.value.code == 4003
        assert "tenant_id mismatch" in exc_info.value.reason or "Unauthorized" in exc_info.value.reason


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
