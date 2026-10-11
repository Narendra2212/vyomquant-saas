# Phase 10 full regression — POSTGRESQL LANE (nonce FULLREG_P10PG).
#
# Byte-identical configuration to the Phase 8/9 PG lanes so figures stay comparable. Sets
# AERORA_TEST_DATABASE_URL to the live local pgserver and leaves DATABASE_URL unset, so the
# PostgreSQL-gated suites execute instead of skipping. LOCAL-substrate evidence only; no
# production DSN. `Add-Content` (not Tee-Object), matching the Phase 9 PG lane, so the
# finalizer's UTF-16-vs-ascii handling is identical across lanes.
$ErrorActionPreference = 'Continue'
Set-Location 'c:\aerora_quant_backend_updated_final1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$dsn = (Get-Content 'staging\pg_dsn.txt' -Raw).Trim()
$env:AERORA_TEST_DATABASE_URL = $dsn
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\AERORA_TEST_REDIS_URL -ErrorAction SilentlyContinue

$nonce = 'FULLREG_P10PG'
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
