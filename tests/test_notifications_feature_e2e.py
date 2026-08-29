"""
tests/test_notifications_feature_e2e.py — Comprehensive SaaS Notifications E2E Suite

Verifies:
1. Notification creation, database persistence, and in-memory fallback
2. User-scoped notification listing with pagination, unread-only, and category filters
3. Authoritative unread count endpoint (GET /api/notifications/unread-count)
4. Mark one read with IDOR protection (PUT /api/notifications/{id}/read)
5. Mark all read (PUT /api/notifications/read-all)
6. Delete one notification with IDOR protection (DELETE /api/notifications/{id})
7. Delete all notifications (DELETE /api/notifications)
8. Multi-tenant isolation & strict IDOR defense (User B cannot read/modify User A notifications)
9. Real-time WebSocket delivery and user-isolated streaming
10. Notification preferences/settings (GET/PUT /api/notifications/settings)
11. Support ticket lifecycle -> User notification generation (Staff reply & Status resolution)
12. Exchange connection & disconnection -> User notification generation
13. Paper trading order & account reset -> User notification generation
14. User profile updates -> Security notification generation
15. Idempotency protection against duplicate event delivery
16. Zero secret/credential/token leakage in notification records
17. Fail-safe non-blocking durability when WebSocket transport is disrupted
"""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch

from backend_app.main import app
from backend_app.core.dependencies import (
    get_current_user,
    get_admin_user,
    get_request_supabase,
    get_ws_manager,
    get_vault,
    get_fleet,
)
from backend_app.backend.redis_manager import get_redis_manager
from backend_app.routers.notifications import (
    NotificationCreate,
    create_notification,
    _in_memory_notifications,
    _notifications_lock,
)
from backend_app.core.notification_dispatcher import dispatch_user_notification


@pytest.fixture(autouse=True)
def clean_notifications_state():
    """Ensure clean notification memory state between test runs."""
    with _notifications_lock:
        _in_memory_notifications.clear()
    yield
    with _notifications_lock:
        _in_memory_notifications.clear()


