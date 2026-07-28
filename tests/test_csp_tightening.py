"""
tests/test_csp_tightening.py

Unit tests verifying tightened Content-Security-Policy headers.
"""

import pytest
from fastapi.testclient import TestClient
from backend_app.main import app


client = TestClient(app)


def test_csp_header_present_and_tightened():
    response = client.get("/health/live")
    assert response.status_code == 200
    
    csp = response.headers.get("Content-Security-Policy", "")
    assert csp, "CSP header must be present"
    
    # Parse directives
    directives = {}
    for part in csp.split(";"):
        part = part.strip()
        if part and " " in part:
            k, v = part.split(" ", 1)
            directives[k.strip()] = v.strip()
    
    script_src = directives.get("script-src", "")
    connect_src = directives.get("connect-src", "")
    
    # 1. script-src must not contain unsafe-inline or unsafe-eval
    assert "'unsafe-inline'" not in script_src, "script-src must not allow unsafe-inline"
    assert "'unsafe-eval'" not in script_src, "script-src must not allow unsafe-eval"
    
    # 2. script-src must have nonce
    assert "'nonce-" in script_src, "script-src must include a per-request nonce"
    
    # 3. connect-src must not contain unscoped http: or https: wildcards
    connect_tokens = connect_src.split()
    assert "http:" not in connect_tokens, "connect-src must not allow all http:"
    assert "https:" not in connect_tokens, "connect-src must not allow all https:"


def test_csp_nonce_unique_per_request():
    resp1 = client.get("/health/live")
    resp2 = client.get("/health/live")
    
    csp1 = resp1.headers.get("Content-Security-Policy", "")
    csp2 = resp2.headers.get("Content-Security-Policy", "")
    
    nonce1 = [t for t in csp1.split() if t.startswith("'nonce-")]
    nonce2 = [t for t in csp2.split() if t.startswith("'nonce-")]
    
    assert nonce1 and nonce2, "Nonces must be present"
    assert nonce1 != nonce2, "Nonce must be unique per request"

