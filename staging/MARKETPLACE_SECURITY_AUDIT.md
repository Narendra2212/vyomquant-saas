# MARKETPLACE SECURITY AUDIT

**Scope:** the marketplace subsystem — listing lifecycle, purchase, entitlement, asset access, creator revenue,
refunds, referral interaction, reviews, enumeration, concurrency, and the database objects behind them.
**Date:** 2026-10-07
**Method:** static reading plus behavioural execution against real PostgreSQL 16.2. No production access.

---

## 1. WHAT THIS MARKETPLACE ACTUALLY IS

Established first, because several risk categories in the audit brief turn out not to exist here, and saying so
is more useful than reporting them as PASS.

The marketplace sells **time-bounded subscriptions to algorithmic trading strategies**. It does not sell files,
bots, or downloadable artifacts. A purchase does not transfer a strategy definition — it creates an
**execution permission**.

| Concern | Status here |
|---|---|
| Downloadable asset / blob / signed URL | **NOT APPLICABLE** — none served |
| Inventory / stock / max subscribers | **NOT IMPLEMENTED** — every listing is infinitely purchasable |
| Creator payout mechanism | **NOT IMPLEMENTED** — deliberate; earnings are reporting-only |
| Referral commission on marketplace purchase | **NOT IMPLEMENTED** — referral covers plan billing only |

`deploy_marketplace_strategy` (`backend_app/routers/library.py:3270-3450`) does **not** create a `strategies`
row carrying the owner's `buy_logic` / `sell_logic` / `risk` / `indicators` / `ml_model_path`. It writes a
`strategy_deployments` row *referencing* the owner's existing `strategy_id` / `version_id`, and the subscriber's
bot executes it server-side. The response body explicitly omits `strategy_id` and `version_id`
(`:3458-3465`). The owner's logic never reaches the buyer.

The absence of a payout mechanism is explicit, not an oversight — `library.py:3660-3664` states that no payout
mechanism exists and that presenting one is forbidden by requirement.

**Core tables:** `library_strategies` (listing), `library_subscriptions` (purchase/entitlement),
`marketplace_settlements` (append-only payment ledger), `library_subscription_transitions` (state history),
`deployment_permissions` (access grant), `library_ratings` (dual-purpose: `rating IS NOT NULL` = review,
`rating IS NULL` = favourite).

**Dead code identified:** `marketplace_listings` and `strategy_subscriptions`
(`backend_app/migrations/001_strategy_architecture.sql:265-366`) are referenced by no router. Excluded from scope
as unreachable, flagged for deletion.

---

## 2. HEADLINE FINDINGS

### 2.1 BUG-056 — FIXED / REGRESSION-SAFE (was P0)

Every UPDATE to `library_subscriptions` failed with PostgreSQL `42703`, because
`trg_library_subscriptions_updated_at` assigned `NEW.updated_at` on a table with no such column. The affected
statement includes `settlement_service.settle()`'s `pending → active` transition — **the activation that follows
a confirmed payment**. No paying customer could receive marketplace access.

Fixed by `backend_app/migrations/022_fix_library_subscriptions_updated_at.sql` (additive, idempotent, plus a
generic trigger/column backstop). Closure evidence in `staging/FINAL_FULL_SYSTEM_AUDIT.md` §2. Regression test
`tests/test_bug056_library_subscriptions_updated_at_postgres.py` exercises the real trigger against a real
database — the thing no prior test did.

### 2.2 BUG-057 — OPEN, CONFIRMED (P1)

**A creator can self-approve, self-activate, self-price and self-feature their own listing by writing directly
to the row.**

Intended rule, read from the code rather than assumed: `admin_moderate_strategy`
(`library.py:2670`) is `Depends(get_admin_user)`, writes via **service-role** (bypassing RLS), and its own
comment states the Submission state machine's six admin actions are *"the only path that moves the effective
lifecycle."*

What the database permits (`archived_migrations/root_migrations/004_rls_library_strategies.sql:21-27`):

```sql
CREATE POLICY "Users can update their own strategies"
  ON public.library_strategies FOR UPDATE TO authenticated
  USING (author_id = auth.uid());
```

