# PHASE 10 — WORKSTREAM K: QUOTA-01 REAL-REDIS VERIFICATION AND DECISION PREPARATION

Primary reference: `staging/PHASE10_WSC_COORDINATOR_REVIEW.md`

**QUOTA-01 is NOT closed by this document.** The real-Redis gate did not run.

**Label key.** **VERIFIED** = checked here by reading code or by a command whose output is
recorded. **REPORTED** = carried from a prior workstream, re-checked for presence but not
re-derived. **NOT VERIFIED / BLOCKED** = cannot be established here; a skip is not a pass.
**DECISION** = awaiting the coordinator.

---

## 0. Headline

The gate is still **BLOCKED**, but the reason has changed and is now much more actionable than
"no Redis on this machine".

1. **No authorized isolated Redis exists in this environment** (**VERIFIED**). Nothing
   configured, nothing reachable, no `redis-server`/`redis-cli`/`docker`/`podman`. The
   provisioning runbook documents a project-local Redis at `.\staging\redis\redis-server.exe`,
   but **that binary is not present** — `staging/redis/` is absent. Obtaining it is an
   installation, which this workstream is not authorized to perform.

2. **CI already runs an isolated Redis — and it still cannot close this gate** (**VERIFIED**,
   and this is the finding worth acting on). `.github/workflows/01-pr-check.yml`'s `unit-tests`
   job provisions `redis:7-alpine`, maps 6379, health-checks it, and exports
   `REDIS_URL: redis://localhost:6379` to a `pytest tests/ …` step that **does** select these
   tests. It looks sufficient. It is not: `tests/conftest.py` line 13 executes
   `os.environ["REDIS_URL"] = ""` **unconditionally at import time**, before any test module is
   imported, so the gate expression resolves to `''` and the tests skip in CI exactly as they
   do here. Measured in §2.3.

3. **A pre-existing test has therefore been dormant since it was written** (**VERIFIED**).
   `tests/test_quota01_metered_reservation_is_hard.py`'s real-Redis variant uses the same gate
   shape and the same two variable names, so it has never executed in CI either. The repository
   has been carrying it as evidence-in-waiting without that being visible.

4. **One-line fix, but it is a CI change and not mine to make.** Setting
   `AERORA_TEST_REDIS_URL` on the `unit-tests` job closes all three gated tests with **no new
   infrastructure** — `conftest.py` does not blank that name. **DECISION D5′** in §7.

5. **My earlier WSC claim about Lua was too strong and is corrected here** (§4). This repository
   already has a reviewed pattern for using Lua safely alongside stub evaluators, and its
   docstring explicitly refuses the `GET`+`DEL` approximation that `_release_slot_lock` currently
   performs.

---

## 1. Current baseline and ownership boundaries

### 1.1 Repository state (**VERIFIED**, not taken from the prior report)

```
git rev-parse HEAD              074b772ab16cece62cbfca0f2b78497be2ae56d5   (unchanged)
git rev-parse --abbrev-ref HEAD main
git status --short               247 entries at the start, 249 at the end
                                 (246 at the end of the WSC review)
```

The additional entries arrived from other tabs while this review ran; a new
`staging/PHASE10_P23_DRAW_ORDER_REPORT.md` is among them, and the shared finding registry and
scorecard were also rewritten by a concurrent tab — see the note in §8. Nothing was reset,
stashed, cleaned, reverted, committed or pushed by this workstream.

### 1.2 The ownership-checked release is still present (**VERIFIED**)

| Element | Location |
|---|---|
| `_acquire_slot_lock` | `backend_app/core/subscription_dependencies.py:907` |
| `_release_slot_lock` | `:946` |
| Four outcomes `released` / `absent` / `not-owner` / `unverified` | `:999`, `:1011`, `:1020`, `:1031`, `:1033` |
| Wired into the guard's `finally` | `:1123` |

`git diff --numstat` for that file is **`253 / 0`** — identical to the figure in the WSC review,
confirming **no code was edited by this workstream** (§8).

### 1.3 The regression tests are still present (**VERIFIED** by name and by execution)

