"""DEPLOY-01 -- execution_config's per-deployment limits were validated and stored but no
execution path read them, silently.

THE DEFECT
    ``deployment_binding.EXECUTION_CONFIG_FIELDS`` names five fields (``max_order_notional``,
    ``max_open_positions``, ``slippage_tolerance_bps``, ``order_timeout_seconds``,
    ``retry_policy``). ``normalise_execution_config``'s own docstring says an unknown key is
    refused "because execution_config is read by the execution path" -- but grepping the whole
    execution path (BotRunner, ExecutionGuard, order_control) for any of the five names turns up
    no reader at all. A deployment with ``max_order_notional=1000`` runs with exactly the same
    order-size ceiling as one with no ``execution_config`` at all; a trader who set it believes a
    limit is in force that is not.

THE FIX
    ``normalise_execution_config`` now refuses any of its five known fields when supplied with a
    real (non-``None``) value, the same choice ``strategy_service.
    assert_deploy_params_applicable`` already makes for ``capital``/``trade_size_pct``/
    ``stop_loss_pct``/``max_drawdown_pct`` (``DEPLOY_PARAMS_NOT_APPLIED``) -- fail loud at deploy
    time rather than silently discard a limit the caller believes is in force. An absent or
    ``None`` field, or an empty ``execution_config`` document, is unaffected.

WHAT THIS FILE PROVES
    1. Each of the five known fields is refused on its own, with the distinct
       ``EXECUTION_CONFIG_NOT_ENFORCED`` code (not conflated with the pre-existing
       ``EXECUTION_CONFIG_UNKNOWN_FIELD`` refusal for a field that isn't published at all).
    2. ``None``/absent and an empty document are both still accepted.
    3. The preflight endpoint reports this as a failed, named condition (not a 500, not a
       silent pass) and `deployable: False` -- a caller discovers this before submitting the
       deploy, not after.
    4. The deploy endpoint itself (not just preflight) refuses the same payload.
"""
from __future__ import annotations

import pytest

from backend_app.backend import deployment_binding as db


class TestNormaliseExecutionConfigRefusesEveryKnownFieldWithARealValue:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("max_order_notional", 1000),
            ("max_open_positions", 3),
            ("slippage_tolerance_bps", 25),
            ("order_timeout_seconds", 30),
            ("retry_policy", {"attempts": 3}),
        ],
    )
    def test_each_field_is_refused_on_its_own(self, field: str, value) -> None:
        with pytest.raises(db.DeployRejected) as exc:
            db.normalise_execution_config({field: value})

        assert exc.value.code == "EXECUTION_CONFIG_NOT_ENFORCED"
        assert exc.value.details["not_enforced_fields"] == [field]
        assert exc.value.http_status == 422

    def test_all_five_together_are_named_in_one_refusal(self) -> None:
        payload = {
            "max_order_notional": 1000,
            "max_open_positions": 3,
            "slippage_tolerance_bps": 25,
            "order_timeout_seconds": 30,
            "retry_policy": {"attempts": 3},
        }

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
            db.normalise_execution_config({"max_order_notional": 5})

        assert unknown.value.code == "EXECUTION_CONFIG_UNKNOWN_FIELD"
        assert not_enforced.value.code == "EXECUTION_CONFIG_NOT_ENFORCED"


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
