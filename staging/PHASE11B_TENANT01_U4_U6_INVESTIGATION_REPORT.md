# Phase 11B — TENANT-01 U4–U6 read-only investigation

**READ-ONLY. No source, migration, test, role, grant, configuration or register edited. No
database or Redis contacted. Nothing staged, committed or pushed. One new artifact: this report.**

**Status preserved: TENANT-01 OPEN · QUOTA-01 OPEN · PostgreSQL enforcement UNEXECUTED · live
trading OFF / NOT CERTIFIED.**

Predecessor (not reproduced here): `staging/PHASE11B_TENANT01_FAST_COORDINATION_REPORT.md`.

**All three uncertainties are resolved, and two produce findings that are worse than recorded:**

* **U5** — the two `SET LOCAL app.current_tenant_id` sites that prior reports treated as partial
  tenant-context coverage are **both non-functional**: one is dead code, the other is **mis-ordered**
  so it would not work even if wired. There is **no** partial coverage anywhere.
* **U6** — a **new, distinct partial-completion defect (proposed CD-8)**: a process death between
  the settlement ledger insert and the entitlement grant is unrecoverable, because the resume state
  is in-process only. The provider's redelivery is then swallowed as `DUPLICATE_IGNORED`. Money
  recorded, owner earning recorded, buyer never granted access, webhook reports success.

---

## 1. Baseline and preservation

| property | prior | measured now | verdict |
|---|---|---|---|
| HEAD | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | same | match |
| branch / upstream | `main` → `origin/main` | same, `0 / 0` | match |
| **`git write-tree`** | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` | **`6f40856cee2bc7ea4d741f69f4b1f68b463d70a4`** | **match — authoritative** |
| staged digest (normalized SHA-256) | `65dfc45367ced6f67573f3ae485a1c92d63c803b4d881b6a186afd3d54a5bd0c` | **identical** | **verified, not assumed** |
| staged / unstaged tracked paths | 22 / 100 | 22 / 100 | match |
| untracked paths | 3383 | 3385 | drift, untracked only |

Digest method unchanged from the predecessor, so the comparison is method-consistent:
`sha256( git diff --cached (raw bytes).replace(b'\r\n', b'\n') )`. The raw `.git/index` hash is not
used. Untracked growth is other sessions writing `staging/` artifacts and is **not** evidence of
tracked-state change — HEAD, index tree, digest and both tracked counts all held.

Output path `staging/PHASE11B_TENANT01_U4_U6_INVESTIGATION_REPORT.md` verified **absent** before
writing. No existing report was overwritten; no session tag was needed.

---

## 2. U4 — `Base.metadata.create_all` dependency inventory

Swept every `*.py` in the repository excluding `.venv/`, `.codex_verify_venv/`, `node_modules/`,
`__pycache__/`. Third-party hits under `python_portable/Lib/site-packages/` (SQLAlchemy, Alembic,
pandas — ~45 matches) are vendor code and excluded.

### 2.1 Direct call sites

| # | file:line | caller & condition | can run in | creates | required, or inherited? | evidence |
|---|---|---|---|---|---|---|
| **A1** | **`backend_app/main.py:795`** | **module level, at import** — `try: Base.metadata.create_all(bind=engine) / except Exception: logger.warning("Database not available at startup")` | **production, tests, dev — every import** | every table in the app `Base` metadata registered by the imports at `:81-95` | **inherited by nearly all callers; required by some tests (§2.3)** | read directly; `engine` resolves to `get_db_pool().engine` via `core/database.py:213-216` |
| A2 | `backend_app/backend/state_persistence.py:524` | `Base.metadata.create_all(self.engine)` inside its own initialiser, against its **own separate `Base`** (`:477`) and a dataclass-default DSN `postgresql://user:pass@localhost/trading` | only if a caller supplies a real DSN | its own metadata, not the app's | **unresolved — no caller found supplying a DSN** | read directly |
| A3 | `scripts/attack_database_integrity_destruction.py:99` | adversarial harness | manual only | its own engine | required by that script | read directly |
| A4 | `tests/test_sqlalchemy_pool_events_fix.py:207, 323` | builds its own engine inside the test | tests only | its own metadata | **required** by those tests | read directly |

### 2.2 Sibling trees — present, and their deployment status is unresolved

Three parallel copies of the application exist with the same pattern. **None is `backend_app`, and
whether any is deployed could not be established from the repository:**

