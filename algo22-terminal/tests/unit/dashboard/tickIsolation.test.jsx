/**
 * @fileoverview Dashboard — one tick re-renders one leaf, and nothing else.
 *
 * vyomquant-ui-redesign task 19.6, for task 19.3. design.md §13.1, §13.2, §13.2b.
 * Requirements 2.6, 7.5.
 *
 * WHAT THIS PINS, AND WHY IT HAS TO BE A RENDER-COUNT TEST
 * =======================================================
 * §13.1's defect is not visible in the DOM. Before task 19.3 this page held seven
 * `wsClient.subscribe` handlers in one page-root effect, each writing page-level state, so
 * a single `pnl` frame re-rendered the entire tree — four tier-1 `ds/Metric`s, three
 * `ds/DataTable`s and a recharts surface — in order to move one number. The rendered output
 * after that frame is IDENTICAL to the rendered output after a correctly isolated one. Only
 * the count of renders differs, so only a count can tell the two apart, and a test that
 * asserted the number on screen would have passed against the defect it exists to catch.
 *
 * Four claims, in the order task 19.6 states them:
 *
 *   1. **A `pnl` frame for one symbol re-renders the leaf watching THAT symbol.** Once, and
 *      with the frame's figures — so the subscription is not merely present but connected.
 *   2. **It re-renders nothing else.** Not the position row watching another symbol, not
 *      the strategy badge, not the venue row, not the realised-P&L cell in the orders table.
 *      `useLiveChannel` gates on `Object.is` before enqueuing a state update, so a frame for
 *      a symbol a leaf is not watching does not even schedule a render for it.
 *   3. **Chart components do not re-render at all** — on any frame, on any channel.
 *      `equity_curve` is a historical series refreshed by the ONE read, and a recharts
 *      re-render is the most expensive thing on this page (§13.2, task 19.3).
 *   4. **A poll does not recreate its interval.** `usePolling` lists `data` in its `fetch`
 *      dependency array, so a completed read tore its interval down and built a new one
 *      (§1.12). Asserted in both directions: this page creates no interval and no frame
 *      makes it create one, AND `usePanelState` — the hook that replaced it here — sets its
 *      interval exactly once across two completed polls. The second half is what stops the
 *      first from being vacuously true of a page that simply has no timer.
 *
 * HOW RENDERS ARE COUNTED
 * =======================
 * The three leaves task 19.3 introduced (`LivePositionPnl`, `LiveStrategyState`,
 * `LiveVenueHealth`) are internal to `pages/Dashboard.jsx` and are not exported — correctly,
 * since nothing outside the page composes them. Each renders exactly one `ds/` primitive
 * with the live value, so the primitive is stubbed with a counting double and its render
 * count IS its leaf's render count. That also makes the count meaningful in the other
 * direction: each leaf is `memo`, so a stub that renders is a leaf whose props or whose own
 * selected value actually moved.
 *
 * The stubs render their values as text, so "re-rendered once" and "re-rendered with the
 * frame's figure" are asserted together and a leaf cannot pass by re-rendering the value it
 * already had.
 *
 * `websocketClient` IS A CONTROLLABLE DOUBLE, NOT A REAL SOCKET
 * ------------------------------------------------------------
 * The real client has no way to inject a frame, and jsdom has no WebSocket to receive one.
 * The double is the same shape the page and `useLiveChannel` use — `subscribe` returning its
 * own unsubscribe, plus `onOpen`/`onStatusChange`/`getStatus` — and it keeps a SET of
 * callbacks per event type rather than one, because `useLiveChannel`'s registry deliberately
 * holds one `wsClient.subscribe` per channel and a double that overwrote its predecessor
 * would hide exactly the sharing this design depends on. `_emit` fans a frame out the way
 * `websocketClient.processMessage` does.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import Dashboard from '../../../src/pages/Dashboard';
import * as dashboardModule from '../../../src/api/modules/dashboard';
import { resetLiveChannels, liveChannelSubscriberCount } from '../../../src/hooks/useLiveChannel';
import { usePanelState } from '../../../src/hooks/usePanelState';

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE COUNTING DOUBLES
 * ══════════════════════════════════════════════════════════════════════════════════════ */

/**
 * `vi.hoisted`, because `vi.mock` factories are hoisted above every import and a factory
 * that closed over an ordinary module-level `const` would read it before it was initialised.
 */
