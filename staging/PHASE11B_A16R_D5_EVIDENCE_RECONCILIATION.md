# Phase 11B-A16R — D5′ Evidence Reconciliation and Approval Handoff

**Output path:** `staging/PHASE11B_A16R_D5_EVIDENCE_RECONCILIATION.md` — confirmed unused
before writing. `staging/PHASE11B_A15_D5_EXECUTION_REPORT.md` was **not** overwritten
(17,011 bytes, mtime `10-10 19:21:01`, unchanged), and all **24** `p11b15_*` artifacts are
preserved. This report exists because one material correction was found: **the workflow
candidate fingerprint recorded in A15 §1b is not reproducible, and the correct value is
`967b4a28`.**

**No source change, no Git mutation, no remote mutation, no database access.** Nothing was
staged, committed, pushed, or closed; no CI was triggered; no object was written to the
shared Git object database; no test was removed.

| | |
|---|---|
| QUOTA-01 | **OPEN** |
| TENANT-01 | **OPEN** |
| D5′ acceptance | **NOT PASSED** |
| F1 | **BLOCKED** |
| F4 / F5 | **UNRESOLVED, UNOWNED** |
| Live trading | **OFF / NOT CERTIFIED** |

---

## 1. Canonical staged-content invariant — method fixed

Three mutually-incomparable values for "the staged-content fingerprint" were circulating.
One method is adopted here and the others are retired to historical status.

**Canonical method** — exact command and normalization:

```
bytes   := stdout of `git diff --cached --raw`
normalize: CRLF -> LF; strip trailing whitespace per line; drop blank lines; join with LF
digest  := sha256(normalized bytes), lowercase hex
```

Chosen because `--raw` covers **path, mode and blob SHA** for every staged entry — the
narrowest thing that actually constitutes "staged content" — and the normalization removes
the shell/encoding dependence that produced the divergence.

