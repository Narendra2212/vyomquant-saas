# Phase 11B-A8 — Read-Only Rebaseline and D5′ Failure Diagnostics

**Scope of authority used:** read-only inspection and diagnostics only. No implementation
changes were made. No `checkout`, `reset`, `clean`, `stash`, `add`, `commit`, `push`,
`merge`, or CI trigger was executed. No production access, no venue calls, no remote
writes.

**Live trading:** OFF / NOT CERTIFIED.
**QUOTA-01:** OPEN (see §8).

> **A stop condition was met.** Three branches and three open pull requests carry the same
> D5′ change set, and the corrections for the observed CI failures exist on none of them.
> A human consolidation decision is needed before any fix is committed. Details in §4
> and §7. Diagnostics continued because they are read-only and do not touch shared
> infrastructure; nothing was changed while the question is open.
>
> **Ownership is partly resolved.** After the diagnostics below were complete, a parallel
> workstream's own report — `staging/PHASE11B_A12_D5_MINIMAL_REPAIR_REPORT.md` — was
> located and cross-checked. It identifies **PR #6 as that workstream's** (Phase 11B
> A9→A12) and reaches the same two root causes independently. §10 records the
> cross-check: five of its claims verified, two of my own figures corrected by it, and one
> defect class it found that these diagnostics had missed. **PR #7 remains unattributed.**

---

## 1. Baseline and index integrity

Measured at entry and again at exit. Byte-identical both times.

| Property | Value |
|---|---|
| `git rev-parse HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| `git rev-parse --abbrev-ref HEAD` | `main` |
| `git rev-list --left-right --count origin/main...HEAD` | `0  0` (no divergence) |
| `.git/index` SHA-256 at entry | `6d965c8eafdd23eeccea41a6bff25df5d9f1ee9fc64dfa17c29c965210458613` |
| `.git/index` SHA-256 at exit | `3dbf1cb07e58b1e9ffb1a70154f9c008ad539188f45ab2cbe2b367fc1caf5676` — **moved, see below** |
| `.git/index` size | 232,081 bytes — **identical at entry and exit** |
| **Staged content** (`git ls-files -s`) SHA-256 | `98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704`, 2104 entries |
| Staged paths (`git diff --cached --name-only`) | **22** |
| Unstaged paths (`git diff --name-only`) | **100** |
| Untracked paths (`git ls-files --others --exclude-standard`) | **3259** |

### 1a. The index checksum moved. Staged content did not.

An interim version of this report claimed the `.git/index` SHA-256 was identical at entry
and exit. **That claim was wrong**, and the correction matters more than the figure, because
it invalidates the invariant this programme has been quoting since A3.

The explanation, with confirmed and inferred parts kept apart. **Confirmed:**
`core.autocrlf = true` and `core.fscache = true` on this repo (`git config --get`), and the
earlier `git status` runs in this phase emitted ~37 `LF will be replaced by CRLF` warnings —
git actively re-checking line endings. **Inferred** from that plus the evidence below: git
rewrote the per-entry **stat cache** inside `.git/index` (device, inode, mtime, ctime,
size), which changes the index file's bytes while changing no staged byte. `git status` and
`git diff` are read-only with respect to *staged content* but **not** byte-read-only with
respect to the index file.

I did not catch the rewrite in the act — by the time I measured, the cache had settled, so
a fresh `git status --porcelain` now moves neither hash. The inference is therefore the
best-supported explanation rather than a direct observation. What is *not* inference is that
no staged byte moved, which is what the isolation claim actually rests on.

Corroborating detail: the index is **232,081 bytes at entry and 232,081 bytes at exit** —
same size, different content. A staging change would almost certainly have moved the size.

Staged content verified intact by four independent measures:

| Measure | Result |
|---|---|
| `git ls-files -s` — every index entry's mode, blob SHA-1, stage, path | 2104 entries, SHA-256 `98ae5bdf…` |
| `git diff --cached --name-status` | the same **22** paths |
| `git diff-index --cached HEAD` — staged blob identities | 22 rows, all resolvable |
| `git ls-files -u` — unmerged/conflicted entries | **0** |
| `git rev-parse HEAD` + `git reflog -n 3` | `074b772a`; newest reflog entry 2026-10-05, i.e. **no new commit** |

No staging command was run in this phase — no `add`, `stash`, `checkout`, `reset`, or
`commit`. A re-test confirmed the mechanism directly: running `git status --porcelain`
again now leaves both the index file hash and the staged-content hash unchanged, because
the cache has settled.

**Methodological consequence.** `.git/index` SHA-256 is not a valid isolation invariant on
this repository; it will drift from benign reads. The correct invariant is the
**staged-content hash** (`git ls-files -s`), together with `HEAD` and the reflog. Future
phases should quote those. A12's report quotes the old checksum as unchanged across its own
window, which was true then and is not contradicted here — the drift happened later.

### 1b. Local state not previously recorded

`git branch --list` shows **15 local branches**, including all three D5′ branches
(`ci/d5-real-redis-switch`, `ci/d5-prime-real-redis`, `ci/d5-real-redis-quota-evidence`).
`git stash list` shows **2 pre-existing stash entries**. Neither was created by this phase;
no stash was pushed or popped here (stashing was explicitly rejected in A7 because it would
have buried other workstreams' changes). They are recorded because an untracked stash is a
place where work can hide, and §11 warns that the three corrections are currently
uncommitted.

The staged set belongs to other workstreams and was not disturbed. Of the 22, one matters
to this report: `backend_app/routers/exchange.py` (§5.2).

Commands and exit codes:

```
git rev-parse HEAD                                      -> 0
git rev-parse --abbrev-ref HEAD                         -> 0
git rev-list --left-right --count origin/main...HEAD    -> 0
certutil -hashfile .git\index SHA256                    -> 0   (moved; see 1a)
git ls-files -s                                         -> 0   (2104 entries, stable)
git diff --cached --name-only                           -> 0   (22 paths)
git diff --name-only                                    -> 0   (100 paths)
git ls-files -u                                         -> 0   (0 unmerged)
```

The 22 staged paths are an identifiable workstream, which settles who they belong to:
tenant-isolation / RLS / credential-vault work — `backend_app/core/tenant_db_context.py`,
`scripts/verify_rls_enforcement.py`, `docs/adr/ADR-0006…`, `ADR-0007-per-tenant-credential-
encryption-keys.md`, `terraform/cloudtrail.tf`, and five `tests/test_tenant_*` /
`test_sc3_*` files, plus modifications to `backend_app/routers/auth.py`,
`backend_app/core/dependencies.py` and **`backend_app/routers/exchange.py`**. The last of
these is the entanglement in §5.2, and this confirms A12's attribution of it.

Historical note: the unstaged count was 97 at A3/A4 and is 100 now; untracked was 3207 when
A12 measured it and is 3259 now. Both grew in other workstreams' files and neither is
attributable to this phase, which wrote only to `staging/`. The staged count and the index
checksum are the two figures that matter for isolation, and both are unchanged.

---

## 2. Verified PR, commit, and run identity

`git ls-remote` plus the GitHub REST API were used for identity. **Three parallel branches
exist for the same D5′ work, each with an open PR.** Keeping their runs separate, as
instructed:

| PR | Branch | Head commit | Parent | Insertions | `01 PR Check` run(s) | Started (UTC) | Conclusion | Owner |
|---|---|---|---|---|---|---|---|---|
| **#6** | `ci/d5-real-redis-quota-evidence` | `d07014d36d32c63db44f8fb9659933c0e84e82f2` | `b43e990d` — **not `main`** | 1879 | `38047483583`, `38043513862` | 11:10, 10:02 | failure, failure | **Phase 11B A9→A12** (§11) |
| **#7** | `ci/d5-prime-real-redis` | `49e70da23c2858ffc9a6f34d5db4521401477c82` | `074b772a` | 1761 | `38044138718` | 10:13 | failure | **unattributed** |
| **#8** | `ci/d5-real-redis-switch` (opened in A7) | `a624ce89b16005b0c73197005d6d0672737dad65` | `074b772a` | 1761 | `38046625040` | 10:55 | failure | this workstream (A7) |

PR #8: `https://github.com/Narendra2212/vyomquant-saas/pull/8` — OPEN, not merged.
All four runs completed with conclusion `failure`.

