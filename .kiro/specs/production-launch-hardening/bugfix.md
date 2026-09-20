# Bugfix Requirements Document

## Introduction

VyomQuant is a retail crypto algo-trading platform. The UI redesign closed at commit `939a428` on
branch `feat/marketplace-subscriptions-paper-trading`. This spec covers the launch-readiness pass
that establishes whether the application is safe to put in front of real traders holding real
exchange credentials. It is a bugfix spec, not a feature spec: every clause below names a defect
that exists in the tree today, or names a safety-critical property that **no test currently
establishes** — which, on the eve of a money-handling launch, is itself the defect.

### Severity scale

| Tier | Meaning | Launch |
|------|---------|--------|
| **P0** | Shows a trader a number no backend produced, crosses a tenant boundary, or can place, duplicate, or lose a real order. | Blocks |
| **P1** | A documented capability is dead, a protocol contract is violated, a credential is exposed, or the release itself cannot ship correctly. | Blocks |
| **P2** | Degraded correctness, performance, or CI confidence that a trader can notice but not be harmed by. | Does not block; must be tracked |
| **P3** | Latent or not currently user-reachable. | Does not block |

Every P0 and P1 MUST be fixed at root cause and MUST carry a regression test that fails before the
fix and passes after. Symptom patches — widening a `try`, defaulting a missing key, silencing a
warning — do not satisfy any clause in this document.

### Evidence status

Each clause is tagged **[CONFIRMED]** or **[UNVERIFIED]**.

- **[CONFIRMED]** — read directly out of this tree during requirements gathering. File and line
  references are as observed at `939a428` plus the uncommitted working tree.
- **[UNVERIFIED]** — the property is safety-critical and no evidence either way was found. The
  defect being asserted is the *absence of proof*, and the clause is satisfied by producing proof,
  or by producing a new numbered P0/P1 defect if the proof fails.

No clause in this document may be closed by assertion. Where this environment cannot produce the
proof, the corresponding Expected Behavior clause says **BLOCKED** and names what is missing,
rather than implying a pass.

### Bug condition

The pass covers many defects, but they share one condition: the system is asked for a fact it does
not have, and answers with a fabrication instead of an absence.

```pascal
FUNCTION isBugCondition(X)
  INPUT: X of type SystemInteraction
  OUTPUT: boolean

  RETURN  (X asks for a financial figure AND the producing read failed or returned empty)
       OR (X asks for venue health AND no health probe was performed)
       OR (X asks for a resource AND the owning tenant is not the caller)
       OR (X submits an order AND a prior identical submission may already exist)
       OR (X is a release artifact AND its bytes are not a real build output)
END FUNCTION
```

```pascal
// Property: Fix Checking
FOR ALL X WHERE isBugCondition(X) DO
  result ← F'(X)
  ASSERT   is_explicit_unavailable(result)          // null / "unavailable" / 404 / 422 / 503
        OR is_refusal_with_code(result)             // 403 with a distinct machine-readable code
        OR is_deduplicated_ack(result)              // the prior order, not a second one
  ASSERT NOT is_synthesised_value(result)
  ASSERT NOT is_500(result)
END FOR
```

```pascal
// Property: Preservation Checking
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT F(X) = F'(X)
END FOR
```

Where **F** is the code at `939a428` plus the current uncommitted working tree, and **F'** is the
code after this hardening pass.

### Environment constraints on provability

These bound what any clause may claim.

- **PowerShell on Windows.** `npx` swallows stdout: invoke `node node_modules/vitest/vitest.mjs --run <path>`
  and `node node_modules/eslint/bin/eslint.js`. `git commit -F <file>` fails silently: use
  `git commit -q -m`. Frontend build needs `$env:NODE_OPTIONS="--max-old-space-size=2048"`.
  The bare full vitest suite exceeds 25 minutes and MUST NOT be run unscoped.
- **AWS access is available in this session.** Contrary to the working assumption carried into this
  spec, `sts get-caller-identity` succeeds as
  `arn:aws:iam::273709947018:user/github-actions`, `ecs describe-services` on
  `vyomquant-cluster` / `vyomquant-api-service-cjema2sl` returns `ACTIVE 1/1` on task definition
  `vyomquant-api:146`, and `HEAD https://d7d88qs4jmch.cloudfront.net/` returns `200`. Production
  health clauses are therefore provable and may not be marked BLOCKED without a fresh failure.
  The IAM principal is deploy-scoped, so a clause needing an API this user cannot call must record
  the specific denied call.
- **`gh` is authenticated** as `Narendra2212`, so Dependabot alert counts are directly checkable.
- **Live-order testing against a real exchange is out of bounds.** Trading-safety clauses are
  established by code reading, unit and property tests, and testnet/paper/simulation only. No
  clause may be closed by a live fill.
- **Merging to `main` auto-fires `06-frontend-deploy.yml`** to S3 and CloudFront. Release-artifact
  clauses (1.30) MUST be closed before any merge to `main`.

