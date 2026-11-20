# CURRENT VERIFIED STATE

**As of:** 2026-10-08, end of Phase 7
**This section supersedes everything below it.**

> ## How to read this document
>
> Everything after this section is the **historical record**, phase by phase, and it is
> preserved deliberately and unedited. It contains findings that were open when they were
> written and are closed now. **Do not read the sections below as a list of current open
> defects** — read this section for that, and read the sections below for how each finding
> was found, what was measured, and why each fix is shaped the way it is.
>
> Every finding below also carries a status in its own document's Phase 7 addendum
> (`staging/PAPER_TRADING_CERTIFICATION.md`, `staging/AUTH_RLS_AUDIT.md`,
> `staging/ENVIRONMENT_PRECEDENCE_AUDIT.md`, `staging/TRADING_SAFETY_AUDIT.md`,
> `staging/MONEY_FLOW_AUDIT.md`, `staging/FULL_REGRESSION_FAILURE_DISPOSITION.md`).

---

## Classification

```
PAPER-01:         CLOSED
PAPER-02:         CLOSED
LIB-01:           CLOSED
LIB-03:           CLOSED
LIB-04:           CLOSED   (introduced by the LIB-01 repair, found by the PostgreSQL lane)
ENV-01:           CLOSED
ENV-03:           CLOSED
ENV-04:           CLOSED
ENV-06:           CLOSED
API-05:           CLOSED

TENANT-02:        OPEN  (P1, NEW - fee_records has no RLS and grants anon full DML)
TRADE-01:         OPEN  (P1, carried forward)
TRADE-02:         OPEN  (P1, carried forward)
MONEY-04:         OPEN  (P0-class, carried forward)

TRADING PAPER:    NOT CLASSIFIED
PAPER-ONLY:       NO-GO
TRADING LIVE:     OFF / NOT CERTIFIED
PRODUCTION:       NOT CLASSIFIED
```

### Why `PAPER-ONLY` is still `NO-GO` after the blockers closed

Three reasons, and none of them is PAPER-02:

1. **`TRADING PAPER` is NOT CLASSIFIED and cannot be classified here.** There is no safe
   venue credential, so no paper order has ever been observed at an exchange sandbox. The
   isolation evidence is **isolation by construction** — import-graph, feature-flag and
   state-machine evidence — **not venue-observed sandbox end-to-end**. Construction evidence
   cannot detect a venue that silently accepts an order the simulator believed it had kept
   local. A paper launch classification that rested on it would be asserting something that
   was never tested.
2. **Five red tests remain**, all classified and none a product defect (see the table
   below), but a launch gate that ignores its own red tests is not a gate.
3. **Open findings remain, and the P1 ledger is not clear.** MONEY-04 (P0-class: Razorpay
   INR credited as USD, ~83× overstated) plus **three open P1s** — the new **TENANT-02**
   (`fee_records` has no RLS and grants `anon` full DML, so one tenant can read, update and
   delete another's fee rows), TRADE-01 (capital check is a read-then-compare with no
   reservation) and TRADE-02 (the global kill switch self-expires after 24 h and trading
   silently resumes). None is a paper-mode defect. TRADE-01, TRADE-02 and MONEY-04 were not
   re-measured in Phase 7 — see the correction note on the open findings table below for why
   they were missing from the current-state record until now. TENANT-02 **was** measured, by
   three independent tests, and it is the reason the PostgreSQL lane was worth running.

### Why `PRODUCTION` is `NOT CLASSIFIED`

No production access, no production DSN, no production credentials, and no production DDL
was performed in any phase. Nothing in this engagement measured the production deployment.

### Why `TRADING LIVE` is `OFF / NOT CERTIFIED`

No live enablement, no live credential installed, no order placed at a production exchange,
in any phase including this one. `core/live_execution_gate.py`'s 13 `REQUIRED_CHECKS` and
the HMAC `LiveOrderAuthorization` are in place and untouched. "Not certified" is the
accurate word: the gate exists and is wired, and it has never been exercised against a real
venue.

---

## Open findings, current

> **CORRECTION, made while assembling this section.** Ten of the rows below were present in
> the per-file `defects` arrays the whole time and **absent from the authoritative
> remediation block** that the current-state record is generated from — so they did not
> appear in any current-state summary, including the first draft of this one. **Two of them
> are P1** (TRADE-01, TRADE-02), and the JSON `p1_ledger` field read `CLEAR`, which was true
> about the Phase 5 P1s and read as an all-clear about the ledger as a whole. It was not one.
>
> Found by `staging/verify_audit_json.py`: first as two `*** NOT PRESENT IN EITHER BLOCK ***`
> lines for watched ids, then by a general sweep added in Phase 7 that reads **every** id in
> **every** findings array across all four JSON files, because a hand-maintained watch list
> can only catch a gap somebody remembered to watch. All ten are now in the block; the sweep
> now reports `ids in NEITHER block = 0`.
>
> A stale `OPEN` on a fixed finding wastes a reader's time. A missing P1 reads as an
> all-clear. The second is the more dangerous direction, and it is the one that happened.
>
> **None of the ten carried-forward findings was re-measured in Phase 7.** Each row states
> the status its own record states, and nothing more.

