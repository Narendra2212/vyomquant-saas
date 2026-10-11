/**
 * Unit tests for the trader-controlled sidebar collapse.
 *
 * `src/store/useSidebarStore.js`, `sidebarModeFor`/`canCollapseAt` in
 * `src/components/shell/ResponsiveGate.jsx`, and the `CollapseToggle` row in
 * `src/components/Sidebar.jsx`.
 *
 * WHAT IS BEING HELD IN PLACE
 * ---------------------------
 *   1. The preference NARROWS ONLY. Below laptop width `ResponsiveGate` forces the 56px
 *      rail, and a stored `collapsed: false` must not be able to re-widen a tablet to
 *      216px — that is the one way this feature could break the viewport floor §11.6
 *      already decided, and it would do it silently on a real device.
 *   2. The control is a real keyboard-operable button with an accessible name at BOTH
 *      widths. At rail width it is icon-only, which is exactly where an unnamed control
 *      becomes unusable.
 *   3. Collapsing does not disturb navigation: ten anchors, the same hrefs, and the active
 *      route still marked.
 *   4. It survives a remount, which is the persistence that refresh and direct URL entry
 *      depend on.
 *   5. The toggle is NOT inside the `Main` nav landmark — `sidebar.test.jsx` asserts that
 *      landmark holds ten anchors and no buttons, and this is the test that says the new
 *      control knows it.
 *
 * HOW WIDTH IS DRIVEN HERE. Same arrangement as `sidebar.test.jsx` and
 * `responsiveGate.test.jsx`: `window.matchMedia` is `undefined` under jsdom 23.2.0 and
 * `vitest.config.js` sets `setupFiles: []`, so the gate falls back to `window.innerWidth`.
 */

