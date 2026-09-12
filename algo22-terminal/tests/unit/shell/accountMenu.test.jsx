/**
 * Unit tests for `src/components/shell/AccountMenu.jsx` — task 8.8.
 * Requirements 2.1, 18.1, 18.2, 18.3, 18.4, 19.4.
 *
 * WHAT THIS FILE HOLDS IN PLACE
 * -----------------------------
 * The defect this task closes is that task 8.6's ten-entry sidebar left seven working
 * routes reachable from nothing. So the first block below is not a rendering test, it is
 * the reachability claim: all seven, as real anchors, at their real paths.
 *
 * The rest are the traps this kind of component falls into:
 *
 *   1. `role="menu"` over links, which would announce away the link semantics the anchors
 *      exist for. The assertion is the absence of those roles.
 *   2. A `Documentation` row that is present and inert when `VITE_DOCS_URL` is unset —
 *      exactly what the old `docs` nav entry did (`window.open(undefined)`), and what
 *      Requirement 19.4 forbids.
 *   3. Sign-out quietly changing shape while being moved out of `Sidebar.jsx`. It is
 *      auth-adjacent, so its three side effects are asserted individually.
 *
 * `supabase` and `api` are doubles because the trigger reads the signed-in user and the
 * plan tier; `wsClient` is a double because the unread count subscribes to a socket frame
 * and the real client constructs a `WebSocket` on `connect()`. The overlay registry and
 * the unread store are module state, so both are reset between cases.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const { mockWsClient, mockApi, mockSupabase, mockNavigate } = vi.hoisted(() => {
  const channels = new Map();
  return {
    mockWsClient: {
      channels,
      status: 'connected',
      getStatus() {
        return this.status;
      },
      onStatusChange: () => () => {},
      connect: vi.fn(),
      subscribe: vi.fn((eventType, handler) => {
        if (!channels.has(eventType)) channels.set(eventType, new Set());
        channels.get(eventType).add(handler);
        return () => channels.get(eventType).delete(handler);
      }),
      emit(eventType, payload) {
        for (const handler of Array.from(channels.get(eventType) ?? [])) handler(payload);
      },
    },
    mockApi: {
      notifications: { getUnreadCount: vi.fn() },
      billing: { getEntitlements: vi.fn() },
    },
    mockSupabase: {
      auth: {
        getUser: vi.fn(),
        signOut: vi.fn(),
      },
    },
    mockNavigate: vi.fn(),
  };
});

vi.mock('../../../src/websocketClient', () => ({ default: mockWsClient }));
vi.mock('../../../src/api', () => ({ api: mockApi, default: mockApi }));
vi.mock('../../../src/supabase', () => ({ supabase: mockSupabase, default: mockSupabase }));
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

import AccountMenu, {
  ACCOUNT_MENU_PATHS,
  ACCOUNT_MENU_WIDTH_PX,
  popoverPosition,
  readDocsUrl,
} from '../../../src/components/shell/AccountMenu';
import Sidebar from '../../../src/components/Sidebar';
import { ResponsiveGate } from '../../../src/components/shell/ResponsiveGate';
import { SECONDARY_ROUTE_TITLES } from '../../../src/components/shell/navigation';
import { isOverlayOpen, resetOverlayRegistry } from '../../../src/components/ds/overlayRegistry';
import { resetUnreadNotifications } from '../../../src/hooks/useUnreadNotifications';

/** The seven routes §6.4 defers out of primary nav. The whole point of this component. */
const DEFERRED_ROUTES = Object.freeze([
  '/app/profile',
  '/app/security-logs',
  '/app/billing',
  '/app/exchange',
  '/app/risk',
  '/app/notifications',
  '/app/support',
]);

const ORIGINAL_INNER_WIDTH = window.innerWidth;

function setWidth(width) {
  Object.defineProperty(window, 'innerWidth', { value: width, configurable: true, writable: true });
}

