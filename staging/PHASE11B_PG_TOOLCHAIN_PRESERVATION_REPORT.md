# Phase 11B — PostgreSQL Toolchain Preservation and Ownership-Safe Recovery

> **Primary references for this workstream, cited by exact filename:**
> `staging/PHASE11B_A9_POSTGRES_AUTHORIZATION_READINESS.md` and
> `staging/PHASE11B_A9_PRESERVATION_MANIFEST.txt`.
> **Not** `staging/PHASE11B_A9_D5_FAILURE_DIAGNOSTICS.md`, **not**
> `staging/PHASE11B_A8_READ_ONLY_DIAGNOSTICS.md` — both are different workstreams that
> reuse the A8/A9 labels.

**OUTCOME: NOTHING WAS STAGED OR COMMITTED. Four independent stop conditions fired.**
The preservation is fully prepared and specified; it awaits approvals this task did not
carry and a worktree it cannot safely obtain here.

**Standing status, unchanged: PostgreSQL lane UNEXECUTED · TENANT-01 OPEN · QUOTA-01 OPEN ·
live trading OFF / NOT CERTIFIED.**

Executed 2026-10-10, 18:40–19:0x local.

---

## 1. Verified facts — Phase 0 rebaseline

All read-only. Measured now, not carried over.

| Property | A9 recorded | Now | Moved? |
|---|---|---|---|
| Branch | `main` | `main` | no |
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | **no** |
| `origin/main` | `074b772a` | `074b772a` | no |
| `HEAD...origin/main` | `0 0` | `0 0` | no |
| `git status --porcelain` (normal) | 256 | **256** | no |
| `git status --porcelain -uall` | 3,325 | **3,419** | **+94** |
| Staged (`--cached --name-only`) | 22 | **22** | no |
| Unstaged (`git diff --name-only`) | 100 | **100** | no |
| `.git/index` size | — | **232,081** | same as `PHASE11B_A8_READ_ONLY_DIAGNOSTICS.md` recorded |
| `.git/index` mtime | `10-10 12:51:24` | **`10-10 18:42:14`** | **changed** |
| `.git/index` SHA-256 | `6D965C8E…0458613` | **`3DBF1CB07E58B1E9FFB1A70154F9C008AD539188F45AB2CBE2B367FC1CAF5676`** | **CHANGED** |

### 1.1 The index hash changed — and it is a benign stat refresh, proven not assumed

Phase 0 requires a stop on an index change. It fired, so it was investigated before
anything else.

**The index *file bytes* changed. The *staged content* did not.** Evidence:

| Measurement | Result |
|---|---|
| Index size | **232,081 bytes — unchanged** from the value `PHASE11B_A8_READ_ONLY_DIAGNOSTICS.md` §1 independently recorded |
| Staged path list | **byte-identical** to the 22 paths and statuses recorded in `staging/PHASE11B_A8_ACCELERATION_REPORT.md` and `staging/PHASE11B_A9_POSTGRES_AUTHORIZATION_READINESS.md` — same paths, same `M`/`A` codes, same order |
| `git diff --cached --name-status` digest | `261CFB2BC09D453278F09F728C6BCE2AED4A0C1ED6FD3EB45E2B1EC3419255B2` |
| `git diff --cached --raw` digest (mode + **blob SHAs** + path) | `4B807EF26F24CB3505D623F6B2E2E65C2FF9E572089E90E87CDA1B883A94994F` |
| `git ls-files -s` digest (2,104 tracked entries) | `FA73915562B730051A1F8C4FFE20B0507F72751889A944593495E09CF22E902C` |
| Index mtime | `18:42:14` — **inside this task's own Phase 0 window** |

The rewrite happened while this task ran only `git rev-parse`, `git status` and `git diff`.
Git refreshes the index's cached `stat` data as a side effect of those reads, and this
checkout has `core.autocrlf=true`, so the same batch emitted 36 `LF will be replaced by
CRLF` notices — git re-examining working-tree files is exactly what triggers the refresh.

