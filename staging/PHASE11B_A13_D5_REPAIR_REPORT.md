# Phase 11B-A13 — D5′ CI Repair and Production-Path Quota Evidence

**Outcome: one unblocked deliverable added; one phase blocked at an ownership boundary; one
new coordination problem discovered.**

The headline finding is not a test result. **There are now three open D5′ pull requests, two of
which are byte-identical duplicates, and none of the three contains the webhook keyspace fix or
the exchange-slot implementation.** All three would fail the same tests in CI.

Nothing was committed, pushed, or triggered. QUOTA-01 remains OPEN. Live trading OFF /
NOT CERTIFIED. Launch-readiness percentages and gate registers untouched.

---

## 1. Phase 0 — Rebaseline (read-only)

| Item | Value |
|---|---|
| branch / HEAD | `main` / `074b772a` |
| `refs/heads/main` = `origin/main` | `074b772a` |
| upstream | `origin/main` |
| D5′ branch local = remote | `d07014d3` |
| staged entries | 22 (unchanged) |
| staged `exchange.py` numstat | `268 / 15` (identical to A12) |
| staged-content fingerprint (`git ls-files -s`) | `CF474F1B83FD37173F0AD3F2B5417D07CE4B26DC66A5CF4D6697AE9ACB568767` |
| unstaged-modified / untracked | 100 / 3207 |

**Methodology correction to A12.** A12 used the raw `.git/index` file hash as the
"shared index unchanged" invariant. That is fragile: it moved from `6D965C8E…` to `3DBF1CB0…`
during this phase purely because Git refreshed the index **stat cache** while running
`status`/`diff` and pytest touched files. The staged *content* did not change — verified
content-addressed (22 entries, `exchange.py` still `268/15`). The `git ls-files -s` fingerprint
above is the correct invariant and is what later phases should use.

### 1a. THREE open D5′ PRs — #7 and #8 are exact duplicates

| PR | Branch | Head | Tree | Created | State |
|---|---|---|---|---|---|
| **#6** | `ci/d5-real-redis-quota-evidence` | `d07014d3` | `d28b6d1a…` | 10-10 10:02 | OPEN |
| **#7** | `ci/d5-prime-real-redis` | `49e70da2` | **`271f13c2…`** | 10-10 10:13 | OPEN |
| **#8** | `ci/d5-real-redis-switch` | `a624ce89` | **`271f13c2…`** | 10-10 10:55 | OPEN |

`#7` and `#8` resolve to the **same tree hash** — they are the same content on two branches,
both 1761 additions across the same five files. One is redundant by construction.

Also open and unrelated: #2 `chore/connect-vyomquant-in-domain`. Merged/closed: #1, #3, #5
(merged), #4 (closed).

### 1b. What each PR actually contains

| Property | PR #6 | PR #7 / #8 | Worktree |
|---|---|---|---|
| `AERORA_TEST_REDIS_URL` | `…/15` (×2: unit-tests + diagnostic job) | `…/15` | **`…/0`** (my A12 edit) |
| webhook test size | 300 non-blank / 383 lines | 333 / 416 lines | **646 lines** |
| webhook keyspace probe (`_PROBE_PREFIX`) | **absent** | **absent** | **present (3 hits)** |
| webhook `skip`→`fail` strengthening | absent | **present** | present |
| `d5-redis-diagnostics` job | **present** (+151) | absent | absent |
| slot-lock guard `def` in `subscription_dependencies.py` | **0** | **0** | **1** |
| `exchange.py` guard references | **0** | **0** | **3** |
| production-path quota test | absent | absent | **present (added here)** |

**Consequences, stated plainly:**
- The webhook keyspace split — root cause of 2 of the 4 CI failures — is **fixed in no PR**.
- The slot-lock implementation is **in no PR**, so its 21 failures would recur in all three.
- All three PRs still carry `/15`.

### 1c. Ownership / staged-vs-unstaged dependencies

