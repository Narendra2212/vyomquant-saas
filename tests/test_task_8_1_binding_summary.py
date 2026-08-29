"""
tests/test_task_8_1_binding_summary.py

The collect-all deployment validation summary, held in place.

Spec: trading-lifecycle-integration task 8.1. ``design.md`` -> "Collect-all deployment
validation (Requirement 13.2)" (``ALGORITHM evaluate_binding_collect_all``) and -> the
preflight response shape. Requirement 13.2, with 13.3's status vocabulary.

WHAT THESE TESTS HOLD IN PLACE
------------------------------
* **13.2 - every failed condition, not the first.** A request that fails two independent
  gates reports two failures. This is the whole point of the second calling convention:
  ``evaluate_binding`` stops at the first, and a summary that did the same would leave the
  author guessing about the next one.

* **``pending`` is not ``passed``.** A gate whose input a failed gate was supposed to
  produce is reported ``pending`` naming its blocker, and a gate that could not be
  evaluated at all is reported ``pending`` carrying ``CONDITION_NOT_EVALUATED``. Neither
  counts toward ``deployable`` - the invariant this spec's earlier tasks established for
  every migration-dependent check: never report a condition as satisfied when it could not
  be checked.

* **The gates are the write path's own.** The condition set is required to cover every gate
  ``evaluate_binding`` calls, by reading both functions' source. If a gate were added to
  the write path and not here, the preflight would enable the Deploy button for a request
  the write path refuses - the one failure mode this read-only surface must not have.

* **Read-only.** The fake PostgREST client records every insert and update it is asked to
  perform; the summary is required to have asked for none.

* **004e unapplied.** ``mode='live'`` with the binding columns absent is reported as the
  ``BINDING_NOT_STORABLE`` failure naming the migration file, not as a 500 and not as a
  deployable summary.

The doubles here are deliberately the ones ``tests/test_task_8_2_deployment_binding.py``
already uses (the same ``_Supabase``/``_Query`` fake, the same real ``AssetUniverse``), so
the preflight is exercised against the same environment the write path is.
"""

import os
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import asset_universe as au
from backend_app.backend import deployment_binding as db
from backend_app.backend.strategy_builder import LIFECYCLE_READY

from tests.test_task_8_2_deployment_binding import (  # the same doubles, not a second set
    ACCOUNT_ID,
    MARKET_TYPE,
    OTHER_ACCOUNT_ID,
    RISK_ID,
    STRATEGY_ID,
    SYMBOL,
    TIMEFRAME,
    VENUE,
    _Supabase,
    _default_rows,
    _universe,
    _user,
    _version,
)

MODULE_PATH = Path(__file__).resolve().parents[1] / "backend_app" / "backend" / "deployment_binding.py"


@pytest.fixture(autouse=True)
def _clean_module_state():
    db.reset_binding_column_support()
    db.reset_venue_timeframe_cache()
    au.reset_asset_universe_state_for_tests()
    au._local_universe = _universe()  # noqa: SLF001 - reset_..._for_tests is the inverse
    yield
    db.reset_binding_column_support()
    db.reset_venue_timeframe_cache()
    au.reset_asset_universe_state_for_tests()


def _sb(**kwargs):
    return _Supabase(rows=_default_rows(), **kwargs)


async def _summary(sb=None, *, payload=None, version=None, user=None):
    return await db.evaluate_binding_summary(
        sb if sb is not None else _sb(),
        user or _user(),
        version or _version(),
        STRATEGY_ID,
        "v3.0",
        db.BindingRequest.from_payload(payload),
    )


def _statuses(summary):
    return {c.name: c.status for c in summary.conditions}


# ---------------------------------------------------------------------------
# 1. The condition set is the write path's gate set
# ---------------------------------------------------------------------------


