# Phase 11B / Workstream 1D — Migration-ledger safety and drift-checker accuracy

**Read-only analysis.** No source was edited, nothing was staged, no commit or Git state
change occurred, no package was installed, no server was started or stopped, and no
database was contacted at all — not even for a `SELECT`. Every fact below comes from
source inspection or from artifacts already on disk.

**Path collision check:** `staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md` did not
exist at Phase 0, and was re-checked immediately before writing. No collision.

**Headline results**

| | |
| --- | --- |
| **Root cause of W1C-01 found, from source** | Three ad-hoc appliers in `staging/` executed migrations **without recording them**. `apply_remediation.py` applies exactly 023+024; `apply_025.py` applies exactly 025; neither touches `schema_migrations`. The ledger-writing applier `apply_migration.py` post-dates them. `scripts/provision_database.py` is **not** implicated. |
| **Reconciliation can avoid re-execution entirely** | The ledger is a plain 4-column table. A targeted `INSERT` per migration reconciles it with no DDL and no re-execution. All four migrations are `CONDITIONAL` for re-execution, so the INSERT-only path is strictly safer. |
| **W1C-02 is wider than reported** | **12 of 16** dimensions can misreport a modification as missing+extra, not just `policies`. A proposed 4-way classification reduces exactly to today's counts in **10/10** synthetic cases. |
| **Provenance is cleanly addable** | All **four** snapshot consumers iterate `for key in SNAPSHOT_QUERIES`, never the JSON's own keys. A sibling `_provenance` key breaks nothing. |
| **Two shared-state hazards in the W1C isolated-lane plan** | `bootstrap_pg.py`'s CLI writes the **shared** `staging/pg_dsn.txt`, and `apply_migration.py` is **hardwired** to it. My own W1C step 2 would have repointed the shared DSN. Corrected in §6. |

---

## 1. Fresh baseline and ownership evidence

Measured `2026-10-10 19:11`–`19:3x` local.

| Property | Value |
| --- | --- |
| Branch | `main` |
| Upstream | `origin/main` |
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| `origin/main` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| Ahead / behind | `0 / 0` |
| `git status --porcelain` | **256** entries — `??` 136, ` M` 98, `A ` 12, `M ` 8, `MM` 1, `AM` 1 |
| Unstaged (` M` + `MM` + `AM`) | 100 |
| Staged entries | **22** |
| `.git/index` SHA-256 | `4A23178D4D771F86E8D4EB810FCE192CC6011DCCA9068F39ECC4567B4BFB0F62` |
| `.git/index` mtime | `2026-10-10 19:09:35` |

### 1.1 Staged content identity, not the index hash

The `.git/index` SHA-256 has now moved three times today — `6D965C8E…` (W1C Phase 0) →
`3DBF1CB0…` (W1C close) → `4A23178D…` (now) — purely from git's stat-cache refresh. It is
**not** a usable safety invariant, which is why the brief asked for content identity
instead.

Two content digests were computed, and **both reproduce the values an independent
concurrent session published** in `staging/PHASE11B_PG_PRESERVATION_COORDINATION_REPORT.md`
§1.1:

| Digest | Method | Value | Cross-check |
| --- | --- | --- | --- |
| Canonical | sha256 over `git diff --cached --raw` stdout, CRLF→LF, trailing whitespace stripped, blank lines dropped, LF-joined | `2e994018e375f318f4e6dc9a67ab248e4622de8c426146ca756d5f47d2767152` | **MATCH** |
| Continuity | `git diff --cached --raw` → `Out-File -Encoding utf8` → `Get-FileHash` | `4B807EF26F24CB3505D623F6B2E2E65C2FF9E572089E90E87CDA1B883A94994F` | **MATCH** |
| Blob-list | sha256 over sorted `<blob-oid>  <path>` lines | `7b1e4e7ffeb972d869a6f0bf31f2a8cb5a8f713e17790124aaed052595bd06fc` | new, W1D |

Two sessions, two independent methods, identical results: **the 22 staged entries' paths,
modes and blob SHAs are unchanged.**

> **A limitation of the W1C report, stated.** W1C recorded only staged *paths and
> statuses*, not blob OIDs or a content digest. So W1C could confirm the staged *set* was
> unchanged but could not have proven the staged *content* was. From this report onward a
> content digest exists; the blob-list digest above is recorded so a future session can
> compare without depending on PowerShell's encoding behaviour.

### 1.2 Concurrent ownership

The 22 staged entries remain another workstream's tenancy/RLS/terraform set — unchanged
from W1C: `05-security.yml`, `.kiro/security-template.md`, `ExchangeManager.jsx`,
`core/dependencies.py`, `core/tenant_db_context.py`, `routers/auth.py`,
`routers/exchange.py`, ADR-0006, ADR-0007, `tenant-isolation-matrix.md`,
`ALGO22_Quant_Platform_Review_10032026.md`, `scripts/verify_rls.py`,
`scripts/verify_rls_enforcement.py`, `terraform/{alb,cloudtrail,providers,variables}.tf`,
`tests/test_sc3_aal_claim_propagation.py`, `tests/test_tenant_db_context{,_postgres}.py`,
`tests/test_verify_rls_script.py`, `tests/test_worker_deployment_coverage.py`.

**Heavy concurrent activity** — 30 files written to `staging/` between 19:06 and 19:13 by
at least three other sessions (`a9_*`, `p11b15_*`, `p11bd3_*`), including
`PHASE11B_A14_D5_CONSOLIDATION_REPORT.md` and
`PHASE11B_PG_PRESERVATION_COORDINATION_REPORT.md`.

**No ownership conflict blocking this task:** those sessions write their own artifacts,
the W1D deliverable path is free, the staged set is unchanged, and W1D touches nothing.
Work proceeded. **§6.4 records a genuine overlap** that affects the recommendation.

### 1.3 Recovered-module and guard verification

| Artifact | Expected (W1C) | Measured now | |
| --- | --- | --- | --- |
| `staging/probe_pg_find.py` | 10,716 B / `A40CB392…4A9F` | 10,716 B / `A40CB392…4A9F` | **MATCH** |
| `staging/pg_keepalive_r1.py` | 4,198 B / `09EA51CA…1E80` | 4,198 B / `09EA51CA…1E80` | **MATCH** |
| mtimes | 12:57:29 / 12:18:55 | 12:57:29 / 12:18:55 | **preserved** |
| `tests/test_p10i_…py` vs pre-W1B snapshot `History\-bcdf794\WFse.py` | 23,442 B / `28E7BB19…B7E3` | 23,442 B / `28E7BB19…B7E3` | **IDENTICAL** — snapshot still available |

### 1.4 Artifact state inspected before relying on any historical count

| Path | Size | mtime |
| --- | --- | --- |
| `staging/provisioning_snapshot.json` | 396,325 B | **2026-10-06 20:14:20** (not recaptured) |
| `scripts/provision_database.py` | 35,363 B | 2026-10-06 20:12:33 |
| `scripts/verify_schema_drift.py` | 12,170 B | 2026-10-06 13:17:28 |
| `staging/p11b_w1c_ledger_paradox.txt` | 7,852 B | 2026-10-10 18:52:19 |
| `staging/p11b_w1c_unledgered.txt` | 9,124 B | 2026-10-10 18:53:55 |
| `staging/PHASE11B_A9_POSTGRES_AUTHORIZATION_READINESS.md` | 41,127 B | 2026-10-10 18:40:05 |
| `staging/PHASE11B_W1C_RECOVERY_REPORT.md` | 38,781 B | 2026-10-10 19:01:21 |

