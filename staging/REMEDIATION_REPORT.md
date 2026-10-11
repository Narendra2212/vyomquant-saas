# REMEDIATION REPORT — Aerora / VyomQuant

Scope: remediation of the launch blockers identified in the pre-launch audit, in the
mandated dependency order. Every claim below is traceable to a nonce-stamped artifact in
`staging/` or to a named test file. Where something was not verified, it says so.

**Read section 0 first.** It corrects a conclusion from the audit phase that was wrong.

---

## 0. CORRECTION — THE LIVE-TRADING GATE WAS VERIFIED IN THE WRONG PLACE

**Status: the audit phase's live-trading conclusion is WITHDRAWN and replaced.**

The audit reported *"live trading OFF — 5/5 cases refused; the decisive case is both
switches set yet still blocked by hardcoded `ExecutionFlags.LIVE_TRADING_ENABLED =
False`."* That conclusion rested on `staging/verify_live_trading_off.py`, which invokes
exactly one thing:

```python
SafetyMonitor.check_execution_allowed("live_order")
```

`SafetyMonitor.check_execution_allowed` is **not on the path that places an order.**
Proven by AST scan of the four functions that form the chain to a venue
(`staging/verify_live_gate_on_real_path.py` → `staging/live_gate_real_RUNH8.txt`):

| Function | References a live-trading gate? |
|---|---|
| `backend_app/backend/master_executor.py:357` `_inner_loop()` | NO |
| `backend_app/core/execution_engine.py:434` `execute_with_idempotency()` | NO |
| `backend_app/core/execution_engine.py:815` `_execute_trade_internal()` | NO |
| `backend_app/backend/exchange_executor.py:747` `place_order()` | NO |

`check_execution_allowed` has exactly six call sites, none on the bot path:
`dag_engine.py:1280`, `:1512`, `:4498`, `routers/orders.py:808` (cancel), `:929`
(cancel-all), `routers/portfolio.py:292` (close-all). `ExecutionFlags` is read only by
that function and by `assert_safe_mode`.

So "5/5 REFUSED" is a true statement about the function that was called and says
**nothing** about whether a live order can be placed. This is precisely the
"exists but is never reached" defect class the audit was commissioned to find, and the
audit committed it.

### What actually keeps live trading off today

| # | Control | Kind | Strength |
|---|---|---|---|
| 1 | `routers/strategies.py:2104`, `:2120`, `:3883` hardcode `paper_trading=True` | literal | Absolute for the ordinary / legacy / DAG deploy routes |
| 2 | `Feature.LIVE_TRADING` entitlement, `strategy_service.py:1847-1850` | entitlement | **Weak** — granted on four paid tiers (`core/subscription_engine.py:461,514,576,645`) |
| 3 | `LIVE_REQUIRES_EXCHANGE_ACCOUNT`, `deployment_binding.py:1203` | precondition | Holds until a user connects an account |
| 4 | `BINDING_NOT_STORABLE`, `deployment_binding.py:2019` | schema | Incidental |
| 5 | Global kill switch, `core/execution_engine.py:~623-645` | **on-path, fails closed** | Real, and the only on-path safety gate |
| 6 | ECS task definition pins `VYOMQUANT_MODE=paper` | configuration | Does **not** gate `BotRunner`'s live branch, because `ExecutionFlags` is off-path |

`strategy_service.py:2061` and `:2959` compute `paper_trading=(mode != MODE_LIVE)`, so
`deploy_version` with `mode="live"` builds a LIVE `BotRunner` with a real
`CCXTExchangeExecutor`. **Live trading is held off by configuration, entitlement and the
absence of a connected exchange account — not by a code gate on the order path.** That is
a materially weaker posture than the audit reported.

`TRADING LIVE: OFF / NOT CERTIFIED` remains the correct classification. The *reason* has
changed, and NOT CERTIFIED is now the operative half.

---

## 1. FIXED AND VERIFIED

Each row: the defect, its root cause, the fix, the test, and how it was verified.

### BUG-056 — every `library_subscriptions` UPDATE failed (`42703`)
* **Root cause** `updated_at` column absent while a trigger referenced it.
* **Fix** `backend_app/migrations/022_fix_library_subscriptions_updated_at.sql`.
* **Test** `tests/test_bug056_library_subscriptions_updated_at_postgres.py`.
* **Verification** Column confirmed present on the live PostgreSQL 16.2 instance at the
  start of this phase. **CLOSED** (not reopened).

### BUG-057 — a listing creator could approve and feature their own listing
* **Root cause** The RLS UPDATE policy had `check_expr = NULL`, and the creator legitimately
  *is* the author, so no predicate expressible in RLS could distinguish "edit my listing"
  from "approve my listing". RLS cannot compare a new row against the old one.
* **Fix** `backend_app/migrations/023_remediate_privilege_escalation.sql` — `REVOKE
  INSERT, UPDATE ON library_strategies FROM authenticated`, then `GRANT INSERT (cols),
  UPDATE (cols)` for only the 39 creator-editable columns, computed from the catalogue
  rather than hardcoded. Moderation and social-proof columns (`moderation_status`,
  `is_featured`, `avg_rating`, `rating_count`, `clone_count`, `subscriber_count`) are not
  granted.
* **Rejected alternatives** `WITH CHECK` alone (predicate passes for the author); a guard
  trigger (more complex, needs service-role exemption logic).
* **Consequence accepted and documented** Deny-by-default: a future column is not writable
  until explicitly granted.
* **Test** `tests/test_bug057_listing_moderation_authz_postgres.py` — **40 passed**,
  covering creator self-approval, self-feature, protected-field edits, an authorised
  moderator approving *and* featuring (proving the legitimate operation still works), an
  unrelated creator, tenant substitution, and a direct database attempt.
* **PostgreSQL lesson** Column privileges are **additive** to table privileges; a
  column-level `REVOKE` is a no-op while a table-wide `GRANT` stands. The first version of
  023 made exactly that mistake and its own postflight caught it.

### API-01 — mass assignment on `PATCH /api/strategies/{id}`
* **Root cause** `update_payload = dict(body)` forwarded any caller-supplied key.
* **Fix** `_UPDATABLE_STRATEGY_COLUMNS` allowlist + `_assert_update_fields_are_mutable()`
  in `backend_app/routers/strategies.py`, rejecting with 422.
* **Decision** Allowlist not denylist (a denylist makes a future column silently
  writable); 422 rather than silent field-stripping (dropping a field tells the caller
  their write succeeded when it did not).
* **Test** `tests/test_api01_strategies_update_allowlist.py` — **64 passed**.

### AUTH-01 — `require_aal2` existed but was wired to nothing
* **Fix** Applied to `backend_app/routers/exchange.py` `store_keys` (POST `/keys`) and
  `delete_connection` (DELETE `/{exchange_id}`).
* **Deliberately not applied** `/supported`, `GET /`, `/test`, `/test-stored`,
  `/{id}/capabilities`, `/connections/*` — these do not persist key material.
* **Decision** Flat "requires aal2" rather than conditional step-up-if-enrolled, because
  `auth.mfa_factors` does not exist on the local database (Supabase-hosted) and a
  conditional design could not be tested. No bypass env flag was added — one would
  become permanently off.
* **Test** `tests/test_auth01_require_aal2_is_wired.py` — **16 passed**, exercising the
  real dependency, both directions.
* **Still open (AUTH-03)** `require_aal2`'s own docstring names billing-payment mutations
  as MUST-apply. Not gated: `billing.py:1668` checkout, `:1946` verify-payment, `:2624`
  payment-methods, `referral.py:688` payouts, `library.py:2826` checkout, `:3468` cancel,
  `:3560` renew. No password-change endpoint exists (handled Supabase-side).

### MONEY-01 — referral commission could be paid twice
* **Root cause** The installed `process_referral_commission` had **no** duplicate guard
  (`PROCESS_REF_COMM_HAS_DUP_GUARD | NO_EXISTS_GUARD`), and the wallet upsert ran
  unconditionally.
* **Fix** `backend_app/migrations/024_remediate_referral_double_commission.sql` — a
  partial `UNIQUE INDEX` on `referral_commissions(payment_id)`, and the wallet update
  moved **inside** the insert guard.
* **Decision** A database constraint, explicitly **not** a `SELECT EXISTS` guard: two
  concurrent deliveries can both pass a guard before either inserts. Keyed on
  `payment_id` alone because the installed function credits exactly one referrer;
  multi-level referral will fail the index and force an explicit decision rather than
  silently double-paying.
* **Test** `tests/test_money01_referral_commission_idempotence_postgres.py` — **17
  passed**, including `test_two_concurrent_workers_cannot_both_credit` using two raw
  `psycopg2` connections and a `threading.Barrier(2)`.
* **PostgreSQL lesson** `CREATE OR REPLACE FUNCTION` with a different argument-type list
  creates a **second overload** and leaves the vulnerable original live. An explicit
  `DROP FUNCTION` was required.
* **Latent issue recorded, not introduced by this fix** `referral_commissions.subscription_tier`
  is `NOT NULL`, so the commission function cannot credit a payment whose tier is unknown.

### MONEY-02 — money divided as a binary float
* **Root cause** Three webhook branches did `/ 100.0` on a provider's minor-unit integer,
  feeding `billing_invoices` and the referral commission base.
* **Fix** `_provider_major_amount(value, currency) -> Optional[Decimal]` in
  `backend_app/routers/billing.py`, composed from the two primitives that already existed
  (`_exact_minor_units` + `marketplace_money.to_major`) rather than a fourth money
  representation. Returns `None`, never `0` — `0` would record a real £0 charge. Amounts
  are now sent as decimal strings.
* **Test** `tests/test_money02_billing_amount_precision.py` — **49 passed**.

### MONEY-04 — a Razorpay INR payment credited a USD-denominated commission (NEW)
* **Root cause** The Razorpay branch passed the INR amount into `p_payment_amount_usd`,
  carrying its own annotation `# Note: This is INR, not USD`. `referral_commissions` has
  no currency column, and the function takes a flat 20%, so an INR payment credited a
  commission roughly 83× its real USD value and recorded it as dollars. Known, annotated,
  never fixed.
* **Fix** **Refuse, do not convert.** The RPC call was removed; the omission is logged at
  `logger.error` with `payment_id`, amount, `user_id` and `item_key` so it is manually
  settleable. The entitlement is still granted and the INR invoice is still written.
* **Decision** FX conversion was rejected: it requires choosing which rate, as of when,
  and who absorbs accrual-to-payout drift — a product decision about money owed to a
  referrer, not something to infer in a webhook handler. Adding a currency column was
  rejected as larger scope.
* **Verification** Exactly one executable `process_referral_commission` call remains, at
  `billing.py:2175` inside `stripe_webhook`, and it is in a scope that converts
  exclusively in USD. Asserted structurally by AST, not by text.

### WORK-01 — the command worker ACKed in `finally`, so failed jobs were discarded
* **Root cause** `finally: await redis_manager.xack(...)` acknowledged a command that had
  *raised* exactly as though it had succeeded. XACK removes the entry from the Pending
  Entries List, which is the only record that work is outstanding. A `stop_bot` that hit a
  transient error was silently dropped and the bot kept trading. Nothing recovered it —
  `backend/reconciliation_worker.py` does not reconcile the fleet against
  `strategy_deployments` (verified: zero matches for `strategy_deployments|start_bot|fleet`).
* **Fix** An `Outcome` enum (`DONE` / `REFUSED` / `FAILED`) separating a terminal policy
  decision from a transient failure — the distinction the `finally` collapsed. `REFUSED`
  and `DONE` acknowledge; `FAILED` leaves the entry pending.
* **Test** `tests/test_work01_command_durability.py` — **22 passed**.

### WORK-02 — no dead-letter queue, no PEL reaper, shared consumer name
* **Fix** `XAUTOCLAIM` reaper with `reclaim_idle_ms = max(poll_interval × 10, 30 s)` — the
  floor matters, because a threshold inside one poll period does not detect abandonment,
  it manufactures it by stealing work from a live consumer (the rule
  `training_worker.MIN_STALE_TO_INTERVAL_RATIO` already states for heartbeats). Bounded
  retry via Redis `INCR` on `command:attempts:<msgid>` with `MAX_DELIVERIES=5`, then a
  dead-letter entry on `command_queue:dead_letter` carrying the original fields plus
  reason/attempts/source-id/consumer — and only then an ACK. If the dead-letter write
  fails the entry stays **pending** rather than being dropped. Unique consumer name
  `{WORKER_NAME|command_worker}-{hostname}-{pid}`.
* **Supporting change** `xautoclaim` and `xpending` added to `SharedRedisManager` and to
  `MockRedisClient`, in the existing fail-soft style.
* **Test** Same file. The fake Redis models real PEL semantics (delivery → per-consumer
  PEL, XACK removes, XAUTOCLAIM transfers only entries idle past the threshold *and* owned
  by another consumer), plus **negative controls** proving the pre-fix `finally` shape
  empties the PEL and that a plain `>` read does not redeliver.

### OBS-04 — no worker ever wrote a health key (NEW)
* **Root cause** `WorkerBase.report_health()` called `redis_manager.get_pool()`.
  `SharedRedisManager` has no `get_pool`, no `__getattr__`, and its `pool` property
  returns the facade itself. Every heartbeat raised `AttributeError`, which the
  surrounding `except Exception` logged as though Redis were "temporarily down". The
  healthy/degraded/unhealthy computation above it was dead code, for every worker in the
  fleet — despite the class docstring promising that *"external liveness probes watching
  these Redis keys will see the real operational state."*
* **Proven before the fix** `staging/probe_worker_health.py` →
  `AttributeError: 'SharedRedisManager' object has no attribute 'get_pool'`.
* **Fix** Call the facade directly; it already exposes `hset` and `expire`.
* **Test** `tests/test_obs04_worker_heartbeat_is_written.py` — **15 passed**. The fake
  deliberately omits `get_pool` while keeping a truthy `pool` decoy, so it measures the
  fix rather than the absence of a fake.

