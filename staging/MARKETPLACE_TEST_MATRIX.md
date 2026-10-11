# MARKETPLACE TEST MATRIX

**Date:** 2026-10-07
**Legend:**

| Code | Meaning |
|---|---|
| **EXEC** | Verified by executing the scenario against a real PostgreSQL instance and observing the outcome |
| **SCHEMA** | Verified by reading the live database catalogue (`pg_constraint`, `pg_indexes`, `information_schema`) |
| **CODE** | Verified by reading the implementation; logic traced end to end |
| **SUITE** | Covered by a test that passed in the post-fix baseline; the test's own assertions were not independently read |
| **N/A** | Mechanism does not exist in this product |
| **BLOCKED** | Could not be exercised (tooling or missing dependency) |

A row is PASS only if the control was observed to refuse what it exists to refuse, or the risk was eliminated
structurally. "Endpoint exists" is never PASS.

---

## 1. LISTING LIFECYCLE

| # | Scenario | Expected | Method | Verdict |
|---|---|---|---|---|
| 1.1 | Create listing | owner-scoped insert | CODE | PASS |
| 1.2 | Draft state not publicly visible | excluded from browse | CODE | PASS |
| 1.3 | Edit own listing | permitted | CODE | PASS |
| 1.4 | Edit another creator's listing | 404 (no existence oracle) | CODE | PASS |
| 1.5 | Publish via submission state machine | admin-gated | CODE | PASS |
| 1.6 | **Publish by writing the row directly** | **refuse** | **EXEC** | **FAIL — BUG-057** |
| 1.7 | Unpublish (creator) | soft, existing clones unaffected | CODE | PASS |
| 1.8 | Unpublish invalidates browse cache | cache cleared | SUITE (`test_marketplace_pipeline::test_13`) | PASS |
| 1.9 | Hard delete | endpoint absent | CODE | N/A |
| 1.10 | Archive / restore distinct from unpublish | absent | CODE | N/A |
| 1.11 | Republish | absent | CODE | N/A |
| 1.12 | Admin moderation (approve/reject/feature/hide) | admin-only | CODE | PASS |
| 1.13 | Moderation disagreeing with submission state | 409 | CODE | PASS |
| 1.14 | Existing subscriber after creator unpublish | retains access (`UNPUBLISHED` is entitling) | CODE | PASS (by design) |
| 1.15 | Existing subscriber after **admin rejection** | expected to lose access | — | **BLOCKED — not traced** |

**1.15 note:** `"REJECTED"` does not appear in `_ENTITLING_SUBMISSION_STATES` (`entitlement_resolver.py:402`),
which suggests rejection cuts entitlement — but the resolver reads
`marketplace_submissions(submission_state)` while admin rejection writes `library_strategies.moderation_status`,
and the equivalence of those two signals was not confirmed. Not claimed as PASS.

---

## 2. AUTHORIZATION

| # | Scenario | Expected | Method | Verdict |
|---|---|---|---|---|
| 2.1 | Creator A modifies Creator B's listing | refuse | CODE | PASS |
| 2.2 | Buyer modifies any listing | refuse | CODE | PASS |
| 2.3 | Buyer A reads Buyer B's subscription | refuse | EXEC (isolation probe) | PASS |
| 2.4 | Buyer A updates Buyer B's subscription | refuse | EXEC | PASS |
| 2.5 | Buyer A deletes Buyer B's subscription | refuse | EXEC | PASS |
| 2.6 | Buyer A forges an insert as Buyer B | refuse | EXEC | PASS |
| 2.7 | Buyer A cancels Buyer B's subscription by UUID | refuse | CODE (`.eq("id",…).eq("user_id",…)`) | PASS |
| 2.8 | Anon reads any per-customer marketplace row | refuse | EXEC | PASS |
| 2.9 | Owner reads own row | permit | EXEC | PASS |
| 2.10 | Tenant substitution via request body | field does not exist; `extra="forbid"` | CODE | PASS |
| 2.11 | ID substitution bypasses authorization | no caller-supplied subscription id exists | CODE | PASS |
| 2.12 | Admin moderates any listing | permit | CODE | PASS |
| 2.13 | **Creator self-approves own listing** | **refuse** | **EXEC** | **FAIL — BUG-057** |

