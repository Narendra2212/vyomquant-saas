/**
 * Unit tests for `src/components/shell/ResponsiveGate.jsx` — task 8.4.
 * Requirements 17.1, 17.4.
 *
 * Scope note: the width sweep asserting `scrollWidth <= clientWidth` for every in-scope
 * page is task 8.11's Property 33, and it needs a real layout engine for the half that
 * matters. What is here is the by-example half — that each of the three tiers renders what
 * §11.6's table says it renders, that the gate screen has no app behind it, and that
 * `/app/builder` is treated differently from the other nine routes at tablet width.
 *
 * HOW WIDTH IS DRIVEN HERE. `window.matchMedia` is `undefined` under jsdom 23.2.0 and
 * `vitest.config.js` has `setupFiles: []`, so nothing stubs it. `ResponsiveGate` falls
 * back to `window.innerWidth` plus a `resize` listener in exactly that case, so these
 * tests set `window.innerWidth` and dispatch `resize`. The last two tests stub
 * `window.matchMedia` to cover the primary path.
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import { act, cleanup, render, screen } from '@testing-library/react';

import {
  ACCESS,
  MINIMUM_SUPPORTED_VIEWPORT,
  ResponsiveGate,
  ROUTE_MIN_VIEWPORT,
  SIDEBAR_WIDTH_PX,
  VIEWPORT,
  VIEWPORT_ORDER,
  readViewport,
  routeAccess,
  sidebarModeFor,
  useViewportAccess,
  viewportFor,
} from '../../../src/components/shell/ResponsiveGate';
import { NAV_ENTRIES } from '../../../src/components/shell/navigation';
import { token } from '../../../src/design/tokens';

const ORIGINAL_INNER_WIDTH = window.innerWidth;

/** Put jsdom at a width. The fallback path reads `innerWidth`, so this is the whole lever. */
function setWidth(width) {
  Object.defineProperty(window, 'innerWidth', { value: width, configurable: true, writable: true });
}

afterEach(() => {
  cleanup();
  setWidth(ORIGINAL_INNER_WIDTH);
  delete window.matchMedia;
  vi.unstubAllEnvs();
});

/** A marker for "the app rendered". Nothing in the gate screen can produce this text. */
const APP_CONTENT = 'SHELL-CONTENT-MARKER';
function AppContent() {
  return <div data-testid="shell">{APP_CONTENT}</div>;
}

// ═══════════════════════════════════════════════════════════════════════════
// The tier table comes from the tokens (Requirement 1.1)
// ═══════════════════════════════════════════════════════════════════════════

describe('VIEWPORT', () => {
  it('takes every boundary from token.breakpoint, parsed out of the px strings', () => {
    // The tokens are strings; the table is numbers. If this ever inverts, the media
    // queries below would read `(min-width: 768pxpx)` and match nothing.
    expect(token.breakpoint.tablet).toBe('768px');
    expect(VIEWPORT.TABLET.min).toBe(Number.parseFloat(token.breakpoint.tablet));
    expect(VIEWPORT.LAPTOP.min).toBe(Number.parseFloat(token.breakpoint.laptop));
    expect(VIEWPORT.DESKTOP.min).toBe(Number.parseFloat(token.breakpoint.desktop));
    expect(typeof VIEWPORT.TABLET.min).toBe('number');
  });

  it('names the four §11.6 ranges, contiguous and open-ended at the top', () => {
    expect(VIEWPORT_ORDER).toEqual(['BELOW', 'TABLET', 'LAPTOP', 'DESKTOP']);
    expect(VIEWPORT.BELOW).toEqual({ min: 0, max: 767 });
    expect(VIEWPORT.TABLET).toEqual({ min: 768, max: 1023 });
    expect(VIEWPORT.LAPTOP).toEqual({ min: 1024, max: 1439 });
    expect(VIEWPORT.DESKTOP).toEqual({ min: 1440, max: Infinity });
  });

  it('lowers the gate from DesktopOnlyOverlay 1000px to the tablet breakpoint', () => {
    expect(VIEWPORT[MINIMUM_SUPPORTED_VIEWPORT].min).toBe(768);
    // The width the shipped overlay blurred at is supported now.
    expect(viewportFor(1000)).toBe('TABLET');
    expect(routeAccess('/app/dashboard', viewportFor(1000)).access).toBe(ACCESS.FULL);
  });
});

