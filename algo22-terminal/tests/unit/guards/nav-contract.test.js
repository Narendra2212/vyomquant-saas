/**
 * `nav-contract` — vyomquant-ui-redesign task 8.2.
 * Requirements 2.1, 2.4. design.md §15.1, §6.3, §6.4, §1.9, §1.10.
 *
 * ===========================================================================
 * WHAT THE SPEC ASKS FOR, AND WHAT THAT TURNS OUT TO MEAN
 * ===========================================================================
 * Task 8.2's own words: "assert `NAV_GROUPS` flattens to exactly the ten
 * Requirement 2.1 ids, no more and no fewer, and that every entry carries both
 * an icon and a non-empty label". design.md §15.1's row says the same thing in
 * one line. Those two clauses are the mandate and they are sections 1 and 2
 * below.
 *
 * Taken alone they would make this file a second copy of
 * `tests/unit/shell/navigation.test.js`, which already pins the ten ids in
 * sidebar order — and a guard that only re-states a unit test adds a file, not
 * a constraint. What makes `nav-contract` a *structural* guard, in the sense the
 * other seven are, is the thing the two clauses are about: `NAV_GROUPS` is the
 * declaration and `App.jsx` is the route table, they are separate files, and
 * they can drift. design.md §1.10 is that drift, already realised and shipped —
 * `portfolio` and `trades` had working routes in `App.jsx` and no sidebar entry
 * at all, and `live-trading` had a route alias and no entry either. Nothing in
 * the repo could have noticed. §1.9 is the same drift from the other direction:
 * the `docs` entry sat in primary nav *presented as a route* while its click
 * handler opened `https://docs.algo22.io`, an external host on a retired brand.
 *
 * So sections 3 to 5 assert the relation between the two tables, which is where
 * the ten-entry set gets its meaning: an entry is only real if a route answers
 * it, and a route is only reachable if an entry (or a deliberate exclusion)
 * names it. Section 7 records what is NOT asserted here and which suite owns it.
 *
 * ---------------------------------------------------------------------------
 * THE CLAUSES, IN FULL
 * ---------------------------------------------------------------------------
 *   1. `NAV_GROUPS` flattens to exactly the ten entries of §6.3's table — the
 *      same ten Requirement 2.1 names — matched on id, label AND path, with no
 *      duplicate in any of the three. No more, no fewer. (Requirement 2.1.)
 *   2. Every entry carries a renderable icon and a non-empty label, and carries
 *      exactly the five declared keys. (Requirement 2.4.)
 *   3. No entry is an external target: every `path` is an app-internal absolute
 *      path, and no primary-nav renderer opens a window or names an absolute
 *      URL. (§1.9.)
 *   4. Every entry's `path` is a route `App.jsx` really declares, and every
 *      authenticated route is either owned by an entry or named in
 *      `SECONDARY_ROUTE_TITLES` as a deliberate exclusion. (§1.10, §6.4.)
 *   5. `navigation.js` is the single source: no other shell file hard-codes a
 *      path that an entry already owns.
 *
 * ===========================================================================
 * NARROWING DECISIONS
 * ===========================================================================
 * THE IDS ARE ASSERTED AS A SET, NOT A SEQUENCE. §6.3 fixes the group order and
 * `tests/unit/shell/navigation.test.js` already pins the flattened order
 * exactly; repeating it here would put the same list in the same shape in two
 * files, so the next id added would have to be added twice or neither. "No more
 * and no fewer" is set equality, and that is what section 1 asserts — over id,
 * label and path together, which is strictly more than the order check the unit
 * test performs and is a different question.
 *
 * "AUTHENTICATED ROUTE" IS THE `/app/` PATH PREFIX, NOT THE `<AuthGuard>`
 * NESTING. The real boundary in `App.jsx` is JSX containment —
 * `<Route element={<AuthGuard />}><Route element={<AppShell />}>…`. A text scan
 * cannot bracket JSX children without a parser, so section 4 takes the prefix
 * instead. In this tree the two coincide exactly: every `/app/…` route is inside
 * that block and every route outside it has a different prefix, including the
 * `/marketplace` route that renders the same page publicly, outside the shell.
 * `the prefix and the guarded block are the same set` names the public routes
 * that must stay out, so a future `/app/…` route declared outside the guard — or
 * a shell route that does not use the prefix — is a visible failure here rather
 * than a silent gap.
 *
 * TWO ROUTES ARE EXEMPT FROM REACHABILITY, AND THE EXEMPTION IS CHECKED RATHER
 * THAN DECLARED. `/app` and `/app/*` carry no page: both render `<Navigate>`.
 * A redirect has nothing for the sidebar to highlight, so requiring an entry for
 * it would be nonsense — but "it's only a redirect" is a claim about the source,
 * so section 4 reads it out of the source. If either becomes a real page, the
 * exemption stops being justified and this file says so.
 *
 * THE EXTERNAL-TARGET BAN IS SCOPED TO THE PRIMARY NAV — `navigation.js`,
 * `Sidebar.jsx`, `TopBar.jsx` — and `AccountMenu.jsx` IS DELIBERATELY OUTSIDE
 * IT. §6.4 gives Docs a home in the account menu precisely *as* an external
 * link, with an external-link icon and `rel="noopener noreferrer"`, and that is
 * the resolution of §1.9 rather than a loophole in it: the defect was an
 * external target sitting in primary nav dressed as a route, not the existence
 * of a documentation link. A carve-out nobody tests is indistinguishable from a
 * hole, so the boundary is asserted from both sides — the three nav files must
 * hold zero external targets, and `AccountMenu.jsx` must still be the file that
 * reads `VITE_DOCS_URL`.
 *
 * NO BUDGET, AND NOT BY OVERSIGHT. Six of the eight structural guards carry a
 * budget or an allowlist because they measure a quantity that a migration is
 * shrinking. This one measures a relation between two tables: it holds or it
 * does not, every clause is at zero today, and there is no migration in flight
 * that would need headroom. A later task should not add one by analogy — a nav
 * entry pointing at a route that does not exist is not debt to be paid down, it
 * is a link that does nothing.
 *
 * ===========================================================================
 * WHY LIVENESS HERE IS FIXED POINTS AND PLANTED COUNTEREXAMPLES, NOT A FLOOR
 * ===========================================================================
 * `legacy-c-budget`'s magnitude floor had to be re-based twice, because a floor
 * on a shrinking quantity trips on the next success. Nothing here is a floor.
 *
 * Liveness comes from two places instead. FIXED POINTS: five named facts that
 * must resolve, each pinning a different link in the chain —
 *
 *   * `dashboard` / `/app/dashboard` — an entry and a route that must meet. If
 *     either the module import or the route extraction broke, this fails.
 *   * `/app/backtester` — a declared route that is no entry's `path` and is
 *     covered by `backtest`'s `matches`. Pins the alias branch of coverage,
 *     which a naive path-equality check would fail.
 *   * `/app/profile` — a declared route that is NOT nav-reachable and IS in
 *     `SECONDARY_ROUTE_TITLES`. Pins the deliberate-exclusion branch, so
 *     section 4 cannot pass by treating every route as excluded.
 *   * the `/app/profile` literal inside `AccountMenu.jsx` — pins the string
 *     extractor in section 5, which otherwise reports nothing in a clean tree
 *     whether it works or not.
 *   * the `VITE_DOCS_URL` read inside `AccountMenu.jsx` — pins the
 *     external-target scope boundary.
 *
 * None of the five shrinks under migration. Each is a named thing the design
 * requires to exist, so re-basing would mean the spec changed.
 *
 * PLANTED COUNTEREXAMPLES: every gate in sections 1-5 compares a list against
 * `[]`, and a list that is always empty passes whatever the tree holds. So the
 * comparison logic is exported as pure functions of `(entries, routes, …)` and
 * section 6 runs both of design.md's real findings back through them — §1.10's
 * missing `portfolio`/`trades`, §1.9's `docs` entry pointing at
 * `https://docs.algo22.io` — plus a synthetic `Sidebar.jsx` that hard-codes
 * `to="/app/dashboard"`. Each must come back reported, by name.
 */

