# D5' verification: under CI's EXACT env for the `unit-tests` job, do the real-Redis tests
# get SELECTED (rather than skipped)?
#
# There is no Redis on this machine, so the expected and correct outcome is FAILURE on the
# PING gate -- which is itself the proof that selection happened and that a broken service
# is loud. A skip here would mean the fix did not work.
$log = "staging/d5prime_ci_env_selects_tests.txt"

$env:REDIS_URL = "redis://localhost:6379"              # what CI already set
$env:AERORA_TEST_REDIS_URL = "redis://localhost:6379/15"  # what D5' adds
$env:ENV = "test"
$env:AERORA_MODE = "safe"
try {
    & .venv\Scripts\python.exe -m pytest `
        tests/test_quota01_exchange_slot_is_atomic.py `
        tests/test_quota01_metered_reservation_is_hard.py `
        -p no:randomly -q --no-header -p no:warnings -rs -k "real_redis" --timeout=180 *> $log
    "pytest exit: $LASTEXITCODE" | Add-Content $log
}
finally {
    Remove-Item Env:\REDIS_URL, Env:\AERORA_TEST_REDIS_URL, Env:\ENV, Env:\AERORA_MODE `
        -ErrorAction SilentlyContinue
}
Get-Content $log | Select-Object -Last 10
