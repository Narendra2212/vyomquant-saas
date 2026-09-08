/**
 * PaperTrading.test.jsx — the first of task 32.9's three page suites.
 *
 * WHAT THIS FILE HOLDS IN PLACE (task 32.9's five clauses, in order)
 * -----------------------------------------------------------------
 * 1. **Each of the eight states of task 32.3 renders**, and each is driven from the signal the
 *    server actually sends — a 401, a `MARKETPLACE_SUBSCRIPTION_EXPIRED` envelope, a
 *    `PAPER_START_REFUSED` carrying `details.validation`, a `feed_state` outside
 *    `TRADEABLE_FEED_STATES`, a `session_state` of `CREATED` — never by poking internal state.
 *    The table is driven from {@link REQUIRED_PANEL_STATES} and its completeness is asserted, so a
 *    ninth state added to the vocabulary without a case here fails this suite rather than being
 *    silently uncovered.
 * 2. **The equity curve is drawn from `sessions.equity()` and not from event state.** Made
 *    structural three ways: `equityCurvePoints` takes exactly one parameter, so it has no access to
 *    a frame at all; the series that reaches the chart is the snapshot array the equity read
 *    returned, value for value; and pushing further frames through the live channel moves the PnL
 *    chart while leaving the equity series byte-identical and the equity read uncalled again.
 * 3. **On unmount every disposal the session effect owns runs**: `subscribeChannel`'s releaser, the
 *    `onOpen` unsubscribe, `clearInterval` on the safety poll's own id, and
 *    `websocketClient.release()` — plus the page-lifetime `onStatusChange` unsubscribe and both
 *    teardown paths of {@link useObservedWidth} (the `resize` listener and, when one exists, the
 *    `ResizeObserver`).
 * 4. **Retained events, ticks and chart points are capped at 500 / 1000 / 2000, oldest first.**
 *    Asserted against the pure `retainFrames` / `boundedTail` (fast, no rendering) AND through the
 *    DOM the page discloses them on — `[data-testid="paper-retention"]` and
 *    `[data-testid="paper-equity-series"]` — including WHICH members survived, so "oldest discarded
 *    first" is checked rather than only the surviving count.
 * 5. **Every figure carries a simulated label**, inside the figure's own region rather than only in
 *    the page header. The header's tag says something longer, so an exact match on `Simulated`
 *    cannot be satisfied by it.
 *
 * Plus the page's three honesty guarantees, which are the ones a regression would be quietest
 * about: `metrics: null` + `computed: false` renders "Not computed" and never a zero;
 * `parseCapitalToMinor` refuses a non-integral minor-unit amount rather than rounding it into a
 * balance; and a `stop` answering `complete: false` is reported as not complete.
 *
 * WHAT IS REAL HERE AND WHAT IS A DOUBLE
 * --------------------------------------
 * Real: `PaperTrading.jsx` in full, `paperTradingFormat.js`, the design-system primitives, `Card`,
 * `Button`, `react-router-dom`.
 *
 * Doubles, and why each one is not the thing under test:
 * * `../../api` — the network. Every scenario here IS a server answer, so the answers are the
 *   fixtures. `apiSourceContract`'s suites already pin that those methods travel the shared client.
 * * `../../websocketClient` — the socket. `subscribeChannel`, `onStatusChange`, `onOpen`, `acquire`,
 *   `release` and `getStatus` are stubbed so the four disposals of clause 3 are observable at all;
 *   jsdom's `WebSocket` would attempt a real connection.
 * * `recharts` — the renderer, not the data. `ResponsiveContainer` measures its container and draws
 *   nothing at a jsdom width of zero, which would make clause 2 unobservable. The stub records the
 *   `data` array each chart was handed, which is precisely the quantity clause 2 is about.
 *
 * No panel-state rule, no reduction, no bound and no formatter is replaced by a test-only version.
 *
 * ResizeObserver: jsdom provides none, so the DEFAULT path exercised by every test in this file is
 * the page's documented `resize`-listener fallback — including a real re-measurement through it.
 * The observer path is covered too, by stubbing one for exactly one test and asserting
 * `disconnect()` on unmount. Both are asserted; neither is assumed.
 *
 * _Requirements: 20.5, 20.6, 20.8, 27.5, 29.2_
 */

import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// ═══════════════════════════════════════════════════════════════════════════
// THE THREE DOUBLES
// ═══════════════════════════════════════════════════════════════════════════

const { paperSessions, libraryApi, wsMock, chartLog } = vi.hoisted(() => ({
  paperSessions: {
    create: vi.fn(),
    list: vi.fn(),
    get: vi.fn(),
    pause: vi.fn(),
    resume: vi.fn(),
    stop: vi.fn(),
    reset: vi.fn(),
    orders: vi.fn(),
    fills: vi.fn(),
    positions: vi.fn(),
    trades: vi.fn(),
    equity: vi.fn(),
    metrics: vi.fn(),
    events: vi.fn(),
  },
  libraryApi: { myStrategies: vi.fn() },
  wsMock: {
    subscribeChannel: vi.fn(),
    onStatusChange: vi.fn(),
    onOpen: vi.fn(),
    acquire: vi.fn(),
    release: vi.fn(),
    getStatus: vi.fn(),
  },
  // Populated by the recharts stub below: one entry per chart render, newest last.
  chartLog: { entries: [] },
}));

vi.mock('../../api', () => ({
  api: { paper: { sessions: paperSessions }, library: libraryApi },
}));

