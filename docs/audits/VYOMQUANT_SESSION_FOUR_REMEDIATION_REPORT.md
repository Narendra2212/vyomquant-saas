# VYOMQUANT — session four remediation report

Covers the engineering session of 2026-11-18 against the master remediation
brief. It supersedes nothing. The architecture audit remains
`VYOMQUANT_MASTER_ENGINEERING_AUDIT.md`, the operational notes remain
`VYOMQUANT_SESSION_HANDOFF.md`, and the first three sessions' outcomes remain
`VYOMQUANT_FINAL_REMEDIATION_AND_VALIDATION_REPORT.md`.

It states what was completed with evidence, what was found to be already
complete, and what remains with the exact blocker. It does not describe
unfinished work as finished.

---

## 1. Headline

Two tasks closed. The larger one is Task 15 D, and closing it meant first
discovering that **the evidence the marketplace eligibility gate reads was
writable by the account being judged** — so the existing gate, which is
carefully specified and property-tested, could be satisfied in one HTTP request.
Task 17 turned out to be complete already and is recorded as measured rather
than re-done. `main` was found red from a third-party cause and unblocked.

| | |
|---|---|
| Tasks closed this session | **T15 D** (the two mandatory listing gates), **T15 B** (the listing affordance), **T10's mandatory-alert floor** |
| Tasks found already complete | **T17** (light mode, 0 live hex-alpha sites measured) |
| `main` incidents fixed | 2 — the support surface baseline stale since PR #309, and one I caused (§8c) |
| Standing blockers resolved | 1 — production *does* configure `SUPABASE_SERVICE_ROLE_KEY` |
| Production data changed | **none** |
| DDL against production | **none** |
| Live orders placed | **none** |

**Landed.** PR **#314** (`079dfbd5`) and PR **#315** (`0364861f`) are merged.
`main`'s `01 PR Check` is green on `0364861f` — all 16 checks, including
`Provisioning, Privilege Hardening & Tenant Isolation`, `Database Tests
(PostgreSQL)`, `Unit & Integration Tests` (17m37s) and `Frontend Build & Unit
Tests`.

The migration and the privilege revoke are **not yet applied to production** —
see §10 for the order.

Commits, in order on `mkt07/listing-eligibility-gates`:

| SHA | What |
|---|---|
| `2ae8c78c` | `fix(MKT-07)`: the two listing gates, and the ledger they read |
| `48a43862` | `fix(ci)`: main has been failing the support surface baseline since PR #309 |
| `d85c4ea0` | `fix(NOTIF-02)`: the mandatory-alert floor |
| `a4e16031` | `test(MKT-07)`: pin the listing control as visible-but-disabled, not hidden |
| `643e112e` | `fix(MKT-07)`: four guards the first commit should have satisfied |
| `44968796` | `chore(MKT-07)`: re-record migration 073's checksum |

---

## 2. MKT-07 — Task 15 D. **CLOSED**, and the gate was reading forgeable evidence

### 2.1 What the brief requires, and what the tree enforced

Task 15 D makes publication conditional on two things:

> 1. "At least five valid, completed, persisted backtest results must exist for
>    that strategy."
> 2. "The strategy must have at least 24 hours of verifiable live algorithmic
>    trading history. […] Do not count paper trading as live trading for this
>    requirement. Do not fabricate or manipulate live trading performance data."

Measured against `main`:

| | Required | Enforced |
|---|---|---|
| Completed backtests | 5 | **3** (`evidence_validator.MIN_CONDITIONS`) |
| Live trading history | 24 h | **no criterion existed** |

So the first was enforced at the wrong number, and the second was not enforced at
all. A strategy that had never placed a live order could be listed, and the
subscriber-facing "verified live performance" claim had nothing behind it.

### 2.2 The finding that changed the shape of the fix

A gate is only as good as the evidence it reads. Measured on a cluster
provisioned from repository source, as role `authenticated` with
`request.jwt.claim.sub` set to the customer's own id:

```
INSERT a fabricated COMPLETED fill, exchange_id='binance',
       filled_at = now() - 48 hours              *** SUCCEEDED ***
UPDATE an existing own row's filled_size          *** SUCCEEDED ***
DELETE an existing own row                        *** SUCCEEDED ***
```

`anon` and `authenticated` both held `SELECT/INSERT/UPDATE/DELETE` on
`public.execution_records`, and the owner policy `execution_records_user_access`
is `FOR ALL` with `auth.uid() = user_id OR auth.uid() = tenant_id` — so the
customer satisfies it for their own rows by construction. **RLS was doing exactly
what it was written to do; the privilege underneath it was wrong.** The browser
holds the anon key (`algo22-terminal/src/supabase.js`), so PostgREST is reachable
from any logged-in session.

The same rows feed Trade History's LIVE tab, realised P&L, and the daily-loss and
drawdown calculations Task 11 depends on.

Adding a live-history criterion on top of that would have been decoration: a
seller could have manufactured the 24 hours in one request. So the privilege is
part of this change rather than a follow-up.

**Why the revoke is safe, measured rather than assumed.** Nothing in the
repository writes `execution_records`, `orders` or `positions` through PostgREST
— AST over every `.table("<name>").insert/update/upsert/delete` chain in
`backend_app`, plus grep across `api_ws/`, `scripts/`, `dev_tools/` and
`algo22-terminal/src`. Zero writers; the one `.select()` is a fake client in a
test. Every real write goes through SQLAlchemy as the table owner, which is
exempt from both the grant and the policy.

### 2.3 What changed

**`evidence_validator.MIN_CONDITIONS` 3 → 5.** The disagreement between the
internal spec's Requirement 3.1 ("at least three") and the brief ("at least
five") is recorded at the constant, with what raising it costs: five windows must
each span 90 days and overlap pairwise by at most 25 %, so the evidence set now
needs materially more distinct market history.

**`eligibility_gate` gains `MP_LIVE_HISTORY` and a fifth owner-scoped read.** The
criterion is the span between the earliest and latest *verified live fill*:
`status='COMPLETED'`, `exchange_id IS NOT NULL`, `filled_size` parsing above
zero, dated by `filled_at`/`submitted_at`/`created_at` in the same coalesce order
`routers/orders.py` already uses — so Trade History and the gate date the same
fill identically.

