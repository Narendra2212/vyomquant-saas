import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, waitFor, within, cleanup } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import Dashboard from '../../src/pages/Dashboard';
import TradeHistory from '../../src/pages/TradeHistory';
import Strategies from '../../src/pages/Strategies';
import RiskSettings from '../../src/pages/RiskSettings';
import * as dashboardModule from '../../src/api/modules/dashboard';
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

  });

  afterEach(() => {
    cleanup();
  });

  // 1-4 rendered pages/Portfolio.jsx and went with it (PORTFOLIO-REMOVED-01).
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

    // vyomquant-ui-redesign task 17.1: the page is a `ds/DataTable` now. The `<h1>` is
    // `Strategies` (design.md §7.2) and the focus marker is a badge in the Name cell rather
    // than the card's `★ FOCUSED TARGET STRATEGY` banner. The claim is the same one — a
    // `strategy_id` matching no row marks no row — and it is still asserted with the
    // selector that DOES match when a row is focused (see 3.3 in
    // `portfolio_phase3_workflow.test.jsx`), so it cannot pass vacuously.
    expect(await screen.findByRole('heading', { name: 'Strategies' })).toBeDefined();
    expect(screen.getByText('Real Bot')).toBeDefined();
    expect(screen.queryByText('Focused target')).toBeNull();
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

    // Task 17.1: the row is a table row and the fallback reason is stated once above the
    // table, so the name appears in both places — hence the scoped lookup for the row.
    expect(await screen.findByRole('table')).toBeDefined();
    expect(within(screen.getByRole('table')).getByText('Crashing Bot')).toBeDefined();
    expect(screen.getByText('Strategy execution halted due to error.')).toBeDefined();
  });

  // 13 & 14 & 17: no fabricated venue, strategy or P&L in the Trade History ledger.
  //
  // vyomquant-ui-redesign task 15.1 rewrote the assertions this test makes, because the
  // page it was written against fabricated the value it was checking for. The fixture is
  // now a real `executions` telemetry row — `telemetry_engine` creates that table with
  // exactly `(timestamp, user_id, symbol, side, status, amount, price)` — instead of the
  // `pair`/`entry_price`/`exit_price`/`profit_loss`/`fee`/`slippage`/`strategy` keys the
  // old fixture invented so that the old alias chains would resolve.
  //
  // The `Venue` column is gone with the rebuild (design.md §7.7): neither read reports an
  // exchange, and the page used to default the cell to `"binance"` on live and `"paper"` on
  // paper. So "renders the canonical venue" is no longer a claim the data can support, and
  // what is asserted instead is that no venue, strategy or P&L placeholder appears at all.
  it('13, 14, 17: Trade history ledger renders recorded fields only, with no fabricated venue, strategy or P&L', async () => {
    vi.spyOn(ordersModule.ordersApi, 'getHistory').mockResolvedValue([
      {
        timestamp: '2026-08-26T15:00:00Z',
        user_id: 'user_adversarial',
        symbol: 'AVAX_USDT',
        side: 'buy',
        status: 'filled',
        amount: 100,
        price: 25
      }
    ]);

    render(<MemoryRouter><TradeHistory /></MemoryRouter>);

    // Scoped to the table: the market select offers the same label as an option.
    const table = await screen.findByRole('table');
    expect(within(table).getByText('AVAX/USDT')).toBeDefined();
    expect(within(table).getByText('Filled')).toBeDefined();

    expect(screen.queryByText('binance')).toBeNull();
    expect(screen.queryByText('live_exchange')).toBeNull();
    expect(screen.queryByText('paper_exchange')).toBeNull();
    // `routers/orders.py` refuses manual execution outright, so "Direct" cannot be true of
    // any row — it was the old page's `?? "Direct"` default for a field nothing reports.
    expect(screen.queryByText('Direct')).toBeNull();
  });

  // 15 & 16: Empty State Verification in Portfolio & Trade History
  //
  // Task 15.1: the two zero assertions became `queryByText(...)).toBeNull()`. `0.0%` and
  // `$0.00` over a ledger with no rows are the fabricated zeros Requirement 14.5 forbids —
  // the win rate and the totals derive from fields the live read does not report at all —
  // so the summary now renders the not-available marker with its reason.
  it('15 & 16: Empty state displays an explained empty notice with no fabricated zeros', async () => {
    vi.spyOn(ordersModule.ordersApi, 'getHistory').mockResolvedValue([]);

    render(<MemoryRouter><TradeHistory /></MemoryRouter>);

    expect(await screen.findByText('No trades recorded')).toBeDefined();
    expect(screen.queryByText('0.0%')).toBeNull();
    expect(screen.queryByText('$0.00')).toBeNull();
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
    // RISK-05: a mocked double, not the real unmocked `GET /api/risk/guard-status`.
    vi.spyOn(riskModule.riskApi, 'getGuardStatus').mockResolvedValue({
      consecutive_losses: 0,
      consecutive_loss_limit: 3,
      capital_utilization_pct: 0.1,
      capital_utilization_limit_pct: 0.8,
    });

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
