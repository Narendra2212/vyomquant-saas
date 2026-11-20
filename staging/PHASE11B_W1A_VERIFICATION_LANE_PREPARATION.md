# PHASE 11B-W1A — VERIFICATION LANE: ANALYSIS AND PREPARATION

**Decision: READY FOR AUTHORIZATION (PostgreSQL lane) · BLOCKED (Redis lane)**, with two
defects that must be fixed first (§6).

**Nothing was provisioned, started, installed, migrated or modified.** No source file was
edited, nothing was staged, committed or pushed. Live trading remained OFF. All database
access was a single server-enforced read-only session.

**QUOTA-01 remains OPEN.** Nothing here closes it.

---

## 0. Headline — the plan is materially safer than the A3 draft, and three A3 items were wrong

| A3 said | W1A measured | Consequence |
|---|---|---|
| Run `scripts/provision_database.py --apply --create-substrate` | The target is **already provisioned and at migration head** — 89 public tables, `alembic_version = d4e5f6a7b8c9` (the single head), **56** rows in `schema_migrations` | **Provisioning is unnecessary.** This removes every destructive operation from the critical path. **Corrected.** |
| Run `staging/run_all_probes.py` | That script runs 12 probes, several of which **MUTATE**: a real `TRUNCATE`/`TRUNCATE CASCADE` loop, and committed `INSERT`/`DELETE` seeding — and `staging/dsn.py` has **no production guard** | **Unsafe as written.** Removed from the plan. **Corrected.** |
| Alembic head unresolved (A3-05, PARTIAL) | **Single head `d4e5f6a7b8c9`**, root `4ef23035a692`, 11 revisions, one merge with a tuple `down_revision` | **A3-05 CLOSED.** The earlier 0-heads result was my own regex artefact, not a cycle. |

**What W1 actually needs is one environment variable**, not a provisioning run.

---

## 1. Baseline and ownership boundaries (VERIFIED)

```
branch / HEAD      main / 074b772ab16cece62cbfca0f2b78497be2ae56d5
origin/main        074b772ab16cece62cbfca0f2b78497be2ae56d5   (ahead 0 / behind 0)
git status         254 entries  —  134 ??, 98 ' M', 12 'A ', 8 'M ', 1 'MM', 1 'AM'
tests collected    14,461  +  1 COLLECTION ERROR (interrupts the run — see W1A-01)
```

### 1.1 The W1 tooling is itself untracked

| File | Exists | Tracked |
|---|---|---|
| `scripts/provision_database.py` | yes (35 KB) | **No** |
| `scripts/verify_schema_drift.py` | yes | **No** |
| `scripts/verify_provisioning.py` | yes | **No** |
| `staging/run_all_probes.py` | yes | **No** |
| `staging/bootstrap_pg.py` | yes (25 KB) | **No** |
| `staging/dsn.py` | yes | **No** |
| `staging/probe_pg_ready.py` | yes | **No** |
| `tests/real_redis_gate.py` | yes | **No** |
| `tests/conftest.py` | yes | yes (` M`) |
| `backend_app/alembic/env.py`, `alembic.ini` | yes | yes |

**The instruments are unreviewed.** Anything they prove is proven by code that has never
passed review or CI. Not a blocker for a local lane; it **is** a blocker for citing the
result as release evidence.

### 1.2 The gated test corpus is mostly untracked

| Lane | Files | Untracked |
|---|---|---|
| PostgreSQL-gated | 38 | **32** |
| real-Redis-gated | 3 | **3** |
| exchange-sandbox | 2 | **2** |

### 1.3 Ownership — files W1 must not touch

| File | Owner |
|---|---|
| `tests/conftest.py` (` M`) | Another tab — a rate-limiter leak guard appended at ~line 269. **Not W1's.** The two lines W1 depends on (`REDIS_URL=""`, `VYOMQUANT_MODE="safe"`) are **already committed on origin/main.** |
| `.github/workflows/01-pr-check.yml` | **Two** uncommitted workstreams share it |
| `staging/pgdata_r1` | **The shared provisioned cluster.** `staging/pg_keepalive_r1.py` keeps it alive and multiple tabs' reports cite it. A destructive probe here disrupts concurrent workstreams. |
| `backend_app/core/cache/redis_manager.py`, `safety_config.py`, `routers/billing.py` | Other tabs |

---

## 2. Phase 1 — PostgreSQL safety assessment