Two things distinguish PR #6 from the other two, beyond its parent commit:

- It adds a **`d5-redis-diagnostics` job** to `01-pr-check.yml`. Verified present on PR #6
  and absent from `main`, PR #7, PR #8, and the working tree. This accounts for most of its
  larger workflow blob (43,699 B vs 35,841 B) and its larger insertion count.
- Its `tests/test_webhook_duplicate_delivery_real_redis.py` is a **third distinct variant**
  (383 lines) rather than a copy of PR #7/#8's (416 lines). See §4.

A12 reports that PR #6's run `38047483583` reached gate `state=run`. That is its evidence,
not a measurement of mine; I decoded only run `38046625040`.

Only run `38046625040` (PR #8) was decoded in full; its job log was the artefact captured
in A7. The failure reasons below therefore describe **PR #8's run only**. PR #7's run
`38044138718` is cited once, in §5.3, because the working-tree webhook fix names it
explicitly as its own motivation — that is the fix author's attribution, not a measurement
of mine.

### Run `38046625040`, job `Unit & Integration Tests` (id `114197790236`)

`https://github.com/Narendra2212/vyomquant-saas/actions/runs/38046625040/job/114197790236`

Confirmed from the job log:

- Service container `redis:7-alpine`, resolved version **7.4.11**, `-p 6379:6379`,
  log line `redis service is healthy.`
- Container log contains `100 changes in 300 seconds. Saving...` followed by
  `DB saved on disk` — independent evidence that real writes reached a real server, not a
  mock.
- `AERORA_TEST_REDIS_URL: redis://localhost:6379/15` present in the job environment.
- Gate skip-reason occurrences: **0**. Real-Redis tests skipped: **0**. The opt-in gate
  reached `run`, so the real-Redis assertions genuinely executed.

Final pytest tally, verbatim:

```
= 62 failed, 12029 passed, 51 skipped, 322 deselected, 4 xfailed, 3 xpassed, 315 warnings in 418.40s (0:06:58) =
```

**The required test passed:**
`tests/test_quota01_metered_reservation_is_hard.py::test_concurrent_reservations_are_atomic_against_real_redis`
→ **PASSED** against the real server.

### Correction to the A7 report

A7 reported `60 failed, 11836 passed` and concluded the FAILURES section had been
"truncated by the log transport." Both statements were wrong, from one cause: PowerShell's
`>` redirect writes **UTF-16LE with a BOM**, and the A7 scripts opened the file as UTF-8
with `errors="replace"`. The log was intact at 5,255,192 bytes (2,627,595 characters); the
decode was broken. Decoded as UTF-16, the log yields pytest's own
`short test summary info` — one line per failure, with its reason — which is the evidence
A7 reported as unobtainable. The true tally is `62 failed, 12029 passed`.

A second, smaller correction belongs to this phase. An interim A8 count of "37
pre-existing failures" came from a strict line-anchored regex that silently dropped two
`test_tenant_isolation_library_paper.py` entries whose assertion text wrapped onto
following lines. A loosened parser recovers **62 of 62**, matching pytest's own tally
exactly. The corrected figure is **39** pre-existing. The two numbers that follow —
62 total, 39 pre-existing — are the reconciled ones.

---

## 3. Complete failure inventory — run `38046625040` (PR #8)

62 entries, all `FAILED` (zero `ERROR`), reconciled against pytest's own count.

| Count | File | Origin |
|---:|---|---|
| **21** | `tests/test_quota01_exchange_slot_is_atomic.py` | **D5′ (new in this commit)** |
| **2** | `tests/test_webhook_duplicate_delivery_real_redis.py` | **D5′ (new in this commit)** |
| 13 | `tests/test_no_credential_in_api_response_body.py` | pre-existing |
| 9 | `tests/test_validation_sweep.py` | pre-existing |
| 7 | `tests/test_task_6_1_backtest_execute.py` | pre-existing |
| 4 | `tests/test_strategy_status_publish.py` | pre-existing |
| 2 | `tests/test_tenant_isolation_library_paper.py` | pre-existing |
| 1 | `tests/property/test_tenant_isolation_matrix.py` | pre-existing |
| 1 | `tests/test_ml_deployment_guards.py` | pre-existing |
| 1 | `tests/test_task_33_2_rate_limit_keys.py` | pre-existing |
| 1 | `tests/test_task_8_4_runtime_readiness_gate.py` | pre-existing |

**23 failures are attributable to the D5′ commit; 39 are not.**

Classification against the requested categories:

| Category | Count | Cluster |
|---|---:|---|
| 1. Missing API in the tested commit | **21** | exchange-slot suite |
| 2. Genuine exchange-slot concurrency defect | **0** | — |
| 3. Webhook Redis keyspace mismatch | **2** | webhook duplicate-delivery suite |
| 4. Fixture or test-isolation defect | **0** | — |
| 5. Unrelated baseline failure | **39** | nine pre-existing files |
| 6. Unresolved | **0** | — |

Category 2 is **nil**: no failure in the exchange-slot suite reports a concurrency outcome.
Twenty of the twenty-one never reached an assertion at all — they died at attribute lookup
during setup. The twenty-first is a static wiring assertion. The suite never got far
enough to be able to observe a concurrency defect, so this run is silent on whether one
exists. That is an absence of evidence, not evidence of absence.

---

## 4. Concurrent work and manifest conflicts

The D5′ manifest is five paths. All three branches ship the identical five:

```
.github/workflows/01-pr-check.yml
tests/real_redis_gate.py
tests/test_quota01_metered_reservation_is_hard.py
tests/test_quota01_exchange_slot_is_atomic.py
tests/test_webhook_duplicate_delivery_real_redis.py
```

Content drift across refs. Sizes are **CRLF-normalised** and each row carries a SHA-256
prefix of the normalised bytes, so identity is decided by content rather than by on-disk
line endings. `absent` = path not present at that ref.

| Path | Worktree | `main` | PR #8 | PR #7 | PR #6 | Worktree vs branches |
|---|---:|---:|---:|---:|---:|---|
| `.github/workflows/01-pr-check.yml` | 46891 | 33886 | 35841 | 35841 | 43699 | **differs** (§5.5) |
| `tests/real_redis_gate.py` | 8003 `5fa398a1` | absent | 8003 `5fa398a1` | 8003 `5fa398a1` | 8003 `5fa398a1` | identical |
| `tests/test_quota01_metered_reservation_is_hard.py` | 11978 `ffdb7f47` | absent | 11978 `ffdb7f47` | 11978 `ffdb7f47` | 11978 `ffdb7f47` | identical |
| `tests/test_quota01_exchange_slot_is_atomic.py` | 40937 `ed8d2ff8` | absent | 40937 `ed8d2ff8` | 40937 `ed8d2ff8` | 40937 `ed8d2ff8` | **identical** |
| `tests/test_webhook_duplicate_delivery_real_redis.py` | 33232 `fee0f339` | absent | 19483 `c6f1b45c` | 19483 `c6f1b45c` | 17291 `5b513667` | **differs** (§5.3) |
| `backend_app/core/subscription_dependencies.py` | 53984 | 39797 | 39797 | 39797 | 39797 | **differs** (§5.1) |
| `backend_app/routers/exchange.py` | 55614 | 34999 | 34999 | 34999 | 34999 | **differs** (§5.2) |

Correction to an earlier figure in this phase. An interim drift table listed the
exchange-slot test as 41,804 B in the worktree against 40,937 B on the branches, which
reads as a content difference. It is not one: the file is CRLF on disk and LF in the git
objects. Normalised, all four are `ed8d2ff8…` — **byte-identical**. A first attempt to
check this "found" a difference at line 17 that was an artefact of comparing
locale-decoded `git show` text against a UTF-8 file read; redone with raw bytes via
`git cat-file blob`, the files are the same. Two of the five manifest paths differ in
content between the worktree and the branches, not three.

Four conflicts follow from this table.

1. **Triplicated work.** Three branches, three open PRs, one change set. PR #6 is rooted on
   `b43e990d` rather than `main`, so its diff is not comparable with the other two (1879 vs
   1761 insertions) and its two runs carry a different baseline.
2. **The two production files are byte-identical to `main` on every branch.** Whatever the
   exchange-slot suite tests, no branch ships it.
3. **The webhook test exists in three different versions across the three PRs** (383, 416,
   416 lines) and a fourth, newest version in the working tree (646 lines). Only the
   working-tree version contains the keyspace fix.
4. **The corrections live in the working tree**, on no branch (§5).

---

## 5. Confirmed findings

Each finding below is supported by a file-and-line read or a log line, not inference.

### 5.1 Category 1 — the exchange-slot suite ships without its subject (21 failures)

Failure reasons, from `short test summary info`:

| Count | Reason |
|---:|---|
| 14 | `AttributeError: module 'backend_app.core.subscription_dependencies' has no attribute 'exchange_connection_slot_guard'` |
| 6 | `AttributeError: module 'backend_app.core.subscription_dependencies' has no attribute '_slot_lock_key'` |
| 1 | `AssertionError: store_keys does not use exchange_connection_slot_guard; the connection limit is back to a read-then-write race (QUOTA-01)` |

The twenty-first (`test_store_keys_uses_the_guard_around_its_vault_write`) is the suite's
own wiring check. It fails for the same underlying reason as the other twenty, by a
different route: it asserts on production structure instead of importing the symbol.

Symbol presence by revision, via `git grep <symbol> <rev> -- '*.py'`:

| Revision | `exchange_connection_slot_guard` | `_slot_lock_key` | Definitions |
|---|---:|---:|---:|
| `HEAD` / `main` `074b772a` | 0 hits | 0 hits | **0** |
| PR #8 `a624ce89` | 19 hits | 6 hits | **0** |
| PR #7 `49e70da2` | 19 hits | 6 hits | **0** |
| PR #6 `d07014d3` | 19 hits | 6 hits | **0** |

**All 19 + 6 hits on all three branches are inside
`tests/test_quota01_exchange_slot_is_atomic.py`.** Zero are definitions. The branches
contain the test's *references* to an API that the branches do not contain. This is a more
precise statement than my interim note that "no branch carries the guard API" — the name is
present on every branch; the implementation is present on none.

The implementation exists, uncommitted, in the working tree:

```
backend_app/core/subscription_dependencies.py:903   def _slot_lock_key(user_id: str, resource: str) -> str:
backend_app/core/subscription_dependencies.py:1036  async def exchange_connection_slot_guard(
backend_app/core/subscription_dependencies.py:1088      lock_key = _slot_lock_key(user_id, resource)
```

Status ` M` (unstaged, 53,984 B vs 39,797 B at `main`).

**Conclusion.** The failures are not a defect in the guard and not a test-isolation
problem. The test was committed without the production code it exercises. Nothing about
the guard's concurrency behaviour can be concluded from this run.

### 5.2 The guard's call site is entangled with another workstream's staged work

The twenty-first failure demands a call site in `backend_app/routers/exchange.py`. That
file is `MM` — staged **and** unstaged modifications — and its staged half belongs to
another workstream.

| Version | Size | Lines | Guard call site |
|---|---:|---:|---|
| `HEAD` | 34,999 B | 825 | **absent** (0 hits) |
| **Index (staged)** | 46,992 B | 1078 | **absent** (0 hits) |
| Worktree | 55,614 B | 1218 | **present** — `:26` import, `:428` `async with exchange_connection_slot_guard(...)` |

All three versions retain the pre-existing `check_exchange_connection_slot` call, which the
test file describes as the read-then-write race being replaced.

Diffstats, measured (`git diff --numstat`):

| Layer | `backend_app/routers/exchange.py` | `backend_app/core/subscription_dependencies.py` |
|---|---|---|
| Staged (index vs `HEAD`) | **+268 / −15** — another workstream's | *(no change)* |
| Unstaged (worktree vs index) | **+154 / −14** — contains the guard wiring | **+253 / −0**, purely additive |

**Consequence for any fix.** The guard's *definition* is cleanly separable:
`subscription_dependencies.py` has nothing staged and its change is purely additive. The
guard's *call site* is not: it sits in the unstaged layer of a file whose staged layer is a
+268/−15 change owned by another workstream.

A12 proved this empirically, and the proof is stronger than my static comparison. It
attempted to lift the unstaged hunks onto `HEAD` through a private index:

```
git apply --cached --recount --check <unstaged exchange.py patch>    (private index = HEAD)
  error: patch failed: backend_app/routers/exchange.py:366
  error: backend_app/routers/exchange.py: patch does not apply       (exit 1)
```

So the guard hunks are expressed against the index state that includes the other
workstream's 268 staged lines. Committing the guard therefore means either shipping that
workstream's unapproved change, or hand-rewriting the integration against `HEAD` — i.e.
fabricating a variant that has never been executed. This is the concrete form of the
ownership stop condition, and it is why A12 stopped at the same point.

Correction to an earlier figure in this phase: an interim note described this as "a
253-line staged change." That conflated the two files. The staged change to `exchange.py`
is +268/−15; +253/−0 is the *unstaged* change to `subscription_dependencies.py`.

### 5.3 Category 3 — webhook Redis keyspace mismatch (2 failures)

Failure reasons:

```
FAILED tests/test_webhook_duplicate_delivery_real_redis.py::test_the_lock_is_recorded_in_redis_under_the_documented_key
  - AssertionError: no idempotency key at webhook:razorpay:evt_19649b746355 after a delivery
FAILED tests/test_webhook_duplicate_delivery_real_redis.py::test_an_in_flight_delivery_blocks_a_concurrent_one
  - AssertionError: a delivery arriving while evt_3e220c56166b was still in flight was processed anyway: {'status': 'success'}
```

Root cause, read from `backend_app/backend/redis_manager.py` (note: a *different* module
from `backend_app/core/cache/redis_manager.py`; both exist, and the distinction matters):

```
 37: REDIS_DB_CACHE = 0                        # the cache is pinned to database 0
 43: REDIS_URL = os.getenv("REDIS_URL", "")    # module-level, read ONCE at import
 49: def get_redis_url(db: int = 0) -> str:
 69:     query_params['db'] = str(db)          # overrides whatever db the URL named
140: cache = await aioredis.from_url(
141:     get_redis_url(REDIS_DB_CACHE), ...
```

In CI, `REDIS_URL=redis://localhost:6379` and
`AERORA_TEST_REDIS_URL=redis://localhost:6379/15`. Line 69 appends `?db=0`, and redis-py
resolves the query parameter in preference to the URL path:

```
parse_url('redis://localhost:6379/15?db=0') -> {'db': 0, 'host': 'localhost', 'port': 6379}
```

So the application handler wrote its idempotency lock to **database 0** while the test
client read **database 15**. Both assertions are correct; they were looking in the wrong
place. The dedup logic itself is not implicated — `DB saved on disk` in the container log
confirms writes were landing, just not where the test looked.

Line 43 compounds it: `REDIS_URL` is a module constant captured at import, so
`monkeypatch.setenv("REDIS_URL", ...)` cannot retroactively move the application to
database 15. Pointing the app at `/15` is not available as a fix from the test side.

**The fix exists only in the uncommitted working tree.** `_app_cache_url()` at line 104 of
`tests/test_webhook_duplicate_delivery_real_redis.py` (33,232 B on disk; 19,483 B on PRs
#8/#7 and 17,291 B on PR #6). It derives the application's effective cache URL by calling
`get_redis_url(REDIS_DB_CACHE)` rather than hard-coding `/0`, imports lazily so the
constants are already frozen, and refuses any non-loopback host unless an explicit
allow-remote variable is set. The file's own docstring attributes the diagnosis to CI run
**`38044138718`** — PR #7's run — and labels itself "Phase 11B-A9 → A12," i.e. a parallel
workstream reached the same conclusion independently.

A12 measured the redis-py precedence directly, offline, against the installed client — a
stronger form of the same evidence than reading the docstring's worked example:

```
redis://localhost:6379/15       -> db = 15
redis://localhost:6379/15?db=0  -> db = 0      <-- query beats path
redis://localhost:6379?db=0     -> db = 0
redis://localhost:6379          -> db = None
redis://localhost:6379/0        -> db = 0
```

The worktree version does two further things beyond relocating its client, both of which
raise its value above a bare fix and are worth preserving in whatever lands:

- `_prove_one_keyspace(manager, client)` — a **bidirectional** sentinel probe (application
  writes → test reads, and test writes → application reads) that fails with an explicit
  `KEYSPACE SPLIT` message. This converts the failure mode from a confusing assertion about
  a missing key into a named diagnosis, so the next occurrence is self-explaining.
  Sentinels are namespaced under `aerora:test:keyspace-probe` with a 30 s TTL and deleted
  through both paths.
- It strengthens the `real_redis_cache` fixture from `pytest.skip` to **`pytest.fail`** when
  the manager resolves to `MockRedisClient`. A required real-Redis assertion may not be
  satisfied by a skip; this closes the hole where a mock would have produced a silent pass.

A caveat the fix states about itself, which should not be lost in a summary: it does **not**
restore `/15` keyspace isolation. It moves the test client onto the application's cache
database (0). The documented reasoning is that `/15` was never where the application wrote,
so the isolation it promised was illusory; every key the file creates is uuid-suffixed and
registered for teardown. Restoring real isolation requires `get_redis_url` to honour an
explicitly configured database — an application change, owned elsewhere.

**It has never been executed against a real server.** A12 confirms it imports, collects and
skips cleanly with the opt-in unset, which is the limit of what local runs can assert. Its
probe is unproven.

One methodological note: `git grep _app_cache_url` returns exit 1 even in the working tree.
That is not evidence of absence. The file is **untracked**, so `git grep` skips it; reading
the file from disk confirms the helper at line 104.

### 5.4 Category 5 — pre-existing failures (39)

Present independently of D5′ and outside this phase's remit. Listed so they are not
misattributed to the D5′ commit:

| Count | File | Representative reason |
|---:|---|---|
| 13 | `test_no_credential_in_api_response_body.py` | `POSITIVE CONTROL FAILED ...` planted sentinel absent from response |
| 9 | `test_validation_sweep.py` | assorted |
| 7 | `test_task_6_1_backtest_execute.py` | `DeployBlueprintIncomplete: The stored blueprint for strategy strategy_task_8_5 cannot start` |
| 4 | `test_strategy_status_publish.py` | assorted |
| 2 | `test_tenant_isolation_library_paper.py` | pinned Requirement 21.4 gap; recorded answers have moved |
| 1 | `property/test_tenant_isolation_matrix.py` | `Requirement 21.8: 4 of the 150 attempted cells failed` |
| 1 | `test_ml_deployment_guards.py` | `FileNotFoundError: .../connection_layer/routers/` |
| 1 | `test_task_33_2_rate_limit_keys.py` | `assert 429 == 422` |
| 1 | `test_task_8_4_runtime_readiness_gate.py` | `assert 429 == 422` |

### 5.5 A third defect, found by A12 and missed by these diagnostics: the gate proves the wrong database is alive

The same `?db=0` override that split the webhook keyspace also makes the **opt-in gate's
liveness check point at a database the application never uses.**

`AERORA_TEST_REDIS_URL` has two jobs: it is the opt-in switch, and it is the endpoint the
gate PINGs to decide `run` vs `fail`. Measured values:

| Ref | `AERORA_TEST_REDIS_URL` in `01-pr-check.yml` |
|---|---|
| `main` | *(absent — the variable does not exist)* |
| PR #8 | line 216: `redis://localhost:6379/15` |
| PR #7 | line 216: `redis://localhost:6379/15` |
| PR #6 | line 216 **and** line 735: `redis://localhost:6379/15` (twice — the second is the `d5-redis-diagnostics` job) |
| **Worktree** | line 248: `redis://localhost:6379/0` — **A12's uncommitted fix** |

So in all four CI runs the gate PINGed **database 15** and reported the real-Redis
environment healthy, while the application's cache was on **database 0**. The gate's
`state=run` is therefore a true statement about a server but not about the keyspace under
test. It is a false assurance rather than a wrong result: the server is the same, so
liveness does transfer; the keyspace does not.

A12 changed the value to `/0` locally so that the database the gate proves reachable is the
database the application actually uses, and rewrote the comment that had claimed `/15`
"pins a dedicated database index … belt-and-braces rather than load-bearing" — a claim
that was false in both halves. **That change is uncommitted and on no branch.**

Two follow-ups it creates, both unresolved:

- PR #6 carries the `/15` value **twice**; the second occurrence, in the
  `d5-redis-diagnostics` job, needs the same one-token change in whatever commit lands this.
- The working-tree webhook test's docstring still quotes the CI value as
  `AERORA_TEST_REDIS_URL: redis://localhost:6379/15`. That quote is now stale. Its
  *conclusion* is unaffected — its client derives the URL from the application, so the
  gate's database is irrelevant to its keyspace — but the quoted value should be refreshed
  by its owner.

This did not cause any of the 62 failures, so it is not a failure category. It is a
correctness defect in the D5′ evidence mechanism itself, and it matters because the gate is
the thing D5′ exists to prove.

---

## 6. Hypotheses — not confirmed

Stated separately so they are not read as findings.

1. **Whether the worktree guard is correct.** Its 21 tests have never executed against it
   on any branch or in CI. A12 ran them locally and got 22 passing slot-lock tests — but
   that is **not** evidence about the guard's correctness under concurrency, and A12 says so
   itself: the tests passed *because the working tree contains the implementation*, with the
   9 real-Redis cases skipping on the unset opt-in. Local green here is an artefact of the
   uncommitted tree. The guard may be right, wrong, or partially wired; no run so far can
   say.
2. **Whether the two webhook failures are the only keyspace casualties.** The same `?db=0`
   override affects any real-Redis test that reads what the application cache wrote. Only
   this file's two assertions failed in run `38046625040`, but the real-Redis suite is small
   (9 gated tests); the pattern could resurface as more are added. The worktree's
   `_prove_one_keyspace` probe would catch it explicitly if adopted (§5.3).
3. **Whether the webhook fix actually works.** Unproven. It has been executed only in the
   collect-and-skip path (§5.3). Its first real test is the next CI run.
4. **Why PR #6 is rooted on `b43e990d`.** Not investigated. Could be an intentional stack
   or an accident of branching. A12 does not explain it either.
5. **Whether the 39 pre-existing failures are stable or load-sensitive.** Earlier in this
   programme, two dashboard failures I had labelled "pre-existing" turned out to be
   load-sensitive flakes that passed 3/3 at a pristine checkout. That correction is a
   standing reason not to assume this cluster is static without a second run. No such run
   was performed here.
6. **Who owns PR #7.** A12 accounts for PR #6 and A7 accounts for PR #8. PR #7 is
   byte-identical to PR #8 across all five manifest paths and shares its parent, which is
   consistent with a duplicate attempt by either workstream, but nothing read here
   attributes it.

Two hypotheses carried earlier in this phase are now **resolved** and have moved into §5:
that the exchange-slot test might differ in content between the worktree and the branches
(it does not — §4), and that the guard's definition might be as entangled as its call site
(it is not — §5.2).

---

## 7. Minimal fix plan

Ordered by dependency. **Every step needs separate authorization; none is authorized by
this task, and none was performed.**

### Step 0 — Consolidate to one branch (blocking, human decision)

Three open PRs carry the same change set. Until one is chosen, any fix lands on an
arbitrary third of the work.

- **Owner:** the Phase 11B A9→A12 workstream (PR #6) and this one (PR #8), jointly.
  PR #7's author is unidentified (§6, item 6) and needs to be found or the branch treated
  as abandoned.
- **Action:** pick one branch; close the other two.
- **Recommendation:** PR #8 or PR #7. Both are rooted on `074b772a` and are byte-identical
  across all five manifest paths, so either gives a diff reviewable against `main`. PR #6
  is rooted on `b43e990d` and additionally carries a `d5-redis-diagnostics` job and a third
  variant of the webhook test, so adopting it means reviewing a different baseline plus
  scaffolding that A12 itself lists for later removal.
- **Counter-consideration:** PR #6 is the one its owner is actively working against, and
  A12's local workflow edit (§5.5) is expressed against the worktree, not against PR #8.
  Whoever consolidates should confirm with that owner rather than assume PR #8 wins on
  cleanliness alone.
- **Dependency:** none. This unblocks everything below.
- **Authorization:** PR close requires remote write. Explicitly out of scope here.

### Step 1 — Resolve the exchange-slot suite: ship the API, or withdraw the test

Two mutually exclusive options. **Option B is the smaller and safer change.**

**Option A — commit the guard with its test.** Add to the chosen branch:

- `backend_app/core/subscription_dependencies.py` — `_slot_lock_key` (`:903`) and
  `exchange_connection_slot_guard` (`:1036`), currently ` M`, worktree-only.
- `backend_app/routers/exchange.py` — the import (`:26`) and the
  `async with exchange_connection_slot_guard(...)` call site (`:428`), worktree-only.

Consequences to accept:
- The call site is in a `MM` file. The commit must be split hunk-by-hunk from another
  workstream's **+268/−15** staged change, or that change must be carried along — and A12's
  `git apply --check` proof (§5.2) shows the hunks do not lift onto `HEAD` cleanly, so the
  split means hand-rewriting an integration that has never been executed. **Needs the other
  owner's sign-off.**
- It changes a production write path for exchange-credential storage. It is a functional
  change, not CI plumbing, and belongs in its own reviewed PR with its own test evidence —
  not folded into a CI-evidence PR.
- The 21 tests have never run against this implementation under concurrency. Expect
  iteration.

**Option B — drop `tests/test_quota01_exchange_slot_is_atomic.py` from the D5′ manifest.**
Reduces the manifest from five paths to four. Eliminates all 21 failures without touching
production code, and leaves D5′ as what it was scoped to be: CI evidence that the metered
reservation path is atomic against real Redis. The exchange-slot guard then proceeds on its
own schedule, as its own change, with this test as its acceptance criterion.

**A12 reached the same recommendation independently**, and names the owner more precisely
than I can: land the test together with its implementation under the **Phase 10 Workstream
C / credential-vault workstream** that holds `exchange.py`, and do not copy that
workstream's code. Two separate read-only analyses converging on Option B is the strongest
signal in this report.

- **Owner:** QUOTA-01 workstream to withdraw it; Phase 10 Workstream C / credential-vault
  to land it later with the implementation.
- **Dependency:** Step 0.
- **Authorization:** commit + push to the chosen branch.

### Step 2 — Include the working-tree webhook fix

Add the worktree version of `tests/test_webhook_duplicate_delivery_real_redis.py`
(33,232 B, containing `_app_cache_url()`) to the chosen branch, replacing the 19,483 B
version on PRs #8/#7.

- Test-only. Touches no application code.
- Resolves both Category 3 failures, and adds the `_prove_one_keyspace` probe plus the
  skip→fail strengthening of `real_redis_cache` (§5.3).
- **Review it on the merits anyway:** it reduces keyspace isolation by design (§5.3), and
  that tradeoff deserves a conscious acceptance rather than inheritance.
- **Refresh its stale `/15` docstring quote** at the same time (§5.5).
- **Owner:** the A9→A12 workstream, which authored it. A12 flags this file as being under
  shared ownership and deliberately did not edit it; whoever lands it must confirm with
  that owner that the worktree version is current.
- **Dependency:** Step 0.
- **Authorization:** commit + push.

### Step 2b — Point the gate at the database the application uses

Change `AERORA_TEST_REDIS_URL` from `redis://localhost:6379/15` to
`redis://localhost:6379/0` in `01-pr-check.yml` (§5.5). A12 has this change locally,
uncommitted. On PR #6 it must be applied **twice** — line 216 and line 735, the latter in
the `d5-redis-diagnostics` job.

- One token per occurrence. No application code.
- Without it the gate keeps certifying a database the application never touches.
- **Owner:** A9→A12 workstream (it authored the local change and the corrected comment).
- **Dependency:** Step 0, because the line number depends on the chosen branch.
- **Authorization:** commit + push.

### Step 3 — Re-run `01 PR Check` on the consolidated branch

Expected after Steps 1B + 2 + 2b: 23 D5′ failures → 0; the 39 pre-existing failures persist
and still fail the job. **So the job will stay red regardless.** The D5′ evidence has to be
read from the job log — the required test's PASSED line and the zero skip counts — not from
the job's conclusion. If a green check is wanted, the 39 pre-existing failures must be
addressed first, which is a larger, separate piece of work.

This run is also the first execution of the webhook fix and of the `_prove_one_keyspace`
probe. Treat it as the fix's first test, not as its confirmation.

- **Owner:** QUOTA-01 workstream.
- **Authorization:** CI trigger. Out of scope here.

### Step 4 — Remove the `d5-redis-diagnostics` job

Only if PR #6 is the branch chosen in Step 0. A12 lists it as temporary scaffolding to be
removed once the four D5′ failures are closed.

### Optional follow-up — make `get_redis_url` honour a configured database

Would restore genuine `/15` isolation and remove the whole class of keyspace mismatch,
including the gate mismatch in §5.5 at its source rather than by matching the test to the
application. Application change to `backend_app/backend/redis_manager.py`, owned by the
platform/cache owner. Not required for D5′.

---

## 8. QUOTA-01 status: OPEN

Run `38046625040` establishes a real fact worth banking:
`test_concurrent_reservations_are_atomic_against_real_redis` **passed against a real Redis
7.4.11 server**, with the opt-in gate verified reached (zero skips) and the server verified
live (`DB saved on disk`). That is the first real-Redis evidence this programme has
obtained, and the A3 verdict of UNVERIFIED is superseded on that narrow point.

It does not close QUOTA-01. The A4 analysis found that **the passing test never calls
`SubscriptionEngine.reserve_quota`** — it reproduces the reservation logic inline.
`reserve_quota` has exactly one definition, at
`backend_app/core/subscription_engine.py:959`, and it remains unexercised against a real
server. The test proves the *pattern* is atomic on real Redis. It does not prove the
*production path* uses that pattern.

Closing QUOTA-01 requires a test that drives `reserve_quota` itself. That is not in the D5′
manifest and is not what any of the three PRs delivers.

A12 reached the same conclusion from the code rather than from A4's analysis, and quotes the
test's own admission:

```python
async def _reserve_real():
    # Mirror reserve_quota's increment-first-revert against the real server.
    new_usage = await client.incrby(key, 1)
    if new_usage > limit:
        await client.decrby(key, 1)
        return False
    return True
```

It borrows `SubscriptionEngine._meter_key` for key naming and nothing else. So what the
green test proves is that **Redis's own INCR/DECR are atomic** — which was never in doubt.
The six logic tests that *do* call `reserve_quota` run against `MockRedisClient`.

A12 also specifies the test that would actually close this, without authoring it (the file
it belongs in is under shared ownership, and it could not be executed locally):
`test_production_reserve_quota_is_atomic_against_real_redis` — gate with the shared
`real_redis_required` / `require_real_redis()`; resolve the real cache and **fail**, not
skip, on `MockRedisClient`; prove one keyspace first via `_prove_one_keyspace`;
`asyncio.gather` 50 calls to `SubscriptionEngine.reserve_quota(..., limit=10)` and assert
exactly 10 are allowed; cross-check via the production reader `get_quota_usage` and the raw
key; assert refusals report the reverted usage rather than an inflated figure; assert
`limit=0` reserves nothing and leaves no key; unique `user_id`/`period` per run with scoped
teardown. That specification is the concrete remaining work for QUOTA-01 and is **not
authorized by this task either**.

---

## 9. Commands executed

All read-only. Exit codes as observed.

| # | Command | Exit | Result |
|---|---|---:|---|
| 1 | `git rev-parse HEAD` | 0 | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| 2 | `git rev-parse --abbrev-ref HEAD` | 0 | `main` |
| 3 | `git rev-list --left-right --count origin/main...HEAD` | 0 | `0  0` |
| 4 | `certutil -hashfile .git\index SHA256` | 0 | `6d965c8e…0458613`, unchanged entry vs exit |
| 5 | `git diff --cached --name-only` | 0 | 22 paths |
| 6 | `git diff --name-only` | 0 | 100 paths |
| 7 | `git status --porcelain -- backend_app/routers/exchange.py backend_app/core/subscription_dependencies.py` | 0 | `MM` and ` M` |
| 8 | `git ls-remote` | 0 | three D5′ branches confirmed |
| 9 | `python staging/a8_failures.py test_quota01_exchange_slot_is_atomic` | 0 | tally + per-file counts (strict parser: 60) |
| 10 | loose re-parse of `short test summary info` | 0 | **62 of 62** reconciled with pytest |
| 11 | `python staging/a8_guard_presence.py` | 0 | guard symbols: `main` absent; 3 branches present |
| 12 | `python staging/a8_guard_hits.py` | 0 | all branch hits in the test file; **0 definitions** |
| 13 | `python staging/a8_webhook_evidence.py` | 0 | 2 webhook reasons + keyspace lines 37/43/49/69 |
| 14 | `python staging/a8_exchange_entangle.py` | 0 | call site worktree-only; index 46,992 B, 0 hits |
| 15 | `python staging/a8_drift.py` | 0 | interim drift table — superseded by #19 |
| 16 | `git cat-file -s <rev>:<path>` ×14 | 0 | object sizes for §4 |
| 17 | `pytest tests/test_quota01_metered_reservation_is_hard.py` (opt-in unset) | 0 | 6 passed, 1 skipped |
| 18 | `python staging/a8_crosscheck_a12.py` | 0 | diffstats, gate URL per ref, untracked 3259, `d5-redis-diagnostics` only on PR #6 |
| 19 | `python staging/a8_crosscheck_bytes.py` | 0 | byte-exact manifest identity (§4) |
| 20 | `python staging/a8_index_delta.py` | 0 | staged content intact: 2104 entries, 22 paths, 0 unmerged, HEAD and reflog unchanged |
| 21 | `python staging/a8_index_cause.py` | 0 | `core.autocrlf=true`; index settled; staged-content hash stable (§1a) |
| 22 | `python staging/a8_validate_report.py` | 0 | section order ascending, 0 dangling refs, 12/12 figures present |

Command 15 was reported as timed out by the shell wrapper after emitting its full output;
the table was read from that output, and its exchange-slot row was later corrected by #19.
Command 17 ran with `AERORA_TEST_REDIS_URL` unset, so the real-Redis assertion correctly
skipped rather than silently passing. Command 18's C3 check was invalid on first run — it
compared locale-decoded `git show` output against a UTF-8 file read — and was redone as #19
against raw bytes.

### Artefacts written

`staging/` only. Nothing outside `staging/` was created, modified, or deleted.

```
staging/PHASE11B_A8_READ_ONLY_DIAGNOSTICS.md   (this report)
staging/a8_guard_presence.py                   (new)
staging/a8_guard_hits.py                       (new)
staging/a8_webhook_evidence.py                 (new)
staging/a8_exchange_entangle.py                (new)
staging/a8_crosscheck_a12.py                   (new)
staging/a8_crosscheck_bytes.py                 (new)
staging/a8_index_delta.py                      (new)
staging/a8_index_cause.py                      (new)
staging/a8_validate_report.py                  (new)
staging/a8_crosscheck.txt, a8_bytes.txt, a8_index_delta.txt,
staging/a8_index_cause.txt, a8_validate.txt    (new, captured output)
staging/a8_failures.py, a8_drift.py, a8_compare_branches.py,
staging/a8_raw_job.log, a8_pr.json, a8_runs.json, a8_other_run.json   (earlier in A8)
```

---

## 10. Cross-check against the parallel workstream's report

`staging/PHASE11B_A12_D5_MINIMAL_REPAIR_REPORT.md` is the A9→A12 workstream's own account
of the same material. It was located after the diagnostics above were complete. Its claims
were **verified independently rather than adopted**, because two of them contradicted
figures in this report.

| # | A12 claim | Verified how | Verdict |
|---|---|---|---|
| C1 | `exchange.py` staged = +268/−15 | `git diff --cached --numstat` | **confirmed** — corrects my "253-line" figure (§5.2) |
| C2 | `subscription_dependencies.py` = +253/−0 unstaged, nothing staged | `git diff --numstat`, `git diff --cached --numstat` | **confirmed** |
| C3 | the exchange-slot test's worktree/branch byte difference is CRLF only | `git cat-file blob`, raw bytes, CRLF-normalised SHA-256 | **confirmed** — corrects my drift table (§4) |
| C4 | it changed `AERORA_TEST_REDIS_URL` `/15`→`/0` locally, uncommitted | parsed the value per ref | **confirmed** — worktree `/0` at L248; all three branches `/15` |
| C5 | untracked = 3207 | `git ls-files --others --exclude-standard` | **superseded** — now 3259; other workstreams grew it |
| C6 | `d5-redis-diagnostics` is PR #6 scaffolding | searched the workflow blob per ref | **confirmed** — present only on PR #6, which also carries `/15` twice |

### What it changes in this report

- **Ownership, partly.** PR #6 belongs to the A9→A12 workstream. PR #8 is this one's. PR #7
  stays unattributed. The consolidation decision in §7 Step 0 now has two identified
  parties instead of none.
- **Two of my figures were wrong** and are corrected in place: the `exchange.py` staged
  diffstat (§5.2) and the exchange-slot drift row (§4).
- **One defect class was missing entirely** from these diagnostics: the gate PINGs database
  15 while the application uses database 0, so `state=run` certifies the wrong keyspace.
  Now §5.5, with a fix step at §7 Step 2b.
- **The entanglement finding is upgraded** from my static three-way comparison to A12's
  `git apply --cached --check` failure (exit 1) — a stronger proof that the guard hunks
  cannot be lifted onto `HEAD` (§5.2).
- **Option B gains independent support.** A12 recommends withdrawing the exchange-slot test
  and landing it with its implementation under the workstream that owns `exchange.py`.
  Two separate read-only analyses converged on it.
- **QUOTA-01's remaining work is now specified**, not just named (§8).

### Where the two reports agree without having coordinated

Both independently found: the DB-0/DB-15 keyspace split as the cause of the two webhook
failures; the missing guard implementation as the cause of the 21 exchange-slot failures;
the `exchange.py` cross-workstream entanglement as a hard blocker; and that the one green
real-Redis quota test does not exercise `reserve_quota`. A12 adds the measured redis-py
precedence table and the `git apply` proof; this report adds the complete 62-entry failure
inventory, the three-PR topology, and the byte-exact manifest identity.

### Where they differ

A12 states its local run as "26 passed, 9 skipped" across three manifest files and notes
that 22 slot-lock tests pass locally. This report does not treat that as evidence about the
PR, and neither does A12 — both record it as an artefact of the uncommitted working tree.
No disagreement on substance; flagged only because "22 passing" could be misread out of
context.

### One finding that affects both reports

Both A12 and every prior phase of this programme quote the **`.git/index` SHA-256** as the
proof that shared state was not disturbed. §1a shows that invariant is unsound on this
repository: `core.autocrlf = true`, so git's own read commands rewrite the index's stat
cache and move the checksum without touching a staged byte. It drifted during this phase for
exactly that reason.

This does not cast doubt on A12's isolation claim — its measurements were consistent within
its own window, and nothing here suggests it staged anything. It does mean the metric should
be replaced going forward with the **staged-content hash** from `git ls-files -s`, plus
`HEAD` and the reflog, which are insensitive to stat-cache churn. Had this been noticed
earlier, the drift observed here would have been a non-event rather than a stop condition.

---

## 11. Exit state

| Check | Result |
|---|---|
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5`, unchanged |
| Branch | `main`, no divergence from `origin/main` |
| `.git/index` SHA-256 | `3dbf1cb0…` — **moved from `6d965c8e…`**; stat-cache refresh only, staged content proven intact (§1a) |
| `.git/index` size | 232,081 bytes — identical to entry |
| **Staged content** (`git ls-files -s`) | `98ae5bdf…`, 2104 entries — the valid invariant |
| Staged / unstaged / untracked | 22 / 100 / 3259 — staged set and blob identities unchanged |
| Unmerged entries | 0 |
| Reflog | newest entry 2026-10-05 — no commit created |
| Local branches | 15, unchanged — none created, moved, or deleted |
| Stash | 2 entries, both pre-existing — none pushed or popped |
| Working-tree files modified | none outside `staging/` |
| Branches created or moved | none |
| Remote writes | none |
| CI runs triggered | none |
| PRs opened, closed, or merged | none |
| Production or venue access | none |
| Live trading | OFF / NOT CERTIFIED |
| QUOTA-01 | OPEN |

**Blocked pending human decision:**

1. Which of PR #6 / #7 / #8 survives. PR #6's owner is identified (A9→A12), PR #8's is this
   workstream, PR #7's is unknown.
2. Whether the exchange-slot guard ships with its test (Option A) or the test is withdrawn
   from the D5′ manifest (Option B). **Both independent analyses recommend Option B.**
3. Who commits the three worktree-only corrections — the webhook fix, the `/15`→`/0` gate
   value, and (under Option A only) the guard — given that two of the three sit in files
   another workstream owns or has partly staged.

Note for whoever acts next: all three corrections live in the uncommitted working tree and
are therefore one `git checkout` away from being lost. Nothing in this phase touched them,
but they are not durable where they are.