**Isolation basis (2.3-2.9):** `scripts/verify_production_security.py` restricted to the five marketplace
tables — **5 ISOLATED, 0 LEAK, 0 NO CONTROL, 0 NOT SEEDED** (`staging/marketplace_isolation.json`).
`NO CONTROL` and `NOT SEEDED` were not counted as passes; there were none.

---

## 3. PURCHASE FLOW

| # | Scenario | Expected | Method | Verdict |
|---|---|---|---|---|
| 3.1 | Successful payment → entitlement | subscription `active` | EXEC (post-fix) | PASS |
| 3.2 | **Pre-fix: successful payment → entitlement** | — | EXEC | **was FAIL (BUG-056), now FIXED** |
| 3.3 | Failed payment | `payment_failed`, no entitlement | CODE | PASS |
| 3.4 | Cancelled payment | row stays PENDING | CODE | PASS |
| 3.5 | Duplicate webhook | second ledger row refused | SCHEMA (`uq_settlement_reference_reversal`) | PASS |
| 3.6 | Webhook replay after 24h lock TTL | DB constraint still refuses | SCHEMA | PASS |
| 3.7 | Delayed webhook | correlated by ledger read-back, not wall clock | CODE | PASS |
| 3.8 | Out-of-order webhook (activate before settle) | refuse | EXEC (`23514`) | PASS |
| 3.9 | Duplicate checkout, same buyer + listing | one subscription only | SCHEMA (`UNIQUE(library_id,user_id)`) | PASS |
| 3.10 | Concurrent first-time checkout — correctness | no double subscription | SCHEMA | PASS |
| 3.11 | Concurrent first-time checkout — loser's response | clean "already subscribed" | — | **AMBER** (generic failure; not behaviourally confirmed) |
| 3.12 | Payment timeout | PENDING persists, reconcilable | CODE | PASS |
| 3.13 | Provider session failure | structured `MarketplaceError` | CODE | PASS |
| 3.14 | PENDING row written before provider session | ordering preserved | CODE | PASS |
| 3.15 | Webhook duplicate delivery vs **real Redis** | dedupe | — | **BLOCKED — 6 skipped, no Redis** |

---

## 4. PRICE INTEGRITY

| # | Scenario | Expected | Method | Verdict |
|---|---|---|---|---|
| 4.1 | Client supplies a price in the checkout body | impossible | CODE (`MarketplaceCheckoutRequest` = `{currency}` only) | PASS |
| 4.2 | Negative price | refuse | SCHEMA + CODE | PASS |
| 4.3 | Zero price | server-read only | CODE | PASS |
| 4.4 | Decimal-precision manipulation | integer minor units throughout | CODE (`money.py`, no float) | PASS |
| 4.5 | Stale price | snapshotted on the subscription row | CODE | PASS |
| 4.6 | Price changed mid-checkout | settlement reconciles against the snapshot | CODE | PASS |
| 4.7 | Currency manipulation on refund | refuse, no write | CODE (`billing.py:910-932`) | PASS |
| 4.8 | Discount manipulation | no discount mechanism | — | N/A |
| 4.9 | Creator sets own listing `price_minor = 1` outside the pricing workflow | refuse | EXEC | **FAIL — BUG-057** |

**4.9 vs 4.1 distinction:** a buyer cannot tamper with what they are charged (4.1 PASS). A creator can
manipulate their **own** listing's price and approval state outside review (4.9 FAIL). Different controls,
different verdicts.

---

## 5. ENTITLEMENT