* `aerora_quant_platform/backend_api/main.py:423` + `state_persistence.py:523`
* `connection_layer/main.py:423` + `state_persistence.py:523` +
  `live_trading_certification.py:488, 596, 866, 926` + `tests/test_group{7,10,14}_*.py` ×3

**Marked unresolved.** If any is deployed, A1's remediation must be applied there too. This is a
question for O3/the deployment owner, not answerable statically.

### 2.3 Indirect dependants — the number that matters for D4

**93 test files import `backend_app.main`**, and each import executes A1. Refining by whether the
file touches an ORM/session surface (`.query(`, `SessionLocal`, `get_db_context`,
`db_pool.get_session`, `…Model`):

| group | count | reading |
|---|---|---|
| import `backend_app.main` | **93** | all trigger A1 at import |
| …of which touch an ORM/session surface | **25** | **candidate genuine dependants** |
| …remaining | 68 | import `main` for `TestClient`, route enumeration or AST assertions — **most likely inherit, not require** |

Representative members of the 25: `test_get_db_dependency.py`, `test_paper_session_api.py`,
`test_marketplace_pipeline.py`, `test_library_detail_projection_regression.py`,
`test_my_strategies_ownership_and_actions.py`, `test_registry_endpoints.py`,
`test_model_versioning.py`, `test_referral_program_decision.py`, `test_node_preview_endpoint.py`,
`test_dashboard_absent_figures.py`.

**Per the brief, the 25 is a candidate set, not a dependency proof.** Touching a session surface is
necessary but not sufficient: a file may stub the session, or query a table some other fixture
creates. The 68/25 split is an upper and lower bound, not a verdict.

**What A1 creates in the test lane:** `tests/conftest.py:37-41` blanks `DATABASE_URL` unless
`AERORA_TEST_DATABASE_URL` is set, so `database_pool.py:140-143` falls through to
`sqlite:///./algo22.db` — **a persisted file**. `staging/fix_sqlite_scratch_schema.py:6` exists
specifically because of this, describing `./algo22.db` as *"a PERSISTED file"* whose schema comes
from `Base.metadata.create_all()`. So in the default lane A1 is the thing that makes ORM tables
exist at all.

### 2.4 Tests that mention `create_all` but do NOT depend on it

Recorded explicitly, because a text search would misclassify them:
`tests/test_orm_models_match_provisioned_schema.py:30`,
`tests/test_tenant02_fee_records_rls_postgres.py:18`,
`tests/test_fee_third_asset_pct_is_nullable_postgres.py:14`, and several `staging/*.py` documents.
All are **prose references** in docstrings/comments. None calls it.

### 2.5 Minimal safe verification plan for D4

Two steps, in order. **Step (i) requires D4** because it edits `main.py` (+289/−36, O3).

1. **Gate and measure.** Put A1 behind an explicit opt-in defaulting **off** in deployed
   environments and **on** for the local/test lane, then run: first the 25 candidates, then the 93
   superset, then the full default lane. Whatever fails with the gate off is the true dependant set.
   Expected cost: one full-lane run.
2. **Stop swallowing.** A1's `except Exception → logger.warning` currently hides a *partial* create.
   Even with the gate off by default, the on-path should surface the failure. This is a separate,
   smaller change and can follow (1).

**No read-only substitute is conclusive** — whether a test needs a table can only be settled by
withholding it. Stated rather than guessed.

---

## 3. U5 — production reachability of the two `SET LOCAL` sites

### 3.1 Reachability matrix

| module | construction / call sites | classification | tenant GUC works? |
|---|---|---|---|
| **`backend/tenant_rls_validator.py`** | class `TenantRlsValidator` (`:59`), factory `get_tenant_rls_validator` (`:485-487`). **Zero importers** anywhere in `backend_app/`, `tests/` or `scripts/`. The only external mention is a docstring reference at `core/tenant_db_context.py:7`. | **apparently unreachable — dead code** | **never executes** |
| **`backend/transactional_execution_manager.py`** | **exactly one** construction site: `backend/exchange_websocket_listener.py:479`, `TransactionalExecutionManager(session, tenant_id=uuid.UUID(order_event.tenant_id))`. Factory `get_transactional_execution_manager` (`:1345-1347`) has **no production caller** (only docstring mentions). `order_watchdog.py:102,118` imports two *helpers* (`json_text`, `_session_dialect`), **not** the manager. | **apparently unreachable in production** — nothing starts the listener | **no — and mis-ordered (§3.2)** |

