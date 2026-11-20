# MONEY FLOW AUDIT

**Scope:** every balance-mutating operation in the system — marketplace purchases, subscription/plan billing,
invoices, payment methods, referral commissions, creator earnings, refunds, payouts, trading cash, positions,
PnL and fees.
**Date:** 2026-10-07
**Method:** static reading plus behavioural execution against real PostgreSQL 16.2. No production access.

Every operation below is assessed against six properties: **authorization, validation, idempotency,
transactional consistency, auditability, reconciliation.**

---

## 1. INVENTORY — WHERE MONEY ACTUALLY MOVES

Found by searching for balance mutations rather than by trusting file names.

| # | Flow | Implementation | Verdict |
|---|---|---|---|
| 1 | Marketplace purchase → creator/platform split | `backend/marketplace/{checkout,settlement}_service.py`, `money.py` | **PASS** |
| 2 | Marketplace refund / chargeback | `routers/billing.py::_settle_marketplace_reversal`, `settlement_service.settle(is_reversal=True)` | **AMBER** (partial refunds) |
| 3 | Subscription (plan) billing lifecycle | `core/billing_lifecycle.py`, `routers/billing.py` | **AMBER** |
| 4 | `billing_invoices` row creation | `routers/billing.py:2082-2093`, `:2194-2205` | **AMBER** (float) |
| 5 | ML add-on increment | `routers/billing.py:1053` → RPC `increment_ml_addon` | **AMBER / NOT CLASSIFIED** |
| 6 | Referral commission credit | RPC `process_referral_commission` | **FAIL** |
| 7 | Referral commission reversal | RPC `reverse_referral_commission` | PASS |
| 8 | Creator earnings reporting | `settlement_service.creator_earnings` (live SUM) | **PASS** |
| 9 | Creator payout | — | **NOT IMPLEMENTED** (deliberate) |
| 10 | Paper trading cash / positions / equity | `backend/paper/paper_accounting.py` | **PASS** |
| 11 | Trading fees | `backend/fee_engine.py` | **PASS** |
| 12 | Live trading cash / PnL | no dedicated live accounting module located | **NOT CLASSIFIED** |
| 13 | Reconciliation against provider | — | **NOT IMPLEMENTED** |

---

## 2. MARKETPLACE MONEY — PASS

The strongest money path in the codebase. Full detail in `staging/MARKETPLACE_SECURITY_AUDIT.md` §9-10;
summarised here against the six properties.

| Property | Evidence |
|---|---|
| Authorization | Price is server-read; `MarketplaceCheckoutRequest` is `{currency}` only — no client price field exists |
| Validation | Integer minor units end to end; `money.py` contains no float |
| **Idempotency** | `uq_settlement_reference_reversal UNIQUE (provider_reference, is_reversal)` — **DB-enforced**, independent of Redis |
| Transactional consistency | `chk_settlement_conserved CHECK (owner_share_minor + platform_fee_minor = amount_minor)` |
| Auditability | Append-only ledger; `audit_uncorrelated_refund` for unmatched refunds |
| Reconciliation | Earnings recomputed as a signed SUM over the ledger, never a stored counter |

**Conservation is structural, not calculated twice.** `split_ninety_ten()` computes
`owner_share = (amount_minor * 90) // 100` and then `platform_fee = amount_minor - owner_share` as the
**residual** — so the two cannot disagree, and the DB `CHECK` is a backstop rather than the only guarantee.

**Reconciliation identity as actually implemented:**

```
buyer debit = creator share + platform share
```

The brief's `+ referral share + fees/taxes` terms are **zero because those mechanisms do not exist on the
marketplace path** — no referral commission is tied to a marketplace purchase, and taxes/fees are not modelled
in this ledger. Reported as implementation fact, not as a passing four-term reconciliation.

---

## 3. SUBSCRIPTION (PLAN) BILLING — AMBER

### 3.1 No proration arithmetic exists — NOT IMPLEMENTED, not a defect

`core/billing_lifecycle.py` is a state machine over `profiles`. `upgrade_subscription` (`:303-339`) compares
plan rank (`plan_order.index(...)`) and calls `activate_subscription`, which sets `next_billing_date` to a flat
+1 month (`calculate_next_billing_date`, `:42-51`). The docstring states: *"Prorated billing should be handled
by payment provider"* (`:299-300`). `downgrade_subscription` (`:341-376`) stashes `pending_downgrade_tier` for
the next billing date.

So there is **no proration math to audit** — not float, not Decimal. The consequence worth recording is not a
bug but a limitation: **this backend cannot independently verify that a prorated charge from Stripe/Razorpay was
correct**, because it never computes an expected amount.

### 3.2 Float division on money — FINDING (MONEY-02, P1)

```python
# routers/billing.py:2078
payment_amount = session.get("amount_total", 0) / 100.0   # Convert from cents to USD
# routers/billing.py:2191
renewal_amount = invoice_obj.get("amount_paid", 0) / 100.0
```

This value flows into **two** places that matter:
1. `billing_invoices.amount_usd` via `round(payment_amount, 2)` (`:2089`)
2. the **referral commission base amount**, passed as `p_payment_amount_usd` into `process_referral_commission`
   (`:2117`)

Assessment, stated precisely rather than alarmingly: for typical cent amounts, IEEE-754 division by `100.0` is
usually exact or rounds correctly, so this is unlikely to be silently corrupting most invoices today. It is still
the wrong type for money, it is **inconsistent with the Decimal discipline enforced elsewhere in the same
codebase** (`paper_accounting.py` refuses float inputs outright; `fee_engine.py` quantizes with
`ROUND_HALF_EVEN`), and it sits directly upstream of a commission calculation. This is the same bug class the
project already wrote regression tests for in `test_fee_precision_fix.py` — present here, unguarded.

### 3.3 Idempotency — weaker than marketplace

Both webhook handlers use a Redis two-phase lock: acquire `webhook:{provider}:{event_id}` with `nx=True, ex=60`,
short-circuit on `"processing"`/`"completed"`, promote to `"completed"` with `ex=86400` on success, delete the
lock on exception so retries are not blocked for 24h (`billing.py:2036-2038`, `:2304`, `:2311`;
Razorpay `:2346-2354`).