| Test | Line |
|---|---|
| `test_the_guard_does_not_delete_a_successors_lock_when_its_own_expired` | `:761` |
| `test_a_normal_exit_releases_the_lock` | `:782` |
| `test_release_reports_each_ownership_outcome` | `:800` |
| `test_a_bare_delete_on_release_fails_this_suite` (negative control) | `:840` |
| `test_the_slot_lock_is_mutually_exclusive_against_real_redis` (gated) | `:646` |
| `test_the_guard_admits_exactly_the_limit_against_real_redis` (gated) | `:702` |

Executed outcome, **not** assumed from the prior report:

```
pytest tests/test_quota01_exchange_slot_is_atomic.py \
       tests/test_quota01_metered_reservation_is_hard.py \
       tests/test_quota01_enforcement_is_wired.py \
       tests/test_quota01_hard_enforcer_is_deprecated_in_code.py \
       -p no:randomly -q --no-header -p no:warnings --timeout=300

-> 50 passed, 3 skipped in 76.75s     EXIT=0
```

The 3 skips are the real-Redis gates. **A skip is not a pass.**

### 1.4 Ownership boundaries observed

No connection-pool code touched (§6). No facade change, no Lua introduced (§4). No migration
(§5). No plan limit, entitlement or downgrade behaviour altered. No shared scorecard or finding
registry edited. The full repository suite was not run.

---

## 2. Redis availability and authorization evidence

Probe: `staging/probe_p10k_redis_authorization.py` → `staging/p10k_redis_authorization.txt`.
Read-only; no credential, token or full connection string is printed anywhere, and a single
connect attempt per endpoint with **no retries**.

### 2.1 Configuration and reachability (**VERIFIED**)

| Check | Result |
|---|---|
| Process env var names containing `REDIS` | **NONE** |
| `AERORA_TEST_REDIS_URL`, `REDIS_URL`, `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `CELERY_BROKER_URL` | all **unset** |
| `.env` / `.env.example` | declare `REDIS_URL` (and `REDIS_REPLICA_URL`, `REDIS_PASSWORD` in the example). **Names only were read; no value was opened or echoed** |
| TCP 127.0.0.1 : 6379 / 6380 / 6399 | all **closed** (`TimeoutError`), one attempt each |
| `redis-server`, `redis-cli`, `docker`, `podman`, `memurai` | all **absent** |
| `wsl` | present (Ubuntu-24.04); `command -v redis-server` inside it → `NO_REDIS_SERVER` |
| `redis-py` client library | **installed, 5.0.8** — so the client is not the blocker |

### 2.2 Approved project infrastructure (**VERIFIED**)

* `DATABASE_PROVISIONING_RUNBOOK.md` documents the approved local pattern:
  `.\staging\redis\redis-server.exe --port 6379 --appendonly no`, under
  *"# 3. Redis, for the tests that need a real one"*. **`staging/redis/` does not exist** and no
  `redis-server*` binary exists anywhere in the checkout. `.gitignore` excludes `redis/`, so a
  prior phase fetched it ad hoc and it did not survive. Re-obtaining it is an **installation**.
* `docker-compose.yml` defines a `redis` service (14 mentions, `redis://redis:6379/0|1|2`), but
  there is **no Docker runtime** on this machine.
* `terraform/` declares managed Redis (`redis.tf`, `security_groups.tf`, `existing_vpc.tf`,
  `outputs.tf`, `variables.tf`). That is **production infrastructure and is explicitly not an
  authorized test target**. It was not contacted and no endpoint from it was resolved.
* `.github/workflows/01-pr-check.yml` — see §2.3.

### 2.3 The CI Redis service cannot close this gate (**VERIFIED** — the key measurement)

The `unit-tests` job (`01-pr-check.yml`) has:

```yaml
services:
  redis:
    image: redis:7-alpine
    ports: [ "6379:6379" ]
    options: --health-cmd "redis-cli ping" --health-interval 10s --health-timeout 5s
...
- name: Run Unit Tests
  run: pytest tests/ -v --tb=short -k "not chaos and not load" -m "not frontend_bundle"
  env:
    REDIS_URL: redis://localhost:6379
```

The selector **does** include these tests. The service **is** healthy and mapped. And yet:

`tests/conftest.py:12-13`

