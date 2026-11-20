# PHASE 11 — FULL CODEBASE LIVE-TRADING LAUNCH READINESS AUDIT

**Repository:** `c:\aerora_quant_backend_updated_final1`
**Branch:** `main`
**HEAD:** `074b772ab16cece62cbfca0f2b78497be2ae56d5` — *"Give the deploy gate permission to read the answer it refuses without"*
**Working tree:** dirty — 249 changed paths, 22 staged files, 98 unstaged files, 2,973 untracked paths
**Audit date:** 2026-10-10
**Mode throughout:** live trading **OFF / NOT CERTIFIED**. No order placed, no venue contacted, no credential read, no production state touched, no migration applied, no commit.

---

## 1. Executive verdict

```
IMPLEMENTATION COMPLETENESS (weighted) ......  70 %
EVIDENCE-BACKED LIVE-LAUNCH READINESS .......  39 %

HARD-GATE VERDICT ............ BLOCKED  (for live cryptocurrency trading)
PAPER-ONLY LAUNCH ............ CONDITIONAL  (4 operational blockers, no venue dependency)
RESTRICTED LIVE PILOT ........ BLOCKED  (10 critical blockers, 7 unverified critical controls)

CONFIRMED CRITICAL BLOCKERS ........... 10
UNVERIFIED CRITICAL CONTROLS ..........  7
SANDBOX ORDERS EVER PLACED ............  0
VENUES TRADING-CERTIFIED ..............  0
VENUES LIVE-TRADING-AUTHORIZED ........  0
```

The single sentence version: **the order path is well engineered and genuinely fails
closed in most of the places that matter, but no order this platform has ever produced
has reached any exchange — not even a sandbox — and the controls that would catch the
consequences of that (equity reconciliation against the venue, restart rediscovery of
resting orders, daily-loss enforcement at the gateway, alerting) are either absent or
unverified.**

### What is genuinely strong

This is not a codebase pretending to be safe. Several controls are better than the
industry norm:

- **There are exactly two functions in `backend_app/` that issue a CCXT venue write**
  (`exchange_executor.place_order:1400` → `create_order:1645`, and
  `transactional_execution_manager._execute_on_exchange:1084` → `create_order:1137`), and
  **both carry `require_live_authorization`**. The gate is inside `place_order` itself, so
  it covers every caller rather than every call site. I traced this and found no path to a
  CCXT write that bypasses it.
- **The live-execution gate is an artefact, not a flag.** `core/live_execution_gate.py`
  requires an HMAC-signed `LiveOrderAuthorization` covering a complete 13-name
  `REQUIRED_CHECKS` set, bound to this exact `(symbol, side, size, exchange_id)`, consumed
  on use. A missing artefact fails closed where a misplaced flag failed open.
- **Fail-closed is the consistent direction.** Equity unreadable → refuse. Price
  non-finite, non-positive, empty or unparseable → refuse. In-flight exposure
  unmeasurable → refuse. Kill switch unreadable → refuse. Risk not evaluated → refuse.
  Sandbox routing unproven → refuse and close the client. Dialect without advisory locks →
  refuse.
- **Several past defects were real, were measured by execution, and are fixed**: the
  executor cache keyed without a user id (one trader's credentials served to another — a
  *funds* leak, not a data leak), the `price or 0` notional that made every market order
  consume zero capacity, the `Decimal + float` TypeError that meant the daily-loss
  accumulator never accumulated, the kill switch whose "stopped" state expired after 24h
  while "permitted" was durable, the HS256 test-token fallback that was live on any
  deployment with `ENV` unset.
- **The documentation is unusually honest.** `staging/EXCHANGE_SANDBOX_CERTIFICATION.md`,
  `docs/saas-architecture/tenant-isolation-matrix.md` and
  `DATABASE_PROVISIONING_RUNBOOK.md` each state plainly what was *not* verified. I found no
  overclaim in them relative to the code. Test files that are gated say "a SKIP here is
  NOT a pass" in their own skip reasons.

### Why it is still BLOCKED

Four themes, in order of severity.

1. **Zero venue evidence.** `0` sandbox orders have been placed, for any venue, ever. No
   sandbox credential exists. The platform's *only* protection against a retry becoming a
   second real order is that `execution_id` is forwarded as `clientOrderId` and the venue
   enforces uniqueness on it — and CCXT does not normalise that field
   (`newClientOrderId` on Binance, `orderLinkId` on Bybit, `clientOrderId` on OKX).
   Nothing verifies the mapping or that any venue echoed the id back. If a venue silently
   ignores the field, every retry path above it doubles a position.
2. **Risk is narrower than it reads.** The gateway owns exactly one capital bound
   (notional ≤ equity × leverage) and *requires evidence* that risk was evaluated — but it
   accepts a caller-supplied boolean. Daily-loss limits, max drawdown and open-position
   counts are computed by `RiskEngine` and **are not enforced on the order path**; the
   code says so at `execution_engine.py:1225-1244`. There is **no price-staleness check
   anywhere** on the authorization path, and the authoritative equity is an in-process
   number never reconciled against the venue.
3. **The verification lane has never run.** 632 tests across 32 PostgreSQL-gated files and
   9+ real-Redis tests are **all skipped** in this environment. That lane contains the
   proofs for the cross-order capacity reservation, the idempotency uniqueness constraint,
   the bot-ownership claim, the durable safety audit, `fee_records` RLS and position
   lifecycle persistence. `tests/conftest.py:13` blanks `REDIS_URL` unconditionally, which
   is why CI's healthy Redis service never switched the real-Redis tests on. **The
   real-Redis gate has never executed.**
4. **Operations is the weakest category by a wide margin.** No test gate stands between a
   push to `main` and production. The ALB health check targets `/health`, which returns
   HTTP 200 unconditionally. There are **zero** CloudWatch alarms, SNS topics, metric
   filters, AWS Config rules or AWS Backup resources in `terraform/`. No restore drill has
   been performed. No current kill-switch or incident-response runbook exists.

---

## 2. Verified repository and deployment baseline

### 2.1 Git state (recorded before any audit action; unchanged after)

| Fact | Value |
|---|---|
| Branch | `main` |
| HEAD | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| HEAD-1 | `6c5660a1` *Construct the interleaving instead of hoping for it* |
| HEAD-2 | `56297a57` *Stop the missing-mock-export defect emerging one CI run at a time* |
| `git status --porcelain` lines | **249** |
| Staged | 22 files, +4,361 / −69 |
| Unstaged | 98 files, +13,971 / −1,414 |
| Untracked paths | 2,973 (includes `staging/`, `.hypothesis/`, `.codex_verify_venv/`, `tests/exchange_sandbox/`, 100+ new test files, 11 new migrations) |

Substantial Phase 10 remediation is **in the working tree and uncommitted**. This matters
for the audit: several defects reported OPEN in the most recent prior scorecard are fixed
only in untracked or unstaged files.

### 2.2 Scale

| Measure | Value |
|---|---|
| Tracked Python files | 984 |
| Tracked Python LOC | 509,043 |
| Test files under `tests/` | 477 |
| Tests collected | **14,400** (with 1 collection error — see §6.1) |
| PostgreSQL-gated test files | 32 (632 tests) |
| Real-Redis-gated test files | 3 (9 tests) |

The ">260K lines" claim understates the repository. It is also not evidence of anything
about safety, and is not used in the scorecard.

### 2.3 Deployed environment (from repository artifacts only)