> **Correction to two prior reports, including this workstream's own.**
> `staging/PHASE11B_A8_ACCELERATION_REPORT.md` and
> `staging/PHASE11B_A9_POSTGRES_AUTHORIZATION_READINESS.md` both present the `.git/index`
> SHA-256 as **the** proof that nothing was staged. **It is not a sound invariant** — a
> read-only `git status` can change it without any staging occurring. The sound invariants
> are the ones above: the staged **path list** and the `--raw` **blob SHAs**, neither of
> which a stat refresh can alter. The prior conclusion ("nothing was staged") still holds
> and is now established on evidence that cannot be falsified this way.
> `staging/PHASE11B_A8_READ_ONLY_DIAGNOSTICS.md` §1 inherits the same weakness.

### 1.2 The 22 staged entries belong to another workstream

Unchanged, and not this task's to touch: `05-security.yml`, `.kiro/security-template.md`,
`ExchangeManager.jsx`, `core/dependencies.py`, `core/tenant_db_context.py`,
`routers/auth.py`, `routers/exchange.py`, ADR-0006, ADR-0007,
`tenant-isolation-matrix.md`, `ALGO22_Quant_Platform_Review_10032026.md`,
`scripts/verify_rls.py`, `scripts/verify_rls_enforcement.py`, `terraform/alb.tf`,
`terraform/cloudtrail.tf`, `terraform/providers.tf`, `terraform/variables.tf`,
`tests/test_sc3_aal_claim_propagation.py`, `tests/test_tenant_db_context.py`,
`tests/test_tenant_db_context_postgres.py`, `tests/test_verify_rls_script.py`,
`tests/test_worker_deployment_coverage.py` — a tenancy/RLS/terraform workstream.

### 1.3 Manifest verification — all 59 digests match

`staging/PHASE11B_A9_PRESERVATION_MANIFEST.txt`, generated **2026-10-10 17:52:05 local**.

| Check | Result |
|---|---|
| Rows parsed | **59 / 59** |
| Files missing | **none** |
| Digest mismatches | **none** |

Scope membership was resolved **from the manifest's recorded paths and hashes**, not
inferred from directory names, as required.

### 1.4 `.gitignore` — conflict re-measured and still open

| Property | Value |
|---|---|
| Status | ` M` — unstaged, another workstream's |
| Diff | **+15 / −0** |
| Working-tree mtime | **`10-06 06:41:54`** — pending for four days |
| In effect now? | **Yes** — `.gitignore` is read from disk, not the index |

Its added block carries preservation-related comments that **name a file list of their
own**: `verify_schema.py`, `rls_inventory.py`, `exposure_check.py`, `fk_typecheck.py`,
`verify_live_trading_off.py`, `run_pg_lane.py`, `try_alembic_head.py`, `bootstrap_pg.py` —
described as *"the re-runnable verifiers the launch readiness report cites… small and worth
keeping"*. Its exclusions are `staging/pgdata*/`, `staging/redis/`, `staging/*.log`,
`staging/pg_dsn.txt`.

**That list overlaps T1 but does not equal it.** It includes `run_pg_lane.py` and
`bootstrap_pg.py` (both T1) and six filenames that are **not** in T1 or anywhere in the
manifest; it omits `dsn.py`, `verify_schema_drift.py`, `provisioning_snapshot.json`,
`run_pg_suite.py` and `paper_pg_client.py` (all T1). So two different preservation
intentions exist for the same directory, authored by two parties. **AP-1 explicitly
requires this be resolved with its owner. It is not resolved.**

### 1.5 Concurrent sessions are active right now

Measured 18:54:33, with `staging/` writes **7 seconds** earlier:

| Time | Path | Workstream |
|---|---|---|
| 18:54:26 | `p11b14_blocking_lane.py` | P11B14 |
| 18:54:23 | `PHASE11B_A8_READ_ONLY_DIAGNOSTICS.md` (grew to 49,892 B) | the *other* A8 |
| 18:54:08 | `PHASE11B_D3_DUP01_RETRY_FIX_REPORT.md` | D3 |
| 18:53:55 | `p11b_w1c_unledgered.txt` | W1C |
| 18:53:37 | `probe_p11b_w1c_unledgered.py` | W1C |
| 18:52:18 | `probe_p11b_w1c_ledger_paradox.py` | W1C |

