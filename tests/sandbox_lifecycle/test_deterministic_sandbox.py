"""
The end-to-end deterministic sandbox test. Requirement 26, task 21.1.

Requirement 26.4 mandates this suite. It is not test sugar and it is not optional: the
requirement names an automated test that exercises Requirement 26.2's full chain with a
deterministic test strategy and asserts six things about it -

    1. the strategy and its version are persisted,
    2. a Backtest_Result is completed and persisted,
    3. a deployment binding in a non-production mode succeeds,
    4. at least one Signal is generated,
    5. that Signal appears with a trace record on the Signal_Trace_Page,
    6. and there is NO real exchange order-placement call for that Signal.

Requirement 26.3 adds a seventh: that Signal's Order_Lifecycle_State reaches a TERMINAL
state distinguishable from the state a real exchange fill produces.

HOW THIS FILE IS ORGANISED, AND WHY IT IS ONE CHAIN
--------------------------------------------------
The chain is a chain: a deployment cannot be bound without a version, and no Signal can be
generated without a deployment. So ``chain`` is one fixture that walks all six steps
against the real mounted app and returns a :class:`ChainReport` of what each step actually
produced; every test below then asserts one clause of Requirement 26 against that report.
Splitting the walk across independent tests would mean walking it six times, and asserting
it all in one test body would mean the first failure hid the other five.

Each step's own refusal shapes, response bodies and boundaries are already covered where
they live - tasks 5.2 (the listing), 6.1 (the backtest), 8.2 (the binding), 10.1-10.3 (the
signal path), 13.1-13.2 (the trace). This file asserts the thing none of those can: that
the artifacts one step persists are the artifacts the next step reads.

WHAT THIS SUITE CANNOT PROVE, STATED UP FRONT
---------------------------------------------
There is no PostgreSQL and no Redis on this host, so nothing here proves that a NOT NULL
constraint, a foreign key or an RLS policy is satisfied by the rows these steps write;
``tests/test_signal_lifecycle_idempotency_migration.py`` and its siblings verify the
migrations statically. What is proved here is the shape of the hand-offs: which columns each
step writes and which columns the next step reads.

DEFECTS THIS SUITE FOUND
------------------------
See ``TestTheHandOffsThisSuiteFound`` at the bottom of this file. They are regression tests
for two integration gaps that only an end-to-end walk could surface, both between
``strategy_service.deploy_version``'s write and ``signal_service``'s read of the same row.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict

import pytest

from backend_app.backend.order_lifecycle_state import (
    TERMINAL_STATES,
    OrderLifecycleState,
    is_terminal,
)
from tests.sandbox_lifecycle.harness import (
    SANDBOX_BARS,
    SANDBOX_EXCHANGE_ACCOUNT_ID,
    SANDBOX_RISK_CONFIG_ID,
    SANDBOX_SYMBOL,
    SANDBOX_TIMEFRAME,
    SANDBOX_USER,
    SANDBOX_VENUE,
    SandboxWorld,
    all_nodes_ready,
    sandbox_action_output,
    sandbox_graph,
)

#: The states a REAL exchange fill puts a Signal in. Requirement 26.3's "distinguishable
#: from a Signal that resulted in a real exchange fill" is measured against exactly this
#: set: a sandbox Signal's terminal state may not be one of them.
FILL_STATES = frozenset(
    {
        OrderLifecycleState.PARTIALLY_EXECUTED,
        OrderLifecycleState.EXECUTED,
        OrderLifecycleState.CLOSED,
    }
)


# ══════════════════════════════════════════════════════════════════════════
# THE CHAIN
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class ChainReport:
    """What each step of Requirement 26.2's chain actually produced."""

    world: SandboxWorld
    save: Dict[str, Any] = field(default_factory=dict)
    version: Dict[str, Any] = field(default_factory=dict)
    listing: Dict[str, Any] = field(default_factory=dict)
    backtest: Dict[str, Any] = field(default_factory=dict)
    deploy: Dict[str, Any] = field(default_factory=dict)
    signal_outcome: Any = None
    trace_list: Dict[str, Any] = field(default_factory=dict)
    trace_detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def signal(self) -> Any:
        assert self.signal_outcome is not None, "the chain never reached the signal step"
        return self.signal_outcome.signal

    def trace_item(self) -> Dict[str, Any]:
        for item in self.trace_list.get("signals") or []:
            if str(item.get("id")) == str(self.signal.id):
                return item
        raise AssertionError(
            f"signal {self.signal.id} does not appear on GET /api/signal-trace/signals; "
            f"the list carried {[i.get('id') for i in self.trace_list.get('signals') or []]}"
        )


