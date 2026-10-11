# Phase 10 — Workstream D: Financial Integrity and Reconciliation

Scope: the four Phase 9 observations recorded in `staging/full_audit_results.json`
(and `staging/PHASE9_DESIGN_DECISIONS.md` §13) as out-of-scope follow-ups.

**Live trading was OFF throughout.** `staging/probe_phase10_env.py` reported no
`LIVE*` / `TRADING*` environment variable set in the probing process. No exchange was
contacted, no payment provider was called, no transfer was initiated. Every database probe
ran inside a transaction that was rolled back; the only durable database change in this
workstream's area is migration `031`, which was applied by the concurrent tab described in
§0, not by this one.

---

## Verdicts

| # | Observation | Verdict | Status |
|---|---|---|---|
| 1 | `fee_records.fee_pct` NOT NULL while `record_fee` writes `None` | **REAL DEFECT — fee record lost** | Fixed (by the concurrent tab, §0); verified |
| 2 | `referral_payouts` `payment_method` vs `payout_method` | **REAL DEFECT — and not cosmetic: balance debited, no payout row** | Column fix by the concurrent tab; **the uncompensated-debit half fixed here** |
| 3 | Referral pending→approved settlement lifecycle incomplete | **CONFIRMED INCOMPLETE** | Documented; unsafe transitions made to fail safely except one, which is a prepared, unapplied migration |
| 4 | `ExchangeReconciliationService` never started | **CONFIRMED never started**, and starting it as-is would be unsafe | Not started, by decision; its two no-evidence paths fixed |

Three further defects were found while testing observation 1 and are recorded in §1b. None
was in the Phase 9 list.

**Stop conditions reached and reported:** two *demonstrated* losses — a fee record
(§1) and a referrer's approved balance (§2). Both are now remediated. No ledger imbalance,
duplicate obligation, unauthorized payout transition or false paid status was demonstrated;
the one latent false-paid exposure is §3's open item and is unreachable from application
code today.

---

## 0. Concurrency: another tab was editing the same files mid-session

This must be read first, because it determines who fixed what.

At session start, `backend_app/backend/fee_engine.py` declared
`fee_pct = Column(String, nullable=False)` and no migration `031` existed. While this
workstream was probing, those files changed underneath it:

| Local time | File | Change |
|---|---|---|
| 04:18 | `backend_app/routers/referral.py` | payout insert switched to `payout_method` / `payout_details` |
| 04:22 | `backend_app/backend/fee_engine.py` | `fee_pct` → `nullable=True` |
| 04:23 | `backend_app/migrations/031_fee_records_fee_pct_nullable.sql` | created **and applied** to the live database |
| 04:27 | `tests/test_fee_third_asset_pct_is_nullable_postgres.py` | created |

So observations 1 and 2 were remediated by a concurrent tab working the same ownership
area. This workstream did **not** duplicate or revert that work. What it did instead:

- Kept its own **pre-fix measurements**, which are the evidence that the observations were
  real (the probe at 04:20 caught the `NotNullViolation` before `031` was applied).
- Verified the remediation rather than redoing it.
- Fixed the parts the remediation did not reach: the uncompensated wallet debit (§2), and
  three further fee-accounting defects (§1b).
- Used surgical `str_replace` edits on `referral.py` and `fee_engine.py` rather than
  whole-file writes, so the concurrent tab's changes were preserved. **Residual risk:** if
  that tab writes those files from a stale buffer, the §1b and §2 edits are lost. They are
  listed in §6 so they can be re-checked.

---

## 1. Observation 1 — third-asset fee accounting

### Was the percentage actually unavailable?

Yes, and the schema was the thing that was wrong.

`fee_pct` is a **derived** figure: `fee_cost_in_quote / trade_volume`. The ratio only means
anything if both sides are in one currency. For a fee denominated in a third asset — a BNB
fee on BTC/USDT, which is the Binance default and carries a discount, so it is the common
case — `record_fee` has no market-data handle and no rate, so it correctly set
`fee_cost_in_quote = None` and `fee_pct = None`.

The column was `character varying NOT NULL`. Measured on the live database
(`staging/probe_p10d_fee_pct_not_null.py`, inside a rolled-back transaction, with
`auto_commit=False` so the INSERT reaches the server without committing):

```
CONTROL  — USDT fee on BTC/USDT (quote asset): ACCEPTED, fee_pct='0.00050000'
SUBJECT  — BNB  fee on BTC/USDT (third asset): REJECTED
           (psycopg2.errors.NotNullViolation) null value in column "fee_pct"
           of relation "fee_records" violates not-null constraint
```

