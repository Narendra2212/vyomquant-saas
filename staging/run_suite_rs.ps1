# Detached runner for an arbitrary pytest selection, WITHOUT a database.
#
# Usage: Start-Process powershell.exe -File staging\run_suite.ps1 <nonce> <file> [<file> ...]
#
# Launched via `Start-Process powershell.exe -File` on purpose. Start-Process'ing
# python.exe directly does not survive the agent tooling's foreground command timeout: the
# timeout delivers a console control event (0xC000013A) to the process group and the child
# dies, observed as exit -1073741510 and as silently truncated output files.
#
# AERORA_TEST_DATABASE_URL and DATABASE_URL are cleared, so this is the CI unit-test lane:
# tests/conftest.py's deliberate blank-DATABASE_URL default holds.
$ErrorActionPreference = 'Continue'
Set-Location 'c:\aerora_quant_backend_updated_final1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Remove-Item Env:\AERORA_TEST_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue

$nonce = $args[0]
$files = $args[1..($args.Count - 1)]

$out = "staging\suite_$nonce.txt"
Remove-Item -ErrorAction SilentlyContinue "staging\suite_done_$nonce.txt"
"START=$(Get-Date -Format o)" | Set-Content -Encoding ascii $out
"FILES=$($files -join ' ')" | Add-Content -Encoding ascii $out

& '.venv\Scripts\python.exe' -m pytest @files -q `
    -p no:cacheprovider -p no:warnings -o addopts="" `
    --timeout=900 --tb=short -rsfE 2>&1 | Add-Content -Encoding ascii $out

$code = $LASTEXITCODE
"EXITCODE=$code" | Add-Content -Encoding ascii $out
"END=$(Get-Date -Format o)" | Add-Content -Encoding ascii $out
"DONE_$nonce exit=$code" | Set-Content -Encoding ascii "staging\suite_done_$nonce.txt"