@pytest.fixture
def chain(sandbox: SandboxWorld, client, registry) -> ChainReport:
    """Walk Requirement 26.2's whole chain once, and report what each step produced.

    Every HTTP call goes through the real mounted app. Nothing here asserts - a step that
    does not answer 200 raises with the body, so a failure names the step that broke rather
    than a downstream symptom of it.
    """
    report = ChainReport(world=sandbox)
    graph = sandbox_graph(registry).to_dict()

    # ── STEP 1a. SAVE (Requirement 26.2, "save") ──────────────────────────
    saved = client.post(
        "/api/strategies",
        json={"name": "Deterministic Sandbox Fixture", **graph},
    )
    assert saved.status_code == 200, f"save failed: {saved.status_code} {saved.text}"
    report.save = saved.json()
    sandbox.strategy_id = report.save["id"]

    # ── STEP 1b. THE IMMUTABLE VERSION ────────────────────────────────────
    # The Builder's own save-version path. Deployment always references an immutable
    # version, so this - not the strategy row - is the artifact the rest of the chain reads.
    versioned = client.post(
        f"/api/strategy-operations/strategies/{sandbox.strategy_id}/versions",
        json={"blueprint": graph, "make_current": True, "is_draft": False},
    )
    assert versioned.status_code == 200, (
        f"version save failed: {versioned.status_code} {versioned.text}"
    )
    report.version = versioned.json()
    sandbox.version_id = report.version["version_id"]
    sandbox.version_label = report.version["version"]

    # ── STEP 2. THE STRATEGIES_PAGE LISTING ───────────────────────────────
    listed = client.get("/api/strategies")
    assert listed.status_code == 200, listed.text
    report.listing = listed.json()

    # ── STEP 3. THE BACKTEST ──────────────────────────────────────────────
    executed = client.post(
        f"/api/strategy-operations/strategies/{sandbox.strategy_id}/backtests/execute",
        json={
            "version_id": sandbox.version_id,
            "start_date": "2024-01-01",
            "end_date": "2024-02-17",
            "initial_capital": 25_000.0,
        },
    )
    assert executed.status_code == 200, (
        f"backtest failed: {executed.status_code} {executed.text}"
    )
    report.backtest = executed.json()
    sandbox.backtest_id = report.backtest.get("backtest_id")

    # ── STEP 4. THE DEPLOYMENT BINDING, mode=paper ────────────────────────
    deployed = client.post(
        f"/api/strategies/{sandbox.strategy_id}/versions/{sandbox.version_label}/deploy",
        json={
            "mode": "paper",
            "exchange_account_id": SANDBOX_EXCHANGE_ACCOUNT_ID,
            "risk_config_id": SANDBOX_RISK_CONFIG_ID,
        },
    )
    assert deployed.status_code == 200, (
        f"deploy failed: {deployed.status_code} {deployed.text}"
    )
    report.deploy = deployed.json()
    sandbox.deployment_id = (report.deploy.get("deployment") or {}).get("id")
    assert sandbox.deployment_id, f"the deploy response named no deployment: {report.deploy}"

    # ── STEP 5. THE SIGNAL ────────────────────────────────────────────────
    report.signal_outcome = asyncio.run(_generate_one_signal(sandbox))
    assert report.signal_outcome.signal is not None, (
        f"no Signal was generated: {report.signal_outcome.to_dict()}"
    )

    # ── STEP 6. THE SIGNAL_TRACE_PAGE ─────────────────────────────────────
    traced = client.get(
        "/api/signal-trace/signals", params={"deployment_id": sandbox.deployment_id}
    )
    assert traced.status_code == 200, traced.text
    report.trace_list = traced.json()

    detail = client.get(f"/api/signal-trace/signals/{report.signal_outcome.signal.id}")
    assert detail.status_code == 200, detail.text
    report.trace_detail = detail.json()

    return report


