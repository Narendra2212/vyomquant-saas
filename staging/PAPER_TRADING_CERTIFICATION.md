# PAPER TRADING CERTIFICATION

Phase 6. The question: can `PAPER-ONLY` become GO?

This document certifies what was **measured**, against a real PostgreSQL 16.2 instance, and
states plainly what was not. Its conclusion is at the bottom and it is not GO.

---

## 1. PAPER-01 — where BotRunner's paper fills diverged from the canonical ledger

### The trace, end to end

```
strategy signal
  → BotRunner                        backend_app/backend/master_executor.py
  → execute_with_idempotency          master_executor.py:~622
  → ExecutionEngine PAPER branch      core/execution_engine.py:~1154 (pre-fix)
  → self.cash / self.positions / self._marks     ← in-process only
  → (nothing)                         ← no paper_orders, no paper_fills, no paper_positions
```

versus the canonical path, which every `/api/paper/*` endpoint and `PaperTrading.jsx` read:

```
POST /api/paper/orders
  → PaperTradingService.place_order    backend_app/backend/paper/paper_trading.py
  → paper_accounting.*                 pure, sync, stateless arithmetic
  → paper_repository.*                 insert_order / insert_fill / upsert_position / bump_version
  → paper_accounts, paper_orders, paper_fills, paper_positions, paper_balance_events
```

### The divergence point

**It is a missing call, not a wrong branch.** `backend_app/backend/master_executor.py` had
**zero import edges** to `backend_app/backend/paper/`. The engine's PAPER branch mutated three
in-process attributes and wrote no table. A bot could run for a day, report fills, and leave
the paper ledger — the thing the dashboard and the API read — completely untouched.

`master_executor`'s own comment named the blocker: the *session* simulator path
(`paper_simulator.submit_intent` / `apply_fill`) requires a `paper_sessions` row with
`feed_state='HEALTHY'`, and a BotRunner bot has neither.

### The fix, and the four design decisions inside it

**Decision 1 — route through the DEFAULT paper account, not the session simulator.**
Migration 013 relaxed `session_id` to `NULLABLE` on all seven child tables for exactly this
case, and `uq_paper_order_idem_default ON (account_id, idempotency_key) WHERE session_id IS
NULL AND idempotency_key IS NOT NULL` exists to make a default-account order idempotent. The
default account is also what the six `/api/paper/*` endpoints read, so a bot fill that lands
there is a fill the customer can see. Verified applied on the live database: 13/13 paper
tables from migration 009, 7/7 children nullable from 013, both unique indexes, all
append-only and transition triggers.

**Decision 2 — an OPTIONAL `paper_ledger` keyword on `ExecutionEngine`, not a mandatory one.**
There are 71 `ExecutionEngine` construction sites. Mandating the argument would be a wider
contract change than the defect it closes — the same reasoning the codebase already applies to
`execution_environment`. An engine constructed with no ledger behaves exactly as it did
before.

> **Rejected:** writing to the ledger *alongside* the in-process simulation. That is two
> accounting systems and two answers to "what is my cash".

**Decision 3 — the engine's in-process figures become a COPY of persisted state.**
`_reconcile_from_paper_ledger()` sets `self.cash` from `available + locked`, rebuilds
`self.positions` from the open rows (`LONG`/`SHORT` → `long`/`short`), sets `self._marks` from
the stored `current_price`, and calls `_sync_equity()`. Disagreement is logged at CRITICAL and
does not raise.

> **Rejected:** leaving the in-process model independent. That is the defect restated one
> layer up.

**Decision 4 — a ledger refusal REFUSES the execution.** `BotPaperLedgerUnavailable` →
`(False, …)`. A fill reported but not recorded is the defect, not the fallback.

New module: `backend_app/backend/paper/bot_paper_ledger.py`. Modified:
`core/execution_engine.py` (the `paper_ledger` keyword, the canonical branch at the top of the
PAPER path, `_reconcile_from_paper_ledger`), `backend/master_executor.py` (the paper branch
constructs `BotPaperLedger(self.user_id)`; **the LIVE branch passes none**).

### Smoke proof

`staging/probe_paper01_seam.py` against the real database (`staging/f_P6A2.txt`):

```
[OK  ] 1. get_or_create_account writes a paper_accounts row
[OK  ] 2. place_order persists order+fill+position
[OK  ] 3. BotPaperLedger.record_fill returns the stored figures
[OK  ] 4. ExecutionEngine PAPER branch persists a fill
[OK  ] 5. engine cash/equity == stored cash/equity
[OK  ] 6. cash + position market value == equity (from the database)
[OK  ] 7. a repeated execution_id adds no fill and moves no money
VERDICT=ALL_PASS of 7
```

**PAPER-01: FIXED.**

---

## 2. Test counts

| Suite | Result | Evidence |
|---|---|---|
| `tests/test_paper01_botrunner_fills_reach_the_ledger_postgres.py` | **26 passed** | `staging/f_P6C2.txt` |
| `tests/test_paper01_paper_live_isolation.py` | **13 passed** | — |
| both, together | **39 passed** in 121.79 s | `staging/f_P6D4.txt` |
| both, re-verified after the local database was restarted mid-phase | **39 passed** in 46.55 s | `staging/raw_PB3L.txt` |
| both, in the PostgreSQL lane beside the other gated suites | **39 passed** (of 87 passed in that lane) | `staging/raw_PL6F.txt` |