**`-uall` grew from 3,325 to 3,419 — 94 new untracked paths in roughly one hour.** That
number is the whole argument for AP-0.

### 1.6 T1 / T3 ownership evidence — no concurrent edit to the approved scope

| Tier | Path | Bytes | mtime | Tracked |
|---|---|---:|---|---|
| T1 | `staging/bootstrap_pg.py` | 25,191 | 10-10 12:32:32 | untracked |
| T1 | `staging/dsn.py` | 4,192 | 10-10 13:06:31 | untracked |
| T1 | `scripts/verify_schema_drift.py` | 12,170 | 10-06 13:17:28 | untracked |
| T1 | `staging/provisioning_snapshot.json` | 396,325 | 10-06 20:14:20 | untracked |
| T1 | `staging/run_pg_lane.py` | 4,200 | 10-10 13:06:45 | untracked |
| T1 | `staging/run_pg_suite.py` | 5,799 | 10-10 13:06:48 | untracked |
| T1 | `tests/paper_pg_client.py` | 17,155 | 10-08 21:05:51 | untracked |
| T3 | `tests/test_api03_rate_limit_storage_is_fail_closed.py` | 21,886 | 10-10 16:23:36 | untracked |
| T3 | `tests/test_p11b_a8_collection_integrity.py` | 16,915 | 10-10 16:33:05 | untracked |
| T3 | `staging/PHASE11B_A8_ACCELERATION_REPORT.md` | 33,440 | 10-10 17:06:28 | untracked |

**All ten are untracked. None has been modified since the manifest was generated at
17:52:05.** The three T3 mtimes are the A8 workstream's own final edits. **Ownership of the
whole approved scope is quiet** — the churn in §1.5 is in other paths.

Ownership attribution from the related reports:
`staging/PHASE11B_W1A_VERIFICATION_LANE_PREPARATION.md` (W1A-06) and
`staging/PHASE11B_W1B_BASELINE.md` (W1B-04) assign the T1 toolchain to **Platform** and
escalate its untracked state to P0 with one realised loss attached —
`staging/probe_pg_find.py` and `staging/pg_keepalive_r1.py`, deleted by a filesystem action
on 2026-10-10 and recoverable only from `.pyc`. T3's two test files are **Stream D**
(tests); the report is the A8 workstream's.

### 1.7 A9-01 independently corroborated, and extended

`staging/p11b_w1c_unledgered.txt` (W1C, written 18:53:55) reaches the same result as
A9-01 by its own probe, and goes further:

```
.sql files in the migration set : 66
rows in public.schema_migrations: 56
rows in the snapshot's ledger   : 50
files on disk with NO ledger row: 10

022 … ledger=False snapshot=False  PRESENT  (trigger on library_subscriptions)
023 … ledger=False snapshot=False  PRESENT  (anon has NO write grants on library_strategies)
024 … ledger=False snapshot=False  PRESENT  (process_referral_commission is SECURITY DEFINER)
025 … ledger=False snapshot=False  PRESENT  (table bot_ownership_claims exists)
026-031 … ledger=True snapshot=False PRESENT
032 … ledger=False snapshot=False  absent   (not applied)
```

This confirms, from a different instrument: 022–025 are **applied but unledgered**
(A9-01); 026–031 are **ledgered but post-baseline** (the drift explanation in
`PHASE11B_A9_POSTGRES_AUTHORIZATION_READINESS.md` §4.4); and the `023` / `024` markers it
checked are exactly the two migrations that account for all 7 "missing" drift items.
**New beyond A9:** a migration **032** now exists on disk and is **not applied**, and the
unledgered total is **10**, not 4. A9-01 should be re-scoped to W1C's numbers.
**No database write was made by this task** — the W1C file was read from disk.

---

## 2. Approvals — status, explicitly

Report recommendations were **not** treated as authorization, as instructed.

