# FAILURE INJECTION AUDIT

**Scope:** behaviour under Redis loss, worker crash, lock failure, stale lock, concurrent workers, timeouts,
partial execution, duplicate webhooks, provider failure, database unavailability.
**Date:** 2026-10-07
**Method:** static reading of failure paths, plus the behavioural evidence available from the existing green
test baseline. Active fault injection was **not** performed — see §10.

---

## 1. SUMMARY

| Failure mode | Behaviour | Verdict |
|---|---|---|
| Redis unreachable — distributed locks | returns `None`, callers refuse | **PASS** (fixed defect) |
| Redis unreachable — kill switch | fail-closed local latch + 10s re-probe | **PASS** |
| Redis unreachable — rate limiter | refuses to boot (if `ENV` set) | **PASS / AMBER** |
| Redis unreachable — WS ticket issuance | 503, no unredeemable ticket | **PASS** |
| Kill-switch check throws | blocks execution | **PASS** |
| Equity unreadable | refuses the order | **PASS** |
| Database unreachable at boot | app starts without DB, logs warning | **AMBER** |
| Worker crash mid-task — order funnel | `EXECUTING` row blocks resubmission | **PASS**, no reaper |
| Worker crash mid-task — command queue | **message already ACKed or double-delivered** | **FAIL** |
| Command-queue task failure | **discarded permanently, no DLQ** | **FAIL** |
| Stale Redis Stream pending entries | **never reclaimed** | **FAIL** |
| Duplicate webhook — marketplace | DB unique constraint | **PASS** |
| Duplicate webhook — plan billing | Redis lock only | **AMBER** |
| Duplicate webhook — referral commission | possibly none | **FAIL** |
| TEE lock lease loss | forced stop | **PASS** |
| Exchange timeout / provider failure | structured refusal, no fabricated success | **PASS** |
| Unhandled exception | 500 with no stack trace to client | **PASS** (but logs leak — §6) |
| Active fault injection | not performed | **BLOCKED — TOOLING** |

---

## 2. THE PATTERN WORTH NAMING FIRST

This codebase's failure handling is **good where a dependency errors** and **weak where a dependency lies or
where a queue message fails**. The distinction matters because the single worst historical defect in this
repository was in the second category, and the remaining open ones are too.

A store that returns an error triggers well-designed fail-closed paths. A store that returns a plausible *wrong
answer* — or a queue that silently drops work — defeats all of them, because the fail-closed handling never
fires.

---

## 3. REDIS LOSS — PASS (and the fixed defect is worth recording)

### 3.1 Current behaviour — correct

`core/cache/redis_manager.py:651-720`. `_get_active_cache` returns **`None`** when Redis is unreachable outside
development, gated by `DEV_MODE` and `_mock_cache_is_acceptable()`, with an explicit `logger.error` naming the
consequence. Callers then refuse rather than proceeding unlocked.

`tests/test_redis_fallback_does_not_fake_a_distributed_lock.py:69-80` parametrizes `ENV` over
`production`/`prod`/`staging` with `VYOMQUANT_MODE=paper`, `DEV_MODE=false`, and asserts no mock is returned.

### 3.2 The defect it replaced — the sharpest bug in the repository's history

The old branch read:

```python
if get_vyomquant_mode() != "production":
    return self._mock_client
```

`get_vyomquant_mode()` returns `VYOMQUANT_MODE` (`safe` / `paper` / `live`). `"production"` is a value of the
**separate** `ENV` variable. **The comparison spanned two different variables' value spaces and could therefore
never be false** — `return None` was unreachable, and *every* environment received `MockRedisClient`: a plain
`dict` on a module global, i.e. **per process**.

Consequence: every pod won its own `SET NX`. Both the execution lock and `claim_submission` became **no-ops
across pods while reporting success**. N pods consuming one signal would produce N real orders.

As the test file puts it, this bug **lies successfully** — so the fail-closed handling built for a store that
*errors* never fired. This is the canonical instance of the §2 pattern and the reason API-03
(`staging/API_SECURITY_AUDIT.md` §9.1) matters: the rate limiter still contains the same
`ENV`-vs-`VYOMQUANT_MODE` vocabulary confusion, with `"paper"` and `"safe"` in its dev allow-list.

### 3.3 Not verified

