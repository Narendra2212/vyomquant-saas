# Phase 10 full regression — CI-COMPARABLE LANE (nonce FULLREG_P10).
#
# Byte-identical configuration to the Phase 8/9 CI lanes so the figures stay comparable.
# The CI lane sets NO database URL, so every PostgreSQL-gated suite skips -- which is the
# point: this is what a clean CI runner sees. Run on the fully-remediated Phase 10 tree
# (PG sweep coverage fix, TEST-05 deterministic @example boundaries, CONN-01 store_keys
# eviction, QUOTA-01 metered-reservation tests, TENANT-01 credential-vault isolation tests,
# referral payout payout_method fix, fee_pct nullable + migration 031, reconciliation
# startup assessment).
$ErrorActionPreference = 'Continue'
Set-Location 'c:\aerora_quant_backend_updated_final1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Remove-Item Env:\AERORA_TEST_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\AERORA_TEST_REDIS_URL -ErrorAction SilentlyContinue

$nonce = 'FULLREG_P10'
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
