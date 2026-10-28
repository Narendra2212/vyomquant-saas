"""DEPLOY-01: ``order_timeout_seconds`` is really applied to the venue call.

WHY THIS FIELD COULD BE ENFORCED WHEN THE OTHER TWO STILL CANNOT
----------------------------------------------------------------
DEPLOY-01 published five per-deployment execution-config fields. Two
(``max_order_notional``, ``max_open_positions``) were wired to
``master_executor.evaluate_deployment_execution_limits``. The remaining three were refused
at deploy time rather than stored-and-ignored, which was the right call while nothing read
them.

``order_timeout_seconds`` is now read, and the reason it is the one of the three that
could be is worth stating, because the other two were not skipped for lack of effort:

* ``retry_policy`` would CONTRADICT RT-17, an implemented control. ``place_order``'s own
  except block refuses to report an ambiguous failure as a rejection, precisely so the
  strategy cannot "roll back, re-enter on the next tick (new price => new execution id)
  and double the position", and it logs "do NOT resubmit". That parenthesis is the real
  obstacle: ``generate_execution_id`` folds PRICE into the hash, so a retry at a re-quoted
  price is a different execution id and the idempotency layer does not recognise it as the
  same order. Enforcing a retry policy needs a price-stable retry key first.

* ``slippage_tolerance_bps`` has nothing to compare synchronously on the live path.
  ``place_order``'s docstring says it returns ``'pending'`` and to wait for the event
  system for FILLED; ``avg_price`` is ``None`` whenever ccxt omits ``average``, and the
  live result then substitutes the REQUESTED price, with its own comment saying so. A
  tolerance check against that field would report ZERO slippage in exactly the case it
  should flag.

* A TIMEOUT, by contrast, composes with RT-17 instead of fighting it. It bounds how long
  the loop WAITS and makes no claim about what happened at the venue, and RT-17 already
  classifies ``asyncio.TimeoutError`` as ambiguous. That is the property this file pins:
  a timed-out order must come back ``"unknown"``, never ``"rejected"``.

THE DEFECT THE TIMEOUT FIXES IS REAL INDEPENDENTLY OF DEPLOY-01. The ccxt client is
constructed with ``{"apiKey", "secret", "enableRateLimit"}`` and no ``timeout`` key, so
before this there was NO timeout on the venue call at all -- a hung response blocked the
awaiting ``BotRunner`` tick for as long as the socket allowed.
"""

from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from decimal import Decimal
from typing import Any

import pytest

from backend_app.backend import deployment_binding as db
from backend_app.backend import exchange_executor as ee


# ──────────────────────────────────────────────────────────────────────────────
# Source-level: the field is published as enforced, and the plumbing exists.
# These need no venue and no event loop.
# ──────────────────────────────────────────────────────────────────────────────


def test_order_timeout_seconds_is_published_as_enforced() -> None:
    assert "order_timeout_seconds" in db.ENFORCED_EXECUTION_CONFIG_FIELDS
    assert "order_timeout_seconds" not in db.UNENFORCED_EXECUTION_CONFIG_FIELDS


def test_a_deployment_may_now_set_it() -> None:
    """The deploy-time refusal is gone for this field.

    It used to say "and only for this field". ``slippage_tolerance_bps`` has since become
    enforceable too, so the still-refused set is read from the published tuple rather
    than listed here -- a hard-coded list makes this test fail every time a field is
    correctly enforced, which is the wrong direction for a guard to point.
    """
    assert db.normalise_execution_config({"order_timeout_seconds": 30}) == {
        "order_timeout_seconds": 30
    }
    assert db.UNENFORCED_EXECUTION_CONFIG_FIELDS, (
        "if nothing is unenforced any more, this loop proves nothing and the refusal "
        "path needs its own dedicated test instead"
    )
    for still_refused in db.UNENFORCED_EXECUTION_CONFIG_FIELDS:
        with pytest.raises(db.DeployRejected) as exc:
            db.normalise_execution_config({still_refused: 5})
        assert exc.value.code == "EXECUTION_CONFIG_NOT_ENFORCED"


def test_place_order_accepts_a_timeout_parameter() -> None:
    signature = inspect.signature(ee.ExchangeExecutor.place_order)
    assert "timeout_seconds" in signature.parameters, (
        "place_order must take the timeout explicitly rather than through **kwargs, so a "
        "caller that misspells it fails loudly instead of silently not timing out"
    )
    assert signature.parameters["timeout_seconds"].default is None, (
        "the default must be None -- no timeout -- or every deployment that never set the "
        "field would silently acquire one"
    )


def test_execute_with_idempotency_takes_and_stores_the_timeout() -> None:
    from backend_app.core.execution_engine import ExecutionEngine

    signature = inspect.signature(ExecutionEngine.execute_with_idempotency)
    assert "order_timeout_seconds" in signature.parameters
    assert signature.parameters["order_timeout_seconds"].default is None


