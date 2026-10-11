# Phase 11B-A23R — Redis Pool Lifecycle Repair (G1/G2/G3): Authorization Gate and Readiness

**STATUS: STOPPED AT THE PHASE 1 AUTHORIZATION GATE. NO PRODUCTION CODE WAS CHANGED. NO TEST FILE WAS CREATED.**

**Live trading remains OFF / NOT CERTIFIED.** Nothing in this report changes that, and nothing in
this report was committed or pushed.

Approval **A** (edit `backend_app/backend/redis_manager.py`, changes 1–4 of §6) and approval **B**
(create `tests/test_a23_redis_pool_lifecycle.py`) are both **NOT GRANTED**. The brief conditions
implementation on both being explicitly present in an authoritative record. They are not. Per the
brief's instruction — "If either A or B is not explicitly granted, do not edit production code or
create the test file" — this report is the deliverable and the work stops here.

---

## 1. Summary of what this pass establishes

| # | Finding | Kind |
|---|---|---|
| 1 | A and B are both **NOT GRANTED**. The target file is recorded as **UNOWNED**, so there is not even an identified grantor | authorization |
| 2 | A23 §7.2's seven script `close()` call sites are **dead legacy code**. A23 approval request **#4 is void** | correction to prior work |
| 3 | `initialize()` has a **third caller** with no cooldown and no lock, in another workstream's file. Target-file-only repair **cannot** serialize it | scope limitation |
| 4 | The open `aclose()` floor question (sibling report's item 5, deferred to "A23R item 3") is **resolved**: no floor is raised at the current exact pin | resolution |
| 5 | G1, G2, G3 remain **reproduced, not fixed**. A23-01 remains **identified, not fixed**. R6 remains **OPEN** | status |
| 6 | All four lifecycle-relevant files are **byte-identical** entry→exit. 261 working-tree entries and 22 staged entries preserved | preservation |

---

## 2. Authorization determination

Per the brief, A and B are judged independently, each with its source.

| Approval | Scope | State | Source |
|---|---|---|---|
| **A** | Implement changes 1–4 of §6 in `backend_app/backend/redis_manager.py` | **NOT GRANTED** | No grant exists in any authoritative record; see §2.1. Owner unidentified; see §2.2 |
| **B** | Create `tests/test_a23_redis_pool_lifecycle.py` | **NOT GRANTED** | Same. Independently nominated and independently refused by a concurrent session; see §2.3 |

Approval for A would not have implied approval for B, and neither was found.

### 2.1 Search method and result

Every `GRANTED` token in `staging/PHASE11B_*.md` is one of exactly two things: the string
`NOT GRANTED`, or a *request* for a grant. There are no affirmative grants for this change.

A prior session in this programme (W1D-07) ran the wider repo-scan: 181 lines matched an
affirmative-looking `GRANTED`, and **all 181 were false positives** — open-source licence texts,
SQL `GRANT` prose, and application-level billing/OTP "grant" language. That scan is not repeated
here; its conclusion is adopted and attributed.

Explicitly **not** treated as approval, per the brief: the existence of this prompt; the existence
of a plan; prior sessions' recommendations; the target file being clean; the standing strict-xfail;
or silence.

### 2.2 There is no identified owner

`staging/PHASE11B_A10_D5_APPROVAL_READINESS.md` lines **244** and **367** record
`backend_app/backend/redis_manager.py` as **"UNOWNED, unfixed."**

This is the harder half of the blocker. A missing approval can be obtained from a known owner. Here
the ownership record itself is empty, so there is no party to ask. Resolving A and B requires first
assigning an owner to this file.

### 2.3 Independent corroboration from two concurrent sessions

Two staging artifacts appeared **after** this pass's entry baseline (22:08:04) and were read before
finalizing:

| Artifact | Written | Size | Bearing |
|---|---|---|---|
| `PHASE11B_A23_REDIS_LIFECYCLE_IMPLEMENTATION_REPORT.md` | 22:12:19 | 17 789 B | §6 rows 1–2 are **exactly** approvals A and B. Both **NOT GRANTED** |
| `PHASE11B_A23B_REDIS_RECOVERY_COVERAGE.md` | 22:16:06 | 23 519 B | §2 row 1 "Pool lifecycle repair — G1/G2/G3 (Step 0)" **NOT GRANTED**; §8 blocker 1 the same |

The name "IMPLEMENTATION_REPORT" is misleading on its face; its own line 3 reads
"STOPPED AT THE PHASE 1 AUTHORIZATION GATE. NO IMPLEMENTATION OCCURRED." No concurrent session
implemented this repair.

That report records the target file's digest as `d7b0e811b615`. Independently confirmed here:
`git hash-object backend_app/backend/redis_manager.py` → `d7b0e811b615ee13ba089b5cbe9d790161176de2`.
Two sessions, two different hash functions, same unmodified content.

**Coordination hazard, now closed:** that session nominated the identical test path
`tests/test_a23_redis_pool_lifecycle.py`. Neither session created it. Whoever is eventually
authorized must claim the path explicitly, or the second writer will clobber the first.

### 2.4 Two further approvals, newly identified elsewhere — A and B are not sufficient

The sibling report identifies two consents that no earlier report listed, and that this pass
confirms are necessary consequences of the §6 changes:

| # | Consent | Owner | State |
|---|---|---|---|
| 3 | `close()`'s changed contract becomes visible at `core/cache/redis_manager.py:619` | owner of `core/cache/redis_manager.py` (currently ` M`, another workstream) | **NOT GRANTED** |
| 4 | Rebuild semantics for the five `Depends(get_redis_manager)` handlers in `routers/exchange.py` | owner of the AUTH-01 / CONN-01 / TENANT-01 work | **NOT GRANTED** |

**Consequence:** granting A and B alone would still not make the repair safely landable. Change 3
of §6 alters `close()`'s postcondition, and `core/cache/redis_manager.py:616-619` is the production
shutdown path that calls it. That file belongs to another workstream and is currently modified.

---

## 3. Correction to prior work: A23 §7.2 is wrong, and its approval request #4 is void

A23 §7.2 attributed seven `close()` call sites to the backend manager and raised approval request #4
to cover them. **That attribution is incorrect.** The sites are:

`scripts/test_data_stream_worker.py:90`, `scripts/test_event_bus.py:98`,
`scripts/test_execution_worker.py:106`, `scripts/test_order_event_flow.py:57,86,114`,
`scripts/test_strategy_worker.py:131`.

All seven resolve through `from backend.services.cache import redis_manager`. Three independent
facts make that import unsatisfiable, and the receiver neither manager:

1. **The package does not exist.** There is no `backend/` directory.
   `python -c "from backend.services.cache import redis_manager"` →
   `ModuleNotFoundError: No module named 'backend'`.
2. The backend `RedisManager` has **no `connect()`** method, which these scripts call.
3. `SharedRedisManager` (the `core/cache` class) has **no `close()`** method, which these scripts
   also call.

No object in the repository satisfies both calls. These scripts are **dead legacy code** and cannot
execute at all. A23 reached its conclusion through a permissive receiver-*name* heuristic without
resolving the import.

**Therefore A23 approval request #4 is withdrawn as void.** It guarded a compatibility surface that
does not exist. This *removes* a blocker rather than adding one.

Contributing cause worth recording: `pytest.ini` sets
`pythonpath = . aerora_quant_backend_updated_final1 backend` — naming the non-existent `backend`
directory. That stale entry is what makes the dead imports look plausible on a skim.

**Self-correction.** This pass's own first probe over-flagged
`scripts/test_order_event_flow.py:62,91` as "use after close." Those lines are `async def`
**definitions**, not calls. Definition order is not execution order. The probe was corrected before
any conclusion was drawn from it. Probe and output retained at
`staging/probe_p11b_a23r_script_sites.py` and `staging/p11b_a23r_script_sites.txt`.

---

## 4. Lifecycle invariants: current state, and state after the proposed repair

"After" is **projected, not measured** — the changes were not applied.

| # | Invariant | Current | After §6 (projected) |
|---|---|---|---|
| 1 | A failed `initialize()` leaks no connection pools | **VIOLATED** (G1) | HELD — change 1 |
| 2 | A client is built and pinged before publication, and closed exactly once | **HALF-HELD** — build-and-ping-before-publish at `:163-170` is already correct; "closed exactly once" **VIOLATED** (G2) | HELD — change 2 |
| 3 | After `close()`, health reads false and no client reference is retained | **VIOLATED** (G3, both halves) | HELD — change 3 |
| 4 | Cleanup errors never mask the original failure | **N/A today** — no cleanup path exists to fail | HELD — change 1 swallows secondary errors, preserves the original |
| 5 | `close()` is idempotent | **UNVERIFIED** — repeated `close()` is untested | HELD — change 3 |
| 6 | All `initialize()` entries are serialized | **VIOLATED** | **STILL VIOLATED** — see §5. Out of reach of a target-file-only repair |

### The three defects, as measured

Proven by execution in the A22V pass (`staging/probe_p11b_a22v_lifecycle_gaps.py` →
`p11b_a22v_lifecycle_gaps.txt`), not by reading:

- **G1 — partial-init leak.** `initialize()` builds three clients into *locals* (`:140,:148,:156`),
  pings all three (`:164-166`), and publishes to `self.*` only on success (`:167-170`). The `except`
  block (`:177-182`) sets `_connected=False` and returns False but **never closes the locals**.
  Measured: 3 clients created, **0 closed**. With `_RECONNECT_COOLDOWN` defaulting to 3.0 s
  (`:30`), a persistently unreachable Redis leaks three pools every 3 seconds.
- **G2 — rebuild leak.** A second `initialize()` replaces `self._cache` and friends without closing
  the previous generation. Measured: 6 created, **0 of the first 3 closed**.
- **G3 — `close()` leaves the object live.** `close()` (`:215-224`) awaits `.close()` on the three
  clients but neither resets `_connected` nor unbinds the attributes. Measured: `_connected` still
  `True`, `_cache` still bound; `.close()` ×3, `.aclose()` ×0.

### A23-01 — reconnect-after-shutdown dead path

Composite of G3's two halves, and the reason a fix must **unbind**, not merely flip the flag:

`close()` → clients closed, flag and bindings retained → later `connect()` → `_ensure_manager()` →
`get_redis_manager():548` fast path returns the stale manager on `_connected` → `core/cache:624`
guard `if self._redis_manager and not self._redis_manager._cache:` evaluates **False** because
`_cache` is still bound → `initialize()` is **skipped** → the application runs on closed clients.

Flipping `_connected` alone would fix the `:548` fast path and leave the `:624` guard broken.
Unbinding alone would fix `:624` and leave `:548` broken. **Both halves are load-bearing, in
different places.**

### R6 contract, as read (`backend_app/backend/redis_manager.py`, 533 lines)

`_connected` is written at exactly three sites — `:122` (False, `__init__`), `:171` (True), `:180`
(False) — the latter two both inside `initialize()`. It gates `:192` (`try_reconnect` first line),
`:202`, `:206`, `:548` (`get_redis_manager` fast path), and `:561`.
`RedisManagerCompatProxy.__getattr__` (`:581`) re-imports `_redis_manager` and falls through to
`_cache` then `_queue`, never consulting `_connected`.

---

## 5. Limitation that must not be overstated — concurrent init/shutdown is NOT serialized

**This repair does not serialize all concurrent initialization and shutdown paths, and no claim to
the contrary should be made on its behalf.**

`initialize()` has **three** callers. A22 and A22V both listed two; the third was found in the A23
pass:

| # | Caller | Debounced? | Locked? |
|---|---|---|---|
| 1 | `backend/redis_manager.py:213` (`try_reconnect`) | yes | yes |
| 2 | `backend/redis_manager.py:571` (`get_redis_manager`) | yes | yes |
| 3 | **`backend_app/core/cache/redis_manager.py:625`** (inside `connect()`), reached from `main.py:370` | **no** | **no** |

Caller 3 is in **another workstream's file**, which the brief designates read-only and which is
currently ` M`. A repair confined to the target file therefore cannot close this hole. Changes 1–3
make each individual lifecycle transition correct **in isolation**; they do not make two
transitions racing from different callers safe. A concurrent `connect()` (caller 3) and `close()`
can still interleave.

Related and also unresolved: `main.py:74` imports `redis_manager` from `backend_app.core.cache`
(type `SharedRedisManager`), **not** the backend manager — so the production shutdown path is
`main.py:607` → `core/cache:616-619 disconnect()` → `await self._redis_manager.close()`. The
backend manager's `close()` is reached only *through* the file this pass may not edit. That is the
same coupling that makes consent #3 in §2.4 mandatory.

Two cooldowns remain unreconciled (A22 F5): the target file's `_RECONNECT_COOLDOWN` and the
core-cache layer's own retry behaviour. Out of scope here and **NOT GRANTED** elsewhere.

---

## 6. The proposed repair — specified, NOT applied

Recorded so that a future authorized pass does not have to re-derive it.

1. **Track the locals in `initialize()`** and close all of them in the `except` block. Swallow
   secondary close errors; preserve and return the original failure.
2. **Publish-then-close.** Capture the outgoing client set, publish the new one, then close the
   outgoing set. Never close a newly published client.
3. **`close()` sets `_connected = False` and unbinds all three clients.** Idempotent on repeat
   calls. Both halves, per A23-01.
4. **Use `aclose()`** in place of the deprecated `close()`. No compatibility shim.

**Explicitly out of scope:** loop identity (option B / B-2′), runtime health invalidation in the
core-cache proxy (option A, ~25 handlers at `core/cache/redis_manager.py:753-1016`), retry and
cooldown policy, and the core-cache proxy itself.

### 6.1 Resolution of the open `aclose()` question (sibling item 5 → "A23R item 3")

**No floor is raised. The question is moot at the current pin.**

- The pin is **exact** — `redis==5.0.8` in `requirements.txt:19`, `requirements-base.txt:69`, and
  `requirements-dev.txt:20`. Installed version confirmed `5.0.8`.
- At 5.0.8, `Redis.aclose` exists. `ConnectionPool.aclose` and `ConnectionPool.disconnect` both
  exist.
- `Redis.close` also still exists and is **functionally correct** — it is a thin deprecated
  forwarder: `@deprecated_function(version="5.0.1", reason="Use aclose() instead", name="close")`
  wrapping `async def close(self, close_connection_pool=None): await self.aclose(...)`.

So change 4 is a **warning-noise fix, not a correctness fix**: today's `close()` works but emits
three `DeprecationWarning`s per shutdown. Standardising on `aclose()` would only raise an effective
floor if someone relaxed the pin below 5.0.1; with an exact pin at 5.0.8 there is nothing to raise.
A release owner's consent is therefore **not** required for change 4 at the current pin.

---

## 7. Test evidence and remaining gaps

### 7.1 What was verified in this pass

R6 characterization suite, re-verified independently and **server-free** — `REDIS_URL` UNSET,
`AERORA_TEST_REDIS_URL` UNSET, port 6379 closed:

```
python -m pytest tests/test_redis_debounce.py -q --no-header -p no:cacheprovider
→ 9 passed, 1 xfailed   (2.13 s / 4.20 s / 7.55 s across three runs)
```

The xfail is `test_r6_d_desired_a_runtime_failure_should_be_recoverable`, marked
`xfail(strict=True)`. It is a **standing, correct** xfail: it encodes the behaviour the system does
not yet have. It was not weakened, unmarked, or converted, and no assertion anywhere was relaxed to
obtain a green run.

This file belongs to the **A22** workstream (` M`, +225/−0, mtime 21:32:35) and was **not modified**
by this pass, per the brief.

### 7.2 What was NOT written, and would have been under approval B

Eight tests in `tests/test_a23_redis_pool_lifecycle.py`: partial-init closes all three locals (G1);
the original failure survives a secondary close error; rebuild closes the previous generation
exactly once (G2); a newly published client is never closed; `close()` resets health (G3a);
`close()` unbinds all three (G3b); `close()` is idempotent; and the A23-01 end-to-end path —
`close()` then `connect()` must rebuild rather than silently reuse closed clients.

### 7.3 Remaining gaps — stated as gaps, not as risks retired

| Gap | State |
|---|---|
| **Real-Redis recovery** | **UNEXECUTED.** No server available; installing one was not authorized. No real-Redis recovery claim is made anywhere in this report |
| Concurrent init/shutdown under load | **UNTESTABLE** within target-file-only scope — caller 3 is unlocked and out of scope (§5) |
| Loop identity across event loops | out of scope; tracked by the concurrent A23 loop-identity workstream |
| Runtime health invalidation (option A) | **NOT GRANTED**; recorded elsewhere as the blocker for closing R6 |
| `close()` idempotency today | **UNVERIFIED** — needs approval B |

---

## 8. Preservation results

No reset, stash, stage, revert, or overwrite was performed. Verification used non-mutating
fingerprints and an isolated object store; **no verification objects were written to the shared Git
object database.**

### 8.1 Repository state — entry (22:08:05) vs exit

| Measure | Entry | Exit |
|---|---|---|
| Branch | `main` | `main` |
| HEAD / `origin/main` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | **same** |
| Ahead / behind | `0 0` | **same** |
| `git status --porcelain` | 261 (`??` 139, ` M` 100, `A ` 12, `M ` 8, `MM` 1, `AM` 1) | **261** |
| Staged entries | 22 | **22** |
| Canonical staged digest | `2e994018e375f318f4e6dc9a67ab248e4622de8c426146ca756d5f47d2767152` | **same** |
| Index tree (isolated) | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` | **same** |
| Loose objects in `.git/objects` | 198 | **198** |
| Objects written to isolated store | 0 | **0** |
| Worktrees / unmerged paths | 1 / 0 | **same** |

Staged-digest method: `git diff --cached --raw` → CRLF→LF → `TrimEnd` → drop blank lines → LF-join →
SHA-256. Cross-validated against a concurrent session's independently published value.

**Why the porcelain count is 261 and not 262 after this report was written.** `staging/` contains
**zero tracked files** (`git ls-files staging` → 0) and 3 400 untracked ones
(`--untracked-files=all`). Default `git status --porcelain` therefore collapses the entire directory
into a **single** `?? staging/` entry, which was already present at entry. This new report is
subsumed under that pre-existing entry rather than adding one. Verified directly: with a pathspec,
`git status --porcelain -- staging/PHASE11B_A23R_REDIS_LIFECYCLE_READINESS.md` reports
`?? staging/PHASE11B_A23R_REDIS_LIFECYCLE_READINESS.md`; the file is 23 433 bytes and untracked.
The unchanged 261 reflects this collapsing, **not** a missing write and **not** a deletion.

### 8.2 File fingerprints — all byte-identical entry→exit

| File | Bytes | SHA-256 (16) | Git status | Note |
|---|---|---|---|---|
| `backend_app/backend/redis_manager.py` | 21 497 | `1FD5CA799E792CCB` | **clean** | target; **NOT edited**. Blob `d7b0e811b615…` |
| `tests/test_redis_debounce.py` | 16 634 | `251E7CF076816CB4` | ` M` | A22 workstream; untouched |
| `backend_app/core/cache/redis_manager.py` | 59 890 | `66D273FC2E582D16` | ` M` | other workstream; read-only |
| `tests/conftest.py` | 22 317 | `9D7BC7FE48C0FFB8` | ` M` | other workstream; read-only |

### 8.3 Concurrent work left intact

`staging/PHASE11B_A23_REDIS_POOL_LIFECYCLE_READINESS.md` (22:02:03),
`PHASE11B_A23_REDIS_LOOP_IDENTITY_REPORT.md` (22:08:04),
`PHASE11B_A23_REDIS_LIFECYCLE_IMPLEMENTATION_REPORT.md` (22:12:19),
`PHASE11B_A23B_REDIS_RECOVERY_COVERAGE.md` (22:16:06) — all read, none modified.

Both write destinations were confirmed absent immediately before writing:
`staging/PHASE11B_A23R_REDIS_LIFECYCLE_READINESS.md` (this file, the only artifact created) and
`tests/test_a23_redis_pool_lifecycle.py` (**still absent** — approval B not granted).

### 8.4 Isolated verification recipe

Copy `.git/index` to a temp path; set `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`, and
`GIT_ALTERNATE_OBJECT_DIRECTORIES=(Resolve-Path .git\objects).Path`; run `git write-tree`; unset all
three; delete the temp directory. The alternates entry lets the tree resolve against existing
objects without writing any, which is why the isolated store received **0** objects and the shared
store stayed at 198.

---

## 9. Explicit statuses

| Item | Status |
|---|---|
| **G1** — partial-init pool leak | **REPRODUCED, NOT FIXED.** Measured by execution: 3 created, 0 closed |
| **G2** — rebuild pool leak | **REPRODUCED, NOT FIXED.** Measured: 6 created, 0 of first 3 closed |
| **G3** — `close()` leaves health true and clients bound | **REPRODUCED, NOT FIXED.** Both halves load-bearing |
| **A23-01** — reconnect-after-shutdown dead path | **IDENTIFIED, NOT FIXED.** Composite of both G3 halves |
| **R6** — runtime failure should be recoverable | **OPEN.** `r6_d` remains a correct strict xfail. Closing R6 needs option A (core-cache health invalidation), which is **NOT GRANTED** and out of this scope |
| **A23 approval #4** (seven script `close()` sites) | **VOID.** Dead code; `ModuleNotFoundError: No module named 'backend'` |
| **A23R item 3** (`aclose()` / redis-py floor) | **RESOLVED.** No floor raised at the exact pin `redis==5.0.8` |
| Approval **A** | **NOT GRANTED** |
| Approval **B** | **NOT GRANTED** |
| Consents **3** and **4** (§2.4) | **NOT GRANTED** — and required in addition to A and B |
| **Live trading** | **OFF / NOT CERTIFIED** |

---

## 10. The exact unblock

Implementation resumes when, and only when, an authoritative record contains all of the following.
Nothing less is sufficient, and none of it may be inferred.

1. **An owner assigned** to `backend_app/backend/redis_manager.py`, which
   `PHASE11B_A10_D5_APPROVAL_READINESS.md:244,367` currently records as **UNOWNED**. This is
   prerequisite to 2 and 3 — today there is no party with standing to grant them.
2. **Approval A**, naming changes 1–4 of §6 in that file, from that owner.
3. **Approval B**, naming `tests/test_a23_redis_pool_lifecycle.py`, from that owner — and claiming
   the path, since a concurrent session nominated the same filename (§2.3).
4. **Consent 3** from the owner of `backend_app/core/cache/redis_manager.py`, because change 3
   alters the `close()` contract consumed at `:619` on the production shutdown path.
5. **Consent 4** from the owner of the AUTH-01 / CONN-01 / TENANT-01 work, for the rebuild semantics
   affecting the five `Depends(get_redis_manager)` handlers in `routers/exchange.py`.

Two further items are **not** blockers and should not be treated as such: A23 approval #4 is void
(§3), and the `aclose()` floor question is resolved (§6.1).

Separately, and independent of any approval: `pytest.ini`'s `pythonpath` names a non-existent
`backend` directory (§3). Worth fixing on its own merits, by whoever owns that file.

---

## 11. Scope discipline — what was deliberately not done

- No edit to `backend_app/backend/redis_manager.py` (approval A absent).
- No creation of `tests/test_a23_redis_pool_lifecycle.py` (approval B absent).
- No edit to `backend_app/core/cache/redis_manager.py` (read-only per the brief; owner has not
  authorized a change).
- No edit to `tests/test_redis_debounce.py` (A22 workstream).
- No assertion weakened and no xfail removed to obtain a green run.
- No claim of real-Redis recovery — that lane is unexecuted.
- No claim that concurrent init/shutdown paths are serialized (§5).
- No commit, no push, no staging change, no reset/stash/revert.
- Approval was **not** inferred from this prompt, from sibling reports' recommendations, from the
  target file being clean, from the standing xfail, or from silence.

---

*Artifacts from this pass: this report; `staging/probe_p11b_a23r_script_sites.py`;
`staging/p11b_a23r_script_sites.txt`. No other file in the repository was created or modified.*
