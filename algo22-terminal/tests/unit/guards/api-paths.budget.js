/**
 * The `api-paths` known-defects list — the ratchet for
 * `tests/unit/guards/api-paths.test.js`.
 *
 * ---------------------------------------------------------------------------
 * WHAT THIS FILE IS
 * ---------------------------------------------------------------------------
 * Every client API path the routers do not serve as written, as it stood on the day
 * the guard was widened from one prefix to all of them. `api-paths.test.js`
 * measures the real set and asserts it matches these keys **exactly**:
 *
 *   * an unrecorded defect -> a call was added that the backend does not serve.
 *                             Fix the call, or add a line here with a reason and a
 *                             task that owns it.
 *   * a recorded defect that is gone -> it was fixed. Delete the line, in the same
 *                             commit.
 *
 * The second arm is the point, and it is the same argument
 * `no-colour-literals.budget.js` makes for requiring its numbers to move: a `<=`
 * assertion lets one contributor fix twelve of these and leave the list at its old
 * length, so the next contributor can put twelve back without CI noticing.
 *
 * **This commit fixes none of them.** The task that produced this list was scoped
 * to the guard; changing a client method or a router here would mix a detection
 * change with twenty-five behaviour changes in one diff. The list is the
 * deliverable.
 *
 * ---------------------------------------------------------------------------
 * KEY FORMAT
 * ---------------------------------------------------------------------------
 *     <kind>|<file, relative to algo22-terminal>|<what was requested>
 *
 * Four kinds:
 *
 *   * `wrong-path`   — no router declares the path under any verb. A 404 on every
 *                      call. The path, not a verb, so the key carries no method:
 *                      two methods aimed at one dead path are one defect.
 *   * `wrong-method` — the path is declared, for a different verb. 405 or 404.
 *   * `missing-query`— the route matched and a `Query(...)` parameter with no
 *                      default is nowhere at the call site. 422 on every call.
 *   * `opaque-query` — the same, except the query string is a caller-supplied bag,
 *                      so the parameter *may* arrive. None today.
 *
 * No line numbers, so moving code does not churn the list. Two occurrences of one
 * defect in one file therefore share a key; where that happens the note names both
 * call sites.
 *
 * ---------------------------------------------------------------------------
 * SEEDED FROM THE TREE ON THE DAY THE GUARD WAS WIDENED
 * ---------------------------------------------------------------------------
 * 25 keys over 6 files, from 263 transport call sites and 308 path literals checked
 * against the 338 routes `main.py`'s mounts resolve to. Measured, not estimated.
 * Nine keys are in live code, covering twelve call sites — three of those dead
 * addresses are each reached by two client methods. The other sixteen are in
 * `src/api/typed-client.ts`, which nothing imports.
 *
 * ---------------------------------------------------------------------------
 * `src/api/typed-client.ts` — sixteen of these, and the honest recommendation
 * ---------------------------------------------------------------------------
 * That file is a complete second API client, `export`ed as `api`, and **no module
 * in `src/` imports it**. It was written against an older route table and never
 * re-checked: it addresses `/api/auth/signin` and `/api/auth/signup` (the router
 * declares `/login` and `/register`), `/api/auth/refresh` (never existed),
 * `/api/market/candles?symbol=…` in query form (the route takes path parameters),
 * `/api/user/stats` and `/api/user/security-logs` (the real reads are `/api/stats`
 * and `/api/security/logs`), and it still carries defects 4 and 5 — the ones
 * `api/modules/orders.js` was corrected off at task 20.3 — untouched.
 *
 * It is recorded rather than excluded because "nothing imports it *today*" is not a
 * property a guard can rely on: one `import { api } from './typed-client'` turns
 * sixteen recorded defects into sixteen shipped ones. The recommendation is to
 * delete the file, and this list is the argument for it: bringing it up to date
 * means re-checking sixteen addresses against a router table
 * `src/api/modules/**` already tracks correctly.
 */

/**
 * Key → what is wrong and what the router actually declares.
 *
 * Grouped by severity: dead addresses first (nothing reaches the server), then
 * missing required parameters (the address is right and every call is refused).
 */