vi.mock('../../websocketClient', () => ({ default: wsMock }));

vi.mock('recharts', async () => {
  const imported = await import('react');
  const R = imported.default ?? imported;

  /** Every `dataKey` under a chart, which is what identifies which chart this is. */
  const collectKeys = (node, out = []) => {
    R.Children.forEach(node, (child) => {
      if (!R.isValidElement(child)) return;
      if (typeof child.props?.dataKey === 'string') out.push(child.props.dataKey);
      if (child.props?.children) collectKeys(child.props.children, out);
    });
    return out;
  };

  const chart = (kind) => {
    const Component = ({ data, children }) => {
      const keys = collectKeys(children);
      const rows = Array.isArray(data) ? data : [];
      chartLog.entries.push({ kind, keys, data: rows });
      return R.createElement('div', {
        'data-chart-kind': kind,
        'data-chart-keys': keys.join(','),
        'data-chart-length': rows.length,
      });
    };
    Component.displayName = `Stub(${kind})`;
    return Component;
  };

  const passthrough = (name) => {
    const Component = ({ children }) => R.createElement('div', { 'data-recharts': name }, children);
    Component.displayName = `Stub(${name})`;
    return Component;
  };

  const nothing = (name) => {
    const Component = () => null;
    Component.displayName = `Stub(${name})`;
    return Component;
  };

  return {
    ResponsiveContainer: passthrough('ResponsiveContainer'),
    AreaChart: chart('AreaChart'),
    ComposedChart: chart('ComposedChart'),
    Area: nothing('Area'),
    Line: nothing('Line'),
    Scatter: nothing('Scatter'),
    CartesianGrid: nothing('CartesianGrid'),
    ReferenceLine: nothing('ReferenceLine'),
    Tooltip: nothing('Tooltip'),
    XAxis: nothing('XAxis'),
    YAxis: nothing('YAxis'),
  };
});

import PaperTrading from '../PaperTrading';
import {
  MAX_CHART_POINTS_PER_SERIES,
  MAX_RETAINED_EVENTS,
  MAX_RETAINED_TICKS,
  PANEL_STATES,
  REQUIRED_PANEL_STATES,
  TRADEABLE_FEED_STATES,
  boundedTail,
  equityCurvePoints,
  parseCapitalToMinor,
  retainFrames,
  toChartNumber,
} from '../paperTradingFormat';

// ═══════════════════════════════════════════════════════════════════════════
// FIXTURES
// ═══════════════════════════════════════════════════════════════════════════

const SESSION_ID = 'sess-1';
const AT = '2026-01-01T00:00:00.000Z';

/**
 * The page's own safety-poll period, spelled here because the module does not export it.
 * The page discloses it on screen, and {@link theSafetyPollPeriodIsDisclosed} pins the two
 * together so this constant cannot drift away from the one the effect uses.
 */
const SAFETY_POLL_INTERVAL_MS = 30000;

/** `apiClient`'s `ApiError` shape: a status and the parsed error envelope on `.data`. */
const apiError = (status, body, message = 'The server refused the read.') =>
  Object.assign(new Error(message), { status, data: body });

const envelope = (code, message, details = {}) => ({ error: { code, message, details } });

const sessionRow = (overrides = {}) => ({
  id: SESSION_ID,
  symbol: 'BTC/USDT',
  timeframe: '1m',
  exchange_id: 'binance',
  session_state: 'RUNNING',
  feed_state: 'HEALTHY',
  feed_transport: 'websocket',
  market_data_source: 'binance.ws',
  currency: 'USD',
  initial_capital_minor: 10000000,
  event_sequence: 12,
  created_at: AT,
  ...overrides,
});

const equitySnapshot = (i) => ({
  series_index: 0,
  taken_at: AT,
  cause: 'TICK',
  stale: false,
  total_equity: `${100000 + i}.0000000000`,
  available_balance: `${90000 + i}.0000000000`,
  locked_balance: '0.0000000000',
  position_market_value: '10000.0000000000',
});

/** A `paper_pnl_updated` frame whose `total_pnl` IS its sequence, so a survivor is identifiable. */
const pnlFrame = (sequence) => ({
  type: 'paper_pnl_updated',
  sequence,
  event_id: `evt-${sequence}`,
  emitted_at: AT,
  payload: {
    realized_pnl: '1.0000000000',
    unrealized_pnl: '2.0000000000',
    total_pnl: String(sequence),
    total_return_pct: '0.5',
  },
});

/** A `market_tick` frame whose `close` IS its sequence, for the same reason. */
const tickFrame = (sequence) => ({
  type: 'market_tick',
  sequence,
  event_id: `tick-${sequence}`,
  emitted_at: AT,
  payload: {
    symbol: 'BTC/USDT',
    timestamp: AT,
    close: String(sequence),
    latency_ms: '4.5',
    feed_state: 'HEALTHY',
  },
});

const OWNED_ENTRY = {
  entry_id: 'entry-owned',
  ownership: 'OWNED',
  strategy_id: 'stg-1',
  name: 'Mean Reversion',
  symbol: 'BTC/USDT',
  timeframe: '1m',
  entitling: true,
  unavailable_reason: null,
  allowed_actions: ['start_paper', 'run_backtest'],
};

/** The socket callbacks the page registered, captured so a scenario can drive them. */
let channelHandler;
let openListener;
let statusListener;
let channelRelease;
let openUnsubscribe;
let statusUnsubscribe;

