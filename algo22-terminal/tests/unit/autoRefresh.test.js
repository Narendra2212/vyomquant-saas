/**
 * AUTO-REFRESH-01 — pages must update without a manual browser refresh.
 *
 * Reported: after stopping a deployment, every page kept showing the old state until the
 * trader pressed F5. Three causes, one test group each:
 *   1. A successful write only dropped cached reads under the write's own URL prefix, so
 *      `POST /api/live/deployments/x/stop` left `/api/strategies` cached for 60 s.
 *   2. The GET cache lived 60 s, longer than any poll interval, so polls were cache hits.
 *   3. No page polled or re-read on returning to the tab.
 */

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { act, cleanup, renderHook } from '@testing-library/react';

const { mockAxiosInstance } = vi.hoisted(() => ({
  mockAxiosInstance: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    patch: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));
vi.mock('axios', () => ({ default: { create: vi.fn(() => mockAxiosInstance) } }));

import { CACHE_TTL, get, post } from '../../src/apiClient';
import { useAutoRefresh, FOCUS_REFRESH_THROTTLE_MS } from '../../src/hooks/useAutoRefresh';
import { usePanelState } from '../../src/hooks/usePanelState';

function setVisibility(value) {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => value });
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
  setVisibility('visible');
});

describe('AUTO-REFRESH-01: the GET cache', () => {
  it('a successful write drops cached reads on unrelated paths', async () => {
    mockAxiosInstance.get.mockResolvedValue({ data: { status: 'active' } });
    mockAxiosInstance.post.mockResolvedValue({ data: { ok: true } });

    await get('/api/strategies/auto-refresh-01');
    await post('/api/live/deployments/d1/stop', {});
    await get('/api/strategies/auto-refresh-01');

    // Without the fix the second read is served from the cache (1 call).
    expect(mockAxiosInstance.get).toHaveBeenCalledTimes(2);
  });

  it('cache lifetime is shorter than the fastest page poll', () => {
    expect(CACHE_TTL).toBeLessThanOrEqual(5000);
  });
});

describe('AUTO-REFRESH-01: useAutoRefresh', () => {
  beforeEach(() => setVisibility('visible'));

  it('polls while visible and skips ticks while hidden', () => {
    vi.useFakeTimers();
    const refresh = vi.fn();
    renderHook(() => useAutoRefresh(refresh, 1000));

    act(() => vi.advanceTimersByTime(3000));
    expect(refresh).toHaveBeenCalledTimes(3);
    expect(refresh).toHaveBeenLastCalledWith(true);

    setVisibility('hidden');
    act(() => vi.advanceTimersByTime(3000));
    expect(refresh).toHaveBeenCalledTimes(3);
  });

  it('refreshes once when the trader comes back to the tab', () => {
    const refresh = vi.fn();
    renderHook(() => useAutoRefresh(refresh, 0));

    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
      window.dispatchEvent(new Event('focus'));
    });
    // Both events in one return → one read (throttled).
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(FOCUS_REFRESH_THROTTLE_MS).toBeGreaterThan(0);
  });

  it('stops on unmount', () => {
    vi.useFakeTimers();
    const refresh = vi.fn();
    const { unmount } = renderHook(() => useAutoRefresh(refresh, 1000));
    unmount();
    act(() => {
      vi.advanceTimersByTime(5000);
      window.dispatchEvent(new Event('focus'));
    });
    expect(refresh).not.toHaveBeenCalled();
  });
});

describe('AUTO-REFRESH-01: usePanelState with an interval', () => {
  it('re-reads on returning to the tab, keeping data on screen', async () => {
    const reader = vi.fn().mockResolvedValue({ items: [1] });
    const { result } = renderHook(() => usePanelState(reader, { intervalMs: 60000 }));
    await act(async () => {});
    expect(reader).toHaveBeenCalledTimes(1);

    await act(async () => {
      window.dispatchEvent(new Event('focus'));
    });
    expect(reader).toHaveBeenCalledTimes(2);
    expect(result.current.data).toEqual({ items: [1] });
  });

  it('a panel without an interval (an editable form) is not re-read on focus', async () => {
    const reader = vi.fn().mockResolvedValue({ items: [1] });
    renderHook(() => usePanelState(reader));
    await act(async () => {});
    await act(async () => {
      window.dispatchEvent(new Event('focus'));
    });
    expect(reader).toHaveBeenCalledTimes(1);
  });
});