| # | Approval | Required for | Status | Basis |
|---|---|---|---|---|
| **AP-0** | Explicit-path staging only; never `git add -A` / `.` | all staging | **ENFORCED** (self-enforceable discipline, not an external grant) | §1.5 — 3,419 untracked paths, +94 in an hour |
| **AP-1** | Approval for T1 **and** resolution of the `.gitignore` ownership conflict with its owner | staging T1 | **NOT GRANTED** | the task directs me to *obtain* it, and does not grant it; the `.gitignore` conflict (§1.4) is unresolved and its owner is not reachable from here |
| **AP-3** | Approval from the relevant test owner **and** coordinator for T3 | staging T3 | **NOT GRANTED** | no test-owner or coordinator approval exists in any artifact or in the task |
| — | Explicit authorization to commit | committing | **NOT GRANTED** | the task conditions the commit on a separate explicit authorization |

**Both AP-1 and AP-3 are outstanding, so Phase 2's precondition — "only if all approvals
are explicit" — is not met.**

---

## 3. Actions performed

| Action | Count | Detail |
|---|---:|---|
| Read-only git inspections | ~12 | `rev-parse`, `status`, `diff --cached`, `ls-files`, `check-ignore`, `diff --numstat` |
| Read-only file/digest checks | 59 + 10 | manifest verification; T1/T3 sizes and mtimes |
| Reports read | 6 | the two A9 primaries, `PHASE11B_A8_READ_ONLY_DIAGNOSTICS.md`, `PHASE11B_A13_REAL_REDIS_VERIFICATION.md`, `PHASE11B_W1B_BASELINE.md`, `p11b_w1c_unledgered.txt` |
| **Files staged** | **0** | — |
| **Commits created** | **0** | — |
| **Branches created** | **0** | — |
| Scratch files created then deleted | 3 | `staging/_a9b_staged.txt`, `_a9b_raw.txt`, `_a9b_lsfiles.txt` — digests recorded in §1.1; deleted because two held another workstream's index metadata |
| Files written | 1 | this report |

**Not run, at any point:** `git add` (any form), `commit`, `push`, `reset`, `clean`,
`stash`, `restore`, `checkout`, `switch`, `worktree`, any PR or CI operation, the
PostgreSQL lane, `verify_provisioning.py`, `provision_database.py`, any migration, any
database write, any Redis start, any `.gitignore` edit. `staging/provisioning_snapshot.json`
and `staging/PHASE11B_A9_PRESERVATION_MANIFEST.txt` were **read and not rewritten** —
their digests in §1.3 are the proof.

---

## 4. Why Phase 2 did not execute — four independent stop conditions

Any one of these is sufficient. All four hold.

**① Approvals AP-1 and AP-3 are absent.** §2. The task's own instruction for this case is
to do the read-only analysis and prepare the staging plan without staging or committing.

**② The shared index holds 22 unrelated staged entries.** §1.2. The task is explicit:
*"If the shared index contains unrelated staged entries, do not try to clean it up or
silently include it in a commit. Stop and use an isolated worktree or coordinate with the
index owner."* An explicit-pathspec `git commit -- <paths>` would not capture those 22, but
the clause does not condition on that, and the index is not this workstream's to work
around.

**③ A branch operation in this shared checkout would disrupt active sessions.**
`git switch -c` moves `HEAD` for the **entire checkout**. At least five workstreams — the
other A8, D3, W1C, P11B14, and the D5′ PR sessions — are writing right now (§1.5) and every
one of them has recorded `HEAD = 074b772a` on `main` as its baseline;
`PHASE11B_A8_READ_ONLY_DIAGNOSTICS.md` and `PHASE11B_A12_D5_MINIMAL_REPAIR_REPORT.md` both
re-verify it at exit. Moving `HEAD` under them would invalidate their baselines mid-run.
Phase 2 item 1 forbids exactly this and directs me to **stop and request an isolated
worktree or owner coordination**. I am requesting it rather than creating one, because the
instruction says request, and because a worktree would still not supply ① or ②.

**④ The index changed during Phase 0.** §1.1. Investigated and shown benign — staged
content provably unchanged — so this one is **documented and cleared** rather than
blocking. It is listed because Phase 0 requires it to be reported.

---

## 5. The exact staging plan, ready to run once ① ② ③ are satisfied

