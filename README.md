# Algo22 Terminal & Quant Platform

Welcome to the **Algo22 Quant Platform** repository. This is the monorepo for the Algo22 algorithmic trading terminal, a professional-grade environment for designing, testing, and deploying quantitative trading strategies using a node-based strategy builder.

This document provides a high-level architectural overview and the definitive guide to setting up your local development environment.

## 🏗 System Architecture (Post-Consolidation)

The platform recently underwent a major backend consolidation to eliminate duplicated execution engines and multiple FastAPI apps. The system is now driven by a single, canonical backend and a unified frontend application.

The primary components of the system are:

1. **Backend & Execution Engine (`/backend_app`)**
   - A consolidated Python FastAPI application.
   - Responsible for REST APIs, real-time WebSocket communication, and background worker jobs.
   - Contains the unified **DAG execution engine** and trading strategy logic (no longer split across multiple directories).
2. **Frontend Trading Terminal (`/algo22-terminal`)**
   - The user-facing web application, built with React and Vite.
   - A dense, professional-grade trading terminal including a node-graph builder, multi-pane charts, and portfolio monitoring.
   - **Note:** The authenticated terminal is designed explicitly for desktop-width viewports.
3. **Infrastructure & Deployment (`/infra`, `/terraform`)**
   - Contains Terraform configurations, ECS/Fargate deployment runbooks, and AWS architecture scripts.
4. **Monitoring & Telemetry (`/monitoring`)**
   - Houses Prometheus, Grafana, and related observability configurations for keeping tabs on system health and trading latency.
5. **Databases & Caching (via Supabase & Redis)**
   - **Supabase**: Handles Auth, Row-Level Security (RLS), and acts as our core PostgreSQL database.
   - **Redis**: Handles high-speed caching, WebSocket pub/sub, and background job queues.

---

## 🛠 Directory Guide & Deeper Documentation

This repository contains ~10,000 files. Instead of detailing every service here, please consult the component-specific documentation:

- **🚀 Deployment Guide**: [`DEPLOYMENT.md`](./DEPLOYMENT.md) — **Start here** for how this system deploys to production (ECS Fargate) and how to run it locally.
- **Frontend Docs:** [`algo22-terminal/README.md`](./algo22-terminal/README.md)
- **Reporting Engine Docs:** [`reports/README.md`](./reports/README.md)
- **Infrastructure Docs:** [`infra/README.md`](./infra/README.md)

---

## 🚀 Local Development Setup

You can run the stack locally either via **Docker Compose (Recommended)** or **Bare-Metal** (running the processes directly on your host machine). 

### Prerequisites
Before starting, ensure you have:
- [Docker & Docker Compose](https://www.docker.com/products/docker-desktop)
- Node.js (v20+)
- Python (v3.11+)
- A local Supabase instance, or credentials for a cloud Supabase project.

### 1. Environment Configuration

Regardless of how you run the application, you must first configure your environment variables.

1. Copy the example configuration template:
   ```bash
   cp .env.example .env
   ```
2. Open `.env` and fill in the required core values. **The application will refuse to start** if these are missing:
   - `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`
   - `MASTER_ENCRYPTION_KEYS` (Generate one using python: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)
   - `DATABASE_URL`

### 2. Method A: Running via Docker Compose (Recommended)

The easiest way to stand up the entire architecture (Backend, Frontend, Postgres, Redis, Prometheus, Grafana, Nginx) is via Docker Compose.

```bash
# 1. Build and start the entire stack in detached mode
docker compose up -d

# 2. View logs to ensure healthy startup
docker compose logs -f
```
**Accessing the Application:**
- **Frontend Terminal**: [http://localhost:3000](http://localhost:3000)
- **Backend API Docs (Swagger)**: [http://localhost:8000/docs](http://localhost:8000/docs)

### 3. Method B: Running Bare-Metal

If you are actively developing the backend or frontend and prefer immediate hot-reloading without Docker boundaries, you can run them on your host OS. You will still need a Redis instance and PostgreSQL/Supabase database accessible.

**Terminal 1: Start the Backend (FastAPI)**
```bash
# Create and activate a virtual environment
python -m venv venv

# On Windows:
.\venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# 3. Install dependencies
# For Local Development (GPU-enabled PyTorch):
pip install -r requirements.txt

# For Server Deployment (CPU-only PyTorch, lean install):
pip install -r requirements-cpu.txt

# Start the consolidated backend
uvicorn backend_app.main:app --reload --port 8000
```

**Terminal 2: Start the Frontend (Vite/React)**
```bash
# Navigate to the frontend application
cd algo22-terminal

# Install dependencies
npm install

# Start the Vite development server
npm run dev
```

**Accessing the Application:**
- **Frontend Terminal**: [http://localhost:5173](http://localhost:5173) (Default Vite port)
- **Backend API Docs (Swagger)**: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## 🚨 Need Help?
- Please ensure you have read the `1000_USER_ENGINE_READINESS.md` and related architectural artifacts in the root directory if you are modifying the core trading engine.
- **For production deployment procedures**, see [`DEPLOYMENT.md`](./DEPLOYMENT.md) — the canonical, single source of truth for how this system deploys.
- **For archived/non-production deployment targets** (Kubernetes, Railway, etc.), see [`deploy-archive/README.md`](./deploy-archive/README.md).
