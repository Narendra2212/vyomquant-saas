/**
 * `api-paths` — every client API path, against every router.
 *
 * ===========================================================================
 * WHY THIS EXISTS
 * ===========================================================================
 * The same bug has now been found **six** times, in six unrelated features, one
 * at a time, each by accident when a page happened to adopt the field:
 *
 *  1. `strategiesApi.listBacktests` read `/api/strategy-operations/backtests`. The
 *     router declares `/backtests`. It 404'd on every load; the handler only
 *     checked `res.ok`, so "Saved Backtest History" was silently, permanently
 *     empty.
 *  2. `strategiesApi.deployVersion` posted to
 *     `/api/strategy-operations/strategies/{id}/versions/{version}/deploy`.
 *     `deploy_version` declares only the unprefixed spelling. Worse than
 *     symmetric: the *preflight* GET beside it registers **both**, so the gate
 *     passed, the Deploy button enabled, and only the POST 404'd.
 *  3. `strategiesApi.listDeployments` read
 *     `/api/strategy-operations/strategies/{id}/deployments`, which is not
 *     declared, while `components/DeploymentConsole.jsx` read the unprefixed
 *     spelling and worked. Two callers, one endpoint, two addresses.
 *  4. `ordersApi.getOpenOrders` read `GET /api/orders/open` with no
 *     `exchange_id`. That parameter is `Query(...)` with no default, so every
 *     call was a 422 — a correct path that cannot succeed.
 *  5. `ordersApi.cancelOrder` sent `DELETE /api/orders/{orderId}`.
 *     `routers/orders.py` declares no `DELETE` **at all**; the cancel is
 *     `POST /cancel/{order_id}`.
 *  6. `ordersApi.cancelAllOrders` sent `DELETE /api/orders?{options}`. The route
 *     is `POST /cancel-all`, and the options it serialised were never the
 *     parameters the route reads.
 *
 * The previous version of this guard covered 1-3 and would have caught none of
 * 4-6: it compared one prefix, `/api/strategy-operations/…`, and it compared
 * paths only. Its own docblock named the two things missing — the full mount table
 * and FastAPI path parameters — and a third it did not: nothing compared the query
 * string against the parameters the route refuses to run without.
 *
 * ===========================================================================
 * WHAT IT CHECKS NOW
 * ===========================================================================
 * Two checks, over every `/api/…` path the browser bundle constructs:
 *
 *  * **Path and method.** Each path must be declared by some router, at the mount
 *    prefix `backend_app/main.py` actually mounts that router under, and the verb
 *    the client issues must be one of the verbs declared for it. Path parameters
 *    are normalised to `{}` on both sides, so a declared
 *    `/strategies/{strategy_id}/deployments` matches a client
 *    `/api/strategies/${id}/deployments`; parameter *names* need not agree, only
 *    positions.
 *  * **Required query parameters.** For each matched route, every `Query(...)`
 *    parameter the handler declares with no default must be visible at the call
 *    site — as a literal `name=` pair, as a key of the axios `params` object, or
 *    as a `params.set('name', …)` in the function that builds the request. This is
 *    defects 4-6's class, and it is invisible to a path-only check: the path is
 *    right and the call still cannot succeed.
 *
 * The mount table is read, not assumed. There is no single prefix: `/api/auth`,
 * `/api/exchanges`, `/api/orders`, bare `/api` (four routers share it, which is
 * what made defects 1-3 possible), `/api/v1/copilot`, `/api/internal/persistence`,
 * `/health`, plus `routers/dag_tasks.py`, which carries `/api/dag/tasks` on the
 * `APIRouter(...)` constructor and is mounted with no prefix at all. Routers whose
 * module lives outside `backend_app/routers/` are followed through their
 * `import … as …` aliases, and `main.py`'s own `@app.get(...)` routes are included
 * — `/api/stats` is one of them, and `api/modules/user.js` calls it.
 *
 * ===========================================================================
 * SCOPE — stated plainly
 * ===========================================================================
 * **Covered.** Every non-test `.js`/`.jsx`/`.ts`/`.tsx` file under `src/api/**`,
 * `src/lib/**`, `src/pages/**`, `src/components/**`, `src/contexts/**`,
 * `src/hooks/**`, `src/design/**`, `src/shell/**` and `src/utils/**`.
 *
 * The brief asked for `src/api/**` at minimum and asked for a decision on
 * `src/pages/**` and `src/lib/**`. The decision is to widen to all of them, and to
 * `src/components/**` as well, because that is where the raw `fetch` calls are and
 * the raw `fetch` calls are the ones no module contract covers:
 * `pages/Backtester.jsx`'s `fetch(`${API_BASE}/api/strategy-operations/backtests/validate-data`)`
 * (the case the old docblock named, and a defect), `pages/Strategies.jsx`'s clone
 * POST, `pages/StrategyDetail.jsx`'s strategy, performance and risk-metric reads,
 * `pages/SignalTrace.jsx`'s CSV export, and `components/DeploymentConsole.jsx`'s six
 * deployment calls — which is the *other* caller in defect 3. Leaving those out
 * would have left half of that defect uncovered by the guard written because of it.
 *
 * `src/api/typed-client.ts` is covered too, and it is worth saying why given how
 * much of the findings list is its: nothing imports it. It is a complete second
 * client, written against an older route table, and pinning its defects here is
 * the argument for deleting it — see the notes in `api-paths.budget.js`.
 *
 * **Not covered: comments.** A path in a docblock cannot 404. Prose is stripped
 * (`source-scan.stripComments`), which is the doctrine every guard here follows:
 * documenting a construct is not using it. Several docblocks do still name dead
 * spellings; that is a documentation defect, not a shipping one.
 *
 * **Not covered: required parameters that are not `Query(...)`.** A bare annotated
 * parameter with no default is also required by FastAPI, but telling
 * `symbol: str` (a query parameter) apart from `body: CancelOrderRequest` (a JSON
 * body), `request: Request` and `user: dict = Depends(...)` needs type resolution a
 * text scan does not have. `Query(...)` is unambiguous, is the form this codebase
 * uses for every required query parameter it has, and is the form all of defects
 * 4-6 turned on. Request bodies are not compared at all.
 *
 * **Deliberately strict: a literal client segment does not match a declared path
 * parameter.** FastAPI would route `POST /api/strategies/stop` into
 * `POST /strategies/{strategy_id}` if such a route existed, so this guard is
 * stricter than the server. That is the useful direction: excusing a literal
 * because *some* parameterised route could swallow it would excuse
 * `GET /api/orders/closed` on the grounds that `/{order_id}` might exist. It costs
 * nothing today — the one literal-in-a-parameter-position finding,
 * `marketApi.haltStrategies`, has no `POST /{strategy_id}` to fall into either and
 * is dead under any reading.
 *
 * **Not covered: whether a route works.** This is an address check. A path that
 * resolves can still 500.
 *
 * ===========================================================================
 * FAIL LOUDLY, NEVER SKIP
 * ===========================================================================
 * A guard that quietly passes on what it cannot parse is the guard that let six of
 * these through. So `api-surface.js` reports rather than drops:
 *
 *  * a recognised transport call whose URL mentions `/api` but does not reduce to
 *    a path is a **named failure** (`reports every call it cannot resolve`);
 *  * every `/api` occurrence in executable code must sit inside a string or
 *    template literal the scanner collected, so a path hidden in a construct it
 *    does not understand fails (`accounts for every /api occurrence in code`);
 *  * a route decorator whose path argument cannot be reduced to a literal fails,
 *    because a hole in the reference set reads as a client defect;
 *  * the counts of routes read, calls checked and literals checked are asserted
 *    against floors, so the guard cannot quietly start checking nothing.
 *
 * Where the router genuinely declares two spellings — `_PREFLIGHT_PATHS`'s pair
 * and `execute_backtest`'s two decorators — both are in the table and both pass. A
 * stack of decorators on one handler shares that handler's query parameters.
 *
 * ===========================================================================
 * THE FINDINGS ARE PINNED, NOT FIXED
 * ===========================================================================
 * This commit changes no client method and no router. The defects it found are
 * recorded in `api-paths.budget.js` as an exact set: a **new** defect fails CI, and
 * a **fixed** defect fails CI too until its line is deleted, which is the same
 * ratchet shape `no-colour-literals.budget.js` and `legacy-c.budget.js` use and the
 * same reason — a `<=` assertion lets the next contributor put back what this one
 * removed.
 */
