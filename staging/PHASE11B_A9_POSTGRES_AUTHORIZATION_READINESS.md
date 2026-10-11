# Phase 11B-A9 — Protect A8 Artifacts and Prepare Authorized PostgreSQL Verification

> **Disambiguation.** Two workstreams used the label "A9" and two used "A8" (§1.1, A9-07).
> This report is **`PHASE11B_A9_POSTGRES_AUTHORIZATION_READINESS.md`** — the PostgreSQL
> lane. It is **not** `PHASE11B_A9_D5_FAILURE_DIAGNOSTICS.md` (the D5′ real-Redis
> workstream, which used the label first). The A8 it protects is
> **`PHASE11B_A8_ACCELERATION_REPORT.md`** (launch-blocker closure), **not**
> `PHASE11B_A8_READ_ONLY_DIAGNOSTICS.md`. Cite these by filename, never by bare label.

**Live trading: OFF / NOT CERTIFIED.** PostgreSQL lane: **UNEXECUTED**. Real-Redis lane:
**UNEXECUTED**. TENANT-01: **OPEN**. QUOTA-01: **OPEN**.

No source change, no shared-index change, no database write, no infrastructure
installation, no CI trigger, no commit, no remote operation. `git add` was never invoked.
No `gh` command was issued — remote state below is read from artifacts another session
had already written to disk.

Executed 2026-10-10, 17:25–18:0x local.

Artifacts produced: this report, `staging/PHASE11B_A9_PRESERVATION_MANIFEST.txt`,
`staging/p11b_a9_drift.txt`.

---

## 1. Current baseline, re-measured

Nothing below was carried over from A8. Where A8's figure and A9's differ, both are shown
with the cause.

| Property | A8 recorded | A9 measured | Moved? |
|---|---|---|---|
| Branch | `main` | `main` | no |
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | **no** |
| `origin/main` | `074b772a` | `074b772a` | no |
| Ahead / behind | `0 / 0` | `0 / 0` | no |
| `.git/index` SHA-256 | `6D965C8E…0458613` | `6D965C8EAFDD23EECCEA41A6BFF25DF5D9F1EE9FC64DFA17C29C965210458613` | **byte-identical** |
| `.git/index` mtime | — | `10-10 12:51:24` | unchanged since before A8 |
| Staged entries | 22 | **22** | no |
| `git status --porcelain` (default) | 254 (W1B) / +4 (A8) | **256** | +2 |
| `git status --porcelain -uall` | 3,321 (A8, implied) | **3,325** | +4 |
| `git reflog` most recent | 2026-10-05 23:40:19 | **2026-10-05 23:40:19** | **no git operation on this checkout, still** |

The index hash being byte-identical across W1B → A8 → A9 is the load-bearing fact: no
session has staged, unstaged, reset, stashed or cleaned anything on this checkout.

**A8's path counts were measured in two different `--untracked-files` modes and that is
worth stating, because it looks like a discrepancy and is not.** `git status --porcelain`
defaults to `normal`, which collapses untracked directories; `-uall` expands them. W1B's
"254 entries / 134 untracked" is `normal`; A8's "staging 2968, tests 91…" breakdown is
`-uall`. Current: **256 entries / 136 untracked (normal)**, **3,325 (-uall)**. Both are
reported here so neither is mistaken for drift.

### 1.1 Concurrent sessions are still active — heavily

Measured at 17:25:33: files written to `staging/` **15 seconds earlier**.

| Time | Path | Not mine |
|---|---|---|
| 17:25:37 | `staging/a8_raw_err.txt` | ✔ |
| 17:24:06 | `staging/p23_measure_P23C_POST30.txt` (16.2 MB) | ✔ |
| 17:23:47 | `staging/p11b12_diff_code.txt` | ✔ |
| 17:23:19 | `staging/a8_runs.json` | ✔ |
| 17:23:02 | `staging/a8_pr.json` | ✔ |
| 17:20:58 | `staging/p11bd3_reg4a.txt` | ✔ |
| 17:10:02 | `staging/PHASE11B_A11_REDIS_CI_DIAGNOSTICS.md` | ✔ |
| 17:01:36 | `staging/p11b_w1c_blob_recovery.txt` | ✔ |

Eleven `python` processes are live, with start times spanning 05:35 to **17:20:56** — at
least four distinct sessions started after A8 began. Workstreams visible by artifact
prefix: **A11** (Redis CI diagnostics), **W1C** (blob recovery, the successor to W1B),
**D3**, **P23**, **P11B12**, and an **a8_\*** PR-inspection session.

> **The phase labels have collided, and this has to be read carefully.** Two different
> workstreams independently used **A8**, and two used **A9**:
>
> | File | mtime | Workstream |
> |---|---|---|
> | `PHASE11B_A8_ACCELERATION_REPORT.md` | 17:06 | launch-blocker closure (the one this report protects) |
> | `PHASE11B_A8_READ_ONLY_DIAGNOSTICS.md` | 18:12 | **different** — D5′ rebaseline + CI failure diagnostics |
> | `PHASE11B_A9_D5_FAILURE_DIAGNOSTICS.md` | 16:34 | **different** — D5′ real-Redis failure diagnosis, and it took the A9 label first |
> | `PHASE11B_A9_POSTGRES_AUTHORIZATION_READINESS.md` | this file | PostgreSQL authorization readiness |
>
> So the `staging/a8_*.json` files are **not** artifacts of the A8 this report protects —
> they are `PHASE11B_A8_READ_ONLY_DIAGNOSTICS`'s `gh` output for pull request #8. The
> protected A8's artifacts are the three `p11b_a8_*` logs plus
> `PHASE11B_A8_ACCELERATION_REPORT.md`. Raised as **A9-07**.

