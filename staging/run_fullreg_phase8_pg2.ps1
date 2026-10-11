# Phase 8 FINAL full regression — POSTGRESQL LANE, SECOND RUN.
#
# WHY A SECOND PostgreSQL RUN. FULLREG_P8PG reported four failures. Two were the retained
# false positives also seen in the CI lane. The other two were NOT in the Phase 7 set and
# had to be reproduced and disposed of:
#
#   * tests/test_work03_bot_ownership_claims_postgres.py::
#     test_the_heartbeat_loop_refreshes_a_real_claim — a TEST DEFECT. Its freshness
#     assertion reused the loop's own 3s `wait_for` timeout as the bound, leaving zero
#     margin for the cancellation and the measuring query. The heartbeat had worked (the
#     row was pre-aged to ~120s and came back at 4.05s); only the measurement was late.
#     FIXED after FULLREG_P8PG completed, so that run's figures no longer describe HEAD.
#
#   * tests/test_subscriber_restricted_operations.py::...::
#     test_the_permitted_paths_disclose_no_protected_logic_either — a FALSE POSITIVE,
#     RETAINED. Mechanism: the seed's protected logic contains
#     `"ema_zzq_secret_window": 233`, so '233' is a genuine three-character token, and the
#     containment rule is a SUBSTRING test. It matched digits inside the server-generated
#     timestamp at `$.items[0].subscription.period_expiry`. Whether it matches depends on
#     the wall clock, which is why it passes in isolation. Recorded as TEST-06.
#
# So this run measures the code AS IT NOW STANDS. FULLREG_P8PG is kept, not discarded: it
# is the run that FOUND both, which is the only reason either is disposed of.
$ErrorActionPreference = 'Continue'
Set-Location 'c:\aerora_quant_backend_updated_final1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$dsn = (Get-Content 'staging\pg_dsn.txt' -Raw).Trim()
$env:AERORA_TEST_DATABASE_URL = $dsn
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\AERORA_TEST_REDIS_URL -ErrorAction SilentlyContinue

$nonce = 'FULLREG_P8PG2'
$out = "staging\fullreg_raw_$nonce.txt"
Remove-Item -ErrorAction SilentlyContinue "staging\fullreg_done_$nonce.txt"

"START=$(Get-Date -Format o)" | Set-Content -Encoding ascii $out
"AERORA_TEST_DATABASE_URL=[$($env:AERORA_TEST_DATABASE_URL)]" | Add-Content -Encoding ascii $out
"DATABASE_URL=[$($env:DATABASE_URL)]" | Add-Content -Encoding ascii $out

& '.venv\Scripts\python.exe' -m pytest tests -q `
    -p no:cacheprovider -o addopts="" `
    --timeout=1800 --timeout-method=thread `
    --junitxml="staging\fullreg_$nonce.xml" `
    --tb=line -rfE 2>&1 | Add-Content -Encoding ascii $out

$code = $LASTEXITCODE
"EXITCODE=$code" | Add-Content -Encoding ascii $out
"END=$(Get-Date -Format o)" | Add-Content -Encoding ascii $out
"DONE_$nonce exit=$code" | Set-Content -Encoding ascii "staging\fullreg_done_$nonce.txt"
