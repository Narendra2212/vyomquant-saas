# Production Launch Hardening Bugfix Design

## Overview

This pass closes 47 defect clauses that decide whether VyomQuant can be put in front of traders
holding real exchange credentials. It is not 47 independent fixes. Reading the tree, the clauses
collapse into **six mechanisms**, and within each mechanism the clauses share one cause, one
representation change and one landing.

| Cluster | Clauses | The one mechanism |
|---|---|---|
| **A — Absent vs zero** | 1.1–1.6, 1.36 | The dashboard response types every headline figure as a non-nullable `float`, so a failed read has no way to say so and must return a number. |
| **B — Backtest key sets** | 1.7–1.10, 3.4 | Four vocabularies (engine → runtime → VectorBT display names → DB columns) with no validation at any boundary, and `dict.get(key, 0)` at the last one. |
| **C — Transport ownership** | 1.21–1.24 | Three sockets, two token stores, and authentication carried in the URL — all properties of `websocketClient`, not of Billing. |
| **D — Client/server route contract** | 1.25–1.29, 1.39 | Frontend call paths and backend routes are never checked against each other, in either direction. |
| **E — Release artifact provenance** | 1.30–1.35, 1.40, 1.41 | The deployed byte set is assembled by convention rather than by a gate that can fail. |
| **F — Unestablished safety** | 1.11–1.20, 1.42–1.47 | Fifteen safety properties with no proof. The defect is the absence of proof; the fix is a named test, not a code change. |

The three P3/P2 presentation clauses (1.38, 1.45, 1.46, 1.43, 1.44) are genuinely local and are
handled individually.

**Cluster A is the spine of the whole pass.** Its representation change is not new work: the
`vyomquant-ui-redesign` spec already built the absent-value convention *in the same file* —
`_finite_float() -> Optional[float]`, `PositionsUnreadable`, `positions_degradation()` and a
top-level `degraded` block, all marked BC-1/BC-2/BC-5 — and the frontend already renders it
through `design/reported.js`'s `Reported<T>` union and `ds/Metric`'s not-available marker. Six of
this pass's P0s exist because that convention was applied to three fields and not to the other
fourteen. The fix is to finish applying it, not to invent a second convention.

**This design does not redesign any UI.** Where a figure becomes not-available, the element that
carries it is `ds/Metric`'s existing marker with a reason; where a panel becomes unreadable, it is
`usePanelState`'s existing `unavailable` / `error` state driving `ds/Panel`. Both already exist,
both are already tested, and both already have a declared reason string per field in
`design/pageFields.js`. No new primitive, no new copy path, no new state.

### Corrections to the requirements, established by reading the tree

Six clauses describe the symptom accurately but the mechanism or the blast radius incorrectly. Each
changes what the fix has to do, so each is recorded here rather than quietly worked around.

1. **1.8 — `final_capital` does not store `0`. It stores `100000.0`.**
   `backtest_runtime.py` sets `"final_capital": stats.get("Final Equity", self.vectorbt_engine.initial_capital)`.
   `"Final Equity"` is a VectorBT *display* name; `stats` has already been normalised to
   `final_equity`, so the lookup always misses and the default always wins. Every completed
   backtest reports its *starting* capital as its *final* capital. That is worse than zero, because
   zero looks like a bug and `100000.00` looks like a result. The fix therefore has a second site
   the clause does not name.

2. **1.7/1.8 understate the blast radius.** `_calculate_performance_metrics` reads five more
   display names `stats` never carries (`Max Drawdown [%]`, `Net Profit`, `Win Rate [%]`,
   `Best Trade`, `Avg Winning Trade`), so `calmar_ratio`, `recovery_factor`, `average_trade`,
   `largest_win`, `largest_loss`, `consecutive_wins`, `consecutive_losses`, `expectancy` and
   `kelly` are *also* fabricated zeros from the same cause. And because the results dict is built
   `{**stats, **performance_metrics}`, `performance_metrics` **overrides** the engine's genuinely
   computed `expectancy` with `0.0`. That contradicts 3.4's premise that the listed columns
   "already receive correct engine output": `expectancy` does not, and `sortino_ratio` is the
   runtime's computation rather than the engine's. The key-set contract test (2.7) is what makes
   this whole family visible at once, which is why it is the right instrument.

3. **1.21 names the wrong owner.** `websocketClient.connect()` builds
   `?token=${encodeURIComponent(token)}` too. Moving Billing onto the shared client, on its own,
   *consolidates* the credential exposure rather than removing it. 2.21's assertion must be made
   against `websocketClient.connect` or the fix is cosmetic. Related: the shared client reads
   `sessionStorage`, Billing reads `localStorage` — two token stores for one session.

4. **1.23 understates the third socket.** `DataPipelineContext.jsx:371` builds
   `` `${wsClient.url}/ws/market-data` ``. `wsClient.url` already ends in `?token=<JWT>`, so the
   constructed URL is `wss://host/ws/telemetry?token=<JWT>/ws/market-data` — malformed, carrying
   the credential mid-path, and aimed at a route that **does not exist** in `ws_routes.py`. The
   "live" pipeline mode has never connected. It is a dead path (cluster D) as much as a third
   socket (cluster C).

5. **1.24 is a three-way mismatch, not one missing publisher.** (a) No service publishes. (b) The
   only broadcaster available, `broadcast_dashboard_update`, wraps everything as
   `{"type": "dashboard_update", "update_type": "strategy_status"}`, and
   `websocketClient.processMessage` routes on exact `message.type` — so wiring that broadcaster up
   would still deliver nothing. (c) The frontend subscribes to upper-case `'STRATEGY_STATUS'`
   (`websocketClient.js:853`). Fixing any one of the three alone leaves the contract dead. This is
   the clearest case in the pass for a fixture pinned on both sides.

6. **1.30 and 3.6 — the fakes are not in git, and the real installer is not deployed.**
   `.gitignore` ignores `releases/`, `**/releases/`, `public/releases/` and `dist/`.
   `git ls-files` returns nothing for any of them: the three fakes and the real 112 MB
   `VyomQuant-Setup-0.1.0.exe` are all untracked. CI checks out, `npm ci`, `npm run build` — so
   CI's `dist/` contains **no** `releases/` directory, and `aws s3 sync dist/ --delete` therefore
   *deletes* the `/releases/` prefix from the bucket. Verified against production during this
   design: all four advertised download URLs return **403** (absent). The real installer lives at
   `algo22-terminal/releases/`, a *sibling* of `public/`, which Vite never copies.

   So the live defect is not "fakes are served, cached for a year". It is: **the download surface
   advertises four installers with four hardcoded sizes (84.2 / 78.5 / 75.4 / 68.2 MB) and serves
   none of them**, and the fakes are a working-tree hazard that would ship the moment anyone
   deploys from a local build. Both are in scope. 3.6 as written — "SHALL CONTINUE TO be served
   intact" — is unsatisfiable, because nothing is served today; it is reinterpreted below as
   "SHALL NOT be broken further, and SHALL be restored or withdrawn deliberately".

7. **Two counts in the requirements are off.** There are **15** UNVERIFIED clauses (1.11–1.20,
   1.39, 1.42, 1.43, 1.44, 1.47), not 17 — all 15 are enumerated in §Testing Strategy. And 1.41's
   "494 test files" is repo-wide (484 tracked, by measurement); the `01-pr-check.yml` comment it
   criticises sizes the **frontend** suite specifically, at "53 files". That suite is now **131**
   files. The correct finding is sharper than the clause: the ceiling was measured at 53 files and
   guards 131.

8. **1.11 and 1.17 are partly already proven.** `tests/crash_recovery/test_worker_crash_mid_submission.py`
   contains roughly twenty tests that establish idempotency-key deduplication on the live path,
   including *the durable unique index refuses a second row for the same key*, *the venue itself
   refuses a second order under the same key*, and *a resumed worker that submits anyway still
   creates no second order*. "No test in this tree establishes" is wrong. What is missing is the
   property test over arbitrary submit-retry sequences that 2.11 asks for, and the paper path.
   Similarly 1.14: `tests/sandbox_lifecycle/test_cross_tenant_ownership_matrix.py` is a rigorous
   enumerated sweep — it resolves every probe through Starlette's own matcher, normalises headers
   and bodies, and self-checks its own coverage — but it is scoped to seven resource types and
   explicitly excludes the paper routes. The fix is to **extend that matrix**, not to write a
   second sweep beside it.

## Glossary

- **Bug_Condition (C)** — the system is asked for a fact it does not have and answers with a
  fabrication instead of an absence. Formalised in §Bug Details, verbatim from the requirements.
- **Property (P)** — the answer is an explicit unavailability (`null` + reason, 404, 422, 503), a
  refusal carrying a distinct machine-readable code, or the prior order rather than a second one.
  Never a synthesised value, never a 500.
- **Preservation** — every path where the system *does* have the fact answers exactly as it does at
  `939a428`. The eighteen 3.x clauses enumerate these.
- **F / F'** — the code at `939a428` plus the current uncommitted working tree; and the code after
  this pass.
- **`Reported<T>`** — `design/reported.js`. `{available: true, value: T} | {available: false, reason: string}`.
  The frontend's absent-value representation, already built and tested. A union rather than a
  nullable field so the availability check cannot be skipped: the `value` key does not exist on the
  unavailable arm.
- **not-available marker** — `ds/Metric`'s rendering of the unavailable arm, carrying the reason.
  The only element that may stand where a figure is absent. It never renders `0`.
- **`_finite_float`** — `dashboard_aggregation_service.py:314`. `Any -> Optional[float]`. The
  backend's absent-value reader: `None` for anything that is not a finite number, and booleans are
  not money. Already present; applied to one field of the fourteen that need it.
- **`degraded` block** — `positions_degradation()`. `None` when every read on a response succeeded;
  otherwise a dict naming what could not be read plus a renderable `reason`. The channel a *list*
  uses, because a list has nowhere to put a null. A nullable *figure* needs no `degraded` block.