Three details that are decisions rather than mechanics:

- **Fewer than two fills is `0.0` hours**, not "the deployment has been up a
  while". One fill demonstrates one order.
- **The read asks for a bounded window from each end** (ascending, then
  descending, 200 rows each). A single capped read would fail a high-frequency
  strategy whose first 200 fills all landed inside an hour.
- **`filled_size > 0` is applied in Python, not as a PostgREST filter.** The
  column is `character varying`, so a PostgREST comparison would be
  lexicographic — `"0.5" > "0"` is true by luck and `"10" > "9"` is false — and a
  wrong filter there would silently drop real fills.

**Why `execution_records` and no other ledger.** `strategy_deployments` would
give deployment runtime directly, which is arguably the more natural reading of
"24 hours". It was rejected because `authenticated` holds INSERT/UPDATE/DELETE on
it *and* the application's own writer uses the caller's client, so the runtime
figure is forgeable by the seller. `execution_records` is written exclusively
through SQLAlchemy as the table owner, and after this change `authenticated`
holds SELECT and nothing else.

**`EVALUATOR_VERSION` → `1.1.0`.** An admission stored under `1.0.0` was decided
without this criterion and against a floor of three. Leaving the string alone
would make the two indistinguishable in the Audit_Log.

**`SubmissionCreateRequest`'s field bound** now reads `MIN_CONDITIONS` /
`MAX_CONDITIONS` instead of literals, so the 422 the field produces cannot
disagree with the criterion the gate decides.

**Task 15 B — the listing affordance.** A "List on marketplace" row action on
every owned strategy and every plan: visible always, enabled only where
publishing is included, carrying the **server's own** refusal sentence
(`lockedFeatureRefusal`) so the plan named in the tooltip cannot drift from the
plan the 403 would name. It opens the strategy's existing Marketplace tab; no
second listing surface was built, because the brief's own instruction for Task 15
is not to build a second listing system. Authorisation remains
`Depends(require_marketplace_publish)` plus `check_marketplace_publish_quota`.

Offering it in every lifecycle state is deliberate: the live-history criterion
needs the strategy to have run, so suppressing the control for a stopped strategy
would hide it from exactly the owners most likely to qualify.

### 2.4 Evidence

| Check | Result |
|---|---|
| `tests/test_mkt07_listing_eligibility_gates.py` (new, 30 cases) | **pass** |
| — of which postgres-lane, attempting the forgery and requiring refusal | 5 |
| `tests/test_marketplace_pipeline.py` + `test_marketplace_concurrency.py` | 72 passed |
| `tests/test_marketplace_eligibility_tenant_verdict.py` | 7 passed |
| `tests/test_schema_table_reference_drift.py` | 86 passed |
| `tests/property/test_evidence_distinctness.py` + `test_tenant_isolation_matrix.py` | 19 passed |
| frontend: `tests/unit/lib` + `tests/unit/marketplace` + `Strategies.test.jsx` | 246 passed |
| privilege probe, before → after | forgery **SUCCEEDED** → **permission denied** |

The forgery probe is the load-bearing one, and it is a regression test rather
than a one-off measurement: it adopts the customer's identity the way a policy
sees it, asserts `auth.uid()` actually resolved to that id — otherwise a refusal
would prove nothing — and then requires the insert to be refused.

### 2.5 Three test doubles were wrong, which is why seven green tests had to move

`not_` was a **method** on the pipeline and concurrency `FakeTable`s. supabase-py
spells a negated predicate `.not_.is_(col, "null")`, so as a method `.not_`
returned a bound method, `.is_` on it raised `AttributeError`, and the gate's
fifth read collapsed every verdict to `unevaluable`. It is now a property, and in
the schema-faithful double the negated filter is *applied* rather than ignored —
a double that dropped `exchange_id IS NOT NULL` would answer the live-history
criterion from rows the real read never returns.

The tenant isolation matrix needed a different correction, and it is the more
interesting one. Its `POST /api/library/submissions` cell was sending three
backtest ids, which the new field bound refuses *before the route runs* — so both
the foreign-identifier and absent-identifier answers became "Input validation
failed". That looks like perfect isolation and measures nothing. It now sends
`MIN_CONDITIONS` ids, the cell probes the gate again, and the recorded
Requirement 21.4 gap is unchanged in substance.

### 2.6 Not claimed

- **`strategy_backtests` is not protected.** It is the other half of the gate's
  evidence and `authenticated` still holds INSERT/UPDATE/DELETE on it, because
  the application's own writer *is* the caller's client
  (`backend/backtest_service.py:336`, used by the insert at `:451`, the result
  update at `:582` and the delete at `:807`). Revoking without moving those three
  writes to the service-role client would stop every backtest persisting.
  `test_the_other_half_of_the_evidence_is_still_writable` pins the gap open and
  **fails when it is fixed**.
- **No distinction between a testnet and a mainnet fill.** `execution_records`
  records no sandbox flag — confirmed against the production column list — so
  "live" here means "a venue order was placed", the same definition Trade History
  uses. Paper trading *is* excluded: paper fills never reach this table, and
  `test_paper01_paper_live_isolation` pins that no module under `backend/paper`
  reaches a venue.
- **No figure for how many strategies currently qualify.** Not measured.

---

## 3. A standing blocker resolved: production does configure the service role

Two previous sessions recorded this as an open prerequisite — for the durable
webhook idempotency layer, and now for moving the backtest writer off the
customer's role.

Measured by reading the ECS task definitions, **names only, no value fetched**:
all **7** services in `vyomquant-cluster` carry `SUPABASE_SERVICE_ROLE_KEY`
alongside `SUPABASE_URL`, `SUPABASE_ANON_KEY` and `SUPABASE_JWT_SECRET`.

Two consequences:

1. `core/webhook_idempotency`'s durable layer **is active in production**. The
   previous report's "the honest prerequisite is to confirm whether production
   sets it" is answered, and the remaining open item there is narrower than
   stated: a failed `mark_webhook_completed` leaves a `processing` row a
   redelivery reclaims after 60 s. Still unfixed; now unambiguously a code fix
   rather than a configuration decision.
