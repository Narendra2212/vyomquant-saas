# Phase 11B — Preservation Coordination and Safe Recovery Plan

> **Cited by exact filename.** Primary: `staging/PHASE11B_PG_TOOLCHAIN_PRESERVATION_REPORT.md`.
> Sources: `staging/PHASE11B_A9_POSTGRES_AUTHORIZATION_READINESS.md`,
> `staging/PHASE11B_A9_PRESERVATION_MANIFEST.txt`,
> `staging/PHASE11B_A8_ACCELERATION_REPORT.md`,
> `staging/PHASE11B_A8_READ_ONLY_DIAGNOSTICS.md`,
> `staging/p11b_w1c_unledgered.txt`, `staging/probe_p11b_w1c_unledgered.py`,
> `staging/PHASE11B_W1A_VERIFICATION_LANE_PREPARATION.md`,
> `staging/PHASE11B_W1B_BASELINE.md`, `POST_LAUNCH_IMPROVEMENTS.md`.
> `PHASE11B_A9_D5_FAILURE_DIAGNOSTICS.md` and `PHASE11B_A8_READ_ONLY_DIAGNOSTICS.md` are
> **different workstreams** that reuse the A8/A9 labels.

## Required final status

| | |
|---|---|
| **Preservation staging** | **BLOCKED pending approvals** |
| **PostgreSQL lane** | **UNEXECUTED** |
| **TENANT-01** | **OPEN** |
| **QUOTA-01** | **OPEN** |
| **Live trading** | **OFF / NOT CERTIFIED** |

**Nothing was staged. No commit, branch, or worktree was created. No database was
contacted. No remote operation occurred.** The toolchain is **not** committed and the
database findings are **not** resolved.

Executed 2026-10-10, ~19:05–19:2x local.

---

## 1. Fresh baseline and staged-content digest

| Property | Prior report | **Now** | Moved? |
|---|---|---|---|
| Branch | `main` | `main` | no |
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | **no** |
| `origin/main` | `074b772a` | `074b772a` | no |
| `HEAD...origin/main` | `0 0` | `0 0` | no |
| Worktrees | 1 | **1** — `C:/aerora_quant_backend_updated_final1 074b772a [main]` | **no linked worktree exists** |
| Staged entries | 22 | **22** | no |
| Unstaged | 100 | **100** | no |
| `--porcelain` (normal) | 256 | **256** | no |
| `--porcelain -uall` | 3,419 | **3,442** | **+23** |

### 1.1 Staged content is unchanged, established two ways

Per the instruction, the raw `.git/index` SHA-256 is **not** used as the safety invariant.

| Digest | Method | Value | Comparable? |
|---|---|---|---|
| **Continuity digest** | `git diff --cached --raw` → `Out-File -Encoding utf8` → `Get-FileHash` (the exact method `PHASE11B_PG_TOOLCHAIN_PRESERVATION_REPORT.md` §1.1 used) | **`4B807EF26F24CB3505D623F6B2E2E65C2FF9E572089E90E87CDA1B883A94994F`** | **identical to the prior report** |
| **Canonical digest** | sha256 over `git diff --cached --raw` stdout bytes, CRLF→LF, trailing whitespace stripped, blank lines dropped, LF-joined | **`2e994018e375f318f4e6dc9a67ab248e4622de8c426146ca756d5f47d2767152`** (22 entries) | new — tool-independent, for future baselines |

The continuity digest reproducing byte-for-byte proves the 22 staged entries' **paths,
modes and blob SHAs** are unchanged. The canonical digest is introduced because the
`Out-File` method embeds PowerShell's encoding and line endings; future rebaselines should
use the canonical form. **Both are recorded so neither is orphaned.**

### 1.2 The 22 staged entries remain another workstream's

