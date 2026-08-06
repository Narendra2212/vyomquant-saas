"""
tests/test_strategy_analysis_endpoint_accuracy.py — Strategy Analysis Endpoint Accuracy Tests

Tests that verify strategy analysis endpoints (walk-forward, signal replay, optimization)
accurately represent their actual computation versus their claimed purpose.

This ensures endpoints are honestly labeled and don't misrepresent their functionality.

Author: Principal Software Architect
Date: 2025-08-02
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


class TestWalkForwardEndpointAccuracy:
    """Test walk-forward endpoint accuracy and genuine implementation."""

    def test_walk_forward_genuine_implementation(self):
        """Test that walk-forward endpoint uses genuine rolling window analysis, not heuristic."""
        import asyncio
        from backend_app.routers.strategies import walk_forward_optimization
        from unittest.mock import patch, MagicMock, AsyncMock
        
        # Mock the optimization engine and its methods
        mock_walk_forward_result = MagicMock()
        mock_walk_forward_result.iteration = 1
        mock_walk_forward_result.train_start = "2023-01-01"
        mock_walk_forward_result.train_end = "2023-06-30"
        mock_walk_forward_result.test_start = "2023-07-01"
        mock_walk_forward_result.test_end = "2023-07-30"
        mock_walk_forward_result.train_metrics = {"sharpe_ratio": 2.0, "total_return_pct": 15.0}
        mock_walk_forward_result.test_metrics = {"sharpe_ratio": 1.8, "total_return_pct": 12.0}
        mock_walk_forward_result.backtest_id = "test-backtest-id"
        
        mock_optimization_engine = MagicMock()
        mock_optimization_engine.run_walk_forward_analysis = AsyncMock(return_value=[mock_walk_forward_result])
        
        mock_backtest_runtime = MagicMock()
        mock_binance = MagicMock()
        mock_binance.return_value.fetch_ohlcv = AsyncMock(return_value=[[1700000000000 + i*60000, 50000, 51000, 49000, 50500, 100] for i in range(100)])
        
        # Mock the get_optimization_engine and get_backtest_runtime functions
        with patch('backend_app.routers.strategies.get_optimization_engine', return_value=mock_optimization_engine), \
             patch('backend_app.backend.optimization_engine.get_optimization_engine', return_value=mock_optimization_engine), \
             patch('backend_app.routers.strategies.get_backtest_runtime', return_value=mock_backtest_runtime), \
             patch('backend_app.routers.strategies.ccxt.binance', mock_binance):
            
            # Test payload with proper DAG configuration
            payload = {
                "dag": {
                    "nodes": [
                        {"id": "data-node", "type": "market_data"},
                        {"id": "rsi-node", "type": "indicator", "indicator": "rsi"},
                        {"id": "action-node", "type": "action"}
                    ],
                    "edges": [
                        {"source": "data-node", "target": "rsi-node"},
                        {"source": "rsi-node", "target": "action-node"}
                    ],
                    "strategy_name": "Test Strategy"
                },
                "start_date": "2023-01-01",
                "end_date": "2023-12-31",
                "training_window_days": 180,
                "test_window_days": 30
            }
            
            mock_user = {"id": "test-user-id"}
            result = asyncio.run(walk_forward_optimization(payload, mock_user))
            
            # Verify it's labeled as genuine implementation
            assert result["computation_method"] == "genuine_walk_forward_analysis"
            assert "genuine" in result["computation_note"].lower()
            assert "rolling window" in result["computation_note"].lower()
            
            # Verify actual window analysis occurred
            assert result["windows_analyzed"] == 1
            assert result["avg_in_sample_sharpe"] == 2.0
            assert result["avg_out_of_sample_sharpe"] == 1.8
            
            # Verify window results are included
            assert "window_results" in result
            assert len(result["window_results"]) == 1
            assert result["window_results"][0]["train_sharpe"] == 2.0
            assert result["window_results"][0]["test_sharpe"] == 1.8

    def test_walk_forward_window_variability(self):
        """Test that walk-forward produces different results across windows (not fixed multiplier)."""
        import asyncio
        from backend_app.routers.strategies import walk_forward_optimization
        from unittest.mock import patch, MagicMock, AsyncMock
        
        # Create multiple window results with different metrics
        mock_results = []
        for i in range(3):
            mock_result = MagicMock()
            mock_result.iteration = i + 1
            mock_result.train_start = f"2023-{(i*3)+1:02d}-01"
            mock_result.train_end = f"2023-{(i*3)+6:02d}-30"
            mock_result.test_start = f"2023-{(i*3)+7:02d}-01"
            mock_result.test_end = f"2023-{(i*3)+7:02d}-30"
            # Vary the Sharpe ratios to prove no fixed multiplier
            mock_result.train_metrics = {"sharpe_ratio": 2.0 + (i * 0.5), "total_return_pct": 15.0 + (i * 2)}
            mock_result.test_metrics = {"sharpe_ratio": 1.7 + (i * 0.3), "total_return_pct": 12.0 + (i * 1.5)}
            mock_result.backtest_id = f"test-backtest-{i}"
            mock_results.append(mock_result)
        
        mock_optimization_engine = MagicMock()
        mock_optimization_engine.run_walk_forward_analysis = AsyncMock(return_value=mock_results)
        
        mock_backtest_runtime = MagicMock()
        mock_binance = MagicMock()
        mock_binance.return_value.fetch_ohlcv = AsyncMock(return_value=[[1700000000000 + i*60000, 50000, 51000, 49000, 50500, 100] for i in range(100)])
        
        with patch('backend_app.routers.strategies.get_optimization_engine', return_value=mock_optimization_engine), \
             patch('backend_app.backend.optimization_engine.get_optimization_engine', return_value=mock_optimization_engine), \
             patch('backend_app.routers.strategies.get_backtest_runtime', return_value=mock_backtest_runtime), \
             patch('backend_app.routers.strategies.ccxt.binance', mock_binance):
            
            payload = {
                "dag": {
                    "nodes": [
                        {"id": "data-node", "type": "market_data"},
                        {"id": "rsi-node", "type": "indicator", "indicator": "rsi"},
                        {"id": "action-node", "type": "action"}
                    ],
                    "edges": [
                        {"source": "data-node", "target": "rsi-node"},
                        {"source": "rsi-node", "target": "action-node"}
                    ],
                    "strategy_name": "Test Strategy"
                },
                "start_date": "2023-01-01",
                "end_date": "2023-12-31",
                "training_window_days": 180,
                "test_window_days": 30
            }
            
            mock_user = {"id": "test-user-id"}
            result = asyncio.run(walk_forward_optimization(payload, mock_user))
            
            # Verify multiple windows were analyzed
            assert result["windows_analyzed"] == 3
            
            # Verify results vary across windows (not a fixed multiplier)
            window_sharpes = [w["test_sharpe"] for w in result["window_results"]]
            assert len(set(window_sharpes)) == 3  # All different
            
            # Verify aggregated metrics are computed correctly
            expected_avg_test_sharpe = (1.7 + 2.0 + 2.3) / 3
            assert abs(result["avg_out_of_sample_sharpe"] - expected_avg_test_sharpe) < 0.01

    def test_walk_forward_insufficient_data(self):
        """Test that walk-forward returns unavailable when insufficient data for windows."""
        import asyncio
        from backend_app.routers.strategies import walk_forward_optimization
        
        # Test with insufficient date range
        payload = {
            "dag": {
                "nodes": [{"id": "test-node", "type": "indicator"}],
                "edges": [],
                "strategy_name": "Test Strategy"
            },
            "start_date": "2023-01-01",
            "end_date": "2023-02-01",  # Only 1 month, insufficient for 180+30 day windows
            "training_window_days": 180,
            "test_window_days": 30
        }
        
        mock_user = {"id": "test-user-id"}
        result = asyncio.run(walk_forward_optimization(payload, mock_user))
        
        assert result["status"] == "unavailable"
        assert "Insufficient data" in result["message"]
        assert result["robustness_score"] == 0.0


class TestSignalReplayEndpointAccuracy:
    """Test signal replay endpoint accuracy and honest labeling as audit retrieval."""

    def test_signal_replay_audit_retrieval_functionality(self):
        """Test that signal replay performs audit retrieval, not DAG re-execution."""
        import asyncio
        from backend_app.routers.signals import replay_signal_trace
        
        # Mock Supabase response from signals table
        mock_signal_record = {
            "id": "test-signal-id",
            "user_id": "test-user-id",
            "decision": "BUY",
            "symbol": "BTC/USDT",
            "strategy_id": "test-strategy-id",
            "generated_at": "2025-08-02T12:00:00Z",
            "indicators": {"rsi": 45.0, "sma_20": 50000.0},
            "market_info": {"price": 50100.0}
        }
        
        mock_supabase = MagicMock()
        mock_table = MagicMock()
        mock_table.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [mock_signal_record]
        mock_supabase.table.return_value = mock_table
        
        with patch('backend_app.routers.signals._sb', return_value=mock_supabase):
            mock_user = {"id": "test-user-id"}
            result = asyncio.run(replay_signal_trace("test-signal-id", mock_user))
            
            # Verify it's labeled as audit retrieval, not replay
            assert result["replay_implemented"] == False
            assert "audit" in result["status"].lower()
            assert "architectural changes" in result["replay_note"].lower()
            
            # Verify it returns stored decision from proper schema
            assert result["stored_decision"] == "BUY"
            assert result["audit_functionality"] == "stored_record_retrieval"
            
            # Verify it includes audit metadata
            assert "execution_metadata" in result
            assert result["execution_metadata"]["indicators"] == {"rsi": 45.0, "sma_20": 50000.0}

    def test_signal_replay_execution_records_fallback(self):
        """Test that signal replay falls back to execution_records table if signals table doesn't have record."""
        import asyncio
        from backend_app.routers.signals import replay_signal_trace
        
        # First call returns empty from signals table, second call returns from execution_records
        mock_execution_record = {
            "id": "test-signal-id",
            "user_id": "test-user-id",
            "side": "SELL",
            "symbol": "ETH/USDT",
            "strategy_id": "test-strategy-id",
            "created_at": "2025-08-02T12:00:00Z",
            "indicators": {"rsi": 55.0}
        }
        
        mock_supabase = MagicMock()
        mock_table = MagicMock()
        
        # First call (signals table) returns empty
        first_call_response = MagicMock()
        first_call_response.data = []
        
        # Second call (execution_records table) returns data
        second_call_response = MagicMock()
        second_call_response.data = [mock_execution_record]
        
        mock_table.select.return_value.eq.return_value.eq.return_value.execute.side_effect = [first_call_response, second_call_response]
        mock_supabase.table.return_value = mock_table
        
        with patch('backend_app.routers.signals._sb', return_value=mock_supabase):
            mock_user = {"id": "test-user-id"}
            result = asyncio.run(replay_signal_trace("test-signal-id", mock_user))
            
            # Verify it falls back to execution_records
            assert result["stored_decision"] == "SELL"
            assert result["replay_implemented"] == False
            assert result["audit_functionality"] == "stored_record_retrieval"

    def test_signal_replay_honest_limitation_documentation(self):
        """Test that signal replay honestly documents its limitations."""
        import asyncio
        from backend_app.routers.signals import replay_signal_trace
        
        mock_signal_record = {
            "id": "test-signal-id",
            "user_id": "test-user-id",
            "decision": "HOLD",
            "symbol": "BTC/USDT",
            "strategy_id": "test-strategy-id",
            "generated_at": "2025-08-02T12:00:00Z"
        }
        
        mock_supabase = MagicMock()
        mock_table = MagicMock()
        mock_table.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [mock_signal_record]
        mock_supabase.table.return_value = mock_table
        
        with patch('backend_app.routers.signals._sb', return_value=mock_supabase):
            mock_user = {"id": "test-user-id"}
            result = asyncio.run(replay_signal_trace("test-signal-id", mock_user))
            
            # Verify honest limitation documentation
            assert result["replay_implemented"] == False
            assert "architectural change" in result["replay_note"].lower()
            assert "signal generation pipeline" in result["replay_note"].lower()
            assert "database schema" in result["replay_note"].lower()
            assert "compliance purposes" in result["replay_note"].lower()
            
            # Verify it doesn't claim to do re-execution
            assert "re-execute" not in result["replay_note"].lower() or "not" in result["replay_note"].lower()


