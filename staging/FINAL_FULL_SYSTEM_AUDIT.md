# FINAL FULL-SYSTEM PRE-LAUNCH AUDIT

**Scope:** Aerora / VyomQuant crypto algorithmic-trading SaaS, repository state at `c:\aerora_quant_backend_updated_final1`.
**Date:** 2026-10-07
**Method:** static code reading + behavioural execution against a real local PostgreSQL 16.2 instance. No production access.

---

## 0. HOW TO READ THIS DOCUMENT

A finding is **PASS** only when a control was observed to *refuse* the thing it exists to refuse, or when the
absence of a risk was established structurally (the parameter does not exist, the column is not selected, the
grant is not held). A route existing, a function existing, a test existing, or a test passing against a mock is
not PASS.

| Verdict | Meaning |
|---|---|
| **PASS** | Control verified. Where behavioural, the refusal was executed and observed. |
| **FAIL** | Defect confirmed and reproduced. |
| **AMBER** | Works, but with a documented limitation or inconsistency that is not itself a defect. |
| **BLOCKED — TOOLING** | Could not be freshly executed because the local shell backend became unreliable. Last reliable evidence preserved. |
| **NOT CLASSIFIED** | Evidence genuinely unavailable (production). **Never** downgraded to AMBER. |
| **NOT IMPLEMENTED** | The feature does not exist. Not a defect by itself. |

`NOT CLASSIFIED` ≠ `AMBER`. Unknown production state is `NOT CLASSIFIED` or `RED`, never AMBER.

---

## 1. EXECUTIVE SUMMARY

The system has an unusually strong *financial core* and an unusually weak *operational perimeter*.

What is genuinely well built, and was verified by execution rather than assertion:

- **Live customer trading cannot be switched on by configuration.** Five env-var permutations were executed,
  including the decisive case with both required switches set to their enabling values. All five refused.
  The final refusal comes from `ExecutionFlags.LIVE_TRADING_ENABLED`, a hardcoded `bool = False` read from no
  environment variable at all — so enabling live trading requires a **code change**, not a config change.
- **Marketplace money conservation is enforced by the database**, not by application arithmetic
  (`chk_settlement_conserved`, `chk_ls_split_conserved`), over a pure-integer minor-units split.
- **Paper-trading accounting refuses `float` inputs outright** and recomputes `total_equity` from components
  rather than incrementing it, with a zero-tolerance invariant check.
- **Marketplace tenant isolation holds behaviourally** — 5/5 tables ISOLATED, 0 LEAK, under a probe that writes
  and rolls back as a second real tenant.
- **Self-activation of a paid entitlement is refused by a database trigger**, not by application logic
  (`trg_subscription_transition_guard`, observed returning `23514`).

What is not ready, and the reasons are specific rather than atmospheric:

- **One P0 was found and fixed during this audit** (BUG-056). It had made *every* marketplace
  purchase-to-entitlement activation fail. It is now fixed and regression-tested.
- **A second confirmed defect of the same class remains open** (BUG-057): a creator can self-approve, self-price
  and self-feature their own marketplace listing by writing directly to the row, because the UPDATE policy has
  no `WITH CHECK` and the table carries no transition-guard trigger. Reproduced freshly.
- **`PUT /api/strategies/{id}` has no column allowlist.** A raw caller dict goes into the UPDATE. The handler's
  own source comment says so.
- **MFA exists and is applied to zero endpoints.** `require_aal2`'s own docstring names exchange-key mutation,
  admin endpoints, password change and billing mutations as mandatory; none of them use it.
- **There is no dead-letter queue anywhere**, and the command worker acknowledges Redis Stream messages in a
  `finally` block — so a failed bot-start command is discarded permanently, and a crash between work and ack
  double-starts a bot.
- **Referral commission has a credible double-credit path**, and the installed function version is not
  determinable from the repository.
- **Production remains unobservable.** No DSN. Three parallel migration trees exist and the repository's own
  migration `020` states in writing that which one was applied to production is unknown even to its authors.

---

## 2. BUG-056 — CLOSED

```
BUG-056: FIXED / REGRESSION-SAFE
```

**Defect.** `trg_library_subscriptions_updated_at` unconditionally assigned `NEW.updated_at` on every UPDATE to
`public.library_subscriptions`. The table had no `updated_at` column. PL/pgSQL raises
`42703 record "new" has no field "updated_at"` rather than ignoring the assignment, so **every** UPDATE to that
table failed — including `settlement_service.settle()`, the function that moves a subscription from `pending` to
`active` after a confirmed payment. No customer who paid for a marketplace subscription could receive access.

