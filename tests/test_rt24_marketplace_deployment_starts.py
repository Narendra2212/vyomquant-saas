"""RT-24 -- a marketplace deployment is recorded AND started.

THE DEFECT
----------
``routers/library.deploy_marketplace_strategy`` ended at a single ``.insert()`` into
``strategy_deployments`` and returned the new id. It performed none of what the first-party
path does after its own insert: no quota reservation, no runtime blueprint, no
``fleet.start_bot``. ``StrategyService.deploy_version`` does all three.

So a marketplace deployment's ``status`` stayed ``'deploying'`` and nothing ever evaluated
the strategy. True of the PAPER path since it was built, and inherited rather than
introduced by MKT-05's live option.

WHAT THE FIX IS, AND WHERE IT LIVES
------------------------------------
The route reserves the quota and checks the live entitlement -- both of which it previously
did not do at all -- and then hands the WRITTEN ROW to
``deployment_runtime.start_recorded_deployment``, which builds the blueprint and starts the
bot.

THE OWNER'S LOGIC NEVER PASSES THROUGH THE ROUTE. A ``strategy_versions`` row carries
``blueprint`` and ``execution_graph``, and a subscriber may EXECUTE an owner's strategy
without being able to READ it (Requirement 7.1) --
``tests/test_marketplace_deployment_subscriber_safe.py::
test_the_owner_version_read_selects_no_protected_logic_column`` asserts that by inspecting
every ``strategy_versions`` select the route's own client makes. A first attempt at this
finding DID read the blueprint in the route and that test caught it. The blueprint is now
loaded by the runtime through the runtime's own service client, which is how the resume path
has always done it, and :func:`test_the_route_never_reads_the_owners_logic` keeps it that
way.

BUILT ON ``rebuild_for_resume`` RATHER THAN BESIDE IT. That function already derives every
runtime input from the ROW -- ``exchange_symbol`` (falling back to the plan's resolved
market), ``exchange_id``, ``mode``, ``execution_config`` and MKT-05's ``authorized_until``.
A second builder would be a second place for the row and the running bot to disagree about
which market, which limits and which authorization window apply.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

import pytest

from backend_app.backend import deployment_runtime as dr

# The harness, reused rather than rebuilt: the injected-client double, the entitled
# subscriber, the audit spy, the rate-limiter stand-down and the quota seams all already
# exist for this route. Importing them is the same thing
# tests/test_rt02_builder_strategy_runs_on_deployment_runtime.py does with the first-party
# deploy fixtures, and for the same reason -- two harnesses for one route is how the two
# come to disagree about what the route does.
from tests.test_marketplace_deployment_subscriber_safe import (  # noqa: F401
    LISTING_ID,
    OWNER_STRATEGY_ID,
    OWNER_VERSION_ID,
    SUBSCRIBER_ID,
    _deploy,
    _quota_and_entitlement_seams,
    _rate_limiter_off,
    audit,
    entitled_client,
)


def run(coro):
    return asyncio.run(coro)


# ══════════════════════════════════════════════════════════════════════════════
#  1. start_recorded_deployment: the runtime half, on its own
# ══════════════════════════════════════════════════════════════════════════════


# The canonical startable version row, imported rather than hand-rolled. A first draft of
# this file built its own and spent two runs discovering that `compiled_plan` needs a
# non-empty `dag_hash` inside it (SB-02) -- which is exactly the knowledge `_version()`
# already encodes. A second fixture for one shape is how the two come to disagree about
# what "startable" means.
from tests.test_task_8_2_deployment_binding import _version  # noqa: E402


def _version_row() -> Dict[str, Any]:
    """A version row the RUNTIME will accept, not merely one the BINDING gates accept.

    `_version()` supplies the valid `compiled_plan` (with the non-empty `dag_hash` SB-02
    requires) that `resolve_binding_market` reads, and that is why it is reused. Its
    `blueprint`, though, is `_STARTABLE_BLUEPRINT` -- whose `nodes` and `edges` are EMPTY
    lists, which satisfies the binding checks and is refused by
    `build_runtime_blueprint`: "the stored version carries no strategy graph
    (nodes/edges), so there is nothing for the runtime to evaluate".
    cf. tests/test_task_8_2_deployment_binding.py's own comment about an empty graph
    passing only because `start_bot` was an AsyncMock.

    So the graph is supplied here. That difference is the point of this file: a deployment
    that satisfies every admission gate and still cannot be STARTED is exactly the class of
    defect RT-24 is.
    """
    return _version(
        id=OWNER_VERSION_ID,
        version="v1.0",
        blueprint={
            "nodes": [
                {"id": "d1", "type": "DATA",
                 "data": {"symbol": "BTC/USDT", "timeframe": "1h"}},
                {"id": "s1", "type": "SIGNAL", "data": {}},
            ],
            "edges": [{"source": "d1", "target": "s1"}],
            "exchange_id": "binance",
        },
    )


def _row(**overrides) -> Dict[str, Any]:
    base = {
        "id": "dep-1",
        "user_id": SUBSCRIBER_ID,
        "strategy_id": OWNER_STRATEGY_ID,
        "version_id": OWNER_VERSION_ID,
        "marketplace_listing_id": LISTING_ID,
        "environment": "paper",
        "status": "deploying",
        "exchange_symbol": "BTC/USDT",
    }
    base.update(overrides)
    return base


class _Fleet:
    """Records what ``start_bot`` was asked for, and answers what the test tells it to."""

    def __init__(self, result: Tuple[bool, str] = (True, "BTC/USDT")):
        self.result = result
        self.calls: List[dict] = []

    async def start_bot(self, *, user_id, symbol, blueprint, paper_trading):
        self.calls.append(
            {
                "user_id": user_id,
                "symbol": symbol,
                "blueprint": blueprint,
                "paper_trading": paper_trading,
            }
        )
        return self.result


def _install_fleet(monkeypatch, fleet: Optional[_Fleet]):
    from backend_app.core import state

    class _AppState:
        pass

    stub = _AppState()
    if fleet is not None:
        stub.fleet = fleet
    monkeypatch.setattr(state, "app_state", stub, raising=False)
    return stub


def _loader(version_row: Optional[Dict[str, Any]]):
    async def _load(_version_id):
        return version_row

    return lambda _factory=None: _load


def test_a_recorded_deployment_is_started_with_the_rows_own_market(monkeypatch):
    fleet = _Fleet()
    _install_fleet(monkeypatch, fleet)
    monkeypatch.setattr(dr, "service_version_loader", _loader(_version_row()))

    started, message = run(dr.start_recorded_deployment(_row(), allow_live=False))

    assert started is True, message
    assert len(fleet.calls) == 1
    call = fleet.calls[0]
    assert call["user_id"] == SUBSCRIBER_ID
    assert call["symbol"] == "BTC/USDT"
    assert call["blueprint"]["deployment_id"] == "dep-1", (
        "the runtime keys its own state on the deployment id, so the blueprint must carry "
        "the REAL id -- not a placeholder minted before the row existed"
    )
    assert call["blueprint"]["symbol"] == "BTC/USDT"


def test_paper_and_live_pass_the_mode_explicitly(monkeypatch):
    """The parameter exists because `BotRunner` defaulting to paper made every live
    deployment run as paper. A live marketplace deployment that started in paper mode would
    be the same defect wearing MKT-05's clothes."""
    for mode, expected_paper in (("paper", True), ("live", False)):
        fleet = _Fleet()
        _install_fleet(monkeypatch, fleet)
        monkeypatch.setattr(dr, "service_version_loader", _loader(_version_row()))

        started, message = run(
            dr.start_recorded_deployment(
                _row(mode=mode, environment=mode, exchange_id="binance"),
                allow_live=(mode == "live"),
            )
        )
        assert started is True, f"{mode}: {message}"
        assert fleet.calls[0]["paper_trading"] is expected_paper, mode
        assert fleet.calls[0]["blueprint"]["mode"] == mode