All against real PostgreSQL 16.2, not a fake. **0 skipped, 0 xfailed, 0 deleted assertions.**

In the repository's default full regression these 26 appear as **skipped**, because that lane
runs with no `DATABASE_URL` and no `AERORA_TEST_DATABASE_URL` — `tests/conftest.py` enforces
that deliberately so the ~13,000 SQLite-lane tests are not sensitive to whatever database
happens to be configured. §10 states why that matters for reading the evidence.

### Why a real database and not the existing fake

PAPER-01 is a *persistence* finding, so the database is the thing under test.
`tests/test_paper_repository.FakeSupabase` cannot enforce a partial unique index, a CHECK
constraint or a trigger, and cannot raise a `psycopg2` error carrying a `pgcode`. A new shim,
`tests/paper_pg_client.PostgresSupabase`, presents the Supabase query surface over real
`psycopg2`. Two details that matter: `select()` always issues `*`, and NUMERIC / uuid /
timestamptz come back as `str`, because `paper_accounting.to_decimal` **refuses `float`** and
accepting one would have silently changed the arithmetic under test.

### Oracle independence

Expected values are read back over a **separate** psycopg2 connection, and the SHORT valuation
convention is hand-written rather than imported from `paper_accounting.position_value` — an
oracle that calls the implementation agrees with it by construction and tests nothing.

---

## 3. The accounting invariants, and the two the oracle got wrong

The canonical engine: `paper_accounting.py`, pure and stateless, `DECIMAL_PRECISION=34`,
`ROUND_HALF_EVEN`, `WEIGHTED_AVERAGE` cost basis only, refuses `float`. SHORT value is
`size * (2*entry − price)`. Cash moves by `-(new_value − old_value) − fee`, so
`total_equity_delta == -fee` by construction.

### Held, measured per fill

| Invariant | Result |
|---|---|
| `cash + position market value == equity`, read from the database | holds |
| per fill, equity falls by **exactly** that fill's fee, valuing the position at that fill's price on both sides | holds |
| a repeated `execution_id` adds no fill and moves no money | holds |
| a repeated `idempotency_key` on the default account is refused by `uq_paper_order_idem_default` | holds |

### CORRECTION — two oracle errors that were real engine properties

Both were my errors, found by the tests failing, and both are properties of the
implementation that the oracle had wrong:

1. **Quantization is per position, not per portfolio.** The oracle summed unrounded values and
   rounded once. The engine rounds each position's value independently. The oracle was changed.

2. **`equity == capital + realized_pnl − fees` is NOT an identity.** Measured: ledger
   `100008.48` against an oracle `100008.49`. Cash is derived from quantized position
   *values*, while `realized_pnl` quantizes independently, so the two quantizations do not
   compose. **The false identity was dropped** and replaced with the invariant the
   implementation actually guarantees — the per-fill fee statement in the table above.

This is recorded rather than smoothed over because an audit that silently fixes its own oracle
to match the code has stopped measuring anything.

---

## 4. Conventions documented rather than changed

### Sell semantics: the canonical path shorts, the pre-fix bot did not

Measured (`staging/probe_paper01_sell_semantics.py`):

* a sell with **no position** is ACCEPTED and opens a **SHORT**;
* an **oversized** sell **reverses through zero** — closing the long and opening a short with
  the remainder.

The pre-fix BotRunner did neither. Its sell branch answered `"No position found for {symbol}"`
and its buy branch calls `open_position(side='long')` — it is long-only.

**Decision: `REDUCE_ONLY_SELLS = True` in `bot_paper_ledger.py`.** A bot sell larger than the
open long raises `BotPaperSellExceedsPosition`, which the engine catches separately and logs at
INFO.

> **Rejected:** adopting the canonical shorting behaviour. That would hand every paper bot —
> including spot strategies that were never evaluated short — the ability to short, as a side
> effect of a persistence fix. The convention is **documented here, not silently changed**,
> which is what the brief asks for.

### A partially filled order is a state a bot cannot reach

A market order in this engine fills **in full**. A `filled_quantity` strictly between zero and
`quantity` is a *resting limit order* state. BotRunner submits market orders, so the
"partial buy" and "partial sell" rows of the test matrix are covered as **partial-quantity
orders that fill completely**, and the resting-limit state is named here as out of reach rather
than asserted against a situation the product cannot produce.

### What the default-account path does NOT write

* **No `paper_equity_snapshots` row.** Snapshots are a session-path artefact.
* **No `paper_trades` row** unless a position reaches zero. `paper_trades` records closed
  round-trips, not fills.

Confirmed by the row counts in the smoke probe: `paper_trades 0`, `paper_equity_snapshots 0`
after three fills that left the position open. Anything reading `paper_trades` as "the bot's
activity" will see nothing until a position closes. Stated so it is a known convention rather
than a future bug report.

