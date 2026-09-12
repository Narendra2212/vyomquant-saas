/**
 * Unit tests for `src/components/TopBar.jsx` and
 * `src/components/shell/ConnectionStatusIndicator.jsx` — task 8.7.
 * Requirements 2.5, 2.6, 14.5, 16.2, 19.4.
 *
 * WHAT THIS FILE HOLDS IN PLACE
 * -----------------------------
 * Every example here is a defect this task removed, not a hypothetical:
 *
 *   1. The bar rendered `<LiveStatusV2 status="running" />` — a literal, so the light
 *      claimed the engine was live over a dead socket (§1.3, Requirement 14.5). The
 *      indicator must now follow `wsClient` for every status word, and must never say
 *      "LIVE" for any of them.
 *   2. There was no disconnected strip at all, so a stale figure read as current. It must
 *      appear for exactly the statuses that mean "down", carry the consequence and a
 *      retry, and not be dismissable.
 *   3. `unreadCount` and `userInitial` were initialised to `0` and `"Q"` — two values
 *      rendered before anything had been read and kept after a failed read.
 *
 * Scope note: shell geometry across route changes is task 8.9's Property 2, and
 * `tests/unit/shell/connectionStatus.test.jsx` (task 8.10) owns the mocked-`wsClient`
 * integration pass. This is the by-example half.
 *
 * `wsClient` is a double: the real one constructs a `WebSocket` on `connect()`. `api` is
 * a double because the bell's count is a request, and what is under test is what the bar
 * does with the answer — including not having one.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const { mockWsClient, mockApi } = vi.hoisted(() => {
  const listeners = new Set();
  const channels = new Map();
  const client = {
    status: 'disconnected',
    acquiredPath: null,
    listeners,
    channels,
    connect: vi.fn(),
    getStatus: () => client.status,
    onStatusChange: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    subscribe: vi.fn((eventType, handler) => {
      if (!channels.has(eventType)) channels.set(eventType, new Set());
      channels.get(eventType).add(handler);
      return () => channels.get(eventType).delete(handler);
    }),
    /** Drive a transition the way `_setStatus` does: set, then notify synchronously. */
    transitionTo(next) {
      client.status = next;
      for (const listener of Array.from(listeners)) listener(next);
    },
    /** Deliver a frame the way `handleMessage` does. */
    emit(eventType, payload) {
      for (const handler of Array.from(channels.get(eventType) ?? [])) handler(payload);
    },
  };
  return {
    mockWsClient: client,
    mockApi: { notifications: { getUnreadCount: vi.fn() } },
  };
});

vi.mock('../../../src/websocketClient', () => ({ default: mockWsClient }));
vi.mock('../../../src/api', () => ({ api: mockApi, default: mockApi }));

import TopBar, { TOPBAR_HEIGHT_PX } from '../../../src/components/TopBar';
import { SIDEBAR_BRAND_HEIGHT_PX } from '../../../src/components/Sidebar';
import {
  CONNECTION_DOWN_COPY,
  CONNECTION_LABELS,
  connectionPresentation,
  isConnectionDown,
} from '../../../src/components/shell/ConnectionStatusIndicator';