Unchanged tenancy/RLS/terraform set: `05-security.yml`, `.kiro/security-template.md`,
`ExchangeManager.jsx`, `core/dependencies.py`, `core/tenant_db_context.py`,
`routers/auth.py`, `routers/exchange.py`, ADR-0006, ADR-0007,
`tenant-isolation-matrix.md`, `ALGO22_Quant_Platform_Review_10032026.md`,
`scripts/verify_rls.py`, `scripts/verify_rls_enforcement.py`, `terraform/{alb,cloudtrail,providers,variables}.tf`,
`tests/test_sc3_aal_claim_propagation.py`, `tests/test_tenant_db_context{,_postgres}.py`,
`tests/test_verify_rls_script.py`, `tests/test_worker_deployment_coverage.py`.

---

## 2. Manifest verification

`staging/PHASE11B_A9_PRESERVATION_MANIFEST.txt`, generated **2026-10-10 17:52:05 local**.

| Check | Result |
|---|---|
| Rows parsed against recorded paths + hashes | **59 / 59** |
| Files missing | **none** |
| Digest mismatches | **none** |
| Scope resolution | from the manifest's recorded paths — **not** inferred from directory names |

The ten proposed T1/T3 paths are all present, all **untracked**, and none has been modified
since the manifest was generated. `staging/provisioning_snapshot.json` still digests to
`8e3057e0e86dd113…` — **read, never rewritten.**

---

## 3. Ownership and approval matrix

| # | Approval | Scope | Owner (evidence) | Status |
|---|---|---|---|---|
| **AP-0** | Explicit-path staging only | all staging | self-enforceable discipline | **ENFORCED** — not an external grant |
| **AP-1a** | Approval for the 7 T1 files | `staging/` ×4, `scripts/` ×1, `tests/` ×1, snapshot ×1 | **Platform** — `PHASE11B_W1A_VERIFICATION_LANE_PREPARATION.md` W1A-06 and `PHASE11B_W1B_BASELINE.md` W1B-04 both assign the toolchain to Platform and rate its untracked state P0 | **NOT GRANTED** |
| **AP-1b** | Resolution of the conflicting `.gitignore` preservation lists | `.gitignore` | **the `POST_LAUNCH_IMPROVEMENTS.md` item-12 author** — see §4 | **NOT GRANTED / UNRESOLVED** |
| **AP-3** | Approval for the 3 T3 artifacts | `tests/` ×2, report ×1 | **Stream D** (tests, per the §7.1 ownership map in `PHASE11_LIVE_TRADING_READINESS_AUDIT.md`) **+ Coordinator** (the report) | **NOT GRANTED** |
| **AP-E** | Isolated worktree, or a coordinated alternative | execution environment | **Coordinator** | **NOT GRANTED** |
| **AP-C** | Separate authorization to commit | the commit | **Coordinator** | **NOT GRANTED** |
| **AP-DB** | Assign migration 032 and the unledgered migrations to the DB owner **for investigation only** | database findings | **DB owner** | **NOT GRANTED** — recorded as a request in §5.5 |

**No approval was inferred from a report recommendation, from silence, or from an approval
for a different tier.** An approval mechanism was neither invented nor exercised: this
environment provides no channel to Platform, Stream D, the Coordinator, the DB owner, or
the `.gitignore` author. **No request was delivered.** §5.5 is the request text, ready for
whoever can route it.

---

## 4. The `.gitignore` conflict and the recommended owner decision

### 4.1 Current state, re-measured

| Property | Value |
|---|---|
| Status | ` M` — unstaged |
| Diff | **+15 / −0** |
| In effect now? | **Yes** — `.gitignore` is read from disk, not the index |
| Last **committed** change | `6c5660a1`, Narendra2212, 2026-10-05 — predates the pending edit, so it attributes nothing |

Added exclusions: `staging/pgdata*/`, `staging/redis/`, `staging/*.log`,
`staging/pg_dsn.txt`.

### 4.2 Attribution — supported by repository evidence

The pending comment ends: *"POST_LAUNCH_IMPROVEMENTS.md item 12: anything here worth
running regularly belongs in scripts/ and in CI rather than in a directory named staging."*

