# Docker Configuration Audit Report

**Generated:** 2026-06-08  
**Dockerfiles scanned:**
- `Dockerfile` (main, multi-stage)
- `Dockerfile.backend`
- `Dockerfile.simulator`
- `Dockerfile.websocket`
- `docker-compose.yml`
- `docker-compose.backend-scaling.yml`
- `docker-compose.redis-architecture.yml`
- `docker-compose.websocket.yml`
- `docker-compose.workers.yml`

---

## Findings

### MEDIUM — Dockerfile.simulator: No Non-Root User

| Field | Value |
|-------|-------|
| File | `Dockerfile.simulator` |
| Line | All |
| Issue | No `USER` directive present. Container runs as root. |
| Tool | Manual static inspection |

The simulator Dockerfile installs packages and runs the application without creating or switching to a non-root user. All other Dockerfiles (`Dockerfile`, `Dockerfile.backend`, `Dockerfile.websocket`) create a non-root user (`appuser`, `websocket`).

---

### MEDIUM — Dockerfile.simulator: No HEALTHCHECK

| Field | Value |
|-------|-------|
| File | `Dockerfile.simulator` |
| Line | All |
| Issue | No `HEALTHCHECK` directive. Container health cannot be monitored by orchestration layer. |
| Tool | Manual static inspection |

All other Dockerfiles include a `HEALTHCHECK`. The simulator is missing one.

---

### MEDIUM — docker-compose.yml: PostgreSQL Port 5432 Exposed to Host

| Field | Value |
|-------|-------|
| File | `docker-compose.yml` |
| Line | 70 |
| Issue | `ports: - "5432:5432"` exposes the database port on the host. In production, the database should not be accessible from outside the Docker network. |
| Tool | Manual static inspection |

```yaml
# docker-compose.yml line 70
ports:
  - "5432:5432"  # DATABASE PORT EXPOSED TO HOST
```

---

### MEDIUM — docker-compose.yml: Redis Port 6379 Exposed to Host

| Field | Value |
|-------|-------|
| File | `docker-compose.yml` |
| Line | 88 |
| Issue | `ports: - "6379:6379"` exposes Redis on the host interface. Redis has no authentication by default in this configuration. |
| Tool | Manual static inspection |

```yaml
# docker-compose.yml line 88
ports:
  - "6379:6379"  # REDIS PORT EXPOSED TO HOST
```

---

### MEDIUM — docker-compose.yml: Prometheus Port 9090 Exposed Without Authentication

| Field | Value |
|-------|-------|
| File | `docker-compose.yml` |
| Lines | 124–126 |
| Issue | Prometheus admin interface exposed on port 9090 with no authentication. |
| Tool | Manual static inspection |

---

### LOW — Dockerfile (main): pip install Without Hash Pinning

| Field | Value |
|-------|-------|
| File | `Dockerfile` |
| Line | 25–26 |
| Issue | `pip install -r requirements.txt` does not use `--require-hashes`. Supply chain attacks possible via compromised PyPI packages. |
| Tool | Manual static inspection |

---

### LOW — Dockerfile.backend: No Multi-Stage Build

| Field | Value |
|-------|-------|
| File | `Dockerfile.backend` |
| Line | All |
| Issue | Single-stage build. Build tools (`gcc`, `libpq-dev`) remain in the final image, increasing attack surface. |
| Tool | Manual static inspection |

---

### LOW — Dockerfile.simulator: pip install Without Version Pinning

| Field | Value |
|-------|-------|
| File | `Dockerfile.simulator` |
| Lines | 8–12 |
| Issue | Packages installed without pinned versions (`fastapi`, `uvicorn`, `websockets`, `aiohttp`). Reproducibility and security cannot be guaranteed. |
| Tool | Manual static inspection |

```dockerfile
# Dockerfile.simulator lines 8-12
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    websockets \
    aiohttp
```

---

### LOW — docker-compose.yml: Grafana Default Admin Password via Env Variable

| Field | Value |
|-------|-------|
| File | `docker-compose.yml` |
| Line | 134 |
| Issue | `GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD}` — if `GRAFANA_PASSWORD` is not set in the environment, Grafana falls back to default `admin`. |
| Tool | Manual static inspection |

---

### LOW — All Dockerfiles: Base Image Tag `latest` or Specific But Not Digested

| Field | Value |
|-------|-------|
| Files | `docker-compose.yml` (prom/prometheus:latest, grafana/grafana:latest) |
| Issue | Using `latest` tag for Prometheus and Grafana in compose. Images can change without notice, breaking reproducibility. |
| Tool | Manual static inspection |

---

## Positive Controls Observed

| Control | Status |
|---------|--------|
| Non-root user (`Dockerfile`, `Dockerfile.backend`, `Dockerfile.websocket`) | ✅ Present |
| HEALTHCHECK (`Dockerfile`, `Dockerfile.backend`, `Dockerfile.websocket`) | ✅ Present |
| Multi-stage build (`Dockerfile`, `Dockerfile.websocket`) | ✅ Present |
| Resource limits (CPU/memory in docker-compose.yml) | ✅ Present |
| `--no-cache-dir` pip flag | ✅ Present |
| `rm -rf /var/lib/apt/lists/*` after apt-get | ✅ Present |
| Nginx TLS termination configured | ✅ Present |