`_mock_cache_is_acceptable()`'s own env logic was **not read**. Given that the original bug was precisely a
mis-read environment variable, that helper deserves direct verification against the deployed task definition.
**NOT VERIFIED.**

### 3.4 Other Redis-dependent paths

| Path | Behaviour on Redis loss | Verdict |
|---|---|---|
| Global kill switch | fail-closed local latch, blocks all execution, re-probes every 10s so a blip does not deadlock the platform (`global_safety.py:160-230`) | **PASS** |
| Rate limiter | `RuntimeError` at import — process will not start (`core/rate_limit.py:47-50`) | **PASS** if `ENV` set; see API-03 |
| WS ticket issuance | 503 rather than issuing an unredeemable ticket (`routers/auth.py:319-336`) | **PASS** |
| Webhook idempotency lock | marketplace falls back to the DB unique constraint; plan billing has no backstop | **PASS / AMBER** |

The kill-switch design is the strongest of these: it distinguishes "Redis is down" (latch, block, retry) from
"the key is absent" (not active), and it does not permanently lock the platform out on a transient failure.

---

## 4. WORKER CRASH AND RETRY

### 4.1 Order funnel — PASS, with no reaper

Two-step, verified by reading:

- `check_idempotent_execution` (`core/models/execution_record.py:272`) is a plain read plus branching — not
  atomic on its own. `COMPLETED` → `skip_return_result`; `EXECUTING` → `skip_already_running`.
- `claim_execution` (`:610-700`) is the atomic gate:
  `UPDATE execution_records SET status='executing' WHERE execution_id=… AND tenant_id=… AND status IN
  ('PENDING','pending') RETURNING *` on PostgreSQL, with a dialect-agnostic rowcount fallback.
  `execution_engine.py:702-710` handles a lost race as `skipped_contention`.

So the row is `EXECUTING` **before** the exchange call and only moves to `COMPLETED`/`FAILED` after. On restart,
the same `execution_id` reads `EXECUTING` and is skipped. **Crash-before-record does not resubmit.** PASS.

Two residual risks:

1. **No reaper for stale `EXECUTING` rows** was found. A crashed order blocks that `execution_id` indefinitely,
   and its true venue state is unknown until `reconciliation_worker` resolves it. *Absence of evidence — not
   exhaustively proven absent.*
2. The `execution_id` is derived from a **bucketed** timestamp (`execution_interval_minutes` default 5), so a
   sufficiently delayed retry produces a new id and sidesteps both the DB dedupe and the venue's
   `clientOrderId` dedupe. See `staging/TRADING_SAFETY_AUDIT.md` §7.2.

### 4.2 FINDING WORK-01 — the command worker loses work (P1)

`backend_app/workers/command_worker.py:94-99`:

```python
except Exception as e:
    logger.exception("Worker failed: %s", e)
finally:
    await redis_manager.xack(stream_name, "command_workers", msg_id)
```

**Three distinct problems from one line.**

**(a) Failed commands are discarded permanently.** The `xack` is in a `finally` attached to a bare
`except Exception` that only logs. A `start_bot` that throws — *including one that threw after partially starting
a bot* — is acknowledged and removed from the stream. No retry, no DLQ, no persisted failure record. The only
trace is a log line.

**(b) The same placement enables double execution.** Because the ACK happens *after* the work and
`fleet.start_bot` is not idempotent, a crash between the work completing and the ACK leaves the entry in the
Pending Entries List for redelivery → **a second bot start**.

(a) and (b) are opposite hazards from the same construction, which is why moving the `xack` alone does not fix
it — the handler needs idempotent work plus an explicit failure destination.

**(c) No PEL reaper.** Grepped `backend_app/` for `XAUTOCLAIM` / `XPENDING`: **no hits.** Entries pending against
a dead consumer are never reclaimed. Compounding it, the consumer name is
`os.getenv("WORKER_NAME", "command_worker_1")` (`:28-30`) — so **a scaled pod set that does not set
`WORKER_NAME` shares one consumer identity**, which breaks the per-consumer PEL ownership the Redis Streams
model depends on.

### 4.3 FINDING WORK-02 — there is no dead letter queue (P1)

Grepped `dead_letter|dlq|DLQ` across `backend_app/**/*.py`: **no implementation.** Only unrelated prose and the
admin endpoint `routers/dag_tasks.py:509` which inspects a dead-letter concept that has no producer.

