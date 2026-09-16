/**
 * @fileoverview Live Trading — a `STRATEGY_STATUS` push reaches tier 1 with no clock advanced.
 *
 * vyomquant-ui-redesign task 20.5, for task 20.2's wiring. design.md §7.5, §13.2, §13.2b.
 * Requirements 7.5, 14.5.
 *
 * WHAT "BY CONSTRUCTION" MEANS HERE, AND WHY THE TIMERS ARE FAKE
 * =============================================================
 * Requirement 7.5: a deployment that stops, errors or disconnects is reflected within five
 * seconds. A poll satisfies that only by being fast enough — which means an interval tuned
 * against a requirement, and re-tuned whenever the payload grows. A push satisfies it by
 * construction: there is no timer in the path at all.
 *
 * So the timers here are fake and the point is what is NOT done with them. Every frame below is
 * pushed with the clock FROZEN by `vi.useFakeTimers({shouldAdvanceTime: false})` and advanced
 * by ZERO — never by 5000ms, never by anything — and the state is asserted in the same
 * synchronous turn. Under a polled implementation that cannot pass: with the clock frozen no
 * interval ever fires. Under a pushed one it passes, because the frame is handed to
 * `setSelected` inside the socket callback. That inversion is the whole test — if a change to
 * the wiring makes it necessary to advance a clock to get green, the wiring became a poll.
 *
 * The clock is frozen AROUND THE PUSH and not around the mount, and the boundary is exact:
 * the four REST reads that seed the page are a settle, not part of Requirement 7.5's bound,
 * and `@testing-library/react` v14's `waitFor` only recognises Jest's fake clock — under
 * `vi`'s it would poll a `setInterval` that never fires and hang. So {@link mountAndSettle}
 * finishes on the real clock and freezes it before returning, and nothing after that point
 * awaits anything. Every assertion about a pushed state is therefore synchronous, with
 * `Date.now()` verified not to have moved.
 *
 * ALL THREE OF STOP, ERROR AND DISCONNECT
 * ---------------------------------------
 * Requirement 7.5 names three transitions and they are not interchangeable: `error` carries a
 * reason the worker wrote and the other two do not, and `disconnected` is the one a reader is
 * most likely to confuse with the exchange-connection figure beside it. Each gets its own
 * case, and the fourth case pushes all three in sequence to show the slot tracks the latest
 * rather than latching on the first.
 *
 * WHAT IS QUERIED, AND WHAT IS DELIBERATELY NOT
 * ---------------------------------------------
 * `data-region="connectionState"` (spelled from `pageFields`, not typed here) and
 * `role="status"` with its accessible name. No class names anywhere: a refactor that renames a
 * utility class must not fail this file, and a refactor that drops the subscription must. The
 * teeth check for that is task 20.5's own — dropping the selector's channel makes every case
 * below fail on the element being absent.
 *
 * REQUIREMENT 14.5 IS ASSERTED IN THE SAME BREATH
 * -----------------------------------------------
 * The pushed run state and the read connection figure are different facts: the figure is
 * `exchange.exchanges[].status`, which the aggregation service returns as a constant
 * `"connected"` and which therefore says an exchange KEY is configured. So every case asserts
 * BOTH that the push arrived AND that it did not overwrite the connection figure — a wiring
 * that wrote `stopped` into a slot labelled *Connection* would satisfy the first half of
 * Requirement 7.5 by making a false statement about the venue's key.
 *
 * `websocketClient` IS A CONTROLLABLE DOUBLE
 * ------------------------------------------
 * The real client cannot be handed a frame and jsdom has no WebSocket to receive one. The
 * double keeps a SET of callbacks per event type, because `useLiveChannel`'s registry
 * deliberately holds ONE `wsClient.subscribe` per channel and a double that overwrote its
 * predecessor would hide exactly the sharing this design depends on
 * (`tests/unit/dashboard/tickIsolation.test.jsx` established the shape).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import LiveTrading from '../../../src/pages/LiveTrading';
import * as dashboardModule from '../../../src/api/modules/dashboard';
import { ordersApi } from '../../../src/api/modules/orders';
import { strategiesApi } from '../../../src/api/modules/strategies';
import { PAGES, PAGE_FIELD_BY_KEY, pageFieldKey } from '../../../src/design/pageFields';
import { tierSelector } from '../../../src/design/pageHierarchy';
import { resetLiveChannels, liveChannelSubscriberCount } from '../../../src/hooks/useLiveChannel';

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE SOCKET DOUBLE
 * ══════════════════════════════════════════════════════════════════════════════════════ */

