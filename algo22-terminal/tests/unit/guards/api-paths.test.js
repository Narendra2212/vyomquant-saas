/**
 * `api-paths` — the `/api/strategy-operations/…` guard.
 *
 * ===========================================================================
 * WHY THIS EXISTS
 * ===========================================================================
 * The same bug has now been found three times, in three unrelated features:
 *
 *  1. `strategiesApi.listBacktests` (and the raw `fetch` it replaced) read
 *     `/api/strategy-operations/backtests`. The router declares `/backtests`.
 *     It 404'd on every load; the handler only checked `res.ok`, so "Saved
 *     Backtest History" was silently, permanently empty.
 *  2. `strategiesApi.deployVersion` posted to
 *     `/api/strategy-operations/strategies/{id}/versions/{version}/deploy`.
 *     `deploy_version` declares only `/strategies/{id}/versions/{version}/deploy`.
 *     Worse than symmetric: the *preflight* GET beside it registers **both**
 *     spellings, so the gate passed, the Deploy button enabled, and only the
 *     POST 404'd.
 *  3. `strategiesApi.listDeployments` read
 *     `/api/strategy-operations/strategies/{id}/deployments`. `list_deployments`
 *     declares only `/strategies/{id}/deployments`, so the Signal_Trace page had
 *     no deployment ids to compose `signal.{deployment_id}` subscriptions from —
 *     while `components/DeploymentConsole.jsx` read the unprefixed spelling
 *     directly and worked. Two callers, one endpoint, two addresses.
 *
 * One shape, three times: `strategy_operations.router` is mounted at
 * `prefix="/api"` (`backend_app/main.py`), and a *minority* of its routes also
 * declare a `strategy-operations` alias (`_PREFLIGHT_PATHS`, `execute_backtest`'s
 * two decorators, the whole `/registry/*` family). So the prefixed spelling is
 * right for some paths and a 404 for the rest, which is exactly the condition
 * under which eyeballing does not work. This guard asks the routers instead.
 *
 * ===========================================================================
 * SCOPE — stated honestly, because it is narrow on purpose
 * ===========================================================================
 * **Covered.** Every `/api/strategy-operations/…` path that appears in *code*
 * (not prose) under `src/api/modules/**` and `src/lib/**`, checked against the
 * route paths the `.py` files under `backend_app/routers/` actually declare, at
 * the mount prefix `backend_app/main.py` actually mounts them under.
 *
 * **Not covered, deliberately.** Every path in every module against every
 * router. That is a much larger job: it needs the full mount table, FastAPI path
 * parameters modelled per route (`{strategy_id}` vs `{id}` vs a literal
 * segment), and a decision about paths built at runtime from values this file
 * cannot see. It would also be a guard that fails for reasons unrelated to the
 * bug it is here to prevent. The `strategy-operations` prefix is where all three
 * instances were, and it is the only prefix in this codebase that is
 * *sometimes* aliased, so it is the one worth pinning.
 *
 * **Not covered: comments.** A path in a docblock cannot 404. Prose is stripped
 * (`source-scan.stripComments`), which is the same doctrine every other guard
 * here follows: documenting a construct is not using it. Several docblocks in
 * `src/lib/` do still name the prefixed spelling of paths that are unprefixed on
 * the wire — worth a pass, but a documentation defect, not a shipping one.
 *
 * **Not covered: `src/pages/**` and `src/components/**`.** Out of this change's
 * scope. `pages/Backtester.jsx` holds a raw
 * `fetch('/api/strategy-operations/backtests/validate-data')` that this guard
 * would have an opinion about; extending the two globs below is all it takes
 * once someone owns fixing what that surfaces.
 *
 * ===========================================================================
 * FAIL LOUDLY, NEVER SKIP
 * ===========================================================================
 * Frontend paths are template literals, so a complete literal path is not always
 * recoverable: `${encodeURIComponent(strategyId)}` is a value this file cannot
 * know. Interpolations are normalised to a `{}` placeholder and matched on the
 * static segments, which is enough — the three bugs were all in static segments.
 *
 * What this file must never do is *skip* a path it cannot parse. A guard that
 * silently ignores its own blind spots is the green-looking non-run
 * `source-scan.js` warns about in its header. So:
 *
 *  * `${IDENT}` is resolved against path constants declared anywhere in the
 *    scanned tree (`REGISTRY_BASE_PATH` → `REGISTRY_BLOCKS_PATH`), recursively;
 *  * a literal that still cannot be reduced to a path is a **named failure**
 *    (`reports every path it cannot resolve`), not an omission;
 *  * every occurrence of the marker in code must be accounted for by a collected
 *    literal (`accounts for every marker occurrence in code`), so a path hidden
 *    in a construct the extractor does not understand fails rather than vanishes;
 *  * the number of paths checked is asserted against a floor, so the guard
 *    cannot quietly start checking zero and passing.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

import { collect, isTestFile, list, stripComments, TERMINAL_ROOT, toPosix } from './source-scan.js';
import { REPO_ROOT } from '../../../src/api/modules/__tests__/apiSourceContract.js';

/** `<repo>/algo22-terminal`, from `source-scan`; `REPO_ROOT` is its parent. */
const TERMINAL = TERMINAL_ROOT;

