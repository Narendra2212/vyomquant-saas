# D8 — W-1b Credential Evidence and Independent Workstream Execution

**Outcome: 0 source, test, migration, CI or deployment files modified.** All five tracks are
blocked, each by the condition its own brief names. **W-1b remains OPEN. Live trading
remains OFF / NOT CERTIFIED.**

This is not a throughput failure. The blocking fact was established before any edit and is
the same for every track:

> **No affirmative owner grant exists anywhere in this programme's records, and this
> environment provides no channel to request one.**

Evidence, from a sibling workstream that ran the repo-wide scan
(`staging/PHASE11B_A23R_REDIS_LIFECYCLE_READINESS.md:43`):

> Every `GRANTED` token in `staging/PHASE11B_*.md` is one of exactly two things: the string
> `NOT GRANTED`, or a *request* for a grant. There are no affirmative grants for this change.

`staging/PHASE11B_A23S_REDIS_LIFECYCLE_OWNERSHIP_HANDOFF.md:170` records the same verdict
reached by **four independent sessions**, and
`staging/PHASE11B_PG_PRESERVATION_COORDINATION_REPORT.md` §3 records that no channel exists
to Platform, Stream D, the Coordinator or the DB owner, and that **no request was
delivered**. Content was rephrased only where quoted above is verbatim.

**I also have to correct my own prior framing** — see §6. D7 §5.1 listed W-3 under "can
proceed now … Cleared on its own merits in D5 §8". That is a *technical* clearance I wrote,
not an owner authorization, and it should not be read as one.

---

## 0. Phase 0 — rebaseline and preservation

| Property | D7 reference | **Measured now** | Moved? |
|---|---|---|---|
| Branch | `main` | `main` | no |
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | **no** |
| `origin/main...HEAD` | `0 0` | `0 0` | no |
| Worktrees | 1 | **1** | no |
| Staged entries / digest | 22 / `2e994018…7152` | **22 / matches** | **no** |
| `--porcelain` total | 257 | 261 | +4, all other workstreams' (§0.2) |

### 0.1 D7's six candidate files — all fingerprints match

| File | SHA-256 (16) | Matches D7 |
|---|---|---|
| `backend_app/backend/api_key_vault.py` | `FE42F4F3E63BF417` | ✔ |
| `backend_app/backend/exchange_executor.py` | `81ED5679BB93AFB0` | ✔ |
| `backend_app/backend/order_watchdog.py` | `507F5C85A7984200` | ✔ |
| `backend_app/main.py` | `DE1CCF9ADFFFBA0C` | ✔ |
| `backend_app/core/state.py` | `6AC55227B378EE5E` | ✔ |
| `tests/test_d5_watchdog_adjudication_repair.py` | `AB918192B4801F13` | ✔ |

All six match, so **D6 and D7 source findings are reused rather than re-derived**, as
instructed. §1 records what was nonetheless re-verified and what was newly established.

### 0.2 Concurrent work, preserved

New since D7, all other workstreams: `PHASE11B_A23_REDIS_LIFECYCLE_IMPLEMENTATION_REPORT.md`,
`PHASE11B_A23R_REDIS_LIFECYCLE_READINESS.md`, `PHASE11B_A23S_REDIS_LIFECYCLE_OWNERSHIP_HANDOFF.md`,
`PHASE11B_A23D_P1_AUTHORIZATION_HANDOFF.md` and related. **No candidate file touched. No
report overwritten.** No `reset`, `clean`, `stash`, `switch` or `checkout` was used.

### 0.3 Commands executed — all read-only

`git rev-parse` / `rev-list` / `worktree list` / `status --porcelain` / `diff --cached --raw`
(digest only); `Get-FileHash` ×6; `Test-Path` ×4; `grep_search` over
`staging/*.md`, `staging/PHASE11B_*.md`, `tests/**/*.py` and two single modules;
`read_file` on the authorization records. **No test run, no database query, no network call,
no secret value read** — variable names and reader code only.

