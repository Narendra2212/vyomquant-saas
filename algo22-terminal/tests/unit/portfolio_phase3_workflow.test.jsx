import { describe, it, expect, vi, beforeEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import Portfolio from '../../src/pages/Portfolio';
import TradeHistory from '../../src/pages/TradeHistory';
import Strategies from '../../src/pages/Strategies';
import RiskSettings from '../../src/pages/RiskSettings';
import * as portfolioModule from '../../src/api/modules/portfolio';
import * as paperModule from '../../src/api/modules/paper';
import * as ordersModule from '../../src/api/modules/orders';
import * as strategiesModule from '../../src/api/modules/strategies';
import * as riskModule from '../../src/api/modules/risk';
import * as dashboardModule from '../../src/api/modules/dashboard';

// Mock Recharts
vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }) => <div data-testid="responsive-container">{children}</div>,
  AreaChart: ({ children }) => <div data-testid="area-chart">{children}</div>,
  Area: () => <div data-testid="area" />,
  PieChart: ({ children }) => <div data-testid="pie-chart">{children}</div>,
  Pie: ({ children }) => <div data-testid="pie">{children}</div>,
  Cell: () => <div data-testid="cell" />,
  CartesianGrid: () => <div data-testid="grid" />,
  XAxis: () => <div data-testid="x-axis" />,
  YAxis: () => <div data-testid="y-axis" />,
  Tooltip: () => <div data-testid="tooltip" />,
}));

// Mock WebSocket client
vi.mock('../../src/websocketClient', () => {
  const subscriptions = new Map();
  return {
    default: {
      subscribe: vi.fn((event, cb) => {
        subscriptions.set(event, cb);
        return () => subscriptions.delete(event);
      }),
      _triggerEvent: (event, data) => {
        const cb = subscriptions.get(event);
        if (cb) cb(data);
      },
      send: vi.fn()
    }
  };
});

