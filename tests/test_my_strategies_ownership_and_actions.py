"""
tests/test_my_strategies_ownership_and_actions.py

Task 17.4 of ``marketplace-subscriptions-paper-trading``: ``GET /api/library/my-strategies``
returns a server-derived ``ownership`` label, a ``subscription`` triple and a server-computed
``allowed_actions`` per entry, in three round trips independent of the entry count.

Requirements 12.1, 12.2, 12.3, 12.4, 12.6, 27.2.

WHAT IS ASSERTED, AND WHY IT IS SPLIT IN TWO HALVES
---------------------------------------------------
The derivation half runs against ``marketplace/library_entries.py`` directly, with no database
and no HTTP: ``ownership``, ``subscription`` and ``allowed_actions`` are decided by a pure
module, so the whole (entitling x listing-consent x renewal-state) matrix is enumerated rather
than sampled. That is the only way "a SUBSCRIBED entry NEVER offers these thirteen actions"
(Requirement 12.4) is a statement about every reachable state instead of about one fixture.

The wiring half runs the real route through a FastAPI ``TestClient`` against a *counting* fake
Supabase double, so the three round trips of Requirement 27.2 are asserted by counting the
``.execute()`` calls the handler actually made across 0, 1 and 50 entries - which is the only
way to test an N+1's absence rather than its current absence.

NON-VACUITY
-----------
The seeded embedded ``library_strategies`` row carries a value in every member of
``listing_projection.DENIED_LISTING_COLUMNS`` and, on top of that, every Protected_Logic column
(``buy_logic``, ``sell_logic``, ``risk``, ``indicators``, ``ml_model_path``, ``blueprint``,
``graph_json``) with distinctive tokens. So "the response carries none of them" is a real
statement about a row that has them. The containment check is the module's own oracle,
``listing_projection.assert_contains_no_protected_logic``, over tokens computed from the same
seeded documents, so the check and the projection cannot drift apart.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient

from backend_app.backend.marketplace import library_entries as le
from backend_app.backend.marketplace import listing_projection
from backend_app.backend.marketplace.entitlement_resolver import EntitlementReason
from backend_app.backend.marketplace.subscription_state import SubscriptionState
from backend_app.core.dependencies import get_current_user
from backend_app.main import app

client = TestClient(app, raise_server_exceptions=False)


# ══════════════════════════════════════════════════════════════════════════
# Identities, instants and seeds
# ══════════════════════════════════════════════════════════════════════════

CALLER_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
AUTHOR_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
LISTING_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SUBSCRIPTION_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd"
OWNED_STRATEGY_ID = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
OWNER_SOURCE_STRATEGY_ID = "ffffffff-ffff-ffff-ffff-ffffffffffff"

NOW = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
FUTURE = (NOW + timedelta(days=20)).isoformat()
PAST = (NOW - timedelta(days=1)).isoformat()

CALLER_USER = {
    "id": CALLER_ID,
    "email": "subscriber@test.example",
    "role": "authenticated",
    "app_metadata": {},
    "user_metadata": {},
}

#: Protected_Logic documents seeded onto the embedded Listing row. Distinctive tokens so a leak
#: is unambiguous rather than a coincidence with ordinary prose.
PROTECTED_STRATEGY_ROW = {
    "buy_logic": {"node_zzq_entry": {"indicator": "rsi_secret_period", "threshold": 27.5}},
    "sell_logic": {"node_zzq_exit": {"indicator": "atr_secret_mult", "threshold": 3.25}},
    "indicators": {"rsi_secret_period": 14, "atr_secret_mult": 3.25},
    "risk": {"stop_loss_pct_secret": 2.75, "max_position_secret": 12500},
    "ml_model_path": "models/private/zzq_secret_model.pkl",
}
PROTECTED_VERSION_ROW = {
    "blueprint": {"nodes": ["node_zzq_entry", "node_zzq_exit"], "wires": ["wire_zzq_1"]},
    "graph_json": {"graph_zzq_hash": "graph_zzq_fingerprint"},
}


def _embedded_listing_row(
    *,
    author_id: str = AUTHOR_ID,
    submission_state: str = "PUBLISHED",
    source_cloning_enabled: bool = True,
    listing_id: str = LISTING_ID,
) -> Dict[str, Any]:
    """An embedded ``library_strategies`` row carrying every denied column AND every
    Protected_Logic document, so the absence assertions below are non-vacuous."""
    row: Dict[str, Any] = {
        # ── Columns LISTING_SELECT requests ──
        "id": listing_id,
        "name": "Momentum Breakout",
        "description": "A subscribed strategy card.",
        "category": "momentum",
        "difficulty": "intermediate",
        "tags": ["trend", "breakout"],
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "exchange_id": "binance",
        "price": "9.99",
        "price_minor": 999,
        "currency": "USD",
        "subscriber_count": 42,
        "avg_rating": 4.5,
        "rating_count": 12,
        "published_at": "2026-06-01T00:00:00+00:00",
        "verification_status": "verified",
        "source_cloning_enabled": source_cloning_enabled,
        "author_id": author_id,
        "supported_timeframes": ["1h", "4h"],
        "market_type": "spot",
        "condition_count": 3,
        "backtest_total_return_pct": "18.5",
        "backtest_sharpe_ratio": "1.7",
        "backtest_max_drawdown_pct": "9.0",
        "backtest_win_rate_pct": "56.0",
        "backtest_profit_factor": "1.9",
        "backtest_total_trades": 120,
        # ── The embedded submission state ──
        "marketplace_submissions": [{"submission_state": submission_state}],
        # ── DENIED columns, each carrying a value (non-vacuity) ──
        "source_strategy_id": OWNER_SOURCE_STRATEGY_ID,
        "moderated_by": "55555555-5555-5555-5555-555555555555",
        "moderation_notes": "internal reviewer note - do not disclose",
        "moderated_at": "2026-06-30T12:00:00+00:00",
        "deployment_requirements": {"min_capital": 1000},
        "version_history": [{"version": 1}],
        "evaluation_score": 87.3,
        "equity_curve_snapshot": [100.0, 101.2],
        "has_ml_model": True,
        "node_count": 9,
        "risk_stop_loss_pct": 3.5,
        "risk_take_profit_pct": 7.0,
        "risk_max_position_size": 25000,
        "risk_max_drawdown_pct": 15.0,
        "is_active": True,
        "moderation_status": "approved",
        "updated_at": "2026-06-30T12:00:00+00:00",
    }
    # ── Protected_Logic, seeded onto the same row (non-vacuity) ──
    row.update(PROTECTED_STRATEGY_ROW)
    row.update(PROTECTED_VERSION_ROW)
    return row


def _subscription_row(
    *,
    status: str = "active",
    period_expiry: Optional[str] = FUTURE,
    renewal_enabled: Optional[bool] = True,
    listing: Optional[Dict[str, Any]] = None,
    subscription_id: str = SUBSCRIPTION_ID,
) -> Dict[str, Any]:
    return {
        "id": subscription_id,
        "library_id": LISTING_ID,
        "status": status,
        "period_expiry": period_expiry,
        "renewal_enabled": renewal_enabled,
        "library_strategies": listing
        if listing is not None
        else _embedded_listing_row(),
    }


def _owned_row(strategy_id: str = OWNED_STRATEGY_ID) -> Dict[str, Any]:
    """One ``strategies`` row as round trip 1 receives it.

    No ``description`` key, because ``public.strategies`` has no such column and
    :data:`library_entries.OWNED_STRATEGY_SELECT` no longer asks for one - see
    ``TestTheOwnedProjectionNamesNoPhantomColumn`` for the production ``42703`` that proved it.
    A fixture carrying a key the real row cannot carry would make every assertion below a
    statement about a row that does not exist.
    """
    return {
        "id": strategy_id,
        "name": "My own strategy",
        "symbol": "ETHUSDT",
        "timeframe": "15m",
        "status": "stopped",
        "is_active": True,
        "created_at": "2026-05-01T00:00:00+00:00",
        "updated_at": "2026-06-01T00:00:00+00:00",
        "archived_at": None,
    }


def _protected_logic_tokens() -> frozenset:
    return listing_projection.protected_logic_tokens(
        strategy_row=PROTECTED_STRATEGY_ROW,
        version_row=PROTECTED_VERSION_ROW,
    )


# ══════════════════════════════════════════════════════════════════════════
# THE DERIVATION HALF - pure, no database, no HTTP
# ══════════════════════════════════════════════════════════════════════════


class TestAllowedActionsForASubscribedEntry:
    """Requirements 12.3, 12.4, 12.5 over the whole reachable state matrix."""

    MATRIX = [
        (entitling, consent, renewal)
        for entitling in (True, False)
        for consent in (True, False)
        for renewal in (le.RENEWAL_ENABLED, le.RENEWAL_CANCELLED, None)
    ]

    @pytest.mark.parametrize("entitling,consent,renewal", MATRIX)
    def test_never_contains_any_of_the_thirteen_forbidden_actions(
        self, entitling: bool, consent: bool, renewal: Optional[str]
    ) -> None:
        """Requirement 12.4. Enumerated, not sampled: every reachable state is checked."""
        actions = le.allowed_actions_for_subscribed(
            entitling=entitling, backtest_permitted=consent, renewal_state=renewal
        )
        leaked = set(actions) & le.SUBSCRIBER_FORBIDDEN_ACTIONS
        assert not leaked, f"SUBSCRIBED entry offered forbidden action(s) {sorted(leaked)}"

    @pytest.mark.parametrize("entitling,consent,renewal", MATRIX)
    def test_is_within_the_eight_permitted_actions(
        self, entitling: bool, consent: bool, renewal: Optional[str]
    ) -> None:
        """Requirement 12.3: the permitted set is an upper bound, never exceeded."""
        actions = le.allowed_actions_for_subscribed(
            entitling=entitling, backtest_permitted=consent, renewal_state=renewal
        )
        surplus = set(actions) - set(le.SUBSCRIBER_PERMITTED_ACTIONS)
        assert not surplus, f"SUBSCRIBED entry offered unnamed action(s) {sorted(surplus)}"

    def test_the_thirteen_forbidden_actions_are_exactly_requirement_12_4s_list(self) -> None:
        """The list is transcribed from the requirement, so hold it to the requirement."""
        assert le.SUBSCRIBER_FORBIDDEN_ACTIONS == frozenset(
            {
                "edit",
                "open_in_builder",
                "view_graph",
                "edit_blocks",
                "view_indicator_params",
                "edit_indicator_params",
                "view_risk_config",
                "edit_risk_config",
                "export_definition",
                "download_definition",
                "view_model_params",
                "re_version",
                "delete",
            }
        )
        assert len(le.SUBSCRIBER_FORBIDDEN_ACTIONS) == 13

    def test_an_entitling_subscription_offers_execution_and_management_actions(self) -> None:
        actions = le.allowed_actions_for_subscribed(
            entitling=True, backtest_permitted=True, renewal_state=le.RENEWAL_ENABLED
        )
        assert set(actions) == set(le.SUBSCRIBER_PERMITTED_ACTIONS)

    def test_run_backtest_is_offered_only_where_the_listing_permits_it(self) -> None:
        """Requirement 12.3's "where the Listing permits it" qualifier."""
        permitted = le.allowed_actions_for_subscribed(
            entitling=True, backtest_permitted=True, renewal_state=le.RENEWAL_ENABLED
        )
        withheld = le.allowed_actions_for_subscribed(
            entitling=True, backtest_permitted=False, renewal_state=le.RENEWAL_ENABLED
        )
        assert "run_backtest" in permitted
        assert "run_backtest" not in withheld
        # Withholding the backtest withholds nothing else.
        assert set(permitted) - set(withheld) == {"run_backtest"}

    def test_a_non_entitling_subscription_disables_every_execution_action_and_offers_renewal(
        self,
    ) -> None:
        """Requirement 12.5."""
        actions = le.allowed_actions_for_subscribed(
            entitling=False, backtest_permitted=True, renewal_state=le.RENEWAL_ENABLED
        )
        assert not set(actions) & le.SUBSCRIBER_EXECUTION_ACTIONS
        assert "renew" in actions
        assert set(actions) == {
            "view_listing",
            "view_performance",
            "view_subscription",
            "renew",
            "cancel_renewal",
        }

    @pytest.mark.parametrize("renewal", [le.RENEWAL_CANCELLED, None])
    def test_cancel_renewal_is_offered_only_while_renewal_is_on(
        self, renewal: Optional[str]
    ) -> None:
        actions = le.allowed_actions_for_subscribed(
            entitling=True, backtest_permitted=True, renewal_state=renewal
        )
        assert "cancel_renewal" not in actions
        assert "renew" in actions

    def test_an_owned_entry_offers_every_existing_action(self) -> None:
        """``design.md``'s ownership table: OWNED | every existing action | —."""
        actions = le.allowed_actions_for_owned()
        assert set(actions) == set(le.SUBSCRIBER_PERMITTED_ACTIONS) | set(
            le.SUBSCRIBER_FORBIDDEN_ACTIONS
        )
        # The owner is not subject to Requirement 12.4 - it constrains a SUBSCRIBED entry only.
        assert le.SUBSCRIBER_FORBIDDEN_ACTIONS <= set(actions)


