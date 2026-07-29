# DEPLOYMENT.md — Canonical Deployment Guide

> [!IMPORTANT]
> **The sole canonical production deployment target for the VyomQuant platform is AWS ECS Fargate, provisioned via Terraform.**
> All other deployment configurations (Kubernetes, Railway, alternative docker-compose variants) are archived in [`deploy-archive/`](./deploy-archive/) and are not in active production use.

---

## Production Deployment: AWS ECS Fargate + Terraform

### Architecture

The production environment runs on **AWS ECS Fargate** in the `ap-southeast-1` (Singapore) region:

| Component | Technology | Configuration |
|-----------|------------|---------------|
| Compute | ECS Fargate tasks | `terraform/ecs.tf` |
| Load Balancer | AWS Application Load Balancer | `terraform/alb.tf` |
| CDN | AWS CloudFront | `terraform/cloudfront.tf` |
| Container Registry | AWS ECR | `terraform/ecs.tf` |
| Networking | Custom VPC | `terraform/vpc.tf` |
| WAF | AWS WAF | `terraform/waf.tf` |
| Secrets | AWS Secrets Manager | `terraform/secrets.tf` |
| Monitoring | Prometheus + Grafana (in `monitoring/`) | — |

### CI/CD Pipeline

Every push to `main`/`master` triggers a three-stage GitHub Actions pipeline:

1. **[01 PR Check](.github/workflows/01-pr-check.yml)** — Lint, type-check, security scan
2. **[02 Build](.github/workflows/02-build.yml)** — Docker image build + push to ECR
3. **[03 Deploy](.github/workflows/03-deploy.yml)** — ECS service update + self-healing diagnostics

The deploy workflow (`03-deploy.yml`) targets:
- **Cluster**: `vyomquant-cluster`
- **Service**: `vyomquant-api-service-cjema2sl`
- **Region**: `ap-southeast-1`

### Manual Production Deployment

If you need to trigger a deploy manually:

```bash
# 1. Apply any infrastructure changes first
cd terraform/
terraform plan
terraform apply

# 2. Build and push a new Docker image
aws ecr get-login-password --region ap-southeast-1 | \
  docker login --username AWS --password-stdin <ECR_REGISTRY>
docker build -t vyomquant-api .
docker tag vyomquant-api:latest <ECR_REGISTRY>/vyomquant-api:latest
docker push <ECR_REGISTRY>/vyomquant-api:latest

# 3. Force a new ECS deployment
aws ecs update-service \
  --cluster vyomquant-cluster \
  --service vyomquant-api-service-cjema2sl \
  --force-new-deployment \
  --region ap-southeast-1
```

Or use the deployment script:

```bash
python scripts/ecs_deploy_and_diagnose.py
```

### Environment Variables in Production

Production environment variables are managed via **AWS Secrets Manager**. The task definition at `ecs-task-definition-full.json` contains the non-secret variables.

The required variables are documented in [.env.example](.env.example). Key ones:

| Variable | Required | Description |
|----------|----------|-------------|
| `VYOMQUANT_MODE` | ✅ | `safe` / `paper` / `live` — execution safety gate |
| `SUPABASE_URL` | ✅ | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | ✅ | Supabase service role secret |
| `MASTER_ENCRYPTION_KEYS` | ✅ | Comma-separated Fernet keys for credential vault |
| `REDIS_URL` | ✅ | Redis connection string (ElastiCache) |
| `DATABASE_URL` | ✅ | PostgreSQL connection string (Supabase pooler) |

See `ecs-secrets-manager-setup.sh` for the script that populates Secrets Manager for a new environment.

---

## Local Development: Docker Compose

For local development, the root-level `docker-compose.yml` is the supported method. It starts the full stack (API, Postgres, Redis, Prometheus, Grafana, Nginx).

### Setup

```bash
# 1. Copy and fill in environment variables
cp .env.example .env
# Edit .env — at minimum set SUPABASE_* and MASTER_ENCRYPTION_KEYS

# 2. Start the local stack
docker compose up -d

# 3. Check all services are healthy
docker compose logs -f
```

**Accessing the local stack:**
- **Frontend Terminal**: [http://localhost:3000](http://localhost:3000)
- **Backend API (Swagger)**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Prometheus**: [http://localhost:9090](http://localhost:9090)
- **Grafana**: [http://localhost:3001](http://localhost:3001)

> [!NOTE]
> The local `docker-compose.yml` sets `VYOMQUANT_MODE=paper` by default. Do **not** set `VYOMQUANT_MODE=live` in a local environment.

### Bare-Metal (without Docker)

If you prefer to run services directly on your host OS (for faster hot-reload during active development):

**Terminal 1 — Backend:**
```bash
python -m venv venv
source venv/bin/activate   # Windows: .\venv\Scripts\activate
# For Local Development (GPU-enabled PyTorch):
pip install -r requirements.txt

# For Server Deployment (CPU-only PyTorch, lean install):
pip install -r requirements-cpu.txt
uvicorn backend_app.main:app --reload --port 8000
```

**Terminal 2 — Frontend:**
```bash
cd algo22-terminal
npm install
npm run dev
```

The Vite dev server starts at [http://localhost:5173](http://localhost:5173).

---

## Archived / Non-Production Deployment Configurations

The following configurations are **not in active use** and have been moved to [`deploy-archive/`](./deploy-archive/):

| Configuration | Location | Status |
|---------------|----------|--------|
| Kubernetes manifests | `deploy-archive/k8s/` | Evaluated, not in production |
| Railway | `deploy-archive/railway/` | Evaluated, not in production |
| `docker-compose.production.yml` | `deploy-archive/docker-compose/` | Superseded by ECS |
| `docker-compose.staging.yml` | `deploy-archive/docker-compose/` | Superseded by ECS |
| `docker-compose.backend-scaling.yml` | `deploy-archive/docker-compose/` | Superseded by ECS |
| `docker-compose.workers.yml` | `deploy-archive/docker-compose/` | Superseded by ECS |
| `docker-compose.websocket.yml` | `deploy-archive/docker-compose/` | Superseded by ECS |
| `docker-compose.redis-architecture.yml` | `deploy-archive/docker-compose/` | Superseded by ECS |

See [`deploy-archive/README.md`](./deploy-archive/README.md) for the rationale behind each and instructions for revisiting them in the future.

---

## Deployment Runbooks

For operational procedures (rollbacks, incident response, scaling), see:

- **Go/No-Go Checklist**: [`deployment_go_no_go.md`](./deployment_go_no_go.md)
- **Incident Response**: [`INCIDENT_RESPONSE_PLAYBOOK.md`](./INCIDENT_RESPONSE_PLAYBOOK.md)
- **Rollback Guide**: [`PRODUCTION_ROLLBACK_GUIDE.md`](./PRODUCTION_ROLLBACK_GUIDE.md)
- **Launch Day Runbook**: [`LAUNCH_DAY_RUNBOOK.md`](./LAUNCH_DAY_RUNBOOK.md)
- **ECS Deployment Guide**: [`ECS_DEPLOYMENT_GUIDE.md`](./ECS_DEPLOYMENT_GUIDE.md)
- **Terraform Guide**: [`TERRAFORM_GUIDE.md`](./TERRAFORM_GUIDE.md)