def test_a_live_deploy_carries_the_authorization_window_into_the_blueprint(monkeypatch):
    """MKT-05's enforcement, and the reason it is not left to the expiry sweep.

    `master_executor.evaluate_authorization_window` reads `authorized_until` off the
    blueprint every tick and refuses an order past it. A live blueprint without it is a bot
    whose only bound is a 30-second worker being alive.
    """
    fleet = _Fleet()
    _install_fleet(monkeypatch, fleet)
    monkeypatch.setattr(dr, "service_version_loader", _loader(_version_row()))

    expiry = "2026-12-31T23:59:59+00:00"
    started, _ = run(
        dr.start_recorded_deployment(
            _row(mode="live", environment="live", exchange_id="binance",
                 authorized_until=expiry),
            allow_live=True,
        )
    )
    assert started is True
    assert fleet.calls[0]["blueprint"]["authorized_until"] == expiry


def test_a_refused_start_is_reported_rather_than_raised(monkeypatch):
    fleet = _Fleet(result=(False, "Bot already running for this symbol"))
    _install_fleet(monkeypatch, fleet)
    monkeypatch.setattr(dr, "service_version_loader", _loader(_version_row()))

    started, message = run(dr.start_recorded_deployment(_row(), allow_live=False))

    assert started is False
    assert "already running" in message


