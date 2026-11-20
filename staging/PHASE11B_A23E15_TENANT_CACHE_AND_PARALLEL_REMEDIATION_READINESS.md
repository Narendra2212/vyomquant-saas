# Phase 11B-A23E15 — Tenant Isolation, Silent-Failure Remediation Readiness, and Parallel Critical-Path Execution

**Destination verified ABSENT immediately before writing.** Continues
`staging/PHASE11B_A23E14_REGISTER_AUTHORIZATION_AND_BLOCKER_REDUCTION.md`.

**0 production files modified · 0 test files modified · 0 migrations · 0 registers · 0 decision
forms · 0 tests executed · 0 approvals created · 1 artifact written (this file).**

**LIVE TRADING: OFF / NOT CERTIFIED.**

---

## 1. Entry / exit Git state and preservation

| Measurement | Entry | Exit |
|---|---|---|
| branch | `main` | `main` |
| `HEAD` | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **unchanged** |
| parent (`HEAD~1`) | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | unchanged |
| `origin/main` divergence | 0 ahead / 0 behind | 0 / 0 |
| staged | 0 | **0** |
| unmerged | 0 | **0** |
| worktrees | 1 | 1 |
| porcelain (collapsed) | 33 | **39** |

`HEAD` **does** still equal `ecd23a9e…` as the brief specified — no new commit occurred during this
pass, and no older baseline was restored.

**Porcelain 33 → 39 is entirely concurrent-session activity plus this report.** The repository is
being written by several sessions throughout; entries appeared under `staging/` while I worked
(`p11b_a23e16_baseline.py`, `p12_w6_lines.py/.txt` and others). I touched none of them. All my
operations except the final write were reads.

| Protected file | Blob | State |
|---|---|---|
| `backend_app/backend/redis_manager.py` | `d7b0e811b615ee13ba089b5cbe9d790161176de2` | **clean, unchanged** |
| `tests/test_a23_redis_pool_lifecycle.py` | — | **ABSENT** |
| `backend_app/core/global_safety.py` | `4b2a1fc52748f3e90e86083b43f9e8c232629c3d` | unchanged |
| `tests/test_redis_debounce.py` | `5a8db7a8afbd57a0dfde35c303b49fb233c3fffc` | unchanged |

### 1.1 Security check on a credential-shaped file

`exchange_keys.json` sits at the repository root and is open in the editor. I did **not** read it.
Verified instead:

- `git ls-files --error-unmatch exchange_keys.json` → **exit 1** (not tracked)
- `git check-ignore exchange_keys.json` → **exit 0** (ignored)

**It was not committed by `ecd23a9e`, and it is correctly ignored.** No secret value was read or
echoed. Noted only so the ignore rule is not removed later by accident.

---

## 2. Evidence classes

Every finding below is tagged. **No production system was contacted.**

| Class | Meaning | Used here |
|---|---|---|
| `SRC` | Read directly from current working-tree source | yes — all primary findings |
| `LOCAL` | A test or command I executed | **none this pass** (§11) |
| `CI` | Observed CI run evidence | **none** — no dispatch, no run inspected for this HEAD |
| `PROD` | Live production/database observation | **none** — forbidden and not attempted |
| `PRIOR` | Another session's recorded finding, cited not re-verified | labelled inline |

---

## 3. Workstream A — TENANT-01 / D6-C: tenant isolation

Highest-priority workstream. All findings `SRC` unless marked.

### 3.1 RLS is enabled on nine tables; FORCE is never executed

`SRC` `backend_app/alembic/versions/implement_rls_policies_20260817.py` creates a
`*_tenant_isolation_policy` on nine tables, each comparing against the GUC:

| Table | Predicate |
|---|---|
| `orders`, `positions`, `fills`, `dag_tasks`, `idempotency_keys`, `transaction_records`, `transaction_checkpoints` | `tenant_id = current_setting('app.current_tenant_id', true)` |
| `execution_records`, `reconciliation_mismatches` | `tenant_id::text = current_setting('app.current_tenant_id', true)` |

All use `FOR ALL` with both `USING` and `WITH CHECK`.

**Refinement to A23E14, which claimed zero FORCE matches.** A scoped search of
`migrations\*.sql`, `backend_app\migrations\*.sql`, `backend_app\alembic\versions\*.py` returns:

- `ENABLE ROW LEVEL SECURITY` → **107** matches
- `FORCE ROW LEVEL SECURITY` → **1** match

The single match is **prose inside a SQL comment** —
`backend_app/migrations/026_fee_records_row_level_security.sql:75`, a line beginning `--` that
*discusses* FORCE. **There is no executable `FORCE ROW LEVEL SECURITY` statement anywhere.**
A23E14's conclusion stands; this is the exact distinction the brief asked for — comment, not
statement. (My A23E14 repo-wide grep was truncated at "too many matches", so the scoped search is
the reliable instrument.)

### 3.2 The application connects as the table owner

`SRC` `backend_app/core/database_pool.py`:

- `:138` `DATABASE_URL = os.getenv("DATABASE_URL")`; `:141` raises in paper/live if unset; `:142`
  SQLite fallback otherwise.
- `:154` Supabase fallback DSN is `postgresql://postgres:postgres@localhost:5432/postgres` — role
  **`postgres`**, which owns tables created by the migrations.
- `:159-178` rewrites `:5432` → `:6543` for Supabase hosts, injects the project ref into the
  username for Supavisor, and appends `pgbouncer=true&pool_mode=transaction`.

