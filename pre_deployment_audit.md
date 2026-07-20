# Pre-Deployment Audit

Generated: 2026-06-18  
Target: `aerora_quant_backend_updated_final1/` (Certified Monolith)  
Deployment: Railway (Backend) + Vercel (Frontend)

---

## Verdict: ✅ READY (with 2 noted items)

---

## File Existence Checks

| Status | File | Size | Notes |
|--------|------|------|-------|
| ✅ PASS | `Dockerfile` | 2,180 bytes | Multi-stage Python 3.11 build |
| ✅ PASS | `Dockerfile.backend` | 862 bytes | Alternative backend Dockerfile |
| ✅ PASS | `startup.sh` | 1,863 bytes | Gunicorn/Uvicorn entrypoint |
| ✅ PASS | `healthcheck.sh` | 66 bytes | `curl /health/live` |
| ✅ PASS | `railway.json` | 353 bytes | Dockerfile builder configured |
| ✅ PASS | `Procfile` | 19 bytes | `web: ./startup.sh` |
| ✅ PASS | `requirements.txt` | 3,556 bytes | 30+ packages |
| ✅ PASS | `main.py` | 25,407 bytes | 649-line FastAPI entrypoint |
| ✅ PASS | `alembic.ini` | 5,141 bytes | Alembic config present |
| ✅ PASS | `.dockerignore` | 143 bytes | Excludes .env, .git, logs |

---

## Application Endpoints Verified

| Status | Endpoint | Source |
|--------|----------|--------|
| ✅ PASS | `GET /health` | `main.py` — full service status |
| ✅ PASS | `GET /health/live` | `main.py` — liveness probe |
| ✅ PASS | `GET /health/services` | `main.py` — runtime services |
| ✅ PASS | `GET /metrics` | `routers/metrics.py` — Prometheus |
| ✅ PASS | `GET /docs` | FastAPI auto-generated (implicit) |

---

## Router Inventory (19 routers)

`admin`, `analytics`, `auth`, `billing`, `dag_tasks`, `distributed_execution`,
`exchange`, `health`, `health_websocket`, `market`, `metrics`, `orders`,
`portfolio`, `risk`, `security`, `strategies`, `support`, `user`, `__init__`

---

## Alembic Migrations

| Migration | File |
|-----------|------|
| Baseline | `4ef23035a692_baseline.py` |
| Execution Schema Rebuild | `d97ffff9c3bb_execution_schema_rebuild.py` |

---

## Noted Items (Non-Blocking for Docker Deploy)

### Item 1 — Missing from `requirements.txt` (installed separately in Dockerfile)
- `gunicorn` — explicitly installed via `pip install --no-cache-dir gunicorn httpx redis` in Dockerfile before `-r requirements.txt`
- `sqlalchemy` / `alembic` / `psycopg2` — pulled in transitively via `supabase==2.7.4` or `ccxt`
- **Resolution:** The Dockerfile explicitly installs `gunicorn` separately. This is the deployment vehicle, so Railway builds are safe.
- **Risk if deploying without Docker (Railway Nixpacks):** MEDIUM — gunicorn would be missing.
- **Recommendation:** Add `gunicorn==21.2.0` and `SQLAlchemy>=2.0` to `requirements.txt` for safety. *(Requires explicit approval to modify file — NOT done here)*

### Item 2 — `SUPABASE_KEY` vs `SUPABASE_SERVICE_ROLE_KEY` naming
- `startup.sh` validates env var `SUPABASE_KEY`
- `core/config.py` reads env var `SUPABASE_SERVICE_ROLE_KEY`
- **Action required:** Set BOTH `SUPABASE_KEY` and `SUPABASE_SERVICE_ROLE_KEY` to the same value in Railway environment variables.
- **Risk:** If only `SUPABASE_SERVICE_ROLE_KEY` is set, startup.sh will exit with error.

---

## Rollback
```powershell
# No files were modified. This is an audit-only step.
# Rollback: N/A
```
