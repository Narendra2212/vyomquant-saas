"""
tests/test_exchange_capabilities.py — Detailed Exchange Capability Matrix & Feature Guard Verification.

Verifies:
1. Capabilities across registered spot venues (none platform-wide claims margin/futures/
   swap -- CLAIM-02/GUARD-01/RT-21: this platform has no such order path at all).
2. Stop-loss, Take-profit, Reduce-only, Post-only capability gating (ccxt-declared, per
   CLAIM-02's Level-1 ceiling -- see exchange_certification.py's module docstring).
3. Passphrase and Subaccount requirement detection.
"""

import pytest
from backend_app.core.exchange_certification import (
    get_exchange_certification_registry, ExchangeCapabilities
)


def test_binance_capabilities_matrix():
    caps = get_exchange_certification_registry().get_capabilities("binance")
    assert caps is not None
    assert caps.spot is True
    assert caps.margin is False
    assert caps.futures is False
    assert caps.swap is False
    assert caps.reduce_only is True
    assert caps.post_only is True
    assert caps.leverage is False
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
    # CLAIM-02: futures/swap/hedge_mode are no longer claimed anywhere in this registry --
    # this platform has no margin/leverage/futures/swap order path at all (GUARD-01/RT-21).
    assert caps.futures is False
    assert caps.swap is False
    assert caps.hedge_mode is False
    assert caps.sandbox is True
    assert any("Passphrase" in lim for lim in caps.known_limitations)
