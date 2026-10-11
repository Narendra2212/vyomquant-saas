# Phase 10 / TEST-05: repeated INDEPENDENT runs of the targeted test, to measure whether
# the census is now deterministic.
#
# Separate processes on purpose: the pre-fix flake was ~8% per run, so the question is
# "does any independent run still fail", which repeated in-process calls (sharing
# Hypothesis's global random state and its DataTree caches) would not answer honestly.
#
# Usage: Start-Process powershell.exe -File staging\run_test05_determinism.ps1 <nonce> <runs>
$ErrorActionPreference = 'Continue'
Set-Location 'c:\aerora_quant_backend_updated_final1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Remove-Item Env:\AERORA_TEST_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue

$nonce = $args[0]
$runs = [int]$args[1]
$target = 'tests/property/test_order_idempotence_under_retry.py'

$out = "staging\test05_determinism_$nonce.txt"
Remove-Item -ErrorAction SilentlyContinue "staging\test05_determinism_done_$nonce.txt"
"START=$(Get-Date -Format o)" | Set-Content -Encoding ascii $out
"TARGET=$target RUNS=$runs" | Add-Content -Encoding ascii $out

$pass = 0
$fail = 0
for ($i = 1; $i -le $runs; $i++) {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $result = & '.venv\Scripts\python.exe' -m pytest $target -q `
        -p no:cacheprovider -p no:warnings -o addopts="" --tb=line -rfE 2>&1
    $code = $LASTEXITCODE
    $sw.Stop()
    $secs = [math]::Round($sw.Elapsed.TotalSeconds, 1)
    if ($code -eq 0) { $pass++ } else { $fail++ }
    "run $i exit=$code elapsed=${secs}s" | Add-Content -Encoding ascii $out
    if ($code -ne 0) {
        "---- FAILURE OUTPUT run $i ----" | Add-Content -Encoding ascii $out
        $result | Add-Content -Encoding ascii $out
        "---- END run $i ----" | Add-Content -Encoding ascii $out
    } else {
        ($result | Select-Object -Last 1) | Add-Content -Encoding ascii $out
    }
}

"PASS=$pass FAIL=$fail OF=$runs" | Add-Content -Encoding ascii $out
"END=$(Get-Date -Format o)" | Add-Content -Encoding ascii $out
"DONE_$nonce pass=$pass fail=$fail" | Set-Content -Encoding ascii "staging\test05_determinism_done_$nonce.txt"