### 1.2 A live remote PR exists, and its test gate is red

Read from `staging/a8_runs.json` (written 17:23:19 by another session) and local refs.
**No `gh` call was made by A9.**

| Fact | Value |
|---|---|
| Repository | `Narendra2212/vyomquant-saas` |
| PR | **#8** |
| Head SHA | `a624ce89b16005b0c73197005d6d0672737dad65` |
| Branch | `ci/d5-real-redis-switch` — exists locally **and** at `origin` |
| Commit | `a624ce89` "Let the CI Redis service reach the tests that exist to use it" |
| Contents vs `main` | `M .github/workflows/01-pr-check.yml`, `A tests/real_redis_gate.py`, `A tests/test_quota01_exchange_slot_is_atomic.py`, `A tests/test_quota01_metered_reservation_is_hard.py`, `A tests/test_webhook_duplicate_delivery_real_redis.py` |
| `05 Security` | `completed` / **success** |
| `01 PR Check` | `completed` / **failure** |

Three observations, none of them A9's to act on:

1. **This is exactly the D5′ manifest `PHASE11B_A5_AUTHORIZATION_READINESS.md` proposed** —
   the 4 test files plus the single `01-pr-check.yml` hunk. That workstream received the
   authorization A5 requested and executed it. **A9 did not and must not touch it.**
2. **`01 PR Check` is red on PR #8 — and this is already diagnosed in depth elsewhere, not
   a finding of A9's.** On a later pass of `staging/` I found the owning reports and they go
   much further than the two JSON files I read:
   - `PHASE11B_A7_D5_CI_VERIFICATION.md` opened PR #8 and captured the job log
     (run `38046625040`, job `114197790236`).
   - `PHASE11B_A8_READ_ONLY_DIAGNOSTICS.md` §3 carries a **62-entry failure inventory** for
     that run, reconciled against pytest's own count, and records **all four runs across
     PRs #6/#7/#8 as `failure`**.
   - `PHASE11B_A13_REAL_REDIS_VERIFICATION.md` reproduces **both** failure classes by
     running each file alone, eliminating full-suite state leakage as the cause.
   - **There are three open D5′ PRs, and #7 and #8 are byte-identical** (tree
     `271f13c2…`, 5 files, +1,761, created 18 minutes apart by two different sessions).
     Both `PHASE11B_A8_READ_ONLY_DIAGNOSTICS` and `A13` raised a **stop condition**: the
     consolidation needs a human decision, and the corrections for the observed failures
     exist on none of the three branches.

   So the residual is **not** "the failure is undiagnosed" — it is **"three duplicate PRs
   need a human consolidation decision before any fix is committed."** A9 defers to those
   reports and corrects its own finding accordingly (**A9-04**).
3. **It is a live confirmation of P11-B08's premise.** A red `01 PR Check` on a PR is
   precisely the state the missing deploy gate would have to refuse. It does not change
   A8's finding; it demonstrates the condition is real and current.

### 1.3 A8 artifact integrity — verified, untampered

| Path | Bytes | mtime | SHA-256 | Verdict |
|---|---|---|---|---|
| `staging/PHASE11B_A8_ACCELERATION_REPORT.md` | 33,440 | 17:06:28 | `57f6d373cc0537f1…7014dbe5` | intact; mtime is A8's own final edit |
| `tests/test_api03_rate_limit_storage_is_fail_closed.py` | 21,886 | 16:23:36 | `33a30d00744ea7c8…d508ea0cff` | intact |
| `tests/test_p11b_a8_collection_integrity.py` | 16,915 | 16:33:05 | `ade04e82d4f04185…9dde4b54a06e` | intact |

All 7 required A8 sections plus §2.0 are present (heading scan). **Of the 59 files in the
preservation manifest, exactly one was modified in the last 60 minutes, and it was modified
by A8 itself.** No concurrent session has touched any manifest file.

---

## 2. Preservation manifest

Full per-file list with sizes and SHA-256 is in
**`staging/PHASE11B_A9_PRESERVATION_MANIFEST.txt`** — **59 files, 1,506,272 bytes.**
Summarised by tier below. **Every path is untracked unless marked TRACKED.**

| Tier | Purpose | Files | Why it is in the smallest set |
|---|---|---:|---|
| **T1** | Lane core | 7 | The read-only lane **cannot run at all** without these |
| **T2** | Provisioning / re-baseline | 2 | The only things that can clear the drift; also the only destructive capability |
| **T3** | Phase 11B-A8 artifacts | 3 | The two closures from A8 exist **only** as untracked files |
| **T4** | Read-only probes | 7 | Produce the identity/activity/blocker evidence the plan cites |
| **T5** | PostgreSQL-gated tests | 29 | **The lane *is* these files** |
| **T6** | Untracked migrations | 11 | Without them provisioning cannot reproduce the live schema (§4.4) |

T1–T4, exact, with hashes abbreviated:

| Path | Bytes | SHA-256 (first 16) |
|---|---:|---|
| `staging/bootstrap_pg.py` | 25,191 | `b22d15cd25baae57` |
| `staging/dsn.py` | 4,192 | `c123dcac39a8cb2e` |
| `scripts/verify_schema_drift.py` | 12,170 | `a7ce585d4e03030c` |
| `staging/provisioning_snapshot.json` | 396,325 | `8e3057e0e86dd113` |
| `staging/run_pg_lane.py` | 4,200 | `0c3b013af1751829` |
| `staging/run_pg_suite.py` | 5,799 | `1997c680746dbce4` |
| `tests/paper_pg_client.py` | 17,155 | `cc654f71b0b97a15` |
| `scripts/provision_database.py` | 35,363 | `8379eacf73a2a5d6` |
| `scripts/verify_provisioning.py` | 16,404 | `e90742f4b36bb1c3` |
| `tests/test_api03_rate_limit_storage_is_fail_closed.py` | 21,886 | `33a30d00744ea7c8` |
| `tests/test_p11b_a8_collection_integrity.py` | 16,915 | `ade04e82d4f04185` |
| `staging/PHASE11B_A8_ACCELERATION_REPORT.md` | 33,440 | `57f6d373cc0537f1` |
| `staging/probe_pg_ready.py` | 11,535 | `16d4fdfd41394d92` |
| `staging/probe_pg_activity.py` | 1,627 | `d4db2e2687c4d10b` |
| `staging/probe_pg_blockers.py` | 2,886 | `01fbff3fb17eebe9` |
| `staging/start_pg.ps1` | 1,494 | `8c1b0c45e07e4a16` |
| `staging/probe_p11b_w1b_import_time_paths.py` | 7,339 | `0435304c09c820a6` |
| `staging/probe_p11b_w1b_pyc_recovery.py` | 7,791 | `3f871e8e61d1981d` |
| `staging/probe_p11b_w1b_lost_coverage.py` | 5,913 | `21988c0aa9303029` |

### 2.1 Dependency relationships

```
staging/pg_dsn.txt            (47 B — the shared DSN; NOW GITIGNORED, see §2.3)
   ^ read by
   +-- staging/dsn.py              resolve_dsn(): AERORA_TEST_DATABASE_URL -> pg_dsn.txt -> exit
   +-- scripts/verify_schema_drift.py      --dsn default
   +-- scripts/verify_provisioning.py      admin DSN default
   +-- staging/run_pg_lane.py / run_pg_suite.py   refuse to run if absent
   ^ WRITTEN BY EXACTLY ONE FUNCTION
   +-- staging/bootstrap_pg.py :: write_dsn_if_safe()     <-- the single safety contract

staging/bootstrap_pg.py   (the shared helper: TargetState, SCHEMA_MARKERS,
   |                       MIN_PROVISIONED_TABLES, DsnWriteRefused, _default_probe, start, main)
   ^ loaded BY PATH (staging/ is not a package) via spec_from_file_location
   +-- staging/probe_pg_ready.py            classification + _default_probe
   +-- tests/test_p10h_bootstrap_pg_selection_is_explicit.py   (TRACKED? no - untracked)
   +-- tests/test_p10j_probe_pg_ready_uses_shared_classification.py
   +-- [absent] staging/probe_pg_find.py, staging/pg_keepalive_r1.py   <-- W1B-01, still OPEN

staging/provisioning_snapshot.json   (396 KB baseline, 50 recorded migrations)
   ^ READ by   scripts/verify_schema_drift.py      (DEFAULT_BASELINE)
   ^ READ by   scripts/verify_production_schema.py (DEFAULT_BASELINE)
   ^ WRITTEN by scripts/verify_provisioning.py:343  <-- the ONLY writer

scripts/verify_provisioning.py
   +-- subprocess --> scripts/provision_database.py --apply --create-substrate   (run TWICE)
   +-- writes staging/verify_provisioning_{label}.log      (now gitignored: staging/*.log)
   +-- writes staging/provisioning_snapshot.json

scripts/provision_database.py
   +-- _numbered_sql_files(): glob backend_app/migrations/*.sql  (less 020) + migrations/*.sql
   +-- subprocess --> alembic upgrade <ALEMBIC_TARGET>
   +-- records each file + SHA-256 in public.schema_migrations

AERORA_TEST_DATABASE_URL  (the ONE opt-in)
   +-- tests/conftest.py:37-41   if set -> os.environ["DATABASE_URL"] = it; else -> ""
         => 38 test files gate on AERORA_TEST_DATABASE_URL
         => the 2 files that gate on DATABASE_URL are reached through this propagation
```

### 2.2 Tracked status — the whole lane is unreviewed code

| Set | Total | Tracked | Untracked |
|---|---:|---:|---:|
| T1–T4 toolchain | 19 | **0** | **19** |
| `tests/**/*_postgres.py` | 32 | 3 | **29** |
| Files gating on `AERORA_TEST_DATABASE_URL` | 38 | — | — |
| `backend_app/migrations/*.sql` | 38 | 27 | **11** |
| `tests/conftest.py`, `pytest.ini` | 2 | **2** (both ` M`) | 0 |

**Every instrument that would produce the PostgreSQL evidence is untracked.** This is
W1A-06 escalated to W1B-04, and W1B-04 already has one realised loss attached
(`probe_pg_find.py` and `pg_keepalive_r1.py`, deleted by a filesystem action, recoverable
only from `.pyc`). A9 adds the measurement that makes the exposure concrete: **59 files,
1.5 MB, zero of them in any commit, and the only recovery path on a second slip is the
SHA-256 list in this manifest.**

### 2.3 A `.gitignore` change is already in flight and partly governs this