Corroborated independently by an existing test's own words,
`tests/test_json_extract_dialect_portability.py:39-45`:

> *"This path cannot currently execute at all. `TransactionalExecutionManager` has exactly one
> construction site — `exchange_websocket_listener.py:343` — and nothing starts that listener (no
> entry in `main.py`, `worker.py`, `workers/`, `startup.sh`, `docker-compose.yml` or a `Procfile`).
> So this is a landmine rather than a live outage: it arms the moment the fill listener is wired
> up."*

(The line number has drifted to `:479`; the "exactly one construction site" claim holds.)

### 3.2 For the one nominally-reachable path, the six required details

`exchange_websocket_listener.py:476-488` → `TransactionalExecutionManager.begin()`
(`transactional_execution_manager.py:221-243`):

1. **Tenant identity source:** `uuid.UUID(order_event.tenant_id)` — from a **websocket order
   event**, *not* from a verified JWT. No authentication step on that field is visible at the
   construction site.
2. **Is context applied?** A statement is issued:
   `self.db.execute(text("SET LOCAL app.current_tenant_id = :tenant_id"), {...})` at `:222-225`.
3. **Same session/connection as protected queries?** Yes — same `Session` from
   `db_pool.get_session()` at `:477`.
4. **Transaction-local?** **This is the defect.** `SET LOCAL` is issued at `:222`, but
   `self.db.begin()` is only called at `:233` — **eleven lines later**. Under
   `pool_mode=transaction` a statement issued outside an explicit transaction gets its own implicit
   one, so the setting is **discarded before the transaction the queries run in**. This is exactly
   the failure mode `core/tenant_db_context.py:38-42` documents and refuses outright with
   `TenantContextNotTransactional`.
5. **Exceptions / cleanup:** the `SET LOCAL` is wrapped in
   `except Exception: logger.debug("[Transaction] SET LOCAL not supported in current DB dialect")`
   — which silences a genuine PostgreSQL failure exactly as readily as SQLite's lack of support.
   Again the precise defect called out at `tenant_db_context.py:44-49`. Additionally, `SET LOCAL`
   takes no server-side bind parameters, so it works only because psycopg2 interpolates
   client-side; it would **fail on asyncpg**.
6. **Unprotected / not statically establishable:** whether anything outside this repository starts
   the listener (a Procfile, ECS task definition or Helm chart not in-tree) is **unresolved**.

### 3.3 What U5 settles

UR-3 from the prior audits is **resolved**: neither module provides production tenant-context
coverage. Two consequences the prior reports did not state:

* **`core/tenant_db_context.py` is not "the missing half" — it is the only correct implementation,
  and there is no partial coverage to build on.** The prior framing implied two working sites plus
  a gap; in fact one site is dead and the other is mis-ordered and swallowed.
* **If the fill listener is ever wired up, it arms a broken tenant-context path**, not a working
  one. Whoever wires it must replace `begin()`'s `SET LOCAL` with `apply_tenant_context` *after*
  the transaction opens. Worth recording against that future change rather than discovering then.

**No claim of global tenant isolation is made.** A validator's existence and a passing unit test are
not enforcement; §3.1 shows the validator never runs at all.

---

## 4. U6 — settlement service write and failure-path audit

`backend_app/backend/marketplace/settlement_service.py`, 1924 lines. Read: module constants
(`:237-399`), `SettlementOutcome`/`SettlementResult` (`:399-508`), exceptions (`:508-556`),
`grant_deployment_permission` (`:564-634`), `_Attempt` (`:634-688`), `settle` (`:688-849`),
`_settle_once` (`:849-1044`), `_insert_settlement` (`:1343-1423`), `_apply_transition` (`:1423-1592`),
`_write_transition_row` (`:1592-1642`), plus the audit writers and helper predicates.

### 4.1 Write-path matrix

