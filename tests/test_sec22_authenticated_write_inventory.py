"""SEC-22: the write-inventory analyzer must keep rediscovering the known misses.

SEC-22's own evidence records that deriving the path-(A) table inventory from source
"proved unreliable three times running": the routers wrap the two client factories in
local helpers (`_sb`, `_get_supabase`, `_client`), so call sites do not grep as factory
names, and two full manual passes produced lists that omitted real writers. Every omission,
had it driven a REVOKE, would have taken a working production feature offline.

So the analyzer that replaced those passes is only worth anything if it keeps finding the
call sites they missed. That is what these tests pin. They are a negative control, not a
smoke test: if a future refactor moves a client behind one more layer of indirection and
the analyzer stops seeing through it, the inventory silently shrinks and the next revoke
is the one that breaks production. This test is the thing standing between that refactor
and that outcome.

No database access: this reads source only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join("scripts", "sec22_authenticated_write_inventory.py")


@pytest.fixture(scope="module")
def inventory():
    proc = subprocess.run(
        [sys.executable, SCRIPT],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, (
        "the analyzer exits non-zero when its own negative control fails:\n"
        + proc.stdout[-4000:] + proc.stderr[-2000:]
    )
    path = os.path.join(REPO_ROOT, "reports",
                        "sec22_authenticated_write_inventory.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# The four call sites two manual passes missed, each named in SEC-22's own evidence and in
# scripts/check_production_privileges.py's PATH_A_ALLOWLIST "why" strings.
# `strategy_backtests` was the first entry and MKT-10 removed the thing it named: that
# module's three writes moved to the service-role client so `authenticated` could lose
# INSERT/UPDATE/DELETE on the table (migration 074). It is asserted in the inverse below.
KNOWN_MISSES = [
    "training_jobs",
    "training_governance_decisions",
    "strategies",
]


class TestNegativeControl:
    @pytest.mark.parametrize("table", KNOWN_MISSES)
    def test_each_known_miss_is_still_discovered(self, inventory, table):
        found = inventory["authenticated_tables"]
        assert table in found, (
            f"{table} is written by a role-`authenticated` client, but the analyzer no "
            f"longer sees it. A revoke driven by this inventory would break a production "
            f"feature. Discovered tables: {sorted(found)}"
        )

    def test_strategies_delete_specifically(self, inventory):
        # backend/strategy_service.py:2502 -- strategy deletion, reached from the UI.
        # The table-level answer is not enough here: DELETE is the privilege the manual
        # passes missed, while INSERT/UPDATE were found.
        ops = inventory["authenticated_tables"].get("strategies", [])
        assert "DELETE" in ops, (
            f"strategies DELETE is reached from the UI via strategy_service.py:2502; "
            f"analyzer saw only {ops}"
        )

    def test_the_analyzer_reports_its_own_control_as_passing(self, inventory):
        assert inventory["negative_control_passed"] is True


class TestMkt10StaysClosed:
    """The inverse of a negative control: a table that must NOT appear.

    MKT-10 moved `backtest_service.py`'s three writes to the service-role client so that
    `authenticated` could lose INSERT/UPDATE/DELETE on `strategy_backtests` (migration
    074). That table is the five-backtest half of the marketplace listing Eligibility_Gate,
    and while a customer could write it they could fabricate their own listing evidence.

    `strategy_backtests` was therefore removed from KNOWN_MISSES above -- it is no longer
    a miss, it is a fixed defect. Asserting its ABSENCE here is what stops the fix being
    quietly undone: if any write to that table moves back onto a caller-supplied client,
    the analyzer finds it again and this fails.
    """

    def test_strategy_backtests_is_not_written_by_a_browser_role_client(self, inventory):
        found = inventory["authenticated_tables"]
        assert "strategy_backtests" not in found, (
            "strategy_backtests is written by a role-`authenticated` client again. "
            "MKT-10 moved those writes to the service-role client so migration 074 could "
            "revoke INSERT/UPDATE/DELETE from `authenticated`; with the grant revoked, "
            "that write now FAILS with 42501, and with it restored a customer can forge "
            "the backtest half of their own marketplace listing evidence. "
            f"Writer(s) seen: {found.get('strategy_backtests')}"
        )

    def test_the_risk_limit_tables_are_not_written_by_a_browser_role_client(
        self, inventory
    ):
        """RISK-07, same class. `strategy_limits` holds caps `paper_trading_service`
        enforces and grants `authenticated` SELECT only, so its writer must be the
        service-role client -- a caller-scoped write there fails with 42501 in production
        and would also make the grant unrevokable."""
        found = inventory["authenticated_tables"]
        assert "strategy_limits" not in found, (
            "strategy_limits is written by a role-`authenticated` client. Production "
            "grants that role SELECT only, so the write fails with 42501; and the table "
            "holds risk caps that are enforced against the caller. Use the service-role "
            f"client. Writer(s) seen: {found.get('strategy_limits')}"
        )


class TestBlindSpotsStayClosed:
    def test_no_write_targets_an_unresolvable_table_name(self, inventory):
        # A write to a table this scan cannot name could name ANY table, which
        # invalidates every "nothing writes this" conclusion drawn from the inventory.
        # routers/legal.py's `supabase.table(TABLE).upsert(...)` was exactly this until
        # module-scoped constant resolution was added.
        assert inventory["non_literal_sites"] == [], (
            "these writes target a table name the scan cannot resolve, so no revoke "
            f"decision can be trusted: {inventory['non_literal_sites']}"
        )

    def test_unresolved_writers_are_reported_not_hidden(self, inventory):
        # Tables whose writing client could not be classified MUST remain listed. They
        # are the ones a revoke must never touch, and the drain tooling keys off this
        # list to exclude them. An empty list here would mean either real completeness
        # or a reporting regression, and the second is far likelier -- so assert the key
        # exists and is a list rather than asserting it is empty.
        assert isinstance(inventory["unresolved_role_tables"], list)
