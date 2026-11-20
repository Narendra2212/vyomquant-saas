# PHASE 10 — WORKSTREAM C: QUOTA AND EXCHANGE CONNECTION SAFETY

**Findings in scope:** QUOTA-01 (P2, downgraded, open), CONN-01 (P2, new in Phase 7, reported)
**Revision at start:** `074b772ab16cece62cbfca0f2b78497be2ae56d5` (`main`)
**Live trading:** OFF throughout. No live order, no venue sandbox, no real credential.

---

## 0. HEADLINE

Both findings were already partly addressed before this workstream started, and both had
**more wrong with them than the finding text said**. Tracing the enforcement path and the
connection lifecycle end to end — rather than re-reading the configuration — turned up five
defective behaviours, all **measured before being fixed**:

| # | Behaviour | Measured before | Measured after |
|---|---|---|---|
| CONN-01-A | A **credential-less caller decided the pooled client for everyone**. A credentialed caller was handed an UNAUTHENTICATED client. | `apiKey=None` served to a caller that passed `real-key` | its own client, `apiKey='real-key'` |
| CONN-01-B | **Nothing closed a pooled client at shutdown.** Every `ccxt` client — open socket, plaintext credential — survived until the process died. | 3 of 3 clients left open; no drain existed; `main.py` never reached the pool | drain exists, wired into the lifespan after the bot fleet stops |
| CONN-01-C | `POST /connections/{id}/reconnect` answered **`RECONNECTED` without reconnecting**. | handler never called `release_exchange` | evicts, then tests |
| QUOTA-01-D | The **exchange-connection limit raced**. Two concurrent stores of DIFFERENT venues both passed one free slot. | Free plan (limit **1**) finished holding **2** venue credentials | 1 admitted, 1 refused |
| QUOTA-01-E | `HardQuotaEnforcer` was **still not deprecated in code** and was still instantiated by two live services. | no marker; 2 constructions | marker present; 0 constructions |

Evidence: `staging/p10c_probe_before.txt` → `staging/p10c_probe_after.txt`
(**5 of 5 defective** → **0 of 5 defective**), produced by `staging/probe_p10c_quota_conn.py`.

**No stop condition was met.** No quota can be bypassed on a money-moving path, and no
exchange credential crosses a tenant boundary — see §4 and §5, where both are measured rather
than asserted.

---

## 1. BASELINE

```
git rev-parse HEAD   074b772ab16cece62cbfca0f2b78497be2ae56d5
git branch           main
```

The working tree carried **substantial pre-existing uncommitted modifications from earlier
phases and from parallel Phase 10 workstreams** (~80 paths). All were preserved; nothing was
stashed, reverted or committed. Two of them are directly relevant:

* `backend_app/routers/exchange.py` — already contained the **CONN-01 rotation fix**
  (`release_exchange` in `store_keys`) plus the AUTH-01 `require_aal2` wiring.
* `tests/test_conn01_rotation_evicts_pooled_client.py` — untracked, 4 tests, already proving
  that fix.

So the finding **as written** ("a rotation is ineffective until restart") was already closed on
arrival. This workstream verified that, then traced the rest of the lifecycle.

**Baseline test run, before any change:**

| Suite | Result |
|---|---|
| `test_exchange_safety_fix.py` + `test_conn01_rotation_evicts_pooled_client.py` | **13 passed** |
| `test_quota01_enforcement_is_wired.py` + `test_quota01_metered_reservation_is_hard.py` | **23 passed, 1 skipped** (real-Redis gate) |

No pre-existing failures in scope. Nothing was inherited red.

### The findings, as the artifacts define them

**QUOTA-01** (`staging/full_audit_results.json`, `staging/QUOTA_ENFORCEMENT_MAP.md`): downgraded
P1→P2 in Phase 4. The premise was partly wrong — quotas **are** enforced before the side effect
by `core/subscription_dependencies.py` at 13 measured `Depends` sites, failing closed with 503
`ENTITLEMENT_USAGE_UNREADABLE`. `HardQuotaEnforcer` must **not** be wired. Residuals left open:
a duplicate quota catalogue, the enforcer not actually deprecated in code, strategy and
exchange-connection races, and no exercise against a real Redis.

**CONN-01** (`staging/ENVIRONMENT_PRECEDENCE_AUDIT.md` §CONN-01): the pool key is
`sha256([user_id, exchange_id])` and omits the credential, so `store_keys` served a pooled
client built from superseded keys. Recorded as "reported, not fixed: the fix is one
`release_exchange` call in `store_keys`".