### API-03 — the rate limiter read a trading mode as a deployment environment
* **Root cause** `env_raw = os.getenv("ENV") or get_vyomquant_mode(default="safe")`, with
  `DEV_TEST_ENVS` containing `safe`, `paper` and `ambiguous`. `VYOMQUANT_MODE` is a
  trading mode (`safe`/`paper`/`live`), not an environment. With the documented production
  value `VYOMQUANT_MODE=paper` and `ENV` unset, the limiter selected `memory://` — so the
  effective limit became the configured limit × replica count, announced at `info`. A
  genuine Redis outage degraded the same way. `"ambiguous"` was unreachable dead code:
  `get_vyomquant_mode` always returns a non-empty string.
* **Fix** One canonical detector, `backend_app/core/runtime_env.py`
  (`local_state_is_acceptable`), reading `DEV_MODE`/`ENV`/`ENVIRONMENT` only and never a
  trading mode; `backend_app/core/rate_limit.py` rebuilt around a frozen
  `RateLimitConfig` resolved by `resolve_rate_limit_config(probe=...)`. The identical
  judgement in `redis_manager._mock_cache_is_acceptable` now delegates to the same
  function, so there is one definition rather than two to drift.
* **Verification — the full matrix, in fresh subprocesses** (`staging/rl_matrix_RUNB2.txt`):

| Environment | Redis | Expected | Actual |
|---|---|---|---|
| local (`ENV=development`) | up | limiter works | `redis://` |
| local | down | documented local behaviour | `memory://` |
| local | unset | documented local behaviour | `memory://` |
| local (`DEV_MODE=true`) | unset | memory | `memory://` |
| test (`ENV=testing`) | up / down / unset | match test architecture | `redis://` / `memory://` / `memory://` |
| **paper (`VYOMQUANT_MODE=paper`)** | **up** | limiter works | `redis://` |
| **paper** | **down** | **fail closed** | **REFUSED** |
| **paper** | **unset** | **fail closed** | **REFUSED** |
| production | up | limiter works | `redis://` |
| production | down / unset | fail closed | REFUSED / REFUSED |
| `ENV` unset | unset | fail closed | REFUSED |
| `ENV` unset | up | works | `redis://` |

  The "Redis available" rows go through the real `redis-py` client against a minimal RESP
  responder, not an injected probe.
* **Fail-closed under concurrency** (`staging/rl_fc_RUND4.txt`): 6 concurrent processes ×
  2 limiter instances each — 6/6 refused when unavailable, **0/6 ever obtained a
  `memory://` fake**, 6/6 selected the same shared store when available.
* **Recovery** down → REFUSED, up → shared store. Recovers.
* **Import graph** (`staging/rl_graph_RUNA1.txt`) 14 modules import `rate_limit`; every
  one takes only the name `limiter`; zero consumers of the removed names.
* **Router slice** (`staging/rl_router_RUNG7.txt`) `main` + all 14 routers import 14/14
  under all three configurations. Pytest slice across the real router graph:
  **131 passed, 0 failed**.
* **Configuration defect found and fixed** The workspace `.env` set `REDIS_URL` but no
  `ENV` at all, while `.env.example` documents `ENV=development`. With API-03 fixed that
  combination made `main` and all 14 routers fail to import (0/14). `ENV=development` was
  appended to `.env` with an explanatory comment → 14/14. The deployed path was never
  affected: `terraform/ecs.tf` sets `{ name = "ENV", value = var.environment }` and
  `terraform/redis.tf` provisions ElastiCache.

**TASK 8: PASS.**

---

## 2. CONTROL EFFECTIVENESS TABLE

A function that exists and has unit tests but is never reached by a production path is
not a security control. Call sites were established by AST, with docstrings and comments
excluded, so a control cannot appear wired because its documentation mentions it.

| Control | File:line | Called | Reachable | Effective | Tested |
|---|---|---|---|---|---|
| `require_aal2` | `core/dependencies.py:456` | 2 (`exchange.py:315`, `:608`) | Yes | **Yes** (default `aal1` → 403) | Yes |
| Global kill switch | `core/execution_engine.py:~623` | 1, on the order path | Yes | **Yes**, fails closed | Partial |
| `assert_live_environment` / `assert_simulation_environment_matches` | `backend/execution_environment.py` | gateway, first statement | Yes | **Yes**, raises | Yes |
| Notional ceiling | `core/execution_engine.py:~963` | live branch | Yes | **Yes**, fails closed | Yes |
| Validation token | `backend/exchange_executor.py:709` | `place_order` | Yes | **Yes**, raises | Yes |
| `SafetyMonitor.check_execution_allowed` | `core/safety_config.py:212` | 6, **none on the order path** | Cancel/DAG only | Yes, where called | Partial |
| `SafetyMonitor.assert_safe_mode` | `:254` | 1 (`main.py` import time) | Yes | Yes, raises | Yes |
| `risk_manager.validate_trade_request` | `core/risk_manager.py:488` | 1 (`master_executor.py:445`) | Worker loop only | Yes | Yes |
| `ExecutionGuard.validate_trade` | `backend/execution_guard.py:164` | 3, **all risk-reducing** | 2 of 3 | Partial | Partial |
| `enforce_kill_switch` | `core/global_safety.py:430` | **0** | **No** | Unreachable | n/a |
| `validate_safety_config_integrity` | `core/safety_config.py:18` | **0** | **No** | **No** — see below | n/a |
| `validate_production_config` | `core/config.py:22` | **0** | **No** | **No** — logs only | n/a |
| `HardQuotaEnforcer.enforce_*` (15) | `core/hard_quota_enforcer.py:194-586` | **0** | **No** | Unreachable | n/a |
| `TenantMiddleware` | `core/tenant_middleware.py:29` | **not installed** | **No** | Unreachable | n/a |
| `get_tenant` / `require_admin` / `TenantQuotaDependency` | `:308` / `:321` / `:333-347` | **0 `Depends`** | **No** | Would 401 on everything | n/a |
| `RateLimitMiddleware` (trade) | `core/rate_limit_middleware.py:40` | **not installed** | **No** | Fails **open** | n/a |
| `require_rate_limit` | `:153` | **0** | **No** | Fails open | n/a |
| `entitlement_dependencies.require_feature` / `require_quota` | `core/entitlement_dependencies.py:25` / `:97` | **0** real | **No** | Unreachable | n/a |
| `authorize_replay` / `_recovery` | `core/replay_auth.py:264` / `:294` | only each other | **No** | Unreachable | n/a |
| `guard_execution` | `core/pipeline_guard.py:309` | **0** | **No** | Unreachable | n/a |
| `TenantRlsValidator` | `backend/tenant_rls_validator.py:59` | 0 external | **No** | **Cannot deny** — appends results, never raises | n/a |
| `SubscriptionMiddleware` | `core/subscription_middleware.py:20` | scripts only | **No** | Unreachable | n/a |

Installed middleware is exactly six (`main.py`): `SlowAPIMiddleware:608`,
`SecurityHeadersMiddleware:610`, `PrometheusMiddleware:611`,
`MarketplacePaperHttpMetricsMiddleware:617`, `CorrelationIdMiddleware:649`,
`CORSMiddleware:669`. There are zero `@app.middleware` decorators.

### Why `validate_safety_config_integrity` could never have worked
Three independent reasons: it compares `VYOMQUANT_MODE` against `"live_trading"` while
the real value is `"live"` (`main.py:238`, `core/feature_flags.py:41`,
`safety_config.py:187` all use `"live"`; `"live_trading"` is the `SystemMode` enum value —
a namespace mix-up); the whole body is gated on `ENV == "production"` with a default of
`"development"`; and `PRODUCTION_ROUTER_ENABLED` is read with a default of `"true"`, which
fails open. This is the same confusion already fixed and documented in
`core/cache/redis_manager.py`, where the old check compared a trading mode against
`"production"`.

### NEW — `POST /api/portfolio/close-all` is dead on a broken import
`routers/portfolio.py:307` imports `backend_app.backend.safety_config`, which does not
exist (only `core/safety_config.py`). The import precedes the `try` at `:334`, so the
route raises `ModuleNotFoundError` on every call, taking its freeze check (`:322`) and its
`ExecutionGuard` (`:353`) with it. Fails closed — no money moves — but it is an outage and
it silently removes one of the three `ExecutionGuard` sites.

---

## 3. EXCHANGE-WRITE PATHS

Only four call sites can write to a real exchange:

| Write | file:line | Via gateway? | Reachable |
|---|---|---|---|
| `create_order` | `backend/exchange_executor.py:1043` | **Yes** | Yes — the canonical path |
| `cancel_order` | `backend/exchange_executor.py:1109` | Yes | Yes |
| `create_order` | `backend/transactional_execution_manager.py:1072` | **No** | **Dead** |
| `cancel_order` | `backend/transactional_execution_manager.py:1024` | **No** | **Dead** |

`place_order` is called from exactly one place in the repository
(`core/execution_engine.py:994`) and self-defends: `verify_and_consume_token`
(`exchange_executor.py:709`) raises `"Bypass attempt detected"` at `:729` unless the token
minted at `execution_engine.py:984` is present.

`transactional_execution_manager`'s writes are confirmed dead three ways: no caller for
`execute_order`/`cancel_order`; its only construction site is
`exchange_websocket_listener.py:343`, which nothing starts (independently asserted by
`tests/test_reconciliation_worker_claims_match_behaviour.py:391`); and `exchange_adapter`
is never supplied, so it would raise before the write. **It is a landmine, not a live
bypass** — one `get_transactional_execution_manager(db, tenant, adapter)` call from a
route would arm it. Deleting it is the highest-value cleanup.

Second doors into `place_order` with no callers: `place_order_with_idempotency:1190` and
`place_order_and_wait_confirmation:1258`, blocked today only by the token check.

All retry/reconcile/recovery paths are read-only (`exchange_reconciliation.py`
`fetch_open_orders:219`, `fetch_order:347`, `:647`; `order_watchdog.py` status polling).
`idempotent_exchange_submission.py:375` is explicitly sealed with
`RISK_EVALUATION_REQUIRED`.

**Structural finding (TRADE-05, OPEN)** Risk is a property of **one caller**, not of the
gateway. `execute_trade` hardcodes `source="bot_runner"`
(`core/execution_engine.py:1958`), so any future caller inherits the gateway's
authorisation **without** inheriting `validate_trade_request`. Two such callers already
exist (`distributed_execution/execution_worker.py:128`,
`idempotent_exchange_submission.py:391`), both currently neutered. Documented in-code at
`execution_engine.py:898-920`.

---

## 4. PAPER-TRADING ISOLATION

Isolation is four layers, not one flag:

1. `paper_trading` is a required keyword-only constructor argument, validated
   `isinstance(..., bool)` with a `ValueError` otherwise (`master_executor.py:89`,
   `:117-124`).
2. **The real exchange client is never constructed and credentials are never decrypted in
   paper mode.** `vault.load_decrypted_keys` is at `:227`, *after* the paper branch returns
   at `:224`, and `get_exchange_executor` is a function-local import at `:267` inside the
   live branch.
3. The declared-simulator guard is the **first statement** of `execute_with_idempotency`
   (`:571-594`), routing to `assert_simulation_environment_matches`, which admits exactly
   one value — the engine's own.
4. Branch-point agreement refusals (`:861-891`) refuse both a PAPER engine holding an
   executor **and** a LIVE engine with none. The second direction matters as much: it
   would otherwise simulate a fill and report a live order as executed.

**NEW GAP (PAPER-01, OPEN)** `BotRunner` paper fills are simulated inside
`ExecutionEngine` against the synthetic `{"total_equity": 10000.0}` set at
`master_executor.py:205`, and are **never written** to `paper_accounts` / `paper_orders` /
`paper_fills` / `paper_positions` — `master_executor` has no import edge to
`backend/paper/` at all. The platform has two paper engines sharing one word, and a
trader who deploys a strategy in paper mode gets simulated executions their paper account
does not show. Documented in-code at `master_executor.py:612-626`; **no test pins it.**
This is a correctness/UX defect, not a safety one.

---

## 5. OPEN FINDINGS

| ID | Severity | Finding | Status |
|---|---|---|---|
| **TRADE-04** | **P0** | The live-trading gate is not on the order path; live is held off by configuration and entitlement, not by code | **OPEN** |
| **WORK-03** | P1 | Cross-pod duplicate `start_bot`. `FleetManager._active_fleet` is process-local; the new reaper can redeliver. `uq_strategy_deployments_one_live` constrains the deployment row, not this path | **OPEN** |
| TRADE-05 | P1 | Risk is a property of one caller, not of the gateway | OPEN |
| OBS-01 | P1 | `/health/ready` checks nothing; `/api/system/readiness` and `/api/system/dependencies` return hardcoded `"HEALTHY"`; `health()`'s `all_ok` ignores Redis; `/health/services` reads a boot-time-only snapshot | **OPEN — not remediated** |
| QUOTA-01 | P1 | `HardQuotaEnforcer`'s 15 caps are never invoked | OPEN |
| TENANT-01 | P1 | The entire tenant-isolation dependency family is unwired; nothing sets `request.state.tenant` or `app.current_tenant_id` | OPEN |
| API-04 | P1 | An unreachable rate-limit store is a hard boot failure for the whole API | OPEN — needs an availability decision |
| PORT-01 | P2 | `POST /api/portfolio/close-all` always 500s on a nonexistent import | OPEN |
| AUTH-03 | P2 | `require_aal2` not applied to billing/payment mutations its docstring names | OPEN |
| PAPER-01 | P2 | BotRunner paper fills never reach the paper ledger | OPEN |
| TRADE-03 | P2 | Un-guarded `create_order` in `transactional_execution_manager` (dead, but a landmine) | OPEN |
| MONEY-05 | P2 | `referral_commissions.subscription_tier` is `NOT NULL`, so an unknown-tier payment cannot be credited | OPEN (pre-existing) |

---

## 6. NOT COMPLETED

Stated plainly rather than implied.

| Task | Status |
|---|---|
| 9 — health/readiness correctness | **NOT COMPLETED.** Defects fully characterised (OBS-01 above, with line numbers); no code change made, no tests written |
| 10 — unwired-control sweep | **COMPLETED** (section 2) |
| 11 — trading-path re-trace | **COMPLETED as analysis** (section 3). No new tests written |
| 12 — paper-isolation verification | **ANALYSIS COMPLETED** (section 4). The live-gate re-run was **superseded** by section 0: re-running `verify_live_trading_off.py` would re-verify the wrong control |
| WORK-03 | **NOT COMPLETED.** Remains OPEN. No migration written, no concurrency tests |
| 13 — full regression | **NOT RUN.** Only targeted slices were run; counts above are per-file, not a suite total |

