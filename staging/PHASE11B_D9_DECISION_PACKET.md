# Phase 11B-D9 — Decision Packet (ready to send)

**0 source/test files modified. Tests run: NONE.** Baseline: `main` @
`074b772ab16cece62cbfca0f2b78497be2ae56d5`, `0 0` vs `origin/main`, 1 worktree, 0 unmerged,
22 staged entries / digest `2e994018…7152` unchanged. All six D8 fingerprints match, so D8's
findings are reused without re-scanning.

**Authorization landscape, confirmed from the newest register and then closed:**
`staging/PHASE11B_A25_LAUNCH_EXECUTION_DASHBOARD.md` §7.1 — "Approvals — all withheld, none
newly granted", "**Total granted: 0.** Three independent repository-wide sweeps across three
sessions", "Approvals granted this phase: 0". No further grant scanning performed.

**Live trading OFF / NOT CERTIFIED. W-1b OPEN. TENANT-01 and QUOTA-01 OPEN.**

---

## Packet 1 → Platform / Deployment owner

**Type: observational evidence only. No approval requested. No code change requested.**

Closes **D6-D**, which currently blocks W-1b *and* already affects the live order path
(`master_executor.py:127` reads the same `global_state.vault`; with a mock,
`keys["api_key"]` at `:255-257` raises `TypeError` on the live path).

> In each deployed environment (staging and production), report exactly three values:
>
> 1. `type(app_state.vault).__name__` — expected `APIKeyVault`. `MockEngine` means the
>    credential vault was silently replaced by a no-op.
> 2. Does the boot log contain the string `Failed to import APIKeyVault`? (yes/no)
> 3. Does the boot log contain the string `APIKeyVault initialization failed, using mock`?
>    (yes/no)
>
> **Do not report, copy or print any credential value, environment-variable value, user
> identifier or email.** All three are type/log observations and expose no secret.

**Minimum response:** one type name + two booleans, per environment.
**Unblocks on response:** D6-D closes; D6-A can then be decided on fact; W-1b gate 1 of 4.

---

## Packet 2 → Database owner

**Type: observational evidence only. Query prepared, NOT executed.**

Partially closes **D6-C**. Counts only — no identifiers.

```sql
-- (a) distinct execution tenants
SELECT count(DISTINCT tenant_id) AS distinct_tenants
  FROM public.execution_records;

-- (b)/(c) tenants with and without a matching credential row
SELECT count(*) FILTER (WHERE k.user_id IS NOT NULL) AS tenants_with_keys,
       count(*) FILTER (WHERE k.user_id IS NULL)     AS tenants_without_keys
  FROM (SELECT DISTINCT tenant_id FROM public.execution_records) e
  LEFT JOIN public.exchange_keys k
         ON k.user_id = e.tenant_id::text;
```

**Minimum response:** three integers. `tenants_without_keys = 0` is **necessary but not
sufficient** — see Packet 3.
**Not run locally, deliberately:** the only reachable database is the provisioned test
cluster with no real connected accounts, so the result would be vacuous.

---

## Packet 3 → Auth / Platform owner (distinct from Packet 2)

**Type: observational evidence only.**

The remaining half of **D6-C**, and it is **not measurable from the application tables**.
The divergence originates at `backend_app/core/dependencies.py:316`:

```python
tenant_id = payload.get("tenant_id") or payload.get("app_metadata", {}).get("tenant_id") or user_id
```

while `exchange_keys.user_id` is written from `user["id"]` = JWT `sub`
(`routers/exchange.py:429-431`). A claim is not a table.

> Report one count, from the Supabase auth configuration or `auth.users.raw_app_meta_data`:
> the number of identities whose issued-token `tenant_id` claim (top-level or in
> `app_metadata`) **differs from** `sub`. **No identifiers, emails or token contents.**

**Minimum response:** one integer. **0** is required for the mapping to hold.
**Unblocks on response (with Packet 2):** D6-C closes; W-1b gate 2 of 4. Until then,
**credentials must not be loaded keyed on `tenant_id`.**

---

## Packet 4 → Security / Credential owner + Coordinator — **D6-A**

**Type: implementation approval + policy decision. Five questions.**

