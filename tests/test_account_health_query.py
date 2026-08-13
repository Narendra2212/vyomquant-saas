"""
tests/test_account_health_query.py

Regression and unit tests for QuestDB column alias in GET /api/risk/account-health.
Verifies:
1. SQL query contains 'total_exposure_usdt AS total_exposure' and no bare 'total_exposure' column reference.
2. Returned response contains real data mapped to standard contract keys: current_drawdown_pct, daily_pnl_pct, total_exposure.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from backend_app.main import app
from backend_app.core.dependencies import get_current_user


client = TestClient(app)


@pytest.mark.asyncio
async def test_account_health_query_column_alias():
    """
    Verify account_health query uses total_exposure_usdt AS total_exposure,
    passes proper SQL string to execute_query, and returns populated dict.
    """
    mock_execute = AsyncMock(return_value={
        "columns": [
            {"name": "current_drawdown_pct"},
            {"name": "daily_pnl_pct"},
            {"name": "total_exposure"}
        ],
        "dataset": [[0.02, -1.5, 1200.0]]
    })

    def mock_user():
        return {"id": "test_user_123", "email": "test@example.com"}

    app.dependency_overrides[get_current_user] = mock_user

    try:
        with patch("backend_app.core.state.app_state") as mock_app_state:
            mock_app_state.telemetry.execute_query = mock_execute

            response = client.get("/api/risk/account-health")

            assert response.status_code == 200
            data = response.json()
            assert data == {
                "current_drawdown_pct": 0.02,
                "daily_pnl_pct": -1.5,
                "total_exposure": 1200.0,
            }

            # Assert execute_query was called once
            mock_execute.assert_called_once()
            called_sql = mock_execute.call_args[0][0]

            # Assert SQL query structure
            assert "total_exposure_usdt AS total_exposure" in called_sql
            assert "SELECT current_drawdown_pct, daily_pnl_pct, total_exposure_usdt AS total_exposure FROM account_health" in called_sql
            # Assert no bare total_exposure reference before FROM clause
            select_part = called_sql.split("FROM")[0]
            assert "total_exposure_usdt AS total_exposure" in select_part
            assert "SELECT current_drawdown_pct, daily_pnl_pct, total_exposure " not in select_part

    finally:
        app.dependency_overrides.clear()
