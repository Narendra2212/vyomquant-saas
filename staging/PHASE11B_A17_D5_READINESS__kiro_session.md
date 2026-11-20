# Phase 11B-A17 — Minimal D5′ Consolidation Readiness (Kiro session)

**Read-only phase.** No source edit, staging, commit, push, PR modification or closure, CI
trigger, database access, or Redis access. Destination verified FREE immediately before writing;
no existing report or artifact was overwritten or modified.

**Why this report exists.** The brief permits a new report only if it adds material evidence
beyond the existing A16 reports. It does — six items, listed in §2 — the most consequential being
that the blob identifiers every prior D5′ report has quoted for worktree files are **computed
digests, not stored Git objects**. That distinction was asserted nowhere before this phase and is
exactly what the brief warned about.

---

## 1. Fresh baseline and preservation invariants

| Invariant | Value | Status |
|---|---|---|
| branch / HEAD | `main` / `074b772ab16cece62cbfca0f2b78497be2ae56d5` | SAME |
| `origin/main` | `074b772a…` | SAME |
| D5′ local / remote | `d07014d3…` / `d07014d3…` | SAME, unmoved |
| **fp-lsfiles** (historical, SHA-256 over `git ls-files -s`) | `CF474F1B83FD3717…` | **matches every prior phase** |
| **fp-cachedraw** (new canonical, SHA-256 over sorted `git diff --cached --raw`) | `D43D55374D735B24…` | **newly established** |
| **`git write-tree`** (independent invariant) | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` | matches A16's recorded index tree |
| staged / unstaged | 22 / 100 | SAME |
| `.gitignore` | `+15 / −0` (concurrent workstream) | **preserved, untouched** |
| PR #6 / #7 / #8 | `d07014d3` / `49e70da2` / `a624ce89`; all OPEN, `UNSTABLE`, `reviewDecision: ''` | SAME — **still no approvals** |

Two digests are recorded deliberately: `fp-lsfiles` carries continuity with A12–A16, and
`fp-cachedraw` + `write-tree` give a staged-content invariant that is independent of index
stat-cache churn. All three agree that the shared index has not drifted.

## 2. Material new evidence (beyond the existing A16 reports)

### 2a. Computed digests vs stored Git objects — the headline correction

`git hash-object` *computes* an identifier; it does not create an object unless `-w` is passed.
Verified with `git cat-file -e`:

| Path | Identifier | Actually in the object store? |
|---|---|---|
| `tests/test_webhook_duplicate_delivery_real_redis.py` (worktree) | `3ffbccde68…` | **NO — computed-only** |
| `tests/test_quota01_metered_reservation_is_hard.py` (worktree) | `7baff0b64f…` | **NO — computed-only** |
| `.github/workflows/01-pr-check.yml` (worktree) | `91d55a3791…` | **NO — computed-only** |
| `d07014d3:.github/workflows/01-pr-check.yml` | `57e2cee8ea…` | **STORED** |
| `d07014d3:tests/test_webhook_duplicate_delivery_real_redis.py` | `105b65671b…` | **STORED** |
| `d07014d3:tests/test_quota01_metered_reservation_is_hard.py` | `f48103ac79…` | **STORED** |

Two consequences worth stating:
- Every prior report (mine included) that described `3ffbccde` / `7baff0b6` as "blobs" was quoting
  a **content fingerprint**, not an existing object. The wording implied more than was true.
- This is also positive evidence: because nothing was ever staged or written with `-w`, these
  identifiers *should* be absent from the store, and they are. **No accidental staging has ever
  occurred.**
- My withdrawn A14/A15 workflow candidate `a2f39ebb` was likewise computed-only and its temp file
  was deleted, so it no longer exists in any form. Withdrawing it (A16) costs nothing.

### 2b. Line-level proof of the workflow scope
A16 reported `+2 / −2`. The literal changed lines, extracted from the patch:
```
-          AERORA_TEST_REDIS_URL: redis://localhost:6379/15
+          AERORA_TEST_REDIS_URL: redis://localhost:6379/0
-      AERORA_TEST_REDIS_URL: redis://localhost:6379/15
+      AERORA_TEST_REDIS_URL: redis://localhost:6379/0
```
**Four lines, nothing else.** Hunks `@@ -213,7 +213,7 @@` (`unit-tests`) and `@@ -732,7 +732,7 @@`
(`d5-redis-diagnostics`). The 37-line comment rewrite is **absent**, as required.

### 2c. Patch pre/post image pairs, from the patch's own `index` lines
```
index 57e2cee8..967b4a28 100644   .github/workflows/01-pr-check.yml
index 105b6567..3ffbccde 100644   tests/test_webhook_duplicate_delivery_real_redis.py
index f48103ac..7baff0b6 100644   tests/test_quota01_metered_reservation_is_hard.py
```
Every pre-image equals PR #6's stored blob. The webhook and metered post-images equal the
worktree content fingerprints, so the patch reproduces exactly the files reviewed in A12–A16.

### 2d. Quota-test properties verified **from the patch**, not the worktree
Added lines in the patch contain all ten required markers:

| Property | Marker | Status |
|---|---|---|
| exercises the production path | `SubscriptionEngine.reserve_quota` | PRESENT |
| production reader cross-check | `get_quota_usage` | PRESENT |
| fail-closed on configured-but-unreachable | `require_real_redis` | PRESENT |
| refuses a mock cache | `MockRedisClient` | PRESENT |
| bidirectional keyspace proof | `_prove_one_keyspace` | PRESENT |
| keyspace derived, not guessed | `get_redis_url` | PRESENT |
| concurrency | `racers = 50`, `limit = 10` | PRESENT |
| safe cleanup / zero-limit assertion | `await client.delete(key)`, `exists(key)` | PRESENT |

### 2e. Apply check against the canonical base, isolated
`git apply --cached --check` in a **private index** seeded from `d07014d3` → **rc=0**. The shared
index and worktree were never named; the private index file was deleted afterwards.
(A16 recorded a cosmetic whitespace warning under a different flag combination; `--check` alone
reports none. Immaterial either way — both rc=0.)

### 2f. F1 mechanism confirmed symbol-by-symbol
| Check | Result |
|---|---|
| `def _slot_lock_key` in worktree `subscription_dependencies.py` | 1 |
| `async def _acquire_slot_lock` | 1 |
| `async def _release_slot_lock` | 1 |
| `def exchange_connection_slot_guard` | 1 |
| `exchange_connection_slot_guard` references in **`HEAD:exchange.py`** | **0** |
| `subscription_dependencies.py` → HEAD apply (private index) | **rc=0, clean** |
| `exchange.py` → HEAD apply | rc=1, `patch failed: …:366` |

The last two rows are the whole F1 story: the guard file lands cleanly on its own; the call site
does not, and `store_keys` in `HEAD` is genuinely unguarded, so the read-then-write race is live.

## 3. The minimal manifest — verified and recommended

**Use `staging/p11b15_proposed_fixed.patch`** (34,947 B, 652 lines, `+545 / −16` across three
files), parent `d07014d3` (PR #6).

| # | Path | Pre-image (stored) | Post-image | Change |
|---|---|---|---|---|
| 1 | `.github/workflows/01-pr-check.yml` | `57e2cee8` | `967b4a28` | **only** the two `AERORA_TEST_REDIS_URL` values `/15 → /0` (4 lines) |
| 2 | `tests/test_webhook_duplicate_delivery_real_redis.py` | `105b6567` | `3ffbccde` | webhook owner's version: fail-not-skip, application-derived URL, bidirectional keyspace probe (383 → 646 lines) |
| 3 | `tests/test_quota01_metered_reservation_is_hard.py` | `f48103ac` | `7baff0b6` | +2 production-path `reserve_quota` real-Redis tests; all 7 originals preserved (264 → 530 lines) |

**Excluded, by design:** the `provisioning-and-isolation` job (absent from PR #6 and not added),
`backend_app/core/subscription_dependencies.py`, either version of `backend_app/routers/exchange.py`,
any removal of `tests/test_quota01_exchange_slot_is_atomic.py`, and the workflow comment rewrite.

> The comment currently in PR #6 still claims `/15` "pins a dedicated database index …
> belt-and-braces rather than load-bearing". That is factually false and was the proximate cause
> of two CI failures. Correcting it is worth doing as a **separate, separately-approved**
> documentation change; it is deliberately not in this manifest.

## 4. F1 — decision request for the owner of `subscription_dependencies.py`

Not decided here, and not decidable by me.

> **Question.** `backend_app/core/subscription_dependencies.py` (` M`, `+253 / −0`) applies onto
> `main` cleanly (rc=0, verified). Shipping it **alone** supplies all four guard symbols and should
> clear the 20 `AttributeError` slot failures (14 on `exchange_connection_slot_guard`, 6 on
> `_slot_lock_key`, per the A11 JUnit grouping). The 21st,
> `test_store_keys_uses_the_guard_around_its_vault_write`, would remain red — **correctly**, because
> `HEAD:exchange.py` has zero guard references and the read-then-write race is still live.
>
> **Option 1 — approve the standalone guard file.** 20 of 21 slot tests go green; one stays red as
> an honest marker of the unfixed `store_keys` race. Does not touch `exchange.py`.
>
> **Option 2 — defer the guard file** until an owner-approved integration protects the vault write,
> keeping all 21 red for now.

`exchange.py` must not be force-applied or copied under either option: 
it fails to apply to HEAD and is partly staged under TENANT-01.

## 5. Test evidence and its limits

| Check | Command | Exit | Result |
|---|---|---|---|
| Batched collection, 3 D5′ files | `pytest <3 files> --collect-only -q` | 0 | **37 collected** (9 metered + 6 webhook + 22 slot) |
| Patch → PR #6 | `git apply --cached --check` (private index) | **0** | applies |
| Guard file → HEAD | `git apply --cached --recount --check` (private index) | **0** | applies |
| `exchange.py` → HEAD | same | 1 | fails at line 366 |

**Limits, stated rather than glossed:**
- No local real-Redis execution. Every local real-Redis case is an **opt-out skip**, which is
  **not a pass**. Real-Redis acceptance cannot be claimed without an actual real-Redis CI run.
- No CI triggered. No PostgreSQL lane. No bounded suite re-run — unchanged files were already
  measured in A14–A16, and the efficiency rule forbids redundant scans.
- The `d5-redis-diagnostics` job's status is meaningless as a verdict because
  `continue-on-error: true` masks step failures; only JUnit **node IDs** count.

### Expected effect on CI, by node ID

| Node IDs | Count | Candidate's effect |
|---|---|---|
| `test_quota01_exchange_slot_is_atomic.py` (21 nodes) | 21 | **NOT fixed** — F1 excluded. 20 become fixable under F1 Option 1; the 21st stays red correctly |
| `…::test_the_lock_is_recorded_in_redis_under_the_documented_key` | 1 | expected FIXED |
| `…::test_an_in_flight_delivery_blocks_a_concurrent_one` | 1 | expected FIXED |
| `…::test_an_invalid_signature_is_rejected_and_leaves_no_lock_behind` | 1 | currently passes **vacuously**; becomes a real assertion |
| 2 new production-path quota tests | +2 | newly executed; **outcome unknown — that is the point** |
| Pre-existing baseline (run `37346853502`, `6c5660a1`, 39 failed; node IDs in `staging/p11b14_preexisting.txt`) | 39 | untouched; must match by **node ID**, not count |

**PR #6 will still be RED after this patch.** Expected, and must not be reported as success.

## 6. Open decisions and blockers

| | Decision | Status |
|---|---|---|
| **A** | Canonical PR = **#6** (recommended: sole carrier of the diagnostic job; #7 ≡ #8 so one closes regardless) | **awaiting your confirmation** |
| **B** | Webhook-owner consent to land on #6 | **RESOLVED** — author's explicit consent, re-verified in `staging/PHASE11B_A16_D5_APPROVAL_HANDOFF.md` §3.B; their A12 PR-#7 references are baseline rows, not a preference |
| **C** | Standalone F1 guard (Option 1 / Option 2) | **awaiting the `subscription_dependencies.py` owner** |
| **D** | Commit / push / CI | **NOT GRANTED** — 0 reviews, 0 labels, 0 assignees on all three PRs; 127 staging docs scanned in A15/A16 found only requests |
| **E** | Close #7 / #8 | **deferred** until approved content is on #6 and CI evidence reviewed |

This prompt grants none of A–E.

## 7. Exact next safe action

**Nothing further can be done read-only.** The single next action is for you to grant **A** and
**D**, at which point the executable step is precisely:

```
# private-index discipline; no `git add` of any path
GIT_INDEX_FILE=<temp> git read-tree d07014d3
GIT_INDEX_FILE=<temp> git apply --cached staging/p11b15_proposed_fixed.patch
GIT_INDEX_FILE=<temp> git write-tree
git commit-tree <tree> -p d07014d3 -m "<message>"
git update-ref refs/heads/ci/d5-real-redis-quota-evidence <commit> d07014d3   # CAS
git push origin ci/d5-real-redis-quota-evidence
```
then await `01 PR Check` and judge it by the §5 node-ID table — not by job status.

**C** is independent and can be answered in parallel; it changes only how many slot tests stay red.

## 8. Status (unchanged)

- **QUOTA-01: OPEN** — production-path quota evidence prepared but **UNEXECUTED**
- **TENANT-01: OPEN**
- **D5′ real-Redis acceptance: NOT PASSED**
- **F1: BLOCKED** — partially unblockable via the standalone guard file (decision C)
- **F4: UNRESOLVED / UNOWNED** — `backend_app/backend/redis_manager.py` clean; production blast
  radius is 3 `from_url` calls in `initialize()` (DB 0/1/2)
- **F5: UNRESOLVED / UNOWNED** — webhook **fails open** on a cache read fault (`get` → `None`),
  **fails closed** on a write fault (`set` → `False`)
- **Live trading: OFF / NOT CERTIFIED**

No launch-readiness percentage, audit mirror, or certification register was modified.