- **`PositionsUnreadable`** — the service-layer exception raised where `get_open_positions` used to
  `return []`. Deliberately not an `HTTPException`: which status code it deserves depends on how
  much of the response survives, which only the route can judge.
- **`usePanelState`** — `hooks/usePanelState.js`. One read → one of eight panel states. Its
  `unavailable` state short-circuits *without issuing a request*, which is how a capability the
  backend does not have is expressed.
- **Key-set contract** — the assertion that a writer's read-key set is a subset of a producer's
  emitted-key set. The instrument for cluster B, because it fails on the *next* rename too.
- **Idempotency_Key** — the per-signal key the live order path already carries, backed by a durable
  unique index and re-asserted at the venue. The existing mechanism 2.11 must be proven over, not a
  new one.
- **Single-use ticket** — a short-lived, one-shot credential minted over HTTPS and exchanged for a
  socket. Necessary because the browser `WebSocket` constructor cannot set request headers, so
  "credential in a header" is not available to us; 2.21 permits the ticket explicitly.
- **Last-known-good revision** — `vyomquant-api:146`, currently `ACTIVE 1/1` on
  `vyomquant-cluster` / `vyomquant-api-service-cjema2sl`. The rollback target for this pass.

## Bug Details

### Bug Condition

The bug manifests when a read, probe, ownership check, duplicate-submission check or build step
fails to produce the fact a caller asked for, and the code path answers with a value it
manufactured rather than with an absence. The producing site is either a literal
(`100000.0`, `"connected"`, `35`), a `dict.get(key, 0)` whose key the producer never emitted, a
response field typed as non-nullable so absence is unrepresentable, an untested ownership
predicate, or a file whose bytes are not a build output.

**Formal Specification:**

```
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

The five arms are not five bugs. Arms 1 and 2 are cluster A; arm 3 is the tenant boundary
(cluster F); arm 4 is order idempotency (cluster F); arm 5 is cluster E. What unifies them is that
in every case a *distinguishable* answer exists and is not given.

**The expected answer, as pseudocode:**

```
FUNCTION expectedBehavior(result)
  INPUT: result, the value F'(X) produced for a buggy X
  OUTPUT: boolean

  RETURN  ( is_explicit_unavailable(result)      // null + reason, 404, 422, 503, or
                                                 //   an empty series for an empty read
         OR is_refusal_with_code(result)         // 403/409 with a distinct wire code
         OR is_deduplicated_ack(result) )        // the prior order, never a second
    AND NOT is_synthesised_value(result)
    AND NOT is_500(result)
END FUNCTION
```

### Examples

**Cluster A — absent read rendered as a funded account**

- `get_portfolio_overview(user, environment="paper")` — the paper read raises. Expected: each
  affected field `null` with an unavailability reason. Actual
  (`dashboard_aggregation_service.py:958-961`): `total_equity`, `total_value`,
  `available_balance` and `free_balance` all `100000.0`. A trader whose account could not be read
  is shown a fully funded one.
- The same read fails inside `get_dashboard_data` (`:1692-1695`). Expected: "read failed",
  distinguishable from zero. Actual: `100000.0` in paper, `0.0` in live — so a live trader holding
  open positions sees zero equity, which is indistinguishable from liquidation, on a failed read.
- QuestDB returns no `equity_curve` rows, environment paper (`:1338-1344`). Expected: `[]`, and
  the chart's no-history state. Actual: a synthesised two-point flat line at `100000.0` spanning
  the requested window — "no history" drawn as "perfectly flat performance".
- The paper account row is missing `total_equity` (`:897-903`). Expected: field absent, propagated.
  Actual: `float(acct.get("total_equity", 100000.0))`.
- `get_exchange_data` (`:615-620`) for a revoked key. Expected: `status: "unknown"`,
  `latency_ms: null`. Actual: `"connected"` and `35`, hardcoded, for every row — and
  `can_trade: len(connections) > 0` (`:625`), so the UI offers to arm live trading on a credential
  nobody probed.
- *Edge case that must keep working:* an account genuinely flat at `0.0`, and a genuinely
  measured 35 ms latency. Both are facts. Zero is a reading; the defect is a *fabricated* zero.

**Cluster B — a computed result stored as zero**

- A backtest completes with a 12.4 % return. Expected: `total_return_pct` 12.4 persisted.
  Actual: `win_rate` and `max_drawdown` store `0` (writer reads `win_rate` / `max_drawdown`, engine
  emits `win_rate_pct` / `max_drawdown_pct`); `total_return` stores `0`; `final_capital` stores
  `100000.0` — the *initial* capital, because the runtime reads `stats["Final Equity"]`, a display
  name nothing emits.
- The same backtest produces a 500-point equity curve. Expected: persisted, length 500.
  Actual: `[]`, because the engine returns the curve as the second element of a tuple and
  `backtest_runtime` drops it when building `results`. The chart renders empty against a run that
  produced a curve.
- Net P&L on the Backtester. Expected: a number. Actual: nothing — `total_pnl` is computed only
  into a log line (`backtesting_engine.py:405-408`) and never emitted.

**Cluster C — a credential written to access logs, and a socket that outlives its page**

- Billing mounts (`Billing.jsx:142`). Expected: no credential in the URL. Actual: the
  `localStorage` JWT as `?token=`, which CloudFront and the ALB log and the browser keeps in
  history. The shared client does the same thing with the `sessionStorage` JWT.
- Billing unmounts (`:170`). Expected: no further sockets. Actual: `ws.close()` fires `onclose`
  (`:165`), which schedules `setTimeout(connectWebSocket, 5000)` with no cancellation. Five seconds
  after the page is gone a new socket opens, and nothing holds a reference to close it. A currency
  change re-runs the effect, so this compounds.
- A strategy is started. Expected: one `STRATEGY_STATUS` frame on the one socket. Actual: nothing
  is published, and the only available broadcaster would emit `type: "dashboard_update"` anyway.

**Cluster D — a documented capability that raises before it reaches the network**

- Indicator computation in the builder (`IndicatorEngineContext.jsx:66`). Expected: a computed
  series or a handled failure. Actual: `ReferenceError: post is not defined` inside a
  `useCallback` — `post` is not imported, and `/indicator/compute` does not exist either.
  `LogicEngineContext.jsx:55` and `StrategyEngineContext.jsx:283` are the same defect. All three
  providers are mounted (`StrategyBuilder.jsx:4590-4603`), so all three are reachable.
- Signal Trace stages 1 and 2 (`signal_trace_engine.py:313-323`). Expected: node detail, projected
  the way stage 3 projects it. Actual: raw `DAGNodeTrace` instances in `nodes`, so the response
  fails JSON serialisation and the stages never render.

**Cluster E — a download page advertising four installers and serving none**

- `GET /releases/windows/VyomQuant-Setup-0.1.0.exe` on CloudFront. Expected: 112,117,309 bytes.
  Actual: **403**, verified. The same for the dmg, AppImage and deb. The page states their sizes as
  84.2 / 78.5 / 75.4 / 68.2 MB — four hardcoded strings matching nothing.
- `GET /robots.txt`. Expected: a short revalidating cache. Actual, verified in production:
  `public, max-age=31536000, immutable`. A correction cannot reach a returning visitor for a year.
- *Edge case:* `GET /` returns 200 with `max-age=0, must-revalidate`. That is correct today and
  3.14 requires it to stay correct.

## Expected Behavior

### Preservation Requirements

**Unchanged behaviours.** These are the `NOT isBugCondition(X)` paths — every one is a path where
the system *has* the fact it is being asked for. The full list is clauses 3.1–3.18; the ones this
design's changes come closest to breaking, and therefore the ones the preservation tests target:

- A genuinely flat account still reports `0.0`, and a genuinely empty ledger still reports `0.0`.
  `None` is reserved for "not read". Collapsing the two is the failure mode of cluster A's own fix
  and is the single most important preservation assertion in the pass (3.2).
- Real QuestDB equity rows are returned unmodified, in ascending timestamp order, with no
  synthesised endpoints appended (3.1).
- A valid, reachable credential still reports connected with `can_trade: true`, and measured
  latency still classifies `optimal` under 150 ms and `normal` under 500 ms (3.3).
- The backtest columns that already receive correct engine output keep receiving it, unchanged
  (3.4) — with the two corrections recorded above: `expectancy` is currently *overwritten* with
  `0.0` and `sortino_ratio` comes from the runtime, not the engine, so for those two the
  preservation baseline must be captured from the engine's value rather than from today's stored
  value.
- `update_backtest_results` keeps its `user_id` predicate, keeps returning `{}`
  indistinguishably for non-owned and nonexistent ids, and keeps degrading gracefully when
  `006_backtest_evidence_columns.sql` has not been applied (3.5).
- Every behaviour `vyomquant-ui-redesign` established at `939a428` holds: design tokens, the
  `design/errorCopy.js` / `design/errorLine.js` copy path with no `err.message` reaching the
  screen, column alignment, review-mode edit suppression, and the eight structural frontend guards
  (3.7).
- Authentication, the per-route rate limits (paper trading `120/minute` reads and `60/minute`
  operations; library `120/60second` plus `60/60second`) and existing ownership predicates apply
  unchanged (3.9).
- `MARKETPLACE_READ_FAILED` (503) is never folded into a 403, so a paying subscriber is never told
  they hold no subscription because a read broke (3.11). This is cluster A's rule applied to
  entitlement, and it is the reason the entitlement matrix test (2.18) asserts wire codes rather
  than status classes.
- The deploy pipeline keeps failing closed when `VITE_API_URL` was not substituted, keeps rejecting
  `ws://localhost:8000` in a production bundle, keeps serving `*.html` with
  `max-age=0, must-revalidate`, and keeps invalidating CloudFront and waiting (3.14).
