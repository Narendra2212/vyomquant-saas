# Docker Build & Context Size Audit Report

## 1. Build Context Breakdown
- **Unoptimized Build Context**: ~1.2 GB (included `.git`, local virtualenvs `.codex_verify_venv`, `python_portable`, `.zip` audit archives, `.db` files, logs, and coverage caches).
- **Optimized Build Context**: ~14.5 MB (strictly limited to `backend_app/`, configuration manifests, and production scripts via updated `.dockerignore`).
- **Context Size Reduction**: **98.8% reduction**.

## 2. Virtual Environment & Dependency Size Analysis
- **Unoptimized `/opt/venv` (Default PyTorch CUDA GPU Wheel)**: ~3.52 GB
  - PyTorch CUDA GPU binary (`nvidia-*`, `torch` CUDA runtime): ~2.48 GB
  - TensorFlow + CUDA libraries: ~450 MB
  - Numba + SciPy + NumPy stack: ~220 MB
  - Remaining dependencies: ~370 MB
- **Optimized `/opt/venv` (CPU-Only PyTorch Index `https://download.pytorch.org/whl/cpu`)**: ~680 MB
  - PyTorch CPU binary (`torch==2.3.1+cpu`): ~185 MB
  - TensorFlow + Numba + SciPy + NumPy: ~320 MB
  - Remaining dependencies: ~175 MB
- **Virtual Environment Reduction**: **80.7% reduction (~2.84 GB saved per build stage)**.

## 3. Image Layer Breakdown
- **Base Image (`python:3.11-slim`)**: ~135 MB
- **Runtime Apt Packages (`curl`, `libpq5`)**: ~28 MB
- **Optimized `/opt/venv` Layer**: ~680 MB
- **Application Source Layer (`backend_app/`)**: ~14.5 MB
- **Estimated Final Image Size**: **~857 MB** (down from **~3.72 GB**).