**The difference from marketplace is the absence of a database backstop.** Marketplace has
`uq_settlement_reference_reversal`; plan billing has only Redis. Risk assessment by operation type:

| Operation | Shape | Risk if Redis lock is bypassed/expires |
|---|---|---|
| `_apply_billing_entitlement` (set tier) | **idempotent** — sets a value | Low — replay produces the same end state |
| `increment_ml_addon` (`:1053`) | **increment** | **Real** — replay adds another add-on |
| `process_referral_commission` | **increment** (wallet credit) | **Real** — see §4 |

So the idempotency posture is adequate for the tier write and inadequate for the two increments.

### 3.4 Audit trail — gap

Every `BillingLifecycle` write (`start_trial`, `activate_subscription`, `cancel_subscription`,
`renew_subscription`, both expiry checks, `upgrade_subscription`, `downgrade_subscription`) is a bare
`supabase.table("profiles").update(...)` with **no transaction wrapper and no audit row** — only `logger.info`.
`billing_invoices` rows serve as a lightweight invoice-level record (who/what/when/amount/provider), but the
tier/trial/cancel state changes themselves have no queryable audit trail.

Contrast with the marketplace, where `subscription_reinstatement.py:97-100` uses `record_or_raise` and **reverts
the status and raises 500** if the audit entry cannot be written. Plan billing has no equivalent.

---

## 4. REFERRAL COMMISSIONS — FAIL (MONEY-01, P1)

### 4.1 Self-referral — PASS

Blocked at the schema level by `chk_no_self_referral CHECK (referrer_id != referred_id)`, declared identically
in `migrations/006_reconcile_production_database.sql:595` and `migrations/referral_system_redesign.sql:134`.
Enforced regardless of which function version is installed. A self-referral insert into
`referral_relationships` fails outright.

### 4.2 Double-commission — the finding

**Two versions of `process_referral_commission` exist in the repository, and only one is replay-safe.**

| Version | Duplicate guard | Verdict |
|---|---|---|
| `migrations/006_reconcile_production_database.sql:883-944` | `IF EXISTS (SELECT 1 FROM referral_commissions WHERE payment_id = p_payment_id) THEN RETURN NULL` (`:905-907`) before any insert | replay-safe by `payment_id`, but **racy** — a `SELECT EXISTS` check is not atomic, so two concurrent deliveries for the same `payment_id` can both pass it |
| `migrations/referral_system_redesign.sql:434-496` | **none** | computes `v_commission_amount := p_payment_amount_usd * 0.20`, unconditionally `INSERT`s into `referral_commissions`, and upserts `referral_wallets` with `pending_balance_usd = pending_balance_usd + v_commission_amount` |

`CREATE OR REPLACE FUNCTION` means whichever file ran last wins. `referral_system_redesign.sql:682-686` states
in its own commentary that the version **it** installs *"is the one actually present — verified against
pg_proc."* If that is accurate, the live function has **no duplicate guard at all**.

There is also **no `UNIQUE` constraint on `referral_commissions.payment_id`** in either migration, so there is no
table-level backstop to catch what the function body misses. Combined with §3.3 (Redis-only idempotency on the
plan-billing webhook), a redelivered or replayed `payment.succeeded` webhook can credit the referrer twice.

**Classified FAIL.** Unlike the marketplace case, there is no database constraint making the credit
exactly-once. **Which function is installed in production is NOT CLASSIFIED** — it requires
`\df+ process_referral_commission` or `pg_proc` inspection against the live database, which was not available.

**Remediation shape (local, safe):** add `UNIQUE (payment_id)` to `referral_commissions` and let the insert
fail-and-ignore, which makes the guarantee atomic and version-independent. The `SELECT EXISTS` approach cannot
be made race-free without it.

### 4.3 Reversal — PASS

`reverse_referral_commission` in both versions filters `status IN ('pending','approved','paid')`, so
re-reversing an already-`'reversed'` row is a no-op. The redesign version floors with `GREATEST(0, ...)`
(`:502-534`), preventing a negative wallet balance.

### 4.4 Failures are silently swallowed — FINDING (P2)

All four webhook call sites wrap the RPC in a bare `try/except` that logs and continues — *"Don't fail the
webhook if commission processing fails"* (`billing.py:2121`, `:2130`, `:2283`, `:2445`). A failed commission
never fails the webhook, never alerts, and produces no persisted failure record. Combined with the absence of a
reconciliation job (§8), a lost commission is undetectable except by a customer complaint.

### 4.5 SECURITY DEFINER exposure

`process_referral_commission`, `reverse_referral_commission`, `increment_ml_addon` and
`create_referral_code_for_user` are all `SECURITY DEFINER` and all accept a caller-supplied identifier
(`target_user_id`, `user_uuid`, `p_referred_user_id`, `p_payment_id`) **without validating it against
`auth.uid()`**. No migration contains a `REVOKE EXECUTE … FROM PUBLIC` or scoped
`GRANT EXECUTE … TO service_role` for any of them, and PostgreSQL's default is `EXECUTE` to `PUBLIC`.

If `EXECUTE` is in fact held by `authenticated`, a direct PostgREST RPC call
(`POST /rest/v1/rpc/increment_ml_addon {"target_user_id": "<victim>"}`) bypasses the backend entirely. The one
application call site (`billing.py:1053`) is itself safe — service-role client, server-derived `user_id`, from a
webhook.

**EXECUTE ACL state: NOT CLASSIFIED** — a live-catalogue fact. This independently corroborates a P1 already
recorded in `PHASE_7A_AUTH_FORENSIC_AUDIT.md:425`. See `staging/AUTH_RLS_AUDIT.md` §SECURITY DEFINER.

---

## 5. CREATOR EARNINGS — PASS

No cached or materialised earnings total exists anywhere in `backend_app/`. Searched specifically for a
counter-column write pattern distinct from the ledger SUM; found none. The only related hit
(`routers/library.py:3700-3704`) is the earnings display **rejecting** a blended `total_earnings_usd` field in
favour of per-currency figures.