---

## 2. Workstream A — migration-ledger reconciliation safety

### 2.1 Root cause of W1C-01, established from source

W1C established *that* migrations 022–025 are applied without ledger rows. W1D establishes
*why*, and it is not a defect in the provisioning path:

| Applier | mtime | Applies | Writes `schema_migrations`? |
| --- | --- | --- | --- |
| `staging/apply_remediation.py` | **10-07 12:25** | `backend_app/migrations/023_remediate_privilege_escalation.sql`, `024_remediate_referral_double_commission.sql` — named explicitly at lines 16–17 | **NO** |
| `staging/apply_025.py` | **10-07 22:27** | `025_work03_bot_ownership_claims.sql` — named at line 15 | **NO** |
| `staging/apply_hardening.py` | 10-06 10:38 | `migrations/hardening/999_privilege_hardening.sql` | **NO** |
| `staging/reprovision.py` | 10-06 10:10 | `DROP SCHEMA public CASCADE` + recreate | **NO** |
| **`staging/apply_migration.py`** | **10-09 22:10** | one file given on argv | **YES** |
| `scripts/provision_database.py` | 10-06 20:12 | the full ordered set | **YES** |

The timeline fits exactly:

```
10-06 20:14   provisioning_snapshot.json captured; its ledger stops at 021
10-06 23:41   022 authored            -> applied by an unidentified path, UNLEDGERED
10-07 12:25   apply_remediation.py authored  (no ledger write)
10-07 12:28   023 authored            -> applied by apply_remediation.py, UNLEDGERED
10-07 12:31   024 authored            -> applied by apply_remediation.py, UNLEDGERED
10-07 22:25   025 authored
10-07 22:27   apply_025.py authored   (no ledger write)
              025                     -> applied by apply_025.py, UNLEDGERED
10-09 ...     026-031 applied via apply_migration.py, LEDGERED (06:07Z .. 22:53Z)
```

**022's applier is not identified.** No script in `staging/` or `scripts/` references
`022_fix_library_subscriptions_updated_at.sql` except W1C's own probe. Its effect is
present. This is the one gap in the causal account and is recorded as **W1D-01**.

### 2.2 Question 1 & 2 — exact SQL, statement by statement

`staging/probe_p11b_w1d_migration_safety.py` → `staging/p11b_w1d_migration_safety.txt`.
Comments stripped; split on `;` at depth 0 with dollar-quoting **and** single-quoted
literals tracked; each statement classified; the **file verdict is the worst statement**,
not an average.

| Migration | bytes | stmts | sha256 | ledger key it would take | **verdict** |
| --- | --- | --- | --- | --- | --- |
| `022_fix_library_subscriptions_updated_at.sql` | 8,438 | 2 | `604b5aaa…1534` | `migrations/022_fix_library_subscriptions_updated_at.sql` | **CONDITIONAL** |
| `023_remediate_privilege_escalation.sql` | 14,492 | 6 | `918f268e…4e63` | `migrations/023_remediate_privilege_escalation.sql` | **CONDITIONAL** |
| `024_remediate_referral_double_commission.sql` | 18,316 | 11 | `2ff5958a…3b1d` | `migrations/024_remediate_referral_double_commission.sql` | **CONDITIONAL** |
| `025_work03_bot_ownership_claims.sql` | 8,839 | 15 | `3dec1233…6e74` | `migrations/025_work03_bot_ownership_claims.sql` | **CONDITIONAL** |

**No statement in any of the four is `DESTRUCTIVE` or `UNSAFE`.** The sole obstacle to
re-execution is PL/pgSQL anonymous blocks whose safety depends on their bodies.

| Migration | SAFE | SAFE-GUARDED | CONDITIONAL | DESTRUCTIVE / UNSAFE | TXN |
| --- | --- | --- | --- | --- | --- |
| 022 | 0 | 0 | **2** `DO $$` | 0 | 0 |
| 023 | 1 `REVOKE` | 2 (`DROP POLICY IF EXISTS`; `CREATE POLICY` paired with it) | **3** `DO $$` | 0 | 0 |
| 024 | 7 (`CREATE UNIQUE INDEX IF NOT EXISTS`, `COMMENT ON`, `CREATE OR REPLACE FUNCTION`, 3× `REVOKE ALL ON FUNCTION`, `GRANT EXECUTE`) | 2 `DROP FUNCTION IF EXISTS` | **2** `DO $$` | 0 | 0 |
| 025 | 11 (`CREATE TABLE IF NOT EXISTS`, 2× `CREATE INDEX IF NOT EXISTS`, 3× `COMMENT ON`, `ALTER TABLE … ENABLE RLS`, `GRANT`, …) | 0 | **2** `DO $$` | 0 | **2** (`BEGIN`/`COMMIT`) |

**Residual to clear before any re-execution: 9 PL/pgSQL blocks** (2 + 3 + 2 + 2). They are
not classified further here because their bodies are control flow, and guessing is what
this exercise exists to avoid.

> **Two defects in my own analyzer, found and fixed mid-analysis.**
> 1. The splitter tracked dollar-quoting but **not single quotes**. A `COMMENT ON INDEX …
>    IS '…one atomic decision.'` in 024 mis-split, and the probe reported a fragment
>    reading `only a unique index ' 'makes the check and the write one atomic decision.'`
>    as `UNCLASSIFIED`. Statement count for 024 was inflated 12 → corrected **11**. An
>    `UNCLASSIFIED` row in a safety matrix is precisely the kind of noise that gets waved
>    through.
> 2. `CREATE POLICY` was scored **UNSAFE** in isolation — correct in general, since
>    PostgreSQL has no `CREATE POLICY IF NOT EXISTS` — but 023 pairs it with a preceding
>    `DROP POLICY IF EXISTS` on the same policy, which makes the pair re-executable. The
>    analyzer now checks for that pairing. 023's file verdict therefore moves **UNSAFE →
>    CONDITIONAL**. The uncorrected version argued for re-running a migration more
>    urgently than the evidence supports.

### 2.3 Question 3 — the exact execution and ledger-recording sequence

`scripts/provision_database.py:460-489`, verbatim structure:

```python
def _apply_sql(conn, path, step, dry_run, already) -> str:
    text   = path.read_text(encoding="utf-8", errors="replace")
    digest = _sha256(text)
    name   = f"{path.parent.name}/{path.name}"        # <-- parent DIRECTORY NAME only

    if name in already:                                # (a) skip-if-recorded
        if already[name] != digest:
            print("REFUSED ... applied previously with a different checksum.")
            sys.exit(4)                                # (b) mutated-file guard
        return "skipped (already applied)"

    if dry_run:
        return "would apply"

    with conn.cursor() as cur:
        try:
            cur.execute(text)                          # (1) EXECUTE THE MIGRATION
            cur.execute(
                "INSERT INTO public.schema_migrations (filename, sha256, step) "
                "VALUES (%s, %s, %s) ON CONFLICT (filename) DO NOTHING",
                (name, digest, step),
            )                                          # (2) RECORD THE FILENAME
            conn.commit()                              # (3) ONE COMMIT FOR BOTH
        except Exception as exc:
            conn.rollback()
            sys.exit(4)
    return "applied"
```