const socket = vi.hoisted(() => {
  const handlers = new Map();
  return {
    handlers,
    subscribe: (eventType, callback) => {
      if (!handlers.has(eventType)) handlers.set(eventType, new Set());
      handlers.get(eventType).add(callback);
      return () => {
        const set = handlers.get(eventType);
        if (set) set.delete(callback);
      };
    },
    /** Fans a frame out the way `websocketClient.processMessage` does. */
    emit: (eventType, frame) => {
      const set = handlers.get(eventType);
      if (!set) return 0;
      for (const callback of Array.from(set)) callback(frame);
      return set.size;
    },
    reset: () => handlers.clear(),
  };
});

vi.mock('../../../src/websocketClient', () => ({
  default: {
    subscribe: (eventType, callback) => socket.subscribe(eventType, callback),
    onOpen: () => () => {},
    onStatusChange: () => () => {},
    getStatus: () => 'connected',
    send: () => {},
  },
}));

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE DECLARATION AND THE FIXTURE
 * ══════════════════════════════════════════════════════════════════════════════════════ */

const declared = (field) => PAGE_FIELD_BY_KEY[pageFieldKey({ page: PAGES.LIVE_TRADING, field })];

/**
 * The channel, read off the declaration rather than typed here.
 *
 * `deploymentStopped`'s `read` is `wsClient.subscribe('STRATEGY_STATUS')`, so this test
 * subscribes the double to whatever the declaration says the page reads — which is what makes
 * the case a lower-case `strategy_status` would produce a failure rather than a silent pass
 * (the defect task 19.3 found).
 */
const STRATEGY_STATUS_CHANNEL = declared('deploymentStopped').read.match(/'([^']+)'/)[1];

const STRATEGY_ID = 's1';

/** One configured venue key, one open live position, one strategy. */
const dashboardBody = () => ({
  environment: 'live',
  exchange: {
    total_exchanges: 1,
    connected_exchanges: 1,
    exchanges: [{ exchange_id: 'binance', status: 'connected', latency_ms: 35, last_sync: null }],
  },
  positions: [
    {
      id: 'p1',
      symbol: 'BTC/USDT',
      environment: 'live',
      market_type: 'future',
      side: 'long',
      contracts: 0.25,
      entry_price: 61000,
      mark_price: 62000,
      notional: 15500,
      unrealized_pnl: 250.5,
      unrealized_pnl_pct: 1.64,
      liquidation_price: 49600,
    },
  ],
  overview: { today_realized_pnl: 412.75, currency: 'USDT' },
  risk: { risk_level: 'elevated' },
  executions: [],
  degraded: null,
});

const strategiesBody = () => ({
  strategies: [{ id: STRATEGY_ID, name: 'Momentum Breakout', version: 4, symbol: 'BTC/USDT' }],
  total: 1,
});

const allRead = () => {
  vi.spyOn(dashboardModule.dashboardApi, 'getDashboard').mockResolvedValue(dashboardBody());
  vi.spyOn(strategiesApi, 'list').mockResolvedValue(strategiesBody());
  vi.spyOn(ordersApi, 'getOpenOrders').mockResolvedValue([]);
  vi.spyOn(strategiesApi, 'listDeployments').mockImplementation(
    (strategyId) => Promise.resolve({ strategy_id: strategyId, deployments: [], total: 0 }),
  );
};

const mount = () => render(<MemoryRouter><LiveTrading /></MemoryRouter>);

/* ══════════════════════════════════════════════════════════════════════════════════════
 * DOM HELPERS — by `data-region` and by role, never by class
 * ══════════════════════════════════════════════════════════════════════════════════════ */

