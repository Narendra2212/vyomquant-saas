# Phase 13 — Critical Defect Closure, Tenant Isolation Safety, External Evidence Delivery

**0 production files modified · 0 test files modified · 0 migrations · 0 commits · 0 pushes ·
0 staged entries · 0 CI dispatched.** 9 new read-only evidence files under `staging/`.
Destination verified absent before writing.

**LIVE TRADING: OFF / NOT CERTIFIED.**

---

## 1. Executive summary — what materially changed since Phase 12

Phase 12 established its RLS and vault positions by reading source and counting statements in
migration text. This phase **measured** them, and three results change the picture.

| # | Finding | Status |
|---|---|---|
| **1** | **The FORCE-RLS danger is now PROVEN on a live server, not quoted.** A non-superuser owner role sees **2 rows under ENABLE, 0 rows under ENABLE+FORCE with the GUC unset, 1 row with the GUC set correctly**. Silent, no error. | **MEASURED** |
| **2** | **Migration text was never evidence of RLS state, and now it does not have to be.** Live catalog: all 11 tenant-scoped tables have `relrowsecurity=true`, `relforcerowsecurity=false`, `owner=postgres`, **20 policies total**. | **MEASURED** |
| **3** | **`app_state.vault` is a `MockEngine` by expectation, not by open question.** `state.py:67` imports the BARE name `api_key_vault`, which **fails to resolve** under both the repo-root path and `pytest.ini`'s path. Only `backend_app/backend/` on `sys.path` resolves it. | **MEASURED** |
| **4** | **W-6 is far worse than 3-in-8.** Expanded matrix: **10 of 20 asserted cases misclassify**, including two new defect classes — a genuine signature auth failure classified as generic, and a ccxt `RateLimitExceeded` classified as an invalid symbol. | **MEASURED** |
| **5** | **Three dead GUC writers, not two.** `tenant_rls_validator.py` exists (Phase 12's investigation could not confirm it) and contains a third `SET LOCAL` in `ServiceRoleGuard.__enter__:438`. It also has zero importers. | **OBSERVED** |
| **6** | **D6-C corrected query verified on fixtures with the real `uuid` types.** 7/7 scenarios exact, 0 mismatches; original fails SQLSTATE **42883**; returns counts only. | **TESTED** |
| **7** | **Two corrections to my own Phase 12 report** (§10.1) and **one withdrawn result from this phase** (§4.2). | — |

**Authorization: 0 grants for any Phase 13 implementation item.** Nothing was implemented.
Five concurrent sessions reached the same conclusion independently (§11).

State vocabulary: **OBSERVED** (read from source) · **MEASURED** (a probe produced a number
this pass) · **TESTED** (a test executed this pass) · **APPROVED** (an authoritative grant
exists) · **IMPLEMENTED** (code changed) · **DEPLOYED** (running in a deployed environment).

---

## 2. Entry and exit repository state

| Property | Phase 12 exit | Phase 13 entry | **Phase 13 exit** |
|---|---|---|---|
| `HEAD` | `ecd23a9e…` | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **unchanged** |
| Parent | — | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | unchanged |
| Branch · divergence | `main` · 0/0 | `main` · 0/0 | **`main` · 0/0** |
| Staged entries | 0 | 0 | **0** |
| **Files differing from `HEAD`** | **2** | **2** | **2 — the same two** |
| Unmerged · worktrees | 0 · 1 | 0 · 1 | 0 · 1 |
| Porcelain | 39 | 47 | **70** |

HEAD was re-confirmed as `ecd23a9e` at entry, as the preflight requires. The two files
differing from `HEAD` are `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` and
`tests/test_drift_classification.py` — **neither touched by this phase**, both left exactly as
found. `test_drift_classification.py` is the `AP-W1D-3` authorized workstream's output.

Porcelain rose 47 → 70 during the phase from concurrent sessions, including a
`staging/PHASE14_MAIN_STABILIZATION_AND_CRITICAL_CLOSURE.md` that appeared mid-phase (§11.2).
Porcelain is not a preservation invariant here; **"exactly 2 files differ from `HEAD`" is**, and
it held at entry and exit.

### 2.1 One piece of shared infrastructure changed state, and was not "fixed"

The provisioned PostgreSQL test cluster went **down mid-phase** (port 51805, connection
refused) after the RLS probes had already run against it. Its log shows
`database system was not properly shut down; automatic recovery in progress`, then
`database system was interrupted while in recovery`.

It was restarted explicitly — `bootstrap_pg.py start --data-dir pgdata_r1`; the script
correctly **refuses to guess** between `pgdata` and `pgdata_r1`, so the provisioned cluster
was named rather than defaulted to. Recovery completed cleanly (`redo done`,
`checkpoint complete`, `database system is ready to accept connections`).

It came back on **port 51815**, while shared `staging/pg_dsn.txt` still reads **51805**.
**That file was deliberately NOT edited.** Every PG-gated test and every concurrent session
reads it; redirecting it would redirect their runs too. The remaining probe took an explicit
`P13_PG_DSN` override instead. **The stale DSN file is reported, not repaired** — repairing it
is a shared-state decision, not mine to take unilaterally.

---

## 3. Workstream J — authorization intake (reported first, because it gates everything)

### 3.1 Grants on record for any Phase 13 item: 0

Re-read this pass: `PHASE11B_W1D12_CLASSIFIER_DECISION_RECORD.md:19-26`,
`PHASE11B_A23E16_SCOPE_CI_AND_BLOCKER_REDUCTION.md:77-83`, and the four artifacts new since
Phase 12.

The only grants anywhere in the programme remain the W1D classifier set:

| Decision | State | Scope |
|---|---|---|
| `AP-W1D-3` | **GRANTED** | the four-file scope in W1D-11 §5.1 — **"No other files are authorized."** |
| `D1`–`D4` | **GRANTED** | drift-classification behaviour, `scripts/verify_provisioning.py` |
| `D5` | **CONDITIONALLY GRANTED — condition NOT satisfied** | production-audit stakeholder sign-off, never given |

**Per the brief, that authority is not transferred.** `AP-W1D-3`'s own text forecloses it.
No Phase 13 target — vault signature, classifier ladder, RLS, Redis, worker lifecycle,
migrations — is inside its scope.

The ledger confirms the rest is ungranted. `A23E16:77-83` records all five ledger rows:

```
800  AP-W1D-1  DB owner (read access)      NOT GRANTED
801  AP-W1D-2  DB owner                    NOT GRANTED
802  AP-W1D-3  Code owner                  NOT GRANTED      <- ledger row, pre-grant
803  AP-W1D-4  DB owner + code owner       NOT GRANTED
804  AP-W1D-5  Coordinator + DB owner      NOT GRANTED
```

and notes `L802 reconciled to 'GRANTED (W1D-12)': False` — "Not chosen on the Code owner's
behalf." The A23E6 governance form is still **12 ☐ empty, 0 marked, 6 blank fields**.

### 3.2 Consolidated decision matrix

| ID | File + behaviour scope | Approver role | Depends on | Acceptance test | Security impact | Cost of deferral | Status |
|---|---|---|---|---|---|---|---|
| **D-13-1** | `routers/orders.py:792`, `:1098`, `routers/market.py:53` — delete `access_token=` arg (3 lines) | Code owner | none | §5.4 matrix; binds against real signature | **Removes a silent drop to unauthenticated market data** | `GET /api/orders/open` 500s; history shows empty; market data unauthenticated | **NOT GRANTED** |
| **D-13-2** | `exchange_executor.py:1246`/`:1249`/`:1252` — reorder + bound tokens | Code owner | **must precede W-3** | §6.6 matrix, 21 cases | Operator sent to rotate credentials on unrelated faults | Misdiagnosis; `retryable` wrong when W-3 lands | **NOT GRANTED** |
| **D-13-3** | Wire `tenant_db_context` into a session/transaction chokepoint | Code owner + DB owner | D-13-4 ordering | §7.6 steps 1-5 | **Prerequisite for any real tenant isolation** | RLS stays inert on the SQLAlchemy path | **NOT GRANTED** |
| **D-13-4** | A FORCE-RLS migration | DB owner + Coordinator | **D-13-3 complete and verified** | §7.6 steps 6-8 | **Would silently empty `orders`/`positions`/`fills` if taken first** | none — deferral is correct here | **NOT GRANTED, and must stay so until D-13-3 lands** |
| **D-13-5** | `portfolio_cache_updater.py:151` — adopt the supported vault interface | Code owner | D6-D evidence | §9.5 matrix | Credential path; must not add a key module | Cache permanently non-functional, not surfaced | **NOT GRANTED** |
| **D-13-6** | Run the corrected D6-C query, return 3 integers | **DB owner (production)** | none | n/a — evidence request | counts only, no identifiers (§8.3) | `tenants_without_keys` unknown; W-1b blocked | **EVIDENCE NOT SUPPLIED** |
| **D-13-7** | Report vault type + 2 boot strings per environment | **Platform/deployment owner** | none | n/a — evidence request | read-only, redacted | Live order path may `TypeError` | **EVIDENCE NOT SUPPLIED** |
| **D-13-8** | JWT `tenant_id` claim divergence count | **Auth/platform owner** | none | n/a — evidence request | counts only | claim divergence unmeasurable from tables | **EVIDENCE NOT SUPPLIED** |
| **D-13-9** | `UNKNOWN` exit policy, then W-5 | Code owner + execution-state owner | **W-4 before W-5** | §10.3 | rows stuck terminal-by-accident | recovery cannot resolve `UNKNOWN` | **NOT GRANTED** |
| **D-13-10** | Approved isolated Redis for the 5 gated tests | Coordinator / infra | none | §11.1 | none — test-only | atomicity unproven against a real server | **NOT GRANTED** |

**No decision form or governance register was edited.** The smallest unblocking request is
**D-13-6** — three integers from one query that now actually runs.

---

## 4. Workstream C — TENANT-01, measured rather than inferred (highest priority)

### 4.1 Live catalog state (MEASURED — `staging/p13_rls_live.txt`)

| Table | exists | owner | `relrowsecurity` | `relforcerowsecurity` | policies |
|---|---|---|---|---|---|
| `orders` | yes | `postgres` | **true** | **false** | 3 |
| `positions` | yes | `postgres` | **true** | **false** | 3 |
| `fills` | yes | `postgres` | **true** | **false** | 3 |
| `execution_records` | yes | `postgres` | **true** | **false** | 3 |
| `dag_tasks` | yes | `postgres` | true | false | 1 |
| `idempotency_keys` | yes | `postgres` | true | false | 1 |
| `transaction_records` | yes | `postgres` | true | false | 1 |
| `transaction_checkpoints` | yes | `postgres` | true | false | 1 |
| `reconciliation_mismatches` | yes | `postgres` | true | false | 1 |
| `exchange_keys` | yes | `postgres` | **true** | false | **1** |
| `fee_records` | yes | `postgres` | true | false | 2 |

**11/11 present · 11 with RLS enabled · 0 with FORCE · 11 owned by the connecting role ·
20 policies.**

Two corrections to the prior investigation follow from this. **`exchange_keys` DOES have RLS
enabled and one policy** (`exchange_keys_authenticated_owner`, role `authenticated`,
`auth.uid()::text = user_id::text`) — it had been reported as having none. And the "0
executable FORCE" conclusion that prior phases derived from migration *text* is now confirmed
against the *catalog*, which matters because the alembic migration wraps every `ENABLE` and
every `CREATE POLICY` in `try/except` that only `print`s — it reports success either way.

### 4.2 A result from this phase that is WITHDRAWN

My first run of the FORCE experiment (`staging/p13_rls_live.txt` §4) measured
*"FORCE did not empty the owner's reads — expected 0, measured 2"* and flagged the
`tenant_db_context` warning as possibly inaccurate.

**That result is withdrawn.** It is an artefact of the harness, and the harness printed the
reason in its own §1: `rolsuper = True`, `rolbypassrls = True`. A PostgreSQL **superuser
bypasses RLS unconditionally** — ENABLE and FORCE alike. Testing FORCE while connected as a
superuser tests nothing. The conclusion in §4.3 supersedes it.

This also surfaces a real constraint on any future migration test:

> **The local test cluster cannot reproduce production RLS behaviour while connected as
> `postgres`.** Local `postgres` is a superuser; Supabase's `postgres` is not. A FORCE
> migration validated locally as `postgres` would return a **false "safe"** signal. It must be
> validated as a non-superuser table owner, as §4.3 does.

### 4.3 The experiment, corrected (MEASURED — `staging/p13_rls_force.txt`)

Scratch schema, scratch **non-superuser `NOBYPASSRLS`** role owning the table, policy
predicate identical to the nine Family A policies, two rows across two tenants. All inside a
transaction that was rolled back; teardown verified (`scratch schema count=0`,
`scratch role count=0`).

| Step | Condition | Rows visible |
|---|---|---|
| (a) | baseline, no RLS | **2** |
| (b) | `ENABLE`, owner, GUC unset | **2** |
| (c) | `ENABLE`+**`FORCE`**, owner, **GUC unset** | **0** |
| (d) | `ENABLE`+`FORCE`, owner, GUC = tenant 1 | **1** |
| (e) | `ENABLE`+`FORCE`, owner, GUC = tenant 3 (no rows) | **0** |
| (f) | cross-tenant `INSERT` under FORCE | **REFUSED**, SQLSTATE `42501`, *"new row violates row-level security policy"* |

```
owner exempt under ENABLE alone       : True
FORCE empties reads with GUC unset    : True
correct GUC restores that tenant only : True
non-matching GUC yields zero rows     : True
```

**CONFIRMED ON A LIVE SERVER.** `tenant_db_context.py:13-19` is accurate and
`026_fee_records_row_level_security.sql:73-78` describes the real mechanism. Enabling FORCE
before the GUC is wired converts reads on `orders`/`positions`/`fills` into **silently empty
result sets** — no exception, no log line, no error code. A customer would see an empty
portfolio and the service would look healthy.

> ### DO NOT ENABLE FORCE ROW LEVEL SECURITY BEFORE `tenant_db_context` IS WIRED AND VERIFIED.
> This is no longer a docstring warning. It is a measured property of the deployed schema
> shape. The sequencing in §7.6 is mandatory, and `D-13-4` must remain NOT GRANTED until
> `D-13-3` is complete.

### 4.4 Why RLS is inert on the application path today

Two independent exemptions are present simultaneously:

1. **Owner exemption.** All 11 tables are owned by `postgres`, and every DSN in the repo
   connects as `postgres` (`database_pool.py:137-178` — no `SET ROLE`, no separate
   low-privilege DSN). Under ENABLE-without-FORCE the owner is exempt. Measured at §4.3(b).
2. **`BYPASSRLS` on the credential path.** `api_key_vault.py:76` reads
   `SUPABASE_SERVICE_ROLE_KEY`; `service_role` carries `BYPASSRLS`, so `exchange_keys`
   policies cannot apply to it at all.

So the 20 policies protect the **PostgREST surface** (`anon`/`authenticated`) and leave the
**SQLAlchemy path** unprotected. Isolation there rests entirely on application `WHERE`
clauses.

### 4.5 Three dead GUC writers

The policies compare `tenant_id` to `current_setting('app.current_tenant_id', true)`. Nothing
sets it on any live path.

| Writer | Location | Mechanism | Live? |
|---|---|---|---|
| `tenant_db_context.apply_tenant_context` | `core/tenant_db_context.py:67` | `set_config(..., true)`, parameter-bound, dialect checked, transaction required | **No — test-only importers** |
| `ReplaySafeTransaction.begin` | `backend/transactional_execution_manager.py:~226` | `SET LOCAL`, client-side interpolation, `except Exception` → `logger.debug` | **No — only reachable via `exchange_websocket_listener.py:480`, which nothing starts** |
| `ServiceRoleGuard.__enter__` | `backend/tenant_rls_validator.py:438` | `SET LOCAL`; `__exit__:446` issues `RESET` | **No — zero importers** |

The third is **new this phase**. `tenant_db_context.py:7` names it; the prior investigation
could not confirm the file existed. It does, and it is also dead.

`ServiceRoleGuard` additionally has a latent defect worth recording for whoever adopts it:
`__exit__` issues `RESET app.current_tenant_id`, which acts on the **session** value, while
`SET LOCAL` wrote a **transaction-scoped** one. The reset is both redundant and aimed at the
wrong scope. Specification note only — the code is unreachable.

**Net: there is no execution path in this repository that sets `app.current_tenant_id`.**

### 4.6 Table-by-table policy and tenant-context matrix

Two overlapping policy families target the same four trading tables. **Permissive policies OR
together**, so a row is visible if either predicate matches.

| Table | Family A (GUC, role `public`) | Family B (`auth.uid()`, `authenticated`/`service_role`) | App-level `WHERE` | SQLAlchemy path protected? | Residual risk |
|---|---|---|---|---|---|
| `orders` | `(tenant_id)::text = current_setting(…)` | `auth.uid()::text = tenant_id::text` + `service_role USING(true)` | yes — `orders.py:819/940` `UUID(user["id"])` | **No** (owner exempt) | app filter is the only control |
| `positions` | same | same | yes | **No** | same |
| `fills` | same | same | yes | **No** | same |
| `execution_records` | `(tenant_id)::text = current_setting(…)` | **`auth.uid() = user_id OR auth.uid() = tenant_id`** | yes | **No** | **predicate disagreement**: Family B treats `user_id` and `tenant_id` as interchangeable |
| `dag_tasks` | yes | — | partial | **No** | GUC-only policy, GUC never set → inert everywhere |
| `idempotency_keys` | yes | — | partial | **No** | same |
| `transaction_records` | yes | — | partial | **No** | same |
| `transaction_checkpoints` | yes | — | partial | **No** | same |
| `reconciliation_mismatches` | yes | — | partial | **No** | same |
| `exchange_keys` | — | `auth.uid()::text = user_id::text` (`authenticated`) | yes — `api_key_vault.py:185/214/230` `.eq("user_id", …)` | **No** — accessed via `service_role` (`BYPASSRLS`) | **credential table: application filter is the sole control** |
| `fee_records` | — | `auth.uid() = tenant_id` (SELECT) + `service_role` | yes | **No** | browser roles revoked; documented decision |

**Tenant-context coverage by path:**

| Path | Tenant context set? | Notes |
|---|---|---|
| FastAPI request via `database_pool.get_db` | **No** | `:385` and again `:591` (the second shadows the first); neither opens a transaction nor sets a GUC |
| FastAPI request via `database.get_db` | **No** | second factory, routed through `db_isolation.isolated_session` |
| `OrderWatchdog` | **No** | started `main.py:431-432`; cross-tenant stale sweep by design |
| `PortfolioCacheUpdater` | **No** | started `main.py:465`; iterates all registered users |
| `DashboardDataIngester` | **No** | started `main.py:64-65` |
| `ReconciliationWorker` | n/a | nothing starts it (`reconciliation_worker.py:63-67`) |
| `exchange_websocket_listener` | would set it | nothing starts it |
| `api_key_vault` | n/a | `service_role`, `BYPASSRLS` |

**A blocking prerequisite for D-13-3:** `apply_tenant_context` raises
`TenantContextNotTransactional` when no transaction is open, and **none of the four `get_db`
variants opens one** — transactions begin implicitly on first `execute`. So the helper cannot
simply be dropped into `get_db`; the chokepoint must open a transaction first. There are
**four** such chokepoints plus direct `db_pool.get_session()` callers, not one.

**Also unresolved — tenant identity itself diverges three ways:**

| Site | Expression |
|---|---|
| `core/dependencies.py:318` | `payload["tenant_id"] or app_metadata["tenant_id"] or sub` |
| `routers/orders.py:819`, `:940` | `UUID(user["id"])` — ignores `user["tenant_id"]` |
| `routers/distributed_execution.py:296` | `user["tenant_id"]` |

These agree **only because no token carries a `tenant_id` claim**, so all three collapse to
the user id. The moment a real claim appears they diverge silently. `D-13-3` must settle which
is authoritative **before** wiring, or it will wire in the divergence.

---

## 5. Workstream A — D7-1, vault signature mismatch

### 5.1 Re-validated at the current HEAD (MEASURED)

Real signature, `backend_app/backend/api_key_vault.py:177`:
`def load_decrypted_keys(self, user_id: str, exchange_id: str) -> dict`.

All three invalid calls are **still present**:

| # | Site | `access_token=` line |
|---|---|---|
| 1 | `backend_app/routers/orders.py:789-794` (`get_open_orders`) | `:792` |
| 2 | `backend_app/routers/orders.py:1094-1099` (trade history) | `:1098` |
| 3 | `backend_app/routers/market.py:50-55` (`_get_data_engine`) | `:53` |

### 5.2 Runtime consequence per site, traced through the actual handlers

| # | Guard | Consequence |
|---|---|---|
| 1 | **none** — the call is the first statement of the body | `TypeError` → FastAPI → **unhandled 500 on every request** to `GET /api/orders/open` |
| 2 | `except Exception as e:` → `logger.warning("Exchange history fetch failed: …")`; `return []` | **client is shown an empty trade history** |
| 3 | `except Exception:` → `ConnectionEngine(exchange_id=…)` → `bridge.connect()` | **silently falls back to an unauthenticated public connection** for `get_candles`, `get_orderbook`, `get_ticker`, `get_funding_rate` |

### 5.3 The patch must be surgical — a naive sweep would break paper trading

`access_token=user.get("access_token")` is a **valid and widely used** pattern elsewhere:
`routers/risk.py:313`, `:325`, `:407` and `routers/paper_trading.py:88`, `:103`, `:141`,
`:161`, `:193`, `:250`, `:282`, `:305` all pass it to `paper_trading_service` methods that
**do** accept it. Only the three `load_decrypted_keys` calls are wrong. A "remove all
`access_token=` arguments" change would break paper trading.

### 5.4 Proposed patch (NOT APPLIED — `D-13-1` NOT GRANTED)

Three single-line deletions:

```diff
--- a/backend_app/routers/orders.py
@@ -789,7 +789,6 @@
     keys = vault.load_decrypted_keys(
         user["id"],
         exchange_id,
-        access_token=user.get("access_token"),
     )
@@ -1095,7 +1094,6 @@
         keys = vault.load_decrypted_keys(
             user["id"],
             exchange_id,
-            access_token=user.get("access_token"),
         )
--- a/backend_app/routers/market.py
@@ -50,7 +50,6 @@
         keys = vault.load_decrypted_keys(
             user["id"],
             exchange_id,
-            access_token=user.get("access_token"),
         )
```

**Security semantics — verified, and this is NOT a silent removal of an auth control.**
`load_decrypted_keys(self, user_id, exchange_id)` never accepted a token; it scopes by
`user_id`, which continues to be passed. The token was being supplied to a parameter that does
not exist, so it was never used for authorization. Authentication still happens upstream at
`Depends(get_current_user)`.

**A behaviour change that must be stated, not glossed:** site 3 currently falls back to a
public connection on *every* call, because the `TypeError` is caught by its own
`except Exception`. After the fix it would use the caller's real keys. That is the intended
semantics, but it changes what the venue sees — authenticated rather than anonymous rate
limits, and the user's key used for market data. The owner should approve that explicitly.

**Preserve** the `market.py:56` guard `if not keys or not isinstance(keys, dict):` — the D3 fix.

**Lint:** flake8 baseline for both files is **224 pre-existing findings**; none on a deletion
line. The blocking `static-audit` check is unaffected.

### 5.5 Why existing tests miss this, and the test design that would not

`tests/test_market_none_keys_fix.py:13` declares its double as
`load_decrypted_keys(self, user_id, exchange_id, access_token=None)`. **The double matches the
broken calls; the real class does not.** Both its tests pass today and would pass with the
defect present.

Proposed regression coverage (not written — no grant): bind every call site against the real
signature with `inspect.signature(...).bind(...)` via AST extraction, never instantiating the
vault; assert `GET /api/orders/open` does not 500; assert `_get_data_engine` returns an
**authenticated** engine when keys exist; assert the `market.py:56` guard still routes a
`None`/partial dict to the public fallback.

---

## 6. Workstream B — W-6, error classification: 10 of 20 misclassify

### 6.1 Phase 12 reproduced exactly

`staging/p13_w6_matrix.txt`: Phase 12's eight cases → **8 cases, 3 misclassified, IDENTICAL**
(same three cases). Reproduction is clean, on the same interpreter and the unchanged file.

### 6.2 Expanded matrix (MEASURED — 21 cases, 20 asserted, **10 misclassified**)

| Case | Actual | Expected | Verdict |
|---|---|---|---|
| P12-2 timeout carrying request URL | `InvalidSymbolError` | `NetworkError` | **MIS** |
| P12-4 network error, venue market path | `InvalidSymbolError` | `NetworkError` | **MIS** |
| P12-6 `"monkey patch failed"` | `AuthenticationError` | `ExchangeError` | **MIS** |
| P13-1 timeout on bybit market path | `InvalidSymbolError` | `NetworkError` | **MIS** |
| P13-2 DNS failure w/ symbol query | `InvalidSymbolError` | `NetworkError` | **MIS** |
| P13-4 `"unexpected keyword argument"` | `AuthenticationError` | `ExchangeError` | **MIS** |
| P13-5 `"redis keyspace notification"` | `AuthenticationError` | `ExchangeError` | **MIS** |
| P13-6 `"monkeypatch could not set"` | `AuthenticationError` | `ExchangeError` | **MIS** |
| **P13-9 `"Signature for this request is not valid"`** | **`ExchangeError`** | **`AuthenticationError`** | **MIS — new class** |
| **P13-11 `"RateLimitExceeded on GET /api/v5/market/ticker"`** | **`InvalidSymbolError`** | **`RateLimitError`** | **MIS — new class** |
| P12-1,3,5,7,8 · P13-3,7,8,10,13 | — | — | ok (10) |
| P13-12 symbol error that also says timeout | `InvalidSymbolError` | — | genuinely ambiguous, not asserted |

### 6.3 Two new defect classes

**(a) A genuine auth failure is MISSED (false negative).** `"Signature for this request is not
valid"` → `ExchangeError` with `error_code=None`. The ladder's auth test
(`:1252`) checks `authentication` / `apikey` / `key` — none appear. A real credential/signature
rejection is reported as a generic error. This is the **inverse** of the `key` over-match and
arguably worse: the operator gets no signal at all.

**(b) ccxt's `RateLimitExceeded` is not detected by class name.** The rate-limit test
(`:1243`) requires the literal `"rate limit"` **with a space**. Lowercased
`"ratelimitexceeded"` does not contain it. Detection therefore depends on the message body
also saying `"too many requests"` — P12-8 passes only for that reason. P13-11, a throttle
whose message lacks that phrase but carries a market path, becomes `INVALID_SYMBOL` and
**loses `retryable=True`**. Rate limits are the most common recoverable venue error.

### 6.4 Token-boundary behaviour (MEASURED)

`"key"` is a 3-character substring with no word boundary. **8 of 8** non-auth words shadow:

```
monkey, monkeypatch, keyword, keyspace, turkey, keychain, donkey, whiskey
                                    -> all AuthenticationError / AUTH_ERROR
```

`"market"`/`"symbol"` shadow **5 of 5** venue paths, including two not previously identified:

```
/api/v5/market/ticker · /v5/market/tickers · /api/v3/ticker?symbol=BTCUSDT
/spot/v1/symbols · /market-data/ohlc        -> all InvalidSymbolError
```

### 6.5 Ordering, and what is already correct

```
insufficient + symbol -> InsufficientFundsError   ok
rate limit   + symbol -> RateLimitError           ok
timeout      + market -> InvalidSymbolError       WRONG
network      + key    -> NetworkError             ok
```

The first two rungs are correctly ordered. The defect is specifically **symbol/market (`:1246`)
sitting above network (`:1249`)**, plus the weak tokens.

**Redaction verified intact** — with `_api_key`/`_api_secret` set, neither leaked into
`err.message`, and classification still worked. Any fix must leave `:1236` first.

### 6.6 `retryable` is still log-only (RE-MEASURED)

18 occurrences of `.retryable` outside `staging/`; **15 production, 0 control flow**. 12 of the
15 are in duplicate source trees (`aerora_quant_platform/`, `connection_layer/`); only 3 are in
`backend_app/`, and the single read is the log f-string at `exchange_executor.py:1687`.
Phase 12's conclusion holds: **latent, not live** — and it becomes live the moment W-3 wires a
retry policy to it.

### 6.7 Proposed correction (NOT APPLIED — `D-13-2` NOT GRANTED)

1. Move the network test **above** the symbol/market test. A transport failure is a transport
   failure regardless of the URL it carried.
2. Replace the bare `"key"` token with word-boundary matching on `api key` / `apikey` /
   `api-key` / `invalid key`, and **add `signature`** to close §6.3(a).
3. Add `ratelimit` (no space) and `429` to the throttle test to close §6.3(b).
4. Leave the redaction at `:1236` exactly where it is.
5. **Do not wire retry behaviour to `retryable`** — out of scope, separate decision.

`staging/p13_w6_expanded_matrix.py` is the executable basis for the tests. Default-lane
coverage for this ladder remains **zero** (the sandbox file skips 16/16 without credentials).

---

## 7. Workstream C (cont.) — the safe RLS sequence

### 7.6 Mandatory dependency order

| Step | Action | Gate to clear before the next step |
|---|---|---|
| **1** | **Settle tenant identity.** Choose one authoritative derivation among the three in §4.6 and document it. | One expression, used everywhere |
| **2** | **Choose the chokepoint(s).** Four `get_db` variants exist, plus direct `get_session()` callers. Resolve the duplicate `get_db` definitions (`database_pool.py:385`/`:591`) first. | Every tenant-scoped read passes through an instrumented path |
| **3** | **Open a transaction at that chokepoint**, then call `apply_tenant_context`. It *refuses* without one. | `read_tenant_context` returns the expected tenant inside a handler |
| **4** | **Fail closed** on missing/invalid context for tenant-scoped paths. Keep workers explicitly exempt and documented. | A request with no derivable tenant is refused, not served broadly |
| **5** | **Regression tests**: context present on every tenant-scoped access path; survives transaction reuse, rollback, and pooled-connection handover; cross-tenant read denied; cross-tenant write denied by `WITH CHECK`; **missing context denies rather than permits** | All green |
| **6** | **Verify under the intended application role** — as a non-superuser owner, per §4.2 | Policies demonstrably bite |
| **7** | **Only now** prepare the FORCE migration (`D-13-4`) | Steps 1-6 complete |
| **8** | **Test the migration in isolation**, reads *and* writes on `orders`/`positions`/`fills`/`execution_records` | 0 silently-empty reads |

Also to resolve before step 7: the **two policy families** (§4.6) should be reconciled rather
than left OR'd, the `execution_records` predicate disagreement settled, and `exchange_keys`
re-examined given it is reached via `BYPASSRLS` where no policy can apply.

**Rollback plan for step 7:** `ALTER TABLE … NO FORCE ROW LEVEL SECURITY` per table, which
restores the owner exemption immediately and needs no data movement. The failure mode being
rolled back is **silent empty reads**, not errors, so the deployment must be gated on a
positive read check per table rather than on absence of exceptions — this is the one place
where "no errors in the log" is actively misleading.

---

## 8. Workstream D — D6-C corrected evidence request

### 8.1 Fixture verification (TESTED — `staging/p13_d6c_fixtures.txt`)

Fixtures mirror the real types measured in Phase 12: `execution_records.tenant_id uuid`,
`exchange_keys.user_id uuid`, `UNIQUE (user_id, exchange_id)`.

**The previously circulated query fails to plan:** `UndefinedFunction`, **SQLSTATE 42883**,
`operator does not exist: uuid = text`. Had the DB owner run it, they would have replied with
an error, not evidence.

**The corrected query: 7/7 scenarios exact, 0 mismatches.**

| Scenario | Measured | Expected |
|---|---|---|
| 1 tenant 2 venues; 1 tenant no keys | `(2, 1, 1)` | `(2, 1, 1)` |
| 1 tenant 3 venues | `(1, 1, 0)` | `(1, 1, 0)` |
| 3 tenants, none with keys | `(3, 0, 3)` | `(3, 0, 3)` |
| 3 tenants, one key each | `(3, 3, 0)` | `(3, 3, 0)` |
| **empty execution table** | `(0, 0, 0)` | `(0, 0, 0)` |
| key row for a tenant with no executions | `(1, 1, 0)` | `(1, 1, 0)` |
| duplicate execution rows (DISTINCT collapses) | `(2, 1, 1)` | `(2, 1, 1)` |

Overcount demonstrated side-by-side on text columns: `ORIGINAL -> with_keys=2,
without_keys=1` — **sum 3 for 2 tenants**; `CORRECTED -> 2, 1, 1`.

### 8.2 The request (`D-13-6` → production DB owner)

```sql
-- D6-C. Counts only: three integers. No identifiers, no credentials, no token contents.
-- Verified to plan and to count correctly against uuid columns (Phase 13 §8.1).
SELECT count(*) AS distinct_tenants,
       count(*) FILTER (
           WHERE EXISTS (SELECT 1 FROM public.exchange_keys k
                          WHERE k.user_id = e.tenant_id)
       ) AS tenants_with_keys,
       count(*) FILTER (
           WHERE NOT EXISTS (SELECT 1 FROM public.exchange_keys k
                              WHERE k.user_id = e.tenant_id)
       ) AS tenants_without_keys
  FROM (SELECT DISTINCT tenant_id FROM public.execution_records) e;
```

Run once, read-only, against production. Expected columns exactly
`distinct_tenants, tenants_with_keys, tenants_without_keys`. **No casts** — both columns are
`uuid`; a `::text` cast on both sides also plans but defeats `idx_exchange_keys_user_id`.

**Interpretation limits.** Only **`tenants_without_keys = 0`** is load-bearing, and it is
**necessary but not sufficient** — see §8.4. A non-zero value means at least one tenant with
execution history has no credential row under the key the vault reads.

### 8.3 Disclosure check (MEASURED)

Columns `['distinct_tenants','tenants_with_keys','tenants_without_keys']`, row `(2,1,1)`,
**every value an integer**. Verdict: **counts only, no identifiers**. No user id, credential,
or token is selected, returned, or logged.

### 8.4 The claim-divergence half is a separate request (`D-13-8` → auth/platform owner)

`core/dependencies.py:318` derives `tenant_id` from a JWT claim chain falling back to `sub`;
`exchange_keys.user_id` is written from `user["id"]` (`routers/exchange.py:429-431`). If a
token carries a `tenant_id` differing from `sub`, execution rows and credential rows key on
different values.

**No query over application tables can detect this** — the divergence lives in the token. The
request is: among tokens issued in the last 30 days, the **count** of distinct subjects whose
`tenant_id` (top-level or `app_metadata`) is present and differs from `sub`. A count, not a
list. **This phase does not claim the §8.2 query can answer it.**

**Three separation levels, kept apart as the brief requires:** query **validity** —
established (§8.1); **non-production fixture verification** — established, 7/7;
**production evidence received** — **NONE**.

---

## 9. Workstreams E and F — D6-D vault type, D7-2 portfolio cache

### 9.1 D6-D: the import cannot resolve (MEASURED — `staging/p13_d6d_import.txt`)

`backend_app/core/state.py:67` is `APIKeyVault = safe_import("APIKeyVault", "api_key_vault")` —
a **bare top-level module name**, not `backend_app.backend.api_key_vault`.

| `sys.path` | `api_key_vault` resolves? |
|---|---|
| repo root only (a `python -m backend_app.main` style path) | **False** |
| `pytest.ini` pythonpath (`. aerora_quant_backend_updated_final1 backend`) | **False** |
| repo root + `backend_app/backend/` | True |
| *(comparison)* `backend_app.backend.api_key_vault` from repo root | True |

The file exists. The **name** does not resolve under either path configuration present in this
repository. `safe_import` catches that and returns
`lambda *a, **k: MockEngine("MockAPIKeyVault")` (`state.py:63-64`), so **`app_state.vault` is a
`MockEngine` under both** — and `master_executor.py:127` → `:252` → `:255-257` `keys["api_key"]`
would raise **`TypeError` on the live order path**, not at boot.

This moves D6-D from "unknown" to "**mock is the expected outcome**". What remains unestablished
is whether a deployed process happens to have `backend_app/backend/` on `sys.path`.

### 9.2 Correction to Phase 12's emitter strings

Phase 12 recorded the emitters as `Failed to import APIKeyVault` and
`APIKeyVault initialization failed, using mock`. Both are paraphrases of **f-string templates**,
not emitted text. The rendered strings are:

```
Failed to import APIKeyVault from api_key_vault: <error>              (state.py:63, WARNING)
DEV_MODE: APIKeyVault initialization failed, using mock: <error>      (state.py:108, WARNING)
```

Line numbers are from a direct enumerated read of the file
(`staging/p13_d6d_import.txt`), not from a grep rendering — the two disagree by one for
match lines, and a citation a platform owner will grep by has to be right.

A log search for the Phase 12 wording would match the first but **miss the second** — and the
second is the one indicating a mock was substituted *after* a successful import.

### 9.3 The evidence request (`D-13-7` → platform/deployment owner)

For **staging** and **production** separately, from the boot log at WARNING or above:

1. Did `Failed to import APIKeyVault from api_key_vault` appear? (yes/no)
2. Did `DEV_MODE: APIKeyVault initialization failed, using mock` appear? (yes/no)
3. The concrete type of `app_state.vault` — `APIKeyVault` or `MockEngine`.
4. Whether `backend_app/backend` is on the process `sys.path`.

**Do not** query, print, or attach any credential, exchange key, token, or decrypted vault
content. Booleans and a type name only. **Must be read from the boot log, not probed:**
`MockEngine.__getattr__` (`state.py:51-55`) answers any attribute with a no-op, so no
introspection distinguishes it from the real vault. **Status: PREPARED, NOT SUPPLIED.**

### 9.4 D7-2: the import is still broken, and the health gap is sharper than reported

`portfolio_cache_updater.py:151` — `from backend_app.backend.key_manager import KeyVault`.
Repo-wide, `key_manager` appears **only** at that line: the module does not exist. Then `:158`
`vault = KeyVault()` and `:159` `keys = vault.load_decrypted_keys(user_id, exchange_id)` — note
this call uses the **correct 2-argument signature**, so D7-2 is purely the missing module.

**The false-green mechanism, traced precisely:**

1. The broken import is **inside `_update_user_portfolio`**, not at module scope or in
   `start_portfolio_cache_updater()`. So startup genuinely succeeds and `main.py:466` records
   `service_status["portfolio_cache_updater"] = "ACTIVE"` — which is **truthful about startup**.
2. The per-user `ImportError` **is** logged, at ERROR: `portfolio_cache_updater.py:131-133`
   `logger.error(f"❌ Failed to update portfolio for {user_id}: {e}")`. *(Correction: earlier
   phases described it as silently swallowed. It is logged — just not surfaced.)*
3. **`/health/services` (`main.py:1077-1082`) reports only four services** —
   `consistency_checker`, `order_watchdog`, `pnl_engine`, `reconciliation_scheduler`.
   **`portfolio_cache_updater` is not among them**, so it contributes to neither
   `summary.active/inactive/failed` nor the overall `status`. It appears only in the `raw`
   passthrough, as `"ACTIVE"`.
4. `main.py:1092-1095` already documents the staleness honestly (OBS-01): the snapshot *"is
   written ONCE, during lifespan startup, and never refreshed… the answer is not 'are these
   services running' but 'did these services start'"*, and `_captured_at` / `as_of` /
   `_age_seconds` expose the age.

So the gap is **not** the documented staleness. It is that the updater is **excluded from the
status computation**, and that even a correct "did it start" answer would be `ACTIVE` while the
loop fails every cycle for every user. Overall `status` can read `"ok"` indefinitely.

### 9.5 D7-2 proposal (NOT APPLIED — `D-13-5` NOT GRANTED)

Adopt the existing supported interface — `backend_app.backend.api_key_vault.APIKeyVault`,
whose `load_decrypted_keys(user_id, exchange_id)` the call already matches. **Do not create a
`key_manager` module to satisfy the import**; that would add a second credential path.

Regression matrix: successful refresh populates both cache keys · dependency-initialisation
failure is reported, not just logged · per-user isolation (one user's failure does not stop
others) · stale cache is distinguishable from fresh · **health reports degraded when the
updater cannot perform its task** · recovery after the dependency returns.

The health change is a **separate, larger decision** than the import fix: including the updater
in the status computation changes what `/health/services` reports for every consumer, and
per-cycle failure reporting needs a heartbeat the four non-`WorkerBase` services do not have.
Recommend splitting: (i) fix the import; (ii) surface updater health. (i) is small and safe;
(ii) needs a decision about heartbeats.

---

## 10. Workstream G — W-3 / W-4 / W-5 ordering

| Item | Current state | Constraint |
|---|---|---|
| **W-3** venue order id | targets `order_watchdog.py:370`, `:712`, `event_router.py:135` | Coalesce, **never swap**. Migration `030_execution_venue_order_id.sql` has a postflight that **aborts if an FK is added to `venue_order_id`** — it is a venue-side identifier, and the postflight exists to stop someone making it a foreign key. |
| **W-3 ↔ W-6** | — | **W-3 must not wire retry behaviour to `error.retryable` while 10 of 20 classifications are wrong (§6).** `D-13-2` precedes `D-13-9`. |
| **W-4** `UNKNOWN` exit | **no exit rule exists** | Confirmed at `execution_record.py:1488`: the recovery CAS is pinned `AND upper(status::text) = 'EXECUTING'`, so an `UNKNOWN` row is **unreachable by `resolve_stale_execution`**. `:1443-1448` documents `UNKNOWN` as the safe unresolved state that releases the idempotency block — rows can leave the block but nothing transitions them out of `UNKNOWN`. |
| **W-5** recovery | blocked | **Must not be attempted before W-4 defines the exit rule**, or rows accumulate terminal-by-accident. |

Required `UNKNOWN` exit criteria before W-5 (specification, not implementation): a venue
reconciliation that produces a real order id → `COMPLETED`; a venue that definitively reports
no such order **and** `submitted_at` evidence excluding submission → `FAILED`; anything else
stays `UNKNOWN` and alerts. Deterministic tests needed for concurrent transitions, ambiguous
submissions, duplicate venue events, and restart recovery. **All NOT GRANTED.**

---

## 11. Workstreams H and I — QUOTA-01, CI, and concurrent sessions

### 11.1 QUOTA-01 — real Redis still unavailable (MEASURED)

`AERORA_TEST_REDIS_URL` **unset** · `redis-server` **not on PATH** · port 6379 **CLOSED** ·
0 matching services. Unchanged from Phase 12.

The 5 gated tests therefore **SKIP**, with the gate's own reason:
*"`AERORA_TEST_REDIS_URL` is not set… **A skip here is NOT a pass.**"* The gate
(`tests/real_redis_gate.py`) correctly distinguishes three states — unset → SKIP, set but
unreachable → **FAIL**, reachable → RUN — so a broken CI Redis cannot read as green.

**No Redis was started.** Per the brief that is an infrastructure change requiring separate
authorization (`D-13-10`). **No skip was converted into a pass.**

Phase 12 measured the suite at this HEAD: **50 passed / 5 skipped**, plus 184 tests across the
execution core at **179 passed / 5 skipped / 0 failed**. Source, fixtures, interpreter and
environment are unchanged, so per the brief those runs were **not repeated**.

### 11.2 CI — cross-referenced, not duplicated

`staging/PHASE14_MAIN_STABILIZATION_AND_CRITICAL_CLOSURE.md` appeared mid-phase from a
concurrent session and covers CI thoroughly. Its load-bearing findings, cross-referenced rather
than re-derived:

- **`01 PR Check` NEVER RAN at `ecd23a9e`.** No test, Redis, PostgreSQL or flake8 evidence
  exists at the current HEAD. **So there is no CI evidence for this baseline at all** — any
  claim resting on "CI is green" is unsupported here.
- `05 Security` failed on Bandit (9 × B608, MEDIUM) and Trivy (`AWS-0054`, `AWS-0104` ×4).
- **`03 Deploy` correctly refused** at its Pre-Deployment Validation Gate; the ECS deploy was
  **skipped**. Nothing was deployed.
- `AP-W1D-4` (provisioning-snapshot recapture) is **NOT GRANTED** at ledger `:803`, and the
  snapshot **must not be regenerated to make CI green**.

Per the brief: **flake8 (`static-audit`) is the blocking lint gate**; `black` and `isort` are
`continue-on-error: true` and are **not** required gates. No CI was dispatched by this phase.

### 11.3 Five independent sessions, one conclusion

| Artifact | Bearing |
|---|---|
| `PHASE11B_A23E12_PARALLEL_BLOCKER_RESOLUTION.md` | D7-1 per-site impact (adopted §5.2); W-6 "CONFIRMED, with a correction to the previously reported trigger" |
| `PHASE11B_A23E13_D7_AUTHORIZATION_AND_BLOCKER_REDUCTION.md` | CI provisions an approved isolated Redis; ledger `:802` `NOT GRANTED` |
| `PHASE11B_A23E14_REGISTER_AUTHORIZATION_AND_BLOCKER_REDUCTION.md` | "no authorization arrived"; **Granted 0 · Unresolved 8** |
| `PHASE11B_A23E15_TENANT_CACHE_AND_PARALLEL_REMEDIATION_READINESS.md` | TENANT-01 test matrix incl. "missing context **denies** rather than permits"; D7-2 and W-3/4/5 ABSENT |
| `PHASE11B_A23E16_SCOPE_CI_AND_BLOCKER_REDUCTION.md` | all five ledger rows `NOT GRANTED` |
| `PHASE14_MAIN_STABILIZATION_AND_CRITICAL_CLOSURE.md` | §11.2 |

All read read-only and **left exactly as found**. One notable agreement: A23E15 independently
specifies that missing tenant context must **deny**, which is step 4 of §7.6.

---

## 12. Commands executed, counts, and limitations

### 12.1 Probes (all read-only; all new files)

| Probe | Output | Result |
|---|---|---|
| `p13_rls_live_probe.py` | `p13_rls_live.txt` | 11/11 tables: RLS on, FORCE off, owner `postgres`; 20 policies. **§4 verdict withdrawn (§4.2)** |
| `p13_rls_force_experiment.py` | `p13_rls_force.txt` | **ENABLE=2, FORCE+no GUC=0, FORCE+GUC=1**; cross-tenant INSERT refused `42501` |
| `p13_w6_expanded_matrix.py` | `p13_w6_matrix.txt` | 21 cases, **10/20 misclassified**; P12 reproduced identically; redaction intact; `retryable` log-only |
| `p13_d6c_fixtures.py` | `p13_d6c_fixtures.txt` | original fails `42883`; corrected **7/7**, counts only |
| `p13_d6d_import_probe.py` | `p13_d6d_import.txt` | bare `api_key_vault` **does not resolve** under 2 of 3 paths |
| `p13_final_audit` (git) | inline §2 | HEAD unchanged, 0 staged, 2 files differ |

### 12.2 Tests

**No pytest run this phase.** No implementation was authorized, so there was nothing new to
validate, and the brief forbids re-running suites whose source, fixtures, interpreter and
environment are unchanged. Phase 12's counts (§11.1) stand as prior evidence and are **not**
re-presented as this phase's results.

Two probe scripts failed before producing usable output and were fixed, with the reason
recorded in each script's docstring rather than hidden: `p13_w6_expanded_matrix.py` (wrong
credential attribute names — `_api_key`, not `api_key`) and `p13_rls_force_experiment.py`
(a refused INSERT aborted the transaction before teardown; fixed with savepoints).

