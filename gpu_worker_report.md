# GPU Workers Extraction Report

Generated: 2026-06-18

## Goal
Isolate Machine Learning models, backtesting, and heavy computational tasks into an independent ML service at `aerora_quant_platform/gpu_workers/`.

## Component Responsibilities
- ML Model Training (TensorFlow / XGBoost)
- Optimization and Backtesting
- Feature Engineering
- Strategy Execution
- Model Registry

## Extraction Scope
- `workers/`
- `strategies/`
- `backend/ml_models.py`
- `backend/feature_engineering.py`
- `backend/backtesting_engine.py`

## Execution Steps (Copy-Only)
```powershell
$src = "d:\aerora_quant_backend_updated_final1\aerora_quant_backend_updated_final1"
$dest = "d:\aerora_quant_backend_updated_final1\aerora_quant_platform\gpu_workers"

Copy-Item -Path "$src\workers" -Destination "$dest" -Recurse -Force
Copy-Item -Path "$src\strategies" -Destination "$dest" -Recurse -Force
New-Item -ItemType Directory -Force -Path "$dest\backend"
Copy-Item -Path "$src\backend\ml_models.py" -Destination "$dest\backend\" -Force
Copy-Item -Path "$src\backend\feature_engineering.py" -Destination "$dest\backend\" -Force
Copy-Item -Path "$src\backend\backtesting_engine.py" -Destination "$dest\backend\" -Force
```

## Rollback Instructions
```powershell
Remove-Item -Recurse -Force "d:\aerora_quant_backend_updated_final1\aerora_quant_platform\gpu_workers\*"
```

## Deployment Status
**NOT DEPLOYED INITIALLY** as per target architecture.
