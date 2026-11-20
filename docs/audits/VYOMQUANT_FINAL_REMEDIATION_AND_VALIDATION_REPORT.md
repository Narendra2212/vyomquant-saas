# VYOMQUANT — remediation and validation report

Covers the engineering session of 2026-11-16 (second session of the day) against
the master remediation brief. It supersedes nothing: the architecture audit
remains `VYOMQUANT_MASTER_ENGINEERING_AUDIT.md`, and the operational notes remain
`VYOMQUANT_SESSION_HANDOFF.md`.

This report states what was completed with evidence, what was found to be already
correct, and what remains — including the exact blocker for each remaining item.
It deliberately does not describe unfinished work as finished.

---

## 1. Headline

Two tasks closed, one of them by discovering the defect was larger than reported
and the other by discovering it was not a defect at all. `main` was unblocked
after a three-hour CI outage caused by a commit-ordering mistake. Five of the
original seventeen tasks remain.

| | |
|---|---|
| Tasks closed this session | T07, T11 |
| Tasks closed previously | T01, T03, T04, T05, T06, T08, T12, T13, T14a |
| Tasks remaining | T02, T09, T10, T15, T16, T17 |
| PRs merged | #286 |
| PRs open and green-pending | #287 |
| Production data changed | none |
| Live orders placed | none |

---

## 2. `main` was red, and the cause was ordering

**Symptom.** Every PR inherited a failing `Provisioning, Privilege Hardening &
Tenant Isolation` job from 16:13 onward:

```
DRIFT tables              baseline=101   live=102
DRIFT recorded_migrations baseline=99    live=100
RESULT: DRIFTED
```

**Root cause.** Not a stale snapshot in the usual sense — a snapshot that never
arrived:

```
$ git merge-base --is-ancestor 94e3ed75 origin/main ; echo $?
1
```

PR #283 added migration 069 and merged as `9ac3129d`. Its paired re-record of
`staging/provisioning_snapshot.json` was committed *afterwards*, as `94e3ed75`,
onto a branch GitHub had already merged. The commit was orphaned, so `main`
carried the migration with the pre-069 baseline and the gate reported accurately.

**Why this matters beyond the incident.** This is the third occurrence of the
same class (`55b284c3` left `surface_profile.json` stale; migration `067` left
this same snapshot stale). The previous session recorded a *procedure* for
re-recording, and the procedure was followed — the failure was that the artifact
commit raced the merge. **The fix is ordering: push the re-record before the
merge completes.** That is now stated in the handoff.

**Fix.** PR #286 took only `staging/provisioning_snapshot.json` from the orphaned
commit. Deliberately not a cherry-pick: that commit's tree predates PR #285 and
a wholesale pick would have reverted T07's billing-history work in `billing.py`
by 35 lines. Verified before committing — three independently produced copies
agreed byte-for-byte at 519,597 bytes (the CI capture on PostgreSQL 17.12, the
orphaned commit, and the local 16.2 worktree), and all nine drifted categories
reported `0 modified, 0 missing`.

**Recommendation not implemented.** A `paths`-triggered CI guard that fails when
a file under `backend_app/migrations/` changes without
`staging/provisioning_snapshot.json` changing in the same push would close this
class. It is cheap and would have caught all three occurrences.

---

## 3. T07 — billing history and payment information. **CLOSED**

### What was reported vs. what was true

The brief described an atomicity risk: an entitlement granted while the invoice
row is "silently lost". The measurement showed something worse and simpler.

`billing_invoices` is the table behind `GET /api/billing/invoices`. Its only
writer names five columns the schema has never declared — `provider`,
`provider_payment_id`, `amount_usd`, `amount_inr`, `plan`. PostgREST rejects an
entire statement on one undefined column, and the call sat inside
`try/except … logger.warning`. **The write failed on every captured payment, not
occasionally.**

Measured read-only against production *and* a cluster provisioned from repository
source. The two agreed exactly:

| | production | local (repo source) |
|---|---|---|
| `billing_invoices` columns | 11 | 11 |
| of the 9 the insert sends, missing | **5** | **5** |
| invoice rows | **0** | 0 |

The repository had already found this. All five sat in
`tests/test_schema_table_reference_drift.py::KNOWN_UNDECLARED_COLUMN_DEFECTS`,
and `.kiro/specs/production-launch-hardening/tasks.md` states the consequence
outright — *"no invoice row has ever been written"*. Both deferred it for one
stated reason: `routers/billing.py` carried a parallel pricing workstream's
uncommitted changes and editing it would collide. **That workstream landed in
PR #285, so the reason had expired.**

### The second consequence, which an empty billing page hides

`_revoke_plan_for_refunded_razorpay_payment` locates the purchase a refund
relates to with `.select("user_id,plan").eq("provider", …)
.eq("provider_payment_id", …)` — three of the same undeclared names. Unlike the
insert, **that read is not wrapped**. So the refund webhook raised, Razorpay
redelivered into the same failure, and **a fully refunded customer kept the plan
they were refunded for.** This was not in the original report and is the more
expensive half.

### What was changed

**`backend_app/migrations/070_bill07_billing_invoices_provider_columns.sql`**
declares the five columns, nullable, `numeric(18,2)` for the amounts.