def test_a_missing_version_is_reported_rather_than_raised(monkeypatch):
    """The row points at a version that has gone. The caller needs to mark the row, so this
    must not escape as an exception."""
    _install_fleet(monkeypatch, _Fleet())
    monkeypatch.setattr(dr, "service_version_loader", _loader(None))

    started, message = run(dr.start_recorded_deployment(_row(), allow_live=False))

    assert started is False
    assert "no longer exists" in message


def test_no_fleet_answers_none_which_is_neither_started_nor_failed(monkeypatch):
    """Three-valued on purpose. The API can run in a process that hosts no bots -- which is
    `deploy_version`'s own behaviour -- and a row left for the boot-time resume sweep must
    not be reported to the subscriber as failed."""
    _install_fleet(monkeypatch, None)
    monkeypatch.setattr(dr, "service_version_loader", _loader(_version_row()))

    started, message = run(dr.start_recorded_deployment(_row(), allow_live=False))

    assert started is None
    assert "no fleet" in message


def test_a_live_start_does_not_depend_on_the_resume_flag(monkeypatch):
    """`RESUME_LIVE_DEPLOYMENTS` gates restarting real-money trading UNATTENDED AFTER A
    CRASH. A trader deploying live right now, having just passed the live gate, is a
    different question, and conflating them would make a deliberate deploy depend on an
    operator flag about crash recovery."""
    monkeypatch.delenv("RESUME_LIVE_DEPLOYMENTS", raising=False)
    fleet = _Fleet()
    _install_fleet(monkeypatch, fleet)
    monkeypatch.setattr(dr, "service_version_loader", _loader(_version_row()))

    started, message = run(
        dr.start_recorded_deployment(
            _row(mode="live", environment="live", exchange_id="binance"),
            allow_live=True,
        )
    )
    assert started is True, message

    # And the resume path still refuses without the flag, so the distinction is real.
    with pytest.raises(dr.RuntimeBlueprintError, match="not resumed automatically"):
        run(
            dr.rebuild_for_resume(
                _row(mode="live", environment="live", exchange_id="binance"),
                load_version=_loader(_version_row())(),
            )
        )


# ══════════════════════════════════════════════════════════════════════════════
#  2. The route: what it writes, and what it refuses before writing
# ══════════════════════════════════════════════════════════════════════════════


def _start_stub(result):
    async def _start(_row, *, allow_live, client_factory=None):
        return result

    return _start


def test_a_started_deployment_is_recorded_running(entitled_client, audit, monkeypatch):
    monkeypatch.setattr(dr, "start_recorded_deployment", _start_stub((True, "BTC/USDT")))

    result = _deploy({})

    assert result["status"] == "running"
    updates = [w for w in entitled_client.writes_to("strategy_deployments")
               if w.op == "update"]
    assert updates, "a started deployment must not be left at 'deploying'"
    assert updates[-1].payload["status"] == "running"
    assert updates[-1].payload.get("started_at"), (
        "started_at is what distinguishes a bot that is running from a row that claims to be"
    )