`POST_LAUNCH_IMPROVEMENTS.md` **item 12** (line 484, *"Remove the verification scaffolding
when it stops earning its keep"*) contains the same list and the same closing argument:
*"Several are worth keeping as runnable evidence and are cited in the launch report:
`verify_schema.py`, `rls_inventory.py`, `exposure_check.py`, `fk_typecheck.py`,
`verify_live_trading_off.py`, `bootstrap_pg.py` …"* and *"If any of the keepers are worth
running regularly, they belong in `scripts/` and in CI rather than in a directory named
`staging`."*

**Attribution: the pending `.gitignore` edit derives from `POST_LAUNCH_IMPROVEMENTS.md`
item 12 and belongs to that document's author/workstream (the post-launch-improvements /
launch-readiness audit).** This is a content linkage, not a commit author — the edit is
uncommitted, so no author is recorded. It is the strongest attribution the repository
supports.

### 4.3 The two lists, measured against each other

All eight filenames named in the comment **exist on disk**:

| File named in `.gitignore` / item 12 | On disk | In T1? |
|---|---|---|
| `staging/verify_schema.py` | yes | **no** |
| `staging/rls_inventory.py` | yes | **no** |
| `staging/exposure_check.py` | yes | **no** |
| `staging/fk_typecheck.py` | yes | **no** |
| `staging/verify_live_trading_off.py` | yes | **no** |
| `staging/try_alembic_head.py` | yes | **no** |
| `staging/run_pg_lane.py` | yes | **yes** |
| `staging/bootstrap_pg.py` | yes | **yes** |

| T1 path | In the item-12 list? |
|---|---|
| `staging/bootstrap_pg.py` | **yes** |
| `staging/run_pg_lane.py` | **yes** |
| `staging/dsn.py` | no |
| `scripts/verify_schema_drift.py` | no |
| `staging/provisioning_snapshot.json` | no |
| `staging/run_pg_suite.py` | no |
| `tests/paper_pg_client.py` | no |

**Overlap 2. Item-12-only 6. T1-only 5.** Two parties have written two different
preservation intentions for the same directory, and neither is a superset.

### 4.4 Recommended owner decision — for the owner, not taken here

1. **Treat the two lists as answering different questions and keep both.** Item 12 asks
   *"which probes are worth keeping as runnable evidence?"*; T1 asks *"what is the minimum
   set without which the PostgreSQL lane cannot run at all?"* The 5 T1-only paths are
   dependency-closure (`dsn.py` resolves the DSN, `verify_schema_drift.py` is the drift
   checker, `provisioning_snapshot.json` is its baseline, `run_pg_suite.py` is the runner,
   `paper_pg_client.py` is the PG test client — dependency graph in
   `PHASE11B_A9_POSTGRES_AUTHORIZATION_READINESS.md` §2.1). Dropping any of them leaves
   the lane unrunnable, so **the union, not either list, is the defensible keep-set.**
2. **The exclusions are incomplete and that is the live risk.** Only `staging/*.log` is
   excluded, while nearly every log in `staging/` is `.txt` — including a 16.2 MB
   `p23_measure_*.txt` and two 2.5 MB collection logs. `-uall` is **3,442** and rose by
   **23 during this task alone**. Recommend adding `staging/*.txt` with named exceptions,
   or an allowlist (`staging/*` ignored, keepers force-added).
3. **Decide whether `provisioning_snapshot.json` is a keeper or an artifact.** It is 396 KB
   of generated output, and it is **also** the only baseline `verify_schema_drift.py` has.
   T1 proposes committing it; item 12's logic would arguably exclude it. **This is the one
   genuine disagreement and it needs an explicit ruling** — committing a generated baseline
   is how a stale baseline becomes authoritative, which is exactly the condition
   `PHASE11B_A9_POSTGRES_AUTHORIZATION_READINESS.md` §4.4 documents.