---

## Bug Analysis

### Current Behavior (Defect)

What the tree does today. Ordered by launch risk: fabricated financial data first, then
unestablished trading and tenant safety, then credential and protocol defects, then release
blockers, then correctness and CI.

**Fabricated financial data**

1.1 **[P0][CONFIRMED]** WHEN the paper portfolio read raises, THEN `dashboard_aggregation_service.py:958-961`
returns `total_equity`, `total_value`, `available_balance` and `free_balance` all set to the literal
`100000.0`, so a read failure is rendered to the trader as a funded account.

1.2 **[P0][CONFIRMED]** WHEN the portfolio fetch fails inside the dashboard composer, THEN
`dashboard_aggregation_service.py:1692-1695` substitutes `100000.0` in paper and `0.0` in live, so a
live trader holding open positions is shown zero equity, indistinguishable from a liquidated
account, on what is only a failed read.

1.3 **[P0][CONFIRMED]** WHEN QuestDB returns no `equity_curve` rows and the environment is paper,
THEN `dashboard_aggregation_service.py:1338-1344` synthesises a two-point flat curve at `100000.0`
spanning the requested window, so "no history" is drawn as "perfectly flat performance".

1.4 **[P0][CONFIRMED]** WHEN the paper account row is missing `total_equity`, `available_balance` or
`initial_capital`, THEN `dashboard_aggregation_service.py:897-903` defaults each to `100000.0`
rather than treating the field as absent.

1.5 **[P0][CONFIRMED]** WHEN dashboard exchange health is requested, THEN
`dashboard_aggregation_service.py:615-620` emits `"status": "connected"` and `"latency_ms": 35` as
hardcoded literals for every row, so a revoked, expired or unreachable venue key reports healthy at
35 ms.

1.6 **[P0][CONFIRMED]** WHEN a user holds any row in `exchange_keys`, THEN
`dashboard_aggregation_service.py:606-625` counts it as a connected exchange and returns
`can_trade: true` without probing the credential, so the UI offers to arm live trading on a key
that cannot place an order.

1.7 **[P0][CONFIRMED]** WHEN a backtest completes, THEN `backtest_service.py:426-427` persists
`win_rate` and `max_drawdown` from payload keys the engine never emits — `backtesting_engine.py:231-232`
and `485-486` emit `win_rate_pct` and `max_drawdown_pct` — so both columns store `0` while being
displayed as computed results.

1.8 **[P0][CONFIRMED]** WHEN a backtest completes, THEN `backtest_service.py:424` and `:438` persist
`total_return` and `final_capital` respectively from keys the engine never emits — it emits `total_return_pct`
and `final_equity` — so both store `0`.

1.9 **[P0][CONFIRMED]** WHEN a backtest completes, THEN `backtest_service.py:434` reads
`results["equity_curve"]`, but `backtesting_engine.run_backtest` returns the curve as a separate
tuple element rather than inside the results dict on the primary path, so `equity_curve` persists as
`[]` and the chart renders empty against a run that produced a curve.

1.10 **[P0][CONFIRMED]** WHEN the Backtester displays Net P&L, THEN no value is available, because
the engine computes `total_pnl` only into a log line (`backtesting_engine.py:405-408`) and emits no
`total_pnl` key for the writer to persist.

**Unestablished trading and tenant safety**

1.11 **[P0][UNVERIFIED]** WHEN an order submission is retried after a timeout, worker restart or
duplicate signal, THEN no test in this tree establishes that the second submission is deduplicated,
so duplicate real orders cannot be ruled out.

1.12 **[P0][UNVERIFIED]** WHEN a strategy bound to the paper environment produces a signal, THEN no
test establishes that no code path can route it to a live venue adapter, so paper-live bleed cannot
be ruled out.

1.13 **[P0][UNVERIFIED]** WHEN a client submits an order exceeding position size, leverage,
daily-loss or kill-switch limits, THEN no test establishes that the limit is enforced server-side
rather than only in the UI, so a direct API call bypassing the client cannot be ruled out.

1.14 **[P0][UNVERIFIED]** WHEN a caller requests a resource id belonging to another user, THEN no
systematic sweep exists across every user-scoped route, so IDOR and BOLA holes cannot be ruled out.
`backtest_service.update_backtest_results` is the precedent: it filtered on `id` alone until it was
found, making it a cross-tenant write and an existence oracle.

1.15 **[P0][UNVERIFIED]** WHEN two tenants' rows share a table, THEN no audit records which tables
carry a `user_id` predicate on every access path, which carry row-level security, and which rely on
application code alone, so the tenant boundary is undocumented.

1.16 **[P0][UNVERIFIED]** WHEN start, stop, delete and deploy are issued concurrently against one
strategy, THEN no test establishes the outcome, so a strategy that is deleted while running, or
deployed twice, cannot be ruled out.