---

## 5. PAPER-02 — an unfundable market buy strands an open order · **REPORTED, NOT FIXED**

Measured (`staging/probe_paper01_unfundable.py`): a market buy the account cannot fund leaves

| Column | Value |
|---|---|
| `status` | `ACCEPTED` → `OPEN` |
| `filled_quantity` | `0` |
| `rejection_reason` | `NULL` |

and the call is classified `PAPER_INVARIANT_VIOLATION` (HTTP 500). Requirement 16.5 wants a
**`REJECTED`** order carrying a reason.

* **Pre-existing**, and reachable today from `POST /api/paper/orders` — it is not introduced
  by the PAPER-01 fix and not confined to the bot path.
* **Not fixed**, because the fix is a product decision about Requirement 16.5's semantics:
  whether an unfundable order should be refused before insert (no row at all) or inserted as
  `REJECTED` with a reason. Those are different contracts for every client that polls order
  status.

---

## 6. Paper/live isolation

### Structural inventory of every exchange-write boundary

`staging/probe_venue_write_inventory.py` — 10 call sites, classified **by receiver** rather
than by method name, because `create_order` on a local repository is not a venue write:

| Class | n | Sites |
|---|---|---|
| **VENUE** | 4 | `exchange_executor.py:1081 create_order`, `:1147 cancel_order` (receiver `_exchange`); `transactional_execution_manager.py:1137 create_order`, `:1050 cancel_order` (receiver `exchange`) |
| LOCAL | 5 | `reconciliation_engine.py:754/756` (`state_service`), `database_scaling.py:617` (`order_repo`), `order_state_engine.py:745` (`self`), `paper_trading.py:249` (`service`) |
| UNREACHABLE | 1 | `routers/orders.py:915` (`execution_engine.cancel_order`) |

The unreachable one is unreachable for two independent reasons, both measured:
`_build_execution_engine` (`routers/orders.py:373`) is deprecated and raises
`HTTPException(403, DIRECT_EXECUTION_BLOCKED)` **unconditionally**, and `ExecutionEngine` has
**no `cancel_order` method** at all.

### What the paper path was proven not to do

`tests/test_paper01_paper_live_isolation.py` — **13 passed**. The real production
object-construction path is exercised far enough to prove the negative, rather than asserted
over mocks:

* a real paper boot constructs **no** exchange adapter;
* it **does not touch the credential vault** — no exchange credential is decrypted;
* **no live `create_order` or `cancel_order` is reachable**;
* `_build_execution_engine` raises 403 unconditionally;
* **a live-branch negative control** proves the test could tell the difference — the LIVE
  branch does construct an executor, so the paper assertions are not vacuously true.

---

## 7. Sandbox end-to-end

```
PAPER SANDBOX E2E BLOCKED — NO SAFE SANDBOX CREDENTIAL
```

Established, not assumed:

* `exchange_keys.json` is an explicit placeholder — first line `# REDACTED: exchange_keys.json`,
  JSON body `{}`;
* the repository-root `.env` holds five keys (`SUPABASE_URL`, `SUPABASE_KEY` len 11
  `dummy_key`, `DATABASE_URL`, `REDIS_URL`, `ENV`) and **no** exchange, testnet or sandbox key;
  `backend_app/.env` — the file the application actually loads, see
  `staging/ENVIRONMENT_PRECEDENCE_AUDIT.md` ENV-03 — holds none either;
* **no CI workflow** sets `TESTNET`, `SANDBOX` or any `BINANCE_API*` variable;
* `core/config.py` has no sandbox handling at all.

**No credential was invented.** This is permanent for this engagement and is carried into the
classification below.

---

## 8. What is certified, and what is not

### Certified

| | |
|---|---|
| PAPER-01 | FIXED — BotRunner paper fills reach the canonical ledger, proven against real PostgreSQL |
| Paper accounting | `cash + position value == equity` holds; per-fill fee invariant holds; idempotency holds at both the execution and order level |
| Paper/live isolation | no adapter constructed, no credential decrypted, no venue write reachable in paper mode — with a live-branch negative control |
| Paper test suite | 39 passed, 0 skipped, 0 xfailed |

### Not certified

| | |
|---|---|
| **Sandbox E2E** | BLOCKED — no safe credential exists. No paper order has ever been placed against a venue sandbox, in any phase. |
| **`TRADING PAPER`** | **NOT CLASSIFIED.** Isolation is proven *structurally and by construction*; it has never been proven *empirically against a venue*. |
| **PRODUCTION** | **NOT CLASSIFIED.** No production DSN was available at any point. |
| **PAPER-02** | OPEN — an unfundable buy strands an `ACCEPTED`/`OPEN` order with no rejection reason, contrary to Requirement 16.5. Reachable from the public paper API. |

---

## 9. Verdict

**`PAPER-ONLY: NO-GO`.**

Three criteria of the eleven are not satisfied, and none of them can be satisfied by further
work inside this engagement:

1. **No venue-sandbox end-to-end run exists**, because no safe credential exists. The brief's
   own instruction — report the blockage, do not invent credentials — was followed, and the
   limitation must be retained explicitly rather than discharged.
