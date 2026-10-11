# Paper-Only Launch Readiness

**The question this document answers:** can this product be launched to real paying
customers with **paper trading only**, live trading off?

**The question it does not answer:** whether live customer trading is safe. That is a
separate decision, it was not assessed, and a paper-only verdict must not be read as
progress toward it. See section 7.

**Date** 6 October 2026.
**Environment** PostgreSQL 16.2 and Redis 5.0.14.1, both real, both stood up locally.
No production system was read or written at any point.

---

## 1. Verdict

> ## PAPER-ONLY READINESS: **NO-GO**
>
> ## PRODUCTION DRIFT CLASSIFICATION: **NOT CLASSIFIED — production was never read**
>
> ## LIVE-TRADING READINESS: **NOT CERTIFIED**
> ## LIVE TRADING: **OFF** — verified by execution, 5 of 5 cases refused
> ## LIVE CERTIFICATION: **NOT COMPLETE** — not attempted, not in scope

NO-GO, and the reason is narrow enough to state in one sentence:

**The schema this work verified has never been compared against the schema production
runs.**

### Why the production comparison did not happen, and what is needed

The verification was built, tested and proven safe. It was not run, for one reason:

**There is no production DSN available in this workspace.** `.env` contains placeholders
only — `DATABASE_URL` is a `sqlite` URL, `SUPABASE_KEY` is the literal string
`"dummy_key"`, and `SUPABASE_URL` points at localhost. Checked without printing any
value. Network egress is *not* the obstacle: DNS resolves and an outbound TCP connection
to port 443 succeeds, so a DSN is the only missing input.

Fabricating a result was the alternative and it is not available. A drift classification
of GREEN, AMBER or RED is a statement about a database that was read, and no database was
read. The classification is therefore **NOT CLASSIFIED**, which is a different thing from
AMBER and must not be rounded to it.

**To complete this, one of two things:**

```powershell
# Option A -- supply the DSN and the verification runs unattended.
#   Direct connection on 5432, NOT the pooler on 6543; the tools refuse 6543.
#   Read-only, server-enforced. Reads no row data. Safe to point at production.
python scripts\verify_production_schema.py --dsn "<production DSN>" `
        --out staging\production_schema.json

# Option B -- run it yourself and send back the output. Same command.
```

Then, as a separate and explicit second act, the behavioural half:

```powershell
# Writes synthetic rows to measure cross-tenant isolation, and commits NOTHING:
# one transaction, unconditional ROLLBACK. Refuses to run without the flag.
python scripts\verify_production_security.py --dsn "<production DSN>" `
        --i-understand-this-writes-and-rolls-back `
        --out staging\production_isolation.json