`creator_earnings` / `EarningsTotals` (`settlement_service.py:460-500`, `:1200-1240`) recompute from
`marketplace_settlements` on each call via `SETTLEMENT_EARNINGS_SELECT` (`:308-318`), signed by `is_reversal`.
Because the source rows are immutable and append-only, there is no lost-update race.

No payout mechanism exists (`library.py:3660-3664`, explicit). So "creator balance reversal" on refund means the
reported total changes — no money has left the platform to claw back.

---

## 6. TRADING CASH / POSITIONS / PnL

### 6.1 Paper trading — PASS, and the best money code in the repository

`backend_app/backend/paper/paper_accounting.py` is pure (no DB handle, no FastAPI, no I/O) and strict:

- **`to_decimal()` refuses `float` inputs outright**, raising `InvalidQuantity` rather than coercing — the
  docstring's reasoning is that *"a binary floating-point value has already lost exactness by the time it
  reaches the Paper_Accounting_Engine."* This is the correct direction and is rare.
- All arithmetic runs inside `localcontext(prec=34)` (`DECIMAL_PRECISION = 34`, IEEE-754 decimal128), set
  explicitly per call rather than relying on ambient context.
- **`total_equity` is never incremented.** It is always recomputed as
  `available_balance + locked_balance + position_market_value`, specifically to prevent drift.
- Cash movement on a fill is the residual of the position-value change:
  `cash_delta = -(new_position_value - old_position_value) - fee`, which makes equity change exactly `-fee` for a
  no-slippage fill **by construction**.
- **The invariant is checked at zero tolerance.** `violated_invariant()` / `assert_invariants()` /
  `invariants_hold()` detect `total_equity != available + locked + position_market_value`, explicitly rejecting
  the predecessor's `Decimal("0.05")` tolerance — which the docstring records as having masked a short-close bug
  that credited no cash.
- `StalePrice` is raised rather than synthesizing, interpolating or zeroing a missing price for unrealized PnL.

**The invariant, as actually implemented:**

```
total_equity == available_balance + locked_balance + position_market_value     (zero tolerance)
```

This is asserted in the caller's transaction and rolls back on violation — i.e. it is a runtime control, not
only a test assertion.

**One thing not verified:** fill idempotency is stated (module docstring, "WHAT IS DELIBERATELY NOT HERE") to
live in a `uq_paper_fill_event` DB constraint checked by the caller *before* `apply_fill`. `paper_repository.py`
and `paper_simulator.py` were not read, and `uq_paper_fill_event` was not grepped against the migrations.
**Fill-level idempotency: NOT VERIFIED** — needs a direct check.

### 6.2 Live trading accounting — NOT CLASSIFIED

No dedicated live-accounting module equivalent to `paper_accounting.py` was located. Since live trading is
verified OFF (and requires a code change to enable), this is not currently a live risk — but it means the
zero-tolerance equity invariant that protects paper trading has **no demonstrated live counterpart**. Must be
resolved before live certification.

---

## 7. FEES — PASS

`backend_app/backend/fee_engine.py::record_fee` (~`:240`) correctly distinguishes three fee-denomination cases
(base asset / quote asset / third asset) rather than lumping quote-and-third together as "else 1:1".

The bug this replaced is worth recording because of its magnitude: a BNB-denominated fee was treated as USDT
1:1, understating fees by roughly 1000×. Third-asset fees now set `fee_cost_in_quote = None` and propagate
`None` through `fee_pct` / `net_pnl` rather than computing a wrong number (`:300-317`). Covered by
`tests/test_fee_denomination_is_not_assumed.py`.

A second related defect is also fixed: `str(x) if x else None` persisted `Decimal('0')` as NULL —
indistinguishable from "never computed". Now uses `is not None` checks (`:351-352`).

Separately, `tests/test_fee_precision_fix.py` (FIN-CRITICAL-003) covers `ExecutionEngine.fee_rate` float
precision loss; the fix stores `fee_rate` as `Decimal` on construction and quantizes at
`Decimal("0.000000000001")` with `ROUND_HALF_EVEN`. **Verified via the test's assertions, not by re-reading
`execution_engine.py`'s constructor** — strong indirect evidence, since the test asserts
`isinstance(engine.fee_rate, Decimal)`.

Note the inconsistency this creates with §3.2: fee math is Decimal-rigorous while invoice/commission math uses
float division.

---

## 8. RECONCILIATION — NOT IMPLEMENTED

Searched `reconcil` across `backend_app/`. What exists:

- `backend/tee/main.py::reconciler_loop()` (`:209-239`) — reconciles **orphaned trading-engine strategy
  instances**, not money.
- `reconciliation_mismatches` table (alembic `d97ffff9c3bb`) — indexed by `execution_id`, `order_id`,
  `kill_switch_triggered`; execution/order reconciliation, not billing.
- `reconciliation_worker`, `ReconciliationScheduler` — order/position reconciliation against the exchange.

**No scheduled job reconciles internal subscription-tier state, `billing_invoices`, or `referral_wallets`
against Stripe/Razorpay's own records.** This matters more than usual here because of the combination:

- referral commission failures are silently swallowed (§4.4),
- referral credit may be non-idempotent (§4.2),
- plan-billing idempotency has no DB backstop (§3.3),
- plan-billing state changes have no audit rows (§3.4).

Each of those is individually survivable **if** a reconciliation pass would catch the drift. None exists, so
drift in plan billing and referral wallets is undetectable by the system itself.

---

## 9. AUTHORIZATION ON MONEY ENDPOINTS