```python
# Clear REDIS_URL to force in-memory rate limiting even if set in environment
os.environ["REDIS_URL"] = ""
```

Unconditional, at import time, before any test module is imported. The gate expression in both
test files is:

```python
_REAL_REDIS_URL = os.environ.get("AERORA_TEST_REDIS_URL") or os.environ.get("REDIS_URL") or ""
```

Measured (`staging/probe_p10k_gate_predicate.py` → `staging/p10k_gate_predicate.txt`), with no
network involved — it only inspects environment state after importing the real conftest:

| Case | `REDIS_URL` before → after | `AERORA_TEST_REDIS_URL` | gate value | tests run? |
|---|---|---|---|---|
| **A — CI's actual config** | `redis://localhost:6379` → `''` | `''` | `''` | **NO** |
| **B — the working lever** | `redis://localhost:6379` → `''` | `redis://localhost:6379/15` (survives) | `redis://localhost:6379/15` | **YES** |
| **C — this machine** | `''` → `''` | `''` | `''` | **NO** |

Confirmed end-to-end with pytest under Case A's exact environment
(`staging/p10k_ci_env_still_skips.txt`) — the tests skip, so nothing dialled a server:

```
REDIS_URL=redis://localhost:6379 pytest <both files> -rs -k "real_redis" --timeout=120
-> 3 skipped, 26 deselected in 12.31s     EXIT=0
```

Two consequences:

* The CI Redis service is **not** evidence for this gate and never has been.
* `tests/test_quota01_metered_reservation_is_hard.py:224` uses the same gate shape, so the
  **pre-existing** metered-reservation real-Redis proof has also **never executed in CI**. It has
  been carried as evidence-in-waiting since it was written. Worth the coordinator's attention
  independently of QUOTA-01.

`conftest.py`'s blanking is **not** a defect — it exists so 11,000+ tests cannot be
accidentally re-pointed at a live cache, and `core/rate_limit.py` raises at import when
`REDIS_URL` is absent outside dev. `AERORA_TEST_REDIS_URL` is the repository's existing
"this one is deliberate" convention (the same shape as `AERORA_TEST_DATABASE_URL`). Nothing
needs redesigning; one job needs one variable.

### 2.4 Gate status

```
QUOTA-01 real-Redis gate: BLOCKED / NOT VERIFIED
```

Not attempted against any endpoint, because none authorized exists. No endpoint was retried.
No Redis was installed or started. No Redis database was flushed or written to.

---

## 3. Actual real-Redis test results

**None. The gate did not execute.** Three tests remain gated and skipped:

| Test | Location | State |
|---|---|---|
| `test_the_slot_lock_is_mutually_exclusive_against_real_redis` | `test_quota01_exchange_slot_is_atomic.py:646` | **SKIPPED** |
| `test_the_guard_admits_exactly_the_limit_against_real_redis` | `:702` | **SKIPPED** |
| `test_concurrent_reservations_are_atomic_against_real_redis` | `test_quota01_metered_reservation_is_hard.py:224` | **SKIPPED** |

Exact command, exit code and counts are in §2.3. **3 skipped, 26 deselected, exit 0.**

Therefore, all **NOT VERIFIED**:

* cross-process `SET NX` exclusion through this facade;
* TTL behaviour on a real server (expiry timing, `TTL` readback);
* bounded admission end-to-end against a real lock;
* behaviour under failover or `maxmemory` key eviction;
* the §4 read-then-delete window against real network latency.

**No test keys were created, so no cleanup was required.** No `FLUSHDB`/`FLUSHALL` was issued
at any point. The gated tests are written to delete only their own `q01-slot-*` /
`q01-slot-e2e-*` keys and to close the client in a `finally`, but none of that ran.

### What would close it

```
# Option 1 -- CI, no new infrastructure (preferred; see D5' in §7)
# add to the `unit-tests` job env in .github/workflows/01-pr-check.yml:
#     AERORA_TEST_REDIS_URL: redis://localhost:6379/15

# Option 2 -- a local authorized instance, per the runbook's documented pattern
.\staging\redis\redis-server.exe --port 6399 --appendonly no --save ""
$env:AERORA_TEST_REDIS_URL = "redis://127.0.0.1:6399/15"
pytest tests/test_quota01_exchange_slot_is_atomic.py \
       tests/test_quota01_metered_reservation_is_hard.py \
       -p no:randomly -q -rs -k "real_redis" --timeout=300
```

