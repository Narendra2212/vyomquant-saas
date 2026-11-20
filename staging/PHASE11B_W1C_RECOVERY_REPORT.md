# Phase 11B / Workstream 1C — Recovery report

**Outcome: both missing modules recovered EXACTLY, verified against their surviving
bytecode, and the temporary guard removed. All 27 suspended node IDs pass. Full
collection succeeds with no guard, exit 0.**

Workstream 2 additionally resolves the PostgreSQL drift question that W1B left open, and
in doing so turns up a finding W1B did not have: **four repository migrations are applied
to the live database with no row in `public.schema_migrations`.**

No database was written. No migration was applied. No server was started or restarted. No
role was altered. The shared DSN was not written by this work. Nothing was staged,
committed, pushed or merged.

---

## 1. Phase 0 baseline and ownership

Measured `2026-10-10 16:52` onward, before any edit.

| Property | Value |
| --- | --- |
| Branch | `main` |
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| `origin/main` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| Ahead / behind | `0 / 0` |
| `git status --porcelain` | **256** entries |
| `.git/index` SHA-256 (at Phase 0) | `6D965C8EAFDD23EECCEA41A6BFF25DF5D9F1EE9FC64DFA17C29C965210458613` |
| `.git/index` mtime (at Phase 0) | `2026-10-10 12:51:24` |
| Most recent `git reflog` entry | `2026-10-05 23:40:19` — **no git operation today** |

Entries by status: `??` 136, ` M` 98, `A ` 12, `M ` 8, `MM` 1, `AM` 1.

### 1.1 Concurrent ownership — 22 staged entries belong to another session

The index carries 22 staged entries that are **not W1C's** and were not touched:

```
M .github/workflows/05-security.yml        A .kiro/security-template.md
M algo22-terminal/src/pages/ExchangeManager.jsx
M backend_app/core/dependencies.py         A backend_app/core/tenant_db_context.py
M backend_app/routers/auth.py              M backend_app/routers/exchange.py
A docs/adr/ADR-0006-bridge-tenancy-with-institutional-silo-tier.md
A docs/adr/ADR-0007-per-tenant-credential-encryption-keys.md
A docs/saas-architecture/tenant-isolation-matrix.md
A docs/security-reviews/ALGO22_Quant_Platform_Review_10032026.md
M scripts/verify_rls.py                    A scripts/verify_rls_enforcement.py
M terraform/alb.tf                         A terraform/cloudtrail.tf
M terraform/providers.tf                   M terraform/variables.tf
A tests/test_sc3_aal_claim_propagation.py  A tests/test_tenant_db_context.py
A tests/test_tenant_db_context_postgres.py A tests/test_verify_rls_script.py
A tests/test_worker_deployment_coverage.py
```

This is a tenant-isolation / security workstream mid-flight. **Verified unchanged at the
end of this work**: `git diff --cached --name-status` returns the identical 22 entries with
identical statuses.

The `.git/index` hash moved to `3DBF1CB07E58B1E9FFB1A70154F9C008AD539188F45AB2CBE2B367FC1CAF5676`
during the session. That is git's **stat-cache refresh**, written by `git status` when it
re-stats working-tree files; the staged *content* is byte-identical. `backend_app/routers/exchange.py`
shows as modified-since-staging, which is the pre-existing `MM` entry recorded at Phase 0,
not a W1C change.

Other sessions remained active throughout — see §6.2 for a DSN rewrite at `17:55:23` that
W1C traced and did not cause.

### 1.2 The temporary guard, as found

`tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py`, untracked, 25,760 bytes,
551 lines, sha256 `2CC05D647687E82A7CE30B3F3AA0BAA7E1E12308A4CC2046ADD45FBCF5ED2455`.
W1B's guard occupied **lines 86–131**: a `_missing_under_test()` helper and a
`pytest.skip(..., allow_module_level=True)` conditional on `PROBE_PATH` being absent.

> **Correction to the W1B report.** W1B §3.2 described the insertion as "25,661 → 25,739
> bytes". Both of those are intermediate states of W1B's own successive edits. The editor
> history for this file records the true sequence, and the pre-W1B state was **23,442
> bytes**:
>
> | history entry | size | time | what |
> | --- | --- | --- | --- |
> | `qdpI.py` | 22,116 B | 13:02:51 | Workstream I |
> | `WFse.py` | **23,442 B** | **13:07:42** | **Workstream I, final — the true pre-W1B state** |
> | `Ubd1.py` | 25,620 B | 15:53:25 | W1B guard inserted |
> | `BFUQ.py` | 25,661 B | 15:53:44 | W1B grammar fix |
> | `1bOI.py` | 25,739 B | 15:58:23 | W1B docstring correction |
> | `uxS3.py` | 25,760 B | 15:58:57 | W1B grammar fix |
>
> The guard was therefore **+2,318 bytes**, not +78. W1B understated its own change by a
> factor of 30.

---

## 2. Recovery evidence

### 2.1 Git: exhaustively searched, nothing there