| step | table | op | columns / payload | guard |
|---|---|---|---|---|
| 1 | `library_subscriptions` | **SELECT** (`_read_subscription`, `:1044-1069`) | `SETTLEMENT_SUBSCRIPTION_SELECT` (`:284`) | none — the correlating read |
| 2 | `marketplace_settlements` | **INSERT** (`_insert_settlement`, `:1392`) | `_settlement_payload` (`:1291`): subscription_id, listing_id, owner_id, purchaser_id, provider, provider_reference, amount_minor, owner_share_minor, platform_fee_minor, currency, is_reversal, reverses_reference, settled_at | **`uq_settlement_reference_reversal`** (`:335`), SQLSTATE **`23505`** (`:339`) |
| 3a | `library_subscriptions` | **UPDATE** (`_apply_transition`, `:1549-1550`) | status, period_start, period_expiry, expires_at | **`.eq("status", <status read in step 1>)`** — optimistic, matches once |
| 3b | `library_subscription_transitions` | **INSERT** (`_write_transition_row`, `:1627`) | history row | — |
| 4 | `deployment_permissions` | **INSERT** (`grant_deployment_permission`, `:617`) | granted_via=`subscription`, expires_at = period expiry | — |
| — | audit trail | INSERT ×1 per outcome | `MARKETPLACE_SETTLEMENT_{CREATED,DUPLICATE_IGNORED,UNMATCHED,MISMATCHED,PERSIST_FAILED}` | — |

**Order is deliberate and documented:** money is recorded (step 2) **before** anything is granted
(steps 3–4). The 90/10 split is computed **once at the boundary** before any statement
(`money.split_ninety_ten`, `:773`), so an inadmissible amount is refused before a row is written and
every retry records identical components.

### 4.2 Transaction / session ownership — the structural fact

**There is no SQL transaction spanning the four writes, and no `commit()` or `rollback()` anywhere
in the file.** All access is PostgREST through the Supabase client, where each `.execute()` is its
own autocommitted statement.

In place of a transaction the module uses a **resumable state machine**: `_Attempt` (`:634-688`)
with `subscription`, `insert_issued`, `settlement_recorded`, `settlement_id`, `status_moved`,
`transition_written`, `entitlement_granted`, `period_start`, `period_expiry`, `from_status`,
`to_status`. `_settle_once` consults it before each step and records completion, so *"retrying is
resumption, not repetition."* `status_moved` is tracked separately from `transition_written`
precisely because the UPDATE is `.eq("status", …)`-guarded and can only ever match once.

### 4.3 Idempotency and duplicate protection — genuinely careful

`_insert_settlement` (`:1389-1414`) distinguishes two readings of one unique violation using
`resumed = state.insert_issued`, which is set **before** the statement is sent:

* `resumed == False` → the row belongs to **another delivery** → return duplicate → `_settle_once`
  emits `DUPLICATE_IGNORED` and **returns early**.
* `resumed == True` → **this call's own insert** landed but its acknowledgement was lost → mark
  `settlement_recorded = True` and **continue to steps 3 and 4**.

Retries are bounded in both count (`MAX_SETTLEMENT_ATTEMPTS = 5`, `:321`) and wall clock
(`SETTLEMENT_RETRY_WINDOW_SECONDS = 60`, `:325`), with a pre-check at `:811-822` that refuses to
start an attempt whose backoff would exceed the window.

**Nothing is swallowed.** Every `except Exception` in the file is converted to
`SettlementPersistenceError` and annotated *"converted to the defined outcome, never swallowed"*;
exhaustion raises `SettlementPersistFailed` (`:844`), which `billing.py:1082-1090` turns into HTTP
500 so the provider redelivers. **This is materially better discipline than `billing.py`'s own
invoice insert**, which logs a warning and continues (CD-7).

`UNMATCHED` / `MISMATCHED` write nothing; the audit line is the response and the webhook returns
success because there is nothing for the provider to retry.

### 4.4 Partial-completion risk — PROPOSED CD-8 (new, distinct)

**The `_Attempt` state is in-process memory only.** It is constructed per `settle()` call
(`attempt_state = _Attempt()`, `:776`) and is never persisted. So the `insert_issued` discriminator
that makes §4.3 correct **cannot survive process death**.

The module's own docstring states the hazard it was built to prevent (`:639-645`):

> *"Without this, a transient failure at the transition UPDATE would restart at the insert, hit
> `uq_settlement_reference_reversal` with its own row, report `DUPLICATE_IGNORED` and leave a
> recorded payment that never granted anything — which is worse than the failure it was recovering
> from."*

That is solved for an **in-process exception**. It is **not** solved for a **crash**:

1. Step 2 inserts the ledger row — owner earning recorded.
2. The process dies (pod restart, OOM, deploy) before step 3 or 4.
3. The provider redelivers. A **fresh** `_Attempt` has `insert_issued = False`.
4. The insert raises 23505; `resumed == False` → read as another delivery → **`DUPLICATE_IGNORED`,
   early return before steps 3 and 4**.
5. `billing.py` treats `DUPLICATE_IGNORED` as success and returns 200.

