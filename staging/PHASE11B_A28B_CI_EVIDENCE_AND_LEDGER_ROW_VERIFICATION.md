# Phase 11B-A28B — CI evidence chain, ledger-row verification, and Phase 4 supplement

**OUTCOME: D-A27-1 NOT AUTHORIZED. The ledger was not edited by me. Line 802 is unchanged.**

> ## ⚠ The repository state changed under this report, twice. Read §9 first.
>
> At **15:13:47**, while this phase was running, the repository owner committed **3,723 files** as
> `ecd23a9e` *"launch phase 1"* and **pushed to `origin/main`**. The entire database-verification
> toolchain is now **tracked, committed and pushed**.
>
> **This supersedes §3.1, §3.2 and §4's risk framing, and largely satisfies my own `D-A28B-1`.**
> Those sections are retained as measured at the time, each marked with a supersession pointer, so
> the before/after is auditable. **§9 is authoritative for the current state.**
>
> The ledger is now committed too — **with line 802 still reading `NOT GRANTED`**. `D-A27-1` is
> unaffected and remains unauthorized.

**This is a Phase 4 evidence supplement, not a second ledger-gate report.** A concurrent session
wrote `staging/PHASE11B_A28_LEDGER_GATE_DELTA.md` at **14:54:02** (12,603 B, blob
`05206e8e73b0eab7ebc1040120be58a80f8f37c9`), which discharges Phases 0–3 and reaches the same
outcome. **I did not overwrite it and I do not restate it.** Everything below is evidence that
report does not contain.

**READ-ONLY** on every source file, test, migration, workflow, register, ledger and existing
report. No database, Redis, exchange, DSN, credential or deployment access. No CI dispatch. No
stage, commit, push, reset, clean, stash, checkout or branch change.

| | |
|---|---|
| Live trading | **OFF / NOT CERTIFIED** |
| PostgreSQL integration | **UNVERIFIED** — now with a mechanical explanation, §3 |
| TENANT-01 / QUOTA-01 | **OPEN / OPEN** |
| Registers or ledgers edited | **0** |
| Approvals created or inferred | **0** |
| Tests executed this phase | **140 passed** — warranted: a concurrent session edited a test file mid-phase (§7.3) |
| Concurrent events detected | **mass-stage of ~3,700 files** (§7.2) and **test 6 implemented** (§7.3) — neither by me |

---

## 1. Authorization artifact and exact scope relied upon

**None exists.** All three authorizations D-A27-1 requires were checked and **zero are held**:

| # | Required | Present? | Basis |
|---|---|---|---|
| 1 | Treat W1D-12 as authoritative for the AP-W1D-3 grant | **NO** | The A28 brief asks me to *establish whether* it was provided; it does not provide it |
| 2 | Identify the authorized **ledger maintainer** | **NO** | Not named in the brief, and **not derivable anywhere in the repository** — §2 extends this negative beyond `staging/` for the first time |
| 3 | Explicit permission to change line 802 | **NO** | Phase 2 is conditional — *"**If** the necessary authority and explicit edit permission are present"*. A detailed conditional description of an edit is not a grant |

**Not treated as permission**, per the brief's own exclusion list: the prior AP-W1D-3
implementation grant; the existence of the discrepancy; the A27 §2.5 / A28 §6 recommendations; my
own W1D-15 report; the `Code owner` role label; commit authorship; or silence.

Ledger state verified at entry and exit: blob `7e238620bc22f0ad246f0ebc373b7032c6b28ef0`, clean,
mtime **10-10 19:44:22** — unmodified since before A27. Line 802 verbatim:

```
| **AP-W1D-3** | Implement the 4-way drift classification + the 10 regression tests (§3.5–3.6) | Code owner | **NOT GRANTED** |
```

**Operational note for whoever is eventually authorized.** The ledger is 56,397 B of UTF-8
containing en-dashes and `§` signs (reading it through a cp1252 console renders `022â€“025` and
`Â§2.7`). A one-line edit performed by a tool that rewrites the file wholesale risks re-encoding
all 940 lines. **The edit must be byte-surgical on line 802 only.** No prior report flags this.

---

## 2. No ledger maintainer exists anywhere in the repository — negative extended

Prior sessions established this for `staging/` registers and for `CODEOWNERS`. I checked the
places nobody had:

| Location | Result |
|---|---|
| `.kiro/steering/` | **ABSENT — the directory does not exist.** No steering rule assigns ownership or writer authority |
| `.kiro/` contents | **only `specs/`** — no governance, owners or authority configuration |
| `CODEOWNERS` / `OWNERS` / `MAINTAINERS` tracked anywhere | **none** |
| Tracked files matching `GOVERNANCE` | **3, all unrelated** — `ML_TRAINING_GOVERNANCE_IMPLEMENTATION_REPORT.md`, `backend_app/migrations/019_training_governance.sql`, `tests/test_ml_training_governance.py`. These are an **ML-training product feature**, not repository governance |
| `.devin/skills/` | 1 file, `sync-api-layer/SKILL.md` — API-layer task guidance, no authority content |

**Requirement 2 of D-A27-1 is therefore not merely unanswered in the brief — it is unanswerable
from the repository.** The authority gap is structural, not an oversight in one register.

---

## 3. The CI evidence chain for PostgreSQL was broken at two independent layers

> **SUPERSEDED at 15:13:47 by commit `ecd23a9e` — see §9.** Everything in §3 was true when measured
> and explains why no PostgreSQL evidence exists *to date*. Both layers it identifies have now been
> repaired by the owner's commit. §3 is retained because it diagnoses why the gate was never
> satisfiable, and because §9's forward-looking consequences only make sense against it.

This was the substantive new finding of the phase. It converts "PostgreSQL UNVERIFIED" from a policy
status into a **mechanically explained** one.

### 3.1 The drift checker is referenced by CI — but only in an uncommitted workflow

`VER` `.github/workflows/01-pr-check.yml:638`, in the working tree:

```yaml
      - name: Fail on schema drift from the committed baseline
        run: python scripts/verify_schema_drift.py --dsn "$PROVISION_DATABASE_URL"
```

