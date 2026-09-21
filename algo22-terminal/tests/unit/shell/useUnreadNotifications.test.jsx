/**
 * Unit tests for `src/hooks/useUnreadNotifications.js` — task 8.8.
 * Requirements 14.5, 18.3. design.md §6.4.
 *
 * WHAT THIS FILE HOLDS IN PLACE
 * -----------------------------
 * Two things, and they pull in opposite directions:
 *
 *   1. **One request, however many consumers.** §6.4's complaint is the two independent
 *      fetches `Sidebar` and `TopBar` made. Two consumers of this hook must produce one
 *      `getUnreadCount()` and one `subscribe('notification')`, and unmounting one of them
 *      must not take the other's data or subscription away.
 *   2. **The honesty semantics, unchanged.** Unknown is `null` and is not a badge and not a
 *      number in the accessible name; a server-reported `0` is a fact and IS announced; a
 *      pushed frame increments a KNOWN count only, because `null + 1` would fabricate the
 *      base. These moved out of `NotificationBell` verbatim and are pinned from both sides
 *      — here, and through the rendered bell in `topBar.test.jsx`.
 *
 * The store is module state, so it is reset between cases the same way the overlay registry
 * is. `resetUnreadNotifications` exists for that and for nothing else.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, cleanup, render } from '@testing-library/react';

const { mockWsClient, mockApi } = vi.hoisted(() => {
  const channels = new Map();
  return {
    mockWsClient: {
      channels,
      subscribe: vi.fn((eventType, handler) => {
        if (!channels.has(eventType)) channels.set(eventType, new Set());
        channels.get(eventType).add(handler);
        return () => channels.get(eventType).delete(handler);
      }),
      emit(eventType, payload) {
        for (const handler of Array.from(channels.get(eventType) ?? [])) handler(payload);
      },
    },
    mockApi: { notifications: { getUnreadCount: vi.fn() } },
  };
});

vi.mock('../../../src/websocketClient', () => ({ default: mockWsClient }));
vi.mock('../../../src/api', () => ({ api: mockApi, default: mockApi }));

import {
  describeUnread,
  resetUnreadNotifications,
  unreadBadgeText,
  useUnreadNotifications,
} from '../../../src/hooks/useUnreadNotifications';

/** A consumer that renders what it read, so a case can assert per-instance. */
function Consumer({ name }) {
  const { unread, known, label, badge } = useUnreadNotifications();
  return (
    <span
      data-consumer={name}
      data-unread={known ? String(unread) : 'unknown'}
      data-label={label}
      data-badge={badge ?? 'none'}
    />
  );
}

const read = (name) => document.querySelector(`[data-consumer="${name}"]`);
const subscriberCount = () => mockWsClient.channels.get('notification')?.size ?? 0;

beforeEach(() => {
  resetUnreadNotifications();
  mockWsClient.channels.clear();
  mockWsClient.subscribe.mockClear();
  mockApi.notifications.getUnreadCount.mockReset();
  mockApi.notifications.getUnreadCount.mockImplementation(() => new Promise(() => {}));
});

afterEach(() => {
  cleanup();
  resetUnreadNotifications();
});

// ═══════════════════════════════════════════════════════════════════════════
// One request, one subscription, however many consumers (§6.4)
// ═══════════════════════════════════════════════════════════════════════════