export const KNOWN_API_DEFECTS = Object.freeze({
  /* ── Dead addresses in LIVE code ─────────────────────────────────────────
   * These are reachable from the running app. Each is a total failure of the
   * feature that calls it, not a degradation. */

  // `marketApi.haltStrategies` — the emergency "halt all strategies" call. It POSTs
  // `/api/strategies/stop`; `routers/strategies.py` declares `POST /{strategy_id}/stop`
  // and no `POST /stop`, and there is no `POST /{strategy_id}` for the literal `stop` to
  // fall into either. There is no fleet-wide halt endpoint at all: the kill switch is
  // `POST /api/risk/kill-switch`, which `riskApi.killSwitch` already reaches. Worst of the
  // nine, because it is a safety control that reports success while doing nothing.
  'wrong-path|src/api/modules/market.js|/api/strategies/stop':
    'POST; routers/strategies.py declares POST /{strategy_id}/stop only. No fleet-wide ' +
    'halt route exists — POST /api/risk/kill-switch is the one that stops trading.',

  // Two call sites, one dead address: `ordersApi.createOrder` (POST) and
  // `ordersApi.getOrders` (GET, with a `?${params}` query). `routers/orders.py` declares
  // nothing at its mount root. The nearest POSTs are `/execute` and `/create`, and both
  // answer 403 MANUAL_EXECUTION_BLOCKED by design — this backend routes all execution
  // through a strategy deployment — so `createOrder` has no correct address to move to.
  // The open/history/cancel reads are the supported surface.
  'wrong-path|src/api/modules/orders.js|/api/orders':
    'createOrder (POST) and getOrders (GET); routers/orders.py declares no route at the ' +
    'mount root. POST /execute and POST /create both answer 403 by design.',

  // `ordersApi.getOrder` (GET) and `ordersApi.updateOrder` (PUT). `routers/orders.py`
  // declares no `/{order_id}` route under any verb — the same absence that made defect 5
  // (`DELETE /api/orders/{orderId}`) a 404. A single order is read out of
  // `GET /open` or `GET /history`; amendment is not offered at all.
  'wrong-path|src/api/modules/orders.js|/api/orders/{}':
    'getOrder (GET) and updateOrder (PUT); routers/orders.py declares no /{order_id} ' +
    'route under any verb. This is the absence behind defect 5.',

  // `ordersApi.getOrderTrades`. No such route; per-order fills are not exposed.
  'wrong-path|src/api/modules/orders.js|/api/orders/{}/trades':
    'getOrderTrades (GET); routers/orders.py declares no per-order trades route.',

  // `ordersApi.getClosedOrders`. `routers/orders.py` declares `/open` and `/history` and
  // no `/closed`; `GET /history` is the closed-order read.
  'wrong-path|src/api/modules/orders.js|/api/orders/closed':
    'getClosedOrders (GET); routers/orders.py declares /open and /history only.',

  // `riskApi.getMarginHealth` AND `riskApi.getAccountHealth` — two methods, one dead
  // address. `routers/risk.py` declares `GET /margin-health` and `GET /status`; there is
  // no `/account-health`. So the margin-health read is addressable and is not being
  // addressed, and the account-health read has no endpoint at all.
  'wrong-path|src/api/modules/risk.js|/api/risk/account-health':
    'getMarginHealth and getAccountHealth (GET); routers/risk.py declares ' +
    'GET /margin-health and GET /status, not /account-health.',

  // `pages/Backtester.jsx`'s raw `fetch`. Defect 1's exact shape, still in the tree:
  // `strategy_operations.router` is mounted at `/api` and this route carries no
  // `strategy-operations` alias, so the declared path is `/api/backtests/validate-data`.
  // The old guard's docblock named this call site and left it out of scope; widening the
  // globs is what surfaced it.
  'wrong-path|src/pages/Backtester.jsx|/api/strategy-operations/backtests/validate-data':
    'POST; strategy_operations.py declares POST /backtests/validate-data at the /api ' +
    'mount and no strategy-operations alias — i.e. /api/backtests/validate-data.',

  // `CopilotContext.generateDAG`. `routers/copilot.py` declares `/chat/stream`,
  // `/sessions`, `/sessions/{id}/messages` and `DELETE /sessions/{id}`. There is no DAG
  // generation endpoint, so the "generate a strategy from a description" action cannot
  // work as written.
  'wrong-path|src/contexts/CopilotContext.jsx|/api/v1/copilot/dag/generate':
    'POST; routers/copilot.py declares no /dag/* route.',

  // `CopilotContext.loadSession` GETs `/api/v1/copilot/sessions/{id}`, which is declared
  // for DELETE only. The read it wants is `GET /sessions/{session_id}/messages`. Its
  // sibling `deleteSession` sends DELETE to the same address and is correct, which is why
  // this survived: one of the two callers works.
  'wrong-method|src/contexts/CopilotContext.jsx|GET /api/v1/copilot/sessions/{}':
    'loadSession; routers/copilot.py declares DELETE for that path. The read is ' +
    'GET /sessions/{session_id}/messages.',

  /* ── Dead addresses in `src/api/typed-client.ts` ─────────────────────────
   * Nothing imports this module. See the header for why they are recorded anyway
   * and why deleting the file is the recommendation. */

  'wrong-path|src/api/typed-client.ts|/api/auth/signin':
    'auth.signIn (POST); routers/auth.py declares POST /login.',
  'wrong-path|src/api/typed-client.ts|/api/auth/signup':
    'auth.signUp (POST); routers/auth.py declares POST /register.',
  'wrong-path|src/api/typed-client.ts|/api/auth/refresh':
    'auth.refreshToken (POST); routers/auth.py declares no refresh route — the token is ' +
    'refreshed by Supabase in apiClient.js, not by this backend.',

  'wrong-path|src/api/typed-client.ts|/api/market/candles':
    'market.getCandles (GET, ?symbol=&timeframe=&limit=); routers/market.py declares ' +
    'GET /candles/{symbol}/{timeframe}. Query form, path route.',
  'wrong-path|src/api/typed-client.ts|/api/market/ticker':
    'market.getTicker (GET, ?symbol=); routers/market.py declares GET /ticker/{symbol}.',
  'wrong-path|src/api/typed-client.ts|/api/market/orderbook':
    'market.getOrderBook (GET, ?symbol=&depth=); routers/market.py declares ' +
    'GET /orderbook/{symbol}.',
  'wrong-path|src/api/typed-client.ts|/api/market/funding-rate':
    'market.getFundingRate (GET, ?symbol=); routers/market.py declares GET /funding/{symbol}.',

  // Defect 5, still live here. `api/modules/orders.js` was corrected at task 20.3; this
  // copy was not, because nothing calls it and nothing checked it.
  'wrong-path|src/api/typed-client.ts|/api/orders/{}':
    'orders.cancel (DELETE); routers/orders.py declares no DELETE at all — the cancel is ' +
    'POST /cancel/{order_id}. This is defect 5, uncorrected in this module.',

  'wrong-path|src/api/typed-client.ts|/api/portfolio/positions':
    'portfolio.getPositions (GET); routers/portfolio.py declares no /positions. Positions ' +
    'arrive in the dashboard aggregation (GET /api/dashboard).',
  'wrong-path|src/api/typed-client.ts|/api/risk/account-health':
    'risk.getAccountHealth (GET); routers/risk.py declares GET /margin-health and ' +
    'GET /status. Same dead address as api/modules/risk.js.',
  'wrong-path|src/api/typed-client.ts|/api/strategies/{}/start':
    'strategies.start (POST); routers/strategies.py declares /deploy, /stop, /pause and ' +
    '/resume, no /start.',
  'wrong-path|src/api/typed-client.ts|/api/user/stats':
    'user.getStats (GET); the read is GET /api/stats, declared on main.py itself.',
  'wrong-path|src/api/typed-client.ts|/api/user/referrals':
    'user.getReferralStats (GET); the read is GET /api/referral/stats.',
  'wrong-path|src/api/typed-client.ts|/api/user/security-logs':
    'user.getSecurityLogs (GET); routers/user.py declares GET /security/logs at the /api ' +
    'mount — i.e. /api/security/logs.',

  /* ── Missing required query parameters — 422 on every call ───────────────
   * Defects 4 and 6's class: the address is right and the request is refused
   * before the handler runs. Both are in `typed-client.ts`; `api/modules/orders.js`
   * was corrected at tasks 20.1c and 20.3 and passes both checks. */

  'missing-query|src/api/typed-client.ts|GET /api/orders/open':
    'orders.getOpen sends no query at all; get_open_orders declares ' +
    'exchange_id: str = Query(...) with no default. This is defect 4, uncorrected here.',
  'missing-query|src/api/typed-client.ts|POST /api/orders/cancel-all':
    'orders.cancelAll sends no query and no body; cancel_all declares ' +
    'exchange_id: str = Query(...) with no default. Defect 6 reached the right path in ' +
    'this module and still cannot succeed.',
});

/**
 * Floors on how much the guard must still be reading.
 *
 * Floors, not equalities: adding a route or a call site must not fail CI, but the
 * guard quietly starting to check *nothing* and reporting green must. A guard that
 * examines zero paths passes for free, which is how a guard becomes decoration —
 * `source-scan.js`'s header calls that the green-looking non-run.
 *
 * Measured on the seeding day: 338 declared routes, 263 transport call sites, 308
 * path literals. Set about 10% below, so ordinary churn does not touch them and a
 * collapse does.
 */
export const ROUTES_READ_AT_LEAST = 300;
export const CALLS_CHECKED_AT_LEAST = 235;
export const LITERALS_CHECKED_AT_LEAST = 275;