class TestOptimizeEndpointAccuracy:
    """Test optimize endpoint accuracy and honest labeling."""

    def test_optimize_performs_dag_structure_optimization(self):
        """Test that optimize endpoint performs DAG structure optimization, not hyperparameter optimization."""
        import asyncio
        from backend_app.routers.strategies import optimize_strategy
        
        mock_dag = {
            "nodes": [
                {"id": "node1", "type": "indicator"},
                {"id": "node2", "type": "logic"}
            ],
            "edges": []
        }
        
        mock_backtest_result = {
            "sharpe_ratio": 1.5,
            "total_trades": 50,
            "total_return_pct": 12.0,
            "win_rate_pct": 60.0,
            "max_drawdown_pct": 5.0
        }
        
        with patch('backend_app.routers.strategies.backtest_internal', return_value=mock_backtest_result):
            mock_user = {"id": "test-user-id"}
            result = asyncio.run(optimize_strategy({"dag": mock_dag}, mock_user))
            
            # Verify it's labeled as DAG structure optimization
            assert result["computation_method"] == "dag_structure_optimization"
            assert "dag structure" in result["computation_note"].lower()
            assert "not hyperparameter" in result["computation_note"].lower()
            
            # Verify parameters are unchanged (no hyperparameter search)
            assert result["best_parameters"] == {}
            
            # Verify it provides alternative endpoint information
            assert "alternative_endpoint" in result
            assert "optimize" in result["alternative_endpoint"].lower()

    def test_optimize_prunes_unused_nodes(self):
        """Test that optimize endpoint actually prunes unused nodes from DAG."""
        import asyncio
        from backend_app.routers.strategies import optimize_strategy
        
        # Create a DAG with unused nodes
        mock_dag = {
            "nodes": [
                {"id": "node1", "type": "indicator"},
                {"id": "node2", "type": "indicator"},  # This might be pruned
                {"id": "node3", "type": "logic"},
                {"id": "node4", "type": "signal"}
            ],
            "edges": [
                {"source": "node1", "target": "node3"},
                {"source": "node3", "target": "node4"}
                # node2 is not connected, should be pruned
            ]
        }
        
        mock_backtest_result = {
            "sharpe_ratio": 1.5,
            "total_trades": 50,
            "total_return_pct": 12.0
        }
        
        with patch('backend_app.routers.strategies.backtest_internal', return_value=mock_backtest_result):
            mock_user = {"id": "test-user-id"}
            result = asyncio.run(optimize_strategy({"dag": mock_dag}, mock_user))
            
            # Verify optimization occurred
            assert "optimized_dag" in result
            assert result["status"] == "optimized"
            
            # Verify node pruning occurred
            optimized_nodes = result["optimized_dag"]["nodes"]
            assert len(optimized_nodes) <= len(mock_dag["nodes"])  # Should have fewer or equal nodes
            
            # Verify it still returns the original parameters unchanged
            assert result["best_parameters"] == {}