1. May decrypted credentials enter the API background process at all?
2. How long may they remain resident? `get_exchange_executor` caches an executor retaining
   `_api_key`/`_api_secret` (`exchange_executor.py:1081-1083`), against the vault's own
   docstring: the returned dict "must be consumed immediately and never logged, serialised,
   or stored in any cache" (`api_key_vault.py:70-71`).
3. How are cached executors' credential references released?
   `release_exchange_executor` (`:2432`) has **no caller in `backend_app`**, and pops the
   dict entry without `await disconnect()`.
4. Who owns eviction, disconnection and shutdown drain? Unassigned — `main.py` drains the
   *other* pool (`connection_engine.close_all_exchanges()`); nothing drains `_executors`.
5. Must a mock vault be **fatal** in a deployed environment (depends on Packet 1)?

**Minimum response:** yes/no on (1), a residency rule for (2), a named owner for (3)+(4), a
yes/no on (5). **Unblocks:** W-1b gate 3 of 4.

---

## Packet 5 → Watchdog owner + Execution-state owner — **D6-B**

**Type: implementation approval + contract decision. Four questions.**

1. Which component owns `connect()` / `disconnect()`? Nothing calls `executor.connect()`
   today; with `_exchange = None`, `find_order_by_client_order_id` degrades to
   `success=False, status="error"` — fail-safe and useless.
2. Timeouts, retries, cancellation and shutdown semantics for network I/O inside a 30 s loop
   whose current backoff is `2 ** attempt`, `max_retries=3`, shutdown a bare `task.cancel()`.
3. Does `_get_executor_for_order` stay synchronous or become `async`?
4. Confirm which D5 structural tests change. Exactly these, all in
   `tests/test_d5_watchdog_adjudication_repair.py`:
   `test_the_executor_lookup_is_synchronous_and_no_call_site_awaits_it`,
   `test_every_awaited_self_method_in_the_module_is_actually_a_coroutine`, and — only if the
   persist helper is touched — `test_the_persist_helper_exists_is_synchronous_and_is_not_awaited`.
   The 15 behaviour tests are unaffected (they inject the executor).

**Minimum response:** a named owner for (1), a timeout/cancellation rule for (2), sync-or-async
for (3), acknowledgement of (4). **Unblocks:** W-1b gate 4 of 4 → the two-file integration.

---

## Packet 6 → Router owner — **D7-1 / D8-5**

**Type: implementation approval. Scope is exact and bounded.**

Re-confirmed at this baseline: `api_key_vault.py:177` is
`def load_decrypted_keys(self, user_id: str, exchange_id: str) -> dict` — two positional
parameters, no `access_token`. Three call sites pass it:

| File:line | Change |
|---|---|
| `backend_app/routers/orders.py:792` | delete `access_token=user.get("access_token"),` |
| `backend_app/routers/orders.py:1098` | delete the same line |
| `backend_app/routers/market.py:53` | delete the same line |

Plus one new test binding real arguments against
`inspect.signature(APIKeyVault.load_decrypted_keys)` via `Signature.bind`, so a
reintroduction fails.

**Preserve:** the two-argument sites (`routers/exchange.py:993`, `api_ws/ws_routes.py:814`,
`master_executor.py:252`, `portfolio_cache_updater.py:161`) and `market.py:56-58`'s
`if not keys or not isinstance(keys, dict)` guard. **Do not** add `access_token` to the vault
signature — that is a new credential contract and is not in scope.

**Why it stayed hidden:** `tests/test_market_none_keys_fix.py:13`'s double declares
`load_decrypted_keys(self, user_id, exchange_id, access_token=None)` — **the double matches
the broken calls while the real class does not.**

`orders.py`, `market.py` and `test_market_none_keys_fix.py` currently have **no unstaged
diff**, so the change is cleanly isolatable. Rollback: revert three lines, delete one test.

**Minimum response:** "approved for exactly these three deletions plus the signature test".

---

## Packet 7 → Portfolio-cache owner + Credential owner — **D7-2 / D8-3**

**Type: a choice, not a typo fix.**

`backend_app/backend/portfolio_cache_updater.py:151` imports
`backend_app.backend.key_manager`, which **does not exist** (only reference in the tree).
Reached from `start()` → `_update_loop()` → `_update_all_portfolios()` →
`_update_user_portfolio()` only when a user is registered; the `ImportError` is swallowed by
the per-user handler at `:134`, so **the portfolio cache is silently nonfunctional** while
`/health/services` still reports `ACTIVE` (a boot-time snapshot).

