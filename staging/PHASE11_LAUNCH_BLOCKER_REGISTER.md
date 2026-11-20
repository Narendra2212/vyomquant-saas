# PHASE 11 — LAUNCH BLOCKER REGISTER

**Companion to:** [`PHASE11_LIVE_TRADING_READINESS_AUDIT.md`](PHASE11_LIVE_TRADING_READINESS_AUDIT.md)
**HEAD:** `074b772ab16cece62cbfca0f2b78497be2ae56d5` (`main`), working tree dirty (249 paths)
**Date:** 2026-10-10
**Live trading:** OFF / NOT CERTIFIED throughout

---

## How to read this register

- **Class** distinguishes four genuinely different things, because they need different
  responses: `DEFECT` (code is wrong), `MISSING` (never built), `UNVERIFIED` (built,
  fails closed by inspection, zero executed evidence), `PRODUCT` (a decision, not a bug).
- **Blocks** states which launch stage each item gates. An item that blocks only full
  public live trading should not hold a paper launch.
- **Effort** is engineer-days with an explicit uncertainty band. Where I could not bound
  it from inspection, it says so rather than inventing a figure.
- **Parallel** says whether the item can proceed concurrently with the others, given the
  shared-file ownership map in §7.1 of the audit.
- An `UNVERIFIED` item is **not** a claim that the control is broken. It is a claim that
  nobody has observed it working, which for a money-moving control is its own blocker.

Severity counts: **critical 10 · high 19 · medium 11 · low 2.**

---

# CRITICAL

---

## P11-B01 — No venue has ever received an order

| | |
|---|---|
| **Severity** | CRITICAL |
| **Class** | MISSING (evidence) |
| **Owner** | Stream A — Exchange engineering |
| **Blocks** | restricted live pilot · full public live trading |
| **Parallel** | yes, after credentials land |
| **Effort** | **5-8 engineer-days** after credentials (±3 d; the lifecycle suite exists, so this is execution and triage, not construction). Credential procurement itself is external and unbounded. |

**Path / configuration**
`tests/exchange_sandbox/` (16 gated tests) · `staging/EXCHANGE_SANDBOX_CERTIFICATION.md` §1, §3.4, §4.2 · credential names `AERORA_SANDBOX_<VENUE>_API_KEY` / `_SECRET` / `_PASSPHRASE`

**Evidence**
`staging/EXCHANGE_SANDBOX_CERTIFICATION.md` §1:
`SANDBOX ORDERS PLACED: 0`, `EXCHANGE SANDBOX CERTIFICATION: NOT ACHIEVED — NO SANDBOX
CREDENTIAL EXISTS`. §2.5 lists five preconditions, **all five unmet**. I reproduced the
consequence: `pytest tests/test_exch01_sandbox_endpoint_verification.py` → 28 passed, and
that file's own header states *"They are not venue sandbox certification: no venue is
contacted, no credential is used, and passing them certifies nothing about any exchange."*

**Reproducer**
```powershell
# Shows the gate and the itemised skips rather than a false green
$env:AERORA_EXCHANGE_SANDBOX_CERT=1
python -m pytest tests/exchange_sandbox/ -q -rs
# expect: 3 failed (reproductions, red by design), 8 passed, 16 skipped
```

**Smallest safe action**
Obtain and fund **Binance and Bybit testnet** credentials only. Execute the 12-scenario
suite in the order the certification report recommends, **scenario 8 first**. Do not
widen `SANDBOX_MECHANISMS` or `SUPPORTED_EXCHANGES`.

**Dependencies** — none technical. External procurement is the critical path.

**Acceptance criteria**
1. Scenarios 1, 2, 3, 4, 5a, 6a, 6b, 6c, 8, 9b, 10, 11 pass on Binance testnet **and**
   Bybit testnet, with logs committed to `staging/`.
2. 5b, 7 and 12 are either executed or carry a written, reviewed statement of why the
   limitation is accepted (the report already drafts all three).
3. The capability registry records `TRADING_CERTIFIED` for exactly those two venues, from
   a connectivity artifact rather than by hand.
4. `registry.tradable()` remains `[]` — certification is not authorization.

---

## P11-B02 — `clientOrderId` venue mapping is unverified, and it is the only venue-side duplicate defence

| | |
|---|---|
| **Severity** | CRITICAL |
| **Class** | DEFECT (unproven mechanism on the money path) |
| **Owner** | Stream A — Exchange engineering |
| **Blocks** | restricted live pilot · full public live trading |
| **Parallel** | no — gates P11-B05 and the chaos matrix |
| **Effort** | **1 engineer-day** to prove per venue; **+3-5 engineer-days** if a venue ignores the field and a per-venue param mapping plus acknowledgement check is required (±2 d) |

**Path / configuration**
`backend_app/backend/exchange_executor.py:1618-1623`
```python
if self._consumed_execution_id:
    extra_params["clientOrderId"] = self._consumed_execution_id
```

**Evidence**
CCXT does **not** unify this field: Binance expects `newClientOrderId`, Bybit
`orderLinkId`, OKX `clientOrderId`. Nothing in the repository verifies the mapping or that
the venue echoed the id back — the response handler at `:1655-1680` reads only
`response["id"]`, `filled`, `remaining`, `average`. `staging/EXCHANGE_SANDBOX_CERTIFICATION.md`
§4.2 names this explicitly:

> *"Scenario 8 is the one to run first. The platform's only protection against a retry
> becoming a second real order is that `execution_id` is forwarded as `clientOrderId` and
> that the venue enforces uniqueness on it… If a venue silently ignores the field, every
> retry path above this layer doubles a position."*

The retry paths above it are real: `idempotent_exchange_submission.py:448-470` deliberately
**keeps** its claim on an exception (correct), but that claim carries a **24-hour TTL** and
then expires; `master_executor`'s Redis lock is **30 seconds**; and
`MockRedisClient` is a per-process dict, so in any environment where the real-Redis
substitution is active the cross-pod lock is not a lock at all (P11-U04).

**Reproducer** — requires a testnet credential
```
1. Submit a limit order far from market with clientOrderId = X.
2. Submit a byte-identical order with the same clientOrderId = X.
3. REQUIRED: the venue rejects #2 as a duplicate.
   If the venue accepts #2, the defect is confirmed and two real orders now rest.
4. Repeat per venue. Assert the venue echoes X back in the order object.
```

**Smallest safe action**
Add a per-venue `clientOrderId` parameter-name map keyed off `exchange_id`, and assert the
venue echoed the id back in the `create_order` response. Fail the order closed if it did
not — an un-echoed id means the platform has no handle for recovery.

**Dependencies** — P11-B01 (credentials)

**Acceptance criteria**
1. A duplicate submission with an identical `clientOrderId` is rejected by the venue, for
   each venue in the initial allowlist, with the rejection recorded in `staging/`.
2. The venue's response object contains the id the platform sent, asserted by test.
3. An order whose id is **not** echoed is refused rather than recorded as placed.
4. Regression test added that fails if a new venue is added to `SUPPORTED_EXCHANGES`
   without a mapping entry.

---

## P11-B03 — Daily-loss and max-drawdown limits are not enforced at the order gateway

| | |
|---|---|
| **Severity** | CRITICAL |
| **Class** | MISSING |
| **Owner** | Stream B — Risk engineering |
| **Blocks** | restricted live pilot · full public live trading |
| **Parallel** | yes — Stream B owns `core/execution_engine.py` exclusively |
| **Effort** | **3-4 engineer-days** (±1 d) |