Database index **15** on a dedicated port, so nothing shared is touched.

### Authorization and infrastructure required

| Need | Detail |
|---|---|
| **Either** a CI workflow edit | one `env:` line on `unit-tests`. Requires authorization to modify `.github/workflows/01-pr-check.yml` and to commit — both outside this workstream |
| **Or** a local Redis binary | fetch `redis-server.exe` into `staging/redis/` (gitignored). This is an **installation** and needs explicit authorization |
| **Not acceptable** | the Terraform-managed/ElastiCache instance. Production, shared, and out of bounds |

---

## 4. Lock-release atomicity assessment

### 4.1 Still non-atomic (**VERIFIED**)

`_release_slot_lock` (`subscription_dependencies.py:946`) performs three separate awaited
operations: `redis_manager.get(lock_key)` → compare to `token` in Python →
`redis_manager.delete(lock_key)`. A key expiring between the read and the delete can still be
mis-deleted. **The limitation recorded in the WSC review stands, unchanged.**

The facade is unchanged by the concurrent edits to `redis_manager.py` in other tabs
(**VERIFIED**): no signature change to `get`/`set`/`delete`/`incr`/`eval`/`getdel`, and the
manager's `get` (`:757`) and `delete` (`:777`) still catch without re-raising. So the
`absent` outcome still conflates "the TTL fired" with "the cache is unreachable", exactly as
the code's own docstring says.

### 4.2 A correction to my earlier WSC conclusion

The WSC review stated that a Lua compare-and-delete "is **not portable** in this repository"
because the cache facade's `eval` is a purpose-built idempotency emulator. **That was too
strong, and the record should show it.**

Both halves of the evidence:

* The overstatement is grounded. `MockRedisClient.eval_lua` (`redis_manager.py:174-213`)
  **ignores its `script` argument** and decides in Python. Its own comment explains the cost:
  a real Lua defect (`string.sub(current_value, 1, 7) ~= 'processing'` — seven characters
  compared against a ten-character literal, constant-true) *survived* precisely because the
  mock never ran the script, and `tests/test_idempotency_lua_prefix_agreement.py` exists to
  stop that recurring.
* **But this repository has already solved that problem.** `SharedRedisManager.getdel`
  (`:847`) uses Lua safely, with three tiers: native `GETDEL` → `_GETDEL_LUA` (`:448`,
  applied at `:883`) → refuse. It **detects a stub evaluator by the shape of what comes back** —
  *"A client whose `eval` is a stub rather than a real script evaluator (several test doubles in
  this repo are) returns a shape this script cannot produce. Treat that as 'unsupported', never
  as a hit."*

  (Line numbers in this file are moving under concurrent edits by another tab; the ones above
  were re-read at the time of writing. The symbols `_GETDEL_LUA` and
  `SharedRedisManager.getdel` are the stable references.)

And its docstring makes an argument that applies directly against the current release
implementation:

> (3) deliberately does **not** fall back to `GET` + `DEL`. A caller asking for this method is
> asking for single-use semantics; quietly handing back a racy approximation would turn a
> replayable credential into a silent outcome rather than a loud one.

`_release_slot_lock` is doing exactly the `GET` + `DEL` approximation that this reviewed,
commented decision rejects for the same class of operation. So an atomic compare-and-delete
release **is** achievable within existing repository conventions, by following `getdel`'s
pattern rather than inventing one.

**Not implemented here.** The brief forbids introducing Lua or changing the facade, and forbids
claiming atomic release without demonstrating consistent behaviour across supported
environments — which cannot be demonstrated while §2 is blocked. Recorded as a scoped follow-up
(**D9**, §7), to be done *after* the gate is open so the Lua path can actually be exercised on a
real server rather than asserted against a mock that ignores scripts.

### 4.3 A durable database backstop is STILL REQUIRED

**Yes — and atomic release would not change that.** The two hazards are different:

