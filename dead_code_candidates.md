# Itemized Dead-Code Candidates Catalog

This document lists candidate files evaluated during the repository-wide static analysis audit. Each file is classified into one of four categories:
1. `SAFE TO DELETE`
2. `LIKELY SAFE`
3. `REQUIRES MANUAL REVIEW`
4. `DO NOT DELETE`

---

## 1. Category: SAFE TO DELETE

### A. Root One-Off Scratch & Debugging Scripts

#### `fix_all_final.py`
- **Classification**: `SAFE TO DELETE`
- **Reason**: Temporary string-replace script used during early code refactoring.
- **Dependencies**: Standard library (`os`, `sys`, `re`).
- **Imported By**: None.
- **Runtime References**: None (Not referenced in Dockerfile, startup.sh, or CI workflows).
- **Confidence Score**: **100%**

#### `apply_fixes_prod.py`
- **Classification**: `SAFE TO DELETE`
- **Reason**: One-off fix script for production imports used during past debugging.
- **Dependencies**: Standard library.
- **Imported By**: None.
- **Runtime References**: None.
- **Confidence Score**: **100%**

#### `patch_imports.py`
- **Classification**: `SAFE TO DELETE`
- **Reason**: AST patch script executed locally to fix relative imports.
- **Dependencies**: Standard library.
- **Imported By**: None.
- **Runtime References**: None.
- **Confidence Score**: **100%**

#### `fix_docker.py`
- **Classification**: `SAFE TO DELETE`
- **Reason**: Scratch script created during Dockerfile multi-stage build optimization.
- **Dependencies**: Standard library.
- **Imported By**: None.
- **Runtime References**: None.
- **Confidence Score**: **100%**

#### `print_f821.py` & `print_e402.py`
- **Classification**: `SAFE TO DELETE`
- **Reason**: Temporary Flake8 diagnostic helpers.
- **Dependencies**: Standard library.
- **Imported By**: None.
- **Runtime References**: None.
- **Confidence Score**: **100%**

#### `sprint1f_patch.py` & `sprint1f_db_patch.py`
- **Classification**: `SAFE TO DELETE`
- **Reason**: Legacy Sprint 1F database migration script superseded by Alembic migrations in `backend_app/alembic/`.
- **Dependencies**: `sqlalchemy`.
- **Imported By**: None.
- **Runtime References**: None.
- **Confidence Score**: **98%**

#### `test_service_role_2.py` & `test_service_role_3.py`
- **Classification**: `SAFE TO DELETE`
- **Reason**: Duplicate Supabase auth test scripts created during initial setup. Main suite is in `tests/`.
- **Dependencies**: `httpx`, `supabase`.
- **Imported By**: None.
- **Runtime References**: None.
- **Confidence Score**: **95%**

---

### B. Obsolete Duplicate Directories & Temporary Folders

#### `aerora_quant_backend_updated_final1/` (Nested Directory)
- **Classification**: `SAFE TO DELETE`
- **Reason**: Redundant nested copy of earlier codebase structure created during archive extraction. Canonical code resides in `backend_app/`.
- **Dependencies**: Internal legacy references.
- **Imported By**: None (Excluded in `.dockerignore` and CI).
- **Runtime References**: None.
- **Confidence Score**: **100%**

#### `_copilot_integration_tmp/`
- **Classification**: `SAFE TO DELETE`
- **Reason**: Temporary staging directory created during Copilot integration tests.
- **Dependencies**: None.
- **Imported By**: None.
- **Runtime References**: None.
- **Confidence Score**: **100%**

---

## 2. Category: LIKELY SAFE

#### `Dockerfile.simulator`
- **Classification**: `LIKELY SAFE`
- **Reason**: Container definition for mock market simulator. Not used in production ECS cluster `vyomquant-cluster`.
- **Dependencies**: `python:3.11-slim`.
- **Imported By**: None.
- **Runtime References**: None in AWS ECS task definitions.
- **Confidence Score**: **85%**