4. **Do not change `.gitignore` as part of the preservation commit.** Keep the two changes
   separable so either can be reverted alone.

---

## 5. Migration findings — repository evidence separated from live-database claims

**No database connection was made by this task.** Every live-catalogue statement below is
attributed to `staging/p11b_w1c_unledgered.txt` (W1C, read from disk) and is **not
re-verified here**.

### 5.1 Confirmed from repository source

| Claim | Verification | Result |
|---|---|---|
| Migrations 022–032 exist | `backend_app/migrations/` glob | **CONFIRMED** — all 11 present, all untracked |
| `032_referral_payouts_terminal_evidence.sql` exists on disk | glob | **CONFIRMED** |
| 023 removes the 6 `library_strategies` items | `023_remediate_privilege_escalation.sql:101` `REVOKE INSERT, UPDATE … FROM authenticated`; `:121` `DROP POLICY IF EXISTS "Users can update their own strategies"`; `:138` `REVOKE INSERT, UPDATE, DELETE … FROM anon` | **CONFIRMED** — accounts for 5 privileges + 1 policy = 6 of the 7 drift items |
| 024 makes `process_referral_commission` SECURITY DEFINER | `024_remediate_referral_double_commission.sql:167` `SECURITY DEFINER` | **CONFIRMED** — the snapshot's function dimension selects `p.prosecdef`, so this is the 7th drift item |
| The baseline ledger stops at 021 | `provisioning_snapshot.json` — 50 entries, highest `migrations/021_strategy_deployment_live_uniqueness.sql` | **CONFIRMED** |
| Ledger key form | `provision_database.py:462` `name = f"{path.parent.name}/{path.name}"` | **CONFIRMED** — see §5.4 |
| Ledger DDL and insert | `:154` `filename text PRIMARY KEY`; `:480-482` `INSERT … ON CONFLICT (filename) DO NOTHING`; `:465-471` refuses a file whose SHA-256 changed since it was applied | **CONFIRMED** |

### 5.2 Live-database claims — W1C's measurement, not re-verified here

| Claim (source: `staging/p11b_w1c_unledgered.txt`) | Status |
|---|---|
| `public.schema_migrations` holds **56** rows | **W1C-measured; not re-verified** |
| 022, 023, 024, 025 — ledger `False`, snapshot `False`, **effect PRESENT** | **W1C-measured; not re-verified** |
| 026–031 — ledger `True`, snapshot `False`, effect PRESENT | **W1C-measured; not re-verified** |
| 032 — ledger `False`, snapshot `False`, **effect absent** (i.e. not applied) | **W1C-measured; not re-verified** |
| Markers used: `anon` has no write grants on `library_strategies`; `process_referral_commission` is `prosecdef = true` | **W1C-measured.** The repository statements that would produce both are confirmed in §5.1 |

### 5.3 Correction — the "10 unledgered files" figure, and my own prior error

W1C's probe defines its set as **distinct `.sql` basenames across three roots,
recursively**:

```python
ROOTS = [REPO/"migrations", REPO/"backend_app"/"migrations", REPO/"archived_migrations"]
for p in sorted(root.rglob("*.sql")): on_disk.setdefault(p.name, …)
```

Reproduced exactly: `migrations/` **13** + `backend_app/migrations/` **38** +
`archived_migrations/` **15** = **66**, with **zero** basename collisions. W1C's 66 is
correct for its own definition.

**But that is not the set `provision_database.py` applies.** The provisioner uses
`backend_app/migrations/*.sql` **non-recursive** less `020` (**37**) plus `migrations/*.sql`
**non-recursive** (**11**) = **48** in Step 1, plus `020` as step 0/3, the
`archived_migrations/root_migrations/*.sql` (**12**, less `ALEMBIC_OWNED`) and the two
`migrations/hardening/*.sql` via named constants. The 66-set therefore **includes files the
provisioner never applies** — the 2 recursive-only `migrations/` files and 3 of the 15
`archived_migrations/` files outside `root_migrations/`.

