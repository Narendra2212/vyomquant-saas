# PHASE 9 — REMEDIATION DESIGN DECISIONS

Dated: 2026-10-09. Run identifier: `P9`.

Every decision below is recorded with the measurement that produced it and the
alternatives that were rejected. This file is append-only with respect to Phase 8;
nothing in `staging/` from earlier phases is rewritten.

Substrate for all measurements: local PostgreSQL 16.2 via `pgserver`,
`postgresql://postgres:@127.0.0.1:50924/postgres` (the port changed from Phase 8's
58620 because `pgserver` allocates a new port on every start). Local evidence only.
**No production DSN, no venue credential, live trading OFF throughout.**

---

## 0. A note on tests that pin the defects

Four Phase 8 tests encode a *current defective behaviour* as a passing assertion, each
with a failure message instructing the reader to correct the audit documentation when the
behaviour changes. They are deliberate tripwires, not contracts to preserve:

| Test | Pins | Its own instruction |
|---|---|---|
| `test_trade02...py::test_enforce_kill_switch_works_but_guards_nothing` | `enforce_kill_switch()` has zero call sites | "That is an improvement, but TRADING_SAFETY_AUDIT.md records it as dead code and must be corrected." |
| `test_trade02...py::test_reset_is_audited_but_takes_no_authorization` | `deactivate()`'s exact 3-parameter signature | "If a future change adds real authorization, this test fails and the documentation gets corrected." |
| `test_trade01...py::test_a_market_order_with_no_price_is_documented_not_silently_zero` | a priceless row contributes 0 exposure | "...TRADING_SAFETY_AUDIT.md's note about the market-order blind spot must be updated" |
| `026_fee_records_row_level_security.sql` (prose + `COMMENT ON COLUMN`) | the `UUID(int=0)` fallback | "Reported as FEE-01; not fixed here, because changing the fallback ... belongs with whoever owns fee accounting" |

Updating these four is **classified as obsolete-test retirement**, not as weakening. Each
is replaced by an assertion of the *new, stronger* contract, and the audit documents they
name are corrected in the same change. No assertion is deleted without a replacement that
is strictly harder to satisfy.

Distinct from that, these Phase 8 contracts are **preserved unchanged** because they encode
correct invariants:

- `test_trade02` — `activate()` must not contain `cancel`/`close_position`/`flatten`/`liquidate`.
- `test_trade01:468` — the capacity SUM counts `EXECUTING` only, never `PENDING`.
- `test_money02` — exactly one `process_referral_commission` call node, in `stripe_webhook`;
  each handler converts in exactly one literal currency.
- `test_money04` — `CHECK (currency = 'USD')` is not widened.
- `test_referral_money_lifecycle:190` — the retired 006 wallet columns stay.
- `test_paper01_paper_live_isolation:167-171` — exactly four venue boundaries.

---

## 1. KILL-03 — authoritative global halt

### Measured
- `enforce_kill_switch()` is defined at `core/global_safety.py:526-540` and has **zero real
  call sites across 401 `backend_app` `.py` files** — not even a commented-out one.
- `POST /api/admin/kill-all` (`routers/admin.py:185-217`) calls `fleet.shutdown_all()`
  (`fleet_manager.py:311-325`), which iterates the **in-process** `_active_fleet` dict.
  It never references `GlobalKillSwitch`, `get_global_kill_switch`, or the Redis key.
- Therefore the halt is per-pod. A second pod, a restarted worker, or a newly scheduled bot
  immediately resumes trading, because nothing durable says "stopped".
- `POST /api/admin/reinitialize` (`admin.py:219-233`) returns `{"status": "ACTIVE"}` and
  **changes no state at all**.
- Operator identity is recorded only by `_log_admin_audit_event` (`admin.py:34-63`):
  a `logger.info` plus a best-effort `RPUSH` to an unbounded Redis LIST
  `audit:admin_actions`, inside a `try/except` that only warns. **No database row.**
- `worker_base.py` never consults any kill switch. `master_executor` learns of a halt only
  implicitly, once per order, via `execute_with_idempotency`'s inline read.

### Decision
1. `/api/admin/kill-all` additionally **activates the authoritative switch**:
   `await get_global_kill_switch().activate(reason, triggered_by=<operator identity>)`.
   This is what makes the halt distributed and durable; `fleet.shutdown_all()` is retained
   because stopping the local fleet promptly is still desirable, but it is no longer the
   only effect.
2. A **real `enforce_kill_switch()` call is placed at the canonical venue boundary**,
   `exchange_executor.place_order`, immediately before the irreversible `create_order`.
   One change closes two findings: it gives KILL-03 its call site and it fixes KILL-04 by
   re-reading live state instead of asserting a check name.
3. Operator identity, reason and outcome are written to a **new durable table** (§3).

### Rejected
- **Rejected: calling `enforce_kill_switch()` only in `execute_with_idempotency`.** The
  engine already reads `is_active()` there (line 770). Adding a second read at the same
  point closes nothing: the uncovered window is 770 → 1081, which spans the idempotency
  claim, the advisory-lock capacity transaction, several DB commits and a rate-limiter
  `await`. The read has to be at the boundary to shrink that window to nothing.
