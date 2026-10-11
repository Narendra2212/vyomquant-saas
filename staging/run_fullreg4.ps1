# Detached full-regression runner.
# Configuration matches the repository's primary CI lane (01-pr-check.yml "Run Unit Tests"):
# no DATABASE_URL / no AERORA_TEST_DATABASE_URL, so tests/conftest.py's deliberate
# "blank DATABASE_URL" default holds and the ~11k SQLite-lane tests behave as they do in CI.
# The PostgreSQL-only lanes are separate CI jobs and were measured separately.
#
# --timeout is a safety net only: without it a single hung test blocks the entire run.
# 300s per test is far above any legitimate test duration in this suite.
$ErrorActionPreference = 'Continue'
Set-Location 'c:\aerora_quant_backend_updated_final1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Remove-Item -ErrorAction SilentlyContinue 'staging\fullreg_done_FULLREG4.txt'
$out = 'staging\fullreg_raw_FULLREG4.txt'
"START=$(Get-Date -Format o)" | Set-Content -Encoding ascii $out
& '.venv\Scripts\python.exe' -m pytest tests -q `
    -p no:cacheprovider -o addopts="" `
    --timeout=300 --timeout-method=thread `
    --tb=line -rfE 2>&1 | Add-Content -Encoding ascii $out
$code = $LASTEXITCODE
"EXITCODE=$code" | Add-Content -Encoding ascii $out
"END=$(Get-Date -Format o)" | Add-Content -Encoding ascii $out
"DONE_FULLREG4 exit=$code" | Set-Content -Encoding ascii 'staging\fullreg_done_FULLREG4.txt'
