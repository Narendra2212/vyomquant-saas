/**
 * tests/unit/hooks/useConnectionStatus.test.jsx
 *
 * vyomquant-ui-redesign task 5.6. `design.md` §6.5. Requirements 2.5, 2.6.
 *
 * WHAT THIS FILE HOLDS IN PLACE
 * -----------------------------
 * 1. **The indicator reads the client, not a literal** (Requirement 2.5). `TopBar.jsx`
 *    renders `<LiveStatusV2 status="running" />` today — a hardcoded string, so the light
 *    claims LIVE whether or not a socket exists. The hook's whole job is that the value
 *    comes from `wsClient`.
 * 2. **A transition is reflected with NO timer advance** (Requirement 2.6). This is the
 *    assertion that distinguishes a push implementation from a polled one, and it is why
 *    the clock is faked in that test and never advanced: a hook that satisfied the
 *    5-second bound by polling every 4 s would fail it, and a hook that polls would also
 *    leave a timer behind. Both halves are checked.
 * 3. **The re-seed absorbs a transition that raced mount.** A drop landing between the
 *    render that seeded the value and the effect that subscribes is missed by both, and
 *    the component would sit reading `connected` on a dead socket.
 * 4. **Unmount unsubscribes**, using the function `onStatusChange` returned.
 *
 * `wsClient` is a double here: the real one constructs a `WebSocket`, and what is under
 * test is this hook's use of the client's contract, not the client's own transitions
 * (which `builderRealtime.test.jsx` covers against a fake socket).
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, cleanup, renderHook } from '@testing-library/react';

const { mockWsClient, readStatus, addStatusListener } = vi.hoisted(() => {
  const listeners = new Set();
  const client = {
    status: 'disconnected',
    listeners,
    getStatus: vi.fn(),
    onStatusChange: vi.fn(),
    /** Drive a transition the way `_setStatus` does: set, then notify synchronously. */
    transitionTo(next) {
      client.status = next;
      for (const listener of Array.from(listeners)) listener(next);
    },
  };
  return {
    mockWsClient: client,
    readStatus: () => client.status,
    addStatusListener: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
});

vi.mock('../../../src/websocketClient', () => ({
  default: mockWsClient,
}));

import { useConnectionStatus } from '../../../src/hooks/useConnectionStatus';