async def _generate_one_signal(world: SandboxWorld) -> Any:
    """One market event through the shipped ``LiveSignalPath``, on the seeded feed's bar.

    The feed gate is fed a real :func:`observe_feed_state` reading measured against the
    seeded feed's own last bar, so the ``LIVE`` classification is ``feed_state.py``'s and
    not this suite's. The closure gate is fed a real ``PlanRuntimeState`` over the version's
    own persisted plan.
    """
    path, _risk, _execution = world.signal_path()
    plan = world.compiled_plan()

    feed_report = path.observe_feed_state(
        symbol=SANDBOX_SYMBOL,
        connected=True,
        age_seconds=5.0,
        available_bars=SANDBOX_BARS,
    )
    return await path.on_action_output(
        sandbox_action_output(world.feed),
        plan=plan,
        runtime_state=all_nodes_ready(plan, bars=SANDBOX_BARS),
        action_node_id="sbx_buy",
        feed=feed_report,
    )


# ══════════════════════════════════════════════════════════════════════════
# 1. REQUIREMENT 26.1 - THE FIXTURE IS DETERMINISTIC
# ══════════════════════════════════════════════════════════════════════════


class TestTheFixtureIsDeterministic:
    """Requirement 26.1: identical Signals from identical input data, no live feed."""

    def test_the_synthetic_feed_is_a_pure_function_of_its_seed(self):
        from tests.sandbox_lifecycle.harness import SANDBOX_SEED, SeededSyntheticFeed

        first = SeededSyntheticFeed(seed=SANDBOX_SEED)
        second = SeededSyntheticFeed(seed=SANDBOX_SEED)

        assert first.bars == second.bars
        assert first.digest() == second.digest()
        assert len(first.bars) == SANDBOX_BARS

        # And a different seed is a different market, so the seed is really the input.
        assert SeededSyntheticFeed(seed=SANDBOX_SEED + 1).digest() != first.digest()

    def test_the_fixture_graph_has_a_stable_identity(self, registry):
        """``dag_hash`` is a function of the node ids, so no id may be minted.

        Requirement 26.1's determinism is about behaviour, and a version whose identity
        changed between two runs could not be shown to behave identically across them.
        """
        from backend_app.backend.strategy_dag.schema import compute_dag_hash

        first = compute_dag_hash(sandbox_graph(registry))
        second = compute_dag_hash(sandbox_graph(registry))
        assert first == second

    def test_the_fixture_declares_no_model_node(self, registry):
        """A model would introduce an artifact, a training state and inference variance."""
        from backend_app.backend.strategy_dag.schema import BlockCategory

        graph = sandbox_graph(registry)
        assert not graph.nodes_in_category(BlockCategory.ML_DL)

    def test_the_fixture_reads_no_live_market(self, chain):
        """Requirement 26.1: the data source is the seeded synthetic one, and only it."""
        assert chain.world.feed.fetch_calls, (
            "the backtest never read the seeded synthetic feed, so the run this suite "
            "asserts about was not driven by it"
        )
        assert all(
            call["symbol"] == SANDBOX_SYMBOL for call in chain.world.feed.fetch_calls
        )


# ══════════════════════════════════════════════════════════════════════════
# 2. REQUIREMENT 26.4, CLAUSE 1 - THE STRATEGY AND ITS VERSION ARE PERSISTED
# ══════════════════════════════════════════════════════════════════════════


