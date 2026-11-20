# PERFORMANCE AUDIT

**Scope:** API load, database saturation, Redis failure under load, exchange timeout, worker restart, duplicate
webhooks under concurrency, concurrent purchase, concurrent order, connection exhaustion, WebSocket load.
Required metrics: throughput, p50, p95, p99, error rate, queue latency.
**Date:** 2026-10-07

---

## 1. HEADLINE

```
PERFORMANCE: BLOCKED — TOOLING

No throughput, p50, p95, p99, error-rate or queue-latency figure was measured during this phase.
No number in this document is a measurement. Nothing here is a PASS.
```

This is reported as BLOCKED rather than AMBER or PASS deliberately. The audit brief requires that infrastructure
limitations be classified BLOCKED rather than passed, and performance is the area where a fabricated or
inherited number would be most damaging — a latency table that looks authoritative but was never measured is
worse than an empty one.

---

## 2. WHY IT IS BLOCKED

Partway through this audit the local PowerShell execution channel degraded to the point where it was returning
**stale, out-of-order output from commands issued minutes earlier** and timing out on trivial commands such as
`Get-Date`. Full disclosure in `staging/FINAL_FULL_SYSTEM_AUDIT.md` §3.

Load testing specifically requires the one capability that was lost:

| Requirement | Why it failed |
|---|---|
| Start a server, hold it up, drive traffic at it | Long-running foreground processes could not be reliably supervised |
| Read a latency histogram back at a known point in time | Output arrived detached from the command that produced it, so no figure could be attributed to a run |
| Correlate a measurement with a configuration | Same reason — attribution was not possible |
| Tear down cleanly | Kill commands risked terminating the local PostgreSQL process tree that the rest of the audit's evidence depended on |

That last point is the decisive one. The marketplace isolation matrix, the 19-file PostgreSQL suite, the 12-probe
set, the BUG-056 fix verification and the BUG-057 reproduction all depend on a single local PostgreSQL instance.
Risking it to obtain latency figures that would then be of questionable attribution was not a reasonable trade.

**It is also worth stating that local figures would have had limited value even if obtained.** A p99 measured on
one developer workstation against one PostgreSQL instance, with no Redis running for part of the session, does
not predict production behaviour and must not be presented as if it did.

---

## 3. WHAT INFRASTRUCTURE EXISTS BUT WAS NOT RUN

The repository contains real load and performance tooling. It was located and read, not executed.

| Asset | Purpose | Status |
|---|---|---|
| `tests/load_test_500_users.py` | 500-concurrent-user load scenario | **NOT RUN** |
| `tests/perf/` | performance test directory | **NOT RUN** |
| `tests/chaos_test.py` | chaos scenarios | **NOT RUN** |
| `tests/test_performance_resource_stability.py` | resource stability under load | not run this phase (passed in prior baseline) |
| `tests/test_task_9_6_performance_budgets.py` | per-operation performance budgets | not run this phase (passed in prior baseline) |
| `tests/test_rate_limiter_concurrency.py` | rate-limiter behaviour under concurrency | not run this phase |
| `tests/test_database_scaling.py`, `test_database_pool.py`, `test_database_pool_math.py` | pool sizing and saturation | not run this phase |
| `tests/test_websocket_cluster.py`, `connection-sweep.spec.js` | WebSocket fan-out and connection churn | **NOT RUN** |
| `tests/test_backpressure.py` | backpressure behaviour | not run this phase |
| `tests/test_autoscaler.py`, `test_scaling_metrics.py` | autoscaling signals | not run this phase |
| `tests/test_slo_monitor.py` | SLO monitoring | not run this phase |

Several of these appear in the prior green baseline, so they are known to pass **as functional tests**. Passing a
performance *budget assertion* is not the same as producing a measured latency distribution, and this document
does not conflate the two.

---

## 4. REQUIRED METRICS — ALL UNMEASURED

| Metric | Value | Status |
|---|---|---|
| Throughput (req/s) | — | **BLOCKED — TOOLING** |
| p50 latency | — | **BLOCKED — TOOLING** |
| p95 latency | — | **BLOCKED — TOOLING** |
| p99 latency | — | **BLOCKED — TOOLING** |
| Error rate under load | — | **BLOCKED — TOOLING** |
| Queue latency | — | **BLOCKED — TOOLING** |
| Max concurrent WebSocket connections | — | **BLOCKED — TOOLING** |
| DB connection-pool exhaustion threshold | — | **BLOCKED — TOOLING** |

---

## 5. SCENARIO COVERAGE

| Scenario | Executed? | Note |
|---|---|---|
| API load | **No** | BLOCKED |
| Database saturation | **No** | BLOCKED |
| Redis failure under load | **No** | Fail-closed *logic* verified statically — `staging/FAILURE_INJECTION_AUDIT.md` §3 |
| Exchange timeout | **No** | Error-mapping path verified statically |
| Worker restart | **No** | Claim/lease *logic* verified statically |
| Duplicate webhooks under concurrency | **No** | DB unique constraint confirmed by schema read; `tests/test_webhook_duplicate_delivery_real_redis.py` **skipped (6 skipped, no Redis)** |
| Concurrent purchase | **No** | `UNIQUE (library_id, user_id)` confirmed by schema read |
| Concurrent order | **No** | TOCTOU window identified by code reading — `staging/TRADING_SAFETY_AUDIT.md` §4 |
| Connection exhaustion | **No** | BLOCKED |
| WebSocket load | **No** | BLOCKED |

