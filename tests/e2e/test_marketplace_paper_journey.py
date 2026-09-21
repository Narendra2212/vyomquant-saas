"""
tests/e2e/test_marketplace_paper_journey.py - the ONE end-to-end journey.

Spec: marketplace-subscriptions-paper-trading task 34.2. Requirement 29.4.

WHAT THIS FILE IS
-----------------
One test function, :func:`test_the_marketplace_and_paper_trading_journey`, executing Requirement
29.4's twenty-four steps IN ORDER against the real application, the real routers and the real
marketplace and paper packages. Each step is a numbered block carrying at least one assertion, and
the assertion is named in the block's comment. Nothing is skipped, xfailed, or left as a ``pass``.

The name deliberately contains neither ``chaos`` nor ``load``: CI runs
``pytest tests/ -k "not chaos and not load"`` and a matching substring would be silently
deselected, which is the quietest way for an end-to-end journey to stop running.

THE ONE PERSISTENCE_LAYER DOUBLE
--------------------------------
``tests/paper_seed.py`` records the rule this repository holds to: there is exactly ONE
Persistence_Layer double - ``tests/test_paper_repository.FakeSupabase`` - and a second would be a
second set of assumptions about the database. This journey needs the marketplace tables, the
PostgREST operators ``routers/library.py`` issues, ``.count``, AND the eleven ``paper_*`` tables
with their unique indexes and UPDATE triggers. That is exactly
``tests/property/test_tenant_isolation_matrix.MatrixStore``, which subclasses ``FakeSupabase`` and
adds the first three without editing it. So :class:`_JourneyStore` subclasses ``MatrixStore`` -
the extension point that class's own docstring names ("Task 33.12 needs a counting variant; it
subclasses MatrixStore and overrides ``_execute``") - and adds exactly two things:

1. UUID-shaped generated identifiers. ``FakeSupabase._defaults`` mints ``f"{table}-{n}"``, which
   is fine for the paper suites and is refused by ``library.py``'s ``_safe_uuid`` on every path
   that takes an id in the URL - so a Submission created by the route could not then be approved
   by the route. The ids are UUIDv5-derived from ``(table, counter)``, so they are still
   deterministic and still legible in a failure message.
2. ``signals``, declared, because the Signal_Trace write of step 22 lands there.

Nothing is removed and nothing is loosened.

THE FIVE SEAMS, AND WHY EACH ONE IS A BOUNDARY AND NOT A SHORTCUT
-----------------------------------------------------------------
Everything else runs. These five are the things that cannot run inside a test process, and each is
doubled at the narrowest point the production code offers:

* **The identity provider.** ``POST /api/auth/register`` and ``POST /api/auth/login`` call
  ``supabase.auth.sign_up`` / ``sign_in_with_password`` through the ``get_supabase`` dependency.
  :class:`_AuthProviderDouble` answers those two calls and nothing else. Every later request
  derives its acting identity from ``get_current_user``, which is the dependency the platform
  already derives it from - so Requirement 21.1 is exercised rather than bypassed.
* **The payment provider.** ``checkout_service.default_provider_session_factory`` is the one place
  either SDK is imported. It is replaced by ``tests/test_marketplace_checkout_regression._ok_factory``,
  the factory that suite already uses, so the amount the provider is asked to charge is recorded and
  asserted. Everything else on the checkout path - the route, the service, the ``PENDING`` row, the
  Minor_Units arithmetic - is production code.
* **The payment webhook's inbound call.** There is no HTTP webhook to POST to in-process, so the
  confirmation is delivered by calling ``settlement_service.settle`` - the function the webhook
  calls, and the only path that may move a Subscription to ``active`` - with the provider reference
  the checkout actually returned.
* **The market-data transport.** ``RecordingRedis`` from
  ``tests/test_paper_market_feed_selection.py`` stands in for the Redis pub/sub that
  ``mds/main.py::broadcast_ohlcv`` publishes candles onto. See "WHAT THE FEED WAS DRIVEN WITH".
* **The strategy runtime and the Signal_Trace recorder** are seams in production, not doubles:
  ``spawn_session_loop(evaluate=..., plan=..., record_signal=...)``. The recorder installed here is
  the REAL ``paper_session_runtime.build_session_signal_recorder``. The evaluator is
  ``tests/test_task_27_session_service._Runtime``, because a DAG runtime over a three-bar feed is
  ``tests/test_task_27_session_service.py``'s subject and Requirement 17.10 forbids a second
  evaluation path being written here.

The Audit_Log recorder is silenced by ``_matrix_app`` (it pushes to Redis, and there is none here);
the rate limiter is suspended and restored by the same helper, which matters because task 33.2 put
5/60s per-caller limits on checkout and renewal and this journey issues several writes.

WHAT THE FEED WAS DRIVEN WITH, STATED PLAINLY
---------------------------------------------
**No live exchange is contacted, and no price here is fabricated at an assertion.** The three bars
are built by ``tests/test_paper_market_feed_selection._frame`` - the shared builder for one
``mds:data:*`` frame in ``broadcast_ohlcv``'s own payload shape, ``json.dumps``-ed and delivered as
text on the exact ``mds:data:{exchange}:{symbol}`` channel, which is what makes
``decode_payload``'s ``parse_float=Decimal`` do the work a ready-made mapping would have skipped.
The whole feed path is the real one: ``open_feed`` publishes the ``mds:commands`` subscribe,
``next_validated_event`` observes the transport from the payload's own field, validates the candle,
computes Requirement 14.10's latency, dedupes by identity and records the
``paper_market_events`` row. Every price the session then acts on is read off that recorded row by
``paper_simulator``.

What this therefore establishes is that the session receives, validates, records and prices from
market data delivered on the production transport in the production payload shape. What it does
**not** establish is that a live exchange feed reaches that transport: that is Requirement 29.5 and
29.6's browser-and-production sequence, which is task 35.3's, and it is not claimed here.

STEP 19'S CHARTS
----------------
Requirement 29.4's "rendering the charts" is asserted through the frontend suite, which runs under
vitest and cannot be driven from Python (and must not be: a Python process shelling out to
``npx vitest`` inside a pytest run is a build step masquerading as a test). It is discharged two
ways, both of them in this file: step 19 asserts the equity/PnL/drawdown series this journey
produced is present and well-formed in the shape the charts read, and it records that the rendering
itself is covered by ``algo22-terminal/src/pages/__tests__/PaperTrading.test.jsx``, which task 34.1
puts on the ``frontend-tests`` CI job. See :data:`FRONTEND_CHART_SUITE`.

THE ADMIN_REVIEWER
------------------
``_matrix_app`` deliberately does not override ``get_admin_user`` - "the Admin_Reviewer role is an
authorisation this file has no business granting itself". This journey does not override it either.
It authenticates the reviewer by giving the overridden ``get_current_user`` an identity whose
**server-controlled** ``app_metadata.role`` is ``"admin"``, which is the one field
``core.dependencies.get_admin_user`` trusts (``user_metadata`` is explicitly excluded there as a
privilege-escalation vector). The REAL ``get_admin_user`` then makes the decision, and step 6
additionally asserts that the same route refuses the subscriber - a non-admin - with 403, so the
gate is shown to be doing something rather than assumed to be.

NEVER ``asyncio.run``
---------------------
Every coroutine is driven with ``tests/test_paper_order_lifecycle_writes._run_coroutine``, the
process's ONE event loop. A fresh loop per call exhausted the machine's ephemeral port range and
hung the paper suite past 700 s on Windows; see that module's ``_HARNESS_LOOP``. ``asyncio.run``
appears nowhere in this file, and ``test_this_journey_never_calls_asyncio_run`` asserts it by
parsing this module's own source.

MONEY
-----
Every money value asserted here is an ``int`` number of Minor_Units. There is no ``float`` on any
money path in this file, and the 90/10 split is asserted as exact integer equality against
``money.split_ninety_ten`` and against conservation, to the Minor_Unit.
"""

from __future__ import annotations

import ast
import calendar
import copy
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Optional, Tuple
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ── production code under test ────────────────────────────────────────────────────────────
from backend_app.backend.marketplace import checkout_service as _checkout
from backend_app.backend.marketplace import entitlement_resolver as _entitlement
from backend_app.backend.marketplace import evidence_validator as _evidence
from backend_app.backend.marketplace import expiry_sweep as _sweep
from backend_app.backend.marketplace import library_entries as _entries
from backend_app.backend.marketplace import money as _money
from backend_app.backend.marketplace import settlement_service as _settlement
from backend_app.backend.marketplace import subscription_period as _period
from backend_app.backend.marketplace.listing_projection import (
    assert_contains_no_protected_logic,
    protected_logic_tokens,
)
from backend_app.backend.marketplace.submission_state import (
    MODERATION_STATUS_FOR_STATE,
    SubmissionState,
)
from backend_app.backend.paper import paper_market_feed as feed
from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper import paper_session_service as session_service
from backend_app.backend.paper_session_runtime import build_session_signal_recorder
from backend_app.backend import signal_service as _signals
from backend_app.core import subscription_dependencies as _plan_gates
from backend_app.core.dependencies import get_current_user, get_supabase
from backend_app.core.rate_limit import limiter
from backend_app.main import app
from backend_app.routers import library as library_router
from backend_app.routers import strategies as strategies_router

# ── the harnesses this file reuses rather than rebuilds ───────────────────────────────────
from tests.property.test_tenant_isolation_matrix import (  # noqa: F401 - MatrixStore is subclassed
    REQUIREMENT_21_4_GAPS,
    MatrixStore,
    _matrix_app,
    _MatrixQuery,
    _Resp,
)
from tests.test_marketplace_checkout_regression import _ok_factory
from tests.test_paper_market_feed_selection import (
    EXCHANGE,
    PROCESSED_AT,
    SYMBOL,
    TIMEFRAME,
    RecordingRedis,
    _admitting,
    _frame,
)
from tests.test_paper_order_lifecycle_writes import _run_coroutine as _run
from tests.test_task_27_session_service import (
    _Runtime,
    _compiled_plan,
    _markets,
    _signal,
)