import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

import { describe, it, expect } from 'vitest';

import {
  NAV_ENTRIES,
  NAV_GROUPS,
  SECONDARY_ROUTE_TITLES,
  activeNavEntry,
} from '../../../src/components/shell/navigation.js';
import { SRC, TERMINAL_ROOT, list, maskStrings, stripComments } from './source-scan.js';

// ---------------------------------------------------------------------------
// What the design says the navigation is
// ---------------------------------------------------------------------------

/** The declaration under test, and the route table it has to agree with. */
const NAV_MODULE = 'components/shell/navigation.js';
const ROUTE_MODULE = 'App.jsx';

/**
 * Requirement 2.1's own words, in its own order: "a sidebar containing exactly
 * these navigation entries, grouped by trading workflow: Dashboard, Strategies,
 * Strategy Builder, Backtester, Live Trading, Signal Trace, Portfolio, Trade
 * History, Marketplace, Paper Trading."
 *
 * The requirement names LABELS, not ids, and lists them in neither the sidebar's
 * order nor any group's. It fixes the set; §6.3 below fixes the order and the
 * routes. Both are written out so the guard can assert they agree with each
 * other as well as with the code.
 */
const REQUIREMENT_2_1_LABELS = Object.freeze([
  'Dashboard',
  'Strategies',
  'Strategy Builder',
  'Backtester',
  'Live Trading',
  'Signal Trace',
  'Portfolio',
  'Trade History',
  'Marketplace',
  'Paper Trading',
]);

/**
 * design.md §6.3's table, transcribed — the id, the label and the route for each
 * of the ten. This is the contract: an entry is wrong if any one of the three
 * disagrees, not only if the id does.
 *
 * An entry joins this list by being added to §6.3 and to Requirement 2.1, not by
 * being added here.
 */
const SIDEBAR_IA = Object.freeze([
  Object.freeze({ id: 'dashboard', label: 'Dashboard', path: '/app/dashboard' }),
  Object.freeze({ id: 'live-trading', label: 'Live Trading', path: '/app/live-trading' }),
  Object.freeze({ id: 'portfolio', label: 'Portfolio', path: '/app/portfolio' }),
  Object.freeze({ id: 'strategies', label: 'Strategies', path: '/app/strategies' }),
  Object.freeze({ id: 'builder', label: 'Strategy Builder', path: '/app/builder' }),
  Object.freeze({ id: 'backtest', label: 'Backtester', path: '/app/backtest' }),
  Object.freeze({ id: 'signal-trace', label: 'Signal Trace', path: '/app/signal-trace' }),
  Object.freeze({ id: 'trades', label: 'Trade History', path: '/app/trades' }),
  Object.freeze({ id: 'paper-trading', label: 'Paper Trading', path: '/app/paper-trading' }),
  Object.freeze({ id: 'marketplace', label: 'Marketplace', path: '/app/marketplace' }),
]);

/** The five keys §6.3's declaration gives an entry. Nothing else is an entry field. */
const ENTRY_KEYS = Object.freeze(['id', 'label', 'icon', 'path', 'matches']);

/**
 * The files that render the primary nav. None of them may carry an external
 * target — that is §1.9's finding, stated as a rule over exactly the files it
 * could recur in. `App.jsx` is not here: it renders the shell, not the nav, and
 * the nav it hands to `Sidebar` comes from the module.
 */
