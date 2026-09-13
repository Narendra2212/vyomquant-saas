/**
 * ═══════════════════════════════════════════════════════════════════════════
 * `pages/Strategies` — the owner's table (vyomquant-ui-redesign task 17.1)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * design.md §7.2. Requirements 4.1, 4.4, 4.5, 11.2, 11.5, 14.5, 19.3.
 *
 * SECTION ONE ONLY. The page has two independent regions built from two independent reads:
 * the owner's list from `endpoints.strategies.list()`, which this file covers, and the
 * marketplace ownership section from `api.library.myStrategies()`, which
 * `src/pages/__tests__/Strategies.test.jsx` covers and task 17.1 left untouched. Both reads
 * are stubbed here because both fire on mount, but nothing below asserts on the second.
 *
 * WHAT IS WORTH ASSERTING, AND WHY
 * --------------------------------
 *  1. **The ten columns, in §7.2's order.** Requirement 4.1 is a list of fields in an order,
 *     which is a claim about the header row and nothing else. Asserted by reading the `<th>`
 *     texts, so a column silently dropped or reordered fails here rather than in review.
 *  2. **`undetermined` risk renders NOT-AVAILABLE.** This is the load-bearing one. The page
 *     used to write `health: row.health ?? "healthy"` against a list endpoint that reports no
 *     `health` field, so every strategy read healthy — including one whose only deployment
 *     had failed. The paired positive (a row that DOES carry a deployment record renders a
 *     verdict) is asserted in the same describe, so "renders not-available" is a fact about
 *     the row and not about a marker the page renders unconditionally.
 *  3. **A `null` performance figure is a marker, never `0`.** `_LIST_COLUMNS` carries no
 *     `pnl`, `win_rate` or `max_dd`, and a `0%` P&L reads as a strategy that has never made
 *     money — a different claim from "this list does not report performance"
 *     (Requirement 14.5). Asserted as the absence of a zero as well as the presence of the
 *     marker, because only the first of those two can fail on the old page.
 *  4. **BC-3 / BC-4 render as figures.** Both keys landed in task 12 and are always present
 *     and `null` when unreported, so both arms are asserted.
 *  5. **The summary, the empty states and the error state.** Requirement 11.5's two empty
 *     cases are told apart by the `n of m` pair, so both are driven through the real filter
 *     controls rather than by calling a helper.
 *  6. **A control over a field the read does not report is not rendered at all**, and
 *     `pageFields`' reason is shown in its place (Requirement 19.3, applied to a control).
 *
 * WHAT IS MOCKED, AND WHY IT IS NOT THE THING UNDER TEST
 * ----------------------------------------------------
 * `src/api` — the shared module, so no request leaves the process — and
 * `src/pages/StrategyBuilder`, a heavy alternate view this page swaps itself for and which
 * the table never reaches. `ds/DataTable`, `ds/FilterBar`, `ds/Panel`, `ds/EmptyState`,
 * `ds/ErrorState`, `ds/StrategyStatus`, `lib/strategyHealth` and `design/pageFields` are all
 * real: every one of them is part of what this task is asserting.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import {
  PAGES,
  PAGE_FIELDS_BY_PAGE,
  PAGE_FIELD_BY_KEY,
  VERDICT,
} from '../../src/design/pageFields';

const { mockApi } = vi.hoisted(() => {
  const fns = (names) => Object.fromEntries(names.map((n) => [n, vi.fn()]));
  return {
    mockApi: {
      library: fns(['myStrategies', 'renewSubscription', 'cancelSubscription']),
      strategies: fns(['list', 'delete', 'rename', 'pause', 'deployVersion', 'deployPreflight']),
      exchange: fns(['list']),
    },
  };
});

vi.mock('../../src/api', () => ({
  default: mockApi,
  api: mockApi,
  endpoints: mockApi,
}));

vi.mock('../../src/pages/StrategyBuilder', () => ({
  default: () => <div data-testid="builder-stub" />,
}));

import Strategies from '../../src/pages/Strategies';

// ─────────────────────────────────────────────────────────────────────────────
// The declaration, read rather than restated
// ─────────────────────────────────────────────────────────────────────────────

/** §7.2's ten `pageFields` keys, in the order Requirement 4.1 names them. */
const COLUMN_FIELDS = Object.freeze([
  'name',
  'status',
  'version',
  'market',
  'deploymentState',
  'performance',
  'riskState',
  'lastSignalAt',
  'lastExecutionAt',
  'updatedAt',
]);