| Measurement | Value |
|---|---|
| **Canonical staged digest** | `2e994018e375f318f4e6dc9a67ab248e4622de8c426146ca756d5f47d2767152` |
| **`git write-tree`** | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` |
| Staged paths | **22** |
| Unstaged paths | **100** |
| HEAD | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| Branch / upstream | `main`, `0 / 0` vs `origin/main` |
| Worktrees | 1 — `C:/aerora_quant_backend_updated_final1 074b772a [main]` |

### 1.1 `write-tree` was captured without writing to the shared object database

§2 asks for the `write-tree` result and also forbids writing Git objects. `git write-tree`
normally writes tree objects, so it was run with its object output redirected:

```
GIT_OBJECT_DIRECTORY            = <unique temp dir>
GIT_ALTERNATE_OBJECT_DIRECTORIES = <repo>/.git/objects    # read-only fallback
git write-tree
```

**Objects created in the temp directory: 0.** Every tree for the current staged state
already existed in the shared store and was found through the alternate, so `write-tree`
was a genuine no-op — not merely redirected. The temp directory was removed and its absence
verified. **Nothing was written to `.git/objects`.**

### 1.2 Historical, method-specific values — do not compare

| Value | Method | Source |
|---|---|---|
| `CF474F1B83FD37173F0AD3F2B5417D07CE4B26DC66A5CF4D6697AE9ACB568767` | `git ls-files -s`, that workstream's encoding | `PHASE11B_A14_D5_CONSOLIDATION_REPORT.md` §1, `PHASE11B_A15_D5_EXECUTION_REPORT.md` §1 |
| `98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704` | `git ls-files -s`, normalized bytes (2,104 entries) | A16R, this phase |
| `FA73915562B730051A1F8C4FFE20B0507F72751889A944593495E09CF22E902C` | `git ls-files -s` via `Out-File -Encoding utf8` (BOM + CRLF) | `PHASE11B_PG_TOOLCHAIN_PRESERVATION_REPORT.md` |
| `4B807EF26F24CB3505D623F6B2E2E65C2FF9E572089E90E87CDA1B883A94994F` | `git diff --cached --raw` via `Out-File -Encoding utf8` | `PHASE11B_PG_TOOLCHAIN_PRESERVATION_REPORT.md`, `PHASE11B_PG_PRESERVATION_COORDINATION_REPORT.md` |

All four describe the **same unchanged staged state**. They differ only by method. The raw
`.git/index` SHA-256 remains unusable as an invariant — ordinary read-only `git status`
rewrites its stat cache.

---

## 2. Candidate fingerprint reconciliation

A hash fingerprint is **not** evidence that a Git object exists. Both facts are reported
separately. `git hash-object` was used without `-w`, so nothing was written.

| Candidate | Path / derivation | State | Measured SHA-1 | Matches recorded? | Stored object? |
|---|---|---|---|---|---|
| Webhook test | `tests/test_webhook_duplicate_delivery_real_redis.py` | **on disk** | `3ffbccde68c3b57485a9971d4977c34aff598b47` | **yes** (`3ffbccde`) | **no** |
| Metered quota test | `tests/test_quota01_metered_reservation_is_hard.py` | **on disk** | `7baff0b64f0c7441f4192a9a6b2df53e7a68dce9` | **yes** (`7baff0b6`) | **no** |
| Gate | `tests/real_redis_gate.py` | **on disk** | `d40a1bb7766307f067d914ebc8e6fd705e04a222` | **yes** (`d40a1bb7`) | **yes** |
| Slot-lock test | `tests/test_quota01_exchange_slot_is_atomic.py` | **on disk** | `2d7ccbbb04b3af04675b365809d89b4429db4888` | **yes** (`2d7ccbbb`) | **yes** |
| **Workflow candidate** | rebuilt — see §2.1 | **reconstructible** | **`967b4a28e29157f97f75d31a48eb9ce5b4133ce5`** | **NO** — A15 records `a2f39ebb` | **no** |

The gate and slot-lock blobs exist in the object store because they are committed on the D5′
branches. The webhook and metered worktree versions are **not** stored objects — they are
worktree-only content whose hashes merely *match* the recorded fingerprints.

### 2.1 The workflow candidate — A15's fingerprint is not reproducible

**Base input, confirmed:** `d07014d3:.github/workflows/01-pr-check.yml`, blob
`57e2cee8ea448266efafa1441c2443a3c668e06e`, 812 lines, LF.
This matches the pre-image `index 57e2cee8..` recorded in `staging/p11b15_wf_only.patch`.

**Documented transformation**, from `staging/p11b15_build_patch.py`:

```python
re.sub(r"(^\s*AERORA_TEST_REDIS_URL:\s*redis://localhost:6379)/15\s*$", r"\1/0",
       wf6, flags=re.M)        # written utf-8, newline=""
