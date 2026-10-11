# Phase 11B / Workstream 1B — PostgreSQL test-write safety matrix

**Scope.** Deliverable 3 of 5. What each PG-related suite would **write** to
`staging/pgdata_r1` if the lane were run, measured **without running any of them**.
No suite in this document was executed. No row was written. The shared cluster was
touched only by the read-only queries recorded in deliverable 2.

**Verdict up front: PASS, with four suites requiring per-suite authorization.**
Write discipline across these suites is markedly better than the raw keyword counts
suggest. The dangerous-looking `TRUNCATE` / `DROP TABLE` hits are, in the majority of
cases, **forbidden-string assertions over migration source text**, not executed SQL.
The real risks are narrower and nameable: 4 suites commit writes to live shared tables
that other suites read.

---

## 1. Method, and what it cannot see

`staging/probe_p11b_w1b_pg_write_matrix.py` (log
`staging/p11b_w1b_pg_write_matrix.txt`) AST-parses every file under `tests/` that
mentions a PG gate token or `psycopg2` — **62 files** — and for each one extracts:

- the **gate**: which variable or DSN file decides whether it runs, and whether absence
  skips, fails, or runs anyway;
- **write operations** by category, from string literals including f-string literal
  segments, with line numbers and the tables named. **Docstrings are excluded by node
  identity**, so prose describing a `DELETE FROM` is not counted as one;
- **commit discipline**: `autocommit = True`, the `autocommit=True` kwarg,
  `ISOLATION_LEVEL_AUTOCOMMIT`, `.commit()`, `.rollback()`, and whether a `rollback()`
  appears in a `finally`;
- **blast radius**: whether each `DELETE`/`UPDATE` carries a `WHERE`, and flagging all
  `TRUNCATE`/`DROP`/`ALTER`/`GRANT` as object-level by nature;
- **role switching**: `SET ROLE` / `SET LOCAL ROLE` / `RESET ROLE`.

### 1.1 Stated limits

| Limit | Consequence |
| --- | --- |
| Static analysis reads literal SQL. SQL assembled from non-literal parts, or issued through an ORM with no literal statement, is invisible to the classifier. | Such files are bucketed **UNKNOWN-ORM**, never scored as safe. 3 files land there. |
| A string literal matching a write verb may be an **assertion about source text**, not an executed statement. | The classifier cannot tell these apart. **Every** `TRUNCATE`/`DROP`/`ALTER`/`GRANT` hit in this document was therefore opened and read by hand. §3 records which were real and which were false positives. |
| "No commit found" is not proof of no persistence — a connection could be left autocommit by a helper the classifier did not follow. | The **UNCOMMITTED** bucket is treated as *unconfirmed*, not safe. Any destructive op in that bucket is escalated for manual confirmation (§4). |
| `conftest.py` fixtures can open connections the test file never names. | Gate attribution is per-file; a suite inheriting a session fixture may write more than its own source shows. |

---

## 2. Summary by risk

62 files scanned.

| Bucket | Count | Meaning |
| --- | --- | --- |
| **DESTRUCTIVE-SHARED** | 8 | commits writes, and at least one is object-level or unscoped |
| **COMMITTED-SCOPED** | 11 | commits writes, all row-scoped by `WHERE` |
| **UNKNOWN-ORM** | 3 | writes through an ORM/dynamic path the classifier cannot read |
| **UNCOMMITTED** | 30 | writes found, no commit or autocommit detected |
| **READ-ONLY** | 10 | no writes in literal SQL and no ORM write hints |

### 2.1 Gate behaviour

Every PG suite examined **skips** when no DSN is available. The two shapes in use:

```python
# module-level skip
pytest.skip("TENANT-01 matrix requires PostgreSQL", allow_module_level=True)

# marker-level skip
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason=...)
```

plus `pytest.importorskip("psycopg2")`. **No suite runs against a default or implicit
DSN**, so there is no path by which an unconfigured machine silently writes to a
database. Gate tokens observed: `pg_dsn.txt`, `TEST_DATABASE_URL`, `DATABASE_URL`,
`bootstrap_pg`.

Two files carry **no gate token at all**:

| File | Note |
| --- | --- |
| `tests/paper_pg_client.py` | a **helper**, not a suite — pytest collects no tests from it; it writes only when a suite calls it with a DSN |
| `tests/test_task_9_5_compile_is_io_free.py` | classified UNCOMMITTED; its matched literals are source-inspection strings, not executed SQL |