- `POST /api/orders/execute` and `POST /api/orders/create` keep answering **403
  `MANUAL_EXECUTION_BLOCKED`**. `createOrder` is not repointed. This is a design decision, not a
  defect, and it constrains 2.13's proof: the risk-limit tests cannot use those two routes because
  they refuse everything.
- The stale duplicate frontend at `aerora_quant_platform/frontend_app/algo22-terminal` is left
  unmodified (3.16).
- CI keeps invoking the suite scoped. The bare full vitest run is never executed (3.18).

**Scope.** Every input that does *not* satisfy `isBugCondition` is unaffected. Specifically
untouched: successful reads of any figure; valid and reachable credentials; backtests whose engine
keys already match the writer's; mouse and keyboard interaction anywhere in the UI; every route
whose ownership predicate is already correct; every already-passing test under `tests/` and
`algo22-terminal/tests/`, including the property suites under `tests/property/`.

The correct behaviour for buggy inputs is defined once, in §Correctness Properties.

## Hypothesized Root Cause

Six mechanisms. Each is stated as the thing that has to change for the cluster's clauses to be
unable to recur, not as a description of the symptom.

### A. The response contract has no type for "not computed"

`get_dashboard_data` composes its `overview` block as `float(portfolio.get(key, 0.0))` for every
headline figure. That expression is the defect. It has two failure modes and no third option: a
number, or a `TypeError`. Absence is *unrepresentable at the response boundary*, so every producer
upstream is forced to invent a number, and the literal it invents — `100000.0` — is simply the
paper default capital leaking out of a `dict.get` fallback into a money field.

This is confirmed by the one field that escapes it. `realized_pnl` is read with
`_finite_float(portfolio.get("realized_pnl"))` and is the only nullable figure on the block,
because BC-5 needed it to be. The machinery exists, it works, and it is applied once.

The hardcoded `"connected"` / `35` in `get_exchange_data` is the same cause in its purest form: the
function has no probe to report, the field is typed as a non-optional string and int, so it
answers with a plausible constant. The honest implementation is twenty lines away in the same class
— `get_health_status` returns `latency_ms: None` with `latency_status: "unavailable"` when nothing
measured, and classifies a real measurement against the existing thresholds. `can_trade` follows:
it is derived from `len(rows) > 0`, which answers "does a row exist", when the question is "can
this credential place an order".

**Why it is one fix and not ten:** the ten sites differ only in which field they fabricate. One
representation change at the composer — figures become `Optional[float]`, lists keep the
`degraded` channel — makes every upstream default removable, and makes the *next* one impossible
to add without a test failing.

### B. Four vocabularies for one payload, and a lossy defaulting boundary

The backtest payload crosses three boundaries and changes vocabulary at each:

```
backtesting_engine.run_backtest_async
    emits  total_return_pct, final_equity, win_rate_pct, max_drawdown_pct, sharpe_ratio,
           sortino_ratio, profit_factor, total_trades, expectancy, trades
    returns (results, equity_curve)          <- the curve is OUT OF BAND
        |
backtest_runtime.run_backtest
    unpacks (stats, equity_curve)
    builds  {**stats, **performance_metrics, charts, execution_time_seconds,
             trades_count, final_capital}
    reads   stats["Final Equity"], stats["Max Drawdown [%]"], stats["Net Profit"],
            stats["Win Rate [%]"], stats["Best Trade"], stats["Avg Winning Trade"]
                                             <- VectorBT DISPLAY names. A FOURTH vocabulary,
                                                which `stats` has already been normalised away
                                                from. Every lookup misses.
    drops   equity_curve
        |
backtest_service.update_backtest_results
    reads   results.get("total_return", 0), .get("win_rate", 0), .get("max_drawdown", 0),
            .get("final_capital", 0), .get("equity_curve", [])
                                             <- DB COLUMN names, with numeric defaults
```

Three root causes stack here, and all three must be named because they have different fixes:

1. **`dict.get(key, numeric_default)` is a silent type-preserving translation of "absent" into
   "zero".** It is the same defect as cluster A, one layer down, and it is why the columns are
   wrong rather than missing.
2. **The equity curve is out of band.** A value returned as a tuple element rather than as a key
   of the payload has to be re-inserted by hand at every hop, and one hop forgot. The fix is to
   put it in the payload, so forgetting is impossible.
3. **No boundary validates.** There is no schema, no `TypedDict`, no assertion that the writer's
   keys are among the producer's. That absence is why a rename in the engine can silently zero a
   column, and it is why the key-set contract test — not a set of per-field assertions — is the
   right instrument: it fails on the next rename too.

### C. Transport ownership is conventional, not structural

`websocketClient` provides everything needed for one socket: `acquire`/`release` refcounting
(so three views take three holds on one connection), `subscribeChannel` with per-channel refcounts,
`reauthenticate` for token refresh without a reconnect, jittered capped backoff, and sequence-gap
replay. It is a good transport. Nothing *prevents* a component from calling `new WebSocket`
directly, so two components did.

Three consequences, each a separate root cause:

1. **Authentication in the URL is a property of the transport.** `connect()` appends
   `?token=<JWT>`, and every backend route signature is `token: str = Query(...)`. So this is one
   contract spanning nine routes and one client — not a Billing bug. The browser `WebSocket`
   constructor cannot set headers, so the only honest fixes are a short-lived single-use ticket
   or a cookie. 2.21 names the ticket.
2. **Reconnect scheduling is owned by whoever wrote the `onclose` handler.** Billing's handler
   schedules a reconnect on *every* close, including the one its own cleanup causes. The shared
   client already distinguishes intentional teardown (`disableReconnect`, `release`) from a dropped
   connection. The defect is not a missing `clearTimeout`; it is that a second socket implementation
   re-solved a solved problem and got it wrong.
3. **`STRATEGY_STATUS` has no single definition of its frame.** The subscriber names it in upper
   case, the only broadcaster wraps it in a `dashboard_update` envelope under a lower-case
   `update_type`, and the router matches `message.type` exactly. Three independent spellings, zero
   shared fixture. A test on either side alone passes while the contract is dead — which is exactly
   how this got through.

### D. The client/server route contract is never checked in either direction

Four frontend modules call routes that do not exist, and three of them do it through an identifier
that is not imported. Both halves are invisible to the current build:

- `post` is a free identifier inside a `useCallback`. ESLint's `no-undef` would catch it — but no
  workflow runs ESLint (1.34), so nothing does.
- Route existence is nobody's assertion. The backend publishes an OpenAPI schema and the frontend
  has a finite set of call paths, and the two are never compared.

`CopilotContext` is a different case and needs a different disposition: it is self-documented
"Intentionally DORMANT", `CopilotProvider` is mounted nowhere, and it bypasses the `api` client
entirely with raw `fetch` + `getAuthHeaders()`. It is dead code that happens to contain two broken
URLs. 2.28's own escape hatch applies — remove, do not implement.

`signal_trace_engine`'s serialisation defect has the same shape at a smaller scale: the
`DAGNodeTrace -> dict` projection exists, but as an **inline expression inside one of three stage
literals**. It could not be reused, so two stages were written without it. The fix is to name the
projection; the three stages then cannot disagree.

### E. The deployed byte set is assembled by convention

`aws s3 sync dist/ --delete` publishes whatever happens to be in `dist/`, and `dist/` is whatever
Vite happens to copy from `public/`. Nothing declares what *should* be published, so three
classes of error are all invisible:

1. **Provenance.** A 64-byte text file named `.AppImage` is indistinguishable from a real one to
   every step in the pipeline. `public/releases/` being gitignored means CI never sees them — but
   it also means CI publishes *nothing* there, and `--delete` removes what was there before. The
   download page's four links and four hardcoded sizes are the user-visible half of the same
   absence of a declaration.
2. **Cache policy is keyed on file extension, not on whether the name is content-hashed.** The
   sync excludes `*.html` and `*.map` and gives everything else one-year immutable — so
   `robots.txt`, `sitemap.xml` and four SVGs, none of which carry a content hash, are immutable for
   a year. Verified live.
3. **Verification steps encode the current topology as literals.** The two-host grep is correct
   until `VITE_API_URL` changes, and then it fails the deploy and reports it as a build error. The
   uncommitted `ALLOWED_API_HOSTS` allow-list is the right shape — a declared set — and its being
   uncommitted is itself the release blocker.

And the ceiling that guards all of this is mis-sized: `01-pr-check.yml`'s `timeout-minutes: 40`
was measured against a 53-file frontend suite that is now 131 files, run sequentially by design.

### F. Fifteen safety properties have no proof, and two of them nearly do

For the 15 UNVERIFIED clauses the root cause is not in the code — it is that nothing in the tree
decides the question. Two distinct situations, with different fixes:

- **The machinery exists and is under-scoped.** `test_worker_crash_mid_submission.py` already
  establishes live-path order idempotency across twenty cases;
  `test_cross_tenant_ownership_matrix.py` already sweeps seven resource types with route-resolution
  and vacuity checks. The fix is to *extend* these, and to record what each covers. Writing a
  parallel sweep would create two partial answers instead of one complete one.
- **Nothing exists.** Server-side risk enforcement, paper-live adapter separation, concurrent
  strategy lifecycle operations, the entitlement × state matrix, secret-in-bundle scanning, and
  the 422-not-500 validation sweep have no coverage. Each needs a named test.

One structural constraint shapes several of these: `POST /api/orders/execute` and `/create` answer
403 `MANUAL_EXECUTION_BLOCKED` by design, so the only path by which an order reaches a venue is
strategy deployment. 2.13's proof must therefore target the paper order route and the internal
execution service, not the two routes a reader would reach for first.

## Correctness Properties

The two global properties come first, because the whole pass is one bug condition. The eleven that
follow are the per-mechanism properties that make the global two checkable.

Property 1: Bug Condition - A fact the system does not have is reported as absent, never invented