beforeEach(() => {
  resetOverlayRegistry();
  resetUnreadNotifications();
  mockWsClient.channels.clear();
  mockWsClient.subscribe.mockClear();
  mockNavigate.mockReset();
  mockApi.notifications.getUnreadCount.mockReset();
  // The count is unknown unless a case says otherwise: the request never answers.
  mockApi.notifications.getUnreadCount.mockImplementation(() => new Promise(() => {}));
  mockApi.billing.getEntitlements.mockReset();
  mockApi.billing.getEntitlements.mockImplementation(() => new Promise(() => {}));
  mockSupabase.auth.getUser.mockReset();
  mockSupabase.auth.getUser.mockResolvedValue({ data: { user: null } });
  mockSupabase.auth.signOut.mockReset();
  mockSupabase.auth.signOut.mockResolvedValue({ error: null });
  vi.unstubAllEnvs();
});

afterEach(() => {
  cleanup();
  resetOverlayRegistry();
  resetUnreadNotifications();
  setWidth(ORIGINAL_INNER_WIDTH);
});

/** Mount the menu on its own, at a viewport width. */
function renderMenu({ width = 1440 } = {}) {
  setWidth(width);
  return render(
    <MemoryRouter initialEntries={['/app/dashboard']}>
      <ResponsiveGate pathname="/app/dashboard">
        <AccountMenu />
      </ResponsiveGate>
    </MemoryRouter>,
  );
}

const trigger = () => document.querySelector('[data-shell="account-menu-trigger"]');
const panel = () => document.querySelector('[data-shell="account-menu"]');
const rowFor = (path) => document.querySelector(`[data-account-menu-item="${path}"]`);

/** Open the panel and hand it back. `act` because the click drives two state updates. */
function openMenu() {
  act(() => trigger().click());
  return panel();
}

// ═══════════════════════════════════════════════════════════════════════════
// The reachability claim (Requirement 2.1, §6.4)
// ═══════════════════════════════════════════════════════════════════════════