---

## 2. ROOT CAUSES

### CONN-01-A — the pool had no notion of credential currency (the real root cause)

`get_or_create_exchange` accepted `api_key`/`secret_key` **and dropped them on a pool hit**:

```python
pool_key = hashlib.sha256(json.dumps([user_id, exchange_id], sort_keys=True).encode()).hexdigest()
async with _pool_lock:
    if pool_key not in _exchange_pool:
        ...
    return _exchange_pool[pool_key]      # the caller's credential never consulted
```

The finding framed this as a rotation problem, so the prescribed fix was a router-level
eviction. That is necessary but **not sufficient**, because a rotation is not the only way two
different credentials reach one pool key. Two of the seven callers pass **no credential at
all**:

* `backend_app/core/consistency_checker.py:319` — `get_or_create_exchange(user_id=str(tenant_id), exchange_id=exchange_id)`
* `backend_app/backend/exchange_websocket_listener.py:206` — same shape

Whichever caller populated the key first decided the client for every later one. If a
credential-less background service won the race, an **unauthenticated** client was pooled and
then handed to `routers/orders.py:794`/`:1100` and `routers/market.py:59`, whose calls
(`fetch_open_orders`, `fetch_balance`) are private and cannot work on it. No router was
involved and no rotation occurred, so the Phase 7 remediation would not have touched it. A
restart was the only cure.

### CONN-01-B — the lifespan stopped the consumers and never closed the connections

`backend_app/main.py`'s shutdown block stopped ConsistencyChecker, OrderWatchdog, PnLEngine,
ReconciliationScheduler, PortfolioCacheUpdater, DashboardDataIngester, AssetUniverseRefresher,
the bot fleet, telemetry, Redis and the WS streamer. Every one of those **opens** venue
connections; none **closes** one. There was no `close_all_exchanges`, no TTL, no idle eviction
and no reference to `_exchange_pool` anywhere in `main.py` — verified by repo-wide grep.

### CONN-01-C — `/reconnect` tested a throwaway client and left the pooled one alone

`reconnect_connection_by_id` only called `test_stored_connection`, which builds its own
unpooled `ConnectionEngine`. The pooled client the trader pressed the button *because of* was
untouched, and the response said `RECONNECTED`. The one situation the endpoint exists for was
the one situation it could not repair.

### QUOTA-01-D — the connection slot was read-then-write

`check_exchange_connection_slot` reads the account's venue set, decides, returns; the handler
then writes. Between those steps the figure is stale, so two concurrent requests for different
venues both read `len(existing) == limit - 1` and both pass.

`exchange_keys` carries `UNIQUE (user_id, exchange_id)`, which is why a double-submit of **one**
venue was never a problem and why the race needs **two** venues to show. The constraint says
nothing about the number of distinct rows, which is what the plan limit is about.

### QUOTA-01-E — the decision lived only in a report

