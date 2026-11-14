"""STOP-REDEPLOY-01: Deploy right after Stop failed with "Bot already running".

Found in the launch new-user run: a paper deployment was stopped and the same version
redeployed 8 s later. The heartbeat (RT-08) had not yet honoured the stop, so the old bot
still held the fleet slot and the NEW deployment was marked failed. `start_bot` now
finishes a stop that the slot's own row already records, then proceeds.
"""

from __future__ import annotations

import asyncio

from backend_app.backend.fleet_manager import FleetManager


def _fleet(row_says_stopped: bool):
    fleet = FleetManager.__new__(FleetManager)
    fleet._lock = asyncio.Lock()
    fleet._active_fleet = {"u1_SOL_USDT": {"bot": object(), "symbol": "SOL/USDT"}}
    fleet.MAX_SYSTEM_BOTS = 0  # stop at the capacity check: proves the slot check passed
    honoured = []

    async def _stop_requested(_key):
        return row_says_stopped

    async def _honour(key):
        honoured.append(key)
        fleet._active_fleet.pop(key, None)

    fleet._stop_requested = _stop_requested
    fleet._honour_stop_request = _honour
    return fleet, honoured


def test_a_slot_held_by_a_stopped_deployment_is_freed_and_the_deploy_proceeds():
    fleet, honoured = _fleet(row_says_stopped=True)
    ok, msg = asyncio.run(fleet.start_bot("u1", "SOL/USDT", {}, paper_trading=True))
    assert honoured == ["u1_SOL_USDT"]
    assert "already running" not in msg
    assert msg == "System is at maximum bot capacity."


def test_a_slot_held_by_a_running_deployment_is_still_refused():
    fleet, honoured = _fleet(row_says_stopped=False)
    ok, msg = asyncio.run(fleet.start_bot("u1", "SOL/USDT", {}, paper_trading=True))
    assert ok is False and msg == "Bot already running for SOL/USDT."
    assert honoured == []
