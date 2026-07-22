# VyomQuant Production Release Checklist

## Pre-Release Phase
- [x] Run AST & Syntax Audit (`python scripts/import_audit.py backend_app`) — 282/282 files PASS.
- [x] Run Dependency Audit (`python scripts/dependency_audit.py`) — 53/53 packages reconciled.
- [x] Run `pip check` — No broken requirements found.
- [x] Validate Dockerfiles (`python scripts/docker_validation.py`) — Static audit PASS.
- [x] Validate AWS Resources (`python scripts/aws_validation.py`) — STS identity, ECR repos, Secrets Manager (6/6 keys), ECS Cluster & Service PASS.
- [x] Run Pre-Deployment Validation Engine (`python scripts/pre_deployment_validation.py`) — PASS.
- [x] Verify GitHub Workflows — 5 consolidated workflows (`01` to `05`) active.

---

## Deployment Execution Phase
- [ ] Push commit to `main` branch to trigger workflow `.github/workflows/02-build.yml`.
- [ ] Monitor ECR container build & image digest generation.
- [ ] Confirm automatic trigger of `.github/workflows/03-deploy.yml`.
- [ ] Observe ECS Fargate task definition registration and service update on `vyomquant-cluster`.
- [ ] Confirm ECS service stability (`services-stable`).

---

## Post-Deployment Phase
- [ ] Execute Live Post-Deployment Probe (`python scripts/post_deployment_validation.py --base-url https://api.vyomquant.com`).
- [ ] Verify `GET /health` returns `status: ok`.
- [ ] Verify `GET /health/live` returns `status: alive`.
- [ ] Verify `GET /health/ready` returns `status: ready`.
- [ ] Verify `GET /metrics` returns Prometheus metrics stream.
- [ ] Inspect CloudWatch Log Group `/ecs/vyomquant-api` for clean startup logs.
- [ ] Confirm GitHub Artifacts created (`deployment-reports`, `security-scan-reports`).