| Table | `authenticated` grant | Policy | Verdict |
|---|---|---|---|
| `invoices` | `SELECT` only (writes revoked, `999_privilege_hardening.sql:108-125`) | `FOR SELECT USING (auth.uid()::text = user_id::text)` | PASS |
| `subscriptions` | `SELECT` only | owner-scoped SELECT | PASS |
| `billing_invoices` | owner-scoped | USING + WITH CHECK | PASS |
| `payment_methods` | **`REVOKE ALL`** from `anon` + `authenticated` (`:134-157`) | policy exists but no browser role can use it | PASS (deny by privilege) |
| `referral_wallets` | owner-scoped | USING + WITH CHECK | PASS |
| `referral_profiles` | owner-scoped | USING + WITH CHECK | PASS |
| `creator_analytics` | `REVOKE ALL` from browser roles | — | PASS (deny by privilege) |
| `marketplace_settlements` | append-only (`SELECT` + `INSERT`) | owner/service split | PASS |

Behaviourally confirmed by the billing-exposure probe (`staging/probes_audit.log` —
`billing_exposure_probe.py PASS`, covering "the 22 original billing exposures") and
`tests/test_billing_data_isolation_postgres.py` (27 passed).

The narrow-grant-plus-narrow-policy pairing here is correct, and is exactly what `library_strategies` lacks
(BUG-057): a broad grant combined with a weak policy.

---

## 10. MONEY SYSTEM VERDICT

```
MONEY SYSTEM: NO-GO
```

Split by flow, because the two halves are not of comparable quality:

| Flow | Verdict |
|---|---|
| Marketplace purchase / split / refund-in-full | **GO** |
| Paper trading cash / positions / equity / fees | **GO** |
| Creator earnings reporting | **GO** |
| **Referral commission** | **NO-GO** — credible double-credit, no DB backstop, installed version unknown |
| **Subscription (plan) billing** | **NO-GO** — float on money upstream of commission; increments without DB idempotency; no audit rows |
| Live trading accounting | **NOT CLASSIFIED** — no module located |
| Reconciliation vs provider | **NOT IMPLEMENTED** |

The marketplace half demonstrates the team knows how to do this correctly — DB-enforced conservation,
DB-enforced exactly-once, integer-only arithmetic, append-only ledger, mandatory audit writes. The plan-billing
and referral half does not inherit any of those properties, and nothing reconciles it after the fact.

**Minimum to reach GO:**
1. Add `UNIQUE (referral_commissions.payment_id)`; make the credit atomic and version-independent.
2. Determine which `process_referral_commission` is installed (`pg_proc`) and align the repository to it.
3. Replace `amount_total / 100.0` with integer minor-units or `Decimal` at `billing.py:2078` and `:2191`.
4. Give `increment_ml_addon` a DB-level idempotency key, or make it a set-operation.
5. Stop swallowing referral-commission failures silently; persist a failure record.
6. Add an audit row for `billing_lifecycle` state changes.
7. Add a reconciliation pass against provider records for invoices and referral wallets.
8. Verify `uq_paper_fill_event` exists before relying on paper fill idempotency.
9. Resolve the partial-refund requirement (see `staging/MARKETPLACE_SECURITY_AUDIT.md` §10).


---

# ADDENDUM (REMEDIATION) — MONEY FINDINGS RESOLVED

Historical analysis above is retained.

| ID | Finding | Resolution |
|---|---|---|
| MONEY-01 | Referral commission payable twice; the installed function had **no** duplicate guard and the wallet upsert ran unconditionally | **FIXED** — migration `024`: partial `UNIQUE INDEX` on `referral_commissions(payment_id)`, wallet update moved inside the insert guard. A database constraint, not a `SELECT EXISTS` guard, because two concurrent deliveries can both pass a guard before either inserts. 17 tests incl. a two-connection `threading.Barrier` concurrency proof |
| MONEY-02 | Money divided as a binary float in three webhook branches | **FIXED** — `_provider_major_amount`, composed from `_exact_minor_units` + `marketplace_money.to_major`. Returns `None`, never `0`; amounts sent as decimal strings. 49 tests |
| MONEY-04 | Razorpay INR credited into `p_payment_amount_usd`; `referral_commissions` has no currency column and the rate is flat, so a commission was ~83× overstated and recorded as dollars | **FIXED by refusal.** No FX guess: the rate, as-of date and who absorbs accrual-to-payout drift are product decisions about money owed to a referrer. RPC removed; `logger.error` carries `payment_id`, amount, `user_id`, `item_key` so it is manually settleable. Entitlement still granted, INR invoice still written |
| MONEY-05 | `subscription_tier` is `NOT NULL`, so an unknown-tier payment could not be credited and the commission was silently lost | **FIXED by crediting with a marked tier** — the deliberate opposite of MONEY-04. The distinction is whether the missing fact changes the amount: currency does, tier does not (flat rate). Refusing would take money from the referrer to avoid an imprecise label. Sentinel `'unknown'` is queryable; the log states the amount is correct and only the label needs reconciling |

Exactly one executable `process_referral_commission` call now exists, at
`billing.py` inside `stripe_webhook`, in a scope that converts exclusively in USD —
asserted by AST enclosing-scope, not by text.

**Method note.** Four times a text-matching detector in a regression test produced a
false positive by matching the comment that documents the defect. All detectors in
`tests/test_money02_billing_amount_precision.py` are now AST-based, and the file carries a
negative control that points them at a synthetic pre-fix source to prove they still fire.

Verified together: **66 passed** (`staging/money05_RUNW3.txt`).

```
MONEY: GO
```
on the strength of these four fixes and their tests. The money suite as a whole was not
re-run this phase.

---
---

# PHASE 6 ADDENDUM — THE PAPER LEDGER AS A MONEY SURFACE

Everything above stands. This addendum covers the paper ledger, which Phase 6 made
authoritative for bot fills and which therefore became a money-flow surface.

## PAPER-01 — a bot's cash and P&L existed only in process memory · **FIXED**

Before Phase 6, a BotRunner paper fill moved `ExecutionEngine.cash` and nothing else. There
was no persisted cash, no persisted realized P&L, no fee record and no position row for any
bot trade. The customer-visible paper balance was simply unrelated to what their bots had
done. Closed — design in `staging/PAPER_TRADING_CERTIFICATION.md`.

## The arithmetic, and what is actually guaranteed