Prerequisites: AP-1 (incl. the `.gitignore` reconciliation), AP-3, a separate commit
authorization, and **an isolated worktree** so neither `HEAD` nor the shared index is
touched.

### Step 0 — re-verify, because none of §1 may still hold

```powershell
python -c "import hashlib,pathlib,re; m=pathlib.Path('staging/PHASE11B_A9_PRESERVATION_MANIFEST.txt').read_text(encoding='utf-8'); rows=re.findall(r'^  (\S+)\s+(?:TRACKED )?\s*\d+\s+([0-9a-f]{64})$', m, re.M); bad=[p for p,h in rows if not pathlib.Path(p).exists() or hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()!=h]; print('rows',len(rows)); print('CHANGED/MISSING:', bad or 'none')"
git diff --cached --raw          # MUST still digest to 4B807EF2…A94994F (22 entries)
```

**Abort if** the row count is not 59, anything is changed or missing, or the staged `--raw`
digest differs — a differing staged digest means real staging activity by another session,
not a stat refresh.

### Step 1 — isolated worktree (do NOT `switch` in the shared checkout)

```powershell
git worktree add ../aerora-preserve-pg -b chore/preserve-pg-verification-toolchain 074b772a
```

A linked worktree gets its **own** `HEAD` and **own** index, so the shared checkout's
`HEAD`, its index, and the 22 staged entries are all untouched. Note the T1/T3 files are
untracked, so they exist **only** in the shared working tree and must be copied into the
new worktree before staging — copy exactly these ten paths, nothing else, and re-verify
their digests against the manifest after copying.

### Step 2 — stage by explicit path only (AP-0). Ten paths, two commands.

```powershell
# T1 — lane core (7)
git add -- staging/bootstrap_pg.py
git add -- staging/dsn.py
git add -- scripts/verify_schema_drift.py
git add -- staging/provisioning_snapshot.json
git add -- staging/run_pg_lane.py
git add -- staging/run_pg_suite.py
git add -- tests/paper_pg_client.py

# T3 — A8 closure artifacts (3)
git add -- tests/test_api03_rate_limit_storage_is_fail_closed.py
git add -- tests/test_p11b_a8_collection_integrity.py
git add -- staging/PHASE11B_A8_ACCELERATION_REPORT.md
```

**Never** `git add -A`, `git add .`, or a directory argument. Note
`staging/provisioning_snapshot.json` is **not** covered by the pending `.gitignore` block,
so no `-f` is needed — and if a future `.gitignore` revision does cover it, **do not force
it**; return to AP-1.

### Step 3 — inspect before committing

```powershell
git diff --cached --name-status      # MUST be exactly the 10 paths above, all 'A'
git diff --cached --stat             # MUST show 10 files, 0 deletions
git status --short                   # confirm no unexpected staged entry
git diff --cached --numstat | Measure-Object   # MUST be 10
```

**Abort if** the staged set is not exactly those ten `A` entries.

### Step 4 — commit, only with explicit authorization, with an explicit pathspec

```powershell
git commit -m "Preserve the PostgreSQL verification toolchain and the A8 closure artifacts" -- `
  staging/bootstrap_pg.py staging/dsn.py scripts/verify_schema_drift.py `
  staging/provisioning_snapshot.json staging/run_pg_lane.py staging/run_pg_suite.py `
  tests/paper_pg_client.py tests/test_api03_rate_limit_storage_is_fail_closed.py `
  tests/test_p11b_a8_collection_integrity.py staging/PHASE11B_A8_ACCELERATION_REPORT.md

git show --stat --name-status HEAD   # verify the commit CONTENTS, not the exit code
git log --oneline -1
```

### Step 5 — verify the result, then release the worktree

Re-run the Step 0 manifest check against the committed blobs; confirm the shared checkout's
`HEAD` is still `074b772a` and its staged `--raw` digest is still `4B807EF2…`; then
`git worktree remove ../aerora-preserve-pg` once the branch is no longer needed locally.
**No push, no PR** — both are outside this task's authorization.

### Deliberately excluded from every step

