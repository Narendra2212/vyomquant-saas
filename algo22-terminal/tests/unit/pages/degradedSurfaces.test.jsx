/**
 * ═══════════════════════════════════════════════════════════════════════════
 * tests/unit/pages/degradedSurfaces.test.jsx — loading, then a recoverable failure,
 * on every primary surface
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * production-launch-hardening task 12.13. Requirements 1.43, 2.43. design.md's row for
 * 1.43: "No empty frame, no permanent skeleton." bugfix.md 2.43: "WHEN an API call is slow
 * or fails, THEN every surface SHALL show a loading state and then a recoverable failure
 * state."
 *
 * WHICH SURFACES, AND WHY THESE FIVE AND NOT A GUESS FROM MEMORY
 * ----------------------------------------------------------------
 * The task's own hint list (Dashboard, PaperTrading, LiveTrading, Backtester, Portfolio) is
 * explicitly marked "verify by grep, not from memory" — and grepping `usePanelState(` across
 * `src/pages/*.jsx` shows two of those five do not actually call the hook for their primary
 * data at all:
 *
 *   * `PaperTrading.jsx` is driven by `paperTradingFormat.js`'s OWN eight-state vocabulary
 *     (`PAPER_TRADING_STATE_TO_PANEL_STATE`) — the hook's own docblock says this is
 *     deliberate and not yet migrated (task 25.1).
 *   * `Portfolio.jsx` and `Strategies.jsx` import `PANEL_STATES` as bare constants and derive
 *     each panel's state by hand from `isLoading`/error booleans; neither calls the hook.
 *
 * Of the pages that DO call `usePanelState(` for a primary, unconditional, top-of-page read,
 * this file covers the five whose wiring is a direct `state={x.state}` /
 * `error={{ error: x.error, onRetry: x.refetch }}` passthrough, which is what makes the
 * delayed/rejected/recovered story observable without reverse-engineering a page-specific
 * combinator (`LiveTrading`'s `selectorState` and `pageFailed`, and `Backtester`'s strategy
 * list, both combine multiple reads before reaching a `Panel`, which is a different and
 * larger claim than this task asks for):
 *
 *   1. **Dashboard** (`src/pages/Dashboard.jsx`) — `dashboardApi.getDashboard`. The one read
 *      renders a page-level `ds/ErrorState` on failure (Requirement 3.6), asserted the same
 *      way `dashboard-tier1.test.jsx` already asserts it.
 *   2. **TradeHistory** (`src/pages/TradeHistory.jsx`) — `api.orders.getHistory()`. The
 *      "Trades" `ds/Panel`.
 *   3. **StrategyMarketplace** (`src/pages/StrategyMarketplace.jsx`) — `api.library.browse` /
 *      `browsePublic`. The catalogue `ds/Panel`.
 *   4. **SecurityLogs** (`src/pages/SecurityLogs.jsx`) — `api.user.getSecurityLogs(100)`. The
 *      "Security events" `ds/Panel`.
 *   5. **ExchangeManager** (`src/pages/ExchangeManager.jsx`) — `api.exchange.list()`. The
 *      "Connected exchanges" `ds/Panel`.
 *
 * `usePanelState` IS THE VOCABULARY, IMPORTED AND NOT RESTATED
 * --------------------------------------------------------------
 * `STATES_WITHOUT_CHILDREN` is imported directly from `hooks/usePanelState.js` — the same
 * import `ds/Panel.jsx` itself uses — rather than a local list of "which states must render
 * no children". A ninth state added to the hook is picked up here without an edit, the same
 * property the production component already has.
 *
 * WHAT "NEITHER AN EMPTY FRAME NOR A PERMANENT SKELETON" MEANS, MADE CONCRETE
 * ------------------------------------------------------------------------------
 *   * An EMPTY FRAME would be a mount that renders no state indication at all — no
 *     `role="status"`, no `role="alert"`, no `[data-panel-state]`, nothing in the
 *     accessibility tree describing what is happening. Every assertion below finds a positive
 *     indicator for the state it is in, rather than merely failing to find an error.
 *   * A PERMANENT SKELETON would be a `role="status"` region that never resolves. Every
 *     delayed case here holds the read open on an uncontrolled promise, confirms the loading
 *     indicator is present WHILE the read is in flight, then resolves the promise and confirms
 *     the indicator is gone and replaced by the ready content — so "eventually resolves" is
 *     asserted, not assumed.
 *   * RECOVERABLE means the SAME mount reaches `ready` after a failure, through the hook's own
 *     `refetch` (wired to the panel's `onRetry`), with no remount and no full page reload.
 *
 * WHAT IS MOCKED, FOLLOWING THIS REPO'S OWN CONVENTION
 * -------------------------------------------------------
 * `src/api` is mocked at the module level, exactly as `riskSettingsKillSwitch.test.jsx` and
 * `strategyMarketplace.test.jsx` already do (`vi.mock('../../../src/api', () => ({ api,
 * default }))`) — both the named `api` export and the `default` export are covered because
 * different pages import it either way. `src/websocketClient` is mocked the same way
 * `riskSettingsKillSwitch.test.jsx` mocks it, for the one surface here (`Dashboard`) that
 * subscribes to it. Every `ds/*` primitive — `Panel`, `LoadingState`, `ErrorState`,
 * `EmptyState` — is REAL: the rendered markup is the thing under test, not a stand-in for it.
 */