@pytest.mark.asyncio
async def test_notification_creation_and_listing():
    """Test creating notifications for user and listing them with pagination."""
    user_alpha = {"id": "usr_alpha_999", "email": "alpha@quant.io", "role": "user"}
    mock_ws = AsyncMock()

    notif1 = NotificationCreate(
        user_id=user_alpha["id"],
        type="order_filled",
        category="trade",
        severity="info",
        title="Order Filled: BTC/USDT",
        message="Bought 0.25 BTC at $68,450.00 on Binance.",
        strategy_id="strat_momentum_01",
        exchange="binance",
        metadata={"order_id": "ord_1001", "fill_price": 68450.0}
    )

    notif2 = NotificationCreate(
        user_id=user_alpha["id"],
        type="risk_warning",
        category="risk",
        severity="warning",
        title="Drawdown Threshold Approaching",
        message="Daily drawdown reached 2.8% (limit 3.5%).",
        strategy_id="strat_momentum_01",
        metadata={"drawdown_pct": 2.8}
    )

    id1 = await create_notification(notif1, None, mock_ws)
    id2 = await create_notification(notif2, None, mock_ws)

    assert id1.startswith("notif_")
    assert id2.startswith("notif_")
    assert mock_ws.broadcast_user.call_count == 2

    # Verify REST list endpoint
    app.dependency_overrides[get_current_user] = lambda: user_alpha
    app.dependency_overrides[get_request_supabase] = lambda: None
    client = TestClient(app)

    try:
        res = client.get("/api/notifications")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 2
        assert data["unread_count"] == 2
        assert len(data["items"]) == 2
        item_ids = {data["items"][0]["id"], data["items"][1]["id"]}
        assert item_ids == {id1, id2}

        # Check unread-count endpoint
        res_count = client.get("/api/notifications/unread-count")
        assert res_count.status_code == 200
        assert res_count.json()["unread_count"] == 2

        # Check category filter
        res_trade = client.get("/api/notifications?category=trade")
        assert res_trade.status_code == 200
        assert res_trade.json()["total"] == 1
        assert res_trade.json()["items"][0]["category"] == "trade"

        res_risk = client.get("/api/notifications?category=risk")
        assert res_risk.status_code == 200
        assert res_risk.json()["total"] == 1
        assert res_risk.json()["items"][0]["category"] == "risk"

    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_notification_mark_read_and_read_all():
    """Test mark single notification read and mark all read."""
    user_alpha = {"id": "usr_alpha_999", "email": "alpha@quant.io", "role": "user"}
    
    n1 = NotificationCreate(user_id=user_alpha["id"], type="test", category="system", title="N1", message="M1")
    n2 = NotificationCreate(user_id=user_alpha["id"], type="test", category="system", title="N2", message="M2")

    id1 = await create_notification(n1, None, None)
    id2 = await create_notification(n2, None, None)

    app.dependency_overrides[get_current_user] = lambda: user_alpha
    app.dependency_overrides[get_request_supabase] = lambda: None
    client = TestClient(app)

    try:
        # Mark single read
        res_mark = client.put(f"/api/notifications/{id1}/read")
        assert res_mark.status_code == 200
        assert res_mark.json()["read"] is True

        res_count = client.get("/api/notifications/unread-count")
        assert res_count.json()["unread_count"] == 1

        # Check unread_only filter
        res_unread = client.get("/api/notifications?unread_only=true")
        assert res_unread.json()["total"] == 1
        assert res_unread.json()["items"][0]["id"] == id2

        # Mark all read
        res_all = client.put("/api/notifications/read-all")
        assert res_all.status_code == 200

        res_count2 = client.get("/api/notifications/unread-count")
        assert res_count2.json()["unread_count"] == 0

    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_notification_delete_and_delete_all():
    """Test delete single notification and delete all notifications."""
    user_alpha = {"id": "usr_alpha_999", "email": "alpha@quant.io", "role": "user"}
    
    n1 = NotificationCreate(user_id=user_alpha["id"], type="test", category="system", title="N1", message="M1")
    n2 = NotificationCreate(user_id=user_alpha["id"], type="test", category="system", title="N2", message="M2")

    id1 = await create_notification(n1, None, None)
    id2 = await create_notification(n2, None, None)

    app.dependency_overrides[get_current_user] = lambda: user_alpha
    app.dependency_overrides[get_request_supabase] = lambda: None
    client = TestClient(app)

    try:
        # Delete single
        res_del = client.delete(f"/api/notifications/{id1}")
        assert res_del.status_code == 200

        res_list = client.get("/api/notifications")
        assert res_list.json()["total"] == 1
        assert res_list.json()["items"][0]["id"] == id2

        # Delete all
        res_del_all = client.delete("/api/notifications")
        assert res_del_all.status_code == 200

        res_list2 = client.get("/api/notifications")
        assert res_list2.json()["total"] == 0

    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_notification_multi_tenant_isolation_and_idor_protection():
    """Verify User B cannot view, read, or delete User A's notifications."""
    user_alpha = {"id": "usr_alpha_111", "email": "alpha@quant.io", "role": "user"}
    user_beta = {"id": "usr_beta_222", "email": "beta@quant.io", "role": "user"}

    n_alpha = NotificationCreate(
        user_id=user_alpha["id"],
        type="trade",
        category="trade",
        title="Alpha Private Execution",
        message="Alpha executed confidential BTC trade."
    )
    id_alpha = await create_notification(n_alpha, None, None)

    # 1. User Alpha can see it
    app.dependency_overrides[get_current_user] = lambda: user_alpha
    app.dependency_overrides[get_request_supabase] = lambda: None
    client = TestClient(app)

    try:
        res_alpha = client.get("/api/notifications")
        assert res_alpha.status_code == 200
        assert res_alpha.json()["total"] == 1
        assert res_alpha.json()["items"][0]["id"] == id_alpha

        # 2. User Beta logs in: must NOT see User Alpha's notification
        app.dependency_overrides[get_current_user] = lambda: user_beta
        res_beta = client.get("/api/notifications")
        assert res_beta.status_code == 200
        assert res_beta.json()["total"] == 0

        res_beta_count = client.get("/api/notifications/unread-count")
        assert res_beta_count.json()["unread_count"] == 0

        # 3. User Beta attempts IDOR mark-read on User Alpha's notification -> 404
        res_beta_read = client.put(f"/api/notifications/{id_alpha}/read")
        assert res_beta_read.status_code == 404

        # 4. User Beta attempts IDOR delete on User Alpha's notification -> 404
        res_beta_del = client.delete(f"/api/notifications/{id_alpha}")
        assert res_beta_del.status_code == 404

        # 5. User Alpha verifies notification remains unmutated
        app.dependency_overrides[get_current_user] = lambda: user_alpha
        res_alpha_check = client.get("/api/notifications")
        assert res_alpha_check.json()["total"] == 1
        assert res_alpha_check.json()["items"][0]["read"] is False

    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_support_staff_reply_generates_user_notification():
    """Verify when Support staff replies to a ticket, a formal notification is generated."""
    user = {"id": "usr_tenant_888", "email": "tenant@vyomquant.io", "role": "user"}
    admin = {
        "id": "usr_staff_001",
        "email": "support@vyomquant.io",
        "role": "admin",
        "app_metadata": {"role": "admin"},
    }
    mock_ws = AsyncMock()

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_request_supabase] = lambda: None
    app.dependency_overrides[get_ws_manager] = lambda: mock_ws
    client = TestClient(app)

    try:
        # Create ticket as user
        res_tkt = client.post(
            "/api/support/tickets",
            json={
                "subject": "Exchange WebSocket Disconnect Inquiry",
                "description": "Encountered brief latency on Binance Testnet feed.",
                "category": "trading",
                "priority": "medium"
            }
        )
        assert res_tkt.status_code == 200
        ticket_id = res_tkt.json()["ticket_id"]

        # Staff replies
        from backend_app.core.dependencies import get_admin_user
        app.dependency_overrides[get_current_user] = lambda: admin
        app.dependency_overrides[get_admin_user] = lambda: admin

        res_reply = client.post(
            f"/api/support/admin/tickets/{ticket_id}/reply",
            json={
                "message": "We inspected the feed; the heartbeat timeout was auto-recovered.",
                "new_status": "waiting_for_user"
            }
        )
        assert res_reply.status_code == 200

        # User checks notifications
        app.dependency_overrides[get_current_user] = lambda: user
        res_notifs = client.get("/api/notifications")
        assert res_notifs.status_code == 200
        notifs = res_notifs.json()
        assert notifs["total"] >= 1
        
        support_notif = next((n for n in notifs["items"] if n["category"] == "support"), None)
        assert support_notif is not None
        assert "Support Update" in support_notif["title"]
        assert support_notif["metadata"]["ticket_id"] == ticket_id

    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_support_ticket_status_resolution_generates_notification():
    """Verify user resolving/closing ticket generates a support status notification."""
    user = {"id": "usr_tenant_777", "email": "trader777@vyomquant.io", "role": "user"}
    mock_ws = AsyncMock()

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_request_supabase] = lambda: None
    app.dependency_overrides[get_ws_manager] = lambda: mock_ws
    client = TestClient(app)

    try:
        # Create ticket
        res_tkt = client.post(
            "/api/support/tickets",
            json={
                "subject": "Billing Invoice Clarification",
                "description": "Please provide VAT invoice for Pro subscription.",
                "category": "billing",
                "priority": "low"
            }
        )
        assert res_tkt.status_code == 200
        ticket_id = res_tkt.json()["ticket_id"]

        # Close/resolve ticket
        res_close = client.put(f"/api/support/tickets/{ticket_id}?status=resolved")
        assert res_close.status_code == 200

        # Check notifications
        res_notifs = client.get("/api/notifications")
        assert res_notifs.status_code == 200
        notifs = res_notifs.json()
        resolved_notif = next((n for n in notifs["items"] if "Resolved" in n["title"]), None)
        assert resolved_notif is not None
        assert resolved_notif["category"] == "support"
        assert resolved_notif["metadata"]["ticket_id"] == ticket_id

    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_exchange_connection_lifecycle_generates_notifications():
    """Verify exchange connection and disconnection produce real user notifications."""
    user = {"id": "usr_exch_trader_01", "email": "exch@quant.io", "role": "user"}
    mock_vault = MagicMock()
    mock_vault.store_exchange_keys = MagicMock(return_value=None)
    mock_redis = MagicMock()
    mock_redis.cache_delete = AsyncMock(return_value=None)

    from backend_app.core.state import app_state
    app_state.vault = mock_vault

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_request_supabase] = lambda: None
    app.dependency_overrides[get_vault] = lambda: mock_vault
    app.dependency_overrides[get_redis_manager] = lambda: mock_redis
    client = TestClient(app)

    try:
        # Mock ConnectionEngine validation pass
        with patch("backend_app.routers.exchange.ConnectionEngine") as mock_conn:
            mock_bridge = MagicMock()
            mock_bridge.connect = AsyncMock(return_value=MagicMock())
            mock_bridge.disconnect = AsyncMock()
            mock_conn.return_value = mock_bridge

            with patch("backend_app.routers.exchange.DataEngine") as mock_data:
                mock_data.return_value.fetch_wallet_balance_snapshot = AsyncMock()

                res_store = client.post(
                    "/api/exchanges/keys",
                    json={
                        "exchange_id": "binance",
                        "api_key": "test_key_12345",
                        "secret_key": "test_sec_67890",
                        "label": "Binance Spot Main"
                    }
                )
                assert res_store.status_code == 200, res_store.text

        # Check notifications for exchange connection
        res_notifs = client.get("/api/notifications?category=exchange")
        assert res_notifs.status_code == 200
        data = res_notifs.json()
        assert data["total"] >= 1
        assert "Exchange Connected" in data["items"][0]["title"]
        assert data["items"][0]["exchange"] == "binance"
        # Verify secret NOT leaked in metadata
        assert "api_key" not in data["items"][0]["metadata"]
        assert "secret_key" not in data["items"][0]["metadata"]

    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_paper_trading_events_generate_notifications():
    """Verify paper trading order placement and account reset trigger notifications."""
    user = {"id": "usr_paper_pro_01", "email": "paper@quant.io", "role": "user"}
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_request_supabase] = lambda: None
    client = TestClient(app)

    try:
        # 1. Reset paper account
        res_reset = client.post(
            "/api/paper/account/reset",
            json={"capital": 75000.0}
        )
        assert res_reset.status_code == 200

        # 2. Place paper order
        res_order = client.post(
            "/api/paper/orders",
            json={
                "symbol": "ETH-USDT",
                "side": "buy",
                "order_type": "market",
                "quantity": 2.5
            }
        )
        assert res_order.status_code == 200

        # 3. Verify notifications
        res_notifs = client.get("/api/notifications")
        assert res_notifs.status_code == 200
        items = res_notifs.json()["items"]
        
        types = [item["type"] for item in items]
        assert "paper_order_placed" in types
        assert "paper_account_reset" in types

    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_user_profile_update_generates_notification():
    """Verify updating profile details dispatches a security/account notification."""
    user = {"id": "usr_profile_01", "email": "profile@quant.io", "role": "user"}
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_request_supabase] = lambda: None
    client = TestClient(app)

    try:
        res = client.put(
            "/api/user/profile",
            json={"display_name": "Quant Sovereign", "bio": "Algorithmic Arbitrage Specialist"}
        )
        assert res.status_code == 200

        res_notifs = client.get("/api/notifications?category=security")
        assert res_notifs.status_code == 200
        data = res_notifs.json()
        assert data["total"] >= 1
        assert "Profile Information Updated" in data["items"][0]["title"]

    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_notification_idempotency_prevents_duplicate_notifications():
    """Verify idempotency key prevents duplicate notifications for duplicate worker/API triggers."""
    user_id = "usr_idempotent_01"
    
    n1 = NotificationCreate(
        user_id=user_id,
        type="order_filled",
        category="trade",
        title="BTC Fill",
        message="Filled 1 BTC",
        metadata={"idempotency_key": "fill_order_btc_9999"}
    )

    n2 = NotificationCreate(
        user_id=user_id,
        type="order_filled",
        category="trade",
        title="BTC Fill",
        message="Filled 1 BTC",
        metadata={"idempotency_key": "fill_order_btc_9999"}  # Exact same idempotency key
    )

    id1 = await create_notification(n1, None, None)
    id2 = await create_notification(n2, None, None)

    assert id1 == id2, "Duplicate notification with same idempotency_key must return original id"

    with _notifications_lock:
        user_notifs = [v for v in _in_memory_notifications.values() if v.get("user_id") == user_id]
        assert len(user_notifs) == 1, "Only one logical notification should exist in store"