The control is what makes this a verdict rather than a coincidence: only the fee asset
differed. `record_fee` has no `try` around `db.add` / `flush` / `commit`, and
`record_fill_fee` is a thin delegate, so the exception propagates to the fill handler and
leaves the session's transaction aborted — any later write in it fails with 25P02.

**A fee the venue actually charged produced no `fee_records` row at all.**

Why the existing test could not see it: `tests/test_fee_denomination_is_not_assumed.py`
uses a `_FakeSession` whose `commit()` is `pass`. It proves the Python-level semantics
correctly and no statement ever reaches a server.

### Disposition

Making `fee_pct` nullable is the right fix and was not done blindly:

- It is derived, like `gross_pnl` and `net_pnl`, which were **already** nullable for this
  exact reason. `fee_amount` and `fee_asset` — the fee actually charged and its
  denomination — remain NOT NULL, so no money ever becomes optional.
- `0` is wrong: a zero fee is a real measurement (a full rebate), and
  `test_a_zero_fee_is_recorded_as_zero_not_absent` already pins that. Using `0` for
  "unknown" would make a rebate indistinguishable from an unconvertible fee.
- A sentinel string would break every `Decimal(rec.fee_pct)` consumer. NULL is the
  SQL-native "not applicable", and consumers already handle it for `net_pnl`.
- Existing records: `fee_records` held **0 rows**, so there was no backfill question.
- Consumers: the only reader of `fee_pct` outside the engine is
  `scripts/attack_money_control_v2.py`, which reads it for a quote-asset fee. No API
  response and no frontend field exposes it.

Applied by the concurrent tab as `031_fee_records_fee_pct_nullable.sql`. Verified against
the live catalogue after application:

```
LIVE fee_records.fee_pct is_nullable = YES
comment: The fee as a fraction of trade volume, or NULL when it is not computable. ...
         NULL rather than 0 (which would falsely state a free trade) or a fabricated
         rate (forbidden). Phase 10 observation A.
```

---

## 1b. Three further fee defects found while testing observation 1

These were **not** in the Phase 9 list. All three are fee-accounting defects in this
workstream's ownership and all three are fixed here.

### A. `get_fee_summary` added amounts in different currencies

The row-level fix stopped treating a BNB fee as quote currency per record. The summary —
the figure an operator reads — went on doing exactly that:

```python
totals[fee.fee_type] += Decimal(fee.fee_amount)   # denominated in fee.fee_asset
total_fees   = sum(totals.values())
avg_fee_rate = total_fees / total_volume * 100    # volume is quote currency
```

A 30 USDT taker fee plus a 0.0005 BNB taker fee totalled `30.0005` — a number in no
currency — and the average rate then divided it by a quote-denominated volume.

**Fix:** group by `fee_asset`. Under a single denomination the totals are unchanged (the
ordinary case, and the one `tests/test_tenant02_fee_records_rls_postgres.py` pins). With
more than one asset, `total_fees`, `avg_fee_rate_pct` and `by_type` are reported as
**absent** and `totals_by_asset` / `by_type_and_asset` carry every fee in its own unit —
the convention `record_fee` already follows. No conversion is invented. Nothing is dropped:
`count` always counts every fee.

### B. A recorded fee could make the whole ledger unreadable

`totals` was seeded with four of `FeeType`'s **six** members, so
`totals[fee.fee_type] += ...` raised `KeyError: <FeeType.WITHDRAWAL: 'withdrawal'>` (and
the same for `DEPOSIT`) and took the entire summary down. Measured, both variants.
**Fix:** derived from the enum, so a member added later is included automatically.

### C. `fee_id` collided within a second, losing the second fee

```python
fee_id = f"fee_{execution_id}_{int(datetime.utcnow().timestamp())}"
```

Whole-second resolution and no fill component. Two fees against one execution inside the
same second shared a primary key, and the second died on `fee_records_pkey` with a
`UniqueViolation` no caller catches — the fee lost, the transaction aborted. One execution
has as many fees as it has fills, and a market order sweeping a liquid book fills several
times in well under a second.

This is not idempotency: the identical duplicate call one second later succeeds, so the old
id deduplicated by luck of the clock. `record_fee` is append-only — the behaviour the
concurrent tab's own test pins, which had to `time.sleep(1.1)` to obtain two rows and
recorded the collision as "a known limitation, not this test's subject".