// The implementations are (re)installed rather than merely cleared, so a test that swaps
// one in cannot leak it into the next.
beforeEach(() => {
  mockWsClient.status = 'disconnected';
  mockWsClient.listeners.clear();
  mockWsClient.getStatus.mockReset();
  mockWsClient.getStatus.mockImplementation(readStatus);
  mockWsClient.onStatusChange.mockReset();
  mockWsClient.onStatusChange.mockImplementation(addStatusListener);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

// ═══════════════════════════════════════════════════════════════════════════
// Seeding — the value is the client's, from the first render (Requirement 2.5)
// ═══════════════════════════════════════════════════════════════════════════

describe('seeding from the client', () => {
  it('returns wsClient.getStatus() on the first render, with no frame required', () => {
    mockWsClient.status = 'connected';

    const { result } = renderHook(() => useConnectionStatus());

    // The defect this replaces is a literal. There is no code path here that produces a
    // status the client did not report.
    expect(result.current).toBe('connected');
    expect(mockWsClient.getStatus).toHaveBeenCalled();
  });

  it.each(['disconnected', 'connecting', 'connected', 'error', 'failed'])(
    'passes the client status %p through unmapped',
    (status) => {
      mockWsClient.status = status;

      const { result } = renderHook(() => useConnectionStatus());

      // No mapping in the hook: §4.1's total status→token mapping lives in the indicator,
      // and a second copy of that vocabulary here is how the two drift apart.
      expect(result.current).toBe(status);
    },
  );

  it('passes a status word this build has never seen through unchanged', () => {
    mockWsClient.status = 'a_status_added_next_year';

    const { result } = renderHook(() => useConnectionStatus());

    expect(result.current).toBe('a_status_added_next_year');
  });

  it('re-seeds on mount, absorbing a transition that raced the first render', () => {
    // The window this closes: `useState`'s initialiser runs during render, the
    // subscription is installed after commit. A drop in between is seen by neither.
    let seededDuringRender = false;
    mockWsClient.status = 'connected';
    mockWsClient.getStatus.mockImplementation(() => {
      if (!seededDuringRender) {
        seededDuringRender = true;
        return 'connected';
      }
      // By the time the effect reads it, the socket has dropped.
      return 'disconnected';
    });

    const { result } = renderHook(() => useConnectionStatus());

    // Without the second read this would be 'connected' — a component confidently
    // reporting a live engine over a dead socket, which is the Requirement 2.6 failure.
    expect(result.current).toBe('disconnected');
    expect(mockWsClient.getStatus.mock.calls.length).toBeGreaterThanOrEqual(2);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// The 5-second bound is met by the push, not by a poll (Requirement 2.6)
// ═══════════════════════════════════════════════════════════════════════════

describe('transitions are reflected by push (Requirement 2.6)', () => {
  it('reflects a disconnect with the clock frozen — no timer advance at all', () => {
    vi.useFakeTimers();
    mockWsClient.status = 'connected';

    const { result } = renderHook(() => useConnectionStatus());
    expect(result.current).toBe('connected');

    act(() => {
      mockWsClient.transitionTo('disconnected');
    });

    // Not a single millisecond has been advanced. Zero elapsed time is inside any bound,
    // and a polled implementation could not pass this line — which is exactly why the
    // assertion is written this way rather than as `advanceTimersByTime(5000)`.
    expect(result.current).toBe('disconnected');
  });

  it('leaves no timer behind, so there is no interval to be slower than the bound', () => {
    vi.useFakeTimers();
    const setIntervalSpy = vi.spyOn(globalThis, 'setInterval');
    const setTimeoutSpy = vi.spyOn(globalThis, 'setTimeout');

    const { unmount } = renderHook(() => useConnectionStatus());
    act(() => {
      mockWsClient.transitionTo('connected');
    });

    expect(setIntervalSpy).not.toHaveBeenCalled();
    expect(setTimeoutSpy).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);

    unmount();
    setIntervalSpy.mockRestore();
    setTimeoutSpy.mockRestore();
  });

  it('follows a whole reconnect sequence in order', () => {
    mockWsClient.status = 'connected';
    const { result } = renderHook(() => useConnectionStatus());

    const seen = [result.current];
    for (const status of ['error', 'disconnected', 'connecting', 'connected']) {
      act(() => {
        mockWsClient.transitionTo(status);
      });
      seen.push(result.current);
    }

    expect(seen).toEqual(['connected', 'error', 'disconnected', 'connecting', 'connected']);
  });

  it('subscribes once per mount', () => {
    renderHook(() => useConnectionStatus());

    expect(mockWsClient.onStatusChange).toHaveBeenCalledTimes(1);
    expect(mockWsClient.listeners.size).toBe(1);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Teardown — the effect cleanup IS the unsubscribe
// ═══════════════════════════════════════════════════════════════════════════

describe('unsubscribing', () => {
  it('drops its listener on unmount', () => {
    const { unmount } = renderHook(() => useConnectionStatus());
    expect(mockWsClient.listeners.size).toBe(1);

    unmount();

    // A listener outliving its component would call `setState` on an unmounted tree for
    // the rest of the session, once per transition.
    expect(mockWsClient.listeners.size).toBe(0);
  });

  it('does not react to a transition after unmount', () => {
    mockWsClient.status = 'connected';
    const { result, unmount } = renderHook(() => useConnectionStatus());
    unmount();

    act(() => {
      mockWsClient.transitionTo('disconnected');
    });

    expect(result.current).toBe('connected');
  });

  it('re-subscribes cleanly when a second consumer mounts after the first left', () => {
    const first = renderHook(() => useConnectionStatus());
    first.unmount();

    mockWsClient.status = 'connecting';
    const second = renderHook(() => useConnectionStatus());

    expect(second.result.current).toBe('connecting');
    expect(mockWsClient.listeners.size).toBe(1);
  });

  it('gives every concurrent consumer its own listener and the same answer', () => {
    // Several places show the status at once (top bar indicator, the disconnected strip,
    // the Live Trading connection tier). None of them may disagree.
    mockWsClient.status = 'connected';
    const a = renderHook(() => useConnectionStatus());
    const b = renderHook(() => useConnectionStatus());
    const c = renderHook(() => useConnectionStatus());

    expect(mockWsClient.listeners.size).toBe(3);

    act(() => {
      mockWsClient.transitionTo('disconnected');
    });

    expect([a.result.current, b.result.current, c.result.current]).toEqual([
      'disconnected',
      'disconnected',
      'disconnected',
    ]);
  });
});
