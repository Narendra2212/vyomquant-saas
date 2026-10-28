"""DEPLOY-01: ``slippage_tolerance_bps`` is a pre-trade price band, and it is in force.

WHY THIS IS A BAND AND NOT A DETECTOR. The refusal this field carried for several sessions
said the order path is "a market-only, synchronous submit with no slippage-tolerance
check", so there was nothing to compare a tolerance against. That premise was wrong, and
these tests pin the measurement that replaced it:

    ExecutionEngine._execute_trade_internal:
        c_order_type = CoreOrderType.market if price <= 0 else CoreOrderType.limit
    master_executor._inner_loop:
        if price <= 0: continue

Every live bot order is therefore already a LIMIT at the decision price -- fillable there
or better, never worse. The path was never market-only, and it already had a slippage
bound of exactly zero. This field widens that bound by a stated amount.

The direction is the dangerous part. A BUY's worst acceptable price is ABOVE the decision
price; a SELL's is BELOW. Inverting the sign would NARROW the band instead of widening it,
and the symptom would be orders quietly failing to fill -- which reads as a market
condition, not a config bug. So the asymmetry is asserted directly, not implied.
"""
from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from backend_app.backend import deployment_binding as db
from backend_app.core.execution_engine import ExecutionEngine, bounded_limit_price

PRICE = Decimal("100")


class TestTheBandItself:
    def test_a_buy_band_is_above_the_decision_price(self):
        # 50 bps = 0.5%. A buy will pay at most 100.50.
        assert bounded_limit_price(PRICE, "buy", 50) == Decimal("100.50")

    def test_a_sell_band_is_below_the_decision_price(self):
        assert bounded_limit_price(PRICE, "sell", 50) == Decimal("99.50")

    def test_the_two_directions_move_opposite_ways(self):
        # THE ASYMMETRY GUARD. An inverted sign would still produce two different
        # numbers, so comparing them to each other is what catches it: the buy bound
        # must be the higher one.
        buy = bounded_limit_price(PRICE, "buy", 125)
        sell = bounded_limit_price(PRICE, "sell", 125)
        assert buy > PRICE > sell, (
            f"buy band {buy} and sell band {sell} must straddle the decision price "
            f"{PRICE}; if they do not, the sign is inverted and the band is tightening "
            f"the limit rather than widening it"
        )
        # and symmetrically far from it
        assert (buy - PRICE) == (PRICE - sell)

    @pytest.mark.parametrize("side", ["buy", "BUY", " Buy "])
    def test_side_matching_is_not_case_or_whitespace_sensitive(self, side):
        assert bounded_limit_price(PRICE, side, 10) > PRICE

    def test_a_bigger_tolerance_is_a_wider_band(self):
        assert (bounded_limit_price(PRICE, "buy", 200)
                > bounded_limit_price(PRICE, "buy", 100) > PRICE)


class TestTheNoToleranceCaseIsUnchanged:
    """NEGATIVE CONTROL. A deployment that sets nothing must send the price it always
    sent -- if this slips, the change stops being additive and every existing deployment
    silently gets a different limit price."""

    def test_none_returns_the_decision_price_untouched(self):
        assert bounded_limit_price(PRICE, "buy", None) == PRICE
        assert bounded_limit_price(PRICE, "sell", None) == PRICE

    def test_zero_returns_the_decision_price_untouched(self):
        assert bounded_limit_price(PRICE, "buy", 0) == PRICE

    def test_a_negative_tolerance_is_ignored_rather_than_inverting_the_band(self):
        # normalise_execution_config refuses these at deploy time, so reaching here means
        # something bypassed that gate. Ignoring is the safe reading: a negative value
        # must never be applied, because it would NARROW the limit below the decision
        # price on a buy.
        assert bounded_limit_price(PRICE, "buy", -50) == PRICE

    def test_the_market_order_case_is_left_alone(self):
        # price <= 0 is the branch where c_order_type falls back to `market`, and a limit
        # price is meaningless there.
        assert bounded_limit_price(Decimal("0"), "buy", 50) == Decimal("0")
        assert bounded_limit_price(Decimal("-1"), "buy", 50) == Decimal("-1")

    def test_a_nonsense_tolerance_does_not_raise_on_the_order_path(self):
        # This runs after mark_submitted; an exception here would be adjudicated by RT-17
        # as an ambiguous outcome and the order recorded "submission unknown". A bad
        # config value must not be able to do that.
        assert bounded_limit_price(PRICE, "buy", "not-a-number") == PRICE