**Answer: yes — SQL executes before the filename is recorded** (step 1 precedes step 2).
`conn.autocommit = False` is set at `:523`, and both statements sit inside one cursor
block before a single `conn.commit()`, so the pair is atomic **provided the migration text
does not commit on its own**. `staging/apply_migration.py:95-107` reproduces this sequence
exactly, including the `ON CONFLICT (filename) DO NOTHING`.

Ledger DDL, `provision_database.py:153-158`:

```sql
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    filename     text        PRIMARY KEY,
    sha256       text        NOT NULL,
    applied_at   timestamptz NOT NULL DEFAULT now(),
    step         text        NOT NULL
);
```

Two files are passed an **empty** `already` set deliberately and are therefore **always
re-applied**: `BUG049_FILE` (`:676`, `already={}`) and `HARDENING_FILE` (`:699`,
`already=set()`). The source explains why at `:685-690` — the hardening file is a
declarative end state. Worth knowing before anyone reasons about "the provisioner skips
what is applied": for these two, it does not.

### 2.4 Question 6 — transaction boundaries, locks, concurrency

| Property | Finding |
| --- | --- |
| Isolation | `conn.autocommit = False` (`:523`); one `conn.commit()` per file |
| **Advisory locks** | **NONE.** No `pg_advisory_lock`, no `LOCK TABLE`, anywhere in `provision_database.py` or `apply_migration.py` |
| Concurrent provisioners | Two runs can interleave. `ON CONFLICT (filename) DO NOTHING` prevents duplicate *rows*; it does **not** prevent duplicate *execution* of the same migration |
| Failure handling | `conn.rollback()` then `sys.exit(4)` — no partial-state report, no resume point |
| Mutated-file guard | `:465-471` refuses and exits 4 if a recorded file's SHA-256 changed. Protects against silent re-application of an edited migration |

**Hazard A — execute-then-record atomicity is broken for most migrations.** Measured
across all 32 numbered files in `backend_app/migrations/`:

| | count |
| --- | --- |
| Files with **no** `COMMIT;` — ledger INSERT atomic with the DDL | **8** (001, 002, 003_preflight, 021, **022**, **023**, **024**, 031*) |
| Files containing their own `COMMIT;` — ledger INSERT lands in a **separate transaction** | **24** (incl. **025**, 026–030, 032) |

For any file in the second group, `cur.execute(text)` commits the DDL and the subsequent
`INSERT` begins a new transaction. An interruption between them leaves the migration
**applied and unrecorded** — exactly the 022–025 condition. So there are **two** distinct
mechanisms: the non-recording appliers (§2.1, explains 023/024/025 and probably 022) and
this atomicity gap (a standing hazard for 24 files). Recorded as **W1D-02**.

**Hazard B — ledger key collision, latent.** The key is
`f"{path.parent.name}/{path.name}"` — the parent *directory name*, not the path. So
`backend_app/migrations/X.sql` and `migrations/X.sql` both record as `migrations/X.sql`.

| Directory | files | key form |
| --- | --- | --- |
| `backend_app/migrations/*.sql` | 38 | `migrations/<name>` |
| `migrations/*.sql` | 11 | `migrations/<name>` |
| **Colliding basenames today** | **0** | — |

Latent, not realised. If it were realised, `_apply_sql` would either skip the second file
as "already applied" or exit 4 on a checksum mismatch — and neither message names the real
problem. Recorded as **W1D-03**. This also explains W1C-07 (45 of 56 ledger rows recording
a path that is not where the file lives): the key was never intended to be a path.

### 2.5 Question 4 — can a missing row be reconciled without re-executing?

**Yes.** `public.schema_migrations` is an ordinary table with four columns and no triggers
on it. Reconciliation needs one `INSERT` per migration, with values that are all already
known from source:

| Column | Value | Source |
| --- | --- | --- |
| `filename` | `migrations/<basename>` | `provision_database.py:463` key format |
| `sha256` | the digests in §2.2 | `_sha256(text)` — computed here, file-only |
| `step` | an operator-chosen label | free text, `NOT NULL` |
| `applied_at` | `DEFAULT now()` | **will be the reconciliation time, not the application time** |

Neither applier exposes a record-only mode: `_apply_sql` has no such branch, and
`apply_migration.py` either skips (if recorded) or executes-then-records. So the INSERT
must be issued directly by the DB owner.

**`applied_at` would be wrong, and must be labelled.** The true application times are
unrecoverable — the migrations ran before any ledger row existed. The `step` value is the
only place to say so, e.g. `reconciled-w1d-unknown-date`. A reconciliation that silently
stamps `now()` creates a ledger that *looks* authoritative about dates it cannot know.

### 2.6 Question 5 — distinguishing already-applied from partially applied

**This is the weakest link in the existing evidence, including W1C's.**

W1C's `probe_p11b_w1c_unledgered.py` used **one marker per migration** — e.g. for 023,
"`anon` has no write grants on `library_strategies`". A single marker cannot distinguish:

- fully applied; from
- partially applied, where the marker statement committed and later ones did not.

For 023 that matters concretely: it contains 6 statements including 3 PL/pgSQL blocks. The
marker covers the `REVOKE` at `:138`. It says nothing about the `DO $$` block protecting
`moderation_status`/`is_active`/`is_featured`, or the policy rewrite at `:121-122`.

**Therefore: "selected effects are present" is not evidence that a migration is fully
applied,** and no claim in this report or W1C's should be read that way.

What would settle it, per migration: a marker **per statement group**, derived from the
file rather than chosen by hand, each a read-only catalogue predicate. Sketch for 023:

| # | Statement group | Read-only predicate |
| --- | --- | --- |
| 1 | `REVOKE … FROM anon` (`:138`) | no `INSERT`/`UPDATE`/`DELETE` grant for `anon` on `library_strategies` |
| 2 | `REVOKE … FROM authenticated` (`:101`) | no `INSERT`/`UPDATE` grant for `authenticated` |
| 3 | `DROP POLICY` + `CREATE POLICY` (`:121-122`) | policy exists **and** `with_check = '(author_id = auth.uid())'` |
| 4 | `DO $$` protected-columns block | the predicate that block asserts, read from its body |
| 5 | `DO $$` function-signature block | as above |
| 6 | `DO $$` leftover-check block | as above |

All four migrations need this before any statement about their completeness is sound. It
is read-only work and needs only a connection. Recorded as **W1D-04**.

### 2.7 Question 7 — smallest safe per-migration procedure

Requires DB-owner approval; **nothing below was executed.**

**Phase 1 — verify completeness (read-only, no approval beyond DB read access)**

1. Build the per-statement predicate set for 022–025 from the files (§2.6).
2. Evaluate them under an enforced read-only session (`set_session(readonly=True,
   autocommit=True)` plus a `SHOW default_transaction_read_only` readback — the pattern
   W1C used, and the `autocommit=True` is load-bearing on psycopg2).
3. Classify each migration **FULLY APPLIED** / **PARTIALLY APPLIED** / **NOT APPLIED**.
4. **Stop** if any is PARTIAL. A partially applied migration must not be ledgered — a row
   would assert a completeness that does not hold, and would make `_apply_sql` skip the
   file forever after.

**Phase 2 — reconcile, only for FULLY APPLIED (DB-owner approval, 1 row each)**