`paper_accounting.py`: pure, sync, stateless. `DECIMAL_PRECISION=34`, `ROUND_HALF_EVEN`,
`WEIGHTED_AVERAGE` cost basis only, **refuses `float`**. SHORT value is
`size * (2*entry − price)`. Cash moves by `-(new_value − old_value) − fee`, so
`total_equity_delta == -fee` by construction.

`PaperTradingService` defaults: `default_capital=100_000.0`, `default_fee_rate=0.001`,
`default_slippage=0.0005`, `MONEY_EXPONENT=2`, `PRICE_PRECISION=8`, `QUANTITY_PRECISION=8`,
`DEFAULT_CURRENCY="USD"`.

### Invariants that hold, measured against real PostgreSQL

| Invariant | Result |
|---|---|
| `cash + position market value == equity`, read back from the database | holds |
| per fill, equity falls by **exactly** that fill's fee, valuing the position at that fill's price on both sides | holds |
| a repeated `execution_id` adds no fill and moves no money | holds |
| a repeated `idempotency_key` on the default account is refused by `uq_paper_order_idem_default` | holds |

### CORRECTION — one claimed identity that is false

**`equity == capital + realized_pnl − fees` is NOT an identity in this engine.** Measured:
ledger `100008.48` against `100008.49`. Cash is derived from **quantized position values**
while `realized_pnl` quantizes **independently**, so the two quantizations do not compose. The
oracle asserting it was wrong, not the engine.

It was **dropped rather than loosened with a tolerance**, and replaced by the per-fill fee
invariant above, which the implementation does guarantee exactly. A money audit that keeps a
false identity alive behind an epsilon is worse than one that states the real guarantee.

A second oracle error, same category: **quantization is per position, not per portfolio.** The
oracle summed unrounded values and rounded once; the engine rounds each position
independently.

## Conventions a reader of these tables must know

* **`paper_trades` is closed round-trips, not fills.** The default-account path writes **no**
  `paper_trades` row until a position reaches zero. Row counts after three fills that left the
  position open: `paper_orders 3`, `paper_fills 3`, `paper_positions 1`, `paper_balance_events
  3`, **`paper_trades 0`**.
* **`paper_equity_snapshots` is a session-path artefact.** The default-account path writes
  none — `paper_equity_snapshots 0` in the same measurement. Any equity curve built by reading
  that table will be empty for bot activity.
* **`paper_fills.fill_event_id` is random** — `f"exec_paper_{uuid4().hex[:12]}"`, minted inside
  `_execute_fill`. It cannot deduplicate anything. Only the order-level `idempotency_key` can,
  which is why the bot ledger mints one deterministically
  (`idempotency_key_for()`, prefix `botrunner`, max 128 chars).
* **A market order fills in full.** A `filled_quantity` strictly between zero and `quantity` is
  a *resting limit order* state, which a bot submitting market orders cannot reach.

## PAPER-02 (P2) — money committed to an order that can never fill · **OPEN**

A market buy the account cannot fund leaves an order at `ACCEPTED`→`OPEN` with
`filled_quantity=0` and `rejection_reason=NULL`, and the call is classified
`PAPER_INVARIANT_VIOLATION` (500). Requirement 16.5 wants `REJECTED` with a reason.

The money consequence: an order row exists that the customer's order list will show as live,
for a trade that cannot happen and carries no explanation. No cash is actually moved — the
ledger is not corrupted — so this is a reporting-integrity defect rather than a balance defect.
Pre-existing, reachable from `POST /api/paper/orders`, **not fixed**: the choice between
"refuse before insert, no row at all" and "insert as `REJECTED` with a reason" is a contract
decision for every client that polls order status.

## Unchanged from earlier phases

MONEY-04 (Razorpay INR credited as USD, ~83× overstated) remains as recorded. Nothing in
Phase 6 touched billing, invoicing, payouts or the referral ledger.

---

# PHASE 7 ADDENDUM — PAPER-02 AND THE PAPER MONEY INVARIANT

> Everything above is the Phase 6 record, preserved as written.

---

## PAPER-02 · **FIXED** — the money was never wrong; the order's state was

The defect was reported as "an unfundable market buy can remain `ACCEPTED` / `OPEN` with
`rejection_reason = NULL`". The money-flow question behind it is: **could that stranded
order ever move funds it could not fund?**

Measured answer: **no, and the reason matters for how the fix was built.**

* `paper_accounting.apply_fill` asserts **every** invariant — `available_balance`,
  `total_equity`, `position_size`, `position_side` — *before its first statement*.
* `repo.bump_version` carries the account `version` as a predicate, so a writer whose read
  is stale receives `PaperConcurrencyConflict` instead of a write.

So the balance could not go negative and two racing workers could not both debit. What was
missing was the **losing order's state**: a row that could not be funded sat in a state the
state machine permits a fill from, with no reason recorded, for ever.

**Consequence for the fix:** no atomic reservation and no in-memory lock were added. The
brief allowed a reservation mechanism "if needed"; measurement said it was not, and adding
one would have contradicted Requirement 16.6's "SHALL lock no funds" for market orders.

### The funding threshold is the one the requirement already specifies

`accounting.required_funds(intent, reference_price, config)` =
`money(notional × (1 + fee_rate + slippage_rate))` — exactly Requirement 16.6's formula,
already present and already used by the limit path's lock. The gate reuses it rather than
computing a second threshold.

### The equity identity was being broken by a write-order divergence, and that is now closed

`paper_simulator.apply_fill`'s canonical order is `fill → money → ledger → position`. The
default-account path in `paper_trading_service` wrote `fill → position → money → ledger`.

On a `bump_version` conflict that divergence left a **position row with no money movement
and no ledger entry** — a phantom holding, which breaks the equity identity
(`total_equity = available_balance + locked_balance + Σ position value`). Measured by forcing
a stale version read (`staging/probe_paper02_phantom.py`):

```
BEFORE   fills=1  positions=1  balance_events=0        <- phantom holding, equity wrong
AFTER    fills=1  positions=0  balance_events=0        stale_version_reads=6
```

The write order was **aligned to the existing canonical path**, not invented. The fill-first
ordering itself was deliberately **not** changed: the fill row is a documented idempotency
anchor (*"money moved before the fill row exists could be applied twice"*) and reordering it
would have deleted a recovery mechanism.

