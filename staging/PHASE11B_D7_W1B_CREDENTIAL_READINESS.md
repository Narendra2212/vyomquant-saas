# Phase 11B-D7 — Watchdog Credential Readiness and W-1b Decision Closure

**READ-ONLY. NO SOURCE, TEST, MIGRATION, CI OR DEPLOYMENT FILE WAS MODIFIED.** No
implementation is authorized by this task and none was performed. **W-1b remains OPEN.**

**Live trading remains OFF / NOT CERTIFIED.**

## Answers to the completion criteria, up front

| # | Question | Answer |
|---|---|---|
| 1 | Is a real credential vault guaranteed at production startup? | **No.** A deployed boot guarantees the six required credential *variables* are present, non-empty and non-placeholder. It guarantees nothing about the *object* on `app_state.vault`: three silent, unchecked substitution paths remain. **And this is not a watchdog-specific risk — the live order path already depends on it** (§1.4). |
| 2 | Is tenant identity mapping authoritative? | **Still unproven, but no longer vague.** The exact divergence mechanism is `dependencies.py:316`. The mapping holds only under an invariant that is asserted nowhere, and `exchange_keys.user_id` has no FK while the vault bypasses RLS (§2). |
| 3 | Who must decide credential caching and executor lifecycle? | D6-A: security/credential owner + coordinator. D6-B: watchdog owner + execution-state owner. Framed, not answered, in §3. |
| 4 | Smallest approved path to complete W-1b safely? | Four gates in order — D6-D, D6-C, D6-A, D6-B — then the two-file integration from D6 §3.1. §5.2. |
| 5 | Which independent tasks can progress now? | Five, listed in §5.1. W-3 is among them, with the end-to-end caveat restated. |

---

## 0. Phase 0 — rebaseline and preservation

| Property | D6 reference | **Measured now** | Moved? |
|---|---|---|---|
| Branch | `main` | `main` | no |
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | **no** |
| `origin/main...HEAD` | `0 0` | `0 0` | no |
| Worktrees | 1 | **1** | no |
| Staged entries / digest | 22 / `2e994018…7152` | **22 / matches** | **no** |

Candidate files, all **hash-identical to D6** — so every D6 source finding is reused rather
than re-derived, as instructed:

| File | SHA-256 (16) |
|---|---|
| `backend_app/backend/api_key_vault.py` | `FE42F4F3E63BF417` |
| `backend_app/backend/exchange_executor.py` | `81ED5679BB93AFB0` |
| `backend_app/backend/order_watchdog.py` | `507F5C85A7984200` |
| `backend_app/main.py` | `DE1CCF9ADFFFBA0C` |
| `backend_app/core/state.py` | `6AC55227B378EE5E` |
| `tests/test_d5_watchdog_adjudication_repair.py` | `AB918192B4801F13` |

Concurrent work observed since D6: `staging/PHASE11B_A23_REDIS_LIFECYCLE_IMPLEMENTATION_REPORT.md`
(another workstream). No candidate file touched. Nothing staged, nothing overwritten.

**Commands executed this phase** — all read-only: `git rev-parse` / `rev-list` /
`worktree list` / `diff --cached --raw` (digest only); `Get-FileHash` on the six candidate
files; `grep_search` over `backend_app/**/*.py`, `backend_app/**/*.sql` and two single
files; `read_file` on nine files. **No test run, no database query, no network call.** No
secret value was read, printed or copied — only variable *names* and the code that reads
them.

---

## 1. Phase 1 — D6-D: real vault readiness

### 1.1 Construction order is the crux

`app_state = AppState()` executes at **import time** of `backend_app/core/state.py`
(bottom of file), i.e. when `main.py` imports it — **before** the lifespan runs any
validation. Two silent failure points, both `logger.warning` only:

| Stage | Code | On failure |
|---|---|---|
| Import | `state.py:67` `APIKeyVault = safe_import("APIKeyVault", "api_key_vault")` — a **bare module name**, resolved through the `sys.path.insert` at `state.py:19-21` | `safe_import` (`:58-64`) catches **every** exception and returns `lambda *a, **k: MockEngine("MockAPIKeyVault")` |
| Construction | `state.py:95` `vault: Any = field(default_factory=lambda: _safe_init(APIKeyVault, "APIKeyVault"))` | `_safe_init` (`:104-109`) catches **every** exception → `MockEngine("MockAPIKeyVault")` |