```

Reproduced in an isolated temp file outside the repository, hashed with `git hash-object`
(no `-w`), temp file removed and its absence verified:

| Input | Result | `provisioning-and-isolation` |
|---|---|---|
| PR #6 blob (LF) + the regex | **`967b4a28e29157f97f75d31a48eb9ce5b4133ce5`** | absent ✔ |
| Worktree copy (CRLF, 46,891 B) + the regex | `91d55a379115c88edd2fd6be01871b82a75ef498` | **present ✘** |
| A15 §1b records | `a2f39ebbe76d4e75be7075080587c00d47094456` | — |

**`a2f39ebb` reproduces from neither documented input.** Its provenance is not recoverable
from the 24 surviving `p11b15_*` artifacts. A14/A15 describe the candidate as rebuilt from
"`d07014d3` + worktree comment block", which is a byte-level recipe no surviving artifact
records.

**`967b4a28` is the correct candidate, and it is independently corroborated.**
`staging/p11b15_wf_only.patch` records the post-image as `index 57e2cee8..967b4a28`, which
is exactly the rebuild above. Every documented semantic requirement is satisfied:

| Requirement | Verified |
|---|---|
| Exactly 2 lines changed | **2** |
| `AERORA_TEST_REDIS_URL … /15` lines remaining | **0** |
| `AERORA_TEST_REDIS_URL … /0` lines present | **2** (`unit-tests` line 216, diagnostic job line 735) |
| `d5-redis-diagnostics` job retained | **yes** |
| `provisioning-and-isolation` not added | **absent** |
| Historical `/15` narrative in comments preserved | **yes** |

Why `a2f39ebb` is most likely stale: `staging/p11b15_eol.txt` diagnoses the original patch
generator as building a diff between an LF old side and a CRLF new side —
*"This is a defect in my patch-generation script, not in the proposed change"* — and three
patch generations survive (`p11b15_proposed.patch` 19:09, `_clean` 19:11, `_fixed` 19:13),
consistent with the fingerprint having been recorded before that fix and never revised in
the report.

> **Correction of record.** Anyone executing against `a2f39ebb` has no artifact to apply
> and no way to verify it. **Use `967b4a28`**, rebuilt by the §2.1 recipe from
> `57e2cee8`. Retire `a2f39ebb`.

---

## 3. Technical and ownership boundaries — reconfirmed without mutation

| Boundary | Status |
|---|---|
| PR #6 recommended canonical because it carries the diagnostic job | **holds** — `d5-redis-diagnostics` present in `57e2cee8`; `provisioning-and-isolation` absent |
| PRs #7 and #8 tree-identical | **holds** — both `271f13c29d3c09a205dbaae959cad002cc0fd0a6`; `git diff 49e70da2 a624ce89` → no differences |
| Webhook owner must agree before their change moves PR | **unchanged** — `PHASE11B_A12_WEBHOOK_KEYSPACE_FIX.md` targets **#7**; A14 proposes **#6**. Conflict **open** |
| F1 separate — `exchange.py` has concurrent staged *and* unstaged changes | **holds** — `MM`: staged **+268 / −15**, unstaged **+154 / −14**; `subscription_dependencies.py` ` M` **+253 / −0**. Read only; nothing reconstructed or copied |
| F4 unowned | **holds** — `backend_app/backend/redis_manager.py` clean |
| F5 unowned | **holds** — billing-critical; the concurrent `billing.py` change does not close it |
| Three-file manifest not authorized | **holds** — see §4 |

PR state re-fetched read-only this phase (`gh pr list`), local and remote heads compared:

| PR | Branch | Head (local == remote) | Tree | State | Merge | reviewDecision | reviews |
|---|---|---|---|---|---|---|---|
| #6 | `ci/d5-real-redis-quota-evidence` | `d07014d36d32c63d…` | `d28b6d1a…` | OPEN | UNSTABLE | **NONE** | **0** |
| #7 | `ci/d5-prime-real-redis` | `49e70da23c2858ff…` | `271f13c2…` | OPEN | UNSTABLE | **NONE** | **0** |
| #8 | `ci/d5-real-redis-switch` | `a624ce89b1600 5b0…` | `271f13c2…` | OPEN | UNSTABLE | **NONE** | **0** |

No PR is a draft. None has a label or assignee.

---

## 4. Approval evidence and the A–E decision request

**No approval exists for any item.** Evidence, from this phase and from
`staging/p11b15_approval_search.txt`:

* `reviewDecision` empty and `reviews = 0` on #6, #7 and #8.
* The only comment on each PR is an **automated `coderabbitai` summary**. Per the brief, an
  automated comment is not approval.
* The repository-side scan of 127 `staging/` documents found only **recommendations** —
  `PHASE11B_A14_D5_CONSOLIDATION_REPORT.md` §3 proposing #6, `PHASE11B_A12_WEBHOOK_KEYSPACE_FIX.md`
  targeting #7 — and no approval record. A recommendation is not approval.
* No owner approval has appeared since the last audit.

| # | Decision requested | Default if withheld | Blocks |
|---|---|---|---|
| **A** | **Canonical PR: #6 or #7.** #6 carries the diagnostic job and excludes `provisioning-and-isolation`; #7 is tree-identical to #8 and is the webhook owner's stated target. Choosing #7 requires porting the diagnostic job. | nothing proceeds | B, D, E |
| **B** | **Webhook owner's explicit agreement** to carry `tests/test_webhook_duplicate_delivery_real_redis.py` (`3ffbccde68c3…`, worktree-only) into the chosen PR, unchanged. | their F2/F3 fix stays unlanded | D |
| **C** | **F1 owner's decision**: ship the slot guard **with** its production integration under their own coordinated change, **or** defer the slot-lock tests to a separately approved change. `exchange.py` carries two concurrent layers, so only that owner can land it. **Do not delete tests and do not carry worktree-only implementation.** | 21 slot-lock failures persist in CI | D |
| **D** | **Commit, push and CI execution** for the approved manifest — workflow `967b4a28` plus whichever of the two test blobs A/B/C admit. | candidate stays inert | E |
| **E** | **Closure of duplicate PRs** after the canonical PR carries approved content. #7 and #8 are byte-identical duplicates; at least one should close. | three duplicate PRs remain open | — |

An approval for one item does not imply any other.

---

## 5. Outstanding risks and the next permitted action

| Risk | State |
|---|---|
| **F1** — exchange-slot guard | **BLOCKED.** Implementation exists only in the worktree, entangled with a concurrent staged change in `exchange.py`. No evidence for slot concurrency or release safety. |
| **F4** — `get_redis_url` discards an explicit DB and captures `REDIS_URL` at import | **UNOWNED, unfixed.** `backend/redis_manager.py` clean. The `/0` alignment and the derived-URL probe are compensations, not a fix. Inspect that module and all call sites before proposing code. |
| **F5** — webhook idempotency cannot distinguish "new" from "cache unavailable" | **UNOWNED. Billing-critical.** Fail-soft cache proxies; needs its own review across `billing.py`, cache fallback and durable DB constraints. |
| D5′ acceptance | **NOT PASSED.** All three PRs failing. Real-Redis results and opt-out skips must stay separated; no real Redis is configured locally, so nothing was executed this phase. |
| Stale fingerprint | **CORRECTED here** — use `967b4a28`, not `a2f39ebb` (§2.1). |

**Next permitted action: obtain decision A.** It is one choice between two named PRs, it
gates B, D and E, and it is the only open item that is a genuine disagreement between two
workstreams rather than a missing signature. Nothing in §2 needs re-measuring first — the
candidate set is now reproducible and hash-verified.

---

## 6. Shared-state preservation

| Check | Before | After |
|---|---|---|
| HEAD / branch | `074b772a` / `main` | **unchanged** |
| Canonical staged digest | `2e994018…2767152` | **unchanged** |
| Staged / unstaged paths | 22 / 100 | **22 / 100** |
| Worktrees | 1 | **1** |
| PR heads (#6/#7/#8) | `d07014d3` / `49e70da2` / `a624ce89` | **unchanged, local == remote** |
| `PHASE11B_A15_D5_EXECUTION_REPORT.md` | 17,011 B, 19:21:01 | **untouched** |
| `p11b15_*` artifacts | 24 | **24, none modified** |
| Objects written to `.git/objects` | — | **0** |
| Temp artifacts | — | 2 created outside the repo, both removed and verified absent |

Commands executed were limited to: `git rev-parse`, `rev-list`, `worktree list`, `status`,
`diff --cached`/`diff` (read), `show`, `hash-object` (no `-w`), `cat-file -e`, `write-tree`
(object output redirected to temp), and `gh pr list` (read-only). Files written: this
report only.

---

QUOTA-01 **OPEN** · TENANT-01 **OPEN** · D5′ acceptance **NOT PASSED** · F1 **BLOCKED** ·
F4 / F5 **UNRESOLVED** · live trading **OFF / NOT CERTIFIED** · no commit, push, PR edit,
PR closure, CI trigger, merge, database access, or concurrent-workstream overwrite