**Not silent.** `settle()` re-raises as `SettlementPersistenceError`; the webhook handler answers HTTP 500 and
logs it. The provider would retry and fail identically. The ledger row in `marketplace_settlements` is written
*before* this UPDATE and is unaffected, so the condition is loud and reconcilable — but it is still a complete
outage of entitlement activation.

**Fix.** `backend_app/migrations/022_fix_library_subscriptions_updated_at.sql` — additive
`ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`, idempotent, plus a generic backstop that scans every
trigger function in `public` for a `NEW.<col>` assignment targeting a column its own table does not declare and
aborts provisioning if one is found. The column was added rather than the trigger removed because
`expiry_sweep.py` already documents `updated_at` as a maintained column it writes.

**Numbering.** Originally written as `021`; renumbered to `022` after discovering `021_strategy_deployment_live_uniqueness.sql`
(a legitimate pre-existing migration from an earlier audit phase) already held that number. The duplicate was removed.

**Evidence of closure:**

| Evidence | Result |
|---|---|
| `staging/reproduce_updated_at_trigger_bug.py` re-run | legitimate settlement UPDATE **succeeds** (previously `42703`) |
| `tests/test_bug056_library_subscriptions_updated_at_postgres.py` (new, real trigger, real DB) | 2 passed |
| Marketplace regression batch A | 317 passed |
| Marketplace regression batch B (incl. new test) | 728 passed, 1 skipped |
| Migration re-application | idempotent (notice, not error) |
| `test_every_manifest_pair_exists` against fixed migration set | 1 passed in 37s |

**Why no existing test caught it** — three independent layers agreed with the bug:
1. `tests/test_settlement_service.py` and the property suite exercise `settle()` against `FakeSupabase`, a
   Python double with no trigger.
2. `tests/test_expiry_sweep.py::test_the_column_contract_entry_covers_every_table_the_sweep_touches` asserts
   `"updated_at" in tables["library_subscriptions"]` against `COLUMN_CONTRACT` — a hand-maintained Python dict.
   The manifest was *correct*; the schema was wrong; the test compared the manifest to itself.
3. `tests/test_marketplace_paper_schema_contract.py` parses migration **text** only (no PostgreSQL in CI, per its
   own docstring) and therefore cannot see a trigger function body.

This is the clearest instance in the repository of the mocked-too-heavily failure class. See §12.

---

## 3. TOOLING RELIABILITY DISCLOSURE

Partway through this audit the local PowerShell execution channel began returning **stale, out-of-order output**
from commands issued minutes earlier, and timing out on trivial commands (`Get-Date`). This is a tooling fault,
not a system fault.

Mitigations actually used, and they are stated so the evidence can be weighed:

- Every result below that is marked as executed was read **from a log file on disk**, not from shell stdout.
- Freshness was established by **content specificity** — newly generated UUIDs and error strings matching the
  specific invocation — not by the process tracker's `running`/`stopped` label, which was demonstrated to be
  unreliable.
- `read_file`, `list_directory` and `list_processes` remained reliable throughout and were preferred.
- Nothing was marked PASS on the strength of an old command printing PASS, and nothing was marked FAIL on the
  strength of an old command printing FAIL.

Items that could not be freshly executed are marked **BLOCKED — TOOLING** with their last reliable evidence
preserved, rather than assigned a verdict.

---

## 4. SUBSYSTEM VERDICTS

