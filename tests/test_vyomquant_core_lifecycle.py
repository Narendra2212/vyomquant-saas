"""
tests/test_vyomquant_core_lifecycle.py

Comprehensive test suite verifying the complete VyomQuant core trading lifecycle:
Strategy Builder -> Saved Strategy -> Strategies Page -> (Backtest & Live Deploy) ->
Live Market Data -> Strategy Engine -> Buy/Sell Signal -> Order Execution -> Signal Trace.
"""

import pytest
import asyncio
from decimal import Decimal
from datetime import datetime, timezone
from uuid import uuid4
import pandas as pd
import numpy as np
from unittest.mock import MagicMock, AsyncMock, patch

from backend_app.backend.strategy_compiler import StrategyCompiler
from backend_app.backend.dag_engine import DAGEngine
from backend_app.backend.signal_service import SignalService, SignalStatus, SignalDecision, get_signal_service
from backend_app.core.risk_manager import RiskManager, RiskVerdict, TradeRequest
from backend_app.core.execution_engine import ExecutionEngine
from backend_app.routers.strategies import backtest_internal


CANONICAL_LIFECYCLE_BLUEPRINT = {
    "schema_version": 2,
    "strategy_name": "Lifecycle SMA Strategy",
    "symbols": ["BTCUSDT"],
    "timeframe": "1h",
    "nodes": [
        {
            "id": "node_feed",
            "block_id": "ohlcv_feed",
            "category": "data",
            "params": {"symbol": "BTC/USDT", "timeframe": "1h", "market_type": "spot", "mode": "streaming"}
        },
        {
            "id": "node_ema",
            "block_id": "ema",
            "category": "indicator",
            "params": {"window": 20, "source": "close"}
        },
        {
            "id": "node_const",
            "block_id": "constant",
            "category": "math",
            "params": {"value": 50000.0}
        },
        {
            "id": "node_gt",
            "block_id": "gt",
            "category": "logic",
            "params": {}
        },
        {
            "id": "node_action",
            "block_id": "action_buy_market",
            "category": "action",
            "params": {"quantity_type": "percent_of_equity", "quantity": 0.25}
        }
    ],
    "edges": [
        {"id": "edge_feed_to_ema", "source": "node_feed", "source_port": "close", "target": "node_ema", "target_port": "series"},
        {"id": "edge_ema_to_gt", "source": "node_ema", "source_port": "value", "target": "node_gt", "target_port": "left"},
        {"id": "edge_const_to_gt", "source": "node_const", "source_port": "value", "target": "node_gt", "target_port": "right"},
        {"id": "edge_gt_to_action", "source": "node_gt", "source_port": "out", "target": "node_action", "target_port": "signal"}
    ]
}


class _Result:
    """The two attributes ``signal_service`` reads off a PostgREST response."""

    def __init__(self, data):
        self.data = data
        self.error = None


class _UnreachableQuery:
    """Chains any builder call, then fails at ``execute()`` - an unresolvable host.

    Exactly what the DEV_MODE client in this offline suite already did for every table:
    the query builds fine and the failure arrives when the statement is awaited, so every
    caller takes the same in-process ``SignalService._local_signals`` fallback it takes
    today. ``execute`` is a real method, so ``__getattr__`` never shadows it.
    """

    def __getattr__(self, name):
        def _chain(*args, **kwargs):
            return self

        return _chain

    async def execute(self):
        raise OSError("[Errno 11001] getaddrinfo failed")


class _StrategyOwnerQuery(_UnreachableQuery):
    """Answers the ONE ownership read: ``select user_id from strategies where id = ?``."""

    def __init__(self, owner_id, strategy_id):
        self._owner_id = owner_id
        self._strategy_id = strategy_id
        self._asked_for = None

    def eq(self, column, value):
        if column == "id":
            self._asked_for = value
        return self

    async def execute(self):
        # A different strategy answers nothing, which is how RLS answers a non-owner.
        if self._asked_for != self._strategy_id:
            return _Result([])
        return _Result([{"user_id": self._owner_id}])


