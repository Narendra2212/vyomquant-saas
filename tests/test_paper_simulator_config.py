"""
tests/test_paper_simulator_config.py - task 25.1's runtime guard and task 25.2's frozen config.

Spec: marketplace-subscriptions-paper-trading tasks 25.1 and 25.2. ``design.md`` ->
"``paper/paper_simulator.py``". Requirements 13.11, 15.6, 16.5, 16.12, 17.5, 18.1, 18.2, 28.3.

The static half of task 25.1 - the AST walk over the paper package - is
``tests/test_paper_no_random.py``, which is authoritative for that. This file covers the three
things that are only observable by running code:

1. **The forbidden-simulator refusal** (Requirement 13.11). Including the two properties that
   make it correct rather than merely present: it compares strings, so the forbidden module is
   never imported; and its message cites line numbers that are checked against the real file, so a
   refusal cannot send a reader to a line that no longer says what it claims.
2. **The metadata refusal** (Requirements 16.5, 17.13, 28.3). Every way the exchange market
   metadata can fail to answer refuses the start, and no path defaults a precision or a maximum.
3. **The frozen configuration** (Requirements 16.12, 17.5, 18.1, 18.2). The fifteen keys, the
   decimal-string serialisation that survives a JSON round trip exactly, and the three separate
   things that make ``paper_sessions.config`` unchangeable - the frozen dataclass, the absence of
   any repository updater, and ``trg_paper_session_config_immutable`` in
   ``009_paper_trading.sql``.

WHAT IS NOT TESTED HERE
-----------------------
``submit_intent``, ``apply_fill``, the fill model and the retry loop are tasks 25.3 to 25.6 and
do not exist yet. Nothing in this file asserts anything about an order.

THE PERSISTENCE_LAYER DOUBLE
----------------------------
``tests/test_paper_repository.FakeSupabase``, the one double this repository has, which as of task
25.2 also enforces ``trg_paper_session_config_immutable``. ``tests/paper_seed.py`` remains the
seeding support module. No second double and no mock of the repository: the config read is
asserted against the same statement-issuing code production uses.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from backend_app.backend.marketplace.errors import (
    ALLOWED_HTTP_STATUS_FOR_CODE,
    HTTP_STATUS_FOR_CODE,
    PAPER_SIMULATOR_MISCONFIGURED,
    PAPER_START_REFUSED,
)
from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper import paper_simulator as sim
from backend_app.backend.paper.errors import PaperError
from backend_app.backend.paper.paper_accounting import (
    DEFAULT_ROUNDING_MODE,
    WEIGHTED_AVERAGE,
    AccountingConfig,
)
from tests.test_paper_repository import FakeCheckViolation, FakeSupabase

USER = "11111111-1111-4111-8111-111111111111"
OTHER_USER = "22222222-2222-4222-8222-222222222222"
SESSION = "33333333-3333-4333-8333-333333333333"
SYMBOL = "BTC/USDT"
EXCHANGE = "binance"

REPO_ROOT = Path(__file__).resolve().parents[1]
EXCHANGE_SIMULATOR = REPO_ROOT / "backend_app" / "backend" / "exchange_simulator.py"
MIGRATION_009 = REPO_ROOT / "backend_app" / "migrations" / "009_paper_trading.sql"


def _run_coroutine(coro: Any) -> Any:
    """Drive one coroutine to completion on a private loop.

    The pattern ``tests/test_settlement_service.py`` establishes, and used for the same reason:
    ``asyncio.run`` closes the loop it created and tears down the async generators bound to it,
    which breaks any later test in the session that expected a loop to still be there. A loop
    created, used and closed here affects nothing outside this call.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# A complete CCXT market entry, in the shape ``connection_engine`` produces and
# ``asset_universe._asset_from_market`` reads. Precision as a TICK SIZE, which is what a
# TICK_SIZE-mode venue reports, so the reading under test is the ambiguous-free one.
def _market_entry(**overrides: Any) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "symbol": SYMBOL,
        "base": "BTC",
        "quote": "USDT",
        "type": "spot",
        "active": True,
        "precision": {"price": 0.01, "amount": 0.00000001},
        "limits": {
            "amount": {"min": 0.0001, "max": 1000.0},
            "cost": {"min": 10.0, "max": 1000000.0},
        },
    }
    entry.update(overrides)
    return entry


def _markets(**overrides: Any) -> Dict[str, Any]:
    return {SYMBOL: _market_entry(**overrides)}


def _metadata(**overrides: Any) -> sim.MarketMetadata:
    return sim.resolve_market_metadata(
        _markets(**overrides), exchange_id=EXCHANGE, symbol=SYMBOL
    )


def _config(**kwargs: Any) -> sim.SessionConfig:
    defaults: Dict[str, Any] = {
        "metadata": _metadata(),
        "currency": "USD",
        "market_data_source": "mds.watch_ohlcv",
    }
    defaults.update(kwargs)
    return sim.freeze_session_config(**defaults)


# ══════════════════════════════════════════════════════════════════════════
#  GUARD 1 - THE FORBIDDEN SIMULATOR (Requirement 13.11)
# ══════════════════════════════════════════════════════════════════════════