---

## 3. The destructive hits, opened and read

This is the section that changes the verdict. Each `TRUNCATE` / `DROP` / `ALTER` /
`GRANT` site was read in source.

### 3.1 FALSE POSITIVES — assertions over migration/application source text

| File | Lines | What is actually there |
| --- | --- | --- |
| `test_kill03_authoritative_global_halt.py` | 1493–1496 | A forbidden-verb tuple scanned against application source: `for verb in ("update public.safety_control_audit", "delete from public.safety_control_audit", "truncate public.safety_control_audit", "drop table public.safety_control_audit"): if verb in lowered: offenders.append(...)`. It **asserts the audit trail is never mutated**. The opposite of a write. |
| `test_money04_referral_ledger_is_single_currency_postgres.py` | 724–725 | `for forbidden in ("DROP TABLE", "DROP COLUMN", "DROP CONSTRAINT", "TRUNCATE", …)` — asserts the migration does none of these. |
| `test_tenant02_fee_records_rls_postgres.py` | 836, 841, 854, 863 | Same pattern against migration 026, plus `assert "DROP POLICY" not in body` and an `assert "TRUNCATE" in raw_upper` that checks 026 still *mentions* TRUNCATE in prose so the test stays meaningful. The file's own docstring at line 731 warns about exactly this confusion. |
| `test_migration_tooling.py` | 553 | `forbidden = ("insert","update","delete","alter","create","drop","truncate","grant")` — a tuple. |
| `test_billing_data_isolation_postgres.py` | 386 | `@pytest.mark.parametrize("privilege", ["TRUNCATE","REFERENCES","TRIGGER"])` — privilege *names*. |

**No TRUNCATE is executed against a live table by any of these.**

### 3.2 REAL executed DDL — all against ephemeral objects

| File | What it creates | Isolation | Teardown |
| --- | --- | --- | --- |
| `test_tenant01_cross_tenant_matrix_postgres.py` | `public."tenant01_canary_<hex8>"`, `public."tenant01_norls_<hex8>"` — `CREATE TABLE`, `ALTER … ENABLE ROW LEVEL SECURITY`, `CREATE POLICY`, `GRANT … TO authenticated` | random per-test name; **never a live table** | `finally: DROP TABLE IF EXISTS … CASCADE` |
| `test_strategy_lifecycle_concurrency_postgres.py` | a whole scratch **schema** `vq_1323_<hex10>` with its own `strategies` / `strategy_deployments` | does not touch `public` at all; `SET LOCAL search_path TO <schema>` | `DROP SCHEMA IF EXISTS … CASCADE` |
| `test_schema_drift_checker_is_read_only_postgres.py` | `public.drift_readonly_ddl_probe` | fixed name, its own probe table | `DROP TABLE IF EXISTS` at both ends |

These are legitimate. The canary table in `test_tenant01` is in fact the **control** that
makes the rest of that suite meaningful — a `USING (true)` policy on a throwaway table,
to prove the harness can see a leak at all.

### 3.3 The residual risk from §3.2: orphans if a run is killed

A `finally` does not run if the process is killed, the host sleeps, or the connection
drops. An orphaned `tenant01_canary_*` table or `vq_1323_*` schema is **permanent**, and
inflates the object count that `scripts/verify_schema_drift.py` compares — so one killed
run makes every later drift check fail.

**Measured now (read-only):** `staging/probe_p11b_w1b_residue.py` →
**0 orphan tables, 0 orphan schemas, 0 unexpected schemas** (9 schemas total: `public`,
`auth`, and the built-ins). So either these suites have never run against this cluster,
or every run completed its teardown. Either way the cluster is currently clean of test
residue, and the drift in deliverable 2 is **not** explained by pollution.

> A correction worth recording: the first version of that sweep checked `public` tables
> only and reported "clean". That claim was not supported by the measurement —
> `test_strategy_lifecycle_concurrency_postgres.py` creates **no** tables in `public`,
> only a schema, which a table sweep cannot see. The sweep was extended before the
> result was used. It also used unescaped LIKE patterns, where `_` is a
> single-character wildcard, producing two false residue hits.

---

## 4. The four suites that genuinely write to live shared tables

These are the ones that matter for approval **A**.

