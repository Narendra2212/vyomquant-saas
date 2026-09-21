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
 * **The two that remained were not oversights, and they are now gone too.** Both were
 * `CopilotContext.jsx`, and both needed a product decision before a fix could be right:
 * `generateDAG` had no endpoint at all, so whether Copilot DAG generation was meant to
 * exist was the question rather than which path to type; and `loadSession`'s read wanted
 * `GET /sessions/{id}/messages`, which returns messages rather than a session, so
 * repointing it would have changed what the caller received. Guessing either would have
 * been a behaviour change disguised as a path fix.
 *
 * `production-launch-hardening` task **9.3** took the decision the other way and deleted
 * the module, which Requirement 2.28 permits explicitly: `CopilotProvider` was mounted
 * nowhere, the file documented itself as "Intentionally DORMANT", and it bypassed the
 * `api` client entirely with raw `fetch` + its own `getAuthHeaders()` — so deleting it
 * removed a second authentication path and a second error surface that did not go through
 * `design/errorCopy.js`, as well as the two dead addresses. `src/components/CopilotChat.jsx`
 * went with it: it was the only consumer of `useCopilot`, was mounted nowhere, and could
 * not have built without the context. `src/hooks/useCopilotSSE.js` went too — it held no
 * code, only a comment saying the logic lived in `CopilotContext`.
 *
 * **The list is now empty, and an empty list is the strongest state this file can be in.**
 * It is kept rather than deleted because `api-paths.test.js` asserts the measured set equals
 * these keys exactly, so an empty map is the assertion that every client address the routers
 * do not serve has been dealt with. The next one that appears fails the guard with nowhere
 * to hide.
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
 * 0 keys, from 181 transport call sites and 209 path literals checked against the 338
 * routes `main.py`'s mounts resolve to. Measured, not estimated.
 *
 * The reading before task 9.3 was 2 keys over 1 file, from 185 transport call sites and
 * 215 path literals against 338 routes. Deleting `CopilotContext.jsx` took its four raw
 * `fetch` call sites and six literals out of the client side. **338 is the same number
 * both days**, which is the check that the reduction is on the client side only — a scanner
 * that had actually broken would have taken the route count down with it. Both client
 * floors below are unchanged and both still clear.
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
  // Empty, as of `production-launch-hardening` task 9.3. Every client address the routers
  // do not serve as written has been fixed, repointed or deleted.
  //
  // The last two entries stood here until that task, and both were `CopilotContext.jsx`:
  //
  //   wrong-path   | src/contexts/CopilotContext.jsx | /api/v1/copilot/dag/generate
  //       `generateDAG`. `routers/copilot.py` declares `/chat/stream`, `/sessions`,
  //       `/sessions/{id}/messages` and `DELETE /sessions/{id}` — no `/dag/*` route at
  //       all, so "generate a strategy from a description" could not work as written.
  //
  //   wrong-method | src/contexts/CopilotContext.jsx | GET /api/v1/copilot/sessions/{}
  //       `loadSession`, against a path declared for DELETE only. Its sibling
  //       `deleteSession` sends DELETE to the same address and was correct, which is why
  //       this survived: one of the two callers worked.
  //
  // Removed rather than lowered or relaxed: the module was deleted, so there is no call
  // site left to record. See the header for why deletion was the right disposition and
  // what else went with it.
});

/**
 * Floors on how much the guard must still be reading.
 *
 * Floors, not equalities: adding a route or a call site must not fail CI, but the
 * guard quietly starting to check *nothing* and reporting green must. A guard that
 * examines zero paths passes for free, which is how a guard becomes decoration —
 * `source-scan.js`'s header calls that the green-looking non-run.
 *
 * Currently measured: 338 declared routes, 181 transport call sites, 209 path
 * literals. Set about 10% below when they were 338/185/215, so ordinary churn does not
 * touch them and a collapse does. Task 9.3's deletion of `CopilotContext.jsx` moved the
 * client readings by four and six and none of the floors, which is what a floor with
 * headroom is for.
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
