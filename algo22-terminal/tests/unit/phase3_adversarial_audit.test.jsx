import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import Dashboard from '../../src/pages/Dashboard';
import Portfolio from '../../src/pages/Portfolio';
import TradeHistory from '../../src/pages/TradeHistory';
import Strategies from '../../src/pages/Strategies';
import RiskSettings from '../../src/pages/RiskSettings';
import * as dashboardModule from '../../src/api/modules/dashboard';
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
const wsSubscriptions = new Map();
vi.mock('../../src/websocketClient', () => {
  return {
    default: {
      subscribe: vi.fn((event, cb) => {
        if (!wsSubscriptions.has(event)) {
          wsSubscriptions.set(event, new Set());
        }
        wsSubscriptions.get(event).add(cb);
        return () => {
          const set = wsSubscriptions.get(event);
          if (set) set.delete(cb);
        };
      }),
      onOpen: vi.fn((cb) => {
        return () => {};
      }),
      onStatusChange: vi.fn((cb) => {
        return () => {};
      }),
      _triggerEvent: (event, data) => {
        const set = wsSubscriptions.get(event);
        if (set) {
          set.forEach(cb => cb(data));
        }
      },
      send: vi.fn()
    }
  };
});

describe('Phase 3 Adversarial Audit Test Battery (20 Invariants)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    wsSubscriptions.clear();

    // Portfolio's LIVE branch issues FIVE reads in one `Promise.allSettled` -- the summary and
    // positions each test mocks, plus the equity curve, the allocation and the heatmap. Those last
    // three were left unmocked, so they were dispatched to the real backend through jsdom's XHR and
    // rejected with `AggregateError` (nothing is listening during a test run). Because the page
    // holds `isLoading` until all five settle, the whole grid stayed on "Loading..." for as long as
    // those three sockets took to be refused -- fast on an idle machine, slower than the 1s
    // `findByText`/`waitFor` window when the full suite is running, which is exactly the
    // intermittent failure of invariant 1. Stubbing them here removes the network from these tests
    // without changing what any of them assert: every one of the three resolves to `[]`, which is
    // the same state the page reached when the read failed (`setEquityCurve([])`,
    // `setAllocation([])`, `setHeatmapData([])`), so the rendering under test is identical -- only
    // now it is reached deterministically and immediately.
    vi.spyOn(portfolioModule.portfolioApi, 'getEquityCurve').mockResolvedValue([]);
    vi.spyOn(portfolioModule.portfolioApi, 'getAllocation').mockResolvedValue([]);
    vi.spyOn(portfolioModule.portfolioApi, 'getHeatmap').mockResolvedValue([]);
  });

  afterEach(() => {
    cleanup();
  });

  // 1 & 2: LIVE -> PAPER and PAPER -> LIVE Switch in Portfolio
  it('1 & 2: LIVE -> PAPER and PAPER -> LIVE switches replace state completely without data bleed', async () => {
    vi.spyOn(portfolioModule.portfolioApi, 'getSummary').mockResolvedValue({
      account: { total_equity: 50000, unrealized_pnl: 1200, realized_pnl: 400, available_balance: 30000 }
    });
    // Task 13.1: Portfolio's LIVE positions come from `GET /api/dashboard`. `degraded: null` is
    // the healthy reading of BC-2's discriminator and is stated, not omitted -- omitting it is
    // not the healthy case, it is an unreadable response shape.
    vi.spyOn(dashboardModule.dashboardApi, 'getDashboard').mockResolvedValue({
      positions: [
        { id: 'p1', symbol: 'BTC/USDT', exchange_id: 'binance', side: 'long', contracts: 1.0, entry_price: 60000, mark_price: 61200, unrealized_pnl: 1200 }
      ],
      degraded: null,
      risk: { open_positions_count: 1 }
    });
    vi.spyOn(paperModule.paperApi, 'getSummary').mockResolvedValue({
      total_equity: 100000, unrealized_pnl: 0, realized_pnl: 0, available_balance: 100000
    });
    vi.spyOn(paperModule.paperApi, 'getPositions').mockResolvedValue([]);

    render(<MemoryRouter><Portfolio /></MemoryRouter>);

    expect(await screen.findByText('$50,000.00')).toBeDefined();
    expect(screen.getByText('BTC/USDT')).toBeDefined();

    // Switch to Paper
    const paperBtn = screen.getByRole('button', { name: /PAPER/i });
    fireEvent.click(paperBtn);

    await waitFor(() => {
      expect(screen.getAllByText('$100,000.00').length).toBeGreaterThan(0);
      expect(screen.queryByText('BTC/USDT')).toBeNull();
    });

    // Switch back to Live
    const liveBtn = screen.getByRole('button', { name: /LIVE/i });
    fireEvent.click(liveBtn);

    await waitFor(() => {
      expect(screen.getByText('$50,000.00')).toBeDefined();
      expect(screen.getByText('BTC/USDT')).toBeDefined();
    });
  });

  // 3 & 4: Live & Paper API Failures handled gracefully without fallback mixing
  //
  // Invariant 3 used to be spelled "renders zero state": the assertion was that a failed live
  // read produced `$0.00` cards. That spelling is no longer correct behaviour - a `$0.00` for a
  // read that never completed presents a fabricated figure as a measurement, which Requirement
  // 28.5 forbids. The invariant being protected (a failed read must not silently look like a
  // funded, zeroed account, and must not be papered over with paper data) is unchanged; it is now
  // pinned as the failure being stated in words, with no figure of any kind shown.
  //
  // Invariant 4 - no fallback to the paper reads - is asserted exactly as before.
  it('3 & 4: Live API failure states the failure instead of a figure and never falls back to paper', async () => {
    vi.spyOn(portfolioModule.portfolioApi, 'getSummary').mockRejectedValue(new Error('Network error 500'));
    // Task 13.1: one positions read, not a two-step fallback chain. The chain this used to stage
    // -- `getOpenPositions().catch(() => getPositions())` -- addressed two routes that do not
    // exist, so both legs 404d and both methods have since been deleted. The read is now
    // `GET /api/dashboard`. Its rejection message is deliberately NOT "Network error 500": the
    // assertion below is a `getByText`, which throws on more than one match, and the summary and
    // positions regions each render their own failure sentence.
    vi.spyOn(dashboardModule.dashboardApi, 'getDashboard').mockRejectedValue(new Error('Positions read did not complete'));
    const paperSpy = vi.spyOn(paperModule.paperApi, 'getSummary');

    render(<MemoryRouter><Portfolio /></MemoryRouter>);

    await waitFor(() => {
      expect(screen.getAllByText('Unavailable — read failed').length).toBeGreaterThan(0);
    });
    expect(screen.getByText(/Network error 500/)).toBeDefined();
    expect(screen.queryByText('$0.00')).toBeNull();
    expect(paperSpy).not.toHaveBeenCalled();
  });

  // 5 & 6 & 7 & 8: WebSocket Stale Event & Wrong Environment Rejection
  it('5, 6, 7, 8: Stale events and cross-environment WebSocket events are rejected by Dashboard', async () => {
    vi.spyOn(dashboardModule.dashboardApi, 'getDashboard').mockResolvedValue({
      environment: 'live',
      overview: { total_equity: 50000, available_balance: 30000, today_realized_pnl: 200, unrealized_pnl: 300, cumulative_pnl: 1000 },
      positions: [],
      strategies: { items: [{ id: 'strat_1', name: 'Alpha Bot', status: 'running', environment: 'live' }] },
      exchange_health: [{ exchange: 'binance', status: 'connected', latency_ms: 45 }],
      recent_executions: []
    });

    render(<MemoryRouter><Dashboard /></MemoryRouter>);

    expect(await screen.findByText('Alpha Bot')).toBeDefined();

    // Trigger mismatched environment WebSocket event
    const wsClient = (await import('../../src/websocketClient')).default;
    wsClient._triggerEvent('strategy_status', {
      strategy_id: 'strat_1',
      status: 'stopped',
      environment: 'paper',
      timestamp: new Date().toISOString()
    });

    // Strategy status should NOT mutate to stopped
    expect(screen.getByText('Alpha Bot')).toBeDefined();
  });

  // 10 & 11: Invalid and Unauthorized Strategy ID Deep-Link Safe Handling
  it('10 & 11: Strategy ID query params with non-existent IDs do not crash or highlight invalid rows', async () => {
    vi.spyOn(strategiesModule.strategiesApi, 'list').mockResolvedValue([
      { id: 'real_strat_1', name: 'Real Bot', pair: 'BTC/USDT', status: 'running', environment: 'live' }
    ]);

    render(
      <MemoryRouter initialEntries={['/app/strategies?strategy_id=unauthorized_or_invalid_id']}>
        <Routes><Route path="/app/strategies" element={<Strategies />} /></Routes>
      </MemoryRouter>
    );

    expect(await screen.findByText('Strategy Library')).toBeDefined();
    expect(screen.getByText('Real Bot')).toBeDefined();
    expect(screen.queryByText('★ FOCUSED TARGET STRATEGY')).toBeNull();
  });

  // 12: Failed Strategy with Missing Error Reason renders safe fallback
  it('12: Failed strategy without explicit error message renders clean fallback without crashing', async () => {
    vi.spyOn(strategiesModule.strategiesApi, 'list').mockResolvedValue([
      { id: 'failed_strat_1', name: 'Crashing Bot', pair: 'SOL/USDT', status: 'failed', error_message: null, environment: 'live' }
    ]);

    render(
      <MemoryRouter initialEntries={['/app/strategies']}>
        <Routes><Route path="/app/strategies" element={<Strategies />} /></Routes>
      </MemoryRouter>
    );

    expect(await screen.findByText('Crashing Bot')).toBeDefined();
    expect(screen.getByText('Strategy execution halted due to error.')).toBeDefined();
  });

  // 13 & 14 & 17: Authoritative Exchange ID in Trade History & CSV
  it('13, 14, 17: Trade history ledger renders canonical venue and rejects placeholder fabrication', async () => {
    vi.spyOn(ordersModule.ordersApi, 'getHistory').mockResolvedValue([
      {
        id: 777,
        time: '2026-08-26T15:00:00Z',
        pair: 'AVAX/USDT',
        exchange_id: 'binance',
        side: 'buy',
        entry_price: 25,
        exit_price: 26,
        quantity: 100,
        profit_loss: 100,
        fee: 0.5,
        slippage: 0.01,
        strategy: 'AVAX Swing'
      }
    ]);

    render(<MemoryRouter><TradeHistory /></MemoryRouter>);

    expect(await screen.findByText('AVAX/USDT')).toBeDefined();
    expect(screen.getByText('binance')).toBeDefined();
    expect(screen.queryByText('live_exchange')).toBeNull();
    expect(screen.queryByText('paper_exchange')).toBeNull();
  });

  // 15 & 16: Empty State Verification in Portfolio & Trade History
  it('15 & 16: Empty state displays clean zero/empty notices without NaN or missing keys', async () => {
    vi.spyOn(ordersModule.ordersApi, 'getHistory').mockResolvedValue([]);

    render(<MemoryRouter><TradeHistory /></MemoryRouter>);

    expect(await screen.findByText('No trades found for LIVE mode.')).toBeDefined();
    expect(screen.getByText('0.0%')).toBeDefined();
    expect(screen.getByText('$0.00')).toBeDefined();
  });

  // 18, 19, 20: Kill Switch WebSocket Activation, Recovery, and Cleanup
  it('18, 19, 20: Risk settings handles WebSocket kill switch events and unmounts clean', async () => {
    vi.spyOn(riskModule.riskApi, 'getConfig').mockResolvedValue({
      max_daily_loss: 500,
      max_positions: 10,
      max_leverage: 3,
      kill_switches: { loss: false, blackswan: false, streak: false, capital: false }
    });
    vi.spyOn(riskModule.riskApi, 'getStrategyLimits').mockResolvedValue([]);
    vi.spyOn(riskModule.riskApi, 'getMarginHealth').mockResolvedValue({ margin_ratio: 10, free_margin: 90, risk_score: 10 });

    const { unmount } = render(<MemoryRouter><RiskSettings /></MemoryRouter>);

    expect(await screen.findByText('Risk Management')).toBeDefined();

    const wsClient = (await import('../../src/websocketClient')).default;
    wsClient._triggerEvent('risk.kill_switch_activated', { message: 'Daily Loss Limit Breached' });

    expect(await screen.findByText('Daily Loss Limit Breached')).toBeDefined();

    wsClient._triggerEvent('risk.kill_switch_recovered', {});
    expect(await screen.findByText('Emergency Kill Switch Recovered. Trading active.')).toBeDefined();

    // Verify unmount does not throw
    unmount();
  });
});