class _Forbidden:
    """A stand-in carrying the forbidden ``(module, qualname)`` pair.

    The identity is what the guard compares, so a class whose ``__module__`` and ``__qualname__``
    are set to the forbidden pair is exactly as forbidden as the real one - and using it means
    this test file does not import ``exchange_simulator`` either, which
    ``tests/test_paper_no_random.py`` would only catch inside the package. A test that imported
    the module to prove the module is never imported would be its own counterexample.
    """


_Forbidden.__module__ = "backend_app.backend.exchange_simulator"
_Forbidden.__qualname__ = "PaperTradingExchange"


class _PermittedSimulator:
    pass


_PermittedSimulator.__module__ = sim.SIMULATOR_MODULE
_PermittedSimulator.__qualname__ = sim.SIMULATOR_QUALNAME


def test_the_forbidden_pair_is_exactly_one_tuple_of_strings() -> None:
    """``FORBIDDEN_SIMULATORS`` holds string pairs, never classes.

    The whole point of task 25.1's first guard: a set of classes could only be built by importing
    ``exchange_simulator``, whose module body runs ``import random`` on line 49 - so the guard's
    own operation would put a randomness source in the import graph of the module that exists to
    keep it out. Asserted on the type, because "we compare strings" is a claim about the data
    structure and not about a code path.
    """
    assert sim.FORBIDDEN_SIMULATORS == frozenset(
        {("backend_app.backend.exchange_simulator", "PaperTradingExchange")}
    )
    for entry in sim.FORBIDDEN_SIMULATORS:
        assert isinstance(entry, tuple) and len(entry) == 2
        assert all(isinstance(part, str) for part in entry)


def test_the_module_never_imports_the_forbidden_simulator() -> None:
    """No import of ``backend_app.backend.exchange_simulator`` anywhere in ``paper_simulator``.

    ``tests/test_paper_no_random.py`` asserts this for the whole package; it is restated here on
    the one module whose *job* is the refusal, because that is the module where such an import
    would be most tempting to write and most damaging to have.
    """
    tree = ast.parse(Path(inspect.getfile(sim)).read_text(encoding="utf-8"))
    imported: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any(
        name == "backend_app.backend.exchange_simulator"
        or name.startswith("backend_app.backend.exchange_simulator.")
        for name in imported
    )
    assert "random" not in [name.split(".")[0] for name in imported]


def test_assert_paper_simulator_refuses_the_forbidden_simulator() -> None:
    """Requirement 13.11: the resolved forbidden simulator refuses the start, with 500."""
    with pytest.raises(sim.PaperSimulatorMisconfigured) as caught:
        sim.assert_paper_simulator(_Forbidden)

    error = caught.value
    assert error.code == PAPER_SIMULATOR_MISCONFIGURED
    assert error.http_status == 500
    assert error.details["module"] == "backend_app.backend.exchange_simulator"
    assert error.details["qualname"] == "PaperTradingExchange"


def test_the_refusal_names_why_and_not_merely_that() -> None:
    """The message names ``random.gauss`` and ``random.random`` with their line numbers.

    Requirement 13.11 asks for the *reason* to be reportable, and a refusal that said only
    "misconfigured" would send an operator to read the whole simulator to find out what is wrong
    with it.
    """
    with pytest.raises(sim.PaperSimulatorMisconfigured) as caught:
        sim.assert_paper_simulator(_Forbidden)
    message = str(caught.value)
    assert "random.gauss" in message
    assert "random.random" in message
    assert "fill_probability" in message
    assert "319" in message and "753" in message and "369" in message


def test_every_line_the_refusal_cites_says_what_it_claims() -> None:
    """The cited line numbers are read against the real file, not trusted from the spec.

    A citation that has drifted by one line is worse than none: it sends a reader to a line that
    does not support the claim and quietly undermines the refusal it was there to justify. So the
    evidence is data (``FORBIDDEN_SIMULATOR_EVIDENCE``) and this test holds it against
    ``exchange_simulator.py`` as it is on disk.

    This reads the file as **text**. It does not import it - importing would run its line 49.
    """
    lines = EXCHANGE_SIMULATOR.read_text(encoding="utf-8").splitlines()
    for lineno, expected in sim.FORBIDDEN_SIMULATOR_EVIDENCE:
        assert 1 <= lineno <= len(lines), (
            f"exchange_simulator.py has {len(lines)} lines; the refusal cites {lineno}"
        )
        actual = lines[lineno - 1].strip()
        assert actual == expected, (
            f"exchange_simulator.py line {lineno} now reads {actual!r}, but the "
            f"PAPER_SIMULATOR_MISCONFIGURED refusal cites it as {expected!r}. Update "
            f"paper_simulator.FORBIDDEN_SIMULATOR_EVIDENCE and the docstring that quotes it - "
            f"never leave a refusal citing a line it has not checked."
        )


def test_the_forbidden_simulator_still_draws_from_random() -> None:
    """The premise of the whole guard, re-checked: that module does use ``random``.

    If ``exchange_simulator`` were ever rewritten to be deterministic, this test fails and says
    so - at which point the refusal needs re-justifying rather than silently guarding a module
    that no longer earns it. Requirement 13.8 would still confine it, but for a different reason,
    and the message this codebase prints would be wrong.
    """
    source = EXCHANGE_SIMULATOR.read_text(encoding="utf-8")
    assert re.search(r"^import random$", source, re.MULTILINE)
    assert "random.gauss(" in source
    assert "random.random()" in source