2. **PAPER-02 is open** and is reachable from `POST /api/paper/orders` today.
3. **TEST-01 and SAST-01 remain red**, with dispositions and owners recorded in
   `staging/FULL_REGRESSION_FAILURE_DISPOSITION.md` but no fix, per the brief.

PAPER-01 itself is closed, and the paper ledger is now the single source of truth for a bot's
paper fills. That is a real improvement and it is not sufficient for GO.

---

## 10. ADDENDUM — what the full suite caught that the paper suite did not

The 39 paper tests above were green before the full regression ran. The full regression still
found something, and it is worth recording because of *which* check found it.

### PAPER-03 — the seam added an edge out of the paper package · **FIXED**

```
AssertionError: bot_paper_ledger.py imports first-party module(s)
['backend_app.backend.paper_trading_service'], which are not in
PERMITTED_FIRST_PARTY_IMPORTS.
```

`tests/test_paper_no_random.py` enforces "no module under `backend_app/backend/paper/` can
reach a randomised value" three ways: an AST walk for `random` / `numpy.random` /
`exchange_simulator`, a refusal of dynamic imports, and — because a static walk cannot see
through a third module — a **pinned list of the package's first-party dependencies**.

The PAPER-01 fix deliberately routes bot fills through the existing `PaperTradingService`,
which is a new edge out of the package. **The AST walk was green and the runtime refusal was
green. The dependency list is what caught it** — the one check in that file that cannot be
satisfied by inspecting the package alone, and the one a reviewer might reasonably have called
bureaucratic.

Resolved to the control's own stated standard — read the module, confirm it draws no random
value on any path the paper package reaches, and say so above the list:

| Question | Measured answer |
|---|---|
| `import random` / `from random` / `numpy.random` in `paper_trading_service`? | none; the word `random` does not occur in the file |
| its module-scope first-party imports | `paper_accounting`, `paper_repository`, `paper.errors`, `paper_order_state` — all inside the package and already enumerated |
| its function-local first-party imports | `core.dependencies`, `routers.risk` — **neither** contains `random`, `from random` or `numpy.random` |
| can either produce a price, quantity, fee or fill decision? | no — `routers.risk` returns a verdict that can *refuse* an order, and a refusal is not a figure |
| is the fill model deterministic? | **yes.** `drift = +slippage_rate` for a buy, `−slippage_rate` for a sell; `fill_price = price(reference × (1 + drift))`; `fee = qty × price × fee_rate`. Adverse slippage as a fixed fraction, no draw |
| is the idempotency key deterministic? | yes — `hashlib` over the canonical intent; its own docstring says it "carries no clock and no random draw" |
| any non-deterministic value at all? | **one**, named rather than elided: `fill_event_id = f"exec_paper_{uuid.uuid4().hex[:12]}"` |

That last one is an **event identifier**, not a price, quantity, fee or fill decision — the
same class the list already permits in `ws_channels`, which imports `uuid`. Its real
consequence was already recorded in §4 above and in `staging/MONEY_FLOW_AUDIT.md`: because the
fill-level id is random, **it cannot deduplicate anything**, which is precisely why
`bot_paper_ledger` mints a deterministic order-level `idempotency_key` and relies on
`uq_paper_order_idem_default`.

`backend_app.backend.paper_trading_service` added to `PERMITTED_FIRST_PARTY_IMPORTS` with that
note. **Verified: 64 passed.**

### The paper suite's own exposure in the full run

| | |
|---|---|
| `tests/test_paper01_paper_live_isolation.py` | **13 passed** in the full run — it needs no database |
| `tests/test_paper01_botrunner_fills_reach_the_ledger_postgres.py` | **26 skipped** in the full run |

The 26 skip because the CI unit-test lane runs with no `DATABASE_URL` and no
`AERORA_TEST_DATABASE_URL`, which `tests/conftest.py` enforces deliberately so the ~13,000
SQLite-lane tests are not sensitive to whatever database happens to be configured. They are a
**PostgreSQL-lane** suite, measured in that lane: **26 passed**, and **39 passed** together
with the isolation file (re-verified at 46.55 s after the local database was restarted
mid-phase).

**This is stated rather than glossed because it is the honest shape of the evidence:** the
PAPER-01 proof lives in a lane the default full run skips. A reader who sees "26 skipped" in
the full-run output and concludes PAPER-01 is unproven would be reading the wrong lane, and a
reader who sees only the lane figure and concludes the suite is green everywhere would be
making the opposite mistake.

---

# PHASE 7 ADDENDUM — PAPER-02 CLOSED, AND TWO NEW FINDINGS

> Everything above is the Phase 6 record, preserved as written. The current state of
> PAPER-02 is this section.

---

## PAPER-02 — an unfundable order can no longer sit in a state it could fill from · **FIXED**

### Root cause, as measured and not as assumed

`paper_trading_service.place_order`'s market branch called `_insert_and_accept` — an
`INSERT` at `CREATED` followed by an `UPDATE` to `ACCEPTED` — **before any funding decision
was made**. There was no affordability check on the market path at all: `avail_bal` was read
and then used **only** in the `limit` branch.