describe('AccountMenu: the seven deferred routes are reachable', () => {
  it('links to every one of them, as a real anchor at its real path', () => {
    renderMenu();
    const open = openMenu();

    for (const path of DEFERRED_ROUTES) {
      const row = within(open).getByRole('link', { name: new RegExp(SECONDARY_ROUTE_TITLES[path], 'i') });
      // A real `href`, so middle-click, Ctrl-click and "copy link address" work — the
      // argument §6.3 makes for the sidebar, applied here.
      expect(row.getAttribute('href')).toBe(path);
      expect(row.tagName).toBe('A');
    }
  });

  it('declares exactly those seven and no more', () => {
    // The table, not the render: if a route is dropped from `MENU_GROUPS` it becomes
    // unreachable from anywhere in the app, which is the defect this task closes.
    expect([...ACCOUNT_MENU_PATHS]).toEqual(DEFERRED_ROUTES);
  });

  it('takes every label from the route table rather than restating it', () => {
    renderMenu();
    const open = openMenu();

    for (const path of DEFERRED_ROUTES) {
      expect(rowFor(path).textContent).toContain(SECONDARY_ROUTE_TITLES[path]);
    }
    // §6.4's groups, in its order.
    expect(within(open).getByText('Account')).toBeTruthy();
    expect(within(open).getByText('Trading setup')).toBeTruthy();
  });

  it('closes when a row is followed, so the popover is not left over the new page', () => {
    renderMenu();
    openMenu();
    act(() => rowFor('/app/profile').click());
    expect(panel()).toBeNull();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// The ARIA pattern: a disclosure over links, NOT role="menu"
// ═══════════════════════════════════════════════════════════════════════════

describe('AccountMenu: the disclosure pattern', () => {
  it('is a button that reports its expanded state and owns the panel', () => {
    renderMenu();
    expect(trigger().getAttribute('aria-expanded')).toBe('false');
    expect(trigger().getAttribute('aria-controls')).toBeNull();

    const open = openMenu();

    expect(trigger().getAttribute('aria-expanded')).toBe('true');
    expect(trigger().getAttribute('aria-controls')).toBe(open.getAttribute('id'));
  });

  it('claims no menu role over its links, and no aria-haspopup', () => {
    // `role="menuitem"` on an `<a href>` replaces the link role, so a screen reader stops
    // announcing "link" and the anchor leaves the links list — while the middle-click
    // behaviour these rows exist for stays. And `aria-haspopup="true"` is a synonym for
    // `"menu"`, which would promise the widget this deliberately is not.
    renderMenu();
    const open = openMenu();

    expect(trigger().getAttribute('aria-haspopup')).toBeNull();
    expect(open.querySelector('[role="menu"]')).toBeNull();
    expect(open.querySelector('[role="menuitem"]')).toBeNull();
    // The rows are announced as what they are.
    expect(within(open).getAllByRole('link').length).toBeGreaterThanOrEqual(7);
  });

  it('names the panel’s navigation landmark, and keeps sign out outside it', () => {
    renderMenu();
    const open = openMenu();

    const nav = within(open).getByRole('navigation', { name: 'Account' });
    // Sign out is an action, not a destination: no URL means "sign out", so it is a button
    // and it does not sit inside a navigation landmark.
    expect(within(nav).queryByRole('button', { name: /sign out/i })).toBeNull();
    expect(within(open).getByRole('button', { name: /sign out/i }).tagName).toBe('BUTTON');
  });

  it('gives the trigger an accessible name at both sidebar widths', () => {
    // Expanded: the name is composed from the content, so the visible identity is inside
    // it (WCAG 2.5.3) rather than replaced by an `aria-label`.
    renderMenu({ width: 1440 });
    expect(screen.getByRole('button', { name: /account and settings/i })).toBe(trigger());
    cleanup();

    // Rail (768–1023px): icon-only, so the name has to come from `aria-label` — the avatar
    // is decorative and a button with only decorative content has no name at all.
    renderMenu({ width: 800 });
    expect(trigger().getAttribute('aria-label')).toBe('Account and settings');
    expect(screen.getByRole('button', { name: 'Account and settings' })).toBe(trigger());
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Keyboard (Requirements 18.1, 18.3, §11.7)
// ═══════════════════════════════════════════════════════════════════════════

describe('AccountMenu: keyboard', () => {
  /** Dispatch a real key event, the way the document-level handlers see it. */
  function press(key, options = {}) {
    act(() => {
      document.activeElement.dispatchEvent(
        new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...options }),
      );
    });
  }

  it('closes on Escape and puts focus back on the trigger', () => {
    renderMenu();
    trigger().focus();
    openMenu();
    expect(panel()).not.toBeNull();

    press('Escape');

    expect(panel()).toBeNull();
    expect(document.activeElement).toBe(trigger());
  });

  it('traps focus inside the panel while it is open', () => {
    renderMenu();
    const open = openMenu();

    // The trap focuses the panel or its first row on open, and Tab is consumed either way.
    expect(open.contains(document.activeElement)).toBe(true);
    press('Tab');
    expect(open.contains(document.activeElement)).toBe(true);
    press('Tab', { shiftKey: true });
    expect(open.contains(document.activeElement)).toBe(true);
  });

  it('moves between rows on ArrowDown / ArrowUp, and wraps', () => {
    renderMenu();
    const open = openMenu();
    const rows = within(open).getAllByRole('link');
    const last = within(open).getByRole('button', { name: /sign out/i });

    rows[0].focus();
    press('ArrowDown');
    expect(document.activeElement).toBe(rows[1]);
    press('ArrowUp');
    expect(document.activeElement).toBe(rows[0]);
    // Wrapping backwards from the first row lands on the last control, which is sign out.
    press('ArrowUp');
    expect(document.activeElement).toBe(last);
  });

  it('jumps to the first and last row on Home / End', () => {
    renderMenu();
    const open = openMenu();
    const rows = within(open).getAllByRole('link');

    rows[2].focus();
    press('End');
    expect(document.activeElement).toBe(within(open).getByRole('button', { name: /sign out/i }));
    press('Home');
    expect(document.activeElement).toBe(rows[0]);
  });

  it('never lands on the scrim, which is a pointer affordance only', () => {
    renderMenu();
    const open = openMenu();
    const scrim = document.querySelector('[data-shell="account-menu-scrim"]');

    expect(scrim.getAttribute('tabindex')).toBe('-1');
    for (let i = 0; i < 12; i += 1) {
      press('ArrowDown');
      expect(document.activeElement).not.toBe(scrim);
    }
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Documentation: absent, not disabled (Requirement 19.4)
// ═══════════════════════════════════════════════════════════════════════════

describe('AccountMenu: the Documentation row', () => {
  it('is omitted entirely when the URL is not configured', () => {
    // The old `docs` nav entry ran `window.open(import.meta.env.VITE_DOCS_URL)` and did
    // nothing at all when that was unset — a control that took a click and had no effect.
    vi.stubEnv('VITE_DOCS_URL', '');
    renderMenu();
    const open = openMenu();

    expect(rowFor('docs')).toBeNull();
    expect(within(open).queryByText('Documentation')).toBeNull();
    // Not present-and-disabled: absent. A greyed row is still an answer.
    expect(open.querySelector('[aria-disabled="true"]')).toBeNull();
  });

  it('renders as an external link when it is configured', () => {
    vi.stubEnv('VITE_DOCS_URL', 'https://docs.example.com/terminal');
    renderMenu();
    const open = openMenu();

    const docs = within(open).getByRole('link', { name: /documentation/i });
    expect(docs.getAttribute('href')).toBe('https://docs.example.com/terminal');
    expect(docs.getAttribute('target')).toBe('_blank');
    // `noreferrer` as well as `noopener`: the opened page gets no `window.opener` handle
    // and no referrer for the terminal's own URL.
    expect(docs.getAttribute('rel')).toBe('noopener noreferrer');
    // A new tab opening unannounced is the thing this text prevents.
    expect(docs.textContent).toContain('(opens in a new tab)');
  });

  it('refuses anything that is not an absolute http(s) URL', () => {
    // A mis-set environment variable must not become a `javascript:` link in the shell,
    // and a relative value would resolve against the app's own origin and land on the
    // `/app/*` catch-all — a broken link with extra steps.
    for (const value of ['', '   ', 'undefined', '/docs', 'javascript:alert(1)', 'data:text/html,x']) {
      vi.stubEnv('VITE_DOCS_URL', value);
      expect(readDocsUrl()).toBeNull();
    }
    vi.stubEnv('VITE_DOCS_URL', ' https://docs.example.com ');
    expect(readDocsUrl()).toBe('https://docs.example.com/');
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Sign out — moved, not redesigned
// ═══════════════════════════════════════════════════════════════════════════

describe('AccountMenu: sign out', () => {
  it('performs the same three steps the sidebar’s card did, in the same order', async () => {
    sessionStorage.setItem('leftover', 'x');
    vi.stubEnv('VITE_SUPABASE_URL', 'https://project.supabase.co');
    localStorage.setItem('sb-https://project.supabase.co-auth-token', 'token');

    renderMenu();
    const open = openMenu();
    await act(async () => {
      within(open).getByRole('button', { name: /sign out/i }).click();
    });

    expect(mockSupabase.auth.signOut).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem('leftover')).toBeNull();
    expect(localStorage.getItem('sb-https://project.supabase.co-auth-token')).toBeNull();
    expect(mockNavigate).toHaveBeenCalledWith('/signin');
  });

  it('still clears the local session when the remote sign-out fails', async () => {
    // A failed remote sign-out must not strand the session locally — the behaviour the
    // sidebar's card had, kept verbatim.
    mockSupabase.auth.signOut.mockRejectedValue(new Error('offline'));
    sessionStorage.setItem('leftover', 'x');

    renderMenu();
    const open = openMenu();
    await act(async () => {
      within(open).getByRole('button', { name: /sign out/i }).click();
    });

    expect(sessionStorage.getItem('leftover')).toBeNull();
    expect(mockNavigate).toHaveBeenCalledWith('/signin');
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// The identity on the trigger states only what it read (Requirement 14.5)
// ═══════════════════════════════════════════════════════════════════════════

describe('AccountMenu: the trigger’s identity', () => {
  it('says "Signed in" rather than inventing a name', async () => {
    renderMenu();
    await waitFor(() => expect(mockSupabase.auth.getUser).toHaveBeenCalled());
    // The old card defaulted to "Quant Trader", which read as a fact about the account.
    expect(trigger().textContent).toContain('Signed in');
    expect(trigger().textContent).not.toMatch(/tier/i);
  });

  it('shows the plan only when the entitlements read answered', async () => {
    mockSupabase.auth.getUser.mockResolvedValue({
      data: { user: { email: 'rita@example.com', user_metadata: {} } },
    });
    mockApi.billing.getEntitlements.mockResolvedValue({ plan: { name: 'pro' } });

    renderMenu();

    await waitFor(() => expect(trigger().textContent).toContain('PRO TIER'));
    expect(trigger().textContent).toContain('rita');
  });

  it('states no plan at all when that read fails, rather than "FREE TIER"', async () => {
    mockSupabase.auth.getUser.mockResolvedValue({
      data: { user: { email: 'rita@example.com', user_metadata: {} } },
    });
    mockApi.billing.getEntitlements.mockRejectedValue(new Error('503'));

    renderMenu();

    await waitFor(() => expect(trigger().textContent).toContain('rita'));
    // That guess was wrong for every paying account whose read failed.
    expect(trigger().textContent).not.toMatch(/tier/i);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// The unread count is the bell's count (§6.4)
// ═══════════════════════════════════════════════════════════════════════════

describe('AccountMenu: the Notifications row', () => {
  it('claims no count until one comes back', () => {
    renderMenu();
    openMenu();

    const row = rowFor('/app/notifications');
    expect(row.getAttribute('data-account-menu-unread')).toBe('unknown');
    expect(row.getAttribute('aria-label')).toBe('Notifications');
    expect(row.textContent).toBe('Notifications');
  });

  it('shows the count, and words it the way the bell does', async () => {
    mockApi.notifications.getUnreadCount.mockResolvedValue({ unread_count: 3 });
    renderMenu();
    openMenu();

    await waitFor(() =>
      expect(rowFor('/app/notifications').getAttribute('data-account-menu-unread')).toBe('3'));
    expect(rowFor('/app/notifications').getAttribute('aria-label')).toBe('Notifications, 3 unread');
    expect(rowFor('/app/notifications').textContent).toContain('3');
  });

  it('announces a real zero without drawing a badge for it', async () => {
    mockApi.notifications.getUnreadCount.mockResolvedValue({ unread_count: 0 });
    renderMenu();
    openMenu();

    await waitFor(() =>
      expect(rowFor('/app/notifications').getAttribute('data-account-menu-unread')).toBe('0'));
    expect(rowFor('/app/notifications').getAttribute('aria-label')).toBe('Notifications, 0 unread');
    expect(rowFor('/app/notifications').textContent).toBe('Notifications');
  });

  it('marks the closed trigger when there is something unread', async () => {
    mockApi.notifications.getUnreadCount.mockResolvedValue({ unread_count: 2 });
    renderMenu();

    await waitFor(() =>
      expect(document.querySelector('[data-shell="account-menu-trigger-dot"]')).not.toBeNull());
    // A dot, not a figure: at the 56px rail there is no room for one, and the count itself
    // is a row inside the panel. The dot is `aria-hidden`, so the trigger's accessible name
    // — composed from its content here — claims no number at all.
    expect(document.querySelector('[data-shell="account-menu-trigger-dot"]')
      .closest('[aria-hidden="true"]')).not.toBeNull();
    expect(trigger().textContent).not.toMatch(/\d/);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Geometry and the overlay registry (Requirement 17.3)
// ═══════════════════════════════════════════════════════════════════════════

describe('AccountMenu: placement', () => {
  const viewport = { width: 1440, height: 900 };

  it('opens upward from the trigger, anchored to its left edge', () => {
    // The trigger sits at the bottom of the leftmost column, so the panel grows up and to
    // the right: `bottom` is measured from the trigger's top, `left` from its left.
    const { left, bottom, maxHeight } = popoverPosition({ top: 800, left: 0 }, viewport);
    expect(left).toBe(8);
    expect(bottom).toBe(900 - 800 + 6);
    expect(maxHeight).toBe(800 - 8 - 6);
  });

  it('cannot render off-screen at the 56px rail width', () => {
    // At `sidebarMode === 'rail'` the trigger's left edge IS the viewport's, and the panel
    // is wider than the rail — so the clamp is what keeps it on screen.
    const narrow = { width: 768, height: 1024 };
    const { left } = popoverPosition({ top: 968, left: 0 }, narrow);
    expect(left).toBeGreaterThanOrEqual(0);
    expect(left + ACCOUNT_MENU_WIDTH_PX).toBeLessThanOrEqual(narrow.width);
  });

  it('pulls back from the right edge rather than overflowing it', () => {
    const { left } = popoverPosition({ top: 800, left: 1430 }, viewport);
    expect(left + ACCOUNT_MENU_WIDTH_PX).toBeLessThanOrEqual(viewport.width - 8);
  });

  it('pins to the viewport when there is no room above, and never collapses', () => {
    // Includes the unmeasurable case: jsdom performs no layout, so every rect it hands
    // back is zeroes — which must produce a usable panel, not a 0px one.
    for (const rect of [{ top: 40, left: 0 }, null, { top: NaN, left: NaN }]) {
      const { bottom, maxHeight } = popoverPosition(rect, viewport);
      expect(bottom).toBe(8);
      expect(maxHeight).toBeGreaterThan(0);
    }
  });

  it('is positioned fixed in a portal, so the sidebar’s overflow cannot clip it', () => {
    renderMenu();
    const open = openMenu();

    expect(open.style.position).toBe('fixed');
    expect(open.style.width).toBe(`${ACCOUNT_MENU_WIDTH_PX}px`);
    // Not inside the sidebar column, which sets `overflow-hidden` in two places.
    expect(document.querySelector('[data-shell="sidebar"]')?.contains(open) ?? false).toBe(false);
  });
});

describe('AccountMenu: the overlay registry', () => {
  it('claims and releases the one overlay slot', () => {
    renderMenu();
    expect(isOverlayOpen()).toBe(false);

    openMenu();
    expect(isOverlayOpen()).toBe(true);

    act(() => trigger().click());
    expect(isOverlayOpen()).toBe(false);
    expect(panel()).toBeNull();
  });

  it('releases it on unmount, so a later overlay is not refused', () => {
    const view = renderMenu();
    openMenu();
    view.unmount();
    expect(isOverlayOpen()).toBe(false);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// In the slot it was built for
// ═══════════════════════════════════════════════════════════════════════════

describe('AccountMenu: inside the sidebar’s footer slot', () => {
  it('fills the reserved 56px block without changing it', () => {
    setWidth(1440);
    const { container } = render(
      <MemoryRouter initialEntries={['/app/dashboard']}>
        <ResponsiveGate pathname="/app/dashboard">
          <Sidebar accountMenu={<AccountMenu />} />
        </ResponsiveGate>
      </MemoryRouter>,
    );

    const slot = container.querySelector('[data-shell="sidebar-account"]');
    expect(slot.style.height).toBe('56px');
    expect(within(slot).getByRole('button', { name: /account and settings/i })).toBeTruthy();

    // The ten nav entries and the seven menu routes do not overlap: seventeen destinations,
    // no duplicates, which is what "the sidebar is ten entries AND nothing is orphaned"
    // means in one assertion.
    const navHrefs = within(screen.getByRole('navigation', { name: 'Main' }))
      .getAllByRole('link')
      .map((a) => a.getAttribute('href'));
    expect(navHrefs).toHaveLength(10);
    for (const path of ACCOUNT_MENU_PATHS) expect(navHrefs).not.toContain(path);
  });
});
