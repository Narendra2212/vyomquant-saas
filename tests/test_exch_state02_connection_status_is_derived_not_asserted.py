"""EXCH-STATE-02: the connections endpoint may not assert a verdict it never measured.

WHAT WAS BROKEN
---------------
`GET /api/exchange/` wrote three literals for EVERY stored credential row:

    "status": "CONNECTED",
    "permissions": ["Spot Trading", "Read"],
    "health": "healthy",

A row nobody had ever probed therefore reported itself connected, healthy, and holding
two permissions nothing had measured -- while the fields immediately below it in the same
dict (`validation_status`, `last_validated_at`, `can_trade`) carried the real answer,
which for most rows was "nothing has checked".

That is the Part 3 inconsistency, and both halves were reading the same row: the Dashboard
derives from those fields through `dashboard_aggregation_service.credential_validity` and
said "unknown", while this endpoint asserted "connected". A customer saw a healthy
exchange on one page and an unknown one on another.

WHY THE FIX IMPORTS RATHER THAN DERIVES
---------------------------------------
There were already THREE readings of this row: this endpoint's literals, the dashboard
service's derivation, and a JavaScript re-implementation in `ExchangeManager.jsx`
(`credentialVerdict`). The brief asks for one authoritative, consistently interpreted
state. Adding a fourth derivation -- even a correct one -- leaves the same class of defect
for the next reader, so the router now consults the dashboard service's vocabulary. These
tests assert that it keeps consulting it, not that it computes the right answer twice.
"""
from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

from backend_app.backend.dashboard_aggregation_service import (
    VENUE_STATUS_CONNECTED, VENUE_STATUS_INVALID_CREDENTIALS, VENUE_STATUS_UNKNOWN)
from backend_app.routers import exchange as exchange_router

REPO = pathlib.Path(__file__).resolve().parents[1]


class TestTheThreeVerdictsAreDistinct:
    """Unknown, invalid and connected are three different findings, not two."""

    def test_an_unprobed_row_is_unknown_not_connected(self):
        row = {"id": "k1", "is_active": True}  # nothing has validated it
        verdict = exchange_router._credential_validity(row)
        assert verdict is None, (
            "a row with no validation_status and no can_trade must not resolve to a "
            "verdict -- `is_active` says the user enabled the key, not that anything "
            "checked it"
        )
        assert exchange_router._CONNECTION_STATUS_BY_VALIDITY[verdict] == (
            VENUE_STATUS_UNKNOWN.upper()
        )
        assert exchange_router._CONNECTION_HEALTH_BY_VALIDITY[verdict] == "unknown"

    def test_a_validated_tradable_row_is_connected_and_healthy(self):
        row = {"id": "k1", "is_active": True, "can_trade": True}
        verdict = exchange_router._credential_validity(row)
        assert verdict is True
        assert exchange_router._CONNECTION_STATUS_BY_VALIDITY[verdict] == (
            VENUE_STATUS_CONNECTED.upper()
        )
        assert exchange_router._CONNECTION_HEALTH_BY_VALIDITY[verdict] == "healthy"

    def test_a_withdrawn_credential_is_invalid_not_unknown(self):
        """A known negative is a different finding from an absent measurement."""
        row = {"id": "k1", "can_trade": False}
        verdict = exchange_router._credential_validity(row)
        assert verdict is False
        assert exchange_router._CONNECTION_STATUS_BY_VALIDITY[verdict] == (
            VENUE_STATUS_INVALID_CREDENTIALS.upper()
        )
        assert exchange_router._CONNECTION_HEALTH_BY_VALIDITY[verdict] == "unhealthy"

    def test_a_deactivated_key_is_invalid(self):
        assert exchange_router._credential_validity({"is_active": False}) is False

    def test_the_three_mappings_are_total_over_the_three_valued_return(self):
        """`credential_validity` returns True/False/None and every one must map."""
        for verdict in (True, False, None):
            assert verdict in exchange_router._CONNECTION_STATUS_BY_VALIDITY
            assert verdict in exchange_router._CONNECTION_HEALTH_BY_VALIDITY


class TestPermissionsAreMeasured:
    def test_an_unprobed_row_reports_no_permissions(self):
        assert exchange_router._measured_permissions({"is_active": True}) == [], (
            "the replaced literal claimed Spot Trading and Read for every row, including "
            "rows no probe had touched"
        )

    def test_an_invalid_row_reports_no_permissions(self):
        assert exchange_router._measured_permissions({"can_trade": False}) == []

    def test_trading_permission_requires_a_positive_can_trade(self):
        """`None` is not a yes. Requirement 2.6 makes the unknown non-tradable."""
        row = {"validation_status": "valid", "can_trade": None}
        perms = exchange_router._measured_permissions(row)
        assert "Spot Trading" not in perms

    def test_a_tradable_row_reports_both(self):
        perms = exchange_router._measured_permissions(
            {"validation_status": "valid", "can_trade": True}
        )
        assert set(perms) == {"Spot Trading", "Read"}

    def test_a_validated_but_read_only_row_reports_read(self):
        perms = exchange_router._measured_permissions(
            {"validation_status": "valid", "can_trade": False}
        )
        # can_trade False makes the credential invalid overall, so nothing is claimed.
        assert "Spot Trading" not in perms

    def test_a_non_dict_is_not_a_permission_grant(self):
        assert exchange_router._measured_permissions(None) == []


class TestTheLiteralsAreGone:
    """The defect was a hardcoded string, so its absence is what regresses."""

    def test_the_connections_response_no_longer_hardcodes_a_verdict(self):
        source = inspect.getsource(exchange_router)
        tree = ast.parse(source)

        # Find every dict literal that builds a connection row -- identified by the
        # `credential_configured` key, which only that dict carries -- and assert its
        # status/health/permissions values are expressions, not constants.
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = {
                k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            if "credential_configured" not in keys:
                continue
            for key, value in zip(node.keys, node.values):
                if not (isinstance(key, ast.Constant) and key.value in
                        ("status", "health", "permissions")):
                    continue
                if isinstance(value, ast.Constant):
                    offenders.append((key.value, value.value))
                elif isinstance(value, ast.List) and all(
                    isinstance(e, ast.Constant) for e in value.elts
                ):
                    offenders.append((key.value, [e.value for e in value.elts]))

        assert not offenders, (
            "the connections response hardcodes a verdict again: "
            f"{offenders}. A stored credential row is not evidence that a venue answered "
            "-- derive from credential_validity, which returns True/False/None."
        )

    def test_the_router_imports_the_dashboards_vocabulary(self):
        """A fourth derivation is the defect, not the fix."""
        source = (REPO / "backend_app" / "routers" / "exchange.py").read_text(
            encoding="utf-8"
        )
        assert "dashboard_aggregation_service import" in source, (
            "the router no longer consults the dashboard service's derivation, so there "
            "are two readings of one row again -- which is the Part 3 defect"
        )

    def test_permissions_measured_is_reported(self):
        """"No permissions" and "no measurement" must be distinguishable by a caller."""
        source = (REPO / "backend_app" / "routers" / "exchange.py").read_text(
            encoding="utf-8"
        )
        assert "permissions_measured" in source, (
            "an empty permissions list cannot express whether anything was measured, "
            "and that decides whether the UI says 'not permitted' or 'not yet checked'"
        )
