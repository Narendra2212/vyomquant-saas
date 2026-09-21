"""
tests/sandbox_lifecycle/test_paper_session_lifecycle.py - the Paper_Session lifecycle, in the
sandbox world.

Spec: marketplace-subscriptions-paper-trading task 34.4. Requirements 17.7, 17.8, 17.13, 17.14,
17.15, 21.4, 25.8, 29.9.

WHAT THIS FILE ADDS TO THIS PACKAGE
-----------------------------------
``tests/sandbox_lifecycle/`` held two chains: the deterministic save -> backtest -> deploy ->
signal chain (task 21.1) and the cross-tenant ownership matrix (task 22.1). Requirement 25.8
names this package as one of the suites that must pass after this specification's change, and
Requirement 29.9 forbids buying that by skipping anything. What was MISSING was the lifecycle this
specification actually introduces: a Paper_Session's

    create -> start -> running -> stop -> persistence across a restart

That chain is what this module drives, and it drives it through the REAL
``backend/paper/paper_session_service.py`` pipeline - the same ``start_session``,
``pause_session``, ``resume_session`` and ``stop_session`` the routes call - rather than through
a re-implementation of the state machine.

THE SANDBOX IS THIS PACKAGE'S, NOT A NEW ONE
--------------------------------------------
Every world fact comes from ``tests/sandbox_lifecycle/harness.py``: :data:`SANDBOX_USER` is the
owner, :data:`SANDBOX_SYMBOL` / :data:`SANDBOX_TIMEFRAME` / :data:`SANDBOX_VENUE` are the market
(SB-06 - one market fact per world), :class:`SeededSyntheticFeed` is the price series, and the
``sandbox`` fixture is what flips the platform's own paper-trading switch and restores every flag
afterwards. The capital and the currency are :data:`SANDBOX_PAPER_CAPITAL_MINOR` and
:data:`SANDBOX_PAPER_CURRENCY`, which the same harness declares for the cross-tenant suite's
seeded rows - so the two files cannot disagree about what a sandbox Paper_Session is worth.

WHICH PERSISTENCE_LAYER, AND WHY IT IS NOT ``SandboxDatabase``
--------------------------------------------------------------
``tests/paper_seed.py`` records the rule: this repository has exactly ONE Persistence_Layer double,
because a second would be a second set of assumptions about the database. For the ``paper_*``
tables that double is ``tests/test_paper_repository.FakeSupabase``, which enforces
``009_paper_trading.sql``'s unique indexes, its column defaults and its two UPDATE triggers -
without which ``paper_events``' sequence allocation, ``uq_paper_account_session`` and the
optimistic-version bump on ``paper_accounts`` are not being exercised at all.

``SandboxDatabase`` is this package's store for the ``001``/``003``/``005b`` surface. It answers
``execute()`` as a coroutine (``routers/strategies.py`` awaits it) whereas ``paper_repository``
calls it synchronously, and it implements none of 009's indexes, defaults or triggers. Teaching it
those would have been precisely the second paper double the rule forbids, so the paper rows live in
the one double and everything else - identity, market, capital, clock, feed and mode - is the
sandbox's. That split is stated here rather than left to be inferred.

WHAT IS REAL, AND WHAT IS SUBSTITUTED
-------------------------------------
Real: ``paper_session_service.start_session`` (the whole Requirement 17.9 pipeline, including the
one admission decision, the version lifecycle check, the capital validation, the market metadata
resolve, the timeframe check, the strategy/market compatibility check and the per-user concurrency
cap), ``pause_session``, ``resume_session``, ``stop_session``, ``paper_market_feed.open_feed``,
``paper_repository``'s readers and writers, and the state machine's own tables.

Substituted, each for a stated reason and each with the double this repository already has:
``FakeSupabase`` for the Persistence_Layer; ``tests/test_paper_market_feed_selection``'s
``RecordingRedis`` for the market-data transport (``open_feed`` publishes to ``mds:commands`` and
calls ``pubsub()``, and two accounts of what it publishes would be two accounts); its
``_admitting()`` measurements, so the source selection is a recorded ``SELECTED`` rather than an
accident; and ``markets=`` injected rather than loaded, because loading it reaches a venue this
host cannot.

Coroutines are driven on the process's ONE event loop through
``tests/test_paper_order_lifecycle_writes._run_coroutine``. ``asyncio.run`` appears nowhere - a
fresh loop per call exhausts the Windows loopback port range and has hung the paper suite past
700 seconds.
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

import pytest

from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper import paper_session_service as service
from backend_app.backend.paper.errors import PaperError

from tests.sandbox_lifecycle.harness import (
    SANDBOX_PAPER_CAPITAL_MINOR,
    SANDBOX_PAPER_CURRENCY,
    SANDBOX_SYMBOL,
    SANDBOX_TIMEFRAME,
    SANDBOX_USER,
    SANDBOX_VENUE,
    SandboxWorld,
)
from tests.test_paper_market_feed_selection import RecordingRedis, _admitting
from tests.test_paper_order_lifecycle_writes import _run_coroutine as _run
from tests.test_paper_repository import FakeSupabase

# ── The listing, version and market fixtures of task 27's suite, imported rather than rebuilt. ──
# ``_listing_row`` and ``_version_row`` are the shapes ``entitlement_resolver`` and
# ``read_strategy_version`` actually read, including the two embedded resources PostgREST returns
# on a Listing. Rebuilding them here would be a second account of what a Listing looks like to the
# start pipeline.
from tests.test_task_27_session_service import (
    LISTING,
    STRATEGY,
    VERSION,
    _compiled_plan,
    _listing_row,
    _markets,
    _version_row,
)

#: The instant the whole chain is stamped with. Fixed, so every persisted timestamp is
#: reproducible and the ordering assertions below are about the chain rather than about the clock.
#: The sandbox's own first bar would do as well; this is a UTC instant inside the sandbox's window.
CHAIN_START = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _forget_the_paper_migration_verdict() -> Iterator[None]:
    """The per-process 009 probe verdict, forgotten before and after each test in this module.

    ``paper_repository`` caches whether ``paper_accounts`` exists, and the verdict is a module
    global - exactly the kind of state that lets one test decide another's answer. The package's
    ``_clean_module_state`` fixture resets the six globals the strategy chain caches; this is the
    paper one, added here rather than there so no existing test's premise moves.
    """
    repo.reset_persistence_probe()
    yield
    repo.reset_persistence_probe()


@pytest.fixture
def paper_store() -> FakeSupabase:
    """The ONE Persistence_Layer double, holding the sandbox owner's Listing and version.

    The owner is the Listing's AUTHOR, so the single admission decision of Requirement 17.4 is
    ``OWNED`` and no subscription plumbing takes part - the subscription paths are
    ``tests/test_entitlement_resolver.py``'s and are not re-proved here. The version's compiled
    plan resolves the sandbox's own market, so the strategy/symbol/timeframe compatibility check
    is answered by the plan rather than waived.
    """
    return FakeSupabase(
        library_strategies=[_listing_row(author_id=SANDBOX_USER["id"])],
        strategy_versions=[
            _version_row(
                compiled_plan=_compiled_plan(
                    symbols={"action-1": SANDBOX_SYMBOL},
                    timeframes={"action-1": SANDBOX_TIMEFRAME},
                )
            )
        ],
    )


class _Caller:
    """The authenticated server-side identity, as the pipeline reads it. Only ``id``."""

    def __init__(self, user_id: str = SANDBOX_USER["id"]) -> None:
        self.id = user_id


def _start(
    store: FakeSupabase,
    *,
    redis: Optional[RecordingRedis] = None,
    caller: Optional[_Caller] = None,
    **overrides: Any,
) -> Any:
    """Drive the REAL start pipeline over the sandbox's market, capital and currency.

    ``exchange=None`` is a legitimate answer and is not mocked: Requirement 15.3 lets a session
    start for a user holding no exchange credentials. ``spawn_loop=None`` is task 27.2's seam -
    ``no_session_loop`` - because a stepping loop would make the persisted figures depend on how
    many bars happened to arrive before the assertion ran.
    """
    kwargs: Dict[str, Any] = {
        "listing_id": LISTING,
        "exchange_id": SANDBOX_VENUE,
        "symbol": SANDBOX_SYMBOL,
        "timeframe": SANDBOX_TIMEFRAME,
        "initial_capital_minor": SANDBOX_PAPER_CAPITAL_MINOR,
        "currency": SANDBOX_PAPER_CURRENCY,
        "now": CHAIN_START,
        "markets": _markets(),
        "exchange": None,
        "redis": redis if redis is not None else RecordingRedis(),
        "measurements": _admitting(),
        "spawn_loop": None,
    }
    kwargs.update(overrides)
    return _run(service.start_session(store, caller or _Caller(), **kwargs))


def _persisted_image(store: FakeSupabase, session_id: str) -> Dict[str, str]:
    """Everything this Paper_Session persisted, as canonical text, read through the repository.

    Read through ``paper_repository`` rather than off the double's attributes, because the claim
    of Requirement 17.2 is that the figures come back from the TABLES through the production
    reader - which is the path a restarted process takes.
    """
    user_id = SANDBOX_USER["id"]
    return {
        "session": json.dumps(
            repo.read_session(store, user_id, session_id), sort_keys=True, default=str
        ),
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
        "events": json.dumps(store.events, sort_keys=True, default=str),
    }


def _recorded_types(store: FakeSupabase) -> list:
    """Every ``paper_events`` row's type, in the order the sequence allocator issued them."""
    return [
        str(row["event_type"])
        for row in sorted(store.events, key=lambda item: int(item["sequence"]))
    ]


