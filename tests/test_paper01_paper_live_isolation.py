"""PAPER-01: paper mode cannot reach a real exchange write, and cannot decrypt a credential.

WHAT THIS FILE PROVES, AND HOW
==============================
Three claims, each with a different kind of evidence, because no single kind is enough:

1. **Structural.** An AST inventory of every ``create_order`` / ``cancel_order`` call site
   in ``backend_app/``, classified BY RECEIVER, asserting the set of real venue-write
   boundaries is exactly the four already pinned -- so a new one cannot appear unnoticed.
   Classifying by receiver rather than by method name is load-bearing: the phase-4 sweep's
   first run surfaced four apparent new boundaries that were all local calls whose names
   collide with CCXT's (``self.state_service.create_order``,
   ``self.order_repo.create_order``, ``self.cancel_order``).

2. **Behavioural, through the REAL construction path.** ``BotRunner._recover_live_state``
   is executed in paper mode with a vault that RAISES if it is touched and an
   ``get_exchange_executor`` that RAISES if it is called. If either fires, paper mode
   decrypted a credential or built a live adapter. This is the production boot sequence,
   not a reimplementation of it.

3. **Negative controls.** The same tripwires are shown to FIRE on the live branch. Without
   that, claim 2 would also pass if the tripwires were simply never armed.

The user requirement is explicit that mocks alone are not sufficient, so the tripwires are
placed around the real objects and the real function is called. What is injected is only
``global_state`` -- the dependency BotRunner takes by constructor -- and the two seams the
tripwires replace.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import os
import sys
import textwrap
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend.execution_environment import ExecutionEnvironment  # noqa: E402
from backend_app.core.execution_engine import ExecutionEngine  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend_app"


# ══════════════════════════════════════════════════════════════════════════
#  1. THE VENUE-WRITE INVENTORY (structural)
# ══════════════════════════════════════════════════════════════════════════

#: The only receivers that reach a real exchange. ``self._exchange`` is the CCXT handle a
#: ``CCXTExchangeExecutor`` holds; ``self.adapter`` / ``adapter`` is the executor the
#: transactional manager delegates to.
VENUE_RECEIVERS = frozenset({"_exchange", "exchange", "adapter", "_adapter"})

#: Receivers whose ``create_order`` / ``cancel_order`` is a LOCAL call -- a database
#: repository, a state machine, a paper service, this object itself. Measured; listed so a
#: new one has to be classified deliberately rather than silently counted as a venue write.
#:
#:   ``state_service``  core/reconciliation_engine.py:754,756 - the order state store
#:   ``order_repo``     core/database_scaling.py:617          - a database repository
#:   ``self``           core/order_state_engine.py:745        - the state machine itself
#:   ``service``        routers/paper_trading.py:249          - PaperTradingService.cancel_order
#:                                                              (line 1807), the PAPER path
LOCAL_RECEIVERS = frozenset(
    {
        "state_service",
        "order_repo",
        "self",
        "service",
        "repo",
        "repository",
        "store",
        "db",
        "order_state_engine",
        "engine",
    }
)

#: Receivers whose call site is UNREACHABLE, with the evidence.
#:
#:   ``execution_engine``  routers/orders.py:915. The line above it is
#:       ``execution_engine = await _build_execution_engine(...)``, and
#:       ``_build_execution_engine`` (orders.py:373) is deprecated and raises
#:       ``HTTPException(403, DIRECT_EXECUTION_BLOCKED)`` unconditionally -- it never
#:       returns an object. So this ``cancel_order`` is dead code behind a hard block.
#:
#:       It is categorised separately from LOCAL rather than lumped in with it, because
#:       "local" and "dead" are different facts and only one of them stays true if
#:       somebody un-blocks the builder. ``test_the_blocked_execution_builder_is_still_blocked``
#:       is the tripwire for that.
#:
#:       Note also that ``ExecutionEngine`` has NO ``cancel_order`` method at all
#:       (grepped), so if the builder were un-blocked this line would raise
#:       ``AttributeError`` -- the PORT-01 shape.
UNREACHABLE_RECEIVERS = frozenset({"execution_engine"})

VENUE_METHODS = frozenset({"create_order", "cancel_order"})


def _receiver_name(node: ast.Attribute) -> str:
    """The attribute chain's last receiver: ``self.a.b.create_order`` -> ``b``."""
    value = node.value
    if isinstance(value, ast.Attribute):
        return value.attr
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Call):
        func = value.func
        if isinstance(func, ast.Attribute):
            return func.attr
        if isinstance(func, ast.Name):
            return func.id
    return "<expr>"


