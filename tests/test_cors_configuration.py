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


# ═══════════════════════════════════════════════════════════════════════════
# vyomquant.in DOMAIN MIGRATION
#
# The production ECS task definition (family `vyomquant-api`) ships
# CORS_ORIGINS="https://app.vyomquant.com,https://vyomquant.com" — the .com domain. The
# domain actually being brought online is vyomquant.in. Verified against the live
# deployment at the time of writing:
#
#   OPTIONS /api/market/health  Origin: https://app.vyomquant.com  -> 200, ACAO echoed
#   OPTIONS /api/market/health  Origin: https://app.vyomquant.in   -> 400, no ACAO header
#
# These two tests pin both halves of that: that the .in origins work once configured, and
# that they are genuinely refused until they are. The second is not a bug being asserted as
# correct — it is the reason the task definition has to change, kept executable so the
# migration cannot be quietly forgotten.
#
# Only relevant when the SPA and the API sit on different origins (app.vyomquant.in calling
# api.vyomquant.in). In the same-origin CloudFront topology the browser never sends Origin
# for these calls and CORS is not consulted at all. See infra/dns.md.
# ═══════════════════════════════════════════════════════════════════════════

VYOMQUANT_IN_ORIGINS = [
    "https://vyomquant.in",
    "https://www.vyomquant.in",
    "https://app.vyomquant.in",
]


def test_cors_vyomquant_in_origins_allowed_when_configured(monkeypatch):
    """Each vyomquant.in production origin is allowed, with credentials, once configured."""
    monkeypatch.setenv("CORS_ORIGINS", ",".join(VYOMQUANT_IN_ORIGINS))

    import importlib
    import backend_app.main
    importlib.reload(backend_app.main)

    client = TestClient(backend_app.main.app)

    for origin in VYOMQUANT_IN_ORIGINS:
        response = client.options(
            "/health/live",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.headers.get("access-control-allow-origin") == origin, (
            f"{origin} must be allowed when present in CORS_ORIGINS"
        )
        # Bearer tokens travel on the Authorization header, so credentialed CORS must stay on.
        assert response.headers.get("access-control-allow-credentials") == "true"


def test_cors_vyomquant_in_rejected_while_only_com_configured(monkeypatch):
    """The deployed .com allow-list does not implicitly cover .in — the TLDs are distinct.

    Guards against assuming the domain migration is "just DNS": until CORS_ORIGINS is
    updated on the task definition, a browser on app.vyomquant.in cannot call the API.
    """
    monkeypatch.setenv("CORS_ORIGINS", "https://app.vyomquant.com,https://vyomquant.com")

    import importlib
    import backend_app.main
    importlib.reload(backend_app.main)

    client = TestClient(backend_app.main.app)

    for origin in VYOMQUANT_IN_ORIGINS:
        response = client.options(
            "/health/live",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.headers.get("access-control-allow-origin") != origin, (
            f"{origin} must NOT be allowed by a .com-only allow-list"
        )
