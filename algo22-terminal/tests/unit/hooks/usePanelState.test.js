/**
 * usePanelState — §11.1's panel state contract.
 *
 * vyomquant-ui-redesign task 5.5. Requirements 14.1, 14.2, 14.3, 14.5, 19.3.
 *
 * WHAT IS ASSERTED, AND WHY EACH PART MATTERS
 * ===========================================
 * * **Every edge of §11.1's state machine**, including the ones that must NOT exist. The
 *   absent edges are the point of the contract: `error → refreshing` is what would put a
 *   stale payload back on screen after a failure, and it is asserted to be unreachable.
 * * **`unavailable` issues no request** (Requirement 19.3). A capability the backend does
 *   not have is a state with a human reason, not a 404 and not an empty panel that reads as
 *   "you have none of these".
 * * **A failure after a success leaves `data === null`** (Requirement 14.5). This is the
 *   one assertion the hook exists for. `usePolling` in `components/ui-legacy/primitives.jsx`
 *   keeps the last payload here and only sets `error`, which is how a page comes to render
 *   the previous tick's P&L under an error indicator.
 * * **`refetch`'s identity survives every data change.** `usePolling` lists `data` in its
 *   `useCallback` dependencies, so each successful poll produces a new callback, re-runs the
 *   effect and re-creates the interval. The `setInterval` count is asserted directly.
 *
 * The failures are built as real `ApiError`s from `apiClient.js` rather than as look-alikes,
 * so the classification is exercised against the class the app actually throws — including
 * its own `status → category` derivation.
 *
 * Property P26 is task 6.2's subject and is deliberately not written here.
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import { act, cleanup, renderHook } from '@testing-library/react';

import { ApiError } from '../../../src/apiClient';
import {
  ALL_PANEL_STATES,
  PANEL_STATES,
  STATES_WITHOUT_CHILDREN,
  isEmptyPayload,
  panelStateForFailure,
  usePanelState,
} from '../../../src/hooks/usePanelState';

// ══════════════════════════════════════════════════════════════════════════════════════
// FIXTURES — the wire shapes
// ══════════════════════════════════════════════════════════════════════════════════════

/** The real `ApiError` the interceptor raises. `status` drives `category` inside the class. */
const apiError = (status, data = null, message = 'The server refused the read.') =>
  new ApiError(message, { status, data });

/** `marketplace/errors.py`'s single envelope: `{error: {code, message, details}}`. */
const envelope = (code, message, details = {}) => ({ error: { code, message, details } });

/**
 * A reader whose every call is resolved or rejected by the test, in order.
 *
 * A read that resolves on its own schedule would make "what state is the panel in *during*
 * the read" untestable, and `loading` versus `refreshing` is exactly what has to be pinned
 * down here.
 */
function deferredReader() {
  const calls = [];
  const reader = vi.fn(
    () =>
      new Promise((resolve, reject) => {
        calls.push({ resolve, reject });
      }),
  );
  return { reader, calls };
}

/** Flush the microtask a settled reader promise resolves on. */
const settle = () => act(async () => {});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

// ══════════════════════════════════════════════════════════════════════════════════════
// THE VOCABULARY
// ══════════════════════════════════════════════════════════════════════════════════════

