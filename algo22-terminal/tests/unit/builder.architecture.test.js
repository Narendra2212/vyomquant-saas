/**
 * The builder / backtester import architecture test (task 3.17).
 *
 * **Property 24, structural half: `StrategyBuilder.jsx` imports nothing from the Backtester
 * module tree.** Validates Requirement 22.2 — "THE Strategy_Builder SHALL operate without
 * depending on any backtest execution module."
 *
 * Why this is a build-time rule and not a runtime one
 * ---------------------------------------------------
 * Requirement 22 splits into two halves. The behavioural half — two consumers of one version
 * produce the same trade intents (22.3, 22.4) — is a backend property and is asserted by the
 * shared-artifact test (task 8.10). The half that can only be asserted structurally is this
 * one: the authoring surface must not be able to reach the execution path at all. If it can,
 * "saving" and "backtesting" are one code path away from being the same operation, which is
 * how a save starts a job (22.1) and how the two consumers drift apart.
 *
 * The handoff is a route and a shared artifact, never an import: the builder hands its canvas
 * to an injected `onBacktest` callback whose default `navigate('/app/backtest', …)`, and
 * `App.jsx` — not the builder — lazily loads the Backtester behind that route. Both pages
 * import `lib/canonicalGraph.js`. That is the shared artifact, and it points *into* both pages
 * rather than between them.
 *
 * How the check is scoped
 * ----------------------
 * A regex over one file's top-level imports would be weaker than the property claims — it
 * would miss `StrategyBuilder` importing a helper that imports the Backtester — so this
 * resolves the import graph instead:
 *
 * 1. Read the entry module, strip block comments, and extract every module specifier from
 *    static `import`, side-effect `import`, re-exporting `export … from`, dynamic `import()`
 *    and `require()` forms.
 * 2. Resolve each *relative* specifier against the file system the way the bundler does
 *    (exact path, then `.js` / `.jsx` / `.ts` / `.tsx` / `.mjs` / `.cjs`, then `index.*` for a
 *    directory).
 * 3. Repeat transitively over every resolved code module. The result is the set of local
 *    modules the entry can reach.
 *
 * Its limits, stated rather than papered over:
 *
 * * **Bare specifiers are not followed into `node_modules`.** Nothing in the Backtester tree is
 *   a published package, and no dependency of this app re-exports an app page, so following
 *   them could not change the verdict. That every bare specifier the builder tree reaches is
 *   free of the word is asserted separately, which is the part that could go wrong.
 * * **Only relative specifiers are resolved.** The app declares no path aliases (checked in
 *   `vite.config.js`, and asserted below by requiring that every relative specifier in the
 *   builder's closure resolves and that no reachable module uses an aliased form), so relative
 *   plus bare is the whole vocabulary.
 * * **Runtime wiring is out of scope by design.** A route string and an injected callback are
 *   not imports, and they are how this handoff is *supposed* to work. Both are asserted
 *   positively, so the decoupling is pinned as a mechanism rather than merely as an absence.
 *
 * Two controls keep the result from being vacuously true: the same resolver is run against
 * `App.jsx`, which *does* reach the Backtester — through a lazy dynamic `import()`, the exact
 * form a naive check misses — and against `Backtester.jsx`, which reaches the shared
 * serializer. If the resolver silently resolved nothing, both controls fail.
 */

import { describe, it, expect } from 'vitest';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';

const TERMINAL_ROOT = path.resolve(__dirname, '..', '..');
const SRC_ROOT = path.join(TERMINAL_ROOT, 'src');

const BUILDER = 'pages/StrategyBuilder.jsx';
const BACKTESTER = 'pages/Backtester.jsx';
const APP = 'App.jsx';

/** Extensions whose contents are parsed for further imports. */
const CODE_EXTENSIONS = Object.freeze(['.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs']);

/**
 * Directories never walked when enumerating the tree. `build`, `coverage` and `target` are
 * listed for the same reason as `dist`: none of them exists under `src/` today, so the module
 * set here is unchanged, but a generated tree landing there must not become part of it.
 */
const SKIPPED_DIRS = Object.freeze([
  'node_modules',
  'dist',
  'build',
  'coverage',
  '.git',
  'archive',
  'target',
]);

// ---------------------------------------------------------------------------
// Specifier extraction
// ---------------------------------------------------------------------------

