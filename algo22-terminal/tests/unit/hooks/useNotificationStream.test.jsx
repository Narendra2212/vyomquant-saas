/**
 * tests/unit/hooks/useNotificationStream.test.jsx
 *
 * vyomquant-ui-redesign task 10.1. design.md §11.5. Requirements 16.1, 16.2.
 *
 * WHAT THIS FILE HOLDS IN PLACE
 * -----------------------------
 * 1. **An allowlisted event raises exactly ONE toast** (Requirement 16.1). "Exactly one" is
 *    the assertion, not "at least one": the transport subscribes to a dozen frame types
 *    over one socket, and the failure mode that matters is a frame reaching the handler
 *    twice.
 * 2. **A non-allowlisted event raises none** (Requirement 16.2). Real backend events the
 *    policy deliberately does not map — a fill, a risk block, a paper-session stop, a
 *    renewed subscription — must be silent, and silent *structurally*, because the
 *    allowlist is default-closed rather than a list of things to suppress.
 * 3. **Garbage raises none and does not throw.** Frames arrive from the network. `null`, a
 *    string, a number, an empty object and an event type nobody has ever declared all have
 *    to be survivable, because the alternative is a socket handler that dies on a malformed
 *    frame and takes every later notification with it.
 * 4. **Unmount releases the subscriptions**, using the functions `wsClient.subscribe`
 *    returned — and a SECOND mount does not double anything, which is the ref count's whole
 *    reason for existing.
 *
 * `wsClient` is a double here: the real one constructs a `WebSocket`. What is under test is
 * this hook's use of the client's contract — one frame is routed to the handlers registered
 * for its `type` — not the client's own routing, which `builderRealtime.test.jsx` covers
 * against a fake socket.
 *
 * `notificationPolicy` is NOT mocked. The decision under test is "the transport raises a
 * toast if and only if the policy said to", and a mocked policy would assert that against a
 * fiction. Task 10.2 owns the exhaustive property (P32) over the whole category space; this
 * file pins the transport's four behaviours with real policy answers.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, cleanup, renderHook } from '@testing-library/react';

const { mockWsClient } = vi.hoisted(() => {
  /** @type {Map<string, Set<Function>>} eventType → handlers, as `websocketClient` keeps it. */
  const subscriptions = new Map();
  const client = {
    subscriptions,
    subscribe: vi.fn(),
    /**
     * Deliver one frame the way `processMessage` does: to the handlers registered for
     * `message.type || message.event_type`, and to nobody else.
     */
    deliver(message) {
      const eventType =
        message && typeof message === 'object'
          ? message.type || message.event_type
          : undefined;
      const handlers = subscriptions.get(eventType);
      if (!handlers) return 0;
      for (const handler of Array.from(handlers)) handler(message);
      return handlers.size;
    },
    /** Hand a frame straight to every registered handler, bypassing the type routing. */
    deliverToAll(message) {
      for (const handlers of subscriptions.values()) {
        for (const handler of Array.from(handlers)) handler(message);
      }
    },
    handlerCount() {
      let total = 0;
      for (const handlers of subscriptions.values()) total += handlers.size;
      return total;
    },
  };
  return { mockWsClient: client };
});

vi.mock('../../../src/websocketClient', () => ({
  default: mockWsClient,
}));

import {
  ENVELOPE_EVENT_TYPES,
  NOTIFICATION_EVENT_TYPES,
  resetNotificationStream,
  useNotificationStream,
} from '../../../src/hooks/useNotificationStream';
import { EVENT_TYPE_KEY, NOTIFIABLE_KEYS } from '../../../src/design/notificationPolicy';

/** The real `subscribe` contract: register, and return a function that deregisters. */
function register(eventType, handler) {
  if (!mockWsClient.subscriptions.has(eventType)) {
    mockWsClient.subscriptions.set(eventType, new Set());
  }
  mockWsClient.subscriptions.get(eventType).add(handler);
  return () => {
    const handlers = mockWsClient.subscriptions.get(eventType);
    if (!handlers) return;
    handlers.delete(handler);
    if (handlers.size === 0) mockWsClient.subscriptions.delete(eventType);
  };
}

