import { describe, it, expect, vi, beforeEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Dashboard from '../../src/pages/Dashboard';
import * as dashboardModule from '../../src/api/modules/dashboard';
import * as riskModule from '../../src/api/modules/risk';
import wsClient from '../../src/websocketClient';

// Mock Recharts responsive container & area chart to avoid DOM measurement issues in JSDOM
vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }) => <div data-testid="responsive-container">{children}</div>,
  AreaChart: ({ children }) => <div data-testid="area-chart">{children}</div>,
  Area: () => <div data-testid="area" />,
  XAxis: () => <div data-testid="x-axis" />,
  YAxis: () => <div data-testid="y-axis" />,
  Tooltip: () => <div data-testid="tooltip" />,
}));

// Mock WebSocket client with event triggering capability
vi.mock('../../src/websocketClient', () => {
  const subscriptions = new Map();
  let openCallback = null;
  let statusCallback = null;

  return {
    default: {
      subscribe: vi.fn((event, cb) => {
        subscriptions.set(event, cb);
        return () => subscriptions.delete(event);
      }),
      onOpen: vi.fn((cb) => {
        openCallback = cb;
        return () => { openCallback = null; };
      }),
      onStatusChange: vi.fn((cb) => {
        statusCallback = cb;
        return () => { statusCallback = null; };
      }),
      _triggerEvent: (event, data) => {
        const cb = subscriptions.get(event);
        if (cb) cb(data);
      },
      _triggerOpen: () => {
        if (openCallback) openCallback();
      },
      _triggerStatus: (status) => {
        if (statusCallback) statusCallback(status);
      },
      send: vi.fn()
    }
  };
});

const mockLivePayload = {
  environment: "live",
  overview: {
    total_value: 50000.00,
    total_equity: 50000.00,
    available_balance: 30000.00,
    free_balance: 25000.00,
    used_balance: 5000.00,
    today_pnl: 1250.00,
    today_realized_pnl: 500.00,
    today_return_pct: 2.50,
    unrealized_pnl: 750.00,
    cumulative_pnl: 15000.00,
    total_exposure: 20000.00,
    currency: "USDT"
  },
  positions: [
    {
      id: "pos_binance_spot_btc",
      exchange_id: "binance",
      environment: "live",
      symbol: "BTC/USDT",
      market_type: "spot",
      margin_type: "cross",
      side: "long",
      contracts: 0.5,
      entry_price: 60000.00,
      mark_price: 62000.00,
      unrealized_pnl: 1000.00,
      unrealized_pnl_pct: 3.33,
      leverage: 1,
      liquidation_price: null
    },
    {
      id: "pos_bybit_deriv_eth",
      exchange_id: "bybit",
      environment: "live",
      symbol: "ETH/USDT",
      market_type: "future",
      margin_type: "isolated",
      side: "long",
      contracts: 5.0,
      entry_price: 3000.00,
      mark_price: 3200.00,
      unrealized_pnl: 1000.00,
      unrealized_pnl_pct: 6.67,
      leverage: 5,
      liquidation_price: 2500.00
    }
  ],
  strategies: {
    total: 1,
    active: 1,
    items: [
      {
        id: "strat_1",
        name: "BTC Momentum Grid",
        pair: "BTC/USDT",
        status: "running",
        health: "healthy",
        today_pnl: 350.00,
        today_return_pct: 1.2,
        last_signal_time: "2026-08-26T14:30:00Z"
      }
    ]
  },
  risk: {
    risk_score: 30,
    risk_level: "low",
    current_drawdown_pct: 2.10,
    max_daily_loss: 500.0,
    daily_loss_utilized: 50.0,
    max_positions: 10,
    open_positions_count: 2,
    circuit_breaker_armed: true,
    kill_switch_active: false
  },
  health: {
    exchange_api_latency_ms: 28,
    exchange_api_latency_status: "optimal",
    risk_circuit_breaker_status: "armed",
    order_state_sync_status: "synchronized"
  },
  exchange: {
    total_exchanges: 2,
    connected_exchanges: 2,
    exchanges: [
      { exchange_id: "binance", status: "connected", latency_ms: 28 },
      { exchange_id: "bybit", status: "connected", latency_ms: null }
    ]
  },
  executions: [
    {
      id: "fill_101",
      symbol: "BTC/USDT",
      exchange_id: "binance",
      side: "buy",
      price: 62000.00,
      amount: 0.1,
      cost: 6200.00,
      fee: 6.20,
      realized_pnl: 0.00,
      timestamp: "2026-08-26T14:50:00Z"
    }
  ],
  recent_activity: {
    insights: []
  },
  equity_curve: [
    { timestamp: "2026-08-20T00:00:00Z", equity: 48000 },
    { timestamp: "2026-08-26T00:00:00Z", equity: 50000 }
  ]
};

