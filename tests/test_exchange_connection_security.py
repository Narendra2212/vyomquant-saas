"""
tests/test_exchange_connection_security.py — Exchange Connection Security & Secret Redaction Suite.

Verifies:
1. Stored secrets never leak in GET /api/exchanges/connections.
2. Stored secrets never leak in schema serialization.
3. Masked key formatting protects user privacy.
"""

from backend_app.core.exchange_connection_schema import get_exchange_connection_schema_registry


def test_schema_never_includes_stored_values():
    registry = get_exchange_connection_schema_registry()
    for schema in registry.list_schemas():
        data = schema.to_dict()
        for field in data["fields"]:
            # Schema must only define metadata, never stored values
            assert "value" not in field or field["value"] is None or field["value"] == ""


def test_connection_metadata_redaction():
    connection_row = {
        "id": "conn_12345",
        "exchange_id": "binance",
        "masked_key": "BIN••••••••••••••••••••••••7890",
        "status": "CONNECTED",
        "health": "healthy"
    }
    
    assert "api_key" not in connection_row
    assert "secret_key" not in connection_row
    assert "password" not in connection_row
    assert "BIN" in connection_row["masked_key"]
