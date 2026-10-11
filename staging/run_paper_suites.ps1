# Detached runner for the PostgreSQL-gated paper suites.
#
# Launched as `Start-Process powershell.exe -File <this>` so it gets its OWN process and its
# OWN console. Directly Start-Process'ing python.exe did not survive: the agent tooling's
# foreground command timeout delivers a console control event (0xC000013A) to the process
# group, which killed the child mid-run three times - once with exit code -1073741510 and
# twice as a silently truncated output file. A wrapper script is what survived in the
# previous phase's full regression, for the same reason.
$ErrorActionPreference = 'Continue'
Set-Location 'c:\aerora_quant_backend_updated_final1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:AERORA_TEST_DATABASE_URL = (Get-Content 'staging\pg_dsn.txt' -Raw).Trim()

$nonce = $args[0]
$files = $args[1..($args.Count - 1)]

$out = "staging\paper_run_$nonce.txt"
Remove-Item -ErrorAction SilentlyContinue "staging\paper_done_$nonce.txt"
"START=$(Get-Date -Format o)" | Set-Content -Encoding ascii $out
"DSN=$($env:AERORA_TEST_DATABASE_URL)" | Add-Content -Encoding ascii $out
"FILES=$($files -join ' ')" | Add-Content -Encoding ascii $out

& '.venv\Scripts\python.exe' -m pytest @files -q `
    -p no:cacheprovider -p no:warnings -o addopts="" `
    --timeout=900 --tb=line -rfE 2>&1 | Add-Content -Encoding ascii $out

$code = $LASTEXITCODE
"EXITCODE=$code" | Add-Content -Encoding ascii $out
"END=$(Get-Date -Format o)" | Add-Content -Encoding ascii $out
"DONE_$nonce exit=$code" | Set-Content -Encoding ascii "staging\paper_done_$nonce.txt"
