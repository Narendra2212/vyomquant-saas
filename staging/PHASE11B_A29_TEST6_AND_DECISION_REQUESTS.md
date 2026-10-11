# Phase 11B / A29 — Ledger Test 6 Delivered; Scope, D5 and D-A27-1 Decision Requests

**Date:** 2026-10-11 · Continues `staging/PHASE11B_A28_LEDGER_GATE_DELTA.md`
**Destination verified ABSENT before writing.** A28 was not overwritten; no register, ledger or decision packet was modified.

> **One authorized change was implemented and verified end to end: ledger §3.6 test 6.**
> **No decision was received. The ledger was not edited. Line 802 still reads `NOT GRANTED`.**
> **A concurrent session committed and pushed the entire tree mid-phase — §1.2. My work survived; nothing was lost.**

| | |
|---|---|
| Live trading | **OFF / NOT CERTIFIED** |
| PostgreSQL integration | **UNVERIFIED** |
| TENANT-01 / QUOTA-01 | **OPEN** |
| Paper track | **53%**, unrevised |
| Decisions received this phase | **0** |
| Ledger / register / scorecard edits | **0** |
| Source files changed | **0** |
| Test files changed | **1** (authorized) |

---

## 1. Git state — initial, the mid-phase event, and final

### 1.1 Initial (A29 entry, verified)

`HEAD` = `origin/main` = `074b772ab16cece62cbfca0f2b78497be2ae56d5`, 0/0 · digest A14
`7379218b…1392d` **SAME** · digest W1D11 `98ae5bdf…2704` **SAME** · 22 staged / 102 unstaged /
140 untracked / 0 unmerged · 2104 tracked · 1 worktree / 2 stash. All seven tracked
fingerprints **SAME**. Line 802 `NOT GRANTED`.

### 1.2 The mid-phase event — a concurrent commit and push, not mine

Partway through this phase the repository state changed underneath the task. Established
read-only, and reported rather than repaired:

| Fact | Value |
|---|---|
| New commit | **`ecd23a9e934ec58a7b1f0e5db638da8e865e8187`** |
| Subject / author / date | *"launch phase 1"* · `Narendra2212 <nt3441449@gmail.com>` · Sun Oct 11 15:13:47 2026 +0530 |
| Parent | `074b772ab16cece62cbfca0f2b78497be2ae56d5` — the A29 entry `HEAD` |
| Files in the commit | **3723** |
| Pushed? | **Yes** — `origin/main` == `HEAD`, 0 ahead / 0 behind |
| `.git/index` mtime | 2026-10-11 15:13:47 |

**I issued no git write command of any kind.** No `add`, `commit`, `push`, `reset`, `clean`,
`stash`, `checkout` or branch change. This was the repository owner committing a launch
snapshot under their own identity.

Two artefacts of catching it in flight, recorded for honesty:

- My exit-audit script ran **during** the staging step and captured a transient snapshot —
  "3723 staged, 5705 tracked, both digests CHANGED". That reading was a race against the
  in-progress `git add`, not a measurement of a stable state. The stable post-commit state is
  §1.3.
- An earlier audit invocation died with exit `-1` and mangled output (`phase 1 l^C`) — my
  script contending with `.git/index.lock`. Re-run cleanly afterwards.

**I did not attempt to restore the previous `HEAD` or unstage anything.** Reverting another
session's committed, pushed work would be destructive and was never authorized.

### 1.3 Final (A29 exit, verified)

| Measurement | Value |
|---|---|
| `HEAD` / `origin/main` | **`ecd23a9e934ec58a7b1f0e5db638da8e865e8187`** / identical, 0 ahead / 0 behind |
| branch / worktrees / stash | `main` / 1 / **2** (unchanged) |
| tracked entries | **5705** (was 2104 — the commit now tracks what was untracked) |
| staged / unstaged / untracked / unmerged | **0 / 2 / 6 / 0** |
| deletions anywhere | **0** |