**Path / configuration**
`backend_app/core/execution_engine.py:1340-1360` — the only risk requirement at the
gateway is `self._live_risk_approved is not True → refuse`, a caller-supplied boolean.
`backend_app/core/risk_engine.py:273-302` — `check_daily_loss`, `check_drawdown`,
`can_trade` exist and are **not called** by the gateway.
`backend_app/backend/execution_guard.py` — describes itself as *"the FINAL CONTROL LAYER
that validates EVERY trade before execution"* and is called only from the two **cancel**
endpoints.

**Evidence**
The code states the gap itself, at `execution_engine.py:1225-1244`:

> *"It is NOT a substitute for the risk engine. It will not catch an order that is merely
> too large for the strategy's configured risk-per-trade, breaches the daily loss limit,
> or exceeds the open-position count. Those remain the caller's responsibility, and wiring
> `ExecutionGuard` into this funnel is recorded as an outstanding item rather than
> attempted here."*

Two secondary findings in the same area:
- `check_daily_loss` compares against **`initial_capital`**, not current equity, so the
  limit does not tighten as the account draws down.
- `check_drawdown` returns `(True, "OK")` when `peak_equity == 0` — an uninitialised peak
  **permits** trading.

**Reproducer**
```powershell
# Shows that only a boolean is required, and that it is the whole risk gate
python -m pytest tests/test_trade04_live_order_gate_at_venue_boundary.py -q -k risk
# Then read execution_engine.py:1340-1360 — no RiskEngine construction, no can_trade() call
```

**Smallest safe action**
Wire `ExecutionGuard` / `RiskEngine.can_trade()` into `_execute_trade_internal` **beside**
the existing `_live_risk_approved` check, not instead of it. Keep the boolean (it catches
a caller that skipped evaluation entirely) and add the gateway-owned evaluation (it catches
a caller that evaluated wrongly). Fix the `initial_capital` denominator and the
`peak_equity == 0` fail-open in the same change.

**Dependencies** — none. **Do not** start this concurrently with P11-B04 in a separate
session; both edit `_execute_trade_internal`.

**Acceptance criteria**
1. An order that breaches the daily loss limit is refused **at the gateway**, with
   `risk_approved=True` supplied — proving the gateway does not trust the caller.
2. Same for max drawdown and open-position count.
3. `check_daily_loss` measures against current equity; the regression test pins the
   denominator.
4. `peak_equity == 0` refuses rather than permitting.
5. `risk` in `REQUIRED_CHECKS` is signed only after the gateway's own evaluation ran.

---

## P11-B04 — Authoritative equity is an in-process number never reconciled against the venue

| | |
|---|---|
| **Severity** | CRITICAL |
| **Class** | MISSING |
| **Owner** | Stream B — Risk engineering |
| **Blocks** | restricted live pilot · full public live trading |
| **Parallel** | no — serialise after P11-B03 (same function) |
| **Effort** | **4-6 engineer-days** (±2 d — requires a reviewed staleness policy, which is a risk decision, not only code) |

**Path / configuration**
`backend_app/core/execution_engine.py:423-545` `_capacity_bounds_for` reads
`getattr(self, "current_equity", None)` · `:410-421` `_sync_equity` computes
`cash + position_market_value()` · the value is seeded from `portfolio_state` by the caller
· `master_executor._recover_live_state` seeds the paper engine with a synthetic
`{"total_equity": 10000.0}`

**Evidence**
Both capital gates — the per-order ceiling at `:1296-1330` and the cross-order reservation
at `:962` — derive from the same helper, which is good design, and that helper reads an
**in-process** figure. The cross-order reservation reads in-flight notional from the
database (authoritative), but the **ceiling** it is compared against is not. **Nothing on
the authorization path reads an exchange balance.**

Related, and confirmed by grep returning zero matches across `core/risk_manager.py`,
`core/global_safety.py`, `core/pricing_service.py` and `core/portfolio_engine.py`: there is
**no staleness check anywhere** on the authorization path. The code concedes this at
`execution_engine.py:477-483`:

> *"Measured: NO price oracle, ticker or mark-price service is consulted by ANY capacity
> or notional check in this repository… nothing defines which price, how stale is too
> stale, or what to do when market data is unavailable."*

Also: `RiskEngine.update_equity` advances `peak_equity`, so **any capital inflow routed
through it would raise the peak and loosen the drawdown limit**. I found no caller that
does this, but nothing structurally prevents it.

**Reproducer**
```python
# Read-only demonstration that the ceiling is caller-supplied
# tests/test_live_orders_are_bounded_by_account_equity.py::_live_engine
#   seeds portfolio_state={"total_equity": equity} — no venue call anywhere in the fixture
python -m pytest tests/test_live_orders_are_bounded_by_account_equity.py -q  # 101-batch, passes
```

**Smallest safe action**
Before minting the authorisation, fetch the venue balance (already available via
`BaseExchangeExecutor.get_balance`), and refuse if it is absent, older than a declared
bound, or disagrees with `current_equity` beyond a declared tolerance. Write the staleness
bound and the tolerance into a reviewed risk policy document first — inventing them inside
a capital check is exactly the pattern `_capacity_bounds_for` already declines to do for
market-order valuation.

**Dependencies** — P11-B01 (a venue balance read must be certified). Serialise after
P11-B03.

**Acceptance criteria**
1. A live order is refused when the venue balance cannot be read.
2. Refused when the balance snapshot is older than the declared bound.
3. Refused when `current_equity` and the venue balance differ beyond tolerance, with both
   figures named in the refusal.
4. The staleness bound and tolerance exist in a reviewed document, not only in code.
5. `peak_equity` cannot be advanced by a capital inflow — pinned by test.

---

## P11-B05 — Restart recovery does not rediscover resting venue orders

| | |
|---|---|
| **Severity** | CRITICAL |
| **Class** | MISSING |
| **Owner** | Stream C — Execution engineering |
| **Blocks** | restricted live pilot · full public live trading |
| **Parallel** | yes — Stream C owns `order_watchdog.py` and `_recover_live_state` |
| **Effort** | **3-5 engineer-days** (±2 d) |

**Path / configuration**
`backend_app/backend/master_executor.py` `_recover_live_state` (re-queries **balance
only**) · `backend_app/backend/exchange_executor.py` `find_order_by_client_order_id`
(exists, used only by the watchdog) · `fetch_open_orders` is **called by nothing on the
recovery path**

**Evidence**
`staging/EXCHANGE_SANDBOX_CERTIFICATION.md` §4.3 (EXCH-07):

> *"Does restart recovery rediscover resting orders? `_recover_live_state` re-queries
> balance only. A sandbox can prove the venue would answer `fetch_open_orders`; nothing
> calls it."*

Compounding this, `master_executor.py` ~675-690 mutates in-process
`self.recovered_exposure_coins` after the gateway returns, and **risk sizing consumes that
figure**. It is memory-only and is lost on restart. So after a worker restart the platform
has neither the resting orders nor the exposure they represent, and will size the next
order as though the account were flat.

`OrderWatchdog` partially covers this — it IS started (`main.py:432`) and does adjudicate
`EXECUTING` rows — but its population is rows the platform already knows about. An order
the venue holds that has no local row at all is invisible to it.

**Reproducer** — requires a testnet credential
```
1. Place a resting limit order far from market.
2. SIGKILL the worker.
3. Restart. REQUIRED: the platform rediscovers the resting order and its exposure.
   Today: it does not, and the next signal sizes as though flat.
```

**Smallest safe action**
Call `fetch_open_orders(symbol)` in `_recover_live_state` and rebuild
`recovered_exposure_coins` from the venue's answer plus the position state, rather than
from memory. Refuse to start the bot if the venue cannot be queried — an unestablished
exposure is indistinguishable from a large one.