/** The frontend trees this guard reads. Both hold transport code. */
const SCANNED_DIRS = [
  path.join(TERMINAL, 'src', 'api', 'modules'),
  path.join(TERMINAL, 'src', 'lib'),
];

const ROUTERS_DIR = path.join(REPO_ROOT, 'backend_app', 'routers');
const MAIN_PY = path.join(REPO_ROOT, 'backend_app', 'main.py');

/** The prefix under audit. */
const MARKER = '/api/strategy-operations';

/**
 * The floor on how many paths must be checked.
 *
 * Eight today: seven complete routes — `registry/blocks`, `registry/timeframes`,
 * `assets`, `strategies/{}/data-quality`,
 * `strategies/{}/versions/{}/deploy/preflight`, `strategies/{}/backtests/execute`,
 * `strategies/{}/nodes/{}/preview` — plus `registryClient.REGISTRY_BASE_PATH`, the
 * one base-path constant.
 *
 * A floor rather than an equality: adding a legitimately-aliased path should not
 * fail CI, but *losing* the ones we know about should. The point of the number is
 * that this guard cannot quietly start checking zero and reporting green, which is
 * how a guard becomes decoration.
 */
const PATHS_CHECKED_AT_LEAST = 8;

/* ── Frontend: extracting paths from JavaScript ─────────────────────────────── */

/**
 * Every complete same-position string or template literal, in source order.
 *
 * Backticks are included — unlike `source-scan.maskStrings`, which excludes them
 * on purpose because it is masking *out* code. Here the template literal **is**
 * the thing being read.
 */