`.gitignore` is ` M` (unstaged, **another workstream's**, +15 / −0) and **already in effect
in the working tree**, because `.gitignore` is read from disk and not from the index:

```
staging/pgdata*/        <-- staging/pgdata_r1/PG_VERSION  confirmed IGNORED (.gitignore:372)
staging/redis/
staging/*.log
staging/pg_dsn.txt      <-- confirmed IGNORED (.gitignore:375)
```

Its comment **names a preservation policy and a file list** — `verify_schema.py`,
`rls_inventory.py`, `exposure_check.py`, `fk_typecheck.py`, `verify_live_trading_off.py`,
`run_pg_lane.py`, `try_alembic_head.py`, `bootstrap_pg.py` — overlapping T1/T4 but **not
identical to it**. Two consequences:

1. **Someone else already owns part of the preservation decision.** The manifest here must
   be reconciled with that list, not merged over it.
2. **The exclusions are incomplete for safety.** Only `staging/*.log` is ignored, and almost
   every log in `staging/` is `.txt` — including a **16.2 MB** `p23_measure_*.txt` and A8's
   two 2.5 MB collection logs. So `staging/` still exposes **3,205 untracked paths** at
   `-uall`. **This is precisely why `git add -A` must not be used** (§3, AP-0).

---

## 3. Ownership conflicts and approval checklist

**A9 took no approval and performed no preserving action.** `git add` in any form was never
run.

| # | Approval | Scope | Owner | Blocking |
|---|---|---|---|---|
| **AP-0** | **Ban `git add -A` / `git add .` for this preservation.** Stage by explicit path only. | 3,205 untracked paths at `-uall`, incl. 16.2 MB and 2.5 MB logs, `exchange_keys.json`, and other workstreams' in-flight files | Coordinator | — |
| **AP-1** | Commit **T1+T4** (14 files) — the read-only lane and its probes | `staging/`, `scripts/verify_schema_drift.py` | Platform (W1A/W1B lineage) | conflicts with the in-flight `.gitignore` list → reconcile first |
| **AP-2** | Commit **T2** (2 files) — `provision_database.py`, `verify_provisioning.py` | carries CREATE/DROP DATABASE | DB owner | review before, not after |
| **AP-3** | Commit **T3** (3 files) — the A8 closures | `tests/` ×2, `staging/` ×1 | Stream D (tests) + Coordinator (report) | none |
| **AP-4** | Commit **T5** (29 PG-gated tests) | `tests/**/*_postgres.py` | per-test owners; several created today by live sessions | **ownership contested — see below** |
| **AP-5** | Commit **T6** (11 migrations 021–031) | `backend_app/migrations/` | DB owner | §4.4 — four are applied but unrecorded |
| **AP-6** | Resolve the in-flight `.gitignore` change vs this manifest | `.gitignore` | whoever owns that diff | AP-1 |
| **AP-7** | **A-2** — run the PG lane against shared `pgdata_r1`, accepting an interference window | the shared cluster | Coordinator **+ every live session** | §5 |
| **AP-8** | Re-baseline (`verify_provisioning.py`) | scratch DB + overwrite of the shared baseline | DB owner | §4.3 |

### 3.1 Contested ownership — do not stage T5 blind

W1B recorded eight `tests/` files edited by other sessions between 12:51 and 15:53. A9
measures the same hazard live: four sessions started after A8, and `staging/` artifacts for
**A11, W1C, D3, P23, P11B12** are being written now. Several T5 files were created today.
**Staging T5 as a block risks absorbing another workstream's mid-flight test file** — the
exact thing A8's constraints and this task's constraints both forbid. Resolution is
per-file confirmation with each live session, not a bulk decision.

### 3.2 What is NOT at risk, and what is

- **Not at risk:** anything in a commit. PR #8's 5 files are committed and pushed.
- **At risk:** all 59 manifest files. The precedent is not hypothetical — W1B-01 is a
  realised loss of exactly this kind, on exactly these directories, today.
- **Mitigation A9 could deliver without authorization:** the SHA-256 manifest. It converts
  "untracked and unverifiable" into "untracked but verifiable" — loss and tampering become
  detectable, and any future restoration is checkable against a recorded digest. It is not
  a substitute for a commit.

---

## 4. PostgreSQL safety preparation

### 4.1 Target identity — verified, read-only, unchanged

```
python staging/bootstrap_pg.py verify
DSN_FILE: C:\aerora_quant_backend_updated_final1\staging\pg_dsn.txt
TARGET:   PROVISIONED  public_tables=89
          data_directory=C:/aerora_quant_backend_updated_final1/staging/pgdata_r1
exit 0
```

| Property | Value |
|---|---|
| DSN | `postgresql://postgres:***@127.0.0.1:51805/postgres` |
| Host / port | **127.0.0.1 : 51805** — loopback, listening, PID 12924 |
| Data directory | `staging/pgdata_r1` — the authorized isolated instance |
| Server | PostgreSQL **16.2** |
| `public` tables | **89** · classification **PROVISIONED** |
| Alembic (A8, unchanged) | single head `d4e5f6a7b8c9`; cluster at `d4e5f6a7b8c9` |
| Backends | 6 `postgres` processes, started 11:31 |
| Established client connections | **0** at 17:25 — up but idle |
| Second cluster | `staging/pgdata` exists and is **not** running (no listener) |
| Keepalive | `staging/pg_keepalive_r1.py` **absent** (W1B-01) — the server survives without it, but **nothing will restart it**, and `pgserver` picks a **new random port** on every start |
| Shared DSN file | rewritten by **another session** at 17:55:23 with byte-identical content — see below |

**A9 initialized, started, stopped, deleted, renamed and overwrote nothing. A9 read
`staging/pg_dsn.txt` and never wrote it.**

**But another session did, mid-phase — and this is the most operationally useful thing A9
measured.** The file's mtime moved from `10-10 13:17:04` to **`10-10 17:55:23`** between
two A9 checks. A9 issued no write, and `bootstrap_pg.write_dsn_if_safe` is the only writer
in the tree (§2.1). Verified immediately afterwards:

| Check | Result |
|---|---|
| Content | `postgresql://postgres:***@127.0.0.1:51805/postgres` — **unchanged** |
| Size | **47 bytes**, same as before |
| SHA-256 (post-write) | `A746580D3CEAD2EFA056E3F422FA38EF4B0D303AF94BBE1F95B75C2661AF3A96` |
| Re-verify | `PROVISIONED  public_tables=89  data_directory=…/pgdata_r1` |
| Listener | still 127.0.0.1 : 51805 |

So a concurrent session re-ran the shared DSN writer, and **the Workstream H/I safety
contract behaved exactly as designed** — it re-confirmed the server was serving
`pgdata_r1`, applied the marker rules, and rewrote the same value rather than repointing
anything. That is the contract working under real concurrency, which is the first live
evidence of it since W1B-01 removed its two callers' regression coverage.

It also sharpens two things in this report: **concurrent sessions are writing shared
PostgreSQL state right now** (§5's interference window is not theoretical), and **Stage B's
stop condition is the correct one** — the port and target must be re-verified immediately
before any lane run, not inherited from this document.

### 4.2 Drift — reproduced identically, and A8 had one item wrong

`python scripts/verify_schema_drift.py` → **`RESULT: DRIFTED`**, exit 1. Full log:
`staging/p11b_a9_drift.txt` (142 lines). The script proves its own session read-only
(`default_transaction_read_only` verified `on` before reading). Every dimension count is
identical to A8's run.

| Dimension | Baseline | Live | missing | unexpected |
|---|---:|---:|---:|---:|
| tables | 85 | 89 | 0 | 4 |
| columns | 1135 | 1193 | 0 | 58 |
| primary_keys | 85 | 89 | 0 | 4 |
| foreign_keys | 121 | 123 | 0 | 2 |
| unique_and_check_constraints | 122 | 139 | 0 | 17 |
| indexes | 400 | 424 | 0 | 24 |
| row_level_security | 85 | 89 | 0 | 4 |
| **policies** | 188 | 195 | **1** | 8 |
| **privileges** | 991 | 1007 | **5** | 21 |
| **functions** | 36 | 38 | **1** | 3 |
| enum_types | 6 | 8 | 0 | 2 |
| recorded_migrations (advisory) | 50 | 56 | 0 | 6 |
| extensions / triggers / sequences / alembic_version | — | — | **same** | **same** |

> **Correction to `staging/PHASE11B_A8_ACCELERATION_REPORT.md` §4.4.** A8 states "with
> **0 missing** in every category" and "Every single delta is 'NOT IN this repository'".
> **That is wrong.** There are **7 missing items** across 3 dimensions — 1 policy, 5
> privileges, 1 function. A8's conclusion (stale baseline) survives, but its stated
> evidence did not hold, and the 7 removals needed explaining rather than denying. They are
> explained in §4.4. The A8 report is left as written, per "preserve prior reports"; this is
> the correction of record.

### 4.3 Exact database effects of the proposed re-baseline

`scripts/verify_provisioning.py` is the **only** writer of
`staging/provisioning_snapshot.json` (`:343`). Read from source, not inferred:

```
DB_PREFIX = "aerora_provision_check"            #: "Fixed prefix so this script can only
                                                #:  ever drop a database it made."
:274  dbname = f"{DB_PREFIX}_{os.getpid()}"
:275  target = _swap_database(dsn, dbname)      # same server, different database
:288  DROP DATABASE IF EXISTS "aerora_provision_check_<pid>"
:289  CREATE DATABASE "aerora_provision_check_<pid>"
:300  assert the scratch database is empty
:309  first  = snapshot(target)                 # after provision run 1
:323  second = snapshot(target)                 # after provision run 2 (idempotence)
:333  diff_snapshots(first, second)
:343  staging/provisioning_snapshot.json <- json.dumps(first)
:359  DROP DATABASE IF EXISTS "aerora_provision_check_<pid>"   (skipped under --keep)
```

**Effects on the shared `postgres` database: none beyond the connection itself.** The
docstring is explicit — *"Creates and drops its OWN database on the target server… The
administrative connection is made to `postgres` purely to issue CREATE/DROP DATABASE"* —
and it refuses the Supabase pooler port and non-local hosts (`--i-understand-this-is-remote`
exists as the deliberate override).

| Effect | Target | Reversible? |
|---|---|---|
| Admin connect (autocommit) | `postgres` db on the shared server | n/a |
| `DROP`/`CREATE DATABASE aerora_provision_check_<pid>` | **new scratch db**, never `postgres` | yes — dropped at `:359` |
| Full provisioning DDL/DML, **twice** | inside the scratch db only | yes, with the db |
| `alembic upgrade` via subprocess, **twice** | inside the scratch db only | yes |
| **Overwrites `staging/provisioning_snapshot.json`** | **shared 396 KB baseline** | **only from the manifest hash** `8e3057e0e86dd113` |
| Writes `staging/verify_provisioning_*.log` | local, now gitignored | yes |

Residual risks on a **shared** server: `CREATE DATABASE` copies `template1` and fails if
another session is connected to it; a crash without `--keep` leaves an orphan
`aerora_provision_check_<pid>` database; temporary disk for a duplicate schema; contention
with the 6 existing backends.

> **A8 overstated this risk and the correction matters for the approval.** A8 said the tool
> "issues `DROP DATABASE` / `CREATE DATABASE`" as the reason not to run it, which reads as a
> threat to the shared database. It is not: the DDL is confined to a PID-named scratch
> database behind a fixed prefix. **The real shared-state hazard is the overwrite of
> `provisioning_snapshot.json`,** plus server-level `CREATE DATABASE` contention.

### 4.4 Does the baseline still explain every drift item?

**Partly — and not the way A8 claimed.** Evidence, all read-only:

**The baseline is older than the migrations that cause the drift.**
`staging/provisioning_snapshot.json` is dated **10-06 20:14** and its ledger holds **50**
entries topping out at `migrations/021_strategy_deployment_live_uniqueness.sql`. It contains
**no** entry for 022–031. The migration files themselves postdate it:

| File (all in `backend_app/migrations/`, all **untracked**) | mtime |
|---|---|
| `023_remediate_privilege_escalation.sql` | 10-07 12:28 |
| `024_remediate_referral_double_commission.sql` | 10-07 12:31 |
| `025_work03_bot_ownership_claims.sql` | 10-07 22:25 |
| `026_fee_records_row_level_security.sql` | 10-09 11:36 |
| `027_referral_ledger_currency.sql` | 10-09 12:13 |
| `028_safety_control_audit.sql` | 10-09 22:11 |
| `029_referral_obligations.sql` | 10-09 22:46 |
| `030_execution_venue_order_id.sql` | 10-09 23:35 |
| `031_fee_records_fee_pct_nullable.sql` | 10-10 04:23 |

**All 7 "missing" items trace to specific statements, one-for-one.** Migration **023**:

```
:101  REVOKE INSERT, UPDATE ON public.library_strategies FROM authenticated;
:121  DROP POLICY IF EXISTS "Users can update their own strategies" ON public.library_strategies;
:138  REVOKE INSERT, UPDATE, DELETE ON public.library_strategies FROM anon;
```

accounts for **6 of 7** — the 5 missing privileges (`anon` INSERT/UPDATE/DELETE,
`authenticated` INSERT/UPDATE) and the 1 missing policy. Confirmed from the other side: the
baseline JSON **does** carry all five of those `library_strategies` grants. The 7th is
`process_referral_commission`, which appears as missing with final field `False` and
unexpected with `True`; the snapshot's function query is
`SELECT p.proname, pg_get_function_identity_arguments(p.oid), p.prosecdef`, so the flipped
field is **`SECURITY DEFINER`**, and migration **024** drops and recreates that exact
signature (`:149`, `:159`).

**The 4 new tables correspond to 025/026/028/029** — `bot_ownership_claims`,
`fee_records`, `referral_obligations`, `safety_control_audit`; none is in the baseline
(verified directly against the JSON).

**Two things A8 did not establish, and both bear on the approval:**

1. **`provision_database.py` would pick these up today.** `_numbered_sql_files()` globs
   **all** of `backend_app/migrations/*.sql` (less `020`), with no upper bound — the
   "001–019" in its own docstring is **stale relative to its code**. So a re-baseline now
   applies 021–031 and is *expected* to clear the drift. That is a **prediction, not a
   result**, and §6 makes it a stop condition rather than an assumption.
2. **Four migrations are applied but unrecorded.** Live ledger = 56 = the baseline's 50 +
   exactly the 6 unexpected (026–031). So **022, 023, 024, 025 are absent from
   `public.schema_migrations` on the live cluster while their effects are plainly present**
   (023's revocations show up as drift; 025's table exists). `provision_database.py` decides
   what to apply from that ledger plus a SHA-256 (`"REFUSED {name}: applied previously with
   a different checksum"`). **New finding A9-01**, below.

**Answer, stated plainly:** the baseline's age explains the *direction and shape* of all 12
drifted dimensions, and all **7/7** removals are traced to named statements in 023/024. The
~148 "unexpected" items are *consistent* with 022–031 but were **not** mapped item-by-item,
and the unrecorded-migration gap means re-baselining is expected — not proven — to clear it.

### 4.5 The lane's gating is sound, and was re-verified

`tests/conftest.py:37-41`:

```python
_EXPLICIT_TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")
if _EXPLICIT_TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = _EXPLICIT_TEST_DATABASE_URL
else:
    os.environ["DATABASE_URL"] = ""
```

One deliberate opt-in: `DATABASE_URL` alone is always blanked, so a `.env` value cannot
re-point the suite (this is the mitigation for W1A-04 on the *test* path). **38** test files
gate on `AERORA_TEST_DATABASE_URL`. The **2** files named `*_postgres.py` that gate on
`DATABASE_URL` instead — `test_database_isolation_level_postgres.py` and
`test_strategy_lifecycle_concurrency_postgres.py` — are reached *through* this propagation,
so the single opt-in covers all of them. **No second environment variable is needed, and
`DATABASE_URL` must never be set by hand.**

`tests/conftest.py` is ` M` (+172 / −0) from another workstream; its diff contains **zero**
occurrences of `DATABASE_URL` or `AERORA_TEST` (grep count 0), so the opt-in block is
intact. `pytest.ini` is ` M` and adds a `realaudit` marker (per A5), not a DB change.

---

## 5. A-2 and the coordination it requires

W1A **A-2** is *"Run the PG-gated lane against the shared `pgdata_r1` cluster, accepting an
interference window with concurrent tabs."* A9 does **not** hold it.

What makes it a real interference window rather than a formality:

| Fact | Measured |
|---|---|
| The lane writes; most tests roll back, **some commit** | W1A Stage 2 |
| The cluster **holds data** | 57 profiles, 28 exchange_keys/orders/positions, 32 audit rows (W1A-05) |
| Concurrent sessions are **live** | 4 started after A8; `staging/` written 15 s before measurement |
| Named live workstreams | A11, W1C, D3, P23, P11B12, PR-#8 inspection |
| A realised cross-session loss already occurred today | W1B-01 |
| **A concurrent session wrote the shared DSN during this phase** | `staging/pg_dsn.txt` rewritten 17:55:23, content identical (§4.1) |

Coordination required before A-2 is exercised: each live session either confirms it is not
using `pgdata_r1` for the duration, or the lane waits. **W1A A-4 remains the alternative** —
stand up a second cluster so no shared state is touched — at the cost of time and
`PG_ALLOW_DSN_DOWNGRADE` discipline so the shared DSN is not repointed at an empty cluster.

**Two things A-2 cannot buy, and they must not be implied by a green lane:**

1. **W1A-03 is unresolved.** The lane connects as table owner `postgres` with
   `FORCE ROW LEVEL SECURITY` on **0 of 89** tables. Any tenant-isolation test that passes
   there **proves nothing about RLS** — the owner bypasses it. **TENANT-01 / P11-B09 evidence
   cannot come from this lane as configured.**
2. **Untracked instruments are not citable release evidence** (W1A-06 / W1B-04). Hence AP-1
   before AP-7.

**The real-Redis lane stays UNEXECUTED.** `AERORA_TEST_REDIS_URL` unset; nothing listening
on 6379 or 6399; `staging/redis/redis-server.exe` absent. Installing one is W1A **A-3** and
needs separate authorization. Note PR #8 is the *CI* Redis lane — **a CI service container
is not local evidence, and a red `01 PR Check` is not evidence of anything yet.**

---

## 6. Exact next commands, once authorization is granted

Run in this order. **Every stage is abort-safe; stop at the first stop condition.**

### Stage A — preserve (needs AP-0 … AP-6). Explicit paths only.

```powershell
# AP-0: never `git add -A` / `git add .` here - 3,205 untracked paths at -uall.
# Re-verify the manifest first; a changed digest means a concurrent session edited a file.
python -c "import hashlib,pathlib,re; m=pathlib.Path('staging/PHASE11B_A9_PRESERVATION_MANIFEST.txt').read_text(encoding='utf-8'); bad=[(p,h) for p,h in re.findall(r'^  (\S+)\s+(?:TRACKED )?\s*\d+\s+([0-9a-f]{64})$', m, re.M) if not pathlib.Path(p).exists() or hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()!=h]; print('CHANGED/MISSING:', bad or 'none')"

git switch -c chore/preserve-pg-verification-toolchain   # NOT main; the 22 staged entries stay put
git add staging/bootstrap_pg.py staging/dsn.py scripts/verify_schema_drift.py `
        staging/provisioning_snapshot.json staging/run_pg_lane.py staging/run_pg_suite.py `
        tests/paper_pg_client.py                                   # T1
git status --short                                                  # MUST show only the T1 paths
git commit -m "..." -- <the same explicit paths>                    # explicit pathspec, never bare
```

**Stop if:** `git status --short` lists anything not named on the `git add` line; any
manifest digest changed; the `.gitignore` reconciliation (AP-6) is not settled.

### Stage B — confirm the target, change nothing (no approval needed; already run by A9)

```powershell
python staging/bootstrap_pg.py verify
python scripts/verify_schema_drift.py
```

**Stop if:** `verify` reports anything other than `PROVISIONED` with
`data_directory=…/pgdata_r1`; the port is no longer 51805 (`pgserver` re-randomises on every
start and **nothing will restart it** — W1B-01).

### Stage C — re-baseline (needs AP-8). Only to clear the drift.

```powershell
Copy-Item staging/provisioning_snapshot.json staging/provisioning_snapshot.pre_a9.json
# ^ the ONLY rollback for the overwrite. Expected digest of the current file:
#   8e3057e0e86dd1132278edda8a048b8a4a8a2485ec06b80055d9d010a77cd18f
python scripts/verify_provisioning.py          # scratch db aerora_provision_check_<pid>; reads staging/pg_dsn.txt
python scripts/verify_schema_drift.py          # MUST now report clean, or itemise what remains
```

**Stop if:** the run reports a non-empty scratch database, or `diff_snapshots` finds the two
provisioning runs disagree (that is a provisioning idempotence defect and a bigger finding
than the drift); or an `aerora_provision_check_*` database survives the run; or the second
drift check is still DRIFTED — in which case **do not proceed to Stage D**, and record the
residual item-by-item, because §4.4's prediction will have been falsified.

### Stage D — the PostgreSQL lane (needs AP-7 / A-2 **and** per-session coordination)

```powershell
$env:AERORA_TEST_DATABASE_URL = (Get-Content staging/pg_dsn.txt -Raw).Trim()
python -m pytest (Get-ChildItem tests -Recurse -Include '*_postgres.py' | ForEach-Object FullName) `
    -p no:randomly -q --no-header -rfsxX --timeout=900 `
    --junitxml=staging/p11b_a9_pg_lane.xml 2>&1 | Tee-Object staging/p11b_a9_pg_lane.txt
Remove-Item Env:\AERORA_TEST_DATABASE_URL
```

Set **only** `AERORA_TEST_DATABASE_URL` — `conftest.py` propagates it to `DATABASE_URL`
(§4.5). **Never set `DATABASE_URL` by hand.** Do not pipe through `Select-Object -First`;
that terminates the pipeline early and truncates the `Tee-Object` log (this bit A9 once).

**Explicitly NOT run at any stage:** `scripts/verify_provisioning.py` outside Stage C ·
`scripts/provision_database.py` directly · `staging/run_all_probes.py` (mutating members) ·
any bare `alembic upgrade` (W1A-04: `env.py` reads `.env`'s `DATABASE_URL` unguarded) · any
production target · any `git add -A` · any push, PR or CI trigger.

**Stop if:** any test truncates or drops a table it does not own; the cluster's table count
leaves 89 unexpectedly; a concurrent session reports interference; the run exceeds the agreed
window.

### Stage E — record, do not interpret

Fill W1A §7's ledger. **A skip is never a pass.** Do not move any launch-gate status without
the coordinator. **Do not claim TENANT-01 evidence from this lane** — W1A-03 (owner role,
`FORCE RLS` on 0 of 89) makes it structurally incapable of producing it.

---

## 7. Findings raised by A9

| ID | Sev | Finding |
|---|---|---|
| **A9-01** | **P1** | **Migrations 022, 023, 024, 025 are applied to `pgdata_r1` but absent from `public.schema_migrations`.** Live ledger = 56 = baseline's 50 + exactly 026–031; yet 023's revocations and 025's table are present in the schema. `provision_database.py` drives off that ledger plus a SHA-256, so a future run may re-apply them or refuse on checksum. Owner: DB owner. |
| **A9-02** | **P2** | **`provision_database.py`'s docstring is stale against its own code** — it says Step 1 covers `backend_app/migrations/001–019`, but `_numbered_sql_files()` globs the whole directory less `020`. The two readings have opposite implications for whether a re-baseline can ever clear the drift. Owner: DB owner. |
| **A9-03** | **P2** | **`staging/PHASE11B_A8_ACCELERATION_REPORT.md` §4.4 is factually wrong on two points** — "0 missing in every category" (there are 7) and the implication that re-baselining risks the shared database (the DDL is confined to a PID-named scratch db). Conclusions survive; evidence did not. Corrected in §4.2 / §4.3. Owner: A8 (this report is the correction of record). |
| **A9-04** | **P1** | ~~`01 PR Check` is red on PR #8 and the disposition is owed.~~ **CORRECTED — not an A9 finding.** The failure is diagnosed in depth by `PHASE11B_A7_D5_CI_VERIFICATION.md` (job log), `PHASE11B_A8_READ_ONLY_DIAGNOSTICS.md` §3 (62-entry inventory; all four runs across PRs #6/#7/#8 `failure`) and `PHASE11B_A13_REAL_REDIS_VERIFICATION.md` (both classes reproduced per-file). **The actual open item is a human consolidation decision across three duplicate D5′ PRs** — #7 and #8 are byte-identical and the corrections exist on none of the three. Both owning reports already raised this as a stop condition. Owner: Coordinator + Stream D / D5′. A9 adds nothing except the cross-reference. |
| **A9-05** | **P2** | **The in-flight `.gitignore` change names a preservation list that does not match this manifest, and its exclusions are incomplete** — only `staging/*.log` is ignored while nearly every log here is `.txt`, leaving 3,205 untracked paths at `-uall`. Owner: whoever owns that diff + Coordinator. |
| **A9-06** | **P2** | **Nothing will restart `pgdata_r1`.** `staging/pg_keepalive_r1.py` is absent (W1B-01) and `pgserver` allocates a new random port on each start, so a restart silently invalidates `staging/pg_dsn.txt` and every PG-gated invocation. Owner: Phase 10 Workstream I. |
| **A9-07** | **P2** | **Phase labels are being consumed concurrently, so "A8" and "A9" no longer identify a workstream.** Two `PHASE11B_A8_*` and two `PHASE11B_A9_*` reports exist from different sessions (table in §1.1); `PHASE11B_A9_D5_FAILURE_DIAGNOSTICS.md` took the A9 label at 16:34, before this report used it. `PHASE11B_A13_REAL_REDIS_VERIFICATION.md` already cites "both A9 diagnoses" meaning the *other* A9. Any cross-reference by bare phase label is ambiguous and must cite the **filename**. Owner: Coordinator. |
| **W1B-04** | **P0** | **Confirmed and quantified:** 59 files / 1,506,272 bytes of verification toolchain, **0 tracked**. One realised loss already. Recovery path on a second slip is the SHA-256 list in `staging/PHASE11B_A9_PRESERVATION_MANIFEST.txt`. Owner: Coordinator. |

---

## 8. Status

| Item | Status |
|---|---|
| A8 artifacts | **INTACT** — 3/3 digests recorded, untampered |
| Preservation | **MANIFEST ONLY** — 59 files hashed; nothing staged, committed or pushed |
| Shared Git index | **UNTOUCHED** — SHA-256 identical across W1B → A8 → A9 |
| PostgreSQL target | **VERIFIED** read-only: loopback `pgdata_r1`, 89 tables, single alembic head `d4e5f6a7b8c9` |
| Schema drift | **DRIFTED** — reproduced identically; 7/7 removals traced; ~148 additions consistent but not itemised |
| Shared DSN | **written by another session mid-phase**, content byte-identical; safety contract held (§4.1) |
| PostgreSQL lane | **UNEXECUTED** |
| Real-Redis lane | **UNEXECUTED** |
| TENANT-01 | **OPEN** — and not obtainable from this lane (W1A-03) |
| QUOTA-01 | **OPEN** |
| Live trading | **OFF / NOT CERTIFIED** |

**Highest-value next action: AP-0 plus AP-1/AP-3 — commit T1 and T3 by explicit path on a
new branch.** It is the only step that needs no database access, no coordination window and
no further measurement; it removes a P0 whose loss has already happened once today; and
every later stage depends on it, because results from untracked instruments are not citable
as release evidence.

---

Live trading OFF / NOT CERTIFIED · No production target contacted · No venue contacted ·
No remote operation performed by A9 · PostgreSQL lane UNEXECUTED · real-Redis lane
UNEXECUTED · Skips are NOT passes · A local PostgreSQL result is NOT production schema
verification · TENANT-01 OPEN · QUOTA-01 OPEN · W1B-01 OPEN · W1B-04 OPEN · P11-B08 OPEN