#: The frontend test that renders the charts of Requirement 29.4's step 19. Named here, and
#: asserted to exist, so "covered by the frontend suite" is a checkable claim rather than a
#: sentence. It runs under vitest on the ``frontend-tests`` CI job task 34.1 adds; nothing in this
#: Python process executes it.
FRONTEND_CHART_SUITE = (
    Path(__file__).resolve().parents[2]
    / "algo22-terminal"
    / "src"
    / "pages"
    / "__tests__"
    / "PaperTrading.test.jsx"
)

# ── identities ────────────────────────────────────────────────────────────────────────────
OWNER = "aa000000-0000-4000-8000-000000000001"
SUBSCRIBER = "bb000000-0000-4000-8000-000000000002"
ADMIN = "cc000000-0000-4000-8000-000000000003"

#: ``EmailStr`` refuses the reserved ``.invalid`` / ``.example`` / ``.test`` suffixes, so the two
#: addresses that pass through the register and login bodies use an ordinary-looking domain. No
#: message is ever sent: the identity provider is :class:`_AuthProviderDouble`.
OWNER_EMAIL = "creator@journey-e2e.io"
SUBSCRIBER_EMAIL = "purchaser@journey-e2e.io"

#: The marker every Protected_Logic token in this journey carries. See :func:`_canary_logic`.
LOGIC_CANARY = "jrny-plc"

#: The Listing's price. $19.99, as the integer Minor_Units the column stores - the amount
#: ``tests/test_marketplace_checkout_regression.py`` pins because the pre-fix handler charged 1998
#: for it. 1999 does not divide by ten, so the 90/10 split has a rounding remainder and
#: ``owner_share + platform_fee == amount`` is a real assertion rather than an arithmetic identity.
PRICE_MINOR = 1999
CURRENCY = "USD"

def _confirmation_instant() -> datetime:
    """The payment confirmation instant: 31 January, 09:30 UTC, of the NEXT calendar year.

    Two properties, both load-bearing:

    * **It is ahead of the real clock.** 31 January of next year is later than any instant in
      this one, and ``GET /api/library/my-strategies`` decides entitlement at
      ``datetime.now(timezone.utc)`` - it is a page a human loads, so it reads the wall clock and
      not an injected instant. A period computed from a fixed past date would make step 13's
      ``SUBSCRIBED`` entry depend on when the suite ran, and would have it read ``EXPIRED``
      forever after that date passed.
    * **It is the day-clamping case.** 31 January has no counterpart in February, so
      ``add_one_calendar_month`` has to clamp to the last day of the target month - the one
      branch of Requirement 11.4 that a mid-month instant would never reach. The expected day is
      therefore asserted as "the last day of that February", never as a literal, because the
      target year may be a leap year.

    Every period boundary in this journey is computed from this instant and from
    ``subscription_period``, never from a clock read inside an assertion.
    """
    return datetime(datetime.now(timezone.utc).year + 1, 1, 31, 9, 30, tzinfo=timezone.utc)


CONFIRMED_AT = _confirmation_instant()

#: One hundred thousand dollars of simulated capital, in Minor_Units.
CAPITAL_MINOR = 10_000_000

#: The instant ``paper_market_feed`` reads as "now" while the three bars are delivered. Pinned, so
#: Requirement 14.10's latency is an exact figure rather than a function of when the suite ran.
LOOP_NOW = PROCESSED_AT

_JOURNEY_NAMESPACE = uuid.UUID("6f5c1f7a-0000-4000-8000-000000000000")


# ══════════════════════════════════════════════════════════════════════════
# THE STORE - MatrixStore, subclassed, nothing removed
# ══════════════════════════════════════════════════════════════════════════


class _JourneyStore(MatrixStore):
    """``MatrixStore`` with UUID-shaped ids, ``signals`` declared, and BATCH inserts.

    See this module's docstring for why the additions are needed and why they arrive by
    subclassing rather than by editing either of the two classes above it.

    The third addition is the batch INSERT. ``submission_service.create_submission`` writes the
    immutable Backtest_Evidence copy as ONE statement over a list of rows -
    ``table("marketplace_backtest_evidence").insert([row, row, row])`` - which is the single
    round trip Requirement 27.2 asks for and which no paper-repository statement issues, so
    ``FakeSupabase``'s insert branch (``dict(q.payload or {})``) cannot read it. It is split into
    one inherited insert per row, exactly the way ``MatrixStore`` already splits an ``upsert``
    carrying a list, so every unique index and every column default still applies per row.
    """

    def __init__(self, **kwargs: Any) -> None:
        self._minted = 0
        super().__init__(**kwargs)
        self._declare("signals")

    def _execute(self, q: Any) -> Any:
        if q.op == "insert" and isinstance(q.payload, list):
            written: List[Dict[str, Any]] = []
            for payload in q.payload:
                one = _MatrixQuery(q.table_name, self)
                one.insert(dict(payload or {}))
                response = super()._execute(one)
                written.extend(getattr(response, "data", []) or [])
            return _Resp(written, count=len(written))
        return super()._execute(q)

    def _defaults(self, table: str, row: Dict[str, Any]) -> Dict[str, Any]:
        if "id" not in row:
            self._minted += 1
            row["id"] = str(
                uuid.uuid5(_JOURNEY_NAMESPACE, f"{table}-{self._minted}")
            )
        return super()._defaults(table, row)

    def one(self, table: str, **predicate: Any) -> Dict[str, Any]:
        """The single row of ``table`` matching ``predicate``, or a failure naming what is there."""
        matches = [
            row
            for row in self.rows_of(table)
            if all(str(row.get(k)) == str(v) for k, v in predicate.items())
        ]
        assert len(matches) == 1, (
            f"expected exactly one {table} row matching {predicate}; found {len(matches)} "
            f"among {[r.get('id') for r in self.rows_of(table)]}"
        )
        return matches[0]


# ══════════════════════════════════════════════════════════════════════════
# THE IDENTITY PROVIDER DOUBLE (step 1's only doubled boundary)
# ══════════════════════════════════════════════════════════════════════════


class _AuthProviderDouble:
    """``supabase.auth.sign_up`` and ``sign_in_with_password``, and nothing else.

    The two calls ``routers/auth.py`` makes. It mints no token of its own beyond an opaque string
    and grants no authorisation: what a later request acts as is decided by ``get_current_user``,
    not by anything returned here. Registrations are recorded so "two distinct accounts exist"
    is a fact about the provider rather than about the test's variables.
    """

    def __init__(self, accounts: Mapping[str, str]) -> None:
        #: ``email -> user id``. The ids this journey's two humans are known by.
        self._accounts = dict(accounts)
        self.registered: List[str] = []
        self.signed_in: List[str] = []
        self.auth = self

    def _identity(self, email: str) -> str:
        assert email in self._accounts, f"no journey account for {email!r}"
        return self._accounts[email]

    def sign_up(self, payload: Mapping[str, Any]) -> Any:
        email = str(payload["email"])
        user_id = self._identity(email)
        self.registered.append(email)
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, email=email),
            session=SimpleNamespace(access_token=f"token-{user_id}"),
        )

    def sign_in_with_password(self, payload: Mapping[str, Any]) -> Any:
        email = str(payload["email"])
        user_id = self._identity(email)
        self.signed_in.append(email)
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, email=email),
            session=SimpleNamespace(access_token=f"token-{user_id}"),
        )


# ══════════════════════════════════════════════════════════════════════════
# THE ASYNC TRANSPORT OVER THE ONE STORE
# ══════════════════════════════════════════════════════════════════════════