- **Rejected: making `fleet.shutdown_all()` broadcast over Redis pub/sub.** That builds a
  second halt mechanism beside the one that already exists and is already read on the order
  path. The switch is the mechanism; the endpoint simply never used it.
- **Rejected: carving out an exemption so a risk-reducing "flatten" can still be placed.**
  `test_trade02::test_activation_refuses_new_orders_and_does_not_flatten` pins the
  documented emergency policy: activation blocks **new orders**, and resting orders and open
  positions are deliberately untouched. A flatten is a new order, so it is blocked, and
  `cancel_order` is a different method and is **not** blocked. That is the existing policy,
  it is coherent, and changing it is a product decision — so it is documented loudly rather
  than quietly carved out.

---

## 2. KILL-04 — a real state check at the boundary

### Measured
- `execution_engine.py:1270` passes the **literal string** `"kill_switch"` inside the
  `checks=(...)` tuple handed to `authorize_live_order`.
- `core/live_execution_gate.py:227-306` (`require_live_authorization`) verifies an
  HMAC-SHA256 over `sorted(checks)` bound to this order and venue. It is a *signed*
  assertion that a check named `kill_switch` happened — but it **never calls `is_active()`**
  and imports nothing from `global_safety`.
- The only genuine read is `execution_engine.py:770`.

### Decision
`place_order` calls `enforce_kill_switch()` after the authorisation check and immediately
before `create_order`. The signed authorisation is kept — it answers a different question
("did the gateway establish every precondition for *this* order?") — and the live read is
added beside it, answering "is the platform halted *right now*?".

### Rejected
- **Rejected: making `require_live_authorization` itself read the switch.** It is a pure,
  synchronous signature verifier with no I/O and no async. Giving it a Redis dependency
  would make the gate's own failure modes depend on Redis availability, and
  `tests/test_trade04...` constructs it directly in several places. The boundary function
  `place_order` is already `async` and already does I/O, so the read belongs there.
- **Rejected: removing `"kill_switch"` from `REQUIRED_CHECKS`.** The name documents that the
  gateway performed an upstream check, which is still true and still worth signing. The
  defect was that *nothing re-evaluated it later*, not that the name was wrong.

---

## 3. KILL-02 — authorized reset, and the retention question

### Measured
- `deactivate()` (`global_safety.py:394`) takes exactly `{self, reason, triggered_by}`.
  `triggered_by` is free text that nothing verifies.
- The `604800` is at `global_safety.py:368`, an `EXPIRE` on the Redis LIST
  `system:kill_switch_history`, applied **only inside `activate()`**. So the 7-day clock is
  anchored to the last activation, and a `deactivate()` audit entry inherits whatever
  remains of it — it can vanish seconds after being written.
- `_redis_set`, `_redis_lpush`, `_redis_expire`, `_redis_lrange` all swallow every
  exception (`global_safety.py:185-224`), so `activate()` can engage the switch and record
  **nothing**.
- **Retention policy: none authoritative exists.** `.kiro/` contains only `specs/` (there is
  no steering directory). Every retention statement found is *relative*:
  `requirements.md:186` "at least the retention period applied to existing Audit_Log
  records"; `design.md:759` "retention matches existing records". The only absolute
  statements are `requirements.md:387` — Settlement_Records retained **"permanently"** — and
  the `ON DELETE RESTRICT` foreign keys commented "Financial and audit records that must
  SURVIVE customer deletion". The only numeric precedent in code is the generic Redis
  `EVENTS` tier, 604800s.

### Decision
1. The **durable DB audit record has no expiry** — retention is permanent. This needs no
   invented duration, and it is the only choice consistent with the two absolute statements
   the repository does make about financial and audit rows. It is stated as "permanent",
   not as "compliant with policy X", because no policy X exists.
2. The Redis LIST keeps its existing 604800 unchanged and is explicitly **demoted to a
   non-authoritative operational view**. Its TTL is no longer the retention story, so the
   absence of a documented duration no longer matters.
3. `deactivate()` **requires authorization evidence** and fails closed when it cannot be
   verified.
4. Activation and reset are **serialised against each other** so a concurrent
   activate/deactivate cannot interleave into "cleared" while a fresh halt is being applied.
5. A reset **refuses while any independent halt remains active**, so clearing the global
   switch cannot silently re-enable trading that something else is still blocking.

### Rejected
- **Rejected: inventing a retention duration (e.g. 90 days, 7 years) and calling it
  compliant.** The brief forbids it and there is nothing in the repository to derive it
  from. Permanent retention is the honest answer and is strictly safer.
- **Rejected: adding the `EXPIRE` to `deactivate()` as well.** That would make the symptom
  symmetrical while leaving a safety audit trail on a store that can be unavailable and that
  expires. The fix is to stop treating the Redis list as the record.