| Subsystem | Verdict | Basis |
|---|---|---|
| Live-trading gate | **PASS** | Executed, 5/5 cases refused. §5 |
| Marketplace money conservation | **PASS** | DB CHECK constraints, int-only split |
| Marketplace tenant isolation | **PASS** | 5/5 ISOLATED, 0 LEAK, behavioural |
| Marketplace entitlement self-grant | **PASS** | DB trigger refused it (`23514`), observed |
| Marketplace listing moderation integrity | **FAIL** | BUG-057, reproduced. §6 |
| Marketplace purchase→entitlement activation | **PASS (post-fix)** | BUG-056 closed. §2 |
| Marketplace asset security / IDOR | **PASS** | No file served; no caller-supplied subscription id exists |
| Marketplace reviews | **PASS** | Dup impossible (DB UNIQUE), range-validated, XSS-safe + regression test |
| Marketplace enumeration | **PASS** | Detail endpoint re-checks independently; identical 404 for all refusals |
| Marketplace refunds | **AMBER** | Full refunds correct & immediate; **partial refunds record nothing** |
| Auth — JWT | **PASS** | ES256/JWKS; HS256 test fallback fails closed on unset ENV |
| Auth — role enforcement | **PASS** | `app_metadata` only; no mass-assignment path to role found |
| Auth — MFA | **FAIL (not enforced)** | `require_aal2` applied to zero endpoints. §7 |
| Auth — email verification | **AMBER** | No backend gate; delegated entirely to Supabase config |
| Auth — password reset | **NOT IMPLEMENTED (backend)** | Entirely Supabase-hosted; unauditable here |
| Tenant ownership (sampled routers) | **PASS** | Owner-scoped query filters, not mere authentication |
| RLS — marketplace tables | **PASS** (except BUG-057) | Behavioural probe |
| RLS — `FORCE ROW LEVEL SECURITY` | **AMBER** | Absent on every table; deliberate and documented |
| RLS — `SECURITY DEFINER` functions | **AMBER / NOT CLASSIFIED** | 4 fns lack caller validation; EXECUTE ACL needs live check |
| Money — marketplace | **PASS** | §MONEY_FLOW_AUDIT |
| Money — subscription billing | **AMBER** | Float division on money; no DB idempotency backstop |
| Money — referral | **FAIL** | Double-commission path. §MONEY_FLOW_AUDIT |
| Money — reconciliation vs provider | **NOT IMPLEMENTED** | No such job exists |
| Trading — paper/live boundary | **AMBER** | Enforced, but by executor presence + opt-in agreement |
| Trading — capital gate | **FAIL** | Read-then-compare, no reservation → TOCTOU |
| Trading — kill switch | **AMBER** | Works; self-expires after 24h |
| Trading — accounting invariant (paper) | **PASS** | Zero-tolerance, recomputed not incremented |
| API — injection (SQL/cmd) | **PASS** | Parameterized; no shell |
| API — SSRF | **PASS** | Parse-based validator; value never dereferenced |
| API — mass assignment | **FAIL** | `PUT /api/strategies/{id}` has no allowlist |
| API — CORS | **PASS** | Wildcard-with-credentials structurally impossible |
| API — rate limiting | **AMBER** | Fails closed, but only if `ENV` is set |
| Workers — idempotency / DLQ | **FAIL** | No DLQ; ACK-in-`finally`; no PEL reaper |
| Observability — secret redaction | **AMBER** | Real and installed, but scoped to 12 loggers |
| Observability — health checks | **FAIL** | Hardcoded `"HEALTHY"`; `/health/ready` checks nothing |
| Observability — audit trail | **PASS** | Mandatory on settlement/reinstatement |
| Performance / failure injection | **BLOCKED — TOOLING** | §PERFORMANCE_AUDIT |
| **Production** | **NOT CLASSIFIED** | No DSN; migration provenance unknown. §13 |

---

## 5. LIVE TRADING — VERIFIED OFF

Executed via `staging/verify_live_trading_off.py`, output `staging/live_gate_final.log`, `EXIT=0`.

| Case | Verdict observed |
|---|---|
| production as deployed (`VYOMQUANT_MODE=paper`, flag unset) | BLOCKED |
| flag `true` but mode still `paper` | BLOCKED |
| mode `live` but flag unset | BLOCKED |
| deprecated alias `AERORA_ENABLE_LIVE_TRADING=true`, mode paper | BLOCKED |
| **both switches set: mode=live AND flag=true** | **BLOCKED** |

The last row is the decisive one. With both documented enabling switches set, execution is still refused — by
`ExecutionFlags.LIVE_TRADING_ENABLED`, which is `bool = False` at class definition
(`backend_app/core/safety_config.py:114-116`) and re-zeroed at module import (`:273-280`), read from no
environment variable.

```
LIVE TRADING: OFF / NOT CERTIFIED
```

OFF is established by execution. NOT CERTIFIED because the trading-safety findings in
`staging/TRADING_SAFETY_AUDIT.md` (TOCTOU capital gate, kill-switch expiry, second un-guarded exchange write,
opt-in environment agreement) have not been remediated — the gate being shut is not the same as the machinery
behind it being certified.

---

## 6. BUG-057 — OPEN, CONFIRMED