Verified from `terraform/` and `docs/saas-architecture/tenant-isolation-matrix.md`.
**Not** verified against AWS — no AWS call was made.

| Aspect | Value |
|---|---|
| Cloud / region / account | AWS, `ap-southeast-1`, `273709947018` |
| Compute | ECS Fargate, cluster `vyomquant-cluster` |
| API service | `vyomquant-api-service-cjema2sl`, gunicorn + 2 uvicorn workers |
| Image | ECR `vyomquant-api`, tagged `$GITHUB_SHA` **and** `latest` |
| Hosted workers | exactly 3 — `backtest`, `dag`, `command` (`terraform/ecs.tf` `local.worker_definitions`) |
| Primary datastore | Supabase PostgreSQL via Supavisor pooler :6543, `pool_mode=transaction` |
| DB connection role | `postgres` — the table owner, which is why RLS is inert (§4.6) |
| Cache / queues | ElastiCache Redis (`rediss://`) |
| Secrets | AWS Secrets Manager, `vyomquant/production/*` — 8 secrets, **0 rotation resources** |
| TLS | HTTPS listener, `ELBSecurityPolicy-TLS13-1-2-2021-06`, ACM cert |
| Terraform state | `terraform.tfstate` on disk, **gitignored and untracked** — confirmed, no secret committed |
| Terraform apply | **not performed by any workflow**; deploy is image-tag update only |

**Not verifiable from the repository** (requires read-only AWS evidence): the running task
revision, whether the three worker ECS services are actually running, live `pg_class` RLS
state, the physical type of `positions.tenant_id`, last-rotated dates on the 8 secrets, and
whether branch protection requires `01 PR Check`.

### 2.4 Prior report claims that are now stale

I checked the most recent scorecard (`staging/FINAL_LAUNCH_SCORECARD.md`, dated
2026-10-08) against current code rather than carrying its findings forward.

| Prior claim | Current state | Evidence |
|---|---|---|
| `TRADE-01` OPEN — capital check is read-then-compare with no reservation | **FIXED in code** | `execution_record.py:758-1035` `claim_execution_within_capacity`, `pg_advisory_xact_lock` per tenant, in-flight notional summed from the DB. Verification is PG-gated → **skipped**. |
| `TRADE-02` OPEN — kill switch self-expires after 24h | **FIXED** | `global_safety.py:557` writes the active key with **no TTL**; the asymmetric `ex=86400` is documented as removed at lines 59-68. 36 tests passed. |
| `MONEY-04` OPEN (P0) — Razorpay INR credited as USD, ~83× overstated | **FIXED** | `billing.py:564-780` `_exact_minor_units` / `_provider_major_amount` with a **hardcoded per-handler currency literal** and validation rather than substitution. |
| `TENANT-02` OPEN (P1) — `fee_records` has no RLS, `anon` holds full DML | **MIGRATION WRITTEN, NOT APPLIED** | `backend_app/migrations/026_fee_records_row_level_security.sql` exists and is **untracked**. Production application **NOT VERIFIED**. |
| `PAPER-ONLY: NO-GO` | **Still correct, for different reasons** | Venue evidence still absent; operational blockers in §4.7 are new. |
| `TRADING LIVE: OFF / NOT CERTIFIED` | **Confirmed, unchanged** | `registry.tradable() == []`, `summary()["tradable_count"] == 0`, asserted by two passing tests. |

Four of six prior findings were stale. Carrying them forward unexamined would have
produced a materially wrong report.

---

## 3. Execution-path inventory

Every path that can create or modify a trading order. `[CODE]` = traced in source.
`[TEST]` = asserted by a test I executed. `[ARTIFACT]` = a committed run log.

| # | Entry point | Calling chain | Shared gateway? | Idempotency | Pre-trade risk | Venue reach | Evidence | Remaining concern |
|---|---|---|---|---|---|---|---|---|
| 1 | `master_executor.py:385` `BotRunner._inner_loop` | risk @473 → exec id @566 → Redis `SET NX` 30s @580 → `execute_with_idempotency` @649 | **yes** | deterministic `execution_id` + Redis lock + DB claim | `validate_trade_request` → `RiskVerdict`, passed as `risk_approved` | **yes, the only live producer** | `[TEST]` 101 passed | in-process `recovered_exposure_coins` feeds risk sizing and is **lost on restart** |
| 2 | `workers/command_worker.py:295` `process_iteration` → `_dispatch` @347 | refuses a command with no explicit `paper_trading` key @392-407 → `BotRunner(...)` @413 | via (1) | Redis Streams consumer group; `FAILED` not acked → PEL; `MAX_DELIVERIES` → dead-letter | inherited from (1) | via (1) | `[TEST]` passed | duplicate delivery produces a duplicate **bot**; dedup then rests on `execution_id` + the 30s lock, not on the command layer |
| 3 | `routers/orders.py:695` `POST /execute` | `validate_strategy_id` → **403 `MANUAL_EXECUTION_BLOCKED`** | n/a | n/a | n/a | **no** | `[TEST]` passed | — |
| 4 | `routers/orders.py:725/738/756` `/create`, `/stop-loss`, `/take-profit` | unconditional **403** | n/a | n/a | n/a | **no** | `[TEST]` passed | — |
| 5 | `routers/orders.py:807/928` `/cancel/{id}`, `/cancel-all` | `SafetyMonitor.check_execution_allowed` @825/946 | cancel only | n/a | n/a | yes (cancel) | `[CODE]` | deliberately **not** kill-switch-gated, so an operator can cancel during a halt — correct |
| 6 | `distributed_execution/idempotent_exchange_submission.py` `submit_order_idempotent` | **sealed @338-380**: returns `RISK_EVALUATION_REQUIRED` before building the engine | sealed | Redis `SET NX` 24h, `IdempotencyUnavailable` fail-closed | none — which is why it is sealed | **no** | `[TEST]` passed | the 24h claim TTL **expires**, so protection is time-bounded |
| 7 | `transactional_execution_manager.py:836` `execute_order` → `_execute_on_exchange:1084` | gated @1111-1123 by `require_live_authorization`; **nothing on this path mints one** | bypasses the gateway | none | none | refuses unconditionally | `[CODE]` | a **second venue-write boundary** kept shut only by the absence of an authorisation minter. Its construction site is `exchange_websocket_listener.py:343`, in a module nothing starts. |
| 8 | `routers/paper_trading.py:183` | `PaperTradingService.place_order:1176` → `paper_*` tables | n/a (paper) | partial unique index `uq_paper_order_idem_default` + optimistic `version` → 409 | n/a | **no exchange client** | `[TEST]` 172 passed | — |
| 9 | `backend/dag_engine.py` buy/sell actions | `SafetyMonitor.check_execution_allowed("strategy_signal")` @1526, @4765 | separate guard | — | — | no | `[CODE]` | a different guard from the one the live path uses |
| 10 | `backend/order_watchdog.py:404` `recover_unsubmitted_executions`, `:489` `_adjudicate_ambiguous_execution` | classify → venue lookup by `clientOrderId` → COMPLETED / FAILED / **UNKNOWN + alert** | n/a | resolves the claim | — | **read-only; never resubmits** | `[CODE]`, started @`main.py:432` | start failure is **swallowed** (`except Exception → "INACTIVE"` + warning) — the app serves traffic with no stale-order recovery |
| 11 | `exchange_reconciliation.py:115` `reconcile_open_orders` | ghost / missing / fill-mismatch handling, exchange treated as source of truth | n/a | — | — | reads venue | `[TEST]` passed | **not started anywhere**; `_fallback_poll` in the WS listener is explicitly `NOT IMPLEMENTED`; a detected drift **never halts trading** |
| 12 | `routers/admin.py:185` `POST /kill-all` | `get_operator_user` → `global_safety.activate()` | n/a | transition lock `SET NX` 15s | n/a | halts new orders only | `[TEST]` 36 passed, 6 skipped | **no `require_aal2`** — the platform-wide halt is password-only |