No `WITH CHECK`. No column restriction. `authenticated` holds a full `INSERT, SELECT, UPDATE, DELETE` grant.
And — grepped across every `*.sql` in the repository — `library_strategies` carries **no transition-guard
trigger**; only `trg_library_strategies_updated_at` (timestamp) and `trg_ls_price_mirror` (price mirror). This is
exactly the control that *does* exist on `library_subscriptions` and that defeats the analogous attack there.

Reproduced freshly, post-BUG-056-fix (`staging/self_approve_recheck.log`):

```
seeded listing dca2d1e7-…: pending, inactive, price_minor=999999, not featured
UPDATE statement affected 1 row(s)
result: moderation_status='approved' is_active=True price_minor=1 is_featured=True
```

Impact: a creator bypasses moderation entirely, can self-feature onto promotional surfaces, and can set
`price_minor = 1`. Note the pricing direction — the server is authoritative on price *at checkout*, so this is
not a "buy for 1 cent" attack against someone else's listing; it is a creator manipulating **their own** listing's
displayed price and approval state without review.

**Reachability: LATENT.** `algo22-terminal/src` grepped for `.from('library_strategies')` /
`.table('library_strategies')` — zero matches. No frontend path. Requires a valid `authenticated` JWT against raw
PostgREST; the anon-key client is present in the browser bundle (`algo22-terminal/src/supabase.js`), so the
capability exists.

**FAIL, not AMBER** — the rule is unambiguous and violated; a missing control is the defect, and current
non-exploitation by the app's own code is mitigation rather than correctness.

---

## 3. LISTING LIFECYCLE

| Action | Endpoint / mechanism | Verdict |
|---|---|---|
| Create / draft | create + Submission state machine | PASS |
| Edit | `update_library_settings` (`library.py:4567+`) | PASS |
| Publish | Submission state machine, admin action | PASS (app layer) / **FAIL at DB layer — BUG-057** |
| Unpublish (creator) | `DELETE /api/library/{id}` → **soft** (`library.py:2120-2185`) | PASS |
| Delete (hard) | **does not exist** | NOT IMPLEMENTED (by design) |
| Archive / restore | **does not exist** as a verb distinct from unpublish | NOT IMPLEMENTED |
| Moderation / approval | `admin_moderate_strategy` + 6 submission actions | PASS (app) / **FAIL (DB)** |
| Republish | **does not exist** | NOT IMPLEMENTED |

**Unpublish does not revoke existing subscribers.** `entitlement_resolver`'s
`_ENTITLING_SUBMISSION_STATES = frozenset({"PUBLISHED", "SUSPENDED", "UNPUBLISHED"})`
(`entitlement_resolver.py:402`) **includes `UNPUBLISHED`**. So unpublishing hides the listing from new buyers
and invalidates the browse cache (`library.py:2176-2182` — a bug `tests/test_marketplace_pipeline.py::test_13`
caught) but leaves current subscribers' deploy access intact until their own period expires.

Assessed as **deliberate and correct**: a buyer who paid for a period should not lose access because the seller
withdrew the listing. Recorded as an AMBER-level *documentation* point rather than a defect — there is no
time-boxed grace period, access simply continues per the existing resolver rule.

**Not fully traced:** whether admin **rejection** (`moderation_status = 'rejected'` + `is_active = False`,
`library.py:2733-2738`) cuts entitlement. `"REJECTED"` does not appear in `_ENTITLING_SUBMISSION_STATES`, which
suggests it does — but the resolver reads `marketplace_submissions(submission_state)` while admin rejection
writes `library_strategies.moderation_status`, and the equivalence of those two signals was not confirmed.
**BLOCKED — needs a follow-up read of `entitlement_resolver.py:440-480` against the admin handler's full update
payload.** Not claimed as PASS.

**Consistency nit (AMBER, not a hole):** `update_library_settings` returns an identical 404 for
absent-vs-not-owned (correct — no existence oracle), while `unpublish_strategy` (`library.py:2119+`) uses 403 for
a non-owner. Both enforce ownership; the status codes disagree.

---

## 4. AUTHORIZATION MATRIX

Actors: Creator A, Creator B, Buyer A, Buyer B, Attacker (authenticated, unrelated), Anon, Admin.

