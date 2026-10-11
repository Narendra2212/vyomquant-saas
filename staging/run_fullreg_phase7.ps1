# Phase 7 full regression — CI-COMPARABLE LANE.
#
# Configuration matches staging/run_fullreg_phase6b.ps1 exactly, so the counts are
# comparable to the Phase 6 baseline (13,534 collected / 13,082 passed / 9 failed /
# 434 skipped / 6 xfailed / 3 xpassed in 3282.70s):
#   * no DATABASE_URL and no AERORA_TEST_DATABASE_URL, so tests/conftest.py's deliberate
#     "blank DATABASE_URL" default holds and the SQLite-lane tests behave as in
#     01-pr-check.yml's "Run Unit Tests" job. The PostgreSQL-only lanes are separate CI
#     jobs and are measured separately by run_fullreg_phase7_pg.ps1 — their skips in THIS
#     run are infrastructure-gated and are reported as such, not counted as green.
#   * --timeout=1800, NOT 300. tests/test_p58_migration_set_is_idempotent legitimately
#     takes ~1029s, and --timeout-method=thread terminates the whole pytest process on
#     Windows, so a 300s budget aborted three earlier runs at 1%/1%/38% and looked like a
#     hang.
#
# Launched with its own console on purpose: the agent tooling's foreground command timeout
# delivers a console control event (0xC000013A) to the whole process group, which took the
# local PostgreSQL instance down mid-test once already in Phase 6.
$ErrorActionPreference = 'Continue'
Set-Location 'c:\aerora_quant_backend_updated_final1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Remove-Item Env:\AERORA_TEST_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\AERORA_TEST_REDIS_URL -ErrorAction SilentlyContinue

$nonce = 'FULLREG_P7'
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
