import { describe, it, expect, vi, beforeEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Dashboard, { floatVal, computeLiquidationDistance } from '../../src/pages/Dashboard';
import * as dashboardModule from '../../src/api/modules/dashboard';
import * as riskModule from '../../src/api/modules/risk';

// `ds/Chart`, lazily imported since task 19.1b, is stubbed rather than recharts mocked —
// see `dashboard_phase2a_ui.test.jsx` for why the recharts mock that stood here stopped
// working. Nothing in this file asserts anything about the chart.
vi.mock('../../src/components/ds/Chart', () => {
  const Stub = (props) => <figure data-testid="chart" data-chart-kind={props.kind} />;
  return { __esModule: true, Chart: Stub, default: Stub };
});

// Mock WebSocket client
vi.mock('../../src/websocketClient', () => ({
  default: {
    subscribe: vi.fn(() => vi.fn()),
    send: vi.fn(),
  }
}));

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
    },
    {
      id: "pos_kraken_deriv_sol",
      exchange_id: "kraken",
      environment: "live",
      symbol: "SOL/USDT",
      market_type: "swap",
      margin_type: "cross",
      side: "short",
      contracts: 20.0,
      entry_price: 150.00,
      mark_price: 140.00,
      unrealized_pnl: 200.00,
      unrealized_pnl_pct: 6.67,
      leverage: 3,
      liquidation_price: 180.00
    }
  ],
  strategies: {
    total: 2,
    active: 1,
    paused: 0,
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
      },
      {
        id: "strat_2",
        name: "ETH Arbitrage Delta",
        pair: "ETH/USDT",
        status: "error",
        health: "error",
        error_message: "Rate limit exceeded on Bybit WebSocket",
        today_pnl: -50.00,
        today_return_pct: -0.2,
        last_signal_time: "2026-08-26T13:00:00Z"
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
    open_positions_count: 3,
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
    total_exchanges: 3,
    connected_exchanges: 3,
    exchanges: [
      { exchange_id: "binance", status: "connected", latency_ms: 28 },
      { exchange_id: "bybit", status: "connected", latency_ms: 35 },
      { exchange_id: "kraken", status: "connected", latency_ms: 45 }
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
    insights: [
      {
        id: "ins_1",
        type: "warning",
        text: "Bybit API rate limit usage reached 78% of capacity",
        actionPath: "/app/exchange",
        actionText: "Check Rate Limits"
      }
    ]
  },
  equity_curve: [
    { timestamp: "2026-08-20T00:00:00Z", equity: 48000 },
    { timestamp: "2026-08-26T00:00:00Z", equity: 50000 }
  ]
};

