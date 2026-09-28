"""
tests/full_system_test.py — FULL SYSTEM SMOKE TEST

Exercises a *running* deployment over HTTP: health, the symbol universe, and the backtest
endpoint's determinism and sanity. It is a real integration read, so nothing here is mocked
and no assertion is relaxed — but it needs a server on ``API_BASE_URL`` to read, and a
default ``pytest tests/`` run does not start one. Without the guard below every test in this
file reported ``ConnectionRefusedError`` as a product failure, which is the one thing a smoke
test must not do: it hid whether the system was broken or simply not up.

So the dependency is declared instead of assumed. ``require_live_server`` probes ``/health``
once per session and skips the file with the reason (and the command that fixes it) when
nothing answers. When a server *is* up — locally or in CI against a deployed URL — all four
tests run exactly as written. ``tests/test_lifecycle_integration.py`` is the sibling
live-server suite; it reaches the same outcome with a blanket ``@pytest.mark.skip``, which
never runs even when the server is there.

Point it at another environment with ``API_BASE_URL``, the variable that suite already uses.

THE CREDENTIAL
--------------
This file used to send ``Authorization: Bearer dev_bypass``. There is no bypass: the string
appears nowhere in ``backend_app/``, and ``get_current_user`` hands every token to
``decode_token_local``, which verifies a signature. So all three authenticated reads here
401'd against any server that was actually running — the connection error simply arrived
first and hid it.

The credential is now the one the codebase sanctions for harnesses: an HS256 token claiming
``iss="algo22-test"``, which ``auth_middleware.decode_token_local`` accepts only when ``ENV``
is a non-production value and only when it really is signed with the server's
``SUPABASE_JWT_SECRET`` (there is no signature bypass in that path). A dozen suites mint the
same shape; ``tests/test_end_to_end_api_suite.py`` is the nearest reference. The server has
to share the secret and run with a non-production ``ENV``, which is what "full system test
against a dev deployment" means.

``/api/market/symbols`` is sent the credential too. It reads authenticated now —
``routers/market.py:129`` marks it "BE-CRITICAL-003 FIX: Require authentication" — and this
file was still calling it bare, so its 401 was the test's omission and not a regression.
"""

import os
import time

import jwt
import pytest
import requests

BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")

# Same resolution order as tests/test_end_to_end_api_suite.py, so one exported secret serves
# both the server and every harness that talks to it.
_JWT_SECRET = (
    os.environ.get("SUPABASE_JWT_SECRET")
    or os.environ.get("JWT_SECRET")
    or "dev-secret-change-in-production"
)


def _harness_token():
    return jwt.encode(
        {
            "sub": os.getenv(
                "SMOKE_TEST_USER_ID", "00000000-0000-0000-0000-000000000001"
            ),
            "email": "smoke@vyomquant.test",
            "role": "authenticated",
            "aud": "authenticated",
            "iss": "algo22-test",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        },
        _JWT_SECRET,
        algorithm="HS256",
    )


HEADERS = {"Authorization": f"Bearer {_harness_token()}"}

# Probed once, then remembered: True, or the reason this file cannot run.
_LIVE_SERVER_STATUS = None


def _live_server_status():
    global _LIVE_SERVER_STATUS
    if _LIVE_SERVER_STATUS is None:
        try:
            requests.get(f"{BASE_URL}/health", timeout=5)
            _LIVE_SERVER_STATUS = True
        except requests.exceptions.RequestException as exc:
            _LIVE_SERVER_STATUS = (
                f"No server is answering at {BASE_URL} ({type(exc).__name__}), so this smoke "
                "test has nothing to read. Start one with "
                "`python -m uvicorn backend_app.main:app --port 8000`, or set API_BASE_URL to "
                "a deployment."
            )
    return _LIVE_SERVER_STATUS


@pytest.fixture(autouse=True)
def require_live_server():
    status = _live_server_status()
    if status is not True:
        pytest.skip(status)


def log(msg):
    print(f"[TEST] {msg}")

def test_health():
    log("Checking health...")
    res = requests.get(f"{BASE_URL}/health")
    assert res.status_code == 200
    log("Health OK")

def test_symbols():
    log("Checking market symbols...")
    res = requests.get(f"{BASE_URL}/api/market/symbols", headers=HEADERS)
    assert res.status_code == 200
    data = res.json()
    assert len(data) > 0
    log(f"Symbols OK: {data[:3]}")

def run_backtest():
    log("Running backtest...")

    payload = {
        "strategies": ["rsi"],
        "symbol": "BTCUSDT",
        "timeframe": "1h"
    }

    res = requests.post(
        f"{BASE_URL}/api/strategies/backtest",
        headers=HEADERS,
        json=payload
    )

    assert res.status_code == 200

    data = res.json()

    log(f"Backtest Result: {data}")

    # Validate structure
    assert "total_return_pct" in data or "total_return" in data
    assert "win_rate_pct" in data or "win_rate" in data
    assert "total_trades" in data or "trades_count" in data

    return data

def test_determinism():
    log("Checking determinism...")

    r1 = run_backtest()
    time.sleep(1)
    r2 = run_backtest()

    # Verify response schemas are identical
    assert r1.keys() == r2.keys(), "Response schemas do not match"
    
    # Check that total return is very close (within 1.0% tolerance) since the latest candle updates in real-time
    r1_ret = r1.get("total_return_pct", 0.0)
    r2_ret = r2.get("total_return_pct", 0.0)
    diff = abs(r1_ret - r2_ret)
    assert diff < 1.0, f"Results diverged too much! {r1_ret} vs {r2_ret}"

    log("Determinism OK")

def test_sanity():
    log("Checking sanity...")

    data = run_backtest()

    trades_key = "total_trades" if "total_trades" in data else "trades_count"
    win_rate_key = "win_rate_pct" if "win_rate_pct" in data else "win_rate"

    assert data[trades_key] >= 0
    assert 0 <= data[win_rate_key] <= 100

    # Warn (not fail)
    if data[win_rate_key] == 100:
        print("[WARNING] Win rate is 100% -> unrealistic")

    log("Sanity OK")

if __name__ == "__main__":
    print("\n=== FULL SYSTEM TEST STARTED ===\n")

    test_health()
    test_symbols()
    test_sanity()
    test_determinism()

    print("\n=== ALL TESTS PASSED ===\n")