**Fix:** a `uuid4().hex[:12]` suffix. `record_funding_fee` deliberately **keeps** its
deterministic `fee_funding_{position_id}_{funding_period}` id — there is exactly one
funding fee per position per period, so there the primary key *is* the idempotency key and
a collision is the correct outcome. The two cases differ and are now treated differently;
a test pins that the funding id did not acquire a random component.

---

## 2. Observation 2 — referral payout schema

### The column question, settled against the catalogue

Phase 9 recorded this as "probable but not yet confirmed against the catalogue". Confirmed
(`staging/probe_p10d_referral_payout_schema.py`):

```
CATALOGUE — live public.referral_payouts
  ABSENT   payment_method
  PRESENT  payout_method    nullable=NO  default=None
  ABSENT   payment_details
  PRESENT  payout_details   nullable=YES default='{}'::jsonb

THE APPLICATION'S INSERT (referral.py, key for key)
  REJECTED — UndefinedColumn
             column "payment_method" of relation "referral_payouts" does not exist
NEGATIVE CONTROL — identical insert, live column names
  ACCEPTED — payout_method='bank_transfer' currency='USD' status='pending'
```

They are **one** logical field under two spellings, declared twice by two migrations that
both `CREATE TABLE IF NOT EXISTS public.referral_payouts`.
`migrations/006_reconcile_production_database.sql` runs first and wins;
`migrations/referral_system_redesign.sql`'s declaration is a no-op — the same
dual-declaration class as BUG-035, which that file documents for the sibling
`referral_wallets` table while providing no equivalent forward migration for payouts.
Migration `027` then added `currency` to 006's shape with a `DEFAULT` precisely so the
application's insert would not break, which is independent confirmation of which shape is
live. The table held **0 rows**, so there was no data-migration question.

The fix is in the application, not the schema, and it is the smallest compatible one: the
DB write uses `payout_method` / `payout_details`; the HTTP field stays `payment_method`, so
the deployed frontend (`algo22-terminal/src/api/modules/referral.js`) is unaffected.
Applied by the concurrent tab.

### The half the column fix did not reach — fixed here

`create_payout_request` does, in order: **(1)** debit `approved_balance_usd` via a CAS
update, **(2)** insert the payout row, **(3)** if the insert *returned no rows*, re-credit
and answer 500.

Step 3 was reachable only through `if not payout_resp.data or len(payout_resp.data) == 0`.
An insert that **raises** never produces a `payout_resp`: control jumps to the handler's
`except Exception`, which logged and answered 500 **without re-crediting**. So any raising
failure — PostgREST error, RLS denial, CHECK or FK violation, timeout, Supabase 5xx — left
the balance reduced with no payout row, and nothing reconciles that: there is no referral
audit table and no settlement job.

Proven in `tests/test_referral_payout_debit_is_compensated_on_insert_failure.py`, which
drives the real handler through the real app with a stub whose payout insert raises. Before
the fix:

```
test_a_successful_payout_debits_the_balance_once                PASSED
test_an_insert_that_returns_no_rows_is_compensated   (CONTROL)  PASSED
test_an_insert_that_raises_is_also_compensated                  FAILED  <-- the defect
test_no_payout_row_is_claimed_when_the_insert_failed            PASSED
```

The control passing is what makes this a verdict: the harness drives the handler correctly,
and only the raising path kept the debit (500.00 → 450.00, zero payout rows).

**Fix:** both failure modes now call one `_compensate_debit()`. A compensation that itself
fails does not raise — it logs `CRITICAL` with user, amount, both balances and both failure
causes, because that is the one case where money really is unaccounted for and it must not
disappear into a generic 500. After the fix: **4 passed**.

### Secondary finding, not fixed (API contract, needs coordination)

`create_payout_request`'s success response has a **duplicate `status` key**:

```python
return {"status": "success", "payout_id": ..., "amount_usd": ..., "status": "pending"}
```

The second wins, so the response is `status: "pending"`, never `"success"`. A client
checking for `"success"` would treat a *created* payout as failed and may retry — a
duplicate-payout-request risk. The frontend's JSDoc carries the same duplicated key.
Fixing it changes a published response shape, so it is reported rather than changed.

---

## 3. Observation 3 — referral settlement lifecycle

### Confirmed incomplete

`staging/probe_p10d_settlement_lifecycle.py`, asking `pg_proc` rather than the migration
files, because what matters is what is installed:

```
referral functions installed: ['create_referral_code_for_user',
  'generate_unique_referral_code', 'process_referral_commission',
  'record_referral_obligation', 'reverse_referral_commission',
  'reverse_referral_obligation', 'trigger_create_referral_code',
  'update_referral_profiles_updated_at']
routines that write a settlement/paid state: NONE
  (only reverse_referral_commission matched, and a reversal is not a settlement)
```

Accrual and reversal are implemented and idempotent. Settlement is not:

| Ledger | Transition | Implemented |
|---|---|---|
| `referral_commissions` | `∅ → pending` | yes (`process_referral_commission`) |
| | `pending → approved`, `approved → paid` | **no writer** |
| | `{pending,approved,paid} → reversed` | yes, idempotent |
| `referral_wallets` | credit `pending_balance_usd` | yes |
| | `pending → approved_balance_usd` | **no writer** |
| `referral_payouts` | `∅ → pending` | yes (the one fixed in §2) |
| | `pending → processing → completed`, `→ rejected` | **no writer** |
| `referral_obligations` | `∅ → payable / unresolved / refused` | yes (`record_referral_obligation`) |
| | `{unresolved,payable} → reversed` | yes, idempotent, refuses to touch `settled` |
| | `unresolved → payable`, `payable → settled` | **no writer** |
| `referral_relationships` | `pending → active` | **no writer** |

There is **no referral audit or history table** (`referral_*audit*`, `*history*`,
`*transition*`, `*event*` — none exist). The marketplace side has
`library_subscription_transitions`; the referral side has a single `processed_at` with no
actor and no provider reference.

Consequence: `POST /referral/payouts` validates against `approved_balance_usd` with a $10
minimum, and nothing can move money into that column — so no payout request can succeed.

**No money is presently trapped** (`staging/probe_p10d_wallet_stuck_balances.py`):

```
wallet rows = 37;  SUM pending/approved/paid/lifetime = 0.00 / 0.00 / 0.00 / 0.00
referral_commissions = 0   referral_payouts = 0   referral_obligations = 0
```

So this is a capability gap that blocks the *first* payout, not a present loss. Settlement
was **not** implemented here: the brief forbids marking a payout paid without evidence of
payment, and the approval rule (when a commission becomes withdrawable) is a product
decision.

### Do the unsafe transitions fail safely? Four of five, yes

| Attempted write | Result |
|---|---|
| `referral_payouts` → `'paid'` | **REFUSED** (CheckViolation — not in the live vocabulary) |
| `referral_payouts` → `'approved'` | **REFUSED** (CheckViolation) |
| `referral_obligations` → `'settled'` without `settled_at` | **REFUSED** (CheckViolation, `chk_referral_obligations_terminal_timestamps`) |
| `referral_obligations` → `'settled'` with `settled_at` | ACCEPTED (correct — the legitimate write) |
| `referral_payouts` → `'completed'` without `processed_at` | **ACCEPTED — GAP** |

The API's `PayoutRecord` advertises `approved_at` / `paid_at` and the live table has
neither, and cannot hold those states. Failing closed is the safe outcome; the contract
divergence is reported, not papered over.

### The one open gap

`'completed'` is the live table's terminal, money-sent state, and it can be set with
`processed_at` NULL — no time, no actor, no payment reference, on a table with no audit
trail. A false paid status waiting for a settlement job or an operator `UPDATE`. It is not
reachable from application code today (nothing writes any status but `'pending'`).

`backend_app/migrations/032_referral_payouts_terminal_evidence.sql` adds
`chk_referral_payouts_terminal_evidence CHECK (status <> 'completed' OR processed_at IS NOT NULL)`,
mirroring 029's constraint on the sibling ledger, with a precheck that refuses rather than
blesses any pre-existing violating row.

**It is PREPARED AND NOT APPLIED.** It alters a shared table and needs the migration
owner's approval. The requirement is carried in the suite as a `xfail(strict=True)`, so
when `032` is applied the test XPASSes and strict mode fails until the marker is removed —
the gap cannot be forgotten and cannot be silently closed.

---

## 4. Observation 4 — `ExchangeReconciliationService`

### Confirmed: never started

```
main.py references to exchange_reconciliation : NONE
repo-wide callers of get_reconciliation_service: NONE
```

`main.py`'s lifespan starts seven background services (ConsistencyChecker, OrderWatchdog,
PnLEngine, ReconciliationScheduler, PortfolioCacheUpdater, DashboardDataIngester,
AssetUniverseRefresher) and this is not among them.

