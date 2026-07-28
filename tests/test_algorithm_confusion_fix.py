"""
tests/test_algorithm_confusion_fix.py

Unit tests verifying CWE-347 Algorithm Confusion fix in backend_app.core.auth_middleware.

WHAT IS TESTED
-------------
1. decode_token_local does not branch on client-controlled unverified alg header.
2. Valid ES256 tokens verify via JWKS.
3. Attacker tokens with alg: HS256 signed with shared secrets are REJECTED for end-user auth.
4. Attacker tokens with alg: none are REJECTED.
5. Explicit test harness tokens with iss="algo22-test" in test mode route through isolated test helper.
"""

import time
import jwt
import pytest
from unittest.mock import patch, MagicMock
from jwt.exceptions import InvalidTokenError

from backend_app.core.auth_middleware import decode_token_local


class TestAlgorithmConfusionFix:
    def test_es256_valid_token_verification(self):
        mock_jwks_client = MagicMock()
        mock_signing_key = MagicMock()
        mock_signing_key.key = "MOCK_EC_PUBLIC_KEY"
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        expected_payload = {
            "sub": "user-es256-123",
            "email": "user@vyomquant.com",
            "aud": "authenticated",
            "exp": int(time.time()) + 3600
        }

        with patch("backend_app.core.auth_middleware._get_jwks_client", return_value=mock_jwks_client):
            with patch("jwt.decode", return_value=expected_payload) as mock_jwt_decode:
                payload = decode_token_local("mock.es256.jwt")
                assert payload == expected_payload
                mock_jwt_decode.assert_called_once()
                # Verify algorithms option is strictly ["ES256"]
                assert mock_jwt_decode.call_args[1]["algorithms"] == ["ES256"]

    def test_hs256_attacker_token_rejected(self, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("SUPABASE_JWT_SECRET", "super-secret-key-12345678901234567890")
        
        # Mint token with alg: HS256 signed with shared secret
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "sub": "attacker-user-999",
            "email": "attacker@evil.com",
            "aud": "authenticated",
            "exp": int(time.time()) + 3600
        }
        token = jwt.encode(payload, "super-secret-key-12345678901234567890", algorithm="HS256", headers=header)

        # In production or standard flow, JWKS ES256 fails to find signing key for HS256 token
        mock_jwks_client = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.side_effect = Exception("No matching kid in JWKS")

        with patch("backend_app.core.auth_middleware._get_jwks_client", return_value=mock_jwks_client):
            with pytest.raises(InvalidTokenError) as exc_info:
                decode_token_local(token)
            assert "Invalid token signature or algorithm" in str(exc_info.value)

    def test_alg_none_attacker_token_rejected(self, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        
        token = "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.ZXlKaGJHY2lPaUp1YjI1bElpd2lkSGx3SWpvaUpWZFVJbjAuZXlKemRXSWlPaUpzYjJkMlpXUWlMQ0psYldGcGJDIJN0WVhSMFpYSWlPaUppWlhKSlpHVWlMQ0psZUhBaU9qRTNNVEV5TkRRNU56Y3NJbUZ1WkNJNkkyRjFkR2hsYm5ScFkyRjBaV1FpZlEu.unsigned"

        mock_jwks_client = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.side_effect = Exception("No matching key")

        with patch("backend_app.core.auth_middleware._get_jwks_client", return_value=mock_jwks_client):
            with pytest.raises(InvalidTokenError):
                decode_token_local(token)

    def test_explicit_test_harness_token_accepted_in_test_mode(self, monkeypatch):
        monkeypatch.setenv("ENV", "testing")
        secret = "dev-secret-change-in-production"
        monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)

        test_payload = {
            "sub": "test-user-id-123",
            "iss": "algo22-test",
            "aud": "authenticated",
            "exp": int(time.time()) + 3600
        }
        token = jwt.encode(test_payload, secret, algorithm="HS256")

        mock_jwks_client = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.side_effect = Exception("Not JWKS")

        with patch("backend_app.core.auth_middleware._get_jwks_client", return_value=mock_jwks_client):
            payload = decode_token_local(token)
            assert payload["sub"] == "test-user-id-123"
            assert payload["iss"] == "algo22-test"