### 0.4 Baseline differs from D7? No

Nothing material changed, so D7's dependency plan stands unmodified. The only revision is
the authorization reading in §6, which is a correction to interpretation, not to the
baseline.

---

## 1. Reused versus re-verified

| Finding | Source | Status |
|---|---|---|
| `get_exchange_executor` is the supported factory; no `ExchangeRouter`/`get_exchange_client` exists | D6 §2.1 | **reused** (file hash matches) |
| `app_state.vault` substitution mechanism; `MockEngine.__getattr__` defeats `hasattr` | D6 §2.2 / D7 §1.1 | **reused** |
| Executors built unconnected; nothing calls `executor.connect()` | D6 §2.3 | **reused** |
| `release_exchange_executor` has no caller | D6 §2.3 | **reused** |
| Deployed boot validates the six credential variables; `MASTER_ENCRYPTION_KEYS` absent from `validate_required()` | D7 §1.2 | **reused** |
| `dependencies.py:316` tenant-claim fallback is the divergence path | D7 §2.2 | **reused** |
| Three `access_token=` call sites (not four) | D7 §4.1 | **re-verified** — §3.1 |
| No test exercises the real signature at those three sites | D7 §4.1 | **re-verified and sharpened** — §3.2 |
| `key_manager` module does not exist | D7 §4.2 | **re-verified**, and its **reachability newly established** — §4 |
| Authorization status of each track | — | **newly established** — §2, §6 |

---

## 2. Authorization status per track

| Track | Item | Required grant | Status | Brief's prescribed action |
|---|---|---|---|---|
| A | D6-D deployed vault evidence | platform/deployment owner check | **UNAVAILABLE** — no deployed access, no channel | "record D6-D as unresolved" |
| B | D6-C tenant identity | DB owner, explicit query authorization | **NOT GRANTED** | "Do not run the query … without explicit authorization" |
| C | D7-1 router signature fix | router owner | **NOT GRANTED** | "produce the smallest actionable patch plan and stop without editing" |
| D | D7-2 `key_manager` import | portfolio-cache owner | **NOT GRANTED** | "stop and report the precise decision needed" |
| E | W-3 venue order-id selection | watchdog owner **+** execution-state owner (D4 approval matrix) | **NOT GRANTED** | conditional on "current authorization remains valid" — it was never granted (§6) |

No grant was inferred from a report recommendation, from silence, or from a grant for a
different item.

---

## 3. Track C — D7-1: real vault signature compatibility

**NOT GRANTED. No edit made. Patch plan below.**

### 3.1 Facts re-verified at this baseline

Signature, `backend_app/backend/api_key_vault.py:177` — two positional parameters, no
`access_token`, no `**kwargs`:

```python
def load_decrypted_keys(self, user_id: str, exchange_id: str) -> dict:
```

All seven call sites in `backend_app/`:

| Call site | Form | Compatible with the real vault? |
|---|---|---|
| `routers/orders.py:789-793` | `(user["id"], exchange_id, access_token=…)` | **NO** |
| `routers/orders.py:1095-1099` | `(user["id"], exchange_id, access_token=…)` | **NO** |
| `routers/market.py:50-54` | `(user["id"], exchange_id, access_token=…)` | **NO** |
| `routers/exchange.py:993-996` | two args | yes — **preserve** |
| `api_ws/ws_routes.py:814` | two args | yes — **preserve** |
| `backend/master_executor.py:252` | two args | yes — **preserve** |
| `backend/portfolio_cache_updater.py:161` | two args | yes — **preserve** (but see §4) |

Three sites, unchanged from D7. D7's correction to D6 (that `ws_routes.py:814` is **not** one
of them) holds.

### 3.2 Test coverage — sharpened

The real method **is** exercised by two tests, but **never at the three router call sites**:

- `tests/test_exchange_phase6c_adversarial_acceptance.py:50` —
  `vault.load_decrypted_keys(user_id="tenant_a", exchange_id="binance")`, two keyword args