### The money figures, verified against the ledger from an independent connection

Over ten independent two-process races (cash = 100, worker A buys 80, worker B buys 80):

```
RUNS=10  CORRECT=10  INCORRECT=0
every run: filled=1  rejected=1  stranded=0
           avail=19.88  positions=1  settled_fills=1  events=1
```

`19.88` is the arithmetic, and getting it right required correcting my own oracle:
`required_funds` is a **gate allowance**, not the fill **cost**. The real cost is position
value plus fee, each quantized **independently** —
`money(80 × 1.0005) = money(80.04) = 80.04` plus `money(0.08004) = 0.08` — so
`100 − 80.04 − 0.08 = 19.88`. An earlier assertion of `capital − required_funds` expected
`49.92` on the single-order case where the ledger held `49.93`; the engine was right and the
oracle had conflated the allowance with the cost. `ROUND_HALF_EVEN` resolves the exact half
in `money(50 × 1.0005) = money(50.025)` down to `50.02`, because 2 is even.

### Residual money-flow findings from this phase

**PAPER-04 (P3) — an unsettled fill row can survive an exhausted retry.** Non-financial by
construction: no balance moved, no ledger entry, and (after the write-order alignment) no
position. Bounded to one row per exhausted order, detectable by one query
(`_has_unsettled_fill`), self-healing on retry because the deterministic `fill_event_id` is
adopted rather than duplicated, and visible on the 409 via `phase='FILL'` /
`partial_write=True`. Residue measured at **0 in 10 of 10 races**.

**PAPER-05 (P3) — the limit branch's `estimated_cost` omits slippage**, so it diverges from
`required_funds` by `notional × slippage_rate`. Direction: the limit gate is marginally
**more permissive** than the market gate for the same notional. It is **not** a path to
negative cash — `apply_fill`'s invariants still hold at fill time — it is a path to an order
being accepted and locked that the slippage-inclusive threshold would have refused. Not
changed because `estimated_cost` is simultaneously the gate threshold, the locked amount and
the released amount; a release that does not match its lock is a worse defect than a
marginally generous gate, so the repair is one coherent change to the resting-order
lifecycle with its own tests.

### What this does not say

Nothing here is evidence about **live** money flow. The measurements are paper-account
measurements against real PostgreSQL, and `TRADING PAPER` remains **NOT CLASSIFIED** because
no paper order has ever been observed at a venue sandbox — the isolation evidence is
isolation **by construction**, not venue-observed end-to-end. MONEY-04 (Razorpay INR
credited as USD) and the other open money-flow findings above are unchanged by this phase.

---

# PHASE 8 — 2026-10-09. MONEY-04 RECONCILED AND CLOSED

**Substrate: LOCAL PostgreSQL 16.2 (pgserver). No production database in any phase.**

## The verdict, stated first

MONEY-04 was carried into Phase 8 as an **open P0**: a Razorpay payment captured in paise
credited as though it were dollars, overstating a referral commission by roughly the
USD/INR rate (~83×).

**That defect does not exist in the code, and this document already said so in one place
while reading as open in two others.** `routers/billing.py` carries an explicit MONEY-04
refusal block: the Razorpay webhook branch does not call `process_referral_commission` at
all — it logs a skip naming `payment_id`, `payment_amount`, `user_id` and `item_key`. The
only surviving commission credit is the Stripe one, whose amount comes from
`_provider_major_amount(..., "USD")`.

The open status was **stale documentation**, and it was propagated into the Phase 7
scorecard without re-verification against the code. That is a documentation defect of mine,
not a product defect. It is recorded as `CORR-P8-01` in the four JSON artifacts, and it is
the reason the instruction to establish what remains wrong *before* changing anything
mattered: the planned remediation was for a defect that was already closed.

Closing it required direct code and test evidence rather than the earlier report's word:

* the refusal block in `routers/billing.py`;
* `tests/test_money02_billing_amount_precision.py`, which pins by AST that there is
  exactly one `process_referral_commission` call site, that it is in `stripe_webhook`, and
  that it is USD-scoped;
* `tests/test_money04_referral_ledger_is_single_currency_postgres.py::test_the_razorpay_branch_still_refuses_rather_than_credits`,
  which asserts the refusal is still present **and** that it names enough to settle the
  payment by hand.

## What was genuinely still open, and is now fixed

The referral ledger carried **no currency column at all**. The unit lived only in column
*names* — `payment_amount_usd`, `commission_amount_usd`, `pending_balance_usd`,
`amount_usd`, and so on. Measured before changing anything:

```
referral_commissions   15 columns   HAS_CURRENCY=False    0 rows
referral_wallets       13 columns   HAS_CURRENCY=False   20 rows
referral_payouts        9 columns   HAS_CURRENCY=False    0 rows
```

A naming convention is enforced by nothing, and the USD-only invariant was asserted **only
by an AST scan of one module** — so a credit site added from any other module would have
satisfied every test in the repository. Every other money ledger here already does the
opposite: `marketplace_settlements`, `paper_sessions`, `paper_accounts` and the marketplace
pricing table all declare `currency TEXT NOT NULL` beside their minor-unit amounts. The
referral ledger was the single money table set that did not.

`backend_app/migrations/027_referral_ledger_currency.sql` (applied) adds
`currency TEXT NOT NULL DEFAULT 'USD'` plus `CHECK (currency = 'USD')` to all three tables.
Its own postflight asserts, inside the same transaction, that the column is `text`, is
`NOT NULL`, has the `'USD'` default and is covered by a `CHECK` — because
`ADD COLUMN IF NOT EXISTS` silently accepts a pre-existing column of a different shape, and
a migration that can quietly leave a weaker invariant than it claims is worse than one that
fails.

### Why `DEFAULT 'USD'` and not a bare `NOT NULL`

A bare `NOT NULL` would force every writer to name the column, which is stronger. It was
rejected on measurement, not preference:

* `routers/referral.py` inserts into `referral_payouts` from **application** code through
  PostgREST without naming `currency`. A bare `NOT NULL` breaks the payout path.