1.17 **[P1][UNVERIFIED]** WHEN the worker crashes between signal generation and order
acknowledgement, THEN no test establishes what state the strategy resumes in, so a lost or replayed
signal across restart cannot be ruled out.

1.18 **[P1][UNVERIFIED]** WHEN a subscription lapses, is cancelled, or fails payment, THEN no
end-to-end test establishes that entitlement is withdrawn everywhere it is read, so continued access
after non-payment cannot be ruled out.

1.19 **[P1][UNVERIFIED]** WHEN a non-entitled caller reads a marketplace listing, THEN no test
establishes that no response body, error message, trace or DAG export carries the creator's strategy
logic, so logic leakage cannot be ruled out.

1.20 **[P1][UNVERIFIED]** WHEN the frontend bundle is built and the backend logs a request, THEN no
audit establishes that no exchange API key, secret, or service-role credential appears in the
bundle, in logs, or in an API response body.

**Credential exposure and protocol violations**

1.21 **[P1][CONFIRMED]** WHEN the Billing page connects its socket, THEN `Billing.jsx:142` places the
`localStorage` JWT in the URL query string as `?token=`, so the credential is written to CloudFront
and ALB access logs and to browser history.

1.22 **[P1][CONFIRMED]** WHEN the Billing page unmounts, THEN the effect cleanup at `Billing.jsx:170`
calls `ws.close()`, which fires the `onclose` handler at `Billing.jsx:165`, which schedules
`setTimeout(connectWebSocket, 5000)` with no cancellation, so a new socket is opened five seconds
after the component is gone and nothing will ever close it.

1.23 **[P1][CONFIRMED]** WHEN the app runs, THEN three WebSocket connections can be live at once —
the shared `websocketClient`, the `new WebSocket` at `Billing.jsx:144`, and the `new WebSocket` to
`/ws/market-data` at `DataPipelineContext.jsx:371` — against a design that permits exactly one.

1.24 **[P1][CONFIRMED]** WHEN a strategy's status changes, THEN nothing in the backend publishes a
`strategy_status` update. The only occurrence is a docstring at `api_ws/ws_routes.py:1050` describing
a call that no service makes, so the frontend's status contract is structurally satisfied and
operationally dead.

**Dead code paths**

1.25 **[P1][CONFIRMED]** WHEN indicator computation is requested, THEN `IndicatorEngineContext.jsx:66`
calls `post('/indicator/compute', ...)`; the route does not exist on the backend and `post` is not
imported in that module, so the call raises `ReferenceError` inside a `useCallback`.

1.26 **[P1][CONFIRMED]** WHEN logic evaluation is requested, THEN `LogicEngineContext.jsx:55` calls
`post('/logic/evaluate', ...)` with the same two defects: no such route, and `post` unimported.

1.27 **[P1][CONFIRMED]** WHEN a strategy execution plan is submitted, THEN `StrategyEngineContext.jsx:283`
calls `post('/strategy/execute', ...)` with the same two defects.

1.28 **[P1][CONFIRMED]** WHEN Copilot generates a DAG, THEN `CopilotContext.jsx` POSTs
`/api/v1/copilot/dag/generate`, which does not exist; and `loadSession` GETs
`/api/v1/copilot/sessions/{id}`, where `copilot.py:306` registers only `DELETE` — the nearest read
route is `GET /sessions/{id}/messages` at `copilot.py:264`.

1.29 **[P1][CONFIRMED]** WHEN a signal trace is formatted for the frontend, THEN
`signal_trace_engine.py:313-323` places raw `DAGNodeTrace` instances into the `nodes` field of the
`market_data` and `indicators` stages, unlike the `dag_nodes` stage at `:325-341` which projects them
to dicts. The objects are not JSON-serialisable, so the response fails serialisation and Signal Trace
stages 1 and 2 never show node detail.

**Release blockers**

1.30 **[P0][CONFIRMED]** WHEN the frontend is built and deployed, THEN three fake desktop installers
ship to production. `algo22-terminal/public/releases/` holds
`linux/VyomQuant-0.1.0.AppImage` (64 bytes), `linux/vyomquant_0.1.0_amd64.deb` (67 bytes) and
`mac/VyomQuant-0.1.0-universal.dmg` (69 bytes) — text files, not binaries. Vite copies `public/`
into `dist/` (byte-identical copies are already present in `algo22-terminal/dist/releases/`), and
`06-frontend-deploy.yml:223-227` runs `aws s3 sync dist/` with
`--cache-control "public, max-age=31536000, immutable"`, so the fakes are served from CloudFront and
cached for one year. The Windows installer at `releases/windows/VyomQuant-Setup-0.1.0.exe`
(112,117,309 bytes) is real and is not part of this defect.

