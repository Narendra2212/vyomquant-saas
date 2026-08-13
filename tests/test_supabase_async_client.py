"""
tests/test_supabase_async_client.py

Unit tests for pooled, tenant-safe async Supabase/PostgREST client in core/dependencies.py.
Verifies:
1. Header isolation (per-request immutable header dicts).
2. Transport sharing (connection pooling across requests).
3. Concurrent tenant isolation (50 rounds of interleaved query execution).
4. Demonstration of why shared-client .auth() mutation is unsafe (negative test).
"""

import asyncio
import os
import pytest
import httpx

from backend_app.core.dependencies import (
    create_request_supabase_async,
    _PooledAsyncPostgrestClient,
    get_shared_async_transport,
)


@pytest.fixture(autouse=True)
def setup_env():
    os.environ["SUPABASE_URL"] = "https://example.supabase.co"
    os.environ["SUPABASE_ANON_KEY"] = "fake_anon_key_123"
    yield


@pytest.mark.asyncio
async def test_header_isolation_object_identity():
    """Verify that each created client has a distinct header dict object with correct auth headers."""
    client_a = await create_request_supabase_async("token_a_123")
    client_b = await create_request_supabase_async("token_b_456")

    assert client_a is not None
    assert client_b is not None
    assert client_a is not client_b

    # Header dicts MUST be different Python objects (no shared mutable state)
    assert client_a.session.headers is not client_b.session.headers
    assert client_a.session.headers["Authorization"] == "Bearer token_a_123"
    assert client_b.session.headers["Authorization"] == "Bearer token_b_456"
    assert client_a.session.headers["apiKey"] == "fake_anon_key_123"
    assert client_b.session.headers["apiKey"] == "fake_anon_key_123"


@pytest.mark.asyncio
async def test_transport_sharing_across_clients():
    """Verify that clients share the same underlying httpx.AsyncHTTPTransport for connection pooling."""
    client_a = await create_request_supabase_async("token_a_123")
    client_b = await create_request_supabase_async("token_b_456")

    # Underlying httpx transport MUST be identical object
    assert client_a.session._transport is client_b.session._transport
    assert client_a._shared_transport is client_b._shared_transport
    assert client_a._shared_transport is await get_shared_async_transport()


@pytest.mark.asyncio
async def test_tenant_isolation_concurrent_queries():
    """
    Mandatory tenant isolation test: 50 rounds of tightly interleaved concurrent queries for User A and User B.
    Verifies zero cross-tenant header leaks or response confusion under concurrency.
    """
    token_a = "jwt_user_a_secret_token"
    token_b = "jwt_user_b_secret_token"

    def mock_handler(request: httpx.Request) -> httpx.Response:
        auth_header = request.headers.get("Authorization", "")
        if auth_header == f"Bearer {token_a}":
            return httpx.Response(
                200,
                json=[{"id": "user_a_123", "owner": "user_a", "secret": "data_a"}],
                headers={"Content-Type": "application/json"},
            )
        elif auth_header == f"Bearer {token_b}":
            return httpx.Response(
                200,
                json=[{"id": "user_b_456", "owner": "user_b", "secret": "data_b"}],
                headers={"Content-Type": "application/json"},
            )
        return httpx.Response(
            401,
            json={"error": "Unauthorized"},
            headers={"Content-Type": "application/json"},
        )

    mock_transport = httpx.MockTransport(mock_handler)

    async def query_as_user(token: str) -> dict:
        client = _PooledAsyncPostgrestClient(
            base_url="https://example.supabase.co/rest/v1",
            schema="public",
            headers={
                "apiKey": "fake_anon_key_123",
                "Authorization": f"Bearer {token}",
            },
            transport=mock_transport,
        )
        # Force a context switch between construction and execution to maximize interleaving race potential
        await asyncio.sleep(0)
        resp = await client.table("profiles").select("*").execute()
        return resp.data[0]

    # Run 50 rounds of concurrent execution
    for round_idx in range(1, 51):
        res_a, res_b = await asyncio.gather(
            query_as_user(token_a),
            query_as_user(token_b),
        )

        assert res_a["owner"] == "user_a", f"Round {round_idx}: User A received leaked user_b data!"
        assert res_a["id"] == "user_a_123", f"Round {round_idx}: User A received incorrect record ID!"

        assert res_b["owner"] == "user_b", f"Round {round_idx}: User B received leaked user_a data!"
        assert res_b["id"] == "user_b_456", f"Round {round_idx}: User B received incorrect record ID!"


@pytest.mark.asyncio
async def test_legacy_shared_client_auth_mutation_vulnerability_demo():
    """
    Sanity test demonstrating why sharing a single client and calling .auth(token) per request is unsafe.
    This test verifies that calling .auth() on a shared client mutates its internal headers dictionary.
    """
    # Construct single client
    shared_client = _PooledAsyncPostgrestClient(
        base_url="https://example.supabase.co/rest/v1",
        schema="public",
        headers={"apiKey": "fake_anon_key_123"},
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=[])),
    )

    # Calling auth("token_a") mutates shared client headers
    shared_client.auth("token_a")
    assert shared_client.session.headers["Authorization"] == "Bearer token_a"

    # Calling auth("token_b") overwrites shared client headers for all concurrent tasks using shared_client
    shared_client.auth("token_b")
    assert shared_client.session.headers["Authorization"] == "Bearer token_b"