---

## 7. TEST RESULTS RECORDED THIS PHASE

Per-file, passed/failed stated separately. These are **not** a full-suite result.

| File | Result |
|---|---|
| `tests/test_bug057_listing_moderation_authz_postgres.py` | 40 passed |
| `tests/test_api01_strategies_update_allowlist.py` | 64 passed |
| `tests/test_auth01_require_aal2_is_wired.py` | 16 passed |
| `tests/test_money01_referral_commission_idempotence_postgres.py` | 17 passed |
| `tests/test_money02_billing_amount_precision.py` | 49 passed |
| `tests/test_work01_command_durability.py` | 22 passed |
| `tests/test_obs04_worker_heartbeat_is_written.py` | 15 passed |
| `tests/test_api03_rate_limit_storage_is_fail_closed.py` + 3 companions | 131 passed |
| Pre-existing worker/expiry/training/mode-propagation regression | 143 passed, 1 xfailed |

No failures were left outstanding in any file listed.

---

## 8. METHOD NOTE — WHY DETECTORS ARE AST-BASED

Three times in this phase a substring scan in a regression test produced a false positive
by matching the explanatory comment that quotes the defective code verbatim. Every
detector is now `ast`-based, and the money-precision test carries a **negative control**
that points the same detectors at a synthetic reconstruction of the pre-fix source to
prove they still fire. That control immediately caught a real defect in one of my own
helpers. The same principle produced section 0: an assertion of absence is worthless
unless the detector can see the thing when it is present.


---

# PHASE 3 — CRITICAL SECURITY REMEDIATION

Appended, not rewritten. Section 0's correction stands as the historical record of a
wrong conclusion; this section records what was done about it.

## TRADE-04 — FIXED. There is now a gate on the order path.

### The design, and why it is not another flag

A flag can be read in the wrong place — that is precisely what happened. So the control
is an **artefact that must be produced and presented**, not a boolean that must be
remembered:

* `backend_app/core/live_execution_gate.py` defines `REQUIRED_CHECKS` (13 names),
  `authorize_live_order(...)` which refuses to issue unless the **complete** set is
  supplied, and `require_live_authorization(...)` which is the gate.
* The execution gateway performs the checks, names them, and mints an HMAC-signed
  `LiveOrderAuthorization` bound to one order: `execution_id`, `tenant_id`, `strategy_id`,
  `symbol`, `side`, `size` and `exchange_id` are all covered by the signature.
* `CCXTExchangeExecutor.place_order` — the one method in the repository whose body calls
  `self._exchange.create_order` — calls `require_live_authorization` and **consumes** it,
  so one authorisation cannot cover two orders.

The inversion matters: a flag read in the wrong place fails open; a **missing artefact
fails closed**. And because the signature covers the check names, the set is re-verified
at the boundary rather than only at issue time — so a valid signature over a partial set
is still refused.