const LITERAL = /`(?:[^`\\]|\\.)*`|'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"/g;

/** `const NAME =` / `export const NAME =` immediately before a literal. */
const CONST_BEFORE = /(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*$/;

/**
 * Literal bodies, with `+`-joined runs folded into one value.
 *
 * Needed because the paths in this codebase are written across two lines:
 *
 *     `/api/strategy-operations/strategies/${encodeURIComponent(id)}` +
 *       `/versions/${encodeURIComponent(version)}/deploy/preflight`
 *
 * Reading those as two separate literals would see two fragments, neither of
 * which is a route — the exact "cannot resolve" state this guard must not enter
 * quietly.
 *
 * @param {string} code Comment-stripped source.
 * @returns {Array<{body: string, line: number, constName: string|null}>}
 */
function foldedLiterals(code) {
  const spans = [...code.matchAll(LITERAL)].map((m) => ({
    body: m[0].slice(1, -1),
    start: m.index,
    end: m.index + m[0].length,
  }));

  const out = [];
  for (let i = 0; i < spans.length; i += 1) {
    let { body, end } = spans[i];
    const { start } = spans[i];

    while (i + 1 < spans.length && /^\s*\+\s*$/.test(code.slice(end, spans[i + 1].start))) {
      i += 1;
      body += spans[i].body;
      end = spans[i].end;
    }

    const before = CONST_BEFORE.exec(code.slice(Math.max(0, start - 120), start));
    out.push({
      body,
      line: code.slice(0, start).split('\n').length,
      constName: before ? before[1] : null,
    });
  }
  return out;
}

/** Every `${…}` whose body holds no brace — i.e. one this file can reason about. */
const INTERPOLATION = /\$\{([^{}]*)\}/g;

/**
 * Substitute `${IDENT}` for the value of a known path constant, recursively.
 *
 * `REGISTRY_BLOCKS_PATH = `${REGISTRY_BASE_PATH}/blocks`` is the case that makes
 * this necessary: without it the marker lives in one constant and the routes
 * built from it are invisible.
 *
 * @param {string} body
 * @param {Map<string, string>} constants
 * @returns {string}
 */
function substitute(body, constants) {
  let value = body;
  for (let depth = 0; depth < 8; depth += 1) {
    const next = value.replace(INTERPOLATION, (whole, inner) => {
      const name = inner.trim();
      return constants.has(name) ? constants.get(name) : whole;
    });
    if (next === value) return value;
    value = next;
  }
  return value;
}

/** `${…}` → `{}`, so a path is compared on its static segments. */
const normaliseFrontend = (value) => value.replace(INTERPOLATION, '{}');

/** A value that is a path this guard can compare. */
const RESOLVED_PATH = /^\/api\/[A-Za-z0-9\-_.{}/]*$/;

/* ── Backend: extracting declared routes from FastAPI ───────────────────────── */

/** `@router.get("…")` / `@router.post(NAME[0])`, anchored to line start. */
const DECORATOR = /^[ \t]*@router\.(get|post|put|patch|delete)\(\s*([^)\n]*?)\s*(?:,|\))/gm;

/** `NAME = ( "…", "…" )` / `NAME = [ "…" ]` — a module-level tuple of paths. */
const PATH_TUPLE = /^([A-Za-z_][\w]*)\s*=\s*[([]([\s\S]*?)[)\]]/gm;

/** A Python string literal. */
const PY_STRING = /"([^"\n]*)"|'([^'\n]*)'/g;

/** `{param}` → `{}`, matching `normaliseFrontend`. */
const normaliseBackend = (value) => value.replace(/\{[^{}]*\}/g, '{}');

/**
 * `module` → mount prefix, from `app.include_router(module.router, prefix="…")`.
 *
 * Same derivation `tests/unit/deployPreflight.test.jsx` uses for the deploy path,
 * generalised over every router. Entries mounted by bare name (`ws_router`,
 * `execution_router`) are absent by design: a router whose mount cannot be read
 * is reported rather than assumed, below.
 *
 * @returns {Map<string, string>}
 */
function mountPrefixes() {
  const source = readFileSync(MAIN_PY, 'utf8');
  const mounts = new Map();
  for (const m of source.matchAll(
    /include_router\(\s*([A-Za-z_][\w]*)\.router\s*,\s*prefix=["']([^"']+)["']/g,
  )) {
    mounts.set(m[1], m[2]);
  }
  return mounts;
}

/**
 * Every route the routers declare, as `{full, module, declared}`, plus the
 * decorator arguments that could not be resolved to a literal path.
 *
 * @returns {{routes: Array<{full: string, module: string, declared: string}>,
 *   unresolvedDecorators: string[]}}
 */
function declaredRoutes() {
  const mounts = mountPrefixes();
  const routes = [];
  const unresolvedDecorators = [];

  for (const file of collect(ROUTERS_DIR, ['.py'])) {
    const module = path.basename(file, '.py');
    const source = readFileSync(file, 'utf8');

    // Module-level tuples of paths, for `@router.get(_PREFLIGHT_PATHS[0])`.
    const tuples = new Map();
    for (const m of source.matchAll(PATH_TUPLE)) {
      const items = [...m[2].matchAll(PY_STRING)].map((s) => s[1] ?? s[2]);
      if (items.length) tuples.set(m[1], items);
    }

    for (const m of source.matchAll(DECORATOR)) {
      const arg = m[2];
      let declared = null;

      // `[^"']*`, not `+`: `@router.get("")` is the mount root (`GET /api/exchanges`),
      // a real declaration and not an unresolved argument.
      const literal = /^["']([^"']*)["']$/.exec(arg);
      const indexed = /^([A-Za-z_][\w]*)\[(\d+)\]$/.exec(arg);
      if (literal) declared = literal[1];
      else if (indexed && tuples.has(indexed[1])) declared = tuples.get(indexed[1])[Number(indexed[2])];

      if (declared == null) {
        unresolvedDecorators.push(`${module}.py: @router.${m[1]}(${arg})`);
        continue;
      }

      const prefix = mounts.get(module);
      if (prefix === undefined) {
        // Only a problem if it could be one of the paths under audit.
        if (declared.includes('strategy-operations')) {
          unresolvedDecorators.push(
            `${module}.py declares ${declared} but main.py does not mount ${module}.router`,
          );
        }
        continue;
      }

      routes.push({ full: normaliseBackend(prefix + declared), module, declared });
    }
  }

  return { routes, unresolvedDecorators };
}

/* ── The scan ───────────────────────────────────────────────────────────────── */

/**
 * Every marker-bearing path in the scanned frontend tree, resolved.
 *
 * @returns {{checked: Array<Object>, prefixes: Array<Object>, unresolved: Array<Object>,
 *   unaccounted: string[]}}
 */