class TestTheStrategyAndItsVersionArePersisted:
    def test_the_strategy_row_exists_and_belongs_to_the_owner(self, chain):
        row = chain.world.strategy_row()
        assert row is not None, "the save reported an id for a row that does not exist"
        assert row["user_id"] == SANDBOX_USER["id"]
        assert row["name"] == "Deterministic Sandbox Fixture"

    def test_the_version_row_carries_the_plan_the_rest_of_the_chain_reads(self, chain):
        row = chain.world.version_row()
        assert row is not None
        assert row["strategy_id"] == chain.world.strategy_id
        assert row["version"] == chain.world.version_label
        assert row.get("compiled_plan"), (
            "the version persisted no compiled_plan, so the backtester and the live "
            "runtime would each have to recompile and could disagree (Requirement 22.3)"
        )
        assert row.get("dag_hash") == chain.version["dag_hash"]

    def test_the_version_is_ready_and_therefore_deployable(self, chain):
        """No model node, so Requirement 14.10 puts the version straight at ``READY``."""
        from backend_app.backend.strategy_builder import LIFECYCLE_READY

        from backend_app.backend.strategy_service import TRAINING_NOT_REQUIRED

        assert chain.version["lifecycle_state"] == LIFECYCLE_READY
        assert chain.version["training"]["state"] == TRAINING_NOT_REQUIRED
        assert chain.version["training"]["required"] is False

    def test_the_saved_market_is_the_data_blocks_own(self, chain):
        """SB-06: nothing on the save path substitutes a market."""
        row = chain.world.strategy_row() or {}
        assert row.get("symbol") == SANDBOX_SYMBOL
        assert row.get("timeframe") == SANDBOX_TIMEFRAME


# ══════════════════════════════════════════════════════════════════════════
# 3. REQUIREMENT 26.2 - THE STRATEGY APPEARS ON THE STRATEGIES_PAGE
# ══════════════════════════════════════════════════════════════════════════


class TestTheStrategyAppearsOnTheListing:
    def test_the_saved_strategy_is_on_the_default_list(self, chain):
        ids = [item["id"] for item in chain.listing["strategies"]]
        assert chain.world.strategy_id in ids, (
            f"the saved strategy is not on GET /api/strategies: {chain.listing}"
        )

    def test_it_is_listed_as_active_not_archived(self, chain):
        (item,) = [
            i for i in chain.listing["strategies"] if i["id"] == chain.world.strategy_id
        ]
        assert item["is_archived"] is False
        assert item["archived_at"] is None
        assert chain.listing["archived_total"] == 0


# ══════════════════════════════════════════════════════════════════════════
# 4. REQUIREMENT 26.4, CLAUSE 2 - A COMPLETED, PERSISTED BACKTEST_RESULT
# ══════════════════════════════════════════════════════════════════════════


class TestTheBacktestCompletesAndPersists:
    def test_the_response_carries_a_completed_result(self, chain):
        assert chain.backtest["status"] == "completed", chain.backtest
        assert chain.backtest["backtest_id"]
        assert isinstance(chain.backtest.get("results"), dict)

    def test_one_strategy_backtests_row_was_persisted_for_this_version(self, chain):
        rows = chain.world.backtest_rows()
        assert len(rows) == 1, f"expected exactly one persisted backtest row, got {rows}"
        (row,) = rows
        assert row["id"] == chain.backtest["backtest_id"]
        assert row["user_id"] == SANDBOX_USER["id"]
        assert row["version_id"] == chain.world.version_id, (
            "the persisted result does not name the immutable version that produced it, "
            "so it is not traceable to it (Requirement 10.1)"
        )
        assert row["status"] == "completed"
        assert row.get("completed_at")

    def test_the_persisted_row_carries_the_configuration_that_was_applied(self, chain):
        """Requirement 6.2: no parameter is accepted and then ignored."""
        (row,) = chain.world.backtest_rows()
        assert row["initial_capital"] == 25_000.0
        assert chain.backtest["configuration"]["initial_capital"] == 25_000.0

    def test_the_persisted_row_names_the_version_it_ran(self, chain):
        (row,) = chain.world.backtest_rows()
        assert row["dag_hash"] == chain.version["dag_hash"]
        assert row["dataset"] == SANDBOX_SYMBOL

    def test_the_run_went_through_the_versions_own_plan(self, chain):
        """Requirements 5.2, 5.7: the artifact executed is the persisted one."""
        assert chain.backtest["version_id"] == chain.world.version_id
        assert chain.backtest["ignored_fields"] == [], (
            "nothing was sent that the endpoint had to ignore, so this list must be empty"
        )