```sql
-- Issued by the DB owner. One statement per migration. No DDL. No re-execution.
INSERT INTO public.schema_migrations (filename, sha256, step) VALUES
  ('migrations/022_fix_library_subscriptions_updated_at.sql',
   '604b5aaac46a4b7e0caad30600ce917a0da5e1cc2355ab94284815f0746e1534',
   'reconciled-w1d-unknown-date'),
  ('migrations/023_remediate_privilege_escalation.sql',
   '918f268ec7ab7936205cc365f848782be92c5bbfb0d0db43454bc9e3e8db4e63',
   'reconciled-w1d-unknown-date'),
  ('migrations/024_remediate_referral_double_commission.sql',
   '2ff5958a1026523b6ca22dfa4f5d4874fe7059511541c7ce71885c4835e33b1d',
   'reconciled-w1d-unknown-date'),
  ('migrations/025_work03_bot_ownership_claims.sql',
   '3dec123362075bd4282536603bf5093893fc4909c171a5180481e685effc6e74',
   'reconciled-w1d-unknown-date')
ON CONFLICT (filename) DO NOTHING;
```

**Why this and not re-execution:** re-execution requires clearing 9 PL/pgSQL blocks by
hand (§2.2) and re-runs a privilege remediation and a money-path function replacement
against a database holding data. The INSERT path touches no schema object. The digests are
file-derived and verifiable by anyone.

**What it does NOT do:** it does not make `applied_at` meaningful, and it does not
retroactively prove the migrations were applied correctly — only that the operator
accepted Phase 1's evidence. Both limits must travel with the change.

**Explicitly excluded:** `032_referral_payouts_terminal_evidence.sql` is unledgered **and
unapplied** (W1C marker: absent). It must go through the normal apply path, not
reconciliation.

### 2.8 Facts from source vs claims needing a live database

| Established by **source inspection alone** (this task) | Requires a **live database** (not done here) |
| --- | --- |
| The three non-recording appliers and which files each names | Whether each migration is fully or partially applied |
| Execute-then-record order; one commit; no advisory lock | Current ledger row count and contents |
| Ledger DDL, PK on `filename`, `ON CONFLICT DO NOTHING` | Whether the 022–025 rows are still absent |
| Ledger key format `parent.name/name`; 0 collisions today | Whether any recorded sha256 now mismatches its file |
| `COMMIT;` counts per migration (8 atomic / 24 not) | Whether a partial application exists |
| Statement classifications and file verdicts | — |
| The four file digests | — |

All live-database figures quoted anywhere in this report are **W1C measurements read from
`staging/p11b_w1c_unledgered.txt` and `staging/p11b_w1c_ledger_paradox.txt`**, and are
**not re-verified here**.

---

## 3. Workstream B — drift-checker accuracy (W1C-02)

### 3.1 The comparison logic, exactly

`scripts/verify_schema_drift.py:191-219`:

```python
def _as_tuples(rows): return [tuple(r) if isinstance(r, list) else r for r in rows]

for key in SNAPSHOT_QUERIES:
    expected = _as_tuples(baseline.get(key, []))
    actual   = _as_tuples(live.get(key, []))
    if expected == actual:          # (A) LIST equality -- order-sensitive
        continue
    missing = [r for r in expected if r not in actual]   # (B) WHOLE-TUPLE membership
    extra   = [r for r in actual   if r not in expected]
    ...
    (advisories if key in ADVISORY_DIMENSIONS else failures).extend(lines)
```

`ADVISORY_DIMENSIONS = frozenset({"recorded_migrations", "alembic_version"})` — a
difference in those two is reported but does not fail the run.

Identical logic appears in `verify_provisioning.py:236` (`diff_snapshots`) and
`verify_production_schema.py:236`, so any conclusion here applies to all three.

### 3.2 Why a changed `with_check` becomes a missing/extra pair

The `policies` query selects **7** columns:

```sql
SELECT tablename, policyname, permissive, roles, cmd,
       coalesce(qual, ''), coalesce(with_check, '')
FROM pg_policies WHERE schemaname = 'public'
ORDER BY tablename, policyname
```

Its natural key is `(tablename, policyname)` — 2 columns, which is also exactly what the
`ORDER BY` uses and what PostgreSQL enforces as unique. The remaining **5 are attributes**.

Line (B) tests `r not in actual` on the **whole 7-tuple**. So migration 023 adding
`WITH CHECK (author_id = auth.uid())` to an existing policy changes column 6 only, and the
same object leaves `expected` and enters `actual` as a different tuple. The checker has no
representation for "same object, different attributes", so it emits both halves with
nothing linking them.

### 3.3 The same issue affects 12 of 16 dimensions

A dimension is vulnerable exactly when its SELECT list is wider than its natural key.
Measured against the live snapshot's own row widths:

| Dimension | cols | key | attrs | Vulnerable |
| --- | --- | --- | --- | --- |
| `tables` | 2 | 1 | 1 | **YES** (owner change) |
| `columns` | 9 | 2 | **7** | **YES** (type, nullability, default, …) |
| `primary_keys` | 3 | 2 | 1 | **YES** (column set) |
| `foreign_keys` | 3 | 2 | 1 | **YES** (definition) |
| `unique_and_check_constraints` | 4 | 2 | 2 | **YES** (definition) |
| `indexes` | 3 | 2 | 1 | **YES** (`indexdef`) |
| `row_level_security` | 3 | 1 | 2 | **YES** (enabled / forced flags) |
| `policies` | 7 | 2 | **5** | **YES** — the observed case |
| `functions` | 3 | 2 | 1 | **YES** — `prosecdef`, the observed 024 case |
| `triggers` | 3 | 2 | 1 | **YES** (function name) |
| `enum_types` | 2 | 1 | 1 | **YES** (label list) |
| `recorded_migrations` | 3 | 1 | 2 | **YES** — `sha256`, `step` (advisory) |
| `privileges` | 3 | **all** | 0 | no |
| `extensions` | 1 | **all** | 0 | no |
| `sequences` | 1 | **all** | 0 | no |
| `alembic_version` | 1 | **all** | 0 | no (advisory) |

**12 of 16.** Two consequences worth stating plainly:

1. **`recorded_migrations` is vulnerable.** A migration file edited after being applied
   changes its `sha256`, and the checker would report it as one missing + one extra rather
   than as a **modified checksum** — which is precisely the condition
   `_apply_sql:465-471` exits 4 on. The most security-relevant difference this tool can
   see is the one it describes least clearly.