**Consequence: "10 files with no ledger row" is not 10 applied-but-unledgered migrations.**
It is on-disk basenames minus ledger keys, and it counts never-applied files. W1C's own
output is careful about this — it reports `effect` per file, and **only 022, 023, 024, 025
are `PRESENT` with no ledger row.** `032` is unledgered *and* unapplied, which W1C
explicitly calls fine.

> **Correction to `staging/PHASE11B_PG_TOOLCHAIN_PRESERVATION_REPORT.md` §7 item 6.** That
> report says *"A9-01 should be re-scoped to W1C's measurement: 10 unledgered files (not
> 4)."* **That is wrong — it conflated two different metrics.** A9-01's figure of **4
> applied-but-unledgered migrations (022–025) was correct**, and W1C's data confirms it
> rather than replacing it. The 10 answers a broader question (ledger completeness over all
> on-disk migration basenames) and should be cited as such. The genuinely new facts from
> W1C are migration **032** (on disk, unapplied) and the ledger-completeness framing — not
> a larger applied-but-unledgered count.

### 5.4 An imprecision in W1C's conclusion, and a latent hazard it exposes

W1C concludes: *"`schema_migrations_pkey` is PRIMARY KEY (filename) and the runner inserts
with ON CONFLICT (filename) DO NOTHING, so a re-run cannot correct this by itself."*

The PK and the `ON CONFLICT` clause are **confirmed** (§5.1). The inference does not follow:
for 022–025 there is **no row at all**, so nothing conflicts. `_apply_sql` checks
`if name in already` — absent, so the provisioner would **re-execute the file's SQL and
then insert the ledger row**. A re-run *would* backfill the ledger; the obstacle is that
backfilling costs a **re-execution of DDL/DML against a live database**, which is a
different and larger objection. **Flagged for the DB owner as an imprecision, not an
error in the finding itself** — the finding (schema ahead of its own ledger) stands.

**Latent hazard found while checking this.** `name = f"{path.parent.name}/{path.name}"`
uses only the **parent directory name**, so `backend_app/migrations/X.sql` and
`migrations/X.sql` both ledger as `migrations/X.sql`. This explains why the live ledger
records `migrations/026_fee_records_row_level_security.sql` for a file that lives at
`backend_app/migrations/026_…` (observed in
`PHASE11B_A9_POSTGRES_AUTHORIZATION_READINESS.md` §4.4). **Two different files sharing a
basename across the two trees would collide on one ledger key**, and the second would
either be skipped as "already applied" or trigger the checksum refusal at `:466`. W1C's
basename dedupe found **zero** collisions today, so this is **latent, not live** — but it
is a real defect in the ledger key and belongs with the DB owner.

### 5.5 Approval request text — not delivered

> **To Platform (AP-1a):** approve committing these 7 untracked files, digests per
> `staging/PHASE11B_A9_PRESERVATION_MANIFEST.txt`: `staging/bootstrap_pg.py`,
> `staging/dsn.py`, `scripts/verify_schema_drift.py`,
> `staging/provisioning_snapshot.json`, `staging/run_pg_lane.py`,
> `staging/run_pg_suite.py`, `tests/paper_pg_client.py`. Rationale: W1A-06 / W1B-04, P0,
> with one realised loss already (`staging/probe_pg_find.py` and
> `staging/pg_keepalive_r1.py`, deleted 2026-10-10, recoverable only from `.pyc`).
>
> **To the `POST_LAUNCH_IMPROVEMENTS.md` item-12 author (AP-1b):** rule on §4.4 — in
> particular whether `staging/provisioning_snapshot.json` is a keeper or a generated
> artifact, and whether the keep-set is the union of your list and T1.
>
> **To Stream D and the Coordinator (AP-3):** approve committing
> `tests/test_api03_rate_limit_storage_is_fail_closed.py`,
> `tests/test_p11b_a8_collection_integrity.py` and
> `staging/PHASE11B_A8_ACCELERATION_REPORT.md` — the P11-H15 closures from
> `staging/PHASE11B_A8_ACCELERATION_REPORT.md`, currently existing only as untracked files.
>
> **To the Coordinator (AP-E, AP-C):** provide an isolated worktree (or coordinate a
> window), and authorize the commit in §6 separately.
>
> **To the DB owner (AP-DB) — investigation only, no database change requested:** 022–025
> applied without ledger rows (W1C); 032 on disk and unapplied; the ledger key collapses
> `backend_app/migrations/` and `migrations/` to one namespace (§5.4); the drift baseline
> predates 022–031 (`PHASE11B_A9_POSTGRES_AUTHORIZATION_READINESS.md` §4.4).