#### `simulate_startup.py`
- **Classification**: `LIKELY SAFE`
- **Reason**: Local startup sequence simulator. Superseded by `scripts/pre_deployment_validation.py`.
- **Dependencies**: `backend_app.main`.
- **Imported By**: None.
- **Runtime References**: None in production.
- **Confidence Score**: **90%**

---

## 3. Category: REQUIRES MANUAL REVIEW

#### `Dockerfile.tee`
- **Classification**: `REQUIRES MANUAL REVIEW`
- **Reason**: Container definition for Trusted Execution Environment (TEE) enclave worker (`vyomquant-tee` ECR repo). Review if TEE feature is active.
- **Dependencies**: `python:3.11-slim`, TEE runtime.
- **Imported By**: ECR `vyomquant-tee` repository reference.
- **Runtime References**: `vyomquant-tee` ECR repo.
- **Confidence Score**: **75%**

#### `Dockerfile.websocket`
- **Classification**: `REQUIRES MANUAL REVIEW`
- **Reason**: Standalone container definition for dedicated WebSocket server. Currently, WebSocket endpoints are mounted directly in `backend_app/main.py`.
- **Dependencies**: `python:3.11-slim`.
- **Imported By**: None.
- **Runtime References**: Standalone WS deployment option.
- **Confidence Score**: **80%**

#### `Dockerfile.mds`
- **Classification**: `REQUIRES MANUAL REVIEW`
- **Reason**: Container definition for Market Data Service (`vyomquant-mds` ECR repo). Review if standalone MDS service is deployed separately from main API.
- **Dependencies**: `python:3.11-slim`.
- **Imported By**: ECR `vyomquant-mds` repository reference.
- **Runtime References**: `vyomquant-mds` ECR repo.
- **Confidence Score**: **75%**

#### `buildspec.yml`
- **Classification**: `REQUIRES MANUAL REVIEW`
- **Reason**: AWS CodeBuild manifest. Review if AWS CodePipeline/CodeBuild is maintained as a fallback alongside GitHub Actions.
- **Dependencies**: AWS CodeBuild specification.
- **Imported By**: AWS CodePipeline.
- **Runtime References**: AWS CodeBuild.
- **Confidence Score**: **70%**

---

## 4. Category: DO NOT DELETE

#### `backend_app/main.py`
- **Classification**: `DO NOT DELETE`
- **Reason**: Main FastAPI application entrypoint.
- **Dependencies**: All router modules in `backend_app/routers/` and `backend_app/core/`.
- **Imported By**: `gunicorn`, `uvicorn`, `scripts/pre_deployment_validation.py`.
- **Runtime References**: Docker `startup.sh`, ECS task definition.
- **Confidence Score**: **100%**

#### `Dockerfile` & `Dockerfile.backend`
- **Classification**: `DO NOT DELETE`
- **Reason**: Canonical production multi-stage Docker build definitions for ECS Fargate deployment.
- **Dependencies**: `python:3.11-slim`, `backend_app/requirements.txt`.
- **Imported By**: `.github/workflows/02-build.yml`, `.github/workflows/03-deploy.yml`.
- **Runtime References**: Amazon ECR `vyomquant-api`.
- **Confidence Score**: **100%**

#### `scripts/pre_deployment_validation.py`
- **Classification**: `DO NOT DELETE`
- **Reason**: Pre-flight validation engine executed in CI prior to deployment.
- **Dependencies**: `import_audit.py`, `dependency_audit.py`, `docker_validation.py`, `aws_validation.py`.
- **Imported By**: `.github/workflows/03-deploy.yml`.
- **Runtime References**: Pre-deployment validation gate.
- **Confidence Score**: **100%**

#### `scripts/ecs_deploy_and_diagnose.py`
- **Classification**: `DO NOT DELETE`
- **Reason**: Deployment rollout engine, CloudWatch traceback harvest, and self-healing engine.
- **Dependencies**: `boto3`, AWS CLI, ECS API.
- **Imported By**: `.github/workflows/03-deploy.yml`.
- **Runtime References**: Production deployment step.
- **Confidence Score**: **100%**