The old index digests (`7379218b…`, `98ae5bdf…`) describe the **pre-commit** index and are now
obsolete by construction. They are not reported as a preservation failure, because the index
legitimately changed owner-side; the preservation question that matters is content loss, and
there is none (§5).

---

## 2. Decisions actually received: **NONE**

`VER` Artifacts created since A28 were scanned. The only new files were a concurrent session's
probe outputs (`p11bd11_*`) and two reports
(`PHASE11B_A28B_CI_EVIDENCE_AND_LEDGER_ROW_VERIFICATION.md`,
`PHASE11B_D11_EVIDENCE_AND_D71_READINESS.md`). **No affirmative decision record appeared.**

Per the brief, a newly authored advisory report is not a decision. Also not treated as a
decision: the `ecd23a9e` commit. A commit is not a decision record — and decisively, **the
committed copy of the ledger still reads `NOT GRANTED` at line 802** `VER`, so the commit
expresses no ledger reconciliation.

---

## 3. W1C-02 scope — **UNRESOLVED**, and the request

Not chosen on the Code owner's behalf. Consequences stated from current source:

| | **Option A — `compare()` only** | **Option B — all three call paths** |
|---|---|---|
| Basis | W1C-02's origin names `verify_schema_drift.py`; ledger §3.6: the change *"touches only `compare()`"*; AP-W1D-3's scope | Ledger `L837`/`L932` scope W1C-02 to *"12 of 16 dimensions"*, and the siblings carry the same defect |
| Implementation left | **None.** `compare()` is already routed through the classifier | Edit `diff_snapshots()` (`verify_provisioning.py:507`) **and** `check_drift()` (`verify_production_schema.py:217`, defect at `:242-243`) |
| Regression tests left | **None after this phase** — test 6 is now delivered (§6) | New tests for two more renderers, each with a *different* cap and label: `compare()` 12 / `MISSING from the database`; `diff_snapshots()` 4 / `only in run 1/2`; `check_drift()` 8 / `MISSING from production` |
| D5 dependency | **None.** W1C-02 would not depend on D5 at all | **Blocked behind D5**, because `check_drift()` is the production-audit tool |
| Status if chosen | Both authoritative criteria (implementation + regression evidence) met → closure becomes a finding-owner decision | Stays OPEN behind D5 |

> **Decision request (Code owner), one line:**
> *"W1C-02's authoritative subject is ⟨ `compare()` only ⟩ / ⟨ `compare()` + `diff_snapshots()` + `check_drift()` ⟩."*

**Live-PostgreSQL is reported as evidence status, not as a criterion.** `VER` (A28 §3.2) the
authoritative criteria are *"implementation and regression evidence"*; `live PostgreSQL` and
`live schema` have **zero** occurrences in the ledger. Live-database verification remains
**NOT DONE** (§7) and that is a confidence gap, not an acceptance criterion.

---

## 4. D5 — unresolved; exact missing decision

Carried forward, not re-searched. Sign-off **ABSENT**, and A28 §3.1 verified the gating role
`production-audit stakeholder` has **zero occurrences** in the authoritative ledger, originating
in W1D-11 §4 as a derived recommendation.

**I did not infer that D5 is satisfied because the role is absent.** `verify_production_schema.py`
is byte-identical at `6e194f43a832d1f9cb0f99efd88dd204c5b3c444` `VER`; no severity policy was
touched; W1C-02 scope was not expanded.

> **Decision request (Code owner) — choose exactly one:**
> 1. ☐ **Identify** an authorized production-audit decision-maker: ____________________
> 2. ☐ **Assume** — *"As Code owner I also hold production-audit authority for `verify_production_schema.py`; D5 sign-off is GIVEN."*
> 3. ☐ **Withdraw** the condition — *"D5's substance is byte-identical preservation, not a policy change, so no production-audit sign-off is required. Any change to the severity policy itself remains a separate decision."*
> 4. ☐ **Defer** — second change stays NOT STARTED; `diff_snapshots()` and `check_drift()` keep the whole-tuple defect.