That step sits in a job `provisioning-and-isolation` (`:565`, display name *"Provisioning,
Privilege Hardening & Tenant Isolation"*), which also runs `scripts/provision_database.py` twice,
`staging/run_all_probes.py`, and a **12-file PostgreSQL isolation lane**.

| Measurement | Result |
|---|---|
| Working-tree `01-pr-check.yml` | ` M` (modified, uncommitted) — contains `provisioning-and-isolation` **and** `verify_schema_drift` |
| **HEAD's `01-pr-check.yml`** | **634 lines, 7 jobs — contains NEITHER** `provisioning-and-isolation` **nor** `verify_schema_drift` |
| *"Provisioning, Privilege Hardening & Tenant Isolation"* in the last **5** `01-pr-check` runs | **NOT PRESENT in any of them** (7–8 jobs each; runs `38061973739`, `38047483583`, `38046625040`, `38044138718`, `38043513862`) |

**Conclusion: CI has never invoked `verify_schema_drift.py`.** The job that would is uncommitted.

**This retires a risk and corrects my own framing.** In W1D-15 I noted the toolchain is untracked;
on finding the CI reference I treated the first classifier change as having CI blast radius. It
has **none today** — the step does not exist in any ref CI can check out. The first change remains
local-only in effect.

### 3.2 Every file that lane would execute is absent from **all** refs

Not just absent from `main` — absent from all 15 remote branches and all local heads
(`git for-each-ref refs/remotes refs/heads`, then `git ls-tree` per ref):

```
scripts/provision_database.py            -> ABSENT FROM ALL REFS
scripts/verify_schema_drift.py           -> ABSENT FROM ALL REFS
tests/test_tenant_isolation_rls_postgres.py -> ABSENT FROM ALL REFS
```

And the full 12-file lane: **12 of 12 NOT in HEAD** —
`test_tenant_isolation_rls_postgres`, `test_cross_tenant_read_sweep_postgres`,
`test_billing_data_isolation_postgres`, `test_tenant_referential_integrity_postgres`,
`test_pgcrypto_preflight_postgres`, `test_signup_integration_postgres`,
`test_position_lifecycle_postgres`, `test_referral_money_lifecycle_postgres`,
`test_orm_models_match_provisioned_schema`, `test_bug049_identity_types_postgres`,
`test_schema_drift_checker_is_read_only_postgres`, `test_production_security_probe_rolls_back_postgres`.

**So even if the workflow were committed, the job would fail at its first step.** Two independent
layers must be fixed before any of this evidence can exist.

**The gap is selective, not a directory-wide pattern** — which makes it a defect rather than a
convention:

| Directory | Tracked | Untracked `.py` |
|---|---|---|
| `scripts/` | 174 files | **8** — `provision_database`, `verify_schema_drift`, `verify_provisioning`, `verify_production_schema`, `verify_production_security`, `verify_exchange_connectivity`, `bug049_enumerate`, `owner_columns` |
| `tests/` | 462 files | **96** |
| `staging/` | **0** | 1,798 |

This is W1C's `W1B-04` (P2, *"toolchain untracked"*) with its blast radius now measured: the
untracked set is precisely the **database-verification toolchain**.

### 3.3 What PostgreSQL evidence CI *has* produced — narrow, but real

I checked rather than assuming the whole lane was dead. HEAD's `database-tests` job
(*"Database Tests (PostgreSQL)"*) reported **success** in run `38061973739`, and it runs:

```
pytest tests/test_database_isolation_level_postgres.py \
       tests/test_strategy_lifecycle_concurrency_postgres.py
```

`VER` **Both files are in HEAD.** So genuine PostgreSQL evidence exists in CI for exactly these
two concerns: transaction isolation level, and strategy-lifecycle concurrency.

**It does not extend to tenant RLS, cross-tenant reads, schema drift, privilege hardening, or ORM
conformance** — those live in the 12-file lane that has never run. TENANT-01's RLS evidence in
particular is **not** produced by any CI run.

### 3.4 A correction to an inference I made mid-phase

I briefly reasoned that the read-only drift suite would skip even inside CI, because
`tests/test_schema_drift_checker_is_read_only_postgres.py:40` gates the whole module on
`AERORA_TEST_DATABASE_URL` while the drift step uses `PROVISION_DATABASE_URL`. **That was wrong:**
`AERORA_TEST_DATABASE_URL` **is** set at `01-pr-check.yml:592`, in the same job block as
`PROVISION_DATABASE_URL:593`. The suite would run if the job ran. The obstacle is §3.1–3.2, not
the env var.

---

## 4. New: line 802 is the only demonstrably stale ledger row

A27 observed the register is *"demonstrably stale in at least one place"*. That raised a real
question nobody tested — **are the other four rows stale too?** If several were, a one-line edit
would be the wrong remedy. I tested the two that are checkable without a database:

| Row | Request | Physical evidence | Verdict |
|---|---|---|---|
| **L803 · AP-W1D-4** | Add the `_provenance` envelope and recapture the snapshot | `staging/provisioning_snapshot.json`: **16 top-level keys, `_provenance` = False**, mtime **10-06 20:14:20** unchanged, 396,325 B | **`NOT GRANTED` is ACCURATE** — not performed |
| **L804 · AP-W1D-5** | Install `pgserver`; create the isolated cluster and the `aerora_app` role | `pgserver_present=False`; `staging/pgdata_w1c_iso` **ABSENT** (`pgdata_r1` and `pgdata` present, both pre-existing) | **`NOT GRANTED` is ACCURATE** — not performed |
| L800 · AP-W1D-1 | Read-only completeness predicates for 022–025 | requires DB access — **not checkable**, and not attempted | **UNVERIFIABLE here** |
| L801 · AP-W1D-2 | Insert 4 ledger rows | requires DB access — **not checkable** | **UNVERIFIABLE here** |
| **L802 · AP-W1D-3** | 4-way classification + 10 regression tests | implementation present and passing (W1D-13; re-verified by A26, A27, W1D-15) | **STALE** |

**Result: of the three rows whose state can be established from the filesystem, exactly one —
802 — is stale, and the other two are correct.** That is positive support for the narrow one-line
remedy D-A27-1 proposes, and it means no broader reconciliation is indicated by current evidence.

---

## 5. Phase 4 — remaining workstreams, kept separate

Verified fact `VER` · prior-report claim `REC` · inference `INF` · unresolved `?`

| Workstream | State | New evidence this phase |
|---|---|---|
| **D5 / W1C-02** | **OPEN; sign-off ABSENT** | **Ledger §3.6 test 6 is now implemented and green (§7.3): 9 of 10 named regression tests covered and executed; only test 10 remains, and it is unexecutable in CI (§3.1–3.3).** Plus §3.1's retirement of the CI-blast-radius concern. `verify_production_schema.py` byte-identical at `6e194f43a832d1f9cb0f99efd88dd204c5b3c444`; `diff_snapshots()` and the three-tier severity routing **not touched** `VER`. Also `VER`: **no CI workflow in any ref invokes `verify_production_schema.py` or `verify_provisioning.py`** — so the D5-gated second change has no CI consumer, which lowers its blast radius but changes no criterion |
| **AP-W1D-1** | **NOT GRANTED** | §4 confirms the sibling rows 803/804 are accurate, so the register is reliable here. Target-database identity still unresolved `REC`. **No DSN read, no connection, no migration work** |
| **TENANT-01** | **OPEN** | `VER` **New:** its RLS evidence lane (`test_tenant_isolation_rls_postgres.py` + 3 further tenant tests) is absent from all refs and sits in an uncommitted job, so **no CI run has ever produced tenant-isolation evidence** (§3.2–3.3). Mechanically corroborates OPEN |
| **QUOTA-01** | **OPEN** | No commit, no CI dispatch, no Redis start, no deploy. D10 §7.5's isolation note stands `REC`: the only slot-guard call site is in `routers/exchange.py`, one of the 22 staged entries belonging to another workstream |
| **D-A25-C1** | **UNRESOLVED — preserved as a decision request** | `VER` **New:** `PHASE11A_READINESS_SCORECARD.json` has **no acceptance-criteria field**. Its 11 top-level keys are `phase, generated_at_note, principle, status_vocabulary, weights, implementation_completeness, evidence_backed_launch_readiness, live_track, production_track, why_no_single_overall_percentage, unknowns_kept_unknown`. `recon_operational_for_paper` carries only `weight 0.10`, `status OPEN` and an evidence string. **So C1 cannot be answered from the artifact — a verified negative, not an inference.** Bearing-but-not-deciding: the sibling key `exchange_reconciliation_service_WIRED_AND_RUNNING` is `NOT_STARTED` (*"main.py lifespan does not start ExchangeReconciliationService; no leader election; no exchange client wired"*), which is consistent with the venue reading A26 §6.1 labels Option 1. **I am not deciding it.** Nothing wired; no service started; live trading untouched |
| **Redis A23E6** | **separate unresolved governance issue** | Its blank response form remains unexercised. **No classifier grant authorizes any Redis lifecycle edit.** `backend_app/backend/redis_manager.py` not read for modification and not touched |

**Cross-cutting, now corroborated by five independent sessions** (A23E6 §5, A25, A26, W1D-15 §1.4,
A28 §3.4): decision **preparation** has an established precedent; decision **delivery** has no
documented channel. That, not any single approval, is why these workstreams are simultaneously
parked.

---

## 6. Commands actually run, and their real outcomes

All read-only. Outcomes as measured, including the two that corrected me.

| Command | Real outcome |
|---|---|
| `git rev-parse HEAD` / `--abbrev-ref` / `rev-list --left-right --count` | `074b772ab16cece62cbfca0f2b78497be2ae56d5`, `main`, `0 0` |
| `git status --porcelain` tallies; `git ls-files` | 22 staged / 102 unstaged / 140 untracked (collapsed) / 0 unmerged / 2104 tracked |
| `Get-Content` ledger L798–806 | L802 verbatim `NOT GRANTED` (§1) |
| `git hash-object` on 5 governance records | ledger `7e238620…`, W1D-12 `1402bdc5…`, A27 `58deaba9…` — all unchanged |
| `Get-ChildItem staging -Filter *.md` newer than 14:40 | 3 — W1D-15 (14:45), **D10 (14:51)**, **A28 (14:54)** |
| Read `PHASE11B_D10_DECISION_INTAKE_AND_REMEDIATION_READINESS.md` | **Not a decision record.** D-series watchdog/credential workstream; 0 decisions; does not touch D-A27-1, AP-W1D-3 or the ledger |
| Read `PHASE11B_A28_LEDGER_GATE_DELTA.md` | Concurrent session; same outcome; independently **confirmed** both W1D-15 claims |
| `.kiro/steering`, `.kiro`, CODEOWNERS/GOVERNANCE, `.devin` scan | **no ownership or authority artifact** (§2) |
| `ConvertFrom-Json` on `provisioning_snapshot.json` | 16 keys, `_provenance` **False** (§4) |
| `importlib.util.find_spec('pgserver')` | `pgserver_present=False`. **First attempt failed** — the PowerShell wrapper stripped the nested quotes, producing `NameError: name 'pgserver' is not defined`; rerun with single-quoted inner string |
| `Select-String` workflows for the drift scripts | 1 hit — `01-pr-check.yml:638` (§3.1) |
| `git show HEAD:.github/workflows/01-pr-check.yml` | **634 lines, 7 jobs, no `provisioning-and-isolation`, no `verify_schema_drift`** (§3.1) |
| `git ls-tree` / `for-each-ref` across 15 remote + local refs | 4 verification scripts and **12 of 12** lane tests **absent from all refs** (§3.2) |
| `gh run list --workflow=01-pr-check.yml --limit 5` | **all 5 `conclusion: failure`** (the failing job is `unit-tests`) |
| `gh run view <id> --json jobs` ×5 | *"Provisioning, Privilege Hardening & Tenant Isolation"* **NOT PRESENT** in any run. `database-tests` **success** |
| `git ls-tree HEAD` on the 2 CI PostgreSQL files | **both in HEAD** — so `database-tests`' success is real evidence, for 2 files only (§3.3) |
| `Select-String` workflows for `AERORA_TEST_DATABASE_URL` | set at `:592` — **my skip-gating inference was wrong** (§3.4) |
| `Get-ChildItem .git/objects -Recurse -File` ×5 samples | **198 → 1200 → 1642 → 2874 → 3009**; `+135` in 5 s with no write command of mine — **concurrent writer identified (§7.1)** |
| `Get-ChildItem … -Filter 'tmp_obj_*'`; `FETCH_HEAD`/`ORIG_HEAD` mtimes; `git reflog` | temp objects at 15:09:01; no fetch; reflog head unchanged — rules out fetch/commit/reset (§7.1) |
| One interrupted command | A 3-sample loop with two 6 s sleeps was cancelled before returning; rerun as a 2-sample loop with an explicit timeout. Recorded for completeness |

**Tests: two focused runs, both warranted, both recorded in §7.3** — `140 passed in 12.01s` and
`16 passed, 76 deselected in 4.32s`. I began this phase intending to run none, on the correct
grounds that the governance discrepancy is not a defect tests can resolve and that the suites would
re-measure byte-identical inputs. **That premise was falsified mid-phase:** a concurrent session
modified `tests/test_drift_classification.py`, so the inputs were no longer identical and the
inherited 124-test result no longer described the file on disk. Re-measuring was then evidence, not
activity. No other suite was run; nothing PostgreSQL-, Redis-, network- or venue-dependent was
touched.

**Artifacts created: 1** — this report. New, untracked, under `staging/`, destination verified
ABSENT immediately before writing.

---

## 7. Preservation

| Check | Entry | Exit |
|---|---|---|
| `HEAD` / branch / divergence | `074b772a…` / `main` / `0 0` | identical |
| staged / unstaged / unmerged / tracked | 22 / 102 / 0 / 2104 | **3723 / 2 / 0 / 5705 — concurrent mass-stage, §7.2** |
| Digest A (sha256 raw `git ls-files -s`) | `98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704` | **`da62907c…07ec` — changed by §7.2, not by me** |
| `.git/objects` file count | 198 | **3009+ and climbing — concurrent writer, §7.1** |
| worktrees / stash | 1 / 2 | identical |
| `git reflog -1` | `074b772a HEAD@{0}` | identical — **no commit, reset or checkout by anyone** |
| **Ledger** | `7e238620…`, clean | **identical, clean** |
| **W1D-12 decision record** | `1402bdc5…`, clean | **identical, clean** |
| Lines 800 / 801 / 802 / 803 / 804 | all `NOT GRANTED` | **all unchanged** |
| Concurrent `A28_LEDGER_GATE_DELTA.md` | `05206e8e…` | **untouched — not overwritten** |

**No edit was made, so there is no diff.** No commit was created and none is claimed. Ledger
reconciliation, had it occurred, would confer **no** implementation, database, CI, deployment or
trading permission — and it did not occur.

### 7.1 Correction — the shared object database grew during this phase, and not by me

My first exit measurement reported `.git/objects` unchanged at 198. **That was wrong**, and the
error is recorded rather than quietly fixed. Measured progression during this phase:

```
198  (entry, carried from W1D-15)
1200 (first exit measurement)
1642 -> 2874 -> 3009   (three later samples)
+135 objects in 5 seconds, with no git write command issued by me between samples
```

`VER` Diagnosis, not inference:

- Transient **`tmp_obj_*`** files were present (`71/tmp_obj_tu1oge`, `07/tmp_obj_6hwcIX`, mtime
  15:09:01) — the signature of a process actively **writing** objects.
- **Not a fetch:** `.git/FETCH_HEAD` mtime is `10-10 18:55:31`, `.git/ORIG_HEAD` is `10-05`.
- **Not a commit, reset or checkout:** `git reflog` top entry is unchanged at
  `074b772a HEAD@{0}`, dated `2026-10-05 23:40:19`.
- **Not me:** every `git` call I made was a read — `rev-parse`, `status`, `ls-files`, `ls-tree`,
  `show`, `for-each-ref`, `reflog`, `hash-object` **without `-w`** (which computes a digest and
  writes nothing). The count kept climbing while I was idle between samples.

**Attribution:** a concurrent session is writing loose objects to the shared database. This is the
same hazard `A17` recorded when it found 4 objects written by another session; the rate here is
roughly **27 objects/second**.

**Impact on this phase: none to content.** Loose-object writes are additive and touch neither the
working tree nor the index, and every content invariant was re-verified **after** the growth was
detected: `HEAD`, branch, divergence `0 0`, `22 / 102 / 0 / 2104`, Digest A
`98ae5bdf…2704`, and the ledger / W1D-12 / concurrent-A28 blobs all identical.

**Consequence for future reports: `.git/objects` file count is not a sound preservation invariant
in this repository** while concurrent sessions are active. The reflog head and per-file blob
fingerprints are. Digest A proved sound as a *detector* — §7.2 is what it detected.

### 7.2 A concurrent session mass-staged ~3,700 files during this phase

`VER` The object writes in §7.1 were a `git add` at scale. Measured:

| Measurement | This phase, entry | This phase, exit | By me? |
|---|---|---|---|
| staged entries | 22 | **3723** | **No** |
| unstaged | 102 | **2** | **No** |
| tracked (`git ls-files`) | 2104 | **5705** | **No** |
| Digest A | `98ae5bdf…2704` | `da62907c…07ec` | **No** |
| `HEAD` / reflog head | `074b772a…` | **identical** | — |

**I issued no `git add`, `stage`, `commit`, `reset` or `stash`.** Every git call I made is listed
in §6 and all are reads. The index is now a different object from the one every prior report in
this workstream fingerprinted as "22 staged".

**What it staged, relevant here** (`A ` = added to index, `M ` = modified-in-index):

```
A   scripts/verify_provisioning.py          A   tests/test_snapshot_dimension_invariants.py
A   scripts/verify_schema_drift.py          AM  tests/test_drift_classification.py
A   scripts/verify_production_schema.py     A   tests/test_schema_drift_checker_is_read_only_postgres.py
A   scripts/provision_database.py           A   tests/test_tenant_isolation_rls_postgres.py
M   .github/workflows/01-pr-check.yml       A   staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md
```

**This substantially pre-empts my own `D-A28B-1` (§8)** — the database-verification toolchain and
the `provisioning-and-isolation` workflow are now in the index. **Three qualifications:**

1. **Nothing is committed.** `git ls-tree HEAD` returns **0** for all of them and the reflog head
   is unchanged, so §3.2's "absent from all refs" **still holds**. An index is not a ref; the
   `git clean -fd` exposure is reduced, not eliminated, and a `git reset` would restore it.
2. **It staged the authoritative ledger itself** (`A staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md`).
   Content is **unaffected** — blob `7e238620…`, L802 still `NOT GRANTED` — but the governance
   record is now queued for commit by a session whose authority to do so is exactly the
   unresolved `D-A27-1` question.
3. **It was done without any identified authority**, which is the same gap this phase is reporting.

**I did not revert, unstage or alter any of it.** Doing so would destroy another session's work —
the failure mode `A15` committed when it overwrote a concurrent report.

### 7.3 W1D-13's work: four files intact, one modified — and the modification closes a real gap

`VER` Worktree versus index, measured per path:

| Path | Index blob | Worktree blob | State |
|---|---|---|---|
| `scripts/verify_provisioning.py` | `8f432d99…` | `8f432d99…` | **intact** |
| `scripts/verify_schema_drift.py` | `f853c148…` | `f853c148…` | **intact** |
| `scripts/verify_production_schema.py` | `6e194f43…` | `6e194f43…` | **intact, still untouched** |
| `tests/test_snapshot_dimension_invariants.py` | `5106dd59…` | `5106dd59…` | **intact** |
| **`tests/test_drift_classification.py`** | `fcffbe8e…` (W1D-13's) | **`99a2917a…`** | **MODIFIED in worktree** |

The modification is **+143 lines, 0 deletions**, appended after `:665`, adding four symbols:

```
_legacy_membership_diff(expected, actual)
test_counts_reduce_to_the_legacy_output(label, dimension, expected, actual)
test_the_legacy_arithmetic_cases_cover_every_classification_category()
test_the_legacy_reconstruction_is_membership_not_multiset()
```

**This is ledger §3.6 test 6 — the single unwritten W1C-02 criterion I reported in W1D-15 §2.3 —
now implemented by a concurrent session.** It independently adopted the *membership* (set)
reconstruction of the legacy diff, which is the distinction my probe established as the correct
one, and pinned it with an explicit test.

**Because the file changed, W1D-15's evidence-reuse justification is void for it, so I re-ran it.**
That is a warranted focused run, not activity:

| Command | Real outcome |
|---|---|
| `pytest tests/test_drift_classification.py tests/test_snapshot_dimension_invariants.py -q --no-header -p no:cacheprovider` | **140 passed in 12.01s** (was 124 — **+16**) |
| `pytest tests/test_drift_classification.py -k legacy -v` | **16 passed, 76 deselected in 4.32s** — `test_counts_reduce_to_the_legacy_output` across **14** parametrised cases (my 12 plus *null vs empty string* and *multi-row mixed outcome*), plus both coverage/semantics guards |

**Consequence for the W1C-02 acceptance matrix:** criterion **test 6 moves from GAP to COVERED
with executed unit evidence.** Of the ledger's 10 named regression tests, **9 are now covered and
executed**, and only **test 10 remains unexecuted** — gated behind a PostgreSQL DSN, in a lane that
§3.1–3.3 shows CI has never run. **I am not recording a W1C-02 closure:** the `Code owner` scope
question (`compare()` only, or all three call paths) is still open, and closure is not mine to
declare.

Confirmed not done: source/test/migration/workflow edit; register, ledger or scorecard edit;
report overwrite; dependency change; stage, commit, push, reset, clean, stash, checkout, branch
change; index mutation; CI trigger or dispatch; Redis start; database, exchange or payment call;
DSN or credential read; deployment.

---

## 8. Remaining blockers and the exact next human decision

The decision request is **unchanged** from A28 §6 and is not restated. Two additions from this
phase:

1. **Requirement 2 of D-A27-1 is unanswerable from the repository** (§2), so the reply must
   *create* the maintainer role rather than cite one.
2. **A new, separate, and genuinely independent request** — it needs no governance resolution and
   blocks a different gate:

> **D-A28B-1 — commit the database-verification toolchain.** The `provisioning-and-isolation` CI
> job exists only in an uncommitted `01-pr-check.yml`, and the 4 verification scripts plus **12 of
> 12** PostgreSQL-lane test files are **absent from every git ref** (§3.1–3.2). Consequences: CI
> has never run schema-drift or tenant-isolation verification; **TENANT-01 has no CI evidence**;
> and the entire W1C-02 implementation plus its 124 tests would be destroyed irrecoverably by
> `git clean -fd`, which `W1B-04` records as having already happened once.
>
> **Required:** name who may commit `scripts/`, `tests/` and `.github/workflows/`, and authorize
> tracking the verification toolchain. **This is orthogonal to D5 and to D-A27-1** — it grants no
> classifier, database, deployment or trading permission.
>
> **RESOLVED at 15:13:47 — withdrawn.** The repository owner committed and pushed the toolchain as
> `ecd23a9e` (§9). Tracking is done; the `git clean -fd` exposure is gone. **`D-A28B-1` no longer
> needs an answer.** What replaces it is narrower and is stated in §9.4.

**Highest-value free answer remains the W1C-02 scope question** (`compare()` only, or all three
call paths): it costs nothing and determines whether D5 gates W1C-02 at all.

No approval was inferred from this brief's framing, from the W1D-12 implementation grant, from
passing tests, from the concurrent A28 report, from role labels, from commit authorship, or from
silence. No owner was contacted.

---

## 9. Authoritative current state — the owner's commit at 15:13:47

**This section supersedes §3.1, §3.2, §4's risk framing, §7.2's "nothing is committed", and
`D-A28B-1`.** It was measured after the event, not inferred.

### 9.1 What happened

`VER` `git log -1`:

| Field | Value |
|---|---|
| Commit | **`ecd23a9e934ec58a7b1f0e5db638da8e865e8187`** |
| Author | **`Narendra2212 <nt3441449@gmail.com>`** — the repository owner, the single author identity across all prior history |
| Date | **Sun Oct 11 15:13:47 2026 +0530** |
| Subject | **`launch phase 1`** |
| Files changed | **3,723** |
| `origin/main` | **`ecd23a9e…` — identical to `HEAD`, so it was pushed** |

**I did not stage, commit or push any of it.** My git calls were reads only (§6). The §7.1 object
growth and §7.2 mass-stage were the preparation for this commit.

### 9.2 State before and after

| Measurement | Phase entry | Now |
|---|---|---|
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | **`ecd23a9e934ec58a7b1f0e5db638da8e865e8187`** |
| `origin/main` / divergence | `074b772a…` / `0 0` | **`ecd23a9e…` / `0 0` (pushed)** |
| staged / unstaged / untracked / tracked | 22 / 102 / 140 / **2104** | **0 / 2 / 11 / 5705** |
| 4 verification scripts + `run_all_probes.py` in HEAD | **0 of 5** | **5 of 5** |
| 12-file CI PostgreSQL lane in HEAD | **0 of 12** | **12 of 12** |
| `.github/workflows/01-pr-check.yml` (`provisioning-and-isolation`) | ` M`, absent from HEAD | **committed** |
| Ledger | untracked, `7e238620…` | **committed, `7e238620…` — content identical** |
| **Ledger line 802** | `NOT GRANTED` | **`NOT GRANTED` — unchanged** |

### 9.3 What this resolves

1. **The untracked-toolchain risk is eliminated.** W1D-15 §4.2 and §3.2 above recorded that the
   W1C-02 implementation and its tests existed only in the working tree, destroyable by
   `git clean -fd` with no git recovery path, and that `W1B-04` records that loss already occurring
   once. **They are now in committed, pushed history.** This was the largest preventable risk in
   the workstream and it is closed.
2. **`D-A28B-1` is satisfied** — by the owner's own action, which is the authority that was missing.
3. **CI can now execute the PostgreSQL lane.** The workflow job and every file it invokes are in
   HEAD, and `AERORA_TEST_DATABASE_URL` is set at `01-pr-check.yml:592` (§3.4).

### 9.4 What it does *not* resolve, and one new consequence

| Item | State |
|---|---|
| **`D-A27-1` / ledger line 802** | **UNCHANGED and still unauthorized.** The ledger is now *committed* with `AP-W1D-3` reading `NOT GRANTED` — so the stale row is now in permanent history. Committing a record is not deciding its content |
| **D5 sign-off** | **ABSENT.** Unaffected. `verify_production_schema.py` committed byte-identical at `6e194f43…`; `check_drift()`, `diff_snapshots()` and the three-tier severity routing **not modified** |
| **W1C-02 scope question** | **OPEN** — still the `Code owner` question, still free to answer |
| **PostgreSQL integration** | **UNVERIFIED.** The lane is now *runnable*, but **no run exists yet.** Runnable is not verified |
| **TENANT-01 / QUOTA-01** | **OPEN / OPEN** |
| **Live trading** | **OFF / NOT CERTIFIED** |

**New consequence — my §3.1 retirement of the CI-blast-radius concern is itself now reversed.**
Before the commit, `verify_schema_drift.py` had no CI consumer, so W1D-13's modified `compare()`
could not affect CI. **It now can:** the next pull request will run
`python scripts/verify_schema_drift.py --dsn "$PROVISION_DATABASE_URL"` against the committed,
classifier-routed `compare()`, plus the 12-file isolation lane and the read-only suite. Three
things follow, stated as expectations to be measured rather than claims:

- Ledger §3.6 **test 10's evidence becomes obtainable for the first time** — the read-only suite's
  module-level gate will be satisfied in CI.
- The drift step is a **hard gate** (*"Fail on schema drift from the committed baseline"*) running
  against `staging/provisioning_snapshot.json`, which `W1C-03` records as **stale since 10-06 with
  no `_provenance`** (§4). A stale baseline plus a hard gate is a plausible CI failure that would
  be a **baseline** problem, not a classifier problem. Worth anticipating rather than discovering.
- `unit-tests` was failing on all five prior runs, and every job `needs: static-audit` only, so
  `provisioning-and-isolation` will now attempt to run regardless of that failure.

**I did not dispatch CI, and I make no claim about what the next run will produce.**

### 9.5 Preservation, restated against the new baseline

| Check | Result |
|---|---|
| Changes made by me to source, tests, migrations, workflows, registers or the ledger | **0** |
| Ledger blob / line 802 | `7e238620…` / `NOT GRANTED` — **unchanged throughout** |
| `W1D-12` decision record | `1402bdc5…` — **unchanged** |
| Concurrent `PHASE11B_A28_LEDGER_GATE_DELTA.md` | `05206e8e…` — **untouched, not overwritten** |
| Concurrent edit to `tests/test_drift_classification.py` | **preserved as found** — not reverted, not unstaged (§7.3) |
| Files I created | **1** — this report, untracked |
| Stage / commit / push / reset / clean / stash / checkout / branch change by me | **none** |
| Database / Redis / exchange / CI dispatch / DSN / credential / deployment | **none** |

**The commit and push were the owner's actions, not mine, and I neither authorized nor executed
them.** I report them because they changed the baseline every other section was measured against.

### 9.6 The next human decision, unchanged in substance

The commit moved the *engineering* blockers; it moved **no governance blocker**. One reply from the
`Code owner` still resolves three items at once:

1. **Ledger authority** — *"W1D-12 is authoritative for AP-W1D-3; ⟨role/person⟩ maintains the
   ledger; update **line 802 only** to `GRANTED (W1D-12)`."* Now with an added reason: the stale row
   is in pushed history.
2. **W1C-02 scope** — *"W1C-02's subject is ⟨`compare()` only⟩ / ⟨all three call paths⟩."* **Still
   the highest-value free answer.** With test 6 now green (§7.3), the `compare()` reading leaves
   **9 of 10** criteria covered and only the CI-obtainable test 10 outstanding.
3. **D5's condition** — identify / assume / **withdraw** / defer, given that the gating role appears
   in **no** authoritative register.
