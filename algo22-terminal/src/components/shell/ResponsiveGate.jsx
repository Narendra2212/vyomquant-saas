/**
 * ═══════════════════════════════════════════════════════════════════════════
 * src/components/shell/ResponsiveGate.jsx — the per-route capability gate
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 8.4. design.md §11.6, §1.11. Requirements 17.1, 17.4.
 * Replaced `components/DesktopOnlyOverlay.jsx`, which task 8.5 unwired from `App.jsx` and
 * task 27.3 then deleted. The description of it below is of a file that no longer exists;
 * it is kept because it is the argument for why this module looks the way it does.
 *
 * ---------------------------------------------------------------------------
 * WHAT WAS WRONG WITH THE THING THIS REPLACES
 * ---------------------------------------------------------------------------
 * `DesktopOnlyOverlay` blurs the ENTIRE app and shows "Desktop Optimized / Minimum
 * width: 1000px" whenever `window.innerWidth < 1000`. Requirement 17.1 asks every
 * in-scope page to render without horizontal overflow at tablet width, and 17.4 asks
 * for a *defined* Strategy Builder behaviour at tablet width. A blanket blur at 1000px
 * answers neither: it is not a defined behaviour for one page, it is the absence of
 * behaviour for all ten. It also renders the app behind 8px of blur at 50% opacity,
 * which is worse than not rendering it — a trader can see figures they cannot read and
 * cannot tell whether they are current.
 *
 * The replacement is a gate with three tiers and a per-route minimum:
 *
 *   ≥ 1024px            Everything available. Sidebar expanded at 216px.
 *   768–1023px          Sidebar collapses to a 56px icon rail (label in the tooltip
 *                       AND in the accessible name, so icon and label are both still
 *                       present per Requirement 2.4). `ds/DataTable` applies its own
 *                       `priority` column reduction. Nine of the ten in-scope pages are
 *                       fully usable; `/app/builder` is RESTRICTED, not gated.
 *   < 768px             One honest gate screen. No blur, and `children` are absent from
 *                       the tree entirely — see the `UNSUPPORTED` branch.
 *
 * The threshold therefore drops from 1000px to 768px, and the 768–1023px band changes
 * from "nothing works" to "one page is restricted".
 *
 * ---------------------------------------------------------------------------
 * RESTRICTED IS A SEPARATE ANSWER FROM UNSUPPORTED, AND THAT IS THE POINT
 * ---------------------------------------------------------------------------
 * `ACCESS` has three values, not two, because "this device cannot run the terminal" and
 * "this page wants more room than you have" are different facts with different
 * renderings:
 *
 *   `unsupported`  The viewport is below the app-wide floor. NOTHING of the app renders.
 *                  A property of the device, identical on every route.
 *   `restricted`   The viewport clears the floor but not this route's declared minimum.
 *                  The page renders and stays interactive; it degrades ITSELF. A
 *                  property of the route.
 *   `full`         Every capability is available.
 *
 * Collapsing the two would force `/app/builder` at 800px to be either a gate screen
 * (the `DesktopOnlyOverlay` mistake, scoped to one route) or silently broken. Neither is
 * a *defined* behaviour, which is what Requirement 17.4 asks for.
 *
 * So a restricted route is NOT gated here. This module renders the guidance strip and
 * publishes the decision through {@link useViewportAccess}; the page reads it and turns
 * its own editing surface off. Task 24.7 gives the Builder that read-only review mode —
 * canvas pans and zooms, palette disabled, inspector as a bottom drawer, Save/Backtest/
 * Deploy disabled with a `disabledReason`. Until then the restriction is at least
 * *stated*, which is already more defined than today's blur.
 *
 * NOTE FOR TASK 24.7 — the Builder must NOT render a guidance strip of its own. The
 * copy lives in `RESTRICTED_ROUTES` below and this component renders it once, at the
 * shell, for whichever route is restricted. Two strips saying the same thing is the
 * duplicate-`SimulatedIndicator` problem again.
 *
 * ---------------------------------------------------------------------------
 * BREAKPOINTS ARE READ FROM THE TOKENS, NOT WRITTEN HERE (Requirement 1.1)
 * ---------------------------------------------------------------------------
 * Every number in this file's tier table comes from `token.breakpoint.*`, which
 * `scripts/gen-tokens.mjs` mirrors verbatim from `src/styles/tokens.css` — the same
 * declarations `ds/DataTable`'s `max-laptop:` / `laptop:` variants compile against. A
 * literal `768` here would be a second source of truth that could disagree with the CSS
 * about where the rail appears, which is precisely the class of drift Requirement 1.1
 * exists to stop.
 *
 * The tokens are CSS lengths — the STRINGS `'768px'`, `'1024px'`, `'1440px'` — so they
 * are parsed, not assumed to be numbers. See {@link parseBreakpointPx}.
 *
 * ---------------------------------------------------------------------------
 * HOW WIDTH IS OBSERVED, AND WHY IT IS NOT A `resize` HANDLER BY DEFAULT
 * ---------------------------------------------------------------------------
 * `ds/DataTable` needs no JS for its column reduction: it is CSS-only, so the DOM is
 * identical at every width. This gate is different in kind — it makes a *rendering*
 * decision, choosing between the app and a gate screen — so it needs the real width.
 *
 * `matchMedia` is the authority. Three `min-width` queries, one per tier; the highest
 * matching one names the tier, none matching means below tablet. `change` on a
 * `MediaQueryList` fires only when a tier boundary is actually crossed, so dragging a
 * window from 1400px to 1100px is zero re-renders, where a `resize` handler is one per
 * frame. Only `min-width` is queried — never `max-width` — so a fractional viewport
 * (browser zoom, a hidpi window at 1023.5px) cannot fall between two tiers.
 *
 * `window.innerWidth` plus a `resize` listener is the FALLBACK, registered only when
 * `matchMedia` is missing or cannot evaluate width queries. That case is not
 * hypothetical: **jsdom 23.2.0 does not implement `window.matchMedia` at all** (it is
 * `undefined`), and `vitest.config.js` has `setupFiles: []`, so nothing stubs it. A
 * matchMedia-only implementation would throw on mount in every unit test in this
 * repository. With the fallback, a test sets `window.innerWidth` and dispatches
 * `resize`; a test that wants the matchMedia path must stub `window.matchMedia` itself.
 *
 * The measured width is deliberately NOT displayed on the gate screen. Tier changes are
 * observed; pixel-by-pixel width is not, so a number rendered from it would be stale
 * the moment the window moved — a fabricated measurement, which Requirement 14.5 rules
 * out. The screen states the minimum, which is a constant and always true.
 */