class TestSubscriptionView:
    """Requirement 12.6: the Subscription_State, the period expiry and the renewal state."""

    def test_it_carries_exactly_the_three_required_keys(self) -> None:
        view = le.subscription_view(_subscription_row(), NOW)
        assert set(view) == {"state", "period_expiry", "renewal_state"}

    def test_the_state_is_the_uppercase_requirement_spelling(self) -> None:
        view = le.subscription_view(_subscription_row(status="active"), NOW)
        assert view["state"] == SubscriptionState.ACTIVE.value == "ACTIVE"

    def test_the_expiry_is_reported_as_the_stored_instant(self) -> None:
        view = le.subscription_view(_subscription_row(period_expiry=FUTURE), NOW)
        assert view["period_expiry"] == FUTURE

    @pytest.mark.parametrize(
        "renewal_enabled,expected",
        [(True, le.RENEWAL_ENABLED), (False, le.RENEWAL_CANCELLED), (None, None)],
    )
    def test_the_renewal_state_is_read_and_never_defaulted(
        self, renewal_enabled: Optional[bool], expected: Optional[str]
    ) -> None:
        """An absent ``renewal_enabled`` is reported unavailable, not guessed (Req 28.5)."""
        view = le.subscription_view(
            _subscription_row(renewal_enabled=renewal_enabled), NOW
        )
        assert view["renewal_state"] == expected

    def test_an_unrecognised_status_is_reported_unavailable_rather_than_guessed(self) -> None:
        view = le.subscription_view(_subscription_row(status="not_a_state"), NOW)
        assert view["state"] is None