W1B concluded "never committed" from `git log --all` and a `rev-list | ls-tree` sweep.
Both see only **reachable** history, and Phase 0 found two reasons that was not enough:
`git stash list` is **not empty** (W1B recorded it as empty — a second correction), and
one of its entries is a `filter-branch: rewrite` artifact, meaning this repository's
history *was* rewritten and pre-rewrite objects could survive unreachable.

So every object was searched, widest last:

| Pass | Scope | Matches |
| --- | --- | --- |
| 1 | the 31 `.git/lost-found/other` objects, by content | **0** (5 blobs; the only Python one is a dashboard-aggregation test) |
| 2 | all **112** dangling commit trees, by path | **0** |
| 2b | both stash entries (952 paths each) | **0** |
| 3 | **every** object in the database — 18,203 objects, 13,280 blobs, 9,259 read in the 1 KB–60 KB band | **0** |

Fingerprints came from the surviving bytecode, not guesses (`def find_and_record`,
`def candidate_dsns`, `HISTORICAL_PORTS`, `KEEPALIVE: sleeping`, `PG_DSN_NOT_RECORDED`, …).

**Exact recovery from git is impossible.** W1B's conclusion holds, now on far stronger
evidence.

> A note on method: a first version of this search spawned one `git cat-file` per object
> and was still running after 30 minutes having produced nothing. The working version uses
> two processes total — one `--batch-check --batch-all-objects` to enumerate, one
> `--batch` fed candidate IDs on stdin. Recorded because the slow version's silence could
> easily have been mistaken for "nothing found".

### 2.2 Kiro editor local history: both modules found intact

`%APPDATA%\Kiro\User\History` holds 1,339 `entries.json` files. Three reference the
modules in question:

```
History\22750178\entries.json   -> staging/probe_pg_find.py     entry QBL9.py  ts 1791617249744
History\-6acb9fd4\entries.json  -> staging/pg_keepalive_r1.py   entry KgFq.py  ts 1791614935587
History\-bcdf794\entries.json   -> tests/test_p10i_...py        8 entries
```

The history timestamps are the decisive detail. Each, divided by 1000, equals the
`source_mtime_epoch` recorded in the corresponding surviving `.pyc` header **exactly**:

| | history ts (s) | `.pyc` `source_mtime_epoch` | history size | `.pyc` `source_size_bytes` |
| --- | --- | --- | --- | --- |
| `probe_pg_find.py` | **1791617249** | **1791617249** | **10,716** | **10,716** |
| `pg_keepalive_r1.py` | **1791614935** | **1791614935** | **4,198** | **4,198** |

Four independent properties agree per file before a single byte was copied.

### 2.3 Verified against the bytecode after restore

`staging/probe_p11b_w1c_verify_restore.py`, three tests weakest to strongest:

| Test | `probe_pg_find` | `pg_keepalive_r1` |
| --- | --- | --- |
| **[1] header** — recorded size and mtime vs file | MATCH / MATCH | MATCH / MATCH |
| **[2] marshal payload** — bytes | length +39, bytes differ → explained below | length +39, bytes differ → explained below |
| **[3] structural** — every code object: `co_name`, `co_argcount`, `co_kwonlyargcount`, `co_flags`, `co_varnames`, `co_names`, `co_consts` (recursive), full opcode stream | **9/9 identical, all 675 opcodes** | **2/2 identical, all 293 opcodes** |

`Copy-Item` preserved `LastWriteTime`, so the restored files carry the original authoring
timestamps (12:57:29 and 12:18:55), not copy times.

**The +39 bytes, explained.** Two wrong hypotheses were tested and discarded before the
right one:

1. *Marshal layout/interning.* Refuted: `marshal.dumps(marshal.loads(original))` reproduces
   the original's **exact length** (+0) with the untouched original as sole input.