The ordered steps, before:

```
clean args -> _account_and_positions -> probe_idempotency_key -> open_positions
  -> kill-switch -> max-daily-loss -> max-positions -> READ avail_bal
  -> resolve exec_price -> strategy limits
  -> [limit branch only] affordability check
  -> _insert_and_accept                     <-- 2 writes, no funding decision yet
  -> idempotency short-circuit -> _execute_fill
```

The shortfall therefore surfaced *downstream*, inside `paper_accounting.apply_fill`, as
`InvariantViolation("available_balance")`. `place_order`'s handler turned that into
`500 PAPER_INVARIANT_VIOLATION` and returned — leaving the row at
`order_state='ACCEPTED'`, `legacy_status='OPEN'`, `filled_quantity=0`,
`rejection_reason=NULL`, **for ever**.

Measured before the fix (`staging/probe_paper02_state.py`, output `staging/raw_Q3C.txt`):

```
A) single unfundable market buy
   state=ACCEPTED  legacy=OPEN  reason=None
   PAPER02_STRANDED_ORDERS=1
   PAPER02_REASON_RECORDED=False

B) two processes, cash=100, each buying 80
   FILLED=1  REJECTED=0  STRANDED=1
   (one run also produced FILL_ROWS=2 with PAPER_CONCURRENCY_CONFLICT)
```

### Where the gate goes, and why not one step earlier or later

**Between the INSERT and the ACCEPT.**

* Not *before* the insert: Requirement 16.6 wants the rejection **persisted** as an order
  carrying its reason, and a row that was never written carries nothing.
* Not *after* the accept: the order would already have been in a state it could fill from,
  which is the exact invariant the brief states — *"an order that cannot be funded must
  never remain in a state that can subsequently execute."*

`CREATED → REJECTED` is one of Requirement 16.2's nine transitions and is already seeded in
`paper_order_allowed_transitions` (migration `009_paper_trading.sql`), enforced by
`paper_order_transition_guard()` / `trg_paper_order_transition_guard`. So:

**No new order model. No new state. No new migration. No second state machine.**

The threshold is `accounting.required_funds(intent, reference_price, config)` =
`money(notional × (1 + fee_rate + slippage_rate))`, which is exactly Requirement 16.6's
formula and already existed. The reason is `REJECTION_INSUFFICIENT_FUNDS =
"INSUFFICIENT_FUNDS"`, already in `paper_simulator.REJECTION_REASONS`. The refusal to the
caller is `PaperInsufficientFunds` → `400 PAPER_INSUFFICIENT_FUNDS`, already in the error
catalogue.

### No in-memory lock was added, and no atomic reservation either

The brief allowed *"if an atomic reservation mechanism is needed, use the existing
accounting architecture rather than adding an in-memory lock."* Measurement showed none was
needed:

* `paper_accounting.apply_fill` asserts **every** invariant *before* its first statement.
* `repo.bump_version` carries the account `version` as a predicate, so a second writer whose
  read is stale gets `PaperConcurrencyConflict` instead of a write.

Two racing workers can therefore never both move the balance. **What was missing was never
the money — it was the losing order's state.** Adding a lock to the market path would also
have contradicted Requirement 16.6's "SHALL lock no funds" and `design.md`'s market path.

### The write order was NOT reordered — and the first instinct was wrong

The obvious-looking repair is to move `bump_version` ahead of `insert_fill`, so money moves
before the fill row exists. The evidence refused it. The fill row is a **documented
idempotency anchor**: `paper_simulator` records that *"money moved before the fill row
exists could be applied twice"*, and the ordering is pinned by
`tests/test_paper_order_lifecycle_writes.py::test_a_conflicting_retry_resumes_the_fill_row_rather_than_writing_a_second_one`
and by `tests/property/test_paper_confluence.py`. Reordering would have **deleted a
recovery mechanism** to fix a reporting defect.

### The position write WAS moved — to match the canonical path

`paper_simulator.apply_fill`'s canonical order is
`fill → money → ledger → position` ("write 1".."write 4"). The default-account path in
`paper_trading_service` wrote `fill → position → money → ledger`.

That divergence is what the race actually produced: on a `bump_version` conflict the
**position** row was already written, leaving a phantom holding with no money movement and
no ledger entry — breaking the equity identity. Measured
(`staging/probe_paper02_phantom.py`, which forces a stale version through a wrapped
`lock_account_for_update`):

```
BEFORE   fills=1  positions=1  balance_events=0        <- phantom holding
AFTER    fills=1  positions=0  balance_events=0        stale_version_reads=6
```

The order was **aligned with the existing canonical path, not invented**.

### The eleven changes to `backend_app/backend/paper_trading_service.py`

1. `_insert_and_accept` → `_insert_fund_and_accept` (it had exactly one caller), with the
   funds gate between the insert and the accept.
2. New `_reject_unfundable_order()` — persists `REJECTED` + `rejection_reason` in one
   statement and **returns** the error for the caller to raise.
3. `place_order` wraps `_execute_fill`, converting a late `InvariantViolation` into a
   persisted `ACCEPTED → REJECTED` rejection — the safety net for any shortfall the gate
   did not anticipate.
