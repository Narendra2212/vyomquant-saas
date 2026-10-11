# Phase 9 focused-suite runner. Detached + done-marker, for the same reason every other
# long runner in staging/ is: the agent tooling's foreground timeout delivers a console
# control event to the process group, and a long pytest run has twice now taken the
# pgserver instance down with it mid-run. A background process with a completion marker
# keeps the DB alive and lets the agent poll at a low frequency.
#
# Usage: run_suite_p9.ps1 <LABEL> <pytest -k EXPRESSION>
param(
    [Parameter(Mandatory = $true)][string]$Label,
    [Parameter(Mandatory = $true)][string]$Expr
)
$ErrorActionPreference = 'Continue'
Set-Location 'c:\aerora_quant_backend_updated_final1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$dsn = (Get-Content 'staging\pg_dsn.txt' -Raw).Trim()
$env:AERORA_TEST_DATABASE_URL = $dsn
$env:DATABASE_URL = $dsn

$raw  = "staging\suite_raw_$Label.txt"
$done = "staging\suite_done_$Label.txt"
Remove-Item -ErrorAction SilentlyContinue $done
"START=$(Get-Date -Format o)" | Set-Content -Encoding ascii $raw

& '.venv\Scripts\python.exe' -m pytest tests/ -p no:cacheprovider --tb=line `
    -W ignore::DeprecationWarning -W ignore::UserWarning -q --no-header `
    -k $Expr 2>&1 | Add-Content -Encoding ascii $raw

"EXITCODE=$LASTEXITCODE" | Add-Content -Encoding ascii $raw
"DONE" | Set-Content -Encoding ascii $done
