# Phase 11B-A12 — Minimal D5′ Repair and Real-Redis Reverification

**Outcome: PARTIAL — one owned fix applied locally; two stop conditions fired and were
honoured.** Phase 1 items 1–2 are done. Phase 1 items 3–4 were **already implemented by
concurrent work** that appeared after A11, more robustly than planned, so I did not touch that
file. Phase 2 is **BLOCKED** by proven cross-workstream entanglement. Phase 3 found that the
one green real-Redis quota test **does not exercise the production API at all**.

Nothing was pushed. No commit was created. PR #6 remains OPEN at `d07014d3`. QUOTA-01 remains
OPEN. Live trading OFF / NOT CERTIFIED.

---

## 1. Phase 0 — Baseline and ownership

| Item | Value at Phase 0 | Re-verified at end |
|---|---|---|
| HEAD / branch | `074b772a` / `main` | unchanged |
| upstream | `origin/main` (`074b772a`) | unchanged |
| `refs/heads/main` | `074b772a` | unchanged |
| D5′ branch local / remote | `d07014d3` / `d07014d3` | unchanged (no push) |
| PR #6 | head `d07014d3`, base `main`, OPEN, `mergedAt: null` | unchanged |
| **Shared index SHA256** | `6D965C8EAFDD23EECCEA41A6BFF25DF5D9F1EE9FC64DFA17C29C965210458613` | **identical** |
| staged / unstaged / untracked | 22 / 100 / 3207 | 22 staged, unchanged |
| PR branch contents vs `main` | `M` workflow + `A` ×4 test files (5 paths) | unchanged |

### 1a. STOP CONDITION #1 — the working tree changed since A11

`tests/test_webhook_duplicate_delivery_real_redis.py` was **modified by concurrent work** after
A11. Verified by comparing the worktree against the same file as committed in `d07014d3`
(line-ending normalised, so this is real content, not CRLF noise):

| File | worktree | in `d07014d3` | normalised identical |
|---|---|---|---|
| `tests/real_redis_gate.py` | 187 | 187 | **yes** |
| `tests/test_quota01_metered_reservation_is_hard.py` | 264 | 264 | **yes** |
| `tests/test_quota01_exchange_slot_is_atomic.py` | 867 | 867 | **yes** (byte diff was CRLF only) |
| `tests/test_webhook_duplicate_delivery_real_redis.py` | **646** | **383** | **NO** — 32,786 vs 17,177 chars |

So exactly one D5′ file is now under shared ownership. I did **not** edit it.

**What the concurrent change does** (reviewed read-only): it fixes the same keyspace split A11
diagnosed, but better than my plan did. Instead of hard-coding a database, the test now derives
its client URL from the application's own `get_redis_url(REDIS_DB_CACHE)`, and adds
`_prove_one_keyspace(manager, client)` — a **bidirectional** sentinel probe (app writes → test
reads, and test writes → app reads) that fails with an explicit `KEYSPACE SPLIT` message. It also
applies the gate's non-loopback refusal to the application's effective URL, namespaces its
sentinels under `aerora:test:keyspace-probe` with a 30 s TTL and deletes them through both paths,
and **strengthens** `real_redis_cache` from `pytest.skip` to `pytest.fail` when the manager
resolves to `MockRedisClient`. All six test names are preserved. Its own comment explicitly
rejects a hard-coded `/0` as drift-prone — a better argument than the one I was about to make.

This satisfies Phase 1 items 3 and 4 (including the fixture-level same-keyspace proof) and
addresses the vacuous `test_an_invalid_signature_is_rejected_and_leaves_no_lock_behind` pass
A11 flagged.

### 1b. STOP CONDITION #2 — `exchange.py` is entangled with another workstream

`backend_app/routers/exchange.py` is `MM`:
- **staged** (index vs HEAD): **+268 / −15** — part of the pre-existing 22-file
  tenant-isolation / RLS / credential-vault workstream. **Not mine.**
- **unstaged** (worktree vs index): +154 / −14 — contains the `store_keys` slot-guard usage.

`exchange_connection_slot_guard` occurrences: **0 in HEAD, 0 in the index, 3 in the worktree.**

Empirical proof of entanglement — the unstaged hunks cannot be lifted onto HEAD:
```
git apply --cached --recount --check <unstaged exchange.py patch>   (private index = HEAD)
  error: patch failed: backend_app/routers/exchange.py:366
  error: backend_app/routers/exchange.py: patch does not apply      (exit 1)
```
The guard hunks are expressed against the index state that carries the other workstream's 268
staged lines. Including the guard therefore requires either (a) also shipping that workstream's
unapproved changes, or (b) hand-rewriting the integration against HEAD — fabricating a variant
that has never been executed. Both are out of bounds, so **A12 stopped here**, as instructed.

By contrast `backend_app/core/subscription_dependencies.py` is cleanly ` M` (+253 / −0, purely
additive, unstaged only) and would be separable on its own — but it is useless without the
`store_keys` call site, since `test_store_keys_uses_the_guard_around_its_vault_write` asserts
exactly that call site exists.

