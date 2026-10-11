"""Phase 7 — contract tests for the canonical exchange capability registry.

These run in the DEFAULT lane. They are credential-free and network-free: everything
asserted here is decidable from the installed ccxt plus the registry's own logic.

The network-dependent part of the model (rungs 3+) is verified by
`scripts/verify_exchange_connectivity.py` and its artifact, and is deliberately NOT
asserted here — a unit lane that reaches an exchange is the ENV-01 defect this repository
already found once.

WHAT THESE TESTS ARE PROTECTING
-------------------------------
One property, stated five different ways: **a capability claim must never be derivable
from something cheaper than the claim itself.** Every historical defect in this area was
an instance of that — `exchange_certification.py` asserting a sandbox from a hardcoded
boolean, `/supported` asserting tradability from `ccxt.exchanges` membership, the FAQ
asserting a testnet that raises `NotSupported`.
"""
from __future__ import annotations

import pytest

from backend_app.core.exchange_capability_registry import (SAFETY_BLOCKED,
                                                           CapabilityFlags,
                                                           ConnectivityState,
                                                           EvidenceClass,
                                                           ExchangeCapabilityRegistry,
                                                           ExchangeRecord, StateEvidence,
                                                           get_capability_registry)


@pytest.fixture(scope="module")
def registry() -> ExchangeCapabilityRegistry:
    """A registry with inventory loaded, and NO verification artifact merged.

    Deliberately skips `load_verification_results` so these tests measure the model, not
    whatever the last connectivity run happened to produce. A test whose outcome depends
    on a network run from an hour ago is not a contract test.
    """
    reg = ExchangeCapabilityRegistry()
    reg.load_inventory()
    return reg


# ══════════════════════════════════════════════════════════════════════════════════════
#  THE LADDER — states are not inferred
# ══════════════════════════════════════════════════════════════════════════════════════

def test_ladder_has_exactly_the_eight_required_states():
    assert [s.name for s in ConnectivityState] == [
        "DISCOVERED",
        "CLIENT_INITIALIZED",
        "PUBLIC_CONNECTIVITY_VERIFIED",
        "MARKET_DATA_VERIFIED",
        "AUTHENTICATED_ACCESS_VERIFIED",
        "SANDBOX_VERIFIED",
        "TRADING_CERTIFIED",
        "LIVE_TRADING_AUTHORIZED",
    ]


def test_a_high_state_does_not_confer_the_states_below_it():
    """The central rule. Holding rung 7 must not imply rungs 3-6.

    Written as a hostile case: a record is handed `TRADING_CERTIFIED` and nothing else,
    which is an incoherent state a buggy ingest could produce. The registry must report
    the rung it was given, must NOT claim the prerequisites, and must flag the gap.
    """
    rec = ExchangeRecord(exchange_id="fictional", display_name="Fictional")
    rec.record(StateEvidence(
        state=ConnectivityState.TRADING_CERTIFIED, attained=True,
        evidence_class=EvidenceClass.SANDBOX, method="hand-built", detail="test",
    ))

    assert rec.has_state(ConnectivityState.TRADING_CERTIFIED)
    for lower in (ConnectivityState.PUBLIC_CONNECTIVITY_VERIFIED,
                  ConnectivityState.MARKET_DATA_VERIFIED,
                  ConnectivityState.AUTHENTICATED_ACCESS_VERIFIED,
                  ConnectivityState.SANDBOX_VERIFIED):
        assert not rec.has_state(lower), f"{lower.name} was inferred, not evidenced"

    # And the incoherence is surfaced rather than silently tolerated.
    problems = rec.inconsistencies()
    assert problems, "an unsupported high state produced no inconsistency report"
    assert any("PUBLIC_CONNECTIVITY_VERIFIED" in p for p in problems)


def test_a_low_state_does_not_confer_the_states_above_it():
    rec = ExchangeRecord(exchange_id="fictional", display_name="Fictional")
    rec.record(StateEvidence(
        state=ConnectivityState.CLIENT_INITIALIZED, attained=True,
        evidence_class=EvidenceClass.MEASURED_LOCAL, method="cls({})", detail="test",
    ))
    assert rec.attained() == ConnectivityState.CLIENT_INITIALIZED
    assert not rec.has_state(ConnectivityState.PUBLIC_CONNECTIVITY_VERIFIED)
    assert not rec.is_tradable()


def test_failed_evidence_does_not_attain_the_state():
    """`attained=False` is a RECORD OF A FAILED CHECK, not a quiet absence.

    The distinction matters for reporting: a venue that was checked and failed, and a
    venue nobody checked, are both "not verified" but only the first has a failure class
    worth acting on.
    """
    rec = ExchangeRecord(exchange_id="fictional", display_name="Fictional")
    rec.record(StateEvidence(
        state=ConnectivityState.PUBLIC_CONNECTIVITY_VERIFIED, attained=False,
        evidence_class=EvidenceClass.NONE, method="load_markets()",
        detail="venue timed out",
    ))
    assert not rec.has_state(ConnectivityState.PUBLIC_CONNECTIVITY_VERIFIED)
    assert rec.attained() == ConnectivityState.DISCOVERED
    assert "PUBLIC_CONNECTIVITY_VERIFIED" in rec.evidence


