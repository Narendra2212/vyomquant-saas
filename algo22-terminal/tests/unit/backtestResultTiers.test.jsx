/**
 * The Backtester's RESULT region, on §7.4's three declared tiers (task 23.2).
 *
 * Requirements 6.2, 6.3, 6.4, 6.6, 14.5, 19.3. The configuration flow is task 23.1's subject
 * (`backtestStrategySelection.test.jsx`), the save flow is 17.2's (`backtestSaveFlow.test.jsx`)
 * and the drawdown arithmetic is 23.3's (`lib/drawdownSeries.test.js`); none of the three is
 * re-asserted here. What this file establishes is the four claims the region is built on:
 *
 *   1. **The three tiers render in the declared order**, each in ONE container carrying
 *      `pageHierarchy`'s `data-page` + `data-page-tier` pair, and tier 1 holds exactly the six
 *      figures the declaration lists — read by their DECLARED labels, so a renamed field fails
 *      here rather than passing against whatever the page currently says.
 *   2. **Requirement 6.6: a failed run renders NO tier-1 figure at all.** Not zeros, and not a
 *      rendered row of em-dashes — the container itself is absent, and an `ErrorState` with a
 *      retry stands in its place. This is the clause task 23.5's Property 4 registration
 *      depends on, and it is the one page in that registry where the vacuous branch is
 *      reachable at all.
 *   3. **A completed run with one figure missing is the OTHER case**, and it renders that one
 *      figure's not-available marker beside the five that were read. The distinction against
 *      (2) is the whole point: a row of dashes still asserts "this run has results".
 *   4. **An equity point the wire omitted is a gap, and the gap is stated in words.** The
 *      mapper reads a missing equity as `null` rather than `0`, so the drawdown derivation
 *      cannot report a crash to zero, and `REASON_UNREADABLE_EQUITY` renders whenever any
 *      point could not be read.
 *
 * Charts are stubbed at `ds/Chart` — the module the page imports — because recharts needs
 * layout APIs jsdom does not implement and the page reaches it through
 * `lazy(() => import(...))`. Its interior is `tests/unit/ds/Chart.test.jsx`'s subject; what is
 * asserted here is the props the page hands it, which is the contract this page owes.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

const charts = vi.hoisted(() => ({ calls: [] }));

vi.mock('../../src/components/ds/Chart', () => {
  const Stub = (props) => {
    charts.calls.push(props);
    return (
      <figure
        data-testid="chart"
        data-chart-kind={props.kind}
        data-chart-points={Array.isArray(props.data) ? props.data.length : -1}
        data-chart-series={(props.series ?? []).map((entry) => entry.key).join(',')}
        data-chart-y={props.yAxis?.label}
      />
    );
  };
  return { __esModule: true, Chart: Stub, default: Stub };
});

/*
 * The two market controls read the venue's asset universe and the published timeframe set on
 * mount, through the shared axios instance rather than through `src/api`. Refused rather than
 * stubbed with a fabricated universe, exactly as the other two Backtester suites do: market
 * selection is not this suite's subject and neither the market nor the interval travels with
 * the run (SB-06).
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

const { mockStrategies } = vi.hoisted(() => ({
  mockStrategies: {
    list: vi.fn(),
    getById: vi.fn(),
    versions: vi.fn(),
    executeBacktest: vi.fn(),
    listBacktests: vi.fn(),
  },
}));

vi.mock('../../src/api', () => ({ endpoints: { strategies: mockStrategies } }));

import Backtester from '../../src/pages/Backtester';
import { PAGES } from '../../src/design/pageFields';
import { PAGE_HIERARCHY_BY_PAGE, tierSelector } from '../../src/design/pageHierarchy';
import { REASON_UNREADABLE_EQUITY } from '../../src/lib/drawdownSeries';

/**
 * §7.4's tier 1, spelled out — `{key, label}` as literals rather than read from the
 * declaration, so this file fails on a rename instead of agreeing with it.
 */
const TIER_ONE = Object.freeze([
  Object.freeze({ key: 'totalReturn', label: 'Total return' }),
  Object.freeze({ key: 'netPnl', label: 'Net P&L' }),
  Object.freeze({ key: 'maxDrawdown', label: 'Max drawdown' }),
  Object.freeze({ key: 'sharpe', label: 'Sharpe' }),
  Object.freeze({ key: 'winRate', label: 'Win rate' }),
  Object.freeze({ key: 'tradeCount', label: 'Trades' }),
]);

