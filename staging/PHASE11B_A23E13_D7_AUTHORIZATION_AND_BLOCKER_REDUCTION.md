# Phase 11B-A23E13 — D7-1 Authorization Readiness and Parallel Blocker Reduction

**READ-ONLY. No production or test file changed. No test executed. No Redis, database, exchange,
credential or production access. No CI dispatched. Nothing staged, committed, pushed or reset.**

**Live trading: OFF / NOT CERTIFIED.**

Destination verified absent before writing. A concurrent session's
`staging/PHASE11B_A23E14_REGISTER_AUTHORIZATION_AND_BLOCKER_REDUCTION.md` covers much of the same
ground and was **read, not modified**. **This report is deliberately scoped to what A23E14 does not
contain** (§1), adopts its one decisive correction (§3), and does not restate its shared findings.

---

## 1. Executive summary — four findings new to this phase

**The headline is not A23. It is that `ecd23a9e` "launch phase 1" is pushed to `origin/main`
untested, undeployed and failing security.**

| # | New finding | Status |
|---|---|---|
| **1** | **`01 PR Check` — the lane holding the full ~11,000-test suite and the ephemeral Redis service — has NEVER run at this HEAD.** Its most recent run is a *different, older* SHA, and that run failed | **VERIFIED via `gh`** |
| **2** | **`03 Deploy` FAILED at HEAD**, refused by its own `Pre-Deployment Validation Gate` at the step *"Require the security scan to have passed for this commit"*; the ECS Fargate deploy was **skipped**. **`05 Security` FAILED twice** — `Bandit SAST` and `Trivy IaC (blocking, CRITICAL)` | **VERIFIED via `gh`** |
| **3** | **`access_token` was never a valid parameter of `load_decrypted_keys`.** Git history across all commits yields exactly one signature form, with two parameters. The broken call sites and the signature entered the tree in the *same* commit `6c5660a1` | **VERIFIED via `git log -S` / `-p`** |
| **4** | **CI already provisions an approved isolated Redis**, and the `/15`→`/0` keyspace fix is committed — but the workflow holding it did not trigger, so `E-6` is now unexecuted for a *new* reason | **VERIFIED in committed workflow** |

Finding 3 is the one that de-risks a D7-1 patch: the brief asks whether removing `access_token`
would lose intended authentication or tenant-context behaviour. **It would not — the keyword never
bound to anything.**

**Authorization status: unchanged. 0 of 8 A23 gates; no D7-1, W-6, QUOTA-01, TENANT-01 or worker
grant located. No file was edited in any workstream.**

---

## 2. Entry / exit repository state

