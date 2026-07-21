#!/bin/bash
set -e

echo "=========================================="
echo " VyomQuant Backend — ECS Fargate Startup "
echo "=========================================="

# ── 1. Validate mandatory environment variables ────────────────────────────
echo "[startup] Checking required environment variables..."

MISSING_VARS=0

if [ -z "$SUPABASE_URL" ]; then
    echo "[startup] FATAL: SUPABASE_URL is missing."
    MISSING_VARS=1
fi

if [ -z "$SUPABASE_SERVICE_ROLE_KEY" ]; then
    echo "[startup] FATAL: SUPABASE_SERVICE_ROLE_KEY is missing."
    MISSING_VARS=1
fi

if [ -z "$SUPABASE_ANON_KEY" ]; then
    echo "[startup] FATAL: SUPABASE_ANON_KEY is missing."
    MISSING_VARS=1
fi

if [ -z "$MASTER_ENCRYPTION_KEYS" ]; then
    echo "[startup] FATAL: MASTER_ENCRYPTION_KEYS is missing."
    MISSING_VARS=1
fi

if [ -z "$DATABASE_URL" ]; then
    echo "[startup] FATAL: DATABASE_URL is missing."
    MISSING_VARS=1
fi

if [ "$MISSING_VARS" -ne 0 ]; then
    echo "[startup] ERROR: One or more required environment variables are missing. Exiting."
    exit 1
fi

echo "[startup] All required environment variables are present."

# ── 2. Optional: Redis connectivity check (non-fatal) ─────────────────────
# Redis is optional at startup — the app handles Redis fallback gracefully.
# A failed ping here will NOT abort startup.
echo "[startup] Checking Redis connectivity (non-fatal)..."
python3 -c "
import os
try:
    import redis
    redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379')
    r = redis.from_url(redis_url, socket_connect_timeout=3)
    r.ping()
    print('[startup] Redis: CONNECTED (' + redis_url + ')')
except Exception as e:
    print('[startup] Redis: UNAVAILABLE (' + str(e) + ') — app will start in fallback mode')
" || true

# ── 3. Optional: Supabase reachability check (non-fatal) ──────────────────
# The app's SecurityVault will do its own strict validation on boot.
# This check only warns if the endpoint is unreachable at pre-flight.
echo "[startup] Checking Supabase reachability (non-fatal)..."
python3 -c "
import os
try:
    import httpx
    url = os.environ.get('SUPABASE_URL', '')
    key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
    if url and key:
        headers = {'apikey': key, 'Authorization': 'Bearer ' + key}
        res = httpx.get(url + '/rest/v1/', headers=headers, timeout=5.0)
        if res.status_code < 500:
            print('[startup] Supabase: REACHABLE (HTTP ' + str(res.status_code) + ')')
        else:
            print('[startup] Supabase: WARNING — HTTP ' + str(res.status_code) + ' (will retry on boot)')
except Exception as e:
    print('[startup] Supabase: WARNING — unreachable (' + str(e) + ') — app will fail on boot if persistent')
" || true

# ── 4. Launch Gunicorn with Uvicorn workers ────────────────────────────────
echo "[startup] Starting Gunicorn with Uvicorn workers..."
echo "[startup] Entry point: backend_app.main:app"
echo "[startup] Port: ${PORT:-8000}"
echo "[startup] Mode: ${AERORA_MODE:-paper}"

# PYTHONPATH=/app is set in the Dockerfile so backend_app is importable as a package.
exec gunicorn backend_app.main:app \
    --workers "${WORKERS:-2}" \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind "0.0.0.0:${PORT:-8000}" \
    --timeout 120 \
    --graceful-timeout 30 \
    --keep-alive 5 \
    --access-logfile - \
    --error-logfile - \
    --log-level "${LOG_LEVEL:-info}"
