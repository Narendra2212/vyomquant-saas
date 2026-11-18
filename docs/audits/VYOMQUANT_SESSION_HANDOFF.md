# VYOMQUANT — session handoff

Last updated 2026-11-16 (second session appended at §8). Read this first in a new
session; it replaces re-auditing.

---

## 1. Where the repo is, and where it is NOT

| Path | What it is |
|---|---|
| `c:\Users\aa\Downloads\vyomquant-saas-main\vyomquant-saas` | **the canonical git clone.** All work happens here. |
| `c:\Users\aa\Downloads\vyomquant-saas-main` | Kiro's workspace root. A **stale non-git copy**. Never edit. `read_file` with a bare relative path resolves HERE and fails — always use the full path above. |
| `.../vyomquant-saas/algo22-terminal/vyomquant-saas/` | untracked 3,269-file nested clone. Keep it out of commits: **never `git add -A`**, always path-scoped. |

---

## 2. Infrastructure access — all measured, all working

**AWS.** `aws.exe` on PATH was a broken extract at `C:\Users\aa\aws_cli_extract`
that hung forever. **It has been deleted** (172 MB reclaimed, and the CLI is no
longer shadowed by it). Use boto3 regardless. Profile `vq-admin` →
`arn:aws:iam::273709947018:user/vq-deployer`, region `ap-southeast-1`.

**Production database.** Secrets Manager `vyomquant/production/database_url`.
- `db.wrkexcjqnidkdrayhlsi.supabase.co` is **IPv6-only** and this host has **no IPv6 route**. The direct endpoint is unreachable regardless of credentials. Do not retry it.
- **Transaction pooler** `aws-1-ap-southeast-1.pooler.supabase.com:6543` — reads only, no multi-statement transactions.
- **Session pooler** same host **:5432** — dedicated backend, **DDL-capable**. This is the route for migrations.

Helper: `.kiro-tmp/vqdb.py` → `prod_ro()`, `prod_ddl()`, `local()`. Verified
working again this session (read-only production query of `billing_invoices`).

**Local verification cluster.** PostgreSQL **16.2** from the `pgserver` wheel's bundled Windows binaries (no Docker, no admin):

```powershell
& 'C:\Users\aa\AppData\Local\Programs\Python\Python312\Lib\site-packages\pgserver\pginstall\bin\postgres.exe' `
    -D 'C:\Users\aa\vq_pgdata' -p 55432
```

DSN `postgresql://postgres:postgres@127.0.0.1:55432/postgres`. Provisioned via:

```powershell
python scripts/provision_database.py --dsn <dsn> --apply --create-substrate
```

`--create-substrate` synthesises the Supabase platform objects (`auth` schema,
`auth.uid()/role()/jwt()`, the `anon`/`authenticated`/`service_role` roles,
pgcrypto). Without it, migration 020's preflight refuses.

**Gotcha:** `tests/conftest.py` blanks `DATABASE_URL` unless
`AERORA_TEST_DATABASE_URL` is also set. Set **both** or tests silently run
against SQLite.

**Disk:** chronically tight. Was 1.75 GB free of 111 GB at the start of session
two; ~1 GB reclaimed via `npm cache clean --force` and deleting
`aws_cli_extract`. Remaining fat if needed: `vq-work` (1.28 GB, a second clone
with no uncommitted work), `wt-rzp` (345 MB), `ci_logs` (100 MB).

---

## 2a. URGENT — every production deploy is blocked (found 2026-11-18)

`03 Deploy` fails on `main` at task-definition registration:

```
Basing new revision on the running revision: vyomquant-api:351
Deployment safety validation failed: CRITICAL: Memory is not 2048, got 4096
```

**Measured.** Revision **351 (running) has memory 4096**; revisions 344–350 all
have 2048. 351 was registered today, outside the pipeline. The deploy script
bases each new revision on the *running* one, so every deploy now inherits 4096
and is refused by `scripts/ecs_deploy_and_diagnose.py:257`.