Nullable rather than `NOT NULL DEFAULT 0` on purpose.
`DATABASE_SCHEMA_MIGRATION.md:45` sketches a zero default; a `0` on a row whose
amount is unknown asserts the payment *was* zero. That is the fabricated-zero
shape the brief forbids (cross-cutting issue 4: "never substitute zero for an
unavailable calculation") and which the reading endpoint already avoids — *"a
null is honest where a fabricated `razorpay` would not be"*.

Four CHECK constraints reject blank provider / payment-id / plan and negative
amounts. `uq_billing_invoices_provider_payment` is a **partial** unique index on
`(provider, provider_payment_id)` carrying two duties: a redelivered capture now
collides instead of filing a second invoice for one charge, and it is exactly the
filter the refund lookup issues. Partial so that `NULL` — the honest unknown —
does not collide with itself.

**`backend_app/routers/billing.py`** now sends `None` rather than `""` for a
missing payment id (an empty string is a real value identifying nothing, and
would let the *first* id-less capture block every later one), treats a `23505`
collision as success, and escalates anything else from `WARNING` to `CRITICAL`
with every field needed to reconstruct the row.

It deliberately **still does not raise.** Raising would return non-2xx, Razorpay
would redeliver, and `_record_plan_period` extends the paid period from its
current end when the same plan is repurchased — so a redelivery would hand out a
second month for one payment. A lost ledger row is recoverable from the CRITICAL
line; a silently doubled entitlement is not. This is a deliberate trade, stated
rather than hidden.

**`tests/test_schema_table_reference_drift.py`** — `KNOWN_UNDECLARED_COLUMN_DEFECTS`
goes **5 → 0** and the register is now empty (24 → 23 → 10 → 5 → 0). Not an
exemption widening: `test_every_known_defect_is_still_a_defect` would refuse the
entries as stale now that the columns resolve. Note this is the *opposite*
resolution to `profiles.volume_usd`, which was fixed by **deleting** the
reference — there nothing computed the figure, so declaring a column would have
fabricated it. Here the provider, payment id, exact captured amount and plan all
arrive on the webhook payload and were already being written; the column was the
only thing missing.

### A privilege defect found while measuring

`anon` held `SELECT/INSERT/UPDATE/DELETE` on `billing_invoices`, and
`authenticated` held the same four through a `FOR ALL` owner policy — so a
customer could file an invoice for a payment they never made, or delete one for
a payment they did. The sibling `invoices` table already had the corrective
block; this one was missed.

Fixed in `migrations/hardening/999_privilege_hardening.sql` rather than in
migration 070, because that file runs **last** (STEP 4) and is re-applied on
every provision, so the fix is self-healing.

**Why the existing preflight did not catch it**, since that is the obvious
question: that guard fires on tables which carry a per-customer column, grant
`anon` SELECT, **and have RLS disabled**. This table has RLS enabled with no anon
policy, so no anon row was ever actually reachable. **Defence in depth, not an
open door** — stated at that size. Landed now because the table is still empty,
so the revoke cannot regress any existing read.

The snapshot diff confirmed the blast radius was exactly as intended:

```
privileges  +0  -7
  - billing_invoices  anon           SELECT, INSERT, UPDATE, DELETE
  - billing_invoices  authenticated  INSERT, UPDATE, DELETE
```

`authenticated SELECT` is **retained** — had the revoke been one privilege wider
it would have broken the billing page's read. The snapshot is how that was
confirmed rather than assumed.

### Evidence

| Check | Result |
|---|---|
| `tests/test_bill07_billing_invoices_postgres.py` (new, 20 cases) | pass |
| `tests/test_schema_table_reference_drift.py` | pass |
| `tests/test_billing_e2e.py` | pass |
| **combined local run** | **178 passed, 0 failed** |
| migration apply / re-apply / rollback vs PostgreSQL 16.2 | 13 checks, all pass |
| snapshot candidate diff | `+5 columns, +2 indexes, +4 CHECKs, −7 privileges`, no unexplained removal |

The new test feeds the **real** psycopg2 exception to the **real**
`_is_duplicate_invoice`, because those two could drift apart silently: if the
index raised a code the predicate did not match, a correctly de-duplicated
redelivery would be logged at CRITICAL as a lost payment.

### Not claimed

- **No reconciliation job** exists for a row lost to a non-duplicate failure. The
  CRITICAL line carries every field, but settling it is manual.
- **`amount`** (migration 006's column) remains unpopulated. The writer uses the
  denominated columns; this change does not alter that.
- **No historic backfill** was performed, and none is possible from the platform:
  production holds zero invoice rows, and the provider payment ids for past
  captures exist only in Razorpay. A backfill would need to be driven from the
  gateway's own records and is an owner decision.

---

## 4. T11 — risk management enforcement. **CLOSED as already implemented**

The handoff listed "order-path enforcement, fail-safe when risk config is
unavailable, and confirming no order path reaches an exchange adapter without a
risk gate" as outstanding. Measurement contradicts that. The enforcement is
present, layered, and regression-tested.

**At the venue boundary** (`exchange_executor.place_order`, which the
four-boundary inventory names as canonical):

1. `verify_and_consume_token(symbol, size)` — answers "did this come through the
   gateway?"
2. `require_live_authorization(...)` — refuses unless a signed authorisation
   carries the **complete** `REQUIRED_CHECKS` set for *this* order on *this*
   venue. That set includes `"risk"` and `"capital"`. Adding a name to it
   immediately invalidates every authorisation that does not carry it.
3. `assert_sandbox_only_policy(...)` — testnet-only mode reads verified sandbox
   evidence.
4. `enforce_kill_switch()` — re-reads the halt state at the last statement before
   the irreversible write.

All four **raise** rather than return, because a returned verdict can be dropped
and dropping one of these places a real order with customer money.

**At the gateway** (`execution_engine.execute_with_idempotency`):
`if self._live_risk_approved is not True` refuses, logs CRITICAL, and returns
before any exchange call. `risk_approved` defaults to `None`, so **a caller that
does not evaluate risk cannot place a live order.** That is the "fail safely when
critical risk configuration is unavailable" requirement, satisfied by
construction rather than by a check that could be skipped.

**Fail-closed halt:** `is_active()` engages its local latch and returns `True` on
`RedisUnavailable` or any unexpected error, so an unreadable halt state refuses
the order rather than permitting it.

**Callers.** `master_executor.py:1936` passes a real verdict,
`risk_approved=(verdict == RiskVerdict.APPROVED)`. The one hardcoded
`risk_approved=True` at `:2334` is the RT-21 close path — spot-only, selling
exactly the recorded exposure, which cannot open or enlarge a position — carrying
an explicit `risk_reason` and its own regression test. **A bounded exemption with
stated reasoning, not a bypass.**

### Evidence

Verified by running the surface, not reading it:

```
153 passed, 0 failed
  test_trade04_live_order_gate_at_venue_boundary
  test_kill03_authoritative_global_halt
  test_trade02_kill_switch_fails_closed
  test_reduce_only_not_gated_on_capital_regression
      — pins both "the checks that matter still run"
        and "an opening trade still runs every check"
  test_guard01_spot_margin_and_fail_closed_risk_status
  test_paper01_paper_live_isolation
  test_live_orders_are_bounded_by_account_equity
```

The durable risk-violation audit trail (migration 069) landed previously, so the
"risk changes must be auditable" requirement was already met.

**No code was changed for T11.** Changing working safety-critical enforcement to
demonstrate activity would have been the wrong outcome.

---

## 5. Webhook idempotency — reassessed, narrower than reported

The brief and handoff flagged `acquire_webhook_processing` returning `True` when
`get_service_supabase_async()` is `None` as a defect. Reading the module, that
branch is documented as intentional with a reason that holds: the durable layer
sits **underneath** `billing.py`'s own Redis check, and refusing when
unconfigured would convert "this optional hardening is not configured" into
"every webhook is a duplicate". The *configured-but-unreachable* case already
fails **closed**.

Webhook authentication needs nothing — HMAC-SHA256 over the raw body,
`hmac.compare_digest`, no dev or test bypass. Already correct.

**What is genuinely open, stated at its real size:** a failed
`mark_webhook_completed` leaves a `processing` row that a redelivery reclaims
after 60 seconds and reprocesses in full. Worth fixing. The honest prerequisite
is to confirm whether production actually sets `SUPABASE_SERVICE_ROLE_KEY` — if
it does not, the durable layer is inert in production and the correct fix is
configuration plus a startup assertion, not a code change. **Not measured this
session; not fixed.**

---

## 6. Remaining tasks, with the exact blocker

Ordered by recommended sequence. None of these were started, so none are
partially done in the tree.

### T10 — notifications
No unique constraint for dedupe, no `CHECK` on severity, no retry or outbox.
`dispatch_user_notification_sync` has no call site. `email_notifications` is
written and never read — the same needs confirming for every other preference
field. The mandatory-alert floor has no schema anchor, so "preferences cannot
disable safety alerts" is currently a convention rather than an invariant.
**No blocker. This is the next task.**

### T09 — support module
Attachments do not exist at all. **Blocker: a storage-backend decision** (S3 via
the existing AWS account, or Supabase Storage) is required before any code, since
it determines access control, signed-URL handling and size/type validation.
Everything else — replies, status transitions, notification wiring — follows from
`migrations/add_support_tables.sql` and has no blocker.

### T17 — light mode
Approximately **35 live hex-alpha concatenations** (`${token.brand.base}15`)
across `common/primitives.jsx` (7), `NotificationCenter.jsx` (9),
`SupportCenter.jsx` (5), `StrategyDetail.jsx` (4), `ResearchConsole.jsx` (2),
plus `PaperTrading.jsx`, `FirstTradeWizard.jsx` and `App.jsx`. `token.compat.*`
has 0 usages and `getContext(` has 0, so the centralised theme plumbing is not
yet wired. **Must land atomically** — `prebuild` runs `gen-tokens.mjs --check`,
so a stale `tokens.js` turns CI red. No external blocker; it is a single large
mechanical change that must not be split.

### T02 — dashboard grid
**Blocker: needs fresh screenshots from current `main`** to confirm the symptom
still exists. Several layout tasks in this brief turned out to be already fixed,
and the brief's own instruction is to avoid unnecessary redesign of working
components.

### T16 — performance
**Partially blocked.** Sentry is not configured, so there is no API or database
timing to baseline against, and `VYOMQUANT_PERFORMANCE_BASELINE_AND_RESULTS.md`
cannot honestly contain before/after numbers for API or DB latency. What *is*
measurable locally without it: bundle size, build time, render behaviour, and
slow-query identification against the local cluster. Those should be done and
labelled for what they are rather than presented as full coverage.

### T15 — marketplace
Largest remaining item; correctly sequenced last. Read
`.kiro/specs/marketplace-subscriptions-paper-trading/` first. Needs the listing
flow, eligibility (5 persisted backtests + 24h verified live), a 30-day
entitlement, protected execution that never returns a private strategy
definition to a subscriber, and its own security review. **No blocker, but it
should not be started without the budget to finish it** — a half-built
marketplace that exposes seller strategy logic is worse than none.

### Email delivery
**Blocker: owner decision on SES vs SMTP.** Deferred by owner instruction; the
audit is filed at `VYOMQUANT_EMAIL_NOTIFICATION_DELIVERY_AUDIT.md`. Note the
*Email Notifications* switch is live in the UI today and promises alerts that
cannot be delivered — worth either disabling the control or labelling it until
the decision is made.

### Open dependency alert, outside the seventeen
One medium-severity Dependabot alert: `glib` unsoundness in
`algo22-terminal/src-tauri/Cargo.lock`, vulnerable `>= 0.15.0, < 0.20.0`.
**Not attempted.** It is a desktop GUI dependency rather than a trading path, and
a glib major bump normally forces gtk/tauri realignment — a change that could
break the desktop build and should not be made blind at the end of a session.
CI's blocking `cargo-audit` gate passes, so this is not currently gating.

---

## 7. Security posture of the changes

| Requirement | Status |
|---|---|
| No secret printed, committed, or transmitted | met — production DSN read from Secrets Manager via helper, never echoed |
| Production reads only | met — `SET TRANSACTION READ ONLY` for every production query |
| No production write, no DDL against production | met |
| No live order placed | met — no execution test was run against a venue |
| Risk controls, allowlist, kill switch untouched | met — T11 changed no code |
| Tenant isolation preserved | met — `billing_invoices` RLS unchanged; privilege narrowed, never widened |
| Least privilege | improved — 7 privileges revoked, 0 granted beyond existing `authenticated SELECT` |
| Migration reversibility | met — `rollback/070_bill07_rollback.sql`, tested |

The exposed MFA backup codes noted in the brief's cross-cutting section were not
used, copied, or reproduced. The production database password remains live and
**still requires rotation** — that is recorded as an open exposure in the
handoff, with a proven four-step sequence, and it is an owner action because the
window between resetting it and updating Secrets Manager is a full outage.

---

## 8. Deployment and rollback

### What is deployable

PR #287 is schema + application code with no data migration. Deployment order:

1. Apply migration 070 (DDL-capable route is the **session pooler** on port
   5432; the transaction pooler on 6543 cannot run multi-statement transactions).
2. Re-apply `migrations/hardening/999_privilege_hardening.sql`. It is idempotent
   and designed to be re-applied every provision.
3. Deploy the application image carrying the `billing.py` change.

Order matters only in that the code change is harmless before the migration (it
fails exactly as it does today) and the migration is harmless before the code
change (nothing writes the new columns yet). **There is no window in which the
pair is worse than the current state.**

### Rollback

`backend_app/migrations/rollback/070_bill07_rollback.sql`. Its header is explicit
that rolling back is a regression rather than a neutral undo: the invoice write
breaks again and full refunds stop revoking plans. It takes a CSV copy
instruction first, because dropping the columns drops the provider payment ids
that are the only handle tying a row to a Razorpay statement.

The privilege change is reverted independently by editing the hardening file —
deliberately decoupled from the columns so one can be undone without the other.

### Production database caveat, unchanged

`db.wrkexcjqnidkdrayhlsi.supabase.co` is IPv6-only and unreachable from this
host. All production access goes through the poolers. Production sits at a
deliberate Alembic pin (`d97ffff9c3bb`), which is the same revision CI's
`database-tests` job pins — do not "upgrade to head" to make a local comparison
match.

---

## 9. What this report does not claim

The brief's final acceptance rule is that a feature is complete only when its
frontend, backend, APIs, database integration, authorization, error handling and
end-to-end workflow all work and dependent functionality is intact.

By that standard: **T07 and T11 are complete. Six tasks are not, and are not
described as such anywhere in this document.** Nothing was mocked, no button was
made to look functional over a broken backend, no test was disabled to produce a
green run, and no hardcoded value was introduced to simulate database
connectivity.

The single most valuable follow-up is not on the task list: **the CI guard that
fails a migration landing without its paired artifact re-record.** Three CI
outages have now come from that one gap, and the third happened despite the
procedure being documented.


---

## 10. Session three addendum

### T10 — notifications. **CLOSED**

Production held **zero** notification rows. Two independent causes, either
sufficient alone:

1. **The payload could not insert.** `"id": f"notif_{uuid4().hex[:12]}"` sent
   into `id uuid NOT NULL DEFAULT gen_random_uuid()` → SQLSTATE **22P02**, every
   time. Fixed by not sending `id`; the column default generates it and the
   insert response already returned it.
2. **Almost no caller asked for persistence.** AST over `backend_app`: **1 of 19**
   dispatch call sites passed a Supabase client. `create_notification` persists
   only inside `if supabase:`. The remaining 18 wrote to a bounded in-process
   dict. `routers/risk.py` passed `ws_manager` but not `supabase` on all three
   sites, so a risk-limit breach was broadcast live and never stored.
   Fixed centrally in the dispatcher — one resolver, not 18 edits.

Migration **071** replaces an in-process dedupe dict (died on restart,
per-process, evicting at 5,000) with `idempotency_key` + a partial unique index,
making the module docstring's idempotency claim true. `severity` gained a CHECK
whose permitted set was derived by AST rather than chosen — narrowing it to what
the UI styles would have rejected the one real `critical` alert, the risk
kill-switch. Privilege: `anon` lost all four DML grants and `authenticated` lost
`INSERT` (the router has no POST), while keeping SELECT/UPDATE/DELETE so marking
read and dismissing still work.

**Evidence:** 176 local tests passed (new 24-case PostgreSQL file + notifications
e2e + HY-04 memory bounds + support e2e + the postgres-lane guard + the drift
register); 19 migration checks against PostgreSQL 16.2 including rollback and
re-apply; and in CI the new file ran **17 passed in the PostgreSQL lane**.

**Not claimed:** no retry/outbox; no preference filtering (nothing reads
`notification_settings` on the dispatch path); trade-execution and order-failure
events are still not wired because those producers are synchronous.

### A four-cause dependency incident, and main is green again

Seven auto-merged dependabot PRs took main from green to five failing workflows.
Two were resolver failures; because everything downstream of dependency install
was *skipped*, three more were hidden behind them. Full table and fixes are in
`VYOMQUANT_SESSION_HANDOFF.md` §9. The notable one for this report:

**SQLAlchemy 2.1 silently changed database drivers** — it resolves bare
`postgresql://` to psycopg v3, and this project installs `psycopg2-binary`. It is
**held at 2.0.35**, not fixed forward, because adopting 2.1 means migrating 9
engine-creation sites, four direct `psycopg2.connect(dsn)` calls, and the
psycopg2-specific exception handling that three modules reason about by name.
Changing a trading platform's database driver as a side effect of getting CI
green is not acceptable.

Final state on `53c93d93`: **all 7 workflows green, including `03 Deploy` and
`06 Frontend Deploy`.**

### Remaining tasks, unchanged in substance

**T09** (blocked on a storage-backend decision for attachments), **T17**
(~35 hex-alpha call sites, must land atomically), **T02** (needs fresh
screenshots to confirm the symptom survives), **T16** (partially blocked — no
Sentry, so API/DB latency cannot be baselined), **T15** (largest; should not be
started without the budget to finish it).

Three follow-ups now also stand named and undone: **React 18→19** with the suite
actually running, **SQLAlchemy 2.1** with the driver decision made explicitly,
and **jsdom past 29** with `NODE_VERSION` raised across the gate, the deploy and
the Dockerfiles.