class TestTheSummaryCoversTheWritePathsGates:
    def test_every_gate_evaluate_binding_calls_is_a_declared_condition(self):
        """The two calling conventions cannot silently diverge.

        ``design.md``: "two calling conventions over the same unchanged gate functions".
        A gate added to ``evaluate_binding`` and not to the summary would make the
        preflight say ``deployable: true`` about a request the write path refuses, so the
        gate names are compared as code rather than trusted to review.
        """
        source = MODULE_PATH.read_text(encoding="utf-8")
        write_path = source.split("async def evaluate_binding(", 1)[1].split("\n# ═", 1)[0]
        # Everything from the summary's own definition to the end of the file: its helpers
        # are defined below it and call the gates it delegates.
        summary_path = source.split("async def evaluate_binding_summary(", 1)[1]

        gates = set(
            re.findall(
                r"\b(assert_\w+|resolve_binding_\w+|load_\w+|normalise_\w+)\(", write_path
            )
        )
        assert len(gates) >= 10, f"only {sorted(gates)} were found in evaluate_binding"
        for gate in sorted(gates):
            assert gate in summary_path, (
                f"{gate} is gated on the write path but never called by the summary"
            )

    def test_conditions_are_reported_once_each_in_declaration_order(self):
        assert len(set(db.BINDING_CONDITIONS)) == len(db.BINDING_CONDITIONS)
        assert set(db.BINDING_CONDITION_DEPENDENCIES) == set(db.BINDING_CONDITIONS)

    def test_every_declared_dependency_is_itself_a_condition(self):
        for name, deps in db.BINDING_CONDITION_DEPENDENCIES.items():
            for dep in deps:
                assert dep in db.BINDING_CONDITIONS, f"{name} depends on unknown {dep}"
                # A dependency must be evaluated first, or it could never have passed.
                assert db.BINDING_CONDITIONS.index(dep) < db.BINDING_CONDITIONS.index(name)


# ---------------------------------------------------------------------------
# 2. A fully passing summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAFullyPassingSummary:
    async def test_every_condition_passes_and_the_summary_is_deployable(self):
        summary = await _summary(
            payload={"mode": "paper", "exchange_account_id": ACCOUNT_ID,
                     "risk_config_id": RISK_ID}
        )
        statuses = _statuses(summary)
        assert statuses == {name: db.CONDITION_PASSED for name in db.BINDING_CONDITIONS}
        assert summary.deployable is True
        assert summary.failed == () and summary.pending == ()

    async def test_the_response_shape_is_designs_shape(self):
        summary = await _summary(payload={"mode": "paper"})
        body = summary.to_dict()
        assert set(body) == {"deployable", "conditions"}
        assert isinstance(body["deployable"], bool)
        for condition in body["conditions"]:
            assert set(condition) <= {"name", "status", "detail", "code", "message", "reason"}
            assert {"name", "status"} <= set(condition)
            assert condition["status"] in db.CONDITION_STATUSES
            # Optional keys are absent rather than null - a UI that renders `detail`
            # unconditionally must not print "None".
            assert None not in condition.values()

    async def test_a_passing_summary_carries_the_binding_the_write_path_would_create(self):
        summary = await _summary(
            payload={"mode": "paper", "exchange_account_id": ACCOUNT_ID}
        )
        assert summary.binding is not None
        assert summary.binding.symbol == SYMBOL
        assert summary.binding.timeframe == TIMEFRAME
        assert summary.binding.market_type == MARKET_TYPE
        assert summary.binding.exchange_id == VENUE
        assert summary.binding.mode == db.MODE_PAPER

    async def test_the_summary_writes_nothing(self):
        """Requirement 13.3's summary is polled while the modal is open (13.6)."""
        sb = _sb()
        await _summary(sb, payload={"mode": "paper", "exchange_account_id": ACCOUNT_ID,
                                    "risk_config_id": RISK_ID})
        assert sb.inserts == []
        assert sb.updates == []


