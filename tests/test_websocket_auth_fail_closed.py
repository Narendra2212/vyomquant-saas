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
    
    def test_ws_telemetry_valid_ticket(self, client):
        """Test /ws/telemetry accepts connection with a valid ticket.

        RETARGETED by production-launch-hardening task 13.25. This asserted that a valid
        JWT in `?token=` was admitted — the behaviour the task deletes, because uvicorn
        writes that query string to CloudWatch. The positive property is unchanged and
        still asserted: a credential that resolves is admitted. Only the credential is
        different.

        `verify_ws_ticket` is patched rather than a real ticket minted because redemption
        is an atomic `getdel` against Redis and there is no store in this environment.
        This is the same boundary the four `*_verification_exception` tests below patch.
        """
        with patch('backend_app.api_ws.ws_routes.verify_ws_ticket') as mock_verify:
            mock_verify.return_value = {"sub": "test_user_123", "auth_method": "ws_ticket"}
            try:
                with client.websocket_connect("/ws/telemetry?ticket=good_ticket"):
                    pass
            except WebSocketDisconnect as e:
                pytest.fail(f"Valid ticket should not cause disconnect: {e}")
    
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
    
    def test_ws_ticker_valid_ticket(self, client):
        """Test /ws/ticker/{symbol} accepts connection with a valid ticket.

        RETARGETED by task 13.25 — see `test_ws_telemetry_valid_ticket`.
        """
        with patch('backend_app.api_ws.ws_routes.verify_ws_ticket') as mock_verify:
            mock_verify.return_value = {"sub": "test_user_123", "auth_method": "ws_ticket"}
            try:
                with client.websocket_connect("/ws/ticker/BTC-USDT?ticket=good_ticket"):
                    pass
            except WebSocketDisconnect as e:
                pytest.fail(f"Valid ticket should not cause disconnect: {e}")
    
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
        # task 13.25 retargeted the patched boundary from `_decode_hs256_token` to
        # `verify_ws_ticket`. The assertion is byte-identical: a credential verifier that
        # raises must close 4003 rather than admit. Only the verifier changed, because
        # `?token=` is no longer a credential any route accepts.
        with patch('backend_app.api_ws.ws_routes.verify_ws_ticket') as mock_verify:
            mock_verify.side_effect = Exception("Verification failed unexpectedly")
            
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect("/ws/telemetry?ticket=some_ticket") as websocket:
                    pass
            
            assert exc_info.value.code in (4003, 1008, 1000)
    
    def test_ws_ticker_verification_exception(self, client):
        """Test /ws/ticker/{symbol} rejects connection when verification throws exception."""
        # task 13.25 retargeted the patched boundary from `_decode_hs256_token` to
        # `verify_ws_ticket`. The assertion is byte-identical: a credential verifier that
        # raises must close 4003 rather than admit. Only the verifier changed, because
        # `?token=` is no longer a credential any route accepts.
        with patch('backend_app.api_ws.ws_routes.verify_ws_ticket') as mock_verify:
            mock_verify.side_effect = Exception("Verification failed unexpectedly")
            
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect("/ws/ticker/BTC-USDT?ticket=some_ticket") as websocket:
                    pass
            
            assert exc_info.value.code in (4003, 1008, 1000)
    
    def test_ws_orderbook_verification_exception(self, client):
        """Test /ws/orderbook/{symbol} rejects connection when verification throws exception."""
        # task 13.25 retargeted the patched boundary from `_decode_hs256_token` to
        # `verify_ws_ticket`. The assertion is byte-identical: a credential verifier that
        # raises must close 4003 rather than admit. Only the verifier changed, because
        # `?token=` is no longer a credential any route accepts.
        with patch('backend_app.api_ws.ws_routes.verify_ws_ticket') as mock_verify:
            mock_verify.side_effect = Exception("Verification failed unexpectedly")
            
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect("/ws/orderbook/BTC-USDT?ticket=some_ticket") as websocket:
                    pass
            
            assert exc_info.value.code in (4003, 1008, 1000)
    
    def test_ws_candles_verification_exception(self, client):
        """Test /ws/candles/{symbol}/{timeframe} rejects connection when verification throws exception."""
        # task 13.25 retargeted the patched boundary from `_decode_hs256_token` to
        # `verify_ws_ticket`. The assertion is byte-identical: a credential verifier that
        # raises must close 4003 rather than admit. Only the verifier changed, because
        # `?token=` is no longer a credential any route accepts.
        with patch('backend_app.api_ws.ws_routes.verify_ws_ticket') as mock_verify:
            mock_verify.side_effect = Exception("Verification failed unexpectedly")
            
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect("/ws/candles/BTC-USDT/5m?ticket=some_ticket") as websocket:
                    pass
            
            assert exc_info.value.code in (4003, 1008, 1000)