- `tests/test_exchange_credential_log_redaction.py:309-311` — two keyword args

Every test that touches the router paths uses a **double**, and the decisive detail:
`tests/test_market_none_keys_fix.py:13` declares

```python
def load_decrypted_keys(self, user_id, exchange_id, access_token=None):
    return None
```

**The double matches the broken call sites while the real class does not.** A passing test
over that double is not evidence of compatibility — it is the mechanism by which the
incompatibility stayed invisible. Several other tests assert the call is *absent* from a
module (`test_task_8_2_deployment_binding.py:495-501`, `security/test_builder_tenant_isolation.py:1460`,
`test_ml_deployment_guards.py:292`) or patch it out
(`test_task_8_2_deploy_preflight.py:460-462`); none constrains the signature at a router.

### 3.3 Smallest actionable patch plan (for the router owner)

| Step | Change |
|---|---|
| 1 | `routers/orders.py:789-793` — delete the `access_token=user.get("access_token"),` line |
| 2 | `routers/orders.py:1095-1099` — same |
| 3 | `routers/market.py:50-54` — same |
| 4 | New test asserting every `load_decrypted_keys` call site in `backend_app/` is callable against the **real** `inspect.signature(APIKeyVault.load_decrypted_keys)` — i.e. bind the actual arguments with `Signature.bind`, so a future `access_token=` reintroduction fails |

Three deletions, one test. **Do not** add `access_token` to the vault signature: that would
create a new credential-loading contract, which the brief forbids and which no owner has
authorized. `market.py:56-58`'s `if not keys or not isinstance(keys, dict)` guard should be
left in place — it is the only thing standing between a `None`-returning vault and a
`TypeError`, and §5 shows that path is live.

Rollback: revert three lines, delete one test file. Isolatable: yes — `orders.py`,
`market.py` and the new test have no D5/D6/D7 delta and are not in the 22 staged entries.

---

## 4. Track D — D7-2: the nonexistent `key_manager` import

**NOT GRANTED. No edit made. One decision required.**

### 4.1 Confirmed at this baseline

`backend_app/backend/portfolio_cache_updater.py:150-151`:

```python
from backend_app.backend.connection_engine import get_or_create_exchange
from backend_app.backend.key_manager import KeyVault
```

`Test-Path backend_app/backend/key_manager.py` → **False**. It is the **only** reference to
`key_manager` anywhere in `backend_app/`.

### 4.2 Reachability — newly established, and it refines the severity

The call chain, all within this module:

```
main.py lifespan → start_portfolio_cache_updater()  (:252)
  → PortfolioCacheUpdater.start()                   (:62)  → asyncio.create_task(_update_loop())
    → _update_loop()                                (:111) while self._running
      → _update_all_portfolios()                    (:123) iterates self._user_exchanges
        → _update_user_portfolio(...)               (:135) ← the broken import is at :150-151
```

So the import is reached **only when at least one user is registered** for portfolio
updates (`_user_exchanges` non-empty). When reached, the `ImportError` is caught by the
per-user handler at `:134` — `logger.error(f"❌ Failed to update portfolio for {user_id}: {e}")`
— and the loop continues to the next user.

**Severity refinement vs D7-2:** this is **not** a boot failure and not a crash. It is a
feature that is **silently dead for every registered user**, reported once per user per
cycle as a generic "failed to update portfolio". The service still reports `ACTIVE` in
`/health/services`, because that is a boot-time snapshot (D4 A-9). D7 flagged it P1; the
refined characterisation keeps it P1 but changes what an operator would see.

### 4.3 The precise decision required

**Which vault implementation should this module use?** Three candidates exist and they are
not interchangeable:

| Candidate | Notes |
|---|---|
| `backend_app.backend.api_key_vault.APIKeyVault` | the real vault; constructing it directly raises on missing config rather than silently mocking — but it is a **new** direct-construction site in a background loop, which touches D6-A |
| `app_state.vault` (via `get_vault()`) | the convention every other working path uses (`master_executor.py:127`) — and therefore inherits the MockEngine exposure in §5 |
| `backend_app.core.credential_vault.CredentialVault` | an async API (`get_credential`), feeds no ccxt client today, and would be a different contract |