import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import { Maximize2, MonitorUp } from 'lucide-react';

import { token } from '../../design/tokens';
// Straight from the module rather than through `ds/index.js`. The barrel is the surface a
// PAGE imports, and it re-exports 26 primitives; this component wraps the shell, so it is
// in the import graph of every route and would pull all 26 into the entry chunk to render
// one strip. Same argument `ds/index.js` makes for keeping `Chart` out of itself, applied
// one level up. `ds/Panel` imports `./EmptyState` directly for the same reason.
import { Alert } from '../ds/Alert';

import { NAV_ENTRIES, activeNavId } from './navigation';

/**
 * `'768px'` → `768`.
 *
 * The tokens are CSS lengths, so this parses rather than trusting the value to already
 * be a number. A bare number is accepted too, so a future `tokens.css` that emits
 * unitless breakpoints does not break this file silently.
 *
 * It THROWS on anything else, at module load, which fails the app's boot rather than
 * letting it start with a guessed breakpoint. That looks harsh for a generated file,
 * and it is the right trade: the alternative is falling back to a literal, and a gate
 * running on a stale literal either blurs a working desktop or admits a phone the app
 * cannot render on. Both reach a trader silently. A boot failure reaches CI.
 *
 * @param {unknown} value A `token.breakpoint.*` value.
 * @param {string} name The token path, for the message.
 * @returns {number} Pixels.
 */
function parseBreakpointPx(value, name) {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  const match = typeof value === 'string' ? /^\s*(\d+(?:\.\d+)?)\s*px\s*$/.exec(value) : null;
  if (match === null) {
    throw new Error(
      `[shell/ResponsiveGate] token.breakpoint.${name} is ${JSON.stringify(value)}, which is not a `
        + 'CSS pixel length. The tier table is derived from tokens.css (Requirement 1.1) and there '
        + 'is no safe literal to fall back to — run `npm run tokens`.',
    );
  }
  return Number.parseFloat(match[1]);
}