/** One execute response, shaped as `execute_backtest` answers it. */
const executeResponse = (results = {}) => ({
  backtest_id: 'bt-7',
  status: 'completed',
  results: {
    total_return_pct: 12.5,
    total_pnl: 1250,
    max_drawdown_pct: -8.25,
    sharpe_ratio: 1.2,
    win_rate_pct: 55,
    total_trades: 2,
    total_fees_paid: 3.5,
    charts: {
      equity_curve: {
        values: [
          { timestamp: '2024-01-01T00:00:00Z', equity: 10000 },
          { timestamp: '2024-01-02T00:00:00Z', equity: 10250 },
          { timestamp: '2024-01-03T00:00:00Z', equity: 9800 },
        ],
      },
    },
    ...results,
  },
  strategy_id: 's-1',
  version_id: 'v-1',
  version: 'v1.0',
  configuration: { initial_capital: 10000 },
  ignored_fields: [],
});

const renderPage = () =>
  render(
    <MemoryRouter>
      <Backtester strategy={{ id: 's-1', name: 'Momentum v2' }} />
    </MemoryRouter>,
  );

const run = async () => {
  const user = userEvent.setup();
  await user.click(screen.getByTestId('backtester-run'));
  await waitFor(() => expect(mockStrategies.executeBacktest).toHaveBeenCalledTimes(1));
};

/** One tier's container for THIS page, or `null`. */
const tierContainer = (tier) => document.querySelector(tierSelector(PAGES.BACKTESTER, tier));

/** Every `ds/Metric` root on the page, in document order, as its tier. */
const metricTiers = () => [...document.querySelectorAll('[data-metric-tier]')]
  .map((element) => Number(element.getAttribute('data-metric-tier')));

beforeEach(() => {
  vi.clearAllMocks();
  charts.calls.length = 0;

  mockStrategies.list.mockResolvedValue([{ id: 's-1', name: 'Momentum v2' }]);
  mockStrategies.versions.mockResolvedValue({ versions: [{ id: 'v-1', is_current: true }] });
  mockStrategies.listBacktests.mockResolvedValue({ backtests: [], total: 0 });
  mockStrategies.executeBacktest.mockResolvedValue(executeResponse());
});

describe('the result region renders §7.4\'s three tiers in the declared order', () => {
  it('renders nothing but an empty state before a run — no tier, no figure', async () => {
    renderPage();
    await waitFor(() => expect(mockStrategies.listBacktests).toHaveBeenCalled());

    for (const tier of [1, 2, 3]) {
      expect(tierContainer(tier), `tier ${tier} before a run`).toBeNull();
    }
    expect(screen.getByText('No backtest has been run yet')).toBeTruthy();
  });

  it('puts the three tier containers on screen in ascending document order', async () => {
    renderPage();
    await run();

    for (const tier of [1, 2, 3]) {
      await waitFor(() => expect(tierContainer(tier), `tier ${tier} container`).not.toBeNull());
    }
    // Exactly one container per tier — Property 4's second clause needs one tier-1 container
    // to be outside of — and their tiers ascend, which is the ordering claim itself.
    const containers = [...document.querySelectorAll('[data-page-tier]')]
      .filter((element) => element.getAttribute('data-page') === PAGES.BACKTESTER)
      .map((element) => Number(element.getAttribute('data-page-tier')));
    expect(containers).toEqual([1, 2, 3]);
  });

  it('holds exactly the six declared figures in tier 1, and no seventh', async () => {
    renderPage();
    await run();
    await waitFor(() => expect(tierContainer(1)).not.toBeNull());

    const container = tierContainer(1);
    const declared = PAGE_HIERARCHY_BY_PAGE[PAGES.BACKTESTER].tiers
      .filter((entry) => entry.tier === 1)
      .map(({ key, label }) => ({ key, label }));
    // The declaration and the spelled-out list agree, which is what makes the loop below a
    // test of the page rather than a test of whatever the module currently says.
    expect(declared).toEqual(TIER_ONE.map(({ key, label }) => ({ key, label })));

    for (const { key, label } of TIER_ONE) {
      const slot = container.querySelector(`[data-region="${key}"]`);
      expect(slot, `${key} is not in tier 1`).not.toBeNull();
      expect(slot.getAttribute('data-metric-tier')).toBe('1');
      expect(within(slot).getByText(label)).toBeTruthy();
    }
    expect(container.querySelectorAll('[data-metric-tier]')).toHaveLength(6);

    // …and every tier-1 figure on the PAGE is one of those six.
    const strays = [...document.querySelectorAll('[data-metric-tier="1"]')]
      .filter((element) => !container.contains(element));
    expect(strays).toEqual([]);
  });

  it('reads the six figures as the engine reported them, without scaling a percentage', async () => {
    renderPage();
    await run();
    await waitFor(() => expect(tierContainer(1)).not.toBeNull());

    const figure = (key) => tierContainer(1).querySelector(`[data-region="${key}"]`).textContent;
    expect(figure('totalReturn')).toContain('12.50%');
    expect(figure('maxDrawdown')).toContain('-8.25%');
    expect(figure('winRate')).toContain('55.00%');
    expect(figure('sharpe')).toContain('1.20');
    expect(figure('tradeCount')).toContain('2');
  });

  it('draws both curves from the one derived series, in tier 2', async () => {
    renderPage();
    await run();
    await waitFor(() => expect(screen.getAllByTestId('chart')).toHaveLength(2));

    const plotted = screen.getAllByTestId('chart');
    expect(tierContainer(2).contains(plotted[0])).toBe(true);
    expect(plotted.map((figure) => figure.dataset.chartSeries)).toEqual(['equity', 'drawdown']);
    // One point per equity point, on both, because the derivation emits one per input.
    expect(plotted.map((figure) => figure.dataset.chartPoints)).toEqual(['3', '3']);
    // The drawdown axis names the units the derivation is in — absolute, not a percentage.
    expect(plotted[1].dataset.chartY).toMatch(/drawdown from peak/i);
  });

  it('puts the three detail tabs behind one strip, opening on Trades', async () => {
    renderPage();
    await run();
    await waitFor(() => expect(tierContainer(3)).not.toBeNull());

    const strip = within(tierContainer(3)).getByRole('tablist', { name: 'Backtest detail' });
    expect(within(strip).getAllByRole('tab').map((tab) => tab.textContent))
      .toEqual(['Trades', 'Monthly returns', 'Extended statistics']);
    expect(within(strip).getByRole('tab', { selected: true }).textContent).toBe('Trades');
  });

  it('orders every tiered figure by its tier, tier 1 first', async () => {
    renderPage();
    await run();
    await waitFor(() => expect(tierContainer(3)).not.toBeNull());

    // The six tier-1 figures precede the tier-3 statistics, and nothing inverts. The
    // configuration column's own pinned-version figure is tier 3 and sits above them, so this
    // is asserted over the RESULT region — which is the region §7.4 tiers.
    const region = document.querySelector('[data-region="backtest-results"]');
    const tiers = [...region.querySelectorAll('[data-metric-tier]')]
      .map((element) => Number(element.getAttribute('data-metric-tier')));
    expect(tiers.slice(0, 6)).toEqual([1, 1, 1, 1, 1, 1]);
    expect([...tiers].sort((a, b) => a - b)).toEqual(tiers);
    expect(metricTiers().length).toBeGreaterThan(tiers.length - 1);
  });
});

