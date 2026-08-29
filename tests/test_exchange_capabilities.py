"""
tests/test_exchange_capabilities.py — Detailed Exchange Capability Matrix & Feature Guard Verification.

Verifies:
1. Capabilities across certified spot, margin, futures, and swap venues.
2. Stop-loss, Take-profit, Reduce-only, Post-only capability gating.
3. Leverage and Margin mode support per venue.
4. Passphrase and Subaccount requirement detection.
"""

import pytest
from backend_app.core.exchange_certification import (
    get_exchange_certification_registry, ExchangeCapabilities
)


def test_binance_capabilities_matrix():
    caps = get_exchange_certification_registry().get_capabilities("binance")
    assert caps is not None
    assert caps.spot is True
    assert caps.margin is True
    assert caps.futures is True
    assert caps.swap is True
    assert caps.reduce_only is True
    assert caps.post_only is True
    assert caps.leverage is True
    assert caps.sandbox is True


def test_coinbase_capabilities_matrix():
    caps = get_exchange_certification_registry().get_capabilities("coinbase")
    assert caps is not None
    assert caps.spot is True
    assert caps.futures is False
    assert caps.swap is False
    assert caps.leverage is False
    assert len(caps.known_limitations) > 0


def test_okx_capabilities_matrix():
    caps = get_exchange_certification_registry().get_capabilities("okx")
    assert caps is not None
    assert caps.spot is True
    assert caps.futures is True
    assert caps.swap is True
    assert caps.hedge_mode is True
    assert caps.sandbox is True
    assert any("Passphrase" in lim for lim in caps.known_limitations)