const PRIMARY_NAV_RENDERERS = Object.freeze([
  NAV_MODULE,
  'components/Sidebar.jsx',
  'components/TopBar.jsx',
]);

/**
 * The shell files that must not re-declare a nav path. `navigation.js` is
 * excluded because it IS the declaration, and `App.jsx` because the route table
 * and the `/app` redirect target are where these paths are supposed to be
 * written down a second time — as routes, which is the thing being pointed at
 * rather than a competing pointer.
 */
const SHELL_RENDERERS = Object.freeze([
  'components/Sidebar.jsx',
  'components/TopBar.jsx',
  'components/shell/AccountMenu.jsx',
  'components/shell/ResponsiveGate.jsx',
]);

/** §6.4's home for the external Docs link, and the scope boundary in section 3. */
const ACCOUNT_MENU = 'components/shell/AccountMenu.jsx';

/** Routes that carry no page: both render `<Navigate>`. Section 4 checks that claim. */
const REDIRECT_ROUTES = Object.freeze(['/app', '/app/*']);

const readSrc = (relative) => readFileSync(path.join(SRC, relative), 'utf8');

/** Not a space, a newline, a quote, a backtick, or a comment delimiter. */
const NEUTRAL = '\u0001';

/** A complete same-line `` `…` `` span. Used only to locate comment delimiters. */
const SAME_LINE_BACKTICK = /`[^`\n]*`/g;

/**
 * `source` with comments blanked and string contents LEFT INTACT, at their
 * original offsets.
 *
 * ---------------------------------------------------------------------------
 * WHY THIS IS NOT `stripComments`, AND WHY IT IS NOT `codeOnly` EITHER
 * ---------------------------------------------------------------------------
 * This guard reads two things that live inside string literals — a route's
 * `path="…"` and a nav file's `https://…` — so `codeOnly`, which masks strings,
 * would blind it. But bare `stripComments` leaves strings intact by scanning for
 * `/*` without knowing whether it is inside one, and `App.jsx` declares
 *
 *     <Route path="/app/*" element={<Navigate to="/app/dashboard" replace />} />
 *
 * whose embedded `/*` opened a block comment that then ran on until it found a
 * terminator — the close of the "Global catch-all" JSX comment five lines later.
 * Five real lines blanked, `/app/*` gone from the route table, `path="` left
 * unterminated, and — because `[^"]*` crossed newlines — the runaway match then
 * ate the opening quote of `path="*"`, so the global catch-all disappeared too.
 * Both losses were silent: the garbage value still began with `/`, so the shape
 * check passed. `AccountMenu.jsx` has the same shape in prose — a backticked
 * `/app/*` inside a `//` line — and lost nineteen lines to it.
 *
 * So comments are located on a copy where every string span and every same-line
 * backtick span has been neutralised — a `/*` in either is not a comment opener —
 * and the blanks that pass finds are then transplanted back onto the original.
 * Both source-scan helpers preserve length and newlines, so the two index spaces
 * stay aligned and reported offsets still match the file on disk.
 *
 * The neutral filler is {@link NEUTRAL} rather than a space on purpose. The
 * transplant recognises a comment by `stripped[i] === ' ' && neutralised[i] !== ' '`;
 * had the filler been a space, a string INSIDE a comment would have been
 * indistinguishable from one outside and would have survived the blanking.
 *
 * Restricting the backtick mask to one line is what keeps it safe: an odd
 * backtick inside a real block comment cannot pair with one on a later line and
 * mask that comment's own terminator away.
 */
export function codeWithStrings(source) {
  const stringMasked = maskStrings(source);
  // `split('')`, not `[...source]` — see source-scan.js on surrogate pairs.
  const neutral = source.split('');
  for (let i = 0; i < neutral.length; i += 1) {
    if (stringMasked[i] !== neutral[i]) neutral[i] = NEUTRAL;
  }
  const neutralised = neutral
    .join('')
    .replace(SAME_LINE_BACKTICK, (span) => NEUTRAL.repeat(span.length));

  const stripped = stripComments(neutralised);
  const chars = source.split('');
  for (let i = 0; i < chars.length; i += 1) {
    if (stripped[i] === ' ' && neutralised[i] !== ' ') chars[i] = ' ';
  }
  return chars.join('');
}

// ---------------------------------------------------------------------------
// Reading the route table
// ---------------------------------------------------------------------------

/**
 * `path="…"` on a `<Route>`. In `App.jsx` no other element takes a `path` prop.
 *
 * `[^"\n]*`, NOT `[^"]*`. A character class that excludes only the quote also
 * admits newlines, so a `path="` whose closing quote had been blanked matched
 * forward across the rest of the file and consumed the next route's opening
 * quote — one bogus path reported and one real route lost. A route path never
 * contains a newline, so excluding it costs nothing and bounds every match to
 * the line it starts on.
 */
const ROUTE_PATH = /\bpath\s*=\s*"([^"\n]*)"/g;

/**
 * Every route path `source` declares, in declaration order.
 *
 * Comments are removed first, and that is load-bearing rather than tidy: a
 * commented-out `<Route>` is not a declared route, and counting one would let a
 * nav entry point at a page nobody can reach. {@link codeWithStrings} keeps the
 * `path` values themselves, which `codeOnly` would not.
 */
export function declaredRoutes(source) {
  return [...codeWithStrings(source).matchAll(ROUTE_PATH)].map((m) => m[1]);
}

/** The authenticated routes: `/app` and everything below it. See the prefix note above. */
export const appRoutes = (routes) => routes.filter((r) => r === '/app' || r.startsWith('/app/'));

