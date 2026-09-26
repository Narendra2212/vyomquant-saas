"""
tests/test_paper_live_separation.py - task 12.2, requirement 1.12 / 2.12.

Spec: production-launch-hardening. ``bugfix.md`` 1.12 / 2.12, ``design.md``'s Requirement 1.12
row. "WHEN a paper-bound strategy signals, THEN no live venue adapter SHALL be reachable
... a test that asserts the live adapter is never constructed for a paper-bound strategy, using
a failing double that raises if invoked."

THE LIVE VENUE ADAPTER, AND WHERE IT IS CONSTRUCTED
----------------------------------------------------
``backend_app/backend/exchange_executor.py`` is the module that places a real order on a real
exchange through CCXT ("Live Exchange Executor ... STEP 6 - LIVE EXCHANGE EXECUTION"). Its class
is ``CCXTExchangeExecutor(BaseExchangeExecutor)``; ``CCXTExchangeExecutor`` declares no
``__init__`` of its own, so ``BaseExchangeExecutor.__init__`` (``exchange_executor.py:531``) is
the ONE constructor call every instance of it runs, whether built directly, through
``ExchangeExecutorFactory.create`` (the only call site in the tree - ``exchange_executor.py:1259``)
or through the module-level ``get_exchange_executor()`` factory
(``master_executor.py``'s live branch calls exactly that). Patching that one ``__init__`` is
therefore a patch on the live adapter's single seam, not on one of several.

``master_executor.BotRunner._recover_live_state`` is the fork this requirement is about:

    is_paper = self.blueprint.get("paper_trading", True)
    if is_paper:
        ...  # ConnectionEngine is constructed, but mocked (_apply_mock_interface()) -
        ...  # get_exchange_executor is never imported on this branch
        return
    # LIVE path only, below this line:
    from backend_app.backend.exchange_executor import get_exchange_executor
    exchange_exec = get_exchange_executor(...)

So the live adapter's construction is reachable from exactly one branch, and every PAPER signal
path this file drives is a different branch: the direct order route, the strategy-driven paper
session loop, and the shared simulator both funnel into. None of the paper modules
(``backend/paper/*.py``, ``backend/paper_trading_service.py``) import ``exchange_executor``,
``connection_engine`` or ``master_executor`` anywhere in their code (only in prose comments about
what OTHER code produces the CCXT market-entry shape they consume as a plain value) - confirmed by
grep over this tree before this file was written. This test does not trust that absence; it proves
it by making the one live construction seam raise if it is ever reached.

EVERY PAPER SIGNAL PATH DRIVEN HERE
------------------------------------
1. ``PaperTradingService.place_order`` - the handler behind ``POST /api/paper/orders``
   (``routers/paper_trading.py``'s ``place_paper_order``). Market order, limit order, and a
   repeated Idempotency-Key (the retry path) are all exercised, because a retry re-reading a
   recorded order is a code path of its own.
2. ``paper_simulator.submit_intent`` directly - the terminal write BOTH the order route and the
   session loop below converge on, driven the same way the existing lifecycle tests drive it
   (``tests/test_paper_order_lifecycle_writes.py``'s harness), for a market order and a resting
   limit order.
3. ``paper_session_service.step_session`` - the strategy-bound Paper_Session loop
   (``POST /api/paper/sessions`` -> ``start_session`` -> ``spawn_session_loop`` ->
   ``session_loop`` -> this function), which turns a strategy's generated signal into an order
   intent (``signal_to_intent``) and submits it. This is the path Requirement 1.12's own words
   name: "a strategy bound to the paper environment produces a signal".

No second harness is built for any of these: the ``FakeSupabase`` double, ``_config``/``_seed``
from ``tests/test_paper_order_lifecycle_writes.py``, and ``_running_session``/``_frozen_config``/
``_loop_feed``/``_Runtime``/``_signal`` from ``tests/test_task_27_session_service.py`` are
imported and reused exactly as the property tests already do, per this task's own instruction not
to author a second account of what a paper order or a paper session is.

WHAT WOULD MAKE THIS TEST FAIL, IF THE BUG IT GUARDS AGAINST EXISTED
----------------------------------------------------------------------
Any code change that made a paper signal path import ``exchange_executor``, call
``ExchangeExecutorFactory.create``, call ``get_exchange_executor``, or construct
``CCXTExchangeExecutor``/``BaseExchangeExecutor`` directly or indirectly would raise
``LiveVenueAdapterConstructed`` the instant it happened, inside the paper call stack, and this
file's assertions on ``FAILING_DOUBLE.calls`` would report it. The double raises rather than
merely recording, so a silent construction whose result is discarded cannot go unnoticed either.

CODE-LEVEL ONLY
---------------
No network call, no real exchange, no venue credential. Every double here is the same
Persistence_Layer double the rest of the paper suite uses.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Tuple

import pytest

from backend_app.backend import exchange_executor
from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper import paper_session_service as session_service
from backend_app.backend.paper import paper_simulator as sim
from backend_app.backend.paper.paper_order_state import PaperOrderState
from backend_app.backend.paper_trading_service import PaperTradingService

# ── The shared harnesses. A second one would be a second account of what a paper order or a
#    paper session is - see this module's docstring. ──────────────────────────────────────────
from tests.test_paper_order_lifecycle_writes import (
    CAPITAL,
    EXCHANGE,
    NOW,
    SYMBOL,
    USER,
    _config,
    _intent,
    _run_coroutine,
    _seed,
)
from tests.test_paper_market_feed_selection import _frame
from tests.test_paper_repository import FakeSupabase
from tests.test_task_27_session_service import USER as SESSION_LOOP_USER
from tests.test_task_27_session_service import (
    _Runtime,
    _frozen_config,
    _loop_feed,
    _running_session,
    _signal,
)


# ══════════════════════════════════════════════════════════════════════════
# THE FAILING DOUBLE
# ══════════════════════════════════════════════════════════════════════════


class LiveVenueAdapterConstructed(AssertionError):
    """Raised the instant the live venue adapter's constructor runs.

    An :class:`AssertionError` subclass rather than a plain one, so a broad
    ``except Exception`` somewhere on the paper path (``step_session`` and
    ``PaperTradingService.place_order`` both contain per-item failures) does not quietly
    swallow this as "just another refused order" - the paper call sites this file drives catch
    only ``PaperError`` / ``PaperFeedError`` / ``InvalidOrderIntent`` / ``ValueError`` /
    ``PermissionError``, never a bare ``AssertionError``, so a live construction still surfaces
    to this test rather than being absorbed as a skipped signal.
    """

    def __init__(self, *, exchange_id: str, api_key: Any) -> None:
        # STEP 6.10 of the module under test says "NEVER log API keys" - this failure message
        # honours that even while proving the constructor ran: it names the exchange, never the
        # key.
        super().__init__(
            f"the live venue adapter (BaseExchangeExecutor.__init__) was constructed for "
            f"exchange_id={exchange_id!r} on what must have been a paper signal path; "
            f"api_key given: {api_key is not None}"
        )
        self.exchange_id = exchange_id


class _FailingDouble:
    """Records every attempted construction, then raises :class:`LiveVenueAdapterConstructed`.

    Installed in place of ``BaseExchangeExecutor.__init__`` - the ONE constructor every live
    adapter instance runs (see module docstring). Recording before raising means a test can
    report exactly what was attempted even though the attempt never returns an instance.

    ``as_init`` produces a plain module-level FUNCTION rather than handing over a callable
    instance directly: assigning an arbitrary object (not a function) as a class attribute does
    not make Python bind it as a method on the next instance access - only a genuine function
    goes through the descriptor protocol that supplies ``self`` automatically. A callable
    instance assigned directly would silently never receive the ``BaseExchangeExecutor`` object
    being constructed, which would make this double's own signature wrong in a way a passing run
    would never reveal.
    """

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def as_init(self) -> Any:
        def _raising_init(_instance: Any, exchange_id: str, api_key: str, api_secret: str, **kwargs: Any) -> None:
            self.calls.append(
                {"exchange_id": exchange_id, "api_key": api_key, "api_secret": api_secret, **kwargs}
            )
            raise LiveVenueAdapterConstructed(exchange_id=exchange_id, api_key=api_key)

        return _raising_init


@pytest.fixture
def failing_live_adapter(monkeypatch: pytest.MonkeyPatch) -> _FailingDouble:
    """Substitute the failing double for the live venue adapter's constructor.

    Patched on ``exchange_executor.BaseExchangeExecutor.__init__`` - the base class -
    rather than on ``CCXTExchangeExecutor``, because ``CCXTExchangeExecutor`` declares no
    ``__init__`` of its own and inherits the base's; patching the subclass's (absent) override
    would patch nothing and this test would pass for a reason that proves nothing, which is
    exactly the failure mode a "failing double" test exists to avoid.
    """
    double = _FailingDouble()
    monkeypatch.setattr(exchange_executor.BaseExchangeExecutor, "__init__", double.as_init())
    return double


def test_the_failing_double_actually_raises_when_the_live_adapter_is_constructed(
    failing_live_adapter: _FailingDouble,
) -> None:
    """Sanity check on the double itself, independent of any paper path.

    A failing double that does not fail proves nothing about the paths below - this is the
    "does the trap spring" check, run once, directly, before any paper call is trusted to have
    passed through the same trap.
    """
    with pytest.raises(LiveVenueAdapterConstructed) as excinfo:
        exchange_executor.CCXTExchangeExecutor(
            exchange_id="binance", api_key="k", api_secret="s"
        )
    assert excinfo.value.exchange_id == "binance"
    assert failing_live_adapter.calls == [
        {"exchange_id": "binance", "api_key": "k", "api_secret": "s"}
    ]


def test_the_double_also_traps_the_module_level_factory_function(
    failing_live_adapter: _FailingDouble,
) -> None:
    """``get_exchange_executor`` - what ``master_executor``'s LIVE branch actually calls - is
    caught by the same seam, not bypassed by its own cache.

    ``get_exchange_executor`` memoizes by ``f"{exchange_id}:{sandbox}"`` in a module-level
    dict; a stale entry from an earlier (unrelated) test importing this module could hide a
    fresh construction behind a cache hit. A key that cannot already be cached is used, and the
    registry is confirmed empty of it beforehand, so a pass here is about THIS call.
    """
    key = "kraken:False"
    assert key not in exchange_executor._executors
    with pytest.raises(LiveVenueAdapterConstructed):
        exchange_executor.get_exchange_executor(
            exchange_id="kraken", api_key="k", api_secret="s", sandbox=False
        )
    assert key not in exchange_executor._executors, (
        "a construction that raised must not leave a half-built executor cached"
    )


# ══════════════════════════════════════════════════════════════════════════
# PAPER SIGNAL PATH 1 - PaperTradingService.place_order (POST /api/paper/orders)
# ══════════════════════════════════════════════════════════════════════════


def _paper_service(supabase: FakeSupabase) -> PaperTradingService:
    """A service instance bound to ``supabase``, the same seam
    ``tests/sandbox_lifecycle/test_paper_session_lifecycle.py`` and
    ``tests/e2e/test_marketplace_paper_journey.py`` use rather than reaching a real client.
    """
    return PaperTradingService(
        default_capital=100_000.0, default_fee_rate=0.001, default_slippage=0.0005,
        supabase=supabase,
    )


def _seeded_service() -> Tuple[PaperTradingService, FakeSupabase]:
    repo.reset_persistence_probe()
    supabase = FakeSupabase()
    service = _paper_service(supabase)
    return service, supabase


class TestPlaceOrderNeverConstructsTheLiveAdapter:
    """Every ``place_order`` call this class makes is a paper order; none may reach the live
    adapter's constructor, whatever else it does or refuses.
    """

    def test_a_market_order(self, failing_live_adapter: _FailingDouble) -> None:
        service, _supabase = _seeded_service()
        order = _run_coroutine(
            service.place_order(
                user_id=USER, symbol="BTC/USDT", side="buy",
                order_type="market", quantity=0.01,
            )
        )
        assert order["side"] == "buy"
        assert failing_live_adapter.calls == []

    def test_a_limit_order(self, failing_live_adapter: _FailingDouble) -> None:
        service, _supabase = _seeded_service()
        order = _run_coroutine(
            service.place_order(
                user_id=USER, symbol="ETH/USDT", side="sell",
                order_type="limit", quantity=0.5, price=3_000.0,
            )
        )
        assert order["order_type"] == "limit"
        assert failing_live_adapter.calls == []

    def test_a_retried_submission_with_the_same_idempotency_key(
        self, failing_live_adapter: _FailingDouble
    ) -> None:
        """The retry path: the second call re-reads the first order rather than placing a new
        one - and neither call may reach the live adapter.
        """
        service, _supabase = _seeded_service()
        first = _run_coroutine(
            service.place_order(
                user_id=USER, symbol="BTC/USDT", side="buy", order_type="market",
                quantity=0.02, idempotency_key="retry-key-1",
            )
        )
        second = _run_coroutine(
            service.place_order(
                user_id=USER, symbol="BTC/USDT", side="buy", order_type="market",
                quantity=0.02, idempotency_key="retry-key-1",
            )
        )
        assert second["order_id"] == first["order_id"]
        assert failing_live_adapter.calls == []

    def test_a_rejected_order_still_never_reaches_the_live_adapter(
        self, failing_live_adapter: _FailingDouble
    ) -> None:
        """A statically-refused intent (zero quantity) is refused BEFORE anything is placed,
        paper or live - proving the refusal itself never routes through the live seam either.
        """
        service, _supabase = _seeded_service()
        with pytest.raises(ValueError):
            _run_coroutine(
                service.place_order(
                    user_id=USER, symbol="BTC/USDT", side="buy",
                    order_type="market", quantity=0.0,
                )
            )
        assert failing_live_adapter.calls == []


# ══════════════════════════════════════════════════════════════════════════
# PAPER SIGNAL PATH 2 - paper_simulator.submit_intent directly
# ══════════════════════════════════════════════════════════════════════════
#
# The terminal write both PaperTradingService.place_order and the session loop below converge
# on. Driven with the exact harness ``tests/test_paper_order_lifecycle_writes.py`` and the
# property suite already use, per this task's instruction to reuse rather than rebuild it.


class TestSubmitIntentNeverConstructsTheLiveAdapter:
    def test_a_market_buy(self, failing_live_adapter: _FailingDouble) -> None:
        supabase, session, account_id = _seed(capital=CAPITAL)
        outcome = _run_coroutine(
            sim.submit_intent(
                supabase, session, _intent(side="buy", quantity="0.4"),
                config=_config(), account_id=account_id,
                # A market order is priced off a validated event or an explicit reference
                # (Requirement 14.9 forbids synthesising one), and its fill needs an instant
                # (Requirement 15.4: no clock read) - the same ``reference=``/``filled_at=`` the
                # harness's own ``_submit``/``_fill`` accept.
                reference="60000",
                filled_at=NOW,
            )
        )
        assert outcome.accepted
        assert failing_live_adapter.calls == []

    def test_a_resting_limit_sell(self, failing_live_adapter: _FailingDouble) -> None:
        supabase, session, account_id = _seed(capital=CAPITAL)
        outcome = _run_coroutine(
            sim.submit_intent(
                supabase, session,
                _intent(side="sell", order_type="limit", quantity="0.1", limit_price="70000"),
                config=_config(), account_id=account_id,
            )
        )
        assert outcome.order["order_type"] == "limit"
        assert failing_live_adapter.calls == []

    def test_an_invalid_intent_is_refused_without_reaching_the_live_adapter(
        self, failing_live_adapter: _FailingDouble
    ) -> None:
        """A quantity of zero fails ``chk_paper_order_quantity`` (Requirement 16.5), one of the
        four rejections ``paper_orders`` itself refuses to represent - so ``submit_intent`` raises
        ``PaperOrderInvalid`` rather than returning a rejected order (see
        ``paper_simulator.ORDER_COLUMN_CONSTRAINTS`` and ``tests/test_paper_order_lifecycle_writes.py``
        for the same branch). The refusal is the point: this test asserts it still never reaches
        the live adapter on its way there.
        """
        supabase, session, account_id = _seed(capital=CAPITAL)
        with pytest.raises(sim.PaperOrderInvalid):
            _run_coroutine(
                sim.submit_intent(
                    supabase, session, _intent(quantity="0"),
                    config=_config(), account_id=account_id,
                )
            )
        assert failing_live_adapter.calls == []


# ══════════════════════════════════════════════════════════════════════════
# PAPER SIGNAL PATH 3 - paper_session_service.step_session (the strategy-bound session loop)
# ══════════════════════════════════════════════════════════════════════════
#
# "a strategy bound to the paper environment produces a signal" (bugfix.md 1.12, verbatim) is
# this path's own description. It is driven with the harness
# ``tests/test_task_27_session_service.py`` already built for it: ``_running_session``,
# ``_frozen_config``, ``_loop_feed``, ``_Runtime`` (the DAG-runtime stand-in Requirement 17.10
# makes an explicit seam) and ``_signal`` (one generated signal in the loop's own wire shape).


def _session_double(*, capital: Decimal = CAPITAL) -> Tuple[FakeSupabase, Dict[str, Any], str]:
    """A running Paper_Session and its isolated account, seeded the way ``start_session`` leaves
    it - the same premise :func:`tests.test_task_27_session_service._loop_double` builds, spelled
    here so this file's only import from that module is the pieces it actually calls.

    Seeded under ``SESSION_LOOP_USER`` - ``test_task_27_session_service.USER`` - and NOT under
    this file's own ``USER`` (imported from ``test_paper_order_lifecycle_writes`` for paths 1 and
    2): the two modules use different literal UUIDs for "the one user", and
    ``_running_session()`` defaults its ``user_id`` to the former. Seeding the account under the
    wrong one makes ``lock_account_for_update`` find nothing to lock - a mismatch, not a
    paper/live concern, and worth naming so a future reader does not "fix" it back.
    """
    repo.reset_persistence_probe()
    row = _running_session()
    supabase = FakeSupabase(sessions=[row])
    account = repo.get_or_create_account(
        supabase, SESSION_LOOP_USER, "USD", row["id"], initial_capital=capital
    )
    supabase.statements.clear()
    supabase.ops.clear()
    return supabase, row, str(account["id"])


class TestStepSessionNeverConstructsTheLiveAdapter:
    """A strategy-bound paper session that generates a BUY signal, an EXIT signal, and a signal
    that is refused as not-executable - none of which may reach the live adapter's constructor.
    """

    def test_a_generated_buy_signal_is_submitted_without_touching_the_live_adapter(
        self, failing_live_adapter: _FailingDouble
    ) -> None:
        supabase, session, account_id = _session_double()
        handle, _redis = _loop_feed(supabase, [_frame()])
        step = _run_coroutine(
            session_service.step_session(
                supabase, session, handle,
                config=_frozen_config(), account_id=account_id,
                evaluate=_Runtime([_signal(decision="BUY", side="buy", quantity="0.3")]),
                plan="plan-live-separation",
            )
        )
        # A real frame was delivered and a real BUY signal was generated and submitted - so this
        # is a genuine end-to-end drive of the strategy-bound loop, not a step the empty feed
        # dropped before the evaluator ever ran.
        assert step.dropped is False
        assert len(step.signals) == 1
        assert len(step.submissions) == 1
        assert step.submissions[0].accepted
        assert failing_live_adapter.calls == []

    def test_a_signal_with_no_executable_intent_is_skipped_not_routed_live(
        self, failing_live_adapter: _FailingDouble
    ) -> None:
        """An EXIT with no open position and no stated quantity produces no intent at all
        (``PaperSignalNotExecutable``) - proving the refusal path is also clean of the live seam.
        """
        supabase, session, account_id = _session_double()
        handle, _redis = _loop_feed(supabase, [_frame()])
        step = _run_coroutine(
            session_service.step_session(
                supabase, session, handle,
                config=_frozen_config(), account_id=account_id,
                evaluate=_Runtime(
                    [_signal(decision="EXIT", side=None, quantity=None)]
                ),
                plan="plan-live-separation",
            )
        )
        assert step.dropped is False
        assert len(step.skipped) == 1
        assert len(step.submissions) == 0
        assert failing_live_adapter.calls == []


# ══════════════════════════════════════════════════════════════════════════
# THE FORK ITSELF - master_executor's paper branch never imports the live adapter module
# ══════════════════════════════════════════════════════════════════════════


def test_master_executor_paper_branch_source_contains_no_reference_to_the_live_factory() -> None:
    """A structural guard on the fork the requirement is about
    (``master_executor.BotRunner._recover_live_state``): the paper branch (``if is_paper:`` up to
    its ``return``) must not import or call ``get_exchange_executor``, because that import is
    what the live branch alone performs (module docstring).

    ``exchange_executor=None`` - the ``ExecutionEngine`` keyword the paper branch DOES pass, to
    say it is wiring in no executor at all - names the module as a substring, so the guard checks
    for the CALL and the IMPORT specifically rather than for that bare substring; a naive
    substring check would flag its own paper-safe keyword as if it were the live import.

    A source-text guard rather than a coverage-based one, because forcing this exact private
    method to execute its network-touching LIVE half is out of bounds for a code-level,
    no-venue proof - the guard establishes that the text a maintainer would have to change to
    introduce the bug is absent, which is what this task's exploration test can establish without
    a running exchange.
    """
    import inspect

    from backend_app.backend.master_executor import BotRunner

    source = inspect.getsource(BotRunner._recover_live_state)
    is_paper_start = source.index("is_paper = self.blueprint")
    paper_branch_end = source.index("# LIVE path", is_paper_start)
    paper_branch = source[is_paper_start:paper_branch_end]

    assert "import get_exchange_executor" not in paper_branch
    assert "get_exchange_executor(" not in paper_branch
    # And the live import is exactly where it is expected to be: after the paper branch, not
    # hoisted to module scope where a paper run would execute it on nothing more than an import.
    assert "from backend_app.backend.exchange_executor import get_exchange_executor" in source
    assert "from backend_app.backend.exchange_executor import get_exchange_executor" not in paper_branch
    assert "get_exchange_executor(" in source[paper_branch_end:]