This is a **credential-ownership choice**, not a typo fix, which is why it is not taken
here. Note that this module was D7's only candidate precedent for "a background loop that
constructs the vault directly" — and it has never worked, so **no such precedent exists**.
Every working credential path reads the global state.

**Owner: portfolio-cache owner, with the credential owner for the choice between the three.**

---

## 5. Track A — D6-D: deployed vault evidence

**UNRESOLVED.** No deployed access and no channel to the platform owner. Recorded as the
brief directs.

### 5.1 The request, ready to route

> **To the platform / deployment owner.** In each deployed environment (staging and
> production), report three facts and nothing else:
>
> 1. `type(app_state.vault).__name__` — expected `APIKeyVault`; `MockEngine` means the
>    credential vault has been silently replaced by a no-op.
> 2. Whether the boot log contains the string `Failed to import APIKeyVault`.
> 3. Whether the boot log contains the string `APIKeyVault initialization failed, using mock`.
>
> Report the type name and two booleans. **Do not report, copy or print any credential
> value, environment-variable value, user identifier or email.** All three are type/log
> observations and expose no secret.

### 5.2 Why this is load-bearing beyond the watchdog

Restated from D7 §1.4 because it changes the priority: `master_executor.py:127` is
`self.vault = global_state.vault`, so BotRunner — the **only working credential→executor
path** — reads the same object. With a mock, `load_decrypted_keys` returns `None` and
`master_executor.py:255-257`'s `keys["api_key"]` raises
`TypeError: 'NoneType' object is not subscriptable` **on the live order path**.

Corroboration that the mock path is live in practice, not hypothetical: `market.py:56-58`
carries a guard shaped exactly for a `None`-returning vault, and
`tests/test_market_none_keys_fix.py:13`'s docstring calls it "simulating the test
environment failure".

**The local measurement (D6: `type == MockEngine`) is not production evidence** and is not
offered as such.

---

## 6. Track B — D6-C: tenant identity evidence

**UNRESOLVED.** No DB-owner authorization; **no query was run.**

### 6.1 Query prepared, not executed

> **To the DB owner.** Against the production database, report **counts only**:
>
> ```sql
> -- (a) distinct execution tenants
> SELECT count(DISTINCT tenant_id) AS distinct_tenants
>   FROM public.execution_records;
>
> -- (b)/(c) tenants with and without a matching credential row
> SELECT count(*) FILTER (WHERE k.user_id IS NOT NULL) AS tenants_with_keys,
>        count(*) FILTER (WHERE k.user_id IS NULL)     AS tenants_without_keys
>   FROM (SELECT DISTINCT tenant_id FROM public.execution_records) e
>   LEFT JOIN public.exchange_keys k
>          ON k.user_id = e.tenant_id::text;
> ```
>
> **(c) = 0** is necessary for the mapping to hold. Return the three integers only — **no
> `tenant_id`, `user_id`, email, exchange id or credential value.**

### 6.2 A limitation that must be stated, not glossed

The fourth count the brief asks for — identities whose **JWT tenant claim or app-metadata
tenant differs from `sub`** — **cannot be measured from `execution_records` or
`exchange_keys` at all.** That divergence originates in the token issuer
(`dependencies.py:316` reads `payload.get("tenant_id")` or
`payload.get("app_metadata", {}).get("tenant_id")`), and a claim is not a table.

It is answerable only from (i) the Supabase auth configuration / custom-claims hook, or
(ii) `auth.users.raw_app_meta_data`, e.g. a count of rows where that JSON carries a
`tenant_id` differing from `id`. Both require the auth/platform owner in addition to the DB
owner. **Until measured, the mapping stays unproven** — and per the brief it is **not**
inferred from UUID formatting or matching types.

