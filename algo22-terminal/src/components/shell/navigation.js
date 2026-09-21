/**
 * ═══════════════════════════════════════════════════════════════════════════
 * src/components/shell/navigation.js — the single route/nav table
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 8.1, design.md §6.2 (4), §6.3, §1.10.
 * Requirements 2.1, 2.3, 2.4, 19.4.
 *
 * Data only. No JSX, no colour literals, no `C` import — `Sidebar.jsx` (task 8.6),
 * `TopBar.jsx` (8.7) and `App.jsx` (8.5) render from this table; none of them declares
 * its own. One table means the sidebar, the active state, the `Suspense` fallback title
 * and the `nav-contract` guard can never disagree about what the product's navigation is.
 *
 * ---------------------------------------------------------------------------
 * WHAT `matches` IS FOR (Requirement 2.3, §1.10)
 * ---------------------------------------------------------------------------
 * `Sidebar.jsx` today computes its active state as
 *
 *     const active = location.pathname === `/app/${n.id}`;
 *
 * An exact string comparison against a path derived from the entry id, which breaks in
 * two ways that exist in the shipped app:
 *
 *   1. `/app/backtester` is a real route in `App.jsx` (an alias rendering the same
 *      `Backtester` page as `/app/backtest`). Reaching it highlights nothing.
 *   2. `/app/strategies/:strategyId` is a real route rendering `StrategyDetail`. Opening
 *      a strategy highlights nothing, so the sidebar claims the user is nowhere.
 *
 * Each entry therefore carries a list of regexes rather than one path. `path` stays as
 * the single canonical destination a `NavLink` points at; `matches` is the wider set of
 * pathnames that entry owns.
 *
 * INVARIANT — the patterns are mutually exclusive by construction. Every one is anchored
 * at `^/app/`, followed by a literal first segment, closed by `(\/|$)` so the segment
 * cannot match a longer word: `/app/backtesting` matches nothing rather than matching
 * `backtest`. No two entries share a first segment, so no pathname can ever satisfy two
 * entries and `activeNavId` returning the first hit is a formality, not the reason at
 * most one id comes back (Property 3). Anything added here must preserve that.
 *
 * The `i` flag is deliberate: react-router matches routes case-insensitively unless a
 * route opts into `caseSensitive`, and none of `App.jsx`'s do. So `/app/Dashboard`
 * genuinely renders `Dashboard`, and without `i` it would be a third instance of exactly
 * the bug above — a rendered page with nothing highlighted. Case folding does not
 * threaten the exclusivity invariant: the first segments differ in letters, not case.
 *
 * NEVER put the `g` flag on one of these. `RegExp.prototype.test` advances `lastIndex`
 * on a global regex, which would make these module-level patterns stateful and
 * `activeNavId` answer differently on consecutive calls with the same input.
 *
 * ---------------------------------------------------------------------------
 * THE SET IS FIXED AT TEN (Requirement 2.1)
 * ---------------------------------------------------------------------------
 * Requirement 2.1 names exactly ten entries; `tests/unit/guards/nav-contract.test.js`
 * (task 8.2) holds this file to that number and to an icon plus a label on each one
 * (Requirement 2.4). Changes from the thirteen entries `Sidebar.jsx` renders today:
 *
 *   ADDED — `live-trading`, `portfolio`, `trades`. All three have working routes in
 *   `App.jsx` and none of them was reachable from the sidebar.
 *
 *   MOVED OUT of primary nav, still reachable via the account menu (§6.4, task 8.8) —
 *   `exchange`, `risk`, `billing`, `support`, `notifications`. Their routes are
 *   untouched; only their entry point changes. `routeTitle` below still names them so
 *   the shell can label them while their chunk loads.
 *
 *   DROPPED — `docs`. It is not a route at all: `Sidebar.jsx`'s `handleNavClick`
 *   special-cases it into a `window.open` of `VITE_DOCS_URL`, so one nav entry in
 *   thirteen navigates nowhere in the app and does nothing at all when the env var is
 *   unset. Requirement 19.4 forbids a non-functional link, so it is omitted rather than
 *   shipped broken; §6.4 gives it a home in the account menu, with the same
 *   omitted-if-not-live rule.
 */