// ---------------------------------------------------------------------------
// The report builders
// ---------------------------------------------------------------------------
//
// Pure functions of their inputs rather than closures over the real tables, so
// "this guard would report §1.9" and "this guard would report §1.10" are tests
// in section 6 and not claims in this docblock.

/** A lucide icon is a `forwardRef` component object; a plain component is a function. */
const isRenderableIcon = (icon) =>
  typeof icon === 'function'
  || (typeof icon === 'object' && icon !== null && typeof icon.$$typeof === 'symbol');

/** An app-internal nav destination: absolute, one segment under `/app`, no authority. */
const INTERNAL_NAV_PATH = /^\/app\/[a-z0-9-]+$/;

/**
 * CLAUSE 2 (Requirement 2.4). Entries missing an icon or a usable label, and
 * entries whose shape is not an entry's shape.
 *
 * The key check belongs with this one rather than on its own: an entry carrying
 * `href`, `url`, `external` or an `onClick` is how an external target gets into
 * a table of routes in the first place, and `docs` got in exactly that way — a
 * nav entry whose destination lived in a click handler instead of in `path`.
 */
export function entryShapeProblems(entries) {
  const problems = [];
  for (const entry of entries) {
    const name = entry?.id ?? JSON.stringify(entry);
    if (!isRenderableIcon(entry?.icon)) {
      problems.push(`${name} — icon is ${entry?.icon === undefined ? 'absent' : typeof entry.icon}, not a component`);
    }
    if (typeof entry?.label !== 'string' || entry.label.trim() === '') {
      problems.push(`${name} — label is ${JSON.stringify(entry?.label)}, not a non-empty string`);
    }
    const extra = Object.keys(entry ?? {}).filter((k) => !ENTRY_KEYS.includes(k));
    if (extra.length) problems.push(`${name} — unknown entry keys: ${extra.join(', ')}`);
  }
  return problems;
}

/**
 * CLAUSE 3 (§1.9). Entries whose destination leaves the app.
 *
 * `https://docs.algo22.io` is the case this exists for. So is `//docs.…`, the
 * protocol-relative form, and so is any path that is not a single segment under
 * `/app` — the sidebar's entries are top-level destinations, and a deep link
 * presented as a primary entry is a different defect worth the same failure.
 */
export const externalTargets = (entries) =>
  entries
    .filter((entry) => !INTERNAL_NAV_PATH.test(String(entry?.path)))
    .map((entry) => `${entry?.id} → ${JSON.stringify(entry?.path)}`);

/** CLAUSE 4a (§1.10). Nav destinations no route answers. */
export const unresolvedNavPaths = (entries, routes) =>
  entries
    .filter((entry) => !routes.includes(entry.path))
    .map((entry) => `${entry.id} → ${entry.path} is not declared in ${ROUTE_MODULE}`);

/**
 * Does any entry own `route`? The same relation `activeNavEntry` applies, taken
 * over a supplied table so section 6 can hand it a synthetic one. `sidebar owns
 * exactly what the module says it owns` pins the two together over every real
 * route, so this cannot drift from the module it mirrors.
 */
export const coveredByNav = (route, entries) =>
  entries.some((entry) => entry.matches.some((pattern) => pattern.test(route)));

/**
 * CLAUSE 4b (§1.10, §6.4). Authenticated routes reachable from nothing.
 *
 * A route passes by being owned by a nav entry — including through `matches`, so
 * `/app/backtester` and `/app/strategies/:strategyId` count — or by being named
 * in `SECONDARY_ROUTE_TITLES`, which is how §6.4 records a page that is
 * deliberately out of primary nav and lives in the account menu instead. Being
 * in neither is what `portfolio` and `trades` were.
 */
export const orphanRoutes = (routes, entries, secondaryTitles) =>
  routes
    .filter((route) => !REDIRECT_ROUTES.includes(route))
    .filter((route) => !coveredByNav(route, entries) && !(route in secondaryTitles))
    .map((route) => `${route} — no nav entry owns it and SECONDARY_ROUTE_TITLES does not name it`);

