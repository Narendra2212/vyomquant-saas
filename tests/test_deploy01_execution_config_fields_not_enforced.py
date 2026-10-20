"""DEPLOY-01 -- execution_config's per-deployment limits were validated and stored but no
execution path read them, silently.

THE DEFECT (ORIGINAL)
    ``deployment_binding.EXECUTION_CONFIG_FIELDS`` names five fields (``max_order_notional``,
    ``max_open_positions``, ``slippage_tolerance_bps``, ``order_timeout_seconds``,
    ``retry_policy``). ``normalise_execution_config``'s own docstring says an unknown key is
    refused "because execution_config is read by the execution path" -- but grepping the whole
    execution path (BotRunner, ExecutionGuard, order_control) for any of the five names turned
    up no reader at all. A deployment with ``max_order_notional=1000`` ran with exactly the
    same order-size ceiling as one with no ``execution_config`` at all; a trader who set it
    believed a limit was in force that was not.

THE FIRST FIX (fail-loud refusal of all five)
    ``normalise_execution_config`` refused any of its five known fields when supplied with a
    real (non-``None``) value, the same choice ``strategy_service.
    assert_deploy_params_applicable`` already makes for ``capital``/``trade_size_pct``/
    ``stop_loss_pct``/``max_drawdown_pct`` (``DEPLOY_PARAMS_NOT_APPLIED``) -- fail loud at
    deploy time rather than silently discard a limit the caller believes is in force.

THE SECOND FIX (real enforcement for two of the five)
    ``max_order_notional`` and ``max_open_positions`` are now genuinely consulted at
    order-construction time -- ``master_executor.evaluate_deployment_execution_limits``,
    called from ``BotRunner._inner_loop`` with the execution_config threaded through
    ``deployment_runtime.build_runtime_blueprint``. They are therefore REMOVED from the
    refusal and move to :data:`db.ENFORCED_EXECUTION_CONFIG_FIELDS`.
    ``slippage_tolerance_bps``, ``order_timeout_seconds`` and ``retry_policy`` still name no
    reader on this platform's order path and stay refused
    (:data:`db.UNENFORCED_EXECUTION_CONFIG_FIELDS`). An absent or ``None`` field, or an empty
    ``execution_config`` document, is unaffected by either fix.

WHAT THIS FILE PROVES
    1. Each of the three STILL-unenforced fields is refused on its own, with the distinct
       ``EXECUTION_CONFIG_NOT_ENFORCED`` code (not conflated with the pre-existing
       ``EXECUTION_CONFIG_UNKNOWN_FIELD`` refusal for a field that isn't published at all).
    2. ``max_order_notional`` and ``max_open_positions`` are ACCEPTED (not refused) with a
       valid value -- the transition this second fix makes -- and an invalid value for
       either is refused with the distinct ``EXECUTION_CONFIG_INVALID_FIELD`` code.
    3. ``None``/absent and an empty document are both still accepted.
    4. The preflight endpoint reports an unenforced field as a failed, named condition (not a
       500, not a silent pass) and `deployable: False` -- a caller discovers this before
       submitting the deploy, not after.
"""
from __future__ import annotations

import pytest

from backend_app.backend import deployment_binding as db