**Net effect: the customer paid, `marketplace_settlements` records the settlement and the owner's
90% earning, `library_subscriptions` is never activated, `deployment_permissions` is never granted,
and every redelivery reports success.** Reached by exactly the route the docstring calls "worse than
the failure it was recovering from" — a crash rather than an exception.

**No backstop exists.** Searched all of `backend_app/` for `marketplace_settlements` outside this
module: the only hit is a comment at `billing.py:985`. **There is no reconciliation sweep for
settled-but-inactive subscriptions.** The only trace is an audit row an operator would have to go
looking for.

**Severity (proposed): P2.** Owner: O3 + the marketplace owner. **Distinct from the existing
findings, and deliberately not merged with them:** it is not cross-tenant (so not TENANT-01); it is
not a duplicate financial effect (so not CD-5 — it is the *opposite*, an effect that never
completes); and it is not a schema disagreement (so not CD-7). Per the brief, CD-7's live schema
uncertainty continues to block any billing uniqueness design, and **CD-8 does not change that** —
CD-8 concerns `marketplace_settlements`, whose constraint demonstrably exists, not
`billing_invoices`, whose columns are unconfirmed.

### 4.5 Test coverage per critical path

| path | covered? | where |
|---|---|---|
| successful settle, ledger + split + period + entitlement | **yes** | `test_settlement_service.py` (46 refs), `test_billing_e2e.py` |
| **in-process** transient failure resumes rather than restarts | **yes** | `test_settlement_service.py:1027` `test_a_transient_failure_is_resumed_rather_than_restarted`; `:1022` asserts `settlement_recorded is True` |
| genuine second delivery → `DUPLICATE_IGNORED` | **yes** | `test_settlement_service.py:788-855, :1149, :1849`; `test_marketplace_concurrency.py:1421`; `test_billing_e2e.py:1266, :1519` |
| amount/currency mismatch → nothing written | yes | `test_settlement_service.py` |
| retry budget / window exhaustion → `SettlementPersistFailed` | yes | `test_settlement_service.py:1286` |
| **process death between step 2 and step 4 (CD-8)** | **NO** | **no test constructs a fresh `_Attempt` against an already-inserted ledger row and asserts the entitlement outcome** |
| reconciliation of settled-but-inactive | **NO — no such code exists** | — |

**Evidence class:** all of the above are **mock/double-based** (`_MarketplaceSupabase`, audit
recorders). `test_bug056_library_subscriptions_updated_at_postgres.py` is real-PG but covers the
`updated_at` trigger, not settlement completion. **No real-PostgreSQL settlement test exists.**

### 4.6 Unresolved in U6

* Whether `marketplace_settlements`, `library_subscription_transitions` and `deployment_permissions`
  carry the columns `_settlement_payload` writes — **not verified against migrations in this pass**,
  and in principle the same class of risk as CD-7. Marked unresolved.
* The live presence of `uq_settlement_reference_reversal`. The code's duplicate handling depends
  entirely on it; its absence would silently convert step 2 into unbounded duplicate ledger rows.
  **Requires D7.**

---

## 5. Unresolved owner decisions and evidence needed

Carried from the predecessor, now with U4–U6 evidence attached. **None approved.**

| # | decision | owner | what U4–U6 added | still needs |
|---|---|---|---|---|
| D1 | who wires the GUC | O1 | **U5: there is no partial coverage to extend — `tenant_db_context.py` is the only correct implementation** | O1's answer |
| D2 | may `database_pool.py` be modified | O1 + O3 | unchanged | — |
| D3 | application login role | O4 | unchanged | live role attrs (D7) |
| **D4** | **gate `create_all`** | **O3** | **U4: 4 direct sites; 93 indirect; 25 candidate dependants; 3 sibling trees unresolved** | **withhold-and-measure run (needs D4 itself)** |
| D5 | `FORCE RLS` migration | O4 | U5 strengthens the "do not force early" rule — **no** path sets the GUC today | D7 evidence |
| D6 | billing remediation | O3 | **U6: CD-8 is in the same blast radius** | CD-7 resolution first |
| D7 | PG lane | O5 | needed for `uq_settlement_reference_reversal` and the settlement column set | O5 authorization |
| D8 | accept CD-4 / CD-5 / CD-7 | register owner | **+ CD-8 proposed** | register owner's decision |

---

## 6. Prioritized next actions

