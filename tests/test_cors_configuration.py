"""
tests/test_cors_configuration.py

Unit tests verifying strict environment-driven CORS configuration and removal of hardcoded legacy production origins.
"""

import os
import pytest
from fastapi.testclient import TestClient


def test_cors_production_origins_allowed_when_configured(monkeypatch):
    """Test that configured production origins in CORS_ORIGINS are allowed with credentials."""
    monkeypatch.setenv("CORS_ORIGINS", "https://app.vyomquant.com,https://vyomquant.com")
    
    import importlib
    import backend_app.main
    importlib.reload(backend_app.main)
    
    client = TestClient(backend_app.main.app)
    
    response = client.options(
        "/health/live",
        headers={
            "Origin": "https://app.vyomquant.com",
            "Access-Control-Request-Method": "GET",
        }
    )
    assert response.headers.get("access-control-allow-origin") == "https://app.vyomquant.com"
    assert response.headers.get("access-control-allow-credentials") == "true"


def test_cors_legacy_algo22_origins_rejected_when_not_in_env(monkeypatch):
    """Test that legacy hardcoded origins (algo22.io) are rejected if not in CORS_ORIGINS."""
    monkeypatch.setenv("CORS_ORIGINS", "https://app.vyomquant.com")
    
    import importlib
    import backend_app.main
    importlib.reload(backend_app.main)
    
    client = TestClient(backend_app.main.app)
    
    response = client.options(
        "/health/live",
        headers={
            "Origin": "https://app.algo22.io",
            "Access-Control-Request-Method": "GET",
        }
    )
    assert response.headers.get("access-control-allow-origin") != "https://app.algo22.io"


def test_cors_untrusted_origin_rejected(monkeypatch):
    """Test that untrusted external origins are rejected by CORS middleware."""
    monkeypatch.setenv("CORS_ORIGINS", "https://app.vyomquant.com")
    
    import importlib
    import backend_app.main
    importlib.reload(backend_app.main)
    
    client = TestClient(backend_app.main.app)
    
    response = client.options(
        "/health/live",
        headers={
            "Origin": "https://attacker-domain.com",
            "Access-Control-Request-Method": "GET",
        }
    )
    assert response.headers.get("access-control-allow-origin") != "https://attacker-domain.com"


def test_cors_wildcard_disables_credentials(monkeypatch):
    """Test that setting CORS_ORIGINS='*' disables allow_credentials per CORS spec."""
    monkeypatch.setenv("CORS_ORIGINS", "*")
    
    import importlib
    import backend_app.main
    importlib.reload(backend_app.main)
    
    client = TestClient(backend_app.main.app)
    
    response = client.options(
        "/health/live",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "GET",
        }
    )
    assert response.headers.get("access-control-allow-credentials") != "true"