# ---------------------------------------------------------------------------
# 3. Requirement 13.2 - every failed condition, not the first
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCollectAllRatherThanFailFast:
    async def test_two_independent_failures_are_both_reported(self):
        """``evaluate_binding`` would raise on the lifecycle gate and never see the mode."""
        summary = await _summary(
            version=_version(lifecycle_state="DRAFT"), payload={"mode": "wishful"}
        )
        statuses = _statuses(summary)
        assert statuses["version_ready"] == db.CONDITION_FAILED
        assert statuses["deployment_mode"] == db.CONDITION_FAILED
        codes = {c.name: c.code for c in summary.failed}
        assert codes["version_ready"] == "VERSION_NOT_READY"
        assert codes["deployment_mode"] == "MODE_UNRECOGNISED"
        assert summary.deployable is False

    async def test_the_write_path_still_stops_at_the_first_failure(self):
        """The additive function must not have changed the write path's disposition."""
        with pytest.raises(db.DeployRejected) as excinfo:
            await db.evaluate_binding(
                _sb(),
                _user(),
                _version(lifecycle_state="DRAFT"),
                STRATEGY_ID,
                "v3.0",
                db.BindingRequest.from_payload({"mode": "wishful"}),
            )
        assert excinfo.value.code == "VERSION_NOT_READY"

    async def test_a_failed_condition_names_the_corrective_action(self):
        """13.2: "the corrective action required to resolve it"."""
        summary = await _summary(version=_version(lifecycle_state="TRAINING"))
        condition = summary.condition("version_ready")
        assert condition.status == db.CONDITION_FAILED
        assert condition.detail["outstanding_prerequisite"]
        assert condition.message and LIFECYCLE_READY in condition.message

    async def test_a_bad_account_and_a_bad_risk_config_are_reported_separately(self):
        """Two wrong ids are two conditions, so both can be corrected in one pass."""
        summary = await _summary(
            payload={"exchange_account_id": OTHER_ACCOUNT_ID, "risk_config_id": "nope"}
        )
        statuses = _statuses(summary)
        assert statuses["exchange_account"] == db.CONDITION_FAILED
        assert statuses["risk_config"] == db.CONDITION_FAILED
        codes = {c.name: c.code for c in summary.failed}
        assert codes["exchange_account"] == "EXCHANGE_ACCOUNT_NOT_FOUND"
        assert codes["risk_config"] == "RISK_CONFIG_NOT_FOUND"

    async def test_another_tenants_account_is_reported_as_not_found_not_forbidden(self):
        summary = await _summary(payload={"exchange_account_id": OTHER_ACCOUNT_ID})
        condition = summary.condition("exchange_account")
        assert condition.code == "EXCHANGE_ACCOUNT_NOT_FOUND"
        assert condition.http_status == 404


# ---------------------------------------------------------------------------
# 4. Dependents are PENDING, never silently skipped and never PASSED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDependentsArePending:
    async def test_symbol_and_timeframe_are_pending_when_the_account_fails(self):
        """``design.md``: "symbol availability depends on the resolved account"."""
        summary = await _summary(payload={"exchange_account_id": OTHER_ACCOUNT_ID})
        for name in ("symbol_available", "timeframe_supported"):
            condition = summary.condition(name)
            assert condition.status == db.CONDITION_PENDING
            assert condition.reason == "blocked by exchange_account"
            assert condition.detail["blocked_by"] == ["exchange_account"]

    async def test_market_and_models_are_pending_when_the_plan_is_unreadable(self):
        summary = await _summary(version=_version(compiled_plan="{not json"))
        statuses = _statuses(summary)
        assert statuses["plan_readable"] == db.CONDITION_FAILED
        for name in ("market_resolved", "models_verified"):
            assert statuses[name] == db.CONDITION_PENDING
        # And the gates that need the market are pending in turn, transitively.
        assert statuses["symbol_available"] == db.CONDITION_PENDING
        assert summary.condition("symbol_available").detail["blocked_by"] == [
            "market_resolved"
        ]

    async def test_a_pending_condition_never_counts_as_deployable(self):
        summary = await _summary(version=_version(compiled_plan="{not json"))
        assert summary.pending
        assert summary.deployable is False

    async def test_a_pending_gate_is_not_called_at_all(self):
        """A blocked gate must not read the asset universe to answer a settled question."""
        with patch(
            "backend_app.backend.deployment_binding.assert_symbol_available",
            AsyncMock(side_effect=AssertionError("must not be called")),
        ):
            summary = await _summary(payload={"exchange_account_id": OTHER_ACCOUNT_ID})
        assert summary.condition("symbol_available").status == db.CONDITION_PENDING

    async def test_a_gate_that_cannot_be_evaluated_is_pending_not_passed(self):
        """The invariant every migration-dependent check in this spec observes."""
        with patch.object(
            au, "read_cached_universe", AsyncMock(side_effect=RuntimeError("no cache"))
        ):
            summary = await _summary(payload={"exchange_account_id": ACCOUNT_ID})
        condition = summary.condition("symbol_available")
        assert condition.status == db.CONDITION_PENDING
        assert condition.code == db.CONDITION_NOT_EVALUATED
        assert summary.deployable is False