class TestEntitlementIsMirroredFromTheResolver:
    """The list's entitlement verdict must agree with the Entitlement_Resolver's ordering."""

    def test_an_active_unexpired_subscription_on_a_published_listing_entitles(self) -> None:
        row = _subscription_row(status="active", period_expiry=FUTURE)
        assert (
            le.entitlement_reason(row, row["library_strategies"], NOW)
            is EntitlementReason.SUBSCRIBED
        )

    def test_a_suspended_subscription_is_its_own_reason(self) -> None:
        row = _subscription_row(status="suspended")
        assert (
            le.entitlement_reason(row, row["library_strategies"], NOW)
            is EntitlementReason.SUBSCRIPTION_SUSPENDED
        )

    @pytest.mark.parametrize("status", ["cancelled", "expired", "pending", "refunded"])
    def test_a_non_active_status_is_expired(self, status: str) -> None:
        row = _subscription_row(status=status)
        assert (
            le.entitlement_reason(row, row["library_strategies"], NOW)
            is EntitlementReason.EXPIRED
        )

    @pytest.mark.parametrize("expiry", [None, PAST])
    def test_expiry_is_sweep_independent(self, expiry: Optional[str]) -> None:
        """Requirement 11.7: a row still reading ``active`` past its expiry does not entitle."""
        row = _subscription_row(status="active", period_expiry=expiry)
        assert (
            le.entitlement_reason(row, row["library_strategies"], NOW)
            is EntitlementReason.EXPIRED
        )

    @pytest.mark.parametrize("state", ["PUBLISHED", "SUSPENDED", "UNPUBLISHED"])
    def test_a_suspended_or_unpublished_listing_still_entitles(self, state: str) -> None:
        """Requirement 4.11: hiding a Listing does not revoke a paid, unexpired Subscription."""
        row = _subscription_row(listing=_embedded_listing_row(submission_state=state))
        assert (
            le.entitlement_reason(row, row["library_strategies"], NOW)
            is EntitlementReason.SUBSCRIBED
        )

    @pytest.mark.parametrize("state", ["DRAFT", "SUBMITTED", "REJECTED", "APPROVED"])
    def test_a_listing_outside_the_entitling_states_is_unavailable(self, state: str) -> None:
        row = _subscription_row(listing=_embedded_listing_row(submission_state=state))
        assert (
            le.entitlement_reason(row, row["library_strategies"], NOW)
            is EntitlementReason.LISTING_UNAVAILABLE
        )

    def test_the_entitling_submission_states_agree_with_the_resolvers(self) -> None:
        from backend_app.backend.marketplace import entitlement_resolver

        assert (
            le.ENTITLING_SUBMISSION_STATES
            == entitlement_resolver._ENTITLING_SUBMISSION_STATES
        )