Two further reasons the database cannot be relied on to enforce it, carried forward from
D7 §2.3: `exchange_keys.user_id` is `TEXT` with **no foreign key**, and the RLS policy
`auth.uid()::text = user_id` that would constrain it is **bypassed** because the vault holds
a service-role client.

### 6.3 No query was run locally, deliberately

The project's read-only lane (`staging/bootstrap_pg.py verify`) was inspected first, as
instructed. The only reachable database is the local provisioned test cluster, which holds
no real connected exchange accounts — so any result would be vacuous rather than evidence,
and running it would create the appearance of a measurement that had not been made.

---

## 7. Track E — W-3, and a correction to my own prior reports

**NOT GRANTED. No edit made.**

### 7.1 The authorization record

D4's approval matrix recorded W-3's requirement explicitly
(`staging/PHASE11B_D4_DUP02_RECOVERY_READINESS.md:593`):

| ID | Required owner/requirement authorization |
|---|---|
| **W-3** | **Watchdog owner + execution-state owner (event_router)** |

No grant for either owner exists in any record.

### 7.2 The correction

D5 §8 answered the question *"Can W-3 safely proceed?"* with "Yes", and that answer was
**technical**: no conflict with D5, the hazard that would have made W-3 untestable is gone,
and a structural guard now exists. D5 §8 simultaneously recorded two conditions — "W-3
spans two owners" and "W-1b should land first or alongside".

D7 §5.1 then listed W-3 under **"Read-only or already-authorised — can proceed now"** with
"Depends on: nothing. Cleared on its own merits in D5 §8."

**That placement was wrong, and I am correcting it here.** A technical clearance I wrote is
not an owner authorization. Reading it as one is exactly the failure mode
`PHASE11B_A23R_…:43` warns against — inferring a grant from a report recommendation. W-3
belongs under "blocked on owner decisions" until the two named owners grant it.

### 7.3 Patch plan, unchanged and still correct