describe('viewportFor', () => {
  it('classifies each boundary and the pixel below it', () => {
    expect(viewportFor(767)).toBe('BELOW');
    expect(viewportFor(768)).toBe('TABLET');
    expect(viewportFor(1023)).toBe('TABLET');
    expect(viewportFor(1024)).toBe('LAPTOP');
    expect(viewportFor(1439)).toBe('LAPTOP');
    expect(viewportFor(1440)).toBe('DESKTOP');
    expect(viewportFor(3840)).toBe('DESKTOP');
  });

  it('puts a fractional width in the tier it has cleared, not between two tiers', () => {
    // A `max-width: 1023px` query would exclude this; classification is `min` only.
    expect(viewportFor(1023.5)).toBe('TABLET');
    expect(viewportFor(767.5)).toBe('BELOW');
  });

  it('answers BELOW for an unmeasurable width rather than assuming a wide one', () => {
    expect(viewportFor(Number.NaN)).toBe('BELOW');
    expect(viewportFor(undefined)).toBe('BELOW');
    expect(viewportFor(-1)).toBe('BELOW');
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// ROUTE_MIN_VIEWPORT — only the Builder needs more than tablet
// ═══════════════════════════════════════════════════════════════════════════

describe('ROUTE_MIN_VIEWPORT', () => {
  it('declares exactly one route, the Builder, at LAPTOP', () => {
    expect(ROUTE_MIN_VIEWPORT).toEqual({ '/app/builder': 'LAPTOP' });
  });

  it('keys off a real navigation.js path, so the two cannot disagree', () => {
    const paths = NAV_ENTRIES.map((entry) => entry.path);
    for (const path of Object.keys(ROUTE_MIN_VIEWPORT)) {
      expect(paths).toContain(path);
    }
  });

  it('leaves the other nine in-scope routes at the app-wide floor', () => {
    const restricted = Object.keys(ROUTE_MIN_VIEWPORT);
    const others = NAV_ENTRIES.filter((entry) => !restricted.includes(entry.path));
    expect(others).toHaveLength(9);
    for (const entry of others) {
      expect(routeAccess(entry.path, 'TABLET').access).toBe(ACCESS.FULL);
    }
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// routeAccess — restricted is a separate answer from unsupported
// ═══════════════════════════════════════════════════════════════════════════

describe('routeAccess', () => {
  it('is unsupported below the floor, on every route, including the Builder', () => {
    for (const entry of NAV_ENTRIES) {
      const decision = routeAccess(entry.path, 'BELOW');
      expect(decision.access).toBe(ACCESS.UNSUPPORTED);
      expect(decision.minimum).toBe(MINIMUM_SUPPORTED_VIEWPORT);
      // Unsupported is a property of the device, so no route-specific restriction is
      // reported — there is nothing for a page to degrade into when it does not render.
      expect(decision.restriction).toBeNull();
    }
  });

  it('distinguishes the Builder at tablet width from the other nine routes', () => {
    const builder = routeAccess('/app/builder', 'TABLET');
    expect(builder.access).toBe(ACCESS.RESTRICTED);
    expect(builder.minimum).toBe('LAPTOP');
    expect(builder.restriction).not.toBeNull();

    expect(routeAccess('/app/dashboard', 'TABLET').access).toBe(ACCESS.FULL);
    expect(routeAccess('/app/trades', 'TABLET').access).toBe(ACCESS.FULL);
  });

  it('restricts the Builder child routes too, via navigation.js patterns', () => {
    expect(routeAccess('/app/builder/abc-123', 'TABLET').access).toBe(ACCESS.RESTRICTED);
    // Case-insensitive for the same reason navigation.js is: react-router matches routes
    // case-insensitively, so `/app/Builder` really renders the Builder.
    expect(routeAccess('/app/Builder', 'TABLET').access).toBe(ACCESS.RESTRICTED);
    // A longer word is a different route, and must not inherit the restriction.
    expect(routeAccess('/app/builderx', 'TABLET').access).toBe(ACCESS.FULL);
  });

  it('gives the Builder full access from laptop up', () => {
    expect(routeAccess('/app/builder', 'LAPTOP').access).toBe(ACCESS.FULL);
    expect(routeAccess('/app/builder', 'DESKTOP').access).toBe(ACCESS.FULL);
  });

  it('gates rather than guesses when handed a tier it does not recognise', () => {
    expect(routeAccess('/app/dashboard', 'HUGE').access).toBe(ACCESS.UNSUPPORTED);
    expect(routeAccess('/app/dashboard', undefined).access).toBe(ACCESS.UNSUPPORTED);
  });

  it('treats an unknown route as unrestricted above the floor', () => {
    // `/app/billing` is a real route with no nav entry (navigation.js §6.4).
    expect(routeAccess('/app/billing', 'TABLET').access).toBe(ACCESS.FULL);
    expect(routeAccess('/nonsense', 'TABLET').access).toBe(ACCESS.FULL);
  });
});

describe('sidebarModeFor', () => {
  it('is the 56px rail at tablet and the 216px expanded sidebar from laptop up', () => {
    expect(sidebarModeFor('TABLET')).toBe('rail');
    expect(sidebarModeFor('LAPTOP')).toBe('expanded');
    expect(sidebarModeFor('DESKTOP')).toBe('expanded');
    expect(SIDEBAR_WIDTH_PX).toEqual({ expanded: 216, rail: 56 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// The three tiers, rendered
// ═══════════════════════════════════════════════════════════════════════════

describe('ResponsiveGate — ≥1024px renders everything', () => {
  it('renders the shell with no strip and reports the expanded sidebar', () => {
    setWidth(1440);
    let seen = null;
    function Probe() {
      seen = useViewportAccess();
      return <AppContent />;
    }
    const { container } = render(<ResponsiveGate pathname="/app/builder"><Probe /></ResponsiveGate>);

    expect(screen.getByText(APP_CONTENT)).toBeTruthy();
    expect(container.querySelector('[data-responsive-gate="full"]')).not.toBeNull();
    expect(container.querySelector('[data-viewport="DESKTOP"]')).not.toBeNull();
    expect(container.querySelector('[role="status"]')).toBeNull();
    expect(seen.access).toBe(ACCESS.FULL);
    expect(seen.sidebarMode).toBe('expanded');
  });
});

describe('ResponsiveGate — 768–1023px is usable, with one restricted route', () => {
  it('renders the nine unrestricted pages fully and asks for the icon rail', () => {
    setWidth(800);
    let seen = null;
    function Probe() {
      seen = useViewportAccess();
      return <AppContent />;
    }
    const { container } = render(<ResponsiveGate pathname="/app/dashboard"><Probe /></ResponsiveGate>);

    expect(screen.getByText(APP_CONTENT)).toBeTruthy();
    expect(container.querySelector('[data-responsive-gate="full"]')).not.toBeNull();
    expect(seen.viewport).toBe('TABLET');
    // Requirement 2.4: the label is not always visible at this width, so the rail must
    // still carry it. That is task 8.6's rendering; this is the decision it reads.
    expect(seen.sidebarMode).toBe('rail');
  });

  it('restricts the Builder without gating it — the page still renders', () => {
    setWidth(800);
    let seen = null;
    function Probe() {
      seen = useViewportAccess();
      return <AppContent />;
    }
    const { container } = render(<ResponsiveGate pathname="/app/builder"><Probe /></ResponsiveGate>);

    // The whole difference from DesktopOnlyOverlay: the page is on screen and interactive.
    expect(screen.getByText(APP_CONTENT)).toBeTruthy();
    expect(container.querySelector('[data-responsive-gate="restricted"]')).not.toBeNull();
    expect(seen.access).toBe(ACCESS.RESTRICTED);
    expect(seen.minimum).toBe('LAPTOP');
  });

  it('states the restriction in a polite live region naming the width it needs', () => {
    setWidth(800);
    render(<ResponsiveGate pathname="/app/builder"><AppContent /></ResponsiveGate>);

    const strip = screen.getByRole('status');
    expect(strip.textContent).toContain('Review mode');
    // Interpolated from the tier table, not typed, so the copy cannot name a width the
    // gate does not switch at.
    expect(strip.textContent).toContain(`${VIEWPORT.LAPTOP.min}px`);
    expect(strip.textContent).toContain('pan, zoom and inspect');
  });

  it('shows no strip on the nine routes that are not restricted', () => {
    setWidth(800);
    for (const entry of NAV_ENTRIES.filter((e) => e.id !== 'builder')) {
      const { container, unmount } = render(
        <ResponsiveGate pathname={entry.path}><AppContent /></ResponsiveGate>,
      );
      expect(container.querySelector('[role="status"]')).toBeNull();
      unmount();
    }
  });
});

describe('ResponsiveGate — <768px is one honest gate screen', () => {
  it('renders no app content behind the gate at all', () => {
    setWidth(600);
    const { container } = render(
      <ResponsiveGate pathname="/app/dashboard"><AppContent /></ResponsiveGate>,
    );

    // Not hidden, not blurred — absent. `queryByText` over the whole container, and the
    // test id too, so a `display: none` wrapper would still fail this.
    expect(screen.queryByText(APP_CONTENT)).toBeNull();
    expect(screen.queryByTestId('shell')).toBeNull();
    expect(container.textContent).not.toContain(APP_CONTENT);
  });

  it('has no blur or opacity trick anywhere in its markup', () => {
    setWidth(600);
    const { container } = render(
      <ResponsiveGate pathname="/app/dashboard"><AppContent /></ResponsiveGate>,
    );
    expect(container.innerHTML).not.toMatch(/blur/i);
    expect(container.innerHTML).not.toMatch(/opacity/i);
  });

  it('states the minimum width, read from the tokens', () => {
    setWidth(320);
    render(<ResponsiveGate pathname="/app/dashboard"><AppContent /></ResponsiveGate>);
    expect(screen.getByText(`Minimum width ${VIEWPORT.TABLET.min}px`)).toBeTruthy();
  });

  it('gives the screen a main landmark and a single h1, and traps no focus', () => {
    setWidth(600);
    const { container } = render(
      <ResponsiveGate pathname="/app/dashboard"><AppContent /></ResponsiveGate>,
    );

    expect(container.querySelectorAll('main')).toHaveLength(1);
    const headings = container.querySelectorAll('h1, h2, h3, h4, h5, h6');
    expect(headings).toHaveLength(1);
    expect(headings[0].tagName).toBe('H1');
    // Nothing focusable, so there is nothing to trap and no focus to restore.
    expect(container.querySelectorAll('a, button, input, select, textarea, [tabindex]')).toHaveLength(0);
    expect(container.querySelector('[aria-hidden="true"][tabindex]')).toBeNull();
  });

  it('gates the Builder too — unsupported outranks the route restriction', () => {
    setWidth(600);
    const { container } = render(
      <ResponsiveGate pathname="/app/builder"><AppContent /></ResponsiveGate>,
    );
    expect(container.querySelector('[data-responsive-gate="unsupported"]')).not.toBeNull();
    // No guidance strip: a strip about review mode over a screen that renders no builder
    // would describe a surface that is not there.
    expect(screen.queryByRole('status')).toBeNull();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Crossing a boundary
// ═══════════════════════════════════════════════════════════════════════════

describe('ResponsiveGate — reacts to a boundary crossing', () => {
  it('swaps the gate screen for the app when the window widens past the floor', () => {
    setWidth(600);
    const { container } = render(
      <ResponsiveGate pathname="/app/dashboard"><AppContent /></ResponsiveGate>,
    );
    expect(screen.queryByText(APP_CONTENT)).toBeNull();

    act(() => {
      setWidth(900);
      window.dispatchEvent(new Event('resize'));
    });

    expect(screen.getByText(APP_CONTENT)).toBeTruthy();
    expect(container.querySelector('[data-viewport="TABLET"]')).not.toBeNull();
  });

  it('drops the Builder into the restricted band when the window narrows', () => {
    setWidth(1200);
    const { container } = render(
      <ResponsiveGate pathname="/app/builder"><AppContent /></ResponsiveGate>,
    );
    expect(container.querySelector('[data-responsive-gate="full"]')).not.toBeNull();

    act(() => {
      setWidth(900);
      window.dispatchEvent(new Event('resize'));
    });

    expect(container.querySelector('[data-responsive-gate="restricted"]')).not.toBeNull();
    expect(screen.getByRole('status')).toBeTruthy();
    // Still rendering the page, which is the Requirement 17.4 point.
    expect(screen.getByText(APP_CONTENT)).toBeTruthy();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// The matchMedia path — the authority when the engine actually has one
// ═══════════════════════════════════════════════════════════════════════════

/**
 * A `matchMedia` that evaluates `min-width` against a fixed width. jsdom ships none, so a
 * test wanting the primary path has to supply it; this is the whole stub it needs.
 */
function stubMatchMedia(width) {
  // An ARRAY of {query, handler}, not a Set of handlers: the component subscribes the same
  // `sync` reference to all three lists, and a Set would dedupe them to one and hide
  // whether every boundary is actually watched.
  const subscriptions = [];
  window.matchMedia = vi.fn((query) => {
    const min = /\(min-width:\s*(\d+(?:\.\d+)?)px\)/.exec(query);
    const drop = (handler) => {
      const at = subscriptions.findIndex((s) => s.query === query && s.handler === handler);
      if (at !== -1) subscriptions.splice(at, 1);
    };
    return {
      media: query,
      matches: min === null ? false : width >= Number.parseFloat(min[1]),
      addEventListener: (_event, handler) => subscriptions.push({ query, handler }),
      removeEventListener: (_event, handler) => drop(handler),
      addListener: (handler) => subscriptions.push({ query, handler }),
      removeListener: (handler) => drop(handler),
    };
  });
  return subscriptions;
}

describe('width observation', () => {
  it('reads matchMedia in preference to innerWidth when the engine evaluates queries', () => {
    // The two disagree on purpose. matchMedia must win.
    setWidth(1600);
    stubMatchMedia(800);
    expect(readViewport()).toBe('TABLET');
  });

  it('falls back to innerWidth when matchMedia is absent, which is the jsdom case', () => {
    setWidth(1600);
    expect(window.matchMedia).toBeUndefined();
    expect(readViewport()).toBe('DESKTOP');
  });

  it('falls back to innerWidth when matchMedia exists but cannot evaluate width', () => {
    // A stub that answers `false` to everything would otherwise make every viewport look
    // like BELOW and gate the whole app on an environment quirk.
    setWidth(1600);
    window.matchMedia = vi.fn(() => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    expect(readViewport()).toBe('DESKTOP');
  });

  it('subscribes to the tier boundaries rather than to resize when matchMedia works', () => {
    setWidth(1600);
    const subscriptions = stubMatchMedia(1600);
    const resizeSpy = vi.spyOn(window, 'addEventListener');

    const { unmount } = render(<ResponsiveGate pathname="/app/dashboard"><AppContent /></ResponsiveGate>);

    // One subscription per tier boundary; three boundaries above BELOW, each a min-width.
    expect(subscriptions).toHaveLength(3);
    expect(subscriptions.map((s) => s.query).sort()).toEqual([
      `(min-width: ${VIEWPORT.DESKTOP.min}px)`,
      `(min-width: ${VIEWPORT.LAPTOP.min}px)`,
      `(min-width: ${VIEWPORT.TABLET.min}px)`,
    ].sort());
    // No max-width anywhere, which is what makes a fractional viewport safe.
    expect(subscriptions.every((s) => !s.query.includes('max-width'))).toBe(true);
    // The resize fallback is not registered when matchMedia is the authority.
    expect(resizeSpy.mock.calls.filter(([event]) => event === 'resize')).toHaveLength(0);

    // And every one of them is released on unmount.
    unmount();
    expect(subscriptions).toHaveLength(0);
    resizeSpy.mockRestore();
  });
});
