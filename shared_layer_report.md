# Shared Layer Report

Generated: 2026-06-18

## Goal
Extract foundational data structures, configurations, and utilities into a shared dependency package at `aerora_quant_platform/shared/`.

## Component Responsibilities
- Pydantic Schemas
- SQLAlchemy Models
- Enums and Constants
- Configuration Management

## Extraction Scope
- `core/schemas.py`
- `core/models/`
- `core/config.py`
- `core/safety_config.py`

## Execution Steps (Copy-Only)
```powershell
$src = "d:\aerora_quant_backend_updated_final1\aerora_quant_backend_updated_final1"
$dest = "d:\aerora_quant_backend_updated_final1\aerora_quant_platform\shared"

New-Item -ItemType Directory -Force -Path "$dest\core"
Copy-Item -Path "$src\core\schemas.py" -Destination "$dest\core\" -Force
Copy-Item -Path "$src\core\models" -Destination "$dest\core\" -Recurse -Force
Copy-Item -Path "$src\core\config.py" -Destination "$dest\core\" -Force
Copy-Item -Path "$src\core\safety_config.py" -Destination "$dest\core\" -Force
```

## Rollback Instructions
```powershell
Remove-Item -Recurse -Force "d:\aerora_quant_backend_updated_final1\aerora_quant_platform\shared\*"
```
