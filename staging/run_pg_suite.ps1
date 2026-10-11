# Detached runner for a PostgreSQL-lane selection, with a long traceback.
#
# Same environment as staging/run_fullreg_phase7_pg.ps1 (AERORA_TEST_DATABASE_URL set from
# staging/pg_dsn.txt), so a failure reproduced here is the same failure the PostgreSQL lane
# of the full regression saw. --tb=long because the point of this runner is to read the
# cause, not to count.
#
# Usage: Start-Process powershell.exe -File staging\run_pg_suite.ps1 <nonce> <args...>
$ErrorActionPreference = 'Continue'
Set-Location 'c:\aerora_quant_backend_updated_final1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:AERORA_TEST_DATABASE_URL = (Get-Content 'staging\pg_dsn.txt' -Raw).Trim()
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue

$nonce = $args[0]
$rest = $args[1..($args.Count - 1)]

$out = "staging\pgsuite_$nonce.txt"
Remove-Item -ErrorAction SilentlyContinue "staging\pgsuite_done_$nonce.txt"
"START=$(Get-Date -Format o)" | Set-Content -Encoding ascii $out
"DSN=$($env:AERORA_TEST_DATABASE_URL)" | Add-Content -Encoding ascii $out
"ARGS=$($rest -join ' ')" | Add-Content -Encoding ascii $out

& '.venv\Scripts\python.exe' -m pytest @rest -q `
    -p no:cacheprovider -p no:warnings -o addopts="" `
    --timeout=900 --tb=long -rfE 2>&1 | Add-Content -Encoding ascii $out

$code = $LASTEXITCODE
"EXITCODE=$code" | Add-Content -Encoding ascii $out
"DONE_$nonce exit=$code" | Set-Content -Encoding ascii "staging\pgsuite_done_$nonce.txt"
