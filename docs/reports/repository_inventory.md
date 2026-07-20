# Aerora Quant Repository Inventory

This inventory lists and categorizes all directories inside the workspace by size, file count, purpose, and disposal criteria.

| Folder | Size (MB) | File Count | Purpose | Safe To Remove | Safe To Archive | Must Keep |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `cleanenv` | 2871.52 | 69271 | Backup python virtual environment with packages | YES (can rebuild from requirements.txt) | NO | NO |
| `aerora_quant_backend_updated_final1` | 1484.71 | 1362 | Main backend application folder containing source, DBs, modules | NO | NO | YES |
| `backup_temp` | 1483.38 | 1314 | Temporary project backup directory | YES (is a redundant backup copy) | YES | NO |
| `backup_temp\aerora_quant_backend_updated_final1` | 1481.69 | 1186 | Nested temporary project backup copy | YES | YES | NO |
| `.git` | 1185.17 | 65524 | Git source history & metadata | NO | NO | YES (exclude from audit pkg) |
| `venv` | 891.23 | 58030 | Active python virtual environment | YES (can rebuild from requirements.txt) | NO | NO |
| `algo22-terminal` | 238.32 | 22100 | Frontend React/Vite/Tauri application folder | NO | NO | YES |
| `algo22-terminal\node_modules` | 234.79 | 21965 | Frontend npm packages | YES (can rebuild via npm install) | NO | NO |
| `aerora_quant_backend_updated_final1\questdb-9.3.5-rt-windows-x86-64` | 228.07 | 394 | QuestDB database distribution server binaries | YES (regenerated from archive/web) | YES | NO |
| `node_modules` | 41.96 | 4587 | Root npm packages for playwright/testing | YES (can rebuild via npm install) | NO | NO |
| `aerora_quant_backend_updated_final1\backend` | 11.82 | 450 | Legacy or fallback backend modules | NO | NO | YES |
| `aerora_quant_backend_updated_final1\test_models` | 7.86 | 17 | Local machine learning trained models registry | NO (requires retraining) | YES | NO |
| `aerora_quant_backend_updated_final1\core` | 3.63 | 234 | Core algorithms, execution logic, order management | NO | NO | YES |
| `logs` | 3.26 | 2 | Platform execution and trade session log files | YES (logs can be generated again) | YES | NO |
| `tests` | 0.92 | 59 | System verification and end-to-end integration tests | NO | NO | YES |
| `algo22-terminal\src` | 0.89 | 59 | Frontend React source files | NO | NO | YES |
| `algo22-terminal\src-tauri` | 0.67 | 28 | Tauri desktop packaging wrapper files | NO | NO | YES |
| `playwright-report` | 0.50 | 1 | Root HTML report for playwright test execution | YES | YES | NO |
| `backend` | 0.37 | 14 | Backend components copy at root level | NO | YES | YES |
| `__pycache__` | 0.29 | 24 | Compiled python bytecode files | YES | NO | NO |
| `api_ws` | 0.13 | 12 | WebSocket API layer files | NO | NO | YES |
| `scripts` | 0.13 | 38 | System auxiliary and verification script utilities | NO | NO | YES |
| `routers` | 0.06 | 2 | FastAPI router definitions | NO | NO | YES |
| `k8s` | 0.05 | 8 | Kubernetes configuration and manifest deployment files | NO | NO | YES |
| `docs` | 0.05 | 10 | System documentation and guides | NO | NO | YES |
| `dev_tools` | 0.04 | 15 | Internal developer tools and scripts | NO | YES | YES |
| `.github` | 0.03 | 3 | GitHub action CI/CD workflow configurations | NO | NO | YES |
| `nginx` | 0.02 | 3 | Nginx reverse proxy configurations | NO | NO | YES |
| `migrations` | 0.02 | 2 | Database alembic migration files | NO | NO | YES |
| `.ruff_cache` | 0.01 | 21 | Linter ruff cached checks | YES | NO | NO |
| `.pytest_cache` | 0.01 | 5 | Pytest test framework caches | YES | NO | NO |
| `monitoring` | 0.01 | 3 | Prometheus/Grafana monitoring dashboard configs | NO | NO | YES |
| `observability` | 0.01 | 3 | Observability configurations for Sentry/GA4 | NO | NO | YES |
| `plans` | 0.01 | 1 | Project implementation design records | NO | YES | YES |
| `terraform` | 0.00 | 1 | Terraform infrastructure provisioning config | NO | NO | YES |
| `.windsurf` | 0.00 | 1 | IDE metadata file | YES | NO | NO |
| `test-results` | 0.00 | 1 | Test framework output files | YES | YES | NO |
| `user stratigies` | 0.00 | 0 | User strategies custom folder | NO | YES | YES |

## Large Workspace Files (>= 0.1 MB)

| File Name | Size (MB) | Purpose | Safe To Remove | Safe To Archive | Must Keep |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `algo22-terminal.zip` | 64.22 | Frontend ZIP distribution bundle | YES | YES | NO |
| `aerora_pre_py312_backup.zip` | 0.49 | Pre-Python 3.12 backup zip | YES | YES | NO |
| `algo22.db` | 0.13 | Active SQLite Database | NO | YES | YES (Review Category D) |