class TestSubscribedListingProjection:
    """Requirement 12.4's server half: nothing that describes the owner's definition."""

    def test_the_output_is_within_the_one_public_allow_list(self) -> None:
        out = le.project_subscribed_listing(_embedded_listing_row())
        surplus = set(out) - listing_projection.PUBLIC_LISTING_FIELDS
        assert not surplus, f"projected fields outside the public allow-list: {sorted(surplus)}"

    def test_no_denied_listing_column_reaches_the_output(self) -> None:
        out = le.project_subscribed_listing(_embedded_listing_row())
        leaked = set(out) & listing_projection.DENIED_LISTING_COLUMNS
        assert not leaked, f"projected denied column(s): {sorted(leaked)}"

    def test_no_protected_logic_reaches_the_output(self) -> None:
        out = le.project_subscribed_listing(_embedded_listing_row())
        listing_projection.assert_contains_no_protected_logic(
            out, _protected_logic_tokens()
        )

    def test_the_price_is_the_exact_integer_plus_a_string_display(self) -> None:
        out = le.project_subscribed_listing(_embedded_listing_row())
        assert out["price_minor"] == 999
        assert out["price_display"] == "9.99"
        assert isinstance(out["price_display"], str)

    def test_a_malformed_price_omits_the_display_rather_than_approximating_it(self) -> None:
        """Requirement 28.5: omit, never substitute."""
        row = _embedded_listing_row()
        row["price_minor"] = "not-an-amount"
        out = le.project_subscribed_listing(row)
        assert "price_display" not in out
        assert out["price_minor"] == "not-an-amount"

    def test_an_unrated_listing_omits_the_rating_rather_than_zeroing_it(self) -> None:
        """Requirement 6.9."""
        row = _embedded_listing_row()
        row["avg_rating"] = None
        row["rating_count"] = 0
        out = le.project_subscribed_listing(row)
        assert "avg_rating" not in out
        assert "rating_count" not in out


class TestRunningSessionCounts:
    def test_sessions_are_grouped_by_strategy_and_by_listing(self) -> None:
        rows = [
            {
                "id": "1",
                "listing_id": LISTING_ID,
                "source_strategy_id": OWNER_SOURCE_STRATEGY_ID,
                "session_state": "RUNNING",
            },
            {
                "id": "2",
                "listing_id": None,
                "source_strategy_id": OWNED_STRATEGY_ID,
                "session_state": "RUNNING",
            },
            {
                "id": "3",
                "listing_id": LISTING_ID,
                "source_strategy_id": OWNER_SOURCE_STRATEGY_ID,
                "session_state": "RUNNING",
            },
        ]
        by_strategy, by_listing = le.running_session_counts(rows)
        assert by_listing[LISTING_ID] == 2
        assert by_strategy[OWNED_STRATEGY_ID] == 1

    def test_a_non_running_session_is_not_counted(self) -> None:
        rows = [
            {
                "id": "1",
                "listing_id": LISTING_ID,
                "source_strategy_id": OWNER_SOURCE_STRATEGY_ID,
                "session_state": "STOPPED",
            }
        ]
        by_strategy, by_listing = le.running_session_counts(rows)
        assert by_listing == {} and by_strategy == {}


class TestCombinedListBuilder:
    """Requirements 12.1, 12.2."""

    def test_owned_and_subscribed_entries_coexist_in_one_list(self) -> None:
        entries = le.build_my_strategies_entries(
            caller_id=CALLER_ID,
            owned_rows=[_owned_row()],
            subscription_rows=[_subscription_row()],
            session_rows=[],
            now=NOW,
        )
        assert [entry["ownership"] for entry in entries] == [
            le.OWNERSHIP_OWNED,
            le.OWNERSHIP_SUBSCRIBED,
        ]

    def test_every_entry_carries_a_server_derived_label_and_action_list(self) -> None:
        entries = le.build_my_strategies_entries(
            caller_id=CALLER_ID,
            owned_rows=[_owned_row()],
            subscription_rows=[_subscription_row()],
            session_rows=[],
            now=NOW,
        )
        for entry in entries:
            assert entry["ownership"] in {le.OWNERSHIP_OWNED, le.OWNERSHIP_SUBSCRIBED}
            assert isinstance(entry["allowed_actions"], list)
            assert entry["allowed_actions"]

    def test_a_subscription_to_the_callers_own_listing_is_not_listed_twice(self) -> None:
        """The owner already sees the strategy as OWNED; two action sets on one strategy is
        exactly the client/server disagreement Requirement 12.2 exists to prevent."""
        entries = le.build_my_strategies_entries(
            caller_id=CALLER_ID,
            owned_rows=[_owned_row()],
            subscription_rows=[
                _subscription_row(listing=_embedded_listing_row(author_id=CALLER_ID))
            ],
            session_rows=[],
            now=NOW,
        )
        assert [entry["ownership"] for entry in entries] == [le.OWNERSHIP_OWNED]

    def test_an_unavailable_session_read_omits_the_count_rather_than_zeroing_it(self) -> None:
        """Requirement 28.5."""
        entries = le.build_my_strategies_entries(
            caller_id=CALLER_ID,
            owned_rows=[_owned_row()],
            subscription_rows=[_subscription_row()],
            session_rows=None,
            now=NOW,
        )
        for entry in entries:
            assert "running_paper_sessions" not in entry

    def test_a_lapsed_entry_reports_the_code_the_server_would_refuse_it_with(self) -> None:
        entries = le.build_my_strategies_entries(
            caller_id=CALLER_ID,
            owned_rows=[],
            subscription_rows=[_subscription_row(status="active", period_expiry=PAST)],
            session_rows=[],
            now=NOW,
        )
        entry = entries[0]
        assert entry["entitling"] is False
        assert entry["unavailable_reason"] == "MARKETPLACE_SUBSCRIPTION_EXPIRED"
        assert entry["subscription"]["state"] == "ACTIVE"


# ══════════════════════════════════════════════════════════════════════════
# THE WIRING HALF - the real route against a counting Supabase double
# ══════════════════════════════════════════════════════════════════════════


class _Resp:
    def __init__(self, data: Any):
        self.data = data