**Dependencies** — P11-B01, P11-B02 (recovery matches by `clientOrderId`)

**Acceptance criteria**
1. After `SIGKILL` and restart, every resting venue order is present in local state.
2. `recovered_exposure_coins` is rebuilt from venue state, not memory, and is correct.
3. A bot whose venue cannot be queried **refuses to start**.
4. No duplicate order is produced by the first post-restart signal.

---

## P11-B06 — Health checks cannot remove a dependency-broken task from rotation

| | |
|---|---|
| **Severity** | CRITICAL |
| **Class** | DEFECT (configuration) |
| **Owner** | Stream E — SRE |
| **Blocks** | **paper launch** · restricted live pilot · full public live trading |
| **Parallel** | yes |
| **Effort** | **0.5 engineer-days** (±0.25 d) — the cheapest critical fix in this register |

**Path / configuration**
`terraform/alb.tf` — `health_check { path = "/health", matcher = "200", interval = 30,
timeout = 5, healthy_threshold = 3, unhealthy_threshold = 3 }`
`healthcheck.sh` — `curl -f http://localhost:8000/health/live || exit 1`
`backend_app/main.py:988` `@app.get("/health")`
`backend_app/main.py:1143` `@app.get("/health/ready")` — the correct endpoint

**Evidence**
I read all three. `/health` returns a **dict**, never a status code:

```python
return {
    "status": "ok" if all_ok else "degraded",
    "mode": ...,
    "services": service_status,
}
```

FastAPI serialises that as **HTTP 200 unconditionally**. The ALB matcher is `"200"`, so a
task whose database is unreachable stays `HEALTHY` and keeps receiving traffic. `/health`
also **never probes the database** — it probes redis, questdb, supabase and fleet only.

The container `HEALTHCHECK` targets `/health/live`, which `readiness.py`'s own tests
assert *consults no dependency* (there is an AST assertion to that effect). So neither
probe can fail for a dependency outage.

Meanwhile `/health/ready` is correct and well tested — `readiness.py` treats `UNKNOWN` as
blocking, uses a 3 s timeout, issues a real `SELECT 1`, converts a falsey Redis ping into a
raised failure, redacts detail, and returns `JSONResponse(status_code=report.http_status)`
i.e. **503**. `tests/test_obs01_readiness_reflects_dependencies.py` passed in my run and
its own comment calls the 503-not-200 assertion *"THE MOST IMPORTANT ASSERTION IN THIS
FILE"*.

**The right behaviour is already built. It is wired to nothing.**

**Reproducer**
```powershell
Select-String -Path terraform\alb.tf -Pattern 'path\s*=|matcher'   # "/health", "200"
Get-Content healthcheck.sh | Select-String health                   # /health/live
# then read backend_app/main.py:988-1062 — no status_code anywhere in the return
```

**Smallest safe action**
Point the ALB target-group `health_check.path` at `/health/ready` and `healthcheck.sh` at
`/health/ready`. Set the ECS health-check grace period above the 3 s probe timeout plus
startup. Leave `/health` and `/health/live` in place for diagnostics.

**Dependencies** — none

**Acceptance criteria**
1. ALB target group probes `/health/ready`; verified by `aws elbv2 describe-target-groups`.
2. With the database unreachable, the target becomes `unhealthy` and is drained.
3. `/health/live` still returns 200 during a dependency outage (liveness ≠ readiness).
4. Grace period documented and tolerates a cold start.

---

## P11-B07 — No alarms, no alerting, no backup drill

| | |
|---|---|
| **Severity** | CRITICAL |
| **Class** | MISSING |
| **Owner** | Stream E — SRE |
| **Blocks** | **paper launch** · restricted live pilot · full public live trading |
| **Parallel** | yes |
| **Effort** | **4-6 engineer-days** (±2 d), of which the restore drill is 1-2 and is the part that can fail and extend |

**Path / configuration**
`terraform/*.tf` · `DATABASE_PROVISIONING_RUNBOOK.md` status table ·
`backend_app/backend/observability/sentry_config.py`

**Evidence**
Measured, not estimated. A grep across `terraform/*.tf` for
`aws_cloudwatch_metric_alarm|aws_sns_topic|aws_cloudwatch_log_metric_filter|aws_config_|aws_backup_|aws_db_snapshot|aws_securityhub`
returned:

```
alerting/backup resource matches: 0
```

`DATABASE_PROVISIONING_RUNBOOK.md` on rollback:

> *"Rollback has not been rehearsed. `downgrade()` exists on the new revisions and two of
> them deliberately refuse rather than destroy data, but **no restore-from-backup drill
> has been performed**." — status: **NOT DONE***

Runbook search returned only `docs/history/INCIDENT_RESPONSE_PLAYBOOK.md`,
`docs/history/LAUNCH_DAY_RUNBOOK.md`, `docs/history/OPERATIONS_RUNBOOK.md` — all under
`history/` — plus `DATABASE_PROVISIONING_RUNBOOK.md` and
`docs/KEY_ROTATION_PROCEDURE.md`. **There is no current kill-switch or incident-response
runbook.**

`04-nightly-audit.yml` runs import, dependency, size, reachability, pip-audit, npm-audit
and cargo-audit with `continue-on-error: true` on every one. It reports; it does not gate.

Also: `main.py:436` swallows an `OrderWatchdog` start failure into
`service_status["order_watchdog"] = "INACTIVE"` plus a warning. The app serves traffic with
no stale-order recovery, and with no alarm nothing notices.

**Reproducer**
```powershell
$pat='aws_cloudwatch_metric_alarm|aws_sns_topic|aws_cloudwatch_log_metric_filter|aws_config_|aws_backup_|aws_db_snapshot|aws_securityhub'
(Select-String -Path 'terraform\*.tf' -Pattern $pat | Measure-Object).Count   # 0
```

**Smallest safe action**
Minimum viable set, in this order:
1. Readiness 503 rate · order-path error rate · count of `UNKNOWN` executions ·
   kill-switch state transitions · worker heartbeat staleness · DB connection saturation.
2. SNS topic to an on-call channel.
3. Automated RDS/Supabase backups enabled; **perform one restore drill and record
   RTO/RPO**.
4. Make the `OrderWatchdog` start failure alarm rather than warn.
5. Write the kill-switch runbook: who may halt, how (`POST /api/admin/kill-all`,
   `get_operator_user`), how to verify (`GET /api/admin/kill-switch` is the authoritative
   read), how to cancel resting orders during a halt (`cancel_order` is deliberately
   un-gated), how to resume.

**Dependencies** — P11-B06 should land first so the readiness alarm has a real signal.

**Acceptance criteria**
1. Each of the six metrics has an alarm with a tested notification path.
2. A restore from backup completed, with measured RTO and RPO recorded in the runbook.
3. The kill-switch runbook exists, outside `docs/history/`, and has been walked through
   once by someone who was not its author.
4. `OrderWatchdog` failing to start pages on-call.

---

## P11-B08 — No test gate between a push to `main` and production

| | |
|---|---|
| **Severity** | CRITICAL |
| **Class** | DEFECT (pipeline) |
| **Owner** | Stream E — SRE |
| **Blocks** | **paper launch** · restricted live pilot · full public live trading |
| **Parallel** | yes |
| **Effort** | **0.5-1 engineer-day** (±0.5 d) |

**Path / configuration**
`.github/workflows/01-pr-check.yml:4-5` — `on: pull_request` **only**
`.github/workflows/02-build.yml:19-20` — `on: push: branches: [main, master]`
`.github/workflows/03-deploy.yml:5-9` — `on: workflow_run: workflows: ["02 Build"]`
`scripts/pre_deployment_validation.py`

