"""Which of the new WS-D reconciliation tests perturbs the connection-pool test?

Read-only diagnostic. Runs each new test followed by
`test_pool_key_is_the_user_exchange_pair_not_the_credential` and reports the outcome, so
the pollution is attributed to a specific test rather than to the file.
"""
from __future__ import annotations

import subprocess
import sys

POOL = (
    "tests/test_exchange_safety_fix.py::"
    "test_pool_key_is_the_user_exchange_pair_not_the_credential"
)
MINE = "tests/test_p10d_exchange_reconciliation_fails_safely.py"

TESTS = [
    "test_the_service_is_not_registered_in_the_application_lifespan",
    "test_the_websocket_fallback_does_not_import_a_name_that_does_not_exist",
    "test_no_exchange_client_raises_instead_of_reporting_no_open_orders",
    "test_a_genuinely_empty_order_list_is_still_passed_through",
    "test_an_exchange_error_still_propagates",
    "test_a_venue_confirmed_fill_is_still_recorded",
]

for name in TESTS:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", f"{MINE}::{name}", POOL,
         "-p", "no:randomly", "-q", "--no-header", "-W", "ignore::DeprecationWarning"],
        capture_output=True, text=True,
    )
    tail = [ln for ln in proc.stdout.splitlines() if "passed" in ln or "failed" in ln]
    print(f"{name:<70} {tail[-1].strip() if tail else '(no summary)'}")
