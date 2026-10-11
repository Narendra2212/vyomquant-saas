# Phase 11B-U7 — CD-8 Settlement Crash-Recovery Verification

**Scope:** read-only investigation of the proposed CD-8 defect (marketplace settlement partial
completion), establishment of the exact recovery failure boundary, a regression-test plan against
existing seams, and a ranked remediation comparison.

**Status of this document:** evidence and design options only. No source, migration, schema, role,
grant, register or baseline was modified. No database or Redis connection was made. No test was
executed. Nothing was staged, committed or pushed.

**Carried forward unchanged:** TENANT-01 OPEN · QUOTA-01 OPEN · PostgreSQL enforcement UNEXECUTED ·
live trading OFF / NOT CERTIFIED. CD-7's live `billing_invoices` schema uncertainty continues to
block any billing-invoice uniqueness design, and nothing below proposes one.

---

## 1. Repository baseline and preservation evidence

### 1.1 Entry baseline, measured

| Invariant | U4–U6 recorded value | U7 entry (measured) | Verdict |
|---|---|---|---|
| HEAD | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | identical |
| branch | `main` | `main` | identical |
| upstream divergence | 0 ahead / 0 behind | 0 ahead / 0 behind | identical |
| `git write-tree` | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` | identical |
| normalized staged digest | `65dfc45367ced6f67573f3ae485a1c92d63c803b4d881b6a186afd3d54a5bd0c` | `65dfc45367ced6f67573f3ae485a1c92d63c803b4d881b6a186afd3d54a5bd0c` | identical |
| staged tracked paths | 22 | 22 | identical |
| unstaged tracked paths | 100 | 100 | identical |
| unmerged entries | — | **0** | no conflict state |
| untracked paths | 3401 | 3428 | drift from concurrent sessions |

Digest method (the repository's established one, unchanged):
`sha256( raw bytes of 'git diff --cached' with b'\r\n' replaced by b'\n' )`.
Measured input length 241,804 bytes raw and 241,804 normalized, i.e. the staged diff is already
LF-only, so the normalization is a no-op on this content and the digest is reproducible on any
platform.

`git write-tree` is the authoritative staged-content invariant. The raw `.git/index` SHA-256 is
**not** used: it embeds mtimes and cache-tree state that other sessions perturb without changing
staged content. The superseded PowerShell `Out-String` hash `814B8AB272E2C845316D29C90BFE4822`
remains superseded.

### 1.2 Untracked drift

3401 → 3428 (+27) between the end of U4–U6 and the start of U7. This session created nothing during
investigation, so all 27 are from concurrent workstreams. Same drift pattern as the whole phase
(3274 → 3385 → 3401 → 3428).

### 1.3 Preservation actions taken

* No `git add`, `checkout`, `reset`, `clean`, `stash`, `commit` or `push` was issued.
* No existing report or evidence artifact was opened for writing.
* Destination availability was checked before authoring: `staging/PHASE11B_U7*`, `staging/*CD8*`
  and `staging/*CRASH*` all returned empty, so
  `staging/PHASE11B_U7_CD8_CRASH_RECOVERY_READINESS.md` is a new path and no session-specific
  fallback name was needed.
* Ownership of every file a remediation would touch was checked read-only (§5.4).

---

## 2. The verified settlement state machine

### 2.1 Entry point and call graph

`settle()` is a module-level keyword-only coroutine — there is no class — at
`backend_app/backend/marketplace/settlement_service.py:688-704`. It takes the Persistence_Layer
handle as an explicit `supabase=` argument, so there is no client getter to patch.

Production funnel, single path:
`routers/billing.py` webhook → `_apply_billing_entitlement` (`billing.py:1298`, dispatching on
`item_key.startswith("marketplace_")`) → `_apply_marketplace_entitlement` (`billing.py:956`) → one
`settlement_service.settle(...)` call (`billing.py:1037-1046`).

`settle()` (`:756-825`) runs `_settle_once` (`:849-1036`) under the Requirement 10.11 retry budget:
`MAX_SETTLEMENT_ATTEMPTS = 5` (`:321`), `SETTLEMENT_RETRY_WINDOW_SECONDS = 60` (`:325`), with the
window checked *before* each backoff (`:816-825`) so an attempt that cannot finish inside 60s is
not started.

### 2.2 The six steps, with their guards

| # | Step | Code | Table / effect | Idempotency guard |
|---|---|---|---|---|
| 1 | correlating read | `_read_subscription` `:1046-1069`, called `:877` | SELECT `library_subscriptions` | none — read only. `None` ⇒ `UNMATCHED` (`:884-897`), writes nothing. Amount/currency disagreement ⇒ `MISMATCHED` (`:901-923`), writes nothing |
| 2 | ledger insert | `_insert_settlement` `:1343-1416`, called `:929` | INSERT `marketplace_settlements` | **`uq_settlement_reference_reversal`** on `(provider_reference, is_reversal)`, SQLSTATE `23505` |
| 3a | transition | `_apply_transition` `:1546-1560` | UPDATE `library_subscriptions` (status, period_start, period_expiry, started_at, expires_at, cancelled_at) | optimistic `.eq("status", from_status)`; zero rows matched ⇒ `SettlementPersistenceError` (`:1568-1572`) |
| 3b | history | `_write_transition_row` `:1591-1634`, called `:1578` | INSERT `library_subscription_transitions` | none |
| 4 | entitlement | `grant_deployment_permission` `:564-631`, called `:985-1002` | INSERT `deployment_permissions` | **`uq_deployment_permissions`** on `(user_id, library_id, is_active)` — *present in schema, **not** handled in code* (§3.5) |
| 5 | audit | `_audit_settlement_created` `:1005-1016` | Audit_Log, via `record_or_raise` (required) | none; a failure here is retried |

Write order is asserted by
`tests/test_settlement_service.py::test_the_write_order_records_the_money_before_granting_anything`
(`:893-918`): read < ledger < transition < history < grant < audit.

### 2.3 No transaction spans the steps

Confirmed: zero `commit()` / `rollback()` / `BEGIN` / `FOR UPDATE` / `RETURNING` anywhere on this
path. Persistence is PostgREST, which exposes no transaction handle. The codebase states this
itself in three places —
`tests/test_settlement_service.py:898-906`, `:960-967`, and the
`tests/test_marketplace_concurrency.py` battery header (`:1507-1530`, "There is **no transaction
over PostgREST** in this codebase"). Each of steps 2, 3a, 3b and 4 therefore **commits
independently**.

This is a divergence from the design, which specifies atomicity:
`.kiro/specs/marketplace-subscriptions-paper-trading/design.md:180-182` shows
`TX BEGIN … TX COMMIT (all or none)` across all five writes, and `:1411-1415` shows
`COMMIT; RETURN` on the duplicate branch. The implementation's own docstring at
`settlement_service.py:898-906` acknowledges the gap and records the ordering as what it provides
instead. **The design's atomicity was never implementable over PostgREST; the ordering is what
shipped.** That is the root of CD-8.

### 2.4 In-process resumption, and its boundary

`_Attempt` (`:634-686`) records per-step completion in **process memory only** —
`__slots__` at `:648-661`, constructed fresh at `:774` inside each `settle()` call. Nothing is
persisted and nothing is read back from the database.

`insert_issued` (`:665`) is the single discriminator between the two readings of a `23505`
(`:1370-1380`): captured as `resumed` at `:1388` *before* being set at `:1389`.

* `resumed = True` — a prior attempt **of this same call** committed the row ⇒ mark recorded,
  return `False`, continue to step 3.
* `resumed = False` — read as "another delivery got there first" ⇒ return `True` ⇒ caller audits
  `MARKETPLACE_SETTLEMENT_DUPLICATE_IGNORED` and **returns at `:953-971`, before step 3**.

`status_moved` (`:675`) is tracked separately from `transition_written` (`:676`) precisely because
3a and 3b fail independently; the comment at `:668-674` explains that re-issuing a landed
status-guarded UPDATE would match zero rows and burn the whole retry budget at a step that already
succeeded. **That reasoning is correct and complete for one call, and has no cross-process
analogue.**

### 2.5 Crash-window table

A "crash" is any loss of the process between two independently-committed writes: SIGKILL, container
eviction, worker recycle, host failure, or an uncaught exception outside the handled types. A
provider redelivery then arrives at a **fresh process with a fresh `_Attempt`**.

| Window | Committed before loss | What a redelivery does | End state | Recoverable? |
|---|---|---|---|---|
| W0 — before step 2 | nothing | step 2 inserts; 3a, 3b, 4, 5 run | correct | **yes** |
| W1 — after 2, before 3a | ledger row | `23505`, `resumed=False` ⇒ `DUPLICATE_IGNORED` at `:953` | paid; `status` still `pending`; no history; **no entitlement** | **no** |
| W2 — after 3a, before 3b | ledger + status/period | same | paid; `active` **with** period; **no history row**; **no entitlement** | **no** |
| W3 — after 3b, before 4 | ledger + status + history | same | paid; `active` with period; history present; **no entitlement** | **no** |
| W4 — after 4, before 5 | all four rows | same | rows correct; `MARKETPLACE_SETTLEMENT_CREATED` audit **missing** | no, but harm is limited to the audit trail |
| W5 — all steps done | everything | same | correct and idempotent | **yes** — this is the only case the test suite covers |

W2 is the most dangerous for an operator, not W1: `library_subscriptions.status = 'active'` with a
valid `period_expiry` makes the subscription row look entirely healthy, while
`deployment_permissions` holds no row — so the purchaser is billed, the UI shows an active
subscription, and deployment is refused. There is also no history row, which is the one artifact
Requirement 11.12 would have made the discrepancy visible through.

### 2.6 Answers to the nine questions posed

1. **What uniquely identifies a settlement and its reversal?** The pair
   `(provider_reference, is_reversal)`. A payment is `(ref, FALSE)`; its refund is `(ref, TRUE)` and
   additionally carries `reverses_reference` (defaulted to the payment's own reference at
   `settle()`'s `reversed_reference` resolution). Both halves are required:
   `chk_settlement_reversal_reference` (`008:671-673`) forbids a non-reversal carrying
   `reverses_reference` and forbids a reversal without one.
2. **Is `uq_settlement_reference_reversal` actually defined in tracked files?** **Yes.** Declared
   inline at `backend_app/migrations/008_marketplace_settlement.sql:673` as
   `CONSTRAINT uq_settlement_reference_reversal UNIQUE (provider_reference, is_reversal)`, with an
   idempotent re-assertion block at `008:872-885` and a postflight existence check at `008:2154`.
   This **closes the first of U6's two unresolved items at the migration level.** Live presence
   remains unverified (§6).
3. **Which steps commit independently?** All of 2, 3a, 3b and 4. See §2.3.
4. **What happens on exit after each persisted step?** §2.5, W0–W5.
5. **Can a fresh `settle()` distinguish its own prior settlement from an independent duplicate?**
   **No.** The only discriminator is `_Attempt.insert_issued`, which is process-local (§2.4).
   Nothing is read from the database to make the distinction.
6. **Does the duplicate path verify that the transition and grant completed?** **No.** It returns
   at `:953-971` before step 3 is reached. The concurrency suite documents this behaviour
   explicitly at `tests/test_marketplace_concurrency.py:1472-1480` ("the duplicate branch returns
   BEFORE the transition").
7. **Is there any reconciliation mechanism?** **No.** See §3.4.
8. **Can the webhook return HTTP 200 while a paid subscription remains inactive?** **Yes.** See
   §3.3.
9. **Are reversals, renewals and concurrent deliveries affected differently?** Yes. See §3.6.

---

## 3. Evidence for and against CD-8

### 3.1 Verdict

**CD-8 is confirmed as a code-path defect by static evidence alone.** Every link in the chain is
read directly from tracked source; none requires a live-database assumption. One instance of
exactly this window has already been realized against a real database and is documented in the
repository (§3.2).

### 3.2 The already-realized instance — BUG-056

`backend_app/migrations/022_fix_library_subscriptions_updated_at.sql:1-80` records that
`trg_library_subscriptions_updated_at` (from
`archived_migrations/root_migrations/007_create_library_subscriptions.sql:30-39`) unconditionally
assigned `NEW.updated_at` on a table that never declared the column, raising
`42703 record "new" has no field "updated_at"` on **every** UPDATE to `library_subscriptions`.
022's header states this was reproduced against a provisioned database: seed a pending
subscription, seed a real non-reversal settlement row, issue the UPDATE `settle()` issues
(`:1549-1553` in 022's line reference) — `42703`, deterministically.

That is window **W1**, triggered deterministically rather than by chance. 022's own disposition
reads:

> a complete, loud, reconcilable outage of entitlement activation, not a silent loss of money

**That conclusion is correct for the first delivery and incorrect for every delivery after it.**
Traced through the code:

1. Delivery 1: step 2 commits. Step 3a raises `42703`, converted to
   `SettlementPersistenceError` at `:1565-1567`.
2. `settle()` retries. Each retry *resumes* — `settlement_recorded` is `True`, so step 2 is
   skipped (`:927`) and 3a is re-attempted. It fails identically, being deterministic. The 5-attempt
   budget is exhausted ⇒ `SettlementPersistFailed`.
3. `billing.py:1047-1055` catches it and raises `HTTPException(500)`. This is the loud failure 022
   describes.
4. The provider redelivers on its own schedule, into a **fresh process**. Fresh `_Attempt`.
   `settlement_recorded = False` ⇒ step 2 runs ⇒ `23505` ⇒ `resumed = False` ⇒
   `DUPLICATE_IGNORED` ⇒ early return at `:953`.
5. `billing.py:1075-1091`: `DUPLICATE_IGNORED` is not in the `(UNMATCHED, MISMATCHED)` branch and
   raises nothing. Control falls to `logger.info` at `:1086` and the function returns `None`.
   **The webhook answers 200.**

So the loud 500 converts to a silent 200 on the provider's first retry, and the provider stops
retrying. 022 did not account for the redelivery path. This is recorded here as a **correction to
022's disposition**, not as a defect in 022's fix — the fix itself (adding the column) is correct
and the generic trigger backstop at `022:117-157` is a genuine improvement.

A consequence 022 also did not address: it states "`library_subscriptions` has no rows on a
correctly-functioning deployment of this defect (every write failed), so there is nothing to
backfill." That reasoning covers the added column. It does not cover `marketplace_settlements`,
whose rows **did** land while the defect was live. Whether any such settled-but-never-activated
rows exist in the live database is a data question, unverified, and is included in the §6 query
list.

### 3.3 The webhook answers 200 on an unrecovered settlement

Confirmed by reading the outcome handling. `billing.py:1075-1091` branches only on `UNMATCHED` and
`MISMATCHED`; `DUPLICATE_IGNORED` falls through to the `logger.info` at `:1086` and returns
normally. The docstring at `billing.py:998-1000` states the intent plainly:

> `RECORDED` / `DUPLICATE_IGNORED` — success. A redelivery is a no-op by construction, so the
> webhook is idempotent whether or not the Redis lock survived

"A no-op by construction" is true when the first delivery completed all six steps. It is false in
W1–W4, and the response code does not distinguish the two.

### 3.4 No backstop exists

* **No reconciliation for settlements.** A repository-wide search for
  `reconcil|incomplete_settlement|settled_but|repair_settlement|settlement_sweep|orphan` across
  `backend_app/**/*.py` returns only trading/position machinery:
  `core/reconciliation_scheduler.py` (started at `main.py:456`), `core/models/reconciliation.py`,
  `core/consistency_checker.py` (exchange-position drift), `core/crash_recovery.py` (deployment
  state and open orders), `tee/main.py:209` (`reconciler_loop`, adopts orphaned strategies). **None
  reads `marketplace_settlements`.** The only mention of that table anywhere in `backend_app/`
  outside the settlement module is a prose comment at `billing.py:985`.
* **No reconciliation table, job table or outbox** is declared in any tracked migration for
  settlements.
* **The expiry sweep cannot repair it, by construction.**
  `backend_app/backend/marketplace/expiry_sweep.py` writes exactly two columns —
  `{"status": EXPIRED_STATUS_TEXT, "updated_at": …}` (`:949-952`) — and
  `_FORBIDDEN_UPDATE_COLUMNS` (`:323-326`) asserts at runtime that it never writes
  `period_expiry`, `period_start`, `expires_at`, `renewal_enabled`, `user_id` or `library_id`. It
  moves rows *out* of `active`; it has no path into it.
* **The only operator trace is one audit row.** `MARKETPLACE_SETTLEMENT_PERSIST_FAILED`
  (`audit_trail.py:721`, written by `_audit_persist_failed` at `:1909`) carries `provider_reference`
  and a `settlement_recorded` flag, and `SettlementPersistFailed`'s own docstring (`:528-533`)
  directs the reader to those two fields "an operator reconciling by hand needs to know which one
  happened." Reconciling by hand is the entire recovery mechanism, and it is only reachable if
  someone reads the audit log. In W1–W4 the redelivery that follows writes
  `MARKETPLACE_SETTLEMENT_DUPLICATE_IGNORED` — indistinguishable from the benign W5 case.

### 3.5 A constraint that blocks the naive fix

`uq_deployment_permissions UNIQUE (user_id, library_id, is_active)` exists at
`migrations/006_reconcile_production_database.sql:112`. `grant_deployment_permission`
(`:564-631`) does **not** classify a `23505` — `:625-631` converts every exception uniformly into
`SettlementPersistenceError`. So a recovery design that blindly re-runs step 4 for an entitlement
that already landed will fail, be retried five times, and surface as `SettlementPersistFailed`.
Any remediation must handle entitlement-grant duplication explicitly. This is a real design
constraint, discovered here, and it is why "just resume all steps on a duplicate" is not
sufficient on its own.

### 3.6 Reversal, renewal and concurrency differ

| Path | Behaviour under crash | Direction of harm | Detectability |
|---|---|---|---|
| **Payment** (`pending`/`expired`/`cancelled`/`suspended`/`payment_failed` → `active`) | W1–W3 as tabulated | purchaser is billed and under-served | W1 detectable (`pending` with a settlement); W2/W3 look healthy on the subscription row |
| **Reversal** (`→ refunded`) | `(ref, TRUE)` is a distinct key, so the reversal insert is not blocked by the payment row. A crash after it commits leaves `status` un-moved; redelivery ⇒ `DUPLICATE_IGNORED` | **inverted** — money went back, the subscriber keeps the entitlement, the owner's earning is already reversed in the ledger | ledger shows a reversal with no `active → refunded` history row |
| **Renewal** (`active → active`) | `trg_subscription_transition_guard` branch 1 (`008:1839-1841`, `IS NOT DISTINCT FROM`) returns early for same-value writes, so the **database payment gate does not apply to renewals at all** — `008:1746-1757` states this is deliberate. A crash after the ledger insert leaves the period unextended; redelivery ⇒ `DUPLICATE_IGNORED` | purchaser paid for a month never granted | **worst** — the subscription is genuinely `active`, so no status looks wrong anywhere; only a ledger-vs-`period_expiry` comparison reveals it |
| **Concurrent deliveries** | **already correct.** The losing writer's insert hits `23505` with `resumed=False` ⇒ `DUPLICATE_IGNORED`, while the winner completes steps 3–5. Arbitrated further by the optimistic `.eq("status", from_status)` | none | covered by `TestDuplicateWebhookDeliveryBattery` and P-6 |

Concurrency is explicitly **not** the defect. CD-8 requires the winner to *die*, which no existing
test simulates.

### 3.7 Separating confirmed from unverified

**Confirmed from tracked source, no database required:**

* The six-step order and the absence of any spanning transaction (§2.2, §2.3).
* `_Attempt` is process-local; `insert_issued` is the only discriminator (§2.4).
* The `DUPLICATE_IGNORED` early return precedes steps 3–5 (`:953-971`).
* `billing.py` answers 200 for `DUPLICATE_IGNORED` (§3.3).
* `uq_settlement_reference_reversal` is declared in a tracked migration (`008:673`).
* `uq_deployment_permissions` is declared in a tracked migration (`006:112`) and is unhandled in
  code (§3.5).
* No settlement reconciliation exists anywhere in the tree (§3.4).
* `trg_settlements_append_only` + `REVOKE UPDATE, DELETE` make the ledger immutable (§5.1).
* The design specified atomicity that PostgREST cannot provide (§2.3).

**Test-double behaviour, not production behaviour:** `FakeSupabase`
(`tests/test_settlement_service.py:266-383`) enforces `uq_settlement_reference_reversal` at
`:332-346` and `_MoneyPathSupabase` at `:841-853`. Neither models `uq_deployment_permissions`,
`chk_settlement_conserved`, `chk_ls_active_has_period`, `trg_subscription_transition_guard` or
`trg_settlements_append_only`. BUG-056 is the standing proof that this gap hides real defects —
022's header says so in as many words: "every layer that could have caught this never touched a
real trigger."

**Unverified against the live database (requires D7):**

* Whether `marketplace_settlements` live columns match `_settlement_payload`'s 13 keys.
* Whether `uq_settlement_reference_reversal` is actually present live. Its absence would convert
  step 2 into unbounded duplicate ledger rows — a different and worse defect than CD-8.
* Whether `deployment_permissions` live shape follows `006` (tracked) or
  `archived_migrations/root_migrations/010_create_deployment_permissions.sql` (untracked, declares
  **no** `created_at`/`updated_at` yet attaches `trg_deployment_permissions_updated_at` assigning
  `NEW.updated_at` at `:22-33` — structurally identical to BUG-056, with no fix migration).
* Whether any settled-but-never-activated rows exist from the BUG-056 window.

### 3.8 Classification

CD-8 is **not** CD-7: CD-7 is a schema disagreement on `billing_invoices` whose columns are
unconfirmed; CD-8 is a control-flow defect on `marketplace_settlements`, whose constraint is
demonstrably declared. CD-8 is **not** CD-5: CD-5 is Redis-only webhook idempotency at the event
level; CD-8 is below it, in the settlement body, and survives a working Redis lock. CD-8 is **not**
a concurrency defect (§3.6). It remains **PROPOSED**; acceptance into the register is D8.

---

## 4. Regression test plan

### 4.1 Existing seams (all present; nothing new required)

| Need | Existing seam |
|---|---|
| plant an already-committed ledger row | `FakeSupabase(settlements=[...])` — kwarg at `tests/test_settlement_service.py:272,277`, **currently unused by any test**. This is the exact seam CD-8 needs |
| simulate process death | `_settle(...)` (`:501-533`) constructs a fresh `_Attempt` per call; a second `_settle` against a pre-populated double *is* a fresh process |
| kill a specific step mid-flight | wrap `client._execute`, pattern established at `:965-981` |
| fail once then succeed | `fail_times={("insert", ss.TRANSITION_TABLE): 1}` (`:296`) |
| fail always | `raise_on={("insert", ss.SETTLEMENT_TABLE)}` (`:292`) |
| PostgREST error envelope | `error_on={...}` (`:294`) |
| observe audit outcomes | `audit` fixture `:485-499`; `action_names()`, `metadata_for()` |
| entitlement: count **and** persisted row | `_CountingGrant` (`tests/property/test_settlement_idempotence.py:205-222`) — counts, then delegates to the real writer |
| end-state oracles | `client.subscription()`, `client.transitions`, `client.permissions`, `client.settlements`, `client.statements_on(table, *ops)` |
| webhook response code | `_deliver_confirmations` (`tests/test_marketplace_concurrency.py:1147-1183`) drives the real `billing._apply_billing_entitlement` funnel with `_MoneyPathSupabase` |
| no-sleep retries | `no_sleep` fixture `:535-556` |

No skip markers, no xfail, no DSN gating exists on any of these four suites; everything runs against
in-memory doubles.

### 4.2 Exact coverage gaps

1. **No test plants a ledger row and re-invokes `settle()`.** The `settlements=` kwarg has existed
   since the double was written and is called by nothing. Every existing redelivery test
   (`:779-822`, `:825-858`, `:1829-1853`, P-6's `_apply`, the webhook battery) runs delivery 1 to
   *full completion* before redelivering — i.e. all of them test W5 and none test W1–W4.
2. **The closest existing test stops one step short.**
   `test_a_step_after_the_ledger_row_that_never_completes_grants_no_entitlement` (`:993-1025`)
   establishes exactly the W3 state and asserts the single-call boundary honestly — and then
   **never redelivers**. Its docstring says the audit "records which steps DID land so an operator
   can reconcile by the provider reference"; no test asserts that such reconciliation exists, and
   §3.4 shows it does not.
3. **`test_a_transient_failure_is_resumed_rather_than_restarted` (`:1027-1051`) tests resumption
   *within* one call only.** Its docstring names the exact failure CD-8 realizes —
   "a paid-but-no-access defect worse than the failure it recovered from" — and the mechanism it
   validates (`_Attempt`) is the one that does not survive process loss.
4. **Unit duplicate tests verify the entitlement by injected-call count, not by row.**
   `len(grant.calls) == 1` cannot distinguish "granted once on delivery 1" from "granted zero times
   because delivery 1 died and delivery 2 short-circuited". Only
   `tests/property/test_settlement_idempotence.py` (via `_CountingGrant`) and the
   `_MoneyPathSupabase` webhook battery assert the persisted `deployment_permissions` row.
5. **No test asserts the HTTP status of a redelivery-after-crash** (§3.3).
6. **The doubles model one constraint.** Validating any design that leans on
   `uq_deployment_permissions` (§3.5) requires extending `FakeSupabase` — an addition to the double,
   not a weakening of any assertion.

### 4.3 Proposed cases

Design rule for all of them: **a passing test must assert the final subscription status and period,
the history row, the persisted `deployment_permissions` row, and the webhook outcome — not merely
that no second ledger row appeared.** Each case also needs a control proving the oracle is not
vacuous (the pattern P-6 uses at its Claim 7).

| ID | Case | Setup | Must assert |
|---|---|---|---|
| T1 | W1 — crash after ledger, before transition | `FakeSupabase(subscriptions=[pending], settlements=[planted row])`; one `_settle` | **current behaviour**: `DUPLICATE_IGNORED`, `status == "pending"`, `transitions == []`, `permissions == []`. This is the characterization test that *documents the defect* before any fix |
| T2 | W2 — crash after transition, before history | plant ledger row **and** pre-set subscription to `active` with period; `transitions == []` | `DUPLICATE_IGNORED`; history still absent; `permissions == []` |
| T3 | W3 — crash after history, before grant | plant ledger + active row + one transition row | `DUPLICATE_IGNORED`; `permissions == []` |
| T4 | W4 — crash after grant, before audit | plant all four rows | `DUPLICATE_IGNORED`; all four rows intact and unduplicated; records that only the audit line is missing |
| T5 | W5 control — genuine duplicate | the existing full-completion flow, redelivered | unchanged from today: one of each row. **Must keep passing identically under any fix** — this is the regression guard that a recovery design does not re-grant or re-extend |
| T6 | two concurrent deliveries | `asyncio.gather` over `_deliver_confirmations` (existing battery) | unchanged: one ledger row, one period extension, one entitlement, n−1 duplicate audits |
| T7 | reversal crash | plant `(ref, TRUE)` reversal row with `status` still `active` | `DUPLICATE_IGNORED`; subscription not `refunded`; entitlement still present — the inverted-harm case |
| T8 | renewal crash | `active` row with stored expiry + planted ledger row, `period_expiry` un-extended | `DUPLICATE_IGNORED`; expiry unmoved — the undetectable case |
| T9 | failure *during* recovery, then success | plant ledger row; `fail_times={("insert", TRANSITION_TABLE): 1}`; `no_sleep` | after a fix: first recovery attempt fails, second completes; exactly one of each row; `no_sleep == [1]` |
| T10 | entitlement already granted | plant all rows **including** the permission row, with the history row missing | after a fix: recovery completes the history row and does **not** attempt a duplicate grant, or tolerates its `23505` — the §3.5 constraint, asserted |
| T11 | webhook response code | `_deliver_confirmations` against a pre-planted `_MoneyPathSupabase` | records that the W1 redelivery currently answers 200 with an inactive subscription |
| T12 | optimistic-lock interaction | concurrent recovery and a competing writer moving `status` | the `.eq("status", …)` guard still arbitrates; no double extension |

T1–T4, T7, T8 and T11 are **characterization tests of current behaviour** and can be committed
before any fix is chosen — they are the executable statement of CD-8. T9, T10 and T12 only become
meaningful once a design is selected. T5 and T6 are existing behaviour that must not move.

No existing test is to be deleted, no assertion weakened, no baseline updated. T5/T6 reuse the
existing suites unchanged.

---

## 5. Remediation alternatives, compared

### 5.1 What the schema forecloses

Before comparing designs: **a mutable progress/status column on `marketplace_settlements` is
architecturally excluded.**

* `trg_settlements_append_only` (`008:1668-1715`) raises `23514` on every UPDATE and on every
  DELETE whose parent subscription still exists.
* `REVOKE UPDATE, DELETE ON public.marketplace_settlements FROM anon, authenticated, service_role`
  (`008:2192`) — the **service role has no UPDATE privilege**.
* Grants are `SELECT, INSERT` only (`008:2186`).
* The table comment (`008:2205-2224`) states append-only as a Requirement 10.8 invariant.
* A column survey confirms no `status`, `state`, `completed_at`, `applied_at`, `processed_at`,
  `reconciled_at` or `consumed_at` column exists; the only temporal columns are `settled_at`,
  `created_at`, `updated_at`, and `updated_at` is unwritable for the same reason.

So the brief's instruction not to assume a new status column is necessary is well-founded: on this
table, one would be actively wrong. Any durable-progress design must derive completion from the
*other three tables' rows*, or live outside the ledger.

### 5.2 Option A — durable recovery state derived from existing rows

**Mechanism.** On a `23505` with `resumed == False`, stop returning immediately. Instead re-read
what exists and resume the missing steps:

1. re-read `library_subscriptions` (step 1's read already exists);
2. SELECT `library_subscription_transitions` for this subscription;
3. SELECT `deployment_permissions` for `(user_id, library_id, is_active=TRUE)`;
4. if all present and consistent ⇒ the W5 case ⇒ `DUPLICATE_IGNORED` exactly as today;
5. otherwise ⇒ resume at the first missing step, returning a **new** outcome (e.g. `RESUMED`).

**Minimal changes.** `settlement_service.py` only: `_insert_settlement`'s duplicate branch, a new
completion probe, one new `SettlementOutcome` member, and duplicate-tolerance in step 4.
**No schema change. No migration. No `billing.py` change** — a new outcome value is not in
`billing.py:1075-1078`'s `(UNMATCHED, MISMATCHED)` tuple, so it falls through to the existing
`logger.info` and still answers 200, which is correct once the work has actually been completed.

**Reads needed.** Both new SELECTs are indexed and granted: `idx_lib_sub_transitions` on
`(subscription_id, transitioned_at)` (`008:1053-1054`) and
`idx_deploy_perm_user (user_id) WHERE is_active = TRUE` (`006:115`);
`GRANT SELECT` exists for both. Cost falls only on the duplicate path.

**Concurrency.** The dangerous interleaving is a redelivery arriving while the *original* delivery
is still mid-flight — the loser would observe "transition missing" and attempt it. The existing
optimistic `.eq("status", from_status)` arbitrates: one of the two matches zero rows and raises
`SettlementPersistenceError`, which is retried. Correct, but it converts an instant no-op into a
retry, so the probe must re-read on each attempt rather than cache. This is the main correctness
risk of Option A and T12's purpose.

**Idempotency.** Requires step 4 to treat `uq_deployment_permissions`' `23505` as "already
granted" (§3.5). That is a deliberate behaviour change to `grant_deployment_permission`, needing
its own test (T10) and an extension to `FakeSupabase` to model the constraint.

**Reversal/renewal.** Compatible. The reversal probe is "is `status` already `refunded`"; the
renewal probe is "is `period_expiry` already at or past what this confirmation would write". Both
are reads of rows that already exist. The renewal case is the one that genuinely needs the ledger
`settled_at` compared against `period_expiry`, which `idx_settlements_subscription`
(`008:925-926`) serves.

**Rollback.** Pure Python, no schema. Revert the file. Recovery from a bad deploy is a redeploy.

**Tests.** T1–T12 above, all against existing doubles plus the one `FakeSupabase` extension. A
real-PostgreSQL test is *desirable* (to prove the probe reads survive real triggers and RLS, which
is precisely the class BUG-056 escaped) but not strictly required for the Python logic.

**Does not cover:** the case where the process never returns and the provider stops retrying. Only
Option B covers that.

### 5.3 Option B — reconciliation of incomplete settlements

**Mechanism.** A periodic read: non-reversal `marketplace_settlements` rows whose subscription is
not `active`, or whose `period_expiry` precedes `settled_at`, or which have no active
`deployment_permissions` row. Each finding is either auto-repaired by re-entering the settlement
body, or surfaced for operator action.

**Minimal changes.** A new module (the natural sibling is
`backend_app/backend/marketplace/`, alongside `expiry_sweep.py`, whose structure —
`SWEEP_CANDIDATE_SELECT`, `_FORBIDDEN_UPDATE_COLUMNS`, a single narrowed write statement — is the
template to copy), plus a scheduler registration in `main.py` mirroring
`start_reconciliation_scheduler` (`main.py:456`). No schema change is strictly required: the
queries are served by `idx_settlements_subscription` and `idx_deploy_perm_user`. A
reconciliation-run table would be an optional addition for observability.

**Owner / authorization.** `main.py` carries 289/36 unstaged from O3, so the scheduler
registration collides with O3's work — D6. The new module itself is uncontested.

**Backfill risk.** The first run against a production database would act on every historical
incomplete settlement at once, including any left by the BUG-056 window. It must therefore start in
report-only mode, and the §6 query list must be run first so the population size is known before
anything is repaired. Repairing blind is the main risk of Option B.

**Concurrency/replay.** Must not race the webhook path. The same optimistic status guard applies;
additionally the sweep should skip rows settled within a grace window (longer than the 60s retry
window) so it never competes with an in-flight `settle()`.

**Reversal/renewal.** The renewal case is the one Option B catches that is otherwise invisible
(§3.6), which is a strong argument for it. Reversal findings should be report-only: auto-moving a
subscription to `refunded` is a revocation of access, and doing that from a sweep on inferred state
is a larger risk than leaving it for an operator.

**Rollback.** Disable the scheduler. Report-only mode is itself the safe first state.

**Tests.** Unit tests over a double; integration against the webhook path to prove no race; and a
real-PostgreSQL test is **required** here, because the candidate query's correctness depends on
real RLS and on the real `deployment_permissions` shape, which §3.7 lists as unverified.

### 5.4 Option C — database-transactional completion

**Mechanism.** A PostgreSQL function performing all four writes, invoked via one PostgREST
`rpc()`. Each PostgREST call runs in its own transaction, so this is genuinely atomic and would
eliminate W1–W4 entirely rather than recovering from them.

**Minimal changes.** A new migration declaring the function; `settlement_service._settle_once`
reduced to one `rpc()` call; `_Attempt`'s resumption machinery largely superseded.

**Owner / authorization.** New schema ⇒ O4 provisioning plus D7 for verification. Highest
authorization cost of the three.

**Specific risks.**
* The function must write `marketplace_settlements`, which `trg_settlements_append_only` permits for
  INSERT — fine — but it must also UPDATE `library_subscriptions`, which fires
  `trg_subscription_transition_guard`. The guard probes `marketplace_settlements` **as the invoking
  role under RLS** and is deliberately **not** `SECURITY DEFINER`; `008:1855-1860` explains that
  making it definer-rights would be a privilege-escalation surface. A `SECURITY DEFINER` settlement
  function reopens exactly that argument, and doing so during an open TENANT-01 would be poor
  sequencing.
* The 90/10 split currently lives in exactly one place (`money.split_ninety_ten`, with
  `chk_settlement_conserved` as the database's independent check). Moving it into SQL creates a
  second implementation of a money rule, or requires passing pre-split values in — the latter is
  preferable and should be stated as a constraint if C is ever chosen.
* The entire existing test suite (1700 + 3161 + 450 + 644 lines) exercises the Python path against
  doubles. An `rpc()` body is untestable by those doubles, so coverage would have to move to
  real-PostgreSQL tests — which is both the cost and, given BUG-056, arguably the benefit.

**Rollback.** Keep the Python path behind a flag for one release; revert the flag. The migration
itself is additive (a function), so reverting is a `DROP FUNCTION`.

**Tests.** Real-PostgreSQL only, for the atomicity claim. Cannot be meaningfully validated without
D7.

### 5.5 Ranking and owner/approval matrix

**Recommended: A now, B next, C as the correct long-term answer gated on D7/O4.**

A is the smallest change that makes recovery durable across process death, needs no schema, and —
importantly for this phase — touches only files no other workstream holds. B is the necessary
complement because A cannot help when the provider stops retrying. C is the only option that closes
the window rather than recovering from it, and is the only one that cannot be assessed at all
without the PostgreSQL lane.

| Option | Files | Schema | Owner | Approval needed | Blast radius |
|---|---|---|---|---|---|
| **A** | `settlement_service.py` (**clean**), `tests/test_settlement_service.py` (**clean**) | none | unowned — no workstream holds either file | **none beyond accepting CD-8 (D8)** | smallest; duplicate path only |
| **B** | new sweep module (uncontested) + `main.py` (**O3, 289/36 unstaged**) | none required | O3 for the registration | **D6** | medium; first production run needs §6 data first |
| **C** | new migration + `settlement_service.py` | **new DB function** | O4 provisioning, O5 for the lane | **D7 + O4**, and re-litigates the `SECURITY DEFINER` decision at `008:1855` | largest |

Ownership was verified read-only this session:

| Path | Working-tree status |
|---|---|
| `backend_app/backend/marketplace/settlement_service.py` | **clean** |
| `tests/test_settlement_service.py` | **clean** |
| `tests/test_marketplace_concurrency.py` | **clean** |
| `tests/property/test_settlement_idempotence.py` | **clean** |
| `backend_app/migrations/008_marketplace_settlement.sql` | **clean** |
| `backend_app/backend/marketplace/expiry_sweep.py` | **clean** |
| `backend_app/routers/billing.py` | ` M` unstaged — **O3** |
| `migrations/006_reconcile_production_database.sql` | ` M` unstaged |

That `settlement_service.py` is clean is the single most useful scheduling fact in this report:
Option A can proceed without waiting on D1 or D6, which is not true of any other open remediation
in this phase.

---

## 6. Read-only PostgreSQL verification plan

**Not executed.** No connection was made, the shared cluster was neither started nor stopped, and
`staging/pg_dsn.txt` was not read or rewritten.

**Authorization required:** D7 — O5 owns the shared cluster at `staging/pgdata_r1` (port 51805).
O4 owns provisioning and must confirm which migration set the target database was built from before
any answer is accepted as production truth. Every query below is `SELECT`-only against catalogue
views; none takes a lock beyond a catalogue read.

**Target scoping.** Results are only meaningful if the database's provenance is recorded alongside
them. Q0 exists for that reason.

| ID | Question | Query shape |
|---|---|---|
| Q0 | provenance | `SELECT current_database(), current_user, version();` plus whatever migration-ledger table the target carries |
| Q1 | column set of all four tables | `SELECT table_name, column_name, data_type, is_nullable, column_default FROM information_schema.columns WHERE table_schema='public' AND table_name IN ('marketplace_settlements','library_subscriptions','library_subscription_transitions','deployment_permissions') ORDER BY table_name, ordinal_position;` |
| Q2 | **does `uq_settlement_reference_reversal` exist, on which columns** | `SELECT conname, contype, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='public.marketplace_settlements'::regclass;` |
| Q3 | all unique constraints/indexes on the four tables | `pg_constraint` where `contype IN ('u','p')` + `pg_indexes` where `indexdef ILIKE '%UNIQUE%'`, for all four |
| Q4 | **does `uq_deployment_permissions` exist** (§3.5 depends on it) | `SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='public.deployment_permissions'::regclass;` |
| Q5 | **does `deployment_permissions` have `updated_at`** (the BUG-056-shaped risk, §3.7) | column presence from Q1 + `SELECT tgname, pg_get_functiondef(tgfoid) FROM pg_trigger WHERE tgrelid='public.deployment_permissions'::regclass AND NOT tgisinternal;` |
| Q6 | triggers on all four tables, with bodies | `pg_trigger` joined to `pg_class`/`pg_proc`, `NOT tgisinternal`, for all four |
| Q7 | is the ledger really append-only | confirm `trg_settlements_append_only` from Q6 **and** `SELECT grantee, privilege_type FROM information_schema.role_table_grants WHERE table_name='marketplace_settlements';` — expect no UPDATE/DELETE for any role |
| Q8 | is the payment gate the 008 or the 014 body | `SELECT pg_get_functiondef('public.marketplace_subscription_guard'::regproc);` and `to_regprocedure('public.marketplace_subscription_reinstatement_shape(text,text,timestamptz,timestamptz,timestamptz,timestamptz)')` |
| Q9 | RLS state and policies | `SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname IN (…);` + `SELECT * FROM pg_policies WHERE tablename IN (…);` — expect ENABLE without FORCE (consistent with CD-1) |
| Q10 | **CD-8 population: settled but never activated** | non-reversal `marketplace_settlements` LEFT JOIN `library_subscriptions` where `status <> 'active'`, **count only** |
| Q11 | **CD-8 population: settled, active, no entitlement** (the W2/W3 state) | non-reversal settlements LEFT JOIN `deployment_permissions` on `(user_id, library_id, is_active)` where the permission row is absent, **count only** |
| Q12 | **CD-8 population: paid renewal never extended** (the §3.6 invisible renewal case) | settlements where `settled_at > library_subscriptions.period_expiry`, **count only** |
| Q13 | missing history rows | settlements with no `library_subscription_transitions` row at or after `settled_at`, **count only** |

Q10–Q13 are the backfill evidence Option B needs before its first production run, and they are also
the only way to establish whether the BUG-056 window left residue. They must return **counts, not
rows**: the rows contain purchaser identifiers and payment references, and a count answers the
question.

**Evidence required before any live schema claim is accepted:** Q0's provenance output attached to
the result set; the raw text of each answer rather than a summary; O4's confirmation that the target
is the production-equivalent schema; and O5's confirmation that the lane was used read-only. Absent
all four, results stay labelled as lane observations, not production facts — the same standard U4–U6
applied.

---

## 7. Acceptance criteria for closing CD-8

CD-8 may move from PROPOSED to CLOSED only when all of the following hold.

**Evidence**

1. CD-8 accepted into the launch-gate register by the register owner (**D8**), with its scope
   recorded as `marketplace_settlements` partial completion — distinct from CD-5 and CD-7.
2. Q1–Q9 answered from the authorized lane, confirming the constraint set the design relies on.
   In particular: `uq_settlement_reference_reversal` present (or CD-8 is superseded by a worse
   defect), and `uq_deployment_permissions` present (or Option A's step-4 handling must change).
3. Q10–Q13 answered, so the historical population is known rather than assumed.

**Implementation**

4. A redelivery after any of W1, W2, W3 reaches the same end state as an uninterrupted delivery:
   one ledger row, correct `status` and period, one history row, one active
   `deployment_permissions` row.
5. W5 behaviour is bit-identical to today — no second ledger row, no second period extension, no
   second grant. T5 and T6 pass unchanged.
6. Entitlement-grant duplication is handled explicitly (§3.5), not by accident of retry.
7. The reversal and renewal paths are addressed or their residue is explicitly accepted and
   recorded. Silence on the renewal case is not acceptable, because it is the one no status
   anywhere reveals.
8. Either an automated recovery exists (Option A and/or B) or an operator runbook exists naming the
   detection query and the repair procedure. "Reconcile by hand" with no documented procedure, which
   is the current state, does not satisfy this.

**Verification**

9. T1–T12 implemented, executed, and reported with exact counts. **Skips are not passes.**
10. At least one real-PostgreSQL test covers the repaired path, running against the actual triggers
    and RLS. This is non-negotiable for CD-8 specifically: BUG-056 is the standing demonstration
    that this module's double-based suite cannot see defects of this class, and 022's own header
    says so.
11. The full existing settlement suite —
    `tests/test_settlement_service.py`, `tests/test_marketplace_concurrency.py`,
    `tests/property/test_settlement_idempotence.py`, `tests/property/test_settlement_ledger.py` —
    passes with no assertion weakened, no test deleted and no baseline updated.
12. `migrations/022`'s disposition corrected to record that the redelivery converts its 500 into a
    200 (§3.2), so the next reader does not re-derive the same incorrect conclusion.

**Out of scope for closing CD-8**

CD-7's `billing_invoices` uncertainty, TENANT-01's GUC wiring, QUOTA-01, and U4's
`Base.metadata.create_all` question are all independent and remain open. Closing CD-8 does not
touch any of them, and CD-7 continues to block any billing-invoice uniqueness design.

---

## Appendix A — U5/U6 items this phase resolved or carried

| Item | U4–U6 state | U7 outcome |
|---|---|---|
| `uq_settlement_reference_reversal` declared in tracked migrations? | unresolved | **resolved** — `008:673`, re-asserted `008:872-885`, postflight `008:2154`. Live presence still **D7** |
| Do the four tables carry the columns `_settlement_payload` writes? | unresolved | **resolved at migration level** — `008:641-674`, `008:985-999`, `006:56-74`, `006:100-113`. Live shape still **D7**; `deployment_permissions` has a competing untracked declaration (§3.7) |
| Any settlement reconciliation? | asserted none | **confirmed none**, with the mechanism (`expiry_sweep` cannot activate) now established rather than inferred |
| `tenant_rls_validator.py` unreferenced | recorded | **carried unchanged.** Module not deleted, listener not wired |
| `TransactionalExecutionManager.begin()` ordering defect (`SET LOCAL` at `:222` precedes `self.db.begin()` at `:233`) | recorded | **carried unchanged.** Recorded again here against any future fill-listener wiring |

## Appendix B — method

Read-only throughout. Repository searches and file reads were batched in parallel, then
consolidated. Two `context-gatherer` sub-agents ran concurrently: one inventorying test fixtures
and seams across the four settlement suites, one inventorying tracked-migration schema for the four
tables. Their findings were cross-checked against direct reads of
`settlement_service.py` (`:495-635`, `:633-760`, `:769-850`, `:848-983`, `:981-1071`, `:1342-1471`,
`:1518-1653`), `billing.py` (`:956-1131`, `:1296-1346`), `008_marketplace_settlement.sql`
(`:640-724`, `:765-978`, `:985-1049`, `:1141-1204`, `:1423-1611`, `:1668-2142`, `:2149-2246`,
`:2453-2578`), `006_reconcile_production_database.sql:55-187`,
`014_subscription_admin_reinstatement.sql:179-527`,
`022_fix_library_subscriptions_updated_at.sql:1-157`,
`archived_migrations/root_migrations/010_create_deployment_permissions.sql:3-33`, and
`expiry_sweep.py` (`:54-57`, `:312-326`, `:943-979`).

**Limitations.** One PowerShell digest attempt failed because `[System.Text.Encoding]::Latin1`
does not exist on .NET Framework (PowerShell 5.1); the digest was recomputed in Python, which is the
method of record and reproduced the expected value exactly. The schema sub-agent's full output
exceeded the inline size limit and was read from its spill file at
`~/.kiro/sessions/.../tool-outputs/invoke_sub_agent-dea1be0e.txt`; the facts quoted above were
individually re-verified against the migration files by direct read. No test was run: every
question in the brief was resolvable statically.
