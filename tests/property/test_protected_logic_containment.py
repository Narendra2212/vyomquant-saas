"""Property tests for Protected_Logic containment and clone gating.

Feature: marketplace-subscriptions-paper-trading
Task 16.4 (P-47) — **Validates: Requirements 6.3, 6.8, 7.1, 19.7**
Task 17.6 (P-48) — **Validates: Requirements 7.3, 7.4**
Design reference: ``design.md § Property-to-test mapping`` ->
``P-47, P-48 | tests/property/test_protected_logic_containment.py |
protected_logic_strategies() | listing_projection.protected_logic_tokens +
assert_contains_no_protected_logic``; and ``design.md § Mechanical Protected_Logic
containment (Requirement 6.8, P-47, P-48)``.

Properties living here
----------------------
``test_p47_no_response_or_event_contains_protected_logic`` (task 16.4) and
``test_p48_clone_is_refused_when_source_cloning_is_disabled`` (task 17.6) — the two properties
``design.md § Property-to-test mapping`` places in this module. One property, one test function,
so the scoreboard in ``tests/property/test_property_coverage.py`` reads exactly one ``test_p47_``
name and exactly one ``test_p48_`` name. They share the generator and nothing else: P-47 is about
what a *response* may carry, P-48 is about what the *clone gate* may create, and each is stated
and asserted on its own. P-48 and its harness live at the bottom of this file, under their own
banner; do not fold them into P-47.

WHAT P-47 CLAIMS
----------------
A strategy's Protected_Logic — its DAG nodes, indicator parameters, risk configuration and
bound ML model — must never surface in anything a caller who is not the owner can read.
``protected_logic_strategies()`` builds a strategy whose node ids, indicator names, parameter
names, threshold values and model path segments are all generated tokens of at least three
characters, so every one of them is a substring the containment oracle can look for. The test
asserts that no field of the one public Listing view — nor of the enriched
authenticated-non-owner shape, nor of an error or diagnostic body, nor of a Paper_Channel event
envelope — contains any of those tokens, at any nesting depth, for both an authenticated
non-owner and an unauthenticated caller.

WHY THE SOURCE ROW CARRIES THE PROTECTED LOGIC
----------------------------------------------
The point of the property is that the *source* row genuinely carries Protected_Logic and the
*projection* does not. So the listing row handed to ``project_listing`` carries both the
public columns (with benign public values) **and** the protected-logic columns the generator
produced (``buy_logic``, ``sell_logic``, ``indicators``, ``risk``, ``ml_model_path`` from the
strategy row, plus the graph documents from the version row). If the projection copied
``select("*")``-style, the tokens would leak; because it is allow-list based, they must not. The
tokens are computed by the module's own oracle ``protected_logic_tokens`` from the *same*
generated documents, so the check and the projection cannot drift apart about what
Protected_Logic is (Requirement 6.8).

TWO ASSERTIONS, NOT ONE: CONTAINMENT AND INVARIANCE
---------------------------------------------------
The designated oracle is a substring search. It is the assertion Requirement 6.8 words
("no field … contains any part of that Protected_Logic") and it is run first, so a failure
names the leaked token and the path it was found at. It is followed by a strictly stronger
statement that costs one more call: the projection of the row **carrying** Protected_Logic is
equal to the projection of the same row **without** the protected columns. Equality says the
public view is a function of the public columns alone, which catches a leak the substring
search could miss — a re-encoded, hashed or truncated derivative that shares no three-character
run with the source document. Neither assertion subsumes the other, so both are here.

WHY A STRUCTURAL KEY IS NOT A DISCLOSURE (the failure this task had to resolve)
------------------------------------------------------------------------------
An earlier run of this test failed on a generated indicator named ``details``, which collides
with the ``MarketplaceError`` envelope's own ``details`` key, and on a node id ``avg_rating``,
which collides with a legitimate Listing field. Neither is a leak, and the fix belongs in the
oracle rather than in the assertion or the generator.

Those two collisions are also not bad luck. Hypothesis mines string literals out of the project's
own source and injects them into ``st.text`` draws whose alphabet admits them, so
``marketplace_generators.tokens()`` produces this API's identifiers *verbatim* — a 2000-draw
probe of that generator returned ``avg_rating``, ``code``, ``currency``, ``details``, ``label``,
``listing_id``, ``message``, ``name``, ``price``, ``reason``, ``request_id`` and ``version``
among its indicator names. Any fix that hoped the collision would not recur, or that struck the
offending words out of the generator one at a time, would be permanently flaky: the injected pool
is the whole codebase's literal set, and every response key this API will ever have is in it.
The fix has to be structural, and it is:

Requirement 6.8 is written about what a *field contains* — "no field of any Marketplace_API …
response … contains any part of that Protected_Logic" — and Requirement 7.1 repeats the wording
("from every field of every response returned to that caller"). The *names* of those fields are
not response data: Requirement 6.2 enumerates them in advance, and
``listing_projection.PUBLIC_LISTING_FIELDS`` is that enumeration, asserted against the
projection's output on every call. A name from a set fixed before any strategy existed appears
in the response for every Listing — including a Listing whose strategy has no indicator, no node
and no model — so it is constant across strategies and carries no information about any of them.
A caller who reads ``details`` in an error body learns only the published schema.

So ``listing_projection.STRUCTURAL_RESPONSE_KEYS`` now names that vocabulary in one place and
``assert_contains_no_protected_logic`` skips it **in key position only**. Every value at every
depth is still searched, including the values stored under those keys: a node identifier returned
as the value of ``name``, inside ``details`` or embedded in ``message`` is still caught. That was
verified by injecting two leaks into ``project_listing`` — the graph document into
``description``, and the ``ml_model_path`` into ``name``, the second one deliberately under a key
the exemption covers — and confirming this property failed on each, naming the token and its
path. The two bodies whose key vocabulary this module does not own — the route's authenticated
enrichment and the Paper_Channel envelope — declare their keys in
:data:`SURFACE_STRUCTURAL_KEYS` below.

WHY SOME EXAMPLES ARE SKIPPED, AND WHY THAT IS NOT A RELAXATION
---------------------------------------------------------------
Exempting keys is not enough on its own, because a response also holds *values* that exist
irrespective of the strategy: the error catalogue's constant sentence, the positional
``"Condition 1"`` label, ``"True"``, the Listing's own public column values. Those are English
prose and short digit runs, and the oracle's floor is three characters, so a generated parameter
named ``ate`` is a substring of ``INTERMEDIATE`` and a generated threshold ``202`` is a
substring of the publication timestamp. Such an example cannot distinguish a disclosure from a
spelling coincidence — the token would be "found" in a response built before the strategy was
drawn — so it is uninformative, and :func:`_strategy_independent_text` collects that text and
``assume`` sends Hypothesis to look elsewhere.

That is a filter on *examples*, not on the property: every example that runs searches every
token over every value. Nothing is dropped from the token set, no assertion is weakened, and the
corpus is built from declared literals and from catalogue constants — never from the response
under test — so a value that reaches a response *because* of the strategy is never in it. Over
1000 drawn examples, 14 were skipped (1.4%), on the tokens ``version``, ``reason`` and
``listing_id`` — the same source-literal injection again. That is far below Hypothesis's
``filter_too_much`` threshold, which is why no health check is suppressed for it: if a later edit
pushes the rate up, the suite says so instead of quietly testing less.

PAPER_CHANNEL EVENT HALF — WHAT IS ASSERTED TODAY AND WHAT TASK 26.1 OWES
-------------------------------------------------------------------------
Requirement 19.7 ("THE Paper_Channel SHALL include no Protected_Logic in any event payload")
and ``design.md § Paper channel`` ("``assert_contains_no_protected_logic`` is applied to all
sixteen in the property test") reach further than the code does today.
``backend_app/backend/paper/paper_events.py`` does not exist — the sixteen payload models are
task 26.1 — so this test asserts over the paper artifacts that *do* exist and says plainly what
is deferred, rather than skipping the half:

* the durable buffer's column vocabulary, ``backend_app.backend.paper.COLUMN_CONTRACT``, has no
  column that could carry a graph: it is disjoint from every Protected_Logic column name, so the
  only place a payload could carry logic is inside the ``payload`` JSONB the serialiser writes;
* the ``paper_error`` payload — one of the sixteen — is buildable today, because its ``code`` and
  ``message`` come from the shared catalogue via ``paper.errors.PaperError``; it is asserted;
* the event envelope of ``design.md § Paper channel`` (``schema_version``, ``channel``,
  ``session_id``, ``type``, ``sequence``, ``event_id``, ``emitted_at``, ``payload``) is asserted.

**Deferred to task 26.1**: the fifteen remaining payload models —
``paper_session_started``, ``paper_session_paused``/``_resumed``/``_stopped``, ``market_tick``,
``signal_generated``, ``paper_order_created``/``_accepted``/``_partially_filled``/``_filled``/
``_rejected``, ``paper_position_updated``, ``paper_balance_updated``, ``paper_pnl_updated``,
``paper_drawdown_updated`` — and the serialiser that fills ``payload``. When 26.1 lands, extend
:data:`PAPER_EVENT_BODIES` with a body per model; the assertion loop below already walks
whatever that tuple holds, and the property function keeps its single name.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterator, List, Optional, Tuple
from unittest import mock
from uuid import uuid4

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from backend_app.backend.marketplace import errors as marketplace_errors
from backend_app.backend.marketplace import money
from backend_app.backend.marketplace.errors import (
    MARKETPLACE_CLONING_DISABLED,
    MarketplaceError,
)
from backend_app.backend.marketplace.listing_projection import (
    STRATEGY_PROTECTED_LOGIC_COLUMNS,
    STRUCTURAL_RESPONSE_KEYS,
    SUBSCRIPTION_PERIOD_NOMINAL_DAYS,
    VERSION_PROTECTED_LOGIC_COLUMNS,
    BACKTEST_PROTECTED_LOGIC_COLUMNS,
    assert_contains_no_protected_logic,
    project_listing,
    protected_logic_tokens,
)
from backend_app.backend.paper import COLUMN_CONTRACT
from backend_app.backend.paper import errors as paper_errors
from backend_app.core.audit_trail import StrategyAuditAction
from backend_app.routers import library as lib
from tests.strategies.marketplace_generators import (
    ProtectedLogicFixture,
    identifiers,
    protected_logic_strategies,
)

#: The configuration ``design.md § Property-based testing configuration`` prescribes for every
#: property test in this plan: at least 100 examples and no per-example deadline (the first
#: example pays the import cost), ``derandomize`` left at its default so the failing-example
#: database keeps accumulating.
PROPERTY_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

# ══════════════════════════════════════════════════════════════════════════
# THE STRATEGY-INDEPENDENT FIXTURE
# ══════════════════════════════════════════════════════════════════════════
#
# Every literal below is chosen so that a match against a generated token means a leak rather
# than a coincidence. Two rules, and they are why the values look shouted rather than realistic:
#
#   * text is upper case, because ``marketplace_generators.tokens`` draws from
#     ``[a-z0-9_]`` — an upper-case run cannot contain a generated token at all;
#   * numbers keep their digit runs to two characters, because a generated threshold is a digit
#     string of three to six characters.
#
# Realism of the public values is not what this property is about; ``tests/
# test_listing_projection.py`` exercises the projection against realistic generated rows. Here
# the fixture's job is to be provably unable to produce a false positive. The one literal that
# cannot follow the rules is the publication instant, whose ``2025`` run is inherent to an ISO
# timestamp; :func:`_strategy_independent_text` covers it.

#: The ``library_strategies`` public columns, benign values. Also the corpus's largest
#: contribution, so the row and the corpus cannot drift apart: both read this one dict.
PUBLIC_COLUMNS: Dict[str, Any] = {
    "id": "PUBLIC-LISTING-ID",
    "name": "A PUBLIC STRATEGY NAME",
    "description": "A BENIGN PUBLIC DESCRIPTION OF THIS LISTING.",
    "category": "TREND-FOLLOWING",
    "difficulty": "INTERMEDIATE",
    "tags": ["PUBLIC", "CATALOGUE"],
    "symbol": "BTC/USDT",
    "timeframe": "ONE-HOUR",
    "supported_timeframes": ["ONE-HOUR", "FOUR-HOUR"],
    "exchange_id": "AN-EXCHANGE",
    "market_type": "SPOT-MARKET",
    "price_minor": 99,
    "currency": "USD",
    "subscriber_count": 12,
    "condition_count": 2,
    # Ratings present, so Requirement 6.9's emitted branch — and with it the ``avg_rating`` key
    # that an earlier run of this test mistook for a leak — is exercised.
    "avg_rating": Decimal("4.50"),
    "rating_count": 12,
    "published_at": "2025-01-01T00:00:00+00:00",
    "verification_status": "VERIFIED",
    "source_cloning_enabled": True,
    "backtest_total_return_pct": Decimal("12.34"),
    "backtest_sharpe_ratio": Decimal("1.25"),
    "backtest_max_drawdown_pct": Decimal("9.87"),
    "backtest_win_rate_pct": Decimal("60.50"),
    "backtest_profit_factor": Decimal("2.50"),
    "backtest_total_trades": 42,
}

#: The ``marketplace_backtest_evidence`` summaries the detail read passes in. Two of them, so
#: ``condition_summaries`` is non-empty and the positional ``label`` and the outcome-metric keys
#: are actually present in the body under test.
EVIDENCE_SUMMARIES: Tuple[Dict[str, Any], ...] = (
    {
        "total_return_pct": Decimal("11.10"),
        "sharpe_ratio": Decimal("1.10"),
        "sortino_ratio": Decimal("1.20"),
        "max_drawdown_pct": Decimal("8.10"),
        "win_rate_pct": Decimal("55.50"),
        "profit_factor": Decimal("1.90"),
        "total_trades": 21,
    },
    {
        "total_return_pct": Decimal("13.50"),
        "sharpe_ratio": Decimal("1.40"),
        "sortino_ratio": Decimal("1.50"),
        "max_drawdown_pct": Decimal("9.90"),
        "win_rate_pct": Decimal("61.20"),
        "profit_factor": Decimal("2.10"),
        "total_trades": 31,
    },
)

#: The positional label ``_condition_summaries`` builds. Declared here because it is a value the
#: projection derives rather than one this fixture supplies.
CONDITION_LABEL_PREFIX = "Condition"

#: A creator alias distinct from any generated ``author_id``: ``project_listing`` refuses a blank
#: alias and one equal to the row's ``author_id``, and the generated owner ids are UUID text.
CREATOR_ALIAS = "PUBLIC-CREATOR-ALIAS"

#: The correlation identifier ``structured_error_body`` folds onto an error (Requirement 26.1).
PUBLIC_REQUEST_ID = "PUBLIC-REQUEST-ID"

#: The fields the detail route folds onto the projection for an authenticated caller. Their
#: values are as benign as the public columns, so a token found here would be the enrichment's
#: own leak and not the projection's.
ROUTE_ENRICHMENT: Dict[str, Any] = {
    "recent_ratings": [
        {"stars": 5, "comment": "A GREAT PUBLIC STRATEGY."},
        {"stars": 4, "comment": "SOLID RETURNS OVERALL."},
    ],
    "user_has_cloned": True,
    "user_rating": 4,
}

#: The two error surfaces Requirement 7.1 names: a bare read failure, and a refusal carrying
#: caller-supplied ``details``. ``(code, details)`` pairs; the message comes from the catalogue.
ERROR_BODIES_UNDER_TEST: Tuple[Tuple[str, Dict[str, Any]], ...] = (
    (marketplace_errors.MARKETPLACE_READ_FAILED, {}),
    (
        marketplace_errors.MARKETPLACE_STRATEGY_UNAVAILABLE,
        {"listing_id": PUBLIC_COLUMNS["id"], "reason": "UNAVAILABLE"},
    ),
)

# ── Paper_Channel (Requirement 19.7). See the module docstring for what task 26.1 owes ──

#: The event envelope of ``design.md § Paper channel``, with strategy-independent values.
PAPER_EVENT_ENVELOPE: Dict[str, Any] = {
    "schema_version": "paper.v1",
    "channel": "paper.PAPER-SESSION-ID",
    "session_id": "PAPER-SESSION-ID",
    "type": "paper_error",
    "sequence": 42,
    "event_id": "PAPER-EVENT-ID",
    # Whole seconds, not ``design.md``'s microsecond example: Hypothesis draws ``"000"`` and
    # ``"0000"`` often, and a fractional-second field of zeros would make one example in six
    # uninformative for no gain. The event's exact timestamp format is task 26.1's to fix.
    "emitted_at": "2025-01-01T00:00:00+00:00",
}

#: The one ``paper_events`` payload buildable before task 26.1: ``paper_error``'s ``code`` and
#: ``message`` come from the shared catalogue through ``PaperError``, so this body is the real
#: thing rather than a hand-written imitation.
PAPER_ERROR_CODE = paper_errors.PAPER_MARKET_DATA_UNAVAILABLE


def _paper_event_bodies() -> Tuple[Dict[str, Any], ...]:
    """The Paper_Channel bodies that exist today: the envelope wrapping ``paper_error``.

    Task 26.1 adds one entry per remaining payload model; the assertion loop walks whatever this
    returns, so extending it needs no change to the property function.
    """
    payload = paper_errors.PaperError(PAPER_ERROR_CODE).to_error_object()
    payload["recoverable"] = True
    payload["at"] = PAPER_EVENT_ENVELOPE["emitted_at"]
    return ({**PAPER_EVENT_ENVELOPE, "payload": payload},)


#: The keys the two bodies this module builds by hand emit unconditionally, on top of the
#: production vocabulary in ``listing_projection.STRUCTURAL_RESPONSE_KEYS``. The Paper_Channel
#: half of this set moves into ``paper_events.py`` at task 26.1, where the envelope is declared.
SURFACE_STRUCTURAL_KEYS: FrozenSet[str] = STRUCTURAL_RESPONSE_KEYS | frozenset(
    {
        # The detail route's authenticated enrichment.
        "recent_ratings",
        "stars",
        "comment",
        "user_has_cloned",
        "user_rating",
        # The Paper_Channel envelope and the ``paper_error`` payload.
        "schema_version",
        "channel",
        "session_id",
        "type",
        "sequence",
        "event_id",
        "emitted_at",
        "payload",
        "recoverable",
        "at",
    }
)


def _value_text(value: Any) -> Iterator[str]:
    """Every *value* of ``value`` as text, at any nesting depth, with mapping keys dropped.

    Keys are dropped because the corpus exists to describe the text a body *carries*, and a key
    is schema — already exempt by name in ``listing_projection.STRUCTURAL_RESPONSE_KEYS`` and in
    :data:`SURFACE_STRUCTURAL_KEYS`. Keeping them here would filter out every example instead:
    ``protected_logic_tokens`` keeps document keys too, so the generator's graph scaffolding
    (``name``, ``type``, ``config``, ``nodes``) is in the token set, and those words are the
    column names this fixture is written with.
    """
    if isinstance(value, (str, bytes, bytearray)):
        yield str(value)
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _value_text(item)
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _value_text(item)
        return
    yield str(value)


def _strategy_independent_text() -> Tuple[str, ...]:
    """Every fragment of text these bodies carry irrespective of the drawn strategy.

    Built from *declarations* — this module's own literals, the projection's derived constants
    and the error catalogue — and never from a response under test, so a value that reaches a
    body because of the strategy cannot appear here and cannot be filtered out by it.
    """
    fragments: List[str] = []

    # This module's literals, and the values every body under test is built from.
    fragments.extend(_value_text(PUBLIC_COLUMNS))
    fragments.extend(_value_text(EVIDENCE_SUMMARIES))
    fragments.extend(_value_text(ROUTE_ENRICHMENT))
    fragments.extend(_value_text(PAPER_EVENT_ENVELOPE))
    fragments.append(CREATOR_ALIAS)
    fragments.append(PUBLIC_REQUEST_ID)
    fragments.append(CONDITION_LABEL_PREFIX)

    # Values the projection derives rather than copies: the major-unit price string, the
    # nominal period, and the ``str()`` of the booleans and absent fields a walker sees.
    fragments.append(
        str(money.to_major(PUBLIC_COLUMNS["price_minor"], PUBLIC_COLUMNS["currency"]))
    )
    fragments.append(str(SUBSCRIPTION_PERIOD_NOMINAL_DAYS))
    fragments.extend(("True", "False", "None"))

    # The error catalogue: the code, its one public sentence, and the scrubber's placeholder.
    # Declared constants, so an interpolated node identifier would not be among them.
    for code, details in ERROR_BODIES_UNDER_TEST:
        fragments.append(code)
        fragments.append(marketplace_errors.message_for_code(code))
        # ``details`` is the one body whose keys are the *raiser's* rather than the schema's, so
        # they are searched like any value. The keys this test supplies are declared here.
        fragments.extend(str(key) for key in details)
        fragments.extend(_value_text(details))
    fragments.append(PAPER_ERROR_CODE)
    fragments.append(marketplace_errors.message_for_code(PAPER_ERROR_CODE))
    fragments.append(marketplace_errors.REDACTED_PLACEHOLDER)

    return tuple(fragments)


#: Computed once at import: the corpus is a function of module-level declarations only.
STRATEGY_INDEPENDENT_TEXT: Tuple[str, ...] = _strategy_independent_text()


def _uninformative(token: str) -> bool:
    """Whether ``token`` already occurs in text these bodies carry without any strategy.

    Such a token cannot separate a disclosure from a coincidence: it would be "found" in a
    response assembled before the strategy was drawn. Measured over 1000 examples, 14 (1.4%)
    contain one, so ``assume``-ing those examples away costs Hypothesis almost nothing and leaves
    the property's statement — every token, every value — exactly as written.
    """
    return any(token in text for text in STRATEGY_INDEPENDENT_TEXT)


def _listing_row(fixture: Any, *, carrying_protected_logic: bool) -> Dict[str, Any]:
    """A ``library_strategies``-shaped row, with or without the strategy's Protected_Logic.

    Both variants carry identical public columns and the same ``author_id``, so the projection of
    the one that carries Protected_Logic and the projection of the one that does not are equal
    unless the projection reads a protected column. ``author_id`` is populated because the alias
    check compares against it; it is a denied column and must not surface either.
    """
    row: Dict[str, Any] = dict(PUBLIC_COLUMNS)
    row["author_id"] = fixture.strategy_row.get("user_id")
    if not carrying_protected_logic:
        return row

    for column in STRATEGY_PROTECTED_LOGIC_COLUMNS:
        if column in fixture.strategy_row:
            row[column] = fixture.strategy_row[column]
    for column in VERSION_PROTECTED_LOGIC_COLUMNS:
        if column in fixture.version_row:
            row[column] = fixture.version_row[column]
    return row


def _paper_event_columns() -> FrozenSet[str]:
    """The persisted column vocabulary of the Paper_Channel's two event tables."""
    tables: Any = COLUMN_CONTRACT["paper_session_lifecycle"]["tables"]
    return frozenset(tables["paper_events"]) | frozenset(tables["paper_market_events"])


