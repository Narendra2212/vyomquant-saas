"""
tests/test_exch_sbx02_testnet_key_reaches_testnet.py

EXCH-SBX-02: a TESTNET key was sent to the REAL venue by every read endpoint.

WHAT WAS WRONG
--------------
``connection_engine.get_or_create_exchange`` takes ``testnet: bool = False``, so a caller
that does not say which environment it wants gets the LIVE endpoint. The live bot
(``master_executor``) and ``exchange_websocket_listener`` read the stored key's EXCH-06
``sandbox`` field and pass it. The read paths built from the same vault keys did not:
``/api/orders/open``, ``/api/orders/history``, authenticated market data, the portfolio
cache updater and the private user websocket. During the live testnet run the Live
Trading page's open-orders read sent the Binance TESTNET key to api.binance.com, which
answered "[BINANCE] Invalid API keys." (Sentry VYOMQUANT-API-PRODUCTION-M).

It failed safe (a read, refused by the venue), but the mirror case is not safe: a page
that shows the LIVE account's state for a testnet deployment shows the wrong money.

WHAT IS ASSERTED
----------------
1. ``key_sandbox`` reads the stored key's flag, and is ``False`` for anything else.
2. A sweep: every ``get_or_create_exchange`` call in ``backend_app`` that passes a
   credential also passes ``testnet=``. A call with no credential (the public market-data
   pool) is exempt: it has no key whose environment could disagree.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from typing import List

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend.connection_engine import key_sandbox  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend_app"


@pytest.mark.parametrize(
    "keys, expected",
    [
        ({"api_key": "k", "secret_key": "s", "sandbox": True}, True),
        ({"api_key": "k", "secret_key": "s", "testnet": True}, True),
        ({"api_key": "k", "secret_key": "s", "sandbox": False}, False),
        ({"api_key": "k", "secret_key": "s"}, False),
        (None, False),
        ("not a dict", False),
    ],
)
def test_key_sandbox_reads_the_stored_keys_flag(keys, expected):
    assert key_sandbox(keys) is expected


def _passes_a_credential(call: ast.Call) -> bool:
    """True when the call hands the pool an API key (positionally or by name)."""
    for kw in call.keywords:
        if kw.arg == "api_key":
            return not (isinstance(kw.value, ast.Constant) and kw.value.value is None)
    # Positional form: (user_id, exchange_id, api_key, ...)
    if len(call.args) >= 3:
        arg = call.args[2]
        return not (isinstance(arg, ast.Constant) and arg.value is None)
    return False


def _calls(path: Path) -> List[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name == "get_or_create_exchange":
            found.append(node)
    return found


def test_every_credentialed_pool_call_states_the_keys_environment():
    offenders: List[str] = []
    credentialed = 0
    for path in sorted(BACKEND.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if "get_or_create_exchange" not in path.read_text(encoding="utf-8", errors="replace"):
            continue
        for call in _calls(path):
            if not _passes_a_credential(call):
                continue
            credentialed += 1
            if not any(kw.arg == "testnet" for kw in call.keywords) and len(call.args) < 6:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{call.lineno}")
    assert credentialed >= 6, (
        "expected the six credentialed callers this guard was written against; the sweep "
        "found fewer, so it needs repointing rather than passing vacuously"
    )
    assert not offenders, (
        "get_or_create_exchange is called with a stored credential but no `testnet=` at "
        f"{offenders}. It defaults to the LIVE endpoint, so a testnet key goes to the real "
        "venue. Pass `testnet=key_sandbox(keys)`."
    )
