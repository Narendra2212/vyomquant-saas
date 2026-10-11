# POST-LAUNCH IMPROVEMENTS

**Companion to:** `FINAL_PRE_LAUNCH_TEST_REPORT.md`, `FINAL_PRE_LAUNCH_BUGS.md`
**Audit date:** 2026-10-05

This document separates what genuinely blocks launch from what merely wants doing. The distinction matters under a two-day deadline: treating everything as urgent is the same as having no priorities, and shipping a cosmetic fix carries its own risk.

**Nothing in the second and third sections should delay launch.** If something in those sections looks alarming, it is in the wrong section and should be moved, not worked around.

---

# MUST FIX BEFORE LAUNCH

Three code items and six verification items. The verification items are not optional in a different way from the code items — a trading system whose money path has never been exercised against a venue is not in a launchable state regardless of how clean its test suite is.

---

## Code

### 1. Debit the position notional on open — BUG-016

**Why it blocks.** `ExecutionEngine.open_position` moves only the fee out of equity. Reproduced: a 5,000 notional position moved 5.00 of equity. Three consequences, each independently serious:

- `cash + positions ≠ equity`. Every figure derived from equity in this engine is wrong while a position is open.
- `get_drawdown()` reads realized-only equity, so **an open position down 40% reports zero drawdown**. The max-drawdown kill switch cannot fire on open risk — which is the only risk that matters while a trade is live.
- The "insufficient capital" check passes repeatedly, so N positions can each be worth up to the whole account. The only aggregate brake is `max_open_trades = 5`, capping exposure at ~500% of equity by accident rather than design.

**The fix already exists in this repository.** `backend/paper/paper_accounting.apply_fill` derives cash as the residual of the position-value change:

```python
cash_delta = -(new_value - old_value) - fill_fee
```

so Δequity ≡ −fee for all five position cases, by construction rather than by case analysis. Converge the legacy engine on that model rather than patching the debit in.

**Why this was not fixed during the audit.** It changes the equity trajectory of every paper run and every backtest. Those numbers are the headline on the Backtester screen and the evidence behind marketplace listings. The change is correct and will move published performance figures, so it needs its own verification pass and a decision about historical backtest results — not a same-day edit inside an audit.

**Effort:** 1–2 days including re-verification of backtest outputs.

---

### 2. Refuse to start on a short `JWT_SECRET` in production — BUG-012

**Why it blocks.** The observed secret is 24 characters, below RFC 7518's 32-byte minimum for HS256, and `config.py` only logs a warning. With BUG-002 fixed this secret no longer signs production authentication tokens — which is what reduces it from P0 to P1 — but it remains in HMAC use and a short secret is materially cheaper to attack.

**Fix.** Raise at startup when `ENV=production` and the secret is under 32 bytes. One line, plus rotating the secret.

**Flagged rather than done:** this can prevent a deployment from booting. That needs an owner's decision and a rotation plan, not an audit edit.

**Effort:** one hour, plus secret rotation.

---

### 3. Decide whether launch is paper-only or live

Not a code change — a decision that gates everything else.

```
VYOMQUANT_MODE = paper          (both ECS task definitions)
VYOMQUANT_ENABLE_LIVE_TRADING   (not set)
```

`safety_config.py` blocks `operation == "live_order"` in paper mode, and `main.py` arms live trading only for `env_mode == "live"`. **Live trading is off as deployed.** `FEATURE_TRADING_ENABLED=true` does not override it, and the pairing reads as though it does.

If launch is paper-only, remove "live trading" from the product copy. If the plan is to flip the mode on launch day, that flip must come *after* a testnet exercise — it would otherwise be the first execution of a path that until this audit carried a cross-tenant credential leak, stop orders that raised `TypeError`, crashing precision normalization, no venue-side idempotency, and a lock that degraded to a per-process dict.

**Effort:** a decision, plus the testnet run already listed as item 5.

---

## Verification — currently NOT VERIFIED

Each of these is blocked by missing infrastructure, not by missing work. None of them can be marked passed by reasoning.

### 4. Confirm the production database schema

**The highest-value hour available before launch.**

`backend_app/migrations/020_declare_pre_existing_tables.sql` records that `execution_records`, `idempotency_keys`, `orders`, `positions` and `fills` are declared only in Alembic and by no SQL migration. `alembic/versions/merge_heads_20260820.py` records that **no workflow, Dockerfile or script runs `alembic upgrade`** — the live schema is applied by hand.

So whether the `execution_records` **primary key on `execution_id`** exists in production cannot be determined from this repository. That key is the database-level guarantee behind order idempotency. If it is absent, every protection in that layer rests on application logic alone, and two workers racing past the application check would both insert.

```sql
-- Run this against production before launch
SELECT conname, contype, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'execution_records'::regclass;

SELECT tablename, rowsecurity, relforcerowsecurity
FROM pg_tables t JOIN pg_class c ON c.relname = t.tablename
WHERE tablename IN ('execution_records','orders','positions','fills','idempotency_keys','strategies','profiles');
```