---

## 6. Validated but unexecuted isolated-worktree plan

Reviewed against the current checkout. **Not run. No worktree or branch was created.**

### 6.1 Review of the prior procedure — three corrections

The procedure in `staging/PHASE11B_PG_TOOLCHAIN_PRESERVATION_REPORT.md` §5 is sound in
shape and needs three fixes before it is run:

1. **The copy step must be explicit about *which* digest is re-checked.** All ten paths are
   untracked, so they exist only in the shared working tree. After copying into the
   worktree, re-verify against the manifest **inside the worktree**, not in the shared
   checkout — otherwise the check passes on the source and says nothing about the copy.
2. **`git worktree add -b <branch> <commit>` creates a branch ref in the shared
   repository.** That is a shared-repo metadata write. It does **not** move the shared
   checkout's `HEAD` or index — which is the property that matters — but it is not a
   no-op and belongs in the approval, not assumed under it.
3. **The continuity digest must be re-measured with the stated method.** `4B807EF2…` is
   method-dependent (§1.1). Use the canonical digest `2e994018…` for new baselines, or
   reproduce the `Out-File -Encoding utf8` form exactly.

### 6.2 The plan

**Step 0 — re-verify; none of §1/§2 may still hold.**

```powershell
python -c "import hashlib,pathlib,re; m=pathlib.Path('staging/PHASE11B_A9_PRESERVATION_MANIFEST.txt').read_text(encoding='utf-8'); rows=re.findall(r'^  (\S+)\s+(?:TRACKED )?\s*\d+\s+([0-9a-f]{64})$', m, re.M); bad=[p for p,h in rows if not pathlib.Path(p).exists() or hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()!=h]; print('rows',len(rows),'CHANGED/MISSING:', bad or 'none')"
python -c "import hashlib,subprocess; raw=subprocess.run(['git','diff','--cached','--raw'],capture_output=True).stdout; L=[l.rstrip() for l in raw.replace(b'\r\n',b'\n').split(b'\n') if l.strip()]; print(len(L),'entries', hashlib.sha256(b'\n'.join(L)).hexdigest())"
```

**Abort if** rows ≠ 59, anything changed or missing, entries ≠ 22, or the canonical digest
≠ `2e994018e375f318f4e6dc9a67ab248e4622de8c426146ca756d5f47d2767152`. A changed staged
digest means real staging by another session — not a stat refresh.

**Step 1 — isolated worktree. Never `git switch` in the shared checkout.**

```powershell
git worktree add ../aerora-preserve-pg -b chore/preserve-pg-verification-toolchain 074b772a
```

A linked worktree has its own `HEAD` and index, so the shared checkout's `HEAD`, index and
22 staged entries are untouched. Confirm with `git worktree list` (must show two) and by
re-running Step 0's second command in the shared checkout.

**Step 2 — copy the ten untracked files in, then verify inside the worktree.** They are
untracked and therefore absent from `074b772a`. Copy exactly these ten, nothing else, then
re-run Step 0's manifest check **with the worktree as the working directory**.

**Step 3 — stage by explicit path (AP-0). Ten commands.**

