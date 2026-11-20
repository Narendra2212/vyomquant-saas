# Phase 11B-A16 — Independent Verification Addendum (Kiro session)

**Why this filename.** The brief's destination,
`staging/PHASE11B_A16_D5_APPROVAL_HANDOFF.md`, was **already occupied** when I checked it in
Phase 0 (19,079 B, 341 lines, mtime 2026-10-10 19:39:03) by a concurrent session's own A16
report. The brief requires "Write a new report only after checking the path is free" — it was
not. Having already destroyed one of that session's reports in A14, I did **not** write there.
This addendum uses a session-tagged path, which is precisely the collision control their report
proposes.

**This is not a duplicate of their A16.** Their report is comprehensive and I endorse it. This
document contains only: independent verification of its load-bearing claims, one scope finding
that changes which patch should be used, and three corrections to **my own** A15.

Read-only phase. No commit, push, PR edit, PR closure, CI trigger, or source modification.

---

## 1. Fresh baseline and preservation (unchanged)

| Item | Value | Status |
|---|---|---|
| branch / HEAD | `main` / `074b772a` | SAME |
| `refs/heads/main` / `origin/main` | `074b772a` | SAME |
| D5′ branch local / remote | `d07014d3` | SAME, unmoved |
| **staged-content fingerprint** | `CF474F1B83FD37173F0AD3F2B5417D07CE4B26DC66A5CF4D6697AE9ACB568767` | **SAME** before and after |
| staged entries | 22 | SAME |
| index tree | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` | recorded |
| PR #6 / #7 / #8 | `d07014d3` / `49e70da2` / `a624ce89`, all OPEN, UNSTABLE, 0 reviews | SAME |
| webhook / metered / slot blobs | `3ffbccde` / `7baff0b6` / `2d7ccbbb` | SAME |

> **Fingerprint methodology note.** Their report records an index content digest of
> `7379218b…`; mine is `CF474F1B…`. These are **not** in conflict — they are different
> serialisations (mine is SHA-256 over `git ls-files -s`). Both are stable across every phase in
> which they were taken, so both support the same conclusion: the shared index has not drifted.
> Worth reconciling on one definition to avoid a future false alarm.

## 2. Independent verification of their A16's load-bearing claims

| Their claim | My independent check | Verdict |
|---|---|---|
| Patch applies to PR #6, pre-images match | workflow pre-image `57e2cee8` == PR #6; webhook `105b6567` == PR #6; metered `f48103ac` == PR #6 | **CONFIRMED** |
| `subscription_dependencies.py` applies onto HEAD **cleanly** | `git apply --cached --recount --check` in a private index seeded from HEAD → **rc=0** | **CONFIRMED** |
| `exchange.py` does **not** apply to HEAD | fails at line 366, rc=1 (re-measured) | **CONFIRMED** |
| Worktree workflow blob is `91d55a37` | `git hash-object` → `91d55a379115c88edd2fd6be01871b82a75ef498` | **CONFIRMED** |
| `.gitignore` carries `+15 / −0` from another workstream | `git diff --numstat -- .gitignore` → `15  0` | **CONFIRMED** — and **my A15 missed this**; their catch is correct |
| #7 tree == #8 tree; #6 distinct | `271f13c2…` == `271f13c2…`; `d28b6d1a…` ≠ | **CONFIRMED** |
| No approval on any PR | `reviewDecision` empty, 0 reviews, 0 labels, 0 assignees ×3 | **CONFIRMED** |
| 17 surviving `p11b14_*` artifacts | I count **16** | **substance confirmed, count differs by one** — immaterial; their point stands |

## 3. Scope finding — use THEIR patch, not my candidate

This is the one place my prior work should be set aside.

| | Their `staging/p11b15_proposed_fixed.patch` | My A14/A15 candidate blob `a2f39ebb` |
|---|---|---|
| Workflow hunks | 2, at `@@ -213,7 +213,7 @@` and `@@ -732,7 +732,7 @@` | 3 regions |
| Workflow lines | **+2 / −2** — only the two `AERORA_TEST_REDIS_URL` values | **+39 / −5** — the two values **plus** a 37-line comment rewrite |
| Matches this brief's Phase 2 scope ("the workflow's two Redis DB path corrections, `/15` to `/0`") | **YES, exactly** | **NO — exceeds it** |

**I withdraw `a2f39ebb` as the workflow candidate for this manifest.** It bundles a comment
rewrite that no approval covers, and this brief's scope statement names only the two DB
corrections. Their 2/2 patch is the correct minimal change.

The comment rewrite is still *worth doing* — the existing block claims `/15` "pins a dedicated
database index … belt-and-braces rather than load-bearing", which is factually false and was the
proximate cause of two CI failures. But it is a **separate, separately-approved** documentation
change, not part of the minimal D5′ consolidation. Recorded here so it is not lost.

## 4. Corrections to my own A15

1. **Webhook ownership (B).** My A15 said the webhook owner "targets #7". That was an inference
   from two incidental PR-state references in their A12 report, and the author has now stated
   directly that neither expressed a preference and that they **consent to the change landing on
   PR #6**. My inference was wrong; **B is resolved** and is no longer a blocker. I had flagged it
   as "a stated preference, not an approval", which was the right hedge, but the premise was still
   incorrect.
2. **Recoverability overstated as total.** My A15 said "the only surviving trace is three lines
   quoted in `p11b15_approval_search.txt`". That is true of the *prose*, but understates the
   record: **16 `p11b14_*` artifacts survive** carrying the substantive evidence (the 39-failure
   baseline with node IDs, the 23/39 introduced-vs-pre-existing split, the content-digest
   invariant, and a 2.6 MB CI log), plus 24 `p11b15_*` artifacts. Only the narrative was lost.
3. **`.gitignore` drift not flagged.** It carries `+15 / −0` from a concurrent workstream. My A15
   baseline did not surface it. Untouched by me.

On the overwrite itself: their report states both their A14 **and** A15 were overwritten by my
session, and that their own A12–A15 writes were equally unchecked. I accept the A14 overwrite
without reservation — I did not check before writing, and that was my error. For A15 I did check
(`Test-Path` reported the path free) and then wrote; if their A15 was created in the interval
between my check and my write, the loss is a race rather than a skipped check. I state that as
sequencing, not as a defence of the outcome: their report is gone either way, and the shared
pattern was unsafe on both sides.

## 5. F1 — a materially better disposition than I gave

Their analysis is sharper than my A13/A15 framing, and I verified its mechanism:

- `subscription_dependencies.py` (` M`, +253 / −0) **applies onto HEAD cleanly (rc=0)**. I had
  called it "separable but useless without the call site"; that was too pessimistic.
- Shipping it **alone** supplies `_slot_lock_key`, `_acquire_slot_lock`, `_release_slot_lock` and
  `exchange_connection_slot_guard`, which resolves the **20** `AttributeError` failures (14 on the
  guard + 6 on `_slot_lock_key`, per the A11 JUnit grouping).
- The 21st, `test_store_keys_uses_the_guard_around_its_vault_write`, would **still fail — and
  correctly so**, because `store_keys` genuinely does not yet wrap its vault write in the guard,
  so QUOTA-01's read-then-write race is still live on `main`.

So F1 can be **partially unblocked without touching the entangled `exchange.py`**, converting 20
red tests to green and leaving exactly one honest red flag pointing at the real unfixed race.
That still needs decision **C** from the owner of `subscription_dependencies.py`, but it is a far
smaller and safer ask than "land the whole guard with its integration".

## 6. Decision table A–E — current status

| | Decision | Status | Evidence |
|---|---|---|---|
| **A** | Canonical PR | **OPEN — needs you.** Recommend **#6** (sole carrier of the diagnostic job; #7≡#8 so one must close regardless) | three independent sessions converged on #6 |
| **B** | Webhook owner consent to land on #6 | **RESOLVED** | author's explicit consent in their A16 §3.B |
| **C** | F1 disposition | **OPEN — needs the `subscription_dependencies.py` owner.** Now has a smaller option: ship that file alone (rc=0 clean) for 20/21 | §5 |
| **D** | Commit / push / CI | **OPEN — no authorization exists** | 0 reviews, 0 labels, 0 assignees; 127 staging docs scanned, only requests found |
| **E** | Close #7/#8 | **OPEN — and should stay deferred** until the canonical PR carries the content and CI has reported | their §3.E |

## 7. Safe validation performed

| Command | Exit | Result |
|---|---|---|
| `pytest <3 D5′ files> --collect-only -q` | 0 | **37 collected** (9 metered + 6 webhook + 22 slot) |
| `git apply --cached --recount --check` (private index ← HEAD) on `subscription_dependencies.py` | **0** | applies clean |
| same on `exchange.py` | 1 | `patch failed: …:366` |
| `git hash-object` ×4, `rev-parse ^{tree}` ×3, `gh pr view` ×3 | 0 | §1, §2 |

No PostgreSQL lane. No CI triggered. No local test execution claimed as real-Redis evidence —
every local real-Redis case is an opt-out skip and is **not** a pass.

### Existing CI failures, and what the candidate does to them

| Node IDs | Count | Candidate's effect |
|---|---|---|
| `test_quota01_exchange_slot_is_atomic.py` — 21 nodes | 21 | **NOT fixed** — F1 excluded. 20 become fixable if C approves shipping `subscription_dependencies.py`; the 21st stays red correctly |
| `…webhook…::test_the_lock_is_recorded_in_redis_under_the_documented_key` | 1 | **expected fixed** (keyspace probe + derived URL) |
| `…webhook…::test_an_in_flight_delivery_blocks_a_concurrent_one` | 1 | **expected fixed** |
| `…webhook…::test_an_invalid_signature_is_rejected_and_leaves_no_lock_behind` | 1 | currently passes **vacuously**; becomes a real assertion |
| 2 new production-path `reserve_quota` tests | +2 | newly executed; outcome **unknown** — this is the point |
| Pre-existing baseline failures (run `37346853502`, `6c5660a1`, 39 failed) | 39 | untouched; must match by **node ID**, not count |

**So the canonical PR will still be RED after this patch.** That is expected and must not be
reported as success — the diagnostic job's `continue-on-error` makes its job status meaningless;
only the JUnit node IDs count.

## 8. Final status (unchanged)

- **QUOTA-01: OPEN**
- **TENANT-01: OPEN**
- **D5′ real-Redis acceptance: NOT PASSED**
- **F1: BLOCKED** (partially unblockable via `subscription_dependencies.py` alone — needs C)
- **F4: UNRESOLVED / UNOWNED** — `backend_app/backend/redis_manager.py` clean; production blast
  radius is 3 calls in `initialize()` (DB 0/1/2)
- **F5: UNRESOLVED / UNOWNED** — `SharedRedisManager.get` → `None` on fault makes the webhook
  **fail open** on a read fault; `set` → `False` makes it **fail closed** on a write fault
- **Live trading: OFF / NOT CERTIFIED**

No launch-readiness percentage, audit mirror, or certification register was modified. No
implementation or remote mutation was performed or is authorized by this task.