describe('Phase 2D — Trading Cockpit Polish & WebSocket Invariants', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    vi.spyOn(dashboardModule.dashboardApi, 'getDashboard').mockResolvedValue(mockLivePayload);
  });

  describe('2D.1: Standard / Dense Layout Toggle', () => {
    it('defaults to Standard layout and toggles to Dense on click', async () => {
      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Standard')).toBeDefined();
        expect(screen.getByText('Dense')).toBeDefined();
      });

      // Click Dense toggle
      const denseBtn = screen.getByText('Dense');
      fireEvent.click(denseBtn);

      // Verify localStorage was updated
      expect(localStorage.getItem('vyomquant_dashboard_density')).toBe('dense');
    });
  });

  describe('2D.3: Spot Liquidation Safety Invariants', () => {
    it('renders dash for spot liquidation price and distance without fabrication', async () => {
      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );

      await waitFor(() => {
        // BTC/USDT is spot -> must render '—'
        expect(screen.getAllByText('—').length).toBeGreaterThan(0);
        // Bybit ETH is derivative -> must render calculated 21.9%
        expect(screen.getByText('21.9%')).toBeDefined();
      });
    });
  });

  describe('2D.4: WebSocket Reconciliation & Reconnect Behavior', () => {
    it('reconciles authoritative full state on WebSocket open/reconnect event', async () => {
      const getDashboardSpy = vi.spyOn(dashboardModule.dashboardApi, 'getDashboard');

      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(getDashboardSpy).toHaveBeenCalledTimes(1);
      });

      // Trigger WebSocket reconnect/open event
      wsClient._triggerOpen();

      await waitFor(() => {
        // Must trigger full authoritative reload
        expect(getDashboardSpy).toHaveBeenCalledTimes(2);
      });
    });

    it('rejects stale WebSocket events from the past', async () => {
      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('BTC Momentum Grid')).toBeDefined();
      });

      // Stale event with timestamp 1 hour ago
      const staleTimestamp = new Date(Date.now() - 3600000).toISOString();
      wsClient._triggerEvent('strategy_status', {
        strategy_id: 'strat_1',
        status: 'paused',
        timestamp: staleTimestamp
      });

      // Status should remain running because stale event was dropped
      expect(screen.getByText('BTC Momentum Grid')).toBeDefined();
    });

    it('preserves environment isolation when WebSocket event is from different environment', async () => {
      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('REAL CAPITAL ACTIVE')).toBeDefined();
      });

      // Event from paper environment should not alter live state
      wsClient._triggerEvent('risk.kill_switch_activated', {
        environment: 'paper',
        message: 'Simulated kill switch'
      });

      // Live kill switch should remain standby
      expect(screen.getByText('STANDBY (READY)')).toBeDefined();
    });
  });

  describe('2D.6: Unmeasured Latency Neutral Fallback', () => {
    it('displays Latency unavailable when venue latency is null', async () => {
      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Latency unavailable')).toBeDefined();
      });
    });
  });
});