### 1c. DB-0 mismatch reconfirmed from current code (not from A11)

`backend_app/backend/redis_manager.py` is **unmodified** (committed == worktree), so this is the
code in the tested commit:
- `REDIS_DB_CACHE = 0`, `REDIS_DB_QUEUE = 1`, `REDIS_DB_EVENTS = 2`
- `REDIS_URL = os.getenv("REDIS_URL", "").strip()` captured at **import**
- `get_redis_url(db)` keeps `parsed.path` **and** sets `query_params['db'] = str(db)`
- `cache = await aioredis.from_url(get_redis_url(REDIS_DB_CACHE), ...)`

Measured locally with the installed redis-py (offline URL parsing, no connection):
```
redis://localhost:6379/15       -> db = 15
redis://localhost:6379/15?db=0  -> db = 0     <-- query beats path
redis://localhost:6379?db=0     -> db = 0
redis://localhost:6379          -> db = None
redis://localhost:6379/0        -> db = 0
```
So the app's cache resolved to **db 0** while the old test client honoured the path and read
**db 15**. A11's mechanism is confirmed independently.

## 2. Exact changes made (local only, not committed)

**One file, my own D5′ hunk in `.github/workflows/01-pr-check.yml`:**

1. `AERORA_TEST_REDIS_URL: redis://localhost:6379/15` → `redis://localhost:6379/0` (L248).
2. Replaced the 5-line comment that claimed `/15` "pins a dedicated database index … belt-and-
   braces rather than load-bearing" — false in both halves — with an accurate block recording
   the measured override, the redis-py precedence, the resulting false-assurance pass, and the
   fact that the robust fix now lives in the test (deriving the URL from the application) while
   this variable's remaining job is the **gate's** PING endpoint. `/0` is named so the database
   the gate proves reachable is the database the application actually uses.

Verification of confinement: the worktree workflow diff vs HEAD is three hunks —
`@@ +186,58 @@` and `@@ +248,1 @@` (both mine) and `@@ +527,143 @@` (the
`provisioning-and-isolation` job, another workstream, **untouched**). YAML parses; jobs list
unchanged; `unit-tests` env now
`{REDIS_URL: …:6379, AERORA_TEST_REDIS_URL: …:6379/0, ENV: test, AERORA_MODE: safe, PYTHONPATH: .}`.

**Nothing else was edited.** No production code, no `tests/conftest.py`, no `redis_manager.py`,
no `real_redis_gate.py`, no assertions, skips or xfails, no test file.

### Follow-up this change creates for the concurrent owner
The concurrent webhook file's docstring still cites the CI value as
`AERORA_TEST_REDIS_URL: redis://localhost:6379/15`. That line is now stale. Its conclusion is
unaffected (its client derives the URL from the app, so the gate's database is irrelevant to its
keyspace), but the owner should update the quoted value. The diagnostic job added in `d07014d3`
also still carries `/15` and needs the same one-token change in whatever commit lands this.

## 3. Preservation of unrelated concurrent work

- Shared index SHA256 **identical** before and after (`6D965C8E…`); 22 staged entries intact.
- `main` / `origin/main` / both D5′ refs unchanged; **no push, no commit, no CI trigger**.
- The 100 unstaged and 3207 untracked paths were not touched, except the one workflow file.
- The `provisioning-and-isolation` hunk in the worktree workflow is byte-untouched.
- The concurrently modified webhook test was left exactly as found.
- Temporary helper scripts and downloaded artifacts removed.

## 4. Phase 4 — Local test evidence

Environment: `AERORA_TEST_REDIS_URL` unset; `127.0.0.1:6379` **not reachable**. No Redis was
installed.

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `pytest tests/test_quota01_exchange_slot_is_atomic.py tests/test_quota01_metered_reservation_is_hard.py tests/test_webhook_duplicate_delivery_real_redis.py -q --tb=short` | 0 | **26 passed, 9 skipped** (116.5 s) |
| 2 | `pytest tests/test_quota01_enforcement_is_wired.py tests/test_quota01_hard_enforcer_is_deprecated_in_code.py tests/test_redis_fallback_does_not_fake_a_distributed_lock.py -q` | 0 | **40 passed** (89.6 s) |
| 3 | `real_redis_gate._classify(...)` over four inputs | 0 | `unset → skip`, `bad-shape → fail`, `redis://localhost:6379/0` (new CI value, no local server) `→ fail`, non-loopback `→ fail` |

**The nine real-Redis tests are UNEXECUTED locally** — all 9 of the skips are the gated
real-Redis cases (2 slot + 1 metered + 6 webhook) taking the legitimate unset opt-out. They are
**not** counted as passes. The gate's `reachable → run` state is likewise UNEXECUTED locally; it
was proven in CI run 38047483583 (`state=run`).

