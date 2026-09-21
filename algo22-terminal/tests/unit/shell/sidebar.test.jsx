/**
 * Unit tests for `src/components/Sidebar.jsx` — task 8.6.
 * Requirements 2.1, 2.3, 2.4, 17.1.
 *
 * Scope note: shell geometry across route changes is task 8.9's Property 2 and the
 * keyboard/accessible-name sweep across every page is task 8.12's Property 36. What is
 * here is the by-example half, and every example is a defect §1.10 records in the
 * sidebar this replaced: the three routes that had no entry, `/app/backtester`
 * highlighting nothing, `/app/strategies/:id` highlighting nothing, and `docs` opening
 * a window instead of navigating.
 *
 * HOW RAIL MODE IS DRIVEN HERE. `Sidebar` reads `sidebarMode` from
 * `useViewportAccess()`, so the lever is a real `ResponsiveGate` above it rather than a
 * prop. `window.matchMedia` is `undefined` under jsdom 23.2.0 and `vitest.config.js` has
 * `setupFiles: []`, so the gate falls back to `window.innerWidth` — which is what these
 * tests set. Same arrangement as `responsiveGate.test.jsx`.
 */

import { describe, it, expect, afterEach } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import Sidebar, {
  SIDEBAR_BRAND_HEIGHT_PX,
  SIDEBAR_FOOTER_HEIGHT_PX,
} from '../../../src/components/Sidebar';
import { ResponsiveGate, SIDEBAR_WIDTH_PX } from '../../../src/components/shell/ResponsiveGate';
import { NAV_ENTRIES, NAV_GROUPS } from '../../../src/components/shell/navigation';

const ORIGINAL_INNER_WIDTH = window.innerWidth;

function setWidth(width) {
  Object.defineProperty(window, 'innerWidth', { value: width, configurable: true, writable: true });
}

afterEach(() => {
  cleanup();
  setWidth(ORIGINAL_INNER_WIDTH);
});

/**
 * Mount the sidebar at `pathname` and at a viewport width.
 *
 * `accountMenu` is supplied by default because that is how the shell renders — `App.jsx`
 * passes `shell/AccountMenu` into the slot (task 8.8). A `<span />` stands in for it here:
 * what the slot CONTAINS is `accountMenu.test.jsx`'s subject, and none of these tests
 * should depend on the menu's Supabase and entitlements reads. Two tests below vary it
 * deliberately.
 */
function renderSidebar({ pathname = '/app/dashboard', width = 1440, accountMenu = <span /> } = {}) {
  setWidth(width);
  return render(
    <MemoryRouter initialEntries={[pathname]}>
      <ResponsiveGate pathname={pathname}>
        <Sidebar accountMenu={accountMenu} />
      </ResponsiveGate>
    </MemoryRouter>,
  );
}

/** The ten nav anchors, in DOM order. */
function navLinks() {
  return within(screen.getByRole('navigation', { name: 'Main' })).getAllByRole('link');
}

// ═══════════════════════════════════════════════════════════════════════════
// It renders the table, and only the table
// ═══════════════════════════════════════════════════════════════════════════