### 3.1 The gateway's guard sequence (`core/execution_engine.py`)

`execute_with_idempotency:656`, in order:

1. **Environment guard, first statement** (`:757-820`) — `assert_live_environment` or
   `assert_simulation_environment_matches`. Raises; never returns a droppable verdict.
2. **Source guard** (`:836`) — `source != "bot_runner"` → `blocked`.
3. **Kill switch** (`:845-870`) — an *exception* in the check also blocks.
4. **Strategy ownership** (`:872`) — `_validate_strategy_exists(tenant, strategy)`.
5. **Idempotency** (`:890-905`) — `check_idempotent_execution`.
6. **Claim + capacity reservation** (`:948-960`) — live orders use
   `claim_execution_within_capacity` (one transaction, per-tenant advisory lock).
7. `_execute_trade_internal:1133` adds: branch-point agreement assertions (`:1180-1209`),
   equity-readable-or-refuse (`:1253`), notional ceiling (`:1296-1330`), **mandatory risk
   evidence** (`:1340-1360`), authorisation minting (`:1366-1420`), `mark_submitted` in a
   separate committed session (`:1432-1480`), then `place_order` (`:1490`).

### 3.2 Confirmed weaknesses in the path

- **Three of thirteen signed checks are attested, not observed.**
  `execution_engine.py:1401-1406` signs `live_trading_entitlement` and `exchange_binding`
  with the comment *"Established by the caller that resolved the deployment"*, and
  `precision` is signed **before** the precision check runs (it happens inside
  `place_order`). The signature proves the gateway *claimed* them.
- **`execution_environment` defaults to `LIVE`.** A caller that omits the argument is
  indistinguishable from one that says `LIVE`. The docstring states 70 of 71 funnel call
  sites omit it and 65 of 71 engine construction sites state no environment.
- **`source="bot_runner"` is the whole authorisation**, and `execute_trade` hardcodes it.
  What currently neutralises that inheritance is the separate `risk_approved` requirement.
- **No two-phase commit around the venue call.** Three separate committed transactions
  straddle `create_order` (claim, `mark_submitted`, `update_status`). The split makes the
  crash window *decidable*, not absent. There is a real interval where the venue holds an
  order and the platform does not know its id.
- **Two paper engines share one word.** `BotRunner(paper_trading=True)` simulates fills
  *inside* `ExecutionEngine` against a synthetic `{"total_equity": 10000.0}` and has **no
  import edge** to `backend_app/backend/paper/`, so those fills never reach
  `paper_accounts`/`paper_orders`/`paper_fills`/`paper_positions`. A trader who deploys in
  paper mode gets executions their paper dashboard does not show.
- **The writer of `public.orders` was not identified.** Migration 030 measured
  `ORDERS_ROWS = 24` and `EXECUTION_RECORDS_WITH_ORDER_ID = 0`. The execution path does not
  write that table.

---

## 4. Weighted readiness scorecard

Weights are the brief's starting weighting, **unmodified**. Nothing was demonstrably
outside the initial launch scope — payments are live, referrals exist in production, and
the marketplace expiry gap is a live billing defect — so **no renormalization was
applied**. Each requirement appears in exactly one category.

Status key: **VER** implemented & verified by executed evidence · **UNV** implemented,
insufficiently verified · **PART** partially implemented · **DEF** confirmed defect or
blocker · **N/A** out of initial scope.

### 4.1 Execution integrity and idempotency — weight 20% · Impl 88% · Evidence 55%

| Requirement | Status | Evidence | Launch impact |
|---|---|---|---|
| All venue writes behind one authorisation boundary | **VER** | 2 of 2 `create_order` sites gated; 101 tests passed | — |
| Signed, order-bound, consumed `LiveOrderAuthorization` | **VER** | `live_execution_gate.py`; mismatch/signature/incomplete-set refusals all tested | 3 of 13 checks attested only |
| Manual order endpoints hard-refused | **VER** | 4 unconditional 403s | — |
| Deterministic idempotency key persisted with a uniqueness guarantee | **UNV** | `execution_records.execution_id`; **the DDL constraint lives in an Alembic revision I did not open**, and its test is PG-gated → skipped | **high** — idempotency correctness partly rests on an unread constraint |
| Venue-side idempotency via `clientOrderId` | **DEF** | forwarded at `exchange_executor.py:1618`; **per-venue field mapping unverified against any venue** | **critical** — sole duplicate-order defence at the venue |
| `mark_submitted` committed before the venue call, fail-closed | **UNV** | `:1432-1480`; PG-gated test skipped | high |
| Ambiguous submission adjudicated, never resubmitted | **UNV** | `order_watchdog.py:404-615` traced; PG-gated test skipped | high |
| Kill switch re-read at the venue boundary, fail-closed | **VER** | 36 passed; **durable audit trail 6 skipped (PG-gated)** | audit unverified |
| `execution_environment` stated rather than defaulted | **PART** | 70/71 call sites omit it; default is `LIVE` | medium |
| Cross-process execution lock behaves as a distributed lock | **UNV** | `MockRedisClient` is a per-process dict; **real-Redis gate has never executed** | **critical** |
| Paper/live isolation | **VER** | 3 independent mechanisms; 172 passed | two-paper-engine divergence |

### 4.2 Risk controls and financial correctness — weight 20% · Impl 66% · Evidence 42%

| Requirement | Status | Evidence | Launch impact |
|---|---|---|---|
| Risk evidence mandatory at the gateway | **VER** | `:1340-1360`, `risk_approved` defaults `None` → refuse | — |
| Per-order notional ceiling, fail-closed on unreadable equity | **VER** | `_capacity_bounds_for`; NaN/∞/empty/0/negative all refuse | — |
| Cross-order, cross-strategy exposure aggregation | **UNV** | `claim_execution_within_capacity` + advisory lock; **PG-gated test skipped** | **critical** |
| Daily loss limit enforced on the order path | **DEF** | `RiskEngine.check_daily_loss` exists; gateway accepts a caller boolean. Code states it is unenforced at `:1225-1244` | **critical** |
| Max drawdown enforced on the order path | **DEF** | same | **critical** |
| Concentration / per-symbol exposure limit | **DEF** | none found on the order path; `exposure_by_symbol` is reporting-only | high |
| Price / position staleness check before authorizing | **DEF** | **no staleness check anywhere** on the authorization path; grep across risk/pricing/portfolio returned zero | **critical** (partly mitigated: market orders are refused) |
| Decimal precision in risk and fee accumulators | **VER** | 218 passed; `Decimal` throughout `risk_engine`, `fee_engine`, `position_model` | floats remain at `exchange_reconciliation.py:514-518` and `referral.py:743,761` |
| Fee denomination never assumed; mixed-currency aggregates absent not summed | **VER** | 3-branch `record_fee`; `get_fee_summary` returns `None` across >1 asset | — |
| Authoritative equity reconciled against venue balance | **DEF** | authoritative value is `ExecutionEngine.current_equity`, in-process, caller-seeded; nothing reads an exchange balance | **critical** |
| Order/fill reconciliation against venue state | **PART** | `exchange_reconciliation.py` exists, **is not started**, and never halts trading | high |
| Deposits / withdrawals excluded from trading P&L | **PART** | no capital-flow ledger; equity is structurally recomputed. `RiskEngine.update_equity` would raise `peak_equity` on any inflow routed through it | medium |
| Referral commission idempotence + payout debit compensation | **UNV** | `_compensate_debit` covers both failure modes; PG-gated tests skipped. Redis lock **fails open** at `referral.py:723-727` | high |
| Safety-control transition audit trail | **UNV** | `safety_audit.py` + migration 028; **6 durable-write tests skipped** | high |
| Billing amount precision (MONEY-02/04) | **VER** | `_exact_minor_units` returns `None` rather than guessing; hardcoded per-handler currency | — |