1.31 **[P1][CONFIRMED]** WHEN the deploy runs from `main`, THEN it uses the older two-host grep for
`d7d88qs4jmch.cloudfront.net` or `api.vyomquant.in`, because the 24-insertion `ALLOWED_API_HOSTS`
allow-list in `.github/workflows/06-frontend-deploy.yml` is uncommitted, so cutting `VITE_API_URL`
over to `app.vyomquant.in` fails the deploy and reports it as a build error.

1.32 **[P1][CONFIRMED]** WHEN `main` is deployed, THEN it is missing work that exists only in the
working tree: modified `backend_app/main.py`, `routers/billing.py`, `routers/referral.py`,
`backend/connection_engine.py`, `.env.example`, `algo22-terminal/.env.production.example`,
`infra/acm.sh`, `infra/alb.sh`, `infra/dns.md`, `scripts/post_migration_validation.py`,
`tests/test_cors_configuration.py`, and untracked `infra/rollback/`, `scripts/apply_migrations.py`,
`scripts/migration_preflight.py`, `tests/test_migration_tooling.py`.

1.33 **[P1][CONFIRMED]** WHEN the default branch is scanned, THEN 295 Dependabot alerts are open — 8
critical, 60 high, 127 medium, 100 low — with no triage record distinguishing runtime from
development dependencies.

1.34 **[P2][CONFIRMED]** WHEN `node node_modules/eslint/bin/eslint.js src` runs in `algo22-terminal`,
THEN it reports 58 errors and 246 warnings, and no workflow in `.github/workflows/` invokes eslint at
all, so the count is unbounded and undetected by CI.

1.35 **[P2][CONFIRMED]** WHEN the repository is built or deployed, THEN a stale duplicate frontend at
`aerora_quant_platform/frontend_app/algo22-terminal` is present and indistinguishable by path
convention from the live one, creating a deployment hazard.

**Correctness and contract gaps**

1.36 **[P2][CONFIRMED]** WHEN Live Trading shows the latest signal, THEN it cannot be attributed to a
strategy, because `dashboard_aggregation_service.get_recent_signals` (`:1421`) selects no strategy
identifier.

1.37 **[P2][CONFIRMED]** WHEN the marketplace catalogue renders, THEN it cannot show whether the
caller already subscribes, because `browse_library` (`library.py:1011`) projects only the public
aggregate `subscriber_count` and no per-caller subscription state; that state exists only on
`GET /api/library/my-strategies` and `GET /api/library/{id}/subscribe`.

1.38 **[P2][CONFIRMED]** WHEN a saved strategy is opened and edited once, THEN the edit cannot be
undone, because `UndoRedoContext.jsx:23` defines `canUndo` as `currentIndex > 0` and the strategy
loader never seeds history with `pushState`, so the first edit lands at index 0.

1.39 **[P1][UNVERIFIED]** WHEN a request fails validation, THEN no sweep establishes that every route
answers 422 with a machine-readable code rather than 500, so unhandled validation paths leaking a
traceback cannot be ruled out.

**Performance, resilience and presentation**

1.40 **[P2][CONFIRMED]** WHEN `06-frontend-deploy.yml:223-227` syncs `dist/`, THEN unhashed assets —
`robots.txt`, `sitemap.xml` and the four SVGs — receive `max-age=31536000, immutable`, because the
sync excludes only `*.html` and `*.map`, so a correction to any of them cannot reach a returning
visitor for a year.

1.41 **[P2][CONFIRMED]** WHEN `01-pr-check.yml` runs, THEN its `timeout-minutes: 40` (`:132`) and the
comment justifying it describe a tree far smaller than the current one — 494 test files are tracked
today — so the ceiling is no longer sized to the suite it guards.

1.42 **[P2][UNVERIFIED]** WHEN a page loads or a session runs for an extended period, THEN no
measurement exists for N+1 query patterns, polling intervals, re-render storms or heap growth.

1.43 **[P2][UNVERIFIED]** WHEN an API call is slow or fails, THEN no systematic check under injected
latency establishes that every surface shows a loading state and then a recoverable failure state
rather than an empty frame or a stuck skeleton.

1.44 **[P2][UNVERIFIED]** WHEN the app is viewed at tablet and mobile widths after the redesign, THEN
no re-verification pass has been recorded against the hardening changes.

1.45 **[P3][CONFIRMED]** WHEN `Drawer` is given `dismissOnScrim={false}`, THEN `Drawer.jsx:231-233`
renders the scrim as `<button aria-label="Close" disabled>`, putting a control named "Close" in the
accessibility tree that cannot close anything. No consumer passes `false` today — `StrategyBuilder.jsx:111`
documents avoiding it — so the defect is latent in the primitive's contract.

1.46 **[P3][CONFIRMED]** WHEN the builder inspector `Drawer` and the shell `AccountMenu` `Drawer` are
open at tablet widths, THEN they contend for the same overlay registry slot.

**Production verification**

1.47 **[P1][UNVERIFIED]** WHEN this hardening pass is deployed, THEN no end-to-end production health
verification has been performed against it, so a green pipeline is the only evidence that the
running service is correct.