```

Both tools are gated by tests that prove the safety properties rather than asserting
them — 12 tests for the read-only guarantee, 9 for the rollback guarantee. See section 8.

Everything else is in better shape than it was. Three of the four blockers from the
previous verdict are closed by execution. The paper trading path itself is the deployed
configuration and the best-covered part of the system. 123 new tests run against a real
PostgreSQL. Ten probes exit 0. Provisioning is reproducible.

None of that is a statement about production, because none of it has been run there.

### What a GO would require — six items, in order of cost

| # | Item | Cost |
| --- | --- | --- |
| 1 | ~~Wire provisioning and drift checking into CI~~ | **DONE** — `01-pr-check.yml` job `provisioning-and-isolation`. Not yet executed; see 2.2. |
| 2 | ~~Build the production verification, and prove it is safe to run there~~ | **DONE** — two tools, 21 gate tests. Not run: no DSN. |
| 3 | **Supply a production DSN and run the two commands above** | minutes of compute; the DSN is the blocker |
| 4 | ~~Fix BUG-049~~ | **DONE** — 12 columns uuid + FK, 76 regression tests, behaviour proven |
| 5 | Decide whether the deploy workflow should provision the real database | a decision, then a few lines of YAML |
| 6 | Enumerate the `service_role` call sites and assert a tenant filter on each | a day |
| 7 | Run a security sweep against the running application | a day |
| 8 | Correct the "110 exchanges" figure and the ML capability claims | a conversation |
| 9 | Accept in writing that billing is unverifiable pre-production | a signature, not an engineer |

**Item 3 is the verdict.** Everything needed for it exists and is tested; the only
missing input is a credential. Until the verifiers have been pointed at production, every
finding in this body of work is a statement about a database on a laptop — including the
reassuring ones.

**Item 4 is new, and it is a finding against the remediated baseline itself** — which
matters more than it first appears. A drift check compares production to this repository.
If the repository is wrong, "no drift" is reported and both are wrong. That is why
`verify_production_schema.py` runs absolute security checks alongside the differential
one, and why BUG-049 was found by pointing the production tooling at the local database
before trusting it anywhere.

---

## 2. Evidence matrix

Each row says what was measured, by what, and what it proves.

**Three statuses, and they are not interchangeable:**

| | meaning |
| --- | --- |
| **LOCAL VERIFIED** | measured against a PostgreSQL database provisioned from this repository. Real, and not a statement about production. |
| **PRODUCTION VERIFIED** | measured against the live customer database. **Nothing in this document carries this status.** |
| **NOT VERIFIED** | nothing was measured. Never a synonym for "probably fine". |

Every row below that says LOCAL VERIFIED is a true result about a real database with real
roles, real policies and real constraints — which is a large improvement on inspecting
source. It is still not production, and no amount of local green converts into it.

### 2.1 Data isolation

| Claim | Evidence | Result |
| --- | --- | --- |
| `anon` cannot read any customer row | `staging/anon_read_sweep.py anon` — seeds a real row, adopts the role, counts | **LOCAL VERIFIED** 49 tables, 0 leaked, **0 not proven** |
| A logged-in customer cannot read another's rows | `staging/anon_read_sweep.py authenticated` | **LOCAL VERIFIED** 79 tables, 0 leaked, **0 not proven** |
| A logged-in customer cannot modify another's rows | `tests/test_tenant_isolation_rls_postgres.py` — UPDATE / DELETE / forged INSERT per table | **LOCAL VERIFIED** 29 tests |
| Billing tables are isolated | `tests/test_billing_data_isolation_postgres.py` | **LOCAL VERIFIED** 27 tests |
| Full behavioural matrix: A creates, B attempts SELECT/UPDATE/DELETE/forged INSERT, anon attempts all four, A re-reads | `scripts/verify_production_security.py` | **LOCAL VERIFIED** 64 ISOLATED, **0 LEAK**, 5 NO CONTROL, 1 NOT SEEDED |
| No per-customer table has an unconstrained policy | `staging/permissive_policy_probe.py` + a provisioning gate + a test | **LOCAL VERIFIED** exit 0 |
| Overlapping permissive policies are all ownership-scoped | `staging/overlapping_policy_detail.py` — reads every predicate | **LOCAL VERIFIED** 13 pairs, 1 accepted by design with a written reason, 0 unread |
| No browser role holds a privilege RLS cannot filter | `staging/truncate_probe.py` | **LOCAL VERIFIED** exit 0 |
| Isolation holds in **production** | the tooling exists and is gated by 21 tests; **no DSN was available** | **NOT VERIFIED** |
| `service_role` paths apply their own tenant filter | — | **NOT VERIFIED** |
| Column-level exposure within a permitted row | — | **NOT VERIFIED** |

**On the 5 NO CONTROL results.** Not counted as passes. Every attempt was blocked
including the legitimate one, so the probe did not establish that the policy
*discriminates* — and `USING (false)` would produce identical output to perfect
isolation. Four of the five are tables where `authenticated` holds no grant at all, which
is intentional: `payment_methods` is served exclusively over SQLAlchemy and the hardening
revokes both browser roles; `creator_analytics` and `marketplace_analytics` were
exposures and were revoked; `transaction_rollbacks` is fail-closed by design. The fifth is
`referral_profiles`, where no policy matched the seeded row.

That count started at **ten** and reached five by fixing the probe, not by relaxing
anything. The last fix was a single index error — `fks[name]` is `(schema, table, column)`
and the ownership check read the *column* — which made every ownership column with a
foreign key fall through to a generic parent-borrow, so the seeded row ended up owned by a
stranger. Found by printing the statement the seeder actually executed, after three wrong
hypotheses from reading the code. Written up in
`RLS_TENANT_ISOLATION_VERIFICATION.md` section 8.3.

### 2.2 Schema and provisioning

| Claim | Evidence | Result |
| --- | --- | --- |
| An empty database can be provisioned in one command | `scripts/provision_database.py` | **LOCAL VERIFIED** |
| Provisioning twice gives an identical schema | `scripts/verify_provisioning.py`, 16 dimensions | **LOCAL VERIFIED** `RESULT: REPRODUCIBLE` |
| A live schema can be checked against the baseline | `scripts/verify_schema_drift.py` | **LOCAL VERIFIED** — and verified by *catching* a real difference, then reporting `NO DRIFT` on a clean one |
| The ORM matches the provisioned schema | `tests/test_orm_models_match_provisioned_schema.py` | **LOCAL VERIFIED, with one recorded exception** — 4 passed, 1 **strict** xfail. See note below. |
| Structural schema invariants hold | `staging/verify_schema.py` | **LOCAL VERIFIED** 23 checks, 0 failed |
| Migrations are non-destructive | `tests/test_marketplace_paper_schema_contract.py::test_no_destructive_statements` | **LOCAL VERIFIED** — caught a `DROP COLUMN` this pass introduced |
| Provisioning runs in **CI**, from empty, twice, on every PR | `.github/workflows/01-pr-check.yml` job `provisioning-and-isolation` | **WIRED — not yet executed.** See note. |
| Drift checking runs in CI | same job, `verify_schema_drift.py` step | **WIRED — not yet executed** |
| Provisioning runs in **deployment** against the real target | — | **NOT DONE — needs a decision, not code** |
| Production schema matches this repository | — | **NOT VERIFIED** |
| Rollback has been rehearsed | — | **NOT DONE** |

**On "wired, not yet executed".** A new job, `provisioning-and-isolation`, provisions an
empty `postgres:17-alpine` from repository source, provisions it **again** to catch the
privilege-re-grant class of defect, fails on drift from the committed baseline, runs all
ten probes, and runs the nine PostgreSQL test files. The YAML parses and the job is
registered.

What is verified: the entire sequence against a **vanilla PostgreSQL with no Supabase
substrate**, repeatedly — that is the same starting condition as the container, because
`--create-substrate` is the only path `staging/reprovision.py` uses.

What is not: the PostgreSQL **major version**. Local runs are 16.2 from the `pgserver`
wheel; the container is 17, to match production's reported `server_version 17.6`. The
first pull request closes that gap. It is committed in this state deliberately — a
provisioner verified by hand that nothing ever runs is documentation, not a control — and
the uncertainty is written into the job's own comment rather than left for someone to
discover.

**On the deployment half.** Wiring `provision_database.py` into `03-deploy.yml` means
running DDL against the production Supabase database from CI. That is hard to reverse and
affects live customer data, so it is **not** done here and should be an explicit decision
with a backup taken first. The pieces are ready: the provisioner refuses non-localhost
DSNs unless `--i-understand-this-is-remote` is passed, records a checksum per file, and
refuses outright if a file changed since it was applied.

**The strict xfail, stated rather than buried.**
`test_only_one_model_claims_the_positions_table` is marked `xfail(strict=True)`. The
*schema* half of the conflict is fixed: alembic `b2c3d4e5f6a7` reshapes `positions` onto
`core/position_model.PositionModel`, the declaration that `position_engine`, `pnl_engine`,
`fee_engine`, `metrics_system`, `event_router` and `consistency_checker` are all built on.

What remains is a source-level duplication: `backend/state_service.py` still declares a
**second** class with `__tablename__ = 'positions'` and a third column set again. It is
unreachable from the provisioned schema — it sits inside a conditional block in a module
with its own async session factory — so it cannot corrupt the table. But two classes
claiming one table is how this defect started.

`strict=True` matters: if someone removes the duplicate declaration, this test turns into
a **failure** and prompts removal of the marker. It is marked rather than deleted because
deleting it loses the finding, and marked rather than left red because a permanently red
suite stops being read. This is a recorded known defect, not a pass.

### 2.3 The paper trading path

| Claim | Evidence | Result |
| --- | --- | --- |
| Live trading is refused on every path | `staging/verify_live_trading_off.py` | **LOCAL VERIFIED** 5 of 5 BLOCKED, including both env switches set |
| Paper and live are separated | `pytest -k "paper_simulator or paper_live_separation or execution_mode or paper_mode"` | **LOCAL VERIFIED** 101 tests |
| Equity and cash have one source of truth (BUG-016) | `test_execution_engine_accounting_identity.py`, `test_paper_accounting_cost_basis.py`, `test_live_orders_are_bounded_by_account_equity.py`, `test_paper_live_separation.py` | **LOCAL VERIFIED** 72 tests |
| Position lifecycle persists correctly | `tests/test_position_lifecycle_postgres.py` | **LOCAL VERIFIED** 6 tests |
| Order cancellation is atomic | `tests/test_atomic_order_cancellation_fix.py` (real PG) | **LOCAL VERIFIED** |
| Position deltas are race-free | `tests/test_position_delta_race_condition_fix.py` (real PG) | **LOCAL VERIFIED** |
| BotRunner paper fills appear in the paper UI | — | **FAIL — known, reported, not fixed.** See 4.1 |
| Paper results have been validated against a reference implementation | — | **NOT VERIFIED** |

### 2.4 Signup and identity

| Claim | Evidence | Result |
| --- | --- | --- |
| Signup works on a database built from this repository | `tests/test_signup_integration_postgres.py` | **LOCAL VERIFIED** 12 tests |
| pgcrypto is present, and its absence fails usefully | `tests/test_pgcrypto_preflight_postgres.py` — two-state test, State A installed → PASS, State B absent → FAIL with a diagnostic | **LOCAL VERIFIED** 5 tests |
| JWT secret strength is enforced, not warned (BUG-012) | regression suite | **LOCAL VERIFIED** |
| `tenant_id` is one type across the financial tables | alembic `a1b2c3d4e5f6`; `tests/test_tenant_referential_integrity_postgres.py` | **LOCAL VERIFIED** 30 tests, all 7 FKs created |
| `user_id` is one type across the schema | `staging/identity_type_matrix.py` | **LOCAL VERIFIED — BUG-049 FIXED.** 81 identity columns: 55 correct, **0 of the wrong type**, 26 uuid without an FK (informational) |
| Customer-identity foreign keys exist, with a deliberate ON DELETE | `tests/test_bug049_identity_types_postgres.py` | **LOCAL VERIFIED** 76 tests — credentials CASCADE, financial records RESTRICT, both proven by executing the deletion |

**BUG-049 — FIXED.** `user_id` was `uuid` on 43 columns, `character varying(36)` on the
three billing tables, and `text` on nine others including `exchange_keys`. A foreign key
cannot span two types, so 12 columns declared no reference to the customer they belonged
to. All 12 are now `uuid` with a foreign key to `profiles(id)`.

The impact was **integrity and retention, not confidentiality**: a row could name a
customer who did not exist, and deleting a customer orphaned the rows rather than
cascading — which for `exchange_keys` meant encrypted venue credentials outliving the
account. RLS always isolated these tables, because their policies cast both sides, and
that was confirmed behaviourally throughout: `invoices` and `subscriptions` come back
`ISOLATED`.

`ON DELETE` was chosen per table rather than defaulted. `CASCADE` for
`exchange_keys`/`exchange_connections`, because an orphaned row is an encrypted API key
nobody can audit, rotate or revoke. `RESTRICT` for the six financial and audit tables,
because `CASCADE` would destroy them and all six columns are NOT NULL so `SET NULL` is
impossible — and because there is **no account-deletion path in `backend_app/`** today, so
`RESTRICT` constrains a flow that does not exist yet rather than breaking one that does.

Proven by executing it, not by reading DDL: an exchange credential for a nonexistent
customer is rejected; deleting a customer removes their credentials; deleting a customer
who has an invoice is refused and the invoice survives. 76 tests in
`tests/test_bug049_identity_types_postgres.py`.

Three further defects surfaced while closing it — a migration that could not restore its
own policies, a provisioning-order problem that left 9 of the 12 columns unconverted on a
fresh database, and a UTF-8 BOM that had silently disabled the AST-based safety checks on
both production verifiers. All three are written up as BUG-052, BUG-053 and BUG-054.

### 2.5 Money paths other than trading

| Claim | Evidence | Result |
| --- | --- | --- |
| A referral commission can be credited | `tests/test_referral_money_lifecycle_postgres.py` | **LOCAL VERIFIED** 12 tests |
| A referral commission can be reversed | same | **LOCAL VERIFIED** |
| A referral payout request can succeed | same | **LOCAL VERIFIED** |
| Duplicate webhook delivery is ignored | `tests/test_webhook_duplicate_delivery_real_redis.py`, real Redis, real handler | **LOCAL VERIFIED** 6 tests |
| Billing end-to-end with sandbox credentials | — | **NOT VERIFIABLE BY DESIGN** |
| Settlement, provider interaction, entitlement grant | — | **NOT VERIFIED** |

### 2.6 Code quality gates

| Claim | Evidence | Result |
| --- | --- | --- |
| No unconstrained interpolation into SQL | `tests/test_bandit_sast_gate_is_green.py` | **LOCAL VERIFIED** 3 tests — caught 24 sites this pass introduced |
| The validators that gate rests on raise rather than sanitise | same file | **LOCAL VERIFIED** |
| PostgreSQL test lane | `staging/run_pg_suite.py` | **LOCAL VERIFIED** 19/19 files, 285 passed, 1 xfailed, 7 skipped |
| Database probe set | `staging/run_all_probes.py` | **LOCAL VERIFIED** all 12 probes exit 0 |
| Full suite | 12,659 passed / 18 failed / 193 skipped | see section 3 |

---

## 3. The 18 full-suite failures, and why none of them blocks

Every one was re-run individually. Full detail in `FINAL_LAUNCH_READINESS_REPORT.md`
section 5 of the addendum.

**Final run: 16 failed, 12,661 passed, 203 skipped, 6 xfailed, 3 xpassed, 36m18s.**

| Group | Count | Blocks a paper-only launch? |
| --- | --- | --- |
| Introduced by this pass | 2 → **0** | No — both fixed, and the suite was re-run to confirm |
| Order-dependent (`test_task_6_1_backtest_execute.py`); pass in isolation | 8 | No — but they make every future suite result need a human to interpret, which is its own cost |
| Pre-existing, unrelated to this pass | 8 | No |

Of the eight pre-existing ones, one deserves naming because it is not a code defect at
all: `test_exchange_safety_fix::test_connection_pooling_safety` calls ccxt's
`load_markets()`, a real network request to Binance, and there is no egress in this
environment. It is also what made one batch run appear to hang.

Baseline was 17. This pass introduced 2, closed both, and ended at 16 — **one below
baseline**. Both of the two were caught by this repository's own gates (the SAST gate and
the no-destructive-migrations contract) rather than by review, which is the clearest
evidence on this page that those gates work.

---

## 4. Known-broken things a paper-only customer could encounter

Listed because a launch decision needs to know what a customer will actually hit, not
only what the tests cover.

### 4.1 BotRunner paper fills do not reach the paper-trading UI

A strategy deployed through `StrategyDetail.jsx` — which hardcodes
`environment: "paper"` on both deploy actions — runs under `BotRunner`, whose fills are
accounted for separately from the Paper Simulator that the paper UI reads. So a customer
can deploy a strategy, watch it run, and see no trades in their paper account.

**Not fixed, and the reason is written down rather than glossed.** The two available
options were:

- route BotRunner through the Paper Simulator — needs a `session_id` BotRunner does not
  have, which is a re-architecture, not a fix;
- remove the feature from the UI — `StrategyDetail.jsx` hardcodes paper on both deploy
  paths, so removal takes the whole deploy surface with it.

The earlier report claimed BotRunner *cannot execute* in paper mode. That was obsolete —
it was fixed previously, and the 101 paper-mode tests in section 2.3 confirm execution
happens. The real defect is narrower: an accounting divergence. A misleading comment in
`backend/master_executor.py` that asserted the obsolete claim has been corrected.

This is the one item on this page that violates the principle "do not leave a visible
feature that appears to work but silently produces nothing", and it is left open
knowingly.

### 4.2 The library favourites endpoints

`POST`/`DELETE /api/library/{id}/favorite` and `GET /api/library/favorites` returned 500
for every caller until this pass (BUG-047). They now work. No shipped UI calls them —
`grep` for `favorite` across every `.js/.jsx/.ts/.tsx` outside `node_modules` returns
nothing — so no customer was affected, and none will exercise them now either.

### 4.3 Referral codes are shorter than intended

The generated suffix is 4–6 characters rather than a fixed 6, because `regexp_replace`
strips the hyphen before the slice. Cosmetic: the keyspace is smaller than designed, and
codes are still unique. Not fixed.

---

## 5. What is deliberately not claimed

The instruction for this work was not to make the report green, and this is the section
where that matters most.

| | Status | Why it is unverified |
| --- | --- | --- |
| Production schema, RLS, privileges | **NOT VERIFIED** | Never contacted. Supabase allows dashboard edits that leave no migration in this tree, so this repository and production can disagree silently. |
| Billing end-to-end | **NOT VERIFIABLE** | `_validate_keys` accepts live prefixes only, in every environment. Defensible for entitlement-granting paths — a test key collects no money, so honouring one would hand out paid plans free — but the consequence is that the revenue path first executes against real customer money. |
| `service_role` code paths | **NOT VERIFIED** | `BYPASSRLS` means the application's own filter is the only control. More than a dozen call sites in `routers/library.py` alone. |
| Application-layer security | **NOT VERIFIED** | No sweep was run against the running service. Everything here is at the database layer. |
| `auth` schema policies | **NOT VERIFIED** | `auth.users` was seeded and read as a fixture; GoTrue's own policies were not audited. |
| Exchange count ("110 exchanges") | **NOT CORRECTED** | Not reconciled against what the code supports. |
| ML capability claims | **NOT ADDRESSED** | Not reconciled against what ships. |
| Load, soak, failover | **NOT TESTED** | Out of scope for this pass. |
| Rollback and restore | **NOT REHEARSED** | `downgrade()` exists on the new revisions and two of them deliberately *refuse* rather than destroy data, but no drill was performed. |

---

## 5a. The production verification: built, proven safe, not run

Two tools. Both were pointed at the local remediated database first, repeatedly, and both
had their first answers rejected — see the calibration notes at the end of
`FINAL_PRE_LAUNCH_BUGS.md`. Shipping a verifier whose output nobody had challenged would
have been the same mistake as shipping a provisioner nobody had run.

### `scripts/verify_production_schema.py` — strictly read-only

Covers the full comparison surface the gate asks for: tables **and their owner**, columns
**and their identity/generated status**, primary keys, foreign keys **with ON DELETE and
ON UPDATE** (via `pg_get_constraintdef`), unique and check constraints, indexes, triggers,
functions, RLS flags **including `relforcerowsecurity`**, policies **with roles, command,
USING and WITH CHECK**, grants for `anon`/`authenticated`/`service_role`, extensions,
sequences, enum types, and migration state. Plus absolute checks that do not depend on the
baseline: billing exposure, constant-TRUE policies, overlapping policies, TRUNCATE-class
grants, `execution_records` primary key, `idempotency_keys` uniqueness, identity types,
and a per-table inventory of every table carrying an ownership column.

Two gaps in the comparison surface were found and closed while building it: columns were
not compared on identity/generated status, and tables were not compared on **owner** — and
ownership is a security property here, because an owner bypasses its own policies unless
`FORCE` is set, which this schema deliberately does not set.

**Read-only is server-enforced, not conventional.** `default_transaction_read_only` is set
*and read back*; the script aborts if the server does not confirm it, so a write would be
rejected with SQLSTATE 25006. Port 6543 is refused because session settings do not survive
the transaction pooler and the guarantee would become unverifiable. No row data is read —
only catalogues, plus `COUNT(*)` so that an empty table is not mistaken for a secure one.

Proven by `tests/test_schema_drift_checker_is_read_only_postgres.py`, **12 tests**: a
write through the same session is attempted and asserted to fail with 25006, DDL likewise,
a full run is asserted to leave every row count and catalogue count unchanged, credential
redaction is tested against a password containing `@`, and the pooler refusal is asserted
not to echo the credential.

### `scripts/verify_production_security.py` — writes, commits nothing

The behavioural matrix: A creates a row, B attempts SELECT/UPDATE/DELETE and a forged
INSERT claiming A's identity, `anon` attempts all four, and A re-reads its own row.

**The instruction tension, resolved explicitly.** "DO NOT modify production data" and
"create only synthetic test records" cannot both be taken literally — a synthetic row is a
modification. So nothing is committed: one transaction, `ROLLBACK` in a `finally`,
unconditionally. It refuses without `--i-understand-this-writes-and-rolls-back`, refuses
if either live-trading switch is set, refuses `VYOMQUANT_MODE=live`, refuses the pooler,
and reports its own rollback plus a count of leftover probe identities (0).

Proven by `tests/test_production_security_probe_rolls_back_postgres.py`, **9 tests**,
including one that fingerprints row counts across every table in `public` plus
`auth.users` before and after a complete run and asserts nothing moved — and a companion
that asserts the run actually measured something, because a script that connected and did
nothing would also leave every count unchanged.

### Local result, for calibration

```
64 ISOLATED    owner reads its own row, nobody else can
 0 LEAK        no cross-tenant or anon attempt succeeded anywhere
 5 NO CONTROL  every attempt blocked INCLUDING the owner's -- not a pass
 1 NOT SEEDED  no row could be created -- nothing measured
