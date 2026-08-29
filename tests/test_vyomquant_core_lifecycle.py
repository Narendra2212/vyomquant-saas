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


class TestVyomQuantCoreLifecycle:
    """Test suite for the end-to-end trading lifecycle."""

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
    async def test_step_4_signal_trace_router_parameter_binding(self):
        """Step 4: Verify Signal Trace API Router endpoints work without attribute errors."""
        from fastapi import FastAPI
        from httpx import AsyncClient, ASGITransport
        from backend_app.routers.signal_trace import router
        from backend_app.core.dependencies import get_current_user

        app = FastAPI()
        
        mock_user = {"id": "user-test-uuid-1234", "access_token": "mock-token"}
        app.dependency_overrides[get_current_user] = lambda: mock_user
        app.include_router(router, prefix="/api/signal-trace")

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
            assert "timeline" in sig_detail
            assert len(sig_detail["timeline"]) >= 4