- **Rejected: reusing `risk_settings_audit`.** Measured: it is keyed `user_id TEXT NOT NULL`
  with RLS granting read-own to `authenticated`. A platform-wide operator action has no
  owning customer, and writing a halt event into a customer-readable per-user table is both
  a modelling error and an information leak.
- **Rejected: reusing the Redis LIST `audit:admin_actions`.** Unbounded, best-effort, and
  lost on a Redis failure — the same class of weakness as the finding itself.

---

## 4. MONEY-06 — durable referral obligations

### Measured
- `razorpay_webhook` has **zero** `process_referral_commission` call nodes. Confirmed by AST,
  not substring: the four textual mentions in Razorpay-named code are a docstring at
  `billing.py:357`, a comment at `:596`, and the MONEY-04 comment block at `:2563-2589`.
- **Correction to the Phase 8 wording (1):** the `referral_commissions` INSERT *does* exist —
  in PL/pgSQL, inside the installed `process_referral_commission`
  (`024_remediate_referral_double_commission.sql:217-232`). Only the Razorpay path lacks it.
- **Correction to the Phase 8 wording (2):** the obligation is not purely lost. Razorpay
  writes a durable `billing_invoices` row (`billing.py:2527-2538`) *before* refusing, and
  attribution lives durably in `referral_relationships` (`auth.py:211-218`). What is absent
  is a row that **states the obligation and its amount**.
- **The scope is wider than Razorpay.** Stripe silently drops a commission in two further
  cases: `_provider_major_amount` returning `None` (`:2230-2237`, log only), and the RPC
  raising anything (`:2227-2229`, bare `except` that logs and continues).
- `uq_referral_commission_payment` (`024:94-96`) is a **partial unique index** on
  `payment_id` — the only race-safe per-payment idempotency in the system. The Razorpay path
  writes no row, so it has **no database-level payment-event idempotency at all**.
  `billing_invoices` has no uniqueness on `provider_payment_id` either, so a redelivery
  outside the 60-second Redis window duplicates the invoice row too.
- **`PostgREST cannot participate in a local transaction.`** Every `.execute()` is a separate
  HTTP request that PostgREST commits before responding. In `stripe_webhook` the three
  writes — tier, invoice, commission — are three independent transactions, each separately
  `try/except`-wrapped. That is precisely how a commission ends up owed and absent while the
  entitlement is granted.
- Intended contract, from the only authoritative source
  (`REFERRAL_SYSTEM_REDESIGN_REPORT.md`): record at capture as `status='pending'` into
  `pending_balance_usd` — an **accrued payable**, not a disbursable balance. Only
  `approved_balance_usd` is payable. The approval step that bridges them is documented and
  **does not exist in code**; nothing anywhere moves `pending` → `approved`.

### Decision
1. A **new table** `referral_obligations` records any obligation that cannot be credited to
   the single-currency ledger — explicit `amount` + `currency`, the provider and its payment
   id, the resolved referrer, and a reason. Every test wall in this area is scoped to
   `referral_commissions` / `referral_wallets` / `referral_payouts` and to the one RPC name,
   so a new table satisfies the requirement without touching any of them.
2. **Database-enforced idempotency** on `(provider, provider_payment_id)` as a real unique
   constraint, consumed by `ON CONFLICT DO NOTHING` — the same shape as `024`, for the same
   measured reason: a `SELECT EXISTS` check is not race-safe.
3. Written by **one `.rpc()` call into one PL/pgSQL function**, because that is the only way
   to get atomicity across the obligation row and any balance movement given the PostgREST
   constraint above.
4. **No FX, no invented rate.** A non-USD obligation is recorded in its own currency with
   status `unresolved`, which is what makes it an investigable payable rather than a wrong
   credit.
5. The Razorpay `logger.error` block is **kept verbatim** — the literals `"MONEY-04"` and
   `"referral commission SKIPPED for Razorpay"` and all four named facts — and the obligation
   write is added *beside* it. The log line is now the operational signal; the row is the
   accounting record.
6. The **two Stripe silent-drop paths** also record an obligation, so "the amount could not
   be read" and "the RPC failed" stop being log-only outcomes.

### Rejected
- **Rejected: calling `process_referral_commission` from `razorpay_webhook` with a converted
  amount.** Requires choosing an FX policy — which rate, as of when, who absorbs the drift
  between accrual and payout. `core/fx_service.py` exists but is float-based with baseline
  fallback rates; `billing.py:2605-2611` already records the decision not to reach for it
  here. Converting is a product decision about money owed to a customer.
- **Rejected: widening `CHECK (currency = 'USD')` to `IN ('USD','INR')`.** Permitting INR
  into a ledger whose columns are named `*_usd` and whose function computes a flat 20% of
  whatever it is handed is exactly how MONEY-04 was reported in the first place. Also
  explicitly pinned by `test_money04:230` and `:287`.
- **Rejected: adding a `currency` column to `referral_commissions` and crediting there.** The
  wallet aggregates `pending_balance_usd` across rows; a mixed-currency wallet has no
  defined sum. `027`'s own column comment records this: widening "requires deciding how a
  mixed-currency balance is denominated and summed".