---

### Expected Behavior (Correct)

Each clause pairs with the Current Behavior clause of the same number and states the proof that
closes it. The regression test named in each P0 and P1 clause MUST fail against `F` and pass
against `F'`.

**Fabricated financial data**

2.1 **[P0]** WHEN the paper portfolio read raises, THEN the system SHALL return each affected field
as explicitly unavailable — `null` with an `unavailable` reason, or a 503 with a machine-readable
code — and SHALL NOT substitute any literal. *Proof:* pytest case that forces the paper read to
raise and asserts no `100000.0` appears anywhere in the response and that the trader-visible field
is flagged unavailable.

2.2 **[P0]** WHEN the portfolio fetch fails in the dashboard composer, THEN the system SHALL
distinguish "read failed" from "value is zero" in both paper and live, and SHALL NOT report `0.0`
equity for a live account whose balance is unknown. *Proof:* pytest cases for both environments
asserting the failure response carries an unavailable marker and is not numerically equal to a
genuine zero-balance response.

2.3 **[P0]** WHEN QuestDB returns no equity rows, THEN the system SHALL return an empty series for
every environment including paper, and the frontend SHALL render an explicit "no history yet" state.
*Proof:* pytest asserting `[]` for paper on empty QuestDB, plus a vitest case asserting the chart
renders the empty state rather than a flat line.

2.4 **[P0]** WHEN a paper account row lacks `total_equity`, `available_balance` or `initial_capital`,
THEN the system SHALL treat the field as absent and propagate unavailability. *Proof:* pytest
parameterised over each missing key asserting no synthesised default.

2.5 **[P0]** WHEN exchange health is requested, THEN `status` and `latency_ms` SHALL come from a real
probe or measurement, and SHALL be `"unknown"` / `null` when none is available — never a literal.
*Proof:* pytest asserting an unprobed connection reports `unknown` / `null`, and that the hardcoded
`35` no longer appears in the module. The existing measured-latency path at
`dashboard_aggregation_service.py:1456-1486` is the model to follow.

2.6 **[P0]** WHEN a user holds `exchange_keys` rows of unknown validity, THEN `can_trade` SHALL be
derived from credential validity, not row existence, and SHALL be `false` when validity is unknown.
*Proof:* pytest with a revoked/unvalidated key asserting `can_trade: false` and the connection
reported as not connected.

2.7 **[P0]** WHEN a backtest completes, THEN `win_rate` and `max_drawdown` SHALL be persisted from
the keys the engine actually emits. *Proof:* a contract test asserting the writer's read-key set is a
subset of the engine's emitted-key set, run against both engine paths (vectorbt and the fallback at
`backtesting_engine.py:230-244`), so the mismatch cannot silently return.

2.8 **[P0]** WHEN a backtest completes, THEN `total_return` and `final_capital` SHALL be persisted
from the engine's emitted values, or the columns SHALL be renamed to match. *Proof:* covered by the
same key-set contract test as 2.7, asserted per column.

2.9 **[P0]** WHEN a backtest produces an equity curve, THEN it SHALL be persisted. *Proof:* an
end-to-end test running a real short backtest and asserting the stored `equity_curve` is non-empty
and its length matches the executed bar count.

2.10 **[P0]** WHEN the Backtester shows Net P&L, THEN either the engine SHALL emit `total_pnl` and
the writer SHALL persist it, or the field SHALL be removed from the UI. A permanently
not-available metric on a results screen is not an acceptable resting state. *Proof:* test asserting
a completed backtest yields a numeric `total_pnl`, or a vitest case asserting the field is absent
from the rendered results.

**Unestablished trading and tenant safety**

2.11 **[P0]** WHEN an order submission is retried, THEN the system SHALL acknowledge the original
order and SHALL NOT create a second. *Proof:* a property test over submit-retry sequences on the
paper/simulation path asserting order count is invariant under duplicate submission, plus a code
reading recording the idempotency key and its uniqueness constraint. No live exchange call.

2.12 **[P0]** WHEN a paper-bound strategy signals, THEN no live venue adapter SHALL be reachable.
*Proof:* a test that asserts the live adapter is never constructed for a paper-bound strategy, using
a failing double that raises if invoked.

2.13 **[P0]** WHEN an order violates a risk limit, THEN the server SHALL refuse it with a distinct
machine-readable code regardless of client state. *Proof:* pytest submitting each violation directly
to the API with no UI involvement, asserting refusal and asserting no order row is written.

2.14 **[P0]** WHEN a caller names a resource owned by another tenant, THEN the system SHALL answer
indistinguishably from "no such resource" and SHALL write no data. *Proof:* an enumerated sweep over
every user-scoped route, each with a two-user fixture, asserting identical responses for
other-tenant-id and nonexistent-id. The sweep list and any route excluded from it SHALL be recorded.