ROLLBACK issued. synthetic identities still present after rollback: 0
```

### How to classify the production result when it arrives

| Observation | Classification |
| --- | --- |
| drift in tables / columns / PKs / FKs / RLS flags / policies / privileges / constraints / functions / triggers | **RED** |
| any billing table with RLS off, or a constant-TRUE policy, or a browser-role grant RLS must catch | **RED** |
| any `LEAK` verdict from the behavioural probe | **RED** |
| `execution_records` missing its primary key on `execution_id`, or `idempotency_keys` without uniqueness | **RED** |
| identity columns of mixed type | **RED** by the gate's criteria — note BUG-049 means the baseline has this too, so expect it on BOTH sides |
| drift in indexes / sequences / extensions / enum types | **AMBER** |
| drift in `alembic_version` / `schema_migrations` only | **AMBER** — deployment state, not schema shape |
| no drift and no absolute finding | **GREEN** |

`verify_production_schema.py` applies exactly this table and exits 0 / 1 / 2 for
GREEN / AMBER / RED. **If it returns RED, stop** — the required remediation package
(impact, SQL, rollback, backup, locking, verification query, post-migration behavioural
test) is section 11 of the gate, and no DDL should be applied without approval.

---

## 6. Reproducing this verdict

```powershell
# infrastructure
$env:PG_DATA_DIR = "pgdata_z1"
.venv\Scripts\python.exe staging\bootstrap_pg.py start
.\staging\redis\redis-server.exe --port 6379 --appendonly no