2. **It refines W1C's accounting.** In `privileges` the key *is* the whole tuple, so its 5
   "missing" rows are **genuine removals** (023's `REVOKE`s). In `policies` and `functions`
   the key is narrower, so their 1 "missing" each is a **modification counted twice**. Of
   the 7 reported disappearances: **5 real deletions, 2 modifications**. W1C explained all
   7 but described them uniformly as "disappearances"; two of them never disappeared.

### 3.4 A second, latent failure mode

Line (A) is order-sensitive list equality; line (B) is order-insensitive. A dimension whose
rows had identical content in a different order would fall past (A) and then produce
`0 missing, 0 unexpected` — a drift report with no items, counted as a **hard failure**.

Demonstrated on synthetic data:

```
expected = [("a","1"), ("b","2")]      actual = [("b","2"), ("a","1")]
expected == actual  -> False           # fast path NOT taken
missing -> []        extra -> []       # nothing to report
=> "0 missing, 0 unexpected", counted as a hard failure
```

**It cannot fire today:** all 16 queries carry an `ORDER BY` that is a superkey of the
dimension (verified per query). The guarantee is **unenforced** — a new dimension added
without a total `ORDER BY` turns the checker into a false-positive generator. Recorded as
**W1D-05**.

### 3.5 Smallest backwards-compatible improvement

Add a key width per dimension and classify four ways. Pure function, no I/O, no change to
the read-only path:

```python
KEY_WIDTH = {"policies": 2, "functions": 2, "columns": 2, ...,
             "privileges": None}   # None = whole tuple is the key

def classify(expected, actual, key_width):
    if key_width is None:
        e, a = set(expected), set(actual)
        return {"unchanged": e & a, "modified": [], "missing": e - a, "added": a - e}
    exp_by = {tuple(r[:key_width]): r for r in expected}
    act_by = {tuple(r[:key_width]): r for r in actual}
    ...
```

**Compatibility invariant** — today's counts remain derivable, so pass/fail semantics need
not change:

```
missing_today = missing + modified
extra_today   = added   + modified
```

Verified on **10/10** synthetic cases
(`staging/probe_p11b_w1d_drift_accuracy.py` → `staging/p11b_w1d_drift_accuracy.txt`):

| Case | today | proposed | invariant |
| --- | --- | --- | --- |
| policies / unchanged | 0 missing, 0 extra | 1 unchanged | OK |
| **policies / modified `with_check`** (the real W1C-02 case) | 1 missing, 1 extra | **1 modified** — `col[6]: '' -> '(a = b)'` | OK |
| policies / missing | 1 missing, 0 extra | 1 missing | OK |
| policies / added | 0 missing, 1 extra | 1 added | OK |
| **functions / modified `prosecdef`** (the real 024 case) | 1 missing, 1 extra | **1 modified** — `col[2]: False -> True` | OK |
| privileges / genuine removal (key = whole tuple) | 1 missing | 1 unchanged, 1 missing | OK |
| columns / modified nullability | 1 missing, 1 extra | **1 modified** — `col[3]: 'NO' -> 'YES'` | OK |
| **recorded_migrations / modified checksum** | 1 missing, 1 extra | **1 modified** — `col[1]: 'aaaa' -> 'bbbb'` | OK |
| tables / modified owner | 1 missing, 1 extra | **1 modified** — `col[1]: 'postgres' -> 'app_role'` | OK |
| mixed: unchanged + modified + missing + added | 2 missing, 2 extra | 1 / 1 / 1 / 1 | OK |

### 3.6 Proposed regression-test design

All pure data — no database, no fixture, no server — so they run in the default lane.

| # | Test | Asserts |
| --- | --- | --- |
| 1 | `test_identical_rows_are_unchanged_not_drift` | `classify` returns all `unchanged`, nothing else |
| 2 | `test_a_changed_attribute_is_reported_as_modified_once` | policies with differing `with_check` → 1 `modified`, 0 `missing`, 0 `added`, and the diff names `with_check` |
| 3 | `test_a_removed_object_is_missing_not_modified` | 1 `missing`, 0 `modified` |
| 4 | `test_an_added_object_is_added_not_modified` | 1 `added`, 0 `modified` |
| 5 | `test_whole_tuple_key_dimensions_never_report_modified` | parametrised over `privileges`, `extensions`, `sequences`, `alembic_version` → `modified` always empty |
| 6 | `test_counts_reduce_to_the_legacy_output` | for every case: `missing + modified == legacy_missing` and `added + modified == legacy_extra` |
| 7 | `test_every_snapshot_query_has_a_total_order_by` | each `SNAPSHOT_QUERIES` value matches `ORDER BY`, closing W1D-05 |
| 8 | `test_every_dimension_declares_a_key_width` | `set(KEY_WIDTH) == set(SNAPSHOT_QUERIES)` — a new dimension cannot be added without deciding its key |
| 9 | `test_a_mutated_migration_checksum_is_reported_as_modified` | `recorded_migrations` with a changed `sha256` → `modified`, not missing+extra |
| 10 | `test_read_only_enforcement_is_unchanged` | `assert_read_only` still reads back `default_transaction_read_only` and still refuses port 6543 |

Test 10 preserves the existing read-only guarantee explicitly: the proposed change touches
only `compare()`, a pure function, and must not alter `live_snapshot`'s session setup.

**W1C-02 remains OPEN.** This is analysis and a design, not an implementation. No
regression evidence for a fixed checker exists, and the checker was not edited.

---

## 4. Workstream C — snapshot provenance

### 4.1 Current shape and writer

`staging/provisioning_snapshot.json`, 396,325 B, written at
`scripts/verify_provisioning.py:343`:

```python
snap_path.write_text(json.dumps(first, indent=1, sort_keys=True), encoding="utf-8")
```

`first` is the raw dimension dict. The file's **16 top-level keys are all dimension
names**. There is no DSN, data directory, server version, role, capture timestamp or tool
version anywhere in it.

### 4.2 A sibling key is backward compatible — all four consumers verified

| Consumer | How it reads | Affected by a new top-level key? |
| --- | --- | --- |
| `scripts/verify_schema_drift.py:197-199, 266-268` | `for key in SNAPSHOT_QUERIES: baseline.get(key, [])` | **No** |
| `scripts/verify_provisioning.py:236-237` (`diff_snapshots`) | `for key in SNAPSHOT_QUERIES: first.get(key, [])` | **No** |
| `scripts/verify_provisioning.py:339-341` | `for key in SNAPSHOT_QUERIES` | **No** |
| `scripts/verify_production_schema.py:236-237` | `baseline.get(name, [])` | **No** |

**Not one consumer iterates the JSON's own keys.** Every one drives off
`SNAPSHOT_QUERIES` and uses `.get(key, [])`. A sibling `_provenance` object is therefore
invisible to all existing readers, and an old reader given a new file keeps working.

Two placement options:

| Option | Backward compatible | Note |
| --- | --- | --- |
| **A — sibling key `_provenance` in the same file** | **Yes**, proven above | One file; provenance travels with the payload and cannot be separated from it |
| B — sidecar `provisioning_snapshot.provenance.json` | Yes, trivially | Can be lost, which is how the current situation arose |

**Recommend A.** The failure this is meant to prevent is a snapshot whose origin is
unknown; a provenance file that can be deleted independently reintroduces it.

### 4.3 Proposed envelope

```json
{
  "_provenance": {
    "schema_version": 1,
    "captured_at_utc": "2026-10-06T14:44:20Z",
    "database": {
      "host_class": "loopback",
      "port": 51805,
      "database": "postgres",
      "data_directory": "C:/aerora_quant_backend_updated_final1/staging/pgdata_r1",
      "dsn_fingerprint": "sha256:9f2c…(host+port+dbname only, no credentials)"
    },
    "server": {
      "version": "16.2",
      "version_full": "PostgreSQL 16.2 on x86_64-pc-mingw64, …"
    },
    "role": { "name": "postgres", "is_superuser": true, "bypassrls": true },
    "alembic": { "revision": ["d4e5f6a7b8c9"], "head_count": 1 },
    "migration_ledger": { "row_count": 50, "digest": "sha256:…", "max_filename": "migrations/021_…sql" },
    "capture_tool": { "script": "scripts/verify_provisioning.py", "git_head": "074b772a…", "dirty": true },
    "dimension_counts": { "tables": 85, "columns": 1135, "policies": 188, "…": 0 }
  },
  "tables": [ … ],
  "columns": [ … ]
}
```

| Field | Why it earns its place |
| --- | --- |
| `captured_at_utc` | W1D had to date the 10-06 capture by interval arithmetic over its own migration list |
| `data_directory` | the single value that proves *which cluster*; two clusters exist in `staging/` |
| `server.version` | 16.2 locally vs 17.x in production — results are not version-transferable |
| `role.{is_superuser,bypassrls}` | a snapshot of `privileges`/`policies` taken as a BYPASSRLS superuser means something different from one taken as an application role |
| `migration_ledger.digest` + `row_count` | makes "is this baseline older than the database?" a one-line comparison instead of a forensic exercise |
| `capture_tool.git_head` + `dirty` | the snapshot is produced by untracked tooling; without this the generator's own version is unknown |
| `dimension_counts` | lets a consumer detect truncation without parsing 396 KB |

### 4.4 Secret handling

**No connection string, password, token or key appears anywhere in the proposal.**
Specifically:

- `host_class` is the **classification** (`loopback` / `private` / `managed`), not the host;
- `dsn_fingerprint` is a SHA-256 over host + port + database name only, with userinfo
  stripped **before** hashing — enough to tell two baselines apart, not enough to connect;
- `role.name` is a role name, not a credential;
- the full DSN, any `password=` component and any Supabase key are excluded by construction.

This mirrors the handling already used by `tests/real_redis_gate.py`, which reports
variable names and conditions and never echoes an endpoint.

**W1C-03 remains OPEN.** `staging/provisioning_snapshot.json` was **not** recaptured and
not modified — verified 396,325 B / mtime `2026-10-06 20:14:20` at the end of this task.
Writing it requires `scripts/verify_provisioning.py`, which issues `DROP DATABASE` /
`CREATE DATABASE`, so it needs its own approval.

---

## 5. Workstream D — isolated PostgreSQL lane safety review

Validated against current source. **Nothing was installed, created, started or executed.**

### 5.1 What the existing infrastructure already provides

| Requirement | Mechanism in source | Status |
| --- | --- | --- |
| Unique data directory | `bootstrap_pg.resolve_data_dir` — refuses to guess when several clusters exist; `--data-dir` beats `PG_DATA_DIR`; rejects blank values and `..` paths | **MET** |
| Unique port | `pgserver` allocates a fresh random port per start; `_port_from_pid_file` reads `postmaster.pid` line 4 rather than assuming | **MET** |
| Fail-closed DSN handling | `write_dsn_if_safe` — 4 checks: reachable, server-reported `data_directory` matches the request, provisioned-marker test, downgrade refusal; leaves the file byte-identical on any refusal | **MET** |
| Explicit database target validation | `provision_database._validate_dsn` refuses non-loopback without `--i-understand-this-is-remote`, refuses port 6543; `--dry-run` is the default | **MET** |
| Separate non-owner application role | **ABSENT.** `provision_database.py:226-232` creates only `anon NOLOGIN`, `authenticated NOLOGIN`, `service_role NOLOGIN BYPASSRLS`. No non-owner `LOGIN` application role exists anywhere in the repository | **GAP** |
| Least-privilege grants | `ALTER DEFAULT PRIVILEGES` + per-table grants exist for the three Supabase roles; nothing for an application role | **GAP** (follows from the above) |
| Tenant-context wiring | `backend_app/core/tenant_db_context.py` (13,226 B) + `tests/test_tenant_db_context{,_postgres}.py` — **staged by another workstream** | **IN PROGRESS, NOT OURS** |
| Forced RLS | `FORCE ROW LEVEL SECURITY` appears in **no** migration as a statement | **ABSENT by design** — §5.3 |
| Cleanup limited to isolated resources | `bootstrap_pg.stop()` → `_server(data_dir).cleanup()`, scoped to the named directory | **MET** |

### 5.2 Two steps in the W1C plan that would touch shared resources

> **A correction to my own W1C report.** `PHASE11B_W1C_RECOVERY_REPORT.md` §6.3 proposed a
> 7-step isolated lane. Step 2 was
> `python staging/bootstrap_pg.py start --data-dir pgdata_w1c_iso --expect-provisioned`.
> **That writes the shared `staging/pg_dsn.txt`.**

**Hazard D-1 — `bootstrap_pg.py`'s CLI has no DSN-file flag.** `start()` accepts
`dsn_file=None` as a Python parameter (`:352`, defaulting to `DSN_FILE` at `:375`), but
`_build_parser()` exposes only `action`, `--data-dir` and `--expect-provisioned`. So the
CLI always writes `staging/pg_dsn.txt` — the file every PG-gated suite reads.

Partial mitigation, from source: `write_dsn_if_safe`'s downgrade guard would **refuse** to
replace the currently `PROVISIONED` `pgdata_r1` DSN with a freshly `initdb`'d `EMPTY`
cluster, absent `PG_ALLOW_DSN_DOWNGRADE=1`. So the *first* start fails closed. But once the
isolated cluster is provisioned it classifies `PROVISIONED`, and a later start would be
permitted to repoint the shared DSN. **The guard protects the opening step, not the lane.**

**Hazard D-2 — `apply_migration.py` is hardwired to the shared DSN.** Line 61:

```python
dsn = (ROOT / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()
```

No `--dsn`, no environment override. If used for per-migration verification against the
isolated cluster it would apply DDL to **`pgdata_r1`**. This is the single most dangerous
step available to any reconciliation plan, and it is the tool whose name most invites use.

**Which tools can be pointed away from the shared DSN:**

| Tool | Isolated-target capable |
| --- | --- |
| `scripts/provision_database.py` | **Yes** — `--dsn` or `PROVISION_DATABASE_URL`, no hardcoded default |
| `scripts/verify_schema_drift.py` | **Yes** — `--dsn` and `--baseline` |
| `scripts/verify_provisioning.py` | **Yes** — `--dsn` |
| `scripts/verify_rls_enforcement.py` | to be confirmed — another workstream's file, not inspected in depth here |
| `staging/bootstrap_pg.py` | **CLI: no.** Python API: yes (`dsn_file=`) |
| `staging/apply_migration.py` | **No** — hardcoded |

**Corrected lane constraint:** drive every step through an explicit `--dsn` and a
**separate** DSN file; do not use `bootstrap_pg.py`'s CLI for the isolated cluster; do not
use `apply_migration.py` against it at all.

### 5.3 Forced RLS is deliberately absent, and the order of steps depends on that

`backend_app/migrations/026_fee_records_row_level_security.sql:71-78`, verbatim comment:

```
-- ENABLE, NOT FORCE — AND THIS IS WHAT KEEPS THE APPLICATION WORKING
-- `fee_records` is owned by `postgres` (measured: `owner=postgres`), and every DSN in this
-- repository connects as `postgres`. A table owner is EXEMPT from its own policies unless
-- `FORCE ROW LEVEL SECURITY` is set. So `ENABLE` closes the PostgREST surface — where the
-- request runs as `anon` or `authenticated` — and leaves the SQLAlchemy path untouched.
-- `FORCE` would break all six `FeeEngine` methods and is deliberately not used. Same
-- reasoning, same words, as 999 section 1.
```

This refines W1B-05 and my W1C framing materially. The missing `FORCE` is **not an
oversight** — it is a documented decision with a stated cost: the backend path is
intentionally outside RLS, and tenant isolation there rests on application `WHERE`
clauses. `migrations/hardening/999_privilege_hardening.sql` section 1 records the same
reasoning.

**Consequence for the lane:** enabling `FORCE` while the application still connects as the
table owner would break six `FeeEngine` methods. The non-owner role must come first and be
proven working; `FORCE` is only safe *after* the application no longer connects as owner.
W1C's step order (role, then FORCE) was right, but it did not state the cost of getting it
wrong.

### 5.4 Ownership overlap — do not duplicate another workstream's tooling

`scripts/verify_rls_enforcement.py` (22,227 B, **staged `A ` by another workstream**)
already implements the analysis W1B-05 called for. From its source:

- `:24` — "The connected role, and whether it holds BYPASSRLS or SUPERUSER."
- `:197-199` — "BYPASSRLS and SUPERUSER are role-level exemptions. FORCE ROW LEVEL
  SECURITY does … holding BYPASSRLS sees every row on every table no matter how the tables
  are [configured]"
- `:209` — selects `r.rolbypassrls AS has_bypassrls`
- `:228` — "role holds BYPASSRLS - RLS is never applied, FORCE cannot change that."
- `:231` — "role holds neither SUPERUSER nor BYPASSRLS."

**W1D recommends the isolated lane USE this script rather than building an equivalent.**
It is another workstream's deliverable, it is staged, and duplicating it would create two
definitions of "is RLS actually enforced" — the same mistake Workstream I corrected for DSN
writing. Coordination required before either lane proceeds.

### 5.5 Corrected minimal lane (proposal only — NOT executed)

| # | Step | Shared-resource risk |
| --- | --- | --- |
| 1 | `pip install pgserver` | none to the database; **package install needs approval** |
| 2 | Create the cluster via the **Python API** with an explicit `dsn_file`, e.g. `staging/pg_dsn_iso.txt` — **not** the `bootstrap_pg.py` CLI | avoids D-1 |
| 3 | `provision_database.py --dsn <iso> --apply --create-substrate` | `_validate_dsn` refuses non-loopback and 6543 |
| 4 | `CREATE ROLE aerora_app LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE`; grant only needed DML; it must own **nothing** | isolated cluster only; **new work** (§5.1 gap) |
| 5 | Point the application/tests at `aerora_app` and prove them green **before** any `FORCE` | §5.3 |
| 6 | `ALTER TABLE … FORCE ROW LEVEL SECURITY` on tenant-scoped tables | only valid after step 5 |
| 7 | Run the tenant suites **and** `scripts/verify_rls_enforcement.py` against `aerora_app` | §5.4 — coordinate |
| 8 | Stop the cluster, delete the directory and `staging/pg_dsn_iso.txt` | scoped cleanup |
| — | **Never** `apply_migration.py` against the isolated cluster | D-2 |
| — | **Never** `reprovision.py` (`DROP SCHEMA public CASCADE`) without an explicit isolated DSN — it has no `--dsn` guard | new observation |

Disk, re-measured by W1C at ~18:30: **2.90 GiB free** (up from 444 MiB). Sufficient for a
`pgserver` install plus a ~71 MiB cluster. **Not re-measured in this task** — no commands
were run against the filesystem beyond reads.

---

## 6. Required approvals and the smallest next action

| ID | Request | Owner | Status |
| --- | --- | --- | --- |
| **AP-W1D-1** | Evaluate per-statement completeness predicates for 022–025 under an enforced read-only session (§2.7 Phase 1) | DB owner (read access) | **NOT GRANTED** |
| **AP-W1D-2** | Insert 4 ledger rows for migrations verified FULLY APPLIED (§2.7 Phase 2) | DB owner | **NOT GRANTED** |
| **AP-W1D-3** | Implement the 4-way drift classification + the 10 regression tests (§3.5–3.6) | Code owner | **NOT GRANTED** |
| **AP-W1D-4** | Add the `_provenance` envelope and recapture the snapshot (§4) | DB owner + code owner | **NOT GRANTED** |
| **AP-W1D-5** | Install `pgserver`; create the isolated cluster and the `aerora_app` role (§5.5) | Coordinator + DB owner | **NOT GRANTED** |

**Smallest next action, and it needs no database and no approval:**

> **AP-W1D-3's test 7 and test 8 alone** — `test_every_snapshot_query_has_a_total_order_by`
> and `test_every_dimension_declares_a_key_width`. Both are pure static assertions over
> `SNAPSHOT_QUERIES`, both pass today, and together they close W1D-05 and prevent a new
> dimension being added without a declared key. They are the only items in this report that
> can be completed with no live database, no role change and no schema change.

**Second smallest, read-only:** AP-W1D-1. It settles §2.6 — the one question on which every
reconciliation decision depends — and writes nothing.

---

## 7. Findings

| ID | Sev | Finding | Status |
| --- | --- | --- | --- |
| **W1D-01** | **P2** | Migration `022_fix_library_subscriptions_updated_at.sql` is applied with no ledger row and **no identified applier**. No script in `staging/` or `scripts/` references it. 023/024/025 are fully accounted for by `apply_remediation.py` and `apply_025.py`. | OPEN |
| **W1D-02** | **P1** | Execute-then-record is atomic only while the migration does not commit itself. **24 of 32** numbered migrations contain their own `COMMIT;`, so the ledger `INSERT` lands in a separate transaction and an interruption leaves the migration applied and unrecorded. A standing hazard, independent of the non-recording appliers. | OPEN |
| **W1D-03** | **P2** | Ledger key is `f"{path.parent.name}/{path.name}"` — parent directory *name* only. `backend_app/migrations/X.sql` and `migrations/X.sql` collide. **0 collisions today**; latent. Also explains W1C-07. | OPEN |
| **W1D-04** | **P1** | Completeness evidence for 022–025 rests on **one marker per migration**. A single marker cannot distinguish fully applied from partially applied. No claim that these migrations are fully applied is currently supported — including W1C's. | OPEN |
| **W1D-05** | **P2** | `compare()`'s fast path is order-sensitive while its fallback is not; a content-identical but reordered dimension would report `0 missing, 0 unexpected` as a hard failure. Cannot fire today (all 16 queries have a superkey `ORDER BY`) but the invariant is unenforced. | OPEN |
| **W1D-06** | **P1** | `staging/apply_migration.py:61` hardcodes `staging/pg_dsn.txt` with no override. Used against an isolated cluster it would apply DDL to the shared `pgdata_r1`. | OPEN |
| **W1D-07** | **P2** | `staging/bootstrap_pg.py`'s CLI cannot redirect the DSN file; `start()` supports `dsn_file=` only as a Python parameter. The documented CLI always writes the shared DSN. | OPEN |
| **W1D-08** | **P2** | `staging/reprovision.py` issues `DROP SCHEMA public CASCADE` / `DROP SCHEMA auth CASCADE` with no `--dsn` flag and no locality guard visible at its call site. Must never run with the shared DSN recorded. | OPEN |

### 7.1 Prior findings refined

| ID | W1D result |
| --- | --- |
| **W1C-01** | **Root cause established from source** — three non-recording ad-hoc appliers (§2.1), plus W1D-02 as a standing second mechanism. Reconciliation without re-execution is available (§2.5). Still **OPEN pending DB-owner review**. |
| **W1C-02** | **Scoped: 12 of 16 dimensions**, not one. Classification designed and demonstrated 10/10 with a proven compatibility invariant. Still **OPEN pending implementation and regression evidence**. |
| **W1C-03** | **Placement resolved** — all four consumers verified safe against a sibling `_provenance` key. Still **OPEN pending an authorized change**. |
| **W1C-07** (45 of 56 ledger paths wrong) | **Explained** — W1D-03; the key was never a path. |
| W1C's "7 disappearances" | **Refined: 5 genuine deletions + 2 modifications.** Two never disappeared. |
| W1C's "a re-run cannot correct this by itself" (in `p11b_w1c_unledgered.txt`'s printed verdict) | **Wrong, and already corrected** in the W1C report body §5.5 — but the probe log still carries the incorrect sentence. With no row present nothing conflicts, so a re-run *would* insert; it would also re-execute. The log and the report disagree; the report is right. Independently caught by `PHASE11B_PG_PRESERVATION_COORDINATION_REPORT.md` §5.4. |
| **W1B-05 / TENANT-01** | **Refined:** the absent `FORCE ROW LEVEL SECURITY` is a **documented deliberate decision** (026:71-78), not an oversight, with a stated cost — `FORCE` "would break all six `FeeEngine` methods". Still **OPEN**. |