| # | Suite | Persistence | Live tables written | Scoping |
| --- | --- | --- | --- | --- |
| 1 | `test_tenant01_cross_tenant_matrix_postgres.py` | `autocommit = True` @ L102 | `auth.users` (L172), `public.profiles` (L174), **plus one seeded victim row in every tenant-scoped live table the matrix iterates** | seeds then `DELETE … WHERE "<pk>"::text = %s` (L375) |
| 2 | `test_money04_referral_ledger_is_single_currency_postgres.py` | `autocommit = True` @ L141, L505 | `auth.users`, `public.profiles`, `public.referral_codes`, `public.referral_relationships`, `public.referral_commissions`, `public.referral_payouts`, `public.referral_wallets` | `DELETE … WHERE` per fixture tenant (L156–161) |
| 3 | `test_kill03_authoritative_global_halt.py` | `autocommit = True` @ L1440, L1459 | `public.safety_control_audit` — 2 `INSERT`s (L1444, L1463) | **inserts only; no delete of its own rows found** |
| 4 | `test_trade01_concurrent_exposure_postgres.py` | `autocommit = True` @ L108, L723 | `public.execution_records`, `public.profiles`, `auth.users` | fixture teardown `DELETE … WHERE tenant_id = %s` (L122–125, L747–750) |

Plus `test_bug057_listing_moderation_authz_postgres.py` and
`test_lib01_clone_strategy_postgres.py`, which write `public.library_strategies`,
`auth.users`, `public.profiles` with `rollback in finally` and scoped deletes.

### 4.1 Why committed writes are the real concern, not DDL

The cluster **holds data**: `profiles` 57, `exchange_keys` 28, `orders` 28, `positions`
28, `safety_control_audit` 32. Every PG-gated suite shares it, and several agent
sessions are active in this workspace concurrently. Committed cross-suite effects:

1. **`test_kill03` leaves rows behind.** It commits 2 `INSERT`s into
   `public.safety_control_audit` and no matching cleanup was found. That table already
   holds 32 rows, which is consistent with earlier runs having done exactly this. Any
   suite asserting on a *count* in that table is coupled to how many times kill03 has
   run. This is the clearest instance of shared-state coupling in the set.
2. **`auth.users` / `public.profiles` are a shared FK root.** Four suites insert and
   delete there. `test_trade01` deletes `auth.users` rows by `tenant_id`; a UUID
   collision is effectively impossible, but a `DELETE` on a parent row with
   `ON DELETE CASCADE` reaches further than the suite that issued it.
3. **Interleaving.** `autocommit = True` means these writes are visible to every other
   connection immediately. Two suites running concurrently — or one suite running while
   another session reads — can observe each other's fixture rows. The suites were
   written for an exclusive database.

### 4.2 Required conditions for approval A

If the shared `pgdata_r1` lane is authorized, it should be on these terms:

| | Condition |
| --- | --- |
| C1 | **Serial execution.** `-p no:randomly` and no `-n`/xdist. These suites assume exclusive access. |
| C2 | **No other session touching the cluster** for the duration, including other agent tabs. |
| C3 | **Row-count snapshot before and after** for `profiles`, `exchange_keys`, `orders`, `positions`, `safety_control_audit`, `execution_records`, `fee_records`, `referral_*`, plus the object and schema sweep, so any residue is detected rather than discovered later by a drift check. |
| C4 | **`test_kill03_authoritative_global_halt.py` excluded, or its 2 committed audit rows accepted in advance** as a permanent increment. |
| C5 | **No `--lf`/`--ff` reruns** on failure until the post-run snapshot is taken; a partial rerun re-seeds. |
| C6 | **The lane does not satisfy W1B-06.** The drift baseline is still unprovenanced, so a green lane does not certify the cluster's shape. |

---

## 5. Tenant-isolation evidence quality

