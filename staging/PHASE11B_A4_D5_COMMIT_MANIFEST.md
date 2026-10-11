# Phase 11B-A4 — D5′ Commit Manifest (PREPARED, NOT STAGED)

**Status:** prepared only. Nothing was staged, committed, branched, pushed, or triggered.
HEAD `074b772a` (`main`), == `origin/main`. Working tree and all concurrent work preserved.

**Purpose:** define the exact, minimal set of changes that constitute D5′ (the real-Redis
opt-in that lets the CI `unit-tests` job actually execute the gated real-Redis tests), isolated
from the many unrelated concurrent workstreams in the working tree.

---

## 1. Files to INCLUDE (D5′ only)

| # | Path | Git state | Inclusion | Notes |
|---|---|---|---|---|
| 1 | `tests/real_redis_gate.py` | untracked (`??`) | **whole file** (149 lines) | The shared gate. Only cross-`tests` import target of the quota files. |
| 2 | `tests/test_quota01_metered_reservation_is_hard.py` | untracked (`??`) | **whole file** (209 lines) | Metered-reservation logic + `test_concurrent_reservations_are_atomic_against_real_redis`. |
| 3 | `tests/test_quota01_exchange_slot_is_atomic.py` | untracked (`??`) | **whole file** (684 lines) | Slot-lock logic + the two slot real-Redis tests. Imports only `tests.real_redis_gate`. |
| 4 | `tests/test_webhook_duplicate_delivery_real_redis.py` | untracked (`??`) | **whole file** (300 lines) | 6 webhook tests, all gated. |
| 5 | `.github/workflows/01-pr-check.yml` | modified (`M`) | **PARTIAL — hunks 1 & 2 only** | See §2. Must NOT include hunk 3. |

All four test files are untracked, so they are wholly new and carry no entangled diff.

## 2. The workflow file is PARTIAL — hunk-level inclusion required

`git diff -U0 -- .github/workflows/01-pr-check.yml` reports exactly three hunks:

| Hunk | Range | Lines | D5′? | Action |
|---|---|---|---|---|
| 1 | `@@ -185,0 +186,26 @@` | 26 | **YES** | INCLUDE — the D5′ comment block in the `unit-tests` job. |
| 2 | `@@ -189,0 +216 @@` | 1 | **YES** | INCLUDE — `AERORA_TEST_REDIS_URL: redis://localhost:6379/15` env line. |
| 3 | `@@ -467,0 +495,143 @@` | 143 | **NO** | **EXCLUDE** — a new `provisioning-and-isolation` PostgreSQL/privilege-hardening/tenant-isolation job. Different workstream (D-series PostgreSQL), out of scope. |

**How to stage the two D5′ hunks without the third (at authorization time, NOT now):**
```
git add -p .github/workflows/01-pr-check.yml
#   hunk 1 (the D5' comment block)          -> y
#   hunk 2 (AERORA_TEST_REDIS_URL env line) -> y
#   hunk 3 (provisioning-and-isolation job) -> n
```
Verify the staged result with `git diff --cached -- .github/workflows/01-pr-check.yml` and
confirm it contains ONLY the two D5′ hunks (26 + 1 lines) and no `provisioning-and-isolation`.

> Note: hunks 1 and 2 are only ~30 lines apart. If `git add -p` offers them as one combined
> hunk, press `s` to split; if it still won't split cleanly from hunk 3, they are far enough
> apart (line 216 vs 495) that they will always be separate hunks — hunk 3 is independently
> selectable.

## 3. Files to EXCLUDE, with reasons (ownership)

| Path | State | Why excluded |
|---|---|---|
| `tests/conftest.py` | modified (`M`) | **Not D5′.** The 172-line insertion is a RATE-LIMITER LEAK GUARD (`REGRESSION_ORDER_ISOLATION.md`) — `_scan_for_limiters`, `_reset_bound_rate_limiters`, autouse `_rate_limit_counts_are_per_test`. It contains NO `REDIS` reference. D5′ needs ZERO conftest change: committed HEAD conftest already does `os.environ["REDIS_URL"] = ""` and already never touches `AERORA_TEST_REDIS_URL`. The workflow comment "conftest does not touch it" describes existing behavior, not a D5′ edit. |
| `.github/workflows/01-pr-check.yml` hunk 3 | modified | `provisioning-and-isolation` PostgreSQL job — D-series/tenant-isolation workstream. |
| `pytest.ini` | modified (`M`) | Adds a `realaudit` marker (PostgreSQL durable-audit tests). Not D5′, not `asyncio_mode`. The webhook/quota async tests apply `pytest.mark.asyncio` explicitly and do not depend on it (confirmed by the clean local run). |
| All other `M`/`A`/`??` paths in `git status` | various | Unrelated concurrent workstreams: API04 rate-limit (`test_api03/04_*`), tenant-isolation/RLS (`test_tenant*`, `tenant_db_context`, `verify_rls*`), billing, PostgreSQL provisioning (`scripts/provision_database.py`, `migrations/hardening/`, dozens of `*_postgres.py`), terraform, frontend (`algo22-terminal/**`), BUG049, fee/referral/money, etc. None are D5′. |

## 4. Exact prepared command sequence (for AFTER authorization)

> Do NOT run any of this yet. Listed for review. `git add -A` is never used.

```
# 1. new branch off current main (name is a suggestion)
git switch -c ci/d5-real-redis-quota-evidence

# 2. stage the four whole new test files BY NAME
git add tests/real_redis_gate.py \
        tests/test_quota01_metered_reservation_is_hard.py \
        tests/test_quota01_exchange_slot_is_atomic.py \
        tests/test_webhook_duplicate_delivery_real_redis.py

# 3. stage ONLY the two D5' hunks of the workflow
git add -p .github/workflows/01-pr-check.yml      # y, y, n  (see §2)

# 4. verify the staged set is exactly D5'
git diff --cached --stat
git diff --cached -- .github/workflows/01-pr-check.yml   # must show only hunks 1 & 2

# 5. commit
git commit -m "ci(d5): export AERORA_TEST_REDIS_URL so real-Redis quota tests execute in CI"

# 6. push + PR (SEPARATE authorization)
git push -u origin ci/d5-real-redis-quota-evidence
gh pr create --fill
```

## 5. Verification that the staged set is complete and self-contained

- The three gated test files import exactly one shared symbol source: `tests.real_redis_gate`
  (included). No other cross-`tests` imports exist (`grep` confirmed).
- No new runtime/production code is required: the tests exercise existing
  `SubscriptionEngine.reserve_quota`, `subscription_dependencies` slot-lock, and
  `backend_app.routers.billing.razorpay_webhook`, all already committed at HEAD.
- No conftest or pytest.ini change is needed (see §3).

**Commit footprint: 4 new files + 2 workflow hunks (27 added lines in the workflow).**
