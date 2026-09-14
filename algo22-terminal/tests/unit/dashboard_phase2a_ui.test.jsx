import { describe, it, expect, vi, beforeEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Dashboard, { floatVal } from '../../src/pages/Dashboard';
import * as dashboardModule from '../../src/api/modules/dashboard';

// Mock Recharts responsive container & area chart to avoid DOM measurement issues in JSDOM
vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }) => <div data-testid="responsive-container">{children}</div>,
  AreaChart: ({ children }) => <div data-testid="area-chart">{children}</div>,
  Area: () => <div data-testid="area" />,
  XAxis: () => <div data-testid="x-axis" />,
  YAxis: () => <div data-testid="y-axis" />,
  Tooltip: () => <div data-testid="tooltip" />,
}));

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
    expect(await screen.findByText('Open Positions (2)')).toBeDefined();
    expect(screen.getByText('BTC/USDT')).toBeDefined();
    expect(screen.getAllByText(/binance/i).length).toBeGreaterThan(0);
    expect(screen.getByText('ETH/USDT')).toBeDefined();
    expect(screen.getAllByText(/bybit/i).length).toBeGreaterThan(0);

    // Spot liquidation price must show dash ('—') and not '$0.00'
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);

    // Verify Recent Execution rendered
    expect(screen.getByText('Recent Order Executions (1)')).toBeDefined();
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

    // Verify Empty Positions state
    expect(screen.getByText('No open positions currently held')).toBeDefined();

    // Verify Blocked Kill Switch badge
    expect(screen.getByText('TRIGGERED (BLOCKED)')).toBeDefined();
  });

  it('correctly handles unmeasured latency with neutral fallback', async () => {
    vi.spyOn(dashboardModule.dashboardApi, 'getDashboard').mockResolvedValue(mockLiveResponse);

    render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
    );

    // Bybit venue has latency null -> should display 'Latency unavailable' (never 38ms or 0ms)
    expect(await screen.findByText('Latency unavailable')).toBeDefined();
    expect(screen.queryByText('38 ms')).toBeNull();
    expect(screen.queryByText('38ms')).toBeNull();
  });
});