| Suite | Switches role? | Mechanism | Is a pass RLS evidence? |
| --- | --- | --- | --- |
| `test_tenant01_cross_tenant_matrix_postgres.py` | **Yes** | `SET LOCAL ROLE authenticated` + `set_config('request.jwt.claim.sub', …, true)` (L324–325); `RESET ROLE` on every exit | **Yes — for the PostgREST/browser path** |
| `test_tenant02_fee_records_rls_postgres.py` | **Yes** | `_as_role(cur, role, claim)` (L246); `SAVEPOINT` + `ROLLBACK TO SAVEPOINT` + outer `db.rollback()`; 7 `RESET ROLE` sites | **Yes — for the browser path** |
| `test_bug057_listing_moderation_authz_postgres.py` | **Yes** | 3 role-switch sites; `rollback in finally` | **Yes — for the browser path** |
| `test_cross_tenant_read_sweep_postgres.py` | No | READ-ONLY, no role switch | **No** — runs as `postgres` |
| `test_billing_data_isolation_postgres.py` | via engine | connects as `anon` through a separate engine | **Yes** for the privilege assertions |

### 5.1 The distinction that must be preserved in any citation

| Role | `rolsuper` | `rolbypassrls` | owns the 89 tables | RLS applies? |
| --- | --- | --- | --- | --- |
| `postgres` | True | **True** | **all 89** | **No** |
| `service_role` | False | **True** | 0 | **No** |
| `authenticated` | False | False | 0 | **Yes** |
| `anon` | False | False | 0 | **Yes** |

- Assertions made **after `SET LOCAL ROLE authenticated`/`anon`** do exercise the 195
  policies. `FORCE ROW LEVEL SECURITY` is irrelevant to them, because FORCE governs only
  the owner. **This evidence is valid.**
- Assertions made as the connection's default `postgres` identity exercise nothing.
  `test_tenant01` uses that deliberately for its side-effect check (`L385: # THE
  SIDE-EFFECT CHECK, as the owner`) — a correct use, reading ground truth rather than
  testing a policy.
- **Production connects as `postgres`** (`DATABASE_URL = postgresql://postgres:…@db.<ref>.supabase.co:6543/postgres`),
  i.e. owner with `BYPASSRLS`, against `relforcerowsecurity = 0` on all 89 tables. So
  **the application backend is not constrained by RLS at all.** A green tenant suite is
  evidence about the browser path and **says nothing about the backend path**.

**Therefore TENANT-01 cannot be closed by running this lane.** The suites are sound; the
gap is production's connection role. That is W1B-05, and it is a code/configuration
change, not a test run.

### 5.2 Related privilege exposure, measured

| Grantee | Privileges held in `public` |
| --- | --- |
| `anon` | `SELECT` on 49 tables; `INSERT`/`UPDATE`/`DELETE` on 42 |
| `authenticated` | `SELECT` on 81; `INSERT` on 64; `UPDATE` on 53; `DELETE` on 47 |
| `service_role` | `SELECT`/`INSERT` on 88; `UPDATE` on 77; `DELETE` on 76; **`TRUNCATE` on 86**; `REFERENCES`/`TRIGGER` on 86 |

`service_role` combines `BYPASSRLS` with `TRUNCATE` on 86 tables — unconstrained by any
policy. Recorded as **W1B-10** in deliverable 2.

---

## 6. Full classification

### 6.1 DESTRUCTIVE-SHARED (8) — after manual review

| Suite | Real risk after reading the source |
| --- | --- |
| `test_tenant01_cross_tenant_matrix_postgres.py` | **Real.** Committed seeds in live tables + ephemeral canary DDL. Needs C1–C5. |
| `test_money04_referral_ledger_is_single_currency_postgres.py` | **Real** for the 10 committed `INSERT`s / 7 scoped `DELETE`s. `DROP`/`TRUNCATE` hits are assertion tuples (§3.1). |
| `test_kill03_authoritative_global_halt.py` | **Real, and unrecovered.** 2 committed `INSERT`s into `safety_control_audit` with no cleanup. `TRUNCATE`/`DROP`/`DELETE`/`UPDATE` hits are assertion tuples. |
| `test_tenant02_fee_records_rls_postgres.py` | **Low.** `commit() x3` against `rollback() x16`, savepoint-wrapped, with an explicit test that the temporary `GRANT` does not leak. All `TRUNCATE`/`DROP` hits are assertion tuples. |
| `test_strategy_lifecycle_concurrency_postgres.py` | **Low.** Entirely inside a per-run scratch schema. |
| `test_schema_drift_checker_is_read_only_postgres.py` | **Low.** One fixed-name probe table, dropped at both ends. |
| `test_bug057_listing_moderation_authz_postgres.py` | **Low–medium.** 5 `INSERT`s / 8 `UPDATE`s on `library_strategies`, `auth.users`, `profiles`; `rollback in finally`. |
| `tests/paper_pg_client.py` | **Helper, not a suite.** Writes only when called with a DSN. |

