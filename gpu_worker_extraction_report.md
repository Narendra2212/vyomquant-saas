# GPU Worker Extraction Report

Generated: 2026-06-17 23:43

## Summary
- Items identified: 17
- Items copied: 17

## Extracted Components

| Component | Description |
|-----------|-------------|
| `workers/` | Background/async worker processes |
| `strategies/` | Trading strategy definitions and ML pipelines |
| `model_registry.py` | Model versioning and registry |
| `extracted/` | ML-specific modules (keyword-filtered) |

## Files Identified (17 total)
- `workers/ (background worker processes)`
- `strategies/ (trading strategy definitions + ML pipelines)`
- `model_registry.py`
- `backend\backtesting_engine.py`
- `backend\data_pipeline_validator.py`
- `backend\feature_engineering.py`
- `backend\ml_models.py`
- `backend\observability\optimized_health_checks.py`
- `backend\observability\optimized_logging.py`
- `backend\observability\optimized_metrics_exporter.py`
- `backend\observability\optimized_tracing.py`
- `backend\validation_runtime\validation_metrics_pipeline.py`
- `core\event_pipeline.py`
- `core\ml_safety.py`
- `core\pipeline_guard.py`
- `core\position_model.py`
- `core\models\pydantic_models.py`


## Notes
- No imports rewritten
- Original source UNTOUCHED
- GPU hardware configs to be added in future phase
