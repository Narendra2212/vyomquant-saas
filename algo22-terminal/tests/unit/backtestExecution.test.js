/**
 * The Backtester's canonical run path, at the API layer (task 17.1).
 *
 * Requirements 5.1 and 4.7. Two things are asserted, both of them about the seam between the
 * page and `POST /api/strategy-operations/strategies/{id}/backtests/execute`:
 *
 * * **The request.** One POST to the canonical execute endpoint, carrying the immutable
 *   version and the window — and *not* a browser-assembled `dag`, symbol, or timeframe. The
 *   endpoint's request model forbids unknown fields, so a stray key here would be a 422
 *   rather than something silently ignored; that makes the exact key set part of the
 *   contract.
 * * **The response.** It arrives complete and synchronously (`{backtest_id, status, results}`
 *   — no `job_id`, nothing to poll), and `mapBacktestExecutionToUI` translates it into the
 *   one flat object the page renders without renaming, defaulting, or computing a metric.
 *
 * The page's own render behaviour is task 17.4's subject and is not duplicated here.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

const { mockClient } = vi.hoisted(() => ({
  mockClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
}));

vi.mock('../../src/apiClient', () => ({
  default: mockClient,
  get: (...args) => mockClient.get(...args),
  post: (...args) => mockClient.post(...args),
  put: (...args) => mockClient.put(...args),
  del: (...args) => mockClient.del(...args),
}));

import {
  strategiesApi,
  mapBacktestExecutionToUI,
  equitySeriesFromBacktestResults,
} from '../../src/api/modules/strategies';

/** One execute response, shaped exactly as `execute_backtest` answers. */
const executeResponse = () => ({
  backtest_id: 'bt-1',
  status: 'completed',
  results: {
    total_return_pct: 12.5,
    win_rate_pct: 55,
    max_drawdown_pct: -8.25,
    total_trades: 4,
    profit_factor: 1.8,
    sharpe_ratio: 1.2,
    sortino_ratio: 1.4,
    calmar_ratio: 0.9,
    trades: [{ trade_id: 0, side: 'BUY' }],
    charts: {
      equity_curve: {
        timestamps: [1, 2],
        values: [
          { timestamp: '2024-01-01T00:00:00Z', equity: 10000 },
          { timestamp: '2024-01-02T00:00:00Z', equity: 10250 },
        ],
      },
    },
  },
  strategy_id: 's-1',
  version_id: 'v-1',
  version: 'v1.0',
  configuration: { initial_capital: 10000, start_date: '2024-01-01', end_date: '2024-01-31' },
  ignored_fields: [],
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe('strategiesApi.executeBacktest', () => {
  it('posts the version and the window to the canonical execute endpoint', async () => {
    mockClient.post.mockResolvedValue(executeResponse());

    await strategiesApi.executeBacktest('s-1', {
      version_id: 'v-1',
      start_date: '2024-01-01',
      end_date: '2024-01-31',
      initial_capital: 10000,
    });

    expect(mockClient.post).toHaveBeenCalledTimes(1);
    const [url, body] = mockClient.post.mock.calls[0];

    expect(url).toBe('/api/strategy-operations/strategies/s-1/backtests/execute');
    expect(body.version_id).toBe('v-1');
    // The legacy job-queue payload's keys, none of which this endpoint accepts.
    for (const forbidden of ['dag', 'strategies', 'symbols', 'timeframe', 'exchange']) {
      expect(body).not.toHaveProperty(forbidden);
    }
  });

  it('encodes the strategy id rather than interpolating it raw', async () => {
    mockClient.post.mockResolvedValue(executeResponse());

    await strategiesApi.executeBacktest('a/b?c', { start_date: 'x', end_date: 'y' });

    expect(mockClient.post.mock.calls[0][0]).toBe(
      '/api/strategy-operations/strategies/a%2Fb%3Fc/backtests/execute',
    );
  });

  it('returns the whole response, with nothing to poll', async () => {
    mockClient.post.mockResolvedValue(executeResponse());

    const data = await strategiesApi.executeBacktest('s-1', { start_date: 'x', end_date: 'y' });

    expect(data.backtest_id).toBe('bt-1');
    expect(data.results.total_return_pct).toBe(12.5);
    expect(data).not.toHaveProperty('job_id');
    expect(mockClient.get).not.toHaveBeenCalled();
  });
});

describe('mapBacktestExecutionToUI', () => {
  it('flattens the engine metrics without renaming or defaulting one', () => {
    const response = executeResponse();

    const ui = mapBacktestExecutionToUI(response);

    for (const [key, value] of Object.entries(response.results)) {
      if (key === 'charts') continue;
      expect(ui[key]).toEqual(value);
    }
    expect(ui.backtest_id).toBe('bt-1');
    expect(ui.version).toBe('v1.0');
    expect(ui.configuration).toEqual(response.configuration);
  });

  it('reads the charted equity curve into the series the page renders', () => {
    const ui = mapBacktestExecutionToUI(executeResponse());

    expect(ui.equity).toEqual([
      { timestamp: '2024-01-01T00:00:00Z', equity: 10000 },
      { timestamp: '2024-01-02T00:00:00Z', equity: 10250 },
    ]);
  });

  it('pairs a bare numeric curve with its own timestamps', () => {
    const series = equitySeriesFromBacktestResults({
      charts: { equity_curve: { timestamps: [10, 20], values: [100, 110] } },
    });

    expect(series).toEqual([
      { timestamp: 10, equity: 100 },
      { timestamp: 20, equity: 110 },
    ]);
  });

  it('yields an empty series rather than inventing one when no curve was produced', () => {
    expect(equitySeriesFromBacktestResults({})).toEqual([]);
    expect(equitySeriesFromBacktestResults(undefined)).toEqual([]);
    expect(mapBacktestExecutionToUI({ backtest_id: 'bt-2', status: 'completed' }).equity).toEqual([]);
  });

  it('answers null for no response at all', () => {
    expect(mapBacktestExecutionToUI(null)).toBeNull();
    expect(mapBacktestExecutionToUI(undefined)).toBeNull();
  });
});

describe('strategiesApi.listBacktests', () => {
  // Task 17.2. This is what "Saved Backtest History" reads, on mount and again after a run
  // completes — the rows `POST .../backtests/execute` persisted, rather than an entry the
  // page saved for itself (Requirement 10.1).

  it('reads the mounted list route, which carries no strategy-operations segment', async () => {
    mockClient.get.mockResolvedValue({ backtests: [], total: 0 });

    await strategiesApi.listBacktests({ limit: 20 });

    // `strategy_operations.router` is mounted at `/api` and declares this route as
    // `/backtests` with no prefixed alias, so `/api/strategy-operations/backtests` — what the
    // raw `fetch` this replaced requested — resolves to nothing.
    expect(mockClient.get).toHaveBeenCalledWith('/api/backtests?limit=20');
  });

  it('narrows to one strategy when asked, and to none when not', async () => {
    mockClient.get.mockResolvedValue({ backtests: [], total: 0 });

    await strategiesApi.listBacktests({ strategyId: 'a/b', limit: 5 });
    expect(mockClient.get).toHaveBeenCalledWith('/api/backtests?strategy_id=a%2Fb&limit=5');

    await strategiesApi.listBacktests();
    expect(mockClient.get).toHaveBeenLastCalledWith('/api/backtests');
  });

  it('returns the persisted rows as the backend lists them', async () => {
    mockClient.get.mockResolvedValue({
      backtests: [{ id: 'bt-1', status: 'completed', total_return_pct: 12.5 }],
      total: 1,
    });

    const payload = await strategiesApi.listBacktests({ limit: 20 });

    expect(payload.backtests[0].id).toBe('bt-1');
    expect(payload.total).toBe(1);
  });
});

describe('strategiesApi.versions', () => {
  it('reads one strategy version history, so a run can name the current version', async () => {
    mockClient.get.mockResolvedValue({
      strategy_id: 's-1',
      versions: [{ id: 'v-2', is_current: false }, { id: 'v-1', is_current: true }],
      total: 2,
    });

    const payload = await strategiesApi.versions('s-1');

    expect(mockClient.get).toHaveBeenCalledWith('/api/strategies/s-1/versions');
    expect(payload.versions.find((row) => row.is_current).id).toBe('v-1');
  });
});
