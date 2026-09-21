/**
 * tests/unit/hooks/useLiveChannel.test.jsx
 *
 * vyomquant-ui-redesign task 5.7. `design.md` §13.1, §13.2(a). Requirements 2.6, 7.5.
 *
 * WHAT THIS FILE HOLDS IN PLACE
 * -----------------------------
 * 1. **One `wsClient.subscribe` per channel, however many components ask.** The point of
 *    §13.2 is to move subscriptions down to the leaf that renders the value; without
 *    sharing, a table of thirty rows would open thirty subscriptions and the cure would be
 *    worse than the disease.
 * 2. **A tick a component did not select does not re-render it.** Asserted by counting
 *    renders, because that is the only thing the requirement is actually about — a
 *    `pnl` tick for BTC/USDT must cost the ETH/USDT row nothing.
 * 3. **The reference count is right at both ends.** The last consumer to leave releases
 *    the underlying subscription; anyone still holding it keeps it; a component that
 *    remounts re-acquires. A leak here is a channel handler that outlives every consumer
 *    and calls `setState` on dead trees for the rest of the session.
 *
 * `wsClient` is a double: the real one constructs a `WebSocket`, and what is under test is
 * this hook's arithmetic over the client's `subscribe` contract, not the client's own
 * routing (which `builderRealtime.test.jsx` covers against a fake socket). The registry,
 * the selector gate and the reference counting are all real.
 */

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, cleanup, render, renderHook, screen } from '@testing-library/react';

const { mockWsClient, subscribeImpl } = vi.hoisted(() => {
  /** channel → Set of the handlers `subscribe` was called with. */
  const handlers = new Map();
  const client = {
    handlers,
    subscribe: vi.fn(),
    /** Deliver one frame the way `processMessage` does. */
    deliver(channel, message) {
      for (const handler of Array.from(handlers.get(channel) || [])) handler(message);
    },
    /** How many handlers the client is holding for `channel`. */
    handlerCount(channel) {
      return (handlers.get(channel) || new Set()).size;
    },
  };
  return {
    mockWsClient: client,
    // Mirrors the real `subscribe`: a routing-table entry plus an unsubscribe that removes
    // exactly this callback and drops the entry when it empties.
    subscribeImpl: (channel, handler) => {
      if (!handlers.has(channel)) handlers.set(channel, new Set());
      handlers.get(channel).add(handler);
      return () => {
        const held = handlers.get(channel);
        if (!held) return;
        held.delete(handler);
        if (held.size === 0) handlers.delete(channel);
      };
    },
  };
});

vi.mock('../../../src/websocketClient', () => ({
  default: mockWsClient,
}));

import {
  activeLiveChannels,
  liveChannelSubscriberCount,
  resetLiveChannels,
  useLiveChannel,
} from '../../../src/hooks/useLiveChannel';

// ---------------------------------------------------------------------------
// Fixtures — module-scope selectors, which is the documented stable form
// ---------------------------------------------------------------------------

const PNL = 'pnl';
const POSITIONS = 'positions';

const markFor = (symbol) => (message) =>
  message.symbol === symbol ? message.mark_price : undefined;

/** A selector that keeps the previous value for a frame it does not care about. */
const pickBtcMark = markFor('BTC/USDT');
const pickEthMark = markFor('ETH/USDT');

const pnlTick = (symbol, mark) => ({ type: PNL, symbol, mark_price: mark });

beforeEach(() => {
  resetLiveChannels();
  mockWsClient.handlers.clear();
  // Reinstalled rather than merely cleared, so `restoreAllMocks` in the teardown cannot
  // leave the next test with a `subscribe` that returns no unsubscribe.
  mockWsClient.subscribe.mockReset();
  mockWsClient.subscribe.mockImplementation(subscribeImpl);
});

afterEach(() => {
  cleanup();
  resetLiveChannels();
  vi.restoreAllMocks();
});