The service is **healthy, 1/1, steady state** — this is not an outage. It is a
pipeline that cannot ship. The service events show a task went unhealthy on the
target group at 08:50 and was replaced, which is the probable reason someone
doubled the memory.

**Owner decision, and do not shortcut it.** Either 4096 is right (raise the
validator's expectation at `:257`, so the pipeline reproduces what runs) or it
was a reaction to the unhealthy check (fix that cause, return to 2048). **Editing
the validator to accept 4096 makes the gate agree with an unreviewed change,
which is the one thing the gate exists to prevent.**

Underneath it: the task definition is **not managed as code**. The only
`"memory": "2048"` in the repo is a rollback snapshot
(`infra/rollback/taskdef-vyomquant-api-145-pre-domain.json`); the deploy script
mutates the running revision in place. A console edit therefore becomes the base
of every future deploy, with one hardcoded literal in a Python script as the only
check.

**Consequence:** migration 073, the `execution_records` privilege revoke and
MKT-07's code are merged but **not live**.

---

## 3. URGENT — not a task, an exposure

The production database password was pasted into a chat transcript and is
**still live**. Rotation sequence (executed successfully once, so it is proven):

1. Supabase dashboard → Settings → Database → Reset password
2. Update `vyomquant/production/database_url`. **Percent-encode `@` as `%40`** — an unencoded `@` in URI userinfo mangles the host and causes a second, more confusing outage.
3. Force new deployments on all **7** ECS services in cluster `vyomquant-cluster`, region `ap-southeast-1`
4. Verify target group `vyomquant-api-tg` reports `healthy`

The window between (1) and (2) is a **full outage** — every ECS task reads that
secret at start. One such outage already happened; see §6.

---

## 4. Task state

**Done and verified (14):** T01, T03, T04, T05, T06, T07, T08, T11, T12, T13,
T14a. (T07 and T11 closed in session two — see §8.)

**Remaining (5), in recommended order:**

### T10 notifications
No unique constraint for dedupe, no `CHECK` on severity, no retry/outbox.
`dispatch_user_notification_sync` is never called. `email_notifications` is
written and **never read** — confirm the same for every other preference field.
The "mandatory-alert floor" has no schema anchor.

### T09 support
Attachments **do not exist at all** — needs a storage backend decision (S3 vs
Supabase Storage) before code. Then replies and notification wiring.
`migrations/add_support_tables.sql` is the starting point.

### T17 light mode
~**35 live hex-alpha concatenations** (`${token.brand.base}15`) in
`common/primitives.jsx` (7), `NotificationCenter.jsx` (9), `SupportCenter.jsx`
(5), `StrategyDetail.jsx` (4), `ResearchConsole.jsx` (2), plus
`PaperTrading.jsx`, `FirstTradeWizard.jsx`, `App.jsx`. `token.compat.*` = 0
usages, `getContext(` = 0. **Must land atomically** — `prebuild` runs
`gen-tokens.mjs --check`, so a stale `tokens.js` turns CI red.

### T02 dashboard grid
Needs fresh screenshots from current `main` to confirm the symptom still exists.

### T16 performance
**Partially blocked** — no Sentry, no API or DB timing available to baseline
against. Bundle size, build time and render behaviour are measurable locally.

### T15 marketplace — largest, do last
Read `.kiro/specs/marketplace-subscriptions-paper-trading/` first. Needs listing
flow, eligibility (5 backtests + 24h live), 30-day entitlement, protected
execution, and its own security review.

### Email — deferred by owner instruction
Audit is filed. Needs an owner decision on SES vs SMTP. Note the *Email
Notifications* switch is live in the UI today and promises undeliverable alerts.

---

## 5. Re-recording `staging/provisioning_snapshot.json` — the trap

Any migration adding an object fails the drift gate until the snapshot is
re-recorded. **Take the candidate from CI, not locally** (CI runs PostgreSQL
17.12, local is 16.2; a version-skewed snapshot just moves the failure):

```powershell
gh run download <run-id> -D C:\Users\aa\artNNN
python C:\Users\aa\Downloads\vyomquant-saas-main\.kiro-tmp\snap_diff_main.py
```

**Verify before committing.** The rule is **`+N -0`**: additions only, and every
addition traceable to the migration. An earlier candidate, taken before the
branch was merged with `main`, showed `uq_strategies_owner_name_live` and 068's
ledger row as **REMOVED** — committing it would have deleted T05's index from the
baseline and failed the next unrelated PR for no traceable reason. Rebase on
`main` first, then confirm nothing shows as a removal.

**Order the two commits correctly.** See §8's first finding: the re-record must
be pushed *before* the PR is merged, or it is orphaned.

---

## 6. Things that bit, so they don't bite twice

- **PowerShell is hostile here.** Output silently truncates on multi-statement blocks, quoting mangles multi-line input, and non-ASCII in Python `print` dies on `cp1252`. Reliable pattern: write a `.ps1` or `.py` file, run it, redirect to a log, then `read_file` the log. `git commit -F <file>` over `-m`, and verify the message file was written.
- **A change merged without its paired artifact re-recorded** has now caused CI outages **three** times: `55b284c3` (Task 13) left `surface_profile.json` stale; migration `067` and then `069` left the provisioning snapshot stale. Check both artifacts whenever you touch a surface or the schema.
- **Production outage, 19:02–19:13.** A dashboard password reset left Secrets Manager stale → ALB health checks failed → ECS replace loop. Fixed by repairing the secret and force-redeploying all 7 services.
- **`pg_ctl start` hangs** when its stdout is a pipe (the detached `postgres.exe` inherits the handle). Launch `postgres.exe` directly as a background process instead.
- **Baselines:** `tests/regression/baseline/*.json`, re-record one with `python tests/regression/capture_baseline.py <name>`. Only re-record when the change is *requested* — and say why in the commit.

---

## 7. Corrections that had to be made — the pattern matters

1. Claimed production was **missing 12 tables** and called it critical. It wasn't: local ran `alembic upgrade head` while production sits at a deliberate pin (`d97ffff9c3bb`, the same revision CI's `database-tests` job pins). Retracted.
2. Recommended pointing `record_risk_violation` at **migration 028's table**, and it was approved. 028 has no `user_id` and deliberately no owner RLS policy — its own header explains why. Following my own plan would have put customer data where customers can't read it. Built migration 069 instead.
3. A **Task 13 commit** left `surface_profile.json` un-recorded, turning `main` red and blocking every PR until CI was checked rather than local green runs.
4. **Session two:** the handoff said T11's enforcement half was outstanding and that `webhook_idempotency`'s fail-open was a defect. Measurement contradicted both — see §8.