const tierOneContainer = () => document.querySelector(tierSelector(PAGES.LIVE_TRADING, 1));

/** Tier 1's connection element — the slot §7.5 assigns the push to. */
const connectionSlot = () =>
  tierOneContainer()?.querySelector('[data-region="connectionState"]') ?? null;

/** The READ figure inside it, which no frame may move (Requirement 14.5). */
const connectionFigure = () => {
  const metric = connectionSlot().querySelector('[data-metric-tier]');
  return metric.getAttribute('data-metric-available') === 'true'
    ? metric.textContent.replace(declared('connectionState').label, '').trim()
    : null;
};

/**
 * The pushed reading, queried by role and accessible name.
 *
 * `role="status"` because it appears with no action from the trader, and the accessible name
 * is `deploymentStopped`'s own declared label — so the element is found by what it MEANS
 * rather than by how it is styled.
 */
const pushedReading = () =>
  screen.queryByRole('status', { name: declared('deploymentStopped').label });

/**
 * One frame on the channel, delivered with the clock frozen and asserted to have cost no time.
 *
 * There is no `vi.advanceTimersByTime` anywhere in this file, and this is where that is
 * enforced rather than only claimed: `Date.now()` is read either side of the emit and must be
 * identical. `act` flushes React's own work, which is not a timer — the state update is
 * enqueued from inside the socket callback.
 */
const push = (frame) => {
  const before = Date.now();
  act(() => {
    socket.emit(STRATEGY_STATUS_CHANNEL, frame);
  });
  expect(Date.now() - before, 'the clock moved while delivering a pushed frame').toBe(0);
};

const frameFor = (status, extra = {}) => ({
  type: STRATEGY_STATUS_CHANNEL,
  strategy_id: STRATEGY_ID,
  status,
  ...extra,
});

/**
 * The page mounted, all four reads answered, the leaf holding the channel — and then the clock
 * frozen.
 *
 * The subscriber-count wait is what makes a push deterministic rather than racy: a frame
 * emitted before the leaf has taken its hold reaches nobody, and `useLiveChannel` keeps the
 * selected value rather than the last raw frame, so a dropped frame does not arrive later.
 *
 * The trailing microtask flushes settle the deployment fan-out, which is the one read whose
 * answer arrives after tier 1 exists. Flushing it BEFORE the freeze is what keeps a REST
 * answer from landing mid-case and being mistaken for something a push did.
 */