| Invariant | Entry | Exit |
|---|---|---|
| Branch / HEAD | `main` / `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | unchanged |
| Parent | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | unchanged |
| **`origin/main`** | **`ecd23a9e` — 0 ahead / 0 behind: HEAD is pushed** | unchanged |
| `git write-tree` | `4b45a7633f76cd8ab5be023c1e45af7f1a63c398` | unchanged |
| Staged / unmerged | 0 / 0 | 0 / 0 |
| Unstaged tracked | 2 | 2 |
| Untracked | 25 | 27 (this report + one CI probe artifact) |
| Worktrees | 1 | 1 |

**Target blob fingerprints — all clean, all unchanged entry to exit:**

| File | Blob (12) |
|---|---|
| `backend_app/backend/api_key_vault.py` | `74a005941146` |
| `backend_app/routers/orders.py` | `ff2d34bf030a` |
| `backend_app/routers/market.py` | `16f0377cb9f6` |
| `backend_app/backend/exchange_executor.py` | `df4fd42a1510` |
| `backend_app/backend/redis_manager.py` | `d7b0e811b615` |
| `tests/test_market_none_keys_fix.py` | `4a14a0916b50` |

The two dirty files are the W1D classifier workstream's, **not this one's**:
`staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md`, `tests/test_drift_classification.py`.

**Continuity-anchor note.** I adopt A23E14 §7.F's point: the pre-commit staged digests
(`2e994018…`, and my own `65dfc453…`) are **unreproducible** now that staged = 0, and must be
retired. **Blob hashes are the stable anchors** from here.

---

## 3. Authorization precedent — adopting A23E14's decisive correction

A23E12 (mine) established that W1D-12 proves an in-session, role-attributed grant is a valid
instrument here. **A23E14 finding 3 sharpens that in a way that changes the ask, and I adopt it:**

> "W1D-12's precedent does not transfer to A23, because the `Code owner` role **pre-existed** in the
> ledger's Owner column before the grant. Redis has no such row."

Verified: `PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md:802` already carried `Code owner` in its Owner
column *before* any grant, with status `NOT GRANTED`. The user then acted **in a role a register had
already assigned**. For `backend_app/backend/redis_manager.py` **no register row exists at all**, so
there is no assigned role to act in.

**Consequence: A23 needs two steps, not one.** (1) a register row establishing the role, (2) a grant
issued in that role. A23E14 §4.1–4.2 drafts both; I do not duplicate that wording. This supersedes
the single-step ask in my own A23D §6 and A23E6 §3.A.

---

## 4. Workstream A — D7-1 · defect verified, **and the patch de-risked**

Shared findings (3 call sites, the `MockEngine` masking path, the test double that accepts the
invalid kwarg) are established in A23E12 §7.A and A23E14 §7.A and are not restated.

### 4.1 New: `access_token` never bound to anything

The brief required verifying *why* the argument was added before proposing its removal. Measured:

```
git log -S "def load_decrypted_keys" -- backend_app/backend/api_key_vault.py   -> 6c5660a1
git log -S "access_token=user.get"  -- routers/market.py routers/orders.py     -> 6c5660a1
git log --all -p -- backend_app/backend/api_key_vault.py | grep 'def load_decrypted_keys'
  -> exactly one unique form:
     +    def load_decrypted_keys(self, user_id: str, exchange_id: str) -> dict:
```

**One signature form in the entire history, two parameters, no `access_token`** — and the method and
its three broken callers entered the tree in the **same commit**. The keyword was wrong from birth;
it never reached a parameter, never reached the body, and never carried authentication or tenant
context anywhere.

**Therefore deleting the three `access_token=` lines removes no behaviour.** That is the specific
assurance the brief asked for, and it was not previously established. The change is a pure deletion
of three lines that cannot bind.

### 4.2 What a patch would and would not fix

| Site | Deleting the kwarg fixes | Leaves open |
|---|---|---|
| `orders.py:789-793` (unguarded, 500s) | yes — endpoint stops raising | — |
| `orders.py:1095-1099` (masked, returns `[]`) | yes | the bare `except Exception` at `:1110` still masks *other* failures |
| `market.py:50-54` (masked, anonymous fallback) | yes — and it makes the `:56` guard **reachable** for the first time | the `except Exception` at `:67` still converts a genuine vault outage into silent anonymous data |

**Honest limit:** the three-line deletion fixes the *binding* defect. It does **not** fix the
*policy* defect at `market.py:67` — distinguishing "user has no keys" (public fallback is correct)
from "vault failed" (should surface). Those are two grants, and conflating them would expand scope.

### 4.3 Test matrix — designed, not written

Server-free, no credentials, no network. All would fail before the patch.

| # | Test | Mechanism |
|---|---|---|
| 1 | Every call site binds against the **real** signature | `inspect.signature(APIKeyVault.load_decrypted_keys).bind(...)` over AST-extracted call sites — the pattern `staging/p11bd11_d71_bind_probe.py` already prototypes |
| 2 | `market._get_data_engine` does **not** return an anonymous engine after a vault *failure* | inject a vault raising `RuntimeError`; assert no public `ConnectionEngine` |
| 3 | Explicit public market-data access still works | preserved-behaviour control |
| 4 | `GET /api/orders/open` does not 500 on a valid vault | `TestClient` with a stub vault |
| 5 | Genuine no-keys (`ValueError`) is distinguished from vault failure | two different injected vaults, two different outcomes |
| 6 | The existing double's signature matches the real one | guards the `test_market_none_keys_fix.py` masking regression |

Blocking static check for any such patch: **flake8** (`static-audit` job).

**Authorization: ABSENT.** No grant located in any authoritative record. **No file touched.**

---

## 5. Workstream B — W-6 · repository-wide control-flow proof

Branch ordering and both defects are established in A23E12 §7.B. **New here: the brief required
searching the entire repository rather than relying on one caller.** Done — every occurrence of
`.retryable` / `retryable ==` / `if … retryable`:

| Site | Kind |
|---|---|
| `exchange_executor.py:92` | assignment in `ExchangeError.__init__` |
| `exchange_executor.py:1257` | assignment in the fallback branch |
| **`exchange_executor.py:1687`** | **f-string log line — the only read** |
| `exchange_simulator.py:67` | assignment |
| `tests/exchange_sandbox/test_sandbox_order_lifecycle.py:587,601` | assertions (sandbox-gated) |
| `tests/test_exch01_sandbox_endpoint_verification.py:123` | assertion |
| `replay_safe_transaction_guard.py:304` | **its own** `self._is_retryable_error(e)` — independent |
| `paper_simulator.py:1691,3168` | **its own** `_is_retryable(exc)` — independent |
| `staging/p12_w6_*.py` | probes |
| `staging/.ccxt_upgrade_probe/.../urllib3/util/retry.py` | vendored third-party |

**Conclusion, now proven repository-wide: zero production control flow reads
`ExchangeError.retryable`.** The two modules that *do* retry on classification use their own private
predicates. So the misclassification **mislabels the recorded outcome and the operator-facing
message; it does not suppress a retry.** Per the brief, I do not overstate it as retry suppression.

Deterministic default-lane cases are designable without credentials — `_handle_ccxt_error` is a pure
method reachable via `object.__new__(CCXTExchangeExecutor)`, the pattern
`staging/p12_w6_classifier_probe.py` already uses. The seven cases the brief enumerates are all
expressible that way. **Authorization ABSENT — no test file created.**

---

## 6. Workstream C — QUOTA-01 / Redis · **the infrastructure exists, in CI**

Local, re-measured: `REDIS_URL`, `AERORA_TEST_REDIS_URL`, `REDIS_HOST`, `REDIS_PORT` **all unset**;
TCP 127.0.0.1:6379 **not reachable**; `redis-py 5.0.8` installed. No value printed.

**New: an approved isolated Redis is already committed in CI.** `.github/workflows/01-pr-check.yml`:

```yaml
    services:
      redis:
        image: redis:7-alpine
        ports: [6379:6379]
        options: --health-cmd "redis-cli ping" --health-interval 10s ...
    env:
      REDIS_URL: redis://localhost:6379
      AERORA_TEST_REDIS_URL: redis://localhost:6379/0     # the /15 -> /0 fix, COMMITTED