2.15 **[P0]** WHEN tenant data is accessed, THEN the boundary SHALL be documented per table —
predicate, row-level security, foreign key, and index — and every gap SHALL be filed as a numbered
P0. *Proof:* a schema audit checked into the spec, plus a test asserting each shared table's access
paths carry the tenant predicate.

2.16 **[P0]** WHEN start, stop, delete and deploy race on one strategy, THEN the system SHALL
serialise them so that no strategy ends running-but-deleted or deployed twice. *Proof:* a concurrency
test firing the operations in parallel against one id and asserting the terminal state is one of the
legal states, repeated enough times to expose interleaving.

2.17 **[P1]** WHEN the worker restarts mid-signal, THEN the signal SHALL be either fully processed or
fully unprocessed, never partially. *Proof:* a test that kills and restarts the worker between
generation and acknowledgement and asserts exactly-once processing.

2.18 **[P1]** WHEN a subscription lapses, is cancelled, or its payment fails, THEN every entitlement
read SHALL refuse with the correct distinct code. *Proof:* an entitlement matrix test over
subscription states × protected operations, asserting each cell's wire code. The existing distinct
codes (`MARKETPLACE_NOT_SUBSCRIBED`, `MARKETPLACE_SUBSCRIPTION_EXPIRED`,
`MARKETPLACE_STRATEGY_UNAVAILABLE`, `MARKETPLACE_OPERATION_NOT_PERMITTED`) are the expected values.

2.19 **[P1]** WHEN a non-entitled caller reads a listing, THEN no response, error, trace or export
SHALL carry creator strategy logic. *Proof:* a test asserting the serialised response for a
non-entitled caller contains none of the node types, parameters or expressions of the underlying DAG.

2.20 **[P1]** WHEN the bundle is built and requests are logged, THEN no secret SHALL appear. *Proof:*
a grep-based assertion over `dist/` for key patterns and known secret names, plus a log-capture test
asserting exchange credentials are redacted. Secrets SHALL be referenced by key name, never value,
in any artifact this pass produces.

**Credential exposure and protocol violations**

2.21 **[P1]** WHEN a socket authenticates, THEN the credential SHALL travel in a header or a
short-lived single-use ticket, never in the URL. *Proof:* a vitest case asserting the constructed
socket URL contains no `token` parameter.

2.22 **[P1]** WHEN a component with a socket unmounts, THEN every pending reconnect timer SHALL be
cancelled and no new socket SHALL be opened. *Proof:* a vitest case that unmounts, advances fake
timers past the reconnect delay, and asserts the socket constructor was not called again.

2.23 **[P1]** WHEN the app runs, THEN exactly one WebSocket SHALL be open, with Billing and market
data multiplexed over the shared client. *Proof:* a vitest case asserting the global socket
constructor is called once across a mount of Billing plus the data pipeline.

2.24 **[P1]** WHEN a strategy's status changes, THEN the backend SHALL publish a `strategy_status`
update over the single socket. *Proof:* a pytest asserting a start and a stop each publish one
update, plus a vitest case asserting the frontend reflects it. If the contract is not to be
implemented, it SHALL be removed from the frontend rather than left dead.

**Dead code paths**

2.25 **[P1]** WHEN indicator computation is requested, THEN it SHALL reach an implemented endpoint
through an imported client, or the client-side path SHALL be removed. *Proof:* a vitest case
asserting no `ReferenceError` and a defined outcome, plus a route-existence assertion against the
OpenAPI schema.

2.26 **[P1]** WHEN logic evaluation is requested, THEN the same SHALL hold. *Proof:* as 2.25.

2.27 **[P1]** WHEN a strategy execution plan is submitted, THEN the same SHALL hold. *Proof:* as
2.25.

2.28 **[P1]** WHEN Copilot generates a DAG or loads a session, THEN each call SHALL target a route
that exists with the method used. *Proof:* a test asserting every frontend Copilot call path appears
in the backend OpenAPI schema with a matching method — a check that generalises to 2.25 through 2.27.

2.29 **[P1]** WHEN a signal trace is formatted, THEN every stage's `nodes` SHALL be JSON-serialisable
dicts projected the same way `dag_nodes` already projects them. *Proof:* a pytest asserting
`json.dumps(to_frontend_format())` succeeds and that stages 1 and 2 carry populated node detail.

**Release blockers**

2.30 **[P0]** WHEN the frontend is deployed, THEN no placeholder release artifact SHALL be published.
The three fake files SHALL be deleted from `public/releases/`, `dist/releases/` and `releases/`, and
either replaced with real signed builds or removed from the download surface. *Proof:* a CI check
failing the build when any file under a `releases/` path is implausibly small for its extension, so
the fakes cannot reappear. This clause MUST close before any merge to `main`, because the merge
auto-fires the deploy.

2.31 **[P1]** WHEN `main` deploys, THEN the `ALLOWED_API_HOSTS` allow-list SHALL be committed so the
`app.vyomquant.in` cutover passes. *Proof:* the committed diff plus a dry-run of the verification
step against a bundle containing each allowed host.