# ---------------------------------------------------------------------------
# 5. 004e unapplied - degrade with the migration named, never a 500
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMigrationDegradation:
    async def test_live_without_the_binding_columns_is_a_failed_condition(self, caplog):
        sb = _Supabase(rows=_default_rows(), absent_columns=db.BINDING_COLUMNS)
        with caplog.at_level("WARNING"):
            summary = await _summary(
                sb, payload={"mode": "live", "exchange_account_id": ACCOUNT_ID}
            )
        condition = summary.condition("binding_storable")
        assert condition.status == db.CONDITION_FAILED
        assert condition.code == "BINDING_NOT_STORABLE"
        assert db.DEPLOYMENT_BINDING_MIGRATION in condition.detail["migration"]
        assert "004e_deployment_bindings.sql" in caplog.text
        assert summary.deployable is False

    async def test_paper_without_the_binding_columns_still_reports_the_migration(self):
        sb = _Supabase(rows=_default_rows(), absent_columns=db.BINDING_COLUMNS)
        summary = await _summary(sb, payload={"mode": "paper"})
        condition = summary.condition("binding_storable")
        assert condition.status == db.CONDITION_PASSED
        assert condition.detail["binding_stored"] is False
        assert condition.detail["migration"] == db.DEPLOYMENT_BINDING_MIGRATION

    async def test_live_with_no_account_reports_13_6_rather_than_the_columns(self):
        summary = await _summary(_sb(), payload={"mode": "live"})
        assert summary.condition("deployment_mode").code == "LIVE_REQUIRES_EXCHANGE_ACCOUNT"
        # The 004e question needs the mode gate's output, so it is pending, not assumed.
        assert summary.condition("binding_storable").status == db.CONDITION_PENDING


# ---------------------------------------------------------------------------
# 6. Boundaries of the summary object itself
# ---------------------------------------------------------------------------


class TestSummaryObject:
    def test_an_empty_summary_is_not_deployable(self):
        """"Nothing was checked" must never enable the Deploy button."""
        assert db.BindingSummary(conditions=()).deployable is False

    def test_a_condition_omits_absent_optional_keys(self):
        condition = db.BindingCondition(name="x", status=db.CONDITION_PASSED)
        assert condition.to_dict() == {"name": "x", "status": db.CONDITION_PASSED}

    def test_deployable_requires_every_condition_to_pass(self):
        passed = db.BindingCondition(name="a", status=db.CONDITION_PASSED)
        pending = db.BindingCondition(name="b", status=db.CONDITION_PENDING)
        failed = db.BindingCondition(name="c", status=db.CONDITION_FAILED)
        assert db.BindingSummary(conditions=(passed,)).deployable is True
        assert db.BindingSummary(conditions=(passed, pending)).deployable is False
        assert db.BindingSummary(conditions=(passed, failed)).deployable is False