def test_the_bot_runner_passes_the_deployments_value_at_every_call_site() -> None:
    """Both ``execute_with_idempotency`` call sites, not just the opening one.

    An AST walk rather than a substring count: the close path (RT-21) is the order that
    most needs a bounded wait, since it is how a deployment stops being exposed, and a
    timeout applied only to the open would be backwards. Counting text would also be
    satisfied by a mention in a comment.
    """
    import ast
    import pathlib

    source = pathlib.Path(
        inspect.getsourcefile(__import__("backend_app.backend.master_executor",
                                         fromlist=["x"]))
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute_with_idempotency"
    ]
    assert len(calls) >= 2, (
        f"expected at least the open and the RT-21 close call site, found {len(calls)}"
    )
    for call in calls:
        kwargs = {kw.arg for kw in call.keywords if kw.arg}
        assert "order_timeout_seconds" in kwargs, (
            "an execute_with_idempotency call site does not pass "
            f"order_timeout_seconds (line {call.lineno}); a deployment's timeout would "
            "not apply to the order placed there"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Behavioural: the timeout fires, and RT-17 adjudicates it as UNKNOWN.
# ──────────────────────────────────────────────────────────────────────────────


#: The harness RT-17 and TRADE-04 already use for exactly this call. Reused rather than
#: rebuilt: ``build_executor`` wires every attribute ``place_order`` touches (normalizer,
#: rate limiter, validation token, credential redaction), and a second hand-built executor
#: is how two tests of one function come to disagree about what it does. A first attempt
#: here DID hand-build one and failed on a missing ``normalizer`` -- the harness exists
#: because that is fiddly.
from tests.test_trade04_live_order_gate_at_venue_boundary import (  # noqa: E402
    RecordingExchange,
    build_executor,
)

#: TRADE-04's own gate refuses every venue write unless the platform is enabled, and it
#: raises rather than returning, so without this the venue is never reached and a timeout
#: test would pass for the wrong reason. Same mark RT-17 carries.
pytestmark = pytest.mark.usefixtures("live_platform_enabled")


class _HangingExchange(RecordingExchange):
    """A venue that takes the order and never answers."""

    def __init__(self, hang_for: float = 30.0) -> None:
        super().__init__()
        self.hang_for = hang_for

    async def create_order(self, *a: Any, **k: Any) -> dict:
        self.calls.append(a)
        await asyncio.sleep(self.hang_for)
        return {"id": "never-returned"}


class _PromptExchange(RecordingExchange):
    async def create_order(self, *a: Any, **k: Any) -> dict:
        self.calls.append(a)
        return {
            "id": "ord-1",
            "filled": 1.0,
            "remaining": 0.0,
            "average": 100.0,
            "status": "closed",
        }


SYMBOL_UNDER_TEST = "BTC/USDT"
SIZE_UNDER_TEST = Decimal("0.001")


@pytest.fixture(autouse=True)
def _gates_stood_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand down the two gates ``place_order`` runs BEFORE the venue call.

    Patched on their SOURCE modules, not on ``exchange_executor``: ``place_order`` imports
    each one INSIDE the function body (``from backend_app.core.live_execution_gate import
    require_live_authorization``, ``from backend_app.core.global_safety import
    enforce_kill_switch``), so the name is resolved at call time and a patch on the
    ``exchange_executor`` namespace never takes effect. That mistake is not silent -- the
    real TRADE-04 gate raises ``LIVE_EXECUTION_NOT_AUTHORIZED`` -- which is how it was
    found here.

    ``require_live_authorization`` is SYNC and ``enforce_kill_switch`` is ASYNC; stubbed
    with matching shapes rather than one convenient coroutine.

    NEITHER GUARANTEE IS WEAKENED. TRADE-04's gate is the subject of
    ``tests/test_trade04_live_order_gate_at_venue_boundary.py`` and KILL-03's of its own
    suite; minting a signed ``LiveOrderAuthorization`` here would duplicate that setup to
    test something else. The anti-bypass token is NOT stubbed -- see ``_place`` -- because
    it is cheap to satisfy honestly and it binds the symbol and size of the order actually
    placed.
    """
    from backend_app.core import global_safety, live_execution_gate

    def _authorised(*_a: Any, **_k: Any) -> None:
        return None

    async def _not_halted(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(
        live_execution_gate, "require_live_authorization", _authorised, raising=True
    )
    monkeypatch.setattr(global_safety, "enforce_kill_switch", _not_halted, raising=True)


def _place(exchange: Any, **extra: Any):
    """Drive the real ``place_order`` against a scripted venue.

    The anti-bypass token is MINTED, not stubbed out. ``verify_and_consume_token`` refuses
    any order that does not carry one ("Bypass attempt detected"), and the engine issues it
    with ``generate_validation_token(execution_id, symbol, size)`` immediately before
    calling the executor (``execution_engine.py``). Doing the same here keeps that control
    genuinely in force for these tests instead of disabling it -- the size and symbol are
    bound into the token, so the call below could not place a DIFFERENT order than the one
    authorised.
    """
    from backend_app.core.global_safety import generate_validation_token

    executor = build_executor(exchange)
    executor._api_key, executor._api_secret, executor._password = "k", "s", None

    execution_id = f"exec_timeout_{uuid.uuid4().hex[:12]}"
    executor._current_execution_id = execution_id
    executor._current_validation_token = generate_validation_token(
        execution_id, SYMBOL_UNDER_TEST, SIZE_UNDER_TEST
    )

    return asyncio.run(
        executor.place_order(
            symbol=SYMBOL_UNDER_TEST,
            side=ee.OrderSide.BUY,
            order_type=ee.OrderType.MARKET,
            size=SIZE_UNDER_TEST,
            price=None,
            **extra,
        )
    )


def test_a_hanging_venue_call_is_abandoned_at_the_timeout() -> None:
    """And the whole point: it comes back UNKNOWN, not rejected.

    ``"rejected"`` would let the strategy roll back and re-enter on the next tick at a new
    price -- a new execution id, so the idempotency layer would NOT stop it -- and double
    the position. That is the exact failure RT-17 exists to prevent, and a timeout is the
    newest way to arrive at it.
    """
    exchange = _HangingExchange(hang_for=30.0)

    started = time.monotonic()
    result = _place(exchange, timeout_seconds=0.25)
    elapsed = time.monotonic() - started

    assert len(exchange.calls) == 1, "the venue must be called exactly once"
    assert elapsed < 10, (
        f"the call took {elapsed:.2f}s against a 0.25s timeout, so the timeout did not "
        "apply -- the venue was told to hang for 30s"
    )
    assert result.success is False
    assert result.status == "unknown", (
        f"a timed-out submission must be UNKNOWN, not {result.status!r}. RT-17: the request "
        "may have reached the venue, so reporting a rejection invites a double position."
    )
    assert result.error_code == "SUBMISSION_UNKNOWN"
    assert result.exchange_order_id is None
    assert result.avg_price is None


def test_the_venue_is_not_called_again_after_a_timeout() -> None:
    """No implicit retry crept in alongside the timeout.

    ``retry_policy`` is still a refused field, and this is the assertion that keeps the
    timeout from quietly becoming one.
    """
    exchange = _HangingExchange(hang_for=30.0)

    _place(exchange, timeout_seconds=0.25)

    assert len(exchange.calls) == 1, (
        f"the venue was called {len(exchange.calls)} times after one timeout -- "
        "resubmitting an order whose fate is unknown is the RT-17 hazard itself"
    )


def test_no_timeout_is_the_default_and_a_prompt_order_is_unaffected() -> None:
    """A deployment that never set the field behaves exactly as it did before."""
    exchange = _PromptExchange()

    result = _place(exchange)

    assert len(exchange.calls) == 1
    assert result.status != "unknown"
    assert result.error_code != "SUBMISSION_UNKNOWN"


def test_a_generous_timeout_does_not_interfere() -> None:
    exchange = _PromptExchange()

    result = _place(exchange, timeout_seconds=30)

    assert len(exchange.calls) == 1
    assert result.status != "unknown"
    assert result.error_code != "SUBMISSION_UNKNOWN"


def test_without_a_timeout_a_slow_venue_is_waited_out() -> None:
    """The negative control: the timeout is what abandons the call, not something else.

    Without this, ``test_a_hanging_venue_call_is_abandoned_at_the_timeout`` could pass for
    an unrelated reason -- any error on the venue call produces ``unknown`` -- and the
    assertion would not actually be about the timeout.

    So the same slow venue is given NO timeout and must be waited out and SUCCEED. The
    delay is short on purpose: the point is "waited rather than abandoned", and proving
    that does not require sitting through 30 seconds.
    """
    delay = 1.5
    exchange = _HangingExchange(hang_for=delay)
    # Answer after the sleep instead of hanging forever, so "waited it out" is observable.
    exchange.create_order = _slow_then_fill(exchange, delay)

    started = time.monotonic()
    result = _place(exchange)
    elapsed = time.monotonic() - started

    assert elapsed >= delay, (
        f"the call returned in {elapsed:.2f}s against a {delay}s venue delay, so something "
        "other than the timeout is cutting the venue call short"
    )
    assert result.status != "unknown", (
        "with no timeout configured a slow venue must be waited out, not abandoned"
    )
    assert result.error_code != "SUBMISSION_UNKNOWN"


def _slow_then_fill(exchange: Any, delay: float):
    async def create_order(*a: Any, **k: Any) -> dict:
        exchange.calls.append(a)
        await asyncio.sleep(delay)
        return {
            "id": "slow-1",
            "filled": 1.0,
            "remaining": 0.0,
            "average": 100.0,
            "status": "closed",
        }

    return create_order