| Scenario | Expected | Observed | Verdict |
|---|---|---|---|
| Creator A modifies Creator B's listing | refuse | refused (owner-scoped filter) | PASS |
| Buyer modifies any listing | refuse | refused (not author) | PASS |
| Buyer A reads Buyer B's subscription | refuse | refused — isolation probe | PASS |
| Buyer A cancels Buyer B's subscription by UUID | refuse | refused — `.eq("id",…).eq("user_id",…)` (`library.py:3466-3560`) | PASS |
| Attacker enumerates pending/rejected listing by ID | refuse, indistinguishably | refused, identical 404 | PASS |
| Attacker forges `user_id` on insert | refuse | refused — probe (0 forged inserts) | PASS |
| Anon reads any per-customer marketplace row | refuse | refused — probe | PASS |
| Tenant substitution via request body | impossible | no such field exists; `extra="forbid"` | PASS |
| **Creator A self-approves own listing** | **refuse** | **permitted** | **FAIL (BUG-057)** |
| Admin moderates any listing | permit | permitted, admin-gated | PASS |

Behavioural basis: `scripts/verify_production_security.py` against the local instance, restricted to the five
marketplace tables — **5 ISOLATED, 0 LEAK, 0 NO CONTROL, 0 NOT SEEDED** (`staging/marketplace_isolation.json`).
`NO CONTROL` and `NOT SEEDED` were **not** counted as isolation passes; there were none.

---

## 5. PURCHASE FLOW

```
listing → checkout → provider session → payment → webhook → settlement ledger → entitlement → deploy permission
```

**Checkout** (`backend_app/backend/marketplace/checkout_service.py`, entry `create_checkout`) persists the
PENDING subscription row **before** creating the provider session — deliberate ordering, documented in the module
docstring, so a provider session can never exist without a local record to reconcile it against.

| Scenario | Mechanism | Verdict |
|---|---|---|
| Successful payment | settlement row + `pending → active` | PASS (post-BUG-056) |
| Failed payment | `payment_failed` status, re-checkout updates existing row | PASS |
| Cancelled payment | row remains PENDING, no entitlement | PASS |
| **Duplicate webhook** | `uq_settlement_reference_reversal UNIQUE (provider_reference, is_reversal)` | PASS (DB-enforced) |
| Webhook replay | same constraint; Redis two-phase lock as first line | PASS |
| Delayed webhook | settlement correlated by ledger read-back, not wall clock | PASS |
| Out-of-order webhook | guard trigger requires settlement before `active` | PASS |
| **Duplicate checkout (same buyer, same listing)** | `library_subscriptions_user_library_idx UNIQUE (library_id, user_id)` (`archived_migrations/root_migrations/007_create_library_subscriptions.sql:23`) | PASS (DB-enforced) |
| Concurrent checkout | read-then-decide + UNIQUE backstop | PASS for correctness; **AMBER for UX** |
| Payment timeout | PENDING row persists; reconcilable | PASS |
| Provider failure | `CheckoutWriteFailed` → structured `MarketplaceError` | PASS |

**The real exactly-once guarantee is the database constraint, not Redis.** The Redis two-phase lock
(`webhook:stripe:{event_id}` / `webhook:razorpay:{event_id}`, processing → completed, 60s → 86400s TTL,
`routers/billing.py:2027-2314` and `:2345-2499`) is a first line that reduces work. If Redis is unavailable or
the 60-second window lapses mid-processing, `uq_settlement_reference_reversal` still refuses the second ledger
row. This layering is correct and is a genuine strength — it is also exactly what subscription billing
*lacks* (see `staging/MONEY_FLOW_AUDIT.md`).

**AMBER (UX, not correctness) on concurrent first-time checkout:** the losing request's `23505` is caught by a
generic `except Exception` in `_persist_pending` (`checkout_service.py:1112-1131`) and surfaces as a checkout
failure rather than a clean "you already have a subscription." No double subscription can be created; the
message is just wrong. **Exact HTTP response not behaviourally confirmed** — would need a concurrent-request
test.

---

## 6. PRICE INTEGRITY — PASS

**Confirmed closed structurally, which is stronger than validated.** `MarketplaceCheckoutRequest`
(`library.py:2823-2826`) accepts **`{currency}` only**. There is no price field in the request body at all, so
there is nothing for a client to tamper with. The server reads `price_minor` from the listing row via
`money.amount_for_listing()`.