| File | Git state | Staged (index vs HEAD) | Unstaged (worktree vs index) | Owner |
|---|---|---|---|---|
| `backend_app/backend/redis_manager.py` | **clean** | — | — | committed; DB-0 logic is in every PR |
| `backend_app/core/subscription_engine.py` | **clean** | — | — | committed; `reserve_quota` is in every PR |
| `backend_app/core/subscription_dependencies.py` | ` M` | — | **+253 / −0** (guard impl) | Workstream C / QUOTA-01 |
| `backend_app/routers/exchange.py` | **`MM`** | **+268 / −15** | +154 / −14 (guard usage) | **two workstreams** |
| `backend_app/core/cache/redis_manager.py` | ` M` | — | **+185 / −5** | concurrent — in the dependency path |
| `tests/real_redis_gate.py` | `??` | — | identical to PR #6 | D5′ (this workstream) |
| `tests/test_quota01_metered_reservation_is_hard.py` | `??` | — | identical to PR #6 **before my change** | D5′ (this workstream) |
| `tests/test_quota01_exchange_slot_is_atomic.py` | `??` | — | identical to PR #6 | D5′, but **depends on unowned impl** |
| `tests/test_webhook_duplicate_delivery_real_redis.py` | `??` | — | **+15.6 k chars vs PR #6** | **concurrent** — not touched by me |

New since A12: `backend_app/core/cache/redis_manager.py` carries **+185/−5 unstaged** concurrent
changes. It is the `SharedRedisManager` facade my new test resolves through, so it is a live
dependency. Its committed version (the one in every PR) does contain every seam the new test
needs — verified: `MockRedisClient` L36, `_bypass_dev_mode` L451, `_get_active_cache` L619,
`SharedRedisManager` instance L1308, and `subscription_engine` imports that same instance.

### 1d. Status / blocker matrix