**Consequence:** a table owner is exempt from its own RLS policies unless `FORCE` is set. With
`FORCE` absent (§3.1) and the connection role owning the tables, **all nine policies are inert on
the ORM path.** Isolation there rests on application `WHERE` clauses alone. This matches `PRIOR`
`PHASE11_READINESS_EVIDENCE.json` SEC-03 and `AUTH_RLS_AUDIT.md` §11.1, which additionally report
live `relrowsecurity = true, relforcerowsecurity = false` — a `PROD` observation I did **not**
re-verify.

### 3.3 Tenant context is never set on any request path

`SRC` measured, not inferred:

- `backend_app/core/dependencies.py` → **0** matches for `current_tenant_id` or `tenant_db_context`.
- Repository-wide, exactly **two** places set the GUC, both off the request path:

| Setter | Form |
|---|---|
| `backend/transactional_execution_manager.py:225` | `text("SET LOCAL app.current_tenant_id = :tenant_id")` |
| `backend/tenant_rls_validator.py:438` | same, inside `ServiceRoleGuard`, with `RESET app.current_tenant_id` on exit (`:446`) |

- `database_pool.py` `get_db()` (`:386`), `get_db_context()` (`:396`), and `get_session()` (`:588`,
  `:598`, `:610`) yield sessions and set **nothing**.

This independently confirms `tenant_db_context.py`'s own docstring: *"Nothing on a request path ever
sets that setting … `get_db`/`get_db_context` set nothing, and no middleware sets it either."*

### 3.4 The remediation module already exists and is correct — it is simply unwired

`SRC` `backend_app/core/tenant_db_context.py` is present and well-formed:

- `TENANT_SETTING_NAME = "app.current_tenant_id"` (`:61`), with a comment warning that changing it
  without reissuing every policy **silently disables tenant filtering**.
- `tenant_db_dependency()` yields a FastAPI dependency; the body does
  `with session.begin(): apply_tenant_context(session, tenant_id)` then `yield`, with
  `session.close()` in `finally`.
- Tenant comes from the **verified JWT** (`UUID(user["id"])`), never from body or query — matching
  what `routers/orders.py` and `routers/portfolio.py` already do.

**A latent defect it documents, which matters independently.** Its docstring (`:22-26`) records that
`SET` takes no bind parameters in PostgreSQL, so
`text("SET LOCAL app.current_tenant_id = :tenant_id")` works **only** because psycopg2 interpolates
client-side; on a server-binding driver such as asyncpg the same statement **fails**. Both existing
setters (§3.3) use exactly that fragile form. The module therefore prescribes
`set_config(name, value, is_local=true)` instead. Any async migration would silently break the only
two places tenant context is set today.

### 3.5 Pooler interaction — why `is_local` is load-bearing, not stylistic

`SRC` Combining §3.2 and §3.4: the DSN forces `pool_mode=transaction`. Under transaction pooling a
server connection returns to the pool at transaction end, so:

- **Transaction-scoped** context (`SET LOCAL`, or `set_config(..., true)`) ends with the transaction
  and is pooler-safe. This is what `tenant_db_context.py` does.
- **Session-scoped** context (plain `SET`, or `set_config(..., false)`) would **persist on the
  pooled server connection and leak into the next tenant's transaction.**

So the module's `is_local=true` choice is a correctness requirement under this pooler, and
`tenant_db_dependency`'s explicit `with session.begin()` is what guarantees a transaction exists to
scope it to. A remediation that wired the GUC without an explicit transaction would be worse than
the status quo.

### 3.6 Minimal remediation sequence — design only, NOT implemented

Ordered so that no step can silently weaken isolation:

| # | Step | Why this order | Authorization needed |
|---|---|---|---|
| 1 | Convert the two existing setters to `set_config(..., is_local=true)` | Removes the psycopg2-only dependency before anything else depends on it (§3.4) | owner of `transactional_execution_manager.py` + `tenant_rls_validator.py` |
| 2 | Wire `tenant_db_dependency()` into the request path for the nine-table routes | Context must exist **before** enforcement is switched on, or every query breaks | owner of `dependencies.py` + each router |
| 3 | Add deterministic cross-tenant regression tests against a local PostgreSQL | Proves enforcement *and* non-regression before role change | test owner |
| 4 | Connect the ORM as a **non-owner** role (or set `FORCE` per table) | Either alone closes the owner exemption; this is the actual fix | DB owner + platform |
| 5 | Issue `FORCE ROW LEVEL SECURITY` behind a feature flag, staging first, with verified rollback | Last, because steps 1–4 must hold or every tenant query fails closed | DB owner + coordinator |

**Required test matrix** (none written — no grant): cross-tenant read denied; cross-tenant write
denied by `WITH CHECK`; **missing context denies rather than permits** (`current_setting(..., true)`
returns NULL when unset, so a NULL-vs-`tenant_id` comparison yields NULL ⇒ no rows — this must be
asserted, not assumed); context does not survive across pooled transactions (§3.5); `::text` cast
tables behave identically to the plain ones; `ServiceRoleGuard` resets on both normal and exception
exit; background workers either set context or are explicitly exempt.

**Separately scoped, not folded in:** `api_key_vault` reads with a service-role credential and
`routers/billing.py` reads `payment_methods` as owner — both bypass RLS by a *different* mechanism
than the ORM owner path and need their own decisions. `PRIOR` D6-C's confirming query remains
**NOT GRANTED** (D8 Track B); D8-4 records that JWT tenant-claim divergence is unmeasurable from
application tables and needs the auth/platform owner.

**Status: TENANT-01 remains OPEN. Tenant isolation is NOT certified.** §3.1–3.5 are static source
facts; production enforcement is unverified here.

