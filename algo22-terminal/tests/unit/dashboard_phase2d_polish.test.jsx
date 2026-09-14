import { describe, it, expect, vi, beforeEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Dashboard from '../../src/pages/Dashboard';
import * as dashboardModule from '../../src/api/modules/dashboard';
import * as riskModule from '../../src/api/modules/risk';
import wsClient from '../../src/websocketClient';

// `ds/Chart`, lazily imported since task 19.1b, is stubbed rather than recharts mocked —
// see `dashboard_phase2a_ui.test.jsx` for why the recharts mock that stood here stopped
// working. Nothing in this file asserts anything about the chart.
vi.mock('../../src/components/ds/Chart', () => {
  const Stub = (props) => <figure data-testid="chart" data-chart-kind={props.kind} />;
  return { __esModule: true, Chart: Stub, default: Stub };
});

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

  // The `2D.1: Standard / Dense Layout Toggle` block stood here. Its one test asserted the
  // toggle rendered `Standard`/`Dense` and wrote `vyomquant_dashboard_density` to
  // localStorage. vyomquant-ui-redesign task 19.4 deleted the toggle and the key: the
  // density preference has no requirement behind it and cost a second layout to maintain
  // (design.md §7.1). The test is deleted with the behaviour it covered rather than relaxed
  // into an assertion that would pass on any page — there is nothing left to assert.

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

      // Task 19.1 replaced the hand-styled `REAL CAPITAL ACTIVE` span with
      // `ds/TradingEnvironmentBadge`, whose LIVE label is "LIVE" (design.md §8.2).
      await waitFor(() => {
        expect(screen.getAllByText('LIVE').length).toBeGreaterThan(0);
      });

      // Event from paper environment should not alter live state
      wsClient._triggerEvent('risk.kill_switch_activated', {
        environment: 'paper',
        message: 'Simulated kill switch'
      });

      // The live kill switch must remain standby. `STANDBY (READY)` was the Risk & Safety
      // Matrix's row, which task 19.1b removed as a second copy of a state the control
      // itself reports. The control is task 19.2's and untouched, so standby is asserted
      // where it is now reported: the trigger still offers the halt rather than the resume,
      // no halted banner is on screen, and the diagnostics pill reads operational.
      expect(screen.getByText('EMERGENCY HALT')).toBeDefined();
      expect(screen.queryByText('RESUME TRADING')).toBeNull();
      expect(screen.queryByText(/EMERGENCY KILL SWITCH ACTIVE/i)).toBeNull();
      expect(screen.getByText('Engine Operational')).toBeDefined();
    });
  });

  describe('2D.6: Unmeasured Latency Neutral Fallback', () => {
    it('reports no per-venue latency, because neither venue field is measured', async () => {
      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );

      /*
       * The old per-venue row read `latency_ms` and printed "Latency unavailable" only for
       * the `null` one, which presented binance's 28 as a measurement. `pageFields`'
       * `exchangeHealth` note records that `status` and `latency_ms` are both constants in
       * the aggregation service, so task 19.1b stopped passing either to
       * `ds/ExchangeStatus`: every row reports its latency as not measured, with that
       * component's own reason, and the measured figure is the page-level
       * `health.exchange_api_latency_ms`.
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
      }
      expect(health.textContent).not.toContain('28 ms');
      // The one measured latency on the page, from `health`, with its unit.
      const latency = document.querySelector('[data-region="exchangeApiLatencyMs"]');
      expect(latency.getAttribute('data-metric-available')).toBe('true');
      expect(latency.textContent).toContain('28');
    });
  });
});