@PROPERTY_SETTINGS
@given(strategy=protected_logic_strategies())
def test_p47_no_response_or_event_contains_protected_logic(strategy: Any) -> None:
    """P-47: no non-owner Marketplace response — projected, enriched or error — and no
    Paper_Channel event carries any substring of the strategy's Protected_Logic, at any nesting
    depth, for an authenticated non-owner and for an unauthenticated caller.

    **Validates: Requirements 6.3, 6.8, 7.1, 19.7**
    """
    # ── The tokens no body may contain, from the module's own oracle ─────────────
    tokens = protected_logic_tokens(
        strategy.strategy_row,
        strategy.version_row,
        strategy.backtest_rows,
    )
    assert tokens, "the generator must produce Protected_Logic tokens to search for"
    # Skip the example, not the token: see "WHY SOME EXAMPLES ARE SKIPPED" above.
    assume(not any(_uninformative(token) for token in tokens))

    # The source row genuinely carries the logic, so a leak would be detectable. Without this,
    # a projection that dropped the columns for the wrong reason would still look correct.
    source_row = _listing_row(strategy, carrying_protected_logic=True)
    with pytest.raises(AssertionError):
        assert_contains_no_protected_logic(source_row, tokens, SURFACE_STRUCTURAL_KEYS)

    # ── The unauthenticated caller's view: the projection, and nothing else ──────
    projected = project_listing(
        source_row,
        evidence_summaries=EVIDENCE_SUMMARIES,
        creator_alias=CREATOR_ALIAS,
    )
    assert_contains_no_protected_logic(projected, tokens, SURFACE_STRUCTURAL_KEYS)

    # ── The authenticated non-owner's enriched view (Requirement 6.7's "same projection") ──
    enriched: Dict[str, Any] = {**projected, **ROUTE_ENRICHMENT}
    assert_contains_no_protected_logic(enriched, tokens, SURFACE_STRUCTURAL_KEYS)

    # ── Error and diagnostic bodies (Requirement 7.1) ───────────────────────────
    for code, details in ERROR_BODIES_UNDER_TEST:
        body = marketplace_errors.MarketplaceError(code, details=details).to_error_object()
        assert_contains_no_protected_logic(body, tokens, SURFACE_STRUCTURAL_KEYS)
        wrapped = marketplace_errors.structured_error_body(
            marketplace_errors.MarketplaceError(code, details=details),
            request_id=PUBLIC_REQUEST_ID,
        )
        assert_contains_no_protected_logic(wrapped, tokens, SURFACE_STRUCTURAL_KEYS)

    # ── Paper_Channel events (Requirement 19.7); the rest is task 26.1 ──────────
    for event in _paper_event_bodies():
        assert_contains_no_protected_logic(event, tokens, SURFACE_STRUCTURAL_KEYS)

    # The buffer has no column a graph could be persisted in, so the ``payload`` the task-26.1
    # serialiser writes is the only place Requirement 19.7 can be broken.
    protected_columns = frozenset(
        STRATEGY_PROTECTED_LOGIC_COLUMNS
        + VERSION_PROTECTED_LOGIC_COLUMNS
        + BACKTEST_PROTECTED_LOGIC_COLUMNS
    )
    overlap = _paper_event_columns() & protected_columns
    assert not overlap, (
        "the Paper_Channel event tables carry Protected_Logic columns: " f"{sorted(overlap)}"
    )

    # ── Invariance: the public view is a function of the public columns alone ────
    # Stronger than the substring search, and it catches a derivative that shares no
    # three-character run with the source document.
    baseline = project_listing(
        _listing_row(strategy, carrying_protected_logic=False),
        evidence_summaries=EVIDENCE_SUMMARIES,
        creator_alias=CREATOR_ALIAS,
    )
    differing = sorted(
        key
        for key in set(projected) | set(baseline)
        if projected.get(key) != baseline.get(key)
    )
    assert projected == baseline, (
        "the projection changed when the row carried Protected_Logic, so it reads a "
        f"protected column: {differing}"
    )