### 12.3 Limitations, stated plainly

- **No production database.** D6-C counts remain unmeasured; only validity and fixture
  correctness are established.
- **No deployed environment.** D6-D's final answer needs a boot log.
- **No real Redis.** 5 tests unexecuted, reported as skips.
- **No authorization channel.** All ten decisions in §3.2 are undecided.
- **No CI evidence at this HEAD at all** (§11.2) — not merely incomplete evidence.
- **The local cluster cannot validate a FORCE migration as `postgres`** (§4.2): local
  `postgres` is a superuser, Supabase's is not. A local run would give a false "safe".
- **Live `relforcerowsecurity` was read from the test cluster, not production.** Production
  values are expected to match the migrations but are **not measured**.
- **`staging/pg_dsn.txt` is stale** (51805 vs the live 51815) and was deliberately not edited.
- **Three corrections to prior claims** are recorded, not quietly fixed: §4.2 (this phase's own
  withdrawn result), §9.2 (Phase 12 emitter strings), §9.4 item 2 (D7-2 "silently swallowed").

---

## 13. Before/after blocker status

Statuses change **only** where this phase produced supporting evidence.

| Blocker | Before (Phase 12) | **After (Phase 13)** | Basis |
|---|---|---|---|
| **TENANT-01** | OPEN; FORCE=0 from migration text; warning quoted from a docstring | **OPEN — and the sequencing constraint is PROVEN** | Live catalog measured; FORCE experiment run as a non-superuser owner: 2 → **0** → 1 |
| **W-6** | OBSERVED+MEASURED, 3/8, latent | **MEASURED, 10/20, two new defect classes** | 21-case matrix; signature miss; `RateLimitExceeded` miss; `retryable` re-confirmed log-only |
| **D6-C** | query corrected, unverified beyond planning | **query VERIFIED on fixtures, 7/7, counts-only** | real `uuid` types; original fails `42883` |
| **D6-D** | BLOCKED, vault type unknown | **BLOCKED — but mock is now the EXPECTED outcome** | bare import fails to resolve under both repo paths; emitter strings corrected |
| **D7-1** | spec verified, 7/4/3 | **re-validated; per-site impact adopted; patch semantics verified** | 3 sites present; auth control confirmed not removed; paper-trading false-positive risk identified |
| **D7-2** | OPEN, import broken | **OPEN — health gap precisely located** | excluded from the 4 services that determine `status`; failures logged at ERROR but unsurfaced |
| **QUOTA-01** | TESTED except real Redis | **unchanged** | Redis still absent; no re-run (unchanged inputs) |
| **W-3 / W-4 / W-5** | OPEN, ordered | **unchanged; W-6 confirmed as a hard prerequisite for W-3** | CAS pinned to `EXECUTING` re-verified at `:1488` |
| **D6-A / D6-B** | NOT DECIDED | **unchanged** | no decision arrived |
| **W-1b** | OPEN | **unchanged** | gated on D6-D |
| **CI** | flake8 blocking; black/isort never passed | **no CI evidence exists at this HEAD** | cross-referenced §11.2 |
| **Live trading** | **OFF / NOT CERTIFIED** | **OFF / NOT CERTIFIED** | not a candidate for change |
| Paper track | 53% | **53%, unrevised** | not examined |