/** `window.open`, and any absolute or protocol-relative URL, in code (not in prose). */
const EXTERNAL_TARGET_IN_SOURCE = /\bwindow\s*\.\s*open\s*\(|\bhttps?:\/\/[^\s'"`)]+|(?<![:\w])\/\/[a-z0-9-]+\.[a-z]{2,}/gi;

/**
 * CLAUSE 3, source half. An external target written into a primary-nav file.
 *
 * Comments are stripped and strings are NOT: the URL and the env-var read both
 * live inside code and string literals, while every mention of the old `docs`
 * behaviour in this tree is prose explaining its removal —
 * `Sidebar.jsx`'s own header records the `window.open` it deleted, and
 * `AccountMenu.jsx`'s records the same. A rule that counted those would make
 * deleting the explanation the cheapest way to pass.
 *
 * The protocol-relative alternative carries a `(?<![:\w])` lookbehind so the
 * `//` of a line comment that survived stripping, and the `://` of a URL already
 * matched by the first alternative, are not counted twice.
 */
export const externalTargetsInSource = (source) =>
  [...stripComments(source).matchAll(EXTERNAL_TARGET_IN_SOURCE)].map((m) => m[0]);

/** A quoted `/app/…` literal. */
const APP_PATH_LITERAL = /['"`](\/app\/[^'"`\s]*)['"`]/g;

/** Every `/app/…` string literal in `source`, comments removed. */
export const appPathLiterals = (source) =>
  [...stripComments(source).matchAll(APP_PATH_LITERAL)].map((m) => m[1]);

/**
 * CLAUSE 5. `/app/…` literals in a shell file that a nav entry already owns.
 *
 * A literal naming a SECONDARY route is fine and expected: `AccountMenu.jsx`'s
 * seven targets and `TopBar.jsx`'s notification bell are §6.4's own destinations,
 * declared nowhere else. What is not fine is a second pointer at one of the ten,
 * because then two files decide where Dashboard is.
 */
export const redeclaredNavPaths = (relative, source, entries) =>
  appPathLiterals(source)
    .filter((literal) => coveredByNav(literal, entries))
    .map((literal) => `${relative} — "${literal}" is already declared in ${NAV_MODULE}`);

// ---------------------------------------------------------------------------
// The scan
// ---------------------------------------------------------------------------

const ROUTE_SOURCE = readSrc(ROUTE_MODULE);
const DECLARED_ROUTES = declaredRoutes(ROUTE_SOURCE);
const APP_ROUTES = appRoutes(DECLARED_ROUTES);

// ---------------------------------------------------------------------------
// 1. The set is exactly Requirement 2.1's ten (Requirement 2.1)
// ---------------------------------------------------------------------------

describe('nav-contract: the ten entries', () => {
  it('flattens to exactly ten, with no duplicate id, label or path', () => {
    expect(NAV_ENTRIES).toHaveLength(10);
    expect(new Set(NAV_ENTRIES.map((e) => e.id)).size).toBe(10);
    expect(new Set(NAV_ENTRIES.map((e) => e.label)).size).toBe(10);
    expect(new Set(NAV_ENTRIES.map((e) => e.path)).size).toBe(10);
    // The flatten is the whole table, not one group of it.
    expect(NAV_GROUPS.reduce((n, g) => n + g.entries.length, 0)).toBe(10);
  });

  it('is §6.3 ids, no more and no fewer', () => {
    const declared = new Set(NAV_ENTRIES.map((e) => e.id));
    const expected = new Set(SIDEBAR_IA.map((e) => e.id));
    const extra = [...declared].filter((id) => !expected.has(id));
    const missing = [...expected].filter((id) => !declared.has(id));

    expect(
      extra,
      `Requirement 2.1 fixes the sidebar at ten entries and design.md §6.3 names them.\n`
        + `These ids are in ${NAV_MODULE} and in neither:\n${list(extra)}`,
    ).toEqual([]);
    expect(
      missing,
      `§6.3 names these entries and ${NAV_MODULE} does not declare them:\n${list(missing)}`,
    ).toEqual([]);
  });

  it('gives each id §6.3 label and route, not only the right id', () => {
    // The id alone is not the contract. §1.10's defect was a table whose ids
    // looked plausible while three routes had no entry at all, and an entry
    // retargeted at the wrong path would keep its id.
    const byId = new Map(NAV_ENTRIES.map((e) => [e.id, e]));
    const wrong = SIDEBAR_IA.filter(
      (row) => byId.get(row.id)?.label !== row.label || byId.get(row.id)?.path !== row.path,
    ).map(
      (row) =>
        `${row.id} — declared ${JSON.stringify(byId.get(row.id)?.label)} at `
        + `${JSON.stringify(byId.get(row.id)?.path)}, §6.3 says ${JSON.stringify(row.label)} `
        + `at ${JSON.stringify(row.path)}`,
    );

    expect(wrong, `These entries disagree with design.md §6.3:\n${list(wrong)}`).toEqual([]);
  });

  it('carries the ten names Requirement 2.1 itself lists', () => {
    // Requirement 2.1 enumerates labels, in an order that is neither the
    // sidebar's nor a group's. Asserted as a set against the code, and against
    // §6.3, so a rename that satisfied the design table while contradicting the
    // requirement is still a failure.
    expect(new Set(NAV_ENTRIES.map((e) => e.label))).toEqual(new Set(REQUIREMENT_2_1_LABELS));
    expect(new Set(SIDEBAR_IA.map((e) => e.label))).toEqual(new Set(REQUIREMENT_2_1_LABELS));
    expect(REQUIREMENT_2_1_LABELS).toHaveLength(10);
  });
});

// ---------------------------------------------------------------------------
// 2. Every entry has an icon and a label (Requirement 2.4)
// ---------------------------------------------------------------------------

describe('nav-contract: an icon and a label on every entry', () => {
  it('declares both on all ten, and nothing that is not an entry field', () => {
    const problems = entryShapeProblems(NAV_ENTRIES);

    expect(
      problems,
      `Requirement 2.4: every navigation entry carries both an icon and a text label, and\n`
        + `§6.3 gives an entry exactly ${ENTRY_KEYS.join(', ')}. A destination that does not fit\n`
        + `\`path\` is not a navigation entry — see the \`docs\` case in §1.9:\n${list(problems)}`,
    ).toEqual([]);
  });

  it('holds a real icon component, not a name or an element', () => {
    // The fixed point for this clause: a string `"LayoutDashboard"` or a
    // pre-built `<LayoutDashboard />` element would both be truthy, which is all
    // the unit test asks of them, and neither is something `Sidebar` can render
    // at a size it chooses.
    const dashboard = NAV_ENTRIES.find((e) => e.id === 'dashboard');
    expect(isRenderableIcon(dashboard.icon)).toBe(true);
    expect(typeof dashboard.icon).not.toBe('string');
    expect(dashboard.label).toBe('Dashboard');
  });
});

// ---------------------------------------------------------------------------
// 3. Nothing in the primary nav leaves the app (§1.9)
// ---------------------------------------------------------------------------

