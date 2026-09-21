"""
tests/test_task_27_session_service.py - the Requirement 17.9 pipeline and the session machine.

Spec: marketplace-subscriptions-paper-trading tasks 27.1, 27.2, 27.3 and 27.4. Requirements 17.2,
17.4, 17.5, 17.6, 17.7, 17.8, 17.9, 17.13, 17.14, 17.15, 19.12, 21.4, 27.4.

WHAT THIS MODULE ASSERTS, AND WHAT IT DOES NOT
----------------------------------------------
Four things, and every one of them is an ORDERING rather than a computation:

1. **Nothing is created until every validation has passed** (task 27.1). One case per validation
   the pipeline runs, each asserting the named refusal AND that the Persistence_Layer double
   recorded no ``insert`` or ``update`` at all and that nothing was published to ``mds:commands``.
   ``FakeSupabase.wrote_anything()`` is the oracle, so "nothing was created" is a statement about
   statements the code did not issue rather than about rows a test happened not to look for.
2. **The four operations move the session only along 009's six edges** (task 27.3), each recorded
   in ``paper_events`` with the requesting user and the resulting state, and each illegal request
   answered 409 naming BOTH the current state and the rejected operation with nothing written.
3. **A stop commits, then releases, then reports** (task 27.4, Requirement 17.8). The two finals
   INSERTs, the ``paper_events`` record, the ``mds:commands`` unsubscribe, the local unsubscribe and
   the Paper_Channel release are recorded in ONE ordered log and asserted as that sequence - and
   ``StopOutcome.complete`` is asserted ``False`` for each release that did not happen, so "both
   happened" and "they happened in this order" are two separate assertions.
4. **A reset restores and deletes nothing** (task 27.4, Requirement 17.15). Balances back to the
   exact recorded ``initial_capital_minor``, every open order ``CANCELLED``, every open position at
   size zero, a new series at ``previous + 1`` - and every pre-reset row still selectable.

``tests/test_paper_session_pipeline.py`` is task **27.6**'s module and is deliberately not written
here.

THE DOUBLES
-----------
``tests/test_paper_repository.FakeSupabase`` - the one Persistence_Layer double this repository
has - which gained ``library_strategies`` and ``strategy_versions`` for this task rather than being
duplicated. ``RecordingRedis``, ``_floor_clean``, ``_admitting`` and ``_mock_served_exchange`` come
from ``tests/test_paper_market_feed_selection.py`` for the same reason: two accounts of what the
feed publishes would be two accounts.

Coroutines are driven with ``tests/test_paper_order_lifecycle_writes._run_coroutine`` - the
process's ONE event loop. Never ``asyncio.run``: a loop per call exhausted the machine's ephemeral
port range and hung this suite, which is why that module owns the loop and every paper test borrows
it.

RED RUNS
--------
Recorded per class, so a reader can tell a test that constrains the code from a test that merely
describes it. Each was taken by removing exactly the mechanism the class is about and re-running
this file.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import inspect
import json
import textwrap
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import pytest

from backend_app.backend.paper import paper_accounting as accounting
from backend_app.backend.paper import paper_channel
from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper import paper_session_service as service
from backend_app.backend.paper import paper_simulator as sim
from backend_app.backend.paper.errors import PaperError
from backend_app.backend.paper import paper_market_feed as feed
from backend_app.backend.paper.paper_order_state import PaperOrderState
from tests.test_paper_market_feed_selection import (
    EXCHANGE,
    PROCESSED_AT,
    SYMBOL,
    TIMEFRAME,
    RecordingRedis,
    _admitting,
    _floor_clean,
    _frame,
    _mock_served_exchange,
)
from tests.test_paper_order_lifecycle_writes import _run_coroutine
from tests.test_paper_repository import FakeSupabase

USER = "11111111-1111-1111-1111-111111111111"
OTHER_USER = "99999999-9999-9999-9999-999999999999"
LISTING = "33333333-3333-3333-3333-333333333333"
STRATEGY = "44444444-4444-4444-4444-444444444444"
VERSION = "55555555-5555-5555-5555-555555555555"
NOW = datetime(2024, 5, 1, 12, 0, 0, tzinfo=timezone.utc)

#: One hundred thousand US dollars, in Minor_Units. An exact integer, because that is what the
#: column stores and what Requirement 17.5 measures precision against.
CAPITAL_MINOR = 10_000_000


class _Caller:
    """The authenticated server-side identity, as the resolver and the service read it."""

    def __init__(self, user_id: str = USER) -> None:
        self.id = user_id


def _market_entry() -> Dict[str, Any]:
    """One CCXT market entry in ``connection_engine``'s shape, precision as a tick size."""
    return {
        "symbol": SYMBOL,
        "base": "BTC",
        "quote": "USDT",
        "type": "spot",
        "active": True,
        "precision": {"price": 0.01, "amount": 0.00000001},
        "limits": {"amount": {"min": 0.0001, "max": 1000.0}},
    }


def _markets() -> Dict[str, Any]:
    return {SYMBOL: _market_entry()}