```

The container is ephemeral and exclusive to the job — exactly the isolation guarantee Workstream C
item 4 asks for. The long-running D5′ question (`/15` vs `/0`) is **resolved in committed source**;
the workflow's own comment block explains that `get_redis_url(db)` appends `?db=`, which redis-py
resolves over the path, so `/15` sent the handler to database 0 while the test read 15.

**But see §9: `01 PR Check` did not run at this HEAD.** So `E-6` moves from "no infrastructure" to
**"infrastructure committed, lane not triggered."** That is a smaller, differently-shaped gap — and
it needs no new provisioning decision, only a run.

Four suites present: `test_quota01_enforcement_is_wired.py`, `test_quota01_exchange_slot_is_atomic.py`,
`test_quota01_hard_enforcer_is_deprecated_in_code.py`, `test_quota01_metered_reservation_is_hard.py`.

**Real-Redis: UNEXECUTED.** No URL invented, no fixture altered, no suite run against an unknown
target. This is missing evidence, not a skip and not a pass.

---

## 7. Workstream D — TENANT-01 · static only

`FORCE ROW LEVEL SECURITY`: repository-wide search returns matches **only** in reports, scorecards
and registers — **zero executable SQL statements** in `migrations/`, `backend_app/migrations/` or
`backend_app/alembic/versions/`. Corroborated by `PHASE11A_READINESS_SCORECARD.json`
(`tenant_force_rls: OPEN`) and `PHASE11_LAUNCH_BLOCKER_REGISTER.md:564-567`, which records the live
instance as `relrowsecurity = true, relforcerowsecurity = false`.

Same mechanism as my earlier CD-1: RLS enabled but unforced, ORM connecting as table owner, so
policies are inert on that path; `tenant_db_context.py` unwired.

**Evidence separation, as required:** the absence of `FORCE` statements and the unwired GUC are
**source-level facts**. Current production enforcement is **NOT** established — no database was
contacted. Any tenant count must use `COUNT(DISTINCT user_id)` or `EXISTS`, since one tenant may
hold several `exchange_keys` rows; **not run**. **Tenant isolation is NOT certified.**

Smallest defensible sequence (design only, no edit): wire the GUC per request **or** connect as a
non-owner role → *then* a migration issuing FORCE RLS behind a flag with verified rollback, staging
first. Needs the DB-role decision; highest blast radius of any item here.

---

## 8. Workstream E — workers

Verified from `backend_app/main.py` code, not comments:

| Component | Started? | Evidence |
|---|---|---|
| `ReconciliationScheduler` | **YES** | `:456` start, `:457` status, `:542` stop |
| `OrderWatchdog` | **YES** | `:432` start, `:433` status |
| `ConsistencyChecker` | **YES** | `:415` |
| `PortfolioCacheUpdater` | **YES** | `:466` |
| `ExchangeReconciliationService` | **NO** | scorecard: `NOT_STARTED`, "no leader election; no exchange client wired" |
| `MarketplaceExpiryWorker` / `expiry_sweep` | **NO** | zero matches in `main.py` |

**The distinction the registers blur, restated because conflating it misreports both:**
`ReconciliationScheduler` (running, `:456`) and `ExchangeReconciliationService` (not started) are
**different components**. Both statements are true.

Expiry-worker impact, bounded: `library_subscriptions.period_expiry` is the authority on entitlement
and the resolver compares `now` against it directly, so a dead sweep **cannot extend access** — it
only lags the stored status label. `main.py` has three contending workstreams per A25 A-5 and no
named coordinator, so no edit was considered.

---

## 9. Workstream G — CI evidence at HEAD · **the most consequential finding**

`origin/main == HEAD == ecd23a9e`, so the 3,723-file commit is **pushed**. Runs at that SHA:

| Workflow | Conclusion | Run |
|---|---|---|
| `02 Build` | **success** | 38129699471 |
| `06 Frontend Deploy` | **success** | 38129699497 |
| `07 Desktop Build (native, unsigned)` | **success** | 38129699516 |
| **`05 Security`** | **FAILURE** | 38129699454, retried 38132697285 — failed again |
| **`03 Deploy`** | **FAILURE** | 38130273012 |
| **`01 PR Check`** | **DID NOT RUN AT THIS HEAD** | latest is 38061973739 at `f5b99e57` — a different SHA, and it **failed** |

Failure causes, read from the job/step data:

```
05 Security  38132697285
  Trivy Container Vulnerability Scanner        success
  Bandit SAST & Dependency Security Scan       FAILURE  -> step "Run Bandit Scan"
  Trivy IaC Misconfiguration & Secret Scan     FAILURE  -> step "Trivy IaC scan - terraform/ (blocking, CRITICAL)"

03 Deploy    38130273012
  Pre-Deployment Validation Gate               FAILURE  -> step "Require the security scan to have
                                                            passed for this commit"
  ECS Fargate Deploy & Self-Healing Diagnostic SKIPPED
