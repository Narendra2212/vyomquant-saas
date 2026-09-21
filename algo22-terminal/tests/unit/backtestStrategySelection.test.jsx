/**
 * The Backtester's strategy selector, across the four things the strategies listing can do
 * (task 23.1's configuration flow — Requirements 6.1, 14.1, 14.3).
 *
 * One defect and the facts that follow from it. `ds/Field` renders a `<select>` from
 * `options`, and a `<select>` whose `value` matches none of them renders as though nothing
 * were chosen. So a page deep-linked to an ENTITLED strategy — one the owner listing does not
 * carry, which is the whole reason the by-id read exists — showed "Choose a strategy…" over a
 * configuration already pinned to a version of that strategy, and the run it would have
 * executed was not the one the control appeared to describe.
 *
 * What is asserted here:
 *
 * * **The selected strategy is among the offered options, whatever the listing returned**, and
 *   it is the option the control reports as selected. The appended entry is one the SERVER
 *   named — through the listing, the caller, or the by-id read — never an id from the URL.
 * * **A listing that failed still leaves a known strategy runnable**, and says so: the other
 *   strategies are reported unknown rather than absent (Requirement 14.3), because a
 *   one-entry selector with no explanation reads as an account with one strategy.
 * * **A listing that carries the selection is untouched** — no duplicate entry, no warning.
 * * **A listing that failed with nothing else naming a strategy** is the one case where no
 *   configuration can be assembled, and there the four panels are replaced.
 *
 * The run request's shape is task 17.1's subject (`backtestExecution.test.js`) and the save
 * flow is 17.2's (`backtestSaveFlow.test.jsx`); neither is re-asserted here.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// Charts are not the subject, and jsdom gives `ResponsiveContainer` no box to measure.
vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }) => <div data-testid="responsive-container">{children}</div>,
  AreaChart: ({ children }) => <div data-testid="area-chart">{children}</div>,
  Area: () => <div data-testid="area" />,
  CartesianGrid: () => <div data-testid="grid" />,
  XAxis: () => <div data-testid="x-axis" />,
  YAxis: () => <div data-testid="y-axis" />,
  Tooltip: () => <div data-testid="tooltip" />,
}));

/*
 * `builder/AssetSelector` and `builder/TimeframeSelector` read the venue's asset universe and
 * the pipeline's published timeframe set on mount, through the shared axios instance rather
 * than through `src/api`. Refused here rather than stubbed with a fabricated universe, for the
 * same reason `backtestSaveFlow` refuses them: market selection is not this suite's subject,
 * both controls answer a refusal with their own error state, and neither the market nor the
 * interval travels with the run (SB-06).
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

/** The two strategies the owner listing carries, when it answers at all. */
const OWNED = [
  { id: 's-1', name: 'Momentum v2' },
  { id: 's-2', name: 'Mean Reversion' },
];

/** The one it does not: an entitled strategy, reachable only by id. */
const ENTITLED = { id: 's-9', name: 'Entitled Momentum' };

const renderPage = ({ search = '', strategy = null } = {}) =>
  render(
    <MemoryRouter initialEntries={[`/app/backtest${search}`]}>
      <Backtester strategy={strategy} />
    </MemoryRouter>,
  );

/** The strategy `<select>`, named by its own visible label and nothing else (Req 15.1). */
const selector = () => screen.getByRole('combobox', { name: 'Strategy' });

/** Its options, in document order — scoped, so the market controls' own lists cannot leak in. */
const offered = () => within(selector())
  .getAllByRole('option')
  .map((option) => option.textContent);