A separate secret from the existing anti-bypass token's, because the two artefacts answer
different questions ("did this come through the gateway?" versus "was this account
permitted to trade real money?") and one secret would let a holder of one mint the other.

### Why the boundary is `place_order`

It is the last point before the irreversible external side effect, and it already hosts
the codebase's existing self-defence (`verify_and_consume_token`). Both are now checked
there, and the test
`test_a_token_without_a_live_authorization_still_cannot_reach_the_venue` proves they are
independent: holding a valid bypass token is not sufficient.

### TRADE-05 — FIXED in the same change

`execute_with_idempotency` now takes `risk_approved` / `risk_reason` and **refuses a live
order whose verdict is not `True`**. Risk is therefore a property of the gateway, not of
one caller.

`BotRunner._inner_loop` passes `risk_approved=(verdict == RiskVerdict.APPROVED)` — the
comparison rather than a literal, so the evidence stays tied to the actual evaluation.
`execute_trade` does **not** forward a verdict, so the second door the audit flagged can
no longer place a live order. That is asserted directly by
`test_execute_trade_cannot_place_a_live_order`.

### The test, and why it is shaped differently from the one it replaces

`tests/test_trade04_live_order_gate_at_venue_boundary.py` — **31 passed**.

The withdrawn verification called one function. This one drives the real chain and the
oracle is **whether a fake exchange client's `create_order` was called**:

```
ExecutionEngine.execute_with_idempotency
  -> _execute_trade_internal (live branch)
    -> CCXTExchangeExecutor.place_order   <- the REAL method body
      -> self._exchange.create_order      <- the recorder
```

`CCXTExchangeExecutor` is built with `object.__new__` and its attributes set directly, so
nothing connects **and the method under test is the genuine one**. A hand-written
stand-in would have reproduced the original mistake: it would pass whether or not the gate
exists.

| Case | `create_order` called? |
|---|---|
| **every prerequisite valid (positive control)** | **YES** — mandatory, or every row below is unproven |
| risk not evaluated | no |
| risk explicitly rejected | no |
| kill switch active | no |
| source is not `bot_runner` | no |
| strategy not authorised | no |
| PAPER order on a LIVE engine | no |
| equity unreadable | no |
| notional above the ceiling | no |
| `place_order` called directly, no gateway | no |
| valid bypass token but no authorisation | no |
| authorisation already consumed | no (second order refused) |
| authorisation for a different order/size | no |
| forged signature | no |
| **any one of the 13 required checks dropped** | no — parametrised, each individually |
| hand-assembled partial set with a valid signature | no |
| `execute_trade` (the second door) | no |

Plus a negative control (`test_the_recorder_records`) proving the oracle works, so
`calls == []` is evidence rather than an artefact of a broken fake.

### Pre-existing tests updated, not disabled

A mandatory gate is a breaking change for anything that previously reached the live path.
Five tests failed and were **updated to satisfy the stronger precondition**, with the
reason recorded in each:

* `tests/test_live_orders_are_bounded_by_account_equity.py` (3 cases) — drives
  `_execute_trade_internal` directly, so `_live_engine` now sets the risk verdict
  explicitly. Without it these tests would have passed for the wrong reason and stopped
  measuring the notional ceiling. The file's docstring claim that the gateway "contains no
  risk evaluation at all" is now marked as superseded rather than deleted.
* `tests/test_simulated_orders_reach_the_simulator.py` (2 ADMITTED live rows) — same
  reason; the precondition is set for every row so the matrix keeps measuring exactly one
  variable.

**Verification:** `staging/exec_regress_RUNP5.txt` — **245 passed, 0 failed** across the
nine execution/environment/paper-separation files. No collateral damage elsewhere:
`staging/collateral_RUNR7.txt` (sandbox lifecycle) **268 passed**;
`staging/collateral_RUNS8.txt` (crash recovery, exchange e2e, block specs, ccxt
compatibility, credential redaction) **181 passed**.

## PORT-01 — FIXED

`routers/portfolio.py` imported `backend_app.backend.safety_config`, which does not exist;
the module is `backend_app.core.safety_config`, which `routers/orders.py` already imports.
The import preceded the `try`, so `POST /api/portfolio/close-all` raised
`ModuleNotFoundError` on every call and its freeze check and `ExecutionGuard` were
unreachable — silently reducing the repository's three `ExecutionGuard` sites to two.

`tests/test_port01_close_all_route_is_reachable.py` — **7 passed**. It asserts more than
"the import works": that the wrong module still does **not** exist (so the fix was not to
create a second copy), that the symbol is the canonical one, that the freeze check and
guard are still real calls, that `reduce_only=True` is preserved, and that three
`ExecutionGuard` construction sites are reachable again.

## MONEY-05 — FIXED, with the policy stated explicitly

`referral_commissions.subscription_tier` is `varchar NOT NULL`, so an empty `item_key`
made the insert inside `process_referral_commission` fail. The call is wrapped in
`except Exception`, so the commission was logged as failed and silently never existed.

**The decision, and it is deliberately the opposite of MONEY-04's.** The question is
whether the missing fact changes the amount owed:

* **MONEY-04, currency unknown → REFUSE.** The currency determines the amount; crediting
  anyway overstated it ~83×. There is no safe value to substitute.
* **MONEY-05, tier unknown → CREDIT, with the tier marked `'unknown'`.** The function
  computes a **flat** percentage of the payment; the tier is descriptive and does not
  enter the arithmetic. The referrer is owed exactly the same money either way, so
  refusing would take money from them to avoid an imprecise label. Dropping it was the
  defect, not the cautious option.

The sentinel is queryable (`WHERE subscription_tier = 'unknown'`) and the omission is
logged at `error` stating that the **amount is correct** and only the label needs
reconciling. Verified: **66 passed** across the two money files
(`staging/money05_RUNW3.txt`).

### A method note, fourth occurrence

Adding the MONEY-05 comment broke
`test_razorpay_branch_does_not_call_the_commission_rpc`, because that test located the
Razorpay section by `source.index("MONEY-04")` and the new comment refers to MONEY-04 by
name when explaining the contrast. The slice then began in the Stripe branch and swept up
Stripe's legitimate call.

That is the **fourth** false positive text matching has produced in this remediation. Both
marker-slicing tests in that file are now scoped by AST enclosing-function instead, which
is the property actually being asserted.

## Phase 3 test results

| File | Result |
|---|---|
| `tests/test_trade04_live_order_gate_at_venue_boundary.py` | 31 passed |
| `tests/test_port01_close_all_route_is_reachable.py` | 7 passed |
| All 10 remediation files together (`staging/remediation_all_RUNX4.txt`) | **293 passed, 0 failed** |
| Execution suite + the gate (`staging/exec_regress_RUNP5.txt`) | 245 passed, 0 failed |
| Sandbox lifecycle (`staging/collateral_RUNR7.txt`) | 268 passed |
| Crash recovery + exchange/ccxt/redaction (`staging/collateral_RUNS8.txt`) | 181 passed |

## Still open after phase 3

| ID | Sev | Finding | Status |
|---|---|---|---|
| WORK-03 | P1 | Cross-pod duplicate `start_bot` | **OPEN — not attempted this phase** |
| TENANT-01 | P1 | Tenant-isolation dependency family unwired | **OPEN — not attempted** |
| QUOTA-01 | P1 | `HardQuotaEnforcer`'s 15 caps never invoked | **OPEN — not attempted** |
| OBS-01 | P1 | Health/readiness correctness | **OPEN — not attempted** |
| API-04 | P1 | Rate-limit store unavailability is a hard boot failure | OPEN — availability decision |
| AUTH-03 | P2 | `require_aal2` absent from payment mutations | **OPEN — not attempted** |
| PAPER-01 | P2 | BotRunner paper fills never reach the paper ledger | **OPEN — not attempted** |
| TRADE-03 | P2 | Dead un-guarded `create_order` landmine | **OPEN.** Note it is now *less* dangerous: `exchange_executor.place_order` is gated, but `transactional_execution_manager` has its own `create_order` call and does **not** go through it, so the landmine is unchanged in kind |

**Full regression (instruction 17) was NOT run.** Every count above is a named subset with
a fresh nonce. No suite-wide `COLLECTED / PASSED / FAILED / SKIPPED / XFAILED` figure
exists, so none is reported.

## Classification after phase 3

```
LOCAL BASELINE:   AMBER
MARKETPLACE:      GO
MONEY:            GO
TRADING PAPER:    NOT CLASSIFIED
TRADING LIVE:     OFF / NOT CERTIFIED
PRODUCTION:       NOT CLASSIFIED
PAPER-ONLY:       NO-GO
```

`PAPER-ONLY: NO-GO` — WORK-03, TENANT-01 and QUOTA-01 are unresolved, and instruction 19
forbids GO while any of them is. Fixing the gate made the architecture materially safer;
it is **not** live certification, and live remains OFF / NOT CERTIFIED.


---

# PHASE 4 — TENANT ISOLATION, SECOND WRITE PATH

Appended. Phase 3's record stands.

## TENANT-01 — DOWNGRADED P1 → P2, NOT CLOSED

Full analysis: **`staging/TENANT_ISOLATION_MAP.md`** (required deliverable).

### The decision: do NOT install `TenantMiddleware`; deprecate the three unwired modules

The brief said not to replace a working ownership model with an invented tenant model
without proving equivalence. Measuring first changed the conclusion.

**Why not to wire it:**

1. **There is no tenant above the user.** `core/dependencies.py:318` resolves
   `tenant_id = … or user_id`, so `tenant_id == user_id` in practice. The framework models
   a hierarchy the product does not have.
2. **`core/tenant.py` IS live production code** — imported by `backend/dag_worker.py:27`,
   `core/hard_quota_enforcer.py:34`, `core/entitlement_engine.py:23`,
   `core/dag_task_queue.py:14`, `backend/ml_training_policy.py:134`. Only
   `tenant_middleware.py`, `tenant_db_context.py` and `entitlement_dependencies.py` have
   zero production importers. "Delete the framework" must not touch `core/tenant.py`.
3. **Installing it would close nothing.** The real gap is the owner-path RLS bypass.
   `TenantMiddleware` sets `request.state.tenant`; it does **not** call
   `set_config('app.current_tenant_id', …)`. Only `tenant_db_context.py` does, and that is
   a session helper, not middleware.
4. `tenant_middleware.py:57-59` documents a CVE-class `startswith` auth-skip bug it
   already had. Adding a second identity path means a second set of those.

### Correction to the audit's characterisation

The audit treated `orders`/`positions`/`fills`/`execution_records` as having only the
inert `current_setting('app.current_tenant_id')` policy. The **live catalogue** shows
three policies each (`staging/tenant_probe_RUNZ6.txt`, `staging/authuid_RUNB8.txt`):

```
orders_owner_access            FOR ALL TO authenticated  USING auth.uid()::text = tenant_id::text
orders_service_role            FOR ALL TO service_role   USING true
orders_tenant_isolation_policy FOR ALL TO PUBLIC         USING tenant_id = current_setting('app.current_tenant_id')
```

Permissive policies are OR'd, so the `auth.uid()` policy governs a request and the inert
one contributes nothing. **They were never unprotected on the request path.**

Five tables *do* rely solely on the inert predicate — `dag_tasks`, `idempotency_keys`,
`transaction_records`, `transaction_checkpoints`, `reconciliation_mismatches`. Because
nothing sets that setting they match **no row**, so this **fails closed**. Pinned by
`test_no_table_relies_solely_on_the_inert_tenant_setting` so the list cannot grow.

### The negative matrix — measured, not reasoned

`tests/test_tenant01_cross_tenant_matrix_postgres.py` — **11 passed**.

Real PostgreSQL. Impersonates a PostgREST request exactly as the deployment does:
`SET LOCAL ROLE authenticated` + `set_config('request.jwt.claim.sub', …, true)`, because
`auth.uid()` here is `NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid`.
No FakeSupabase.

**A 403 is never accepted as evidence.** Every negative case asserts zero rows affected
**and** re-reads the victim row **as the table owner** to prove no mutation landed.

Result (`staging/tenant_matrix_RUNH5.txt`): **13 tables probed, 0 cross-tenant leaks** for
read, update and delete:

```
billing_invoices  exchange_keys  fills  library_strategies  notifications
orders  paper_accounts  paper_sessions  payment_methods  positions
risk_settings  security_logs  strategies          -> ALL DENIED
```

Three controls make that meaningful: a **positive** control (A can read its own rows), a
**weakened-policy** control (a scratch table with `USING (true)` must be reported as a
leak), and an **RLS-off** control. Without them, a database denying everything would look
perfectly isolated.

16 of 29 target tables were not probed and **the reason is reported for each** — mostly
second-order FKs needing a chained parent row. That is a coverage gap in the test, not a
finding about the product.

### Why it is NOT closed

* The **owner-path RLS bypass** is real and unaddressed: `FORCE ROW LEVEL SECURITY` is set
  on **zero** tables and the SQLAlchemy connection is the table owner, so on that path no
  policy applies and application `WHERE user_id = …` is the whole story. Do not enable
  `FORCE` before wiring the setter — `core/tenant_db_context.py:4-20` explains that doing
  so turns every owner-path read into a silently empty result set.
* `api_key_vault` reaches encrypted credentials through a service-role client whose only
  isolation is a Python argument.
* Billing webhooks are the one caller-supplied identity reaching a privileged write; the
  control is HMAC + IP allowlist, so a signing-secret compromise is a write-any-tenant
  primitive.
* 16 tables unprobed.

The seven prioritised follow-ups are in `TENANT_ISOLATION_MAP.md` §8. **None is
implemented.**

## TRADE-03 — FIXED (gated, not deleted)

Deletion was the brief's preference but I could not prove obsolescence to the standard
that justifies removing transaction machinery, so I took the stated fallback: **make it
impossible to activate without the same gate.**

`_execute_on_exchange` and `_cancel_on_exchange` in
`backend/transactional_execution_manager.py` now call `require_live_authorization` before
touching the adapter. With no gateway on that path to mint an authorisation, both refuse
unconditionally today. If the path is ever legitimately reinstated it must route through
`ExecutionEngine.execute_with_idempotency`, and it will then work unchanged.

The cancel half is gated too. The argument for leaving it open ("cancelling is always
safe") is the argument that leaves an unauthenticated venue-write method in the codebase,
and cancelling arbitrary orders by id on an account you do not own is a cross-tenant
action, not a safety measure.

`tests/test_trade03_second_write_path_is_gated.py` — **8 passed**. It arms the manager
**fully** (real adapter, passing validation, a recording exchange client) and asserts the
write is still refused; that the refusal precedes any use of the adapter; that a mismatched
or consumed authorisation is refused; and — the positive control — that a **correct**
authorisation does let the write through, so the gate is not a brick.

### The authoritative venue-write inventory

`test_every_venue_write_in_the_repository_is_accounted_for` sweeps `backend_app/` by AST
for `create_order`, `create_limit_order`, `create_market_order`, `cancel_order`,
`cancel_all_orders`, `edit_order` and fails on any unclassified site.

Its first run reported **four previously unknown call sites**. All four are **false
positives** — the method names collide with CCXT's:

| Site | Receiver | Disposition |
|---|---|---|
| `core/reconciliation_engine.py:754`, `:756` | `self.state_service` | writes local reconciliation state |
| `core/database_scaling.py:617` | `self.order_repo` | a database repository write |
| `core/order_state_engine.py:745` | `self` | self-call to its own state machine; the code says "Simulated cancel" |

The sweep now classifies by **receiver**, not by file, so the same method name on a real
exchange client in one of those files would still be caught.

**The real external trading side-effect boundaries, complete:**

| # | Site | Gate |
|---|---|---|
| 1 | `backend/exchange_executor.py:1043` `create_order` | **TRADE-04 gated** (canonical) |
| 2 | `backend/exchange_executor.py:1109` `cancel_order` | canonical cancel path |
| 3 | `backend/transactional_execution_manager.py` `create_order` | **TRADE-03 gated** |
| 4 | `backend/transactional_execution_manager.py` `cancel_order` | **TRADE-03 gated** |

Nothing else in `backend_app/` can reach a venue.

## Phase 4 evidence

| Scope | Result | Artifact |
|---|---|---|
| TENANT-01 matrix | 11 passed, **0 leaks across 13 tables** | `staging/tenant01_RUNG4.txt`, `staging/tenant_matrix_RUNH5.txt` |
| TRADE-03 | 8 passed | `staging/trade03_RUNK7.txt` |
| Execution + gate + tenant + port01 regression | **229 passed, 0 failed** | `staging/phase4_regress_RUNL8.txt` |
| Full-suite collection | **13,326 tests collected, 0 collection errors** | `staging/collect_RUNM9.txt` |

## Not attempted in phase 4

WORK-03, QUOTA-01, OBS-01, API-04, AUTH-03, PAPER-01. All remain **OPEN**, unchanged.


## Full regression — NOT COMPLETED

| Metric | Value |
|---|---|
| **COLLECTED** | **13,326** |
| Collection errors | **0** |
| PASSED | not obtained |
| FAILED | not obtained |
| SKIPPED | not obtained |
| XFAILED | not obtained |

Command (nonce `FULLREG2`, artifact `staging/fullreg_raw_FULLREG2.txt`):

```
$env:AERORA_TEST_DATABASE_URL="postgresql://postgres:@127.0.0.1:62674/postgres"
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider -o addopts="" --timeout=120 -rN
```

Collection succeeded cleanly: **13,326 tests, zero collection errors** (verified
separately, `staging/collect_RUNM9.txt`). Execution was launched and was progressing
normally — roughly 1% complete with skips, xfails and xpasses appearing but no failures
visible — when the session's available execution window ran out. A suite of this size
needs hours.

**No suite-wide PASSED / FAILED / SKIPPED / XFAILED figure is reported**, because none was
obtained. Per the brief, targeted-subset totals are not substituted for a full regression.
The subset totals recorded throughout this report are exactly that: named subsets with
fresh nonces.

The prior-phase suites that were **not** re-run in phase 4 — PostgreSQL batches,
marketplace batches, money, auth, worker crash/recovery, rate limiter, paper accounting,
quota concurrency, health/readiness — remain at their last recorded results and are **not**
evidence about the current tree.

Redis-dependent tests: **still not run with a real Redis.** `tests/test_webhook_duplicate_delivery_real_redis.py`
previously reported 6 skipped with no Redis running. Those skips are **not** counted as
passes anywhere in this report.


---

# PHASE 5 — WORK-03, QUOTA-01, OBS-01, API-04

Appended. Phases 3 and 4 stand, including the withdrawn live-trading conclusion.

Two of the four P1s closed outright; two downgraded to P2 with measured evidence that the
prescribed remediation would have made things worse.

## WORK-03 — CLOSED

### The invariant, and why process-local state could not provide it

`FleetManager.start_bot` refused a duplicate with `if bot_key in self._active_fleet`, a
**per-process dict**. Two pods do not see each other's bots. The WORK-02 XAUTOCLAIM
reaper made that reachable rather than theoretical: a reclaimed `start_bot` command is
delivered to a *different* consumer than the one that first took it.

`uq_strategy_deployments_one_live` already existed but is keyed on `strategy_id` and
constrains the deployment ROW; `FleetManager` neither reads nor writes that table, and the
fleet's logical key is `(user_id, symbol)`.

### The mechanism — migration `025_work03_bot_ownership_claims.sql`

`bot_ownership_claims`, **PRIMARY KEY (user_id, symbol)**. The claim is one statement:

```sql
INSERT INTO bot_ownership_claims (...) VALUES (...)
ON CONFLICT (user_id, symbol) DO UPDATE SET owner_token = EXCLUDED.owner_token, ...
 WHERE bot_ownership_claims.heartbeat_at < now() - make_interval(secs => <stale>)
RETURNING owner_token, (xmax = 0) AS was_inserted
```

PostgreSQL takes the row lock for the ON CONFLICT path and re-evaluates the WHERE after
acquiring it, so of two concurrent claimants exactly one matches. The loser gets **zero
rows** — a deterministic no-op, not an error.

**Why PostgreSQL and not a Redis lock.** `core/cache/redis_manager.py` is deliberately
fail-soft: every method swallows its exception and returns a falsey default. A Redis
outage would make `SET NX` answer "not acquired" for everybody, and a future refactor
returning a truthy default would make it answer "acquired" for everybody. **An
availability dependency must not be able to flip a correctness invariant in the unsafe
direction.** Pinned by `test_redis_being_down_cannot_grant_ownership`, which fails if the
claim ever starts consulting `redis_manager`.

**No permanent lock.** A worker that dies stops heartbeating and the claim becomes
reclaimable after the stale threshold, floored at 3× the heartbeat interval so the
detector cannot manufacture loss mid-heartbeat.

**Ownership is a token.** `owner_token` is regenerated per claim and release/heartbeat are
scoped to it, so a worker that was reaped cannot release its successor's claim when it
wakes up. Without that, a resurrected process would delete a live claim and let a third
worker start a second bot.

### Wired, not merely implemented

`claim_bot` is called in `FleetManager.start_bot` after the cheap local checks and
**before the BotRunner is constructed**; `release_bot` is called on all three exit paths
(`stop_bot`, construction failure, `_cleanup_dead_bot`). A claim that is never released
strands the bot until the stale threshold, so the trader is told "another process owns it"
when nothing does. Asserted by `test_every_exit_path_releases_the_claim`.

The no-store case reuses `runtime_env.local_state_is_acceptable()` — the same detector as
the rate limiter — so a declared local environment falls back to the in-process registry
with a warning, and **anything else refuses**. One definition of "is there a second
process to coordinate with?", pinned by
`test_the_no_store_policy_uses_the_same_detector_as_the_rate_limiter`.

### Evidence — `tests/test_work03_bot_ownership_claims_postgres.py`, **27 passed**

| Case | Result |
|---|---|
| 2 independent connections + barrier | exactly 1 owner |
| 8 independent connections + barrier | exactly 1 owner |
| **2 separate OS processes** | exactly 1 owner |
| **4 separate OS processes** | exactly 1 owner |
| duplicate delivery to a different consumer | 1 claim, idempotent |
| worker dies holding the claim | reclaimable after the threshold |
| 2 workers racing to recover the same stale claim | exactly 1 recovers |
| resurrected worker tries to release | refused, successor's claim intact |
| live claim, heartbeat fresh | not reclaimable |
| heartbeat after ownership loss | returns False, worker stands down |
| different tenants, same symbol | both granted |
| one user, two symbols | both granted |
| claim store unreachable | **refused** (fail closed) |
| `start_bot` with a refused claim | no BotRunner constructed, nothing registered |
| **negative control** | a process-local dict yields **2 owners**, proving the harness detects the defect |

Full design: **`staging/WORK03_CONCURRENCY_MAP.md`**.

## OBS-01 — CLOSED

### Three questions, three answers

* **Liveness** (`/health/live`) — dependency-free, unchanged. A pod whose database is down
  is not a broken pod; restarting it does not fix the database. Pinned by
  `test_liveness_does_not_consult_any_dependency`.
* **Readiness** (`/health/ready`) — now measures required dependencies and returns **503**
  when not ready. A 200 with a sad body is invisible to every load balancer, which is
  precisely why the old version looked harmless and was useless.
* **Dependency health** (`/api/system/dependencies`) — always 200, reports each dependency
  with whether it is REQUIRED. A diagnostic, not a gate.

### Which dependencies are mandatory, and why

| Dependency | Required | Reasoning |
|---|---|---|
| Database | **always** | every authenticated request resolves identity and RLS through it |
| Redis | **outside a declared local environment** | it backs the rate limiter (API-03) and the distributed execution locks — not a cache |
| Telemetry / QuestDB | **no** | degrades to a mock by design; making readiness depend on it turns a metrics outage into a total outage |
| Fleet manager | **no** | in-process state, not something we talk to |

`UNKNOWN` fails **closed**: "we could not tell" is not a reason to take traffic.

### The three other OBS-01 defects

* `health()`'s `all_ok` ignored Redis — the probe ran and its result was **discarded**, so
  Redis being down reported `"ok"`. Now counted, and only when the environment requires it.
* `/api/system/readiness` and `/api/system/dependencies` returned the literal `"HEALTHY"`
  for five things, three of which (`risk_engine`, `execution_engine`,
  `reconciliation_scheduler`) are in-process components rather than dependencies — category
  error on top of fabrication. Removed; worker/service state lives at `/health/services`.
* `/health/services` reads a boot-time-only snapshot, so a service that died an hour ago
  still reported ACTIVE. Refreshing it properly needs those four services to publish
  heartbeats, which they do not. The honest fix available was to **stop presenting boot
  state as live state**: `as_of: "process_startup"`, `captured_at`, `age_seconds` and an
  explicit note now travel with it.

No secrets: probe detail is a class name and a scrubbed 100-char message. Six credential
shapes are tested, because these endpoints are typically unauthenticated.

### Evidence — `tests/test_obs01_readiness_reflects_dependencies.py`, **28 passed**

Through the real routes with `TestClient`, asserting **HTTP status**. Includes a genuine
connection failure against a dead port (not a mocked health response), a hanging probe
hitting the timeout, a **falsey fail-soft Redis ping converted into a failure** (otherwise
an outage reads as UP), recovery, liveness staying 200 while readiness is 503, and a
negative control reconstructing the pre-fix endpoints.

One pre-existing test was **updated, not deleted**:
`tests/test_end_to_end_api_suite.py::test_health_ready_endpoint` asserted
`== {"status": "ready"}` with 200 — it was pinning the defect. It now asserts the
contract: 200 iff READY, 503 iff NOT_READY, status and body agreeing, blocking
dependencies named.

## API-04 — CLOSED AS A DECISION

Full reasoning: **`staging/API04_AVAILABILITY_DECISION.md`**.

**Option A (fail closed) is RETAINED. Option B (degraded mode) is specified and NOT
implemented.**

* Option A is **secure**: an in-memory limiter across N replicas yields an effective limit
  of `configured × N`, silently, and the rate limiter is the only shared abuse control in
  front of authentication, checkout, payment verification and payouts.
* Option A is **unsatisfactory on availability**: refusing at *import* takes the whole API
  down, including `/health/*`, `cancel`, `cancel-all` and `stop_bot` — the risk-REDUCING
  direction. The security argument does not extend to endpoints that reduce risk.
* Option B is strictly better on availability and equal on security, and composes with
  OBS-01 (Redis is already a required readiness dependency, so a degraded pod would leave
  rotation). It is not built because items 2, 3 and 6 of its specification are new
  request-path machinery and item 3 — which endpoints are safety-critical — is a **product**
  decision. Shipping a middleware that classifies endpoints by a list I invented is the
  kind of unreviewed policy this engagement has refused elsewhere.

The policy is **pre-existing**: the original module already raised for
production-without-Redis; the API-03 fix only corrected which environments count as local,
which made it actually fire.

### Evidence — `tests/test_api04_store_failure_after_startup.py`, **12 passed**

The frozen config cannot be mutated to `memory://` after startup; a re-resolve during an
outage refuses rather than degrading; recovery restores the shared store; no trading mode
can reintroduce a per-process limiter; a declared local environment still works; auth and
billing routes carry real per-route limits (so Option A defends something); `/health/live`
carries no limit (a throttled liveness probe is a self-inflicted outage); the Option B
allowlist contains no revenue or credential endpoint; and the decision document exists and
states the cost.

A test bug worth recording: two cases initially reported the decision as broken because
`conftest.py` sets `DEV_MODE=true` session-wide, which makes
`local_state_is_acceptable()` true and masks fail-closed behaviour. The autouse `clean_env`
fixture now clears it — the same fixture the API-03 file already had.

## QUOTA-01 — DOWNGRADED P1 → P2, NOT CLOSED

Full trace: **`staging/QUOTA_ENFORCEMENT_MAP.md`**.

### The audit finding was true but the inference was wrong

"15 `HardQuotaEnforcer` methods with zero call sites" is correct and remains correct. The
inference — that the product's quotas are unenforced — is false. Enforcement lives in
`core/subscription_dependencies.py`, wired as FastAPI `Depends(...)` on the mutation
routes:

| Quota | Enforcement point |
|---|---|
| Strategy count | `strategies.py:1152`, `library.py:2199` |
| **Bots / deployments** | `strategies.py:2012` |
| Backtests | `strategies.py:2860`, `strategy_operations.py:1345`, `:1577` |
| ML training | `strategies.py:2475`, `strategy_operations.py:5930` |
| Optimization | `strategies.py:3472`, `:3541`, `:3601`, `strategy_operations.py:2227` |
| Marketplace publish / subscribe | `library.py:1893`, `:2834` |
| Exchange connections, ML model slots, strategy versions | helper form |

A FastAPI dependency resolves **before the handler body**, so enforcement precedes the
side effect *structurally*, not by convention. And it already **fails closed**:
`core/subscription_dependencies.py:649-654` — *"A figure that could not be read is not a
figure of zero. 503, not a pass."*

### Wiring `HardQuotaEnforcer` would be a defect

1. Its limit source is `TenantQuota` (`core/tenant.py`), a different catalogue from the
   subscription plans the wired path reads — **two sources of truth for one number**.
2. Its tenant model is the one TENANT-01 concluded is vestigial
   (`dependencies.py:318` collapses `tenant_id` to `user_id`).
3. **Decisive:** its usage counting reads the fail-soft Redis facade
   (`scard`, `smembers`, `hget`), so a Redis outage makes every count read **zero** and
   every quota **pass**. That is fail-open beside a fail-closed path.

`test_hard_quota_enforcer_is_still_unwired_and_that_is_the_decision` fails if anyone wires
it, pointing at the reasoning first.

### Concurrency

The safety-critical quota — *can two concurrent requests start two bots* — is closed at
the database level by `uq_strategy_deployments_one_live` **and** WORK-03's
`pk_bot_ownership_claims`, verified under real concurrency including across separate OS
processes.

### Why it is not closed

Real gaps remain, recorded rather than folded into a green tick: order-rate / daily-trade
/ active-order limits are unwired with no equivalent on the wired path (mitigated today
only by live trading being OFF); aggregate capital/leverage/concentration are unbounded
(the gateway bounds one order, not the portfolio); strategy-count and
exchange-connection creation have no DB constraint, so two concurrent requests could both
pass; `HardQuotaEnforcer` is not actually deprecated in code.

### Evidence — `tests/test_quota01_enforcement_is_wired.py`, **17 passed**

Dependencies resolved from the **function signature** by AST, not a substring scan — which
matters, because `core/entitlement_dependencies.py` contains three `Depends(...)`
*docstring examples* that make an entirely unwired module look wired to a text search.
Plus a real FastAPI route proving a refusing dependency means the handler body never ran,
a positive control, and a negative control reconstructing enforce-after-mutate.

## Phase 5 evidence summary

| Scope | Result | Artifact |
|---|---|---|
| WORK-03 | **27 passed, 0 failed** | `staging/work03_W3H8.txt` |
| OBS-01 | **28 passed, 0 failed** | `staging/obs01_OB1B2.txt` |
| API-04 | **12 passed, 0 failed** | `staging/api04_AP4B2.txt` |
| QUOTA-01 | **17 passed, 0 failed** | `staging/quota01_QT1A1.txt` |
| All phase-3/4/5 remediation files | **173 passed, 0 failed** | `staging/phase5_P5A1.txt` |
| Affected pre-existing suites | **112 passed, 0 failed** | `staging/phase5_P5D4.txt` |
| Fleet / worker / execution regression | **125 passed, 1 xfailed, 0 failed** | `staging/w3_regress_W3I9.txt` |

---

# PHASE 5 (CONT.) — WORK-03 WAS REPORTED CLOSED TOO EARLY

The WORK-03 section above was written on the strength of 27 passing tests. **That report
was premature.** Re-reading the module against its own production call sites before
publishing `WORK03_CONCURRENCY_MAP.md` surfaced two defects, and the first is the same
class this engagement was commissioned to find.

## Defect 1 — `heartbeat()` was wired to nothing

`bot_ownership.heartbeat()` was written, unit-tested and correct. It was **called from
nowhere in `backend_app`**. Measured, not assumed:

```
grep -r "bot_ownership" backend_app/**/*.py
  → fleet_manager.py:107  from backend_app.core.bot_ownership import claim_bot, release_bot
  → fleet_manager.py:193  from backend_app.core.bot_ownership import release_bot
  → fleet_manager.py:227  from backend_app.core.bot_ownership import release_bot
```

`claim_bot` and `release_bot`. No heartbeat. The consequence is not cosmetic — it made the
invariant hold for exactly `STALE_AFTER_SECONDS` and no longer:

1. Worker A claims `(user, symbol)` at T0. `heartbeat_at = T0`, never refreshed.
2. At T0+90s the row is stale **by its own predicate**, while A's bot is still trading.
3. Worker B calls `start_bot` for the same pair. B's `_active_fleet` check passes
   (different process); `claim_bot` finds the stale row and **grants** B ownership.
4. A is never told, because nothing calls `heartbeat()`.

Two runners, same account, same pair. The original defect postponed by 90 seconds, not
prevented.

This is CORRECTION-001's shape repeated by the remediation itself: a control that exists,
is correct, and is reached by too little. It is also exactly why the WORK-03 suite already
contained an AST assertion that `start_bot` calls `claim_bot` — the same assertion simply
had not been written for the heartbeat, because at the time of writing there was no
heartbeat to assert.

### What was added

`FleetManager._heartbeat_claim` (`fleet_manager.py:194`), started as a task beside the bot
(`:151`) and carried in the fleet record under `"heartbeat"`. It refreshes through
`bot_ownership.heartbeat_bot` via `asyncio.to_thread`, because psycopg2 is blocking and a
direct call would stall the event loop every other bot in the process shares.

The new `ClaimStoreUnavailable` exception exists so the loop can tell two outcomes apart:

| Outcome | Meaning | Action |
|---|---|---|
| `False` | the token-scoped UPDATE matched zero rows — somebody else owns this bot | surrender now |
| `ClaimStoreUnavailable` | the store could not be reached — *we do not know* | keep running, bounded by `effective_stale_seconds()` of consecutive failures |

Collapsing those into one `False` — which is what `heartbeat(cursor, claim)` does, correctly,
for a caller that can see the exception itself — would stop a healthy bot on a transient
network blip. Ignoring the second would grant indefinite ownership. The bounded tolerance
is the fail-closed resolution: once we have been unable to refresh for longer than the
stale threshold, another worker is *entitled* to take over, so we assume one has.

`_surrender_bot` cancels the runner and **deliberately does not release the claim**.
Release is token-scoped so the DELETE would match zero rows today, but if that scoping
were relaxed, releasing here would delete the **new** owner's claim. Asserted structurally
so the protection does not depend on two files agreeing.

## Defect 2 — the boot-failure path leaked the claim

`_boot_and_run` pops the fleet record **itself** on a boot failure and returns normally.
The done-callback that performs the release then found `None` and did nothing, so the claim
survived with nothing running behind it — the bot was unstartable for a full stale interval
after a failure that left no bot at all.

`test_every_exit_path_releases_the_claim` had asserted over three methods. There were four.
Both the test and the code are corrected; `_cancel_claim_heartbeat` now precedes every
release so the heartbeat cannot refresh a claim about to be deleted.

## Why this is reported as falsified rather than tested

A test that passes against fixed code proves nothing unless it also fails against the
broken code. `staging/falsify_work03_fixes.py` neutralises each fix and checks the oracle
notices:

| Case | Setup | Required observation | Result |
|---|---|---|---|
| A | `release_bot` stubbed to a no-op | the claim row **survives** a boot failure | `rows = 1` — leak detectable |
| B | no heartbeat loop at all | the aged claim is **takeable** | `reclaimed_stale, granted = True` — the gap, reproduced |
| C | heartbeat loop running | the rival is **refused** and the owner keeps running | `held_by_another`, `cancelled = False` — the fix |

**3/3 controls behaved** (`staging/f_W4B2.txt`).

## Corrected WORK-03 evidence

| Scope | Result | Artifact |
|---|---|---|
| `tests/test_work03_bot_ownership_claims_postgres.py` | **39 passed** (was 27; +12) | `staging/f_W4A1.txt` |
| Falsification harness | **3/3 controls behaved** | `staging/f_W4B2.txt` |
| Files affected by the completion fix | **168 passed, 1 skipped** | `staging/f_W4D5.txt` |

The 12 added tests: the unrefreshed-claim negative control, the heartbeated-claim positive
control, takeover reporting, the store-error/takeover distinction, the loop against real
PostgreSQL, loss-stops-the-bot, blip tolerance, prolonged-outage surrender, surrender not
disturbing the successor's row (behavioural **and** AST), the `start_bot` heartbeat wiring
assertion, and the boot-failure release.

Full design, including the state machine, the stale mechanism and the four exit paths:
**`staging/WORK03_CONCURRENCY_MAP.md`**.

> The consolidated figure of **173 passed** (`staging/phase5_P5A1.txt`) in the phase-5
> evidence summary above predates these 12 tests and is superseded by the full-suite
> execution result, not re-reported.

---
---

# PHASE 6 — PAPER TRADING CERTIFICATION

Everything above stands. This part records Phase 6: closing PAPER-01, proving paper/live
isolation, root-causing the 17 remaining regression failures, and auditing environment
precedence for further RUNTIME-01-class fail-opens.

Starting point, from Phase 5: **13,435 collected / 13,046 passed / 17 failed / 363 skipped /
6 xfailed / 3 xpassed.**

## What was fixed

| ID | Finding | Evidence |
|---|---|---|
| **PAPER-01** (P1) | BotRunner paper fills never reached the canonical paper ledger. `master_executor.py` had **zero import edges** to `backend_app/backend/paper/`; the engine's PAPER branch mutated three in-process attributes and wrote no table. | `staging/PAPER_TRADING_CERTIFICATION.md`; 26 + 13 = **39 passed** against real PostgreSQL 16.2 |
| **TEST-02** (P3) | 8 order-dependent failures in `test_task_6_1_backtest_execute.py`. Root cause: orphaned slowapi `Limiter` instances left behind by module re-imports in three other files, so a per-test counter was shared and the target answered 429 `MARKETPLACE_RATE_LIMITED`. | `staging/REGRESSION_ORDER_ISOLATION.md`; the repro order went from **8 failed** to **91 passed** |
| **ENV-02** (P3) | `library`/`referral` `_get_service_client` degraded a **`service_role`** client to `None` on `DEV_MODE` alone, never consulting `ENV`. `ENV=production` + `DEV_MODE=true` measured returning `None`. | `staging/ENVIRONMENT_PRECEDENCE_AUDIT.md`; **55 passed**, falsified **6/6** |
| **ENV-05** (P3) | `main.py`'s `/docs` and `/redoc` took their deployment environment's default from a **trading mode** — the API-03 defect in a module API-03 did not touch. `ENV` unset + `VYOMQUANT_MODE=paper` served them. | same |

## What was found and deliberately NOT fixed

Each with a measured mechanism and a named owner, because an audit that fixes everything it
touches stops being able to tell the difference between a repair and a product decision.

| ID | Finding | Why not fixed |
|---|---|---|
| **PAPER-02** (P2) | An unfundable paper market buy strands an `ACCEPTED`/`OPEN` order with `filled_quantity=0` and `rejection_reason=NULL`, classified `PAPER_INVARIANT_VIOLATION` (500). Requirement 16.5 wants `REJECTED` with a reason. Reachable from `POST /api/paper/orders`. | The choice between "refuse before insert" and "insert as `REJECTED`" is a contract decision for every client polling order status. |
| **LIB-01** (P3) | `_build_service_client`, documented to *"raise 500 if env is not configured"*, returns `None`; callers dereference it; `library.clone_strategy` answers 500 "Failed to look up library entry." whose message names the wrong cause. | Fixing it means either checking `None` at every call site or never returning it — the latter changes every `/api/library` route under the test suite's own environment. |
| **ENV-03** (P3) | `core/config.py:load_dotenv()` resolves **`backend_app/.env`**, not the repository-root `.env`, because `find_dotenv()` walks up from the calling module's directory. That file injects 7 **placeholder** credentials, and `bool('YOUR_SUPABASE_SERVICE_ROLE_KEY')` is `True`, so a required-credential check written as a truthiness test passes on the value it exists to reject. | `.dockerignore` excludes it, so it does not ship. Local/CI-fidelity finding. Reported at length because it means local measurements are shaped by an invisible file. |
| **ENV-04** (P2) | `PRODUCTION_ROUTER_ENABLED` defaults to **`"True"`** in two separate modules, so its absence mounts `execution_router`. The `feature_flags.py` docstring says `False (unverified safety)`; four task definitions say `"False"`; `deployment_go_no_go.md` says `True`. | Flipping the default changes which routes are mounted. Product decision. |
| **API-05** (P3) | `/openapi.json` is served in every environment — `_get_openapi_url()` reads no environment variable, and no task definition sets `ENABLE_OPENAPI`. Production hides `/docs` and publishes the schema `/docs` renders from. | Unmounting it can break a generated client or an API-gateway import. |
| **TEST-01** | Protected-logic containment: a **false positive**. The oracle matches 3-character tokens by plain substring, and `'vie'` is inside the server-side constant `node_preview` in a refusal body. Zero production call sites — measured. | Owner is the marketplace test oracle; the fix is a token-length floor or whole-token matching. |
| **SAST-01** | 18 SQL-interpolation violations, **18/18 in one UNTRACKED file**, 0 in tracked files. All 18 safe in fact; 16 are a validated `column` demoted by the gate's module-wide name arithmetic. Real finding: the gate walks the filesystem, so its local and CI verdicts can disagree. | Owner is the SAST gate (scope) and the local migration's author. |
| **ENV-01** | `test_connection_pooling_safety` makes an **unmocked authenticated call to `api.binance.com`** with the literal key `key1`. It can never pass: with network it authenticates and fails, without network it raises `ExchangeNotAvailable`. | Owner is the exchange-connection tests; the subject needs no network. |
| **TEST-04** | A tenth order-dependent failure, found while verifying the precedence fix: `importlib.reload` of `core.rate_limit` makes `isinstance(config, RateLimitConfig)` false against the pre-reload class. | Does not occur in the repository's own collection order (alphabetical: 52 passed). Fix belongs in the asserting test. |

Full dispositions for the nine pre-existing failures:
`staging/FULL_REGRESSION_FAILURE_DISPOSITION.md` Part 3.

## CORRECTION-005 — my own order-isolation guard was inert on its first version

The conftest guard that resets bound rate limiters was first keyed on the module limiter's
`id()` alone. That meant it scanned once *before* `backend_app.main` was imported, found
nothing, recorded the id, and never scanned again. It was dead code, and the 8 failures
survived unchanged — which is how I noticed. Re-keyed on
`(id(module limiter), id(app), route count)`.

Also recorded in that work: the first discovery attempt used `gc.get_objects()` to find live
`Limiter` instances and raised `MemoryError` on this process. Replaced by walking
`app.routes[*].endpoint.__closure__`, which is where slowapi actually keeps the limiter —
measured: 2 limiters across 351 endpoints, and `endpoint attributes mentioning 'limit': []`.

## CORRECTION-006 — the `.env` fix I reported earlier was applied to a file the app does not load

Earlier in this engagement I reported fixing "`.env` missing `ENV=development`". I edited the
**repository-root** `.env`. `core/config.py` loads **`backend_app/.env`** (ENV-03), and no
module in `backend_app/` passes `usecwd=True` to `find_dotenv`, so the root file is loaded by
essentially nothing in the application. **That edit is inert for `config.py` and the claim as
written was wrong.**

The practical effect happens to be in the safe direction — with `ENV` unset the API-03 rate
limiter refuses to start rather than fail-opening — but a favourable accident is not a fix, and
the record needed correcting rather than resting on it.

## Also corrected: two oracle errors in my own paper tests

Both were mine, both were found by the tests failing, and both turned out to be real
properties of the engine:

1. **Quantization is per position, not per portfolio.** The oracle summed unrounded values and
   rounded once.
2. **`equity == capital + realized_pnl − fees` is NOT an identity** (measured: `100008.48` vs
   `100008.49`), because cash derives from quantized position *values* while `realized_pnl`
   quantizes independently. The false identity was **dropped**, not hidden behind a tolerance,
   and replaced with the per-fill fee invariant the implementation does guarantee exactly.

## Environment casualty worth recording for whoever reproduces this

The foreground command timeout in this tooling delivers a console control event
(`0xC000013A`) to the whole process group. The local PostgreSQL instance shared that console,
so one timed-out test run took the database down mid-test:

```
LOG:  background worker "logical replication launcher" (PID 16752) was terminated by exception 0xC000013A
LOG:  terminating any other active server processes
LOG:  all server processes terminated; reinitializing
LOG:  database system was not properly shut down; automatic recovery in progress
```

Recovery then stalled on a `sharing violation` opening its own log file and left an orphaned
`--forkaux` process holding the data directory. Restarted cleanly; the schema survived intact
(13 paper tables, all append-only and transition triggers, `uq_paper_position_open`, 9+6
seeded transition rows — re-verified). The port changed, which is why
`staging/pg_dsn.txt` was rewritten and the probes that hard-coded it now resolve it.

**Nothing about this is a product finding.** It is recorded so that a stalled run is diagnosed
in minutes rather than mistaken for a test hang, and so that DB-dependent suites are always
launched detached with their own console.

## Net test-count change in Phase 6

**0 tests deleted. 0 marked skip. 0 marked xfail. 0 assertions relaxed.**

| File | Before | After | Delta |
|---|---|---|---|
| `tests/test_paper01_botrunner_fills_reach_the_ledger_postgres.py` | — | 26 | **+26** |
| `tests/test_paper01_paper_live_isolation.py` | — | 13 | **+13** |
| `tests/test_environment_precedence_is_centralised.py` | — | 55 | **+55** |
| `tests/test_paper_no_random.py` (parametrised per paper module) | 59 | 64 | **+5** |
| `tests/conftest.py` (order-isolation guard) | — | — | 0 (fixture only) |
| **Total** | | | **+99** |

## Phase 6 final regression

**13,534 collected / 13,082 passed / 9 failed / 434 skipped / 6 xfailed / 3 xpassed** in
3282.70 s (`staging/fullreg_raw_FULLREG_P6B.txt`, junit `staging/fullreg_FULLREG_P6B.xml`).

9 + 13,082 + 434 + 6 + 3 = 13,534 = collected; junit `tests=13534 failures=9 errors=0`.

The 9 are exactly the 9 dispositioned pre-existing failures. The 8 order-dependent failures
are **absent**, confirming TEST-02 closed at full-suite scale.

The +99 collected is accounted for id-by-id by diffing the two junit XMLs
(`staging/probe_collected_delta.py`): 100 ids added (55 + 26 + 13 + 5 + 1), 1 removed (a
renamed parametrised id in the same file). The +71 skipped is the three
`AERORA_TEST_DATABASE_URL`-gated files, which this run deliberately cleared to match the CI
unit-test lane and which were measured separately in the PostgreSQL lane.

**The first Phase 6 run had 11 failures.** One extra was mine — PAPER-03, the paper package's
pinned dependency list catching the new edge the PAPER-01 seam introduced — and is fixed. The
other, TEST-05, is a Hypothesis coverage census that passed in the second run. Both are
documented in `staging/FULL_REGRESSION_FAILURE_DISPOSITION.md` Part 4 rather than quietly
dropped between runs.

---

# PHASE 7 — PAPER LAUNCH BLOCKERS

> A new section, not a rewrite. Everything above is the Phase 1–6 record and is left as
> written, including the entries that list PAPER-02, LIB-01, ENV-01, ENV-03, ENV-04, ENV-06
> and API-05 as open. Those entries were true when written; this section is the current
> state.

---

## What this phase closed

| ID | Was | Now | Evidence |
|---|---|---|---|
| **PAPER-02** | P2 — an unfundable market buy strands an `ACCEPTED`/`OPEN` order, `rejection_reason=NULL` | **FIXED** | 16 real-PostgreSQL tests; 10/10 two-process races |
| **LIB-01** | P3 — `clone_strategy` answers 500 naming the wrong cause | **FIXED** | 17 real-PostgreSQL tests; validation sweep green |
| **LIB-03** | not previously known | **FIXED** | `.single()` made two 404 branches unreachable |
| **ENV-01** | P3 — unmocked authenticated request to `api.binance.com` from the unit lane | **FIXED** | 36 passed, no network, no credential |
| **ENV-03** | P3 — placeholder credentials satisfy every check; the wrong `.env` wins | **FIXED** | 170 passed |
| **ENV-04** | P2 — flag absence mounts the execution router; nine sources disagree | **FIXED** | 45 passed, nine-cell matrix |
| **ENV-06** | P3 — Sentry's `environment` tag is the trading mode | **FIXED** | 12-cell matrix + two AST sweeps |
| **API-05** | P3 — `/openapi.json` served in every environment | **FIXED** | 60-cell environment matrix |

New findings raised and **reported, not fixed**: **PAPER-04**, **PAPER-05**, **LIB-02**,
**CONN-01**, plus a second uncalled validator (`config.validate_production_config`).

Per-finding detail lives in the Phase 7 addenda of
`staging/PAPER_TRADING_CERTIFICATION.md` (PAPER-02/04/05),
`staging/AUTH_RLS_AUDIT.md` (LIB-01/02/03, API-05),
`staging/ENVIRONMENT_PRECEDENCE_AUDIT.md` (ENV-01/03/04/06, API-05, CONN-01) and
`staging/TRADING_SAFETY_AUDIT.md` (ENV-04, TRADE-06).

---

## The pattern that held for a seventh phase: measuring first changed the fix

Three times in this phase the obvious repair was the wrong one, and only measurement said
so. Recorded in full because it is the method, not an anecdote.

### 1. PAPER-02 — reordering the writes would have deleted a recovery mechanism

The natural fix for "a conflict leaves an orphaned fill row" is to move `bump_version` ahead
of `insert_fill`, so money moves before the fill row exists. The evidence refused it: the
fill row is a **documented idempotency anchor**. `paper_simulator` records that *"money moved
before the fill row exists could be applied twice"*, and the ordering is pinned by
`tests/test_paper_order_lifecycle_writes.py::test_a_conflicting_retry_resumes_the_fill_row_rather_than_writing_a_second_one`
and by `tests/property/test_paper_confluence.py`.

Reordering would have traded a **reporting** defect for a **double-spend** window.

What *was* wrong was a different divergence in the same function: the default-account path
wrote `fill → position → money → ledger` where `paper_simulator.apply_fill`'s canonical order
is `fill → money → ledger → position`. That left a **position** row orphaned on a conflict — a
phantom holding that breaks the equity identity. The fix aligned the order with the existing
canonical path. Measured before and after by forcing a stale version read
(`staging/probe_paper02_phantom.py`): `positions=1` → `positions=0`, with
`stale_version_reads=6` proving the retry-and-resume path ran and did not write a second
fill.

### 2. ENV-04 — flipping the code default would have changed nothing locally

`PRODUCTION_ROUTER_ENABLED` defaulted to `"True"` in two modules, so the report said the fix
was to flip the default. Probing the real route table showed the default was not what mounted
the router on this machine at all:

```
staging/probe_env04_mount.py unset     (variable popped from os.environ)
  PREDICATE=True
  EXECUTION_SIGNAL_MOUNTED=True
```

`backend_app/.env` — git-ignored, and loaded by `core/config.py` **before** `safety_config`
reads the flag — carried `PRODUCTION_ROUTER_ENABLED=True`. Had I only flipped the default,
the local lane would have gone on logging
`INFO:Algo22: Production execution router ENABLED` while this report claimed the opposite.
ENV-03 and ENV-04 turned out to be the same file seen twice.

### 3. API-05 — the "inconsistency" had a positive reason that had since expired

`/openapi.json` was published in production while `/docs` was hidden, which reads as an
oversight. But CloudFront distribution `EEOXECPHQ8SR0` carries an explicit `/openapi.json`
cache behaviour, and two middlewares list the path as public — three deliberate acts of
publishing. Unmounting it on the "obvious inconsistency" reading would have contradicted
deployed infrastructure.

The resolution came from **dating** the evidence: those routing and auth facts exist because
`/docs` used to be served in production, before the Phase 6 ENV-05 fix restricted it. Once
the console became local-only, the schema's publication lost its reason. Combined with the
measurement that **nothing in this repository fetches the schema over HTTP** — the frontend
mentions it only in comments, no CI workflow references it, and
`tests/test_route_contract.py` reads `app.openapi()` in process — the change became safe. The
CDN behaviour and allowlists are left in place and now forward to a 404, because deleting a
deployed cache behaviour is an infrastructure change.

---

## Two of my own test oracles were wrong, and the engine was right

Recorded because they are properties of the system, not of the tests.

1. **`required_funds` is a gate allowance, not the fill cost.** I asserted a post-fill
   balance of `capital − required_funds` and expected `49.92`; the ledger held `49.93`. The
   real cost is position value plus fee, each quantized **independently**:
   `money(50 × 1.0005) = money(50.025) = 50.02` — `ROUND_HALF_EVEN` resolves the exact half
   down because 2 is even — plus `money(0.050025) = 0.05`.
2. **A duplicate fill raises `repo.PaperDuplicateFill`, not `PaperConcurrencyConflict`.**
   `uq_paper_fill_event` has its own dedicated exception, whose message is *"already recorded
   on order"*.

A third correction is to the **record** rather than to a test: Phase 6's CORRECTION-006
(adding `ENV=development` to the repository-root `.env`) was **inert**, because
`backend_app/.env` is the file `core/config.py` loads. Logged as CORRECTION-007.

A fourth is to an earlier claim about the schema: `library_ratings.rating` is **NULLABLE** in
the provisioned test schema, with
`CHECK ((rating IS NULL) OR (rating >= 1 AND rating <= 5))`. An earlier phase recorded that
the step-9 upsert would violate a NOT NULL. It would not.

---

## What was deliberately NOT done

| Not done | Why |
|---|---|
| No in-memory lock or atomic reservation on the paper market path | `apply_fill` asserts every invariant before its first statement and `bump_version` carries the version as a predicate, so two racing workers can never both move the balance. Measurement said the money was never at risk — only the losing order's state was. A lock would also contradict Requirement 16.6's "SHALL lock no funds". |
| No new paper order model, state or migration | `CREATED → REJECTED` is one of Requirement 16.2's nine transitions, already seeded in `paper_order_allowed_transitions` and enforced by the existing trigger. |
| No new marketplace error code for LIB-01 | `MARKETPLACE_READ_FAILED` is the code `get_categories` in the same module already raises for this exact condition. Adding one would have meant editing the catalogue and its invariant tests to express something the catalogue already expresses. |
| `_build_service_client` was not made to raise | That would turn `browse_library`, `get_featured_strategies` and `get_trending_strategies` from empty-200 into 503. The empty page on a developer machine is a deliberate browse-surface choice several suites rely on. A `_require_service_client` sibling makes the per-call-site choice visible instead. |
| The other ~22 unguarded `_build_service_client` call sites were not changed | A large, risky diff inside an error-handling repair. Reported. `checkout` is deliberately unguarded and documented. |
| The limit branch's `estimated_cost` was not raised to `required_funds` | It is simultaneously the gate threshold, the locked amount and the released amount. A release that does not match its lock is worse than a marginally generous gate. Reported as PAPER-05. |
| No unique constraint added to `strategies(user_id, source_library_id)` | A schema change to apply deliberately. Reported as LIB-02; the concurrency test names the actual outcome (two rows the caller owns) rather than asserting an absent constraint. |
| `validate_safety_config_integrity` and `validate_production_config` were not wired up | Both are uncalled, and the first compares `VYOMQUANT_MODE` against `"live_trading"` — a value the variable never holds (TRADE-06). Wiring a broken comparison is worse than leaving it documented. Both now have a test pinning the code-and-note pair. |
| `PRODUCTION_ROUTER_ENABLED`'s enabling vocabulary was not widened | Accepting `1` / `yes` / `on` is a permissiveness increase on the one switch whose purpose is explicit enablement. An unrecognised value is refused **and logged by name** instead. |
| The CloudFront `/openapi.json` behaviour and middleware allowlists were not removed | Deleting a deployed cache behaviour is an infrastructure change; a routed path that answers 404 is harmless. |
| `CONN-01` was not fixed | The repair is one `release_exchange` call in `store_keys`, but it closes a live socket on a path that currently cannot, and belongs with whoever owns the key-rotation flow. |
| No sandbox end-to-end order | No safe venue credential exists. `TRADING PAPER` stays NOT CLASSIFIED rather than being certified on construction evidence. |
| No live enablement, no production exchange order, no live credential, no production DDL | Out of scope in every phase, and in this one too. |

---

## Net test-count change for Phase 7

**0 tests deleted. 0 marked skip. 0 marked xfail. 0 assertions relaxed.**

| Change | Count |
|---|---|
| New test files | 5 |
| New tests added | **339** |

Collected per file (`staging/collect_C1.txt`):

| File | Collected | For |
|---|---|---|
| `tests/test_paper02_unfunded_order_state_postgres.py` | 16 | PAPER-02, real PostgreSQL |
| `tests/test_lib01_clone_strategy_postgres.py` | 17 | LIB-01/02/03, real PostgreSQL |
| `tests/test_env03_placeholder_secrets_are_invalid.py` | 138 | ENV-03 |
| `tests/test_env04_production_router_requires_explicit_enablement.py` | 45 | ENV-04 |
| `tests/test_api05_documentation_exposure_matrix.py` | 120 | API-05 + ENV-06 |
| `tests/test_exchange_safety_fix.py` | 9 (was 6) | ENV-01 + CONN-01 |
| | **339 new** | |

The large ENV-03 and API-05 counts are matrices, not padding: ENV-03 is each governed
credential × each unfit verdict, and API-05 is 10 environments × 2 `DEV_MODE` × 3 trading
modes with all three URLs asserted per cell.

| Tests rewritten in place | 1 (`test_connection_pooling_safety` — same name, deterministic, strictly stronger assertions) |
| Tests updated for a deliberate contract change | 1 (`test_the_documented_contract_of_build_service_client_now_holds`: 500 `HTTPException` → 503 `MarketplaceError`; the guarded property unchanged) |
| Test doubles extended to match the real client | 2 (`.limit()` on `_Query` and `_CloneQuery`) |
| Skips converted to passes | 1 (`library.unpublish_strategy` left `_ENVIRONMENT_GAP_ROUTE_KEYS`) |

The one skip-to-pass conversion follows the precedent the validation sweep's own comment
records for `billing.create_portal_session`: a route key leaves the environment-gap list
when the environment gap is fixed, not when the test is adjusted.

---

# PHASE 8 — 2026-10-09. TENANT ISOLATION, MONEY INTEGRITY, TRADING SAFETY

**Substrate for every database figure below: LOCAL PostgreSQL 16.2 via pgserver, with
migrations 026 and 027 applied. No production DSN existed in any phase of this audit. No
Redis server was available. No venue credential was available. No live trading was enabled,
no exchange credential was added, no real order was placed, and no production execution path
was turned on.**

## What was closed

| ID | Was | Now | Mechanism |
|---|---|---|---|
| TENANT-02 | P1 OPEN | **FIXED** | migration `026_fee_records_row_level_security.sql` |
| MONEY-04 | P0 OPEN *(mis-recorded)* | **FIXED** | stale documentation reconciled; residual closed by migration `027_referral_ledger_currency.sql` |
| TRADE-01 | P1 OPEN | **FIXED** | `claim_execution_within_capacity` — advisory-lock-serialised capacity reservation |
| TRADE-02 | P1 OPEN | **FIXED** | kill-switch TTL made symmetric |
| **KILL-01** | *new* | **FIXED** | the kill switch failed **open** on any unreadable backend |
| **TRADE-10** | *new* | **FIXED** | `claim_execution`'s PostgreSQL branch had never executed |

Detail for each lives in the phase blocks of `TENANT_ISOLATION_MAP.md`,
`AUTH_RLS_AUDIT.md`, `MONEY_FLOW_AUDIT.md` and `TRADING_SAFETY_AUDIT.md`. The two new
findings are summarised here because they were not in the brief and are the most
consequential things this phase found.

### KILL-01 — the emergency stop failed open

`GlobalKillSwitch._redis_get` caught every exception and returned `None`, so `None` meant
**both** "Redis answered and the key is not set" **and** "Redis could not be read". The
fail-closed `except` at the bottom of `is_active()` was therefore unreachable for a
connection error: control fell through to `if value is None: return False`, and a Redis
outage reported the kill switch as **NOT ACTIVE**. A *triggered* switch stopped blocking the
moment Redis went away.

It was broader than an outage. `redis_manager` is a `__getattr__` proxy that raises
`AttributeError` when never initialised, so any worker that had not brought Redis up also
read "switch off".

The same conflation produced the **mirror** defect: once the local latch engaged, recovery
lifted it only `if probe_result is not None`, and a healthy system's resting state *is*
`None` — so a recovered Redis with no key could never lift the latch, contradicting the same
docstring's promise that transient failures do not deadlock the platform.

The clearest evidence that the fix is load-bearing is that it **broke the repository's own
live-order tests**: two positive tests in `test_live_risk_gate_enforcement.py` were green
only because the switch failed open, and an order that previously reached the CCXT mock is
now stopped. Their implicit precondition is now explicit.

### TRADE-10 — the documented atomic claim had never run

`claim_execution`'s PostgreSQL fast path used lowercase literals (`SET status = 'executing'`,
`WHERE … status = 'pending'`) against an enum whose labels are uppercase only, so PostgreSQL
raised on **every call**, the `except` caught it, and the ORM fallback silently did the work.
The audit had recorded that `UPDATE … RETURNING` as "the only atomic predicate on the live
order path". It had never executed there.

The outcome was still correct — the fallback is also a single guarded `UPDATE` — but the
mechanism named in the audit was not the mechanism doing the work. Found only because the
new capacity reservation was built on the same predicate and, deliberately, **without a
fallback**; the error surfaced immediately instead of being masked.

## What is open

Nine findings. Five are new to Phase 8.

| ID | Sev | Summary |
|---|---|---|
| **KILL-02** | P1 | `deactivate()` has no authorization parameter; its audit trail is a Redis list with 7-day retention |
| **KILL-03** | P1 | No operator-facing path engages the global kill switch: `enforce_kill_switch()` has zero call sites, and `/api/admin/kill-all` calls `fleet.shutdown_all()` and never touches `GlobalKillSwitch` |
| **MONEY-06** | P1 | Razorpay referral commissions are never credited; the only record of the obligation is a log line |
| **KILL-04** | P2 | The venue boundary records `"kill_switch"` as an asserted string, not a re-check |
| **MONEY-07** | P2 | The Razorpay capture path hardcodes `"INR"` and never reads `payment.currency` |
| **FEE-01** | P2 | `FeeEngine` falls back to the all-zero uuid, so an unattributable fee row is owned by nobody |
| **ML-01** | P2 | `MLExecutor` cannot consume a `FeatureMatrix` (zero references to it, three to `pd.Series`) |
| **TRADE-11** | P2 | A market order with no price contributes nothing to measured exposure — a shared, documented blind spot |
| TRADE-07 | P2 | *(pre-existing)* No reaper for stale `EXECUTING` rows. **Partially mitigated** this phase: the capacity reservation's 300s stale cutoff stops such a row holding capacity, but the row stays unclaimable |
| plus retained items from earlier phases | — | see the per-finding JSON blocks |

Each is open for a stated reason, not for lack of time. KILL-02 needs a decision about where
operator identity comes from on that path; MONEY-06 needs a decision about how a non-USD
obligation is denominated and settled; TRADE-11 needs a reference price source. Inventing
any of those would be the kind of guess this audit exists to prevent.

## Corrections to this audit's own record

Five, including one where I damaged an artifact.

**CORR-P8-01 — MONEY-04 was not open.** It was carried into Phase 8 as an open P0. The ~83×
overstatement had already been closed by an explicit refusal block in `routers/billing.py`,
and `MONEY_FLOW_AUDIT.md` said so in one place while two phase-scoped "this phase did not
touch it" notes elsewhere read as open. My Phase 7 scorecard propagated the open reading
without re-verifying it against the code. The planned remediation was for a defect that no
longer existed.

**CORR-P8-02 — a test's scope was mis-recorded.** I had noted that
`test_marketplace_paper_schema_contract.py::test_no_destructive_statements` is hard-coded to
migrations 006–010 and would therefore not cover a new file. It globs every `*.sql` in both
migration directories, so it covered 026 and 027 from the moment they were written.

**CORR-P8-03 — I destroyed an audit artifact and reconstructed it.** While undoing a bad
intermediate write during the TRADE-01 baseline re-record I ran `git checkout --` on
`tests/regression/baseline/live_order_path.json`. Its TRADE-05 authorisation note was an
**uncommitted** change from an earlier phase and HEAD never carried it, so the checkout
deleted it; only its truncated opening survived in an earlier grep. The note now present is
an explicitly **labelled reconstruction**, rebuilt from the code and tests it cites and
carrying a provenance sentence that says so. The substance is accurate; the wording is not
the original.

**CORR-P8-04 — TRADE-02's finding named the wrong mechanism.** It was recorded as "the kill
switch may not be enforced on the live order path". The live path does consult the switch.
What the finding missed was the fail-open behaviour (KILL-01) and the asymmetric TTL. A
finding that points at the wrong mechanism is worse than a vague one, because it directs
remediation at code that was already correct.

**CORR-P8-05 — the atomic claim named in the audit was not the one running.** See TRADE-10
above.

**CORR-P8-06 — three id/state contradictions, two inherited and one mine.** Found by
`staging/verify_phase8_consistency.py`, not by reading.

*Mine:* I assigned the ids `TRADE-06` and `TRADE-07` to two findings new in this phase.
Both were already taken. `TRADE-06` collided **visibly** — the same id appeared in the
resolved and open blocks at once — but `TRADE-07` collided **silently**: my open entry
overwrote the pre-existing "no reaper for stale `EXECUTING` execution records" finding with
no warning, which is the worse of the two failures. The original text was recovered from the
generator source and restored, my findings are renumbered **TRADE-10** and **TRADE-11**, and
TRADE-07 now also records the partial mitigation this phase gave it.

*Inherited:* `PAPER-01` and `TEST-02` each appeared in **both** the resolved and the open
block, because a later phase recorded the fix without removing the earlier phase's open
entry. Each file therefore asserted that the finding was closed and open simultaneously. The
stale open entries are removed; the resolved entries, which are later and carry evidence,
stand.

This is the clearest argument for the checker being an assertion rather than a report: all
three had been sitting in four JSON files across multiple phases, and a printed
reconciliation would have scrolled past them again.

## Migrations added

Both forward-only, additive, idempotent, and carrying their own in-transaction postflight
that `RAISE`s if the resulting state is not what the file claims — because
`ADD COLUMN IF NOT EXISTS` and `CREATE TABLE IF NOT EXISTS` both silently accept a
pre-existing object of a different shape, and a migration that can quietly leave a weaker
invariant than it advertises is worse than one that fails.

* `backend_app/migrations/026_fee_records_row_level_security.sql` — creates `fee_records`,
  enables RLS, adds an owner `SELECT` policy keyed on `auth.uid() = tenant_id` and a
  `service_role` policy, revokes everything from both browser roles. Includes a **fail-safe**
  that refuses outright if `tenant_id` is absent or is not `uuid`, rather than installing a
  policy that would look protective and filter nothing.
* `backend_app/migrations/027_referral_ledger_currency.sql` — adds
  `currency TEXT NOT NULL DEFAULT 'USD'` with `CHECK (currency = 'USD')` to
  `referral_commissions`, `referral_wallets` and `referral_payouts`. Refuses if any
  pre-existing `currency` column already holds a non-USD value, naming the rows, rather than
  picking a winner.

Both applied to the local substrate and verified there. **Their effect on production is
unverified, because no production database was connected to.**

## Tests added

| File | Tests | Lane |
|---|---|---|
| `tests/test_tenant02_fee_records_rls_postgres.py` | 22 | PostgreSQL (local) |
| `tests/test_money04_referral_ledger_is_single_currency_postgres.py` | 24 | PostgreSQL (local) |
| `tests/test_trade01_concurrent_exposure_postgres.py` | 28 | PostgreSQL (local), multi-connection |
| `tests/test_trade02_kill_switch_fails_closed.py` | 30 | CI |

Four properties were applied throughout, and each caught something:

* **Read the catalogues, not the application's answer.** RLS status, policy predicates and
  role privileges come from `pg_class`, `pg_policies` and
  `information_schema.role_table_grants`.
* **Pair every refusal assertion with a negative control.** "An unreachable backend blocks"
  is worthless without "a healthy backend with no key permits".
* **Demonstrate the defect as a passing test before demonstrating the fix.**
  `test_without_the_reservation_two_rows_both_reach_executing` races the *original*
  `claim_execution` and asserts the database ends up over-committed.
* **Pin the limits of the guarantee as passing tests.** Three of them:
  `test_the_constraint_cannot_catch_a_mislabelled_amount`,
  `test_a_market_order_with_no_price_is_documented_not_silently_zero`,
  `test_reset_is_audited_but_takes_no_authorization`. If a future change ever closes one of
  those gaps, the test fails and the documentation must be corrected — which is the
  opposite of a comment that quietly goes stale.

## Regression

Both lanes were re-run in full after all changes. Counts are recalculated from the new runs,
cross-checked against the JUnit XML, and recorded in
`FULL_REGRESSION_FAILURE_DISPOSITION.md` and `staging/fullreg_phase8_counts.json`. The
Phase 7 figures are treated as historical baselines, not targets.

**Two failures are expected to remain in both lanes**, both retained false positives with
their exact mechanisms documented. Neither lane is presented as fully green.

## Launch classification after Phase 8

* **PAPER-ONLY LAUNCH — NO-GO.** PAPER-02 being fixed is explicitly not treated as
  sufficient. Two P1 findings sit on the emergency-stop control itself and one P1 is a real
  customer money obligation recorded only in logs.
* **TRADING LIVE — OFF / NOT CERTIFIED.** Unchanged and deliberately so.
* **TRADING PAPER — NOT CLASSIFIED.** Isolation from live venues is isolation *by
  construction*, not venue-observed sandbox evidence. No safe venue credential existed in any
  phase.
* **PRODUCTION — NOT CLASSIFIED.** No production DSN existed in any phase.


# PHASE 9 — 2026-10-10. OPERATIONAL SAFETY AND FINANCIAL INTEGRITY

Phase 9 closed the ten findings Phase 8 left open in its operational-safety and
financial-integrity set, found and closed one new finding (TRADE-12), and completed two
authorised regression-baseline re-records. Every finding was independently re-verified against
the code by `staging/verify_phase9_open_findings.py` (**11 checked, 11 remediated or
reclassified, 0 still present**). No live trading was enabled, no exchange credential was
invented, and no production DDL was run; every database figure is LOCAL PostgreSQL 16.2.

## What changed, by finding

| Finding | Disposition | One-line resolution |
|---|---|---|
| KILL-02 | FIXED | reset requires `operator_id`/`authorization_source`/`acknowledge_holds` (keyword-only, no defaults); durable `safety_control_audit` row per applied **and refused** transition (migration 028) |
| KILL-03 | FIXED | `enforce_kill_switch()` gains its only call site at the venue boundary; `/api/admin/kill-all` activates the authoritative switch **before** the fleet fan-out; new `GET /api/admin/kill-switch` |
| KILL-04 | FIXED | the same boundary call re-reads live kill state immediately before `create_order`, replacing the stale `"kill_switch"` claim string |
| MONEY-06 | FIXED | an owed referral commission is a durable `referral_obligations` row (migration 029), not only a log line; **no FX** — non-USD is `unresolved` with NULL commission |
| MONEY-07 | FIXED | `_require_provider_currency` validates the provider's own declared currency and refuses a mismatch, rather than assuming the denomination |
| FEE-01 | FIXED | `TenantResolutionError` replaces the `UUID(int=0)` fallback (0 call nodes in executable code, AST-confirmed) |
| ML-01 | CLOSED (FALSE POSITIVE) | `MLExecutor.execute` raises on every reachable path and returns no value, so "cannot consume a FeatureMatrix" is unreachable; **no nominal reference added** |
| TRADE-07 | FIXED | age-based `classify_stale_executions` + `resolve_stale_execution` (EXECUTING-only, narrow destinations) + `recover_unsubmitted_executions` wired into the watchdog; **never resubmits** an ambiguous row |
| TRADE-11 | FIXED | missing/empty/zero price is **counted as unusable and refused** (fail closed), not summed as zero; the `price or 0` form is gone from executable code |
| TRADE-12 (new) | FIXED | dedicated `venue_order_id` column (migration 030, no FK) stops the venue id colliding with the `orders` foreign key on `order_id` (the 23503 that stranded EXECUTING rows) |
| TEST-06 | FIXED | numeric protected-logic tokens matched on a numeric-value boundary, removing the wall-clock dependence; no short-token exemption |

## Migrations applied (locally, with append-only RLS postflights)

* `028_safety_control_audit.sql` — SHA256 `953089a10af73bd85a59df79192e8041b607bcafce8a00e2172ea26d62afc5e4`. Its postflight caught a real gap: the substrate's `ALTER DEFAULT PRIVILEGES` grants `service_role` ALL on new tables, so a bare `GRANT SELECT,INSERT` was a no-op — fixed by `REVOKE ALL FROM service_role` first, preserving the append-only guarantee.
* `029_referral_obligations.sql` — SHA256 `60f94f12befabd8f005c7c655469c3676e2659a3ddf846fa3adfcad5134206e8`.
* `030_execution_venue_order_id.sql` — SHA256 `d7d4d06a115b9019f5bb2f288ad691448b1c8b2592f0a6bc9da433027355c911`; postflight asserts `venue_order_id` is varchar, nullable and FK-free.

## Baseline re-records (authorised, sanctioned process)

Applied by `staging/rerecord_baselines_phase9.py`, which carries forward every existing
`_baseline_note`, patches **only** the authorised delta and **aborts on any other
difference** — it does not regenerate the file from the capture. Two deltas:

* `auth_admin_role` — the `global_kill_switch` return dict gains `kill_switch_active`,
  `holds`, `holds_readable` (KILL-02/KILL-03).
* `live_order_path` — the `update_status` collaborator call swaps its `order_id` argument for
  `venue_order_id` (TRADE-12), same call, same position.

The full `tests/regression/test_baseline_unchanged.py` suite then passed (**all 60 captures**),
confirming no other recorded contract drifted.

## Net test change

Six new suites plus corrections to existing tripwires; **0 tests deleted, 0 blanket skips, 0
assertions weakened, 0 expected outcomes changed**. Several test "walls" were deliberately
**inverted** tripwires (e.g. `test_reset_is_audited_but_takes_no_authorization` →
`test_reset_now_requires_authorization_evidence`;
`test_a_market_order_with_no_price_is_documented_not_silently_zero` →
`test_a_priceless_in_flight_row_now_refuses_rather_than_vanishing`): the old passing-but-wrong
contract was proven obsolete before the assertion was changed.

## Two corrections recorded this phase

* **CORR-P9-01** — my own `verify_phase9_open_findings.py` first reported FEE-01 and TRADE-11
  as STILL PRESENT. Both were the verifier's own substring false positives (markers in
  docstrings/comments, and a NULLIF now used to *detect* unusable price rather than vanish it);
  both checks were re-expressed with AST. This is the sixth/seventh text-scan false positive in
  the engagement, and like the earlier ones it was in the audit's own detector, not the product.
