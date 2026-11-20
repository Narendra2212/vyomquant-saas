"""OBS-02: the production config loader must not claim live trading is on when it is off.

It logged CRITICAL "PRODUCTION MODE: Live trading enabled!" on every load with ENV=production,
whatever the real gate said; Sentry raised it as FATAL on every deployments page view while
ExecutionFlags.LIVE_TRADING_ENABLED was false.
"""

from __future__ import annotations

import logging

from backend_app.core import deployment_config as dc
from backend_app.core.safety_config import ExecutionFlags


def _load(monkeypatch, caplog, live: bool):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("SANDBOX_MODE", "false")
    monkeypatch.setattr(ExecutionFlags, "LIVE_TRADING_ENABLED", live)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=dc.logger.name):
        dc.DeploymentConfig.from_environment()
    return [r for r in caplog.records if "Live trading enabled" in r.getMessage()]


def test_no_live_trading_claim_when_the_gate_is_off(monkeypatch, caplog) -> None:
    assert _load(monkeypatch, caplog, live=False) == []


def test_the_critical_warning_remains_when_the_gate_is_on(monkeypatch, caplog) -> None:
    records = _load(monkeypatch, caplog, live=True)
    assert records and records[0].levelno == logging.CRITICAL