**None of the "verified statically" entries is a performance result.** They are correctness findings recorded in
their own documents, listed here only so this document is not read as implying those areas are unexamined.

---

## 6. PERFORMANCE-RELEVANT OBSERVATIONS FROM CODE READING

These are *not* measurements. They are structural observations with performance consequences, recorded because
they came up while auditing other areas.

### 6.1 A single test file takes 35 minutes

`tests/test_marketplace_paper_schema_contract.py` was measured at **2129.13 s (35m29s)** for 1129 tests in one
observed complete run. The targeted subset `::test_every_manifest_pair_exists` completes in **37 s**, so the
runtime is concentrated in the file's other, migration-set-wide checks.

This is a **CI-cost and feedback-loop finding**, not a product-latency finding. Its practical effect is that
developers are unlikely to run this file locally, which matters because it is one of the schema-contract guards.
(It is also the test that, by design, cannot see trigger bodies — the structural reason BUG-056 survived it;
see `staging/FINAL_FULL_SYSTEM_AUDIT.md` §12.)

### 6.2 Metrics pipeline exists and is sensibly built

`routers/metrics.py:35-58` serves a single Prometheus exposition, **merging** `prometheus_client`'s default
registry with `backend/metrics.py`'s collector (`:31-32`) rather than exposing two endpoints — the stated
reasoning being that *"a second registry with a second endpoint is what an operator would have to remember to
scrape, and would not."* It returns 500 on failure rather than fabricating a healthy payload (`:40-42`,
`:53-58`).

Feeders: `PrometheusMiddleware` (`main.py:611`) and `MarketplacePaperHttpMetricsMiddleware` (`:613-617`),
recording `marketplace.http.*` / `paper.http.*` per-route latency and error rate for `/api/library/*` and
`/api/paper/*`.

So **the instrumentation needed to answer this document's questions in production already exists**. The gap is
measurement, not observability — with two caveats: the endpoint is **unauthenticated**
(`staging/API_SECURITY_AUDIT.md` §12), and the endpoint label was previously attacker-controllable via
Host-header path poisoning, now fixed by reading `scope["path"]` (`main.py:773-782`).

### 6.3 Rate limiting may be per-process rather than distributed

If `ENV` is unset and `VYOMQUANT_MODE=paper`, the rate limiter resolves to **in-memory** storage
(`core/rate_limit.py:13-16`, with `"paper"` and `"safe"` in `DEV_TEST_ENVS`). Across N pods that means N× the
intended limit. This is a correctness finding (`staging/API_SECURITY_AUDIT.md` §9.1) with a direct capacity
consequence: **the effective rate limit in production is unknown until `ENV` is confirmed.**

### 6.4 Known pathological async pattern, already fixed

`strategies.py:2730-2740` dispatches synchronous backtest work onto the **shared default executor** via
`run_in_executor(None, ...)` for `sync=true` requests. The request model bounds the fan-out that decides how much
compute one request can buy (`initial_capital` ≤ 1e9, `trade_size_pct` ≤ 1.0, `symbols`/`strategies` ≤ 50) and
the endpoint is rate-limited `30/minute`, with `check_backtest_quota` **reserving** a run from the monthly
allowance before enqueue. The model's own docstring records the reasoning: an unbounded request body is an
unbounded amount of shared-executor work.

Noted as a correctly-mitigated hazard, not a finding.

### 6.5 Connection-pool configuration is parameterised, not measured

`test_database_pool_math.py` exists, indicating pool sizing is reasoned about rather than guessed. The actual
saturation threshold under concurrency was not measured.

---

## 7. WHAT A REAL PERFORMANCE PASS WOULD NEED

Recorded so this document is actionable rather than merely abstaining.

1. **A reliable execution environment** — the precondition. Everything else is blocked on it.
2. **A target that is not a developer workstation.** Staging with production-shaped instance sizes, a real Redis,
   and a real connection pool. Local numbers should not be promoted to production expectations
   (the same rule this audit applies to security evidence).
3. **`ENV=production` confirmed on the target**, otherwise the rate limiter and the Redis cache path are not the
   ones production runs (§6.3, `staging/FAILURE_INJECTION_AUDIT.md` §3).
4. **Redis running**, so `tests/test_webhook_duplicate_delivery_real_redis.py` actually executes instead of
   skipping — that is the single highest-value currently-skipped test.
5. **Scrape `/metrics` during the run** rather than instrumenting separately; the per-route latency and error-rate
   series already exist (§6.2).
6. **Two concurrent workers against one tenant** to settle the TOCTOU question in
   `staging/TRADING_SAFETY_AUDIT.md` §4 empirically — that is a correctness test that happens to need
   concurrency, and it is more important than any throughput figure.
7. **Separate the 35-minute schema-contract file** from the fast suite so the feedback loop is usable (§6.1).

---

## 8. VERDICT

```
PERFORMANCE: BLOCKED — TOOLING
```

Not AMBER, not PASS, and no inherited numbers. The system's performance characteristics under load are
**unknown**, and that unknown is a launch consideration in its own right — independent of, and not offset by, the
functional and security findings in the companion documents.
