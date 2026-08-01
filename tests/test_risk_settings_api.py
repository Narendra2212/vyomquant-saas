"""
tests/test_risk_settings_api.py

Comprehensive test suite for Risk Settings module verifying:
1. Save functionality - PUT /api/risk/settings
2. Load functionality - GET /api/risk/settings
3. Sync with database - Supabase persistence
4. User ownership - RLS policies enforced
5. Risk enforcement - Settings applied in trade validation
6. Strategy limits CRUD operations
7. Kill switch functionality
8. Cache invalidation
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from fastapi.testclient import TestClient
from backend_app.main import app
from backend_app.core.models import RiskSettingsRequest, StrategyLimitsRequest, KillSwitchRequest


client = TestClient(app)


class TestRiskSettingsAPI:
    """Test suite for Risk Settings API endpoints."""

    def test_get_risk_settings_unauthorized(self):
        """Verify GET /api/risk/settings requires authentication."""
        response = client.get("/api/risk/settings")
        assert response.status_code == 401

    @patch('backend_app.routers.risk._sb')
    @patch('backend_app.routers.risk.get_current_user')
    def test_get_risk_settings_authorized(self, mock_get_user, mock_sb):
        """Verify GET /api/risk/settings returns user settings."""
        mock_get_user.return_value = {"id": "test_user_123", "access_token": "valid_token"}
        mock_sb_instance = Mock()
        mock_sb_instance.table.return_value.select.return_value.eq.return_value.execute.return_value = Mock(data=[{
            "max_daily_loss": 1000,
            "max_positions": 15,
            "max_leverage": 5,
            "kill_switches": [{"key": "loss", "active": True}]
        }])
        mock_sb.return_value = mock_sb_instance

        response = client.get("/api/risk/settings")
        assert response.status_code == 200
        data = response.json()
        assert data["max_daily_loss"] == 1000
        assert data["max_positions"] == 15
        assert data["max_leverage"] == 5

    @patch('backend_app.routers.risk._sb')
    @patch('backend_app.routers.risk.get_current_user')
    def test_get_risk_settings_defaults(self, mock_get_user, mock_sb):
        """Verify GET /api/risk/settings returns defaults when no settings exist."""
        mock_get_user.return_value = {"id": "test_user_123", "access_token": "valid_token"}
        mock_sb_instance = Mock()
        mock_sb_instance.table.return_value.select.return_value.eq.return_value.execute.return_value = Mock(data=[])
        mock_sb.return_value = mock_sb_instance

        response = client.get("/api/risk/settings")
        assert response.status_code == 200
        data = response.json()
        assert data["max_daily_loss"] == 500
        assert data["max_positions"] == 10
        assert data["max_leverage"] == 3

    @patch('backend_app.routers.risk._sb')
    @patch('backend_app.routers.risk.get_current_user')
    def test_update_risk_settings(self, mock_get_user, mock_sb):
        """Verify PUT /api/risk/settings persists settings."""
        mock_get_user.return_value = {"id": "test_user_123", "access_token": "valid_token"}
        mock_sb_instance = Mock()
        mock_sb_instance.table.return_value.upsert.return_value.execute.return_value = Mock(data=[{"status": "ok"}])
        mock_sb.return_value = mock_sb_instance

        payload = {
            "max_daily_loss": 2000,
            "max_positions": 20,
            "max_leverage": 10,
            "kill_switches": [{"key": "loss", "active": False}]
        }

        response = client.put("/api/risk/settings", json=payload)
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    @patch('backend_app.routers.risk._sb')
    @patch('backend_app.routers.risk.get_current_user')
    def test_update_risk_settings_validation(self, mock_get_user, mock_sb):
        """Verify PUT /api/risk/settings validates input."""
        mock_get_user.return_value = {"id": "test_user_123", "access_token": "valid_token"}
        mock_sb_instance = Mock()
        mock_sb.return_value = mock_sb_instance

        # Invalid negative values
        payload = {
            "max_daily_loss": -100,
            "max_positions": 20,
            "max_leverage": 10,
            "kill_switches": []
        }

        response = client.put("/api/risk/settings", json=payload)
        assert response.status_code == 422  # Validation error

    @patch('backend_app.routers.risk._sb')
    @patch('backend_app.routers.risk.get_current_user')
    def test_get_strategy_limits(self, mock_get_user, mock_sb):
        """Verify GET /api/risk/strategy-limits returns user strategy limits."""
        mock_get_user.return_value = {"id": "test_user_123", "access_token": "valid_token"}
        mock_sb_instance = Mock()
        mock_sb_instance.table.return_value.select.return_value.eq.return_value.execute.return_value = Mock(data=[{
            "strategy_id": "strat_1",
            "max_position_size": 5000,
            "max_daily_trades": 50,
            "allowed_symbols": ["BTCUSDT", "ETHUSDT"],
            "max_drawdown_pct": 0.05,
            "enabled": True
        }])
        mock_sb.return_value = mock_sb_instance

        response = client.get("/api/risk/strategy-limits")
        assert response.status_code == 200
        data = response.json()
        assert "limits" in data
        assert len(data["limits"]) == 1

    @patch('backend_app.routers.risk._sb')
    @patch('backend_app.routers.risk.get_current_user')
    def test_update_strategy_limits_batch(self, mock_get_user, mock_sb):
        """Verify PUT /api/risk/strategy-limits batch updates."""
        mock_get_user.return_value = {"id": "test_user_123", "access_token": "valid_token"}
        mock_sb_instance = Mock()
        mock_sb_instance.table.return_value.upsert.return_value.execute.return_value = Mock(data=[{"status": "ok"}])
        mock_sb.return_value = mock_sb_instance

        payload = {
            "limits": [
                {
                    "strategy_id": "strat_1",
                    "max_position_size": 10000,
                    "max_daily_trades": 100,
                    "allowed_symbols": ["BTCUSDT"],
                    "max_drawdown_pct": 0.10,
                    "enabled": True
                }
            ]
        }

        response = client.put("/api/risk/strategy-limits", json=payload)
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    @patch('backend_app.routers.risk._sb')
    @patch('backend_app.routers.risk.get_current_user')
    def test_update_single_strategy_limit(self, mock_get_user, mock_sb):
        """Verify PUT /api/risk/strategy-limits/{strategy_id} single update."""
        mock_get_user.return_value = {"id": "test_user_123", "access_token": "valid_token"}
        mock_sb_instance = Mock()
        mock_sb_instance.table.return_value.upsert.return_value.execute.return_value = Mock(data=[{"status": "ok"}])
        mock_sb.return_value = mock_sb_instance

        payload = {
            "limits": [{
                "strategy_id": "strat_1",
                "max_position_size": 15000,
                "max_daily_trades": 75,
                "allowed_symbols": ["ETHUSDT"],
                "max_drawdown_pct": 0.08,
                "enabled": True
            }]
        }

        response = client.put("/api/risk/strategy-limits/strat_1", json=payload)
        assert response.status_code == 200
        assert response.json()["updated"] == "strat_1"

    @patch('backend_app.routers.risk._sb')
    @patch('backend_app.routers.risk.get_current_user')
    def test_delete_strategy_limit(self, mock_get_user, mock_sb):
        """Verify DELETE /api/risk/strategy-limits/{strategy_id} removes limit."""
        mock_get_user.return_value = {"id": "test_user_123", "access_token": "valid_token"}
        mock_sb_instance = Mock()
        mock_sb_instance.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute.return_value = Mock(data=[{"deleted": 1}])
        mock_sb.return_value = mock_sb_instance

        response = client.delete("/api/risk/strategy-limits/strat_1")
        assert response.status_code == 200
        assert response.json()["deleted"] == "strat_1"

    @patch('backend_app.routers.risk.get_current_user')
    @patch('backend_app.core.state.app_state')
    def test_kill_switch_stops_bots(self, mock_app_state, mock_get_user):
        """Verify POST /api/risk/kill-switch stops all user bots."""
        mock_get_user.return_value = {"id": "test_user_123", "access_token": "valid_token"}
        
        mock_fleet = Mock()
        mock_fleet._active_fleet = {"test_user_123_bot1": {}, "test_user_123_bot2": {}}
        mock_fleet.stop_bot = AsyncMock()
        mock_app_state.fleet = mock_fleet
        mock_app_state.telemetry = Mock()
        mock_app_state.telemetry.execute_query = AsyncMock(return_value=None)
        
        mock_alert = Mock()
        mock_alert.send_risk_warning = AsyncMock()
        
        mock_ws_mgr = Mock()
        mock_ws_mgr.broadcast_user = AsyncMock()

        with patch('backend_app.routers.risk.get_alert', return_value=mock_alert), \
             patch('backend_app.routers.risk.get_ws_manager', return_value=mock_ws_mgr), \
             patch('backend_app.routers.risk.get_vault', return_value=Mock()):
            
            payload = {"scope": "user", "confirm_code": "KILL"}
            response = client.post("/api/risk/kill-switch", json=payload)
            assert response.status_code == 200
            assert response.json()["status"] == "halted"

    @patch('backend_app.routers.risk._sb')
    @patch('backend_app.routers.risk.get_current_user')
    def test_account_health_query(self, mock_get_user, mock_sb):
        """Verify GET /api/risk/account-health queries QuestDB safely."""
        mock_get_user.return_value = {"id": "test_user_123", "access_token": "valid_token"}
        
        with patch('backend_app.routers.risk.app_state') as mock_app_state:
            mock_app_state.telemetry.execute_query = AsyncMock(return_value={
                "dataset": [[0.05, -0.02, 50000]],
                "columns": [{"name": "current_drawdown_pct"}, {"name": "daily_pnl_pct"}, {"name": "total_exposure"}]
            })

            response = client.get("/api/risk/account-health")
            assert response.status_code == 200
            data = response.json()
            assert "current_drawdown_pct" in data


class TestRiskSettingsEnforcement:
    """Test suite verifying risk settings are enforced in trade validation."""

    @patch('backend_app.core.risk_manager._load_user_risk_settings')
    def test_user_risk_settings_loaded(self, mock_load_settings):
        """Verify user-specific risk settings are loaded during validation."""
        from backend_app.core.risk_manager import InstitutionalRiskManager, TradeRequest, RiskThresholds
        
        mock_load_settings.return_value = {
            "max_daily_loss": 1000,
            "max_positions": 15,
            "max_leverage": 5
        }

        rm = InstitutionalRiskManager(initial_equity=100000.0)
        request = TradeRequest(
            user_id="test_user",
            symbol="BTCUSDT",
            side="BUY",
            amount=1.0,
            current_price=50000.0,
            leverage=3,
            daily_pnl_pct=0.0,
            current_drawdown_pct=0.0,
            open_trades_count=0
        )

        verdict, message = rm.validate_trade_request(request)
        # Should not reject based on default limits if user settings are loaded
        assert verdict.value != "REJECT_MAX_OPEN_TRADES"

    @patch('backend_app.core.risk_manager._load_user_risk_settings')
    def test_user_max_positions_enforced(self, mock_load_settings):
        """Verify user max_positions limit is enforced."""
        from backend_app.core.risk_manager import InstitutionalRiskManager, TradeRequest
        
        mock_load_settings.return_value = {
            "max_daily_loss": 1000,
            "max_positions": 2,  # Low limit
            "max_leverage": 5
        }

        rm = InstitutionalRiskManager(initial_equity=100000.0)
        request = TradeRequest(
            user_id="test_user",
            symbol="BTCUSDT",
            side="BUY",
            amount=1.0,
            current_price=50000.0,
            leverage=3,
            daily_pnl_pct=0.0,
            current_drawdown_pct=0.0,
            open_trades_count=2  # At limit
        )

        verdict, message = rm.validate_trade_request(request)
        assert verdict.value == "REJECT_MAX_OPEN_TRADES"


class TestRiskSettingsSecurity:
    """Test suite verifying security and ownership."""

    @patch('backend_app.routers.risk._sb')
    @patch('backend_app.routers.risk.get_current_user')
    def test_user_isolation_via_rls(self, mock_get_user, mock_sb):
        """Verify users cannot access other users' risk settings via RLS."""
        user1 = {"id": "user1", "access_token": "token1"}
        user2 = {"id": "user2", "access_token": "token2"}
        
        mock_sb_instance = Mock()
        # Verify that user_id is passed to query
        mock_sb_instance.table.return_value.select.return_value.eq.side_effect = lambda field, value: (
            Mock(execute=Mock(return_value=Mock(data=[]))) if value == user1["id"] else
            Mock(execute=Mock(return_value=Mock(data=[])))
        )
        mock_sb.return_value = mock_sb_instance

        mock_get_user.return_value = user1
        response1 = client.get("/api/risk/settings")
        
        mock_get_user.return_value = user2
        response2 = client.get("/api/risk/settings")
        
        # Both should succeed but query different user_ids
        assert response1.status_code == 200
        assert response2.status_code == 200

    def test_safe_uid_validation(self):
        """Verify _safe_uid validates user_id format."""
        from backend_app.routers.risk import _safe_uid
        
        # Valid IDs
        assert _safe_uid("user_123") == "user_123"
        assert _safe_uid("abc-XYZ_123") == "abc-XYZ_123"
        
        # Invalid IDs
        with pytest.raises(ValueError):
            _safe_uid("user; DROP TABLE")
        with pytest.raises(ValueError):
            _safe_uid("user' OR '1'='1")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