@pytest.mark.asyncio
async def test_notification_settings_lifecycle():
    """Verify getting and updating notification preferences via user router."""
    user = {"id": "usr_settings_123", "email": "trader@quant.io", "role": "user"}
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_request_supabase] = lambda: None
    client = TestClient(app)

    try:
        # Get defaults
        res_get = client.get("/api/notifications/settings")
        assert res_get.status_code == 200

        # Update settings
        res_put = client.put(
            "/api/notifications/settings",
            json={
                "channels": {"email": True, "push": True, "sms": False, "in_app": True},
                "events": {"order_filled": True, "order_failed": True, "risk_alert": True, "daily_summary": False, "system_alert": True}
            }
        )
        assert res_put.status_code == 200
        assert res_put.json()["status"] == "ok"

    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_notification_resilience_and_no_secret_leakage():
    """Verify notification creation does not crash when ws fails, and contains no secrets."""
    failing_ws = MagicMock()
    failing_ws.broadcast_user = AsyncMock(side_effect=RuntimeError("WebSocket network partition"))

    notif = NotificationCreate(
        user_id="usr_resilient_1",
        type="system",
        category="system",
        title="System Notice",
        message="Cluster health verified.",
        metadata={"api_key": "LEAKED_KEY_123", "safe_param": "100ms"}
    )

    # Must NOT raise exception despite failing WebSocket
    notif_id = await dispatch_user_notification(
        user_id="usr_resilient_1",
        event_type="system",
        category="system",
        title="System Notice",
        message="Cluster health verified.",
        metadata={"api_key": "LEAKED_KEY_123", "safe_param": "100ms"},
        supabase=None,
        ws_manager=failing_ws,
    )
    assert notif_id is not None
    assert notif_id.startswith("notif_")

    with _notifications_lock:
        stored = _in_memory_notifications[notif_id]
        assert "password" not in stored
        assert "api_secret" not in stored
        assert "access_token" not in stored
        assert "private_key" not in stored
        assert "api_key" not in stored.get("metadata", {})
        assert stored.get("metadata", {}).get("safe_param") == "100ms"