- **Rejected: composing the obligation write from two PostgREST calls.** Measured to be
  impossible to make atomic. One of the two can commit while the other fails, which
  reproduces the defect in a new place.
- **Not attempted: implementing the missing `pending` → `approved` settlement step.** It is a
  real gap and is reported, but it needs a product decision (who approves, on what evidence,
  with what reversal window) and is outside a defect remediation.

---

## 5. MONEY-07 — currency read from the provider, not assumed

### Measured
- `_provider_major_amount` (`billing.py:584-632`) arithmetic is already **exact** — integer
  minor units into `marketplace/money.py:236-271` `to_major`, `Decimal.scaleb` + `quantize`
  at precision 28. It returns `None` (never `0`) for a float, bool, unreadable string,
  out-of-range magnitude, or unsupported currency. The amount handling is not the defect.
- `money.py:93`: `MINOR_UNIT_EXPONENT = {"USD": 2, "INR": 2}` — **only two currencies**,
  case-sensitive, raising `UnsupportedCurrency` otherwise.
- Three call sites, every one with a **string literal** currency: `:2131` `"USD"`,
  `:2304-2306` `"USD"`, `:2523` `"INR"`.
- `payment.get("currency")` is read **once in the whole file**, at `:693` inside
  `_razorpay_settlement_metadata` — a different function, which early-returns for anything
  that is not a marketplace item. `session.get("currency")` likewise only at `:673`.
- The **refund** branches of both providers already pass the payload currency through
  (`:2420` Stripe, `:2645` Razorpay). It is specifically the two *payment capture* paths and
  the renewal path that hardcode the denomination.
- `billing_invoices` writes a hardcoded `"currency"` and splits the amount across
  `amount_usd` / `amount_inr` by **which handler ran**. Its only declared DDL
  (`006:796-809`) has `currency VARCHAR(10) DEFAULT 'USD'`, **no CHECK**, and **no uniqueness
  on `provider_payment_id`**; the five columns the code actually writes have no declared DDL
  anywhere.

### Decision
**Validate the provider's own currency field against the single denomination each handler
accepts, rather than replacing the literal.** Before each conversion, read the currency from
the correct provider field and refuse — recording an obligation where money is owed — when it
is missing, malformed, unsupported, or inconsistent with that handler's denomination.

This satisfies every stated requirement: the currency is read from the correct provider
field; missing/malformed/unsupported/inconsistent data is rejected; a payload amount is no
longer treated as INR *merely because the code path expects INR*; arithmetic stays exact
integer-minor-unit; and nothing is silently converted.

### Rejected
- **Rejected: `_provider_major_amount(payment.get("amount"), payment.get("currency"))`.**
  This is the obvious fix and it is wrong twice. First, it would make the handler silently
  accept any currency the payload declares, and `money.py` only knows USD and INR — a
  `"EUR"` payment would return `None` and be indistinguishable from a malformed amount.
  Second, `test_money02::test_each_webhook_converts_amounts_in_its_own_currency` asserts
  `by_scope["razorpay_webhook"] == {"INR"}` from the **AST literal**, so a variable makes it
  `{None}`. That test encodes a *correct* invariant — each handler is single-currency — and
  the right response is to keep the invariant and add the validation, not to break it.
- **Rejected: moving the conversion into a shared helper.** `test_money02`'s `_scope_of`
  reports the **innermost** enclosing function, so a helper would also fail the same
  assertion, and it would obscure which currency each provider path accepts.
- **Rejected: widening `money.MINOR_UNIT_EXPONENT`.** Adding currencies with no pricing, no
  invoice column and no settlement path would be inventing product scope, and
  `test_money02::test_an_unsupported_currency_is_refused` deliberately pins `GBP`/`EUR`/`XYZ`
  as refused.

---

## 6. TRADE-07 — stale execution recovery

### Measured
- **Correction to the Phase 8 wording:** an age-based janitor *does* exist and *is* running.
  `OrderWatchdog` is started unconditionally at `main.py:421-434`
  (`check_interval=30s`, `stale_threshold=60s`, `alert=300s`, `max_retries=3`) and drives
  rows to COMPLETED / FAILED / EXECUTING / UNKNOWN, with a deliberate UNKNOWN fail-safe that
  refuses to assume FILLED.
- The real mechanism is narrower: `_find_stale_orders` (`order_watchdog.py:387-416`) and
  `_find_open_orders` (`:324-347`) both filter **`order_id IS NOT NULL`**, and `order_id` is
  written **only at the COMPLETED transition** (`execution_engine.py:962-968`, after
  `create_order` has already returned). A row stuck by a crash therefore has
  `order_id IS NULL` and is invisible to the watchdog that exists to rescue it.
- On a stuck row, `order_id`, `submitted_at`, `exchange_id`, `last_exchange_sync` and
  `result` are **all NULL**. Nothing distinguishes "crashed before `create_order`" from
  "crashed after `create_order` but before the response".
- **`submitted_at` is declared in the ORM and in the migration and is never written by any
  code in `backend_app`.** It is a dead column, and it is exactly the evidence seam needed.