| # | Item | Committed in a PR? | Worktree only? | Blocker |
|---|---|---|---|---|
| 1 | D5′ opt-in reaches CI (gate `state=run`) | yes (#6, proven run 38047483583) | — | none — **PROVEN** |
| 2 | Webhook keyspace probe | **no** | yes | needs committing by its owner |
| 3 | `/15` → `/0` (gate/app coherence) | **no** | yes (mine) | needs committing |
| 4 | Slot-lock implementation | **no** | yes | **`exchange.py` entangled** |
| 5 | Production-path `reserve_quota` real-Redis test | **no** | yes (added here) | needs committing + a CI run |
| 6 | PR duplication (#7 ≡ #8) | — | — | **triage decision required** |
| 7 | order-rate / daily-trade / active-order ceilings | — | — | not implemented at all |

## 2. Phase 1 — D5′ CI configuration

**Retained from A12, re-validated here:** in the worktree `01-pr-check.yml`,
`AERORA_TEST_REDIS_URL: redis://localhost:6379/0` (L248) plus a 58-line comment replacing the
5-line block that falsely claimed `/15` "pins a dedicated database index … belt-and-braces
rather than load-bearing".

**Justification against Phase 1.3/1.4** ("prefer the application's authoritative cache URL";
"avoid hard-coding a database path merely to make a test green"):
- The **authoritative keyspace mechanism is not this variable.** It is the test deriving its
  client from the application's own `get_redis_url(REDIS_DB_CACHE)` — the worktree webhook probe
  does this, and my new quota test does the same. No database-selection logic is duplicated.
- `/0` is therefore **not** what makes any test green, and is not load-bearing for keyspace. Its
  only job is the **gate's PING endpoint**: with `/15` the gate proves database 15 is reachable
  while the suite exercises database 0. `/0` makes the database the gate proves reachable the
  database the application actually uses. Measured below.
- The false comment had to be corrected regardless of the value.

**Preserved:** the concurrent `provisioning-and-isolation` job is byte-untouched. Worktree
workflow diff vs HEAD is three hunks — `@@ +186,58 @@` and `@@ +248,1 @@` (mine) and
`@@ +527,143 @@` (provisioning). Total `202 / 0`. YAML parses; 8 jobs intact;
`unit-tests` env = `{REDIS_URL: …:6379, AERORA_TEST_REDIS_URL: …:6379/0, ENV: test,
AERORA_MODE: safe, PYTHONPATH: .}`.

**Still outstanding:** the `d5-redis-diagnostics` job committed in `#6` carries `/15`, and the
concurrent webhook docstring quotes `/15` as the CI value. Both need the same one-token update
in whichever PR becomes canonical.

## 3. Phase 2 — Exchange-slot dependency: STOPPED at the boundary

Re-verified, not assumed:

```
exchange_connection_slot_guard def  ->  HEAD: 0   INDEX: 0   PR#6: 0   PR#7/8: 0   WORKTREE: 1
exchange.py guard references        ->  HEAD: 0   INDEX: 0                          WORKTREE: 3
```

`backend_app/routers/exchange.py` is `MM`: **+268/−15 staged** belongs to the pre-existing
22-file tenant-isolation / RLS / credential-vault workstream, and the guard call site sits in the
+154/−14 unstaged layer **on top of it**. A12 proved the unstaged hunks cannot be lifted onto
HEAD (`git apply --check` → `error: patch failed: backend_app/routers/exchange.py:366`,
exit 1), because their context depends on those 268 staged lines.

`subscription_dependencies.py` (+253/−0, unstaged only) is separable on its own, but useless
alone: `test_store_keys_uses_the_guard_around_its_vault_write` asserts the `exchange.py` call
site exists.

**Action taken: none.** Phase 2.5 permits removing the dependent test file from D5′ *if the
implementation owner approves*. **No such approval exists**, so I did not remove
`tests/test_quota01_exchange_slot_is_atomic.py`, did not copy the implementation, and did not
rewrite the integration against an untested `exchange.py`. The exact dependency is reported
above for the owner's decision.

## 4. Phase 3 — Production-path real-Redis quota coverage (ADDED)

Target file `tests/test_quota01_metered_reservation_is_hard.py` is owned by this workstream and
was **byte-identical** to PR #6 before my change (i.e. not concurrently modified), so ownership
is unambiguous. Added **+266 lines** (264 → 530): three helpers and two gated tests.

**Why it was needed.** `test_concurrent_reservations_are_atomic_against_real_redis` — the one
real-Redis quota test that PASSED in CI — **never calls** `SubscriptionEngine.reserve_quota`. It
opens its own client and re-implements the pattern inline (its own comment: "Mirror
reserve_quota's increment-first-revert against the real server"), borrowing only `_meter_key`.
It proves Redis's INCR/DECR are atomic — never in doubt — not that the production function is
correct. The six tests that do call `reserve_quota` run on `MockRedisClient`.

**Added:**
- `_app_cache_or_fail()` — resolves the shared `SharedRedisManager` via the supported
  `_bypass_dev_mode(True)` seam (listed in conftest's `_REDIS_SINGLETON_SEAMS`, so the autouse
  `_restore_shared_redis_singleton` restores it; no leak, no application change). **Fails, never
  skips,** on `MockRedisClient`/`None`.
- `_probe_client_on_the_apps_database()` — derives the URL from the application's own
  `get_redis_url(REDIS_DB_CACHE)` rather than re-deriving a database, and applies the gate's
  non-loopback refusal to it. No endpoint is echoed.
- `_prove_one_keyspace(manager, client)` — bidirectional sentinel (app→test and test→app), TTL
  30 s, namespaced `aerora:test:q01-keyspace:*`, deleted through both paths.
- `test_production_reserve_quota_admits_exactly_the_limit_against_real_redis` — 50 concurrent
  production `reserve_quota` calls at limit 10; asserts exactly 10 allowed, 40 refused;
  `get_quota_usage` **and** the raw key both equal 10; every refusal reports a reverted figure in
  `[0, 10]`; a post-exhaustion retry is refused and does not move the meter.
- `test_production_zero_allowance_writes_no_meter_against_real_redis` — limit 0 refuses and
  leaves **no key** (`EXISTS == 0`), with `get_quota_usage == 0`.
- Teardown deletes only `quota:{unique_user}:{resource}:{unique_period}` plus its own sentinels.

No reservation semantics were re-specified: the asserted properties are the ones
`reserve_quota`'s own docstring claims.

**These two tests are UNEXECUTED.** No Redis is reachable here, so they skip. Per the explicit
instruction, real-Redis success is **not claimed** and will not be until they run against
reachable Redis in CI with zero gated skips.

## 5. Phase 4 — Verification evidence

Redis genuinely reachable locally: **NO** (`AERORA_TEST_REDIS_URL` unset; `127.0.0.1:6379`
closed). Nothing was installed.

| Step | Command | Exit | Result | Redis real? |
|---|---|---|---|---|
| 1a | `real_redis_gate._classify` × 5 inputs | 0 | `unset→skip`; `bad-shape→fail`; `…/0→fail`; `…/15→fail`; non-loopback`→fail` | n/a |
| 1b | `get_redis_url(REDIS_DB_CACHE)` + `ConnectionPool.from_url` | 0 | `REDIS_DB_CACHE=0` → `redis://localhost:6379/0` → **db 0** | n/a |
| 2 | `pytest tests/test_quota01_metered_reservation_is_hard.py -rs` | 0 | **6 passed, 3 skipped** | **no** — 3 real-Redis skipped |
| 3 | `pytest` ×4 (quota wiring, hard-enforcer deprecation, redis-fallback lock, webhook) | 0 | **40 passed, 6 skipped** | **no** — 6 webhook skipped |
| 4 | slot-lock file | — | **NOT RUN as evidence** | — |
| 5 | `yaml.safe_load` on `01-pr-check.yml` | 0 | parses; 8 jobs; `provisioning-and-isolation` intact; `/0` present | n/a |
| 6 | `pytest --collect-only` on the metered file | 0 | 9 tests collected incl. both new ones | n/a |

**Skips are not passes.** The 9 skips across steps 2–3 are all gated real-Redis cases taking the
legitimate unset opt-out (3 metered incl. my 2 new, 6 webhook). The gate's `reachable → run`
state is UNEXECUTED locally; it was proven in CI run 38047483583 (`state=run`).

**Step 4, explicitly:** the slot-lock tests were **not** run as evidence because no commit
contains their implementation. They pass in this worktree only because the worktree carries the
uncommitted `+253` implementation — which is exactly why local green misled A6. Worktree-only
code is not evidence for a PR commit.

## 6. Changed-file manifest (worktree only — nothing committed)

| Path | Change | Phase | Owner |
|---|---|---|---|
| `.github/workflows/01-pr-check.yml` | `/15`→`/0` + 5-line false comment replaced by 58 lines. Diff vs HEAD `202/0` = mine `59` + concurrent provisioning `143` (untouched) | A12, revalidated A13 | D5′ (mine) |
| `tests/test_quota01_metered_reservation_is_hard.py` | **+266** (264→530): 3 helpers + 2 gated production-path tests + `os`/`urlsplit` imports | A13 | D5′ (mine) |

Not touched: every other file, including the concurrently modified
`tests/test_webhook_duplicate_delivery_real_redis.py`, `core/cache/redis_manager.py`,
`subscription_dependencies.py`, `exchange.py`, all 22 staged entries, and all unrelated
untracked work. No assertions weakened, no skip converted, no xfail added, no production code
changed.

## 7. Phase 5 — Remote-action boundary (NOT crossed)

No commit, push, merge, or CI trigger. Authorization is requested for the following, in order:

1. **Triage the duplicate PRs.** `#7` and `#8` have identical trees — close one. Then decide the
   canonical D5′ PR. Recommendation: keep **#6** (it alone carries the diagnostic job that
   produced the only usable tracebacks) and close #7 and #8, *or* adopt #7/#8 and port #6's
   diagnostic job — but not three.
2. **Commit the webhook keyspace probe** from the worktree into the canonical PR. Its author
   should do this, or approve my doing it. Without it, 2 of the 4 failures persist in every PR.
3. **Commit my two files** (§6) to the canonical PR, using private-index/hunk isolation so the
   `provisioning-and-isolation` hunk and the 22 staged entries stay out.
4. **Decide the slot-lock test's fate** (§3) with the `exchange.py` owner.
5. Re-run CI, then confirm: gate `state=run`, and the production-path quota tests + the 6 webhook
   tests PASS with **zero** gated skips.
6. Remove the temporary `d5-redis-diagnostics` job once the failures are closed.

On authorization I will re-verify the staged-content fingerprint and the PR head immediately
before pushing.

## 8. QUOTA-01 disposition

**OPEN.** Not closed by this phase, and not closeable yet.

| Claim | Status |
|---|---|
| D5′ opt-in reaches CI; gate executes rather than skipping | **PROVEN** (run 38047483583, `state=run`) |
| Redis INCR/DECR atomicity | proven — but this is a Redis property, **not** application evidence |
| Production `reserve_quota` against real Redis | **test now exists, UNEXECUTED** — no evidence yet |
| Exchange-slot concurrency + release safety against real Redis | **NO EVIDENCE** — implementation in no PR; cannot be claimed |
| Webhook idempotency against real Redis | 4 of 6 proven; the 2 failures have a worktree-only fix that is **UNEXECUTED** |
| order-rate / daily-trade / active-order ceilings | **OPEN** — no gate exists |
| Slot-lock release non-atomicity residual (read-then-compare-then-delete) | **OPEN**, documented |

Live trading remains OFF / NOT CERTIFIED.