# ══════════════════════════════════════════════════════════════════════════
# 5. REQUIREMENT 26.4, CLAUSE 3 - A SANDBOX DEPLOYMENT BINDING SUCCEEDS
# ══════════════════════════════════════════════════════════════════════════


class TestTheSandboxDeploymentBindingSucceeds:
    def test_the_binding_is_recorded_in_a_non_production_mode(self, chain):
        """Requirement 26.2: any Deployment_Binding mode other than ``live``."""
        binding = chain.deploy["binding"]
        assert binding["mode"] == "paper"
        assert binding["mode"] != "live"
        assert binding["binding_stored"] is True

    def test_the_row_carries_the_version_the_account_and_the_mode_as_one_record(
        self, chain
    ):
        """Requirement 13.1."""
        row = chain.world.deployment_row()
        assert row is not None
        assert row["version_id"] == chain.world.version_id
        assert row["version"] == chain.world.version_label
        assert row["mode"] == "paper"
        assert row["exchange_account_id"] == SANDBOX_EXCHANGE_ACCOUNT_ID
        assert row["risk_config_id"] == SANDBOX_RISK_CONFIG_ID
        assert row["user_id"] == SANDBOX_USER["id"]

    def test_the_market_on_the_row_is_the_versions_own(self, chain):
        """SB-06 again, at the deploy end: the symbol comes from the plan's DATA node."""
        row = chain.world.deployment_row() or {}
        assert row["exchange_symbol"] == SANDBOX_SYMBOL
        assert chain.deploy["binding"]["symbol"] == SANDBOX_SYMBOL
        assert chain.deploy["binding"]["timeframe"] == SANDBOX_TIMEFRAME

    def test_no_credential_appears_anywhere_in_the_deploy_response(self, chain):
        """Requirements 13.9, 21.7: the binding is a reference, never a secret."""
        import json

        body = json.dumps(chain.deploy).lower()
        for forbidden in ("api_key", "apikey", "secret", "passphrase", "private_key"):
            assert forbidden not in body, (
                f"the deploy response contains {forbidden!r}"
            )


# ══════════════════════════════════════════════════════════════════════════
# 6. REQUIREMENT 26.4, CLAUSE 4 + REQUIREMENT 26.3 - THE SIGNAL
# ══════════════════════════════════════════════════════════════════════════


