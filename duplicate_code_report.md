# Duplicate Code & Implementation Analysis Report

This document details duplicate code structures, legacy file copies, and duplicate configuration manifests identified during the static analysis audit.

---

## 1. Nested Subdirectory Duplication (`aerora_quant_backend_updated_final1/`)

### Summary
The repository contains a nested directory with the exact same name as the repository root: `aerora_quant_backend_updated_final1/aerora_quant_backend_updated_final1/`.

### Duplicated Modules Identified
- `aerora_quant_backend_updated_final1/core/` (Duplicated from `backend_app/core/`)
- `aerora_quant_backend_updated_final1/backend/` (Duplicated from `backend_app/backend/`)
- `aerora_quant_backend_updated_final1/routers/` (Duplicated from `backend_app/routers/`)
- `aerora_quant_backend_updated_final1/models/` (Duplicated from `backend_app/models/`)

### Impact Analysis
- **Canonical Package**: `backend_app/` is the active, imported Python package used in `Dockerfile`, `Dockerfile.backend`, and `.github/workflows/`.
- **Nested Directory Status**: Dead legacy code copy. It is completely excluded in `.dockerignore` to prevent build context inflation.

---

## 2. Dockerfile Manifest Duplication

| Dockerfile Manifest | Target Service | Status | Recommendation |
| :--- | :--- | :---: | :--- |
| `Dockerfile` | Multi-stage main API container | `ACTIVE (Canonical)` | **Keep**. Used for ECS Fargate deployment. |
| `Dockerfile.backend` | Multi-stage backend container | `ACTIVE (Identical)` | **Keep**. Maintained as standardized production image. |
| `Dockerfile.websocket` | Standalone WebSocket server | `STANDALONE` | **Keep for manual review**. Keep if separate WS deployment is needed. |
| `Dockerfile.tee` | TEE Enclave worker container | `SPECIALIZED` | **Keep for manual review**. Keep if TEE enclave service is deployed. |
| `Dockerfile.mds` | Market Data Service container | `SPECIALIZED` | **Keep for manual review**. Keep if MDS is deployed separately. |
| `Dockerfile.simulator` | Mock Market Simulator | `OBSOLETE` | **Candidate for archiving**. Not used in ECS production task definitions. |

---

## 3. Requirement Manifest Synchronization

### Status
- **Root `requirements.txt`**: 53 dependencies pinned.
- **Backend `backend_app/requirements.txt`**: 53 dependencies pinned.

### Synchronization Status
Both requirements files are fully reconciled and synchronized to identical version specifiers, ensuring identical pip behavior regardless of context.

---

## 4. One-Off Debugging Script Duplication

Over 70 root scratch scripts exist (e.g., `fix_final_1.py`, `fix_final_2.py`, `fix_final_3.py`, `fix_final_55.py`, `fix_final_coordinator.py`, `fix_final_final.py`).

### Recommendation
Move obsolete one-off debug scripts into `archive/scratch_scripts/` or remove them to maintain repository cleanliness.