_For any_ interaction where the bug condition holds (`isBugCondition` returns true), the fixed
system SHALL answer with an explicit unavailability — a `null` figure carrying a reason, an empty
series, a 404, a 422, or a 503 — or with a refusal carrying a distinct machine-readable code, or
with the prior order rather than a second one; and SHALL NOT answer with a synthesised value and
SHALL NOT answer 500.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.13, 2.14, 2.30, 2.39**

Property 2: Preservation - Every path that has its fact answers exactly as it does today

_For any_ interaction where the bug condition does NOT hold (`isBugCondition` returns false), the
fixed system SHALL produce the same result as the code at `939a428` plus the current working tree,
preserving every successful read's value and type, the per-route rate limits, the existing ownership
predicates, the `MANUAL_EXECUTION_BLOCKED` refusals, the eight structural frontend guards and every
behaviour the `vyomquant-ui-redesign` spec established.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13, 3.14, 3.15, 3.16, 3.17, 3.18**

Property 3: Absent is not zero, and zero is not absent

_For any_ headline financial figure on the dashboard response, the fixed service SHALL report `None`
when the producing read failed, did not run, or returned a value that is not a finite number, and
SHALL report `0.0` only when a read succeeded and its value is genuinely zero; and the two cases
SHALL be distinguishable by the client without consulting anything outside the response.

**Validates: Requirements 2.1, 2.2, 2.4**

Property 4: An empty read is an empty series, in every environment

_For any_ equity-curve request that finds no rows, the fixed service SHALL return `[]` for paper as
it already does for live, and SHALL NOT synthesise endpoints; and the rendering surface SHALL show
its no-history state rather than a line.

**Validates: Requirements 2.3, 3.1**

Property 5: Venue health is measured or unknown, never asserted

_For any_ exchange connection whose credential has not been successfully probed, the fixed service
SHALL report `status: "unknown"` and `latency_ms: null`, and SHALL derive `can_trade` from
credential validity rather than row existence, yielding `false` when validity is unknown; and for a
connection that HAS been probed it SHALL report the measured latency classified by the existing
thresholds.

**Validates: Requirements 2.5, 2.6, 3.3**

Property 6: The writer reads only keys the producer emits

_For any_ completed backtest, on either engine path, the set of keys
`backtest_service.update_backtest_results` reads from the results payload SHALL be a subset of the
keys the payload actually carries, and the payload SHALL carry the equity curve in band; so no
persisted column can hold a defaulted value standing in for a name mismatch.

**Validates: Requirements 2.7, 2.8, 2.9, 2.10, 3.4**

Property 7: One socket, one credential, and no credential in a URL

_For any_ session, exactly one WebSocket SHALL be open with Billing and market data multiplexed over
it; the constructed socket URL SHALL contain no `token` parameter and no JWT in any position; and
every pending reconnect timer SHALL be cancelled on unmount so no socket is opened after the
component that owns it is gone.

**Validates: Requirements 2.21, 2.22, 2.23**

Property 8: A published frame is one frame, spelled the same on both sides

_For any_ strategy start or stop, the backend SHALL publish exactly one status frame whose envelope
the frontend's router matches and whose channel name the frontend's subscriber spells identically,
established by one fixture asserted on both sides.

**Validates: Requirements 2.24**

Property 9: Every client call path resolves to a route that exists with the method used

_For any_ HTTP call the frontend can issue, the path-and-method pair SHALL appear in the backend
OpenAPI schema, and the module issuing it SHALL have every identifier it references in scope; or the
call path SHALL not exist.

**Validates: Requirements 2.25, 2.26, 2.27, 2.28**

Property 10: Every stage of a serialised trace is JSON-serialisable

_For any_ signal trace, `json.dumps(to_frontend_format())` SHALL succeed, and every stage's `nodes`
SHALL be dicts produced by one shared projection rather than by per-stage expressions.

**Validates: Requirements 2.29**

Property 11: No artifact is published whose bytes are not a build output, and no unhashed asset is immutable

_For any_ file the deploy publishes under a `releases/` path, its size SHALL be plausible for its
extension or the build SHALL fail; and _for any_ asset whose filename carries no content hash, the
cache-control SHALL be short and revalidating rather than one-year immutable.

**Validates: Requirements 2.30, 2.40**

Property 12: A tenant boundary answers indistinguishably, and writes nothing

_For any_ user-scoped route and _for any_ resource id belonging to another tenant, the response
SHALL be byte-identical to the response for an id that names nothing — status, headers and body —
and no row SHALL be written; and the owner SHALL be answered differently, so the assertion is not
vacuous.

**Validates: Requirements 2.14, 2.15**

Property 13: A duplicate submission acknowledges the first order

_For any_ sequence of submissions and retries carrying one idempotency key, across process
restarts, the number of orders created SHALL be exactly one, and every submission after the first
SHALL be answered with that order.

**Validates: Requirements 2.11, 2.17**

## Fix Implementation

### Changes Required

Ordered by dependency, not by severity. Each wave is defined by the fact that its members share a
mechanism and must land together or not at all.

---

#### Wave 0 — Release blockers. Gates the merge, therefore gates everything.

Nothing in waves 1–5 can reach production until wave 0 lands, because the merge to `main` that
carries them auto-fires `06-frontend-deploy.yml`. Wave 0 is deliberately the smallest possible set
that makes that merge safe.

**0a. `ALLOWED_API_HOSTS` (1.31, 2.31).**
**File:** `.github/workflows/06-frontend-deploy.yml` — already written in the working tree, 24
insertions, uncommitted.

The change replaces the two-host grep with a declared allow-list of five hosts
(`app.vyomquant.in`, `www.vyomquant.in`, `vyomquant.in`, `api.vyomquant.in`,
`d7d88qs4jmch.cloudfront.net`). **Reconciling it with the old grep on `main` without a failed
cutover:** the allow-list is a strict superset of the two hosts the old grep accepted, so it passes
for every bundle the old check passed. That is what makes the ordering safe, and it is why this must
be committed *before* the `VITE_API_URL` secret is cut over to `app.vyomquant.in`, never after:

1. Commit the allow-list to `main`. The deploy this triggers builds a bundle still containing
   `d7d88qs4jmch.cloudfront.net`, which the allow-list accepts. Deploy is green.
2. Only then cut the `VITE_API_URL` secret over to `app.vyomquant.in`.
3. The next deploy's bundle contains `app.vyomquant.in`, which the allow-list accepts.

Cutting the secret first fails step 3's check before step 1 exists, and reports it as
`VITE_API_URL not properly substituted` — a build error for a configuration change. That
misattribution is the defect 1.31 names, and the ordering above is the whole fix for it.

**0b. Release artifact provenance (1.30, 2.30).**
**Files:** `algo22-terminal/public/releases/**` and `algo22-terminal/dist/releases/**` (delete);
`.github/workflows/06-frontend-deploy.yml` (add gate);
`algo22-terminal/src/components/download/DownloadPage.jsx` and
`algo22-terminal/src/components/landing/DownloadSection.jsx` (withdraw the unbacked links).

1. Delete the three fake files from `public/releases/` and `dist/releases/`. They are untracked, so
   this is a working-tree deletion with no commit — which is exactly why a **gate** is required
   rather than a deletion: without one they reappear the next time anyone copies a placeholder in.
2. Add a build-time gate that fails when any file under a `releases/` path is implausibly small for
   its extension. Minimums, generous enough that a real build never trips them: `.exe`/`.dmg`
   ≥ 20 MB, `.AppImage`/`.deb` ≥ 10 MB. The gate runs on `dist/` after `vite build`, before the
   sync, so it fails the deploy rather than publishing.
3. **The download surface must be made honest, and this is the risky half.** Four links currently
   404 and four sizes are hardcoded strings. Two options, and the safe one is not the obvious one:
   - *Publish the real artifacts.* The Windows installer is 112 MB and lives at
     `algo22-terminal/releases/`, a sibling of `public/` that Vite never copies, and `releases/` is
     gitignored. Making CI publish it means either committing 112 MB to the repository — which
     inflates every clone and every CI checkout permanently — or adding a separate artifact upload
     step outside the `dist/` sync. The mac and linux builds do not exist at all.
   - *Withdraw the surface.* Render each platform's card through the existing not-available
     convention: `usePanelState`'s `unavailable` state with a reason, which short-circuits without
     issuing a request. The hardcoded sizes go with the links — a size string for a file that does
     not exist is the same fabrication as a fabricated balance, and it is on a rendered surface.
   **Withdraw is the recommendation.** It is reversible in one commit, it removes four 404s and four
   fabricated figures immediately, and it does not couple the launch to producing and signing three
   desktop builds. Restoring a platform later is additive: produce the artifact, upload it, flip the
   card's verdict in `design/pageFields.js`. Publishing 112 MB through git to meet a launch date is
   the change that is hard to undo.

   *This is a change to a rendered surface, so per the constraint: the element that carries it is
   `ds/Panel` driven by `usePanelState`'s existing `unavailable` state, with the reason string
   declared in `design/pageFields.js` as `VERDICT.UNAVAILABLE`. No new primitive.*

**0c. Commit the reviewed working tree (1.32, 2.32).**
14 modified paths plus 5 untracked, committed in purpose-named commits with `git commit -q -m`.
1.32's list omits two modified paths found by measurement: `algo22-terminal/bundle-analysis.html`
(a build artifact — exclude, or gitignore it) and `.kiro/specs/vyomquant-ui-redesign/tasks.meta.json`
(spec bookkeeping — include). `tests/test_migration_tooling.py` and
`tests/test_cors_configuration.py` must pass before the commit that carries them.

**0d. ESLint in CI (1.34, 2.34).**
**File:** `.github/workflows/01-pr-check.yml`.
This belongs in wave 0 rather than with the P2s because it is the gate that would have caught wave
4's `post is not defined`, and because cluster D's fix is not durable without it. 58 errors must
reach zero; the 246 warnings are capped at their then-current count. Command:
`node node_modules/eslint/bin/eslint.js src`.