class TestTheSignalIsGeneratedAndReachesADistinguishableTerminalState:
    def test_at_least_one_signal_was_generated_and_persisted(self, chain):
        from backend_app.backend.signal_service import OUTCOME_SUBMITTED

        assert chain.signal_outcome.status == OUTCOME_SUBMITTED, (
            f"the signal path did not submit: {chain.signal_outcome.to_dict()}"
        )
        rows = chain.world.signal_rows()
        assert len(rows) == 1, f"expected exactly one signals row, got {rows}"
        (row,) = rows
        assert row["id"] == chain.signal.id
        assert row["deployment_id"] == chain.world.deployment_id
        assert row["strategy_id"] == chain.world.strategy_id
        assert row["symbol"] == SANDBOX_SYMBOL

    def test_the_signal_is_attributable_to_the_version_that_produced_it(self, chain):
        """Requirement 15.2."""
        (row,) = chain.world.signal_rows()
        assert row["strategy_version"] == chain.world.version_label
        assert row["user_id"] == SANDBOX_USER["id"]
        assert row["exchange_id"] == SANDBOX_VENUE
        assert row["idempotency_key"] == f"signal:{chain.signal.id}"

    def test_it_reaches_a_terminal_state(self, chain):
        """Requirement 26.3's first half."""
        reached = chain.signal_outcome.order_lifecycle_state
        assert is_terminal(reached), (
            f"the sandbox Signal is at {reached}, which is not one of Requirement 16.1's "
            f"terminal states {sorted(s.value for s in TERMINAL_STATES)}"
        )
        (row,) = chain.world.signal_rows()
        assert row["order_lifecycle_state"] == reached.value, (
            "the state the path reported is not the state that was persisted"
        )

    def test_that_terminal_state_is_not_one_a_real_fill_produces(self, chain):
        """Requirement 26.3's second half, as a structural claim about the vocabulary."""
        reached = chain.signal_outcome.order_lifecycle_state
        assert reached not in FILL_STATES, (
            f"the sandbox Signal reached {reached.value}, which is exactly what a real "
            f"exchange fill produces - so the Signal_Trace_Page cannot distinguish it "
            f"from one (Requirement 26.3)"
        )
        assert reached is OrderLifecycleState.REJECTED, (
            "this suite pins the sandbox's terminal state, so a change to what a "
            "non-production mode records is a deliberate change and not a drift"
        )

    def test_the_record_says_why_it_is_not_a_fill(self, chain):
        """The state alone is the requirement; the reason is what makes it actionable."""
        transitions = chain.world.transitions(chain.signal.id)
        assert [t["to_state"] for t in transitions] == [
            "GENERATED",
            "PENDING",
            "REJECTED",
        ], transitions
        assert "NOT routed to a real exchange order-placement call" in (
            transitions[-1]["reason"]
        )

    def test_the_signals_own_row_records_the_sandbox_mode(self, chain):
        """So a reader can tell a sandbox Signal from a live one without a join.

        ``public.signals`` has no ``mode`` column, so ``Signal._market_info`` carries it in
        the ``market_info`` JSONB - which is where it is asserted, rather than at a
        top-level column that does not exist.
        """
        (row,) = chain.world.signal_rows()
        assert row["market_info"]["mode"] == "paper"

    def test_no_order_reference_was_recorded_for_it(self, chain):
        """There is no order anywhere, so the record must not name one."""
        (row,) = chain.world.signal_rows()
        assert not row.get("order_id")
        assert not row.get("trade_id")

    def test_the_transition_log_is_append_only(self, chain):
        """Requirement 16.7, measured on the verbs that actually reached the table."""
        assert chain.world.db.verbs_on("order_lifecycle_transitions") <= {
            "select",
            "insert",
        }

    def test_the_submission_was_guarded_by_the_real_idempotency_layer(self, chain):
        """Requirement 19.1, and it is the shipped layer over the shipped Lua path."""
        key = f"signal:{chain.signal.id}"
        cached = chain.world.redis.cached_results()
        assert any(key in name for name in cached), (
            f"no result was cached under the signal's own key; Redis held {list(cached)}"
        )
        assert chain.world.redis.held_locks() == [], (
            "the idempotency lock was not released after the submission completed"
        )


# ══════════════════════════════════════════════════════════════════════════
# 7. REQUIREMENT 26.4, CLAUSE 5 - THE SIGNAL_TRACE_PAGE
# ══════════════════════════════════════════════════════════════════════════


class TestTheSignalAppearsWithATraceRecord:
    def test_it_is_on_the_signal_trace_list(self, chain):
        item = chain.trace_item()
        assert item["symbol"] == SANDBOX_SYMBOL
        assert item["decision"] == "BUY"
        assert item["deployment_id"] == chain.world.deployment_id

    def test_the_list_reports_the_canonical_state_not_a_legacy_one(self, chain):
        item = chain.trace_item()
        assert item["order_lifecycle_state"] == (
            chain.signal_outcome.order_lifecycle_state.value
        )
        assert chain.trace_list["filters_active"] is True

    def test_the_detail_view_carries_the_full_trace(self, chain):
        """Requirement 17.6: the DAG node trace, the risk validation and the outcome."""
        detail = chain.trace_detail
        assert str(detail.get("id") or (detail.get("signal") or {}).get("id")) == str(
            chain.signal.id
        )
        body = _flatten(detail)
        assert "sbx_buy" in body, (
            "the trace detail names no source node, so it cannot say which ACTION node "
            "produced this decision (Requirement 15.2)"
        )
        assert "risk" in body.lower()

    def test_no_credential_appears_on_the_trace_surface(self, chain):
        """Requirements 15.3, 15.4, on the surface a user actually reads."""
        body = _flatten(chain.trace_list) + _flatten(chain.trace_detail)
        for forbidden in ("api_key", "apikey", "secret", "passphrase", "private_key"):
            assert forbidden not in body.lower()


def _flatten(payload: Any) -> str:
    import json

    return json.dumps(payload, default=str)