The only runtime code that tried to use it —
`exchange_websocket_listener._fallback_poll` — imported
`ExchangeReconciliationEngine`, **a name the module does not define** (the class is
`ExchangeReconciliationService`), with no constructor arguments where a `db_session` is
required, inside a bare `try`. Every invocation raised `ImportError` and the handler logged
it as `"STEP 3: Fallback poll error"`, which reads like a transient exchange problem. **The
WebSocket fallback poll has never once run.**

### Should it run? Not as-is — and that is the finding

Starting it would have been the dangerous change, not the safe one:

- **`OrderWatchdog` already reconciles the same rows and is started.** Its
  `_reconciliation_loop` polls `execution_records` every 5–10s with jitter. A second
  reconciler over the same rows needs coordination that does not exist.
- **It is per-tenant** (`reconcile_open_orders(tenant_id)`) and nothing enumerates tenants.
- **Duplicate workers are real.** `startup.sh` runs `gunicorn --workers ${WORKERS:-2}` and
  four ECS task definitions set `WORKERS=2`, so the lifespan runs once per process. No
  lifespan service uses a lock or leader election, and the repo's leader-election package
  does not import.
- **`_handle_ghost_order` is not idempotent**: a deterministic `exec_ghost_<order_id>` primary
  key with no existence check, inserting rows with `price=None` that the exposure `SUM`
  cannot see — so running it could silently widen the risk ceiling.
- Live trading is off, so there is nothing to reconcile today.

Decision: **not started.** Instead the paths that would fabricate an outcome were fixed, so
that whenever it is wired up it fails safely, and the dead fallback was made honest.

### Fixed: two paths that resolved orders with no evidence

**(1) `exchange_client is None` returned `[]`.** An empty list is indistinguishable from
"the venue reports no open orders", and `reconcile_open_orders` acts on that — every
PENDING/EXECUTING row is treated as absent and resolved. A service built by
`get_reconciliation_service(db)` has `exchange_client=None` by default, so its first pass
would have resolved **every** active order having observed nothing. This is the same defect
as BUG-FIX UK-01, which hardened only the *exception* path. Now raises.

**(2) The missing-order fallback invented outcomes.** With no `fetch_order` on the client:
`filled_size > 0` → `transition_to_filled()` + `COMPLETED`; otherwise
`transition_to_cancelled()` + `FAILED` — from absence from a list, with no venue
confirmation, using whatever stale figures the row already held. Commented "conservative";
it is not. It also contradicted ORD-03 and REC-01 *in the same function*, which preserve
state whenever the venue's answer is uncertain. Now `NO_ACTION` with state preserved and a
warning, leaving the row to `OrderWatchdog`'s stale-execution recovery (TRADE-07) — a stuck
`EXECUTING` row is visible and recoverable; a fabricated `COMPLETED` is neither.

**(3) The dead WS fallback** no longer attempts an impossible import. It logs plainly that
no reconciler is wired to that path and names `OrderWatchdog` as the actual coverage.
Correcting the class name was rejected: that would start the reconciler with no session and
no exchange client — exactly the configuration (1) now refuses.

### What is *not* claimed

Everything in §4 is verified with mocks. **No exchange reconciliation was verified end to
end.** No sandbox venue credential exists in any phase, so the claim is only what was
measured: the two no-evidence paths no longer fabricate an outcome, and the service is not
running.

---

## 5. Commands and outcomes

Environment: Windows, Python 3.12, local PostgreSQL from `staging/bootstrap_pg.py`
(`staging/pgdata_r1`, DSN in `staging/pg_dsn.txt`), `AERORA_TEST_DATABASE_URL` set to that
DSN for the PG-gated suites.

### Probes (all read-only or rolled back)

| Command | Outcome |
|---|---|
| `python staging/probe_phase10_env.py` | PG up, 89 public tables, alembic head `d4e5f6a7b8c9`, **no live-trading env var set** |
| `python staging/probe_p10d_columns.py` | live column/constraint dump for `fee_records`, `referral_payouts`, `referral_obligations`; all three 0 rows |
| `python staging/probe_p10d_fee_pct_not_null.py` | **`NotNullViolation` on `fee_pct`** for the third-asset fee; quote-asset control accepted |
| `python staging/probe_p10d_referral_payout_schema.py` | `payment_method` **absent**; app insert `UndefinedColumn`; live-name control accepted; debit 500.00→450.00 with 0 payout rows |
| `python staging/probe_p10d_settlement_lifecycle.py` | no settlement writer installed; 4 of 5 unsafe writes refused; **`completed` without `processed_at` accepted** |
| `python staging/probe_p10d_wallet_stuck_balances.py` | 37 wallets, all balances `0.00`, 0 commissions/payouts/obligations |
| `python staging/probe_p10d_test_pollution.py` | attributed a cross-file test interaction to the pre-fix destructive branch, not to the new tests |