2. *Content difference.* Refuted by test [3].
3. **`co_filename`.** Recompiling with a path relative to the repo root
   (`staging/probe_pg_find.py`) yields **exactly** 12,584 B and 4,832 B — the stored payload
   lengths, to the byte. The +39 is the absolute-path prefix
   `C:\aerora_quant_backend_updated_final1\`, which is 39 characters. The original was
   compiled with a relative filename; the probe passed an absolute one.

   A residual byte difference remains at identical length, and it is marshal's
   reference/interning layout: `dumps(loads(original))` is also the same length as the
   original yet not byte-equal to it. Test [3] deliberately excludes `co_filename` —
   where a file sat when it was compiled is not what the program does.

> **A defect in my own instrument, recorded rather than hidden.** The first run of this
> probe reported `co_flags` mismatches on **all 9** of `probe_pg_find`'s code objects —
> which reads as a failed recovery. The cause was the probe, not the file: the probe module
> carries `from __future__ import annotations` and called `compile(..., dont_inherit=False)`,
> so it leaked its own future into every compile, setting `CO_FUTURE_ANNOTATIONS`.
> `pg_keepalive_r1.py` declares that future itself and so matched; `probe_pg_find.py`
> deliberately does **not** — its own comment explains why — and so every one of its code
> objects flipped. One line (`dont_inherit=True`) turned 9 apparent mismatches into 0. Had
> this gone unexamined it would have been reported as evidence that the recovery was unsafe.

### 2.4 The write-safety contract still fits

`staging/bootstrap_pg.py` was edited at 12:32:32 — **after** the keepalive was authored
(12:18:55) and **before** the probe was (12:57:29). So "this is the exact deleted file" does
not by itself mean "this file still fits the tree". Every name the recovered modules reach
for was resolved by AST against the helper as it exists now:

| Module | Helper names used | TargetState attrs | Blockers |
| --- | --- | --- | --- |
| `probe_pg_find.py` | `DSN_FILE`, `STAGING_DIR`, `DsnWriteRefused`, `_default_probe`, `_same_directory`, `resolve_data_dir`, `write_dsn_if_safe` | `classification`, `data_directory`, `describe`, `error`, `missing_markers`, `public_tables`, `reachable` | **0** |
| `pg_keepalive_r1.py` | `DsnWriteRefused`, `_default_probe`, `write_dsn_if_safe` | `describe`, `error`, `reachable` | **0** |

All 23 of `bootstrap_pg.py`'s top-level names were enumerated; every one needed is present.
Three accesses reach into the sibling's privates (`bp._default_probe` ×2,
`bp._same_directory`) — pre-existing design, noted not changed.

**Write-safety contract, confirmed by independent probe.** `staging/probe_p10i_writer_inventory.py`
(which W1B correctly reported as non-functional while the modules were missing) now runs:

```
1. staging/bootstrap_pg.py write sites (must all be inside write_dsn_if_safe):
   ok  line 436: dsn_file.write_text(...)  in ['write_dsn_if_safe']
2. modules that must contain NO DSN write site:
   ok  staging/probe_pg_find.py: 0 write site(s); delegates=True
   ok  staging/pg_keepalive_r1.py: 0 write site(s); delegates=True
```

So the Workstream I unification property — one definition of "safe to record", held by
`write_dsn_if_safe`, with both other writers delegating — holds for the restored source,
asserted by a probe independent of the test suite.

That probe exits 1 for an unrelated reason: `staging/a7_billing_committed.py` is UTF-16
(`0xFF` BOM), written by another session at 15:42 via a PowerShell redirect, and its
scanner reads UTF-8. Not a Workstream I file and not in scope here.

**`pgserver` is not installed**, so `pg_keepalive_r1.py` cannot *execute* — it imports
`pgserver` at module level. Collection and all tests are unaffected: the suite only
`read_text()`s that file. This is a pre-existing environment gap (§5), not a recovery
defect.

---

## 3. Exact proposed diff, as proposed before any edit

Three changes, stated in full before the first byte was written:

1. Restore `staging/probe_pg_find.py` from `History\22750178\QBL9.py` — 10,716 B, sha256 `A40CB392…4A9F`
2. Restore `staging/pg_keepalive_r1.py` from `History\-6acb9fd4\KgFq.py` — 4,198 B, sha256 `09EA51CA…1E80`
3. Delete the 46-line W1B guard, lines 86–131, from `tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py`

```diff
--- a/tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py
+++ b/tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py
@@ -84,52 +84,6 @@
     return mod
 
 
-def _missing_under_test():
-    """The modules this suite loads BY PATH, and whether they are on disk right now.
-
-    PHASE 11B / WORKSTREAM 1B -- collection-blocker guard. See
-    `staging/PHASE11B_W1B_BASELINE.md`.
-    [... 20 further docstring lines ...]
-    """
-    return [p for p in (PROBE_PATH, KEEPALIVE_PATH) if not p.exists()]
-
-
-_MISSING = _missing_under_test()
-if PROBE_PATH in _MISSING:
-    pytest.skip(
-        "W1B collection-blocker guard: "
-        + ", ".join(f"staging/{p.name}" for p in _MISSING)
-        + (" are" if len(_MISSING) > 1 else " is")
-        + " absent from the working tree and never committed "
-        "(`git log --all` is empty), so there is no revision to check out. "
-        "This suite loads probe_pg_find.py by path at import time, which aborted "
-        "collection for the entire repository. Skipping this module instead. "
-        "THIS IS NOT A PASS: "
-        "the Workstream I DSN-writer unification has no regression evidence while "
-        "this guard is active. Recovery evidence: staging/p11b_w1b_pyc_recovery.txt. "
-        "Details: staging/PHASE11B_W1B_BASELINE.md",
-        allow_module_level=True,
-    )
-
 bp = _load("_p10i_bootstrap_pg", BOOTSTRAP_PATH)
 pf = _load("_p10i_probe_pg_find", PROBE_PATH)
