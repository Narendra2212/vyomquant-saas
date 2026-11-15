# VYOMQUANT — Master Engineering Audit (Phase 1: Discovery)

**Date:** 2026-11-15
**Auditor:** Kiro (Principal Architect role)
**Repository:** `github.com/Narendra2212/vyomquant-saas` @ `main` = `fc4e3a1a`
(merge of PR #271, 2026-11-15 16:38 +0530)
**Status:** Discovery complete. No code, schema, migration or infrastructure has been
modified. Implementation is gated on the two decisions in §13.

---

## 0. Reading this document

Every claim below is one of three kinds, and is labelled:

- **VERIFIED** — I read the code/DDL named by `file:line` and the conclusion follows from it.
- **MEASURED** — I executed something and report its output.
- **UNVERIFIED** — a hypothesis consistent with the evidence, not yet proven. Several
  reported symptoms fall here because they require a running system or live database.

Nothing in this document asserts that a workflow works end to end. No end-to-end workflow
was executed, because no deployed environment or database was reachable from this
workspace (§4).

---

## 1. Workspace hazard — resolve before any code is written

**MEASURED.** The directory Kiro has open as its workspace root is
`c:\Users\aa\Downloads\vyomquant-saas-main`. That directory is **not a git repository** and
is **not the project**. It is a container holding seven sibling directories, four of which
are near-identical copies of the product:

| Directory | git | Branch | HEAD | Newest file | Verdict |
|---|---|---|---|---|---|
| `vyomquant-saas/` | yes | `main`, clean | `fc4e3a1a` 2026-11-15 | 2026-11-15 17:05 | **CANONICAL** |
| `wt-rzp/` | yes | `kiro/protect-downloads-prefix` | `83410376` 2026-11-04 | 2026-11-04 | stale worktree |
| `vq-work/` | yes | `kiro/tenant01-real-vault-invariant` | `d089f54b` 2026-11-02 | 2026-11-02 | stale worktree |
| `vyomquant-saas-main/` | **no** | — | — | 2026-10-22 | **stale export, no VCS** |
| `supabase-demo/` | yes | `master`, 0 commits | — | — | scratch |

All three git copies share one remote: `https://github.com/Narendra2212/vyomquant-saas.git`.

`vyomquant-saas/` is the only copy whose HEAD matches the dates in the supplied screenshots
(11/15/2026). **All work must target `vyomquant-saas/`.** Editing the workspace root's
`vyomquant-saas-main/` copy would write three weeks of regressions into a directory with no
version control and no path to the remote — the changes could not be reviewed, tested in CI,
or rolled back.

**RISK: CRITICAL.** Not a defect in the product; a defect in the working environment that
would silently destroy the engagement's output. Blocks all of Part 2.

**Secondary finding.** The repository is **PUBLIC** (`gh repo view` → `"visibility": "PUBLIC"`).
For a platform that custodies exchange API credentials, this materially raises the cost of any
committed secret and makes the `08-secret-history-audit.yml` workflow load-bearing rather than
precautionary. Worth a deliberate decision; out of scope for Part 2.

---

## 2. Architecture overview

**VERIFIED** by directory and file inspection.

A Python/FastAPI monolith with worker processes, a React/Vite SPA, a Tauri desktop shell,
Supabase (PostgreSQL + Auth) for identity and persistence, Razorpay for billing, and
Redis/QuestDB for runtime state and time series.

```
vyomquant-saas/
├── backend_app/            FastAPI application
│   ├── routers/            29 HTTP routers (see §3.2)
│   ├── backend/            domain services (aggregation, executors, reconciliation)
│   ├── core/               auth dependencies, credential vault, safety audit
│   ├── workers/            background execution
│   ├── mds/                market data service
│   ├── strategies/         strategy runtime
│   └── migrations/         75 SQL migrations  ← authoritative
├── algo22-terminal/        React 19 + Vite SPA (+ src-tauri desktop)
│   ├── src/pages/          24 route pages
│   ├── src/components/     shared UI, incl. shell/ (nav, topbar, account menu)
│   ├── src/design/         design-layer data: tokens, pageFields, entitlements
│   ├── src/styles/         tokens.css ← single colour source of truth
│   └── tests/unit/         vitest suites incl. guards/ (CI-enforced invariants)
├── migrations/             11 SQL migrations  ← legacy, see §8.3
├── tests/                  711 pytest files
├── .github/workflows/      8 workflows
└── docs/, infra/, terraform/, monitoring/
```

**MEASURED** file census (excluding `.git`, `node_modules`, `__pycache__`, `dist`):
1,761 `.py` · 236 `.jsx` · 190 `.js` · 106 `.sql` · 692 `.md` · 3,241 total.

### 2.1 Engineering culture observed

This is important context for the remediation plan. The codebase is unusually
well-documented: modules carry long header comments that cite requirement IDs, name the
defect a given line fixes, and record decisions that were deliberately *not* taken. CI
enforces invariants through dedicated guard tests
(`algo22-terminal/tests/unit/guards/`: `nav-contract`, `no-colour-literals`,
`absolute-font-sizes`, `a11y-ratchet`), and `pytest.ini` documents each custom marker at
paragraph length.

Two consequences:

1. **Several "bugs" in Part 2 are deliberate, documented behaviour.** Changing them without
   reading the rationale would reintroduce a fixed defect. Task 02, Part 3 §2 and Part 3 §4
   are all in this category — see §5.
2. **The guard tests will reject careless UI work.** `no-colour-literals.budget.js` caps
   hardcoded colours per file; Task 17 (light mode) must work *through* the token system, not
   around it. This is a help, not an obstacle.

---

## 3. Module and dependency map

### 3.1 Frontend routes and pages

Navigation is centralised in one frozen table, `src/components/shell/navigation.js`
(**VERIFIED**, read in full). Nine primary sidebar entries in four workflow groups:

| Group | Entries |
|---|---|
| Monitor | Dashboard, Live Trading |
| Build & Test | Strategies, Strategy Builder, Backtester |
| Review | Signal Trace, Trade History |
| Practise & Discover | Paper Trading, Marketplace |

Eight further authenticated routes exist with no sidebar entry, reached via the account
menu, titled through `SECONDARY_ROUTE_TITLES`: `/app/profile`, `/app/security-logs`,
`/app/billing`, `/app/exchange`, `/app/risk`, `/app/notifications`, `/app/support`,
`/app/2fa`.

`NAV_ENTRIES` is deep-frozen and `tests/unit/guards/nav-contract.test.js` pins the count at
nine. `/app/portfolio` was removed on 2026-11-15 (`PORTFOLIO-REMOVED-01`) and now redirects
to `/app/trades`. One supplied screenshot still shows a "Portfolio" sidebar entry, which
dates that screenshot to a build older than current `main`.

Page sizes (`src/pages/`, largest first): `StrategyBuilder.jsx` 230 kB · `PaperTrading.jsx`
197 kB · `LiveTrading.jsx` 179 kB · `Dashboard.jsx` 150 kB · `Strategies.jsx` 142 kB ·
`Billing.jsx` 124 kB · `Backtester.jsx` 113 kB · `StrategyDetail.jsx` 110 kB ·
`StrategyMarketplace.jsx` 109 kB · `Profile.jsx` 98 kB · `ExchangeManager.jsx` 85 kB ·
`RiskSettings.jsx` 54 kB · `SecurityLogs.jsx` 22 kB.

Support and Notifications are **components, not pages**: `components/SupportCenter.jsx`
(48 kB) and `components/NotificationCenter.jsx` (31 kB), with `design/notificationPolicy.js`
(15 kB) and two hooks (`useNotificationStream`, `useUnreadNotifications`).

Single-file pages of 100–230 kB are themselves a maintainability and bundle finding — see §7.

### 3.2 Backend routers

29 routers under `backend_app/routers/`. By size: `library.py` 321 kB ·
`strategy_operations.py` 300 kB · `strategies.py` 188 kB · `billing.py` 167 kB ·
`paper_trading.py` 97 kB · `exchange.py` 74 kB · `orders.py` 58 kB · `risk.py` 49 kB ·
`support.py` 43 kB · `referral.py` 42 kB · `signal_trace.py` 29 kB · `admin.py` 22 kB ·
`notifications.py` 19 kB.

**Every module Part 2 asks about has a substantial backend already.** Support (43 kB),
Notifications (19 kB) and Risk (49 kB) are implemented, not stubs. The audit question for
Tasks 09/10/11 is therefore *correctness and wiring*, not *existence*. This materially
changes their shape versus the prompt's framing.

### 3.3 Design layer

`src/design/` holds data-only modules that the pages render from: `pageFields.js` (196 kB —
copy and field definitions), `errorCopy.js` (53 kB), `pageHierarchy.js` (20 kB),
`alertCondition.js` (19 kB), `subscriptionState.js` (18 kB), `notificationPolicy.js` (15 kB),
`entitlements.js` (14 kB), `tokens.js` (5 kB, **generated** from `styles/tokens.css` by
`npm run tokens`).

---

## 4. Infrastructure and integration access status

**MEASURED.** This section is the honest boundary of the audit.

| System | Access | Evidence |
|---|---|---|
| **GitHub** | ✅ **read/write** | `gh auth status` → logged in as `Narendra2212`, scopes `repo`, `workflow`, `read:org`, `gist`, `codespace`. `gh repo view` succeeded. |
| **Local git** | ✅ | `vyomquant-saas/` on `main`, working tree clean. |
| **Frontend toolchain** | ✅ **working** | `npm install` → 335 packages. `npx vitest run tests/unit/shell/navigation.test.js` → **21/21 passed, 6.11 s**. |
| **AWS** | ❌ **unavailable** | `aws.exe` present at `C:\Users\aa\aws_cli_extract\...`, and `~/.aws/{config,credentials,sso,login}` exist, but the binary produces no output and does not terminate — `aws --version` returns nothing. Not a permissions finding; the CLI is non-functional in this environment. |
| **Supabase** | ❌ **unavailable** | No `supabase` CLI on PATH. No `.env` in the repo (only `.env.example`, 15 kB, 60+ keys). No `DATABASE_URL`/`SUPABASE_*` in the environment. No `psql`. Schema was audited **statically from migration DDL**. |
| **Sentry** | ❌ **unavailable** | No `sentry-cli`, no `SENTRY_*` in the environment. No issues, traces or releases were read. |
| **Docker** | ❌ | Not on PATH. Container and integration lanes cannot run locally. |
| **Python deps** | ⚠️ not installed | No venv. 711 pytest files present but not yet runnable. |

### 4.1 What this forecloses

These are hard blockers, not matters of effort:

- **No live database inspection.** RLS policies, actual indexes, row counts, trigger
  definitions and data compatibility were read from DDL, not from `pg_catalog`. A migration
  can only be designed here; verifying it against production data requires §13 decision 2.
- **No Sentry evidence.** Task 16's "Sentry performance traces" input does not exist for me.
- **No AWS inspection.** Workers, ECS task definitions, storage and deployment pipelines were
  read as committed files (`ecs-task-definition-full.json`, `terraform/`), not as running
  resources.
- **No end-to-end execution.** Every "test the complete workflow" instruction in Part 2 and
  Part 5 is, in this environment, limited to unit and component level. Part 3's symptoms
  (paper-trading engine connectivity, exchange health, deployment allowlist) are observations
  about a **deployed** system I cannot reach.

Task 16 as written ("Do not claim improved performance without measurements") is therefore
**partially blocked**: I can measure bundle size, build time and render behaviour locally;
I cannot measure API latency, database query latency or route navigation time against a real
backend.

---

## 5. Findings per requested task

Ordered by confidence. `file:line` references are against `fc4e3a1a`.

### 5.1 Root cause found, fix is small and local

**TASK 03 — Strategy Builder layout gap. RISK: MEDIUM. Confidence: HIGH.**

**VERIFIED.** `src/pages/StrategyBuilder.jsx:3963` opens the page with:

```js
<div style={{ display: 'flex', flexDirection: 'column', height: '100vh', ... }}>
```

`100vh` is the **full viewport**. But the builder does not own the viewport — it renders
inside the authenticated shell's `<main>`, which `src/App.jsx:805-841` declares as a grid
child with `minHeight: 0; overflowY: 'auto'`. The height actually available to a page is
`100vh − topbar − environment strip`. A child demanding `100vh` is therefore taller than its
container by exactly the height of the chrome above it. The toolbar sits at the top of that
over-tall column, the canvas is a `flex: 1` child measured against the over-tall box, and the
surplus renders as the reported gap plus unwanted scrolling.

This also explains the zoom behaviour precisely: browser zoom changes the CSS-pixel height of
the fixed chrome but not the definition of `vh`, so the mismatch shrinks or grows with zoom
and "becomes less noticeable" at some levels. That symptom is the signature of this class of
bug, not a separate problem.

**The codebase already states the correct rule.** `App.jsx:770-772`, on a sibling element:

> `100%` rather than `100dvh` — the gate's outer element is already the […] `100dvh` here
> would overflow the document by the strip's height.

The builder violates a convention its own shell documents. Fix: `height: '100%'` plus
`minHeight: 0` on the builder root so flex children may shrink below content size. Small
diff; needs a resolution/zoom regression test to hold it.

**TASK 08 — Remove Manage Billing. RISK: LOW. Confidence: HIGH.**

**VERIFIED.** This button cannot succeed and never could. The backend endpoint
`backend_app/routers/billing.py:2995 create_portal_session` returns **HTTP 501
`PROVIDER_HAS_NO_BILLING_PORTAL`** (`billing.py:3042`), because Razorpay publishes no hosted
self-serve portal and the Stripe branch that once created a `stripe.billing_portal.Session`
has been removed (`billing.py:3017`; `api/modules/billing.js:180-186`).

The frontend still renders *Manage billing* at `src/pages/Billing.jsx:1642`. The supplied
screenshot shows the resulting red banner — the user's own evidence of a control whose only
possible outcome is an error. Removing it satisfies Task 08 and Rule 8 ("never make a button
appear functional when its backend operation is broken") in the same change.

Removal scope: `Billing.jsx:1642` and its handler, plus the copy in `design/pageFields.js`
(:3031, :3117, :3154, :3488) that explains the control. The **endpoint stays** — Task 08 says
remove the control, not the provider integration.

**TASK 12 — Remove user-facing Security Logs. RISK: LOW. Confidence: HIGH.**

**VERIFIED.** Reference inventory (code only; the 20+ historical `.md` reports are left
alone as records):

- Page: `src/pages/SecurityLogs.jsx` (22 kB)
- Route: `src/App.jsx`
- Nav title: `src/components/shell/navigation.js` → `SECONDARY_ROUTE_TITLES['/app/security-logs']`
- Account menu entry: `src/components/shell/AccountMenu.jsx`
- Inbound links: `src/pages/Profile.jsx` ("Review All Logs" — visible in screenshot),
  `src/pages/Backtester.jsx`, `src/pages/ExchangeManager.jsx`, `src/pages/Wizard.jsx`
- Copy: `src/design/pageFields.js`
- Frontend API: `src/api/modules/user.js`
- Backend: `backend_app/routers/user.py` — **retain**, per Task 12's own carve-out
- Tests needing update: `tests/unit/shell/accountMenu.test.jsx`,
  `tests/unit/shell/navigation.test.js`, `tests/unit/pages/degradedSurfaces.test.jsx`,
  `tests/unit/profile_phase5b_remediation.test.jsx`,
  `tests/unit/profile_phase5e_trader_ux.test.jsx`, `tests/unit/guards/*.budget.js`

Note the guard budgets reference the file; deleting the page without updating
`no-colour-literals.budget.js` and `absolute-font-sizes.budget.js` will red CI.

### 5.2 Root cause found, fix is architectural

**TASK 01 — Exchange connection MFA. RISK: HIGH. Confidence: HIGH.**

**VERIFIED.** The connect and delete endpoints are guarded by
`Depends(require_aal2)` — `backend_app/routers/exchange.py:375` and `:848`.
`backend_app/core/dependencies.py` implements it by reading the **`aal` claim of the Supabase
JWT** and returning 403 unless it equals `"aal2"`:

```
aal = app_metadata.get("aal") or user.get("aal", "aal1")
if aal != "aal2": raise HTTPException(403, "Two-factor authentication is required…")
```

A session reaches `aal2` **only** by completing a Supabase MFA challenge/verify, which mints a
new JWT. Today the app performs that exchange only in the Profile/TwoFA surface. Hence the
reported behaviour: connecting an exchange bounces the user to Profile. The guard is correct
and must stay — Task 01 says integrate the challenge, not remove it.

The right fix is an **inline step-up**: when a mutation returns 403 with this reason, present
an MFA modal within the connection workflow that runs Supabase
`mfa.challenge()` + `mfa.verify()`, which upgrades the session JWT to `aal2`, then
transparently retry the original request. Verification stays server-side (Supabase validates
the TOTP), the SaaS TOTP never touches an exchange API, and Profile keeps enrollment, reset
and recovery.

Three things need deliberate design, not just a modal:
- A generic 403-`aal2` interceptor in `apiClient.js` so every AAL2 endpoint benefits and the
  logic exists once. `exchange.py:848` (delete) needs it too.
- Replay/expiry/rate-limiting live in Supabase's challenge lifecycle; what *this* repo must
  add is correct single-flight retry so one challenge replays one request, and a bounded
  number of attempts in the UI.
- `exchange.py:1108-1117` documents that `test_connection` must **not** require AAL2 and pins
  it with `tests/test_auth01_require_aal2_is_wired.py`. Do not widen the guard.

**TASK 05 — Strategy naming and versioning. RISK: HIGH. Confidence: HIGH for the
name defect, MEDIUM for versioning.**

**VERIFIED — duplicate names.** `public.strategies`
(`backend_app/migrations/020_declare_pre_existing_tables.sql:267-296`) declares exactly one
constraint: `CONSTRAINT strategies_pkey PRIMARY KEY (id)`. Its five secondary indexes
(`archived_at`, `current_version`, `dag_hash`, `environment`, `(user_id, status)`) are all
non-unique. A repository-wide search of all 86 migrations for a uniqueness rule on strategy
name returned **nothing**.

So the database permits two strategies with the same name under the same `user_id`. The
reported symptom is a missing constraint, exactly as Task 05 suspects. Fix is a partial unique
index — necessarily partial, because `archived_at` soft-deletes rows and a plain unique index
would make a name unreusable after archiving:

```sql
CREATE UNIQUE INDEX CONCURRENTLY uq_strategies_owner_name_live
  ON public.strategies (user_id, lower(name)) WHERE archived_at IS NULL;
```

`lower(name)` because "Momentum" and "momentum" are the same name to a user. **This migration
cannot be written safely yet** — existing duplicates would make the index creation fail, and I
cannot query for them (§4.1). It needs a pre-flight duplicate report against the real
database, then a documented remediation of existing collisions, then the index. That ordering
is non-negotiable and is why this task is gated on §13 decision 2.

**VERIFIED — two competing version representations.** `strategies.current_version` is a
`VARCHAR(20)` string defaulting to `'v1.0'`, while the authoritative history lives in
`strategy_versions` with `CONSTRAINT unique_strategy_version UNIQUE(strategy_id, version)`
and a partial unique index `uq_strategy_versions_one_current ... WHERE is_current`
(`backend_app/migrations/001_strategy_architecture.sql:22-109`). A denormalised string
alongside a normalised table with no declared trigger keeping them in agreement is a drift
hazard. The screenshots are consistent with drift: the Strategies table shows `v1.0` for
`vyom`, while the deploy dialog for `Untitled Strategy` binds `v2.0`.

The partial-unique-index design in `001` is sound. The remediation is to make
`strategy_versions` the single source and reduce `current_version` to a derived value (or drop
its independent writes), inside a transaction — **UNVERIFIED** until the write paths in
`strategy_operations.py` (300 kB) are read in full, which I have not done.

### 5.3 Already implemented — verify, do not rebuild

**TASK 04 — Save/Backtest/Deploy gating. RISK: LOW. Confidence: MEDIUM-HIGH.**

**VERIFIED that the gating exists.** `StrategyBuilder.jsx` computes three refusal values and
binds them to the three buttons: `disabled={saveRefused}` (:4110),
`disabled={backtestRefusal !== null}` (:4121), `disabled={deployRefusal !== null}` (:4131).
Line :655 generates the reason copy — `` if (unsaved) return `Save this strategy before ${gerund}` `` —
which is precisely the "Save this strategy before backtesting / before deploying" text in the
supplied screenshot.

So the behaviour Task 04 asks for is present in the UI, and the screenshot the task cites as
evidence of a problem is actually a picture of the feature working. What remains genuinely
open is Task 04's real requirement: **that eligibility is enforced on the backend, not just
in frontend state.** That is an audit of the `strategies`/`strategy_operations` deploy and
backtest endpoints, and it is **UNVERIFIED**. Rebuilding the frontend gating would be wasted
work and would risk regressing a shipped feature.

**TASKS 09, 10, 11 — Support, Notifications, Risk. RISK: HIGH (Risk engine). Confidence: LOW
on correctness; HIGH that implementations exist.**

`support.py` (43 kB), `notifications.py` (19 kB) and `risk.py` (49 kB) exist, with
`add_support_tables.sql`, `add_notifications_table.sql` and `create_risk_settings_tables.sql`.
`create_risk_settings_tables.sql:40-51` declares `strategy_limits` with
`UNIQUE(user_id, strategy_id)`, matching the "Strategy capital allocations" panel in the
screenshot.

I have **not** audited their correctness, RLS, tenant isolation or event wiring. Doing so
properly means reading ~110 kB of router code plus policies plus the execution-service
integration for Task 11's "every live order is checked against applicable risk limits" — which
is the single highest-risk claim in the whole prompt and the one that most needs a live
database to verify. Scoped in §12, not attempted here.

### 5.4 New build on sound foundations

**TASK 06 — Backtest graph modal. RISK: LOW. Confidence: HIGH on scope.**

**VERIFIED.** No enlargement modal exists in `Backtester.jsx`. Shared overlay infrastructure
does: `components/ConfirmDialog.jsx` and `components/overlayRegistry.js`. Task 06 should
extend the registry rather than introduce a second overlay mechanism (Rule 3). Escape-to-close,
focus management, scroll locking and single-chart-instance reuse all belong in the registry
layer. The existing chart components already render responsively, so "preserve the existing
visualization logic" is achievable by reparenting rather than re-implementing.

**TASK 17 — Light mode. RISK: MEDIUM. Confidence: HIGH on feasibility.**

**VERIFIED.** There is currently **no theming at all**: a search across `src/**` for
`data-theme`, `prefers-color-scheme`, `ThemeProvider`, `toggleTheme`, `lightMode` or
`theme-light` returned **zero matches**. Light mode is new construction.

The foundation is unusually good. `src/styles/tokens.css` declares itself the sole source of
truth —

> No other file in this repository may declare a colour, radius, shadow, transition,
> breakpoint, z-index or type-scale value.

— uses Tailwind v4 `@theme static` (with a documented reason: plain `@theme` tree-shook 56 of
89 tokens at tailwindcss 4.2.4), and is the generator input for `design/tokens.js`
(`npm run tokens`, with `prebuild` running `--check`). Semantic naming is already in place:
`--color-surface-*`, `--color-content-*`, `--color-status-*`, `--color-env-*`, with contrast
ratios recorded per token and a token explicitly marked non-text-only at 3.2:1.

The clean approach is a second token set scoped to `[data-theme="light"]`, a provider that
sets the attribute on `<html>`, persisted preference, and a pre-hydration inline script to
avoid flash. Components that read tokens need no change.

**MEASURED obstacle.** Hardcoded hex literals remain outside the token system and must be
swept: `ExchangeManager.jsx` 21, `StrategyMarketplace.jsx` 19, `primitives.jsx` 19,
`AuthPage.jsx` 17, `RiskSettings.jsx` 16, `SupportCenter.jsx` 13, `Badge.jsx` 12,
`Dashboard.jsx` 12, `DeployPreflightPanel.jsx` 12, `TwoFA.jsx` 8, `StatusBadge.jsx` 7
(`tokens.js`'s 41 are generated and correct). Each is a component that would stay dark in
light mode — exactly the "isolated components stuck in the previous theme" Task 17 forbids.
`tests/unit/guards/no-colour-literals.budget.js` already tracks these; the budget should
ratchet down as they are converted.

### 5.5 Largest unknown

**TASK 15 — Marketplace. RISK: HIGH. Confidence: LOW.**

A substantial marketplace already exists and must be audited before anything is added:
`routers/library.py` (321 kB), `pages/StrategyMarketplace.jsx` (109 kB) with a 57 kB test
file, `pages/AdminMarketplaceReview.jsx`, `migrations/007_marketplace_submissions.sql`
(≈2,700 lines), `056_mkt05_subscriber_live_authorization_window.sql`,
`059_mkt06_creator_payout_atomic.sql`, `050_mkt03_settlement_atomic_transition.sql`, and a
full spec at `.kiro/specs/marketplace-subscriptions-paper-trading/`.

`007:697-710` shows the care already taken — a partial unique index
`uq_submission_open_per_strategy` chosen over a plain `UNIQUE (source_strategy_id)`
specifically so that resubmission after rejection stays possible.

Task 15 is the largest item in the prompt (sub-parts A–G, including source-code protection and
30-day entitlement expiry). Its eligibility rules — five completed backtests, 24 h of verified
live history — are **data** questions that cannot be designed, let alone verified, without the
database. Read the existing spec before writing any code; it may already specify much of D–G.

### 5.6 Deliberate behaviour that should not be "fixed"

**TASK 02 — Dashboard layout. RISK: LOW.**

The first supplied screenshot shows a dashboard with large empty regions. The current
`main` has **22 dashboard audit documents** (`DASHBOARD_PHASE_*`, `DASHBOARD_FINAL_FORENSIC_ACCEPTANCE.md`,
…). Before changing layout I need to know whether the screenshot predates that work — as the
"Portfolio" sidebar entry in another screenshot proves at least one screenshot does (§3.1).
Much of the emptiness is also empty **state**, not empty **layout**: the account genuinely has
no positions, no alerts and no deployments, so the cards are correctly showing their empty
states. Grid distribution can still be improved; a redesign driven by a stale screenshot
cannot.

**PART 3 §2 — Exchange status "unknown". RISK: MEDIUM. Confidence: HIGH on mechanism.**

**VERIFIED.** This is not a display bug. `backend/dashboard_aggregation_service.py:415-419`
defines:

```python
#: A venue nothing has probed. The honest reading for a row that exists and was never
#: checked: not "connected" (nothing answered), not "disconnected" (nothing was asked).
VENUE_STATUS_UNKNOWN = "unknown"
```

The service already draws every distinction Part 3 §2 asks for: `VENUE_STATUS_CONNECTED`,
`VENUE_STATUS_UNKNOWN`, `VENUE_STATUS_INVALID_CREDENTIALS` as a *known* negative held separate
from unknown, `_CREDENTIAL_VALID_STATUSES` vs `_CREDENTIAL_INVALID_STATUSES` with
`unvalidated`/`pending` deliberately in neither, a `withdraw_enabled` state, an `unreadable`
state imported from the vault module so writer and reader cannot drift, and
`classify_measured_latency()` returning `"unavailable"` rather than inventing a band
(`:459-470`).

So the Dashboard saying `binance — unknown` is the system being honest: **no probe has written
a health record for that venue.** ExchangeManager says CONNECTED · healthy because it reports a
different fact — that a credential exists and its own live test passed.

The real defects are therefore (a) the probe that should populate venue health is not running
or is not writing where the aggregator reads, and (b) two surfaces report different facts in
the same vocabulary, so the user reasonably reads them as contradictory. **(a) is UNVERIFIED
and needs the deployed system.** The fix must not relabel `unknown` as connected — Part 3 §2's
own instruction, and the service's design, agree.

**PART 3 §4 — Paper trading "Not computed". RISK: LOW. Confidence: MEDIUM.**

The screenshot's own copy distinguishes the states Part 3 §4 asks for: "Absent — not zero —
while no trade has closed" (win rate), "Computed from the persisted equity snapshots; zero
while fewer than two exist" (max drawdown), "From the closed round-trip read" (closed trade
count = 0). Closed trade count is 0, so win rate and drawdown are genuinely not computable.
This reads as correct behaviour and matches Part 3 §4's "Never substitute zero for an
unavailable calculation." Remaining question — whether events are being persisted at all — is
**UNVERIFIED** and needs the running session.

**PART 3 §1 — Paper trading engine disconnected while session RUNNING. RISK: HIGH.
Confidence: LOW.** A genuine contradiction and the most serious item in Part 3: a session
presented as RUNNING while the page warns it is not connected to the trading engine. Diagnosing
it requires the deployed system and its WebSocket. Cannot be investigated from this workspace.

**PART 3 §3 — Live deployment allowlist. RISK: LOW as a defect.** The refusal is the
allowlist working, and the dialog already reports "No order was sent." Scope here is UI
explanation and next steps, not behaviour. Do not bypass.

**PART 3 §5 — Exposed MFA backup codes. RISK: CRITICAL, and outside code.**

Ten backup codes are legible in the supplied screenshot. I have not transcribed, stored or
used them, and will not. **They must be treated as compromised and invalidated by the account
owner now**; a replacement set should be generated and the screenshot should not be circulated
further. `backend_app/backend/mfa_backup_codes.py` and
`migrations/062_sec24_mfa_backup_codes.sql` exist and are the surfaces to audit for hashing,
single-use enforcement and regeneration. This is an **operational action for the owner** —
no code change substitutes for invalidating them.

---

## 6. Security and authorization findings

From static reading only; no penetration testing, no live RLS verification.

1. **CRITICAL (environment, §1):** work would be lost or unreviewable if written to the
   non-git workspace root.
2. **CRITICAL (operational, Part 3 §5):** exposed MFA backup codes pending invalidation.
3. **HIGH:** repository is public while custodying exchange-credential handling code.
   Raises the blast radius of any historical secret; makes
   `08-secret-history-audit.yml` load-bearing.
4. **MEDIUM:** AAL2 step-up is applied to exchange create (`exchange.py:375`) and delete
   (`:848`). `dependencies.py` lists admin endpoints, password change and billing mutations as
   also requiring it. Whether all of those are actually guarded is **UNVERIFIED** — the
   docstring records that `require_aal2` previously existed and was wired to **zero**
   endpoints, so the gap between intent and application is a demonstrated failure mode here.
   Worth a full inventory.
5. **MEDIUM (positive):** credential-state handling is careful — an `unreadable` status for
   ciphertext that opens under no configured master key, a `withdraw_enabled` known-negative,
   and a quarantine script. No finding; recorded so it is not disturbed.
6. **Not reviewed:** RLS policy correctness, tenant isolation at runtime, webhook
   authentication and idempotency (Task 07), marketplace source-code leakage (Task 15 G).
   `rls_migration.sql`, `tenant_isolation_validation.sql` and
   `tests/test_tenant01_cross_tenant_matrix_postgres.py` exist and are the right starting
   points, but they need a PostgreSQL instance.

---

## 7. Performance observations

No baseline was captured. Latency, query time and navigation timing all need a running
system (§4.1). What is visible statically:

- **Page component size.** `StrategyBuilder.jsx` 230 kB, `PaperTrading.jsx` 197 kB,
  `LiveTrading.jsx` 179 kB, `Dashboard.jsx` 150 kB in single files. Likely the dominant
  frontend cost and the first thing a real baseline would confirm.
- **Design-layer size.** `design/pageFields.js` 196 kB and `errorCopy.js` 53 kB are imported
  for copy; if not code-split per route they inflate initial load.
- A `bundle-analysis.html` and `PERFORMANCE_REPORT.md` already exist in `algo22-terminal/` and
  should be read before measuring, to avoid repeating completed work.
- `prebuild` runs `gen-tokens.mjs --check`, so token drift fails the build. Good; keep.

Locally achievable for Task 16: bundle composition, build time, token-check timing, component
render counts. Not achievable: API/DB latency, Sentry traces, N+1 detection against real
queries.

---

## 8. Database integrity findings

1. **HIGH — no uniqueness on strategy name.** §5.2. Fix designed, blocked on a duplicate
   pre-flight against real data.
2. **MEDIUM — duplicated version state.** `strategies.current_version` (string) vs
   `strategy_versions` (normalised, correctly constrained). §5.2.
3. **MEDIUM — two migration directories.** `backend_app/migrations/` (75 SQL files,
   numbered to 067, authoritative) and `/migrations/` (11 files, mixed numeric and
   unnumbered: `003`–`007` plus `add_notifications_table.sql`,
   `add_support_tables.sql`, `create_risk_settings_tables.sql`,
   `create_plan_tables.sql`, `referral_system_redesign.sql`,
   `add_billing_currency_and_dag_hash.sql`). Two series with overlapping low numbers and no
   single ordering. `055_deploy05_migration_ledger.sql` suggests a ledger exists; which
   directory it governs is **UNVERIFIED**. Also note `SCHEMA_AS_CODE_COMPLETENESS_AUDIT_REPORT.md`
   already covers this ground.
4. **MEDIUM — duplicate migration numbers.** Two `055_*` (`dbalm02_identity_types_uncovered`,
   `deploy05_migration_ledger`) and two `056_*` (`mkt05_subscriber_live_authorization_window`,
   `vault01_credential_validity_state`). Numeric prefixes are the ordering mechanism;
   collisions make apply order ambiguous.
5. **Positive, preserve:** partial unique indexes used deliberately and documented —
   `uq_strategy_versions_one_current`, `uq_submission_open_per_strategy`,
   `idx_strategies_archived_at WHERE archived_at IS NULL`.

---

## 9. Architectural weaknesses

1. Routers of 300–321 kB (`library.py`, `strategy_operations.py`) and pages of 150–230 kB.
   Auditing a single workflow means reading very large files; this is why several tasks above
   are LOW confidence rather than resolved.
2. Two migration lineages (§8.3).
3. Denormalised state kept in step by convention rather than constraint or trigger (§8.2).
4. 692 markdown reports at repository root and in `docs/`, many superseded. Finding the
   current truth for a module is itself a task — 22 dashboard documents alone.
5. No theming layer despite a mature token system (§5.4) — a gap, now fillable cheaply.

---

## 10. Existing functionality that must be preserved

Confirmed present and not to be regressed:

- Backtesting: runs, persists, renders equity and drawdown curves, exports CSV
  (screenshot: `1.51%` return, Sharpe `0.00`, 1 trade, saved id `58e8853b`).
- Strategy Builder: drag-and-drop, node connections, zoom/pan, undo/redo
  (`UndoRedoContext`), 100-block registry, Inspector, review mode.
- Builder action gating with server-stated refusal reasons (§5.3) — **do not rebuild.**
- Nine-entry frozen navigation contract and its CI guard.
- Token system and `prebuild --check` drift gate.
- Honest status vocabulary in `dashboard_aggregation_service.py` (§5.6) — **do not collapse
  `unknown` into `connected`.**
- Risk guards shown armed: daily loss limit, max drawdown, consecutive loss, capital
  utilisation; emergency halt; live-trading allowlist.
- Paper trading: sessions, equity snapshots, candle/trade-marker charts, five session
  operations.
- Razorpay purchase, renewal, cancellation and webhook processing (Task 08 removes only the
  dead portal control).
- Marketplace submission lifecycle and its partial unique index.
- Backend security logging and audit events (Task 12 removes only the user-facing page).
- MFA enrollment, reset and recovery in Profile (Task 01 adds an inline path, does not move
  these).

---

## 11. Risk classification summary

| # | Finding | Risk | Confidence | Blocked? |
|---|---|---|---|---|
| 1 | Workspace root is not the repo (§1) | CRITICAL | VERIFIED | needs §13.1 |
| 2 | MFA backup codes exposed (Part 3 §5) | CRITICAL | VERIFIED | owner action |
| 3 | Risk engine enforcement unverified (T11) | HIGH | LOW | needs DB |
| 4 | No unique strategy name (T05) | HIGH | VERIFIED | needs DB pre-flight |
| 5 | AAL2 blocks exchange connect (T01) | HIGH | VERIFIED | no |
| 6 | Paper engine disconnected vs RUNNING (P3 §1) | HIGH | LOW | needs deployment |
| 7 | Marketplace scope unaudited (T15) | HIGH | LOW | needs DB + spec read |
| 8 | Public repo + credential custody | HIGH | VERIFIED | owner decision |
| 9 | Duplicated version state (T05) | MEDIUM | VERIFIED | partially |
| 10 | Builder `100vh` layout bug (T03) | MEDIUM | VERIFIED | no |
| 11 | Venue health probe not populating (P3 §2) | MEDIUM | LOW | needs deployment |
| 12 | Two migration lineages + dup numbers (§8) | MEDIUM | VERIFIED | no |
| 13 | AAL2 coverage inventory incomplete (§6.4) | MEDIUM | LOW | no |
| 14 | No theming layer (T17) | MEDIUM | VERIFIED | no |
| 15 | Colour literals outside tokens (T17) | MEDIUM | MEASURED | no |
| 16 | Dead *Manage billing* control (T08) | LOW | VERIFIED | no |
| 17 | Security Logs page removal (T12) | LOW | VERIFIED | no |
| 18 | No backtest chart modal (T06) | LOW | VERIFIED | no |
| 19 | Dashboard layout (T02) | LOW | LOW | needs current screenshots |
| 20 | Oversized routers and pages (§9) | MEDIUM | MEASURED | no |

---

## 12. Remediation plan

Sequenced by dependency and by what is verifiable in this environment. Every item carries its
own tests; none is "done" until they pass.

### Wave A — unblocked, verifiable locally, low risk

| Task | Work | Verification |
|---|---|---|
| T03 | `100vh` → `100%` + `minHeight: 0` on builder root | layout test at several viewports and zoom levels; manual sweep |
| T08 | remove *Manage billing* control + handler + copy; keep endpoint | billing component test; assert 501 endpoint untouched |
| T12 | remove page, route, nav title, menu entry, inbound links, frontend API; keep backend | update 5 named test files + 2 guard budgets; full vitest run |
| T06 | chart modal via existing `overlayRegistry` | modal open/close/Escape/focus tests per graph |
| T17 | `[data-theme="light"]` token set, provider, persistence, no-flash script, literal sweep | contrast assertions; ratchet `no-colour-literals.budget.js`; render every route in both themes |

### Wave B — unblocked, higher risk, needs careful design

| Task | Work | Verification |
|---|---|---|
| T01 | inline MFA step-up; 403-`aal2` interceptor in `apiClient.js`; single-flight retry; cover `exchange.py:848` too | component tests for success/invalid/expired/rate-limited; assert `test_connection` still needs no AAL2 |
| T04 | audit backend deploy/backtest eligibility enforcement; add server-side checks if missing. **Do not touch working frontend gating** | backend tests asserting refusal without a saved version |
| §6.4 | inventory every endpoint `dependencies.py` says needs AAL2; wire the gaps | extend `test_auth01_require_aal2_is_wired.py` |
| T13 | remove Subscription & Plan, platform-wide statistics, Notification Preferences from Profile; move preferences into Notifications | profile and notifications tests |
| T14 | Strategies page → cards; consolidate Owned/Subscribed | preserve search, filters, all row actions |

### Wave C — needs a database (gated on §13.2)

| Task | Work |
|---|---|
| T05 | duplicate-name pre-flight report → remediate collisions → `CREATE UNIQUE INDEX CONCURRENTLY` partial index + rollback script; then unify version state in a transaction |
| T11 | risk persistence and **order-path enforcement**; fail-safe when risk config unavailable; audit trail |
| T09 | Support end to end: RLS, ownership, attachments, replies, notifications |
| T10 | Notifications end to end: event wiring, dedupe, retries, mandatory-alert floor, preferences |
| T07 | billing history with provider payment metadata; webhook auth + idempotency |
| §8.3/8.4 | reconcile the two migration lineages and the duplicate numbers |

### Wave D — largest, do last

| Task | Work |
|---|---|
| T15 | read `.kiro/specs/marketplace-subscriptions-paper-trading/` first; audit existing listing/subscription/entitlement/payout infrastructure; then listing button, workflow, eligibility (5 backtests + 24 h live), 30-day entitlement, protected execution, security review |
| T16 | performance baseline then optimisation — **partially blocked**, no Sentry/API/DB timing available |
| T02 | dashboard grid work, once current-build screenshots confirm the symptom |
| P3 §1,§2 | paper-engine session/connection truth; venue health probe population — **blocked on deployment** |

### Deferred by instruction

Part 3 §3 (allowlist) — UI explanation only, no bypass. Part 3 §5 — owner must invalidate the
exposed codes; I will not handle them.

---

## 13. Decisions required before implementation

**13.1 Confirm the target and the branch.** All work goes to
`c:\Users\aa\Downloads\vyomquant-saas-main\vyomquant-saas\` (§1), not the workspace root.
`main` is clean at `fc4e3a1a`; I propose a feature branch per wave
(`kiro/wave-a-ui-remediation`, …) with one commit per task and a PR for review, rather than
committing to `main`. Confirm, or name the branch and PR convention you want.

**13.2 Database access, or Wave C and most of Wave D stay blocked.** Tasks 05, 07, 09, 10, 11
and 15's eligibility rules all need the schema and data. To proceed I need **either**
credentials for a **non-production** Supabase/PostgreSQL instance (per Part 6: "test against a
non-production environment"), **or** a working `supabase`/`psql` client plus a read-only
connection, **or** an explicit instruction to deliver migrations and risk-path changes as
**reviewed-but-unapplied** artefacts with rollback scripts, verified only against local
PostgreSQL if one can be stood up. I will not apply a migration to production, and I will not
write a uniqueness constraint without first reporting the duplicates it would reject.

**13.3 Scope and order.** Part 2 is 17 tasks; Parts 3–6 add cross-cutting audits, a
performance baseline and a full regression pass. That is a multi-week programme, not a single
change. Wave A is five tasks I can implement and verify now. Confirm Wave A as the first slice,
or re-order it.

**13.4 Current screenshots for Task 02.** At least one supplied screenshot predates current
`main` (it shows the removed Portfolio nav entry). Fresh dashboard captures from the current
build would prevent a redesign aimed at an already-fixed layout.

---

## 14. What this audit did not do

Stated plainly so nothing here is read as broader than it is:

- No live database, AWS or Sentry inspection (§4).
- No end-to-end workflow execution; no live or paper orders placed.
- No RLS, tenant-isolation or webhook verification at runtime.
- No performance baseline.
- `library.py` (321 kB), `strategy_operations.py` (300 kB), `strategies.py` (188 kB),
  `billing.py` (167 kB) and `paper_trading.py` (97 kB) were searched, not read in full.
- The 692 existing markdown reports were not reconciled against current `main`.
- One test suite was executed (`navigation.test.js`, 21/21). The other ~90 frontend suites and
  711 pytest files were not run; Python dependencies are not installed.