```
BUG-057 [P1]: library_strategies — creator can self-approve, self-price and self-feature
STATUS: FAIL (reproduced). NOT FIXED.
```

**Intended rule, established from the code, not assumed.** `admin_moderate_strategy`
(`backend_app/routers/library.py:2670`) requires `Depends(get_admin_user)`, writes through a **service-role**
client (which bypasses RLS entirely), and its own comment states that the Submission state machine and its six
admin actions are *"the only path that moves the effective lifecycle."* Creator self-approval is not the
intended behaviour.

**What the database actually permits.** The UPDATE policy
(`archived_migrations/root_migrations/004_rls_library_strategies.sql:21-27`) is:

```sql
CREATE POLICY "Users can update their own strategies"
  ON public.library_strategies FOR UPDATE TO authenticated
  USING (author_id = auth.uid());
```

No `WITH CHECK`. No column restriction. And — confirmed by grepping every `*.sql` in the repository —
`library_strategies` carries only `trg_library_strategies_updated_at` (timestamp bump) and `trg_ls_price_mirror`
(price mirror). **There is no transition-guard trigger**, unlike `library_subscriptions`, which has
`trg_subscription_transition_guard` and which is precisely why the analogous self-activation attack fails.

**Reproduced** (`staging/reproduce_listing_self_approve.py` → `staging/self_approve_recheck.log`), freshly, after
the BUG-056 fix:

```
seeded listing dca2d1e7-…: pending, inactive, price_minor=999999, not featured
UPDATE statement affected 1 row(s)
result: moderation_status='approved' is_active=True price_minor=1 is_featured=True
```

**Reachability: LATENT.** `algo22-terminal/src` was grepped for `.from('library_strategies')` and
`.table('library_strategies')` — zero matches. No frontend code path touches this table directly. The exploit
requires a valid `authenticated` JWT issued against raw PostgREST. The anon-key client *is* in the browser
bundle (`algo22-terminal/src/supabase.js`), so the capability is present even though the application does not
exercise it.

**Classified FAIL, not AMBER.** The intended rule is unambiguous and it is violated. The absence of a control is
the defect; current non-exploitation by the app's own code is mitigation, not correctness. Same risk class as the
TRUNCATE finding from an earlier phase.

**Remediation shape** (not applied — local fix deferred, see §14): `WITH CHECK (author_id = auth.uid())` is
necessary but **insufficient alone**, because the creator legitimately owns the row and the check would pass. The
fix must either (a) revoke column-level UPDATE on `moderation_status`, `is_active`, `is_featured`, `price_minor`
from `authenticated` and route them through service-role only, or (b) add a guard trigger analogous to
`marketplace_subscription_guard()` that refuses any transition not originated by an admin action.

---

## 7. CROSS-CUTTING FINDING: CONTROLS THAT EXIST BUT ARE NOT WIRED

This is the dominant pattern of the audit and it deserves naming once, centrally. In five separate places a
correct, well-documented control is implemented and then applied to nothing:

| Control | Defined at | Call sites |
|---|---|---|
| `require_aal2` (MFA/AAL2 gate) | `core/dependencies.py:456-489` | **zero** |
| `enforce_kill_switch()` | `core/global_safety.py:430-443` | **zero** |
| `validate_safety_config_integrity()` | `core/safety_config.py:18-55` | **zero** — and compares `"live_trading"` against a value that is `"live"`, so it could never fire |
| `ExecutionGuard` ("the FINAL CONTROL LAYER that validates EVERY trade") | `backend/execution_guard.py` | two **cancel** endpoints only; never on the submit path |
| `rate_limit_middleware` | `core/rate_limit_middleware.py` | not installed in `main.py` |

Each is individually a modest finding. Together they mean the repository's *stated* safety posture and its
*enforced* safety posture differ, and the documentation reads as though the controls are active. `require_aal2`
is the sharpest: its docstring mandates it for exchange API key mutation, and
`POST /api/exchanges/keys` (`routers/exchange.py` ~`:309-320`) depends only on `get_current_user`.

---

## 8. WHAT WAS VERIFIED BEHAVIOURALLY (not statically)

Listed separately because the audit brief distinguishes these sharply.

