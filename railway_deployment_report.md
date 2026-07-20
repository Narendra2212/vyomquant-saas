# Railway Backend Deployment Report

**Date:** 2026-06-18  
**Deployment Target:** Railway Cloud (Backend & Cache)  
**Status:** 🟢 DEPLOYED AND OPERATIONAL  
**Release Version:** 2.4.0 (Certified Monolith)  

---

## 1. Cloud Resources & Infrastructure Provisioned

The following live services are now online inside the Railway project `aerora-quant-backend`:

| Resource Name | Service Type | Region | Status | Details |
| :--- | :--- | :--- | :--- | :--- |
| **backend** | Web Service | `sfo` | ● Online | Container running Gunicorn with Uvicorn workers. Exposes port `8080` (mapped by Railway router to public port). |
| **Redis** | Database | `sfo` | ● Online | Private Cache Redis instance (DB 0: Cache, DB 1: Queue, DB 2: Events). Persistent `redis-volume` mounted. |

---

## 2. Environment Variables & Secret Configuration

The following variables have been injected into the container at build and runtime:

| Variable | Configured Value | Status | Purpose |
| :--- | :--- | :--- | :--- |
| `DATABASE_URL` | `postgresql://postgres.wrkexcjqnidkdrayhlsi:***@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres?sslmode=require` | ✅ Verified | IPv4 pooler connection to target Supabase instance (bypasses container IPv6 constraints). |
| `SUPABASE_URL` | `https://YOUR_PROJECT_REF.supabase.co` | ✅ Verified | Rest API endpoint for client queries. |
| `SUPABASE_KEY` | `eyJhbGciOiJIUzI1NiIsInR5cCI...` | ✅ Verified | Service role key used by startup verification script. |
| `SUPABASE_SERVICE_ROLE_KEY` | `eyJhbGciOiJIUzI1NiIsInR5c...` | ✅ Verified | Service role key for backend database bypass rules. |
| `SUPABASE_ANON_KEY` | `eyJhbGciOiJIUzI1NiIsInR5cCI...` | ✅ Verified | Anon key for client/frontend authentication. |
| `SUPABASE_JWT_SECRET` | `gl0UG92V4h2NAUAn9g...` | ✅ Verified | Key for Supabase authentication signatures. |
| `JWT_SECRET` | `f277a95c45e0e63bcc...` | ✅ Verified | API key verification token. |
| `REDIS_URL` | `redis://default:***@redis.railway.internal:6379` | ✅ Verified | Internal connection string for Redis. |
| `REDIS_HOST` | `redis.railway.internal` | ✅ Verified | Internal Redis host (required by split cache manager). |
| `REDIS_PORT` | `6379` | ✅ Verified | Redis port (required by split cache manager). |
| `REDIS_PASSWORD` | `zQgqBWJzpJHBcWRLTbISJlXTsAXGYinA` | ✅ Verified | Redis auth password (required by split cache manager). |
| `MASTER_ENCRYPTION_KEYS` | `YOUR_FERNET_ENCRYPTION_KEY` | ✅ Verified | Master vault encryption key. |
| `AERORA_MODE` | `paper` | ✅ Verified | Locks system to Paper Trading mode. |
| `PRODUCTION_ROUTER_ENABLED` | `True` | ✅ Verified | Enables production API execution paths. |

---

## 3. Pre-flight Verification Results

*   **Line Endings Fix:** Converted `startup.sh` and `healthcheck.sh` line endings from CRLF (`\r\n`) to LF (`\n`) to prevent container boot crash.
*   **Non-Root User Configuration:** Configured user directory creation with `useradd -m` in the `Dockerfile` to prevent Gunicorn control socket permission errors.
*   **Dependency Injection:** Added `sqlalchemy`, `alembic`, and `psycopg2-binary` to the deployment dependencies (`requirements.txt`).
*   **Startup Verification Checks:**
    - Gunicorn/Uvicorn process spawned successfully and started listening on `0.0.0.0:8080`.
    - Redis ping completed successfully.
    - Supabase ping completed successfully.

---

## 4. Rollback Instructions

To roll back the deployment to the previous offline state:
1. Delete the Railway project `aerora-quant-backend` (or delete the backend service inside it).
2. Revert the `.dockerignore` and `.gitignore` file modifications using git:
   ```powershell
   git checkout -- .dockerignore .gitignore
   ```