* Three separate migrations define functions that insert into this ledger. Only two are
  installed and neither names `currency` (measured:
  `create_referral_code_for_user(uuid)` and
  `process_referral_commission(uuid,varchar,varchar,numeric)`, `names_currency=False` for
  both). A bare `NOT NULL` is therefore also a replay hazard.

### Why `CHECK (currency = 'USD')` and not an `IN` list

`CHECK (currency IN ('USD','INR'))` was rejected deliberately. Permitting INR without a
defined conversion is how MONEY-04 was reported in the first place. No conversion rate was
invented and no implicit FX is performed anywhere. Widening this constraint is the one
place a multi-currency referral ledger would begin, and doing so forces an explicit
decision about how a mixed balance is denominated and summed.

## Evidence

`tests/test_money04_referral_ledger_is_single_currency_postgres.py` — **24 passed**.

* Column shape read from `information_schema`, per table. The `CHECK` exists, names USD,
  and is asserted **not** to admit INR.
* No money column on any of the three tables is `double precision` or `real` — a ledger
  column of that type has no exact unit regardless of what `currency` says.
* An explicit non-USD `INSERT` raises `CheckViolation`; the **identical** `INSERT` in USD
  succeeds. Without that negative control the refusal could have come from any other
  constraint on the table.
* The exact no-`currency`-key `INSERT` shape the application uses lands as `USD`.
* `UPDATE … SET currency = 'INR'` is refused on all three tables, after creating a real row
  by each table's real write route. Relabelling a stored balance is how a currency becomes
  interchangeable after the fact.
* A credit through `process_referral_commission` lands `USD` on **both** the commission row
  and the wallet row, with the commission equal to 0.20 × payment.
* Duplicate delivery → one row. Five provider retries → one row. A rolled-back transaction
  on a separate non-autocommit connection → nothing survives. Two concurrent connections
  released by a `threading.Barrier` → one row, wallet moved once, both `USD`.
* 027 replayed inside a savepoint changes nothing; the file is additive-only.

## What is NOT claimed

The constraint **cannot detect an amount that is in the wrong unit but declared as USD**. A
writer that puts an INR figure into `commission_amount_usd` without naming `currency` gets
the default and succeeds. No database constraint can distinguish "20.00 USD" from "20.00
rupees labelled USD".

This is pinned as a **passing** test — `test_the_constraint_cannot_catch_a_mislabelled_amount`
stores 8300.00 (roughly 100 USD in rupees) and asserts it succeeds — so the honest limit is
part of the suite rather than a sentence in a document, and so that if a future change ever
makes it detectable, that test fails and this section must be corrected.

The surface is narrowed from "any module may credit any unit silently" to "any module may
credit USD, and must say so to credit anything else". It is not eliminated.

## New findings raised here

### MONEY-06 (P1, OPEN) — Razorpay referral commissions are never credited

The refusal that closed MONEY-04's arithmetic defect is the right call, and its consequence
is that a referrer owed commission on an INR payment is **paid nothing** until someone
settles it by hand. The only evidence that a commission was owed is a `logger.error`. That
makes settlement dependent on **log retention** rather than on a ledger row.

A deliberate, auditable "owed, unpaid, non-USD" record would make the obligation durable.
Nothing writes one. The structurally dead `reverse_referral_commission` path for Razorpay is
a facet of the same gap — there is no commission row to reverse.

Not fixed in Phase 8: it needs a product decision about how a non-USD obligation is
denominated and settled, and inventing one would be exactly the kind of guess this audit
exists to prevent.

### MONEY-07 (P2, OPEN) — the capture path never reads the payment's own currency

`routers/billing.py` hardcodes `"INR"` when converting the captured Razorpay amount from
minor units, rather than reading `payment.currency` from the webhook payload. Razorpay
supports multiple settlement currencies, so a non-INR Razorpay payment would be converted
with the wrong minor-unit exponent.

Today the amount is only written to the invoice row and no commission is credited, so the
blast radius is a mislabelled invoice rather than a mispaid commission — which is why this
is P2. It is recorded because the fix is one line, and because MONEY-04's whole lesson was
that a hardcoded denomination is a latent arithmetic defect waiting for a second currency.

## Correction to this document

The three passages that disagreed about MONEY-04's status are superseded by this block. The
"FIXED by refusal" passage was correct; the two phase-scoped "this phase did not touch it"
notes were about scope, not status, and should not have been read as a status. The finding
is **closed**, with the residual recorded separately as MONEY-06 and MONEY-07.


# PHASE 9 — 2026-10-10. FINANCIAL INTEGRITY (MONEY-06, MONEY-07, FEE-01)

Phase 9 closed the three money-flow findings this document left open at Phase 8. Earlier
prose is retained as the historical record; this section supersedes it where a finding is now
closed. Each was independently re-verified against the code by
`staging/verify_phase9_open_findings.py` and backed by focused tests. No FX was performed, no
currency was substituted, and no live payment or credential was involved.

## MONEY-06 — an owed referral commission is now a durable obligation row, not a log line — CLOSED

Before, the Razorpay branch (and two Stripe cases) refused to credit a non-USD commission —
correctly, since crediting the wrong amount is MONEY-04's defect — but the only artefact was a
`logger.error`, so settlement depended on log retention. A **new table**
`public.referral_obligations` (migration `029_referral_obligations.sql`) now records the
obligation with an explicit amount, an explicit currency, the resolved referrer, a
machine-readable reason and a unique constraint; `_record_referral_obligation` writes it in
both the Razorpay and the Stripe branches.

A new table was chosen over adding a currency column to `referral_commissions` because
MONEY-04's test pins that table to `CHECK currency='USD'` and wallet aggregation over mixed
currencies is undefined. The MONEY-04 refusal log is kept **verbatim** alongside the new row.
**No FX is performed:** a non-USD obligation is recorded with status `unresolved` and a NULL
commission, because the rate source and as-of time are the product decision MONEY-04 refused
to guess. The migration postflight asserts the obligation writer touches neither ledger table.
Evidence: `tests/test_money06_referral_obligations_postgres.py` (50 passed, real PostgreSQL).