```powershell
git add -- staging/bootstrap_pg.py
git add -- staging/dsn.py
git add -- scripts/verify_schema_drift.py
git add -- staging/provisioning_snapshot.json
git add -- staging/run_pg_lane.py
git add -- staging/run_pg_suite.py
git add -- tests/paper_pg_client.py
git add -- tests/test_api03_rate_limit_storage_is_fail_closed.py
git add -- tests/test_p11b_a8_collection_integrity.py
git add -- staging/PHASE11B_A8_ACCELERATION_REPORT.md
```

Never `git add -A`, `git add .`, or a directory argument. `staging/provisioning_snapshot.json`
is **not** matched by the pending `.gitignore` block, so no `-f` is required — and if a
revised `.gitignore` ever matches it, **do not force it**; return to AP-1b.

**Step 4 — inspect before committing.**

```powershell
git diff --cached --name-status      # exactly the 10 paths, all 'A'
git diff --cached --numstat          # exactly 10 rows, 0 deletions
git status --short                   # no unexpected entry
```

**Abort if** the staged set is not exactly those ten `A` entries.

**Step 5 — commit only with AP-C, explicit pathspec, then verify contents.**

```powershell
git commit -m "Preserve the PostgreSQL verification toolchain and the A8 closure artifacts" -- `
  staging/bootstrap_pg.py staging/dsn.py scripts/verify_schema_drift.py `
  staging/provisioning_snapshot.json staging/run_pg_lane.py staging/run_pg_suite.py `
  tests/paper_pg_client.py tests/test_api03_rate_limit_storage_is_fail_closed.py `
  tests/test_p11b_a8_collection_integrity.py staging/PHASE11B_A8_ACCELERATION_REPORT.md
git show --stat --name-status HEAD   # verify CONTENTS, not the exit code
```

**Step 6 — confirm the shared checkout is untouched, then release.** Shared `HEAD` must
still be `074b772a`; canonical staged digest still `2e994018…`; 22 staged entries. Then
`git worktree remove ../aerora-preserve-pg` when the branch is no longer needed.
**No push, no PR, no CI** — outside authorization.

**Excluded from every step:** T2 (`provision_database.py`, `verify_provisioning.py`) · T4
(7 probes) · T5 (29 PostgreSQL-gated tests, and never as a block) · T6 (11 untracked
migrations) · `.gitignore` · `staging/pg_dsn.txt` · any log · the manifest ·
`tests/conftest.py` · `pytest.ini`.

---

## 7. Remaining blockers and the smallest next action

| # | Blocker | Owner | Unblocks |
|---|---|---|---|
| 1 | **AP-1a** — Platform approval for the 7 T1 files | Platform | T1 staging |
| 2 | **AP-1b** — ruling on the two `.gitignore` preservation lists, incl. whether `provisioning_snapshot.json` is a keeper (§4.4) | `POST_LAUNCH_IMPROVEMENTS.md` item-12 author | T1 staging |
| 3 | **AP-3** — Stream D + Coordinator approval for the 3 T3 artifacts | Stream D + Coordinator | T3 staging |
| 4 | **AP-E** — isolated worktree or a coordinated window | Coordinator | any staging |
| 5 | **AP-C** — explicit commit authorization | Coordinator | the commit |
| 6 | **AP-DB** — assign the migration findings for investigation | DB owner | §5 follow-up |
| 7 | **W1B-04 still OPEN** — 59 files / 1,506,272 bytes untracked; one realised loss today | Coordinator | the reason this exists |

**Smallest next action: obtain AP-1b — a single ruling on whether the keep-set is the union
of the item-12 list and T1, and whether `staging/provisioning_snapshot.json` is a keeper.**
It is one decision by one identifiable owner, it is the only blocker that is a genuine
disagreement rather than a missing signature, and AP-1a cannot be answered without it —
Platform cannot approve a file set that another workstream's pending `.gitignore` is
arguing about. Everything else in §6 is mechanical once the scope is ruled on.

---

## 8. Every command executed, and its effect

All read-only. Nothing in the repository was modified except the creation of this report.