### 2.1 Target identity (VERIFIED, read-only, server-enforced)

My probe **aborted on its first attempt** because `default_transaction_read_only` read
`off` — `psycopg2.set_session(readonly=True)` with `autocommit=False` defers the setting.
The guard fired correctly and nothing was read. Re-run with `autocommit=True` (the pattern
`verify_schema_drift.py` already uses), the server confirmed `on`:

| Property | Value |
|---|---|
| Endpoint | `127.0.0.1:51805`, database `postgres` (credential withheld) |
| Loopback | **confirmed → non-production** |
| `server_version` | **16.2** (production reports 17.x — the versions differ) |
| `data_directory` | `staging/pgdata_r1` |
| Connected role | **`postgres` — the table owner** |
| Postmaster up since | 2026-10-10 06:01:08 UTC |
| Public tables | **89** |

### 2.2 Does it contain data? YES — and it is not disposable by assumption

| Table | Rows |
|---|---|
| `profiles` | 57 |
| `exchange_keys` | 28 |
| `orders` | 28 |
| `positions` | 28 |
| `safety_control_audit` | 32 |
| `execution_records`, `fee_records`, `referral_payouts` | 0 |

These are test fixtures, **but disposability is an ownership question the schema cannot
answer** — and the cluster is shared with concurrent workstreams (§1.3). **Do not infer
disposability from the name `pgdata_r1`.** Treat it as shared state.

### 2.3 Migration state — already at head

| | |
|---|---|
| `alembic_version` | **`d4e5f6a7b8c9`** |
| Repository head (AST-computed) | **`d4e5f6a7b8c9`** — single head, 11 revisions, root `4ef23035a692` |
| **At head** | **TRUE** |
| `schema_migrations` | **56 rows**, 2026-10-06 → 2026-10-09; steps: `step0` 1, `step0b` 10, `step1` 37, `step3b` 1, `step4` 1, plus `phase8` 2 / `phase9` 3 / `phase10` 1 |

### 2.4 RLS posture — TENANT-01 / P11-B09 confirmed on this cluster

| Measure | Value |
|---|---|
| Public tables | 89 |
| RLS **enabled** | **82** |
| RLS **FORCED** | **0** |
| Connected role | `postgres`, the **table owner** |

An owner role bypasses non-forced RLS. **This is the P11-B09 finding, now measured rather
than inferred** — and it means **any tenant-isolation test run on this lane as `postgres`
proves nothing about RLS.** It must be validated through a non-owner role or with RLS
forced. Recorded as **W1A-03**.

### 2.5 What `--apply` and `--create-substrate` actually do

`scripts/provision_database.py`: **`--dry-run` is the default; nothing executes without
`--apply`.** Guards (`_validate_dsn`):

* **Refuses any non-loopback host** unless `--i-understand-this-is-remote`; the host is
  deliberately withheld from the refusal message.
* **Refuses port 6543** (Supabase transaction pooler).
* DSN comes from `--dsn` or `PROVISION_DATABASE_URL`; **never printed**; the alembic
  subprocess output is regex-redacted.
* Per-file SHA-256 in `public.schema_migrations`; a re-run **skips** applied files and
  **refuses** if a file's checksum changed since it was applied.

`--create-substrate` creates the Supabase stand-in: roles `anon`, `authenticated`,
`service_role` (`BYPASSRLS`), schema `auth`, table `auth.users`, functions `auth.uid()`,
`auth.role()`, `auth.jwt()`, `pgcrypto` (or a **non-cryptographic `gen_random_bytes` shim**
when unavailable — documented as acceptable only for a disposable verification database),
and `ALTER DEFAULT PRIVILEGES … GRANT ALL`.

**Destructive verbs it can issue** — the material one is not in the script but in the
migration chain it drives:

> `backend_app/alembic/versions/4ef23035a692_baseline.py::upgrade()` issues
> **`DROP TABLE … CASCADE`** against `orders`, `positions`, `fills`, `transaction_records`,
> `transaction_checkpoints`, `transaction_rollbacks`, `idempotency_keys`.

On a database **already stamped past that revision** alembic will not re-run it — which is
precisely the case here (`alembic_version = d4e5f6a7b8c9`). On an un-stamped database with
data in those tables, `alembic upgrade head` **would drop them**. That is the single most
important safety fact in this assessment.

Also present: `scripts/verify_provisioning.py` performs **`DROP DATABASE` / `CREATE
DATABASE`** with a **substring-only locality guard**. **Excluded from this plan entirely.**