function frontendPaths() {
  const files = SCANNED_DIRS.flatMap((dir) => collect(dir, ['.js', '.jsx'])).filter(
    (file) => !isTestFile(toPosix(path.relative(TERMINAL, file))),
  );

  // Pass 1: every path constant in the tree, so cross-file `${IDENT}` resolves.
  const constants = new Map();
  const parsed = files.map((file) => {
    const code = stripComments(readFileSync(file, 'utf8'));
    const literals = foldedLiterals(code);
    for (const literal of literals) {
      if (literal.constName && !literal.constName.startsWith('_')) {
        constants.set(literal.constName, literal.body);
      }
    }
    return { file, code, literals };
  });

  // Pass 2: resolve, then keep the ones that address the prefix under audit.
  const found = [];
  const unaccounted = [];
  for (const { file, code, literals } of parsed) {
    const relative = toPosix(path.relative(TERMINAL, file));
    let markersInLiterals = 0;

    for (const literal of literals) {
      const resolved = substitute(literal.body, constants);
      if (!resolved.includes(MARKER)) continue;
      markersInLiterals += literal.body.split(MARKER).length - 1;
      found.push({
        where: `${relative}:${literal.line}`,
        raw: literal.body,
        constName: literal.constName,
        value: normaliseFrontend(resolved),
      });
    }

    // Every marker in code must have come out of a literal we collected. If one
    // did not, the extractor met a construct it does not understand and this
    // says so instead of passing.
    const markersInCode = code.split(MARKER).length - 1;
    if (markersInCode !== markersInLiterals) {
      unaccounted.push(
        `${relative}: ${markersInCode} occurrence(s) of ${MARKER} in code, ` +
          `${markersInLiterals} recovered from string/template literals`,
      );
    }
  }

  const unresolved = found.filter((p) => !RESOLVED_PATH.test(p.value));
  return {
    checked: found.filter((p) => RESOLVED_PATH.test(p.value)),
    unresolved,
    unaccounted,
  };
}

/* ── The assertions ─────────────────────────────────────────────────────────── */

const backend = declaredRoutes();
const frontend = frontendPaths();
const declaredFull = new Set(backend.routes.map((r) => r.full));

/**
 * Merely a segment-prefix of some declared route. Necessary for a base path, and
 * nowhere near sufficient — see `isFragmentOfDeclared`.
 */
const isPrefixOfDeclared = (value) =>
  [...declaredFull].some((full) => full.startsWith(`${value}/`));

/**
 * A path fragment rather than an address: a constant that other literals build
 * declared routes out of.
 *
 * `registryClient.REGISTRY_BASE_PATH = '/api/strategy-operations/registry'` is the
 * only one today. It addresses nothing on its own, and the two paths built from it
 * are checked in full, so failing it would be wrong.
 *
 * **Prefix-ness alone is not the test, and the negative control below is why.**
 * `/api/strategy-operations/strategies/{}/versions/{}/deploy` — bug #2, the deploy
 * POST that 404'd — is a perfectly good segment-prefix of the declared preflight
 * alias `…/deploy/preflight`. An escape hatch keyed on prefix-ness would have
 * excused the exact bug this guard exists to catch. So the requirement is
 * *demonstrated use as a building block*: the literal must be a `const`, and some
 * other collected literal must interpolate that constant and resolve to a route
 * the backend actually declares.
 *
 * Residual hole, stated rather than hidden: a constant that is both interpolated
 * into a declared route *and* used directly as an address would be excused. No
 * such constant exists here, and one would be odd on its own terms.
 *
 * @param {{constName: string|null, value: string}} found
 * @param {Array<{raw: string, value: string}>} all Every collected literal.
 */
const isFragmentOfDeclared = (found, all) =>
  found.constName !== null &&
  isPrefixOfDeclared(found.value) &&
  all.some(
    (other) =>
      other !== found &&
      other.raw.includes(`\${${found.constName}}`) &&
      declaredFull.has(other.value),
  );

/** `source-scan.list` — indent report rows under the failure message. */
const report = list;

