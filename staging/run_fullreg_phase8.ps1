# Phase 8 FINAL full regression — CI-COMPARABLE LANE.
#
# Configuration is identical to run_fullreg_phase7b.ps1 so the figures stay comparable to
# the Phase 5/6/7 baselines, with ONE change: pytest output goes through `Tee-Object`
# instead of `Add-Content`. `Add-Content` at the end of a pipeline BUFFERS, so a run that
# is killed mid-flight loses every line and is indistinguishable from a run that hung.
# That happened three times during Phase 8. Tee-Object writes through as it goes.
#
# WHAT LANDED SINCE FULLREG_P7B (the Phase 7 authoritative CI run):
#   * backend_app/migrations/026_fee_records_row_level_security.sql  (TENANT-02)
#   * backend_app/migrations/027_referral_ledger_currency.sql        (MONEY-04 residual)
#   * backend_app/core/global_safety.py                             (KILL-01, TRADE-02)
#   * backend_app/core/models/execution_record.py                   (TRADE-01, TRADE-06)
#   * backend_app/core/execution_engine.py                          (TRADE-01 wiring)
#   * staging/anon_read_sweep.py                                    (owner-column seeding)
#   * four new test modules and eight corrected ones
$ErrorActionPreference = 'Continue'
Set-Location 'c:\aerora_quant_backend_updated_final1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Remove-Item Env:\AERORA_TEST_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\AERORA_TEST_REDIS_URL -ErrorAction SilentlyContinue

$nonce = 'FULLREG_P8'
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
