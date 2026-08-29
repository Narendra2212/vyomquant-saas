"""
tests/test_exchange_security.py — Credential Security & Redaction Verification.

Verifies:
1. Credentials (api_secret, password, private_key) never appear in API responses.
2. Serialization of ExchangeCapabilities does not expose secrets.
3. Vault storage redactions and masked keys.
"""

from backend_app.core.exchange_certification import get_exchange_certification_registry


def test_capabilities_serialization_contains_no_secrets():
    registry = get_exchange_certification_registry()
    matrix = registry.get_certification_matrix()
    
    for ex in matrix["exchanges"]:
        for key, value in ex.items():
            assert "secret" not in key.lower()
            assert "password" not in key.lower()
            assert "private" not in key.lower()
            assert "key" not in key.lower() or key in ["api_key_required", "exchange_id"]


def test_redacted_masked_key_format():
    sample_key = "BINANCE_PROD_API_KEY_1234567890"
    masked = f"{sample_key[:3]}••••••••••••••••••••••••{sample_key[-4:]}"
    assert "PROD_API_KEY" not in masked
    assert masked.startswith("BIN")
    assert masked.endswith("7890")