class _Query:
    """A fluent builder that ignores the filter chain and returns the scripted rows for its
    table at ``.execute()``. A table whose name is in ``fail_on`` raises instead."""

    def __init__(self, table: str, db: "CountingSupabase"):
        self.table_name = table
        self.db = db

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def is_(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def single(self, *a, **k):
        return self

    def execute(self):
        self.db.calls.append(self.table_name)
        if self.table_name in self.db.fail_on:
            raise RuntimeError(f"scripted failure reading {self.table_name}")
        return _Resp(list(self.db.script.get(self.table_name, [])))


class CountingSupabase:
    def __init__(self, script: Dict[str, List[Dict[str, Any]]], fail_on=()):
        self.script = script
        self.fail_on = set(fail_on)
        self.calls: List[str] = []

    def table(self, name: str):
        return _Query(name, self)


def _call_route(db: CountingSupabase):
    app.dependency_overrides[get_current_user] = lambda: CALLER_USER
    try:
        with patch(
            "backend_app.routers.library._build_service_client", return_value=db
        ):
            return client.get("/api/library/my-strategies")
    finally:
        app.dependency_overrides.clear()


def _script(owned: int, subscribed: int, sessions: int = 0) -> Dict[str, List[Dict]]:
    return {
        "strategies": [
            _owned_row(f"{i:08d}-0000-0000-0000-000000000000") for i in range(owned)
        ],
        "library_subscriptions": [
            _subscription_row(
                subscription_id=f"{i:08d}-1111-1111-1111-111111111111",
                listing=_embedded_listing_row(
                    listing_id=f"{i:08d}-2222-2222-2222-222222222222"
                ),
            )
            for i in range(subscribed)
        ],
        "paper_sessions": [
            {
                "id": f"{i:08d}-3333-3333-3333-333333333333",
                "listing_id": f"{i:08d}-2222-2222-2222-222222222222",
                "source_strategy_id": OWNER_SOURCE_STRATEGY_ID,
                "session_state": "RUNNING",
            }
            for i in range(sessions)
        ],
    }


class TestTheRouteIsWired:
    def test_it_answers_200_with_a_labelled_list(self) -> None:
        resp = _call_route(CountingSupabase(_script(owned=1, subscribed=1)))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        assert body["owned_total"] == 1
        assert body["subscribed_total"] == 1
        assert {entry["ownership"] for entry in body["items"]} == {"OWNED", "SUBSCRIBED"}

    def test_a_subscribed_entry_carries_the_requirement_12_6_triple(self) -> None:
        resp = _call_route(CountingSupabase(_script(owned=0, subscribed=1)))
        entry = resp.json()["items"][0]
        assert set(entry["subscription"]) == {"state", "period_expiry", "renewal_state"}
        assert entry["subscription"]["state"] == "ACTIVE"
        assert entry["subscription"]["renewal_state"] == le.RENEWAL_ENABLED

    def test_a_subscribed_entrys_actions_never_name_a_forbidden_one(self) -> None:
        resp = _call_route(CountingSupabase(_script(owned=0, subscribed=1)))
        entry = resp.json()["items"][0]
        assert not set(entry["allowed_actions"]) & le.SUBSCRIBER_FORBIDDEN_ACTIONS
        assert set(entry["allowed_actions"]) <= set(le.SUBSCRIBER_PERMITTED_ACTIONS)

    def test_no_protected_logic_reaches_the_response_body(self) -> None:
        resp = _call_route(CountingSupabase(_script(owned=0, subscribed=1)))
        listing_projection.assert_contains_no_protected_logic(
            resp.json(), _protected_logic_tokens()
        )

    def test_no_owner_identity_reaches_a_subscribed_entry(self) -> None:
        resp = _call_route(CountingSupabase(_script(owned=0, subscribed=1)))
        body = resp.text
        assert AUTHOR_ID not in body
        assert OWNER_SOURCE_STRATEGY_ID not in body

    @pytest.mark.parametrize("owned,subscribed", [(0, 0), (1, 1), (25, 25)])
    def test_three_round_trips_independent_of_the_entry_count(
        self, owned: int, subscribed: int
    ) -> None:
        """Requirement 27.2, asserted by counting the reads the handler actually made."""
        db = CountingSupabase(_script(owned=owned, subscribed=subscribed))
        resp = _call_route(db)
        assert resp.status_code == 200, resp.text
        assert db.calls == ["strategies", "library_subscriptions", "paper_sessions"], (
            f"{len(db.calls)} round trip(s) for {owned + subscribed} entries: {db.calls}"
        )

    def test_a_running_session_count_is_reported_per_entry(self) -> None:
        db = CountingSupabase(_script(owned=0, subscribed=1, sessions=1))
        body = _call_route(db).json()
        assert body["running_paper_sessions_available"] is True
        assert body["items"][0]["running_paper_sessions"] == 1

    def test_an_unavailable_session_read_is_reported_unavailable_not_zero(self) -> None:
        """Requirement 28.5: the figure is omitted, never substituted with a zero."""
        db = CountingSupabase(_script(owned=1, subscribed=1), fail_on={"paper_sessions"})
        resp = _call_route(db)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["running_paper_sessions_available"] is False
        for entry in body["items"]:
            assert "running_paper_sessions" not in entry

    @pytest.mark.parametrize("failing", ["strategies", "library_subscriptions"])
    def test_a_failed_read_is_never_a_zero_filled_200(self, failing: str) -> None:
        """Requirements 1.5, 1.7."""
        db = CountingSupabase(_script(owned=1, subscribed=1), fail_on={failing})
        resp = _call_route(db)
        assert resp.status_code in (500, 503), resp.text
        payload = resp.json()
        assert payload["error"]["code"] == "MARKETPLACE_READ_FAILED"
        assert "items" not in payload
        assert "total" not in payload


class TestTheThreeProjectionsAreExplicit:
    """No ``select("*")``, and the embed carries the one canonical Listing column list."""

    def test_no_projection_asks_for_the_whole_row(self) -> None:
        for projection in (
            le.OWNED_STRATEGY_SELECT,
            le.SUBSCRIPTION_SELECT,
            le.RUNNING_PAPER_SESSION_SELECT,
        ):
            assert "*" not in projection

    def test_the_subscription_read_embeds_the_listing_in_the_same_request(self) -> None:
        assert "library_strategies!inner(" in le.SUBSCRIPTION_SELECT
        assert listing_projection.LISTING_SELECT in le.SUBSCRIPTION_SELECT
        assert "marketplace_submissions(submission_state)" in le.SUBSCRIPTION_SELECT

    def test_no_projection_requests_a_protected_logic_column(self) -> None:
        protected = (
            listing_projection.STRATEGY_PROTECTED_LOGIC_COLUMNS
            + listing_projection.VERSION_PROTECTED_LOGIC_COLUMNS
        )
        for projection in (
            le.OWNED_STRATEGY_SELECT,
            le.SUBSCRIPTION_SELECT,
            le.RUNNING_PAPER_SESSION_SELECT,
        ):
            requested = {token.strip() for token in projection.replace("(", ",").replace(")", ",").split(",")}
            leaked = requested & set(protected)
            assert not leaked, f"{projection!r} requests Protected_Logic column(s) {sorted(leaked)}"

# ══════════════════════════════════════════════════════════════════════════
# EVERY REQUESTED COLUMN IS ONE SOMETHING ACTUALLY CREATES
#
# production-launch-hardening. The defect this section exists for:
#
#   ERROR:backend_app.routers.library:my_strategies owned-read failed for user <uuid>:
#     {'code': '42703', 'message': 'column strategies.description does not exist'}
#
# (``/ecs/vyomquant-api``, production CloudWatch.) PostgreSQL ``42703`` is
# ``undefined_column``. ``OWNED_STRATEGY_SELECT`` named ``description``, PostgREST put it in the
# statement's target list, Postgres refused the whole statement, round trip 1's ``except``
# raised ``MARKETPLACE_READ_FAILED``, and the Strategies page rendered "Ownership list
# unavailable - Server error occurred." for EVERY user on EVERY call. The projection had never
# worked: no migration in this repository creates ``strategies.description``.
#
# The assertions below are deliberately two: one naming ``description`` specifically, so the
# regression is pinned to the log line that found it, and one general - every column any of the
# three projections requests must be created by a migration, or be named in
# ``OUT_OF_REPO_COLUMNS`` with a reason. The general one is what stops the NEXT phantom column,
# because a name typed into a projection constant is otherwise unchecked until production.
#
# WHAT THIS CANNOT VERIFY, STATED RATHER THAN GUESSED
# --------------------------------------------------
# ``public.strategies`` has no ``CREATE TABLE`` in either migration directory - the table
# pre-dates this migration set and is defined outside the repo (Supabase-side). So the migration
# files CANNOT settle whether one of its base columns exists; only the columns a migration
# ``ALTER TABLE ... ADD COLUMN``s are provable from this tree. ``OUT_OF_REPO_COLUMNS`` is the
# place that fact is written down per column, with what the evidence for it actually is. It is
# an admission of an unverifiable claim, not a waiver - which is why ``description`` may never
# be listed in it, and why a separate assertion enforces that.
# ══════════════════════════════════════════════════════════════════════════

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: Both migration directories, the same pair
#: ``tests/test_marketplace_paper_schema_contract.py`` scans. ``backend_app/migrations/`` holds
#: the marketplace and paper-trading set; the root ``migrations/`` holds the older reconciliation
#: set, and ``strategies.is_active`` comes from there - so scanning only one of the two would
#: report a real column as phantom.
_MIGRATION_DIRS = (_REPO_ROOT / "backend_app" / "migrations", _REPO_ROOT / "migrations")

#: Columns PROVEN ABSENT from the live schema, each by a production failure. A name here may
#: never appear in any projection, and may never be moved into ``OUT_OF_REPO_COLUMNS`` to make a
#: failure go away: the whole point is that this particular absence is not a gap in the
#: repository's knowledge but a fact the database told us.
PHANTOM_COLUMNS = {
    ("strategies", "description"): (
        "production 42703 undefined_column - 'column strategies.description does not exist' - "
        "logged by routers/library.py::my_strategies on the owned-read, /ecs/vyomquant-api. "
        "No migration in either directory creates it; the `description TEXT` in "
        "001_strategy_architecture.sql belongs to the marketplace_listings CREATE TABLE. "
        "Adding the column is a schema decision, not a fix for a query that asks for it."
    ),
}

#: Requested columns that no migration in this repository creates, each with the reason its
#: existence is nonetheless believed. Every entry is a claim the migration files cannot check.
OUT_OF_REPO_COLUMNS = {
    ("strategies", "id"): (
        "base column of public.strategies, which has no CREATE TABLE in either migration "
        "directory. Evidenced by the production failure itself: the failing target list named "
        "`id` and `name` BEFORE `description`, and Postgres reported 42703 for `description`, "
        "so the two ahead of it resolved."
    ),
    ("strategies", "name"): (
        "base column of public.strategies; same evidence as `id` - it resolved ahead of the "
        "column that raised 42703."
    ),
    ("strategies", "symbol"): (
        "base column of public.strategies. NOT provable from the migration files. Documented as "
        "part of the table in docs/history/PROJECT_ARCHITECTURE.md ('name, symbol, timeframe, "
        "buy_logic, ... status') - a list that notably does NOT include `description` - and read "
        "on the live GET /api/strategies path."
    ),
    ("strategies", "timeframe"): (
        "base column of public.strategies; same evidence as `symbol`."
    ),
    ("strategies", "created_at"): (
        "base column of public.strategies; NOT provable from the migration files. Read on the "
        "live GET /api/strategies path, which ORDERs BY it."
    ),
    ("strategies", "updated_at"): (
        "base column of public.strategies; NOT provable from the migration files. Round trip 1 "
        "ORDERs BY it, and the ordering is how Requirement 12.1's list is sequenced."
    ),
}
# Deliberately NOT in the map, and worth recording:
#   * library_subscriptions.{id,library_id,status} - migrations/006_reconcile_production_database.sql
#   * library_subscriptions.{period_expiry,renewal_enabled} - backend_app/migrations/008
#   * paper_sessions.{id,listing_id,source_strategy_id,session_state} - backend_app/migrations/009
# All five of round trip 2's own columns and all four of round trip 3's are created in-repo, so
# neither sibling projection needs an unverifiable claim. Only ``strategies`` does, because only
# ``strategies`` has no CREATE TABLE here.


def _blank_sql_comments(sql: str) -> str:
    """``--`` and ``/* */`` comments replaced by spaces, offsets and line breaks preserved.

    Needed because several migrations discuss ``ALTER TABLE strategies ADD COLUMN ...`` in
    commented-out prose (005a's rollback note is one), and a scanner that counted those would
    report a column as created that nothing creates - the exact failure mode this guard exists
    to catch, inverted.
    """
    out: List[str] = []
    i = 0
    n = len(sql)
    in_string = False
    while i < n:
        ch = sql[i]
        if in_string:
            out.append(ch)
            if ch == "'":
                in_string = False
            i += 1
            continue
        if ch == "'":
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "-" and sql.startswith("--", i):
            end = sql.find("\n", i)
            end = n if end == -1 else end
            out.append(" " * (end - i))
            i = end
            continue
        if ch == "/" and sql.startswith("/*", i):
            end = sql.find("*/", i)
            end = n if end == -1 else end + 2
            out.append("".join(c if c == "\n" else " " for c in sql[i:end]))
            i = end
            continue
        out.append(ch)
        i += 1
    return "".join(out)


_NOT_A_COLUMN = frozenset(
    {
        "constraint",
        "primary",
        "foreign",
        "unique",
        "check",
        "exclude",
        "like",
        "period",
    }
)


def _create_table_columns(sql: str, table: str) -> List[str]:
    """The column names one ``CREATE TABLE <table> (...)`` declares, or ``[]`` when absent."""
    match = re.search(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?" + table + r"\s*\(",
        sql,
        re.IGNORECASE,
    )
    if match is None:
        return []
    depth = 1
    i = match.end()
    start = i
    while i < len(sql) and depth:
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
        i += 1
    body = sql[start : i - 1]

    items: List[str] = []
    depth = 0
    current: List[str] = []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            items.append("".join(current))
            current = []
            continue
        current.append(ch)
    items.append("".join(current))

    columns = []
    for item in items:
        tokens = item.strip().split()
        if not tokens:
            continue
        name = tokens[0].strip('"')
        if name.lower() in _NOT_A_COLUMN:
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            columns.append(name)
    return columns


def _added_columns(sql: str, table: str) -> List[str]:
    """Every column an ``ALTER TABLE <table> ... ADD COLUMN ...`` statement adds.

    One statement may add several (008 adds fourteen to ``library_subscriptions`` in one), so
    each ``ALTER`` is taken up to its terminating ``;`` and every ``ADD COLUMN`` inside it read.
    """
    added: List[str] = []
    for match in re.finditer(
        r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:public\.)?" + table + r"\b",
        sql,
        re.IGNORECASE,
    ):
        end = sql.find(";", match.end())
        end = len(sql) if end == -1 else end
        statement = sql[match.end() : end]
        added.extend(
            name
            for name in re.findall(
                r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
                statement,
                re.IGNORECASE,
            )
        )
    return added


def _columns_the_migrations_create(table: str) -> Dict[str, str]:
    """``{column: "file:line"}`` for every column a migration creates on ``table``."""
    found: Dict[str, str] = {}
    for directory in _MIGRATION_DIRS:
        for path in sorted(directory.glob("*.sql")):
            sql = _blank_sql_comments(path.read_text(encoding="utf-8", errors="replace"))
            for column in _create_table_columns(sql, table) + _added_columns(sql, table):
                found.setdefault(column, path.name)
    return found


def _requested_columns(projection: str) -> List[str]:
    """The columns a PostgREST ``select`` string requests FROM THE OUTER TABLE.

    Only depth-0 tokens. Everything inside ``(...)`` belongs to an embedded resource - a
    different table, whose columns this guard must not attribute to the outer one. The relation
    names themselves (``library_strategies!inner``, and any bare ``name(...)``) are dropped too:
    they are relations, not columns.
    """
    columns: List[str] = []
    token: List[str] = []
    depth = 0
    embedded = False
    for ch in projection:
        if ch == "(":
            depth += 1
            if depth == 1:
                embedded = True
            continue
        if ch == ")":
            depth -= 1
            continue
        if depth:
            continue
        if ch == ",":
            name = "".join(token).strip()
            if name and not embedded and "!" not in name and ":" not in name:
                columns.append(name)
            token = []
            embedded = False
            continue
        token.append(ch)
    name = "".join(token).strip()
    if name and not embedded and "!" not in name and ":" not in name:
        columns.append(name)
    return columns


class TestTheOwnedProjectionNamesNoPhantomColumn:
    """The 42703 regression, pinned to the column and the log line that found it."""

    def test_description_is_not_requested_from_the_strategies_table(self) -> None:
        """``column strategies.description does not exist`` - production 42703.

        ``OWNED_STRATEGY_SELECT`` asked for it, so round trip 1 failed on every call and
        ``GET /api/library/my-strategies`` answered ``MARKETPLACE_READ_FAILED`` every time.
        """
        assert "description" not in _requested_columns(le.OWNED_STRATEGY_SELECT), (
            "OWNED_STRATEGY_SELECT requests `strategies.description`, which does not exist in "
            "the live schema. Production logged "
            "ERROR:backend_app.routers.library:my_strategies owned-read failed ... "
            "{'code': '42703', 'message': 'column strategies.description does not exist'}, "
            "and the Strategies page showed 'Ownership list unavailable - Server error "
            "occurred.' for every user."
        )

    def test_an_owned_entry_omits_description_rather_than_nulling_it(self) -> None:
        """A fact the system does not have is reported as absent, never invented.

        ``""`` or ``null`` here would say "we read the description and it was empty", which is a
        different claim from "this build does not carry one".
        """
        entries = le.build_my_strategies_entries(
            caller_id=CALLER_ID,
            owned_rows=[_owned_row()],
            subscription_rows=[],
            session_rows=[],
            now=NOW,
        )
        assert len(entries) == 1
        assert entries[0]["ownership"] == le.OWNERSHIP_OWNED
        assert "description" not in entries[0]

    def test_a_row_that_somehow_carries_one_still_yields_no_description_key(self) -> None:
        """Non-vacuity: the key is absent because nothing projects it, not because the fixture
        happens to lack it."""
        row = dict(_owned_row())
        row["description"] = "a value the live column cannot supply"
        entries = le.build_my_strategies_entries(
            caller_id=CALLER_ID,
            owned_rows=[row],
            subscription_rows=[],
            session_rows=[],
            now=NOW,
        )
        assert "description" not in entries[0]

    def test_the_subscribed_half_still_carries_the_listings_own_description(self) -> None:
        """Scope guard. ``library_strategies.description`` is a real column on a different table
        and is what the product actually shows; this fix removed the ``strategies`` one only."""
        entries = le.build_my_strategies_entries(
            caller_id=CALLER_ID,
            owned_rows=[],
            subscription_rows=[_subscription_row()],
            session_rows=[],
            now=NOW,
        )
        assert entries[0]["listing"]["description"] == "A subscribed strategy card."


class TestEveryOwnedColumnIsOneSomethingCreates:
    """The general guard: no projection may name a column nothing creates.

    Applied to the two projections whose table the repository actually declares
    (``strategies`` through its ``ALTER``s, ``paper_sessions`` through 009's ``CREATE TABLE``)
    and to ``library_subscriptions``' own columns. The Listing embed's column list is
    ``listing_projection.LISTING_SELECT``, which is the marketplace browse path's canonical
    projection and is already bound to ``marketplace.COLUMN_CONTRACT`` by
    ``tests/test_marketplace_paper_schema_contract.py``; it is not re-checked here, so there is
    one owner of that assertion rather than two that can disagree.
    """

    PROJECTIONS = (
        ("strategies", "OWNED_STRATEGY_SELECT"),
        ("library_subscriptions", "SUBSCRIPTION_SELECT"),
        ("paper_sessions", "RUNNING_PAPER_SESSION_SELECT"),
    )

    @pytest.mark.parametrize("table,constant", PROJECTIONS)
    def test_every_requested_column_is_created_or_declared_out_of_repo(
        self, table: str, constant: str
    ) -> None:
        created = _columns_the_migrations_create(table)
        unexplained = []
        for column in _requested_columns(getattr(le, constant)):
            if column in created:
                continue
            if (table, column) in OUT_OF_REPO_COLUMNS:
                continue
            unexplained.append(column)
        assert not unexplained, (
            f"{constant} requests {table} column(s) {sorted(unexplained)} that no migration in "
            f"{[d.name for d in _MIGRATION_DIRS]} creates and that OUT_OF_REPO_COLUMNS does not "
            "explain. Either a migration creates it, or add it to OUT_OF_REPO_COLUMNS with the "
            "evidence for its existence. This is the 42703 that killed "
            "GET /api/library/my-strategies; a projection is the one place a column name is "
            "never checked until production."
        )

    def test_the_scanner_actually_finds_the_columns_the_migrations_add(self) -> None:
        """Non-vacuity. A scanner that found nothing would make the guard above pass for any
        projection at all, which is worse than no guard."""
        strategies = _columns_the_migrations_create("strategies")
        assert strategies.get("status") == "001_strategy_architecture.sql"
        assert strategies.get("archived_at") == "005a_strategy_archive.sql"
        # From the ROOT migrations/ directory, not backend_app/migrations/ - the reason both are
        # scanned.
        assert strategies.get("is_active") == "006_reconcile_production_database.sql"

        sessions = _columns_the_migrations_create("paper_sessions")
        for column in ("id", "listing_id", "source_strategy_id", "session_state"):
            assert sessions.get(column) == "009_paper_trading.sql", column

        subscriptions = _columns_the_migrations_create("library_subscriptions")
        assert subscriptions.get("period_expiry") == "008_marketplace_settlement.sql"
        assert subscriptions.get("renewal_enabled") == "008_marketplace_settlement.sql"

    def test_the_embeds_columns_are_not_attributed_to_the_outer_table(self) -> None:
        """``library_strategies``' and ``marketplace_submissions``' columns belong to those
        tables. Crediting them to ``library_subscriptions`` would make the guard above report
        every one of them as phantom, and the noise would get the guard deleted."""
        outer = _requested_columns(le.SUBSCRIPTION_SELECT)
        assert outer == ["id", "library_id", "status", "period_expiry", "renewal_enabled"]
        assert "submission_state" not in outer
        assert not any("library_strategies" in column for column in outer)

    def test_the_scanner_does_not_credit_a_commented_out_migration(self) -> None:
        """005a and rls_rollback.sql both discuss ``strategies`` DDL inside ``--`` comments."""
        sql = _blank_sql_comments(
            "-- ALTER TABLE strategies ADD COLUMN IF NOT EXISTS invented_by_a_comment TEXT;\n"
            "ALTER TABLE strategies ADD COLUMN IF NOT EXISTS really_added TEXT;\n"
        )
        added = _added_columns(sql, "strategies")
        assert "invented_by_a_comment" not in added
        assert "really_added" in added

    @pytest.mark.parametrize("table,constant", PROJECTIONS)
    def test_no_projection_requests_a_column_proven_absent(
        self, table: str, constant: str
    ) -> None:
        """The phantom list is a floor, not a suggestion."""
        requested = set(_requested_columns(getattr(le, constant)))
        for (phantom_table, column), reason in PHANTOM_COLUMNS.items():
            if phantom_table != table:
                continue
            assert column not in requested, (
                f"{constant} requests {table}.{column}, proven absent: {reason}"
            )

    def test_a_phantom_column_cannot_be_excused_as_out_of_repo(self) -> None:
        """The escape hatch is for columns whose existence the repo cannot prove, not for
        columns the database has already refused."""
        overlap = set(PHANTOM_COLUMNS) & set(OUT_OF_REPO_COLUMNS)
        assert not overlap, (
            f"{sorted(overlap)} is both proven absent and excused as out-of-repo. A 42703 is "
            "evidence, not an unknown."
        )

    def test_every_out_of_repo_entry_carries_a_real_reason(self) -> None:
        """An empty reason turns the map into a silent allow-list."""
        for key, reason in OUT_OF_REPO_COLUMNS.items():
            assert isinstance(reason, str) and len(reason.strip()) > 40, key

    def test_no_out_of_repo_entry_is_stale(self) -> None:
        """A column a migration DOES create must not sit in the unverifiable map, or the map
        stops meaning "the repo cannot check this"."""
        stale = [
            (table, column)
            for (table, column) in OUT_OF_REPO_COLUMNS
            if column in _columns_the_migrations_create(table)
        ]
        assert not stale, (
            f"{sorted(stale)} are created by a migration and need no out-of-repo excuse."
        )
