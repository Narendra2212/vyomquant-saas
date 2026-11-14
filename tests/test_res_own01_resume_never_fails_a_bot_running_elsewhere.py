"""
tests/test_res_own01_resume_never_fails_a_bot_running_elsewhere.py

RES-OWN-01, found during the live testnet run.

Every worker process runs ``FleetManager.resume_deployments`` on start-up. A LIVE row cannot be
resumed automatically (RESUME_LIVE_DEPLOYMENTS is off), so the rebuild raises - and the row was
then marked ``failed`` ("could not resume after restart") even when the bot was running happily
in ANOTHER process. Measured in production: a live BTC/USDC deployment started at 09:30:14 was
marked failed at 09:31:10 - with its strategy settled to failed - while its ownership claim kept
heartbeating and it went on to fill a testnet order at 09:32:16. The UI said "failed" about a
bot that was trading.

Now a row whose bot holds a fresh claim somewhere - or whose ownership cannot be read - is left
alone. Only a row nothing owns is marked failed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend_app.backend import deployment_runtime as dr
from tests.test_rt08_deployment_state_is_durable import _fleet, _row, _Store

pytestmark = pytest.mark.asyncio


def _refuse(row):
    raise dr.RuntimeBlueprintError(
        "live deployments are not resumed automatically after a restart"
    )


@pytest.mark.parametrize("claim, failed, skipped", [
    (True, 0, 1),     # another process runs it: leave it alone
    (None, 0, 1),     # ownership unreadable: never declare it dead
    (False, 1, 0),    # nothing owns it: it really is not running
])
async def test_a_live_row_is_failed_only_when_nothing_owns_it(monkeypatch, claim, failed, skipped):
    monkeypatch.setattr("backend_app.core.bot_ownership.claim_is_live", lambda u, s: claim)
    store = _Store()
    store.live_rows = [_row("l1", mode="live", environment="live", exchange_symbol="BTC/USDC")]
    fleet = _fleet(store)
    fleet.start_bot = AsyncMock()

    outcome = await fleet.resume_deployments(_refuse)

    assert outcome["failed"] == failed
    assert outcome.get("owned_elsewhere", 0) == skipped
    assert len(store.failed) == failed
    fleet.start_bot.assert_not_awaited()


async def test_claim_is_live_is_false_in_a_local_single_process_environment(monkeypatch):
    from backend_app.core import bot_ownership

    monkeypatch.setattr(bot_ownership, "_claim_store_dsn", lambda: None)
    monkeypatch.setattr("backend_app.core.runtime_env.local_state_is_acceptable", lambda: True)
    assert bot_ownership.claim_is_live("u1", "BTC/USDC") is False
    monkeypatch.setattr("backend_app.core.runtime_env.local_state_is_acceptable", lambda: False)
    assert bot_ownership.claim_is_live("u1", "BTC/USDC") is None
