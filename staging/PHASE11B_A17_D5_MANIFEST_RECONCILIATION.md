# Phase 11B-A17 — D5′ Manifest Reconciliation and Approval Readiness

**Date:** 2026-10-10
**Destination:** `staging/PHASE11B_A17_D5_MANIFEST_RECONCILIATION.md` — verified **ABSENT**
before writing. No existing report or artifact was overwritten.

**Status: the three-file candidate is VERIFIED and can proceed to a human approval decision.
Execution remains BLOCKED. This phase was strictly read-only.**

| | |
|---|---|
| D5′ execution | **BLOCKED pending approvals A, C, D1–D4, E** |
| QUOTA-01 | **OPEN** |
| TENANT-01 | **OPEN** |
| Exchange-slot concurrency | **UNPROVEN** |
| Real-Redis production-path verification | **UNEXECUTED** |
| Live trading | **OFF / NOT CERTIFIED** |

---

## HEADLINE

Five things, two of them corrections to the current record.

1. **The candidate is now independently verified in this phase, not inherited.** I rebuilt the
   workflow post-image from the current PR #6 blob and got **`967b4a28e29157f97f75d31a48eb9ce5b4133ce5`**
   — confirming A16R and **retiring A15's `a2f39ebb`**, which my own A10 report carried
   forward with a caveat. `a2f39ebb` reproduces from no documented input (§4).
2. **`staging/p11b15_proposed_fixed.patch` still applies cleanly to the current PR #6 head:
   `rc=0`.** All three declared pre-images equal PR #6's stored blobs (§5).
3. **Request B is RESOLVED, and the newer report is the one that is wrong about it.** A16
   (19:39) records the webhook author's explicit consent; A16R (19:55) still calls the
   conflict open because it reasoned from A12's content rather than A16's consent. I verified
   the basis directly: A12 contains exactly **2** PR references and **0** expressing a target
   or canonical preference (§3).
4. **Correction to how every prior report describes `exchange.py`'s staged layer.** It is
   routinely called "tenant-isolation / RLS / credential-vault". By content it is **not**:
   of 268 staged added lines, **0 match tenant/RLS patterns and 0 match auth/AAL**, while 42
   match connection, 24 error-handling and 23 credential-vault. The tenant/RLS attribution
   comes from *co-location in the same staged set*, not from this file (§6).
5. **A concurrent session is working the same phase.** `PHASE11B_A17_D5_READINESS__kiro_session.md`
   (19:57) and six `p11b17_*` artifacts already exist. My destination differs, so nothing
   collided, but this is the fourth parallel effort on D5′ and I am reporting it rather than
   adapting silently (§2).

---

## 1. Entry and exit preservation invariants

Measured at entry, re-measured at exit. All held.