### 2.6 Re-run safety

Idempotent by construction, with one deliberate exception and one fixed defect:

* Checksum tracking skips applied files.
* `STEP 4` privilege hardening is **always re-applied** (declarative end state; every
  statement is `REVOKE`/`GRANT`/`ENABLE RLS`/`DROP POLICY IF EXISTS` + `CREATE POLICY`).
* A **real past defect**, fixed and documented in-source: a `GRANT ALL ON ALL TABLES`
  in the substrate made run 2 **more permissive** than run 1 by **787 privilege rows**,
  including `anon` holding DELETE/INSERT/SELECT/REFERENCES on `alembic_version`. Removed.
* **No test under `tests/` covers provisioner idempotency** — the only evidence is the
  manually-run `verify_provisioning.py` harness.

### 2.7 Is drift verification read-only? YES — and it proves it

`scripts/verify_schema_drift.py` opens `set_session(readonly=True, autocommit=True)`, then
**reads `SHOW default_transaction_read_only` back from the server** and aborts unless it is
`on` (because a pooler can swallow session settings). It refuses port 6543. It compares 16
schema dimensions against the committed `staging/provisioning_snapshot.json`. **Row data is
never read.** `redact()` keeps scheme/host/port/db only.

### 2.8 Can the guards prevent production targeting? Mostly — with two gaps

| Instrument | Guard | Verdict |
|---|---|---|
| `provision_database.py` | loopback-only + 6543 refusal + checksums | **Strong** |
| `verify_schema_drift.py` | read-only proven by read-back + 6543 refusal | **Strong** |
| `bootstrap_pg.py` | explicit data-dir, fail-closed inference, DSN written **last** and only after the server confirms its own `data_directory`, **never downgraded** (`PG_ALLOW_DSN_DOWNGRADE` override) | **Strong** |
| **`staging/dsn.py`** | announces its source, redacts — but **no production guard of any kind** | **GAP → W1A-02** |
| **`backend_app/alembic/env.py`** | `load_dotenv()` + `os.getenv("DATABASE_URL")`, **no guards**; `alembic.ini` defaults to SQLite | **GAP → W1A-04** |

---

## 3. Phase 2 — Redis safety and test assessment

### 3.1 Endpoint: UNSET and UNREACHABLE

`AERORA_TEST_REDIS_URL` unset · `REDIS_URL` unset · `127.0.0.1:6379`, `:6380`, `:6399` all
closed · no `redis-server`, `redis-cli`, `docker`, `podman`, `memurai`; WSL present but
reports `NO_REDIS_SERVER`. `redis-py 5.0.8` **is** installed, so the client is not the
blocker. `staging/redis/` is absent although the runbook documents that path.

### 3.2 Does the gate distinguish an intentional skip from a broken endpoint? YES

`tests/real_redis_gate.py` reads **`AERORA_TEST_REDIS_URL` only** (no `REDIS_URL`
fallback — verified by AST: zero code-level `"REDIS_URL"` constants) and decides three
states: **unset → SKIP · set-but-malformed/unreachable/non-loopback → FAIL · reachable →
RUN**. Reachability is a real `PING`. Non-loopback is refused unless
`AERORA_TEST_REDIS_ALLOW_REMOTE=1`. Reasons name variables, never values.

**Divergence, documented not changed:** `tests/test_webhook_duplicate_delivery_real_redis.py`
uses its own `_redis_reachable()` and **SKIPS** on a configured-but-unreachable endpoint
where the quota gates **FAIL**. A dead CI Redis would make its six tests read as "nobody
asked".

### 3.3 Can the mock silently substitute after a passing gate? Analysed — and the answer differs per file

This is the sharpest question in Phase 2, because `tests/conftest.py` sets `DEV_MODE=true`
and `redis_manager._get_active_cache()` returns `self._mock_client` whenever `DEV_MODE` is
set and `_dev_mode_bypass` is false.

| File | Path to Redis | Mock substitution possible? |
|---|---|---|
| `test_webhook_duplicate_delivery_real_redis.py` | Calls **`redis_manager._bypass_dev_mode(True)`**, restores `REDIS_URL`, then **asserts** the resolved cache is not `None` and not a `MockRedisClient` | **No — and it fails loudly if it were.** Correct by construction. |
| `test_quota01_metered_reservation_is_hard.py`, `test_quota01_exchange_slot_is_atomic.py` | Build their **own** client and inject a `_RealCache` wrapper over `sd.redis_manager` | **No** — but they **bypass the application facade entirely** |

