"""TRACE-EXP-01: Signal Trace records the exposure the risk check judged.

Found in production on 2026-11-15 (paper SOL/USDT, free plan, $5,000 exposure cap): an
APPROVED buy's trace said ``exposure: 4993.66`` while a later REFUSED one said
``exposure: 4990.82`` "would exceed $5,000". The approved signal's audit runs after the
fill, so it read the position INCLUDING the trade just made; the refused one read it
before. Both now record ``checked_exposure``, the figure handed to the risk engine.
"""

from __future__ import annotations

import inspect

from backend_app.backend.master_executor import BotRunner


def test_the_risk_check_and_the_approved_trace_read_the_same_exposure():
    src = inspect.getsource(BotRunner)
    assert "checked_exposure = float(self.recovered_exposure_coins) * price" in src
    assert "current_exposure=checked_exposure," in src
    approved_audit = src.split("risk_passed=(v_verdict == RiskVerdict.APPROVED)", 1)[1][:900]
    assert "exposure=checked_exposure," in approved_audit
    assert "exposure=float(self.recovered_exposure_coins" not in approved_audit