- `place_order` performs **no database write at all**.
- The recovery handle does exist: `execution_id` is deterministic and is already forwarded to
  the venue as `clientOrderId` (`exchange_executor.py:~1059`). **Nothing anywhere looks an
  order up by `clientOrderId`**; `get_order_status` can only `fetch_order` by venue order id.
- `check_idempotent_execution` returns `skip_already_running` for EXECUTING with **no age
  test**, so a stuck row makes that exact (tenant, strategy, symbol, bucket, side, qty,
  price) permanently un-retryable.
- `order_watchdog.py:437` and `:482` assign `order.exchange_status_message`, which is **not a
  column** (the model has `exchange_status`) — those assignments silently do not persist.

### Decision
1. **Create the evidence.** Write `submitted_at` (and `exchange_id`) immediately before the
   venue call. This is what makes the four cases distinguishable at all.
2. **Recover on the four cases, explicitly:**
   - `submitted_at IS NULL` + stale → confirmed failure *before* submission. Safe to fail the
     row; no venue order can exist.
   - `submitted_at` set, `order_id IS NULL` → **ambiguous**. The venue may hold the order.
     Reconcile by `clientOrderId`; **never resubmit**.
   - Venue outcome not establishable → leave a safe unresolved state (UNKNOWN) and alert. Do
     not guess, in either direction.
   - `order_id` set → already in the existing watchdog's population; unchanged.
3. Capacity release and the status transition are **transactionally consistent** — the stale
   cutoff already stops a stuck row holding capacity; the transition now also stops it
   blocking retries.

### Rejected
- **Rejected: resubmitting a stale execution.** The whole point of the ambiguous case. An
  order the venue accepted but never acknowledged would become a second real position.
- **Rejected: marking stale rows FAILED unconditionally.** That is the mirror error: it
  declares an order dead that the venue may have filled, and the next signal would then open
  a duplicate.
- **Rejected: dropping the `order_id IS NOT NULL` filter from the existing watchdog.** It
  would then call `get_order_status(exchange_order_id=None)` for every stuck row. The filter
  is correct *for a reconciler keyed on venue order id*; the gap is that no path handles rows
  that never got one.
- **Rejected: starting `ExchangeReconciliationService`.** It is the only `fetch_order`-based
  reconciler over `execution_records` and it is never started. Wiring an unstarted service
  into production startup is a much larger change than this finding, and it inserts "ghost
  order" rows with `price=None` — which would feed TRADE-11. Noted as a separate gap.

---

## 7. TRADE-11 — fail-closed valuation

### Measured
The blind spot is in **three** places, not the two recorded:
1. `execution_engine.py:~455` (`_capacity_bounds_for`):
   `notional = abs(Decimal(str(size))) * abs(Decimal(str(price or 0)))`.
2. `execution_engine.py:1180` (the per-order ceiling gate): the same expression.
3. `risk_engine.py:146`: the notional cap is gated on
   `if price_decimal > 0 and self.max_position_notional_pct is not None:`, so a zero or
   missing price **skips the cap entirely**, leaving ATR sizing unbounded.

`price or 0` means `None`, `""`, `0`, `0.0` and `Decimal("0")` all yield notional `0`, which
always fits. Only a *non-numeric, non-falsey* price produces a refusal.

In SQL, `ABS(size::numeric) * ABS(NULLIF(price,'')::numeric)`: `NULL` and `''` make the
product NULL and `SUM` skips the row; `'0'` contributes 0; non-castable text raises, which
rolls back and refuses (that part already fails closed).

On the live path a market order is expressed as `price = 0`
(`c_order_type = market if price <= Decimal("0") else limit`), so it consumes **no modelled
capacity**. `exchange_reconciliation.py:~303` is a second producer of priceless EXECUTING
rows, inserting ghost orders with `price=None` explicitly.

**No price oracle, mark-price service or ticker is consulted by any capacity or notional
check, and no risk policy document specifying pre-trade market-order valuation exists in
code.** Only MARKET and LIMIT are reachable from the strategy path.

### Decision
Fail closed at every one of the three sites and in the aggregate:
- A **missing or empty required price refuses** rather than becoming zero.
- Invalid, non-finite or negative prices are rejected.
- A **priceless row must not silently vanish from the aggregate** — the aggregate must
  refuse rather than under-report.
- Because **no market-order valuation policy exists**, a market order is **refused on the
  live capacity path** rather than valued by a guess. This is the brief's own rule: an
  estimate must follow an explicit existing risk policy, and there is none.

### Rejected
- **Rejected: valuing a market order at the last trade / ticker price.** There is no policy
  for which price, how stale is too stale, or what to do when market data is unavailable.
  Inventing one inside a capital check would be an unreviewed risk rule governing real money.
- **Rejected: treating a missing price as zero "because the venue's balance check is the
  backstop".** That is the current comment's reasoning and it is what the finding is. A
  venue-side rejection arrives after the idempotency claim has been consumed and the
  execution record says an attempt was made.
