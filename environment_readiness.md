# Environment Readiness Report

Generated: 2026-06-18  
Method: Static analysis of `startup.sh`, `core/config.py`, `.env.production.template`  
Note: **No secret values are exposed. Only PRESENT / MISSING status shown.**

---

## Verdict: ⚠️ REQUIRES CONFIGURATION (env vars must be set in Railway dashboard)

The application will NOT start without these variables being set in Railway.  
This is expected — secrets are never committed to git.

---

## Required Environment Variables

| Variable | startup.sh Check | app Config Read | Status | Action |
|----------|-----------------|-----------------|--------|--------|
| `DATABASE_URL` | ✅ Hard-checked | `core/database.py` | ❌ MISSING (local) | Set in Railway |
| `SUPABASE_URL` | ✅ Hard-checked | `core/config.py` | ❌ MISSING (local) | Set in Railway |
| `SUPABASE_KEY` | ✅ Hard-checked | ⚠️ NOT READ by app | ❌ MISSING (local) | Set to same value as `SUPABASE_SERVICE_ROLE_KEY` |
| `SUPABASE_SERVICE_ROLE_KEY` | ❌ Not checked in startup.sh | `core/config.py` | ❌ MISSING (local) | Set in Railway |
| `SUPABASE_ANON_KEY` | ❌ Not checked | `core/config.py` | ❌ MISSING (local) | Set in Railway |
| `SUPABASE_JWT_SECRET` | ❌ Not checked | `core/config.py` (required) | ❌ MISSING (local) | Set in Railway |
| `REDIS_URL` | ✅ Validated (ping) | `core/config.py` | ❌ MISSING (local) | Set to Railway Redis URL |

---

## Optional Environment Variables

| Variable | Purpose | Status | Notes |
|----------|---------|--------|-------|
| `JWT_SECRET` | JWT signing (falls back to default) | ❌ MISSING | **Set for production** — default is `dev-secret-change-in-production` |
| `MASTER_ENCRYPTION_KEYS` | AES-256 credential vault | ❌ MISSING | Required for exchange key storage |
| `AERORA_MODE` | Execution mode: `safe`/`paper`/`live` | ❌ MISSING | Default: `safe` — set to `paper` for production |
| `SENTRY_DSN` | Error monitoring | ❌ MISSING | Non-blocking |
| `QUESTDB_HOST` | Telemetry engine | ❌ MISSING | Falls back to `127.0.0.1` (no-op if unavailable) |
| `CORS_ORIGINS` | Allowed frontend origins | ❌ MISSING | Defaults to `https://algo22.io` |
| `STRIPE_SECRET_KEY` | Billing | ❌ MISSING | Non-blocking if billing unused |
| `DISCORD_WEBHOOK_URL` | Alert engine | ❌ MISSING | Non-blocking |

---

## ⚠️ Critical Naming Mismatch — Action Required

```
startup.sh validates:    SUPABASE_KEY
core/config.py reads:    SUPABASE_SERVICE_ROLE_KEY
```

**Solution:** Set BOTH variables in Railway to the same Supabase Service Role Key value:
```
SUPABASE_KEY=<your-service-role-key>
SUPABASE_SERVICE_ROLE_KEY=<your-service-role-key>
```

---

## Railway Environment Variables Checklist

Copy this checklist into Railway → Service → Variables:

```
DATABASE_URL=postgresql://<user>:<pass>@<host>:<port>/<db>?sslmode=require
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_KEY=<service-role-key>
SUPABASE_SERVICE_ROLE_KEY=<service-role-key>
SUPABASE_ANON_KEY=<anon-key>
SUPABASE_JWT_SECRET=<jwt-secret-from-supabase-dashboard>
JWT_SECRET=<same-as-supabase-jwt-secret>
REDIS_URL=redis://default:<password>@<host>:<port>
MASTER_ENCRYPTION_KEYS=<fernet-key>
AERORA_MODE=paper
PRODUCTION_ROUTER_ENABLED=True
CORS_ORIGINS=https://<your-vercel-domain>.vercel.app,https://app.algo22.io
SENTRY_DSN=<sentry-dsn>
```

---

## Rollback
```powershell
# No files modified. Environment variables are managed in Railway dashboard.
# To rollback: remove all variables from Railway service settings.
```