# ══════════════════════════════════════════════════════════════════════════════════════════
#
#  P-48 — CLONE GATING  (task 17.6, Requirements 7.3, 7.4)
#
# ══════════════════════════════════════════════════════════════════════════════════════════
#
# WHAT P-48 CLAIMS
# ----------------
# For all Listings with source cloning disabled and all non-owner callers, the clone operation
# is refused and creates no `strategies` row.
#
# WHY THE "DISABLED" SIDE IS THE DEFAULT SIDE, NOT A CONFIGURED ONE
# -----------------------------------------------------------------
# Requirement 7.4 says the owner's source-cloning choice is stored, "SHALL default that choice
# to disabled, and SHALL back-fill existing rows to disabled", and
# `007_marketplace_submissions.sql` implements it as
# `source_cloning_enabled BOOLEAN NOT NULL DEFAULT FALSE` — with the migration itself RAISEing
# if the column is nullable or its default is not false. So "cloning disabled" is not an
# unusual state a test has to arrange: it is the state EVERY Listing that existed before 007
# was back-filled to, and the state every Listing created since is born in. The property is
# quantified over exactly that default-closed state, which is why
# `test_source_cloning_is_stored_default_disabled_and_back_filled` below pins the DDL: without
# it, "disabled" would be a value this file made up rather than the shipped default, and the
# property would be about a configuration nobody is in.
#
# :data:`DISABLED_READINGS` therefore spans the three ways the gate can see "disabled": the
# migrated `FALSE`, a `None` (a nullable read of the column, as a database that has not applied
# 007 yet would answer), and the column being absent from the row altogether (a projection that
# never selected it). `clone_strategy`'s gate is `if not lib_entry.get(...)`, so all three must
# refuse; a gate written as `is False` would admit the last two and this property would catch it.
#
# WHAT IS ASSERTED, AND WHY EACH ASSERTION IS SEPARATE
# ---------------------------------------------------
# 1. the refusal is `MARKETPLACE_CLONING_DISABLED` at HTTP 403 (Requirement 7.3's first half);
# 2. no `strategies` row is written — Requirement 7.3's "SHALL create no copy of the strategy",
#    and the assertion the recording double exists for;
# 3. NOTHING at all is written: `clone_count` is unchanged and no `library_ratings`
#    verified-clone marker appears, so a refused clone leaves no trace of having half-happened;
# 4. the owner's `strategies` row is never even READ. Stronger than (2) and independent of it:
#    a path that fetched the owner's `buy_logic` / `sell_logic` / `risk` / `indicators` /
#    `ml_model_path` and then declined to insert them would satisfy (2) while having pulled the
#    Protected_Logic into the process. The generated source row genuinely carries all five
#    (`protected_logic_strategies()`), so this is a statement about a row that has them;
# 5. exactly one Audit_Log entry, and its metadata is the operation and the Listing id and
#    nothing else — structurally, by key set, not by substring search. A substring search
#    against generated tokens is P-47's oracle and belongs there; here it would fail on a
#    generated indicator named `ate` inside the catalogue's own refusal sentence, which is the
#    coincidence P-47's docstring works through at length.
#
# NON-VACUITY
# -----------
# "No `strategies` row is created" is worth nothing from a harness that could not create one.
# `test_the_clone_path_creates_a_strategies_row_once_consent_and_entitlement_are_present` drives
# the SAME double through the SAME handler with consent given and an entitling Subscription, and
# requires the insert to happen and to carry the owner's logic — that is what cloning IS. If the
# double ever stops being able to complete a clone, that test fails and P-48 stops being a
# tautology quietly.
#
# WHY THE HANDLER IS CALLED UNWRAPPED
# -----------------------------------
# `clone_strategy` is decorated `@limiter.limit("20/minute")`, and the shared limiter is armed
# (`core/rate_limit.py` builds a real `Limiter`). Two consequences for a property test: the
# wrapper demands a genuine `starlette.requests.Request`, and 100 examples against a 20/minute
# bucket would be answered 429 from the twenty-first example on — the property would then be
# about the rate limiter rather than about the clone gate. So the test calls
# :data:`CLONE_HANDLER`, the undecorated coroutine underneath, exactly as it would be called
# after the limiter admitted the request. The rate limit itself is asserted by
# `tests/test_library_route_resolution.py`; nothing here removes or loosens it.


