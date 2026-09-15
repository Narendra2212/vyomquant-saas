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
 * The commit that seeded this list fixed none of them, deliberately: the task was
 * scoped to the guard, and changing a client method there would have mixed a
 * detection change with twenty-five behaviour changes in one diff.
 *
 * ---------------------------------------------------------------------------
 * 25 SEEDED, 23 CLEARED, 2 LEFT
 * ---------------------------------------------------------------------------
 * The follow-up commit cleared the twenty-three whose fix was unambiguous — every
 * one where the router either declares the right address or declares nothing the
 * call could have meant:
 *
 *   * **Sixteen went with `src/api/typed-client.ts`**, deleted outright. It was a
 *     complete second API client written against an older route table, exported as
 *     `api`, and nothing in `src/` imported it (verified across `.js`, `.jsx`, `.ts`
 *     and `.tsx` — the twenty-two `import { api } from '../api'` sites all resolve
 *     to `src/api/index.js`, which neither imports nor re-exports it).
 *     `src/types/api.types.ts` went with it: it was imported by that file and
 *     nothing else, so it died with its only consumer. Sixteen addresses that would
 *     have become sixteen shipped defects on one `import` are now zero lines of code.
 *   * **Four were dead methods with no caller**, deleted rather than repointed
 *     because no correct address exists for them: `marketApi.haltStrategies` (there
 *     is no fleet-wide halt endpoint — `POST /api/risk/kill-switch` is the real one),
 *     `riskApi.getAccountHealth`, and the six `orders.js` methods aimed at the three
 *     dead order addresses (`/api/orders`, `/api/orders/{}`, `/api/orders/{}/trades`,
 *     `/api/orders/closed`). `createOrder` in particular must never be repointed:
 *     `POST /execute` and `POST /create` answer 403 MANUAL_EXECUTION_BLOCKED **by
 *     design**, because this backend routes all execution through a strategy
 *     deployment.
 *   * **Two were live reads at the wrong address**, repointed:
 *     `riskApi.getMarginHealth` to `GET /api/risk/margin-health` (its caller,
 *     `pages/RiskSettings.jsx`, renders exactly the body that route returns — the
 *     read was real and simply was not being reached) and `pages/Backtester.jsx`'s
 *     raw `fetch` to `POST /api/backtests/validate-data`.
 *
 * **The two that remain are not oversights.** Both `CopilotContext.jsx` entries need
 * a product decision before a fix can be right: `generateDAG` has no endpoint at all,
 * so whether Copilot DAG generation is meant to exist is the question, not which path
 * to type; and `loadSession`'s read wants `GET /sessions/{id}/messages`, which returns
 * messages rather than a session, so repointing it changes what the caller receives.
 * Guessing either would be a behaviour change disguised as a path fix.
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
 * WHAT THE SCAN NOW READS
 * ---------------------------------------------------------------------------
 * 2 keys over 1 file, from 185 transport call sites and 215 path literals checked
 * against the 338 routes `main.py`'s mounts resolve to. Measured, not estimated.
 *
 * The seeding day read 263 call sites and 308 literals. The drop is almost entirely
 * `typed-client.ts`: that one file held 78 of the call sites and 93 of the literals,
 * which is the scale of the duplicate it was. The rest is the ten deleted methods.
 * No router changed and no route was lost — 338 is the same number both days, which
 * is the check that the reduction is on the client side only.
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
});

/**
 * Floors on how much the guard must still be reading.
 *
 * Floors, not equalities: adding a route or a call site must not fail CI, but the
 * guard quietly starting to check *nothing* and reporting green must. A guard that
 * examines zero paths passes for free, which is how a guard becomes decoration —
 * `source-scan.js`'s header calls that the green-looking non-run.
 *
 * Currently measured: 338 declared routes, 185 transport call sites, 215 path
 * literals. Set about 10% below, so ordinary churn does not touch them and a
 * collapse does.
 *
 * THE TWO CLIENT FLOORS WERE LOWERED WHEN `typed-client.ts` WAS DELETED, and that is
 * the one move a floor like this has to be defended for. They were 235 and 275,
 * measured against 263 call sites and 308 literals. Deleting a 473-line duplicate
 * client took 78 call sites and 93 literals out of the tree, so the old floors would
 * have failed a commit that removed dead code rather than a commit that broke the
 * scanner — which is the opposite of what they are for.
 *
 * What makes it safe to lower them rather than a hole: `ROUTES_READ_AT_LEAST` did
 * **not** move, and 338 was the reading before and after. A scanner that had actually
 * broken would have taken the route count down with it. And the reduction was
 * accounted for file by file, not absorbed as churn: 78 + 93 from one deleted file
 * plus ten deleted methods explains the whole of it.
 */
export const ROUTES_READ_AT_LEAST = 300;
export const CALLS_CHECKED_AT_LEAST = 165;
export const LITERALS_CHECKED_AT_LEAST = 190;