import {
  Activity,
  BarChart2,
  FlaskConical,
  Layers,
  LayoutDashboard,
  Radio,
  Receipt,
  Store,
  Wallet,
  Workflow,
} from 'lucide-react';

/**
 * Freezes a nav table all the way down — groups, their `entries` arrays, every entry and
 * every `matches` array. A shallow `Object.freeze` on the outer array would still let a
 * page push a pattern onto `entry.matches` or retarget an entry's `path` at runtime, and
 * a navigation table that a render can rewrite is not a contract a CI guard can hold.
 */
function freezeNavGroups(groups) {
  for (const group of groups) {
    for (const entry of group.entries) {
      Object.freeze(entry.matches);
      Object.freeze(entry);
    }
    Object.freeze(group.entries);
    Object.freeze(group);
  }
  return Object.freeze(groups);
}

/**
 * The four workflow groups, in the order a trader works: is everything OK → build and
 * validate → understand what happened → rehearse and find more (§6.3).
 *
 * `Portfolio` sits under Monitor rather than Review because Requirement 10 frames it as
 * current exposure, not history.
 */
export const NAV_GROUPS = freezeNavGroups([
  {
    id: 'monitor',
    label: 'Monitor',
    entries: [
      {
        id: 'dashboard',
        label: 'Dashboard',
        icon: LayoutDashboard,
        path: '/app/dashboard',
        matches: [/^\/app\/dashboard(\/|$)/i],
      },
      {
        id: 'live-trading',
        label: 'Live Trading',
        icon: Radio,
        path: '/app/live-trading',
        matches: [/^\/app\/live-trading(\/|$)/i],
      },
      {
        id: 'portfolio',
        label: 'Portfolio',
        icon: Wallet,
        path: '/app/portfolio',
        matches: [/^\/app\/portfolio(\/|$)/i],
      },
    ],
  },
  {
    id: 'build',
    label: 'Build & Test',
    entries: [
      {
        id: 'strategies',
        label: 'Strategies',
        icon: Layers,
        path: '/app/strategies',
        // `(\/|$)` is what picks up `/app/strategies/:strategyId` → `StrategyDetail`.
        matches: [/^\/app\/strategies(\/|$)/i],
      },
      {
        id: 'builder',
        label: 'Strategy Builder',
        icon: Workflow,
        path: '/app/builder',
        matches: [/^\/app\/builder(\/|$)/i],
      },
      {
        id: 'backtest',
        // `BarChart2` is lucide's compatibility name for `ChartNoAxesColumn`; it is the
        // name `Sidebar.jsx` already imports and the one §6.3 specifies.
        label: 'Backtester',
        icon: BarChart2,
        path: '/app/backtest',
        // `(er)?` is the `/app/backtester` alias `App.jsx` declares alongside
        // `/app/backtest`. Both render `Backtester`; both must highlight this entry.
        matches: [/^\/app\/backtest(er)?(\/|$)/i],
      },
    ],
  },
  {
    id: 'review',
    label: 'Review',
    entries: [
      {
        id: 'signal-trace',
        label: 'Signal Trace',
        icon: Activity,
        path: '/app/signal-trace',
        // Covers `/app/signal-trace/:signalId`, also a declared route.
        matches: [/^\/app\/signal-trace(\/|$)/i],
      },
      {
        id: 'trades',
        label: 'Trade History',
        icon: Receipt,
        path: '/app/trades',
        matches: [/^\/app\/trades(\/|$)/i],
      },
    ],
  },
  {
    id: 'practise',
    label: 'Practise & Discover',
    entries: [
      {
        id: 'paper-trading',
        label: 'Paper Trading',
        icon: FlaskConical,
        path: '/app/paper-trading',
        matches: [/^\/app\/paper-trading(\/|$)/i],
      },
      {
        id: 'marketplace',
        label: 'Marketplace',
        icon: Store,
        path: '/app/marketplace',
        matches: [/^\/app\/marketplace(\/|$)/i],
      },
    ],
  },
]);