---

## 4. Workstream B — D7-2: portfolio-cache failure and false-green health

`SRC` **The failure path is more deterministic than previously recorded, and the precise catch point
differs from D8-3's description.**

`backend_app/backend/portfolio_cache_updater.py`:

```
149-151   from backend_app.backend.connection_engine import get_or_create_exchange
          from backend_app.backend.key_manager import KeyVault      ← line 151
153-155   # Build cache keys  (balance_key, position_key)
157       try:
159-160       vault = KeyVault();  keys = vault.load_decrypted_keys(user_id, exchange_id)
224       except Exception as e:   # "Don't re-raise - background job should continue"
```

- `Test-Path backend_app\backend\key_manager.py` → **False**. The module does not exist.
- **The import at `:151` sits OUTSIDE the `try:` at `:157`.** So the `ImportError` is *not* caught by
  this function's own handler at `:224`. That handler can never run for this failure.
- It propagates to the per-user wrapper at `:132` —
  `except Exception as e: logger.error(f"❌ Failed to update portfolio for {user_id}: {e}")` — inside
  the per-user loop. The outer loop handler at `:119` catches anything else and `sleep(1)`s.

**Therefore:** the failure is **deterministic, not per-user-conditional**. `ImportError` fires on
every user, every cycle, for every exchange. No portfolio is ever cached. The loop logs and
continues forever. Nothing marks the worker degraded — which is why `/health/services` can report
`ACTIVE` (`PRIOR` D8-3): the worker *is* running its loop; it simply cannot do its job. The
false-green signal is the security-relevant part, because it suppresses the operator signal that
would otherwise surface a total cache outage.

`SRC` **Supported vault interfaces** (from `order_watchdog.py:1084-1088`'s inventory and
`api_key_vault.py`): `APIKeyVault` exposes `store_exchange_keys`, `load_decrypted_keys`,
`delete_exchange_keys`, `list_connected_exchanges`, `get_user_tier`, `rotate_key`. The correct
import is `backend_app.backend.api_key_vault`. Note the updater's own call at `:160` is
`load_decrypted_keys(user_id, exchange_id)` — **already signature-correct** (contrast §5), so only
the import and class name are wrong.

**Proposed test matrix** (not written): dependency-initialization failure surfaces as *unhealthy*,
not `ACTIVE`; a successful refresh populates both `portfolio:{u}:{e}:balance` and `:positions`; one
user's venue error does not stop other users; absent/stale cache is distinguishable from fresh;
recovery after a transient failure; and an import-time guard that fails fast rather than per-cycle.

**Authorization: ABSENT** (`PRIOR` D8 §445-449 lists D7-2 **NOT GRANTED**; owner
*portfolio-cache + credential*). **No file touched.** Per the brief I did **not** create a
`key_manager` shim: pointing the import at `api_key_vault` changes which security contract the worker
runs under, and that is the owner's decision, not a mechanical rename.

---

## 5. Workstream C — D7-1: vault signature compatibility

`SRC` Signature, `backend_app/backend/api_key_vault.py:177`:

```python
def load_decrypted_keys(self, user_id: str, exchange_id: str) -> dict:
```

Two positional parameters; **no `access_token`**. Verified caller map:

| Call site | Form | Verdict |
|---|---|---|
| `routers/orders.py:789-793` | `user["id"], exchange_id, access_token=…` | **BROKEN**; call not inside a `try` |
| `routers/orders.py:1095-1099` | same | **BROKEN**; inside `try` at `:1094` |
| `routers/market.py:50-54` | same | **BROKEN**; inside `try` at `:49` |
| `routers/exchange.py:993-996` | `user["id"], body.exchange_id` | **correct** |
| `api_ws/ws_routes.py:814` | `(user_id, exchange_id)` | correct |
| `backend/master_executor.py:252` | `(self.user_id, self.exchange_id)` | correct |
| `backend/portfolio_cache_updater.py:160` | `(user_id, exchange_id)` | correct (its defect is the import, §4) |

Each broken site raises `TypeError: load_decrypted_keys() got an unexpected keyword argument
'access_token'`. The count is firmly **three**; `exchange.py` needs no change.

`PRIOR` D8-5 explains why tests never caught it: `tests/test_market_none_keys_fix.py:13`'s double
declares `access_token=None`, so the double matches the broken call sites while the real class does
not.

**Patch proposal** (not applied): delete the three `access_token=` lines only. Per the brief I
checked whether that would *conceal* an auth or tenant problem — it would not: the vault derives
nothing from an access token, and both broken sites already pass `user["id"]`, which is the verified
JWT subject and the same tenant identity `tenant_db_context` would use. The parameter is inert at
the callee, so removing it restores binding without changing the authorization model. **Regression
test** must bind against the real class via `inspect.signature(...).bind(...)` rather than a
permissive mock, plus coverage for market-data fallback, open orders, trade history, and legitimate
public market data.

**Authorization: ABSENT** (`PRIOR` D8 Track C, D10 §5, D11 §4 — all "NOT GRANTED"/"ABSENT").
`PRIOR` D11 §5.3: the blocking static check is **flake8** (`static-audit`); `black`/`isort` run
`continue-on-error: true` and have never passed. **No file touched.**

---

## 6. Workstream D — W-6: exchange error classification

### 6.1 The ladder, reconfirmed

`SRC` `backend_app/backend/exchange_executor.py:_handle_ccxt_error` (`:1224-1257`), ordered
substring tests on the lowercased redacted message:

```
"insufficient" | "balance"        → InsufficientFundsError   (INSUFFICIENT_FUNDS, retryable=False)
"rate limit" | "too many requests"→ RateLimitError           (RATE_LIMIT,        retryable=True)
"symbol" | "market"               → InvalidSymbolError       (INVALID_SYMBOL,    retryable=False)
"network" | "timeout" | "connection" → NetworkError          (NETWORK_ERROR,     retryable=True)
"authentication" | "apikey" | "key"  → AuthenticationError   (AUTH_ERROR,        retryable=False)
else ExchangeError(retryable = any of "timeout","network","temporarily","busy")
```

Both reported risks hold. The `symbol`/`market` rung **precedes** `timeout`/`network`, so a timeout
naming a symbol returns `InvalidSymbolError`. And `"market"` matches a URL path literally, so a ccxt
error quoting `/v5/market/tickers` misclassifies regardless of cause — a broader trigger than
"a message containing a symbol". `"key"` matches bare substrings (`keyword`, `keyspace`, `monkey`),
though it is shadowed whenever an earlier rung fires.

### 6.2 Control-flow analysis — the severity is lower than registered, and I'm correcting it

The brief asked whether `retryable` drives control flow or only logging. Measured `SRC`:

| Probe | Result |
|---|---|
| `except InvalidSymbolError\|NetworkError\|AuthenticationError\|RateLimitError` across `backend_app/**/*.py` | **0 matches** |
| `error_code ==` comparisons | **0**. The only other `INVALID_SYMBOL` literals are unrelated request-validation strings at `routers/orders.py:134,151` |
| Reads of `.retryable` | **one**, a log f-string at `:1687` (`f"retryable={error.retryable}"`) |
| Independent retry logic | `backend/replay_safe_transaction_guard.py:322-344` `_is_retryable_error()` re-derives retryability from `str(error).lower()` substrings and **never consults `.retryable`** |

**So today the misclassification affects the error label and `error_code` surfaced to operators and
clients — not retry control flow.** Nothing branches on the class or the code. The register carries
W-6 as **P0** ("timeout relabelled as a determinate rejection"); on this evidence the *active* harm
is misleading diagnostics, while the *latent* harm is real and sharp: the first consumer to branch
on `except NetworkError` or `error_code` inherits a silently wrong taxonomy. I am reporting this as
a severity observation for the execution-state owner, **not** re-grading the register myself.

### 6.3 Deterministic test plan (default lane, no credentials)

Six cases, all constructible from local strings — `_handle_ccxt_error` takes an `Exception` and needs
no venue: timeout containing `symbol=BTCUSDT` ⇒ must be `NetworkError`; timeout containing
`/v5/market/tickers` ⇒ `NetworkError`; plain `RequestTimeout` ⇒ `NetworkError`; genuine
`BadSymbol: ... does not have market symbol` ⇒ `InvalidSymbolError`; messages containing `keyword` /
`keyspace` / `monkey` ⇒ **not** `AuthenticationError`; genuine `AuthenticationError` and
`RateLimitExceeded` ⇒ unchanged.

**Smallest robust correction** (not applied): move the `network`/`timeout`/`connection` rung
**above** `symbol`/`market`, and tighten `"key"` to word-boundary or specific tokens
(`"api key"`, `"apikey"`, `"invalid key"`, `"signature"`). No genuine handling weakens, because no
branch consumes the taxonomy today (§6.2).

**Authorization: ABSENT** — `PRIOR` D7 §405 lists W-6 P0, owner *execution-state + coordinator*,
open; D11 measured default-lane coverage at **zero**. **No source or test changed.**

---

## 7. Workstream E — workers, lifecycle, deployment

`SRC` Reconciled by direct inspection rather than by trusting any single report.

| Component | Referenced in `backend_app/main.py`? | Evidence |
|---|---|---|
| `ExchangeReconciliationService` | **NO — 0 matches** | repository search of `main.py` |
| `MarketplaceExpiryWorker` | **NO — 0 matches** | same |

Neither name appears in the application entrypoint, so neither is constructed or started. A class
definition is not a running worker, and here there is not even a reference. This independently
confirms `PRIOR` `PHASE11A_READINESS_SCORECARD.json`:
`exchange_reconciliation_service_WIRED_AND_RUNNING: NOT_STARTED` — *"main.py lifespan does not start
ExchangeReconciliationService; no leader election; no exchange client wired"*.

`SRC` **Watchdog defect, documented in its own source.** `backend/order_watchdog.py:1084-1088`
records that `app_state.vault.get_exchange_client` **does not exist anywhere**; `APIKeyVault` exposes
the six methods listed in §4. The call raises `AttributeError`, which the surrounding `except`
converts to `None` — another silent degradation, same shape as D7-2.

**Not resolved this pass, and I will not guess:** `ReconciliationScheduler` vs
`ExchangeReconciliationService` (whether a scheduler runs while the service is absent),
`ConsistencyChecker`, ECS/deployment manifests for dedicated worker services, leader election, and
whether expiry-worker absence affects access control or only stored status. These need the
deployment manifests read alongside the worker sources; flagged as the remaining Workstream-E gap
rather than inferred.

**Authorization: ABSENT** for W-3 / W-4 / W-5 (`PRIOR` D8 §445-449, all NOT GRANTED). Shared startup
and deployment files untouched.

---

## 8. Workstream F — QUOTA-01 and real-Redis disposition

**Not re-run, deliberately.** `PRIOR` D11 records QUOTA-01 as **TESTED: 50 passed / 5 skipped**, with
**real-Redis UNEXECUTED**. Nothing material changed: the quota suites, fixtures and
`tests/real_redis_gate.py` are unmodified at this HEAD, and `PRIOR` A28B §214 notes the only
slot-guard call site is in `routers/exchange.py`, another workstream's file. Re-running an unchanged
suite would manufacture activity, which the brief forbids.

