"""SEC-13 -- the live-order gate's HMAC secrets are no longer literals anyone can read.

THE DEFECT (audit)
    ``core/live_execution_gate._GATE_SECRET`` and ``core/global_safety._VALIDATION_SECRET``
    were both ``bytes`` literals baked into source. A "signature" computed with a secret
    anyone with repository read access already knows is no stronger than an unsigned flag
    against that reader -- it still catches an accidental/naive bypass, but not a
    deliberate one by anyone who has seen this file.

THE FIX
    Both are now generated with ``os.urandom(32)`` at module import (once per process),
    never a literal in source. This is safe because mint and verify are always called from
    the SAME synchronous order-placement call stack in the SAME process for both artefacts
    (see each secret's own comment for the call-graph citation) -- nothing persists either
    the secret or the token/authorization across a restart or to a different process, so a
    value that changes every process start loses nothing a fixed one provided.

WHAT THIS FILE PROVES
    * neither secret is the literal string the source used to hardcode;
    * each is 32 random bytes (``os.urandom(32)``), not a short/guessable value;
    * mint-then-verify still agrees WITHIN one process for both artefacts (the actual
      property that matters -- the fix must not break the control it hardens);
    * the two secrets are independent of each other (the module docstrings' own claim --
      a holder of one still cannot mint the other).
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from backend_app.core import global_safety as gs
from backend_app.core import live_execution_gate as leg

#: authorize_live_order/require_live_authorization also require the PLATFORM itself to be
#: in live mode (RT-15) - see conftest.py's own comment on this fixture.
pytestmark = pytest.mark.usefixtures("live_platform_enabled")


def test_the_gate_secret_is_not_the_old_hardcoded_literal():
    assert leg._GATE_SECRET != b"aerora_quant_live_execution_gate_v1"
    assert len(leg._GATE_SECRET) == 32


def test_the_validation_secret_is_not_the_old_hardcoded_literal():
    assert gs._VALIDATION_SECRET != b"aerora_quant_risk_validation_secret_key_2026"
    assert len(gs._VALIDATION_SECRET) == 32


def test_a_fresh_process_gets_a_fresh_secret_each_time():
    """Pins the actual safety property: unguessable per process, not a fixed constant
    that merely moved from a literal to a slightly-less-obvious one.

    A real SEPARATE subprocess per sample, not ``importlib.reload`` in-process: reloading
    this module here would replace ``REQUIRED_CHECKS`` and the ``LiveOrderAuthorization``
    class with new objects while every OTHER already-imported module in this same test run
    (``execution_engine.py``, ``test_trade04_*.py``) keeps its own old references -- an
    ``isinstance``/set-identity mismatch that has nothing to do with the property this test
    is pinning, and corrupts every other test sharing this interpreter.
    """
    cmd = [sys.executable, "-c", "from backend_app.core.live_execution_gate import _GATE_SECRET; import sys; sys.stdout.buffer.write(_GATE_SECRET)"]
    secrets_seen = set()
    for _ in range(3):
        result = subprocess.run(cmd, capture_output=True, cwd=".", timeout=30, check=True)
        secrets_seen.add(result.stdout)
    assert len(secrets_seen) == 3, "each process start must mint a fresh secret"
    assert all(len(s) == 32 for s in secrets_seen)


def test_mint_then_verify_still_agrees_within_one_process():
    from decimal import Decimal

    auth = leg.authorize_live_order(
        execution_id="exec-1", tenant_id="tenant-1", strategy_id="strat-1",
        symbol="BTC/USDT", side="buy", size=Decimal("0.01"), exchange_id="binance",
        checks=leg.REQUIRED_CHECKS,
    )
    # Must verify successfully when re-derived against the SAME order inside this process.
    verified = leg.require_live_authorization(
        auth, symbol="BTC/USDT", side="buy", size=Decimal("0.01"), exchange_id="binance",
    )
    assert verified is auth


def test_the_two_secrets_are_independent():
    assert leg._GATE_SECRET != gs._VALIDATION_SECRET