- **Rejected: a second reservation mechanism.** The brief forbids it and it would conflict
  with TRADE-01. All changes are made inside the existing
  `claim_execution_within_capacity` / `_capacity_bounds_for` pair.

---

## 8. FEE-01 — no sentinel tenant

### Measured
- `fee_engine.py:543` and `:553`: `return record.tenant_id if record else UUID(int=0)`. No
  log, no raise, no comment. Written into `fee_records.tenant_id` at `:352` and `:403`.
- `fee_records.tenant_id` is `uuid NOT NULL` with **no foreign key**, so the all-zero uuid
  inserts cleanly.
- Under `fee_records_owner_access` (`auth.uid() = tenant_id`) such a row is invisible to
  every authenticated caller — fail-closed, but silently unreadable rather than loudly wrong.
- **No production caller of the fee engine exists.** Every importer is a test or an
  `attack_*` script; `get_fee_engine()` has zero callers. Every current caller already holds
  a real tenant uuid.
- `UUID(int=0)` is **not an established convention** here: four occurrences repo-wide, of
  which two are this defect, one is a test monkeypatch and one is the detector.
- Measured on this database: `SENTINEL_TENANT_ROWS = 0`.

### Decision
Replace the fallback with **explicit validated tenant context and fail-closed behaviour**: a
fee whose owning tenant cannot be established is refused, not attributed to a tenant that
does not exist. No production call site breaks, because there is none, and every test caller
already has a real tenant.

### Rejected
- **Rejected: keeping the sentinel and adding a log line.** The row is still written against
  a non-existent tenant; a warning does not make the accounting correct.
- **Rejected: adding a foreign key on `tenant_id`.** It would convert a silent bad row into a
  constraint violation at flush, which is better, but it also changes the write behaviour of
  five other paths and `tenant_id` has deliberately never had one. The caller-side fix is
  narrower and addresses the actual question ("whose fee is this?").

---

## 9. ML-01 — reclassified, with evidence

### Measured
The count is correct **as a text count** and the inference drawn from it is not.
`MLExecutor` is `dag_engine.py:1233-1405`. The three `pd.Series` occurrences are:
- `:1252` — inside the class **docstring**, describing code that was *removed*.
- `:1280` — the **return annotation**, identical on every sibling executor because the legacy
  loose-dict engine propagates `Dict[str, pd.Series]`.
- `:1311` — the one executable `if isinstance(value, pd.Series)`.

And `execute()` **unconditionally raises `MLInferenceUnavailable`** at `:1396-1405`, whether
or not a model is found. It can never return. `model_registry` is provably always `{}` — a
test already asserts it on every engine the repo builds. So the `isinstance` filter can only
change *which refusal message* is produced.

Meanwhile the FEATURE_MATRIX contract **is** implemented and enforced where features actually
reach a model: `strategy_service.run_feature_pipeline:4849-4852` raises
`TrainingBlocked(REASON_FEATURES)` for any non-`FeatureMatrix`; `concat_matrices:579-584`
refuses a non-matrix by name; `FeatureMatrix.align_to` refuses a missing timestamp rather
than filling. Spec authority exists (`requirements.md:64`, Req 18.12/18.13,
`design.md:620/919/1972`). And `tests/test_dag_engine_port_addressing.py:103-109` already
records: *"It cannot consume a feature matrix. That is not a gap in this fixture."*

### Decision
**Reclassify as a false positive in the check, not a product defect.** The defective artefact
is `staging/verify_phase8_open_findings.py:287-308`, which is a substring count over the
class slice that includes docstrings and type annotations — the same error class its own
MONEY-07 section was rewritten to avoid. Fix the check.

### Rejected
- **Rejected: adding a `FeatureMatrix` import or `isinstance` branch to `MLExecutor`.**
  Explicitly forbidden by the brief, and it would flip the verifier to "premise gone" while
  changing **no behaviour whatsoever**, because the method raises unconditionally regardless.
  That is the definition of a nominal fix.
- **Rejected: implementing ML inference on the legacy path.** That would turn a path which has
  never produced a real prediction into one that drives trades. A feature with an owner, not
  a defect fix — and the executor's own docstring already says so.

---

## 10. TEST-06 and the protected-logic containment failure — one root cause

### Measured
Both reduce to **substring matching with short and numeric tokens**. TEST-06 has 29 protected
tokens, of which `['233', 'from']` are ≤4 characters and `['233']` is all digits; the seed
contains `"ema_zzq_secret_window": 233`. A bare `"233" in text` matches a timestamp, so the
verdict depends on the clock. Corroborated by the runs themselves: TEST-06 **failed** in
`FULLREG_P8PG` and **passed** in `FULLREG_P8PG2` with no change to the test or to the code it
examines.

### Decision
Replace clock-dependent substring matching with a **deterministic check using exact token
boundaries / structured parsing**, and prove the original security property still holds with
both **fixed-time** and **randomized-time** regression tests. One change fixes TEST-06 and the
containment failure because they share the mechanism.

