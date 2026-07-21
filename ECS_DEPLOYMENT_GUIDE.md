# VyomQuant SaaS — AWS ECS Fargate Deployment Guide

> **Account:** `273709947018` | **Region:** `ap-southeast-1`  
> **Cluster:** `vyomquant-cluster` | **Service:** `vyomquant-api-service-cjema2sl`  
> **Task Family:** `vyomquant-api` | **ECR Repo:** `vyomquant-api`  
> **Execution Role:** `ecsTaskExecutionRole`

---

## Startup Order Verification

```
startup.sh validates env vars            <- FATAL if mandatory vars missing
startup.sh: Redis ping (non-fatal)
startup.sh: Supabase reachability (non-fatal)
gunicorn spawns workers
  core/safety_config.py                  <- reads AERORA_MODE
  core/state.py -> backend/security_vault.py.__init__()
    reads: SUPABASE_URL + SERVICE_ROLE_KEY + MASTER_ENCRYPTION_KEYS
    -> Supabase client created + MultiFernet cipher armed
  FastAPI lifespan:
    settings.validate_required()         <- JWT_SECRET + SUPABASE_ANON_KEY
    database / redis / fleet / watchdog start
FastAPI serving on 0.0.0.0:8000
```

---

## Environment Variable Classification

### ECS `environment` block (non-secrets — already in ecs-task-definition-full.json)

| Variable | Value | Required? |
|----------|-------|-----------|
| `AERORA_MODE` | `paper` | YES |
| `ENV` | `production` | YES |
| `SUPABASE_URL` | `https://your-project.supabase.co` | **YES — FILL THIS IN** |
| `REDIS_URL` | `redis://localhost:6379` | YES |
| `LOG_LEVEL` / `LOG_FORMAT` / `PYTHONUNBUFFERED` / `PYTHONPATH` | `INFO` / `json` / `1` / `/app` | YES |
| `PORT` / `WORKERS` | `8000` / `2` | YES |
| `CORS_ORIGINS` | `https://app.vyomquant.com,...` | YES |
| `PRODUCTION_ROUTER_ENABLED` | `False` | Safety flag |
| Feature flags | `true`/`false` | Optional |

### ECS `secrets` block (AWS Secrets Manager — 6 secrets required)

| Variable | Secret Path |
|----------|-------------|
| `SUPABASE_SERVICE_ROLE_KEY` | `/vyomquant/production/SUPABASE_SERVICE_ROLE_KEY` |
| `SUPABASE_ANON_KEY` | `/vyomquant/production/SUPABASE_ANON_KEY` |
| `SUPABASE_JWT_SECRET` | `/vyomquant/production/SUPABASE_JWT_SECRET` |
| `DATABASE_URL` | `/vyomquant/production/DATABASE_URL` |
| `MASTER_ENCRYPTION_KEYS` | `/vyomquant/production/MASTER_ENCRYPTION_KEYS` |
| `JWT_SECRET` | `/vyomquant/production/JWT_SECRET` |

---

## STEP 1 — Generate Missing Secret Values

```bash
# AES-256 Fernet key for MASTER_ENCRYPTION_KEYS
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# JWT_SECRET (64-byte hex)
python3 -c "import secrets; print(secrets.token_hex(64))"
```

From **Supabase Dashboard → Settings → API**: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_ANON_KEY`, `SUPABASE_JWT_SECRET`  
From **Supabase Dashboard → Settings → Database → URI**: `DATABASE_URL`

---

## STEP 2 — Configure AWS CLI

```bash
aws configure
# Region: ap-southeast-1

aws sts get-caller-identity --region ap-southeast-1
# Expected Account: 273709947018
```

---

## STEP 3 — Verify and Fix IAM Execution Role

```bash
# Check attached policies
aws iam list-attached-role-policies --role-name ecsTaskExecutionRole --output table

# Attach base ECS policy (if missing)
aws iam attach-role-policy \
  --role-name ecsTaskExecutionRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy

# Add Secrets Manager inline policy (REQUIRED)
aws iam put-role-policy \
  --role-name ecsTaskExecutionRole \
  --policy-name VyomQuantSecretsAccess \
  --policy-document file://ecs-execution-role-policy.json

# Verify
aws iam get-role-policy \
  --role-name ecsTaskExecutionRole \
  --policy-name VyomQuantSecretsAccess
```

---

## STEP 4 — Create Secrets in AWS Secrets Manager

```bash
export SUPABASE_SERVICE_ROLE_KEY="eyJhbGci..."
export SUPABASE_ANON_KEY="eyJhbGci..."
export SUPABASE_JWT_SECRET="your-supabase-jwt-secret"
export DATABASE_URL="postgresql://postgres.PROJECT:PASSWORD@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres"
export MASTER_ENCRYPTION_KEYS="your-fernet-key"
export JWT_SECRET="your-64-char-hex"

bash ecs-secrets-manager-setup.sh

# Verify 6 secrets exist
aws secretsmanager list-secrets \
  --region ap-southeast-1 \
  --query "SecretList[?starts_with(Name, '/vyomquant/production/')].{Name:Name,ARN:ARN}" \
  --output table