* **CORR-P9-02** — the first CI run (FULLREG_P9) reported 12 failures; all 12 were collateral
  from my own correct Phase 9 changes (a billing F821, a monkeypatch-bypassing local import, a
  test double missing a new method, a stale gitignored SQLite scratch schema, a SQLite/PG
  asymmetry, and the two legitimately-shifted baselines). 10 fixed, 2 re-recorded; a FINAL lane
  was then run so no figure is reported from the intermediate run.

## Regression

Both lanes re-run on the fully-remediated tree: `FULLREG_P9FINAL` (CI-comparable, no database
URL) and `FULLREG_P9PGFINAL` (PostgreSQL). Counts recalculated and cross-checked against the
JUnit XML in `FULL_REGRESSION_FAILURE_DISPOSITION.md` and `staging/fullreg_phase9_counts.json`;
the Phase 8 figures are treated as historical baselines, not targets.

## Launch classification after Phase 9

* **PAPER-ONLY LAUNCH — NO-GO.** Materially stronger operational safety, but no venue-sandbox
  end-to-end run exists and open findings remain.
* **TRADING LIVE — OFF / NOT CERTIFIED.** Unchanged and deliberately so.
* **TRADING PAPER — NOT CLASSIFIED.** No venue-sandbox evidence in any phase.
* **PRODUCTION — NOT CLASSIFIED.** No production DSN in any phase; migrations 028–030 verified
  locally only.