# ══════════════════════════════════════════════════════════════════════════
# 8. REQUIREMENT 26.4, CLAUSE 6 - NO REAL EXCHANGE ORDER-PLACEMENT CALL
# ══════════════════════════════════════════════════════════════════════════


class TestNoRealExchangeOrderPlacementCallWasMade:
    """The structural assertion, plus the positive control that makes it falsifiable."""

    def test_the_order_placement_seam_was_never_called(self, chain):
        exchange = chain.world.exchange
        assert exchange.order_placement_calls == 0, (
            f"a sandbox deployment reached the venue's order-placement seam "
            f"{exchange.order_placement_calls} time(s): {exchange.placements_by_seam()}"
        )
        assert exchange.placements == []

    def test_the_execution_component_was_consulted_all_the_same(self, chain):
        """Zero placements because the component refused, not because it was skipped.

        Requirement 11.2 forbids a second order-placement path, so "no order was placed"
        has to mean the platform's own execution component decided that - not that the
        submission never reached it.
        """
        detail = (chain.signal_outcome.detail or {})
        assert detail.get("deployment_id") == chain.world.deployment_id
        (row,) = chain.world.signal_rows()
        assert row["order_lifecycle_state"] == OrderLifecycleState.REJECTED.value

    def test_the_counter_is_falsifiable(self, sandbox: SandboxWorld):
        """The positive control: the same seam, in ``live`` mode, IS counted.

        Without this, ``order_placement_calls == 0`` could hold because the double is
        unreachable rather than because the sandbox declined to reach it.
        """
        from tests.sandbox_lifecycle.harness import SandboxExecutionComponent

        component = SandboxExecutionComponent(sandbox.exchange, mode="live")

        class _Signal:
            id = "control-signal"
            symbol = SANDBOX_SYMBOL
            side = "BUY"
            decision = "BUY"
            quantity = 0.25
            idempotency_key = "signal:control-signal"

        outcome = asyncio.run(component.submit_order(_Signal()))

        assert sandbox.exchange.order_placement_calls == 1
        assert sandbox.exchange.placements_by_seam() == {"create_order": 1}
        assert outcome.accepted is True

    def test_every_named_placement_seam_is_counted(self, sandbox: SandboxWorld):
        """The count is over the whole seam, not over one spelling of it."""
        from tests.sandbox_lifecycle.harness import ORDER_PLACEMENT_SEAMS

        async def _call_all() -> None:
            for name in ORDER_PLACEMENT_SEAMS:
                await getattr(sandbox.exchange, name)(symbol=SANDBOX_SYMBOL)

        asyncio.run(_call_all())
        assert sandbox.exchange.order_placement_calls == len(ORDER_PLACEMENT_SEAMS)

    def test_the_named_seams_are_the_ones_the_platform_actually_calls(self):
        """The list is checked against the shipped executor's source, not maintained blind.

        ``CCXTExchangeExecutor.place_order``'s one venue call is
        ``self._exchange.create_order(**params)``. If that call is ever renamed, this
        assertion fails and the sandbox's seam list is updated deliberately rather than
        silently ceasing to cover the real path.
        """
        import inspect

        from backend_app.backend.exchange_executor import CCXTExchangeExecutor
        from tests.sandbox_lifecycle.harness import ORDER_PLACEMENT_SEAMS

        source = inspect.getsource(CCXTExchangeExecutor.place_order)
        assert "self._exchange.create_order(" in source, (
            "the platform's order-placement seam no longer calls create_order; update "
            "ORDER_PLACEMENT_SEAMS deliberately"
        )
        assert "create_order" in ORDER_PLACEMENT_SEAMS
        assert "place_order" in ORDER_PLACEMENT_SEAMS


# ══════════════════════════════════════════════════════════════════════════
# 9. THE HAND-OFFS THIS SUITE FOUND
# ══════════════════════════════════════════════════════════════════════════


