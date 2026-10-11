# Phase 8 FINAL full regression — POSTGRESQL LANE.
#
# Pair to run_fullreg_phase8.ps1. AERORA_TEST_DATABASE_URL is read from staging/pg_dsn.txt
# on every run, because pgserver allocates a NEW random port on every start and a
# hard-coded port is stale by construction. The difference between this lane's skip count
# and the CI lane's IS the infrastructure-gated surface, which is why both lanes are kept
# as distinct result sets rather than merged.
$ErrorActionPreference = 'Continue'
Set-Location 'c:\aerora_quant_backend_updated_final1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$dsn = (Get-Content 'staging\pg_dsn.txt' -Raw).Trim()
$env:AERORA_TEST_DATABASE_URL = $dsn
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\AERORA_TEST_REDIS_URL -ErrorAction SilentlyContinue

$nonce = 'FULLREG_P8PG'
$out = "staging\fullreg_raw_$nonce.txt"
Remove-Item -ErrorAction SilentlyContinue "staging\fullreg_done_$nonce.txt"

"START=$(Get-Date -Format o)" | Set-Content -Encoding ascii $out
"AERORA_TEST_DATABASE_URL=[$($env:AERORA_TEST_DATABASE_URL)]" | Add-Content -Encoding ascii $out
"DATABASE_URL=[$($env:DATABASE_URL)]" | Add-Content -Encoding ascii $out

# `Add-Content -Encoding ascii`, exactly as the Phase 7 wrappers did, NOT `Tee-Object`.
# Tee-Object on PowerShell 5.1 has no -Encoding parameter and defaults to UTF-16LE, which
# the counts parser (which reads UTF-8) cannot parse - the CI-lane run of this phase hit
# that and had to be normalised afterwards. Keeping the writer identical to Phase 7 also
# keeps the reader/writer contract identical, which is what makes the figures comparable.
# The file is exclusively locked while this runs; poll the done-marker, not the file.
& '.venv\Scripts\python.exe' -m pytest tests -q `
    -p no:cacheprovider -o addopts="" `
    --timeout=1800 --timeout-method=thread `
    --junitxml="staging\fullreg_$nonce.xml" `
    --tb=line -rfE 2>&1 | Add-Content -Encoding ascii $out

$code = $LASTEXITCODE
"EXITCODE=$code" | Add-Content -Encoding ascii $out
"END=$(Get-Date -Format o)" | Add-Content -Encoding ascii $out
"DONE_$nonce exit=$code" | Set-Content -Encoding ascii "staging\fullreg_done_$nonce.txt"
