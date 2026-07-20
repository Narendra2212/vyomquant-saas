# Aerora Quant Backend — Static Analysis Final Report

**Generated:** 2026-06-08  
**Codebase:** `d:\aerora_quant_backend_updated_final1`

## Tools Run

| Tool | Version | Target | Report |
|------|---------|--------|--------|
| bandit | 1.9.4 | Python (`aerora_quant_backend_updated_final1/`) | `bandit_report.json`, `bandit_report.md` |
| ruff | 0.15.12 | Python (all .py) | `ruff_report.txt` |
| eslint | 9.x | JavaScript/JSX (`algo22-terminal/src/`) | `eslint_report.txt` |
| npm audit | — | Node.js (`algo22-terminal/`) | `dependency_audit.md` |
| pip-audit | 2.10.0 | Python (`requirements.txt`) | `dependency_audit.md` (partial — Python 3.14 incompatibility) |
| gitleaks | 8.21.2 | All files (`--no-git`) | `gitleaks_raw.json`, `secrets_scan.md` |
| Manual inspection | — | Dockerfiles, docker-compose, .env | `docker_audit.md`, `secrets_scan.md` |

---

## CRITICAL Findings

### [SECRETS] Live Supabase Service Role Key Committed to Repository

| Field | Value |
|-------|-------|
| Tool | Manual / gitleaks |
| File | `.env` |
| Line | 5 |
| Finding | `SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...` |
| Detail | Live JWT for project `wrkexcjqnidkdrayhlsi.supabase.co`. Service role key bypasses Row Level Security. Key is in a committed file. |

---

### [SECRETS] Live Supabase JWT Secret Committed

| Field | Value |
|-------|-------|
| Tool | Manual / gitleaks |
| File | `.env` |
| Line | 6 |
| Finding | `SUPABASE_JWT_SECRET=YOUR_SUPABASE_JWT_SECRET` |
| Detail | UUID used to sign all Supabase JWTs. Anyone with this value can forge valid tokens for any user. |

---

### [SECRETS] Master Encryption Key Committed (Vault Root Key)

| Field | Value |
|-------|-------|
| Tool | Manual / gitleaks |
| File | `.env` |
| Line | 13 |
| Finding | `MASTER_ENCRYPTION_KEYS=YOUR_FERNET_ENCRYPTION_KEY` |
| Detail | Fernet key used to encrypt/decrypt all exchange API keys in `exchange_keys.json`. Both the key and the ciphertext are in the same repository. |

---

### [SECRETS] Live Redis Connection String with Password Committed

| Field | Value |
|-------|-------|
| Tool | Manual / gitleaks |
| File | `.env` |
| Line | 21 |
| Finding | `REDIS_URL=redis://default:GX8WtmjRNWSoanmAYpWM8K19PaDKom5s@redis-10512.crce292...` |
| Detail | Cloud Redis instance with embedded password. Full access to queue, cache, and state data. |

---

### [SECRETS] Plaintext User Password in Committed .env

| Field | Value |
|-------|-------|
| Tool | Manual / gitleaks |
| File | `.env` |
| Lines | 9–10, 32–33 |
| Finding | `TEST_USER_EMAIL="your-test-email@example.com"` / `TEST_USER_PASSWORD=os.environ.get("TEST_USER_PASSWORD", "YOUR_TEST_PASSWORD")` |
| Detail | Real email and plaintext password in a committed file, duplicated twice. |

---

### [SECRETS] TLS Private Key Committed to Repository

| Field | Value |
|-------|-------|
| Tool | gitleaks (rule: private-key) |
| File | `nginx/ssl/aerora.key` |
| Line | 1 |
| Finding | RSA/EC private key in source repository |
| Risk | Anyone with repo access can impersonate the server or decrypt TLS traffic |

---

### [SECRETS] JWT Hardcoded in Python Source Files

| Tool | gitleaks (rule: jwt) |
|------|---------------------|

| File | Line | Detail |
|------|------|--------|
| `list_rpcs.py` | 6 | Supabase JWT hardcoded in production utility script |
| `check_supabase_keys.py` | 8 | Supabase JWT hardcoded in production utility script |

---


## HIGH Findings

### [SECURITY] eval() Called on Redis/External Data — Code Injection Risk

| Tool | bandit / grep |
|------|--------------|
| Rule | B307 (Use of possibly insecure function - eval) |

| File | Line | Code |
|------|------|------|
| `aerora_quant_backend_updated_final1/backend/execution_guard.py` | 852 | `order_data = eval(existing.decode())` |
| `aerora_quant_backend_updated_final1/backend/execution_guard.py` | 1192 | `status_data = eval(cb_status.decode())` |
| `aerora_quant_backend_updated_final1/backend/execution_guard.py` | 1298 | `snapshot = eval(snapshot_data.decode())` |
| `aerora_quant_backend_updated_final1/backend/execution_guard.py` | 1359 | `status_data = eval(status.decode())` |
| `aerora_quant_backend_updated_final1/backend/distributed_execution/execution_deduplication.py` | 305 | `state = eval(state_data)` |
| `aerora_quant_backend_updated_final1/backend/exchange_validation/sandbox_execution_runner.py` | 168 | `history.append(eval(record))` |