| Attack | Verdict | Why |
|---|---|---|
| Frontend price tampering | PASS | No price field in request body |
| Negative price | PASS | `price_minor` is server-read; listing-side validation + `chk_ls_split_conserved` |
| Zero price | PASS | Server-read |
| Decimal precision abuse | PASS | Integer minor units end to end; no float anywhere in `money.py` |
| Stale price / price changed during checkout | PASS | Price snapshotted onto the subscription row at checkout; settlement reconciles against the snapshot, not the live listing |
| Currency manipulation | PASS | Refund in a differing currency is refused with no write (`billing.py:910-932`) |
| Discount manipulation | NOT APPLICABLE | No discount mechanism in the marketplace path |

**Caveat tied to BUG-057:** a creator can set their *own* listing's `price_minor` to 1 outside the pricing
workflow. That is a moderation-integrity failure, not a price-tampering failure — the buyer is still charged
exactly what the row says.

---

## 7. ENTITLEMENT

`entitlement_resolver.resolve()` is the single admission decision for both deploy and paper-session start.
Properties verified by reading:

- **Identity is server-side only.** Scoped by `caller.id`; never from a request body.
- **Sweep-independent expiry.** Compares `now` to `period_expiry` directly rather than trusting the `status`
  label, so a late expiry sweep cannot extend access.
- **Read failures fail loud.** `EntitlementReadFailed` → 503. Never folded into a false 403/404 — a database
  problem is not reported as "you are not entitled."

| Scenario | Verdict |
|---|---|
| Unauthorized access (no subscription) | PASS |
| Expired entitlement | PASS (direct timestamp comparison) |
| Revoked / cancelled | PASS — cancellation retains access to unchanged expiry (deliberate, documented) |
| **Refunded purchase** | PASS — `status → refunded`, access lost on next resolve |
| Chargeback | PASS — same reversal path as refund |
| Deleted listing | NOT APPLICABLE (no hard delete) |
| Unpublished listing | **By design retains access** — `UNPUBLISHED` is an entitling state |
| Repeated deploy | PASS — resolver consulted fresh on every request |
| Direct storage access | NOT APPLICABLE (no storage artifact) |
| Signed URL reuse | NOT APPLICABLE (no signed URLs in marketplace) |
| **Buyer self-grants entitlement** | **PASS — refused by DB trigger** |

The last row is the one worth stating in full, because it is the strongest control found in the subsystem.
`library_subscriptions` RLS is `FOR ALL` with `USING (auth.uid() = user_id)` **and**
`WITH CHECK (auth.uid() = user_id)` — i.e. the buyer has full self-service CRUD on their own row at the RLS
layer, and RLS alone would permit self-activation. It is defeated independently by
`trg_subscription_transition_guard` (function `marketplace_subscription_guard()`), which requires a
non-reversal `marketplace_settlements` row with `settled_at` at or after `period_expiry` before any transition
to `active`. Executed (`staging/self_activate_postfix.log`):

```
seeded PENDING subscription 2fca5ac6-… (no payment made, no settlement row exists)
marketplace_settlements rows for this subscription: 0
UPDATE REFUSED: 23514 transition pending -> active requires a confirmed payment:
  no non-reversal marketplace_settlements row … has settled_at at or after the current period expiry <NULL>
result: status='pending' period_expiry=None
```

This is a real, working, non-RLS defence-in-depth control, and it is precisely the control BUG-057 shows is
**missing** on `library_strategies`.

---

## 8. ASSET SECURITY — PASS (largely NOT APPLICABLE)

No file, blob, object key or signed URL is served by the marketplace, so the entire class of asset attacks has
no surface. What remains is the deployment-permission grant, and it is structurally IDOR-free:

- `check_deployment_permission` (`library.py:427-560`) filters on **both** `.eq("library_id", lib_uid)` and
  `.eq("user_id", user_uid)` (`:500-507`).
- No endpoint accepts a `subscription_id` as a parameter. The only path parameter is `library_id`, which is the
  public listing identifier. There is nothing to substitute.
- `DeployMarketplaceStrategyRequest` (`library.py:3307-3330`) uses `model_config = ConfigDict(extra="forbid")`,
  accepting only `symbol`, `timeframe`, `capital`, `session_options`. An attempt to smuggle `subscription_id`,
  `owner_id`, `version_id` or `tenant_id` is a 422 that **echoes no supplied value**.