# provisioning: reproducible, and matching the baseline
.venv\Scripts\python.exe scripts\verify_provisioning.py      # RESULT: REPRODUCIBLE
.venv\Scripts\python.exe staging\reprovision.py
.venv\Scripts\python.exe scripts\verify_schema_drift.py      # RESULT: NO DRIFT

# all ten probes
.venv\Scripts\python.exe staging\run_all_probes.py           # all 10 exited 0

# the PostgreSQL test lane
.venv\Scripts\python.exe staging\run_pg_suite.py             # 19/19 files green, 285 passed

# the production tooling, validated against the local database first
.venv\Scripts\python.exe scripts\verify_production_schema.py `
        --dsn (Get-Content staging\pg_dsn.txt -Raw).Trim()
.venv\Scripts\python.exe scripts\verify_production_security.py `
        --dsn (Get-Content staging\pg_dsn.txt -Raw).Trim() `
        --i-understand-this-writes-and-rolls-back

# the full suite -- NO --timeout, see POST_LAUNCH_IMPROVEMENTS item 20
$env:AERORA_TEST_DATABASE_URL=""; $env:AERORA_TEST_REDIS_URL=""
.venv\Scripts\python.exe -m pytest tests/ -q
```

## 6a. Verification status of the verifiers themselves

| Tool | Safety property | Proven by |
| --- | --- | --- |
| `verify_production_schema.py` | read-only, server-enforced; no credential in output | `tests/test_schema_drift_checker_is_read_only_postgres.py` — 12 tests |
| `verify_production_security.py` | commits nothing; refuses without acknowledgement; refuses live-trading env | `tests/test_production_security_probe_rolls_back_postgres.py` — 9 tests |
| `verify_provisioning.py` | provisions twice, diffs 16 dimensions | exit 0, `RESULT: REPRODUCIBLE` |
| `verify_schema_drift.py` | detects real difference, and reports none when there is none | verified in both directions |