/**
 * Every module specifier in `source`.
 *
 * Block comments are stripped first, and the statement forms are anchored to the start of a
 * line, so the prose in this repo's docblocks — which names module paths freely — cannot be
 * read as a dependency. The `from` clause is restricted to characters that cannot appear
 * between two statements (no quote, no semicolon), so one import cannot swallow the next.
 */
const specifiersIn = (source) => {
  const code = source.replace(/\/\*[\s\S]*?\*\//g, '');
  const patterns = [
    /^[ \t]*import\s+(?:[^'";]*?\sfrom\s*)?['"]([^'"]+)['"]/gm, // import x from 'y'; import 'y'
    /^[ \t]*export\s+(?:[^'";]*?\s)?from\s*['"]([^'"]+)['"]/gm, // export … from 'y'
    /\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)/g, // await import('y'), lazy(() => import('y'))
    /\brequire\s*\(\s*['"]([^'"]+)['"]\s*\)/g, // require('y')
  ];

  const found = [];
  for (const pattern of patterns) {
    for (const match of code.matchAll(pattern)) found.push(match[1]);
  }
  return found;
};

const isRelative = (specifier) => specifier.startsWith('./') || specifier.startsWith('../');

const toRelative = (absolute) => path.relative(SRC_ROOT, absolute).split(path.sep).join('/');

const isCode = (absolute) => CODE_EXTENSIONS.includes(path.extname(absolute));

const readSource = (relative) => readFileSync(path.join(SRC_ROOT, relative), 'utf8');

// ---------------------------------------------------------------------------
// Memoisation
// ---------------------------------------------------------------------------
//
// Every cache below is keyed on a path relative to `src/` and holds the answer for a tree that
// cannot change while the file is running. Nine tests in this file each resolve one or more
// import closures, and the reverse-direction check resolves one per Backtester module; without
// memoisation the same modules were re-read and re-scanned dozens of times, which on a suite
// that runs all its files through one long-lived worker is megabytes of short-lived string
// allocation per test rather than a few hundred kilobytes once.

/** Specifiers of one module, parsed at most once. */
const specifierCache = new Map();

/** Resolved specifier targets, keyed `from\0specifier`. Caches misses (`null`) too. */
const resolutionCache = new Map();

/** Whole closures, keyed on entry module. */
const closureCache = new Map();

/** Every module under `src/`, enumerated at most once. */
let moduleCache = null;

/** Resolve one relative specifier the way the bundler does, or `null`. */
const resolveRelative = (fromRelative, specifier) => {
  const key = `${fromRelative}\u0000${specifier}`;
  if (resolutionCache.has(key)) return resolutionCache.get(key);

  const base = path.resolve(path.dirname(path.join(SRC_ROOT, fromRelative)), specifier);
  const candidates = [
    base,
    ...CODE_EXTENSIONS.map((extension) => `${base}${extension}`),
    ...CODE_EXTENSIONS.map((extension) => path.join(base, `index${extension}`)),
  ];
  let resolved = null;
  for (const candidate of candidates) {
    if (existsSync(candidate) && statSync(candidate).isFile()) {
      resolved = candidate;
      break;
    }
  }
  resolutionCache.set(key, resolved);
  return resolved;
};

/** The specifiers of one module by path, read and parsed at most once per file run. */
const specifiersOf = (relative) => {
  let found = specifierCache.get(relative);
  if (found === undefined) {
    found = specifiersIn(readSource(relative));
    specifierCache.set(relative, found);
  }
  return found;
};

/**
 * The transitive closure of local modules reachable from `entry`.
 *
 * @param {string} entry Path relative to `src/`.
 * @returns {{ modules: Set<string>, external: Set<string>, unresolved: Array<[string, string]>,
 *   edges: Map<string, Set<string>> }}
 */
const importClosure = (entry) => {
  const cached = closureCache.get(entry);
  // Callers only read the result, so one shared answer per entry is safe.
  if (cached !== undefined) return cached;

  const modules = new Set();
  const external = new Set();
  const unresolved = [];
  const edges = new Map();

  const queue = [entry];
  while (queue.length > 0) {
    const current = queue.shift();
    if (modules.has(current)) continue;
    modules.add(current);
    if (!isCode(current)) continue;

    const out = new Set();
    edges.set(current, out);
    for (const specifier of specifiersOf(current)) {
      if (!isRelative(specifier)) {
        external.add(specifier);
        continue;
      }
      const resolved = resolveRelative(current, specifier);
      if (resolved === null) {
        unresolved.push([current, specifier]);
        continue;
      }
      const next = toRelative(resolved);
      out.add(next);
      if (!modules.has(next)) queue.push(next);
    }
  }

  modules.delete(entry);
  const closure = { modules, external, unresolved, edges };
  closureCache.set(entry, closure);
  return closure;
};

/** Every module under `src/`, relative to it. */
const allModules = () => {
  if (moduleCache !== null) return moduleCache;

  const found = [];
  const walk = (dir) => {
    // The directory entry already says whether it is a directory, so no `statSync` per name.
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (SKIPPED_DIRS.includes(entry.name)) continue;
      const absolute = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(absolute);
      else found.push(toRelative(absolute));
    }
  };
  walk(SRC_ROOT);
  moduleCache = found;
  return found;
};

/**
 * The Backtester module tree, discovered rather than hardcoded: every module under `src/`
 * whose path names a backtest.
 *
 * This is deliberately a superset of the Backtester page. `components/landing/
 * BacktestingDemo.jsx` is a marketing component and not an execution module, but the builder
 * has no business importing it either, and a rule stated as "nothing whose path says backtest"
 * keeps holding when the tree grows a second file.
 */
const backtestTree = () =>
  allModules().filter((relative) => isCode(relative) && /backtest/i.test(relative));

// ---------------------------------------------------------------------------
// The resolver has to work before its answer means anything
// ---------------------------------------------------------------------------

describe('the import resolver', () => {
  it('finds the entry modules it is pointed at', () => {
    for (const entry of [BUILDER, BACKTESTER, APP]) {
      expect(existsSync(path.join(SRC_ROOT, entry))).toBe(true);
    }
  });

  it('discovers a non-empty Backtester tree', () => {
    // A rule about an empty set is satisfied by anything at all.
    const tree = backtestTree();
    expect(tree).toContain(BACKTESTER);
    expect(tree.length).toBeGreaterThan(0);
  });

  it('follows a lazy dynamic import: App.jsx does reach the Backtester', () => {
    // The control that matters most. `App.jsx` reaches the Backtester only through
    // `lazy(() => import('./pages/Backtester'))` — extensionless, inside a callback, inside a
    // module-level const. If the resolver missed that form, the builder assertion below would
    // pass for the wrong reason.
    const { modules } = importClosure(APP);

    expect(modules).toContain(BACKTESTER);
    expect(modules).toContain(BUILDER);
  });

  it('follows a plain static import: the Backtester reaches the strategies API module', () => {
    // The plain-static control, held against a module the Backtester reaches through a
    // directory specifier (`import { endpoints } from '../api'` → `api/index.js` →
    // `api/modules/strategies.js`), so both the extensionless-file and the index-resolution
    // forms are exercised. This used to be held against `lib/canonicalGraph.js`; task 17.1
    // removed the Backtester's dependency on the serializer, because the canonical execute
    // endpoint runs the version's own persisted graph and the page no longer serializes one.
    const { modules } = importClosure(BACKTESTER);

    expect(modules).toContain('api/index.js');
    expect(modules).toContain('api/modules/strategies.js');
  });

  it('resolves every relative specifier in the builder closure', () => {
    // An unresolvable relative import would be a broken module, and would also be a hole the
    // traversal walks straight past.
    const { unresolved } = importClosure(BUILDER);

    expect(unresolved).toEqual([]);
  });

  it('does not read a module path out of prose', () => {
    // The docblock at the top of this very file names `StrategyBuilder.jsx`, `Backtester.jsx`
    // and `canonicalGraph.js`, none of them as imports.
    const prose = [
      '/**',
      " * See ./pages/Backtester and `import Backtester from '../pages/Backtester'`.",
      " * Also import('./pages/Backtester').",
      ' */',
      "// import Backtester from './pages/Backtester';",
      "import { toCanonical } from '../lib/canonicalGraph';",
    ].join('\n');

    expect(specifiersIn(prose)).toEqual(['../lib/canonicalGraph']);
  });

  it('extracts each import form, including consecutive and multi-line statements', () => {
    const source = [
      "import 'reactflow/dist/style.css';",
      "import React, { useState } from 'react';",
      'import {',
      '  Activity,',
      '  BarChart2,',
      "} from 'lucide-react';",
      "export { Card } from './components/ui/Card';",
      "export * from './lib/graphValidation';",
      "const Page = lazy(() => import('./pages/Thing'));",
      "const cjs = require('./legacy/thing');",
    ].join('\n');

    expect(specifiersIn(source)).toEqual([
      'reactflow/dist/style.css',
      'react',
      'lucide-react',
      './components/ui/Card',
      './lib/graphValidation',
      './pages/Thing',
      './legacy/thing',
    ]);
  });
});

// ---------------------------------------------------------------------------
// Property 24, structural half
// ---------------------------------------------------------------------------

describe('Property 24 (structural): the builder imports nothing from the Backtester tree', () => {
  it('reaches no Backtester module, at any depth (Requirement 22.2)', () => {
    const tree = backtestTree();
    const { modules } = importClosure(BUILDER);

    const reachable = tree.filter((module) => modules.has(module));
    expect(reachable).toEqual([]);
  });

  it('has a closure large enough for that to mean something', () => {
    // If the traversal collapsed to nothing, the assertion above would be trivially true. The
    // builder genuinely depends on the registry client, the serializer, the validation state
    // machine, the parameter form and the strategies API module.
    const { modules } = importClosure(BUILDER);

    for (const expected of [
      'lib/canonicalGraph.js',
      'lib/registryClient.js',
      'lib/graphValidation.js',
      'lib/connectionLegality.js',
      'lib/blockRegistry.js',
      'components/builder/ParameterForm.jsx',
      'contexts/ValidationContext.jsx',
      'api/modules/strategies.js',
    ]) {
      expect(modules).toContain(expected);
    }
    expect(modules.size).toBeGreaterThan(20);
  });

  it('names no backtest module among its own direct imports either', () => {
    // The narrow check as well as the transitive one, so a direct import is reported against
    // the builder itself rather than as a path through a helper.
    const direct = specifiersOf(BUILDER);

    expect(direct.filter((specifier) => /backtest/i.test(specifier))).toEqual([]);
  });

  it('reaches no bare specifier that names a backtest module', () => {
    // Bare specifiers are not followed into `node_modules`; this is the assertion that stands
    // in for following them.
    const { external } = importClosure(BUILDER);

    expect([...external].filter((specifier) => /backtest/i.test(specifier))).toEqual([]);
  });

  it('is reached by no Backtester module in the other direction', () => {
    // Property 24 is stated one way round, but a Backtester that imported the builder would
    // couple them just as tightly and would drag the whole authoring surface into the
    // execution bundle.
    for (const module of backtestTree()) {
      const { modules } = importClosure(module);
      expect([...modules].filter((reached) => reached === BUILDER)).toEqual([]);
    }
  });

  it('shares the API layer instead — one module, pointed into from both sides', () => {
    // The separation is not isolation. Both pages talk to the same backend through the same
    // API module, and they do it by importing that module, not by importing each other.
    //
    // Until task 17.1 the shared artifact named here was `lib/canonicalGraph.js`: the
    // Backtester serialized the canvas and posted the resulting graph to the legacy
    // job-queue endpoint, so both pages had to agree about what a graph is. It now posts a
    // `version_id` to `POST .../backtests/execute`, which loads the version's own persisted
    // canonical graph server-side, so the Backtester has no graph to serialize and the
    // serializer is the builder's alone. The shared module is one level up.
    const builder = importClosure(BUILDER).modules;
    const backtester = importClosure(BACKTESTER).modules;

    expect(builder).toContain('api/modules/strategies.js');
    expect(backtester).toContain('api/modules/strategies.js');

    // And the serializer is still reached from the authoring side, so its removal from the
    // Backtester is a narrowing rather than a deletion.
    expect(builder).toContain('lib/canonicalGraph.js');
  });
});

// ---------------------------------------------------------------------------
// The handoff, asserted as a mechanism rather than an absence
// ---------------------------------------------------------------------------

describe('the builder hands off by route and callback, not by import', () => {
  it('routes to the backtest path instead of rendering the Backtester', () => {
    const source = readSource(BUILDER);

    expect(source).toContain('/app/backtest');
    // Nothing in the file constructs the component, which is the only thing an import would
    // have been for.
    expect(/<\s*Backtester\b/.test(source)).toBe(false);
    expect(/\bBacktester\s*\(/.test(source)).toBe(false);
  });

  it('takes the backtest action as an injected prop, so the default is replaceable', () => {
    const source = readSource(BUILDER);

    expect(source).toContain('onBacktestProp');
    expect(source).toContain('onBacktest');
  });

  it('loads the Backtester from the router, which is where that decision belongs', () => {
    const app = readSource(APP);

    expect(app).toMatch(/lazy\(\(\)\s*=>\s*import\(['"]\.\/pages\/Backtester['"]\)\)/);
    expect(app).toContain('/app/backtest');
  });
});