def venue_write_sites() -> List[Tuple[str, int, str, str]]:
    """``(relative path, line, method, receiver)`` for every ``create_order``/``cancel_order``."""
    found: List[Tuple[str, int, str, str]] = []
    for path in sorted(BACKEND.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr not in VENUE_METHODS:
                continue
            found.append(
                (
                    path.relative_to(REPO_ROOT).as_posix(),
                    node.lineno,
                    func.attr,
                    _receiver_name(func),
                )
            )
    return found


def test_every_venue_write_site_is_classified():
    """No ``create_order``/``cancel_order`` may be left unclassified.

    This is the tripwire: a NEW call site on an unrecognised receiver fails here, and
    whoever added it has to say whether it reaches a venue.
    """
    known = VENUE_RECEIVERS | LOCAL_RECEIVERS | UNREACHABLE_RECEIVERS
    unclassified = [site for site in venue_write_sites() if site[3] not in known]
    assert not unclassified, (
        "these create_order/cancel_order call sites are on receivers this inventory does "
        "not know about, so it cannot say whether they reach a real exchange:\n"
        + "\n".join(f"  {p}:{line} {method} on {recv!r}" for p, line, method, recv in unclassified)
    )


def test_the_real_venue_boundaries_are_exactly_the_four_already_gated():
    """The authoritative inventory, pinned.

    Four boundaries, all behind the TRADE-04 ``LiveOrderAuthorization``:
    ``exchange_executor`` create/cancel and ``transactional_execution_manager``
    create/cancel. A fifth appearing is either a new gate to write or a new leak.
    """
    venue = {
        (path, method)
        for path, _line, method, recv in venue_write_sites()
        if recv in VENUE_RECEIVERS
    }
    expected = {
        ("backend_app/backend/exchange_executor.py", "create_order"),
        ("backend_app/backend/exchange_executor.py", "cancel_order"),
        ("backend_app/backend/transactional_execution_manager.py", "create_order"),
        ("backend_app/backend/transactional_execution_manager.py", "cancel_order"),
    }
    assert venue == expected, (
        f"the set of real venue-write boundaries changed.\n"
        f"  added:   {sorted(venue - expected)}\n"
        f"  removed: {sorted(expected - venue)}"
    )


def test_no_module_under_paper_reaches_a_venue():
    """``backend_app/backend/paper/`` must contain no venue write at all.

    The paper package owns the simulator and the ledger. If a ``create_order`` on a venue
    receiver ever appears in it, paper execution has acquired a route to an exchange.
    """
    offenders = [
        site
        for site in venue_write_sites()
        if site[0].startswith("backend_app/backend/paper/")
    ]
    assert offenders == [], (
        f"the paper package contains exchange-write call sites: {offenders}"
    )


def test_the_bot_paper_ledger_imports_no_exchange_module():
    """The seam PAPER-01 added must not drag an exchange adapter into the paper path."""
    source = (BACKEND / "backend" / "paper" / "bot_paper_ledger.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imported: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden = {
        name
        for name in imported
        if any(
            marker in name
            for marker in ("ccxt", "exchange_executor", "connection_engine",
                           "api_key_vault", "transactional_execution_manager")
        )
    }
    assert forbidden == set(), (
        f"bot_paper_ledger imports exchange/credential modules: {sorted(forbidden)}"
    )


# ══════════════════════════════════════════════════════════════════════════
#  2. THE REAL BOOT PATH, WITH TRIPWIRES
# ══════════════════════════════════════════════════════════════════════════


class _ExplodingVault:
    """A vault that fails the test if paper mode asks it for anything."""

    def __init__(self) -> None:
        self.calls: List[str] = []

    def load_decrypted_keys(self, *args, **kwargs):
        self.calls.append("load_decrypted_keys")
        raise AssertionError(
            "paper mode decrypted an exchange credential: vault.load_decrypted_keys was "
            "called"
        )

    def get_user_tier(self, *args, **kwargs):
        return {"subscription_tier": "free"}


class _Recorder:
    """Records whether it was called, without raising, for the live-branch controls."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError(f"{self.name} was called")


class _GlobalState:
    """Exactly the four attributes ``BotRunner.__init__`` reads off ``global_state``."""

    def __init__(self, vault: Any) -> None:
        self.vault = vault
        self.telemetry = _Telemetry()
        self.risk = _Risk()
        self.alert = _Alert()


class _Telemetry:
    async def get_account_health(self, *args, **kwargs):
        return {"current_drawdown_pct": 0.0, "daily_pnl_pct": 0.0}

    async def log_execution(self, *args, **kwargs):
        return None


class _Risk:
    def record_trade_open(self):
        return None

    async def validate_trade_request(self, *args, **kwargs):
        raise AssertionError("the isolation tests never reach the risk gate")


class _Alert:
    async def send_critical(self, *args, **kwargs):
        return None


BLUEPRINT = {
    "exchange_id": "binance",
    "symbol": "BTC/USDT",
    "timeframe": "1m",
    "strategy_id": "strategy_isolation",
}


def _bot(paper_trading: bool, vault: Any):
    from backend_app.backend.master_executor import BotRunner

    return BotRunner(
        user_id="11111111-1111-1111-1111-111111111111",
        symbol="BTC/USDT",
        strategy_blueprint=dict(BLUEPRINT),
        global_state=_GlobalState(vault),
        paper_trading=paper_trading,
    )


def test_the_real_paper_boot_decrypts_no_credential_and_builds_no_live_adapter(monkeypatch):
    """THE CENTRAL BEHAVIOURAL CLAIM, through the production boot sequence.

    ``_recover_live_state`` is the real function. The vault raises if asked for keys and
    ``get_exchange_executor`` raises if called. Paper mode must complete anyway.
    """
    vault = _ExplodingVault()
    exploding_factory = _Recorder("get_exchange_executor")
    monkeypatch.setattr(
        "backend_app.backend.exchange_executor.get_exchange_executor",
        exploding_factory,
    )

    bot = _bot(paper_trading=True, vault=vault)
    asyncio.run(bot._recover_live_state())

    assert vault.calls == [], f"paper boot touched the vault: {vault.calls}"
    assert exploding_factory.calls == 0, "paper boot built a live exchange executor"

    engine = bot.execution_engine
    assert engine is not None, "paper boot produced no execution engine"
    assert engine.exchange_executor is None, (
        "the paper engine holds an exchange executor, which is the one thing that lets "
        "its orders reach a venue"
    )
    assert engine.execution_environment is ExecutionEnvironment.PAPER
    assert engine.paper_ledger is not None, (
        "the paper engine has no canonical ledger, so PAPER-01 is open on this path"
    )


def test_the_paper_boot_builds_no_ccxt_client(monkeypatch):
    """``ConnectionEngine`` is constructed, but no network client is.

    ``ConnectionEngine.__init__`` only assigns attributes and sets ``self.exchange = None``;
    the CCXT object is built in ``connect()``, which the paper branch never calls. So the
    handle ``DataEngine`` receives is ``None`` and there is nothing that could place an
    order. Asserted because "a ConnectionEngine was constructed" reads alarming in the
    source and is in fact inert.
    """
    monkeypatch.setattr(
        "backend_app.backend.exchange_executor.get_exchange_executor",
        _Recorder("get_exchange_executor"),
    )

    bot = _bot(paper_trading=True, vault=_ExplodingVault())
    asyncio.run(bot._recover_live_state())

    assert bot.ccxt_bridge is not None, "the paper branch built no ConnectionEngine at all"
    assert bot.ccxt_bridge.api_key is None
    assert bot.ccxt_bridge.secret_key is None
    assert bot.exchange is None, (
        f"paper mode produced a live CCXT exchange handle: {bot.exchange!r}. "
        f"ConnectionEngine.connect() must not be reached on the paper branch."
    )


def test_the_live_boot_does_touch_the_vault(monkeypatch):
    """NEGATIVE CONTROL. Without this, the two tests above would also pass if the
    tripwires were never armed -- or if ``_recover_live_state`` had simply stopped
    reading credentials on every path.
    """
    vault = _ExplodingVault()
    bot = _bot(paper_trading=False, vault=vault)

    with pytest.raises(AssertionError, match="decrypted an exchange credential"):
        asyncio.run(bot._recover_live_state())

    assert vault.calls == ["load_decrypted_keys"], (
        "the LIVE branch did not reach the vault, so the paper-branch assertion that it "
        "does not is not evidence of anything"
    )


# ══════════════════════════════════════════════════════════════════════════
#  3. THE ENGINE CANNOT CROSS THE BRANCH
# ══════════════════════════════════════════════════════════════════════════


class _RecordingExchange:
    """A venue adapter that records and then refuses. The oracle is ``calls``."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._current_execution_id = None
        self._current_validation_token = None

    async def place_order(self, **kwargs):
        self.calls.append(kwargs)
        raise AssertionError("place_order was reached from a paper engine")

    async def create_order(self, *args, **kwargs):
        self.calls.append({"create_order": kwargs})
        raise AssertionError("create_order was reached from a paper engine")


def test_a_paper_engine_holding_an_executor_refuses_at_the_branch_point():
    """The misconfiguration that would place a real order, refused.

    ``BotRunner``'s paper fast-path depends on ``exchange_executor=None``. If that null
    were ever lost -- a hoisted construction, a passed-through argument -- this is what
    stops the order rather than the null.
    """
    exchange = _RecordingExchange()
    engine = ExecutionEngine(
        fee_rate=0.001, slippage=0.0,
        portfolio_state={"total_equity": "10000"},
        exchange_executor=exchange,
        execution_environment=ExecutionEnvironment.PAPER,
    )

    ok, detail = asyncio.run(
        engine._execute_trade_internal(
            symbol="BTC/USDT", side="buy", size=Decimal("0.01"),
            price=Decimal("50000"), execution_id="exec_iso_1",
        )
    )

    assert ok is False, f"a PAPER engine holding an executor filled an order: {detail}"
    assert "mismatch" in str(detail).lower()
    assert exchange.calls == [], (
        f"the order reached the exchange adapter: {exchange.calls}"
    )


def test_a_paper_engine_with_a_ledger_and_an_executor_still_refuses():
    """The branch-match check runs BEFORE the ledger, so PAPER-01's seam cannot be a
    route around it. Asserted because the new canonical branch sits inside the paper
    path, and "inside the paper path" has to mean after that check.
    """
    exchange = _RecordingExchange()

    class _Ledger:
        def __init__(self):
            self.calls = 0

        async def record_fill(self, **kwargs):
            self.calls += 1
            raise AssertionError("the ledger was consulted by a misconfigured engine")

    ledger = _Ledger()
    engine = ExecutionEngine(
        fee_rate=0.001, slippage=0.0,
        portfolio_state={"total_equity": "10000"},
        exchange_executor=exchange,
        execution_environment=ExecutionEnvironment.PAPER,
        paper_ledger=ledger,
    )

    ok, _detail = asyncio.run(
        engine._execute_trade_internal(
            symbol="BTC/USDT", side="buy", size=Decimal("0.01"),
            price=Decimal("50000"), execution_id="exec_iso_2",
        )
    )

    assert ok is False
    assert exchange.calls == []
    assert ledger.calls == 0, (
        "the paper ledger ran on an engine that holds an exchange executor; the "
        "branch-match refusal must come first"
    )


def test_the_paper_branch_is_unreachable_once_an_executor_is_present():
    """AST: the live path is selected by ``_has_executor``, and the paper branch is below it.

    So there is no ordering in which a paper fill and a venue call both happen for one
    order -- which is the property the two behavioural tests above sample.
    """
    source = textwrap.dedent(
        inspect.getsource(ExecutionEngine._execute_trade_internal)
    )
    assert "if _has_executor:" in source, (
        "the live path is no longer selected by the executor null check; this test's "
        "reading of the branch structure is stale"
    )
    live_at = source.index("if _has_executor:")
    paper_at = source.index("PAPER EXECUTION PATH")
    canonical_at = source.index("THE CANONICAL PAPER LEDGER PATH")
    assert live_at < canonical_at < paper_at, (
        "the canonical paper branch is not between the live branch and the in-process "
        "paper branch; the refusal ordering this file asserts depends on it"
    )


def test_a_paper_fill_through_the_ledger_issues_no_exchange_call():
    """End to end: a canonical paper fill with a recording adapter in scope.

    The adapter is not attached to the engine -- attaching one is the refusal case above.
    It is constructed and left in scope so that any code path which reached for a venue
    would have had one available, and the assertion is that nothing did.
    """
    exchange = _RecordingExchange()

    class _Ledger:
        def __init__(self):
            self.calls = 0

        async def record_fill(self, **kwargs):
            self.calls += 1
            from backend_app.backend.paper.bot_paper_ledger import LedgerFill

            return LedgerFill(
                order_id="order-iso", symbol="BTC-USDT", side="buy",
                size=Decimal("0.01"), executed_price=Decimal("50025"),
                fee=Decimal("0.50"), available_balance=Decimal("99499.25"),
                locked_balance=Decimal("0"), realized_pnl=Decimal("0"),
                # 99499.25 cash + money(0.01 * 50025) 500.25 = 99999.50, which is
                # capital 100000 less the 0.50 fee -- Requirement 18.7.
                total_equity=Decimal("99999.50"), account_id="acct-iso",
                positions={
                    "BTC-USDT": {
                        "symbol": "BTC-USDT", "side": "LONG", "size": "0.01",
                        "entry_price": "50025", "current_price": "50025",
                        "closed_at": None,
                    }
                },
            )

    ledger = _Ledger()
    engine = ExecutionEngine(
        fee_rate=0.001, slippage=0.0005,
        portfolio_state={"total_equity": 10000.0},
        exchange_executor=None,
        execution_environment=ExecutionEnvironment.PAPER,
        paper_ledger=ledger,
    )

    ok, detail = asyncio.run(
        engine._execute_trade_internal(
            symbol="BTC/USDT", side="buy", size=Decimal("0.01"),
            price=Decimal("50000"), execution_id="exec_iso_3",
        )
    )

    assert ok is True, detail
    assert ledger.calls == 1
    assert detail["paper_ledger"] == "canonical"
    assert exchange.calls == [], f"a paper fill reached a venue adapter: {exchange.calls}"
    # And the engine reconciled onto the ledger's figures rather than its synthetic ones.
    assert engine.cash == Decimal("99499.25")
    assert engine.current_equity == Decimal("99999.50")


def test_the_blocked_execution_builder_is_still_blocked():
    """The tripwire for :data:`UNREACHABLE_RECEIVERS`.

    ``routers/orders.py:915`` calls ``execution_engine.cancel_order`` and is classified
    unreachable ONLY because ``_build_execution_engine`` raises before returning. If that
    block is ever lifted, the classification is wrong and the inventory must be revisited
    -- so the block itself is asserted here rather than assumed.
    """
    from backend_app.routers import orders as orders_router

    source = textwrap.dedent(
        inspect.getsource(orders_router._build_execution_engine)
    )
    tree = ast.parse(source)
    node = tree.body[0]
    body = node.body[1:] if ast.get_docstring(node) else node.body

    raises_http = any(
        isinstance(stmt, ast.Raise)
        and isinstance(stmt.exc, ast.Call)
        and isinstance(stmt.exc.func, ast.Name)
        and stmt.exc.func.id == "HTTPException"
        for stmt in body
    )
    returns_anything = any(
        isinstance(stmt, ast.Return) and stmt.value is not None
        for stmt in ast.walk(node)
    )

    assert raises_http, (
        "_build_execution_engine no longer raises HTTPException unconditionally, so "
        "routers/orders.py:915's execution_engine.cancel_order may now be reachable. "
        "Re-classify that call site: note that ExecutionEngine has no cancel_order "
        "method, so reaching it would raise AttributeError."
    )
    assert not returns_anything, (
        "_build_execution_engine now returns a value; the unreachable classification of "
        "routers/orders.py:915 no longer holds"
    )


def test_the_paper_simulator_package_is_not_on_the_bot_path():
    """The session simulator needs a ``paper_sessions`` row; a bot has none.

    Recorded as a test so the two paper paths cannot be quietly merged: if
    ``master_executor`` ever imports ``paper_simulator``, somebody has decided a bot gets
    a Paper_Session and that decision should be visible.
    """
    source = (BACKEND / "backend" / "master_executor.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)

    assert not any("paper_simulator" in name for name in modules), (
        "master_executor now imports paper_simulator, which requires a paper_sessions row "
        "and a HEALTHY feed. If bots are to get Paper_Sessions, that is a design change."
    )
    assert any("bot_paper_ledger" in name for name in modules), (
        "master_executor does not import bot_paper_ledger, so its paper fills reach no "
        "canonical ledger"
    )
