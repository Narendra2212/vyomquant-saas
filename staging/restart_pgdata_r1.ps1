# Bring the REAL staging cluster (staging/pgdata_r1) back up after the 05:50 crash.
#
# Context: staging/pg_dsn.txt was overwritten at 11:10 to point at a FRESH EMPTY cluster in
# staging/pgdata (port 56595, postmaster PID 13708). That is consistent with a from-scratch
# provisioning rehearsal, which is exactly what bootstrap_pg.py's PG_DATA_DIR override
# exists for. That cluster is LEFT ALONE and pg_dsn.txt is NOT modified, so whoever is using
# it is undisturbed. This script starts pgdata_r1 on its own port and prints its DSN for the
# caller to pass via AERORA_TEST_DATABASE_URL only.
#
# Safety of each step:
#   1. Kills ONLY orphaned auxiliary processes whose parent postmaster no longer exists.
#      The live 56595 cluster's children have a living parent and are excluded by
#      construction. The orphan has burned 0 CPU for hours and the server log has not
#      advanced since 05:52, so it cannot be mid-anything useful; WAL replay restarts from
#      the last checkpoint on the next start regardless.
#   2. RENAMES (does not delete) the stale postmaster.pid, which names dead PID 8752.
#   3. Starts the postmaster with a LONG pg_ctl timeout. The previous start's
#      data-directory fsync alone took 67s, which is why pgserver's hardcoded 10s failed.
#
# It does NOT delete any data directory, does NOT run initdb, and does NOT touch
# staging/pg_dsn.txt or the other cluster.

$ErrorActionPreference = 'Continue'

$pgBin   = 'C:/aerora_quant_backend_updated_final1/.venv/Lib/site-packages/pgserver/pginstall/bin'
$pgCtl   = Join-Path $pgBin 'pg_ctl.exe'
$dataDir = 'C:/aerora_quant_backend_updated_final1/staging/pgdata_r1'
$logFile = 'C:/aerora_quant_backend_updated_final1/staging/pgdata_r1_restart.log'
$port    = 49324

Write-Output '--- step 0: confirm the target cluster has no live postmaster ---'
$live = Get-CimInstance Win32_Process -Filter "Name='postgres.exe'" |
        Where-Object { $_.CommandLine -like '*pgdata_r1*' -and $_.CommandLine -like '*-D*' }
if ($live) {
    Write-Output ('ABORT: a postmaster for pgdata_r1 is already running, PID ' + $live.ProcessId)
    exit 1
}
Write-Output 'OK: no live postmaster for pgdata_r1'

Write-Output '--- step 1: kill orphaned aux processes of the dead cluster ---'
$all = Get-CimInstance Win32_Process -Filter "Name='postgres.exe'"
foreach ($p in $all) {
    if ($p.CommandLine -notlike '*--forkaux*' -and $p.CommandLine -notlike '*--fork*') { continue }
    if ($p.CommandLine -like '*-D*') { continue }
    $nums = [regex]::Matches($p.CommandLine, '(?<=")\d+(?=")') | ForEach-Object { $_.Value }
    if (-not $nums) { continue }
    $parentId = [int]$nums[0]
    $parentAlive = $null -ne (Get-Process -Id $parentId -ErrorAction SilentlyContinue)
    if ($parentAlive) {
        Write-Output ('  KEEP  PID ' + $p.ProcessId + ' (parent ' + $parentId + ' alive, belongs to the live cluster)')
    } else {
        Write-Output ('  KILL  PID ' + $p.ProcessId + ' (parent ' + $parentId + ' is gone)')
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
}
Start-Sleep -Seconds 3

Write-Output '--- step 2: move the stale postmaster.pid aside (reversible) ---'
$pidFile = Join-Path $dataDir 'postmaster.pid'
if (Test-Path $pidFile) {
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    Move-Item $pidFile ($pidFile + '.stale_' + $stamp) -Force
    Write-Output ('  renamed to postmaster.pid.stale_' + $stamp)
} else {
    Write-Output '  no postmaster.pid present'
}

Write-Output ('--- step 3: start the postmaster on port ' + $port + ', pg_ctl timeout 600s ---')
& $pgCtl -D $dataDir -l $logFile -o "-h 127.0.0.1" -o ('-p ' + $port) -t 600 -w start
Write-Output ('pg_ctl exit code: ' + $LASTEXITCODE)

Write-Output '--- step 4: status ---'
& $pgCtl -D $dataDir status
Write-Output ''
Write-Output ('DSN_FOR_THIS_CLUSTER: postgresql://postgres:@127.0.0.1:' + $port + '/postgres')
Write-Output 'NOTE: staging/pg_dsn.txt was deliberately NOT modified.'