| # | action | evidence basis | owner | approval / live PG needed? |
|---|---|---|---|---|
| **1** | **Open CD-8** (settlement partial completion) and decide whether a reconciliation sweep for settled-but-inactive subscriptions is in scope. Highest value: it is a money-and-access defect with **no test and no backstop**. | §4.4, §4.5 — read from source; the module's own docstring names the failure mode | O3 + marketplace owner; register owner for acceptance | **no approval to open; a fix needs D6** |
| **2** | **Record against any future fill-listener wiring** that `TransactionalExecutionManager.begin()` must use `apply_tenant_context` *after* `db.begin()`. Cheap now, expensive later. | §3.2 — `SET LOCAL` at `:222` precedes `begin()` at `:233`; failure swallowed to `debug` | O3 / execution owner | no |
| **3** | **Consider deleting or marking `tenant_rls_validator.py`** — zero importers, and its presence has twice been cited as partial coverage that does not exist. | §3.1 | O1 | no — but it is O1's file |
| **4** | **Run the withhold-and-measure experiment for D4** against the 25 candidates then the 93 superset. | §2.3, §2.5 | O3 | **D4 (edits `main.py`)** |
| **5** | **Resolve the sibling-tree question** — is `connection_layer/` or `aerora_quant_platform/` deployed? If so, A1 exists there too. | §2.2 | deployment owner | no |
| **6** | **Add the settlement column/constraint check to the D7 query list** alongside CD-7's `billing_invoices` questions — one DB session answers both. | §4.6 | O5 | **D7** |
| **7** | **A real-PostgreSQL settlement test** (currently all mock-based) covering CD-8 and the unique constraint. | §4.5 | O6 | **D7** |

Actions 1, 2, 3 and 5 need **no approval and no database** — they are the fastest value available.

---

## 7. Commands executed

All read-only. **No test was run in this phase** — U4/U5/U6 are static questions, and the brief
directs against running suites unless a specific uncertainty requires it. None did.

| # | operation | result |
|---|---|---|
| 1 | baseline digest script (`git rev-parse` / `write-tree` / normalized `diff --cached` SHA-256 / three path counts) | §1 — exit 0 |
| 2 | `Test-Path` on the output path | absent → safe to create |
| 3 | repository-wide `metadata.create_all|create_all\(` sweep, vendor trees excluded | §2.1–2.4 — exit 0 |
| 4 | `tests/*.py` importers of `backend_app.main`, then refined by ORM/session surface | 93 → 25 — exit 0 |
| 5 | importer sweeps for `transactional_execution_manager` / `tenant_rls_validator` across `backend_app/`, `tests/`, `scripts/` | §3.1 — exit 0 |
| 6 | targeted reads: `exchange_websocket_listener.py:470-515`, `transactional_execution_manager.py:205-245`, `tests/test_json_extract_dialect_portability.py:36-52` | §3.2 |
| 7 | `settlement_service.py` structure sweep (defs/classes/constants; writes/rpc/commit/except) + targeted reads of `:634-688`, `:688-849`, `:849-979`, `:1380-1425` | §4 |
| 8 | test-coverage sweeps for `settlement_service`, `DUPLICATE_IGNORED`, resume flags, and any `marketplace_settlements` reconciliation outside the module | §4.5 — exit 0 |

**Limitations:** three PowerShell invocations were interrupted by the console (`^C`, exit −1) and
were re-run successfully in a different form; no partial state resulted because every command was a
read. `settlement_service.py` was read in the regions listed in §4, not byte-for-byte in full —
the unread remainder is audit writers and small helper predicates whose behaviour is described by
their call sites. Stated rather than implied.

---

## 8. Exit verification

```
HEAD                 = 074b772ab16cece62cbfca0f2b78497be2ae56d5
INDEX_TREE           = 6f40856cee2bc7ea4d741f69f4b1f68b463d70a4
STAGED_DIGEST_SHA256 = 65dfc45367ced6f67573f3ae485a1c92d63c803b4d881b6a186afd3d54a5bd0c
STAGED_COUNT         = 22
UNSTAGED_COUNT       = 100
```

Measured values are recorded in §9 of the session summary. No tracked file was modified; every
tracked modification present belongs to another workstream. The only new artifact is this report.

**Nothing here establishes exchange correctness, exactly-once execution, paper-trading
certification, or live-trading readiness. TENANT-01 OPEN · QUOTA-01 OPEN · PostgreSQL enforcement
UNEXECUTED · live trading OFF / NOT CERTIFIED.**