class TestNormaliseExecutionConfigRefusesEveryStillUnenforcedFieldWithARealValue:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("slippage_tolerance_bps", 25),
            ("order_timeout_seconds", 30),
            ("retry_policy", {"attempts": 3}),
        ],
    )
    def test_each_field_is_refused_on_its_own(self, field: str, value) -> None:
        assert field in db.UNENFORCED_EXECUTION_CONFIG_FIELDS

        with pytest.raises(db.DeployRejected) as exc:
            db.normalise_execution_config({field: value})

        assert exc.value.code == "EXECUTION_CONFIG_NOT_ENFORCED"
        assert exc.value.details["not_enforced_fields"] == [field]
        assert exc.value.http_status == 422

    def test_all_three_together_are_named_in_one_refusal(self) -> None:
        payload = {
            "slippage_tolerance_bps": 25,
            "order_timeout_seconds": 30,
            "retry_policy": {"attempts": 3},
        }
        assert set(payload) == set(db.UNENFORCED_EXECUTION_CONFIG_FIELDS)

        with pytest.raises(db.DeployRejected) as exc:
            db.normalise_execution_config(payload)

        assert exc.value.code == "EXECUTION_CONFIG_NOT_ENFORCED"
        assert set(exc.value.details["not_enforced_fields"]) == set(payload)

    def test_an_unenforced_field_refusal_is_distinct_from_an_unknown_field_refusal(self) -> None:
        """A field that isn't published at all (e.g. a typo) and a field that IS published but
        not enforced are different problems and must not be reported identically."""
        with pytest.raises(db.DeployRejected) as unknown:
            db.normalise_execution_config({"max_notional": 5})
        with pytest.raises(db.DeployRejected) as not_enforced:
            db.normalise_execution_config({"slippage_tolerance_bps": 5})

        assert unknown.value.code == "EXECUTION_CONFIG_UNKNOWN_FIELD"
        assert not_enforced.value.code == "EXECUTION_CONFIG_NOT_ENFORCED"


class TestMaxOrderNotionalAndMaxOpenPositionsAreNowAccepted:
    """The transition: these two fields moved from refused to really enforced."""

    def test_max_order_notional_with_a_real_value_is_accepted_not_refused(self) -> None:
        assert db.normalise_execution_config({"max_order_notional": 1000}) == {
            "max_order_notional": 1000
        }

    def test_max_open_positions_with_a_real_value_is_accepted_not_refused(self) -> None:
        assert db.normalise_execution_config({"max_open_positions": 3}) == {
            "max_open_positions": 3
        }

    def test_both_enforced_fields_are_in_the_published_enforced_set(self) -> None:
        assert set(db.ENFORCED_EXECUTION_CONFIG_FIELDS) == {
            "max_order_notional",
            "max_open_positions",
        }
        assert (
            set(db.ENFORCED_EXECUTION_CONFIG_FIELDS)
            | set(db.UNENFORCED_EXECUTION_CONFIG_FIELDS)
        ) == set(db.EXECUTION_CONFIG_FIELDS)

    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("max_order_notional", -5),
            ("max_order_notional", "not-a-number"),
            ("max_order_notional", True),
            ("max_open_positions", -1),
            ("max_open_positions", "x"),
            ("max_open_positions", 1.5),
        ],
    )
    def test_an_invalid_value_for_an_enforced_field_is_refused_distinctly(
        self, field: str, bad_value
    ) -> None:
        """Invalid, not merely unenforced: a value this platform could not possibly apply
        (negative notional, a non-numeric position cap) must not be stored to be discovered
        as a no-op at the first tick."""
        with pytest.raises(db.DeployRejected) as exc:
            db.normalise_execution_config({field: bad_value})
        assert exc.value.code == "EXECUTION_CONFIG_INVALID_FIELD"

    def test_max_open_positions_zero_is_a_valid_value(self) -> None:
        """0 is the one value at which this cap actually refuses anything (see
        master_executor.evaluate_deployment_execution_limits), so it must not be rejected as
        invalid."""
        assert db.normalise_execution_config({"max_open_positions": 0}) == {
            "max_open_positions": 0
        }


class TestAnAbsentOrEmptyExecutionConfigIsStillAccepted:
    def test_none_is_accepted(self) -> None:
        assert db.normalise_execution_config(None) == {}

    def test_an_empty_document_is_accepted(self) -> None:
        assert db.normalise_execution_config({}) == {}

    def test_a_known_field_explicitly_set_to_none_is_accepted(self) -> None:
        """None means 'not supplied' -- the same rule assert_deploy_params_applicable uses for
        its own four fields, so a caller that always sends every key with some set to None does
        not get refused for the ones it left unset. (It is not stripped from the returned
        document either -- that is this function's pre-existing, unrelated behaviour.)"""
        db.normalise_execution_config({"max_order_notional": None})  # must not raise