// ═══════════════════════════════════════════════════════════════════════════
// 1. One subscription per channel, whatever the subscriber count
// ═══════════════════════════════════════════════════════════════════════════

describe('one wsClient.subscribe per channel (§13.2a)', () => {
  it('subscribes once for one consumer', () => {
    renderHook(() => useLiveChannel(PNL, pickBtcMark, null));

    expect(mockWsClient.subscribe).toHaveBeenCalledTimes(1);
    expect(mockWsClient.subscribe.mock.calls[0][0]).toBe(PNL);
    expect(mockWsClient.handlerCount(PNL)).toBe(1);
  });

  it('subscribes once for twelve consumers of the same channel', () => {
    const hooks = [];
    for (let i = 0; i < 12; i += 1) {
      hooks.push(renderHook(() => useLiveChannel(PNL, pickBtcMark, null)));
    }

    // The client holds ONE handler. Twelve would defeat the purpose of moving the
    // subscription into the leaf in the first place.
    expect(mockWsClient.subscribe).toHaveBeenCalledTimes(1);
    expect(mockWsClient.handlerCount(PNL)).toBe(1);
    // The registry, meanwhile, knows there are twelve holds to release.
    expect(liveChannelSubscriberCount(PNL)).toBe(12);
    for (const hook of hooks) hook.unmount();
  });

  it('subscribes once per channel and keeps two channels apart', () => {
    renderHook(() => useLiveChannel(PNL, pickBtcMark, null));
    renderHook(() => useLiveChannel(POSITIONS, (m) => m.qty, 0));

    expect(mockWsClient.subscribe.mock.calls.map((call) => call[0]).sort()).toEqual(
      [PNL, POSITIONS].sort(),
    );
    expect(activeLiveChannels().sort()).toEqual([PNL, POSITIONS].sort());
  });

  it('subscribes to nothing for a falsy channel, and returns the initial value', () => {
    // A leaf with no id to watch yet is a normal state, not a special case at the call site.
    const { result } = renderHook(() => useLiveChannel('', pickBtcMark, 'not-yet'));

    expect(mockWsClient.subscribe).not.toHaveBeenCalled();
    expect(activeLiveChannels()).toEqual([]);
    expect(result.current).toBe('not-yet');
  });

  it('delivers one frame to every consumer of the channel', () => {
    const a = renderHook(() => useLiveChannel(PNL, pickBtcMark, null));
    const b = renderHook(() => useLiveChannel(PNL, pickBtcMark, null));

    act(() => {
      mockWsClient.deliver(PNL, pnlTick('BTC/USDT', 101));
    });

    // Sharing the subscription must not mean sharing the delivery.
    expect(a.result.current).toBe(101);
    expect(b.result.current).toBe(101);
  });

  it('keeps delivering to the other consumers when one selector throws', () => {
    // With one subscription per component this was the client's problem, and it guards
    // each callback. Collapsing N subscriptions into one moves it here: a throw partway
    // through the fan-out would silently freeze every consumer after it.
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const exploding = () => {
      throw new Error('cannot read this frame');
    };

    const first = renderHook(() => useLiveChannel(PNL, exploding, 'untouched'));
    const second = renderHook(() => useLiveChannel(PNL, pickBtcMark, null));

    act(() => {
      mockWsClient.deliver(PNL, pnlTick('BTC/USDT', 7));
    });

    expect(first.result.current).toBe('untouched');
    expect(second.result.current).toBe(7);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 2. The selector gate — Object.is, before any render is scheduled
// ═══════════════════════════════════════════════════════════════════════════

describe('re-renders only when the selected value changed', () => {
  it('returns only the selected slice, not the frame', () => {
    const { result } = renderHook(() => useLiveChannel(PNL, pickBtcMark, null));

    act(() => {
      mockWsClient.deliver(PNL, pnlTick('BTC/USDT', 42));
    });

    expect(result.current).toBe(42);
  });

  it('does not re-render a consumer whose selected value is Object.is-equal', () => {
    let renders = 0;
    const { result } = renderHook(() => {
      renders += 1;
      return useLiveChannel(PNL, pickBtcMark, null);
    });
    const afterMount = renders;

    act(() => {
      mockWsClient.deliver(PNL, pnlTick('BTC/USDT', 42));
    });
    const afterFirstTick = renders;

    // The same price again. A different frame object, an equal selected value.
    act(() => {
      mockWsClient.deliver(PNL, pnlTick('BTC/USDT', 42));
    });

    expect(result.current).toBe(42);
    expect(afterFirstTick).toBe(afterMount + 1);
    expect(renders).toBe(afterFirstTick);
  });

  it('does not re-render an ETH row for a BTC tick (§13.2a, the named case)', () => {
    let ethRenders = 0;
    let btcRenders = 0;

    const eth = renderHook(() => {
      ethRenders += 1;
      return useLiveChannel(PNL, pickEthMark, undefined);
    });
    const btc = renderHook(() => {
      btcRenders += 1;
      return useLiveChannel(PNL, pickBtcMark, undefined);
    });
    const ethBaseline = ethRenders;
    const btcBaseline = btcRenders;

    act(() => {
      mockWsClient.deliver(PNL, pnlTick('BTC/USDT', 30000));
    });

    expect(btc.result.current).toBe(30000);
    expect(btcRenders).toBe(btcBaseline + 1);
    // The whole reason this hook exists. `pickEthMark` returned `undefined`, which is
    // `Object.is`-equal to its previous value, so no render was even scheduled.
    expect(eth.result.current).toBeUndefined();
    expect(ethRenders).toBe(ethBaseline);
  });

  it('re-renders once, not twice, for two ticks inside one batch that differ', () => {
    // The gate reads a ref rather than the committed state, so a second frame arriving
    // inside the same React batch is compared against the first frame's value, not against
    // the value from the last commit.
    let renders = 0;
    const { result } = renderHook(() => {
      renders += 1;
      return useLiveChannel(PNL, pickBtcMark, null);
    });
    const baseline = renders;

    act(() => {
      mockWsClient.deliver(PNL, pnlTick('BTC/USDT', 1));
      mockWsClient.deliver(PNL, pnlTick('BTC/USDT', 1));
      mockWsClient.deliver(PNL, pnlTick('BTC/USDT', 2));
    });

    expect(result.current).toBe(2);
    expect(renders).toBe(baseline + 1);
  });

  it('re-renders for a new object even when it is deep-equal, since the gate is Object.is', () => {
    // Stated so it is a decision rather than a surprise: a selector returning a fresh
    // object per frame defeats the gate. Selectors return primitives, or a memoised slice.
    let renders = 0;
    const pickPair = (m) => ({ mark: m.mark_price });
    renderHook(() => {
      renders += 1;
      return useLiveChannel(PNL, pickPair, null);
    });
    const baseline = renders;

    act(() => {
      mockWsClient.deliver(PNL, pnlTick('BTC/USDT', 5));
    });
    act(() => {
      mockWsClient.deliver(PNL, pnlTick('BTC/USDT', 5));
    });

    expect(renders).toBe(baseline + 2);
  });

  it('renders the initial value until a frame changes it', () => {
    const { result } = renderHook(() => useLiveChannel(PNL, pickBtcMark, 'no data yet'));

    expect(result.current).toBe('no data yet');

    act(() => {
      mockWsClient.deliver(PNL, pnlTick('ETH/USDT', 1));
    });

    // A frame for another symbol is not this row's data. It stays as it was.
    expect(result.current).toBe('no data yet');
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 2b. A declined frame does not wipe the value it had
// ═══════════════════════════════════════════════════════════════════════════

describe('a selector returning undefined declines the frame', () => {
  it('keeps a price already on screen when another symbol ticks', () => {
    // The defect this pins: read literally against an `Object.is` gate, the ordinary
    // filtering selector returns `undefined` for a frame it does not want, `undefined` is
    // not equal to 3200.5, and the row blanks on every other symbol's tick — the failure
    // §13 exists to prevent, inverted.
    const { result } = renderHook(() => useLiveChannel(PNL, pickEthMark, null));

    act(() => {
      mockWsClient.deliver(PNL, pnlTick('ETH/USDT', 3200.5));
    });
    expect(result.current).toBe(3200.5);

    act(() => {
      mockWsClient.deliver(PNL, pnlTick('BTC/USDT', 30000));
      mockWsClient.deliver(PNL, pnlTick('SOL/USDT', 140));
    });

    expect(result.current).toBe(3200.5);
  });

  it('schedules no render for a declined frame', () => {
    let renders = 0;
    renderHook(() => {
      renders += 1;
      return useLiveChannel(PNL, pickEthMark, 'idle');
    });
    const baseline = renders;

    act(() => {
      mockWsClient.deliver(PNL, pnlTick('BTC/USDT', 1));
    });

    expect(renders).toBe(baseline);
  });

  it('clears a figure when the selector returns null, which is how "gone" is said', () => {
    const pickOrNull = (m) => (m.symbol === 'ETH/USDT' ? m.mark_price : null);
    const { result } = renderHook(() => useLiveChannel(PNL, pickOrNull, 'idle'));

    act(() => {
      mockWsClient.deliver(PNL, pnlTick('ETH/USDT', 10));
    });
    expect(result.current).toBe(10);

    act(() => {
      mockWsClient.deliver(PNL, pnlTick('BTC/USDT', 20));
    });

    // `null` is a value like any other and passes the gate. The overload is on `undefined`
    // alone, and `null` is what the `Metric` not-available marker reads anyway.
    expect(result.current).toBeNull();
  });

  it('hands the previous selected value to the selector as its second argument', () => {
    // For a selector that wants to be explicit, or to derive from what it had.
    const runningMax = (m, previous) =>
      previous === null || m.mark_price > previous ? m.mark_price : previous;
    const { result } = renderHook(() => useLiveChannel(PNL, runningMax, null));

    act(() => {
      mockWsClient.deliver(PNL, pnlTick('BTC/USDT', 5));
    });
    act(() => {
      mockWsClient.deliver(PNL, pnlTick('BTC/USDT', 3));
    });
    act(() => {
      mockWsClient.deliver(PNL, pnlTick('BTC/USDT', 9));
    });

    expect(result.current).toBe(9);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 3. An unstable selector is handled, not punished
// ═══════════════════════════════════════════════════════════════════════════

describe('selector stability', () => {
  it('causes no resubscribe when the selector identity changes every render', () => {
    // The documented contract asks for a module-scope or `useCallback` selector. A caller
    // who ignores it gets working behaviour rather than a subscribe/unsubscribe storm.
    const { rerender } = renderHook(
      ({ symbol }) =>
        useLiveChannel(PNL, (m) => (m.symbol === symbol ? m.mark_price : undefined), null),
      { initialProps: { symbol: 'BTC/USDT' } },
    );

    rerender({ symbol: 'BTC/USDT' });
    rerender({ symbol: 'BTC/USDT' });

    expect(mockWsClient.subscribe).toHaveBeenCalledTimes(1);
    expect(mockWsClient.handlerCount(PNL)).toBe(1);
  });

  it('uses the newest selector for the next frame', () => {
    const { result, rerender } = renderHook(
      ({ symbol }) =>
        useLiveChannel(PNL, (m) => (m.symbol === symbol ? m.mark_price : undefined), null),
      { initialProps: { symbol: 'BTC/USDT' } },
    );

    act(() => {
      mockWsClient.deliver(PNL, pnlTick('BTC/USDT', 100));
    });
    expect(result.current).toBe(100);

    rerender({ symbol: 'ETH/USDT' });
    act(() => {
      mockWsClient.deliver(PNL, pnlTick('ETH/USDT', 200));
    });

    // The value follows the changed selector on the next frame — which is precisely the
    // caveat the module docblock states, and why `useCallback` over the changing prop is
    // the documented form.
    expect(result.current).toBe(200);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 4. Reference counting — release at zero, re-acquire on remount
// ═══════════════════════════════════════════════════════════════════════════

describe('reference counting', () => {
  it('releases the underlying subscription when the last consumer leaves', () => {
    const only = renderHook(() => useLiveChannel(PNL, pickBtcMark, null));
    expect(mockWsClient.handlerCount(PNL)).toBe(1);

    only.unmount();

    expect(mockWsClient.handlerCount(PNL)).toBe(0);
    expect(liveChannelSubscriberCount(PNL)).toBe(0);
    expect(activeLiveChannels()).toEqual([]);
  });

  it('keeps the subscription while any consumer still holds it', () => {
    const a = renderHook(() => useLiveChannel(PNL, pickBtcMark, null));
    const b = renderHook(() => useLiveChannel(PNL, pickBtcMark, null));
    const c = renderHook(() => useLiveChannel(PNL, pickBtcMark, null));

    a.unmount();
    expect(mockWsClient.handlerCount(PNL)).toBe(1);
    expect(liveChannelSubscriberCount(PNL)).toBe(2);

    b.unmount();
    expect(mockWsClient.handlerCount(PNL)).toBe(1);
    expect(liveChannelSubscriberCount(PNL)).toBe(1);

    c.unmount();
    // Only now. Releasing at the first unmount is the bug this counts to avoid: the two
    // remaining rows would have gone quiet while still on screen.
    expect(mockWsClient.handlerCount(PNL)).toBe(0);
  });

  it('re-acquires a fresh subscription when a consumer remounts', () => {
    const first = renderHook(() => useLiveChannel(PNL, pickBtcMark, null));
    first.unmount();
    expect(mockWsClient.handlerCount(PNL)).toBe(0);

    const second = renderHook(() => useLiveChannel(PNL, pickBtcMark, null));

    expect(mockWsClient.subscribe).toHaveBeenCalledTimes(2);
    expect(mockWsClient.handlerCount(PNL)).toBe(1);

    act(() => {
      mockWsClient.deliver(PNL, pnlTick('BTC/USDT', 9));
    });
    expect(second.result.current).toBe(9);
  });

  it('leaves no handler behind after an unmount, so no dead listener is called', () => {
    let renders = 0;
    const { unmount } = renderHook(() => {
      renders += 1;
      return useLiveChannel(PNL, pickBtcMark, null);
    });
    unmount();
    const afterUnmount = renders;

    act(() => {
      mockWsClient.deliver(PNL, pnlTick('BTC/USDT', 123));
    });

    expect(renders).toBe(afterUnmount);
    expect(mockWsClient.handlerCount(PNL)).toBe(0);
  });

  it('moves the hold when the channel changes, and clears the stale slice', () => {
    const pickValue = (m) => m.value;
    const { result, rerender } = renderHook(({ channel }) => useLiveChannel(channel, pickValue, 'none'), {
      initialProps: { channel: PNL },
    });

    act(() => {
      mockWsClient.deliver(PNL, { type: PNL, value: 'from pnl' });
    });
    expect(result.current).toBe('from pnl');

    rerender({ channel: POSITIONS });

    // A value selected from `pnl` is not a value for `positions`. Carrying it over would
    // render a real-but-wrong figure as current, which is the Requirement 14.5 failure.
    expect(result.current).toBe('none');
    expect(mockWsClient.handlerCount(PNL)).toBe(0);
    expect(mockWsClient.handlerCount(POSITIONS)).toBe(1);
    expect(activeLiveChannels()).toEqual([POSITIONS]);
  });

  it('survives a mixed mount/unmount sequence without leaking or over-releasing', () => {
    const held = [];
    for (let i = 0; i < 5; i += 1) {
      held.push(renderHook(() => useLiveChannel(PNL, pickBtcMark, null)));
    }
    expect(liveChannelSubscriberCount(PNL)).toBe(5);

    held[1].unmount();
    held[3].unmount();
    held.push(renderHook(() => useLiveChannel(PNL, pickBtcMark, null)));
    held.push(renderHook(() => useLiveChannel(PNL, pickBtcMark, null)));

    expect(liveChannelSubscriberCount(PNL)).toBe(5);
    // Still the one handler it started with: the channel never emptied, so it was never
    // re-subscribed.
    expect(mockWsClient.subscribe).toHaveBeenCalledTimes(1);
    expect(mockWsClient.handlerCount(PNL)).toBe(1);

    for (const hook of held) hook.unmount();

    expect(liveChannelSubscriberCount(PNL)).toBe(0);
    expect(mockWsClient.handlerCount(PNL)).toBe(0);
    expect(activeLiveChannels()).toEqual([]);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 5. In a tree — the shape §13.2(b) actually asks for
// ═══════════════════════════════════════════════════════════════════════════

describe('subscribed at the leaf, in a real tree', () => {
  it('re-renders one row for a tick naming one symbol, and not its siblings', () => {
    const rowRenders = { 'BTC/USDT': 0, 'ETH/USDT': 0, 'SOL/USDT': 0 };
    let pageRenders = 0;

    const selectors = {
      'BTC/USDT': pickBtcMark,
      'ETH/USDT': pickEthMark,
      'SOL/USDT': markFor('SOL/USDT'),
    };

    const Row = React.memo(function Row({ symbol }) {
      rowRenders[symbol] += 1;
      const mark = useLiveChannel(PNL, selectors[symbol], null);
      return <div data-testid={symbol}>{mark === null ? '—' : String(mark)}</div>;
    });

    function Page() {
      pageRenders += 1;
      // The page root holds only the structural data: which symbols exist. That set
      // changes on a REST read, not on a tick.
      return (
        <div>
          {Object.keys(selectors).map((symbol) => (
            <Row key={symbol} symbol={symbol} />
          ))}
        </div>
      );
    }

    render(<Page />);
    const baseline = { ...rowRenders };
    const pageBaseline = pageRenders;

    act(() => {
      mockWsClient.deliver(PNL, pnlTick('ETH/USDT', 3200.5));
    });

    expect(screen.getByTestId('ETH/USDT').textContent).toBe('3200.5');
    expect(screen.getByTestId('BTC/USDT').textContent).toBe('—');
    expect(rowRenders['ETH/USDT']).toBe(baseline['ETH/USDT'] + 1);
    // The three assertions that are the point of §13: the page tree did not re-render,
    // and neither did the two rows the tick said nothing about.
    expect(pageRenders).toBe(pageBaseline);
    expect(rowRenders['BTC/USDT']).toBe(baseline['BTC/USDT']);
    expect(rowRenders['SOL/USDT']).toBe(baseline['SOL/USDT']);
    // Three rows, one subscription.
    expect(mockWsClient.handlerCount(PNL)).toBe(1);
  });

  it('releases the channel when the tree unmounts', () => {
    function Row() {
      const mark = useLiveChannel(PNL, pickBtcMark, null);
      return <span>{String(mark)}</span>;
    }
    const { unmount } = render(
      <div>
        <Row />
        <Row />
      </div>,
    );
    expect(liveChannelSubscriberCount(PNL)).toBe(2);

    unmount();

    expect(liveChannelSubscriberCount(PNL)).toBe(0);
    expect(mockWsClient.handlerCount(PNL)).toBe(0);
  });
});