| Hazard | Atomic release fixes it? | Database backstop fixes it? |
|---|---|---|
| Holder deletes a successor's lock after its own expired | **Yes** | n/a |
| Lock key *disappears* — failover, `maxmemory` eviction, cache restart | **No.** Mutual exclusion is simply gone, however the release is written | **Yes** |
| Facade returns `None` instead of raising, so causes are indistinguishable | No | **Yes** — the engine does not consult the cache |

The WORK-03 migration's own header already made this argument for an adjacent control: a Redis
`SET NX` is *"the obvious reach, and it is not sufficient on its own here"*, because the facade
is deliberately fail-soft. That reasoning applies unchanged. **The backstop requirement is
unaffected by anything in this workstream.**

---

## 5. Database design tradeoffs and pending coordinator decisions

Carried from the WSC review. **No option is selected, and no migration was written or applied.**

| Option | Mechanism | Pros | Cons |
|---|---|---|---|
| **A** | Per-account slot-claim table, `PRIMARY KEY (user_id, slot_index)`, claimed in the same transaction as the key write | Plan-**independent** at the DB layer — the WORK-03 shape; no limit duplicated into SQL | The *number* of slots still decided in application code; needs one transaction spanning claim + credential write |
| **B** | `BEFORE INSERT` trigger counting rows against `profiles → plans → plan_limits` | Enforces the real inequality in the database | Second source of truth; must honour `plan_limit_overrides`; plan-table drift silently changes enforcement; RLS / `SECURITY DEFINER` care needed |
| **C** | Materialised `profiles.exchange_connection_allowance`, written by billing; trigger compares against it | One authority; cheap check; overrides naturally included | A new write path to get wrong; stale on plan change if billing is unreliable |
| **D** | Retain the advisory lock only | No migration risk | Not durable; broken by failover or key eviction; §4 window remains |

### Unresolved requirements

1. **Effective limits and per-account overrides.** The effective allowance is
   `SubscriptionEngine.get_effective_quotas(plan, overrides)` — the in-code catalogue
   **widened by `profiles.plan_limit_overrides`** (JSONB). A `plan_limits` **table does exist**
   (`migrations/create_plan_tables.sql`: `plan_id → plans(id)`, `resource_type`, `limit_value`,
   `limit_type DEFAULT 'hard'`, `UNIQUE(plan_id, resource_type)`) and is populated, but
   **the enforcement path never reads it** — `PLAN_LIMITS`
   (`subscription_engine.py:1150`) is a *derived* index of `_PLANS`, not a DB read. Option B
   would make the database a third authority. A trigger that ignored the overrides would
   **refuse capacity a customer has contractually been granted**.
2. **Existing over-limit accounts.** See §6 — obtained for the local test substrate only.
3. **Key rotation above a downgraded allowance.** Today a downgrade leaves stored credentials
   alone and only blocks the next add. A naive row-count constraint makes that state permanently
   un-writable, which would block **rotation** — a security-relevant operation, and the last
   thing a plan limit should obstruct. Needs an explicit carve-out or an explicit refusal.
4. **Transaction boundary between slot claim and credential write.** Option A is only sound if
   the claim and the `exchange_keys` upsert commit atomically. `store_keys` currently writes
   through the vault's Supabase client, which is **not** in a shared transaction with anything —
   so Option A implies a change to how that write is issued. This is the least-appreciated cost
   of Option A and should not be waved through.
5. **Redis outage behaviour and rollback.** With a durable constraint in place, the advisory lock
   could be demoted from *the control* to an *optimisation*, which would let
   `POST /api/exchange/keys` be reconsidered as available-but-serialised rather than 503
   (WSC §5.6). That is a **separate** decision and must not be assumed to follow automatically.
   Rollback: dropping a trigger or claim table is non-destructive; Option C's column would be
   left behind.

---

## 6. Read-only over-limit counts

**Obtained, with an important caveat about what they do and do not mean.**

An authorized **local** endpoint was found: `staging/pg_dsn.txt` names `127.0.0.1:51805`,
database `postgres` — the bootstrap cluster the provisioning runbook documents
(`staging/bootstrap_pg.py`). The probe refuses any DSN that does not name a loopback host, so
it cannot be pointed at production by accident. No production or replica access was sought or
used.

