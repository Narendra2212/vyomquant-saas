# Backend API Extraction Report

Generated: 2026-06-18

## Goal
Extract the core FastAPI web service from `aerora_quant_backend_updated_final1/` into `aerora_quant_platform/backend_api/`.

## Component Responsibilities
- Authentication and JWT validation
- User and Strategy management
- Portfolio and Risk APIs
- Paper trading and Execution APIs
- Alembic database migrations

## Extraction Scope
- `core/`
- `backend/`
- `routers/`
- `api/`
- `main.py`
- `alembic/`
- `requirements.txt`

## Execution Steps (Copy-Only)
```powershell
$src = "d:\aerora_quant_backend_updated_final1\aerora_quant_backend_updated_final1"
$dest = "d:\aerora_quant_backend_updated_final1\aerora_quant_platform\backend_api"

Copy-Item -Path "$src\core" -Destination "$dest" -Recurse -Force
Copy-Item -Path "$src\backend" -Destination "$dest" -Recurse -Force
Copy-Item -Path "$src\routers" -Destination "$dest" -Recurse -Force
Copy-Item -Path "$src\api" -Destination "$dest" -Recurse -Force
Copy-Item -Path "$src\main.py" -Destination "$dest" -Force
Copy-Item -Path "$src\alembic" -Destination "$dest" -Recurse -Force
Copy-Item -Path "$src\requirements.txt" -Destination "$dest" -Force
```

## Rollback Instructions
```powershell
Remove-Item -Recurse -Force "d:\aerora_quant_backend_updated_final1\aerora_quant_platform\backend_api\*"
```

## Verification
- Core web API files present.
- Deployment target ready for Railway.