2.32 **[P1]** WHEN `main` deploys, THEN it SHALL contain every reviewed change now in the working
tree, each in a commit whose message names its purpose, using `git commit -q -m`. *Proof:* a clean
`git status` against `main` with the tests in `tests/test_migration_tooling.py` and
`tests/test_cors_configuration.py` passing.

2.33 **[P1]** WHEN the default branch is scanned, THEN every critical and high alert on a runtime
dependency SHALL be resolved or carry a recorded, justified exception naming why it is not
exploitable here. *Proof:* a re-run of the alert query showing zero unexcepted critical or high
runtime alerts, with the exception list checked in. Development-only alerts MAY be deferred to P2.

2.34 **[P2]** WHEN CI runs on a pull request, THEN eslint SHALL run and SHALL fail on errors. *Proof:*
`node node_modules/eslint/bin/eslint.js src` reporting zero errors, and a workflow step that
enforces it. The 246 warnings MAY be capped at the then-current count rather than driven to zero.

2.35 **[P2]** WHEN the repository is built, THEN the stale duplicate frontend SHALL be unambiguously
marked as dead or removed. *Proof:* a check asserting no build or deploy path references
`aerora_quant_platform/frontend_app/algo22-terminal`. It SHALL NOT be modified in place.

**Correctness and contract gaps**

2.36 **[P2]** WHEN Live Trading shows the latest signal, THEN the response SHALL carry the strategy
id and name. *Proof:* pytest asserting the field is present and non-null for a signal from a known
strategy.

2.37 **[P2]** WHEN the catalogue renders, THEN each card SHALL carry the caller's subscription state
from the browse response, in the same three round trips `my-strategies` already achieves. *Proof:*
pytest asserting per-card state with no per-card query, and a vitest case asserting the badge
renders.

2.38 **[P2]** WHEN a saved strategy is opened, THEN the loader SHALL seed undo history so the first
edit is undoable. *Proof:* a vitest case asserting `canUndo` is true after one edit on a freshly
loaded strategy.

2.39 **[P1]** WHEN a request fails validation, THEN the response SHALL be 422 with a
machine-readable code and no traceback. *Proof:* a sweep submitting malformed bodies to every
mutating route, asserting no 500 and no `err.message`-style leakage. The redesign already routes
user-facing copy through `design/errorCopy.js`; this extends the guarantee to the server.

**Performance, resilience and presentation**

2.40 **[P2]** WHEN unhashed assets are synced, THEN they SHALL receive a short revalidating
cache-control, not one-year immutable. *Proof:* the workflow diff plus a check asserting only
content-hashed asset paths receive `immutable`.

2.41 **[P2]** WHEN `01-pr-check.yml` runs, THEN its timeout SHALL be justified against an observed
run of the current suite, and the justification comment SHALL state the measured duration and the
test count it was measured at. *Proof:* the measured duration recorded in the workflow comment.

2.42 **[P2]** WHEN a page loads or a session runs long, THEN query counts, polling intervals, render
counts and heap growth SHALL be measured and recorded, and any regression against the redesign
baseline SHALL be filed. *Proof:* the recorded measurements. Absolute performance targets are out of
scope; the requirement is a measurement, not a number.

2.43 **[P2]** WHEN an API call is slow or fails, THEN every surface SHALL show a loading state and
then a recoverable failure state. *Proof:* vitest cases with delayed and rejected fetches across the
primary surfaces, asserting neither an empty frame nor a permanent skeleton.

2.44 **[P2]** WHEN the app is viewed at tablet and mobile widths, THEN the redesign layout SHALL hold.
*Proof:* the existing responsive test files re-run scoped, not the full suite.

2.45 **[P3]** WHEN `Drawer` receives `dismissOnScrim={false}`, THEN the scrim SHALL NOT be a disabled
button carrying an accessible name. *Proof:* a vitest case asserting no element named "Close" is
exposed when the scrim is non-interactive.

2.46 **[P3]** WHEN two drawers can be open at tablet widths, THEN the overlay registry SHALL define
which wins. *Proof:* a vitest case asserting deterministic stacking with both mounted.

**Production verification**

2.47 **[P1]** WHEN the pass is deployed, THEN production health SHALL be verified end to end:
`ecs describe-services` on `vyomquant-cluster` / `vyomquant-api-service-cjema2sl` reporting
`ACTIVE` with `runningCount == desiredCount` on the new task definition, the backend health endpoint
answering through the ALB, and `https://d7d88qs4jmch.cloudfront.net/` serving the new bundle after
invalidation. *Proof:* the recorded command output. Baseline captured during requirements gathering:
`ACTIVE 1/1` on `vyomquant-api:146`, CloudFront `200`. If any call is denied to the
`github-actions` IAM principal, that specific call SHALL be recorded as **BLOCKED** with the denial,
and the clause SHALL NOT be reported as passing.