| Attack | Verdict |
|---|---|
| IDOR on subscription/entitlement | PASS (no caller-supplied id exists) |
| Path traversal | NOT APPLICABLE |
| Object-key manipulation | NOT APPLICABLE |
| Malicious filename / MIME spoofing / oversized file | NOT APPLICABLE |
| Direct object-storage access | NOT APPLICABLE |
| Unauthorized download | NOT APPLICABLE |
| Cover-image reference (the one user-supplied URL) | PASS — see `staging/API_SECURITY_AUDIT.md` §SSRF |

Related fix verified present: `library.py:2411-2435` deliberately does **not** copy `ml_model_path` when
cloning. It used to, which placed the author's filesystem path onto the cloning user's row — a column the live
bot hands to `joblib.load`.

---

## 9. CREATOR REVENUE

**Split:** `backend_app/backend/marketplace/money.py::split_ninety_ten()` —
`owner_share = (amount_minor * 90) // 100`, `platform_fee = amount_minor - owner_share`. The platform fee is the
**residual**, so conservation holds by construction, not by a second calculation that could disagree. Pure
integer arithmetic; no float appears in the module.

**Database-enforced conservation** (confirmed via `pg_get_constraintdef` on the live instance):

```
chk_settlement_conserved   CHECK (owner_share_minor + platform_fee_minor = amount_minor)
chk_ls_split_conserved     CHECK (price_minor IS NULL OR owner_share_minor + platform_fee_minor = price_minor)
chk_ls_active_has_period   CHECK (status <> 'active' OR (period_start IS NOT NULL
                                 AND period_expiry IS NOT NULL AND period_expiry > period_start))
```

**Reconciliation identity, as actually implemented:**

```
buyer debit (amount_minor) = creator share (owner_share_minor) + platform share (platform_fee_minor)
referral share  = 0   (no referral commission exists on marketplace purchases)
fees / taxes    = 0   (not modelled in the marketplace ledger)
```

The brief's full identity reduces to the two-term form here because the other two terms are not implemented.
That is reported as implementation fact, not as a passing four-term reconciliation.

**Aggregation is race-free by construction.** `creator_earnings` / `EarningsTotals`
(`settlement_service.py:460-500`, `:1200-1240`) computes totals as a live `SUM` over immutable, append-only
ledger rows, signed per `is_reversal`. There is no counter column, so there is no `UPDATE counter SET total =
total + x` lost-update race. Negative `owner_total_minor` is reported as-is rather than clamped
(`settlement_service.py:482-484`) — correct, since clamping would hide a net-refunded creator.

| Scenario | Verdict |
|---|---|
| Duplicate webhook double-credits creator | PASS — DB unique constraint |
| Concurrent purchases | PASS — independent append-only rows |
| Refund reduces creator total | PASS — signed aggregate |
| Chargeback | PASS — same path |
| Concurrent payout | NOT APPLICABLE — no payout mechanism exists |
| Concurrent settlement vs aggregation read | PASS structurally; **no test exists** |

Last row is a **test-coverage** gap, not a defect: correctness follows from append-only + recompute, but nothing
pins it.

---

## 10. REFUNDS / CHARGEBACKS

**Trigger:** Stripe `charge.refunded` (`billing.py:2269-2314` — deliberately *not* `charge.refund.updated`,
which also fires on a *failed* refund attempt) and Razorpay `refund.processed` (`:2477-2483`).

**Correlation by own ledger, not provider metadata.** `find_settled_payment(provider_reference=…)` reads back
the settlement row this system wrote, yielding `subscription_id`, `currency`, `amount_minor` authoritatively.
`billing.py:820-850` documents that metadata-based correlation previously dropped most refunds silently in
production.

**Recording:** a new row with `is_reversal = TRUE`. Never an UPDATE or DELETE of the original.
`uq_settlement_reference_reversal` lets the payment and its reversal coexist while making a redelivered refund
webhook a no-op.

| Aspect | Behaviour | Verdict |
|---|---|---|
| Entitlement reversal | **Immediate** — `status → refunded`; resolver treats non-active as lapsed | PASS |
| Creator balance reversal | Signed aggregate reduces owner total | PASS |
| Platform accounting | Reversal row carries the mirrored split | PASS |
| Referral reversal | NOT APPLICABLE (no marketplace referral) | — |
| Invoice state | Reversal recorded in ledger | PASS |
| Audit trail | `audit_uncorrelated_refund(...)` for a refund with no matching settlement (`billing.py:901-909`) | PASS |
| Currency mismatch | Refused, no write (`billing.py:910-932`) | PASS |
| **Partial refund** | **Nothing recorded; entitlement untouched** | **AMBER — open requirement** |