---

## 5. D-A27-1 — **NOT AUTHORIZED. No ledger diff.**

The three requirements were verified separately; **none is satisfied**:

| # | Requirement | State |
|---|---|---|
| 1 | W1D-12 is authoritative for the `AP-W1D-3` grant | **not affirmed** by any decision record |
| 2 | The person/role authorized to maintain the ledger is explicitly identified | **NOT IDENTIFIED** — the register carries 5 role values, 0 persons |
| 3 | Explicit permission to change line 802 only | **ABSENT** |

**Ledger unchanged** `VER` — `staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md`:

| Line | Status |
|---|---|
| L800 / L801 / **L802** / L803 / L804 | `**NOT GRANTED**` × 5 — **all untouched** |

`L802 edited by A29: False`. The same line is `NOT GRANTED` in the committed copy of the new
`HEAD` too. Not treated as substitutes for permission: the AP-W1D-3 implementation grant, the
inconsistency itself, A28's recommendation, the `Code owner` label, the `ecd23a9e` commit,
commit authorship, or silence.

> **Decision request:** *"W1D-12 is authoritative for AP-W1D-3; ⟨role/person⟩ maintains the
> W1D ledger; update **line 802 only** to `GRANTED (W1D-12)`, leaving 800/801/803/804 untouched."*

---

## 6. Implemented: ledger §3.6 test 6 — the only authorized work available

### 6.1 Authorization basis

| | |
|---|---|
| Grant | **`AP-W1D-3` — GRANTED**, `PHASE11B_W1D12_CLASSIFIER_DECISION_RECORD.md` §1 (Code owner, no conditions) |
| Scope, verbatim | *"Implement the 4-way drift classification **+ the 10 regression tests** (§3.5–3.6)"* |
| File | `tests/test_drift_classification.py` — **one of the four authorized files** (W1D-12 §1.1) |
| Gap being closed | **9 of 10** named tests delivered; test 6 unwritten. W1D-15 §2.3: *"a pure coverage gap — one test, no decision, no approval beyond the existing AP-W1D-3 four-file scope."* |

No new authority was needed or claimed. **No other file was opened for writing.**

### 6.2 What test 6 asserts

Ledger §3.5 (L527-528) states the compatibility invariant that makes the four-way split a safe
drop-in; §3.6 test 6 (L558) names it `test_counts_reduce_to_the_legacy_output` and requires it
*"for every case"*:

```
missing_today = missing + modified
extra_today   = added   + modified
```

Nothing asserted this arithmetic. `test_the_header_counts_match_the_detail` checks the header
agrees with the detail it prints — a different and weaker property. Without test 6 the
classifier could start reporting a different **total** and every existing test would stay green.

### 6.3 The semantics decision, made explicit rather than silent

Phase 4's constraint — *"do not silently change set semantics to multiset semantics"* — is the
crux. The two readings genuinely disagree:

| Reading | Invariant result |
|---|---|
| **Membership / set** (`row not in actual`) | **HOLDS — 12/12** in W1D-15's probe |
| Multiset (`Counter - Counter`) | **BREAKS on 2** duplicate-multiplicity cases |

Membership is the **historical** semantics, and that is anchored in source, not inferred:
`scripts/verify_schema_drift.py`'s `render()` docstring records the replaced behaviour as
*"`compare()` by whole-tuple **membership**, the summary by list equality"* `VER`. The
pre-W1D-13 source is untracked and absent from the object database, so it cannot be diffed —
the reconstruction is anchored on that docstring instead, and the test says so.

So I added a **third test that pins the choice**:
`test_the_legacy_reconstruction_is_membership_not_multiset` proves the two readings diverge on
`[GRANT, GRANT]` vs `[GRANT]` (membership 0/0; multiset 1; classifier routes it to
`duplicated`). A later "tidy-up" of the helper into a `Counter` therefore fails loudly instead
of silently changing semantics.