import { describe, it, expect, afterEach, beforeEach } from 'vitest';
import { cleanup, render, screen, within, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import Sidebar, {
  SIDEBAR_TOGGLE_HEIGHT_PX,
  SIDEBAR_TOGGLE_LABELS,
} from '../../../src/components/Sidebar';
import {
  ResponsiveGate,
  SIDEBAR_WIDTH_PX,
  canCollapseAt,
  sidebarModeFor,
} from '../../../src/components/shell/ResponsiveGate';
import { NAV_ENTRIES } from '../../../src/components/shell/navigation';
import {
  SIDEBAR_STORAGE_KEY,
  getSidebarCollapsed,
  rehydrateSidebarCollapsed,
  resetSidebarCollapsed,
  setSidebarCollapsed,
} from '../../../src/store/useSidebarStore';

const ORIGINAL_INNER_WIDTH = window.innerWidth;

function setWidth(width) {
  Object.defineProperty(window, 'innerWidth', { value: width, configurable: true, writable: true });
}

beforeEach(() => {
  // Module state outlives `cleanup()`, so each test starts from the default.
  act(() => resetSidebarCollapsed());
});

afterEach(() => {
  cleanup();
  act(() => resetSidebarCollapsed());
  setWidth(ORIGINAL_INNER_WIDTH);
});

function renderSidebar({ pathname = '/app/dashboard', width = 1440 } = {}) {
  setWidth(width);
  return render(
    <MemoryRouter initialEntries={[pathname]}>
      <ResponsiveGate pathname={pathname}>
        <Sidebar accountMenu={<span />} />
      </ResponsiveGate>
    </MemoryRouter>,
  );
}

const aside = () => document.querySelector('[data-shell="sidebar"]');
const toggleRow = () => document.querySelector('[data-shell="sidebar-toggle"]');
const toggle = () => screen.queryByRole('button', { name: /sidebar$/i });

function navLinks() {
  return within(screen.getByRole('navigation', { name: 'Main' })).getAllByRole('link');
}

// ═══════════════════════════════════════════════════════════════════════════
// The pure decision: the preference narrows, the viewport floor wins
// ═══════════════════════════════════════════════════════════════════════════

describe('sidebarModeFor: the preference can only narrow', () => {
  it('is unchanged as a one-argument viewport lookup', () => {
    // The signature this function had before the toggle existed. `responsiveGate.test.jsx`
    // calls it this way and a trader who has never touched the control gets this answer.
    expect(sidebarModeFor('TABLET')).toBe('rail');
    expect(sidebarModeFor('LAPTOP')).toBe('expanded');
    expect(sidebarModeFor('DESKTOP')).toBe('expanded');
  });

  it('collapses laptop and desktop when asked', () => {
    expect(sidebarModeFor('LAPTOP', true)).toBe('rail');
    expect(sidebarModeFor('DESKTOP', true)).toBe('rail');
  });

  it('cannot re-widen a viewport below the laptop breakpoint', () => {
    // THE IMPORTANT ONE. `collapsed: false` is the DEFAULT, so if the preference were
    // treated as a vote rather than a floor, every tablet would get a 216px sidebar.
    for (const collapsed of [false, true]) {
      expect(sidebarModeFor('TABLET', collapsed)).toBe('rail');
      expect(sidebarModeFor('BELOW', collapsed)).toBe('rail');
    }
  });

  it('treats an unrecognised viewport as the narrow end, either way', () => {
    expect(sidebarModeFor(undefined, false)).toBe('rail');
    expect(sidebarModeFor('nonsense', false)).toBe('rail');
  });
});

describe('canCollapseAt', () => {
  it('is true only where the control could change something', () => {
    expect(canCollapseAt('DESKTOP')).toBe(true);
    expect(canCollapseAt('LAPTOP')).toBe(true);
    expect(canCollapseAt('TABLET')).toBe(false);
    expect(canCollapseAt('BELOW')).toBe(false);
    expect(canCollapseAt(undefined)).toBe(false);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// The store
// ═══════════════════════════════════════════════════════════════════════════

describe('useSidebarStore', () => {
  it('defaults to expanded', () => {
    expect(getSidebarCollapsed()).toBe(false);
  });

  it('persists the preference under its own key', () => {
    act(() => setSidebarCollapsed(true));
    expect(getSidebarCollapsed()).toBe(true);
    expect(window.localStorage.getItem(SIDEBAR_STORAGE_KEY)).toBe('1');
  });

  it('does not share a key with the workspace layout store', () => {
    // `useWorkspaceStore.resetWorkspace()` clears `aerora-workspace-storage` wholesale to
    // restore the default panel grid; that must not reopen the sidebar.
    expect(SIDEBAR_STORAGE_KEY).not.toBe('aerora-workspace-storage');
    act(() => setSidebarCollapsed(true));
    window.localStorage.removeItem('aerora-workspace-storage');
    expect(getSidebarCollapsed()).toBe(true);
  });

  /*
   * THESE GO THROUGH `rehydrateSidebarCollapsed`, NOT `resetSidebarCollapsed`.
   *
   * The earlier version of this block wrote a value into localStorage, called
   * `resetSidebarCollapsed()` and asserted `false`. That assertion could not fail:
   * `resetSidebarCollapsed` ASSIGNS the default and never consults storage, so the test
   * passed regardless of what the parser did — and the parser was therefore untested.
   *
   * `readPersisted()` runs once per module load, so the only honest way to exercise it in
   * jsdom is a seam that re-runs it. That is what `rehydrateSidebarCollapsed()` is, and it
   * is also the nearest thing to the real event this is about: a page reload.
   */
  it('reads the collapsed sentinel back out of storage, which is what survives a reload', () => {
    window.localStorage.setItem(SIDEBAR_STORAGE_KEY, '1');
    expect(rehydrateSidebarCollapsed()).toBe(true);
    expect(getSidebarCollapsed()).toBe(true);
  });

  it('reads an absent key as expanded', () => {
    window.localStorage.removeItem(SIDEBAR_STORAGE_KEY);
    expect(rehydrateSidebarCollapsed()).toBe(false);
  });

  it.each(['yes-please', '0', 'true', '', 'null', '11', ' 1'])(
    'coerces the hand-edited value %p to the expanded default',
    (stored) => {
      // The key is localStorage: a trader can write anything into it, and a non-boolean
      // reaching the context would be indexed straight into `SIDEBAR_WIDTH_PX`. Only the
      // exact sentinel counts as collapsed. `'true'` and `'11'` are the interesting rows —
      // a truthiness check or a `startsWith`/`includes` would let both through.
      window.localStorage.setItem(SIDEBAR_STORAGE_KEY, stored);
      expect(rehydrateSidebarCollapsed()).toBe(false);
      expect(getSidebarCollapsed()).toBe(false);
    },
  );

  it('round-trips a write through storage back into module state', () => {
    // The two halves together: `setSidebarCollapsed` writes the sentinel, and a fresh read
    // of that same key returns `true`. Neither half is trusted on its own.
    act(() => setSidebarCollapsed(true));
    expect(window.localStorage.getItem(SIDEBAR_STORAGE_KEY)).toBe('1');
    act(() => resetSidebarCollapsed());
    expect(getSidebarCollapsed()).toBe(false);

    act(() => setSidebarCollapsed(true));
    expect(rehydrateSidebarCollapsed()).toBe(true);
  });

  it('survives localStorage throwing on read, which is Safari private mode', () => {
    // `localStorage` can throw on ACCESS, not merely on `getItem`, so the whole expression
    // is inside the `try`. A shell that cannot read a cosmetic preference must boot, not
    // crash — this is the one failure mode that would take the authenticated app down.
    const real = window.localStorage;
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() {
        throw new DOMException('denied', 'SecurityError');
      },
    });
    try {
      expect(rehydrateSidebarCollapsed()).toBe(false);
      // And a WRITE must not propagate either.
      expect(() => act(() => setSidebarCollapsed(true))).not.toThrow();
      // The in-memory preference still applied, so the sidebar still collapses this session.
      expect(getSidebarCollapsed()).toBe(true);
    } finally {
      Object.defineProperty(window, 'localStorage', { configurable: true, value: real });
    }
  });

  it('renders the shell with storage unavailable', () => {
    const real = window.localStorage;
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() {
        throw new DOMException('denied', 'SecurityError');
      },
    });
    try {
      expect(() => renderSidebar()).not.toThrow();
      expect(aside()).not.toBeNull();
      expect(toggle()).not.toBeNull();
    } finally {
      Object.defineProperty(window, 'localStorage', { configurable: true, value: real });
    }
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// The control
// ═══════════════════════════════════════════════════════════════════════════

describe('Sidebar: the collapse control', () => {
  it('is a real button, in the tab order, outside the Main nav landmark', () => {
    renderSidebar();
    const button = toggle();

    expect(button).not.toBeNull();
    expect(button.tagName).toBe('BUTTON');
    expect(button.getAttribute('type')).toBe('button');
    // Not removed from the tab order.
    expect(button.getAttribute('tabindex')).toBeNull();
    // And NOT a navigation destination — `sidebar.test.jsx` asserts the nav holds no
    // buttons, and this is the other side of that contract.
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(nav.contains(button)).toBe(false);
    expect(within(nav).queryAllByRole('button')).toEqual([]);
  });

  it('names the action and reports the state separately', () => {
    renderSidebar();

    // The NAME is what pressing it will do; the STATE is `aria-expanded`.
    expect(toggle().getAttribute('aria-label')).toBe(SIDEBAR_TOGGLE_LABELS.collapse);
    expect(toggle().getAttribute('aria-expanded')).toBe('true');
    // And it points at the element that actually changes shape.
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(toggle().getAttribute('aria-controls')).toBe(nav.getAttribute('id'));
    expect(nav.getAttribute('id')).toBeTruthy();
  });

  it('collapses the track to the rail width on click, and back', async () => {
    const user = userEvent.setup();
    renderSidebar({ width: 1440 });

    expect(aside().getAttribute('data-sidebar-mode')).toBe('expanded');
    expect(aside().style.width).toBe(`${SIDEBAR_WIDTH_PX.expanded}px`);

    await user.click(toggle());

    expect(aside().getAttribute('data-sidebar-mode')).toBe('rail');
    expect(aside().style.width).toBe(`${SIDEBAR_WIDTH_PX.rail}px`);
    // The width still cannot be stretched by content (Requirement 2.2, 17.1).
    expect(aside().style.maxWidth).toBe(`${SIDEBAR_WIDTH_PX.rail}px`);

    await user.click(toggle());

    expect(aside().getAttribute('data-sidebar-mode')).toBe('expanded');
    expect(aside().style.width).toBe(`${SIDEBAR_WIDTH_PX.expanded}px`);
  });

  it('swaps the name and the state when collapsed, and keeps a name at 56px', async () => {
    const user = userEvent.setup();
    renderSidebar();
    await user.click(toggle());

    // Icon-only at this width — so the accessible name is the ONLY name it has.
    expect(toggle().getAttribute('aria-label')).toBe(SIDEBAR_TOGGLE_LABELS.expand);
    expect(toggle().getAttribute('aria-expanded')).toBe('false');
    expect(toggle().getAttribute('title')).toBe(SIDEBAR_TOGGLE_LABELS.expand);
    expect(screen.getByRole('button', { name: SIDEBAR_TOGGLE_LABELS.expand })).toBe(toggle());
  });

  it('is operable from the keyboard with Enter and Space', async () => {
    const user = userEvent.setup();
    renderSidebar();

    toggle().focus();
    expect(document.activeElement).toBe(toggle());

    await user.keyboard('{Enter}');
    expect(aside().getAttribute('data-sidebar-mode')).toBe('rail');

    // Focus survives the collapse, so the control can be pressed again without re-finding
    // it — the button is not remounted, only relabelled.
    expect(document.activeElement).toBe(toggle());
    await user.keyboard(' ');
    expect(aside().getAttribute('data-sidebar-mode')).toBe('expanded');
  });

  it('reserves the same row height in both states, so the nav does not move', async () => {
    const user = userEvent.setup();
    renderSidebar();

    expect(toggleRow().style.height).toBe(`${SIDEBAR_TOGGLE_HEIGHT_PX}px`);
    await user.click(toggle());
    expect(toggleRow().style.height).toBe(`${SIDEBAR_TOGGLE_HEIGHT_PX}px`);
  });

  it('is not rendered below laptop width, where it could not change anything', () => {
    renderSidebar({ width: 800 });

    // The gate forces the rail at tablet width, so a control offering to expand would be
    // a control that does nothing (Requirement 19.4).
    expect(aside().getAttribute('data-sidebar-mode')).toBe('rail');
    expect(toggle()).toBeNull();
    expect(toggleRow()).toBeNull();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Navigation is unaffected
// ═══════════════════════════════════════════════════════════════════════════

describe('Sidebar: collapsing does not disturb navigation', () => {
  it('keeps all ten entries, their hrefs and their accessible names', async () => {
    const user = userEvent.setup();
    renderSidebar();
    await user.click(toggle());

    const links = navLinks();
    expect(links).toHaveLength(NAV_ENTRIES.length);
    expect(links.map((a) => a.getAttribute('href'))).toEqual(NAV_ENTRIES.map((e) => e.path));

    // Requirement 2.4 at the collapsed width the trader chose, not just the one the
    // viewport imposed: every icon-only entry still announces its label.
    for (const entry of NAV_ENTRIES) {
      expect(screen.getByRole('link', { name: entry.label }).getAttribute('data-nav-id'))
        .toBe(entry.id);
      expect(screen.getByRole('link', { name: entry.label }).getAttribute('title'))
        .toBe(entry.label);
    }
  });

  it('still marks the active route, including an alias and a child route', async () => {
    const user = userEvent.setup();

    for (const [pathname, expectedId] of [
      ['/app/portfolio', 'portfolio'],
      ['/app/backtester', 'backtest'],
      ['/app/strategies/abc-123', 'strategies'],
    ]) {
      const view = renderSidebar({ pathname });
      await user.click(toggle());

      const current = navLinks()
        .filter((a) => a.getAttribute('aria-current') === 'page')
        .map((a) => a.getAttribute('data-nav-id'));
      expect(current).toEqual([expectedId]);

      view.unmount();
      act(() => resetSidebarCollapsed());
    }
  });

  it('keeps the group headings announced while collapsed', async () => {
    const user = userEvent.setup();
    renderSidebar();
    await user.click(toggle());

    for (const label of ['Monitor', 'Build & Test', 'Review', 'Practise & Discover']) {
      expect(screen.getByRole('heading', { name: label }).className).toContain('sr-only');
      expect(screen.getByRole('list', { name: label })).toBeTruthy();
    }
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Persistence — refresh and direct URL entry
// ═══════════════════════════════════════════════════════════════════════════

describe('Sidebar: the preference survives a remount', () => {
  it('restores the collapsed track on a fresh mount at a different route', async () => {
    const user = userEvent.setup();
    const first = renderSidebar({ pathname: '/app/dashboard' });
    await user.click(toggle());
    expect(aside().getAttribute('data-sidebar-mode')).toBe('rail');

    first.unmount();

    // A remount is what a refresh and a direct URL entry both look like to this component.
    renderSidebar({ pathname: '/app/trades' });
    expect(aside().getAttribute('data-sidebar-mode')).toBe('rail');
    expect(toggle().getAttribute('aria-expanded')).toBe('false');
  });

  it('honours a stored preference that was never set in this session', () => {
    act(() => setSidebarCollapsed(true));
    renderSidebar();
    expect(aside().getAttribute('data-sidebar-mode')).toBe('rail');
  });

  it('does not let a stored preference widen a tablet on reload', () => {
    // Stored "expanded" plus a tablet viewport: the floor has to win, or a 768px device
    // gets a 216px sidebar and a 552px content column.
    act(() => setSidebarCollapsed(false));
    renderSidebar({ width: 800 });
    expect(aside().style.width).toBe(`${SIDEBAR_WIDTH_PX.rail}px`);
  });
});