const counters = vi.hoisted(() => ({
  /** One entry per `ds/PnLDisplay` render: `{label, value, percent}`. */
  pnl: [],
  /** One entry per `ds/StrategyStatus` render: `{status, health}`. */
  strategy: [],
  /** One entry per `ds/ExchangeStatus` render: `{exchange}`. */
  venue: [],
  /** One entry per `ds/Chart` render: its point count. */
  chart: [],
  reset() {
    this.pnl.length = 0;
    this.strategy.length = 0;
    this.venue.length = 0;
    this.chart.length = 0;
  },
}));

/**
 * The socket double. A SET of callbacks per event type — see the header.
 */
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

vi.mock('../../../src/components/ds/PnLDisplay', () => {
  const Stub = ({ value, percentValue, label }) => {
    counters.pnl.push({ label, value, percent: percentValue });
    return (
      <span data-pnl-stub={label} data-pnl-value={String(value)} data-pnl-percent={String(percentValue)} />
    );
  };
  return { __esModule: true, PnLDisplay: Stub, default: Stub };
});

vi.mock('../../../src/components/ds/StrategyStatus', () => {
  const Stub = ({ status, health }) => {
    counters.strategy.push({ status, health });
    return <span data-strategy-stub={String(status)} data-strategy-health={String(health)} />;
  };
  return {
    __esModule: true,
    StrategyStatus: Stub,
    default: Stub,
    STRATEGY_STATUSES: Object.freeze([]),
  };
});

vi.mock('../../../src/components/ds/ExchangeStatus', () => {
  const Stub = ({ exchange }) => {
    counters.venue.push({ exchange });
    return <span data-venue-stub={String(exchange)} />;
  };
  return { __esModule: true, ExchangeStatus: Stub, default: Stub, isMeasuredLatency: () => false };
});

/*
 * `ds/Chart` and not `recharts`: task 19.1b put the equity curve behind
 * `lazy(() => import('../components/ds/Chart'))`, so the page never imports recharts and
 * stubbing the module the page actually asks for is the boundary that exists. Claim 3 is
 * about this component's render count, which is why the stub counts rather than only
 * renders.
 */