**Evidence**
I read the trigger blocks. Every test job lives in `01-pr-check.yml`: `unit-tests` (line
123), `paper-mode-tests` (251), `database-tests` (349), `provisioning-and-isolation` (533),
`docker-check` (638), `frontend-tests` (663) — and that workflow runs **only on
`pull_request`**.

`scripts/pre_deployment_validation.py` runs five steps — AST import audit, dependency
reconciliation, FastAPI startup simulation, docker build probe, AWS task-definition
validation — and **never invokes pytest**. Its `test_fastapi_startup` is a local helper,
not a test run.

So the gates between a commit and the live ECS service are: build succeeded, `05 Security`
passed for the SHA (**CRITICAL severity only**; HIGH and below do not block), five
non-pytest validation steps, and an ECR artifact-exists check. **A direct push to `main`
with a red test suite deploys to production.**

Whether GitHub branch protection closes this is **not determinable from the repository** —
it requires `GET /repos/{owner}/{repo}/branches/main/protection`.

What *is* well built here and should not be disturbed: `require_security_scan.py` is
properly fail-closed (refuses on missing token, missing run, HTTP 403, non-success,
`cancelled`, `skipped`), and the deploy `concurrency` group with `cancel-in-progress:
false` is documented against a real incident where two runs 99 s apart reset each other's
ECS rollout.

**Reproducer**
```powershell
Select-String -Path .github\workflows\01-pr-check.yml -Pattern '^on:' -Context 0,3
Select-String -Path .github\workflows\02-build.yml   -Pattern '^on:' -Context 0,3
Select-String -Path .github\workflows\03-deploy.yml  -Pattern 'workflow_run' -Context 0,4
Select-String -Path scripts\pre_deployment_validation.py -Pattern 'pytest'   # no match
```

**Smallest safe action**
Two changes, both small:
1. Enable branch protection on `main` requiring `01 PR Check` to pass, and disallowing
   direct pushes.
2. Add a `required-tests` job to `03-deploy.yml`'s `pre-deployment-validation` that
   verifies `01 PR Check` concluded `success` for the deploying SHA — reusing the exact
   fail-closed pattern `require_security_scan.py` already implements for `05 Security`.

**Dependencies** — P11-D01 (the collection error) must be fixed first, or the new gate
blocks every deploy immediately.

**Acceptance criteria**
1. A direct push to `main` is rejected by branch protection.
2. A deploy of a SHA whose `01 PR Check` did not pass is refused, with the refusal logged.
3. Refusal is fail-closed on an API error, matching `require_security_scan.py`.
4. The override path, if any, is explicit, logged, and requires a human dispatch.

---

## P11-B09 — RLS is inert on all nine execution tables; no tenant context on any request path

| | |
|---|---|
| **Severity** | CRITICAL |
| **Class** | DEFECT (architecture) |
| **Owner** | Stream F — Security engineering |
| **Blocks** | full public live trading · multi-tenant restricted pilot (**not** an internal-accounts-only pilot) |
| **Parallel** | yes — Stream F owns `tenant_db_context.py` and the migrations |
| **Effort** | **6-9 engineer-days** (±3 d; the type divergences are the uncertain part) |

**Path / configuration**
`backend_app/core/tenant_db_context.py:66-76` — `SELECT set_config('app.current_tenant_id',
:tenant_id, true)`, correct and transaction-scoped
`backend_app/core/dependencies.py` — `get_db` / `get_db_context` set **nothing**
`docs/saas-architecture/tenant-isolation-matrix.md` gaps 1, 2, 5, 6, 8
`staging/AUTH_RLS_AUDIT.md` §11.1, §12

**Evidence**
Two independent facts combine into one hole.

1. **RLS is `ENABLE` without `FORCE`, and the app connects as the table owner.** The
   isolation matrix records the connection role as `postgres`, which owns the tables
   created by the migrations. `staging/AUTH_RLS_AUDIT.md` §11.1 reports
   `FORCE ROW LEVEL SECURITY` grepped across **all three** migration trees with **zero
   matches**, and confirmed on the live instance as
   `relrowsecurity = true, relforcerowsecurity = false`. §12 states the consequence:
   *"RLS is never consulted for any of these queries, regardless of how correct the
   `WHERE` clause is."*
2. **Nothing sets the tenant GUC on a request path.** `tenant_db_context.py` is well
   designed — parameterised, transaction-scoped because pgbouncer `pool_mode=transaction`
   would leak a session-scoped setting to the next tenant, refusing a non-transactional
   session, canonicalising to lower-case UUID — and its own docstring records that nothing
   on a request path calls it. `RLS_TENANT_ISOLATION_VERIFICATION.md` §3.2 describes it as
   *"sound, and inert"*, and §9.4 notes **9 policies depend on a GUC nothing sets**.

So isolation on the execution tables is application `WHERE` clauses only. The matrix is
candid: a missing or wrong filter in any future raw-SQL addition would not be caught by the
database.

Three things make forcing RLS non-trivial rather than a one-line change: 7 of 9 policies
compare `varchar(36)` against `text`; `positions.tenant_id` is `PG_UUID` in the ORM and
`String(36)` in the rebuild migration, with the live type **unverified**; and
`payment_methods` has **no `tenant_id` column at all**.

What does work, and should be stated: RLS **binds** on the PostgREST path
(`core/dependencies.py` anon-key clients execute as `authenticated`, a non-owner role),
idempotency is tenant-scoped via a composite PK `(idempotency_key, tenant_id)`, and the
local sweep measured 79 tables with **0 leaked and 0 unmeasured** across 29 passing tests.

**Reproducer**
```powershell
# the negative grep is the finding
Select-String -Path migrations\*.sql,backend_app\migrations\*.sql,backend_app\alembic\versions\*.py `
  -Pattern 'FORCE ROW LEVEL SECURITY'        # zero matches