**0e. Deploy-time cache policy and the CI ceiling (1.40, 1.41, 2.40, 2.41).**
`06-frontend-deploy.yml`: the immutable sync is narrowed to content-hashed asset paths
(`assets/**`), and `robots.txt`, `sitemap.xml` and the four SVGs move to a short revalidating
policy. Verified live: `robots.txt` currently carries `max-age=31536000, immutable`, so this has
already happened and the correction must be accompanied by a CloudFront invalidation for those
paths — an immutable object already in an edge cache does not re-fetch on its own.
`01-pr-check.yml:132`: the justification comment is re-measured. The measurement to record is the
observed wall clock of the **131-file** frontend suite, not the 53 it was written for, and the
ceiling is set from that measurement rather than kept at 40.

**0f. Mark the stale duplicate frontend dead (1.35, 2.35).**
A check asserting no build or deploy path references
`aerora_quant_platform/frontend_app/algo22-terminal`. The tree itself is **not modified** (3.16).

---

#### Wave 1 — Absent vs zero. One representation, ten sites.

**File:** `backend_app/backend/dashboard_aggregation_service.py`. **Related:**
`backend_app/routers/dashboard.py`.

This is the wave that touches money figures, so it is sequenced first among the code changes and
carries the heaviest preservation burden. **The risk is specific and worth stating plainly: the
failure mode of this fix is collapsing a genuine `0.0` into `None`, which would render a flat
account as unreadable and is a *new* lie in the opposite direction.** That is why Property 3 is
two-sided and why the preservation test (below) asserts the zero case as hard as the null case.

1. **The composer stops coercing.** Replace `float(portfolio.get(key, 0.0))` with
   `_finite_float(portfolio.get(key))` for every figure on the `overview` block, exactly as
   `realized_pnl` is already read. `_finite_float` already exists at line 314 and already returns
   `None` for non-numbers, NaN, infinity and booleans. This is the change that makes every downstream
   removal possible; nothing else in wave 1 works without it.
2. **`get_portfolio_overview`'s paper branch (1.1, 1.4).** Remove the `100000.0` defaults from
   `acct.get(...)` — read through `_finite_float` and propagate `None`. The paper failure branch
   (`:958-961`) already sets `realized_pnl: None` with a BC-5 comment explaining why; the remaining
   fields join it.
3. **The composer's exception branch (1.2).** `100000.0 if paper else 0.0` becomes `None` in both
   environments. The environment-dependent literal is the exact artefact 1.2 describes: a live
   trader shown zero equity on a failed read.
4. **`get_equity_curve` (1.3).** Delete the paper synthesis branch. `return []` for every
   environment, as live already does.
5. **`get_exchange_data` (1.5, 1.6).** `status` and `latency_ms` come from the probe or are
   `"unknown"` / `None`. `can_trade` is derived from credential validity and is `false` when
   validity is unknown. `get_health_status` at `:1456-1486` is the model — it already reads measured
   latency from Redis, already returns `None` / `"unavailable"` when nothing measured, and already
   classifies `optimal` / `normal` / `degraded` against the thresholds 3.3 preserves.
6. **`get_recent_signals` (1.36).** Add `strategy_id` to the `select` and carry the strategy id and
   name onto each item. One extra column on an existing query.
7. **Route disposition.** `routers/dashboard.py::get_dashboard_overview` already raises 503 rather
   than degrading, because every figure it returns is a headline figure — that judgement is correct
   and is preserved. `get_dashboard_data` continues to degrade, because balances, curve and
   executions on the same response are still real reads.

**Frontend consequence (2.3, and the null figures from 1–3).** No new work. Figures already flow
through `ds/Metric`, which accepts `Reported<T>` or a raw value and renders the not-available marker
with a reason for `null`. Each field's reason string is already declared in `design/pageFields.js`
— including `exchange_api_latency_ms`'s note that it "must never render as 0 ms". The pages adopt
`fromNullable(read(body, path), entry.reason)` per field, which is the pattern `pageFields.js`'s own
header documents. The equity chart's empty state is `usePanelState`'s `empty` state, which `[]`
already produces via `isEmptyPayload`.

---

#### Wave 2 — Backtest key sets. One contract, three boundaries.

**Files:** `backend_app/backend/backtesting_engine.py`,
`backend_app/backend/backtest_runtime.py`, `backend_app/backend/backtest_service.py`.

Landing order within the wave matters: the contract test is written first and is expected to fail,
because it is what tells us the rename set is complete.

1. **Declare the payload's key set once** — a module-level frozenset of emitted keys beside the
   engine, and the writer's read-key set derived from it. This is the artefact the contract test
   asserts over; without it the test is a second hand-maintained list.
2. **Engine emits `total_pnl` (1.10).** `final_equity - initial_equity_logged` is already computed
   at `backtesting_engine.py:405-408` — into a log line. It becomes a key. 2.10 allows removing the
   field from the UI instead; emitting it is strictly better, because the value already exists and
   a permanently not-available metric on a results screen is what 2.10 itself rules out.
3. **Equity curve moves in band.** The fallback path already does `results["equity_curve"] = equity_curve`
   before returning the tuple; the vectorbt path does not. Make both set the key, keep the tuple
   return for existing callers, and have `backtest_runtime` read the key rather than the tuple
   element. That removes the drop site rather than patching it.
4. **`backtest_runtime` stops reading VectorBT display names.** `stats["Final Equity"]` →
   `stats["final_equity"]`; and the five display-name reads in `_calculate_performance_metrics`
   (`Max Drawdown [%]`, `Net Profit`, `Win Rate [%]`, `Best Trade`, `Avg Winning Trade`) are
   repointed at the keys the engine emits or removed where nothing emits them. **This is the site the
   requirements do not name and it is where `final_capital: 100000.0` comes from.**