```

**The removal was exact, proven independently of the diff.** The file is now byte-identical
to its pre-W1B history snapshot:

```
WFse.py (13:07:42, pre-W1B)  23,442 B  sha256 28E7BB19A8E3BBD9C9E6646B3450DC3A25A0B2A9D198F6053E3BB25C63C3B7E3
current                      23,442 B  sha256 28E7BB19A8E3BBD9C9E6646B3450DC3A25A0B2A9D198F6053E3BB25C63C3B7E3
IDENTICAL = True
```

Not one byte besides W1B's insertion changed. **No assertion was added, removed, weakened
or re-worded** at any point across W1B and W1C — the file is bit-for-bit what Workstream I
wrote.

---

## 4. Collection results, and the 27 suspended node IDs accounted for

### 4.1 Every suspended node ID, individually

W1B suspended 23 test functions / **27 node IDs**. All 27 now run, and all 27 **PASS**:

```
collected 27 items
test_a_valid_provisioned_candidate_is_recorded                           PASSED
test_the_pid_file_port_is_discovered_without_starting_anything           PASSED
test_a_candidate_serving_a_different_directory_is_not_recorded           PASSED
test_a_sixty_table_cluster_without_markers_is_not_provisioned            PASSED
test_an_empty_candidate_cannot_replace_a_provisioned_dsn                 PASSED
test_all_candidates_unreachable_records_nothing                          PASSED
test_the_dsn_is_preserved_byte_for_byte_on_every_refusal[unreachable]    PASSED
test_the_dsn_is_preserved_byte_for_byte_on_every_refusal[wrong-directory]PASSED
test_the_dsn_is_preserved_byte_for_byte_on_every_refusal[partial-schema] PASSED
test_pg_allow_dsn_downgrade_permits_the_rehearsal_case                   PASSED
test_a_validated_endpoint_replaces_an_unreachable_one                    PASSED
test_the_probe_no_longer_writes_the_dsn_itself                           PASSED
test_every_remaining_dsn_writer_routes_through_the_shared_helper[path0]  PASSED
test_every_remaining_dsn_writer_routes_through_the_shared_helper[path1]  PASSED
test_the_shared_helper_is_the_only_direct_writer                         PASSED
test_no_other_module_writes_the_dsn_file_directly[path0]                 PASSED
test_no_other_module_writes_the_dsn_file_directly[path1]                 PASSED
test_candidate_discovery_is_ordered_and_deduplicated                     PASSED
test_a_missing_pid_file_does_not_break_discovery                         PASSED
test_a_malformed_pid_file_is_ignored                                     PASSED
test_the_cli_parses_the_documented_invocations                           PASSED
test_a_refused_write_maps_to_cli_exit_code_3                             PASSED
test_the_probe_maps_a_refused_write_to_exit_code_3                       PASSED
test_dry_run_never_writes                                                PASSED
test_both_modules_load_by_path_without_sys_modules_registration          PASSED
test_the_probe_resolves_its_bootstrap_sibling_at_runtime                 PASSED
test_this_suite_did_not_touch_the_real_dsn_file                          PASSED
============================= 27 passed in 5.69s ==============================
```

**27 suspended → 27 passed, 0 skipped, 0 failed.** Suspended coverage is fully restored,
including both halves of the parametrised writer-unification tests that depend on
`pg_keepalive_r1.py` — the file W1B noted would still have failed had only the probe been
restored.

### 4.2 Collection without the guard

| Command | Exit | Result |
| --- | --- | --- |
| `python -m pytest --collect-only -q` | **0** | `14496 tests collected in 116.33s`, **no errors, no `Interrupted`** |

Count movement: 14,467 (W1B close, guard active, 0 from this file) → **14,496**. The
restored suite contributes **+27**; the remaining **+2** is concurrent churn from other
sessions. Consistent and fully attributed.

### 4.3 Regression over the affected suites

```
tests/test_p10h_bootstrap_pg_selection_is_explicit.py
tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py
tests/test_p10j_probe_pg_ready_uses_shared_classification.py
tests/test_migration_tooling.py
tests/test_schema_drift_checker_is_read_only_postgres.py
tests/test_orm_models_match_provisioned_schema.py
tests/test_production_security_probe_rolls_back_postgres.py
tests/test_pr_check_postgres_service.py

========== 151 passed, 25 skipped, 1 xfailed, 14 warnings in 28.54s ===========
EXIT 0
```

The P10H/I/J trio alone: **78 passed** (31 + 27 + 20), 0 skipped, 0 failed.

**Pass / fail / skip accounting**

| | Count | Disposition |
| --- | --- | --- |
| Passed | **151** | includes all 27 previously suspended |
| Failed | **0** | — |
| xfailed | **1** | `test_only_one_model_claims_the_positions_table`, pre-existing `strict=True` marker documenting a source-level duplication; unrelated to W1C |
| Skipped | **25** | **all** the shared-cluster PostgreSQL integration lane — 12 in `test_schema_drift_checker_is_read_only_postgres.py`, 13 in `test_production_security_probe_rolls_back_postgres.py`. Every skip reason is the same shape: *"needs a real PostgreSQL instance"*. These suites gate on `TEST_DATABASE_URL`/`DATABASE_URL`, and the local `.env` carries `DATABASE_URL=sqlite:///./test.db`. **Deliberately not run** — W1C is not authorized to execute the shared-cluster lane. These are **not** the suspended 27, which all pass. |

**No skip in this work is counted as a pass.**

---

## 5. The 16 drift dimensions and their evidence

`scripts/verify_schema_drift.py` still exits 1 (`RESULT: DRIFTED`). W1B could not decide
between the tool's own two candidate causes — stale baseline versus out-of-band change —
and recorded that as W1B-06 (no provenance) and W1B-08 (7 unexplained disappearances).
Both are now resolved, read-only.

