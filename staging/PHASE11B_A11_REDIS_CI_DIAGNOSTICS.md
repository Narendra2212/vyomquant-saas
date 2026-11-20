# Phase 11B-A11 — Authorized Focused Real-Redis CI Diagnostics

**Outcome: SUCCESS — all four tracebacks captured and classified.** The contamination
hypothesis carried since Phase 11B-A6 is **REJECTED**: every failure reproduces identically
when each file runs alone against the same real Redis. Two independent, concrete root causes
were found, neither of which is a defect in the quota or webhook production logic.

PR #6 remains OPEN. QUOTA-01 remains OPEN. Live trading OFF / NOT CERTIFIED.

---

## 1. Phase 0 — Read-only baseline

| Item | Value at start |
|---|---|
| Repo HEAD / branch | `074b772a` / `main` (== `origin/main`) |
| PR #6 | head `b43e990d`, base `main`, OPEN, `mergedAt: null` |
| Shared staged index | 22 files (tenant-isolation/RLS/terraform workstream) — untouched throughout |
| Workflow trigger | `pull_request` → `[main, master]`, types `[opened, synchronize, reopened]`; **no `push`, no `workflow_dispatch`** |
| `01-pr-check.yml` permissions | none declared (inherits contents/metadata/packages: read). `upload-artifact@v4` needs no extra scope |
| Workflow ownership | all commits authored by `Narendra2212` (sole owner); D5′ test files owned by this workstream |
| Artifact convention | `actions/upload-artifact@v4` + `if: always()` + `retention-days` (as used by 03-deploy, 04-nightly-audit, 05-security) |

**Critical baseline subtlety:** the working-tree `01-pr-check.yml` is 742 lines and contains the
`provisioning-and-isolation` job; the **D5′ branch** version is 611 lines and does not. The
diagnostic job was therefore built on the **branch** version, so the unrelated provisioning
workstream could not leak into the commit.

## 2. Phase 1/2 — The diagnostic change (exact diff)

One commit, one file, additive only:

- **Commit:** `d07014d36d32c63db44f8fb9659933c0e84e82f2`
- **Parent:** `b43e990d` (the D5′ commit)
- **Message:** `ci(diag): capture D5' real-Redis tracebacks per file with JUnit artifacts`
- **Changed paths:** `M .github/workflows/01-pr-check.yml` — **+151 / −0**, nothing else
- Built in a **private index** (`GIT_INDEX_FILE` seeded from `b43e990d`, blob via
  `git hash-object -w`, `commit-tree`, `update-ref` with CAS against `b43e990d`). The shared
  index was never referenced; the worktree was never switched.

Added job `d5-redis-diagnostics` ("D5' Real-Redis Diagnostics (non-blocking)"):
`needs: static-audit`, `continue-on-error: true`, `timeout-minutes: 25`, `redis:7-alpine`
service and `REDIS_URL` / `AERORA_TEST_REDIS_URL` copied verbatim from `unit-tests`,
**one pytest invocation per file** with `-v --tb=long -rf --junitxml=...`, each step
`continue-on-error: true`, plus an `actions/upload-artifact@v4` step with `if: always()`.

**Pre-flight verification (all passed before pushing):**
- YAML parses; jobs = the 7 existing + `d5-redis-diagnostics`.
- **`original jobs MODIFIED: NONE`** (deep-compared each pre-existing job dict).
- Exactly the three intended test files selected; 3 JUnit outputs; 2 `AERORA_TEST_REDIS_URL`
  (unit-tests + diagnostic); 3 `redis:7-alpine` services; upload step `if: always()`.