def test_the_permitted_simulator_is_admitted_and_its_identity_returned() -> None:
    """The guard is a refusal of one pair, not a refusal of everything."""
    assert sim.assert_paper_simulator(_PermittedSimulator) == (
        sim.SIMULATOR_MODULE,
        sim.SIMULATOR_QUALNAME,
    )


def test_an_instance_is_checked_on_the_class_it_is_an_instance_of() -> None:
    """A caller that resolved a simulator by constructing it is checked on what it constructed."""
    with pytest.raises(sim.PaperSimulatorMisconfigured):
        sim.assert_paper_simulator(_Forbidden())


def test_a_candidate_with_no_identity_is_refused_rather_than_admitted() -> None:
    """A candidate the identity comparison cannot read is refused.

    The guard *is* an identity comparison, so admitting something with no identity would make it
    decorative. ``object()`` has a ``__module__`` on its type but no ``__qualname__`` on the
    instance, and an object with both stripped has neither.
    """

    class _Nameless:
        pass

    _Nameless.__qualname__ = ""
    with pytest.raises(sim.PaperSimulatorMisconfigured) as caught:
        sim.assert_paper_simulator(_Nameless)
    assert caught.value.details["reason"] == "UNIDENTIFIABLE_SIMULATOR"


def test_the_misconfiguration_status_is_pinned_to_500() -> None:
    """No call site can answer anything but 500 for this code.

    ``PAPER_SIMULATOR_MISCONFIGURED`` is absent from ``ALLOWED_HTTP_STATUS_FOR_CODE``, so
    ``StructuredError.__init__`` refuses an override - the same pinning ``PAPER_READ_FAILED`` has,
    and the reason "this server is wired to a random-number generator" cannot be reported as a
    200 (Requirements 1.5, 1.7).
    """
    assert HTTP_STATUS_FOR_CODE[PAPER_SIMULATOR_MISCONFIGURED] == 500
    assert PAPER_SIMULATOR_MISCONFIGURED not in ALLOWED_HTTP_STATUS_FOR_CODE
    with pytest.raises(ValueError) as caught:
        PaperError(PAPER_SIMULATOR_MISCONFIGURED, http_status=200)
    assert "may only answer with [500]" in str(caught.value)


def test_the_audit_record_is_written_through_the_existing_facility() -> None:
    """Requirement 13.11's Audit_Log half, through ``core.audit_trail`` and no second facility."""
    recorded: List[Dict[str, Any]] = []

    class _Logger:
        async def log(self, action: Any, **kwargs: Any) -> None:
            recorded.append({"action": action, **kwargs})

    import backend_app.core.audit_trail as audit

    original = audit.get_strategy_audit_logger
    audit.get_strategy_audit_logger = lambda: _Logger()  # type: ignore[assignment]
    try:
        _run_coroutine(
            sim.audit_simulator_misconfigured(
                ("backend_app.backend.exchange_simulator", "PaperTradingExchange"),
                actor_id=USER,
                session_id=SESSION,
            )
        )
    finally:
        audit.get_strategy_audit_logger = original  # type: ignore[assignment]

    assert len(recorded) == 1
    entry = recorded[0]
    assert entry["action"] is audit.StrategyAuditAction.PAPER_SIMULATOR_MISCONFIGURED
    assert entry["actor_id"] == USER
    assert entry["resource_id"] == SESSION
    assert "random" in entry["reason"]
    assert entry["metadata"]["qualname"] == "PaperTradingExchange"


def test_an_audit_outage_does_not_replace_the_refusal_with_a_different_failure() -> None:
    """The record is best-effort; the refusal is not.

    A Redis outage must not turn "we refused to run on a coin flip" into an unexplained 500 from
    the audit path. So the coroutine swallows its own failure - after logging it - and the caller
    still has its :class:`PaperSimulatorMisconfigured` to raise.
    """

    class _Broken:
        async def log(self, action: Any, **kwargs: Any) -> None:
            raise RuntimeError("audit storage unavailable")

    import backend_app.core.audit_trail as audit

    original = audit.get_strategy_audit_logger
    audit.get_strategy_audit_logger = lambda: _Broken()  # type: ignore[assignment]
    try:
        assert (
            _run_coroutine(
                sim.audit_simulator_misconfigured(
                    ("backend_app.backend.exchange_simulator", "PaperTradingExchange"),
                    actor_id=USER,
                    session_id=SESSION,
                )
            )
            is None
        )
    finally:
        audit.get_strategy_audit_logger = original  # type: ignore[assignment]


def test_a_forbidden_simulator_cannot_reach_a_frozen_config() -> None:
    """The guard runs where it has to: before the configuration that records the simulator.

    Task 25.1 requires the guard "installed before anything calls a simulator". Freezing the
    config is the earliest point at which a resolved simulator is named, so that is where the
    assertion sits - a forbidden simulator is refused before any session row could carry it.
    """
    with pytest.raises(sim.PaperSimulatorMisconfigured):
        _config(simulator=_Forbidden)