| # | Scenario | Expected | Method | Verdict |
|---|---|---|---|---|
| 5.1 | No subscription → deploy | refuse | CODE | PASS |
| 5.2 | Expired entitlement | refuse | CODE (direct timestamp compare, sweep-independent) | PASS |
| 5.3 | Expiry sweep has not yet run | still refuse | CODE | PASS |
| 5.4 | Cancelled subscription | retains access to unchanged expiry | CODE | PASS (by design) |
| 5.5 | Refunded purchase | access lost on next resolve | CODE | PASS |
| 5.6 | Chargeback | same reversal path | CODE | PASS |
| 5.7 | Deleted listing | no hard delete | — | N/A |
| 5.8 | Unpublished listing | retains access | CODE | PASS (by design) |
| 5.9 | Repeated deploy | resolver consulted fresh each time | CODE | PASS |
| 5.10 | **Buyer self-grants entitlement with zero payment** | **refuse** | **EXEC (`23514`)** | **PASS** |
| 5.11 | Entitlement read failure | 503, never a false 403/404 | CODE | PASS |
| 5.12 | Identity supplied in request body | ignored; server-side only | CODE | PASS |
| 5.13 | Direct storage access | no storage artifact | — | N/A |
| 5.14 | Signed URL reuse | no signed URLs | — | N/A |

**5.10 observed output:**

```
marketplace_settlements rows for this subscription: 0
UPDATE REFUSED: 23514 transition pending -> active requires a confirmed payment
result: status='pending' period_expiry=None
```

This is the strongest control in the subsystem, and it is **not** RLS — `library_subscriptions` RLS is `FOR ALL`
with buyer-scoped `USING` **and** `WITH CHECK`, so RLS alone would permit self-activation. It is defeated
independently by `trg_subscription_transition_guard`. It is also precisely the control BUG-057 shows is missing
on `library_strategies`.

---

## 6. ASSET SECURITY

| # | Scenario | Expected | Method | Verdict |
|---|---|---|---|---|
| 6.1 | Buyer receives the strategy definition | must not | CODE (`deploy_marketplace_strategy` omits `strategy_id`/`version_id`; no logic columns copied) | PASS |
| 6.2 | IDOR on subscription id | no such parameter exists | CODE | PASS |
| 6.3 | IDOR on `library_id` belonging to another's subscription | refuse (`.eq(library_id).eq(user_id)`) | CODE | PASS |
| 6.4 | Mass assignment on deploy request | `extra="forbid"`, 422 echoes no value | CODE | PASS |
| 6.5 | Path traversal | no file path from marketplace input | — | N/A |
| 6.6 | Object-key manipulation | no object store | — | N/A |
| 6.7 | Malicious filename / MIME spoof / oversized upload | no upload surface | — | N/A |
| 6.8 | Clone copies author's `ml_model_path` | must not | CODE (`library.py:2411-2435`) | PASS |
| 6.9 | Cover-image reference SSRF | refuse | CODE + SUITE | PASS |

---

## 7. CREATOR REVENUE

| # | Scenario | Expected | Method | Verdict |
|---|---|---|---|---|
| 7.1 | `owner_share + platform_fee == amount` | always | SCHEMA (`chk_settlement_conserved`) | PASS |
| 7.2 | Split arithmetic uses no float | integer only | CODE (`money.py`) | PASS |
| 7.3 | Platform fee is the residual | conservation by construction | CODE | PASS |
| 7.4 | Subscription split conserved | always | SCHEMA (`chk_ls_split_conserved`) | PASS |
| 7.5 | Active subscription has a valid period | always | SCHEMA (`chk_ls_active_has_period`) | PASS |
| 7.6 | Duplicate webhook double-credits creator | refuse | SCHEMA | PASS |
| 7.7 | Concurrent purchases | independent append-only rows | CODE | PASS |
| 7.8 | Refund reduces creator total | signed aggregate | CODE | PASS |
| 7.9 | Earnings stored as a counter | must not be | CODE (live SUM, no counter column found) | PASS |
| 7.10 | Negative creator total after net refund | reported, not clamped | CODE | PASS |
| 7.11 | Concurrent settlement vs earnings read | consistent | — | **no test exists** |
| 7.12 | Payout / concurrent payout | no payout mechanism | — | N/A |
| 7.13 | Ledger rows protected from deletion | `ON DELETE RESTRICT` | SCHEMA | PASS |

**Reconciliation identity as implemented:** `buyer debit = creator share + platform share`. The brief's
`referral share` and `fees/taxes` terms are **zero because those mechanisms do not exist on this path** — stated
as implementation fact, not as a passing four-term reconciliation.

---

## 8. REFUNDS / CHARGEBACKS