| ID | Sev | Finding | Status |
|---|---|---|---|
| **MONEY-04** | P0-class | Razorpay INR amounts credited as USD, ~83× overstated. Found by refusing to convert rather than converting: the correct currency, rate source and rate as-of time are product decisions, and a wrong rate silently produces a plausible number. | OPEN (Phase 2), not re-measured in Phase 7 |
| **TENANT-02** | **P1** | **NEW.** `fee_records` has **RLS disabled** and grants `anon` and `authenticated` full SELECT/INSERT/UPDATE/DELETE, so one tenant can read, update and **delete** another tenant's fee rows and an unauthenticated caller can read them. Three independent PG-lane tests agree and are precise: `anon saw 1 of 1 row(s)` with **43** other tables blocked, `authenticated saw 1 of 1` with **69** blocked, and `b_select=ALLOWED; b_update=ALLOWED; b_delete=ALLOWED`. **Root cause is structural:** the table exists only as `fee_engine.FeeRecordModel` and is created by `Base.metadata.create_all()` — **zero** `.sql` files in the repository mention it, so the `ENABLE ROW LEVEL SECURITY` and policy statements that every migration-created table receives do not exist for it anywhere. Measured on the local substrate; production is **unverified** (no production DSN in any phase), but the missing statement is missing from the *repository*, so a production database built from these migrations also lacks it. | **REPORTED (new, Phase 7)** |
| **TRADE-01** | **P1** | The capital/notional check is a read-then-compare with **no reservation** (TOCTOU). Nothing decrements between the comparison and `place_order`; no `SELECT … FOR UPDATE`, no atomic decrement; exposure updated after the fill in process memory. The Redis lock is per-execution-id, not per-capital, so two concurrent orders with different parameters take different locks and both pass. Marked empirically demonstrated. | OPEN (Phase 1), not re-measured in Phase 7 |
| **TRADE-02** | **P1** | The global kill switch **self-expires after 24 h**: `activate()` writes the key with `ex=86400`, and once it expires `is_active()` takes the "key absent → not active (safe default)" branch, so trading silently resumes. No renewal loop found. The "safe default" is safe for an unreachable Redis and the opposite of safe for this case. | OPEN (Phase 1), not re-measured in Phase 7 |
| **TRADE-07** | P2 | No reaper for stale `EXECUTING` execution records: a crash after claiming but before the exchange call blocks that `execution_id` indefinitely with the venue state unknown until the reconciliation worker resolves it. (WORK-01/WORK-02's XAUTOCLAIM reaper fixed the worker queue, not this table.) | OPEN, not re-measured in Phase 7 |
| **TRADE-08** | P2 | Retry idempotency is **time-boxed by a 5-minute bucket**: `generate_execution_id` includes a bucketed timestamp, so a retry landing in a later bucket produces a different `execution_id` → a different `clientOrderId` → a **new order at the venue**, sidestepping both the database dedupe and the venue's. An idempotency key that changes with the clock is not an idempotency key. | OPEN, not re-measured in Phase 7 |
| **TRADE-09** | P2 | Exchange-connection ownership is not re-checked at submission: `place_order` takes no tenant/user argument and the executor is bound to credentials at construction time, so per-tenant scoping is a property of every construction site. Adjacent to CONN-01. | OPEN, not re-measured in Phase 7 |
| **MKT-01** | P2 | Partial refunds record nothing and leave entitlement untouched: no reversal row is written, it is logged as a reconciliation mismatch and `settle()` answers `MISMATCHED`. A partially refunded buyer keeps full access. Marked in source as an open **requirements** decision, not an oversight. | OPEN (documented requirement gap), not re-measured in Phase 7 |
| **MKT-02** | P3 | The losing side of a concurrent first-time checkout surfaces a generic failure instead of "already subscribed". Entitlement correctness is unaffected — exactly one subscription is created. Same shape as LIB-02, but here the constraint exists and only the message is wrong. | OPEN, not re-measured in Phase 7 |
| **MONEY-03** | P2 | **No reconciliation job against the providers' source of truth.** Nothing reconciles subscription tier state, `billing_invoices` or `referral_wallets` against Stripe or Razorpay. This is what makes MONEY-04 worse than a single arithmetic bug: a wrongly credited amount has nothing downstream that would ever notice. | NOT IMPLEMENTED, not re-measured in Phase 7 |
| **PAPER-04** | P3 | An unsettled fill row can survive an exhausted retry. Non-financial, bounded to one row per order, detectable by one query, self-healing on retry, visible on the 409 via `phase='FILL'` / `partial_write=True`. Measured residue 0 in 10/10 races. | REPORTED (new, Phase 7) |
| **PAPER-05** | P3 | The limit branch's `estimated_cost` omits slippage, so it diverges from `required_funds`. Marginally more permissive than the market gate; not a path to negative cash. Coupled to the lock and release amounts, so the repair is one coherent change to the resting-order lifecycle. | REPORTED (new, Phase 7) |
| **LIB-02** | P3 | No unique constraint on `strategies(user_id, source_library_id)` behind a read-then-write, and `clone_count` has a lost-update shape. Duplicate is two rows the caller owns — not cross-tenant. | REPORTED (new, Phase 7) |
| **CONN-01** | P2 | The exchange pool key is `sha256([user_id, exchange_id])` and omits the credential, so a rotation through `POST /api/exchange/keys` is silently ineffective until the connection is deleted or the process restarts. Per-user isolation holds. | REPORTED (new, Phase 7) |
| **TRADE-06** | P2 | `safety_config.validate_safety_config_integrity()` has zero call sites, and its live-trading branch compares `VYOMQUANT_MODE` against `"live_trading"`, a value the variable never holds. Its contradictory `PRODUCTION_ROUTER_ENABLED` assertion was removed in Phase 7; the function remains uncalled and documented as uncalled. | REPORTED |
| — | P3 | `config.validate_production_config()` is a **second** uncalled validator of the same shape. Documented as uncalled, with a test pinning the code-and-note pair. | REPORTED (new, Phase 7) |
| **TENANT-01** | P2 | `TenantMiddleware` is not installed. Downgraded P1→P2 in Phase 4 with the measurement that 13 tables showed 0 cross-tenant leaks; installing it was measured to be the **wrong** action. | REPORTED |
| **TEST-01** | — | Protected-logic containment false positive (3-character substring oracle) | REPORTED |
| **SAST-01** | — | SQL-interpolation gate false positive at repository scope; the real finding is that its local and CI verdicts can disagree | REPORTED |
| **TEST-04** | — | A tenth order-dependent failure that does not occur in the repository's collection order | REPORTED |
| **TEST-05** | — | **Not a constant-harvesting flake — that attribution was wrong.** A non-vacuity **census** defect in `test_order_idempotence_under_retry.py`: `sequence_length_10` is an **existence** floor on one drawn value, and Hypothesis generation is tree-guided (it explores many children under one prefix, so a 100-example run visits a mean of **8.33 of the 9** possible lengths, min 6, rather than redrawing length 100 independent times). Measured 0 in **12 of 150** runs (~8%) **including in isolation**, so the earlier "8/8 passed in fresh subprocesses" was a 1-in-3 coincidence, not evidence. Fixed by forcing the three length boundaries as `@example` cases from one `DETERMINISTIC_COVERAGE` tuple, plus two self-checking guards. No floor lowered, `max_examples` unchanged at 100, nothing mocked, no test skipped. **The idempotency property itself never failed: no duplicate order and no duplicate financial effect.** | **FIXED** (Phase 10, Workstream B) |
| — | P3 | 20 copies of `os.getenv("ENV", "development")` across independent `_is_production()` sites, so absence means development. `core/runtime_env.py` exists as the consolidation destination. | REPORTED |

## Closed in Phase 7

| ID | Finding | Evidence |
|---|---|---|
| **PAPER-02** | An unfundable market buy could remain `ACCEPTED`/`OPEN` with `rejection_reason=NULL` | 16 real-PostgreSQL tests; 10/10 two-process races, `RACE_VERDICT=CORRECT`, zero stranded orders, zero PAPER-04 residue |
| **LIB-01** | `clone_strategy` answered 500 naming the wrong cause, after dereferencing a `None` client | 17 real-PostgreSQL tests; validation sweep `381+1F+11S` → `383 passed, 10 skipped` |
| **LIB-03** | `.single()` made two 404 branches unreachable; a nonexistent listing answered 5xx | Replaced with `.limit(1)`; 11-suite set back to `1 failed, 1126 passed` |
| **LIB-04** | **Introduced by the LIB-01 repair itself.** Narrowing to `except _PostgrestAPIError` let every non-`APIError` client failure escape to a generic 500 — and in production the commonest read failure arrives as `httpx.ConnectError`, which is not an `APIError`. Found by the **PostgreSQL lane**, which the CI lane structurally could not surface. | 19 passed, parametrised over 8 exception types across both routes, because the property is that the answer does not depend on the type |
| **ENV-01** | The unit lane made an unmocked authenticated request to `api.binance.com` | 36 passed, deterministic, no network, no credential; pooling and venue authentication separated by name |
| **ENV-03** | `backend_app/.env` wins silently; `bool("YOUR_SUPABASE_SERVICE_ROLE_KEY")` satisfied every credential check | 170 passed; production fails closed, development reports and continues |
| **ENV-04** | The flag's **absence** mounted the execution router; nine sources disagreed | 45 passed; nine-cell matrix; one authoritative definition; all nine sources reconciled |
| **ENV-06** | Sentry's `environment` tag was the trading mode, so production events were filed under `paper` | 12-cell matrix plus two AST sweeps; both dimensions now recorded independently |
| **API-05** | `/openapi.json` was served in every environment, including production | 60-cell environment matrix; schema now follows the consoles it feeds, with an independent override |

---

## Regression, current

**CI lane (`FULLREG_P7B`), comparable to the Phase 5/6 baselines:**

```
COLLECTED 13892   PASSED 13410   FAILED 7   ERRORS 0
SKIPPED 466       XFAILED 6      XPASSED 3  DURATION 1871.39s
```

Against Phase 6 (`13,534 / 13,082 / 9 / 434 / 6 / 3`): **+358 collected** — exactly the new
tests — and **−2 failed**, exactly the two closed.

**A second lane was run, and it is not optional.** The CI lane skips every PostgreSQL-only
suite by design, so a single green number from it would be reporting a pass on tests that
never ran. The PostgreSQL lane converted **402 skips into runs**, and it produced two
findings the CI lane structurally could not reach: **LIB-04** (product, fixed) and
**TENANT-02** (P1, reported). Figures, both lanes, the two intermediate runs, the JUnit
cross-check and the classification of every failure are in
`staging/FULL_REGRESSION_FAILURE_DISPOSITION.md` Parts 5 and 5B.

---
---

# HISTORICAL RECORD — PHASES 1–6, PRESERVED AS WRITTEN

> The classification block and finding statuses below were accurate at the dates shown.
> Where they conflict with the CURRENT VERIFIED STATE above, the section above is correct.

# FINAL LAUNCH SCORECARD

**System:** Aerora / VyomQuant crypto algorithmic-trading SaaS
**Date:** 2026-10-07
**Production access:** none
**Live trading enabled during audit:** no

---

## 1. EXECUTIVE CLASSIFICATION

```
LOCAL BASELINE:   AMBER

MARKETPLACE:      NO-GO

MONEY SYSTEM:     NO-GO

TRADING:          NO-GO   (live)
                  GO      (paper-only execution path, with caveats)

PRODUCTION:       NOT CLASSIFIED

PAPER-ONLY:       NO-GO

LIVE:             OFF / NOT CERTIFIED
```

`NOT CLASSIFIED` ≠ `AMBER`. Production is unobservable, not partially observed.

---

## 2. WHY PAPER-ONLY IS NO-GO

This is the verdict most likely to be misread, so it is stated directly.

The **trading execution path** for paper is sound — live is verified refused in all five permutations and
requires a code change to enable, manual order submission is disabled, direct executor bypass is impossible,
strategy ownership is tenant-scoped and fail-closed, the execution claim is atomic, and the paper accounting
engine refuses float inputs and recomputes equity at zero tolerance. On the narrow question "can a paper session
accidentally place a real order," the answer is no.

But a paper-only launch is not just the execution path. It still ships:

| Shipped in a paper-only launch | Status |
|---|---|
| The marketplace (listings, purchases, real money) | **NO-GO** — BUG-057 |
| Subscription billing (real money) | **NO-GO** — MONEY-02 |
| Referral commissions (real money) | **NO-GO** — MONEY-01 |
| User accounts, API keys, admin surface | **AUTH-01** — MFA enforced nowhere |
| The public API | **API-01** — mass assignment |
| Workers that start and stop bots | **WORK-01/02** — no DLQ, work silently discarded |
| Health endpoints the load balancer trusts | **OBS-01** — hardcoded `"HEALTHY"` |

Paper trading does not make customer money, credentials or moderation integrity hypothetical. Those are live on
day one of a paper-only launch. **PAPER-ONLY: NO-GO.**

---

## 3. GATING DEFECTS

Must be closed before any launch.

| # | ID | Severity | Finding | Verified by |
|---|---|---|---|---|
| 1 | **BUG-057** | P1 | Creator can self-approve, self-activate, self-price and self-feature their own marketplace listing | **EXECUTED** |
| 2 | **API-01** | P1 | `PUT /api/strategies/{id}` passes a raw caller dict into the UPDATE — no column allowlist | code (handler's own comment) |
| 3 | **AUTH-01** | P1 | `require_aal2` applied to zero endpoints; exchange API key creation unprotected | grep: zero call sites |
| 4 | **MONEY-01** | P1 | Referral commission double-credit; no `UNIQUE` on `payment_id`; installed function version unknown | code, two versions compared |
| 5 | **MONEY-02** | P1 | Float division on money feeding `billing_invoices` and the referral commission base | code |
| 6 | **WORK-01** | P1 | Command worker ACKs in `finally` → failed work discarded; crash → double `start_bot` | code |
| 7 | **WORK-02** | P1 | No dead-letter queue anywhere; no PEL reaper; shared default consumer name | grep: no implementation |
| 8 | **OBS-01** | P1 | Hardcoded `"HEALTHY"`; `/health/ready` checks nothing; Redis down ≠ degraded | code |
| 9 | **RLS-01** | P1 | Four `SECURITY DEFINER` functions take a caller-supplied identity with no internal check | code; **ACL NOT CLASSIFIED** |

Additional, live-only (not gating a paper-only launch, gating live):

| # | ID | Finding |
|---|---|---|
| 10 | TRADE-01 | Capital check has no reservation → concurrent distinct orders can both pass |
| 11 | TRADE-02 | Kill switch self-expires after 24h; `enforce_kill_switch()` unused; per-user switch absent from the live path |
| 12 | TRADE-04/05/06 | Environment agreement opt-in (65/71 unstated); no risk engine in the gateway; mode never consulted on the order path |

---

## 4. NON-GATING FINDINGS

Should ship with the above, do not individually block.

| ID | Severity | Finding |
|---|---|---|
| OBS-02 | P2 | Redaction scoped to 12 loggers; catch-all prints raw traceback past the logging pipeline |
| OBS-03 | P2 | `/metrics` unauthenticated |
| AUTH-02 | P2 | WebSocket JWT in query string on two legacy sockets |
| TRADE-03 | P2 | Second `create_order` with none of the guards (probably dead code) |
| API-02 | P2 | `RequestValidationError` handler preserves `exc.errors()` — can echo submitted input |
| API-03 | P2 | Rate-limit fail-closed depends on `ENV` being set; `paper`/`safe` are in `DEV_TEST_ENVS` |
| RLS-02 | P2 | Three UPDATE policies lack explicit `WITH CHECK` (PG's fallback currently covers it) |
| MONEY-03 | P2 | No reconciliation against provider source of truth |
| MKT-01 | P2 | Partial refunds record nothing; buyer keeps access, creator total unadjusted |
| MKT-02 | P3 | Losing concurrent checkout returns a generic failure rather than "already subscribed" |

---

## 5. WHAT IS GENUINELY STRONG

Recorded deliberately — a scorecard that only lists defects misrepresents the system.

| Control | Why it counts |
|---|---|
| **Live trading requires a code change** | Verified by executing all five env permutations, including both-switches-set. The final refusal is a hardcoded `False`. |
| **Money conservation is database-enforced** | `chk_settlement_conserved`, `chk_ls_split_conserved`, over a pure-integer split where the platform fee is the residual. |
| **Entitlement self-grant refused by a trigger** | Observed `23514`. Not RLS — RLS alone would permit it. Real defence in depth. |
| **Marketplace isolation holds behaviourally** | 5/5 ISOLATED, 0 LEAK, under a probe that writes and rolls back as a second real tenant. |
| **Paper accounting refuses `float` outright** | And recomputes equity rather than incrementing it, at zero tolerance. Rare and correct. |
| **Price tampering is structurally impossible** | The checkout request body has no price field at all. |
| **Webhook exactly-once survives Redis loss** | The DB unique constraint is the guarantee; Redis is only the first line. |
| **Enumeration is properly closed** | The detail endpoint re-makes the authorization decision rather than inheriting the list query's filter, and refusal is indistinguishable from absence. |
| **Fail-closed instincts throughout** | Kill-switch check failure blocks; unreadable equity blocks; entitlement read failure returns 503 rather than a false 403; `"unknown"` subscription status blocks a bot start; a `start_bot` command with no stated mode is refused rather than defaulted. |

---

## 6. THE PATTERN THAT EXPLAINS MOST OF THE DEFECTS

Three recurring causes account for nearly every finding, and each is more useful than its instances.

### 6.1 Controls that exist and are wired to nothing

| Control | Call sites |
|---|---|
| `require_aal2` | 0 |
| `enforce_kill_switch()` | 0 |
| `validate_safety_config_integrity()` | 0 — and compares `"live_trading"` to a value that is `"live"`, so it could never fire |
| `ExecutionGuard` ("validates EVERY trade") | cancel endpoints only |
| `rate_limit_middleware` | not installed |

The documentation reads as though these are active. They are not. This is the single highest-leverage cleanup:
each is already written and tested.

### 6.2 Comparisons that span two environment variables' value spaces

The worst defect in this repository's history was `get_vyomquant_mode() != "production"` — comparing
`VYOMQUANT_MODE` (`safe`/`paper`/`live`) against a value of the separate `ENV` variable. It **could never be
false**, so every environment silently received a per-process `MockRedisClient`, and every pod won its own
`SET NX` while reporting success.

That specific bug is fixed. The **same shape survives** in `core/rate_limit.py:13-16`, where `DEV_TEST_ENVS`
contains `"paper"` and `"safe"` and `env_raw = os.getenv("ENV") or get_vyomquant_mode(default="safe")`. With
`ENV` unset and `VYOMQUANT_MODE=paper` — the documented deployed value — rate limiting becomes per-process.

**`ENV=production` must be explicitly confirmed on the deployed task definition.** It is load-bearing for both
rate limiting and the Redis cache path.

### 6.3 Tests that agree with the bug

BUG-056 was missed by three layers **simultaneously**, which is the finding, not the bug:

| Layer | Why it could not catch it |
|---|---|
| `tests/test_settlement_service.py` + property suite | `FakeSupabase` has no triggers, no constraints, no RLS |
| `tests/test_expiry_sweep.py::test_the_column_contract_entry_covers_every_table_the_sweep_touches` | asserts against `COLUMN_CONTRACT`, a hand-maintained dict — the manifest was *correct*, the schema was wrong, and the test compared the manifest to itself |
| `tests/test_marketplace_paper_schema_contract.py` | parses migration **text** only (no PostgreSQL in CI, per its own docstring) and is structurally incapable of seeing a trigger body |

Closing it required an UPDATE to actually run against a real trigger on a real table. Nothing less would have
done, which is exactly what `tests/test_bug056_library_subscriptions_updated_at_postgres.py` now does.

---

## 7. TEST QUALITY

```
TEST QUALITY: AMBER
```

| Class | Instance |
|---|---|
| Mock-only coverage of the money path | `FakeSupabase` for all settlement tests |
| Self-referential schema assertion | `COLUMN_CONTRACT` compared to itself |
| Structurally blind static test | migration-text parser cannot see trigger bodies |
| Silently skipped integration test | `test_webhook_duplicate_delivery_real_redis.py` — **6 skipped**, no Redis. Reads as green. **Counted as NOT RUN.** |
| Opt-in invariant with near-zero coverage | 65 of 71 execution-engine sites do not state an environment, so the agreement test constrains 6 |
| Scope-limited guard test | `test_log_redaction.py` walks only two packages; a new unredacted logger elsewhere is **not** a failure |
| Absent concurrency coverage | no test for concurrent settlement vs earnings aggregation; none for the second `create_order` path; none demonstrating the TOCTOU window |
| Feedback-loop cost | one test file takes **35m29s** (1129 tests); developers will not run it locally |

**What the suite does well:** the `*_postgres.py` convention exists and is used for exactly the right things
(RLS, FK actions, isolation, identity types), the 12-probe database set is genuinely behavioural, and several
tests assert *refusals* rather than successes — `test_rate_limiter_fails_closed_in_production.py`,
`test_redis_fallback_does_not_fake_a_distributed_lock.py`, `test_account_freeze_fail_closed.py`,
`test_idempotent_submission_fails_closed.py`. That is the right instinct; it needs extending to the money path.

---

## 8. REGRESSION BASELINE (post-fix)

| Suite | Result | Artifact |
|---|---|---|
| PostgreSQL suite | 19/19 files green | `staging/pg_audit.log` |
| Database probe set | 12/12 PASS | `staging/probes_audit.log` |
| Marketplace batch A | 317 passed | `staging/bug056_batch_a.log` |
| Marketplace batch B | 728 passed, 1 skipped | `staging/bug056_batch_b.log` |
| Money / auth suite | 85 passed | `staging/money_test_run.log` |
| Schema-contract manifest test | 1 passed (37s) | `staging/schema_contract_targeted.log` |
| Marketplace isolation | 5 ISOLATED, 0 LEAK, 0 NO CONTROL, 0 NOT SEEDED | `staging/marketplace_isolation.json` |
| Live-trading gate | 5/5 refused, exit 0 | `staging/live_gate_final.log` |

**Not counted as a pass:** `test_webhook_duplicate_delivery_real_redis.py` — 6 skipped, no Redis running.

---

## 9. BUG-056 — CLOSED

```
BUG-056: FIXED / REGRESSION-SAFE
```

Every UPDATE to `library_subscriptions` failed with `42703`, so no paying customer could ever receive marketplace
access. Fixed by `backend_app/migrations/022_fix_library_subscriptions_updated_at.sql` (additive, idempotent,
with a generic trigger/column backstop). Regression test added that exercises the real trigger against a real
database. Verified by reproduction re-run, new test, two full marketplace batches, idempotent re-apply, and the
manifest test.

Renumbered from `021` after discovering `021_strategy_deployment_live_uniqueness.sql` already held that number;
the duplicate was removed.

**Not reopened** on account of the later tooling unreliability.

---

## 10. PRODUCTION

```
PRODUCTION: NOT CLASSIFIED
```

No DSN was available at any point. `.env` carries a placeholder `sqlite` `DATABASE_URL` and a `"dummy_key"`
Supabase key. Neither production verification script was run.

This does **not** improve because local tests pass, and it is **not** AMBER. Two facts make production
specifically *unknowable* rather than merely unmeasured:

1. **Three parallel migration trees** exist, and
   `backend_app/migrations/020_declare_pre_existing_tables.sql:1-30` states in its own header that the
   repository "cannot provision its own database" and that production's schema was partly applied
   out-of-band.
2. **`migrations/hardening/999_privilege_hardening.sql`** — which carries the backstops that abort provisioning
   on an exposed per-customer table — was validated *by live execution* against production, not by text review.
   Whether it was re-run after later schema additions is not derivable from the repository.

And the detail that makes this more than bookkeeping: **both confirmed RLS defects in this audit (BUG-056's
trigger and BUG-057's policy) originate in `archived_migrations/root_migrations/`.** "Archived" describes the
file's location, not whether its objects are live in production.

**No production remediation plan is offered**, per the audit's own rule: no production drift has been *observed*,
because production has not been read. Inventing one would be manufacturing evidence.

**If a DSN becomes available**, the approved order is:
1. `scripts/verify_production_schema.py` (read-only) first;
2. then, separately, `scripts/verify_production_security.py --i-understand-this-writes-and-rolls-back`;
3. against the direct `5432` endpoint, never the pooler.

Do **not** run `03-deploy.yml`. Do **not** run `alembic upgrade head`. Do **not** run production DDL.

---

## 11. LIVE TRADING

```
LIVE: OFF / NOT CERTIFIED
```

**OFF** is established by execution, not configuration review: all five env permutations refused, including both
required switches set to their enabling values. The final refusal comes from a hardcoded `bool = False`, so
enabling live trading requires a **code change**.

**NOT CERTIFIED** because the machinery behind the gate has nine open findings (§3 items 10-12 plus
`staging/trading_audit.json`), including a capital check with no reservation, a kill switch that self-expires,
and no demonstrated live accounting invariant.

The current posture is safe *because* the gate requires a code change. **That property should not be weakened to
a configuration flag before the trading findings are addressed.**

---

## 12. MINIMUM PATH TO PAPER-ONLY GO

In dependency order.

1. **Fix BUG-057.** Revoke column-level UPDATE on `moderation_status` / `is_active` / `is_featured` /
   `price_minor` from `authenticated`, **or** add a guard trigger analogous to `marketplace_subscription_guard()`.
   `WITH CHECK` alone is insufficient — the creator legitimately owns the row.
2. **Add a column allowlist to `PUT /api/strategies/{id}`.**
3. **Apply `require_aal2`** to exchange-key mutation, billing mutations, admin actions and password change.
4. **Add `UNIQUE (referral_commissions.payment_id)`** and determine which `process_referral_commission` is
   installed.
5. **Replace float money arithmetic** at `billing.py:2078` and `:2191`.
6. **Give the command queue a failure destination** — DLQ or a failure table — and make `start_bot` idempotent
   before moving the ACK.
7. **Make the health endpoints real.** Remove the hardcoded `"HEALTHY"` literals; have `/health/ready` check
   dependencies; include Redis in `all_ok`.
8. **Revoke `EXECUTE` from `PUBLIC`/`anon`/`authenticated`** on the four `SECURITY DEFINER` functions and add
   internal `auth.uid()` guards.
9. **Confirm `ENV=production`** on the deployed task definition.
10. **Add a regression test for each fix** following the `*_postgres.py` convention — against live policies and
    live triggers, not mocks.
11. **Re-run** the 19-file PostgreSQL suite, the 12-probe set, both marketplace batches, and the money/auth
    suite. **With Redis running**, so the webhook duplicate-delivery test executes instead of skipping.
12. **Then classify production**, using the approved scripts in the approved order, if a DSN is available.

---

## 13. SCOPE AND HONESTY STATEMENT

What this audit did **not** establish, listed so the report is not read as more complete than it is:

| Item | Status |
|---|---|
| Dynamic API security testing | **BLOCKED — TOOLING** |
| Active fault injection | **BLOCKED — TOOLING** |
| Performance: throughput, p50, p95, p99, error rate, queue latency | **BLOCKED — TOOLING** — no figure measured |
| Webhook duplicate delivery vs real Redis | **NOT RUN** — test skipped |
| Concurrent purchase / concurrent order, executed | **BLOCKED — TOOLING** |
| `EXECUTE` ACL on four `SECURITY DEFINER` functions | **NOT CLASSIFIED** — live-catalogue fact |
| `strategies` / `exchange_keys` / `copilot_*` RLS policy bodies | **NOT READ** |
| `billing.py` / `exchange.py` request models (mass assignment) | **NOT LOCATED** |
| `resolve_model_artifact_path` containment logic | **NOT READ** |
| `_mock_cache_is_acceptable()` env logic | **NOT READ** — same bug class as the worst historical defect |
| `uq_paper_fill_event` existence | **NOT VERIFIED** |
| Live trading accounting invariant | **NOT CLASSIFIED** — no module located |
| `CCXTExchangeExecutor` construction sites (tenant credential scoping) | **NOT ENUMERATED** |
| Admin-rejection effect on entitlement | **NOT TRACED** |
| Supabase project password-reset settings | **OUT OF SCOPE** — backend has no reset endpoint |
| Production schema, policies, grants, drift | **NOT CLASSIFIED** — no DSN |

Only **BUG-056** was remediated during this audit. Every other defect is reported, reproduced where possible, and
left for an explicit decision.

---

## 14. FINAL VERDICT

```
LOCAL BASELINE:   AMBER
MARKETPLACE:      NO-GO
MONEY SYSTEM:     NO-GO
TRADING:          NO-GO (live) / GO (paper execution path, with caveats)
PRODUCTION:       NOT CLASSIFIED
PAPER-ONLY:       NO-GO
LIVE:             OFF / NOT CERTIFIED
```

This system is closer to launchable than the verdict suggests, and the distance is specific rather than diffuse:
**nine gating defects, of which five are a few lines each** (apply a dependency, add a unique constraint, add a
column allowlist, replace a float division, delete three hardcoded literals). The financial core, the isolation
model and the live-trading gate are the hard parts, and they are the parts that are right.

The objective was not to make the report green. On the evidence gathered: **do not launch yet**, close §12
items 1-9, and re-audit production separately once a DSN exists.


---

# ADDENDUM (REMEDIATION PHASE) — UPDATED STATUS

Everything above is the pre-remediation record and is retained. This addendum supersedes
it. Full detail: `staging/REMEDIATION_REPORT.md`.

## Blockers now closed and verified

| ID | Finding | Status | Test |
|---|---|---|---|
| BUG-056 | `library_subscriptions` UPDATE failed `42703` | **CLOSED** | `test_bug056_library_subscriptions_updated_at_postgres.py` |
| BUG-057 | Creator could self-approve / self-feature a listing | **FIXED** (migration 023, column privileges) | 40 passed |
| API-01 | Mass assignment on strategy update | **FIXED** (allowlist, 422) | 64 passed |
| AUTH-01 | `require_aal2` wired to nothing | **FIXED** (exchange key mutations) | 16 passed |
| MONEY-01 | Referral commission payable twice | **FIXED** (migration 024, partial unique index) | 17 passed incl. 2-connection concurrency proof |
| MONEY-02 | Money divided as a binary float | **FIXED** (`_provider_major_amount`) | 49 passed |
| MONEY-04 | INR credited as USD (~83× overstated) | **FIXED** (refuse, log for manual settlement) | included above |
| WORK-01 | ACK in `finally` discarded failed jobs | **FIXED** (`Outcome` enum; FAILED stays pending) | 22 passed |
| WORK-02 | No DLQ, no PEL reaper, shared consumer name | **FIXED** (XAUTOCLAIM + bounded retry + dead-letter) | included above |
| OBS-04 | No worker ever wrote a health key | **FIXED** (`get_pool()` did not exist) | 15 passed |
| API-03 | Rate limiter read a trading mode as an environment | **FIXED + END-TO-END VERIFIED** | 131 passed; 15-row env matrix; 6/6 fail-closed under concurrency |

## P0 correction to this scorecard

This scorecard listed live trading as OFF on the strength of a hardcoded
`ExecutionFlags.LIVE_TRADING_ENABLED = False`. **That control is not on the order path.**
See `staging/TRADING_SAFETY_AUDIT.md` addendum and `REMEDIATION_REPORT.md` section 0.
New finding **TRADE-04 (P0, OPEN)**.

## Blockers still open

| ID | Sev | Finding | Status |
|---|---|---|---|
| **TRADE-04** | **P0** | Live-trading gate is not on the order path | **OPEN** |
| WORK-03 | P1 | Cross-pod duplicate `start_bot` | **OPEN** |
| TRADE-05 | P1 | Risk is a property of one caller, not of the gateway | OPEN |
| OBS-01 | P1 | Hardcoded `"HEALTHY"`; `/health/ready` checks nothing; Redis down ≠ degraded | **OPEN — not remediated** |
| QUOTA-01 | P1 | `HardQuotaEnforcer`'s 15 caps never invoked | OPEN |
| TENANT-01 | P1 | Tenant-isolation dependency family entirely unwired | OPEN |
| API-04 | P1 | Rate-limit store unavailability is a hard boot failure | OPEN (availability decision) |
| PORT-01 | P2 | `/api/portfolio/close-all` 500s on a nonexistent import | OPEN |
| AUTH-03 | P2 | `require_aal2` absent from billing/payment mutations | OPEN |
| PAPER-01 | P2 | BotRunner paper fills never reach the paper ledger | OPEN |
| TRADE-03 | P2 | Un-guarded `create_order` (dead, but a landmine) | OPEN |
| MONEY-05 | P2 | `subscription_tier NOT NULL` blocks unknown-tier credits | OPEN |

## Not completed

Task 9 (health endpoints) — characterised, not fixed. WORK-03 — not fixed. Task 13 (full
regression) — **not run**; all counts above are per-file.

## Classification

```
LOCAL BASELINE:   AMBER
MARKETPLACE:      GO
MONEY:            GO
TRADING PAPER:    NOT CLASSIFIED
TRADING LIVE:     OFF / NOT CERTIFIED
PRODUCTION:       NOT CLASSIFIED
PAPER-ONLY:       NO-GO
```


---

# ADDENDUM 2 (PHASE 3) — P0 CLOSED

Addendum 1 listed TRADE-04 as the open P0. It is now fixed. Addendum 1's record of the
withdrawn conclusion is retained deliberately.

## Closed this phase

| ID | Sev | Finding | Resolution | Evidence |
|---|---|---|---|---|
| **TRADE-04** | **P0** | No live-trading gate on the order path | `core/live_execution_gate.py` — the gateway mints an HMAC-signed `LiveOrderAuthorization` over 13 named checks, bound to one order; `place_order` refuses and consumes it at the venue boundary | 31 passed, oracle is a recording `create_order` with a mandatory positive control |
| **TRADE-05** | P1 | Risk was a property of one caller | `execute_with_idempotency` refuses a live order whose `risk_approved` is not `True`; `execute_trade` forwards none and can no longer trade live | same file |
| PORT-01 | P2 | `/api/portfolio/close-all` 500s on a nonexistent import | Corrected to `core.safety_config`, restoring the route's freeze check and `ExecutionGuard` | 7 passed |
| MONEY-05 | P2 | Unknown tier silently dropped the referral commission | Credit with sentinel `'unknown'` — the rate is flat so the tier does not change the amount owed; deliberately the opposite of MONEY-04 | 66 passed |

## Cumulative: closed and verified

BUG-056, BUG-057, API-01, API-03, AUTH-01, RLS-01, RLS-03, MONEY-01, MONEY-02, MONEY-04,
MONEY-05, WORK-01, WORK-02, OBS-04, PORT-01, TRADE-04, TRADE-05.

## Still open

| ID | Sev | Status |
|---|---|---|
| WORK-03 | P1 | OPEN — not attempted |
| TENANT-01 | P1 | OPEN — not attempted |
| QUOTA-01 | P1 | OPEN — not attempted |
| OBS-01 | P1 | OPEN — not attempted |
| API-04 | P1 | OPEN — availability decision required |
| AUTH-03 | P2 | OPEN — not attempted |
| PAPER-01 | P2 | OPEN — not attempted |
| TRADE-03 | P2 | OPEN — note the new gate does **not** cover it: `transactional_execution_manager` calls `create_order` directly, not via `place_order` |
| OBS-02, OBS-03, AUTH-02, RLS-02, API-02 | P2 | OPEN |

## Evidence

| Scope | Result | Artifact |
|---|---|---|
| All 10 remediation files | **293 passed, 0 failed** | `staging/remediation_all_RUNX4.txt` |
| Execution suite + the new gate | 245 passed, 0 failed | `staging/exec_regress_RUNP5.txt` |
| Sandbox lifecycle | 268 passed | `staging/collateral_RUNR7.txt` |
| Crash recovery, exchange e2e, ccxt, redaction | 181 passed | `staging/collateral_RUNS8.txt` |
| Rate limiter + real router graph | 131 passed | `staging/rl_pytest_RUNE5.txt` |
| Pre-existing worker/expiry/training | 143 passed, 1 xfailed | `staging/work01_regress_a.txt` |

**Full repository regression was NOT run.** No suite-wide
`COLLECTED / PASSED / FAILED / SKIPPED / XFAILED` figure exists, so none is reported.

## Classification

```
LOCAL BASELINE:   AMBER
MARKETPLACE:      GO
MONEY:            GO
TRADING PAPER:    NOT CLASSIFIED
TRADING LIVE:     OFF / NOT CERTIFIED
PRODUCTION:       NOT CLASSIFIED
PAPER-ONLY:       NO-GO
```

`PAPER-ONLY: NO-GO` — WORK-03, TENANT-01 and QUOTA-01 are unresolved and the full
regression has not run. Instruction 19 forbids GO while any of those stands.


---

# ADDENDUM 3 (PHASE 4) — TENANT ISOLATION MEASURED, SECOND WRITE PATH GATED

Addenda 1 and 2 stand, including addendum 1's record of the withdrawn live-trading
conclusion.

## Closed / reclassified this phase

| ID | Was | Now | Basis |
|---|---|---|---|
| **TENANT-01** | P1 OPEN | **P2 OPEN (downgraded, not closed)** | Live-DB negative matrix: **13 tables probed, 0 cross-tenant leaks** for read/update/delete, victim row verified unchanged. Positive + weakened-policy + RLS-off controls prove the harness can detect a leak. Decision recorded: do **not** install `TenantMiddleware` |
| **TRADE-03** | P2 OPEN | **FIXED** | Both raw venue writes in `transactional_execution_manager` now require the TRADE-04 `LiveOrderAuthorization`. 8 tests incl. a fully-armed refusal and a positive control |

## Why TENANT-01 was downgraded rather than closed or escalated

**Downgraded** because the feared defect is measured absent, and because the prescribed
fix turned out to be the wrong action: `core/dependencies.py:318` collapses `tenant_id` to
`user_id`, so the framework models a hierarchy that does not exist; and
`TenantMiddleware` would not have closed the one real gap, because it sets
`request.state.tenant` and never calls `set_config('app.current_tenant_id', …)`.

**Also a correction:** the audit treated `orders`/`positions`/`fills`/`execution_records`
as having only the inert tenant-setting policy. The live catalogue shows **three** policies
each, including a real `auth.uid()` owner policy for `authenticated`. They were never
unprotected on the request path.

**Not closed** because the owner-path RLS bypass is real: `FORCE ROW LEVEL SECURITY` is
set on **zero** tables and the SQLAlchemy connection is the table owner, so no policy
applies there. Plus `api_key_vault`'s service-role credential access, the billing-webhook
caller-supplied identity, and 16 of 29 target tables unprobed.

Full detail and seven prioritised follow-ups: **`staging/TENANT_ISOLATION_MAP.md`**.

## The authoritative venue-write inventory

Four boundaries exist in `backend_app/`, all now gated:

| # | Site | Gate |
|---|---|---|
| 1 | `backend/exchange_executor.py:1043` `create_order` | TRADE-04 (canonical) |
| 2 | `backend/exchange_executor.py:1109` `cancel_order` | canonical cancel path |
| 3 | `backend/transactional_execution_manager.py` `create_order` | TRADE-03 |
| 4 | `backend/transactional_execution_manager.py` `cancel_order` | TRADE-03 |

Pinned by an AST sweep that fails on any unclassified site. Its first run surfaced four
apparent new boundaries; all four proved to be local DB/state-machine calls whose method
names collide with CCXT's (`self.state_service.create_order`,
`self.order_repo.create_order`, `self.cancel_order`). The sweep now classifies by
receiver.

## Still open

| ID | Sev | Status |
|---|---|---|
| WORK-03 | P1 | **OPEN — not attempted** |
| QUOTA-01 | P1 | **OPEN — not attempted** |
| OBS-01 | P1 | **OPEN — not attempted** |
| API-04 | P1 | **OPEN — not attempted** |
| TENANT-01 | P2 | OPEN (downgraded) — owner-path RLS bypass, vault, webhook identity |
| AUTH-03 | P2 | **OPEN — not attempted** |
| PAPER-01 | P2 | **OPEN — not attempted** |
| OBS-02, OBS-03, AUTH-02, RLS-02, API-02 | P2 | OPEN |

## Evidence

| Scope | Result | Artifact |
|---|---|---|
| TENANT-01 cross-tenant matrix (live PostgreSQL) | 11 passed, **0 leaks / 13 tables** | `staging/tenant_matrix_RUNH5.txt` |
| TRADE-03 second write path | 8 passed | `staging/trade03_RUNK7.txt` |
| Execution + gate + tenant + port01 | **229 passed, 0 failed** | `staging/phase4_regress_RUNL8.txt` |
| Full-suite **collection** | **13,326 collected, 0 collection errors** | `staging/collect_RUNM9.txt` |
| Full-suite **execution** | see REMEDIATION_REPORT §Full regression | — |

## Classification

```
LOCAL BASELINE:   AMBER
MARKETPLACE:      GO
MONEY:            GO
TRADING PAPER:    NOT CLASSIFIED
TRADING LIVE:     OFF / NOT CERTIFIED
PRODUCTION:       NOT CLASSIFIED
PAPER-ONLY:       NO-GO
```

`PAPER-ONLY: NO-GO` — WORK-03 and QUOTA-01 remain unresolved P1s, OBS-01 health/readiness
is unresolved, and the full regression has no completed count. Instruction 10 of the phase
brief forbids GO while any of those stands.

---

# ADDENDUM 4 (PHASE 5) — THE FOUR REMAINING P1s RESOLVED

Addenda 1–3 stand, including addendum 1's record of the withdrawn live-trading
conclusion. Nothing in this addendum reopens TRADE-04, TRADE-05, TRADE-03, MONEY-01/02/04/05,
API-01, AUTH-01, WORK-01/02, OBS-04 or PORT-01; no regression demonstrated a bypass of any
of them.

## Closed / reclassified this phase

| ID | Was | Now | Basis |
|---|---|---|---|
| **WORK-03** | P1 OPEN | **FIXED** | PostgreSQL claim table keyed `(user_id, symbol)` (migration 025), `core/bot_ownership.py`, wired into `FleetManager` on the claim path and all **four** exit paths, plus a heartbeat loop that keeps the claim live for the bot's lifetime. 39 tests incl. 2- and 4-process races and a process-local negative control |
| **OBS-01** | P1 OPEN | **FIXED** | `core/readiness.py` measures dependencies and returns a real HTTP status; 4 endpoints rewired; Redis is a required readiness dependency outside local envs; `/health/services` now declares its own staleness. 28 tests |
| **API-04** | P1 OPEN | **CLOSED AS A DOCUMENTED DECISION** | Option A (fail closed at startup) **retained** and justified; Option B (degraded-mode availability) specified but deliberately **not** implemented. 12 tests pin the retained behaviour |
| **QUOTA-01** | P1 OPEN | **P2 OPEN (downgraded, not closed)** | Quotas **are** enforced, before the side effect, by `core/subscription_dependencies.py` at 13 measured call sites. `HardQuotaEnforcer` is unwired and must stay unwired. 17 tests |

## The third time the prescribed remediation was the wrong action

A pattern now established across three phases. In each case measuring first changed the
instruction:

| Phase | Instruction | What measurement showed |
|---|---|---|
| 4 | Install `TenantMiddleware` | It never calls `set_config('app.current_tenant_id', …)`, so it would not have closed the real gap. **Not installed.** |
| 5 | Wire `HardQuotaEnforcer` | Its usage counting reads the **fail-soft** Redis facade, so a Redis outage makes every count read zero and every quota pass. Wiring it would have introduced a **fail-open** path beside a working fail-closed one. **Not wired.** |
| 5 | Remove the API-04 startup failure | "Which endpoints are safety-critical" is a product decision. Shipping a middleware that classifies endpoints by an invented list is the unreviewed-policy pattern refused at MONEY-04 and TENANT-01. **Documented, not built.** |

## A correction to this phase's own WORK-03 claim

WORK-03 was reported closed earlier in this phase on the strength of 27 passing tests. That
report was **premature**, and the error is the same class this engagement was commissioned
to find — a control that exists and is wired to too little.

`bot_ownership.heartbeat()` was written, unit-tested and correct, but **called from nowhere
in `backend_app`**. Verified by grep: the only production importers were `claim_bot` and
`release_bot`. Consequence: the claim was never refreshed, so it went stale by its own
90-second predicate while the bot was still trading, and a second process calling
`start_bot` for the same `(user, symbol)` was then **granted** ownership. The original
defect was postponed by 90 seconds, not prevented.

A second defect surfaced from the same re-reading: `_boot_and_run` pops the fleet record
itself on a boot failure, so the done-callback that releases the claim found `None` and the
claim **leaked** — stranding the bot for a full stale interval after a failure that left
nothing running. `test_every_exit_path_releases_the_claim` had asserted over three exit
paths when there were four.

Both are now fixed and both are falsified rather than merely tested:
`staging/falsify_work03_fixes.py` neutralises each fix and confirms the oracle notices —
3/3 controls behaved. Full detail: **`staging/WORK03_CONCURRENCY_MAP.md`**.

## OBS-01 — what the health semantics now are

| Endpoint | Before | Now |
|---|---|---|
| `/health/ready` | always `200` + `"HEALTHY"` | `evaluate_readiness()`; `200` only if every **required** dependency is `UP`, else `503` |
| `/api/system/readiness` | same string | same measured report |
| `/api/system/dependencies` | asserted | measured per dependency |
| `/health/services` | undated snapshot read as live | `as_of: "process_startup"`, `captured_at`, `age_seconds`, explanatory note |

Required for readiness: **PostgreSQL**, and **Redis outside a declared local environment**
(it backs the API-03 rate limiter and the WORK-03 execution claims). **Not** required:
telemetry/QuestDB, which degrades to a mock by design. `UNKNOWN` fails closed — a
dependency we could not probe is not a dependency that is up. `_safe_detail()` scrubs DSNs
so a readiness probe cannot leak credentials.

Deliberately **not** done: changing `"HEALTHY"` to `"DEGRADED"` (a string, not a control),
and making readiness depend on optional services.

## API-04 — the availability decision

**Option A retained: the process refuses to start when the rate-limiter store is
unavailable outside a local environment.** Rationale: every request path in this
application is capable of a money or venue side effect, so there is no subset that is safe
to serve unlimited. A process that starts without the control it was configured to have is
a process whose security posture nobody has reviewed.

Option B — serve read-only/non-safety-critical endpoints in a declared degraded mode — is
specified in **`staging/API04_AVAILABILITY_DECISION.md`** and **not implemented**, because
three of its six items are new request-path machinery and the endpoint classification is a
product decision. 12 tests pin the retained behaviour, including that a store failure
*after* startup fails closed rather than silently passing traffic.

## QUOTA-01 — enforcement is real; the enforcer named in the finding is not the path

13 measured enforcement points, each a FastAPI `Depends` evaluated **before** the handler
body, so a breach prevents the side effect rather than recording it afterwards:

| Dependency | Call sites |
|---|---|
| `check_strategy_quota` | `strategies.py:1152`, `library.py:2199` |
| `check_bot_quota` | `strategies.py:2012` |
| `check_backtest_quota` | `strategies.py:2860`, `strategy_operations.py:1345`, `:1577` |
| `check_ml_quota` | `strategies.py:2475`, `strategy_operations.py:5930` |
| `check_optimization_quota` | `strategies.py:3472`, `:3541`, `:3601`, `strategy_operations.py:2227` |
| `check_marketplace_publish_quota` | `library.py:1893` |
| `check_marketplace_subscription_quota` | `library.py:2834` |

Fail-closed at `core/subscription_dependencies.py:649-654`, repeated at `:807`, `:823`,
`:858`: *a figure that could not be read is not a figure of zero — 503, not a pass.*

`HardQuotaEnforcer` is **deprecated in place, not wired**, for three reasons: its limit
source is `TenantQuota` rather than the subscription catalogue (two sources of truth); its
tenant model is the vestigial one; and decisively, its usage counting reads the fail-soft
Redis facade, so a Redis outage would make every count read zero and every quota pass.
Detail: **`staging/QUOTA_ENFORCEMENT_MAP.md`**.

Downgraded to P2 rather than closed because the quota **catalogue** is still duplicated
between `subscription_dependencies` and `TenantQuota`, and because no test exercises the
quota path against a real Redis.

## Still open

| ID | Sev | Status |
|---|---|---|
| QUOTA-01 | P2 | OPEN (downgraded) — duplicate quota catalogue; never exercised against real Redis |
| TENANT-01 | P2 | OPEN (downgraded) — owner-path RLS bypass (`FORCE ROW LEVEL SECURITY` on zero tables), vault service-role access, webhook caller-supplied identity, 16/29 tables unprobed |
| AUTH-03 | P2 | OPEN — `require_aal2` absent from `billing.py:1668` checkout, `:1946` verify-payment, `:2624` payment-methods, `referral.py:688` payouts, `library.py:2826`/`:3468`/`:3560` |
| PAPER-01 | P2 | OPEN — `BotRunner` paper fills never reach `paper_accounts`/`paper_orders`/`paper_fills`/`paper_positions`; user-visible accounting gap |
| OBS-02, OBS-03, AUTH-02, RLS-02, API-02 | P2 | OPEN |
| API-04 Option B | — | Specified, not implemented (improvement, not a defect) |
| WORK-03 reaper / claim re-check at venue boundary | P3 | Noted in `WORK03_CONCURRENCY_MAP.md` §12 |

## Evidence

| Scope | Result | Artifact |
|---|---|---|
| WORK-03 claim + concurrency + liveness (real PostgreSQL, real subprocesses) | **39 passed** | `staging/f_W4A1.txt` |
| WORK-03 falsification harness (fixes neutralised) | **3/3 controls behaved** | `staging/f_W4B2.txt` |
| OBS-01 readiness reflects dependencies | **28 passed** | `staging/obs01_OB1B2.txt` |
| API-04 store failure after startup | **12 passed** | `staging/api04_AP4B2.txt` |
| QUOTA-01 enforcement is wired | **17 passed** | `staging/quota01_QT1A1.txt` |
| Files affected by the WORK-03 completion fix | **168 passed, 1 skipped** | `staging/f_W4D5.txt` |
| Affected pre-existing suites (phase 5) | **112 passed** | `staging/phase5_P5D4.txt` |
| Full-suite **collection** | **13,326 collected, 0 collection errors** | `staging/collect_RUNM9.txt` |
| Full-suite **execution** | see "FULL REGRESSION" below | `staging/fullreg_v_FULLREG6.txt` |

> The earlier consolidated figure of 173 passed (`staging/phase5_P5A1.txt`) predates the
> WORK-03 completion fix, which added 12 tests. It is superseded by the full-suite
> execution result rather than re-reported here.

## Classification

```
LOCAL BASELINE:   AMBER
MARKETPLACE:      GO
MONEY:            GO
TRADING PAPER:    NOT CLASSIFIED
TRADING LIVE:     OFF / NOT CERTIFIED
PRODUCTION:       NOT CLASSIFIED
PAPER-ONLY:       NO-GO
```

All four phase-5 P1s are resolved, so the P1 ledger is clear. `PAPER-ONLY` nevertheless
stays **NO-GO**, on four independent grounds:

1. **PAPER-01 is unresolved and is a paper-mode defect specifically.** `BotRunner` paper
   fills never reach `paper_accounts`, `paper_orders`, `paper_fills` or `paper_positions`.
   A paper-only launch is precisely the configuration in which that accounting gap is
   user-visible on every trade.
2. **The full regression result governs** (phase-brief rule 9). See below.
3. **Two P2s were downgraded, not closed** — QUOTA-01's duplicate quota catalogue and
   TENANT-01's owner-path RLS bypass.
4. **`TRADING PAPER: NOT CLASSIFIED`** stands: no end-to-end paper-trading run was
   executed against a live venue sandbox in any phase. A paper-only GO cannot rest on unit
   tests alone.

`TRADING LIVE` remains **OFF / NOT CERTIFIED** and was never enabled at any point in this
engagement. `PRODUCTION` remains **NOT CLASSIFIED**: no production DSN was available, and
`.env` carries a placeholder `sqlite` `DATABASE_URL` and a `"dummy_key"` `SUPABASE_KEY`.

---

# ADDENDUM 5 (FULL REGRESSION) — THE SUITE WAS RUN, AND IT FOUND A P0

Addendum 4 reported the four phase-5 P1s closed on the strength of targeted subsets and
recorded the full regression as incomplete. **The full suite has now been executed twice to
completion.** It found 51 failures, 34 of them caused by the remediation, and one of those
34 was a production fail-open in the API-03 fix.

## The headline numbers

```
COLLECTED   13,435
ERRORS           0   (collection)
PASSED      13,046
FAILED          17
SKIPPED        363
XFAILED          6
XPASSED          3
```

`17 + 13,046 + 363 + 6 + 3 = 13,435`, which equals the collected count.
Duration 1:15:44. Artifact `staging/fullreg_v_FINAL2.txt`, junit `staging/fullreg_FINAL2.xml`.

Cross-checked against three independent sources — pytest's summary line, the junit XML
session attributes (`tests=13435 failures=17 errors=0 skipped=369`, where 369 = 363 + 6
xfailed), and a count of the per-test outcome lines. They agree.

**First completed run: 51 failed, 13,001 passed.** Second, after disposition: 17 failed,
13,046 passed.

## Why no prior phase produced this figure — PERF-01

Not scale. A harness defect. Every earlier attempt used
`--timeout=300 --timeout-method=thread`, and on Windows the thread method cannot interrupt
the test thread, so pytest-timeout dumps every stack and **terminates the whole pytest
process**. `test_p58_migration_set_is_idempotent` takes **1,029 seconds and passes**, so it
killed the run at a different percentage each time — 1%, 1%, 38% — with no summary line and
no junit XML. Four phases reported "full regression incomplete" because of one slow test and
one wrong flag.

## What the suite found that four phases of targeted subsets did not

| | |
|---|---|
| Remediation-caused failures found | **34** |
| Found by any targeted subset | **0** |

A targeted subset is chosen by the person who made the change. That is the structural
reason it cannot be evidence that the change broke nothing.

### RUNTIME-01 (P0-class) — a production fail-open inside the API-03 fix

`core/runtime_env.local_state_is_acceptable()` returned `True` on `DEV_MODE` **before
consulting `ENV`**. A deployment with `ENV=production` and a leftover `DEV_MODE=true`
resolved to *local*:

```
WARNING [RateLimit] Using PER-PROCESS in-memory rate limiting:
        explicitly local environment 'production'; no REDIS_URL.
```

The same line appeared for `staging`, `prod` and `live`. Because API-03 deliberately
centralised this judgement, **two** controls asked it and both failed open:

* `core/rate_limit.py` → per-process counters behind N replicas, so the effective limit
  becomes the configured limit × replica count.
* `core/cache/redis_manager.py` → the **mock cache**, which backs the distributed lock. A
  lock that reports success separately in every pod.

API-04's premise — that an unreachable rate-limit store is a hard boot failure — was
bypassable by one environment variable.

**My own 131-test API-03 suite could not have caught it, because it asserted the defect was
correct:** `test_explicit_dev_mode_may_use_memory_even_in_production`, reasoning "an
operator saying `DEV_MODE=true` has stated the intent explicitly". But an operator who set
`ENV=production` has also stated their intent, and the two contradict. The implementation
and its test were written together and shared the mistaken premise. The pre-existing tests
caught it because they set both variables and expected a refusal — and they only ran once a
full regression ran.

Fixed: a declared non-local `ENV` wins, an ignored `DEV_MODE` is logged at `CRITICAL`, and
`DEV_MODE` still works where the environment is silent or already local. Nine tests added,
each with a positive control.

### The other 33, by cause

| Cause | Count | Disposition |
|---|---|---|
| AUTH-01 `require_aal2` on the two credential endpoints | 8 | Tests given a real `aal2` claim, not an override of the control |
| TRADE-04 authorisation now required at the venue boundary | 11 | The file's single `_authorize()` helper now mints the authorisation too |
| TRADE-05 risk verdict + TRADE-04 attribution | 3 | One `_arm_risk_verdict()` helper supplies what the gateway supplies |
| API-01 allowlist refused `description` | 1 | **`strategies` has no `description` column** — the test was pinning the mass-assignment defect |
| RUNTIME-01 + allow-list/message drift | 11 | One code fix, four tests re-pointed or inverted |

## Two baseline tripwires fired, and both were right

`tests/regression/test_baseline_unchanged.py` caught TRADE-05 adding `risk_approved` /
`risk_reason` to the live-order contract, and AUTH-01 moving two `depends_on` bindings from
`get_current_user` to `require_aal2`. Both were re-recorded **with the authorisation written
into the baseline file beside the entry** as a `_baseline_note` member, which is the
protocol that file documents. The patch script changes only those two deltas and aborts on
an unexpected value — a blanket regeneration would have absorbed drift nobody authorised.

## The remaining 17, every one accounted for

**Zero are caused by the remediation.**

| Group | Count | Proof |
|---|---|---|
| `test_strategy_status_publish.py` | 4 | 4 failed at HEAD in a clean worktree |
| `test_task_8_4_runtime_readiness_gate.py` | 1 | 1 failed at HEAD |
| `test_validation_sweep[library.clone_strategy]` | 1 | 3 failed at HEAD — the remediation fixed two |
| **TEST-01** protected-logic containment | 1 | Substring collision: generated token `'vie'` inside the literal `node_pre`**`vie`**`w`. Not a leak |
| **SAST-01** SQL identifier interpolation | 1 | All 18 violations in an **untracked** alembic migration that did not exist at HEAD |
| **ENV-01** `connection_pooling_safety` | 1 | Real Binance call, `AuthenticationError -2008`, no venue credentials |
| **TEST-02** `test_task_6_1_backtest_execute.py` | 8 | 33 passed alone **and** 33 passed at HEAD; fails only in the full-suite ordering |

Attribution was measured, not judged: `git status --porcelain` for the modified set, and a
clean `git worktree` at commit `074b772a` run with the same interpreter for anything
ambiguous.

## Test integrity

**0 tests deleted. 0 marked skip. 0 marked xfail. +25 added.**

Every inverted test kept a **positive control** beside it, so none of the new assertions
could be satisfied by deleting the feature it guards — including the two
`test_dev_mode_is_honoured_where_the_environment_is_silent` controls, without which the
RUNTIME-01 regression would also have passed if `DEV_MODE` support had simply been removed.

## Classification

```
LOCAL BASELINE:   AMBER
MARKETPLACE:      GO
MONEY:            GO
TRADING PAPER:    NOT CLASSIFIED
TRADING LIVE:     OFF / NOT CERTIFIED
PRODUCTION:       NOT CLASSIFIED
PAPER-ONLY:       NO-GO
```

The P1 ledger is clear and the full regression is complete, so two of addendum 4's four
grounds for `NO-GO` are discharged. It stays **NO-GO** on the others, now joined by a
fourth:

1. **17 known-red tests.** All accounted for, none mine — but TEST-01 narrows a
   protected-logic containment control and SAST-01 is real SQL identifier interpolation.
   Both need an owner. A suite with known red tests is not a clean launch baseline.
2. **PAPER-01 (P2) is unresolved and is a paper-mode defect specifically.** `BotRunner`
   paper fills never reach `paper_accounts` / `paper_orders` / `paper_fills` /
   `paper_positions`. A paper-only launch is exactly the configuration where that
   accounting gap is visible on every trade.
3. **`TRADING PAPER: NOT CLASSIFIED`.** No end-to-end paper run against a venue sandbox was
   executed in any phase. A paper-only GO cannot rest on unit tests alone.
4. **RUNTIME-01 is a reason for caution about the whole deployment-configuration surface.**
   One environment variable silently disabled both the rate limiter and the distributed
   lock, and it survived a 131-test suite written specifically to prevent that class of
   defect. The fix is verified, but nothing has yet audited the *other* deployment
   variables for the same shape of precedence bug.

`TRADING LIVE` remains **OFF / NOT CERTIFIED** and was never enabled at any point.
`PRODUCTION` remains **NOT CLASSIFIED**: no production DSN was available, and `.env` carries
a placeholder `sqlite` `DATABASE_URL` and a `"dummy_key"` `SUPABASE_KEY`.

---
---

# ADDENDUM 6 (PHASE 6) — PAPER-01 CLOSED, THE PRECEDENCE SURFACE AUDITED

Addendum 5 ended with four grounds for `NO-GO`. This phase discharges two of them, audits the
surface the fourth was a warning about, and finds three more things on the way.

## Addendum 5's four grounds, re-answered

| # | Ground | Now |
|---|---|---|
| 1 | 17 known-red tests, TEST-01 and SAST-01 without owners | **8 fixed** (TEST-02, the order-dependent class). The remaining 9 each have a measured mechanism, a classification and a **named owner** — `staging/FULL_REGRESSION_FAILURE_DISPOSITION.md` Part 3. TEST-01 is a **false positive** with zero production call sites; SAST-01 is **0 violations at repository scope** (18/18 in one untracked file). Still red, now explained. |
| 2 | PAPER-01 unresolved | **FIXED and proven** against real PostgreSQL 16.2 — 39 passed. |
| 3 | `TRADING PAPER: NOT CLASSIFIED` | **Unchanged. Still NOT CLASSIFIED.** `PAPER SANDBOX E2E BLOCKED — NO SAFE SANDBOX CREDENTIAL`, established from four independent places, and no credential was invented. |
| 4 | Nothing had audited the other deployment variables for RUNTIME-01's shape | **Done.** 119 variables, 265 read sites, extracted from the AST. Two more instances found and fixed; three reported. `staging/ENVIRONMENT_PRECEDENCE_AUDIT.md`. |

## Closed and verified this phase

| ID | Finding | Verification |
|---|---|---|
| **PAPER-01** (P1) | BotRunner paper fills reach the canonical paper ledger. Routed through the existing `PaperTradingService.place_order` on the default paper account — **not** a second accounting implementation. A ledger refusal refuses the execution. | 26 + 13 = **39 passed**, real PostgreSQL, 0 skipped |
| **TEST-02** (P3) | The 8 order-dependent failures: orphaned slowapi `Limiter` instances left by module re-imports. Fixed in `tests/conftest.py`, not by changing assertions and not by forbidding the re-imports (which are the point of the tests that do them). | repro order **8 failed → 91 passed** |
| **ENV-02** (P3) | A `service_role` client no longer degrades to `None` in a declared production environment. | **55 passed**, falsified **6/6** |
| **ENV-05** (P3) | A trading mode no longer decides whether `/docs` and `/redoc` are served. | same |

## Opened this phase

| ID | Sev | Finding |
|---|---|---|
| **PAPER-02** | P2 | An unfundable paper market buy strands an `ACCEPTED`/`OPEN` order, `filled_quantity=0`, `rejection_reason=NULL`, answered as 500 `PAPER_INVARIANT_VIOLATION`. Requirement 16.5 wants `REJECTED` with a reason. Reachable from `POST /api/paper/orders`. **Gating for paper-only** — it is a paper-mode order-reporting defect in the exact surface a paper launch exposes. |
| **ENV-04** | P2 | `PRODUCTION_ROUTER_ENABLED` defaults to `"True"` in two modules, so its *absence* mounts `execution_router`. Code, docstring, four task definitions and `deployment_go_no_go.md` all disagree. Observed live: `INFO:Algo22: Production execution router ENABLED`. |
| **ENV-03** | P3 | The application loads `backend_app/.env`, not the repository-root `.env`. Seven placeholder credentials injected; a truthiness-based required-credential check passes on a placeholder. Does not ship (`.dockerignore`), so local/CI-fidelity only. |
| **API-05** | P3 | `/openapi.json` served in every environment. Production hides `/docs` and publishes the schema it renders from. |
| **LIB-01** | P3 | `_build_service_client` returns `None` from a function documented to raise; callers dereference it; the resulting 500 names the wrong cause. |
| **ENV-01** | P3 | The test suite makes an unmocked authenticated request to `api.binance.com`. No order placed, key is `key1`, but the suite is **not network-isolated**. |
| **TEST-04** | P4 | A tenth order-dependent failure, from `importlib.reload` invalidating a class identity. Not present in the repository's own collection order. |

## Corrections to this audit's own record

* **CORRECTION-005.** The first version of my order-isolation conftest guard was **inert** —
  keyed on the module limiter's `id()` alone, it scanned once before `backend_app.main` was
  imported, found nothing, and never scanned again. The 8 failures survived unchanged, which is
  how I noticed. Re-keyed on `(id(module limiter), id(app), route count)`.
* **CORRECTION-006.** My earlier report of fixing "`.env` missing `ENV=development`" edited the
  **repository-root** `.env`, which `core/config.py` does not load. **The claim as written was
  wrong.** The effect is in the safe direction by accident, not by design.
* **Two oracle errors in my own paper tests**, both real properties of the engine: quantization
  is per position, not per portfolio; and `equity == capital + realized_pnl − fees` is **not**
  an identity (`100008.48` vs `100008.49`). The false identity was **dropped**, not hidden
  behind a tolerance.

## §6.2 of the original scorecard, updated

The original §6.2 — "comparisons that span two environment variables' value spaces" — was a
hypothesis. It is now measured, and it was right:

* `core/rate_limit.py` listed trading modes as dev environments (API-03, fixed).
* `core/runtime_env.py` let `DEV_MODE` beat `ENV` (RUNTIME-01, fixed).
* `main.py` defaulted `ENV` **from** a trading mode (ENV-05, fixed this phase).
* `routers/library.py` / `routers/referral.py` never read `ENV` at all (ENV-02, fixed this
  phase).
* `core/safety_config.py` compares `VYOMQUANT_MODE` against `"live_trading"`, a value the
  variable never holds (TRADE-06, still open, zero call sites).

The original note — **"`ENV=production` must be explicitly confirmed on the deployed task
definition. It is load-bearing."** — is now quantified: `os.getenv("ENV", "development")`
appears at **20** independent `_is_production()` sites, so an unset `ENV` means *development*
at all twenty. The production task definitions do set it. It remains load-bearing and is now
the single most important line of deployment configuration in the system.

## Regression baseline

| | |
|---|---|
| Phase 5 baseline | 13,435 collected / 13,046 passed / **17 failed** / 363 skipped / 6 xfailed / 3 xpassed |
| **Phase 6 final** | **13,534 collected / 13,082 passed / 9 failed / 434 skipped / 6 xfailed / 3 xpassed** in 3282.70 s |
| arithmetic | 9 + 13,082 + 434 + 6 + 3 = **13,534** = collected; junit `tests=13534 failures=9 errors=0` |
| Added this phase | **+99 collected**, accounted id-by-id by diffing the two junit XMLs |
| Deleted / skipped / xfailed / relaxed to reach it | **0** |

**The 9 failures are exactly the 9 dispositioned pre-existing ones.** The eight
order-dependent failures are **absent** — `test_task_6_1_backtest_execute.py` does not appear
in the failure list, which is the full-suite confirmation that TEST-02 is closed.

Two things the measurement forced into the record rather than letting them pass:

* **The first Phase 6 run had 11 failures, not 9**, and one of the two extras was mine:
  `test_paper_no_random.py[bot_paper_ledger.py]` — the PAPER-01 seam added a first-party edge
  out of the paper package, and that file's **pinned dependency list** caught it while its AST
  walk and its runtime refusal were both green. Fixed to the control's own standard (read the
  module, prove no random draw reaches a price/quantity/fee/fill decision, say so above the
  list); 64 passed. Recorded as PAPER-03.
* **The other extra, TEST-05, passed in the second run** on the same selection — a Hypothesis
  *coverage census* (`sequence_length_10 occurred 0 time(s), floor 1`), which asserts that the
  generator produced a case, not anything about the system. Flaky by construction; the fix the
  test itself demands is in the generator.
  > **SUPERSEDED IN PART BY PHASE 10 (Workstream B). TEST-05 is now FIXED, and this phase's
  > attribution of it was wrong.** "Flaky by construction" and "the fix is in the generator"
  > both held. What did not hold is the inference drawn from the second run passing — that the
  > flake needed a full suite, because Hypothesis harvests constants from a larger
  > `sys.modules`. Measured in Phase 10: the shortfall reproduces **in isolation** in 12 of 150
  > independent 100-example runs (~8%), so a green run proved nothing at that rate. The real
  > mechanism is tree-guided prefix visitation against an **existence** floor. Full
  > measurement: `staging/PHASE10_TEST05_IDEMPOTENCY_REPORT.md`.

* **Skipped moved 363 → 434 (+71)** because this run deliberately cleared
  `AERORA_TEST_DATABASE_URL` to match the CI unit-test lane, while the Phase 5 final run had it
  set. Those 71 are `test_work03_bot_ownership_claims_postgres` (34),
  `test_paper01_botrunner_fills_reach_the_ledger_postgres` (26) and
  `test_tenant01_cross_tenant_matrix_postgres` (11) — all measured separately in the
  **PostgreSQL lane**: paper01 **39 passed**, tenant01 **11 passed**, work03 **39 passed**
  (60.80 s alone; the 2 failures seen when it ran concurrently with the 55-minute regression
  were subprocess-spawn starvation, settled by 60.80 s against 3932.79 s for the same file).

Full accounting: `staging/fullreg_phase6_counts.json` and
`staging/FULL_REGRESSION_FAILURE_DISPOSITION.md` Part 4.

## Classification

> **SUPERSEDED AS CURRENT STATE BY ADDENDUM 8 (PHASE 8), 2026-10-09.** The verdict below
> still holds — `PAPER-ONLY: NO-GO` — but two of its three grounds have moved and must not
> be read as current state: **PAPER-02 is now fixed**, and **"nine tests remain red" is
> stale** (the authoritative Phase 8 lanes report **2 in each lane** — the same two retained
> false positives — every one accounted for). The grounds that replace them are in
> Addendum 8. Ground 1 is unchanged.
> Retained here unedited as the Phase 6 record.

**`PAPER-ONLY: NO-GO`.**

Three grounds, and none can be discharged from inside this engagement:

1. **No venue-sandbox end-to-end paper run exists, in any phase**, because no safe credential
   exists. The brief requires the limitation be retained explicitly rather than argued away.
   `TRADING PAPER` stays **NOT CLASSIFIED**.
2. **PAPER-02 is open** and is a paper-mode defect in the surface a paper launch exposes.
3. **Nine tests remain red.** Every one is explained, classified and owned — none is mine, none
   was silenced — but a suite with known red tests is not a clean launch baseline, and two of
   the nine (LIB-01, ENV-01) are genuine defects rather than stale tests.

`TRADING LIVE` remains **OFF / NOT CERTIFIED** and was never enabled at any point in any
phase. Phase 6 did not touch the live gate; `master_executor`'s LIVE branch passes no paper
ledger, and the paper path was proven to construct no exchange adapter and decrypt no
credential — **by construction, not against a venue.**

`PRODUCTION` remains **NOT CLASSIFIED**: no production DSN was available at any point.

---

# ADDENDUM 8 (PHASE 8) — 2026-10-09. TENANT ISOLATION, MONEY INTEGRITY, TRADING SAFETY

**This addendum is the authoritative current state.** Addendum 6's classification is
retained above as history with a superseding note on the two grounds that moved.

**Substrate for every database figure: LOCAL PostgreSQL 16.2 via pgserver, migrations 026
and 027 applied. No production DSN existed in any phase of this audit. No Redis server. No
venue credential. No live trading was enabled, no exchange credential was added, no real
order was placed, and no production execution path was turned on.**

## The headline numbers

Recalculated from the new runs, not carried forward. Both lanes cross-checked against their
own JUnit XML — **7 of 7 checks OK in each**.

| | CI lane (`FULLREG_P8`) | PostgreSQL lane (`FULLREG_P8PG2`) |
|---|---|---|
| collected | **13,997** | **13,997** |
| passed | **13,468** | **13,922** |
| failed | **2** | **2** |
| errors | **0** | **0** |
| skipped | **518** | **64** |
| xfailed | **6** | **6** |
| xpassed | **3** | **3** |
| duration | 3,823.43 s (1:03:43) | 8,109.02 s (2:15:09) |
| arithmetic | 2+13468+518+6+3 = **13,997 = collected** | 2+13922+64+6+3 = **13,997 = collected** |

Delta against the Phase 7 baselines — **historical baselines, not targets**:

| | CI | PostgreSQL |
|---|---|---|
| collected | +105 | +105 |
| passed | +58 | +117 |
| failed | **−5** | **−12** |
| skipped | +52 | 0 |
| xfailed / xpassed | 0 / 0 | 0 / 0 |

Infrastructure-gated surface: **454 tests** that the CI lane could not measure and the
PostgreSQL lane did (518 − 64). That difference is why both lanes are kept as separate
result sets and neither is merged into the other.

### The xfail / xpass accounting convention

The two sources disagree **by design**, and the reconciliation accounts for it rather than
treating it as a discrepancy:

* **pytest's terminal summary** counts `xfailed` and `xpassed` as their own categories, so
  the arithmetic that reconciles against `collected` is
  `failed + passed + skipped + xfailed + xpassed`.
* **The JUnit XML has no such categories.** Its `testsuite@skipped` attribute **folds
  xfailed into skipped**, and its per-element count **folds xpassed into passed**. So a
  direct attribute-to-summary comparison is expected to differ by exactly the xfail and
  xpass counts.
* `pytest -q` prints no `collected N items` line, so `collected` is taken from the JUnit
  `tests` attribute. That source is recorded on every lane, so a reader can see the
  cross-check is not comparing a figure with itself.

## NEITHER LANE IS GREEN

Both lanes report **2 failures**, and they are the same two in each:

```
tests/property/test_protected_logic_containment.py::test_trace_and_export_surfaces_contain_no_protected_logic
tests/test_bandit_sast_gate_is_green.py::test_every_interpolation_into_sql_is_constrained_before_it_gets_there
```

Both are **retained false positives** with their exact mechanisms recorded in
`FULL_REGRESSION_FAILURE_DISPOSITION.md`. Neither was silenced, no assertion was weakened,
and in particular an already-applied Alembic revision was **not** edited to make a static
scan pass. `fullreg_phase8_counts.json` records `"fully_green": false` and names both.

## Closed this phase

| ID | Mechanism | Evidence |
|---|---|---|
| **TENANT-02** | migration 026: `fee_records` created, RLS enabled, owner policy on `auth.uid() = tenant_id`, both browser roles revoked | 22 tests, catalogue-read, owner policy exercised under a rolled-back `GRANT` |
| **MONEY-04** | stale documentation reconciled; residual closed by migration 027 (`currency TEXT NOT NULL DEFAULT 'USD'` + `CHECK`) | 24 tests, incl. duplicate delivery, retries, rollback, two-connection race |
| **TRADE-01** | `claim_execution_within_capacity` — advisory-lock-serialised capacity reservation, the `executing` row *is* the reservation | 28 tests, genuinely multi-connection; the defect demonstrated first |
| **TRADE-02** | kill-switch TTL made symmetric; "stopped" no longer decays to "permitted" | 30 tests, incl. a pre-fix negative control |
| **KILL-01** *(new)* | the kill switch **failed open** on any unreadable backend; and the mirror-image permanent lockout | two pre-fix negative controls reproduce both |
| **TRADE-10** *(new)* | `claim_execution`'s PostgreSQL branch had **never executed** — lowercase literals against an uppercase-only enum | probe + a regression that watches for the fallback warning |

## Still open — ten findings, each re-verified against the code

Not carried forward on the strength of an earlier summary.
`staging/verify_phase8_open_findings.py` restates each as a falsifiable check and runs it:
**10 checked, 10 confirmed open, 0 premise-gone, 0 inconclusive.** Three of those checks
were wrong on their first run and reported a premise gone; all three were defects in the
check (a text search that could not tell a call from a comment about a call) and were fixed
before the result was believed.

| ID | Sev | Confirmed by |
|---|---|---|
| **KILL-02** | P1 | `deactivate()` params = `['reason','self','triggered_by']` — no credential/role/operator. History retention **604,800 s on a Redis LIST** |
| **KILL-03** | P1 | `enforce_kill_switch()` has **0 call sites across 575 source files**; the `/api/admin/kill-all` handler exists and does **not** reference `GlobalKillSwitch` |
| **MONEY-06** | P1 | **zero** real `process_referral_commission` call nodes in any razorpay-named handler, and no `referral_commissions` INSERT — the only artefact of an owed commission is a log line |
| **KILL-04** | P2 | `"kill_switch"` is a literal check string at line 1270 while `is_active()` is genuinely called only at line 770 |
| **MONEY-07** | P2 | `_provider_major_amount` called with a **literal** currency at `(2130,'USD') (2303,'USD') (2523,'INR')`; `razorpay_webhook` never reads the captured payment's own currency |
| **FEE-01** | P2 | 2 × `UUID(int=0)` tenant fallbacks in `fee_engine.py` |
| **ML-01** | P2 | `MLExecutor` body: **0** `FeatureMatrix` references, 3 `pd.Series` |
| **TRADE-07** | P2 | no age-based `UPDATE` moves rows off `EXECUTING` anywhere. **Partially mitigated**: the 300 s stale cutoff stops such a row holding capacity; the row stays unclaimable |
| **TRADE-11** | P2 | the capacity SUM uses `NULLIF(price,'')::numeric`, so a priceless market order contributes nothing — the same blind spot the per-order check documents |
| **TEST-06** | P3 | 29 protected tokens, `['233','from']` ≤ 4 chars, `['233']` all digits — a substring match against a timestamp is possible, so the verdict depends on the clock |

TEST-06 is corroborated by the runs themselves: the containment check **failed** in
`FULLREG_P8PG` and **passed** in `FULLREG_P8PG2` with no change to it or to the code it
examines.

## Classification

### `PAPER-ONLY LAUNCH` — **NO-GO**

**PAPER-02 being fixed is explicitly not treated as sufficient**, and the grounds are no
longer the ones Addendum 6 gave. The current grounds:

1. **Two P1 findings sit on the emergency-stop control itself.** KILL-02: the kill switch
   can be reset by anyone who can call it, and the record of who stopped and restarted
   trading expires after seven days — from the same store whose unavailability the switch
   is meant to survive. KILL-03: **no operator-facing path engages it at all** — the
   audited, authenticated, `confirm_code == "HALT"` admin route stops the in-process fleet
   and leaves the distributed switch untouched, while the distributed switch has no human
   control. A paper-only platform still needs a halt it can trust.
2. **MONEY-06 is live money.** A referrer owed commission on an INR payment is paid
   nothing, and the only evidence the obligation exists is a log line. Paper trading does
   not insulate anyone from that.
3. **No venue-sandbox end-to-end paper run exists, in any phase**, because no safe
   credential exists. Unchanged from Addendum 6 and still not dischargeable from inside
   this engagement.
4. **Both lanes still report failures.** Explained, classified and owned — but a suite with
   known red tests is not a clean launch baseline, and TEST-06 means one of the red results
   is not even stable run-to-run.

### `TRADING LIVE` — **OFF / NOT CERTIFIED**

Unchanged, and deliberately so. The live order path remains unreachable by construction
through several independent refusals, and Phase 8 added a capacity reservation to that path
rather than opening it. The kill-switch work made the gate *more* restrictive: the clearest
evidence is that fixing KILL-01 **broke two of the repository's own live-order tests**,
which had been green only because the switch failed open — an order that previously reached
the CCXT mock is now stopped.

### `TRADING PAPER` — **NOT CLASSIFIED**

Isolation from live venues is isolation **by construction** — read off the code, the
environment guards and the independent refusals — **not venue-observed sandbox evidence**.
No venue was contacted. Those are different claims and this scorecard keeps them apart.

### `PRODUCTION` — **NOT CLASSIFIED**

No production DSN was available at any point in any phase. Every policy check, privilege
check, migration result and row count in this phase was measured against a local PostgreSQL
instance. Migrations 026 and 027 are applied locally and verified locally — both are
recorded in `public.schema_migrations` with `step=phase8`, and the catalogue confirms
`fee_records` at `rls=true / forced=false / 2 policies / 0 browser grants` and all three
referral tables at `currency text NOT NULL DEFAULT 'USD'` with a CHECK. **Their effect on
production is unverified.**

## Corrections to this audit's own record, this phase

Six, including two where I damaged or mis-numbered an artifact. Full text in
`REMEDIATION_REPORT.md` and the `corrections` array of all four JSONs.

* **CORR-P8-01** — MONEY-04 was carried into this phase as an open P0 for a defect that no
  longer existed. My Phase 7 scorecard propagated an open reading without re-verifying it.
* **CORR-P8-02** — I mis-recorded a contract test's scope as hard-coded to five migration
  files when it globs every `*.sql`.
* **CORR-P8-03** — I **destroyed** an uncommitted TRADE-05 baseline note with
  `git checkout --`; the replacement is an explicitly labelled reconstruction.
* **CORR-P8-04** — TRADE-02's finding named the wrong mechanism, directing remediation at
  code that was already correct.
* **CORR-P8-05** — the audit named an atomic claim that had never executed (TRADE-10).
* **CORR-P8-06** — three id/state contradictions: I reused `TRADE-06` and `TRADE-07`, and
  the `TRADE-07` collision **silently overwrote** a pre-existing finding; plus `PAPER-01`
  and `TEST-02` had each been listed as resolved *and* open since earlier phases. All found
  by `verify_phase8_consistency.py`, which exits non-zero rather than printing a report —
  which is the point: three contradictions had sat in four JSON files across multiple
  phases, and a printed reconciliation would have scrolled past them again.


# ADDENDUM 9 (PHASE 9) — 2026-10-10. OPERATIONAL SAFETY AND FINANCIAL INTEGRITY

Phase 9 closed the ten operational-safety and financial-integrity findings that ADDENDUM 8
("Still open — ten findings, each re-verified against the code") carried forward, closed one
new finding found during the work (TRADE-12), and completed the two authorised baseline
re-records. Each finding was independently re-verified against the code by
`staging/verify_phase9_open_findings.py` — **11 checked, 11 remediated or reclassified, 0
still present** — and the four audit JSONs were re-reconciled by
`staging/verify_phase9_consistency.py` (**all assertions pass; 0 orphans; classifications
intact**).

## Closed / reclassified this phase

* **KILL-02** FIXED — the kill-switch reset now requires `operator_id`,
  `authorization_source` and `acknowledge_holds` **by its signature** (keyword-only, no
  defaults), and every applied **and refused** transition is a durable, permanent-retention
  `safety_control_audit` row (migration 028). The pinned tripwire
  `test_reset_is_audited_but_takes_no_authorization` was **inverted** to
  `test_reset_now_requires_authorization_evidence`.
* **KILL-03** FIXED — `enforce_kill_switch()` gains its only call site at the venue boundary;
  `/api/admin/kill-all` activates the authoritative switch **before** the fleet fan-out; a new
  `GET /api/admin/kill-switch` exposes the state.
* **KILL-04** FIXED — that same boundary call re-reads live kill state immediately before
  `create_order`, replacing the stale `"kill_switch"` claim string.
* **MONEY-06** FIXED — an owed non-USD referral commission is now a durable
  `referral_obligations` row (migration 029). **No FX**: it is recorded `unresolved` with a
  NULL commission.
* **MONEY-07** FIXED — `_require_provider_currency` validates the provider's own declared
  currency and refuses a mismatch, instead of assuming the denomination.
* **FEE-01** FIXED — `TenantResolutionError` replaces the `UUID(int=0)` fallback (0 call nodes
  in executable code, AST-confirmed).
* **ML-01** CLOSED AS A FALSE POSITIVE — `MLExecutor.execute` raises on every reachable path
  and returns no value, so the "cannot consume a FeatureMatrix" branch is unreachable; **no
  nominal reference was added** (forbidden, and it would change nothing).
* **TRADE-07** FIXED — age-based classification and recovery of stale EXECUTING rows, wired
  into the watchdog; an ambiguous row is reconciled by `clientOrderId` and **never
  resubmitted**.
* **TRADE-11** FIXED — a missing/empty/zero price is **counted as unusable and refused** (fail
  closed), not summed as zero. The tripwire
  `test_a_market_order_with_no_price_is_documented_not_silently_zero` was **inverted**.
* **TRADE-12 (NEW)** FIXED — a dedicated `venue_order_id` column (migration 030, no FK) stops
  the venue id colliding with the `orders` foreign key on `order_id`, which had raised 23503
  on every real completion and stranded the row in EXECUTING.
* **TEST-06** FIXED — numeric protected-logic tokens matched on a numeric-value boundary,
  removing the wall-clock dependence; no short-token exemption added.

## P0 / safety ledger after Phase 9

No new P0 or P1 was opened. KILL-02/03/04 sit on the emergency-stop control itself and are now
closed with a signature-enforced authorised reset, a durable audit, and a genuine
live-state re-read at the venue boundary. The emergency-stop control is, for the first time in
this engagement, both reachable by an operator path and re-checked at the order boundary.

## Classification

Unchanged by Phase 9, and the reasons are unchanged:

* **PAPER-ONLY LAUNCH — NO-GO.** Operational safety is materially stronger, but no
  venue-sandbox end-to-end run exists in any phase and open findings remain (TENANT-01,
  QUOTA-01, CONN-01, and lower-severity carry-forwards). Local-substrate evidence is explicitly
  not treated as production or venue certification.
* **TRADING LIVE — OFF / NOT CERTIFIED.** Safe *by construction*, which is not live
  certification. No live trading was enabled, no credential invented, no real order placed.
* **TRADING PAPER — NOT CLASSIFIED.** No safe sandbox credential existed in any phase, so no
  end-to-end paper run against a venue was performed. Isolation is isolation by construction.
* **PRODUCTION — NOT CLASSIFIED.** No production DSN in any phase; migrations 028–030 are
  verified against a local PostgreSQL 16.2 only.

## Corrections to this scorecard, Phase 9

* **CORR-P9-01** — my own `verify_phase9_open_findings.py` first reported FEE-01 and TRADE-11
  STILL PRESENT; both were the verifier's own substring false positives and were corrected to
  AST. Sixth/seventh text-scan false positive in the engagement, again in the audit's own
  detector rather than the product.
* **CORR-P9-02** — the intermediate CI run's 12 failures were all collateral from correct
  Phase 9 changes (an F821, a monkeypatch-bypassing local import, a stale test double, a stale
  gitignored SQLite scratch schema, a SQLite/PG asymmetry, and the two legitimately-shifted
  baselines); 10 fixed, 2 re-recorded, FINAL lanes re-run.


# ADDENDUM 10 (PHASE 10) — 2026-10-10. REMAINING LAUNCH BLOCKERS & INDEPENDENT VERIFICATION

Phase 10 continued from the Phase 9 report. It repaired the two retained full-regression
failures, closed CONN-01 and two new financial-integrity findings, closed the QUOTA-01
residual, added TENANT-01 negative-control coverage, and assessed the referral settlement and
exchange-reconciliation startup. Each finding was independently re-verified against the code by
`staging/verify_phase10_open_findings.py` (**6 checked, 0 status-lies**), and the four audit
JSONs re-reconciled by `staging/verify_phase10_consistency.py` (**all assertions pass; 0
orphans; classifications intact**).

## Closed / covered this phase

* **CONN-01** FIXED — `store_keys` now calls `release_exchange` after the vault write, so a
  credential rotation evicts the stale pooled client and the new credential takes effect
  in-process. (`tests/test_conn01_rotation_evicts_pooled_client.py`, 4 passed.)
* **FEE-02** (new) FIXED — a third-asset fee was lost to a `fee_pct` NOT NULL violation;
  `fee_pct` is now nullable (model + migration 031), `fee_amount`/`fee_asset` stay mandatory.
  (`tests/test_fee_third_asset_pct_is_nullable_postgres.py`, 4 passed.)
* **MONEY-08** (new) FIXED — `create_payout_request` wrote the non-existent columns
  `payment_method`/`payment_details`; corrected to the provisioned `payout_method`/
  `payout_details`; the API field is unchanged. (`tests/test_referral_payout_schema_contract_postgres.py`, 5 passed.)
* **QUOTA-01** residual CLOSED — the metered reservation now has a concurrency test (N racers,
  hard limit K → exactly K admitted) plus a real-Redis-gated variant; HardQuotaEnforcer stays
  correctly unwired. (`tests/test_quota01_metered_reservation_is_hard.py`, 6 passed / 1 skipped.)
* **The two retained Phase 9 failures** FIXED — TEST-05 (deterministic `@example` boundaries;
  the CI lane now has 0 failures) and the PostgreSQL tenant-isolation sweep (the real gap was
  `referral_obligations`, a Phase 9 table, missing a seed override — not `risk_settings`).

## Still OPEN after Phase 10

* **TENANT-01** (P2) — architectural residuals unchanged (FORCE RLS unset, service-role
  bypass on the ORM path, billing-webhook caller-supplied `user_id`); deliberate decisions.
  Phase 10 added credential-vault cross-tenant negative-control coverage and one more swept
  table, but did NOT change the architecture, so the finding remains OPEN.
* **RECON-01** (P2, new) — `ExchangeReconciliationService` is never started, so venue
  order/fill reconciliation is not operational. Reported, not fabricated (wiring it needs a
  sandbox exchange, which the engagement forbids). The escalation scheduler IS started.
* **Referral settlement** — the pending→approved→paid lifecycle and payment initiation are
  unimplemented. A payout stays pending (fail-safe; no money moves). Not fabricated.

## P0/P1 ledger after Phase 10

No new P0 or P1 opened. CONN-01, FEE-02 and MONEY-08 were P2/P3-class financial/operational
defects. The two findings left OPEN (TENANT-01, RECON-01) are both P2 and both deliberate
(architectural decision / needs a sandbox exchange).

## Classification (UNCHANGED, and the reasons are unchanged)

* **PAPER-ONLY LAUNCH — NO-GO.** Stronger posture, but no venue-sandbox end-to-end run exists,
  RECON-01 means venue reconciliation is not operational, referral settlement is unimplemented,
  and TENANT-01's residuals remain. Local-substrate evidence is not venue or production
  certification.
* **TRADING LIVE — OFF / NOT CERTIFIED.** No live trading, no credential, no real order.
* **TRADING PAPER — NOT CLASSIFIED.** No venue-sandbox evidence in any phase.
* **PRODUCTION — NOT CLASSIFIED.** No production DSN; migration 031 verified locally only.

## Corrections to this scorecard, Phase 10

* **CORR-P10-01** — the Phase 9 report's `risk_settings` sweep-failure attribution was stale;
  the real NOT-PROVEN table was `referral_obligations`, a Phase 9 table added without its seed
  override. Found by measuring the JUnit, not trusting the report.
* **CORR-P10-02** — TEST-05's `sequence_length_10` floor relied on a random draw within 100
  examples; forced with `@example` per the census helper's own policy.
* **A full-lane process note** (not a finding): the first Phase 10 CI run skipped all ~14,000
  tests at `exit=0` because a new, uncommitted `tests/exchange_sandbox/conftest.py` skipped the
  WHOLE session instead of its own directory. Caught by reading the JUnit XML (an all-skip with
  exit 0 looks green), fixed by scoping the skip. See FULL_REGRESSION_FAILURE_DISPOSITION.md.

## Regression (both lanes, cross-checked)

* CI (`FULLREG_P10`): 14292 collected, **13618 passed, 0 failed**, 666 skipped, 5 xfailed, 3 xpassed.
* PostgreSQL (`FULLREG_P10PG`): 14316 collected, **14211 passed, 1 failed**, 94 skipped, 7 xfailed, 3 xpassed.
  The 1 failure is `test_rate_limiter_concurrency::test_different_endpoints_run_concurrently`,
  a wall-clock timing flake under heavy machine load that passes deterministically in isolation
  (3/3). Not a product defect, not a Phase 10 regression.