| # | Command | Effect |
|---|---|---|
| 1 | `git rev-parse --abbrev-ref HEAD` / `HEAD` / `origin/main` | read only |
| 2 | `git rev-list --left-right --count HEAD...origin/main` | read only |
| 3 | `git worktree list` | read only — confirmed exactly 1 worktree |
| 4 | `git diff --cached --name-only \| Measure-Object` | read only |
| 5 | `git diff --name-only \| Measure-Object` | read only |
| 6 | `git status --porcelain=v1` and `-uall`, counted | read only; may refresh the index stat cache (§1.1) |
| 7 | `git diff --cached --raw` → `$env:TEMP\a9c_raw.txt` → `Get-FileHash` → `Remove-Item` | continuity digest; **temp file written outside the repository and deleted** |
| 8 | `git diff --numstat -- .gitignore`, `git status --porcelain -- .gitignore` | read only |
| 9 | `git log -3 --format=… -- .gitignore` | read only |
| 10 | Python: manifest regex parse + 59 sha256 reads + canonical staged digest + destination `exists()` | read only |
| 11 | Python: glob `backend_app/migrations`, `migrations`, `migrations/hardening`, `archived_migrations/root_migrations`, `archived_migrations` (rglob) | read only |
| 12 | Python: existence check of the 8 item-12 filenames | read only |
| 13 | `grep` `SECURITY DEFINER` in `024_…sql`; `ON CONFLICT\|PRIMARY KEY\|schema_migrations` in `provision_database.py`; set-definition terms in `probe_p11b_w1c_unledgered.py`; `staging` in `POST_LAUNCH_IMPROVEMENTS.md` | read only |
| 14 | `read_file` on `scripts/provision_database.py:460-491` | read only |
| 15 | `Test-Path` on the destination (twice, incl. immediately before writing) | read only |
| 16 | `fs_write` → `staging/PHASE11B_PG_PRESERVATION_COORDINATION_REPORT.md` | **the only repository write** |

One Python invocation failed with a `SyntaxError` from a malformed expression and was
rerun; it executed no git or filesystem mutation.

---

## 9. Explicit confirmation

| Action | Occurred? |
|---|---|
| Any file staged (`git add` in any form) | **NO** — `git add` was never invoked |
| Any commit created | **NO** |
| Any branch created | **NO** |
| Any worktree created | **NO** — `git worktree list` shows exactly one, the pre-existing shared checkout |
| `git switch` / `checkout` / `reset` / `clean` / `stash` / `restore` | **NO** |
| `.gitignore` modified | **NO** |
| Another workstream's staged or unstaged files modified | **NO** — 22 staged and 100 unstaged, both unchanged |
| Any database connection, query, write, or migration | **NO** |
| `verify_provisioning.py` / `provision_database.py` run | **NO** |
| `staging/provisioning_snapshot.json` rewritten | **NO** — digest still `8e3057e0e86dd113…` |
| `staging/PHASE11B_A9_PRESERVATION_MANIFEST.txt` rewritten | **NO** |
| PostgreSQL or real-Redis lane run | **NO** |
| Redis started or any infrastructure installed | **NO** |
| Push, PR open/close, merge, or CI trigger | **NO** |
| Pre-existing report overwritten | **NO** — destination confirmed absent before writing |
| Files written | **1** — this report |
| Files written outside the repository | **1** — `$env:TEMP\a9c_raw.txt`, deleted immediately |

**The toolchain is NOT committed. The database findings are NOT resolved. Schema drift is
NOT repaired.**

---

Preservation staging **BLOCKED pending approvals** · PostgreSQL lane **UNEXECUTED** ·
real-Redis lane **UNEXECUTED** · no database contacted · no production target contacted ·
no remote operation · TENANT-01 **OPEN** · QUOTA-01 **OPEN** · W1B-01 OPEN · W1B-04 OPEN ·
live trading **OFF / NOT CERTIFIED**