Also worth settling in the same session: whether the inert `app.current_tenant_id` RLS policies (BUG-018) are actually present, since a partially-applied migration is silent.

### 5. Exercise the live order path on a testnet

The money path has not been exercised against a real venue in this audit. Minimum viable verification: one Binance testnet account, one strategy, one order through `BotRunner` → gateway → venue, with the fill reconciled back to `execution_records`, the portfolio and the trade history.

This also exercises three fixes that have never met a real venue: the `clientOrderId` forwarding, the min-notional enforcement, and the stop-order call shape — the last of which raised `TypeError` on every stop order before this audit, meaning **no stop loss has ever reached an exchange from this system.**

### 6. Exchange reconciliation

Internal order vs venue order, internal position vs venue position, internal balance vs venue balance — including a deliberately induced mismatch, to confirm detection rather than only agreement.

### 7. Billing against provider sandboxes

Checkout, renewal, cancellation, failed payment, and **duplicate webhook delivery**. Webhook idempotency is the one that silently double-charges or double-entitles.

### 8. Tenant isolation against live RLS

With the full six-user matrix the brief specifies. Priority targets are the `routers/library.py` paths, because that router uses the **service-role** client which bypasses RLS entirely — the Python ownership checks are its only control. Those checks were reviewed and found present, but reviewed is not tested.

### 9. ML train → persist → reload → infer

`VYOMQUANT_MODE=safe` blocks `ml_inference`, so the entire ML feature is unverified here. ML strategy blocks are a headline product capability. Verify in an environment where inference runs, including model reload after a worker restart.

---

# SHOULD IMPROVE SOON

Real problems. None of them is a reason to hold a launch.

### Wire the risk engine into the execution gateway — BUG-014

**The most important architectural item in this document.**

`backend/execution_guard.py` describes itself as *"the FINAL CONTROL LAYER that validates EVERY trade before execution"* and implements around ten validations. It is called from two cancel endpoints and **never from the path that places an order**. An AST search of `execute_with_idempotency` and `_execute_trade_internal` for `ExecutionGuard`, `RiskEngine`, `validate_trade` or `validate_trade_request` returns nothing.

Risk is enforced by `BotRunner`, the only production caller. That is why this is not a launch blocker: there is no live bypass. It is also why it should not stay this way — "this order passed risk" is a property of one caller rather than of the gateway, and `execute_trade` hardcodes `source="bot_runner"`, so any new caller inherits the authorisation without the check.

The audit added a notional ceiling (`notional ≤ equity × max_leverage`, failing closed) and sealed the one unrisked path. That bounds the catastrophic case; it is not a risk engine and does not claim to be.

**Target state:** the risk verdict becomes a required input to the live path, so "no risk evaluation" is impossible by construction rather than by convention. Deliberately not attempted under launch pressure — it touches every construction site and the wrong version of it would be worse than the current state.

### Route `BotRunner` paper mode to the Paper_Simulator

A paper bot started through `routers/strategies.py` now has its orders **refused** at the gateway. That is the environment guard working as specified, but the consequence is that paper trading via `BotRunner` produces no executions. The `Paper_Session` path works correctly and is structurally separate.

Either route the intent to the `Paper_Simulator` as the design intends, or have the UI offer only the `Paper_Session` path. The current state is a feature that appears available and silently does nothing.

### State the environment at all 71 `ExecutionEngine` construction sites

65 of 71 do not state their execution environment, so for those the entire paper/live separation is `exchange_executor is None` — a null dependency, not a control. The sentinel machinery to distinguish "unstated" from "explicitly None" already exists. Stating the environment everywhere lets the default become a refusing sentinel, which turns the guard from advisory into mandatory.

### Scope the Decimal context locally — BUG-024

`core/decimal_utils.py` sets a process-global `prec=18` at import; `core/risk_engine.py` sets `prec=28`. Whichever imports last wins for every computation not inside a `localcontext()`. Financial precision should not depend on import order. `paper_accounting` is immune because every function opens its own context — adopt that pattern.

### Finish the fee-currency work started in this audit

`record_fee` now distinguishes base / quote / third-asset denominations and reports an unconvertible fee as absent rather than as 1:1. Two functions in the same file still contradict that:

- `calculate_net_pnl` does `sum(fees)` on raw amounts across assets with no conversion.
- `get_fee_summary` adds BNB, USDT and BTC amounts into one scalar and divides by a USD-denominated volume to produce `avg_fee_rate_pct`.

Both should either convert or decline, matching `record_fee`. Separately, `_parse_symbol` silently falls back to `return sym_clean, "USDT"` for any symbol it cannot parse, which makes every fee on an unrecognised symbol look quote-denominated.

### Fix the remaining financial correctness defects