import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { STATES_WITHOUT_CHILDREN } from '../../../src/hooks/usePanelState';

const { orders, library, user, exchange, wsSubscribe } = vi.hoisted(() => ({
  orders: { getHistory: vi.fn() },
  library: {
    featured: vi.fn(),
    featuredPublic: vi.fn(),
    trending: vi.fn(),
    trendingPublic: vi.fn(),
    categories: vi.fn(),
    categoriesPublic: vi.fn(),
    browse: vi.fn(),
    browsePublic: vi.fn(),
    subscriptionStatus: vi.fn(),
  },
  user: { getSecurityLogs: vi.fn() },
  exchange: { list: vi.fn(), getSupported: vi.fn(), getAuthSchema: vi.fn() },
  wsSubscribe: vi.fn(),
}));

// Both the named `api` export and the `default` export, because pages import it both ways
// (`ExchangeManager`/`SecurityLogs` import `{ api }`; `StrategyMarketplace` imports the
// default). One factory, both shapes, so neither page's import style is left unmocked.
vi.mock('../../../src/api', () => ({
  api: { orders, library, user, exchange },
  default: { orders, library, user, exchange },
}));
vi.mock('../../../src/websocketClient', () => ({
  default: { subscribe: wsSubscribe },
}));

// `dashboardApi.getDashboard` is spied on the real module, the same way
// `dashboard-tier1.test.jsx` does it — `Dashboard.jsx` imports `{ dashboardApi }` by path,
// not through `src/api`, so it is unaffected by the mock above.
import * as dashboardModule from '../../../src/api/modules/dashboard';
import { ApiError } from '../../../src/apiClient';

import Dashboard from '../../../src/pages/Dashboard';
import TradeHistory from '../../../src/pages/TradeHistory';
import StrategyMarketplace from '../../../src/pages/StrategyMarketplace';
import SecurityLogs from '../../../src/pages/SecurityLogs';
import ExchangeManager from '../../../src/pages/ExchangeManager';

/*
 * `ds/Chart` is stubbed, the same way `dashboard-tier1.test.jsx` stubs it: `Dashboard.jsx`
 * lazy-imports it for the equity curve, and none of this file's fixtures carry a series, so
 * the stub only has to exist for the lazy import to resolve — nothing here asserts anything
 * about it.
 */