### 4.3 Exchange connectivity and order lifecycle — weight 15% · Impl 68% · Evidence 32%

| Requirement | Status | Evidence | Launch impact |
|---|---|---|---|
| Exchange library exactly pinned | **VER** | `requirements-base.txt:95` → `ccxt[async]==4.3.92` | re-measure `SANDBOX_MECHANISMS` on any bump |
| Capability registry fail-closed; `LIVE_TRADING_AUTHORIZED` unsettable in code | **VER** | 132 passed; `tradable() == []`; `_load_safety_blocked()` falls back to *blocked* on ImportError | — |
| Requested sandbox flag distinguished from proven sandbox endpoint | **VER** | `verify_sandbox_endpoint` requires positive evidence (`apiBackup` present, `api == test`, `api != apiBackup`), or header **value** for OKX; refuses & closes the client on failure | **unit tests over fake clients — certifies nothing about any venue** |
| Unsafe venue withheld at every construction path | **VER** | bitget refused at factory **and** in `BaseExchangeExecutor.__init__` before credentials are stored; case/whitespace normalised | — |
| Venue sandbox order-lifecycle certification (12 scenarios) | **DEF** | **0 orders, 0 credentials, 16 tests skipped**; 3 reproductions red by design | **critical** |
| Duplicate `clientOrderId` rejected by the venue | **DEF** | named in the certification report as *"the highest-value unrun test"* | **critical** |
| Per-venue rate-limit conformance; 429 / `Retry-After` | **DEF** | live limiter is flat; `RateLimitError` hardcodes `retry_after=60`; `exchange_rate_limiter.py` has **zero importers** | high |
| Precision, amount-step snapping, venue minimums | **VER** | dual-convention `_decimal_places`, `ROUND_DOWN`, `BELOW_PRECISION`/`BELOW_MIN_QUANTITY`/`BELOW_MIN_NOTIONAL` | `format_symbol_for_ccxt` is suffix-matching over a hardcoded quote list — EUR/GBP/TRY/DAI split wrong |
| Error normalization + credential redaction before logging | **VER** | `_redact_credentials` called **before** classification | substring-based classification is brittle |
| Per-tenant executor cache (funds-leak fix) | **VER** | length-prefixed sha256 over `(user_id, venue, mode, key, secret, password)`; keyword-only required `user_id`; belt-and-braces recheck | process-global; no cross-process invalidation |
| WebSocket fill ingestion running | **DEF** | listener **never started**; `_fallback_poll` logs `NOT IMPLEMENTED`; `handle_fill` drops `fee.cost` | high |
| Submission timeout → UNKNOWN + reconciliation | **UNV** | `place_order_and_wait_confirmation` marks `status="unknown"`; recovery handle `find_order_by_client_order_id` code-verified only | high |
| OKX usable on the pinned ccxt | **DEF** | `load_markets` documented broken (`connection_engine.py:196-250`) | exclude OKX from initial scope |

### 4.4 Persistence and reconciliation — weight 15% · Impl 68% · Evidence 33%

| Requirement | Status | Evidence | Launch impact |
|---|---|---|---|
| Venue id separated from platform order id (TRADE-12) | **VER** | migration 030 + `:1056` `venue_order_id=` + `update_status:1412`; the FK violation that stranded rows EXECUTING after a real fill is closed | DDL unapplied to production |
| Position lifecycle persistence | **UNV** | PG-gated → skipped | high |
| State transitions atomic with the venue write | **PART** | 3 committed transactions straddle `create_order`; compensated, not eliminated | accepted design; needs the watchdog verified |
| Exposure used for risk sizing is durable | **DEF** | `recovered_exposure_coins` is in-process memory, lost on restart, and risk sizing consumes it | high |
| Writer of `public.orders` identified and consistent | **DEF** | not identified; `ORDERS_ROWS=24`, `EXECUTION_RECORDS_WITH_ORDER_ID=0` | medium |
| Restart recovery rediscovers resting venue orders | **DEF** | `_recover_live_state` re-queries **balance only**; nothing calls `fetch_open_orders` | **critical** |
| Schema drift checker is read-only and proven so | **UNV** | `default_transaction_read_only` set **and read back**; pooler port refused; 12 tests — PG-gated → skipped | medium |
| Production schema verified to match | **DEF** | runbook: *"No verification has been run against production"* | **critical** |
| Single authoritative migration ordering | **DEF** | **three** migration systems (`backend_app/alembic/versions/`, `backend_app/migrations/*.sql`, `migrations/*.sql`) with no documented ordering | high |
| Paper ledger idempotency and concurrency | **UNV** | partial unique index + optimistic `version` → 409; partly PG-gated | medium |
| One paper ledger, not two | **DEF** | deploy-in-paper fills never reach the `paper_*` tables the dashboard reads | medium (paper-launch blocker) |

### 4.5 Concurrency, quotas and dependency safety — weight 10% · Impl 76% · Evidence 30%

| Requirement | Status | Evidence | Launch impact |
|---|---|---|---|
| Cache fallback does not fake a distributed lock outside dev/test | **VER** | `_get_active_cache` returns `None`; the `VYOMQUANT_MODE != "production"` comparison that could never be false is documented as removed | — |
| Cache facade fails closed for lock and quota callers | **DEF** | facade is fail-**soft**: `get→None`, `set→False`, `delete→0`, `incr→0`, all swallowing exceptions. Callers see a miss, never an error | high |
| Lock release is atomic and ownership-scoped | **PART** | `_release_slot_lock` is read→compare→delete across 3 awaits; every non-owner branch declines to delete, so the unsafe direction is unreachable *from them* | documented residual; `getdel` + `_GETDEL_LUA` already exists, so the "not portable" rationale is superseded |
| Quota enforcement atomic across processes | **UNV** | advisory `SET NX` only — **not a DB constraint**; a failover or `maxmemory` eviction admits two holders. **Real-Redis verification never executed** | high |
| Single-bot ownership durable across pods | **UNV** | `bot_ownership.py` Postgres `ON CONFLICT` upsert, token-scoped release/heartbeat, fail-closed — the strongest control here; **PG-gated test skipped** | **critical** |
| Lua idempotency script correct in production | **UNV** | `MockRedisClient.eval_lua` **ignores the script** and reimplements the predicate in Python. That is how a constant-true `string.sub(...,1,7) ~= 'processing'` survived | high |
| Worker heartbeat, duplicate-worker prevention, dead-lettering | **VER** | 103 passed in-process | cross-process unverified |
| DSN / cluster selection safe, single write site, ambiguity fails closed | **VER** | explicit `--data-dir` > `PG_DATA_DIR` > default; exit 2/3 on ambiguity; 58 tests per the WS-H/WS-I reports | dormant empty `staging/pgdata` retained |
| Tenant DB context propagated on request paths | **DEF** | `set_config('app.current_tenant_id', …, true)` is correct and transaction-scoped — and **nothing on a request path calls it** | **critical** (see 4.6) |
| Connection pool bounded (TTL / idle eviction) | **DEF** | no TTL; each entry holds a plaintext credential for the process lifetime | medium |
| Plan downgrade reclaims stored exchange credentials | **DEF** | downgrade blocks the *next* add only | medium (product decision) |