const TABLET_PX = parseBreakpointPx(token.breakpoint.tablet, 'tablet');
const LAPTOP_PX = parseBreakpointPx(token.breakpoint.laptop, 'laptop');
const DESKTOP_PX = parseBreakpointPx(token.breakpoint.desktop, 'desktop');

/**
 * The four named ranges of design.md §11.6, in pixels derived from the tokens above.
 *
 * `max` is inclusive and exists because §11.6 declares the ranges that way and because a
 * consumer reading "what is the tablet band?" should get both ends. Classification does
 * NOT use it — {@link viewportFor} compares against `min` descending, so a viewport of
 * 1023.5px is TABLET rather than falling into the gap between two `max` values.
 *
 * `token.breakpoint.wide` (1920px) is deliberately not a tier: §11.6's table stops at
 * DESKTOP, and a fifth tier nothing branches on would be a decision with no consequence.
 */
export const VIEWPORT = Object.freeze({
  BELOW: Object.freeze({ min: 0, max: TABLET_PX - 1 }),
  TABLET: Object.freeze({ min: TABLET_PX, max: LAPTOP_PX - 1 }),
  LAPTOP: Object.freeze({ min: LAPTOP_PX, max: DESKTOP_PX - 1 }),
  DESKTOP: Object.freeze({ min: DESKTOP_PX, max: Infinity }),
});

/**
 * The tiers narrowest → widest. The ORDER is the comparison: "does this viewport meet
 * that minimum?" is an index comparison in this array, so nothing in this file compares
 * pixel numbers to decide capability.
 */
export const VIEWPORT_ORDER = Object.freeze(['BELOW', 'TABLET', 'LAPTOP', 'DESKTOP']);

/** The app-wide floor. Below this the terminal does not render at all (§11.6). */
export const MINIMUM_SUPPORTED_VIEWPORT = 'TABLET';

/** The three answers this module can give about a route. See the module docblock. */
export const ACCESS = Object.freeze({
  FULL: 'full',
  RESTRICTED: 'restricted',
  UNSUPPORTED: 'unsupported',
});

/** Sidebar geometry per tier (§11.6, task 8.6). Both widths are decided once, here. */
export const SIDEBAR_MODES = Object.freeze(['expanded', 'rail']);

/** `mode` → width in px. 216 fits "Strategy Builder" at `--text-small`; 56 is icon-only. */
export const SIDEBAR_WIDTH_PX = Object.freeze({ expanded: 216, rail: 56 });

/** Narrowest → widest rank of a tier name, or `-1`. */
function rank(viewport) {
  return VIEWPORT_ORDER.indexOf(viewport);
}

/**
 * The `min-width` media queries, widest first, so the first match names the tier.
 * BELOW has no query — it is the absence of all three.
 */
const TIER_QUERIES = Object.freeze(
  ['DESKTOP', 'LAPTOP', 'TABLET'].map((name) =>
    Object.freeze({ name, query: `(min-width: ${VIEWPORT[name].min}px)` }),
  ),
);

/**
 * The routes that need more than the app-wide floor. Declared by NAV ENTRY ID, not by
 * path string, so `navigation.js` stays the only place a route's path and its child-route
 * patterns are written down (task 8.1). `/app/builder/abc-123` is restricted for the same
 * reason it highlights the Builder in the sidebar: one regex table, consulted twice.
 *
 * The copy is §11.6's, and the width in it is interpolated from the tier table rather
 * than typed — a strip that says "1024px" while the gate switches at some other number
 * would be worse than no strip.
 */
const RESTRICTED_ROUTES = Object.freeze([
  Object.freeze({
    navId: 'builder',
    minimum: 'LAPTOP',
    // `Alert`'s `title` is the announcement and must state the condition, so the
    // restriction and its cause go there; the detail is what the trader CAN still do.
    title: `Review mode. Editing a strategy graph needs a screen at least ${VIEWPORT.LAPTOP.min}px wide.`,
    detail: 'You can pan, zoom and inspect nodes here.',
  }),
]);

/**
 * Per-route minimum viewport, keyed by canonical path — §11.6's declared shape.
 *
 * DERIVED from {@link RESTRICTED_ROUTES}, and the paths come from `NAV_ENTRIES`, so this
 * module contains no route path literal at all. Every route absent from this object is
 * `MINIMUM_SUPPORTED_VIEWPORT`; only `/app/builder` needs more.
 */