**Real-Redis dependency, stated exactly:** no local server is reachable, `REDIS_URL` is blanked
unconditionally by `tests/conftest.py:13`, `AERORA_TEST_REDIS_URL` is unset, and `D-A23C-6` / `E-6`
are ungranted. Starting a service or installing dependencies is outside this task. **The 5 skips are
not passes**, and mocks would not constitute real-Redis acceptance. No endpoint value or secret was
inspected or recorded.

---

## 9. Workstream G — A23 Redis lifecycle gate matrix

Recomputed from the committed form with the codepoint probe (ASCII `[ ]`/`[x]` patterns return 0/0
against U+2610 ballot boxes and must not be used):

```
U+2610 (empty)              → 12
U+2611/2612/2713/2714/2718  → 0
_{6,} blank fields          → 6
```

| Gate | Decision | Status | Evidence |
|---|---|---|---|
| **E-1** | Owner + scope | **UNRESOLVED — no grantor** | §3.A, 3 blank fields; A10 `:244,367` unowned; A3 §3 has no Redis row |
| **D-A23C-1** | Implementation | **UNRESOLVED** | §3.B both boxes unchecked |
| **D-A23C-7** | Test file | **UNRESOLVED** | §3.C unchecked; path ABSENT on disk |
| **D-A23C-3** | Proxy Option A/B + co-decision | **UNRESOLVED** | §3.D unchecked, co-decision blank |
| **D-A23C-8a** | Core-cache acknowledgement | **UNRESOLVED** | §3.E unchecked |
| **D-A23C-9** | Exchange-handler acknowledgement | **UNRESOLVED — OPEN in register** | §3.G 2 ☐ + 2 blanks |
| **E-5 / D-A23C-12a/b/c** | Concurrency disposition | **UNRESOLVED** | §3.F all three unchecked |
| **E-6** | Real-Redis environment | **UNEXECUTED, no disposition** | §8 |

**Granted 0 · Refused 0 · Unresolved 8.** `PRIOR` A28B: *"No classifier grant authorizes any Redis
lifecycle edit"*, corroborated across five sessions.

**Proxy inventory, carried forward unchanged** (target byte-identical, so not re-derived): 38 call
sites over six in-scope modules, 34 guarded / 4 unguarded (`dag_tasks.py:591,595,599,603`, reached
from `list_tasks` at `:166`, which also lacks a handler). `core/rate_limiter.py` remains struck from
the impact list — it imports `core.cache`, not the backend proxy.

**New assessment the brief requested — would the narrow `dag_tasks.py` handler need an extra file
grant?** **Yes, under either option.** Option A's grant covers "accept and document" with no code
written, and Option B's covers a typed error in the authorized P1 file. Adding a handler to
`backend_app/routers/dag_tasks.py` touches a **third** file that neither option names, so it
requires its own grant from that router's owner. It is therefore a **follow-up**, not a variant of
either option, and must not be bundled into P1.

Role assignment, implementation grant, test grant and infrastructure approval remain **four separate
decisions**. No box ticked, no owner row added, no test file created, no Redis source touched.

---

## 10. Workstream H — CI, provisioning, and schema evidence

**`CI` evidence for this HEAD: NONE.** I did not dispatch CI, and I did not inspect a run for
`ecd23a9e`. So I cannot assert whether `provisioning-and-isolation` has executed on the current
commit, and I decline to infer it from the workflow file's existence.

What is established without CI access: `PRIOR` records distinguish earlier narrow PostgreSQL test
success from full-pipeline success and that distinction remains unclosed. `SRC` `01-pr-check.yml`
remains `pull_request`-triggered with no `push` or `workflow_dispatch` (`PRIOR` A11/A2), so a commit
pushed directly to `main` — which `ecd23a9e` was — **does not trigger it**. That is the structural
reason CI evidence for this HEAD is unlikely to exist, and it is the precise question for the CI
owner.

**Not done, and not simulated:** provisioning snapshot freshness, schema-drift execution,
production-schema checks, and whether the CI environment exercises a non-owner database role — the
last being the one that would actually test §3's remediation. No cloud resource provisioned, no
production access, no credential exposed.

---

## 11. Tests and commands executed

**No test was executed this pass.** Every authorized-work gate is closed (§5, §6, §4, §7), §8 would
duplicate completed work, and §3's tests require a change that is unauthorized. Nothing is reported
as passing that did not run.

| Lane | Disposition |
|---|---|
| Tenant-isolation cross-tenant matrix | **NOT RUN** — depends on unauthorized remediation |
| D7-1 signature-binding regression | **NOT RUN** — D7-1 ABSENT |
| W-6 classification cases | **NOT RUN** — W-6 ABSENT |
| D7-2 portfolio-cache regressions | **NOT RUN** — D7-2 NOT GRANTED |
| 8 Redis lifecycle cases | **NOT RUN** — file absent, `D-A23C-7` unresolved |
| `tests/test_redis_debounce.py` | **NOT RUN.** `PRIOR` 9 passed / 1 xfailed. `test_r6_d` remains `XFAIL(strict)`, untouched |
| QUOTA-01 suites | **NOT RE-RUN** — `PRIOR` 50 passed / 5 skipped, real-Redis UNEXECUTED |
| flake8 `static-audit` | **NOT RUN** — no patch produced |