## MONEY-07 — the capture path now validates the provider's own declared currency instead of assuming one — CLOSED

Before, the Razorpay conversion paired a payload amount with a hardcoded `"INR"` and never
read the payment's own currency field, so a non-INR payment would convert with the wrong
minor-unit exponent. The fix **validates rather than substitutes**: each handler keeps its
single literal denomination (preserving MONEY-02's single-currency-per-handler invariant and
the fixed amount column) but calls `_require_provider_currency(provider_declared, expected,
...)`, which reads the provider's **own** currency field, normalises case, and raises
`ProviderCurrencyMismatch` (`CURRENCY_MISSING` / `CURRENCY_MALFORMED` / `CURRENCY_UNSUPPORTED`
/ `CURRENCY_MISMATCH`) on anything that does not match. A genuine EUR Stripe session or a
non-INR Razorpay payment now **refuses** rather than being silently mis-denominated.
Substituting the provider's currency was rejected because it breaks that invariant and the
fixed amount column. Evidence: the billing suite and
`tests/test_money06_referral_obligations_postgres.py` (215 passed across the money suites).

## FEE-01 — an unattributable fee row is no longer filed under the all-zero tenant — CLOSED

`FeeEngine._get_tenant_id_from_execution` and `_get_tenant_id_from_position` fell back to
`UUID(int=0)` when the parent row was missing, writing a real fee against a tenant that
belongs to no customer. Both lookups now **raise `TenantResolutionError`**. No production code
path calls the fee engine and every current caller already holds a real tenant uuid, so
refusing to resolve breaks no live call site. Re-verified with **AST** (not substring, since
the fix's own prose names the removed `UUID(int=0)` form three times): **0** such call nodes
in executable code, **2** `TenantResolutionError` raises. Evidence:
`tests/test_fee01_tenant_is_validated_not_sentinel_postgres.py`,
`tests/test_fee_denomination_is_not_assumed.py`.

**Out-of-scope note:** `fee_records.fee_pct` is NOT NULL in the 026 DDL while `record_fee`
writes `None` for a third-asset fee — observed during FEE-01, recorded as an open follow-up,
not in FEE-01's scope.

## MONEY-04 status

MONEY-04 (Razorpay INR amounts credited as USD) was **closed in Phase 8**. Phase 9's
MONEY-06/07 build on that closure by making the owed-commission obligation durable and
refusing a mismatched provider denomination; they do **not** themselves perform any FX
conversion. There is still no reconciliation job against the providers' source of truth
(MONEY-03, open), which the obligation table does not change.


# PHASE 10 — 2026-10-10. FEE-02, MONEY-08 (financial integrity)

Phase 10 confirmed and fixed two financial-integrity defects against the LIVE provisioned
schema, and assessed the referral settlement lifecycle. Each was independently re-verified by
`staging/verify_phase10_open_findings.py`. No live payment, no production, no fabricated rate.

## FEE-02 — a third-asset fee was lost to a NOT NULL violation — FIXED

`fee_records.fee_pct` is the fee as a fraction of trade volume, computable only when the fee
and the volume are the same currency. For a fee in a THIRD asset — a BNB fee on BTC/USDT, the
Binance default — `FeeEngine.record_fee` has no conversion rate and correctly writes
`fee_pct=None`. But the provisioned column was `character varying NOT NULL`, so the commit
raised a NotNullViolation and the real fee (amount + asset known exactly) was **lost**.
Measured: `staging/probe_fee_pct_notnull.py` reproduced the refusal.

FIX: `FeeRecordModel.fee_pct` is now `nullable=True` (authoritative for fresh
`create_all()` databases) and migration `031_fee_records_fee_pct_nullable.sql` drops the NOT
NULL on provisioned databases. `fee_amount` and `fee_asset` stay NOT NULL, so the money
charged is never lost — only the derived, non-computable percentage is absent. **NULL, not 0
(which would falsely state a free trade) or a fabricated rate (forbidden).** Evidence:
`tests/test_fee_third_asset_pct_is_nullable_postgres.py` (4 passed, real PostgreSQL) — the
third-asset fee records with `fee_pct` NULL and amount/asset exact; a quote-asset fee still
computes a percentage; FEE-01 still refuses an unknown execution.

## MONEY-08 — the referral payout insert named non-existent columns — FIXED

`routers/referral.py::create_payout_request` inserted into `referral_payouts` using
`payment_method`/`payment_details`. The provisioned table
(`migrations/006_reconcile_production_database.sql`, confirmed on the live DB) names those
columns `payout_method` (VARCHAR(50) NOT NULL) and `payout_details` (JSONB) — a dual-definition
`CREATE TABLE IF NOT EXISTS` collision (a second migration declares the other names, but 006's
shape is the one materialised, and migration 027 builds on it). So the insert wrote a
non-existent column and left the NOT NULL `payout_method` unset: **every payout request failed
its insert and 500-ed** after the wallet had been debited (then rolled back). The reads read
`payment_method`, which never exists, so a stored method always came back None.

FIX: the insert now writes `payout_method`/`payout_details`; the reads prefer `payout_method`
(falling back to `payment_method` for any legacy row). The API field stays `payment_method`,
so the client contract is unchanged. Evidence:
`tests/test_referral_payout_schema_contract_postgres.py` (5 passed) — the corrected insert
succeeds; the OLD shape still fails as a revert guard; a payout defaults to pending/USD.

## Referral settlement lifecycle — assessed, incomplete, fail-safe

A payout is created `status="pending"`. There is **no** pending→approved→paid transition and
**no** payment initiation anywhere in the code. This is fail-safe — no money moves, and
nothing claims a payout is paid. Reported as incomplete rather than implemented; the
engagement forbids implementing an actual transfer or marking a payout paid without confirmed
payment evidence.

## MONEY-04 status (carried)

MONEY-04 was closed in Phase 8. Phase 10 does not re-open it. No reconciliation job against
the payment providers (MONEY-03) still exists; the new obligation and payout fixes do not add
one.