export const ROUTE_MIN_VIEWPORT = Object.freeze(
  Object.fromEntries(
    RESTRICTED_ROUTES.map((route) => {
      const entry = NAV_ENTRIES.find((candidate) => candidate.id === route.navId);
      if (entry === undefined) {
        // Unreachable while task 8.2's `nav-contract` guard holds the ten ids, and
        // reported rather than thrown because losing a restriction is a downgrade to
        // "fully available", not a crash: the Builder would render unrestricted at
        // tablet width. That is worth a loud line in the console, not a dead app.
        console.error(
          `[shell/ResponsiveGate] no nav entry "${route.navId}" — its viewport restriction is `
            + 'not in effect. Check NAV_ENTRIES in shell/navigation.js.',
        );
        return null;
      }
      return [entry.path, route.minimum];
    }).filter((pair) => pair !== null),
  ),
);

/**
 * The tier a pixel width falls in.
 *
 * Compares against `min` descending, so it is total over every finite width and immune
 * to fractional viewports. A non-finite or negative width answers `BELOW`: an
 * unmeasurable viewport is not a licence to assume a wide one.
 *
 * @param {number} width
 * @returns {'BELOW'|'TABLET'|'LAPTOP'|'DESKTOP'}
 */
export function viewportFor(width) {
  if (typeof width !== 'number' || !Number.isFinite(width)) return 'BELOW';
  if (width >= VIEWPORT.DESKTOP.min) return 'DESKTOP';
  if (width >= VIEWPORT.LAPTOP.min) return 'LAPTOP';
  if (width >= VIEWPORT.TABLET.min) return 'TABLET';
  return 'BELOW';
}

/**
 * True when `matchMedia` exists AND the engine really evaluates width queries.
 *
 * `(min-width: 0px)` is true in every engine that evaluates width, so a `false` here
 * means the queries would all report `false` and every viewport would look like BELOW —
 * the app gated on a jsdom quirk. Falling back to `innerWidth` in that case is what
 * makes this component testable without a `matchMedia` stub.
 */
function canObserveWithMedia() {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  try {
    return window.matchMedia('(min-width: 0px)').matches === true;
  } catch {
    // A partial stub that throws on an unexpected query is still a stub we cannot trust.
    return false;
  }
}

/**
 * Read the current tier. `matchMedia` first, `innerWidth` as the fallback.
 *
 * With no `window` at all there is nothing to measure, and refusing to render the app
 * because we could not measure would be the worse failure, so the widest tier is
 * assumed. This app is a Vite SPA with no server render, so that branch is a guard
 * rather than a code path.
 *
 * @returns {'BELOW'|'TABLET'|'LAPTOP'|'DESKTOP'}
 */
export function readViewport() {
  if (typeof window === 'undefined') return 'DESKTOP';
  if (canObserveWithMedia()) {
    const matched = TIER_QUERIES.find((tier) => window.matchMedia(tier.query).matches);
    return matched === undefined ? 'BELOW' : matched.name;
  }
  return viewportFor(window.innerWidth);
}

/**
 * The current tier, kept current.
 *
 * One `change` subscription per tier boundary when `matchMedia` can be trusted, a single
 * `resize` listener when it cannot — never both, so there is one mechanism in play at a
 * time. Re-reads once immediately after subscribing, which absorbs a resize that landed
 * between the initial `useState` read and the effect.
 *
 * @returns {'BELOW'|'TABLET'|'LAPTOP'|'DESKTOP'}
 */
export function useViewport() {
  const [viewport, setViewport] = useState(readViewport);

  useEffect(() => {
    // `readViewport()` inside the setter, not a captured value: the listener must report
    // what is true when it fires. Setting state to its current value is a no-op in React,
    // so a boundary crossing that does not change the tier costs no render.
    const sync = () => setViewport(readViewport());

    if (canObserveWithMedia()) {
      const lists = TIER_QUERIES.map((tier) => window.matchMedia(tier.query));
      // `addListener` is the Safari < 14 spelling. It is deprecated, not absent, and the
      // terminal's users are not all on current browsers.
      lists.forEach((list) => {
        if (typeof list.addEventListener === 'function') list.addEventListener('change', sync);
        else if (typeof list.addListener === 'function') list.addListener(sync);
      });
      sync();
      return () => {
        lists.forEach((list) => {
          if (typeof list.removeEventListener === 'function') list.removeEventListener('change', sync);
          else if (typeof list.removeListener === 'function') list.removeListener(sync);
        });
      };
    }

    window.addEventListener('resize', sync);
    sync();
    return () => window.removeEventListener('resize', sync);
  }, []);

  return viewport;
}