beforeEach(() => {
  mockWsClient.subscriptions.clear();
  mockWsClient.subscribe.mockReset();
  mockWsClient.subscribe.mockImplementation(register);
  window.showToast = vi.fn();
});

afterEach(() => {
  cleanup();
  // Module state, so it has to be dropped between cases — the same reason
  // `resetUnreadNotifications` exists.
  resetNotificationStream();
  delete window.showToast;
  vi.restoreAllMocks();
});

// ═══════════════════════════════════════════════════════════════════════════
// 1. An allowlisted event raises exactly one toast (Requirement 16.1)
// ═══════════════════════════════════════════════════════════════════════════

describe('an allowlisted event raises exactly one toast', () => {
  it('raises one toast for a deployment success channel frame', () => {
    renderHook(() => useNotificationStream());

    act(() => {
      mockWsClient.deliver({
        type: 'deploy_success',
        channel: 'deployment_events',
        strategy_id: 'strat_1',
        payload: { strategy_name: 'Momentum Alpha', environment: 'live' },
      });
    });

    expect(window.showToast).toHaveBeenCalledTimes(1);
    expect(window.showToast).toHaveBeenCalledWith(
      'success',
      'Momentum Alpha is now deployed to live.',
    );
  });

  it('raises one toast for a `notification` envelope, reading the inner row', () => {
    // The envelope the outer `type` cannot discriminate: `notification` carries the row on
    // `data`, and `strategy:strategy_stopped` is what makes it one of the seven.
    renderHook(() => useNotificationStream());

    act(() => {
      mockWsClient.deliver({
        type: 'notification',
        data: {
          id: 'n_1',
          type: 'strategy_stopped',
          category: 'strategy',
          severity: 'critical',
          title: 'Strategy stopped',
          metadata: { strategy_name: 'Mean Reversion' },
        },
      });
    });

    expect(window.showToast).toHaveBeenCalledTimes(1);
    // 'warning' — the POLICY's severity for this key, not the row's 'critical'. The client
    // decides how loudly to speak (§11.5).
    expect(window.showToast).toHaveBeenCalledWith('warning', 'Mean Reversion stopped.');
  });

  it('raises one toast for a `subscription_update` envelope', () => {
    renderHook(() => useNotificationStream());

    act(() => {
      mockWsClient.deliver({
        type: 'subscription_update',
        event: 'subscription_expired',
        data: { listing_name: 'Trend Rider' },
      });
    });

    expect(window.showToast).toHaveBeenCalledTimes(1);
    expect(window.showToast).toHaveBeenCalledWith(
      'warning',
      'Your subscription to Trend Rider has expired.',
    );
  });

  it('raises one toast per frame across a burst, in arrival order', () => {
    renderHook(() => useNotificationStream());

    act(() => {
      mockWsClient.deliver({ type: 'order_rejected', exchange: 'binance', symbol: 'BTC/USDT' });
      mockWsClient.deliver({ type: 'deploy_failed', strategy_name: 'Momentum Alpha' });
      mockWsClient.deliver({ type: 'exchange_disconnected', exchange_id: 'bybit' });
    });

    expect(window.showToast).toHaveBeenCalledTimes(3);
    expect(window.showToast.mock.calls.map(([severity]) => severity)).toEqual([
      'error',
      'error',
      'error',
    ]);
  });

  it('subscribes to every frame type the allowlist can resolve, and each exactly once', () => {
    renderHook(() => useNotificationStream());

    // One handler per type: the transport cannot receive one frame twice.
    expect(mockWsClient.subscribe).toHaveBeenCalledTimes(NOTIFICATION_EVENT_TYPES.length);
    expect(new Set(NOTIFICATION_EVENT_TYPES).size).toBe(NOTIFICATION_EVENT_TYPES.length);
    expect(mockWsClient.handlerCount()).toBe(NOTIFICATION_EVENT_TYPES.length);

    // Non-vacuity, and the reason the subscription list is read off the policy rather than
    // typed out here: EVERY event type the allowlist can resolve is actually subscribed, so
    // no category is silent merely because nothing was listening for the frame that carries
    // it. A type added to `EVENT_TYPE_KEY` tomorrow is covered by this loop the same commit.
    for (const eventType of Object.keys(EVENT_TYPE_KEY)) {
      window.showToast.mockClear();
      act(() => {
        mockWsClient.deliver({ type: eventType });
      });
      expect(window.showToast, `${eventType} reached no handler`).toHaveBeenCalledTimes(1);
    }

    // The two envelopes are the exception, and deliberately so: their discriminator is
    // inside the frame (`data.type` / `event`), so the outer name alone resolves to nothing.
    // The envelope tests above are what cover them.
    for (const envelope of ENVELOPE_EVENT_TYPES) {
      window.showToast.mockClear();
      act(() => {
        mockWsClient.deliver({ type: envelope });
      });
      expect(window.showToast).not.toHaveBeenCalled();
    }

    // …and between them the subscribed types reach all seven of Requirement 16.1's
    // categories. A subscription set that covered six would pass every other test here.
    expect(new Set(Object.values(EVENT_TYPE_KEY))).toEqual(new Set(NOTIFIABLE_KEYS));
    expect(NOTIFIABLE_KEYS).toHaveLength(7);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 2. A non-allowlisted event raises none (Requirement 16.2)
// ═══════════════════════════════════════════════════════════════════════════

describe('a non-allowlisted event raises nothing', () => {
  // Every one of these is a real backend event that the policy records as a deliberate
  // exclusion. They are delivered to EVERY registered handler, not just the one for their
  // own type, so "nothing was subscribed to it" cannot be what makes them silent.
  it.each([
    ['a fill', { type: 'order_filled', symbol: 'BTC/USDT' }],
    ['a submitted order', { type: 'order_submitted', symbol: 'BTC/USDT' }],
    ['a risk block', { type: 'risk_block', strategy_name: 'Momentum Alpha' }],
    ['a kill switch', { type: 'risk.kill_switch_activated' }],
    ['a paper rejection', { type: 'paper_order_rejected', symbol: 'BTC/USDT' }],
    ['a deploy starting', { type: 'deploy_started', strategy_name: 'Momentum Alpha' }],
    ['a bot connecting', { type: 'bot_connected', bot_id: 'bot_1' }],
    ['an exchange connecting', { type: 'exchange_connected', exchange: 'binance' }],
    ['a heartbeat', { type: 'heartbeat' }],
    ['a market tick', { type: 'market_tick', symbol: 'BTC/USDT' }],
    [
      'a renewed subscription',
      { type: 'subscription_update', event: 'subscription_renewed', data: {} },
    ],
    [
      'a payment failure notification row',
      { type: 'notification', data: { type: 'payment_failed', category: 'billing' } },
    ],
    [
      'an allowlisted type under an undeclared category',
      { type: 'notification', data: { type: 'order_rejected', category: 'system' } },
    ],
  ])('stays silent for %s', (_label, event) => {
    renderHook(() => useNotificationStream());

    act(() => {
      mockWsClient.deliverToAll(event);
    });

    expect(window.showToast).not.toHaveBeenCalled();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 3. Garbage raises nothing and does not throw
// ═══════════════════════════════════════════════════════════════════════════

describe('an unrecognised or malformed event', () => {
  it.each([
    ['null', null],
    ['undefined', undefined],
    ['a number', 42],
    ['a string', 'order_rejected'],
    ['an empty object', {}],
    ['an array', []],
    ['a type of the wrong type', { type: { name: 'order_rejected' } }],
    ['an empty type', { type: '' }],
    ['a whitespace type', { type: '   ' }],
    ['an event type nobody has declared', { type: 'quantum_flux_detected' }],
    ['an envelope with no row', { type: 'notification' }],
    ['an envelope whose row is not an object', { type: 'notification', data: 'nope' }],
    ['a bare `expired` from nowhere', { type: 'expired', status: 'expired' }],
  ])('raises nothing and does not throw for %s', (_label, event) => {
    renderHook(() => useNotificationStream());

    expect(() => {
      act(() => {
        mockWsClient.deliverToAll(event);
      });
    }).not.toThrow();
    expect(window.showToast).not.toHaveBeenCalled();
  });

  it('drops a notification rather than throwing when no toast host is mounted', () => {
    // Mounted outside `AppShell` — a page rendered standalone, or a test. There is nothing
    // to call, and the socket handler must survive it: a throw here would kill every later
    // frame on the same subscription.
    delete window.showToast;
    renderHook(() => useNotificationStream());

    expect(() => {
      act(() => {
        mockWsClient.deliver({ type: 'deploy_failed', strategy_name: 'Momentum Alpha' });
      });
    }).not.toThrow();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 4. Teardown, and one transport no matter how many callers
// ═══════════════════════════════════════════════════════════════════════════

describe('subscription lifecycle', () => {
  it('releases every subscription on unmount', () => {
    const { unmount } = renderHook(() => useNotificationStream());
    expect(mockWsClient.handlerCount()).toBe(NOTIFICATION_EVENT_TYPES.length);

    unmount();

    // The cleanup IS the unsubscribe: the client is left holding nothing.
    expect(mockWsClient.handlerCount()).toBe(0);
    expect(mockWsClient.subscriptions.size).toBe(0);
  });

  it('raises nothing after unmount', () => {
    const { unmount } = renderHook(() => useNotificationStream());
    unmount();

    act(() => {
      mockWsClient.deliver({ type: 'deploy_success', strategy_name: 'Momentum Alpha' });
    });

    expect(window.showToast).not.toHaveBeenCalled();
  });

  it('does not double a toast when a second copy is mounted', () => {
    // The regression the ref count exists to prevent: a page mounting its own copy would
    // otherwise register a second handler per frame type and toast everything twice.
    const first = renderHook(() => useNotificationStream());
    const second = renderHook(() => useNotificationStream());

    expect(mockWsClient.subscribe).toHaveBeenCalledTimes(NOTIFICATION_EVENT_TYPES.length);
    expect(mockWsClient.handlerCount()).toBe(NOTIFICATION_EVENT_TYPES.length);

    act(() => {
      mockWsClient.deliver({ type: 'deploy_failed', strategy_name: 'Momentum Alpha' });
    });

    expect(window.showToast).toHaveBeenCalledTimes(1);

    // The second copy leaving does not silence the first.
    second.unmount();
    expect(mockWsClient.handlerCount()).toBe(NOTIFICATION_EVENT_TYPES.length);

    window.showToast.mockClear();
    act(() => {
      mockWsClient.deliver({ type: 'deploy_failed', strategy_name: 'Momentum Alpha' });
    });
    expect(window.showToast).toHaveBeenCalledTimes(1);

    first.unmount();
    expect(mockWsClient.handlerCount()).toBe(0);
  });

  it('re-subscribes after the last consumer leaves and a new one arrives', () => {
    const { unmount } = renderHook(() => useNotificationStream());
    unmount();
    expect(mockWsClient.handlerCount()).toBe(0);

    renderHook(() => useNotificationStream());
    expect(mockWsClient.handlerCount()).toBe(NOTIFICATION_EVENT_TYPES.length);

    act(() => {
      mockWsClient.deliver({ type: 'backtest_complete', strategy_name: 'Momentum Alpha' });
    });

    expect(window.showToast).toHaveBeenCalledTimes(1);
    expect(window.showToast).toHaveBeenCalledWith('info', 'Backtest finished for Momentum Alpha.');
  });
});
