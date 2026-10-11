# Phase 9 FINAL full regression — CI-COMPARABLE LANE.
#
# Byte-identical configuration to run_fullreg_phase8.ps1 so the figures stay comparable to
# the Phase 7/8 baselines. The CI lane sets NO database URL, so every PostgreSQL-gated
# suite skips -- which is the point: this lane is what a clean CI runner sees.
#
# WHAT LANDED SINCE FULLREG_P8 (the Phase 8 authoritative CI run):
#   * backend_app/migrations/028_safety_control_audit.sql           (KILL-02/03)
#   * backend_app/migrations/029_referral_obligations.sql           (MONEY-06)
#   * backend_app/migrations/030_execution_venue_order_id.sql       (TRADE-12, new)
#   * backend_app/core/global_safety.py                            (KILL-02 reset authz)
#   * backend_app/core/safety_audit.py                             (durable audit, new)
#   * backend_app/backend/exchange_executor.py                     (KILL-03/04, TRADE-07)
#   * backend_app/routers/admin.py                                 (KILL-03 activation)
#   * backend_app/routers/billing.py                               (MONEY-06, MONEY-07)
#   * backend_app/core/execution_engine.py                         (TRADE-07, TRADE-11, 12)
#   * backend_app/core/models/execution_record.py                  (TRADE-07/11/12)
#   * backend_app/core/risk_engine.py                              (TRADE-11)
#   * backend_app/backend/fee_engine.py                            (FEE-01)
#   * backend_app/backend/order_watchdog.py                        (TRADE-07 recovery)
#   * backend_app/backend/marketplace/listing_projection.py        (TEST-06)
#   * new test modules: test_kill03_*, test_money06_*, test_trade07_*, test_fee01_*,
#     test_ml01_*, test_test06_*; corrected test_trade02_*, test_trade01_*,
#     test_order_normalization_*, test_billing_e2e, test_bandit_sast_gate_is_green,
#     property/test_protected_logic_containment
$ErrorActionPreference = 'Continue'
Set-Location 'c:\aerora_quant_backend_updated_final1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Remove-Item Env:\AERORA_TEST_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\AERORA_TEST_REDIS_URL -ErrorAction SilentlyContinue

$nonce = 'FULLREG_P9'
$out = "staging\fullreg_raw_$nonce.txt"
Remove-Item -ErrorAction SilentlyContinue "staging\fullreg_done_$nonce.txt"

"START=$(Get-Date -Format o)" | Set-Content -Encoding ascii $out
"AERORA_TEST_DATABASE_URL=[$($env:AERORA_TEST_DATABASE_URL)]" | Add-Content -Encoding ascii $out
"DATABASE_URL=[$($env:DATABASE_URL)]" | Add-Content -Encoding ascii $out

& '.venv\Scripts\python.exe' -m pytest tests -q `
    -p no:cacheprovider -o addopts="" `
    --timeout=1800 --timeout-method=thread `
    --junitxml="staging\fullreg_$nonce.xml" `
    --tb=line -rfE 2>&1 | Tee-Object -FilePath $out -Append

$code = $LASTEXITCODE
"EXITCODE=$code" | Add-Content -Encoding ascii $out
"END=$(Get-Date -Format o)" | Add-Content -Encoding ascii $out
"DONE_$nonce exit=$code" | Set-Content -Encoding ascii "staging\fullreg_done_$nonce.txt"