describe('nav-contract: no nav entry is an external target', () => {
  it('points every entry at an app-internal path', () => {
    const external = externalTargets(NAV_ENTRIES);

    expect(
      external,
      `design.md §1.9: the \`docs\` entry opened https://docs.algo22.io from primary nav,\n`
        + `presented as if it were a route. A primary entry is one segment under /app.\n`
        + `§6.4 is where an external link belongs:\n${list(external)}`,
    ).toEqual([]);
  });

  it('opens no window and names no absolute URL in any primary-nav file', () => {
    const findings = PRIMARY_NAV_RENDERERS.flatMap((relative) => {
      expect(existsSync(path.join(SRC, relative)), `${relative} is missing`).toBe(true);
      return externalTargetsInSource(readSrc(relative)).map((hit) => `${relative} — ${hit}`);
    });

    expect(
      findings,
      `§1.9's defect was a \`window.open\` inside the nav click handler, not only a bad URL.\n`
        + `These files render the primary nav and must contain neither:\n${list(findings)}`,
    ).toEqual([]);
  });

  it('leaves the account menu outside that rule, which is where §6.4 puts Docs', () => {
    // The boundary asserted from the other side. §6.4 gives Docs an external
    // home with an external-link icon and rel="noopener noreferrer"; that is the
    // resolution of §1.9, not an exception to it. If this read moved back into a
    // nav file the assertion above would catch it, and if it vanished entirely
    // this one would — so the carve-out cannot quietly become a hole.
    expect(PRIMARY_NAV_RENDERERS).not.toContain(ACCOUNT_MENU);
    const accountMenu = readSrc(ACCOUNT_MENU);
    expect(accountMenu).toContain('VITE_DOCS_URL');
    // And no nav file reads it, which is the half §1.9 was actually about.
    for (const relative of PRIMARY_NAV_RENDERERS) {
      expect(stripComments(readSrc(relative))).not.toContain('VITE_DOCS_URL');
    }
  });
});

// ---------------------------------------------------------------------------
// 4. The declaration and the route table agree (§1.10, §6.4)
// ---------------------------------------------------------------------------