| Invariant | Entry | Exit |
|---|---|---|
| Branch / `HEAD` | `main` / `074b772ab16cece62cbfca0f2b78497be2ae56d5` | **same** |
| `origin/main` / divergence | `074b772a…` / `0 0` | **same** |
| Tracked index entries | 2104 | 2104 |
| Staged paths | 22 | 22 |
| Unstaged tracked | 100 | 100 |
| Unmerged entries | 0 | 0 |
| Stash entries | 2 (pre-existing) | 2 |
| **Digest A** (`git ls-files -s`, raw lines, file order) | `98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704` | **identical** |
| **Digest C** (sorted `git diff-index --cached HEAD`) | `8d9c475b86a4a71d89759bcfb15ba750073a3bb4ede719279249af22b4192251` | **identical** |
| **`git write-tree`** (recorded separately, as instructed) | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` | **identical** |
| `.git/objects` file count | 189 | **192 — +4, written by a concurrent session (§1a)** |
| Untracked | 3403 | 3410 (this phase's own `staging/` artifacts) |

### 1a. CONCURRENT CHANGE DETECTED — four objects appeared, not mine

Per the brief's instruction to stop and report rather than adapt silently: the shared object
database grew by four loose blobs **during** this phase. They are exactly the D5′ candidate
files:

| Time | SHA | What it is |
|---|---|---|
| 20:24:40 | `57e2cee8ea44…` | PR #6's workflow pre-image (already reachable; a loose copy of a packed blob) |
| 20:24:53 | `3ffbccde68c3…` | webhook test candidate |
| 20:24:53 | `7baff0b64f0c…` | metered test candidate |
| 20:26:06 | `967b4a28e291…` | **rebuilt workflow candidate** |

**These are not mine, and I established that by experiment rather than assertion.** The
obvious suspect was my own `git apply --cached --check` runs, so I tested whether that command
writes blobs when pointed at a private `GIT_INDEX_FILE`. Two attempts were inconclusive
because `git diff --no-index` emitted `a/a/<path>` headers and the patch never addressed an
index path — so "0 objects written" proved nothing and I did not report it as evidence. The
third attempt constructed the patch headers explicitly:

```
probe post-image (PR #6 workflow with /15 -> /7) : 92cbd271797f5a38c35179b83a4163c0fd64c964
present before test                              : False
git apply --cached --check  (private index)      : exit 0   <- valid and applicable
loose objects created by --check                 : 0
probe post-image stored afterwards               : False
```

A **valid, applicable** patch under `--check` created nothing. So `--check` does not write to
the object store, and my apply-checks are exonerated. The remaining explanation is the
concurrent D5′ session whose `p11b17_*` artifacts and session-tagged A17 report appeared at
19:55–19:57 (§2); materialising those three candidates with `apply --cached` (without
`--check`) or `hash-object -w` produces exactly this set.

**Impact: none on repository content.**

| Check | Result |
|---|---|
| `967b4a28`, `3ffbccde`, `7baff0b6` reachable from any ref | **False** — dangling, garbage-collectable |
| `57e2cee8` reachable | True — it is PR #6's committed blob; only a loose duplicate of a packed object |
| `HEAD` / `origin/main` | `074b772a…` — unmoved |
| Staged paths / digest C | 22 / `8d9c475b…` — unchanged |
| Branch refs | unmoved |

No ref moved, nothing was staged, and no tracked file changed. The practical significance is
not the objects themselves but what they imply: **another session is now one step from
committing this candidate.** If approval is granted, whoever executes should re-verify PR #6's
head immediately beforehand, because two sessions are now holding the same prepared change.

### Resolving a conflict in the brief

Phase 0 asks for `git write-tree`; the hard constraints forbid shared Git-object writes.
`git write-tree` normally materialises tree objects. Both were honoured by redirecting object
writes while keeping the store readable:

```
GIT_OBJECT_DIRECTORY             = <disposable temp dir>
GIT_ALTERNATE_OBJECT_DIRECTORIES = <repo>/.git/objects
git write-tree   ->  6f40856cee2bc7ea4d741f69f4b1f68b463d70a4
objects created in the temp dir : 0
shared .git/objects             : 189 -> 189
```

Zero objects were created even in the temp directory: every tree for the current staged state
already exists in the store and was found through the alternate, so `write-tree` was a genuine
no-op. The temp directory was deleted and its absence verified. **`6f40856c…` is reproducible
and method-independent**, which is why it is the invariant worth quoting (§2, digest note).

### Protected files — confirmed untouched

| Path | Status | Note |
|---|---|---|
| `.gitignore` | ` M` +15/−0 | concurrent workstream; **not touched** |
| `backend_app/core/subscription_dependencies.py` | ` M` +253/−0 | read only |
| `backend_app/routers/exchange.py` | `MM` +268/−15 staged, +154/−14 unstaged | read only |
| `tests/test_quota01_exchange_slot_is_atomic.py` | `??` | hash `2d7ccbbb04b3af04675b365809d89b4429db4888` — **present, byte-unchanged, not removed** |
| 22 staged paths | — | unchanged, digest C identical |
| `p11b14_*` / `p11b15_*` / `p11b16_*` / `p11b17_*` | 16 / 24 / 6 / 6 | **all preserved, none modified** |

---

## 2. Report discrepancy matrix

Reconciled against current evidence, not by recency. Where a newer report disagrees with an
older one, the underlying evidence decides.

| # | Claim | A10 (19:44) | A15 (19:21) | A16 (19:39) | A16R (19:55) | A17-kiro (19:57) | My measurement | Disposition |
|---|---|---|---|---|---|---|---|---|
| 1 | Canonical PR | #6 recommended | #6 | #6 | #6 or #7, A open | #6 | — | **UNRESOLVED** — recommendation only, no approval |
| 2 | Reason to keep `d5-redis-diagnostics` | sole carrier of JUnit + `state=run` | same | same | same | same | **PR #6 contains it; HEAD/#7/#8 do not** | **RESOLVED, consistent** |
| 3 | Webhook-owner consent for #6 | ABSENT | ABSENT | **GIVEN — author consents** | **"conflict open"** | RESOLVED, cites A16 | A12 has 2 PR refs, **0 targeting** | **RESOLVED. A16R is stale on this point** |
| 4 | Workflow candidate hash | `a2f39ebb` (carried, flagged unverified) | `a2f39ebb` | `967b4a28` (post-image in patch) | **`967b4a28`, `a2f39ebb` not reproducible** | `967b4a28` | **`967b4a28…` rebuilt in this phase** | **RESOLVED — `a2f39ebb` RETIRED** |
| 5 | Webhook test candidate | `3ffbccde` | `3ffbccde` | `3ffbccde` | `3ffbccde` | `3ffbccde` | **`3ffbccde68c3…` MATCH** | **VERIFIED** |
| 6 | Metered test candidate | `7baff0b6` | `7baff0b6` | `7baff0b6` | `7baff0b6` | `7baff0b6` | **`7baff0b64f0c…` MATCH** | **VERIFIED** |
| 7 | Candidates are stored Git objects | implied "blob" | implied | implied | states not stored | **states computed-only** | `cat-file -e`: **absent** | **RESOLVED — wording in 1–4 was loose** |
| 8 | Patch applies to PR #6 | not tested | asserted | `rc=0` | — | `rc=0` | **`rc=0` re-tested** | **VERIFIED** |
| 9 | Patch pre-images | — | — | `57e2cee8`/`105b6567`/`f48103ac` | same | same | **all three MATCH PR #6** | **VERIFIED** |
| 10 | Exchange-slot test disposition | defer, pending B/C | defer | defer | defer, do not delete | Option 1 / Option 2 | **test present, unmodified** | **UNRESOLVED — needs C** |
| 11 | `exchange.py` staged layer attribution | "tenant-isolation / RLS / credential-vault" | "tenant-isolation / RLS" | "AUTH-01 / CONN-01 / TENANT-01" | "concurrent staged + unstaged" | TENANT-01 | **0 tenant/RLS, 0 auth lines; 42 conn, 24 err, 23 vault** | **CORRECTED (§6)** |
| 12 | `exchange.py` apply failure line | `:197` | `:366` | `:366` | — | `:366` | **`:197` for HEAD→worktree; `:366` reported for the unstaged-only diff** | **RESOLVED — different diffs, same verdict: REFUSES** |
| 13 | Approval status | all ABSENT | A–E ABSENT | B resolved, rest ABSENT | all ABSENT | A–E not granted | **0 reviews, 0 approvals on all 3 PRs** | **CONFIRMED** |
| 14 | Staged-content digest | `98ae5bdf` | `CF474F1B` | `7379218b` | `2e994018` | `D43D5537` | **5 incomparable values** | **See digest note** |

### Digest note — the "canonical" digest has been redefined four times

Five values are now on record for one unchanged staged state: `98ae5bdf` (A9/A10),
`CF474F1B` (A14/A15), `7379218b` (A16), `2e994018` (A16R, declared canonical),
`D43D5537` (A17-kiro, also declared canonical). Each hashes a different serialisation, so
none is wrong and none is comparable with another.

**Recommendation: stop defining digests and quote `git write-tree` instead.** It is a
property of content rather than of a serialisation choice, and three independent
sessions — A16R, A17-kiro and this phase — each measured
**`6f40856cee2bc7ea4d741f69f4b1f68b463d70a4`**. That is the only staged-state invariant in
this programme that has ever reproduced across workstreams.

### Concurrent-work notice

Per the brief's instruction to report rather than adapt silently: a concurrent session
produced `PHASE11B_A17_D5_READINESS__kiro_session.md` and six `p11b17_*` artifacts at 19:55–19:57,
covering substantially this same brief. Its conclusions and mine agree on every verified item
above. My destination was free and nothing was overwritten. This is the **fourth** parallel
effort on D5′ (A14/A15 one session, A16/A16R another, A17-kiro a third, this one). The
duplication itself is now a larger risk to the work than any single technical defect in it.

---

## 3. Request B — resolved, with the consent chain checked

The brief directs that A16's consent resolves B, subject to confirming it refers to the same
candidate file and destination PR. Both confirmed:

| Link | Requirement | Evidence |
|---|---|---|
| Who | author of the webhook change | A16 §3B: "I wrote the webhook change", authored in Phase 11B-A12 |
| What | the same candidate file | consent is for `tests/test_webhook_duplicate_delivery_real_redis.py`; A16's manifest records worktree `3ffbccde`; **I measured `3ffbccde68c3b57485a9971d4977c34aff598b47` — unchanged** |
| Where | the intended destination PR | A16 §3B: "I consent to the webhook change landing on PR #6" — the PR recommended under A |
| Basis for the prior "targets #7" claim | should not survive checking | **verified: A12 contains 2 PR references, both incidental (a baseline table row at L44, a safety confirmation at L494), and 0 expressing a target or canonical preference** |

The candidate has **not** materially changed, so per the brief this consent is **not** to be
requested again. **B = RESOLVED.**

One caveat for completeness: the consent is recorded by a session identifying itself as the
author. There is no independent cryptographic attestation — `staging/` is untracked and
unsigned. If a stricter provenance standard is required, that is a process decision, not a
technical gap I can close.

---

## 4. Independently recomputed three-file manifest

Parent: PR #6 head `d07014d36d32c63db44f8fb9659933c0e84e82f2`.

| # | Path | PR #6 pre-image (stored) | Candidate post-image | Recomputed here? |
|---|---|---|---|---|
| 1 | `.github/workflows/01-pr-check.yml` | `57e2cee8ea448266efafa1441c2443a3c668e06e` | **`967b4a28e29157f97f75d31a48eb9ce5b4133ce5`** | **YES — rebuilt from the PR #6 blob** |
| 2 | `tests/test_webhook_duplicate_delivery_real_redis.py` | `105b65671b279efb958905c718e1831719ad26ee` | `3ffbccde68c3b57485a9971d4977c34aff598b47` | **YES — re-hashed** |
| 3 | `tests/test_quota01_metered_reservation_is_hard.py` | `f48103ac79a2b7adf76339aab0f2861168b1defc` | `7baff0b64f0c7441f4192a9a6b2df53e7a68dce9` | **YES — re-hashed** |

### Workflow — rebuilt and verified line by line

Base: `d07014d3:.github/workflows/01-pr-check.yml`, 43,699 B, 812 lines, **LF** (no CRLF).
Transformation, the documented regex from `p11b15_build_patch.py`:

```
re.sub(r"(^\s*AERORA_TEST_REDIS_URL:\s*redis://localhost:6379)/15\s*$", r"\1/0", flags=re.M)
substitutions made: 2
```

| Required property | Measured |
|---|---|
| Both `AERORA_TEST_REDIS_URL` values target `/0` | **2 of 2** — `['redis://localhost:6379/0', 'redis://localhost:6379/0']` |
| Lines still on `/15` | **0** |
| `d5-redis-diagnostics` retained | **yes** |
| `provisioning-and-isolation` not imported | **absent** |
| Unrelated workflow changes | **none** — exactly 2 lines differ |
| Line count | unchanged (812) |
| Byte delta | **−2** (two characters, `/15`→`/0` twice) |

The two differing lines, in full:

```
L216:  AERORA_TEST_REDIS_URL: redis://localhost:6379/15  ->  ...6379/0
L735:  AERORA_TEST_REDIS_URL: redis://localhost:6379/15  ->  ...6379/0
```

L216 is in `unit-tests`; L735 is in `d5-redis-diagnostics`. The 37-line comment rewrite that
A14/A15 described is **not** in the candidate — correctly, since the comment in PR #6 still
falsely claims `/15` "pins a dedicated database index". Correcting it is a separate,
separately-approved documentation change.

**`a2f39ebb` is retired.** It does not reproduce from the PR #6 blob plus the documented
regex (which yields `967b4a28`), nor from the worktree copy plus the regex (which yields
`91d55a37`, and additionally contains `provisioning-and-isolation`). My A10 report carried
`a2f39ebb` with an explicit caveat that it was not independently reproduced; that caveat is
now resolved against it.

### Candidate identifiers are computed digests, not stored objects

Verified with `git cat-file -e`:

| Identifier | In the object store? |
|---|---|
| `57e2cee8`, `105b6567`, `f48103ac` (PR #6 pre-images) | **STORED** |
| `d40a1bb7` (gate), `2d7ccbbb` (slot test) | **STORED** — committed on the D5′ branches |
| **`967b4a28`** (workflow candidate) | **computed-only** |
| `3ffbccde`, `7baff0b6` (test candidates) | **computed-only** |
| `a2f39ebb` (retired) | **computed-only** |

A committer cannot reference a computed-only identifier; the content must come from the
worktree or from the patch. This is also positive evidence that **nothing was ever staged or
written with `-w`** across any phase.

### Unrelated worktree content is not in the candidate

The worktree differs from `HEAD` in ~120 files, ~59 of them production. The candidate takes
content for exactly three paths, two of them test files and one derived from PR #6 rather
than from the worktree. The worktree workflow (`91d55a37`) is explicitly **not** used,
because it lacks the diagnostic job and carries `provisioning-and-isolation`.

---

## 5. Patch applicability

Tested against the **current** PR #6 head with a disposable private index seeded from
`d07014d3`, check-only. The shared index was never named.

| Patch | Size | `apply --cached --check` | Declared pre→post images |
|---|---|---|---|
| **`staging/p11b15_proposed_fixed.patch`** | 34,947 B | **`rc=0` — APPLIES CLEANLY** | `57e2cee8..967b4a28`, `105b6567..3ffbccde`, `f48103ac..7baff0b6` |
| `staging/p11b15_wf_only.patch` | 1,111 B | `rc=0` — applies | `57e2cee8..967b4a28` |
| `staging/p11b15_proposed.patch` | 35,695 B | `rc=1` — **REJECTED** | same declarations |

The rejected variant fails at `01-pr-check.yml:213` and
`test_webhook_duplicate_delivery_real_redis.py:41`. That is the known emitter defect
diagnosed in `p11b15_eol.txt` (a diff built between an LF old side and a CRLF new side), not
a problem with the change. **It was not repaired** — per the brief, the mismatch is reported
and that line of validation stops. `p11b15_proposed_fixed.patch` is the one to use.

All three patches declare the same post-image for the workflow, `967b4a28`, which
independently corroborates my rebuild in §4.

### Private-index safeguards, as performed

| Safeguard | Evidence |
|---|---|
| Disposable private index seeded from the exact PR #6 head | `GIT_INDEX_FILE=<temp>`, `git read-tree d07014d3` → exit 0 |
| Check-only application | `git apply --cached --check` — never without `--check` |
| No staging in the shared index | shared `.git/index` never named; digest C identical |
| No candidate objects written **by this phase** | `git hash-object` always without `-w`, on temp files outside the repo; `apply --cached --check` proven non-writing by experiment (§1a). The +4 objects observed are a concurrent session's. |
| Temp files deleted | temp dirs removed, absence asserted |
| Shared invariant verified afterward | digest A, digest C, `write-tree`, staged count all identical |

---

## 6. Exchange-slot ownership and boundary

Kept entirely outside the three-file candidate. Nothing removed, rewritten, or excluded from
the repository.

### The guard and the call site are both absent from the canonical branch

| Symbol | `HEAD` | PR #6 | Worktree |
|---|---|---|---|
| `def _slot_lock_key` | **NO** | **NO** | yes |
| `async def _acquire_slot_lock` | **NO** | **NO** | yes |
| `async def _release_slot_lock` | **NO** | **NO** | yes |
| `def exchange_connection_slot_guard` | **NO** | **NO** | yes |

`backend_app/routers/exchange.py` is **825 lines and identical at `HEAD` and PR #6**, with
`guard_refs=0` and `legacy_refs=3`. The staged version is 1078 lines, still `guard_refs=0`.
Only the worktree (1218 lines) has `guard_refs=3`.

### `store_keys` — the race, at line level

Identical on `HEAD` and PR #6:

```
L201:  await check_exchange_connection_slot(user, supabase, body.exchange_id)   # read
L221:  vault.store_exchange_keys(...)                                           # write
```

A read-then-write with no lock between them. **The QUOTA-01 defect is live on the canonical
branch**, and deferring the test would hide the failing test, not the defect.

### Why the integration cannot be copied or cherry-picked

Apply tests onto `HEAD`, private index, check-only, with a positive control:

```
backend_app/core/subscription_dependencies.py   13,923 B patch   rc=0  APPLIES   (control)
backend_app/routers/exchange.py                 28,938 B patch   rc=1  REFUSES
    error: patch failed: backend_app/routers/exchange.py:197
```

The control is what makes the refusal meaningful: the guard definitions land cleanly, so the
harness is sound and `exchange.py`'s refusal is a real conflict. Prior reports cite `:366`;
I measure `:197`. Both are correct — `:366` is the *unstaged-only* diff (worktree vs index),
`:197` is the *full* `HEAD`→worktree diff. The verdict is the same under either.

Copying the call site would therefore require hand-rewriting an integration that has never
been executed, or carrying the other workstream's 268 staged lines. Neither is permissible.

### Which unrelated changes share the file — correcting the record

Every prior report attributes `exchange.py`'s staged layer to "tenant-isolation / RLS /
credential-vault". Content analysis of its **268 staged added lines** does not support the
tenant/RLS part:

| Theme | Matching added lines |
|---|---|
| connection / CONN | **42** |
| error handling | **24** |
| credential vault | **23** |
| tenant / RLS (`tenant`, `rls`, `row-level`, `set_config`, `app.current`) | **0** |
| auth / AAL (`aal`, `claim`, `jwt`, `mfa`) | **0** |
| slot guard (F1) | **0** |

The tenant-isolation/RLS attribution is correct for the **co-located staged set** — which
includes `backend_app/core/tenant_db_context.py`, `scripts/verify_rls_enforcement.py`,
`docs/adr/ADR-0006/0007`, `docs/saas-architecture/tenant-isolation-matrix.md` and five
`tests/test_tenant_*` / `test_sc3_*` files — but **not** for this file's own hunks. The
practical consequence is unchanged (the layer is another workstream's and must not be
carried), but the decision question in C should be addressed to the holder of a
**connection / credential-vault** change, not described to them as tenant-isolation work.

### Owner identifiability: UNKNOWN

| Evidence | Result |
|---|---|
| `git log` on `exchange.py` | one commit, `6c5660a1 Narendra2212` — the sole committer for all repository history; identifies nobody in particular |
| Staged layer authorship | uncommitted, so no git authorship exists |
| `staging/` reports | no session claims the `exchange.py` or `subscription_dependencies.py` change |
| PR assignees / reviewers | 0 on all three PRs |

**Marked UNKNOWN**, as the brief directs. The exact decision question that owner must answer:

> `backend_app/core/subscription_dependencies.py` (` M`, +253/−0) applies onto `main`
> **cleanly** (`rc=0`, verified with a control) and supplies all four guard symbols. Shipping
> it **alone** should clear the 20 `AttributeError` slot failures (14 on
> `exchange_connection_slot_guard`, 6 on `_slot_lock_key`). The 21st,
> `test_store_keys_uses_the_guard_around_its_vault_write`, would stay red — **correctly**,
> because `store_keys` at `exchange.py:201` still calls the non-atomic
> `check_exchange_connection_slot` before the vault write at `:221`.
>
> **Option 1 —** approve the standalone guard file: 20 of 21 slot tests go green, one stays
> red as an honest marker of the unfixed race. Does not touch `exchange.py`.
> **Option 2 —** defer the guard file until an owner-approved integration protects the vault
> write; all 21 stay red.
>
> Under both options `exchange.py` must not be force-applied, copied, or reconstructed, and
> `tests/test_quota01_exchange_slot_is_atomic.py` must not be removed without separate
> approval (D4).

---

## 7. Evidence boundary

### The nine production-path requirements

`tests/test_quota01_metered_reservation_is_hard.py` re-hashes to
`7baff0b64f0c7441f4192a9a6b2df53e7a68dce9` — **byte-identical** to the file whose nine
requirements I verified pattern-by-pattern in `PHASE11B_A10_D5_APPROVAL_READINESS.md` §3. The
hash match is the evidence that that verification still applies; it is reused rather than
re-run, per the efficiency instruction. All nine were met: opt-in and fail-closed gating;
mock rejection via `pytest.fail`; bidirectional keyspace proof; 50 concurrent calls through
production `SubscriptionEngine.reserve_quota` at `limit=10`; exact admission and refusal
counts; `get_quota_usage` cross-checked against the raw key; reverted usage on refusal;
zero-allowance with `exists(key) == 0`; cleanup scoped to `_meter_key(user, resource, period)`.

**No third test was written.** The two existing tests cover the requirements.

### Real-Redis execution status

**UNEXECUTED.** No required real-Redis case has run against a real server in any phase. No
Redis was installed, started, or contacted here, and no CI was triggered.

| Evidence class | Result |
|---|---|
| Local opt-out run (A10, files unchanged since by hash) | 26 passed, **11 skipped** — skips are **not** passes |
| Configured-but-unreachable (A10) | **exit 1, 2 failed, 0 skipped** via `real_redis_gate.py:175` — fail-closed confirmed |
| Endpoint secrecy in that output | `6399`, `127.0.0.1`, `redis://` → **0 occurrences** |
| Collection across the three D5′ files | 37 tests (9 metered + 6 webhook + 22 slot) |
| Mock-rejection branch, keyspace probe | **never executed** — both need a reachable server |

No required real-Redis case can silently skip: the gate classifies `unset → skip` but `/0`,
unreachable-port and non-loopback all **→ fail**.

### F5 — stated without overclaiming

The mechanism is confirmed from source. `backend_app/core/cache/redis_manager.py` proxies are
fail-soft — `get` returns `None` on any exception (L757-765), `set` returns `False`
(L767-775). In `razorpay_webhook` (`billing.py:2846-2855`) a cache **read** fault is
indistinguishable from "key absent" so the handler proceeds (**fails open**), while a
**write** fault reads as a lock collision so it returns `duplicate` (**fails closed**).

**The consequence is not established and is not asserted here.** A genuine DB-level replay
guard exists for the referral-obligation write — a unique constraint on
`(provider, provider_payment_id)` with a SQL function returning `NULL` on replay
(`billing.py:840`, `:3070-3071`). The main settlement writes pass `provider_payment_id`
(`:2460`, `:2673`, `:2978`) but carry no visible `ON CONFLICT`, and I did not inspect the
database schema. So: **cache-layer fail-open confirmed; double settlement neither
demonstrated nor excluded.** F5 is billing-critical, **UNOWNED**, and outside D5′.

### Status not changed without new evidence

QUOTA-01 **OPEN** and TENANT-01 **OPEN**. No evidence in this phase bears on either: the
production-path quota tests remain unexecuted, `store_keys` remains unguarded, and the 22
staged TENANT-01 paths were not touched.

---

## 8. Approval matrix

Legend — **VERIFIED**: measured in this phase. **PROPOSED**: a recommendation only.
**APPROVED**: explicit human grant on record. **BLOCKED**: cannot proceed. **UNRESOLVED**:
awaiting a named party.

| # | Item | State | Basis |
|---|---|---|---|
| **A** | Canonical PR = #6 | **PROPOSED / UNRESOLVED** | #6 is the sole carrier of `d5-redis-diagnostics` (**VERIFIED**); #7 ≡ #8 tree-identical so one closes regardless (**VERIFIED**). **No approval exists** — 0 reviews, 0 approvals, 0 labels, 0 assignees on all three PRs; the only comment on each is from the `coderabbitai` bot. |
| **B** | Webhook-owner consent for the verified candidate | **RESOLVED** | Author's explicit consent in A16 §3B, chain checked in §3: same file (`3ffbccde…`, re-measured), same destination (#6), and A12 contains **0** PR-targeting statements. Not to be requested again — candidate unchanged. |
| **C** | Exchange-slot: guard + `store_keys` land together, or defer | **UNRESOLVED — owner UNKNOWN** | Guard and call site both absent from PR #6 (**VERIFIED**); guard file applies to `HEAD` cleanly, `exchange.py` refuses (**VERIFIED**, with control); owner not identifiable from repository evidence (§6). Decision question drafted in §6. |
| **D1** | Commit the exact three-file patch | **BLOCKED — not approved** | `p11b15_proposed_fixed.patch` applies at `rc=0` to the current PR #6 head; all pre-images match; post-images recomputed here (**VERIFIED**). Ready, unauthorized. |
| **D2** | Push the named branch | **BLOCKED — not approved** | depends on D1 and A |
| **D3** | Trigger the canonical PR's CI | **BLOCKED — not approved** | first real execution of both repairs |
| **D4** | Optional exchange-slot test removal | **BLOCKED — requires separate approval** | depends on C; test is present and byte-unchanged |
| **E** | Close duplicate PRs | **BLOCKED — deferred by design** | only after canonical content lands and CI evidence is reviewed; closing earlier discards the only branches carrying the A5 webhook variant |

**No approval is inferred from any recommendation, report authorship, or from the patch being
ready.** B is the only item resolved, and it is resolved by an explicit consent statement, not
by inference.

### Expected CI outcome, so a red build is not misread

If D1–D3 are granted without C: the 2 webhook keyspace failures should be fixed, the 2 new
production-path quota tests execute for the first time (**outcome unknown — that is the
point**), one webhook test stops passing vacuously, and the **21 slot-lock failures persist by
construction**. **PR #6 will remain RED.** Acceptance must be judged by node ID against the
baseline (run `37346853502`, `6c5660a1`, 39 failed, node IDs in
`staging/p11b14_preexisting.txt`) — not by job status, and never reported as green.

---

## 9. Command and action log

All read-only. No mutation of any kind.

| # | Action | Mutating? |
|---|---|---|
| 1 | Destination pre-existence check for the A17 path | no |
| 2 | `git rev-parse` HEAD / branch / origin-main; `rev-list --count` | no |
| 3 | `git ls-files -s`, `diff-index --cached HEAD`, `diff --name-only`, `ls-files --others/-u`, `stash list` | no |
| 4 | `git write-tree` with `GIT_OBJECT_DIRECTORY` redirected to a temp dir | no — 0 objects created, temp removed |
| 5 | `git rev-parse <PR6>:<path>` ×3 — pre-images | no |
| 6 | `git hash-object` (no `-w`) on two worktree test files | no |
| 7 | Workflow candidate rebuilt from the PR #6 blob in a temp file outside the repo; hashed without `-w`; file deleted | no |
| 8 | `git read-tree <PR6>` into a private `GIT_INDEX_FILE`; `git apply --cached --check` ×3 | no — private index, check-only, removed |
| 9 | `git cat-file -e` ×9 — stored vs computed | no |
| 10 | `git cat-file blob` for `HEAD`/PR#6 versions of `exchange.py`, `subscription_dependencies.py` | no |
| 11 | `git diff --cached --numstat` / `--numstat` / `--cached` on `exchange.py`; theme classification of added lines | no |
| 12 | `git read-tree HEAD` into private indexes; `git apply --cached --recount --check` ×2 with positive control | no — removed |
| 13 | `git log -3` on `exchange.py`; staged path listing | no |
| 14 | Read A10, A12, A15, A16, A16R, A17-kiro reports and `p11b15_*` patches | no |
| 15 | Exit re-measurement of all invariants | no |
| 16 | Object-delta investigation: `git cat-file -t/-s/-p`, `rev-list --all --objects`, `fsck --connectivity-only`, mtime walk of `.git/objects` | no |
| 17 | Attribution experiment ×3 — probe patch built in a temp dir, `apply --cached --check` against a private index, `hash-object` without `-w`; all temp files removed | no — proven non-writing (§1a) |
| 18 | Wrote `staging/a17_*.{py,txt}` and this report | `staging/` only |

**Not performed:** no `git add`, commit, push, checkout, reset, clean, stash, cherry-pick, or
unguarded `apply`; no PR edit, closure, merge, label, or review; no CI trigger; no Redis,
PostgreSQL, production or shared-service connection; no package installation or infrastructure
start; **no object written to the shared database by this phase** — the four that appeared are
attributed to a concurrent session by experiment (§1a), and I am reporting rather than
claiming an unqualified delta of zero; no change to `.gitignore`, migration ledgers,
PostgreSQL snapshots, or any other workstream's artifacts; no test removed, rewritten, or
excluded; no readiness score, gate register, or certification record altered.

---

## 10. The smallest next human decision

**Decision A: name the canonical PR — #6 or #7.** One choice between two named branches. It
gates D1–D3 and E, it is the only open item that is a genuine disagreement rather than a
missing signature, and nothing needs re-measuring first: the candidate is reproducible,
hash-verified, and applies at `rc=0`.

**C is independent and can be answered in parallel** by the holder of the `exchange.py` /
`subscription_dependencies.py` change, whoever that is. It changes only how many slot tests
stay red.

### Actions that remain unauthorized

1. Committing `p11b15_proposed_fixed.patch` to any branch (**D1**).
2. Pushing any branch (**D2**).
3. Triggering or re-running any CI workflow (**D3**).
4. Removing or modifying `tests/test_quota01_exchange_slot_is_atomic.py` (**D4**).
5. Closing, editing, re-targeting, or merging PR #6, #7, or #8 (**E**).
6. Committing `subscription_dependencies.py` or any version of `exchange.py` (**C**).
7. Writing the workflow comment correction (separate documentation change).
8. Touching F4 (`backend_app/backend/redis_manager.py`) or F5
   (`backend_app/core/cache/redis_manager.py`, `billing.py`) — both **UNOWNED**.

QUOTA-01 **OPEN** · TENANT-01 **OPEN** · exchange-slot concurrency **UNPROVEN** ·
real-Redis production-path verification **UNEXECUTED** · D5′ execution **BLOCKED pending
approvals** · live trading **OFF / NOT CERTIFIED**