# PHASE 10 — 2026-10-10. REMAINING LAUNCH BLOCKERS & INDEPENDENT VERIFICATION

Phase 10 continued from the Phase 9 report. It closed CONN-01, two newly-confirmed
financial-integrity defects (FEE-02, MONEY-08), and the QUOTA-01 residual; repaired the two
retained full-regression failures (the PostgreSQL tenant-isolation sweep and TEST-05); added
negative-control coverage for a TENANT-01 residual; and assessed the referral settlement
lifecycle and the exchange-reconciliation startup. Every finding was independently
re-verified against the code by `staging/verify_phase10_open_findings.py` (**6 checked, 0
status-lies**). No live trading was enabled, no exchange credential invented, no production
DDL run; every database figure is LOCAL PostgreSQL 16.2.

## What changed, by finding

| Finding | Disposition | One-line resolution |
|---|---|---|
| PG sweep failure | FIXED | the NOT-PROVEN table was `referral_obligations` (a Phase 9 table), not `risk_settings`; added its `currency: "USD"` CHECK_OVERRIDE; sweep now 0 leaked / 0 not proven, 6/6 pytest |
| TEST-05 | FIXED | `st.integers(2,10)` biases low so length-10 was never drawn in 100 examples; forced the length boundaries (10, 5, 2) with `@example` — deterministic, no floor lowered, `max_examples` unchanged, property preserved |
| CONN-01 | FIXED | `store_keys` now calls `release_exchange` after the vault write, so a credential rotation evicts the stale pooled client and the new credential takes effect in-process |
| QUOTA-01 | CLOSED (documented decision) + residual coverage | metered reservation now has a concurrency test (N racers, hard limit K → exactly K admitted) + a real-Redis-gated variant; HardQuotaEnforcer stays correctly unwired |
| TENANT-01 | OPEN (unchanged) | architectural residuals (FORCE RLS unset, service-role bypass, billing-webhook caller-supplied user_id) are deliberate; Phase 10 added credential-vault cross-tenant negative-control coverage and one more swept table |
| FEE-02 (new) | FIXED | third-asset fee lost to a `fee_pct` NOT NULL violation → `fee_pct` nullable (model + migration 031); `fee_amount`/`fee_asset` stay mandatory |
| MONEY-08 (new) | FIXED | `create_payout_request` wrote non-existent `payment_method`/`payment_details`; corrected to the provisioned `payout_method`/`payout_details`; API field unchanged |
| RECON-01 (new) | OPEN | `ExchangeReconciliationService` is never started, so venue order/fill reconciliation is not operational; reported, not fabricated (needs a sandbox exchange to wire+verify) |