Commands run were read-only: `git rev-parse`, `git status`, `git diff --cached --name-only`,
`git worktree list`, `git hash-object`, `git ls-files`, `git check-ignore`, `Select-String`,
`Test-Path`, and file reads.

---

## 12. Files changed and deliberately untouched

**Changed: 1 — this report.** No authorization was required or claimed for writing a new staging
artifact at a verified-absent path.

**Deliberately untouched, with the reason:**

| File(s) | Reason |
|---|---|
| `backend_app/backend/redis_manager.py` | `D-A23C-1` unresolved |
| `tests/test_a23_redis_pool_lifecycle.py` | `D-A23C-7` unresolved — not created |
| `routers/orders.py`, `routers/market.py` | D7-1 ABSENT |
| `backend/portfolio_cache_updater.py` | D7-2 NOT GRANTED; no shim created |
| `backend/exchange_executor.py` | W-6 ABSENT |
| `core/dependencies.py`, `core/tenant_db_context.py`, `core/database_pool.py`, RLS migrations | TENANT-01 remediation unauthorized; high blast radius |
| `backend/transactional_execution_manager.py`, `backend/tenant_rls_validator.py` | owners not established |
| `main.py`, deployment manifests | shared startup; Workstream-E grants absent |
| `tests/test_redis_debounce.py` | A22's workstream |
| All registers, ledgers, A23E6 form | no authority to edit (A23E14 §4) |
| 38 concurrent `staging/` artifacts | other sessions' work |

No stage, commit, push, deploy, CI dispatch, reset, clean, branch switch or stash. No database,
exchange, credential or production access.

---

## 13. Ranked next actions — smallest decision per workstream

| # | Workstream | Smallest unblocking decision | Risk if left |
|---|---|---|---|
| 1 | **TENANT-01** | DB owner + platform decide §3.6 step 4: non-owner ORM role **or** per-table `FORCE`. Steps 1–3 are prerequisites and need only code owners | **Highest.** Nine policies inert; money-table isolation is `WHERE` clauses only |
| 2 | **D7-2** | Portfolio-cache + credential owner choose: repoint the import to `api_key_vault`, remove the worker, or gate it — **and** make `/health/services` report degraded | Total cache outage masked by a green health check |
| 3 | **D7-1** | Router owner grants the three-line deletion + `Signature.bind` test (Packet 6). Verified inert at the callee, so no auth model changes | `TypeError` on live market-data and order paths |
| 4 | **W-6** | Execution-state owner + coordinator authorize reordering the ladder and tightening `"key"`, plus the six §6.3 tests | Wrong `error_code` now; silently wrong taxonomy for the first consumer that branches on it |
| 5 | **Workers** | Service/deployment owner authorizes reading deployment manifests, then wiring reconciliation with leader election | No reconciliation runs; `ReconciliationScheduler` question unresolved |
| 6 | **A23 Redis P1** | Project decision-maker assigns the `Code owner` role (A23E14 §4.1), then completes A23E6 §3 | G1/G2/G3 + A23-01 unfixed; R6 open |
| 7 | **QUOTA-01 / E-6** | Authorize an isolated Redis service or a CI dispatch | Real-Redis acceptance unprovable; 5 skips stay skips |
| 8 | **CI / provisioning** | CI owner confirms whether `main`-push builds exist, given `01-pr-check.yml` is `pull_request`-only | No pipeline evidence for `ecd23a9e` |

---

## 14. Disposition

Eight workstreams investigated; **six defects verified directly against current source** this pass —
FORCE RLS present only as a SQL comment, nine inert policies under an owner-role connection, zero
tenant-context references in `dependencies.py`, the psycopg2-only `SET LOCAL` fragility, D7-2's
import sitting outside its own try block, and W-6's taxonomy consumed by nothing. Two prior claims
refined: A23E14's "zero FORCE matches" becomes "one comment, zero statements", and W-6's active
severity is labeling rather than retry suppression.

**No implementation occurred in any workstream — every one is authorization-blocked.** Nothing was
accepted, certified, or claimed complete.

**LIVE TRADING: OFF / NOT CERTIFIED.** No gate bearing on certification was passed, and tenant
isolation — the highest-risk item — remains unenforced at the database layer.

---

# Addendum A — CI evidence obtained, and a new highest-priority blocker

Added after the main body. §10 above recorded `CI` evidence as **NONE**; that gap is now **closed**
by read-only queries I ran myself. Read operations only — **no dispatch, no provisioning, no
deploy.** Nothing else in this report changes.

## A.1 The commit broke the security gate, and `main` is currently undeployable

`CI` **Independently verified** with `gh run list` (not carried from a sibling report):

```
gh run list --workflow=05-security.yml --branch main --limit 8 --json headSha,conclusion,event
```

| `headSha` | Event | Conclusion |
|---|---|---|
| `ecd23a9e` | schedule | **failure** |
| `ecd23a9e` | push | **failure** |
| `074b772a` | schedule ×5 | success |
| `074b772a` | push | success |

**`05 Security` passed six consecutive times on the parent and fails twice on the current HEAD.**

```
gh run list --workflow=03-deploy.yml --branch main --limit 4 --json headSha,conclusion,event
```

| `headSha` | Conclusion |
|---|---|
| `ecd23a9e` | **failure** |
| `074b772a` | success |

**`03 Deploy` refused on `ecd23a9e`.** That refusal is the gate working as designed — it must not be
bypassed.

Failing jobs in run `38132697285`:

| Job | Conclusion |
|---|---|
| Trivy Container Vulnerability Scanner | success |
| **Bandit SAST & Dependency Security Scan** | **failure** |
| **Trivy IaC Misconfiguration & Secret Scan** | **failure** |