#: The three ways a Listing can read as "source cloning disabled". See the banner above.
DISABLED_READINGS: Tuple[str, ...] = ("migrated_false", "null", "column_absent")

#: The moderation states that get a caller PAST `clone_strategy`'s availability check, so the
#: consent gate is the thing under test rather than a 404 arriving first.
CLONEABLE_MODERATION_STATES: Tuple[str, ...] = ("approved", "featured")

#: `clone_strategy` without its rate-limit decorator. `functools.wraps` inside slowapi sets
#: `__wrapped__`, so `inspect.unwrap` reaches the handler itself.
CLONE_HANDLER = inspect.unwrap(lib.clone_strategy)

assert inspect.iscoroutinefunction(CLONE_HANDLER), (
    "clone_strategy no longer unwraps to a coroutine function; the decorator stack changed and "
    "this harness needs revisiting"
)

#: The migration that introduces the column, for the Requirement 7.4 DDL assertion.
MIGRATION_007 = (
    Path(__file__).resolve().parents[2]
    / "backend_app"
    / "migrations"
    / "007_marketplace_submissions.sql"
)


# ── The recording double ───────────────────────────────────────────────────────────────────


class _CloneResponse:
    def __init__(self, data: Any) -> None:
        self.data = data


class _CloneQuery:
    """One link of the supabase-py fluent chain, recording what it was asked to do.

    `.single()` is tracked rather than ignored because `clone_strategy` and the
    Entitlement_Resolver read the SAME table two different ways: the handler's step-1 lookup is
    a `.single()` and wants a dict, the resolver's admission read is an embedded select and
    wants a list. One flag keeps both honest against one row store.
    """

    def __init__(self, table: str, client: "CloneClient") -> None:
        self.table_name = table
        self.client = client
        self.op = "select"
        self.columns: Optional[str] = None
        self.payload: Any = None
        self.filters: List[Tuple[str, Any]] = []
        self.single_row = False

    def select(self, columns: Any) -> "_CloneQuery":
        self.columns = columns
        return self

    def insert(self, payload: Any) -> "_CloneQuery":
        self.op = "insert"
        self.payload = payload
        return self

    def update(self, payload: Any) -> "_CloneQuery":
        self.op = "update"
        self.payload = payload
        return self

    def upsert(self, payload: Any, **_kwargs: Any) -> "_CloneQuery":
        self.op = "upsert"
        self.payload = payload
        return self

    def eq(self, column: str, value: Any) -> "_CloneQuery":
        self.filters.append((column, value))
        return self

    def single(self) -> "_CloneQuery":
        self.single_row = True
        return self

    def execute(self) -> _CloneResponse:
        return self.client.execute(self)