Method (`staging/probe_p10k_overlimit_counts.py` → `staging/p10k_overlimit_counts.txt`): one
connection, `set_session(readonly=True)`, `SELECT` only, `rollback()`, close. **No customer
identifier was selected or printed** — the queries project only counts.

| Measure | Value |
|---|---|
| Rows in `public.exchange_keys` | **28** |
| Distinct accounts holding a key | **28** |
| Connections-per-account distribution | 1 connection: **28 accounts** |
| Accounts exceeding **1** (Free) | **0** |
| Accounts exceeding **2** (Trader) | **0** |
| Accounts exceeding **5** (Pro Quant) | **0** |
| Accounts exceeding **8** (Business) | **0** |

**What this establishes:** the aggregate query is correct and runnable, and this substrate has
no over-limit accounts, so a constraint added here would validate cleanly.

**What this does NOT establish — and must not be read as:** a production population figure.
These 28 rows are **test fixtures** created by this repository's own suites on a local
throwaway cluster. Decision **D3** (grandfather vs remediate) needs the same aggregate against
production or a production replica, which this workstream has no authorized access to.
The query is reproduced in WSC §10.4 and is safe to hand to whoever does.

---

## 7. Remaining blockers and recommended order

### Blockers to QUOTA-01

| # | Blocker | Status |
|---|---|---|
| 1 | Real-Redis cross-process concurrency unproven | **BLOCKED** — §2/§3. Cause now precisely known |
| 2 | No approved durable DB backstop | **DECISION** — §5 |
| 3 | Production over-limit population unknown | **NOT OBTAINABLE here** — §6 |
| 4 | Lock release non-atomic | **VERIFIED** — §4. Narrowed, not eliminated |
| 5 | Plan-ladder duplication (`TenantQuota` vs `_PLANS`) | **DECISION**, unchanged |
| 6 | Order-rate / daily-trade / active-order / strategy-count gaps | **REPORTED**, unchanged, out of scope here |

### Coordinator decisions

| # | Decision | Change from WSC |
|---|---|---|
| **D5′** | Authorize adding `AERORA_TEST_REDIS_URL: redis://localhost:6379/15` to the `unit-tests` job in `01-pr-check.yml`, and committing the three gated tests so the lane runs them | **Supersedes WSC D5.** No new infrastructure needed — CI already has the Redis. This is the cheapest remaining action on QUOTA-01 |
| **D5a** | Alternatively/additionally authorize fetching `redis-server.exe` into `staging/redis/` for local runs, per the runbook | new |
| **D9** | Authorize an atomic compare-and-delete release following `getdel`'s try-native → Lua → refuse pattern, **after** D5′ so it can be exercised on a real server | new, from §4.2 |
| **D10** | Note that the pre-existing metered-reservation real-Redis test has never run in CI either; D5′ closes it too | new, from §2.3 |
| D1 | Approve CONN-01 closure on existing evidence (§6 of WSC) | unchanged |
| D2 | Select a durable connection-limit option (A/B/C/D) | unchanged |
| D3 | Rule on existing over-limit accounts — needs a production count first | unchanged |
| D4 | Rule on rotation for accounts above a downgraded allowance | unchanged |
| D6 | Accept or revisit the 503 availability dependency on `POST /api/exchange/keys` | unchanged |
| D7 | Decide which trading dimensions to enforce, and on which path | unchanged |
| D8 | Choose one plan ladder | unchanged |

### Recommended order

1. **D5′** — one `env:` line; closes blocker 1 and D10 together, with no infrastructure spend.
2. **D1** — CONN-01 can close independently of all of this.
3. **D9** — once D5′ lands, the atomic release can be built *and demonstrated* rather than argued.
4. **D3** — the production count, before any schema design is chosen.
5. **D2 + D4** together; **D6** follows from D2.
6. **D7**, **D8** as separate product work.

---

## 8. Safety confirmation