The failed-step logs confirm the IaC job runs with `severity: CRITICAL` / `INPUT_SEVERITY: CRITICAL`
and both jobs exited 1. **The specific rule IDs are not in the step output** — Trivy wrote SARIF to
an artifact — so I do not attribute individual findings as verified.

## A.2 Source-grounded diagnosis of the IaC failure

`SRC` `git diff-tree` shows the commit's terraform changes: `M alb.tf`, **`A cloudtrail.tf`**,
`M providers.tf`, `M variables.tf`. The new file is the natural suspect, and it is otherwise
carefully written — `block_public_acls`/`block_public_policy`/`ignore_public_acls`/
`restrict_public_buckets` all `true`, versioning on, lifecycle rules, a bucket policy with
`DenyUnencryptedTransport`, and a trail with `is_multi_region_trail`,
`include_global_service_events` and `enable_log_file_validation` all enabled.

Two deliberate gaps remain, both of which Trivy's AWS policy set rates CRITICAL:

| Location | Finding |
|---|---|
| `terraform/cloudtrail.tf:73` | `sse_algorithm = "AES256"` — SSE-S3, **not** `aws:kms` |
| `terraform/cloudtrail.tf:194-217` | `aws_cloudtrail "main"` has **no `kms_key_id`** |

The file documents the choice itself (`:68-72`): SSE-KMS needs a key policy granting
`cloudtrail.amazonaws.com` `kms:GenerateDataKey*`, there is no customer-managed key in the
configuration yet, *"that gap is SC-9, tracked separately"*, and coupling the two "would couple two
unrelated fixes."

**So this reads as a documented engineering tradeoff that the CRITICAL-severity gate rejects.**
Stated as a **strong hypothesis, not a verified attribution** — Trivy's CloudTrail-CMK and
S3-CMK checks match these two lines exactly, but I could not read the rule IDs. **Confirmation
route:** download the SARIF artifact from run `38132697285`, or run Trivy locally against
`terraform/` (installing it needs separate authorization). The Bandit failure is a **separate**
finding set and was not diagnosed.

## A.3 The PostgreSQL lane has still never run — now confirmed by enumeration

§10 inferred this structurally; it is now confirmed. `01 PR Check` triggers on `pull_request` only,
and `ecd23a9e` reached `main` by **direct push**, so there is **no `01 PR Check` run with
`headSha = ecd23a9e`**. `provisioning-and-isolation` — carrying the drift gate, the PostgreSQL
lane, privilege hardening and the tenant-isolation steps — **has not executed once.**

Committing the toolchain made that lane *runnable*, not *run*. **A runnable lane is not PostgreSQL
certification**, and it means §3's tenant-isolation findings have no CI counter-evidence either way.

**I did not dispatch it, and that is deliberate:** `provisioning-and-isolation` invokes
`provision_database.py --apply --create-substrate`, which is an infrastructure-provisioning act, not
a read. A request to investigate CI is not authorization to provision. **Missing permission:**
authority to open a PR against `main` (or dispatch) for `ecd23a9e`, plus confirmation that
`PROVISION_DATABASE_URL` resolves to an isolated throwaway service.

## A.4 Correction to §1 — the porcelain count changed counting mode

§1 attributed porcelain 33 → 39 to concurrent activity plus this report. True, but incomplete, and
the deeper reason is worth recording: **before `ecd23a9e`, `staging/` held zero tracked files**, so
`git status --porcelain` collapsed the entire directory into a single `?? staging/` entry. **The
commit tracked `staging/`**, so every new report there now appears as its **own** entry.

So the counts before and after the commit are **not comparable** — the metric changed granularity,
on top of genuine concurrent writes. Any continuity check across `074b772a` → `ecd23a9e` must use
`--untracked-files=all` or per-path queries, not the collapsed total. This also retires the
`2e994018…` staged digest permanently (staged is 0).

## A.5 Revised ranked actions

| # | Blocker | Why it moves | Smallest next action |
|---|---|---|---|
| **1** | **`05 Security` fails on `main`; `03 Deploy` blocked** | **New, and now the top item.** `main` is undeployable; verified by my own queries | Triage the Trivy IaC CRITICALs (A.2 names the two candidate lines) and the separate Bandit set. **Do not bypass the gate.** Decide SC-9 (introduce a CMK) or record an explicit, reviewed exception |
| 2 | **TENANT-01** | Unchanged as highest *standing* risk | §3.6 step 4: non-owner ORM role or per-table FORCE |
| 3 | **PostgreSQL lane never ran** | Now confirmed, not inferred | Open a PR against `main` for `ecd23a9e`, or authorize a dispatch with isolation confirmed |
| 4 | D7-2 → 5 D7-1 → 6 W-6 → 7 workers → 8 A23 Redis P1 → 9 QUOTA-01/E-6 | unchanged | as §13 |

## A.6 Addendum preservation

Read-only throughout: `gh run list` ×3, `gh run view --json jobs`, `gh run view --log-failed`,
`git diff-tree`, `Select-String`, two file reads. **No dispatch, no provisioning, no deploy, no
credential read, no write outside this file.** `HEAD` remains `ecd23a9e…`, staged **0**,
`backend_app/backend/redis_manager.py` still `d7b0e811b615…`,
`tests/test_a23_redis_pool_lifecycle.py` still **ABSENT**.

**LIVE TRADING: OFF / NOT CERTIFIED** — and `main` additionally cannot deploy at all until A.5 item 1
is resolved.

---

# Addendum B — Correction: §A.2's diagnosis was wrong