### 4.6 Security and tenant isolation — weight 8% · Impl 62% · Evidence 48%

| Requirement | Status | Evidence | Launch impact |
|---|---|---|---|
| ES256-only JWT; no HS256 fallback in production | **VER** | `_test_token_fallback_allowed` reads only real `ENV`/`ENVIRONMENT`, returns `False` when unset; `settings.ENV` deliberately not consulted; AST + behavioural tests passed | `_build_jwks_url` placeholder fallback makes a missing `SUPABASE_URL` an outage, not a degradation |
| Step-up auth on money-moving and emergency operations | **DEF** | `require_aal2` is on exactly **2** handlers (`store_keys`, `delete_connection`). **Not** on order placement, `/kill-all`, close-all, admin or billing mutation | high |
| Database-enforced tenant isolation on execution tables | **DEF** | RLS `ENABLE` without `FORCE`; app connects as owner `postgres` → all 9 execution-table policies **inert**. `FORCE ROW LEVEL SECURITY` grepped across all three migration trees: **zero matches** | **critical** |
| Cross-tenant read/write isolation measured | **UNV** | 79 tables, 0 leaked, 0 unmeasured; 29 tests passing — **against a locally provisioned PostgreSQL**. Production **NOT VERIFIED** (stated 3× in `RLS_TENANT_ISOLATION_VERIFICATION.md`) | high |
| Exchange credential vault fails closed and rotates | **VER** | `api_key_vault.py` raises at construction without `MASTER_ENCRYPTION_KEYS`; `MultiFernet` first-encrypts/all-decrypt makes the documented rotation executable | key *set* is platform-wide, not per-tenant (ADR-0007 target, not built) |
| No orphaned module that would break rotation if wired | **DEF** | `core/credential_vault.py` derives a single Fernet from `MASTER_ENCRYPTION_KEYS.split(",")[0]`, **discarding every other key**. Imported by nothing — wiring it up would destroy all stored credentials | medium (delete it) |
| Placeholder and weak secrets rejected at startup | **VER** | 4-verdict `classify_secret`; `bool("YOUR_SUPABASE_SERVICE_ROLE_KEY") is True` is the motivating fact; JWT strength measured in **bytes**; enforced for empty `ENV` too | — |
| Rate limiter fails closed in production | **VER** | import-time fail-closed branch tested | order-dependent test — see §6.2 |
| `fee_records` RLS | **UNV** | migration 026 written, **untracked**, unapplied, PG-gated test skipped | high |
| Credential rotation procedures for all high-value secrets | **DEF** | documented for `MASTER_ENCRYPTION_KEYS` only. **None** for `JWT_SECRET`, `SUPABASE_SERVICE_ROLE_KEY`, `DATABASE_URL`, or the **static long-lived CI AWS access keys** | high |
| Secrets manager rotation configured | **DEF** | 8 secrets declared, **0** `aws_secretsmanager_secret_rotation` resources | medium |

### 4.7 Deployment, monitoring, recovery and rollback — weight 7% · Impl 40% · Evidence 22%

| Requirement | Status | Evidence | Launch impact |
|---|---|---|---|
| Tests gate production deploy | **DEF** | `01-pr-check.yml:4-5` is `on: pull_request` **only**. `02-build.yml:19` is `on: push [main, master]`. `03-deploy.yml:5-9` chains off `02 Build`. `pre_deployment_validation.py` runs 5 steps and **never invokes pytest**. A push to `main` with a red suite deploys | **critical** |
| Health check can remove a broken task from rotation | **DEF** | ALB `health_check { path = "/health", matcher = "200" }`; `main.py:988` `/health` **always returns 200** (status is a JSON field, not an HTTP code) and **never probes the database**. Container `HEALTHCHECK` targets `/health/live`, which is dependency-free. The correct 503-returning `/health/ready` is wired to **nothing** | **critical** |
| Readiness reflects real dependency state | **VER** | `readiness.py` — `UNKNOWN` blocks, 3s timeout, real `SELECT 1`, falsey Redis ping raised, detail redacted. Tests passed | correct code, **unused by infrastructure** |
| Metrics, alarms and alerting | **DEF** | `aws_cloudwatch_metric_alarm`, `aws_sns_topic`, `aws_cloudwatch_log_metric_filter`, `aws_config_*`, `aws_backup_*`, `aws_securityhub*` across `terraform/*.tf`: **0 matches** | **critical** |
| Backup and restore evidence | **DEF** | runbook: rollback *"has not been rehearsed… no restore-from-backup drill has been performed"* | **critical** |
| Kill switch / incident-response runbook | **DEF** | only `docs/history/INCIDENT_RESPONSE_PLAYBOOK.md`, `LAUNCH_DAY_RUNBOOK.md`, `OPERATIONS_RUNBOOK.md` — all under `history/`. No current kill-switch runbook | high |
| Security scan gates deploy | **VER** | `require_security_scan.py` refuses on missing token/run/403/non-success; `actions: read` granted | **CRITICAL only** — HIGH and below do not block |
| Immutable-artifact gate | **VER** | refuses to deploy a SHA with no ECR image | — |
| Deploy serialization | **VER** | `concurrency: ecs-deploy-…`, `cancel-in-progress: false`, documented against a real incident | — |
| All required workers hosted | **DEF** | `terraform/ecs.tf` hosts 3. `MarketplaceExpiryWorker` and `ReconciliationWorker` are unhosted — confirmed by a **strict xfail that behaved as expected**. Subscriptions past `period_expiry` are **never expired** → paid access outlives the paid period | high (billing) |
| Recovery service start failure is visible | **DEF** | `main.py:436` swallows an `OrderWatchdog` start failure into a warning; the app serves traffic regardless | high |
| Nightly audit gates anything | **DEF** | every job `continue-on-error: true` except `docker_validation.py` | low |
| Infrastructure drift controlled | **DEF** | terraform applied by no workflow; state local | medium |
| AWS auth hardened | **DEF** | static long-lived access keys, not OIDC | medium |
| TLS | **VER** | HTTPS listener, `ELBSecurityPolicy-TLS13-1-2-2021-06` | — |

### 4.8 Release testing and operational readiness — weight 5% · Impl 62% · Evidence 30%

