# Phase 11B-D6 — W-1b Executor Dependency Investigation

**READ-ONLY INVESTIGATION. NO FILE WAS MODIFIED.** Stopped at the Phase 2 authorization
gate: the integration requires changes outside the approved scope. **W-1b remains OPEN.**

**Live trading remains OFF / NOT CERTIFIED.**

## Headline

A supported executor factory **does** already exist — `get_exchange_executor()` in
`backend_app/backend/exchange_executor.py:2363`, with a per-tenant cache, an allowlist, a
safety block list and one production caller. The watchdog was reaching for two names
(`app_state.exchange_router.get_executor`, `app_state.vault.get_exchange_client`) that
were never built.

But wiring the watchdog to the real factory is **not sufficient**, and D5's single-blocker
framing of W-1b was incomplete. There are **three independent blockers**, and two of them
would survive the obvious fix:

| | Blocker | Would adding the missing method fix it? |
|---|---|---|
| **W-1b-1** | The watchdog is not wired to the real factory | yes |
| **W-1b-2** | `app_state.vault` is a `MockEngine` whose `__getattr__` makes **every** credential call silently return `None`, and `hasattr` cannot detect it | **no** |
| **W-1b-3** | Executors are constructed **unconnected** and nothing in the application ever calls the async `connect()`; `find_order_by_client_order_id` needs `self._exchange` | **no** |

---

## 1. Phase 0 — rebaseline and preservation

| Property | D5 reference | **Measured now** | Moved? |
|---|---|---|---|
| Branch | `main` | `main` | no |
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | **no** |
| `origin/main...HEAD` | `0 0` | `0 0` | no |
| Worktrees | 1 | **1** | no |
| Staged entries | 22 | **22** | no |
| Canonical staged digest | `2e994018…7152` | **`2e994018e375f318f4e6dc9a67ab248e4622de8c426146ca756d5f47d2767152`** | **no** |

Candidate-file hashes, taken at Phase 0 and **re-verified identical at the end**:

