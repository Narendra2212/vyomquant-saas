# Phase 7 FINAL full regression — CI-COMPARABLE LANE, against the frozen code.
#
# WHY A SECOND CI-LANE RUN. FULLREG_P7 completed at 23:42 and FULLREG_P7PG at 01:01, and
# three code changes landed after each of them:
#   * tests/test_orm_models_match_provisioned_schema.py — the skip guard now checks that the
#     server ANSWERS, not merely that a DSN string exists (it was failing four tests against
#     a stale port instead of skipping, which its own docstring promises). Present in
#     FULLREG_P7PG, absent from FULLREG_P7.
#   * core/config.py — validate_config() and print_config_status() now classify the value
#     instead of testing truthiness, so the boot report stopped printing
#     "Supabase: CONNECTED (project: YOUR_PROJECT_REF)" two lines above a block listing
#     thirteen unconfigured credentials. Absent from BOTH earlier runs.
#   * routers/library.py + tests/test_lib01_clone_strategy_postgres.py — LIB-04: a client
#     failure that is not a PostgREST APIError now answers 503 instead of escaping to a
#     500, and the LIB-01 test no longer races on a module global across threads. Absent
#     from BOTH earlier runs.
#
# So neither earlier run measures the code as it now stands. This run and
# run_fullreg_phase7b_pg.ps1 are the authoritative pair; the earlier two are kept as the
# intermediate measurements that FOUND the LIB-04 defect, which is the only reason it is
# fixed.
#
# Configuration is otherwise identical to run_fullreg_phase7.ps1, so the figures stay
# comparable to the Phase 5 and Phase 6 baselines.
$ErrorActionPreference = 'Continue'
Set-Location 'c:\aerora_quant_backend_updated_final1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Remove-Item Env:\AERORA_TEST_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\AERORA_TEST_REDIS_URL -ErrorAction SilentlyContinue

$nonce = 'FULLREG_P7B'
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