import { describe, expect, it } from 'vitest';

import {
  CALLS_CHECKED_AT_LEAST,
  KNOWN_API_DEFECTS,
  LITERALS_CHECKED_AT_LEAST,
  ROUTES_READ_AT_LEAST,
} from './api-paths.budget.js';
import { clientPaths, declaredRoutes } from './api-surface.js';
import { list } from './source-scan.js';

const backend = declaredRoutes();
const client = clientPaths();

/** Declared path → the set of verbs declared for it. */
const methodsByPath = new Map();
/** `VERB path` → the route, for its query parameters. */
const routeByKey = new Map();
for (const route of backend.routes) {
  if (!methodsByPath.has(route.full)) methodsByPath.set(route.full, new Set());
  methodsByPath.get(route.full).add(route.method);
  const key = `${route.method} ${route.full}`;
  if (!routeByKey.has(key)) routeByKey.set(key, route);
}

/** The file part of a `file:line` location, so a finding id survives a line moving. */
const fileOf = (where) => where.slice(0, where.lastIndexOf(':'));

/**
 * A base-path constant rather than an address.
 *
 * `registryClient.REGISTRY_BASE_PATH = '/api/strategy-operations/registry'` is the
 * one today. It addresses nothing on its own and the routes built from it are
 * checked in full, so failing it would be wrong.
 *
 * **Segment-prefix-ness alone is not the test, and the negative control below is
 * why.** `/api/strategy-operations/strategies/{}/versions/{}/deploy` — defect 2,
 * the deploy POST that 404'd — is a perfectly good segment-prefix of the declared
 * preflight alias `…/deploy/preflight`. An escape hatch keyed on prefix-ness would
 * have excused the exact bug this guard exists to catch. So the requirement is
 * *demonstrated use as a building block*: the literal must be a binding, and some
 * other literal must interpolate that binding and land on a declared route.
 *
 * @param {{bindingName: string|null, path: string}} literal
 * @param {Array<{raw: string, path: string}>} all
 */
