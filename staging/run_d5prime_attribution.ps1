# D5' attribution: does the api04 failure occur with this workstream's helper ABSENT?
# Fully reversible; touches only the file D5' created. No git operation.
$log = "staging/d5prime_api04_without_helper.txt"

Move-Item tests/real_redis_gate.py tests/_real_redis_gate.bak -Force
try {
    "helper present during run: $(Test-Path tests/real_redis_gate.py)" | Set-Content $log
    & .venv\Scripts\python.exe -m pytest `
        "tests/test_api04_store_failure_after_startup.py::test_the_sensitive_endpoints_that_must_stay_rate_limited_are_decorated" `
        -p no:randomly -q --no-header -p no:warnings --timeout=180 *>> $log
    "pytest exit: $LASTEXITCODE" | Add-Content $log
}
finally {
    Move-Item tests/_real_redis_gate.bak tests/real_redis_gate.py -Force
    "helper restored: $(Test-Path tests/real_redis_gate.py)" | Add-Content $log
}
Get-Content $log | Select-Object -Last 8
