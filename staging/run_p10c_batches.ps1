# Phase 10 / Workstream C -- affected-caller regression, in batches.
#
# WHY BATCHES. Primarily so a failure names a batch (and therefore ~10 files) instead of
# forcing a re-run of the whole set, and so the run survives a single pathological file.
#
# A NOTE ON A MISLEADING EARLIER RESULT, kept because it cost time. One invocation of this
# set reported "4699 skipped, 0 passed". That was NOT a suite pathology: the file list had
# been overwritten by a mangled shell pipeline and held UTF-16 lines of the form
# "tests/foo.py  <- entitlement" rather than paths, so pytest never received the arguments
# intended and fell back on `testpaths` from pytest.ini. The list is now generated only by
# staging/p10c_build_list.py and validated (every entry exists, starts with tests/, ends
# .py) before use. Re-running the corrected list as a SINGLE invocation passes normally, so
# there is no scale effect to report.
param([int]$BatchSize = 10)

$all = @(Get-Content staging/p10c_affected_list.txt | Where-Object { $_.Trim() })
$total = $all.Count
$log = "staging/p10c_batches.txt"
"Workstream C batched regression over $total file(s), batch size $BatchSize" | Set-Content $log

$passed = 0; $failed = 0; $skipped = 0; $errors = 0
$badBatches = @()

for ($i = 0; $i -lt $total; $i += $BatchSize) {
    $end = [Math]::Min($i + $BatchSize - 1, $total - 1)
    $files = $all[$i..$end]
    $tag = "b{0:d3}" -f ($i / $BatchSize)
    $raw = "staging/p10c_batch_$tag.txt"

    & .venv\Scripts\python.exe -m pytest $files -p no:randomly -q --no-header -p no:warnings `
        --timeout=600 *> $raw
    $code = $LASTEXITCODE
    $summary = (Get-Content $raw -Tail 1)

    $p = 0; $f = 0; $s = 0; $e = 0
    if ($summary -match '(\d+) passed')  { $p = [int]$Matches[1] }
    if ($summary -match '(\d+) failed')  { $f = [int]$Matches[1] }
    if ($summary -match '(\d+) skipped') { $s = [int]$Matches[1] }
    if ($summary -match '(\d+) error')   { $e = [int]$Matches[1] }
    $passed += $p; $failed += $f; $skipped += $s; $errors += $e

    $line = "[$tag] files $i..$end  exit=$code  passed=$p failed=$f skipped=$s errors=$e"
    Write-Output $line
    Add-Content $log $line
    if ($f -gt 0 -or $e -gt 0) { $badBatches += $tag }
    if ($p -eq 0 -and $s -gt 0 -and $f -eq 0) {
        Add-Content $log "[$tag] NOTE: zero passes with $s skips -- inspect $raw"
    }
}

$tot = "TOTAL passed=$passed failed=$failed skipped=$skipped errors=$errors"
Write-Output $tot
Add-Content $log $tot
if ($badBatches.Count) {
    $b = "BATCHES WITH FAILURES/ERRORS: $($badBatches -join ', ')"
    Write-Output $b; Add-Content $log $b
} else {
    Write-Output "no failures or errors in any batch"; Add-Content $log "no failures or errors in any batch"
}
Set-Content "staging/p10c_batches_done.txt" "done failed=$failed errors=$errors"