**One correction to the source material:** the probe's closing note
(`probe_p11b_w1d15_compat_invariant.py`, and its `.txt` footer) still states that test 6
*"needs a scope qualification before it can be written. That qualification is a decision."*
That text is **stale** — W1D-15 §2.3 explicitly retracted it (*"the probe's closing note still
records that anticipation… Under the real legacy semantics no qualification is required"*). The
probe's **data table** is what holds. I did not edit the probe (another session's artifact);
the discrepancy is recorded here so nobody re-derives the retracted conclusion from the footer.

### 6.4 Exact change

| | |
|---|---|
| File | `tests/test_drift_classification.py` — **the only file modified in this phase** |
| Blob before | `fcffbe8e01367aa7f99fffe2677267821fb8424d` (= the version in commit `ecd23a9e`) |
| Blob after | **`99a2917ac0a65761ef3dfe9299ed1df87647552a`** |
| Lines | 665 → **808** (append-only; nothing above line 665 was altered) |
| Status | ` M` — unstaged modification. **Not staged, not committed** |

Added, all at the end of the file:

| Symbol | Line | Kind |
|---|---|---|
| `_legacy_membership_diff()` | 689 | helper, documents the semantics anchor |
| `LEGACY_ARITHMETIC_CASES` | 711 | 14 cases |
| `test_counts_reduce_to_the_legacy_output` | 730 | **ledger test 6**, parametrised ×14 |
| `test_the_legacy_arithmetic_cases_cover_every_classification_category` | 752 | guards the table against covering nothing |
| `test_the_legacy_reconstruction_is_membership_not_multiset` | 772 | pins set-vs-multiset |

**14 cases, not the probe's 12** — I added `null vs empty string` and
`multi-row mixed outcome`. Both pass, so the invariant is now confirmed on a strictly larger
set than was measured. `'production_schema' touched anywhere in target: False` `VER`.

---

## 7. Tests executed — commands, exit codes, counts

Every suite was run **twice**: once before the concurrent commit and once after, because the
baseline moved mid-phase and pre-commit results could not be assumed to carry over.