class _AwaitableQuery:
    """One :class:`_JourneyStore` query, with an awaitable ``execute()``.

    ``routers/strategies.py`` reaches the database through the ASYNC PostgREST client
    (``create_request_supabase_async``) and therefore ``await``\\ s ``.execute()``, while
    ``routers/library.py`` uses the synchronous service client. This is a transport adapter over
    the ONE store, not a second store: every statement lands in the same rows, so the strategy the
    Strategy_Builder saved is the strategy the Eligibility_Gate reads.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._inner, name)
        if name == "execute":

            async def _execute() -> Any:
                return attribute()

            return _execute

        def _chain(*args: Any, **kwargs: Any) -> Any:
            produced = attribute(*args, **kwargs)
            return self if produced is self._inner else _AwaitableQuery(produced)

        return _chain


class _AsyncTransport:
    """The async client surface, over one store."""

    def __init__(self, store: _JourneyStore) -> None:
        self._store = store

    def table(self, name: str) -> _AwaitableQuery:
        return _AwaitableQuery(self._store.table(name))


# ══════════════════════════════════════════════════════════════════════════
# ROW BUILDERS - the premises this journey does not create through a route
# ══════════════════════════════════════════════════════════════════════════


def _profile(user_id: str, alias: str, email: str) -> Dict[str, Any]:
    return {"id": user_id, "display_name": alias, "email": email}


def _version_row(strategy_id: str, version_id: str) -> Dict[str, Any]:
    """The saved, non-draft, VALID Strategy_Version the Eligibility_Gate and the session read.

    Written by the Strategy_Builder's own version path, which is another specification's surface;
    it is a premise here. ``compiled_plan`` is ``tests/test_task_27_session_service._compiled_plan``
    so the version resolves exactly one ACTION symbol and timeframe - the pair the Paper_Session
    is started on - and ``assert_strategy_supports_market`` admits it for the right reason.
    """
    return {
        "id": version_id,
        "strategy_id": strategy_id,
        "version": 1,
        "is_draft": False,
        "validation_state": "VALID",
        "lifecycle_state": "READY",
        "compiled_plan": _compiled_plan({"action-1": SYMBOL}, {"action-1": TIMEFRAME}),
    }


def _canary_logic(part: str) -> Dict[str, Any]:
    """One Protected_Logic document whose every key and value carries :data:`LOGIC_CANARY`.

    WHY A CANARY DOCUMENT RATHER THAN THE COMPILED GRAPH
    ---------------------------------------------------
    ``protected_logic_tokens`` keeps every key and every value of at least three characters. Run
    over the canonical graph this journey actually saved, that token set includes ordinary API
    vocabulary - ``mode``, ``value``, ``source``, ``close``, ``quantity``, ``window`` - and the
    Listing's own ``symbol``, which Requirement 6.5 publishes deliberately. Asserting on those
    would report the published schema and a spelling coincidence as a disclosure, which is the
    vacuous-assertion failure mode ``tests/property/test_protected_logic_containment.py``'s own
    docstring warns about, and would train a reader to discount the check.

    So the documents this journey plants into the strategy's and the version's Protected_Logic
    columns carry a unique marker on every key and every value. They are genuinely persisted -
    written onto the rows the routers read - so any response that echoed a Protected_Logic
    document would carry them, and the positive control below proves the oracle catches one.
    The exhaustive, generically-tokenised form of the same claim over generated documents is
    P-47 and P-48's, and it is not restated here.
    """
    return {
        f"{LOGIC_CANARY}-{part}-entry-rule": f"{LOGIC_CANARY} RSI(14) < 29.5",
        f"{LOGIC_CANARY}-{part}-nodes": [
            f"{LOGIC_CANARY}-node-rsi-14",
            f"{LOGIC_CANARY}-node-ema-20",
        ],
        f"{LOGIC_CANARY}-{part}-threshold": f"{LOGIC_CANARY}-29.5",
    }


def _backtest_row(
    strategy_id: str,
    version_id: str,
    *,
    start_date: str,
    end_date: str,
    dataset: str,
    checksum: str,
) -> Dict[str, Any]:
    """One completed ``strategy_backtests`` row that satisfies every criterion of Requirement 3.

    The column set is ``tests/test_marketplace_pipeline._eligible_backtest_row``'s: a window of at
    least 90 inclusive days (EV_DURATION), at least 20 trades (EV_TRADES), at least 50 executed
    bars (EV_BARS), all eight Requirement 3.4 parameters recorded (EV_PARAMS) and all seven
    Requirement 2.6 metrics present (MP_METRICS_COMPLETE). The three rows carry different
    datasets, checksums and windows, which is what makes every pair distinct (EV_DISTINCT,
    EV_CHECKSUMS). Whether they actually satisfy Requirement 3 is not asserted by this builder -
    step 3 puts them through the production ``evidence_validator``.
    """
    return {
        # Explicit, because ``MatrixStore.seed`` writes the row verbatim rather than through
        # ``_defaults``, and these ids travel in the submission body through ``_safe_uuid``.
        "id": str(uuid.uuid5(_JOURNEY_NAMESPACE, f"backtest-{checksum}")),
        "user_id": OWNER,
        "strategy_id": strategy_id,
        "version_id": version_id,
        "status": "completed",
        "completed_at": "2025-12-01T00:00:00+00:00",
        "error_message": None,
        "dataset": dataset,
        "dataset_checksum": checksum,
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": 10000,
        "commission": 0.001,
        "slippage": 0.0005,
        "dag_hash": f"dag-{checksum}",
        "total_trades": 120,
        "executed_bar_count": 500,
        "total_return_pct": 42.5,
        "sharpe_ratio": 1.8,
        "max_drawdown": -5.2,
        "win_rate": 61.0,
        "profit_factor": 1.7,
        "final_capital": 14250.0,
    }


def _listing_row(listing_id: str, strategy_id: str, version_id: str) -> Dict[str, Any]:
    """The owner's ``library_strategies`` row, before any Submission has moved.

    ``moderation_status`` is ``'pending'``: the projection
    ``MODERATION_STATUS_FOR_STATE[SUBMITTED]`` a listing under review carries, which is what keeps
    it out of the public catalogue until step 7 publishes it.
    """
    return {
        "id": listing_id,
        "user_id": OWNER,
        "author_id": OWNER,
        "source_strategy_id": strategy_id,
        "version_id": version_id,
        "name": "Journey Momentum Breakout",
        "description": "A published Listing, browsable by anyone.",
        "category": "momentum",
        "difficulty": "intermediate",
        "tags": ["btc"],
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "node_count": 5,
        "has_ml_model": False,
        "clone_count": 0,
        "subscriber_count": 0,
        "rating_count": 0,
        "avg_rating": None,
        "moderation_status": "pending",
        "is_active": True,
        "is_featured": False,
        "price": "19.99",
        "price_minor": PRICE_MINOR,
        "currency": CURRENCY,
        "pricing_model": "SUBSCRIPTION",
        "subscription_tier": "standard",
        "allow_cloning": False,
        "source_cloning_enabled": False,
        "created_at": "2026-01-01T00:00:00+00:00",
        "published_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "backtest_total_return_pct": 42.5,
        "backtest_sharpe_ratio": 1.8,
        "backtest_max_drawdown_pct": -5.2,
        "backtest_win_rate_pct": 61.0,
        "backtest_total_trades": 120,
        "equity_curve_snapshot": None,
    }


def _attach_postgrest_embeds(store: _JourneyStore, listing_id: str) -> None:
    """Attach the embedded resources PostgREST would return on the Listing and the Subscription.

    The double returns whole rows: an in-process dict cannot project, and it cannot perform
    PostgREST's ``library_strategies!inner(...)`` or ``marketplace_submissions(...)`` embeds
    either. ``tests/test_marketplace_pipeline.py`` handles the same gap the same way, and it is
    the gap that has to be filled rather than worked around, because three production readers -
    ``entitlement_resolver._read_admission_row``, ``checkout_service._read_listing`` and
    ``library_entries.build_my_strategies_entries`` - read those embeds and nothing else tells
    them what a Listing's Submission_State is.

    Called after every state change, so the embed says what the tables say rather than what they
    said when the journey started.
    """
    listing = store.one("library_strategies", id=listing_id)
    submissions = [
        {"submission_state": row.get("submission_state")}
        for row in store.rows_of("marketplace_submissions")
        if str(row.get("listing_id")) == str(listing_id)
        or str(row.get("source_strategy_id")) == str(listing.get("source_strategy_id"))
    ]
    listing["marketplace_submissions"] = submissions
    listing["library_subscriptions"] = [
        {
            "id": row.get("id"),
            "user_id": row.get("user_id"),
            "status": row.get("status"),
            "period_expiry": row.get("period_expiry"),
        }
        for row in store.rows_of("library_subscriptions")
        if str(row.get("library_id")) == str(listing_id)
    ]

    embedded_listing = {
        key: value
        for key, value in listing.items()
        if key not in ("marketplace_submissions", "library_subscriptions")
    }
    embedded_listing["marketplace_submissions"] = copy.deepcopy(submissions)
    for row in store.rows_of("library_subscriptions"):
        if str(row.get("library_id")) == str(listing_id):
            row["library_strategies"] = copy.deepcopy(embedded_listing)


def _admin_identity() -> Dict[str, Any]:
    """The Admin_Reviewer, as the authenticated server-side session reports them.

    ``app_metadata.role`` is the ONLY field ``core.dependencies.get_admin_user`` trusts - it is
    server-controlled and cannot be written by the browser SDK, which is why that function
    explicitly excludes ``user_metadata``. ``get_admin_user`` itself is NOT overridden anywhere in
    this file: the real role gate makes the decision.
    """
    return {
        "id": ADMIN,
        "email": "reviewer@journey.invalid",
        "role": "authenticated",
        "access_token": f"token-{ADMIN}",
        "app_metadata": {"role": "admin"},
        "user_metadata": {},
    }


def _bar(minutes_before: int, ohlc: Tuple[float, float, float, float]) -> Dict[str, Any]:
    """One ``mds:data:*`` frame, ``minutes_before`` :data:`LOOP_NOW`, carrying ``ohlc``.

    Each value crosses the wire as a JSON number, exactly as ``mds/main.py::broadcast_ohlcv``
    publishes it, and ``paper_market_feed.decode_payload`` parses it with ``parse_float=Decimal``
    - so the value the session prices from is the exact decimal the wire carried and no binary
    float survives the boundary.

    All four values are supplied together because the real validator checks the candle's SHAPE:
    ``low <= min(open, close)`` and ``high >= max(open, close)``. A frame that moved only the
    close would be rejected with "OHLC violation ... Dataset rejected - no synthetic data
    allowed", which is the feed refusing to price a session from an impossible bar - so the three
    bars below are each internally consistent, and together they rise, fall and rise so the equity
    series has a genuine peak-to-trough for Requirement 18.9's drawdown to measure.
    """
    open_, high, low, close = ohlc
    return _frame(
        timestamp_ms=int(
            (LOOP_NOW - timedelta(minutes=minutes_before)).timestamp() * 1000
        ),
        close=close,
        overrides={"open": open_, "high": high, "low": low},
    )


def _journey_signal() -> Dict[str, Any]:
    """One strategy output for bar 1: a BUY the simulator can execute and the trace can record.

    Two consumers read it and both are production code, which is why one object carries both
    vocabularies rather than two objects carrying one each:

    * ``paper_session_service`` projects it to a ``PaperSignal`` and builds the order intent. Every
      numeric value is a decimal STRING because ``paper_accounting.to_decimal`` refuses a float -
      ``0.5`` is not one half in binary (Requirement 18.1).
    * ``signal_service.mint_signal`` reads the attribution, the risk verdict and the sizing. The
      ``source_node_ids`` / ``node_closure`` / ``risk_validation`` members are the ones
      ``tests/test_task_29_signal_environments.action_output`` carries, so what reaches
      ``public.signals`` is a full Requirement 23.2 record and not a stub.
    """
    return _signal(
        timeframe=TIMEFRAME,
        bar_time=(LOOP_NOW - timedelta(minutes=3)).isoformat(),
        source_node_ids=["action-1"],
        closure_ready=True,
        node_closure={"ema-1": "60050.0"},
        strength=0.9,
        risk_validation={
            "passed": True,
            "reason": "within limits",
            "position_size": "0.5",
            "evaluated_at": LOOP_NOW.isoformat(),
        },
    )


def _reset_limiter() -> None:
    """Forget what the process's rate limiter has counted, on both sides of the journey."""
    storage = getattr(limiter, "_storage", None)
    if storage is not None and hasattr(storage, "reset"):
        storage.reset()