| Requirement | Status | Evidence | Launch impact |
|---|---|---|---|
| The full suite can be collected and run | **DEF** | `14400 tests collected, 1 error` — `tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py` raises `FileNotFoundError: staging/probe_pg_find.py` **at import**, which interrupts collection. `pytest tests/` cannot complete | high |
| The suite is order-independent | **DEF** | reproduced: `test_api03…` passes alone and in alphabetical order, **fails** when run after `test_rate_limiter_fails_closed_in_production.py` | medium |
| Infrastructure-gated lanes have executed | **DEF** | **632 tests / 32 files PostgreSQL-gated → all skipped**. 9 tests / 3 files real-Redis-gated → all skipped; **the gate has never executed** (root cause: `conftest.py:13` blanks `REDIS_URL`) | **critical** |
| Venue sandbox lane has executed | **DEF** | 16 skips, 0 orders | **critical** |
| Production verification scripts have run against production | **DEF** | read-only checker and rollback-proven probe both exist and are well built; neither has been run against production | **critical** |
| Skips are self-explaining and not counted as passes | **VER** | every gated reason names the env var and says *"a SKIP here is NOT a pass"*; `pytest.ini` documents each marker | exemplary |
| Load / soak evidence for the live order path | **DEF** | none found | medium |

### 4.9 Totals

| Category | Weight | Implementation | Evidence-backed |
|---|---:|---:|---:|
| Execution integrity and idempotency | 20% | 88% | 55% |
| Risk controls and financial correctness | 20% | 66% | 42% |
| Exchange connectivity and order lifecycle | 15% | 68% | 32% |
| Persistence and reconciliation | 15% | 68% | 33% |
| Concurrency, quotas and dependency safety | 10% | 76% | 30% |
| Security and tenant isolation | 8% | 62% | 48% |
| Deployment, monitoring, recovery, rollback | 7% | 40% | 22% |
| Release testing and operational readiness | 5% | 62% | 30% |
| **WEIGHTED TOTAL** | **100%** | **70%** | **39%** |

**The two numbers are not interchangeable, and the gap between them is the finding.**
70% says most of the required machinery exists. 39% says most of it has never been
observed working against anything real. For a system that spends customer money
irreversibly, the second number governs.

**A high average must not conceal a critical blocker, and here it would.** Execution
integrity scores 88% implementation — and the one requirement in it that is a confirmed
defect (unverified `clientOrderId` mapping) is sufficient on its own to double a
customer's position. That is why the hard gate is a separate verdict.

---

## 5. Confirmed blockers and unverified critical controls

Full detail, owners, effort and acceptance criteria are in
`staging/PHASE11_LAUNCH_BLOCKER_REGISTER.md`. Summary:

### 5.1 Confirmed critical blockers (10)

| ID | Blocker | Category |
|---|---|---|
| P11-B01 | No venue has ever received an order; 12-scenario lifecycle unexecuted; no sandbox credential exists | Exchange |
| P11-B02 | `clientOrderId` per-venue field mapping unverified — the sole venue-side duplicate-order defence | Execution |
| P11-B03 | Daily-loss and max-drawdown limits not enforced at the order gateway | Risk |
| P11-B04 | Authoritative equity is an in-process number, never reconciled against venue balance | Risk |
| P11-B05 | Restart recovery does not rediscover resting venue orders | Persistence |
| P11-B06 | ALB and container health checks cannot remove a dependency-broken task from rotation | Ops |
| P11-B07 | Zero alarms, metrics filters, SNS topics or backup resources; no restore drill | Ops |
| P11-B08 | No test gate between a push to `main` and production deploy | Ops |
| P11-B09 | RLS inert on all 9 execution tables; no tenant context on any request path | Security |
| P11-B10 | Production schema never verified against the repository's migrations | Persistence |

### 5.2 Unverified critical controls (7)

Implemented, fail-closed by inspection, **zero executed evidence**. Each is skipped
because the infrastructure lane has never run. A skip is not a pass.

| ID | Control | Gate |
|---|---|---|
| P11-U01 | Cross-order capacity reservation under per-tenant advisory lock | `AERORA_TEST_DATABASE_URL` |
| P11-U02 | `execution_records` idempotency uniqueness constraint (DDL unread) | `AERORA_TEST_DATABASE_URL` |
| P11-U03 | Single-bot ownership claim across pods | `AERORA_TEST_DATABASE_URL` |
| P11-U04 | Cross-process quota and lock behaviour on real Redis | `AERORA_TEST_REDIS_URL` |
| P11-U05 | Durable safety-control audit trail | `AERORA_TEST_DATABASE_URL` |
| P11-U06 | `fee_records` RLS (migration 026, unapplied) | `AERORA_TEST_DATABASE_URL` |
| P11-U07 | Position lifecycle persistence and `FOR UPDATE` serialization | `AERORA_TEST_DATABASE_URL` |

### 5.3 High-severity, non-gating (abbreviated)

Second venue-write boundary kept shut only by the absence of an authorisation minter ·
`execution_environment` defaults to LIVE for 70/71 call sites · no price-staleness check ·
no concentration limit · WebSocket fill listener never started and `handle_fill` drops
fees · reconciliation service never started and never halts trading · in-process exposure
lost on restart · two paper engines · `MarketplaceExpiryWorker` unhosted (paid access
outlives the paid period) · `require_aal2` on 2 endpoints only, not on `/kill-all` ·
cache facade fails soft for every quota and lock caller · Lua idempotency script never
executed · rotation procedures missing for 4 high-value secrets · full-suite collection
error · order-dependent test · three migration systems · OKX `load_markets` broken on the
pinned ccxt · per-venue rate limits and `Retry-After` absent.

---

## 6. Test evidence and limitations

Every command below was executed in this workspace on 2026-10-10. Logs are in
`staging/phase11/`.

| # | Command (abbreviated) | Result | Duration | Log |
|---|---|---|---|---|
| 1 | `pytest` — live gate, risk gate, equity bound, single pipeline, exchange-claiming paths, second write path, sealed submission | **101 passed, 0 failed** | 396.3 s | `t1_execution_gate.txt` |
| 2 | `pytest` — risk accumulators, KILL-03, KILL fail-closed, fee denomination, billing precision, accounting identity, order normalization, close-all | **218 passed, 6 skipped** | 46.5 s | `t2_risk_kill.txt` |
| 3 | `pytest` — sandbox endpoint verification, bitget routing, capability registry, executor cache, connection pool, reconciliation fail-safe | **132 passed, 0 skipped** | 62.1 s | `t3_exchange.txt` |
| 4 | `pytest` — quota ×4, Redis fallback, command durability, worker heartbeat, webhook duplicate | **103 passed, 9 skipped** | 33.6 s | `t4_quota_redis.txt` |
| 5 | `pytest` — HS256, AAL2 ×2, JWT strength, placeholder secrets, rate limit ×2, readiness, worker deployment, credential redaction, vault isolation, test-token | **372 passed, 1 failed, 1 xfailed** | 61.7 s | `t5_security.txt` |
| 6 | `pytest` — paper/live isolation ×2, simulator routing, paper determinism | **172 passed** | 54.6 s | `t6_paper.txt` |
| 7 | `pytest` — property idempotence under retry, protected-logic containment, atomic cancellation, position delta race, env precedence | **67 passed, 6 skipped** | 161.6 s | `t7_property.txt` |
| 8 | `pytest tests/ --collect-only` | **14,400 collected, 1 ERROR — collection interrupted** | 125.3 s | `t8_collect.txt` |
| 9 | `pytest` over all 32 `*_postgres.py` files `--collect-only` | **632 collected** (all would skip) | 26.8 s | — |
| 10 | `pytest tests/test_kill03_…` `-rs` | 36 passed, **6 skipped — durable audit trail unverified** | 14.1 s | — |
| 11 | `pytest test_api03… test_rate_limiter…` (alphabetical) | **52 passed** | — | — |
| 12 | `pytest test_rate_limiter… test_api03…` (reversed) | **1 failed, 51 passed** → order dependence | 3.6 s | — |