export const isBasePathFragment = (literal, all) =>
  literal.bindingName !== null &&
  [...methodsByPath.keys()].some((full) => full.startsWith(`${literal.path}/`)) &&
  all.some(
    (other) =>
      other !== literal &&
      other.raw.includes(`\${${literal.bindingName}}`) &&
      methodsByPath.has(other.path),
  );

/**
 * Every defect, as `id → detail`.
 *
 * Four kinds, and the split is deliberate so that one defect produces one entry:
 *
 *  * `wrong-path` — no router declares this path under any verb. Raised from the
 *    literal scan, which sees paths that are not call arguments too
 *    (`lib/deployFlow.js` states a route as data).
 *  * `wrong-method` — the path is declared, but not for the verb the client
 *    issues. Defect 5's class.
 *  * `missing-query` — the route matched and a required `Query(...)` parameter is
 *    nowhere at the call site. Defects 4 and 6's class.
 *  * `opaque-query` — same, except the query string is a caller-supplied bag
 *    (`new URLSearchParams(filters)`) so the parameter *might* arrive. Recorded
 *    separately because it is a weaker claim, not because it is acceptable.
 *
 * Ids carry the file but not the line, so moving code does not churn the list. Two
 * occurrences of one defect in one file therefore share an entry; that is stated
 * rather than hidden, and the ratchet still holds for distinct defects.
 */
