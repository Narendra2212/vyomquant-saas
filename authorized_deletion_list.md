# Authorized Deletion List

> [!NOTE]
> All functional certifications logically passed. The following original files in the root are authorized for deletion by category.

## SAFE_DELETE_NOW

Files that are dead code, unreferenced, and have no test coverage:

| File | Reason |
|---|---|
| `connection_engine.py` | 3-line stub, zero logic, zero imports |
| `data_seeking_engine.py` | 3-line stub, zero logic, zero imports |
| `order_execution_engine.py` | 3-line stub, zero logic, zero imports |
| `disable.py` | Dev utility, not in any import chain |
| `check_enum.py` | Dev utility, not in any import chain |
| `check_indexes.py` | Dev utility, not in any import chain |
| `check_positions.py` | Dev utility, not in any import chain |
| `check_tables.py` | Dev utility, not in any import chain |
| `inspect_db.py` | Dev utility, not in any import chain |
| `introspect_models.py` | Dev utility, not in any import chain |
| `detail_check.py` | Dev utility, not in any import chain |
| `stripe_test.py` | Test utility, not in any import chain |
| `api_test.py` | Test utility, not in any import chain |
| `temp_key.txt` | Temporary credential file — security risk |

## DELETE_AFTER_30_DAYS

Files that are likely obsolete but need a monitoring window:

| File | Reason |
|---|---|
| `fix_auth.py` | One-time migration fix script |
| `fix_enum_case.py` | One-time migration fix script |
| `fix_sql.py` | One-time migration fix script |
| `patch_missing_routes.py` | One-time patch script |
| `patch_vault.py` | One-time patch script |
| `verify_fix3_risk_trip.py` | One-time verification script |
| `verify_fix4_stripe.py` | One-time verification script |
| `find_duplicates.py` | Analysis utility, superseded by reports |
| `schema_inspector.py` | Dev utility, superseded by alembic |
| `migrate_db.py` | One-time migration, handled by alembic |
| `main.py.bak` | Backup file, not executable |
| `burn_in.log` | Log artifact |
| `burn_in_results.txt` | Test artifact |
| `dead_code_report.txt` | Analysis artifact |
| `unused_imports_report.txt` | Analysis artifact |
| `duplicate_report.txt` | Analysis artifact |
| `backend/distributed_execution/dr_testing_framework.py` | Testing framework, not in runtime path |
| `aerora_fallback.db` | Local development database |
| `algo22.db` | Local development database |

## KEEP_FOREVER

| Category | Files |
|---|---|
| Packages | `backend_app/`, `connection_layer/` |
| Config | `requirements.txt`, `Dockerfile`, `railway.json`, `startup.sh` |