**Totals across batches 1-7: 1,165 passed · 1 failed · 27 skipped · 1 xfailed (strict,
expected).**

### 6.1 The collection error

```
ERROR tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py
FileNotFoundError: [Errno 2] No such file or directory:
  'C:\aerora_quant_backend_updated_final1\staging\probe_pg_find.py'
!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!
```

The test reads that probe at **module import**, so the failure happens during collection
and aborts the whole run. `staging/` holds `probe_pg_activity.py`, `probe_pg_blockers.py`
and `probe_pg_ready.py` — `probe_pg_find.py` was never created. Both the test and
`staging/` are **untracked**, and `staging/` is **not gitignored**, so committing the test
without the probe (or the probe without the test) breaks every CI test lane.

### 6.2 The one failure

`tests/test_api03_rate_limit_storage_is_fail_closed.py:342`
`test_the_module_exposes_the_decision_it_actually_applied` — `assert rl.config.storage_uri
in (MEMORY_URI, rl.config.redis_url)` → `assert False`.

Passes alone (34 passed) and in alphabetical collection order (52 passed). Fails when run
after `tests/test_rate_limiter_fails_closed_in_production.py`, which re-imports
`backend_app.core.rate_limit` under a different environment and leaves the module global
inconsistent. **This is a test-hygiene defect, not a production defect** — but it means
the fail-closed rate-limit wiring assertion can be silently neutralised by collection
order, which is exactly the property that assertion exists to guarantee.

### 6.3 Limitations of this campaign — stated plainly

- **No PostgreSQL and no Redis were available.** `AERORA_TEST_DATABASE_URL` and
  `AERORA_TEST_REDIS_URL` are both unset. Per the absolute safety rules I did not start,
  provision or install either. **632 PostgreSQL tests and 9 real-Redis tests did not run.**
- **No exchange was contacted.** Every exchange test I ran is a unit test over fake
  clients or recorded fixtures. `tests/test_exch01_sandbox_endpoint_verification.py` says
  so in its own header: *"passing them certifies nothing about any exchange."*
- **No full-suite run.** Blocked by §6.1, and running 14,400 tests blindly was out of
  bounds. Batches were chosen for safety-criticality.
- **Pass/fail counts quoted from `staging/*.md` and `staging/*.txt`** (e.g. the 4,619-pass
  WS-C regression, the 166-pass EXCH-01 affected run, the 194-pass bitget regression) are
  **committed artifacts of previous executions**, not reproduced by me. They are marked as
  artifacts wherever cited.
- **Nothing about AWS was verified.** Running revision, worker service state, live RLS
  catalogue, secret rotation dates and branch protection all require read-only AWS or
  GitHub API evidence.
- **The Alembic DDL for the `execution_records` uniqueness constraint was not read**
  (`d97ffff9c3bb_execution_schema_rebuild.py`). Idempotency correctness partly rests on it.

---

## 7. Fifteen-day critical-path plan

Six parallel workstreams is the maximum useful number — beyond that, shared-file ownership
collides. **The critical path runs through sandbox credential procurement**, which is an
external dependency and must start on day 1.

### 7.1 Ownership map (no two streams edit the same critical module)

| Stream | Owner | Exclusive files |
|---|---|---|
| **A — Venue certification** | Exchange eng. | `tests/exchange_sandbox/**`, `staging/EXCHANGE_SANDBOX_CERTIFICATION.md` |
| **B — Risk at the gateway** | Risk eng. | `core/risk_engine.py`, `backend/execution_guard.py`, the risk block in `core/execution_engine.py` |
| **C — Reconciliation & recovery** | Execution eng. | `backend/exchange_reconciliation.py`, `backend/order_watchdog.py`, `master_executor._recover_live_state` |
| **D — Infrastructure lane** | Platform eng. | `tests/conftest.py`, `.github/workflows/01-pr-check.yml`, `scripts/verify_*.py` |
| **E — Observability & deploy gates** | SRE | `terraform/*.tf`, `.github/workflows/02,03,05`, `healthcheck.sh` |
| **F — Security & isolation** | Security eng. | `core/tenant_db_context.py`, `core/auth_middleware.py`, migrations 023/026 |

Streams B and C both touch `core/execution_engine.py`. **B owns it.** C integrates through
`order_watchdog.py` and a review handoff on day 8.

### 7.2 Day by day

| Days | Priority | Streams | Gate |
|---|---|---|---|
| **1** | Procure Binance + Bybit **testnet** credentials and fund them. Fix the collection error (`probe_pg_find.py`) and the order-dependent test. Stand up the PostgreSQL + Redis test lane: remove the unconditional `REDIS_URL=""` blanking in `conftest.py:13` and export `AERORA_TEST_REDIS_URL` in CI. | A, D | **G0: credentials in hand.** Miss this and 15 days is not feasible. |
| **2-3** | Run the full 632-test PostgreSQL lane and the 9 real-Redis tests. Triage. Apply migrations 023/026/028/030 to staging and verify. Wire `/health/ready` to the ALB target group and `healthcheck.sh`. Add branch protection requiring `01 PR Check`; add a test job to the deploy chain. | D, E, F | **G1: the verification lane runs green**, or every failure is classified. |
| **3-5** | **Scenario 8 first** — duplicate `clientOrderId` on Binance testnet, then Bybit. Then scenarios 1,2,3,4,6a/6b/6c,9b,10. Enforce daily-loss, drawdown and open-position limits **at the gateway** (wire `ExecutionGuard` into `_execute_trade_internal`). | A, B | **G2: duplicate-`clientOrderId` rejection proven on both venues.** Hard gate — nothing proceeds without it. |
| **5-7** | Reconcile authoritative equity against venue balance before authorizing; add a staleness bound with an explicit, reviewed policy. Start `ExchangeReconciliationService` under a single owner; make drift engage the kill switch. Add `fetch_open_orders` to `_recover_live_state`. | B, C | **G3: an order cannot be authorized against an equity figure the venue has not confirmed.** |
| **6-8** | CloudWatch alarms + SNS for: order-path error rate, `UNKNOWN` execution count, kill-switch state, readiness 503s, worker heartbeat staleness, DB connection saturation. Enable automated backups; **perform a restore drill**. Write the kill-switch runbook. | E | **G4: restore drill completed with a recorded RTO/RPO.** |
| **8-10** | Scenarios 5a, 11, 12. Fee ingestion from the venue (`handle_fill` currently drops `fee.cost`). Start the WebSocket fill listener or document polling as the supported mechanism. Deploy `MarketplaceExpiryWorker`. Integration review between B and C on `execution_engine.py`. | A, C, E | **G5: a venue-reported fee reaches the ledger.** |
| **10-12** | Wire `tenant_db_context` into `get_db` so `app.current_tenant_id` is set per request; **then** enable `FORCE ROW LEVEL SECURITY` on the 9 execution tables behind a feature flag with a verified rollback. Extend `require_aal2` to `/kill-all`, close-all and billing mutation. Document rotation for `JWT_SECRET`, service-role key, `DATABASE_URL`, CI AWS keys. Migrate CI to OIDC. | F | **G6: cross-tenant sweep green with RLS forced**, and rollback proven. |
| **12-13** | Run `verify_production_schema.py` (read-only) then `verify_production_security.py` (writes, rolls back) against production under change control with a fresh backup. Close drift. | D, F | **G7: production schema matches the repository.** |
| **13-14** | Full regression. Soak the live order path on testnet for 24h: restart a worker mid-flight, kill Redis, kill the database, engage the kill switch, verify no duplicate and no orphan. | all | **G8: zero duplicates and zero orphans across the chaos matrix.** |
| **15** | Go/no-go. Live pilot with: 2 venues, limit orders only, internal accounts, hard notional cap, one strategy, kill switch rehearsed, on-call staffed. | all | **G9: formal live-pilot approval.** |