function collectFindings() {
  const found = new Map();
  const add = (id, detail) => {
    if (!found.has(id)) found.set(id, detail);
  };

  for (const literal of client.literals) {
    if (methodsByPath.has(literal.path)) continue;
    if (isBasePathFragment(literal, client.literals)) continue;
    const trimmed = literal.path.replace(/\/$/, '');
    const detail = methodsByPath.has(trimmed)
      ? `trailing slash; ${trimmed} is declared`
      : 'no router declares this path under any verb';
    add(`wrong-path|${fileOf(literal.where)}|${literal.path}`, `${literal.where} — ${detail}`);
  }

  for (const call of client.calls) {
    for (const target of call.paths) {
      const methods = methodsByPath.get(target);
      // The path itself is reported by the literal scan above; reporting it here
      // as well would give one defect two entries.
      if (!methods) continue;

      if (!methods.has(call.method)) {
        add(
          `wrong-method|${fileOf(call.where)}|${call.method} ${target}`,
          `${call.where} — router declares ${[...methods].sort().join(', ')} for ${target}`,
        );
        continue;
      }

      const route = routeByKey.get(`${call.method} ${target}`);
      const missing = route.requiredQuery.filter((name) => !call.queryNames.includes(name));
      if (missing.length === 0) continue;

      const kind = call.queryOpaque ? 'opaque-query' : 'missing-query';
      add(
        `${kind}|${fileOf(call.where)}|${call.method} ${target}`,
        `${call.where} — ${route.module}::${route.handler} requires ` +
          `${missing.map((n) => `${n}=`).join(', ')}`,
      );
    }
  }

  return found;
}

const findings = collectFindings();

/** `source-scan.list` — indent report rows under the failure message. */
const report = list;