5. **Fix the merge order.** `{**stats, **performance_metrics}` lets the runtime's `0.0` overwrite the
   engine's real `expectancy`. Either invert the precedence or stop having two producers for one key.
   Inverting is the smaller change but it silently re-points a stored column, so it needs its own
   preservation assertion (see 3.4's correction above).
6. **Writer reads the declared keys, with no numeric defaults.** `results.get("win_rate", 0)` →
   read `win_rate_pct`; `total_return` → `total_return_pct`; `max_drawdown` → `max_drawdown_pct`;
   `final_capital` → `final_equity`. Where a DB column name differs from the engine key, the
   *mapping is explicit and declared*, not implied by a matching string. A key that is genuinely
   absent writes SQL `NULL`, not `0` — the same rule as wave 1.

   *DB column renames are an option 2.8 permits, but a rename is a migration against a table that
   already holds rows. Mapping in the writer is reversible; a migration is not. Map.*
7. **`update_backtest_results` keeps its `user_id` predicate and its `{}` return** (3.5). That
   predicate was added because its absence made this a cross-tenant write, and it is the precedent
   2.14's sweep generalises. Nothing in this wave touches it.

---

#### Wave 3 — Transport ownership. One socket, one credential.

**Files:** `algo22-terminal/src/websocketClient.js`, `algo22-terminal/src/pages/Billing.jsx`,
`algo22-terminal/src/contexts/DataPipelineContext.jsx`,
`backend_app/api_ws/ws_routes.py`, plus a new ticket route.

**This is an auth change and it is the riskiest wave in the pass.** It alters how every socket in
the application authenticates, across nine backend routes. The sequencing below exists to make each
step independently revertable, and step 1 is deliberately not the credential change.

1. **Collapse to one socket first, with no auth change.** Billing drops `new WebSocket` and takes
   `wsClient.acquire()` + `subscribeChannel('billing', handler)`; its `onclose` reconnect scheduling
   is deleted outright, because the shared client already owns backoff, jitter and the
   intentional-teardown distinction. `DataPipelineContext.connectLiveData` does the same — and note
   its socket pointed at `/ws/market-data`, which does not exist, so this step also deletes a dead
   path rather than migrating a working one. Effect: 1.22 and 1.23 close, and the credential is now
   exposed in exactly **one** place instead of three. That alone is worth landing on its own.
2. **Then replace the credential.** A new `POST /api/ws/ticket` returns a single-use,
   short-TTL (≤ 30 s) opaque ticket bound to the user and consumed on first use. `connect()` fetches
   a ticket over HTTPS and passes `?ticket=` instead of `?token=`. The backend routes accept
   `ticket` and resolve it to a user; the existing `token` query parameter is kept accepting for one
   release so a cached bundle does not lose its socket mid-session, then removed.
   *Why not a header:* the browser `WebSocket` constructor cannot set request headers. A ticket is
   what 2.21 permits and it is the only option that does not require moving session auth to cookies.
   *Why the ticket is not just a shorter JWT:* it is single-use, so a logged ticket cannot be
   replayed — which is the actual harm in 1.21, since access logs are retained.
3. **Unify the token store.** The shared client reads `sessionStorage`, Billing reads
   `localStorage`. One store, chosen to match whatever `api`'s HTTP client already uses, so a session
   cannot be authenticated for HTTP and anonymous for the socket.
4. **`STRATEGY_STATUS` (1.24).** Publish from the strategy start/stop service paths, with the frame
   shape defined in **one** shared fixture. The frame's `type` must be the string
   `wsClient.subscribeStrategyStatus` subscribes to — upper-case `STRATEGY_STATUS` — because
   `processMessage` matches `message.type` exactly and routes nothing otherwise. Do not route this
   through `broadcast_dashboard_update`: that wraps everything as `type: "dashboard_update"` and
   would leave the contract just as dead, in a way that a backend-only test would not notice.

---

#### Wave 4 — Route contract. Make the mismatch impossible, then fix the four instances.

**Files:** `algo22-terminal/src/contexts/{IndicatorEngine,LogicEngine,StrategyEngine}Context.jsx`,
`algo22-terminal/src/contexts/CopilotContext.jsx` (delete),
`backend_app/backend/signal_trace_engine.py`.

1. **The general check first (2.28's own observation that it generalises to 2.25–2.27).** A test
   that enumerates every frontend call path and asserts each appears in the backend OpenAPI schema
   with the method used. This is what stops the fourth instance from becoming a fifth. ESLint in CI
   (wave 0d) is the other half: `no-undef` catches the unimported `post`.
2. **The three engine contexts (1.25–1.27).** Each currently raises `ReferenceError` before it
   reaches the network, and no matching route exists. Two dispositions, per capability:
   - If the computation belongs server-side, import the client properly and point at a route that
     exists.
   - If it does not, remove the client-side call path and let the builder's local computation stand.
   The builder already computes indicators locally via `DataPipelineContext` and
   `utils/engineHelpers`, which is why these calls have never been missed. **Removing is the
   likelier correct answer and should be confirmed per capability before implementing** — the
   clause's requirement is a *defined outcome*, not necessarily a network call.
3. **`CopilotContext` (1.28).** Delete the module. `CopilotProvider` is mounted nowhere, the file
   documents itself as dormant, and it bypasses the `api` client with raw `fetch`. 2.28 explicitly
   permits removal over implementation. Deleting also removes a second auth path and a second error
   surface that does not go through `design/errorCopy.js`.
4. **`signal_trace_engine.to_frontend_format` (1.29).** Extract the inline `DAGNodeTrace -> dict`
   projection from the `dag_nodes` stage literal into a named function, and call it from all three
   stages. Naming it is the fix; the two broken stages are the symptom. The three stages then cannot
   disagree, which is the property that matters more than the two current instances.

---

#### Wave 5 — Local correctness and presentation.

1. **`UndoRedoContext` (1.38).** The fix is at the **loader**, not the predicate. `canUndo =
   currentIndex > 0` is correct for a history whose index 0 is the pre-edit baseline; it is wrong for
   one whose index 0 is the first edit. Seed the baseline with `pushState` when a saved strategy
   loads. Changing the predicate to `>= 0` instead would make `undo()` return `history[-1]` —
   `undefined` — so that direction is wrong. While here, note but do not necessarily fix:
   `pushState` calls `setCurrentIndex` *inside* the `setHistory` updater (a side effect React
   StrictMode double-invokes) and skips the increment on the `maxHistory` shift branch. Those are
   pre-existing and out of 1.38's scope; record them rather than widen the change.
2. **`browse_library` (1.37).** `_enrich_cards_with_user_context(items, svc, user_id)` already
   exists and already folds per-caller state onto cached, projected items. Add subscription state to
   that existing pass with one bulk query keyed by the page's listing ids — which is how
   `my-strategies` already achieves three round trips independent of entry count (3.13). No per-card
   query. The badge renders through the existing `design/subscriptionState.js` view, which already
   reads `subscription.state`, `period_expiry`, `renewal_state`, `entitling` and
   `unavailable_reason`.
3. **`Drawer` scrim (1.45).** When `dismissOnScrim` is false the scrim must not be a disabled button
   carrying an accessible name. Render a non-interactive element with no accessible name in that
   case. Latent today — no consumer passes `false` — so this is a contract fix on the primitive.
4. **Overlay contention (1.46).** `AccountMenu` claims `OVERLAY_KIND.DRAWER`, the same kind
   `Drawer` claims, so the builder inspector and the shell menu contend for one registry slot at
   tablet widths. `ds/overlayRegistry.js` already arbitrates and already raises
   `OverlayConflictError` in development. Define which wins; do not add a third overlay kind —
   `tests/unit/ds/overlayRegistry.test.js` pins the two-kind contract.
5. **Dependabot triage (1.33).** 295 alerts: 8 critical, 60 high, 127 medium, 100 low. Every
   critical and high on a **runtime** dependency is resolved or carries a checked-in, justified
   exception naming why it is not exploitable here. Development-only alerts defer to P2. The triage
   record distinguishing runtime from development is the deliverable, because its absence is what
   1.33 actually names.

### Change Ordering and Deployment Safety

**The merge is the deploy.** `06-frontend-deploy.yml` fires on push to `main` for any change under
`algo22-terminal/**`. There is no staging gate between merge and CloudFront.

**Order:**

1. **Wave 0 lands on the feature branch and is verified there.** Every wave-0 item is a repository
   or workflow change and none of them requires the deploy to run. `tests/test_migration_tooling.py`
   and `tests/test_cors_configuration.py` pass; `node node_modules/eslint/bin/eslint.js src` reports
   zero errors; the `releases/` size gate is exercised against a deliberately undersized file to
   confirm it fails.
2. **Waves 1–5 land on the feature branch,** each with its regression test passing and the
   preservation suites green.
3. **`ALLOWED_API_HOSTS` merges to `main` before the `VITE_API_URL` cutover,** per 0a. The
   allow-list is a superset of the old grep, so this deploy is green with the existing secret.
4. **Merge to `main`.** The deploy fires. The `releases/` gate, the localhost-fallback check, the
   service-role-key check and the API-host allow-list all run before the sync.
5. **Cut `VITE_API_URL` over to `app.vyomquant.in`** and re-deploy.
6. **Verify (2.47),** then invalidate the paths whose cache policy changed in 0e — those objects are
   in edge caches with a one-year immutable header and will not re-fetch otherwise.

**Backend deploy and rollback.** `03-deploy.yml` verifies an immutable ECR artifact exists for the
commit SHA before deploying (3.15) and that check stays. For a bad revision:

- **Diagnose before retrying.** A blind re-deploy of the same task definition reproduces the same
  failure and costs another rollout window. Read, in order: the ECS service's `events` (for
  placement and health-check failures), the stopped task's `stoppedReason` and container
  `exitCode` (for a crash-on-boot), and the target group's health state (for a service that starts
  but fails its probe). These distinguish the three failure classes that look identical from the
  service status alone.
- **Roll back to the named last-known-good revision:** `vyomquant-api:146`, currently `ACTIVE 1/1`
  on `vyomquant-cluster` / `vyomquant-api-service-cjema2sl`. `update-service --task-definition
  vyomquant-api:146 --force-new-deployment`, then confirm `runningCount == desiredCount` on 146.
- **Frontend rollback** is a re-deploy of the previous commit plus an invalidation. The `--delete`
  flag on the sync means the bucket mirrors `dist/` exactly, so there is no partial state to
  reconcile — but it also means a rollback removes any object the previous build did not produce,
  which is precisely how `/releases/` came to be empty. Anything published outside the `dist/` sync
  must be re-uploaded after a rollback.
- **The IAM principal is deploy-scoped** (`arn:aws:iam::273709947018:user/github-actions`). If a
  diagnostic call is denied, record the specific denied call rather than reporting the clause
  passed (2.47).

**Changes that must not be batched with anything else,** because each needs an independently
observable before/after:

- Wave 1's composer change — it alters the type of every money figure on the dashboard response.
- Wave 3 step 2 — the socket credential change, across nine routes.
- Wave 2 step 6 — the writer's key mapping, because it changes what lands in persisted columns.

## Testing Strategy

### Validation Approach

Two phases. First, surface counterexamples on the **unfixed** tree, so the root-cause hypotheses in
§Hypothesized Root Cause are confirmed or refuted before any code moves. Then verify the fix holds
for every buggy input and that behaviour is unchanged for every non-buggy one.

Three rules govern every test named below, and all three exist because of how these defects got
through in the first place:

1. **A contract between backend and frontend is pinned on BOTH sides, from one fixture.** The
   backtest key set, the `STRATEGY_STATUS` frame and the Signal Trace node projection each have a
   backend test and a frontend test reading the same declared shape. A test on one side alone passes
   while the contract is dead — that is not a hypothetical, it is the observed history of 1.24.
2. **Every P0 and P1 names its file and its assertion,** and the assertion must fail against `F`.
   A test that passes before the fix has not established the fix.
3. **Tooling, without exception.** `node node_modules/vitest/vitest.mjs --run <path>` — always
   scoped, never the bare suite, which exceeds 25 minutes. `node node_modules/eslint/bin/eslint.js`.
   `git commit -q -m`. `$env:NODE_OPTIONS="--max-old-space-size=2048"` for the build. PowerShell.

### Exploratory Bug Condition Checking

**Goal:** surface counterexamples that demonstrate the bug on the unfixed tree, and confirm or refute
each hypothesised root cause. Where a hypothesis is refuted, re-hypothesise before writing the fix.

**Test plan:** write the assertion first, run it against `F`, record what it produces. Four
hypotheses are already confirmed by direct measurement during this design and are recorded rather
than re-derived:

| Hypothesis | Status | Evidence |
|---|---|---|
| E: `robots.txt` is immutable-cached in production | **CONFIRMED** | `HEAD /robots.txt` → 200, `public, max-age=31536000, immutable` |
| E: the four advertised installers are not served | **CONFIRMED** | `HEAD` on all four `/releases/*` → 403 |
| E: 58 ESLint errors, 246 warnings | **CONFIRMED** | `node node_modules/eslint/bin/eslint.js src` |
| E: the frontend suite is 2.5× the size the CI ceiling was measured at | **CONFIRMED** | 131 tracked test files vs the comment's 53 |

**Exploratory cases, run against `F`:**

1. **Cluster A — force each read to raise** and record the exact response. Expected on `F`:
   `100000.0` in paper, `0.0` in live, a two-point flat curve, `"connected"` / `35`,
   `can_trade: true`. Confirms the hypothesis that the composer's `float()` is what forces a number.
   *Refutation signal:* if a `None` reaches the response without raising `TypeError`, the composer is
   not the only gate and the fix is larger than wave 1.
