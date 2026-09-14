import { describe, it, expect, vi, beforeEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Dashboard, { floatVal } from '../../src/pages/Dashboard';
import * as dashboardModule from '../../src/api/modules/dashboard';

// The equity curve is a lazily-imported `ds/Chart` since vyomquant-ui-redesign task 19.1b,
// so the page imports recharts nowhere and the `vi.mock('recharts', ...)` that stood here
// could no longer satisfy the lazy chunk (it resolves `ds/Chart`, which imports more of
// recharts than this mock declared, and the rejected import took the whole page down).
// Stubbing the module the page imports is `portfolio-rendering.test.jsx`'s approach since
// task 16.2, and nothing in this file asserts anything about the chart.
vi.mock('../../src/components/ds/Chart', () => {
  const Stub = (props) => <figure data-testid="chart" data-chart-kind={props.kind} />;
  return { __esModule: true, Chart: Stub, default: Stub };
});

const mockLiveResponse = {
  environment: "live",
  overview: {
    total_value: 45250.00,
    total_equity: 45250.00,
    available_balance: 25000.00,
    free_balance: 20000.00,
    used_balance: 5000.00,
    today_pnl: 550.00,
    today_realized_pnl: 250.00,
    today_return_pct: 1.22,
    unrealized_pnl: 300.00,
    cumulative_pnl: 5250.00,
    total_exposure: 20250.00,
    currency: "USDT"
  },
  positions: [
    {
      id: "pos_binance_btc",
      exchange_id: "binance",
      environment: "live",
      symbol: "BTC/USDT",
      market_type: "spot",
      side: "long",
      contracts: 0.5,
      entry_price: 64000.00,
      mark_price: 64600.00,
      unrealized_pnl: 300.00,
      unrealized_pnl_pct: 0.94,
      leverage: 1,
      liquidation_price: null
    },
    {
      id: "pos_bybit_eth",
      exchange_id: "bybit",
      environment: "live",
      symbol: "ETH/USDT",
      market_type: "perp",
      side: "short",
      contracts: 2.0,
      entry_price: 3600.00,
      mark_price: 3550.00,
      unrealized_pnl: 100.00,
      unrealized_pnl_pct: 1.39,
      leverage: 3,
      liquidation_price: 4800.00
    }
  ],
  executions: [
    {
      id: "exec_1",
      exchange_id: "binance",
      environment: "live",
      symbol: "SOL/USDT",
      side: "buy",
      price: 145.50,
      amount: 10.0,
      cost: 1455.00,
      fee: 1.45,
      realized_pnl: 0.0,
      timestamp: "2026-08-26T12:00:00Z"
    }
  ],
  risk: {
    risk_score: 25,
    risk_level: "low",
    current_drawdown_pct: 1.22,
    max_daily_loss: 500.0,
    daily_loss_utilized: 50.0,
    max_positions: 10,
    open_positions_count: 2,
    circuit_breaker_armed: true,
    kill_switch_active: false
  },
  health: {
    exchange_api_latency_ms: 42,
    exchange_api_latency_status: "optimal",
    risk_circuit_breaker_status: "armed",
    order_state_sync_status: "active"
  },
  exchange: {
    total_exchanges: 2,
    connected_exchanges: 2,
    exchanges: [
      { exchange_id: "binance", status: "connected", latency_ms: 42 },
      { exchange_id: "bybit", status: "connected", latency_ms: null }
    ]
  },
  strategies: {
    total: 1,
    active: 1,
    items: [
      {
        id: "strat_1",
        name: "BTC Trend Follower",
        pair: "BTC/USDT",
        status: "active",
        health: "healthy",
        today_pnl: 150.00,
        today_return_pct: 0.8
      }
    ]
  },
  equity_curve: [
    { timestamp: "2026-08-20T00:00:00Z", equity: 44000 },
    { timestamp: "2026-08-26T00:00:00Z", equity: 45250 }
  ]
};

const mockPaperResponse = {
  environment: "paper",
  overview: {
    total_value: 100000.00,
    total_equity: 100000.00,
    available_balance: 100000.00,
    free_balance: 100000.00,
    used_balance: 0.00,
    today_pnl: 0.00,
    today_realized_pnl: 0.00,
    today_return_pct: 0.00,
    unrealized_pnl: 0.00,
    cumulative_pnl: 0.00,
    total_exposure: 0.00,
    currency: "USD"
  },
  positions: [],
  executions: [],
  risk: {
    risk_score: 0,
    risk_level: "blocked",
    current_drawdown_pct: 0.0,
    max_daily_loss: 1000.0,
    daily_loss_utilized: 0.0,
    max_positions: 10,
    open_positions_count: 0,
    circuit_breaker_armed: false,
    kill_switch_active: true
  },
  health: {
    exchange_api_latency_ms: null,
    exchange_api_latency_status: "unavailable",
    risk_circuit_breaker_status: "triggered",
    order_state_sync_status: "synchronized"
  },
  exchange: {
    total_exchanges: 0,
    connected_exchanges: 0,
    exchanges: []
  },
  strategies: { items: [] },
  equity_curve: []
};

describe('Dashboard Phase 2A Frontend Unit Tests', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('floatVal safely parses numeric, string, and invalid inputs', () => {
    expect(floatVal(123.45)).toBe(123.45);
    expect(floatVal("456.78")).toBe(456.78);
    expect(floatVal(null)).toBe(0.00);
    expect(floatVal(undefined)).toBe(0.00);
    expect(floatVal("invalid")).toBe(0.00);
  });

  it('renders Live trading cockpit with hero capital metrics and authoritative positions', async () => {
    vi.spyOn(dashboardModule.dashboardApi, 'getDashboard').mockResolvedValue(mockLiveResponse);

    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    );

    // Verify Title & Live Environment Indicator.
    //
    // vyomquant-ui-redesign task 19.1: the title is `ds/PageHeader`'s `<h1>` and the
    // environment is `ds/TradingEnvironmentBadge`, whose LIVE label is "LIVE". The
    // hand-styled `REAL CAPITAL ACTIVE` span it replaces is gone, and the badge appears
    // more than once on the page — the tier-1 panel declares `money` and so carries one too.
    expect(await screen.findByText('Command Center')).toBeDefined();
    expect(screen.getAllByText('LIVE').length).toBeGreaterThan(0);

    // Verify tier 1 — the four §7.1 figures, in one container.
    //
    // `ds/Metric`'s `currency` format renders the grouped digits and puts the denomination
    // in its own span, so the figure text carries no `$`. The five hero cards these replace
    // rendered `$45,250.00`, `+$550.00 (+1.22%)` and `Lifetime P&L: +$5250.00`; today's
    // return percentage and the realised/unrealised split are not §7.1 tier-1 fields and
    // are not rendered here.
    expect(screen.getByText('45,250.00')).toBeDefined();   // overview.total_value
    expect(screen.getByText('550.00')).toBeDefined();      // overview.today_pnl, as ONE field
    expect(screen.getByText('5,250.00')).toBeDefined();    // overview.cumulative_pnl
    // BC-1's `risk.current_drawdown_pct_v2` is absent from this payload, so the figure is
    // the marker and its reason — never `0.00%`, and never the deprecated
    // `current_drawdown_pct: 1.22` sitting beside it, which is today's return.
    expect(screen.getByLabelText('Current drawdown: not available')).toBeDefined();
    expect(screen.queryByText('0.00%')).toBeNull();
    expect(screen.queryByText('1.22%')).toBeNull();

    // Verify Open Positions rendered with authoritative exchange_id.
    //
    // Awaited rather than read synchronously: tier 1 is derived during the render that
    // receives the payload, while the tier-2 zones are projected out of it in an effect, so
    // the positions table arrives one commit after the figures do.
    //
    // Task 19.1b re-points the two panel titles: the count is no longer interpolated into
    // the heading (`Open Positions (2)`), because `positions.length` and the server's
    // `risk.open_positions_count` are different quantities and only the second one can
    // report that it is unknown (BC-2). The heading is now the declared `pageFields` label
    // and the count is a `ds/Metric` beside the table, which is the field that renders the
    // marker when the read failed.
    expect(await screen.findByText('Open positions')).toBeDefined();
    expect(
      document.querySelector('[data-region="openPositionsCount"]').textContent,
    ).toContain('2');

    /*
     * Scoped to the positions region rather than to the document. `BTC/USDT` is now on the
     * page twice — once as the position's market and once as the deployment's, because the
     * fleet panel renders `strategies.items[].pair` beside each strategy — so an unscoped
     * `getByText` matches two elements and cannot say which surface it found. `within` is
     * the honest form of the same claim: the POSITIONS table names the market and the venue.
     */
    const positions = within(document.querySelector('[data-region="openPositions"]'));
    expect(positions.getByText('BTC/USDT')).toBeDefined();
    expect(positions.getAllByText(/binance/i).length).toBeGreaterThan(0);
    expect(positions.getByText('ETH/USDT')).toBeDefined();
    expect(positions.getAllByText(/bybit/i).length).toBeGreaterThan(0);

    // Spot liquidation price must show dash ('—') and not '$0.00'
    expect(positions.getAllByText('—').length).toBeGreaterThan(0);

    // Verify Recent Execution rendered. `Recent Order Executions (1)` was the same
    // count-in-the-heading pattern; the heading is the declared label and the row count is
    // the table's.
    expect(screen.getByText('Recent orders')).toBeDefined();
    expect(screen.getByText('SOL/USDT')).toBeDefined();
  });

  it('switches to Paper mode and displays simulation indicators & empty state', async () => {
    const getDashboardSpy = vi.spyOn(dashboardModule.dashboardApi, 'getDashboard')
      .mockResolvedValueOnce(mockLiveResponse)
      .mockResolvedValueOnce(mockPaperResponse);

    render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
    );

    // Initial Live mode
    expect(await screen.findAllByText('LIVE')).toBeDefined();

    // Select the PAPER ledger. Task 19.1 replaced the two hand-styled toggle buttons with
    // `ChipRadioGroup`'s radios — the selected one is no longer a `<button>` that does
    // nothing when pressed (Requirement 19.4).
    fireEvent.click(screen.getByRole('radio', { name: 'Paper' }));

    // Verify Paper environment request called
    await waitFor(() => {
      expect(getDashboardSpy).toHaveBeenCalledWith(expect.objectContaining({ environment: 'paper' }));
    });

    // Verify Paper indicators. `ds/TradingEnvironmentBadge` labels the paper ledger
    // "PAPER TRADING"; the figure and its denomination are the tier-1 metric's two spans.
    expect((await screen.findAllByText('PAPER TRADING')).length).toBeGreaterThan(0);
    expect(screen.getByText('Portfolio value')).toBeDefined();
    expect(screen.getAllByText('100,000.00').length).toBeGreaterThan(0);
    expect(screen.getAllByText('USD').length).toBeGreaterThan(0);

    // Verify Empty Positions state. Task 19.1b renders it through `ds/EmptyState`, which
    // Requirement 14.1 makes say what is missing, why it matters and what to do — the
    // headline is the first of those three.
    expect(screen.getByText('No open positions')).toBeDefined();

    // Verify the blocked state is reported. `TRIGGERED (BLOCKED)` was the Risk & Safety
    // Matrix's kill-switch row, which task 19.1b removed: it was a second copy of a state
    // the control itself reports, beside two limits the server never sent. The halt is
    // reported by the Requirement 3.3 strip and by the diagnostics pill — both task 19.2's
    // and both untouched — so those are what this asserts.
    expect(screen.getByText(/EMERGENCY KILL SWITCH ACTIVE/i)).toBeDefined();
    expect(screen.getByText('Trading Blocked')).toBeDefined();
    expect(screen.getByText(/RISK CIRCUIT BREAKER TRIGGERED/i)).toBeDefined();
  });

  it('correctly handles unmeasured latency with neutral fallback', async () => {
    vi.spyOn(dashboardModule.dashboardApi, 'getDashboard').mockResolvedValue(mockLiveResponse);

    render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
    );

    /*
     * The claim is unchanged — an unmeasured latency is never a number — but task 19.1b
     * moved where it is decided, and made it stronger. The old row rendered
     * `ex.latency_ms != null ? '${ex.latency_ms} ms' : 'Latency unavailable'` per venue, so
     * bybit's `null` read as unavailable and binance's `42` read as a measurement. Neither
     * per-venue field is a measurement: `pageFields`' `exchangeHealth` note records that
     * `status` and `latency_ms` are constants in the aggregation service. So NO venue row
     * reports latency now — `ds/ExchangeStatus` marks each one `data-latency-reported=false`
     * with its own reason — and the measured figure is `health.exchange_api_latency_ms`,
     * page-level and nullable, rendered as one `ds/Metric`.
     */
    // Waited on the venue ROWS, not on the panel: `data-region` is on the `ds/Panel`
    // section, which is in the DOM in its loading state too, so querying on the region
    // alone would read the skeleton.
    await waitFor(() => {
      expect(document.querySelectorAll('[data-exchange]').length).toBe(2);
    });

    const health = document.querySelector('[data-region="exchangeHealth"]');
    const venues = health.querySelectorAll('[data-exchange]');
    expect(venues.length).toBe(2);
    for (const venue of venues) {
      expect(venue.getAttribute('data-latency-reported')).toBe('false');
      expect(venue.getAttribute('data-connection-state')).toBe('unreported');
    }
    // Neither the venue that reported 42 nor the constant 35 is presented as a reading.
    expect(health.textContent).not.toContain('42 ms');
    expect(health.textContent).not.toContain('35 ms');
    expect(screen.queryByText('38 ms')).toBeNull();
    expect(screen.queryByText('38ms')).toBeNull();

    // The measured figure, which this payload does report, with its unit beside it.
    const latency = document.querySelector('[data-region="exchangeApiLatencyMs"]');
    expect(latency.getAttribute('data-metric-available')).toBe('true');
    expect(latency.textContent).toContain('42');
    expect(latency.textContent).toContain('ms');
  });
});