| # | Scenario | Expected | Method | Verdict |
|---|---|---|---|---|
| 8.1 | Full refund recorded | `is_reversal=TRUE` row, original untouched | CODE | PASS |
| 8.2 | Entitlement revoked | immediate (`status → refunded`) | CODE | PASS |
| 8.3 | Creator share reversed | signed aggregate | CODE | PASS |
| 8.4 | Duplicate refund webhook | no-op | SCHEMA | PASS |
| 8.5 | Refund in wrong currency | refuse, no write | CODE | PASS |
| 8.6 | Non-integer refund amount | refuse | CODE | PASS |
| 8.7 | Refund with no matching settlement | audited, not dropped | CODE | PASS |
| 8.8 | Correlation via provider metadata | must not rely on it | CODE (reads back own ledger) | PASS |
| 8.9 | `charge.refund.updated` on a *failed* refund | must not revoke | CODE (only `charge.refunded` handled) | PASS |
| 8.10 | **Partial refund** | defined behaviour | CODE | **AMBER — nothing recorded, entitlement untouched** |
| 8.11 | Referral reversal | no marketplace referral | — | N/A |

---

## 9. REFERRAL (marketplace interaction)

| # | Scenario | Expected | Method | Verdict |
|---|---|---|---|---|
| 9.1 | Referral commission on a marketplace purchase | none exists | CODE | N/A |
| 9.2 | Self-referral | refuse | SCHEMA (`chk_no_self_referral`) | PASS |
| 9.3 | Duplicate attribution | — | — | N/A for marketplace |
| 9.4 | Duplicate commission | — | — | **FAIL in plan billing** — see `staging/MONEY_FLOW_AUDIT.md` §4 |
| 9.5 | Refund after commission | reversal exists | CODE | PASS |
| 9.6 | Webhook replay → double commission | refuse | — | **FAIL** (plan billing, not marketplace) |

Rows 9.4 and 9.6 are recorded here only to prevent a reader concluding referral is clean; the defect belongs to
the plan-billing flow.

---

## 10. REVIEWS / RATINGS

| # | Scenario | Expected | Method | Verdict |
|---|---|---|---|---|
| 10.1 | Review without purchase | refuse | CODE (`is_verified_clone` required) | PASS |
| 10.2 | Clone without entitlement | refuse | CODE (`entitlement_resolver.resolve()` must entitle) | PASS |
| 10.3 | Duplicate review | impossible | SCHEMA + CODE (upsert `on_conflict`) | PASS |
| 10.4 | Edit another user's review | impossible | CODE (payload always own `user_id`) | PASS |
| 10.5 | Delete another user's review | no such endpoint | CODE | PASS |
| 10.6 | Rating out of range | 422 | CODE (`ge=1, le=5`) | PASS |
| 10.7 | Oversized review text | 422 | CODE (`max_length=500`) | PASS |
| 10.8 | XSS via `review_text` | rendered inert | SUITE (payload test asserts `querySelector('img') === null`) | PASS |
| 10.9 | `dangerouslySetInnerHTML` introduced later | test fails | SUITE (static-analysis-style guard) | PASS |
| 10.10 | Review moderation | no surface | — | N/A |
| 10.11 | Self-review by the author | self-clone 409 blocks the prerequisite | CODE | PASS |

---

## 11. SEARCH / ENUMERATION

| # | Scenario | Expected | Method | Verdict |
|---|---|---|---|---|
| 11.1 | Browse excludes pending/rejected/suspended | always | CODE (base query, filter-independent) | PASS |
| 11.2 | Filter combination widens visibility | must not | CODE | PASS |
| 11.3 | Detail endpoint for a pending listing (non-owner) | 404 | CODE (independent re-check at `:1721-1726`) | PASS |
| 11.4 | Detail endpoint for another creator's unpublished listing | 404 | CODE | PASS |
| 11.5 | Nonexistent id vs unauthorized id | **identical** response | CODE (shared NOT_FOUND path `:1757-1773`) | PASS |
| 11.6 | Owner reads own listing in any state | permit | CODE (author-scoped second query) | PASS |
| 11.7 | Anon browse | permitted, no per-user enrichment | CODE | PASS |
| 11.8 | Enumeration via sort/pagination | base filter applies | CODE | PASS |