/** The restriction declared for `pathname`, or `null`. Route matching is `navigation.js`'s. */
function restrictionFor(pathname) {
  const navId = activeNavId(pathname);
  if (navId === null) return null;
  return RESTRICTED_ROUTES.find((route) => route.navId === navId) ?? null;
}

/**
 * The capability decision for a route at a viewport. Pure, total, and the whole of this
 * module's logic — the component below only renders it.
 *
 * An unrecognised `viewport` is treated as `BELOW`, which gates. Guessing a wider tier
 * would put the app on screen at a width nobody measured.
 *
 * @param {string} pathname A `location.pathname`.
 * @param {'BELOW'|'TABLET'|'LAPTOP'|'DESKTOP'} viewport
 * @returns {{access: string, viewport: string, minimum: string, restriction: object|null}}
 */
export function routeAccess(pathname, viewport) {
  const resolved = rank(viewport) === -1 ? 'BELOW' : viewport;
  const here = rank(resolved);

  if (here < rank(MINIMUM_SUPPORTED_VIEWPORT)) {
    return {
      access: ACCESS.UNSUPPORTED,
      viewport: resolved,
      minimum: MINIMUM_SUPPORTED_VIEWPORT,
      restriction: null,
    };
  }

  const restriction = restrictionFor(pathname);
  if (restriction !== null && here < rank(restriction.minimum)) {
    return {
      access: ACCESS.RESTRICTED,
      viewport: resolved,
      minimum: restriction.minimum,
      restriction,
    };
  }

  return {
    access: ACCESS.FULL,
    viewport: resolved,
    minimum: restriction === null ? MINIMUM_SUPPORTED_VIEWPORT : restriction.minimum,
    restriction: null,
  };
}

/** `'expanded'` at laptop and above, `'rail'` at tablet (§11.6, Requirement 2.4). */
export function sidebarModeFor(viewport) {
  return rank(viewport) >= rank('LAPTOP') ? 'expanded' : 'rail';
}

/**
 * The default a page gets with no `ResponsiveGate` above it: fully available on a desktop.
 *
 * A page mounted outside the shell — a focused unit test, a route rendered in isolation —
 * must not silently fall into review mode, because that would make the restricted path
 * the one nobody notices they are testing. The gate is what restricts; its absence is not.
 */
const DEFAULT_CONTEXT = Object.freeze({
  viewport: 'DESKTOP',
  access: ACCESS.FULL,
  minimum: MINIMUM_SUPPORTED_VIEWPORT,
  restriction: null,
  sidebarMode: 'expanded',
});

const ViewportAccessContext = createContext(DEFAULT_CONTEXT);

/**
 * What this route may do at the current width.
 *
 * `{ viewport, access, minimum, restriction, sidebarMode }`. Task 8.5/8.6 read
 * `sidebarMode` for the rail; task 24.7 reads `access === 'restricted'` to turn the
 * Builder's editing surface off. Neither re-derives a breakpoint, which is the point of
 * publishing the decision rather than the width.
 */
export function useViewportAccess() {
  return useContext(ViewportAccessContext);
}

/** Copy for the one gate screen. Stated once so a test can assert against this module. */
const GATE_HEADLINE = 'This screen is too narrow for the terminal';
const GATE_REASON =
  'The terminal is a dense, multi-panel environment: positions, orders, strategy graphs '
  + 'and charts are meant to be read side by side, and below this width they cannot be '
  + 'laid out without hiding or clipping figures you would be trading on.';
const GATE_NEXT_STEP =
  'Rotate to landscape, widen this window, or open the terminal on a laptop or desktop.';

/**
 * The gate screen. The ONLY thing on screen below the floor.
 *
 * No blur and no `children`: the unsupported branch of `ResponsiveGate` does not put them
 * in the tree, so there is no partially-rendered app behind this — the failure mode
 * `DesktopOnlyOverlay` shipped, where unreadable live figures sat behind 8px of blur at
 * half opacity and a trader could not tell whether they were current.
 *
 * Structure: one `<main>` landmark, one `<h1>`, then the reason and the next step in
 * reading order. Nothing is focusable, so there is nothing to trap and no focus to
 * manage. The icons are `aria-hidden` — the heading beside them already says it.
 */