describe('Backtester strategy selection (task 23.1)', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    mockStrategies.list.mockResolvedValue(OWNED);
    // Answers the strategy that was asked for, as the endpoint does — a stub that returned the
    // entitled one whatever the id would hide which read named the selection.
    mockStrategies.getById.mockImplementation(async (id) => ({
      strategy: id === ENTITLED.id ? ENTITLED : OWNED.find((row) => row.id === id) ?? { id },
    }));
    mockStrategies.versions.mockResolvedValue({ versions: [{ id: 'v-1', is_current: true }] });
    mockStrategies.listBacktests.mockResolvedValue({ backtests: [], total: 0 });
    mockExchange.getSupported.mockResolvedValue({ supported: ['BTC/USDT'] });
  });

  it('offers the deep-linked strategy the listing does not carry, and reports it selected', async () => {
    renderPage({ search: '?strategy_id=s-9' });

    // Named by the by-id read — the only path that can name an entitled strategy.
    expect(await screen.findByRole('option', { name: ENTITLED.name })).toBeTruthy();
    expect(mockStrategies.getById).toHaveBeenCalledWith('s-9');

    // The point of the whole exercise: the control shows the strategy the run is pinned to,
    // not the placeholder that says nothing has been chosen.
    expect(selector().value).toBe('s-9');
    expect(screen.getByRole('option', { name: ENTITLED.name }).selected).toBe(true);
    expect(screen.getByRole('option', { name: /choose a strategy/i }).selected).toBe(false);

    // And it is offered ALONGSIDE the listing, which is still every strategy the trader owns.
    expect(offered()).toEqual([
      'Choose a strategy…',
      ENTITLED.name,
      ...OWNED.map((row) => row.name),
    ]);
  });

  it('keeps a handed-over strategy selectable when the listing fails, and says the rest are unknown', async () => {
    mockStrategies.list.mockRejectedValue(new Error('strategies unavailable'));
    renderPage({ strategy: { id: 's-1', name: 'Momentum v2', nodes: [], edges: [] } });

    // The failure is reported where it is about the thing it is about — inside the panel,
    // with a retry (Requirement 14.3) — and not by replacing a panel that still works.
    expect(await screen.findByTestId('backtester-list-error')).toBeTruthy();
    expect(screen.getByRole('button', { name: /try again/i })).toBeTruthy();

    // The configuration is intact: this strategy is named, so it is runnable.
    expect(selector().value).toBe('s-1');
    expect(offered()).toEqual(['Choose a strategy…', 'Momentum v2']);
    expect(screen.getByTestId('backtester-run')).toBeTruthy();
    // The caller named it, so nothing is read by id — the one entry path where that holds
    // from the first render, because the prop is there before any read has settled.
    expect(mockStrategies.getById).not.toHaveBeenCalled();
  });

  it('adds nothing and warns about nothing when the listing carries the selection', async () => {
    renderPage({ search: '?strategy_id=s-1' });

    expect(await screen.findByRole('option', { name: 'Momentum v2' })).toBeTruthy();
    // One entry, not two: the listing carries it, so there is nothing to append. (A deep link
    // does issue the by-id read while the listing is still in flight — from the URL alone
    // nothing yet names the selection — and its answer is then simply the same strategy. What
    // matters at the control is that the option is not offered twice.)
    expect(offered()).toEqual(['Choose a strategy…', ...OWNED.map((row) => row.name)]);
    expect(screen.getByRole('option', { name: 'Momentum v2' }).selected).toBe(true);
    // Nothing failed, so nothing is said about the listing.
    expect(screen.queryByTestId('backtester-list-error')).toBeNull();
    expect(screen.queryByTestId('backtester-strategy-error')).toBeNull();
  });

  it('replaces the configuration only when the listing failed and nothing named a strategy', async () => {
    mockStrategies.list.mockRejectedValue(new Error('strategies unavailable'));
    const { container } = renderPage();

    // No selection, no listing, nothing to configure — so the four panels go, and the
    // failure is stated in their place rather than as a warning beside an empty selector.
    // Awaited on the region itself: whether `ds/ErrorState` offers a retry is a property of
    // the failure (`translateError`'s verdict), not something this case gets to assume.
    await waitFor(() => {
      expect(container.querySelector('[data-region="configuration-error"]')).toBeTruthy();
    });
    expect(screen.queryByRole('combobox', { name: 'Strategy' })).toBeNull();
    expect(screen.queryByTestId('backtester-run')).toBeNull();
    expect(screen.queryByTestId('backtester-list-error')).toBeNull();
  });
});