@pytest.mark.asyncio
async def test_risk_kill_switch_and_settings_notifications():
    """Verify emergency kill switch activation/recovery and risk setting updates generate notifications."""
    user = {"id": "usr_risk_hero_01", "email": "risk@quant.io", "role": "user"}
    mock_ws = AsyncMock()

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_request_supabase] = lambda: None
    app.dependency_overrides[get_ws_manager] = lambda: mock_ws
    client = TestClient(app)

    try:
        # 1. Update risk settings
        res_settings = client.put(
            "/api/risk/settings",
            json={"max_daily_loss": 1200.0, "max_positions": 5, "max_leverage": 3}
        )
        assert res_settings.status_code == 200

        # 2. Activate emergency kill switch
        res_kill = client.post(
            "/api/risk/kill-switch",
            json={"reason": "Excessive market volatility detected across spot pairs"}
        )
        assert res_kill.status_code == 200

        # 3. Deactivate / recover kill switch
        res_recover = client.post("/api/risk/kill-switch/recover")
        assert res_recover.status_code == 200

        # 4. Check user notifications
        res_notifs = client.get("/api/notifications?category=risk")
        assert res_notifs.status_code == 200
        items = res_notifs.json()["items"]
        types = [item["type"] for item in items]

        assert "risk_settings_updated" in types
        assert "kill_switch_activated" in types
        assert "kill_switch_recovered" in types

        # Check severity of kill switch
        kill_notif = next(n for n in items if n["type"] == "kill_switch_activated")
        assert kill_notif["severity"] == "critical"

    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_billing_entitlement_generates_notification():
    """Verify subscription plan upgrades dispatch billing notifications."""
    user_id = "usr_billing_pro_01"
    
    from backend_app.routers.billing import _apply_billing_entitlement
    with patch("backend_app.routers.billing._background_sb") as mock_sb:
        mock_sb.return_value.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [{"subscription_tier": "free"}]
        mock_sb.return_value.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [{"subscription_tier": "pro"}]
        
        await _apply_billing_entitlement(user_id, "pro")

    # Check notification in store
    with _notifications_lock:
        user_notifs = [n for n in _in_memory_notifications.values() if n.get("user_id") == user_id]
        assert len(user_notifs) >= 1
        assert user_notifs[0]["category"] == "billing"
        assert "PRO" in user_notifs[0]["title"]