# ══════════════════════════════════════════════════════════════════════════════════════
#  TRADING AUTHORIZATION — fail closed
# ══════════════════════════════════════════════════════════════════════════════════════

def test_no_venue_is_tradable_without_the_top_rung(registry):
    """Live trading is OFF. The inventory alone must authorize nothing."""
    assert registry.tradable() == [], (
        "a venue is reported tradable from inventory alone; LIVE_TRADING_AUTHORIZED "
        "must be granted only by the release gate"
    )


def test_full_connectivity_still_does_not_authorize_trading():
    """The requirement, as an assertion: connectivity is not permission.

    Every credential-free and credentialed verification rung is granted, including
    `SANDBOX_VERIFIED` and `TRADING_CERTIFIED`. The venue must still not be tradable,
    because the final rung is a release decision rather than a measurement.
    """
    rec = ExchangeRecord(exchange_id="fictional", display_name="Fictional")
    for state in (ConnectivityState.DISCOVERED,
                  ConnectivityState.CLIENT_INITIALIZED,
                  ConnectivityState.PUBLIC_CONNECTIVITY_VERIFIED,
                  ConnectivityState.MARKET_DATA_VERIFIED,
                  ConnectivityState.AUTHENTICATED_ACCESS_VERIFIED,
                  ConnectivityState.SANDBOX_VERIFIED,
                  ConnectivityState.TRADING_CERTIFIED):
        rec.record(StateEvidence(state=state, attained=True,
                                 evidence_class=EvidenceClass.SANDBOX,
                                 method="test", detail="test"))

    assert rec.attained() == ConnectivityState.TRADING_CERTIFIED
    assert not rec.is_tradable(), (
        "TRADING_CERTIFIED was treated as permission to trade; only "
        "LIVE_TRADING_AUTHORIZED may do that"
    )


def test_bitget_is_safety_blocked_with_a_stated_reason(registry):
    """EXCH-01. The block must exist, be reasoned, and remove bitget from routing."""
    assert "bitget" in SAFETY_BLOCKED
    reason = SAFETY_BLOCKED["bitget"]
    assert "EXCH-01" in reason
    assert "production" in reason.lower()

    assert "bitget" not in registry.order_routing_allowlisted(), (
        "bitget is still routable despite the safety block"
    )

    rec = registry.get("bitget")
    assert rec is not None
    assert any("SAFETY BLOCK" in lim for lim in rec.known_limitations)
    assert not rec.is_tradable()


def test_safety_blocked_venues_are_distinguishable_from_merely_unlisted(registry):
    """"Deliberately blocked" and "never allowlisted" must not look the same.

    A venue absent from the allowlist is a scope decision; a venue removed from it for a
    money-safety defect is an incident. Collapsing them loses the reason bitget is gone.
    """
    blocked = registry.safety_blocked()
    allowlisted = registry.order_routing_allowlisted()

    assert "bitget" in blocked
    assert "bitget" not in allowlisted
    # A venue nobody ever allowlisted carries no block reason.
    assert "yobit" not in blocked
    assert "yobit" not in allowlisted


# ══════════════════════════════════════════════════════════════════════════════════════
#  INVENTORY INTEGRITY — duplicates, aliases, invalid ids
# ══════════════════════════════════════════════════════════════════════════════════════

def test_inventory_matches_the_installed_ccxt(registry):
    import ccxt

    assert len(registry.all_records()) == len(ccxt.exchanges)
    for eid in ccxt.exchanges:
        assert registry.get(eid) is not None, f"{eid} missing from the registry"


def test_every_record_id_is_unique_and_lowercase(registry):
    ids = [r.exchange_id for r in registry.all_records()]
    assert len(ids) == len(set(ids)), "duplicate exchange ids in the registry"
    assert all(i == i.lower() for i in ids)


def test_aliases_are_excluded_from_eligible_candidates(registry):
    """An alias must not inflate the connectivity denominator.

    ccxt 4.3.92 self-declares 7 aliases (`gateio` for `gate`, `huobi` for `htx`, and so
    on). Counting both names would make the 100-110 target easier to hit by double
    counting the same venue, which is precisely the kind of inflation the brief forbids.
    """
    eligible = registry.eligible_candidates()
    assert all(not r.is_alias for r in eligible)

    aliases = [r.exchange_id for r in registry.all_records() if r.is_alias]
    assert aliases, "no aliases detected at all — the alias signal has regressed"
    for a in aliases:
        assert a not in {r.exchange_id for r in eligible}


def test_unknown_exchange_returns_none_not_a_fabricated_record(registry):
    assert registry.get("definitely-not-an-exchange") is None
    assert registry.get("") is None


def test_eligible_candidates_require_a_public_market_data_method(registry):
    """Eligibility means "worth attempting a credential-free check", nothing more."""
    for r in registry.eligible_candidates():
        c = r.capabilities
        assert any((c.public_ticker, c.public_tickers, c.public_order_book,
                    c.public_ohlcv, c.public_trades, c.public_markets)), (
            f"{r.exchange_id} is eligible with no public market-data method"
        )


