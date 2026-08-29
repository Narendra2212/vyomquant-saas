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

    // Verify Title & Live Environment Indicator
    expect(await screen.findByText('Trading Cockpit')).toBeDefined();
    expect(screen.getByText('REAL CAPITAL ACTIVE')).toBeDefined();

    // Verify Primary Capital Hero Cards
    expect(screen.getByText('$45,250.00')).toBeDefined();
    expect(screen.getByText('+$550.00')).toBeDefined();
    expect(screen.getByText('(+1.22%)')).toBeDefined();
    expect(screen.getByText('Lifetime P&L: +$5250.00')).toBeDefined();

    // Verify Open Positions rendered with authoritative exchange_id
    expect(screen.getByText('Open Positions (2)')).toBeDefined();
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
    expect(await screen.findByText('REAL CAPITAL ACTIVE')).toBeDefined();

    // Click PAPER button
    const paperButton = screen.getByRole('button', { name: /PAPER/i });
    fireEvent.click(paperButton);

    // Verify Paper environment request called
    await waitFor(() => {
      expect(getDashboardSpy).toHaveBeenCalledWith(expect.objectContaining({ environment: 'paper' }));
    });

    // Verify Paper indicators
    expect(await screen.findByText('SIMULATED EXECUTION')).toBeDefined();
    expect(screen.getByText('Total Equity (USD)')).toBeDefined();
    expect(screen.getAllByText('$100,000.00').length).toBeGreaterThan(0);

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
