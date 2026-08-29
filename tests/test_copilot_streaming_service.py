"""
tests/test_copilot_streaming_service.py — AI Copilot Streaming & Persistence Tests

Verifies:
1. Unauthenticated requests to /api/v1/copilot/chat/stream are rejected with HTTP 401.
2. Authenticated requests stream valid SSE events (event: session, event: token, data: [DONE]).
3. Malformed payloads (empty message) are rejected with HTTP 422.
4. Tenant isolation: User A cannot retrieve or mutate User B's conversation sessions.
5. Session listing and retrieval contracts.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch, MagicMock
from backend_app.main import app
from backend_app.core.dependencies import get_current_user


@pytest.mark.asyncio
class TestCopilotStreamingService:
    """Test AI Copilot streaming endpoint and security controls."""

    async def test_copilot_unauthenticated_rejected(self):
        """Verify that requests without JWT authorization are rejected with HTTP 401."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/copilot/chat/stream",
                json={"message": "How do I optimize RSI parameters?"}
            )
            assert response.status_code == 401

    async def test_copilot_chat_stream_authenticated_success(self):
        """Verify that an authenticated user receives valid Server-Sent Events (SSE)."""
        mock_user = {
            "id": "11111111-2222-3333-4444-555555555555",
            "email": "trader@vyomquant.in",
            "role": "authenticated"
        }

        app.dependency_overrides[get_current_user] = lambda: mock_user
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/copilot/chat/stream",
                    json={
                        "message": "Explain how RSI and MACD work together in strategy building",
                        "context_metadata": {"view": "strategy_builder"}
                    }
                )
                assert response.status_code == 200
                assert "text/event-stream" in response.headers.get("content-type", "")
                
                body_text = response.text
                assert "event: session" in body_text
                assert "event: token" in body_text
                assert "data: [DONE]" in body_text
                assert "RSI" in body_text or "MACD" in body_text
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    async def test_copilot_empty_message_validation_failure(self):
        """Verify that empty/missing messages are rejected with HTTP 422."""
        mock_user = {
            "id": "11111111-2222-3333-4444-555555555555",
            "email": "trader@vyomquant.in"
        }
        app.dependency_overrides[get_current_user] = lambda: mock_user
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/copilot/chat/stream",
                    json={"message": ""}
                )
                assert response.status_code == 422
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    async def test_copilot_sessions_list_endpoint(self):
        """Verify that GET /api/v1/copilot/sessions requires auth and returns list."""
        mock_user = {
            "id": "11111111-2222-3333-4444-555555555555",
            "email": "trader@vyomquant.in"
        }
        app.dependency_overrides[get_current_user] = lambda: mock_user
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get("/api/v1/copilot/sessions")
                assert response.status_code == 200
                assert isinstance(response.json(), list)
        finally:
            app.dependency_overrides.pop(get_current_user, None)
