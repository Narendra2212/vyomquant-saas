# Phase 10 / Workstream B Scope 3: detached runner for the sibling census margin probe.
# Usage: Start-Process powershell.exe -File staging\run_sibling_census_probe.ps1 <nonce> <which>
$ErrorActionPreference = 'Continue'
Set-Location 'c:\aerora_quant_backend_updated_final1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Remove-Item Env:\AERORA_TEST_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue

$nonce = $args[0]
$which = $args[1]
$out = "staging\sibling_census_$nonce.txt"
Remove-Item -ErrorAction SilentlyContinue "staging\sibling_census_done_$nonce.txt"
"START=$(Get-Date -Format o) WHICH=$which" | Set-Content -Encoding ascii $out

& '.venv\Scripts\python.exe' staging\probe_phase10_sibling_census_margins.py $which 2>&1 |
    Add-Content -Encoding ascii $out

$code = $LASTEXITCODE
"EXITCODE=$code" | Add-Content -Encoding ascii $out
"END=$(Get-Date -Format o)" | Add-Content -Encoding ascii $out
"DONE_$nonce exit=$code" | Set-Content -Encoding ascii "staging\sibling_census_done_$nonce.txt"