describe('api-paths guard: every client API path resolves to a declared route', () => {
  it('reads the routers and the client', () => {
    expect(
      backend.routes.length,
      'no routes read from the mounted routers — the extractor is broken',
    ).toBeGreaterThanOrEqual(ROUTES_READ_AT_LEAST);
    expect(
      client.filesRead,
      'no client files read — the scanned globs are wrong',
    ).toBeGreaterThan(50);
    expect(
      [...methodsByPath.keys()].filter((p) => p.includes('strategy-operations')).length,
      'no strategy-operations alias found — at least the registry family and the ' +
        'preflight declare one, so the extractor is broken',
    ).toBeGreaterThan(0);
    expect(
      [...methodsByPath.keys()].filter((p) => p.startsWith('/api/dag/tasks/')).length,
      'no /api/dag/tasks route found — that prefix lives on the APIRouter(...) ' +
        'constructor, not on the mount, so the mount table is only half read',
    ).toBeGreaterThan(0);
  });

  it('resolves every mount and every route decorator', () => {
    expect(
      backend.unresolvedDecorators,
      'a mount or a route decorator could not be reduced to a path, so the reference ' +
        `set may be incomplete and a client path may be blamed for it:\n${report(
          backend.unresolvedDecorators,
        )}`,
    ).toEqual([]);
  });

  it('accounts for every /api occurrence in code', () => {
    expect(
      client.unaccounted,
      'a path was found in code that no collected literal explains — the scanner ' +
        `cannot see it, and skipping it silently is not an option:\n${report(
          client.unaccounted,
        )}`,
    ).toEqual([]);
  });

  it('reports every call it cannot resolve', () => {
    expect(
      client.unresolvable,
      'transport call(s) whose URL could not be reduced to a path. Hoist the path into ' +
        `a binding this guard can read; do not delete the case:\n${report(
          client.unresolvable,
        )}`,
    ).toEqual([]);
  });

  it('checks at least the calls and paths we know about', () => {
    expect(
      client.calls.length,
      `only ${client.calls.length} call site(s) checked, expected at least ` +
        `${CALLS_CHECKED_AT_LEAST}. A guard that checks nothing passes for free.`,
    ).toBeGreaterThanOrEqual(CALLS_CHECKED_AT_LEAST);
    expect(
      client.literals.length,
      `only ${client.literals.length} path literal(s) checked, expected at least ` +
        `${LITERALS_CHECKED_AT_LEAST}.`,
    ).toBeGreaterThanOrEqual(LITERALS_CHECKED_AT_LEAST);
  });

  it('finds no defect that is not already recorded', () => {
    const unrecorded = [...findings.entries()]
      .filter(([id]) => !(id in KNOWN_API_DEFECTS))
      .map(([id, detail]) => `${id}\n      ${detail}`)
      .sort();

    expect(
      unrecorded,
      'client path(s) the routers do not serve as written. Either fix the call or, if ' +
        'the fix belongs to another task, record it in api-paths.budget.js with a ' +
        `reason:\n${report(unrecorded)}`,
    ).toEqual([]);
  });

  it('records no defect that no longer exists', () => {
    const stale = Object.keys(KNOWN_API_DEFECTS)
      .filter((id) => !findings.has(id))
      .sort();

    expect(
      stale,
      'api-paths.budget.js records defect(s) this scan no longer finds. If they were ' +
        'fixed, delete the entries in the same commit — a list that outlives its ' +
        `defects stops meaning anything:\n${report(stale)}`,
    ).toEqual([]);
  });

  /**
   * The negative control. Without this, "everything passes" could equally mean "the
   * comparison never says no". These are the six spellings that shipped broken, each
   * with the thing the router actually declares.
   */
  it('rejects the six spellings this guard exists because of', () => {
    // 1-3: the prefixed spellings, and the unprefixed ones that resolve.
    const historical = [
      ['/api/strategy-operations/backtests', '/api/backtests'],
      [
        '/api/strategy-operations/strategies/{}/versions/{}/deploy',
        '/api/strategies/{}/versions/{}/deploy',
      ],
      ['/api/strategy-operations/strategies/{}/deployments', '/api/strategies/{}/deployments'],
    ];
    for (const [prefixed, declared] of historical) {
      expect(methodsByPath.has(prefixed), `${prefixed} must not be a declared route`).toBe(false);
      expect(methodsByPath.has(declared), `${declared} must be a declared route`).toBe(true);
      expect(
        isBasePathFragment({ bindingName: 'ANY_NAME', path: prefixed }, client.literals),
        `${prefixed} must not be excused as a base path`,
      ).toBe(false);
    }

    // The trap that shaped `isBasePathFragment`: defect 2's dead path IS a segment
    // prefix of the declared `…/deploy/preflight` alias, so a prefix-only escape
    // hatch would have passed it.
    expect(
      [...methodsByPath.keys()].some((full) =>
        full.startsWith('/api/strategy-operations/strategies/{}/versions/{}/deploy/'),
      ),
      'the preflight alias must still be declared, or the trap above is not being sprung',
    ).toBe(true);

    // 4: the path is right and the call cannot succeed without the parameter.
    const openOrders = routeByKey.get('GET /api/orders/open');
    expect(openOrders, 'GET /api/orders/open must be declared').toBeTruthy();
    expect(openOrders.requiredQuery, 'exchange_id must be read as required').toEqual([
      'exchange_id',
    ]);

    // 5 and 6: no DELETE on the orders router at all, and the two POSTs that replaced
    // the spellings that shipped.
    expect(methodsByPath.get('/api/orders/{}')?.has('DELETE') ?? false).toBe(false);
    expect(methodsByPath.get('/api/orders')?.has('DELETE') ?? false).toBe(false);
    expect(methodsByPath.get('/api/orders/cancel/{}')?.has('POST') ?? false).toBe(true);
    expect(methodsByPath.get('/api/orders/cancel-all')?.has('POST') ?? false).toBe(true);

    // And the three corrected call sites are not in the tree under the old spellings.
    const offenders = client.literals
      .filter((literal) => historical.some(([prefixed]) => literal.path === prefixed))
      .map((literal) => `${literal.where}: ${literal.path}`);
    expect(offenders).toEqual([]);
  });

  it('permits a base-path constant only where routes are demonstrably built from it', () => {
    const bases = client.literals.filter(
      (literal) =>
        !methodsByPath.has(literal.path) && isBasePathFragment(literal, client.literals),
    );
    // Not an emptiness assertion: there is one and it is fine. What is asserted is
    // that this is the only kind excused — a named binding that other literals
    // interpolate into routes the backend declares — and that the list of them is
    // short enough to read.
    //
    // `apiBaseUrl → /api/v1/copilot` stood beside it until `production-launch-hardening`
    // task 9.3 deleted `contexts/CopilotContext.jsx`, where it was a destructured prop
    // default interpolated into four `fetch` calls — two of which were the last two
    // entries in `KNOWN_API_DEFECTS`. `api-surface.js` still accepts a destructuring
    // default as a binding, and that generality is kept deliberately: it is what stops
    // the next `({ apiBaseUrl = "/api/…" })` reading as an address no router serves.
    expect([...new Set(bases.map((b) => `${b.bindingName} → ${b.path}`))].sort()).toEqual([
      'REGISTRY_BASE_PATH → /api/strategy-operations/registry',
    ]);
  });
});