| Claim | How observed | Artifact |
|---|---|---|
| Live execution refused in all 5 env permutations | executed | `staging/live_gate_final.log` |
| Buyer cannot self-activate a pending subscription | executed; `23514` refusal | `staging/self_activate_postfix.log` |
| Creator **can** self-approve own listing | executed; update applied | `staging/self_approve_recheck.log` |
| Legitimate settlement UPDATE now succeeds | executed | BUG-056 reproduction re-run |
| Marketplace tables isolate across tenants | executed, write+rollback as 2nd tenant | `staging/marketplace_isolation.json` (5 ISOLATED / 0 LEAK) |
| 19/19 PostgreSQL regression files green | executed | `staging/pg_audit.log` |
| 12/12 database security probes pass | executed | `staging/probes_audit.log` |
| 1045 marketplace/money tests green post-fix | executed | `staging/bug056_batch_a.log`, `_b.log` |

---

## 9. REGRESSION BASELINE (post-fix)

| Suite | Result | Log |
|---|---|---|
| PostgreSQL suite | 19/19 files green | `staging/pg_audit.log` |
| Database probe set | 12/12 PASS | `staging/probes_audit.log` |
| Marketplace batch A | 317 passed | `staging/bug056_batch_a.log` |
| Marketplace batch B | 728 passed, 1 skipped | `staging/bug056_batch_b.log` |
| Money / auth suite | 85 passed | `staging/money_test_run.log` |
| Schema-contract manifest test | 1 passed (37s) | `staging/schema_contract_targeted.log` |

One skip is material and is **not** counted as a pass: `tests/test_webhook_duplicate_delivery_real_redis.py`
reported **6 skipped** in `staging/pg_audit.log` because no Redis was running. Webhook duplicate-delivery
behaviour against *real* Redis is therefore **BLOCKED — TOOLING / NOT RUN**, not verified.

---

## 10. DEFECT REGISTER

| ID | Severity | Area | Status |
|---|---|---|---|
| BUG-056 | P0 | Marketplace entitlement activation | **FIXED / REGRESSION-SAFE** |
| BUG-057 | P1 | Marketplace listing moderation (RLS) | **OPEN — confirmed** |
| API-01 | P1 | `PUT /api/strategies/{id}` mass assignment | OPEN |
| AUTH-01 | P1 | `require_aal2` enforced nowhere | OPEN |
| MONEY-01 | P1 | Referral double-commission | OPEN |
| MONEY-02 | P1 | Float division on money (`billing.py:2078`, `:2191`) | OPEN |
| WORK-01 | P1 | No DLQ; ACK-in-`finally`; no PEL reaper | OPEN |
| TRADE-01 | P1 | Capital check TOCTOU (no reservation) | OPEN |
| TRADE-02 | P1 | Kill switch self-expires after 24h | OPEN |
| OBS-01 | P1 | Hardcoded `"HEALTHY"`; `/health/ready` checks nothing; Redis down ≠ degraded | OPEN |
| OBS-02 | P2 | Redaction scoped to 12 loggers; catch-all prints raw traceback | OPEN |
| TRADE-03 | P2 | Second un-guarded `create_order` (`transactional_execution_manager.py:1072`) | OPEN (possibly dead code) |
| AUTH-02 | P2 | WebSocket JWT in query string (`dag_tasks.py:302`, `dag_event_loop.py:2123`) | OPEN |
| RLS-01 | P2 | 4 `SECURITY DEFINER` fns without caller validation | OPEN / ACL NOT CLASSIFIED |
| RLS-02 | P2 | `strategy_deployments`/`signals`/`strategy_versions` UPDATE lack explicit `WITH CHECK` | OPEN (mild) |
| OBS-03 | P2 | `/metrics` unauthenticated | OPEN |
| API-02 | P2 | `RequestValidationError` handler preserves `exc.errors()` (can echo input) | OPEN |
| API-03 | P2 | Rate-limit fail-closed depends on `ENV` being set | OPEN |
| MONEY-03 | P2 | No reconciliation job vs provider source of truth | NOT IMPLEMENTED |
| MKT-01 | P2 | Partial refunds record nothing, entitlement untouched | OPEN (documented requirement gap) |

---

## 11. COMPANION DOCUMENTS

