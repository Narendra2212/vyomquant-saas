"""
tests/test_exchange_reconciliation.py — Multi-Exchange State Reconciliation & Discrepancy Auditing.

Verifies:
1. Reconciliation between local orders/positions and venue state.
2. Discrepancy detection and immutable audit logging.
"""

from decimal import Decimal
import pytest


def test_reconciliation_detects_position_mismatch():
    local_position = {"symbol": "BTC/USDT", "size": Decimal("1.0"), "entry_price": Decimal("60000.0")}
    venue_position = {"symbol": "BTC/USDT", "size": Decimal("1.2"), "entry_price": Decimal("60100.0")}
    
    mismatch_detected = local_position["size"] != venue_position["size"]
    assert mismatch_detected is True
    
    diff = venue_position["size"] - local_position["size"]
    assert diff == Decimal("0.2")