**Critical caveat on reading result #1 as green.** The 22 slot-lock tests pass locally *because
the working tree contains the implementation* (`subscription_dependencies.py` +253 unstaged). The
tested commit `d07014d3` does **not** contain it, which is why the same 21 tests fail in CI with
`AttributeError`. Local green here is an artefact of the uncommitted tree and must not be read as
evidence about the PR.

The concurrently modified webhook file imports, collects, and skips cleanly (it is part of the 9
skips). That is the limit of what I can assert about it — its new probe has **not** been executed
against a real Redis.

## 5. Phase 3 — Was the production quota API exercised? NO

`backend_app/core/subscription_engine.py` is **unmodified/committed**, so production
`reserve_quota` (L959), `_meter_key` (L945) and `get_quota_usage` (L1049) are all present in the
tested commit. `reserve_quota` increments first via `redis_manager.incrby`, sets
`_METER_TTL_SECONDS` on first write, and reverts with `decrby` when `new_usage > effective`.

But `test_concurrent_reservations_are_atomic_against_real_redis` — the single real-Redis quota
test that PASSED in CI — **never calls it.** It opens its own `aioredis` client and re-implements
the pattern inline, as its own comment says:

```python
async def _reserve_real():
    # Mirror reserve_quota's increment-first-revert against the real server.
    new_usage = await client.incrby(key, 1)
    if new_usage > limit:
        await client.decrby(key, 1)
        return False
    return True
```

It borrows `SubscriptionEngine._meter_key` for key naming and nothing else. **What it proves is
that Redis's own INCR/DECR are atomic** — which was never in doubt — not that the application's
reservation path is correct against a real server. The six logic tests that *do* call
`reserve_quota` run against `MockRedisClient`. So QUOTA-01's metered path has **no real-Redis
production-path evidence**, despite a green test.

### Proposed test (specified, NOT authored)
Not written here: the only file it belongs in is now under shared ownership (§1a), and I cannot
execute it locally, which is exactly the mistake that produced A11's findings. Specification:

`test_production_reserve_quota_is_atomic_against_real_redis`
1. Gate with the shared `real_redis_required` + `require_real_redis()` (unset → skip;
   configured-but-unreachable → **fail**).
2. Resolve `redis_manager` to the real cache the way the webhook file does
   (`_bypass_dev_mode(True)`, then fail — not skip — if it hands back `MockRedisClient`).
3. Reuse the concurrent file's `_prove_one_keyspace` pattern so the app and the probe client are
   proven to share one database before anything is asserted.
4. `asyncio.gather` N=50 calls to `SubscriptionEngine.reserve_quota(user, "free", resource,
   limit=K, period=unique)` with K=10; assert **exactly K** return `allowed=True`.
5. Assert the production reader `get_quota_usage(...)` returns exactly K, and cross-check the raw
   key with the probe client.
6. Assert every refusal reports the **reverted** usage (`usage <= K`), never an inflated figure.
7. Assert `limit=0` reserves nothing and leaves **no key behind**; assert a refusal does not
   decrement below zero.
8. Unique `user_id` and `period` per run; teardown deletes only `quota:{user}:{resource}:{period}`.

## 6. Remaining CI and authorization dependencies

| # | Action | Needs |
|---|---|---|
| 1 | Apply the same `/15 → /0` change to the diagnostic job in `d07014d3`, and have the concurrent owner refresh the stale `/15` quote in the webhook docstring | owner coordination |
| 2 | **Decide the slot-lock test's fate.** Recommended: **remove `tests/test_quota01_exchange_slot_is_atomic.py` from PR #6** and land it together with its implementation, owned by the Phase 10 Workstream C / credential-vault workstream that holds `exchange.py`. Do not copy that workstream's code. | explicit decision |
| 3 | Commit the workflow change + whatever is decided in (1)/(2), using the private-index/hunk discipline | remote-action approval |
| 4 | Push and let CI re-run; confirm the webhook file's 6 tests and the metered test against real Redis | remote-action approval |
| 5 | Remove the temporary `d5-redis-diagnostics` job once the four failures are closed | — |
| 6 | Author and run the production-path `reserve_quota` real-Redis test from §5 | ownership + approval |

## 7. QUOTA-01 status and evidence still required

**OPEN.** Not advanced by this phase.

| Claim | Status |
|---|---|
| Real-Redis opt-in reaches CI (D5′ mechanism) | **PROVEN** (gate `state=run`, run 38047483583) |
| Metered reservation — Redis INCR atomicity | proven, but **not the application path** |
| Metered reservation — production `reserve_quota` against real Redis | **NO EVIDENCE** (§5) |
| Exchange-slot concurrency + release safety against real Redis | **NO EVIDENCE** — implementation absent from the tested commit; cannot be claimed |
| Webhook idempotency against real Redis | 4 of 6 proven; the 2 failures have a fix in the tree that is **UNEXECUTED** |
| order-rate / daily-trade / active-order ceilings | **OPEN**, no gate exists |
| Slot-lock release non-atomicity residual (read-then-compare-then-delete) | **OPEN**, documented |

Launch-readiness percentages and gate registers were **not** touched, as instructed.