# and the GUC has no request-path caller
Select-String -Path backend_app\core\dependencies.py -Pattern 'current_tenant_id'  # none
```

**Smallest safe action**
Strict ordering — reversing it takes production down:
1. Wire `tenant_db_context` into `get_db` so `app.current_tenant_id` is set per request.
2. Resolve the `positions.tenant_id` type divergence and the `varchar(36)` vs `text`
   comparisons; force each restored policy to type-check via `EXPLAIN`, which
   `RLS_TENANT_ISOLATION_VERIFICATION.md` §9.2 already establishes as the standard.
3. **Only then** enable `FORCE ROW LEVEL SECURITY`, behind a feature flag, with a verified
   rollback, on staging first.
4. Add `tenant_id` to `payment_methods`, or document why it is control-plane-only.
5. Add the missing write policy on `referrals`.

**Dependencies** — P11-U01, U02, U05, U06, U07 (the PostgreSQL lane must run first; forcing
RLS without it means shipping blind)

**Acceptance criteria**
1. Every request-scoped DB session carries `app.current_tenant_id`, asserted by test.
2. `relforcerowsecurity = true` on all nine execution tables.
3. The cross-tenant sweep is green **with RLS forced**: 0 leaked, 0 unmeasured.
4. Rollback exercised on staging and documented.
5. A deliberately removed `WHERE tenant_id` filter is caught by the **database**, proven
   by restoring the defect and watching the test go red.

---

## P11-B10 — Production schema has never been verified against the repository

| | |
|---|---|
| **Severity** | CRITICAL |
| **Class** | UNVERIFIED |
| **Owner** | Stream D — Platform / Stream F — Security |
| **Blocks** | restricted live pilot · full public live trading |
| **Parallel** | no — needs a change window and a fresh backup |
| **Effort** | **1-2 engineer-days** to run and triage (±1 d); **unbounded** if material drift is found |

**Path / configuration**
`scripts/verify_production_schema.py` (read-only) ·
`scripts/verify_production_security.py` (writes, rolls back) ·
`DATABASE_PROVISIONING_RUNBOOK.md` §7

**Evidence**
The runbook's own status table:

> *"No verification has been run against **production**. Everything here is a staging
> database built from this repository. Production may differ, and a dashboard-applied
> change leaves no trace in these files." — status: **NOT VERIFIED***
>
> *"The **deploy** workflow does not provision the real database. `03-deploy.yml` invokes
> nothing." — status: **DECISION NEEDED***

`RLS_TENANT_ISOLATION_VERIFICATION.md` says the same thing three times (§1, §8.6, §9.5),
including that `.env` in this workspace holds a sqlite URL, a localhost Supabase URL and
the literal `"dummy_key"` — so no production DSN exists here.

This matters concretely: eleven migrations (022-032) are **untracked** in the working tree,
including 026 (`fee_records` RLS), 028 (safety-control audit) and 030 (`venue_order_id`,
the TRADE-12 fix). Whether production has them is unknown. The platform also has **three**
migration systems with no documented ordering, which the isolation matrix names as the
likely root cause of several findings.

The tooling is genuinely good and should be trusted: the read-only checker sets
`default_transaction_read_only` **and reads it back**, aborting if the server does not
confirm; it refuses port 6543 because session settings do not survive transaction pooling;
it reads catalogues only plus `COUNT(*)`; and it reduces the DSN to scheme/host/port/db
before printing anything. `tests/test_schema_drift_checker_is_read_only_postgres.py`
attempts a write through the same session and requires SQLSTATE 25006. The write probe
commits nothing — one transaction, unconditional `ROLLBACK` in a `finally` — and refuses
without an explicit flag, refuses if any live-trading env var is set, and refuses the
pooler.

**Reproducer** — under change control, with a fresh backup, read-only step first
```powershell
python scripts\verify_production_schema.py --dsn "<DSN>" --out staging\production_schema.json
# exit 0/1/2 = GREEN/AMBER/RED
```

**Smallest safe action**
Take a fresh backup. Run the read-only checker. Triage drift. **Only then**, as a separate
deliberate act, run the security probe with its explicit flag.

**Dependencies** — P11-B07 (backups must exist and a restore must have been proven **before**
anything touches production)

**Acceptance criteria**
1. `verify_production_schema.py` exits GREEN against production, or every AMBER/RED item
   has a disposition.
2. `verify_production_security.py` reports zero cross-tenant reads and zero leftover probe
   identities after rollback.
3. The migration state of 022-032 in production is recorded.
4. A decision is recorded on whether `03-deploy.yml` provisions the database.

---

# UNVERIFIED CRITICAL CONTROLS

All seven share one root cause and one fix. **Grouped deliberately** — fixing the lane
fixes all of them at once, and splitting them across owners would waste the shared setup.

| | |
|---|---|
| **Severity** | CRITICAL (as a group) |
| **Class** | UNVERIFIED |
| **Owner** | Stream D — Platform engineering |
| **Blocks** | restricted live pilot · full public live trading |
| **Parallel** | yes, and it should go **first** — it unblocks triage for five other streams |
| **Effort** | **1 engineer-day** to stand the lane up; **3-6 engineer-days** to triage what it finds (±3 d — the failure count is genuinely unknown, because these tests have never run) |

**Root cause**
`tests/conftest.py:13` executes `os.environ["REDIS_URL"] = ""` **unconditionally at
import**, before any test module loads. `tests/real_redis_gate.py` documents the
consequence: CI's healthy `redis:7-alpine` service, mapped and exported as `REDIS_URL`, can
never switch these tests on — the gate reads `''` and skips. The PostgreSQL lane needs the
second deliberate name `AERORA_TEST_DATABASE_URL`, which is **not set in this
environment**.

**Measured in this audit**

```
postgres-gated test files ....... 32   →  632 tests collected, ALL SKIPPED
real-redis-gated test files ......  3   →    9 tests,           ALL SKIPPED
AERORA_TEST_DATABASE_URL ......... unset
AERORA_TEST_REDIS_URL ............ unset
```

Example skip reason, from `tests/test_kill03_authoritative_global_halt.py`:
> *"requires PostgreSQL: set `AERORA_TEST_DATABASE_URL` to a postgres DSN. **A SKIP here is
> NOT a pass** — the durable audit trail is unverified."*

The gating design is exemplary: the Redis gate separates three states — unset → SKIP,
set-but-unreachable → **FAIL** (so a dead CI service cannot look green), reachable → RUN —
and refuses a non-loopback host unless `AERORA_TEST_REDIS_ALLOW_REMOTE=1`, and never echoes
an endpoint into a log. **The design is right and it has never run.**

| ID | Control | Module | Test (skipped) | Consequence if wrong |
|---|---|---|---|---|
| **P11-U01** | Cross-order capacity reservation under per-tenant advisory lock | `execution_record.py:758-1035` | `test_trade01_concurrent_exposure_postgres.py` | Two orders at 60% of the ceiling commit 120% of it — the exact TRADE-01 defect the method was written to close |
| **P11-U02** | `execution_records` idempotency uniqueness constraint | DDL in `alembic/versions/d97ffff9c3bb_execution_schema_rebuild.py` — **I did not read it** | PG lane | Duplicate executions; the whole idempotency scheme rests on this constraint |
| **P11-U03** | Single-bot ownership across pods | `core/bot_ownership.py:131-148` | `test_work03_bot_ownership_claims_postgres.py` | Two bots trading the same pair on one account. **Note the escape hatch**: `claim_bot` grants a synthetic `local-<uuid>` claim when no DSN is configured **and** `local_state_is_acceptable()` — single-owner then rests on a per-process registry |
| **P11-U04** | Cross-process quota and lock behaviour on real Redis | `subscription_dependencies.py:907-1034` | `test_quota01_exchange_slot_is_atomic.py:641,700`, `test_quota01_metered_reservation_is_hard.py:228`, `test_webhook_duplicate_delivery_real_redis.py` ×6 | Quota overshoot; **and `MockRedisClient.eval_lua` ignores the script argument entirely**, so the production Lua idempotency path has never executed — that is precisely how a constant-true `string.sub(...,1,7) ~= 'processing'` survived |
| **P11-U05** | Durable safety-control audit trail | `core/safety_audit.py`, migration 028 | `test_kill03_…` ×6 (`realaudit` marker) | A halt or reset with no durable record of who did it |
| **P11-U06** | `fee_records` RLS (TENANT-02) | migration 026 — **untracked, unapplied** | `test_tenant02_fee_records_rls_postgres.py` | One tenant reads, updates and deletes another's fee rows. Measured pre-fix: `anon` and `authenticated` each held full DML, `policies=0` |
| **P11-U07** | Position lifecycle persistence and `FOR UPDATE` serialization | `core/position_model.py` | `test_position_lifecycle_postgres.py`, `test_position_delta_race_condition_fix.py:72,114`, `test_atomic_order_cancellation_fix.py:113,190,279,302` | Lost or duplicated position state under concurrency |

**Reproducer**
```powershell
python -m pytest (Get-ChildItem tests -Filter '*_postgres.py' -Recurse | % FullName) --collect-only -q
# 632 tests collected  —  and every one skips without AERORA_TEST_DATABASE_URL
```

**Smallest safe action**
Provision a **dedicated, isolated** PostgreSQL 17 and Redis for testing. Do **not** point
either at anything shared or production. Make `conftest.py:13` conditional, exactly as the
`DATABASE_URL` blanking at `:36-43` already is. Export both opt-ins in CI's `unit-tests`
and `database-tests` jobs.

> **Explicit authorization required.** Provisioning infrastructure is outside this audit's
> remit and was deliberately not performed. PostgreSQL 17 is specified to match
> production's reported `server_version 17.6`; local runs have been 16.2 from the
> `pgserver` wheel, and the version gap is itself unverified.

**Acceptance criteria**
1. All 632 PostgreSQL tests execute. Pass, or every failure classified with a disposition.
2. All 9 real-Redis tests execute and pass, including TTL, eviction and failover behaviour.
3. The Lua idempotency script executes against real Redis, not the mock.
4. The uniqueness constraint DDL in `d97ffff9c3bb` is read and pinned by test.
5. CI runs both lanes on every PR; a dead service **fails** rather than skipping.
6. `staging/PHASE11_READINESS_EVIDENCE.json` is regenerated with the real results.

---

# HIGH

---

## P11-H01 — Second venue-write boundary is kept shut only by the absence of an authorisation minter

**Class** DEFECT · **Owner** Stream C · **Blocks** live pilot · **Parallel** yes ·
**Effort** 1-2 d (±1)

`backend_app/backend/transactional_execution_manager.py:1084` `_execute_on_exchange` →
`create_order` at `:1137`, bypassing `place_order` entirely. It **is** gated at `:1111-1123`
by `require_live_authorization`, and refuses unconditionally today because nothing on that
path mints an authorisation. Its only caller `execute_order:836` has no callers; its
construction site is `exchange_websocket_listener.py:343`, in a module nothing starts.

This is a latent bypass, not an open one — but it is one minted authorisation away from
being a second, unaudited order path. **Acceptance:** either delete the path, or route it
through `execute_with_idempotency`; a test asserts exactly one venue-write boundary exists.

## P11-H02 — `execution_environment` defaults to `LIVE` for callers that omit it

**Class** DEFECT · **Owner** Stream B · **Blocks** live pilot · **Parallel** yes ·
**Effort** 3-5 d (±2, 70 call sites)

`core/execution_engine.py:668` — `execution_environment: Any = ExecutionEnvironment.LIVE`.
A caller that says nothing is indistinguishable from one that says LIVE. The docstring
records **70 of 71** funnel call sites omitting it and **65 of 71** engine construction
sites stating no environment. Only `BotRunner._inner_loop` and `execute_trade` pass it.
The default was deliberately not changed to a refusing sentinel because that would break
working paths. **Acceptance:** drive the omitting call sites to zero, then make omission
raise; `tests/test_execution_environment_is_stated_not_defaulted.py` enforces it.

## P11-H03 — No price or position staleness check on the authorization path

**Class** MISSING · **Owner** Stream B · **Blocks** live pilot · **Parallel** no (merge
with P11-B04) · **Effort** folded into B04

Grep across `core/risk_manager.py`, `core/global_safety.py`, `core/pricing_service.py`,
`core/portfolio_engine.py` for `stale|max_age|age_seconds|last_update|data_quality`:
**zero matches**. The only staleness concept in the money path is
`CAPACITY_STALE_AFTER_SECONDS = 300`, which ages out *execution rows*, not prices.
Partly mitigated: market orders (`price <= 0`) are already refused. **Acceptance:** a
reviewed staleness policy exists and an order priced from stale data is refused.

## P11-H04 — No concentration or per-symbol exposure limit on the order path

**Class** MISSING · **Owner** Stream B · **Blocks** full public live trading · **Parallel**
yes · **Effort** 2-3 d (±1)

`position_model.py:746-751` builds `exposure_by_symbol` for **reporting only**. The
cross-order reservation aggregates per tenant across strategies, which is right, but it
does not bound a single symbol. **Acceptance:** a per-symbol notional cap is enforced at
the gateway and pinned by test.

## P11-H05 — Open positions are excluded from the exposure aggregate; stale rows stop counting

**Class** DEFECT · **Owner** Stream B · **Blocks** live pilot · **Parallel** yes ·
**Effort** 2-3 d (±1)

`claim_execution_within_capacity` sums only in-flight `EXECUTING` rows newer than
`CAPACITY_STALE_AFTER_SECONDS = 300`. **Open positions are not in the aggregate**, and a
row stuck `EXECUTING` for over five minutes stops counting against the account while the
position may still be live. Excluding `PENDING` is correct and load-bearing (counting it
causes mutual deadlock), and excluding `COMPLETED` is correct (equity reflects it) — but
only if equity is reconciled, which is P11-B04. **Acceptance:** committed exposure includes
open positions; a stale row escalates rather than silently freeing capacity.

## P11-H06 — WebSocket fill listener is never started, and `handle_fill` drops venue fees

**Class** DEFECT · **Owner** Stream C · **Blocks** live pilot · **Parallel** yes ·
**Effort** 3-5 d (±2)

`exchange_websocket_listener.py` — nothing starts it, confirmed by AST-scanning tests and
`staging/verify_phase10_open_findings.py`. `_fallback_poll` now honestly logs
`NOT IMPLEMENTED` rather than raising a misleading `ImportError` (it referenced a class
name that does not exist, so **the fallback has never once run**). Separately, EXCH-08:
`handle_fill` has no fee field and the listener drops `fee.cost` — the venue will report a
fee correctly and the platform will discard it.
**Acceptance:** either the listener is started under a single owner with
duplicate-worker prevention, or polling is documented as the supported mechanism; a
venue-reported fee reaches `fee_records`.

## P11-H07 — Reconciliation service is never started and never halts trading

**Class** DEFECT · **Owner** Stream C · **Blocks** live pilot · **Parallel** yes ·
**Effort** 3-4 d (±2)

`exchange_reconciliation.py` has a `start_reconciliation_loop:587-633` that nothing calls.
Grep for `kill_switch|halt` in that file: **no matches** — a detected drift updates a DB
row and emits a result; it does not engage `global_safety` and does not gate the order
path. The loop also swallows errors (`await asyncio.sleep(interval)  # Continue after
error`). Fills are compared as `float` with a `1e-9` tolerance at `:514-518`, the one place
a position quantity is compared in binary float.
**Acceptance:** the service runs under one owner; a drift beyond tolerance engages the kill
switch and pages; fill comparison uses `Decimal`.

## P11-H08 — Exposure used for risk sizing is in-process and lost on restart

**Class** DEFECT · **Owner** Stream C · **Blocks** live pilot · **Parallel** no (merge with
P11-B05) · **Effort** folded into B05

`master_executor.py` ~675-690 mutates `self.recovered_exposure_coins` after the gateway
returns, under a 30-second Redis lock. That figure is memory-only, is lost on restart, and
**risk sizing consumes it**.

## P11-H09 — `MarketplaceExpiryWorker` is unhosted: paid access outlives the paid period

**Class** DEFECT · **Owner** Stream E · **Blocks** **paper launch** (billing correctness) ·
**Parallel** yes · **Effort** 1 d (±0.5)

`terraform/ecs.tf` `local.worker_definitions` holds exactly `backtest`, `dag`, `command`.
`tests/test_worker_deployment_coverage.py` carries an `xfail(strict=True)` that **behaved
as expected** in my run, confirming the gap. `design.md` specifies a 30-second sweep to
satisfy Requirement 11.8; the real interval is **never**. The worker's own health design
cannot catch this — it publishes `unhealthy` after three consecutive *failures*, which
requires the process to exist.

`ReconciliationWorker` is the second entry: instantiated as a module global at
`reconciliation_worker.py:779`, `.start()` never called, probably superseded by the TEE's
own `reconciler_loop()`. **Confirm, then delete** — an unhosted reconciler that looks live
is worse than no reconciler.

**Acceptance:** `MarketplaceExpiryWorker` has an ECS entry and expires a subscription past
`period_expiry` within 60 s; `ReconciliationWorker` is deleted or hosted;
`test_known_unhosted_workers_are_still_unhosted` flips to a genuine pass and
`KNOWN_UNHOSTED` is updated.

## P11-H10 — `require_aal2` covers 2 endpoints; not order placement, not the kill switch

**Class** MISSING · **Owner** Stream F · **Blocks** full public live trading · **Parallel**
yes · **Effort** 2-3 d (±1)

`EXPECTED_AAL2_HANDLERS` = `{exchange.store_keys, exchange.delete_connection}`. Confirmed:
`routers/admin.py:185` `POST /kill-all` depends on `get_operator_user` only — the
platform-wide halt is **password-only**. `staging/AUTH_RLS_AUDIT.md` §4 says the gap covers
credential creation *and billing mutation*; only the two exchange endpoints were closed.

The implementation that exists is good: aal2 → 200, aal1 → 403 containing "two-factor",
**missing `aal` claim → 403** (fails closed), top-level `aal` honoured, no session → 401.
**Acceptance:** AAL2 required on `/kill-all`, close-all, order-affecting admin routes and
billing mutation; negative controls keep read routes at aal1.

## P11-H11 — The cache facade fails soft for every quota and lock caller

**Class** DEFECT · **Owner** Stream D · **Blocks** live pilot · **Parallel** yes ·
**Effort** 3-4 d (±2)

`core/cache/redis_manager.py` — `get` → `None` (`:757`), `set` → `False` (`:767`), `delete`
→ `0` (`:777`), `incr` → `0` (`:907`), each swallowing every exception. Callers see a miss,
never an error. This is the documented reason `HardQuotaEnforcer` was deprecated: its
`except` branches were unreachable, so during an outage every count read zero and every
limit admitted.

`_get_active_cache` is now correctly fail-closed (returns `None` outside dev/test, and the
`VYOMQUANT_MODE != "production"` comparison that could never be false is documented as
removed) — but the **proxy methods above it** still convert an outage into a miss.
`_release_slot_lock` cannot distinguish "the TTL fired" from "the cache is unreachable" and
says so in its own docstring.
**Acceptance:** lock and quota callers receive a distinguishable error on cache
unavailability and refuse; cache-only callers may keep failing soft.

## P11-H12 — Slot-lock release is not atomic, and the stated reason is now superseded

**Class** DEFECT · **Owner** Stream D · **Blocks** full public live trading · **Parallel**
yes · **Effort** 1-2 d (±1)

`subscription_dependencies.py:946-1034` is read → compare → delete across three awaits. The
docstring rejects the Lua form as non-portable — and `staging/PHASE10_WSK_QUOTA_REAL_REDIS_REVIEW.md`
§4.2 **corrects that**: `SharedRedisManager.getdel` + `_GETDEL_LUA` (`redis_manager.py:448`,
`:847-905`) already uses Lua safely by detecting stub evaluators. Atomic compare-and-delete
is achievable today. Filed as follow-up D9, not implemented.
**Acceptance:** release is a single atomic compare-and-delete; the superseded rationale is
removed from the docstring.

## P11-H13 — Quota enforcement is advisory cache state, not a database constraint

**Class** MISSING · **Owner** Stream D + schema owner · **Blocks** full public live trading
· **Parallel** no (shared migration) · **Effort** 2-3 d (±1)

`exchange_connection_slot_guard` takes a `SET NX` lock and re-reads inside it, which is
correct in shape. It is **not** a DB constraint: a Redis failover or `maxmemory` eviction
admits two holders. `staging/PHASE10_WSC_QUOTA_CONNECTION_SAFETY.md` §8 records the durable
`exchange_keys` row-count constraint as **not written**, needing a coordinator.
**Acceptance:** a row-count constraint enforces the limit regardless of what any
application process believes.

## P11-H14 — Credential rotation is documented for one secret out of five

**Class** MISSING · **Owner** Stream F · **Blocks** full public live trading · **Parallel**
yes · **Effort** 2-3 d (±1)

`docs/KEY_ROTATION_PROCEDURE.md` is specific and executable for `MASTER_ENCRYPTION_KEYS` —
`MultiFernet` first-encrypts/all-decrypt, 44-character keys, keep the old key until
re-encryption completes, `scripts/rotate_all_keys.py` with a non-zero exit on any failure,
and a suspected-compromise path that includes telling users to rotate exchange-side keys.

**Nothing for** `JWT_SECRET` / `SUPABASE_JWT_SECRET`, `SUPABASE_SERVICE_ROLE_KEY`,
`DATABASE_URL`, or the **static long-lived CI AWS access keys** — which are the
highest-value unrotatable credential in the pipeline (`02-build.yml` and `03-deploy.yml`
both use `secrets.AWS_ACCESS_KEY_ID`, not OIDC). Also **0**
`aws_secretsmanager_secret_rotation` resources against 8 declared secrets.
**Acceptance:** a tested procedure per secret; CI migrated to OIDC; the wildcard
`kms:Decrypt` on the execution role scoped down.

## P11-H15 — Full-suite collection fails; and the suite is order-dependent

**Class** DEFECT · **Owner** Stream D · **Blocks** **paper launch** (CI integrity) ·
**Parallel** yes · **Effort** 0.5 d (±0.25)

Reproduced:
```
ERROR tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py
FileNotFoundError: 'staging\probe_pg_find.py'
!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!
14400 tests collected, 1 error
```
The test reads the probe at **module import**, so collection aborts. `staging/` holds
`probe_pg_activity.py`, `probe_pg_blockers.py`, `probe_pg_ready.py` — `probe_pg_find.py`
was never created. Both the test and `staging/` are **untracked**, and `staging/` is **not
gitignored**, so committing either side of the pair breaks every CI test lane.

Second, independent defect — order dependence:
```
pytest test_api03… test_rate_limiter…   →  52 passed      (alphabetical)
pytest test_rate_limiter… test_api03…   →   1 failed, 51 passed
```
`test_the_module_exposes_the_decision_it_actually_applied` (line 342) fails because the
preceding file re-imports `backend_app.core.rate_limit` under a different environment and
leaves the module global inconsistent. Not a production defect — but it means the
**fail-closed rate-limit wiring assertion can be neutralised by collection order**, which
is the exact property that assertion exists to guarantee.

**Acceptance:** `pytest tests/` collects 14,400 tests with 0 errors; the suite passes under
`-p no:randomly` **and** reversed file order; the probe/test pair is either completed or
both sides removed.

## P11-H16 — OKX is unusable on the pinned ccxt

**Class** DEFECT (upstream) · **Owner** Stream A · **Blocks** OKX only · **Parallel** yes ·
**Effort** 1-2 d to confirm and exclude (±1)

`load_markets` is documented broken on ccxt 4.3.92 at `connection_engine.py:196-250`, and
`staging/EXCHANGE_SANDBOX_CERTIFICATION.md` §2.5 lists it as unmet precondition 5. OKX is
nonetheless in `SUPPORTED_EXCHANGES` and in `SANDBOX_MECHANISMS` (as `HEADER`,
`x-simulated-trading`). **Acceptance:** OKX is excluded from the initial allowlist with the
reason recorded, or the defect is resolved and certified.

## P11-H17 — Per-venue rate limits and `Retry-After` handling do not exist on the live path

**Class** MISSING · **Owner** Stream A · **Blocks** full public live trading · **Parallel**
yes · **Effort** 3-4 d (±2)

`RateLimiter` is a flat per-endpoint limiter (default 10 req/s). The registry *reads* each
venue's `rateLimit` into `rec.rate_limit_ms` and **never applies it**.
`core/exchange_rate_limiter.py` holds the configured per-exchange limits and has **zero
importers**. `RateLimitError` hardcodes `retry_after=60` regardless of what the venue said.
ccxt's own `enableRateLimit: True` is the real backstop.
**Acceptance:** per-venue limits applied from one source of truth; `Retry-After` honoured;
a 429 does not consume an idempotency claim.

## P11-H18 — Referral payout Redis lock fails open

**Class** DEFECT · **Owner** Money/ledger owner · **Blocks** full public live trading ·
**Parallel** yes · **Effort** 1-2 d (±1)

`routers/referral.py:723-727` — `except Exception as lock_err: … lock_acquired = True  #
Proceed if Redis is unavailable`. The real serialisation guard is the CAS at `:764`
(`.gte("approved_balance_usd", amount)`), which is why this has not produced a loss — but
the lock is decorative. Balance arithmetic is also `float` at `:743` and `:761`.
The compensation gap **is** closed: `_compensate_debit` at `:810-837` now runs on both
failure modes.
**Acceptance:** the lock fails closed; balance arithmetic is `Decimal`; the PG-gated
idempotence and compensation tests execute.

## P11-H19 — Two paper engines share one word

**Class** DEFECT · **Owner** Paper/marketplace owner · **Blocks** **paper launch** (if
deploy-in-paper is customer-visible) · **Parallel** yes · **Effort** 4-6 d (±3)

Documented at `master_executor.py:625-648`. `BotRunner(paper_trading=True)` simulates fills
*inside* `ExecutionEngine` against a synthetic `{"total_equity": 10000.0}`, and that module
has **no import edge** to `backend_app/backend/paper/`. Those fills never reach
`paper_accounts` / `paper_orders` / `paper_fills` / `paper_positions`, which is where the
paper dashboard reads from. A trader who deploys in paper mode gets real simulated
executions their paper account does not show. Requirement 13.3 wants this loop's intents
routed to the Paper_Simulator; the missing piece is a `session_id`, which a BotRunner bot
does not have.
**Acceptance:** one paper ledger; a deploy-in-paper fill appears in the paper dashboard.

---

# MEDIUM

| ID | Item | Owner | Effort | Blocks |
|---|---|---|---|---|
| **P11-M01** | `public.orders` writer unidentified (`ORDERS_ROWS=24`, `EXECUTION_RECORDS_WITH_ORDER_ID=0`) — migration 030 measured it and the execution path does not write that table | Stream C | 1-2 d (±1) | live pilot |
| **P11-M02** | Three migration systems with no documented ordering; the isolation matrix names this as the likely root cause of several findings, including a phantom `exchange_credentials` table in `verify_rls.py`'s `CRITICAL_TABLES` | Stream F | 3-5 d (±2) | full public |
| **P11-M03** | Orphaned `core/credential_vault.py` derives one Fernet from `MASTER_ENCRYPTION_KEYS.split(",")[0]`, **discarding every other key** — wiring it up would make the documented rotation destroy all stored credentials. Imported by nothing. **Delete it** | Stream F | 0.5 d (±0.25) | full public |
| **P11-M04** | Connection pool has no TTL or idle eviction; each entry holds a plaintext credential for the process lifetime | Stream D | 1-2 d (±1) | full public |
| **P11-M05** | Plan downgrade does not reclaim already-stored exchange credentials — it blocks only the next add. **PRODUCT decision** | Product | 1-2 d (±1) | full public |
| **P11-M06** | No deposit/withdrawal segregation from trading P&L; `FeeType.WITHDRAWAL`/`DEPOSIT` are annotated non-trading and are summed alongside trading fees | Money owner | 3-4 d (±2) | full public |
| **P11-M07** | `format_symbol_for_ccxt` suffix-matches a hardcoded quote list (`USDT, USDC, USD, BTC, ETH`) with a `clean[:-3]` fallback — EUR/GBP/TRY/BRL/DAI/TUSD split wrong | Stream A | 1-2 d (±1) | symbol scope |
| **P11-M08** | `_handle_ccxt_error` classifies by substring (`"key" in error_str` → `AuthenticationError`), which is brittle across venues. Redaction before classification **is** correct | Stream A | 2-3 d (±1) | full public |
| **P11-M09** | `idempotent_exchange_submission` claim carries a **24-hour TTL** and then expires, so duplicate protection is time-bounded | Stream C | 1 d (±0.5) | live pilot |
| **P11-M10** | Terraform applied by no workflow; state local; drift unknown. `.disabled` copies of alb/cloudfront/ecs/vpc/waf exist beside the live files | Stream E | 2-4 d (±2) | full public |
| **P11-M11** | `_build_jwks_url` falls back to `https://placeholder.supabase.co/...` with a stale comment claiming "HS256 path will still work". Real behaviour is total auth failure — fail-closed, but a missing `SUPABASE_URL` is an outage rather than a degradation | Stream F | 0.5 d (±0.25) | full public |