`MockEngine` (`state.py:45-56`) answers **any** attribute with a noop returning `None`, so
`hasattr` cannot detect the substitution (D6 §2.2). A repository-wide search confirms
`MockEngine` appears **only** in `state.py` — nothing anywhere detects or reports it.

### 1.2 What a deployed boot does guarantee

`main.py`'s lifespan runs three checks, in order:

1. `settings.validate_required()` (`config.py:234-253`) — requires `SUPABASE_URL`,
   `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, plus `JWT_SECRET` and
   `SUPABASE_ANON_KEY` when `DEV_MODE` is off. **`MASTER_ENCRYPTION_KEYS` is NOT in this
   list.**
2. `settings.assert_jwt_secret_is_production_grade()`
3. `settings.assert_credentials_are_production_grade()` (`config.py:496`) — enforces
   `REQUIRED_DEPLOYED_CREDENTIALS` (`config.py:415-422`), which **does** include
   `MASTER_ENCRYPTION_KEYS`, and whose own comment cites `api_key_vault.py:80` and `:84` as
   the reason. Verdicts distinguish MISSING / EMPTY / PLACEHOLDER.

So a deployed process **cannot serve traffic** with the vault's three environment variables
missing: the vault would be a mock, and step 1 or 3 would raise `RuntimeError` first. That
is a genuine, pre-existing protection and it is worth stating plainly.

**Two caveats on its reach.** `assert_credentials_are_production_grade` returns early when
`_is_deployed_environment()` is False — `ENV` in development/dev/local/test/testing, or
`DEV_MODE=true` — so `MASTER_ENCRYPTION_KEYS` is unchecked outside a deployment. And every
check validates **environment variables**, never the constructed object.

### 1.3 The three gaps that survive a clean deployed boot

| # | Failure mode | Detected by startup? |
|---|---|---|
| **G1** | `ImportError` on the bare name `api_key_vault` — a missing `cryptography` or `supabase` wheel, a container path difference, a circular import. Independent of every environment variable. | **No** |
| **G2** | `APIKeyVault.__init__` raising for a non-env reason: `ConnectionError("Vault database connection failed.")` if `create_client(supa_url, supa_key)` throws (`api_key_vault.py:86-91`), or `ValueError("One or more MASTER_ENCRYPTION_KEYS are invalid.")` if `Fernet(k.encode())` rejects a key (`:97-104`). **A present, non-placeholder but malformed Fernet key passes §1.2 step 3 and fails here.** | **No** |
| **G3** | No `isinstance(app_state.vault, APIKeyVault)` anywhere, no vault field in `/health/services`, and `hasattr` is defeated by `MockEngine.__getattr__`. | **No** |

### 1.4 This is already on the live order path — not just a prospective integration risk

`master_executor.py:127`:

```python
self.vault = global_state.vault
```

BotRunner — the **only** working credential→executor path in the application — reads the
vault from the same global state. So with a mock:

- `master_executor.py:252` `keys = self.vault.load_decrypted_keys(...)` → `None`
- `master_executor.py:255-257` `api_key=keys["api_key"]` → **`TypeError: 'NoneType' object
  is not subscriptable`**, on the live trading path

**The MockEngine exposure is therefore a current property of live trading, not a new risk
the watchdog integration would introduce.** It raises the priority of D6-D above where D6
placed it, and it means fixing the readiness contract benefits the live path too.

Corroborating evidence that the mock path is live in practice: `routers/market.py:56-58`
carries a guard shaped exactly for a `None`-returning vault —
`if not keys or not isinstance(keys, dict): raise ValueError("Invalid or missing keys from
vault")` — and `tests/test_market_none_keys_fix.py:13` encodes a double whose
`load_decrypted_keys` returns `None` "simulating the test environment failure".

### 1.5 Local measurement, and what it does and does not prove

D6 measured, in this workspace: `type(app_state.vault).__name__ == "MockEngine"`,
`hasattr(vault, "load_decrypted_keys") == True`, `hasattr(app_state, "exchange_router") ==
False`. Re-confirmed by hash that the relevant files are unchanged.

**This proves the substitution mechanism is real and reachable. It proves nothing about the
deployed environment**, which has different environment variables, a different import path
and a different wheel set. Per the brief, it is not treated as production evidence.

### 1.6 Deliverable — readiness contract (recommendation, NOT implemented)

A contract that distinguishes a real vault from a fallback mock must assert **identity**,
not capability:

1. **Positive type assertion at startup.** Import `APIKeyVault` explicitly (not through
   `safe_import`) and assert `isinstance(app_state.vault, APIKeyVault)`. Equivalently,
   expose a `vault_ready: bool` that is computed from the type, never from `hasattr`.
2. **Fatal in a deployed environment.** A mock credential vault in a deployed process
   should fail the boot, the same way a placeholder service-role key already does
   (`assert_credentials_are_production_grade`). Silently substituting a no-op for the
   component that decrypts customer API keys is a different class of fallback from
   substituting a telemetry engine.
3. **Validate the key material, not just its presence.** G2 is closed by attempting
   `MultiFernet([Fernet(k) for k in keys])` during the credential check, so a malformed
   key is a boot failure rather than a silent mock.
4. **Surface it.** Add the vault's readiness to `/health/services` alongside
   `order_watchdog`, with the same honesty caveat that endpoint already carries about
   boot-time snapshots.
5. **Prefer explicit injection over global lookup** at every new consumer, so a missing
   vault is a startup fact rather than a per-row `None`.

**Verification required from the platform owner** (safe, no secret exposure): in a deployed
environment, confirm that `type(app_state.vault).__name__` is `APIKeyVault` and that the
warning strings `"Failed to import APIKeyVault"` and
`"APIKeyVault initialization failed, using mock"` are absent from boot logs. Both are
type/log checks and reveal no credential value. Owner: **platform / deployment.**

---

## 2. Phase 2 — D6-C: tenant identity

### 2.1 The two identifiers, and where each is written

| | Value | Written from | Constraint |
|---|---|---|---|
| `exchange_keys.user_id` | `user["id"]` | `routers/exchange.py:429-431` — `vault.store_exchange_keys(user_id=user["id"], exchange_id=body.exchange_id, …)` | **`TEXT`, no foreign key**; RLS `USING (auth.uid()::text = user_id)` (`migrations/003_create_exchange_keys_table.sql:14, 44-45`) |
| `execution_records.tenant_id` | the engine's tenant | `fk_execution_records_tenant_id` → `profiles(id)` (`alembic/versions/add_foreign_keys_20260817.py:87-89`) | `uuid`, FK enforced |

### 2.2 The exact divergence mechanism

`backend_app/core/dependencies.py:315-317`:

```python
user_id = payload.get("sub")
email = payload.get("email", "")
tenant_id = payload.get("tenant_id") or payload.get("app_metadata", {}).get("tenant_id") or user_id
```

and `:323-326`:

```python
user_data = {
    "id": user_id,
    "email": email,
    "tenant_id": tenant_id,
```

So `user["id"]` is **always** the JWT `sub`, while `user["tenant_id"]` is `sub` **only when
neither** a top-level `tenant_id` claim **nor** `app_metadata.tenant_id` is present.

**Therefore: if any deployment issues JWTs carrying a `tenant_id` claim distinct from
`sub`, then `str(execution_records.tenant_id) != exchange_keys.user_id`**, and a watchdog
that loaded credentials by `tenant_id` would query the wrong account or find none. The
fallback chain exists precisely to support that case.

This is a material advance on D6's "unproven, formats match": the mapping is not merely
unverified, there is a **named, reachable code path that breaks it**, and nothing asserts
the invariant that would keep it intact.

### 2.3 Two further reasons the database cannot be relied on to enforce it

- `exchange_keys.user_id` is `TEXT` with **no FK**, so nothing at the schema level ties it
  to `auth.users` or `profiles`. The RLS policy does — but the vault holds a
  **service-role** client (`api_key_vault.py:80`), which **bypasses RLS**. So neither
  control constrains what the vault reads.
- The type boundary is real: `uuid` on one side, `TEXT` on the other. Any comparison must
  be an explicit `tenant_id::text`, and `_validate_id`'s regex
  `^[a-zA-Z0-9\-_]{1,128}$` accepts a dashed UUID string — so a *wrong* identifier of the
  right shape passes validation silently.

### 2.4 Verdict and the verification required

**UNPROVEN. Credentials must not be loaded on this mapping.** Per the brief, format
agreement is not inferred as identity.

Established read-only procedure inspected first, as instructed: the project's PostgreSQL
lane is `staging/bootstrap_pg.py` (+ `staging/dsn.py`, `run_pg_lane.py`), whose `verify`
subcommand is read-only. **No query was run**, for a reason that matters: the only database
reachable here is the local provisioned test cluster, which holds no real connected
exchange accounts, so any result would be vacuous rather than evidence.

**Required from the DB owner — one aggregate query, no identifiers in the output:**

> Against the production database, report three counts only:
> (a) `SELECT count(DISTINCT tenant_id) FROM public.execution_records;`
> (b) the number of those distinct tenants for which a row exists in
> `public.exchange_keys` with `user_id = tenant_id::text`;
> (c) the number for which no such row exists.
> Additionally: the number of issued JWTs, or of `profiles`/`auth.users` rows, whose
> `tenant_id` claim or app-metadata tenant differs from `sub`.
>
> **(c) = 0 and the claim-divergence count = 0** would establish the mapping. Any non-zero
> value refutes it. No user identifier, email or credential should appear in the answer.

If divergence exists, the integration must resolve credentials through an explicit
tenant→account mapping (the `exchange_account_id` handle that
`backend_app/backend/deployment_binding.py:95-98, 879-883` already describes as "the handle
the execution process resolves credentials with … It comes from the account row and from
nowhere else") rather than from `tenant_id` directly. **Owner: DB owner + execution-state
owner.**

---

## 3. Phase 3 — decisions required from owners

Framed only. No answer is selected on any owner's behalf.

### D6-A — credential posture · owner: **security / credential owner + coordinator**

| Question | Evidence the owner needs |
|---|---|
| May the API process load decrypted credentials inside the watchdog's 30-second loop? | `api_key_vault.py:70-71` — the returned dict "must be consumed immediately and never logged, serialised, or stored in any cache." `get_exchange_executor` **caches an executor that retains `_api_key`/`_api_secret` on the instance** (`exchange_executor.py:1081-1083`). Today that happens in the worker process; this would extend it to the API process. |
| How are executor-held credentials released and clients disconnected? | `release_exchange_executor` (`exchange_executor.py:2432`) has **no caller in `backend_app`** — only its own definition. It also only pops the dict entry; it does **not** `await disconnect()`. So `_executors` grows for the process lifetime and keeps decrypted secrets reachable. |
| Who owns the lifecycle of cached executors? | Unassigned. `master_executor.py:886-889` closes only the `ConnectionEngine` bridge. `main.py` has a documented precedent for the *other* pool (`connection_engine.close_all_exchanges()`), and none for `_executors`. |

Note for this decision: a per-tenant loop over ambiguous rows multiplies the number of
distinct decrypted credential sets resident in the API process, and the cache has no
eviction. That is a posture change, not a bug fix.

### D6-B — connection lifecycle · owner: **watchdog owner + execution-state owner**

| Question | Evidence the owner needs |
|---|---|
| Which component owns `connect()` / `disconnect()`? | Construction is synchronous and does zero I/O; `async def connect` (`exchange_executor.py:1271`) does `load_markets` + `fetch_balance`. **Nothing in `backend_app` calls `executor.connect()`** (D6 §2.3). With `_exchange = None`, `find_order_by_client_order_id` degrades to `success=False, status="error"` — fail-safe, useless. |
| Timeout / cancellation / failure behaviour for network I/O in the loop? | The watchdog's 30-second `_monitoring_loop` has no per-call timeout; `_refresh_order_status` uses `2 ** attempt` backoff with `max_retries=3`. A `connect()` against an unreachable venue would occupy the loop. Cancellation on shutdown is `_watchdog_task.cancel()` (`order_watchdog.py:160-171`). |
| Should `_get_executor_for_order` become asynchronous? | D5 deliberately made it synchronous because its body does no I/O, and `tests/test_d5_watchdog_adjudication_repair.py` asserts the invariant **in both directions** precisely so this becomes one conscious change rather than a drift. |
| Which D5 structural tests change if the invariant is revised? | Exactly three, all in `tests/test_d5_watchdog_adjudication_repair.py`: `test_the_executor_lookup_is_synchronous_and_no_call_site_awaits_it`, `test_every_awaited_self_method_in_the_module_is_actually_a_coroutine`, and — if the persist helper is touched — `test_the_persist_helper_exists_is_synchronous_and_is_not_awaited`. The 15 behaviour tests are unaffected, because they inject the executor. |

---

## 4. Phase 4 — adjacent vault-call audit

### 4.1 Correction to D6: three sites, not four

Measured against the current signature `load_decrypted_keys(self, user_id: str,
exchange_id: str) -> dict` (`api_key_vault.py:177`) — **two positional parameters, no
`access_token`, no `**kwargs`**:

| Call site | Passes `access_token=`? | Owner |
|---|---|---|
| `backend_app/routers/orders.py:789-793` | **YES** | router owner |
| `backend_app/routers/orders.py:1095-1099` | **YES** | router owner |
| `backend_app/routers/market.py:50-54` | **YES** | router owner |
| `backend_app/routers/exchange.py:993-996` | **no** — two args | — |
| `backend_app/api_ws/ws_routes.py:814` | **no** — two args | — |
| `backend_app/backend/master_executor.py:252` | no | — |
| `backend_app/backend/portfolio_cache_updater.py:161` | no | — |

**D6 listed `api_ws/ws_routes.py:814` among the four. That was wrong** — it calls
`vault.load_decrypted_keys(user_id, exchange_id)` with two arguments. The third genuine site
is `routers/market.py:50`. The mismatch **is still present** at the three sites above.

Against a real `APIKeyVault` each is a `TypeError`. Against the mock, `__getattr__`'s noop
accepts any signature and returns `None` — which is why `market.py:56-58` needs its
`if not keys or not isinstance(keys, dict)` guard at all.

**No test covers the real-vault path for any of the three.** Every test touching them uses a
vault double, and `tests/test_market_none_keys_fix.py:13`'s double declares
`load_decrypted_keys(self, user_id, exchange_id, access_token=None)` — i.e. the *double*
matches the call sites while the real class does not. A passing test over that double is not
evidence of compatibility with the real implementation. **Not fixed here, per scope.**

### 4.2 New finding — a background credential path with a non-existent import

`backend_app/backend/portfolio_cache_updater.py:151`:

```python
from backend_app.backend.key_manager import KeyVault
```

**`backend_app/backend/key_manager.py` does not exist** (`Test-Path` → False), and this is
the **only** reference to `key_manager` anywhere in `backend_app`. The import sits at line
151, *above* the `try:` at line 158, so the `ImportError` is not caught by the handler that
follows it. `PortfolioCacheUpdater` is started from the lifespan
(`main.py` — `start_portfolio_cache_updater()`, reported `ACTIVE`).

This matters for the integration plan beyond its own severity: it was the **only candidate
precedent** for a background loop constructing a vault directly rather than reading
`app_state` — the pattern that would have side-stepped the MockEngine problem entirely. It
is not a working precedent. **Every working credential path in the application reads the
global state** (`master_executor.py:127`), and all of them are exposed to the silent
substitution in §1. **Owner: portfolio-cache / platform owner.**

---

## 5. Phase 5 — dependency graph and parallel work

### 5.1 Read-only or already-authorised — can proceed now, in parallel

| # | Task | Owner | Depends on |
|---|---|---|---|
| **P1** | **W-3** — teach `_find_stale_orders`, `_find_open_orders` and `event_router._find_execution_by_order_id` to read `venue_order_id` (coalesce, never swap: `order_id` carries the FK to `orders(id)`, `venue_order_id` deliberately does not) | watchdog + execution-state | nothing. Cleared on its own merits in D5 §8 |
| **P2** | Platform check of §1.6 — vault type and boot-log strings in a deployed environment | platform | nothing |
| **P3** | DB owner's aggregate query, §2.4 | DB owner | nothing |
| **P4** | Fix the three `access_token=` call sites, §4.1, with a test that exercises the **real** signature | router owner | nothing |
| **P5** | Resolve the `key_manager` import, §4.2 | portfolio-cache owner | nothing |

### 5.2 Blocked on owner decisions

| Task | Blocked by |
|---|---|
| Readiness-contract implementation (§1.6) | a decision that a mock credential vault should be **fatal** in a deployed environment — a startup-behaviour change |
| Watchdog credential loading | **D6-A** |
| Watchdog executor connection; possible async contract revision | **D6-B** |

### 5.3 Blocked on authoritative evidence

| Task | Blocked by |
|---|---|
| Any credential load keyed on `tenant_id` | **D6-C** (§2.4). Must not proceed on an unverified mapping |
| Declaring W-1b fixed | **D6-D** (§1.6). A mock vault makes the integration inert *and* already endangers the live path (§1.4) |

### 5.4 Requires integration + regression verification after approval

The two-file integration from D6 §3.1 (`order_watchdog.py`, `main.py`) plus the twelve
focused tests in D6 §3.4, and the three D5 structural tests re-settled in whichever
direction D6-B chooses.

### 5.5 Smallest approved path to complete W-1b

```
P2 (vault type in deployment)  ─┐
P3 (aggregate identity query)  ─┼─→ D6-A ─→ D6-B ─→ two-file integration ─→ 12 tests + D5 re-run
                                │
            (both read-only, parallel, no code change)
```

Gates in order: **D6-D → D6-C → D6-A → D6-B → implement.** The first two are read-only
evidence requests that can run today and in parallel; only after both can the two
credential/lifecycle decisions be taken on facts rather than assumptions.

### 5.6 Downstream status — unchanged

- **W-3 may proceed independently, and does NOT establish end-to-end recovery while W-1b is
  open.** It restores *which rows are selected*; W-1b decides *whether the venue can be
  asked about them*. W-3 alone yields more rows correctly refused and none resolved.
- **W-4 before W-5**, preserved: widening `classify_stale_executions` before an explicit
  `UNKNOWN` exit rule would select rows into a state nothing can resolve.
- **W-6 remains visible and open**: `_handle_ccxt_error`'s substring ladder places
  `symbol|market` above `network|timeout|connection`, so a ccxt timeout whose message
  embeds `symbol=` is classified `InvalidSymbolError` — a timeout stored as a determinate
  rejection, upstream of every consumer.

---

## 6. Blocker register after D7

| ID | Blocker | Sev | Owner | Status |
|---|---|---|---|---|
| **D6-D** | No guarantee `app_state.vault` is a real vault; three silent substitution paths; **already on the live order path** (§1.4) | **P0** | platform | evidence requested (P2) |
| **D6-C** | `tenant_id` → vault `user_id` unproven, with a named divergence path at `dependencies.py:316` | **P0** | DB owner | evidence requested (P3) |
| **D6-A** | Decrypted credentials in the API background loop; `_executors` never drained; no teardown owner | **P0** | security + coordinator | decision pending |
| **D6-B** | Who connects/disconnects; timeout and cancellation; sync-vs-async contract | **P0** | watchdog + execution-state | decision pending |
| **W-1b** | Watchdog cannot obtain a usable executor | **P0** | — | **OPEN**, gated on the four above |
| **W-3** | `order_id` never written; two selectors and the fill path match nothing | P0 | watchdog + execution-state | cleared to proceed |
| **W-4** | Nothing exits `UNKNOWN`; the DUP-02 guard refuses it forever | P0 | execution-state + coordinator | open, before W-5 |
| **W-6** | Timeout relabelled as a determinate rejection | P0 | execution-state + coordinator | open |
| **A-6** | `.env.example` `ALERT_*` names have no consumer; `get_alert_system()` reads different names | P0 | platform | open |
| **D7-1** | Three `access_token=` call sites incompatible with the real vault signature; no real-vault test | P1 | router owner | open (P4) |
| **D7-2** | `portfolio_cache_updater.py:151` imports a module that does not exist | P1 | portfolio-cache owner | open (P5) |
| **D7-3** | `MASTER_ENCRYPTION_KEYS` absent from `validate_required()`; covered only by the deployed-only ENV-03 check | P2 | platform | open |
| **D7-4** | A present-but-malformed Fernet key passes the credential check and yields a mock vault (G2) | P1 | platform | open |

---

## 7. Preservation

| Check | Result |
|---|---|
| Source / test / migration / CI / deployment files modified | **0** |
| Files written | **1** — this report |
| Candidate file hashes | all six **identical to D6** ✔ |
| Staged index | 22 entries, digest matches D6 ✔ |
| `HEAD` / branch / divergence | `074b772a…` / `main` / `0 0` — unchanged ✔ |
| Staged, committed, pushed, branch changed | **none** |
| Database query / mutation | **none** |
| Live venue contacted | **none** |
| Secrets read, printed or copied | **none** — variable names and reader code only |
| Existing staged / unstaged / untracked work | preserved ✔ |

---

**W-1b OPEN** · a real vault is **not** guaranteed at startup and the exposure already
reaches the live order path · tenant identity **unproven** with a named divergence path ·
two credential/lifecycle decisions pending with named owners · 5 tasks can proceed in
parallel today · 2 new findings (D7-1 correction, D7-2) · 0 files modified · **live trading
OFF / NOT CERTIFIED**.