**T2** (`provision_database.py`, `verify_provisioning.py`) · **T4** (7 probes) ·
**T5** (29 PostgreSQL-gated tests — and never as a block) · **T6** (11 untracked
migrations) · `.gitignore` · `staging/pg_dsn.txt` · any `staging/*.log` or `*.txt` log ·
`staging/PHASE11B_A9_PRESERVATION_MANIFEST.txt` · `tests/conftest.py` · `pytest.ini`.

---

## 6. Phase 3 verification — reported against what actually happened

| Required item | Result |
|---|---|
| Branch and commit SHA | **none — no branch created, no commit created** |
| Files staged | **none** |
| Files committed | **none** |
| Digests vs the approved manifest | **59 / 59 match**; 0 missing, 0 mismatched (§1.3) |
| Before/after index hash | `6D965C8E…` → **`3DBF1CB0…`**; size identical at 232,081; **staged content provably unchanged** (§1.1) |
| Before/after staged entries | **22 → 22**, byte-identical path list; `--raw` blob-SHA digest `4B807EF2…A94994F` |
| Unrelated tracked paths changed | **none** — unstaged count 100, unchanged |
| Unrelated untracked paths changed | **none by this task.** Repository-wide, `-uall` moved 3,325 → 3,419 (+94) from other sessions (§1.5) |
| Tests or checks actually run | **no pytest run.** Checks were: 59-row manifest digest verification (pass), 10-file T1/T3 size+mtime capture, 4 git-state digests, `git check-ignore` on the scope, `.gitignore` numstat. **No test suite, no PostgreSQL lane, no database query** |
| Approvals still missing | **AP-1** (T1 + `.gitignore` owner) and **AP-3** (T3 test owner + coordinator); plus a separate commit authorization |
| Ownership conflicts unresolved | the `.gitignore` preservation list vs T1 (§1.4); the shared 22-entry index (§1.2); 5+ live sessions sharing `HEAD` (§1.5) |

Success is **not** claimed from clean exit codes: no state-changing command was run, and
the git state was inspected directly to establish that.

---

## 7. Unresolved items

| # | Item | Owner | Blocks |
|---|---|---|---|
| 1 | **AP-1** — T1 approval **and** reconciliation of the pending `.gitignore` preservation list with T1 | Platform + the `.gitignore` diff owner | staging T1 |
| 2 | **AP-3** — T3 approval from the test owner and coordinator | Stream D + Coordinator | staging T3 |
| 3 | **An isolated worktree, or coordination to make a branch operation safe** in this shared checkout | Coordinator | all staging |
| 4 | Explicit authorization to commit | Coordinator | the commit |
| 5 | **W1B-04 remains OPEN and is the reason this task exists** — 59 files / 1,506,272 bytes untracked, one realised loss already today | Coordinator | — |
| 6 | **A9-01 should be re-scoped to W1C's measurement**: 10 unledgered files (not 4), and migration `032` present on disk but unapplied | DB owner | — |
| 7 | **The `.git/index` SHA-256 is cited as a safety invariant in three reports and is not one** — `PHASE11B_A8_ACCELERATION_REPORT.md`, `PHASE11B_A9_POSTGRES_AUTHORIZATION_READINESS.md`, `PHASE11B_A8_READ_ONLY_DIAGNOSTICS.md`. Future baselines should use the staged `--raw` blob-SHA digest | all report authors | report accuracy |
| 8 | Phase-label collision (A9-07) — two `PHASE11B_A8_*` and two `PHASE11B_A9_*` reports from different workstreams; cite by filename only | Coordinator | — |

**Highest-value next action: grant AP-1 and AP-3 and provide an isolated worktree.** The
plan in §5 is complete and re-verifiable; what is missing is authorization and a safe place
to execute, not analysis. The exposure it closes is a P0 that has already materialised once
in this repository today.

---

Live trading OFF / NOT CERTIFIED · PostgreSQL lane **UNEXECUTED** · real-Redis lane
**UNEXECUTED** · no PostgreSQL verification was run by this task · no database was written ·
no production target contacted · no remote operation performed · TENANT-01 **OPEN** ·
QUOTA-01 **OPEN** · W1B-01 OPEN · W1B-04 OPEN · nothing staged · nothing committed