2. Moving the three `strategy_backtests` writes to the service role is feasible.
   It is a separate PR because it touches the backtest result path.

---

## 4. NOTIF-02 — Task 10's mandatory-alert floor. **CLOSED**

### 4.1 The defect, in two adjacent lines of one component

`components/NotificationPreferences.jsx` ended with:

> "Mandatory risk alerts (global kill switch triggers) cannot be disabled."

Directly above that sentence it rendered a switch labelled *Emergency Kill Switch
& Liquidations*, wired to `events.kill_switch_activated`, which a customer could
turn off. **The panel stated an invariant and then offered the control that broke
it.**

Nothing enforced the sentence. Readers of `notification_settings` across
`backend_app`: two, both in `routers/user.py` — the GET and the PUT of the panel
itself.

### 4.2 The measurement that changed the fix

The obvious constraint would have been vacuous. The column declares:

```
events DEFAULT '{"system": true, "stop_loss": true,
                 "risk_alerts": true, "trade_fills": true}'
```

The vocabulary actually **stored** is a different one — the eight fields of
`pydantic_models.NotificationEvents`. Measured read-only against production with
`jsonb_each` over all four rows:

```
events keys   : api_key_expiring, backtest_complete, bot_state_change,
                daily_pnl_summary, kill_switch_activated,
                new_login_detected, stop_loss_triggered, trade_executed
channels keys : email, mobile, telegram
```

All four rows carry all eleven. **Not one row carries `system`, `risk_alerts`,
`trade_fills`, `push` or `in_app`.** The declared defaults have never been
realised, because the only writer always sends the whole Pydantic object and
nothing inserts without one.

A `CHECK` against `risk_alerts` would have constrained a key no row has, passed
on every row, and read in review like an enforced invariant.

### 4.3 Three layers, one list

| Layer | What it does |
|---|---|
| `MANDATORY_NOTIFICATION_EVENTS` | declares the three, beside the model |
| `NotificationEvents.mandatory_alerts_stay_on` | refuses the payload, naming every offending switch |
| migration 073's `chk_notification_settings_mandatory_alerts` | refuses the row, whatever writes it |

The three are `kill_switch_activated`, `stop_loss_triggered` and
`new_login_detected` — each an event whose suppression leaves a customer unaware
that their money is at risk or their account was reached.
`routers/risk.py:849` (`kill_switch_activated`) is the only `severity="critical"`
dispatch in the tree.

**Deliberately not on the floor**, because a floor that swallows everything stops
being a floor: `api_key_expiring` (a maintenance warning a customer may
legitimately mute), `trade_executed`, `daily_pnl_summary`, `bot_state_change`,
`backtest_complete`, and all three delivery channels. Silencing email is not
silencing the alert.

Refused rather than silently coerced to `true`: a coercion stores the right value
and tells the customer their change was saved, so the switch springs back on the
next read with no explanation.

The dead column defaults are corrected in the same transaction. Left alone, a row
inserted without a payload would carry four keys nothing reads and be missing all
three mandatory ones — which the constraint's `IS NOT FALSE` reading would then
permit.

The panel now locks those three switches (`aria-disabled`, `cursor: not-allowed`,
a lock glyph, the reason in place of the description) and its closing sentence
names all three. It used to name only the kill switch while three alerts are on
the floor; a sentence that under-claims is as misleading as one that over-claims.

### 4.4 Also in this change

- The upsert at `routers/user.py:151` was **unguarded**, so any write failure
  became a 500 with no sentence a customer could act on. It now answers 503, and
  409 for a floor violation — with the offending keys logged at ERROR, because
  reaching that branch means the request model and the constraint disagree, which
  is a defect rather than a customer mistake.
- `anon` held all four DML privileges on `notification_settings`. Revoked.
  `authenticated` keeps SELECT/INSERT/UPDATE (the panel's write is an upsert
  through the caller's own client, the only PostgREST writer in the tree, and an
  upsert needs both verbs). DELETE goes: no endpoint deletes a preference row,
  and a deleted row reads as "nothing stored", which the panel renders as the
  contract defaults — a silent reset rather than an erasure the customer sees.

### 4.5 Evidence

| Check | Result |
|---|---|
| `tests/test_notif02_mandatory_alert_floor.py` (new, 22 cases) | **pass** |
| — postgres-lane: the CHECK refuses each mandatory key against a **real** profile row | 3 |
| — postgres-lane: a discretionary preference still stores `false` | 1 |
| — postgres-lane: a payload-less insert carries the vocabulary the app reads | 1 |
| — postgres-lane: `anon` holds nothing, `authenticated` holds exactly three verbs | 1 |
| migration apply / re-apply / rollback / re-apply vs PostgreSQL 16.2 | clean |
| `tests/regression/test_baseline_unchanged.py` | 58 passed |
| frontend `notificationPreferences.test.jsx` | 8 passed |

The migration's postflight **attempts the write it exists to refuse** and
requires the refusal, because a constraint that merely exists proves nothing — a
predicate using `->>` against a `jsonb` literal would pass an existence check and
permit the write.

`switchFor` in the panel's test located a control by `cursor: pointer`, so it
stopped finding the three locked rows. It now reads `role="switch"` plus the
accessible name — the contract the panel documents, rather than a presentational
detail that makes a styling change look like a missing control.

### 4.6 Not claimed — and pinned by tests, not prose

**Preferences still do not take effect.** Nothing on the dispatch path reads
`notification_settings`, so no preference suppresses any notification. And of the
eight event keys, **seven have no producer**: AST over every
`dispatch_user_notification` call site finds 17 sites across 8 categories, and
only `kill_switch_activated` matches a declared preference key. Building a filter
over that would be building a mechanism with no input.

Both facts are held by tests rather than described in a note:
`test_nothing_on_the_dispatch_path_reads_a_preference` and
`test_seven_of_eight_preference_keys_have_no_producer` fail when either changes,
so wiring a filter has a test to delete rather than a comment to find.