| Document | Covers |
|---|---|
| `staging/MARKETPLACE_SECURITY_AUDIT.md` | Marketplace deep-dive, BUG-056/057 detail |
| `staging/MARKETPLACE_TEST_MATRIX.md` | Per-scenario marketplace matrix with verdicts |
| `staging/MONEY_FLOW_AUDIT.md` | Every balance mutation, conservation, idempotency |
| `staging/TRADING_SAFETY_AUDIT.md` | Order paths, live gate, risk, kill switch |
| `staging/AUTH_RLS_AUDIT.md` | Auth, RLS, grants, SECURITY DEFINER |
| `staging/API_SECURITY_AUDIT.md` | IDOR, injection, SSRF, mass assignment, rate limits |
| `staging/FAILURE_INJECTION_AUDIT.md` | Redis loss, worker crash, duplicate webhook |
| `staging/PERFORMANCE_AUDIT.md` | Load/latency — largely BLOCKED |
| `staging/FINAL_LAUNCH_SCORECARD.md` | Verdicts and gating conditions |

---

## 12. TEST-QUALITY FINDINGS (summary; detail in scorecard)

The audit's most transferable result is not any single defect but the demonstration that this repository's test
suite can be **simultaneously green and wrong** about a P0.

1. **Mock-only coverage of the money path.** `FakeSupabase` has no triggers, no constraints, no RLS. Every
   settlement test built on it would pass against a schema that cannot accept a single UPDATE.
2. **Self-referential schema assertions.** `COLUMN_CONTRACT` is a hand-maintained dict; a test comparing the
   handler's reads to that dict proves the handler agrees with the dict, not with PostgreSQL.
3. **Text-only migration parsing.** The schema-contract test reads migration *text* and so is structurally
   incapable of seeing a trigger body — which is where BUG-056 lived.
4. **Silently skipped integration tests.** 6 skipped webhook-duplicate tests read as a green suite.
5. **Opt-in invariants.** 65 of 71 execution-engine construction sites do not state an environment, so the
   environment-agreement test constrains only the 6 that do.
6. **Absent concurrency coverage** for simultaneous settlements against the creator-earnings aggregation, and for
   the second `create_order` path.

---

## 13. PRODUCTION

```
PRODUCTION: NOT CLASSIFIED
```

No production DSN was available at any point. `.env` carries a placeholder `sqlite` `DATABASE_URL` and a
`"dummy_key"` Supabase key. Neither `scripts/verify_production_schema.py` nor
`scripts/verify_production_security.py` was run against production.

This is **NOT CLASSIFIED and not AMBER**, and it does not improve because local tests pass. Two facts make it
specifically unknowable rather than merely unmeasured:

- Three parallel migration trees exist (`backend_app/migrations/`, `migrations/`,
  `archived_migrations/root_migrations/`), and `backend_app/migrations/020_declare_pre_existing_tables.sql`
  states in its own header that the repository "cannot provision its own database" and that production's schema
  was partly applied out-of-band.
- `migrations/hardening/999_privilege_hardening.sql` — which carries the backstops that would abort provisioning
  on an exposed per-customer table — was itself validated *by live execution* against production, not by text
  review. Whether it was re-run after later schema additions is not derivable from the repository.

**No production remediation plan is offered here**, per the audit's own rule: no production drift has been
*observed*, because production has not been read. Inventing a remediation plan for an unobserved state would be
manufacturing evidence.

If a DSN becomes available, the approved order is: `scripts/verify_production_schema.py` first (read-only), then
separately `scripts/verify_production_security.py --i-understand-this-writes-and-rolls-back`, against the direct
`5432` endpoint, never the pooler. Do not run `03-deploy.yml` or `alembic upgrade head`.

---

## 14. WHAT THIS AUDIT DID NOT DO

Stated so the report is not read as more complete than it is.

- **Did not fix BUG-057.** A safe fix requires a product decision between column-level revocation and a guard
  trigger, and it touches the moderation path that the Submission state machine owns.
- **Did not fix the other open defects.** Only BUG-056 was remediated, because it was a complete outage with an
  additive, idempotent, test-covered fix.
- **Did not enable live trading, run production DDL, run `03-deploy.yml`, or run `alembic upgrade head`.**
- **Did not run performance or load tests** — see `staging/PERFORMANCE_AUDIT.md`.
- **Did not audit** `routers/billing.py` / `routers/exchange.py` request models for mass assignment (models not
  located), `resolve_model_artifact_path` containment logic, `_mock_cache_is_acceptable()` env logic, or the
  concrete assertions of five named redaction/error tests.
- **Did not verify** the `EXECUTE` ACL on the four `SECURITY DEFINER` functions — that is a live-catalogue fact.
- **Did not verify** webhook duplicate delivery against real Redis (skipped, no Redis running).