No DLQ, no retry counter, no failure table — therefore nothing to monitor and nothing to replay. Combined with
§4.2(a), a failing `command_queue` message is **permanently lost with a log line as its only record**.

### 4.4 What the command worker *does* get right

Worth stating, because it shows the fail-closed instinct is present in the same file:

- Subscription is verified before starting a bot, and **`"unknown"` is in the blocked set** —
  `BLOCKED_STATUSES = ("cancelled","expired","suspended","unknown")` (`:62-68`). If the subscription cache is
  unavailable, the bot does not start. That is the correct direction.
- A `start_bot` command carrying **no `paper_trading` key is refused**, not defaulted (`:70-83`). The comment
  records why: reading it with a default *"is exactly the defect that made every live deployment run as paper"*
  (`blueprint.get("paper_trading", True)` in `master_executor`). Refusing means no bot, which is the safe
  direction. Regression: `tests/test_live_deployment_mode_propagation.py`.

So the *authorization* and *mode* failure handling in this worker is good; the *delivery* failure handling is
not.

### 4.5 Order cancellation idempotency — AMBER

`routers/orders.py` accepts an `Idempotency-Key` header (`:812`, `:932`) and uses it as the `execution_id`. The
fallback when absent is:

```python
f"cancel_{tenant_id}_{order_id}_{datetime.utcnow().timestamp()}"      # :895
f"cancelall_{tenant_id}_{timestamp}"                                  # :1018
```

A client that omits the header mints a **new** `execution_id` on every retry, so the idempotency guard cannot
dedupe. Server-side determinism (a hash of tenant + order + operation, with **no** timestamp) would close it.

---

## 5. LOCKS AND LEASES

| Lock | TTL | Renewal | On loss | Verdict |
|---|---|---|---|---|
| TEE consumer lock | `LOCK_TTL_SECONDS = 15` (`tee/main.py:20`), `set(..., nx=True, ex=15)` (`:50`) | `expire` (`:196`) | **forced stop** (`:198-199`); released on refusal (`:109`, `:122`) and graceful shutdown (`:277-279`) | **PASS** — a correct lease |
| Order execution lock | `set(..., nx=True, ex=30)` (`master_executor.py:549-553`) | none | released in `finally` (`:689-691`) | **PASS** for its purpose (per-execution-id dedupe) |
| Kill switch | `ex=86400` on activate | **none** | **expires → trading resumes** | **FAIL** — see `staging/TRADING_SAFETY_AUDIT.md` §5 |
| `command_queue` | **no lease at all** | — | — | **FAIL** — §4.2 |

**Stale in-progress reaping:** `OrderWatchdog` is the one real reaper — `check_interval_seconds=30`,
`stale_threshold_seconds=60`, `alert_threshold_seconds=300` (`main.py:394-401`). It covers stale orders. Nothing
reaps stale Redis Stream pending entries or stale `EXECUTING` execution records.

---

## 6. OBSERVABILITY UNDER FAILURE — the leak is in the failure path

`main.py:829-843`, the catch-all exception handler:

```python
print(f"GLOBAL EXCEPTION HANDLER: {type(exc).__name__}: {exc}")
print(f"TRACEBACK: {traceback.format_exc()}")
traceback.print_exc()
```

The response body is correct — always `"Internal server error"`, no traceback to the client. But these three
calls write to **stdout**, bypassing `logging` entirely and therefore bypassing `RedactingLogFilter`
(`core/observability.py`). Any credential in an exception message, in a `repr`'d frame local, or in a formatted
traceback reaches the container log **unredacted**.

This compounds with the redaction scope gap (`staging/PERFORMANCE_AUDIT.md` is not the right home for this —
see `staging/FINAL_FULL_SYSTEM_AUDIT.md` §10, OBS-02): the filter is attached to only **12 logger names**, all
under `backend/marketplace/` and `backend/paper/` (`observability.py:979`, `GUARDED_LOGGER_NAMES`). Its own
docstring states plainly (`:118-119`): *"Nothing outside those logger names is touched, so `main.py`'s
`basicConfig` and every other module's output are unchanged."*

Unredacted today: `ReferralRouter` (`referral.py:43`), `CommandWorker` (`command_worker.py:12`), `RateLimit`
(`rate_limit.py:10`), `RateLimitMiddleware`, `StrategyEngine`, and every router logger.