Also measured this session and worth recording: **`public.notifications` still
holds 0 rows in production.** NOTIF-01's fix (migration 071 plus the dispatcher's
`_resolve_persistence_client`) is in the repository; either it has not been
deployed or no qualifying event has fired since. That is a deployment fact, not a
code defect, and it is the thing to check first after the next release.

---

## 5. T17 — light mode. **Already complete.** Measured, not re-done

The previous two reports listed "~35 live hex-alpha concatenations
(`${token.brand.base}15`)" as T17's outstanding work, spread across eight files.

A naive grep still finds 8 matches. **All 8 are inside comments** documenting the
retired form. Stripping block and line comments first — the same way
`tests/unit/guards/no-colour-literals.test.js` does before counting:

```
LIVE hex-alpha concatenations: 0
alpha(...) call sites:        54
```

`node scripts/gen-tokens.mjs --check` — the `prebuild` gate — passes:
`src/design/tokens.js is up to date (107 tokens)`. The theme system is present
and wired: `design/theme.jsx`, `components/shell/ThemeToggle.jsx`,
`public/theme-boot.js` (classic and blocking by design, to prevent the flash) and
`design/alpha.js`.

**Nothing was changed for T17.** Converting call sites that no longer exist, to
demonstrate activity, would have been the wrong outcome. The figure in the
earlier reports was a grep count that included its own documentation; this
corrects it.

One local artefact, not a defect in the tree:
`tests/unit/guards/tokens.generated.test.js` fails to *load* under Node 24 with
`SyntaxError: Invalid or unexpected token` while its five cases skip. No
token/theme file is touched by this session's commits, and the gate that CI
actually runs passes. CI runs Node 20.

---

## 6. `main` was red, from a cause outside this session

`tests/regression/test_baseline_unchanged.py::test_baseline_unchanged[surface_support]`
fails on `origin/main`:

```
api_calls[3].handlers[0].raises: entry added - {503}   POST /api/support/tickets
api_calls[4].handlers[0].raises: entry added - {503}   POST .../comments
```

PR #309 (`f646cd87`, SUP-02) made both handlers fail closed, which is correct and
which its own commit message explains. Its five changed files were the migration,
its rollback, the router, a new test and a workflow line.
`tests/regression/baseline/surface_support.json` was last touched by `c69b25bc`,
long before — so the baseline still recorded the empty-200 contract.

CI runs the whole of `tests/` at `01-pr-check.yml:251`, so every PR opened since
has inherited the failure.

**This is the fourth occurrence of the same class:** `surface_profile.json` at
Task 13 (`55b284c3`), then the provisioning snapshot against migrations 067 and
069, and now this. Fixed in `48a43862` by re-recording the baseline — verified
additive, two `raises` entries added and nothing removed — and by restoring the
`_baseline_note_raises` the capture tool drops, plus a new note for the two
changed handlers.

**The standing recommendation, still not built, is now four-for-four:** a
`paths`-triggered CI guard that fails when a file under `backend_app/routers/` or
`backend_app/migrations/` changes without its paired artifact
(`tests/regression/baseline/surface_*.json`,
`staging/provisioning_snapshot.json`) changing in the same push. It is cheap and
would have caught every one.

---

## 7. Artifacts re-recorded, and how each was verified

The handoff's rule is that a snapshot captured locally (PostgreSQL 16.2) against
a baseline captured in CI (17.12) moves the failure rather than fixing it. So
`staging/provisioning_snapshot.json` was edited **surgically** rather than
re-captured, twice:

| Change | Diff | Verified by |
|---|---|---|
| MKT-07 privileges | `−105 lines, +0` = exactly the 21 revoked rows | drift gate reports `privileges: 0 modified, 0 added` |
| NOTIF-02 | `+1 CHECK, +1 recorded migration, 2 defaults changed, −5 privileges` | every category reports `0 modified, 0 added` |

The remaining local drift is pre-existing cluster staleness — migration 069's
`risk_violation_audit`, `pgcrypto`, and PostgreSQL 16 vs 17 function signatures —
unrelated to these commits. **CI provisions from scratch on 17 and is the
authority.**

One trap worth recording, because it would have failed the gate on any CRLF
checkout: `scripts/provision_database.py` digests
`path.read_text(encoding="utf-8")`, which applies universal-newline translation.
The recorded checksum is therefore over **LF-normalised text**, not over the
file's bytes. The hardening file's entry was corrected accordingly.

---

## 8. Remaining work, with the exact blocker

### T15 — marketplace, the parts other than D and B

A and C were found already built and were extended rather than replaced:
`eligibility_gate`, `submission_service`, `entitlement_resolver`,
`subscriber_operation_guard`, `subscription_reinstatement`, `expiry_sweep`,
`listing_projection`, `CreatorMarketplacePanel`, and property suites for
protected-logic containment and tenant isolation.