# ══════════════════════════════════════════════════════════════════════════
# THE CHAIN
# ══════════════════════════════════════════════════════════════════════════


def test_the_paper_session_lifecycle_runs_create_start_running_stop_and_survives_a_restart(
    sandbox: SandboxWorld, paper_store: FakeSupabase
) -> None:
    """create -> start -> running -> pause -> resume -> stop -> persistence across a restart.

    One test rather than six, for the reason ``test_deterministic_sandbox.py`` gives about its own
    chain: each step's premise is the previous step's result, and six tests would either re-run the
    prefix six times or share mutable state between them. Every step names what it asserts.

    **Validates: Requirements 17.7, 17.8, 17.13, 17.14, 17.15, 25.8**
    """
    redis = RecordingRedis()

    # ══════════════════════════════════════════════════════════════════
    # STEP 1. Create and start.
    # Asserts: the pipeline admits the owner on the strength of the entitlement decision alone,
    # creates the session in environment PAPER at the recorded capital with its own isolated
    # Paper_Account, writes the opening equity point at series index 0, records the
    # paper_session_started frame, opens the market-data subscription on this pair, and leaves
    # the session at RUNNING.
    # ══════════════════════════════════════════════════════════════════
    started = _start(paper_store, redis=redis)
    session_id = str(started.session["id"])

    assert started.entitlement.entitling is True
    assert started.session["environment"] == "PAPER", (
        "chk_paper_session_environment pins the column to 'PAPER'; a session created in any "
        "other environment is a simulated execution presented as something else "
        "(Requirement 28.4)"
    )
    assert started.session["session_state"] == service.STATE_RUNNING
    assert str(started.session["user_id"]) == SANDBOX_USER["id"]
    assert int(started.session["initial_capital_minor"]) == SANDBOX_PAPER_CAPITAL_MINOR, (
        "the recorded capital must be the exact integer Minor_Units the caller asked for; "
        "money is never a float in this repository (Requirement 18.1)"
    )
    assert str(started.account["session_id"]) == session_id, (
        "a Paper_Session's account is its OWN account (uq_paper_account_session), not the "
        "caller's default one"
    )
    assert str(started.session["source_strategy_id"]) == STRATEGY
    assert str(started.session["version_id"]) == VERSION
    assert _recorded_types(paper_store) == ["paper_session_started"]
    assert redis.subscribed == [f"mds:data:{SANDBOX_VENUE}:{SANDBOX_SYMBOL}"], redis.log

    opening = repo.get_equity_snapshots(paper_store, SANDBOX_USER["id"], session_id=session_id)
    assert [int(row["series_index"]) for row in opening] == [0], opening
    assert str(opening[0]["cause"]) == "SESSION_START"

    # ══════════════════════════════════════════════════════════════════
    # STEP 2. RUNNING is a state the machine enforces, not a label.
    # Asserts: an operation that has no edge from RUNNING is refused 409 naming BOTH the current
    # state and the rejected operation, and nothing is written - Requirement 17.14 measured on
    # the statements the double did not see rather than on a rollback this transport does not
    # have.
    # ══════════════════════════════════════════════════════════════════
    before_rejection = _persisted_image(paper_store, session_id)
    with pytest.raises(PaperError) as rejected:
        _run(service.resume_session(paper_store, _Caller(), session_id, now=CHAIN_START))
    detail = getattr(rejected.value, "details", None) or {}
    assert str(detail.get("session_state")) == service.STATE_RUNNING, rejected.value
    assert str(detail.get("operation")) == service.OPERATION_RESUME, rejected.value
    assert _persisted_image(paper_store, session_id) == before_rejection, (
        "a rejected operation wrote something (Requirement 17.14)"
    )

    # ══════════════════════════════════════════════════════════════════
    # STEP 3. Pause and resume, along 009's own edges.
    # Asserts: RUNNING -> PAUSED -> RUNNING, each recorded in paper_events with the requesting
    # user and the RESULTING state (Requirement 17.7's three members), each at the next sequence,
    # and the feed subscription left OPEN across the pause - a pause that released it would be a
    # stop under another name.
    # ══════════════════════════════════════════════════════════════════
    paused = _run(
        service.pause_session(
            paper_store, _Caller(), session_id, now=CHAIN_START + timedelta(minutes=1)
        )
    )
    assert (paused.from_state, paused.to_state) == (
        service.STATE_RUNNING,
        service.STATE_PAUSED,
    )
    resumed = _run(
        service.resume_session(
            paper_store, _Caller(), session_id, now=CHAIN_START + timedelta(minutes=2)
        )
    )
    assert (resumed.from_state, resumed.to_state) == (
        service.STATE_PAUSED,
        service.STATE_RUNNING,
    )
    assert _recorded_types(paper_store) == [
        "paper_session_started",
        "paper_session_paused",
        "paper_session_resumed",
    ]
    assert [int(row["sequence"]) for row in sorted(
        paper_store.events, key=lambda item: int(item["sequence"])
    )] == [1, 2, 3], (
        "uq_paper_event_seq allocates one sequence per session; a gap or a repeat is what a "
        "client resuming a replay would read as a lost frame"
    )
    for row in paper_store.events:
        assert str(row["user_id"]) == SANDBOX_USER["id"]
        assert str(row["payload"]["actor_id"]) == SANDBOX_USER["id"], (
            "Requirement 17.7 requires the REQUESTING user recorded, and it is the "
            "authenticated identity rather than anything a request carried"
        )
    paused_commands = [str(command.get("action")) for command in redis.commands()]
    assert "unsubscribe" not in paused_commands, (
        f"the market-data subscription was released during a pause, which would make the pause a "
        f"stop under another name. Commands published so far: {paused_commands!r}"
    )

    # ══════════════════════════════════════════════════════════════════
    # STEP 4. Stop.
    # Asserts: RUNNING -> STOPPED, the closing equity point and the closing metrics row are
    # COMMITTED before the stop is reported, the mds unsubscribe is published, the
    # paper_session_stopped frame is recorded, and the persisted session_state is STOPPED.
    # ══════════════════════════════════════════════════════════════════
    stopped = _run(
        service.stop_session(
            paper_store,
            _Caller(),
            session_id,
            now=CHAIN_START + timedelta(minutes=3),
            feed=started.feed,
            loop_task=None,
        )
    )
    assert (stopped.from_state, stopped.to_state) == (
        service.STATE_RUNNING,
        service.STATE_STOPPED,
    )
    assert stopped.finals.snapshot is not None, (
        "Requirement 17.8 requires the closing equity point COMMITTED before the stop is "
        "reported"
    )
    assert stopped.finals.metrics_row is not None
    assert stopped.mds_released is True, redis.log
    assert _recorded_types(paper_store)[-1] == "paper_session_stopped"
    assert (
        repo.read_session(paper_store, SANDBOX_USER["id"], session_id)["session_state"]
        == service.STATE_STOPPED
    )

    # A second stop is refused from STOPPED and releases nothing again.
    with pytest.raises(PaperError):
        _run(
            service.stop_session(
                paper_store,
                _Caller(),
                session_id,
                now=CHAIN_START + timedelta(minutes=4),
                feed=None,
                loop_task=None,
            )
        )

    # ══════════════════════════════════════════════════════════════════
    # STEP 5. Persistence across a simulated restart.
    # Asserts: nothing this session reported lived in process memory. The paper singleton is
    # dropped and the 009 probe verdict forgotten - which is what a fresh process starts with -
    # and every session row, order, position, trade and equity point reads back byte-identically
    # through the production reader.
    # ══════════════════════════════════════════════════════════════════
    before_restart = _persisted_image(paper_store, session_id)

    from backend_app.backend import paper_trading_service as paper_module

    previous_instance = paper_module._paper_service_instance
    paper_module._paper_service_instance = None
    repo.reset_persistence_probe()
    restarted = paper_module.PaperTradingService(
        default_capital=100_000.0, default_fee_rate=0.001, default_slippage=0.0005
    )
    restarted.bind_persistence(paper_store)
    paper_module._paper_service_instance = restarted
    try:
        after_restart = _persisted_image(paper_store, session_id)
    finally:
        restarted.bind_persistence(None)
        paper_module._paper_service_instance = previous_instance
        repo.reset_persistence_probe()

    assert after_restart == before_restart, (
        "a figure changed across the restart, so it was held in process memory rather than in "
        "the paper_* tables (Requirements 17.2, 28.3)"
    )
    for name in ("session", "equity", "events"):
        assert after_restart[name] not in ("[]", "null"), (
            f"{name} came back empty after the restart, so the comparison above compared nothing"
        )

    # The sandbox world is untouched by all of this: the paper rows live in the paper double, and
    # the strategy/deployment tables this package's other chain asserts on are a different store.
    assert sandbox.db.rows("paper_sessions") == [], (
        "the Paper_Session lifecycle wrote into SandboxDatabase, which holds this package's "
        "001/003/005b surface and does not implement 009's indexes, defaults or triggers"
    )
    assert sandbox.exchange.order_placement_calls == 0, (
        "a simulated session asked the venue to place an order (Requirement 26.4)"
    )