const mountAndSettle = async () => {
  mount();
  await waitFor(() => expect(tierOneContainer()).not.toBeNull());
  await waitFor(() => expect(liveChannelSubscriberCount(STRATEGY_STATUS_CHANNEL))
    .toBeGreaterThan(0));
  await waitFor(() => expect(strategiesApi.listDeployments).toHaveBeenCalled());
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });

  // From here on the clock does not move, and nothing below awaits anything.
  vi.useFakeTimers({ shouldAdvanceTime: false });
};

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE CASES
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('LiveTrading — STRATEGY_STATUS reaches tier 1 with no clock advanced (task 20.5)', () => {
  beforeEach(() => {
    // Real timers for the mount and the four reads; {@link mountAndSettle} freezes the clock
    // before it returns, and every push below happens under the frozen one.
    vi.useRealTimers();
    cleanup();
    resetLiveChannels();
    socket.reset();
    vi.restoreAllMocks();
    allRead();
  });

  afterEach(() => {
    // Thawed BEFORE the unmount, so teardown is not itself waiting on a clock nothing moves.
    vi.useRealTimers();
    cleanup();
    resetLiveChannels();
    socket.reset();
  });

  it('subscribes tier 1 to the declared channel, and renders no pushed line before a frame', async () => {
    await mountAndSettle();

    // The subscription is the mechanism Requirement 7.5 rests on, so its absence is a
    // failure in its own right and not only a symptom of one.
    expect(liveChannelSubscriberCount(STRATEGY_STATUS_CHANNEL)).toBeGreaterThan(0);

    // Nothing pushed yet renders NO line at all — not a not-available marker. The socket
    // having said nothing is not a gap in a read (Requirement 14.5).
    expect(pushedReading()).toBeNull();
    expect(connectionFigure()).toContain('connected');
  });

  it('reflects a stop with the clock advanced by nothing at all', async () => {
    await mountAndSettle();

    push(frameFor('stopped'));

    // THE ASSERTION THIS FILE EXISTS FOR: on screen, inside tier 1's connection element,
    // without a single millisecond of the 5-second budget being spent — {@link push} checks
    // the clock itself.
    const reading = pushedReading();
    expect(reading).not.toBeNull();
    expect(connectionSlot().contains(reading)).toBe(true);
    expect(reading.textContent).toContain('stopped');

    // Requirement 14.5: the read figure is untouched. The exchange key is exactly as
    // configured as it was, and the worker's run state is a different fact.
    expect(connectionFigure()).toContain('connected');
    expect(connectionFigure()).not.toContain('stopped');
  });

  it('reflects an error, and renders the worker\'s own reason verbatim', async () => {
    await mountAndSettle();

    push(frameFor('error', { error: 'Exchange rejected the order: insufficient margin' }));

    const reading = pushedReading();
    expect(reading).not.toBeNull();
    expect(reading.textContent).toContain('error');
    // The only thing on the page that says WHY, so it is not paraphrased away.
    expect(reading.textContent).toContain('insufficient margin');
    expect(connectionFigure()).toContain('connected');
  });

  it('reflects a disconnect without touching the exchange-connection figure', async () => {
    await mountAndSettle();

    push(frameFor('disconnected'));

    const reading = pushedReading();
    expect(reading).not.toBeNull();
    expect(reading.textContent).toContain('disconnected');

    // The confusion this slot is most exposed to: a disconnected WORKER is not a
    // disconnected exchange key, and the figure must not start saying it is.
    expect(connectionFigure()).toContain('connected');
    expect(connectionFigure()).not.toContain('disconnected');
  });

  it('tracks the latest of stop, error and disconnect, and never latches on the first', async () => {
    await mountAndSettle();

    for (const status of ['stopped', 'error', 'disconnected']) {
      push(frameFor(status));
      expect(pushedReading().textContent).toContain(status);
    }

    // The last one stands, and the two before it are gone from the reading.
    const reading = pushedReading();
    expect(reading.textContent).toContain('disconnected');
    expect(reading.textContent).not.toContain('stopped');
  });

  it('declines a frame for another strategy, and a frame from another ledger', async () => {
    await mountAndSettle();

    push(frameFor('stopped', { strategy_id: 'some-other-strategy' }));
    expect(pushedReading()).toBeNull();

    // `components/trading/liveFrame.js`'s ledger gate: a PAPER frame may not move a LIVE
    // figure, and this leaf reads a frame through the same gate the three panels do.
    push(frameFor('stopped', { environment: 'paper' }));
    expect(pushedReading()).toBeNull();

    // The frame that IS about this deployment, on this ledger, still lands.
    push(frameFor('stopped'));
    expect(pushedReading().textContent).toContain('stopped');
  });

  it('does not blank a reported state on a later frame that carries no status', async () => {
    await mountAndSettle();

    push(frameFor('error', { error: 'worker exited' }));
    expect(pushedReading().textContent).toContain('error');

    // A selector returns `undefined` to DECLINE a frame, never `null`: `null` means "the
    // figure is gone", and a frame carrying no `status` is not evidence of that.
    push({ type: STRATEGY_STATUS_CHANNEL, strategy_id: STRATEGY_ID });
    expect(pushedReading().textContent).toContain('error');
  });

  it('holds ONE wsClient subscription for the channel however many leaves ask for it', async () => {
    await mountAndSettle();

    // Tier 1's leaf and `components/trading/ExecutionsPanel.jsx`'s two selectors are all on
    // this channel, and `useLiveChannel`'s registry is what keeps that one handler rather
    // than three (§13.2a). The double keeps a set, so a broken registry would show here.
    expect(socket.handlers.get(STRATEGY_STATUS_CHANNEL).size).toBe(1);
    expect(liveChannelSubscriberCount(STRATEGY_STATUS_CHANNEL)).toBeGreaterThan(1);
  });
});