## Migration

* `031_fee_records_fee_pct_nullable.sql` — SHA256 `9632a2548c6773bb81385722004f0f057dc81870ebec2c5add96f2285adc443e`; drops NOT NULL on `fee_records.fee_pct`, postflight asserts `fee_amount`/`fee_asset` stay mandatory. Applied locally; idempotent on re-run (skips on matching checksum). The ORM `FeeRecordModel.fee_pct` is updated to `nullable=True` for fresh `create_all()` databases, and the ORM and provisioned column agree.

## Two corrections recorded

* **CORR-P10-01** — the Phase 9 report attributed the sweep failure to `risk_settings`; measured in Phase 10, that case was already handled and the real NOT-PROVEN table was `referral_obligations` (a Phase 9 table I added without its seed override). Fixed by adding the override; the coverage-census test correctly refused to pass until then.
* **CORR-P10-02** — TEST-05's `sequence_length_10` floor relied on a random draw within 100 examples; forced with `@example` per the census helper's own documented policy.

## Settlement lifecycle & reconciliation (assessed, not fabricated)

* **Referral settlement**: a payout is created as `status="pending"`; no pending→approved→paid transition and no payment initiation is implemented. This is **fail-safe** — no money moves and nothing claims a payout is paid. Reported as incomplete rather than implemented (the engagement forbids implementing an actual transfer).
* **Exchange reconciliation**: the mismatch-ESCALATION scheduler is started in lifespan with visible status; the venue order/fill comparison service (`ExchangeReconciliationService`) is **not** started (RECON-01, OPEN). The service fails safe on exchange errors (re-raises, never assumes filled).

## Regression

Both lanes re-run on the fully-remediated Phase 10 tree: `FULLREG_P10` (CI-comparable) and
`FULLREG_P10PG` (PostgreSQL). Counts recalculated and cross-checked against JUnit in
`FULL_REGRESSION_FAILURE_DISPOSITION.md` and `staging/fullreg_phase10_counts.json`.

## Launch classification after Phase 10 (UNCHANGED)

* **PAPER-ONLY LAUNCH — NO-GO.** Stronger posture, but no venue-sandbox evidence, RECON-01 open, settlement unimplemented, TENANT-01 residuals remain.
* **TRADING LIVE — OFF / NOT CERTIFIED.**
* **TRADING PAPER — NOT CLASSIFIED.** No venue-sandbox evidence in any phase.
* **PRODUCTION — NOT CLASSIFIED.** No production DSN; migration 031 verified locally only.