const defaultReads = () => {
  paperSessions.list.mockResolvedValue({ sessions: [sessionRow()] });
  paperSessions.get.mockResolvedValue({ session: sessionRow() });
  paperSessions.equity.mockResolvedValue({ equity: [equitySnapshot(0)] });
  paperSessions.metrics.mockResolvedValue({ metrics: null, computed: false });
  paperSessions.positions.mockResolvedValue({ positions: [] });
  paperSessions.orders.mockResolvedValue({ orders: [] });
  paperSessions.trades.mockResolvedValue({ trades: [], count: 0 });
  paperSessions.events.mockResolvedValue({ events: [] });
  libraryApi.myStrategies.mockResolvedValue({ items: [OWNED_ENTRY] });
};

beforeEach(() => {
  vi.clearAllMocks();
  chartLog.entries.length = 0;

  channelHandler = null;
  openListener = null;
  statusListener = null;
  channelRelease = vi.fn();
  openUnsubscribe = vi.fn();
  statusUnsubscribe = vi.fn();

  wsMock.getStatus.mockReturnValue('connected');
  wsMock.subscribeChannel.mockImplementation((channel, handler) => {
    channelHandler = handler;
    return channelRelease;
  });
  wsMock.onOpen.mockImplementation((listener) => {
    openListener = listener;
    return openUnsubscribe;
  });
  wsMock.onStatusChange.mockImplementation((listener) => {
    statusListener = listener;
    return statusUnsubscribe;
  });
  wsMock.acquire.mockReturnValue(1);
  wsMock.release.mockReturnValue(0);

  window.showToast = vi.fn();
  defaultReads();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  delete window.showToast;
});

// ═══════════════════════════════════════════════════════════════════════════
// HARNESS
// ═══════════════════════════════════════════════════════════════════════════

const renderPage = (url = '/app/paper-trading') =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <PaperTrading />
    </MemoryRouter>,
  );

/** Render, then select the fixture session so the seven session-scoped reads run. */
async function renderWithSession() {
  const utils = renderPage();
  const select = await screen.findByLabelText('Session');
  await act(async () => {
    fireEvent.change(select, { target: { value: SESSION_ID } });
  });
  await waitFor(() => expect(paperSessions.events).toHaveBeenCalledWith(SESSION_ID, 0));
  // One more turn of the loop so the last read's setState has been applied and flushed.
  await act(async () => {});
  return utils;
}

/** The most recent chart render whose series include `key`, or `undefined`. */
const lastChartWith = (key) =>
  [...chartLog.entries].reverse().find((entry) => entry.keys.includes(key));

const retention = () => screen.getByTestId('paper-retention');
const attr = (element, name) => element.getAttribute(name);

// ═══════════════════════════════════════════════════════════════════════════
// 1. THE EIGHT STATES OF TASK 32.3
// ═══════════════════════════════════════════════════════════════════════════

/**
 * One driver per state: the server signal that produces it, and a phrase the rendered panel must
 * carry. The phrase is what stops a passing assertion from being a coincidence — `panel-error`
 * appearing is one claim, and it saying which read did not complete is another.
 *
 * Every driver here changes a RESPONSE. None of them reaches into the component.
 */
const STATE_DRIVERS = {
  [PANEL_STATES.LOADING]: {
    signal: 'a read that has not settled',
    mustContain: 'Loading',
    async drive() {
      // A read that never answers. `status` is what the panel branches on, so this is the only
      // input `loading` has.
      libraryApi.myStrategies.mockReturnValue(new Promise(() => {}));
      renderPage();
      await screen.findAllByTestId('panel-loading');
    },
  },

  [PANEL_STATES.EMPTY]: {
    signal: 'a settled my-strategies read carrying no startable entry',
    mustContain: 'No strategy you own or subscribe to currently offers a paper session.',
    async drive() {
      libraryApi.myStrategies.mockResolvedValue({ items: [] });
      renderPage();
      await screen.findAllByTestId('panel-empty');
    },
  },

  [PANEL_STATES.ERROR]: {
    signal: 'HTTP 500 with no recognised code',
    mustContain: 'This read did not complete',
    async drive() {
      libraryApi.myStrategies.mockRejectedValue(
        apiError(500, envelope('INTERNAL', 'The library read blew up.')),
      );
      renderPage();
      await screen.findAllByTestId('panel-error-with-retry');
    },
  },

  [PANEL_STATES.DISABLED]: {
    signal: "session_state 'CREATED', which admits none of the four transitions",
    mustContain: 'admits none of pause, resume, stop or reset',
    async drive() {
      paperSessions.get.mockResolvedValue({ session: sessionRow({ session_state: 'CREATED' }) });
      paperSessions.list.mockResolvedValue({
        sessions: [sessionRow({ session_state: 'CREATED' })],
      });
      await renderWithSession();
      await screen.findAllByTestId('panel-disabled');
    },
  },

  [PANEL_STATES.UNAUTHORISED]: {
    signal: 'HTTP 401',
    mustContain: 'Not authorised',
    async drive() {
      libraryApi.myStrategies.mockRejectedValue(apiError(401, null, 'Sign in again.'));
      renderPage();
      await screen.findAllByTestId('panel-unauthorised');
    },
  },

  [PANEL_STATES.EXPIRED_SUBSCRIPTION]: {
    signal: 'a MARKETPLACE_SUBSCRIPTION_EXPIRED envelope',
    mustContain: 'MARKETPLACE_SUBSCRIPTION_EXPIRED',
    async drive() {
      libraryApi.myStrategies.mockRejectedValue(
        apiError(
          403,
          envelope('MARKETPLACE_SUBSCRIPTION_EXPIRED', 'This subscription period has ended.'),
        ),
      );
      renderPage();
      await screen.findAllByTestId('panel-expired-subscription');
    },
  },

  [PANEL_STATES.UNAVAILABLE_STRATEGY]: {
    signal: "PAPER_START_REFUSED with details.validation = 'STRATEGY_NOT_EXECUTABLE'",
    mustContain: 'PAPER_START_REFUSED',
    async drive() {
      paperSessions.create.mockRejectedValue(
        apiError(
          403,
          envelope('PAPER_START_REFUSED', 'No runnable version resolves for this listing.', {
            validation: 'STRATEGY_NOT_EXECUTABLE',
          }),
        ),
      );
      renderPage();
      // Selected, filled and started exactly as a user would: the refusal has to come back from
      // `POST /sessions`, because that is where the server re-resolves the artifact.
      const strategy = await screen.findByLabelText('Strategy');
      await act(async () => {
        fireEvent.change(strategy, { target: { value: OWNED_ENTRY.entry_id } });
      });
      await act(async () => {
        fireEvent.click(
          screen.getByRole('button', { name: 'Start a simulated paper session' }),
        );
      });
      await screen.findAllByTestId('panel-unavailable-strategy');
    },
  },

  [PANEL_STATES.FEED_DISCONNECTED]: {
    signal: "feed_state 'DEGRADED', which is not in TRADEABLE_FEED_STATES",
    mustContain: 'Feed disconnected',
    async drive() {
      expect(TRADEABLE_FEED_STATES).not.toContain('DEGRADED');
      paperSessions.get.mockResolvedValue({ session: sessionRow({ feed_state: 'DEGRADED' }) });
      await renderWithSession();
      await screen.findAllByTestId('panel-feed-disconnected');
    },
  },
};