---

# LOW

| ID | Item | Owner | Effort |
|---|---|---|---|
| **P11-L01** | `04-nightly-audit.yml` sets `continue-on-error: true` on every job except `docker_validation.py` — it reports, it does not gate | Stream E | 0.5 d |
| **P11-L02** | Widespread `datetime.utcnow()` deprecation warnings (`execution_engine.py:1034,1082`, `global_safety.py:472`) and Pydantic V1 `@validator` deprecations across `execution_record.py`, `pydantic_models.py`, `position_model.py`. Noise today, breakage on the next major bump | Platform | 2-3 d |

---

## Appendix A — Execution order and dependency graph

```
P11-U01..U07  (test lane)  ──┬──> P11-B09 (force RLS)
   [GO FIRST — 1 day]        ├──> P11-B10 (production schema)
                             └──> triage input for B03, B04, B05

P11-H15 (collection error) ──> P11-B08 (test gate on deploy)
P11-B06 (health checks)    ──> P11-B07 (alarms + restore drill) ──> P11-B10

P11-B01 (credentials)  ──> P11-B02 (clientOrderId)  ──┬──> P11-B05 (restart recovery)
   [EXTERNAL — DAY 1]         [HARD GATE]             └──> P11-B04 (equity recon)

P11-B03 (risk at gateway) ──> P11-B04   [same function — SERIALISE, do not parallelise]
```

**Cheapest risk reduction, in order:** P11-B06 (0.5 d) → P11-H15 (0.5 d) → P11-B08 (0.5 d)
→ P11-U01..U07 lane setup (1 d) → P11-H09 (1 d). **Three and a half engineer-days** closes
three critical blockers, unblocks seven unverified controls and fixes a live billing
defect.

## Appendix B — Register-wide acceptance gate

The register is discharged for a **restricted live pilot** when:

1. All 10 critical blockers are closed with evidence committed to `staging/`.
2. All 7 unverified critical controls have **executed** and passed.
3. Every HIGH item blocking "restricted live pilot" is closed or has a written, reviewed
   risk acceptance naming an owner and a review date.
4. The chaos matrix (worker `SIGKILL`, Redis down, database down, kill switch engaged mid
   flight) produces **zero duplicate orders and zero orphaned venue orders** across 24 h on
   testnet.
5. `registry.tradable()` returns exactly the approved venues, and
   `LIVE_TRADING_AUTHORIZED` was granted by the release gate rather than set in code.
6. A restore drill has succeeded with recorded RTO/RPO, and the kill switch has been
   rehearsed by someone who did not write the runbook.

**No item in this register was fixed during this audit.** Audit only, as instructed.