function GateScreen({ minimumWidthPx }) {
  return (
    <main
      data-responsive-gate="unsupported"
      className="flex min-h-dvh w-full flex-col items-center justify-center gap-4 bg-surface-canvas px-4 py-10 text-center"
    >
      <div className="flex items-center gap-3 text-content-secondary">
        <Maximize2 size={20} strokeWidth={1.5} aria-hidden="true" />
        <MonitorUp size={32} strokeWidth={1.5} aria-hidden="true" />
      </div>

      <h1 className="text-page font-semibold text-content-primary">{GATE_HEADLINE}</h1>

      <p className="max-w-md text-body text-content-secondary">{GATE_REASON}</p>

      <div className="flex w-full max-w-sm flex-col gap-2 rounded-lg border border-line-default bg-surface-panel px-4 py-3">
        <p className="font-mono text-small tabular-nums text-brand">
          {`Minimum width ${minimumWidthPx}px`}
        </p>
        <p className="text-small text-content-secondary">{GATE_NEXT_STEP}</p>
      </div>
    </main>
  );
}

/**
 * The gate. Wraps the shell; decides what a route may do at the current width.
 *
 * `pathname` is a PROP rather than a `useLocation()` call, so this module needs no
 * router — the same reason `navigation.js` is data only. `App.jsx` already imports
 * `useLocation`, and the alternative would make every test of this component wrap it in a
 * `MemoryRouter` to assert something that has nothing to do with routing.
 *
 * @param {Object} props
 * @param {string} props.pathname `location.pathname`. Selects the route's minimum.
 * @param {React.ReactNode} props.children The shell. Not rendered when unsupported.
 */
export function ResponsiveGate({ pathname, children }) {
  const viewport = useViewport();
  const { access, minimum, restriction } = routeAccess(pathname, viewport);

  const contextValue = useMemo(
    () => Object.freeze({
      viewport,
      access,
      minimum,
      restriction,
      sidebarMode: sidebarModeFor(viewport),
    }),
    [viewport, access, minimum, restriction],
  );

  // Requirement 17.1's floor. `children` are not evaluated into the tree here — the same
  // discipline `ds/Panel` applies to its six childless states, and the reason there is no
  // markup below that could leak a half-laid-out app behind the gate.
  if (access === ACCESS.UNSUPPORTED) {
    return <GateScreen minimumWidthPx={VIEWPORT[minimum].min} />;
  }

  // Both wrappers are present in BOTH remaining access states, and the strip is the only
  // thing that varies. Property 2 wants shell geometry invariant across route changes, and
  // `/app/builder` and `/app/dashboard` are two in-scope routes at the same width — a
  // wrapper that existed on one and not the other would move the whole shell on that
  // transition, which is the class of shift Requirement 2.2 is about.
  //
  // NOTE FOR TASK 8.5 — the shell grid should be `height: 100%`, not `height: 100dvh`.
  // The outer element here is already the viewport's height and the inner one is the
  // remainder after the strip, so `100dvh` on a descendant would overflow the document by
  // the strip's height at tablet width on `/app/builder` — a horizontal-overflow sibling
  // of exactly the vertical kind, and Requirement 17.1's `min-width: 0` on `main` still
  // belongs there.
  return (
    <ViewportAccessContext.Provider value={contextValue}>
      <div
        data-responsive-gate={access}
        data-viewport={viewport}
        className="flex h-dvh w-full min-w-0 flex-col"
      >
        {/* Requirement 17.4. Rendered here, once, for whichever route is restricted —
            the page must not render its own copy of this. `guidance` is the calm end of
            `Alert`'s severity scale: a width restriction is a standing condition to be
            read, not a failure to be interrupted by (Requirement 16.2). */}
        {access === ACCESS.RESTRICTED && restriction !== null ? (
          <Alert severity="guidance" variant="strip" title={restriction.title}>
            {restriction.detail}
          </Alert>
        ) : null}
        <div className="flex min-h-0 w-full min-w-0 flex-1 flex-col">{children}</div>
      </div>
    </ViewportAccessContext.Provider>
  );
}

export default ResponsiveGate;
