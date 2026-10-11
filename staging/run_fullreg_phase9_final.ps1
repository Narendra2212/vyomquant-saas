# Phase 9 FINAL full regression — CI-COMPARABLE LANE (nonce FULLREG_P9FINAL).
#
# Byte-identical configuration to run_fullreg_phase9.ps1 / phase8, so the figures stay
# comparable to the Phase 7/8 baselines. The CI lane sets NO database URL, so every
# PostgreSQL-gated suite skips -- which is the point: this is what a clean CI runner sees.
#
# This FINAL run is on the fully-remediated tree: it includes the 10 collateral fixes made
# after the first FULLREG_P9 run (billing.py F821, execution_engine SessionLocal module ref,
# test_trade04 _Repo.mark_submitted, algo22.db venue_order_id, mark_submitted absent-row
# symmetry) AND the 2 sanctioned baseline re-records (auth_admin_role, live_order_path).
$ErrorActionPreference = 'Continue'
Set-Location 'c:\aerora_quant_backend_updated_final1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Remove-Item Env:\AERORA_TEST_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\AERORA_TEST_REDIS_URL -ErrorAction SilentlyContinue

$nonce = 'FULLREG_P9FINAL'
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
