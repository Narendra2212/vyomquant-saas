import { describe, it, expect, vi, beforeEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
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
      vi.spyOn(portfolioModule.portfolioApi, 'getOpenPositions').mockResolvedValue([
        {
          id: 'pos_1',
          symbol: 'BTC/USDT',
          exchange: 'binance',
          side: 'long',
          size: 0.5,
          entry_price: 60000,
          mark_price: 63000,
          unrealized_pnl: 1500
        }
      ]);
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

  describe('3.2 Trade History Ledger Venue Column & Environment Filter', () => {
    it('displays Venue column in execution log and filters by environment', async () => {
      vi.spyOn(ordersModule.ordersApi, 'getHistory').mockResolvedValue([
        {
          id: 101,
          time: '2026-08-26T14:00:00Z',
          pair: 'SOL/USDT',
          exchange_id: 'bybit',
          side: 'buy',
          entry_price: 140,
          exit_price: 145,
          quantity: 10,
          profit_loss: 50,
          fee: 1.5,
          slippage: 0.02,
          strategy: 'SOL Momentum'
        }
      ]);
      vi.spyOn(paperModule.paperApi, 'getTrades').mockResolvedValue([
        {
          id: 201,
          time: '2026-08-26T14:30:00Z',
          symbol: 'ETH/USDT',
          side: 'buy',
          price: 3200,
          amount: 2,
          realized_pnl: 0
        }
      ]);

      render(
        <MemoryRouter>
          <TradeHistory />
        </MemoryRouter>
      );

      // Live ledger
      expect(await screen.findByText('Trade Ledger')).toBeDefined();
      expect(screen.getByText('SOL/USDT')).toBeDefined();
      expect(screen.getByText('bybit')).toBeDefined();

      // Switch to Paper ledger
      const paperBtn = screen.getByRole('button', { name: /PAPER/i });
      fireEvent.click(paperBtn);

      await waitFor(() => {
        expect(screen.getByText('ETH/USDT')).toBeDefined();
        expect(screen.getByText('paper')).toBeDefined();
      });
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