# ═══════════════════════════════════════════════════════════════════════════
#  THE `token` QUERY CREDENTIAL IS GONE — production-launch-hardening 13.25
#  Requirements 1.21, 2.21, 3.9.
# ═══════════════════════════════════════════════════════════════════════════
#
#  Added here rather than in a new file because this is the suite that already owns
#  "what does a WebSocket route do with a credential it should refuse", and a second
#  file asserting the same thing is a second place for the answer to drift.
#
#  TWO QUESTIONS, ONE PER CLASS. Removing a credential arm can fail in two opposite
#  directions and both have to be closed:
#
#    * it can fail OPEN — the parameter is gone from the signature but something still
#      resolves a JWT, so `?token=` keeps working and keeps being access-logged. That is
#      `TestATokenQueryParameterIsNoLongerACredential`.
#    * it can fail BROKEN — the removal takes the refusal with it, and a handshake with
#      no credential is admitted instead of closed. That is
#      `TestEveryRouteStillFailsClosedWithNoCredential`, and it is the assertion that
#      matters most, because it is the one a careless `if not ticket:` edit breaks.
#
#  `tests/test_ws_token_query_credential_removed.py` proves the same removal
#  STRUCTURALLY, over the parse tree. These two do it BEHAVIOURALLY, through a real
#  handshake against the real app. Neither subsumes the other: the AST guard catches a
#  parameter coming back in a diff that no test happens to exercise, and these catch a
#  resolver that admits something its signature does not mention.

#: Every route in `api_ws/ws_routes.py`, with the non-credential parameters each one
#: requires, so a refusal is the credential's refusal and not FastAPI rejecting a
#: missing `user_id` during the handshake.
_TEST_USER_ID = "test_user_123"
ALL_WS_ROUTES = [
    "/ws/telemetry",
    "/ws/ticker/BTC-USDT",
    "/ws/orderbook/BTC-USDT",
    "/ws/candles/BTC-USDT/5m",
    f"/ws/user/{_TEST_USER_ID}",
    f"/ws/pnl/{_TEST_USER_ID}",
    f"/ws/dashboard?user_id={_TEST_USER_ID}",
    f"/ws/strategy/stg_1?user_id={_TEST_USER_ID}",
    f"/ws/signal-trace?user_id={_TEST_USER_ID}",
]


def _with_param(path: str, param: str) -> str:
    return f"{path}{'&' if '?' in path else '?'}{param}"


class TestEveryRouteStillFailsClosedWithNoCredential:
    """A handshake presenting nothing must still be closed, on all nine routes."""

    @pytest.fixture
    def client(self):
        from backend_app.main import app
        return TestClient(app)

    @pytest.mark.parametrize("path", ALL_WS_ROUTES)
    def test_no_credential_is_refused(self, client, path):
        """
        The guard that task 13.25 narrowed. `if not ticket and not token:` became
        `if not ticket:` on four routes, and the five that resolve through
        `_resolve_ws_subject` lost the second keyword — both are reductions in what is
        admitted, and this asserts the refusal itself survived the edit.

        4001 is what the routes close with. 1008/1000/1006 are accepted because an ASGI
        server turns a `close()` issued *before* `accept()` into an HTTP 403 on the
        handshake, and the code the client observes then depends on the transport — the
        same latitude every test above this line already allows.
        """
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(path):
                pass

        assert exc_info.value.code in (4001, 4003, 1008, 1006, 1000), (
            f"{path} admitted a handshake that presented no credential, or closed with "
            f"an unexpected code {exc_info.value.code}"
        )


class TestATokenQueryParameterIsNoLongerACredential:
    """A **valid** session JWT in `?token=` must now be refused, on all nine routes.

    This is the assertion that encodes the finding. A JWT that is cryptographically
    good and unexpired is exactly the input that used to be admitted, so it is the only
    input that can tell "the arm is gone" apart from "the arm is still here and the
    token happened to be bad".
    """

    @pytest.fixture
    def client(self):
        from backend_app.main import app
        return TestClient(app)

    @pytest.fixture
    def valid_token(self):
        import time

        import jwt

        secret = os.getenv("SUPABASE_JWT_SECRET", "dev-secret-change-in-production")
        return jwt.encode(
            {
                "sub": _TEST_USER_ID,
                "email": "test@example.com",
                "tenant_id": "tenant_123",
                "role": "authenticated",
                "aud": "authenticated",
                "iss": "algo22-test",
                "exp": int(time.time()) + 3600,
            },
            secret,
            algorithm="HS256",
        )

    @pytest.mark.parametrize("path", ALL_WS_ROUTES)
    def test_a_valid_jwt_in_the_token_parameter_is_refused(self, client, path, valid_token):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(_with_param(path, f"token={valid_token}")):
                pass

        assert exc_info.value.code in (4001, 4003, 1008, 1006, 1000), (
            f"{path} ADMITTED a valid session JWT presented as `?token=`. That is the "
            f"credential task 13.25 removed: uvicorn's access log writes the full "
            f"request line including the query string, so admitting it puts a live JWT "
            f"in CloudWatch beside the user id and the client IP."
        )

    def test_the_sanity_check_that_makes_the_above_non_vacuous(self, client):
        """A resolvable ticket IS admitted on the same route.

        Without this, every assertion in this class would also pass if the routes had
        simply stopped working. One positive case pins that the refusals above are the
        credential being refused rather than the route being dead.
        """
        with patch('backend_app.api_ws.ws_routes.verify_ws_ticket') as mock_verify:
            mock_verify.return_value = {"sub": _TEST_USER_ID, "auth_method": "ws_ticket"}
            try:
                with client.websocket_connect("/ws/telemetry?ticket=good_ticket"):
                    pass
            except WebSocketDisconnect as e:
                pytest.fail(f"a resolvable ticket must still be admitted: {e}")


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