const declared = (field) => PAGE_FIELD_BY_KEY[`${PAGES.STRATEGIES}/${field}`];

/** The header text the page must carry for a field: the declaration's own label. */
const labelOf = (field) => declared(field).label;

/** `ds/Metric`'s marker, and `ds/StatusBadge`'s. Both mark a cell as not-available. */
const markersIn = (element) => element.querySelectorAll('[data-metric-marker="not-available"]');

// ─────────────────────────────────────────────────────────────────────────────
// Fixtures — `routers/strategies.list_strategies`' row, key for key
// ─────────────────────────────────────────────────────────────────────────────

/**
 * One row exactly as the list projection builds it: `_LIST_COLUMNS` narrowed, plus
 * `is_archived`, `archived_at` and BC-3's / BC-4's two timestamps. No `pnl`, no `win_rate`,
 * no `max_dd`, no `health`, no `environment`, no `most_recent_deployment` — none of those is
 * on the response, and a fixture that carried them would be testing a payload nobody serves.
 */
const strategyRow = (overrides = {}) => ({
  id: 's-1',
  name: 'Momentum v2',
  description: null,
  symbol: 'BTCUSDT',
  timeframe: '1h',
  status: 'running',
  is_active: true,
  deployed_exchange: 'binance',
  created_at: '2024-04-01T09:00:00+00:00',
  updated_at: '2024-05-02T11:30:00+00:00',
  version: '1.2',
  is_archived: false,
  archived_at: null,
  last_signal_at: '2024-05-02T11:25:00+00:00',
  last_execution_at: '2024-05-02T11:26:30+00:00',
  ...overrides,
});

const listBody = (rows) => ({
  strategies: rows,
  total: rows.length,
  include_archived: false,
  archived_total: 0,
});

/** An `ApiError`-shaped rejection. `.status` is what the client classifies on. */
const apiError = (status, message) =>
  Object.assign(new Error(message), { status, getUserMessage: () => message });

/** The page's own formatting of an instant, so the assertion is locale-independent. */
const asLocal = (iso) => new Date(iso).toLocaleString();

// ─────────────────────────────────────────────────────────────────────────────
// Rendering
// ─────────────────────────────────────────────────────────────────────────────

const renderPage = (entries = ['/app/strategies']) => render(
  <MemoryRouter initialEntries={entries}>
    <Strategies />
  </MemoryRouter>,
);

/** Mount with one list body stubbed, and wait for the table to arrive. */
const mountWith = async (rows, entries) => {
  mockApi.strategies.list.mockResolvedValue(listBody(rows));
  const view = renderPage(entries);
  const table = await screen.findByRole('table');
  return { ...view, table, user: userEvent.setup() };
};

/** One row's `<td>` for a column, by the column key `DataTable` stamps on every cell. */
const cell = (row, key) => row.querySelector(`[data-column-key="${key}"]`);

/** The data rows, excluding the header row and any expanded detail row. */
const bodyRows = (table) =>
  [...table.querySelectorAll('tr[data-row-id]')];