describe('PaperTrading — the eight panel states of task 32.3 (Requirement 20.5)', () => {
  it('has a driver for every member of REQUIRED_PANEL_STATES and for nothing else', () => {
    // The completeness gate. A ninth state added to the vocabulary lands here first.
    expect([...Object.keys(STATE_DRIVERS)].sort()).toEqual([...REQUIRED_PANEL_STATES].sort());
    expect(REQUIRED_PANEL_STATES).toHaveLength(8);
  });

  it.each(REQUIRED_PANEL_STATES)('renders panel-%s from a real server signal', async (state) => {
    const driver = STATE_DRIVERS[state];
    expect(
      typeof driver?.drive,
      `no driver for '${state}' — task 32.3 requires every one of REQUIRED_PANEL_STATES to render`,
    ).toBe('function');

    await driver.drive();

    const panels = screen.getAllByTestId(`panel-${state}`);
    expect(panels.length, `${state} (${driver.signal}) rendered no panel`).toBeGreaterThan(0);
    // The panel says what happened, so a bare testid cannot carry the assertion on its own.
    expect(
      panels.some((panel) => panel.textContent.includes(driver.mustContain)),
      `panel-${state} rendered without '${driver.mustContain}'`,
    ).toBe(true);
    // And it is tagged with its own state, from the single non-ready renderer.
    expect(panels[0].getAttribute('data-panel-state')).toBe(`panel-${state}`);
  });

  it('renders none of the eight when every read settled and nothing is wrong', async () => {
    // The falsifiability half of the table above: if `getAllByTestId('panel-X')` were satisfied by
    // the page's ordinary output, this would fail. With no session selected the panels are `idle`,
    // which is deliberately NOT one of the eight.
    renderPage();
    await screen.findByLabelText('Session');
    await act(async () => {});

    for (const state of REQUIRED_PANEL_STATES) {
      expect(
        screen.queryAllByTestId(`panel-${state}`),
        `panel-${state} appeared with no signal to justify it`,
      ).toHaveLength(0);
    }
    expect(screen.getAllByTestId('panel-idle').length).toBeGreaterThan(0);
  });

  it('leaves the loading state as soon as the read settles', async () => {
    let resolveIt;
    libraryApi.myStrategies.mockReturnValue(
      new Promise((resolve) => {
        resolveIt = resolve;
      }),
    );
    renderPage();
    expect(screen.getAllByTestId('panel-loading').length).toBeGreaterThan(0);

    await act(async () => {
      resolveIt({ items: [OWNED_ENTRY] });
    });
    await waitFor(() =>
      expect(screen.queryByTestId('panel-loading')).toBeNull(),
    );
  });

  it('shows the reconnecting indicator only while the socket reports a reconnection', async () => {
    wsMock.getStatus.mockReturnValue('connected');
    renderPage();
    await screen.findByLabelText('Session');
    expect(screen.queryByTestId('paper-reconnecting')).toBeNull();

    await act(async () => {
      statusListener('connecting');
    });
    expect(screen.getByTestId('paper-reconnecting')).toBeTruthy();

    await act(async () => {
      statusListener('connected');
    });
    expect(screen.queryByTestId('paper-reconnecting')).toBeNull();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 2. THE EQUITY CURVE'S SOURCE (Requirement 18.11)
// ═══════════════════════════════════════════════════════════════════════════

describe('PaperTrading — the equity curve comes from sessions.equity() and nothing else', () => {
  it('cannot see event state: equityCurvePoints takes the snapshot array and no second input', () => {
    // Structural rather than behavioural, and it is the strongest form the claim has: a function
    // with one parameter has no frame to accumulate from.
    expect(equityCurvePoints).toHaveLength(1);
  });

  it('plots exactly the snapshots the equity read returned, in the order it returned them', async () => {
    const snapshots = [0, 1, 2, 3, 4].map(equitySnapshot);
    paperSessions.equity.mockResolvedValue({ equity: snapshots });
    paperSessions.events.mockResolvedValue({ events: [pnlFrame(1), pnlFrame(2)] });

    await renderWithSession();

    const equityChart = lastChartWith('equity');
    expect(equityChart, 'no chart was handed an `equity` series').toBeTruthy();
    expect(equityChart.data.map((point) => point.equity)).toEqual(
      snapshots.map((row) => toChartNumber(row.total_equity)),
    );
    expect(equityChart.data.map((point) => point.totalEquityText)).toEqual(
      snapshots.map((row) => row.total_equity),
    );
    expect(attr(screen.getByTestId('paper-equity-series'), 'data-chart-points')).toBe('5');

    // The frames that were also read carry a `total_pnl` of their own. None of it is on the curve.
    const plotted = equityChart.data.map((point) => point.equity);
    expect(plotted).not.toContain(1);
    expect(plotted).not.toContain(2);
  });

  it('does not move the curve when only the frames change', async () => {
    const snapshots = [0, 1, 2, 3, 4].map(equitySnapshot);
    paperSessions.equity.mockResolvedValue({ equity: snapshots });
    paperSessions.events.mockResolvedValue({ events: [pnlFrame(1), pnlFrame(2)] });

    await renderWithSession();

    const before = lastChartWith('equity').data.map((point) => point.equity);
    const pnlBefore = lastChartWith('total').data.length;
    const equityCallsBefore = paperSessions.equity.mock.calls.length;

    // Live frames, through the channel handler the page registered — the one input clause 2 says
    // must not reach the curve.
    for (const sequence of [3, 4, 5]) {
      // eslint-disable-next-line no-await-in-loop
      await act(async () => {
        channelHandler(pnlFrame(sequence));
      });
    }

    // The control: the frames really did land, so "unchanged" is not "nothing happened".
    const pnlAfter = lastChartWith('total').data.length;
    expect(pnlAfter).toBe(pnlBefore + 3);

    const after = lastChartWith('equity').data.map((point) => point.equity);
    expect(after).toEqual(before);
    expect(attr(screen.getByTestId('paper-equity-series'), 'data-chart-points')).toBe('5');
    expect(paperSessions.equity.mock.calls.length).toBe(equityCallsBefore);
  });

  it('redraws the identical curve after a remount, because the read is the only source', async () => {
    const snapshots = [0, 1, 2].map(equitySnapshot);
    paperSessions.equity.mockResolvedValue({ equity: snapshots });
    paperSessions.events.mockResolvedValue({ events: [pnlFrame(9)] });

    const first = await renderWithSession();
    const before = lastChartWith('equity').data.map((point) => point.equity);
    first.unmount();

    chartLog.entries.length = 0;
    await renderWithSession();
    expect(lastChartWith('equity').data.map((point) => point.equity)).toEqual(before);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 3. THE DISPOSALS ON UNMOUNT (Requirement 20.8)
// ═══════════════════════════════════════════════════════════════════════════

describe('PaperTrading — everything the route creates is disposed of on unmount', () => {
  it('calls the subscribeChannel releaser, the onOpen unsubscribe, clearInterval and websocketClient.release', async () => {
    const setIntervalSpy = vi.spyOn(globalThis, 'setInterval');
    const clearIntervalSpy = vi.spyOn(globalThis, 'clearInterval');

    const { unmount } = await renderWithSession();

    // Precondition: all four were created, and the channel is the session's own.
    expect(wsMock.subscribeChannel).toHaveBeenCalledWith(`paper.${SESSION_ID}`, expect.any(Function));
    expect(wsMock.acquire).toHaveBeenCalled();
    expect(wsMock.onOpen).toHaveBeenCalled();

    const pollCalls = setIntervalSpy.mock.calls.filter(([, delay]) => delay === SAFETY_POLL_INTERVAL_MS);
    expect(pollCalls, 'the safety poll interval was never created').toHaveLength(1);
    const pollId = setIntervalSpy.mock.results[setIntervalSpy.mock.calls.indexOf(pollCalls[0])].value;

    expect(channelRelease).not.toHaveBeenCalled();
    expect(openUnsubscribe).not.toHaveBeenCalled();
    expect(statusUnsubscribe).not.toHaveBeenCalled();
    expect(wsMock.release).not.toHaveBeenCalled();

    unmount();

    // 1. the ref-counted channel releaser
    expect(channelRelease).toHaveBeenCalledTimes(1);
    // 2. onOpen's own unsubscribe
    expect(openUnsubscribe).toHaveBeenCalledTimes(1);
    // 3. every clearInterval — asserted against the poll's OWN id, not merely "some interval"
    expect(clearIntervalSpy).toHaveBeenCalledWith(pollId);
    // 4. this page's hold on the one connection
    expect(wsMock.release).toHaveBeenCalledTimes(1);
    // and the page-lifetime status listener from task 32.3
    expect(statusUnsubscribe).toHaveBeenCalledTimes(1);

    // No interval the route created outlives it.
    const created = setIntervalSpy.mock.calls
      .map((call, index) => ({ delay: call[1], id: setIntervalSpy.mock.results[index].value }))
      .filter(({ delay }) => delay === SAFETY_POLL_INTERVAL_MS);
    for (const { id } of created) {
      expect(clearIntervalSpy.mock.calls.some(([cleared]) => cleared === id)).toBe(true);
    }
  });

  it('discloses the same safety-poll period the effect uses', async () => {
    await renderWithSession();
    // If the effect's period and this file's constant diverged, one of the two assertions in the
    // previous test would be asserting about a timer nobody created.
    expect(screen.getByTestId('paper-trading-page').textContent).toContain(
      `re-read every ${SAFETY_POLL_INTERVAL_MS / 1000} seconds`,
    );
  });

  it('releases the subscription for the previous session when the selection changes', async () => {
    paperSessions.list.mockResolvedValue({
      sessions: [sessionRow(), sessionRow({ id: 'sess-2' })],
    });
    await renderWithSession();
    expect(channelRelease).not.toHaveBeenCalled();

    await act(async () => {
      fireEvent.change(screen.getByLabelText('Session'), { target: { value: 'sess-2' } });
    });

    expect(channelRelease).toHaveBeenCalledTimes(1);
    expect(wsMock.release).toHaveBeenCalledTimes(1);
    expect(wsMock.subscribeChannel).toHaveBeenLastCalledWith('paper.sess-2', expect.any(Function));
  });

  it('removes the resize listener it added in place of the ResizeObserver jsdom has none of', async () => {
    // The DEFAULT path in this environment. Made explicit rather than assumed, so a jsdom that
    // gains `ResizeObserver` later cannot silently turn this test into a no-op.
    const saved = globalThis.ResizeObserver;
    delete globalThis.ResizeObserver;
    const addSpy = vi.spyOn(window, 'addEventListener');
    const removeSpy = vi.spyOn(window, 'removeEventListener');
    try {
      const { unmount } = await renderWithSession();

      const added = addSpy.mock.calls.filter(([type]) => type === 'resize');
      expect(added, 'no resize fallback was installed').toHaveLength(1);

      // The fallback is not decoration: it re-measures, and the layout follows.
      const page = screen.getByTestId('paper-trading-page');
      expect(attr(page, 'data-layout-mode')).toBe('wide');
      Object.defineProperty(page, 'clientWidth', { value: 300, configurable: true });
      await act(async () => {
        window.dispatchEvent(new Event('resize'));
      });
      expect(attr(page, 'data-layout-mode')).toBe('stacked');

      unmount();
      const removed = removeSpy.mock.calls.filter(([type]) => type === 'resize');
      expect(removed).toHaveLength(1);
      // The same function, not merely another resize listener.
      expect(removed[0][1]).toBe(added[0][1]);
    } finally {
      if (saved === undefined) delete globalThis.ResizeObserver;
      else globalThis.ResizeObserver = saved;
    }
  });

  it('disconnects the ResizeObserver on unmount when the environment has one', async () => {
    const observers = [];
    const saved = globalThis.ResizeObserver;
    globalThis.ResizeObserver = class {
      constructor(callback) {
        this.callback = callback;
        this.observe = vi.fn();
        this.disconnect = vi.fn();
        observers.push(this);
      }
    };
    try {
      const { unmount } = await renderWithSession();
      expect(observers, 'the observer path was not taken').toHaveLength(1);
      expect(observers[0].observe).toHaveBeenCalledTimes(1);
      expect(observers[0].disconnect).not.toHaveBeenCalled();

      unmount();
      expect(observers[0].disconnect).toHaveBeenCalledTimes(1);
    } finally {
      if (saved === undefined) delete globalThis.ResizeObserver;
      else globalThis.ResizeObserver = saved;
    }
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 4. THE RETENTION BOUNDS (Requirements 27.5, 20.8)
// ═══════════════════════════════════════════════════════════════════════════

describe('PaperTrading — retained events, ticks and chart points are bounded, oldest first', () => {
  it('bounds events at 500 and ticks at 1000, discarding the oldest by sequence', () => {
    // Pure, so this needs no render and no clock.
    expect(MAX_RETAINED_EVENTS).toBe(500);
    expect(MAX_RETAINED_TICKS).toBe(1000);

    const events = Array.from({ length: 600 }, (_, i) => pnlFrame(i + 1));
    const ticks = Array.from({ length: 1200 }, (_, i) => tickFrame(i + 601));
    const kept = retainFrames(events, ticks);

    expect(kept.events).toHaveLength(MAX_RETAINED_EVENTS);
    expect(kept.ticks).toHaveLength(MAX_RETAINED_TICKS);
    expect(kept.eventsDiscarded).toBe(100);
    expect(kept.ticksDiscarded).toBe(200);
    // Oldest first: the survivors are the TAIL of the sequence, not the head.
    expect(kept.events[0].sequence).toBe(101);
    expect(kept.events[kept.events.length - 1].sequence).toBe(600);
    expect(kept.ticks[0].sequence).toBe(801);
    expect(kept.ticks[kept.ticks.length - 1].sequence).toBe(1800);
    // `lastSequence` is the highest SEEN, including from a frame the bound dropped.
    expect(kept.lastSequence).toBe(1800);
  });

  it('bounds a chart series at 2000, discarding the oldest first', () => {
    expect(MAX_CHART_POINTS_PER_SERIES).toBe(2000);
    const series = Array.from({ length: 2500 }, (_, i) => i);
    const bounded = boundedTail(series, MAX_CHART_POINTS_PER_SERIES);
    expect(bounded).toHaveLength(2000);
    expect(bounded[0]).toBe(500);
    expect(bounded[bounded.length - 1]).toBe(2499);
    // Within the bound the same array is returned, so a memo downstream is not invalidated.
    const short = [1, 2, 3];
    expect(boundedTail(short, MAX_CHART_POINTS_PER_SERIES)).toBe(short);
  });

  it('discloses the same three bounds in the DOM, with the oldest members gone', async () => {
    paperSessions.events.mockResolvedValue({
      events: [
        ...Array.from({ length: 600 }, (_, i) => pnlFrame(i + 1)),
        ...Array.from({ length: 1200 }, (_, i) => tickFrame(i + 601)),
      ],
    });
    paperSessions.equity.mockResolvedValue({
      equity: Array.from({ length: 2100 }, (_, i) => equitySnapshot(i)),
    });

    await renderWithSession();

    const panel = retention();
    expect(attr(panel, 'data-retained-event-cap')).toBe('500');
    expect(attr(panel, 'data-retained-tick-cap')).toBe('1000');
    expect(attr(panel, 'data-chart-point-cap')).toBe('2000');
    expect(attr(panel, 'data-retained-events')).toBe('500');
    expect(attr(panel, 'data-retained-ticks')).toBe('1000');
    expect(attr(panel, 'data-events-discarded')).toBe('100');
    expect(attr(panel, 'data-ticks-discarded')).toBe('200');
    expect(attr(panel, 'data-pnl-chart-points')).toBe('500');
    expect(attr(panel, 'data-price-chart-points')).toBe('1000');
    expect(attr(panel, 'data-equity-chart-points')).toBe('2000');

    const series = screen.getByTestId('paper-equity-series');
    expect(attr(series, 'data-chart-points')).toBe('2000');
    expect(attr(series, 'data-chart-points-dropped')).toBe('100');

    // WHICH members survived, not just how many. Each fixture frame carries its own sequence as
    // its value, so the first plotted point names the oldest survivor.
    expect(lastChartWith('total').data[0].totalText).toBe('101');
    expect(lastChartWith('close').data[0].closeText).toBe('801');
    expect(lastChartWith('equity').data[0].totalEquityText).toBe(equitySnapshot(100).total_equity);

    // And the page says so in words as well as in attributes.
    expect(panel.textContent).toContain('500 / 500 events');
    expect(panel.textContent).toContain('1,000 / 1,000 ticks');
    expect(panel.textContent).toContain('The oldest are discarded first');
  });

  it('discards a frame whose event_id has already been applied', async () => {
    paperSessions.events.mockResolvedValue({ events: [pnlFrame(1), pnlFrame(2)] });
    await renderWithSession();
    expect(attr(retention(), 'data-retained-events')).toBe('2');

    await act(async () => {
      channelHandler(pnlFrame(2));
    });
    expect(attr(retention(), 'data-retained-events')).toBe('2');

    await act(async () => {
      channelHandler(pnlFrame(3));
    });
    expect(attr(retention(), 'data-retained-events')).toBe('3');
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 5. THE SIMULATED LABEL ON EVERY FIGURE (Requirement 20.6)
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Each figure region, and how many elements up from its label the region starts.
 *
 * `Figure` wraps its label in a header row inside the panel (two levels); the status-strip panels
 * put the label directly in the panel (one). The number is part of the assertion: a region that
 * had to be widened to find a tag would be finding the page header's tag instead, which is the
 * defect Requirement 20.6 is about.
 */
const FIGURE_REGIONS = [
  ['Market-data status', 1],
  ['Session status', 1],
  ['Feed latency & health', 1],
  ['Current price', 2],
  ['Equity (USD)', 2],
  ['Cash — available (USD)', 2],
  ['Unrealized PnL (USD)', 2],
  ['Realized PnL (USD)', 2],
  ['Total return', 2],
  ['Max drawdown (USD)', 2],
  ['Win rate', 2],
  ['Closed trade count', 2],
];

/** The card a titled region (a chart or a table) lives in. */
const TITLED_REGIONS = [
  'Equity curve',
  'Profit and loss',
  'Drawdown',
  'Price series and trade markers',
  'Live event stream',
];

const regionFor = (label, up) => {
  let node = screen.getByText(label);
  for (let i = 0; i < up; i += 1) node = node.parentElement;
  return node;
};

describe('PaperTrading — every figure carries a simulated label (Requirement 20.6)', () => {
  beforeEach(() => {
    paperSessions.equity.mockResolvedValue({ equity: [0, 1].map(equitySnapshot) });
    paperSessions.metrics.mockResolvedValue({
      metrics: {
        realized_pnl: '10.0000000000',
        unrealized_pnl: '-2.0000000000',
        total_return_pct: '1.25',
        max_drawdown_amount: '5.0000000000',
        max_drawdown_fraction: '0.0500000000',
        win_rate: '0.6250000000',
        closed_trade_count: 8,
      },
      computed: true,
    });
    paperSessions.events.mockResolvedValue({ events: [tickFrame(1), pnlFrame(2)] });
  });

  it.each(FIGURE_REGIONS)('tags the %s figure inside its own region', async (label, up) => {
    await renderWithSession();
    const region = regionFor(label, up);

    // The tag is in the region, and the region is a figure rather than the page.
    expect(within(region).getAllByText('Simulated').length).toBeGreaterThan(0);
    expect(region.textContent).toContain(label);
    expect(
      region.querySelector('h1'),
      `the '${label}' region reaches the page header, so its tag may not be its own`,
    ).toBeNull();
    expect(region.getAttribute('data-testid')).not.toBe('paper-trading-page');
  });

  it.each(TITLED_REGIONS)('tags the %s panel beside its title', async (title) => {
    await renderWithSession();
    // `PanelTitle`'s `right` slot: the tag sits in the title row of the card it belongs to.
    const titleRow = screen.getByText(title).parentElement.parentElement;
    expect(within(titleRow).getAllByText('Simulated').length).toBeGreaterThan(0);
    expect(titleRow.querySelector('h1')).toBeNull();
  });

  it('does not rely on the page header for any of them', async () => {
    await renderWithSession();
    // The header's tag says something longer, so an exact match on 'Simulated' cannot be it.
    expect(screen.getByText('Simulated — no live order is ever placed')).toBeTruthy();
    expect(screen.getAllByText('Simulated').length).toBeGreaterThanOrEqual(
      FIGURE_REGIONS.length,
    );
  });

  it('tags the tables too, and their captions name them as simulated', async () => {
    paperSessions.positions.mockResolvedValue({
      positions: [
        {
          id: 'pos-1',
          symbol: 'BTC/USDT',
          side: 'LONG',
          size: '0.5000000000',
          entry_price: '100.0000000000',
          current_price: '110.0000000000',
          unrealized_pnl: '5.0000000000',
          price_at: AT,
        },
      ],
    });
    await renderWithSession();
    expect(screen.getByText('Simulated open positions')).toBeTruthy();
    const card = screen.getByText('Open positions (1)').parentElement.parentElement;
    expect(within(card).getAllByText('Simulated').length).toBeGreaterThan(0);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// THE PAGE'S HONESTY GUARANTEES
// ═══════════════════════════════════════════════════════════════════════════

describe("PaperTrading — absence is rendered as absence, never as a zero", () => {
  it("renders 'Not computed' and no zero when metrics is null and computed is false", async () => {
    paperSessions.metrics.mockResolvedValue({ metrics: null, computed: false });
    await renderWithSession();

    // The server's own statement, quoted.
    expect(screen.getByTestId('paper-trading-page').textContent).toContain('computed: false');

    for (const label of [
      'Unrealized PnL (USD)',
      'Realized PnL (USD)',
      'Total return',
      'Max drawdown (USD)',
      'Win rate',
    ]) {
      const region = regionFor(label, 2);
      expect(within(region).getAllByText('Not computed').length).toBeGreaterThan(0);
      // Not a zero in any of its spellings.
      expect(region.textContent).not.toMatch(/(^|[^\d.,])0([^\d.,]|$)/);
      expect(region.textContent).not.toContain('0.00');
    }
  });

  it('refuses a capital amount that is not an exact whole number of minor units', () => {
    const refused = parseCapitalToMinor('100000.555', 'USD');
    expect(refused.ok).toBe(false);
    expect(refused.message).toContain('not an exact whole number of minor units');
    expect(refused).not.toHaveProperty('minor');

    // The digits are concatenated, never multiplied: 19.99 USD is 1999 exactly.
    expect(parseCapitalToMinor('19.99', 'USD')).toEqual({ ok: true, minor: 1999 });
    expect(parseCapitalToMinor('100000.00', 'USD')).toEqual({ ok: true, minor: 10000000 });
  });

  it('reports the refusal on screen and disables Start while the amount is not exact', async () => {
    renderPage();
    const strategy = await screen.findByLabelText('Strategy');
    await act(async () => {
      fireEvent.change(strategy, { target: { value: OWNED_ENTRY.entry_id } });
    });
    const start = screen.getByRole('button', { name: 'Start a simulated paper session' });
    expect(start.disabled).toBe(false);

    await act(async () => {
      fireEvent.change(screen.getByLabelText('Initial simulated capital'), {
        target: { value: '100000.555' },
      });
    });

    expect(screen.getByLabelText('Initial simulated capital').getAttribute('aria-invalid')).toBe('true');
    expect(screen.getByText(/not an exact whole number of minor units/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Start a simulated paper session' }).disabled).toBe(true);
    expect(paperSessions.create).not.toHaveBeenCalled();
  });

  it("reports a stop answering complete: false as not complete", async () => {
    paperSessions.stop.mockResolvedValue({
      complete: false,
      session_state: 'STOPPED',
      outstanding: ['market_data_subscription'],
      finals_committed: false,
      finals_reason: 'feed_degraded',
      stale: true,
      loop_settled: false,
      registrations_closed: 0,
    });
    await renderWithSession();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Stop the selected session' }));
    });

    expect(screen.getByText('Stop not complete')).toBeTruthy();
    expect(screen.queryByText('Stop complete')).toBeNull();
    expect(screen.getByText('market_data_subscription')).toBeTruthy();
    expect(window.showToast).toHaveBeenCalledWith(
      'error',
      expect.stringContaining('the stop is not complete'),
    );
  });

  it("reports a stop answering complete: true as complete", async () => {
    // The other half, so the previous test is reading the flag rather than always saying no.
    paperSessions.stop.mockResolvedValue({
      complete: true,
      session_state: 'STOPPED',
      outstanding: [],
      finals_committed: true,
      finals_reason: null,
      stale: false,
      loop_settled: true,
      registrations_closed: 1,
    });
    await renderWithSession();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Stop the selected session' }));
    });

    expect(screen.getByText('Stop complete')).toBeTruthy();
    expect(screen.queryByText('Stop not complete')).toBeNull();
  });
});