The point of this table: the production run will be the first time any of this touches
customer data, so the tools had to be gated before the database was. 21 of those tests
exist for no other reason.

To confirm the regression tests are load-bearing rather than merely green:

```powershell
.venv\Scripts\python.exe staging\regress_old_ratings_policy.py
.venv\Scripts\python.exe -m pytest tests\test_tenant_isolation_rls_postgres.py -q
#   -> 2 failed: the favourites leak and the unconstrained-policy sweep
.venv\Scripts\python.exe staging\apply_hardening.py
#   -> 29 passed
```

---

## 6b. The nine GO conditions, scored

| # | Condition | Status |
| --- | --- | --- |
| 1 | production schema matches the verified remediation schema | **NOT VERIFIED** — production never read |
| 2 | billing records are inaccessible cross-tenant | **LOCAL VERIFIED** (27 tests + behavioural `ISOLATED`); **NOT VERIFIED** in production |
| 3 | `referral_codes` are isolated | **LOCAL VERIFIED** — BUG-045 fixed, enumeration closed, behavioural `ISOLATED` |
| 4 | `library_ratings` isolated without breaking legitimate writes | **LOCAL VERIFIED** — BUG-046 and BUG-047 fixed; all three documented write paths accepted; reviews stay public, favourites owner-only |
| 5 | tenant isolation behavioural probes pass | **LOCAL VERIFIED** — 64 ISOLATED, 0 LEAK; 5 NO CONTROL reported as gaps, not passes |
| 6 | execution database invariants exist | **LOCAL VERIFIED** — `execution_records_pkey PRIMARY KEY (execution_id)`, `idempotency_keys_pkey (idempotency_key, tenant_id)` |
| 7 | no launch-critical production drift remains | **NOT CLASSIFIED** — production never read. BUG-049, which was a launch-critical finding against the baseline itself, is now fixed |
| 8 | all existing paper-only gates pass | **LOCAL VERIFIED** — 19/19 PostgreSQL files, 285 passed; all 12 probes exit 0 |
| 9 | live trading remains OFF | **LOCAL VERIFIED** — 5 of 5 refused, including both env switches set |