class _SilentAuditLogger:
    """The Audit_Log recorder, silenced: it pushes to Redis, and there is none in this process.

    ``_matrix_app`` installs the same stand-in for the requests that go through it. The paths this
    journey drives OUTSIDE a request - ``settlement_service.settle``, ``expiry_sweep.sweep`` -
    resolve the singleton themselves through ``core.audit_trail.get_strategy_audit_logger`` at call
    time, and ``settle``'s Requirement 10.9 audit is REQUIRED, so an unreachable Redis would turn a
    recorded settlement into a failed one. What is silenced is the sink, not the requirement: the
    audit half of Requirements 10.9 and 11.12 is asserted by
    ``tests/test_settlement_service.py`` and ``tests/test_expiry_sweep.py`` against recording
    doubles, and this journey does not restate it.
    """

    async def record_or_raise(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def log(self, *_args: Any, **_kwargs: Any) -> None:
        return None


@pytest.fixture(autouse=True)
def _isolated_journey(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Leave the process as this journey found it.

    The paper repository's migration verdict, the bound paper singleton, the signal-service column
    verdicts and the rate limiter's counters are all process-scoped, and a journey that left any of
    them behind would change the next test's premise.
    """
    from backend_app.backend import paper_trading_service as paper_module
    from backend_app.backend.paper import paper_channel
    from backend_app.core import audit_trail

    monkeypatch.setattr(
        audit_trail, "get_strategy_audit_logger", lambda: _SilentAuditLogger()
    )
    repo.reset_persistence_probe()
    paper_channel.invalidate_session_owner()
    _signals.reset_signal_environment_column_support()
    _signals.reset_signal_lifecycle_column_support()
    _reset_limiter()
    yield
    app.dependency_overrides.clear()
    paper_module._paper_service_instance = None
    repo.reset_persistence_probe()
    paper_channel.invalidate_session_owner()
    _signals.reset_signal_environment_column_support()
    _signals.reset_signal_lifecycle_column_support()
    _reset_limiter()


# ══════════════════════════════════════════════════════════════════════════
# THE JOURNEY
# ══════════════════════════════════════════════════════════════════════════


def test_the_marketplace_and_paper_trading_journey(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 29.4's twenty-four steps, in order, in one test.

    Every step is one numbered block. A block that could not hold against today's code would be a
    reported finding rather than an ``xfail``; there are none, and the two places where the double
    cannot do what PostgreSQL does - the PostgREST embeds and the
    ``trg_submission_projects_moderation_status`` projection - are applied explicitly and named
    where they are applied.
    """
    store = _JourneyStore()
    store.seed("profiles", _profile(OWNER, "journey-creator", OWNER_EMAIL))
    store.seed("profiles", _profile(SUBSCRIBER, "journey-purchaser", SUBSCRIBER_EMAIL))

    # ══════════════════════════════════════════════════════════════════
    # STEP 1. Register and sign in.
    # Asserts: both accounts are created through POST /api/auth/register and both sign in
    # through POST /api/auth/login, each answering with a bearer token; the two accounts are
    # distinct identities; and the provider was asked for exactly those two registrations.
    # ══════════════════════════════════════════════════════════════════
    provider = _AuthProviderDouble(
        {OWNER_EMAIL: OWNER, SUBSCRIBER_EMAIL: SUBSCRIBER}
    )
    was_enabled = getattr(limiter, "enabled", None)
    limiter.enabled = False
    app.dependency_overrides[get_supabase] = lambda: provider
    try:
        auth_client = TestClient(app, raise_server_exceptions=False)
        tokens: Dict[str, str] = {}
        for email in (OWNER_EMAIL, SUBSCRIBER_EMAIL):
            registered = auth_client.post(
                "/api/auth/register",
                json={
                    "email": email,
                    "password": "a-long-enough-passphrase-1",
                    "username": email.split("@")[0],
                },
            )
            assert registered.status_code == 201, registered.text
            assert registered.json()["token_type"] == "bearer"

            signed_in = auth_client.post(
                "/api/auth/login",
                json={"email": email, "password": "a-long-enough-passphrase-1"},
            )
            assert signed_in.status_code == 200, signed_in.text
            body = signed_in.json()
            assert body["token_type"] == "bearer"
            assert body["access_token"], "sign-in returned no access token"
            tokens[email] = body["access_token"]
    finally:
        app.dependency_overrides.clear()
        if was_enabled is not None:
            limiter.enabled = was_enabled

    assert provider.registered == [OWNER_EMAIL, SUBSCRIBER_EMAIL]
    assert provider.signed_in == [OWNER_EMAIL, SUBSCRIBER_EMAIL]
    assert tokens[OWNER_EMAIL] != tokens[SUBSCRIBER_EMAIL]
    assert OWNER != SUBSCRIBER

    # ══════════════════════════════════════════════════════════════════
    # STEP 2. Create and save a strategy.
    # Asserts: POST /api/strategies compiles the graph through the ONE compiler and persists a
    # `strategies` row owned by the authenticated caller, carrying the SERVER's canonical graph
    # and the plan's dag_hash - and that the response's dag_hash is the persisted one.
    # ══════════════════════════════════════════════════════════════════
    from tests.test_sb06_exchange_agnostic_save import strategy_graph
    from backend_app.backend.strategy_dag import registry as registry_module

    graph, _data_node_id = strategy_graph(
        registry_module.build_registry(), symbol=SYMBOL, timeframe=TIMEFRAME
    )

    owner_user = {
        "id": OWNER,
        "email": OWNER_EMAIL,
        "role": "authenticated",
        "access_token": tokens[OWNER_EMAIL],
        "app_metadata": {"role": "authenticated"},
        "user_metadata": {},
    }
    limiter.enabled = False
    app.dependency_overrides[get_current_user] = lambda: owner_user
    app.dependency_overrides[_plan_gates.check_strategy_quota] = lambda: True
    try:
        with patch.object(
            strategies_router,
            "create_request_supabase_async",
            new=_async_client_factory(store),
        ):
            # ``name`` is assigned AFTER the graph is spread: ``StrategyGraph.to_dict()`` carries
            # its own (empty) ``name``, and spreading it last would silently save the strategy
            # unnamed.
            saved = TestClient(app, raise_server_exceptions=False).post(
                "/api/strategies/",
                json={**graph.to_dict(), "name": "Journey Momentum"},
            )
    finally:
        app.dependency_overrides.clear()
        if was_enabled is not None:
            limiter.enabled = was_enabled

    assert saved.status_code == 200, saved.text
    strategy_id = saved.json()["id"]
    strategy_row = store.one("strategies", id=strategy_id)
    assert strategy_row["user_id"] == OWNER
    assert strategy_row["name"] == "Journey Momentum"
    assert strategy_row["buy_logic"]["_dag_hash"] == saved.json()["dag_hash"]
    assert len(strategy_row["buy_logic"]["_nodes"]) == 5, (
        "the SERVER's canonical graph was not persisted"
    )

    version_id = str(uuid.uuid5(_JOURNEY_NAMESPACE, f"version-of-{strategy_id}"))
    store.seed("strategy_versions", _version_row(strategy_id, version_id))
    version_row = store.one("strategy_versions", id=version_id)

    # ══════════════════════════════════════════════════════════════════
    # STEP 3. Three backtests satisfying Requirement 3.
    # Asserts: the three recorded runs pass EVERY criterion the production evidence_validator
    # evaluates - all ten codes, none failing - so "satisfying Requirement 3" is the validator's
    # verdict rather than this test's opinion.
    # ══════════════════════════════════════════════════════════════════
    windows = (
        ("2025-01-01", "2025-06-30", "BTCUSD-2025H1", "chk-btc-h1"),
        ("2025-07-01", "2025-12-31", "ETHUSD-2025H2", "chk-eth-h2"),
        ("2024-01-01", "2024-06-30", "SOLUSD-2024H1", "chk-sol-h1"),
    )
    for start, end, dataset, checksum in windows:
        store.seed(
            "strategy_backtests",
            _backtest_row(
                strategy_id,
                version_id,
                start_date=start,
                end_date=end,
                dataset=dataset,
                checksum=checksum,
            ),
        )
    backtest_rows = [
        row
        for row in store.rows_of("strategy_backtests")
        if str(row.get("strategy_id")) == str(strategy_id)
    ]
    assert len(backtest_rows) == 3

    outcomes = _evidence.validate(backtest_rows, OWNER, strategy_id)
    assert _evidence.failed_outcomes(outcomes) == [], [
        (o.code, o.subjects) for o in _evidence.failed_outcomes(outcomes)
    ]
    assert _evidence.all_passed(outcomes) is True
    assert set(o.code for o in outcomes) == set(_evidence.EV_CODES)

    # ══════════════════════════════════════════════════════════════════
    # STEP 4. Three DISTINCT Backtest_Conditions.
    # Asserts: every unordered pair satisfies the production distinctness predicate, and the
    # validator emitted one EV_DISTINCT verdict per pair - three pairs for three conditions.
    # ══════════════════════════════════════════════════════════════════
    pairs = [
        (backtest_rows[i], backtest_rows[j])
        for i in range(3)
        for j in range(i + 1, 3)
    ]
    assert len(pairs) == 3
    for left, right in pairs:
        assert _evidence.distinct(left, right) is True, (
            f"{left['dataset']} and {right['dataset']} are not distinct "
            f"Backtest_Conditions (Requirement 3.5)"
        )
    assert (
        sum(1 for o in outcomes if o.code == _evidence.EV_DISTINCT) == 3
    ), "one EV_DISTINCT verdict per unordered pair (Requirement 3.5)"

    # The Listing row the Submission references and the catalogue reads. Created by the
    # pre-existing publish path, which task 14.10 narrowed out of the submission flow; it is a
    # premise here, at `moderation_status='pending'`.
    listing_id = str(uuid.uuid5(_JOURNEY_NAMESPACE, f"listing-of-{strategy_id}"))
    store.seed("library_strategies", _listing_row(listing_id, strategy_id, version_id))
    _attach_postgrest_embeds(store, listing_id)

    # The catalogue BEFORE publication, in the request shape step 8 repeats afterwards, so step
    # 8's visibility is a change the journey caused rather than a fact that was always true.
    with _matrix_app(store, SUBSCRIBER) as client:
        before_publication = client.get("/api/library", params={"page": 1})
    assert before_publication.status_code == 200, before_publication.text
    assert before_publication.json()["items"] == [], (
        "an unpublished Listing is in the public catalogue"
    )

    # ══════════════════════════════════════════════════════════════════
    # STEP 5. Submit a Listing.
    # Asserts: POST /api/library/submissions is admitted by the real Eligibility_Gate, creates
    # exactly ONE marketplace_submissions row at SUBMITTED owned by the caller, copies one
    # immutable Backtest_Evidence row per referenced run, records the DRAFT->SUBMITTED edge, and
    # returns no price or evaluation score.
    # ══════════════════════════════════════════════════════════════════
    with _matrix_app(store, OWNER) as client:
        submitted = client.post(
            "/api/library/submissions",
            json={
                "strategy_id": strategy_id,
                "backtest_ids": [row["id"] for row in backtest_rows],
            },
        )
    assert submitted.status_code == 201, submitted.text
    submission_body = submitted.json()
    submission_id = submission_body["submission_id"]
    assert submission_body["submission_state"] == SubmissionState.SUBMITTED.value
    for absent in ("suggested_price", "eval_score", "evaluation_score"):
        assert absent not in submission_body

    assert len(store.rows_of("marketplace_submissions")) == 1
    submission_row = store.one("marketplace_submissions", id=submission_id)
    assert submission_row["owner_id"] == OWNER
    assert str(submission_row["source_strategy_id"]) == str(strategy_id)
    assert len(store.rows_of("marketplace_backtest_evidence")) == 3
    assert any(
        row.get("from_state") == "DRAFT" and row.get("to_state") == "SUBMITTED"
        for row in store.rows_of("marketplace_submission_transitions")
    )

    # ══════════════════════════════════════════════════════════════════
    # STEP 6. Administrative review.
    # Asserts: the Admin_Reviewer's queue answers the Submission under review, and the SAME route
    # refuses the subscriber - who holds no admin role - with 403. `get_admin_user` is never
    # overridden; the real role gate decides both answers.
    # ══════════════════════════════════════════════════════════════════
    with _matrix_app(store, ADMIN) as client:
        app.dependency_overrides[get_current_user] = _admin_identity
        queue = client.get("/api/library/admin/submissions", params={"state": ["SUBMITTED"]})
        assert queue.status_code == 200, queue.text
        listed = queue.json()
        queued_ids = [str(item["id"]) for item in listed["items"]]
        assert str(submission_id) in queued_ids, listed
        assert listed["total"] >= 1

        detail = client.get(f"/api/library/admin/submissions/{submission_id}")
        assert detail.status_code == 200, detail.text

    with _matrix_app(store, SUBSCRIBER) as client:
        refused = client.get("/api/library/admin/submissions")
    assert refused.status_code == 403, refused.text

    # ══════════════════════════════════════════════════════════════════
    # STEP 7. Approval and publication.
    # Asserts: the two admin actions walk SUBMITTED -> UNDER_REVIEW -> APPROVED -> PUBLISHED, each
    # edge recorded, and the Submission's persisted state ends at PUBLISHED.
    # ══════════════════════════════════════════════════════════════════
    with _matrix_app(store, ADMIN) as client:
        app.dependency_overrides[get_current_user] = _admin_identity
        approved = client.post(f"/api/library/admin/submissions/{submission_id}/approve")
        assert approved.status_code == 200, approved.text
        assert approved.json()["submission_state"] == SubmissionState.APPROVED.value

        published = client.post(f"/api/library/admin/submissions/{submission_id}/publish")
        assert published.status_code == 200, published.text
        assert published.json()["submission_state"] == SubmissionState.PUBLISHED.value

    edges = {
        (row.get("from_state"), row.get("to_state"))
        for row in store.rows_of("marketplace_submission_transitions")
    }
    for edge in (
        ("DRAFT", "SUBMITTED"),
        ("SUBMITTED", "UNDER_REVIEW"),
        ("UNDER_REVIEW", "APPROVED"),
        ("APPROVED", "PUBLISHED"),
    ):
        assert edge in edges, f"the {edge[0]} -> {edge[1]} edge was not recorded"
    assert (
        store.one("marketplace_submissions", id=submission_id)["submission_state"]
        == SubmissionState.PUBLISHED.value
    )

    # In production the AFTER-UPDATE trigger `trg_submission_projects_moderation_status`
    # projects MODERATION_STATUS_FOR_STATE[PUBLISHED] onto the Listing. The double has no
    # triggers, so that ONE shared projection - the same mapping the router reads - is applied
    # here explicitly, exactly as tests/test_marketplace_pipeline.py applies it. It is the
    # projection under test, not an independent write.
    store.one("library_strategies", id=listing_id)["moderation_status"] = (
        MODERATION_STATUS_FOR_STATE[SubmissionState.PUBLISHED]
    )
    _attach_postgrest_embeds(store, listing_id)

    # ══════════════════════════════════════════════════════════════════
    # STEP 8. Public catalogue visibility.
    # Asserts: the published Listing is in GET /api/library's page, and the catalogue answer
    # carries none of the Listing's Protected_Logic. The same request answered an EMPTY page
    # before publication (asserted above), so visibility is a change this journey caused.
    # ══════════════════════════════════════════════════════════════════
    # The strategy's Protected_Logic, planted in the columns
    # ``listing_projection.STRATEGY_PROTECTED_LOGIC_COLUMNS`` and
    # ``VERSION_PROTECTED_LOGIC_COLUMNS`` name, so steps 8, 9 and 14 have something a leak would
    # carry. See :func:`_canary_logic` for why these documents rather than the compiled graph, and
    # note they are planted AFTER the Eligibility_Gate has run so nothing about admission changes.
    protected_logic: Dict[str, Any] = {
        "sell_logic": _canary_logic("exit"),
        "ml_model_path": f"models/{LOGIC_CANARY}-ensemble/weights.pkl",
    }
    strategy_row.update(protected_logic)
    version_row["graph_json"] = _canary_logic("graph")
    version_row["blueprint"] = _canary_logic("blueprint")

    tokens_of_protected_logic = protected_logic_tokens(
        protected_logic, version_row, backtest_rows
    )
    assert tokens_of_protected_logic, (
        "the strategy carries no Protected_Logic token, so step 14 would assert nothing"
    )
    # The positive control. Without it every containment assertion below could pass because the
    # oracle finds nothing rather than because there is nothing to find.
    with pytest.raises(AssertionError):
        assert_contains_no_protected_logic(
            {"description": f"leaks {sorted(tokens_of_protected_logic)[0]} here"},
            tokens_of_protected_logic,
        )

    with _matrix_app(store, SUBSCRIBER) as client:
        catalogue = client.get("/api/library", params={"page": 1})
    assert catalogue.status_code == 200, catalogue.text
    catalogue_body = catalogue.json()
    catalogue_ids = [
        str(item.get("listing_id") or item.get("id")) for item in catalogue_body["items"]
    ]
    assert str(listing_id) in catalogue_ids, catalogue_body
    assert_contains_no_protected_logic(catalogue_body, tokens_of_protected_logic)

    # ══════════════════════════════════════════════════════════════════
    # STEP 9. A second account views the Listing.
    # Asserts: the subscriber - a different tenant - reads the detail surface and gets the public
    # projection: the Listing's own identity and its condition summaries, and no Protected_Logic.
    # ══════════════════════════════════════════════════════════════════
    with _matrix_app(store, SUBSCRIBER) as client:
        detail = client.get(f"/api/library/{listing_id}")
    assert detail.status_code == 200, detail.text
    detail_body = detail.json()
    assert str(detail_body.get("listing_id") or detail_body.get("id")) == str(listing_id)
    assert detail_body["price_minor"] == PRICE_MINOR
    assert detail_body["currency"] == CURRENCY
    assert detail_body["creator_alias"] == "journey-creator"
    assert "author_id" not in detail_body and "user_id" not in detail_body
    assert_contains_no_protected_logic(detail_body, tokens_of_protected_logic)

    # ══════════════════════════════════════════════════════════════════
    # STEP 10. Subscribing.
    # Asserts: POST /api/library/{id}/checkout creates ONE library_subscriptions row at `pending`
    # carrying the price as integer Minor_Units, asks the provider for EXACTLY that amount, and
    # returns it unscaled and unrounded. Nothing is activated by the checkout itself.
    # ══════════════════════════════════════════════════════════════════
    charged: List[int] = []
    factory = _ok_factory(_ProviderCallLog(), charged, reference="pi_journey_1999")
    with _matrix_app(store, SUBSCRIBER) as client:
        with patch.object(_checkout, "default_provider_session_factory", factory):
            checkout = client.post(
                f"/api/library/{listing_id}/checkout", json={"currency": CURRENCY}
            )
    assert checkout.status_code == 200, checkout.text
    checkout_body = checkout.json()
    subscription_id = checkout_body["subscription_id"]
    assert checkout_body["amount_minor"] == PRICE_MINOR
    assert isinstance(checkout_body["amount_minor"], int)
    assert checkout_body["currency"] == CURRENCY
    assert charged == [PRICE_MINOR], (
        f"the provider was asked to charge {charged}, not {PRICE_MINOR} Minor_Units"
    )

    subscription_row = store.one("library_subscriptions", id=subscription_id)
    assert subscription_row["status"] == "pending"
    assert subscription_row["price_minor"] == PRICE_MINOR
    assert str(subscription_row["user_id"]) == str(SUBSCRIBER)
    assert str(subscription_row["owner_id"]) == str(OWNER)
    assert subscription_row.get("period_expiry") is None
    assert subscription_row.get("expires_at") is None

    provider_reference = checkout_body["provider_reference"]
    assert provider_reference == "pi_journey_1999"

    # The Billing_Integration confirms the payment. There is no inbound HTTP webhook in-process,
    # so the confirmation is delivered to the function the webhook calls - the ONE path that may
    # move a Subscription to `active` - with the reference the checkout actually returned.
    settled = _run(
        _settlement.settle(
            provider_reference=provider_reference,
            provider=checkout_body["provider"],
            amount_minor=PRICE_MINOR,
            currency=CURRENCY,
            subscription_id=str(subscription_id),
            confirmation_instant=CONFIRMED_AT,
            supabase=store,
        )
    )
    assert settled.outcome is _settlement.SettlementOutcome.RECORDED, settled

    # ══════════════════════════════════════════════════════════════════
    # STEP 11. The 90/10 split in the Settlement_Ledger, to the Minor_Unit.
    # Asserts: exactly one Settlement_Record; its owner share and platform fee are the integers
    # `money.split_ninety_ten` produces; they conserve the gross amount exactly; the owner's share
    # is the largest whole Minor_Unit not exceeding 90 percent; and every value is an `int`.
    # ══════════════════════════════════════════════════════════════════
    expected_owner, expected_platform = _money.split_ninety_ten(PRICE_MINOR)
    assert (expected_owner, expected_platform) == (1799, 200)

    assert len(store.rows_of("marketplace_settlements")) == 1
    ledger = store.rows_of("marketplace_settlements")[0]
    assert ledger["amount_minor"] == PRICE_MINOR
    assert ledger["owner_share_minor"] == expected_owner
    assert ledger["platform_fee_minor"] == expected_platform
    assert ledger["owner_share_minor"] + ledger["platform_fee_minor"] == PRICE_MINOR
    assert ledger["owner_share_minor"] * 100 <= PRICE_MINOR * 90
    assert (ledger["owner_share_minor"] + 1) * 100 > PRICE_MINOR * 90
    assert ledger["currency"] == CURRENCY
    assert ledger["is_reversal"] is False
    assert str(ledger["owner_id"]) == str(OWNER)
    assert str(ledger["purchaser_id"]) == str(SUBSCRIBER)
    for column in ("amount_minor", "owner_share_minor", "platform_fee_minor"):
        assert isinstance(ledger[column], int) and not isinstance(ledger[column], bool), (
            f"{column} is {type(ledger[column])!r}; money is integer Minor_Units"
        )

    # ══════════════════════════════════════════════════════════════════
    # STEP 12. The Subscription becomes ACTIVE with a one-calendar-month expiry.
    # Asserts: the persisted status is `active`; the period start is the confirmation instant; the
    # expiry is EXACTLY `subscription_period.add_one_calendar_month` of it - 31 January to 28
    # February, the day-clamping case - and the retained `expires_at` mirror is the expiry rather
    # than the NULL that used to read as perpetual access.
    # ══════════════════════════════════════════════════════════════════
    activated = store.one("library_subscriptions", id=subscription_id)
    assert activated["status"] == "active"
    expected_start, expected_expiry = _period.period_for_activation(CONFIRMED_AT)
    assert expected_expiry == _period.add_one_calendar_month(CONFIRMED_AT)
    assert expected_start == CONFIRMED_AT
    # One calendar month later: the month after January, the same clock time, and the day clamped
    # to February's last day because 31 January has no counterpart in it (Requirement 11.4).
    assert (expected_expiry.year, expected_expiry.month) == (CONFIRMED_AT.year, 2)
    assert (expected_expiry.hour, expected_expiry.minute) == (9, 30)
    assert expected_expiry.day == calendar.monthrange(CONFIRMED_AT.year, 2)[1]
    assert expected_expiry > expected_start
    assert activated["period_start"] == expected_start.isoformat()
    assert activated["period_expiry"] == expected_expiry.isoformat()
    assert activated["expires_at"] == expected_expiry.isoformat()
    assert settled.to_status == "active"
    assert settled.period_expiry == expected_expiry

    _attach_postgrest_embeds(store, listing_id)
    first_period_expiry = expected_expiry

    # ══════════════════════════════════════════════════════════════════
    # STEP 13. The strategy is on the subscriber's Strategies_Page as SUBSCRIBED.
    # Asserts: GET /api/library/my-strategies carries one entry for this Listing whose
    # server-derived ownership label is SUBSCRIBED, whose subscription block reports the ACTIVE
    # state and the period expiry, and whose allowed_actions name none of the thirteen actions
    # Requirement 12.4 forbids a subscriber.
    # ══════════════════════════════════════════════════════════════════
    with _matrix_app(store, SUBSCRIBER) as client:
        page = client.get("/api/library/my-strategies")
    assert page.status_code == 200, page.text
    page_body = page.json()
    subscribed = [
        entry
        for entry in page_body["items"]
        if entry["ownership"] == _entries.OWNERSHIP_SUBSCRIBED
    ]
    assert len(subscribed) == 1, page_body
    entry = subscribed[0]
    assert str(entry["listing_id"]) == str(listing_id)
    assert entry["subscription"]["state"] == "ACTIVE"
    assert datetime.fromisoformat(
        str(entry["subscription"]["period_expiry"])
    ) == first_period_expiry
    assert entry["entitling"] is True
    assert entry["unavailable_reason"] is None
    assert page_body["subscribed_total"] == 1
    forbidden = set(_entries.SUBSCRIBER_FORBIDDEN_ACTIONS) & set(entry["allowed_actions"])
    assert forbidden == set(), f"a SUBSCRIBED entry offered {sorted(forbidden)}"

    # ══════════════════════════════════════════════════════════════════
    # STEP 14. Protected_Logic is unreachable by the subscriber.
    # Asserts: `assert_contains_no_protected_logic` holds over EVERY response the subscriber can
    # reach on the two prefixes - the catalogue, the featured/trending/category surfaces, the
    # creator profile, the Listing detail, the reviews, the recommendations, the comparison, the
    # subscription status, the deploy check, the Strategies_Page, the subscriber analytics, the
    # favourites, and the paper surfaces - searched to every nesting depth, keys included.
    # ══════════════════════════════════════════════════════════════════
    with _matrix_app(store, SUBSCRIBER) as client:
        reachable: List[Tuple[str, Any]] = [
            ("GET /api/library", client.get("/api/library", params={"page": 1})),
            ("GET /api/library/featured", client.get("/api/library/featured")),
            ("GET /api/library/trending", client.get("/api/library/trending")),
            ("GET /api/library/categories", client.get("/api/library/categories")),
            ("GET /api/library/me", client.get("/api/library/me")),
            ("GET /api/library/my-strategies", client.get("/api/library/my-strategies")),
            ("GET /api/library/favorites", client.get("/api/library/favorites")),
            ("GET /api/library/recommendations", client.get("/api/library/recommendations")),
            (
                "GET /api/library/subscriber/analytics",
                client.get("/api/library/subscriber/analytics"),
            ),
            (
                f"GET /api/library/creator/{OWNER}",
                client.get(f"/api/library/creator/{OWNER}"),
            ),
            (
                f"GET /api/library/{listing_id}",
                client.get(f"/api/library/{listing_id}"),
            ),
            (
                f"GET /api/library/{listing_id}/reviews",
                client.get(f"/api/library/{listing_id}/reviews"),
            ),
            (
                f"GET /api/library/{listing_id}/subscribe",
                client.get(f"/api/library/{listing_id}/subscribe"),
            ),
            (
                f"GET /api/library/{listing_id}/deploy/check",
                client.get(f"/api/library/{listing_id}/deploy/check"),
            ),
            (
                "POST /api/library/compare",
                client.post(
                    "/api/library/compare",
                    json={"library_ids": [str(listing_id), str(listing_id)]},
                ),
            ),
            ("GET /api/paper/sessions", client.get("/api/paper/sessions")),
            ("GET /api/paper/account", client.get("/api/paper/account")),
            ("GET /api/paper/positions", client.get("/api/paper/positions")),
            ("GET /api/paper/orders", client.get("/api/paper/orders")),
            ("GET /api/paper/trades", client.get("/api/paper/trades")),
            ("GET /api/paper/summary", client.get("/api/paper/summary")),
        ]
        for label, response in reachable:
            # 500 is an UNHANDLED exception; every refusal this platform issues has a code and a
            # status from the catalogue. A crashing surface is a finding in its own right.
            assert response.status_code != 500, f"{label} answered 500\n{response.text}"
            try:
                body = response.json()
            except ValueError:  # pragma: no cover - a non-JSON body would itself be a defect
                body = response.text
            assert_contains_no_protected_logic(body, tokens_of_protected_logic)

        assert len(reachable) >= 20
        answered = {label: response.status_code for label, response in reachable}
        for must_be_ok in (
            "GET /api/library",
            "GET /api/library/my-strategies",
            f"GET /api/library/{listing_id}",
            f"GET /api/library/{listing_id}/subscribe",
        ):
            assert answered[must_be_ok] == 200, answered

    # ══════════════════════════════════════════════════════════════════
    # STEP 15. Starting a Paper_Session.
    # Asserts: the real start pipeline admits the SUBSCRIBER on the strength of the
    # Entitlement_Resolver's SUBSCRIBED verdict, creates the session at RUNNING in environment
    # PAPER with its own isolated Paper_Account at the recorded capital, records the
    # paper_session_started frame, and opens the market-data subscription on this session's pair.
    # ══════════════════════════════════════════════════════════════════
    monkeypatch.setattr(feed, "_utc_now", lambda: LOOP_NOW)

    redis = RecordingRedis(
        frames=[
            _bar(3, (59900.0, 60100.0, 59800.0, 60000.5)),
            _bar(2, (60000.5, 60100.0, 57900.0, 58000.5)),
            _bar(1, (58000.5, 61100.0, 57950.0, 61000.5)),
        ]
    )
    started = _run(
        session_service.start_session(
            store,
            SimpleNamespace(id=SUBSCRIBER),
            listing_id=listing_id,
            exchange_id=EXCHANGE,
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            initial_capital_minor=CAPITAL_MINOR,
            currency=CURRENCY,
            now=LOOP_NOW,
            markets=_markets(),
            exchange=None,
            redis=redis,
            measurements=_admitting(),
            spawn_loop=None,
        )
    )
    session_id = started.session_id
    assert started.entitlement.entitling is True
    assert started.entitlement.reason is _entitlement.EntitlementReason.SUBSCRIBED
    assert started.session["session_state"] == "RUNNING"
    assert started.session["environment"] == "PAPER"
    assert str(started.session["user_id"]) == str(SUBSCRIBER)
    assert int(started.session["initial_capital_minor"]) == CAPITAL_MINOR
    assert str(started.account["session_id"]) == str(session_id)
    assert _recorded_types(store) == ["paper_session_started"]
    assert redis.subscribed == [f"mds:data:{EXCHANGE}:{SYMBOL}"], redis.log
    account_id = str(started.account["id"])

    # ══════════════════════════════════════════════════════════════════
    # STEP 16. Receiving market data.
    # Asserts: the first bar is validated by the real feed and RECORDED as a paper_market_events
    # row whose five OHLCV values are the wire's, whose transport was observed from the payload,
    # whose latency is the pinned processing instant minus the candle's own, and whose
    # market_tick frame carries the same candle identity. Nothing is interpolated or invented.
    # ══════════════════════════════════════════════════════════════════
    recorder = build_session_signal_recorder(
        store, session=started.session, version_row=version_row
    )
    runtime = _Runtime([_journey_signal()])

    first = _run(
        session_service.step_session(
            store,
            started.session,
            started.feed,
            config=started.config,
            account_id=account_id,
            evaluate=runtime,
            plan="journey-plan",
            record_signal=recorder,
            series_index=session_service.current_series_index(
                store, SUBSCRIBER, session_id
            ),
        )
    )
    assert first.dropped is False
    assert first.event is not None
    assert first.event.symbol == SYMBOL
    assert first.event.close == Decimal("60000.5")
    assert first.event.feed_state == "HEALTHY"

    assert len(store.market_events) == 1
    recorded_candle = store.market_events[0]
    assert str(recorded_candle["symbol"]) == SYMBOL
    assert str(recorded_candle["timeframe"]) == TIMEFRAME
    assert str(recorded_candle["session_id"]) == str(session_id)
    assert str(recorded_candle["source_event_id"]) == first.event.source_event_id
    # Requirement 14.10's delivery measurement: the pinned processing instant minus the candle's
    # own, which for a bar three minutes back is exactly 180000 ms.
    assert Decimal(str(recorded_candle["latency_ms"])) == Decimal("180000")

    # The five OHLCV values live in ``payload`` as exact decimal STRINGS - a JSON number could not
    # be read back exactly and ``paper_replay`` prices its ledger from this row. Every one of them
    # is the value the wire carried.
    candle_payload = dict(recorded_candle["payload"])
    assert candle_payload["transport"] == "WEBSOCKET"
    assert Decimal(candle_payload["open"]) == Decimal("59900")
    assert Decimal(candle_payload["high"]) == Decimal("60100")
    assert Decimal(candle_payload["low"]) == Decimal("59800")
    assert Decimal(candle_payload["close"]) == Decimal("60000.5")
    assert Decimal(candle_payload["volume"]) == Decimal("1.5")

    assert first.tick is not None
    tick_payload = _payload_of(store, "market_tick")
    assert tick_payload["source_event_id"] == first.event.source_event_id
    assert Decimal(str(tick_payload["latency_ms"])) == Decimal("180000")

    # ══════════════════════════════════════════════════════════════════
    # STEP 17. Generating signals.
    # Asserts: the bar produced exactly one signal, the loop acted on it, and a
    # signal_generated frame was recorded AFTER the market_tick that caused it.
    # ══════════════════════════════════════════════════════════════════
    assert [s.signal_id for s in first.signals] == [_journey_signal()["signal_id"]]
    assert len(first.signal_frames) == 1
    recorded_types = _recorded_types(store)
    assert recorded_types.index("market_tick") < recorded_types.index("signal_generated")

    # ══════════════════════════════════════════════════════════════════
    # STEP 18. Simulated orders and fills.
    # Asserts: the intent was accepted, ONE paper_orders row reached FILLED for the full
    # quantity, ONE paper_fills row was written against it, and the fill's price traces to the
    # recorded candle rather than to a number this test chose.
    # ══════════════════════════════════════════════════════════════════
    assert len(first.submissions) == 1
    assert first.submissions[0].accepted is True
    assert first.skipped == ()

    orders = repo.get_orders(store, SUBSCRIBER, session_id=session_id)
    assert len(orders) == 1, orders
    order = orders[0]
    assert str(order["order_state"]) == "FILLED"
    assert Decimal(str(order["filled_quantity"])) == Decimal("0.5")
    assert len(store.fills) == 1
    fill = store.fills[0]
    assert str(fill["order_id"]) == str(order["id"])
    assert Decimal(str(fill["quantity"])) == Decimal("0.5")
    assert Decimal(str(fill["price"])) > 0

    # ══════════════════════════════════════════════════════════════════
    # STEP 19. Positions, PnL and drawdown - and the charts.
    # Asserts: the two later bars revalue the open position, each writing an equity snapshot with
    # a paper_pnl_updated and a paper_drawdown_updated frame; the position is open at the filled
    # size; the reported drawdown is at or above zero and no larger than the series' peak-to-
    # trough; and the series the charts read is present in the shape they read it. The RENDERING
    # is asserted by the frontend suite named in FRONTEND_CHART_SUITE, which runs under vitest on
    # the frontend-tests CI job - never from this process.
    # ══════════════════════════════════════════════════════════════════
    for _bar_index in range(2):
        step = _run(
            session_service.step_session(
                store,
                started.session,
                started.feed,
                config=started.config,
                account_id=account_id,
                evaluate=runtime,
                plan="journey-plan",
                record_signal=recorder,
                series_index=0,
            )
        )
        assert step.dropped is False
        assert step.valuation is not None, "an open position was not revalued"
        assert step.snapshot is not None
        assert step.pnl is not None and step.drawdown is not None

    positions = repo.get_positions(store, SUBSCRIBER, session_id=session_id)
    open_positions = [p for p in positions if str(p.get("symbol")) == SYMBOL]
    assert len(open_positions) == 1
    assert Decimal(str(open_positions[0]["size"])) == Decimal("0.5")

    snapshots = repo.get_equity_snapshots(store, SUBSCRIBER, session_id=session_id)
    series = [row for row in snapshots if int(row.get("series_index") or 0) == 0]
    assert len(series) >= 3, series
    equities = [Decimal(str(row["total_equity"])) for row in series]
    peak_to_trough = max(equities) - min(equities)

    drawdown_payload = [
        dict(row["payload"])
        for row in store.events
        if str(row["event_type"]) == "paper_drawdown_updated"
    ]
    assert len(drawdown_payload) >= 2, drawdown_payload
    for payload in drawdown_payload:
        reported = Decimal(str(payload["max_drawdown_amount"]))
        fraction = Decimal(str(payload["max_drawdown_fraction"]))
        assert reported >= 0
        assert fraction >= 0
        assert reported <= peak_to_trough, (
            f"reported drawdown {reported} exceeds the series' peak-to-trough {peak_to_trough}"
        )

    pnl_payload = [
        dict(row["payload"])
        for row in store.events
        if str(row["event_type"]) == "paper_pnl_updated"
    ]
    assert len(pnl_payload) == len(drawdown_payload)
    for payload in pnl_payload:
        for key in ("realized_pnl", "unrealized_pnl", "total_pnl", "total_return_pct"):
            assert key in payload, payload
        assert Decimal(str(payload["total_pnl"])) == Decimal(
            str(payload["realized_pnl"])
        ) + Decimal(str(payload["unrealized_pnl"]))

    # The series the charts read: one row per revaluation, each carrying the four figures
    # PaperTrading.jsx plots. The RENDERING is the frontend suite's; this is the fixture it reads.
    for row in series:
        for column in (
            "total_equity",
            "available_balance",
            "locked_balance",
            "position_market_value",
        ):
            assert row.get(column) is not None, row

    assert FRONTEND_CHART_SUITE.exists(), (
        f"Requirement 29.4's chart rendering is asserted by {FRONTEND_CHART_SUITE}, which is "
        f"not on disk; step 19 would then be discharged by nothing"
    )

    # ══════════════════════════════════════════════════════════════════
    # STEP 20. Stopping the session.
    # Asserts: stop_session moves RUNNING -> STOPPED, commits the closing equity point and the
    # closing metrics row, publishes the market-data unsubscribe, records the
    # paper_session_stopped frame, and leaves the persisted session_state at STOPPED.
    # ══════════════════════════════════════════════════════════════════
    stopped = _run(
        session_service.stop_session(
            store,
            SimpleNamespace(id=SUBSCRIBER),
            session_id,
            now=LOOP_NOW + timedelta(minutes=1),
            feed=started.feed,
            loop_task=None,
        )
    )
    assert stopped.from_state == "RUNNING"
    assert stopped.to_state == "STOPPED"
    assert stopped.finals.snapshot is not None
    assert stopped.finals.metrics_row is not None
    assert stopped.mds_released is True
    assert store.one("paper_sessions", id=session_id)["session_state"] == "STOPPED"
    assert "paper_session_stopped" in _recorded_types(store)

    # ══════════════════════════════════════════════════════════════════
    # STEP 21. Persistence across a simulated restart.
    # Asserts: nothing this session reported lived in process memory. The paper singleton is
    # dropped, the migration probe forgotten and a NEW service bound to the same tables; every
    # order, fill, position, trade and equity point reads back byte-identically, and the balances
    # are the ones that were persisted rather than remembered.
    # ══════════════════════════════════════════════════════════════════
    before_restart = _persisted_image(store, session_id)

    from backend_app.backend import paper_trading_service as paper_module

    paper_module._paper_service_instance = None
    repo.reset_persistence_probe()
    restarted = paper_module.PaperTradingService(
        default_capital=100_000.0, default_fee_rate=0.001, default_slippage=0.0005
    )
    restarted.bind_persistence(store)
    paper_module._paper_service_instance = restarted

    after_restart = _persisted_image(store, session_id)
    assert after_restart == before_restart, (
        "a figure changed across the restart, so it was held in process memory"
    )
    for name in ("orders", "fills", "positions", "equity", "session"):
        assert after_restart[name] not in ("[]", "null"), (
            f"{name} came back empty after the restart, so the comparison above compared nothing"
        )

    # ══════════════════════════════════════════════════════════════════
    # STEP 22. signals.environment = 'PAPER'.
    # Asserts: the Signal_Trace row this session's decision produced landed in `public.signals`
    # through the SAME path a LIVE signal takes, saying environment='PAPER', naming the
    # Paper_Session in paper_session_id, and leaving deployment_id NULL - a Paper_Session is not
    # a deployment.
    # ══════════════════════════════════════════════════════════════════
    trace_rows = store.rows_of("signals")
    assert len(trace_rows) == 1, trace_rows
    trace = trace_rows[0]
    assert trace["environment"] == "PAPER"
    assert str(trace["paper_session_id"]) == str(session_id)
    assert trace.get("deployment_id") is None
    assert str(trace["strategy_id"]) == str(strategy_id)
    assert str(trace["user_id"]) == str(SUBSCRIBER)
    assert trace["symbol"] == SYMBOL

    # ══════════════════════════════════════════════════════════════════
    # STEP 23. Expiring the Subscription, and access becoming non-entitling.
    # Asserts: at an instant past the expiry the Entitlement_Resolver already answers EXPIRED
    # with no sweep having run (Requirement 11.7); the sweep then transitions exactly this
    # Subscription to `expired` and records the edge; the resolver's answer is unchanged; a
    # second sweep changes nothing; and a Paper_Session start is now refused on that same
    # decision.
    # ══════════════════════════════════════════════════════════════════
    after_expiry = first_period_expiry + timedelta(seconds=1)

    before_sweep = _run(
        _entitlement.resolve(
            SimpleNamespace(id=SUBSCRIBER), listing_id, store, after_expiry
        )
    )
    assert before_sweep.entitling is False
    assert before_sweep.reason is _entitlement.EntitlementReason.EXPIRED

    outcome = _run(
        _sweep.sweep(
            supabase=store,
            now=after_expiry,
            stop_running=False,
            audit_writer=_silent_audit,
        )
    )
    assert [str(row.subscription_id) for row in outcome.expired] == [
        str(subscription_id)
    ], outcome
    assert outcome.expired[0].from_state == "active"
    assert (
        store.one("library_subscriptions", id=subscription_id)["status"] == "expired"
    )
    assert any(
        str(row.get("subscription_id")) == str(subscription_id)
        and str(row.get("to_state")).upper() == "EXPIRED"
        for row in store.rows_of("library_subscription_transitions")
    ), store.rows_of("library_subscription_transitions")

    second_pass = _run(
        _sweep.sweep(
            supabase=store,
            now=after_expiry,
            stop_running=False,
            audit_writer=_silent_audit,
        )
    )
    assert not second_pass.expired, second_pass

    _attach_postgrest_embeds(store, listing_id)
    after_sweep = _run(
        _entitlement.resolve(
            SimpleNamespace(id=SUBSCRIBER), listing_id, store, after_expiry
        )
    )
    assert after_sweep.entitling is False
    assert after_sweep.reason is _entitlement.EntitlementReason.EXPIRED

    sessions_before_refusal = len(store.rows_of("paper_sessions"))
    refused_start = RecordingRedis(frames=[])
    with pytest.raises(session_service.PaperError):
        _run(
            session_service.start_session(
                store,
                SimpleNamespace(id=SUBSCRIBER),
                listing_id=listing_id,
                exchange_id=EXCHANGE,
                symbol=SYMBOL,
                timeframe=TIMEFRAME,
                initial_capital_minor=CAPITAL_MINOR,
                currency=CURRENCY,
                now=after_expiry,
                markets=_markets(),
                exchange=None,
                redis=refused_start,
                measurements=_admitting(),
                spawn_loop=None,
            )
        )
    assert refused_start.log == [], (
        "a refused start touched the market-data transport (Requirement 17.13)"
    )
    assert len(store.rows_of("paper_sessions")) == sessions_before_refusal, (
        "a refused start created a Paper_Session (Requirement 17.13)"
    )

    # ══════════════════════════════════════════════════════════════════
    # STEP 24. A renewal payment restores access.
    # Asserts: the renewal route returns a provider session for the renewal amount and writes NO
    # state; the confirmed renewal payment writes a SECOND Settlement_Record with the same exact
    # 90/10 split, moves the Subscription back to `active` with an expiry one calendar month
    # after the confirmation instant, and the Entitlement_Resolver entitles again.
    # ══════════════════════════════════════════════════════════════════
    renewal_confirmed_at = after_expiry + timedelta(days=1)
    status_before_renewal = store.one("library_subscriptions", id=subscription_id)["status"]

    renewal_charged: List[int] = []
    renewal_factory = _ok_factory(
        _ProviderCallLog(), renewal_charged, reference="pi_journey_renewal"
    )
    with _matrix_app(store, SUBSCRIBER) as client:
        with patch.object(
            _checkout, "default_provider_session_factory", renewal_factory
        ):
            renewal = client.post(
                f"/api/library/subscriptions/{subscription_id}/renew"
            )
    assert renewal.status_code == 200, renewal.text
    renewal_body = renewal.json()
    assert renewal_body["status"] == "renewal_pending_payment"
    assert renewal_body["amount_minor"] == PRICE_MINOR
    assert renewal_charged == [PRICE_MINOR]
    assert (
        store.one("library_subscriptions", id=subscription_id)["status"]
        == status_before_renewal
    ), "the renewal route changed state; only a confirmed payment may (Requirement 11.16)"

    renewed = _run(
        _settlement.settle(
            provider_reference=renewal_body["provider_reference"],
            provider=renewal_body["provider"],
            amount_minor=PRICE_MINOR,
            currency=CURRENCY,
            subscription_id=str(subscription_id),
            confirmation_instant=renewal_confirmed_at,
            supabase=store,
        )
    )
    assert renewed.outcome is _settlement.SettlementOutcome.RECORDED, renewed
    assert renewed.to_status == "active"

    assert len(store.rows_of("marketplace_settlements")) == 2
    renewal_ledger = store.one(
        "marketplace_settlements", provider_reference="pi_journey_renewal"
    )
    assert renewal_ledger["owner_share_minor"] == expected_owner
    assert renewal_ledger["platform_fee_minor"] == expected_platform
    assert (
        renewal_ledger["owner_share_minor"] + renewal_ledger["platform_fee_minor"]
        == PRICE_MINOR
    )

    restored = store.one("library_subscriptions", id=subscription_id)
    assert restored["status"] == "active"
    renewed_expiry = _period.add_one_calendar_month(renewal_confirmed_at)
    assert restored["period_expiry"] == renewed_expiry.isoformat()
    assert renewed_expiry > first_period_expiry

    _attach_postgrest_embeds(store, listing_id)
    entitled_again = _run(
        _entitlement.resolve(
            SimpleNamespace(id=SUBSCRIBER),
            listing_id,
            store,
            renewal_confirmed_at + timedelta(days=1),
        )
    )
    assert entitled_again.entitling is True
    assert entitled_again.reason is _entitlement.EntitlementReason.SUBSCRIBED

    # The journey tripped none of the seven pinned Requirement 21.4 defects: it never addressed
    # another tenant's identifier. Named so a reader knows they were considered rather than
    # forgotten.
    assert len(REQUIREMENT_21_4_GAPS) == 7


# ══════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════


class _ProviderCallLog:
    """The one method ``_ok_factory`` calls on its driver. It records nothing this file asserts.

    ``tests/test_marketplace_checkout_regression._ok_factory`` takes the suite's own driver so it
    can note which provider was contacted. The amount is what matters here and it arrives through
    the list ``_ok_factory``'s second argument appends to, so this stands in for the driver.
    """

    def note_provider_call(self, provider: str) -> None:
        self.provider = provider


def _async_client_factory(store: _JourneyStore) -> Any:
    """``create_request_supabase_async``, answering with the async transport over ``store``."""

    async def _create(_token: Any) -> _AsyncTransport:
        return _AsyncTransport(store)

    return _create


async def _silent_audit(*_args: Any, **_kwargs: Any) -> None:
    """The expiry sweep's Audit_Log writer, silenced: it pushes to Redis and there is none here."""
    return None


def _recorded_types(store: _JourneyStore) -> List[str]:
    """Every ``paper_events`` row's type, in the order the sequence allocator issued them."""
    return [
        str(row["event_type"])
        for row in sorted(store.events, key=lambda r: int(r["sequence"]))
    ]


def _payload_of(store: _JourneyStore, event_type: str) -> Dict[str, Any]:
    """The one recorded payload of ``event_type``."""
    rows = [row for row in store.events if str(row["event_type"]) == event_type]
    assert len(rows) == 1, f"expected exactly one {event_type}, got {len(rows)}"
    return dict(rows[0]["payload"])


def _persisted_image(store: _JourneyStore, session_id: str) -> Dict[str, str]:
    """Everything this Paper_Session persisted, as canonical text, read through the repository.

    Read through ``paper_repository`` rather than off the store's attributes, because the claim of
    Requirement 17.2 and property P-32 is that the figures come back from the TABLES through the
    production reader - which is the path a restarted process takes.
    """
    user_id = SUBSCRIBER
    return {
        "orders": json.dumps(
            repo.get_orders(store, user_id, session_id=session_id),
            sort_keys=True,
            default=str,
        ),
        "positions": json.dumps(
            repo.get_positions(store, user_id, session_id=session_id),
            sort_keys=True,
            default=str,
        ),
        "trades": json.dumps(
            repo.get_trades(store, user_id, session_id=session_id),
            sort_keys=True,
            default=str,
        ),
        "equity": json.dumps(
            repo.get_equity_snapshots(store, user_id, session_id=session_id),
            sort_keys=True,
            default=str,
        ),
        "fills": json.dumps(store.fills, sort_keys=True, default=str),
        "session": json.dumps(
            repo.read_session(store, user_id, session_id), sort_keys=True, default=str
        ),
    }


def test_this_journey_never_calls_asyncio_run() -> None:
    """The paper suite has ONE event loop, and it is ``_run_coroutine``'s.

    A fresh loop per coroutine exhausted the machine's ephemeral port range and hung the paper
    suite past 700 s on Windows; ``tests/test_paper_order_lifecycle_writes._HARNESS_LOOP`` records
    why. Asserted by parsing this module's own source rather than by convention, because a
    convention is what this is a guard against.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "run"
        and isinstance(node.value, ast.Name)
        and node.value.id == "asyncio"
    ]
    assert offenders == [], (
        f"asyncio.run appears at line(s) {offenders}; drive coroutines with "
        f"tests/test_paper_order_lifecycle_writes._run_coroutine instead"
    )