```

**The chain, stated plainly.** Bandit and a blocking CRITICAL Trivy IaC finding fail `05 Security`;
`03 Deploy`'s gate correctly refuses to proceed without a passing security scan; the ECS deploy is
skipped. **The gates behaved correctly — they refused.** Nothing was deployed.

**And the test lane never executed.** `01 PR Check` is PR-triggered; a direct push to `main` did not
trigger it. So at this HEAD there is **no evidence from the ~11,000-test suite, the Redis-backed
real-Redis gate, the PostgreSQL `database-tests` lane, or the `static-audit`/flake8 gate** — the same
flake8 gate any D7-1 patch would have to clear.

**Nothing here was dispatched.** I read existing run records only; no workflow was triggered, no
re-run requested. Note the repository carries `tests/test_bandit_sast_gate_is_green.py`, so the
Bandit failure is itself testable locally once a grant exists.

---

## 10. Tests executed

**None.** No authorized implementation existed to validate, and the brief forbids rerunning suites
to reconfirm an unchanged state. **No pass, fail, skip or xfail is claimed.** `test_r6_d` remains
`XFAIL(strict)`, untouched, in a file this workstream does not own.

| Lane | Status | Reason |
|---|---|---|
| Local unit | **not run** | nothing authorized to verify |
| Real Redis | **UNEXECUTED** | no local server; CI lane did not trigger (§6, §9) |
| PostgreSQL / D6-C | **UNEXECUTED** | no connection; no grant |
| Exchange sandbox | **UNEXECUTED** | gate unset, no credentials |
| Production | **UNEXECUTED** | no access, none sought |

---

## 11. A23 prerequisite matrix

Recomputed from the committed form this phase: **0 ticked / 12 unticked**, unmodified since
`ecd23a9e`.

| Prerequisite | Status |
|---|---|
| `E-1` ownership + scope | **UNRESOLVED — no grantor; and per §3, no register row to confer the role** |
| `D-A23C-1` production | **UNRESOLVED** |
| `D-A23C-7` test file | **UNRESOLVED** — path ABSENT |
| `D-A23C-3` proxy A/B + co-decision | **UNRESOLVED** |
| `D-A23C-8a` core-cache ack | **NOT GRANTED** |
| `D-A23C-9` AUTH/CONN/TENANT ack | **UNRESOLVED — open in register** |
| `E-5` / `D-A23C-12a/b/c` | **UNRESOLVED** |
| `E-6` real-Redis | **UNEXECUTED — infrastructure committed, lane not triggered (§6, §9)** |

**Granted 0 · Refused 0 · Unresolved 8.** Discharged earlier, not reopened: `D-A23C-8b` VOID,
`D-A23C-10` RESOLVED, `D-A23C-11` CLOSED.

`D-A23C-3`'s behavioural implication is unchanged by the commit: unbinding still makes
`RedisManagerCompatProxy.__getattr__` (`redis_manager.py:581-591`) raise `AttributeError` at the 11
`global_safety.py` sites, because that is a property of the code. What *did* change is that
`global_safety.py` is now clean and committed, so the **coordination** hazard is materially reduced
(A23E12 §2.2).

`D-A23C-8a` and `D-A23C-9` are **acknowledgements only** and can be resolved without touching shared
code — they are the two cheapest of the eight.

---

## 12. Files changed · untouched · authorizations relied upon

**Changed:** this report, plus `staging/p12e13_ci.json` (a CI query artifact). **No production file,
no test, no migration, no dependency, no workflow, no configuration, no governance record.**

**Deliberately untouched, with reason:**

| File | Why |
|---|---|
| `backend_app/routers/orders.py`, `routers/market.py` | D7-1 grant ABSENT |
| `backend_app/backend/exchange_executor.py` | W-6 grant ABSENT |
| `backend_app/backend/redis_manager.py` | A23 `D-A23C-1` UNRESOLVED |
| `tests/test_a23_redis_pool_lifecycle.py` | `D-A23C-7` UNRESOLVED — still ABSENT |
| `backend_app/main.py` | three contending workstreams, no coordinator |
| `pytest.ini`, `tests/conftest.py`, migrations, RLS policies, DB roles | out of every current scope |
| `staging/PHASE11B_A23E11_…md`, `tests/test_drift_classification.py` | **another session's in-flight work — preserved** |
| `PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md:802` | stale, but editing authority unidentified |

**Authorizations relied upon: none.** No grant was exercised, because none was located.

---

## 13. Prioritized next actions

| # | Workstream | Smallest concrete decision |
|---|---|---|
| **1** | **CI / launch** | Decide whether `main` may carry an untested, security-failing commit. Two sub-actions, neither needing a code grant: **(a)** trigger `01 PR Check` at `ecd23a9e` so the test and Redis lanes produce evidence; **(b)** triage the Bandit and Trivy-IaC-CRITICAL findings blocking `03 Deploy` |
| **2** | **D7-1** | Router owner grants the three-line deletion + the §4.3 bind test. **Now the lowest-risk item on the board** — §4.1 proves no behaviour is lost; all files clean |
| **3** | **TENANT-01** | DB-role decision (GUC per request vs non-owner role). Highest risk, highest blast radius, production-track only |
| **4** | **A23 P1** | Two steps per §3: register row establishing the role, then the grant in that role |
| **5** | **W-6** | Authorize 7 deterministic default-lane cases; the default lane currently asserts nothing about classification |
| **6** | **Workers** | `main.py` coordinator named, then wire the expiry worker |
| **7** | **QUOTA-01** | Follows action 1(a) — no new provisioning needed |
| **8** | **Ledger L802** | Identify the maintainer |

---

## 14. Preservation audit

| Check | Result |
|---|---|
| HEAD / tree / `origin/main` | `ecd23a9e` / `4b45a763` / `ecd23a9e` — **unchanged entry to exit** |
| Staged / unmerged | 0 / 0 — unchanged |
| Unstaged | 2 — unchanged, **neither mine** |
| Untracked | 25 → 27 (this report + CI artifact) |
| Six target blobs | **all identical to §2** |
| `tests/test_a23_redis_pool_lifecycle.py` | still **ABSENT** |
| Concurrent work overwritten | **none** — A23E14 and the two dirty files read only |
| Destructive git operation | **none** — no reset, checkout, stash, clean, branch switch, index mutation |
| Staged / committed / pushed / CI dispatched | **none** |
| Dependency / schema / migration / workflow / deploy | **none** |
| Redis / DB / exchange / credential / production | **none** |

---

## 15. Status

| Item | State |
|---|---|
| **`01 PR Check` at HEAD** | **NEVER RAN** — no test, Redis, PostgreSQL or flake8 evidence at this SHA |
| **`05 Security` at HEAD** | **FAILING** — Bandit + Trivy IaC CRITICAL |
| **`03 Deploy` at HEAD** | **FAILED at its gate; ECS deploy SKIPPED** — nothing deployed |
| D7-1 | **CONFIRMED + de-risked** (§4.1); unfixed, grant ABSENT |
| W-6 | **CONFIRMED; retry-suppression ruled out repository-wide**; unfixed, grant ABSENT |
| QUOTA-01 / `E-6` | **OPEN** — infrastructure committed, lane untriggered; real-Redis **UNEXECUTED** |
| TENANT-01 | **OPEN, static-only**; **NOT certified** |
| Workers | scheduler + watchdog running; **expiry worker and ExchangeReconciliationService not started** |
| A23 P1 | **NOT STARTED — 0 of 8** |
| Tests executed | **0** |
| **Live trading** | **OFF / NOT CERTIFIED** |

No approval was inferred from this prompt, from the `ecd23a9e` commit or its push, from the W1D-12
grant for another workstream, from a role label, from commit authorship, from file cleanliness, from
CI results, or from silence.