Phase 4 decided not to wire `HardQuotaEnforcer`. The code carried no marker, the module
docstring still advertised it as live ("Ensures limits are respected even if middleware is
bypassed" — never true here; it has never had a call site), and two live services constructed
it and never read the attribute (`core/dag_task_queue.py:349`,
`core/entitlement_engine.py:224`). `tests/test_quota01_enforcement_is_wired.py` could only
assert the *absence* of call sites, which is indistinguishable from "nobody has got round to
it yet".

**A correction to the audit's wording.** The residual is recorded as a "duplicate quota
catalogue". Measured, that is imprecise in a way that matters: `TenantQuota` (`core/tenant.py`)
and `SubscriptionEngine._PLANS` share **zero field names**. What is duplicated is the *plan
ladder* (`Plan` vs `TenantPlan`) and the authority to set limits — not the numbers. Every
dimension in `TenantQuota` that would matter for trading (orders/min, daily trades, active
orders, positions, capital, leverage, concentration) has **no counterpart at all** in the
subscription catalogue. So "de-duplicate the catalogues" would *delete* dimensions nothing else
covers. The honest action was to make the single source of truth explicit and keep the record
of the gaps, which is what the deprecation docstring now does. **Not fixed, and not claimed as
fixed** — see §7.

---

## 3. WHAT CHANGED

### Implementation

| File | Change |
|---|---|
| `backend_app/backend/connection_engine.py` (+188/−18) | `_pool_key()` extracted (create and release paths previously inlined the same expression twice — a one-sided edit would have made eviction silently miss). New `_credential_identity()` (non-reversible sha256 fingerprint, `None` when no credential). `_pool_credential_id` tracks the credential **alongside** the entry. `get_or_create_exchange` rebuilds in place on a credential mismatch. `release_exchange` clears both dicts in one critical section. New `exchange_pool_size()` and `close_all_exchanges()`. |
| `backend_app/main.py` (+8 in the lifespan) | Drains the pool at shutdown, **after** `fleet.shutdown_all()` and before the Redis disconnect. Failures logged, never raised. |
| `backend_app/routers/exchange.py` (+~60) | `/connections/{id}/reconnect` now evicts **before** testing. `store_keys` wraps its vault write in `exchange_connection_slot_guard`, with `except HTTPException: raise` so the guard's refusals are not relabelled as storage failures. |
| `backend_app/core/subscription_dependencies.py` (+173/−0) | New `exchange_connection_slot_guard` (async context manager) and `_acquire_slot_lock`. Per-account cross-process `SET NX` lock, re-read inside the lock, held across the caller's write. |
| `backend_app/core/hard_quota_enforcer.py` (+115/−21) | Deprecated in code: module docstring stating the reasoning and **where the swallowing happens**, `DEPRECATION_MESSAGE`, `DeprecationWarning` on `__init__`, and a **lazy** singleton via module `__getattr__` so importing is not itself deprecated use. |
| `backend_app/core/dag_task_queue.py` (+8/−2), `backend_app/core/entitlement_engine.py` (+8/−2) | The two dormant constructions and their now-unused imports removed, each with a note saying what was there and why it went. |

### The pool reuse rule, stated

Monotone in authentication, which is what keeps it from thrashing:

* **no credential supplied** → reuse whatever is pooled. Such a caller must not downgrade an
  authenticated client it did not build.
* **credential matches the pooled client** → reuse. The ordinary multi-bot case: one socket,
  one handshake.
* **credential differs, including when the pooled client has none** → close the stale client
  and rebuild under the same key.

Because the credential-less branch never rebuilds, alternating callers (a REST router with a
credential, a background listener without — the actual workload) settle on the authenticated
client instead of tearing it down in turn. Measured:
`test_alternating_credentialed_and_credential_less_callers_do_not_thrash` runs ten alternating
calls and asserts **one** client is ever constructed.

**The pool key is unchanged, deliberately.** Re-keying on the credential would let one
(user, venue) pair hold several simultaneous authenticated sockets, defeating FIX CE-4 — the
reason the pool exists. The fix evicts *around* the primitive rather than re-keying it.

### Tests

| File | Tests | Status |
|---|---|---|
| `tests/test_conn01_pool_lifecycle_and_shutdown.py` | **23** | new |
| `tests/test_quota01_exchange_slot_is_atomic.py` | **18** (16 + 2 real-Redis gated) | new |
| `tests/test_quota01_hard_enforcer_is_deprecated_in_code.py` | **7** | new |
| `tests/test_exchange_safety_fix.py` | 9 | 1 test rewritten, fixture extended |
| `tests/test_conn01_rotation_evicts_pooled_client.py` | 4 | fixture extended |

**One existing test was deliberately changed.**
`test_pool_key_is_the_user_exchange_pair_not_the_credential` was a *characterisation* test: it
asserted `after_rotation is pooled` and `apiKey == "old-key"` — i.e. it pinned the defect, by
design, pending an owner. The fix changes that behaviour, so the test failed, which was the
correct signal. It now pins the parts that are still true and load-bearing: the key derivation
is still `sha256([user_id, exchange_id])` (computed independently of the implementation), the
rotation **replaces** rather than adds an entry, and the new credential is the one in service.
No assertion was weakened; the file gained 4 assertions net.

---

## 4. ACCEPTANCE CRITERIA

### QUOTA-01

| Criterion | Where | Verdict |
|---|---|---|
| Enforcement at the actual protected operation | `test_quota01_enforcement_is_wired.py` (13 `Depends` sites, handler-never-ran proof); `test_store_keys_uses_the_guard_around_its_vault_write` asserts the guard **wraps** the write | **met** |
| Concurrent requests cannot exceed a hard limit | `test_two_concurrent_stores_of_different_venues_cannot_both_take_one_free_slot`; `test_eight_concurrent_stores_admit_exactly_the_limit` (8 racers, limit 5, lands on exactly 5) | **met in-process**; cross-process gated on Redis (§7) |
| Retries cannot bypass the limit | `test_retrying_a_refused_request_does_not_eventually_succeed` (20 attempts at an exhausted cap, all 403) | **met** |
| Accounting consistent after failures | `test_the_lock_is_released_when_the_guarded_write_raises`, `test_a_failed_write_consumes_no_capacity` (5 failures then full allowance), `test_the_lock_is_released_on_the_guards_own_refusal` | **met** |
| One tenant cannot consume or alter another's quota | `test_one_tenant_cannot_consume_or_alter_another_tenants_quota`, `test_different_accounts_do_not_contend_with_each_other` | **met** |
| Tests detect a deliberately broken enforcement path | **two** negative controls: `test_a_guard_that_does_not_lock_fails_this_suite` (neuters `nx`) and `test_a_guard_that_does_not_recheck_fails_this_suite` (neuters the re-read). Each asserts the sabotage **reproduces the overshoot**, then that the restored guard is bounded | **met** |

The guard **fails closed**, which is the direction that matters:
`test_an_unreachable_lock_store_refuses_rather_than_admits` and
`test_a_lock_store_that_raises_also_refuses` both assert 503 and that the write did not run.
That is precisely the failure mode `HardQuotaEnforcer` was deprecated for, so it would have
been incoherent to introduce it here.

### CONN-01

| Criterion | Where | Verdict |
|---|---|---|
| Credential rotation explicitly tested | `test_conn01_rotation_evicts_pooled_client.py` (4, router) + `test_a_rotation_takes_effect_at_the_pool_even_without_the_router`, `test_a_password_change_alone_rebuilds_the_connection` (primitive) | **met, at two layers** |
| Tenant/account connection isolation tested | `test_two_tenants_never_share_a_client_even_with_identical_credentials` (the adversarial case: identical fingerprints must still not collide), `test_one_tenants_rotation_does_not_evict_another_tenants_client`, `test_the_pool_key_is_still_the_user_exchange_pair` | **met** |
| Failure and shutdown cleanup tested | `test_a_failed_rebuild_leaves_no_pool_entry_and_no_stale_client`, `test_a_close_that_raises_during_a_rebuild_still_produces_a_working_client`, `test_close_all_exchanges_closes_and_empties_the_pool`, `test_the_drain_closes_every_client_even_when_one_refuses`, `test_the_drain_is_a_no_op_on_an_empty_pool`, `test_the_lifespan_shutdown_drains_the_pool` | **met** |
| Diagnostic output checked for secret leakage | `test_no_credential_appears_in_pool_log_output` (exercises create, rebuild, release and drain, then greps captured records for 5 planted secrets **and** the fingerprint), `test_a_failed_rebuild_does_not_put_the_credential_in_the_exception` | **met** |

Additional, from the brief's verification list:

* **Concurrent creation is safe** — `test_concurrent_callers_with_the_same_new_credential_rebuild_exactly_once`: 8 racers notice a new credential at once with a genuinely suspending `load_markets`; exactly one client is constructed and the superseded one is closed exactly once. The comparison is inside `_pool_lock` along with the construction, which is what makes that true.
* **Connection counts bounded** — `test_the_pool_is_bounded_by_distinct_pairs_not_by_callers`: 50 calls including rotations over 3 pairs leave exactly 3 entries, and a refused handshake adds nothing.
* **Credentials not in diagnostics** — the fingerprint is asserted *not* to contain the key or secret, and to be a 64-char hexdigest (`test_the_credential_fingerprint_is_not_the_credential`).

---

## 5. STOP CONDITIONS — BOTH CHECKED, NEITHER MET

**"A quota can be bypassed on a money-moving path."** Not met. The money-moving quota is
**bots/deployments**, and it is closed at the database level by WORK-03
(`uq_strategy_deployments_one_live` and `pk_bot_ownership_claims`), proven under 4-process
concurrency in Phase 5. The quota this workstream repaired — exchange connections — is a
commercial capacity limit, not a money path: an overshoot was one extra credential row, not an
order. Verified separately that the order-submission path carries **no** per-tenant countable
cap (it has a global kill switch, a per-process venue throttle, deterministic idempotency,
signed authorisation and an equity×leverage notional bound) — that remains QUOTA-01 §4's open
gap, unchanged by this work and still gated by live trading being OFF.

**"Exchange credentials can cross tenant boundaries."** Not met, and now measured rather than
argued. Isolation is a property of `_pool_key`, which was never broken. The adversarial case
for a credential-aware pool is two tenants whose fingerprints are *identical*; if the credential
had been folded into the key, or consulted before it, they would collide. `test_two_tenants_
never_share_a_client_even_with_identical_credentials` asserts they get separate clients, and
`test_one_tenants_rotation_does_not_evict_another_tenants_client` asserts a rebuild is scoped
to the pair that rotated — a venue-scoped eviction would have dropped every other customer's
connection to that exchange.

---

## 6. COMMANDS AND RESULTS

```
# Baseline, before any change
pytest tests/test_exchange_safety_fix.py tests/test_conn01_rotation_evicts_pooled_client.py
  -> 13 passed
pytest tests/test_quota01_enforcement_is_wired.py tests/test_quota01_metered_reservation_is_hard.py
  -> 23 passed, 1 skipped

# Defect probe, before -> after
python staging/probe_p10c_quota_conn.py
  -> 5 of 5 probed behaviours are defective     (staging/p10c_probe_before.txt)
  -> 0 of 5 probed behaviours are defective     (staging/p10c_probe_after.txt)

# The two findings and their suites
pytest <7 suites> -p no:randomly
  -> 82 passed, 3 skipped                       (staging/p10c_suite_WSC1.txt)
     (the 3 skips are the real-Redis gates: 1 pre-existing metered, 2 added here)

# Order independence (random order, pool-contending files first)
pytest tests/test_conn01_pool_lifecycle_and_shutdown.py tests/test_exchange_safety_fix.py
       tests/test_conn01_rotation_evicts_pooled_client.py
       tests/test_quota01_exchange_slot_is_atomic.py
  -> 52 passed

# Every test file touching a changed module (92 files, batched) -- AUTHORITATIVE
powershell -File staging/run_p10c_batches.ps1 -BatchSize 10
  -> TOTAL passed=4619 failed=0 skipped=77 errors=0
     no failures or errors in any batch         (staging/p10c_batches.txt,
                                                 staging/p10c_batch_b000..b009.txt)

# The same 92 files as ONE invocation -- confirmation that batching is not hiding an
# interaction between them
powershell -File staging/run_p10c_affected.ps1 WSC4
  -> 4619 passed, 79 skipped, 3 xfailed in 1563.57s (26m04s), exit=0
     (staging/p10c_affected_WSC4.txt, staging/p10c_affected_WSC4.xml)
```

The two runs agree on the pass count exactly (**4619**), which is the point of running both:
batching localises a failure to ten files, and the single invocation rules out an interaction
between files that batching would have hidden. The skip count differs by two (77 → 79) only
because the two real-Redis tests in §4 were added after the batched run started. The 3 xfails
are pre-existing and belong to other suites.

The 92-file set was derived mechanically (`staging/p10c_find_affected.py`) from every test
module mentioning a changed symbol or module, then filtered (`staging/p10c_build_list.py`) to
drop conftests, `tests/perf/` (excluded by `pytest.ini`) and the documented 17-minute
`test_marketplace_paper_schema_contract.py` Hypothesis replay (TO-01, pre-existing, not in
scope). Every entry is validated to exist, to live under `tests/` and to end in `.py` before
the run, for the reason in the next paragraph. The 77 skips are the repository's existing
opt-in gates: 16 for Workstream E's `tests/exchange_sandbox/`
(`AERORA_EXCHANGE_SANDBOX_CERT`), 3 real-Redis variants, and the Postgres-gated
`*_postgres.py` suites.

**A misleading intermediate result, recorded because it cost time and could mislead a
reader of these artifacts.** One earlier invocation of this set reported `4699 skipped, 0
passed`. That was **not** a suite pathology and not caused by any change here: the file list
had been overwritten by a mangled shell pipeline and contained UTF-16 lines of the form
`tests/foo.py  <- entitlement` instead of paths, so pytest never received the intended
arguments. The list is now generated only by `staging/p10c_build_list.py` and validated
before use, and the corrected list passes both batched and as a single invocation. No
scale-dependent skip behaviour exists.

### A design flaw the tests caught, and the correction

The slot guard was first written as a **non-blocking** lock: any contender got 409 at once.
`test_eight_concurrent_stores_admit_exactly_the_limit` failed with `1 == 5` — safe, but
**over-refusing**: a customer connecting five venues from the UI would have got one success and
four fabricated failures against a plan that permitted all five. The lock now waits a bounded
`_SLOT_LOCK_WAIT_SECONDS = 2.0` with jittered polling before giving up, which is two orders of
magnitude more than the critical section (one read, one upsert) needs. A contender that still
cannot get in has met something pathological, and 409 is then honest rather than premature.
Recorded because the first implementation would have passed a weaker test suite.

### A test-infrastructure defect fixed in passing

`_pool_lock` is a module-global `asyncio.Lock`, and `Lock.acquire` binds itself to the running
loop the **first time it has to wait**. `pytest-asyncio` gives each test a fresh loop, so the
first test anywhere in the suite that *contends* on the pool lock bound it, and every later
contending test on a different loop raised
`RuntimeError: <Lock ...> is bound to a different event loop`. This surfaced only once a second
file contended — i.e. it was latent until this workstream added one — and made the result
depend on file order, the defect class Phase 6 already found in this module. All three pool
test fixtures now install a fresh lock per test and restore the original. **Production is
unaffected**: one loop for the process lifetime.

---

## 7. RESIDUAL RISKS — STATED, NOT FOLDED INTO A GREEN TICK

1. **The slot guard is an advisory lock, not a database constraint.** It narrows the race from
   "always" to "only if the lock store loses the key" — a Redis failover, or eviction under
   `maxmemory`, would let two holders in. The durable fix has the shape WORK-03 used for bots: a
   constraint in the schema, which holds regardless of what any application process believes.
   **That is a shared migration and was not taken unilaterally** — see §8.
2. **The guard has not run against a real Redis in this environment.** Neither Redis nor Docker
   is available here (measured: nothing on 6379/6380, no Docker server). Two gated tests exist
   and **skip**; a skip is not a pass. Close with:
   `AERORA_TEST_REDIS_URL=redis://localhost:6379 pytest tests/test_quota01_exchange_slot_is_atomic.py -k real_redis`
   This is the same residual QUOTA-01 already carried ("the quota path is never exercised
   against a real Redis") — now with tests standing ready rather than an open question.
3. **Redis is now on the critical path for storing exchange keys.** With the lock store
   unreachable, `POST /api/exchange/keys` answers 503. That is deliberate and consistent with
   API-04's retained fail-closed decision and with the 503 the usage read already returns, but
   it **is** a new availability dependency on one endpoint and support should know. In
   development `DEV_MODE` supplies the in-memory client, so local work is unaffected.
4. **The pool has no TTL and no idle eviction.** It is bounded by distinct (user, venue) pairs
   in flight — tested — but a long-lived process accumulates one entry per pair that ever
   connected, each holding a plaintext credential. No invented ceiling was added, because
   refusing a connection or evicting a live trading socket by age is a product decision, not a
   bug fix.
5. **The duplicate-catalogue residual is NOT closed.** As §2 records, the audit's framing is
   imprecise: the two tables share no field names, so there is nothing to de-duplicate without
   *losing* dimensions. The decision is now documented in code; picking one plan ladder, and
   deciding which trading dimensions the product actually wants enforced, remains open.
6. **QUOTA-01 §4's other gaps are untouched.** No orders-per-minute, daily-trade or
   active-order ceiling exists on the wired path. Mitigated today only by live trading being
   OFF and by per-route SlowAPI limits. Becomes material the moment live trading is certified.
   The deprecation docstring says explicitly that reviving `HardQuotaEnforcer` is **not** the
   way to close them.
7. **The strategy-count race is untouched.** Same read-then-write shape, no DB constraint,
   P3 — an overshoot is one row, not money. Left alone deliberately: `routers/strategies.py`
   and `routers/library.py` are outside this workstream's ownership.
8. **Nothing here is venue-verified.** Every test replaces the venue client class with an
   in-process transport. The claims are about in-process pool bookkeeping, eviction, and
   quota admission. Whether any venue accepts any key is the ENV-01 boundary and is still not
   decided in this lane.

---

## 8. DEPENDENCIES ON OTHER WORKSTREAMS / COORDINATOR

| Item | Needs |
|---|---|
| A row-count constraint on `exchange_keys` to make the connection limit durable | **Coordinator approval — shared migration.** Not written. Suggested shape: a `BEFORE INSERT` trigger, or a claims table keyed `(user_id, slot_index)` mirroring `pk_bot_ownership_claims`. Requires a decision on what happens to existing over-limit accounts and on plan downgrades (today a downgrade leaves stored credentials alone and only prevents adding the next one). |
| Shared scorecard and finding registry (`staging/FINAL_LAUNCH_SCORECARD.md`, `staging/full_audit_results.json`) | **Not updated, per the brief.** QUOTA-01 and CONN-01 still read as previously recorded. This report is the input for that decision. |
| `tests/exchange_sandbox/` (Workstream E) | Not touched. Its opt-in collection gate is correctly scoped — verified directly (`pytest tests/exchange_sandbox/... tests/test_exchange_safety_fix.py` → 9 passed, 16 skipped), because an unscoped version would have skipped this workstream's evidence too. |
| TEST-05 / order-idempotency, tenant-isolation fixtures, RLS policies, fee/referral/reconciliation | Not touched. |
| `backend_app/main.py` | Shared file. One 8-line block added inside the lifespan's shutdown half; no other line altered. Flagged because other workstreams also hold modifications to this file. |
| `backend_app/core/subscription_dependencies.py` | Shared with any workstream touching entitlements. Changes are **purely additive** (+173/−0): one context manager, one helper, three constants. No existing function was modified, so existing gates and their tests are untouched. |

---

## 9. REVISION

All work is in the **working tree on `main`**, uncommitted, alongside the pre-existing
modifications described in §1. Nothing was committed, stashed or branched, because the tree
already carried other workstreams' in-flight changes and committing would have mixed them.

**This workstream's paths:**

```
modified:   backend_app/backend/connection_engine.py
modified:   backend_app/core/subscription_dependencies.py
modified:   backend_app/core/hard_quota_enforcer.py
modified:   backend_app/core/dag_task_queue.py
modified:   backend_app/core/entitlement_engine.py
modified:   backend_app/main.py                        (8-line lifespan block only)
modified:   backend_app/routers/exchange.py            (also carries AUTH-01/SC-27 from earlier)
modified:   tests/test_exchange_safety_fix.py
modified:   tests/test_conn01_rotation_evicts_pooled_client.py   (fixture only)
new:        tests/test_conn01_pool_lifecycle_and_shutdown.py
new:        tests/test_quota01_exchange_slot_is_atomic.py
new:        tests/test_quota01_hard_enforcer_is_deprecated_in_code.py
new:        staging/probe_p10c_quota_conn.py
new:        staging/PHASE10_WSC_QUOTA_CONNECTION_SAFETY.md
evidence:   staging/p10c_probe_before.txt, staging/p10c_probe_after.txt,
            staging/p10c_suite_WSC1.txt, staging/p10c_batches.txt,
            staging/p10c_batch_b000..b009.txt
harness:    staging/p10c_find_affected.py, staging/p10c_build_list.py,
            staging/run_p10c_batches.ps1, staging/run_p10c_affected.ps1
```

Isolating these onto a branch is a one-line `git stash push -- <paths>` away if the coordinator
wants them separated; doing so now would have required deciding which of the ~80 pre-existing
modifications belong to which workstream, which is not this tab's call.

---

## 10. STATUS

```
CONN-01:  the finding as written was already closed on arrival (router eviction, Phase 10).
          THREE FURTHER DEFECTS on the same lifecycle found, measured and fixed:
          credential-less pool poisoning, no shutdown drain, a /reconnect that did not
          reconnect. All four acceptance criteria met. NOT venue-verified.

QUOTA-01: enforcement was already real, wired and fail-closed. The exchange-connection
          limit RACED and now does not; the enforcer is now deprecated in code rather than
          only in a report. Five of six acceptance criteria met outright; the concurrency
          criterion is met in-process and GATED cross-process on a Redis this environment
          does not have.
          STILL OPEN, and not claimed otherwise: no DB-level backstop for the connection
          limit, the §4 order-rate/daily-trade/active-order gaps, the strategy-count race,
          and the plan-ladder duplication.
```

**Neither finding is marked fixed here.** CONN-01's four acceptance criteria are met with
targeted proof and it is a defensible close; QUOTA-01 is materially stronger but retains named
open items, at least one of which (the database backstop) needs coordinator approval to
address. Both are the coordinator's call, with this report as the evidence.

Live trading was OFF for the entire workstream. No live order was submitted, no venue was
contacted, and no real credential was used at any point.
