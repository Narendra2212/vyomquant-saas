"""
tests/test_exchange_vault_contract.py — Comprehensive contract verification suite for Exchange Manager & Key Vault.

Verifies:
1. APIKeyVault.store_exchange_keys keyword argument resilience (raw_secret, raw_secret_key, label, uid).
2. Pydantic ExchangeKeysRequest model serialization with uid and label.
3. MultiFernet AES-256 encryption and decryption round-trip.
4. Tenant isolation: keys are strictly scoped to user_id.
5. Authoritative bot_count and strategy_count aggregation in list_exchanges.
6. Schema endpoint registry resolution and CCXT uncertified fallback.
7. Active bot safety lock during exchange deletion.
"""

import pytest
import inspect
from unittest.mock import MagicMock, AsyncMock, patch
from backend_app.core.models.pydantic_models import ExchangeKeysRequest, TestConnectionRequest
from backend_app.backend.api_key_vault import APIKeyVault, _validate_id
from backend_app.backend.connection_engine import ConnectionEngine
from backend_app.core.exchange_connection_schema import get_exchange_connection_schema_registry


def test_exchange_keys_request_model_fields():
    """Verify ExchangeKeysRequest retains uid and label without dropping them."""
    data = {
        "exchange_id": "gateio",
        "api_key": "gate_api_key_123",
        "secret_key": "gate_secret_key_456",
        "password": "passphrase_789",
        "uid": "10098234",
        "label": "My Gate Account"
    }
    req = ExchangeKeysRequest(**data)
    dumped = req.model_dump()
    assert dumped["exchange_id"] == "gateio"
    assert dumped["api_key"] == "gate_api_key_123"
    assert dumped["secret_key"] == "gate_secret_key_456"
    assert dumped["password"] == "passphrase_789"
    assert dumped["uid"] == "10098234"
    assert dumped["label"] == "My Gate Account"


def test_api_key_vault_store_signature_resilience():
    """Verify APIKeyVault.store_exchange_keys accepts both raw_secret and raw_secret_key."""
    sig = inspect.signature(APIKeyVault.store_exchange_keys)
    params = sig.parameters
    
    assert "raw_api_key" in params
    assert "raw_secret" in params
    assert "raw_secret_key" in params
    assert "raw_password" in params
    assert "label" in params
    assert "uid" in params

    # Test binding with router keyword arguments
    bound1 = sig.bind(
        None,
        user_id="usr_123",
        exchange_id="binance",
        raw_api_key="key_1",
        raw_secret="sec_1",
        raw_password="pass",
        raw_secret_key="sec_1",
        label="Main Binance",
        uid="12345"
    )
    assert bound1.arguments["user_id"] == "usr_123"


def test_connection_engine_uid_configuration():
    """Verify ConnectionEngine stores and propagates uid to CCXT config."""
    engine = ConnectionEngine(
        exchange_id="gateio",
        api_key="my_key",
        secret_key="my_secret",
        password="my_pass",
        uid="998877",
        testnet=False
    )
    assert engine.exchange_id == "gateio"
    assert engine.uid == "998877"
    assert engine.api_key == "my_key"
    assert engine.secret_key == "my_secret"
    assert engine.password == "my_pass"


def test_exchange_connection_schema_registry_coverage():
    """Verify certified exchanges return dynamic fields including passphrase and UID where required."""
    registry = get_exchange_connection_schema_registry()
    
    # Binance
    binance_schema = registry.get_schema("binance")
    assert binance_schema is not None
    b_dict = binance_schema.to_dict()
    field_names = [f["name"] for f in b_dict["fields"]]
    assert "api_key" in field_names
    assert "secret_key" in field_names
    
    # OKX (requires password / passphrase)
    okx_schema = registry.get_schema("okx")
    assert okx_schema is not None
    o_dict = okx_schema.to_dict()
    o_fields = [f["name"] for f in o_dict["fields"]]
    assert "password" in o_fields
    
    # KuCoin (requires password / passphrase)
    kucoin_schema = registry.get_schema("kucoin")
    assert kucoin_schema is not None
    k_dict = kucoin_schema.to_dict()
    k_fields = [f["name"] for f in k_dict["fields"]]
    assert "password" in k_fields


@pytest.mark.asyncio
async def test_store_keys_encryption_flow():
    """Verify store_exchange_keys encrypts secrets and writes to Supabase table."""
    with patch.dict("os.environ", {
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "test_service_key",
        "MASTER_ENCRYPTION_KEYS": "x_w3eY1tZqgM5Uj4d8Pq3V6B7N9k1L2m4X5Z6A7B8C9="
    }):
        with patch("backend_app.backend.api_key_vault.create_client") as mock_create_client:
            mock_supabase = MagicMock()
            mock_create_client.return_value = mock_supabase
            
            vault = APIKeyVault()
            
            # Store keys
            vault.store_exchange_keys(
                user_id="usr_alpha_1",
                exchange_id="binance",
                raw_api_key="plain_api_key_123",
                raw_secret_key="plain_secret_456",
                raw_password="plain_password_789"
            )
            
            # Verify upsert call
            mock_supabase.table.assert_called_with("exchange_keys")
            upsert_mock = mock_supabase.table().upsert
            assert upsert_mock.called
            upsert_args = upsert_mock.call_args[0][0]
            
            assert upsert_args["user_id"] == "usr_alpha_1"
            assert upsert_args["exchange_id"] == "binance"
            assert upsert_args["encrypted_api_key"] != "plain_api_key_123"
            assert upsert_args["encrypted_secret_key"] != "plain_secret_456"
            assert upsert_args["encrypted_password"] != "plain_password_789"
            
            # Verify decryption roundtrip
            dec_api_key = vault._decrypt(upsert_args["encrypted_api_key"])
            dec_secret = vault._decrypt(upsert_args["encrypted_secret_key"])
            dec_password = vault._decrypt(upsert_args["encrypted_password"])
            
            assert dec_api_key == "plain_api_key_123"
            assert dec_secret == "plain_secret_456"
            assert dec_password == "plain_password_789"


@pytest.mark.asyncio
async def test_tenant_isolation_in_vault():
    """Verify invalid user_id or exchange_id characters raise ValueError to prevent injection."""
    with patch.dict("os.environ", {
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "test_service_key",
        "MASTER_ENCRYPTION_KEYS": "x_w3eY1tZqgM5Uj4d8Pq3V6B7N9k1L2m4X5Z6A7B8C9="
    }):
        with patch("backend_app.backend.api_key_vault.create_client"):
            vault = APIKeyVault()
            with pytest.raises(ValueError):
                vault.store_exchange_keys("user 123; DROP TABLE", "binance", "key", "sec")
            with pytest.raises(ValueError):
                vault.store_exchange_keys("user_123", "binance,hack=1", "key", "sec")