2. **Cluster B — run a real short backtest** and dump the payload at each of the three boundaries.
   Expected on `F`: the engine's key set and the writer's read-key set are disjoint for four columns;
   `equity_curve` present in the engine's return and absent from the runtime's `results`;
   `final_capital == 100000.0`. Confirms the four-vocabulary hypothesis and, critically, tells us
   whether the display-name reads in `_calculate_performance_metrics` are the full extent of the
   blast radius or only part of it.
3. **Cluster C — mount Billing, unmount it, advance fake timers past 5 s.** Expected on `F`: the
   `WebSocket` constructor is called a second time after unmount. Then assert the constructed URL:
   expected on `F`, a `token` parameter on *both* Billing's socket and `wsClient.connect`'s.
4. **Cluster C — construct `DataPipelineContext`'s live URL** and print it. Expected on `F`:
   `wss://…/ws/telemetry?token=<JWT>/ws/market-data`. Confirms the concatenation defect *and* that
   the route does not exist.
5. **Cluster D — call each engine context's compute function.** Expected on `F`: `ReferenceError`.
6. **Cluster D — `json.dumps(trace.to_frontend_format())`.** Expected on `F`: `TypeError`, naming
   `DAGNodeTrace`.
7. **Cluster F — the 15 UNVERIFIED clauses.** Each exploratory run *is* the proof, and the outcome
   determines whether the clause closes or produces a new numbered P0. This is the one place where
   "the test passes on `F`" is an acceptable and expected result.

**Expected counterexamples:** a failed read rendered as a funded account; a computed metric
persisted as `0`; a socket opened after its component unmounted; a credential in an access-loggable
URL; a `ReferenceError` inside a `useCallback`; a 403 where a 200 with bytes was advertised.

### Fix Checking

**Goal:** verify that for every input where the bug condition holds, the fixed code produces the
expected behaviour.

**Pseudocode:**

```
FOR ALL X WHERE isBugCondition(X) DO
  result := F'(X)
  ASSERT expectedBehavior(result)
END FOR
```

**Named regression tests. Every P0 and P1, with the assertion that would have caught it.**

| Clause | Test file | The assertion |
|---|---|---|
| 1.1 P0 | `tests/test_dashboard_absent_figures.py` | Paper portfolio read raises → no `100000.0` anywhere in the response, and each affected field is `None` |
| 1.2 P0 | `tests/test_dashboard_absent_figures.py` | Composer's portfolio branch raises → `None` in **both** environments, and the response is not numerically equal to a genuine zero-balance response |
| 1.3 P0 | `tests/test_dashboard_absent_figures.py` + `algo22-terminal/tests/unit/dashboard/equityCurveEmpty.test.jsx` | Empty QuestDB, paper → `[]`; and the chart renders `usePanelState`'s empty state, not a line |
| 1.4 P0 | `tests/test_dashboard_absent_figures.py` | Parameterised over each of `total_equity`, `available_balance`, `initial_capital` missing → no synthesised default |
| 1.5 P0 | `tests/test_exchange_health_probe.py` | Unprobed connection → `status == "unknown"`, `latency_ms is None`; and the literal `35` does not appear in the module |
| 1.6 P0 | `tests/test_exchange_health_probe.py` | Revoked/unvalidated key → `can_trade is False` and the connection is not reported connected |
| 1.7 P0 | `tests/test_backtest_key_contract.py` | Writer's read-key set ⊆ engine's emitted-key set, asserted per column, on **both** engine paths (vectorbt and the fallback) |
| 1.8 P0 | `tests/test_backtest_key_contract.py` | Same contract, asserted for `total_return` and `final_capital`; plus `final_capital != initial_capital` for a run that moved |
| 1.9 P0 | `tests/test_backtest_equity_curve_persisted.py` | A real short backtest → stored `equity_curve` non-empty and its length equals the executed bar count |
| 1.10 P0 | `tests/test_backtest_key_contract.py` + `algo22-terminal/tests/unit/pages/backtesterNetPnl.test.jsx` | Completed backtest yields a numeric `total_pnl`; and the rendered field is present (or, if removed, absent from the results surface) |
| 1.21 P1 | `algo22-terminal/tests/unit/lib/socketCredential.test.js` | The URL built by `wsClient.connect` **and** by Billing's mount contains no `token` parameter and no JWT substring |
| 1.22 P1 | `algo22-terminal/tests/unit/pages/billingSocketLifecycle.test.jsx` | Unmount, advance fake timers past the reconnect delay → the `WebSocket` constructor was not called again |
| 1.23 P1 | `algo22-terminal/tests/unit/lib/singleSocket.test.jsx` | Across a mount of Billing plus the data pipeline, the global `WebSocket` constructor is called exactly once |
| 1.24 P1 | `tests/test_strategy_status_publish.py` + `algo22-terminal/tests/unit/liveTrading/deploymentState.test.jsx` | One start and one stop each publish exactly one frame whose `type` is the string the frontend subscribes to; and the frontend updates from that same fixture |
| 1.25–1.27 P1 | `algo22-terminal/tests/unit/builder/enginePaths.test.jsx` + `tests/test_route_contract.py` | No `ReferenceError` and a defined outcome; and every frontend call path appears in the OpenAPI schema with its method |
| 1.28 P1 | `tests/test_route_contract.py` | The same sweep, which subsumes Copilot — and passes trivially once the dormant module is deleted |
| 1.29 P1 | `tests/test_signal_trace_serialisation.py` | `json.dumps(to_frontend_format())` succeeds, and stages 1 and 2 carry populated node detail projected identically to stage 3 |
| 1.30 P0 | CI gate + `tests/test_release_artifacts.py` | No file under a `releases/` path is implausibly small for its extension; and no download link is rendered for a platform with no artifact |
| 1.31 P1 | workflow dry-run | The allow-list check passes for a bundle containing each of the five allowed hosts, and fails for a bundle containing none |
| 1.32 P1 | `git status` + `tests/test_migration_tooling.py`, `tests/test_cors_configuration.py` | Clean status against `main`, both suites passing |
| 1.33 P1 | alert re-query | Zero unexcepted critical or high alerts on runtime dependencies, with the exception list checked in |

The two-sided assertion that guards wave 1's own failure mode lives in the same file as 1.1–1.4:
**a genuine `0.0` still reports `0.0`**, parameterised over every figure the wave touches. Without it
the fix trades one lie for another.

### Preservation Checking

**Goal:** verify that for every input where the bug condition does not hold, the fixed code produces
the same result as the code before it.

**Pseudocode:**

```
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT F(X) = F'(X)
END FOR
```

**Testing approach.** Property-based testing is the right instrument for preservation here, for
three reasons specific to this pass: the input domain is large (every combination of present and
absent figures across two environments), the dangerous cases are at the boundaries that hand-written
cases miss (`0.0` vs `None`, `NaN`, `-0.0`, an empty ledger vs an unread one), and the guarantee
needed is "for all non-buggy inputs", which is what a property states and an example does not. The
repo already runs Hypothesis under `tests/property/` and has `fast-check` available for vitest.

**Method.** Capture behaviour on the **unfixed** tree first, then write the property that pins it.
`tests/regression/capture_baseline.py` and `test_baseline_unchanged.py` already implement exactly
this pattern and are the mechanism to extend rather than duplicate.

**Preservation cases:**

1. **Genuine zeros and genuine values survive wave 1** — `tests/property/test_absent_vs_zero.py`.
   Over generated account rows and QuestDB responses: a read that succeeded reports its value
   unchanged, including `0.0`; only a read that failed reports `None`. Both directions, because a
   one-directional property would pass on a fix that nulls everything. Covers 3.1, 3.2.
2. **Latency classification is unchanged** — `tests/test_exchange_health_probe.py`. A measured
   latency still classifies `optimal` under 150 ms and `normal` under 500 ms, and a valid reachable
   credential still reports connected with `can_trade: true`. Covers 3.3.
3. **The correctly-persisted backtest columns are unchanged** — `tests/test_backtest_key_contract.py`.
   Captured from the engine's output rather than from today's stored values, because `expectancy` is
   currently overwritten with `0.0` and `sortino_ratio` comes from the runtime. Covers 3.4 as
   corrected.
4. **`update_backtest_results` keeps its ownership behaviour** — existing tests, re-run. Still scoped
   by `user_id`, still returns `{}` indistinguishably, still degrades when `006` is unapplied.
   Covers 3.5.
5. **The eight structural frontend guards and the redesign's behaviours hold** — the existing
   `algo22-terminal/tests/unit/guards/` and `design/` suites, re-run scoped. No `err.message` reaches
   the screen; tokens, alignment and review-mode suppression unchanged. Covers 3.7, 3.8.
6. **Rate limits and ownership predicates are unchanged** — existing suites. Paper trading's
   `120/minute` and `60/minute`, library's `120/60second` and `60/60second`. Covers 3.9.
7. **`MANUAL_EXECUTION_BLOCKED` still refuses** — `tests/test_orders_manual_execution_blocked.py`.
   `POST /execute` and `POST /create` both 403 with that code. Asserted explicitly because wave 3
   and the 2.13 work are both in the neighbourhood of the order path, and this is the constraint
   easiest to break by accident.
8. **Entitlement failures stay 503, not 403** — existing marketplace suites. `MARKETPLACE_READ_FAILED`
   is never folded into a refusal. Covers 3.11, 3.12.
9. **The paper lifecycle and its shared replay are unchanged** — `tests/property/test_paper_*`,
   re-run. REST event replay still shares its implementation with the WebSocket history. Covers 3.10.
10. **Deploy pipeline invariants hold** — workflow assertions. Fails closed on unsubstituted
    `VITE_API_URL`, rejects `ws://localhost:8000`, serves `*.html` with `max-age=0, must-revalidate`,
    invalidates and waits; ECR artifact verified before backend deploy. Covers 3.14, 3.15.
11. **The stale duplicate frontend is untouched** — `git diff` asserts no change under
    `aerora_quant_platform/frontend_app/algo22-terminal`. Covers 3.16.
12. **The property suites still pass** — `tests/property/` re-run. Covers 3.18.