class CloneClient:
    """A scripted-read, recording-write stand-in for the service-role client.

    It holds ONE `library_strategies` row carrying both the columns `clone_strategy` selects
    and the embeds `entitlement_resolver` selects, so there is no second copy of the Listing to
    drift. Every write is recorded rather than applied, which is what makes "no `strategies` row
    is created" and "`clone_count` is unchanged" assertions rather than claims.
    """

    def __init__(
        self,
        *,
        listing: Dict[str, Any],
        source_strategy: Optional[Dict[str, Any]] = None,
        version_rows: Tuple[Dict[str, Any], ...] = (),
        existing_clones: Tuple[Dict[str, Any], ...] = (),
    ) -> None:
        self.listing = listing
        self.source_strategy = source_strategy
        self.version_rows = version_rows
        self.existing_clones = existing_clones
        self.calls: List[_CloneQuery] = []

    def table(self, name: str) -> _CloneQuery:
        return _CloneQuery(name, self)

    def execute(self, query: _CloneQuery) -> _CloneResponse:
        self.calls.append(query)
        if query.op == "insert":
            row = dict(query.payload)
            row.setdefault("id", str(uuid4()))
            return _CloneResponse([row])
        if query.op in ("update", "upsert"):
            return _CloneResponse([dict(query.payload)])
        if query.table_name == "library_strategies":
            return _CloneResponse(
                dict(self.listing) if query.single_row else [dict(self.listing)]
            )
        if query.table_name == "strategy_versions":
            return _CloneResponse([dict(row) for row in self.version_rows])
        if query.table_name == "strategies":
            if query.single_row:
                return _CloneResponse(
                    dict(self.source_strategy) if self.source_strategy else None
                )
            return _CloneResponse([dict(row) for row in self.existing_clones])
        return _CloneResponse([])

    # -- reading the recording ---------------------------------------------------------------
    def writes(self) -> List[_CloneQuery]:
        return [c for c in self.calls if c.op in ("insert", "update", "upsert")]

    def writes_to(self, table: str) -> List[_CloneQuery]:
        return [c for c in self.writes() if c.table_name == table]

    def reads_of(self, table: str) -> List[_CloneQuery]:
        return [c for c in self.calls if c.op == "select" and c.table_name == table]