---

### [SECURITY] Weak MD5 Hash Used in Security Context (B324)

| Tool | bandit |
|------|--------|
| Rule | B324 — Use of weak MD5 hash |

| File | Line | Context |
|------|------|---------|
| `aerora_quant_backend_updated_final1/core/exchange_rate_limit_engine.py` | 469 | `hashlib.md5(str(params).encode()).hexdigest()[:8]` |
| `aerora_quant_backend_updated_final1/execution_safety_layer.py` | 47 | `hashlib.md5(key.encode()).hexdigest()[:16]` |
| `aerora_quant_backend_updated_final1/model_registry.py` | 138 | `hashlib.md5(base.encode()).hexdigest()[:8]` |

---

### [SECURITY] subprocess with shell=True (B602)

| Tool | bandit |
|------|--------|
| Rule | B602 — subprocess call with shell=True |

| File | Line | Code |
|------|------|------|
| `create_bundle.py` | 7 | `return subprocess.check_output(cmd, shell=True, text=True, errors='ignore')` |

---

### [SECURITY] SSL Certificate Verification Disabled (B501)

| Tool | bandit |
|------|--------|
| Rule | B501 |

| File | Line |
|------|------|
| `test_backend_api.py` | 12 |
| `verify_phase2.py` | 32 |
| `verify_supabase_phase1.py` | 36, 70 |

---

### [DEPENDENCY] axios HIGH Vulnerability (GHSA-pjwm-pj3p-43mv)

| Tool | npm audit |
|------|-----------|
| Package | `axios ^1.15.0` |
| Range | `1.0.0 – 1.15.2` |
| File | `algo22-terminal/package.json` |
| Advisory | https://github.com/advisories/GHSA-pjwm-pj3p-43mv |
| Fix | Upgrade to latest |

---

### [DEPENDENCY] react-router HIGH Vulnerability (GHSA-8x6r-g9mw-2r78)

| Tool | npm audit |
|------|-----------|
| Package | `react-router ^7.14.1`, `react-router-dom ^7.14.1` |
| Range | `7.0.0 – 7.14.2` |
| File | `algo22-terminal/package.json` |
| Advisory | https://github.com/advisories/GHSA-8x6r-g9mw-2r78 |
| Fix | Upgrade to latest |

---

### [SECURITY] pickle.loads on Redis Data (Deserialization)

| Tool | grep / bandit |
|------|--------------|

| File | Line | Code |
|------|------|------|
| `aerora_quant_backend_updated_final1/backend/state_persistence.py` | 269 | `df = pickle.loads(pickled)` |

---

## MEDIUM Findings

### [SECURITY] SQL Injection Vectors — String-Based Query Construction (B608)

| Tool | bandit |
|------|--------|
| Rule | B608 — Possible SQL injection |

| File | Lines |
|------|-------|
| `aerora_quant_backend_updated_final1/backend/tenant_rls_validator.py` | 120, 172 |
| `aerora_quant_backend_updated_final1/backend/transactional_execution_manager.py` | 405, 429, 500, 522, 544 |
| `aerora_quant_backend_updated_final1/core/database_scaling.py` | 446, 551, 564 |
| `aerora_quant_backend_updated_final1/core/telemetry_engine.py` | 166, 221, 285 |
| `aerora_quant_backend_updated_final1/routers/analytics.py` | 36 |
| `aerora_quant_backend_updated_final1/routers/risk.py` | 129 |
| `aerora_quant_backend_updated_final1/routers/user.py` | 207 |
| `backend/telemetry_engine.py` | 298, 309, 332, 365, 396, 418, 440, 458, 475 |

---

### [SECURITY] CORS Wildcard Allow All Origins

| Tool | grep |
|------|------|
| File | `aerora_quant_backend_updated_final1/backend/ws_server.py` |
| Line | 280 |
| Code | `allow_origins=["*"],  # Configure for production` |
| Detail | WebSocket server allows connections from any origin. |

---

### [SECURITY] Binding to All Interfaces (B104)

| Tool | bandit |
|------|--------|
| Rule | B104 — Possible binding to all interfaces |

| File | Line |
|------|------|
| `aerora_quant_backend_updated_final1/backend/ws_server.py` | 73 |
| `aerora_quant_backend_updated_final1/core/metrics_exporter.py` | 46 |
| `aerora_quant_backend_updated_final1/strategy_monitor_api.py` | 298 |
| `aerora_quant_backend_updated_final1/strategy_monitor_service.py` | 651 |

---

### [SECURITY] Token Partially Logged to Console (apiClient.js)

| Tool | Manual |
|------|--------|
| File | `algo22-terminal/src/apiClient.js` |
| Line | 621 |
| Code | `console.log("🔐 TOKEN USED:", token.substring(0, 20) + "...")` |
| Detail | JWT token prefix logged on every API request in browser console. |

---

### [DOCKER] PostgreSQL Port Exposed to Host

| Tool | Manual |
|------|--------|
| File | `docker-compose.yml` |
| Line | 70 |
| Finding | `"5432:5432"` — database port accessible on host |