4. New `_fill_event_id(order_id)` → `f"exec_paper_{order_id}"`, replacing
   `uuid.uuid4().hex[:12]`. Deterministic, so `uq_paper_fill_event (order_id,
   fill_event_id)` can actually deduplicate. Safe because every order on this path fills
   exactly once. `paper_simulator.market_fill_event_id` was deliberately *not* reused: it
   spells the prefix `market-`, which would be wrong for the resting-limit fills this path
   also mints.
5. `_execute_fill` split into a retry wrapper (`FILL_ATTEMPTS=3`,
   `FILL_RETRY_DELAYS=(0.05, 0.10)`, mirroring `paper_simulator`) plus `_attempt_fill`.
6. New `_assert_account_unmoved()` — re-reads the version immediately before the first
   write.
7. New `_fill_row_for_attempt()` — from attempt 2 it **adopts** the existing fill row.
   Settled (a `FILL` balance event carries it) ⇒ no-op, per Requirement 18.13. Unsettled ⇒
   resume. This is the recovery path the fill-first ordering exists for.
8. `upsert_position` moved after `bump_version` and `insert_balance_event`.
9. `_conflict()` gained `phase=` / `partial_write=`; retry exhaustion raises with
   `phase='FILL'`.
10. New `_has_unsettled_fill()` — the PAPER-04 footprint query.
11. `PaperInsufficientFunds` details quantize **both** `required` and `available` to the
    money scale (previously only one, so the two figures in one message were at different
    scales).

### Deliberately NOT changed

* **Only `available_balance` invariants become rejections.** `total_equity`,
  `position_size` and `position_side` keep the `500 PAPER_INVARIANT_VIOLATION`. Labelling
  them `INSUFFICIENT_FUNDS` would put a false reason into a closed vocabulary.
  `locked_balance` is unreachable on this path (market orders release nothing).
* **Sells are not gated on notional.** A closing sell *releases* cash; gating it on notional
  would refuse an always-affordable position-reducing order. A short-opening sell is still
  caught by the `InvariantViolation` safety net.
* **The limit branch's `estimated_cost` was left alone.** See PAPER-05.

### Evidence from database state, not HTTP responses

After the fix (`staging/raw_Q9I.txt`):

```
A) single unfundable market buy
   state=REJECTED  legacy=REJECTED  reason='INSUFFICIENT_FUNDS'
   PAPER02_STRANDED_ORDERS=0
   account untouched; 0 fills, 0 positions, 0 balance events

B) two processes, cash=100, each buying 80
   RACE_VERDICT=CORRECT
```

Race stability over ten independent races (`staging/probe_paper02_race_stability.py 10`):

```
RUNS=10  CORRECT=10  INCORRECT=0
REQUIRED_INVARIANTS=HELD EVERY RUN
PAPER04_RESIDUE  unsettled_fill_rows -> 0:10

every run: filled=1  rejected=1  stranded=0  avail=19.88
           positions=1  settled_fills=1  events=1
```

Exactly one accepted, exactly one rejected. None of the four forbidden outcomes — both
accepted, negative cash, double reservation, silent overwrite — occurred in any run.

### The sixteen required tests

`tests/test_paper02_unfunded_order_state_postgres.py` — **16 passed in 57s**, all against
real PostgreSQL 16.2, none against `FakeSupabase`.

| # | Case |
|---|---|
| 1 | insufficient cash |
| 2 | insufficient cash after fees |
| 3 | exact affordable amount (positive control) |
| 4 | one-cent / minor-unit boundary |
| 5 | oversized market buy |
| 6 | rejected order persistence |
| 7 | rejection-reason persistence |
| 8 | a rejected order cannot later fill |
| 9 | retry after rejection |
| 10 | duplicate execution ID |
| 11 | duplicate idempotency key |
| 12 | account balance unchanged after rejection |
| 13 | concurrent competing buys (cash=100, A buys 80, B buys 80) |
| 14 | two independent PostgreSQL connections |
| 15 | no multi-statement transaction to roll back — see below |
| 16 | no phantom reservation remains after rejection |

**Case 15 is named honestly.** There is no multi-statement transaction over PostgREST, so
there is nothing to "roll back". What is asserted is that a refusal **issues no statement at
all** against the other tables — verified from an independent psycopg2 connection. The
distinction is kept explicit rather than borrowing a word that would imply a transaction
boundary this architecture does not have.

The oracle has an independent psycopg2 connection and a `force_state()` helper for raw
`UPDATE`s, so case 8 can put a row into `REJECTED` by a route the service does not control
and then ask the service to fill it.

### Two of my own assertions were wrong, and the engine was right

Recorded because the corrections are properties of the system, not of the test:

1. **`required_funds` is a gate allowance, not the fill cost.** I asserted the post-fill
   balance as `capital − required_funds` and expected `49.92`; the ledger held `49.93`. The
   real cost is position value plus fee, each quantized **independently**:
   `money(50 × 1.0005) = money(50.025) = 50.02` — `ROUND_HALF_EVEN` resolves the exact half
   down because 2 is even — plus `money(0.050025) = 0.05`. The engine was right; my oracle
   conflated the allowance with the cost.