- `tests/real_redis_gate.py`, `tests/conftest.py`, `redis_manager.py`, every assertion, skip
  and xfail: **untouched** (gate's unset/unreachable/reachable behavior intact by construction).

## 3. Phase 3 — Tested SHA, run, artifacts

| Item | Value |
|---|---|
| PR #6 head after push | `d07014d36d32c63db44f8fb9659933c0e84e82f2` |
| Run | `38047483583` (workflow "01 PR Check", event `pull_request`) |
| `github.sha` (PR merge commit) | `d8eb6fc4512bd1f7e1d0b544374d7abb9dbf3937` |
| Artifact | `d5-redis-diagnostics-d8eb6fc4512bd1f7e1d0b544374d7abb9dbf3937` (id `11668905597`, 21,802 B) |
| Contents | `gate_state.txt`, `slot_lock.{xml,log}`, `metered.{xml,log}`, `webhook.{xml,log}` |

**Gate proof (`gate_state.txt`) — real Redis was reached, zero skips:**
```
real-Redis gate: state=run (AERORA_TEST_REDIS_URL=set, remote_allowed=False)
state=run
reason=(none)
```

**Isolated results (JUnit, authoritative — the job's `success` badge only reflects
`continue-on-error`):**

| File | tests | failures | skipped | Full-suite result (run 38043513862) |
|---|---|---|---|---|
| `test_quota01_exchange_slot_is_atomic.py` | 22 | **21** | 0 | 21 failed / 1 passed — **identical** |
| `test_quota01_metered_reservation_is_hard.py` | 7 | 0 | 0 | 7 passed — identical |
| `test_webhook_duplicate_delivery_real_redis.py` | 6 | **2** | 0 | 2 failed / 4 passed — **identical** |

**→ HYPOTHESIS REJECTED.** Full-suite state contamination (the rate-limiter/ordering leak in
the excluded `tests/conftest.py`) does **not** explain these failures. They reproduce exactly
in isolation. The Phase 11B-A6 "pre-existing contamination" attribution was wrong for the D5′
tests, and the Phase 11B-A10 "fixture/collection-level signature" guess was directionally
right but not the mechanism.

## 4. The four diagnoses (evidence-based)

### Cause A — the D5′ commit shipped tests without their implementation
**Classification: commit-manifest defect (my error in Phase 11B-A4/A6). Not a code defect.**

Grouped failure messages from `slot_lock.xml`:
- **14 tests:** `AttributeError: module 'backend_app.core.subscription_dependencies' has no attribute 'exchange_connection_slot_guard'`
- **6 tests:** `AttributeError: module 'backend_app.core.subscription_dependencies' has no attribute '_slot_lock_key'`
- **1 test** (`test_store_keys_uses_the_guard_around_its_vault_write`):
  `AssertionError: store_keys does not use exchange_connection_slot_guard; the connection limit is back to a read-then-write race (QUOTA-01)`

Verified directly:
| Symbol | Working tree | Committed at `b43e990d` |
|---|---|---|
| `_slot_lock_key` | `subscription_dependencies.py` L903 | **absent** |
| `_acquire_slot_lock` | L907 | **absent** |
| `_release_slot_lock` | L946 | **absent** |
| `exchange_connection_slot_guard` | L1036 | **absent** |
| `store_keys` guard usage | `routers/exchange.py` L428 | **absent (0 occurrences)** |

Both implementation files are uncommitted (` M`) and belong to the Phase 10 Workstream C /
QUOTA-01 workstream. **Required tests #1 and #2**
(`test_the_slot_lock_is_mutually_exclusive_against_real_redis`,
`test_the_guard_admits_exactly_the_limit_against_real_redis`) are in the 6-test
`_slot_lock_key` group — they never reached Redis at all; they failed at attribute lookup.

Note: in Phase 11B-A I read these functions and reported on release atomicity — I was reading
the **working-tree** file and did not notice it was uncommitted. That is the origin of this gap.

### Cause B — `AERORA_TEST_REDIS_URL=.../15` is incompatible with the app's fixed cache DB
**Classification: test-harness/configuration defect introduced by the D5′ workflow env. The
production two-phase idempotency lock is present and correct.**

Required tests #3 and #4, full tracebacks (`webhook.xml`):
```
test_the_lock_is_recorded_in_redis_under_the_documented_key
  redis_client = (Redis(ConnectionPool(Connection(host=localhost,port=6379,db=15))), [...])
  value = client.get(key)
> assert value is not None, f"no idempotency key at {key} after a delivery"
E AssertionError: no idempotency key at webhook:razorpay:evt_90948c27af79 after a delivery
E assert None is not None
tests/test_webhook_duplicate_delivery_real_redis.py:311

test_an_in_flight_delivery_blocks_a_concurrent_one
  client.set(key, "processing", nx=True, ex=60)
  result = await _deliver(handler, _event(event_id))
> assert result.get("status") == "duplicate"
E AssertionError: a delivery arriving while evt_6571563fb645 was still in flight was
  processed anyway: {'status': 'success'}
E assert 'success' == 'duplicate'
tests/test_webhook_duplicate_delivery_real_redis.py:357
```

Mechanism, read out of the code:
- The test's own client honors the URL path: `db=15` (visible in the fixture repr above).
- `backend_app/backend/redis_manager.py` hard-partitions databases — `REDIS_DB_CACHE = 0`,
  `REDIS_DB_QUEUE = 1`, `REDIS_DB_EVENTS = 2` — and `get_redis_url(db)` **overrides** whatever
  database the URL names: `query_params['db'] = str(db)`. The cache is opened as
  `aioredis.from_url(get_redis_url(REDIS_DB_CACHE))`, i.e. **always db 0**.
- So the handler writes/reads the idempotency key in **db 0** while the test inspects **db 15**.
  Test #3 finds nothing; test #4's `processing` marker is invisible to the handler.

The committed razorpay handler's lock is intact (`billing.py` L2346-2355: `get` → return
`duplicate` on `("completed","1","processing")`, then `set(..., nx=True, ex=60)`), which is
why the two handler-only tests pass (`test_a_redelivered_webhook_is_ignored`,
`test_a_different_event_id_is_not_deduplicated`) — both sides of those use db 0 consistently.