vi.mock('../../../src/components/ds/Chart', () => {
  const Stub = (props) => {
    counters.chart.push(Array.isArray(props.data) ? props.data.length : -1);
    return <figure data-testid="chart" data-chart-points={Array.isArray(props.data) ? props.data.length : -1} />;
  };
  return { __esModule: true, Chart: Stub, default: Stub };
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE FIXTURE — two positions on two symbols, one strategy, one venue
 * ══════════════════════════════════════════════════════════════════════════════════════ */

/**
 * TWO symbols is the whole experiment. One position could not distinguish "re-rendered
 * because the frame was mine" from "re-rendered because a frame arrived".
 */
const BTC = 'BTC/USDT';
const ETH = 'ETH/USDT';

const STRATEGY_ID = 'strat_1';
const VENUE_ID = 'binance';

const position = (symbol, overrides = {}) => ({
  id: `pos_${symbol}`,
  exchange_id: VENUE_ID,
  environment: 'live',
  symbol,
  market_type: 'spot',
  margin_type: 'cross',
  side: 'long',
  contracts: 1,
  entry_price: 100,
  mark_price: 110,
  unrealized_pnl: 10,
  unrealized_pnl_pct: 9.09,
  liquidation_price: null,
  ...overrides,
});

const body = () => ({
  environment: 'live',
  overview: {
    total_value: 45250,
    today_pnl: 400,
    cumulative_pnl: 5250,
    currency: 'USDT',
  },
  positions: [position(BTC), position(ETH)],
  degraded: null,
  executions: [
    {
      id: 'fill_1',
      symbol: 'SOL/USDT',
      exchange_id: VENUE_ID,
      side: 'buy',
      price: 145.5,
      amount: 10,
      realized_pnl: 12.5,
      timestamp: '2026-08-26T12:00:00Z',
    },
  ],
  risk: {
    current_drawdown_pct_v2: 3.2,
    open_positions_count: 2,
    circuit_breaker_armed: true,
    kill_switch_active: false,
  },
  health: {
    exchange_api_latency_ms: 42,
    exchange_api_latency_status: 'optimal',
    order_state_sync_status: 'synchronized',
  },
  exchange: {
    total_exchanges: 1,
    connected_exchanges: 1,
    can_trade: true,
    exchanges: [
      { exchange_id: VENUE_ID, status: 'connected', latency_ms: 35, last_sync: '2026-08-26T12:00:00Z' },
    ],
  },
  strategies: {
    total: 1,
    active: 1,
    paused: 0,
    items: [
      { id: STRATEGY_ID, name: 'BTC Trend Follower', pair: BTC, status: 'running', health: 'healthy' },
    ],
  },
  recent_activity: {
    signals: [{ id: 'sig_1', time: '2026-08-26T11:59:00Z', text: 'Long BTC/USDT accepted by risk' }],
    insights: [],
  },
  equity_curve: [
    { timestamp: '2026-08-20T00:00:00Z', equity: 44000 },
    { timestamp: '2026-08-26T00:00:00Z', equity: 45250 },
  ],
});

const read = () => vi.spyOn(dashboardModule.dashboardApi, 'getDashboard');

const mount = () => render(<MemoryRouter><Dashboard /></MemoryRouter>);

/** Push one frame the way `websocketClient.processMessage` would. */
const emit = (eventType, frame) => {
  act(() => {
    socket.emit(eventType, frame);
  });
};

/**
 * Wait until the ONE read has answered, the projection effect has run, and the lazy
 * `ds/Chart` chunk has resolved — so the counts taken next are of a settled page and not of
 * a page still arriving.
 */
const settled = async () => {
  await waitFor(() => {
    const panel = document.querySelector('[data-region="openPositions"]');
    expect(panel).not.toBeNull();
    expect(['loading', 'idle']).not.toContain(panel.getAttribute('data-panel-state'));
  });
  await waitFor(() => expect(screen.getByTestId('chart')).toBeDefined());
  // The two position cells and the one order cell have all rendered by now.
  await waitFor(() => expect(pnlRenders('Unrealised P&L').length).toBe(2));
};

/** Every recorded `ds/PnLDisplay` render carrying `label`, in order. */
const pnlRenders = (label) => counters.pnl.filter((entry) => entry.label === label);

/** The recorded unrealised-P&L renders for one symbol's cell, identified by its seeded value. */
const cellRenders = (value) =>
  pnlRenders('Unrealised P&L').filter((entry) => Number(entry.value) === value);

/* ══════════════════════════════════════════════════════════════════════════════════════
 * ONE TICK, ONE LEAF
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('Dashboard tick isolation — a `pnl` frame re-renders one leaf', () => {
  beforeEach(() => {
    cleanup();
    resetLiveChannels();
    socket.reset();
    counters.reset();
    vi.restoreAllMocks();
    read().mockResolvedValue(body());
  });

  afterEach(() => {
    cleanup();
    resetLiveChannels();
    socket.reset();
  });

  it('re-renders the cell watching that symbol, with the frame\'s figures', async () => {
    mount();
    await settled();

    // Both cells were seeded from the read: `unrealized_pnl: 10` for each.
    expect(cellRenders(10).length).toBe(2);
    const before = pnlRenders('Unrealised P&L').length;

    emit('pnl', { symbol: BTC, unrealized_pnl: 1234.5, unrealized_pnl_pct: 4.25 });

    // Exactly ONE further render, and it carries the frame's figures — so the subscription
    // is connected and not merely installed.
    const after = pnlRenders('Unrealised P&L');
    expect(after.length).toBe(before + 1);
    expect(after[after.length - 1]).toEqual({
      label: 'Unrealised P&L',
      value: 1234.5,
      percent: 4.25,
    });
  });

  it('re-renders nothing that is not watching that symbol', async () => {
    mount();
    await settled();

    const pnlBefore = pnlRenders('Unrealised P&L').length;
    const realisedBefore = pnlRenders('Realised P&L').length;
    const strategyBefore = counters.strategy.length;
    const venueBefore = counters.venue.length;

    emit('pnl', { symbol: BTC, unrealized_pnl: 1234.5, unrealized_pnl_pct: 4.25 });

    // One leaf moved, so one render happened — and the ETH cell, whose seeded value is
    // untouched, is not among the renders that followed.
    expect(pnlRenders('Unrealised P&L').length).toBe(pnlBefore + 1);
    expect(cellRenders(10).length).toBe(2);

    // Nothing on another channel, and nothing structural, re-rendered. Before task 19.3 the
    // frame was written into page state and every one of these counts would have moved.
    expect(pnlRenders('Realised P&L').length).toBe(realisedBefore);
    expect(counters.strategy.length).toBe(strategyBefore);
    expect(counters.venue.length).toBe(venueBefore);
  });

  it('re-renders nothing at all for a symbol no row holds', async () => {
    mount();
    await settled();

    const pnlBefore = pnlRenders('Unrealised P&L').length;
    const strategyBefore = counters.strategy.length;
    const venueBefore = counters.venue.length;

    emit('pnl', { symbol: 'DOGE/USDT', unrealized_pnl: 999, unrealized_pnl_pct: 12 });

    // `useLiveChannel` compares the selected slice before enqueuing anything, so a declined
    // frame does not schedule a render for any consumer of the channel.
    expect(pnlRenders('Unrealised P&L').length).toBe(pnlBefore);
    expect(counters.strategy.length).toBe(strategyBefore);
    expect(counters.venue.length).toBe(venueBefore);
  });

  it('shares ONE client subscription across both position rows', async () => {
    mount();
    await settled();

    // Two rows, two selected figures each — four listeners — and one `wsClient.subscribe`,
    // which is what makes subscribing per leaf affordable (§13.2a).
    expect(liveChannelSubscriberCount('pnl')).toBe(4);
    expect(socket.handlers.get('pnl')?.size).toBe(1);
  });

  it('does not re-issue the ONE read for a frame', async () => {
    const reader = read();
    mount();
    await settled();
    expect(reader).toHaveBeenCalledTimes(1);

    emit('pnl', { symbol: BTC, unrealized_pnl: 1234.5, unrealized_pnl_pct: 4.25 });
    emit('pnl', { symbol: ETH, unrealized_pnl: 77, unrealized_pnl_pct: 1.5 });

    // A tick is not a question. The read is issued by mount, by Refresh, by a ledger change
    // and by a reconnect — never by a frame.
    expect(reader).toHaveBeenCalledTimes(1);
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE OTHER TWO CHANNELS REACH THEIR OWN LEAF AND NO OTHER
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('Dashboard tick isolation — the strategy and venue leaves', () => {
  beforeEach(() => {
    cleanup();
    resetLiveChannels();
    socket.reset();
    counters.reset();
    vi.restoreAllMocks();
    read().mockResolvedValue(body());
  });

  afterEach(() => {
    cleanup();
    resetLiveChannels();
    socket.reset();
  });

  it('routes a `STRATEGY_STATUS` frame to that strategy\'s badge alone', async () => {
    mount();
    await settled();

    const strategyBefore = counters.strategy.length;
    const pnlBefore = pnlRenders('Unrealised P&L').length;
    const venueBefore = counters.venue.length;

    emit('STRATEGY_STATUS', { strategy_id: STRATEGY_ID, status: 'error', health: 'degraded' });

    const strategyAfter = counters.strategy;
    expect(strategyAfter.length).toBeGreaterThan(strategyBefore);
    expect(strategyAfter[strategyAfter.length - 1]).toEqual({ status: 'error', health: 'degraded' });

    // The positions table and the venue row are on other channels and did not move.
    expect(pnlRenders('Unrealised P&L').length).toBe(pnlBefore);
    expect(counters.venue.length).toBe(venueBefore);
  });

  it('ignores a `STRATEGY_STATUS` frame for another deployment', async () => {
    mount();
    await settled();

    const before = counters.strategy.length;
    emit('STRATEGY_STATUS', { strategy_id: 'strat_other', status: 'error', health: 'degraded' });
    expect(counters.strategy.length).toBe(before);
  });

  it('routes an `exchange_health` frame to that venue alone', async () => {
    mount();
    await settled();

    const venueBefore = counters.venue.length;
    const pnlBefore = pnlRenders('Unrealised P&L').length;
    const strategyBefore = counters.strategy.length;

    emit('exchange_health', { exchange_id: VENUE_ID, last_sync: '2026-08-26T12:30:00Z' });

    expect(counters.venue.length).toBeGreaterThan(venueBefore);
    // The frame's `last_sync` is what reaches the screen — the leaf is connected, not just
    // subscribed. Its `status` and `latency_ms` are deliberately not read (§7.1's note on the
    // aggregation service's two constants), which is why the reading asserted here is time.
    expect(document.querySelector('time[datetime="2026-08-26T12:30:00Z"]')).not.toBeNull();

    expect(pnlRenders('Unrealised P&L').length).toBe(pnlBefore);
    expect(counters.strategy.length).toBe(strategyBefore);
  });

  it('drops a frame from the other ledger', async () => {
    mount();
    await settled();

    const before = counters.strategy.length;
    // The page is on `live`. A paper frame is not about the row it names.
    emit('STRATEGY_STATUS', {
      strategy_id: STRATEGY_ID,
      status: 'error',
      health: 'degraded',
      environment: 'paper',
    });
    expect(counters.strategy.length).toBe(before);
  });

  it('drops a frame published before the read that seeded the leaf', async () => {
    mount();
    await settled();

    const before = counters.strategy.length;
    emit('STRATEGY_STATUS', {
      strategy_id: STRATEGY_ID,
      status: 'error',
      health: 'degraded',
      timestamp: new Date(Date.now() - 3600000).toISOString(),
    });
    expect(counters.strategy.length).toBe(before);
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE CHART IS OUT OF THE TICK PATH ENTIRELY
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('Dashboard tick isolation — charts do not re-render', () => {
  beforeEach(() => {
    cleanup();
    resetLiveChannels();
    socket.reset();
    counters.reset();
    vi.restoreAllMocks();
    read().mockResolvedValue(body());
  });

  afterEach(() => {
    cleanup();
    resetLiveChannels();
    socket.reset();
  });

  it('renders the equity curve once and never again for a frame on any channel', async () => {
    mount();
    await settled();

    const chartBefore = counters.chart.length;
    expect(chartBefore).toBeGreaterThan(0);
    expect(counters.chart[chartBefore - 1]).toBe(2);

    emit('pnl', { symbol: BTC, unrealized_pnl: 1234.5, unrealized_pnl_pct: 4.25 });
    emit('pnl', { symbol: ETH, unrealized_pnl: 77, unrealized_pnl_pct: 1.5 });
    emit('STRATEGY_STATUS', { strategy_id: STRATEGY_ID, status: 'error', health: 'degraded' });
    emit('exchange_health', { exchange_id: VENUE_ID, last_sync: '2026-08-26T12:30:00Z' });
    emit('notification', { severity: 'critical', title: 'Anything', message: 'Anything' });

    // Five frames on four channels, and recharts was not re-entered once. `equity_curve` is a
    // historical series that only the ONE read writes, and `EquityCurveChart`'s `memo` takes
    // primitive-plus-stable-array props so nothing short of a read can move it.
    expect(counters.chart.length).toBe(chartBefore);
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * A POLL DOES NOT RECREATE ITS INTERVAL (§1.12)
 * ══════════════════════════════════════════════════════════════════════════════════════ */

/** A probe whose only job is to hold a polling `usePanelState`. */
function PollingProbe({ reader, intervalMs }) {
  const { state } = usePanelState(reader, { intervalMs });
  return <span data-probe-state={state} />;
}

describe('Dashboard tick isolation — no interval is torn down and rebuilt', () => {
  let setIntervalSpy;

  beforeEach(() => {
    cleanup();
    resetLiveChannels();
    socket.reset();
    counters.reset();
    vi.restoreAllMocks();
    read().mockResolvedValue(body());
    setIntervalSpy = vi.spyOn(globalThis, 'setInterval');
  });

  afterEach(() => {
    cleanup();
    resetLiveChannels();
    socket.reset();
  });

  it('creates no interval on the Dashboard, and no frame makes it create one', async () => {
    mount();
    await settled();

    // The page reads once through `usePanelState` with no `intervalMs`, so there is no timer
    // to recreate. This is the shape `usePolling` could not have: its `fetch` callback listed
    // `data`, so every completed read replaced the callback and the interval with it.
    const before = setIntervalSpy.mock.calls.length;

    emit('pnl', { symbol: BTC, unrealized_pnl: 1234.5, unrealized_pnl_pct: 4.25 });
    emit('STRATEGY_STATUS', { strategy_id: STRATEGY_ID, status: 'error', health: 'degraded' });
    emit('exchange_health', { exchange_id: VENUE_ID, last_sync: '2026-08-26T12:30:00Z' });

    expect(setIntervalSpy.mock.calls.length).toBe(before);
  });

  it('sets a polling interval exactly once across two completed polls', async () => {
    /*
     * The other direction, so the assertion above is not vacuously true of a page with no
     * timer. `usePanelState`'s `run` closes over nothing a read changes — `data`, `state` and
     * `error` are all reached through refs or through `setPanel`'s updater — so its effect's
     * dependency array is stable and the interval is set on mount and not again.
     */
    const reader = vi.fn().mockResolvedValue({ rows: [1] });
    const intervalMs = 20;

    render(<PollingProbe reader={reader} intervalMs={intervalMs} />);

    await waitFor(() => expect(reader).toHaveBeenCalledTimes(1));
    // Two polls beyond the first read, each of which resolves and writes state.
    await waitFor(() => expect(reader.mock.calls.length).toBeGreaterThanOrEqual(3), { timeout: 3000 });

    const intervals = setIntervalSpy.mock.calls.filter(([, delay]) => delay === intervalMs);
    expect(intervals.length).toBe(1);
  });
});