class TestTheHandOffsThisSuiteFound:
    """Two integration gaps only an end-to-end walk could surface, pinned as regressions.

    Both sit between what ``strategy_service.deploy_version`` WRITES to
    ``strategy_deployments`` and what ``signal_service._deployment_facts`` READS from the
    same row. Each unit suite on either side was self-consistent: task 8.2's asserts the
    columns the writer produces, and task 10.3's drives the signal path from a hand-built
    row that happens to carry the names the reader wants. Nothing compared the two until
    this chain did.

    Requirement 15.2 requires a Signal to be traceable to the market and the venue that
    produced it, and ``signals.exchange_id`` is ``VARCHAR(50) NOT NULL`` in migration 003 -
    so with either gap open, ``mint_signal`` refuses every candidate with
    ``SIGNAL_ATTRIBUTION_INCOMPLETE`` and a deployed strategy can never trade.
    """

    def test_the_venue_is_persisted_on_the_deployment_row(self, chain):
        """GAP 1. ``deploy_version`` wrote a literal ``None`` into ``exchange_id``.

        The comment justifying it said the column was "migration 001's UUID FK into an
        ``exchanges`` table that migration 003 records as absent". Migration 003 does
        record that table as absent - and in the same statement it REDEFINES
        ``strategy_deployments.exchange_id`` as ``VARCHAR(50)`` with no foreign key, i.e.
        as the venue name. So the column the writer was avoiding is exactly the column the
        venue belongs in, and it is where ``_deployment_facts`` looks for it.
        """
        row = chain.world.deployment_row() or {}
        assert row.get("exchange_id") == SANDBOX_VENUE, (
            "the deployment row names no venue, so no Signal generated from it can say "
            "which exchange it belongs to (Requirement 15.2) and signals.exchange_id "
            "(NOT NULL) cannot be written"
        )

    def test_the_deployment_rows_market_is_readable_by_the_signal_path(self, chain):
        """GAP 2. The writer's column is ``exchange_symbol``; the reader wanted ``symbol``.

        ``_deployment_facts`` aliases every other fact it reads (``id``/``deployment_id``,
        ``version``/``strategy_version``, ``exchange_id``/``venue``/``exchange``), so the
        fix is the same additive alias for this one. Asserted here on the projection rather
        than on the row, because the projection is what the signal path consumes.
        """
        from backend_app.backend.signal_service import _deployment_facts

        facts = _deployment_facts(chain.world.deployment_row() or {})
        assert facts["symbol"] == SANDBOX_SYMBOL, (
            "the signal path cannot read the market off a deployment row that "
            "deploy_version wrote"
        )
        assert facts["venue"] == SANDBOX_VENUE
        assert facts["mode"] == "paper"

    def test_a_signal_can_be_minted_from_the_persisted_row_alone(self, chain):
        """The consequence, stated as the claim that actually matters.

        The ACTION-node output the live runtime hands the path happens to carry a symbol,
        so gap 2 alone was survivable; gap 1 was not, and no output carries a venue. This
        asserts the row is sufficient on its own, which is the property the two paths have
        to share.
        """
        from backend_app.backend.signal_service import mint_signal

        minted = mint_signal(
            chain.world.deployment_row() or {},
            {
                "decision": "BUY",
                "quantity": 0.25,
                "closure_ready": True,
                "risk_validation": {"passed": True},
            },
        )
        assert minted.symbol == SANDBOX_SYMBOL
        assert minted.venue == SANDBOX_VENUE
        assert minted.strategy_version == chain.world.version_label


# ══════════════════════════════════════════════════════════════════════════
# 10. THE SUITE LEAVES NOTHING BEHIND
# ══════════════════════════════════════════════════════════════════════════


def test_paper_mode_is_not_left_enabled():
    """The ``sandbox_paper_mode`` fixture's restore path, asserted rather than assumed.

    This test takes none of this package's fixtures, so it runs with whatever state the
    others left. A permissive execution flag leaking out of this package would make some
    unrelated test's safe-mode assumption silently false.
    """
    import os

    from backend_app.core.safety_config import ExecutionFlags, SafetyMonitor

    assert os.environ.get("VYOMQUANT_MODE") == "safe"
    assert ExecutionFlags.PAPER_TRADING_ENABLED is False
    assert ExecutionFlags.LIVE_TRADING_ENABLED is False
    assert ExecutionFlags.STRATEGY_SIGNAL_EXECUTION is False
    assert SafetyMonitor.check_execution_allowed("strategy_signal")
