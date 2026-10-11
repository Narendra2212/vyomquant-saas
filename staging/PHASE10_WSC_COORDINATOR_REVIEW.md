# PHASE 10 — WORKSTREAM C: COORDINATOR CLOSEOUT REVIEW

Quota and Exchange Connection Safety — findings **QUOTA-01** and **CONN-01**.

This is a review document. It does **not** close either finding in any shared registry.

**Label key used throughout.**

| Label | Meaning |
|---|---|
| **VERIFIED** | Independently checked in this closeout by reading the current code or running a command whose output is recorded here |
| **REPORTED** | Claimed by `staging/PHASE10_WSC_QUOTA_CONNECTION_SAFETY.md`; plausible and consistent with the code, but not independently re-measured in this closeout |
| **UNVERIFIED** | Cannot be established in this environment; a skip is not a pass |
| **DECISION** | Awaiting a coordinator decision; no technical blocker |

---

## 1. Scope and safety status

| Rule | Status |
|---|---|
| Live trading OFF | **VERIFIED** — no `LIVE*`/`TRADING*` variable set; no execution path invoked |
| No live or sandbox exchange orders placed | **VERIFIED** — no venue contacted. `tests/exchange_sandbox/` stayed gated (`AERORA_EXCHANGE_SANDBOX_CERT` unset, 16 skips recorded) |
| No real exchange credentials used | **VERIFIED** — all tests replace the venue client class with an in-process transport |
| No reset / stash / clean / revert / overwrite of unrelated changes | **VERIFIED** — no `git` write command of any kind was run |
| Concurrent work preserved | **VERIFIED** — `git status --short` went 244 → 246 entries during this closeout. The increase is other tabs adding files, not this one (this closeout's new files all live under the single pre-existing `?? staging/` entry). Nothing was removed; see §2 |
| No commit, push, or deploy | **VERIFIED** — `HEAD` unchanged at `074b772a` |
| Shared scorecard / finding registry untouched | **VERIFIED** by modification time: `FINAL_LAUNCH_SCORECARD.md` 11:35, `full_audit_results.json` 11:33, `PHASE10_WSD_FINANCIAL_INTEGRITY.md` 11:52 — all predate the WSC report (12:20) and this review (12:52) |
| No shared database migration created or applied | **VERIFIED** — §10 is a review-only proposal; no `.sql` file was written |
| No claim that mocked tests prove real-Redis or venue behaviour | **VERIFIED** — §8 and §9 record both as NOT VERIFIED |

Scope note: this closeout made **one code change** beyond review — an ownership-checked lock
release, in response to a defect found during the §5 diff review. It is documented in §5.4
and §12, is confined to this workstream's own code, and is covered by four new tests plus a
negative control.

---

## 2. Branch, commit, and working-tree baseline

```
git rev-parse HEAD        074b772ab16cece62cbfca0f2b78497be2ae56d5
git rev-parse --abbrev-ref HEAD   main
git log --oneline -1      074b772a Give the deploy gate permission to read the answer it refuses without
```

`git status --short` → **244 entries** at the start of this closeout, **246** at the end
(**VERIFIED**). The tree carries extensive concurrent work from other Phase 10 tabs and earlier
phases; the two additional entries appeared while this review was in progress and belong to
other tabs. Nothing was staged, committed or discarded. The whole `staging/` directory is
untracked (`?? staging/`), so every report and evidence file in it — this one included — is
untracked by design, which is why no `staging/` path appears individually in that count.

**Concurrent change that affects this review (VERIFIED).**
`backend_app/backend/exchange_websocket_listener.py` is now modified by another tab
(`36` insertions / `13` deletions). It is one of the two **credential-less** callers of
`get_or_create_exchange` that CONN-01-A turns on, so it was checked specifically: the diff
contains no `get_or_create_exchange`, `api_key` or `secret` line, and the call at
`exchange_websocket_listener.py:206` is still credential-less. `core/consistency_checker.py:319`
is likewise unchanged. **The CONN-01-A premise and fix both remain valid.**

Current caller inventory, re-derived in this closeout (**VERIFIED**, 7 sites):

| Caller | Credentials |
|---|---|
| `routers/market.py:59` | from vault |
| `routers/orders.py:794`, `:1100` | from vault |
| `api_ws/ws_routes.py:815` | from vault |
| `backend/portfolio_cache_updater.py:161` | from vault |
| `core/consistency_checker.py:319` | **none** |
| `backend/exchange_websocket_listener.py:206` | **none** |

---

## 3. Report rename and reference audit

**The premise in the closeout brief does not match the repository. Corrected below.**

| File | State |
|---|---|
| `staging/PHASE10_WSC_QUOTA_CONNECTION_SAFETY.md` | **EXISTS** — 32,229 bytes |
| `staging/PHASE10_WSD_QUOTA_CONNECTION_SAFETY.md` | **DOES NOT EXIST** |
| `staging/PHASE10_WORKSTREAM_C_REPORT.md` (pre-rename name) | **DOES NOT EXIST** |

There was never a **WSD**-named quota/connection report. The rename that took place was
`PHASE10_WORKSTREAM_C_REPORT.md` → `PHASE10_WSC_QUOTA_CONNECTION_SAFETY.md` — **WSC**, this
workstream. "WSD" in the brief appears to be a transcription of the neighbouring file
`staging/PHASE10_WSD_FINANCIAL_INTEGRITY.md`, which is **a different workstream's distinct
work** (Workstream D — Financial Integrity and Reconciliation: `fee_records.fee_pct`,
`referral_payouts` payout-method, migration `031`). Its first lines were read to confirm this.
**It was not touched, and must not be.**

Reference searches (repository-wide, **VERIFIED**):

| Search | Matches |
|---|---|
| `PHASE10_WSD_QUOTA_CONNECTION_SAFETY` | **0** |
| `PHASE10_WORKSTREAM_C_REPORT` | **0** — no stale references |
| `PHASE10_WSC_QUOTA_CONNECTION_SAFETY` | **5**, all resolving to the existing file |

The live references, re-checked **after** the §5.4 code change so the line numbers are the
current ones: `backend_app/core/subscription_dependencies.py:1069` (was `:980` before
`_release_slot_lock` was inserted above it), `backend_app/core/hard_quota_enforcer.py:58`,
`backend_app/backend/connection_engine.py:217`,
`tests/test_quota01_exchange_slot_is_atomic.py:40` and `:625`, plus one self-reference inside
the report itself. Each was verified by reading the cited line.

**Conclusion: the rename is COMPLETE and references are CONSISTENT. No correction was needed
and none was made. No report was deleted.** No unresolved ambiguity, other than the brief's
WSC/WSD mislabel, which this section resolves.

---

## 4. Files and diffs inspected

Read in the current working tree (not from the prior report):

| File | What was inspected |
|---|---|
| `backend_app/backend/connection_engine.py` | `_pool_key`, `_credential_identity`, the full reuse/rebuild block, `release_exchange`, `exchange_pool_size`, `close_all_exchanges` |
| `backend_app/routers/exchange.py` | `store_keys` pre-check + guard + eviction, `delete_connection` eviction, `reconnect_connection_by_id` eviction, import block |
| `backend_app/core/subscription_dependencies.py` | `_acquire_slot_lock`, `exchange_connection_slot_guard`, `check_exchange_connection_slot`, `enforce_quota`, release path |
| `backend_app/main.py` | lifespan shutdown ordering around the pool drain |
| `backend_app/core/hard_quota_enforcer.py` | module docstring, `DEPRECATION_MESSAGE`, `__init__` warning, lazy singleton |
| `backend_app/core/dag_task_queue.py`, `core/entitlement_engine.py` | removal of dormant constructions and imports |
| `backend_app/backend/exchange_websocket_listener.py` | concurrent-tab diff, for interaction with CONN-01-A |
| `migrations/003_create_exchange_keys_table.sql` | existing constraints on `exchange_keys` |
| `migrations/create_plan_tables.sql` | `plan_limits` DDL, for §10 |
| `backend_app/migrations/025_work03_bot_ownership_claims.sql` | the WORK-03 precedent, for §10 |
| `backend_app/core/subscription_engine.py` | `PLAN_LIMITS` provenance |

All six changed Python modules parse cleanly (`ast.parse`) — **VERIFIED**.

---

## 5. Credential isolation and client lifecycle findings

### 5.1 Credential fingerprinting and pool reuse

**Implementation demonstrates (VERIFIED by reading `connection_engine.py:140-181`).** The pool
key remains `sha256(json.dumps([user_id, exchange_id], sort_keys=True))` — the credential is
**not** in the key. Credential identity is held separately in `_pool_credential_id`, as
`sha256([api_key, secret_key, password])`, or `None` when neither key nor secret is supplied.
The reuse rule is:

```
wanted is None            -> return pooled          (no downgrade of an authenticated client)
wanted == held            -> return pooled          (one socket per pair; FIX CE-4 intact)
wanted != held            -> pop, close, rebuild    (rotation, or upgrade from unauthenticated)
```

The comparison and the rebuild are both inside `_pool_lock`, and both dicts are mutated in the
same critical section. A `connect()` that raises propagates with neither dict written.

**Tests demonstrate (VERIFIED by execution, 23 tests in
`tests/test_conn01_pool_lifecycle_and_shutdown.py`).** Credentialed caller is never served an
unauthenticated client; credential-less caller reuses and does not downgrade; ten alternating
calls construct exactly one client (anti-thrash); same credential reused for three callers with
one handshake; a passphrase-only change rebuilds; the fingerprint contains neither key nor
secret and is a 64-char hexdigest.

**Unverified.** That the fingerprint's non-reversibility is adequate against an attacker who
can already read process memory — out of scope, and no weaker than the plaintext the `ccxt`
client already holds. No cryptographic review was performed.

### 5.2 Tenant isolation

**VERIFIED.** Isolation is a property of `_pool_key` and is untouched by the credential work.
The adversarial case is tested directly: two tenants with **identical** credential fingerprints
still receive separate clients (`test_two_tenants_never_share_a_client_even_with_identical_credentials`),
and a rebuild is scoped to the rotating pair, so one tenant's rotation does not evict another's
client (`test_one_tenants_rotation_does_not_evict_another_tenants_client`). The key derivation
is pinned against an independently computed digest.

**No cross-tenant credential reuse path was found.** This is the stop condition from the
original brief and it is not met.

### 5.3 Reconnect and shutdown

**VERIFIED by reading.** `reconnect_connection_by_id` (`routers/exchange.py:1193`) now calls
`release_exchange` **before** delegating to `test_stored_connection`, wrapped in
swallow-and-warn. `delete_connection:717` and `store_keys:490` also evict.
`main.py` drains the pool at `:591-597` — after `yield` (`:509`), after
`app_state.fleet.shutdown_all()` (`:570`), before the Redis disconnect, failures logged not
raised. Ordering confirmed in source and asserted by
`test_the_lifespan_shutdown_drains_the_pool`.

**Tests demonstrate.** Drain closes and empties; drain continues past a client that raises on
close; drain is a no-op on an empty pool; a failing reconnect still evicts; pool size is bounded
by distinct pairs across 50 calls including rotations and a refused handshake.

**Unverified.** That a real `ccxt` client's `close()` releases every OS resource, and that a
real uvicorn shutdown reaches the lifespan block under SIGTERM. Both are asserted against the
in-process transport and by source inspection only.

### 5.4 Per-account Redis lock — DEFECT FOUND AND FIXED IN THIS CLOSEOUT

**Finding (VERIFIED).** `exchange_connection_slot_guard` generated a per-request ownership
token and wrote it as the lock's value, then released with an unconditional
`redis_manager.delete(lock_key)` and **never read the token back**. This is the classic unlock
defect: if a holder's critical section outlives `_SLOT_LOCK_TTL_SECONDS` (15 s), the key
expires, a waiting request legitimately acquires it, and the original holder's `finally` deletes
**that request's** lock — after which a third request can acquire while the second is still
inside its critical section. Two concurrent writers is precisely the overshoot the guard exists
to prevent, and it would have been silent.

Probability is low (the critical section is one `exchange_keys` read plus one upsert against a
15-second TTL) but not theoretical: a GC pause or a stalled database call is sufficient.

**Correction applied.** New `_release_slot_lock(lock_key, token, user_id)` reads the holder back
and deletes **only** on an exact token match. It returns one of four outcomes, and every outcome
except `released` declines to delete, so the unsafe direction is unreachable from any of them:

| Outcome | Meaning | Action |
|---|---|---|
| `released` | this request still held it | deleted |
| `absent` | nothing came back | not deleted; logged ERROR |
| `not-owner` | a different request holds it | not deleted; logged ERROR |
| `unverified` | the store raised | not deleted; left to the TTL |

**A second, smaller over-claim was caught by the new test and corrected.** The first version of
this fix reported `expired` and logged "the critical section outlived the TTL". That asserts a
cause the code cannot know: `redis_manager.get` is **fail-soft** and returns `None` both for a
key the TTL removed and for a cache it could not reach. The outcome is now named `absent` and
the log states both possibilities explicitly. This is the same facade property that made
`HardQuotaEnforcer` fail open; it is survivable here only because every branch declines to
delete.

**Honest limitation (recorded in the code and in §13).** Read-then-compare-then-delete is **not
atomic**. A key expiring between the read and the delete can still be mis-deleted. The atomic
form is a Lua compare-and-delete, which is **not portable in this repository**: the cache
facade's `eval` is a purpose-built idempotency-script emulator
(`core/cache/redis_manager.py:214`), not a general interpreter, so a Lua release would behave
differently in the development lane than in production. A lock that behaves differently per
environment was judged worse than one with a narrow, documented race.

**A `not-owner` or `absent` result also means mutual exclusion was already lost before the
release.** The guard cannot un-write the row it wrapped, so both are logged at ERROR naming the
possibility that the account's connection limit was exceeded. That is an operator signal, not a
tidy-up detail, and it is the honest limit of an advisory lock.

### 5.5 Connection-limit enforcement under concurrency

**Implementation.** Two checks, deliberately: a fast pre-check
(`check_exchange_connection_slot`, `routers/exchange.py:388`) that refuses an over-limit account
*before any credential is sent to a venue*, and the control
(`exchange_connection_slot_guard`, `:428`) that re-reads under the lock and holds it across the
vault write at `:430`. **VERIFIED** by reading, and the ordering (guard before write, pre-check
before `ConnectionEngine(`) is asserted by
`test_store_keys_uses_the_guard_around_its_vault_write`.

**Tests demonstrate (in-process substrate only).** Two concurrent stores of different venues
admit exactly one against a limit of 1; eight concurrent stores admit exactly 5 against a limit
of 5; concurrent rotations of one venue are never refused as over-limit; different accounts do
not contend; one tenant cannot consume another's capacity.

**Two negative controls prove the suite can fail**: neutering `nx` and neutering the re-read
each reproduce the overshoot, and the restored guard is then bounded again. A third negative
control now covers the unlock fix (§5.4).

**Unverified.** Cross-process atomicity — see §8. The in-process `_LockStore` has real `SET NX`
semantics but two coroutines are not two uvicorn workers.

### 5.6 Fail-closed behaviour

**VERIFIED by execution.** Lock store unreachable → 503 `ENTITLEMENT_SLOT_LOCK_UNAVAILABLE`,
write did not run. Lock store raises → same. Contended beyond the wait budget → 409
`EXCHANGE_CONNECTION_CHANGE_IN_FLIGHT`. Unreadable usage figure → 503
`ENTITLEMENT_USAGE_UNREADABLE`, asserted behaviourally (not from a comment) in
`test_the_enforced_path_is_the_one_that_fails_closed`. Lock released on the guard's own
refusal, on an exception inside the guard, and on a failed write — with no capacity consumed.

**Consequence to flag: Redis is now on the critical path for `POST /api/exchange/keys`.** With
the lock store unreachable that endpoint answers 503. Consistent with API-04's retained
fail-closed decision, but it is a **new availability dependency on one endpoint**. In
development `DEV_MODE` supplies the in-memory client. **DECISION** — support/ops should be told
before launch.

### 5.7 Secret handling and log safety

**VERIFIED by execution.** `test_no_credential_appears_in_pool_log_output` exercises create,
rebuild, release and drain, then greps every captured log record for five planted secrets **and**
for the credential fingerprint. `test_a_failed_rebuild_does_not_put_the_credential_in_the_exception`
checks the propagating exception text. The drain logs only a 12-character prefix of the pool-key
digest, which names neither user nor venue.

**Unverified.** Third-party log output. A real `ccxt` traceback can include frames holding the
client `config`, which contains `apiKey`. Pre-existing, out of scope, and noted in the prior
report.

### 5.8 `HardQuotaEnforcer` deprecation

**VERIFIED by execution, 7 tests.** Construction emits a `DeprecationWarning` whose message
names `subscription_dependencies`, `redis`, `fail`-open and `TenantQuota`; importing the module
does **not** warn (the singleton is lazy via module `__getattr__`) while touching the singleton
does; an AST sweep of `backend_app/` finds **zero** constructions; the two removed attributes
have no remaining readers; the fail-open premise is pinned at **both** ends — this module still
reads `redis_manager`, and `redis_manager.get`/`incr` still swallow.

---

## 6. CONN-01 evidence and closure recommendation

| Acceptance criterion | Evidence | Status |
|---|---|---|
| Credential rotation explicitly tested | 4 router tests + 2 primitive tests (incl. passphrase-only) | **VERIFIED** |
| Tenant/account connection isolation tested | identical-fingerprint, scoped-rebuild, pinned key derivation | **VERIFIED** |
| Failure and shutdown cleanup tested | failed rebuild leaves nothing; close-raises tolerated; drain closes/empties/continues-past-failure/no-ops; lifespan wiring + ordering asserted | **VERIFIED** |
| Diagnostic output checked for secret leakage | log sweep over 4 code paths + exception text | **VERIFIED** |
| Concurrent creation safe | 8 racers with a suspending handshake → exactly one client, one close | **VERIFIED** |
| Connection counts bounded | 50 calls / 3 pairs → 3 entries; refused handshake adds none | **VERIFIED** |

### Recommendation: **READY FOR COORDINATOR CLOSURE APPROVAL**, with the residual limitations below.

All four stated acceptance criteria are met with targeted, executed proof, and the two
additional lifecycle properties from the original brief are met as well. The finding as
originally written (a rotation ineffective until restart) is closed at **two** independent
layers — the router eviction and, more importantly, the pool primitive, so a future second
rotation path that forgets to evict cannot resurrect it.

**Residual limitations that closure should record:**

1. **Venue behaviour is NOT VERIFIED** (§9). Every test replaces the venue client class. These
   claims are about in-process pool bookkeeping and eviction, not about whether any exchange
   accepts, rejects or revokes a key.
2. **No real shutdown was exercised.** Lifespan wiring and ordering are established by source
   inspection and a source-reading test, not by a SIGTERM against a running uvicorn.
3. **No pool TTL or idle eviction.** Bounded by distinct (user, venue) pairs — tested — but a
   long-lived process retains one entry per pair that ever connected, each holding a plaintext
   credential. Adding a ceiling is a product decision (**DECISION**), not a bug fix.
4. **Credential-less callers remain credential-less.** `consistency_checker.py:319` and
   `exchange_websocket_listener.py:206` still pass no credential. That is now *safe* — they can
   no longer poison a credentialed caller — but they will still pool an unauthenticated client
   if they arrive first, and their own private calls, if any are later added, would fail. Worth
   a follow-up ticket; not a CONN-01 blocker.

CONN-01 is **not** marked closed in any shared registry by this document.

---

## 7. QUOTA-01 open issues — remains **OPEN**

QUOTA-01 stays open. Enforcement is real, wired and fail-closed, and the connection-limit race
is repaired in-process, but the following are outstanding.

| # | Open item | Status | Minimum evidence / decision needed to reconsider |
|---|---|---|---|
| 1 | Real-Redis concurrency unverified | **UNVERIFIED** (§8) | Run the two gated tests against an isolated Redis; record output. Command in §8 |
| 2 | No durable DB-level connection-limit backstop approved or applied | **DECISION** (§10) | Coordinator approval of one proposal variant, then a migration authored and applied by the owning workstream |
| 3 | Existing over-limit accounts | **UNVERIFIED** | A read-only count per §10.2. No database is reachable from this environment |
| 4 | Plan-downgrade behaviour | **DECISION** | Today a downgrade leaves stored credentials alone and only blocks the next add. Needs an explicit product ruling |
| 5 | Order-rate, daily-trade, active-order ceilings absent on the wired path | **REPORTED**, unchanged | A product decision on which dimensions to enforce, then implementation on the fail-closed path — **not** by reviving `HardQuotaEnforcer` |
| 6 | Strategy-count race | **REPORTED**, unchanged | Same read-then-write shape; P3, one row, no money. Owned by `routers/strategies.py` / `library.py`, outside this workstream |
| 7 | Plan-limit authority duplicated between `TenantQuota` and `SubscriptionEngine._PLANS` | **REPORTED**, now better characterised | A decision on one plan ladder. See the correction below |
| 8 | Advisory-lock release is not atomic | **VERIFIED** limitation (§5.4) | Superseded by item 2; a DB constraint makes the lock an optimisation rather than the control |

**Correction carried forward and independently confirmed in this closeout.** The audit records
item 7 as a "duplicate quota catalogue". Checked: `TenantQuota` (`core/tenant.py`) and
`SubscriptionEngine._PLANS` share **zero field names**. `PLAN_LIMITS`
(`subscription_engine.py:1150`) is a *derived* index of `_PLANS`, not a third copy. What is
duplicated is the plan **ladder** and the authority to set limits, not the numbers — and every
trading dimension in `TenantQuota` (orders/min, daily trades, active orders, positions, capital,
leverage, concentration) has **no counterpart at all** in the subscription catalogue. So
"de-duplicate the catalogues" would *delete* dimensions nothing else covers. The decision is now
documented in `hard_quota_enforcer.py`'s docstring; choosing one ladder remains open.

---

## 8. Real-Redis concurrency verification — **NOT VERIFIED**

Availability probe (**VERIFIED**):

| Check | Result |
|---|---|
| `AERORA_TEST_REDIS_URL` | unset |
| `REDIS_URL` | unset |
| TCP 127.0.0.1 : 6379 / 6380 / 6381 | all closed (`TimeoutError`) |
| `redis-server`, `redis-cli`, `docker`, `podman`, `memurai` on PATH | all **absent** |
| `wsl` | present (Ubuntu-24.04); `command -v redis-server` inside it → `NO_REDIS_SERVER` |

**No isolated test Redis exists in this environment, and none was started or installed.**
Installing `redis-server` into the WSL distribution would be installing infrastructure, which
this closeout is not authorised to do.

Gate command and actual outcome (**VERIFIED**, recorded in
`staging/p10c_redis_gate_NOTVERIFIED.txt`):

```
pytest tests/test_quota01_exchange_slot_is_atomic.py \
       tests/test_quota01_metered_reservation_is_hard.py \
       -p no:randomly -q --no-header -p no:warnings -rs -k "real_redis" --timeout=300

-> 3 skipped, 26 deselected in 12.60s
   SKIPPED test_quota01_exchange_slot_is_atomic.py:644  (slot lock mutual exclusion)
   SKIPPED test_quota01_exchange_slot_is_atomic.py:700  (guard admits exactly the limit)
   SKIPPED test_quota01_metered_reservation_is_hard.py:224 (metered reservation atomicity)
```

**Three skips. A skip is not a pass.** The two slot-lock variants drive the production
`_acquire_slot_lock` and the real guard against a real client; the metered one pre-dates this
workstream.

To close, with an isolated instance on a non-default port and a dedicated database index:

```
AERORA_TEST_REDIS_URL=redis://127.0.0.1:6399/15 pytest \
  tests/test_quota01_exchange_slot_is_atomic.py \
  tests/test_quota01_metered_reservation_is_hard.py \
  -p no:randomly -q -rs -k "real_redis" --timeout=300
```

**What remains unproven without it:** that `SET NX` excludes across processes under this
facade; behaviour under failover or `maxmemory` key eviction (which would break mutual exclusion
outright); and the §5.4 read-then-delete window against real latency.

---

## 9. Exchange-venue verification — **NOT VERIFIED**

No venue was contacted and no real credential used. Every pool and router test substitutes the
venue client class with an in-process transport that receives the same assembled config dict.
`tests/exchange_sandbox/` remained gated (`AERORA_EXCHANGE_SANDBOX_CERT` unset); its 16 tests
skipped, and its collection gate was previously confirmed correctly scoped so it does not
suppress other suites.

Specifically **not** established: that a rotated key is accepted by any venue; that a superseded
key is rejected; that revocation at the venue is observable in-process; that a real `ccxt`
`close()` frees every resource; that reconnect succeeds against a live endpoint.

Keeping this separate from §8 is deliberate: a Redis result would say nothing about a venue, and
vice versa.

---

## 10. Database constraint proposal and pending decisions — **REVIEW ONLY**

No migration was written or applied. No remediation or downgrade policy is chosen here.

### 10.1 The invariant

> For every account, the number of distinct `exchange_keys` rows must not exceed that
> account's **effective** exchange-connection allowance.

### 10.2 Why this is materially harder than the WORK-03 precedent

`025_work03_bot_ownership_claims.sql` is the natural model, and its own header argues the case
for a database control: a Redis `SET NX` is "the obvious reach, and it is not sufficient on its
own here", because the cache facade is deliberately fail-soft. That reasoning applies directly
to §5.4.

But the two invariants differ in kind:

* WORK-03's is **plan-independent** — at most one owner per `(user_id, symbol)`. A `PRIMARY KEY`
  expresses it completely, with no knowledge of billing.
* The connection limit is **plan-dependent, and per-account overridable**. The effective
  allowance is `SubscriptionEngine.get_effective_quotas(plan, overrides)` — the catalogue in
  `_PLANS`, **widened by `profiles.plan_limit_overrides`** (JSONB, read at
  `subscription_dependencies.py` via `_PROFILE_COLUMN_SETS`).

Consequences the coordinator must weigh:

* A `plan_limits` table **does exist** (`migrations/create_plan_tables.sql`:
  `plan_id → plans(id)`, `resource_type`, `limit_value`, `limit_type DEFAULT 'hard'`,
  `UNIQUE(plan_id, resource_type)`) and is populated. **The enforcement path never reads it** —
  limits come from code. A trigger that read it would make the database a *second authority*,
  which must then be kept in step with `_PLANS`. That is QUOTA-01 item 7 made worse, not better.
* A trigger that ignored `plan_limit_overrides` would **refuse capacity a customer has
  contractually been granted** — a revenue-affecting false refusal.

### 10.3 Options (for coordinator selection — no recommendation is binding)

| Option | Mechanism | Pros | Cons |
|---|---|---|---|
| **A. Slot-claim table** | `exchange_connection_claims(user_id, slot_index)`, `PRIMARY KEY (user_id, slot_index)`; the writer claims the lowest free index inside the same transaction as the key write | Plan-**independent** at the DB layer, exactly the WORK-03 shape; no limit duplicated into SQL | The *number* of slots is still decided in application code; enforces "no two writers take slot N", not "N ≤ limit". Needs the claim and the key write to share one transaction |
| **B. Trigger reading `plan_limits`** | `BEFORE INSERT` on `exchange_keys` counting rows and comparing against a join through `profiles → plans → plan_limits` | Enforces the real inequality in the database | Second source of truth; must also honour `plan_limit_overrides`; a plan-table drift silently changes enforcement; Supabase RLS/`SECURITY DEFINER` interaction needs care |
| **C. Materialised allowance column** | `profiles.exchange_connection_allowance`, written by the billing path; trigger compares against it | One authority (billing writes it), cheap check, overrides naturally included | A new write path to get wrong; stale on plan change unless billing is reliable |
| **D. Keep advisory lock only** | status quo after §5.4 | No migration risk | Not durable; breaks under failover or key eviction; §5.4 race remains |

### 10.4 Decisions required before any migration

1. **Which option** (A / B / C / D).
2. **Existing over-limit accounts** — grandfather, or force remediation? **UNVERIFIED how many
   exist**: no `AERORA_TEST_DATABASE_URL`, `DATABASE_URL` or `SUPABASE_DB_URL` is set in this
   environment, so no count could be taken. Needed first, read-only:
   ```sql
   -- review-only; not executed here
   SELECT user_id, COUNT(DISTINCT exchange_id) AS connections
   FROM exchange_keys GROUP BY user_id HAVING COUNT(DISTINCT exchange_id) > 1
   ORDER BY connections DESC;
   ```
   A constraint added with violating rows present will **fail to validate**, or will lock those
   accounts out of key **rotation** — which is a security-relevant operation and the last thing a
   plan limit should obstruct.
3. **Plan-downgrade semantics** — today a downgrade leaves stored credentials alone and only
   prevents the next add. A DB constraint makes that state permanently un-writable, so rotation
   for a downgraded over-limit account must be explicitly allowed or explicitly refused.

### 10.5 Migration, rollback, concurrency and outage interaction

* **Migration risk.** `exchange_keys` carries RLS and an owner policy; a trigger must be
  `SECURITY DEFINER`-reviewed or it will evaluate under the caller's row visibility and
  undercount. Options B and C change insert behaviour for *every* writer, including the vault and
  any admin tooling.
* **Rollback.** Dropping a trigger or a claim table is straightforward and non-destructive;
  rows written while it was active remain valid. Option C's new column would be left in place.
* **Concurrency.** Options A and C are decided by the engine under the insert's own lock, so they
  hold without cooperation from any application process — the property the advisory lock lacks.
  Option B's `COUNT` needs the right isolation level or `SELECT ... FOR UPDATE` on the owner row,
  or it races in exactly the way the application code did.
* **Interaction with a Redis outage and fail-closed API behaviour.** This is the strongest
  argument for doing it. With a durable constraint, a Redis outage can degrade the advisory lock
  to an *optimisation* rather than the control, which would let `POST /api/exchange/keys` be
  reconsidered as available-but-serialised-by-the-database instead of 503 (§5.6). That is a
  second coordinator decision and should not be assumed.

---

## 11. Tests run and existing test evidence

**Existing evidence, used as reported and not re-run** (the brief's instruction):
`4,619 passed, 79 skipped, 3 xfailed, 0 failed` across the 92 affected files.
**REPORTED.** It does **not** establish real-Redis concurrency safety, venue connectivity, or
live-trading readiness.

**Run in this closeout**, each with a bounded `--timeout` and a specific reason:

| # | Command (abbreviated) | Reason | Result |
|---|---|---|---|
| 1 | `pytest tests/test_quota01_exchange_slot_is_atomic.py --timeout=300` | validate the §5.4 lock-ownership fix | **1 failed, 19 passed, 2 skipped** — the new `unverified` assertion failed, exposing the over-claim in §5.4 |
| 2 | same, after correcting the outcome naming | confirm the correction | **20 passed, 2 skipped** |
| 3 | `pytest -k real_redis ... -rs --timeout=300` | establish the §8 gate state | **3 skipped** (not passed) |
| 4 | `pytest <4 exchange-router / entitlement caller suites> --timeout=300` | the fix touches `store_keys`' call path | **123 passed** |
| 5 | `pytest <all 7 workstream suites> --timeout=300` | regression after the fix | **86 passed, 3 skipped** (`staging/p10c_suite_WSC5.txt`) |
| 6 | `ast.parse` over the 6 changed modules | syntax integrity | all **OK** |

Run 1 is worth keeping on the record: the first version of the fix asserted a cause the code
could not know, and the test caught it. Suite total rose from 82 to 86 with the four new
ownership tests, one of which is a negative control that restores the bare delete and confirms
the defect reproduces.

The 92-file suite was **not** re-run. The change is additive and confined to
`subscription_dependencies.py`'s release path; runs 4 and 5 cover its callers and its own
suites, which is proportionate to a one-function change.

---

## 12. Files changed by this closeout

| File | Change |
|---|---|
| `backend_app/core/subscription_dependencies.py` | Added `_release_slot_lock`; `exchange_connection_slot_guard`'s `finally` now calls it instead of a bare `delete`. `git diff --numstat` vs `HEAD` is **`253 / 0`** for this file — still purely additive, no pre-existing line altered or removed; this closeout contributed roughly 80 of those 253 added lines |
| `tests/test_quota01_exchange_slot_is_atomic.py` | **+4 tests** (section 7: successor-lock preservation, normal release, all four outcomes, negative control) and `raise_on_get` on the lock-store double. 20 tests + 2 gated |
| `staging/PHASE10_WSC_COORDINATOR_REVIEW.md` | **new** — this document |
| `staging/p10c_redis_gate_NOTVERIFIED.txt` | **new** — §8 gate evidence |
| `staging/p10c_suite_WSC5.txt` | **new** — post-fix suite evidence |

**Not modified by this closeout:** `staging/PHASE10_WSC_QUOTA_CONNECTION_SAFETY.md`,
`staging/PHASE10_WSD_FINANCIAL_INTEGRITY.md`, `staging/FINAL_LAUNCH_SCORECARD.md`,
`staging/full_audit_results.json`, any migration, and every file belonging to another
workstream. No file was renamed or deleted. `HEAD` unchanged.

---

## 13. Blockers and recommended next actions

### Blockers to QUOTA-01 closure (not to CONN-01)

1. **Real-Redis concurrency NOT VERIFIED.** Needs an isolated instance and the §8 command.
2. **No durable DB backstop.** Needs a §10.3 option selected, then a migration by the owning
   workstream.
3. **Over-limit account population unknown.** Needs the §10.4 read-only count.
4. **The advisory-lock release is not atomic** (§5.4). Narrowed, not eliminated. Resolved
   properly by blocker 2, not by more application code.

### Not blockers, but should be recorded

5. Redis is now a hard dependency of `POST /api/exchange/keys` (503 when unreachable).
6. No pool TTL or idle eviction.
7. Credential-less callers still pool unauthenticated clients when they arrive first — safe, but
   worth a follow-up ticket.

### Coordinator decisions required

| # | Decision |
|---|---|
| D1 | Approve CONN-01 closure on the §6 evidence, accepting that venue behaviour is NOT VERIFIED |
| D2 | Select a durable connection-limit option (§10.3 A/B/C/D) |
| D3 | Rule on existing over-limit accounts — grandfather or remediate (§10.4.2) |
| D4 | Rule on plan-downgrade semantics for over-limit accounts (§10.4.3) |
| D5 | Authorise an isolated test Redis so §8 can be closed |
| D6 | Accept or revisit the new 503 availability dependency on `POST /api/exchange/keys` (§5.6) |
| D7 | Decide which, if any, of the §7 item 5 trading dimensions to enforce, and on which path |
| D8 | Choose one plan ladder, resolving §7 item 7 |

### Recommended order

D5 (cheap, unblocks blocker 1) → D1 (CONN-01 can close independently) → D3 (data before design)
→ D2 and D4 together → D6 as a consequence of D2 → D7 and D8 as separate product work.

---

## 14. Closeout summary

* **Rename:** complete. `PHASE10_WSC_QUOTA_CONNECTION_SAFETY.md` exists; the pre-rename name has
  **zero** remaining references; `PHASE10_WSD_QUOTA_CONNECTION_SAFETY.md` **never existed**. The
  brief's "WSD" was a mislabel for Workstream D's distinct financial-integrity report, which was
  left untouched. No ambiguity remains.
* **CONN-01:** **ready for coordinator closure approval**, with venue behaviour NOT VERIFIED and
  three further residual limitations recorded. Not closed in any registry here.
* **QUOTA-01:** **OPEN**, with four blockers and eight coordinator decisions enumerated.
* **Real-Redis concurrency tests:** did **not** run. No Redis, no `redis-server`, no Docker, and
  installing one is unauthorised. Three gated tests skipped; a skip is not a pass.
* **Exchange-venue behaviour:** **NOT VERIFIED.** No venue contacted, no real credential used.
* **Live trading:** OFF throughout. Nothing committed, pushed or deployed. No shared registry,
  scorecard or migration touched.