Conditions 3, 4, 5, 6 and 9 are met as far as a non-production environment can meet them.
**Conditions 1 and 7 cannot be assessed at all**, and condition 7 additionally fails on
BUG-049. Two unmet and one failed, out of nine.

**PAPER-ONLY: NO-GO.**

---

## 7. Live trading — read this before quoting the verdict above

**A PAPER-ONLY GO, if one is ever issued, certifies nothing about live trading.** This
section exists so that cannot be misread.

**Live trading is OFF.** Verified by invoking the guard in a subprocess under five
environments, not by reading configuration:

| Environment | Verdict |
| --- | --- |
| Production as deployed: `ENV=production`, `VYOMQUANT_MODE=paper`, enable flag unset | BLOCKED |
| `VYOMQUANT_ENABLE_LIVE_TRADING=true`, mode still `paper` | BLOCKED |
| `VYOMQUANT_MODE=live`, enable flag unset | BLOCKED |
| Deprecated alias `AERORA_ENABLE_LIVE_TRADING=true`, mode `paper` | BLOCKED |
| **Both switches set: `VYOMQUANT_MODE=live` AND `VYOMQUANT_ENABLE_LIVE_TRADING=true`** | **BLOCKED** |

The last row is the one worth knowing. With both environment switches set to the values
that are supposed to enable live trading, execution is **still** refused — by
`ExecutionFlags.LIVE_TRADING_ENABLED`, a hardcoded `bool = False` class attribute at
`backend_app/core/safety_config.py:114` that is read from no environment variable at all.
Enabling live customer trading therefore requires a **code change**, not a configuration
change. There is a third control and it is in source.