---

### [DOCKER] Redis Port Exposed to Host

| Tool | Manual |
|------|--------|
| File | `docker-compose.yml` |
| Line | 88 |
| Finding | `"6379:6379"` — Redis port accessible on host |

---

### [DOCKER] Simulator Runs as Root

| Tool | Manual |
|------|--------|
| File | `Dockerfile.simulator` |
| Line | — |
| Finding | No `USER` directive. Container runs as root. |

---

### [DEPENDENCY] ws MODERATE Vulnerability (GHSA-58qx-3vcg-4xpx)

| Tool | npm audit |
|------|-----------|
| Package | `ws` (via dev dependency chain) |
| Range | `8.0.0 – 8.20.0` |
| Advisory | https://github.com/advisories/GHSA-58qx-3vcg-4xpx |
| Fix | Upgrade |

---

### [JS] ESLint: `no-undef` Errors (53 errors total)

| Tool | eslint |
|------|--------|
| File | `algo22-terminal/src/App.jsx` |

Representative errors:

| File | Line | Rule | Detail |
|------|------|------|--------|
| `App.jsx` | 85 | no-undef | `navigator` is not defined |
| `App.jsx` | 1269 | no-undef | `performance` is not defined |
| `App.jsx` | 1280, 1286 | no-undef | `requestAnimationFrame` is not defined |
| `App.jsx` | 2322 | no-undef | `alert` is not defined |
| `App.jsx` | 3350 | no-undef | `AbortController` is not defined |
| `store/useWorkspaceStore.js` | 27 | no-undef | `Blob` is not defined |
| `types/api.types.ts` | 20 | parse-error | `Unexpected token type` (TypeScript not supported without parser plugin) |

Total: **53 errors, 217 warnings** across `algo22-terminal/src/`

---

## LOW Findings

### [SECURITY] Requests Without Timeout (B113)

| Tool | bandit |
|------|--------|
| Rule | B113 |

| File | Lines |
|------|-------|
| `aerora_quant_backend_updated_final1/test_system.py` | 31, 42, 60, 78, 111 |
| `test_full_system.py` | 15, 25, 56 |
| `get_schema.py` | 7 |
| `test_pg_meta.py` | 12 |
| `tests/full_system_test.py` | 12, 18, 33 |

---

### [QUALITY] Ruff: Undefined Names (F821) — Runtime Errors

| Tool | ruff |
|------|------|
| Rule | F821 — Undefined name |

| File | Line | Name |
|------|------|------|
| `aerora_quant_backend_updated_final1/api_ws/ws_routes.py` | 81 | `time` |
| `aerora_quant_backend_updated_final1/api_ws/ws_routes.py` | 93 | `time` |
| `aerora_quant_backend_updated_final1/api_ws/ws_routes.py` | 104 | `time` |
| `aerora_quant_backend_updated_final1/api_ws/ws_routes.py` | 117 | `time` |
| `aerora_quant_backend_updated_final1/api_ws/ws_routes.py` | 154 | `os` |
| `aerora_quant_backend_updated_final1/api_ws/ws_routes.py` | 590, 603, 610 | `time` |
| `aerora_quant_backend_updated_final1/api_ws/ws_routes.py` | 601, 606 | `json` |
| `aerora_quant_backend_updated_final1/backend/circuit_breaker.py` | 449, 864 | `threading` |
| `aerora_quant_backend_updated_final1/backend/dag_engine.py` | 505, 516, 901 | `prepared`, `result` |
| `aerora_quant_backend_updated_final1/backend/dag_event_loop.py` | 578, 588, 610, 964 | `time`, `json` |

---

### [QUALITY] Ruff: 1312 Total Errors

| Tool | ruff |
|------|------|
| Total | 1,312 errors |
| Top Rules | F401 (unused imports), F821 (undefined names), F841 (unused variables), E402 (import order), E722 (bare except) |

Full output: [ruff_report.txt](ruff_report.txt)

---

### [DOCKER] Unpinned Base Image Tags (latest)

| Tool | Manual |
|------|--------|
| File | `docker-compose.yml` |
| Lines | 117, 130 |
| Finding | `prom/prometheus:latest`, `grafana/grafana:latest` — mutable tags |

---

### [DOCKER] pip install Without Hash Pinning

| Tool | Manual |
|------|--------|
| File | `Dockerfile` |
| Lines | 25–26 |
| Finding | No `--require-hashes` flag on pip install |

---

## Report File Index

| Report | Path |
|--------|------|
| Bandit JSON | [bandit_report.json](bandit_report.json) |
| Bandit Markdown | [bandit_report.md](bandit_report.md) |
| Ruff | [ruff_report.txt](ruff_report.txt) |
| ESLint | [eslint_report.txt](eslint_report.txt) |
| Dependency Audit | [dependency_audit.md](dependency_audit.md) |
| Secrets Scan | [secrets_scan.md](secrets_scan.md) |
| Docker Audit | [docker_audit.md](docker_audit.md) |
| Gitleaks JSON | [gitleaks_raw.json](gitleaks_raw.json) (scan running) |