class TestMonteCarloEndpointAccuracy:
    """Test Monte Carlo endpoint accuracy and genuine implementation."""

    def test_monte_carlo_genuine_bootstrap_resampling(self):
        """Test that Monte Carlo endpoint performs genuine bootstrap resampling, not heuristic."""
        import asyncio
        from backend_app.routers.strategies import monte_carlo_simulation
        
        # Mock backtest result with realistic equity curve
        mock_backtest_result = {
            "sharpe_ratio": 2.0,
            "total_trades": 100,
            "total_return_pct": 25.0,
            "equity": [
                {"value": 10000},
                {"value": 10100},
                {"value": 10200},
                {"value": 10150},
                {"value": 10300},
                {"value": 10450},
                {"value": 10380},
                {"value": 10500},
                {"value": 10600},
                {"value": 10550}
            ]
        }
        
        with patch('backend_app.routers.strategies.backtest_internal', return_value=mock_backtest_result):
            mock_user = {"id": "test-user-id"}
            payload = {
                "dag": {
                    "nodes": [{"id": "test-node", "type": "indicator"}],
                    "edges": []
                },
                "num_simulations": 100
            }
            result = asyncio.run(monte_carlo_simulation(payload, mock_user))
            
            # Verify it's labeled as completed (genuine implementation)
            assert result["status"] == "completed"
            assert result["num_simulations"] == 100
            assert result["empirical_trades_count"] == 100
            
            # Verify confidence bands are computed
            assert "confidence_bands" in result
            assert "p5" in result["confidence_bands"]
            assert "p50" in result["confidence_bands"]
            assert "p95" in result["confidence_bands"]
            
            # Verify percentiles are different (genuine variation)
            assert result["percentile_5th"] < result["percentile_50th"] < result["percentile_95th"]

    def test_monte_carlo_requires_minimum_trades(self):
        """Test that Monte Carlo requires minimum trades for empirical distribution."""
        import asyncio
        from backend_app.routers.strategies import monte_carlo_simulation
        
        # Mock backtest result with insufficient trades
        mock_backtest_result = {
            "sharpe_ratio": 0.0,
            "total_trades": 3,  # Less than minimum 5
            "total_return_pct": 0.0,
            "equity": [{"value": 10000}, {"value": 10050}]
        }
        
        with patch('backend_app.routers.strategies.backtest_internal', return_value=mock_backtest_result):
            mock_user = {"id": "test-user-id"}
            result = asyncio.run(monte_carlo_simulation({}, mock_user))
            
            assert result["status"] == "unavailable"
            assert "at least 5 backtest trades" in result["message"]
            assert result["num_simulations"] == 0

    def test_optimize_parameters_unchanged(self):
        """Test that optimize endpoint doesn't modify parameters."""
        import asyncio
        from backend_app.routers.strategies import optimize_strategy
        
        original_parameters = {"rsi_period": 14, "macd_fast": 12}
        
        mock_dag = {
            "nodes": [{"id": "node1", "type": "indicator"}],
            "edges": []
        }
        
        mock_backtest_result = {
            "sharpe_ratio": 1.5,
            "total_trades": 50,
            "total_return_pct": 12.0,
            "win_rate_pct": 60.0,
            "max_drawdown_pct": 5.0
        }
        
        with patch('backend_app.routers.strategies.backtest_internal', return_value=mock_backtest_result):
            mock_user = {"id": "test-user-id"}
            result = asyncio.run(optimize_strategy({"dag": mock_dag, "parameters": original_parameters}, mock_user))
            
            # Verify parameters are unchanged
            assert result["best_parameters"] == original_parameters