### Rejected
- **Rejected: exempting short tokens, or imposing a minimum token length.** The brief forbids
  it without documented security justification, and there is none: a short protected token is
  still protected logic. Exempting it narrows the assertion.
- **Rejected: changing the seed values so `233` no longer appears.** That makes the test pass
  by removing the input that exposes the weakness, which is weakening dressed as a fix.
- **Rejected: exempting timestamp fields from the scan.** Same objection — it narrows the
  surface the check covers rather than making the check correct.

---

## 11. SQL-interpolation constraint tracing

### Measured
`tests/test_bandit_sast_gate_is_green.py::test_every_interpolation_into_sql_is_constrained_before_it_gets_there`
fails because the checker's constraint-tracing does not follow a **variable binding
introduced inside an already-applied Alembic revision**.

### Decision
Fix the checker's source analysis so it follows the binding. The production path is in fact
constrained; the analysis cannot see it.

### Rejected
- **Rejected: editing the applied Alembic revision to rename the variable.** Editing an
  already-applied migration to satisfy a linter is both unsafe and dishonest about history.
- **Rejected: adding a suppression.** It removes the check's coverage of that pattern
  everywhere, not just here.
- **If the fix cannot be made without weakening the guarantee, the failure is retained and the
  exact decision required is documented** — per the brief.

---

## 12. CI reproducibility of the PostgreSQL requirement

### Measured
`tests/test_tenant02_fee_records_rls_postgres.py`'s `_resolve_dsn()` falls back to
`staging/pg_dsn.txt`. Measured consequence in Phase 8: it ran **22 passed in both lanes**,
unlike `test_money04_...` (24 skipped in CI) and `test_trade01_...` (28 skipped in CI). So the
"CI-comparable" lane was CI-*comparable*, not CI-*identical* — a clean CI runner without that
file would behave differently.

### Decision
Make the database requirement **explicit**: PostgreSQL-required tests belong to the
PostgreSQL lane and skip only on a clearly detected absence of the required test database,
through the same mechanism the other PG-gated suites already use. The local DSN file must not
be what makes the CI lane look representative.

### Rejected
- **Rejected: deleting the fallback and leaving the suite unable to run locally.** The fallback
  exists so the suite is runnable; the defect is that it is consulted in the *CI* lane. The
  gate, not the convenience, is what changes.

---

## 13. What this phase does not attempt

Stated so the report's boundaries are explicit rather than implied:

- **Venue-sandbox end-to-end verification.** No safe sandbox credential exists in any phase.
  `TRADING PAPER` therefore stays **NOT CLASSIFIED**.
- **Production verification.** No production DSN exists in any phase. All evidence here is
  local-substrate. `PRODUCTION` stays **NOT CLASSIFIED**.
- **The referral `pending` → `approved` settlement step.** Documented, unimplemented, and a
  product decision.
- **Starting `ExchangeReconciliationService`.** Reported as a separate gap (§6).
- **The `referral_payouts` column-name mismatch** (`payment_method` written vs `payout_method`
  declared in the live shape). Measured as probable but not yet confirmed against the
  catalogue; reported, not silently changed.

---

## 14. TRADE-12 — a new finding, discovered while building TRADE-07 recovery

### Measured
Not in the Phase 9 brief. While writing the stale-execution recovery I found that
`execution_records.order_id` carries `FOREIGN KEY (order_id) REFERENCES orders(id)`
(non-deferrable), and `public.orders` holds **this platform's** order ids — but the only
value the application ever writes into that column is the **exchange's** id, from
`create_order`'s response. Measured in a rolled-back transaction
(`staging/probe_phase9_order_id_fk_blocks_completion.py`): a venue-shaped id is **rejected**
by the FK, an `orders`-table id is accepted, and `0` of the current rows carry one.

Because `update_status` commits and the success call site has no `try` around it, a real
live fill would: `create_order` succeeds (money moves) → the COMPLETED write raises 23503 →
the session's transaction aborts → the enclosing handler's own FAILED write also fails →
the row is left `EXECUTING` with `order_id IS NULL`, which is exactly the stuck state
TRADE-07 is about and which `OrderWatchdog` (filtering `order_id IS NOT NULL`) cannot see.
So TRADE-07's stuck-row population has a **guaranteed producer**, not only a crash-induced
one. `EXECUTION_RECORDS_WITH_ORDER_ID = 0` is consistent with the write never having
succeeded; live trading has never been enabled, so no such row exists yet.

### Decision
Migration `030` adds a **separate `venue_order_id` column with no foreign key** (the
exchange assigns it, so no local table can be its parent). `order_id` keeps its meaning
and its FK. `update_status` gains a `venue_order_id` parameter; the success path and the
recovery path both write the venue id there. `OrderStatusResult` gains a `venue_order_id`
field so `find_order_by_client_order_id` can return the venue's id for recovery to record.

### Rejected
- **Rejected: dropping `fk_execution_records_order_id`.** It is a real referential
  guarantee for platform orders, added deliberately by `add_foreign_keys_20260817.py`.
  Dropping a constraint to make a wrong write succeed fixes the symptom and keeps the
  modelling error.