### Unit Tests

- Wave 1: each figure's absent path and its zero path, per environment, per field.
- Wave 2: each renamed key at each of the three boundaries; the equity-curve in-band read; the
  `{**stats, **performance_metrics}` precedence.
- Wave 3: URL construction; unmount timer cancellation; refcount behaviour under two holds; ticket
  single-use and expiry.
- Wave 4: each engine context's defined outcome; the named node projection over every `NodeType`.
- Wave 5: `canUndo` true after one edit on a freshly loaded strategy (2.38); the subscription badge
  (2.37); the `Drawer` scrim exposing no element named "Close" when non-interactive (2.45);
  deterministic overlay stacking with both drawers mounted (2.46).
- Release: the size gate against a deliberately undersized file; the cache-policy check asserting
  only content-hashed paths receive `immutable`.

### Property-Based Tests

- `tests/property/test_absent_vs_zero.py` — the two-sided absent/zero property over generated
  account rows, QuestDB responses and equity series. The central property of the pass.
- `tests/property/test_order_idempotence_under_retry.py` — over generated submit/retry/restart
  sequences carrying one idempotency key, order count is invariant at exactly one, and every
  submission after the first is answered with that order (2.11, 2.17). Paper and simulation paths
  only; no live exchange call. Extends `test_paper_idempotence.py` rather than replacing it.
- `tests/property/test_backtest_key_contract.py` — over generated engine payloads, the writer reads
  no key the payload does not carry.
- `algo22-terminal/tests/unit/lib/socketCredential.test.js` — `fast-check` over generated tokens and
  paths: no constructed socket URL contains the token in any position.
- Existing property suites are extended, not duplicated: `test_tenant_isolation_matrix.py`,
  `test_protected_logic_containment.py`, `test_no_synthesised_price.py`,
  `test_settlement_idempotence.py`.

### Integration Tests

- A real short backtest end to end: run → persist → read back → render. The only test that catches
  all four of 1.7–1.10 in one pass (2.9).
- A full strategy start/stop with the socket open, asserting one `STRATEGY_STATUS` frame per
  transition reaches the frontend (2.24).
- Billing plus the data pipeline mounted together, asserting one socket and a working billing update
  (2.23).
- The deploy pipeline dry-run: build with `$env:NODE_OPTIONS="--max-old-space-size=2048"`, run the
  allow-list check against a bundle containing each allowed host, run the `releases/` gate (2.31,
  2.30).
- Production verification after deploy (2.47): `ecs describe-services` on `vyomquant-cluster` /
  `vyomquant-api-service-cjema2sl` reporting `ACTIVE` with `runningCount == desiredCount` on the new
  task definition; the backend health endpoint answering through the ALB; and
  `https://d7d88qs4jmch.cloudfront.net/` serving the new bundle after invalidation. Baseline to beat:
  `ACTIVE 1/1` on `vyomquant-api:146`, CloudFront `200`.

### The 15 UNVERIFIED Clauses: Proof Strategy, and What This Environment Can Actually Prove

For these the deliverable is a proof or a new numbered defect, not a code change. The requirements
say 17; there are 15. Each row states the test and — plainly — whether it can be established by
automated test in this environment or needs a running stack.

| Clause | Property | Proof | Provable here? |
|---|---|---|---|
| **1.11** Order idempotency under duplicate signal | Exactly one order per idempotency key | `tests/property/test_order_idempotence_under_retry.py`, plus a code reading recording the key and its unique constraint. **Partly already proven:** `tests/crash_recovery/test_worker_crash_mid_submission.py` establishes this for the live path across ~20 cases, including the durable unique index and the venue's own refusal. The gap is the property test and the paper path. | **Yes** — fully. Paper/simulation only; no live fill, per the constraint. |
| **1.12** Paper–live separation | No live venue adapter is constructible for a paper-bound strategy | `tests/test_paper_live_separation.py`. A failing double substituted for the live adapter that raises if constructed; assert it never raises across every paper signal path. | **Yes.** Code-level, no venue needed. |
| **1.13** Server-side risk enforcement | Each violation refused with a distinct code, no order row written | `tests/test_risk_limits_server_side.py`. Submit each violation (position size, leverage, daily loss, kill switch) with no UI involvement. **Constraint:** not via `/execute` or `/create` — those answer 403 `MANUAL_EXECUTION_BLOCKED` to everything, so a pass there proves nothing. Target the paper order route and the internal execution service. | **Yes** for the paper route and the service. **Partly** for the deployment path, which needs a worker. |
| **1.14** IDOR / BOLA sweep | Other-tenant id indistinguishable from nonexistent id | **Extend** `tests/sandbox_lifecycle/test_cross_tenant_ownership_matrix.py`. It already covers strategy, version, backtest, backtest_result, deployment, signal, exchange_account with route resolution, header+body normalisation, vacuity checks and coverage self-checks. Add: orders, positions, portfolios, traces, credentials, billing, subscriptions, listings, paper accounts, invoices. The matrix's own coverage test fails if a cell is left unprobed, which is what makes the sweep list auditable. **Any route excluded must be recorded with its reason** — the file already requires this via `test_every_probe_without_an_owner_control_states_why`. | **Yes** for routes the sandbox can serve. **No** for the paper routes without work: the file records that `SandboxDatabase` does not implement `009_paper_trading.sql`'s semantics. That is a named blocker, not a pass. |
| **1.15** Tenant boundary documented per table | Predicate, RLS, FK and index recorded per table; every gap filed as a P0 | A schema audit checked into this spec directory, plus a test asserting each shared table's access paths carry the tenant predicate. | **Yes** — the migrations are in the tree and readable. |
| **1.16** Concurrent strategy lifecycle | No running-but-deleted, no double deploy | `tests/test_strategy_lifecycle_concurrency.py`. Fire start/stop/delete/deploy in parallel against one id, repeated enough to expose interleaving; assert the terminal state is one of the legal states. | **Partly.** Provable against the sandbox. A real Postgres is needed to establish that the *database's* serialisation holds, not just the application's. |
| **1.17** Worker restart mid-signal | Exactly-once processing | Already covered by `tests/crash_recovery/test_worker_crash_mid_submission.py` — including that recovery never moves a state backwards and the marker is written once however many times a worker restarts. **Cite it and record its scope** rather than writing a second suite. | **Yes** — already proven; the clause closes by citation. |
| **1.18** Entitlement withdrawn on lapse | Every read refuses with the correct distinct code | `tests/test_entitlement_matrix.py`. Subscription states × protected operations, asserting each cell's wire code against the four existing codes (`MARKETPLACE_NOT_SUBSCRIBED`, `MARKETPLACE_SUBSCRIPTION_EXPIRED`, `MARKETPLACE_STRATEGY_UNAVAILABLE`, `MARKETPLACE_OPERATION_NOT_PERMITTED`) — and asserting `MARKETPLACE_READ_FAILED` is not folded in (3.11). | **Yes.** `tests/property/test_subscription_state_machine.py` and `test_entitlement_expiry_boundary.py` already model the states. |
| **1.19** No creator logic leakage | No response, error, trace or export carries the DAG | **Extend** `tests/property/test_protected_logic_containment.py` to the trace and export surfaces. Assert the serialised response for a non-entitled caller contains none of the node types, parameters or expressions of the underlying DAG. | **Yes.** |
| **1.20** No secret in bundle or logs | No key, secret or service-role credential in `dist/`, logs or a response body | A grep assertion over `dist/` for key patterns and known secret **names**, plus a log-capture test asserting exchange credentials are redacted. The workflow already checks `service_role`; this generalises it. Secrets are referenced by key name, never value, in anything this pass produces. | **Yes** for the bundle. **Partly** for logs — a full audit of production log content needs log access this IAM principal may not have; a denial is recorded, not passed. |
| **1.39** 422 not 500 on malformed input | Every mutating route answers 422 with a machine-readable code, no traceback | `tests/test_validation_sweep.py`. Malformed bodies to every mutating route; assert no 500 and no `err.message`-style leakage. The frontend half is already guaranteed by `design/errorCopy.js`; this extends it server-side. | **Yes** — `TestClient` over the real app, same technique the cross-tenant matrix already uses. |
| **1.42** Performance measured | Query counts, polling intervals, render counts, heap growth recorded | Recorded measurements; `tests/perf/` already holds `test_market_data_latency.py` and `test_strategy_builder_budgets.py` to extend. The requirement is a measurement, not a number. | **Partly.** Query counts and render counts: yes. Heap growth over a long session: needs a browser, so it is a manual measurement, recorded as such. |
| **1.43** Loading then recoverable failure everywhere | No empty frame, no permanent skeleton | `algo22-terminal/tests/unit/pages/degradedSurfaces.test.jsx`. Delayed and rejected fetches across the primary surfaces. `usePanelState` makes this cheap: its eight states are already the vocabulary, and `STATES_WITHOUT_CHILDREN` already encodes which must not render children. | **Yes.** |
| **1.44** Responsive layout holds | Tablet and mobile layout unchanged after this pass | Re-run the existing responsive test files, **scoped**, never the full suite. | **Yes.** |
| **1.47** Production health verified end to end | ECS `ACTIVE` with `runningCount == desiredCount` on the new revision; ALB health answering; CloudFront serving the new bundle | Recorded command output, per 2.47. | **Yes** — AWS access is confirmed available in this session. Any denied call is recorded as **BLOCKED** with the denial and the clause is not reported as passing. |

**Summary of what needs a running stack and cannot be closed by automated test here:** 1.14's paper
routes (the sandbox does not implement `009`'s semantics), 1.16's database-level serialisation,
1.13's deployment path, 1.20's production log audit, and 1.42's heap measurement. Five partial
blockers out of fifteen. Each is named at its row with what is missing, per the requirements'
instruction that a clause this environment cannot prove says **BLOCKED** and names the gap rather
than implying a pass.
