"""
tests/test_execution_environment_guard.py

The live-path Execution_Environment guard of
``backend_app/backend/execution_environment.py``, and its installation at the top of
``backend_app/core/execution_engine.ExecutionEngine.execute_with_idempotency``.

WHAT IS ASSERTED, AND WHY IT IS ASSERTED THIS WAY
-------------------------------------------------
Requirement 13.9 says a ``PAPER`` order reaching the live order path is rejected *before
any exchange call*, with the rejection recorded in the Audit_Log and every balance left
alone. Requirement 13.10 says an order carrying no Execution_Environment, or one outside
the three values of Requirement 13.1, is rejected the same way and is **never defaulted to
LIVE**.

"Before any exchange call" is asserted on the **collaborators invoked**, not on log text:
the engine is given an exchange executor, a kill switch, a database session factory and a
strategy validator that each record every touch, and the assertion is that the recorder is
empty after the refusal. A test that grepped a log line would still pass if the order had
been placed and then logged.

``"live "`` - LIVE with a trailing space - is in the refused set on purpose. It is the
shape a hand-written config or a trimmed-wrong query parameter produces, and it is the case
where a guard that "helpfully" normalised its input would place a real order for something
it did not recognise.

**Validates: Requirements 13.1, 13.9, 13.10, 23.1**
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Dict, List
from uuid import uuid4

import pytest

from backend_app.backend.execution_environment import (
    EXECUTION_ENVIRONMENT_MISMATCH_ACTION,
    EXECUTION_ENVIRONMENTS,
    ExecutionEnvironment,
    ExecutionEnvironmentMismatch,
    UnresolvedExecutionEnvironment,
    assert_live_environment,
    parse_execution_environment,
)
from backend_app.core.execution_engine import ExecutionEngine

# Every value the guard must refuse. ``None`` and ``"live "`` are Requirement 13.10's two
# shapes - absent, and out of set - and the two enum members are Requirement 13.9's.
REFUSED_BY_13_9 = [ExecutionEnvironment.PAPER, ExecutionEnvironment.BACKTEST]
REFUSED_BY_13_10 = [None, "live ", "live", "Live", "", "SANDBOX", 0, True]


# ══════════════════════════════════════════════════════════════════════════
#  DOUBLES
# ══════════════════════════════════════════════════════════════════════════


class RecordingAuditLogger:
    """The audit seam. Records what would have been written, without Redis."""

    def __init__(self) -> None:
        self.records: List[Dict[str, Any]] = []

    async def log(self, action: Any, **fields: Any) -> Dict[str, Any]:
        entry = {"action": getattr(action, "value", action), **fields}
        self.records.append(entry)
        return entry


class CollaboratorLog:
    """Every touch of a live-path collaborator, in order."""

    def __init__(self) -> None:
        self.calls: List[str] = []

    def record(self, name: str) -> None:
        self.calls.append(name)


class RecordingExchangeExecutor:
    """Any attribute reached on the exchange is a recorded touch.

    ``__getattr__`` rather than a fixed set of methods, so a future live path that calls
    something this test never named is still caught.
    """

    def __init__(self, log: CollaboratorLog) -> None:
        self._log = log

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_log"):
            raise AttributeError(name)
        self._log.record(f"exchange.{name}")

        async def _recorded(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError(
                f"the live order path called exchange.{name} for a refused order"
            )

        return _recorded


class RecordingKillSwitch:
    def __init__(self, log: CollaboratorLog, active: bool = False) -> None:
        self._log = log
        self._active = active

    async def is_active(self) -> bool:
        self._log.record("kill_switch.is_active")
        return self._active


def _engine_with_recorders(monkeypatch, *, kill_switch_active: bool = False):
    """An engine whose every live-path collaborator records instead of acting."""
    log = CollaboratorLog()
    engine = ExecutionEngine(
        portfolio_state={"total_equity": Decimal("100000.0")},
        exchange_executor=RecordingExchangeExecutor(log),
    )

    import backend_app.core.execution_engine as engine_module
    import backend_app.core.global_safety as global_safety

    kill_switch = RecordingKillSwitch(log, active=kill_switch_active)
    monkeypatch.setattr(global_safety, "get_global_kill_switch", lambda: kill_switch)

    def _session_local(*_args: Any, **_kwargs: Any):
        log.record("SessionLocal")
        raise AssertionError("the live order path opened a database session for a refused order")

    monkeypatch.setattr(engine_module, "SessionLocal", _session_local)

    def _validate(*_args: Any, **_kwargs: Any) -> bool:
        log.record("ExecutionEngine._validate_strategy_exists")
        return True

    monkeypatch.setattr(engine, "_validate_strategy_exists", _validate)

    async def _internal(*_args: Any, **_kwargs: Any):
        log.record("ExecutionEngine._execute_trade_internal")
        raise AssertionError("the live order path executed a refused order")

    monkeypatch.setattr(engine, "_execute_trade_internal", _internal)
    return engine, log


def _intent(**overrides: Any) -> Dict[str, Any]:
    intent: Dict[str, Any] = {
        "tenant_id": uuid4(),
        "strategy_id": "strat-guard-1",
        "symbol": "BTCUSDT",
        "side": "buy",
        "size": Decimal("0.25"),
        "price": Decimal("61234.50"),
        "source": "bot_runner",
    }
    intent.update(overrides)
    return intent


# ══════════════════════════════════════════════════════════════════════════
#  REQUIREMENT 13.1 — THE VALUE SET
# ══════════════════════════════════════════════════════════════════════════


def test_execution_environment_is_exactly_three_values() -> None:
    """Requirement 13.1: ``BACKTEST``, ``PAPER``, ``LIVE`` - and nothing else."""
    assert [member.value for member in ExecutionEnvironment] == ["BACKTEST", "PAPER", "LIVE"]
    assert tuple(EXECUTION_ENVIRONMENTS) == (
        ExecutionEnvironment.BACKTEST,
        ExecutionEnvironment.PAPER,
        ExecutionEnvironment.LIVE,
    )
    # The `str` mixin: a value compares equal to its spelling, so an existing string
    # comparison keeps working and no stored value is renamed (Requirement 13.1, additive).
    assert ExecutionEnvironment.LIVE == "LIVE"
    assert ExecutionEnvironment("PAPER") is ExecutionEnvironment.PAPER


@pytest.mark.parametrize("value", REFUSED_BY_13_10)
def test_parse_refuses_anything_but_an_exact_spelling(value: Any) -> None:
    """Requirement 13.10: no trimming, no case-folding, no default."""
    assert parse_execution_environment(value) is None


@pytest.mark.parametrize("member", list(ExecutionEnvironment))
def test_parse_accepts_the_member_and_its_exact_spelling(member: ExecutionEnvironment) -> None:
    assert parse_execution_environment(member) is member
    assert parse_execution_environment(member.value) is member


# ══════════════════════════════════════════════════════════════════════════
#  THE GUARD ITSELF
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_live_passes_and_writes_no_audit_record() -> None:
    audit = RecordingAuditLogger()
    for value in (ExecutionEnvironment.LIVE, "LIVE"):
        assert await assert_live_environment(value, audit_logger=audit) is ExecutionEnvironment.LIVE
    assert audit.records == []


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", REFUSED_BY_13_9)
async def test_paper_and_backtest_are_refused_and_audited(
    environment: ExecutionEnvironment,
) -> None:
    """Requirement 13.9: refused, and the rejection is in the Audit_Log."""
    audit = RecordingAuditLogger()

    with pytest.raises(ExecutionEnvironmentMismatch) as raised:
        await assert_live_environment(
            environment,
            actor_id="tenant-7",
            strategy_id="strat-guard-1",
            symbol="BTCUSDT",
            audit_logger=audit,
        )

    assert raised.value.received is environment
    assert raised.value.code == "EXECUTION_ENVIRONMENT_MISMATCH"

    assert len(audit.records) == 1
    record = audit.records[0]
    assert record["action"] == EXECUTION_ENVIRONMENT_MISMATCH_ACTION
    assert record["actor_id"] == "tenant-7"
    assert record["resource_id"] == "strat-guard-1"
    assert record["metadata"]["received_environment"] == environment.value
    assert record["metadata"]["expected_environment"] == "LIVE"
    assert record["metadata"]["symbol"] == "BTCUSDT"
    assert record["reason"]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", REFUSED_BY_13_10)
async def test_unresolved_is_refused_and_never_defaulted_to_live(value: Any) -> None:
    """Requirement 13.10: absent or out of set raises, and resolves to nothing."""
    audit = RecordingAuditLogger()

    with pytest.raises(UnresolvedExecutionEnvironment) as raised:
        await assert_live_environment(value, audit_logger=audit)

    assert raised.value.code == "EXECUTION_ENVIRONMENT_UNRESOLVED"
    assert repr(value) in str(raised.value)
    # No mismatch record: there is no environment to name in one. The refusal is in the log,
    # asserted separately below.
    assert audit.records == []


@pytest.mark.asyncio
async def test_the_default_audit_sink_writes_the_mismatch_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """With no injected sink the record goes to the real strategy audit logger.

    Its structured log line is written first and unconditionally - Redis is a cache on top
    of it - so this asserts the record reaches the sink that exists in every environment.
    """
    caplog.set_level(logging.INFO, logger="OrderAuditTrail")

    with pytest.raises(ExecutionEnvironmentMismatch):
        await assert_live_environment(
            ExecutionEnvironment.PAPER, actor_id="tenant-7", strategy_id="strat-guard-1"
        )

    audit_lines = []
    for entry in caplog.records:
        if entry.name != "OrderAuditTrail":
            continue
        try:
            audit_lines.append(json.loads(entry.getMessage()))
        except (ValueError, TypeError):
            continue

    assert any(
        line.get("action") == EXECUTION_ENVIRONMENT_MISMATCH_ACTION
        and line.get("resource_id") == "strat-guard-1"
        and line.get("metadata", {}).get("received_environment") == "PAPER"
        for line in audit_lines
    ), f"no mismatch audit line was written; saw {audit_lines}"


@pytest.mark.asyncio
async def test_an_unavailable_audit_sink_does_not_rescue_the_order() -> None:
    """An audit write that fails must not turn a refusal into a success."""

    class BrokenAuditLogger:
        async def log(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("audit backend down")

    with pytest.raises(ExecutionEnvironmentMismatch):
        await assert_live_environment(
            ExecutionEnvironment.PAPER, audit_logger=BrokenAuditLogger()
        )


# ══════════════════════════════════════════════════════════════════════════
#  THE INSTALLATION — core/execution_engine.execute_with_idempotency
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", REFUSED_BY_13_9)
async def test_live_path_refuses_paper_and_backtest_before_any_collaborator(
    monkeypatch: pytest.MonkeyPatch, environment: ExecutionEnvironment
) -> None:
    """Requirement 13.9, asserted on collaborators rather than on log text."""
    engine, log = _engine_with_recorders(monkeypatch)

    with pytest.raises(ExecutionEnvironmentMismatch):
        await engine.execute_with_idempotency(**_intent(execution_environment=environment))

    assert log.calls == [], f"a refused {environment.value} order touched {log.calls}"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "live ", "PAPER_TRADING"])
async def test_live_path_refuses_an_unresolved_environment_before_any_collaborator(
    monkeypatch: pytest.MonkeyPatch, value: Any
) -> None:
    """Requirement 13.10: no exchange call, and no fallback to LIVE."""
    engine, log = _engine_with_recorders(monkeypatch)

    with pytest.raises(UnresolvedExecutionEnvironment):
        await engine.execute_with_idempotency(**_intent(execution_environment=value))

    assert log.calls == [], f"an unresolved-environment order touched {log.calls}"


@pytest.mark.asyncio
async def test_the_guard_runs_ahead_of_the_source_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard is first, so not even the pre-existing source refusal precedes it.

    A ``PAPER`` order from a source the engine would reject anyway must still be refused as
    an environment mismatch - otherwise the ordering claim ("the top of
    ``execute_with_idempotency``") would not hold.
    """
    engine, log = _engine_with_recorders(monkeypatch)

    with pytest.raises(ExecutionEnvironmentMismatch):
        await engine.execute_with_idempotency(
            **_intent(source="manual_ui", execution_environment=ExecutionEnvironment.PAPER)
        )

    assert log.calls == []


@pytest.mark.asyncio
async def test_a_live_order_still_reaches_the_existing_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 25.1: the guard adds a refusal, it does not remove one.

    The kill switch is armed, so the pre-existing block is what answers - proving the LIVE
    order passed the guard and arrived at the check that already existed.
    """
    engine, log = _engine_with_recorders(monkeypatch, kill_switch_active=True)

    result = await engine.execute_with_idempotency(
        **_intent(execution_environment=ExecutionEnvironment.LIVE)
    )

    assert result["status"] == "blocked"
    assert "kill switch" in result["message"]
    assert log.calls == ["kill_switch.is_active"]


@pytest.mark.asyncio
async def test_a_caller_that_names_no_environment_keeps_its_existing_behaviour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parameter's default is the pre-existing live-only convention, written down.

    Every existing caller of this engine submits live orders and predates the parameter, so
    omitting it must behave exactly as before: the kill switch answers, not the guard.
    """
    engine, log = _engine_with_recorders(monkeypatch, kill_switch_active=True)

    result = await engine.execute_with_idempotency(**_intent())

    assert result["status"] == "blocked"
    assert log.calls == ["kill_switch.is_active"]