- **Rejected: inserting an `orders` row per venue id.** Fabricates platform orders from
  exchange responses and makes `orders` mean two things.
- **Rejected: storing the venue id in `exchange_status`.** A free-text status field, not
  an identifier; nothing could index or join on it.

---

## 15. TEST-06 and the protected-logic containment failure — the deterministic check

### Measured
`listing_projection.protected_logic_tokens` keeps `str(scalar)` for every leaf ≥3 chars,
and `assert_contains_no_protected_logic` did bare substring containment. The seed carries
`"ema_zzq_secret_window": 233`, so `"233"` was a token and `"233" in text` is true of the
timestamp `1760036233`, the count `2337`, the price `233.90`. TEST-06 failed in
`FULLREG_P8PG` and passed in `FULLREG_P8PG2` with no code change — a clock-dependent
verdict.

The separate `test_protected_logic_containment.py` failure was a different collision in the
same oracle: a generated protected-logic token `vie` is a substring of the route constant
`operation.name == "node_preview"`, which the refusal body echoes. Measured in
`staging/probe_test06_trace_collision.py`.

### Decision
Two independent, minimal changes:
1. **A numeric token is matched on a numeric-value boundary**, not as a substring:
   `(?<![\d.])233(?![\d.])`. `233` still matches the value 233 (own field, list element,
   or the word in free text) but not `1760036233`/`2337`/`233.90`. String tokens keep
   substring matching, which is correct and deterministic for them.
2. **The property test's strategy-independent corpus gains the route operation names** the
   refusal bodies echo (`node_preview`, `version_compare`), read from the guard's own
   constants — the exact mechanism the file's own docstring prescribes for the earlier
   `avg_rating`/`details` collisions.

### Rejected
- **Rejected: exempting short tokens / a minimum length / changing the seed.** The brief
  forbids all three, and each narrows the assertion. `233` is still a protected token and
  is still searched for — as the number 233 rather than the characters "233".

---

## 16. The SQL-interpolation bandit gate — scope-aware constraint tracing

### Measured
`test_every_interpolation_into_sql_is_constrained_before_it_gets_there` failed because its
`_constrained_names` collected constrained and unconstrained names **module-wide** and
subtracted. An alembic migration validates `column = _validate_table_name(raw_column)`
inside each function (RULE 2, safe), but the same name is also a loop target elsewhere, so
the module-wide subtraction struck `column` out and every validated interpolation was
reported. The TRADE-07 code I added introduced one more (`tenant_clause`, a fixed literal).
Measured violation-by-violation in `staging/probe_bandit_scope*.py`.

### Decision
Make the checker's source analysis **scope-aware** and able to trace the genuinely-safe
patterns the migrations use, without editing any applied migration and without weakening
the gate:
- analyse each function body as its own name scope (a constraint is recognised where the
  name is bound, never shared across a scope boundary);
- RULE 5: a name bound to a string literal or a conditional/boolean of string literals
  (`tenant_clause = ""` / `target = "a" if c else "b"`);
- RULE 6: a name bound to a comprehension that can only yield strings from a literal
  sequence (`present = [(t,c,d) for t,c,d in _TARGETS if ...]`), including `list(X)+list(Y)`
  and module-level `_TARGETS: tuple[...] = (...)` annotated literals;
- RULE 7: an accumulator (`pending = []` then `pending.append((table, column))` where only
  constrained names are appended) is itself a literal sequence;
- tuple-unpacking loop targets (`for table, column, on_delete in present`).

Every extension can only recognise *more* genuinely-constrained names; none shares a
constraint across a scope boundary or admits a value from outside a literal set. The
use-site check is also scoped, so an f-string is checked against the names its own scope
plus the module globals can resolve.

### Rejected
- **Rejected: editing the applied Alembic revision** to rename the variable — unsafe and
  dishonest about history, and explicitly forbidden.
- **Rejected: a `# nosec` or a `--baseline`** — the AST check is invisible to `# nosec` by
  design, and a baseline turns the one injection gate into decoration.
- **Rejected: loosening the gate** (`-lll`, `|| true`). The gate is correct to block; it
  had nothing true to say, so the fix is to teach the premise-check to see the real
  constraints.

---

## 17. CI reproducibility of the PostgreSQL requirement — resolved by construction

Every new Phase 9 test module (`test_kill03_...`, `test_money06_...`,
`test_trade07_...`, `test_fee01_...`) gates on `AERORA_TEST_DATABASE_URL` naming
PostgreSQL and has **no fallback to `staging/pg_dsn.txt`**, with an explicit
`test_the_postgres_requirement_is_explicit` / `test_the_audit_table_requirement_is_explicit`
that fails if the gate is weakened or a local DSN literal appears. This is the
CI-reproducibility fix applied prospectively to all the new suites. The pre-existing
`test_tenant02_...`'s fallback is noted as an unresolved discrepancy carried from Phase 8
rather than changed here, because altering a Phase 8 suite's DSN resolution is outside the
Phase 9 findings and TENANT-02 is already closed.
