"""Phase 12 read-only probe: what does `_handle_ccxt_error` ACTUALLY return?

W-6. Prior phases established the ladder by reading it. This probe EXECUTES the real
classifier against the exact message shapes in question and records the resulting class,
`error_code` and `retryable`, so the shadowing is measured rather than inferred.

No network. No exchange. No credential. The executor is built with `object.__new__` so
`__init__` never runs — the pattern `tests/test_trade04_live_order_gate_at_venue_boundary.py`
already uses — and the three credential attributes `_redact_credentials` reads are set to
empty strings so redaction is a no-op.

EDITS NOTHING.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from backend_app.backend.exchange_executor import \
    CCXTExchangeExecutor  # noqa: E402

ex = object.__new__(CCXTExchangeExecutor)
ex._api_key = ""
ex._api_secret = ""
ex._password = ""

CASES = [
    ("genuine invalid symbol",
     "binance InvalidOrder: Invalid symbol BTCUSDT"),
    ("TIMEOUT whose message carries the request URL",
     "binance RequestTimeout: GET https://api.binance.com/api/v3/order?symbol=BTCUSDT timed out"),
    ("TIMEOUT, no symbol text",
     "binance RequestTimeout: request timed out"),
    ("network failure carrying a market path",
     "okx NetworkError: connection reset while fetching /api/v5/market/ticker"),
    ("genuine authentication failure",
     "binance AuthenticationError: API-key format invalid"),
    ("unrelated message containing the substring 'key'",
     "monkey patch failed"),
    ("insufficient funds",
     "binance InsufficientFunds: Account has insufficient balance"),
    ("rate limit",
     "binance RateLimitExceeded: too many requests"),
]

EXPECTED = {
    "genuine invalid symbol": "InvalidSymbolError",
    "TIMEOUT whose message carries the request URL": "NetworkError",
    "TIMEOUT, no symbol text": "NetworkError",
    "network failure carrying a market path": "NetworkError",
    "genuine authentication failure": "AuthenticationError",
    "unrelated message containing the substring 'key'": "ExchangeError",
    "insufficient funds": "InsufficientFundsError",
    "rate limit": "RateLimitError",
}

print(f"{'case':<48} {'actual class':<24} {'error_code':<18} retryable  verdict")
print("-" * 118)

wrong = 0
for label, message in CASES:
    err = ex._handle_ccxt_error(Exception(message))
    actual = type(err).__name__
    want = EXPECTED[label]
    ok = actual == want
    if not ok:
        wrong += 1
    verdict = "ok" if ok else f"MISCLASSIFIED (expected {want})"
    print(
        f"{label:<48} {actual:<24} {str(err.error_code):<18} "
        f"{str(err.retryable):<9} {verdict}"
    )

print("-" * 118)
print(f"cases: {len(CASES)}   misclassified: {wrong}")