All four were caught by measuring, not by reasoning harder. **Measure first, then
decide** — five of the original seventeen tasks turned out not to be defects.

---

## 8. Session two (2026-11-16, later) — what changed and what the measurements said

### `main` was red, and the cause was an ordering mistake, not a bad snapshot

`main` failed the `Provisioning` drift gate from 16:13. The previous session's
note predicted a stale snapshot and gave the re-record procedure. The actual
cause was different and worth recording, because the procedure does not prevent
it:

```
$ git merge-base --is-ancestor 94e3ed75 origin/main ; echo $?
1
```

PR #283 added migration 069 and merged as `9ac3129d`. The paired re-record was
committed **afterwards**, as `94e3ed75`, onto a branch GitHub had already merged
— so it was orphaned and `main` carried the migration with the pre-069 baseline.

**The lesson is about order, not content:** push the artifact re-record *before*
the merge completes, or it lands nowhere. Fixed by PR #286, which took only
`staging/provisioning_snapshot.json` from the orphaned commit — deliberately not
a cherry-pick, because that commit's tree predates PR #285 and would have
reverted T07's billing-history work in `billing.py` by 35 lines.

A `paths`-triggered CI guard that fails when a migration lands without a
snapshot re-record would close this class of failure. Not built; recommended.

### T07 — CLOSED. The read path was fine; the table could never hold a row

