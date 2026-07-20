$BackupDir = "backup_temp"
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

# 1. Export full dependency inventory
.\venv\Scripts\python.exe -m pip freeze > "$BackupDir\requirements.lock"

# 2. Save current validation reports (*.md containing validation info or specific files)
Copy-Item -Path ".\*.md" -Destination $BackupDir -ErrorAction SilentlyContinue

# 3. Save current .env files
Copy-Item -Path ".\.env*" -Destination $BackupDir -ErrorAction SilentlyContinue
Copy-Item -Path ".\*\.env*" -Destination $BackupDir -ErrorAction SilentlyContinue

# 4. Save Supabase keys (already in .env, but let's grab anything specific like supabase config)
# 5. Save Redis configs
Copy-Item -Path ".\redis.conf" -Destination $BackupDir -ErrorAction SilentlyContinue

# 6. Save Docker configs
Copy-Item -Path ".\docker-compose*.yml" -Destination $BackupDir -ErrorAction SilentlyContinue
Copy-Item -Path ".\Dockerfile*" -Destination $BackupDir -ErrorAction SilentlyContinue

# Also grab the source code just in case
Copy-Item -Path ".\aerora_quant_backend_updated_final1" -Destination $BackupDir -Recurse -ErrorAction SilentlyContinue
# Remove pycache or venv from backup_temp if it copied
Remove-Item "$BackupDir\aerora_quant_backend_updated_final1\__pycache__" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item "$BackupDir\aerora_quant_backend_updated_final1\venv" -Recurse -Force -ErrorAction SilentlyContinue

# 7. Create rollback archive
if (Test-Path "aerora_pre_py312_backup.zip") {
    Remove-Item "aerora_pre_py312_backup.zip" -Force
}
Compress-Archive -Path "$BackupDir\*" -DestinationPath "aerora_pre_py312_backup.zip" -Force

# Clean up temp dir
Remove-Item $BackupDir -Recurse -Force

Write-Output "BACKUP_SUCCESS"