| # | Command | Exit | Result (pre-commit) | Result (post-commit) |
|---|---|---|---|---|
| 1 | `pytest tests/test_drift_classification.py -q --no-header -p no:cacheprovider` | **0** | **92 passed** in 7.64s | — (folded into #2) |
| 2 | `pytest tests/test_drift_classification.py tests/test_snapshot_dimension_invariants.py -q --no-header -p no:cacheprovider` | **0** | **140 passed** in 5.79s | **140 passed** in 4.65s |
| 3 | `pytest` five-file drift group `-q --no-header -p no:cacheprovider` | **0** | **103 passed, 12 skipped, 1 xfailed** in 32.56s | **103 passed, 12 skipped, 1 xfailed** in 35.05s |
| 4 | `python staging/probe_p11b_w1d05_guard_mutation.py` (**run alone** — it mutates module dicts) | **0** | 12 applied / 12 bit / 0 silent | 12 applied / 12 bit / 0 silent; dicts restored; 16 dims / 16 keys |

**Deltas:** `test_drift_classification.py` 76 → **92** (+16). Combined 124 → **140** (+16). The
five-file group is **unchanged at 103/12/1** — correct, since my change is test-only and that
file is not in the group.

**Environmental limitations, stated:** the **12 skips are PostgreSQL-gated and are not passes** —
each names its reason (`default_transaction_read_only` and SQLSTATE 25006 do not exist in
SQLite). No PostgreSQL, Redis, exchange, CI or network was used. One command failed and is
recorded: the first audit invocation exited `-1` with mangled output, contending with
`.git/index.lock` during the concurrent commit; re-run cleanly.

### 7.1 The four claims, kept separate

| Claim | Status |
|---|---|
| **Implementation** | **COMPLETE** for `compare()` (W1D-13) + ledger test 6 (this phase) |
| **Regression evidence** | **COMPLETE for Option A scope** — 10 of 10 named tests now covered by executed unit evidence, bar test 10 which is PG-gated |
| **Live-database verification** | **NOT DONE. Zero evidence.** Not a criterion (§3); a confidence gap |
| **Production deployment** | **NOT DONE / NOT CERTIFIED.** Nothing deployed, no CI dispatched |

**W1C-02 remains OPEN** — closure depends on the §3 scope answer and is the finding owner's
call, not mine.

---

## 8. Preservation audit

| Check | Result |
|---|---|
| Files I modified | **exactly 1** — `tests/test_drift_classification.py` (authorized) |
| `scripts/verify_provisioning.py` | **SAME** `8f432d99…` |
| `scripts/verify_schema_drift.py` | **SAME** `f853c148…` |
| `tests/test_snapshot_dimension_invariants.py` | **SAME** `5106dd59…` |
| `scripts/verify_production_schema.py` (not authorized) | **SAME** `6e194f43…` |
| W1D ledger / W1D-12 record / readiness scorecard | **SAME** |
| `main.py` / `subscription_engine.py` / `backend/redis_manager.py` | **SAME** |
| Original 22 staged paths | **22 / 22 present in the index** — all preserved through the commit |
| Deletions anywhere | **0** |
| Stash entries | **2** — unchanged |
| Other sessions' in-flight work | preserved — ` M staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` and two new untracked reports left untouched |
| Git write commands issued by me | **NONE** — no add / commit / push / reset / clean / stash / checkout / branch change / index mutation |
| Database / Redis / exchange / CI / deployment / DSN / credential | **none accessed**; `exchange_keys.json`, `.env`, `staging/pg_dsn.txt` all gitignored and **never opened** |

**No content was lost in the concurrent commit.** Staging changes which files git tracks; it
does not alter or remove worktree content. My change sits safely as an unstaged modification
and is **not** staged or committed — consistent with having no commit authorization.

Artifacts created this phase, all under `staging/`:

```
PHASE11B_A29_TEST6_AND_DECISION_REQUESTS.md   this report
p11b29_baseline.py / .txt                     entry baseline
p11b29_audit.py / .txt                        exit audit (captured mid-commit -- see §1.2)
p11b29_indexdiag.py / .txt                    index-change diagnostic
```

---

## 9. Remaining blockers and the highest-value next action

| # | Blocker | Owner role | State |
|---|---|---|---|
| 1 | **W1C-02 scope** (§3) | Code owner | **UNRESOLVED** — free to answer, and it is the pivot |
| 2 | **D5 condition** (§4) | Code owner | **UNRESOLVED** — role undefined in any register |
| 3 | **D-A27-1** ledger authority (§5) | ledger maintainer — **UNIDENTIFIED** | **UNRESOLVED** |
| 4 | `AP-W1D-1` / `-2` / `-4` / `-5` | `DB owner` variants — UNIDENTIFIED | **NOT GRANTED.** No DSN read, no DB connection, no migration run |
| 5 | `D-A25-C1` | Scorecard owner | **UNRESOLVED** — preserved as a request; nothing wired |
| 6 | TENANT-01 / QUOTA-01 | — | **OPEN** — no commit, CI, Redis start or deploy |
| 7 | Redis `A23E6` | — | **separate lifecycle-governance issue.** No classifier/AP-W1D-3 authority extends to Redis |

### Highest-value next action

> **Answer the W1C-02 scope question (§3).** It costs nothing, needs no database, no code and
> no new authorization — and after this phase it is decisive rather than merely clarifying:
> under **Option A** both authoritative criteria are now met, test 6 is delivered, W1C-02 does
> **not** depend on D5, and closure reduces to the finding owner's sign-off. Under **Option B**
> W1C-02 stays blocked behind D5 and the D5 question (§4) becomes the critical path.
>
> One sentence selects between those two worlds.

No decision was manufactured; no gate is labelled CLOSED; nothing is labelled GRANTED,
IMPLEMENTED beyond what was executed, or DEPLOYED. No owner was contacted.