def test_a_refused_start_is_recorded_failed_with_the_reason(
    entitled_client, audit, monkeypatch
):
    monkeypatch.setattr(dr, "start_recorded_deployment", _start_stub((False, "no slots")))

    result = _deploy({})

    assert result["status"] == "failed"
    updates = [w for w in entitled_client.writes_to("strategy_deployments")
               if w.op == "update"]
    assert updates[-1].payload["status"] == "failed"
    assert "no slots" in updates[-1].payload["error_message"], (
        "a deployment that did not start must say why; 'failed' alone sends the subscriber "
        "to support"
    )
    # The row is KEPT and marked, never deleted -- `deploy_version`'s own choice. A
    # deployment that was accepted and then failed to start is a thing the subscriber needs
    # to be able to see.
    assert [w for w in entitled_client.writes_to("strategy_deployments")
            if w.op == "insert"]


def test_no_fleet_leaves_the_row_deploying_rather_than_failed(
    entitled_client, audit, monkeypatch
):
    monkeypatch.setattr(dr, "start_recorded_deployment", _start_stub((None, "no fleet")))

    result = _deploy({})

    assert result["status"] == "deploying", (
        "calling this failed would tell a subscriber their deployment broke when it is "
        "waiting for a process that hosts bots"
    )


def test_a_quota_refusal_writes_no_row(entitled_client, audit, monkeypatch):
    """The reservation sits AFTER every other refusal and BEFORE the insert, so a refusal
    leaves nothing behind -- `deploy_version`'s ordering, and the property that makes a
    refused deploy free."""
    from fastapi import HTTPException

    from backend_app.core.subscription_engine import SubscriptionEngine

    async def _exhausted(*_a, **_k):
        return (False, 5, 5)

    monkeypatch.setattr(SubscriptionEngine, "reserve_quota", _exhausted, raising=False)

    with pytest.raises(HTTPException) as caught:
        _deploy({})

    assert caught.value.status_code == 402
    assert caught.value.detail["error"] == "DEPLOYMENT_QUOTA_EXCEEDED"
    assert not entitled_client.writes_to("strategy_deployments"), (
        "a quota refusal wrote a deployment row; the reservation must precede the insert"
    )


def test_a_live_deploy_without_the_entitlement_is_refused_and_writes_no_row(
    entitled_client, audit, monkeypatch
):
    """A marketplace subscription entitles the STRATEGY, not real-money execution. The
    first-party path has always refused this; this route did not check at all."""
    from fastapi import HTTPException

    from backend_app.core.subscription_engine import SubscriptionEngine

    async def _not_entitled(*_a, **_k):
        return False

    monkeypatch.setattr(
        SubscriptionEngine, "check_feature_entitlement", _not_entitled, raising=False
    )

    with pytest.raises(HTTPException) as caught:
        _deploy({"mode": "live", "exchange_account_id": str(OWNER_VERSION_ID)})

    assert caught.value.status_code in (403, 404), caught.value.detail
    assert not entitled_client.writes_to("strategy_deployments")


def test_the_route_never_reads_the_owners_logic(entitled_client, audit, monkeypatch):
    """The property a first attempt at this finding broke.

    The blueprint is loaded by the runtime's own service client. Nothing the ROUTE's client
    selects may name a Protected_Logic column, whatever else the route gained.
    """
    monkeypatch.setattr(dr, "start_recorded_deployment", _start_stub((True, "ok")))

    _deploy({})

    version_reads = [
        c for c in entitled_client.calls
        if c.table_name == "strategy_versions" and c.op == "select"
    ]
    assert version_reads, "the owner's version label is still read for the NOT NULL column"
    for read in version_reads:
        assert "blueprint" not in (read.cols or "")
        assert "execution_graph" not in (read.cols or "")
        assert "compiled_plan" not in (read.cols or "")


def test_the_response_reports_the_status_it_recorded(entitled_client, audit, monkeypatch):
    """A caller that cannot tell 'running' from 'failed' has to poll to find out whether
    its strategy trades."""
    for start_result, expected in (
        ((True, "ok"), "running"),
        ((False, "nope"), "failed"),
        ((None, "no fleet"), "deploying"),
    ):
        monkeypatch.setattr(dr, "start_recorded_deployment", _start_stub(start_result))
        assert _deploy({})["status"] == expected