describe('Sidebar: the entries come from NAV_GROUPS', () => {
  it('renders exactly ten links, in group order, pointing at the table paths', () => {
    renderSidebar();
    const links = navLinks();

    expect(links).toHaveLength(NAV_ENTRIES.length);
    expect(links).toHaveLength(10);
    expect(links.map((a) => a.getAttribute('data-nav-id'))).toEqual(
      NAV_ENTRIES.map((e) => e.id),
    );
    expect(links.map((a) => a.getAttribute('href'))).toEqual(NAV_ENTRIES.map((e) => e.path));
  });

  it('names the four workflow groups and labels each list with its heading', () => {
    renderSidebar();

    expect(NAV_GROUPS.map((g) => g.label)).toEqual([
      'Monitor',
      'Build & Test',
      'Review',
      'Practise & Discover',
    ]);
    for (const group of NAV_GROUPS) {
      // `aria-labelledby` -> the heading, so the grouping is announced rather than
      // merely drawn. `within` proves the entries really are inside their own group.
      const list = screen.getByRole('list', { name: group.label });
      expect(within(list).getAllByRole('link').map((a) => a.getAttribute('data-nav-id'))).toEqual(
        group.entries.map((e) => e.id),
      );
    }
  });

  it('surfaces the three routes that had a working route but no entry', () => {
    renderSidebar();
    const ids = navLinks().map((a) => a.getAttribute('data-nav-id'));
    expect(ids).toContain('live-trading');
    expect(ids).toContain('portfolio');
    expect(ids).toContain('trades');
  });

  it('carries no `docs` entry and no external link, so nav is only navigation', () => {
    renderSidebar();
    const links = navLinks();

    expect(links.map((a) => a.getAttribute('data-nav-id'))).not.toContain('docs');
    for (const link of links) {
      // A `target` or an absolute `href` here would be the `window.open` branch back in
      // another form (Requirement 19.4, §6.3).
      expect(link.getAttribute('target')).toBeNull();
      expect(link.getAttribute('href')).toMatch(/^\/app\//);
    }
  });

  it('renders a real anchor per entry, not a button calling navigate()', () => {
    renderSidebar();
    // Middle-click, Ctrl-click and "copy link address" all need the `href` above; this
    // asserts the element type that carries it (Requirement 18.1, §6.3).
    for (const link of navLinks()) {
      expect(link.tagName).toBe('A');
    }
    expect(
      within(screen.getByRole('navigation', { name: 'Main' })).queryAllByRole('button'),
    ).toEqual([]);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Active state — the alias and the child route are the point (Requirement 2.3)
// ═══════════════════════════════════════════════════════════════════════════

describe('Sidebar: active state', () => {
  /** The `data-nav-id`s claiming to be the current page. */
  function currentIds() {
    return navLinks()
      .filter((a) => a.getAttribute('aria-current') === 'page')
      .map((a) => a.getAttribute('data-nav-id'));
  }

  it('marks the canonical route', () => {
    renderSidebar({ pathname: '/app/portfolio' });
    expect(currentIds()).toEqual(['portfolio']);
  });

  it('marks Backtester on the /app/backtester alias', () => {
    // The exact `location.pathname === '/app/' + n.id` comparison this replaced
    // highlighted nothing here, on a route `App.jsx` really declares (§1.10).
    renderSidebar({ pathname: '/app/backtester' });
    expect(currentIds()).toEqual(['backtest']);
  });

  it('marks Strategies on a /app/strategies/:strategyId child route', () => {
    renderSidebar({ pathname: '/app/strategies/abc-123' });
    expect(currentIds()).toEqual(['strategies']);
  });

  it('marks Signal Trace on a child route too', () => {
    renderSidebar({ pathname: '/app/signal-trace/sig-9' });
    expect(currentIds()).toEqual(['signal-trace']);
  });

  it('marks nothing on a live route the sidebar deliberately does not carry', () => {
    // `/app/billing` moved to the account menu (§6.4). Highlighting nothing is the
    // correct answer, not a bug — `activeNavId` returns null and this must not guess.
    renderSidebar({ pathname: '/app/billing' });
    expect(currentIds()).toEqual([]);
  });

  it('gives the active entry a non-colour affordance as well as the wash', () => {
    renderSidebar({ pathname: '/app/backtester' });
    const links = navLinks();
    const active = links.find((a) => a.getAttribute('data-nav-id') === 'backtest');
    const inactive = links.find((a) => a.getAttribute('data-nav-id') === 'dashboard');

    // WCAG 1.4.1: the rule is present on one and absent on the other, and the weight
    // differs. Neither is a hue comparison.
    expect(active.style.borderLeftColor).toBe('var(--color-brand)');
    expect(inactive.style.borderLeftColor).toBe('transparent');
    expect(active.style.fontWeight).toBe('600');
    expect(inactive.style.fontWeight).toBe('500');

    // And the wash, from the token rather than an rgba() literal.
    expect(active.style.background).toBe('var(--color-brand-wash)');
    expect(inactive.style.background).toBe('');
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Rail mode (768-1023px) — Requirement 2.4
// ═══════════════════════════════════════════════════════════════════════════

describe('Sidebar: rail mode', () => {
  it('is 216px expanded and 56px at tablet width, from the gate not a local breakpoint', () => {
    const { container: expanded } = renderSidebar({ width: 1440 });
    const expandedAside = expanded.querySelector('[data-shell="sidebar"]');
    expect(expandedAside.getAttribute('data-sidebar-mode')).toBe('expanded');
    expect(expandedAside.style.width).toBe(`${SIDEBAR_WIDTH_PX.expanded}px`);
    expect(expandedAside.style.width).toBe('216px');

    cleanup();

    const { container: rail } = renderSidebar({ width: 800 });
    const railAside = rail.querySelector('[data-shell="sidebar"]');
    expect(railAside.getAttribute('data-sidebar-mode')).toBe('rail');
    expect(railAside.style.width).toBe(`${SIDEBAR_WIDTH_PX.rail}px`);
    expect(railAside.style.width).toBe('56px');
    // The width cannot be stretched by content (Requirement 2.2, 17.1).
    expect(railAside.style.maxWidth).toBe('56px');
  });

  it('keeps every entry an accessible name equal to its table label at 56px', () => {
    renderSidebar({ width: 800 });
    const links = navLinks();

    // The whole of Requirement 2.4 at rail width: an icon with no accessible name is a
    // nav item a screen-reader user cannot announce.
    for (const entry of NAV_ENTRIES) {
      expect(screen.getByRole('link', { name: entry.label })).toBe(
        links.find((a) => a.getAttribute('data-nav-id') === entry.id),
      );
    }
  });

  it('keeps the label in the tooltip as well as the accessible name at 56px', () => {
    renderSidebar({ width: 800 });
    for (const link of navLinks()) {
      const entry = NAV_ENTRIES.find((e) => e.id === link.getAttribute('data-nav-id'));
      expect(link.getAttribute('title')).toBe(entry.label);
    }
  });

  it('drops the visible group headings but keeps the groups, in order', () => {
    renderSidebar({ width: 800 });

    // Still announced and still labelling their lists — only the visible text goes.
    for (const group of NAV_GROUPS) {
      const heading = screen.getByRole('heading', { name: group.label });
      expect(heading.className).toContain('sr-only');
      expect(screen.getByRole('list', { name: group.label })).toBeTruthy();
    }
    expect(
      screen.getAllByRole('heading').map((h) => h.textContent),
    ).toEqual(NAV_GROUPS.map((g) => g.label));
  });

  it('carries no title attribute when the label is on screen', () => {
    renderSidebar({ width: 1440 });
    for (const link of navLinks()) {
      expect(link.getAttribute('title')).toBeNull();
      expect(link.textContent).toBe(
        NAV_ENTRIES.find((e) => e.id === link.getAttribute('data-nav-id')).label,
      );
    }
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Keyboard order and the reserved blocks
// ═══════════════════════════════════════════════════════════════════════════

describe('Sidebar: keyboard and geometry', () => {
  it('puts all ten entries in the tab order, none of them removed from it', () => {
    renderSidebar();
    for (const link of navLinks()) {
      // A real `href` is the tab stop; a negative `tabIndex` would take it away.
      expect(link.getAttribute('href')).toBeTruthy();
      expect(link.getAttribute('tabindex')).toBeNull();
      expect(link.hasAttribute('disabled')).toBe(false);
    }
  });

  it('reserves the brand and account blocks at 56px, filled or not', () => {
    const { container } = renderSidebar({ accountMenu: null });
    const account = container.querySelector('[data-shell="sidebar-account"]');
    expect(account.style.height).toBe(`${SIDEBAR_FOOTER_HEIGHT_PX}px`);
    expect(SIDEBAR_BRAND_HEIGHT_PX).toBe(56);
    expect(SIDEBAR_FOOTER_HEIGHT_PX).toBe(56);

    // UPDATED BY TASK 8.8. This used to assert the fallback account card's sign-out
    // button, which existed only because between 8.6 and 8.8 nothing else in the tree
    // could log a trader out. `shell/AccountMenu` now owns sign-out (and the profile and
    // tier reads), `App.jsx` passes it into this slot, and the fallback is deleted — so an
    // unfilled slot is reserved and EMPTY, which is what this asserts. Sign-out's own
    // coverage moved to `tests/unit/shell/accountMenu.test.jsx`.
    expect(account.textContent).toBe('');
    expect(account.children).toHaveLength(0);
  });

  it('hands the account slot to whatever it is given, and reads nothing itself', () => {
    const { container } = renderSidebar({
      accountMenu: <button type="button">Account menu</button>,
    });
    const account = container.querySelector('[data-shell="sidebar-account"]');
    expect(within(account).getByRole('button', { name: 'Account menu' })).toBeTruthy();
    // Nothing of the sidebar's own is in there beside it: this component reads no user,
    // no entitlements and no notification count (task 8.8 moved all three out).
    expect(account.children).toHaveLength(1);
    // Same reserved height either way, so mounting the menu shifts nothing above it.
    expect(account.style.height).toBe('56px');
  });
});