def test_a_stored_config_naming_the_forbidden_simulator_is_refused_on_read_back() -> None:
    """The other door: a config assembled from a stored payload rather than from a class.

    A row written before this guard existed, or by hand, would name the forbidden simulator as a
    string that never passes through ``assert_paper_simulator``. So the same refusal is restated
    in ``SessionConfig.__post_init__``, which every read-back goes through.
    """
    payload = _config().to_jsonb()
    payload["simulator"] = "backend_app.backend.exchange_simulator.PaperTradingExchange"
    with pytest.raises(sim.PaperSimulatorMisconfigured):
        sim.session_config_from_jsonb(payload)


# ══════════════════════════════════════════════════════════════════════════
#  THE MARKET METADATA - REFUSE, NEVER DEFAULT (Req 16.5, 17.13, 28.3)
# ══════════════════════════════════════════════════════════════════════════


def test_the_three_figures_are_read_from_the_market_entry() -> None:
    """Requirement 16.5's precisions and maximum come from the venue, exactly."""
    metadata = _metadata()
    assert metadata.exchange_id == EXCHANGE
    assert metadata.symbol == SYMBOL
    assert metadata.market_type == "spot"
    assert metadata.price_precision == 2  # tick size 0.01
    assert metadata.quantity_precision == 8  # tick size 1e-8
    assert metadata.max_order_quantity == Decimal("1000")
    assert isinstance(metadata.max_order_quantity, Decimal)


def test_decimal_places_mode_is_read_as_a_count_when_the_caller_says_so() -> None:
    """A ``DECIMAL_PLACES``-mode venue reports counts, and is read as counts."""
    metadata = sim.resolve_market_metadata(
        _markets(precision={"price": 2, "amount": 6}),
        exchange_id=EXCHANGE,
        symbol=SYMBOL,
        precision_mode=sim.PRECISION_MODE_DECIMAL_PLACES,
    )
    assert (metadata.price_precision, metadata.quantity_precision) == (2, 6)


@pytest.mark.parametrize(
    "markets, expected_validation",
    [
        (None, sim.PaperMarketMetadataUnavailable.NO_MARKET_MAP),
        ({}, sim.PaperMarketMetadataUnavailable.NO_MARKET_MAP),
        ({"ETH/USDT": {}}, sim.PaperMarketMetadataUnavailable.SYMBOL_NOT_LISTED),
    ],
)
def test_an_unreadable_market_map_refuses_the_start(
    markets: Any, expected_validation: str
) -> None:
    """No market map, an empty one, or one that does not list the symbol: refuse.

    An empty map is a refusal and not "this venue lists no markets": a venue whose metadata did
    not load has stated nothing, and starting a session on nothing is what Requirement 28.3
    forbids. The code is ``PAPER_START_REFUSED`` and ``details["validation"]`` names the check,
    which is exactly Requirement 17.13's shape.
    """
    with pytest.raises(sim.PaperMarketMetadataUnavailable) as caught:
        sim.resolve_market_metadata(markets, exchange_id=EXCHANGE, symbol=SYMBOL)
    error = caught.value
    assert error.code == PAPER_START_REFUSED
    assert error.http_status == 409
    assert error.details["validation"] == expected_validation
    assert error.details["symbol"] == SYMBOL
    assert error.details["exchange_id"] == EXCHANGE


@pytest.mark.parametrize(
    "precision, limits, missing",
    [
        ({"amount": 0.00000001}, {"amount": {"max": 1000.0}}, "precision.price"),
        ({"price": 0.01}, {"amount": {"max": 1000.0}}, "precision.amount"),
        (
            {"price": 0.01, "amount": 0.00000001},
            {"amount": {"min": 0.0001}},
            "limits.amount.max",
        ),
        ({"price": None, "amount": None}, {"amount": {"max": 1000.0}}, "precision.price"),
        (
            {"price": 0.01, "amount": 0.00000001},
            {"amount": {"max": 0}},
            "limits.amount.max",
        ),
        (
            {"price": 0.01, "amount": 0.00000001},
            {"amount": {"max": -5}},
            "limits.amount.max",
        ),
    ],
)
def test_an_absent_or_unusable_figure_refuses_rather_than_defaulting(
    precision: Dict[str, Any], limits: Dict[str, Any], missing: str
) -> None:
    """Requirement 28.3: a figure the venue did not state is never invented.

    Each case names which figure was missing in ``details["missing"]``, so an operator reading the
    refusal knows whether to look at the venue or at the symbol. Nothing here falls back to the
    ``2`` / ``8`` of ``design.md``'s worked example: those are the values of one illustrative
    session, not defaults.
    """
    with pytest.raises(sim.PaperMarketMetadataUnavailable) as caught:
        sim.resolve_market_metadata(
            _markets(precision=precision, limits=limits),
            exchange_id=EXCHANGE,
            symbol=SYMBOL,
        )
    error = caught.value
    assert error.details["validation"] == (
        sim.PaperMarketMetadataUnavailable.FIGURE_NOT_STATED
    )
    assert error.details["missing"].startswith(missing)