And `tests/test_log_redaction.py` walks **only those two packages'** sources for new logger names
(`observability.py:123-125`) — so a new unredacted logger *outside* them is **not a test failure**, it is a
silent hole.

**This is the highest-value observability fix: route the catch-all through a redacted logger and extend the
filter to the root handler.** The filter mechanism itself is well built (two-pass key+value redaction, bounded
walk depth of 12, `filter()` catching `Exception` and replacing message **and args wholesale** with a placeholder
while setting `record.redaction_failed = True` so the line survives but the value cannot) — it is simply pointed
at too little of the application.

---

## 7. DUPLICATE WEBHOOK DELIVERY

| Path | Mechanism | On Redis failure | Verdict |
|---|---|---|---|
| Marketplace settlement | Redis two-phase lock **+** `uq_settlement_reference_reversal UNIQUE (provider_reference, is_reversal)` | **DB constraint still refuses** | **PASS** |
| Marketplace reversal | same unique constraint with `is_reversal=TRUE` | DB refuses | **PASS** |
| Plan-billing entitlement (`_apply_billing_entitlement`) | Redis lock only; write is a **set**, not an increment | replay is idempotent in effect | **AMBER** |
| `increment_ml_addon` | Redis lock only; write is an **increment** | **replay adds another add-on** | **AMBER → FAIL risk** |
| `process_referral_commission` | Redis lock only; the installed function may have **no `payment_id` guard** | **replay double-credits** | **FAIL** |

Detail in `staging/MONEY_FLOW_AUDIT.md` §3.3 and §4.2. The marketplace layering — Redis as a work-reducing first
line, a DB constraint as the actual guarantee — is the correct pattern and is exactly what the plan-billing and
referral paths lack.

### 7.1 Not verified behaviourally

```
WEBHOOK DUPLICATE DELIVERY vs REAL REDIS: BLOCKED — NOT RUN
```

`tests/test_webhook_duplicate_delivery_real_redis.py` reported **6 skipped** in `staging/pg_audit.log` because no
Redis instance was running during the suite. This is the one test that would exercise the two-phase lock against
a real store, and it did not run. **Counted as not-run, not as a pass** — a skip is not a green.

---

## 8. PROVIDER AND EXCHANGE FAILURE — PASS

| Failure | Behaviour |
|---|---|
| Exchange rejects / errors | `_handle_ccxt_error(e)` → `OrderResult(success=False, status="rejected", error_message=…, error_code=…)` (`exchange_executor.py:1114-1131`). Structured refusal, never a fabricated fill. |
| Order below venue minimum | refused **locally** before sending, naming which minimum and by how much (`:925-990`) — rather than consuming the idempotency claim and receiving the venue's opaque rejection |
| Size truncated to zero by precision | `BELOW_PRECISION` refusal (`:947-956`) |
| Checkout provider failure | `CheckoutWriteFailed` → structured `MarketplaceError` (`checkout_service.py:494-497`) |
| Refund in mismatched currency / non-integer amount | refused with **no write** (`billing.py:910-932`) |
| Uncorrelated refund (no matching settlement) | `audit_uncorrelated_refund(...)` rather than a silent drop (`billing.py:901-909`) |
| Entitlement read failure | `EntitlementReadFailed` → **503**, never folded into a false 403/404 |
| Settlement persistence failure | `SettlementPersistenceError` → HTTP 500, logged, provider retries |

The last two are the ones that matter most and both are right: a database problem is never reported to the user
as "you are not entitled", and a failed settlement never answers the webhook with a fabricated 200.

---

## 9. DATABASE UNAVAILABILITY — AMBER

`main.py` wraps `Base.metadata.create_all(bind=engine)` in a try/except that logs
*"Backend will start without database connection"* and continues. Combined with the health-check findings
(`/health/ready` returns a static `{"status": "ready"}`, and `/api/system/readiness` returns hardcoded
`"HEALTHY"` literals — `staging/FINAL_FULL_SYSTEM_AUDIT.md` OBS-01), a pod with **no database** will:

1. start successfully,
2. report ready to the load balancer,
3. receive traffic,
4. and report `"database": "HEALTHY"` on the dependency endpoint.