| Statement | Status |
|---|---|
| Live trading remained **OFF** | **VERIFIED** — no `LIVE*`/`TRADING*` variable set; no execution path invoked |
| No exchange contacted, no exchange credential used, no order placed | **VERIFIED** — this workstream ran no exchange code; `tests/exchange_sandbox/` was not invoked |
| No database migration created or applied | **VERIFIED** — no `.sql` file written; §5 is review-only |
| No shared scorecard or finding registry edited **by this workstream** | **VERIFIED** — this workstream issued no write to `staging/FINAL_LAUNCH_SCORECARD.md` or `staging/full_audit_results.json`. **See the note below: both were modified by a CONCURRENT tab during this review.** |
| No plan limit, entitlement or downgrade behaviour changed | **VERIFIED** — no implementation file edited at all |
| No commit, push, or deploy | **VERIFIED** — `HEAD` still `074b772a` |
| No reset, clean, stash or revert | **VERIFIED** — no `git` write command was run |
| Concurrent working-tree changes preserved | **VERIFIED** — 247 → 249 entries (growth from other tabs, nothing removed); `subscription_dependencies.py` still `253/0`, i.e. identical to the WSC baseline |
| Redis not installed or started; no Redis DB flushed or written | **VERIFIED** — §2.4 |
| Full repository suite not run | **VERIFIED** — four QUOTA-01 files plus two gate probes only |
| Database access was read-only | **VERIFIED** — `readonly=True` session, `SELECT` only, `rollback()`; no identifier selected |
| No secret, token or full connection string printed | **VERIFIED** — probes redact userinfo and report names/host/port only |

### Note for the coordinator: the shared registry changed underneath this review

An earlier draft of this section proved "registry untouched" by modification time. That proof is
no longer sound and has been replaced, because the timestamps moved while this review was
running:

| File | mtime at WSC review (≈11:35) | mtime now |
|---|---|---|
| `staging/full_audit_results.json` | 11:33 | **13:08** |
| `staging/FINAL_LAUNCH_SCORECARD.md` | 11:35 | **13:14** |

**This workstream did not write to either file** — it issued no write to any path outside the
`staging/p10k_*`, `staging/probe_p10k_*`, `staging/verify_p10k_*` and this report's own
filename. The changes come from a **concurrent tab**. Flagged rather than left implicit for two
reasons: it is visible evidence that another workstream is editing the shared finding registry
and scorecard, which several Phase 10 briefs (including this one) forbid without coordinator
approval; and anyone auditing this review should know that mtime is not a usable integrity
signal for those two files in this working tree. Whether that concurrent edit was authorised is
outside this workstream's knowledge and is **DECISION** territory.

Working-tree entry count also moved 247 → **249** during the review, from the same concurrent
activity. Nothing was removed.

### Files added by this workstream (no file modified, none deleted)

```
new:  staging/PHASE10_WSK_QUOTA_REAL_REDIS_REVIEW.md      (this document)
new:  staging/probe_p10k_redis_authorization.py           + p10k_redis_authorization.txt
new:  staging/probe_p10k_gate_predicate.py                + p10k_gate_predicate.txt
new:  staging/probe_p10k_db_availability.py               + p10k_db_availability.txt
new:  staging/probe_p10k_overlimit_counts.py              + p10k_overlimit_counts.txt
new:  staging/p10k_ci_env_still_skips.txt
```

---

## 9. CONN-01 boundary

No connection-pool code was modified in this workstream (**VERIFIED**:
`backend_app/backend/connection_engine.py` numstat is `188/18`, unchanged from the WSC review;
`routers/exchange.py` differences are other tabs' concurrent work, not this one's).

**CONN-01 remains eligible for separate coordinator closure approval on its existing evidence**
(WSC review §6: 23 lifecycle tests plus 4 rotation tests, covering credential rotation, tenant
isolation, failure and shutdown cleanup, and log-secret safety). Two limitations stand and
should be recorded with any approval:

* **exchange-venue behaviour — NOT VERIFIED**, and
* **real shutdown behaviour — NOT VERIFIED** (lifespan wiring and ordering are established by
  source inspection and a source-reading test, not by a SIGTERM against a running server).

**CONN-01 is not marked closed in any shared registry by this document.**

---

## 10. QUOTA-01 status

```
QUOTA-01: OPEN
```

Not closed, and not eligible for closure. The real-Redis gate did not execute, no durable
database backstop is approved, the production over-limit population is unknown, and the lock
release is non-atomic. The single most valuable change is **D5′** — one environment variable on
a CI job that already has a healthy, isolated Redis attached to it.
