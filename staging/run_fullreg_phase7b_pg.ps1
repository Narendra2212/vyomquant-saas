# Phase 7 FINAL full regression — POSTGRESQL LANE, against the frozen code.
#
# Pair to run_fullreg_phase7b.ps1. See that file for why a second pair of runs exists: three
# code changes landed after FULLREG_P7 and FULLREG_P7PG completed, including the LIB-04 fix
# that the PostgreSQL lane itself found.
#
# AERORA_TEST_DATABASE_URL is read from staging/pg_dsn.txt on every run, because pgserver
# allocates a NEW random port on every start and a hard-coded port is stale by construction.
# The difference between this lane's skip count and the CI lane's IS the infrastructure-gated
# surface.
$ErrorActionPreference = 'Continue'
Set-Location 'c:\aerora_quant_backend_updated_final1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$dsn = (Get-Content 'staging\pg_dsn.txt' -Raw).Trim()
$env:AERORA_TEST_DATABASE_URL = $dsn
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\AERORA_TEST_REDIS_URL -ErrorAction SilentlyContinue

$nonce = 'FULLREG_P7BPG'
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