- **Zero average entry price → 100% phantom profit** (BUG-023). `calculate_new_avg_entry` returns `Decimal('0')` on zero size and `position_engine` accepts a fill with no price. `paper_accounting._weighted_average_entry` raises in the same situation — mirror it.
- **Two incompatible definitions of trade PnL in one engine.** `close_position` nets entry + exit fees; `handle_partial_fill` nets only the exit fee. The same closed trade reports a different PnL depending on which path ran, and `get_statistics()` aggregates across both.
- **A hardcoded absolute fill tolerance.** `remaining_size <= Decimal("0.0001")` is asset-independent; 0.0001 BTC is roughly $10.
- **`random.uniform` slippage** makes backtests non-reproducible run to run. Seed it or make slippage deterministic.
- **Win rate counts gross PnL**, so a trade that made $3 gross and paid $8 in fees is reported as a win.

### Resolve the exchange registry contradictions

Three registries disagree about which venues are supported. `bitget` is tradable per the factory but uncertified, so preflight refuses it while the directory advertises it. `gateio` and `bitfinex` are certified but the factory rejects them. `okx` is marked "Level 5 production certified" while the repository's own comment records that its markets do not load on the pinned CCXT.

Collapse to one registry. A venue is tradable, or it is not.

### Add CCXT options to the order-placing client — BUG-021

The client that *verifies* credentials sets `adjustForTimeDifference: True`; the client that *places orders* sets no options at all. Binance rejects orders whose timestamp falls outside `recvWindow`. Also stop replacing the `options` dict wholesale on the sandbox branch — merge it, or the next concern added will silently erase the first.

### Enforce MFA on sensitive mutations — BUG-019

`require_aal2` is implemented, correct and applied to zero endpoints. A stolen session token is currently sufficient to replace exchange API credentials and change billing. Apply it to exchange key mutation, billing mutation, password change and admin routes.

### Restrict `/metrics` — BUG-020

Unauthenticated Prometheus exposition including per-tenant counters. Network-layer restriction is sufficient and is the least invasive option.

### Tighten the `copilot.py` ownership checks

`DELETE /sessions/{session_id}` makes its ownership check conditional (`if sdata and sdata.data`) and then deletes filtered by id alone. Safe today only because both statements use the same RLS-scoped client. Make the refusal unconditional, as the sibling `GET .../messages` handler already does.

Separately, three handlers pass the user **dict** to `create_request_supabase_async`, whose signature expects an access-token string. This looks like a genuine plumbing bug that would also mean the RLS layer those handlers rely on is never in effect. Confirm by running it.

### Delete or correct `place_order_with_idempotency` — BUG-025

Its docstring claims *"This is the ONLY allowed method for order placement"*, and it cannot succeed — it never routes through `ExecutionEngine`, so no validation token is set and the bypass guard raises. Two blocks in one file contradict each other and the wrong one is the one a new engineer would follow. No callers.

### Fix the protected-logic containment matcher — BUG-026

The scan matched the three-character token `'vie'` inside an ordinary word. Add a minimum token length or word-boundary matching. Flagged rather than fixed because loosening a security control deserves its own review.

### Resolve or remove the inert RLS policies — BUG-018

Nine tables carry policies keyed on `current_setting('app.current_tenant_id')`, and nothing in the application sets it. Either set the GUC per request — a `tenant_db_context` module appears to exist in the working tree for this — or drop the policies, so the security model is not misread as having a layer it does not have. Also remove the swallowing `try/except` around each statement, which makes partial application silent.

### Remove the audit scaffolding

`audit_p0_proof.py` and `audit_recheck.py` are temporary verification harnesses. Delete before merge.

---

# FUTURE SCALE / OPTIMIZATION

Not defects. Work that becomes necessary as volume grows.

### Make rate limiting distributed

`RateLimiter(requests_per_second=5.0)` is hardcoded and per-process, so the effective rate is 5 × pod count and a venue ban becomes more likely as you scale out. Move the budget to Redis, and take the per-endpoint weights from CCXT's own metadata rather than a single constant.

### Bound the executor and connection pools

`_executors` and `_exchange_pool` are unbounded process-global dicts holding decrypted API secrets. `release_exchange_executor` was added by this audit; nothing calls it yet. Wire it into bot shutdown and add a TTL, so a worker that has served many accounts does not accumulate credentials indefinitely.

### Per-exchange adapters where venues genuinely differ

Seven venues share one generic CCXT passthrough with one normalization layer. That is the right starting point. The places it will not hold: `fetch_positions` and `fetch_trading_fees` (currently unimplemented for all venues), per-venue order-type support, and symbol formats the current suffix-guessing chain cannot parse — EUR/TRY/JPY/BRL quotes, four-letter non-listed quotes, and swap symbols such as `BTC/USDT:USDT`.

### Implement leverage, or remove it from the UI

Leverage is configured per tier, validated at deploy time, and reported in the risk API as `"current": 1.0`. Nothing multiplies a size by it and nothing computes realized account leverage. Either implement it or stop offering it.

### Replace fabricated metric sentinels