### 5.1 Provenance, established forensically

The snapshot has **no** metadata fields: all 16 keys are schema dimensions. There is no
DSN, data directory, server version, role or capture time inside it. So provenance was
derived from its contents:

| Evidence | Finding |
| --- | --- |
| `alembic_version` | snapshot `d4e5f6a7b8c9` = live `d4e5f6a7b8c9` — **same revision** |
| Ledger relationship | snapshot's 50 migrations are a **strict subset** of live's 56. **Snapshot-only: 0.** A subset is exactly what a stale capture of the *same* database looks like; items present only in the snapshot would indicate a different database |
| Capture interval, from its own data | newest shared `applied_at` = `2026-10-06 17:02:05Z`; oldest live-only `applied_at` = `2026-10-09 06:07:04Z` ⇒ captured **between** them |
| Independent corroboration | file mtime `2026-10-06 20:14:20` **falls inside** that interval, derived purely from contents |

**W1B-06 is RESOLVED.** The snapshot demonstrably describes this cluster, captured
2026-10-06 ~20:14, three days before migrations 026–031 were applied.

> **Correction to W1B.** W1B argued that `bot_ownership_claims` appearing as an unexpected
> table while migration `025` was not an unexpected ledger row was "compatible with the
> snapshot having come from a different database". That inference was wrong. `025` is not
> in *either* ledger — it is one of the four unledgered migrations in §5.4. The table is
> there because 025 ran; the ledger simply never recorded it.

### 5.2 All 16 dimensions

| # | Dimension | baseline | live | missing | extra | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `tables` | 85 | 89 | 0 | 4 | stale baseline |
| 2 | `columns` | 1135 | 1193 | 0 | 58 | stale baseline |
| 3 | `primary_keys` | 85 | 89 | 0 | 4 | stale baseline |
| 4 | `foreign_keys` | 121 | 123 | 0 | 2 | stale baseline |
| 5 | `unique_and_check_constraints` | 122 | 139 | 0 | 17 | stale baseline |
| 6 | `indexes` | 400 | 424 | 0 | 24 | stale baseline |
| 7 | `row_level_security` | 85 | 89 | 0 | 4 | stale baseline |
| 8 | `policies` | 188 | 195 | **1** | 8 | stale + **1 tool artifact** (§5.3) |
| 9 | `privileges` | 991 | 1007 | **5** | 21 | stale + **unledgered 023** (§5.4) |
| 10 | `extensions` | 1 | 1 | 0 | 0 | **same** |
| 11 | `functions` | 36 | 38 | **1** | 3 | stale + **unledgered 024** (§5.4) |
| 12 | `triggers` | 61 | 61 | 0 | 0 | **same** |
| 13 | `sequences` | 1 | 1 | 0 | 0 | **same** |
| 14 | `enum_types` | 6 | 8 | 0 | 2 | stale baseline |
| 15 | `alembic_version` | 1 | 1 | 0 | 0 | **same** |
| 16 | `recorded_migrations` | 50 | 56 | 0 | 6 | stale baseline |

12 of 16 drift. The 6 live-only ledger rows, with their real timestamps:

```
2026-10-09 06:07:04Z  phase8   026_fee_records_row_level_security.sql
2026-10-09 06:43:48Z  phase8   027_referral_ledger_currency.sql
2026-10-09 16:41:22Z  phase9   028_safety_control_audit.sql
2026-10-09 17:16:50Z  phase9   029_referral_obligations.sql
2026-10-09 18:05:43Z  phase9   030_execution_venue_order_id.sql
2026-10-09 22:53:53Z  phase10  031_fee_records_fee_pct_nullable.sql
```

These account for all additive drift: 4 new tables, 2 new enums (`feestatus`, `feetype`),
`execution_records.venue_order_id`, and the attendant columns, keys, indexes and policies.

### 5.3 One "disappearance" is not one — it is a tool artifact

The policy the tool reports missing **exists**:

| | `policyname` | `permissive` | `roles` | `cmd` | `qual` | `with_check` |
| --- | --- | --- | --- | --- | --- | --- |
| **live** | Users can update their own strategies | PERMISSIVE | `{authenticated}` | UPDATE | `(author_id = auth.uid())` | `(author_id = auth.uid())` |
| **snapshot** | Users can update their own strategies | PERMISSIVE | authenticated | UPDATE | `(author_id = auth.uid())` | *(empty)* |

The only difference is `with_check`, which migration 023 adds. `verify_schema_drift.py`
compares whole tuples, so **a modified row reads as one missing plus one extra** rather
than as a modification. Both halves are the same policy.

That means the tool systematically over-reports modifications as disappearance/addition
pairs — worth knowing before anyone reads a "missing" count as deletions.

### 5.4 The remaining 6 — and the finding behind them

All 6 trace to specific statements, found by matching actual DDL rather than token
presence:

| Item | Responsible statement |
| --- | --- |
| `library_strategies` / `anon` / DELETE, INSERT, UPDATE | `023_remediate_privilege_escalation.sql:138` — `REVOKE INSERT, UPDATE, DELETE ON public.library_strategies FROM anon` |
| `library_strategies` / `authenticated` / INSERT, UPDATE | `023_remediate_privilege_escalation.sql:101` — `REVOKE INSERT, UPDATE ON public.library_strategies FROM authenticated` |
| `process_referral_commission` overload | `024_remediate_referral_double_commission.sql:149,159` — `DROP FUNCTION` then `CREATE OR REPLACE FUNCTION`; live `prosecdef = true` vs baseline `false` |

Live state confirms both took effect: `anon` holds only `SELECT` on `library_strategies`,
`authenticated` holds `DELETE, SELECT`, and the function is `SECURITY DEFINER`.

> **An intermediate result I had to discard.** My first forensics probe reported all 7
> disappearances as "EXPLAINED by 026, 027, 028, 029, 030, 031" — all six late migrations
> for every item. That was token collision: it extracted identifiers from each item and
> looked for *any* of them in the late migrations, and the bare table name
> `library_strategies` occurs in all six. "Explained by everything" is not an explanation,
> and accepting it would have let a genuine out-of-band change pass as drift noise. The
> replacement probe searches for the specific `REVOKE`/`DROP POLICY`/`CREATE OR REPLACE
> FUNCTION` naming both the object and the grantee, across the whole migration set.

**W1B-08 is RESOLVED: 0 unexplained disappearances.** But the explanation produces a new
finding, because neither 023 nor 024 is in *either* ledger.

### 5.5 W1C-01 — four migrations are applied but not ledgered

Each migration's effect was probed directly in the live catalogue:

| Migration | in live ledger | in snapshot ledger | effect in live DB | marker probed |
| --- | --- | --- | --- | --- |
| `022_fix_library_subscriptions_updated_at.sql` | **False** | False | **PRESENT** | trigger on `library_subscriptions` |
| `023_remediate_privilege_escalation.sql` | **False** | False | **PRESENT** | `anon` has no write grants on `library_strategies` |
| `024_remediate_referral_double_commission.sql` | **False** | False | **PRESENT** | `process_referral_commission` is `SECURITY DEFINER` |
| `025_work03_bot_ownership_claims.sql` | **False** | False | **PRESENT** | table `bot_ownership_claims` exists |
| `026`–`031` | True | False | PRESENT | (properly ledgered, §5.2) |
| `032_referral_payouts_terminal_evidence.sql` | False | False | **absent** | correctly not applied |

**Four repository migrations changed the live schema with no row in
`public.schema_migrations`.** The ledger cannot date, attribute or audit them, and
`verify_schema_drift.py`'s `recorded_migrations` dimension is blind to them. The schema is
ahead of its own ledger.

Direction of the change is benign — 023 is a privilege-escalation remediation that *removes*
`anon`/`authenticated` write access, and 024 fixes double-commission. Both are repository
migrations, not dashboard edits. But the evidence chain is broken, and three of the drift
dimensions the tool flags are the visible symptom.

On self-correction: `schema_migrations_pkey` is `PRIMARY KEY (filename)` and
`provision_database.py:480` inserts with `ON CONFLICT (filename) DO NOTHING`. Since no row
exists for 022–025, a re-run **would** insert one — but it would also **re-execute the
SQL**, so idempotence must be established per migration first. That is a provisioning
action and needs approval; W1C did not do it and does not recommend doing it blind.

### 5.6 Corrected disposition of the drift

The drift is **not** a single cause. It is three:

| Cause | Dimensions affected | Severity |
| --- | --- | --- |
| **Stale baseline** — captured 2026-10-06, before 026–031 | all 12 drifting, additive portion | benign; re-capture fixes it |
| **Unledgered migrations 022–025** | `privileges` (5), `functions` (1) | **W1C-01, P1** — real gap in the evidence chain |
| **Tool artifact** — whole-tuple comparison | `policies` (1) | **W1C-02, P2** — modifications reported as missing+extra pairs |

No item is unexplained, and no change was made outside the repository's migration set.
`verify_schema_drift.py`'s "a change was made outside the migration set" branch is
**ruled out**.

---

## 6. Resource and authorization requirements

### 6.1 Re-measured infrastructure

| Property | W1B (≈16:00) | W1C (≈18:30) |
| --- | --- | --- |
| `C:` free | 444 MiB | **2.90 GiB (2,973 MiB)** |
| `pgserver` | ABSENT | **ABSENT** |
| `testing.postgresql` / `pytest_postgresql` | absent | absent |
| `psycopg2` / `alembic` / `sqlalchemy` / `redis` | present | present |
| `pgdata_r1` | live on 51805, 89 tables | **live on 51805, 89 tables, datadir confirmed** |
| `pgdata` | stale pid (56595 dead) | **stale pid (56595 dead)** |
| `pgdata` / `pgdata_r1` on disk | 38 / 71 MiB | 38 / 71 MiB |
| `postgres` processes | — | **6**, in two groups (11:31:08 and 11:31:50); only one cluster answers |

