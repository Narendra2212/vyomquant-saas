# Phase 10 / Workstream C -- regression across every test file that touches a changed module.
# Usage: .\staging\run_p10c_affected.ps1 <TAG>
param([string]$Tag = "WSC2")

$files = @(Get-Content staging/p10c_affected_list.txt | Where-Object { $_.Trim() })
Write-Output "Workstream C affected regression: $($files.Count) file(s), tag=$Tag"

$raw = "staging/p10c_affected_$Tag.txt"
& .venv\Scripts\python.exe -m pytest $files -p no:randomly -q --no-header -p no:warnings `
    --timeout=600 --junitxml="staging/p10c_affected_$Tag.xml" *> $raw
$code = $LASTEXITCODE

Write-Output "pytest exit code: $code"
Set-Content "staging/p10c_affected_done_$Tag.txt" "exit=$code"
Get-Content $raw -Tail 30