The partial-refund gap is documented in the source rather than hidden (`billing.py:838-848`, `:933-944`): if
`refunded_amount != original_amount`, no reversal row is written, it is logged as a reconciliation mismatch, and
`settle()` answers `MISMATCHED`. The comment states this is an open requirements decision because a partial
reversal must also decide what happens to the entitlement. Reported as AMBER (known, bounded, documented,
non-silent) rather than FAIL — but it means **a partially refunded buyer keeps full access and the creator's
total is unadjusted**.

Note the contrast with voluntary cancellation, which is deliberately different: `cancel_subscription`
(`library.py:3466-3496`) retains entitlement to the unchanged expiry. Refund revokes; cancel does not. Both are
intentional and documented.

---

## 11. REFERRAL / AFFILIATE INTERACTION — NOT APPLICABLE HERE

No referral commission is tied to a marketplace purchase. The referral system credits on **subscription/plan
billing** only. Self-referral is blocked at the schema level by
`chk_no_self_referral CHECK (referrer_id != referred_id)` (`migrations/006_reconcile_production_database.sql:595`
and `migrations/referral_system_redesign.sql:134`) — enforced regardless of which function version is installed.

The referral system does carry a **confirmed double-commission defect**, but it belongs to the plan-billing
money flow, not the marketplace. It is documented in `staging/MONEY_FLOW_AUDIT.md` §Referral.

---

## 12. REVIEWS / RATINGS — PASS

| Control | Mechanism | Verdict |
|---|---|---|
| Purchase-gated | `rate_strategy` requires an existing `library_ratings` row with `is_verified_clone = TRUE` (`library.py:2585-2608`), checked via `.single()` **before** the upsert | PASS |
| Duplicate review | Structurally impossible — upsert `on_conflict="library_id,user_id"` against `UNIQUE(library_id, user_id)` (`library.py:2613-2625`) | PASS |
| Edit/delete another user's review | Impossible — payload always sets `user_id` to the caller's own; no PATCH/DELETE-by-review-id endpoint exists | PASS |
| Rating range | `rating: int = Field(..., ge=1, le=5)` (`library.py:229`) — 422 before handler body | PASS |
| Review length | `max_length=500` (`library.py:230`) | PASS |
| XSS / HTML injection | React renders `{review.review_text}` as text content (`StrategyMarketplace.jsx:1459-1464`); no `dangerouslySetInnerHTML` | PASS |
| Moderation | No review-moderation surface | NOT IMPLEMENTED |

**Purchase-gating is genuine, and the chain matters.** Review requires `is_verified_clone = TRUE`; that flag
comes from having cloned; and `clone_strategy` (`library.py:2195-2357`) requires
`entitlement_resolver.resolve()` to be entitling — gate order: self-clone 409 → owner-consent
(`source_cloning_enabled`) 403 → **entitlement required** 403 → idempotent clone. So a review requires an active
paid subscription. (An initial reading suggested cloning was free; that was checked against the handler and is
wrong.)

**XSS has a regression guard, not just a current-state pass.**
`StrategyMarketplace.test.jsx:22-43, 193-196, 884-894` feeds `<img src=x onerror=…>` through `review_text`,
asserts `querySelector('img') === null`, and separately fails if `dangerouslySetInnerHTML` is ever introduced
into that file. Backend stores the text length-bounded and unsanitized, which is correct for a
text-rendered-as-text field, but means safety depends on the render path staying escaped — which is what the
test pins.

---

## 13. SEARCH / ENUMERATION — PASS

**Browse** (`GET /api/library` → `browse_library`, `library.py:1023-1103+`): base query is always
`.eq("is_active", True).in_("moderation_status", ["approved", "featured"])`. No filter combination
(`category`, `difficulty`, `has_ml`, `min_sharpe`, `min_return`, `tags`, `q`, `sort`) can widen it. Auth is
optional; anon sees the same catalogue without per-user enrichment.