**The scope limitation that follows, stated precisely:** the quota real-Redis tests prove
the **lock/meter primitive's** cross-process semantics against a real server. They do **not**
prove that the production facade path reaches Redis. Only the webhook test exercises
`_get_active_cache()` → real client, and it is therefore the test that would close
**`P11-U04`'s Lua half** (`MockRedisClient.eval_lua` ignores its `script` argument, so the
production Lua has never executed). **Sequencing consequence: the webhook test matters more
than its severity suggests.**

### 3.4 Isolation and cleanup

Namespaced keys (`q01-slot-*`, `q01-real-*`, and the webhook file's own `created` list),
clients closed in `finally`, and **no `FLUSHDB`/`FLUSHALL` anywhere** in the gate or any of
the three files (verified). The quota tests also target database index **15** when CI
supplies `redis://localhost:6379/15`.

### 3.5 External calls and shared state

No gated Redis test contacts an exchange, a payment provider or any external endpoint. The
webhook tests construct a `_FakeRequest` and verify signatures locally. No application
configuration change is required for any of the three files to run — only the env var.

### 3.6 Is the CI Redis service usable without new infrastructure? YES

`01-pr-check.yml`'s `unit-tests` job already runs `redis:7-alpine`, health-gated
(`redis service is healthy` observed in a real job log). **But the tests are uncommitted,
so CI has never seen them** — the D5′ CI question is already answered **UNVERIFIED** by a
concurrent tab in `staging/PHASE11B_A3_CI_RUN_EVIDENCE.md`. Not re-litigated here.

---

## 4. Phase 3 — Test-lane inventory

| Category | Count | Basis |
|---|---|---|
| Tests collected | **14,461** | executed `--collect-only` |
| **Collection errors** | **1 — interrupts the whole run** | `tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py` → `FileNotFoundError: staging/probe_pg_find.py` |
| PostgreSQL-gated files | 38 (32 untracked) | source inspection |
| real-Redis-gated files | 3 (3 untracked) | source inspection |
| exchange-sandbox files | 2 (2 untracked) | source inspection |
| Sandbox-credential-dependent | 16 scenario tests | all skip; `AERORA_SANDBOX_*` unset |
| Cannot safely run here | `verify_provisioning.py` (DROP/CREATE DATABASE); `run_all_probes.py` mutating members | source inspection |

**Evidence class, stated per the brief:**

* **Actual execution** in W1A: the `--collect-only` count, `bootstrap_pg.py verify`, and one
  read-only SQL session. Nothing else.
* **Source inspection**: every safety claim about the provisioner, drift checker, gate and
  mutating probes.
* **Mocked coverage**: everything in the default lane. It is not evidence of real-service
  behaviour, and no skip anywhere in this document is counted as a pass.

---

## 5. Phase 4 — Proposed execution plan (NOT EXECUTED)

Ordered so that each stage is abort-safe and no stage mutates shared state.

### Stage 0 — fix the blockers (required; see §6)

W1A-01 must be fixed or the suite cannot complete a run at all.

### Stage 1 — confirm the target, change nothing

```powershell
python staging/bootstrap_pg.py verify
python scripts/verify_schema_drift.py          # read-only; defaults to staging/pg_dsn.txt
```

**Side effects:** none. `verify` only reports; the drift checker proves its own session is
read-only before issuing a query.
**Abort if:** `verify` reports anything other than `PROVISIONED` with
`data_directory=.../pgdata_r1`; or drift reports hard failures (shape has moved — do not
run tests against a drifted schema and call it evidence).

### Stage 2 — run the PostgreSQL lane (read-committed, no provisioning)

```powershell
$env:AERORA_TEST_DATABASE_URL = (Get-Content staging/pg_dsn.txt -Raw).Trim()
python -m pytest <the 38 PG-gated files> -p no:randomly -q --no-header `
    -rfsxX --timeout=900 --junitxml=staging/w1_pg_lane.xml
Remove-Item Env:\AERORA_TEST_DATABASE_URL
```

**Side effects:** the tests themselves write to the shared cluster (most use savepoints and
roll back; some commit). **This is the one stage that touches shared state** — §6 W1A-05.
**Abort if:** any test is observed truncating or dropping a table it does not own, or the
cluster's table count changes.
**Explicitly NOT run:** `scripts/provision_database.py` (unnecessary — already at head),
`scripts/verify_provisioning.py` (DROP/CREATE DATABASE), `staging/run_all_probes.py`
(mutating members).

### Stage 3 — run the Redis lane  ◀ BLOCKED

```powershell
# requires an authorized isolated server; NOT installed here
.\staging\redis\redis-server.exe --port 6399 --appendonly no --save ""   # runbook pattern
$env:AERORA_TEST_REDIS_URL = "redis://127.0.0.1:6399/15"
python -m pytest tests/test_quota01_metered_reservation_is_hard.py `
    tests/test_quota01_exchange_slot_is_atomic.py `
    tests/test_webhook_duplicate_delivery_real_redis.py `
    -p no:randomly -q --no-header -rfsxX --timeout=300
Remove-Item Env:\AERORA_TEST_REDIS_URL
```

**Side effects:** keys under `q01-*` and the webhook file's own namespace, on DB index 15,
cleaned up in `finally`. No `FLUSHDB`.
**Abort if:** the gate reports `fail` for a reason other than reachability (that would mean
a misconfiguration, not an absent server).

### Stage 4 — record, do not interpret

Fill the §7 ledger. **Do not update any launch-gate status** without the coordinator.

---

## 6. Defects that must be fixed before the lane yields meaningful evidence

| ID | Sev | Defect | Evidence | Why it blocks | Fix owner |
|---|---|---|---|---|---|
| **W1A-01** | **P0 for the lane** | **Collection aborts.** `tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py` raises `FileNotFoundError: staging/probe_pg_find.py` and pytest reports `Interrupted: 1 error during collection`. 14,461 tests collected, **0 runnable in one invocation.** | executed `--collect-only` | No full-lane run can complete. Either restore `staging/probe_pg_find.py` (referenced by `staging/dsn.py`'s own error text, so it is expected to exist) or deselect the file — **deselection must be recorded, not silent** | Platform |
| **W1A-02** | P1 | **`staging/dsn.py` has no production guard.** It resolves `AERORA_TEST_DATABASE_URL` → `staging/pg_dsn.txt` → exit, announcing and redacting, but never checks the host. Every mutating probe in `staging/` inherits that. | source: `resolve_dsn()` | A stale `AERORA_TEST_DATABASE_URL` from a parent shell + a mutating probe = committed writes to an unintended database. Its own docstring names this as "the worst possible output of this tooling". Add a loopback check or an explicit remote opt-in, matching `provision_database.py` | Platform |
| **W1A-03** | **P0 for TENANT-01 evidence** | **The lane connects as the table owner with `FORCE ROW LEVEL SECURITY` on 0 of 89 tables.** Measured: RLS enabled 82, forced **0**, role `postgres`. | read-only probe | Any tenant-isolation test passing here **proves nothing about RLS** — the owner bypasses it. The brief forbids validating isolation with a privileged role. Needs a non-owner test role or forced RLS **before** `P11-B09` evidence is claimed | Security + DB |
| **W1A-04** | P1 | **`alembic env.py` reads `DATABASE_URL` from `.env` with no guard**, and `.env` **does** carry a `DATABASE_URL`. `alembic.ini` defaults to SQLite. | source + A3 probe | A bare `alembic upgrade head` targets whatever `.env` names — and the root revision issues `DROP TABLE … CASCADE`. `provision_database.py` is safe because it passes `DATABASE_URL=dsn` explicitly to the subprocess; a human running alembic directly is not | DB owner |
| **W1A-05** | P2 | **The target cluster is shared with concurrent workstreams** (`pgdata_r1`, kept alive by `staging/pg_keepalive_r1.py`, cited by multiple tabs' reports) and **holds data** (57 profiles, 28 exchange_keys/orders/positions, 32 audit rows). | read-only probe | Stage 2 writes to it. Either accept the interference window with the other tabs' agreement, or stand up a second cluster — which needs a data dir and the `PG_ALLOW_DSN_DOWNGRADE` discipline | Coordinator |
| **W1A-06** | P2 | **All W1 instruments are untracked** (provisioner, drift checker, bootstrap, dsn, gate) and **32 of 38 PG-gated test files** are untracked. | `git ls-files` | Results are produced by unreviewed code. Fine for a local lane; **not citable as release evidence** until committed | Coordinator |

---

## 7. Test-result ledger template

One row per test file. **A skip is never a pass.** `Target` must name the real substrate.

```
| File | Lane | Target | Collected | Passed | Failed | Skipped | XFail | Exit | Skip reason | Owner | Classification |
|------|------|--------|-----------|--------|--------|---------|-------|------|-------------|-------|----------------|
```

`Classification` ∈ VERIFIED · PARTIAL · BLOCKED · FAILED · NOT CLASSIFIED.

Per-lane header to record:

```
Lane            : postgres | real-redis
Target identity : server_version, data_directory, current_database, connected role
Migration state : alembic_version  (must equal d4e5f6a7b8c9)
Drift check     : pass | hard failures (list)
RLS posture     : enabled N / forced N / role        <-- governs W1A-03
Command         : <verbatim, credentials redacted>
Exit code       : <actual>
Totals          : passed / failed / skipped / xfailed / errors
Unexecuted      : <files that never ran, and why>
```

Mandatory footer, per the brief:

```
Live trading OFF. No production target contacted. Skips are NOT passes.
A local PostgreSQL result is NOT production schema verification.
QUOTA-01 remains OPEN.
```

---

## 8. Minimum approvals required to execute

| # | Approval | Needed for | Risk if granted |
|---|---|---|---|
| **A-1** | Fix or record-and-deselect `W1A-01` (one test file / one missing probe script) | Any lane run | Low — restores a reference its own tooling expects |
| **A-2** | Run the PG-gated lane against the **shared** `pgdata_r1` cluster, accepting an interference window with concurrent tabs | Stage 2 | Low-medium — tests write; most roll back. Coordinate with the other tabs. |
| **A-3** | Obtain and start a **local, isolated** `redis-server` on a non-default port (none is installed) | Stage 3 | Low — loopback, ephemeral, DB index 15, no `FLUSHDB` |
| **A-4** | *(optional, instead of A-2)* stand up a **second** PostgreSQL cluster so no shared state is touched | Stage 2 without interference | Low, costs time; needs `PG_ALLOW_DSN_DOWNGRADE` discipline so the shared DSN is not repointed at an empty cluster |

**Not requested, and not required:** provisioning (`--apply`), any migration,
`verify_provisioning.py`, `run_all_probes.py`, any commit or push, any CI trigger, any
production access, any infrastructure change.

---

## 9. Decision

```
PostgreSQL lane : READY FOR AUTHORIZATION  (after W1A-01; no provisioning needed)
Redis lane      : BLOCKED                   (no server; installing not authorized)
TENANT-01 evidence via this lane : BLOCKED  (W1A-03 — owner role, RLS not forced)
```

The PG lane is in far better shape than A3 assumed: the target is already provisioned, at a
single unambiguous head, with 56 tracked migrations — so the destructive path is **not on
the critical path at all**. What stands between here and ~632 executed tests is one missing
file (W1A-01) and one coordination decision about a shared cluster (W1A-05).

**Two things must not be claimed afterwards**, whatever the lane returns: this is
PostgreSQL **16.2** while production reports **17.x**, so it is **not** production schema
verification; and tenant-isolation results obtained as the **table owner** with RLS unforced
are **not** evidence for `P11-B09`/TENANT-01.

### Safety confirmation

| Statement | Status |
|---|---|
| Live trading OFF | **VERIFIED** |
| No provisioning, migration, service start or install | **VERIFIED** |
| No source file edited; nothing staged/committed/pushed | **VERIFIED** — `HEAD` unchanged, 0 ahead |
| No production DB / Redis / exchange / payment endpoint | **VERIFIED** — one loopback read-only session |
| Database access read-only | **VERIFIED** — server-enforced; the probe **aborted** when the guarantee could not be proven, then re-ran correctly |
| No credential, DSN or customer identifier printed | **VERIFIED** — host/port/db only; row counts without identifiers |
| Concurrent work preserved | **VERIFIED** — 254 entries, nothing removed |
| Launch-gate statuses unchanged | **VERIFIED** |
| QUOTA-01 | **OPEN** |

### Files written by W1A (all under `staging/`, no source changes)

```
staging/PHASE11B_W1A_VERIFICATION_LANE_PREPARATION.md   (this document)
staging/probe_p11b_w1a_graph_and_inventory.py  + p11b_w1a_graph_inventory.txt
staging/probe_p11b_w1a_target_state.py         + p11b_w1a_target_state.txt
staging/p11b_w1a_pg_verify.txt
```