def _compiled_plan(
    symbols: Optional[Dict[str, str]] = None,
    timeframes: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """A ``strategy_versions.compiled_plan`` payload carrying the two resolved market maps.

    Only the two members :func:`paper_session_service.plan_markets` reads. The rest of a real plan
    - levels, dependencies, warmups - is deliberately absent: the pipeline reads these two and
    nothing else, and a fixture carrying more would suggest it reads more.
    """
    return {
        "compiler_version": "2.1.0",
        "action_symbols": dict(
            symbols if symbols is not None else {"action-1": SYMBOL}
        ),
        "action_timeframes": dict(
            timeframes if timeframes is not None else {"action-1": TIMEFRAME}
        ),
    }


def _listing_row(
    *,
    author_id: str = USER,
    submission_state: str = "PUBLISHED",
    subscriptions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """One ``library_strategies`` row with its two embedded resources, as PostgREST returns them.

    The caller is the AUTHOR by default, so the admission decision is ``OWNED`` and no
    subscription plumbing participates. That keeps every case below about the validation it names:
    the subscription paths are ``tests/test_entitlement_resolver.py``'s and are not re-proved here.
    """
    return {
        "id": LISTING,
        "author_id": author_id,
        "source_strategy_id": STRATEGY,
        "source_cloning_enabled": False,
        "marketplace_submissions": [{"submission_state": submission_state}],
        "library_subscriptions": list(subscriptions or []),
    }


def _version_row(
    *,
    lifecycle_state: str = "READY",
    is_draft: bool = False,
    compiled_plan: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "id": VERSION,
        "strategy_id": STRATEGY,
        "version": 1,
        "is_draft": is_draft,
        "lifecycle_state": lifecycle_state,
        "compiled_plan": _compiled_plan() if compiled_plan is None else compiled_plan,
    }


def _double(**overrides: Any) -> FakeSupabase:
    """A Persistence_Layer double carrying the listing and the version, and nothing else.

    No ``paper_sessions`` row, no ``paper_accounts`` row and no snapshot: the whole point of the
    refusal cases is that the pipeline creates none of the three, and a seeded one would make
    ``wrote_anything()`` say nothing about it.
    """
    repo.reset_persistence_probe()
    kwargs: Dict[str, Any] = {
        "library_strategies": [_listing_row()],
        "strategy_versions": [_version_row()],
    }
    kwargs.update(overrides)
    return FakeSupabase(**kwargs)


def _start(
    supabase: FakeSupabase,
    *,
    redis: Optional[RecordingRedis] = None,
    caller: Optional[_Caller] = None,
    **overrides: Any,
) -> Any:
    """Drive :func:`paper_session_service.start_session` with everything injected.

    ``markets`` is injected rather than loaded, ``exchange`` is ``None`` (a legitimate answer -
    Requirement 15.3 lets a session start for a user holding no exchange credentials, and ``None``
    is not mocked), and ``measurements`` are the floor-clean pair so the source selection is a
    ``SELECTED`` rather than an accident.
    """
    kwargs: Dict[str, Any] = {
        "listing_id": LISTING,
        "exchange_id": EXCHANGE,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "initial_capital_minor": CAPITAL_MINOR,
        "currency": "USD",
        "now": NOW,
        "markets": _markets(),
        "exchange": None,
        "redis": redis if redis is not None else RecordingRedis(),
        "measurements": _admitting(),
        "spawn_loop": None,
    }
    kwargs.update(overrides)
    return _run_coroutine(
        service.start_session(supabase, caller or _Caller(), **kwargs)
    )


def _assert_created_nothing(supabase: FakeSupabase, redis: RecordingRedis) -> None:
    """The whole of Requirement 17.13, as one assertion pair.

    ``wrote_anything()`` covers every ``insert`` and every ``update`` on every table the double
    holds, so it says "no Paper_Session, no Paper_Account, no equity snapshot, no order, no balance
    and no event" in one place rather than as six absences that a seventh table could slip past.
    ``redis.log`` is the market-data subscription half: ``open_feed`` publishes to ``mds:commands``
    and calls ``pubsub()``, and an empty log is both of those not having happened.
    """
    offenders = [
        (statement.op, statement.table_name)
        for statement in supabase.statements
        if statement.op in ("insert", "update")
    ]
    assert offenders == [], (
        f"the refusal wrote {offenders}; Requirement 17.13 requires a refused start to create no "
        f"Paper_Session, no paper order and no paper balance, and to leave every existing "
        f"Paper_Account unchanged"
    )
    assert redis.log == [], (
        f"the refusal touched the market-data transport ({redis.log}); Requirement 17.13 requires "
        f"no market-data subscription"
    )


# ══════════════════════════════════════════════════════════════════════════
#  1. THE MACHINE ITSELF (task 27.3 - Requirement 17.7)
# ══════════════════════════════════════════════════════════════════════════


class TestTheMachine:
    """The five operations, the six edges, and their agreement with the column vocabulary.

    Red run: ``OPERATION_TRANSITIONS[OPERATION_STOP]`` was reduced to ``{RUNNING: STOPPED}`` -
    dropping the ``PAUSED -> STOPPED`` edge, which is the one edge a five-operation reading of
    Requirement 17.7 most easily loses. ``_assert_machine_agrees`` raised at IMPORT, so every test
    in this file failed at collection, which is the loudest possible answer and the intended one.
    """

    def test_the_four_states_are_the_columns_four(self) -> None:
        assert service.SESSION_STATES == repo.SESSION_STATES == (
            "CREATED",
            "RUNNING",
            "PAUSED",
            "STOPPED",
        )

    def test_the_five_operations_are_requirement_17_7s_five(self) -> None:
        assert service.SESSION_OPERATIONS == (
            "start",
            "pause",
            "resume",
            "stop",
            "reset",
        )

    def test_the_machine_flattens_to_009s_six_pairs(self) -> None:
        """The seed is parsed and compared in ``tests/test_submission_state_agreement.py``.

        Here the six pairs are transcribed so this file fails on its own if an edge is added or
        lost, without depending on that module being run.
        """
        assert service._flatten(service.PAPER_SESSION_TRANSITIONS) == frozenset(
            {
                ("CREATED", "RUNNING"),
                ("RUNNING", "PAUSED"),
                ("PAUSED", "RUNNING"),
                ("RUNNING", "STOPPED"),
                ("PAUSED", "STOPPED"),
                ("STOPPED", "CREATED"),
            }
        )

    @pytest.mark.parametrize(
        "operation,state,expected",
        [
            ("start", "CREATED", "RUNNING"),
            ("pause", "RUNNING", "PAUSED"),
            ("resume", "PAUSED", "RUNNING"),
            ("stop", "RUNNING", "STOPPED"),
            ("stop", "PAUSED", "STOPPED"),
            ("reset", "STOPPED", "CREATED"),
        ],
    )
    def test_each_permitted_operation_names_its_resulting_state(
        self, operation: str, state: str, expected: str
    ) -> None:
        assert service.target_state(operation, state) == expected
        assert service.can_operate(operation, state) is True

    @pytest.mark.parametrize(
        "operation,state",
        [
            ("pause", "CREATED"),
            ("pause", "PAUSED"),
            ("pause", "STOPPED"),
            ("resume", "CREATED"),
            ("resume", "RUNNING"),
            ("resume", "STOPPED"),
            ("stop", "CREATED"),
            ("stop", "STOPPED"),
            ("reset", "CREATED"),
            ("reset", "RUNNING"),
            ("reset", "PAUSED"),
            ("start", "RUNNING"),
            ("start", "PAUSED"),
            ("start", "STOPPED"),
        ],
    )
    def test_every_other_pair_is_refused_by_the_gate(
        self, operation: str, state: str
    ) -> None:
        """Fourteen of the twenty (state, operation) pairs are not permitted. All fourteen."""
        assert service.can_operate(operation, state) is False
        assert service.target_state(operation, state) is None

    def test_an_unrecognised_state_or_operation_permits_nothing(self) -> None:
        """Not an exception and not a default: ``None``, which the caller turns into a 409.

        A machine that raised here would make an unrecognised stored ``session_state`` a 500, and a
        machine that defaulted would let one through.
        """
        assert service.normalise_state("ARCHIVED") is None
        assert service.normalise_operation("delete") is None
        assert service.can_operate("delete", "RUNNING") is False
        assert service.can_operate("stop", "ARCHIVED") is False

    def test_the_deployable_lifecycle_states_agree_with_the_platforms_vocabulary(
        self,
    ) -> None:
        """``DEPLOYABLE_LIFECYCLE_STATES`` is spelled in the paper package, so it is checked here.

        ``paper_session_service`` may not import ``strategy_lifecycle`` -
        ``tests/test_paper_no_random.py`` pins this package's first-party dependency list and that
        module reaches ``deployment_binding``, ``strategy_builder`` and ``core.audit_trail`` at
        module scope. A test has no such constraint, so the agreement is asserted here rather than
        assumed: every member is one of ``chk_lifecycle_state``'s values, ``READY`` is the state the
        live deploy gate asserts, and the three states a version reaches AFTER that gate are the
        other three.

        Red run: ``"SAVED"`` was added to the tuple - a version that has not been through the
        READY gate. This failed; nothing else did.
        """
        from backend_app.backend import strategy_lifecycle as lifecycle
        from backend_app.backend.strategy_builder import LIFECYCLE_STATES

        assert set(service.DEPLOYABLE_LIFECYCLE_STATES) <= set(LIFECYCLE_STATES)
        assert set(service.DEPLOYABLE_LIFECYCLE_STATES) == {
            lifecycle.LIFECYCLE_READY,
            lifecycle.LIFECYCLE_DEPLOYED,
            lifecycle.LIFECYCLE_RUNNING,
            lifecycle.LIFECYCLE_PAUSED,
        }
        # The states a paper session must NOT run against, named so the exclusion is explicit.
        for excluded in ("DRAFT", "VALIDATED", "SAVED", "TRAINING", "TRAINED", "STOPPED", "ARCHIVED"):
            assert excluded not in service.DEPLOYABLE_LIFECYCLE_STATES


# ══════════════════════════════════════════════════════════════════════════
#  2. THE CONFIGURED LIMITS AND THEIR RANGES
# ══════════════════════════════════════════════════════════════════════════


class TestTheConfiguredLimits:
    """Requirement 27.4's cap and Requirement 17.5's maximum, with their stated ranges.

    Red run: ``resolve_concurrency_limit`` was reduced to ``return value or 3`` - the range check
    removed. The two out-of-range cases failed.
    """

    def test_the_concurrency_cap_defaults_to_three(self) -> None:
        assert service.MAX_CONCURRENT_PAPER_SESSIONS_PER_USER == 3
        assert service.resolve_concurrency_limit(None) == 3

    @pytest.mark.parametrize("value", [1, 3, 20])
    def test_the_cap_is_configurable_across_its_whole_range(self, value: int) -> None:
        assert service.resolve_concurrency_limit(value) == value

    @pytest.mark.parametrize("value", [0, -1, 21, 100])
    def test_a_cap_outside_one_to_twenty_is_refused(self, value: int) -> None:
        with pytest.raises(ValueError) as caught:
            service.resolve_concurrency_limit(value)
        assert "Requirement 27.4" in str(caught.value)

    def test_the_capital_maximum_defaults_to_a_million_major_units(self) -> None:
        assert service.MAX_SESSION_CAPITAL_MAJOR_UNITS == Decimal("1000000")
        assert service.resolve_capital_maximum_major(None) == Decimal("1000000")

    @pytest.mark.parametrize("value", ["1", "1000000", "1000000000"])
    def test_the_capital_maximum_is_configurable_across_its_whole_range(
        self, value: str
    ) -> None:
        assert service.resolve_capital_maximum_major(value) == Decimal(value)

    @pytest.mark.parametrize("value", ["0", "0.5", "1000000001"])
    def test_a_capital_maximum_outside_requirement_17_5s_range_is_refused(
        self, value: str
    ) -> None:
        with pytest.raises(ValueError) as caught:
            service.resolve_capital_maximum_major(value)
        assert "Requirement 17.5" in str(caught.value)

    def test_a_float_capital_maximum_is_refused_outright(self) -> None:
        """Requirement 18.1: the comparison is exact, so the bound cannot be a binary float."""
        with pytest.raises(ValueError) as caught:
            service.resolve_capital_maximum_major(1000000.0)
        assert "float" in str(caught.value)


# ══════════════════════════════════════════════════════════════════════════
#  3. THE CAPITAL VALIDATION AS A PURE FUNCTION (Requirement 17.5)
# ══════════════════════════════════════════════════════════════════════════


class TestValidateCapital:
    """The four refusals of Requirement 17.5, each named separately.

    Red run: the ``amount != amount.to_integral_value()`` check was removed.
    ``test_a_fraction_of_a_minor_unit_is_refused`` failed and the accepted-value case silently
    truncated ``100.5`` cents to ``100`` - which is the exact failure the check exists to prevent.
    """

    def test_an_exact_amount_is_accepted_in_both_units(self) -> None:
        accepted = service.validate_capital(CAPITAL_MINOR, "usd")
        assert accepted.minor == CAPITAL_MINOR
        assert accepted.major == Decimal("100000")
        assert accepted.exponent == 2
        assert accepted.currency == "USD"

    @pytest.mark.parametrize("amount", [0, -1, -10_000])
    def test_a_non_positive_capital_is_refused(self, amount: int) -> None:
        with pytest.raises(service.PaperStartRefused) as caught:
            service.validate_capital(amount, "USD")
        assert caught.value.validation == service.VALIDATION_CAPITAL_NOT_POSITIVE
        assert caught.value.http_status == 422

    def test_a_capital_above_the_maximum_is_refused(self) -> None:
        with pytest.raises(service.PaperStartRefused) as caught:
            service.validate_capital(100_000_000_001, "USD")
        assert caught.value.validation == service.VALIDATION_CAPITAL_EXCEEDS_MAXIMUM
        assert caught.value.details["maximum_major_units"] == "1000000"

    def test_the_maximum_moves_with_the_configured_one(self) -> None:
        """A figure refused at the default is accepted at a configured higher maximum."""
        with pytest.raises(service.PaperStartRefused):
            service.validate_capital(200_000_000, "USD")
        accepted = service.validate_capital(
            200_000_000, "USD", maximum_major="5000000"
        )
        assert accepted.major == Decimal("2000000")

    @pytest.mark.parametrize("amount", [Decimal("100.5"), Decimal("1.000001")])
    def test_a_fraction_of_a_minor_unit_is_refused(self, amount: Decimal) -> None:
        """Requirement 17.5's precision clause, against the unit the column stores.

        A hundred and a half cents is not a rounding matter; it is an amount no ledger holds.
        """
        with pytest.raises(service.PaperStartRefused) as caught:
            service.validate_capital(amount, "USD")
        assert caught.value.validation == service.VALIDATION_CAPITAL_PRECISION

    def test_a_float_capital_is_refused_before_it_is_coerced(self) -> None:
        with pytest.raises(service.PaperStartRefused) as caught:
            service.validate_capital(10_000_000.0, "USD")
        assert caught.value.validation == service.VALIDATION_CAPITAL_PRECISION

    def test_an_unsupported_currency_is_refused_rather_than_assumed_to_have_two_places(
        self,
    ) -> None:
        with pytest.raises(service.PaperStartRefused) as caught:
            service.validate_capital(CAPITAL_MINOR, "XYZ")
        assert caught.value.validation == service.VALIDATION_CURRENCY_NOT_SUPPORTED

    def test_the_exponent_is_read_per_currency_rather_than_assumed(self) -> None:
        """Requirement 18.2: the exponent comes from the persisted table, for whichever currency.

        ``marketplace.money`` holds two today (``USD`` and ``INR``), both at 2. What is asserted is
        that the exponent used is the one the TABLE states for the currency asked about - so a
        third currency at a different exponent is converted correctly the day it is added, and a
        hardcoded ``2`` here would be found by this test rather than by a wrong balance.
        """
        from backend_app.backend.marketplace.money import minor_unit_exponent

        for currency in ("USD", "INR"):
            expected = minor_unit_exponent(currency)
            accepted = service.validate_capital(CAPITAL_MINOR, currency)
            assert accepted.exponent == expected
            assert accepted.major == Decimal(CAPITAL_MINOR).scaleb(-expected)


# ══════════════════════════════════════════════════════════════════════════
#  4. THE TIMEFRAME AND THE PLAN COMPATIBILITY CHECKS
# ══════════════════════════════════════════════════════════════════════════


class TestTimeframeAndPlanChecks:
    """Two validations of Requirement 17.4, as pure functions.

    Red run: ``validate_timeframe`` was reduced to ``return str(timeframe).strip()``. Both
    unsupported-timeframe cases failed. ``assert_strategy_supports_market``'s symbol branch was
    removed; the two incompatibility cases failed while the admitting ones stayed green - which is
    the pair that shows the check is doing work rather than passing everything.
    """

    @pytest.mark.parametrize("label", ["1m", "5m", "1h"])
    def test_a_supported_timeframe_passes(self, label: str) -> None:
        assert service.validate_timeframe(label) == label

    @pytest.mark.parametrize("label", ["7s", "3mo", "", "  "])
    def test_an_unsupported_timeframe_is_refused_with_the_platforms_own_set(
        self, label: str
    ) -> None:
        with pytest.raises(service.PaperStartRefused) as caught:
            service.validate_timeframe(label)
        assert caught.value.validation == service.VALIDATION_TIMEFRAME_NOT_SUPPORTED
        assert caught.value.http_status == 422

    def test_the_plans_two_maps_are_read_off_the_stored_payload(self) -> None:
        symbols, timeframes = service.plan_markets(_version_row())
        assert symbols == frozenset({SYMBOL})
        assert timeframes == frozenset({TIMEFRAME})

    def test_a_plan_stored_as_json_text_is_read_too(self) -> None:
        """A driver that hands a JSONB column back as text must not read as "no markets"."""
        row = _version_row()
        row["compiled_plan"] = json.dumps(row["compiled_plan"])
        assert service.plan_markets(row) == (
            frozenset({SYMBOL}),
            frozenset({TIMEFRAME}),
        )

    def test_a_matching_symbol_and_timeframe_are_admitted(self) -> None:
        service.assert_strategy_supports_market(
            _version_row(), symbol=SYMBOL, timeframe=TIMEFRAME
        )

    def test_a_symbol_the_plan_does_not_trade_is_refused(self) -> None:
        with pytest.raises(service.PaperStartRefused) as caught:
            service.assert_strategy_supports_market(
                _version_row(), symbol="ETH/USDT", timeframe=TIMEFRAME
            )
        assert (
            caught.value.validation
            == service.VALIDATION_STRATEGY_MARKET_INCOMPATIBLE
        )

    def test_a_timeframe_the_plan_does_not_read_is_refused(self) -> None:
        with pytest.raises(service.PaperStartRefused) as caught:
            service.assert_strategy_supports_market(
                _version_row(), symbol=SYMBOL, timeframe="1h"
            )
        assert (
            caught.value.validation
            == service.VALIDATION_STRATEGY_MARKET_INCOMPATIBLE
        )

    def test_the_refusal_discloses_no_part_of_the_plan(self) -> None:
        """Requirements 19.7, 23.3: a refused subscriber learns nothing about the strategy."""
        with pytest.raises(service.PaperStartRefused) as caught:
            service.assert_strategy_supports_market(
                _version_row(), symbol="ETH/USDT", timeframe=TIMEFRAME
            )
        body = json.dumps(caught.value.details)
        assert SYMBOL not in body
        assert "action_symbols" not in body
        assert set(caught.value.details) == {"validation", "symbol", "timeframe"}

    def test_an_unresolved_map_admits_rather_than_inventing_an_answer(self) -> None:
        """A 2.0.0 plan resolved no symbols and a multi-timeframe action has no timeframe.

        Absence means UNKNOWN, which is ``resolve_action_markets``' own instruction to consumers.
        Refusing on it would reject strategies the platform supports; defaulting would invent the
        ``"1m"`` that whole requirement exists to delete.
        """
        row = _version_row(compiled_plan={"compiler_version": "2.0.0"})
        assert service.plan_markets(row) == (frozenset(), frozenset())
        service.assert_strategy_supports_market(
            row, symbol="ANY/THING", timeframe="1h"
        )


# ══════════════════════════════════════════════════════════════════════════
#  5. EVERY VALIDATION REFUSES BEFORE ANYTHING IS CREATED (Req 17.13)
# ══════════════════════════════════════════════════════════════════════════


class TestNothingIsCreatedUntilEveryValidationHasPassed:
    """One case per validation, each asserting the refusal AND that nothing was written.

    Red run 1 (the ordering itself): the ``paper_sessions`` INSERT was moved to immediately after
    the entitlement check, above the other seven validations - which is the single change that
    turns Requirement 17.13 from a guarantee into a coincidence. Every case in this class except
    the entitlement one failed on ``_assert_created_nothing``; every other test in this file stayed
    green, including the happy path. That is the point: no behavioural test notices a session
    created before a refusal, which is why these cases exist.

    Red run 2 (the cap): ``assert_within_session_limit`` was moved to AFTER the create block.
    ``test_the_concurrent_cap_refuses_with_429_and_creates_nothing`` failed on the same assertion
    while still seeing the 429 - so the case constrains the ordering and not merely the status.
    """

    def test_a_caller_who_is_not_entitled_is_refused_403(self) -> None:
        redis = RecordingRedis()
        supabase = _double(
            library_strategies=[_listing_row(author_id=OTHER_USER)],
            strategy_versions=[_version_row()],
        )

        with pytest.raises(service.PaperStartRefused) as caught:
            _start(supabase, redis=redis)

        assert caught.value.validation == service.VALIDATION_ENTITLEMENT
        assert caught.value.http_status == 403
        assert caught.value.details["reason"] == "NOT_SUBSCRIBED"
        _assert_created_nothing(supabase, redis)

    def test_the_forbidden_simulator_is_refused_before_any_create(self) -> None:
        """Requirement 13.11. A server wired to a random-number generator starts nothing.

        The candidate is a bare class carrying the forbidden identity as strings - the same shape
        ``assert_paper_simulator`` compares - so no part of ``exchange_simulator`` is imported and
        its ``import random`` on line 49 is not executed.
        """

        class PaperTradingExchange:
            # Both dunders are set explicitly: a class defined inside a function carries a
            # ``__qualname__`` ending in ``<locals>.PaperTradingExchange``, which is NOT the
            # identity ``FORBIDDEN_SIMULATORS`` holds - so a fixture that relied on the implicit
            # value would sail past the guard and prove nothing.
            __module__ = "backend_app.backend.exchange_simulator"
            __qualname__ = "PaperTradingExchange"

        assert (
            PaperTradingExchange.__module__,
            PaperTradingExchange.__qualname__,
        ) in sim.FORBIDDEN_SIMULATORS

        redis = RecordingRedis()
        supabase = _double()

        with pytest.raises(sim.PaperSimulatorMisconfigured):
            _start(supabase, redis=redis, simulator=PaperTradingExchange)

        _assert_created_nothing(supabase, redis)

    @pytest.mark.parametrize(
        "lifecycle_state", ["DRAFT", "VALIDATED", "SAVED", "TRAINING", "TRAINED", "ARCHIVED"]
    )
    def test_a_version_that_is_not_deployable_is_refused_before_any_create(
        self, lifecycle_state: str
    ) -> None:
        redis = RecordingRedis()
        supabase = _double(
            strategy_versions=[_version_row(lifecycle_state=lifecycle_state)]
        )

        with pytest.raises(service.PaperStartRefused) as caught:
            _start(supabase, redis=redis)

        assert (
            caught.value.validation == service.VALIDATION_STRATEGY_NOT_EXECUTABLE
        )
        _assert_created_nothing(supabase, redis)

    def test_a_strategy_whose_only_version_is_a_draft_is_refused_by_the_resolver(
        self,
    ) -> None:
        """The one non-deployable case the ADMISSION DECISION reaches first, and correctly.

        ``entitlement_resolver._resolve_current_version`` ignores drafts, so a strategy with no
        saved version does not resolve to a live one - which is Requirement 7.11's
        ``LISTING_UNAVAILABLE``, reported here as the entitlement refusal rather than as a
        lifecycle one. Asserted rather than left to look like a gap in the lifecycle check: the
        draft branch of ``assert_version_deployable`` is still exercised directly below, and it
        exists because ``version_id`` could name a draft in a database where the two reads
        disagree.
        """
        redis = RecordingRedis()
        supabase = _double(strategy_versions=[_version_row(is_draft=True)])

        with pytest.raises(service.PaperStartRefused) as caught:
            _start(supabase, redis=redis)

        assert caught.value.validation == service.VALIDATION_ENTITLEMENT
        assert caught.value.details["reason"] == "LISTING_UNAVAILABLE"
        _assert_created_nothing(supabase, redis)

    @pytest.mark.parametrize(
        "row",
        [
            None,
            {"id": VERSION, "is_draft": True, "lifecycle_state": "READY"},
            {"id": VERSION, "is_draft": False, "lifecycle_state": "STOPPED"},
            {"id": VERSION, "is_draft": False},
        ],
        ids=["no-row", "draft", "stopped", "no-lifecycle-column"],
    )
    def test_assert_version_deployable_refuses_each_non_executable_shape(
        self, row: Any
    ) -> None:
        """The four shapes, as one refusal, tested on the function rather than the pipeline.

        Telling a subscriber WHICH of the four would describe the author's work in progress, so all
        four answer ``STRATEGY_NOT_EXECUTABLE``.
        """
        with pytest.raises(service.PaperStartRefused) as caught:
            service.assert_version_deployable(row)
        assert caught.value.validation == service.VALIDATION_STRATEGY_NOT_EXECUTABLE

    def test_a_version_with_no_lifecycle_column_is_refused_rather_than_admitted(
        self,
    ) -> None:
        """Migration 004 part 1 unapplied. The safe direction, and the one the sibling takes."""
        row = _version_row()
        row.pop("lifecycle_state")
        redis = RecordingRedis()
        supabase = _double(strategy_versions=[row])

        with pytest.raises(service.PaperStartRefused) as caught:
            _start(supabase, redis=redis)

        assert caught.value.validation == service.VALIDATION_STRATEGY_NOT_EXECUTABLE
        _assert_created_nothing(supabase, redis)

    @pytest.mark.parametrize(
        "amount,expected",
        [
            (0, "CAPITAL_NOT_POSITIVE"),
            (-1, "CAPITAL_NOT_POSITIVE"),
            (100_000_000_001, "CAPITAL_EXCEEDS_MAXIMUM"),
            (Decimal("100.5"), "CAPITAL_PRECISION_EXCEEDED"),
        ],
    )
    def test_a_capital_refusal_creates_nothing(self, amount: Any, expected: str) -> None:
        redis = RecordingRedis()
        supabase = _double()

        with pytest.raises(service.PaperStartRefused) as caught:
            _start(supabase, redis=redis, initial_capital_minor=amount)

        assert caught.value.validation == expected
        _assert_created_nothing(supabase, redis)

    def test_an_unsupported_currency_refusal_creates_nothing(self) -> None:
        redis = RecordingRedis()
        supabase = _double()

        with pytest.raises(service.PaperStartRefused) as caught:
            _start(supabase, redis=redis, currency="XYZ")

        assert caught.value.validation == service.VALIDATION_CURRENCY_NOT_SUPPORTED
        _assert_created_nothing(supabase, redis)

    def test_a_symbol_the_venue_does_not_list_is_refused_before_any_create(self) -> None:
        """``paper_simulator``'s own refusal, reached through this pipeline.

        The validation name is that module's - ``SYMBOL_NOT_LISTED`` - and is deliberately not
        re-spelled in this one: one check, one vocabulary.
        """
        redis = RecordingRedis()
        supabase = _double()

        with pytest.raises(sim.PaperMarketMetadataUnavailable) as caught:
            _start(supabase, redis=redis, symbol="DOGE/USDT")

        assert (
            caught.value.validation
            == sim.PaperMarketMetadataUnavailable.SYMBOL_NOT_LISTED
        )
        _assert_created_nothing(supabase, redis)

    def test_an_empty_market_map_is_refused_rather_than_read_as_no_markets(self) -> None:
        redis = RecordingRedis()
        supabase = _double()

        with pytest.raises(sim.PaperMarketMetadataUnavailable) as caught:
            _start(supabase, redis=redis, markets={})

        assert (
            caught.value.validation
            == sim.PaperMarketMetadataUnavailable.NO_MARKET_MAP
        )
        _assert_created_nothing(supabase, redis)

    def test_an_unsupported_timeframe_is_refused_before_any_create(self) -> None:
        redis = RecordingRedis()
        supabase = _double()

        with pytest.raises(service.PaperStartRefused) as caught:
            _start(supabase, redis=redis, timeframe="7s")

        assert caught.value.validation == service.VALIDATION_TIMEFRAME_NOT_SUPPORTED
        _assert_created_nothing(supabase, redis)

    def test_an_incompatible_symbol_is_refused_before_any_create(self) -> None:
        redis = RecordingRedis()
        supabase = _double(
            strategy_versions=[
                _version_row(compiled_plan=_compiled_plan(symbols={"a": "ETH/USDT"}))
            ]
        )

        with pytest.raises(service.PaperStartRefused) as caught:
            _start(supabase, redis=redis)

        assert (
            caught.value.validation
            == service.VALIDATION_STRATEGY_MARKET_INCOMPATIBLE
        )
        _assert_created_nothing(supabase, redis)

    def test_the_concurrent_cap_refuses_with_429_and_creates_nothing(self) -> None:
        """Requirement 27.4, in ONE round trip against ``idx_paper_sessions_running``."""
        redis = RecordingRedis()
        running = [
            {
                "id": f"aaaaaaaa-0000-0000-0000-00000000000{index}",
                "user_id": USER,
                "session_state": "RUNNING",
            }
            for index in range(3)
        ]
        supabase = _double(sessions=running)

        with pytest.raises(service.PaperSessionLimitReached) as caught:
            _start(supabase, redis=redis)

        assert caught.value.http_status == 429
        assert caught.value.details["limit"] == 3
        assert caught.value.details["running"] == 3
        assert "Stop one before starting another" in caught.value.details["reason"]
        _assert_created_nothing(supabase, redis)

        # ONE statement against paper_sessions, and its predicates are the partial index's.
        counts = [
            statement
            for statement in supabase.statements_on(repo.SESSIONS_TABLE, "select")
        ]
        assert len(counts) == 1
        assert counts[0].filtered_columns() == {"user_id", "session_state"}
        assert counts[0].filter_value("session_state") == "RUNNING"
        assert counts[0].cols == repo.SESSION_COUNT_SELECT

    def test_the_cap_counts_only_the_callers_own_running_sessions(self) -> None:
        """Requirements 21.2, 21.5: another user's running sessions are not fetched at all.

        Three of another tenant's ``RUNNING`` sessions must not consume this caller's cap - and
        must not be readable in the process either, because ``user_id`` is a predicate.
        """
        redis = RecordingRedis()
        supabase = _double(
            sessions=[
                {
                    "id": f"bbbbbbbb-0000-0000-0000-00000000000{index}",
                    "user_id": OTHER_USER,
                    "session_state": "RUNNING",
                }
                for index in range(3)
            ]
        )

        started = _start(supabase, redis=redis)

        assert started.session["session_state"] == "RUNNING"

    def test_a_paused_or_created_session_does_not_consume_the_cap(self) -> None:
        """The index is PARTIAL on ``RUNNING``, and the cap counts what the index holds."""
        redis = RecordingRedis()
        supabase = _double(
            sessions=[
                {"id": "cccccccc-0000-0000-0000-000000000001", "user_id": USER,
                 "session_state": "PAUSED"},
                {"id": "cccccccc-0000-0000-0000-000000000002", "user_id": USER,
                 "session_state": "STOPPED"},
                {"id": "cccccccc-0000-0000-0000-000000000003", "user_id": USER,
                 "session_state": "CREATED"},
            ]
        )

        started = _start(supabase, redis=redis)

        assert started.session["session_state"] == "RUNNING"

    def test_a_configured_cap_of_one_refuses_the_second_session(self) -> None:
        redis = RecordingRedis()
        supabase = _double(
            sessions=[
                {
                    "id": "dddddddd-0000-0000-0000-000000000001",
                    "user_id": USER,
                    "session_state": "RUNNING",
                }
            ]
        )

        with pytest.raises(service.PaperSessionLimitReached) as caught:
            _start(supabase, redis=redis, concurrency_limit=1)

        assert caught.value.details["limit"] == 1
        _assert_created_nothing(supabase, redis)

    def test_a_blocked_market_data_source_is_refused_before_any_create(self) -> None:
        """No measurement, so no candidate cleared the correctness floor (Requirement 14.4).

        Refused BEFORE the creates, and the module docstring says why: ``market_data_source`` is
        ``NOT NULL`` and the frozen config records the same identity, so there is nothing honest to
        write for a deployment whose feed is inadmissible. Stronger than leaving a session at
        ``CREATED``, not weaker.
        """
        redis = RecordingRedis()
        supabase = _double()

        with pytest.raises(feed.PaperMarketDataUnavailable):
            _start(supabase, redis=redis, measurements=None)

        _assert_created_nothing(supabase, redis)


# ══════════════════════════════════════════════════════════════════════════
#  6. THE CREATE BLOCK, THE FEED, AND ``RUNNING`` (task 27.1)
# ══════════════════════════════════════════════════════════════════════════


class TestTheCreateBlockAndTheFeed:
    """The three inserts, in order; then the feed; then ``RUNNING`` and the frame.

    Red run 1: the ``paper_equity_snapshots`` insert was deleted.
    ``test_the_three_creates_happen_in_the_one_order_whose_prefixes_read_correctly`` and
    ``test_the_opening_snapshot_records_the_accounts_own_figures`` failed.

    Red run 2: the ``transition_session_state`` call was moved ABOVE ``open_feed``.
    ``test_a_feed_refusal_leaves_the_session_at_created_with_no_subscription`` failed - the session
    was left ``RUNNING`` with no subscription, which is the worst of the three possible states and
    the one this ordering exists to prevent.
    """

    def test_the_three_creates_happen_in_the_one_order_whose_prefixes_read_correctly(
        self,
    ) -> None:
        redis = RecordingRedis()
        supabase = _double()

        started = _start(supabase, redis=redis)

        inserts = [
            statement.table_name
            for statement in supabase.statements
            if statement.op == "insert"
        ]
        assert inserts[:3] == [
            repo.SESSIONS_TABLE,
            repo.ACCOUNTS_TABLE,
            repo.EQUITY_SNAPSHOTS_TABLE,
        ]
        assert started.session_id == supabase.sessions[0]["id"]

    def test_the_session_row_is_created_at_created_with_the_frozen_config_and_sequence_zero(
        self,
    ) -> None:
        redis = RecordingRedis()
        supabase = _double()

        _start(supabase, redis=redis)

        insert = supabase.statements_on(repo.SESSIONS_TABLE, "insert")[0]
        assert insert.payload["session_state"] == "CREATED"
        assert insert.payload["event_sequence"] == 0
        assert insert.payload["environment"] == "PAPER"
        assert insert.payload["feed_state"] == "PENDING"
        assert insert.payload["initial_capital_minor"] == CAPITAL_MINOR
        assert insert.payload["currency"] == "USD"
        assert insert.payload["version_id"] == VERSION
        assert insert.payload["source_strategy_id"] == STRATEGY
        assert insert.payload["listing_id"] == LISTING
        # The frozen configuration, as the column's one write path renders it.
        assert insert.payload["config"]["simulator"] == sim.SIMULATOR_NAME
        assert insert.payload["config"]["schema_version"] == sim.SCHEMA_VERSION
        assert insert.payload["config"]["price_precision"] == 2
        assert insert.payload["config"]["quantity_precision"] == 8

    def test_the_account_is_session_scoped_at_version_one(self) -> None:
        """Requirement 17.6: the session's own isolated balance set, shared with nothing."""
        redis = RecordingRedis()
        supabase = _double()

        started = _start(supabase, redis=redis)

        insert = supabase.statements_on(repo.ACCOUNTS_TABLE, "insert")[0]
        assert insert.payload["session_id"] == started.session_id
        assert insert.payload["version"] == 1
        assert insert.payload["user_id"] == USER
        assert Decimal(insert.payload["initial_capital"]) == Decimal("100000")
        assert Decimal(insert.payload["available_balance"]) == Decimal("100000")
        assert Decimal(insert.payload["locked_balance"]) == Decimal("0")
        assert Decimal(insert.payload["total_equity"]) == Decimal("100000")

    def test_the_opening_snapshot_records_the_accounts_own_figures(self) -> None:
        redis = RecordingRedis()
        supabase = _double()

        started = _start(supabase, redis=redis)

        insert = supabase.statements_on(repo.EQUITY_SNAPSHOTS_TABLE, "insert")[0]
        assert insert.payload["cause"] == "SESSION_START"
        assert insert.payload["series_index"] == 0
        assert insert.payload["session_id"] == started.session_id
        assert Decimal(insert.payload["total_equity"]) == Decimal("100000")
        assert Decimal(insert.payload["position_market_value"]) == Decimal("0")
        assert insert.payload["stale"] is False

    def test_the_account_balances_agree_with_the_sessions_recorded_capital(self) -> None:
        """The one thing the missing transaction cannot break, and why.

        Both figures come from ONE validated ``initial_capital_minor`` in
        :func:`~paper_session_service.validate_capital`, before either statement is issued - so
        their agreement is arithmetic done up front rather than two statements that happened to
        commit together.
        """
        redis = RecordingRedis()
        supabase = _double()

        started = _start(supabase, redis=redis)

        minor = int(supabase.sessions[0]["initial_capital_minor"])
        assert Decimal(started.account["initial_capital"]) == Decimal(minor).scaleb(-2)

    def test_the_session_reaches_running_and_the_started_frame_is_recorded(self) -> None:
        redis = RecordingRedis()
        supabase = _double()

        started = _start(supabase, redis=redis)

        assert started.session["session_state"] == "RUNNING"
        assert started.session["started_at"] == NOW.isoformat()
        recorded = supabase.events
        assert [row["event_type"] for row in recorded] == ["paper_session_started"]
        assert recorded[0]["sequence"] == 1
        payload = recorded[0]["payload"]
        assert payload["actor_id"] == USER
        assert payload["session_id"] == started.session_id
        assert payload["initial_capital_minor"] == CAPITAL_MINOR
        assert payload["market_data_source"] == started.feed.market_data_source

    def test_the_started_frame_carries_a_digest_and_not_the_configuration(self) -> None:
        """Requirement 19.7: a subscriber may tell one configuration from another, not read it."""
        redis = RecordingRedis()
        supabase = _double()

        started = _start(supabase, redis=redis)

        payload = supabase.events[0]["payload"]
        assert payload["config_digest"] == service.config_digest(started.config)
        assert len(payload["config_digest"]) == 64
        body = json.dumps(payload)
        assert "fee_rate" not in body
        assert "slippage_rate" not in body
        assert str(started.config.fee_rate) not in body

    def test_the_digest_is_stable_across_processes(self) -> None:
        """``hashlib``, not ``hash()``: a per-process salt would change it on every restart."""
        redis = RecordingRedis()
        supabase = _double()
        started = _start(supabase, redis=redis)

        assert service.config_digest(started.config) == service.config_digest(
            started.config.to_jsonb()
        )

    def test_the_subscription_is_opened_after_the_creates_and_not_before(self) -> None:
        redis = RecordingRedis()
        supabase = _double()

        _start(supabase, redis=redis)

        assert redis.commands() == [
            {"action": "subscribe", "exchange": EXCHANGE, "symbol": SYMBOL}
        ]
        assert redis.subscribed == [feed.mds_data_channel(EXCHANGE, SYMBOL)]

    def test_the_loop_seam_is_called_with_the_session_and_the_feed(self) -> None:
        """Task 27.2 owns the body; 27.1 owns the call. Asserted as the call."""
        redis = RecordingRedis()
        supabase = _double()
        seen: List[Dict[str, Any]] = []

        async def spawn(
            session: Any, handle: Any, *, config: Any, account_id: Any
        ) -> str:
            seen.append(
                {
                    "session": session,
                    "feed": handle,
                    "config": config,
                    "account_id": account_id,
                }
            )
            return "spawned"

        started = _start(supabase, redis=redis, spawn_loop=spawn)

        assert len(seen) == 1
        assert seen[0]["session"]["id"] == started.session_id
        assert seen[0]["session"]["session_state"] == "RUNNING"
        assert seen[0]["feed"] is started.feed

    def test_the_seam_carries_the_pipelines_own_config_and_account(self) -> None:
        """The two values a spawner could not have had, supplied by the layer that created them.

        The loop cannot step without the frozen configuration and the session's own Paper_Account,
        and both are made INSIDE ``start_session`` - the config several validations deep, the
        account by the second statement of the create block. So they travel through the seam, and
        they are asserted BY IDENTITY: an installing layer that re-derived the configuration would
        be a second implementation of Requirement 16.12, free to disagree with the row the session
        stores, and an equal-but-not-identical config is exactly what that would look like here.
        """
        redis = RecordingRedis()
        supabase = _double()
        seen: List[Dict[str, Any]] = []

        async def spawn(
            session: Any, handle: Any, *, config: Any, account_id: Any
        ) -> str:
            seen.append({"config": config, "account_id": account_id})
            return "spawned"

        started = _start(supabase, redis=redis, spawn_loop=spawn)

        assert seen[0]["config"] is started.config
        assert seen[0]["account_id"] == started.account["id"]
        # And the configuration the loop was handed is the configuration the ROW records, not a
        # second one built for the loop.
        assert started.config.to_jsonb() == supabase.sessions[0]["config"]

    def test_what_the_spawner_produced_is_carried_out_to_the_caller(self) -> None:
        """``StartedSession.loop_task``: the only handle on a running loop leaves the function.

        ``stop_session`` takes the loop task as ``loop_task=`` and settles it before it commits a
        closing figure (Requirement 17.8, step 3), and this module keeps no registry of running
        loops. If ``start_session`` dropped what the spawner returned, that handle would go out of
        scope with the local variable and no stop could ever settle the loop.
        """
        redis = RecordingRedis()
        supabase = _double()
        produced = object()

        def spawn(session: Any, handle: Any, *, config: Any, account_id: Any) -> Any:
            return produced

        started = _start(supabase, redis=redis, spawn_loop=spawn)

        assert started.loop_task is produced

    def test_a_spawner_that_raises_leaves_the_session_running_with_no_loop(self) -> None:
        """Contained, named in the log, and reported as ``loop_task=None`` rather than as a failure.

        The session IS ``RUNNING``, the transition IS committed and the started frame IS recorded,
        so a raise here would report a started session as unstarted and leave the caller with no
        identifier for the session that exists.
        """
        redis = RecordingRedis()
        supabase = _double()

        def spawn(session: Any, handle: Any, *, config: Any, account_id: Any) -> Any:
            raise RuntimeError("no event loop for this session")

        started = _start(supabase, redis=redis, spawn_loop=spawn)

        assert started.loop_task is None
        assert started.session["session_state"] == "RUNNING"

    def test_the_default_seam_logs_an_error_rather_than_passing_quietly(
        self, caplog: Any
    ) -> None:
        """A session ``RUNNING`` with no loop produces truthful zeros forever. Say so."""
        redis = RecordingRedis()
        supabase = _double()

        with caplog.at_level("ERROR", logger="PaperSessionService"):
            _start(supabase, redis=redis)

        assert any(
            "NO session loop is installed" in record.getMessage()
            for record in caplog.records
        )

    def test_a_feed_refusal_leaves_the_session_at_created_with_no_subscription(
        self, monkeypatch: Any
    ) -> None:
        """Requirement 14.4 / 14.8, and the ordering that makes it true.

        The DEV_MODE mock interface refusal is the post-create one: the session and its account
        exist, the session is ``CREATED``, nothing was published to ``mds:commands`` and nothing was
        subscribed. The audit write is stubbed because this case is about the session's state and
        the transport, not about the record - ``tests/test_paper_market_feed_selection.py`` owns
        that assertion.
        """

        async def _no_audit(config: Any) -> None:
            return None

        monkeypatch.setattr(feed, "_audit_mock_interface_refusal", _no_audit)
        redis = RecordingRedis()
        supabase = _double()

        with pytest.raises(feed.PaperMarketDataUnavailable) as caught:
            _start(supabase, redis=redis, exchange=_mock_served_exchange())

        assert caught.value.rule == feed.PaperMarketDataUnavailable.RULE_MOCK_INTERFACE
        # The session exists and is CREATED - not RUNNING, and not absent.
        assert len(supabase.sessions) == 1
        assert supabase.sessions[0]["session_state"] == "CREATED"
        assert supabase.sessions[0]["feed_state"] == "PENDING"
        # No subscription, either half of it.
        assert redis.log == []
        assert redis.published == []
        # And no session lifecycle event was recorded, because none happened.
        assert supabase.events == []


# ══════════════════════════════════════════════════════════════════════════
#  7. THE FOUR OPERATIONS (task 27.3 - Requirements 17.7, 17.14, 21.4)
# ══════════════════════════════════════════════════════════════════════════


def _frozen_config() -> sim.SessionConfig:
    """The session's frozen configuration, built the way task 27.1 builds it at start.

    Defined here rather than beside the loop helpers because :func:`_running_session` needs it and
    is itself called at module scope. ``_loop_config`` delegates to it, so there is one frozen
    configuration in this file and both halves of it read the same rounding mode and precisions.
    """
    metadata = sim.resolve_market_metadata(
        _markets(), exchange_id=EXCHANGE, symbol=SYMBOL
    )
    return sim.freeze_session_config(
        metadata=metadata, currency="USD", market_data_source="mds.watch_ohlcv"
    )


def _running_session(
    session_state: str = "RUNNING", user_id: str = USER
) -> Dict[str, Any]:
    return {
        "id": "eeeeeeee-0000-0000-0000-000000000001",
        "user_id": user_id,
        "session_state": session_state,
        "environment": "PAPER",
        "exchange_id": EXCHANGE,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "initial_capital_minor": CAPITAL_MINOR,
        "currency": "USD",
        # ``paper_sessions.config`` is NOT NULL and it is what the stop's closing figures and the
        # reset's invariant check are computed under (Requirement 16.12), so a seeded session row
        # carries it exactly as ``insert_session`` would have written it - through
        # ``session_config_payload``, which renders every Decimal as its exact decimal string.
        "config": repo.session_config_payload(_frozen_config()),
        "market_data_source": "mds.watch_ohlcv",
        "feed_state": "HEALTHY",
        "feed_transport": "WEBSOCKET",
        "event_sequence": 0,
        "started_at": NOW.isoformat(),
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
    }


SESSION = _running_session()["id"]


def _with_session(session_state: str, user_id: str = USER) -> FakeSupabase:
    repo.reset_persistence_probe()
    return FakeSupabase(sessions=[_running_session(session_state, user_id)])


#: The account's recorded initial capital in the major units ``paper_accounts`` stores - the same
#: amount :data:`CAPITAL_MINOR` is, at USD's two decimal places. Both figures are written from one
#: place so a test cannot assert that the balances returned to a figure the session never recorded.
CAPITAL_MAJOR = Decimal(CAPITAL_MINOR).scaleb(-2)


def _lifecycle_double(
    session_state: str = "RUNNING",
    *,
    capital: Decimal = CAPITAL_MAJOR,
    snapshots: Tuple[str, ...] = ("SESSION_START", "REVALUATION"),
    user_id: str = USER,
) -> Tuple[FakeSupabase, str]:
    """A session at ``session_state`` with its isolated account and an equity series to close.

    The premise ``start_session`` and the loop leave behind, built through the real repository
    functions so it is the premise the code under test would actually meet: the account is created
    by ``get_or_create_account`` and the series by ``insert_equity_snapshot``. Two snapshots by
    default, because Requirement 18.9 reports zero drawdown below two and a one-point series would
    make every drawdown assertion below pass for the wrong reason.

    The setup's own statements are cleared, so what a case reads afterwards is what the stop or the
    reset issued.
    """
    repo.reset_persistence_probe()
    paper_channel.invalidate_session_owner()
    supabase = FakeSupabase(sessions=[_running_session(session_state, user_id)])
    account = repo.get_or_create_account(
        supabase, user_id, "USD", SESSION, initial_capital=capital
    )
    for index, cause in enumerate(snapshots):
        repo.insert_equity_snapshot(
            supabase,
            user_id=user_id,
            session_id=SESSION,
            series_index=0,
            total_equity=capital,
            available_balance=capital,
            locked_balance=Decimal("0"),
            position_market_value=Decimal("0"),
            stale=False,
            cause=cause,
            taken_at=NOW - timedelta(minutes=len(snapshots) - index),
        )
    supabase.statements.clear()
    supabase.ops.clear()
    return supabase, str(account["id"])


def _operate(
    supabase: FakeSupabase,
    operation: str,
    *,
    caller: Optional[_Caller] = None,
    session_id: Any = SESSION,
) -> Any:
    return _run_coroutine(
        service.apply_operation(
            supabase, caller or _Caller(), session_id, operation, now=NOW
        )
    )


class TestTheFourOperations:
    """Each accepted operation transitions and is recorded; each illegal one answers 409.

    Red run 1 (the gate): the ``can_operate`` check in ``apply_operation`` was removed, leaving the
    guarded UPDATE and ``trg_paper_session_guard`` as the only enforcement. Every case in
    :class:`TestAnIllegalOperationChangesNothing` failed - the double's transition guard raised a
    ``23514`` rather than the 409 Requirement 17.14 requires, so the caller got a 500-shaped
    failure naming a constraint instead of an error naming their own state and operation.

    Red run 2 (the record): the ``_emit`` call was removed from ``apply_operation``. Every
    ``paper_events`` assertion here failed while every transition assertion stayed green - which is
    the pair that shows Requirement 17.7's "record each accepted operation" is asserted separately
    from the transition.
    """

    @pytest.mark.parametrize(
        "state,operation,expected,event_type",
        [
            ("RUNNING", "pause", "PAUSED", "paper_session_paused"),
            ("PAUSED", "resume", "RUNNING", "paper_session_resumed"),
            ("RUNNING", "stop", "STOPPED", "paper_session_stopped"),
            ("PAUSED", "stop", "STOPPED", "paper_session_stopped"),
            ("STOPPED", "reset", "CREATED", "paper_session_stopped"),
        ],
    )
    def test_each_permitted_operation_transitions_and_is_recorded(
        self, state: str, operation: str, expected: str, event_type: str
    ) -> None:
        supabase = _with_session(state)

        outcome = _operate(supabase, operation)

        assert outcome.operation == operation
        assert outcome.from_state == state
        assert outcome.to_state == expected
        assert supabase.sessions[0]["session_state"] == expected

        assert len(supabase.events) == 1
        row = supabase.events[0]
        assert row["event_type"] == event_type
        assert row["sequence"] == 1
        # Requirement 17.7: the requesting user, the resulting state, and a timestamp.
        assert row["payload"]["actor_id"] == USER
        assert row["payload"]["session_state"] == expected
        assert row["payload"]["at"] == "2024-05-01T12:00:00.000000Z"
        assert row["user_id"] == USER

    def test_the_transition_is_guarded_on_the_state_that_was_read(self) -> None:
        """This transport's substitute for ``SELECT ... FOR UPDATE``.

        The UPDATE carries three predicates and the third is the state the gate ran against, so a
        concurrent operation that already moved the session makes it match zero rows.
        """
        supabase = _with_session("RUNNING")

        _operate(supabase, "pause")

        update = supabase.statements_on(repo.SESSIONS_TABLE, "update")[0]
        assert update.filtered_columns() == {"id", "user_id", "session_state"}
        assert update.filter_value("session_state") == "RUNNING"
        assert update.payload["session_state"] == "PAUSED"

    def test_a_concurrent_operation_makes_the_guarded_update_lose(self) -> None:
        """The race the state predicate exists to detect, injected at the double's seam."""
        from backend_app.backend.paper.paper_repository import PaperConcurrencyConflict

        supabase = _with_session("RUNNING")

        def move_it(client: FakeSupabase, query: Any) -> None:
            if query.table_name == repo.SESSIONS_TABLE and query.op == "update":
                client.sessions[0]["session_state"] = "STOPPED"
                client.before_update = None

        supabase.before_update = move_it

        with pytest.raises(PaperConcurrencyConflict):
            _operate(supabase, "pause")

        assert supabase.sessions[0]["session_state"] == "STOPPED"
        assert supabase.events == []

    def test_pause_writes_paused_at_and_resume_does_not_rewrite_started_at(self) -> None:
        """``paper_sessions`` has no ``resumed_at``, so a resume stamps nothing.

        A resume that re-stamped ``started_at`` would erase when the session actually began.
        """
        supabase = _with_session("RUNNING")
        _operate(supabase, "pause")
        assert supabase.sessions[0]["paused_at"] == NOW.isoformat()

        began = supabase.sessions[0]["started_at"]
        _operate(supabase, "resume")
        assert supabase.sessions[0]["started_at"] == began
        assert "resumed_at" not in supabase.sessions[0]

    def test_stop_writes_stopped_at(self) -> None:
        supabase = _with_session("RUNNING")
        _operate(supabase, "stop")
        assert supabase.sessions[0]["stopped_at"] == NOW.isoformat()

    def test_the_operations_sequence_contiguously_in_paper_events(self) -> None:
        """Requirement 19.3: 1 for the first, +1 for each after it - on the database's counter."""
        supabase = _with_session("RUNNING")

        _operate(supabase, "pause")
        _operate(supabase, "resume")
        _operate(supabase, "stop")
        _operate(supabase, "reset")

        assert [row["sequence"] for row in supabase.events] == [1, 2, 3, 4]
        assert [row["event_type"] for row in supabase.events] == [
            "paper_session_paused",
            "paper_session_resumed",
            "paper_session_stopped",
            "paper_session_stopped",
        ]

    def test_the_reset_record_carries_created_even_though_the_type_says_stopped(
        self,
    ) -> None:
        """The stated gap in Requirement 19.2's sixteen types, asserted rather than hidden.

        There is no ``paper_session_reset`` among the sixteen and ``chk_paper_event_type`` admits
        no seventeenth, so the reset is recorded under the nearest type - and ``session_state``
        carries the truth, which is the field that exists so a client can read the current state
        off a frame instead of inferring it from a type.
        """
        supabase = _with_session("STOPPED")

        _operate(supabase, "reset")

        row = supabase.events[0]
        assert row["event_type"] == "paper_session_stopped"
        assert row["payload"]["session_state"] == "CREATED"
        assert row["payload"]["final_metrics"] is None

    def test_the_four_named_wrappers_reach_the_same_implementation(self) -> None:
        """All four move the session along the edge they name.

        ``stop`` and ``reset`` do considerably more than that now (task 27.4, asserted in
        :class:`TestTheStopCommitsThenReleasesThenReports` and
        :class:`TestTheResetRestoresAndDeletesNothing`), so they are driven against a double that
        HOLDS an account - their bodies read balances - while ``pause`` and ``resume`` touch none.
        The claim here is only the shared one: each wrapper reaches ``apply_operation`` and takes
        its own edge.
        """
        for state, call, expected in (
            ("RUNNING", service.pause_session, "PAUSED"),
            ("PAUSED", service.resume_session, "RUNNING"),
        ):
            outcome = _run_coroutine(
                call(_with_session(state), _Caller(), SESSION, now=NOW)
            )
            assert outcome.to_state == expected

        for state, call, expected in (
            ("RUNNING", service.stop_session, "STOPPED"),
            ("STOPPED", service.reset_session, "CREATED"),
        ):
            supabase, _account_id = _lifecycle_double(state)
            outcome = _run_coroutine(call(supabase, _Caller(), SESSION, now=NOW))
            assert outcome.to_state == expected


class TestAnIllegalOperationChangesNothing:
    """409 naming BOTH the state and the operation, and no statement issued. Requirement 17.14."""

    @pytest.mark.parametrize(
        "state,operation",
        [
            ("CREATED", "pause"),
            ("PAUSED", "pause"),
            ("STOPPED", "pause"),
            ("CREATED", "resume"),
            ("RUNNING", "resume"),
            ("STOPPED", "resume"),
            ("CREATED", "stop"),
            ("STOPPED", "stop"),
            ("CREATED", "reset"),
            ("RUNNING", "reset"),
            ("PAUSED", "reset"),
        ],
    )
    def test_the_refusal_names_the_state_and_the_operation_and_writes_nothing(
        self, state: str, operation: str
    ) -> None:
        supabase = _with_session(state)

        with pytest.raises(service.PaperSessionOperationRejected) as caught:
            _operate(supabase, operation)

        error = caught.value
        assert error.http_status == 409
        assert error.code == "PAPER_SESSION_OPERATION_REJECTED"
        # BOTH, which is what Requirement 17.14 requires and what a caller acts on.
        assert error.details["session_state"] == state
        assert error.details["operation"] == operation
        assert error.details["permitted_from"] == sorted(
            service.OPERATION_TRANSITIONS[operation]
        )

        # Nothing changed: no statement, no state move, no event.
        assert supabase.sessions[0]["session_state"] == state
        assert not supabase.wrote_anything()
        assert supabase.events == []

    def test_start_is_refused_as_an_operation_even_from_created(self) -> None:
        """The edge exists; the OPERATION does not.

        ``start`` is the tail of :func:`~paper_session_service.start_session`, not a route: a second
        start would open a second feed and a second loop against one session. This is the one place
        the service is stricter than the edge table, and it is asserted so that strictness is
        deliberate rather than accidental.
        """
        assert service.can_operate("start", "CREATED") is True
        supabase = _with_session("CREATED")

        with pytest.raises(service.PaperSessionOperationRejected) as caught:
            _operate(supabase, "start")

        assert caught.value.details["operation"] == "start"
        assert caught.value.details["session_state"] == "CREATED"
        assert not supabase.wrote_anything()

    def test_an_unknown_operation_is_refused_the_same_way(self) -> None:
        supabase = _with_session("RUNNING")

        with pytest.raises(service.PaperSessionOperationRejected) as caught:
            _operate(supabase, "delete")

        assert caught.value.details["operation"] == "delete"
        assert caught.value.details["permitted_from"] == []
        assert not supabase.wrote_anything()

    def test_a_stored_state_outside_the_four_is_refused_and_reported_verbatim(self) -> None:
        """``chk_paper_session_state`` makes this unreachable; a hand-reconciled table does not.

        The body carries the value AS STORED, so an operator reading the rejection sees what caused
        it rather than a normalised guess.
        """
        supabase = _with_session("RUNNING")
        supabase.sessions[0]["session_state"] = "ARCHIVED"

        with pytest.raises(service.PaperSessionOperationRejected) as caught:
            _operate(supabase, "stop")

        assert caught.value.details["session_state"] == "ARCHIVED"
        assert not supabase.wrote_anything()


class TestAnotherUsersSessionAnswersAsAnUnknownOne:
    """Requirement 21.4, on all four operations.

    Red run: ``read_session``'s ``user_id`` predicate cannot be removed without failing
    ``tests/test_paper_repository.py``, so the red run was taken in this module instead: the
    ``if row is None`` branch was changed to re-read without the caller scope and answer
    ``PAPER_SESSION_OPERATION_REJECTED``. Both cases here failed - the 409 body named the other
    tenant's ``session_state``, which discloses both that the session exists and what it is doing.
    """

    @pytest.mark.parametrize("operation", ["pause", "resume", "stop", "reset"])
    def test_another_tenants_session_answers_not_found(self, operation: str) -> None:
        supabase = _with_session("RUNNING", user_id=OTHER_USER)

        with pytest.raises(PaperError) as caught:
            _operate(supabase, operation)

        assert caught.value.code == "NOT_FOUND"
        assert caught.value.details == {"session_id": SESSION}
        assert supabase.sessions[0]["session_state"] == "RUNNING"
        assert not supabase.wrote_anything()

    @pytest.mark.parametrize("operation", ["pause", "resume", "stop", "reset"])
    def test_an_unknown_session_answers_identically(self, operation: str) -> None:
        """Byte-identical bodies, compared as bodies rather than as two separate assertions."""
        unknown_id = "ffffffff-0000-0000-0000-000000000000"

        foreign = _with_session("RUNNING", user_id=OTHER_USER)
        with pytest.raises(PaperError) as foreign_caught:
            _operate(foreign, operation)

        absent = _with_session("RUNNING")
        with pytest.raises(PaperError) as absent_caught:
            _operate(absent, operation, session_id=unknown_id)

        foreign_body = foreign_caught.value.to_error_object()
        absent_body = absent_caught.value.to_error_object()
        # The only difference permitted is the identifier the caller themselves supplied.
        assert foreign_caught.value.http_status == absent_caught.value.http_status
        assert foreign_body["code"] == absent_body["code"]
        assert foreign_body["message"] == absent_body["message"]
        assert set(foreign_body["details"]) == set(absent_body["details"]) == {
            "session_id"
        }

    def test_the_read_is_scoped_by_the_caller_in_the_statement(self) -> None:
        """Requirements 21.2, 21.5: never fetched, not merely never returned."""
        supabase = _with_session("RUNNING", user_id=OTHER_USER)

        with pytest.raises(PaperError):
            _operate(supabase, "pause")

        read = supabase.statements_on(repo.SESSIONS_TABLE, "select")[0]
        assert read.filter_value("user_id") == USER


# ══════════════════════════════════════════════════════════════════════════
#  7a. THE STOP AND THE RESET (task 27.4 - Requirements 17.2, 17.8, 17.15, 19.12)
# ══════════════════════════════════════════════════════════════════════════
#
# The claim under test is an ORDER, and an order is only assertable against an ordered record. So
# every side effect a stop produces - the two finals INSERTs, the ``paper_events`` INSERT, the
# ``mds:commands`` publish, the local pubsub unsubscribe and close, and the Paper_Channel release -
# is written into ONE list, in the order it happened, and the assertion is that the list is that
# sequence. ``RecordingRedis.log`` is already such a list for the transport half (that is what it
# exists for in ``tests/test_paper_market_feed_selection.py``), so the other halves are appended to
# the SAME list rather than to counters of their own: "the registration was closed after the
# unsubscribe was published" is a statement about sequence, and two booleans cannot carry it.
#
# The doubles are the ones this repository already has, in every case: ``FakeSupabase`` for the
# Persistence_Layer, ``RecordingRedis`` plus the REAL ``open_feed``/``FeedHandle`` for the
# transport, the real ``paper_channel.REGISTRY`` with the ``Connection`` double for delivery, and
# the real ``paper_accounting`` for every figure.


def _cancellable_order(
    supabase: FakeSupabase,
    account_id: str,
    *,
    state: Any = PaperOrderState.ACCEPTED,
    key: str = "idem-1",
) -> Dict[str, Any]:
    """One ``paper_orders`` row at ``state``, through the real writer."""
    return repo.insert_order(
        supabase,
        account_id=account_id,
        user_id=USER,
        session_id=SESSION,
        symbol=SYMBOL,
        side="buy",
        order_type="limit",
        quantity=Decimal("0.5"),
        limit_price=Decimal("59000"),
        reference_price=Decimal("60000"),
        fingerprint=f"fingerprint-{key}",
        idempotency_key=key,
        order_state=state,
    )


def _open_position(
    supabase: FakeSupabase,
    account_id: str,
    *,
    size: Decimal = Decimal("0.5"),
    price: Decimal = Decimal("60000"),
) -> Dict[str, Any]:
    """One OPEN ``paper_positions`` row, priced - so it has a validated price to close against."""
    return repo.upsert_position(
        supabase,
        account_id=account_id,
        user_id=USER,
        session_id=SESSION,
        symbol=SYMBOL,
        side="LONG",
        size=size,
        entry_price=price,
        opened_at=NOW - timedelta(minutes=5),
        current_price=price,
        unrealized_pnl=Decimal("0"),
        price_at=NOW - timedelta(minutes=1),
    )


def _traced(supabase: FakeSupabase, trace: List[Tuple[str, str]]) -> None:
    """Record every write this session's stop or reset issues into ``trace``, in order.

    ``before_insert`` and ``before_update`` are ``FakeSupabase``'s own seams, so this is the
    double's ordering record rather than a second one.
    """

    def note_insert(_client: FakeSupabase, query: Any) -> None:
        trace.append(("insert", query.table_name))

    def note_update(_client: FakeSupabase, query: Any) -> None:
        trace.append(("update", query.table_name))

    supabase.before_insert = note_insert
    supabase.before_update = note_update


class TestTheStopCommitsThenReleasesThenReports:
    """Requirement 17.8, as the sequence it is.

    Red run 1 (the order): the two release calls in ``stop_session`` were moved ABOVE the
    ``apply_operation`` call - the "release first, then finish" shape.
    ``test_the_whole_sequence_is_committed_then_released_then_reported`` failed on the ordered log
    (the publish and the channel release appeared before the two finals INSERTs and before the
    ``paper_events`` row), and
    ``test_the_stopped_frame_reaches_a_subscriber_before_its_registration_is_closed`` failed because
    the subscriber's socket was already closed when the frame was broadcast - it received nothing.
    Every ``complete`` assertion stayed GREEN, which is the pair that shows "both happened" and "they
    happened in this order" are asserted separately.

    Red run 2 (the loop settle): ``_settle_loop`` was reduced to ``task.cancel()`` with no ``await``.
    ``test_the_loop_is_cancelled_and_awaited_before_a_final_figure_is_written`` failed - the task was
    still pending when the closing snapshot was written, which is exactly the race that would let a
    bar append an equity point after the "final" one.
    """

    @pytest.mark.parametrize("state", ["RUNNING", "PAUSED"])
    def test_stop_is_legal_from_running_and_from_paused(self, state: str) -> None:
        """Requirement 17.7's one operation with two sources, and both commit the same finals."""
        supabase, _account_id = _lifecycle_double(state)
        handle, _redis = _loop_feed(supabase, [])

        outcome = _run_coroutine(
            service.stop_session(
                supabase,
                _Caller(),
                SESSION,
                now=NOW,
                feed=handle,
                release_registrations=_noop_release(),
            )
        )

        assert outcome.from_state == state
        assert outcome.to_state == "STOPPED"
        assert supabase.sessions[0]["session_state"] == "STOPPED"
        assert supabase.sessions[0]["stopped_at"] == NOW.isoformat()
        assert outcome.complete is True

    def test_the_closing_equity_snapshot_exists_with_the_session_stop_cause(self) -> None:
        """Requirement 18.11's fifth point, and it is the last point of the series it closes."""
        supabase, _account_id = _lifecycle_double("RUNNING")
        handle, _redis = _loop_feed(supabase, [])

        outcome = _run_coroutine(
            service.stop_session(
                supabase,
                _Caller(),
                SESSION,
                now=NOW,
                feed=handle,
                release_registrations=_noop_release(),
            )
        )

        closing = outcome.finals.snapshot
        assert closing is not None
        assert closing["cause"] == "SESSION_STOP"
        assert closing["series_index"] == 0
        assert closing["taken_at"] == NOW.isoformat()
        # The whole series, in the order the API serves it: the two seeded points then this one.
        series = repo.get_equity_snapshots(supabase, USER, session_id=SESSION)
        assert [row["cause"] for row in series] == [
            "SESSION_START",
            "REVALUATION",
            "SESSION_STOP",
        ]

    def test_the_closing_figures_are_the_accounts_own_and_not_recomputed(self) -> None:
        """A session that held no position closes at exactly its cash, and zero is a measurement."""
        supabase, _account_id = _lifecycle_double("RUNNING")
        handle, _redis = _loop_feed(supabase, [])

        outcome = _run_coroutine(
            service.stop_session(
                supabase,
                _Caller(),
                SESSION,
                now=NOW,
                feed=handle,
                release_registrations=_noop_release(),
            )
        )

        closing = outcome.finals.snapshot or {}
        assert Decimal(str(closing["total_equity"])) == CAPITAL_MAJOR
        assert Decimal(str(closing["available_balance"])) == CAPITAL_MAJOR
        assert Decimal(str(closing["locked_balance"])) == Decimal("0")
        # The sum over an empty set of open positions. A measurement, so not stale.
        assert Decimal(str(closing["position_market_value"])) == Decimal("0")
        assert closing["stale"] is False
        assert outcome.finals.stale is False

    def test_the_metrics_row_is_committed_and_an_absent_figure_is_absent(self) -> None:
        """Requirement 17.8's metrics, and Requirements 18.10 / 28.5 on the ones it cannot make."""
        supabase, _account_id = _lifecycle_double("RUNNING")
        handle, _redis = _loop_feed(supabase, [])

        outcome = _run_coroutine(
            service.stop_session(
                supabase,
                _Caller(),
                SESSION,
                now=NOW,
                feed=handle,
                release_registrations=_noop_release(),
            )
        )

        row = repo.get_metrics(supabase, USER, session_id=SESSION)
        assert row is not None
        assert row is not None and outcome.finals.metrics_row is not None
        assert row["computed_at"] == NOW.isoformat()
        # No closed trade, so the win rate is ABSENT rather than zero (Requirement 18.10).
        assert row["win_rate"] is None
        assert int(row["closed_trade_count"]) == 0
        # A flat series: the drawdown is a measured zero, and the return on the recorded capital is
        # a measured zero too - both computed from the persisted snapshots.
        assert Decimal(str(row["max_drawdown_amount"])) == Decimal("0")
        assert Decimal(str(row["total_return_pct"])) == Decimal("0")

    def test_the_stopped_frames_final_metrics_are_the_figures_that_were_committed(self) -> None:
        """``final_metrics`` is no longer ``None``, and it is not a zero either (Req 28.3)."""
        supabase, _account_id = _lifecycle_double("RUNNING")
        handle, _redis = _loop_feed(supabase, [])

        _run_coroutine(
            service.stop_session(
                supabase,
                _Caller(),
                SESSION,
                now=NOW,
                feed=handle,
                release_registrations=_noop_release(),
            )
        )

        payload = _payload_of(supabase, "paper_session_stopped")
        assert payload["session_state"] == "STOPPED"
        final = payload["final_metrics"]
        assert isinstance(final, dict) and final
        row = repo.get_metrics(supabase, USER, session_id=SESSION) or {}
        # Every figure on the frame that has a column agrees with the column, exactly.
        for column in (
            "realized_pnl",
            "unrealized_pnl",
            "max_drawdown_amount",
            "total_return_pct",
        ):
            assert Decimal(str(final[column])) == Decimal(str(row[column]))
        assert int(final["closed_trade_count"]) == int(row["closed_trade_count"])
        # Absent stays absent: ``win_rate`` has no key at all rather than a zero one.
        assert "win_rate" not in final

    def test_the_whole_sequence_is_committed_then_released_then_reported(self) -> None:
        """THE ordering assertion. One log, in the order the steps happened.

        Requirement 17.8 is a sequence and this is that sequence: the closing equity point, the
        metrics row, the ``paper_events`` record, the ``mds:commands`` unsubscribe, the local
        unsubscribe and close, and only then the Paper_Channel release. Asserted as an ordered list
        and not as a set of "did it happen" booleans, because a stop that released first and
        committed second satisfies every boolean and fails this.
        """
        supabase, _account_id = _lifecycle_double("RUNNING")
        handle, redis = _loop_feed(supabase, [])
        trace: List[Tuple[str, str]] = redis.log
        trace.clear()

        async def release(session_id: Any) -> Tuple[Any, ...]:
            trace.append(("channel-release", str(session_id)))
            return ()

        def note_insert(_client: FakeSupabase, query: Any) -> None:
            if query.table_name in (
                repo.EQUITY_SNAPSHOTS_TABLE,
                repo.METRICS_TABLE,
                repo.EVENTS_TABLE,
            ):
                trace.append(("insert", query.table_name))

        supabase.before_insert = note_insert

        outcome = _run_coroutine(
            service.stop_session(
                supabase,
                _Caller(),
                SESSION,
                now=NOW,
                feed=handle,
                release_registrations=release,
            )
        )

        assert trace == [
            ("insert", repo.EQUITY_SNAPSHOTS_TABLE),
            ("insert", repo.METRICS_TABLE),
            ("insert", repo.EVENTS_TABLE),
            ("publish", feed.MDS_COMMAND_CHANNEL),
            ("unsubscribe", handle.channel),
            ("close", ""),
            ("channel-release", SESSION),
        ]
        # And the report is only made once every one of them has happened.
        assert outcome.complete is True

    def test_the_published_command_is_the_unsubscribe_handle_commands_reads(self) -> None:
        """The wire contract, compared against the three keys that separate process reads."""
        supabase, _account_id = _lifecycle_double("RUNNING")
        handle, redis = _loop_feed(supabase, [])
        redis.published.clear()

        _run_coroutine(
            service.stop_session(
                supabase,
                _Caller(),
                SESSION,
                now=NOW,
                feed=handle,
                release_registrations=_noop_release(),
            )
        )

        assert redis.commands() == [
            {"action": "unsubscribe", "exchange": EXCHANGE, "symbol": SYMBOL}
        ]

    def test_the_stopped_frame_reaches_a_subscriber_before_its_registration_is_closed(
        self,
    ) -> None:
        """Requirement 19.12 releases a subscriber AFTER telling it, not instead of telling it.

        Driven through the real ``paper_channel.REGISTRY`` and the real default seam, because the
        claim is about the two production paths meeting in the right order: ``_emit`` broadcasts on
        the singleton registry and ``release_session`` is what the stop's last step calls.
        """
        from tests.test_task_26_4_paper_channel_delivery import Connection

        supabase, _account_id = _lifecycle_double("RUNNING")
        handle, _redis = _loop_feed(supabase, [])
        connection = Connection("watcher")
        _run_coroutine(
            paper_channel.REGISTRY.subscribe(
                SESSION, connection, user={"id": USER}
            )
        )
        try:
            outcome = _run_coroutine(
                service.stop_session(
                    supabase, _Caller(), SESSION, now=NOW, feed=handle
                )
            )
        finally:
            _run_coroutine(paper_channel.REGISTRY.release_session(SESSION))

        # It was told, and what it was told is the stop.
        assert connection.types() == ["paper_session_stopped"]
        # Then it was released, with the code Requirement 19.12's release carries.
        assert connection.closed_with == [paper_channel.CLOSE_CODE_SESSION_RELEASED]
        assert paper_channel.REGISTRY.subscriptions(SESSION) == ()
        assert outcome.registrations_closed == 1
        assert outcome.complete is True

    def test_the_loop_is_cancelled_and_awaited_before_a_final_figure_is_written(self) -> None:
        """Step 3 before step 4, because a bar still writing would supersede the "final" point.

        The task is created and the whole stop is driven inside ONE ``_run_coroutine``, so the
        harness's single event loop is as idle after this case as before it (see ``_HARNESS_LOOP``
        in ``tests/test_paper_order_lifecycle_writes.py``): a fresh loop per call exhausts this
        machine's ephemeral ports.
        """
        supabase, _account_id = _lifecycle_double("RUNNING")
        handle, _redis = _loop_feed(supabase, [])
        observed: List[str] = []

        async def forever() -> None:
            try:
                while True:
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                observed.append("loop-cancelled")
                raise

        def note_insert(_client: FakeSupabase, query: Any) -> None:
            if query.table_name == repo.EQUITY_SNAPSHOTS_TABLE:
                observed.append("closing-snapshot")

        supabase.before_insert = note_insert

        async def drive() -> Any:
            task = asyncio.create_task(forever())
            await asyncio.sleep(0)
            outcome = await service.stop_session(
                supabase,
                _Caller(),
                SESSION,
                now=NOW,
                feed=handle,
                loop_task=task,
                release_registrations=_noop_release(),
            )
            assert task.done()
            return outcome

        outcome = _run_coroutine(drive())

        assert observed == ["loop-cancelled", "closing-snapshot"]
        assert outcome.loop_settled is True
        assert outcome.complete is True

    def test_the_task_the_production_spawner_returned_is_what_the_stop_settles(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The two halves of the seam, composed: what ``spawn_session_loop`` made, the stop settles.

        The case above pins the ORDER with a task of its own making; this one pins that the object
        the production spawner hands back through ``start_session`` is accepted by ``loop_task=``
        and is settled before the closing figure. Both halves are needed: a route that held the
        task and a stop that settled a different object would pass either one alone.

        The loop's BODY is replaced, because what one bar does is this file's other twenty cases
        and stepping a real one here would race the stop that is committing finals. Everything
        else - ``spawn_session_loop``, ``asyncio.create_task``, the task's name, the cancellation
        and the await - is production's.
        """
        supabase, account_id = _lifecycle_double("RUNNING")
        handle, _redis = _loop_feed(supabase, [])
        row = supabase.sessions[0]
        observed: List[str] = []

        async def idle() -> int:
            try:
                while True:
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                observed.append("loop-cancelled")
                raise

        def _stub_loop(*args: Any, **kwargs: Any) -> Any:
            # NOT an ``async def``: the coroutine has to exist for ``create_task`` to drive it, and
            # recording the two seam values at construction time is what makes them assertable.
            observed.append(f"config={kwargs['config'] is config}")
            observed.append(f"account={kwargs['account_id'] == account_id}")
            return idle()

        config = _loop_config()
        monkeypatch.setattr(service, "session_loop", _stub_loop)

        def note_insert(_client: FakeSupabase, query: Any) -> None:
            if query.table_name == repo.EQUITY_SNAPSHOTS_TABLE:
                observed.append("closing-snapshot")

        supabase.before_insert = note_insert

        async def drive() -> Tuple[Any, Any]:
            spawn = service.spawn_session_loop(supabase, evaluate=_Runtime([]))
            task = spawn(row, handle, config=config, account_id=account_id)
            await asyncio.sleep(0)
            outcome = await service.stop_session(
                supabase,
                _Caller(),
                SESSION,
                now=NOW,
                feed=handle,
                loop_task=task,
                release_registrations=_noop_release(),
            )
            return task, outcome

        task, outcome = _run_coroutine(drive())

        assert task.get_name() == f"paper-session-loop:{SESSION}"
        assert observed == [
            "config=True",
            "account=True",
            "loop-cancelled",
            "closing-snapshot",
        ]
        assert task.done() is True and task.cancelled() is True
        assert outcome.loop_settled is True
        assert outcome.complete is True

    def test_the_history_stays_readable_after_the_stop(self) -> None:
        """Requirements 17.2 and 17.8: every read still answers, and nothing was deleted.

        The same reads ``/api/paper/sessions/{id}/*`` serves, taken after the stop. A stop that
        tidied up after itself would show here as an empty list where a row was seeded.
        """
        supabase, account_id = _lifecycle_double("RUNNING")
        order = _cancellable_order(supabase, account_id)
        position = _open_position(supabase, account_id)
        # The account has to STATE the equity the position implies, or Requirement 18.3's identity
        # fails and the stop reports the breach instead of closing the curve - which is a different
        # case, asserted by test_stored_figures_that_break_the_equity_identity_are_reported...
        repo.bump_version(
            supabase,
            user_id=USER,
            account_id=account_id,
            expected_version=1,
            payload={"total_equity": CAPITAL_MAJOR + Decimal("30000")},
        )
        trade = repo.insert_trade(
            supabase,
            account_id=account_id,
            user_id=USER,
            session_id=SESSION,
            symbol=SYMBOL,
            side="LONG",
            quantity=Decimal("0.25"),
            entry_price=Decimal("59000"),
            exit_price=Decimal("60000"),
            realized_pnl=Decimal("250"),
            fee_minor=10,
            opened_at=NOW - timedelta(minutes=10),
            closed_at=NOW - timedelta(minutes=2),
        )
        fill = repo.insert_fill(
            supabase,
            order_id=order["id"],
            session_id=SESSION,
            user_id=USER,
            fill_event_id="fill-1",
            quantity=Decimal("0.25"),
            price=Decimal("60000"),
            fee_minor=10,
            slippage_minor=0,
            filled_at=NOW - timedelta(minutes=2),
        )
        handle, _redis = _loop_feed(supabase, [])

        outcome = _run_coroutine(
            service.stop_session(
                supabase,
                _Caller(),
                SESSION,
                now=NOW,
                feed=handle,
                release_registrations=_noop_release(),
            )
        )

        assert outcome.to_state == "STOPPED"
        assert [row["id"] for row in repo.get_orders(supabase, USER, session_id=SESSION)] == [
            order["id"]
        ]
        assert [row["id"] for row in repo.get_fills(supabase, USER, session_id=SESSION)] == [
            fill["id"]
        ]
        assert [row["id"] for row in repo.get_trades(supabase, USER, session_id=SESSION)] == [
            trade["id"]
        ]
        assert [
            row["id"] for row in repo.get_positions(supabase, USER, session_id=SESSION)
        ] == [position["id"]]
        assert len(repo.get_equity_snapshots(supabase, USER, session_id=SESSION)) == 3
        assert repo.get_metrics(supabase, USER, session_id=SESSION) is not None
        assert [row["event_type"] for row in supabase.events] == ["paper_session_stopped"]
        assert outcome.complete is True

    def test_the_stop_persists_the_final_orders_it_does_not_cancel_them(self) -> None:
        """Requirement 17.8 says persist. Cancelling is Requirement 17.15's, and it is the RESET's."""
        supabase, account_id = _lifecycle_double("RUNNING")
        order = _cancellable_order(supabase, account_id)
        handle, _redis = _loop_feed(supabase, [])

        _run_coroutine(
            service.stop_session(
                supabase,
                _Caller(),
                SESSION,
                now=NOW,
                feed=handle,
                release_registrations=_noop_release(),
            )
        )

        after = repo.read_order(supabase, USER, order["id"]) or {}
        assert after["order_state"] == "ACCEPTED"
        assert after["legacy_status"] == "OPEN"

    def test_a_stop_that_released_nothing_is_not_reported_complete(self) -> None:
        """The whole point of :attr:`StopOutcome.complete`. No handle, no Redis, no release."""
        supabase, _account_id = _lifecycle_double("RUNNING")

        outcome = _run_coroutine(
            service.stop_session(
                supabase,
                _Caller(),
                SESSION,
                now=NOW,
                release_registrations=_noop_release(),
            )
        )

        # The transition and the finals DID happen - the session is stopped and its curve is closed.
        assert outcome.to_state == "STOPPED"
        assert outcome.finals.committed is True
        # And the report may NOT be made, because the subscription was never released.
        assert outcome.mds_released is False
        assert outcome.subscription_released is False
        assert outcome.complete is False

    def test_a_stop_whose_registrations_could_not_be_closed_is_not_complete(self) -> None:
        """Requirement 19.12 unmet is reported, not swallowed - and it is not a raise either."""
        supabase, _account_id = _lifecycle_double("RUNNING")
        handle, _redis = _loop_feed(supabase, [])

        async def refuse(_session_id: Any) -> Tuple[Any, ...]:
            raise RuntimeError("the registry is unavailable")

        outcome = _run_coroutine(
            service.stop_session(
                supabase,
                _Caller(),
                SESSION,
                now=NOW,
                feed=handle,
                release_registrations=refuse,
            )
        )

        assert outcome.mds_released is True
        assert outcome.subscription_released is True
        # ``None`` and not ``0``: nobody-was-watching is 0, and this is "the release did not run".
        assert outcome.registrations_closed is None
        assert outcome.complete is False

    def test_a_second_stop_is_refused_by_the_state_machine_and_releases_nothing(self) -> None:
        """Requirement 17.14 on the stop path, and the prefix the module records as unretryable.

        ``stop`` is idempotent in the sense the state machine gives it: the second attempt is a 409
        naming ``STOPPED`` and ``stop``, and it does NOT reach the release path - which is why
        ``release_session_resources`` is public.
        """
        supabase, _account_id = _lifecycle_double("RUNNING")
        handle, redis = _loop_feed(supabase, [])

        first = _run_coroutine(
            service.stop_session(
                supabase,
                _Caller(),
                SESSION,
                now=NOW,
                feed=handle,
                release_registrations=_noop_release(),
            )
        )
        assert first.complete is True
        redis.log.clear()
        before = len(supabase.statements)

        with pytest.raises(service.PaperSessionOperationRejected) as caught:
            _run_coroutine(
                service.stop_session(
                    supabase,
                    _Caller(),
                    SESSION,
                    now=NOW,
                    feed=handle,
                    release_registrations=_noop_release(),
                )
            )

        assert caught.value.details["session_state"] == "STOPPED"
        assert caught.value.details["operation"] == "stop"
        # Nothing published, nothing written: one read and no more.
        assert redis.log == []
        assert [
            statement.op for statement in supabase.statements[before:]
        ] == ["select"]
        assert len(supabase.events) == 1

    def test_a_second_release_publishes_no_second_unsubscribe(self) -> None:
        """``FeedHandle.close`` is idempotent, and two unsubscribes are not a repeat of one."""
        supabase, _account_id = _lifecycle_double("RUNNING")
        handle, redis = _loop_feed(supabase, [])

        _run_coroutine(
            service.stop_session(
                supabase,
                _Caller(),
                SESSION,
                now=NOW,
                feed=handle,
                release_registrations=_noop_release(),
            )
        )
        assert len(redis.commands()) == 2  # the subscribe at open, then this unsubscribe

        again = _run_coroutine(
            service.release_session_resources(
                SESSION, feed=handle, release_registrations=_noop_release()
            )
        )

        assert again[0] is False
        assert len(redis.commands()) == 2

    def test_a_worker_without_the_handle_still_publishes_the_mds_unsubscribe(self) -> None:
        """The cross-worker gap, reported rather than papered over.

        The ``mds`` half is process-independent and is released; the LOCAL pubsub handle belongs to
        the worker that opened it, so it is reported outstanding and the stop is not complete.
        """
        supabase, _account_id = _lifecycle_double("RUNNING")
        redis = RecordingRedis()

        outcome = _run_coroutine(
            service.stop_session(
                supabase,
                _Caller(),
                SESSION,
                now=NOW,
                redis=redis,
                release_registrations=_noop_release(),
            )
        )

        assert redis.commands() == [
            {"action": "unsubscribe", "exchange": EXCHANGE, "symbol": SYMBOL}
        ]
        assert outcome.mds_released is True
        assert outcome.subscription_released is False
        assert outcome.complete is False

    def test_an_open_position_with_no_validated_price_writes_no_closing_point(self) -> None:
        """Requirement 18.15: nothing is interpolated, nothing is zeroed, and it is REPORTED."""
        supabase, account_id = _lifecycle_double("RUNNING")
        repo.upsert_position(
            supabase,
            account_id=account_id,
            user_id=USER,
            session_id=SESSION,
            symbol=SYMBOL,
            side="LONG",
            size=Decimal("0.5"),
            entry_price=Decimal("60000"),
            opened_at=NOW - timedelta(minutes=5),
            # No ``current_price``: this session has never had a validated price for the symbol.
        )
        handle, _redis = _loop_feed(supabase, [])

        outcome = _run_coroutine(
            service.stop_session(
                supabase,
                _Caller(),
                SESSION,
                now=NOW,
                feed=handle,
                release_registrations=_noop_release(),
            )
        )

        assert outcome.finals.reason == service.FINALS_NO_VALIDATED_PRICE
        assert outcome.finals.snapshot is None
        assert outcome.finals.metrics_row is None
        assert outcome.complete is False
        # No point was appended and no figure was invented.
        assert len(repo.get_equity_snapshots(supabase, USER, session_id=SESSION)) == 2
        assert repo.get_metrics(supabase, USER, session_id=SESSION) is None
        # The frame says "could not be computed" rather than reporting a flat session (Req 28.5).
        assert _payload_of(supabase, "paper_session_stopped")["final_metrics"] is None
        # And the subscription was still released: a stop that abandoned the release because a
        # figure was uncomputable would leave the session holding a stream.
        assert outcome.mds_released is True

    def test_a_stop_with_an_open_priced_position_marks_the_closing_point_stale(self) -> None:
        """A stop supplies no bar, so an open position is valued at its LAST validated price."""
        supabase, account_id = _lifecycle_double("RUNNING")
        _open_position(supabase, account_id)
        # The account has to state the equity identity the position implies, or the stop reports the
        # violation instead of snapshotting it - which is a different case, asserted below.
        repo.bump_version(
            supabase,
            user_id=USER,
            account_id=account_id,
            expected_version=1,
            payload={"total_equity": CAPITAL_MAJOR + Decimal("30000")},
        )
        handle, _redis = _loop_feed(supabase, [])

        outcome = _run_coroutine(
            service.stop_session(
                supabase,
                _Caller(),
                SESSION,
                now=NOW,
                feed=handle,
                release_registrations=_noop_release(),
            )
        )

        closing = outcome.finals.snapshot or {}
        assert closing["stale"] is True
        assert Decimal(str(closing["position_market_value"])) == Decimal("30000")
        assert outcome.complete is True

    def test_stored_figures_that_break_the_equity_identity_are_reported_not_snapshotted(
        self,
    ) -> None:
        """Requirement 18.3 admits zero tolerance, and a breach must not reach the curve."""
        supabase, account_id = _lifecycle_double("RUNNING")
        _open_position(supabase, account_id)
        handle, _redis = _loop_feed(supabase, [])

        outcome = _run_coroutine(
            service.stop_session(
                supabase,
                _Caller(),
                SESSION,
                now=NOW,
                feed=handle,
                release_registrations=_noop_release(),
            )
        )

        assert outcome.finals.reason == service.FINALS_INVARIANT_VIOLATED
        assert outcome.finals.snapshot is None
        assert outcome.complete is False
        assert len(repo.get_equity_snapshots(supabase, USER, session_id=SESSION)) == 2


class TestTheResetRestoresAndDeletesNothing:
    """Requirement 17.15, including the half that is about what is still there afterwards.

    Red run 1 (the order): the balance return was moved BELOW the ``apply_operation`` call, the
    "transition first" shape. ``test_the_reset_body_runs_before_the_transition`` failed on the
    ordered log - the ``paper_sessions`` state UPDATE appeared before the account UPDATE, which is
    the prefix that would leave a ``CREATED`` session holding the previous series' money with
    ``start`` legal and ``reset`` not.

    Red run 2 (nothing deleted): ``repo.get_orders`` in the cancel loop was changed to filter
    ``legacy_status='OPEN'`` and the closed positions were re-inserted rather than updated. The
    ``series_index`` and cancellation cases stayed green; ``test_nothing_is_deleted_and_the_two_series_are_distinguishable``
    failed on the position count, which is the pair that shows "the reset worked" and "the reset
    destroyed nothing" are asserted separately.
    """

    def test_the_balances_return_to_exactly_initial_capital_minor(self) -> None:
        """Requirement 17.15's "the recorded initial simulated capital", to the minor unit."""
        supabase, account_id = _lifecycle_double("STOPPED")
        repo.bump_version(
            supabase,
            user_id=USER,
            account_id=account_id,
            expected_version=1,
            payload={
                "available_balance": Decimal("40000"),
                "locked_balance": Decimal("10000"),
                "realized_pnl": Decimal("-50000"),
                "total_equity": Decimal("50000"),
            },
        )

        outcome = _run_coroutine(
            service.reset_session(supabase, _Caller(), SESSION, now=NOW)
        )

        assert outcome.to_state == "CREATED"
        # The figure is the one the session RECORDED, as the exact integer the column holds.
        assert outcome.initial_capital_minor == CAPITAL_MINOR
        account = repo.read_account(supabase, USER, "USD", SESSION) or {}
        assert Decimal(str(account["available_balance"])) == CAPITAL_MAJOR
        assert Decimal(str(account["locked_balance"])) == Decimal("0")
        assert Decimal(str(account["realized_pnl"])) == Decimal("0")
        assert Decimal(str(account["total_equity"])) == CAPITAL_MAJOR
        assert account["stale"] is False
        assert account["last_price_at"] is None

    def test_the_movement_is_recorded_in_the_balance_ledger_under_reset(self) -> None:
        """``chk_paper_balance_event_cause``'s fifth value, and what makes the return auditable."""
        supabase, account_id = _lifecycle_double("STOPPED")
        repo.bump_version(
            supabase,
            user_id=USER,
            account_id=account_id,
            expected_version=1,
            payload={
                "available_balance": Decimal("40000"),
                "locked_balance": Decimal("10000"),
                "realized_pnl": Decimal("-50000"),
                "total_equity": Decimal("50000"),
            },
        )

        _run_coroutine(service.reset_session(supabase, _Caller(), SESSION, now=NOW))

        events = repo.get_balance_events(supabase, USER, session_id=SESSION)
        assert [row["cause"] for row in events] == ["RESET"]
        row = events[0]
        assert Decimal(str(row["available_delta"])) == CAPITAL_MAJOR - Decimal("40000")
        assert Decimal(str(row["locked_delta"])) == Decimal("-10000")
        assert Decimal(str(row["realized_delta"])) == Decimal("50000")
        assert Decimal(str(row["available_after"])) == CAPITAL_MAJOR
        assert Decimal(str(row["locked_after"])) == Decimal("0")
        assert Decimal(str(row["realized_after"])) == Decimal("0")

    def test_every_open_order_becomes_cancelled(self) -> None:
        """Requirement 17.15's "no open paper order", on both states that mean open."""
        supabase, account_id = _lifecycle_double("STOPPED")
        accepted = _cancellable_order(supabase, account_id, key="idem-a")
        partial = _cancellable_order(
            supabase,
            account_id,
            state=PaperOrderState.PARTIALLY_FILLED,
            key="idem-b",
        )
        filled = _cancellable_order(
            supabase, account_id, state=PaperOrderState.FILLED, key="idem-c"
        )

        outcome = _run_coroutine(
            service.reset_session(supabase, _Caller(), SESSION, now=NOW)
        )

        assert set(outcome.cancelled_orders) == {accepted["id"], partial["id"]}
        for order_id in (accepted["id"], partial["id"]):
            after = repo.read_order(supabase, USER, order_id) or {}
            assert after["order_state"] == "CANCELLED"
            assert after["legacy_status"] == "CANCELLED"
        # A terminal order is left alone: Requirement 16.3 permits no transition out of FILLED.
        assert (repo.read_order(supabase, USER, filled["id"]) or {})["order_state"] == "FILLED"
        assert repo.get_orders(supabase, USER, session_id=SESSION, legacy_status="OPEN") == []

    def test_an_order_stuck_at_created_is_reported_rather_than_forced(self) -> None:
        """Requirement 16.2 gives ``CREATED`` no ``CANCELLED`` edge, and it is not OPEN either.

        Forcing it would be the ``23514`` ``trg_paper_order_transition_guard`` answers with, so the
        reset reports it. Requirement 17.15 is still satisfied: ``legacy_status`` is ``NEW``.
        """
        supabase, account_id = _lifecycle_double("STOPPED")
        created = _cancellable_order(
            supabase, account_id, state=PaperOrderState.CREATED, key="idem-d"
        )

        outcome = _run_coroutine(
            service.reset_session(supabase, _Caller(), SESSION, now=NOW)
        )

        assert outcome.cancelled_orders == ()
        assert outcome.orders_not_cancellable == (created["id"],)
        after = repo.read_order(supabase, USER, created["id"]) or {}
        assert after["order_state"] == "CREATED"
        assert after["legacy_status"] == "NEW"
        assert repo.get_orders(supabase, USER, session_id=SESSION, legacy_status="OPEN") == []

    def test_every_open_position_closes_to_exactly_zero_and_is_not_deleted(self) -> None:
        """Requirement 18.5: exactly zero with ``closed_at`` set, never a removed row."""
        supabase, account_id = _lifecycle_double("STOPPED")
        position = _open_position(supabase, account_id)

        outcome = _run_coroutine(
            service.reset_session(supabase, _Caller(), SESSION, now=NOW)
        )

        assert outcome.closed_positions == (SYMBOL,)
        # ``include_closed=True``, because the default read is open-only - and the whole claim here
        # is that the row is STILL THERE at size zero rather than gone.
        rows = repo.get_positions(
            supabase, USER, session_id=SESSION, include_closed=True
        )
        assert [row["id"] for row in rows] == [position["id"]]
        assert repo.get_positions(supabase, USER, session_id=SESSION) == []
        assert Decimal(str(rows[0]["size"])) == Decimal("0")
        assert rows[0]["closed_at"] == NOW.isoformat()
        # The measurements that WERE taken are kept; the side is what the position was.
        assert rows[0]["side"] == "LONG"
        assert Decimal(str(rows[0]["current_price"])) == Decimal("60000")
        assert Decimal(str(rows[0]["unrealized_pnl"])) == Decimal("0")
        # A reset is not a liquidation: no round-trip is invented for the discarded quantity.
        assert repo.get_trades(supabase, USER, session_id=SESSION) == []

    def test_a_new_equity_series_begins_at_previous_plus_one(self) -> None:
        """Requirement 17.15's new series, and the index is the previous one plus exactly one."""
        supabase, _account_id = _lifecycle_double("STOPPED")

        outcome = _run_coroutine(
            service.reset_session(supabase, _Caller(), SESSION, now=NOW)
        )

        assert outcome.previous_series_index == 0
        assert outcome.series_index == 1
        opening = outcome.opening_snapshot or {}
        assert opening["series_index"] == 1
        assert opening["cause"] == "SESSION_START"
        assert Decimal(str(opening["total_equity"])) == CAPITAL_MAJOR
        assert Decimal(str(opening["position_market_value"])) == Decimal("0")
        assert opening["stale"] is False

    def test_a_second_reset_begins_a_third_series_rather_than_reusing_the_second(self) -> None:
        """``previous + 1`` is read from the rows, so it counts up rather than being assumed."""
        supabase, _account_id = _lifecycle_double("STOPPED")

        first = _run_coroutine(
            service.reset_session(supabase, _Caller(), SESSION, now=NOW)
        )
        # Back to STOPPED the only way the machine allows: start, then stop.
        supabase.sessions[0]["session_state"] = "STOPPED"
        second = _run_coroutine(
            service.reset_session(supabase, _Caller(), SESSION, now=NOW)
        )

        assert (first.series_index, second.series_index) == (1, 2)
        assert sorted(
            {
                int(row["series_index"])
                for row in repo.get_equity_snapshots(supabase, USER, session_id=SESSION)
            }
        ) == [0, 1, 2]

    def test_nothing_is_deleted_and_the_two_series_are_distinguishable(self) -> None:
        """The other half of Requirement 17.15, and the honest scope of ``series_index``.

        Everything seeded before the reset is still selectable afterwards. The two equity SERIES are
        separable by ``series_index`` - the only table 009 gives that column - while the orders,
        fills, trades and metrics of the two are separated by time, which is stated in
        ``reset_session``'s docstring rather than implied here.
        """
        supabase, account_id = _lifecycle_double("STOPPED")
        order = _cancellable_order(supabase, account_id)
        position = _open_position(supabase, account_id)
        fill = repo.insert_fill(
            supabase,
            order_id=order["id"],
            session_id=SESSION,
            user_id=USER,
            fill_event_id="fill-1",
            quantity=Decimal("0.25"),
            price=Decimal("60000"),
            fee_minor=10,
            slippage_minor=0,
            filled_at=NOW - timedelta(minutes=3),
        )
        trade = repo.insert_trade(
            supabase,
            account_id=account_id,
            user_id=USER,
            session_id=SESSION,
            symbol=SYMBOL,
            side="LONG",
            quantity=Decimal("0.25"),
            entry_price=Decimal("59000"),
            exit_price=Decimal("60000"),
            realized_pnl=Decimal("250"),
            fee_minor=10,
            opened_at=NOW - timedelta(minutes=10),
            closed_at=NOW - timedelta(minutes=2),
        )
        metrics = repo.insert_metrics(
            supabase,
            user_id=USER,
            session_id=SESSION,
            computed_at=NOW - timedelta(minutes=1),
            realized_pnl=Decimal("250"),
            closed_trade_count=1,
            win_rate=Decimal("1"),
        )
        before = [dict(row) for row in supabase.equity_snapshots]

        _run_coroutine(service.reset_session(supabase, _Caller(), SESSION, now=NOW))

        # Not one DELETE was issued.
        assert {statement.op for statement in supabase.statements} <= {
            "select",
            "insert",
            "update",
        }
        assert [row["id"] for row in repo.get_orders(supabase, USER, session_id=SESSION)] == [
            order["id"]
        ]
        assert [row["id"] for row in repo.get_fills(supabase, USER, session_id=SESSION)] == [
            fill["id"]
        ]
        assert [row["id"] for row in repo.get_trades(supabase, USER, session_id=SESSION)] == [
            trade["id"]
        ]
        assert [
            row["id"]
            for row in repo.get_positions(
                supabase, USER, session_id=SESSION, include_closed=True
            )
        ] == [position["id"]]
        assert (repo.get_metrics(supabase, USER, session_id=SESSION) or {})["id"] == metrics["id"]

        # And the pre-reset series is intact, distinguished from the new one by series_index.
        series = repo.get_equity_snapshots(supabase, USER, session_id=SESSION)
        old = [row for row in series if int(row["series_index"]) == 0]
        new = [row for row in series if int(row["series_index"]) == 1]
        assert [row["id"] for row in old] == [row["id"] for row in before]
        assert [row["cause"] for row in new] == ["SESSION_START"]

    def test_the_reset_is_recorded_as_paper_session_stopped_with_created(self) -> None:
        """Requirement 19.2 fixes SIXTEEN event types and none of them means "reset".

        A seventeenth would be a ``23514`` from ``chk_paper_event_type`` in production, so the reset
        is recorded under the nearest of the sixteen and ``session_state`` carries the truth.
        ``final_metrics`` is ``None`` on purpose: the session a client is now watching has realized
        nothing, so reporting the pre-reset figures here would misdescribe it.
        """
        supabase, _account_id = _lifecycle_double("STOPPED")

        _run_coroutine(service.reset_session(supabase, _Caller(), SESSION, now=NOW))

        assert [row["event_type"] for row in supabase.events] == ["paper_session_stopped"]
        payload = _payload_of(supabase, "paper_session_stopped")
        assert payload["session_state"] == "CREATED"
        assert payload["actor_id"] == USER
        assert payload["final_metrics"] is None
        assert "paper_session_reset" not in set(repo.PAPER_EVENT_TYPES)
        assert len(repo.PAPER_EVENT_TYPES) == 16

    def test_the_reset_body_runs_before_the_transition(self) -> None:
        """The prefix argument, as an ordered log.

        Cancel, then the balances and their ledger row, then the positions, then the new series, and
        the ``paper_sessions`` state UPDATE LAST. A reset that transitioned first and died would
        leave a ``CREATED`` session holding the previous series' money, with ``start`` legal from
        there and ``reset`` not.
        """
        supabase, account_id = _lifecycle_double("STOPPED")
        _cancellable_order(supabase, account_id)
        _open_position(supabase, account_id)
        trace: List[Tuple[str, str]] = []
        _traced(supabase, trace)

        _run_coroutine(service.reset_session(supabase, _Caller(), SESSION, now=NOW))

        assert trace == [
            ("update", repo.ORDERS_TABLE),
            ("update", repo.ACCOUNTS_TABLE),
            ("insert", repo.BALANCE_EVENTS_TABLE),
            ("update", repo.POSITIONS_TABLE),
            ("insert", repo.EQUITY_SNAPSHOTS_TABLE),
            # The guarded STOPPED -> CREATED transition, last of the state writes.
            ("update", repo.SESSIONS_TABLE),
            # Then the record: ``allocate_session_event_sequence`` bumps
            # ``paper_sessions.event_sequence`` and the row lands after it. Both are listed rather
            # than filtered out, because a trace that hid a write would not be a trace.
            ("update", repo.SESSIONS_TABLE),
            ("insert", repo.EVENTS_TABLE),
        ]

    def test_the_account_update_is_guarded_on_the_version_that_was_read(self) -> None:
        """This transport's substitute for ``SELECT ... FOR UPDATE``, on the money statement."""
        supabase, _account_id = _lifecycle_double("STOPPED")

        _run_coroutine(service.reset_session(supabase, _Caller(), SESSION, now=NOW))

        update = supabase.statements_on(repo.ACCOUNTS_TABLE, "update")[-1]
        assert update.filtered_columns() == {"id", "user_id", "version"}
        assert update.filter_value("version") == 1

    def test_the_order_cancellation_is_guarded_on_the_state_that_was_read(self) -> None:
        """The same substitute, on the order statement: a fill in between must make it lose."""
        supabase, account_id = _lifecycle_double("STOPPED")
        _cancellable_order(supabase, account_id)

        _run_coroutine(service.reset_session(supabase, _Caller(), SESSION, now=NOW))

        update = supabase.statements_on(repo.ORDERS_TABLE, "update")[-1]
        assert update.filtered_columns() == {"id", "user_id", "order_state"}
        assert update.filter_value("order_state") == "ACCEPTED"

    @pytest.mark.parametrize("state", ["CREATED", "RUNNING", "PAUSED"])
    def test_a_reset_from_a_state_that_does_not_permit_it_writes_nothing(
        self, state: str
    ) -> None:
        """Requirement 17.14 on the reset path, and it is a fact about statements not issued.

        The gate runs before the body, so the balances, the orders, the positions and the history
        are all untouched - not restored afterwards.
        """
        supabase, account_id = _lifecycle_double(state)
        order = _cancellable_order(supabase, account_id)
        _open_position(supabase, account_id)
        repo.bump_version(
            supabase,
            user_id=USER,
            account_id=account_id,
            expected_version=1,
            payload={"available_balance": Decimal("40000")},
        )
        before = len(supabase.statements)

        with pytest.raises(service.PaperSessionOperationRejected) as caught:
            _run_coroutine(
                service.reset_session(supabase, _Caller(), SESSION, now=NOW)
            )

        assert caught.value.http_status == 409
        assert caught.value.details["session_state"] == state
        assert caught.value.details["operation"] == "reset"
        assert [
            statement.op for statement in supabase.statements[before:]
        ] == ["select"]
        assert supabase.sessions[0]["session_state"] == state
        assert Decimal(
            str((repo.read_account(supabase, USER, "USD", SESSION) or {})["available_balance"])
        ) == Decimal("40000")
        assert (repo.read_order(supabase, USER, order["id"]) or {})["order_state"] == "ACCEPTED"
        assert supabase.events == []

    def test_the_final_state_satisfies_the_accounting_invariants(self) -> None:
        """Requirement 18.3, 18.4 and 18.5 on the rows the body wrote, checked by the engine."""
        supabase, account_id = _lifecycle_double("STOPPED")
        _open_position(supabase, account_id)
        repo.bump_version(
            supabase,
            user_id=USER,
            account_id=account_id,
            expected_version=1,
            payload={"total_equity": CAPITAL_MAJOR + Decimal("30000")},
        )

        _run_coroutine(service.reset_session(supabase, _Caller(), SESSION, now=NOW))

        account = sim.account_of(repo.read_account(supabase, USER, "USD", SESSION) or {})
        positions = sim.positions_of(
            repo.get_positions(supabase, USER, session_id=SESSION)
        )
        # No open position, so no price is needed and none is invented.
        accounting.assert_invariants(account, positions, {}, _frozen_config().accounting())
        assert account.total_equity == CAPITAL_MAJOR

    def test_recorded_capital_reads_the_stored_figure_and_refuses_a_broken_one(self) -> None:
        """The conversion is the one ``validate_capital`` performs, without its policy maximum.

        Re-running the start-time validation here would refuse the reset of a session started under
        a per-session maximum that has since been lowered - a refusal Requirement 17.15 does not
        license. What IS checked is what has to be true of a stored figure.
        """
        capital = service.recorded_capital(CAPITAL_MINOR, "usd")
        assert capital.minor == CAPITAL_MINOR
        assert capital.major == CAPITAL_MAJOR
        assert capital.currency == "USD"

        # A capital far above the configured per-session maximum still resets, because the maximum
        # is a policy about new requests and this figure is already recorded.
        huge = service.recorded_capital(10_000_000_000_000, "USD")
        assert huge.minor == 10_000_000_000_000

        for broken in (0, -1, Decimal("100.5"), 1.5, None, "not-a-number"):
            with pytest.raises(ValueError):
                service.recorded_capital(broken, "USD")
        with pytest.raises(ValueError):
            service.recorded_capital(CAPITAL_MINOR, "ZZZ")


def _noop_release() -> Any:
    """A Paper_Channel release that closes nothing, for the cases that are not about Req 19.12.

    It returns an empty tuple, which is what ``release_session`` returns for a session nobody is
    watching - so ``registrations_closed`` is ``0`` and not ``None``, and those cases can still
    assert ``complete``. The real seam is exercised by
    :func:`TestTheStopCommitsThenReleasesThenReports.
    test_the_stopped_frame_reaches_a_subscriber_before_its_registration_is_closed`.
    """

    async def release(_session_id: Any) -> Tuple[Any, ...]:
        return ()

    return release


# ══════════════════════════════════════════════════════════════════════════
#  8. THE SESSION LOOP (task 27.2 - Requirements 17.10, 23.1, 23.5, 27.3)
# ══════════════════════════════════════════════════════════════════════════
#
# The loop is driven ONE EVENT AT A TIME through ``step_session``, which is why that function
# exists separately from ``session_loop``: "the per-event order is the contract" is a claim about
# one call, and asserting it against a background task would be a race. The one case that genuinely
# needs the task - cancellation - creates it and awaits it inside a single ``_run_coroutine``
# drive, so the harness's loop is as idle after it as before (see ``_HARNESS_LOOP``).
#
# Every double here is one this repository already has: ``FakeSupabase`` for the Persistence_Layer,
# ``RecordingRedis`` + the real ``open_feed`` / ``next_validated_event`` for the market feed, and
# the real ``paper_simulator`` for execution. The only stand-ins written here are for the two
# collaborators the loop deliberately does not own - the platform's DAG execution runtime and the
# Signal_Trace recorder - because those are the seams Requirements 17.10 and 23.5 make them.


#: The instant ``paper_market_feed`` reads as "now". Pinned, so ``received_at`` (which every frame
#: this loop emits is stamped with) and ``latency_ms`` are exact figures rather than a range.
LOOP_NOW = PROCESSED_AT

#: The bar the frames below carry: one minute before :data:`LOOP_NOW`.
BAR_CLOSE = Decimal("60000.5")


class _Runtime:
    """A stand-in for the platform's DAG execution runtime: ``evaluate(plan, event)``.

    NOT a stand-in for anything under test. Requirement 17.10 makes the runtime the platform's one
    existing evaluator and the loop treats it as opaque, so what a test needs to control is what it
    returned for a bar - and what a test needs to observe is that it was called once per bar, off
    the event loop's thread, with the plan it was given.

    ``batches[i]`` is what the i-th bar produces; bars beyond the list produce nothing, which is
    the common case for a candle.
    """

    def __init__(self, *batches: Any) -> None:
        self.batches: List[Any] = list(batches)
        self.calls: List[Tuple[Any, Any]] = []

    def __call__(self, plan: Any, event: Any) -> Any:
        index = len(self.calls)
        self.calls.append((plan, event))
        return self.batches[index] if index < len(self.batches) else ()


class _Recorder:
    """A stand-in for the Signal_Trace recorder (task 29.2 owns the write).

    Records the three things Requirement 23.1 and 23.5 are about - the signal, the
    Execution_Environment and the Paper_Session identifier - plus ``events_before``, which is how
    many ``paper_events`` rows existed at the moment it was called. That last one is what makes
    "recorded before broadcast" assertable without a clock or a shared mutable log: a recorder
    called after the frame would see the frame's row.
    """

    def __init__(self, supabase: Optional[FakeSupabase] = None) -> None:
        self.supabase = supabase
        self.calls: List[Dict[str, Any]] = []

    async def __call__(
        self, signal: Any, *, environment: str, paper_session_id: Any
    ) -> None:
        self.calls.append(
            {
                "signal": signal,
                "environment": environment,
                "paper_session_id": paper_session_id,
                "events_before": len(self.supabase.events)
                if self.supabase is not None
                else None,
            }
        )


class _LoopCase:
    """Mixin for the cases that drive a real feed: it pins the one clock on that path.

    ``paper_market_feed._utc_now`` is what ``next_validated_event`` reads for ``received_at`` and
    for Requirement 14.10's ``latency_ms``, and both reach a frame this loop emits - so an unpinned
    clock would make ``market_tick``'s latency depend on when the suite ran. Pinned on the CLASS
    rather than for the module, so the 27.1 and 27.3 cases above keep the clock they were written
    against.

    It changes no price: the five OHLCV values and the bar's own instant come from the frame, which
    is what keeps a session replayable (Requirement 15.4).
    """

    @pytest.fixture(autouse=True)
    def _pinned_feed_clock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(feed, "_utc_now", lambda: LOOP_NOW)


def _loop_config() -> sim.SessionConfig:
    """The session's frozen configuration - :func:`_frozen_config`, under the loop's own name."""
    return _frozen_config()


def _loop_double(
    *,
    feed_state: str = "HEALTHY",
    capital: Decimal = Decimal("100000"),
) -> Tuple[FakeSupabase, Dict[str, Any], str]:
    """A double carrying one ``RUNNING`` session and its isolated Paper_Account.

    The account is created through ``paper_repository.get_or_create_account``, so the premise is
    the premise ``start_session`` leaves behind. The setup's own statements are cleared, so what a
    case reads afterwards is what the loop issued.
    """
    repo.reset_persistence_probe()
    paper_channel.invalidate_session_owner()
    row = _running_session()
    row["feed_state"] = feed_state
    supabase = FakeSupabase(sessions=[row])
    account = repo.get_or_create_account(
        supabase, USER, "USD", SESSION, initial_capital=capital
    )
    supabase.statements.clear()
    supabase.ops.clear()
    return supabase, row, str(account["id"])


def _loop_feed(
    supabase: FakeSupabase, frames: List[Any]
) -> Tuple[feed.FeedHandle, RecordingRedis]:
    """An open feed on this session's own ``(exchange, symbol)``, holding ``frames``."""
    redis = RecordingRedis(frames=list(frames))
    handle = _run_coroutine(
        feed.open_feed(
            feed.PaperFeedConfig(
                session_id=SESSION,
                user_id=USER,
                exchange_id=EXCHANGE,
                symbol=SYMBOL,
                timeframe=TIMEFRAME,
            ),
            exchange=None,
            redis=redis,
            supabase=supabase,
            measurements=_admitting(),
        )
    )
    return handle, redis


def _signal(**overrides: Any) -> Dict[str, Any]:
    """One signal as the existing signal-generation path's public shape.

    Every numeric value is a decimal STRING, not a float. That is the wire this loop admits, and
    it is asserted directly by
    :func:`test_a_float_quantity_is_refused_rather_than_converted`: ``0.5`` is not one half in
    binary and ``paper_accounting.to_decimal`` refuses it (Requirement 18.1).
    """
    payload: Dict[str, Any] = {
        "signal_id": "11111111-aaaa-4aaa-8aaa-000000000001",
        "decision": "BUY",
        "symbol": SYMBOL,
        "side": "buy",
        "quantity": "0.5",
        "price": "60000",
        "order_lifecycle_state": "GENERATED",
        "generated_at": (LOOP_NOW - timedelta(minutes=1)).isoformat(),
    }
    payload.update(overrides)
    return {key: value for key, value in payload.items() if value is not None}


def _step(
    supabase: FakeSupabase,
    session: Dict[str, Any],
    handle: feed.FeedHandle,
    *,
    account_id: str,
    **kwargs: Any,
) -> service.SessionStep:
    """One ``step_session`` call, with the frozen configuration supplied."""
    config = kwargs.pop("config", None) or _loop_config()
    return _run_coroutine(
        service.step_session(
            supabase,
            session,
            handle,
            config=config,
            account_id=account_id,
            **kwargs,
        )
    )


def _recorded_types(supabase: FakeSupabase) -> List[str]:
    """Every ``paper_events`` row's type, in the order the sequence allocator issued them."""
    return [
        str(row["event_type"])
        for row in sorted(supabase.events, key=lambda r: int(r["sequence"]))
    ]


def _payload_of(supabase: FakeSupabase, event_type: str) -> Dict[str, Any]:
    """The one recorded payload of ``event_type``."""
    rows = [row for row in supabase.events if str(row["event_type"]) == event_type]
    assert len(rows) == 1, f"expected exactly one {event_type}, got {len(rows)}"
    return dict(rows[0]["payload"])


class TestThePerEventOrder(_LoopCase):
    """Task 27.2's per-event sequence, asserted as the sequence.

    Red run: the ``market_tick`` emission was moved below the DAG step. The recorded order became
    ``signal_generated, market_tick, ...`` and
    :func:`test_the_tick_is_recorded_before_anything_the_bar_caused` failed on the list, naming the
    two frames that swapped - which is the failure a reader can act on, as opposed to a sequence
    number being one lower than expected somewhere.
    """

    def test_the_tick_is_recorded_before_anything_the_bar_caused(self) -> None:
        supabase, row, account_id = _loop_double()
        handle, _ = _loop_feed(supabase, [_frame()])
        runtime = _Runtime([_signal()])

        step = _step(
            supabase, row, handle, account_id=account_id, evaluate=runtime, plan="plan-1"
        )

        assert step.dropped is False
        assert _recorded_types(supabase) == [
            "market_tick",
            "signal_generated",
            "paper_pnl_updated",
            "paper_drawdown_updated",
        ]

    def test_the_sequence_is_one_per_frame_with_no_gap(self) -> None:
        """Requirement 19.3: 1 for the first, +1 for each after it, per session."""
        supabase, row, account_id = _loop_double()
        handle, _ = _loop_feed(supabase, [_frame()])

        _step(
            supabase,
            row,
            handle,
            account_id=account_id,
            evaluate=_Runtime([_signal()]),
        )

        assert sorted(int(row["sequence"]) for row in supabase.events) == [1, 2, 3, 4]

    def test_the_step_reports_what_it_did_rather_than_what_it_intended(self) -> None:
        supabase, row, account_id = _loop_double()
        handle, _ = _loop_feed(supabase, [_frame()])

        step = _step(
            supabase,
            row,
            handle,
            account_id=account_id,
            evaluate=_Runtime([_signal()]),
        )

        assert step.event is not None
        assert step.event.close == BAR_CLOSE
        assert [s.signal_id for s in step.signals] == [_signal()["signal_id"]]
        assert len(step.submissions) == 1
        assert step.submissions[0].accepted is True
        assert step.skipped == ()
        assert step.snapshot is not None
        assert step.stale is False

    def test_a_dropped_event_emits_nothing_and_writes_nothing(self) -> None:
        """Requirement 14.7: a duplicate or invalid candle is a no-op, not an error.

        The frame names another market, which ``next_validated_event`` drops with a counted
        reason. Nothing about the session may move on a bar it never received.
        """
        supabase, row, account_id = _loop_double()
        handle, _ = _loop_feed(
            supabase, [_frame(overrides={"symbol": "ETH/USDT"})]
        )
        runtime = _Runtime([_signal()])

        step = _step(
            supabase, row, handle, account_id=account_id, evaluate=runtime
        )

        assert step.dropped is True
        assert supabase.events == []
        assert runtime.calls == [], "the DAG was stepped on a bar the feed refused"
        assert supabase.orders == []
        assert supabase.equity_snapshots == []


class TestTheDagStepIsOffloaded(_LoopCase):
    """Requirement 27.3: the one CPU-bound step goes through ``run_in_threadpool``.

    Red run: ``await run_in_threadpool(evaluate, plan, event)`` was replaced with
    ``evaluate(plan, event)``. :func:`test_the_runtime_is_called_through_run_in_threadpool` failed
    on the spy never being called, and
    :func:`test_step_session_never_calls_the_runtime_directly` failed on the AST - which is the one
    that would still fail if a future edit reached for a thread another way.
    """

    def test_the_runtime_is_called_through_run_in_threadpool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        supabase, row, account_id = _loop_double()
        handle, _ = _loop_feed(supabase, [_frame()])
        runtime = _Runtime([_signal()])
        offloaded: List[Tuple[Any, ...]] = []
        real = service.run_in_threadpool

        async def spy(func: Any, *args: Any, **kwargs: Any) -> Any:
            offloaded.append((func, *args))
            return await real(func, *args, **kwargs)

        monkeypatch.setattr(service, "run_in_threadpool", spy)

        step = _step(
            supabase, row, handle, account_id=account_id, evaluate=runtime, plan="plan-1"
        )

        assert len(offloaded) == 1, (
            "the DAG evaluation for a bar must be offloaded exactly once; a slow indicator that "
            "ran on the event loop's thread would stall every HTTP handler in the process "
            "(Requirement 27.3)"
        )
        assert offloaded[0][0] is runtime
        assert offloaded[0][1] == "plan-1"
        assert offloaded[0][2] is step.event
        assert runtime.calls[0][0] == "plan-1"

    def test_step_session_never_calls_the_runtime_directly(self) -> None:
        """``evaluate`` appears as an ARGUMENT to the offload, never as a callee.

        A spy proves one path took the offload; this proves no path can skip it. Asserted on the
        parsed body rather than on the source text, because this module's own docstrings say the
        word ``evaluate`` repeatedly and a substring search would match the explanation of the
        guarantee instead of the guarantee.
        """
        tree = ast.parse(textwrap.dedent(inspect.getsource(service.step_session)))
        direct = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "evaluate"
        ]
        assert direct == [], (
            f"step_session calls the strategy runtime directly at line(s) {direct}; it must be "
            f"handed to run_in_threadpool, which is what keeps a slow indicator off the HTTP "
            f"event loop (Requirement 27.3)"
        )
        offloads = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "run_in_threadpool"
        ]
        assert len(offloads) == 1
        assert isinstance(offloads[0].args[0], ast.Name)
        assert offloads[0].args[0].id == "evaluate"

    def test_nothing_in_the_module_calls_time_sleep(self) -> None:
        """Task 27.2: "Nothing in the loop calls ``time.sleep``".

        Checked over the WHOLE module rather than over the loop's own body: a blocking sleep is a
        blocking sleep wherever a coroutine in this file reaches it, and it would stall every other
        session and every HTTP handler in the process for its duration.
        """
        source = inspect.getsource(service)
        tree = ast.parse(source)

        imports = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            and any(alias.name.split(".")[0] == "time" for alias in node.names)
        ] + [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.split(".")[0] == "time"
        ]
        assert imports == [], (
            f"paper_session_service imports the `time` module at line(s) {imports}; the session "
            f"loop's only waits are asyncio.sleep (the pause poll) and FeedHandle.reconnect's own "
            f"bounded backoff (Requirement 27.3)"
        )

        sleeps = [
            (
                node.lineno,
                node.func.value.id if isinstance(node.func.value, ast.Name) else "?",
            )
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "sleep"
        ]
        assert sleeps, "the pause poll must wait on something; none was found"
        assert all(owner == "asyncio" for _, owner in sleeps), (
            f"every sleep in this module must be asyncio.sleep; found {sleeps}"
        )


class TestTheSignalIsRecordedWithPaperAndTheSession(_LoopCase):
    """Requirements 23.1 and 23.5, as the arguments the recorder actually received.

    Red run: ``SIGNAL_ENVIRONMENT`` was changed to ``'LIVE'``.
    :func:`test_the_environment_is_paper` failed on the value, and - because
    ``SignalGeneratedPayload.environment`` is a ``Literal['PAPER']`` -
    :class:`TestTheSignalGeneratedFrameIsTheSafeProjection` failed on payload validation as well,
    which is the belt-and-braces Requirement 28.4 asks for.
    """

    def test_the_environment_is_paper_and_the_session_is_named(self) -> None:
        supabase, row, account_id = _loop_double()
        handle, _ = _loop_feed(supabase, [_frame()])
        recorder = _Recorder(supabase)

        _step(
            supabase,
            row,
            handle,
            account_id=account_id,
            evaluate=_Runtime([_signal()]),
            record_signal=recorder,
        )

        assert len(recorder.calls) == 1
        assert recorder.calls[0]["environment"] == "PAPER"
        assert recorder.calls[0]["paper_session_id"] == SESSION

    def test_the_recorder_receives_the_runtime_s_own_object_not_the_projection(
        self,
    ) -> None:
        """Requirement 23.2 lists thirteen fields the trace records per signal.

        Projecting before the record would narrow what is STORED rather than what is disclosed -
        Requirement 23.3's subscriber restriction is a read-path rule. So the recorder is handed
        the value the runtime produced, and only the emission path sees the safe projection.
        """
        supabase, row, account_id = _loop_double()
        handle, _ = _loop_feed(supabase, [_frame()])
        produced = _signal(node_closure={"node-1": "READY"}, sizing_intention={"pct": "1"})
        recorder = _Recorder(supabase)

        _step(
            supabase,
            row,
            handle,
            account_id=account_id,
            evaluate=_Runtime([produced]),
            record_signal=recorder,
        )

        assert recorder.calls[0]["signal"] is produced

    def test_the_record_happens_before_the_frame_is_broadcast(self) -> None:
        """The tick is the only frame recorded when the recorder runs."""
        supabase, row, account_id = _loop_double()
        handle, _ = _loop_feed(supabase, [_frame()])
        recorder = _Recorder(supabase)

        _step(
            supabase,
            row,
            handle,
            account_id=account_id,
            evaluate=_Runtime([_signal()]),
            record_signal=recorder,
        )

        assert recorder.calls[0]["events_before"] == 1, (
            "signal_generated was recorded before the Signal_Trace record; a frame a reader "
            "cannot look up is a frame that arrived too early (Requirements 23.1, 23.5)"
        )

    def test_the_default_recorder_logs_an_error_rather_than_passing_quietly(
        self, caplog: Any
    ) -> None:
        """A paper session whose signals reach no Signal_Trace must say so out loud."""
        supabase, row, account_id = _loop_double()
        handle, _ = _loop_feed(supabase, [_frame()])

        with caplog.at_level("ERROR", logger="PaperSessionService"):
            _step(
                supabase,
                row,
                handle,
                account_id=account_id,
                evaluate=_Runtime([_signal()]),
            )

        assert any(
            "NOT recorded in the Signal_Trace" in record.getMessage()
            for record in caplog.records
        )

    def test_a_recorder_that_raises_does_not_stop_the_order(self) -> None:
        """Stated behaviour, asserted: the paper order stands and the gap is in the log.

        Deliberately different from the live path, which refuses to route a signal it could not
        persist (Requirement 15.5). See ``_record_signal``'s docstring for the argument.
        """
        supabase, row, account_id = _loop_double()
        handle, _ = _loop_feed(supabase, [_frame()])

        async def broken(signal: Any, **kwargs: Any) -> None:
            raise RuntimeError("the trace store is unavailable")

        step = _step(
            supabase,
            row,
            handle,
            account_id=account_id,
            evaluate=_Runtime([_signal()]),
            record_signal=broken,
        )

        assert len(step.submissions) == 1
        assert step.submissions[0].accepted is True
        assert len(supabase.fills) == 1


class TestTheSignalGeneratedFrameIsTheSafeProjection(_LoopCase):
    """Requirement 23.5 / 19.7: the nine fields, and no route for a tenth.

    Red run: ``safe_signal_payload`` was given a tenth key carrying the runtime's own object. The
    payload model's ``extra='forbid'`` raised at ``record_event``, so every case in this class and
    in :class:`TestThePerEventOrder` failed - which is the containment being enforced by the type
    rather than by this test.
    """

    #: Names that would each be Protected_Logic or a version reference on a channel frame. Not a
    #: guess: they are ``SignalGeneratedPayload``'s own forbidden list, ``Signal.to_row``'s three
    #: JSONB columns and the fields Requirement 23.3 excludes for a subscriber.
    FORBIDDEN = (
        "plan",
        "compiled_plan",
        "node",
        "node_id",
        "node_ids",
        "nodes",
        "node_closure",
        "source_node_ids",
        "indicator",
        "indicators",
        "feature",
        "features",
        "graph",
        "ml",
        "ml_info",
        "ml_inference",
        "risk",
        "risk_validation",
        "risk_reason",
        "market_info",
        "version",
        "version_id",
        "strategy_version",
        "sizing_intention",
        "deployment_id",
    )

    def _frame_payload(self) -> Dict[str, Any]:
        supabase, row, account_id = _loop_double()
        handle, _ = _loop_feed(supabase, [_frame()])
        produced = _signal(
            node_closure={"node-1": {"value": "42"}},
            indicators={"rsi_14": "71.2"},
            compiled_plan={"levels": [["node-1"]]},
            ml_inference={"probability": "0.81"},
            risk_validation={"passed": True},
            strategy_version="v3",
            sizing_intention={"pct_of_capital": "2"},
        )
        _step(
            supabase,
            row,
            handle,
            account_id=account_id,
            evaluate=_Runtime([produced]),
        )
        return _payload_of(supabase, "signal_generated")

    def test_the_payload_is_exactly_the_nine_fields(self) -> None:
        assert set(self._frame_payload()) == {
            "signal_id",
            "decision",
            "symbol",
            "side",
            "quantity",
            "price",
            "order_lifecycle_state",
            "generated_at",
            "environment",
        }

    def test_no_protected_field_reaches_the_frame_under_any_name(self) -> None:
        payload = self._frame_payload()
        rendered = json.dumps(payload, sort_keys=True)
        leaked = [name for name in self.FORBIDDEN if name in payload]
        assert leaked == [], (
            f"signal_generated carries {leaked}; Requirement 19.7 forbids Protected_Logic in a "
            f"Paper_Channel payload and Requirement 23.3 gives the same list for a subscriber's "
            f"read path"
        )
        for value in ("rsi_14", "node-1", "probability", "pct_of_capital", "levels"):
            assert value not in rendered, (
                f"signal_generated's rendered payload contains {value!r}, which came off the "
                f"strategy's own structure: {rendered}"
            )

    def test_the_environment_is_paper_on_the_wire(self) -> None:
        assert self._frame_payload()["environment"] == "PAPER"

    def test_a_signal_with_no_price_carries_null_rather_than_zero(self) -> None:
        """Requirements 14.9, 28.5: absent is not zero, and is carried as absent."""
        supabase, row, account_id = _loop_double()
        handle, _ = _loop_feed(supabase, [_frame()])

        _step(
            supabase,
            row,
            handle,
            account_id=account_id,
            evaluate=_Runtime([_signal(price=None)]),
        )

        assert _payload_of(supabase, "signal_generated")["price"] is None


class TestASignalThatStatesNoExecutableIntent(_LoopCase):
    """The signal is recorded and broadcast; no order is created. Neither is an error.

    Red run: ``signal_to_intent`` was given a ``quantity or Decimal('1')`` fallback - the invented
    position size Requirement 28.5 forbids.
    :func:`test_a_sizing_intention_is_not_turned_into_a_quantity` failed on an order existing.
    """

    def test_a_sizing_intention_is_not_turned_into_a_quantity(self) -> None:
        supabase, row, account_id = _loop_double()
        handle, _ = _loop_feed(supabase, [_frame()])
        recorder = _Recorder(supabase)

        step = _step(
            supabase,
            row,
            handle,
            account_id=account_id,
            evaluate=_Runtime([_signal(quantity=None)]),
            record_signal=recorder,
        )

        assert len(recorder.calls) == 1, "the signal WAS generated, so it is recorded"
        assert supabase.orders == [], "position sizing is the strategy's decision, not the loop's"
        assert step.skipped == (
            (_signal()["signal_id"], service.INTENT_NO_QUANTITY),
        )
        # No frame either: the payload's quantity is exact and required, and there is no true
        # value to put in it (Requirement 28.5).
        assert "signal_generated" not in _recorded_types(supabase)

    def test_an_exit_with_no_open_position_creates_no_order(self) -> None:
        supabase, row, account_id = _loop_double()
        handle, _ = _loop_feed(supabase, [_frame()])

        step = _step(
            supabase,
            row,
            handle,
            account_id=account_id,
            evaluate=_Runtime([_signal(decision="EXIT", side=None, quantity=None)]),
        )

        assert supabase.orders == []
        assert step.skipped == (
            (_signal()["signal_id"], service.INTENT_NO_OPEN_POSITION),
        )

    def test_a_signal_naming_another_market_is_discarded(self) -> None:
        """A session subscribes to ONE market; another symbol has no validated price here."""
        supabase, row, account_id = _loop_double()
        handle, _ = _loop_feed(supabase, [_frame()])

        step = _step(
            supabase,
            row,
            handle,
            account_id=account_id,
            evaluate=_Runtime([_signal(symbol="ETH/USDT")]),
        )

        assert step.signals == ()
        assert supabase.orders == []
        assert _recorded_types(supabase) == ["market_tick"]

    def test_an_unreadable_output_costs_one_signal_and_not_the_bar(self) -> None:
        """Requirement 14.7's containment: later outputs and later bars continue."""
        supabase, row, account_id = _loop_double()
        handle, _ = _loop_feed(supabase, [_frame()])

        step = _step(
            supabase,
            row,
            handle,
            account_id=account_id,
            evaluate=_Runtime([{"decision": "BUY"}, _signal()]),
        )

        assert [s.signal_id for s in step.signals] == [_signal()["signal_id"]]
        assert len(step.submissions) == 1

    def test_a_float_quantity_is_refused_rather_than_converted(self) -> None:
        """Requirement 18.1: ``0.5`` is not one half, and no rounding hides that here.

        The existing ``signal_service.Signal.quantity`` is typed ``Optional[float]``, so a wiring
        layer handing one straight over gets this named refusal. Widening that field is
        ``signal_service``'s change; converting it here would substitute one number for another.
        """
        supabase, row, account_id = _loop_double()
        handle, _ = _loop_feed(supabase, [_frame()])

        step = _step(
            supabase,
            row,
            handle,
            account_id=account_id,
            evaluate=_Runtime([_signal(quantity=0.5)]),
        )

        assert step.event is not None
        assert step.signals == ()
        assert supabase.orders == []
        with pytest.raises(service.PaperSignalUnreadable):
            service.paper_signal(_signal(quantity=0.5), event=step.event)


class TestAnUnhealthyFeedProducesNoFill(_LoopCase):
    """Requirement 14.5's simulator half, reached through the loop.

    The frame carries no ``transport`` field, so the feed state becomes ``TRANSPORT_UNKNOWN`` -
    not in ``TRADEABLE_FEED_STATES``, which is exactly ``('HEALTHY',)``. The market order is
    refused by ``admit_execution`` BEFORE it is accepted, so there is no order to fill either.

    Red run: ``TRADEABLE_FEED_STATES`` was widened to include ``TRANSPORT_UNKNOWN``.
    :func:`test_no_fill_and_no_order_exists` failed on a fill row existing - a fill priced from a
    bar whose delivery path could not be identified.
    """

    def _degraded_step(self) -> Tuple[FakeSupabase, service.SessionStep]:
        supabase, row, account_id = _loop_double()
        handle, _ = _loop_feed(supabase, [_frame(transport=None)])
        step = _step(
            supabase,
            row,
            handle,
            account_id=account_id,
            evaluate=_Runtime([_signal()]),
        )
        return supabase, step

    def test_the_feed_state_is_not_tradeable(self) -> None:
        supabase, _ = self._degraded_step()
        assert feed.TRADEABLE_FEED_STATES == ("HEALTHY",)
        assert supabase.sessions[0]["feed_state"] == "TRANSPORT_UNKNOWN"

    def test_no_fill_and_no_order_exists(self) -> None:
        supabase, step = self._degraded_step()
        assert supabase.fills == []
        assert supabase.orders == []
        assert step.submissions == ()

    def test_the_refusal_is_reported_and_the_session_keeps_reporting(self) -> None:
        """A refused order is not a dead session: the bar is still on the channel."""
        supabase, step = self._degraded_step()
        assert [reason for _, reason in step.skipped] == ["FeedNotHealthy"]
        assert _recorded_types(supabase) == ["market_tick", "signal_generated"]


class TestTheRevaluationAndItsEmissions(_LoopCase):
    """"Revalue and snapshot equity with the PnL and drawdown emissions", as the rows written.

    Red run: the ``REVALUATION`` snapshot was written before ``check_resting_orders``. The equity
    point then valued the book as it was BEFORE the bar's fills, and
    :func:`test_the_equity_point_values_the_book_after_the_bar_s_fills` failed on the figure.
    """

    def _filled_step(self) -> Tuple[FakeSupabase, service.SessionStep]:
        supabase, row, account_id = _loop_double()
        handle, _ = _loop_feed(supabase, [_frame()])
        step = _step(
            supabase,
            row,
            handle,
            account_id=account_id,
            evaluate=_Runtime([_signal()]),
        )
        return supabase, step

    def test_the_position_is_priced_from_the_bar_and_from_nothing_else(self) -> None:
        """Requirements 14.9, 18.8, 28.3: the price is the validated close, exactly."""
        supabase, step = self._filled_step()
        assert len(supabase.positions) == 1
        position = supabase.positions[0]
        assert Decimal(str(position["current_price"])) == BAR_CLOSE
        assert position["price_at"] is not None

    def test_one_revaluation_snapshot_is_written_for_the_bar(self) -> None:
        supabase, step = self._filled_step()
        causes = [str(row["cause"]) for row in supabase.equity_snapshots]
        assert causes == ["FILL", "REVALUATION"], (
            "Requirement 18.11 names a snapshot at each applied fill and at each revaluation from "
            f"a validated price; got {causes}"
        )
        assert step.snapshot is not None

    def test_the_equity_point_values_the_book_after_the_bar_s_fills(self) -> None:
        supabase, step = self._filled_step()
        assert step.valuation is not None
        revaluation = [
            row for row in supabase.equity_snapshots if row["cause"] == "REVALUATION"
        ][0]
        assert Decimal(str(revaluation["total_equity"])) == (
            step.valuation.account.total_equity
        )
        # Requirement 18.3, on the row: the identity holds with zero tolerance.
        assert Decimal(str(revaluation["total_equity"])) == (
            Decimal(str(revaluation["available_balance"]))
            + Decimal(str(revaluation["locked_balance"]))
            + Decimal(str(revaluation["position_market_value"]))
        )

    def test_the_pnl_and_drawdown_frames_carry_measurements_and_not_zeros(self) -> None:
        supabase, step = self._filled_step()
        pnl = _payload_of(supabase, "paper_pnl_updated")
        drawdown = _payload_of(supabase, "paper_drawdown_updated")

        assert pnl["stale"] is False
        assert Decimal(pnl["total_pnl"]) == (
            Decimal(pnl["realized_pnl"]) + Decimal(pnl["unrealized_pnl"])
        )
        assert pnl["price_at"] is not None
        # Two snapshots exist by now, so the drawdown is computed rather than reported as zero
        # for want of a series (Requirement 18.9).
        assert drawdown["snapshot_count"] == 2
        assert Decimal(drawdown["max_drawdown_amount"]) >= Decimal("0")
        assert Decimal("0") <= Decimal(drawdown["max_drawdown_fraction"]) <= Decimal("1")

    def test_a_bar_that_revalued_nothing_writes_no_equity_point(self) -> None:
        """A book with no open position has nothing to restate, and says so by writing nothing.

        Requirement 18.11 asks for a snapshot at "each position revaluation from a validated
        price". A row per candle for an idle session would make Requirement 18.9's drawdown a
        function of how long the session ran rather than of what it did.
        """
        supabase, row, account_id = _loop_double()
        handle, _ = _loop_feed(supabase, [_frame()])

        step = _step(
            supabase, row, handle, account_id=account_id, evaluate=_Runtime()
        )

        assert step.dropped is False
        assert step.valuation is None
        assert supabase.equity_snapshots == []
        assert _recorded_types(supabase) == ["market_tick"]


class TestTheMarketTickCarriesTheValidatedBar(_LoopCase):
    """P-55's conjunct 4, at the emitter: every price is the one the event recorded.

    ``market_tick`` had no emitter before this task -
    ``tests/property/test_no_synthesised_price.py`` says so in its own gap list - so this is the
    first place the five OHLCV values reach a channel frame, and they are the event's.
    """

    def test_the_five_values_and_the_identity_are_the_events_own(self) -> None:
        supabase, row, account_id = _loop_double()
        handle, _ = _loop_feed(supabase, [_frame()])

        step = _step(supabase, row, handle, account_id=account_id, evaluate=_Runtime())

        payload = _payload_of(supabase, "market_tick")
        event = step.event
        assert event is not None
        for name in ("open", "high", "low", "close", "volume"):
            assert Decimal(payload[name]) == getattr(event, name)
        assert payload["source_event_id"] == event.source_event_id
        assert payload["feed_state"] == "HEALTHY"
        assert Decimal(payload["latency_ms"]) == event.latency_ms

    def test_the_broadcast_price_is_the_price_the_market_event_row_holds(self) -> None:
        """The provenance P-55 asserts, on one bar: the frame and the log agree exactly."""
        supabase, row, account_id = _loop_double()
        handle, _ = _loop_feed(supabase, [_frame()])

        _step(supabase, row, handle, account_id=account_id, evaluate=_Runtime())

        payload = _payload_of(supabase, "market_tick")
        stored = [
            r
            for r in supabase.market_events
            if str(r["source_event_id"]) == payload["source_event_id"]
        ]
        assert len(stored) == 1
        for name in ("open", "high", "low", "close", "volume"):
            assert Decimal(payload[name]) == Decimal(str(stored[0]["payload"][name]))


class TestSignalToIntent:
    """The three readings, as a pure function. No feed, no database, no simulator."""

    @staticmethod
    def _position(side: str = "LONG", size: str = "0.5") -> Any:
        return sim.position_of(
            {
                "symbol": SYMBOL,
                "side": side,
                "size": size,
                "entry_price": "60000",
                "opened_at": NOW.isoformat(),
            }
        )

    def _read(self, **overrides: Any) -> service.PaperSignal:
        event = feed.MarketEvent(
            session_id=SESSION,
            user_id=USER,
            sequence=1,
            source_event_id="evt-1",
            exchange_id=EXCHANGE,
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            event_timestamp=NOW,
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            volume=Decimal("1"),
            transport="WEBSOCKET",
            latency_ms=Decimal("0.000"),
            received_at=NOW,
            payload={},
            feed_state="HEALTHY",
        )
        return service.paper_signal(_signal(**overrides), event=event)

    def test_an_entry_takes_its_side_from_the_decision(self) -> None:
        intent = service.signal_to_intent(
            self._read(decision="SELL", side=None), positions={}
        )
        assert intent.side == "sell"
        assert intent.order_type == "market"

    def test_an_exit_takes_its_side_from_the_open_position(self) -> None:
        signal = self._read(decision="EXIT", side=None, quantity=None)
        intent = service.signal_to_intent(
            signal, positions={SYMBOL: self._position("LONG")}
        )
        assert intent.side == "sell", "a LONG is closed by a sell"
        assert intent.quantity == Decimal("0.5"), "an EXIT closes the whole position"

    def test_a_short_is_closed_by_a_buy(self) -> None:
        signal = self._read(decision="CLOSE", side=None, quantity=None)
        intent = service.signal_to_intent(
            signal, positions={SYMBOL: self._position("SHORT", "2")}
        )
        assert intent.side == "buy"
        assert intent.quantity == Decimal("2")

    def test_a_limit_signal_with_no_limit_is_refused_rather_than_priced(self) -> None:
        """The decision price is not a limit, and substituting it would invent one."""
        signal = self._read(order_type="limit", price="60000")
        with pytest.raises(service.PaperSignalNotExecutable) as caught:
            service.signal_to_intent(signal, positions={})
        assert caught.value.reason == service.INTENT_NO_LIMIT_PRICE

    def test_a_quantity_at_or_below_zero_is_refused(self) -> None:
        with pytest.raises(service.PaperSignalNotExecutable) as caught:
            service.signal_to_intent(self._read(quantity="0"), positions={})
        assert caught.value.reason == service.INTENT_NOT_POSITIVE

    def test_the_intent_carries_the_signal_id_and_its_derived_idempotency_key(self) -> None:
        """One decision produces at most one paper order (Requirement 16.8).

        The key is ``"signal:{signal_id}"`` - the same derivation the live path uses - so a
        redelivered bar, a restarted loop or a second worker reaches ``paper_simulator``'s
        idempotency probe rather than creating a second order.
        """
        signal = self._read()
        key = service.signal_intent_idempotency_key(signal)
        intent = service.signal_to_intent(signal, positions={}, idempotency_key=key)
        assert intent.signal_id == signal.signal_id
        assert intent.idempotency_key == f"signal:{signal.signal_id}"
        assert len(intent.idempotency_key) <= 128

    def test_an_unrecognised_lifecycle_state_is_refused(self) -> None:
        """No paper-specific state vocabulary is added (task 29.2's own rule, applied here)."""
        with pytest.raises(service.PaperSignalUnreadable):
            self._read(order_lifecycle_state="RESTING")


class TestTheLoopItself(_LoopCase):
    """``session_loop``: the state gate, the bar count, and cancellation.

    Red run: the ``PAUSED`` branch was removed, so a paused session kept stepping.
    :func:`test_a_paused_session_steps_no_bar` failed on the runtime having been called - a pause
    that keeps trading is a pause in name only.
    """

    def test_a_stopped_session_is_not_stepped_at_all(self) -> None:
        supabase, row, account_id = _loop_double()
        row["session_state"] = "STOPPED"
        supabase.sessions[0]["session_state"] = "STOPPED"
        handle, _ = _loop_feed(supabase, [_frame()])
        runtime = _Runtime([_signal()])

        processed = _run_coroutine(
            service.session_loop(
                supabase,
                row,
                handle,
                config=_loop_config(),
                account_id=account_id,
                evaluate=runtime,
            )
        )

        assert processed == 0
        assert runtime.calls == []
        assert supabase.events == []

    def test_a_session_whose_row_is_gone_ends_the_loop(self) -> None:
        """Requirement 21.4's read, applied per iteration: not readable is not runnable."""
        supabase, row, account_id = _loop_double()
        handle, _ = _loop_feed(supabase, [_frame()])
        # Removed AFTER the feed was opened, which is the order the condition actually arises in:
        # a session deleted, or a handle whose identity no longer matches, while a loop is running.
        supabase.sessions.clear()

        processed = _run_coroutine(
            service.session_loop(
                supabase,
                row,
                handle,
                config=_loop_config(),
                account_id=account_id,
                evaluate=_Runtime(),
            )
        )

        assert processed == 0

    def test_a_paused_session_steps_no_bar_and_keeps_its_subscription(self) -> None:
        """Requirement 17.7: a pause stops the stepping; it does not release the feed.

        Driven as a cancelled task because a paused loop does not terminate on its own - it polls
        - which is the behaviour under test.
        """
        supabase, row, account_id = _loop_double()
        supabase.sessions[0]["session_state"] = "PAUSED"
        handle, redis = _loop_feed(supabase, [_frame()])
        runtime = _Runtime([_signal()])

        async def drive() -> None:
            task = asyncio.create_task(
                service.session_loop(
                    supabase,
                    row,
                    handle,
                    config=_loop_config(),
                    account_id=account_id,
                    evaluate=runtime,
                )
            )
            await asyncio.sleep(0.05)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        _run_coroutine(drive())

        assert runtime.calls == []
        assert supabase.events == []
        assert handle.closed is False, "a pause is not a stop; the subscription stays open"
        assert redis.pubsub_calls == 1

    def test_the_loop_processes_bars_until_the_session_leaves_running(self) -> None:
        """The gate is the PERSISTED state, re-read every iteration.

        The runtime is what moves the row - which is a test's stand-in for the pause or stop that
        arrives on another request, in another worker. It is the only thing the loop could not
        learn from its own memory.
        """
        supabase, row, account_id = _loop_double()
        handle, _ = _loop_feed(supabase, [_frame(), _frame(timestamp_ms=_bar_ms(0))])

        class _StopsAfterOneBar(_Runtime):
            def __call__(self, plan: Any, event: Any) -> Any:
                supabase.sessions[0]["session_state"] = "STOPPED"
                return super().__call__(plan, event)

        runtime = _StopsAfterOneBar([_signal()])

        processed = _run_coroutine(
            service.session_loop(
                supabase,
                row,
                handle,
                config=_loop_config(),
                account_id=account_id,
                evaluate=runtime,
            )
        )

        assert processed == 1
        assert len(runtime.calls) == 1
        assert _recorded_types(supabase) == [
            "market_tick",
            "signal_generated",
            "paper_pnl_updated",
            "paper_drawdown_updated",
        ]

    def test_cancelling_the_task_stops_the_loop_and_leaks_no_subscription(self) -> None:
        """The spawner's task is the handle on a running session, and cancelling it is the exit.

        "Leaks no subscription" is asserted three ways: the loop opened no second ``pubsub``, it
        registered nothing on the Paper_Channel (the registration is the subscribe path's, and
        releasing it is the stop path's - task 27.4), and the harness's loop is left with no
        pending task of its own.
        """
        supabase, row, account_id = _loop_double()
        handle, redis = _loop_feed(supabase, [_frame()])
        runtime = _Runtime([_signal()])
        spawn = service.spawn_session_loop(
            supabase,
            evaluate=runtime,
            plan="plan-1",
        )

        async def drive() -> Tuple["asyncio.Task[int]", List[Any]]:
            # The two the pipeline creates arrive at spawn time; the three the installing layer
            # owns were closed over above. That is the whole seam.
            task = spawn(row, handle, config=_loop_config(), account_id=account_id)
            assert isinstance(task, asyncio.Task)
            for _ in range(200):
                if runtime.calls:
                    break
                await asyncio.sleep(0.01)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            left_behind = [
                pending
                for pending in asyncio.all_tasks()
                if pending is not asyncio.current_task() and not pending.done()
            ]
            return task, left_behind

        task, left_behind = _run_coroutine(drive())

        assert task.done() is True
        assert task.cancelled() is True
        assert len(runtime.calls) == 1, "the task ran the loop rather than merely existing"
        assert redis.pubsub_calls == 1, "the loop opened no second subscription"
        assert paper_channel.REGISTRY.subscriptions(SESSION) == ()
        assert left_behind == [], f"the cancelled loop left {left_behind} behind"

    def test_the_loop_reconnects_a_dropped_subscription_rather_than_ending(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Requirement 14.5: record DEGRADED, then one bounded attempt, then keep going.

        ``FeedHandle.reconnect``'s own bounded delay is replaced so the case does not wait a
        second; the delay sequence itself is
        ``tests/test_paper_market_feed_events.py``'s assertion, not this one's.
        """
        supabase, row, account_id = _loop_double()
        handle, _ = _loop_feed(supabase, [])
        attempts: List[int] = []

        async def instant_reconnect() -> bool:
            attempts.append(1)
            supabase.sessions[0]["session_state"] = "STOPPED"
            return True

        monkeypatch.setattr(handle, "reconnect", instant_reconnect)

        processed = _run_coroutine(
            service.session_loop(
                supabase,
                row,
                handle,
                config=_loop_config(),
                account_id=account_id,
                evaluate=_Runtime(),
            )
        )

        assert processed == 0
        assert attempts == [1]
        assert supabase.sessions[0]["feed_state"] == "DEGRADED"
        # Requirement 14.5's other half: one paper_error record per outage.
        assert [
            str(r["event_type"]) for r in supabase.events
        ] == ["paper_error"]


def _bar_ms(minutes_before: int) -> int:
    """A candle open time ``minutes_before`` minutes before :data:`LOOP_NOW`, in epoch ms."""
    return int((LOOP_NOW - timedelta(minutes=minutes_before)).timestamp() * 1000)
