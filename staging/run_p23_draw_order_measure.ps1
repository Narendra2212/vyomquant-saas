# Phase 10 / P-23 draw-order experiment: detached runner for N IN-TEST census measurements.
#
# Uses the existing in-test measurement mechanism
# (staging/probe_phase10_sibling_census_margins.py, which runs the REAL collected property and
# reads the Recorder it built). Generator-only sweeps are NOT used here - they measure the
# strategy, not the in-test distribution, and the two were measured to differ.
#
# Usage: Start-Process powershell.exe -File staging\run_p23_draw_order_measure.ps1 <nonce> <repeats>
$ErrorActionPreference = 'Continue'
Set-Location 'c:\aerora_quant_backend_updated_final1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Remove-Item Env:\AERORA_TEST_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue

$nonce = $args[0]
$repeats = $args[1]

$out = "staging\p23_measure_$nonce.txt"
Remove-Item -ErrorAction SilentlyContinue "staging\p23_measure_done_$nonce.txt"
"START=$(Get-Date -Format o) REPEATS=$repeats" | Set-Content -Encoding ascii $out

& '.venv\Scripts\python.exe' staging\probe_phase10_sibling_census_margins.py p23 $repeats 2>&1 |
    Select-String -Pattern 'in-test run|verdict=|side_buy|side_sell|limit2_was|minimum margin|floors at or below|SHORT|THIN' |
    ForEach-Object { $_.Line.TrimEnd() } | Add-Content -Encoding ascii $out

$code = $LASTEXITCODE
"EXITCODE=$code" | Add-Content -Encoding ascii $out
"END=$(Get-Date -Format o)" | Add-Content -Encoding ascii $out
"DONE_$nonce exit=$code" | Set-Content -Encoding ascii "staging\p23_measure_done_$nonce.txt"