**Flagged false assurance (not changed here):**
`test_an_invalid_signature_is_rejected_and_leaves_no_lock_behind` PASSES **vacuously** — it
asserts the key is absent in db 15, which is always true while the app writes db 0. It is
currently proving nothing. This must be re-confirmed once Cause B is fixed.

Also consistent: the metered real-Redis test passes because it creates its own client and does
`incrby`/`get` through that same client — one database throughout, so the `/15` mismatch cannot
bite it.

## 5. Minimum justified follow-up fix

Two independent, small changes — neither weakens anything:

**Fix A (unblocks 21 slot-lock tests, incl. required #1 and #2).** Add the owned implementation
to the PR so the test has its subject:
- `backend_app/core/subscription_dependencies.py` — `_slot_lock_key`, `_acquire_slot_lock`,
  `_release_slot_lock`, `exchange_connection_slot_guard`
- `backend_app/routers/exchange.py` — `store_keys`' `async with exchange_connection_slot_guard(...)`

Both are uncommitted working-tree files whose diffs must be reviewed for unrelated hunks before
inclusion (same hunk-level discipline used for the workflow). *Alternative, if that
implementation is not ready to ship: drop `test_quota01_exchange_slot_is_atomic.py` from this PR
and land it with its implementation.* Do not keep a test in the PR whose subject is absent.

**Fix B (unblocks required #3 and #4).** Drop the `/15` suffix so the test client and the
application cache share one database:
```diff
-          AERORA_TEST_REDIS_URL: redis://localhost:6379/15
+          AERORA_TEST_REDIS_URL: redis://localhost:6379
```
One token, inside the D5′ hunk this workstream owns, in a job whose Redis container is already
ephemeral and exclusive — so the `/15` "belt-and-braces" the D5′ comment claimed buys nothing
and is in fact what broke these two tests. (The alternative — teaching the test to read
`REDIS_DB_CACHE` — touches the harness for no gain and leaves the comment wrong.) The D5′
workflow comment about `/15` must be corrected in the same change.

**Order:** Fix B is a one-line change and independently verifiable; Fix A requires reviewing
two production diffs for ownership. Do Fix B and Fix A together in one re-run so all nine tests
are judged once, then remove the diagnostic job (it is scaffolding).

## 6. Preservation

- `main` / `origin/main` unchanged at `074b772a`; HEAD still `main`; never switched.
- Shared staged index still **22 files**, untouched (private index used throughout).
- Working-tree `01-pr-check.yml` still carries the `provisioning-and-isolation` job — not
  touched, not committed.
- Commit `d07014d3` changed exactly one path (+151/−0). `git add -A` never used.
- No change to `redis_manager.py`, `tests/conftest.py`, `real_redis_gate.py`, any assertion,
  skip or xfail. No production code changed. No merge. PR #6 OPEN.
- Temporary helper scripts and downloaded artifacts deleted from the workspace.

**Acceptance met:** all four required tracebacks are available as durable artifacts and each
has an evidence-based classification. This does **not** close QUOTA-01 — D9/D3/D2, the
order-rate / daily-trade / active-order ceilings, and slot-lock release atomicity remain open,
and the slot-lock real-Redis evidence itself is still unproven pending Fix A.