class _CloneAuditLogger:
    """Records the Audit_Log entries the refusal writes, instead of persisting them."""

    def __init__(self) -> None:
        self.entries: List[Dict[str, Any]] = []

    async def log(self, action: Any, **kwargs: Any) -> None:
        self.entries.append({"action": action, **kwargs})


class _NoRedis:
    """`clone_strategy`'s cache invalidation, neutered.

    The success path scans Redis for `library:browse:*` keys. Returning no client takes the
    `if r_client:` branch that skips it, so no example waits on a socket.
    """

    async def get_client(self) -> None:
        return None


# ── Row builders ───────────────────────────────────────────────────────────────────────────


def _clone_listing_row(
    strategy: Any,
    *,
    listing_id: str,
    reading: str,
    moderation_status: str,
    clone_count: int,
    subscription: Optional[Dict[str, Any]] = None,
    submission_state: str = "PUBLISHED",
) -> Dict[str, Any]:
    """A `library_strategies` row as BOTH readers of it see it.

    `clone_strategy` reads `is_active`, `moderation_status`, `clone_count`, `author_id`,
    `source_strategy_id`, `name` and `source_cloning_enabled`; the Entitlement_Resolver reads
    `author_id`, `source_strategy_id` and the two embedded collections. One row carries both.
    """
    row: Dict[str, Any] = {
        "id": listing_id,
        "author_id": strategy.strategy_row["user_id"],
        "source_strategy_id": strategy.strategy_row["id"],
        "name": strategy.strategy_row["name"],
        "is_active": True,
        "moderation_status": moderation_status,
        "clone_count": clone_count,
        "marketplace_submissions": [{"submission_state": submission_state}],
        "library_subscriptions": [] if subscription is None else [subscription],
    }
    if reading == "migrated_false":
        row["source_cloning_enabled"] = False
    elif reading == "null":
        row["source_cloning_enabled"] = None
    elif reading == "column_absent":
        pass
    else:  # pragma: no cover - guards a typo in DISABLED_READINGS
        raise AssertionError(f"unknown source-cloning reading {reading!r}")
    return row