beforeEach(() => {
  for (const namespace of Object.values(mockApi)) {
    for (const fn of Object.values(namespace)) fn.mockReset();
  }
  mockApi.strategies.list.mockResolvedValue(listBody([]));
  mockApi.exchange.list.mockResolvedValue([]);
  // The ownership section's read. Stubbed empty so it renders its own empty state and stays
  // out of the way; nothing here asserts on it.
  mockApi.library.myStrategies.mockResolvedValue({ items: [], total: 0 });
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'log').mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ═════════════════════════════════════════════════════════════════════════════
// 1. The columns (Requirements 4.1, 4.5)
// ═════════════════════════════════════════════════════════════════════════════

describe('the ten columns §7.2 names, in §7.2\'s order (Requirement 4.1)', () => {
  it('renders each one under the label `pageFields` declares for it', async () => {
    const { table } = await mountWith([strategyRow()]);

    const headers = [...table.querySelectorAll('thead th')]
      .map((th) => th.textContent.trim())
      // The expander column's header is screen-reader-only copy, not a column.
      .filter((text) => text !== 'Expand row');

    expect(headers).toEqual([
      ...COLUMN_FIELDS.map(labelOf),
      // The eleventh is the row's action set, which §7.2 draws as the trailing `⋯`. It is
      // not one of Requirement 4.1's fields, so it is named separately rather than folded in.
      'Actions',
    ]);
  });

  it('renders one row per strategy and no cards (Requirement 4.5)', async () => {
    const { table } = await mountWith([
      strategyRow(),
      strategyRow({ id: 's-2', name: 'Mean reversion' }),
    ]);

    expect(bodyRows(table)).toHaveLength(2);
  });

  it('keeps both reads exactly as they were — this is a presentation change', async () => {
    await mountWith([strategyRow()]);

    expect(mockApi.strategies.list).toHaveBeenCalledTimes(1);
    expect(mockApi.strategies.list).toHaveBeenCalledWith();
    await waitFor(() => expect(mockApi.library.myStrategies).toHaveBeenCalledTimes(1));
  });

  it('renders the recorded market, version and deployment flag', async () => {
    const { table } = await mountWith([strategyRow()]);
    const [row] = bodyRows(table);

    // `symbol · deployed_exchange · timeframe` — the three keys the `market` entry names.
    expect(cell(row, 'market').textContent).toBe('BTCUSDT · binance · 1h');
    expect(cell(row, 'current_version').textContent).toBe('1.2');
    // `is_active`, which reports the row's own flag and claims no live worker.
    expect(cell(row, 'deploymentState').textContent).toContain('Active');
  });

  it('renders the marker, never a placeholder, for a field the row does not carry', async () => {
    // A row the projection can legitimately produce: no symbol, no version, no `is_active`.
    // The page used to write `"N/A"`, `"1.0"` and `"paper"` into these three cells.
    const { table } = await mountWith([
      strategyRow({ symbol: null, timeframe: null, deployed_exchange: null, version: null, is_active: null }),
    ]);
    const [row] = bodyRows(table);

    for (const key of ['market', 'current_version', 'deploymentState']) {
      expect(markersIn(cell(row, key)), key).toHaveLength(1);
      expect(cell(row, key).textContent, key).not.toMatch(/N\/A|1\.0|paper/i);
    }
  });
});

// ═════════════════════════════════════════════════════════════════════════════
// 2. Risk: `undetermined` is not-available (Requirement 4.1, and 1.8/1.9)
// ═════════════════════════════════════════════════════════════════════════════

describe('the risk column goes through computeStrategyHealth', () => {
  it('renders `undetermined` as not-available, never as a healthy verdict', async () => {
    // The row as the list endpoint really serves it: neither permitted source is on it, so
    // `computeStrategyHealth` returns `undetermined` for it — which is EVERY row today.
    const { table } = await mountWith([strategyRow()]);
    const risk = cell(bodyRows(table)[0], 'health');

    expect(markersIn(risk)).toHaveLength(1);
    expect(risk.textContent).not.toMatch(/healthy/i);
    // The reason is the declaration's own, so the cell says why rather than showing a dash.
    expect(risk.querySelector('[data-metric-marker]').getAttribute('title'))
      .toBe(declared('riskState').reason);
  });

  it('renders a real verdict when a deployment record decides one', async () => {
    // The paired positive: the assertion above is about `undetermined`, not about a marker
    // the column renders whatever it is handed. When the listing is widened to carry the
    // record, this is the cell a trader gets — with no change to the page.
    const { table } = await mountWith([
      strategyRow({ most_recent_deployment: { status: 'failed' } }),
      strategyRow({ id: 's-2', most_recent_deployment: { status: 'running' } }),
      strategyRow({ id: 's-3', most_recent_deployment: { status: 'paused' } }),
    ]);
    const rows = bodyRows(table);

    expect(cell(rows[0], 'health').textContent).toMatch(/error/i);
    expect(cell(rows[1], 'health').textContent).toMatch(/healthy/i);
    expect(cell(rows[2], 'health').textContent).toMatch(/degraded/i);
    for (const row of rows) expect(markersIn(cell(row, 'health'))).toHaveLength(0);
  });

  it('reads an archived row as history beside its status, not as a status', async () => {
    const { table } = await mountWith([strategyRow({ is_archived: true })]);
    const status = cell(bodyRows(table)[0], 'status');

    expect(status.textContent).toContain('Archived');
    // The actionable status is still the server's own.
    expect(status.textContent).toMatch(/running/i);
  });

  it('reads a row reporting no status as unreported, not as stopped', async () => {
    const { table } = await mountWith([strategyRow({ status: null })]);

    expect(cell(bodyRows(table)[0], 'status').textContent).toMatch(/status not reported/i);
  });
});

// ═════════════════════════════════════════════════════════════════════════════
// 3. Performance: a marker, never a zero (Requirement 14.5)
// ═════════════════════════════════════════════════════════════════════════════

describe('the performance column', () => {
  it('is declared unavailable on this read, so the column has nothing to render', () => {
    // Read from the declaration, not asserted about the page: if a listing widening ever
    // makes this ✅, the expectation below stops describing the intended behaviour and this
    // test is what says so.
    expect(declared('performance').verdict).toBe(VERDICT.UNAVAILABLE);
  });

  it('renders not-available for every row, and no zero', async () => {
    const { table } = await mountWith([
      strategyRow(),
      strategyRow({ id: 's-2', name: 'Mean reversion' }),
    ]);

    for (const row of bodyRows(table)) {
      const performance = cell(row, 'performance');
      expect(markersIn(performance)).toHaveLength(1);
      // The failure this exists to stop: `0`, `0%`, `+0.00%` — a figure that reads as a
      // measurement of nothing rather than as the absence of one.
      expect(performance.textContent).not.toMatch(/\d/);
      expect(performance.querySelector('[data-metric-marker]').getAttribute('title'))
        .toBe(declared('performance').reason);
    }
  });

  it('offers no sort control over a column of markers', async () => {
    const { table } = await mountWith([strategyRow()]);
    const header = [...table.querySelectorAll('thead th')]
      .find((th) => th.textContent.trim() === labelOf('performance'));

    expect(header.querySelector('button')).toBeNull();
  });
});

// ═════════════════════════════════════════════════════════════════════════════
// 4. BC-3 and BC-4 (Requirement 4.1, 19.2)
// ═════════════════════════════════════════════════════════════════════════════

describe('last signal (BC-3) and last execution (BC-4)', () => {
  it('renders both as figures now that task 12 landed', async () => {
    const row = strategyRow();
    const { table } = await mountWith([row]);
    const [tr] = bodyRows(table);

    expect(cell(tr, 'last_signal_at').textContent).toBe(asLocal(row.last_signal_at));
    expect(cell(tr, 'last_execution_at').textContent).toBe(asLocal(row.last_execution_at));
  });

  it('renders each as not-available when the server reports null, with its own reason', async () => {
    // Both keys are ALWAYS present on the response and `null` when there is nothing to
    // report, which is the shape these two cells are written for.
    const { table } = await mountWith([
      strategyRow({ last_signal_at: null, last_execution_at: null }),
    ]);
    const [tr] = bodyRows(table);

    expect(markersIn(cell(tr, 'last_signal_at'))).toHaveLength(1);
    expect(cell(tr, 'last_signal_at').querySelector('[data-metric-marker]').getAttribute('title'))
      .toBe(declared('lastSignalAt').reason);
    expect(markersIn(cell(tr, 'last_execution_at'))).toHaveLength(1);
    expect(cell(tr, 'last_execution_at').querySelector('[data-metric-marker]').getAttribute('title'))
      .toBe(declared('lastExecutionAt').reason);
  });

  it('renders the updated time from the row\'s own updated_at', async () => {
    const row = strategyRow();
    const { table } = await mountWith([row]);

    expect(cell(bodyRows(table)[0], 'updated_at').textContent).toBe(asLocal(row.updated_at));
  });
});

// ═════════════════════════════════════════════════════════════════════════════
// 5. The filter row (Requirements 11.2, 11.5, 19.3)
// ═════════════════════════════════════════════════════════════════════════════

describe('the filter row', () => {
  const countRegion = () => document.querySelector('[data-filter-count="true"]');

  it('reports `n of m` over the rows that were read', async () => {
    await mountWith([
      strategyRow(),
      strategyRow({ id: 's-2', name: 'Mean reversion', status: 'draft' }),
      strategyRow({ id: 's-3', name: 'Breakout', status: 'paused' }),
    ]);

    expect(countRegion().getAttribute('data-result-count')).toBe('3');
    expect(countRegion().getAttribute('data-total-count')).toBe('3');
    expect(countRegion().textContent).toBe('3 of 3 strategies');
  });

  it('narrows the table and the count on a status segment', async () => {
    const { table, user } = await mountWith([
      strategyRow(),
      strategyRow({ id: 's-2', name: 'Mean reversion', status: 'draft' }),
    ]);
    expect(bodyRows(table)).toHaveLength(2);

    await user.click(screen.getByRole('radio', { name: 'Draft' }));

    await waitFor(() => expect(bodyRows(screen.getByRole('table'))).toHaveLength(1));
    expect(within(screen.getByRole('table')).getByText('Mean reversion')).toBeTruthy();
    expect(countRegion().getAttribute('data-result-count')).toBe('1');
    // The unfiltered count is what tells Requirement 11.5's two empty states apart.
    expect(countRegion().getAttribute('data-total-count')).toBe('2');
  });

  it('narrows on a search over the name and the market', async () => {
    const { user } = await mountWith([
      strategyRow(),
      strategyRow({ id: 's-2', name: 'Mean reversion', symbol: 'ETHUSDT' }),
    ]);

    await user.type(screen.getByLabelText(/search name or market/i), 'ethusdt');

    await waitFor(() => expect(countRegion().getAttribute('data-result-count')).toBe('1'));
    expect(within(screen.getByRole('table')).getByText('Mean reversion')).toBeTruthy();
  });

  it('renders no environment control, and states why in its place', async () => {
    await mountWith([strategyRow()]);

    // §7.2's sketch draws an `[Environment ▾]` select; `_LIST_COLUMNS` carries no
    // environment for it to filter on, so the control is absent rather than dead.
    expect(screen.queryByLabelText(/environment/i)).toBeNull();
    const withheld = screen.getByTestId('withheld-filters');
    expect(withheld.textContent).toContain(declared('environment').label);
    expect(withheld.textContent).toContain(declared('environment').reason);
  });

  it('declares the environment field unavailable, which is what withholds the control', () => {
    expect(declared('environment').verdict).toBe(VERDICT.UNAVAILABLE);
    // Every Strategies entry carries a reason wherever its marker is reachable, so the note
    // above can never render an empty sentence.
    for (const field of PAGE_FIELDS_BY_PAGE[PAGES.STRATEGIES]) {
      if (field.verdict === VERDICT.UNAVAILABLE) {
        expect(typeof field.reason, field.field).toBe('string');
        expect(field.reason.length, field.field).toBeGreaterThan(20);
      }
    }
  });
});

// ═════════════════════════════════════════════════════════════════════════════
// 6. Empty, no-match and error (Requirements 4.4, 11.5, 14.5)
// ═════════════════════════════════════════════════════════════════════════════

describe('the table region\'s states', () => {
  it('renders Requirement 4.4\'s empty state for zero strategies, with the builder action', async () => {
    mockApi.strategies.list.mockResolvedValue(listBody([]));
    renderPage();

    const empty = await screen.findByText('No strategies yet');
    const region = empty.closest('[data-empty-variant]');
    expect(region.getAttribute('data-empty-variant')).toBe('no-data');
    // The body says what an empty list MEANS, not that it is empty.
    expect(region.textContent).toMatch(/nothing trades until you build one and deploy it/i);
    expect(within(region).getByRole('link', { name: /open the strategy builder/i })
      .getAttribute('href')).toBe('/app/builder');
    // No table at all, so no header row standing over nothing.
    expect(screen.queryByRole('table')).toBeNull();
  });

  it('distinguishes "no strategies" from "none match this filter", and offers clear-filters', async () => {
    const { user } = await mountWith([strategyRow({ status: 'running' })]);

    await user.click(screen.getByRole('radio', { name: 'Draft' }));

    const empty = await screen.findByText('No strategies match these filters');
    const region = empty.closest('[data-empty-variant]');
    expect(region.getAttribute('data-empty-variant')).toBe('no-match');
    // Requirement 11.5: the rows exist, so the way out is to widen the filter — and the
    // no-data case above must NOT offer this.
    expect(within(region).getByRole('button', { name: /clear filters/i })).toBeTruthy();

    await user.click(within(region).getByRole('button', { name: /clear filters/i }));
    await waitFor(() => expect(bodyRows(screen.getByRole('table'))).toHaveLength(1));
  });

  it('renders an error state for a failed read, never an empty table (Requirement 14.5)', async () => {
    mockApi.strategies.list.mockRejectedValue(apiError(503, 'The strategy list is down.'));
    renderPage();

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/again|retry|wrong/i);
    // The two renderings Requirement 14.5 forbids for a failed read.
    expect(screen.queryByRole('table')).toBeNull();
    expect(screen.queryByText('No strategies yet')).toBeNull();
  });

  it('re-reads on retry and renders the rows that arrive', async () => {
    mockApi.strategies.list.mockRejectedValueOnce(apiError(503, 'down'));
    renderPage();
    await screen.findByRole('alert');

    mockApi.strategies.list.mockResolvedValue(listBody([strategyRow()]));
    await userEvent.setup().click(screen.getByRole('button', { name: /try again|retry/i }));

    await waitFor(() => expect(mockApi.strategies.list).toHaveBeenCalledTimes(2));
    const table = await screen.findByRole('table');
    expect(within(table).getByText('Momentum v2')).toBeTruthy();
  });

  it('keeps the two reads independent: the ownership read failing leaves the table alone', async () => {
    mockApi.library.myStrategies.mockRejectedValue(apiError(503, 'ownership down'));
    const { table } = await mountWith([strategyRow()]);

    expect(within(table).getByText('Momentum v2')).toBeTruthy();
  });
});

