# Phase 7 full regression — POSTGRESQL LANE.
#
# WHY A SECOND LANE EXISTS. staging/run_fullreg_phase7.ps1 reproduces the repository's
# primary CI job (01-pr-check.yml "Run Unit Tests"), which runs with NO DATABASE_URL and no
# AERORA_TEST_DATABASE_URL. That is the right configuration for comparability with the
# Phase 5 and Phase 6 baselines, but it means every PostgreSQL-only suite SKIPS, and a
# single green figure from that lane would be reporting a pass on tests that never ran.
#
# This lane sets AERORA_TEST_DATABASE_URL from staging/pg_dsn.txt, so tests/conftest.py's
# deliberate blank-DATABASE_URL default is lifted and the PostgreSQL suites execute against
# the real server. The two lanes are reported side by side, and the difference between them
# IS the infrastructure-gated surface.
#
# The DSN is read from the file on every run on purpose: pgserver allocates a NEW random
# port on every start, so a hard-coded port is stale by construction.
#
# --timeout=1800, NOT 300: tests/test_p58_migration_set_is_idempotent legitimately takes
# ~1029s, and --timeout-method=thread terminates the whole pytest process on Windows.
#
# Launched with its own console: the agent tooling's foreground timeout delivers a console
# control event (0xC000013A) to the process group, which is exactly what killed this
# PostgreSQL instance earlier in this phase - its log carries
# `background worker "logical replication launcher" was terminated by exception 0xC000013A`
# and the next start had to run crash recovery.
$ErrorActionPreference = 'Continue'
Set-Location 'c:\aerora_quant_backend_updated_final1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$dsn = (Get-Content 'staging\pg_dsn.txt' -Raw).Trim()
$env:AERORA_TEST_DATABASE_URL = $dsn
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\AERORA_TEST_REDIS_URL -ErrorAction SilentlyContinue

$nonce = 'FULLREG_P7PG'
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
