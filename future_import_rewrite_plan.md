# Future Import Rewrite Plan

Generated: 2026-06-17 23:43

> NOTE: This is an ANALYSIS-ONLY report. No imports have been rewritten.
> All rewrites are planned for a future phase after thorough testing.

## Import Mapping Table

| Old Import | New Target Import | Risk Level |
|------------|-------------------|------------|
| `from core.*` | `from backend_api.core.*` | LOW |
| `from backend.*` | `from backend_api.backend.*` | MEDIUM |
| `from routers.*` | `from backend_api.routers.*` | LOW |
| `from api.*` | `from backend_api.api.*` | LOW |
| `from api_ws.*` | `from backend_api.api_ws.*` | MEDIUM |
| `from alembic.*` | `from backend_api.alembic.*` | LOW |
| `from connection.*` | `from connection_layer.connection.*` | MEDIUM |
| `from workers.*` | `from gpu_workers.workers.*` | HIGH |
| `from strategies.*` | `from gpu_workers.strategies.*` | HIGH |
| `from backend_app.core.*` | `from backend_api.core.*` | LOW |
| `from backend_app.backend.*` | `from backend_api.backend.*` | MEDIUM |
| `from backend_app.routers.*` | `from backend_api.routers.*` | LOW |

## Risk Assessment

| Level | Meaning |
|-------|---------|
| LOW | Mechanical rename, no logic changes needed |
| MEDIUM | May affect runtime initialization order |
| HIGH | Requires worker orchestration changes |

## Recommended Rewrite Order
1. `shared/` (no dependencies on other layers)
2. `connection_layer/` (depends on shared)
3. `backend_api/` (depends on shared + connection_layer)
4. `gpu_workers/` (depends on all layers)
5. `frontend_app/` (API-only, no Python imports)

## Scan Commands (Run to quantify scope)
```bash
grep -r "^from core\." backend_api/ --include="*.py" | wc -l
grep -r "^from backend\." backend_api/ --include="*.py" | wc -l
grep -r "^from routers\." backend_api/ --include="*.py" | wc -l
```

## Rollback Plan
- All originals preserved in `aerora_quant_backend_updated_final1/`
- Git history provides full recovery
- `backend_app/` namespace already validated (Phase 2 prior work)