def _source_strategy_row(strategy: Any) -> Dict[str, Any]:
    """The owner's row as `clone_strategy` step 6 selects it — every protected column present.

    This is the row a refused clone must never read and an admitted clone must copy, so it
    carries the generated Protected_Logic verbatim rather than a placeholder.
    """
    row = {
        "name": strategy.strategy_row["name"],
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "exchange_id": "an-exchange",
    }
    for column in STRATEGY_PROTECTED_LOGIC_COLUMNS:
        if column in strategy.strategy_row:
            row[column] = strategy.strategy_row[column]
    return row


def _fixed_protected_logic_strategy() -> ProtectedLogicFixture:
    """One concrete strategy carrying Protected_Logic, for the deterministic control below.

    Hand-built rather than drawn: the control is not a property, and
    ``protected_logic_strategies().example()`` would pull the Hypothesis engine into a test that
    wants one fixed input. The shape is the generator's — every
    ``STRATEGY_PROTECTED_LOGIC_COLUMNS`` member populated — so the control exercises the same
    columns P-48 asserts are never fetched.
    """
    node_id = "node_alpha"
    strategy_id = str(uuid4())
    logic = [{"node_id": node_id, "indicator": "rsi_fast", "params": {"period": "417"}}]
    return ProtectedLogicFixture(
        strategy_row={
            "id": strategy_id,
            "user_id": str(uuid4()),
            "name": "control_strategy",
            "buy_logic": logic,
            "sell_logic": logic,
            "indicators": [{"name": "rsi_fast", "node_id": node_id}],
            "risk": {"stop_loss": "213"},
            "ml_model_path": "models/control/alpha.pkl",
        },
        version_row={
            "id": str(uuid4()),
            "strategy_id": strategy_id,
            "version": "v4",
            "is_draft": False,
            "blueprint": {"nodes": [{"id": node_id}]},
            "graph_json": {"nodes": [{"id": node_id}]},
        },
        backtest_rows=[],
    )


def _run_clone(client: CloneClient, *, listing_id: str, caller_id: str, audit: Any) -> Any:
    """Call the unwrapped handler with the doubles patched in, per example.

    `mock.patch.object` context managers rather than a `monkeypatch` fixture: Hypothesis's
    `function_scoped_fixture` health check exists precisely because a function-scoped fixture is
    set up once for a whole `@given` run and not once per example. These are entered and exited
    inside the example, so each example gets its own doubles.
    """
    with mock.patch.object(lib, "_build_service_client", lambda: client), mock.patch.object(
        lib, "get_strategy_audit_logger", lambda: audit
    ), mock.patch.object(lib, "redis_manager", _NoRedis()):
        return asyncio.run(
            CLONE_HANDLER(
                request=None,
                library_id=listing_id,
                user={"id": caller_id},
            )
        )


# ── The property ───────────────────────────────────────────────────────────────────────────