Disk rose by ~2.5 GiB, which **changes the isolated-cluster decision**: W1B ruled option B
out partly on 444 MiB. 2.90 GiB now accommodates a `pgserver` install (~100–300 MB) plus a
provisioned cluster (~71 MiB + WAL).

`pgserver` remaining absent is also why `pg_keepalive_r1.py` cannot execute, and why
`bootstrap_pg.py start` cannot create a cluster, even though `pgdata_r1` is running —
something started it before `pgserver` became unavailable to this interpreter.

### 6.2 A DSN rewrite W1C traced and did not cause

`staging/pg_dsn.txt` mtime moved from `13:17:04` to **`17:55:23`**, content **unchanged**
(sha256 `A746580D…3A96` throughout). Attribution:

- `staging/p11bd3_pgstart.txt` was written at **the same second**, `17:55:23` — a concurrent
  session ran a PG start/bootstrap;
- re-running the P10I suite leaves the mtime at `17:55:23`, unchanged;
- the content is identical, i.e. the shared helper's data-directory check and downgrade
  guard both passed and recorded the same endpoint — the safety contract working as designed.

**W1C did not write the DSN.** Verified identical sha256 at Phase 0 and at close.

### 6.3 Smallest isolated setup — proposed, NOT executed

Requires approval; nothing below was run.

| Step | Action | Why minimal |
| --- | --- | --- |
| 1 | `pip install pgserver` | the only missing tool; `bootstrap_pg.py` already targets it |
| 2 | `python staging/bootstrap_pg.py start --data-dir pgdata_w1c_iso --expect-provisioned` | reuses the existing guarded entry point; a new directory, so `resolve_data_dir`'s ambiguity refusal is satisfied explicitly |
| 3 | `python scripts/provision_database.py --apply --create-substrate` against the isolated DSN only | `_validate_dsn` refuses non-loopback and port 6543; `--dry-run` is the default |
| 4 | **Create a non-owner test role** — `CREATE ROLE aerora_app LOGIN; NOSUPERUSER NOBYPASSRLS`; grant only the DML the application needs; it must **not** own any table | this is the step that does not exist today and is the whole point |
| 5 | `ALTER TABLE … FORCE ROW LEVEL SECURITY` on tenant-scoped tables | closes the owner-exemption half of W1B-05 |
| 6 | Run the tenant suites with `aerora_app`'s DSN | first evidence for the **backend** path rather than only the PostgREST path |
| 7 | `bootstrap_pg.py stop --data-dir pgdata_w1c_iso`, then delete the directory | nothing persists; no baseline needed, so §5's drift question does not arise |

**What it would buy that approval A cannot:** W1B-05 records that production connects as
`postgres` — table owner, `rolbypassrls = true` — with `FORCE` off on all 89 tables, so the
195 RLS policies do not constrain the application backend at all. The tenant suites are
sound but evidence only the `anon`/`authenticated` path. Steps 4–6 are the only route to
testing the backend path, and they are impossible on the shared cluster without altering
roles there — which W1C is forbidden to do and would not recommend.

**Authorization still required, nothing assumed:**

| Request | For | Status |
| --- | --- | --- |
| **A** | run the PG lane against the shared `pgdata_r1` | **NOT GRANTED.** Also now less attractive: §5 shows the baseline is stale and 4 migrations are unledgered, so a green shared-cluster run certifies less than it appears |
| **B** | the isolated cluster above (install `pgserver`, ~300 MB, non-owner role) | **NOT GRANTED.** Disk is no longer the blocker; the install and the role creation are |
| **C** | install a local Redis for the real-Redis lane | **NOT GRANTED.** Unchanged from W1B: no server, no binary, no container runtime, no Redis in WSL |
| **D** | re-capture `provisioning_snapshot.json` with provenance fields, and reconcile the 022–025 ledger gap | **NOT GRANTED.** Both are write operations; `verify_provisioning.py` issues `DROP DATABASE`/`CREATE DATABASE` |

---

## 7. Findings

| ID | Sev | Finding | Status |
| --- | --- | --- | --- |
| **W1C-01** | **P1** | Migrations `022`, `023`, `024`, `025` are applied to the live database with **no row** in `public.schema_migrations`. Effects individually verified in the live catalogue. The ledger cannot date or attribute them; `verify_schema_drift.py`'s `recorded_migrations` dimension is blind to them. Direction is benign (023 removes `anon`/`authenticated` write grants; 024 fixes double-commission) but the evidence chain is broken. Reconciliation re-executes SQL, so per-migration idempotence must be established first. | **OPEN** |
| **W1C-02** | **P2** | `verify_schema_drift.py` compares whole tuples, so a **modified** object reports as one missing plus one extra. The single "missing policy" is present live and differs only in `with_check`. Any reading of its "missing" counts as deletions will be wrong. | **OPEN** |
| **W1C-03** | **P2** | `staging/provisioning_snapshot.json` is 4 days stale and still carries no provenance fields. Provenance was established forensically here, but the next reader has to repeat that work. Re-capture needs approval **D**. | **OPEN** |
| **W1C-04** | **P3** | `staging/a7_billing_committed.py` is UTF-16 (PowerShell redirect default), which breaks `probe_p10i_writer_inventory.py`'s UTF-8 scanner and makes it exit 1 on an otherwise clean inventory. Another session's file. | **OPEN** |
| **W1C-05** | **P3** | `staging/pgdata` carries a stale `postmaster.pid` naming dead port 56595, and 6 `postgres` processes exist in two groups while only one cluster answers. Contained by `resolve_data_dir`'s fail-closed ambiguity refusal. Carried from W1B-19. | **OPEN** |

