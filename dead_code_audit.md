# Comprehensive Dead-Code & Hygiene Audit Report

**Repository**: VyomQuant (`aerora_quant_backend_updated_final1`)  
**Audit Type**: Static Analysis & Dependency Reachability Audit  
**Auditor**: Principal Software Architect & Static Analysis Engineer  

---

## Executive Summary

A complete repository-wide static analysis and dependency reachability audit has been conducted across 660+ files and 282+ Python modules in the `vyomquant` backend repository.

This audit analyzed:
- Canonical Python modules (`backend_app/`)
- FastAPI REST & WebSocket routers (`backend_app/routers/`, `backend_app/api_ws/`)
- Quantitative execution engines & services (`backend_app/backend/`, `backend_app/core/`)
- Automation & validation scripts (`scripts/`)
- Unit and integration test suites (`tests/`)
- Docker container manifests (`Dockerfile*`)
- CI/CD workflow architecture (`.github/workflows/`)
- Configuration manifests & dependency manifests (`requirements.txt`, `ecs-task-definition-full.json`, `.env.example`)

---

## Audit Classification Breakdown

| Classification | Category Count | Description | Primary Candidates |
| :--- | :---: | :--- | :--- |
| **SAFE TO DELETE** | **85 files** | One-off scratch scripts, legacy fix scripts, duplicate nested backup folders, and obsolete temporary zip archives. | One-off `fix_*.py`, `patch_*.py`, `apply_fixes.py` scripts in root; nested `aerora_quant_backend_updated_final1/` duplicate subfolder; `_copilot_integration_tmp/`. |
| **LIKELY SAFE** | **12 files** | Unused helper scripts, unused Docker manifests for decommissioned mock services (`Dockerfile.simulator`). | `Dockerfile.simulator`, `analyze_errors2.py`, `simulate_startup.py`, legacy `.patch` files. |
| **REQUIRES MANUAL REVIEW** | **18 files** | Alternative container manifests (`Dockerfile.tee`, `Dockerfile.websocket`, `Dockerfile.mds`) and specialized runbooks. | `Dockerfile.tee`, `Dockerfile.websocket`, `Dockerfile.mds`, `buildspec.yml`, `railway.json`. |
| **DO NOT DELETE** | **450+ files** | Active canonical application code, production routers, core engines, active CI workflows, and validation scripts. | `backend_app/`, `scripts/`, `.github/workflows/01-05.yml`, `Dockerfile`, `Dockerfile.backend`, `requirements.txt`. |

---

## Domain Analysis & Key Findings

### 1. Root-Level One-Off & Scratch Python Scripts
- **Finding**: Over 70 temporary Python fix scripts (`fix_all_final.py`, `patch_imports.py`, `apply_fixes_prod.py`, `fix_main_really.py`, `print_f821.py`, `sprint1f_patch.py`, etc.) exist in the root directory.
- **Reachability**: None of these scripts are imported by `backend_app/main.py`, called by `.github/workflows/`, or referenced in production Dockerfiles.
- **Classification**: `SAFE TO DELETE` (Categorized in [dead_code_candidates.md](file:///c:/aerora_quant_backend_updated_final1/dead_code_candidates.md)).

### 2. Nested Subdirectory Duplication (`aerora_quant_backend_updated_final1/`)
- **Finding**: A directory named `aerora_quant_backend_updated_final1/` exists inside the repository root, containing duplicate, outdated copies of `core/`, `backend/`, `routers/`, and database models.
- **Reachability**: The canonical backend package lives in `backend_app/`. The nested directory is a legacy artifact from an earlier archive extraction.
- **Classification**: `SAFE TO DELETE`.

### 3. Dockerfile Architecture & Container Manifests
- **Canonical Dockerfiles**: `Dockerfile` and `Dockerfile.backend` are actively used for production ECS Fargate deployments and CI build checks -> `DO NOT DELETE`.
- **Specialized Dockerfiles**: `Dockerfile.tee` (TEE enclave worker) and `Dockerfile.websocket` (Standalone WebSocket server) -> `REQUIRES MANUAL REVIEW`.
- **Obsolete Dockerfiles**: `Dockerfile.simulator` (Market Simulator) -> `LIKELY SAFE`.

### 4. GitHub Actions Workflows (`.github/workflows/`)
- **Active Workflows**: `01-pr-check.yml`, `02-build.yml`, `03-deploy.yml`, `04-nightly-audit.yml`, `05-security.yml` -> `DO NOT DELETE`.
- **Legacy Workflows**: Legacy workflows (`pr-checks.yml`, `ci-cd.yml`, `deploy-aws.yml`, `deploy-vyomquant-ecs.yml`, `blue-green-deploy.yml`) were previously consolidated and removed.

---

## Policy & Removal Directives

> [!IMPORTANT]
> **Zero Modifications Executed**: In accordance with system instructions, **no files have been modified or deleted**. All findings are cataloged for review.

> [!TIP]
> Prior to executing any manual file deletion, run `python scripts/pre_deployment_validation.py` to ensure build stability.