| File | SHA-256 (16) | Unchanged |
|---|---|---|
| `backend_app/backend/order_watchdog.py` | `507F5C85A7984200` | ✔ (D5's build) |
| `backend_app/core/state.py` | `6AC55227B378EE5E` | ✔ |
| `backend_app/backend/exchange_executor.py` | `81ED5679BB93AFB0` | ✔ |
| `backend_app/core/execution_engine.py` | `E1D65AA0E60868A4` | ✔ |
| `backend_app/core/models/execution_record.py` | `DE6C95CE08AEF318` | ✔ |
| `backend_app/backend/api_key_vault.py` | `FE42F4F3E63BF417` | ✔ |
| `backend_app/main.py` | `DE1CCF9ADFFFBA0C` | ✔ |
| `tests/test_d5_watchdog_adjudication_repair.py` | `AB918192B4801F13` | ✔ |

No concurrent change to any candidate file. No ownership conflict. The only writes this
phase made anywhere are this report and the read-only evidence file
`staging/p11bd6_verify.txt`.

---

## 2. Phase 1 — findings

### 2.1 Evidence table

| Question | Evidence |
|---|---|
| **Where are executors created today?** | `get_exchange_executor()` — `exchange_executor.py:2363`, synchronous `def`, keyword-only mandatory `user_id`. Delegates to `ExchangeExecutorFactory.create()` (`:2271`) → `CCXTExchangeExecutor(...)`. Cached in the module-global `_executors` (`:2323`) under `_executor_cache_key()` (`:2326`). **One production caller: `BotRunner._recover_live_state`, `master_executor.py:296`.** `ExchangeExecutorFactory.create` is the only `CCXTExchangeExecutor(...)` construction in application code. |
| **How does the vault supply credentials?** | `APIKeyVault.load_decrypted_keys(user_id, exchange_id)` — `api_key_vault.py:177`. Two positional args, **no `access_token` parameter**. Returns exactly `{"api_key", "secret_key", "password"}`; `password` is `None` when unset. Raises `ValueError(f"No keys found for {user_id}/{exchange_id}.")` on an empty result. Validation is `_validate_id` only (`:44`, regex `^[a-zA-Z0-9\-_]{1,128}$`). **Note the key-name mismatch the factory needs bridged:** the vault returns `secret_key`, the factory takes `api_secret`; `master_executor.py:299-302` does that mapping. |
| **How is tenant ownership enforced?** | There is no dedicated ownership guard between auth and credential load. Three layered controls: the vault's `.eq("user_id", user_id)` filter plus RLS `USING (auth.uid()::text = user_id)` (`migrations/003_create_exchange_keys_table.sql:44-45`); the executor cache's mandatory keyword-only `user_id` folded into a credential-salted digest (`:2326-2361`) with a belt-and-braces `cached._api_key == api_key` re-check on hit (`:2404-2417`); and `ConnectionEngine`'s separate `_pool_key = sha256([user_id, exchange_id])`. Tests: `tests/test_exchange_executor_cache_is_per_tenant.py:55-95` (*"Trader B's orders would be signed with trader A's credentials"*), `tests/test_exchange_phase6c_adversarial_acceptance.py:31-50` (IDOR), `tests/test_exchange_safety_fix.py:252-274` (pool isolation). |
| **How does the application inject dependencies?** | `AppState` (`core/state.py:88`) is a dataclass with exactly `vault, telemetry, risk, alert, fleet, ws`, built at import time. `core/dependencies.py:659-691` is the whole engine DI surface — six two-line accessors returning `app_state.X`. **No runtime assignment onto `app_state` exists in product code** (only tests), so `hasattr(app_state, "exchange_router")` is permanently False. `OrderWatchdog` is constructed at `main.py:431` with exactly `(SessionLocal(), WatchdogConfig(...))`, and a failure is swallowed into `service_status["order_watchdog"] = "INACTIVE"` with a warning. |
| **What is missing?** | `ExchangeRouter`, `exchange_router`, `get_executor`, `get_exchange_client`, `create_executor`, `build_executor`, `make_executor`, `executor_for`, `ExchangeManager` — **zero definitions anywhere in the tree.** The only occurrences of the three the watchdog uses are the two unresolvable lines in `order_watchdog.py` and that method's own docstring. |
| **What is the smallest safe integration?** | Reuse `get_exchange_executor`, fed by `APIKeyVault.load_decrypted_keys`, with an **injected** vault and factory rather than `app_state` lookup. Candidate files: `backend_app/backend/order_watchdog.py` (watchdog owner) and `backend_app/main.py` (startup owner). Rationale and the four unresolved questions: §3. |
| **What lifecycle risks exist?** | Construction is **synchronous** and does zero I/O. Connecting is **async and does network I/O** (`async def connect`, `:1271` — `load_markets` then `fetch_balance`). **Nothing in `backend_app` ever calls `executor.connect()`** (verified: every `.connect()` hit is `ConnectionEngine`, websockets, the event publisher/listener, or alembic). `release_exchange_executor` (`:2432`) has **no caller in `backend_app`** — only its own definition — so `_executors` is never drained and holds decrypted credentials for the process lifetime. It also only pops the dict entry; it does not `await disconnect()`. |
| **What regression coverage is needed?** | §4. |

### 2.2 W-1b-2 — the MockEngine finding, and why it changes the diagnosis

`core/state.py:67`:

```python
APIKeyVault = safe_import("APIKeyVault", "api_key_vault")
```

`safe_import` catches **every** exception and returns `lambda *a, **k: MockEngine(f"Mock{class_name}")`. `MockEngine` (`state.py:45-57`):

```python
def __getattr__(self, name):
    """Return a no-op function for any method call"""
    def noop(*args, **kwargs):
        return None
    return noop
```

**Measured in this environment:**

```
type = MockEngine
has load_decrypted_keys = True
has exchange_router attr on app_state = False
```

So `app_state.vault` is a mock, `hasattr(vault, "load_decrypted_keys")` answers **True**
for a method that does not exist, and the call returns `None`. Consequences:

1. **`hasattr` is useless as a readiness probe on `app_state`.** Any guard of the form
   `if hasattr(app_state, "vault") and app_state.vault:` passes against the mock.
2. **The originally-intended code would also have failed silently.** Even if
   `get_exchange_client` had been implemented, `MockEngine.__getattr__` would have
   returned a noop returning `None`. The watchdog's `elif` branch would "succeed" and hand
   back `None`. **D4/D5 attributed the failure to the missing definition; that is correct
   but incomplete — the mock is a second, independent cause that the obvious fix does not
   touch.**
3. **A naive integration crashes on a `TypeError`.** `keys = vault.load_decrypted_keys(...)`
   returns `None`; `None["api_key"]` raises; the watchdog's `except` converts it to `None`
   and the row is refused. Fail-safe, but indistinguishable from "this tenant has no keys".
4. `MockEngine` is reached whenever `api_key_vault` import fails — and that import requires
   `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` and `MASTER_ENCRYPTION_KEYS`, all of which
   `APIKeyVault.__init__` treats as fatal (`api_key_vault.py:73-91`). **Whether a given
   deployment has a real vault or a mock is NOT ESTABLISHED** and is an environment
   question, not a code one.

Related, not this phase's to fix: `routers/orders.py:789`/`:1095`, `routers/market.py:50`
and `api_ws/ws_routes.py:814` all call `load_decrypted_keys(..., access_token=...)`, a
keyword the real signature does not accept. Against a real vault that is a `TypeError`;
against the mock it silently returns `None`. `tests/test_market_none_keys_fix.py:13`
encodes exactly that shape. Flagged for the router/vault owner.

### 2.3 W-1b-3 — executors are built unconnected, and nothing connects them

`get_exchange_executor` → `ExchangeExecutorFactory.create` → `BaseExchangeExecutor.__init__`
performs **no I/O**: an allowlist/block-list check, attribute assignment, a `RateLimiter`
and an `ExchangeNormalizer`, then `self._exchange = None` and `self._connected = False`
(`exchange_executor.py:1079-1113`). That is *why* D5 could legitimately leave
`_get_executor_for_order` synchronous.

Connecting is a different matter — `async def connect` (`:1271`) builds the ccxt client,
runs `verify_sandbox_endpoint`, then `await load_markets(...)` and
`await fetch_balance()` before setting `_connected = True`.

**No application code calls it.** Even BotRunner's live branch connects a *separate*
`ConnectionEngine` bridge (`master_executor.py:259`) and never awaits
`exchange_exec.connect()`. So the cached executor handed to `ExecutionEngine` has
`_exchange = None`.

Traced through `find_order_by_client_order_id` (`:1768`) with `_exchange = None`:

1. `await self._exchange.fetch_order(...)` → `AttributeError` → caught by the method's own
   `except`, logged "not conclusive", falls through;
2. `getattr(self._exchange, "fetch_open_orders", None)` → `getattr(None, …)` → `None` →
   `continue`; same for `fetch_closed_orders`;
3. `reachable` stays `False` → returns `success=False, status="error"`.

The watchdog then records `UNKNOWN` and alerts. **Fail-safe — no resubmission, no invented
fill — but adjudication still resolves nothing.** So W-1b cannot be closed by construction
alone; the integration must also connect the client, which is async and does network I/O
inside the API process's 30-second loop.

### 2.4 The identity question — the one unresolved data fact

| Column | Type | Constraint |
|---|---|---|
| `execution_records.tenant_id` | `uuid` | `fk_execution_records_tenant_id` → `profiles(id)` (`alembic/versions/add_foreign_keys_20260817.py:87-89`) |
| `exchange_keys.user_id` | **`TEXT`** | RLS `USING (auth.uid()::text = user_id)` (`migrations/003_create_exchange_keys_table.sql:14, 44-45`) |

Both ultimately denote the Supabase auth user, so `str(order.tenant_id)` is the *plausible*
vault `user_id`, and the vault's `_validate_id` regex accepts a dashed UUID string.

**But no existing code or test crosses that boundary**, because nothing today loads vault
credentials from an `execution_records` row. BotRunner takes `user_id` from its own
constructor (`master_executor.py:91`), never from a database row. **This is unproven and it
decides whose credentials get loaded, so it must be verified before any integration — not
assumed.** Evidence needed: a query proving, on a real database with real connected
accounts, that every `execution_records.tenant_id` has a matching
`exchange_keys.user_id = tenant_id::text`.

### 2.5 Approaches compared

| Approach | Verdict |
|---|---|
| **A. Add `get_exchange_client` to `APIKeyVault`** | **Reject.** Adds a vault method that returns a credential-bearing client, widening the vault's contract past "decrypt and hand back, consumed immediately". Does not address W-1b-2 — `MockEngine` answers it as a noop anyway — and duplicates `get_exchange_executor`'s cache/allowlist/identity logic. The brief prefers reuse over a new vault method; this is the new vault method. |
| **B. Build an `ExchangeRouter` and attach it to `app_state`** | **Reject.** Requires a new `AppState` field, a runtime assignment pattern that **does not exist in product code anywhere**, and a new abstraction over a factory that already works. Largest blast radius, and inherits W-1b-2 by construction. |
| **C. Inject a vault + factory into `OrderWatchdog` at construction, reusing `get_exchange_executor`** | **Recommended.** Reuses the established factory, its per-tenant cache, its allowlist and its block list. Makes the dependency explicit instead of reaching into a global whose mock cannot be detected, so a missing vault is a startup fact rather than a silent per-row `None`. Changes two files. Testable with injected stubs. **Still requires authorization** — §3. |
| **D. Leave W-1b open; the watchdog refuses and alerts** | **The current state.** Safe, and the honest baseline if authorization is withheld. Ambiguous rows become `UNKNOWN` with a `critical` log, never resubmitted. |

Approach C was chosen on architecture, not on testability: it is the only option that adds
no new abstraction and no new credential surface. It was **not** selected because it makes a
test pass — no test for it has been written, because no code was written.

---

## 3. Phase 2 — authorization gate: STOPPED

The integration cannot be completed within this phase's authorization. Four decisions are
required, three of which cross owners.

### 3.1 Recommended integration path (NOT implemented)

1. Add two optional constructor parameters to `OrderWatchdog` — a credential loader and an
   executor factory — defaulting to `None`. With both `None` the behaviour is exactly
   today's: refuse, record `UNKNOWN`, alert. **No behaviour change unless startup wires
   them.**
2. `_get_executor_for_order` uses the injected loader/factory when present, mapping
   `secret_key` → `api_secret` and passing `user_id=str(order.tenant_id)` **only after
   §2.4 is resolved**. It keeps `assert_exchange_routable` and the allowlist by going
   through `get_exchange_executor`, and must **not** fall back to `"binance"` when
   `order.exchange_id` is NULL — an unknown venue must refuse, not guess.
3. Startup (`main.py`) wires the real vault **only if it is a real vault**, using a positive
   type/capability check rather than `hasattr` (§2.2 item 1). If it is a `MockEngine`, log
   once at startup and leave the watchdog in refuse-only mode.
4. Connecting the client (W-1b-3) is decided separately — see 3.3.

### 3.2 Files that would need modification

| File | Change | Owner |
|---|---|---|
| `backend_app/backend/order_watchdog.py` | two optional constructor params; `_get_executor_for_order` body | watchdog owner |
| `backend_app/main.py` | wire the loader/factory in the lifespan, with a real-vault check | startup / platform owner |
| `tests/test_d6_watchdog_executor_integration.py` (new) | the coverage in §4 | Stream D |
| *(none)* | `exchange_executor.py` and `api_key_vault.py` are **reused unchanged** | — |

### 3.3 Cross-owner decisions required

| # | Decision | Owner | Why it cannot be taken here |
|---|---|---|---|
| **D6-A** | **May the API process load decrypted exchange credentials in a 30-second background loop?** | security / credential owner **+ coordinator** | `load_decrypted_keys`' own docstring says the returned dict "must be consumed immediately and never logged, serialised, or stored in any cache" — and `get_exchange_executor` caches an executor that **holds** `_api_key`/`_api_secret` on the instance. That is already true for the worker process; this extends it to the API process. `release_exchange_executor` has **no caller**, so `_executors` is never drained. This is a credential-posture change, not a bug fix. |
| **D6-B** | **Who connects the executor, and does `_get_executor_for_order` become async?** | watchdog owner **+ execution-state owner** | W-1b-3. Either the call sites `await executor.connect()` (network I/O in the watchdog loop, with its own failure and timeout semantics), or `_get_executor_for_order` becomes `async def` and D5's invariant is deliberately inverted. D5's structural test asserts both halves precisely so this is a conscious, single change. A third option — a connect-on-demand guard inside `find_order_by_client_order_id` — would edit `exchange_executor.py`, which is forbidden here. |
| **D6-C** | **Is `str(execution_records.tenant_id)` the vault's `user_id`?** | DB owner / data owner | §2.4. Unproven, and it decides whose credentials are loaded. Must be measured, not assumed. |
| **D6-D** | **Is `app_state.vault` a real `APIKeyVault` in the deployed environment?** | platform / deployment owner | §2.2. It is a `MockEngine` here. If it is also a mock in production, the integration is inert and the `MockEngine` fallback for a **credential vault** is itself a finding worth escalating. |

### 3.4 Minimal test plan (for when authorization exists)

All with injected stubs; no venue, no network.

1. Successful construction — stub loader returns the three keys, stub factory records its
   arguments; assert `user_id` is the row's tenant, `api_secret` came from `secret_key`, and
   the executor is returned.
2. Missing credentials — loader raises `ValueError("No keys found…")` → returns `None`, row
   refused as `UNKNOWN`, **no resubmission**.
3. Mock/absent vault — loader returns `None` (the `MockEngine` shape) → returns `None`
   rather than raising `TypeError`, row refused.
4. Unsupported venue — `exchange_id="ftx"` → `ValueError` from the allowlist → refused.
5. Blocked venue — `exchange_id="bitget"` → `ExchangeRoutingBlocked` → refused.
6. NULL `exchange_id` → refused, and specifically **not** silently routed to `"binance"`.
7. Tenant isolation — two rows for two tenants produce two different executors; assert the
   second does not carry the first's credentials (mirrors
   `tests/test_exchange_executor_cache_is_per_tenant.py`).
