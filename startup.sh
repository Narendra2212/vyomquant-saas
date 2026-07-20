#!/bin/bash
set -e

echo "Running production startup checks..."

# 1. Check required env vars
if [ -z "$DATABASE_URL" ]; then
    echo "ERROR: DATABASE_URL is missing."
    exit 1
fi
if [ -z "$SUPABASE_URL" ]; then
    echo "ERROR: SUPABASE_URL is missing."
    exit 1
fi
if [ -z "$SUPABASE_KEY" ]; then
    echo "ERROR: SUPABASE_KEY is missing."
    exit 1
fi

echo "All required environment variables are present."

# 2. Connection Validation (Redis & Supabase)
python -c "
import os, sys
try:
    import redis
    import httpx
except ImportError as e:
    print('Failed to import dependencies:', e)
    sys.exit(1)

print('Validating Redis connection...')
try:
    redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379')
    r = redis.from_url(redis_url)
    r.ping()
    print('✓ Redis connection successful.')
except Exception as e:
    print('✗ Redis connection failed:', str(e))
    sys.exit(1)

print('Validating Supabase connection...')
try:
    url = os.environ['SUPABASE_URL']
    key = os.environ['SUPABASE_KEY']
    headers = {'apikey': key, 'Authorization': f'Bearer {key}'}
    # Simple GET request to check Supabase health
    res = httpx.get(f'{url}/rest/v1/', headers=headers, timeout=5.0)
    if res.status_code >= 400 and res.status_code != 404:
        print('✗ Supabase connection failed with status:', res.status_code)
        sys.exit(1)
    print('✓ Supabase connection successful.')
except Exception as e:
    print('✗ Supabase connection failed:', str(e))
    sys.exit(1)
"

if [ $? -ne 0 ]; then
    echo "ERROR: Startup validation failed."
    exit 1
fi

echo "Starting Gunicorn with Uvicorn workers..."
# 3. Start Gunicorn with Uvicorn Workers
# Uses backend_app.main:app — the canonical package-qualified entry point.
# PYTHONPATH=/app is set in the Dockerfile so backend_app is importable.
exec gunicorn backend_app.main:app --workers 4 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:${PORT:-8000} --timeout 120
