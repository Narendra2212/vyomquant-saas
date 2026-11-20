"""PROD-5 / PROD-6 (Sentry, 2026-11-11, 54 events each from one paper session).

PROD-5: ``_log_summary`` put a conditional inside an f-string format spec, which raised on every
passing bar - the handler then marked the metrics as failed. PROD-6: ``_check_alert_conditions``
called ``AlertSystem.send_alert`` on the CLASS with a ``channels`` argument the async instance
method does not take, so no data-quality alert was ever sent.
"""

from __future__ import annotations

import asyncio
import logging

from backend_app.backend import data_observability as dobs


def _metrics(mean, passed=True, nan=0.0):
    m = dobs.DataQualityMetrics.__new__(dobs.DataQualityMetrics)
    m.stage, m.timestamp, m.nan_ratio, m.feature_mean = "market_data", "t", nan, mean
    m.gap_count, m.passed, m.violations, m.data_delay_seconds = 0, passed, ["v"], None
    return m


def test_the_summary_formats_a_mean_and_a_missing_mean(caplog) -> None:
    with caplog.at_level(logging.INFO):
        dobs.DataObservability._log_summary(_metrics(0.12345), "BTC/USDT")
        dobs.DataObservability._log_summary(_metrics(None), "BTC/USDT")
    text = caplog.text
    assert "mean=0.1235" in text and "mean=N/A" in text


def test_an_alert_reaches_the_alert_system_without_a_type_error(monkeypatch, caplog) -> None:
    sent = []

    class _Alerts:
        async def send_alert(self, **kwargs):
            sent.append(kwargs)
            return True

    import backend_app.backend.alert_system as alert_system

    monkeypatch.setattr(alert_system, "get_alert_system", lambda: _Alerts())
    with caplog.at_level(logging.ERROR):
        dobs.DataObservability._send_alert(_metrics(1.0, passed=False, nan=0.5), "BTC/USDT", ["nan"])
    assert "Failed to send alert" not in caplog.text
    assert len(sent) == 1 and sent[0]["level"].name == "CRITICAL"
    assert "channels" not in sent[0]


def test_dispatch_schedules_on_a_running_loop() -> None:
    ran = []

    async def _coro():
        ran.append(1)

    async def _main():
        dobs._dispatch(_coro())
        await asyncio.sleep(0)

    asyncio.run(_main())
    assert ran == [1]