---

## 8. Commands actually run, and their results

Every command was read-only. No database connection was opened by this task.

| # | Command | Result |
| --- | --- | --- |
| 1 | `Test-Path staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md` (×2, incl. immediately before writing) | `False` both times — no collision |
| 2 | `git rev-parse HEAD` / `--abbrev-ref HEAD` / `origin/main` / `@{u}` | `074b772a…`, `main`, same, `origin/main` |
| 3 | `git rev-list --left-right --count origin/main...HEAD` | `0 0` |
| 4 | `git status --porcelain` | 256 entries |
| 5 | `git diff --cached --name-status` | 22 entries, identical to W1C |
| 6 | `git ls-files -s` per staged path | 22 blob OIDs → digest `7b1e4e7f…` |
| 7 | `git diff --cached --raw` + canonical normalisation | `2e994018…` — **matches** the concurrent report |
| 8 | same + `Out-File -Encoding utf8` + `Get-FileHash` | `4B807EF2…` — **matches** the concurrent report |
| 9 | `Get-FileHash` on both recovered modules | sizes and sha256 **match** W1C's record |
| 10 | `Get-FileHash` on `tests/test_p10i_…py` vs `History\-bcdf794\WFse.py` | **identical**, `28E7BB19…` |
| 11 | `Get-ChildItem` on `staging`,`tests`,`scripts`,`backend_app`,`docs` filtered to last 45 min | 30 files from other sessions, 19:06–19:13 |
| 12 | `Get-Content` / `Select-String` on the two concurrent reports | constraints and digests read |
| 13 | `read_file` on `scripts/provision_database.py` `:403-425`, `:440-510` | `_numbered_sql_files`, `_applied`, `_apply_sql`, `main` |
| 14 | `Select-String` on `provision_database.py` for ledger/txn/lock terms | no advisory lock; `already=set()` for 2 files |
| 15 | `read_file` on `scripts/verify_schema_drift.py` `:185-245` | `_as_tuples`, `compare`, `main` |
| 16 | `grep_search` for `SNAPSHOT_QUERIES` / `ADVISORY_DIMENSIONS` / `order by` | all 16 queries + their `ORDER BY` |
| 17 | `read_file` on `staging/apply_migration.py` (full) | execute-then-record; hardcoded DSN at `:61` |
| 18 | `Select-String` on the 6 `staging/apply*|reprovision*` scripts | 3 appliers write no ledger |
| 19 | PowerShell regex count of `BEGIN;`/`COMMIT;`/`DO $$` over `backend_app/migrations/*.sql` | 8 atomic / 24 self-committing |
| 20 | SHA-256 of migrations 022–025 | the four digests in §2.2 |
| 21 | `python staging/probe_p11b_w1d_migration_safety.py` (×2; second after fixing 2 analyzer defects) | statement matrix; all four **CONDITIONAL** |
| 22 | `python staging/probe_p11b_w1d_drift_accuracy.py` | 12/16 vulnerable; **10/10** invariant held |
| 23 | `Select-String` for snapshot consumers and `CREATE ROLE` / `BYPASSRLS` / `FORCE ROW LEVEL SECURITY` | 4 consumers; no app role; FORCE only in comments |
| 24 | `read_file` on `026_…sql:69-85` | the ENABLE-not-FORCE rationale |
| 25 | Final invariant re-check (HEAD, staged digest, module hashes, snapshot, DSN) | all **unchanged** |

