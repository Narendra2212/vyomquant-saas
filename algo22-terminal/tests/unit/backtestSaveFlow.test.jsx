/**
 * The Backtester's save-result flow, after it collapsed into the run (task 17.2).
 *
 * Requirement 10.1. `POST .../backtests/execute` creates the `strategy_backtests` row, runs,
 * and writes the metrics back before it answers, so a completed run is already persisted when
 * the response arrives. Everything asserted here follows from that:
 *
 * * **Nothing offers to save the run.** The "Save Backtest Run" button and its two-step
 *   `POST /strategies/{id}/backtests` + `PUT /backtests/{id}/results` glue are gone. Those
 *   endpoints still exist for other callers; what would be wrong is this page calling them,
 *   because against the extended endpoint that is a second row describing the same run.
 * * **The run appears in "Saved Backtest History" on completion.** By re-reading the list
 *   from the backend — the row the endpoint persisted — not by appending a locally
 *   assembled entry.
 * * **The history read travels the shared API module.** `endpoints.strategies.listBacktests`,
 *   not a raw `fetch` with a hand-rolled `Authorization` header.
 *
 * The run request's own shape is task 17.1's subject (`backtestExecution.test.js`) and is not
 * re-asserted here.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

/*
 * Charts are not the subject, and recharts needs layout APIs jsdom does not implement.
 *
 * The stub is `ds/Chart` and no longer `recharts`, because task 23.2 put both tier-2 curves
 * behind `lazy(() => import('../components/ds/Chart'))`: the page imports recharts nowhere,
 * and a `vi.mock('recharts', …)` can no longer satisfy the lazy chunk — it resolves
 * `ds/Chart`, which imports more of recharts than a hand-written factory declares, and the
 * rejected import takes the region down. Stubbing the module the page actually asks for is
 * the boundary that exists; it is what `dashboard_phase2a_ui.test.jsx` did at task 19.1b and
 * `portfolio-rendering.test.jsx` at 16.2. Nothing asserted below concerns a chart.
 */
vi.mock('../../src/components/ds/Chart', () => {
  const Stub = (props) => <figure data-testid="chart" data-chart-kind={props.kind} />;
  return { __esModule: true, Chart: Stub, default: Stub };
});

/*
 * The two market controls the configuration flow reuses from task 23.1 —
 * `builder/AssetSelector` and `builder/TimeframeSelector` — read the venue's asset universe
 * and the pipeline's published timeframe set on mount, both through the shared axios instance
 * rather than through `src/api`. Unmocked, mounting this page attempts two real requests that
 * jsdom sends at localhost and reports as unhandled `AggregateError`s.
 *
 * They are refused here rather than stubbed with a market list, for the same reason the charts
 * above are replaced rather than measured: market selection is not this suite's subject, and a
 * fabricated universe would be a list neither the page nor a trader could have got from the
 * platform. Both controls answer a refusal with their own error state and a retry, so what
 * renders during these tests is exactly what a trader sees when those reads fail — and the
 * save flow, which is the subject, is unaffected either way because the execute endpoint
 * accepts no market at all (SB-06).
 */
vi.mock('../../src/apiClient', () => {
  const refuse = () => Promise.reject(new Error('this suite makes no network requests'));
  return {
    default: { get: refuse, post: refuse, put: refuse, patch: refuse, delete: refuse },
    get: refuse,
    post: refuse,
    put: refuse,
    del: refuse,
    patch: refuse,
    publicGet: refuse,
    ApiError: Error,
    clearApiCache: () => {},
    getMetrics: () => ({}),
    getToken: () => null,
    isAuthenticated: () => false,
    logout: refuse,
    testConnection: refuse,
  };
});

const { mockStrategies, mockExchange } = vi.hoisted(() => ({
  mockStrategies: {
    list: vi.fn(),
    getById: vi.fn(),
    versions: vi.fn(),
    executeBacktest: vi.fn(),
    listBacktests: vi.fn(),
  },
  mockExchange: { getSupported: vi.fn() },
}));

vi.mock('../../src/api', () => ({
  endpoints: { strategies: mockStrategies, exchange: mockExchange },
}));

import Backtester from '../../src/pages/Backtester';

/** One execute response, as `execute_backtest` answers it. */
const executeResponse = () => ({
  backtest_id: 'bt-9',
  status: 'completed',
  results: {
    total_return_pct: 12.5,
    win_rate_pct: 55,
    max_drawdown_pct: -8.25,
    total_trades: 0,
    charts: {
      equity_curve: {
        values: [
          { timestamp: '2024-01-01T00:00:00Z', equity: 10000 },
          { timestamp: '2024-01-02T00:00:00Z', equity: 11250 },
        ],
      },
    },
  },
  strategy_id: 's-1',
  version_id: 'v-1',
  version: 'v1.0',
  configuration: { initial_capital: 10000 },
  ignored_fields: [],
});