### Test runs

| Command (abbreviated) | Outcome |
|---|---|
| `pytest tests/test_referral_payout_debit_is_compensated_on_insert_failure.py` | **1 failed, 3 passed** before the fix → **4 passed** after |
| `pytest tests/test_p10d_exchange_reconciliation_fails_safely.py` | **4 failed, 4 passed** before the fixes → **8 passed** after |
| `pytest tests/test_p10d_exchange_reconciliation_fails_safely.py tests/test_exchange_safety_fix.py` | **17 passed** (twice, to confirm) |
| `pytest tests/test_p10d_fee_summary_does_not_mix_denominations_postgres.py` | **7 failed** before the fixes (incl. two `KeyError`) |
| fee group: `test_p10d_fee_summary_…` + `test_tenant02_fee_records_rls_postgres` + `test_fee_third_asset_pct_is_nullable_postgres` + `test_fee_denomination_is_not_assumed` + `test_fee01_tenant_is_validated_not_sentinel_postgres` + `test_fee_precision_fix` | **57 passed** |
| `pytest tests/test_p10d_referral_settlement_lifecycle_postgres.py` | **9 passed, 1 xfailed** (the documented `032` gap) |

`tests/test_tenant02_fee_records_rls_postgres.py` passes unchanged. It was **not edited**
(TEST-05 / tenant-isolation fixtures are out of this workstream's ownership), and the
`get_fee_summary` change deliberately preserves the single-denomination contract it asserts.

### Consolidated run (after the outage in §7 was resolved)

Re-run against the recovered `pgdata_r1` cluster (89 public tables, `031` intact), one group
per invocation with a pytest-level `--timeout=240` so that no run could be force-killed
again:

| Group | Files | Result |
|---|---|---|
| FEE (observation 1 + §1b) | `test_fee_third_asset_pct_is_nullable_postgres`, `test_p10d_fee_summary_does_not_mix_denominations_postgres`, `test_fee_denomination_is_not_assumed`, `test_fee01_tenant_is_validated_not_sentinel_postgres`, `test_fee_precision_fix`, `test_tenant02_fee_records_rls_postgres` | **57 passed** |
| REFERRAL (observations 2 + 3) | `test_referral_payout_schema_contract_postgres`, `test_referral_payout_debit_is_compensated_on_insert_failure`, `test_p10d_referral_settlement_lifecycle_postgres`, `test_referral_money_lifecycle_postgres`, `test_money01_…`, `test_money04_…`, `test_money06_…`, `test_referral_missing_tables`, `test_referral_program_decision` | **124 passed, 1 xfailed** |
| RECONCILIATION (observation 4) | `test_p10d_exchange_reconciliation_fails_safely`, `test_exchange_safety_fix`, `test_exchange_reconciliation` | **18 passed** |
| SCHEMA CONTRACTS | `test_orm_models_match_provisioned_schema`, `test_schema_table_reference_drift`, `test_schema_as_code_completeness` | **96 passed, 1 xfailed** |

**Total: 295 passed, 2 xfailed, 0 failed.** The two xfails are the documented `032` gap
(§3) and one pre-existing xfail in `test_orm_models_match_provisioned_schema.py`.

`test_orm_models_match_provisioned_schema.py` passing is an independent cross-check on §1:
the `FeeRecordModel.fee_pct = Column(String, nullable=True)` declaration and the provisioned
`fee_records.fee_pct` column now agree. Had `031` been applied without the model change, or
vice versa, that suite would have caught the divergence.

Post-recovery state verified before the run (`staging/probe_p10d_wait_for_pg.py`):

```
UP: public tables=89 in_recovery=False  PostgreSQL 16.2
   fee_records.fee_pct is_nullable = YES
   referral_payouts method column  = ['payout_method']
```

---

## 6. Changed files

### Changed by this workstream

| File | Change |
|---|---|
| `backend_app/backend/fee_engine.py` | `get_fee_summary` groups by fee asset and covers the whole `FeeType` enum (§1b A, B); `record_fee`'s `fee_id` gains a `uuid4` suffix (§1b C); `uuid4` import |
| `backend_app/routers/referral.py` | `_compensate_debit()` runs on **both** payout-insert failure modes; a failed compensation logs CRITICAL (§2) |
| `backend_app/backend/exchange_reconciliation.py` | no exchange client now raises instead of returning `[]`; the unconfirmable missing-order fallback returns `NO_ACTION` (§4) |
| `backend_app/backend/exchange_websocket_listener.py` | `_fallback_poll` no longer imports a non-existent class; logs that it is not implemented (§4) |
| `backend_app/migrations/032_referral_payouts_terminal_evidence.sql` | **NEW, PREPARED, NOT APPLIED** — needs migration owner approval (§3) |
| `tests/test_referral_payout_debit_is_compensated_on_insert_failure.py` | NEW — 4 tests |
| `tests/test_p10d_exchange_reconciliation_fails_safely.py` | NEW — 8 tests |
| `tests/test_p10d_referral_settlement_lifecycle_postgres.py` | NEW — 9 tests + 1 strict xfail |
| `tests/test_p10d_fee_summary_does_not_mix_denominations_postgres.py` | NEW — 9 tests |
| `staging/probe_p10d_*.py`, `staging/run_p10d_suite.ps1` | NEW — evidence probes |
| `staging/restart_pgdata_r1.ps1` | NEW — guarded recovery for the §7 outage; aborted unused, retained |
| `staging/PHASE10_WSD_FINANCIAL_INTEGRITY.md` | NEW — this report |

### Changed by the concurrent tab (§0) — verified, not authored here

`backend_app/backend/fee_engine.py` (`fee_pct` nullable),
`backend_app/routers/referral.py` (payout column names, service-role client for
`/referral/validate`), `backend_app/migrations/031_fee_records_fee_pct_nullable.sql`
(**applied**), `tests/test_fee_third_asset_pct_is_nullable_postgres.py`,
`tests/test_referral_payout_schema_contract_postgres.py`.

### Migration dependencies

- `031` (applied, by the concurrent tab) — `fee_records.fee_pct DROP NOT NULL`. Required by
  §1. Depends on `026`. Applied before the last checkpoint, so durable.
- `032` (**not applied**) — `chk_referral_payouts_terminal_evidence`. Depends on `006`
  (the table) and `027` (`currency`). Independent of `031`. Modelled on `029`.
- The shared registry (`staging/full_audit_results.json`, `trading_audit.json`,
  `money_flow_audit.json`, `marketplace_audit.json`) and
  `staging/FINAL_LAUNCH_SCORECARD.md` were **not modified** — coordinator approval
  required.

---

## 7. Infrastructure incident

The staging PostgreSQL went down mid-workstream and did not recover.

From `staging/pgdata_r1/log` (GMT; local = GMT+5:30):

```
00:20:47 GMT [8752] background worker "logical replication launcher" (PID 1860)
                    was terminated by exception 0xC000013A
00:20:47 GMT [8752] terminating any other active server processes
00:20:48 GMT [8752] all server processes terminated; reinitializing
^C
00:20:54 GMT [11092] database system was interrupted; last known up at 00:08:59 GMT
00:21:33 GMT [11092] could not open file "./log": sharing violation
00:22:01 GMT [11092] syncing data directory (fsync), elapsed time: 67.75 s
00:22:02 GMT [11092] database system was not properly shut down; automatic recovery in progress
00:22:02 GMT [11092] redo starts at 0/59E99B0
```

`0xC000013A` is `STATUS_CONTROL_C_EXIT`, and a literal `^C` is in the log. The most likely
trigger is this workstream's consolidated suite command being force-killed when it hit a
30-minute tool timeout, with the console interrupt reaching the postmaster's process group.

Current state: the log has not advanced past `redo starts` in over an hour; the postmaster
(8752) and the recovery process (11092) are both gone; one orphaned `postgres` process
(13772, started 05:50:53 local) survives at **0% CPU** holding the data directory, and
`postmaster.pid` is stale (names the dead 8752, port 49323, status `ready`). Nothing
listens on 49323. **Recovery is wedged, not running.**

One restorative step was taken, using the project's own idempotent tool rather than any
hand surgery: `PG_DATA_DIR=pgdata_r1 .venv/Scripts/python staging/bootstrap_pg.py start`.
`pgserver` calls `pg_ctl ... start` with a hardcoded 10-second timeout, which expired —
unsurprising, since the data-directory fsync alone took 67 seconds on the previous start.
It wrote nothing new to the log, so no new postmaster was launched; the stale pid file and
the orphan process blocked a clean start.

### Intermediate state: the shared DSN briefly pointed at an empty cluster

At 11:09–11:10 a **second, empty** cluster appeared at `staging/pgdata` (postmaster PID
13708, port 56595) and `staging/pg_dsn.txt` was rewritten to point at it. `bootstrap_pg.py`
defaults `DATA_DIR` to `staging/pgdata` when `PG_DATA_DIR` is unset, and its own docstring
says that override exists "so a from-scratch provisioning rehearsal can use a clean
directory" — so this was most likely a deliberate clean-provisioning rehearsal by the
concurrent tab. Measured against that DSN: `public tables = 0`,
`fee_records.fee_pct = ABSENT`. Any PG-gated suite run in that window would have been
testing an empty database.

`staging/restart_pgdata_r1.ps1` was written to bring the real cluster up on its own port
**without** modifying the shared `pg_dsn.txt` and without touching the other cluster, so
neither workstream would disturb the other. It was not needed: its step-0 safety check
aborted with `a postmaster for pgdata_r1 is already running, PID 12924`, because the
concurrent tab had meanwhile restarted the correct cluster and repointed the DSN. The
script is retained for the next occurrence.

### Resolution

`pgdata_r1` is up on port 51805, `staging/pg_dsn.txt` points at it, and the data survived
intact — 89 public tables, `pg_is_in_recovery() = false`, migration `031` still applied
(`fee_pct is_nullable = YES`), `referral_payouts` carrying `payout_method`. The consolidated
suite was then run in full (§5): **295 passed, 2 xfailed, 0 failed**.

No data-directory deletion and no `initdb` were performed by this workstream, and no
database process was force-killed by it.

**Operational lesson, recorded because it caused the outage:** long test invocations must
carry a pytest-level `--timeout` and be sized well under the harness timeout. A
force-killed shell sent `STATUS_CONTROL_C_EXIT` into the postmaster's process group and
took down shared infrastructure for roughly five hours. The re-run in §5 is split one group
per invocation with `--timeout=240` for exactly this reason.

---

## 8. Remaining risks

1. **`032` is unapplied**, so `referral_payouts` can still be marked `'completed'` with no
   `processed_at`. Latent: unreachable from application code today. Carried as a strict
   xfail.
2. **Referral settlement is unimplemented end to end.** No route from a credited commission
   to a paid referrer. Blocks the first payout. Needs an approval rule (product), a
   provider payment reference (schema), and an audit trail (schema) — none of which exist.
3. **No referral audit trail.** Any future settlement job would have nowhere to record who
   approved what, when, and against which provider payment.
4. **The payout response's duplicate `status` key** reports `"pending"` and never
   `"success"`; a client retrying on that could create duplicate payout requests. API
   contract change, not made.
5. **`ExchangeReconciliationService` remains unstarted and partly unsafe.**
   `_handle_ghost_order` is still non-idempotent and still inserts `price=None` rows
   invisible to the exposure `SUM`; `check_order_exists_on_exchange` still returns `None`
   both for "not found" and for any error, which would be a duplicate-submission hazard for
   a retry path. Both are latent (no caller) and out of this workstream's fix scope; the
   service must not be wired up before they are addressed, along with tenant enumeration
   and multi-worker coordination.
6. **`record_funding_fee` writes `fee_pct="0"` with `trade_volume="0"`.** A percentage of
   zero volume is undefined, and `"0"` asserts the fee was 0% of the trade. Pre-existing,
   not the reported observation, and now representable as NULL thanks to `031` — but not
   changed here, because funding fees have no trade volume by nature and the right
   representation is a question for whoever owns funding reporting.
7. **Concurrent-edit risk on `fee_engine.py` and `referral.py`** (§0). Surgical edits were
   used, but a whole-file write from the other tab's stale buffer would silently drop the
   §1b and §2 fixes. The four new test files would then fail, which is the intended
   detection.
8. **No end-to-end exchange or payment-provider verification.** No sandbox venue
   credential and no payment-provider sandbox was used. `TRADING PAPER` and `PRODUCTION`
   remain NOT CLASSIFIED, consistent with Phase 9.
9. **The staging cluster is one crash-restart away from confusing a future run.** Two data
   directories now exist — `staging/pgdata_r1` (the provisioned one, 89 tables) and
   `staging/pgdata` (empty, from the 11:09 rehearsal). `bootstrap_pg.py` defaults to the
   empty one, and `start()` overwrites the shared `staging/pg_dsn.txt`. Anyone running it
   without `PG_DATA_DIR=pgdata_r1` silently repoints every PG-gated suite at an empty
   database, where the tests skip or fail for reasons unrelated to the code. Worth either
   deleting the stray directory or making the default explicit — neither done here, since
   the rehearsal may still be in use.