`profit_factor = 9999.0` stands in for infinity, and Sharpe is forced to `0.0` below 20 trades — so "no edge" and "insufficient sample" are indistinguishable, in a field the marketplace ranks on. Make both nullable and render the distinction.

### Give the daily PnL report a producer

`get_daily_pnl_report` returns hardcoded `'0.0'` placeholders in a real-looking API shape. Either implement it or remove the endpoint.

### Make the test suite parallel

78 minutes serially for 12,509 tests. `pytest-xdist` is not installed. At this size, suite duration is the main brake on iteration speed. Audit for shared-state safety first — `tests/conftest.py` already documents several session-scoped singletons that leak between tests.

### Annualize Sortino and Calmar by timeframe too

The Sharpe fix in this audit left `backtest_runtime.py`'s `sortino_ratio` and `annual_return` still using the hardcoded 252, with the same error for the same reason. They are sourced from vectorbt rather than computed in the engine, so they were out of scope, but the defect is identical.

### Reconcile the two Sharpe implementations

`pnl_engine.calculate_sharpe_ratio` uses population standard deviation and no annualization; the backtester uses sample standard deviation and annualizes; `routers/analytics.py` computes `AVG(pnl)/STDDEV(pnl)` in SQL over per-trade dollar PnL. Three different numbers surface under one field name. Pick one definition.

### Add a step-size and min-notional check to the paper path

The audit added venue-minimum enforcement to the live order path. The paper path captures the same metadata and should reject the same orders, so that a strategy which paper-trades successfully cannot fail on its first live order for a reason paper never surfaced.

---

## Priority summary

| Tier | Items | Blocks launch |
|---|---|---|
| Code blockers | 2 | **Yes** |
| Decisions | 1 | **Yes** |
| Verification blockers | 6 | **Yes** — currently NOT VERIFIED |
| Should improve soon | 16 | No |
| Future scale | 10 | No |

The nine launch blockers are the whole of the launch decision. Everything below them is ordinary engineering debt in a codebase that, in its better modules, is built to a notably high standard.

**One note on sequencing.** Of the nine, only two are code changes and one is a decision. The remaining six are verification items — they need an environment, not engineering. That is the realistic shape of the delay: a day of code, and two to four days of standing up a testnet and a Supabase staging environment so the money path can be exercised before customers reach it.

**And one on how the two worst findings were found.** The cross-tenant credential leak and the Redis lock degradation were both found by tracing a specific question to the line that answers it, and then *running* it — not by reading the module they lived in. The leak was found while mapping which code can place an order; the lock degradation while reading a task definition. Neither would have surfaced from a test run, because both failure modes are confident wrong answers rather than errors. Whatever process follows this audit, it should keep that property: ask what the system would do, then make it do it.

---
---

# ADDENDUM — remediation pass, 6 October 2026

Items below are **not** launch blockers. The blockers are in
`FINAL_LAUNCH_READINESS_REPORT.md`; the new defects are BUG-028 to BUG-038 in
`FINAL_PRE_LAUNCH_BUGS.md`. These are the structural follow-ups that fall out of having
actually run the system against real PostgreSQL and real Redis for the first time.

Ordered by how much risk each one retires per unit of work.

---

### 1. Wire the provisioner into deployment — BUG-028's real lesson

`scripts/provision_database.py` now provisions a complete schema from an empty database,
exit 0. Nothing calls it. That is the same gap the audit found — no Dockerfile, no
`startup.sh`, no Procfile, no workflow and no script ever ran a migration — and building a
working provisioner does not close it, it just makes closing it possible.

Until something runs it on deploy, every environment's schema is whatever was applied by
hand, and the seventeen defects found this pass were only findable because a from-scratch
run was attempted. A schema nobody can rebuild is a schema nobody can verify.

Suggested shape: a one-shot ECS task or a deploy step that runs
`provision_database.py --apply` against the target, gated on the
`public.schema_migrations` tracking table it already maintains (filename, sha256,
applied_at, step), so re-runs are no-ops and drift is visible.

**Read the production precondition in BUG-028 before the first run against production.**
`implement_rls_policies` now actually executes and enables RLS on nine financial tables.

### 2. Adopt `tenant_db_context`, then force RLS one table at a time

The nine `app.current_tenant_id` policies were proven sound this pass — with the GUC set,
each tenant sees exactly its own rows; with it unset, a non-owner sees none. They are
simply never given an input. `core/tenant_db_context.py` is the mechanism, it is tested
(including 11 PostgreSQL integration tests that now pass against a real server), and
ADR-0006 records its adoption as not started.

The order matters and ADR-0006 already has it right: adopt the context on the
tenant-scoped routers and workers → confirm with `scripts/verify_rls_enforcement.py` →
then `FORCE ROW LEVEL SECURITY` one table at a time. Forcing before adoption turns reads
on `orders`, `positions` and `fills` into silently empty result sets.