```

---

## STEP 5 — Edit SUPABASE_URL in Task Definition

Open `ecs-task-definition-full.json` and replace:
```
"REPLACE_WITH_YOUR_SUPABASE_URL"
```
with your actual URL:
```
"https://your-project-ref.supabase.co"
```

---

## STEP 6 — Register New Task Definition Revision

```bash
aws ecs register-task-definition \
  --cli-input-json file://ecs-task-definition-full.json \
  --region ap-southeast-1 \
  --query "taskDefinition.{Family:family,Revision:revision,Status:status}" \
  --output table
```

**Verify secrets were registered:**
```bash
aws ecs describe-task-definition \
  --task-definition vyomquant-api \
  --region ap-southeast-1 \
  --query "taskDefinition.{Revision:revision,SecretCount:length(containerDefinitions[0].secrets)}" \
  --output table
# Expected: SecretCount = 6
```

---

## STEP 7 — Deploy to ECS Service

```bash
aws ecs update-service \
  --cluster vyomquant-cluster \
  --service vyomquant-api-service-cjema2sl \
  --task-definition vyomquant-api \
  --force-new-deployment \
  --region ap-southeast-1 \
  --query "service.{ServiceName:serviceName,DesiredCount:desiredCount,TaskDef:taskDefinition}" \
  --output table
```

---

## STEP 8 — Wait for Stable Deployment

```bash
aws ecs wait services-stable \
  --cluster vyomquant-cluster \
  --services vyomquant-api-service-cjema2sl \
  --region ap-southeast-1

echo "Deployment stable: $(date)"
```

---

## STEP 9 — Verify Running Task

```bash
aws ecs describe-services \
  --cluster vyomquant-cluster \
  --services vyomquant-api-service-cjema2sl \
  --region ap-southeast-1 \
  --query "services[0].{Status:status,Running:runningCount,Desired:desiredCount,Pending:pendingCount}" \
  --output table

TASK_ARN=$(aws ecs list-tasks \
  --cluster vyomquant-cluster \
  --service-name vyomquant-api-service-cjema2sl \
  --region ap-southeast-1 \
  --query "taskArns[0]" \
  --output text)

aws ecs describe-tasks \
  --cluster vyomquant-cluster \
  --tasks "$TASK_ARN" \
  --region ap-southeast-1 \
  --query "tasks[0].{Status:lastStatus,Health:healthStatus,StoppedReason:stoppedReason}" \
  --output table
```

---

## STEP 10 — Check CloudWatch Logs

```bash
LOG_STREAM=$(aws logs describe-log-streams \
  --log-group-name /ecs/vyomquant-api \
  --region ap-southeast-1 \
  --order-by LastEventTime \
  --descending \
  --max-items 1 \
  --query "logStreams[0].logStreamName" \
  --output text)

aws logs get-log-events \
  --log-group-name /ecs/vyomquant-api \
  --log-stream-name "$LOG_STREAM" \
  --region ap-southeast-1 \
  --limit 100 \
  --query "events[*].message" \
  --output text
```

**Green lines (success):**
```
[startup] All required environment variables are present.
 ALGO22 server starting up...
 Application startup complete.
```

**Red lines (failure — see STEP 11):**
```
[startup] FATAL: ... is missing.
FATAL: Missing required environment variables:
EnvironmentError: Missing Supabase configuration.
ResourceInitializationError: unable to pull secrets
```

---

## STEP 11 — Rollback If Needed

```bash
# Get current revision number
CURRENT_REV=$(aws ecs describe-task-definition \
  --task-definition vyomquant-api \
  --region ap-southeast-1 \
  --query "taskDefinition.revision" \
  --output text)

PREVIOUS_REV=$((CURRENT_REV - 1))

# Roll back
aws ecs update-service \
  --cluster vyomquant-cluster \
  --service vyomquant-api-service-cjema2sl \
  --task-definition "vyomquant-api:${PREVIOUS_REV}" \
  --region ap-southeast-1 \
  --query "service.{TaskDef:taskDefinition,Status:status}" \
  --output table

aws ecs wait services-stable \
  --cluster vyomquant-cluster \
  --services vyomquant-api-service-cjema2sl \
  --region ap-southeast-1

echo "Rollback to revision ${PREVIOUS_REV} complete."
```

---

## Failure Diagnosis Quick Reference

| Error Message | Root Cause | Fix |
|---------------|-----------|-----|
| `FATAL: SUPABASE_URL missing` | Env block not in task def | Re-register task definition |
| `FATAL: SUPABASE_SERVICE_ROLE_KEY missing` | Secret not in Secrets Manager | Run Step 4 |
| `ResourceInitializationError: unable to pull secrets` | IAM role missing `secretsmanager:GetSecretValue` | Run Step 3 |
| `CannotPullContainerError` | ECR image URI wrong region | Verify `ap-southeast-1` in image URI |
| Task PENDING forever | VPC/subnet/SG issue | Check task networking config |
| Health check fails | `/health/live` blocked by SG | Open port 8000 inbound in task security group |

---

## Only 2 Things You Need to Supply

1. **`SUPABASE_URL`** — edit in `ecs-task-definition-full.json`
2. **6 secret values** — export as env vars and run `ecs-secrets-manager-setup.sh`

All other values (account, region, cluster, service, ECR, role) are already set.