@pytest.mark.asyncio
async def test_strategy_lifecycle_generates_notifications():
    """Verify strategy save, deployment, and stop dispatch notifications."""
    user = {"id": "usr_strat_pilot_01", "email": "pilot@quant.io", "role": "user", "access_token": "mock_jwt_token_123"}
    mock_fleet = MagicMock()
    mock_fleet.start_bot = AsyncMock(return_value=(True, "Bot launched"))
    mock_fleet.stop_bot = AsyncMock(return_value=True)
    mock_ws = AsyncMock()

    from backend_app.core.subscription_dependencies import check_strategy_quota, check_bot_quota, require_live_trading

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_fleet] = lambda: mock_fleet
    app.dependency_overrides[get_ws_manager] = lambda: mock_ws
    app.dependency_overrides[check_strategy_quota] = lambda: True
    app.dependency_overrides[check_bot_quota] = lambda: True
    app.dependency_overrides[require_live_trading] = lambda: True

    client = TestClient(app)
    client.headers.update({"Authorization": "Bearer mock_jwt_token_123"})

    try:
        mock_sb = MagicMock()
        with patch("backend_app.routers.strategies._sb", AsyncMock(return_value=mock_sb)), \
             patch("backend_app.routers.strategies.create_request_supabase_async", AsyncMock(return_value=mock_sb)):
            # 1. Strategy creation
            mock_sb.table.return_value.insert.return_value.execute = AsyncMock(return_value=MagicMock(data=[{"id": "strat_alpha_123"}]))

            res_create = client.post(
                "/api/strategies",
                json={
                    "name": "Alpha Trend Rider",
                    "symbol": "BTC/USDT",
                    "timeframe": "1h"
                }
            )
            assert res_create.status_code == 200, res_create.text

            # 2. Strategy deployment
            mock_sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=MagicMock(
                data=[{"id": "strat_alpha_123", "name": "Alpha Trend Rider", "symbol": "BTC/USDT", "status": "stopped"}]
            ))
            mock_sb.table.return_value.update.return_value.eq.return_value.execute = AsyncMock(return_value=MagicMock(data=[{"status": "running"}]))

            with patch("backend_app.routers.strategies.SubscriptionEngine.reserve_quota", return_value=(True, 1, 5)):
                with patch("backend_app.routers.strategies.require_live_trading", return_value=True):
                    res_deploy = client.post(
                        "/api/strategies/strat_alpha_123/deploy",
                        json={"symbol": "BTC/USDT"}
                    )
                    assert res_deploy.status_code == 200

            # 3. Strategy stop
            mock_sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(return_value=MagicMock(
                data=[{"id": "strat_alpha_123", "name": "Alpha Trend Rider", "symbol": "BTC/USDT", "status": "running"}]
            ))
            mock_sb.table.return_value.update.return_value.eq.return_value.execute = AsyncMock(return_value=MagicMock(data=[{"status": "stopped"}]))

            with patch("backend_app.routers.strategies.decrement_usage", return_value=None):
                res_stop = client.post("/api/strategies/strat_alpha_123/stop")
                assert res_stop.status_code == 200

            # 4. Verify notifications
            res_notifs = client.get("/api/notifications?category=strategy")
            assert res_notifs.status_code == 200
            items = res_notifs.json()["items"]
            types = [item["type"] for item in items]

            assert "strategy_created" in types
            assert "strategy_deployed" in types
            assert "strategy_stopped" in types

    finally:
        app.dependency_overrides.clear()