---

### Unchanged Behavior (Regression Prevention)

What must still hold after the pass. These are the `NOT isBugCondition(X)` cases: every one of them
is a path where the system already has the fact it is being asked for.

3.1 WHEN QuestDB returns real equity rows, THEN the system SHALL CONTINUE TO return them unmodified,
in ascending timestamp order, with no synthesised endpoints appended.

3.2 WHEN a paper account row carries real `total_equity`, `available_balance`, `locked_balance`,
`realized_pnl`, `unrealized_pnl` and `initial_capital`, THEN the system SHALL CONTINUE TO report
those exact values, and today's realized P&L SHALL CONTINUE TO be computed from paper trades since
00:00 UTC.

3.3 WHEN a venue credential is genuinely valid and reachable, THEN the system SHALL CONTINUE TO
report it connected, with `can_trade` true, and SHALL CONTINUE TO classify measured latency as
`optimal` under 150 ms and `normal` under 500 ms per the existing thresholds.

3.4 WHEN a backtest completes, THEN the columns that already receive correct engine output —
`total_return_pct`, `sharpe_ratio`, `sortino_ratio`, `profit_factor`, `total_trades`,
`winning_trades`, `losing_trades`, `execution_time_seconds`, `trades`, `status`, `completed_at` —
SHALL CONTINUE TO be persisted unchanged.

3.5 WHEN `update_backtest_results` is called with `executed_bar_count`, THEN it SHALL CONTINUE TO
scope its UPDATE by `user_id` as well as `id`, SHALL CONTINUE TO return `{}` indistinguishably for
a non-owned and a nonexistent id, and SHALL CONTINUE TO degrade gracefully when
`006_backtest_evidence_columns.sql` has not been applied.

3.6 WHEN the real Windows installer is requested, THEN `releases/windows/VyomQuant-Setup-0.1.0.exe`
SHALL CONTINUE TO be served intact.

3.7 WHEN the UI renders, THEN every behaviour established by `vyomquant-ui-redesign` at `939a428`
SHALL CONTINUE TO hold: design tokens, the `design/errorCopy.js` and `design/errorLine.js` copy path
with no `err.message` reaching the screen, column alignment, and review-mode edit suppression in the
builder.

3.8 WHEN a backend traceback occurs, THEN it SHALL CONTINUE TO be absent from every user-facing
message.

3.9 WHEN an authenticated caller uses a route, THEN existing authentication, the per-route rate
limits (including the paper-trading `120/minute` reads and `60/minute` operations and the library
`120/60second` plus `60/60second` pair), and existing ownership predicates SHALL CONTINUE TO apply
unchanged.

3.10 WHEN a paper trading session is paused, resumed, stopped or reset, THEN the lifecycle and its
seven sub-resource reads SHALL CONTINUE TO behave as they do today, and the REST event replay SHALL
CONTINUE TO share its implementation with the WebSocket history so the two cannot diverge.

3.11 WHEN an entitlement read fails rather than refuses, THEN the system SHALL CONTINUE TO answer
`MARKETPLACE_READ_FAILED` (503) and SHALL NOT fold the failure into a 403, so a paying subscriber is
never told they hold no subscription because a read broke.

3.12 WHEN a marketplace clone is attempted, THEN owner self-clone SHALL CONTINUE TO return 409,
idempotent re-clone SHALL CONTINUE TO return the existing clone, and refusals SHALL CONTINUE TO carry
their distinct wire codes.

3.13 WHEN `my-strategies` is read, THEN it SHALL CONTINUE TO derive `ownership`, `subscription` and
`allowed_actions` server-side in three round trips independent of entry count, and SHALL CONTINUE TO
report an unavailable figure as unavailable rather than zero.

3.14 WHEN the deploy pipeline runs, THEN it SHALL CONTINUE TO fail closed when `VITE_API_URL` was not
substituted, SHALL CONTINUE TO reject `ws://localhost:8000` in a production bundle, SHALL CONTINUE TO
serve `*.html` with `max-age=0, must-revalidate`, and SHALL CONTINUE TO invalidate CloudFront and
wait for completion.

3.15 WHEN `03-deploy.yml` runs, THEN it SHALL CONTINUE TO verify an immutable ECR artifact exists for
the commit SHA before deploying.

3.16 WHEN any change is made, THEN the stale duplicate frontend at
`aerora_quant_platform/frontend_app/algo22-terminal` SHALL CONTINUE TO be left unmodified.

3.17 WHEN production is running, THEN the ECS service SHALL CONTINUE TO report `ACTIVE` with
`runningCount == desiredCount`, and CloudFront SHALL CONTINUE TO return 200 for `/`.

3.18 WHEN the test suite runs in CI, THEN it SHALL CONTINUE TO be invoked scoped, never as a bare
full vitest run, and the existing passing tests — including the property tests under
`tests/property/` — SHALL CONTINUE TO pass.