### 6.2 COMMITTED-SCOPED (11)

`test_atomic_order_cancellation_fix.py`,
`test_fee01_tenant_is_validated_not_sentinel_postgres.py`,
`test_fee_third_asset_pct_is_nullable_postgres.py`,
`test_lib01_clone_strategy_postgres.py`,
`test_money01_referral_commission_idempotence_postgres.py`,
`test_paper02_unfunded_order_state_postgres.py`,
`test_referral_payout_schema_contract_postgres.py`,
`test_tenant_db_context_postgres.py`,
`test_trade01_concurrent_exposure_postgres.py`,
`test_trade07_stale_execution_recovery_postgres.py`,
`test_work03_bot_ownership_claims_postgres.py`

All writes carry a `WHERE`. Safe to run under C1–C3.

### 6.3 UNKNOWN-ORM (3) — not classifiable statically

| Suite | Why |
| --- | --- |
| `test_position_delta_race_condition_fix.py` | ORM writes, no literal SQL |
| `test_production_security_probe_rolls_back_postgres.py` | its subject *is* rollback discipline; it statically asserts the probe has no reachable `commit()`, never enables `autocommit`, asserts `not conn.autocommit` before writing, and performs `conn.rollback()` in a `finally`. Strong evidence it does not persist — but established by its own assertions, not by this classifier. |
| `test_transaction_isolation_serializable.py` | isolation-level manipulation through the ORM |

Treat as **unconfirmed**. Run only after reading, or only in the isolated option.

### 6.4 UNCOMMITTED (30) — writes found, no persistence detected

```
test_billing_data_isolation_postgres.py      test_p10j_probe_pg_ready_uses_shared_classification.py
test_bug049_identity_types_postgres.py       test_pgcrypto_preflight_postgres.py
test_bug056_library_subscriptions_updated_at_postgres.py   test_position_lifecycle_postgres.py
test_database_isolation_level_control.py     test_positions_table_column_agreement.py
test_database_isolation_level_postgres.py    test_pr_check_postgres_service.py
test_env03_placeholder_secrets_are_invalid.py  test_referral_money_lifecycle_postgres.py
test_json_extract_dialect_portability.py     test_signup_integration_postgres.py
test_marketplace_aliases.py                  test_sqlite_lane_is_greenable.py
test_marketplace_concurrency.py              test_task_9_5_compile_is_io_free.py
test_migration_tooling.py                    test_tenant_db_context.py
test_money06_referral_obligations_postgres.py  test_tenant_isolation_rls_postgres.py
test_no_secrets_in_bundle.py                 test_tenant_referential_integrity_postgres.py
test_orm_models_match_provisioned_schema.py  test_trade02_kill_switch_fails_closed.py
test_p10d_fee_summary_does_not_mix_denominations_postgres.py
test_p10d_referral_settlement_lifecycle_postgres.py
test_p10h_bootstrap_pg_selection_is_explicit.py
test_p10i_probe_pg_find_uses_shared_dsn_contract.py
```

**Escalated for manual confirmation** — unconfirmed persistence combined with an
unscoped destructive verb:

| Suite | Unscoped verbs | Note |
| --- | --- | --- |
| `test_billing_data_isolation_postgres.py` | `TRUNCATE`, `DELETE`, `UPDATE` | the `TRUNCATE` at L443 **is** executed — as `anon`, expected to be **refused**, and rolled back (L450). Safe by design, but it is a real attempt. |
| `test_migration_tooling.py` | `TRUNCATE`, `GRANT/REVOKE` | assertion tuple (§3.1) |
| `test_pgcrypto_preflight_postgres.py` | `DROP` | unreviewed |
| `test_pr_check_postgres_service.py` | `DROP` | unreviewed |
| `test_tenant_isolation_rls_postgres.py` | `DELETE`, `UPDATE` | unreviewed |
| `test_positions_table_column_agreement.py`, `test_json_extract_dialect_portability.py` | `CREATE` | unreviewed |
| `test_referral_money_lifecycle_postgres.py`, `test_signup_integration_postgres.py`, `test_bug049_identity_types_postgres.py`, `test_tenant_referential_integrity_postgres.py` | `DELETE` | `test_signup_integration` has `rollback in finally` |

