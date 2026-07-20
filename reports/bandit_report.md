# Bandit Security Scan Report

**Tool:** bandit 1.9.4  
**Generated:** 2026-06-08T09:43:10Z  
**Scope:** Production codebase (excluding archive/, audit_package/, claude_audit_package/)

---

## Summary (Production Scope)

| Severity | Count |
|----------|-------|
| HIGH     | 6     |
| MEDIUM   | 50    |
| LOW      | ~870  |

> Full scan total (including archive duplicates): HIGH=22, MEDIUM=319, LOW=3143 across 3484 findings.

---

## HIGH Severity Findings

### B324 — Weak MD5 Hash (Security Context)

| File | Line | Issue |
|------|------|-------|
| `aerora_quant_backend_updated_final1/core/exchange_rate_limit_engine.py` | 469 | Use of weak MD5 hash for security. Consider usedforsecurity=False |
| `aerora_quant_backend_updated_final1/execution_safety_layer.py` | 47 | Use of weak MD5 hash for security. Consider usedforsecurity=False |
| `aerora_quant_backend_updated_final1/model_registry.py` | 138 | Use of weak MD5 hash for security. Consider usedforsecurity=False |

### B602 — subprocess call with shell=True

| File | Line | Issue |
|------|------|-------|
| `create_bundle.py` | 7 | subprocess call with shell=True identified, security issue. |

### B501 — SSL Certificate Verification Disabled

| File | Line | Issue |
|------|------|-------|
| `test_backend_api.py` | 12 | Call to httpx with verify=False disabling SSL certificate checks |
| `verify_phase2.py` | 32 | Call to httpx with verify=False disabling SSL certificate checks |
| `verify_supabase_phase1.py` | 36 | Call to httpx with verify=False disabling SSL certificate checks |
| `verify_supabase_phase1.py` | 70 | Call to httpx with verify=False disabling SSL certificate checks |

---

## MEDIUM Severity Findings (Production Core Files)

### B608 — SQL Injection Vector (String-Based Query Construction)

| File | Line |
|------|------|
| `aerora_quant_backend_updated_final1/backend/tenant_rls_validator.py` | 120 |
| `aerora_quant_backend_updated_final1/backend/tenant_rls_validator.py` | 172 |
| `aerora_quant_backend_updated_final1/backend/transactional_execution_manager.py` | 405 |
| `aerora_quant_backend_updated_final1/backend/transactional_execution_manager.py` | 429 |
| `aerora_quant_backend_updated_final1/backend/transactional_execution_manager.py` | 500 |
| `aerora_quant_backend_updated_final1/backend/transactional_execution_manager.py` | 522 |
| `aerora_quant_backend_updated_final1/backend/transactional_execution_manager.py` | 544 |
| `aerora_quant_backend_updated_final1/core/database_scaling.py` | 446 |
| `aerora_quant_backend_updated_final1/core/database_scaling.py` | 551 |
| `aerora_quant_backend_updated_final1/core/database_scaling.py` | 564 |
| `aerora_quant_backend_updated_final1/core/telemetry_engine.py` | 166 |
| `aerora_quant_backend_updated_final1/core/telemetry_engine.py` | 221 |
| `aerora_quant_backend_updated_final1/core/telemetry_engine.py` | 285 |
| `aerora_quant_backend_updated_final1/routers/analytics.py` | 36 |
| `aerora_quant_backend_updated_final1/routers/risk.py` | 129 |
| `aerora_quant_backend_updated_final1/routers/user.py` | 207 |
| `backend/telemetry_engine.py` | 298, 309, 332, 365, 396, 418, 440, 458, 475 |
| `check_db_keys.py` | 26 |
| `search_db.py` | 22 |

### B104 — Binding to All Network Interfaces (0.0.0.0)

| File | Line |
|------|------|
| `aerora_quant_backend_updated_final1/backend/ws_server.py` | 73 |
| `aerora_quant_backend_updated_final1/core/metrics_exporter.py` | 46 |
| `aerora_quant_backend_updated_final1/strategy_monitor_api.py` | 298 |
| `aerora_quant_backend_updated_final1/strategy_monitor_service.py` | 651 |
| `tests/test_autoscaler.py` | 24, 32, 46 |

### B113 — Requests Without Timeout

| File | Lines |
|------|-------|
| `aerora_quant_backend_updated_final1/test_system.py` | 31, 42, 60, 78, 111 |
| `aerora_quant_backend_updated_final1/tests/staging/test_full_pipeline.py` | 198 |
| `get_schema.py` | 7 |
| `test_full_system.py` | 15, 25, 56 |
| `test_pg_meta.py` | 12 |
| `tests/full_system_test.py` | 12, 18, 33 |

### B301 — eval() on External/Redis Data (Code Injection)

> Detected via direct grep scan (not flagged by bandit as HIGH due to context, but classified here):

| File | Line |
|------|------|
| `aerora_quant_backend_updated_final1/backend/execution_guard.py` | 852 |
| `aerora_quant_backend_updated_final1/backend/execution_guard.py` | 1192 |
| `aerora_quant_backend_updated_final1/backend/execution_guard.py` | 1298 |
| `aerora_quant_backend_updated_final1/backend/execution_guard.py` | 1359 |
| `aerora_quant_backend_updated_final1/backend/distributed_execution/execution_deduplication.py` | 305 |
| `aerora_quant_backend_updated_final1/backend/exchange_validation/sandbox_execution_runner.py` | 168 |

---

## LOW Severity Findings (Representative Sample)

| Rule | Description | Count |
|------|-------------|-------|
| B101 | assert used in production code (assert-used) | Many |
| B105/B106 | Hardcoded password strings (variable names) | Many |
| B110 | try-except-pass pattern | Many |
| B112 | try-except-continue | Many |
| B311 | Standard pseudo-random generators (not cryptographic) | Many |
| B403 | Import of pickle module | Multiple |
| B324 | Weak MD5 (non-security use) | Multiple |

---

*Full machine-readable output: [bandit_report.json](bandit_report.json)*