class _OwnershipOnlySupabase:
    """A stand-in client that can answer strategy ownership and nothing else.

    WHY THIS EXISTS (task 29.4, Requirement 23.3)
        Task 29.4 of the marketplace-subscriptions-paper-trading spec added
        ``signal_service.resolve_signal_viewer_role``, which decides ``owner`` vs
        ``subscriber`` server-side by reading ``strategies.user_id`` for the signal's
        ``strategy_id`` through the caller's own client, and FAILS CLOSED to the
        subscriber-safe projection when that read cannot be answered. The subscriber
        projection deliberately omits ``timeline`` (it embeds ``indicators``,
        ``market_info`` and ``risk_reason``).

        This offline smoke test holds a DEV_MODE client whose host does not resolve, so
        the ownership read raised, the role resolved to ``subscriber``, and ``timeline``
        was withheld. That was the production contract working as specified, NOT a bug -
        the test simply asserted the OWNER projection without ever establishing the
        ownership it assumed.

        So the test establishes it here, at the one seam the read goes through: this
        client answers the single ``strategies`` select on the strategy id with a row
        whose ``user_id`` IS the authenticated caller, and behaves exactly as the
        unreachable DEV_MODE client did for every other table. The role is still resolved
        by production code comparing two identities; nothing is loosened.

    RLS IS NOT EMULATED, DELIBERATELY
        The ``strategies`` read answers whoever asks, so a handler that trusted the mere
        SUCCESS of the read instead of comparing ``user_id`` would still be caught here.
    """

    def __init__(self, owner_id, strategy_id):
        self.owner_id = owner_id
        self.strategy_id = strategy_id
        self.strategy_reads = 0

    def table(self, name):
        if name == "strategies":
            self.strategy_reads += 1
            return _StrategyOwnerQuery(self.owner_id, self.strategy_id)
        return _UnreachableQuery()