2. **A duplicate fill raises `repo.PaperDuplicateFill`, not `PaperConcurrencyConflict`.**
   `uq_paper_fill_event` has its own dedicated exception, whose message is
   *"already recorded on order"*. I had expected the concurrency exception.

### No regressions from the write-order change

| Scope | Result |
|---|---|
| `tests/test_paper02_unfunded_order_state_postgres.py` | **16 passed** (57 s) |
| `tests/test_paper01_botrunner_fills_reach_the_ledger_postgres.py` | **26 passed** (57 s) |
| paper01 isolation + paper02 together | **29 passed** (104 s) |
| 12 SQLite-lane paper suites | **347 passed** (573 s) |

---

## PAPER-04 (new, P3) — an unsettled fill row can survive a conflict, bounded and visible

When `_attempt_fill` writes the fill row and then loses the `bump_version` race, the fill row
exists with no money movement and no ledger entry. The retry wrapper **adopts** that row on
the next attempt (that is what the fill-first ordering is for), and across ten races the
residue was `0` every time.

What remains is the case where all three attempts are exhausted. Then the 409 carries
`phase='FILL'` and `partial_write=True`, and `_has_unsettled_fill()` is the query that finds
the footprint. So the residue is **bounded** (one row per exhausted order), **detectable**
(one query), **non-financial** (no balance moved, no ledger entry, no position after the
write-order alignment) and **self-healing on retry** (the same `fill_event_id` is adopted,
not duplicated).

**Reported rather than fixed** because removing it entirely means either reordering the
writes — which deletes the documented idempotency anchor, see above — or adding a
compensating delete, which is a new write on a failure path and needs its own idempotency
story. Measured residue: `unsettled_fill_rows -> 0` in 10 of 10 races.

---

## PAPER-05 (new, P3) — the limit branch's `estimated_cost` omits slippage

The market path now gates on `accounting.required_funds(...)` =
`money(notional × (1 + fee_rate + slippage_rate))`, which is Requirement 16.6's formula.

The **limit** branch computes its own `estimated_cost` with the **fee only** — no slippage.
It therefore diverges from `required_funds` by `notional × slippage_rate`.

**Not changed, and the reason is coupling.** `estimated_cost` is simultaneously (a) the gate
threshold, (b) the amount locked on the account, and (c) the amount released when the order
is cancelled or fills. Raising it to `required_funds` changes all three together, across the
resting-order path, the cancellation path and the fill path — and a release that does not
match its lock is a worse defect than a gate that is marginally generous. The correct repair
is one coherent change to the resting-order lifecycle with its own tests, not a one-line
edit inside a rejection fix.

**Direction of the error, stated:** the limit gate is slightly *more permissive* than the
market gate for the same notional. It is not a path to negative cash, because
`apply_fill`'s invariants still hold at fill time; it is a path to an order being accepted
and locked that the slippage-inclusive threshold would have refused.

---

## What is certified, and what is not

**PAPER-02 is closed**, proven from PostgreSQL state across 16 targeted tests and 10
independent two-process races.

**`TRADING PAPER` remains NOT CLASSIFIED.** Nothing in this phase changed the sandbox
limitation recorded above. There is no safe venue credential, so no paper order has ever
been observed at an exchange sandbox. The isolation evidence in this document is
**isolation by construction** — import-graph, flag and state-machine evidence — **not
venue-observed sandbox end-to-end**. That distinction is the reason the classification stays
open, and it is not a formality: construction evidence cannot detect a venue that silently
accepts an order the simulator believed it had kept local.

---

# PHASE 8 — 2026-10-09. CERTIFICATION STATUS REVIEW

**`TRADING_PAPER` remains NOT CLASSIFIED.** Phase 8 did not change that, and this block
records why in terms of what was and was not measured.

## What Phase 8 changed that touches paper trading

Nothing in the paper simulator itself. Two changes touch the shared execution funnel that
paper orders pass through, and both were deliberately scoped so the paper path is
unaffected:

* **TRADE-01's capacity reservation** applies to **live-bound orders only**. The branch is
  taken on `_is_declared_simulator`, which is computed from the engine's own declared
  environment and the presence of an exchange executor. A declared `PAPER` or `BACKTEST`
  engine keeps the original `claim_execution`, because there is no real account whose
  capacity could be oversubscribed and the paper path has its own ledger.
* **KILL-01's fail-closed fix** applies to the global kill switch, which the execution
  funnel consults before the paper/live branch. So a paper order is now also refused when
  the kill-switch backend cannot be read. That is a behaviour change for paper, and it is
  the correct direction: a process that cannot establish whether trading is halted should
  not be simulating fills either, because paper fills drive the same strategy state and the
  same operator dashboards.

The per-user kill switch consumed by `paper_trading_service` is separate process-local
state and was not touched.

## Why paper trading is still NOT CLASSIFIED

The blocker is unchanged from earlier phases and is environmental, not a defect:

> **No safe venue credential was available in any phase of this audit.** No exchange
> sandbox end-to-end test was run, and none is inferred.