describe('nav-contract: the route table', () => {
  it('extracts routes that look like routes, and reaches the whole file', () => {
    // Liveness for everything below. Every value is an absolute path or the one
    // global catch-all, and both ends of the file are represented — a public
    // route and the last authenticated one — so an extraction that stopped early
    // cannot pass.
    for (const route of DECLARED_ROUTES) {
      expect(route, `${route} is not a route path`).toMatch(/^(?:\/|\*$)/);
    }
    expect(DECLARED_ROUTES).toContain('/legal/privacy');
    expect(DECLARED_ROUTES).toContain('/app/notifications');
    expect(DECLARED_ROUTES).toContain('*');
    // The two exempt routes are really there, so section 4's exclusion is a
    // filter over something rather than a no-op.
    for (const route of REDIRECT_ROUTES) expect(APP_ROUTES).toContain(route);
  });

  it('agrees that the /app prefix and the guarded block are the same set', () => {
    // The narrowing named in the header. `/marketplace` renders the same page as
    // `/app/marketplace` but outside the shell and outside `<AuthGuard>`, so it
    // is the one route that would break this if the prefix and the block ever
    // came apart — and it is public, so it must NOT be treated as an app route.
    expect(DECLARED_ROUTES).toContain('/marketplace');
    expect(APP_ROUTES).not.toContain('/marketplace');
    for (const route of ['/', '/signin', '/2fa', '/wizard', '/admin/waitlist']) {
      expect(DECLARED_ROUTES).toContain(route);
      expect(APP_ROUTES).not.toContain(route);
    }
    // `/app/2fa` is a different route from the public `/2fa` and is in the set.
    expect(APP_ROUTES).toContain('/app/2fa');
  });

  it('exempts /app and /app/* from reachability only because they redirect', () => {
    // Read out of the source rather than assumed. A redirect has no page for the
    // sidebar to highlight; a page would.
    expect(ROUTE_SOURCE).toMatch(/path="\/app"\s+element=\{<Navigate\b/);
    expect(ROUTE_SOURCE).toMatch(/path="\/app\/\*"\s+element=\{<Navigate\b/);
  });

  it('declares a route for every one of the ten nav destinations', () => {
    const unresolved = unresolvedNavPaths(NAV_ENTRIES, DECLARED_ROUTES);

    expect(
      unresolved,
      `A nav entry whose \`path\` is not a declared route is a link that goes nowhere.\n`
        + `Either add the route to ${ROUTE_MODULE} or remove the entry:\n${list(unresolved)}`,
    ).toEqual([]);
  });

  it('declares a route for every secondary title too', () => {
    // `SECONDARY_ROUTE_TITLES` is what excuses a route from the sidebar (§6.4),
    // and `routeTitle` hands its values to the shell's `Suspense` header. An
    // entry naming a route that no longer exists would put a title on nothing.
    const stale = Object.keys(SECONDARY_ROUTE_TITLES).filter((p) => !DECLARED_ROUTES.includes(p));

    expect(
      stale,
      `SECONDARY_ROUTE_TITLES names routes ${ROUTE_MODULE} does not declare:\n${list(stale)}`,
    ).toEqual([]);
  });

  it('leaves no authenticated route reachable from nothing', () => {
    // THE ASSERTION §1.10 EXISTS FOR. `/app/portfolio` and `/app/trades` were
    // declared routes with no sidebar entry and no recorded reason, and
    // `/app/live-trading` was too. Nothing in the repo could have said so.
    const orphans = orphanRoutes(APP_ROUTES, NAV_ENTRIES, SECONDARY_ROUTE_TITLES);

    expect(
      orphans,
      `design.md §1.10: an authenticated route with no way into it is a page that exists and\n`
        + `cannot be found. Give it a nav entry (which means changing Requirement 2.1's ten), or\n`
        + `record the deliberate exclusion in SECONDARY_ROUTE_TITLES the way §6.4 does for the\n`
        + `account-menu pages, or delete the route:\n${list(orphans)}`,
    ).toEqual([]);
  });

  it('owns the /app/backtester alias without making it a second entry', () => {
    // The fixed point for the alias branch. A coverage check written as path
    // equality would pass every other route here and fail this one.
    expect(APP_ROUTES).toContain('/app/backtester');
    expect(NAV_ENTRIES.map((e) => e.path)).not.toContain('/app/backtester');
    expect(coveredByNav('/app/backtester', NAV_ENTRIES)).toBe(true);
    expect(activeNavEntry('/app/backtester').id).toBe('backtest');
    // And the child routes, which are declared with their params in the source.
    expect(coveredByNav('/app/strategies/:strategyId', NAV_ENTRIES)).toBe(true);
    expect(coveredByNav('/app/signal-trace/:signalId', NAV_ENTRIES)).toBe(true);
  });

  it('keeps a deliberately excluded route excluded, not silently covered', () => {
    // The fixed point for the other branch. If `coveredByNav` were ever widened
    // to something that matched `/app/*`, the orphan check above would pass
    // vacuously — every route covered, nothing ever reported.
    expect(APP_ROUTES).toContain('/app/profile');
    expect(coveredByNav('/app/profile', NAV_ENTRIES)).toBe(false);
    expect(SECONDARY_ROUTE_TITLES['/app/profile']).toBe('Profile');
    expect(orphanRoutes(['/app/profile'], NAV_ENTRIES, {})).toHaveLength(1);
  });

  it('owns exactly what the module says it owns', () => {
    // `coveredByNav` mirrors `activeNavEntry` so section 6 can pass it a
    // synthetic table. Pinned to the real function over every declared route, so
    // the mirror cannot drift from the thing it mirrors.
    for (const route of APP_ROUTES) {
      expect(coveredByNav(route, NAV_ENTRIES), route).toBe(activeNavEntry(route) !== null);
    }
  });
});

// ---------------------------------------------------------------------------
// 5. One source of truth
// ---------------------------------------------------------------------------

describe('nav-contract: navigation.js is the single source', () => {
  it('lets no other shell file hard-code a path an entry owns', () => {
    const findings = SHELL_RENDERERS.flatMap((relative) => {
      expect(existsSync(path.join(SRC, relative)), `${relative} is missing`).toBe(true);
      return redeclaredNavPaths(relative, readSrc(relative), NAV_ENTRIES);
    });

    expect(
      findings,
      `Two files deciding where a nav destination is means they can disagree — which is what\n`
        + `§1.10's \`'/app/' + n.id\` was. Read the path from ${NAV_MODULE}:\n${list(findings)}`,
    ).toEqual([]);
  });

  it('finds the secondary-route literals that are supposed to be there', () => {
    // The fixed point for this clause: in a clean tree the assertion above
    // reports nothing whether the extractor works or not. §6.4's account-menu
    // targets are declared as literals in `AccountMenu.jsx` by design — they are
    // not nav entries, so nothing else declares them — and `TopBar.jsx`'s bell
    // points at one. Both must be found, and neither may be nav-owned.
    const accountMenuPaths = appPathLiterals(readSrc(ACCOUNT_MENU));
    expect(accountMenuPaths).toContain('/app/profile');
    expect(accountMenuPaths).toContain('/app/billing');
    expect(appPathLiterals(readSrc('components/TopBar.jsx'))).toContain('/app/notifications');
    for (const literal of accountMenuPaths) {
      expect(literal in SECONDARY_ROUTE_TITLES, `${literal} is not a §6.4 route`).toBe(true);
    }
  });
});

// ---------------------------------------------------------------------------
// 6. The guard is live, not vacuous
// ---------------------------------------------------------------------------

describe('nav-contract: the guard would report the defects it exists for', () => {
  /** §6.3's table as a minimal entry set, for planting one defect at a time. */
  const entry = (over = {}) =>
    Object.freeze({
      id: 'dashboard',
      label: 'Dashboard',
      icon: () => null,
      path: '/app/dashboard',
      matches: [/^\/app\/dashboard(\/|$)/i],
      ...over,
    });

  it('would report §1.10 — a route with a working page and no entry', () => {
    // The real thirteen-entry table from §1.10, reduced to the part that matters:
    // `portfolio` and `trades` absent while `App.jsx` declares both. This is the
    // shipped defect, run through the real comparison.
    const before = [entry(), entry({ id: 'strategies', label: 'Strategies', path: '/app/strategies', matches: [/^\/app\/strategies(\/|$)/i] })];
    const routes = ['/app', '/app/dashboard', '/app/strategies', '/app/portfolio', '/app/trades'];

    const orphans = orphanRoutes(routes, before, {});
    expect(orphans).toHaveLength(2);
    expect(orphans.join('\n')).toContain('/app/portfolio');
    expect(orphans.join('\n')).toContain('/app/trades');
    // And the real table answers the same routes cleanly.
    expect(orphanRoutes(routes, NAV_ENTRIES, SECONDARY_ROUTE_TITLES)).toEqual([]);
  });

  it('would report §1.9 — an external target dressed as a nav entry', () => {
    const docs = entry({ id: 'docs', label: 'Docs', path: 'https://docs.algo22.io', matches: [] });

    expect(externalTargets([docs])).toEqual(['docs → "https://docs.algo22.io"']);
    expect(externalTargets([entry({ id: 'docs', path: '//docs.algo22.io' })])).toHaveLength(1);
    expect(externalTargets([entry({ id: 'docs', path: 'http://localhost:3000/app/docs' })])).toHaveLength(1);
    // And the handler form, which is how `docs` actually shipped: the
    // destination was not in `path` at all.
    expect(entryShapeProblems([entry({ id: 'docs', path: '/app/docs', onClick: () => {} })])).toEqual([
      'docs — unknown entry keys: onClick',
    ]);
    expect(externalTargets(NAV_ENTRIES)).toEqual([]);
  });

  it('would report §1.9 in a nav file, past a comment that documents it', () => {
    // `Sidebar.jsx`'s real header describes the `window.open` it removed. The
    // prose must not count and the code must.
    expect(
      externalTargetsInSource("  const url = import.meta.env.VITE_DOCS_URL;\n  window.open(url, '_blank');"),
    ).toEqual(['window.open(']);
    expect(externalTargetsInSource('<a href="https://docs.algo22.io">Docs</a>')).toEqual([
      'https://docs.algo22.io',
    ]);
    expect(
      externalTargetsInSource(
        [
          '/*',
          " * `handleNavClick`'s `docs` branch — a `window.open(import.meta.env.VITE_DOCS_URL)`",
          ' * opening https://docs.algo22.io, a stale brand. Removed at task 8.6.',
          ' */',
        ].join('\n'),
      ),
    ).toEqual([]);
    expect(externalTargetsInSource('// window.open(url) used to live here')).toEqual([]);
  });

  it('would report a missing icon, an empty label and a stray field', () => {
    expect(entryShapeProblems([entry({ icon: undefined })])[0]).toContain('icon is absent');
    expect(entryShapeProblems([entry({ icon: 'LayoutDashboard' })])[0]).toContain('not a component');
    expect(entryShapeProblems([entry({ label: '' })])[0]).toContain('not a non-empty string');
    expect(entryShapeProblems([entry({ label: '   ' })])).toHaveLength(1);
    expect(entryShapeProblems([entry({ href: '/x' })])).toEqual([
      'dashboard — unknown entry keys: href',
    ]);
    expect(entryShapeProblems(NAV_ENTRIES)).toEqual([]);
  });

  it('would report an entry pointing at a route that does not exist', () => {
    const typo = entry({ id: 'trades', label: 'Trade History', path: '/app/trade-history' });

    expect(unresolvedNavPaths([typo], DECLARED_ROUTES)).toHaveLength(1);
    expect(unresolvedNavPaths([typo], DECLARED_ROUTES)[0]).toContain('/app/trade-history');
    expect(unresolvedNavPaths(NAV_ENTRIES, DECLARED_ROUTES)).toEqual([]);
  });

  it('would report a second file deciding where Dashboard is', () => {
    const planted = '<NavLink to="/app/dashboard">Dashboard</NavLink>';

    expect(redeclaredNavPaths('components/Sidebar.jsx', planted, NAV_ENTRIES)).toHaveLength(1);
    expect(redeclaredNavPaths('components/Sidebar.jsx', planted, NAV_ENTRIES)[0]).toContain(
      '/app/dashboard',
    );
    // The `'/app/' + n.id` form §1.10 records is not a literal and is not caught
    // here — `activeNavId` is what replaced it and `sidebar.test.jsx` asserts the
    // rendered hrefs. A literal secondary target stays legal.
    expect(redeclaredNavPaths('components/shell/AccountMenu.jsx', "path: '/app/profile'", NAV_ENTRIES)).toEqual([]);
  });

  it('does not count a commented-out route as declared', () => {
    // Load-bearing: a nav entry pointing at a route someone commented out would
    // otherwise resolve, and the entry would be a dead link that passes.
    expect(declaredRoutes('<Route path="/app/x" element={<Suspense fallback={F}><X /></Suspense>} />')).toEqual(['/app/x']);
    expect(declaredRoutes('{/* <Route path="/app/x" element={<X />} /> */}')).toEqual([]);
    expect(declaredRoutes('// <Route path="/app/x" element={<X />} />')).toEqual([]);
    expect(unresolvedNavPaths([entry({ path: '/app/x' })], declaredRoutes('// <Route path="/app/x" />'))).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// 7. What this guard does not decide
// ---------------------------------------------------------------------------

describe('nav-contract: the boundaries of a static scan', () => {
  it('leaves the rendered half of Requirement 2.4 to the shell suites', () => {
    // This file asserts that the DECLARATION carries an icon and a label. That
    // both reach the screen is a rendering question, and at 768-1023px the label
    // moves into a tooltip and the accessible name rather than staying visible
    // (§11.6) — no source scan can see either. `tests/unit/shell/sidebar.test.jsx`
    // owns it, including at rail width, and this asserts the two files exist to
    // divide the clause rather than leaving half of it unclaimed.
    const suites = ['tests/unit/shell/sidebar.test.jsx', 'tests/unit/shell/navigation.test.js'];
    for (const suite of suites) {
      expect(existsSync(path.join(TERMINAL_ROOT, suite)), `${suite} is missing`).toBe(true);
    }
  });

  it('cannot decide whether an external documentation site is live', () => {
    // §1.9's rule is "omitted rather than shipped broken if the URL is not live".
    // Whether `VITE_DOCS_URL` is set is a build fact and whether it answers is a
    // network fact; neither is in the source. `AccountMenu.jsx` resolves the env
    // var at runtime and omits the entry when it is absent, which is the half
    // that IS testable — `tests/unit/shell/accountMenu.test.jsx` owns it.
    expect(readSrc(ACCOUNT_MENU)).toContain('VITE_DOCS_URL');
    expect(existsSync(path.join(TERMINAL_ROOT, 'tests/unit/shell/accountMenu.test.jsx'))).toBe(true);
  });

  it('cannot decide that a declared route renders a working page', () => {
    // Section 4 proves a route exists for every entry. It does not prove the
    // page behind it works, and two of the authenticated routes do not even take
    // a `Suspense` fallback — they render directly. "The route is declared" is
    // the strongest statement available from the route table as text.
    expect(APP_ROUTES).toContain('/app/support');
    expect(APP_ROUTES).toContain('/app/notifications');
    expect(ROUTE_SOURCE).toMatch(/path="\/app\/support"\s+element=\{<SupportCenter\b/);
  });
});