`/health` itself *is* real (it probes Redis, QuestDB/telemetry, FleetManager and constructs a Supabase client),
but its `all_ok` computation (`:884-889`) considers only `supabase` and `questdb` — **Redis being down does not
make the service `degraded`**, despite Redis now being load-bearing for distributed locks and idempotency claims
after the §3 fix.

That last point is the one-line fix with the best ratio in this document.

A good counter-example exists in the same codebase: `workers/marketplace_expiry_worker.py:54-59` writes
`status = "unhealthy"` to `worker:health:marketplace_expiry_worker`, advances `last_run_at` **only on a fully
successful pass**, and ships `interval_seconds` alongside it so a probe does not have to guess what "stale"
means. That is the pattern `/health/services` should adopt — it currently reads `_runtime_service_status`,
populated **once** during lifespan startup (`main.py:470-472`), so a worker that died after boot still reads
`ACTIVE`.

---

## 10. WHAT WAS NOT INJECTED

```
ACTIVE FAULT INJECTION: BLOCKED — TOOLING
```

No fault was actually induced during this phase. The PowerShell execution channel became unreliable (stale,
out-of-order output; timeouts on trivial commands — `staging/FINAL_FULL_SYSTEM_AUDIT.md` §3), which made
orchestrating "kill Redis, observe behaviour, restore" unsafe: the primary risk was killing the local PostgreSQL
process tree that the rest of the audit's evidence depended on.

Not performed, and therefore **not claimed**:

| Scenario | Status |
|---|---|
| Stop Redis, observe lock refusal end-to-end | **BLOCKED — TOOLING** |
| Kill a worker mid-task, observe redelivery | **BLOCKED — TOOLING** |
| Replay a duplicate webhook against real Redis | **BLOCKED — NOT RUN** (test skipped, §7.1) |
| Exchange timeout simulation | **BLOCKED — TOOLING** |
| Connection-pool exhaustion | **BLOCKED — TOOLING** |
| Concurrent-purchase race, executed | **BLOCKED — TOOLING** (DB constraint verified statically + by schema read) |
| Concurrent-order TOCTOU, executed | **BLOCKED — TOOLING** (window identified by reading) |

What *is* behavioural evidence, from the green baseline: `tests/test_chaos_recovery.py`,
`tests/test_crash_recovery/`, `tests/test_redis_fallback_does_not_fake_a_distributed_lock.py`,
`tests/test_reconciliation_engine_fail_closed.py`, `tests/test_account_freeze_fail_closed.py`,
`tests/test_idempotent_submission_fails_closed.py`, `tests/test_rate_limiter_fails_closed_in_production.py` all
passed as part of the suite. Their concrete assertions were not read, so they are recorded as **suite-green**
rather than as independent confirmation.

---

## 11. FAILURE INJECTION VERDICT

```
FAILURE BEHAVIOUR: AMBER (static) / BLOCKED — TOOLING (dynamic)
```

Dependency-error handling is consistently fail-closed and, in several places, thoughtfully so — the kill switch
distinguishing "down" from "absent", the entitlement resolver refusing to turn a read failure into a false
denial, the venue-minimum check refusing locally rather than burning an idempotency claim, and the command
worker treating `"unknown"` subscription status as blocked.

The gaps are concentrated in **queue delivery** and **health signalling**:

| # | Blocker | Severity |
|---|---|---|
| 1 | No DLQ anywhere; failed command-queue work is permanently discarded | P1 |
| 2 | ACK-in-`finally` also permits double `start_bot` on crash | P1 |
| 3 | No PEL reaper; default shared `WORKER_NAME` breaks PEL ownership when scaled | P1 |
| 4 | Hardcoded `"HEALTHY"` endpoints; `/health/ready` checks nothing; Redis down ≠ degraded | P1 |
| 5 | Kill switch self-expires after 24h | P1 |
| 6 | Catch-all handler prints raw traceback past the redaction pipeline | P2 |
| 7 | Redaction scoped to 12 loggers; a new unredacted logger outside them is not a test failure | P2 |
| 8 | Referral/`increment_ml_addon` replay has no DB backstop | P1 (see MONEY) |
| 9 | Order-cancel idempotency is client-dependent | P2 |
| 10 | No stale-`EXECUTING` reaper | P2 |
| 11 | `_mock_cache_is_acceptable()` env logic unverified — the exact bug class that caused the worst defect here | P2 |