### 7.1 Prior findings re-checked

| ID | W1C result |
| --- | --- |
| **W1B-01** (P0, both modules absent and never committed) | **RESOLVED.** Exact source recovered from editor local history and verified against the surviving bytecode. Git recovery confirmed impossible by exhaustive object search. |
| **W1B-02** (P0, one missing dependency aborts all collection) | **RESOLVED** for this file; guard removed, collection exit 0. The structural fragility remains: `staging/` is not a package, so module-scope by-path loading is the pattern across P10H/I/J. |
| **W1B-03** (P1, WSI closeout cites a non-existent file) | **RESOLVED.** Both files exist and the unification property is confirmed by test (27/27) and by independent probe (0 write sites, delegates=True). |
| **W1B-04** (P2, toolchain untracked) | **CONFIRMED, unchanged.** Both restored files are `??`. The loss recurred once today and was only recoverable because the editor kept local history — not because the repository did. |
| **W1B-06** (P1, snapshot has no provenance) | **RESOLVED** forensically — §5.1. Same Alembic revision, strict ledger subset, capture interval brackets its own mtime. |
| **W1B-08** (P1, 7 unexplained disappearances) | **RESOLVED** — §5.3–5.4. 1 is a tool artifact, 6 trace to specific DDL in migrations 023/024. **0 unexplained.** |
| W1B's "`bot_ownership_claims` suggests a different database" | **WRONG, corrected.** It is migration 025, applied unledgered. |
| W1B's "`git stash list` empty" | **WRONG, corrected.** Two entries, one a `filter-branch: rewrite` artifact. |
| W1B's "pre-W1B file was 25,661 B" | **WRONG, corrected.** 23,442 B; the guard was +2,318 B, not +78. |
| **W1B-05** (P0, production connects as owner with BYPASSRLS, FORCE off) | **OPEN, unchanged.** Not addressable without approval B step 4–5. **TENANT-01 remains OPEN.** |
| **W1B-09** (P2, 16.2 vs production 17.x) | **OPEN, unchanged.** |
| **W1B-15** (P1, real-Redis lane never executed) | **OPEN, unchanged.** **QUOTA-01 remains OPEN.** |

---

## 8. Final state verification

| Invariant | Phase 0 | Close |
| --- | --- | --- |
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | **same** |
| ahead / behind `origin/main` | `0 / 0` | **same** |
| staged entries | 22 | **22, byte-identical list** |
| `staging/pg_dsn.txt` sha256 | `A746580D…3A96` | **same** |
| public tables / `alembic_version` / ledger rows | 89 / `d4e5f6a7b8c9` / 56 | **89 / `d4e5f6a7b8c9` / 56** |
| commits / pushes / merges / CI runs | — | **none** |
| migrations applied / DB writes / role changes | — | **none** |
| servers started or restarted | — | **none** |

Files changed by W1C: **3** — two restorations (`staging/probe_pg_find.py`,
`staging/pg_keepalive_r1.py`) and one guard removal returning the test to its pre-W1B
sha256. Plus 8 read-only probes and their logs under `staging/`.

| Probe | Log | Purpose |
| --- | --- | --- |
| `probe_p11b_w1c_blob_recovery.py` | — | lost-found + dangling-commit search (passes 1–2) |
| `probe_p11b_w1c_all_objects.py` | `p11b_w1c_all_objects.txt` | exhaustive object-database search |
| `probe_p11b_w1c_contract_check.py` | `p11b_w1c_contract_check.txt` | recovered source vs current helper surface |
| `probe_p11b_w1c_verify_restore.py` | `p11b_w1c_verify_restore.txt` | bytecode verification + payload-delta explanation |
| `probe_p11b_w1c_drift_forensics.py` | `p11b_w1c_drift_forensics.txt` | provenance + all 16 dimensions |
| `probe_p11b_w1c_disappearances.py` | `p11b_w1c_disappearances.txt` | the responsible DDL per disappearance |
| `probe_p11b_w1c_ledger_paradox.py` | `p11b_w1c_ledger_paradox.txt` | ledger-vs-schema reconciliation |
| `probe_p11b_w1c_unledgered.py` | `p11b_w1c_unledgered.txt` | ledger completeness (W1C-01) |
| `probe_p11b_w1c_isolated_remeasure.py` | `p11b_w1c_infra.txt` | disk, tooling, cluster state |

---

Live trading: OFF / NOT CERTIFIED · Production database: NOT ACCESSED · Shared PostgreSQL integration lane: NOT RUN without explicit approval · Real-Redis integration: UNVERIFIED · TENANT-01: OPEN · QUOTA-01: OPEN · Skipped tests: never counted as passes.