**Artifacts created — 2 probes and 2 logs, all under `staging/`, all read-only by
construction:**

| Path | Purpose |
| --- | --- |
| `staging/probe_p11b_w1d_migration_safety.py` | statement-level matrix + the two ledger hazards |
| `staging/p11b_w1d_migration_safety.txt` | its output |
| `staging/probe_p11b_w1d_drift_accuracy.py` | dimension vulnerability + synthetic classification |
| `staging/p11b_w1d_drift_accuracy.txt` | its output |

Neither probe opens a database connection.

---

## 9. Confirmation of no mutation

| Invariant | Phase 0 | Close | |
| --- | --- | --- | --- |
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | same | **unchanged** |
| Branch / upstream | `main` / `origin/main` | same | **unchanged** |
| Ahead / behind | `0 / 0` | `0 / 0` | **unchanged** |
| `git status --porcelain` | 256 | 256 | **unchanged** |
| Staged entries | 22 | 22 | **unchanged** |
| Staged canonical digest | `2e994018…7152` | `2e994018…7152` | **unchanged** |
| `staging/probe_pg_find.py` | 10,716 B `A40CB392…` | same | **unchanged** |
| `staging/pg_keepalive_r1.py` | 4,198 B `09EA51CA…` | same | **unchanged** |
| `tests/test_p10i_…py` | `28E7BB19…` | same | **unchanged** |
| `staging/provisioning_snapshot.json` | 396,325 B / 10-06 20:14:20 | same | **not recaptured** |
| `staging/pg_dsn.txt` | `A746580D…` / 17:55:23 | same | **not written by W1D** |