The billing-history read landed in PR #285 and is correct. Its **source table was
empty and could never be anything else**: the only writer named five columns the
schema has never declared — `provider`, `provider_payment_id`, `amount_usd`,
`amount_inr`, `plan`. PostgREST rejects a whole statement on one undefined
column, and the call sat in `try/except … logger.warning`.

Measured read-only against production **and** a cluster provisioned from repo
source; the two agreed exactly: 11 columns, 5 of the 9 sent missing, **0 invoice
rows in production**.

The repository already knew — all five were in
`KNOWN_UNDECLARED_COLUMN_DEFECTS`, and the launch-hardening spec states "no
invoice row has ever been written". Both deferred it because `billing.py`
carried a parallel workstream's uncommitted changes. **That reason expired when
PR #285 landed.**

**The second consequence was not in the original report and is worse:**
`_revoke_plan_for_refunded_razorpay_payment` finds a purchase with
`.select("user_id,plan").eq("provider", …).eq("provider_payment_id", …)` — three
of the same undeclared names — and that read is **not** wrapped. So the refund
webhook raised, Razorpay redelivered into the same failure, and **a fully
refunded customer kept their plan.**

Fixed by migration **070** (PR #287): the five columns, four CHECKs, and a
*partial* unique index on `(provider, provider_payment_id)` that both
de-duplicates a redelivery and serves the refund lookup. The handler now sends
`None` rather than `""` (an empty string is a real value identifying nothing and
would let the first id-less capture block every later one), treats `23505` as
success, and escalates anything else to CRITICAL with every field needed to
reconstruct the row. It deliberately **does not raise** — Razorpay would
redeliver and `_record_plan_period` extends the paid period from its current
end, so a redelivery would grant a second month for one payment.

`KNOWN_UNDECLARED_COLUMN_DEFECTS` is now **empty** (24 → 23 → 10 → 5 → 0).

**Privilege, found while measuring:** `anon` held SELECT/INSERT/UPDATE/DELETE on
`billing_invoices`, and `authenticated` held the same four through a `FOR ALL`
owner policy — a customer could file or delete their own invoice. The sibling
`invoices` table already had the corrective block; this one was missed. Fixed in
`999_privilege_hardening.sql` (which runs last and is re-applied every provision,
so it is self-healing). **Why the existing preflight missed it:** that guard
fires on tables granting `anon` SELECT **with RLS disabled**, and this table has
RLS enabled with no anon policy — so no anon row was ever reachable. Defence in
depth, not an open door, and stated at that size.

Not claimed: no reconciliation job exists for a row lost to a non-duplicate
failure, and `amount` (migration 006's column) is still unpopulated.

### T11 — CLOSED as already implemented. The handoff was conservative

The handoff listed "order-path enforcement, fail-safe when risk config is
unavailable, and confirming no order path reaches an exchange adapter without a
risk gate" as outstanding. Measurement says the enforcement is already there and
regression-tested:

- **Venue boundary** (`exchange_executor.place_order`, the canonical boundary):
  `verify_and_consume_token`, then `require_live_authorization` — which refuses
  unless a signed authorisation carries the **complete** `REQUIRED_CHECKS` set
  (which includes `"risk"` and `"capital"`) for *this* order on *this* venue —
  then `assert_sandbox_only_policy`, then `enforce_kill_switch()`. All **raise**
  rather than return, because a returned verdict can be dropped.
- **Gateway** (`execute_with_idempotency`): `if self._live_risk_approved is not
  True` refuses, logs CRITICAL, and returns before any exchange call.
  `risk_approved` defaults to `None`, so **a caller that does not evaluate risk
  cannot place a live order** — fail-closed by construction, which is the
  "fail-safe when risk config is unavailable" requirement.
- **Fail-closed halt:** `is_active()` engages its local latch and returns True on
  `RedisUnavailable`, so an unreadable halt state refuses rather than permits.
- **Callers:** `master_executor.py:1936` passes a real verdict
  (`risk_approved=(verdict == RiskVerdict.APPROVED)`). The one hardcoded
  `risk_approved=True` (`:2334`) is the RT-21 close — spot-only, selling exactly
  the recorded exposure, which cannot open or enlarge a position — with a stated
  `risk_reason` and its own regression test. A bounded exemption, not a bypass.

Verified by running the surface rather than reading it: **153 passed** across
`test_trade04_live_order_gate_at_venue_boundary`,
`test_kill03_authoritative_global_halt`, `test_trade02_kill_switch_fails_closed`,
`test_reduce_only_not_gated_on_capital_regression` (which pins both "the checks
that matter still run" and "an opening trade still runs every check"),
`test_guard01_spot_margin_and_fail_closed_risk_status`,
`test_paper01_paper_live_isolation`, and
`test_live_orders_are_bounded_by_account_equity`.

### Webhook idempotency — reassessed, and the fail-open is deliberate

The handoff flagged `acquire_webhook_processing` returning `True` when
`get_service_supabase_async()` is `None` as a defect. Reading the module, that
branch is documented as intentional: the durable layer sits **underneath**
`billing.py`'s own Redis check, and refusing when unconfigured would turn "this
optional hardening is not configured" into "every webhook is a duplicate". The
*configured-but-unreachable* case already fails **closed**. Webhook
authentication is HMAC-SHA256 over the raw body with `hmac.compare_digest` and no
dev/test bypass — already correct.

What remains genuinely open there is narrower than stated: a failed
`mark_webhook_completed` leaves a `processing` row that a redelivery reclaims
after 60s and reprocesses in full. Worth fixing; not fixed here. The honest
prerequisite is to confirm whether production actually sets
`SUPABASE_SERVICE_ROLE_KEY` — if it does not, the durable layer is off in
production and the fix is configuration plus a startup assertion, not a code
change.


---

## 9. Session three — T10 closed, and a four-cause dependency incident

### T10 notifications — CLOSED. Two root causes, either fatal alone

`public.notifications` held **zero rows in production**.

**(a) The payload could not insert.** `create_notification` sent
`"id": f"notif_{uuid4().hex[:12]}"` into a column declared
`id uuid NOT NULL DEFAULT gen_random_uuid()`. PostgreSQL rejects that with
SQLSTATE **22P02**. Every notification, every time, on both databases.

**(b) Almost no caller asked for persistence.** `create_notification` writes to
Supabase only inside `if supabase:`, and `dispatch_user_notification` defaulted
`supabase=None` and forwarded it. AST over `backend_app`: **1 of 19 call sites
passed a client.** The sharpest case was `routers/risk.py` — all three sites pass
`ws_manager` but not `supabase`, so a risk-limit breach was broadcast to a live
socket and never stored. A customer not watching that tab never learned.

Fixed by `_resolve_persistence_client` in the dispatcher (one change, not 18) and
by omitting `id` so the column default generates it. Migration **071** adds
`idempotency_key` with a partial unique index, replacing an in-process dict that
died on restart, could not see a second task, and evicted at 5,000 — so the
docstring's "skips duplicate events" claim is now true. Severity gained a CHECK
whose permitted set was **derived by AST** from the call sites unioned with what
the frontend styles; narrowing it to the UI's set would have rejected the one
real `critical` alert (the risk kill-switch). Failed dispatch of a
warning/error/critical now logs at ERROR instead of DEBUG.

Deleted `dispatch_user_notification_sync`: zero references, and unsafe twice over
(`get_event_loop()` raises in a thread with no loop; `create_task` with no
retained reference can be GC'd before running).

**Still open in T10, stated plainly:** no retry/outbox; no preference filtering
(nothing on the dispatch path reads `notification_settings`, so no preference
suppresses anything today — a test pins that absence beside the mandatory event
set); and **trade-execution and order-failure notifications are not wired at
all** because the execution engine and bot runner are synchronous.

### The dependency incident — four root causes behind one another

Seven auto-merged dependabot PRs (#296–#302) turned main red with **five failing
workflows**. Only two were resolver errors, and because every job downstream of
"install the dependencies" was *skipped* rather than attempted, the other three
were invisible until the resolver was fixed. They had to be peeled one at a time:

| # | cause | symptom |
|---|---|---|
| 1 | `@vitejs/plugin-react` 6.x needs peer `vite@^8`; vite stayed at 7 | npm ERESOLVE |
| 2 | `uvicorn[standard]` 0.54 needs `websockets>=13`; pin was 12.0 | pip ResolutionImpossible |
| 3 | **SQLAlchemy 2.1 resolves bare `postgresql://` to psycopg v3**, not psycopg2 | `ModuleNotFoundError: No module named 'psycopg'` — Database Tests + Provisioning, the latter at STEP 0c before any migration ran |
| 4 | **jsdom 31 requires Node `>=22`**; CI runs Node 20 | `[vitest-pool]: Failed to start forks worker`, 190 unhandled errors — ESLint and `vite build` both passed; the suite never executed |
| 5 | Docker base images jumped `python:3.11-slim` → `python:3.15-slim`; `numpy==1.26.4` supports `<3.13` | image would not build, so Trivy never scanned |

Fixes: `websockets` → 13.1 and `@vitejs/plugin-react` → 5.2.0 (the newest
accepting vite 7 *and* 8); `sqlalchemy` **held** at 2.0.35 with the driver
decision written at the pin; the frontend set restored to the last green commit;
all seven `FROM python:` lines back to 3.11.

**The frontend restore was not laziness.** #301/#302 also took `react`/`react-dom`
from ^18.3.1 to **^19.3.1** — a framework major by auto-merge, with
`@testing-library/react` bumped 14→16 to compensate. Whether the app is correct
under React 19 is unknown *and untested*, because jsdom 31 meant the suite never
ran on any commit carrying it.

### Two process lessons, both earned the hard way

1. **The owner merges PRs quickly and manually** (auto-merge is disabled
   repo-wide; #303, #304 and #305 were all merged by `Narendra2212` within
   minutes of going green). So a paired artifact commit must be pushed **before**
   the PR is opened, or the PR must carry a visible hold notice. #303 merged
   without its snapshot for exactly this reason, leaving main with 071 against
   the pre-071 baseline.
2. **A force-push does not always fire `synchronize`.** #304 merged at the
   *earlier* head, shipping half the fix. Verify the merged SHA is the one you
   intended, not just that the PR is green.

### Dependabot configuration is the standing recommendation

Grouping a **major** plugin bump with a **minor** bump of the package it peers on
produces a set that installs for neither. Second occurrence (`216b7481` was the
first), and this time it cost five workflows and five root causes. Either split
the frontend group or add `ignore` rules for majors. Not done.

---

## 10. Session four (2026-11-18) — the gate was reading forgeable evidence

Full report: `VYOMQUANT_SESSION_FOUR_REMEDIATION_REPORT.md`. Branch
`mkt07/listing-eligibility-gates`, three commits: `2ae8c78c`, `48a43862`,
`d85c4ea0`.

### MKT-07 — T15 D closed, and the privilege under it was the real finding

The brief requires 5 completed backtests and 24 h of verified live trading before
a strategy can be listed. The tree enforced **3** and **no live criterion at
all**. Both are now enforced: `evidence_validator.MIN_CONDITIONS` is 5, and the
Eligibility_Gate has a fifth owner-scoped read of `execution_records` behind a new
`MP_LIVE_HISTORY` criterion. `EVALUATOR_VERSION` → `1.1.0`, because an admission
stored under 1.0.0 was decided under weaker rules.

**The part worth remembering.** A gate is only as good as the evidence it reads,
and `authenticated` held INSERT/UPDATE/DELETE on `execution_records`. Measured on
a cluster provisioned from repo source, as that role with
`request.jwt.claim.sub` set to the customer's own id: a fabricated `COMPLETED`
fill backdated 48 hours **inserted successfully**, an existing row's
`filled_size` **updated**, and a row **deleted**. The owner policy is `FOR ALL`
with `auth.uid() = user_id OR auth.uid() = tenant_id`, so the customer satisfies
it by construction — RLS was correct, the privilege was wrong. The browser holds
the anon key, so PostgREST is reachable from any session.

Revoked for `execution_records`, `orders` and `positions` in
`999_privilege_hardening.sql` SECTION 1b, keeping `authenticated SELECT`. Safe
because **zero** PostgREST writers exist for those three tables anywhere in the
repo (AST over `backend_app` + grep elsewhere); every real write is SQLAlchemy as
the table owner.

**Still open, and pinned by a test that fails when fixed:**
`strategy_backtests` is the other half of the evidence and is writable by the
same customer — but there the app's own writer IS the caller's client
(`backend/backtest_service.py:336`, used at `:451`, `:582`, `:807`). Revoking
would stop every backtest persisting. The fix is to move those three writes to
the service-role client.

### A two-session blocker resolved: production DOES set SUPABASE_SERVICE_ROLE_KEY

Verified by reading all 7 ECS task definitions in `vyomquant-cluster`, **names
only**. So the durable webhook idempotency layer is active in production, and the
`strategy_backtests` writer move is feasible. Both were previously recorded as
"needs configuration confirmed first".

### NOTIF-02 — the panel claimed an invariant and offered the switch that broke it

`NotificationPreferences.jsx` printed "Mandatory risk alerts … cannot be
disabled" directly below a switch that disabled them. Nothing enforced it.

**The trap:** the column default declares `{system, stop_loss, risk_alerts,
trade_fills}` and **no stored row has ever carried any of those keys**. All four
production rows carry the eight `NotificationEvents` fields instead, because the
only writer always sends the whole Pydantic object. A CHECK against `risk_alerts`
would have constrained a key no row has and read like an enforced invariant.
Measure the stored vocabulary before constraining a jsonb column.

Migration **073** constrains `kill_switch_activated`, `stop_loss_triggered` and
`new_login_detected` against `false`, corrects the dead defaults, and its
postflight **attempts the write it exists to refuse**. The model refuses the
payload (422) and the router answers 409 for a constraint violation; the panel
locks the three switches.

**Not built, and pinned by two tests rather than a note:** preference filtering.
Nothing on the dispatch path reads `notification_settings`, and **7 of the 8
preference keys have no producer** (AST: 17 dispatch sites, only
`kill_switch_activated` matches a key). Also measured: `public.notifications`
still holds **0 rows in production**, so NOTIF-01's fix is either undeployed or
has had no qualifying event — check that first after the next release.

### T17 was already done, and the "35 remaining" figure was a grep artefact

Stripping comments first — as the colour-literal guard does — gives **0** live
hex-alpha concatenations and 54 `alpha()` call sites. The 8 a naive grep finds
are all inside notes documenting the retired form. `gen-tokens.mjs --check`
passes. Nothing was changed.

Local-only artefact: `tests/unit/guards/tokens.generated.test.js` fails to LOAD
under Node 24 (`SyntaxError`) while its cases skip. CI runs Node 20 and the real
gate passes.

### main was red again — fourth paired-artifact failure

PR #309 (SUP-02) made the support ticket and comment writes return 503 and did
not re-record `tests/regression/baseline/surface_support.json`. CI runs all of
`tests/` at `01-pr-check.yml:251`, so every PR since inherited it. Fixed in
`48a43862`.

Note the capture tool **drops `_baseline_note_raises`** when it regenerates a
file, so restore any existing note by hand after re-recording.

**The guard is now four-for-four and still unbuilt:** a `paths`-triggered CI job
that fails when `backend_app/routers/**` or `backend_app/migrations/**` changes
without `tests/regression/baseline/surface_*.json` or
`staging/provisioning_snapshot.json` in the same push.

### Two new traps

1. **`not_` is a PROPERTY, not a method,** in supabase-py. Three test doubles had
   it as a method, so `.not_.is_(...)` raised `AttributeError` and collapsed every
   gate verdict to `unevaluable`. Seven green tests moved because of it.
2. **`provision_database.py` digests `read_text`, not bytes.** Universal-newline
   translation means the recorded checksum is over LF-normalised text, so a
   raw-bytes digest fails the drift gate on any CRLF checkout.

### I turned main red, and the mechanism is worth knowing before you do

PR #314 was merged **while `Frontend Build & Unit Tests` was still failing on
it**, at the commit before the fix. So `main` carried the defect for ~80 minutes
until #315. §8 of session three recorded that the owner merges quickly; what it
did not say is that a merge can land with a job still red. **Treat a red job as
blocking your own next push, not as something you will fix before the merge.**

The defect itself is the more useful lesson. `pages/Strategies` started reading
`useFeature(FEATURES.MARKETPLACE_PUBLISH)`, and `useEntitlements.load()` calls
`isAuthenticated()` unguarded. A `vi.mock` factory **replaces** the module, so an
omitted export throws on access — from inside a `useEffect`. Four test files
render that page and three had no `isAuthenticated` in their factory.

**Why I missed it locally.** The run said `1 failed | 191 passed`, the one
failure being an unrelated Node 24 load error. I read the pass count. The signal
was the next line:

```
Vitest caught 5 unhandled errors during the test run.
```

`tests/test_api_mock_factories_declare_isauthenticated.py`'s docstring describes
this exactly — *"surfaces as an UNHANDLED ERROR with every test still reporting
as passed, and vitest exits non-zero anyway."* **Read the exit code and the
unhandled-error count, never the pass count alone.**

And the pinned list's own note had predicted the shape: *"None of these currently
renders a component that reaches `useEntitlements` … That is a property of what
they render today, not a guarantee."* If you make a page read entitlements, grep
for every test that renders it before you push.

### Running the full backend suite on Windows: classify before you read

`pytest tests/` gives **40 failed, 16749 passed** on this host, and **36 of the
40 are the environment**:

* **24 `UnicodeDecodeError: 'charmap'`** — tests calling `open(path)` with no
  encoding, cp1252 locale, UTF-8 box drawing in the file. CI is Linux/UTF-8.
* **8** postgres-gated files (`test_sup02`, `test_bt04`) reaching the local
  cluster, which lacks migrations 069–072. **Re-provision it** and these go.
* **1** `test_protected_logic_containment` — Hypothesis drew the token
  `'subscribe'` and the containment check is a SUBSTRING match, so it collides
  with `errors.py:367`'s "You cannot subscribe to your own listing."
  `.hypothesis/` caches it so it replays. The real fix is whole-identifier
  comparison; it was deliberately not made, because loosening a Protected_Logic
  guard to get green is how leakage gets masked.
* **1** starlette version assertion, **1** pre-existing pydantic
  `AcceptanceRequest` error, **1** log line the classifier counted as an error.

So budget ~85 minutes for the run and expect to classify, not to see green. The
four that WERE real this session are listed in the session-four report §8a.

**`test_api_mock_factories_declare_isauthenticated` used to descend into the
nested clone** and report 18 phantom failures. Fixed this session; if another
guard does the same, that is the shape of the cause.

### Snapshot edits stayed surgical, both times

MKT-07: `−105 lines, +0` = exactly 21 revoked privilege rows. NOTIF-02: `+1
CHECK, +1 recorded migration, 2 defaults changed, −5 privileges`. Both verified
by the drift gate reporting `0 modified, 0 added` in every category. Local
PostgreSQL is 16.2 and CI is 17.12, so a whole re-capture would import version
skew — the surgical edit avoids it entirely.