Note what this buys and what it does not: the SQLAlchemy path connects as the table owner,
so until FORCE is set the policies cannot bite there regardless. The value of adoption is
that it makes FORCE *possible*, and FORCE is what gives the financial tables a
database-level backstop behind the application's `WHERE tenant_id = ...` clauses.

### 3. Normalise `tenant_id` typing — BUG-033

Five of seven foreign keys cannot be created because `tenant_id` is `varchar(36)` on
`orders`/`positions`/`fills`/`dag_tasks` and `uuid` on `execution_records`, against a
`uuid` `profiles.id`. Referential integrity on those columns does not exist today.

This is a genuine project, not a patch: the ORM reproduces both conventions consistently
(`execution_tables.py` and `dag_task.py` say `String(36)`;
`execution_record.py`, `reconciliation.py` and `fee_engine.py` say `PG_UUID`), so
normalising means retyping columns on four financial tables *and* changing the models that
currently match them. `profiles.id` is not movable — it mirrors Supabase `auth.users.id`.

Doing it properly also fixes `execution_records.task_id` (uuid) against
`dag_tasks.task_id` (varchar(36) primary key), which is the same collision internally.

### 4. Resolve the three `positions` declarations

Carried from the audit and now proven by execution rather than inspection:
`PositionModel` declares 14 columns, the provisioned table has 5, and a real query raises
`UndefinedColumn: column positions.position_id does not exist`. Three declarations compete
— alembic `d97ffff9c3bb` (the 5-column blob shape), `core/position_model.py:369`, and
`backend/state_service.py:333`.

One must win. The blast radius is `backend/position_engine.py`,
`backend/pnl_engine.py`, `backend/metrics_system.py`, `backend/fee_engine.py` and
`core/consistency_checker.py` — the last of which is reconciliation, so this is load
bearing and not cosmetic.

Guarded in the meantime by `tests/test_orm_models_match_provisioned_schema.py`, which
keeps the finding visible via `xfail(strict=True)` rather than deleting it or leaving the
suite permanently red.

### 5. Resolve the referral schema divergence properly

`referral_system_redesign.sql` and `006_reconcile_production_database.sql` declare the same
five tables with different column sets, and `006` wins on provisioning order. BUG-035 is
the immediate consequence (the payout endpoint cannot succeed) and needs handling before
launch, but the underlying divergence is the thing to fix.

The redesign's four-state balance model (pending/approved/paid/lifetime) is more
expressive than `006`'s three columns and is what the router was written against. If the
redesign is the intended destination, it needs a real forward migration from `006`'s shape
with the live balances carried over — not an `IF NOT EXISTS` that quietly loses. If it is
not, the redesign file and the router both need retiring.

### 6. Make the billing paths testable before they handle real money — BUG-038

`_validate_keys` refuses test credentials in every environment, by design, with sound
reasoning for the entitlement-granting paths. The side effect is that the entire revenue
surface is unexercisable in staging and CI, and will run for the first time against real
customer money.

Narrower rules would keep the protection and recover the coverage. For example: allow test
prefixes only when `ENV` is not `production`, and make the entitlement grant a no-op
recorded to an audit table on that branch. Or split the gate so the webhook's signature
and idempotency handling can be exercised with test keys while settlement still demands
live ones. The specific shape is a judgement call; the current state is that nothing can be
verified at all.

`tests/test_webhook_duplicate_delivery_real_redis.py` shows what becomes possible — six
real executions of the dedup control — once the gate is stepped around for one fixture.

### 7. Run the PostgreSQL and Redis lanes, and extend them

`.github/workflows/01-pr-check.yml` has a `database-tests` job with a `postgres:17-alpine`
service. It passes: 47 tests, verified this pass, including the `SELECT ... FOR UPDATE`
row-locking control that fails on SQLite and the partial unique index that refuses a second
live deployment. Two further gated files
(`test_tenant_db_context_postgres.py`, `test_json_extract_dialect_portability.py`) are not
in its selection and should be.

Add `tests/test_tenant_isolation_rls_postgres.py` to that job. It needs the full schema
rather than `upgrade d97ffff9c3bb`, which the provisioner can now supply — and the job's
own block comment explains that it stopped at the branchpoint because a from-scratch
provision was documented as untested. It is tested now.

Add a Redis lane on the `AERORA_TEST_REDIS_URL` opt-in (BUG-037). The suite's
`MockRedisClient` is a per-process dict where `SET NX` always succeeds, which is precisely
the condition that let BUG-027 survive. Any control described as a distributed lock should
have at least one test that runs against a real server.

### 8. Replace source-counting tests where the behaviour is testable

`tests/test_billing_e2e.py` verifies webhook idempotency with
`assert source.count("await redis_manager.delete(idempotency_key)") >= 2`. The pattern
appears elsewhere too. It is a reasonable last resort for something genuinely
unexecutable, and a poor substitute where execution is possible — it cannot distinguish
correct code from code that contains the right string.