def test_a_refused_start_creates_no_paper_session_at_all(
    sandbox: SandboxWorld, paper_store: FakeSupabase
) -> None:
    """Requirement 17.13: nothing is created until every validation has passed.

    The validation chosen is the one this sandbox can state without inventing anything: a
    timeframe the platform does not support. The refusal has to arrive with the Persistence_Layer
    having seen no ``insert`` and no ``update`` at all, and with nothing published to
    ``mds:commands`` - so "nothing was created" is a fact about statements the code did not issue
    rather than about rows a test happened not to look for.

    Paired with the chain above deliberately: a pipeline that refused everything would satisfy
    this test, and a pipeline that created eagerly would satisfy that one. Neither passes both.

    **Validates: Requirement 17.13**
    """
    redis = RecordingRedis()
    with pytest.raises(PaperError):
        _start(paper_store, redis=redis, timeframe="17s")

    offenders = [
        (statement.op, statement.table_name)
        for statement in paper_store.statements
        if statement.op != "select"
    ]
    assert offenders == [], (
        f"a refused start issued {offenders!r}; Requirement 17.13 requires every validation to "
        f"pass before anything is created"
    )
    assert redis.log == [], (
        f"a refused start touched the market-data transport: {redis.log!r}"
    )
    assert sandbox.exchange.order_placement_calls == 0


def test_this_module_never_calls_asyncio_run() -> None:
    """The paper suite has ONE event loop, and it is ``_run_coroutine``'s.

    A fresh loop per coroutine exhausted the machine's ephemeral port range through
    ``socket._fallback_socketpair`` and hung the paper suite past 700 s on Windows;
    ``tests/test_paper_order_lifecycle_writes._HARNESS_LOOP`` records why. Asserted by parsing
    this module's own source rather than by convention, because a convention is what this is a
    guard against. ``tests/e2e/test_marketplace_paper_journey.py`` carries the same guard for the
    same reason.
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