describe('useUnreadNotifications: the read is shared', () => {
  it('issues one request and one subscription for two consumers', async () => {
    mockApi.notifications.getUnreadCount.mockResolvedValue({ unread_count: 4 });

    render(
      <>
        <Consumer name="bell" />
        <Consumer name="menu" />
      </>,
    );

    // The whole point of the hook: two surfaces, one read.
    expect(mockApi.notifications.getUnreadCount).toHaveBeenCalledTimes(1);
    expect(subscriberCount()).toBe(1);

    await act(async () => {});

    expect(read('bell').getAttribute('data-unread')).toBe('4');
    expect(read('menu').getAttribute('data-unread')).toBe('4');
  });

  it('serves a consumer that mounts later from the value already there', async () => {
    mockApi.notifications.getUnreadCount.mockResolvedValue({ unread_count: 7 });

    const view = render(<Consumer name="bell" />);
    await act(async () => {});
    expect(read('bell').getAttribute('data-unread')).toBe('7');

    // The account menu mounts when the trader opens it, which is long after the bell.
    view.rerender(
      <>
        <Consumer name="bell" />
        <Consumer name="menu" />
      </>,
    );

    expect(read('menu').getAttribute('data-unread')).toBe('7');
    expect(mockApi.notifications.getUnreadCount).toHaveBeenCalledTimes(1);
  });

  it('keeps the store alive when one consumer unmounts and another remains', async () => {
    mockApi.notifications.getUnreadCount.mockResolvedValue({ unread_count: 2 });

    const view = render(
      <>
        <Consumer name="bell" />
        <Consumer name="menu" />
      </>,
    );
    await act(async () => {});

    // The menu closes; the bell stays in the bar.
    view.rerender(<Consumer name="bell" />);

    expect(subscriberCount()).toBe(1);
    expect(read('bell').getAttribute('data-unread')).toBe('2');
    // Re-opening the menu costs no request.
    view.rerender(
      <>
        <Consumer name="bell" />
        <Consumer name="menu" />
      </>,
    );
    expect(mockApi.notifications.getUnreadCount).toHaveBeenCalledTimes(1);
    expect(read('menu').getAttribute('data-unread')).toBe('2');
  });

  it('drops the subscription only when the last consumer leaves', () => {
    const view = render(
      <>
        <Consumer name="bell" />
        <Consumer name="menu" />
      </>,
    );
    expect(subscriberCount()).toBe(1);

    view.rerender(<Consumer name="bell" />);
    expect(subscriberCount()).toBe(1);

    view.unmount();
    expect(subscriberCount()).toBe(0);
  });

  it('does not carry a count across a full teardown', async () => {
    // A count that outlived its last consumer would be re-rendered on the next mount
    // without being re-read — a figure from an unknown time ago presented as current, which
    // is what Requirement 14.5 rules out.
    mockApi.notifications.getUnreadCount.mockResolvedValue({ unread_count: 5 });
    const first = render(<Consumer name="bell" />);
    await act(async () => {});
    expect(read('bell').getAttribute('data-unread')).toBe('5');
    first.unmount();

    mockApi.notifications.getUnreadCount.mockImplementation(() => new Promise(() => {}));
    render(<Consumer name="bell" />);

    expect(read('bell').getAttribute('data-unread')).toBe('unknown');
    expect(mockApi.notifications.getUnreadCount).toHaveBeenCalledTimes(2);
  });

  it('discards an answer that arrives after the last consumer left', async () => {
    let settle;
    mockApi.notifications.getUnreadCount.mockImplementation(
      () => new Promise((resolve) => {
        settle = resolve;
      }),
    );

    const view = render(<Consumer name="bell" />);
    view.unmount();
    await act(async () => {
      settle({ unread_count: 9 });
    });

    // Nothing to publish to, and nothing left behind for the next mount to inherit.
    render(<Consumer name="bell" />);
    expect(read('bell').getAttribute('data-unread')).toBe('unknown');
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// The honesty semantics (Requirement 14.5)
// ═══════════════════════════════════════════════════════════════════════════

describe('useUnreadNotifications: unknown is not zero', () => {
  it('starts unknown, with no badge and no number in the name', () => {
    render(<Consumer name="bell" />);

    expect(read('bell').getAttribute('data-unread')).toBe('unknown');
    expect(read('bell').getAttribute('data-label')).toBe('Notifications');
    expect(read('bell').getAttribute('data-badge')).toBe('none');
  });

  it('stays unknown when the read fails', async () => {
    mockApi.notifications.getUnreadCount.mockRejectedValue(new Error('503'));
    render(<Consumer name="bell" />);
    await act(async () => {});

    expect(read('bell').getAttribute('data-unread')).toBe('unknown');
    expect(read('bell').getAttribute('data-label')).toBe('Notifications');
  });

  it('announces a server-reported zero, and draws no badge for it', async () => {
    mockApi.notifications.getUnreadCount.mockResolvedValue({ unread_count: 0 });
    render(<Consumer name="bell" />);
    await act(async () => {});

    // A zero the server really reported is a reading, not an absence.
    expect(read('bell').getAttribute('data-unread')).toBe('0');
    expect(read('bell').getAttribute('data-label')).toBe('Notifications, 0 unread');
    expect(read('bell').getAttribute('data-badge')).toBe('none');
  });

  it.each([
    ['a missing field', {}],
    ['a null count', { unread_count: null }],
    ['a string', { unread_count: '3' }],
    ['a negative', { unread_count: -1 }],
    ['no body at all', undefined],
  ])('treats %s as unknown rather than zero', async (_label, body) => {
    mockApi.notifications.getUnreadCount.mockResolvedValue(body);
    render(<Consumer name="bell" />);
    await act(async () => {});

    expect(read('bell').getAttribute('data-unread')).toBe('unknown');
  });

  it('increments a known count on a pushed frame, for every consumer at once', async () => {
    mockApi.notifications.getUnreadCount.mockResolvedValue({ unread_count: 1 });
    render(
      <>
        <Consumer name="bell" />
        <Consumer name="menu" />
      </>,
    );
    await act(async () => {});

    act(() => mockWsClient.emit('notification', { type: 'notification', data: {} }));

    expect(read('bell').getAttribute('data-unread')).toBe('2');
    expect(read('menu').getAttribute('data-unread')).toBe('2');
  });

  it('does not invent a base count when a frame arrives while unknown', () => {
    render(<Consumer name="bell" />);
    act(() => mockWsClient.emit('notification', { type: 'notification', data: {} }));

    // `null + 1` would have been a fabricated 1.
    expect(read('bell').getAttribute('data-unread')).toBe('unknown');
    expect(read('bell').getAttribute('data-label')).toBe('Notifications');
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// The two formatters, which exist so two surfaces cannot word one count twice
// ═══════════════════════════════════════════════════════════════════════════

describe('describeUnread / unreadBadgeText', () => {
  it('names the count only when there is one', () => {
    expect(describeUnread(null)).toBe('Notifications');
    expect(describeUnread(undefined)).toBe('Notifications');
    expect(describeUnread(0)).toBe('Notifications, 0 unread');
    expect(describeUnread(3)).toBe('Notifications, 3 unread');
    expect(describeUnread(128)).toBe('Notifications, 128 unread');
  });

  it('draws a badge only for a known, non-zero count, and caps the text', () => {
    expect(unreadBadgeText(null)).toBeNull();
    expect(unreadBadgeText(0)).toBeNull();
    expect(unreadBadgeText(1)).toBe('1');
    expect(unreadBadgeText(99)).toBe('99');
    // The cap is display only — the accessible name keeps the real number.
    expect(unreadBadgeText(128)).toBe('99+');
    expect(describeUnread(128)).toContain('128');
  });
});