**The detail endpoint does not trust the list endpoint.** `get_library_detail` (`library.py:1687-1797+`)
re-applies the same gate in its own query (`:1721-1726`). A non-owner requesting a pending, rejected or
unpublished listing by ID gets a `.single()` miss that falls through to a **shared NOT_FOUND path**
(`:1757-1773`) where "no such id", "listing whose submission is not published" and "another creator's
unpublished listing" return the **identical status and body**, disclosing no existence, owner or state. The
listing's own author can still read their own row in any state via a second, author-scoped query
(`.eq("author_id", caller_id)`, `:1750-1766`) that structurally cannot return another creator's row.

This is the correct shape — the authorization decision is re-made at the object boundary rather than inherited
from the collection query.

---

## 14. CONCURRENCY

| Scenario | Mechanism | Verdict |
|---|---|---|
| Two simultaneous purchases, same buyer+listing | `UNIQUE (library_id, user_id)` | PASS (correctness); AMBER (loser's error message) |
| Duplicate payment webhook | `UNIQUE (provider_reference, is_reversal)` | PASS |
| Simultaneous refund | same constraint, `is_reversal=TRUE` row | PASS |
| Simultaneous payout | NOT APPLICABLE (no payouts) | — |
| Simultaneous listing publication | Submission state machine + `trg_submission_transition_guard` re-checks the edge against `marketplace_submission_allowed_transitions` | PASS |
| Price change during checkout | price snapshotted on the subscription row | PASS |
| Concurrent settlement vs earnings read | append-only + recompute | PASS structurally; **untested** |

`tests/test_marketplace_concurrency.py` exists and passes (within the 317-test batch A). It does not cover the
settlement-vs-aggregation read, and it does not assert the HTTP response of the losing concurrent checkout.

---

## 15. DATABASE OBJECTS — VERIFIED ON THE LIVE INSTANCE

Queried directly via `pg_constraint` / `pg_indexes` / `information_schema.columns` / `pg_policies`.

- **Identity columns:** all marketplace identity columns are `uuid` — consistent with the BUG-049 fix from an
  earlier phase. No `text`/`varchar(36)` divergence remains in this subsystem.
- **RLS:** enabled (`relrowsecurity = true`) with policies present on all 7 marketplace tables checked.
  `relforcerowsecurity = false` everywhere — see the FORCE RLS note in `staging/AUTH_RLS_AUDIT.md`.
- **Foreign keys:** `ON DELETE CASCADE` (`deployment_permissions → library_strategies`,
  `library_subscriptions → library_strategies` / `auth.users`), `ON DELETE RESTRICT`
  (`marketplace_settlements → library_strategies` / `library_subscriptions` — ledger rows protected from
  deletion), `ON DELETE SET NULL` (`deployment_permissions.subscription_id`). The RESTRICT on the ledger is the
  right choice and was chosen, not defaulted.
- **Append-only enforcement:** `trg_submission_transitions_append_only`, `trg_evidence_append_only`.
- **Unique constraints:** `uq_settlement_reference_reversal`, `library_subscriptions_user_library_idx`,
  `UNIQUE(library_id, user_id)` on `library_ratings`.

---

## 16. MARKETPLACE VERDICT

```
MARKETPLACE: NO-GO
```

**Not** because the subsystem is weak — its money handling, isolation, entitlement resolution and enumeration
discipline are the strongest parts of this codebase, and BUG-056 (which would have been an absolute blocker) is
fixed and regression-tested.

NO-GO rests on **BUG-057**, which is a confirmed, reproduced failure of moderation integrity at the database
layer: a creator can place an unreviewed listing into the approved, active and featured state. For a marketplace
whose trust model is "listings are independently reviewed before buyers see them," that is a gating defect, and
it is latent only because no frontend code happens to call the table today.

Secondary items that do not individually gate but should ship with it: the partial-refund gap (buyer keeps
access, creator total unadjusted), and the losing-concurrent-checkout error message.

**Minimum to reach GO:**
1. Fix BUG-057 — column-level revocation of `moderation_status` / `is_active` / `is_featured` / `price_minor`
   from `authenticated`, **or** a guard trigger analogous to `marketplace_subscription_guard()`.
2. Re-run `staging/reproduce_listing_self_approve.py` and require a refusal.
3. Add a `*_postgres.py` regression test asserting the refusal against a live policy — not a mock.
4. Re-run the marketplace suite plus the 12-probe set and the 19-file PostgreSQL suite.
5. Decide the partial-refund requirement explicitly (revoke, pro-rate, or document as accepted).
