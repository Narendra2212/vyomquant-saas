# Collect-only counts for a set of files. Safe to run alongside a full regression:
# nothing is executed, so no shared fixture, database or .hypothesis state is touched.
$ErrorActionPreference = 'Continue'
Set-Location 'c:\aerora_quant_backend_updated_final1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Remove-Item Env:\AERORA_TEST_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue

$nonce = $args[0]
$files = $args[1..($args.Count - 1)]
$out = "staging\collect_$nonce.txt"
Remove-Item -ErrorAction SilentlyContinue "staging\collect_done_$nonce.txt"
"START=$(Get-Date -Format o)" | Set-Content -Encoding ascii $out

foreach ($f in $files) {
    "=== $f" | Add-Content -Encoding ascii $out
    & '.venv\Scripts\python.exe' -m pytest $f --collect-only -q `
        -p no:cacheprovider -p no:warnings -o addopts="" 2>&1 |
        Select-String -Pattern "tests? collected|error" | Add-Content -Encoding ascii $out
}

"DONE_$nonce" | Set-Content -Encoding ascii "staging\collect_done_$nonce.txt"