| Option | Security / lifecycle implication |
|---|---|
| `api_key_vault.APIKeyVault` constructed directly | raises on missing config instead of silently mocking — but creates a **new** direct-construction site in a background loop, which is inside Packet 4's scope |
| `app_state.vault` via `get_vault()` | the convention every working path uses (`master_executor.py:127`) — and inherits the mock exposure in Packet 1 |
| `core.credential_vault.CredentialVault` | async `get_credential`, feeds no ccxt client today, a different contract entirely |

**Minimum response:** name the option. **Note:** this module was the only candidate
precedent for "a background loop that constructs the vault directly", and it has never
worked — so no such precedent exists.

---

## Packet 8 → Watchdog owner **AND** Execution-state owner — **W-3**

**Type: implementation approval from two owners. Neither has granted it.**

Recorded requirement: `staging/PHASE11B_D4_DUP02_RECOVERY_READINESS.md:593` — "Watchdog
owner **+** execution-state owner (event_router)".

**Correction carried from D8 §7.2:** D5 §8's "cleared to proceed" was a *technical*
clearance and D7 §5.1 mis-filed it as "already-authorised". It is not a grant.

Targets, line numbers **refreshed at this baseline** (they moved when D5 added ~143 lines):

| File:line | Current | Change |
|---|---|---|
| `backend_app/backend/order_watchdog.py:370` | `_find_open_orders` — `order_id.isnot(None)` | coalesce with `venue_order_id` |
| `backend_app/backend/order_watchdog.py:712` | `_find_stale_orders` — `order_id.isnot(None)` | coalesce with `venue_order_id` |
| `backend_app/backend/event_router.py:135` | `ExecutionRecordModel.order_id == order_id` | coalesce with `venue_order_id` |

**Invariants to preserve:** coalesce, **never swap** — `order_id` carries
`fk_execution_records_order_id` → `orders(id)`; `venue_order_id` deliberately carries **no
FK** and migration 030's postflight aborts if one is added. Tests needed: a PostgreSQL test
proving a live completed row is selected, and a fill-event test.

**W-3 alone does not establish end-to-end recovery** while W-1b is open: it restores *which
rows are selected*; W-1b decides *whether the venue can be asked about them*.

---

## Not requested in this packet

- **W-4 before W-5** — ordering preserved. W-5 (widening `classify_stale_executions`) must
  not precede W-4 (an explicit `UNKNOWN` exit rule), or rows are selected into a state
  nothing can resolve. Owners: execution-state + coordinator.
- **W-6** — `_handle_ccxt_error`'s substring ladder places `symbol|market` **above**
  `network|timeout|connection`, so a ccxt timeout whose message embeds `symbol=` is stored
  as a determinate rejection. **Verified untouched this phase**:
  `backend_app/backend/exchange_executor.py` still hashes `81ED5679BB93AFB0`.
  `staging/PHASE11B_W1D13_CLASSIFIER_IMPLEMENTATION_REPORT.md` is a **different** classifier
  — 0 references to `_handle_ccxt_error`, `W-6` or `exchange_executor`. Owners:
  execution-state + coordinator.
- **TENANT-01** — RLS enabled-not-forced + owner-role ORM connection; `tenant_db_context.py`
  exists but unwired (`PHASE11A_LAUNCH_GATE_REGISTER.md:26`). **OPEN.** It compounds D6-C,
  because the vault's service-role client also bypasses RLS.
- **QUOTA-01** — **OPEN.** No D9 track touches it.

---

## Observed inconsistency between two concurrent workstreams (not mine to adjudicate)

`PHASE11B_A25_LAUNCH_EXECUTION_DASHBOARD.md:341` (13:22) records `AP-W1D-3` —
"Classifier implementation + 10 regression tests", `Code owner` — as **NOT GRANTED**.
`PHASE11B_W1D13_CLASSIFIER_IMPLEMENTATION_REPORT.md` (13:34) is titled "**Authorized**
Classifier Implementation" and reports `AP-W1D-3 first change` **COMPLETE** with four files
changed.

Either a grant exists that A25's sweep did not see, or the implementation proceeded without
one. **Flagged for the coordinator**; it does not affect any D9 item, and no D9 item is
being treated as granted on the strength of it.