Worth a sweep: find the assertions that read source text and ask, for each, whether the
behaviour could now be executed against the PostgreSQL and Redis lanes that exist.

### 9. Resolve the `SessionLocal` isolation-level conflict

Three tests assert the general `SessionLocal()` reports `serializable`. Against a real
server it reports `read committed`, which is what `core/database.py` deliberately
specifies — FINANCIAL is SERIALIZABLE, GENERAL and READ_ONLY are READ COMMITTED. CI
deselects one of the three and does not select the other file at all.

The design is probably right: nothing outside `backend/paper/paper_repository.py` retries
on PostgreSQL's `40001`, so making the shared engine SERIALIZABLE would convert a dormant
control into live `could not serialize access` errors. If so, the tests are wrong and
should be rewritten against the FINANCIAL session. Either way the conflict should be closed
rather than carried as a permanent deselect.

### 10. Add `CREATE EXTENSION` discipline and a preflight that cannot pass vacuously

BUG-029 and BUG-030 together are the instructive pair: the repository depended on pgcrypto
without declaring it, and the preflight written to catch exactly that checked
`gen_random_uuid()`, which is core PostgreSQL from version 13 and therefore passes
whether or not the extension exists.

Two habits worth adopting. Declare every extension a migration depends on, in the file that
depends on it. And when writing a precondition, verify it actually fails when its
precondition is absent — a guard that cannot fail is read as coverage and provides none.

### 11. One test is 45% of the suite's wall clock

`tests/test_marketplace_paper_schema_contract.py::test_p58_migration_set_is_idempotent`
takes **16 minutes 41 seconds** on its own, measured in isolation. The full suite takes
roughly 37 minutes, so this single test is close to half of it.

It passes, and what it checks is worthwhile — it replays the migration set and asserts
idempotency. The cost comes from the shape: a Hypothesis `@given` driving
`_strip_sql_comments_and_strings`, a character-at-a-time Python parser, across the whole
migration corpus, once per generated example.

Two practical consequences, both encountered during this pass:

- **It defeats per-test timeouts.** Running the suite with `--timeout=300` leaves it
  apparently hung: `pytest-timeout`'s thread method cannot interrupt a tight Python loop,
  so the run neither progresses nor fails. The suite only completes if no timeout is set,
  or if the timeout exceeds 17 minutes. Anyone adding a timeout to CI will hit this and is
  likely to misdiagnose it as a deadlock.
- **It makes the suite expensive to re-run**, which in turn discourages running it, which
  is how regressions get in.

Cheap fixes, in order of preference: cache the stripped form of each file across examples
(the corpus is identical every time); reduce `max_examples` for this test; or replace the
hand-written parser with `sqlparse`, which is already a dependency. Any of the three
should bring it under a minute without weakening the assertion.

### 12a. Two hygiene checks worth running before anyone trusts these documents

`staging/check_doc_citations.py` extracts every `staging/…`, `scripts/…`, `tests/…`,
`migrations/…`, `backend_app/…` and `.github/…` path these documents cite and asserts it
exists. It currently resolves **320 citations** across seven documents and exits 0 — and
it found two stale ones (a `migrations/020_…` that actually lives under
`backend_app/migrations/`). A readiness report whose "reproduce this" section names a
deleted script invites a reader to dismiss the whole thing as approximate, so this is
worth keeping green.

`staging/uncited_scripts.py` is the reverse: it lists scripts in `staging/` that no
document points a reader at. Those are one-off probes, and the value of the list is that
removing them becomes a decision rather than a guess. Four were deleted on that basis
(`check_referral_funcs.py`, superseded by `which_referral_funcs.py`;
`drop_superseded_policies.py`, a backfill for databases provisioned before the
`referral_codes` policy fix; `fix_positions_notnull.py`; `uuid_roundtrip_probe.py`).

Both are cheap enough to belong in `04-nightly-audit.yml`.

## 12. Remove the verification scaffolding when it stops earning its keep

`staging/` holds the tools this pass was built on. Several are worth keeping as runnable
evidence and are cited in the launch report: `verify_schema.py`, `rls_inventory.py`,
`exposure_check.py`, `fk_typecheck.py`, `verify_live_trading_off.py`, `bootstrap_pg.py`,
`run_pg_lane.py`, `try_alembic_head.py`. The rest — one-off probes, logs, and the
`pgdata_*` directories — are disposable and should not accumulate.

If any of the keepers are worth running regularly, they belong in `scripts/` and in CI
rather than in a directory named `staging`.

---

# Added by the post-remediation closure pass

Ordered by what a reader should do first, not by severity. Each one names the file.

## 13. Finish wiring the provisioner — the CI half is done, the deploy half is a decision