def test_eligible_count_is_in_the_expected_band(registry):
    """A guard on the inventory, not on connectivity.

    If a ccxt bump moves this materially, the launch target's denominator has changed and
    the launch list must be re-derived rather than silently carried forward. Measured on
    4.3.92: 103. On 4.5.85: 104 (and the raw id count DROPS to 104, because upstream
    removed venues) — see staging/ccxt_upgrade_delta.json.
    """
    n = len(registry.eligible_candidates())
    assert 90 <= n <= 130, (
        f"eligible candidate count is {n}, outside the band this launch plan was "
        f"derived against. Re-derive the launch list."
    )


# ══════════════════════════════════════════════════════════════════════════════════════
#  CAPABILITY FLAGS — tri-state, never one ambiguous boolean
# ══════════════════════════════════════════════════════════════════════════════════════

def test_capability_flags_default_to_unverified_not_false():
    """`None` must not collapse to `False`.

    "Unverified" and "unavailable" drive different decisions — one is a gap in testing,
    the other is a product limitation — and a dashboard that renders both as a red cross
    is lying about one of them.
    """
    c = CapabilityFlags()
    for fname in ("public_ticker", "private_balance", "order_create",
                  "sandbox_routing_proven", "order_post_only"):
        assert getattr(c, fname) is None
        assert getattr(c, fname) is not False


def test_sandbox_declared_and_sandbox_proven_are_separate_fields(registry):
    """EXCH-01's shape, encoded in the schema.

    `sandbox_url_declared` is a `urls['test']` lookup. `sandbox_routing_proven` is a
    measurement of whether arming sandbox mode actually left production. Bitget is the
    case where a single field would have been actively dangerous: it would have read
    `False` for the URL (correct) while the executor happily "entered sandbox mode".
    Neither field may be derived from the other.
    """
    c = CapabilityFlags(sandbox_url_declared=True)
    assert c.sandbox_routing_proven is None, (
        "declaring a sandbox URL inferred that routing was proven"
    )

    # And the inventory never sets the proven flag, because it makes no measurement.
    for r in registry.all_records():
        assert r.capabilities.sandbox_routing_proven is None, (
            f"{r.exchange_id} has sandbox_routing_proven set from inventory alone"
        )


def test_declared_order_capability_is_not_a_trading_claim(registry):
    """103 venues declare `createOrder`; the platform routes to 6. Both must be visible."""
    declares = [r for r in registry.eligible_candidates()
                if r.capabilities.order_create is True]
    assert len(declares) > 50, "ccxt declares createOrder far more widely than this"

    routable = set(registry.order_routing_allowlisted())
    assert len(routable) < len(declares), (
        "the routing allowlist has been widened to match ccxt's declarations — the "
        "connectivity target must never be satisfied by broadening order routing"
    )
    assert not any(r.is_tradable() for r in declares)


# ══════════════════════════════════════════════════════════════════════════════════════
#  INVENTORY MAKES NO NETWORK CLAIM
# ══════════════════════════════════════════════════════════════════════════════════════

def test_inventory_never_claims_connectivity(registry):
    """`load_inventory` sends no packet, so it must assert no rung above 2.

    This is the constructor-only conflation the brief explicitly forbids counting.
    """
    for r in registry.all_records():
        for state in (ConnectivityState.PUBLIC_CONNECTIVITY_VERIFIED,
                      ConnectivityState.MARKET_DATA_VERIFIED,
                      ConnectivityState.AUTHENTICATED_ACCESS_VERIFIED,
                      ConnectivityState.SANDBOX_VERIFIED,
                      ConnectivityState.TRADING_CERTIFIED,
                      ConnectivityState.LIVE_TRADING_AUTHORIZED):
            assert not r.has_state(state), (
                f"{r.exchange_id} claims {state.name} from offline inventory"
            )


def test_client_initialized_evidence_says_it_made_no_network_call(registry):
    rec = registry.get("binance")
    ev = rec.evidence["CLIENT_INITIALIZED"]
    assert ev.attained
    assert ev.evidence_class == EvidenceClass.MEASURED_LOCAL
    assert "no network" in (ev.detail or "").lower()


def test_summary_reports_the_target_against_connectivity_not_discovery(registry):
    s = registry.summary()
    assert s["target"]["metric"] == "PUBLIC_CONNECTIVITY_VERIFIED"
    # With no artifact merged, the honest answer is zero and the target is unmet.
    assert s["target"]["current"] == 0
    assert s["target"]["met"] is False
    assert s["target"]["remaining_to_low"] == 100
    assert s["tradable_count"] == 0


def test_summary_counts_every_ladder_state(registry):
    s = registry.summary()
    assert set(s["state_counts"]) == {st.name for st in ConnectivityState}
    assert s["state_counts"]["DISCOVERED"] == len(registry.all_records())


def test_registry_singleton_is_stable():
    assert get_capability_registry() is get_capability_registry()
