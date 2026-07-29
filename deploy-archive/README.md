# deploy-archive/ — Non-Production Deployment Configurations

> [!IMPORTANT]
> **None of the configurations in this directory are in active production use.**
> The sole canonical production deployment target is **AWS ECS Fargate via Terraform**, documented in the root [DEPLOYMENT.md](../DEPLOYMENT.md).

This directory contains three deployment configurations that were evaluated or used historically but are **not** receiving active production deploys. They are archived here — not deleted — to preserve optionality in case the team later decides to revisit any of these as a deployment target.

---

## Contents

### `k8s/` — Kubernetes Manifests (Archived)
Full Kubernetes manifest set including:
- `namespace.yaml`, `backend-deployment.yaml`, `ingress.yaml`
- `hpa.yaml`, `autoscaler.yaml` — HPA/autoscaler configurations
- `slo-alerts.yaml` — SLO alert rules
- `websocket-server-deployment.yaml`, `backend-hpa-deployment.yaml`

**Status:** Evaluated, not in production use. The HPA autoscaler targets a deployment mechanism that is not in active use (the production autoscaling is handled natively by ECS Fargate task-count scaling configured in `terraform/ecs.tf`).

**To revisit:** Requires a Kubernetes cluster (EKS or similar) and updating image references from the current ECR registry. The manifests have not been kept in sync with recent architecture changes.

---

### `railway/` — Railway Deployment (Archived)
- `railway.json` — Railway platform build/deploy configuration
- `railway_deployment_checklist.md` — Deployment checklist for Railway

**Status:** Evaluated, not in production use. Railway was considered as a zero-ops hosting option but ECS/Fargate was chosen for its alignment with the existing AWS infrastructure (ECR, RDS-compatible Supabase, ElastiCache Redis).

**To revisit:** Railway supports the same `./startup.sh` start command and Dockerfile, so re-activation would mainly require updating secrets via the Railway dashboard.

---

### `docker-compose/` — Non-Local Docker Compose Variants (Archived)
Production/staging compose variants that overlap with what ECS manages:
- `docker-compose.production.yml`
- `docker-compose.staging.yml`
- `docker-compose.backend-scaling.yml`
- `docker-compose.workers.yml`
- `docker-compose.websocket.yml`
- `docker-compose.redis-architecture.yml`

**Status:** These are superseded by ECS Fargate for all production and staging environments. They are retained as a reference for the service topology.

**Note:** The root-level `docker-compose.yml` is **retained in its original location** and is the active, supported local development tool. See [DEPLOYMENT.md](../DEPLOYMENT.md) for local dev instructions.

---

## Removal Timeline

These configurations will be fully deleted (not just archived) if:
1. A deliberate decision is made not to revisit any of these deployment targets, AND
2. The team confirms the archived configs have not been referenced in any production runbook or external documentation.

Proposed review checkpoint: **90 days from 2026-07-29** (i.e., ~2026-10-27).
