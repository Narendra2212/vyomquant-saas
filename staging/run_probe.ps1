# Detached runner for an arbitrary python probe script.
#
# Usage: Start-Process powershell.exe -File staging\run_probe.ps1 <nonce> <script> [args...]
#
# Same reason as run_suite.ps1: Start-Process'ing python.exe directly does not survive the
# agent tooling's foreground command timeout (exit -1073741510, truncated output files).
$ErrorActionPreference = 'Continue'
Set-Location 'c:\aerora_quant_backend_updated_final1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$nonce = $args[0]
$script = $args[1]
$rest = @()
if ($args.Count -gt 2) { $rest = $args[2..($args.Count - 1)] }

$out = "staging\probe_$nonce.txt"
Remove-Item -ErrorAction SilentlyContinue "staging\probe_done_$nonce.txt"
"START=$(Get-Date -Format o)" | Set-Content -Encoding ascii $out
"SCRIPT=$script $($rest -join ' ')" | Add-Content -Encoding ascii $out

& '.venv\Scripts\python.exe' $script @rest 2>&1 | Add-Content -Encoding ascii $out

$code = $LASTEXITCODE
"EXITCODE=$code" | Add-Content -Encoding ascii $out
"DONE_$nonce exit=$code" | Set-Content -Encoding ascii "staging\probe_done_$nonce.txt"