### 7.3 What makes the date impossible

| Risk | Why it breaks the date |
|---|---|
| **Testnet credentials not obtained by day 2** | Everything in streams A, B(equity), C(recovery) and the entire chaos matrix is downstream. **No substitute exists.** |
| **Duplicate `clientOrderId` is silently ignored by a venue** | The platform's only venue-side duplicate defence fails. The fix is a venue-specific param mapping plus an acknowledgement check — 3-5 engineer-days *after* discovery, discovered on day 3-5. |
| **The 632-test PostgreSQL lane reveals real defects** | Those tests have never run. The expected-failure count is genuinely unknown. A 20% failure rate is ~5 engineer-days of triage. |
| **Forcing RLS empties reads in production** | Policies compare `varchar(36)` against `text` in 7 of 9 cases and `positions.tenant_id` has a type divergence between the ORM and the migration. Must be flagged and rollback-tested, not shipped. |
| **No restore drill has ever succeeded** | If the first attempt fails, the fix is infrastructure work on the critical path. |

### 7.4 Safely deferrable until after the initial launch

Per-venue rate-limit conformance and `Retry-After` · OKX, Coinbase, Kraken, KuCoin
onboarding · per-tenant encryption keys (ADR-0007) · unifying the two paper engines ·
deleting the orphaned `core/credential_vault.py` · consolidating the three migration
systems · connection-pool TTL · deposit/withdrawal P&L segregation · the `public.orders`
writer question · `execution_environment` call-site cleanup (70 sites) ·
`format_symbol_for_ccxt` quote-list generalisation.

---

## 8. Initial-launch scope recommendation

### 8.1 Recommended restricted live pilot — **after** G0-G9

| Dimension | Recommendation | Why |
|---|---|---|
| **Venues** | **Binance and Bybit only** | The only two with a `URL_SWAP` sandbox mechanism **measured** on ccxt 4.3.92, so they are the only two whose order path can be certified before going live. |
| **Explicitly excluded** | **bitget** — safety-blocked (sandbox routes to production). **OKX** — `load_markets` broken on the pinned ccxt. **Coinbase, Kraken, KuCoin** — no sandbox exists on ccxt 4.3.92, so no lifecycle certification is possible at all. | Do **not** widen `SUPPORTED_EXCHANGES` or `SANDBOX_MECHANISMS` to meet a date. |
| **Order types** | **Limit only** | Market orders arrive as `price <= 0` and are already refused by `_capacity_bounds_for` — correctly, since no mark-price policy exists. Keep that refusal; do not invent a valuation rule inside a capital check. |
| **Strategies** | One, reviewed, with a fixed symbol set | Minimises the surface the unverified controls cover. |
| **Accounts** | Internal / employee accounts, opt-in | No customer funds until G1-G8 have held for a full cycle. |
| **Capital** | Hard per-tenant notional cap set well below the equity ceiling, plus a platform-wide aggregate cap | The cross-order reservation is implemented but **unverified** (P11-U01). |
| **Concurrency** | One bot per `(tenant, symbol)`, enforced | Single-bot ownership is the strongest control and is **unverified** (P11-U03). |
| **Duration** | 10 trading days before any customer exposure | Time for the reconciler and watchdog to demonstrate they catch drift. |

### 8.2 Paper-only launch — **CONDITIONAL**, and much closer

Paper has no venue dependency, and paper/live isolation is verified by three independent
mechanisms and 172 passing tests. Four blockers stand in the way, none of which needs a
venue:

1. **P11-B06** — health checks (ops; 0.5 day)
2. **P11-B07** — alarms and a restore drill (ops; 3-4 days)
3. **P11-B08** — a test gate on deploy (ops; 0.5 day)
4. **`MarketplaceExpiryWorker` unhosted** — paid access outlives the paid period, which is
   a live billing defect on a platform that already takes payments (1 day)

Plus the two-paper-engine divergence if deploy-in-paper is customer-visible: a trader's
simulated fills do not appear in their paper dashboard.

**A paper-only launch is achievable in roughly 5-7 engineer-days of operational work.**

### 8.3 Verdict on the 15-day target

> **CONDITIONAL — feasible only if Binance and Bybit testnet credentials are in hand and
> funded by end of day 2, and only for the restricted pilot scope in §8.1.**
>
> If credentials slip past day 2, the date is **not feasible**: G2 (duplicate
> `clientOrderId` proven) is a hard gate, and five downstream workstreams depend on it.
>
> If the 632-test PostgreSQL lane or the restore drill surfaces material defects, the date
> is **not feasible**. Both are genuinely unknown today — neither has ever run.
>
> **Do not weaken any gate to meet the date.** The cheapest alternative that preserves
> every safety criterion is to launch **paper-only** on schedule (§8.2) and gate live
> trading on G0-G9 completing on their own timeline.

---

## 9. Final recommendation

**Do not enable live cryptocurrency trading. Hard-gate verdict: BLOCKED.**

The engineering here is better than its evidence. The order path funnels through one
gateway, the venue boundary demands a signed artefact rather than reading a flag, and the
failure direction is consistently closed. Past defects were found by execution, fixed at
the right layer, and documented with the measurement attached. Four of the six findings in
the most recent prior scorecard are now genuinely stale, which is a sign of real progress
rather than of reports being rewritten.

What is missing is not more code. It is contact with reality:

- **No order this platform has ever produced has reached any exchange.** Not one, not even
  a sandbox. Every exchange assertion rests on unit tests over fake clients, and the test
  files say so themselves.
- **The verification lane has never run.** 632 PostgreSQL tests and 9 real-Redis tests
  hold the proofs for seven controls that are load-bearing for live trading, and all of
  them are skipped. The root cause is two lines of test configuration, not a design
  problem — which makes it cheap to fix and inexcusable to leave.
- **Operations is not ready to run a trading venue.** A dependency-broken task stays in
  the load balancer rotation. Nothing alerts. No restore has ever been attempted. A push
  to `main` with a red suite reaches production.

Three things should happen in this order. **First**, close the operational blockers
(P11-B06, B07, B08) and the test-lane blockers — none needs a venue, and they are the
cheapest risk reduction available. **Second**, launch paper-only, which the evidence
supports once those are closed. **Third**, run the venue certification campaign and the
risk-at-the-gateway work against testnet, and bring live trading to a formal pilot gate
when G0-G9 have actually held — on their own timeline, not on a date chosen first.

The brief asked for a defensible percentage. It is **39% evidence-backed readiness against
70% implementation completeness**, and the honest reading of that gap is that this platform
has built most of a safe trading system and has not yet demonstrated that it works.

---

*Audit performed read-only. Live trading remained OFF / NOT CERTIFIED throughout. No
order was submitted, no venue contacted, no credential read or printed, no production
configuration or data modified, no migration applied, no database or Redis process
started or stopped, and no git commit, push, stash, reset or clean was performed. All
pre-existing audit reports and JSON mirrors were left unmodified.*