def test_a_precision_of_one_is_refused_as_ambiguous_rather_than_guessed() -> None:
    """``1`` means one decimal place under one CCXT convention and none under the other.

    Guessing "one place" records a precision looser than the venue's, which admits an order the
    venue would reject and then prices fills against a market that does not exist. Guessing "no
    places" refuses orders the venue accepts. So neither is guessed: the caller passes
    ``precision_mode``, or the session does not start.
    """
    with pytest.raises(sim.PaperMarketMetadataUnavailable) as caught:
        sim.resolve_market_metadata(
            _markets(precision={"price": 1, "amount": 0.00000001}),
            exchange_id=EXCHANGE,
            symbol=SYMBOL,
        )
    assert "ambiguous" in caught.value.details["missing"]

    # ...and with the mode named, it is unambiguous and admitted, both ways round.
    as_places = sim.resolve_market_metadata(
        _markets(precision={"price": 1, "amount": 1}),
        exchange_id=EXCHANGE,
        symbol=SYMBOL,
        precision_mode=sim.PRECISION_MODE_DECIMAL_PLACES,
    )
    assert (as_places.price_precision, as_places.quantity_precision) == (1, 1)
    as_tick = sim.resolve_market_metadata(
        _markets(precision={"price": 1, "amount": 1}),
        exchange_id=EXCHANGE,
        symbol=SYMBOL,
        precision_mode=sim.PRECISION_MODE_TICK_SIZE,
    )
    assert (as_tick.price_precision, as_tick.quantity_precision) == (0, 0)


def test_a_market_type_outside_the_supported_set_refuses() -> None:
    """``asset_universe.SUPPORTED_MARKET_TYPES``, reused rather than re-listed."""
    with pytest.raises(sim.PaperMarketMetadataUnavailable) as caught:
        sim.resolve_market_metadata(
            _markets(type="option"), exchange_id=EXCHANGE, symbol=SYMBOL
        )
    assert caught.value.details["validation"] == (
        sim.PaperMarketMetadataUnavailable.MARKET_TYPE_UNSUPPORTED
    )


def test_a_precision_beyond_what_the_column_stores_is_refused() -> None:
    """``NUMERIC(28,10)`` keeps ten decimal places, so an eleventh could not be honoured.

    Recording a precision the storage silently rounds away would make the recorded precision a
    claim the database does not keep - the same class of untruth as a defaulted one.
    """
    with pytest.raises(sim.PaperMarketMetadataUnavailable):
        sim.resolve_market_metadata(
            _markets(precision={"price": 0.01, "amount": Decimal("1E-11")}),
            exchange_id=EXCHANGE,
            symbol=SYMBOL,
        )


def test_freeze_session_config_requires_the_metadata_and_will_not_default_it() -> None:
    """There is no call shape in which the three figures are guessed.

    ``metadata`` is a required keyword argument of a specific type, so a caller that has not read
    the venue's metadata cannot produce a configuration at all - which is the difference between
    "we refuse when it is missing" and "we refuse when someone remembers to check".
    """
    with pytest.raises(TypeError):
        sim.freeze_session_config(  # type: ignore[call-arg]
            currency="USD", market_data_source="mds.watch_ohlcv"
        )
    with pytest.raises(sim.InvalidSessionConfig):
        sim.freeze_session_config(
            metadata={"price_precision": 2},  # type: ignore[arg-type]
            currency="USD",
            market_data_source="mds.watch_ohlcv",
        )


# ══════════════════════════════════════════════════════════════════════════
#  THE FROZEN CONFIGURATION (Requirements 16.12, 17.5, 18.1, 18.2)
# ══════════════════════════════════════════════════════════════════════════


def test_the_config_carries_exactly_the_fifteen_keys_the_design_names() -> None:
    """``design.md``'s frozen config, key for key - no fewer and no extras."""
    payload = _config().to_jsonb()
    assert list(payload) == list(sim.CONFIG_KEYS)
    assert set(payload) == {
        "fee_rate",
        "slippage_rate",
        "participation_rate",
        "rounding_mode",
        "cost_basis",
        "price_precision",
        "quantity_precision",
        "minor_unit_exponent",
        "max_order_quantity",
        "supported_order_types",
        "supported_sides",
        "validated_symbols",
        "market_data_source",
        "simulator",
        "schema_version",
    }


def test_the_recorded_values_are_the_ones_the_design_prints() -> None:
    """The worked example of ``design.md``, reproduced from the code rather than restated."""
    payload = _config().to_jsonb()
    assert payload["fee_rate"] == "0.0010"
    assert payload["slippage_rate"] == "0.0005"
    assert payload["participation_rate"] == "0.10"
    assert payload["rounding_mode"] == "ROUND_HALF_EVEN"
    assert payload["cost_basis"] == "WEIGHTED_AVERAGE"
    assert payload["supported_order_types"] == ["market", "limit"]
    assert payload["supported_sides"] == ["buy", "sell"]
    assert payload["validated_symbols"] == [SYMBOL]
    assert payload["market_data_source"] == "mds.watch_ohlcv"
    assert payload["simulator"] == (
        "backend_app.backend.paper.paper_simulator.PaperSimulator"
    )
    assert payload["schema_version"] == "paper.v1"


