$BackupDir = "backup_temp_fast"
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

# 1. Export full dependency inventory
.\venv\Scripts\python.exe -m pip freeze > "$BackupDir\requirements.lock"

# 2. Save current validation reports
Copy-Item -Path ".\*.md" -Destination $BackupDir -ErrorAction SilentlyContinue

# 3. Save current .env files
Copy-Item -Path ".\.env*" -Destination $BackupDir -ErrorAction SilentlyContinue

# 5. Save Redis configs
Copy-Item -Path ".\redis.conf" -Destination $BackupDir -ErrorAction SilentlyContinue

# 6. Save Docker configs
Copy-Item -Path ".\docker-compose*.yml" -Destination $BackupDir -ErrorAction SilentlyContinue
Copy-Item -Path ".\Dockerfile*" -Destination $BackupDir -ErrorAction SilentlyContinue

# 7. Create rollback archive
if (Test-Path "aerora_pre_py312_backup.zip") {
    Remove-Item "aerora_pre_py312_backup.zip" -Force
}
Compress-Archive -Path "$BackupDir\*" -DestinationPath "aerora_pre_py312_backup.zip" -Force

Remove-Item $BackupDir -Recurse -Force
Write-Output "BACKUP_SUCCESS"