@PROPERTY_SETTINGS
@given(
    strategy=protected_logic_strategies(),
    listing_id=identifiers(),
    caller_id=identifiers(),
    reading=st.sampled_from(DISABLED_READINGS),
    moderation_status=st.sampled_from(CLONEABLE_MODERATION_STATES),
    clone_count=st.integers(min_value=0, max_value=1_000_000),
)
def test_p48_clone_is_refused_when_source_cloning_is_disabled(
    strategy: Any,
    listing_id: str,
    caller_id: str,
    reading: str,
    moderation_status: str,
    clone_count: int,
) -> None:
    """P-48: for every Listing whose source cloning is disabled — the migrated default state —
    and every non-owner caller, the clone is refused 403 ``MARKETPLACE_CLONING_DISABLED``, no
    ``strategies`` row is created, nothing at all is written, and the owner's strategy row is
    never even read.

    **Validates: Requirements 7.3, 7.4**
    """
    owner_id = strategy.strategy_row["user_id"]
    # A non-owner caller. Two independent UUID draws collide with vanishing probability, but the
    # owner path is a 409 rather than this refusal, so the distinction is asserted, not assumed
    # away silently.
    assume(caller_id != owner_id)

    listing = _clone_listing_row(
        strategy,
        listing_id=listing_id,
        reading=reading,
        moderation_status=moderation_status,
        clone_count=clone_count,
    )
    client = CloneClient(
        listing=listing,
        source_strategy=_source_strategy_row(strategy),
        version_rows=(
            {
                "id": strategy.version_row["id"],
                "strategy_id": strategy.strategy_row["id"],
                "version": strategy.version_row["version"],
                "is_draft": False,
            },
        ),
    )
    audit = _CloneAuditLogger()

    # ── 1. The refusal (Requirement 7.3) ───────────────────────────────────
    with pytest.raises(MarketplaceError) as caught:
        _run_clone(client, listing_id=listing_id, caller_id=caller_id, audit=audit)

    assert caught.value.code == MARKETPLACE_CLONING_DISABLED
    assert caught.value.http_status == 403

    # ── 2. No copy of the strategy exists (Requirement 7.3's second half) ──
    assert client.writes_to("strategies") == [], (
        "a Listing with source cloning disabled produced a `strategies` row — Requirement 7.3 "
        "says the operation creates no copy of the strategy"
    )

    # ── 3. Nothing at all was written: clone_count untouched, no marker ────
    assert client.writes() == [], (
        "the refused clone wrote to "
        f"{sorted({w.table_name for w in client.writes()})}; a refusal leaves clone_count and "
        "the library_ratings verified-clone marker exactly as they were"
    )

    # ── 4. The owner's Protected_Logic was never even fetched ──────────────
    # Independent of (2): a path that read the owner's buy_logic/sell_logic/risk/indicators/
    # ml_model_path and then declined to insert them would satisfy (2) and still have pulled
    # the logic into the process.
    assert client.reads_of("strategies") == [], (
        "the refused clone read the owner's `strategies` row, so the gate runs after the "
        "Protected_Logic has already been fetched"
    )

    # ── 5. One Audit_Log entry, carrying the operation and the Listing only ─
    assert len(audit.entries) == 1, (
        f"expected one Audit_Log entry for the refusal, got {len(audit.entries)}"
    )
    entry = audit.entries[0]
    assert entry["action"] is StrategyAuditAction.MARKETPLACE_ACCESS_REFUSED
    assert entry["actor_id"] == str(caller_id)
    assert entry["resource_id"] == str(listing_id)
    assert entry["reason"] == MARKETPLACE_CLONING_DISABLED
    # By key set, not by substring: the entry may carry the operation name and the Listing id,
    # and no third thing. P-47 owns the substring oracle.
    assert set(entry["metadata"]) == {"operation", "listing_id"}
    assert entry["metadata"] == {"operation": "clone", "listing_id": str(listing_id)}


# ── Non-vacuity and the Requirement 7.4 default ────────────────────────────────────────────


def test_the_clone_path_creates_a_strategies_row_once_consent_and_entitlement_are_present() -> (
    None
):
    """The control for P-48 assertions 2, 3 and 4: the same double CAN complete a clone.

    Consent given, an ACTIVE unexpired Subscription for the caller, the owner's version
    resolvable — and the handler inserts a `strategies` row carrying the owner's logic, bumps
    `clone_count` and writes the verified-clone marker. Without this, "no `strategies` row is
    created" could pass on a harness that is simply unable to write one.
    """
    strategy = _fixed_protected_logic_strategy()
    listing_id = str(uuid4())
    caller_id = str(uuid4())

    listing = _clone_listing_row(
        strategy,
        listing_id=listing_id,
        reading="migrated_false",
        moderation_status="approved",
        clone_count=7,
        subscription={
            "id": str(uuid4()),
            "user_id": caller_id,
            "status": "active",
            "period_expiry": "2999-01-01T00:00:00+00:00",
        },
    )
    listing["source_cloning_enabled"] = True  # the owner's explicit consent

    client = CloneClient(
        listing=listing,
        source_strategy=_source_strategy_row(strategy),
        version_rows=(
            {
                "id": strategy.version_row["id"],
                "strategy_id": strategy.strategy_row["id"],
                "version": strategy.version_row["version"],
                "is_draft": False,
            },
        ),
    )

    result = _run_clone(
        client, listing_id=listing_id, caller_id=caller_id, audit=_CloneAuditLogger()
    )

    inserts = client.writes_to("strategies")
    assert len(inserts) == 1, "consent plus entitlement must produce exactly one clone row"
    payload = inserts[0].payload
    assert payload["user_id"] == caller_id
    assert payload["source_library_id"] == listing_id
    # Cloning IS the logic transfer Requirement 7.2 permits under explicit consent, so the
    # inserted row carries the owner's protected columns. That is what P-48 asserts the
    # default-closed state prevents.
    for column in ("buy_logic", "sell_logic", "risk", "indicators", "ml_model_path"):
        assert column in payload

    assert client.writes_to("library_strategies"), "clone_count is incremented on success"
    assert client.writes_to("library_ratings"), "the verified-clone marker is written"
    assert client.reads_of("strategies"), "the owner's row is read on the success path"
    assert result["new_strategy_id"], "the success response carries the new clone's identifier"


def test_source_cloning_is_stored_default_disabled_and_back_filled() -> None:
    """Requirement 7.4: the choice is stored, defaults to disabled, and existing rows are
    back-filled to disabled.

    P-48 is quantified over "cloning disabled". This is what makes that the DEFAULT state of
    every Listing rather than a value this file invented: `007_marketplace_submissions.sql`
    adds the column `NOT NULL DEFAULT FALSE`, which both defaults new rows to disabled and — as
    PostgreSQL's `ADD COLUMN … NOT NULL DEFAULT` does — back-fills every existing row to it.
    """
    assert MIGRATION_007.is_file(), f"{MIGRATION_007} is missing"
    ddl = MIGRATION_007.read_text(encoding="utf-8-sig")

    declaration = re.search(
        r"source_cloning_enabled\s+BOOLEAN\s+NOT\s+NULL\s+DEFAULT\s+FALSE",
        ddl,
        re.IGNORECASE,
    )
    assert declaration is not None, (
        "007_marketplace_submissions.sql does not declare `source_cloning_enabled BOOLEAN NOT "
        "NULL DEFAULT FALSE`, so Requirement 7.4's stored, default-disabled, back-filled "
        "choice is not what P-48 is quantified over"
    )