describe('Phase 2C — Dashboard Safety & Real-time Unit Tests', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(dashboardModule.dashboardApi, 'getDashboard').mockResolvedValue(mockLivePayload);
    vi.spyOn(riskModule.riskApi, 'killSwitch').mockResolvedValue({
      status: 'halted',
      kill_switch_active: true,
      message: 'Emergency Kill Switch is ACTIVE.'
    });
    vi.spyOn(riskModule.riskApi, 'recoverKillSwitch').mockResolvedValue({
      status: 'active',
      kill_switch_active: false,
      message: 'Kill switch recovered. Trading is active.'
    });
  });

  describe('P0.3: Liquidation Distance Mathematics', () => {
    it('computes positive distance for Long derivative position', () => {
      // Long: ((Mark - Liq) / Mark) * 100
      // ((3200 - 2500) / 3200) * 100 = 700 / 3200 * 100 = 21.875%
      const dist = computeLiquidationDistance(3200, 2500, 'long', 'future');
      expect(dist).toBeCloseTo(21.875, 2);
    });

    it('computes positive distance for Short derivative position', () => {
      // Short: ((Liq - Mark) / Mark) * 100
      // ((180 - 140) / 140) * 100 = 40 / 140 * 100 = 28.571%
      const dist = computeLiquidationDistance(140, 180, 'short', 'swap');
      expect(dist).toBeCloseTo(28.571, 2);
    });

    it('returns null for spot position', () => {
      const dist = computeLiquidationDistance(62000, null, 'long', 'spot');
      expect(dist).toBeNull();
    });

    it('returns null if liquidation price is null or zero', () => {
      expect(computeLiquidationDistance(100, 0, 'long', 'future')).toBeNull();
      expect(computeLiquidationDistance(100, null, 'long', 'future')).toBeNull();
    });
  });

  describe('P0.1: Emergency Halt UI Trigger & Modal Verification', () => {
    it('renders EMERGENCY HALT button and opens modal on click', async () => {
      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('EMERGENCY HALT')).toBeDefined();
      });

      // Click Emergency Halt
      fireEvent.click(screen.getByText('EMERGENCY HALT'));

      // Modal should be visible with confirmation text
      expect(screen.getByText('Activate Emergency Kill Switch')).toBeDefined();
      expect(screen.getByText(/WARNING: Activating the Emergency Kill Switch will IMMEDIATELY halt all live strategy execution/i)).toBeDefined();
    });

    it('activates kill switch when confirmed and updates risk state', async () => {
      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('EMERGENCY HALT')).toBeDefined();
      });

      fireEvent.click(screen.getByText('EMERGENCY HALT'));

      const confirmBtn = screen.getByText('Yes, HALT TRADING IMMEDIATELY');
      fireEvent.click(confirmBtn);

      await waitFor(() => {
        expect(riskModule.riskApi.killSwitch).toHaveBeenCalled();
      });
    });
  });

  describe('P0.2: Critical Operational Alert Banner', () => {
    /*
     * This asserted the opposite until task 19.2a: that `recent_activity.insights` reached
     * the banner. It no longer does, and the inversion is the point of the change rather
     * than a regression. `insights` has no `pageFields` entry, is not one of the three
     * declared inputs of the Requirement 3.3 condition, and two of the three items
     * `dashboard_aggregation_service` publishes are prose it hardcodes. A hardcoded sentence
     * rendered in a live region states a finding nothing measured (Requirement 14.5).
     *
     * The condition itself is `design/alertCondition.js`'s and is asserted in
     * `dashboard-alert-strip.test.jsx`; what is pinned here is that this field is not an
     * input to it.
     */
    it('does not render `recent_activity.insights` — it is not one of the declared inputs', async () => {
      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );

      // Wait for the ONE read to answer, then assert the absence — an assertion made before
      // the payload arrives would pass on an empty page.
      await waitFor(() => {
        expect(screen.getByText('Command Center')).toBeDefined();
        expect(document.querySelector('[data-page-tier="1"]')).not.toBeNull();
      });

      expect(screen.queryByText(/Bybit API rate limit usage reached 78% of capacity/i)).toBeNull();
      expect(screen.queryByText(/Check Rate Limits/i)).toBeNull();
    });

    it('renders kill switch alert when kill switch is active', async () => {
      const activeKsPayload = {
        ...mockLivePayload,
        risk: {
          ...mockLivePayload.risk,
          kill_switch_active: true,
          risk_level: "blocked"
        }
      };
      vi.spyOn(dashboardModule.dashboardApi, 'getDashboard').mockResolvedValue(activeKsPayload);

      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText(/EMERGENCY KILL SWITCH ACTIVE — ALL EXECUTIONS HALTED/i)).toBeDefined();
      });
    });
  });

  describe('P0.3: Derivatives Table Margin Mode and Liquidation Rendering', () => {
    it('renders margin modes and liquidation distance columns', async () => {
      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );

      await waitFor(() => {
        // Check margin mode labels
        expect(screen.getAllByText(/spot/i).length).toBeGreaterThan(0);
        expect(screen.getAllByText(/isolated/i).length).toBeGreaterThan(0);
        expect(screen.getAllByText(/cross/i).length).toBeGreaterThan(0);

        // Check liquidation distance values
        expect(screen.getByText('21.9%')).toBeDefined();
        expect(screen.getByText('28.6%')).toBeDefined();
      });
    });
  });

  describe('P1.3: Strategy Error Telemetry', () => {
    it('displays FAILED badge and error message for errored strategies', async () => {
      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('ETH Arbitrage Delta')).toBeDefined();
        // The status is `ds/StrategyStatus`'s since task 19.1b. It normalises the server's
        // `error` to `failed` and humanises it, so the badge reads "Failed" rather than the
        // old inline `FAILED` span — and, unlike that span, an ABSENT status renders
        // "Status not reported" instead of defaulting to paused.
        expect(screen.getByText('Failed')).toBeDefined();
        expect(document.querySelector('[data-strategy-status="failed"]')).not.toBeNull();
        // The server's own account of the failure, still verbatim and still beside the row.
        expect(screen.getByText(/Rate limit exceeded on Bybit WebSocket/i)).toBeDefined();
      });

      // `Inspect` was a per-row `<button onClick={navigate('/app/strategies')}>` — a
      // navigation wearing a button, which announces as the wrong thing and cannot be
      // opened in a new tab. It is one real `<a>` at panel level now, naming its
      // destination because `anchor-ambiguous-text` rejects a link whose text names none.
      const manage = screen.getByRole('link', { name: /manage strategies/i });
      expect(manage.getAttribute('href')).toBe('/app/strategies');
    });
  });
});