**Done.** `.github/workflows/01-pr-check.yml` gains a `provisioning-and-isolation` job:
provision an empty `postgres:17-alpine` from repository source, provision it **again** (the
BUG-041 privilege-re-grant regression), fail on drift from
`staging/provisioning_snapshot.json`, run all ten probes, run the nine PostgreSQL test
files. Every probe now resolves its database through `staging/dsn.py`, which prefers
`AERORA_TEST_DATABASE_URL` over the local `pg_dsn.txt` — verified by moving that file
aside and re-running the set, which still exited 0 on all ten.

Wired but **not yet executed**: the sequence is verified locally against a vanilla
PostgreSQL with no Supabase substrate, but on 16.2 rather than the container's 17. The
first pull request closes that gap.

**Still open, and deliberately left as a decision rather than done:**

- `03-deploy.yml` — run `provision_database.py --apply --i-understand-this-is-remote`
  against the real target before the application rolls, failing the deploy on a non-zero
  exit. This is the step that turns the checksum refusal and the three `RAISE EXCEPTION`
  backstops into deploy gates. It also means running DDL against production Supabase from
  CI, which is hard to reverse and affects live customer data, so it wants a named owner
  and a backup first. Needs a `PROVISION_DATABASE_URL` secret, which does not exist.
- `04-nightly-audit.yml` — run `staging/run_all_probes.py` against staging. All ten
  currently exit 0; the value of a nightly run is learning the day one of them stops.
- The **first** production run of `scripts/verify_schema_drift.py`. Read-only, shape only,
  never row data. This is item 2 of the GO list in `PAPER_ONLY_LAUNCH_READINESS.md` and
  the single highest-value hour remaining anywhere in this document.

## 14. Adopt `tenant_db_context` on the request path

Nine RLS policies on `orders` and `positions` compare `tenant_id::text` to
`current_setting('app.current_tenant_id', true)`. **Nothing on any request path sets it.**
`backend_app/core/tenant_db_context.py` provides the mechanism; its only importers are
tests; ADR-0006 records adoption as not started.

Both halves were measured, and that is what makes this a small change rather than a large
one:

- with the setting absent the policies match **zero rows** — fail-closed, not fail-open
  (`test_guc_policy_denies_a_non_owner_when_the_setting_is_absent`);
- with the setting installed they isolate correctly across all six test tenants
  (`test_guc_policy_isolates_correctly_once_the_setting_is_installed`).

So the predicates are right and only the input is missing. Adopting the context manager is
sufficient; no policy needs rewriting. Latent rather than live today, because those two
tables are reached as `service_role` or `postgres`, which bypass or are exempt from RLS.

## 15. Verify the `service_role` code paths — the largest remaining isolation gap

`service_role` holds `BYPASSRLS`. For any endpoint that queries through it, **the
application's own `WHERE user_id = …` is the only control**, and RLS provides nothing.

`RLS_TENANT_ISOLATION_VERIFICATION.md` establishes what the database permits. It
deliberately does not claim anything about this, and it is recorded there as
`NOT VERIFIED`. The work is: enumerate every `_get_service_client()` / service-key call
site, and for each one assert that a tenant filter is applied. `routers/library.py` alone
has more than a dozen.

Note that this pass *added* one such call site on purpose —
`routers/referral.py::_get_service_client`, for BUG-045 — so the inventory has grown by
one and the reasoning for that one is written at the call site.

## 16. Decide about `FORCE ROW LEVEL SECURITY`

The billing tables use `ENABLE`, not `FORCE`. A table's owner is exempt from its own
policies unless `FORCE` is set, and that exemption is currently relied upon:
`routers/billing.py:1598` and `:2576` read through SQLAlchemy as `postgres`, and `FORCE`
would break both. Measured, not assumed.

The tradeoff is therefore explicit rather than accidental: compromise of the owner
credential is not contained by RLS. Closing it means moving those two reads onto a
non-owner role first, then enabling `FORCE`. Worth doing; not worth doing in a hurry.

## 17. Retire the three vestigial `referral_wallets` columns, deliberately

`balance_usd`, `total_earned_usd` and `pending_usd` are retained on purpose (BUG-043):
Requirement 24.7 forbids destructive migrations, and they are the only record against
which the four-state backfill can be reconciled. They are nullable, defaulted, written by
no installed function and read by nothing in `backend_app/`.

Once real balances have been checked against the four-state figures, drop them in a change
whose entire purpose is that drop — reviewed as such, with a backup taken first.
`tests/test_referral_money_lifecycle_postgres.py::test_the_retired_006_columns_are_inert_but_retained`
asserts the current state and will need updating at the same time; that is the intended
signal, not an obstacle.

## 18. Give `library_ratings` its own favourites table, or remove the endpoints

The table is doing two jobs, and that is what made BUG-046 and BUG-047 possible: reviews
and favourites share a row shape, distinguished only by `rating IS NULL`, so one RLS
policy has to serve both and a `NOT NULL` constraint broke one of them silently.

Either split favourites onto their own table — the tenant-isolation matrix already calls
them `library_favorites`, a table that does not exist — or remove
`POST`/`DELETE /{id}/favorite` and `GET /favorites`, which no shipped UI calls. Leaving
three routable endpoints that only recently stopped returning 500 is the worst of the
three options.