**Net: 0 blockers closed — none could be, with 0 grants. 5 advanced in evidence quality
(TENANT-01, W-6, D6-C, D6-D, D7-2). 2 new W-6 defect classes found. 3 prior claims corrected.**

---

## 14. Ranked next actions

| # | Action | Smallest thing needed | Why first |
|---|---|---|---|
| **1** | **Keep `D-13-4` (FORCE RLS) blocked** | nothing — a decision *not* to act | §4.3 proves it would silently empty `orders`/`positions`/`fills`. The only action here that can make things worse is acting. |
| **2** | Deliver §8.2 to the production DB owner | **3 integers** | Blocked on a broken query since D8; now verified to run. Cheapest blocker to retire. |
| **3** | Deliver §9.3 to the platform owner | **4 fields × 2 environments** | §9.1 indicates the vault is a mock; this confirms it. Gates W-1b and the live order path. |
| **4** | Grant or refuse `D-13-1` (D7-1) | **one decision, 3 line deletions** | One endpoint 500s; one shows customers empty history; one silently drops authentication. |
| **5** | Grant or refuse `D-13-2` (W-6) | **one decision** | 10/20 misclassify, and it is a hard prerequisite for W-3. |
| **6** | Settle tenant identity (§7.6 step 1) | **choose one of three expressions** | Wiring `tenant_db_context` before this wires in the divergence. Costs no code. |
| **7** | Split `D-13-5` into import-fix and health-surfacing | **scope decision** | The import fix is small and safe; health reporting needs a heartbeat decision. |
| **8** | Decide `D-13-10` (isolated Redis) | **one decision** | 5 tests unproven against a real server. |
| **9** | Note that **no CI evidence exists at `ecd23a9e`** | — | Any readiness claim resting on CI is currently unsupported. |

---

*Phase 13 closed. 0 production files · 0 test files · 0 migrations · 0 commits · 0 pushes ·
0 staged entries · 0 CI dispatched. 9 new evidence files, none overwriting another session's
artifact. **LIVE TRADING: OFF / NOT CERTIFIED.***