def test_the_precisions_and_maximum_come_from_the_metadata_and_not_from_a_constant() -> None:
    """Change the venue's figures and the config changes with them."""
    config = _config(
        metadata=_metadata(
            precision={"price": 0.0001, "amount": 0.001},
            limits={"amount": {"max": 42.5}},
        )
    )
    assert config.price_precision == 4
    assert config.quantity_precision == 3
    assert config.max_order_quantity == Decimal("42.5")
    assert config.to_jsonb()["max_order_quantity"] == "42.5"


def test_the_minor_unit_exponent_is_read_from_the_persisted_currency_table() -> None:
    """``marketplace.money.minor_unit_exponent``, reused. An unsupported currency refuses."""
    assert _config(currency="USD").minor_unit_exponent == 2
    assert _config(currency="INR").minor_unit_exponent == 2
    with pytest.raises(sim.InvalidSessionConfig) as caught:
        _config(currency="ZZZ")
    assert "ZZZ" in str(caught.value)


def test_no_value_in_the_jsonb_is_a_float() -> None:
    """Requirement 18.1, at the storage boundary.

    A ``float`` in the payload is a value the session cannot read back exactly, so a fee rate
    stored as a JSON number is a rate that cannot reproduce the fills it priced. Asserted over the
    whole structure, nested lists included, rather than key by key.
    """
    def _walk(value: Any, where: str) -> None:
        if isinstance(value, float):
            raise AssertionError(f"{where} is a float: {value!r}")
        if isinstance(value, dict):
            for key, item in value.items():
                _walk(item, f"{where}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                _walk(item, f"{where}[{index}]")

    _walk(_config().to_jsonb(), "config")


def test_every_money_and_rate_survives_a_json_round_trip_exactly() -> None:
    """The reason they are strings: ``json`` cannot round-trip a decimal as a number.

    Serialised, parsed back, and compared as exact ``Decimal`` - which is the trip a stored
    ``JSONB`` value actually makes.
    """
    config = _config(
        fee_rate=Decimal("0.00075"),
        slippage_rate=Decimal("0.000125"),
        participation_rate=Decimal("0.075"),
        metadata=_metadata(limits={"amount": {"max": Decimal("123456.789")}}),
    )
    restored = sim.session_config_from_jsonb(json.loads(json.dumps(config.to_jsonb())))

    assert restored == config
    assert restored.fee_rate == Decimal("0.00075")
    assert restored.slippage_rate == Decimal("0.000125")
    assert restored.participation_rate == Decimal("0.075")
    assert restored.max_order_quantity == Decimal("123456.789")
    for key in sim.DECIMAL_CONFIG_KEYS:
        assert isinstance(config.to_jsonb()[key], str)


def test_a_stored_config_missing_a_key_is_refused_rather_than_completed() -> None:
    """Requirement 16.12: read as written, or not at all."""
    for key in sim.CONFIG_KEYS:
        payload = _config().to_jsonb()
        del payload[key]
        with pytest.raises(sim.InvalidSessionConfig) as caught:
            sim.session_config_from_jsonb(payload)
        assert key in str(caught.value)


def test_the_config_object_cannot_be_edited_in_place() -> None:
    """A frozen dataclass, because the column is frozen (Requirement 16.12)."""
    config = _config()
    with pytest.raises(Exception):
        config.fee_rate = Decimal("0.5")  # type: ignore[misc]


def test_a_rate_outside_zero_to_one_is_refused() -> None:
    """A "rate" above 1 is not a rate; a negative one is not either."""
    for bad in (Decimal("-0.01"), Decimal("1.5")):
        with pytest.raises(sim.InvalidSessionConfig):
            _config(fee_rate=bad)
        with pytest.raises(sim.InvalidSessionConfig):
            _config(slippage_rate=bad)
        with pytest.raises(sim.InvalidSessionConfig):
            _config(participation_rate=bad)


def test_a_float_rate_is_refused_at_the_boundary() -> None:
    """``paper_accounting.to_decimal`` reused, so a ``float`` never becomes a recorded rate."""
    with pytest.raises(sim.InvalidSessionConfig):
        _config(fee_rate=0.001)


def test_an_unsupported_rounding_mode_or_cost_basis_is_refused() -> None:
    """Requirements 18.2 and 18.8: the recorded convention must be an applicable one."""
    with pytest.raises(sim.InvalidSessionConfig):
        _config(rounding_mode="ROUND_TOWARDS_PROFIT")
    with pytest.raises(sim.InvalidSessionConfig):
        _config(cost_basis="FIFO")


def test_the_supported_vocabularies_are_the_check_constraints() -> None:
    """A session cannot record a type or side the column would refuse."""
    assert sim.SUPPORTED_ORDER_TYPES == tuple(repo.ORDER_TYPES) == ("market", "limit")
    assert sim.SUPPORTED_SIDES == tuple(repo.ORDER_SIDES) == ("buy", "sell")
    payload = _config().to_jsonb()
    payload["supported_order_types"] = ["market", "stop_loss"]
    with pytest.raises(sim.InvalidSessionConfig):
        sim.session_config_from_jsonb(payload)


def test_validated_symbols_must_contain_the_symbol_the_precisions_came_from() -> None:
    """Otherwise Requirement 16.5's checks compare against another market's figures."""
    assert _config(validated_symbols=[SYMBOL]).validated_symbols == (SYMBOL,)
    with pytest.raises(sim.InvalidSessionConfig):
        _config(validated_symbols=["ETH/USDT"])
    with pytest.raises(sim.InvalidSessionConfig):
        _config(validated_symbols=[])


def test_the_accounting_engine_reads_this_configuration_unchanged() -> None:
    """One configuration, two readers, no second copy of a rate.

    ``AccountingConfig.from_session_config`` reads the stored JSONB and
    ``SessionConfig.accounting`` builds the same object from the value in memory; both must agree,
    or the simulator and the accounting engine would be applying different fees to one session.
    """
    config = _config()
    from_object = config.accounting()
    from_storage = AccountingConfig.from_session_config(config.to_jsonb())
    assert from_object == from_storage
    assert from_object.rounding_mode == DEFAULT_ROUNDING_MODE
    assert from_object.cost_basis == WEIGHTED_AVERAGE
    assert from_object.money_quantum == Decimal("0.01")
    assert from_object.qty_quantum == Decimal("0.00000001")
    # No recorded market type, so no margin figure - Requirement 18.12 for a spot session, and a
    # stated gap for a margined one (see paper_simulator's module docstring).
    assert from_object.supports_margin is False


# ══════════════════════════════════════════════════════════════════════════
#  IMMUTABILITY - THE THREE THINGS THAT HOLD IT (Requirement 16.12)
# ══════════════════════════════════════════════════════════════════════════


def _session_row(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "id": SESSION,
        "user_id": USER,
        "listing_id": None,
        "source_strategy_id": SESSION,
        "version_id": SESSION,
        "exchange_id": EXCHANGE,
        "symbol": SYMBOL,
        "timeframe": "1m",
        "initial_capital_minor": 10_000_000,
        "currency": "USD",
        "config": config if config is not None else _config().to_jsonb(),
        "market_data_source": "mds.watch_ohlcv",
    }


def _client(**kwargs: Any) -> FakeSupabase:
    repo.reset_persistence_probe()
    client = FakeSupabase(accounts=[{"user_id": USER, "currency": "USD"}], **kwargs)
    return client


def test_the_config_is_read_back_through_the_repository_scoped_to_its_owner() -> None:
    """Requirements 21.2, 21.5: ``user_id`` is a predicate, not a post-filter."""
    client = _client(sessions=[_session_row()])

    row = repo.read_session_config(client, USER, SESSION)
    assert row is not None
    assert sim.session_config_from_jsonb(row["config"]) == _config()
    assert row["currency"] == "USD"

    statement = client.statements[-1]
    assert statement.filtered_columns() == {"user_id", "id"}
    assert statement.filter_value("user_id") == USER

    # Another user's session is never fetched, not merely never returned.
    assert repo.read_session_config(client, OTHER_USER, SESSION) is None


def test_the_repository_exposes_no_way_to_update_the_config() -> None:
    """The second of the three guards: there is no writer to misuse.

    ``update_session_feed`` is the only UPDATE against ``paper_sessions`` in the backend, and its
    signature admits three named feed columns. An argument it does not have is not a payload key
    it can set - which is asserted here on the signature rather than on a docstring.
    """
    parameters = set(inspect.signature(repo.update_session_feed).parameters)
    assert parameters == {
        "supabase",
        "user_id",
        "session_id",
        "feed_state",
        "feed_transport",
        "market_data_source",
    }
    assert not any(
        "config" in name.lower()
        for name, value in vars(repo).items()
        if callable(value) and name.startswith("update")
    )

    client = _client(sessions=[_session_row()])
    repo.update_session_feed(
        client, user_id=USER, session_id=SESSION, feed_state="HEALTHY"
    )
    written = client.statements[-1].payload or {}
    assert set(written) == {"feed_state", "updated_at"}
    assert "config" not in written


def test_a_direct_update_that_changes_the_config_is_refused_by_the_database() -> None:
    """The third and decisive guard: ``trg_paper_session_config_immutable``.

    Asserted against the double that models it, because the guarantee has to hold for a writer
    that does not exist yet as much as for the ones that do. A mid-session fee change is
    unrepresentable, not discouraged.
    """
    client = _client(sessions=[_session_row()])
    changed = _config(fee_rate=Decimal("0")).to_jsonb()

    with pytest.raises(FakeCheckViolation) as caught:
        client.table(repo.SESSIONS_TABLE).update({"config": changed}).eq(
            "id", SESSION
        ).eq("user_id", USER).execute()

    assert caught.value.pgcode == "23514"
    assert "frozen at start" in str(caught.value)
    # Nothing moved.
    assert client.sessions[0]["config"]["fee_rate"] == "0.0010"


def test_an_update_setting_the_config_to_what_it_already_is_is_permitted() -> None:
    """``IS DISTINCT FROM``, reproduced faithfully rather than made stricter.

    The real trigger compares values, so a no-op write passes. A double that refused it would
    assert a rule the database does not have, and the next reader would believe it.
    """
    client = _client(sessions=[_session_row()])
    same = _config().to_jsonb()
    client.table(repo.SESSIONS_TABLE).update({"config": same}).eq("id", SESSION).eq(
        "user_id", USER
    ).execute()
    assert client.sessions[0]["config"] == same


def test_the_migration_declares_the_trigger_that_makes_this_true() -> None:
    """009 section 14d, read rather than assumed.

    Task 25.2 said to verify ``trg_paper_session_config_immutable`` and to add a migration 015
    only if it did not cover an ``UPDATE`` that changes ``config``. It does, so no migration was
    added: the trigger is ``BEFORE UPDATE ... FOR EACH ROW`` on ``public.paper_sessions``, its
    function compares ``NEW.config IS DISTINCT FROM OLD.config``, and it raises with SQLSTATE
    ``23514``. Each of those four facts is asserted, because dropping any one of them would leave
    a trigger that exists and does not protect anything: a ``BEFORE INSERT`` timing would never
    fire on an update, a ``FOR EACH STATEMENT`` trigger has no ``OLD`` row to compare, and a
    ``RETURN NEW`` with no comparison is a no-op.
    """
    sql = MIGRATION_009.read_text(encoding="utf-8")

    assert "CREATE OR REPLACE FUNCTION public.paper_session_config_immutable()" in sql
    assert "NEW.config IS DISTINCT FROM OLD.config" in sql
    assert "USING ERRCODE = '23514'" in sql
    assert re.search(
        r"CREATE TRIGGER trg_paper_session_config_immutable\s+"
        r"BEFORE UPDATE ON public\.paper_sessions\s+"
        r"FOR EACH ROW EXECUTE FUNCTION public\.paper_session_config_immutable\(\)",
        sql,
    ), (
        "009 no longer declares trg_paper_session_config_immutable as a BEFORE UPDATE FOR EACH "
        "ROW trigger on public.paper_sessions. Requirement 16.12's freeze rests on exactly that "
        "declaration; a migration 015 restoring it is required before this suite is trusted."
    )
    assert re.search(r"config\s+JSONB NOT NULL", sql), (
        "paper_sessions.config must stay JSONB NOT NULL: a NULL config would be a session with "
        "no recorded fee, slippage or precision, and jsonb is what IS DISTINCT FROM compares"
    )


def test_the_config_payload_is_serialised_through_the_repository() -> None:
    """The one write path, and the ``float`` refusal that goes with it.

    ``session_config_payload`` routes through ``paper_repository._jsonb``, which is where a
    ``float`` is refused and a ``Decimal`` becomes its exact decimal string. So the session INSERT
    of task 26.x cannot write a payload that fails to round-trip, whatever it assembles.
    """
    config = _config()
    assert repo.session_config_payload(config) == config.to_jsonb()
    assert repo.session_config_payload(config.to_jsonb()) == config.to_jsonb()

    assert repo.session_config_payload({"fee_rate": Decimal("0.001")}) == {
        "fee_rate": "0.001"
    }
    with pytest.raises(ValueError) as caught:
        repo.session_config_payload({"fee_rate": 0.001})
    assert "float" in str(caught.value)

    for empty in ({}, None, "not a mapping"):
        with pytest.raises(ValueError):
            repo.session_config_payload(empty)


def test_a_broken_config_read_is_never_reported_as_an_absent_config() -> None:
    """Requirement 28.3 at the read boundary.

    A statement that did not complete raises, so a caller cannot mistake it for "this session has
    no configuration" and go on to apply a zero fee rate to recorded fills.
    """
    client = _client(sessions=[_session_row()], raise_on={("select", repo.SESSIONS_TABLE)})
    with pytest.raises(repo.PaperPersistenceError):
        repo.read_session_config(client, USER, SESSION)


def test_an_unapplied_migration_refuses_the_config_read() -> None:
    """The same 503 every other entry point in the repository answers, naming 009."""
    repo.reset_persistence_probe()
    client = FakeSupabase(missing_tables=True)
    with pytest.raises(Exception) as caught:
        repo.read_session_config(client, USER, SESSION)
    assert getattr(caught.value, "code", None) == "PAPER_PERSISTENCE_UNAVAILABLE"
    assert caught.value.details["migration"] == "009_paper_trading.sql"
    repo.reset_persistence_probe()


def test_the_new_projection_names_every_column_it_reads() -> None:
    """``SESSION_CONFIG_SELECT`` is a module-level literal, and its columns are manifest-listed.

    ``tests/test_marketplace_paper_schema_contract.py`` resolves ``.select(NAME)`` by looking the
    name up among module-level string assignments and holds every named column against
    ``paper/__init__.py::COLUMN_CONTRACT``. Restated here so a projection added without its
    manifest entry fails in the suite that owns the projection, not only in the schema suite.
    """
    from backend_app.backend.paper import COLUMN_CONTRACT

    manifest = COLUMN_CONTRACT["paper_repository"]["tables"]["paper_sessions"]
    for column in repo.SESSION_CONFIG_SELECT.split(","):
        assert column in manifest, f"{column} is read but not in the paper_sessions manifest"
    assert {"config", "currency"} <= set(manifest)
