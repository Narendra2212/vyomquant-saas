"""LIVE-EXIT-01 -- a LIVE deployment of a strategy that can only open positions is refused.

Production, 2026-11-20 (Binance Testnet): strategy "vyom" was a single *buy market 0.0001*
action gated by EMA(14) > WMA(9). The condition held on every 1m bar of an uptrend, so it
bought 43 times; it had no rule that could ever sell. Live, that is an ever-growing
position with real money. Paper stays allowed: one-sided experiments risk nothing.
"""
import pytest

from backend_app.backend import deployment_binding as db
from backend_app.backend.strategy_dag.plan import CompiledPlan


def _plan(*action_blocks):
    nodes = {"n_data": {"id": "n_data", "block_id": "data.market_data", "category": "data",
                        "params": {"symbol": "BTC/USDC", "timeframe": "1m", "market_type": "spot"}}}
    actions = []
    for i, block in enumerate(action_blocks):
        nid = f"n_act{i}"
        nodes[nid] = {"id": nid, "block_id": block, "category": "action", "params": {}}
        actions.append(nid)
    return CompiledPlan.from_dict({
        "dag_hash": "deadbeef", "execution_order": ["n_data", *actions],
        "data_nodes": ["n_data"], "action_nodes": actions, "node_index": nodes,
    })


VYOM = ("action_buy_market",)


def test_the_vyom_shape_is_refused_live():
    with pytest.raises(db.DeployRejected) as exc:
        db.assert_live_strategy_can_exit(_plan(*VYOM), "live")
    assert exc.value.code == "LIVE_STRATEGY_HAS_NO_EXIT"


def test_the_vyom_shape_is_allowed_on_paper():
    assert db.assert_live_strategy_can_exit(_plan(*VYOM), "paper")["checked"] is False


@pytest.mark.parametrize("blocks", [
    ("action_buy_market", "action_sell_market"),
    ("action_buy_limit", "action_sell_limit"),
    ("action_buy_market", "action_close_position"),
    ("action_buy_market", "action_stop_market"),
    ("action_buy_market", "action_take_profit_limit"),
    (),  # trades nothing, so there is nothing to exit
])
def test_a_strategy_with_an_exit_path_deploys_live(blocks):
    assert db.assert_live_strategy_can_exit(_plan(*blocks), "live")["checked"] is True


@pytest.mark.parametrize("blocks", [("action_sell_market",), ("action_buy_market", "action_buy_limit")])
def test_one_sided_strategies_are_refused_live(blocks):
    with pytest.raises(db.DeployRejected):
        db.assert_live_strategy_can_exit(_plan(*blocks), "live")


def test_the_preflight_reports_it_as_a_named_condition():
    assert "exit_path" in db.BINDING_CONDITIONS
    assert db.BINDING_CONDITION_DEPENDENCIES["exit_path"] == ("plan_readable", "deployment_mode")


class _FakeSvc:
    """Just enough of the Supabase client: strategy_versions -> one row with a plan."""
    def __init__(self, plan_dict):
        self.rows = [] if plan_dict is None else [{"id": "v1", "compiled_plan": plan_dict}]
        self.selected = None

    def table(self, name):
        assert name == "strategy_versions"
        return self

    def select(self, cols):
        self.selected = cols
        return self

    def eq(self, *_):
        return self

    def limit(self, *_):
        return self

    def execute(self):
        from types import SimpleNamespace
        return SimpleNamespace(data=self.rows)


def _plan_dict(*blocks):
    return _plan(*blocks).to_dict()


@pytest.mark.asyncio
async def test_the_marketplace_live_path_refuses_the_vyom_shape():
    svc = _FakeSvc(_plan_dict(*VYOM))
    with pytest.raises(db.DeployRejected) as exc:
        await db.assert_version_can_exit_live(svc, "v1")
    assert exc.value.code == "LIVE_STRATEGY_HAS_NO_EXIT"
    assert svc.selected == "id, compiled_plan"


@pytest.mark.asyncio
async def test_the_marketplace_live_path_allows_a_strategy_with_an_exit():
    await db.assert_version_can_exit_live(_FakeSvc(_plan_dict("action_buy_market", "action_sell_market")), "v1")


@pytest.mark.asyncio
async def test_a_missing_version_is_refused_not_waved_through():
    with pytest.raises(db.DeployRejected) as exc:
        await db.assert_version_can_exit_live(_FakeSvc(None), "v1")
    assert exc.value.code == "PLAN_UNREADABLE"