describe('Requirement 6.6: a failed run renders no tier-1 figure at all', () => {
  it('renders no tier-1 container, no figure and no zero — an ErrorState instead', async () => {
    mockStrategies.executeBacktest.mockRejectedValue(new Error('the engine refused'));
    vi.spyOn(console, 'error').mockImplementation(() => {});

    renderPage();
    await run();

    await waitFor(() => expect(screen.getByTestId('backtester-run-error')).toBeTruthy());
    // The container is ABSENT, which is what makes this structural rather than a formatting
    // choice: there is no row to render zeros or em-dashes into.
    expect(tierContainer(1)).toBeNull();
    expect(document.querySelectorAll('[data-metric-tier="1"]')).toHaveLength(0);
    for (const { key } of TIER_ONE) {
      expect(document.querySelector(`[data-region="${key}"]`), `${key} survived`).toBeNull();
    }
    // …and neither curve is drawn from the previous state either.
    expect(screen.queryAllByTestId('chart')).toEqual([]);
  });

  it('is not the same thing as a completed run with one figure missing', async () => {
    // `total_pnl` omitted, which is in fact what the engine always sends: it reports a final
    // equity and no net P&L at all.
    const response = executeResponse();
    delete response.results.total_pnl;
    mockStrategies.executeBacktest.mockResolvedValue(response);

    renderPage();
    await run();
    await waitFor(() => expect(tierContainer(1)).not.toBeNull());

    // The row IS rendered, because the run is readable: five figures, and the sixth carries
    // the declared not-available marker with its reason.
    const slot = tierContainer(1).querySelector('[data-region="netPnl"]');
    expect(slot.getAttribute('data-metric-available')).toBe('false');
    expect(within(slot).getByLabelText('Net P&L: not available')).toBeTruthy();
    expect(tierContainer(1).querySelector('[data-region="totalReturn"]').textContent)
      .toContain('12.50%');
  });
});