8. Factory raises → caught, returns `None`, refused.
9. No credential value appears in any log record (extend
   `tests/test_exchange_credential_log_redaction.py`'s approach).
10. Lifecycle — whichever of D6-B is chosen, assert the client's connected state before
    `find_order_by_client_order_id` is called.
11. The no-resubmission invariant re-asserted with the stub executor whose order-placing
    methods raise, as in D5.
12. D5's 18 tests still pass, including the structural invariant in whichever direction
    D6-B settles.

### 3.5 Unresolved credential / tenant / lifecycle questions

- **Credential:** is a real vault present in production (D6-D)? May credentials be cached
  in the API process (D6-A)? Who drains `_executors`, given `release_exchange_executor` has
  no caller?
- **Tenant:** does `tenant_id` map to `user_id` (D6-C)? Is `execution_records.exchange_id`
  reliably populated — `mark_submitted` writes it from the executor, so it should be on the
  live path, but the `or "binance"` fallback in the watchdog is a latent mis-routing hazard.
- **Lifecycle:** who connects (D6-B)? Who disconnects? `ExchangeExecutorFactory.create`
  enforces the allowlist but `BaseExchangeExecutor.__init__` checks only the **block** list
  and keeps `exchange_id` un-normalised (`:1079`), so direct construction is not
  allowlist-checked — the integration must go through the factory, not the class.

---

## 4. Phase 4 — verification and preservation

No code changed, so this is a preservation check plus confirmation that D5 is intact.

```
$env:AERORA_TEST_DATABASE_URL = (Get-Content staging/pg_dsn.txt -Raw).Trim()
.venv\Scripts\python.exe -m pytest `
  tests/test_d5_watchdog_adjudication_repair.py `
  tests/test_exchange_executor_cache_is_per_tenant.py `
  tests/test_paper_live_separation.py `
  -q --no-header --tb=short -rfs --timeout=600
```

**EXIT=0 — 51 passed, 0 failed, 0 skipped** (83.5 s) · `staging/p11bd6_verify.txt`

- **D5's 18 tests: intact and passing.** The suite file is hash-unchanged (`AB918192B4801F13`).
- PostgreSQL tests **did run** — `AERORA_TEST_DATABASE_URL` pointed at the provisioned
  cluster and `test_persist_is_durable_on_postgresql` was not skipped. The cluster was not
  started, stopped or re-pointed; `pg_dsn.txt` was not rewritten.
- Executor tenant-isolation and paper/live separation suites pass, so nothing in this
  investigation disturbed the credential-isolation controls.

Preservation:

| Check | Result |
|---|---|
| Files modified by D6 | **0 source files.** Only this report and `staging/p11bd6_verify.txt` |
| All eight candidate files | hash-identical to Phase 0 ✔ |
| Staged index | 22 entries, digest `2e994018…` — unchanged ✔ |
| `HEAD` | `074b772a…` on `main`, `0 0` — unchanged ✔ |
| Anything staged | **no** — `git add` never invoked |
| Commit / push / migration / branch / PR | **none** |
| Live venue contacted | **none** |
| Database-wide mutation | **none** — read-only queries only |
| Unrelated work overwritten or absorbed | **none** |

**Nothing here is production venue verification.** All executor findings are read from
application source and from installed ccxt 4.3.92; no venue was contacted.

---

## 5. Acceptance criteria — W-1b verdict

| Criterion | Status |
|---|---|
| Production dependency path verified | **PARTIAL** — the factory is identified and verified to exist; the credential source and identity mapping are not verified |
| Watchdog obtains an executor through the supported architecture | **NO** — not wired; not authorised to wire |
| Focused integration tests pass | **N/A** — no integration written |
| Credential / tenant / lifecycle behaviour covered | **NO** — four open decisions (§3.3) |

### **W-1b: OPEN.**

**Exact blockers, in the order they must be cleared:**

1. **D6-D** — establish whether a real `APIKeyVault` exists in the deployed environment.
   It is a `MockEngine` here, and `hasattr` cannot tell the difference. *(platform)*
2. **D6-C** — prove `str(execution_records.tenant_id)` is the vault's `user_id`. *(DB owner)*
3. **D6-A** — authorise, or refuse, decrypted credentials in the API process's background
   loop, and name an owner for draining `_executors`. *(security + coordinator)*
4. **D6-B** — decide who connects the client and whether `_get_executor_for_order` becomes
   async. *(watchdog + execution-state)*
5. Then the two-file integration in §3.1 and the twelve tests in §3.4.

## 6. Downstream status, unchanged

- **W-3 is NOT end-to-end recovery while W-1b is open.** It restores *which rows are
  selected*; W-1b decides *whether the venue can be asked about them*. W-3 alone produces
  more rows correctly refused and none resolved. It remains safe to implement and is still
  cleared on its own merits (D5 §8).
- **W-4 before W-5**, unchanged: widening `classify_stale_executions` before an explicit
  `UNKNOWN` exit rule would select rows into a state nothing can resolve.
- **W-6** (the `_handle_ccxt_error` substring ladder relabelling a timeout as a determinate
  rejection) is unaffected and still open.

## 7. New findings this phase contributed

| # | Finding | Severity |
|---|---|---|
| 1 | `app_state.vault` is a `MockEngine`; `__getattr__` answers any name with a noop returning `None`, so `hasattr` is not a usable readiness probe and the originally-intended `get_exchange_client` path would have failed silently too | **P0** |
| 2 | Executors are constructed unconnected and **nothing in `backend_app` calls `executor.connect()`**; `find_order_by_client_order_id` degrades to `status="error"` — safe but useless | **P0** |
| 3 | `release_exchange_executor` has **no caller**, so `_executors` retains decrypted credentials for the process lifetime and is never drained; it also does not `await disconnect()` | **P1** |
| 4 | `tenant_id` → vault `user_id` is unproven and crosses a type boundary (`uuid` vs `TEXT`) | **P1** |
| 5 | `routers/orders.py:789,1095`, `routers/market.py:50`, `api_ws/ws_routes.py:814` pass `access_token=` to `load_decrypted_keys`, which has no such parameter — a `TypeError` against a real vault, silent `None` against the mock | **P1** |
| 6 | `BaseExchangeExecutor.__init__` enforces only the **block** list, not the allowlist, and keeps `exchange_id` un-normalised; the allowlist lives solely in `ExchangeExecutorFactory.create` | **P2** |
| 7 | The watchdog's `getattr(order, "exchange_id", None) or "binance"` default would route an unknown-venue row to binance | **P2** |

---

W-1b **OPEN** · 3 independent blockers, 2 of which survive the obvious fix · 4 cross-owner
decisions required · **0 source files modified** · D5's 18 tests intact and passing · 51
tests passed with 0 skips, PostgreSQL included · no venue contacted · no credential,
allowlist, CCXT or live-trading change · no migration or data mutation · no commit, push or
staging · shared `HEAD` and the 22-entry staged index preserved byte-for-byte · **live
trading OFF / NOT CERTIFIED**.
