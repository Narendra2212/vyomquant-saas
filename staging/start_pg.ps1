# Start the local PostgreSQL instance in its own console and record the DSN.
#
# pgserver allocates a NEW random port on every start, so staging/pg_dsn.txt is rewritten
# each time and anything holding an older port is stale by construction. Detached for the
# same reason as every other wrapper here: the agent tooling's foreground timeout delivers a
# console control event to the process group, which took this instance down mid-test in
# Phase 6.
$ErrorActionPreference = 'Continue'
Set-Location 'c:\aerora_quant_backend_updated_final1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PG_DATA_DIR = 'pgdata_r1'

$out = "staging\pg_start.txt"
Remove-Item -ErrorAction SilentlyContinue "staging\pg_start_done.txt"
"START=$(Get-Date -Format o)" | Set-Content -Encoding ascii $out

# `--data-dir` as well as the environment variable, so the selection is visible in the
# command itself and cannot be lost if the variable is cleared between here and the call.
# `--expect-provisioned` because this wrapper exists to serve the REGRESSION LANE: if the
# cluster comes up without the provisioned schema, the right outcome is a loud failure
# here, not a run in which every PG-gated suite skips. Phase 10 / Workstream H.
& '.venv\Scripts\python.exe' staging\bootstrap_pg.py start --data-dir pgdata_r1 --expect-provisioned 2>&1 | Add-Content -Encoding ascii $out

"EXITCODE=$LASTEXITCODE" | Add-Content -Encoding ascii $out
"DONE" | Set-Content -Encoding ascii "staging\pg_start_done.txt"