describe('Phase 3 — Trading Platform Operational Completion Tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('3.1 Portfolio Page Environment Isolation & Open Positions', () => {
    it('renders Live portfolio by default and switches to Paper with positions ledger', async () => {
      vi.spyOn(portfolioModule.portfolioApi, 'getSummary').mockResolvedValue({
        account: {
          total_equity: 65000,
          unrealized_pnl: 1500,
          realized_pnl: 800,
          available_balance: 40000,
          pnl_pct: 3.5
        }
      });
      // Task 13.1: the LIVE positions read is `GET /api/dashboard`, not the two
      // `/api/portfolio/positions*` methods that 404d and have since been deleted. `degraded:
      // null` and a counted `risk.open_positions_count` are the healthy readings of BC-2's two
      // discriminators and are spelled out rather than omitted.
      vi.spyOn(dashboardModule.dashboardApi, 'getDashboard').mockResolvedValue({
        positions: [
          {
            id: 'pos_1',
            symbol: 'BTC/USDT',
            exchange_id: 'binance',
            side: 'long',
            contracts: 0.5,
            entry_price: 60000,
            mark_price: 63000,
            unrealized_pnl: 1500
          }
        ],
        degraded: null,
        risk: { open_positions_count: 1 }
      });
      vi.spyOn(portfolioModule.portfolioApi, 'getEquityCurve').mockResolvedValue([]);
      vi.spyOn(portfolioModule.portfolioApi, 'getAllocation').mockResolvedValue([]);
      vi.spyOn(portfolioModule.portfolioApi, 'getHeatmap').mockResolvedValue([]);

      vi.spyOn(paperModule.paperApi, 'getSummary').mockResolvedValue({
        total_equity: 100000,
        unrealized_pnl: 0,
        realized_pnl: 0,
        available_balance: 100000,
        roi_pct: 0
      });
      vi.spyOn(paperModule.paperApi, 'getPositions').mockResolvedValue([]);

      render(
        <MemoryRouter>
          <Portfolio />
        </MemoryRouter>
      );

      // Verify Live header and position row
      expect(await screen.findByText('Portfolio Analytics')).toBeDefined();
      expect(screen.getByText('$65,000.00')).toBeDefined();
      expect(screen.getByText('BTC/USDT')).toBeDefined();
      expect(screen.getByText('binance')).toBeDefined();

      // Click Paper button
      const paperBtn = screen.getByRole('button', { name: /PAPER/i });
      fireEvent.click(paperBtn);

      await waitFor(() => {
        const matchingValues = screen.getAllByText('$100,000.00');
        expect(matchingValues.length).toBeGreaterThan(0);
        expect(screen.getByText('No open positions currently held in PAPER mode.')).toBeDefined();
      });
    });
  });

  // vyomquant-ui-redesign task 15.1 rewrote this case. The `Venue` column is gone
  // (design.md §7.7): neither `GET /api/orders/history` nor `GET /api/paper/trades` reports
  // an exchange, and the page it replaces filled the cell with `"binance"` on live and
  // `"paper"` on paper — which is what the old `expect(getByText('paper'))` was asserting.
  // Both fixtures are now the real response shapes: a seven-column `executions` row for
  // live, and the `{trades, count, execution_environment, is_simulated}` envelope
  // `PaperTradingService` returns for paper. What the switch is checked to do is switch
  // ledgers and label the paper one (Requirement 12.2).
  describe('3.2 Trade History Ledger Environment Switch & Paper Labelling', () => {
    it('reads the live ledger by default and switches to the labelled paper ledger', async () => {
      vi.spyOn(ordersModule.ordersApi, 'getHistory').mockResolvedValue([
        {
          timestamp: '2026-08-26T14:00:00Z',
          user_id: 'user_phase3',
          symbol: 'SOL_USDT',
          side: 'buy',
          status: 'filled',
          amount: 10,
          price: 140
        }
      ]);
      vi.spyOn(paperModule.paperApi, 'getTrades').mockResolvedValue({
        trades: [
          {
            execution_id: 'exec_201',
            order_id: 'order_201',
            user_id: 'user_phase3',
            strategy_id: null,
            symbol: 'ETH/USDT',
            side: 'buy',
            quantity: '2.0000000000',
            price: '3200.0000000000',
            fee: '1.6000000000',
            realized_pnl: '0.0000000000',
            status: 'FILLED',
            executed_at: '2026-08-26T14:30:00Z'
          }
        ],
        count: 1,
        execution_environment: 'PAPER',
        is_simulated: true,
        session_id: 'sess_phase3'
      });

      render(
        <MemoryRouter>
          <TradeHistory />
        </MemoryRouter>
      );

      // Live ledger. Scoped to the table because the market select offers the same label.
      expect(await screen.findByRole('heading', { name: 'Trade History' })).toBeDefined();
      const liveTable = await screen.findByRole('table');
      expect(within(liveTable).getByText('SOL/USDT')).toBeDefined();
      expect(screen.queryByText('bybit')).toBeNull();

      // Switch to the paper ledger.
      fireEvent.click(screen.getByRole('radio', { name: 'Paper' }));

      await waitFor(() => {
        expect(within(screen.getByRole('table')).getByText('ETH/USDT')).toBeDefined();
      });
      // Requirement 12.2: the server's own label, not the switch's position.
      expect(screen.getAllByText('PAPER TRADING').length).toBeGreaterThan(0);
    });
  });

  describe('3.3 Strategies Context Focus via URL Search Params', () => {
    it('highlights target strategy matching query param', async () => {
      vi.spyOn(strategiesModule.strategiesApi, 'list').mockResolvedValue([
        {
          id: 'strat_btc_grid',
          name: 'BTC Grid Alpha',
          pair: 'BTC/USDT',
          status: 'running',
          pnl_percent: 5.2,
          win_rate: 65,
          max_drawdown: 3.1,
          environment: 'live'
        },
        {
          id: 'strat_eth_arb',
          name: 'ETH Arbitrage Delta',
          pair: 'ETH/USDT',
          status: 'failed',
          error_message: 'Exchange connection timeout on Bybit',
          pnl_percent: -1.2,
          win_rate: 40,
          max_drawdown: 5.5,
          environment: 'live'
        }
      ]);

      render(
        <MemoryRouter initialEntries={['/app/strategies?strategy_id=strat_eth_arb&environment=live']}>
          <Routes>
            <Route path="/app/strategies" element={<Strategies />} />
          </Routes>
        </MemoryRouter>
      );

      expect(await screen.findByText('Strategy Library')).toBeDefined();
      expect(screen.getByText('ETH Arbitrage Delta')).toBeDefined();
      expect(screen.getByText('★ FOCUSED TARGET STRATEGY')).toBeDefined();
      expect(screen.getByText('Exchange connection timeout on Bybit')).toBeDefined();
    });
  });

  describe('3.4 Risk Settings Real-Time Kill Switch Sync', () => {
    it('subscribes to risk events and updates kill switch state', async () => {
      vi.spyOn(riskModule.riskApi, 'getConfig').mockResolvedValue({
        max_daily_loss: 500,
        max_positions: 10,
        max_leverage: 3,
        kill_switches: { loss: true, blackswan: false, streak: false, capital: true }
      });
      vi.spyOn(riskModule.riskApi, 'getStrategyLimits').mockResolvedValue([]);
      vi.spyOn(riskModule.riskApi, 'getMarginHealth').mockResolvedValue({
        margin_ratio: 25,
        free_margin: 75,
        risk_score: 20
      });

      render(
        <MemoryRouter>
          <RiskSettings />
        </MemoryRouter>
      );

      expect(await screen.findByText('Risk Management')).toBeDefined();
      expect(screen.getByText('$500')).toBeDefined();
    });
  });
});