Nothing in this pass set either flag. No real-money order was placed. No production
credential was read. No production database was contacted. The provisioner refuses port
6543 and any non-localhost host.

### What live-trading certification would require, none of which was done

- Exchange credential handling, rotation and per-tenant key isolation under real load
- Order lifecycle against a real venue, including partial fills, rejections and
  cancellations racing with fills
- Reconciliation against real venue state — note BUG-042: `PositionRepository.create()`
  silently dropped `exchange_id`, and `consistency_checker` filters on it, so every
  reconciliation run compared the venue against an empty local set. Fixed, but never
  exercised against a venue.
- Risk limits, kill switches and circuit breakers under adverse conditions
- Slippage, fee and funding accounting against real statements
- Regulatory and custody questions, which are not engineering questions at all

**LIVE TRADING: NOT CERTIFIED. Do not enable it on the strength of this document.**




---

## 8. Addendum — the production drift gate, and BUG-049 closure

**Date** 6 October 2026, fourth pass.

### 8.1 Production remains NOT CLASSIFIED

Full record: `staging/FINAL_PRODUCTION_VERIFICATION.md`.

`.env` holds placeholders only — a `sqlite` `DATABASE_URL`, a localhost `SUPABASE_URL`,
and `SUPABASE_KEY="dummy_key"`. Network egress was tested and works, so a credential is
the only missing input. No production database was read, so there is no GREEN, AMBER or
RED to report. **`NOT CLASSIFIED` has not been rounded to AMBER**, and no local pass has
been promoted into a production one.