**§A.2 attributed the Trivy IaC CRITICALs to the newly added `terraform/cloudtrail.tf`. That is
incorrect. `cloudtrail.tf` produced ZERO findings.** The hypothesis is withdrawn.

I verified the real distribution myself from the run log of `38132697285`, after a concurrent
Phase 14 report contradicted me:

```
gh run view 38132697285 --log-failed | Select-String "\.tf \("
→ alb.tf (terraform) · redis-only/main.tf (terraform) · security_groups.tf (terraform)
```

| File | Trivy result |
|---|---|
| `terraform/alb.tf` | `Tests: 1 (FAILURES: 1)` · **CRITICAL: 1** |
| `terraform/redis-only/main.tf` | `Tests: 1 (FAILURES: 1)` · **CRITICAL: 1** |
| `terraform/security_groups.tf` | `Tests: 3 (FAILURES: 3)` · **CRITICAL: 3** |
| **`terraform/cloudtrail.tf`** | **absent from the failure set — 0 findings** |

**Total 5 CRITICAL across 3 files, two distinct rules.** No secret findings.

## Why the hypothesis failed, so the reasoning error is not repeated

My inference chain was: the commit added `cloudtrail.tf` → the file uses `sse_algorithm = "AES256"`
with no `kms_key_id` → Trivy flags CloudTrail without a CMK → therefore it caused the new CRITICALs.

Each individual link was true **except the one that mattered**: Trivy rates the CloudTrail/S3
customer-managed-key checks **below CRITICAL**, and the gate runs with `severity: CRITICAL`
(`05-security.yml:120-128`), so those checks are filtered out and never fail the build. I reasoned
from "this file has a documented encryption gap" to "this file broke the gate" without checking the
gate's own severity filter against the rule's severity. **A real finding at the wrong severity is
not the failing finding.** I had the per-file evidence available in the same log I was already
reading and did not extract it before publishing the attribution.

`cloudtrail.tf`'s AES256-not-KMS gap is still real, still documented in-file as SC-9, and still
tracked separately — it is simply **not** what is blocking `main`.

## The actual failures (verified counts; rule attribution per concurrent Phase 14 triage)

| Rule | Count | Location | Character |
|---|---|---|---|
| `AWS-0054` ALB not using HTTPS | 1 | `alb.tf` | **False positive with an enforced compensating control.** The config redirects :80 → :443 when a custom domain exists, and a `lifecycle.precondition` *refuses the apply* in prod without one. Trivy cannot evaluate `dynamic` blocks or preconditions |
| `AWS-0104` unrestricted egress | 4 | `security_groups.tf` ×3, `redis-only/main.tf` ×1 | **Real but mostly conventional.** 3 are the standard pattern (tasks must reach ECR, Secrets Manager, venues). The `aws_security_group.redis` egress is **genuinely unnecessary** and is the one safe tightening |

Rule IDs and the ALB/egress characterisations are `PRIOR` from
`staging/PHASE14_MAIN_STABILIZATION_AND_CRITICAL_CLOSURE.md` §3.5-3.6; the per-file counts above are
`CI` verified by me. **Do not make the ALB redirect unconditional** to silence `AWS-0054` — that
breaks non-domain deploys. The honest options are a justified `trivy:ignore` citing the precondition,
or acceptance, both needing the infrastructure owner.

## The Bandit half — root cause found by Phase 14, and it is structural

`PRIOR` Phase 14 reproduced the Bandit failure **locally and exactly** (bandit 1.9.4, same command,
exit 1, 9 results, 9 MEDIUM / 0 HIGH) — executed rather than inferred evidence. The findings are
**9 × B608** in `backend_app/alembic/versions/`, all validating their interpolated identifier through
`_validate_table_name` (`^[a-zA-Z0-9_]{1,64}$`, raises, never sanitises) and binding user-controlled
values as parameters — so safe, but legitimately red because unsuppressed.

The structural cause is worth recording because it defeats the repo's own guard:
`tests/test_bandit_sast_gate_is_green.py` holds the premise two ways — one test asserts Bandit
silence on **a single module** (`transactional_execution_manager.py`), and an AST test checks
constraint **tree-wide** but is blind to `# nosec`. CI scans the **whole tree** (`-r backend_app`).
The 9 alembic sites pass the AST test, were never given the `# nosec B608` the four original sites
received, are scanned by CI, and are **invisible to the one-file Bandit test**. **So the suite is
green while the gate is red, and the suite cannot see it.**

Phase 14's A2 — broaden the Bandit test to run the gate's actual command — is the fix that matters;
A1 (adding the 9 `# nosec B608` with per-site reasons) only clears today's instance. Both need a
grant covering the 3 migration files and `tests/test_bandit_sast_gate_is_green.py`.

## Standing

The §A.5 ranking is unchanged — **`05 Security` failing and `03 Deploy` blocked remains item 1**,
and A.1's and A.3's verified CI findings stand. Only §A.2's *attribution* was wrong, and the fix is
narrower and cheaper than I implied: two Redis security-group egress rules are the single safe
tightening, and the remaining three CRITICALs plus the ALB rule are acceptance-and-documentation
decisions, not infrastructure changes.

**Nothing was changed in this addendum beyond this file.** No Terraform, workflow, migration or test
was touched; no CI dispatched; no Trivy installed. `HEAD` `ecd23a9e…`, staged **0**,
`backend_app/backend/redis_manager.py` `d7b0e811b615…`, `tests/test_a23_redis_pool_lifecycle.py`
**ABSENT**. **LIVE TRADING: OFF / NOT CERTIFIED**, and `main` still cannot deploy.