class TestTheFieldIsAcceptedAtDeployTime:
    def test_it_is_declared_enforced(self):
        assert "slippage_tolerance_bps" in db.ENFORCED_EXECUTION_CONFIG_FIELDS
        assert "slippage_tolerance_bps" not in db.UNENFORCED_EXECUTION_CONFIG_FIELDS

    def test_retry_policy_is_the_only_field_still_refused(self):
        # It cannot be enforced without a price-stable idempotency key; see the note on
        # UNENFORCED_EXECUTION_CONFIG_FIELDS. If this set grows or shrinks, the reasoning
        # recorded there needs revisiting rather than the test relaxing.
        assert db.UNENFORCED_EXECUTION_CONFIG_FIELDS == ("retry_policy",)

    def test_a_real_value_is_accepted_and_preserved(self):
        out = db.normalise_execution_config({"slippage_tolerance_bps": 25})
        assert out == {"slippage_tolerance_bps": 25}

    # `True` is listed deliberately: isinstance(True, int) holds in Python, so a caller
    # who sent `true` would otherwise get a 1-bps band rather than a refusal.
    @pytest.mark.parametrize("bad", [0, -1, -0.5, True, "25", [], {}])
    def test_a_value_that_cannot_be_a_tolerance_is_refused(self, bad):
        with pytest.raises(db.DeployRejected):
            db.normalise_execution_config({"slippage_tolerance_bps": bad})

    @pytest.mark.parametrize("bad", [10000, 10001, 50000])
    def test_a_band_that_would_erase_the_bound_is_refused(self, bad):
        # At 10000 bps a sell limit is price * (1 - 1.0) == 0, i.e. no bound at all.
        with pytest.raises(db.DeployRejected) as exc:
            db.normalise_execution_config({"slippage_tolerance_bps": bad})
        assert "10000" in str(exc.value.details) or "10000" in str(exc.value)

    def test_just_under_the_ceiling_is_still_accepted(self):
        assert db.normalise_execution_config(
            {"slippage_tolerance_bps": 9999}
        ) == {"slippage_tolerance_bps": 9999}


class TestItIsPlumbedToTheOrderPath:
    def test_the_engine_accepts_the_per_order_value(self):
        sig = inspect.signature(ExecutionEngine.execute_with_idempotency)
        assert "slippage_tolerance_bps" in sig.parameters
        # Defaulted, so every existing caller and every executor double in the suite is
        # unaffected -- the lesson order_timeout_seconds learned the hard way when an
        # unconditional kwarg turned 7 working executions into "submission unknown".
        assert sig.parameters["slippage_tolerance_bps"].default is None

    def test_an_engine_starts_with_no_band(self):
        engine = ExecutionEngine.__new__(ExecutionEngine)
        assert getattr(engine, "_slippage_tolerance_bps", None) is None

    def test_both_bot_runner_call_sites_pass_it(self):
        # Entries AND exits. A band on entries alone would leave the position that is
        # already exposed unbounded, which is backwards.
        import backend_app.backend.master_executor as me

        source = inspect.getsource(me)
        assert source.count("slippage_tolerance_bps=self.execution_config.get") == 2, (
            "both execute_with_idempotency call sites in master_executor -- the entry in "
            "_inner_loop and the exit in the close path -- must pass the band"
        )