### 8.2 BUG-049 is closed, and that was a precondition

The gate required the baseline to be free of a known customer-identity type defect
*before* production classification, and the reason is worth restating: a drift check
answers *are they the same*, not *are they correct*. BUG-049 was in this repository, so a
production comparison would have reported no drift while both sides were wrong.

That is not hypothetical reasoning — it is what happened at a smaller scale inside this
pass. BUG-053 was found by the **differential** check (`9 missing, 9 unexpected`) and
would have been missed by the absolute one, while BUG-049 itself was found by the
**absolute** check and would have been missed by the differential one. Each catches what
the other cannot, which is why `verify_production_schema.py` runs both.

### 8.3 Rollback proof: two properties, not one

The claim "byte-for-byte unchanged" has been withdrawn. What is asserted and what is
merely reported are now separate:

| | status |
| --- | --- |
| **DATABASE ROWS UNCHANGED** | **asserted** — row counts across all 85 `public` tables plus `auth.users`; the exact primary keys of `profiles` and `auth.users` compared as sets rather than counts, so a delete-and-reinsert cannot hide; every `text`/`varchar` column swept for the probe's synthetic markers; the 10 audit and trigger-backed tables checked by name |
| **DATABASE STATE FULLY RESTORED** | **reported, and currently NO** — `public.reconciliation_mismatches_id_seq` advanced `0 → 1` and did not roll back |

`nextval()` is non-transactional in PostgreSQL by design, so a sequence consumed inside a
rolled-back transaction stays consumed. No customer data is affected; only the next
surrogate key. The point is that the stronger property is **measured and named**, not
assumed, and the documents are only entitled to the weaker claim.

The rollback is additionally enforced structurally: the probe's source is parsed and the
tests fail on any reachable `commit()`, any `autocommit = True`, any `isolation_level`
argument, a `conn.rollback()` not inside a `finally`, a connection opened before the
acknowledgement flag is checked, or a missing `assert not conn.autocommit`. 20 tests.

### 8.4 Final local gate position

| | |
| --- | --- |
| PostgreSQL lane | **19/19 files green**, 285 passed, 1 xfailed, 7 skipped |
| Database probes | **all 12 exit 0** — `identity_type_matrix.py` was the one failing before BUG-049 |
| Provisioning | `RESULT: REPRODUCIBLE` — 121 foreign keys, 50 recorded migrations |
| Drift vs baseline | `RESULT: NO DRIFT` |
| Behavioural isolation | **64 ISOLATED, 0 LEAK**, 5 NO CONTROL, 1 NOT SEEDED |
| Live trading | **OFF** — 5 of 5 refused |

### 8.5 The verdict is unchanged

**PAPER-ONLY NO-GO.** The local evidence is now stronger than it has been at any point in
this engagement, and it is still local. One GO condition — "production schema matches the
verified remediation schema" — cannot be assessed at all, and five others inherit the same
gap. A credential is the only thing standing between this and a classification.

**LIVE TRADING: OFF. NOT CERTIFIED.**