Targets re-confirmed at this baseline: `order_watchdog.py:359` (`_find_open_orders`),
`:694` (`_find_stale_orders`), and `event_router.py:128-136`
(`_find_execution_by_order_id`). The approved shape is **coalesce, never swap** —
`order_id` carries `fk_execution_records_order_id` → `orders(id)` and `venue_order_id`
deliberately carries no FK (migration 030's postflight aborts if one is added). Tests
needed: a PostgreSQL test proving a live completed row is selected, and a fill-event test.

---

## 8. Phase 2 — the two blocked decisions, exactly as they must be put

Not presented to any owner (no channel). Recorded for routing. **Neither may be implemented
before its decision is recorded, and credentials must not be loaded on the unverified
`tenant_id` mapping.**

### D6-A — credential posture · security/credential owner + coordinator

1. May decrypted credentials enter the API background process at all?
2. For how long may they remain resident? `get_exchange_executor` caches an executor that
   retains `_api_key`/`_api_secret` on the instance (`exchange_executor.py:1081-1083`),
   against a vault docstring that says the returned dict "must be consumed immediately and
   never logged, serialised, or stored in any cache" (`api_key_vault.py:70-71`).
3. How are cached executors' credential references released?
   `release_exchange_executor` (`:2432`) has **no caller in `backend_app`**, and it pops the
   dict entry without `await disconnect()`.
4. Who owns eviction, disconnection and shutdown drain? Unassigned today;
   `main.py` drains the *other* pool (`connection_engine.close_all_exchanges()`) and
   nothing drains `_executors`.
5. How are failures and partial initialisation handled — specifically, must a mock vault be
   **fatal** in a deployed environment (D6-D / §5)?

### D6-B — connection lifecycle · watchdog owner + execution-state owner

1. Which component owns `connect()` / `disconnect()`? Nothing calls
   `executor.connect()` today; with `_exchange = None`,
   `find_order_by_client_order_id` degrades to `success=False, status="error"` — fail-safe
   and useless.
2. Timeouts, retries, cancellation and shutdown semantics for network I/O inside a
   30-second loop whose current backoff is `2 ** attempt` with `max_retries=3` and whose
   shutdown is a bare `task.cancel()`.
3. Does `_get_executor_for_order` stay synchronous or become `async`?
4. Which D5 structural tests change? Exactly these, all in
   `tests/test_d5_watchdog_adjudication_repair.py`:
   `test_the_executor_lookup_is_synchronous_and_no_call_site_awaits_it`,
   `test_every_awaited_self_method_in_the_module_is_actually_a_coroutine`, and — only if the
   persist helper is touched — `test_the_persist_helper_exists_is_synchronous_and_is_not_awaited`.
   The 15 behaviour tests are unaffected because they inject the executor.

---

## 9. Phase 3 — verification

No code changed, so there is nothing to diff and no focused test to attribute to a change.
Per the brief, this section reports the preservation checks instead, and does **not** present
the D5 suite as verification of work that was not done.

| Check | Result |
|---|---|
| Source / test / migration / CI / deployment files modified | **0** |
| Files written | **1** — this report |
| D7's six candidate hashes | all **identical** ✔ |
| Staged index | 22 entries, digest `2e994018…` unchanged ✔ |
| `HEAD` / branch / divergence | `074b772a…` / `main` / `0 0` ✔ |
| Anything staged, committed, pushed, branch changed | **none** |
| Production DB query, CI run, live exchange call, deployment, real-Redis test | **none** |
| Secrets read, printed or copied | **none** — names and reader code only |
| Leaked secrets / unsafe fallback introduced / credential retention changed / tenant scope changed | **none** — no code changed |
| Existing staged, unstaged and untracked work | preserved ✔ (261 entries, other workstreams' +4 since D7) |

D5's 18 regression tests were last executed in D7 at these exact file hashes: **51 passed,
0 failed, 0 skipped**, PostgreSQL included (`staging/p11bd6_verify.txt`). Because every
relevant hash still matches, that evidence remains valid and was **not** re-run — there is
no change for it to verify.

---

## 10. Phase 4 — status register

### 10.1 Tracks

| Track | Owner | Authorization | Files touched | Tests | Outcome |
|---|---|---|---|---|---|
| A · D6-D | platform/deployment | n/a — evidence request | 0 | — | **UNRESOLVED**, request in §5.1 |
| B · D6-C | DB owner (+ auth owner) | **NOT GRANTED** | 0 | — | **UNRESOLVED**, query in §6.1, limitation in §6.2 |
| C · D7-1 | router owner | **NOT GRANTED** | 0 | — | patch plan §3.3 |
| D · D7-2 | portfolio-cache + credential owner | **NOT GRANTED** | 0 | — | decision §4.3 |
| E · W-3 | watchdog + execution-state | **NOT GRANTED** | 0 | — | plan §7.3; **D7 §5.1 corrected** |

### 10.2 Newly discovered this phase

| # | Finding | Sev |
|---|---|---|
| **D8-1** | No affirmative owner grant exists in any programme record, and no channel exists to obtain one — four independent sessions concur | **process P0** |
| **D8-2** | D7 §5.1 mis-filed W-3 as "already-authorised"; corrected in §7.2 | correction |
| **D8-3** | The `key_manager` import is reached only for registered users and the `ImportError` is swallowed per user — the portfolio cache is **silently dead**, not crashing, while `/health/services` still reports `ACTIVE` | P1 (refines D7-2) |
| **D8-4** | JWT tenant-claim divergence is **unmeasurable from the application tables**; it needs the auth/platform owner, not only the DB owner | blocks D6-C |
| **D8-5** | `tests/test_market_none_keys_fix.py:13`'s double declares `access_token=None` — the double matches the broken call sites while the real class does not, which is the mechanism that hid D7-1 | P1 |

### 10.3 Blocker register

| ID | Blocker | Sev | Owner | Status |
|---|---|---|---|---|
| **W-1b** | Watchdog cannot obtain a usable executor | **P0** | watchdog + credential/DI | **OPEN** — gated on D6-D, D6-C, D6-A, D6-B |
| **D6-D** | Real vault not guaranteed; exposure already on the live order path | **P0** | platform | **UNRESOLVED** — §5.1 |
| **D6-C** | `tenant_id` → vault `user_id` unproven; divergence path at `dependencies.py:316` | **P0** | DB + auth owner | **UNRESOLVED** — §6 |
| **D6-A** | Decrypted credentials in the API loop; `_executors` never drained; no teardown owner | **P0** | security + coordinator | decision pending §8 |
| **D6-B** | Who connects/disconnects; timeouts; sync-vs-async contract | **P0** | watchdog + execution-state | decision pending §8 |
| **W-3** | `order_id` never written; two selectors and the fill path match nothing | **P0** | watchdog + execution-state | **NOT GRANTED** (§7) |
| **W-4** | Nothing exits `UNKNOWN`; the DUP-02 guard refuses it forever | **P0** | execution-state + coordinator | **OPEN — before W-5**, ordering preserved |
| **W-5** | Widen `classify_stale_executions` | P0 | execution-state | **OPEN — after W-4** |
| **W-6** | `_handle_ccxt_error`'s substring ladder places `symbol\|market` above `network\|timeout\|connection`, so a ccxt timeout whose message embeds `symbol=` is stored as a determinate rejection | **P0** | execution-state + coordinator | **OPEN** |
| **A-6** | `.env.example` `ALERT_*` names have no consumer | P0 | platform | OPEN |
| **TENANT-01** | RLS enabled-not-forced + owner-role ORM connection → RLS bypassed on the ORM path; `tenant_db_context.py` exists but unwired | P1 prod / P2 restricted paper | tenancy owner | **OPEN** (`PHASE11A_LAUNCH_GATE_REGISTER.md:26`) — unchanged by D8; note it compounds D6-C, since the vault also bypasses RLS |
| **QUOTA-01** | Exchange connection-slot quota | P1 | quota owner | **OPEN** — unchanged by D8, no D8 track touches it |
| **D7-1 / D8-5** | Three `access_token=` sites; the test double masks them | P1 | router owner | plan ready §3.3 |
| **D7-2 / D8-3** | `key_manager` import; portfolio cache silently dead | P1 | portfolio-cache owner | decision ready §4.3 |

---

## 11. Independently completed work versus end-to-end recovery

**Independently completed this phase:** nothing in code. What was completed is evidential —
the authorization status of all five tracks established from authoritative records rather
than assumed, two owner-ready evidence requests written with their privacy constraints,
three patch plans precise enough to execute on grant, one reachability question closed
(§4.2), one measurement limitation identified (§6.2), and one correction to my own prior
report (§7.2).

**End-to-end ambiguous-submission recovery does not work and nothing in D8 moves it.** The
chain needs all of: a usable executor (**W-1b**, gated on four decisions), rows that the
selectors can see (**W-3**, not granted), an exit from `UNKNOWN` (**W-4**), and an outcome
that is not a timeout mislabelled as a rejection (**W-6**). D5 fixed the two defects that
stopped the adjudicator being *reachable*; everything downstream of reachability is still
open.

**W-3 alone would not establish end-to-end recovery even if granted** — it restores *which
rows are selected*, while W-1b decides *whether the venue can be asked about them*. W-3
without W-1b yields more rows correctly refused and none resolved.

---

**0 files modified · 5 tracks blocked, each by its own brief's stated condition · W-1b
OPEN · D6-D and D6-C UNRESOLVED with requests routed · D6-A and D6-B framed for decision ·
W-4-before-W-5 preserved · W-6, TENANT-01 and QUOTA-01 open and visible · no commit, push,
staging, database query, deployment or venue call · shared `HEAD` and the 22-entry staged
index preserved byte-for-byte · **live trading OFF / NOT CERTIFIED**.**