/**
 * The ten entries, group structure flattened away, in sidebar order. What the
 * `nav-contract` guard counts and what anything needing "every nav destination" — the
 * responsive sweep's route registry, Property 3's generator — should iterate.
 */
export const NAV_ENTRIES = Object.freeze(NAV_GROUPS.flatMap((group) => group.entries));

/**
 * The entry that owns `pathname`, or `null` if no entry does.
 *
 * At most one entry can ever match (see the exclusivity invariant above), so this is
 * total and single-valued for every string, including nonsense. `null` is a real answer:
 * `/app/billing` is a live route that deliberately has no sidebar entry, and highlighting
 * nothing is correct there. Requirement 2.3.
 *
 * @param {string} pathname A `location.pathname`, e.g. `/app/strategies/abc-123`.
 * @returns {(typeof NAV_ENTRIES)[number] | null}
 */
export function activeNavEntry(pathname) {
  if (typeof pathname !== 'string') return null;
  for (const entry of NAV_ENTRIES) {
    if (entry.matches.some((pattern) => pattern.test(pathname))) return entry;
  }
  return null;
}

/**
 * The id of the entry that owns `pathname`, or `null`. Requirement 2.3, Property 3.
 *
 * @param {string} pathname
 * @returns {string | null}
 */
export function activeNavId(pathname) {
  return activeNavEntry(pathname)?.id ?? null;
}

/**
 * Titles for the authenticated routes that are NOT sidebar entries — the pages §6.4
 * moves into the account menu, plus `/app/2fa`.
 *
 * These exist because `App.jsx`'s route-level `Suspense` fallback wraps every lazy page
 * in the shell, not only the ten in the sidebar. Without them, `/app/billing` would fall
 * back to a header with no title while its chunk loads. Their layouts are out of scope
 * for the redesign; only the loading title comes from here.
 *
 * Copy matches §6.4's account-menu labels so the same page is not called two things.
 * Keyed by exact path — none of these routes has children in `App.jsx`, so the regex
 * machinery the ten nav entries need would be scaffolding with nothing to hold up.
 */
export const SECONDARY_ROUTE_TITLES = Object.freeze({
  '/app/profile': 'Profile',
  '/app/security-logs': 'Security log',
  '/app/billing': 'Billing & plan',
  '/app/exchange': 'Exchange accounts',
  '/app/risk': 'Risk settings',
  '/app/notifications': 'Notifications',
  '/app/support': 'Support',
  '/app/2fa': 'Two-factor authentication',
});

/**
 * The page title for `pathname`, or `null` when this table does not know the route.
 *
 * Task 8.5 reads this for `<PageHeader title={routeTitle} />` in the route `Suspense`
 * fallback, so the reserved 64px header block carries the right title from the first
 * frame instead of `App.jsx`'s current bare `<div style={{background:'#080A0E'}} />`
 * blanking and then repopulating (§6.2 (4)).
 *
 * A child route resolves to its parent entry's title — `/app/strategies/abc` loads under
 * "Strategies", and `StrategyDetail` replaces that with the strategy's own name once its
 * chunk has mounted. `null` rather than a guessed title for an unknown path: `App.jsx`'s
 * `/app/*` catch-all redirects those to the dashboard, and inventing a heading for a
 * route that is about to disappear would be fabrication (Requirement 19.4).
 *
 * @param {string} pathname
 * @returns {string | null}
 */
export function routeTitle(pathname) {
  const entry = activeNavEntry(pathname);
  if (entry) return entry.label;
  if (typeof pathname !== 'string') return null;
  // Tolerate one trailing slash, the only normalisation these exact-path routes need.
  const exact = pathname.length > 1 && pathname.endsWith('/') ? pathname.slice(0, -1) : pathname;
  // `Object.hasOwn`, not a bare index: a plain object inherits `constructor`, `toString`
  // and friends, so `routeTitle('constructor')` would otherwise hand back a function
  // where every other caller gets a string or `null`.
  return Object.hasOwn(SECONDARY_ROUTE_TITLES, exact) ? SECONDARY_ROUTE_TITLES[exact] : null;
}
