"""CLAIM-02 -- the exchange-certification registry no longer claims more than has been
measured, and the one HTTP surface that read it as a "real" number is corrected too.

THE DEFECT
    ``backend_app/core/exchange_certification.py`` hand-wrote Level 5 ("fully certified
    for live trading") for binance, bybit, coinbase, kraken, and okx, Level 4 for kucoin,
    Level 3 for gateio, and Level 2 for bitfinex -- and `margin`/`leverage`/`futures`/
    `swap` support for most of them. None of that was measured. The real, dated evidence
    this platform already has (``staging/EXCHANGE_SANDBOX_CERTIFICATION.md``, Phase 10,
    2026-10-10, and its companion ``staging/phase10_exchange_sandbox_matrix.json``) states
    plainly: "SANDBOX ORDERS PLACED: 0" and "EXCHANGE SANDBOX CERTIFICATION: NOT ACHIEVED
    -- NO SANDBOX CREDENTIAL EXISTS". No venue has had a single authenticated account
    call, sandbox order, or live order placed against it by this platform. Separately,
    GUARD-01/RT-21 already established this platform has no margin, leverage, futures, or
    swap order path anywhere -- so claiming any venue supports them was never true of this
    platform, independent of what the venue itself offers.

    `ExchangeCapabilities.last_verified_at` compounded this: it defaulted to
    ``datetime.now(timezone.utc).isoformat()`` on every construction, so reading a
    capability record -- not verifying anything -- made it look freshly checked.

    `main.py`'s `/api/system/readiness` surfaced `total_certified_level_5` under a
    comment claiming it was "retained because that part was always real".

THE FIX
    Every registry entry is now capped at LEVEL_1_MARKET_METADATA_VERIFIED (ccxt's own
    declared `exchange.has` map -- a real, if purely declarative, fact ccxt reports about
    itself) and claims no margin/leverage/futures/swap support anywhere.
    `last_verified_at` now defaults to `None`. The `readiness` endpoint's comment was
    corrected; its behaviour was not changed beyond what the registry's honest data now
    produces (0 certified exchanges instead of a fabricated 5+).

    `sandbox` booleans were already corrected by an earlier pass (EXCH-04) and are
    reconfirmed, not re-derived, here.

WHAT THIS FILE PROVES
    * no registered exchange claims Level 5 / margin / leverage / futures / swap;
    * every registered exchange is capped at exactly Level 1;
    * `last_verified_at` is `None` by default, not a fabricated "now";
    * `validate_deployment_preflight` now refuses every registered venue -- the safe,
      honest behaviour, given live trading has zero measured certifications;
    * nothing with a legitimate frontend dependency on `is_production_certified`/
      `certification_level` exists (this file does not re-prove the earlier session's
      frontend grep, but pins that `/api/system/readiness`'s own exposed count reflects
      the corrected registry, not the old fabricated one).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend_app.core.exchange_certification import (
    CertificationLevel,
    ExchangeCapabilities,
    get_exchange_certification_registry,
)

REGISTERED_EXCHANGES = ("binance", "bybit", "coinbase", "kraken", "okx", "kucoin", "gateio", "bitfinex")

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestNoVenueOverclaimsCertificationOrMarginFeatures:
    def test_no_venue_is_level_5_production_certified(self):
        registry = get_exchange_certification_registry()
        for exchange_id in REGISTERED_EXCHANGES:
            caps = registry.get_capabilities(exchange_id)
            assert caps.certification_level != CertificationLevel.LEVEL_5_PRODUCTION_READY, (
                f"{exchange_id} still claims Level 5 with no sandbox/live order ever placed"
            )
            assert caps.is_production_certified is False

    def test_every_venue_is_capped_at_level_1(self):
        registry = get_exchange_certification_registry()
        for exchange_id in REGISTERED_EXCHANGES:
            caps = registry.get_capabilities(exchange_id)
            assert caps.certification_level == CertificationLevel.LEVEL_1_MARKET_METADATA_VERIFIED

    def test_no_venue_claims_margin_leverage_futures_swap_hedge_mode(self):
        """GUARD-01/RT-21: this platform has no such order path anywhere."""
        registry = get_exchange_certification_registry()
        for exchange_id in REGISTERED_EXCHANGES:
            caps = registry.get_capabilities(exchange_id)
            assert caps.margin is False, exchange_id
            assert caps.leverage is False, exchange_id
            assert caps.futures is False, exchange_id
            assert caps.swap is False, exchange_id
            assert caps.hedge_mode is False, exchange_id

    def test_sandbox_booleans_are_unchanged_from_the_earlier_exch04_correction(self):
        """Not re-derived here -- just confirming this fix did not disturb EXCH-04's
        already-measured sandbox-routability fields.
        """
        registry = get_exchange_certification_registry()
        expected = {
            "binance": True, "bybit": True, "coinbase": False, "kraken": False,
            "okx": True, "kucoin": False, "gateio": True, "bitfinex": False,
        }
        for exchange_id, sandbox in expected.items():
            assert registry.get_capabilities(exchange_id).sandbox is sandbox, exchange_id


class TestLastVerifiedAtIsNoLongerFabricated:
    def test_default_is_none_not_a_fabricated_now(self):
        caps = ExchangeCapabilities(
            exchange_id="x", display_name="X",
            certification_level=CertificationLevel.LEVEL_1_MARKET_METADATA_VERIFIED,
        )
        assert caps.last_verified_at is None

    def test_every_registered_venue_reports_none(self):
        registry = get_exchange_certification_registry()
        for exchange_id in REGISTERED_EXCHANGES:
            assert registry.get_capabilities(exchange_id).last_verified_at is None


class TestPreflightNowHonestlyRefusesEveryVenue:
    def test_preflight_refuses_every_registered_venue_citing_level_1(self):
        registry = get_exchange_certification_registry()
        for exchange_id in REGISTERED_EXCHANGES:
            valid, reason = registry.validate_deployment_preflight({
                "exchange_id": exchange_id,
                "api_key": "k", "api_secret": "s",
                "symbol": "BTC/USDT", "strategy_id": "strat-1",
                "risk_settings": {"max_daily_loss": 500.0},
            })
            assert valid is False
            assert "Level 1" in reason


class TestTheReadinessEndpointSurfacesTheCorrectedCount:
    def test_certification_matrix_total_certified_is_zero(self):
        """What `/api/system/readiness` reads (`get_certification_matrix()
        ["total_certified_level_5"]`) now reports the corrected, honest figure.
        """
        registry = get_exchange_certification_registry()
        matrix = registry.get_certification_matrix()
        assert matrix["total_certified_level_5"] == 0
        assert matrix["total_registered"] == len(REGISTERED_EXCHANGES)

    def test_readiness_handler_no_longer_claims_the_matrix_was_always_real(self):
        """The misleading comment this finding also targeted is gone from main.py."""
        main_py = (REPO_ROOT / "backend_app" / "main.py").read_text(encoding="utf-8")
        assert "retained because that part was always real" not in main_py
        idx = main_py.find("async def system_readiness")
        assert idx != -1
        snippet = main_py[idx: idx + 1200]
        assert "CLAIM-02" in snippet


class TestEvidenceDocumentIsWhatTheRegistryCitesAndItSaysWhatThisFileClaims:
    def test_the_phase10_document_and_matrix_exist_and_agree_with_the_fix(self):
        doc = REPO_ROOT / "staging" / "EXCHANGE_SANDBOX_CERTIFICATION.md"
        matrix_path = REPO_ROOT / "staging" / "phase10_exchange_sandbox_matrix.json"
        assert doc.exists(), "the cited evidence document is missing"
        assert matrix_path.exists(), "the cited evidence matrix is missing"

        text = doc.read_text(encoding="utf-8")
        assert "SANDBOX ORDERS PLACED:            0" in text or "SANDBOX ORDERS PLACED: 0" in text.replace("  ", " ")

        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
        assert matrix["summary"]["credentials_available"] is False
        assert set(matrix["summary"]["order_path_sandbox_reachable"]) == {"binance", "bybit", "okx"}