## 19. Fix the order-dependence in `tests/test_task_6_1_backtest_execute.py`

Eight tests in this file fail in a full-suite run and **pass in isolation** (verified: 95
passed when run with three other files). That is state leaking between modules, and its
cost is that every full-suite result needs a human to decide which failures are real.

Until it is fixed, a full-suite failure count cannot be compared across runs without
re-running the suspects individually — which is what had to be done for this report.

## 20. Speed up or rework `test_p58_migration_set_is_idempotent`

Already item 11, re-stated because it was hit again and cost real time: the test takes
**16m41s**, defeats `--timeout` (the thread method cannot interrupt a tight Python loop),
and makes any investigation touching `test_marketplace_paper_schema_contract.py`
disproportionately expensive. Run that file's other tests by node id in the meantime.

## 21. Correct the exchange-count and ML claims before any marketing copy ships

Still open from the previous pass. The "110 exchanges" figure and the ML capability claims
have not been reconciled against what the code does. This is a truthfulness question
rather than a technical one, and it does not belong on an engineering backlog — but it
does belong on somebody's.

---

# Added by the production drift gate pass

## 22. Supply a production DSN — still the entire remaining blocker

Everything needed to classify production exists and is gated by 41 tests proving the two
verifiers are safe to point there. `.env` holds placeholders (`sqlite` URL, `"dummy_key"`),
so nothing was run. Two commands, in `staging/FINAL_PRODUCTION_VERIFICATION.md`.

The single highest-value query within that is `execution_records`' primary key on
`execution_id`. It is the database-level guarantee behind order idempotency; the
provisioning documentation records that several execution tables are declared as
*pre-existing* rather than created by the migration path, so its presence in production
cannot be inferred from this repository. Locally it exists.

## 23. Consider the native uuid comparison in the converted RLS policies

BUG-049 converted 12 columns to `uuid` and preserved their policies by casting the column
in the predicate: `(auth.uid())::text = (user_id)::text`. That is the original text
comparison, kept deliberately so a type conversion did not silently change who can see a
row.

A native `auth.uid() = user_id` would be better in two ways: uuid equality normalises
case, so a stored uppercase identifier would match its owner where the text comparison
never did; and an index on the column becomes usable where the double cast prevents it.

Both are behaviour changes — the first alters access, the second alters plans — so they
belong in a change whose purpose is that, with the 27 billing isolation tests and the
behavioural probe re-run afterwards.

## 24. Declare the remaining 26 identity foreign keys, or record why not

`staging/identity_type_matrix.py` reports 81 identity columns: 55 correct, 0 of the wrong
type, and **26 that are `uuid` with no declared foreign key**. The type defect is closed;
these are the columns where a reference is now *possible* and simply absent.

Reported as informational rather than failing, because unlike a type mismatch there is a
legitimate reading: an append-only audit row should arguably survive the deletion of the
customer it describes. But 26 is too many to leave undecided. Each needs an `ON DELETE`
chosen the way the twelve were — `CASCADE` where the row is operational, `RESTRICT` or
`SET NULL` where it must outlive the account — or an explicit note that it is deliberately
unconstrained.

## 25. Collapse the two BUG-049 implementations when the provisioner is restructured

The conversion exists twice: `alembic d4e5f6a7b8c9` for databases that exist, and
`migrations/hardening/998_bug049_identity_types.sql` for the from-empty path, because
alembic runs at STEP 0c before 9 of the 12 target tables are created (BUG-053).

`tests/test_bug049_sql_and_alembic_agree.py` holds them in step, but two implementations
of one decision is a liability that a test can only contain, not remove. The root cause is
the provisioner's ordering: any alembic revision touching a table that the SQL set creates
has this problem, and BUG-049 is merely the first to expose it.

The structural fix is a second alembic pass after the SQL set, or moving the
`execution_records` declaration into SQL so alembic can run last. Either is a change to
the provisioning architecture and wants its own review.

## 26. Stop editing source through PowerShell

Two concrete defects came from it in this pass alone, and both were silent:

- `Set-Content -Encoding utf8` writes UTF-8 **with a BOM**. Both production verifiers
  acquired one. CPython tolerates it so the scripts ran correctly, while every AST-based
  safety check in `tests/test_production_security_probe_rolls_back_postgres.py` was unable
  to parse its target — the checks that prove the probe contains no reachable `commit()`.
  They failed closed, but only because they assert on the parse. (BUG-054)
- `Get-Content -Raw | ... | Set-Content` round-trips UTF-8 through cp1252 and mangles em
  dashes and curly quotes. 62 characters in one launch document.

`staging/strip_bom.py` and `staging/fix_mojibake.py` detect and repair both, and
`staging/check_doc_citations.py` catches the broken file references that result. Worth
adding the BOM scan to `01-pr-check.yml`: it reads 681 files in under a second.