11.5 is the row that matters — the detail endpoint does not inherit the collection query's filtering, it re-makes
the decision at the object boundary, and refusal is indistinguishable from absence.

---

## 12. CONCURRENCY

| # | Scenario | Expected | Method | Verdict |
|---|---|---|---|---|
| 12.1 | Two simultaneous purchases, same buyer + listing | one succeeds | SCHEMA | PASS |
| 12.2 | Duplicate payment webhook | one ledger row | SCHEMA | PASS |
| 12.3 | Simultaneous refund | one reversal row | SCHEMA | PASS |
| 12.4 | Simultaneous payout | no payouts | — | N/A |
| 12.5 | Simultaneous listing publication | state machine + trigger re-check | CODE | PASS |
| 12.6 | Price change during checkout | snapshot wins | CODE | PASS |
| 12.7 | Concurrent settlement vs earnings aggregation | consistent | — | **untested** |
| 12.8 | Concurrent purchase, **executed** | — | — | **BLOCKED — TOOLING** |
| 12.9 | Submission transition race | refuse illegal edge | CODE (`trg_submission_transition_guard`) | PASS |

---

## 13. DATABASE OBJECTS

| # | Object | Verified | Method | Verdict |
|---|---|---|---|---|
| 13.1 | `chk_settlement_conserved` | present | SCHEMA | PASS |
| 13.2 | `chk_ls_split_conserved` | present | SCHEMA | PASS |
| 13.3 | `chk_ls_active_has_period` | present | SCHEMA | PASS |
| 13.4 | `uq_settlement_reference_reversal` | present | SCHEMA | PASS |
| 13.5 | `library_subscriptions_user_library_idx` | present | SCHEMA | PASS |
| 13.6 | `UNIQUE(library_id,user_id)` on `library_ratings` | present | SCHEMA | PASS |
| 13.7 | `trg_subscription_transition_guard` | present and **firing** | EXEC | PASS |
| 13.8 | `trg_submission_transitions_append_only` | present | SCHEMA | PASS |
| 13.9 | `trg_evidence_append_only` | present | SCHEMA | PASS |
| 13.10 | All marketplace identity columns are `uuid` | yes | SCHEMA | PASS |
| 13.11 | RLS enabled on all 7 marketplace tables | yes | SCHEMA | PASS |
| 13.12 | `FORCE ROW LEVEL SECURITY` | **absent everywhere** | SCHEMA | AMBER (deliberate) |
| 13.13 | FK `ON DELETE` actions chosen per table | yes (CASCADE / RESTRICT / SET NULL) | SCHEMA | PASS |
| 13.14 | Ledger rows protected from deletion | `ON DELETE RESTRICT` | SCHEMA | PASS |
| 13.15 | `library_subscriptions.updated_at` exists | yes (post-fix) | SCHEMA + EXEC | PASS |
| 13.16 | **`library_strategies` transition guard** | **absent** | SCHEMA | **FAIL — BUG-057** |

---

## 14. TALLY

| Verdict | Count |
|---|---|
| PASS | 97 |
| FAIL | 6 (all BUG-057 facets: 1.6, 2.13, 4.9, 13.16 — plus 9.4/9.6 which belong to plan billing) |
| AMBER | 3 (3.11, 8.10, 13.12) |
| BLOCKED | 4 (1.15, 3.15, 12.8, plus 7.11/12.7 as untested) |
| N/A | 18 |

**The FAIL rows are not independent.** 1.6, 2.13, 4.9 and 13.16 are four observable facets of the single
defect BUG-057 — one missing control (no `WITH CHECK`, no guard trigger, broad grant) producing four distinct
failures. 9.4 and 9.6 are the referral double-commission defect, which is a money-flow finding listed here only
for completeness.

**Interpretation.** The marketplace passes 97 of 110 applicable checks, including every money-conservation
check, every tenant-isolation check, every enumeration check and the entitlement self-grant check — and the one
P0 that existed (BUG-056) is fixed and regression-tested. It fails on a single, specific, reproduced control:
listing moderation integrity at the database layer.

```
MARKETPLACE: NO-GO  — on BUG-057 alone
```
