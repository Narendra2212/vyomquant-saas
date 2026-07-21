# VyomQuant SaaS — ECS Fargate Production Deployment Report

**Generated:** 2026-07-20
**Scope:** Production deployment readiness for AWS ECS Fargate

---

## 1. Root Cause Analysis

### 1.1 The FATAL Crash

The application exits immediately in ECS because `backend_app/backend/security_vault.py` performs
fail-fast validation at `__init__` time, before FastAPI can even start:

```python
# backend_app/backend/security_vault.py — lines 72-84
def __init__(self):
    supa_url    = os.environ.get("SUPABASE_URL")
    supa_key    = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    keys_string = os.environ.get("MASTER_ENCRYPTION_KEYS")

    if not supa_url or not supa_key:
        logger.critical("FATAL: SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing.")
        raise EnvironmentError("Missing Supabase configuration.")   # CRASH

    if not keys_string:
        logger.critical("FATAL: MASTER_ENCRYPTION_KEYS missing.")
        raise EnvironmentError("Missing master encryption keys.")   # CRASH
```

`core/state.py` imports this class at **module level** (import time), before FastAPI lifespan.
The ECS Task Definition had `Environment: []` and `Secrets: null` — so the variables were never injected.

### 1.2 Additional Bugs Fixed

| # | File | Bug | Fix |
|---|------|-----|-----|
| 1 | `startup.sh` | Checked `SUPABASE_KEY` — code uses `SUPABASE_SERVICE_ROLE_KEY` | Corrected |
| 2 | `startup.sh` | `MASTER_ENCRYPTION_KEYS` never checked | Added check |
| 3 | `startup.sh` | Redis ping failure killed startup | Made non-fatal |
| 4 | `startup.sh` | Supabase pre-flight failure killed startup | Made non-fatal warning |
| 5 | `core/auth_middleware.py` | JWKS URL hardcoded to `YOUR_PROJECT_REF.supabase.co` | Dynamic from `SUPABASE_URL` |

---

## 2. Environment Variable Audit

### MANDATORY (crash without these)

| Variable | Delivery | Notes |
|----------|----------|-------|
| `SUPABASE_URL` | Environment | Public project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | **Secrets Manager** | Bypasses RLS — server only |
| `SUPABASE_ANON_KEY` | **Secrets Manager** | For RLS-enforced requests |
| `SUPABASE_JWT_SECRET` | **Secrets Manager** | HS256 token verification |
| `MASTER_ENCRYPTION_KEYS` | **Secrets Manager** | AES-256 Fernet (comma-separated) |
| `DATABASE_URL` | **Secrets Manager** | PostgreSQL DSN |
| `AERORA_MODE` | Environment | `paper` or `live` |
| `JWT_SECRET` | **Secrets Manager** | Internal JWT signing |

### IMPORTANT — Optional with Defaults

| Variable | Default | Notes |
|----------|---------|-------|
| `REDIS_URL` | `redis://localhost:6379` | App degrades gracefully |
| `LOG_LEVEL` | `INFO` | CloudWatch log verbosity |
| `PORT` | `8000` | Container port |
| `WORKERS` | `2` | Gunicorn workers |
| `CORS_ORIGINS` | prod URLs | Comma-separated |
| `SENTRY_DSN` | None | Error tracking |

### OPTIONAL — Billing

| Variable | Notes |
|----------|-------|
| `STRIPE_SECRET_KEY` | Only if Stripe billing active |
| `STRIPE_WEBHOOK_SECRET` | Only if Stripe webhooks configured |
| `RAZORPAY_KEY_ID` | Only if Razorpay active |
| `RAZORPAY_KEY_SECRET` | Only if Razorpay active |

### NEVER SET IN PRODUCTION

`DEV_MODE=true`, `DEBUG=true`, `TEST_USER_EMAIL`, `TEST_USER_PASSWORD`

---

## 3. Files Modified

| File | Change | Reason |
|------|--------|--------|
| `startup.sh` | Fixed `SUPABASE_KEY`→`SUPABASE_SERVICE_ROLE_KEY`, added `MASTER_ENCRYPTION_KEYS` check, Redis/Supabase non-fatal | Naming mismatch caused silent boot failure |
| `backend_app/core/auth_middleware.py` | Dynamic JWKS URL from `SUPABASE_URL` env var | ES256 JWT auth broken for all users |

## 4. Files Created

| File | Purpose |
|------|---------|
| `ecs-secrets-manager-setup.sh` | Bootstrap all secrets in AWS Secrets Manager |
| `ecs-task-definition-full.json` | Complete ECS Task Definition with env + secrets |
| `ecs-execution-role-policy.json` | IAM policy for ECS Task Execution Role |

---

## 5. ECS Task Definition Configuration

### 5.1 Environment Block (non-secrets, set directly in task def)

```json
"environment": [
  { "name": "AERORA_MODE",      "value": "paper" },
  { "name": "ENV",              "value": "production" },
  { "name": "LOG_LEVEL",        "value": "INFO" },
  { "name": "PYTHONUNBUFFERED", "value": "1" },
  { "name": "PORT",             "value": "8000" },
  { "name": "WORKERS",          "value": "2" },
  { "name": "SUPABASE_URL",     "value": "https://YOUR_PROJECT_REF.supabase.co" },
  { "name": "REDIS_URL",        "value": "redis://YOUR_ELASTICACHE_ENDPOINT:6379" },
  { "name": "CORS_ORIGINS",     "value": "https://app.vyomquant.com,https://vyomquant.com" }
]
```

### 5.2 Secrets Block (injected from AWS Secrets Manager)