What can be said is that the paper path's isolation from live venues is **isolation by
construction** — read off the code, the environment guards and the independent refusals on
the live branch, and asserted by
`tests/test_simulated_orders_reach_the_simulator.py` and
`tests/test_execution_environment_guard.py`. What cannot be said is that a venue was
observed to receive nothing, because no venue was contacted.

Those are different claims and this document keeps them apart. "Isolation by construction"
is a strong claim about the code. It is not a certification.

## PAPER-02 is fixed, and that is explicitly not sufficient

PAPER-02 being closed does not make a paper-only launch approvable, and it is not treated
as doing so. As of the end of Phase 8 there are nine open findings, including:

| Finding | Severity | Why it bears on a paper-only launch |
|---|---|---|
| KILL-02 | P1 | The emergency stop can be reset by anyone who can call it, and the audit trail expires after 7 days. A paper-only platform still needs a trustworthy halt. |
| KILL-03 | P1 | No operator-facing path engages the global kill switch at all — `/api/admin/kill-all` stops the in-process fleet and leaves the distributed switch untouched. |
| MONEY-06 | P1 | A real customer obligation (Razorpay referral commission) exists only as a log line. That is live money, independent of whether trading is paper. |
| FEE-01, MONEY-07, KILL-04, ML-01, TRADE-11, and the retained items from earlier phases | P2 | — |

`PAPER_ONLY_LAUNCH` is therefore **NO-GO**. The two P1 kill-switch findings are on the
control that a paper-only launch would rely on most heavily if something went wrong, and
MONEY-06 is a money obligation that paper trading does not insulate anyone from.

## What would change the classification

1. A safe venue sandbox credential, and a real end-to-end test that places an order into
   the sandbox and observes it there. Until then `TRADING_PAPER` stays NOT CLASSIFIED
   regardless of how strong the by-construction argument becomes.
2. KILL-02 and KILL-03 closed, so that a halt is authorised, durable and reachable by an
   operator.
3. MONEY-06 given a durable ledger record rather than a log line.


# PHASE 9 — 2026-10-10. TRADING PAPER STILL NOT CLASSIFIED

Phase 9 strengthened the operational-safety controls that stand between a paper deployment and
a live venue, but it did **not** change the paper-trading classification, and the reason is the
same one every prior phase recorded: **no end-to-end paper run against a venue sandbox was
performed in any phase, because no safe sandbox credential was available.** None was invented.

What Phase 9 did that touches the paper surface:

* The kill-switch reset is now authorised by signature and durably audited (KILL-02), the
  authoritative switch is engaged by an operator path and re-read at the venue boundary
  (KILL-03/04), and a missing/zero price fails closed rather than vanishing from measured
  exposure (TRADE-11). These make the *live* boundary safer by construction, which indirectly
  protects a paper deployment from a mis-routed order, but they are not venue-observed evidence.
* TRADE-12 (the venue-id/orders-FK collision) and TRADE-07 (stale EXECUTING recovery) remove a
  class of stuck in-flight rows. Both were verified against **local PostgreSQL**, not a venue.

Paper/live isolation remains **isolation by construction** — import-graph, feature-flag and
state-machine evidence — which cannot detect a venue that silently accepts an order the
simulator believed it had kept local. That is precisely why `TRADING PAPER` stays **NOT
CLASSIFIED** rather than being argued up to a pass on the strength of the Phase 9 safety work.

**TRADING PAPER — NOT CLASSIFIED. PAPER-ONLY LAUNCH — NO-GO.** Unchanged.


# PHASE 10 — 2026-10-10. TRADING PAPER STILL NOT CLASSIFIED

Phase 10 closed CONN-01 and two financial-integrity defects, added quota/tenant/fee coverage,
and repaired the two retained regression failures — but it did NOT change the paper-trading
classification, and the reasons have, if anything, sharpened:

* **No end-to-end paper run against a venue sandbox was performed in any phase**, including
  this one, because no safe sandbox credential was available. None was invented.
* **RECON-01 (new, OPEN)**: `ExchangeReconciliationService` is never started, so venue
  order/fill reconciliation is not operational. A paper (or live) deployment therefore has no
  running control that compares internal order/fill records against a venue's actual orders —
  the escalation scheduler runs, but nothing produces the venue mismatches for it. This is a
  further, concrete reason paper trading cannot be classified from local evidence: the
  reconciliation that would catch a divergence between the simulator's belief and a venue's
  reality is not even wired.
* **Referral settlement** (not paper-trading, but money-adjacent) remains incomplete and
  fail-safe: a payout stays pending; nothing marks it paid.

Paper/live isolation remains **isolation by construction** (import-graph, feature-flag,
state-machine evidence), which cannot detect a venue that silently accepts an order the
simulator believed it kept local — and with RECON-01 open, nothing downstream would catch it
either.

**TRADING PAPER — NOT CLASSIFIED. PAPER-ONLY LAUNCH — NO-GO. TRADING LIVE — OFF / NOT
CERTIFIED. PRODUCTION — NOT CLASSIFIED.** Unchanged.
