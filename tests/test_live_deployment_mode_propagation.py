"""
tests/test_live_deployment_mode_propagation.py

A deployment bound as ``mode='live'`` must run as a LIVE bot, and a bot must never decide
its own mode from a default.

WHAT WAS WRONG
--------------
``BotRunner._recover_live_state`` chose between the paper and live boot paths like this::

    is_paper = self.blueprint.get("paper_trading", True)

and **nothing in the codebase ever wrote that key.** A repo-wide search for
``paper_trading`` found it at that single read site in ``backend_app/`` and nowhere in
``algo22-terminal/src/`` as a payload field. The default therefore won on every deployment.

So the whole live path was recorded and never run:

* ``deploy_version`` evaluated every Requirement 13 gate, checked the ``LIVE_TRADING``
  entitlement, required and resolved an exchange account, and persisted
  ``strategy_deployments.mode = 'live'``;
* ``strategies.status`` and the deployment row both moved to ``'running'`` and the version
  lifecycle to ``RUNNING``;
* the UI showed the strategy running, live, on the bound account;
* and the runtime took the paper branch — ``ExecutionEngine(exchange_executor=None)``,
  returning at the top of ``_recover_live_state`` **before the credentials were even
  decrypted**.

It failed SAFE: no real order was possible, so no money moved. But a trader who believes
they are live is not safe, and a platform that reports a live deployment it did not perform
is making a claim it cannot support.

WHAT THE FIX IS, AND WHY IT IS A REQUIRED ARGUMENT
--------------------------------------------------
``paper_trading`` is now a **required keyword-only** parameter of both
``FleetManager.start_bot`` and ``BotRunner``, derived at the deployment site from the
binding's own ``mode``. It is not read from the blueprint, and there is no default.

That shape is the point. A default is what made this defect invisible for the entire life of
the live path: the value was absent, the default was taken, and nothing anywhere said so. A
required argument cannot be absent — a caller that forgets it is a ``TypeError`` at the call
site, not a silently paper bot reporting itself live. This is the same reasoning
``ExecutionEngine.__init__`` already applies to ``portfolio_state``, which refuses rather
than inventing capital.

WHAT IS ASSERTED
----------------
1. Neither ``BotRunner`` nor ``start_bot`` has a default for ``paper_trading``.
2. ``master_executor`` no longer derives the mode from the blueprint.
3. The paper boot path never reads exchange credentials; the live boot path does.
4. Every ``start_bot`` call site in the repository passes the flag explicitly.
5. ``deploy_version`` derives it from ``binding.mode`` — so ``mode='live'`` produces a live
   bot and ``mode='paper'`` a paper one.
6. The two ungated legacy call sites force paper.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

REPO_ROOT = Path(__file__).resolve().parents[1]
MASTER_EXECUTOR = REPO_ROOT / "backend_app" / "backend" / "master_executor.py"
FLEET_MANAGER = REPO_ROOT / "backend_app" / "backend" / "fleet_manager.py"
STRATEGY_SERVICE = REPO_ROOT / "backend_app" / "backend" / "strategy_service.py"
STRATEGIES_ROUTER = REPO_ROOT / "backend_app" / "routers" / "strategies.py"

#: The flag that decides whether a bot can reach a real exchange.
FLAG = "paper_trading"


# ---------------------------------------------------------------------------
# 1 + 2. The flag is required, and is not taken from the blueprint
# ---------------------------------------------------------------------------

def test_bot_runner_requires_the_mode_and_does_not_default_it():
    """A default is what hid the defect, so there must not be one."""
    from backend_app.backend.master_executor import BotRunner

    parameters = inspect.signature(BotRunner.__init__).parameters
    assert FLAG in parameters, (
        f"BotRunner.__init__ does not accept {FLAG!r}. It must be told its mode by the "
        f"caller that resolved the deployment binding, not infer one."
    )
    assert parameters[FLAG].default is inspect.Parameter.empty, (
        f"BotRunner.__init__'s {FLAG!r} has default {parameters[FLAG].default!r}. A "
        f"default is exactly how `blueprint.get('paper_trading', True)` made every live "
        f"deployment run as paper without anything saying so."
    )
    assert parameters[FLAG].kind is inspect.Parameter.KEYWORD_ONLY, (
        f"{FLAG!r} must be keyword-only so it cannot be supplied positionally by accident "
        f"at a call site that meant something else"
    )


def test_start_bot_requires_the_mode_and_does_not_default_it():
    from backend_app.backend.fleet_manager import FleetManager

    parameters = inspect.signature(FleetManager.start_bot).parameters
    assert FLAG in parameters, (
        f"FleetManager.start_bot does not accept {FLAG!r}, so the deployment site has no "
        f"way to tell the runtime which mode it bound"
    )
    assert parameters[FLAG].default is inspect.Parameter.empty, (
        f"start_bot's {FLAG!r} has a default ({parameters[FLAG].default!r}); a caller that "
        f"forgets the mode must be a TypeError, not a silently paper bot"
    )
    assert parameters[FLAG].kind is inspect.Parameter.KEYWORD_ONLY


def test_the_runtime_no_longer_reads_the_mode_out_of_the_blueprint():
    """The defective read, pinned out of existence.

    ``blueprint.get("paper_trading", ...)`` is the exact expression that decided the mode
    from a key nothing wrote. Reintroducing it would restore the defect even with the
    parameter in place, because the parameter would then be shadowed by a blueprint value.
    """
    # Read from the AST, not from the text. The constructor documents the defective
    # expression verbatim so a reader learns what went wrong, and a substring search would
    # make this guard permanently red for that documentation — the same false positive the
    # Lua prefix guard had to exclude itself from.
    tree = ast.parse(MASTER_EXECUTOR.read_text(encoding="utf-8", errors="replace"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == FLAG
    ]
    assert not offenders, (
        f"master_executor.py reads {FLAG!r} out of a mapping at line(s) {offenders}. "
        f"Nothing writes that key — that read, with its `True` default, is what made every "
        f"live deployment run as paper. The mode must come from the required parameter."
    )


# ---------------------------------------------------------------------------
# 3. Paper never reads credentials; live does
# ---------------------------------------------------------------------------

class _ExplodingVault:
    """A credential vault that fails the test if it is consulted.

    The paper branch returned before ``load_decrypted_keys`` on purpose — "skip ALL real
    CCXT calls" — and that property is worth asserting directly rather than inferring from
    which engine was built: a paper bot that decrypts live API keys has already done the
    thing paper mode exists to avoid.
    """

    def __init__(self) -> None:
        self.calls: List[tuple] = []

    def load_decrypted_keys(self, user_id, exchange_id):
        self.calls.append((user_id, exchange_id))
        raise AssertionError(
            "the paper boot path read exchange credentials; it must return before any "
            "credential access"
        )

    def get_user_tier(self, user_id):
        return {"subscription_tier": "pro"}


class _State:
    """The ``global_state`` singletons ``BotRunner`` pulls off app_state."""

    def __init__(self, vault: Any) -> None:
        self.vault = vault
        self.telemetry = _Noop()
        self.risk = _Noop()
        self.alert = _Noop()


class _Noop:
    def __getattr__(self, _name):
        async def _async_noop(*_a, **_k):
            return None
        return _async_noop


BLUEPRINT: Dict[str, Any] = {
    "exchange_id": "binance",
    "timeframe": "1m",
    "strategy_id": "test_mode_propagation",
    "indicators": [],
}


def _runner(paper_trading: bool, vault: Any):
    from backend_app.backend.master_executor import BotRunner
    from tests.fakes_market_data import (FakePublicExchange, make_closed_ohlcv,
                                         public_factory)

    # RT-01: the paper boot path connects to REAL public market data (the DEV_MODE mock is no
    # longer used for it), so the test supplies an offline stand-in instead of the network.
    return BotRunner(
        user_id="11111111-1111-4111-8111-111111111111",
        symbol="BTC/USDT",
        strategy_blueprint=dict(BLUEPRINT),
        global_state=_State(vault),
        paper_trading=paper_trading,
        public_market_data_factory=public_factory(
            FakePublicExchange(make_closed_ohlcv(60, "1m"))
        ),
    )


def test_a_paper_bot_never_reads_exchange_credentials():
    vault = _ExplodingVault()
    bot = _runner(paper_trading=True, vault=vault)

    asyncio.run(bot._recover_live_state())

    assert vault.calls == [], (
        f"the paper boot path consulted the credential vault: {vault.calls!r}"
    )
    assert bot.execution_engine is not None, "the paper bot built no execution engine"
    assert getattr(bot.execution_engine, "exchange_executor", "missing") is None, (
        "a paper bot's ExecutionEngine must carry no exchange_executor — that object being "
        "None is what makes `_execute_trade_internal` take the paper branch, and it is the "
        "only thing standing between a paper strategy and a real order"
    )


def test_a_live_bot_does_read_exchange_credentials():
    """The regression half.

    A fix that forced paper everywhere would satisfy the test above and leave live trading
    exactly as broken as it was. This is the assertion that distinguishes "the mode is
    propagated" from "the mode is always paper": the live branch must get as far as the
    credential read.
    """
    vault = _ExplodingVault()
    bot = _runner(paper_trading=False, vault=vault)

    # `_ExplodingVault` raises on access, so reaching it is observable as that error. What
    # is under test is that the LIVE branch is taken at all.
    with pytest.raises(AssertionError, match="paper boot path read exchange credentials"):
        asyncio.run(bot._recover_live_state())

    assert vault.calls, (
        "a live bot did not reach the credential read, so it took the paper branch. This "
        "is the defect: mode='live' recorded in the database, paper runtime in process."
    )
    assert vault.calls[0][1] == "binance", (
        f"the live branch read credentials for the wrong venue: {vault.calls!r}"
    )


# ---------------------------------------------------------------------------
# 4. Every call site passes the flag
# ---------------------------------------------------------------------------

def _start_bot_calls(path: Path) -> List[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Attribute) and node.func.attr == "start_bot")
            or (isinstance(node.func, ast.Name) and node.func.id == "start_bot")
        )
    ]


TEE_MAIN = REPO_ROOT / "backend_app" / "tee" / "main.py"
COMMAND_WORKER = REPO_ROOT / "backend_app" / "workers" / "command_worker.py"

#: Every module that starts a bot. Enumerated so a NEW one has to be added here, which is
#: the moment to ask what mode it deploys in.
#:
#: There were FIVE, not the two the original audit of this defect found. The sweep below is
#: what turned them up: besides `deploy_version`, `strategy_service` has a second call in
#: `deploy_strategy`, and the TEE path (`tee/main.py`) and the Redis command consumer
#: (`workers/command_worker.py`) both start bots from a published command. Any of them
#: defaulting the mode would have reproduced the defect on its own path.
#
#: RT-03: `routers/strategies.py` is no longer in this list because it no longer starts a bot --
#: its deploy and resume handlers delegate to `StrategyService.deploy_version`, whose own call
#: site is covered here. tests/test_rt03_legacy_deploy_route_uses_canonical_deploy.py pins that
#: it stays that way.
CALL_SITE_FILES = (STRATEGY_SERVICE, TEE_MAIN, COMMAND_WORKER)


@pytest.mark.parametrize("path", CALL_SITE_FILES, ids=lambda p: p.name)
def test_every_start_bot_call_passes_the_mode_explicitly(path: Path):
    calls = _start_bot_calls(path)
    assert calls, (
        f"{path.name} no longer calls start_bot; this guard was written against its call "
        f"site(s) and needs repointing"
    )
    for call in calls:
        keywords = {kw.arg for kw in call.keywords if kw.arg is not None}
        assert FLAG in keywords, (
            f"{path.name}:{call.lineno} calls start_bot without {FLAG!r}. Every deployment "
            f"site must state the mode it bound; there is no default to fall back on."
        )


def test_no_other_module_starts_a_bot_without_declaring_the_mode():
    """A sweep, so a third deployment site cannot appear unguarded."""
    offenders: List[str] = []
    for root in (REPO_ROOT / "backend_app",):
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or path.name == "fleet_manager.py":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "start_bot" not in text:
                continue
            for call in _start_bot_calls(path):
                keywords = {kw.arg for kw in call.keywords if kw.arg is not None}
                if FLAG not in keywords:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{call.lineno}")

    assert not offenders, (
        "start_bot is called without an explicit mode at:\n  " + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# 5 + 6. The mode comes from the binding, and the legacy paths force paper
# ---------------------------------------------------------------------------

def test_deploy_version_derives_the_mode_from_the_binding():
    """The gated path must honour what it bound.

    ``deploy_version`` already resolves ``binding.mode`` through every Requirement 13 gate
    and persists it. The runtime flag has to be that same decision, not a second one.
    """
    calls = _start_bot_calls(STRATEGY_SERVICE)
    assert len(calls) == 2, (
        f"expected two start_bot calls in {STRATEGY_SERVICE.name} (deploy_version and "
        f"deploy_strategy), found {len(calls)}. A new deployment path needs its own mode "
        f"decision reviewed, not this count relaxed."
    )
    for call in calls:
        keyword = next(kw for kw in call.keywords if kw.arg == FLAG)
        rendered = ast.unparse(keyword.value)
        assert "MODE_LIVE" in rendered and "mode" in rendered, (
            f"{STRATEGY_SERVICE.name}:{call.lineno} passes {FLAG}={rendered!r}, which is "
            f"not derived from the mode this deployment resolved. The binding is where "
            f"live/paper was decided, account-gated and persisted; the runtime must not "
            f"make that decision a second time."
        )


@pytest.mark.parametrize("path", (TEE_MAIN, COMMAND_WORKER), ids=lambda p: p.name)
def test_the_command_driven_paths_take_the_mode_from_the_command(path: Path):
    """A published ``start_bot`` command must carry its own mode, and be refused without it.

    These two consume a Redis command rather than resolving a binding, so the mode has to
    travel in the payload. What they must NOT do is read it with a default — that is
    precisely the shape of the original defect, one layer out.
    """
    source = path.read_text(encoding="utf-8", errors="replace")
    calls = _start_bot_calls(path)
    assert calls, f"{path.name} no longer starts a bot; repoint this guard"

    for call in calls:
        keyword = next(kw for kw in call.keywords if kw.arg == FLAG)
        rendered = ast.unparse(keyword.value)
        assert f'payload[{FLAG!r}]' in rendered.replace('"', "'"), (
            f"{path.name}:{call.lineno} passes {FLAG}={rendered!r}. It must read the mode "
            f"the command stated, by subscript — a `.get(..., default)` here would "
            f"reintroduce the defaulted read this whole fix removes."
        )

    # And the absent case must be refused rather than defaulted.
    tree = ast.parse(source)
    defaulted = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == FLAG
    ]
    assert not defaulted, (
        f"{path.name} reads {FLAG!r} with .get() at line(s) {defaulted}; a command that "
        f"does not state its mode must be refused, not defaulted"
    )
    assert f'"{FLAG}" not in payload' in source or f"'{FLAG}' not in payload" in source, (
        f"{path.name} does not refuse a start_bot command that carries no {FLAG!r}. "
        f"Starting a bot in an unestablished mode is the defect; starting none is safe."
    )


def test_the_ungated_legacy_deploy_and_resume_force_paper():
    """Neither legacy path resolves a binding, so neither may deploy live.

    ``POST /api/strategies/{id}/deploy`` binds no account, no risk configuration and no mode
    and travels no gate — the frontend was deliberately re-pointed away from it for exactly
    that reason. The resume path reads a ``strategies`` row, which carries no mode column at
    all. A path that cannot establish that live was authorised must not run live.
    """
    # RT-03: the legacy deploy and resume handlers no longer call `fleet.start_bot` themselves;
    # they delegate to the canonical paper deploy (tests/test_rt03_*). Whatever call sites
    # REMAIN in this router must still force paper, which is what the loop below enforces.
    calls = _start_bot_calls(STRATEGIES_ROUTER)
    for call in calls:
        keyword = next(kw for kw in call.keywords if kw.arg == FLAG)
        rendered = ast.unparse(keyword.value)
        assert rendered == "True", (
            f"{STRATEGIES_ROUTER.name}:{call.lineno} passes {FLAG}={rendered!r}. This path "
            f"resolves no deployment binding, so it cannot establish that live trading was "
            f"authorised for this strategy, and must force paper."
        )