/** One persisted `strategy_backtests` row, as `GET /api/backtests` lists it. */
const savedRow = () => ({
  id: 'bt-9',
  strategy_id: 's-1',
  version: 'v1.0',
  dataset: 'BTC/USDT',
  status: 'completed',
  created_at: '2024-01-02T00:00:00+00:00',
  total_return_pct: 12.5,
  win_rate: 0.55,
  max_drawdown: 0.0825,
  equity_curve: [{ timestamp: '2024-01-01T00:00:00Z', equity: 10000 }],
  blueprint: { name: 'Momentum v2', nodes: [], edges: [] },
});

const renderPage = () =>
  render(
    <MemoryRouter>
      <Backtester strategy={{ id: 's-1', name: 'Momentum v2', nodes: [], edges: [] }} />
    </MemoryRouter>,
  );

const run = async (user) => {
  await user.click(screen.getByRole('button', { name: /run backtest/i }));
};

/** Every URL the page passed to `fetch`, in order. */
const fetchedUrls = () => globalThis.fetch.mock.calls.map(([url]) => String(url));

describe('Backtester save-result flow (task 17.2)', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    mockStrategies.list.mockResolvedValue([]);
    mockStrategies.versions.mockResolvedValue({ versions: [{ id: 'v-1', is_current: true }] });
    mockStrategies.executeBacktest.mockResolvedValue(executeResponse());
    // Empty on mount, one row once the endpoint has persisted the run.
    mockStrategies.listBacktests.mockResolvedValueOnce({ backtests: [], total: 0 });
    mockStrategies.listBacktests.mockResolvedValue({ backtests: [savedRow()], total: 1 });
    mockExchange.getSupported.mockResolvedValue({ supported: ['BTC/USDT'] });

    // The pre-run historical-data check is the page's only remaining raw `fetch`, and task
    // 17.2 leaves it as it is. Stubbing it here is also what makes an unexpected call
    // visible: any other URL in `fetchedUrls()` is a request this page should not make.
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      json: async () => ({ valid: true, warnings: [] }),
    }));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('reads the history through the API module on mount, not a raw fetch', async () => {
    renderPage();

    await waitFor(() => expect(mockStrategies.listBacktests).toHaveBeenCalledWith({ limit: 20 }));
    expect(fetchedUrls().some((url) => /backtests(\?|$)/.test(url))).toBe(false);
  });

  it('offers no button to save a run that is already persisted', async () => {
    const user = userEvent.setup();
    renderPage();
    await run(user);

    await waitFor(() => expect(mockStrategies.executeBacktest).toHaveBeenCalledTimes(1));
    // Present before task 17.2, and only ever meaningful while the client did the saving.
    expect(screen.queryByRole('button', { name: /save backtest/i })).toBeNull();
    expect(screen.queryByText(/saved successfully/i)).toBeNull();
    expect(screen.queryByText(/failed to save backtest/i)).toBeNull();
  });

  it('issues no second write for the completed run', async () => {
    const user = userEvent.setup();
    renderPage();
    await run(user);

    await waitFor(() => expect(mockStrategies.executeBacktest).toHaveBeenCalledTimes(1));

    // The two-step glue's own requests: creating a row, then putting metrics into it.
    for (const url of fetchedUrls()) {
      expect(url).not.toMatch(/\/strategies\/[^/]+\/backtests$/);
      expect(url).not.toMatch(/\/backtests\/[^/]+\/results$/);
    }
    // And no create/update call sneaked onto the API module either.
    expect(mockStrategies).not.toHaveProperty('createBacktest');
    expect(mockStrategies).not.toHaveProperty('updateBacktestResults');
  });

  it('shows the persisted run in Saved Backtest History on completion', async () => {
    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(mockStrategies.listBacktests).toHaveBeenCalledTimes(1));
    expect(screen.queryByText('Saved Backtest History')).toBeNull();

    await run(user);

    // Refreshed after the run, so what appears is the row the backend holds.
    await waitFor(() => expect(mockStrategies.listBacktests).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('Saved Backtest History')).toBeTruthy();
    // Scoped to the table: the strategy selector carries the same name.
    expect(screen.getByRole('cell', { name: 'Momentum v2' })).toBeTruthy();
    expect(screen.getByRole('cell', { name: '12.50%' })).toBeTruthy();
    expect(screen.getByRole('cell', { name: 'BTC/USDT' })).toBeTruthy();
  });

  it('keeps the run reported as successful when the history refresh fails', async () => {
    const user = userEvent.setup();
    mockStrategies.listBacktests.mockReset();
    mockStrategies.listBacktests.mockRejectedValue(new Error('history unavailable'));
    vi.spyOn(console, 'error').mockImplementation(() => {});

    renderPage();
    await run(user);

    await waitFor(() => expect(mockStrategies.executeBacktest).toHaveBeenCalledTimes(1));
    // The run is persisted server-side whether or not this list could be re-read, so a
    // failed refresh is not reported as a failed run.
    await waitFor(() => expect(screen.queryByTestId('backtester-run-error')).toBeNull());
    expect(await screen.findByTestId('backtester-persisted-run')).toBeTruthy();
  });
});