**E, F and G were then audited — see §8d.** F holds. E has one real defect, now
answered honestly (PR #318). G was checked only to the depth of naming its
mechanisms and is the genuine remainder.

**Named and not done: move the three `strategy_backtests` writes to the
service-role client, then revoke `authenticated`'s DML there.** No blocker — the
service-role prerequisite is now confirmed present in production. Until then the
five-backtest half of the gate reads evidence the seller can write, and
`test_the_other_half_of_the_evidence_is_still_writable` says so.

### T14b — merge the entitlement list into the main strategy listing

Unchanged from `TASK_14_STRATEGIES_PAGE_SCOPE_FINDING.md`. Task 14's instruction
to remove the *Owned and subscribed* section is **conditional**, and the
condition is still not satisfied: that section is the only surface rendering a
subscribed Listing and the only place renew, cancel and listing-deploy exist.
Deleting it would remove paying subscribers' access to what they bought. The
merge is achievable and sequenced with T15's remainder.

### T09 — support attachments

**Blocker: a storage-backend decision.** Attachments are metadata only
(`AttachmentMeta`: filename, content type, size); no endpoint accepts file bytes,
and production has one bucket, `strategy-images`, with nothing for support. The
UI and the FAQ both say so rather than pretending otherwise, which is the right
interim state. S3 via the existing AWS account or Supabase Storage determines
access control, signed-URL handling and size/type validation, so it must precede
code. Everything else in T09 — the ticket write, replies, status transitions —
landed in PR #309.

### T10 — preference filtering and retry/outbox

The floor is now an invariant (§4). Filtering is **not** built, and the reason is
measured rather than asserted: seven of the eight preference keys have no
producer, so there is nothing to filter. The prerequisite is wiring trade-execution
and order-failure notifications, which needs a safe sync→async bridge
(`asyncio.run_coroutine_threadsafe` against the application loop, holding the
future) because the execution engine and bot runner are synchronous. No
retry/outbox.

### T16 — performance

PERF-01 landed in session three with before/after measurements
(`VYOMQUANT_PERFORMANCE_BASELINE_AND_RESULTS.md`): −48 kB gzipped and two
requests off the critical path. Further optimisation was not attempted this
session, and nothing new was measured, so nothing new is claimed.

### Email delivery

**Blocker: owner decision on SES vs SMTP.** Unchanged. The *Email Notifications*
switch is live and promises alerts that cannot be delivered; the panel's own
docblock records the gap and
`VYOMQUANT_EMAIL_NOTIFICATION_DELIVERY_AUDIT.md` has the four measurements.

### The two sharpest remaining unknowns in T15

Named separately from the rest because both are the kind that look like nothing
until they are expensive:

1. **Subscriber-run backtesting does not exist** (§8d E). The refusal is now
   honest, but the capability the brief asks for is not built. The shape is
   specified; it is a new execution path and needs its own PR.
2. **The 30-day entitlement expiry was not verified against the clock.**
   `subscription_period.py`, `expiry_sweep` and
   `test_entitlement_expiry_boundary` exist and pass, but no measurement was
   taken. An entitlement that fails to expire is a revenue defect that is
   invisible until it is large, and it is the one T15 item where the test
   passing and the behaviour being right are not the same claim.

### Named this session and not done

- **`tests/property/test_protected_logic_containment.py`'s substring comparison**
  (§8a). It will fail intermittently in CI whenever Hypothesis draws an English
  word that appears in a public refusal sentence. Not touched, deliberately.
- **24 tests that `open()` a file without an encoding.** They pass on CI and fail
  on any Windows cp1252 locale, which makes a local full-suite run unreadable
  without classifying 24 failures by hand first. A one-line
  `encoding="utf-8"` per call site would close it.
- **The local verification cluster is behind the migration set** (missing 069's
  `risk_violation_audit`, and 070–073 unrecorded). Re-provisioning it with
  `scripts/provision_database.py --apply --create-substrate` would make 8 more
  of those 40 failures go away and make the drift gate usable locally for more
  than the privileges category.

### Outside the task list

- **The production database password is still live** and still requires rotation.
  Owner action: the window between resetting it and updating Secrets Manager is a
  full outage, and one such outage has already happened. The four-step sequence
  is in the handoff §3.
- **The exposed MFA backup codes are still pending invalidation.** Not used,
  copied or reproduced here.
- **React 18 → 19** with the suite actually running; **SQLAlchemy 2.1** with the
  psycopg driver decision made explicitly; **jsdom past 29** with `NODE_VERSION`
  raised across the gate, the deploy and the Dockerfiles. All three named in
  session three, all three still undone.
- **One medium Dependabot alert**: `glib` in
  `algo22-terminal/src-tauri/Cargo.lock`. Desktop GUI dependency, not a trading
  path; the blocking `cargo-audit` gate passes.

---

## 8a. The full-suite sweeps, and every failure classified

Both suites were run end to end, not just the touched files.

### Frontend — `npx vitest run`

```
Test Files  1 failed | 191 passed (192)
     Tests  3944 passed | 11 skipped (3955)
```

**Zero test failures.** The one "failed" file is
`tests/unit/guards/tokens.generated.test.js`, which fails to *load* under Node 24
(`SyntaxError`) while its five cases skip. No token or theme file is touched by
this session; CI runs Node 20 and `gen-tokens.mjs --check` passes.

### Backend — `pytest tests/ -k "not chaos and not load" -m "not frontend_bundle"`

```
40 failed, 16749 passed, 112 skipped, 368 deselected,
5 xfailed, 4 xpassed, 8 errors in 4881s (1:21:21)
```

**Four of the 40 were this branch**, each caught by a guard that exists to catch
exactly that, and each fixed in `643e112e`:

| Guard | What it caught |
|---|---|
| `test_every_postgres_gated_file_runs_in_a_postgres_lane` | the two new files were in no PostgreSQL lane, so their 12 postgres assertions would have run against a real server **nowhere** |
| `test_no_destructive_statements` | migration 073's postflight carried a `DELETE FROM` on its failure branch (Requirement 24.7) |
| `tests/e2e/test_marketplace_paper_journey` | seeded 3 backtests and no live history → `EV_COUNT`, then `MP_LIVE_HISTORY` |
| `test_api_mock_factories_declare_isauthenticated` | `Strategies.test.jsx` had left the pinned-incomplete list and the ratchet had not moved |

The first is the one worth dwelling on: the forgery regression and the
mandatory-alert floor are the two most load-bearing assertions of this branch,
and without that guard they would have skipped silently in CI.

**The other 36 are not this branch**, and are classified rather than waved at:

| Count | Cause |
|---|---|
| 24 | `UnicodeDecodeError: 'charmap'` — tests calling `open(path)` with no encoding, on a Windows cp1252 locale, against files containing UTF-8 box drawing. CI is Linux/UTF-8. |
| 8 | postgres-gated files (`test_sup02`, `test_bt04`) reaching a **local** cluster that lacks migrations 069–072. Stale verification cluster. |
| 1 | `test_badhost_path_poisoning_regression` — a starlette version assertion. |
| 1 | `test_task_2_4` — a pre-existing pydantic `TypeAdapter … is not fully defined` on `AcceptanceRequest`. |
| 1 | `test_protected_logic_containment` — see below. |
| 1 | a log line the classifier matched as an error, not a failure. |

**The containment failure is worth naming precisely, because it will recur.**
Hypothesis drew the token `'subscribe'` from its `a-z0-9_` alphabet, and the
containment assertion is a **substring** check — so it collides with
`errors.py:367`, *"You cannot subscribe to your own listing."* No string added by
this branch contains "subscribe", and `.hypothesis/` caches the example so it
replays locally. In CI, with no example database, it fails only when the draw
recurs — which it will, eventually, for any English word appearing in a public
refusal sentence.

The fix is to compare whole identifiers, or to exclude tokens that appear in the
catalogue's own sentences. It was **deliberately not made here**: loosening a
Protected_Logic containment guard at the end of a session is how real leakage
gets masked, and this is the guard standing between a subscriber and a seller's
strategy internals.

### One guard was unusable locally, and that is now fixed

`test_api_mock_factories_declare_isauthenticated` walks `algo22-terminal/**` and
so descended into `algo22-terminal/vyomquant-saas/` — the untracked nested clone
of the whole repository that the handoff records as a hazard. It reported
eighteen "missing" factories, every one a copy of a file already on the pinned
list, under paths CI does not have. The walk now skips that directory.

A guard that fails for a reason the reader cannot act on is a guard the reader
learns to ignore, and then a real omission arrives behind the noise.

### After the fixes

```
382 passed across the eleven suites this branch touches
```

Including both new files **in the postgres lane** (12 postgres cases, 0 skipped),
the drift register, all 58 regression baselines, and the e2e journey.

---

## 8b. Four defects a self-review found that the tests did not

The suites were green before any of these. That is the finding worth recording,
because a green suite written by the same person who wrote the code tests the
cases that person thought of.

### The CHECK constraint only caught one of six shapes

`(events -> 'kill_switch_activated') IS DISTINCT FROM 'false'::jsonb` reads
correctly and binds for almost nothing. `->` yields jsonb, so only the JSON
*boolean* `false` matches. Measured against that predicate:

| shape | verdict |
|---|---|
| JSON boolean `false` | refused |
| JSON boolean `true` | accepted — correct |
| JSON string `"false"` | **accepted** |
| JSON string `"no"` | **accepted** |
| JSON number `0` | **accepted — dangerous** |
| JSON `null` | **accepted — dangerous** |
| key absent | accepted — correct |

`0` and `null` are dangerous not because of what the database makes of them but
because of what a reader would: `bool(0)` and `bool(None)` are both `False`, so
the preference filter this floor exists to constrain would read either as
"switched off" — the exact suppression the constraint forbids, arriving through a
shape it waved through.

Reachable, because `authenticated` retains INSERT and UPDATE on the table (the
panel's upsert needs both), so a hand-rolled PostgREST request can send any of
them even though the request model cannot.

Now `IS NULL OR = 'true'::jsonb`. Re-measured: all five non-`true` shapes
refused, absent and `true` accepted. The postgres case is parametrised over the
five, because one lucky shape is what made the weaker predicate look enforced.

### The end-windows sorted by a nullable column

The two reads ordered by `filled_at` — the column the span is *dated* by, and
nullable. PostgreSQL places nulls at one end, so a strategy with more than 200
fills whose `filled_at` was never stamped would have its descending window filled
entirely with those rows and its genuinely latest fill excluded.

A false *fail* rather than a false pass, so not dangerous — but user-visible in
the worst way on a gate whose refusal the owner cannot argue with. Now ordered by
`created_at`, which is `NOT NULL`.

### The evidence generator still drew sets of three

`tests/strategies/marketplace_generators.MIN_CONDITIONS` stayed at 3 and nothing
failed, because `test_evidence_distinctness` computes the expected verdict rather
than assuming admission. The cost was coverage, not correctness: every drawn set
of 3 or 4 becomes a guaranteed `EV_COUNT` failure, so the generator would spend
most of its draws re-proving one criterion and the admitted path would quietly
empty out.

### Two stale prose references

`test_validation_sweep.py` quoted `min_items=3, max_items=10` as the field's
declaration and `test_marketplace_pipeline.py` described the flow as admitting
"on three completed runs". Comments, not assertions — which is why neither
failed, and why a reader would have been misled rather than warned.

### And one claim checked and found sound (see also §8c)

The report's assertion that "nothing in the repository writes `execution_records`,
`orders` or `positions` through PostgREST" was re-verified independently, with a
wider net than the original AST pass: `.table("…")`, `.from_(…)` and `rest/v1/…`
across `backend_app`, `api_ws`, `scripts`, `dev_tools`, `infra`, `monitoring`,
`observability` and `algo22-terminal/src`. The only PostgREST reference to any of
the three in the entire tree is this change's own `SELECT` in
`eligibility_gate.py`. The Alembic hits are `op.create_table`, which is DDL.

---

## 8c. I turned `main` red, and CI is what told me

PR #314 was merged **while `Frontend Build & Unit Tests` was still failing on
it**, at `220cbfb3` — the commit before the fix, which had already been written
and pushed to the branch. `main` carried the defect for about eighty minutes
until PR #315 (`0364861f`) landed and all 16 checks went green.

### The defect

`pages/Strategies` started reading `useFeature(FEATURES.MARKETPLACE_PUBLISH)`,
and `hooks/useEntitlements.load()` calls `isAuthenticated()` unguarded. A
`vi.mock` factory **replaces** the module, so an omitted export throws on access
rather than falling through — from inside a `useEffect`. Four test files render
that page; three had no `isAuthenticated` in their factory.

### Why the local run looked fine

```
Test Files  1 failed | 191 passed (192)
     Tests  3944 passed | 11 skipped (3955)
```

The one failed file was the unrelated Node 24 load error. I read the pass count.
The signal was the line underneath it:

```
Vitest caught 5 unhandled errors during the test run.
```

Which is what the guard's own docstring says it looks like: *"this surfaces as an
UNHANDLED ERROR with every test still reporting as passed, and vitest exits
non-zero anyway."*

### Two things that had already written this down

The guard existed, and its pinned list carried this note:

> *"None of these currently renders a component that reaches `useEntitlements`,
> which is the only reason they are green. That is a property of what they render
> today, not a guarantee, and it is exactly how the three known instances
> arrived."*

That is the whole incident, predicted before it happened. The property expired
for every file rendering `pages/Strategies` the moment that page reached the
hook, and I did not grep for them.

The note now records which four retired and why, and says that the way to retire
a remaining entry is to add the export rather than to wait for a page to reach
the hook.

### What this says about the session's own claims

Everything in §8a and §8b was found by measurement. **This one was found by CI,
after a merge** — which is the honest ordering and is recorded as such. The
value of the guard was not that it existed; it is that it names the failure mode
precisely enough that the fix took one reading of its docstring.

---

## 8d. Task 15 E, F and G — the audit this report had deferred

§8 originally recorded these as "not re-audited this session". They have been.

### F — protect strategy logic. **HOLDS**

The brief calls this "a critical marketplace requirement", so it was audited by
following the subscriber's four reachable paths rather than by reading the test
names.

| Surface | Finding |
|---|---|
| `project_listing` | builds its output by **explicit assignment**, not by filtering. A column added to `library_strategies` by a future migration is not exposed by default — which is the difference between an allow-list and `select("*")` followed by `pop()`. |
| P-47 property | no non-owner response — projected, enriched, error body, structured error envelope, or Paper_Channel event — carries any Protected_Logic substring at any nesting depth. **And** the projection is asserted *invariant* when the source row carries the logic, which catches a derivative that shares no three-character run with the source. The test first asserts the source row *would* trip the check, so a projection that dropped the columns for the wrong reason still fails. |
| subscriber **deploy** (`POST /{library_id}/deploy`) | never touches the blueprint. It is built by `deployment_runtime.start_recorded_deployment` **from the row, through the runtime's own service client** — not through the subscriber's request path. `_owner_version_label` selects `id, version` only; `strategy_versions`' two protected columns (`blueprint`, `execution_graph`) are not in the projection, and the pair is bound by `COLUMN_CONTRACT`'s `marketplace_deployment` entry. |
| subscriber **paper session** | `paper_sessions` has **no graph column at all**. Measured against the production column list: `listing_id`, `source_strategy_id`, `version_id`, and a `config` jsonb whose keys are execution parameters (fee rate, slippage, participation rate, precisions, permitted order types and sides). `listing_id` and `strategy_id` are mutually exclusive in the request and the artifact is resolved server-side. |

One design decision is worth repeating because it is the load-bearing one: the
deploy path accepts that **an unstartable marketplace strategy leaves a row
marked `failed` with a reason**, rather than refusing before the insert as the
owner's own `deploy_version` does. The runtime derives every input from the row,
so the row must exist first. The router's own comment states the trade: *"for a
subscriber deploying someone else's strategy that is the better of the two: a
recorded refusal they can read beats a 422 about an artifact they are not allowed
to see."* The quota is released either way.

### E — subscriber functionality. **Three of five work; one is a real defect**

| Capability the brief requires | State |
|---|---|
| View all qualifying backtest results | works — evidence summaries reach the projection, outcomes only (`CONDITION_OUTCOME_METRICS`) |
| Review the verified live performance history | **not re-verified this session** |
| Backtest the subscribed strategy | **not implemented** — see below |
| Start paper trading | works |
| Deploy live for the entitlement period | works |

**The defect.** `library_entries.allowed_actions_for_subscribed` offers
`run_backtest` to an entitling subscriber whose Listing permits it;
`pages/Strategies` links it to `/app/backtest?listing_id=<id>`; and
`pages/Backtester` read only `strategy_id`. No backend route accepts a listing
for a backtest — every one is keyed on `strategy_id` (`routers/strategies.py:2889`
plus seven in `routers/strategy_operations.py`; `routers/library.py` has no
backtest verb at all).

So a paying subscriber landed on a Backtester whose selector **could not** contain
their listing, with nothing explaining why. The selector being empty of it is
correct — MKT-05 keeps the Listing's logic out of the subscriber's tenant — which
is exactly why the page had to say so. The one state indistinguishable from a
broken page is a correct page that explains nothing.

PR **#318** makes the refusal honest: the parameter is recognised, the reason is
named, and the two actions that do work are offered. It deliberately does **not**
withhold `run_backtest` server-side (that would silently remove a capability
Requirement 12.3 says to offer) and does **not** implement the feature. What
would close it is a route taking a `listing_id`, resolving the entitlement and
running the engine against the owner's version without returning the definition —
the shape the paper-session route already has.

### G — marketplace security. **Mechanisms named, not independently re-audited**

Every item the brief lists has a named mechanism, and this is the honest depth of
the check: the mechanisms were located and read, not exercised against an
adversary.

| Brief's concern | Mechanism |
|---|---|
| Unauthorized strategy access | `subscriber_operation_guard.refuse_if_entitled_subscriber`, `tests/test_subscriber_restricted_operations.py` |
| Source-code leakage | §F above |
| Subscription bypass / expiry failure | `entitlement_resolver`, `expiry_sweep`, `workers/marketplace_expiry_worker.py`, `migration 056` |
| Duplicate charges / webhook replay | `uq_settlement_reference_reversal`, `settlement_service.settle` as the only writer of `status='active'` |
| Privilege escalation, incorrect ownership | `tests/property/test_tenant_isolation_matrix.py` (155 cells) |
| Revenue / transaction records | `migration 059` creator payout atomicity, `migration 050` settlement transition |

**The 30-day entitlement period was not independently verified against the
clock** — `subscription_period.py` and `test_entitlement_expiry_boundary` exist
and pass, but no measurement was taken this session. That is the sharpest
remaining unknown in T15, because an entitlement that fails to expire is a
revenue defect that looks like nothing until it is large.

---

## 8e. **URGENT — every production deploy is blocked, and has been since today**

Found while confirming that this session's commits reached production. They have
not, and neither has anything else merged today.

### The measurement

`03 Deploy` fails on `main` at the registration step:

```
[ecs_deploy] Basing new revision on the running revision: vyomquant-api:351
Task definition registration failed: Deployment safety validation failed:
    CRITICAL: Memory is not 2048, got 4096
```

Read from AWS (task-definition metadata only, no write):

| revision | cpu | memory | registered |
|---|---|---|---|
| **351 — RUNNING** | 1024 | **4096** | 2026-11-18 |
| 350 | 1024 | 2048 | 2026-11-18 |
| 349 … 344 | 1024 | 2048 | 2026-11-16/17 |

### Why that stops every deploy

`scripts/ecs_deploy_and_diagnose.py` bases each new revision on the **running**
revision and then validates it:

```python
if task_def.get("memory") != "2048":
    return False, f"CRITICAL: Memory is not 2048, got {task_def.get('memory')}"
```

Revision 351 was registered with 4096 **outside the pipeline**, so every
subsequent deploy inherits 4096 and is refused by the pipeline's own safety
check. The gate is working exactly as designed: it is refusing to reproduce a
change nobody reviewed.

### The probable trigger, from the service events

```
08:50:17  (task aa03cd2f…) (port 8000) is unhealthy in (target-group …)
08:50:17  has stopped 1 running tasks: (task aa03cd2f…)
08:53:28  has reached a steady state.
14:54:05  has reached a steady state.
```

A task went unhealthy and was replaced, and the memory was doubled shortly after.
The service is currently **healthy, 1/1, steady state** on 351 — so this is not
an outage. It is a pipeline that can no longer ship.

### This needs an owner decision, and deliberately was not "fixed"

Two reconciliations, opposite in meaning:

1. **4096 is correct** — the task was genuinely memory-starved at 2048. Then the
   validator's expectation must be raised to 4096 at
   `scripts/ecs_deploy_and_diagnose.py:257`, so the pipeline reproduces what is
   running.
2. **4096 was a reaction to the unhealthy task** — then the cause of the
   unhealthy check is the thing to fix, and the revision should go back to 2048.

**Editing the validator to accept 4096 would make the gate agree with an
unreviewed change, which is the one thing the gate exists to prevent.** So it is
reported rather than changed.

### One structural finding underneath it

The task definition is **not managed as code**. The only `"memory": "2048"` in
the repository is a rollback snapshot
(`infra/rollback/taskdef-vyomquant-api-145-pre-domain.json`), not a live
declaration — the deploy script mutates the running revision in place. That is
why a console edit can silently become the base of every future deploy, and why
the only thing standing between it and production is a hardcoded literal in a
Python script.

### Consequence for this session's work

**Migration 073, the privilege revoke and MKT-07's code are merged but not
live.** The deployment order in §10 still applies and has not been executed.

---

## 9. Security posture of these changes

| Requirement | Status |
|---|---|
| No secret printed, committed or transmitted | met — production DSN via Secrets Manager, ECS read was **names only** |
| Production reads only | met — `SET TRANSACTION READ ONLY` on every production query |
| No production write, no DDL against production | met |
| No live order placed | met — no execution test ran against a venue |
| Risk controls, allowlist, kill switch untouched | met |
| Tenant isolation preserved | met — the gate's fifth read is owner-scoped and tested against a foreign tenant and a foreign strategy |
| Least privilege | **improved** — 26 privileges revoked, 0 granted beyond existing reads |
| Migration reversibility | met — `rollback/073_notif02_rollback.sql`, tested apply → rollback → re-apply |
| Forgery path closed | **measured** — succeeded before, refused after, and the refusal is a regression test |

The 26 revoked privileges: 21 on `execution_records`/`orders`/`positions`
(MKT-07) and 5 on `notification_settings` (NOTIF-02). `authenticated SELECT` is
retained on the three ledgers — had the revoke been one privilege wider it would
have broken a read, and the snapshot diff is how that was confirmed rather than
assumed.

---

## 10. Deployment and rollback

### What is deployable

Three commits, two schema changes, no data migration.

1. Apply migration **073**. The DDL-capable route is the **session pooler** on
   port 5432; the transaction pooler on 6543 cannot run multi-statement
   transactions.
2. Re-apply `migrations/hardening/999_privilege_hardening.sql`. Idempotent by
   design and re-applied on every provision, so the privilege changes are
   self-healing.
3. Deploy the application image carrying `eligibility_gate`,
   `evidence_validator`, `routers/library.py`, `routers/user.py` and
   `pydantic_models.py`.
4. Deploy the frontend bundle.

**Order matters in one direction only.** The privilege revoke is safe before the
code (nothing uses those grants) and the migration is safe before the code
(nothing writes the constrained shape). The code is *not* safe before the
migration in one respect: `MIN_LIVE_HISTORY_HOURS` would be enforced while the
`notification_settings` constraint is absent, which is merely the current state
for that half. There is no window in which the set is worse than today.

### Rollback

- **073**: `backend_app/migrations/rollback/073_notif02_rollback.sql`. Its header
  is explicit that rolling back is a regression rather than a neutral undo — a
  mandatory safety alert becomes switchable off again at the database, and only
  `routers/user.py` still refuses it. **No data is lost**; it drops a constraint
  and restores two defaults.
- **The privilege changes** revert by editing the hardening file and re-applying,
  deliberately decoupled from the migrations so either can be undone without the
  other.
- **MKT-07's code half** reverts by reverting `2ae8c78c`. Note what that
  restores: a floor of three backtests and no live-history requirement, so a
  strategy that has never traded live can be listed again.

### Production database caveat, unchanged

`db.wrkexcjqnidkdrayhlsi.supabase.co` is IPv6-only and unreachable from this
host; all production access goes through the poolers. Production sits at a
deliberate Alembic pin (`d97ffff9c3bb`) — the same revision CI's `database-tests`
job pins. Do not "upgrade to head" to make a comparison match.

---

## 11. What this report does not claim

The brief's final acceptance rule is that a feature is complete only when its
frontend, backend, APIs, database integration, authorization, error handling and
end-to-end workflow all work and dependent functionality is intact.

By that standard: **Task 15 D and B are complete, Task 10's mandatory-alert floor
is complete, and Task 17 was already complete.** Everything in §8 is not, and is
not described as such anywhere above.

Nothing was mocked. No button was made to look functional over a broken backend.
No test was disabled to produce a green run — seven previously-green tests were
*corrected*, and in each case the correction is recorded at the line with what
was wrong. No hardcoded value was introduced to simulate database connectivity.

The single most valuable follow-up remains the one that is not on the task list:
**the CI guard that fails a router or migration landing without its paired
artifact.** Four outages have now come from that one gap, and the fourth happened
despite the procedure being documented twice.