// ═════════════════════════════════════════════════════════════════════════════
// 7. The row still acts (Requirement 4.2) — task 17.2 repartitions these
// ═════════════════════════════════════════════════════════════════════════════

describe('the row\'s actions survive the rebuild', () => {
  it('offers the same seven controls the card carried', async () => {
    const { table } = await mountWith([strategyRow({ status: 'stopped' })]);
    const actions = within(cell(bodyRows(table)[0], 'actions'));

    for (const label of ['Edit', 'Clone', 'Rename', 'Backtest', 'Trace', 'Deploy']) {
      expect(actions.getByRole('button', { name: label }), label).toBeTruthy();
    }
    expect(actions.getByRole('button', { name: 'Archive Momentum v2' })).toBeTruthy();
  });

  it('offers Pause instead of Deploy while the strategy is running', async () => {
    const { table } = await mountWith([strategyRow({ status: 'running' })]);
    const actions = within(cell(bodyRows(table)[0], 'actions'));

    expect(actions.getByRole('button', { name: 'Pause' })).toBeTruthy();
    expect(actions.queryByRole('button', { name: 'Deploy' })).toBeNull();
  });

  it('links the row to its detail page from the name cell', async () => {
    const { table } = await mountWith([strategyRow()]);

    expect(cell(bodyRows(table)[0], 'name').querySelector('a').getAttribute('href'))
      .toBe('/app/strategies/s-1');
  });

  it('marks the deep-linked strategy, and only that one', async () => {
    const { table } = await mountWith(
      [strategyRow(), strategyRow({ id: 's-2', name: 'Mean reversion' })],
      ['/app/strategies?strategy_id=s-2'],
    );
    const rows = bodyRows(table);

    expect(cell(rows[0], 'name').textContent).not.toMatch(/focused/i);
    expect(cell(rows[1], 'name').textContent).toMatch(/focused target/i);
  });
});