describe('api-paths guard: /api/strategy-operations paths resolve to declared routes', () => {
  it('reads both trees and the routers', () => {
    expect(backend.routes.length, 'no routes read from backend_app/routers').toBeGreaterThan(0);
    expect(
      [...declaredFull].filter((f) => f.includes('strategy-operations')).length,
      'no strategy-operations alias found in the routers — the extractor is broken, ' +
        'since at least the registry family and the preflight declare one',
    ).toBeGreaterThan(0);
  });

  it('resolves every decorator it finds in the routers', () => {
    expect(
      backend.unresolvedDecorators,
      'a route decorator could not be reduced to a path, so the reference set may be ' +
        `incomplete:\n${report(backend.unresolvedDecorators)}`,
    ).toEqual([]);
  });

  it('accounts for every marker occurrence in code', () => {
    expect(
      frontend.unaccounted,
      'a path was found in code that no collected literal explains — the extractor ' +
        `cannot see it, and skipping it silently is not an option:\n${report(frontend.unaccounted)}`,
    ).toEqual([]);
  });

  it('reports every path it cannot resolve', () => {
    const rows = frontend.unresolved.map((p) => `${p.where}: ${p.value}`);
    expect(
      rows,
      `unresolvable path(s). Normalise the interpolation or hoist the path into a ` +
        `constant this guard can read; do not delete the case:\n${report(rows)}`,
    ).toEqual([]);
  });

  it('checks at least the paths we know about', () => {
    expect(
      frontend.checked.length,
      `only ${frontend.checked.length} path(s) checked, expected at least ` +
        `${PATHS_CHECKED_AT_LEAST}. A guard that checks nothing passes for free — if a ` +
        `path was legitimately removed, lower the floor deliberately.`,
    ).toBeGreaterThanOrEqual(PATHS_CHECKED_AT_LEAST);
  });

  it('finds each one declared with the strategy-operations alias', () => {
    const failures = [];

    for (const found of frontend.checked) {
      if (declaredFull.has(found.value)) continue;
      if (isFragmentOfDeclared(found, frontend.checked)) continue;

      const unprefixed = found.value.replace(`${MARKER}/`, '/api/');
      const because = declaredFull.has(unprefixed)
        ? `the router declares ${unprefixed} and no alias — use that`
        : 'no route with either spelling was found';
      failures.push(`${found.where}\n      requests ${found.value}\n      ${because}`);
    }

    expect(
      failures,
      'path(s) the backend does not declare with the strategy-operations alias. This is ' +
        'the fourth instance of a bug found three times; the router is mounted at /api and ' +
        `only some of its routes carry the alias:\n${report(failures)}`,
    ).toEqual([]);
  });

  /**
   * The negative control. Without this, "everything passes" could equally mean
   * "the comparison never says no". These are the three paths that shipped
   * broken, each with the spelling that actually resolves.
   */
  it('rejects the three spellings this guard exists because of', () => {
    const historical = [
      ['/api/strategy-operations/backtests', '/api/backtests'],
      [
        '/api/strategy-operations/strategies/{}/versions/{}/deploy',
        '/api/strategies/{}/versions/{}/deploy',
      ],
      ['/api/strategy-operations/strategies/{}/deployments', '/api/strategies/{}/deployments'],
    ];

    for (const [prefixed, declared] of historical) {
      expect(declaredFull.has(prefixed), `${prefixed} must not be a declared route`).toBe(false);
      expect(declaredFull.has(declared), `${declared} must be a declared route`).toBe(true);

      // Not excused as a path fragment either — under any constant name, and with
      // every literal in the tree available to build from.
      expect(
        isFragmentOfDeclared({ constName: 'ANY_NAME', value: prefixed }, frontend.checked),
        `${prefixed} must not be excused as a base path`,
      ).toBe(false);
    }

    // The trap that shaped `isFragmentOfDeclared`, asserted so it cannot be forgotten:
    // bug #2's dead path IS a segment-prefix of the declared `…/deploy/preflight` alias.
    // A prefix-only escape hatch would have passed it. This one does not.
    expect(
      isPrefixOfDeclared('/api/strategy-operations/strategies/{}/versions/{}/deploy'),
    ).toBe(true);

    // And none of the three is still in the tree.
    const offenders = frontend.checked
      .filter((p) => historical.some(([prefixed]) => p.value === prefixed))
      .map((p) => `${p.where}: ${p.value}`);
    expect(offenders).toEqual([]);
  });

  it('permits a base-path constant only where routes are demonstrably built from it', () => {
    const bases = frontend.checked.filter(
      (p) => !declaredFull.has(p.value) && isFragmentOfDeclared(p, frontend.checked),
    );
    // Not an emptiness assertion: `registryClient.REGISTRY_BASE_PATH` is one, and it is
    // fine. What is asserted is that it is the only kind excused — a named constant that
    // other literals interpolate into routes the backend declares.
    expect(bases.map((p) => `${p.constName} → ${p.value}`)).toEqual([
      'REGISTRY_BASE_PATH → /api/strategy-operations/registry',
    ]);
  });
});