```json
"secrets": [
  { "name": "SUPABASE_SERVICE_ROLE_KEY",
    "valueFrom": "arn:aws:secretsmanager:REGION:ACCOUNT:secret:/vyomquant/production/SUPABASE_SERVICE_ROLE_KEY" },
  { "name": "SUPABASE_ANON_KEY",
    "valueFrom": "arn:aws:secretsmanager:REGION:ACCOUNT:secret:/vyomquant/production/SUPABASE_ANON_KEY" },
  { "name": "SUPABASE_JWT_SECRET",
    "valueFrom": "arn:aws:secretsmanager:REGION:ACCOUNT:secret:/vyomquant/production/SUPABASE_JWT_SECRET" },
  { "name": "DATABASE_URL",
    "valueFrom": "arn:aws:secretsmanager:REGION:ACCOUNT:secret:/vyomquant/production/DATABASE_URL" },
  { "name": "MASTER_ENCRYPTION_KEYS",
    "valueFrom": "arn:aws:secretsmanager:REGION:ACCOUNT:secret:/vyomquant/production/MASTER_ENCRYPTION_KEYS" },
  { "name": "JWT_SECRET",
    "valueFrom": "arn:aws:secretsmanager:REGION:ACCOUNT:secret:/vyomquant/production/JWT_SECRET" }
]
```

---

## 6. Application Startup Order (Verified Correct)

```
startup.sh: Validate mandatory env vars     <- FATAL if missing
startup.sh: Redis check (non-fatal)
startup.sh: Supabase reachability (non-fatal)
  |
  v gunicorn spawn workers
  |
  Python module imports:
    core/safety_config.py        <- AERORA_MODE check
    core/config.py               <- Settings loaded
    core/state.py                <- SecurityVault() <- needs SUPABASE_URL + SERVICE_ROLE_KEY + MASTER_KEYS
    All routers imported
  |
  FastAPI lifespan:
    Sentry init
    validate_required()          <- needs JWT_SECRET + SUPABASE_ANON_KEY
    startup_recovery()
    Redis connect
    QuestDB connect
    AlertEngine arm
    FleetManager online
    ConsistencyChecker start
    OrderWatchdog start
    PnLEngine start
    WebSocket Streamer start
  |
  Serving requests on 0.0.0.0:8000
```

---

## 7. Deployment Checklist

### Step 1 — Generate Secrets (one-time)

```bash
# AES-256 Fernet key for MASTER_ENCRYPTION_KEYS
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# JWT_SECRET
python -c "import secrets; print(secrets.token_hex(64))"
```

Retrieve from **Supabase Dashboard → Settings → API**:
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_ANON_KEY`
- `SUPABASE_JWT_SECRET`

Retrieve from **Supabase Dashboard → Settings → Database**:
- `DATABASE_URL` (PostgreSQL direct connection)

### Step 2 — Upload to AWS Secrets Manager

```bash
# Export all secrets as env vars, then run:
export SUPABASE_SERVICE_ROLE_KEY="eyJhbGci..."
export SUPABASE_ANON_KEY="eyJhbGci..."
export SUPABASE_JWT_SECRET="your-jwt-secret"
export DATABASE_URL="postgresql://postgres:PASSWORD@db.PROJECT.supabase.co:6543/postgres?sslmode=require"
export MASTER_ENCRYPTION_KEYS="your-fernet-key-here"
export JWT_SECRET="your-64-char-hex-secret"

bash ecs-secrets-manager-setup.sh
```

### Step 3 — Update Task Definition Placeholders

Edit `ecs-task-definition-full.json`:
- Replace `YOUR_ACCOUNT_ID` with your AWS account ID
- Replace `YOUR_PROJECT_REF` with your Supabase project ref
- Replace `YOUR_ELASTICACHE_ENDPOINT` with your Redis endpoint
- Replace `ap-south-1` if using a different region
- Update `image` URI with your ECR repo

### Step 4 — IAM Setup

```bash
# Attach execution role policy
aws iam put-role-policy \
  --role-name vyomquant-ecs-execution-role \
  --policy-name SecretsManagerAccess \
  --policy-document file://ecs-execution-role-policy.json
```

### Step 5 — Deploy

```bash
# Register new task definition
aws ecs register-task-definition \
  --cli-input-json file://ecs-task-definition-full.json \
  --region ap-south-1

# Update service
aws ecs update-service \
  --cluster vyomquant-cluster \
  --service vyomquant-backend \
  --task-definition vyomquant-backend \
  --force-new-deployment \
  --region ap-south-1
```

### Step 6 — Verify in CloudWatch

After deployment, the log stream in `/ecs/vyomquant-backend` should show:

```
[startup] All required environment variables are present.
[startup] Starting Gunicorn with Uvicorn workers...
INFO:     ALGO22 server starting up...
INFO:     Service Status: {'redis': 'connected', 'questdb': 'connected', 'supabase': 'connected', ...}
INFO:     Application startup complete.
```

**Health endpoints to check:**
- `GET /health/live` → `{"status": "alive"}` (HTTP 200)
- `GET /health` → `{"status": "ok"}` or `"degraded"` (never 5xx)

---

## 8. Key Architecture Decisions

> **Why `SUPABASE_URL` is an environment variable, not a secret**
> It appears in frontend JS bundles (Supabase anon key + URL are public by design).
> Treating the URL as a secret adds operational complexity with zero security gain.

> **Why fail-fast in `backend/security_vault.py` is kept**
> These credentials are mandatory for exchange key encryption/decryption — the core
> security feature of the platform. A degraded startup without them would silently
> allow unencrypted key storage. The fix is to provide the variables, not to weaken
> the guard.

> **Why Redis startup check is non-fatal**
> Redis failure triggers fallback mode in `redis_manager`. Rate limiting and caching
> degrade gracefully. A fatal startup abort for Redis would block the entire API
> whenever Redis restarts or is momentarily unavailable — inappropriate for production.