vi.mock('../../../src/components/ds/Chart', () => {
  const Stub = (props) => <figure data-testid="chart" data-chart-kind={props.kind} />;
  return { __esModule: true, Chart: Stub, default: Stub };
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * SHARED HELPERS
 * ══════════════════════════════════════════════════════════════════════════════════════ */

/** A promise this test controls the settlement of, for the "delayed" half of every case. */
function controlledPromise() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/**
 * `ds/LoadingState`'s own marker: `role="status"` AND `data-loading-kind`.
 *
 * `role="status"` alone is not specific enough — `ds/DataTable`'s row-count line
 * (`"Showing 1–8 of 8"`) and `ds/FilterBar`'s result count are ALSO polite live regions, and
 * both are legitimately present while a panel is `ready`, not while it is `loading`. Only
 * `ds/LoadingState` writes `data-loading-kind`, so that is the unambiguous signature of the
 * loading state specifically, and the one this file's "not a permanent skeleton" claim is
 * actually about.
 */
const loadingRegions = (scope = document) => scope.querySelectorAll('[data-loading-kind]');

/** `role="alert"` — `ds/ErrorState`'s own marker, carried by both the panel and page arms. */
const alertRegions = (scope = document) => scope.querySelectorAll('[role="alert"]');

/** Every `[data-panel-state]` currently in one of the hook's children-suppressing states. */
const panelStatesWithoutChildren = () =>
  [...document.querySelectorAll('[data-panel-state]')].filter((el) =>
    STATES_WITHOUT_CHILDREN.includes(el.getAttribute('data-panel-state')),
  );

/**
 * The `ds/Panel` `<section>` whose own heading (its `<h2>`/`<h3>`) is exactly `title` — not a
 * substring match against the whole subtree, which is what let the "Ledger summary" panel's
 * `[data-panel-state]` stand in for the "Trades" panel's in an earlier version of this file.
 * Several panels can share a page, and each surface's assertions must be about ITS OWN read.
 */
const panelTitled = (title) =>
  [...document.querySelectorAll('[data-panel-state]')].find(
    (el) => el.querySelector(':scope > header h2, :scope > header h3')?.textContent === title,
  );

const mount = (children) => render(<MemoryRouter>{children}</MemoryRouter>);

beforeEach(() => {
  wsSubscribe.mockReturnValue(() => {});
  vi.restoreAllMocks();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * 1. DASHBOARD — page-level loading, page-level error, page-level recovery
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('Dashboard: loading then a recoverable failure', () => {
  const readSpy = () => vi.spyOn(dashboardModule.dashboardApi, 'getDashboard');

  const readyBody = () => ({
    environment: 'live',
    overview: {
      total_value: 1000,
      total_equity: 1000,
      today_pnl: 10,
      today_realized_pnl: 10,
      unrealized_pnl: 0,
      cumulative_pnl: 10,
      currency: 'USDT',
    },
    positions: [],
    executions: [],
    degraded: null,
    risk: {
      current_drawdown_pct_v2: 0,
      current_drawdown_pct: 0,
      open_positions_count: 0,
      circuit_breaker_armed: true,
      kill_switch_active: false,
    },
    health: { exchange_api_latency_ms: null, order_state_sync_status: 'active' },
    exchange: { total_exchanges: 0, connected_exchanges: 0, exchanges: [] },
    strategies: { total: 0, active: 0, items: [] },
    recent_activity: { signals: [], insights: [] },
    equity_curve: [],
  });

  /** Dashboard's own page-level failure marker (`dashboard-tier1.test.jsx`'s convention). */
  const pageError = () => document.querySelector('[data-region="page-error"]');

  it('shows a loading indicator while the read is in flight, and it is not permanent', async () => {
    const { promise, resolve } = controlledPromise();
    readSpy().mockReturnValue(promise);

    mount(<Dashboard />);

    // Neither an empty frame — a `role="status"` region is present — nor, yet, resolved.
    await waitFor(() => expect(loadingRegions().length).toBeGreaterThan(0));
    expect(pageError()).toBeNull();

    // Advancing past the delay: the loading indicator is not permanent.
    resolve(readyBody());
    await waitFor(() => expect(pageError()).toBeNull());
    expect(document.body.textContent).toContain('1,000.00');
  });

  it('shows one page-level recoverable error on rejection, and recovers to ready on retry', async () => {
    const spy = readSpy()
      .mockRejectedValueOnce(new ApiError('The dashboard read failed.', { status: 503 }))
      .mockResolvedValue(readyBody());

    mount(<Dashboard />);

    // The rejected half: the page-level error marker is present — not an empty frame — and
    // it carries `role="alert"` itself (Requirement 14.3/14.4).
    await waitFor(() => expect(pageError()).not.toBeNull());
    expect(pageError().getAttribute('role')).toBe('alert');
    expect(loadingRegions()).toHaveLength(0);
    const retry = within(pageError()).getByRole('button', { name: /try again/i });

    // Recoverable: the same mount, reconfigured to succeed, reaches `ready` through the
    // hook's own `refetch` — no remount, no reload.
    fireEvent.click(retry);

    await waitFor(() => expect(pageError()).toBeNull());
    expect(document.body.textContent).toContain('1,000.00');
    expect(spy).toHaveBeenCalledTimes(2);
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * 2. TRADE HISTORY — the "Trades" panel
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('TradeHistory: loading then a recoverable failure', () => {
  // Named, not "the first panel on the page" — this page's OTHER panel ("Ledger summary")
  // only ever reads `loading`/`ready` off the same `state` variable and never `error`, so a
  // positional selector here would silently assert against the wrong panel's DOM node.
  const tradesPanel = () => panelTitled('Trades');

  it('shows a loading indicator while the read is in flight, and it is not permanent', async () => {
    const { promise, resolve } = controlledPromise();
    orders.getHistory.mockReturnValue(promise);

    mount(<TradeHistory />);

    await waitFor(() => expect(tradesPanel()?.getAttribute('data-panel-state')).toBe('loading'));
    expect(loadingRegions(tradesPanel()).length).toBeGreaterThan(0);
    expect(alertRegions(tradesPanel())).toHaveLength(0);

    resolve([{ id: 't1', symbol: 'BTCUSDT', side: 'buy', quantity: 1, price: 100, timestamp: Date.now() }]);

    await waitFor(() =>
      expect(tradesPanel()?.getAttribute('data-panel-state')).not.toBe('loading'),
    );
    expect(loadingRegions(tradesPanel())).toHaveLength(0);
  });

  it('shows a recoverable panel error on rejection, and recovers to ready on retry', async () => {
    orders.getHistory
      .mockRejectedValueOnce(new Error('Network request failed'))
      .mockResolvedValue([
        { id: 't1', symbol: 'BTCUSDT', side: 'buy', quantity: 1, price: 100, timestamp: Date.now() },
      ]);

    mount(<TradeHistory />);

    await waitFor(() => expect(tradesPanel()?.getAttribute('data-panel-state')).toBe('error'));
    // Not an empty frame: the panel's own error body carries `role="alert"`, and no children
    // (the table) are rendered under it — the property `STATES_WITHOUT_CHILDREN` encodes.
    expect(alertRegions(tradesPanel()).length).toBeGreaterThan(0);
    expect(panelStatesWithoutChildren()).toContain(tradesPanel());

    fireEvent.click(within(tradesPanel()).getByRole('button', { name: /try again/i }));

    await waitFor(() => expect(tradesPanel()?.getAttribute('data-panel-state')).toBe('ready'));
    expect(alertRegions(tradesPanel())).toHaveLength(0);
    expect(orders.getHistory).toHaveBeenCalledTimes(2);
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * 3. STRATEGY MARKETPLACE — the catalogue panel
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('StrategyMarketplace: loading then a recoverable failure', () => {
  const catalogueListing = Object.freeze({
    listing_id: 'lst-1',
    name: 'Aurora Momentum',
    creator_alias: 'aurora-labs',
    category: 'trend',
    performance_summary: { total_return_pct: 1 },
    risk_metrics: { sharpe_ratio: 1 },
    subscriber_count: 1,
    price_minor: 0,
    currency: 'INR',
  });

  const catalogueContainer = () => document.querySelector('[data-panel-state]');

  beforeEach(() => {
    sessionStorage.setItem('token', 'test-token');
    // The three OTHER reads on this page — not this file's subject — resolve to something
    // harmless so the only in-flight/failing read is the catalogue's, on both the
    // authenticated and anonymous arm (the page picks one; both are mocked so it cannot
    // matter which).
    library.featured.mockResolvedValue({ items: [] });
    library.featuredPublic.mockResolvedValue({ items: [] });
    library.trending.mockResolvedValue({ items: [] });
    library.trendingPublic.mockResolvedValue({ items: [] });
    library.categories.mockResolvedValue({ categories: [] });
    library.categoriesPublic.mockResolvedValue({ categories: [] });
    library.subscriptionStatus.mockResolvedValue({ status: 'not_subscribed' });
  });

  afterEach(() => sessionStorage.clear());

  it('shows a loading indicator while the catalogue read is in flight, and it is not permanent', async () => {
    const { promise, resolve } = controlledPromise();
    library.browse.mockReturnValue(promise);
    library.browsePublic.mockReturnValue(promise);

    mount(<StrategyMarketplace />);

    await waitFor(() =>
      expect(catalogueContainer()?.getAttribute('data-panel-state')).toBe('loading'),
    );
    expect(loadingRegions(catalogueContainer()).length).toBeGreaterThan(0);

    resolve({ items: [catalogueListing], pages: 1 });

    await waitFor(() =>
      expect(catalogueContainer()?.getAttribute('data-panel-state')).not.toBe('loading'),
    );
    expect(loadingRegions(catalogueContainer())).toHaveLength(0);
  });

  it('shows a recoverable panel error on rejection, and recovers to ready on retry', async () => {
    library.browse
      .mockRejectedValueOnce(new Error('Marketplace read failed'))
      .mockResolvedValue({ items: [catalogueListing], pages: 1 });
    library.browsePublic
      .mockRejectedValueOnce(new Error('Marketplace read failed'))
      .mockResolvedValue({ items: [catalogueListing], pages: 1 });

    mount(<StrategyMarketplace />);

    await waitFor(() =>
      expect(catalogueContainer()?.getAttribute('data-panel-state')).toBe('error'),
    );
    expect(alertRegions(catalogueContainer()).length).toBeGreaterThan(0);
    expect(panelStatesWithoutChildren()).toContain(catalogueContainer());

    const retry = within(catalogueContainer()).getByRole('button', { name: /try again/i });
    fireEvent.click(retry);

    await waitFor(() =>
      expect(catalogueContainer()?.getAttribute('data-panel-state')).toBe('ready'),
    );
    expect(alertRegions(catalogueContainer())).toHaveLength(0);
    expect(screen.getByText('Aurora Momentum')).toBeTruthy();
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * 4. SECURITY LOGS — the "Security events" panel
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('SecurityLogs: loading then a recoverable failure', () => {
  // Exact title match, and scoped ("Account security summary" is a SECOND panel on this
  // page whose own state is `summaryPanelState`, derived from this same `logsState` — so it
  // legitimately enters `error` at the same moment `Security events` does, and a substring
  // match or an unscoped `getByRole` would find both panels' retry buttons at once).
  const logsPanel = () => panelTitled('Security events');

  it('shows a loading indicator while the read is in flight, and it is not permanent', async () => {
    const { promise, resolve } = controlledPromise();
    user.getSecurityLogs.mockReturnValue(promise);

    mount(<SecurityLogs />);

    await waitFor(() => expect(logsPanel()?.getAttribute('data-panel-state')).toBe('loading'));
    expect(loadingRegions(logsPanel()).length).toBeGreaterThan(0);

    resolve([{ id: 'e1', event_type: 'login', ip_address: '10.0.0.1', created_at: new Date().toISOString() }]);

    await waitFor(() => expect(logsPanel()?.getAttribute('data-panel-state')).not.toBe('loading'));
    expect(loadingRegions(logsPanel())).toHaveLength(0);
  });

  it('shows a recoverable panel error on rejection, and recovers to ready on retry', async () => {
    user.getSecurityLogs
      .mockRejectedValueOnce(new Error('Security log read failed'))
      .mockResolvedValue([
        { id: 'e1', event_type: 'login', ip_address: '10.0.0.1', created_at: new Date().toISOString() },
      ]);

    mount(<SecurityLogs />);

    await waitFor(() => expect(logsPanel()?.getAttribute('data-panel-state')).toBe('error'));
    expect(alertRegions(logsPanel()).length).toBeGreaterThan(0);
    expect(panelStatesWithoutChildren()).toContain(logsPanel());

    fireEvent.click(within(logsPanel()).getByRole('button', { name: /try again/i }));

    await waitFor(() => expect(logsPanel()?.getAttribute('data-panel-state')).toBe('ready'));
    expect(alertRegions(logsPanel())).toHaveLength(0);
    expect(user.getSecurityLogs).toHaveBeenCalledTimes(2);
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * 5. EXCHANGE MANAGER — the "Connected exchanges" panel
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('ExchangeManager: loading then a recoverable failure', () => {
  // Exact title match — this page also renders "Select an exchange" and "API credentials"
  // panels, so a scoped selector keeps every assertion here about the one read under test.
  const connectionsPanel = () => panelTitled('Connected exchanges');

  beforeEach(() => {
    // The other two reads on this page — `getSupported` (unconditional) and
    // `getAuthSchema` (gated on a selection that never happens here) — are not this
    // block's subject; the former resolves to something harmless so its own panel does
    // not sit in `error` and confuse the DOM query above.
    exchange.getSupported.mockResolvedValue({ exchanges: [], total: 0 });
  });

  it('shows a loading indicator while the read is in flight, and it is not permanent', async () => {
    const { promise, resolve } = controlledPromise();
    exchange.list.mockReturnValue(promise);

    mount(<ExchangeManager />);

    await waitFor(() =>
      expect(connectionsPanel()?.getAttribute('data-panel-state')).toBe('loading'),
    );
    expect(loadingRegions(connectionsPanel()).length).toBeGreaterThan(0);

    resolve([{ id: 'conn-1', exchange_id: 'binance', exchange: 'binance', is_active: true }]);

    await waitFor(() =>
      expect(connectionsPanel()?.getAttribute('data-panel-state')).not.toBe('loading'),
    );
    expect(loadingRegions(connectionsPanel())).toHaveLength(0);
  });

  it('shows a recoverable panel error on rejection, and recovers to ready on retry', async () => {
    exchange.list
      .mockRejectedValueOnce(new Error('Exchange connections read failed'))
      .mockResolvedValue([{ id: 'conn-1', exchange_id: 'binance', exchange: 'binance', is_active: true }]);

    mount(<ExchangeManager />);

    await waitFor(() => expect(connectionsPanel()?.getAttribute('data-panel-state')).toBe('error'));
    expect(alertRegions(connectionsPanel()).length).toBeGreaterThan(0);
    expect(panelStatesWithoutChildren()).toContain(connectionsPanel());

    const retry = within(connectionsPanel()).getByRole('button', { name: /try again/i });
    fireEvent.click(retry);

    await waitFor(() => expect(connectionsPanel()?.getAttribute('data-panel-state')).toBe('ready'));
    expect(alertRegions(connectionsPanel())).toHaveLength(0);
    expect(exchange.list).toHaveBeenCalledTimes(2);
  });
});