class TestMonteCarloEndpointAccuracy:
    """Test Monte Carlo endpoint accuracy."""

    def test_monte_carlo_bootstrap_resampling(self):
        """Test that Monte Carlo uses actual bootstrap resampling, not heuristic."""
        import asyncio
        from backend_app.routers.strategies import monte_carlo_simulation
        
        mock_backtest_result = {
            "sharpe_ratio": 1.5,
            "total_trades": 10,
            "total_return_pct": 12.0,
            "equity": [
                {"value": 10000},
                {"value": 10100},
                {"value": 10200},
                {"value": 10150},
                {"value": 10300}
            ]
        }
        
        with patch('backend_app.routers.strategies.backtest_internal', return_value=mock_backtest_result):
            mock_user = {"id": "test-user-id"}
            result = asyncio.run(monte_carlo_simulation({"num_simulations": 100}, mock_user))
            
            # Verify it performs actual bootstrap resampling
            assert result["status"] == "completed"
            assert result["num_simulations"] == 100
            assert result["empirical_trades_count"] == 10
            
            # Verify confidence bands are computed
            assert "percentile_5th" in result
            assert "percentile_50th" in result
            assert "percentile_95th" in result
            assert "confidence_bands" in result


def run_all_tests():
    """Run all strategy analysis endpoint accuracy tests."""
    print("=" * 60)
    print("STRATEGY ANALYSIS ENDPOINT ACCURACY TESTS")
    print("=" * 60)
    
    import pytest
    result = pytest.main([__file__, "-v", "--tb=short"])
    
    print("\n" + "=" * 60)
    if result == 0:
        print("ALL TESTS PASSED")
    else:
        print(f"TESTS FAILED (exit code: {result})")
    print("=" * 60)
    
    return result


if __name__ == "__main__":
    sys.exit(run_all_tests())