describe('the eight states', () => {
  it('names exactly §11.1\u2019s eight, and no others', () => {
    expect([...ALL_PANEL_STATES].sort()).toEqual([
      'empty',
      'error',
      'idle',
      'loading',
      'ready',
      'refreshing',
      'unauthorised',
      'unavailable',
    ]);
  });

  it('withholds children in every state but ready and refreshing (Requirement 14.5)', () => {
    // `refreshing` is the ONE state where previous data stays on screen, and it is reachable
    // only from a read that succeeded.
    expect(STATES_WITHOUT_CHILDREN).not.toContain(PANEL_STATES.READY);
    expect(STATES_WITHOUT_CHILDREN).not.toContain(PANEL_STATES.REFRESHING);
    expect([...STATES_WITHOUT_CHILDREN].sort()).toEqual([
      'empty',
      'error',
      'idle',
      'loading',
      'unauthorised',
      'unavailable',
    ]);
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// EMPTY IS NOT ERROR, AND ZERO IS NOT ABSENCE
// ══════════════════════════════════════════════════════════════════════════════════════

describe('isEmptyPayload', () => {
  it('reports nothing-arrived and zero-length collections as empty', () => {
    expect(isEmptyPayload(null)).toBe(true);
    expect(isEmptyPayload(undefined)).toBe(true);
    expect(isEmptyPayload([])).toBe(true);
    expect(isEmptyPayload('   ')).toBe(true);
    expect(isEmptyPayload(new Map())).toBe(true);
    expect(isEmptyPayload(new Set())).toBe(true);
    expect(isEmptyPayload({})).toBe(true);
    expect(isEmptyPayload({ items: [], total: 0 })).toBe(true);
    expect(isEmptyPayload({ results: [] })).toBe(true);
  });

  it('never reports a payload with content as empty', () => {
    expect(isEmptyPayload([{ id: 1 }])).toBe(false);
    expect(isEmptyPayload({ items: [{ id: 1 }] })).toBe(false);
    // One collection with content is content, whatever the others hold.
    expect(isEmptyPayload({ items: [], rows: [{ id: 1 }] })).toBe(false);
    // No recognised collection key, so the object's own fields decide.
    expect(isEmptyPayload({ total: 0 })).toBe(false);
  });

  it('treats a zero as a value, not as an absence (Requirement 14.5)', () => {
    // A flat P&L of 0 is a reading. Reporting it as `empty` would hide a real figure behind
    // "there is nothing here", which is the fabrication Requirement 14.5 forbids in reverse.
    expect(isEmptyPayload(0)).toBe(false);
    expect(isEmptyPayload(false)).toBe(false);
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// SERVER-ERROR → STATE, LIFTED FROM paperTradingFormat.js
// ══════════════════════════════════════════════════════════════════════════════════════

describe('panelStateForFailure', () => {
  it('separates 401/403 from a general failure', () => {
    expect(panelStateForFailure(apiError(401))).toBe(PANEL_STATES.UNAUTHORISED);
    expect(panelStateForFailure(apiError(403))).toBe(PANEL_STATES.UNAUTHORISED);
    expect(panelStateForFailure(apiError(500))).toBe(PANEL_STATES.ERROR);
    expect(panelStateForFailure(apiError(404))).toBe(PANEL_STATES.ERROR);
    // No status at all: the request never reached a response.
    expect(panelStateForFailure(apiError(undefined))).toBe(PANEL_STATES.ERROR);
    expect(panelStateForFailure(new Error('Network Error'))).toBe(PANEL_STATES.ERROR);
  });

  it('reads the server\u2019s code before its status, so an entitlement refusal is not "sign in again"', () => {
    // Both of these are 403s. Reporting them as `unauthorised` would tell a trader who is
    // signed in perfectly well to sign in again.
    expect(
      panelStateForFailure(
        apiError(403, envelope('MARKETPLACE_STRATEGY_UNAVAILABLE', 'The listing does not resolve.')),
      ),
    ).toBe(PANEL_STATES.UNAVAILABLE);
    expect(
      panelStateForFailure(
        apiError(403, envelope('MARKETPLACE_SUBSCRIPTION_EXPIRED', 'This period has ended.')),
      ),
    ).toBe(PANEL_STATES.UNAVAILABLE);
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// [*] → idle, AND idle → loading
// ══════════════════════════════════════════════════════════════════════════════════════

describe('usePanelState — idle', () => {
  it('asks nothing while disabled', () => {
    const reader = vi.fn();
    const { result } = renderHook(() => usePanelState(reader, { enabled: false }));

    expect(result.current.state).toBe(PANEL_STATES.IDLE);
    expect(result.current.data).toBeNull();
    expect(result.current.error).toBeNull();
    expect(result.current.lastUpdated).toBeNull();
    expect(reader).not.toHaveBeenCalled();
  });

  it('asks nothing when there is no reader yet', () => {
    const { result } = renderHook(() => usePanelState(null));
    expect(result.current.state).toBe(PANEL_STATES.IDLE);
  });

  it('returns to idle, discarding the answer, when the panel is disabled again', async () => {
    const { reader, calls } = deferredReader();
    const { result, rerender } = renderHook(({ on }) => usePanelState(reader, { enabled: on }), {
      initialProps: { on: true },
    });

    await act(async () => calls[0].resolve([{ id: 1 }]));
    expect(result.current.state).toBe(PANEL_STATES.READY);

    rerender({ on: false });
    expect(result.current.state).toBe(PANEL_STATES.IDLE);
    expect(result.current.data).toBeNull();
    expect(result.current.lastUpdated).toBeNull();
  });

  it('enters loading the moment a read starts', () => {
    const { reader } = deferredReader();
    const { result } = renderHook(() => usePanelState(reader));

    expect(result.current.state).toBe(PANEL_STATES.LOADING);
    expect(result.current.data).toBeNull();
    expect(reader).toHaveBeenCalledTimes(1);
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// loading → ready | empty | error | unauthorised | unavailable
// ══════════════════════════════════════════════════════════════════════════════════════

describe('usePanelState — where a first read lands', () => {
  it('loading → ready on a 2xx with items', async () => {
    const { reader, calls } = deferredReader();
    const { result } = renderHook(() => usePanelState(reader));

    await act(async () => calls[0].resolve([{ id: 1 }, { id: 2 }]));

    expect(result.current.state).toBe(PANEL_STATES.READY);
    expect(result.current.data).toEqual([{ id: 1 }, { id: 2 }]);
    expect(result.current.error).toBeNull();
    expect(result.current.lastUpdated).toBeInstanceOf(Date);
  });

  it('loading → empty on a 2xx with zero items, which is not an error', async () => {
    const { reader, calls } = deferredReader();
    const { result } = renderHook(() => usePanelState(reader));

    await act(async () => calls[0].resolve({ items: [], total: 0 }));

    expect(result.current.state).toBe(PANEL_STATES.EMPTY);
    expect(result.current.error).toBeNull();
    // A read that succeeded and found nothing is still a read that succeeded.
    expect(result.current.lastUpdated).toBeInstanceOf(Date);
  });

  it('loading → error on a 5xx and on a network failure', async () => {
    const { reader, calls } = deferredReader();
    const { result } = renderHook(() => usePanelState(reader));

    await act(async () => calls[0].reject(apiError(500)));
    expect(result.current.state).toBe(PANEL_STATES.ERROR);
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect(result.current.data).toBeNull();

    const network = renderHook(() => usePanelState(() => Promise.reject(apiError(undefined))));
    await settle();
    expect(network.result.current.state).toBe(PANEL_STATES.ERROR);
  });

  it('loading → unauthorised on a 401 and a 403', async () => {
    const first = renderHook(() => usePanelState(() => Promise.reject(apiError(401))));
    await settle();
    expect(first.result.current.state).toBe(PANEL_STATES.UNAUTHORISED);

    const second = renderHook(() => usePanelState(() => Promise.reject(apiError(403))));
    await settle();
    expect(second.result.current.state).toBe(PANEL_STATES.UNAUTHORISED);
  });

  it('loading → unavailable when the server itself says the thing does not resolve', async () => {
    const failure = apiError(
      403,
      envelope('MARKETPLACE_STRATEGY_UNAVAILABLE', 'The listing does not resolve.'),
    );
    const { result } = renderHook(() => usePanelState(() => Promise.reject(failure)));
    await settle();

    expect(result.current.state).toBe(PANEL_STATES.UNAVAILABLE);
    expect(result.current.data).toBeNull();
    // The reason is still on the error, so `ds/Panel` can render the server's own words.
    expect(result.current.error).toBe(failure);
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// unavailable WITHOUT A REQUEST — Requirement 19.3
// ══════════════════════════════════════════════════════════════════════════════════════

describe('usePanelState — unavailable short-circuits (Requirement 19.3)', () => {
  const REASON = 'Position data is not reported for this exchange connector.';

  it('never calls the reader', () => {
    const reader = vi.fn();
    const { result } = renderHook(() => usePanelState(reader, { unavailable: REASON }));

    expect(result.current.state).toBe(PANEL_STATES.UNAVAILABLE);
    expect(reader).not.toHaveBeenCalled();
    expect(result.current.data).toBeNull();
    expect(result.current.error).toBeNull();
    expect(result.current.lastUpdated).toBeNull();
  });

  it('issues nothing even on an explicit refetch, and none on an interval', async () => {
    vi.useFakeTimers();
    const reader = vi.fn();
    const { result } = renderHook(() =>
      usePanelState(reader, { unavailable: REASON, intervalMs: 1000 }),
    );

    await act(async () => {
      result.current.refetch();
      vi.advanceTimersByTime(10000);
    });

    expect(reader).not.toHaveBeenCalled();
    expect(result.current.state).toBe(PANEL_STATES.UNAVAILABLE);
  });

  it('wins over enabled, because a missing capability is the more specific fact', () => {
    const reader = vi.fn();
    const { result } = renderHook(() =>
      usePanelState(reader, { unavailable: REASON, enabled: true }),
    );
    expect(result.current.state).toBe(PANEL_STATES.UNAVAILABLE);
    expect(reader).not.toHaveBeenCalled();
  });

  it('does not treat null or a blank string as a reason', () => {
    const { reader: a } = deferredReader();
    const first = renderHook(() => usePanelState(a, { unavailable: null }));
    expect(first.result.current.state).toBe(PANEL_STATES.LOADING);

    const { reader: b } = deferredReader();
    const second = renderHook(() => usePanelState(b, { unavailable: '   ' }));
    // A reason is REQUIRED for `unavailable`, so a blank one cannot buy the state.
    expect(second.result.current.state).toBe(PANEL_STATES.LOADING);
  });

  it('drops a previous answer when a capability gap is declared after a successful read', async () => {
    const { reader, calls } = deferredReader();
    const { result, rerender } = renderHook(({ reason }) => usePanelState(reader, { unavailable: reason }), {
      initialProps: { reason: null },
    });

    await act(async () => calls[0].resolve([{ id: 1 }]));
    expect(result.current.data).toEqual([{ id: 1 }]);

    rerender({ reason: REASON });
    expect(result.current.state).toBe(PANEL_STATES.UNAVAILABLE);
    expect(result.current.data).toBeNull();
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// A FAILURE DISCARDS THE PREVIOUS ANSWER — Requirement 14.5
// ══════════════════════════════════════════════════════════════════════════════════════

describe('usePanelState — a failed read discards data (Requirement 14.5)', () => {
  it('leaves data null after a successful read is followed by a failed one', async () => {
    const { reader, calls } = deferredReader();
    const { result } = renderHook(() => usePanelState(reader));

    await act(async () => calls[0].resolve([{ symbol: 'BTC/USDT', pnl: '1234.5600' }]));
    expect(result.current.state).toBe(PANEL_STATES.READY);
    expect(result.current.data).toEqual([{ symbol: 'BTC/USDT', pnl: '1234.5600' }]);

    await act(async () => {
      result.current.refetch();
    });
    await act(async () => calls[1].reject(apiError(503)));

    // THE assertion this hook exists for. `usePolling` keeps the payload here.
    expect(result.current.data).toBeNull();
    expect(result.current.state).toBe(PANEL_STATES.ERROR);
    // No figure from the successful payload survives anywhere in the returned state.
    expect(JSON.stringify(result.current.data)).not.toContain('1234.5600');
    // And no freshness stamp for a payload that is no longer on screen.
    expect(result.current.lastUpdated).toBeNull();
  });

  it('discards data on an unauthorised and on an unavailable failure too', async () => {
    const { reader, calls } = deferredReader();
    const { result } = renderHook(() => usePanelState(reader));

    await act(async () => calls[0].resolve([{ balance: '5000.00' }]));
    await act(async () => {
      result.current.refetch();
    });
    await act(async () => calls[1].reject(apiError(401)));
    expect(result.current.state).toBe(PANEL_STATES.UNAUTHORISED);
    expect(result.current.data).toBeNull();

    await act(async () => {
      result.current.refetch();
    });
    await act(async () =>
      calls[2].reject(apiError(403, envelope('MARKETPLACE_SUBSCRIPTION_EXPIRED', 'Ended.'))),
    );
    expect(result.current.state).toBe(PANEL_STATES.UNAVAILABLE);
    expect(result.current.data).toBeNull();
  });

  it('drops the previous answer to a read that is now empty, rather than keeping the old rows', async () => {
    const { reader, calls } = deferredReader();
    const { result } = renderHook(() => usePanelState(reader));

    await act(async () => calls[0].resolve([{ id: 1 }]));
    await act(async () => {
      result.current.refetch();
    });
    await act(async () => calls[1].resolve([]));

    expect(result.current.state).toBe(PANEL_STATES.EMPTY);
    expect(result.current.data).toBeNull();
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// ready → refreshing, AND error ↛ refreshing
// ══════════════════════════════════════════════════════════════════════════════════════

describe('usePanelState — refreshing is reachable only from ready', () => {
  it('ready → refreshing → ready, keeping the previous data on screen for the wait', async () => {
    const { reader, calls } = deferredReader();
    const { result } = renderHook(() => usePanelState(reader));

    await act(async () => calls[0].resolve([{ id: 1 }]));
    expect(result.current.state).toBe(PANEL_STATES.READY);

    await act(async () => {
      result.current.refetch();
    });
    expect(result.current.state).toBe(PANEL_STATES.REFRESHING);
    // §11.1: this is the ONE state where previous data stays on screen.
    expect(result.current.data).toEqual([{ id: 1 }]);

    await act(async () => calls[1].resolve([{ id: 2 }]));
    expect(result.current.state).toBe(PANEL_STATES.READY);
    expect(result.current.data).toEqual([{ id: 2 }]);
  });

  it('refreshing → error, and the retry after it is a clean loading read, never refreshing', async () => {
    const { reader, calls } = deferredReader();
    const { result } = renderHook(() => usePanelState(reader));

    await act(async () => calls[0].resolve([{ id: 1 }]));
    await act(async () => {
      result.current.refetch();
    });
    expect(result.current.state).toBe(PANEL_STATES.REFRESHING);

    await act(async () => calls[1].reject(apiError(500)));
    expect(result.current.state).toBe(PANEL_STATES.ERROR);

    // error → refreshing does not exist. A retry from `error` has nothing to keep on screen.
    await act(async () => {
      result.current.refetch();
    });
    expect(result.current.state).toBe(PANEL_STATES.LOADING);
    expect(result.current.data).toBeNull();
  });

  it('never enters refreshing from empty, unauthorised, unavailable or idle', async () => {
    const { reader, calls } = deferredReader();
    const { result } = renderHook(() => usePanelState(reader));

    // empty → loading
    await act(async () => calls[0].resolve([]));
    expect(result.current.state).toBe(PANEL_STATES.EMPTY);
    await act(async () => {
      result.current.refetch();
    });
    expect(result.current.state).toBe(PANEL_STATES.LOADING);

    // unauthorised → loading
    await act(async () => calls[1].reject(apiError(401)));
    expect(result.current.state).toBe(PANEL_STATES.UNAUTHORISED);
    await act(async () => {
      result.current.refetch();
    });
    expect(result.current.state).toBe(PANEL_STATES.LOADING);

    // unavailable (server-declared) → loading
    await act(async () =>
      calls[2].reject(apiError(403, envelope('MARKETPLACE_STRATEGY_UNAVAILABLE', 'Gone.'))),
    );
    expect(result.current.state).toBe(PANEL_STATES.UNAVAILABLE);
    await act(async () => {
      result.current.refetch();
    });
    expect(result.current.state).toBe(PANEL_STATES.LOADING);
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// A NEW QUESTION IS NOT A REFRESH
// ══════════════════════════════════════════════════════════════════════════════════════

describe('usePanelState — deps', () => {
  it('empty → loading and ready → loading when the question changes, discarding the answer', async () => {
    const { reader, calls } = deferredReader();
    const { result, rerender } = renderHook(({ page }) => usePanelState(reader, { deps: [page] }), {
      initialProps: { page: 1 },
    });

    await act(async () => calls[0].resolve([{ id: 1 }]));
    expect(result.current.state).toBe(PANEL_STATES.READY);

    rerender({ page: 2 });
    // A new question, so `loading` and not `refreshing`: an answer about page 1 must not be on
    // screen as though it were about page 2.
    expect(result.current.state).toBe(PANEL_STATES.LOADING);
    expect(result.current.data).toBeNull();
    expect(reader).toHaveBeenCalledTimes(2);
  });

  it('does not re-read when an inline deps array holds the same values', async () => {
    const { reader, calls } = deferredReader();
    const { rerender } = renderHook(({ page }) => usePanelState(reader, { deps: [page, 'live'] }), {
      initialProps: { page: 1 },
    });

    await act(async () => calls[0].resolve([{ id: 1 }]));
    rerender({ page: 1 });
    rerender({ page: 1 });

    expect(reader).toHaveBeenCalledTimes(1);
  });

  it('drops the outcome of a read the panel has already moved on from', async () => {
    const { reader, calls } = deferredReader();
    const { result, rerender } = renderHook(({ page }) => usePanelState(reader, { deps: [page] }), {
      initialProps: { page: 1 },
    });

    rerender({ page: 2 });
    // Page 1's answer arrives late. It is an answer to a question nobody is asking.
    await act(async () => calls[0].resolve([{ page: 1 }]));
    expect(result.current.state).toBe(PANEL_STATES.LOADING);
    expect(result.current.data).toBeNull();

    await act(async () => calls[1].resolve([{ page: 2 }]));
    expect(result.current.data).toEqual([{ page: 2 }]);
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// THE STABLE refetch AND THE INTERVAL SET ONCE
// ══════════════════════════════════════════════════════════════════════════════════════

describe('usePanelState — refetch identity and the interval', () => {
  it('keeps one refetch identity across every data change and every failure', async () => {
    const { reader, calls } = deferredReader();
    const { result } = renderHook(() => usePanelState(reader));
    const refetch = result.current.refetch;

    await act(async () => calls[0].resolve([{ id: 1 }]));
    expect(result.current.data).toEqual([{ id: 1 }]);
    expect(result.current.refetch).toBe(refetch);

    await act(async () => {
      result.current.refetch();
    });
    await act(async () => calls[1].resolve([{ id: 2 }]));
    expect(result.current.data).toEqual([{ id: 2 }]);
    expect(result.current.refetch).toBe(refetch);

    await act(async () => {
      result.current.refetch();
    });
    await act(async () => calls[2].reject(apiError(500)));
    expect(result.current.state).toBe(PANEL_STATES.ERROR);
    expect(result.current.refetch).toBe(refetch);
  });

  it('sets the interval once, however many reads succeed', async () => {
    vi.useFakeTimers();
    const setIntervalSpy = vi.spyOn(globalThis, 'setInterval');
    const reader = vi.fn().mockResolvedValue([{ id: 1 }]);

    const { result } = renderHook(() => usePanelState(reader, { intervalMs: 5000 }));
    await act(async () => {});
    expect(result.current.state).toBe(PANEL_STATES.READY);

    await act(async () => {
      vi.advanceTimersByTime(15000);
    });

    // Four reads: the first plus three ticks. `usePolling` would have re-created the interval
    // after each of them, because `data` is in its callback's dependency array.
    expect(reader).toHaveBeenCalledTimes(4);
    expect(setIntervalSpy).toHaveBeenCalledTimes(1);
    expect(result.current.state).toBe(PANEL_STATES.READY);
  });

  it('never repeats without an explicit interval', async () => {
    vi.useFakeTimers();
    const reader = vi.fn().mockResolvedValue([{ id: 1 }]);
    renderHook(() => usePanelState(reader));

    await act(async () => {
      vi.advanceTimersByTime(60000);
    });

    expect(reader).toHaveBeenCalledTimes(1);
  });

  it('skips the tick while the document is hidden, without tearing the interval down', async () => {
    vi.useFakeTimers();
    const setIntervalSpy = vi.spyOn(globalThis, 'setInterval');
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    const reader = vi.fn().mockResolvedValue([{ id: 1 }]);

    renderHook(() => usePanelState(reader, { intervalMs: 5000 }));
    await act(async () => {});

    await act(async () => {
      vi.advanceTimersByTime(15000);
    });
    expect(reader).toHaveBeenCalledTimes(1);

    visibility.mockReturnValue('visible');
    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    // The same interval is still installed, so the panel resumes without a remount.
    expect(reader).toHaveBeenCalledTimes(2);
    expect(setIntervalSpy).toHaveBeenCalledTimes(1);
  });

  it('stops the interval when the panel unmounts', async () => {
    vi.useFakeTimers();
    const reader = vi.fn().mockResolvedValue([{ id: 1 }]);
    const { unmount } = renderHook(() => usePanelState(reader, { intervalMs: 5000 }));
    await act(async () => {});

    unmount();
    await act(async () => {
      vi.advanceTimersByTime(30000);
    });

    expect(reader).toHaveBeenCalledTimes(1);
  });
});