describe('the curves are honest about what could not be read', () => {
  it('carries an omitted equity as a gap and says the curve is incomplete', async () => {
    renderPage();
    // The middle point's `equity` is absent on the wire — the case the mapper used to read as
    // a genuine 0, which is a crash to zero and overstates the drawdown depth.
    mockStrategies.executeBacktest.mockResolvedValue(executeResponse({
      charts: {
        equity_curve: {
          values: [
            { timestamp: '2024-01-01T00:00:00Z', equity: 10000 },
            { timestamp: '2024-01-02T00:00:00Z' },
            { timestamp: '2024-01-03T00:00:00Z', equity: 9800 },
          ],
        },
      },
    }));
    await run();

    await waitFor(() => expect(screen.getAllByTestId('chart')).toHaveLength(2));
    // The point is kept, so both curves still have three points and the gap is at the same x.
    expect(screen.getAllByTestId('chart').map((f) => f.dataset.chartPoints)).toEqual(['3', '3']);
    const [equityChart, drawdownChart] = charts.calls;
    expect(equityChart.data[1].equity).toBeNull();
    expect(drawdownChart.data[1].drawdown).toBeNull();
    // The depth after the gap is measured from the highest equity that COULD be read, and the
    // page says so rather than leaving a silent hole.
    expect(drawdownChart.data[2].drawdown).toBe(200);
    expect(screen.getByTestId('backtester-drawdown-incomplete').textContent)
      .toContain(REASON_UNREADABLE_EQUITY);
  });

  it('renders the empty state rather than a flat line when no point could be read', async () => {
    mockStrategies.executeBacktest.mockResolvedValue(executeResponse({ charts: {} }));

    renderPage();
    await run();
    await waitFor(() => expect(tierContainer(1)).not.toBeNull());

    expect(screen.queryAllByTestId('chart')).toEqual([]);
    expect(within(tierContainer(2)).getAllByText('This run produced no equity series'))
      .toHaveLength(2);
  });
});

describe('a saved run is inspected through the same mapper, and only for what it carries', () => {
  /**
   * One persisted `strategy_backtests` row as `GET /api/backtests` lists it — including the
   * three fields the writer fills from keys the engine never sends, which is why every row
   * ever written holds `0`/`[]` for them.
   */
  const savedRow = () => ({
    id: 'bt-9',
    strategy_id: 's-1',
    version: 'v1.0',
    dataset: 'BTC/USDT',
    status: 'completed',
    created_at: '2024-01-02T00:00:00+00:00',
    total_return_pct: 12.5,
    sharpe_ratio: 1.2,
    total_trades: 2,
    win_rate: 0,
    max_drawdown: 0,
    equity_curve: [],
    blueprint: { name: 'Momentum v2' },
  });

  it('renders the row\'s real figures and the marker for the ones it cannot hold', async () => {
    mockStrategies.listBacktests.mockResolvedValue({ backtests: [savedRow()], total: 1 });
    renderPage();
    await waitFor(() => expect(screen.getByText('Saved Backtest History')).toBeTruthy());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Inspect' }));

    await waitFor(() => expect(tierContainer(1)).not.toBeNull());
    const figure = (key) => tierContainer(1).querySelector(`[data-region="${key}"]`);
    expect(figure('totalReturn').textContent).toContain('12.50%');
    expect(figure('sharpe').textContent).toContain('1.20');
    // The two the database can only ever hold as `0`: the marker, never "0%" — which would
    // read as "every trade lost" and "the account never declined" (Requirement 14.5).
    expect(figure('winRate').getAttribute('data-metric-available')).toBe('false');
    expect(figure('maxDrawdown').getAttribute('data-metric-available')).toBe('false');
    // The stored curve is empty for the same reason, so the empty state stands in for it.
    expect(screen.queryAllByTestId('chart')).toEqual([]);
    // Nothing was run: the row was already persisted.
    expect(mockStrategies.executeBacktest).not.toHaveBeenCalled();
  });
});

describe('Requirement 19.3: what the engine does not produce says so', () => {
  it('states that monthly returns carry no months rather than charting them', async () => {
    renderPage();
    await run();
    await waitFor(() => expect(tierContainer(3)).not.toBeNull());

    const panel = tierContainer(3).querySelector('[data-region="monthly-returns"]');
    expect(panel.getAttribute('data-panel-state')).toBe('unavailable');
    expect(panel.textContent).toMatch(/no dated monthly returns/i);
    // Not a placeholder and not a dead control: nothing in there offers an action.
    expect(within(panel).queryAllByRole('button')).toEqual([]);
  });

  it('reports the declared fees field in the extended statistics, from its declaration', async () => {
    renderPage();
    await run();
    await waitFor(() => expect(tierContainer(3)).not.toBeNull());

    const fees = tierContainer(3).querySelector('[data-region="fees"]');
    expect(fees.getAttribute('data-metric-tier')).toBe('3');
    expect(within(fees).getByText('Fees')).toBeTruthy();
    expect(fees.textContent).toContain('3.50');
  });
});