Two of these (`test_p10h_…`, `test_p10i_…`) are hermetic `tmp_path` suites that never
open the real DSN, and `test_p10i_…` is currently skipped by the W1B guard
(deliverable 1).

### 6.5 READ-ONLY (10)

`conftest.py`, `real_redis_gate.py`, `test_cross_tenant_read_sweep_postgres.py`,
`test_lib04_a_broken_client_is_503_not_500.py`, `test_marketplace_error_surface.py`,
`test_obs01_readiness_reflects_dependencies.py`,
`test_paper01_botrunner_fills_reach_the_ledger_postgres.py`, `test_quality_issues.py`,
`test_sqlalchemy_pool_events_fix.py`, `test_webhook_duplicate_delivery_real_redis.py`

No writes in literal SQL and no ORM write hints. Safe against the shared cluster.

---

## 7. Blast radius: live tables reachable by committed writes

```
auth.users                      DELETE, INSERT
public.profiles                 DELETE, INSERT
public.orders                   DELETE, INSERT, UPDATE
public.execution_records        DELETE, INSERT, UPDATE
public.fee_records              DELETE, INSERT, UPDATE
public.safety_control_audit     INSERT                 (no cleanup found)
public.referral_codes           DELETE, INSERT
public.referral_commissions     DELETE, INSERT
public.referral_payouts         DELETE, INSERT
public.referral_relationships   DELETE, INSERT
public.referral_wallets         DELETE, INSERT
public.library_strategies       INSERT, UPDATE
public.bot_ownership_claims     DELETE, UPDATE
library_ratings                 DELETE
paper_orders                    UPDATE
strategies                      DELETE, INSERT, UPDATE     (scratch schema)
strategy_deployments            INSERT, UPDATE             (scratch schema)
public.drift_readonly_ddl_probe CREATE                     (probe table)
tenant_probe*, tenant_seq_probe INSERT                     (probe tables)
```

---

## 8. Decision

| Item | Decision |
| --- | --- |
| **PG test-write safety** | **PASS.** Every suite gates on an explicit DSN and skips without one. No `TRUNCATE` reaches a live table. All real DDL is on ephemeral, randomly-named objects with `finally` teardown, and the cluster currently holds **zero** residue. |
| **Shared-cluster execution** | **NOT AUTHORIZED** absent recorded approval — approval request **A**. 4 suites commit writes to live shared tables; 1 (`test_kill03`) leaves rows permanently. Conditions C1–C6 (§4.2). |
| **TENANT-01 closure via this lane** | **NOT POSSIBLE.** The harness is valid for the browser path; production connects as owner with `BYPASSRLS` and `FORCE` is off everywhere, so the backend path has no RLS at all (W1B-05). Running the lane cannot change that. |

---

## 9. Findings

| ID | Sev | Finding |
| --- | --- | --- |
| **W1B-11** | **P1** | `test_kill03_authoritative_global_halt.py` commits 2 `INSERT`s into `public.safety_control_audit` (L1444, L1463, `autocommit=True`) with no cleanup. The table holds 32 rows, consistent with prior runs. Any suite asserting a count there is coupled to kill03's run history. |
| **W1B-12** | **P2** | 4 suites create objects in the shared cluster and rely on `finally` for teardown. A killed run orphans them permanently and makes every later `verify_schema_drift.py` fail. Currently 0 orphans, so this is latent. A pre/post object sweep should be a standing part of the lane. |
| **W1B-13** | **P2** | 3 suites write through ORM/dynamic paths that static analysis cannot classify; their persistence is unconfirmed. `test_production_security_probe_rolls_back_postgres.py` self-asserts rollback discipline convincingly, but by its own assertions. |
| **W1B-14** | **P2** | The local cluster has only 9 schemas (`public`, `auth`, built-ins). Production Supabase additionally carries `storage`, `realtime`, `extensions`, `vault`, `graphql`. Suites touching those are untestable here — a scope limit on this lane beyond the 16.2-vs-17.x version gap (W1B-09). |

---

Live trading: OFF / NOT CERTIFIED · Production database: NOT ACCESSED · Shared PostgreSQL integration lane: NOT RUN without explicit approval · Real-Redis integration: UNVERIFIED · TENANT-01: OPEN · QUOTA-01: OPEN · Skipped tests: never counted as passes.