Explicitly confirmed:

- **No source edit.** The only files created are the 2 probes and their 2 logs, all new
  files under `staging/`. `scripts/verify_schema_drift.py`, `scripts/provision_database.py`,
  the migrations and the tests were **read only**.
- **No staging.** `git add` was never invoked. The 22 staged entries are another
  workstream's and their content digest is byte-identical.
- **No commit, branch, worktree, stash, reset or remote operation.**
- **No database write.** **No database connection of any kind was opened by this task** —
  not even a read-only `SELECT`. Every live-database figure quoted is attributed to W1C's
  artifacts read from disk.
- **No role change.** No `CREATE ROLE`, `ALTER ROLE`, `GRANT` or `REVOKE` was issued.
- **No server operation.** No server started, stopped or restarted; no package installed.
- **No migration applied**, no ledger row inserted, no schema object altered.
- **`staging/PHASE11B_W1C_RECOVERY_REPORT.md` and every other workstream's artifact left
  untouched.** W1D's corrections to W1C are recorded here, not by editing that report.

---

## Final status

| | |
| --- | --- |
| **W1C-01** | **OPEN pending DB-owner review** — root cause now established; reconciliation sequence proposed, not executed |
| **W1C-02** | **OPEN pending implementation and regression evidence** — mechanism scoped to 12/16 dimensions; classification designed and demonstrated only on synthetic data |
| **W1C-03** | **OPEN pending authorized snapshot-provenance change** — envelope proposed; snapshot not recaptured |
| **TENANT-01** | **OPEN** |
| **QUOTA-01** | **OPEN** |
| **PostgreSQL integration lane** | **UNEXECUTED** |
| **Live trading** | **OFF / NOT CERTIFIED** |

Skipped tests are never counted as passes. No approval was inferred from a recommendation,
from silence, or from the existence of a plan.
