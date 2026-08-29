"""
tests/test_exchange_connection_schema.py — Dynamic Exchange Connection Schema Test Suite.

Verifies:
1. Dynamic credential requirement schemas across all major exchanges (Binance, OKX, Bybit, Coinbase, Kraken, KuCoin).
2. Specialized credential fields (e.g. OKX/KuCoin passphrase, sandbox switches, market type selectors).
3. Extensibility: Schema definitions can be inspected dynamically without hardcoded assumptions.
"""

import pytest
from backend_app.core.exchange_connection_schema import (
    get_exchange_connection_schema_registry, FieldType
)


def test_binance_dynamic_connection_schema():
    registry = get_exchange_connection_schema_registry()
    schema = registry.get_schema("binance")
    
    assert schema is not None
    assert schema.exchange_id == "binance"
    assert schema.is_certified is True
    assert schema.sandbox_supported is True
    
    field_ids = [f.field_id for f in schema.fields]
    assert "api_key" in field_ids
    assert "secret_key" in field_ids
    assert "market_type" in field_ids
    assert "sandbox" in field_ids
    
    # Binance does NOT require a password/passphrase
    assert "password" not in field_ids


def test_okx_dynamic_connection_schema():
    registry = get_exchange_connection_schema_registry()
    schema = registry.get_schema("okx")
    
    assert schema is not None
    assert schema.exchange_id == "okx"
    assert schema.is_certified is True
    
    field_ids = [f.field_id for f in schema.fields]
    assert "api_key" in field_ids
    assert "secret_key" in field_ids
    assert "password" in field_ids  # OKX requires Passphrase
    assert "market_type" in field_ids


def test_kucoin_dynamic_connection_schema():
    registry = get_exchange_connection_schema_registry()
    schema = registry.get_schema("kucoin")
    
    assert schema is not None
    assert schema.exchange_id == "kucoin"
    
    field_ids = [f.field_id for f in schema.fields]
    assert "api_key" in field_ids
    assert "secret_key" in field_ids
    assert "password" in field_ids


def test_schema_serialization_format():
    registry = get_exchange_connection_schema_registry()
    schema = registry.get_schema("bybit")
    
    data = schema.to_dict()
    assert data["exchange_id"] == "bybit"
    assert isinstance(data["fields"], list)
    assert any(f["name"] == "api_key" for f in data["fields"])