class TestVyomQuantCoreLifecycle:
    """Test suite for the end-to-end trading lifecycle.

    RECORDED OBSERVATIONS, NOT ASSERTED HERE (candidate follow-ups, no behaviour change)
        1. On ``resolve_signal_viewer_role``'s read-failure branch the detail envelope's
           ``degraded`` key stays ``None``, because ``_lifecycle_degradation`` reports only
           whether migration 005b is applied. An INFRASTRUCTURE failure that forced the
           subscriber projection is therefore indistinguishable, on the wire, from a
           genuine subscriber view - an "absence vs failure" gap in the sense of
           Requirement 28.5.
        2. ``resolve_signal_viewer_role``'s ``sb is None`` exemption justifies itself by
           the ROW'S PROVENANCE (an in-process ``_local_signals`` row, already scoped to
           the caller) but TESTS the client's absence. This test is precisely the case
           where the justification holds and the condition does not: a local in-process
           signal read through a non-``None`` but unreachable client.
    """

    def test_step_1_strategy_compiler_canonical_dag(self):
        """Step 1: Strategy Builder compiles canonical DAG into an executable plan."""
        compiler = StrategyCompiler()
        plan = compiler.compile_plan(CANONICAL_LIFECYCLE_BLUEPRINT)

        assert plan.schema_version == 2
        assert len(plan.execution_order) == 5
        assert plan.dag_hash != ""
        assert len(plan.node_index) == 5
        assert len(plan.action_nodes) == 1
        assert "node_action" in plan.action_nodes

        # Check topological execution order
        assert plan.execution_order[0] in ("node_feed", "node_const")
        assert plan.execution_order[-1] == "node_action"

    def test_step_2_backtesting_engine_dag_execution(self):
        """Step 2: Backtester runs DAG simulation and produces complete performance metrics."""
        payload = {
            "dag": {
                "nodes": [
                    {"id": "node_feed", "type": "market_data", "category": "market_data"},
                    {"id": "node_sma", "type": "indicator", "category": "indicator", "params": {"indicator_type": "sma", "period": 10}},
                    {"id": "node_logic", "type": "logic", "category": "logic", "params": {"operator": "gt", "threshold": 0}},
                    {"id": "node_action", "type": "action", "category": "action", "params": {"action_type": "buy"}}
                ],
                "edges": [
                    {"source": "node_feed", "target": "node_sma"},
                    {"source": "node_sma", "target": "node_logic"},
                    {"source": "node_logic", "target": "node_action"}
                ],
                "symbols": ["BTCUSDT"],
                "timeframe": "1h"
            },
            "initial_capital": 10000.0,
            "trade_size_pct": 0.1,
            "stop_loss_pct": 0.02,
            "take_profit_pct": 0.04,
            "symbols": ["BTCUSDT"],
            "timeframe": "1h",
            "sync": True
        }

        result = backtest_internal(payload)

        assert "total_return_pct" in result
        assert "win_rate_pct" in result
        assert "max_drawdown_pct" in result
        assert "total_trades" in result
        assert "profit_factor" in result
        assert "sharpe_ratio" in result
        assert "sortino_ratio" in result
        assert "calmar_ratio" in result
        assert "equity" in result
        assert isinstance(result["equity"], list)
        assert len(result["equity"]) > 0
        assert result["execution_mode"] == "dag"
        assert result["dag_results"]["nodes_count"] == 4

    @pytest.mark.asyncio
    async def test_step_3_signal_service_audit_lifecycle(self):
        """Step 3: Signal Trace Service captures complete audit lifecycle from decision to execution."""
        service = SignalService()
        user = {"id": "user-test-uuid-1234"}
        strategy_id = "strat-lifecycle-001"

        # 3.1 Signal Generation
        sig = await service.create_signal(
            user=user,
            strategy_id=strategy_id,
            strategy_version="1.0.0",
            deployment_id="dep-live-001",
            exchange_id="binance",
            symbol="BTC/USDT",
            timeframe="1h",
            worker_id="worker_live_1",
            decision=SignalDecision.BUY,
            indicators={"sma_fast": 65000.0, "sma_slow": 64200.0, "rsi": 58.4},
            market_info={"price": 65100.0, "volume": 120.5, "timestamp": 1700000000000},
            ml_info={"confidence": 0.88}
        )

        assert sig["id"] is not None
        assert sig["status"] == SignalStatus.PENDING
        assert sig["decision"] == SignalDecision.BUY
        signal_id = sig["id"]

        # 3.2 Risk Evaluation
        updated_risk = await service.update_risk_decision(
            user=user,
            signal_id=signal_id,
            risk_passed=True,
            risk_reason="Position within 2% account equity risk limit",
            position_size=0.15,
            capital=10000.0,
            exposure=9765.0,
            expected_loss=200.0,
            expected_reward=400.0,
            drawdown_check=True
        )

        assert updated_risk["status"] == SignalStatus.ACCEPTED
        assert updated_risk["risk_passed"] is True
        assert updated_risk["position_size"] == 0.15

        # 3.3 Order Creation & Placement
        order_update = await service.update_order(
            user=user,
            signal_id=signal_id,
            order_id="exec_order_9988",
            exchange_order_id="binance_ord_112233",
            order_status="FILLED",
            quantity=0.15,
            filled=0.15,
            remaining=0.0,
            average_price=65100.0,
            fees=9.76,
            slippage=0.0005,
            latency_ms=12.5
        )

        assert order_update["status"] == SignalStatus.EXECUTED
        assert order_update["order_id"] == "exec_order_9988"
        assert order_update["filled"] == 0.15

        # 3.4 Execution & PnL Recording
        exec_update = await service.update_execution(
            user=user,
            signal_id=signal_id,
            trade_id="trade_btc_5544",
            pnl=250.0,
            realized_pnl=250.0
        )

        assert exec_update["trade_id"] == "trade_btc_5544"
        assert exec_update["pnl"] == 250.0

        # 3.5 Timeline Retrieval
        timeline = await service.get_signal_timeline(user, signal_id)
        events = [e["event"] for e in timeline]

        assert "SIGNAL_GENERATED" in events
        assert "RISK_EVALUATED" in events
        assert "ORDER_CREATED" in events
        assert "EXCHANGE_RESPONSE" in events
        assert "EXECUTED" in events

        # 3.6 List & Filter Signals
        listed = await service.list_signals(user, strategy_id=strategy_id, decision="BUY")
        assert len(listed) >= 1
        assert listed[0]["id"] == signal_id

        # 3.7 Export Signals (JSON & CSV)
        json_export = await service.export_signals(user, {"strategy_id": strategy_id}, format="json")
        assert signal_id in json_export

        csv_export = await service.export_signals(user, {"strategy_id": strategy_id}, format="csv")
        assert "trade_btc_5544" in csv_export or signal_id in csv_export

    @pytest.mark.asyncio
    async def test_step_4_signal_trace_router_parameter_binding(self, monkeypatch):
        """Step 4: Verify Signal Trace API Router endpoints work without attribute errors.

        The detail assertions at the end are the OWNER's projection, so the caller has to
        BE the owner: task 29.4 resolves that role from ``strategies.user_id``, and
        Requirement 23.3 withholds ``timeline`` when the read cannot be answered. See
        :class:`_OwnershipOnlySupabase` for why the DEV_MODE client cannot answer it and
        what is substituted.
        """
        from fastapi import FastAPI
        from httpx import AsyncClient, ASGITransport
        from backend_app.routers.signal_trace import router
        from backend_app.core.dependencies import get_current_user
        from backend_app.backend import signal_service as signal_service_module

        app = FastAPI()
        
        mock_user = {"id": "user-test-uuid-1234", "access_token": "mock-token"}
        app.dependency_overrides[get_current_user] = lambda: mock_user
        app.include_router(router, prefix="/api/signal-trace")

        # The one seam every SignalService read goes through (``_get_supabase`` calls it),
        # so patching it once makes strategy ownership answerable for every endpoint below
        # without any handler being aware of a test. monkeypatch restores it, which matters
        # because ``get_signal_service`` hands out a process singleton.
        ownership_client = _OwnershipOnlySupabase(mock_user["id"], "strat-router-test")
        monkeypatch.setattr(
            signal_service_module,
            "create_request_supabase_async",
            lambda token=None: ownership_client,
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Test POST /api/signal-trace/signals
            create_payload = {
                "strategy_id": "strat-router-test",
                "strategy_version": "1.0.0",
                "deployment_id": "dep-001",
                "exchange_id": "binance",
                "symbol": "ETH/USDT",
                "timeframe": "15m",
                "worker_id": "w_eth_1",
                "decision": "BUY",
                "indicators": {"rsi": 42.0},
                "market_info": {"price": 3200.0}
            }
            res_create = await client.post("/api/signal-trace/signals", json=create_payload)
            assert res_create.status_code == 200, res_create.text
            data = res_create.json()
            assert "id" in data
            sig_id = data["id"]

            # Test PUT /api/signal-trace/signals/{id}/risk
            risk_payload = {
                "risk_passed": True,
                "risk_reason": "Approved by risk engine",
                "position_size": 1.5,
                "capital": 15000.0
            }
            res_risk = await client.put(f"/api/signal-trace/signals/{sig_id}/risk", json=risk_payload)
            assert res_risk.status_code == 200, res_risk.text

            # Test PUT /api/signal-trace/signals/{id}/order
            order_payload = {
                "order_id": "ord_12345",
                "exchange_order_id": "ex_ord_67890",
                "order_status": "FILLED",
                "quantity": 1.5,
                "filled": 1.5,
                "remaining": 0.0,
                "average_price": 3200.0,
                "fees": 4.8,
                "slippage": 0.0002,
                "latency_ms": 11.2
            }
            res_order = await client.put(f"/api/signal-trace/signals/{sig_id}/order", json=order_payload)
            assert res_order.status_code == 200, res_order.text

            # Test PUT /api/signal-trace/signals/{id}/execution
            exec_payload = {
                "trade_id": "tr_9999",
                "pnl": 75.0,
                "realized_pnl": 75.0
            }
            res_exec = await client.put(f"/api/signal-trace/signals/{sig_id}/execution", json=exec_payload)
            assert res_exec.status_code == 200, res_exec.text

            # Test GET /api/signal-trace/signals
            res_list = await client.get("/api/signal-trace/signals")
            assert res_list.status_code == 200
            list_data = res_list.json()
            assert "signals" in list_data
            assert len(list_data["signals"]) >= 1

            # Test GET /api/signal-trace/signals/export (static route above param route)
            res_export = await client.get("/api/signal-trace/signals/export?format=json")
            assert res_export.status_code == 200
            assert sig_id in res_export.text

            # Test GET /api/signal-trace/signals/{id}
            res_get = await client.get(f"/api/signal-trace/signals/{sig_id}")
            assert res_get.status_code == 200
            sig_detail = res_get.json()
            assert "signal" in sig_detail
            # The OWNER's envelope - task 29.4 resolved the role by comparing the caller
            # against the ``strategies.user_id`` the client above answered with. ``trace``
            # is owner-only (the subscriber envelope's four keys do not include it, and it
            # is the one that carries the strategy's own evaluation), so its presence is a
            # positive marker for the role and not merely the absence of withholding. Note
            # the owner envelope has no ``viewer_role`` key at all - only the subscriber's.
            assert "trace" in sig_detail, sig_detail
            assert "timeline" in sig_detail
            assert len(sig_detail["timeline"]) >= 4
            # The ownership question was actually ASKED, so this test would fail again if
            # the role stopped being resolved server-side from ``strategies``.
            assert ownership_client.strategy_reads >= 1