beforeEach(() => {
  mockWsClient.status = 'disconnected';
  mockWsClient.acquiredPath = null;
  mockWsClient.listeners.clear();
  mockWsClient.channels.clear();
  mockWsClient.connect.mockReset();
  mockWsClient.subscribe.mockClear();
  mockApi.notifications.getUnreadCount.mockReset();
  // The default for tests that are not about the count: the request never answers, so
  // the count stays unknown and no badge is claimed.
  mockApi.notifications.getUnreadCount.mockImplementation(() => new Promise(() => {}));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

/** Mount the bar at `pathname` with the socket in `status`. */
function renderTopBar({ pathname = '/app/dashboard', status = 'connected' } = {}) {
  mockWsClient.status = status;
  return render(
    <MemoryRouter initialEntries={[pathname]}>
      <TopBar />
    </MemoryRouter>,
  );
}

const indicator = () => document.querySelector('[data-shell="connection-indicator"]');
const strip = () => document.querySelector('[data-shell="connection-strip"]');
const barRow = () => document.querySelector('[data-shell="topbar-bar"]');

// ═══════════════════════════════════════════════════════════════════════════
// The indicator reads the client, not a literal (Requirements 2.5, 14.5)
// ═══════════════════════════════════════════════════════════════════════════

describe('TopBar: the connection indicator is real', () => {
  it.each([
    ['connected', 'Connected'],
    ['connecting', 'Connecting'],
    ['reconnecting', 'Reconnecting'],
    ['disconnected', 'Disconnected'],
    ['error', 'Connection error'],
    ['failed', 'Connection failed'],
  ])('renders %p as %p, from wsClient', (status, label) => {
    renderTopBar({ status });

    expect(indicator()).not.toBeNull();
    expect(indicator().getAttribute('data-connection-status')).toBe(status);
    expect(indicator().textContent).toContain(label);
  });

  it('renders a status word this build has never seen as itself, neutrally', () => {
    renderTopBar({ status: 'quiesced' });

    // §6.5: an unrecognised status renders neutral with the raw value as its label. The
    // old component defaulted an unknown status to `stopped` and painted it loss red.
    expect(indicator().textContent).toContain('quiesced');
    expect(indicator().querySelector('[data-status-group]').getAttribute('data-status-group'))
      .toBe('neutral');
    expect(strip()).toBeNull();
  });

  it('renders an unreadable status as Unknown rather than guessing either way', () => {
    // Set directly rather than through `renderTopBar`'s parameter default, which would
    // substitute `'connected'` for `undefined`.
    renderTopBar();
    act(() => mockWsClient.transitionTo(undefined));

    expect(indicator().getAttribute('data-connection-status')).toBe('unknown');
    expect(indicator().textContent).toContain('Unknown');
    // Not evidence that data is stale — only that we cannot say it is fresh.
    expect(strip()).toBeNull();
  });

  it('never says LIVE for any status websocketClient reports', () => {
    // The exact regression: `LiveStatusV2`'s `running` config labelled itself LIVE, and
    // this bar passed `running` unconditionally. `connected` is the interesting row — the
    // old label for an open socket was LIVE, which conflated "we are receiving frames"
    // with "a strategy is trading", on Paper Trading as much as anywhere.
    //
    // Asserted on the indicator, not the whole bar: the disconnected strip legitimately
    // contains "Live positions, orders and P&L below may be out of date", which is the
    // opposite of a claim.
    renderTopBar({ status: 'connected' });

    for (const status of [
      'connected', 'connecting', 'reconnecting', 'disconnected', 'error', 'failed',
      'anything_at_all', 42, null,
    ]) {
      act(() => mockWsClient.transitionTo(status));
      expect(indicator().textContent).not.toMatch(/\bLIVE\b/i);
    }
  });

  it('echoes a word it does not know rather than translating it', () => {
    // The label map covers the six words `_setStatus` uses. Anything else is the
    // client's own report, printed verbatim (§6.5) — the component has no branch that
    // can INTRODUCE a status word, which is the property that matters here. If the
    // transport ever named a state `live`, that would be the transport saying so.
    renderTopBar({ status: 'quiescing' });
    expect(indicator().textContent).toContain('quiescing');
    expect(Object.keys(CONNECTION_LABELS)).not.toContain('quiescing');
  });

  it('reflects a transition with the clock frozen — the path is push, not poll', () => {
    vi.useFakeTimers();
    renderTopBar({ status: 'connected' });
    expect(indicator().textContent).toContain('Connected');

    act(() => {
      mockWsClient.transitionTo('disconnected');
    });

    // Requirement 2.6's 5s bound, met at zero elapsed time. A polled indicator could not
    // pass this line.
    expect(indicator().textContent).toContain('Disconnected');
  });

  it('carries no injected keyframes and no pulsing dot', () => {
    // `LiveStatus` and `LiveStatusV2` both injected a `<style>` element with @keyframes
    // and animated the dot. Requirement 1.5 retires that.
    renderTopBar({ status: 'connected' });
    expect(document.querySelectorAll('style').length).toBe(0);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// The disconnected strip (Requirements 14.5, 16.2, §6.5)
// ═══════════════════════════════════════════════════════════════════════════

describe('TopBar: the disconnected strip', () => {
  it.each(['disconnected', 'error', 'failed'])('appears on %p', (status) => {
    renderTopBar({ status });

    const band = strip();
    expect(band).not.toBeNull();
    expect(band.textContent).toContain(CONNECTION_DOWN_COPY.title);
    // The consequence, not just the mechanism. This sentence is the whole point of the
    // strip: a trader who reads "disconnected" has been told what broke, not what it
    // means for the figures on screen.
    expect(band.textContent).toContain(CONNECTION_DOWN_COPY.detail);
  });

  it.each(['connected', 'connecting', 'reconnecting'])('does not appear on %p', (status) => {
    renderTopBar({ status });

    // `reconnecting` is the interesting one: self-healing, so indicator only — a band for
    // it would be the noise Requirement 16.2 forbids.
    expect(strip()).toBeNull();
    expect(indicator()).not.toBeNull();
  });

  it('appears and disappears with the socket, without a remount', () => {
    renderTopBar({ status: 'connected' });
    expect(strip()).toBeNull();

    act(() => mockWsClient.transitionTo('disconnected'));
    expect(strip()).not.toBeNull();

    act(() => mockWsClient.transitionTo('connecting'));
    expect(strip()).toBeNull();
  });

  it('is assertive and not dismissable', () => {
    renderTopBar({ status: 'disconnected' });

    // `role="alert"` — the figures being read may no longer be current, which is worth
    // interrupting for.
    expect(strip().getAttribute('role')).toBe('alert');
    // No close button: the trader cannot make the statement untrue, and dismissing it
    // would put the silence back.
    expect(strip().querySelector('[data-ds="alert-dismiss"]')).toBeNull();
  });

  it('offers a retry that reconnects the socket', () => {
    renderTopBar({ status: 'error' });

    const retry = within(strip()).getByRole('button', { name: CONNECTION_DOWN_COPY.retryLabel });
    retry.click();

    expect(mockWsClient.connect).toHaveBeenCalledTimes(1);
    expect(mockWsClient.connect).toHaveBeenCalledWith(undefined);
  });

  it('retries on the path the session acquired, not the default one', () => {
    mockWsClient.acquiredPath = '/ws/builder';
    renderTopBar({ status: 'disconnected' });

    within(strip()).getByRole('button', { name: CONNECTION_DOWN_COPY.retryLabel }).click();

    expect(mockWsClient.connect).toHaveBeenCalledWith('/ws/builder');
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// `connectionPresentation` — the pure decision both of the above read
// ═══════════════════════════════════════════════════════════════════════════

describe('connectionPresentation', () => {
  it('is total, and never labels an unknown value as a claim', () => {
    for (const input of [undefined, null, '', '   ', 42, {}, [], 'nonsense']) {
      const { label, down } = connectionPresentation(input);
      expect(typeof label).toBe('string');
      expect(label.length).toBeGreaterThan(0);
      expect(label).not.toMatch(/\bLIVE\b/i);
      expect(down).toBe(false);
    }
  });

  it('treats every error-group status as down, including ones not in the label map', () => {
    // `down` is `statusToken`'s group, not a hand-kept list — which is how `failed` (the
    // status the client sets after it gives up reconnecting, the stalest state there is)
    // gets a strip without anyone remembering to add it.
    for (const status of ['disconnected', 'error', 'failed', 'closed', 'stale']) {
      expect(isConnectionDown(status)).toBe(true);
    }
    for (const status of ['connected', 'connecting', 'reconnecting', 'idle', 'unheard-of']) {
      expect(isConnectionDown(status)).toBe(false);
    }
  });

  it('is case- and whitespace-insensitive about the client’s spelling', () => {
    expect(connectionPresentation('  DISCONNECTED ').label).toBe(CONNECTION_LABELS.disconnected);
    expect(isConnectionDown(' Error ')).toBe(true);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Page context comes from the navigation table (§6.3)
// ═══════════════════════════════════════════════════════════════════════════

describe('TopBar: page context', () => {
  const trail = () => screen.queryByRole('navigation', { name: 'Page context' });

  it('names the group and the page for a nav route', () => {
    renderTopBar({ pathname: '/app/dashboard' });

    expect(trail().textContent).toContain('Monitor');
    expect(within(trail()).getByText('Dashboard')).toHaveProperty('tagName', 'SPAN');
  });

  it('resolves a child route to its parent entry', () => {
    // `/app/strategies/abc-123` highlighted nothing in the old shell (§1.10).
    renderTopBar({ pathname: '/app/strategies/abc-123' });

    expect(trail().textContent).toContain('Build & Test');
    expect(trail().textContent).toContain('Strategies');
  });

  it('resolves an alias route', () => {
    renderTopBar({ pathname: '/app/backtester' });
    expect(trail().textContent).toContain('Backtester');
  });

  it('titles a secondary route that has no nav entry', () => {
    renderTopBar({ pathname: '/app/billing' });
    expect(trail().textContent).toContain('Billing & plan');
  });

  it('renders no trail at all for a route the table does not know', () => {
    // Rather than a guessed heading for a path `App.jsx` is about to redirect away.
    renderTopBar({ pathname: '/app/not-a-route' });
    expect(trail()).toBeNull();
  });

  it('does not name its landmark "Breadcrumb", which ds/PageHeader already uses', () => {
    renderTopBar({ pathname: '/app/dashboard' });
    expect(screen.queryByRole('navigation', { name: 'Breadcrumb' })).toBeNull();
  });

  it('marks only the last crumb as the current page, and links neither', () => {
    renderTopBar({ pathname: '/app/portfolio' });

    const current = trail().querySelectorAll('[aria-current="page"]');
    expect(current).toHaveLength(1);
    expect(current[0].textContent).toBe('Portfolio');
    // A group has no route and the current page needs no link to itself (Requirement 19.4).
    expect(within(trail()).queryAllByRole('link')).toHaveLength(0);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// The notification bell states what it read, and nothing more (Requirement 14.5)
// ═══════════════════════════════════════════════════════════════════════════

describe('TopBar: the notification bell', () => {
  const bell = () => screen.getByRole('link', { name: /notifications/i });

  it('navigates as a real link, so middle-click and Ctrl-click work', () => {
    renderTopBar();
    expect(bell().getAttribute('href')).toBe('/app/notifications');
  });

  it('claims no count before the request answers', () => {
    renderTopBar();

    expect(bell().getAttribute('data-unread')).toBe('unknown');
    expect(bell().getAttribute('aria-label')).toBe('Notifications');
    // The old bar said "View notifications (0 unread)" from the first frame.
    expect(bell().textContent).toBe('');
  });

  it('shows a count once one comes back', async () => {
    mockApi.notifications.getUnreadCount.mockResolvedValue({ unread_count: 3 });
    renderTopBar();

    await waitFor(() => expect(bell().getAttribute('data-unread')).toBe('3'));
    expect(bell().getAttribute('aria-label')).toBe('Notifications, 3 unread');
    expect(bell().textContent).toBe('3');
  });

  it('renders no badge for a real zero, and says so in the name', async () => {
    mockApi.notifications.getUnreadCount.mockResolvedValue({ unread_count: 0 });
    renderTopBar();

    await waitFor(() => expect(bell().getAttribute('data-unread')).toBe('0'));
    expect(bell().getAttribute('aria-label')).toBe('Notifications, 0 unread');
    expect(bell().textContent).toBe('');
  });

  it('stays unknown when the read fails, instead of falling back to zero', async () => {
    mockApi.notifications.getUnreadCount.mockRejectedValue(new Error('503'));
    renderTopBar();

    await waitFor(() => expect(mockApi.notifications.getUnreadCount).toHaveBeenCalled());
    expect(bell().getAttribute('data-unread')).toBe('unknown');
    expect(bell().getAttribute('aria-label')).toBe('Notifications');
  });

  it('increments a known count on a pushed notification', async () => {
    mockApi.notifications.getUnreadCount.mockResolvedValue({ unread_count: 1 });
    renderTopBar();
    await waitFor(() => expect(bell().getAttribute('data-unread')).toBe('1'));

    act(() => mockWsClient.emit('notification', { type: 'notification', data: {} }));

    expect(bell().getAttribute('data-unread')).toBe('2');
  });

  it('does not invent a base count when a notification arrives while unknown', () => {
    renderTopBar();
    expect(bell().getAttribute('data-unread')).toBe('unknown');

    act(() => mockWsClient.emit('notification', { type: 'notification', data: {} }));

    // `null + 1` would have been a fabricated 1.
    expect(bell().getAttribute('data-unread')).toBe('unknown');
  });

  it('caps the badge text but not the accessible name', async () => {
    mockApi.notifications.getUnreadCount.mockResolvedValue({ unread_count: 128 });
    renderTopBar();

    await waitFor(() => expect(bell().textContent).toBe('99+'));
    expect(bell().getAttribute('aria-label')).toBe('Notifications, 128 unread');
  });

  it('drops its socket subscription on unmount', () => {
    const view = renderTopBar();
    expect(mockWsClient.channels.get('notification').size).toBe(1);

    view.unmount();

    expect(mockWsClient.channels.get('notification').size).toBe(0);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// What is no longer here (Requirement 19.4, §6.1)
// ═══════════════════════════════════════════════════════════════════════════

describe('TopBar: the bar holds §6.1’s four regions and nothing else', () => {
  it('has no profile control — §6.4 puts Profile in the sidebar’s account menu', () => {
    renderTopBar();

    expect(screen.queryByRole('link', { name: /profile/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /profile/i })).toBeNull();
    expect(document.querySelector('[href="/app/profile"]')).toBeNull();
  });

  it('has exactly one interactive control while connected: the bell', () => {
    renderTopBar({ status: 'connected' });

    const links = within(barRow()).getAllByRole('link');
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute('href')).toBe('/app/notifications');
    expect(within(barRow()).queryAllByRole('button')).toHaveLength(0);
  });

  it('renders a UTC clock that ticks', () => {
    vi.useFakeTimers({ now: new Date('2024-05-01T09:15:30Z') });
    renderTopBar();

    const clock = document.querySelector('[data-shell="topbar-clock"]');
    expect(clock.textContent).toBe('09:15:30 UTC');

    act(() => vi.advanceTimersByTime(1000));
    expect(clock.textContent).toBe('09:15:31 UTC');
    // The machine-readable value and the visible one come from one `toISOString()`.
    expect(clock.getAttribute('datetime')).toBe('2024-05-01T09:15:31.000Z');
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Geometry (Requirement 2.2; Property 2 proper is task 8.9)
// ═══════════════════════════════════════════════════════════════════════════

describe('TopBar: geometry', () => {
  it('agrees with the sidebar’s brand block, so the seam is one line', () => {
    // The sidebar spans both grid rows, so a bar of a different height puts its bottom
    // border and the brand block's on two different pixels, across the whole shell.
    expect(TOPBAR_HEIGHT_PX).toBe(SIDEBAR_BRAND_HEIGHT_PX);
  });

  it('pins the bar row to that height, border included', () => {
    renderTopBar();

    const style = barRow().style;
    expect(style.height).toBe(`${TOPBAR_HEIGHT_PX}px`);
    expect(style.minHeight).toBe(`${TOPBAR_HEIGHT_PX}px`);
    expect(style.maxHeight).toBe(`${TOPBAR_HEIGHT_PX}px`);
    // Without this the 1px bottom border would be added to the 56.
    expect(style.boxSizing).toBe('border-box');
  });

  it('keeps the bar row identical across routes at one status', () => {
    for (const pathname of ['/app/dashboard', '/app/strategies/abc', '/app/billing', '/app/nope']) {
      const view = renderTopBar({ pathname });
      expect(barRow().style.height).toBe(`${TOPBAR_HEIGHT_PX}px`);
      view.unmount();
    }
  });

  it('keeps the strip outside the bar row, so it cannot resize it', () => {
    renderTopBar({ status: 'disconnected' });

    // The gate's restricted-route strip sits above the whole shell; this one sits below
    // the bar row inside the same `<header>`. Neither is inside the other's slot.
    expect(barRow().contains(strip())).toBe(false);
    expect(document.querySelector('[data-shell="topbar"]').contains(strip())).toBe(true);
    expect(barRow().style.height).toBe(`${TOPBAR_HEIGHT_PX}px`);
  });
});
